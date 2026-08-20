"""
FDA Catalyst Alert Bot
======================

Real-time alerts for market-moving FDA events:
  - FDA approvals (NDA/BLA/sNDA/ANDA)
  - Complete Response Letters (CRLs / rejections)
  - FDA designations (Fast Track, Breakthrough, Orphan, Rare Pediatric, RMAT)
  - PDUFA date announcements / delays
  - Clinical holds (FDA halts a trial — major negative catalyst)
  - Recalls & safety comms (Class I recalls, boxed warnings)
  - Priority Review / Accelerated Approval grants

Source strategy (three tiers, polled at different cadences):
  Tier 1 — PR wires (GlobeNewswire, PRNewswire, BusinessWire)    [~45s]
  Tier 2 — SEC EDGAR 8-Ks (Item 8.01 / 7.01 — required filings)  [~5 min]
  Tier 3 — FDA.gov official feeds (press, recalls, MedWatch)     [~10 min]

Note on openFDA: deliberately NOT used as a real-time trigger.
FDA's own enforcement API updates weekly and FDA explicitly advises
against using it to issue alerts. It can be added later as an NDC
enrichment layer.
"""

import os
import re
import time
import sqlite3
import logging
import hashlib
import html
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict

import feedparser
import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Platform runtime
# ---------------------------------------------------------------------------
# Configuration, credentials, state location, logging and delivery all come
# from the platform (see services/alertlib). Nothing is read from a local
# .env and no path is relative to the working directory.
from alertlib import Service, get_logger

SVC: Service = None          # bound in main()
log = get_logger("fda-catalysts")

# Set from the spec in main(): polling.user_agent_secret resolved by
# the apply engine. SEC requires an identifying UA on every request.
EDGAR_USER_AGENT = "FDA-CatalystBot contact@example.com"

# Polling cadences — PR wires fastest, FDA official feeds slowest
WIRE_POLL_INTERVAL_SECONDS = 45
EDGAR_POLL_INTERVAL_SECONDS = 300
FDA_POLL_INTERVAL_SECONDS = 600
HEARTBEAT_INTERVAL_SECONDS = 3600

DB_PATH = "fda_seen.db"

HTTP_HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (compatible; FDA-CatalystBot/1.0)",
    "Accept": "*/*",
}

# ---------------------------------------------------------------------------
# Categories — ORDER MATTERS (first match wins).
# Higher-severity / more-specific events listed first so they claim the match
# before a more generic pattern catches them.
#
# Urgency tiers surface in the alert header:
#   CRITICAL — binary, outcome-defining events (approval / CRL / clinical hold)
#   HIGH     — material but not binary (AdCom, Class I recall, priority review)
#   STANDARD — positive but expected / early-stage (designations, PDUFA dates)
# ---------------------------------------------------------------------------

