"""State paths.

The fleet declares `state.dir: /var/lib/alert-platform` and the apply
engine passes `<dir>/<service-name>` as ALERT_STATE_DIR. Services must
put every file they persist inside it.

Before the migration each bot opened `sqlite3.connect("ct_seen.db")` —
a relative path, so the database landed wherever the process happened to
start. That is why the pre-migration repo contained a 3.5 MB `ct_seen.db`
and a 7 MB log: production state was living in the working copy. It also
made backups unlocatable and meant a `cd` in a unit file could silently
create a fresh, empty database and re-alert on everything.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_path(state_dir: str, filename: str) -> str:
    """Absolute path to a state file, creating the directory if needed.

    Rejects anything that would escape the state directory: a service must
    not be able to write outside the space the platform owns and backs up.
    """
    base = Path(state_dir).resolve()
    target = (base / filename).resolve()
    if base != target and base not in target.parents:
        raise ValueError(
            f"{filename!r} resolves outside the state dir {base}; "
            f"services may only persist inside their own state directory."
        )
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o750)
    return str(target)
