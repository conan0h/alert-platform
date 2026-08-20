"""
form4_bot.py
============
Live Form 4 alerter. Polls EDGAR's "current filings" atom feed every couple
of minutes, parses new filings, and sends a Telegram alert for each that
passes the filter rules:

  - Transaction code is P (buy) OR non-10b5-1 S (discretionary sell)
  - USD size >= $100,000
  - AND EITHER:
      - Insider's 90-day alpha is in the top 25% of scored insiders, OR
      - USD size >= $1,000,000 (let big trades through regardless of history)

Alert message includes the insider's 30/90/180-day alpha numbers and trade
count, so you can see at a glance whether this is a known performer or a
rookie swinging big.

Run after form4_backfill.py + form4_scorer.py have populated the leaderboard.
"""

from __future__ import annotations

import os
import html
import time
import sqlite3
import feedparser
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

import requests

import form4_common
from form4_common import init_db, edgar_get, fetch_price_history
from form4_backfill import parse_form4_xml, fetch_primary_xml, _ACCESSION_RE

# ---------------------------------------------------------------------------
# Platform runtime
# ---------------------------------------------------------------------------
from alertlib import Service, get_logger

SVC: Service = None          # bound in main()
log = get_logger("form4-insider")

# Bound from the spec in main(). The values below are the pre-migration
# defaults and apply only if a key is absent from spec.polling.
POLL_INTERVAL_SECONDS = 120
MIN_USD_ALERT = 100_000               # filter floor
LARGE_TRADE_USD = 1_000_000           # bypass leaderboard filter if >= this
ALPHA_PERCENTILE_CUTOFF = 0.75        # top 25% of scored insiders

CURRENT_FORM4_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&company=&dateb=&owner=include&count=100&output=atom"
)


# ---------------------------------------------------------------------------
# Leaderboard cutoff
# ---------------------------------------------------------------------------
def get_alpha_cutoff(conn: sqlite3.Connection, min_n_trades: int = 5) -> Optional[float]:
    """
    Return the 90-day alpha threshold for the 75th percentile of insiders
    with at least `min_n_trades` scored trades. Used to decide if an insider
    is "top 25%".
    """
    rows = conn.execute(
        "SELECT alpha_90 FROM insiders WHERE n_trades >= ? AND alpha_90 IS NOT NULL ORDER BY alpha_90",
        (min_n_trades,),
    ).fetchall()
    if not rows:
        return None
    values = [r[0] for r in rows]
    idx = int(len(values) * ALPHA_PERCENTILE_CUTOFF)
    idx = min(idx, len(values) - 1)
    return values[idx]


def get_insider_stats(conn: sqlite3.Connection, insider_cik: str) -> Optional[Dict]:
    row = conn.execute(
        """SELECT name, n_trades, alpha_30, alpha_90, alpha_180, total_buy_usd, total_sell_usd
             FROM insiders WHERE insider_cik = ?""",
        (insider_cik,),
    ).fetchone()
    if not row:
        return None
    return {
        "name": row[0],
        "n_trades": row[1] or 0,
        "alpha_30": row[2],
        "alpha_90": row[3],
        "alpha_180": row[4],
        "total_buy_usd": row[5] or 0,
        "total_sell_usd": row[6] or 0,
    }


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------
def should_alert(tx: Dict, insider_stats: Optional[Dict], alpha_cutoff: Optional[float]) -> Tuple[bool, str]:
    """
    Returns (should_alert, reason).

    Filter rules:
      1. Code must be P, or S with is_10b5_1=0.
      2. USD >= $100k.
      3. Either USD >= $1M (always alert big trades) OR
         insider is scored AND their alpha_90 >= cutoff.
    """
    code = tx["tx_code"]
    if code not in ("P", "S"):
        return False, f"code {code} not actionable"
    if code == "S" and tx["is_10b5_1"] == 1:
        return False, "10b5-1 planned sale"
    if tx["usd_value"] < MIN_USD_ALERT:
        return False, f"size ${tx['usd_value']:,.0f} below threshold"

    if tx["usd_value"] >= LARGE_TRADE_USD:
        return True, f"large trade ${tx['usd_value']:,.0f}"

    if insider_stats is None or insider_stats.get("alpha_90") is None:
        return False, "no insider history & below large-trade threshold"
    if insider_stats["n_trades"] < 5:
        return False, f"insufficient history ({insider_stats['n_trades']} trades)"
    if alpha_cutoff is None:
        return False, "no leaderboard cutoff available"
    if insider_stats["alpha_90"] < alpha_cutoff:
        return False, f"alpha_90 {insider_stats['alpha_90']:.2%} below cutoff {alpha_cutoff:.2%}"

    return True, f"top-tier insider (alpha_90 {insider_stats['alpha_90']:.2%})"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text: str) -> bool:
    """Delivery via the platform client (rate limit, retries, metrics)."""
    return SVC.telegram.send(text)


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:+.1%}"


