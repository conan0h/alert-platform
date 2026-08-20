"""
form4_scorer.py
===============
Reads the transactions table, computes 30/90/180-day forward returns for each
trade, and aggregates them into per-insider alpha scores.

Forward return = (price at T+N days) / (price at T) - 1
SPY return     = same, but for SPY
Alpha          = dollar-value-weighted average of (forward_return - spy_return)

Memory-bounded: processes one ticker at a time (group trades by ticker),
fetches prices once per ticker, applies to all trades.

Run after form4_backfill.py completes. Also run periodically to refresh scores.

Usage:
    python form4_scorer.py                 # compute everything missing
    python form4_scorer.py --rescore       # recompute all (don't skip cached)
"""

from __future__ import annotations

import argparse
import time
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from form4_common import (
    init_db, make_logger, bootstrap_job, fetch_price_history, compute_forward_return,
)

log = make_logger("form4_scorer", "form4_scorer.log")

SPY_TICKER = "SPY"


def tickers_needing_scoring(conn: sqlite3.Connection, rescore: bool) -> List[str]:
    if rescore:
        cur = conn.execute(
            "SELECT DISTINCT ticker FROM transactions WHERE ticker IS NOT NULL ORDER BY ticker"
        )
    else:
        cur = conn.execute(
            """SELECT DISTINCT ticker FROM transactions
               WHERE ticker IS NOT NULL AND fwd_ret_90 IS NULL
               ORDER BY ticker"""
        )
    return [r[0] for r in cur.fetchall() if r[0]]


def load_spy_series(conn: sqlite3.Connection, oldest: str, newest_plus_buffer: str) -> Dict[str, float]:
    start = datetime.fromisoformat(oldest)
    end = datetime.fromisoformat(newest_plus_buffer)
    return fetch_price_history(SPY_TICKER, start, end, cache_conn=conn)


def score_ticker(conn: sqlite3.Connection, ticker: str, spy_prices: Dict[str, float], rescore: bool):
    """Fetch prices for this ticker, then update every transaction's forward returns."""
    # Trades for this ticker
    rows = conn.execute(
        """SELECT id, trade_date FROM transactions
           WHERE ticker = ? AND (? OR fwd_ret_90 IS NULL)
           ORDER BY trade_date""",
        (ticker, 1 if rescore else 0),
    ).fetchall()
    if not rows:
        return 0

    # Find the price window we need
    dates = [r[1] for r in rows]
    oldest = min(dates)
    # Need +180d buffer for the longest forward horizon, +10d for weekends/holidays safety
    newest = (datetime.fromisoformat(max(dates)) + timedelta(days=200)).strftime("%Y-%m-%d")

    start_dt = datetime.fromisoformat(oldest)
    end_dt = datetime.fromisoformat(newest)
    prices = fetch_price_history(ticker, start_dt, end_dt, cache_conn=conn)
    if not prices:
        log.debug("No prices for %s, skipping %d trades", ticker, len(rows))
        return 0

    updated = 0
    for tx_id, trade_date in rows:
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
        updated += 1
    conn.commit()
    return updated


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
                    help="Recompute forward returns for all transactions (default: only missing)")
    args = ap.parse_args()

    conn = init_db()

    # Determine the full price window we need for SPY
    row = conn.execute("""
        SELECT MIN(trade_date), MAX(trade_date)
          FROM transactions
         WHERE ticker IS NOT NULL
    """).fetchone()
    if not row or not row[0]:
        log.warning("No transactions in DB. Run form4_backfill.py first.")
        return

    oldest, newest = row
    newest_plus = (datetime.fromisoformat(newest) + timedelta(days=200)).strftime("%Y-%m-%d")
    log.info("Loading SPY prices from %s to %s…", oldest, newest_plus)
    spy_prices = load_spy_series(conn, oldest, newest_plus)
    if not spy_prices:
        log.error("Could not fetch SPY price history. Abort.")
        return
    log.info("SPY: %d days cached", len(spy_prices))

    tickers = tickers_needing_scoring(conn, args.rescore)
    log.info("%d tickers to score", len(tickers))

    t0 = time.time()
    for i, ticker in enumerate(tickers, 1):
        try:
            n = score_ticker(conn, ticker, spy_prices, args.rescore)
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
