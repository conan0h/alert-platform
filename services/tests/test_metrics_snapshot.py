"""The metrics snapshot: every counter, on the one path off the host.

`/metrics` is bound to the host's loopback and no read verb returns its body,
so a cumulative counter is only readable where the journal is readable. These
tests pin the two properties that makes the snapshot worth trusting: it carries
exactly what `/metrics` carries, and it cannot stop the poll loop it measures.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICES))

from alertlib import Service  # noqa: E402
from alertlib.health import Metrics  # noqa: E402
from alertlib.log import JsonFormatter  # noqa: E402
from alertlib.service import METRICS_SNAPSHOT_INTERVAL_SEC  # noqa: E402

BASE_ENV = {
    "ALERT_SERVICE_NAME": "edgar-mna",
    "ALERT_SERVICE_TIER": "standard",
    "ALERT_POLL_INTERVAL_SEC": "45",
    "ALERT_SOURCE_URL": "https://efts.sec.gov/LATEST/search-index",
    "ALERT_HEARTBEAT_INTERVAL_SEC": "300",
    "ALERT_STARTUP_GRACE_SEC": "60",
    "ALERT_METRICS_ENABLED": "false",
    "ALERT_METRICS_PORT": "0",
    "ALERT_RATE_LIMIT_PER_MIN": "20",
    "ALERT_DEDUP_RETENTION_DAYS": "90",
    "ALERT_LOG_LEVEL": "INFO",
    "ALERT_LOG_FORMAT": "json",
    "ALERT_DEPLOYED_REF": "v1.4.2",
    "ALERT_POLLING_FORMS": '["8-K"]',
    "ALERT_POLLING_EDGAR_INTERVAL_SEC": "300",
    "ALERT_SECRET_TG_BOT_TOKEN": "123:fake",
    "ALERT_SECRET_TG_CHAT_MNA": "-1001",
    "ALERT_SECRET_EDGAR_USER_AGENT": "alert-platform ops@example.com",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("ALERT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ALERT_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def logged(capsys) -> list[dict]:
    """The JSON lines the service wrote to stdout, which is what journald gets.

    Read from stdout rather than through `caplog`: `configure_logging` replaces
    the root handlers when a `Service` is built, which removes pytest's, and
    capturing what systemd would capture is the more faithful test anyway.
    """
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def snapshots(capsys) -> list[dict]:
    return [r["metrics"] for r in logged(capsys) if r["msg"] == "metrics snapshot"]


# -- what the snapshot contains -------------------------------------------
def test_snapshot_carries_the_same_names_as_metrics(env):
    """The two exposition paths must not drift apart.

    A counter that reaches Prometheus and not the journal is a counter nobody
    here can read, since nothing scrapes this fleet yet.
    """
    metrics = Metrics("edgar-mna", "v1.4.2")
    metrics.declare_counter("alert_funnel_changed_total", "Trials whose status moved.")

    rendered = set(re.findall(r"^# TYPE (\S+) ", metrics.render(), re.MULTILINE))
    assert set(metrics.snapshot()) == rendered


def test_snapshot_includes_counters_that_are_still_zero(env):
    """Zero is the answer to "has any alert ever been sent", not an absence."""
    snapshot = Metrics("edgar-mna", "v1.4.2").snapshot()
    assert snapshot["alert_alerts_sent_total"] == 0
    assert snapshot["alert_sends_refused_total"] == 0


def test_snapshot_carries_the_window_the_counters_accumulated_over(env):
    snapshot = Metrics("edgar-mna", "v1.4.2").snapshot()
    assert snapshot["alert_uptime_seconds"] >= 0


def test_snapshot_renders_whole_numbers_as_integers(env):
    metrics = Metrics("edgar-mna", "v1.4.2")
    metrics.inc("alert_polls_total", 1890)
    metrics.set("alert_last_poll_duration_seconds", 1.3449)

    snapshot = metrics.snapshot()
    assert snapshot["alert_polls_total"] == 1890
    assert isinstance(snapshot["alert_polls_total"], int)
    assert snapshot["alert_last_poll_duration_seconds"] == 1.345


def test_snapshot_survives_the_json_formatter_intact(env):
    """Nesting exists to defeat the formatter's reserved-key filter.

    A metric named after a `logging.LogRecord` attribute is dropped when the
    values are flattened into the record, so the whole mapping travels under
    one key.
    """
    metrics = Metrics("edgar-mna", "v1.4.2")
    metrics.declare_counter("name", "A metric named like a LogRecord attribute.")
    metrics.inc("name", 7)
    metrics.inc("alert_polls_total", 3)

    record = logging.LogRecord(
        "edgar-mna", logging.INFO, __file__, 0, "metrics snapshot", (), None
    )
    record.metrics = metrics.snapshot()
    payload = json.loads(JsonFormatter("edgar-mna", "v1.4.2").format(record))

    assert payload["metrics"]["name"] == 7
    assert payload["metrics"]["alert_polls_total"] == 3


# -- when the snapshot is written -----------------------------------------
def test_first_poll_cycle_emits_a_snapshot(env, capsys):
    """A restarted service is legible immediately, not a quarter hour later."""
    svc = Service.from_env()
    with svc.poll_cycle():
        pass
    assert len(snapshots(capsys)) == 1


def test_later_cycles_stay_quiet_until_the_interval_elapses(env, capsys):
    svc = Service.from_env()
    for _ in range(5):
        with svc.poll_cycle():
            pass
    assert len(snapshots(capsys)) == 1


def test_a_cycle_after_the_interval_emits_again(env, capsys):
    svc = Service.from_env()
    with svc.poll_cycle():
        pass
    svc._last_snapshot_at -= METRICS_SNAPSHOT_INTERVAL_SEC
    with svc.poll_cycle():
        pass

    lines = snapshots(capsys)
    assert len(lines) == 2
    # The second line is the cumulative total, not this cycle's count: that is
    # the whole difference between the snapshot and the funnel line beside it.
    assert lines[0]["alert_polls_total"] == 1
    assert lines[1]["alert_polls_total"] == 2


def test_shutdown_records_what_the_outgoing_process_counted(env, capsys):
    """A deploy discards the process; the journal must keep its totals."""
    svc = Service.from_env()
    with svc:
        with svc.poll_cycle():
            pass
        capsys.readouterr()          # drop the startup and first-cycle lines

    lines = snapshots(capsys)
    assert len(lines) == 1
    assert lines[0]["alert_polls_total"] == 1


# -- what it must never do ------------------------------------------------
def test_a_failing_snapshot_does_not_end_the_poll_loop(env, capsys, monkeypatch):
    """It runs from `poll_cycle`'s finally, where a raise escapes the loop."""
    svc = Service.from_env()

    def explode() -> dict:
        raise RuntimeError("registry wedged")

    monkeypatch.setattr(svc.metrics, "snapshot", explode)

    with svc.poll_cycle():
        pass
    with svc.poll_cycle():
        pass

    records = logged(capsys)
    assert svc.metrics.get("alert_polls_total") == 2
    assert svc.metrics.get("alert_poll_errors_total") == 0
    assert not [r for r in records if r["msg"] == "metrics snapshot"]
    # Once, not once per cycle: a broken registry must not become log spam.
    assert len([r for r in records if r["msg"] == "metrics snapshot failed"]) == 1


def test_the_snapshot_never_carries_a_secret_value(env, capsys):
    """The registry holds names and numbers; a spec's secrets must stay out."""
    svc = Service.from_env()
    with svc.poll_cycle():
        pass

    snapshot = snapshots(capsys)[0]
    assert "123:fake" not in json.dumps(snapshot)
    assert "-1001" not in json.dumps(snapshot)
    assert all(isinstance(v, (int, float)) for v in snapshot.values())
