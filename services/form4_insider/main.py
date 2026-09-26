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
On the live host neither has ever run, so the leaderboard is empty and the
second branch cannot fire: everything under $1M is refused. See backlog #39,
and the `funnel` line this module emits, which says so every cycle.
"""

from __future__ import annotations

import html
import sqlite3
import time
from datetime import datetime, timezone

import feedparser
import form4_common

# ---------------------------------------------------------------------------
# Platform runtime
# ---------------------------------------------------------------------------
from alertlib import Alert, CycleFunnel, Service, get_logger
from form4_backfill import _ACCESSION_RE, fetch_primary_xml, parse_form4_xml
from form4_common import edgar_get, init_db

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


# Every outcome `should_alert` can reach, named rather than described. The
# funnel counts these, so the per-cycle line is a histogram of the filter
# itself and a silent feed says which branch silenced it. Returning a name
# alongside the prose reason keeps the two from drifting: a new branch that
# forgets its name fails the funnel's undeclared-stage check immediately.
DECISIONS = (
    "code_not_actionable",
    "planned_sale",
    "below_floor",
    "large_trade",
    "no_insider_history",
    "thin_history",
    "no_leaderboard",
    "below_cutoff",
    "top_tier",
)

# The two that alert. Everything else is a refusal with a reason.
ALERTING_DECISIONS = frozenset({"large_trade", "top_tier"})

# The candidate pipeline, in order:
#
#   entries -> new -> fetched -> parsed -> claimed -> transactions
#           -> <one of DECISIONS> -> sent
#
# Named here so the funnel line and the /metrics counters cannot drift apart.
# Each stage before the decisions is a different reason for silence with a
# different fix: an empty feed, a feed of filings already handled, a fetch
# that fails, XML that will not parse, and a dedup write that refuses the
# send (the 2026-09-21 incident's safe failure). Without them, all five
# produce the same output — a cycle that sends nothing in a fifth of a
# second, which is what the host has logged for weeks.
FUNNEL_STAGES = (
    "entries",
    "new",
    "fetched",
    "parsed",
    "claimed",
    "transactions",
    *DECISIONS,
    "sent",
)


# ---------------------------------------------------------------------------
# Leaderboard cutoff
# ---------------------------------------------------------------------------
def get_alpha_cutoff(conn: sqlite3.Connection, min_n_trades: int = 5) -> float | None:
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


# The gauges below carry these counts off the host in the metrics snapshot,
# so the question can be answered without catching the hourly log line.
LEADERBOARD_GAUGES = {
    "insiders": ("alert_leaderboard_insiders",
                 "Rows in the insiders table."),
    "eligible": ("alert_leaderboard_eligible",
                 "Insiders with at least 5 scored trades, the leaderboard's entry bar."),
    "scored": ("alert_leaderboard_scored",
               "Insiders with a computed alpha_90."),
    "transactions": ("alert_leaderboard_transactions",
                     "Rows in the transactions table, which the scorer reads."),
}


def leaderboard_state(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts that distinguish an empty leaderboard from an unscored one.

    `get_alpha_cutoff` returns None for both, and they need different fixes:
    no rows in `insiders` means `form4_backfill.py` has never run, while rows
    without `alpha_90` means `form4_scorer.py` has not. Neither script is in
    the fleet spec, so neither runs on a schedule (backlog #39), and until one
    of them does the cutoff stays None and every trade under $1M is refused.
    """
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "insiders": count("SELECT COUNT(*) FROM insiders"),
        "eligible": count("SELECT COUNT(*) FROM insiders WHERE n_trades >= 5"),
        "scored": count("SELECT COUNT(*) FROM insiders WHERE alpha_90 IS NOT NULL"),
        "transactions": count("SELECT COUNT(*) FROM transactions"),
    }


def publish_leaderboard_state(conn: sqlite3.Connection, cutoff: float | None) -> dict[str, int]:
    """Read the counts, set the gauges, log the line. Returns the counts."""
    state = leaderboard_state(conn)
    for key, (metric, _help) in LEADERBOARD_GAUGES.items():
        SVC.metrics.set(metric, state[key])
    log.info("leaderboard state", extra={**state, "alpha_cutoff": cutoff})
    return state


