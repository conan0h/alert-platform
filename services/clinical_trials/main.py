"""
ClinicalTrials.gov Alert Bot
============================
Monitors the ClinicalTrials.gov API v2 for high-signal events:

  SIGNAL 1 — Results posted before company PR
    Phase 2/3 trials flip to COMPLETED and results appear in the database.
    Companies are legally required to post within 12 months of completion.
    Many post immediately. The window between DB update and press release
    can be minutes to hours — and sometimes the market never finds out via PR.

  SIGNAL 2 — Trial terminated early
    TERMINATED status = almost always bad news. Drug failed, safety issues,
    or futility analysis. Company often hasn't announced yet.
    Move: typically -20% to -70% on small caps.

  SIGNAL 3 — Unexpected status changes
    ACTIVE_NOT_RECRUITING → COMPLETED ahead of schedule = results imminent
    NOT_YET_RECRUITING → WITHDRAWN = pipeline killed quietly

Strategy: poll the API every 5 minutes for recent changes, compare against
a local snapshot DB, fire Telegram alerts on meaningful transitions.

Run: python ct_bot.py
"""

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# Platform runtime
# ---------------------------------------------------------------------------
# Configuration, credentials, state location, logging and delivery all come
# from the platform (see services/alertlib). Nothing is read from a local
# .env and no path is relative to the working directory.
from alertlib import Service, get_logger

SVC: Service = None          # bound in main()
log = get_logger("clinical-trials")

# Bound from the spec in main(): polling.interval_sec and the platform
# state dir. The defaults below only apply to `--selftest`.
POLL_INTERVAL_SECONDS = 300
DB_PATH = "ct_seen.db"

# ClinicalTrials.gov API v2
CT_API_BASE = "https://clinicaltrials.gov/api/v2/studies"

# Only watch Phase 2, 3, and 4 — Phase 1 moves are rare and smaller
PHASES_OF_INTEREST = {"PHASE2", "PHASE3", "PHASE4", "PHASE2_PHASE3"}

# Status transitions that matter for trading
# Format: (from_status, to_status) -> (signal_name, emoji, description, direction)
# None as from_status = any previous status
SIGNAL_TRANSITIONS = {
    # Results posted — the core edge
    ("COMPLETED", "COMPLETED", True):   ("RESULTS_POSTED",   "🧬", "Results posted on ClinicalTrials.gov", "UNKNOWN — read results"),
    # Trial killed — almost always bad
    (None, "TERMINATED"):               ("TERMINATED",        "💀", "Trial terminated early",               "BEARISH"),
    # Trial withdrawn before it started — pipeline killed
    (None, "WITHDRAWN"):                ("WITHDRAWN",         "🚫", "Trial withdrawn",                      "BEARISH"),
    # Completed but no results yet — watch this one, results imminent
    (None, "COMPLETED"):                ("COMPLETED",         "✅", "Trial completed — results pending",    "WATCH"),
    # Suspended = safety signal, serious
    (None, "SUSPENDED"):                ("SUSPENDED",         "⛔", "Trial suspended — possible safety signal", "BEARISH"),
}

# Fields to request from the API — keeps response lean
CT_FIELDS = [
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.identificationModule.officialTitle",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.lastUpdatePostDate",
    "protocolSection.statusModule.resultsFirstPostDate",
    "protocolSection.designModule.phases",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
    "protocolSection.conditionsModule.conditions",
    "protocolSection.armsInterventionsModule.interventions",
    "resultsSection.outcomeMeasuresModule.outcomeMeasures",
    "hasResults",
]

# Ticker regex — some sponsors include their ticker in their name or description
TICKER_RE = re.compile(
    r"\(\s*(?:NASDAQ|NYSE|NYSE\s*American|AMEX|OTCQB|OTCQX|OTC|Nasdaq)\s*:?\s*([A-Z]{1,5})\s*\)",
    re.IGNORECASE,
)

# Logging is configured by the platform (JSON to stdout -> journald).


