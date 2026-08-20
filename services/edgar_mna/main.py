"""
M&A Catalyst Alert Bot
======================

Real-time alerts for market-moving M&A events, with enrichment.

Deal types (urgency tier):
  🚨 CRITICAL
    SIGNED_DEAL           - Definitive/signed merger or acquisition agreement
    UNSOLICITED_PROPOSAL  - Hostile bid, bear hug, non-binding proposal, rejected offer
    TENDER_OFFER          - Public tender offer commenced
    TAKEOVER_RUMOR        - Reuters/Bloomberg scoops, "in talks to acquire"
  ⚠️ HIGH
    COMPETING_BID         - Superior proposal, bidding war, go-shop
    DEAL_TERMINATED       - Deal collapsed, regulator blocked, mutually terminated
    ACTIVIST_13D          - PR wire activist disclosure narrative
    PE_TAKEPRIVATE        - Private-equity take-private (named sponsors)
    STRATEGIC_REVIEW      - "Exploring strategic alternatives" / sale process
  📋 STANDARD
    SPAC_COMBINATION      - de-SPAC / business combination agreement

Dropped (not alerted):
    COMPLETED             - Deals that already closed; no tradeable move
    LOI                   - Non-binding letters of intent (mostly small-cap mining noise)

Enrichment (populated when available):
    Offer price per share, cash/stock/mixed, premium-if-stated, deal size.
    RSS summary tried first; PR body fetched only if offer price missing.

Source tiers:
    Tier 1 — PR wires          [~45s]   GlobeNewswire, PRNewswire, Yahoo Finance
    Tier 2 — SEC EDGAR         [~5 min] 8-K, SC 13D, SC 13G, SC TO-T, DEFM14A
    Tier 3 — Financial press   [~10 min] NYT, MarketWatch, CNBC, SeekingAlpha
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
log = get_logger("edgar-mna")

# Set from the spec in main(): polling.user_agent_secret resolved by
# the apply engine. SEC requires an identifying UA on every request.
EDGAR_USER_AGENT = "MA-CatalystBot contact@example.com"

WIRE_POLL_INTERVAL_SECONDS = 45
EDGAR_POLL_INTERVAL_SECONDS = 300
PRESS_POLL_INTERVAL_SECONDS = 600
HEARTBEAT_INTERVAL_SECONDS = 3600

# Enrichment config
PR_FETCH_TIMEOUT_SECONDS = 3          # hard cap — never stall the poll loop
PR_FETCH_MAX_BYTES = 200_000          # don't pull multi-megabyte pages

DB_PATH = "ma_seen.db"

HTTP_HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (compatible; MA-CatalystBot/2.0)",
    "Accept": "*/*",
}

# ---------------------------------------------------------------------------
# Categories — order matters, first match wins.
# Pattern design notes:
#   * UNSOLICITED_PROPOSAL sits BEFORE SIGNED_DEAL so a proposal never
#     accidentally claims the "definitive agreement" label. A bid that's
#     "unsolicited" or "rejected" by the board is a PROPOSAL, not a signed deal.
#   * DEAL_TERMINATED before SIGNED_DEAL so "terminate merger agreement"
#     doesn't match the naked "merger agreement" language.
#   * COMPETING_BID before SIGNED_DEAL for the same reason ("go-shop" refs agreement).
#   * COMPLETED explicitly DROPPED — no category for it. If a wire says
#     "completes acquisition", nothing matches, no alert. Intentional.
#   * LOI patterns removed from SIGNED_DEAL — LOIs are now unmatched.
# ---------------------------------------------------------------------------

CATEGORIES = [
    # ---- CRITICAL ----
    {
        "name": "UNSOLICITED_PROPOSAL",
        "emoji": "⚔️",
        "urgency": "CRITICAL",
        "label": "UNSOLICITED / HOSTILE PROPOSAL",
        "patterns": [
            # Explicit hostile / unsolicited framing
            r"\bunsolicited\s+(?:offer|proposal|bid)\b",
            r"\bhostile\s+(?:takeover|bid|offer|proposal)\b",
            r"\brejects?\s+(?:the\s+)?(?:unsolicited|hostile)\s+(?:offer|proposal|bid)\b",
            r"\bbear[\s-]?hug\s+(?:letter|offer|proposal)\b",
            r"\bopen(?:\s+)?letter\s+to\s+(?:the\s+)?(?:board|shareholders)\b.{0,100}\b(?:acqui|offer|propos|takeover)\b",
            # "Proposes to acquire" — this is exactly the RePay / Forage language.
            # A real signed deal uses "enters into agreement", not "proposes".
            r"\bproposes?\s+to\s+acquire\b",
            r"\bsubmits?\s+(?:a\s+)?(?:non-?binding\s+)?proposal\s+to\s+acquire\b",
            r"\bnon-?binding\s+proposal\s+to\s+acquire\b",
            # Board rejection language = implies it was unsolicited.
            # Allow a few words (dollar amounts, adjectives) between rejects and the noun.
            r"\bboard\s+(?:unanimously\s+)?rejects?\s+(?:the\s+)?(?:\$?[\d.,]+[bBmM]?\s+)?(?:\w+\s+){0,3}(?:offer|proposal|bid)\b",
            # Sweetened/revised unsolicited bids (before COMPETING_BID claims them)
            r"\brevised\s+(?:unsolicited|hostile)\s+(?:bid|offer|proposal)\b",
        ],
    },
    {
        "name": "TENDER_OFFER",
        "emoji": "📣",
        "urgency": "CRITICAL",
        "label": "TENDER OFFER",
        "patterns": [
            r"\bcommences?\s+(?:a\s+)?tender\s+offer\b",
            r"\btender\s+offer\s+to\s+(?:acquire|purchase)\b",
            r"\bcash\s+tender\s+offer\b",
            r"\bexchange\s+offer\s+for\s+(?:all|any\s+and\s+all)\b",
            r"\bbegins?\s+tender\s+offer\b",
        ],
    },
    # DEAL_TERMINATED before SIGNED_DEAL — "terminate merger agreement" must not match "merger agreement"
    {
        "name": "DEAL_TERMINATED",
        "emoji": "💔",
        "urgency": "HIGH",
        "label": "DEAL TERMINATED / COLLAPSED",
        "patterns": [
            r"\bterminates?\s+(?:the\s+)?(?:merger|acquisition|agreement)\b",
            r"\bmerger\s+(?:agreement\s+)?terminat(?:ed|ion)\b",
            r"\bmutually\s+terminate\b",
            r"\bwalk(?:s|ed)?\s+away\s+from\s+(?:the\s+)?(?:deal|merger|acquisition)\b",
            r"\babandon(?:s|ed)\s+(?:the\s+)?(?:merger|acquisition|deal)\b",
            r"\bcall(?:s|ed)?\s+off\s+(?:the\s+)?(?:\$?[\d.,]+[bBmM]?\s+)?(?:merger|deal|acquisition|transaction)\b",
            r"\bdeal\s+collapse[sd]?\b",
            r"\bbreak(?:up)?\s+fee\s+paid\b",
            r"\bantitrust\s+block(?:s|ed)?\b.{0,80}\b(?:merger|acquisition|deal)\b",
            r"\b(?:FTC|DOJ|EC)\s+block(?:s|ed)?\b.{0,80}\b(?:merger|acquisition|deal)\b",
        ],
    },
    # COMPETING_BID before SIGNED_DEAL — "go-shop period" refs merger agreement
    {
        "name": "COMPETING_BID",
        "emoji": "🥊",
        "urgency": "HIGH",
        "label": "COMPETING BID / BIDDING WAR",
        "patterns": [
            r"\bcompeting\s+(?:bid|offer|proposal)\b",
            r"\bsuperior\s+(?:proposal|offer)\b",
            r"\btopping\s+(?:bid|offer)\b",
            r"\braises?\s+(?:its\s+)?(?:offer|bid)\b",
            r"\bincreases?\s+(?:its\s+)?(?:offer|bid)\s+(?:price|to)\b",
            r"\brevised\s+(?:offer|bid|proposal)\b",
            r"\bsweetens?\s+(?:its\s+)?(?:offer|bid)\b",
            r"\bgo[\s-]?shop\s+(?:period|provision)\b",
        ],
    },
    # PE_TAKEPRIVATE before SIGNED_DEAL — so "Thoma Bravo to acquire X for $Y"
    # gets classified as a PE take-private rather than a generic signed deal.
    {
        "name": "PE_TAKEPRIVATE",
        "emoji": "🏦",
        "urgency": "HIGH",
        "label": "PRIVATE EQUITY TAKE-PRIVATE",
        "patterns": [
            r"\btake[\s-]?private\s+(?:transaction|deal|offer)\b",
            r"\b(?:Blackstone|KKR|Carlyle|Apollo|Bain\s+Capital|Thoma\s+Bravo|Vista\s+Equity|Silver\s+Lake|Advent|TPG|Warburg\s+Pincus|CVC|Permira|EQT|Francisco\s+Partners)\b.{0,120}\b(?:to\s+acquire|acquires?)\b",
            r"\bprivate\s+equity\s+(?:consortium|firm|group)\s+to\s+acquire\b",
            r"\bled\s+by\s+(?:a\s+)?private\s+equity\b.{0,60}\bacqui",
        ],
    },
    {
        "name": "SIGNED_DEAL",
        "emoji": "💰",
        "urgency": "CRITICAL",
        "label": "SIGNED DEFINITIVE AGREEMENT",
        "patterns": [
            # "Definitive agreement" with explicit acquisition language
            r"\bdefinitive\s+agreement\s+to\s+(?:be\s+)?acqui(?:re|red)\b",
            r"\bdefinitive\s+(?:merger\s+)?agreement\b.{0,100}\bacqui",
            # "Enters into" / "Signs" phrasings
            r"\benters?\s+into\s+(?:a\s+)?(?:definitive\s+)?(?:merger\s+)?agreement\b.{0,80}\bacqui",
            r"\benter\s+into\s+definitive\s+merger\s+agreement\b",
            r"\bsigns?\s+(?:a\s+)?definitive\s+agreement\s+to\s+acqui",
            # Reverse phrasing
            r"\bto\s+be\s+acquired\s+by\b",
            r"\bagrees?\s+to\s+acquire\b",
            # Naked "to acquire" — common real-world pattern.
            # Required substance context so we don't match "plans to acquire expertise":
            r"\bto\s+acquire\s+(?:all\s+of|the\s+outstanding|\$[\d,.]+|100%)",
            r"\bto\s+acquire\s+(?:\w+\s+)?\d+\s+(?:asset|propert|project|compan|busines|stream|mine|well|store)",
            r"\bto\s+acquire\s+(?:[A-Z][\w&.-]+\s+){1,6}(?:for\s+\$|in\s+(?:a\s+)?(?:\$|all-?cash|stock))",
            # Deal mechanics
            r"\ball[-\s]cash\s+(?:transaction|acquisition|deal|merger)\b",
            r"\bcombine\s+(?:in\s+)?(?:a\s+)?merger\s+of\s+equals\b",
            r"\bmerger\s+of\s+equals\b",
            r"\bstock[-\s]for[-\s]stock\s+(?:transaction|merger|exchange)\b",
            # Share purchase agreement (common for private-company acquisitions)
            r"\bshare\s+purchase\s+agreement\b.{0,80}\bacqui",
            # Takeover offer language (European deals on GNW)
            r"\bvoluntary\s+takeover\s+offer\b",
            r"\btakeover\s+offer\s+to\s+(?:the\s+)?shareholders\b",
            # NOTE: no "Completes acquisition" — dropped intentionally.
            # NOTE: no "LOI" / "letter of intent" — dropped intentionally.
        ],
    },
    {
        "name": "TAKEOVER_RUMOR",
        "emoji": "🔥",
        "urgency": "CRITICAL",
        "label": "TAKEOVER RUMOR / SCOOP",
        "patterns": [
            r"\bin\s+(?:advanced\s+|early\s+)?(?:talks|discussions)\s+to\s+(?:acquire|buy|merge)\b",
            r"\bnear(?:ing)?\s+(?:a\s+)?deal\s+to\s+(?:acquire|buy)\b",
            r"\bexplor(?:ing|es)\s+(?:a\s+)?(?:potential\s+)?(?:acquisition|takeover|sale)\b.{0,80}\b(?:report|reportedly|sources)",
            r"\b(?:report|reportedly|sources?\s+say|according\s+to\s+sources?)\b.{0,80}\b(?:acqui|takeover|buyout)\b",
            r"\b(?:Reuters|Bloomberg|WSJ|Wall\s+Street\s+Journal|FT|Financial\s+Times)\b.{0,120}\b(?:acqui|takeover|merger|buyout)\b",
            r"\bweigh(?:ing|s)\s+(?:a\s+)?(?:sale|acquisition|takeover|bid)\b",
            r"\bconsider(?:ing|s)\s+(?:a\s+)?(?:sale|acquisition|takeover|bid)\b",
        ],
    },

    # ---- HIGH ----
    {
        "name": "ACTIVIST_13D",
        "emoji": "📊",
        "urgency": "HIGH",
        "label": "ACTIVIST 13D/13G FILING",
        "patterns": [
            r"\bfiles?\s+(?:a\s+)?(?:Schedule\s+)?13[DG]\b",
            r"\b(?:Schedule\s+)?13D\s+filing\b",
            r"\bactivist\s+(?:investor\s+)?(?:stake|position)\b",
            r"\bdiscloses?\s+(?:a\s+)?\d+(?:\.\d+)?%\s+(?:stake|position)\b",
        ],
    },
    {
        "name": "STRATEGIC_REVIEW",
        "emoji": "🔍",
        "urgency": "HIGH",
        "label": "STRATEGIC REVIEW / EXPLORING ALTERNATIVES",
        "patterns": [
            r"\bexploring\s+strategic\s+alternatives\b",
            r"\breview(?:ing)?\s+strategic\s+alternatives\b",
            r"\breview\s+of\s+strategic\s+alternatives\b",
            r"\bstrategic\s+review\b",
            r"\bconsider(?:s|ing)\s+strategic\s+alternatives\b",
            r"\bannounces?\s+(?:a\s+)?(?:formal\s+)?(?:review\s+of\s+)?strategic\s+alternatives\b",
            r"\bengag(?:es|ed)\s+(?:a\s+)?(?:financial\s+)?advisors?\s+to\s+explore\b",
            r"\bexploring\s+(?:a\s+)?(?:potential\s+)?sale\s+of\s+the\s+company\b",
            r"\bput(?:s|ting)\s+itself\s+up\s+for\s+sale\b",
        ],
    },

    # ---- STANDARD ----
    {
        "name": "SPAC_COMBINATION",
        "emoji": "🎫",
        "urgency": "STANDARD",
        "label": "SPAC BUSINESS COMBINATION",
        "patterns": [
            r"\bdefinitive\s+(?:business\s+)?combination\s+agreement\b.{0,100}\bSPAC\b",
            r"\bSPAC\b.{0,60}\bdefinitive\s+(?:business\s+)?combination\b",
            r"\bbusiness\s+combination\s+agreement\b.{0,100}\b(?:SPAC|special\s+purpose\s+acquisition)\b",
            r"\bto\s+go\s+public\s+via\s+(?:a\s+)?(?:merger\s+with\s+)?SPAC\b",
            r"\bde[\s-]?SPAC\s+(?:transaction|merger)\b",
        ],
    },

    # EDGAR-filing-type placeholders
    {
        "name": "EDGAR_SC13D",
        "emoji": "📊",
        "urgency": "HIGH",
        "label": "SEC SCHEDULE 13D FILING",
        "patterns": [],
    },
    {
        "name": "EDGAR_SC13G",
        "emoji": "📈",
        "urgency": "HIGH",
        "label": "SEC SCHEDULE 13G FILING",
        "patterns": [],
    },
    {
        "name": "EDGAR_TENDER",
        "emoji": "📣",
        "urgency": "CRITICAL",
        "label": "SEC TENDER OFFER FILING (SC TO-T)",
        "patterns": [],
    },
    {
        "name": "EDGAR_MERGER_PROXY",
        "emoji": "📜",
        "urgency": "HIGH",
        "label": "SEC MERGER PROXY (DEFM14A)",
        "patterns": [],
    },
]

for cat in CATEGORIES:
    cat["_re"] = re.compile("|".join(cat["patterns"]), re.IGNORECASE) if cat["patterns"] else None

URGENCY_BANNER = {
    "CRITICAL": "🚨🚨🚨 CRITICAL",
    "HIGH": "⚠️ HIGH",
    "STANDARD": "📋 STANDARD",
}

# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

WIRE_FEEDS = [
    # GlobeNewswire subject codes VERIFIED April 2026
    ("GlobeNewswire-MA",
     "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions"),
    ("GlobeNewswire-Financings",
     "https://www.globenewswire.com/RssFeed/subjectcode/28-Financings/feedTitle/GlobeNewswire%20-%20Financings"),
    ("GlobeNewswire-Business",
     "https://www.globenewswire.com/RssFeed/subjectcode/18-Business/feedTitle/GlobeNewswire%20-%20Business"),
    ("GlobeNewswire-PublicCos",
     "https://www.globenewswire.com/RssFeed/orgclass/1-Public%20Companies/feedTitle/GlobeNewswire%20-%20Public%20Companies"),
    ("PRNewswire-Financial",
     "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"),
    ("PRNewswire-AllNews",
     "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("Yahoo-Finance-Headlines",
     "https://finance.yahoo.com/news/rssindex"),
]

EDGAR_FEEDS = [
    ("EDGAR-8K",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
     "8-K"),
    ("EDGAR-SC13D",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13D&company=&dateb=&owner=include&count=40&output=atom",
     "SC 13D"),
    ("EDGAR-SC13G",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13G&company=&dateb=&owner=include&count=40&output=atom",
     "SC 13G"),
    ("EDGAR-SC-TO-T",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+TO-T&company=&dateb=&owner=include&count=40&output=atom",
     "SC TO-T"),
    ("EDGAR-DEFM14A",
     "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=DEFM14A&company=&dateb=&owner=include&count=40&output=atom",
     "DEFM14A"),
]

PRESS_FEEDS = [
    ("NYT-Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("MarketWatch-TopStories", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC-Business", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
    ("SeekingAlpha-Currents", "https://seekingalpha.com/market_currents.xml"),
]

TICKER_RE = re.compile(
    r"\((?:NASDAQ|NYSE|NYSE\s+American|NYSEAmerican|AMEX|OTCQB|OTCQX|OTC|Nasdaq|TSX|TSXV|LSE)\s*:?\s*([A-Z]{1,5})\)",
    re.IGNORECASE,
)

# Logging is configured by the platform (JSON to stdout -> journald).

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class DealFacts:
    """Enriched facts extracted from PR text."""
    offer_price: Optional[float] = None
    offer_price_currency: Optional[str] = None
    deal_structure: Optional[str] = None       # "cash", "stock", "cash-and-stock"
    premium_pct: Optional[float] = None        # Only if stated in PR; not computed
    total_deal_size: Optional[str] = None      # "$43B", "$850M"


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
    facts: DealFacts = field(default_factory=DealFacts)

    def fingerprint(self) -> str:
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
            source TEXT, category TEXT, title TEXT, link TEXT, seen_at TEXT
        )"""
    )
    conn.commit()
    return conn


