"""Tests for the alert archive.

Two properties matter and pull in opposite directions:

* everything sent is recorded, with the fields a later analysis needs; and
* nothing the archive does can stop an alert going out.

Most of what follows is the second one — the failure paths — because that is
where an archive quietly becomes a liability rather than a record.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alertlib.archive import (  # noqa: E402
    FAILED,
    PENDING,
    SCHEMA_VERSION,
    SENT,
    Alert,
    AlertArchive,
)


class _Metrics:
    def __init__(self) -> None:
        self.counts: dict[str, float] = {}
        self.declared: dict[str, str] = {}

    def inc(self, name: str, amount: float = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + amount

    def declare_counter(self, name: str, help_text: str) -> None:
        self.declared[name] = help_text


def an_alert(**over) -> Alert:
    fields = {
        "source": "SEC EDGAR Form 4",
        "dedup_key": "0001193125-26-396718#S",
        "title": "DELL S $4,612,795 by Silver Lake Partners IV, L.P.",
        "body": "🔴 <b>INSIDER SELL</b> …",
        "ticker": "DELL",
        "reason": "large trade",
        "payload": {"tx_code": "S", "usd_value": 4_612_795.0},
    }
    fields.update(over)
    return Alert(**fields)


@pytest.fixture
def archive(tmp_path):
    return AlertArchive(
        path=str(tmp_path / "alerts.db"),
        service="form4-insider",
        ref="v0.3.0",
        metrics=_Metrics(),
    )


# -- the record itself ------------------------------------------------------

def test_a_delivered_alert_is_recorded_in_full(archive):
    row_id = archive.record(an_alert())
    archive.record_delivery(row_id, True)

    (row,) = archive.recent()
    assert row["service"] == "form4-insider"
    assert row["ref"] == "v0.3.0", "the ref is what makes per-release comparison possible"
    assert row["source"] == "SEC EDGAR Form 4"
    assert row["ticker"] == "DELL"
    assert row["reason"] == "large trade"
    assert row["body"] == "🔴 <b>INSIDER SELL</b> …"
    assert row["payload"] == {"tx_code": "S", "usd_value": 4_612_795.0}
    assert row["delivery"] == SENT
    assert row["delivered_at"]


def test_a_failed_delivery_is_recorded_as_failed_not_dropped(archive):
    archive.record_delivery(archive.record(an_alert()), False)
    (row,) = archive.recent()
    assert row["delivery"] == FAILED


def test_an_unsettled_row_stays_pending(archive):
    """A process killed between the send and the outcome leaves evidence that
    the alert was attempted, which is the reason the row is written first."""
    archive.record(an_alert())
    (row,) = archive.recent()
    assert row["delivery"] == PENDING
    assert row["delivered_at"] is None


def test_recent_returns_newest_first(archive):
    for i in range(3):
        archive.record(an_alert(dedup_key=f"key-{i}", title=f"alert {i}"))
    assert [r["title"] for r in archive.recent()] == ["alert 2", "alert 1", "alert 0"]
    assert archive.count() == 3


def test_the_schema_version_is_readable_without_inspecting_columns(archive, tmp_path):
    archive.record(an_alert())
    conn = sqlite3.connect(str(tmp_path / "alerts.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_duplicate_dedup_keys_are_both_kept(archive):
    """The archive records what happened, not what should have happened. Two
    rows with one key mean the service sent twice — which is exactly the
    condition the 2026-09-21 incident made invisible."""
    archive.record(an_alert())
    archive.record(an_alert())
    assert archive.count() == 2


def test_no_file_is_created_until_something_is_recorded(tmp_path):
    path = tmp_path / "alerts.db"
    AlertArchive(path=str(path), service="edgar-mna")
    assert not path.exists()


# -- the failure paths ------------------------------------------------------

def test_a_write_failure_returns_none_rather_than_raising(tmp_path):
    """The whole point: an archive that cannot write must not take the feed
    down with it. A directory where the file should be makes every open fail
    the way a permission problem or a full disk would."""
    blocked = tmp_path / "alerts.db"
    blocked.mkdir()
    metrics = _Metrics()
    archive = AlertArchive(path=str(blocked), service="edgar-mna", metrics=metrics)

    assert archive.record(an_alert()) is None
    assert metrics.counts["alert_archive_write_failures_total"] == 1
    assert metrics.counts.get("alert_archive_records_total") is None


def test_settling_a_row_that_was_never_written_is_a_no_op(archive):
    """So the send path reads identically whether or not the archive works."""
    archive.record_delivery(None, True)
    assert archive.count() == 0


def test_a_payload_that_will_not_serialise_costs_the_payload_not_the_row(archive):
    """`default=str` copes with unusual types, so what is left is the payload
    json cannot walk at all — a cycle."""
    cyclic: dict = {}
    cyclic["self"] = cyclic
    row_id = archive.record(an_alert(payload=cyclic))
    assert row_id is not None
    (row,) = archive.recent()
    assert row["payload"] == {}
    assert row["title"].startswith("DELL")


def test_a_payload_of_dates_and_decimals_is_stored_as_text(archive):
    """`default=str` rather than a failure: a datetime in a payload is
    ordinary, and losing the row over it would not be."""
    import datetime as dt

    archive.record(an_alert(payload={"published": dt.date(2026, 9, 21)}))
    (row,) = archive.recent()
    assert row["payload"] == {"published": "2026-09-21"}


def test_the_counters_are_declared_so_they_appear_before_the_first_alert(tmp_path):
    """A counter that only exists after it fires reads as a gap in the
    dashboard rather than a zero."""
    metrics = _Metrics()
    AlertArchive(path=str(tmp_path / "a.db"), service="fda-catalysts", metrics=metrics)
    assert "alert_archive_records_total" in metrics.declared
    assert "alert_archive_write_failures_total" in metrics.declared


# -- the seam with the rest of the platform ---------------------------------

def test_the_archive_survives_the_service_being_restarted(tmp_path):
    path = str(tmp_path / "alerts.db")
    first = AlertArchive(path=path, service="form4-insider", ref="v0.2.0")
    first.record(an_alert(dedup_key="before"))
    first.close()

    second = AlertArchive(path=path, service="form4-insider", ref="v0.3.0")
    second.record(an_alert(dedup_key="after"))
    assert second.count() == 2
    assert [r["ref"] for r in second.recent()] == ["v0.3.0", "v0.2.0"]


def test_rows_are_valid_json_end_to_end(archive):
    """The website and the `alerts` verb will both re-serialise these rows, so
    whatever goes in has to come back out as JSON."""
    archive.record_delivery(archive.record(an_alert()), True)
    assert json.loads(json.dumps(archive.recent(), default=str))
