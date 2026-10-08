"""
form4_scorer.py
===============
Computes 30/90/180-day forward returns for open-market buys (code P) and
aggregates them into per-insider alpha scores, the leaderboard that lets
form4-insider alert on trades under $1M.

Forward return = (price at T+N days) / (price at T) - 1
SPY return     = same, but for SPY
Alpha          = dollar-value-weighted average of (forward_return - spy_return)

Only buys are scored because only buys enter the leaderboard.

form4-insider runs this incrementally: `IncrementalScorer.step()` spends a
few seconds of each poll cycle on the tickers that are due, and recomputes
the leaderboard when none are left. The command line does the same work in
one pass, for a backfill.

Usage:
    python form4_scorer.py                 # score everything due
    python form4_scorer.py --rescore       # recompute every buy
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from form4_common import (
    bootstrap_job,
    compute_forward_return,
    fetch_price_history,
    init_db,
    make_logger,
)

log = make_logger("form4_scorer", "form4_scorer.log")

SPY_TICKER = "SPY"

# A horizon's return is computable once its end date has a close; these days
# cover a weekend plus a holiday. Before then the trade is not due, so a young
# trade is not refetched every pass only to come back empty.
SETTLE_DAYS = 5

# A ticker that was tried and still has due trades (no prices, or history
# that starts after the trade) waits this long before the next attempt.
RETRY_DAYS = 7

# Prices are fetched to this far past the newest due trade, or to today.
PRICE_WINDOW_DAYS = 200

# A trade is due when a horizon the leaderboard uses has become computable
# and is still empty. 30 days is filled alongside 90 and never on its own.
_DUE = """t.tx_code = 'P' AND (:rescore
          OR (t.fwd_ret_90  IS NULL AND t.trade_date <= :due90)
          OR (t.fwd_ret_180 IS NULL AND t.trade_date <= :due180))"""


def _due_params(today: date, rescore: bool) -> dict:
    return {
        "rescore": 1 if rescore else 0,
        "due90": (today - timedelta(days=90 + SETTLE_DAYS)).isoformat(),
        "due180": (today - timedelta(days=180 + SETTLE_DAYS)).isoformat(),
        "retry_before": (today - timedelta(days=RETRY_DAYS)).isoformat(),
    }


def tickers_needing_scoring(conn: sqlite3.Connection, rescore: bool,
                            today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    cur = conn.execute(
        f"""SELECT DISTINCT t.ticker FROM transactions t
              LEFT JOIN score_attempts a ON a.ticker = t.ticker
             WHERE t.ticker IS NOT NULL AND {_DUE}
               AND (:rescore OR a.attempted_on IS NULL OR a.attempted_on <= :retry_before)
             ORDER BY t.ticker""",
        _due_params(today, rescore),
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def score_ticker(conn: sqlite3.Connection, ticker: str, spy_prices: dict[str, float],
                 rescore: bool, today: date | None = None) -> str:
    """Fetch prices for this ticker and fill the forward returns of its due
    buys. Returns `scored`, `no_prices`, or `nothing_due`."""
    today = today or datetime.now(timezone.utc).date()
    rows = conn.execute(
        f"""SELECT t.id, t.trade_date FROM transactions t
             WHERE t.ticker = :ticker AND {_DUE}
             ORDER BY t.trade_date""",
        {"ticker": ticker, **_due_params(today, rescore)},
    ).fetchall()
    if not rows:
        return "nothing_due"

    dates = [r[1] for r in rows]
    start_dt = datetime.fromisoformat(min(dates))
    end_dt = min(datetime.fromisoformat(max(dates)) + timedelta(days=PRICE_WINDOW_DAYS),
                 datetime.combine(today, datetime.min.time()))
    prices = fetch_price_history(ticker, start_dt, end_dt, cache_conn=conn)

    outcome = "scored" if prices else "no_prices"
    for tx_id, trade_date in rows if prices else ():
        fwd_30 = compute_forward_return(prices, trade_date, 30)
        fwd_90 = compute_forward_return(prices, trade_date, 90)
        fwd_180 = compute_forward_return(prices, trade_date, 180)
        spy_30 = compute_forward_return(spy_prices, trade_date, 30)
        spy_90 = compute_forward_return(spy_prices, trade_date, 90)
        spy_180 = compute_forward_return(spy_prices, trade_date, 180)
        conn.execute(
            """UPDATE transactions
               SET fwd_ret_30 = ?, fwd_ret_90 = ?, fwd_ret_180 = ?,
                   spy_ret_30 = ?, spy_ret_90 = ?, spy_ret_180 = ?
               WHERE id = ?""",
            (fwd_30, fwd_90, fwd_180, spy_30, spy_90, spy_180, tx_id),
        )
    conn.execute(
        "INSERT OR REPLACE INTO score_attempts (ticker, attempted_on, outcome) VALUES (?, ?, ?)",
        (ticker, today.isoformat(), outcome),
    )
    conn.commit()
    if not prices:
        log.debug("No prices for %s, skipping %d trades", ticker, len(rows))
    return outcome


def load_spy_for(conn: sqlite3.Connection, today: date) -> dict[str, float]:
    """SPY closes from the oldest buy to today; empty if there are no buys."""
    row = conn.execute(
        "SELECT MIN(trade_date) FROM transactions WHERE ticker IS NOT NULL AND tx_code = 'P'"
    ).fetchone()
    if not row or not row[0]:
        return {}
    return fetch_price_history(SPY_TICKER, datetime.fromisoformat(row[0]),
                               datetime.combine(today, datetime.min.time()), cache_conn=conn)


class IncrementalScorer:
    """Scores due tickers inside form4-insider's poll loop, within a time budget.

    Each `step()` scores tickers until `budget_sec` has passed, then returns,
    so a large first pass spreads over many cycles instead of stalling the
    poll. When a step finds nothing left to score and something changed since
    the last leaderboard, it recomputes the leaderboard. A new process counts
    as changed, so the leaderboard is recomputed once per start.
    """

    SPY_RETRY_SEC = 3600

    def __init__(self, conn: sqlite3.Connection, budget_sec: float,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], date] = lambda: datetime.now(timezone.utc).date()):
        self.conn = conn
        self.budget_sec = budget_sec
        self._clock = clock
        self._today = today
        self._spy: dict[str, float] = {}
        self._spy_day: date | None = None
        self._spy_tried_at: float | None = None
        self._changed = True

    def _refresh_spy(self, today: date) -> bool:
        if self._spy_day == today:
            return True
        now = self._clock()
        if self._spy_tried_at is not None and now - self._spy_tried_at < self.SPY_RETRY_SEC:
            return bool(self._spy)
        self._spy_tried_at = now
        spy = load_spy_for(self.conn, today)
        if spy:
            self._spy, self._spy_day = spy, today
        return bool(self._spy)

    def step(self) -> dict:
        """Score for up to `budget_sec`. Returns the counts for this step."""
        today = self._today()
        result = {"due": 0, "scored": 0, "no_prices": 0, "remaining": 0,
                  "leaderboard": False, "spy": True}
        deadline = self._clock() + self.budget_sec
        tickers = tickers_needing_scoring(self.conn, False, today)
        result["due"] = len(tickers)
        if tickers and not self._refresh_spy(today):
            # Without SPY no alpha can be computed; scoring now would mark
            # every ticker as attempted and delay it a week for nothing.
            result["spy"] = False
            result["remaining"] = len(tickers)
            return result

        done = 0
        for ticker in tickers:
            if self._clock() >= deadline:
                break
            outcome = score_ticker(self.conn, ticker, self._spy, False, today)
            if outcome in ("scored", "no_prices"):
                result[outcome] += 1
                self._changed = True
            done += 1
        result["remaining"] = len(tickers) - done

        if result["remaining"] == 0 and self._changed:
            compute_leaderboard(self.conn)
            self._changed = False
            result["leaderboard"] = True
        return result


def compute_leaderboard(conn: sqlite3.Connection):
    """
    For each insider, compute dollar-weighted alpha (= forward - SPY) over their
    buy transactions (code P only). Sells have different signal dynamics and
    aren't suited to a simple 'does buying here beat SPY' metric.
    """
    log.info("Computing leaderboard…")
    conn.execute("""
        WITH buy_tx AS (
            SELECT
                insider_cik,
                usd_value,
                fwd_ret_30 - spy_ret_30  AS a30,
                fwd_ret_90 - spy_ret_90  AS a90,
                fwd_ret_180 - spy_ret_180 AS a180,
                trade_date
            FROM transactions
            WHERE tx_code = 'P'
              AND fwd_ret_90 IS NOT NULL
              AND spy_ret_90 IS NOT NULL
        ),
        agg AS (
            SELECT
                insider_cik,
                COUNT(*)                                              AS n,
                SUM(usd_value * a30)   / NULLIF(SUM(usd_value), 0)    AS a30w,
                SUM(usd_value * a90)   / NULLIF(SUM(usd_value), 0)    AS a90w,
                SUM(usd_value * a180)  / NULLIF(SUM(usd_value), 0)    AS a180w,
                MAX(trade_date)                                       AS last_trade
            FROM buy_tx
            GROUP BY insider_cik
        )
        UPDATE insiders AS i
           SET n_trades        = COALESCE((SELECT n       FROM agg WHERE agg.insider_cik = i.insider_cik), 0),
               alpha_30        =          (SELECT a30w    FROM agg WHERE agg.insider_cik = i.insider_cik),
               alpha_90        =          (SELECT a90w    FROM agg WHERE agg.insider_cik = i.insider_cik),
               alpha_180       =          (SELECT a180w   FROM agg WHERE agg.insider_cik = i.insider_cik),
               last_trade_date =          (SELECT last_trade FROM agg WHERE agg.insider_cik = i.insider_cik),
               last_scored_at  = ?
    """, (datetime.now(timezone.utc).isoformat(),))

    # Also compute total buy/sell USD across all codes for informational display
    conn.execute("""
        WITH sums AS (
            SELECT insider_cik,
                   SUM(CASE WHEN tx_code = 'P' THEN usd_value ELSE 0 END) AS tb,
                   SUM(CASE WHEN tx_code = 'S' THEN usd_value ELSE 0 END) AS ts
            FROM transactions
            GROUP BY insider_cik
        )
        UPDATE insiders AS i
           SET total_buy_usd  = COALESCE((SELECT tb FROM sums WHERE sums.insider_cik = i.insider_cik), 0),
               total_sell_usd = COALESCE((SELECT ts FROM sums WHERE sums.insider_cik = i.insider_cik), 0)
    """)
    conn.commit()

    # Print the top-of-book as a sanity check
    rows = conn.execute("""
        SELECT name, n_trades, alpha_30, alpha_90, alpha_180, total_buy_usd
          FROM insiders
         WHERE n_trades >= 5
         ORDER BY alpha_90 DESC
         LIMIT 15
    """).fetchall()
    log.info("Top 15 insiders by 90-day alpha (min 5 trades):")
    for name, n, a30, a90, a180, buyusd in rows:
        log.info("  %-40s n=%3d  a30=%+.1f%%  a90=%+.1f%%  a180=%+.1f%%  buys=$%.1fM",
                 name[:40], n,
                 (a30 or 0) * 100, (a90 or 0) * 100, (a180 or 0) * 100,
                 buyusd / 1e6)


def main():
    bootstrap_job("form4-scorer")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescore", action="store_true",
                    help="Recompute forward returns for every buy (default: only those due)")
    args = ap.parse_args()

    conn = init_db()
    today = datetime.now(timezone.utc).date()
    spy_prices = load_spy_for(conn, today)
    if not spy_prices:
        log.error("No buys to score, or no SPY price history. Abort.")
        return
    log.info("SPY: %d days", len(spy_prices))

    tickers = tickers_needing_scoring(conn, args.rescore, today)
    log.info("%d tickers to score", len(tickers))

    t0 = time.time()
    for i, ticker in enumerate(tickers, 1):
        try:
            score_ticker(conn, ticker, spy_prices, args.rescore, today)
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(tickers) - i) / rate if rate > 0 else 0
                log.info("Scored %d/%d tickers (%.1f/s, ETA %.1f min)",
                         i, len(tickers), rate, eta / 60)
        except Exception as e:
            log.exception("Failed ticker %s: %s", ticker, e)

    compute_leaderboard(conn)
    log.info("Scoring complete.")


if __name__ == "__main__":
    main()