# ---------------------------------------------------------------------------
# Database — stores last known state of every trial we've seen
# ---------------------------------------------------------------------------
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trials (
            nct_id          TEXT PRIMARY KEY,
            status          TEXT,
            has_results     INTEGER,
            last_updated    TEXT,
            sponsor         TEXT,
            title           TEXT,
            phases          TEXT,
            first_seen      TEXT,
            alerted_status  TEXT   -- last status we fired an alert for
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nct_id      TEXT,
            signal      TEXT,
            fired_at    TEXT
        )
    """)
    conn.commit()
    return conn


def get_trial(conn: sqlite3.Connection, nct_id: str) -> dict | None:
    cur = conn.execute(
        "SELECT nct_id, status, has_results, last_updated, sponsor, title, phases, first_seen, alerted_status FROM trials WHERE nct_id = ?",
        (nct_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    keys = ["nct_id", "status", "has_results", "last_updated", "sponsor", "title", "phases", "first_seen", "alerted_status"]
    return dict(zip(keys, row, strict=True))


def upsert_trial(conn: sqlite3.Connection, trial: dict):
    conn.execute("""
        INSERT INTO trials (nct_id, status, has_results, last_updated, sponsor, title, phases, first_seen, alerted_status)
        VALUES (:nct_id, :status, :has_results, :last_updated, :sponsor, :title, :phases, :first_seen, :alerted_status)
        ON CONFLICT(nct_id) DO UPDATE SET
            status       = excluded.status,
            has_results  = excluded.has_results,
            last_updated = excluded.last_updated,
            sponsor      = excluded.sponsor,
            title        = excluded.title,
            phases       = excluded.phases,
            alerted_status = excluded.alerted_status
    """, trial)
    conn.commit()


def log_alert(conn: sqlite3.Connection, nct_id: str, signal: str):
    conn.execute(
        "INSERT INTO alert_log (nct_id, signal, fired_at) VALUES (?, ?, ?)",
        (nct_id, signal, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()


# ---------------------------------------------------------------------------
# ClinicalTrials.gov API
# ---------------------------------------------------------------------------
def fetch_recent_changes(days_back: int = 2):
    """
    Yields trials updated in the last N days, one at a time, streaming pages.
    Much lower peak memory than collecting all into a list.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    query_term = (
        f"AREA[StudyType]Interventional"
        f" AND (AREA[Phase]Phase2 OR AREA[Phase]Phase3 OR AREA[Phase]Phase4)"
        f" AND AREA[LastUpdatePostDate]RANGE[{since},MAX]"
    )
    params = {
        "format": "json",
        "pageSize": 200,                     # was 1000 — smaller pages = lower peak memory
        "query.term": query_term,
        "sort": "LastUpdatePostDate:desc",
        "countTotal": "false",
    }

    next_page_token = None
    pages_fetched = 0
    total_yielded = 0

    while True:
        if next_page_token:
            params["pageToken"] = next_page_token
        else:
            params.pop("pageToken", None)

        try:
            resp = requests.get(
                CT_API_BASE,
                params=params,
                timeout=30,
                headers={"User-Agent": "CatalystBot/1.0 (contact@example.com)"},
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as e:
            log.warning("ClinicalTrials API HTTP %s: %s", e.response.status_code, e.response.text[:300])
            return
        except Exception as e:
            log.warning("ClinicalTrials API fetch failed: %s", e)
            return

        studies = data.get("studies", [])
        for s in studies:
            yield s
            total_yielded += 1
        pages_fetched += 1

        next_page_token = data.get("nextPageToken")
        if not next_page_token or pages_fetched >= 10:   # 10 pages × 200 = 2000 cap
            break

        time.sleep(0.15)

    log.info("Streamed %d recently-updated trials from ClinicalTrials.gov", total_yielded)


def parse_trial(raw: dict) -> dict | None:
    """Extract the fields we care about from a raw API response study."""
    try:
        proto = raw.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        conditions = proto.get("conditionsModule", {}).get("conditions", [])
        interventions = proto.get("armsInterventionsModule", {}).get("interventions", [])

        nct_id = ident.get("nctId", "")
        if not nct_id:
            return None

        phases_raw = design.get("phases", [])

        drug_names = [
            i.get("name", "") for i in interventions
            if i.get("type", "").upper() in ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT")
        ]

        return {
            "nct_id": nct_id,
            "title": ident.get("briefTitle", ident.get("officialTitle", "Unknown"))[:200],
            "status": status_mod.get("overallStatus", "UNKNOWN"),
            "has_results": 1 if raw.get("hasResults") else 0,
            "results_posted_date": status_mod.get("resultsFirstPostDate", ""),
            "last_updated": status_mod.get("lastUpdatePostDate", ""),
            "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", "Unknown Sponsor"),
            "phases": json.dumps(sorted(phases_raw)),
            "conditions": conditions[:3],           # top 3 conditions
            "drugs": drug_names[:3],                # top 3 interventions
        }
    except Exception as e:
        log.debug("parse_trial error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------
def detect_signal(prev: dict | None, curr: dict) -> tuple[str, str, str, str] | None:
    """
    Returns (signal_name, emoji, description, direction) if a tradeable
    event is detected, else None.
    """
    new_status = curr["status"]
    new_has_results = curr["has_results"]
    prev_status = prev["status"] if prev else None
    prev_has_results = prev["has_results"] if prev else 0
    prev_alerted = prev["alerted_status"] if prev else None

    # Dedup: don't re-alert the same status we already alerted for
    if prev_alerted == new_status and not (new_has_results and not prev_has_results):
        return None

    # Signal 1: Results appeared on a COMPLETED trial (highest value signal)
    if new_status == "COMPLETED" and new_has_results and not prev_has_results:
        return ("RESULTS_POSTED", "🧬", "Results posted on ClinicalTrials.gov BEFORE press release", "UNKNOWN — check results now")

    # Signal 2: Status became TERMINATED
    if new_status == "TERMINATED" and prev_status != "TERMINATED":
        return ("TERMINATED", "💀", "Trial terminated early", "⬇️ BEARISH")

    # Signal 3: Trial SUSPENDED (safety signal)
    if new_status == "SUSPENDED" and prev_status != "SUSPENDED":
        return ("SUSPENDED", "⛔", "Trial suspended — potential safety signal", "⬇️ BEARISH")

    # Signal 4: Trial WITHDRAWN
    if new_status == "WITHDRAWN" and prev_status != "WITHDRAWN":
        return ("WITHDRAWN", "🚫", "Trial withdrawn before starting", "⬇️ BEARISH")

    # Signal 5: Newly COMPLETED (no results yet — watch for imminent PR)
    if new_status == "COMPLETED" and prev_status not in ("COMPLETED", None) and not new_has_results:
        return ("COMPLETED", "✅", "Trial completed — results / press release imminent", "👀 WATCH")

    return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text: str) -> bool:
    """Delivery goes through the platform client: rate-limited per the
    spec's delivery.rate_limit_per_min, retried on 429, metrics counted."""
    return SVC.telegram.send(text)