def get_insider_stats(conn: sqlite3.Connection, insider_cik: str) -> dict | None:
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
def should_alert(
    tx: dict, insider_stats: dict | None, alpha_cutoff: float | None
) -> tuple[str, str]:
    """
    Returns (decision, reason), where decision is one of `DECISIONS` and the
    trade is alerted iff it is in `ALERTING_DECISIONS`.

    Filter rules:
      1. Code must be P, or S with is_10b5_1=0.
      2. USD >= $100k.
      3. Either USD >= $1M (always alert big trades) OR
         insider is scored AND their alpha_90 >= cutoff.
    """
    code = tx["tx_code"]
    if code not in ("P", "S"):
        return "code_not_actionable", f"code {code} not actionable"
    if code == "S" and tx["is_10b5_1"] == 1:
        return "planned_sale", "10b5-1 planned sale"
    if tx["usd_value"] < MIN_USD_ALERT:
        return "below_floor", f"size ${tx['usd_value']:,.0f} below threshold"

    if tx["usd_value"] >= LARGE_TRADE_USD:
        return "large_trade", f"large trade ${tx['usd_value']:,.0f}"

    if insider_stats is None or insider_stats.get("alpha_90") is None:
        return "no_insider_history", "no insider history & below large-trade threshold"
    if insider_stats["n_trades"] < 5:
        return "thin_history", f"insufficient history ({insider_stats['n_trades']} trades)"
    if alpha_cutoff is None:
        return "no_leaderboard", "no leaderboard cutoff available"
    if insider_stats["alpha_90"] < alpha_cutoff:
        return ("below_cutoff",
                f"alpha_90 {insider_stats['alpha_90']:.2%} below cutoff {alpha_cutoff:.2%}")

    return "top_tier", f"top-tier insider (alpha_90 {insider_stats['alpha_90']:.2%})"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text: str) -> bool:
    """Delivery via the platform client (rate limit, retries, metrics)."""
    return SVC.telegram.send(text)


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.1%}"


def format_alert(parsed: dict, tx: dict, accession: str,
                 insider_stats: dict | None, reason: str) -> str:
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


def build_alert(parsed: dict, tx: dict, accession: str, reason: str, body: str) -> Alert:
    """The archive record for one transaction.

    The dedup key is accession plus the transaction's position within the
    filing, because one Form 4 can carry several qualifying trades and the
    accession alone would collapse them into one row. `alerted` is keyed on
    the accession; this is keyed on what was actually sent.
    """
    return Alert(
        source="SEC EDGAR Form 4",
        dedup_key=f"{accession}#{tx['tx_code']}:{tx['trade_date']}:{tx['shares']:.0f}",
        title=f"{parsed.get('ticker') or '?'} {tx['tx_code']} ${tx['usd_value']:,.0f} "
              f"by {parsed['insider_name'][:40]}",
        body=body,
        ticker=(parsed.get("ticker") or "").upper(),
        reason=reason,
        payload={
            "accession": accession,
            "issuer_cik": parsed.get("issuer_cik"),
            "insider_cik": parsed.get("insider_cik"),
            "insider_name": parsed.get("insider_name"),
            "relationship": parsed.get("relationship"),
            "tx_code": tx["tx_code"],
            "trade_date": tx["trade_date"],
            "shares": tx["shares"],
            "price": tx["price"],
            "usd_value": tx["usd_value"],
        },
    )


# ---------------------------------------------------------------------------
# Fetch + process loop
# ---------------------------------------------------------------------------
def fetch_current_form4_entries() -> list[tuple[str, str]]:
    """
    Returns list of (accession, cik) from EDGAR's current Form 4 atom feed.
    Filter duplicates via DB state upstream.
    """
    resp = edgar_get(CURRENT_FORM4_URL, timeout=30,
                     extra_headers={"Accept": "application/atom+xml"})
    feed = feedparser.parse(resp.content)
    out: list[tuple[str, str]] = []
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


