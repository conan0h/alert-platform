"""What each service records when it alerts.

Every alert now leaves the service through `Service.send_alert`, which writes
the archive row, delivers, and settles the outcome. These tests cover the part
each service owns: turning its own domain object into the Alert that gets
stored. The archive itself is covered by test_archive.py.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import re
from pathlib import Path

from _loader import SERVICES, load_service_main  # noqa: E402

form4 = load_service_main("form4_insider", "form4_insider_main")
fda = load_service_main("fda_catalysts", "fda_catalysts_main")
edgar = load_service_main("edgar_mna", "edgar_mna_main")
trials = load_service_main("clinical_trials", "clinical_trials_main")


# -- form4-insider ----------------------------------------------------------

PARSED = {
    "insider_cik": "0001005731",
    "issuer_cik": "0000826083",
    "insider_name": "Silver Lake Partners IV, L.P.",
    "relationship": "10% Owner",
    "ticker": "dell",
}
TX = {"tx_code": "S", "trade_date": "2026-09-21", "shares": 40_000.0,
      "price": 115.32, "usd_value": 4_612_795.0}


def test_form4_records_the_fields_an_analysis_will_group_by():
    alert = form4.build_alert(PARSED, TX, "0001193125-26-396718", "large trade", "body")
    assert alert.source == "SEC EDGAR Form 4"
    assert alert.ticker == "DELL", "tickers are upper-cased so grouping does not split"
    assert alert.reason == "large trade"
    assert alert.body == "body"
    assert alert.payload["usd_value"] == 4_612_795.0
    assert alert.payload["tx_code"] == "S"
    assert alert.payload["accession"] == "0001193125-26-396718"


def test_form4_gives_two_trades_in_one_filing_two_keys():
    """`alerted` is keyed on the accession, so it cannot tell two qualifying
    transactions in one Form 4 apart. The archive has to."""
    buy = dict(TX, tx_code="P", shares=1_000.0)
    a = form4.build_alert(PARSED, TX, "0001193125-26-396718", "r", "body")
    b = form4.build_alert(PARSED, buy, "0001193125-26-396718", "r", "body")
    assert a.dedup_key != b.dedup_key


def test_form4_survives_a_filing_with_no_ticker():
    alert = form4.build_alert(dict(PARSED, ticker=None), TX, "acc", "r", "body")
    assert alert.ticker == ""
    assert alert.title.startswith("? S")


# -- fda-catalysts and edgar-mna -------------------------------------------

def test_fda_records_the_feed_that_produced_the_hit():
    hit = fda.Hit(source="FDA Press", category="APPROVAL", title="FDA approves X",
                  link="https://fda.gov/x", summary="…", published="2026-09-21",
                  ticker="abcd", matched_phrase="approves")
    alert = fda.build_alert(hit, "body")
    assert alert.source == "FDA Press"
    assert alert.dedup_key == hit.fingerprint(), "same identity as the dedup row"
    assert alert.ticker == "ABCD"
    assert alert.reason == "APPROVAL"
    assert alert.payload["link"] == "https://fda.gov/x"
    assert alert.payload["urgency"] == fda.category_meta("APPROVAL").get("urgency")


def test_edgar_records_the_deal_facts_it_extracted():
    hit = edgar.Hit(source="EDGAR 8-K", category="MERGER_AGREEMENT",
                    title="Acquirer to buy Target", link="https://sec.gov/x",
                    summary="…", published="2026-09-21", ticker="tgt")
    hit.facts.offer_price = 68.0
    hit.facts.premium_pct = 0.31
    hit.facts.deal_structure = "cash"
    alert = edgar.build_alert(hit, "body")
    assert alert.ticker == "TGT"
    assert alert.payload["offer_price"] == 68.0
    assert alert.payload["premium_pct"] == 0.31
    assert alert.payload["deal_structure"] == "cash"


def test_a_long_summary_is_bounded_rather_than_stored_whole():
    """An RSS summary can be the entire article. The archive is meant to be
    queried, so a row stays a row."""
    hit = fda.Hit(source="s", category="APPROVAL", title="t" * 500,
                  link="l", summary="x" * 50_000, published="p")
    alert = fda.build_alert(hit, "body")
    assert len(alert.title) == 200
    assert len(alert.payload["summary"]) == 2000


# -- clinical-trials --------------------------------------------------------

TRIAL = {
    "nct_id": "NCT05555555",
    "title": "A Study of Something in Advanced Disease",
    "sponsor": "Acme Pharma",
    "status": "COMPLETED",
    "phases": '["PHASE3"]',
    "conditions": ["Condition A"],
    "drugs": ["Drug B"],
    "results_posted_date": None,
}


def test_trials_keys_on_the_signal_as_well_as_the_study():
    """One study legitimately alerts more than once over its life; a status
    change and posted results are different events, not a repeat."""
    a = trials.build_alert(TRIAL, "COMPLETED", "d", "👀 WATCH", "body")
    b = trials.build_alert(TRIAL, "RESULTS_POSTED", "d", "👀 WATCH", "body")
    assert a.dedup_key != b.dedup_key
    assert a.dedup_key.startswith("NCT05555555")


def test_trials_leaves_the_ticker_empty_rather_than_inventing_one():
    alert = trials.build_alert(TRIAL, "COMPLETED", "d", "👀 WATCH", "body")
    assert alert.ticker == ""
    assert alert.payload["sponsor"] == "Acme Pharma"
    assert alert.source == "ClinicalTrials.gov"


# -- the property that holds across all four --------------------------------

ALERT_SEND = re.compile(r"SVC\.send_alert\(\s*build_alert\(")
UNARCHIVED_SEND = re.compile(r"send_telegram\(\s*(format_alert\(|msg\b)")


def test_no_service_can_emit_an_alert_without_recording_it():
    """A source check, deliberately.

    The behavioural tests above prove each `build_alert` is right; they cannot
    prove a future change did not add a second, unarchived send path beside
    it. `send_telegram` still exists for the startup banner and the crash
    notice, which are operational messages rather than alerts — so the thing
    to forbid is passing a rendered alert to it.
    """
    for service in ("form4_insider", "fda_catalysts", "edgar_mna", "clinical_trials"):
        source = (Path(SERVICES) / service / "main.py").read_text()
        assert ALERT_SEND.search(source), f"{service} does not archive its alerts"
        assert not UNARCHIVED_SEND.search(source), (
            f"{service} sends a rendered alert straight to Telegram; route it "
            f"through SVC.send_alert so it reaches the archive"
        )