CATEGORIES = [
    # ----- CRITICAL tier -----
    {
        "name": "FDA_APPROVAL",
        "emoji": "✅",
        "urgency": "CRITICAL",
        "label": "FDA APPROVAL",
        "patterns": [
            # Accelerated approval IS an approval — keep it here, not in PRIORITY_REVIEW
            r"\bFDA\s+grants?\s+accelerated\s+approval\b",
            r"\baccelerated\s+approval\s+(?:from|by)\s+(?:the\s+)?FDA\b",
            r"\bFDA\s+approve[sd]\b",
            r"\bapproved\s+by\s+the\s+(?:U\.?S\.?\s+)?FDA\b",
            r"\breceives?\s+FDA\s+approval\b",
            r"\bannounces?\s+FDA\s+approval\b",
            r"\bgrants?\s+(?:full\s+|traditional\s+)?approval\b.{0,60}\bFDA\b",
            r"\bFDA\s+grants?\s+(?:full\s+|traditional\s+)?approval\b",
            r"\bNDA\s+approval\b",
            r"\bBLA\s+approval\b",
            r"\bsNDA\s+approval\b",
            r"\bsBLA\s+approval\b",
            r"\bANDA\s+approval\b",
        ],
    },
    {
        "name": "FDA_CRL",
        "emoji": "❌",
        "urgency": "CRITICAL",
        "label": "COMPLETE RESPONSE LETTER (CRL)",
        "patterns": [
            r"\bcomplete\s+response\s+letter\b",
            r"\bissued\s+a\s+complete\s+response\b",
            r"\breceived\s+a\s+CRL\b",
            r"\bCRL\s+from\s+(?:the\s+)?FDA\b",
            r"\bFDA\s+issues?\s+CRL\b",
        ],
    },
    {
        "name": "CLINICAL_HOLD",
        "emoji": "🛑",
        "urgency": "CRITICAL",
        "label": "CLINICAL HOLD",
        "patterns": [
            r"\bclinical\s+hold\b",
            r"\bpartial\s+clinical\s+hold\b",
            r"\bFDA\s+places?\s+.{0,40}\bon\s+(?:partial\s+)?(?:clinical\s+)?hold\b",
            r"\bplaced\s+on\s+(?:partial\s+)?clinical\s+hold\b",
            r"\bFDA\s+lifts?\s+clinical\s+hold\b",
            r"\bclinical\s+hold\s+(?:lifted|removed)\b",
        ],
    },

    # ----- HIGH tier -----
    {
        "name": "PRIORITY_REVIEW",
        "emoji": "⚡",
        "urgency": "HIGH",
        "label": "PRIORITY REVIEW",
        "patterns": [
            r"\bpriority\s+review\s+(?:designation|granted|voucher)\b",
            r"\bgranted\s+priority\s+review\b",
            r"\bFDA\s+grants?\s+priority\s+review\b",
            r"\baccepted\s+for\s+priority\s+review\b",
        ],
    },
    {
        "name": "ADCOM",
        "emoji": "🗳️",
        "urgency": "HIGH",
        "label": "ADVISORY COMMITTEE (AdCom)",
        "patterns": [
            r"\badvisory\s+committee\s+(?:meeting|votes?|recommends?)\b",
            r"\bAdCom\s+(?:meeting|vote|recommends?)\b",
            r"\bFDA\s+advisory\s+committee\b",
            r"\bvoted?\s+\d+\s*[-–to]+\s*\d+\b.{0,60}\b(?:recommend|approve|support)\b",
            r"\boncologic\s+drugs\s+advisory\s+committee\b",
            r"\bODAC\b",
            r"\bCRDAC\b",  # Cellular, Tissue, Gene Therapies
            r"\bPADAC\b",  # Peripheral and Central Nervous System Drugs
        ],
    },
    {
        "name": "CLASS_I_RECALL",
        "emoji": "🚨",
        "urgency": "HIGH",
        "label": "CLASS I RECALL / SAFETY ALERT",
        "patterns": [
            r"\bClass\s+I\s+recall\b",
            r"\bnationwide\s+recall\b",
            r"\burgent\s+recall\b",
            r"\bboxed\s+warning\b",
            r"\bblack\s+box\s+warning\b",
            r"\bFDA\s+safety\s+(?:alert|communication)\b",
            r"\bMedWatch\s+safety\s+alert\b",
            r"\bFDA\s+warns?\s+(?:of|about)\b",
        ],
    },

    # ----- STANDARD tier -----
    {
        "name": "BREAKTHROUGH",
        "emoji": "⭐",
        "urgency": "STANDARD",
        "label": "BREAKTHROUGH THERAPY DESIGNATION",
        "patterns": [
            r"\bbreakthrough\s+therapy\s+designation\b",
            r"\bgranted\s+breakthrough\s+therapy\b",
            r"\breceives?\s+breakthrough\s+therapy\b",
            r"\bBTD\s+from\s+(?:the\s+)?FDA\b",
        ],
    },
    {
        "name": "FAST_TRACK",
        "emoji": "🎯",
        "urgency": "STANDARD",
        "label": "FAST TRACK DESIGNATION",
        "patterns": [
            r"\bfast[\s-]?track\s+designation\b",
            r"\bgranted\s+fast[\s-]?track\b",
            r"\breceives?\s+fast[\s-]?track\b",
            r"\bawarded\s+fast[\s-]?track\b",
            r"\bFDA\s+fast[\s-]?track\b",
        ],
    },
    {
        "name": "ORPHAN_DRUG",
        "emoji": "🧬",
        "urgency": "STANDARD",
        "label": "ORPHAN DRUG DESIGNATION",
        "patterns": [
            r"\borphan\s+drug\s+designation\b",
            r"\bgranted\s+orphan\s+drug\b",
            r"\breceives?\s+orphan\s+(?:drug\s+)?designation\b",
            r"\bODD\s+from\s+(?:the\s+)?FDA\b",
        ],
    },
    {
        "name": "RARE_PEDIATRIC",
        "emoji": "👶",
        "urgency": "STANDARD",
        "label": "RARE PEDIATRIC DISEASE DESIGNATION",
        "patterns": [
            r"\brare\s+pediatric\s+disease\s+designation\b",
            r"\brare\s+pediatric\s+disease\s+voucher\b",
            r"\bpriority\s+review\s+voucher\b.{0,80}\bpediatric\b",
        ],
    },
    {
        "name": "RMAT",
        "emoji": "🧪",
        "urgency": "STANDARD",
        "label": "REGENERATIVE MEDICINE (RMAT)",
        "patterns": [
            r"\bregenerative\s+medicine\s+advanced\s+therapy\b",
            r"\bRMAT\s+designation\b",
            r"\bgranted\s+RMAT\b",
        ],
    },
    {
        "name": "PDUFA",
        "emoji": "📅",
        "urgency": "STANDARD",
        "label": "PDUFA DATE / DELAY",
        "patterns": [
            r"\bPDUFA\s+(?:date|action\s+date|goal\s+date)\b",
            r"\bPDUFA\s+extension\b",
            r"\bextended\s+PDUFA\b",
            r"\bFDA\s+extends?\s+(?:the\s+)?(?:review|PDUFA)\b",
            r"\bPDUFA\s+target\s+(?:action\s+)?date\b",
            r"\bFDA\s+accept(?:s|ed)\s+(?:NDA|BLA|sNDA|sBLA)\b",
            r"\b(?:NDA|BLA|sNDA|sBLA)\s+accepted\s+for\s+(?:filing|review)\b",
        ],
    },
    # EDGAR-triggered placeholder — matched by filing content, not regex alone
    {
        "name": "EDGAR_FDA_EVENT",
        "emoji": "📄",
        "urgency": "HIGH",
        "label": "SEC 8-K — FDA MATERIAL EVENT",
        "patterns": [],  # matched via EDGAR + keyword combo
    },
]

