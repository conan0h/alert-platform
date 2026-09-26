"""Tests for form4-insider's per-cycle accounting and leaderboard reporting.

The service has sent nothing in five observed windows and its cycles finish
in about 0.2 seconds, which is too fast to have fetched a single filing. From
the journal, "the feed was empty", "every filing was already handled", "the
XML would not parse", "the dedup write refused the send" and "the filter said
no" are the same output. These tests pin the counts that separate them, and
the counts that say whether the leaderboard is empty or merely unscored
(backlog #39).

Run: python3 -m pytest services/tests -q     (from the repo root)
"""

from __future__ import annotations

import sqlite3

import pytest
from _form4 import ACCESSION, CIK, Svc, form4, form4_common, make_funnel


def tx(**over) -> dict:
    """A transaction that alerts on the large-trade branch unless overridden."""
    base = {
        "tx_code": "S", "is_10b5_1": 0, "usd_value": 4_612_795.0,
        "shares": 40_000.0, "price": 115.32, "trade_date": "2026-09-21",
    }
    base.update(over)
    return base


def stats(**over) -> dict:
    base = {"name": "A Person", "n_trades": 9, "alpha_30": 0.01,
            "alpha_90": 0.20, "alpha_180": 0.03,
            "total_buy_usd": 0, "total_sell_usd": 0}
    base.update(over)
    return base


# --- should_alert returns a name, and the name is a declared stage ---------

@pytest.mark.parametrize(("transaction", "insider", "cutoff", "expected"), [
    (tx(tx_code="A"), None, None, "code_not_actionable"),
    (tx(tx_code="S", is_10b5_1=1), None, None, "planned_sale"),
    (tx(usd_value=50_000.0), None, None, "below_floor"),
    (tx(usd_value=2_000_000.0), None, None, "large_trade"),
    (tx(usd_value=500_000.0), None, None, "no_insider_history"),
    (tx(usd_value=500_000.0), stats(n_trades=2), None, "thin_history"),
    (tx(usd_value=500_000.0), stats(), None, "no_leaderboard"),
    (tx(usd_value=500_000.0), stats(alpha_90=0.01), 0.10, "below_cutoff"),
    (tx(usd_value=500_000.0), stats(alpha_90=0.20), 0.10, "top_tier"),
])
def test_every_branch_returns_its_declared_name(transaction, insider, cutoff, expected):
    decision, reason = form4.should_alert(transaction, insider, cutoff)
    assert decision == expected
    assert decision in form4.DECISIONS
    assert reason, "every decision carries an operator-facing reason"


def test_only_the_two_alerting_decisions_alert():
    assert form4.ALERTING_DECISIONS == {"large_trade", "top_tier"}
    assert form4.ALERTING_DECISIONS <= set(form4.DECISIONS)


def test_every_decision_is_a_funnel_stage():
    """The funnel raises on an undeclared stage, so a branch whose name is not
    a stage would take down a cycle in production rather than here."""
    assert set(form4.DECISIONS) <= set(form4.FUNNEL_STAGES)


# --- the funnel over process_filing ---------------------------------------

@pytest.fixture
def db(tmp_path):
    conn = form4_common.init_db(str(tmp_path / "form4.db"))
    conn.execute("PRAGMA busy_timeout=50")
    return conn


@pytest.fixture
def svc(monkeypatch):
    s = Svc()
    monkeypatch.setattr(form4, "SVC", s)
    monkeypatch.setattr(form4, "format_alert", lambda *_a: "alert body")
    return s


@pytest.fixture
def funnel(svc):
    """One registry shared with the fake Service, so a test can assert on the
    cumulative counters as well as on the rendered line."""
    return make_funnel(svc.metrics)


def filing(monkeypatch, transactions, insider=None):
    monkeypatch.setattr(form4, "fetch_primary_xml", lambda cik, acc: ("url", b"<xml/>"))
    monkeypatch.setattr(form4, "parse_form4_xml", lambda _b: {
        "insider_cik": "0001005731",
        "insider_name": "Silver Lake Partners IV, L.P.",
        "ticker": "DELL",
        "issuer_name": "Dell Technologies Inc.",
        "transactions": transactions,
    })
    monkeypatch.setattr(form4, "get_insider_stats", lambda _c, _cik: insider)


