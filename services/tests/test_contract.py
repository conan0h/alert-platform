"""The control plane and the data plane must agree on the env contract.

`alertctl` (Go) renders the EnvironmentFile; `alertlib.ServiceConfig` (Python)
reads it. Nothing in either language checks the other, so a renamed variable
on one side is a runtime failure on the host, discovered after a restart.

This test closes that loop: it runs the real binary, parses the file systemd
would parse, and asserts the resulting config matches the spec. It skips if
the binary has not been built, so the Python suite stays runnable on its own.

Build the binary first:  make build
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from alertlib import ServiceConfig  # noqa: E402

BINARY = next(
    (p for p in [ROOT / "bin" / "alertctl", Path(shutil.which("alertctl") or "/nonexistent")]
     if p.exists()),
    None,
)

pytestmark = pytest.mark.skipif(
    BINARY is None, reason="alertctl not built; run `make build` to enable contract tests"
)


def render_env(service: str) -> dict[str, str]:
    """Run `alertctl render` and parse the EnvironmentFile half of its output."""
    out = subprocess.run(
        [str(BINARY), "render", "-service", service, "-root", str(ROOT)],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr

    marker = f"# /etc/alert-platform/{service}.env"
    assert marker in out.stdout, f"render did not emit an env section:\n{out.stdout}"
    env_block = out.stdout.split(marker, 1)[1]

    env: dict[str, str] = {}
    for line in env_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # systemd strips surrounding quotes and unescapes; mirror that here.
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        env[key] = value
    return env


def spec_for(service: str) -> dict:
    return yaml.safe_load((ROOT / "fleet" / "services" / f"{service}.yaml").read_text())


@pytest.mark.parametrize("service", [
    "clinical-trials", "edgar-mna", "fda-catalysts", "form4-insider",
])
def test_rendered_env_satisfies_the_python_config_loader(service, monkeypatch):
    env = render_env(service)

    for key in list(os.environ):
        if key.startswith("ALERT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Secrets are placeholders in `render`; inject the ones the spec names so
    # the loader sees the same shape it will see in production.
    spec = spec_for(service)
    delivery = spec["spec"]["delivery"]
    monkeypatch.setenv("ALERT_SECRET_TG_BOT_TOKEN", "123:fake")
    monkeypatch.setenv(
        "ALERT_SECRET_" + delivery["telegram_chat_secret"].upper(), "-1001"
    )

    cfg = ServiceConfig.from_env()          # raises ConfigError on any mismatch

    assert cfg.name == service
    assert cfg.poll_interval_sec == spec["spec"]["polling"]["interval_sec"]
    assert cfg.metrics_port == spec["spec"]["health"]["metrics"]["port"]
    assert cfg.rate_limit_per_min == delivery["rate_limit_per_min"]
    assert cfg.state_dir == f"/var/lib/alert-platform/{service}"
    assert cfg.log_format == "json"
    assert cfg.secret("tg_bot_token") == "123:fake"


def test_service_overrides_beat_fleet_defaults_end_to_end():
    """form4-insider declares heartbeat_interval_sec: 120 against a 300 default."""
    env = render_env("form4-insider")
    assert env["ALERT_HEARTBEAT_INTERVAL_SEC"] == "120"

    env = render_env("edgar-mna")
    assert env["ALERT_HEARTBEAT_INTERVAL_SEC"] == "300"   # inherited


def test_polling_extension_keys_survive_the_round_trip(monkeypatch):
    """Lists and per-service knobs must arrive decodable on the Python side."""
    env = render_env("edgar-mna")
    for key in list(os.environ):
        if key.startswith("ALERT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = ServiceConfig.from_env()
    assert cfg.polling("forms") == ["8-K", "SC 13D", "SC 13G", "DEFM14A", "425"]
    assert int(cfg.polling("edgar_interval_sec")) == 300
    assert int(cfg.polling("press_interval_sec")) == 600


def test_render_never_emits_a_real_secret_value():
    """`render` is a documentation command; it must be safe to paste anywhere."""
    for service in ["clinical-trials", "edgar-mna", "fda-catalysts", "form4-insider"]:
        env = render_env(service)
        for key, value in env.items():
            if key.startswith("ALERT_SECRET_"):
                assert value == "<resolved-at-apply>", f"{key} leaked a value"