def format_alert(trial: dict, signal: str, emoji: str, description: str, direction: str) -> str:
    nct_id = trial["nct_id"]
    conditions_str = ", ".join(trial.get("conditions", [])) or "Not specified"
    drugs_str = ", ".join(trial.get("drugs", [])) or "Not specified"
    phases_raw = json.loads(trial.get("phases", "[]"))
    phase_str = " / ".join(p.replace("PHASE", "Phase ").replace("_", "/") for p in phases_raw) or "Unknown"

    results_line = ""
    if trial.get("results_posted_date"):
        results_line = f"📋 <b>Results posted:</b> {trial['results_posted_date']}\n"

    ct_link = f"https://clinicaltrials.gov/study/{nct_id}"

    return (
        f"{emoji} <b>CLINICALTRIALS SIGNAL: {signal.replace('_', ' ')}</b>\n\n"
        f"<b>{trial['title']}</b>\n\n"
        f"⚡ <b>Signal:</b> {description}\n"
        f"📈 <b>Bias:</b> {direction}\n\n"
        f"🏢 <b>Sponsor:</b> {trial['sponsor']}\n"
        f"💊 <b>Drug(s):</b> {drugs_str}\n"
        f"🩺 <b>Condition(s):</b> {conditions_str}\n"
        f"🔬 <b>Phase:</b> {phase_str}\n"
        f"📅 <b>Last updated:</b> {trial['last_updated']}\n"
        f"{results_line}"
        f"🆔 <b>NCT ID:</b> <code>{nct_id}</code>\n\n"
        f"🔗 {ct_link}\n\n"
        f"⚠️ Search <b>{trial['sponsor']}</b> on your broker — ticker may not be auto-detected"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global SVC, DB_PATH, POLL_INTERVAL_SECONDS
    SVC = Service.from_env()
    DB_PATH = SVC.state_file("ct_seen.db")
    POLL_INTERVAL_SECONDS = SVC.cfg.poll_interval_sec

    with SVC:
        conn = init_db()

        send_telegram(
            "✅ <b>ClinicalTrials.gov Bot started.</b>\n\n"
            "Monitoring Phase 2/3/4 trials for:\n"
            "🧬 Results posted (before press release)\n"
            "💀 Early termination\n"
            "⛔ Trial suspension\n"
            "🚫 Withdrawal\n"
            "✅ Completion (results imminent)\n\n"
            f"Poll interval: every {POLL_INTERVAL_SECONDS // 60} minutes."
        )

        # First run seeds the DB without alerting: snapshot current state so
        # we fire only on changes observed from now on. With state now living
        # in the platform state dir, "first run" means a genuinely new
        # service — not merely a restart from a different working directory.
        if not _has_trials(conn):
            log.info("seeding state with current snapshot; no alerts this pass")
            seeded = 0
            for raw in fetch_recent_changes(days_back=3):
                trial = parse_trial(raw)
                if not trial:
                    continue
                if not get_trial(conn, trial["nct_id"]):
                    trial["first_seen"] = datetime.now(timezone.utc).isoformat()
                    trial["alerted_status"] = trial["status"]
                    upsert_trial(conn, trial)
                    seeded += 1
            log.info("seeded state", extra={"trials": seeded})

        while SVC.running():
            with SVC.poll_cycle():
                alert_count = 0
                for raw in fetch_recent_changes(days_back=2):
                    trial = parse_trial(raw)
                    if not trial:
                        continue
                    SVC.metrics.inc("alert_items_seen_total")

                    nct_id = trial["nct_id"]
                    prev = get_trial(conn, nct_id)
                    signal_result = detect_signal(prev, trial)

                    # State is updated whether or not we alert.
                    trial["first_seen"] = prev["first_seen"] if prev else datetime.now(timezone.utc).isoformat()
                    trial["alerted_status"] = trial["status"]
                    upsert_trial(conn, trial)

                    if not signal_result:
                        continue

                    signal, emoji, description, direction = signal_result
                    log.info("signal detected", extra={
                        "signal": signal, "nct_id": nct_id,
                        "sponsor": trial["sponsor"], "title": trial["title"][:80],
                    })

                    if send_telegram(format_alert(trial, signal, emoji, description, direction)):
                        log_alert(conn, nct_id, signal)
                        alert_count += 1
                    else:
                        # Delivery failed: roll back alerted_status so the
                        # next cycle retries instead of silently dropping it.
                        trial["alerted_status"] = prev["alerted_status"] if prev else None
                        upsert_trial(conn, trial)

                if alert_count:
                    log.info("alerts fired", extra={"count": alert_count})

            SVC.sleep_until_next_poll()

        conn.close()


def _has_trials(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM trials LIMIT 1").fetchone() is not None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        log.exception("Fatal error")
        try:
            send_telegram(f"❌ <b>ClinicalTrials bot crashed:</b> <code>{str(e)[:200]}</code>")
        except Exception:
            pass
        raise
