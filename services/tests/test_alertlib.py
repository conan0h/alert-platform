"""Tests for the platform runtime contract.

These cover the seam, not the signal logic: if `alertlib` misreads the
injected environment or a service fails to bootstrap, every service breaks
at once, so this is where tests earn the most.

Run: python3 -m pytest services/tests -q     (from the repo root)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICES))

from alertlib import Service, ServiceConfig  # noqa: E402
from alertlib.config import ConfigError  # noqa: E402
from alertlib.health import Heartbeat, HealthServer, Metrics  # noqa: E402
from alertlib.log import JsonFormatter  # noqa: E402
from alertlib.state import state_path  # noqa: E402
from alertlib.telegram import _TokenBucket  # noqa: E402


BASE_ENV = {
    "ALERT_SERVICE_NAME": "edgar-mna",
    "ALERT_SERVICE_TIER": "standard",
    "ALERT_POLL_INTERVAL_SEC": "45",
    "ALERT_SOURCE_URL": "https://efts.sec.gov/LATEST/search-index",
    "ALERT_HEARTBEAT_INTERVAL_SEC": "300",
    "ALERT_STARTUP_GRACE_SEC": "60",
    "ALERT_METRICS_ENABLED": "true",
    "ALERT_METRICS_PORT": "9101",
    "ALERT_RATE_LIMIT_PER_MIN": "20",
    "ALERT_DEDUP_RETENTION_DAYS": "90",
    "ALERT_LOG_LEVEL": "INFO",
    "ALERT_LOG_FORMAT": "json",
    "ALERT_DEPLOYED_REF": "v1.4.2",
    "ALERT_POLLING_FORMS": '["8-K", "SC 13D"]',
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


# -- config ---------------------------------------------------------------
def test_config_reads_injected_environment(env):
    cfg = ServiceConfig.from_env()
    assert cfg.name == "edgar-mna"
    assert cfg.poll_interval_sec == 45
    assert cfg.metrics_port == 9101
    assert cfg.deployed_ref == "v1.4.2"


def test_polling_extension_keys_decode(env):
    cfg = ServiceConfig.from_env()
    assert cfg.polling("forms") == ["8-K", "SC 13D"]
    assert int(cfg.polling("edgar_interval_sec")) == 300
    assert cfg.polling("absent", "fallback") == "fallback"


def test_secrets_resolve_by_spec_name(env):
    cfg = ServiceConfig.from_env()
    assert cfg.secret("tg_bot_token") == "123:fake"
    assert cfg.optional_secret("openfda_api_key") == ""


def test_missing_secret_is_fatal_not_silent(env):
    cfg = ServiceConfig.from_env()
    with pytest.raises(ConfigError, match="was not injected"):
        cfg.secret("nonexistent_secret")


def test_missing_required_env_is_fatal(monkeypatch):
    for key in list(os.environ):
        if key.startswith("ALERT_"):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError, match="ALERT_SERVICE_NAME"):
        ServiceConfig.from_env()


# -- logging --------------------------------------------------------------
def test_json_log_lines_are_parseable_and_labelled():
    fmt = JsonFormatter("edgar-mna", "v1.4.2")
    record = logging.LogRecord("test", logging.INFO, __file__, 1,
                               "deal detected", (), None)
    record.ticker = "ACME"
    payload = json.loads(fmt.format(record))
    assert payload["service"] == "edgar-mna"
    assert payload["ref"] == "v1.4.2"
    assert payload["level"] == "INFO"
    assert payload["msg"] == "deal detected"
    assert payload["ticker"] == "ACME"          # extra fields ride along


# -- state ----------------------------------------------------------------
def test_state_path_creates_dir_and_returns_absolute(tmp_path):
    path = state_path(str(tmp_path / "svc"), "seen.db")
    assert path.endswith("/svc/seen.db")
    assert (tmp_path / "svc").is_dir()


def test_state_path_refuses_escape(tmp_path):
    with pytest.raises(ValueError, match="outside the state dir"):
        state_path(str(tmp_path / "svc"), "../../etc/passwd")


# -- metrics and health ---------------------------------------------------
def test_metrics_render_prometheus_format():
    m = Metrics("edgar-mna", "v1.4.2")
    m.inc("alert_alerts_sent_total", 3)
    m.set("alert_last_poll_duration_seconds", 1.5)
    text = m.render()
    assert 'alert_alerts_sent_total{service="edgar-mna",ref="v1.4.2"} 3' in text
    assert "# TYPE alert_alerts_sent_total counter" in text
    assert "# TYPE alert_last_poll_duration_seconds gauge" in text


def test_heartbeat_goes_stale_after_two_intervals():
    hb = Heartbeat(interval_sec=10)
    assert hb.is_fresh()
    hb._last = time.time() - 25
    assert not hb.is_fresh()


def test_healthz_reports_503_when_heartbeat_is_stale():
    metrics = Metrics("edgar-mna", "v1.4.2")
    hb = Heartbeat(interval_sec=1)
    server = HealthServer(port=19137, metrics=metrics, heartbeat=hb)
    server.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:19137/healthz", timeout=2) as resp:
            body = json.loads(resp.read())
        assert resp.status == 200
        assert body["status"] == "ok"

        hb._last = time.time() - 30                 # simulate a wedged poll loop
        try:
            urllib.request.urlopen("http://127.0.0.1:19137/healthz", timeout=2)
            pytest.fail("expected 503 once the heartbeat went stale")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503

        with urllib.request.urlopen("http://127.0.0.1:19137/metrics", timeout=2) as resp:
            assert b"alert_polls_total" in resp.read()
    finally:
        server.stop()


# -- rate limiting --------------------------------------------------------
def test_token_bucket_throttles_beyond_capacity():
    bucket = _TokenBucket(per_min=60)
    for _ in range(60):
        assert bucket.take() == 0.0
    assert bucket.take() > 0                      # 61st in the same minute waits


# -- service lifecycle ----------------------------------------------------
def test_poll_cycle_records_success_and_survives_failure(env):
    svc = Service.from_env()
    svc.cfg = svc.cfg  # readability: config is frozen

    with svc.poll_cycle():
        pass
    assert svc.metrics.get("alert_polls_total") == 1
    assert svc.metrics.get("alert_last_success_timestamp_seconds") > 0

    with svc.poll_cycle():
        raise RuntimeError("upstream 502")        # must not propagate
    assert svc.metrics.get("alert_poll_errors_total") == 1
    assert svc.metrics.get("alert_polls_total") == 2


def test_chat_secret_is_discovered_from_injected_secrets(env):
    svc = Service.from_env()
    assert svc.chat_secret_name == "tg_chat_mna"


def test_sigterm_stops_the_loop(env):
    svc = Service.from_env()
    assert svc.running()
    svc._on_signal(15, None)
    assert not svc.running()
