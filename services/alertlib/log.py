"""Structured logging.

The fleet declares `logging.format: json` and `destination: journald`, so
services write to stdout and let systemd capture it. No FileHandler, no
log rotation inside the service, no per-service log path to go stale —
which is what the pre-migration bots did (each appending to its own
`*.log` next to the code, unrotated, one of them 7 MB in the repo).

Every record carries the service name and deployed ref, so a single
`journalctl -o cat -u 'alert-*' | jq` view across the fleet is filterable
without guessing which bot wrote what.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, stable key order, UTC timestamps."""

    def __init__(self, service: str, ref: str) -> None:
        super().__init__()
        self.service = service
        self.ref = ref

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "service": self.service,
            "ref": self.ref,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Anything passed via logger.info("...", extra={"nct_id": ...}) rides
        # along as a top-level field — that is how services attach the
        # identifiers you actually grep for during an incident.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _safe(value: object) -> object:
    if isinstance(value, (str, int, float, bool, type(None), list, dict)):
        return value
    return str(value)


def configure_logging(service: str, level: str = "INFO", fmt: str = "json",
                      ref: str = "unknown") -> logging.Logger:
    """Install the root handler. Idempotent — safe to call from tests."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service, ref))
    else:
        # Human-readable fallback for local development only. Production
        # always runs json; `alertctl` refuses to render a unit that sets
        # anything else in prod.
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # These two are chatty at INFO and drown out service events.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    return logging.getLogger(service)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
