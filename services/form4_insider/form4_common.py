"""
form4_common.py
===============
Shared utilities for the Form 4 bot suite:
  - SQLite schema
  - EDGAR helpers (rate-limited requests, User-Agent compliance)
  - Yahoo Finance price fetcher (free, cached to SQLite)
  - Transaction-code classification

Used by form4_backfill.py, form4_scorer.py, and form4_bot.py.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from alertlib import get_logger, state_path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Three entrypoints share this module: the live alerter (main.py), the
# nightly scorer, and the historical backfill. All three run under the same
# platform env contract, so config is resolved here once rather than being
# passed down three call paths.
#
# DB_PATH is module-level because the existing call sites read it directly;
# `configure()` rebinds it during startup, before any connection is opened.
DB_PATH = os.environ.get("ALERT_STATE_DIR", ".") + "/form4.db"

EDGAR_USER_AGENT = os.environ.get(
    "ALERT_SECRET_EDGAR_USER_AGENT", "Form4-Bot contact@example.com"
)


def bootstrap_job(name: str) -> str:
    """Startup for the one-shot jobs (scorer, backfill).

    They run under the same env contract as the live service — systemd timer
    units rendered from the same spec — so they resolve state and identity
    the same way, and log JSON to stdout like everything else.
    """
    from alertlib import configure_logging
    from alertlib.config import ConfigError

    state_dir = os.environ.get("ALERT_STATE_DIR")
    if not state_dir:
        raise ConfigError(
            "ALERT_STATE_DIR is not set. Run this job through its systemd "
            "timer, or export the dev env (see docs/runbooks/local-development.md)."
        )
    configure_logging(name, level=os.environ.get("ALERT_LOG_LEVEL", "INFO"),
                      fmt=os.environ.get("ALERT_LOG_FORMAT", "json"),
                      ref=os.environ.get("ALERT_DEPLOYED_REF", "unknown"))
    return configure(state_dir, os.environ.get(
        "ALERT_SECRET_EDGAR_USER_AGENT", EDGAR_USER_AGENT))


def configure(state_dir: str, user_agent: str) -> str:
    """Bind state path and EDGAR identity. Call once at startup."""
    global DB_PATH, EDGAR_USER_AGENT
    DB_PATH = state_path(state_dir, "form4.db")
    EDGAR_USER_AGENT = user_agent
    return DB_PATH
# EDGAR fair-use: 10 req/sec MAX. We stay well under that.
EDGAR_MIN_INTERVAL = 0.12  # ~8 req/sec

# Transaction codes worth knowing about (SEC General Instruction 8 to Form 4):
#   P — Open-market purchase at non-discounted price        (BUY signal)
#   S — Open-market sale at non-discounted price            (SELL signal)
#   A — Grant, award, or other acquisition                  (not signal)
#   M — Option exercise / conversion                         (not signal)
#   F — Payment of exercise price / tax via share surrender  (not signal)
#   D — Sale to issuer (buyback participation)              (noise-ish)
#   G — Bona fide gift                                       (not signal)
#   J — Other (rare; check footnotes)                        (not signal)
#   X — Exercise of out-of-money option                      (not signal)
SIGNAL_CODES = {"P", "S"}
BUY_CODES = {"P"}
SELL_CODES = {"S"}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,     -- EDGAR accession number, e.g. 0001209191-24-012345
    filed_at  TEXT NOT NULL,         -- ISO timestamp of EDGAR acceptance
    cik       TEXT NOT NULL,         -- issuer CIK
    ticker    TEXT,                  -- resolved ticker, may be NULL
    processed INTEGER DEFAULT 0      -- 1 once transactions extracted
);
CREATE INDEX IF NOT EXISTS idx_filings_filed   ON filings(filed_at);
CREATE INDEX IF NOT EXISTS idx_filings_proc    ON filings(processed);

CREATE TABLE IF NOT EXISTS insiders (
    insider_cik TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    -- cached alpha scores, updated by form4_scorer.py
    n_trades         INTEGER DEFAULT 0,
    alpha_30         REAL,
    alpha_90         REAL,
    alpha_180        REAL,
    total_buy_usd    REAL DEFAULT 0,
    total_sell_usd   REAL DEFAULT 0,
    last_trade_date  TEXT,
    last_scored_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_insiders_a90 ON insiders(alpha_90);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accession   TEXT NOT NULL,
    insider_cik TEXT NOT NULL,
    ticker      TEXT,
    trade_date  TEXT NOT NULL,    -- YYYY-MM-DD
    tx_code     TEXT NOT NULL,    -- P/S/A/M/F/D/G/J/X/…
    shares      REAL NOT NULL,
    price       REAL NOT NULL,    -- USD per share
    usd_value   REAL NOT NULL,    -- shares * price
    is_10b5_1   INTEGER DEFAULT 0,
    relationship TEXT,             -- 'CEO', 'CFO', 'Director', '10% Owner', …
    -- forward return columns, populated by form4_scorer.py
    fwd_ret_30  REAL,
    fwd_ret_90  REAL,
    fwd_ret_180 REAL,
    spy_ret_30  REAL,
    spy_ret_90  REAL,
    spy_ret_180 REAL,
    FOREIGN KEY(accession) REFERENCES filings(accession),
    FOREIGN KEY(insider_cik) REFERENCES insiders(insider_cik)
);
CREATE INDEX IF NOT EXISTS idx_tx_insider  ON transactions(insider_cik);
CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(trade_date);
CREATE INDEX IF NOT EXISTS idx_tx_code     ON transactions(tx_code);
CREATE INDEX IF NOT EXISTS idx_tx_ticker   ON transactions(ticker);

CREATE TABLE IF NOT EXISTS price_cache (
    ticker   TEXT NOT NULL,
    date     TEXT NOT NULL,   -- YYYY-MM-DD
    close    REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_px_ticker ON price_cache(ticker);

CREATE TABLE IF NOT EXISTS alerted (
    accession TEXT PRIMARY KEY,
    alerted_at TEXT NOT NULL
);
"""