def test_a_filing_already_handled_stops_at_entries(db, funnel, svc, monkeypatch):
    """The reading the host's 0.2-second cycles point at.

    A feed of 100 accessions that are all already in `alerted` produces no
    fetch, no parse and no send — identical output to an empty feed until
    `new` distinguishes them.
    """
    filing(monkeypatch, [tx()])
    form4.mark_alerted(db, ACCESSION)

    with funnel.cycle():
        funnel.count("entries", 1)
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0

    assert svc.metrics.get("alert_funnel_entries_total") == 1
    assert svc.metrics.get("alert_funnel_new_total") == 0
    assert svc.metrics.get("alert_funnel_fetched_total") == 0


def test_a_fetch_that_fails_is_counted_as_new_but_not_fetched(db, funnel, svc, monkeypatch):
    filing(monkeypatch, [tx()])
    monkeypatch.setattr(form4, "fetch_primary_xml", lambda cik, acc: None)

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0

    assert svc.metrics.get("alert_funnel_new_total") == 1
    assert svc.metrics.get("alert_funnel_fetched_total") == 0
    assert svc.metrics.get("alert_funnel_parsed_total") == 0


def test_xml_that_will_not_parse_is_fetched_but_not_parsed(db, funnel, svc, monkeypatch):
    filing(monkeypatch, [tx()])
    monkeypatch.setattr(form4, "parse_form4_xml", lambda _b: None)

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0

    assert svc.metrics.get("alert_funnel_fetched_total") == 1
    assert svc.metrics.get("alert_funnel_parsed_total") == 0
    assert svc.metrics.get("alert_funnel_claimed_total") == 0


def test_a_refused_claim_is_parsed_but_never_claimed(db, funnel, svc, monkeypatch, tmp_path):
    """The 2026-09-21 incident's safe failure, told apart from a quiet filter.

    Both send nothing. `claimed` is what says the service declined to send
    rather than found nothing worth sending.
    """
    filing(monkeypatch, [tx()])
    blocker = sqlite3.connect(str(tmp_path / "form4.db"), timeout=0)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with funnel.cycle():
            assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
    finally:
        blocker.rollback()
        blocker.close()

    assert svc.metrics.get("alert_funnel_parsed_total") == 1
    assert svc.metrics.get("alert_funnel_claimed_total") == 0
    assert svc.metrics.get("alert_sends_refused_total") == 1


def test_the_live_refusal_names_the_empty_leaderboard(db, funnel, svc, monkeypatch):
    """What the host is doing today: a real trade, under $1M, no cutoff.

    Three of the four filter branches are unreachable while the leaderboard
    is empty, and this is the count that says so from the journal.
    """
    filing(monkeypatch, [tx(usd_value=500_000.0)], insider=stats())

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
        line = funnel.render()

    assert svc.metrics.get("alert_funnel_transactions_total") == 1
    assert svc.metrics.get("alert_funnel_no_leaderboard_total") == 1
    assert svc.metrics.get("alert_funnel_sent_total") == 0
    assert "no_leaderboard=1" in line
    assert svc.alerts == []


def test_one_filing_can_fall_into_several_decisions(db, funnel, svc, monkeypatch):
    """A Form 4 carries several trades, and the funnel counts each of them.

    The decision counts must sum to `transactions`, or the line is a
    histogram with a hole in it.
    """
    filing(monkeypatch, [
        tx(usd_value=2_000_000.0),          # large_trade  -> sent
        tx(tx_code="A"),                    # code_not_actionable
        tx(tx_code="S", is_10b5_1=1),       # planned_sale
        tx(usd_value=1_000.0),              # below_floor
    ])

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 1

    per_decision = sum(
        svc.metrics.get(f"alert_funnel_{name}_total") for name in form4.DECISIONS
    )
    assert per_decision == svc.metrics.get("alert_funnel_transactions_total") == 4
    assert svc.metrics.get("alert_funnel_sent_total") == 1
    assert len(svc.alerts) == 1