# Compile unified regex per category
for cat in CATEGORIES:
    cat["_re"] = re.compile("|".join(cat["patterns"]), re.IGNORECASE) if cat["patterns"] else None

# Urgency → header emoji banner
URGENCY_BANNER = {
    "CRITICAL": "🚨🚨🚨 CRITICAL",
    "HIGH": "⚠️ HIGH",
    "STANDARD": "📋 STANDARD",
}

# ---------------------------------------------------------------------------
# Feed sources
# ---------------------------------------------------------------------------

# Tier 1 — PR wires (company announcements, fastest)
WIRE_FEEDS = [
    # PR wire aggregators — biotech/pharma/health sub-feeds
    ("GlobeNewswire-Pharma",
     "https://www.globenewswire.com/RssFeed/industry/9572-Pharmaceuticals/feedTitle/GlobeNewswire%20-%20Pharmaceuticals"),
    ("GlobeNewswire-Biotech",
     "https://www.globenewswire.com/RssFeed/industry/4577-Biotechnology/feedTitle/GlobeNewswire%20-%20Biotechnology"),
    ("GlobeNewswire-Health",
     "https://www.globenewswire.com/RssFeed/industry/1200-Health/feedTitle/GlobeNewswire%20-%20Health"),
    ("PRNewswire-Health",
     "https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss"),
    ("PRNewswire-Biotech",
     "https://www.prnewswire.com/rss/news-releases-list.rss?category=BIO"),
    # Biotech/pharma trade press — often fastest for editorial coverage of wire hits
    ("FierceBiotech", "https://www.fiercebiotech.com/rss/xml"),
    ("FiercePharma", "https://www.fiercepharma.com/rss/xml"),
    ("BioPharmaDive", "https://www.biopharmadive.com/feeds/news/"),
    ("EndpointsNews", "https://endpts.com/feed/"),
    # NOTE: BusinessWire public RSS (portal/site/home/rss/?industry=...) returns
    # 403 to non-partner IPs as of 2025+. Removed — use their partner feed if
    # you have one, or scrape the /newsroom/industry/... pages separately.
]

