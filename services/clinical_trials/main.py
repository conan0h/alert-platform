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
from alertlib import Alert, CycleFunnel, Service, get_logger

SVC: Service = None          # bound in main()
log = get_logger("clinical-trials")

# Bound from the spec in main(): polling.interval_sec and the platform
# state dir. The defaults below only apply to `--selftest`.
POLL_INTERVAL_SECONDS = 300
DB_PATH = "ct_seen.db"

# ClinicalTrials.gov API v2
CT_API_BASE = "https://clinicaltrials.gov/api/v2/studies"

# 10 pages x pageSize 200 = 2000 candidates per cycle. Named so the funnel
# line can say whether the cap was reached rather than leaving a truncated
# window to look like a complete one.
MAX_PAGES = 10

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

# The candidate pipeline, in order. Named here rather than inline so the
# funnel line and the /metrics counters cannot drift apart, and so the shape
# of the question backlog #35 asks is readable in one place:
#
#   streamed -> parsed -> (known | first_sight) -> changed -> signals -> sent
#
# `first_sight_completed` is not a stage; it is the count that tests the
# leading explanation for zero alerts. A trial enters the two-day window
# *because* it was just updated, so the update that flips it to COMPLETED is
# usually the same update that first shows it to us — and `detect_signal`
# suppresses COMPLETED when it has no previous status to compare against.
# If that number is large every cycle, the filter is not too tight: the
# transition is being observed one cycle too late to count as a transition.
FUNNEL_STAGES = (
    "streamed",
    "parsed",
    "known",
    "first_sight",
    "first_sight_completed",
    "changed",
    "signals",
    "sent",
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
        # The authoritative size of the match, which is the difference
        # between "399 is everything there is" and "399 is where we stopped
        # reading". Backlog #35 could not tell those apart from the old log
        # line. The API computes it once per page and we only read it from
        # the first, so the cost is one count per cycle.
        "countTotal": "true",
    }

    next_page_token = None
    pages_fetched = 0
    total_yielded = 0
    total_available = None
    capped = False

    # The tally is reported in a `finally` because the two early returns below
    # skip it otherwise: a cycle that failed on page two then logs nothing at
    # all, which is indistinguishable from a cycle that never ran. Page count
    # is reported for the same reason — whether this query paginates at all
    # was an open question in backlog #35, and it is one line to answer.
    try:
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

            if total_available is None:
                total_available = data.get("totalCount")

            # Both counters advance at the moment their fact becomes true,
            # not after the consumer comes back. A generator abandoned at a
            # yield never resumes, so counting after `yield study` reported
            # one fewer than it had handed out — a tally that under-reports
            # exactly when a cycle went wrong is worse than no tally.
            pages_fetched += 1
            studies = data.get("studies", [])
            for study in studies:
                total_yielded += 1
                yield study

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
            if pages_fetched >= MAX_PAGES:
                capped = True
                break

            time.sleep(0.15)
    finally:
        log.info(
            "Streamed %d of %s recently-updated trials from "
            "ClinicalTrials.gov in %d page(s)%s",
            total_yielded,
            "unknown" if total_available is None else total_available,
            pages_fetched,
            " (page cap reached — the window is larger than we read)" if capped else "",
        )


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
def observation_stages(prev: dict | None, curr: dict) -> tuple[str, ...]:
    """Which funnel stages one observation of a trial falls into.

    A separate function from `detect_signal` because it answers a different
    question: not "is this tradeable" but "what did we just look at". Kept
    out of the poll loop so the counts backlog #35 turns on can be tested
    without running a cycle.

    `first_sight_completed` is the one worth explaining. The query selects
    trials whose last update landed in the past two days, so a trial usually
    enters our view *because* of the update we care about — and when that
    update is the flip to COMPLETED, we have no earlier status to compare it
    against. `detect_signal` drops that case (signal 5 requires
    `prev_status not in ("COMPLETED", None)`), on the reasoning that an
    unobserved transition is not a transition. This counter measures what
    that reasoning costs per cycle.
    """
    if prev is None:
        if curr["status"] == "COMPLETED":
            return ("first_sight", "first_sight_completed")
        return ("first_sight",)
    if prev["status"] != curr["status"]:
        return ("known", "changed")
    return ("known",)


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


def build_alert(trial: dict, signal: str, description: str, direction: str,
                body: str) -> Alert:
    """The archive record for one trial-status signal.

    The dedup key carries the signal as well as the NCT id: one trial can
    legitimately alert more than once over its life — a status change, then
    results posted — and those are different events, not a repeat.
    """
    return Alert(
        source="ClinicalTrials.gov",
        dedup_key=f"{trial['nct_id']}#{signal}",
        title=f"{signal.replace('_', ' ')}: {trial.get('title', '')[:160]}",
        body=body,
        # The registry identifies studies, not issuers; there is no ticker to
        # record, and an empty column is more honest than the sponsor's name
        # in a field every other service fills with a symbol.
        ticker="",
        reason=description,
        payload={
            "nct_id": trial["nct_id"],
            "signal": signal,
            "direction": direction,
            "sponsor": trial.get("sponsor"),
            "status": trial.get("status"),
            "phases": trial.get("phases"),
            "conditions": trial.get("conditions"),
            "drugs": trial.get("drugs"),
            "results_posted_date": trial.get("results_posted_date"),
        },
    )


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

    funnel = CycleFunnel(SVC.metrics, FUNNEL_STAGES, log=log)

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
            with SVC.poll_cycle(), funnel.cycle():
                alert_count = 0
                cohort: list[str] = []
                for raw in fetch_recent_changes(days_back=2):
                    funnel.count("streamed")
                    trial = parse_trial(raw)
                    if not trial:
                        continue
                    funnel.count("parsed")
                    SVC.metrics.inc("alert_items_seen_total")

                    nct_id = trial["nct_id"]
                    cohort.append(nct_id)
                    prev = get_trial(conn, nct_id)
                    for stage in observation_stages(prev, trial):
                        funnel.count(stage)

                    signal_result = detect_signal(prev, trial)

                    # State is updated whether or not we alert.
                    trial["first_seen"] = prev["first_seen"] if prev else datetime.now(timezone.utc).isoformat()
                    trial["alerted_status"] = trial["status"]
                    upsert_trial(conn, trial)

                    if not signal_result:
                        continue
                    funnel.count("signals")

                    signal, emoji, description, direction = signal_result
                    log.info("signal detected", extra={
                        "signal": signal, "nct_id": nct_id,
                        "sponsor": trial["sponsor"], "title": trial["title"][:80],
                    })

                    body = format_alert(trial, signal, emoji, description, direction)
                    if SVC.send_alert(build_alert(trial, signal, description, direction, body)):
                        log_alert(conn, nct_id, signal)
                        funnel.count("sent")
                        alert_count += 1
                    else:
                        # Delivery failed: roll back alerted_status so the
                        # next cycle retries instead of silently dropping it.
                        trial["alerted_status"] = prev["alerted_status"] if prev else None
                        upsert_trial(conn, trial)

                funnel.observe_cohort(cohort)

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
