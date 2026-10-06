"""form4-insider sends one alert per filing and direction.

On 2026-10-05 Berkshire Hathaway's LEN purchases produced eight alerts in the
same minute, one per transaction line. These tests hold the rule
that replaced that: the filter still decides per transaction, and the
transactions it passes are sent as one buy and at most one sell per filing.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import pytest
from _form4 import ACCESSION, CIK, Svc, form4, form4_common, make_funnel


def tx(**over) -> dict:
    base = {"tx_code": "P", "is_10b5_1": 0, "usd_value": 2_000_000.0,
            "shares": 10_000.0, "price": 200.0, "trade_date": "2026-10-02"}
    base.update(over)
    return base


# --- group_trades ----------------------------------------------------------

@pytest.mark.parametrize("qualifying, want", [
    pytest.param(
        [(tx(), "large_trade", "large trade $2,000,000")],
        [("P", 1, 2_000_000.0, 10_000.0, "large_trade")],
        id="one transaction is one trade"),
    pytest.param(
        [(tx(), "large_trade", "a")] * 8,
        [("P", 8, 16_000_000.0, 80_000.0, "large_trade")],
        id="eight buys in one filing are one trade"),
    pytest.param(
        [(tx(tx_code="S"), "large_trade", "s"), (tx(), "large_trade", "p")],
        [("P", 1, 2_000_000.0, 10_000.0, "large_trade"),
         ("S", 1, 2_000_000.0, 10_000.0, "large_trade")],
        id="a buy and a sell stay apart, buy first"),
    pytest.param(
        [(tx(), "large_trade", "a"), (tx(usd_value=500_000.0, shares=2_500.0), "top_tier", "b")],
        [("P", 2, 2_500_000.0, 12_500.0, "top_tier")],
        id="top_tier wins when any member is top_tier"),
    pytest.param([], [], id="nothing qualifying is nothing to send"),
])
def test_group_trades(qualifying, want):
    got = [(t["tx_code"], len(t["transactions"]), t["usd_value"], t["shares"], t["decision"])
           for t in form4.group_trades(qualifying)]
    assert got == want


def test_a_group_carries_the_average_price_the_date_range_and_the_largest_reason():
    [t] = form4.group_trades([
        (tx(usd_value=1_000_000.0, shares=10_000.0, trade_date="2026-10-03"), "large_trade", "small"),
        (tx(usd_value=3_000_000.0, shares=10_000.0, trade_date="2026-10-01"), "large_trade", "big"),
    ])
    assert t["price"] == 200.0, "volume-weighted: $4M over 20,000 shares"
    assert (t["trade_date"], t["last_trade_date"]) == ("2026-10-01", "2026-10-03")
    assert t["reason"] == "big"


# --- the rendered alert ----------------------------------------------------

PARSED = {"insider_cik": "0001067983", "issuer_cik": "0000920760",
          "insider_name": "BERKSHIRE HATHAWAY INC", "relationship": "10% Owner",
          "ticker": "LEN"}


def test_a_grouped_alert_says_how_many_transactions_and_over_which_dates():
    [t] = form4.group_trades([
        (tx(trade_date="2026-10-01"), "large_trade", "large trade $2,000,000"),
        (tx(trade_date="2026-10-02"), "large_trade", "large trade $2,000,000"),
    ])
    body = form4.format_alert(PARSED, t, ACCESSION, None)
    assert "$4,000,000 in 2 transactions" in body
    assert "20,000 @ avg $200.00" in body
    assert "2026-10-01 to 2026-10-02" in body


def test_a_single_transaction_alert_reads_as_before():
    [t] = form4.group_trades([(tx(), "large_trade", "large trade $2,000,000")])
    body = form4.format_alert(PARSED, t, ACCESSION, None)
    assert "$2,000,000  (10,000 @ $200.00)" in body
    assert "transactions" not in body
    assert " to " not in body.split("\n")[1], "one date, not a range"


# --- through process_filing ------------------------------------------------

@pytest.fixture
def db(tmp_path):
    return form4_common.init_db(str(tmp_path / "form4.db"))


@pytest.fixture
def svc(monkeypatch):
    s = Svc()
    monkeypatch.setattr(form4, "SVC", s)
    return s


def filing(monkeypatch, transactions):
    monkeypatch.setattr(form4, "fetch_filing_xml", lambda cik, acc: ("url", b"<xml/>"))
    monkeypatch.setattr(form4, "parse_form4_xml", lambda _b: dict(PARSED, transactions=transactions))
    monkeypatch.setattr(form4, "get_insider_stats", lambda _c, _cik: None)


def test_eight_qualifying_buys_in_one_filing_send_one_alert(db, svc, monkeypatch):
    """The shape of the 2026-10-05 LEN alerts."""
    filing(monkeypatch, [tx() for _ in range(8)])
    funnel = make_funnel(svc.metrics)

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 1

    assert len(svc.alerts) == 1
    assert svc.alerts[0].payload["usd_value"] == 16_000_000.0
    assert svc.metrics.get("alert_funnel_large_trade_total") == 8, "decisions count transactions"
    assert svc.metrics.get("alert_funnel_sent_total") == 1, "sent counts alerts"


def test_a_filing_that_buys_and_sells_sends_two(db, svc, monkeypatch):
    filing(monkeypatch, [tx(), tx(tx_code="S"), tx(), tx(tx_code="A")])
    funnel = make_funnel(svc.metrics)

    with funnel.cycle():
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 2

    assert [a.payload["tx_code"] for a in svc.alerts] == ["P", "S"]
    assert [len(a.payload["transactions"]) for a in svc.alerts] == [2, 1]
    assert svc.metrics.get("alert_funnel_code_not_actionable_total") == 1
