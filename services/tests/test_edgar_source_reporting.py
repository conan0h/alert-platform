"""Tests for how edgar-mna reports a failing feed.

The gap these close: this service polls sixteen feeds across three tiers and
said nothing about which of them work. A failed fetch logged one WARNING and
a dead feed logged that WARNING forever, so "PRNewswire 404'd twice" and
"PRNewswire has been dead for three weeks" produced the same output — and
nothing ever stated the whole picture. `fda-catalysts` ran a month with two
permanently-403 feeds on exactly that blindness.

Against the version before this change the whole file errors at its fixture:
`edgar_mna.main` had no `SOURCES` to substitute. The behavioural regression
guard that survives a partial adoption is
`test_every_configured_feed_reaches_the_tracker`, which fails on any feed
fetched by a path that does not report.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import pytest
from _loader import load_service_main  # noqa: E402
from alertlib.sources import PRESUMED_DEAD_AFTER, SourceHealth  # noqa: E402

edgar = load_service_main("edgar_mna", "edgar_mna_main")


class _Metrics:
    """Minimal stand-in for alertlib.Metrics that records what it was told."""

    def __init__(self):
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}

    def inc(self, name: str, amount: float = 1.0) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + amount

    def set(self, name: str, value: float) -> None:
        self.gauges[name] = float(value)


class _Svc:
    def __init__(self):
        self.metrics = _Metrics()


class _Resp:
    """An empty but well-formed feed: fetched fine, matched nothing."""

    content = b"<rss><channel></channel></rss>"

    def raise_for_status(self):
        return None


@pytest.fixture
def svc(monkeypatch):
    """Bind the module-level SVC the way main() does, and a fresh tracker."""
    s = _Svc()
    monkeypatch.setattr(edgar, "SVC", s)
    monkeypatch.setattr(edgar, "SOURCES", SourceHealth())
    return s


@pytest.fixture
def always_404(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError(
            "404 Client Error: Not Found for url: "
            "https://www.prnewswire.com/rss/news-releases-list.rss/"
        )
    monkeypatch.setattr(edgar.requests, "get", boom)


@pytest.fixture
def always_ok(monkeypatch):
    monkeypatch.setattr(edgar.requests, "get", lambda *a, **k: _Resp())


def test_repeated_failures_do_not_log_every_cycle(svc, always_404, caplog):
    """200 failing fetches must not produce 200 log records."""
    caplog.set_level("DEBUG", logger="edgar-mna")

    for _ in range(200):
        assert edgar.fetch_feed("PRNewswire-AllNews", "https://example.invalid/feed") == []

    records = [r for r in caplog.records if "PRNewswire-AllNews" in r.getMessage()]
    assert len(records) < 10, [r.getMessage() for r in records]

    # Every attempt is still counted, which is the half that must not regress
    # when the logging is quietened.
    assert svc.metrics.counters["alert_source_fetches_total"] == 200
    assert svc.metrics.counters["alert_source_fetch_failures_total"] == 200


def test_the_edgar_tier_is_tracked_too(svc, always_404, caplog):
    """`fetch_edgar` had its own log.warning and its own way of being missed.

    Wiring only `fetch_feed` would leave the five SEC feeds — the ones this
    service is named for — outside the accounting, and the summary line would
    read 11/11 healthy while EDGAR was down.
    """
    caplog.set_level("DEBUG", logger="edgar-mna")

    for _ in range(PRESUMED_DEAD_AFTER):
        assert edgar.fetch_edgar("EDGAR-8K", "https://example.invalid/atom", "8-K") == []

    assert svc.metrics.counters["alert_source_fetch_failures_total"] == PRESUMED_DEAD_AFTER
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "EDGAR-8K" in errors[0].getMessage()
    assert "presumed dead" in errors[0].getMessage()


def test_every_configured_feed_reaches_the_tracker(svc, always_ok):
    """All sixteen sources must be visible to `summary()`, not just the wires.

    The failure this catches is a feed added to one of the three lists and
    fetched by some path that does not report: it would poll forever and
    never appear in the health line, which is the condition the line exists
    to make impossible.
    """
    for name, url in edgar.WIRE_FEEDS:
        edgar.fetch_feed(name, url)
    for name, url, ftype in edgar.EDGAR_FEEDS:
        edgar.fetch_edgar(name, url, ftype)
    for name, url in edgar.PRESS_FEEDS:
        edgar.fetch_feed(name, url)

    configured = {n for n, _ in edgar.WIRE_FEEDS}
    configured |= {n for n, _, _ in edgar.EDGAR_FEEDS}
    configured |= {n for n, _ in edgar.PRESS_FEEDS}

    assert set(edgar.SOURCES.sources) == configured
    assert edgar.SOURCES.summary() == f"all {len(configured)} sources healthy"


def test_a_flapping_source_stays_quiet(svc, monkeypatch, caplog):
    """The condition read from the host on 2026-09-27, replayed.

    PRNewswire-AllNews answered `edgar-mna` with 404, 502, 503 and read
    timeouts roughly one cycle in five, recovering immediately each time. The
    source is neither healthy nor dead, and the log is the wrong instrument
    for it: what it must not do is produce a line per flap.
    """
    caplog.set_level("DEBUG", logger="edgar-mna")

    calls = {"n": 0}

    def every_fifth_fails(*_a, **_kw):
        calls["n"] += 1
        # Not every fifth call exactly: the run must end on a success, so
        # that what is asserted is the settled state after twenty flaps and
        # not a blip still in flight.
        if calls["n"] % 5 == 3:
            raise RuntimeError("503 Server Error: Service Unavailable")
        return _Resp()

    monkeypatch.setattr(edgar.requests, "get", every_fifth_fails)

    for _ in range(100):
        edgar.fetch_feed("PRNewswire-AllNews", "https://example.invalid/feed")

    assert [r.getMessage() for r in caplog.records] == []
    assert edgar.SOURCES.summary_if_changed() == "all 1 sources healthy"

    # Silent in the journal, fully present in the counters — 20 failures in
    # 100 attempts is the number the rate question actually needs.
    assert svc.metrics.counters["alert_source_fetches_total"] == 100
    assert svc.metrics.counters["alert_source_fetch_failures_total"] == 20


def test_a_sustained_outage_still_speaks(svc, always_404, caplog):
    """The quietening must not reach the case the tracker exists for."""
    caplog.set_level("DEBUG", logger="edgar-mna")

    for _ in range(PRESUMED_DEAD_AFTER):
        edgar.fetch_feed("Yahoo-Finance-Headlines", "https://example.invalid/feed")

    levels = [r.levelname for r in caplog.records]
    assert "WARNING" in levels and "ERROR" in levels
    assert svc.metrics.gauges["alert_sources_failing"] == 1
    assert svc.metrics.gauges["alert_sources_presumed_dead"] == 1
    assert "Yahoo-Finance-Headlines" in edgar.SOURCES.summary()


def test_a_good_fetch_clears_the_gauges(svc, always_404, monkeypatch):
    for _ in range(PRESUMED_DEAD_AFTER):
        edgar.fetch_feed("NYT-Business", "https://example.invalid/feed")
    assert svc.metrics.gauges["alert_sources_failing"] == 1

    monkeypatch.setattr(edgar.requests, "get", lambda *a, **k: _Resp())
    edgar.fetch_feed("NYT-Business", "https://example.invalid/feed")

    assert svc.metrics.gauges["alert_sources_failing"] == 0
    assert svc.metrics.gauges["alert_sources_presumed_dead"] == 0


def test_enrichment_fetches_are_not_sources(svc, always_404):
    """`fetch_pr_body` pulls one article, not a feed.

    Counting it would put a per-article URL in a tracker keyed by feed name
    and make the healthy count meaningless.
    """
    assert edgar.fetch_pr_body("https://example.invalid/some-release") is None
    assert edgar.SOURCES.sources == {}
    assert svc.metrics.counters == {}
