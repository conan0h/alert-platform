"""
form4_backfill.py
=================
Downloads Form 4 filings from EDGAR for the last N years, parses each,
extracts individual insider transactions, and writes them to SQLite.

Strategy:
  1. Walk EDGAR quarterly form.idx files (cheap, one file per quarter lists ALL filings).
  2. Filter to Form 4 / Form 4/A only.
  3. For each filing, fetch its primary XML document and parse transactions.
  4. Batch-insert to SQLite (never hold more than BATCH_SIZE in memory).

Fully resumable: records filings as 'processed' only after their transactions
are committed. Crash halfway through, re-run, picks up from the last processed.

Run:
    python form4_backfill.py --years 2
Options:
    --years N          Lookback window (default 2)
    --start YYYY-QN    Start at specific quarter (e.g. 2024-Q1)
    --only-parse       Skip index download, only process known-unprocessed filings
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from form4_common import (
    bootstrap_job,
    edgar_get,
    init_db,
    make_logger,
)

log = make_logger("form4_backfill", "form4_backfill.log")

BATCH_SIZE = 200                 # commit to SQLite every N filings
CHECKPOINT_EVERY = 1000          # log progress every N filings


# ---------------------------------------------------------------------------
# Walk EDGAR quarterly index
# ---------------------------------------------------------------------------
def quarters_since(years: int, start_override: str | None = None) -> list[tuple[int, int]]:
    """Generate list of (year, quarter) tuples to crawl, newest first."""
    out: list[tuple[int, int]] = []
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=365 * years)

    if start_override:
        m = re.match(r"(\d{4})-Q([1-4])", start_override)
        if not m:
            raise ValueError(f"bad --start: {start_override}")
        sy, sq = int(m.group(1)), int(m.group(2))
    else:
        sy, sq = start.year, (start.month - 1) // 3 + 1

    y, q = now.year, (now.month - 1) // 3 + 1
    while (y, q) >= (sy, sq):
        out.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return out


def iter_form4_index(year: int, quarter: int) -> Iterator[tuple[str, str, str, str]]:
    """
    Stream Form 4 entries from an EDGAR form.idx for a given quarter.
    Yields (cik, company, date_filed, filename) per row.

    form.idx is a fixed-width text file starting with a header we skip.
    Form types: we take "4" and "4/A" only.
    """
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    log.info("Fetching index %s", url)
    resp = edgar_get(url, timeout=60)
    text = resp.text

    lines = text.splitlines()
    # Find the divider line (dashes) that precedes data
    data_start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("---"):
            data_start = i + 1
            break
    # Columns are space-delimited but form.idx uses fixed alignment.
    # Form Type column is always first 12 chars of each row.
    for ln in lines[data_start:]:
        if not ln.strip():
            continue
        form_type = ln[:12].strip()
        if form_type not in ("4", "4/A"):
            continue
        company = ln[12:74].strip()
        cik = ln[74:86].strip()
        date_filed = ln[86:98].strip()
        filename = ln[98:].strip()
        yield cik, company, date_filed, filename


# ---------------------------------------------------------------------------
# Parse Form 4 XML
# ---------------------------------------------------------------------------
def _txt(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    v = el.findtext("value")
    if v is None:
        v = el.text
    return v.strip() if isinstance(v, str) else None


def parse_form4_xml(xml_bytes: bytes) -> dict | None:
    """
    Parse a Form 4 XML document into a structured dict.
    Returns None if the doc isn't a parseable Form 4.

    Form 4 schema has:
      - issuer (company): issuerCik, issuerTradingSymbol
      - reportingOwner (insider): reportingOwnerId/{rptOwnerCik, rptOwnerName}
                                  reportingOwnerRelationship/{isDirector, isOfficer, officerTitle, ...}
      - nonDerivativeTable/nonDerivativeTransaction (direct share trades)
      - derivativeTable/derivativeTransaction (options etc — skip for now)
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    issuer = root.find("issuer")
    if issuer is None:
        return None
    issuer_cik = _txt(issuer.find("issuerCik"))
    ticker = _txt(issuer.find("issuerTradingSymbol"))

    # May be multiple reportingOwners for joint filings — take first.
    owner = root.find("reportingOwner")
    if owner is None:
        return None
    owner_id = owner.find("reportingOwnerId")
    insider_cik = _txt(owner_id.find("rptOwnerCik")) if owner_id is not None else None
    insider_name = _txt(owner_id.find("rptOwnerName")) if owner_id is not None else None
    if not insider_cik or not insider_name:
        return None

    rel = owner.find("reportingOwnerRelationship")
    relationship = _build_relationship(rel) if rel is not None else None

    transactions: list[dict] = []
    ndt = root.find("nonDerivativeTable")
    if ndt is not None:
        for tx in ndt.findall("nonDerivativeTransaction"):
            parsed = _parse_non_derivative_tx(tx)
            if parsed:
                transactions.append(parsed)

    return {
        "issuer_cik": issuer_cik,
        "ticker": (ticker or "").upper() or None,
        "insider_cik": insider_cik,
        "insider_name": insider_name,
        "relationship": relationship,
        "transactions": transactions,
    }