def mark_alerted(conn: sqlite3.Connection, accession: str) -> bool:
    """Record the filing as handled. Returns False if that did not persist.

    Callers must check the result before sending anything. An alert sent for a
    filing that was not recorded is sent again on the next poll, and if the
    write keeps failing the feed emits the same alerts indefinitely — see
    docs/incidents/2026-09-21-form4-duplicate-alerts.md.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO alerted (accession, alerted_at) VALUES (?, ?)",
            (accession, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        # Roll back so the connection is usable for the next filing; a failure
        # here is about the database, not about this accession.
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def process_filing(
    conn: sqlite3.Connection,
    accession: str,
    cik: str,
    alpha_cutoff: float | None,
    funnel: CycleFunnel,
) -> int:
    """Fetch the XML, parse transactions, alert on any that qualify. Returns number of alerts sent."""
    if is_already_alerted(conn, accession):
        return 0
    funnel.count("new")

    result = fetch_primary_xml(cik, accession)
    if result is None:
        # Mark to stop re-fetching; we will never recover this one. Nothing is
        # sent on this path, so a failed write costs only a repeated fetch.
        mark_alerted(conn, accession)
        return 0
    _, xml_bytes = result
    funnel.count("fetched")

    parsed = parse_form4_xml(xml_bytes)
    if parsed is None:
        mark_alerted(conn, accession)
        return 0
    funnel.count("parsed")

    insider_stats = get_insider_stats(conn, parsed["insider_cik"])

    # Claim the filing before sending, not after. Sending first and recording
    # afterwards means any failure in between re-sends every alert for this
    # filing on the next poll, for as long as the failure lasts; that is what
    # produced the 2026-09-21 duplicate-alert incident. Refusing to send when
    # the claim fails trades one missed filing for a bounded failure, which is
    # the right direction for a feed a human reads.
    if not mark_alerted(conn, accession):
        log.error(
            "could not record filing before sending; sending nothing",
            extra={"accession": accession},
        )
        SVC.metrics.inc("alert_sends_refused_total")
        return 0
    funnel.count("claimed")

    sent = 0
    for tx in parsed["transactions"]:
        funnel.count("transactions")
        decision, reason = should_alert(tx, insider_stats, alpha_cutoff)
        funnel.count(decision)
        if decision not in ALERTING_DECISIONS:
            log.debug("Skip %s tx %s: %s", accession, tx["tx_code"], reason)
            continue

        msg = format_alert(parsed, tx, accession, insider_stats, reason)
        if SVC.send_alert(build_alert(parsed, tx, accession, reason, msg)):
            sent += 1
            funnel.count("sent")
            log.info("Alert sent: %s %s [%s] %s $%.0f",
                     parsed.get("ticker"), tx["tx_code"],
                     parsed["insider_name"][:30], reason, tx["usd_value"])

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

    for metric, help_text in LEADERBOARD_GAUGES.values():
        SVC.metrics.declare_gauge(metric, help_text)

    with SVC:
        conn = init_db()
        funnel = CycleFunnel(SVC.metrics, FUNNEL_STAGES, log=log)

        alpha_cutoff = get_alpha_cutoff(conn)
        state = publish_leaderboard_state(conn, alpha_cutoff)

        # The last bullet is conditional because without a cutoff it describes
        # a branch that cannot be reached: everything under LARGE_TRADE_USD is
        # refused, and saying otherwise misrepresents the feed to its reader.
        leaderboard_rule = (
            "• Otherwise: insider must be top 25% by 90d alpha"
            if alpha_cutoff is not None
            else f"• No leaderboard yet, so nothing under ${LARGE_TRADE_USD:,} can alert"
        )
        send_telegram(
            "✅ <b>Form 4 Insider Bot started</b>\n\n"
            f"Leaderboard: {state['scored']:,} scored of {state['insiders']:,} insiders\n"
            f"Cutoff (top 25%% by 90d alpha): "
            f"{f'{alpha_cutoff:+.1%}' if alpha_cutoff else '<i>not yet scored</i>'}\n\n"
            "Filters:\n"
            "• Open-market buys (P) or discretionary sells (non-10b5-1 S)\n"
            f"• Minimum size: ${MIN_USD_ALERT:,}\n"
            f"• Auto-alert if size ≥ ${LARGE_TRADE_USD:,} (bypass leaderboard)\n"
            + leaderboard_rule
        )

        last_cutoff_refresh = time.time()

        while SVC.running():
            with SVC.poll_cycle(), funnel.cycle():
                # Refresh the cutoff hourly — the nightly scorer may have run.
                if time.time() - last_cutoff_refresh >= 3600:
                    alpha_cutoff = get_alpha_cutoff(conn)
                    last_cutoff_refresh = time.time()
                    publish_leaderboard_state(conn, alpha_cutoff)

                entries = fetch_current_form4_entries()
                SVC.metrics.inc("alert_items_seen_total", len(entries))
                funnel.count("entries", len(entries))

                sent_this_cycle = 0
                for accession, cik in entries:
                    try:
                        sent_this_cycle += process_filing(
                            conn, accession, cik, alpha_cutoff, funnel
                        )
                    except Exception:
                        # One malformed filing must not cost us the whole cycle;
                        # EDGAR's current feed rolls over fast.
                        log.exception("filing failed", extra={"accession": accession})

                # After the loop: the cohort is what the feed returned, and
                # `new_in_window` answers whether the feed is moving at all.
                # Overnight it does not — Form 4s are filed after the close —
                # and every run so far has read the same pre-market hour.
                funnel.observe_cohort(accession for accession, _cik in entries)

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