def format_alert(parsed: Dict, tx: Dict, accession: str,
                 insider_stats: Optional[Dict], reason: str) -> str:
    is_buy = tx["tx_code"] == "P"
    banner = "🟢 <b>INSIDER BUY</b>" if is_buy else "🔴 <b>INSIDER SELL</b>"

    ticker = parsed.get("ticker") or "—"
    insider_name = parsed["insider_name"]
    relationship = parsed.get("relationship") or "Insider"

    size_line = f"💰 <b>Size:</b> ${tx['usd_value']:,.0f}  ({tx['shares']:,.0f} @ ${tx['price']:,.2f})"

    if insider_stats and insider_stats.get("n_trades", 0) >= 5:
        track_line = (
            f"📊 <b>Track record:</b> {insider_stats['n_trades']} prior buys\n"
            f"     30d alpha: {_pct(insider_stats['alpha_30'])}   "
            f"90d: {_pct(insider_stats['alpha_90'])}   "
            f"180d: {_pct(insider_stats['alpha_180'])}"
        )
    else:
        track_line = "📊 <b>Track record:</b> <i>no qualifying history — flagged for trade size</i>"

    edgar_link = f"https://www.sec.gov/Archives/edgar/data/{int(parsed['issuer_cik'])}/{accession.replace('-', '')}/"

    return (
        f"{banner}\n"
        f"🎯 <b>{html.escape(ticker)}</b>   "
        f"📅 {tx['trade_date']}\n\n"
        f"👤 <b>{html.escape(insider_name)}</b>\n"
        f"     <i>{html.escape(relationship)}</i>\n\n"
        f"{size_line}\n\n"
        f"{track_line}\n\n"
        f"🏷️ <i>Reason: {html.escape(reason)}</i>\n"
        f"🔗 {edgar_link}"
    )


# ---------------------------------------------------------------------------
# Fetch + process loop
# ---------------------------------------------------------------------------
def fetch_current_form4_entries() -> List[Tuple[str, str]]:
    """
    Returns list of (accession, cik) from EDGAR's current Form 4 atom feed.
    Filter duplicates via DB state upstream.
    """
    resp = edgar_get(CURRENT_FORM4_URL, timeout=30,
                     extra_headers={"Accept": "application/atom+xml"})
    feed = feedparser.parse(resp.content)
    out: List[Tuple[str, str]] = []
    for entry in getattr(feed, "entries", []):
        link = (getattr(entry, "link", "") or "").strip()
        # Example: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001234567&...
        # Or direct: https://www.sec.gov/Archives/edgar/data/1234567/000120919124012345/0001209191-24-012345-index.htm
        m = _ACCESSION_RE.search(link)
        if not m:
            continue
        accession = m.group(1)
        # The CIK is in the path after /data/
        cik = None
        parts = link.split("/data/")
        if len(parts) > 1:
            cik = parts[1].split("/")[0]
        if not cik:
            continue
        out.append((accession, cik))
    return out


def is_already_alerted(conn: sqlite3.Connection, accession: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM alerted WHERE accession = ?", (accession,)
    ).fetchone() is not None


