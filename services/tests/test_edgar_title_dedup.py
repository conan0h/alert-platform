"""edgar-mna sends one story once, however many wires carry it (backlog #45).

Dedup was keyed on category plus link, so a release syndicated by two feeds
under one headline alerted twice: Sun Life, 2026-10-08 21:02Z, two sends in
the same minute. These tests run the real `_process_hits` against a real
SQLite file.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from _loader import load_service_main  # noqa: E402
from alertlib.funnel import METRIC_PREFIX, CycleFunnel  # noqa: E402
from alertlib.health import Metrics  # noqa: E402

edgar = load_service_main("edgar_mna", "edgar_mna_main")

SUN_LIFE = "Sun Life cautions shareholders regarding Ocehan LLC's below-market bid for shares"


class _Svc:
    def __init__(self):
        self.metrics = Metrics("test", "v0.0.0")
        self.sent: list[str] = []

    def send_alert(self, alert) -> bool:
        self.sent.append(alert.title)
        return True


@pytest.fixture
def svc(monkeypatch, tmp_path):
    s = _Svc()
    monkeypatch.setattr(edgar, "SVC", s)
    monkeypatch.setattr(edgar, "FUNNEL", CycleFunnel(
        s.metrics, edgar.FUNNEL_STAGES, log=logging.getLogger("test"), cohort=False))
    monkeypatch.setattr(edgar, "DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.setattr(edgar, "enrich_hit", lambda hit: None)
    return s


def _hit(source: str, title: str, link: str, category: str = "TENDER_OFFER") -> edgar.Hit:
    return edgar.Hit(source=source, category=category, title=title, link=link,
                     summary="stub", published="")


def _count(svc, stage: str) -> int:
    return int(svc.metrics.get(f"{METRIC_PREFIX}_{stage}_total"))


def test_one_headline_from_two_wires_sends_once(svc):
    conn = edgar.init_db()
    edgar._process_hits(conn, [
        _hit("GlobeNewswire-MA", SUN_LIFE, "https://globenewswire.example/a"),
        _hit("PRNewswire-AllNews", SUN_LIFE, "https://prnewswire.example/b"),
    ])
    assert svc.sent == [SUN_LIFE]
    assert _count(svc, "duplicate_title") == 1


def test_case_punctuation_and_category_do_not_split_a_story(svc):
    conn = edgar.init_db()
    edgar._process_hits(conn, [
        _hit("GlobeNewswire-MA", SUN_LIFE, "https://a.example/1", "TENDER_OFFER"),
        _hit("Yahoo-Finance-Headlines", SUN_LIFE.upper().replace("'", "’") + ".",
             "https://b.example/2", "UNSOLICITED_PROPOSAL"),
    ])
    assert len(svc.sent) == 1


def test_a_dropped_duplicate_stays_dropped_on_the_next_poll(svc):
    """Marked seen by link too, so it is `already_seen` next time, not resent
    once the title window lapses."""
    conn = edgar.init_db()
    batch = [
        _hit("GlobeNewswire-MA", SUN_LIFE, "https://a.example/1"),
        _hit("PRNewswire-AllNews", SUN_LIFE, "https://b.example/2"),
    ]
    edgar._process_hits(conn, batch)
    conn.execute("UPDATE seen_title SET seen_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),))
    edgar._process_hits(conn, batch)
    assert len(svc.sent) == 1
    assert _count(svc, "already_seen") == 2


def test_the_same_headline_a_day_later_alerts_again(svc):
    conn = edgar.init_db()
    edgar._process_hits(conn, [_hit("GlobeNewswire-MA", SUN_LIFE, "https://a.example/1")])
    later = datetime.now(timezone.utc) - timedelta(hours=edgar.TITLE_DEDUP_HOURS, minutes=1)
    conn.execute("UPDATE seen_title SET seen_at = ?", (later.isoformat(),))
    edgar._process_hits(conn, [_hit("PRNewswire-AllNews", SUN_LIFE, "https://b.example/2")])
    assert len(svc.sent) == 2


def test_edgar_filings_with_one_title_are_not_merged(svc):
    """Two filers' SC 13D on one subject share a title; each is its own filing."""
    conn = edgar.init_db()
    title = "SC 13D - Acme Corp (0000000001) (Subject)"
    edgar._process_hits(conn, [
        _hit("EDGAR-SC13D", title, "https://sec.example/1", "EDGAR_SC13D"),
        _hit("EDGAR-SC13D", title, "https://sec.example/2", "EDGAR_SC13D"),
    ])
    assert len(svc.sent) == 2


def test_different_headlines_both_send(svc):
    conn = edgar.init_db()
    edgar._process_hits(conn, [
        _hit("GlobeNewswire-MA", "Bidco commences tender offer for Target Inc", "https://a.example/1"),
        _hit("GlobeNewswire-MA", "Bidco extends tender offer for Target Inc", "https://a.example/2"),
    ])
    assert len(svc.sent) == 2
