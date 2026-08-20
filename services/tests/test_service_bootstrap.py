"""Every entrypoint must import cleanly and bind to the platform contract.

Import-time failures are the expensive kind: they only show up when systemd
starts the unit on the host, minutes into a deploy, after the plan already
said the change was safe. Catching them in CI is the whole point.

Each service is imported in its own subprocess because they define
overlapping module-level names (`log`, `SVC`, `DB_PATH`, `main`) and the
form4 entrypoints import each other by bare module name — exactly as they do
under the deployed PYTHONPATH.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parent.parent

ENTRYPOINTS = [
    ("clinical-trials", "clinical_trials", "main.py"),
    ("edgar-mna", "edgar_mna", "main.py"),
    ("fda-catalysts", "fda_catalysts", "main.py"),
    ("form4-insider", "form4_insider", "main.py"),
    ("form4-scorer", "form4_insider", "form4_scorer.py"),
    ("form4-backfill", "form4_insider", "form4_backfill.py"),
]

PROBE = """
import importlib.util, sys, pathlib
path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("entrypoint", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert hasattr(mod, "main"), "entrypoint has no main()"
banned = [n for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if hasattr(mod, n)]
assert not banned, f"credentials still read at module level: {banned}"
src = path.read_text()
assert "load_dotenv" not in src, "still loads a local .env"
assert "FileHandler" not in src, "still writes its own log file"
print("ok")
"""


@pytest.mark.parametrize("name,pkg,entry", ENTRYPOINTS, ids=[e[0] for e in ENTRYPOINTS])
def test_entrypoint_imports_cleanly(name, pkg, entry, tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SERVICES), str(SERVICES / pkg)])
    env["ALERT_STATE_DIR"] = str(tmp_path)
    env["ALERT_SERVICE_NAME"] = name

    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(SERVICES / pkg / entry)],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, (
        f"{pkg}/{entry} failed to import:\n{result.stdout}\n{result.stderr}"
    )
    assert "ok" in result.stdout


def test_no_service_writes_state_relative_to_cwd():
    """State must be resolved through the platform, never a bare filename.

    This is the regression guard for the bug that put a 3.5 MB production
    SQLite file in the repository.
    """
    offenders = []
    for path in SERVICES.rglob("*.py"):
        if "tests" in path.parts or "alertlib" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'sqlite3.connect("' in stripped or "sqlite3.connect('" in stripped:
                offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, "state opened by literal path:\n" + "\n".join(offenders)
