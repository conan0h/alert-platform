"""Tests for the price source the form4 scorer depends on (backlog #39).

The leaderboard is unscored because `form4_scorer.py` has never run, and
running it is only worth deciding if it can get prices. These pin the v8
chart parser, the outcome the probe reports for each way a fetch can fail,
and that the probe reaches the metrics snapshot without ever raising.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests
from _form4 import Svc, form4, form4_common

# Three NYSE sessions, Wed 30 Sep to Fri 2 Oct, as v8 returns them: bar open
# 09:30 ET (13:30Z) with gmtoffset -14400; the middle one has a null close.
CHART = {
    "chart": {
        "result": [{
            "meta": {"symbol": "SPY", "gmtoffset": -14400},
            "timestamp": [1790775000, 1790861400, 1790947800],
            "indicators": {
                "quote": [{"close": [571.0, None, 573.0]}],
                "adjclose": [{"adjclose": [570.5, None, 572.5]}],
            },
        }],
        "error": None,
    }
}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def svc(monkeypatch):
    s = Svc()
    s.metrics.declare_gauge(form4.PRICE_PROBE_GAUGE, form4.PRICE_PROBE_HELP)
    monkeypatch.setattr(form4, "SVC", s)
    return s


def respond(monkeypatch, resp):
    seen = []

    def get(url, **_kw):
        seen.append(url)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(form4_common.requests, "get", get)
    return seen


# --- parse_chart -------------------------------------------------------------

def test_the_chart_parses_to_trading_dates_and_adjusted_closes():
    assert form4_common.parse_chart(CHART) == {
        "2026-09-30": 570.5,
        "2026-10-02": 572.5,
    }


def test_without_adjclose_the_plain_close_is_used():
    body = {"chart": {"result": [{
        "meta": {"gmtoffset": -14400},
        "timestamp": [1790861400],
        "indicators": {"quote": [{"close": [571.0]}]},
    }]}}
    assert form4_common.parse_chart(body) == {"2026-10-01": 571.0}


@pytest.mark.parametrize("body", [
    {"chart": {"result": None, "error": {"code": "Not Found"}}},
    {"chart": {"result": []}},
    {"finance": {"error": "unauthorized"}},
    {"chart": {"result": [{"meta": {}, "timestamp": [1790775000, 1790861400],
                           "indicators": {"quote": [{"close": [571.0]}]}}]}},
    [],
])
def test_a_body_that_is_not_a_chart_raises(body):
    with pytest.raises(ValueError):
        form4_common.parse_chart(body)


# --- fetch_yahoo_closes: the outcome names the failure -----------------------

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize("resp,outcome,closes", [
    (_Resp(200, CHART), "200", 2),
    (_Resp(401), "401", 0),
    (_Resp(429), "429", 0),
    (_Resp(200, ValueError("not json")), "unparsable", 0),
    (_Resp(200, {"chart": {"result": None}}), "unparsable", 0),
    (requests.ConnectionError("down"), "ConnectionError", 0),
    (requests.Timeout("slow"), "Timeout", 0),
])
def test_each_failure_has_its_own_outcome(monkeypatch, resp, outcome, closes):
    respond(monkeypatch, resp)
    got_outcome, got = form4_common.fetch_yahoo_closes("SPY", START, END)
    assert (got_outcome, len(got)) == (outcome, closes)


def test_the_request_goes_to_the_v8_chart_endpoint(monkeypatch):
    seen = respond(monkeypatch, _Resp(200, CHART))
    form4_common.fetch_yahoo_closes("SPY", START, END)
    assert seen[0].startswith("https://query1.finance.yahoo.com/v8/finance/chart/SPY?")
    assert f"period1={int(START.timestamp())}" in seen[0]


# --- fetch_price_history keeps its contract ----------------------------------

def test_fetched_prices_are_written_through_to_the_cache(monkeypatch, tmp_path):
    conn = form4_common.init_db(str(tmp_path / "form4.db"))
    respond(monkeypatch, _Resp(200, CHART))

    assert form4_common.fetch_price_history("spy", START, END, cache_conn=conn) == {
        "2026-09-30": 570.5, "2026-10-02": 572.5,
    }
    rows = conn.execute("SELECT ticker, date, close FROM price_cache ORDER BY date").fetchall()
    assert rows == [("SPY", "2026-09-30", 570.5), ("SPY", "2026-10-02", 572.5)]


def test_a_failed_fetch_returns_what_the_cache_holds(monkeypatch, tmp_path):
    conn = form4_common.init_db(str(tmp_path / "form4.db"))
    conn.execute("INSERT INTO price_cache (ticker, date, close) VALUES ('SPY', '2026-09-02', 560.0)")
    conn.commit()
    respond(monkeypatch, _Resp(401))

    assert form4_common.fetch_price_history("SPY", START, END, cache_conn=conn) == {
        "2026-09-02": 560.0,
    }


# --- the startup probe -------------------------------------------------------

def test_a_working_source_reports_its_closes(monkeypatch, svc):
    respond(monkeypatch, _Resp(200, CHART))

    assert form4.probe_price_source(END) == ("200", 2)
    assert svc.metrics.get(form4.PRICE_PROBE_GAUGE) == 2


def test_a_dead_source_reads_zero_in_the_snapshot(monkeypatch, svc):
    respond(monkeypatch, _Resp(401))

    assert form4.probe_price_source(END) == ("401", 0)
    assert svc.metrics.snapshot()[form4.PRICE_PROBE_GAUGE] == 0


def test_the_probe_never_raises(monkeypatch, svc):
    """The probe runs before the poll loop; an exception there would stop the
    alerter starting, which is a worse outcome than not knowing."""
    def boom(*_a, **_kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", boom)
    assert form4.probe_price_source(END) == ("RuntimeError", 0)
    assert svc.metrics.get(form4.PRICE_PROBE_GAUGE) == 0