def mark_alerted(conn: sqlite3.Connection, accession: str):
    conn.execute(
        "INSERT OR IGNORE INTO alerted (accession, alerted_at) VALUES (?, ?)",
        (accession, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def process_filing(conn: sqlite3.Connection, accession: str, cik: str, alpha_cutoff: Optional[float]) -> int:
    """Fetch the XML, parse transactions, alert on any that qualify. Returns number of alerts sent."""
    if is_already_alerted(conn, accession):
        return 0

    result = fetch_primary_xml(cik, accession)
    if result is None:
        # Mark alerted to stop re-fetching; we'll never recover this one
        mark_alerted(conn, accession)
        return 0
    _, xml_bytes = result

    parsed = parse_form4_xml(xml_bytes)
    if parsed is None:
        mark_alerted(conn, accession)
        return 0

    insider_stats = get_insider_stats(conn, parsed["insider_cik"])

    sent = 0
    alerted_this_filing = False
    for tx in parsed["transactions"]:
        ok, reason = should_alert(tx, insider_stats, alpha_cutoff)
        if not ok:
            log.debug("Skip %s tx %s: %s", accession, tx["tx_code"], reason)
            continue

        msg = format_alert(parsed, tx, accession, insider_stats, reason)
        if send_telegram(msg):
            sent += 1
            alerted_this_filing = True
            log.info("Alert sent: %s %s [%s] %s $%.0f",
                     parsed.get("ticker"), tx["tx_code"],
                     parsed["insider_name"][:30], reason, tx["usd_value"])

    # Mark the filing as alerted regardless of whether any tx fired — we've
    # evaluated it and don't want to re-evaluate it on next poll.
    mark_alerted(conn, accession)
    return sent


def main():
    global SVC, POLL_INTERVAL_SECONDS, MIN_USD_ALERT, LARGE_TRADE_USD, ALPHA_PERCENTILE_CUTOFF

    SVC = Service.from_env()
    POLL_INTERVAL_SECONDS = SVC.cfg.poll_interval_sec

    # Alert thresholds are policy, not code. They live in spec.polling so a
    # change is a reviewable one-line diff against desired state rather than
    # an edit to a running service.
    MIN_USD_ALERT = int(SVC.cfg.polling("min_transaction_value_usd", MIN_USD_ALERT))
    LARGE_TRADE_USD = int(SVC.cfg.polling("large_transaction_value_usd", LARGE_TRADE_USD))
    ALPHA_PERCENTILE_CUTOFF = float(
        SVC.cfg.polling("alpha_percentile_cutoff", ALPHA_PERCENTILE_CUTOFF)
    )

    # The scorer and backfill share this database; configure() binds all three
    # entrypoints to the same platform-owned path.
    form4_common.configure(SVC.cfg.state_dir, SVC.cfg.secret("edgar_user_agent"))

    with SVC:
        conn = init_db()

        alpha_cutoff = get_alpha_cutoff(conn)
        scored_n = conn.execute(
            "SELECT COUNT(*) FROM insiders WHERE n_trades >= 5"
        ).fetchone()[0]
        log.info("leaderboard loaded", extra={
            "scored_insiders": scored_n,
            "alpha_cutoff": alpha_cutoff,
        })

        send_telegram(
            "✅ <b>Form 4 Insider Bot started</b>\n\n"
            f"Leaderboard: {scored_n:,} scored insiders\n"
            f"Cutoff (top 25%% by 90d alpha): "
            f"{f'{alpha_cutoff:+.1%}' if alpha_cutoff else '<i>not yet scored</i>'}\n\n"
            "Filters:\n"
            "• Open-market buys (P) or discretionary sells (non-10b5-1 S)\n"
            f"• Minimum size: ${MIN_USD_ALERT:,}\n"
            f"• Auto-alert if size ≥ ${LARGE_TRADE_USD:,} (bypass leaderboard)\n"
            f"• Otherwise: insider must be top 25% by 90d alpha"
        )

        last_cutoff_refresh = time.time()

        while SVC.running():
            with SVC.poll_cycle():
                # Refresh the cutoff hourly — the nightly scorer may have run.
                if time.time() - last_cutoff_refresh >= 3600:
                    alpha_cutoff = get_alpha_cutoff(conn)
                    last_cutoff_refresh = time.time()
                    log.info("alpha cutoff refreshed", extra={"cutoff": alpha_cutoff})

                entries = fetch_current_form4_entries()
                SVC.metrics.inc("alert_items_seen_total", len(entries))

                sent_this_cycle = 0
                for accession, cik in entries:
                    try:
                        sent_this_cycle += process_filing(conn, accession, cik, alpha_cutoff)
                    except Exception:
                        # One malformed filing must not cost us the whole cycle;
                        # EDGAR's current feed rolls over fast.
                        log.exception("filing failed", extra={"accession": accession})

                if sent_this_cycle:
                    log.info("alerts fired", extra={"count": sent_this_cycle})

            SVC.sleep_until_next_poll()

        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        log.exception("Fatal error")
        try:
            send_telegram(f"❌ <b>Form 4 Bot crashed</b>\n<code>{html.escape(str(e))[:300]}</code>")
        except Exception:
            pass
        raise