def _build_relationship(rel: ET.Element) -> str:
    parts = []
    if _txt(rel.find("isDirector")) in ("1", "true", "True"):
        parts.append("Director")
    if _txt(rel.find("isOfficer")) in ("1", "true", "True"):
        title = _txt(rel.find("officerTitle"))
        parts.append(title if title else "Officer")
    if _txt(rel.find("isTenPercentOwner")) in ("1", "true", "True"):
        parts.append("10% Owner")
    if _txt(rel.find("isOther")) in ("1", "true", "True"):
        other = _txt(rel.find("otherText"))
        parts.append(other if other else "Other")
    return ", ".join(parts) if parts else "Insider"


def _parse_non_derivative_tx(tx: ET.Element) -> dict | None:
    date = _txt(tx.find("transactionDate"))
    tx_coding = tx.find("transactionCoding")
    if tx_coding is None:
        return None
    code = _txt(tx_coding.find("transactionCode"))
    if not date or not code:
        return None

    amounts = tx.find("transactionAmounts")
    if amounts is None:
        return None
    try:
        shares = float(_txt(amounts.find("transactionShares")) or 0)
        price = float(_txt(amounts.find("transactionPricePerShare")) or 0)
    except (TypeError, ValueError):
        return None

    if shares <= 0:
        return None

    # 10b5-1 checkbox (added April 2023). Pre-2023 filings won't have this;
    # treat missing as 0 (not-10b5-1) since that's the conservative assumption.
    is_10b5_1 = 0
    # The flag may appear in several places depending on filer software:
    for tag in ("isRule10b5-1Transaction", "rule10b5-1Transaction"):
        el = tx_coding.find(tag)
        if el is not None and _txt(el) in ("1", "true", "True"):
            is_10b5_1 = 1
            break

    return {
        "trade_date": date,
        "tx_code": code,
        "shares": shares,
        "price": price,
        "usd_value": shares * price,
        "is_10b5_1": is_10b5_1,
    }


# ---------------------------------------------------------------------------
# Filing fetch
# ---------------------------------------------------------------------------
_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")


def index_row_to_xml_urls(cik: str, filename: str) -> list[str]:
    """
    Given an index row, try to locate the primary Form 4 XML document.
    form.idx gives us a .txt path; the primary XML lives alongside it
    in the filing's archive folder.

    Strategy: hit the filing's index.json and pick the .xml file whose
    name contains 'primary_doc' OR the first .xml that doesn't contain 'form'.
    """
    m = _ACCESSION_RE.search(filename)
    if not m:
        return []
    accession = m.group(1)
    accession_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}"
    # index.json lists files in the filing
    return [f"{base}/index.json", f"{base}/{accession}-index.htm"]


def fetch_primary_xml(cik: str, filename: str) -> tuple[str, bytes] | None:
    """
    Return (accession, xml_bytes) or None. Tries the index.json route first.
    """
    m = _ACCESSION_RE.search(filename)
    if not m:
        return None
    accession = m.group(1)
    accession_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}"

    # Get file listing
    try:
        idx = edgar_get(f"{base}/index.json", timeout=30).json()
    except Exception as e:
        log.debug("index.json failed for %s: %s", accession, e)
        return None

    items = idx.get("directory", {}).get("item", [])
    xml_name = None
    # Prefer primary_doc.xml if present, else first .xml that isn't a financial statement
    for it in items:
        name = it.get("name", "")
        if name == "primary_doc.xml":
            xml_name = name
            break
    if xml_name is None:
        for it in items:
            name = it.get("name", "")
            if name.lower().endswith(".xml") and "r" not in name.split(".")[0][-2:]:
                # Skip Rxxx.xml financial statement files
                xml_name = name
                break
    if xml_name is None:
        return None

    try:
        xml_bytes = edgar_get(f"{base}/{xml_name}", timeout=30).content
    except Exception as e:
        log.debug("xml fetch failed for %s: %s", accession, e)
        return None

    return accession, xml_bytes


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------
def _upsert_filing(conn: sqlite3.Connection, rows: list[tuple]):
    conn.executemany(
        "INSERT OR IGNORE INTO filings (accession, filed_at, cik, ticker, processed) VALUES (?, ?, ?, ?, 0)",
        rows,
    )


def _upsert_insider(conn: sqlite3.Connection, insider_cik: str, name: str):
    conn.execute(
        "INSERT OR IGNORE INTO insiders (insider_cik, name) VALUES (?, ?)",
        (insider_cik, name),
    )


