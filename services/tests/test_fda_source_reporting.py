"""Tests for how fda-catalysts reports a failing feed.

The regression these pin: `fetch_feed` used to call `log.warning` on every
failed fetch, so two permanently-403 feeds produced about 3,800 lines a day —
two thirds of the service's output, and enough to displace the alert content
from the 24 KB the `logs` read verb captures. Against that version
`test_repeated_failures_do_not_log_every_cycle` fails with 200 records.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import alertlib.sources as alertlib_sources  # noqa: E402
import pytest
from _loader import load_service_main  # noqa: E402
from alertlib.funnel import CycleFunnel  # noqa: E402
from alertlib.sources import DEAD_RETRY_SEC, PRESUMED_DEAD_AFTER, SourceHealth  # noqa: E402

fda = load_service_main("fda_catalysts", "fda_catalysts_main")


class _Metrics:
    """Minimal stand-in for alertlib.Metrics that records what it was told."""

    def __init__(self):
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}

    def inc(self, name: str, amount: float = 1.0) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + amount

    def set(self, name: str, value: float) -> None:
        self.gauges[name] = float(value)

    def declare_counter(self, name: str, _help: str) -> None:
        self.counters.setdefault(name, 0.0)

    def declare_gauge(self, name: str, _help: str) -> None:
        self.gauges.setdefault(name, 0.0)


class _Svc:
    def __init__(self):
        self.metrics = _Metrics()


@pytest.fixture
def svc(monkeypatch):
    """Bind the module-level SVC the way main() does, and a fresh tracker."""
    s = _Svc()
    monkeypatch.setattr(fda, "SVC", s)
    monkeypatch.setattr(fda, "SOURCES", SourceHealth())
    monkeypatch.setattr(fda, "FUNNEL", CycleFunnel(
        s.metrics, fda.FUNNEL_STAGES, log=logging.getLogger("test"), cohort=False))
    return s


@pytest.fixture
def always_403(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("403 Client Error: Forbidden for url: https://example.invalid/feed")
    monkeypatch.setattr(fda.requests, "get", boom)

@pytest.fixture
def clock(monkeypatch):
    """The tracker's clock, moved only by the test."""
    now = [1_000_000.0]
    monkeypatch.setattr(alertlib_sources, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def test_repeated_failures_do_not_log_every_cycle(svc, always_403, clock, caplog):
    """200 failing fetches must not produce 200 log records."""
    caplog.set_level("DEBUG", logger="fda-catalysts")

    # A retry interval apart, so every call fetches even once presumed dead.
    for _ in range(200):
        clock[0] += DEAD_RETRY_SEC
        assert fda.fetch_feed("FiercePharma", "https://example.invalid/feed") == []

    records = [r for r in caplog.records if "FiercePharma" in r.getMessage()]
    assert len(records) < 10, [r.getMessage() for r in records]

    # Every attempt is still counted, which is the half that must not regress
    # when the logging is quietened.
    assert svc.metrics.counters["alert_source_fetches_total"] == 200
    assert svc.metrics.counters["alert_source_fetch_failures_total"] == 200


def test_a_dead_feed_is_named_at_error(svc, always_403, caplog):
    caplog.set_level("DEBUG", logger="fda-catalysts")

    for _ in range(PRESUMED_DEAD_AFTER):
        fda.fetch_feed("EndpointsNews", "https://example.invalid/feed")

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "EndpointsNews" in errors[0].getMessage()
    assert "presumed dead" in errors[0].getMessage()


def test_failing_source_gauges_track_the_condition(svc, always_403):
    for _ in range(PRESUMED_DEAD_AFTER):
        fda.fetch_feed("EndpointsNews", "https://example.invalid/feed")

    assert svc.metrics.gauges["alert_sources_failing"] == 1
    assert svc.metrics.gauges["alert_sources_presumed_dead"] == 1


def test_a_good_fetch_clears_the_gauges(svc, monkeypatch, always_403, clock):
    for _ in range(PRESUMED_DEAD_AFTER):
        fda.fetch_feed("EndpointsNews", "https://example.invalid/feed")
    assert svc.metrics.gauges["alert_sources_failing"] == 1

    class _Resp:
        content = b"<rss><channel></channel></rss>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(fda.requests, "get", lambda *a, **k: _Resp())
    clock[0] += DEAD_RETRY_SEC
    fda.fetch_feed("EndpointsNews", "https://example.invalid/feed")

    assert svc.metrics.gauges["alert_sources_failing"] == 0
    assert svc.metrics.gauges["alert_sources_presumed_dead"] == 0


def test_each_destination_gets_its_own_user_agent(svc, monkeypatch):
    """Wire feeds must not be sent the SEC contact string.

    `main()` used to write the EDGAR User-Agent into HTTP_HEADERS_DEFAULT,
    which fetch_edgar_8k sets per request anyway — so the one place it was
    needed did not rely on it, and the twelve press feeds got it instead.
    Commercial press behind Cloudflare commonly refuses that.
    """
    seen: list[dict] = []

    class _Resp:
        content = b"<rss><channel></channel></rss>"

        def raise_for_status(self):
            return None

    def capture(url, headers=None, timeout=None, **kw):
        seen.append(headers or {})
        return _Resp()

    monkeypatch.setattr(fda.requests, "get", capture)
    monkeypatch.setattr(fda, "EDGAR_USER_AGENT", "SEC-Contact someone@example.invalid")

    fda.fetch_feed("FiercePharma", "https://example.invalid/feed")
    fda.fetch_edgar_8k("EDGAR-8K", "https://example.invalid/atom")

    wire_ua, edgar_ua = seen[0]["User-Agent"], seen[1]["User-Agent"]
    assert wire_ua != edgar_ua
    assert "FDA-CatalystBot/1.0" in wire_ua
    assert "someone@example.invalid" in edgar_ua
    # The shared default must not have been mutated by either call.
    assert fda.HTTP_HEADERS_DEFAULT["User-Agent"] == wire_ua


@pytest.mark.parametrize("fetch", [
    lambda: fda.fetch_feed("GlobeNewswire-Pharma", "https://example.invalid/feed"),
    lambda: fda.fetch_edgar_8k("EDGAR-8K", "https://example.invalid/atom"),
], ids=["wire", "edgar"])
def test_a_dead_feed_is_retried_on_an_interval_not_every_cycle(svc, clock, monkeypatch, fetch):
    """GlobeNewswire refused seven feeds with 403 at every 45s cycle (2026-10-11)."""
    requested = []

    def refuse(url, *_a, **_kw):
        requested.append(url)
        raise RuntimeError("403 Client Error: Forbidden for url: " + url)

    monkeypatch.setattr(fda.requests, "get", refuse)
    for _ in range(PRESUMED_DEAD_AFTER):
        fetch()
    assert len(requested) == PRESUMED_DEAD_AFTER

    clock[0] += fda.WIRE_POLL_INTERVAL_SECONDS
    assert fetch() == []
    assert len(requested) == PRESUMED_DEAD_AFTER
    assert svc.metrics.counters["alert_source_fetches_total"] == PRESUMED_DEAD_AFTER

    clock[0] += DEAD_RETRY_SEC
    fetch()
    assert len(requested) == PRESUMED_DEAD_AFTER + 1
