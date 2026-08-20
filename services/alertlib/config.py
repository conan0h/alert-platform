"""Configuration, read from the environment the apply engine injects.

The env-var contract is deliberately narrow and fully documented here,
because it is the seam between the Go control plane and the Python data
plane. `alertctl` renders these into the systemd unit from the effective
spec (fleet defaults deep-merged with the service spec); the service
reads them and nothing else.

    ALERT_SERVICE_NAME              metadata.name          (required)
    ALERT_SERVICE_TIER              metadata.tier
    ALERT_STATE_DIR                 state.dir/<name>       (required)
    ALERT_LOG_LEVEL                 logging.level
    ALERT_LOG_FORMAT                logging.format         json|text
    ALERT_POLL_INTERVAL_SEC         polling.interval_sec   (required)
    ALERT_SOURCE_URL                polling.source_url
    ALERT_HEARTBEAT_INTERVAL_SEC    health.heartbeat_interval_sec
    ALERT_STARTUP_GRACE_SEC         health.startup_grace_sec
    ALERT_METRICS_ENABLED           health.metrics.enabled
    ALERT_METRICS_PORT              health.metrics.port
    ALERT_RATE_LIMIT_PER_MIN        delivery.rate_limit_per_min
    ALERT_DEDUP_RETENTION_DAYS      dedup.retention_days
    ALERT_DEPLOYED_REF              source.ref             (for /metrics label)

Service-specific polling keys — the `spec.polling` extension point from
docs/spec.md D5 — arrive as ALERT_POLLING_<UPPERCASED_KEY>, JSON-encoded
when not a scalar. `cfg.polling("forms", [])` reads them back.

Secrets are resolved from the store by the apply engine and injected as
ALERT_SECRET_<UPPERCASED_SECRET_NAME>. A spec saying
`bot_token_secret: tg_bot_token` becomes ALERT_SECRET_TG_BOT_TOKEN.
`cfg.secret("tg_bot_token")` reads it; a missing one raises rather than
silently degrading, because a service running without its credentials is
a silent outage.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


class ConfigError(RuntimeError):
    """Raised when the injected environment is missing or malformed.

    Always fatal: the service exits non-zero, systemd restarts it, and the
    post-deploy gate in `alertctl apply` catches the crash loop and rolls
    back. Failing loudly here is what makes that chain work.
    """


_TRUE = {"1", "true", "yes", "on"}


def _req(name: str) -> str:
    val = os.environ.get(name)
    if val is None or val == "":
        raise ConfigError(
            f"{name} is not set. Services are configured by the apply engine; "
            f"run them via `alertctl apply`, or source a dev env file for "
            f"local runs (see docs/runbooks/local-development.md)."
        )
    return val


def _int(name: str, default: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if default is None:
            raise ConfigError(f"{name} is not set and has no default.")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an integer.") from exc


@dataclass(frozen=True)
class ServiceConfig:
    """Effective configuration for one service instance."""

    name: str
    tier: str
    state_dir: str
    poll_interval_sec: int
    source_url: str
    heartbeat_interval_sec: int
    startup_grace_sec: int
    metrics_enabled: bool
    metrics_port: int
    rate_limit_per_min: int
    dedup_retention_days: int
    log_level: str
    log_format: str
    deployed_ref: str
    _polling: dict[str, Any] = field(default_factory=dict, repr=False)
    _secrets: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls) -> ServiceConfig:
        polling: dict[str, Any] = {}
        secrets: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.startswith("ALERT_POLLING_"):
                polling[key[len("ALERT_POLLING_"):].lower()] = _decode(value)
            elif key.startswith("ALERT_SECRET_"):
                secrets[key[len("ALERT_SECRET_"):].lower()] = value

        return cls(
            name=_req("ALERT_SERVICE_NAME"),
            tier=os.environ.get("ALERT_SERVICE_TIER", "standard"),
            state_dir=_req("ALERT_STATE_DIR"),
            poll_interval_sec=_int("ALERT_POLL_INTERVAL_SEC"),
            source_url=os.environ.get("ALERT_SOURCE_URL", ""),
            heartbeat_interval_sec=_int("ALERT_HEARTBEAT_INTERVAL_SEC", 300),
            startup_grace_sec=_int("ALERT_STARTUP_GRACE_SEC", 60),
            metrics_enabled=os.environ.get("ALERT_METRICS_ENABLED", "true").lower() in _TRUE,
            metrics_port=_int("ALERT_METRICS_PORT", 0),
            rate_limit_per_min=_int("ALERT_RATE_LIMIT_PER_MIN", 20),
            dedup_retention_days=_int("ALERT_DEDUP_RETENTION_DAYS", 90),
            log_level=os.environ.get("ALERT_LOG_LEVEL", "INFO").upper(),
            log_format=os.environ.get("ALERT_LOG_FORMAT", "json").lower(),
            deployed_ref=os.environ.get("ALERT_DEPLOYED_REF", "unknown"),
            _polling=polling,
            _secrets=secrets,
        )

    def polling(self, key: str, default: Any = None) -> Any:
        """Read a service-specific polling key (spec.polling extension point)."""
        return self._polling.get(key.lower(), default)

    def secret(self, name: str) -> str:
        """Resolve a secret by its spec name. Raises if the platform didn't inject it."""
        try:
            return self._secrets[name.lower()]
        except KeyError:
            raise ConfigError(
                f"secret {name!r} was not injected. The spec must reference it "
                f"(e.g. bot_token_secret: {name}) and it must exist in the "
                f"secrets backend under the fleet's secrets_prefix."
            ) from None

    def optional_secret(self, name: str, default: str = "") -> str:
        """Like `secret`, but for genuinely optional credentials (e.g. an API key
        that only raises the rate limit)."""
        return self._secrets.get(name.lower(), default)


def _decode(value: str) -> Any:
    """Polling values arrive JSON-encoded when they aren't plain scalars.

    Kept lenient on purpose: a bare string like `8-K` is not valid JSON and
    should stay a string rather than becoming a config error.
    """
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
