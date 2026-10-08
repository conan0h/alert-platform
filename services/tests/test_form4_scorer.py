"""Tests for the in-process scorer (backlog #39).

The scorer has never run in production. These pin what decides its cost and
its output there: which buys are due, that a young trade or a priceless
ticker is not refetched every cycle, that a step stops at its time budget,
and that the leaderboard appears once the queue is empty.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from _form4 import Svc, form4, form4_backfill, form4_common, form4_scorer

TODAY = date(2026, 10, 8)


def daily(start: str, days: int, first: float = 100.0, step: float = 0.1) -> dict[str, float]:
    """A close for every calendar day: enough for the cache's density check."""
    d0 = datetime.fromisoformat(start)
    return {(d0 + timedelta(days=i)).strftime("%Y-%m-%d"): first + i * step for i in range(days)}


class Prices:
    """Stands in for the price source; records every ticker fetched."""

    def __init__(self, series: dict[str, dict[str, float]]):
        self.series = series
        self.fetched: list[str] = []

    def __call__(self, ticker, start, end):
        self.fetched.append(ticker)
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        closes = {d: c for d, c in self.series.get(ticker, {}).items() if s <= d <= e}
        return ("200" if closes else "404"), closes


@pytest.fixture
def conn(tmp_path):
    return form4_common.init_db(str(tmp_path / "form4.db"))


def add_trade(conn, ticker, trade_date, code="P", insider="0001", usd=200_000.0):
    conn.execute("INSERT OR IGNORE INTO insiders (insider_cik, name) VALUES (?, ?)",
                 (insider, f"Insider {insider}"))
    conn.execute(
        """INSERT INTO transactions (accession, insider_cik, ticker, trade_date, tx_code,
                                     shares, price, usd_value)
           VALUES ('acc', ?, ?, ?, ?, 1000, 200, ?)""",
        (insider, ticker, trade_date, code, usd),
    )
    conn.commit()


def fwd90(conn, ticker):
    return [r[0] for r in conn.execute(
        "SELECT fwd_ret_90 FROM transactions WHERE ticker = ? ORDER BY trade_date", (ticker,))]


# --- which buys are due ------------------------------------------------------

# TODAY - (90 + SETTLE_DAYS) is 2026-07-05.
@pytest.mark.parametrize("trade_date, code, due", [
    ("2026-06-01", "P", True),
    ("2026-07-05", "P", True),    # the first day a 90-day close is certain to exist
    ("2026-07-06", "P", False),
    ("2026-09-01", "P", False),   # young: refetching it would return nothing new
    ("2026-06-01", "S", False),   # sells never enter the leaderboard
    ("2026-06-01", "A", False),
])
def test_a_buy_is_due_once_its_90_day_close_exists(conn, trade_date, code, due):
    add_trade(conn, "ABC", trade_date, code)
    assert form4_scorer.tickers_needing_scoring(conn, False, TODAY) == (["ABC"] if due else [])


def test_a_buy_with_90_but_not_180_comes_due_again_at_180(conn):
    add_trade(conn, "ABC", "2026-03-01")
    conn.execute("UPDATE transactions SET fwd_ret_90 = 0.1")
    conn.commit()
    assert form4_scorer.tickers_needing_scoring(conn, False, TODAY) == ["ABC"]
    conn.execute("UPDATE transactions SET fwd_ret_180 = 0.2")
    conn.commit()
    assert form4_scorer.tickers_needing_scoring(conn, False, TODAY) == []


def test_a_trade_without_a_ticker_is_never_due(conn):
    add_trade(conn, None, "2026-01-05")
    assert form4_scorer.tickers_needing_scoring(conn, False, TODAY) == []


# --- scoring a ticker --------------------------------------------------------

def test_a_due_buy_gets_its_forward_returns(conn, monkeypatch):
    prices = Prices({"ABC": daily("2026-05-01", 160)})
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", prices)
    spy = daily("2026-05-01", 160, first=500.0, step=0.0)
    add_trade(conn, "ABC", "2026-06-01")

    assert form4_scorer.score_ticker(conn, "ABC", spy, False, TODAY) == "scored"
    (ret,) = fwd90(conn, "ABC")
    assert ret == pytest.approx(9.0 / 103.1)   # 06-01 at 103.1, 08-30 at 112.1
    assert form4_scorer.tickers_needing_scoring(conn, False, TODAY) == []


def test_prices_are_never_requested_past_today(conn, monkeypatch):
    seen = []

    def fetch(ticker, start, end):
        seen.append(end)
        return "200", daily("2026-05-01", 160)

    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", fetch)
    add_trade(conn, "ABC", "2026-06-01")
    form4_scorer.score_ticker(conn, "ABC", {}, False, TODAY)
    assert seen[0].date() <= TODAY


def test_a_ticker_with_no_prices_waits_a_week(conn, monkeypatch):
    prices = Prices({})
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", prices)
    add_trade(conn, "GONE", "2026-01-05")

    assert form4_scorer.score_ticker(conn, "GONE", {}, False, TODAY) == "no_prices"
    for days in range(form4_scorer.RETRY_DAYS):
        assert form4_scorer.tickers_needing_scoring(
            conn, False, TODAY + timedelta(days=days)) == []
    later = TODAY + timedelta(days=form4_scorer.RETRY_DAYS)
    assert form4_scorer.tickers_needing_scoring(conn, False, later) == ["GONE"]


# --- the price cache ---------------------------------------------------------

