"""Tests for form4-insider's send/record ordering.

The property under test: an alert is never sent for a filing whose dedup record
did not persist. Violating it produced the 2026-09-21 incident, where the same
nine DELL insider sales were re-sent every twenty seconds because
`mark_alerted` raised `database is locked` after the sends had already gone out.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import sqlite3

import pytest
from _form4 import ACCESSION, CIK, Svc, form4, form4_common, make_funnel


@pytest.fixture
def db(tmp_path):
    """A real database on disk, so a real lock can be taken against it.

    `init_db` opens with `timeout=30.0`, which is right in production and would
    make each locked-write test wait half a minute. The pragma shortens only the
    wait, not the behaviour being tested.
    """
    conn = form4_common.init_db(str(tmp_path / "form4.db"))
    conn.execute("PRAGMA busy_timeout=50")
    return conn


@pytest.fixture
def fake_filing(monkeypatch):
    """One filing that parses to one transaction which always qualifies.

    Patched at the seams `process_filing` calls, so the test drives the real
    ordering logic rather than a copy of it. Yields the list of Alert objects
    that reached `Service.send_alert`.

    The transaction carries `trade_date` because that is the key
    `parse_form4_xml` produces; an earlier version of this fixture said
    `tx_date`, which nothing read until `build_alert` did.
    """
    svc = Svc()

    monkeypatch.setattr(form4, "fetch_primary_xml", lambda cik, acc: ("url", b"<xml/>"))
    monkeypatch.setattr(form4, "parse_form4_xml", lambda _b: {
        "insider_cik": "0001005731",
        "insider_name": "Silver Lake Partners IV, L.P.",
        "ticker": "DELL",
        "issuer_name": "Dell Technologies Inc.",
        "transactions": [{
            "tx_code": "S", "is_10b5_1": 0, "usd_value": 4_612_795.0,
            "shares": 40_000.0, "price": 115.32, "trade_date": "2026-09-21",
        }],
    })
    monkeypatch.setattr(form4, "get_insider_stats", lambda _c, _cik: None)
    monkeypatch.setattr(form4, "should_alert", lambda *_a: ("large_trade", "large trade"))
    monkeypatch.setattr(form4, "format_alert", lambda *_a: "alert body")
    monkeypatch.setattr(form4, "SVC", svc)
    return svc.alerts


@pytest.fixture
def funnel():
    """A real funnel over a real registry, so the extra argument these tests
    now pass is the one production uses rather than a no-op stub."""
    return make_funnel()


def test_a_qualifying_filing_alerts_once(db, fake_filing, funnel):
    assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 1
    assert len(fake_filing) == 1

    # Second poll of the same filing must be a no-op. This is the behaviour the
    # incident lacked, not because this check was missing but because the record
    # it reads was never written.
    assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
    assert len(fake_filing) == 1


def test_nothing_is_sent_when_the_filing_cannot_be_recorded(db, fake_filing, tmp_path, funnel):
    """The regression. A writer holding the lock must stop sends, not cause them.

    This takes a genuine EXCLUSIVE lock rather than patching the failure in, so
    it reproduces `sqlite3.OperationalError: database is locked` from the real
    driver against the real schema.
    """
    blocker = sqlite3.connect(str(tmp_path / "form4.db"), timeout=0)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
        assert fake_filing == [], (
            "sent an alert for a filing that could not be recorded; the next "
            "poll will send it again, and keep sending while the lock is held"
        )
        assert form4.SVC.metrics.get("alert_sends_refused_total") == 1
    finally:
        blocker.rollback()
        blocker.close()


def test_the_filing_is_retried_once_the_lock_clears(db, fake_filing, tmp_path, funnel):
    """Refusing to send must not silently drop the filing forever."""
    blocker = sqlite3.connect(str(tmp_path / "form4.db"), timeout=0)
    blocker.execute("BEGIN EXCLUSIVE")
    assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
    blocker.rollback()
    blocker.close()

    assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 1
    assert len(fake_filing) == 1


def test_an_unparseable_filing_is_recorded_and_sends_nothing(db, fake_filing, monkeypatch, funnel):
    monkeypatch.setattr(form4, "parse_form4_xml", lambda _b: None)
    assert form4.process_filing(db, ACCESSION, CIK, None, funnel) == 0
    assert fake_filing == []
    assert form4.is_already_alerted(db, ACCESSION) is True


def test_mark_alerted_reports_failure_rather_than_raising(db, tmp_path):
    blocker = sqlite3.connect(str(tmp_path / "form4.db"), timeout=0)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        assert form4.mark_alerted(db, ACCESSION) is False
    finally:
        blocker.rollback()
        blocker.close()

    # And the connection is still usable afterwards, so one locked write does
    # not poison the rest of the cycle.
    assert form4.mark_alerted(db, ACCESSION) is True