# Tier 2 — SEC EDGAR current 8-K filings (atom).
# 8-Ks covering FDA events are usually filed under Item 8.01 (Other Events)
# or occasionally Item 7.01 (Reg FD) and 2.02 (Results of Ops for trial data).
EDGAR_FEEDS = [
    ("EDGAR-8K",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
     "8-K"),
]

# Tier 3 — FDA.gov official RSS (authoritative, slower cadence)
FDA_FEEDS = [
    ("FDA-PressReleases",
     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    ("FDA-Recalls",
     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml"),
    ("FDA-MedWatch",
     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml"),
    ("FDA-Drugs",
     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"),
    ("FDA-Biologics",
     "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/biologics/rss.xml"),
]

# Ticker extraction — matches (EXCHANGE: TICKER) in press releases
TICKER_RE = re.compile(
    r"\((?:NASDAQ|NYSE|NYSE\s+American|NYSEAmerican|AMEX|OTCQB|OTCQX|OTC|Nasdaq|TSX|TSXV|LSE)\s*:?\s*([A-Z]{1,5})\)",
    re.IGNORECASE,
)

# Logging is configured by the platform (JSON to stdout -> journald).

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class Hit:
    source: str
    category: str
    title: str
    link: str
    summary: str
    published: str
    ticker: Optional[str] = None
    matched_phrase: Optional[str] = None

    def fingerprint(self) -> str:
        # Fingerprint on (source, category, link) so the same PR picked up
        # under two categories from two feeds still collapses sensibly
        base = f"{self.category}|{self.link.strip().lower()}"
        return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS seen (
            fp TEXT PRIMARY KEY,
            source TEXT,
            category TEXT,
            title TEXT,
            link TEXT,
            seen_at TEXT
        )"""
    )
    conn.commit()
    return conn


def is_seen(conn, fp: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen WHERE fp = ?", (fp,))
    return cur.fetchone() is not None


def mark_seen(conn, hit: Hit):
    conn.execute(
        "INSERT OR IGNORE INTO seen (fp, source, category, title, link, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
        (hit.fingerprint(), hit.source, hit.category, hit.title, hit.link,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def classify(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (category_name, matched_phrase) for first matching category."""
    for cat in CATEGORIES:
        if cat["_re"] is None:
            continue
        m = cat["_re"].search(text or "")
        if m:
            return cat["name"], m.group(0)
    return None, None


def category_meta(name: str) -> Dict:
    for cat in CATEGORIES:
        if cat["name"] == name:
            return cat
    return {"emoji": "📢", "label": name, "urgency": "STANDARD"}


def extract_ticker(text: str) -> Optional[str]:
    m = TICKER_RE.search(text or "")
    return m.group(1).upper() if m else None


def clean_summary(raw: str, max_len: int = 400) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", raw or "")
    no_tags = html.unescape(no_tags)
    collapsed = re.sub(r"\s+", " ", no_tags).strip()
    return collapsed[:max_len] + ("…" if len(collapsed) > max_len else "")


def _fmt_published(entry) -> str:
    if getattr(entry, "published", None):
        return str(entry.published)
    if getattr(entry, "updated", None):
        return str(entry.updated)
    if getattr(entry, "published_parsed", None):
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    if getattr(entry, "updated_parsed", None):
        dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_feed(name: str, url: str, headers: Optional[Dict] = None, limit: int = 50) -> List[Hit]:
    """Generic RSS/Atom fetcher with classification."""
    hits: List[Hit] = []
    try:
        resp = requests.get(url, headers=headers or HTTP_HEADERS_DEFAULT, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Failed to fetch %s: %s", name, e)
        return []

    for entry in getattr(feed, "entries", [])[:limit]:
        title = (getattr(entry, "title", "") or "").strip()
        link = (getattr(entry, "link", "") or "").strip()
        summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        published = _fmt_published(entry)

        text_blob = f"{title}\n{summary_raw}"
        category, phrase = classify(text_blob)
        if not category:
            continue

        hits.append(Hit(
            source=name,
            category=category,
            title=title,
            link=link,
            summary=clean_summary(summary_raw),
            published=published,
            ticker=extract_ticker(text_blob),
            matched_phrase=phrase,
        ))

    return hits


def fetch_edgar_8k(name: str, url: str) -> List[Hit]:
    """
    EDGAR 8-K current feed. Unlike v2, we classify by keyword content —
    a plain 8-K filing is too noisy to alert on without an FDA keyword match.
    """
    hits: List[Hit] = []
    try:
        headers = {"User-Agent": EDGAR_USER_AGENT, "Accept": "application/atom+xml"}
        resp = requests.get(url, headers=headers, timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("EDGAR fetch failed (%s): %s", name, e)
        return []

    for entry in getattr(feed, "entries", [])[:60]:
        title = (getattr(entry, "title", "") or "").strip()
        link = (getattr(entry, "link", "") or "").strip()
        summary_raw = getattr(entry, "summary", "") or ""
        published = _fmt_published(entry)

        text_blob = f"{title}\n{summary_raw}"
        category, phrase = classify(text_blob)
        if not category:
            continue

        hits.append(Hit(
            source=name,
            category=category,
            title=title,
            link=link,
            summary=clean_summary(summary_raw),
            published=published,
            ticker=extract_ticker(text_blob),
            matched_phrase=phrase,
        ))

    return hits


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def send_telegram(text: str) -> bool:
    """Delivery via the platform client (rate limit, retries, metrics)."""
    return SVC.telegram.send(text)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


def format_alert(hit: Hit) -> str:
    meta = category_meta(hit.category)
    emoji = meta.get("emoji", "📢")
    label = meta.get("label", hit.category)
    urgency = meta.get("urgency", "STANDARD")
    banner = URGENCY_BANNER.get(urgency, "📋 STANDARD")

    ticker_line = (
        f"🎯 Ticker: <b>{html.escape(hit.ticker)}</b>\n"
        if hit.ticker else "⚠️ Ticker: <i>not auto-detected</i>\n"
    )
    phrase_line = (
        f"🔑 Match: <code>{html.escape(hit.matched_phrase or '')}</code>\n"
        if hit.matched_phrase else ""
    )

    return (
        f"{banner}\n"
        f"{emoji} <b>{html.escape(label)}</b>\n"
        f"📰 Source: {html.escape(hit.source)}\n"
        f"🕒 {html.escape(hit.published)}\n\n"
        f"{ticker_line}"
        f"{phrase_line}\n"
        f"<b>{html.escape(hit.title)}</b>\n\n"
        f"{html.escape(hit.summary)}\n\n"
        f"🔗 {html.escape(hit.link)}"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

# Priority order for processing — CRITICAL first so if the Telegram API
# throttles us, the highest-urgency alerts go out first.
_URGENCY_ORDER = {"CRITICAL": 0, "HIGH": 1, "STANDARD": 2}


def _process_hits(conn, hits: List[Hit]) -> int:
    SVC.metrics.inc("alert_items_seen_total", len(hits))
    # Sort by urgency before sending
    hits_sorted = sorted(
        hits,
        key=lambda h: _URGENCY_ORDER.get(category_meta(h.category).get("urgency", "STANDARD"), 99),
    )
    sent = 0
    for hit in hits_sorted:
        fp = hit.fingerprint()
        if is_seen(conn, fp):
            continue
        # Mark seen BEFORE sending — prevents duplicates if Telegram errors
        mark_seen(conn, hit)
        if send_telegram(format_alert(hit)):
            sent += 1
            log.info("Alert sent: [%s] %s | %s",
                     category_meta(hit.category).get("urgency"),
                     hit.category, hit.title[:90])
        else:
            log.warning("Alert failed: %s | %s", hit.category, hit.title[:90])
    return sent


def main():
    global SVC, DB_PATH, EDGAR_USER_AGENT
    global WIRE_POLL_INTERVAL_SECONDS, EDGAR_POLL_INTERVAL_SECONDS, FDA_POLL_INTERVAL_SECONDS

    SVC = Service.from_env()
    DB_PATH = SVC.state_file("fda_seen.db")

    # SEC blocks unidentified clients; the UA is a credential-ish value and
    # therefore lives in the secret store, referenced by name in the spec.
    EDGAR_USER_AGENT = SVC.cfg.secret("edgar_user_agent")
    HTTP_HEADERS_DEFAULT["User-Agent"] = EDGAR_USER_AGENT

    # Primary cadence comes from polling.interval_sec. The two slower tiers
    # are service-specific and ride the spec.polling extension point, so all
    # three cadences are visible in the spec rather than only in this file.
    WIRE_POLL_INTERVAL_SECONDS = SVC.cfg.poll_interval_sec
    EDGAR_POLL_INTERVAL_SECONDS = int(SVC.cfg.polling("edgar_interval_sec", 300))
    FDA_POLL_INTERVAL_SECONDS = int(SVC.cfg.polling("fda_interval_sec", 600))

    with SVC:
        conn = init_db()

        send_telegram(
            "✅ <b>FDA Catalyst Bot started</b>\n\n"
            "Tracking:\n"
            "🚨 CRITICAL: Approvals, CRLs, Clinical Holds\n"
            "⚠️ HIGH: AdCom, Class I Recalls, Priority Review, 8-K FDA events\n"
            "📋 STANDARD: Fast Track, Breakthrough, Orphan, RMAT, Pediatric, PDUFA\n\n"
            "Sources: PR wires, SEC EDGAR 8-Ks, FDA.gov official feeds"
        )

        # Cadence bookkeeping for the slower tiers.
        last_edgar_poll = 0.0
        last_fda_poll = 0.0

        while SVC.running():
            with SVC.poll_cycle():
                now = time.time()

                # Tier 1 — PR wires (every cycle, ~45s)
                wire_sent = 0
                for src_name, src_url in WIRE_FEEDS:
                    try:
                        wire_sent += _process_hits(conn, fetch_feed(src_name, src_url))
                    except Exception as e:
                        log.exception("Error processing wire %s: %s", src_name, e)
                if wire_sent:
                    log.info("Wire alerts sent: %d", wire_sent)

                # Tier 2 — EDGAR 8-Ks (every ~5 min)
                if now - last_edgar_poll >= EDGAR_POLL_INTERVAL_SECONDS:
                    edgar_sent = 0
                    for name, url, _ftype in EDGAR_FEEDS:
                        try:
                            edgar_sent += _process_hits(conn, fetch_edgar_8k(name, url))
                        except Exception as e:
                            log.exception("Error processing EDGAR %s: %s", name, e)
                    if edgar_sent:
                        log.info("EDGAR alerts sent: %d", edgar_sent)
                    last_edgar_poll = now

                # Tier 3 — FDA.gov (every ~10 min)
                if now - last_fda_poll >= FDA_POLL_INTERVAL_SECONDS:
                    fda_sent = 0
                    for src_name, src_url in FDA_FEEDS:
                        try:
                            fda_sent += _process_hits(conn, fetch_feed(src_name, src_url))
                        except Exception as e:
                            log.exception("Error processing FDA feed %s: %s", src_name, e)
                    if fda_sent:
                        log.info("FDA.gov alerts sent: %d", fda_sent)
                    last_fda_poll = now

            SVC.sleep_until_next_poll(WIRE_POLL_INTERVAL_SECONDS)



        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt)")
    except Exception as e:
        log.exception("Fatal error")
        try:
            send_telegram(f"❌ <b>FDA Bot crashed</b>\n<code>{html.escape(str(e))}</code>")
        except Exception:
            pass
        raise