def test_a_send_that_does_not_deliver_is_not_counted_as_sent(db, funnel, svc, monkeypatch):
    filing(monkeypatch, [tx(usd_value=2_000_000.0)])
    svc.delivers = False

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0

    assert svc.metrics.get("alert_funnel_large_trade_total") == 1
    assert svc.metrics.get("alert_funnel_sent_total") == 0


def test_the_cohort_is_the_feed_not_the_filings_we_processed(funnel, svc):
    """`new_in_window` tests whether the feed is moving.

    Overnight it does not: EDGAR's current-filings feed returns the same
    accessions for hours, because Form 4s are filed after the close. Every
    scheduled run so far has read that hour, which is why "no alert in any
    observed window" is weaker evidence than it reads.
    """
    with funnel.cycle():
        funnel.observe_cohort(["a", "b", "c"])
        assert "new_in_window=?" in funnel.render()

    with funnel.cycle():
        funnel.observe_cohort(["a", "b", "c"])
        assert "new_in_window=0" in funnel.render()

    with funnel.cycle():
        funnel.observe_cohort(["a", "b", "d", "e"])
        assert "new_in_window=2" in funnel.render()


def test_the_line_is_emitted_even_when_a_cycle_dies(funnel, caplog):
    """A cycle that raised partway is exactly when the partial funnel matters,
    and the host logged one this morning: a 30-second read timeout on sec.gov."""
    with pytest.raises(RuntimeError), caplog.at_level("INFO"):
        with funnel.cycle():
            funnel.count("entries", 100)
            raise RuntimeError("read timed out")

    assert any("entries=100" in r.getMessage() for r in caplog.records)


# --- leaderboard state -----------------------------------------------------

def test_an_empty_leaderboard_reads_as_all_zeros(db):
    assert form4.leaderboard_state(db) == {
        "insiders": 0, "eligible": 0, "scored": 0, "transactions": 0,
    }


def test_a_populated_but_unscored_leaderboard_is_distinguishable(db):
    """The distinction the hourly `cutoff: null` line cannot make.

    Rows with no `alpha_90` mean the scorer has not run; no rows at all mean
    the backfill has not. Both leave the cutoff None and every trade under
    $1M refused, and they need different fixes.
    """
    db.executemany(
        "INSERT INTO insiders (insider_cik, name, n_trades, alpha_90) VALUES (?, ?, ?, ?)",
        [("1", "Scored Enough", 9, None), ("2", "Thin", 2, None)],
    )
    db.commit()

    state = form4.leaderboard_state(db)
    assert state["insiders"] == 2
    assert state["eligible"] == 1, "one insider clears the 5-trade bar"
    assert state["scored"] == 0, "and nothing has been scored, so there is no cutoff"
    assert form4.get_alpha_cutoff(db) is None


def test_a_scored_leaderboard_yields_a_cutoff(db):
    db.executemany(
        "INSERT INTO insiders (insider_cik, name, n_trades, alpha_90) VALUES (?, ?, ?, ?)",
        [(str(i), f"Insider {i}", 9, i / 100) for i in range(1, 9)],
    )
    db.commit()

    state = form4.leaderboard_state(db)
    assert state["insiders"] == state["eligible"] == state["scored"] == 8
    assert form4.get_alpha_cutoff(db) is not None


def test_publishing_the_state_sets_the_gauges(db, svc):
    db.execute(
        "INSERT INTO insiders (insider_cik, name, n_trades, alpha_90) VALUES ('1', 'X', 9, 0.2)"
    )
    db.commit()

    form4.publish_leaderboard_state(db, 0.2)

    assert svc.metrics.get("alert_leaderboard_insiders") == 1
    assert svc.metrics.get("alert_leaderboard_eligible") == 1
    assert svc.metrics.get("alert_leaderboard_scored") == 1
    assert svc.metrics.get("alert_leaderboard_transactions") == 0


def test_the_gauges_reach_the_metrics_snapshot(db, svc):
    """The gauges exist to leave the host, and the snapshot is the only way
    off it — `/metrics` binds to loopback and no read verb returns its body."""
    form4.publish_leaderboard_state(db, None)

    snapshot = svc.metrics.snapshot()
    for metric, _help in form4.LEADERBOARD_GAUGES.values():
        assert metric in snapshot
