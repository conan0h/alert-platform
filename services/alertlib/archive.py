"""The alert archive: an append-only record of everything a bot emitted.

Until this existed, an alert was a Telegram message and a journald line and
nothing else. Neither is queryable: journald is rotated, capped at what the
`logs` verb can return in one 24 KB read, and holds the rendered message
rather than the fields that produced it. So the question CLAUDE.md §1 puts
first — is this signal any good — had no way to be answered, and the four
feeds the website is meant to present had nothing to present.

What is recorded, per alert: which service and which released ref produced
it, which upstream source it came from, the service's own dedup key, the
ticker if one was identified, the reason the filter fired, the message as
sent, a JSON payload of structured detail, and whether delivery succeeded.
The ref is in there because "did signal quality change after v0.3.0" is the
question this table exists to make answerable.

Two decisions worth stating, because both are the opposite of what the
surrounding code does:

**A separate database file.** Each service already owns a SQLite database
for dedup state. The archive deliberately does not share it. On 2026-09-21
`form4-insider` emitted the same alerts for hours because something held
the write lock on that database past its 30-second timeout
(docs/incidents/2026-09-21-form4-duplicate-alerts.md). Adding a second
writer to the file whose lock contention caused an incident would be
trading measurement for the thing being measured.

**A write failure is swallowed.** `record` never raises and never blocks a
send. The dedup path does the reverse — `form4_insider.process_filing`
refuses to send when it cannot record — and the asymmetry is deliberate: a
dedup write that does not persist causes duplicate alerts, which is harm,
while an archive write that does not persist causes a missing row, which is
only lost measurement. Losing a row is visible in
`alert_archive_write_failures_total` and in the log.

Nothing here deletes. Retention is a later decision made against real
volume rather than a guess; at the fleet's observed rate a row is a few
hundred bytes and alerts are tens per day, so the table is small for years.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .log import get_logger

log = get_logger("alertlib.archive")

# Bumped when the table changes shape. Read back from PRAGMA user_version so
# a reader — the `alerts` verb, later the website — can tell an old file from
# a new one instead of discovering it column by column.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    service       TEXT NOT NULL,
    ref           TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    source        TEXT NOT NULL,
    dedup_key     TEXT NOT NULL,
    ticker        TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL,
    payload       TEXT NOT NULL DEFAULT '{}',
    delivery      TEXT NOT NULL,
    delivered_at  TEXT
);
CREATE INDEX IF NOT EXISTS alerts_created_at ON alerts (created_at);
CREATE INDEX IF NOT EXISTS alerts_ticker     ON alerts (ticker);
CREATE INDEX IF NOT EXISTS alerts_dedup      ON alerts (service, dedup_key);
"""

# Delivery outcomes. A row is written as PENDING before the send and settled
# afterwards, so an alert whose process died mid-send leaves evidence that it
# was attempted rather than no evidence at all.
PENDING = "pending"
SENT = "sent"
FAILED = "failed"


@dataclass(frozen=True)
class Alert:
    """One alert, as the archive stores it.

    `body` is the message exactly as sent, so the archive can be replayed
    into a web page without re-deriving anything from the payload. `payload`
    is whatever structured detail the service already has — the fields a
    later analysis will want to group by, which the rendered message has
    already flattened into prose.
    """

    source: str
    dedup_key: str
    title: str
    body: str
    ticker: str = ""
    reason: str = ""
    payload: dict = field(default_factory=dict)


class AlertArchive:
    """Append-only alert storage for one service."""

    def __init__(self, path: str, service: str, ref: str = "unknown",
                 metrics=None, timeout: float = 5.0) -> None:
        self.path = path
        self.service = service
        self.ref = ref
        self.metrics = metrics
        self.timeout = timeout
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if metrics is not None:
            metrics.declare_counter(
                "alert_archive_records_total",
                "Alerts written to the archive.")
            metrics.declare_counter(
                "alert_archive_write_failures_total",
                "Archive writes that did not persist. An alert was still sent.")

    # -- connection -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Open on first use and keep it. A service that never alerts never
        creates the file, which keeps an empty database out of the backup and
        out of the way of anyone reading the state directory."""
        if self._conn is None:
            conn = sqlite3.connect(self.path, timeout=self.timeout,
                                   check_same_thread=False)
            # Set once here rather than inside the read methods: mutating a
            # shared connection's row factory per call is a race waiting for
            # the first concurrent reader.
            conn.row_factory = sqlite3.Row
            # WAL so a reader — the `alerts` verb, run while the service is
            # polling — never blocks a write.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # -- writes -----------------------------------------------------------
    def record(self, alert: Alert) -> int | None:
        """Write the alert as PENDING. Returns its row id, or None on failure.

        Callers pass the returned id to `record_delivery`. A None return is
        not an error the caller must handle: `record_delivery(None, ...)` is a
        no-op, so the send path reads the same whether or not the archive is
        working.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            payload = json.dumps(alert.payload, default=str, sort_keys=True)
        except (TypeError, ValueError) as exc:
            # A payload that will not serialise must not cost the row that
            # holds everything else about the alert.
            log.warning("archive payload not serialisable; storing empty",
                        extra={"dedup_key": alert.dedup_key, "error": str(exc)})
            payload = "{}"

        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute(
                    "INSERT INTO alerts (service, ref, created_at, source, "
                    "dedup_key, ticker, title, reason, body, payload, delivery) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.service, self.ref, now, alert.source, alert.dedup_key,
                     alert.ticker, alert.title, alert.reason, alert.body,
                     payload, PENDING),
                )
                conn.commit()
                row_id = cur.lastrowid
            if self.metrics:
                self.metrics.inc("alert_archive_records_total")
            return row_id
        except (sqlite3.Error, OSError) as exc:
            self._write_failed("record", alert.dedup_key, exc)
            return None

    def record_delivery(self, row_id: int | None, delivered: bool) -> None:
        """Settle a row written by `record`. Silent no-op for a None id."""
        if row_id is None:
            return
        outcome = SENT if delivered else FAILED
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "UPDATE alerts SET delivery = ?, delivered_at = ? WHERE id = ?",
                    (outcome, now, row_id),
                )
                conn.commit()
        except (sqlite3.Error, OSError) as exc:
            self._write_failed("record_delivery", str(row_id), exc)

    def _write_failed(self, op: str, key: str, exc: Exception) -> None:
        # Deliberately not `raise`: see the module docstring. The counter is
        # what makes a silently unrecorded alert visible on the dashboard.
        if self.metrics:
            self.metrics.inc("alert_archive_write_failures_total")
        log.error("archive write failed; the alert was not affected",
                  extra={"op": op, "key": key, "error": str(exc)})

    # -- reads ------------------------------------------------------------
    def recent(self, limit: int = 50) -> list[dict]:
        """Newest alerts first. For tests, the console, and the `alerts` verb.

        Reads are allowed to raise. A caller asking for the archive wants to
        know that it could not be read; only the write path has to stay out
        of the way of the alerts themselves.
        """
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            entry["payload"] = json.loads(entry["payload"])
            out.append(entry)
        return out

    def count(self) -> int:
        with self._lock:
            conn = self._connect()
            return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