def _insert_transactions(conn: sqlite3.Connection, accession: str, insider_cik: str,
                         ticker: str | None, relationship: str | None,
                         txs: list[dict]):
    if not txs:
        return
    rows = [
        (accession, insider_cik, ticker, t["trade_date"], t["tx_code"],
         t["shares"], t["price"], t["usd_value"], t["is_10b5_1"], relationship)
        for t in txs
    ]
    conn.executemany(
        """INSERT INTO transactions
           (accession, insider_cik, ticker, trade_date, tx_code, shares, price, usd_value, is_10b5_1, relationship)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _mark_processed(conn: sqlite3.Connection, accessions: list[str]):
    conn.executemany(
        "UPDATE filings SET processed = 1 WHERE accession = ?",
        [(a,) for a in accessions],
    )


def download_indices(conn: sqlite3.Connection, years: int, start_override: str | None):
    """Phase 1 — populate filings table from quarterly indices."""
    discovered = 0
    for (y, q) in quarters_since(years, start_override):
        try:
            rows = []
            for cik, _company, date_filed, filename in iter_form4_index(y, q):
                m = _ACCESSION_RE.search(filename)
                if not m:
                    continue
                accession = m.group(1)
                rows.append((accession, date_filed, cik, None))
                if len(rows) >= BATCH_SIZE:
                    _upsert_filing(conn, rows)
                    conn.commit()
                    discovered += len(rows)
                    rows = []
            if rows:
                _upsert_filing(conn, rows)
                conn.commit()
                discovered += len(rows)
            log.info("Indexed %d-Q%d (running total discovered: %d)", y, q, discovered)
        except Exception as e:
            log.exception("Error indexing %d-Q%d: %s", y, q, e)
    log.info("Phase 1 complete: %d filings known to DB", discovered)


def process_filings(conn: sqlite3.Connection):
    """Phase 2 — fetch XML + parse transactions for every unprocessed filing."""
    cur = conn.execute("SELECT COUNT(*) FROM filings WHERE processed = 0")
    total_pending = cur.fetchone()[0]
    log.info("Phase 2: %d filings to process", total_pending)

    processed_n = 0
    batch_processed: list[str] = []
    t0 = time.time()

    while True:
        rows = conn.execute(
            "SELECT accession, cik FROM filings WHERE processed = 0 LIMIT ?",
            (BATCH_SIZE,),
        ).fetchall()
        if not rows:
            break

        for accession, cik in rows:
            filename = f"edgar/data/{int(cik)}/{accession}.txt"  # nominal; we don't use it directly
            result = fetch_primary_xml(cik, filename + accession)
            if result is None:
                batch_processed.append(accession)
                continue
            _, xml_bytes = result
            parsed = parse_form4_xml(xml_bytes)
            if parsed is None:
                batch_processed.append(accession)
                continue

            # Upsert insider
            _upsert_insider(conn, parsed["insider_cik"], parsed["insider_name"])

            # Update ticker if we got one
            if parsed["ticker"]:
                conn.execute(
                    "UPDATE filings SET ticker = ? WHERE accession = ?",
                    (parsed["ticker"], accession),
                )

            # Insert transactions
            _insert_transactions(
                conn, accession, parsed["insider_cik"], parsed["ticker"],
                parsed["relationship"], parsed["transactions"],
            )

            batch_processed.append(accession)

        _mark_processed(conn, batch_processed)
        conn.commit()
        processed_n += len(batch_processed)
        batch_processed = []

        if processed_n % CHECKPOINT_EVERY < BATCH_SIZE:
            elapsed = time.time() - t0
            rate = processed_n / elapsed if elapsed > 0 else 0
            remaining = (total_pending - processed_n) / rate if rate > 0 else 0
            log.info("Processed %d/%d (%.1f/s, ETA %.1f min)",
                     processed_n, total_pending, rate, remaining / 60)

    log.info("Phase 2 complete: processed %d filings in %.1f min",
             processed_n, (time.time() - t0) / 60)


def main():
    bootstrap_job("form4-backfill")
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--start", type=str, default=None, help="Start at YYYY-QN")
    ap.add_argument("--only-parse", action="store_true",
                    help="Skip index download; just process already-discovered filings")
    args = ap.parse_args()

    conn = init_db()

    if not args.only_parse:
        log.info("=== Phase 1: download quarterly indices ===")
        download_indices(conn, args.years, args.start)

    log.info("=== Phase 2: fetch XML + extract transactions ===")
    process_filings(conn)

    log.info("Backfill complete. Next step: run form4_scorer.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Interrupted — state is saved, resume with: python form4_backfill.py --only-parse")
    except Exception:
        log.exception("Fatal error")
        sys.exit(1)