def is_seen(conn, fp: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE fp = ?", (fp,)).fetchone() is not None


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


def clean_text(raw: str, max_len: int = 400) -> str:
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
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
    if getattr(entry, "updated_parsed", None):
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enrichment — extract deal facts from PR text
# ---------------------------------------------------------------------------

_OFFER_PRICE_RE = re.compile(
    r"(?P<currency>US\$|C\$|A\$|£|€|\$)\s*(?P<amount>\d+(?:\.\d+)?)\s*(?:per\s+share|/share|a\s+share)\b",
    re.IGNORECASE,
)

_STRUCTURE_RES = [
    ("cash-and-stock", re.compile(r"\bcash[\s-]+and[\s-]+stock\b", re.I)),
    ("stock",          re.compile(r"\bstock[\s-]for[\s-]stock\b|\ball[\s-]stock\s+(?:deal|transaction|merger)\b", re.I)),
    ("cash",           re.compile(r"\ball[\s-]cash\s+(?:deal|transaction|acquisition|merger|offer)\b|\bin\s+cash\b", re.I)),
]

_PREMIUM_RE = re.compile(
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s+premium\b",
    re.IGNORECASE,
)

_DEAL_SIZE_RE = re.compile(
    r"\$\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>billion|million|trillion|B|M|T)\b",
    re.IGNORECASE,
)


def _currency_code(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("US$") or s == "$":
        return "USD"
    if s.startswith("C$"):
        return "CAD"
    if s.startswith("A$"):
        return "AUD"
    if s == "£":
        return "GBP"
    if s == "€":
        return "EUR"
    return s


def extract_facts(text: str) -> DealFacts:
    """Extract offer price, deal structure, stated premium, deal size."""
    facts = DealFacts()
    if not text:
        return facts

    m = _OFFER_PRICE_RE.search(text)
    if m:
        try:
            facts.offer_price = float(m.group("amount"))
            facts.offer_price_currency = _currency_code(m.group("currency"))
        except ValueError:
            pass

    for label, rx in _STRUCTURE_RES:
        if rx.search(text):
            facts.deal_structure = label
            break

    pm = _PREMIUM_RE.search(text)
    if pm:
        try:
            pct = float(pm.group("pct"))
            if 0 < pct < 500:
                facts.premium_pct = pct
        except ValueError:
            pass

    sm = _DEAL_SIZE_RE.search(text)
    if sm:
        amount = sm.group("amount")
        unit = sm.group("unit").upper()
        if unit in ("BILLION", "B"):
            facts.total_deal_size = f"${amount}B"
        elif unit in ("MILLION", "M"):
            facts.total_deal_size = f"${amount}M"
        elif unit in ("TRILLION", "T"):
            facts.total_deal_size = f"${amount}T"

    return facts


def fetch_pr_body(url: str) -> Optional[str]:
    """
    Fetch and clean PR body. Hard timeout + size cap. Never raises.
    """
    if not url:
        return None
    try:
        resp = requests.get(
            url, headers=HTTP_HEADERS_DEFAULT,
            timeout=PR_FETCH_TIMEOUT_SECONDS, stream=True,
        )
        if resp.status_code != 200:
            return None
        content = b""
        for chunk in resp.iter_content(chunk_size=16384):
            content += chunk
            if len(content) > PR_FETCH_MAX_BYTES:
                break
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            return None
        no_script = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.I)
        no_style = re.sub(r"<style[^>]*>.*?</style>", " ", no_script, flags=re.DOTALL | re.I)
        no_tags = re.sub(r"<[^>]+>", " ", no_style)
        decoded = html.unescape(no_tags)
        return re.sub(r"\s+", " ", decoded)[:50_000]
    except Exception as e:
        log.debug("fetch_pr_body failed for %s: %s", url[:80], e)
        return None


def enrich_hit(hit: Hit) -> None:
    """Hybrid enrichment. Never raises."""
    try:
        facts = extract_facts(hit.summary or "")
    except Exception:
        facts = DealFacts()

    # If summary gave us the offer price, good enough
    if facts.offer_price is not None:
        hit.facts = facts
        return

    # Only fetch body for deal-relevant categories, to save bandwidth
    enrich_categories = {
        "UNSOLICITED_PROPOSAL", "SIGNED_DEAL", "TENDER_OFFER",
        "TAKEOVER_RUMOR", "COMPETING_BID", "PE_TAKEPRIVATE",
    }
    if hit.category not in enrich_categories or not hit.link:
        hit.facts = facts
        return

    body = fetch_pr_body(hit.link)
    if body:
        try:
            body_facts = extract_facts(body)
            if facts.offer_price is None and body_facts.offer_price is not None:
                facts.offer_price = body_facts.offer_price
                facts.offer_price_currency = body_facts.offer_price_currency
            if facts.deal_structure is None:
                facts.deal_structure = body_facts.deal_structure
            if facts.premium_pct is None:
                facts.premium_pct = body_facts.premium_pct
            if facts.total_deal_size is None:
                facts.total_deal_size = body_facts.total_deal_size
        except Exception as e:
            log.debug("enrich parse failed for %s: %s", hit.link[:80], e)

    hit.facts = facts


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_feed(name: str, url: str, headers: Optional[Dict] = None, limit: int = 50) -> List[Hit]:
    hits: List[Hit] = []
    try:
        resp = requests.get(url, headers=headers or HTTP_HEADERS_DEFAULT, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Failed to fetch %s: %s", name, e)
        return []

    for entry in getattr(feed, "entries", [])[:limit]:
        title = clean_text(getattr(entry, "title", "") or "", max_len=300)
        link = (getattr(entry, "link", "") or "").strip()
        summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        published = _fmt_published(entry)

        # Skip UK Rule 8.3/8.5 disclosure noise
        if re.search(r"\bForm\s+8\.(?:3|5)\b", title):
            continue

        # Skip non-binding letters of intent — user specced these as dropped.
        # LOIs are overwhelmingly small-cap mining noise and rarely trade well.
        if re.search(r"\b(?:letter\s+of\s+intent|non[-\s]?binding\s+LOI|\bLOI\s+to\s+acquire)\b", title, re.I):
            continue

        text_blob = f"{title}\n{summary_raw}"
        category, phrase = classify(text_blob)
        if not category:
            continue

        hits.append(Hit(
            source=name, category=category, title=title, link=link,
            summary=clean_text(summary_raw), published=published,
            ticker=extract_ticker(text_blob), matched_phrase=phrase,
        ))

    return hits


def fetch_edgar(name: str, url: str, form_type: str) -> List[Hit]:
    hits: List[Hit] = []
    try:
        headers = {"User-Agent": EDGAR_USER_AGENT, "Accept": "application/atom+xml"}
        resp = requests.get(url, headers=headers, timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("EDGAR fetch failed (%s): %s", name, e)
        return []

    TYPE_TO_CATEGORY = {
        "SC 13D": ("EDGAR_SC13D", "SC 13D"),
        "SC 13G": ("EDGAR_SC13G", "SC 13G"),
        "SC TO-T": ("EDGAR_TENDER", "SC TO-T"),
        "DEFM14A": ("EDGAR_MERGER_PROXY", "DEFM14A"),
    }

    for entry in getattr(feed, "entries", [])[:60]:
        title = clean_text(getattr(entry, "title", "") or "", max_len=300)
        link = (getattr(entry, "link", "") or "").strip()
        summary_raw = getattr(entry, "summary", "") or ""
        published = _fmt_published(entry)

        if form_type in TYPE_TO_CATEGORY:
            category, phrase = TYPE_TO_CATEGORY[form_type]
        else:
            text_blob = f"{title}\n{summary_raw}"
            category, phrase = classify(text_blob)
            if not category:
                continue

        hits.append(Hit(
            source=name, category=category, title=title, link=link,
            summary=clean_text(summary_raw), published=published,
            ticker=extract_ticker(title) or extract_ticker(summary_raw),
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
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


def _format_facts_block(facts: DealFacts) -> str:
    """Multi-line HTML block of extracted facts, or '' if nothing found."""
    lines = []
    if facts.offer_price is not None:
        currency = facts.offer_price_currency or "USD"
        symbol = {"USD": "$", "CAD": "C$", "AUD": "A$", "EUR": "€", "GBP": "£"}.get(currency, "")
        lines.append(f"💵 <b>Offer:</b> {symbol}{facts.offer_price:.2f}/share")
    if facts.premium_pct is not None:
        lines.append(f"📈 <b>Premium (stated):</b> {facts.premium_pct:.1f}%")
    if facts.deal_structure:
        lines.append(f"💱 <b>Structure:</b> {facts.deal_structure}")
    if facts.total_deal_size:
        lines.append(f"💼 <b>Deal size:</b> {facts.total_deal_size}")
    return "\n".join(lines)


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

    facts_block = _format_facts_block(hit.facts)
    facts_section = f"{facts_block}\n\n" if facts_block else ""

    return (
        f"{banner}\n"
        f"{emoji} <b>{html.escape(label)}</b>\n"
        f"📰 Source: {html.escape(hit.source)}\n"
        f"🕒 {html.escape(hit.published)}\n\n"
        f"{ticker_line}"
        f"{phrase_line}\n"
        f"<b>{html.escape(hit.title)}</b>\n\n"
        f"{facts_section}"
        f"{html.escape(hit.summary)}\n\n"
        f"🔗 {html.escape(hit.link)}"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_URGENCY_ORDER = {"CRITICAL": 0, "HIGH": 1, "STANDARD": 2}


def _process_hits(conn, hits: List[Hit]) -> int:
    SVC.metrics.inc("alert_items_seen_total", len(hits))
    hits_sorted = sorted(
        hits,
        key=lambda h: _URGENCY_ORDER.get(category_meta(h.category).get("urgency", "STANDARD"), 99),
    )
    sent = 0
    for hit in hits_sorted:
        fp = hit.fingerprint()
        if is_seen(conn, fp):
            continue
        try:
            enrich_hit(hit)
        except Exception as e:
            log.debug("Enrichment failed for %s: %s", hit.title[:60], e)
        mark_seen(conn, hit)
        if send_telegram(format_alert(hit)):
            sent += 1
            log.info("Alert: [%s] %s | %s | offer=%s premium=%s",
                     category_meta(hit.category).get("urgency"),
                     hit.category, hit.title[:70],
                     hit.facts.offer_price, hit.facts.premium_pct)
        else:
            log.warning("Alert failed: %s | %s", hit.category, hit.title[:90])
    return sent


def main():
    global SVC, DB_PATH, EDGAR_USER_AGENT
    global WIRE_POLL_INTERVAL_SECONDS, EDGAR_POLL_INTERVAL_SECONDS, PRESS_POLL_INTERVAL_SECONDS

    SVC = Service.from_env()
    DB_PATH = SVC.state_file("ma_seen.db")

    # SEC blocks unidentified clients; the UA is a credential-ish value and
    # therefore lives in the secret store, referenced by name in the spec.
    EDGAR_USER_AGENT = SVC.cfg.secret("edgar_user_agent")
    HTTP_HEADERS_DEFAULT["User-Agent"] = EDGAR_USER_AGENT

    # Primary cadence comes from polling.interval_sec. The two slower tiers
    # are service-specific and ride the spec.polling extension point, so all
    # three cadences are visible in the spec rather than only in this file.
    WIRE_POLL_INTERVAL_SECONDS = SVC.cfg.poll_interval_sec
    EDGAR_POLL_INTERVAL_SECONDS = int(SVC.cfg.polling("edgar_interval_sec", 300))
    PRESS_POLL_INTERVAL_SECONDS = int(SVC.cfg.polling("press_interval_sec", 600))

    with SVC:
        conn = init_db()

        send_telegram(
            "✅ <b>M&amp;A Catalyst Bot v2 started</b>\n\n"
            "Tracking with fact enrichment:\n"
            "🚨 CRITICAL: Signed deals, Unsolicited proposals, Tender offers, Rumors/scoops\n"
            "⚠️ HIGH: Competing bids, Terminations, 13D/G, PE take-privates, Strategic reviews\n"
            "📋 STANDARD: SPAC combinations\n\n"
        )

        # Cadence bookkeeping for the slower tiers.
        last_edgar_poll = 0.0
        last_press_poll = 0.0

        while SVC.running():
            with SVC.poll_cycle():
                now = time.time()

                wire_sent = 0
                for src_name, src_url in WIRE_FEEDS:
                    try:
                        wire_sent += _process_hits(conn, fetch_feed(src_name, src_url))
                    except Exception as e:
                        log.exception("Error processing wire %s: %s", src_name, e)
                if wire_sent:
                    log.info("Wire alerts sent: %d", wire_sent)

                if now - last_edgar_poll >= EDGAR_POLL_INTERVAL_SECONDS:
                    edgar_sent = 0
                    for name, url, ftype in EDGAR_FEEDS:
                        try:
                            edgar_sent += _process_hits(conn, fetch_edgar(name, url, ftype))
                        except Exception as e:
                            log.exception("Error processing EDGAR %s: %s", name, e)
                    if edgar_sent:
                        log.info("EDGAR alerts sent: %d", edgar_sent)
                    last_edgar_poll = now

                if now - last_press_poll >= PRESS_POLL_INTERVAL_SECONDS:
                    press_sent = 0
                    for src_name, src_url in PRESS_FEEDS:
                        try:
                            press_sent += _process_hits(conn, fetch_feed(src_name, src_url))
                        except Exception as e:
                            log.exception("Error processing press %s: %s", src_name, e)
                    if press_sent:
                        log.info("Press alerts sent: %d", press_sent)
                    last_press_poll = now

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
            send_telegram(f"❌ <b>M&amp;A Bot crashed</b>\n<code>{html.escape(str(e))}</code>")
        except Exception:
            pass
        raise