def test_a_dense_cache_that_reaches_both_ends_answers_without_a_fetch(conn, monkeypatch):
    prices = Prices({"ABC": daily("2026-01-01", 200)})
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", prices)
    start, end = datetime(2026, 1, 1), datetime(2026, 7, 1)
    form4_common.fetch_price_history("ABC", start, end, cache_conn=conn)
    form4_common.fetch_price_history("ABC", start, end, cache_conn=conn)
    assert prices.fetched == ["ABC"]


def test_a_dense_cache_that_stops_short_of_the_end_is_refetched(conn, monkeypatch):
    # Filled through mid-May; a later call wants closes to July. Before the
    # end check, density alone (135 of 181 days) passed and the July closes
    # were never fetched.
    conn.executemany("INSERT INTO price_cache (ticker, date, close) VALUES ('ABC', ?, ?)",
                     daily("2026-01-01", 135).items())
    prices = Prices({"ABC": daily("2026-01-01", 200)})
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", prices)
    got = form4_common.fetch_price_history("ABC", datetime(2026, 1, 1), datetime(2026, 7, 1),
                                           cache_conn=conn)
    assert prices.fetched == ["ABC"]
    assert max(got) == "2026-07-01"


# --- the incremental step ----------------------------------------------------

class Clock:
    def __init__(self, tick: float):
        self.now, self.tick = 0.0, tick

    def __call__(self) -> float:
        self.now += self.tick
        return self.now


def test_a_step_stops_at_its_budget_and_the_next_one_continues(conn, monkeypatch):
    series = {t: daily("2026-01-01", 280) for t in ("AAA", "BBB", "CCC", "SPY")}
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", Prices(series))
    for t in ("AAA", "BBB", "CCC"):
        add_trade(conn, t, "2026-03-02")
    # Every clock read advances 1s. Reads: deadline (1 -> 4.5), SPY (2),
    # AAA (3), BBB (4), CCC (5, past the deadline).
    scorer = form4_scorer.IncrementalScorer(conn, 3.5, clock=Clock(1.0), today=lambda: TODAY)

    first = scorer.step()
    assert (first["due"], first["scored"], first["remaining"], first["leaderboard"]) == (3, 2, 1, False)
    second = scorer.step()
    assert (second["due"], second["scored"], second["remaining"], second["leaderboard"]) == (1, 1, 0, True)


def test_an_empty_queue_recomputes_the_leaderboard_once_per_start(conn, monkeypatch):
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", Prices({}))
    scorer = form4_scorer.IncrementalScorer(conn, 20, today=lambda: TODAY)
    assert scorer.step()["leaderboard"] is True
    assert scorer.step()["leaderboard"] is False


def test_without_spy_nothing_is_marked_attempted(conn, monkeypatch):
    prices = Prices({"ABC": daily("2026-01-01", 280)})
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", prices)
    add_trade(conn, "ABC", "2026-03-02")
    scorer = form4_scorer.IncrementalScorer(conn, 20, today=lambda: TODAY)

    result = scorer.step()
    assert (result["spy"], result["scored"], result["remaining"]) == (False, 0, 1)
    assert conn.execute("SELECT COUNT(*) FROM score_attempts").fetchone()[0] == 0
    # SPY is retried hourly, not every cycle.
    scorer.step()
    assert prices.fetched == ["SPY"]


def test_scoring_fills_the_leaderboard_and_the_cutoff(conn, monkeypatch):
    # Five insiders with five scored buys each: the leaderboard's entry bar.
    series = {"SPY": daily("2025-12-01", 320, first=500.0, step=0.0)}
    for i in range(5):
        t = f"T{i}"
        series[t] = daily("2025-12-01", 320, step=0.05 * (i + 1))
        for k in range(5):
            add_trade(conn, t, f"2026-0{k + 1}-05", insider=f"000{i}")
    monkeypatch.setattr(form4_common, "fetch_yahoo_closes", Prices(series))
    assert form4.get_alpha_cutoff(conn) is None

    scorer = form4_scorer.IncrementalScorer(conn, 60, today=lambda: TODAY)
    assert scorer.step()["leaderboard"] is True

    state = form4.leaderboard_state(conn)
    assert (state["eligible"], state["scored"]) == (5, 5)
    assert form4.get_alpha_cutoff(conn) is not None


# --- the service's wrapper ---------------------------------------------------

def test_a_failing_step_is_counted_and_never_raises(monkeypatch):
    svc = Svc()
    monkeypatch.setattr(form4, "SVC", svc)

    class Broken:
        def step(self):
            raise RuntimeError("disk I/O error")

    assert form4.run_scoring_step(Broken()) is False
    assert svc.metrics.get(form4.SCORER_ERRORS) == 1


def test_a_step_reaches_the_snapshot(monkeypatch):
    svc = Svc()
    monkeypatch.setattr(form4, "SVC", svc)

    class Done:
        def step(self):
            return {"due": 4, "scored": 3, "no_prices": 1, "remaining": 0,
                    "leaderboard": True, "spy": True}

    assert form4.run_scoring_step(Done()) is True
    snap = svc.metrics.snapshot()
    assert snap["alert_scorer_tickers_scored_total"] == 3
    assert snap["alert_scorer_tickers_no_prices_total"] == 1
    assert snap["alert_scorer_tickers_due"] == 0


# --- placeholder tickers -----------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("exmp", "EXMP"), (" lly ", "LLY"), ("NONE", None), ("none", None),
    ("N/A", None), ("", None), (None, None),
])
def test_placeholder_trading_symbols_mean_no_ticker(raw, want):
    assert form4_backfill.normalize_ticker(raw) == want