def init_db(path: str | None = None) -> sqlite3.Connection:
    # Resolved at call time, not def time: `configure()` may have
    # rebound DB_PATH after this module was imported.
    path = path or DB_PATH
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Rate-limited EDGAR requests
# ---------------------------------------------------------------------------
_edgar_lock = threading.Lock()
_edgar_last = [0.0]


def edgar_get(url: str, timeout: int = 30, extra_headers: dict | None = None) -> requests.Response:
    """
    EDGAR-compliant GET:
      - User-Agent header with contact info (SEC requires)
      - Rate limit: never exceed ~8 req/sec (SEC limit is 10, we leave headroom)
    """
    headers = {
        "User-Agent": EDGAR_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov" if "sec.gov" in url else None,
    }
    if extra_headers:
        headers.update(extra_headers)
    headers = {k: v for k, v in headers.items() if v is not None}

    with _edgar_lock:
        dt = time.monotonic() - _edgar_last[0]
        if dt < EDGAR_MIN_INTERVAL:
            time.sleep(EDGAR_MIN_INTERVAL - dt)
        _edgar_last[0] = time.monotonic()

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Yahoo Finance — free price history (no API key needed)
# ---------------------------------------------------------------------------
def _yahoo_history_url(ticker: str, start_unix: int, end_unix: int) -> str:
    # v7 download endpoint returns CSV — simpler to parse than v8 JSON
    q = urlencode({
        "period1": start_unix,
        "period2": end_unix,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    })
    return f"https://query1.finance.yahoo.com/v7/finance/download/{ticker}?{q}"


def fetch_price_history(ticker: str, start: datetime, end: datetime,
                        cache_conn: sqlite3.Connection | None = None) -> dict[str, float]:
    """
    Returns {YYYY-MM-DD: adj_close} for the ticker between start and end (inclusive).
    Uses SQLite cache if provided, so repeated calls for the same ticker don't hammer Yahoo.
    """
    ticker = ticker.upper().strip()

    # Check cache first
    cached: dict[str, float] = {}
    if cache_conn is not None:
        rows = cache_conn.execute(
            "SELECT date, close FROM price_cache WHERE ticker = ? AND date >= ? AND date <= ?",
            (ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        ).fetchall()
        cached = {d: c for d, c in rows}

    # If we have a dense cache for the window, return it — no fetch needed
    expected_days = (end - start).days
    if len(cached) >= int(expected_days * 0.55):  # ~0.7 trading days per calendar day, give leeway
        return cached

    # Otherwise fetch from Yahoo
    url = _yahoo_history_url(ticker, int(start.timestamp()), int(end.timestamp()))
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 Form4-Bot"}, timeout=20)
        if resp.status_code != 200:
            logging.getLogger("form4").debug("Yahoo %s returned %s", ticker, resp.status_code)
            return cached
        lines = resp.text.splitlines()
        if len(lines) < 2:
            return cached
        # CSV: Date,Open,High,Low,Close,Adj Close,Volume
        result: dict[str, float] = {}
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            date_str, _, _, _, _, adj_close = parts[:6]
            try:
                result[date_str] = float(adj_close)
            except ValueError:
                continue

        # Write-through cache
        if cache_conn is not None and result:
            cache_conn.executemany(
                "INSERT OR IGNORE INTO price_cache (ticker, date, close) VALUES (?, ?, ?)",
                [(ticker, d, c) for d, c in result.items()],
            )
            cache_conn.commit()

        return result
    except Exception as e:
        logging.getLogger("form4").debug("Yahoo fetch failed %s: %s", ticker, e)
        return cached


def price_on_or_after(prices: dict[str, float], target_date: str) -> tuple[str, float] | None:
    """Find the first available trading-day price on or after target_date."""
    if not prices:
        return None
    # Sorted dates — prices is already chronologically inserted but not guaranteed.
    # Cheap to sort since per-ticker window is small.
    for d in sorted(prices.keys()):
        if d >= target_date:
            return d, prices[d]
    return None


def compute_forward_return(prices: dict[str, float], trade_date: str, days_forward: int) -> float | None:
    """
    Percent return from the first trading-day price on/after trade_date
    to the first trading-day price on/after (trade_date + days_forward).
    Returns None if either endpoint can't be located.
    """
    try:
        start_pt = price_on_or_after(prices, trade_date)
        target = (datetime.fromisoformat(trade_date) + timedelta(days=days_forward)).strftime("%Y-%m-%d")
        end_pt = price_on_or_after(prices, target)
        if start_pt is None or end_pt is None:
            return None
        _, start_px = start_pt
        _, end_px = end_pt
        if start_px <= 0:
            return None
        return (end_px - start_px) / start_px
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------
def make_logger(name: str, log_file: str = "") -> logging.Logger:
    """Kept for call-site compatibility; the platform owns log configuration.

    `log_file` is ignored — services log JSON to stdout and systemd routes it
    to journald. The parameter stays so the scorer and backfill call sites
    remain untouched.
    """
    return get_logger(name)


