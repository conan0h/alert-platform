"""alertlib — the runtime contract between a service and the platform.

Every managed service reads its configuration from the environment the
apply engine injects, logs JSON to stdout, keeps state under a
platform-owned directory, and exposes a health/metrics endpoint on its
reserved port. This package is the single implementation of all four.

The rule that makes the platform work: a service never reads a config
file of its own and never holds a secret value in its source. Everything
comes from `Service.from_env()`, which is populated by the systemd unit
that `alertctl` renders from the fleet spec.

Typical use:

    from alertlib import Service

    svc = Service.from_env()
    log = svc.log

    with svc:                          # starts health server, logs startup
        while True:
            with svc.poll_cycle():     # times the loop, counts errors
                for item in fetch():
                    if svc.telegram.send(render(item)):
                        svc.metrics.inc("alerts_sent_total")
            svc.sleep_until_next_poll()
"""

from .config import ConfigError, ServiceConfig
from .health import HealthServer, Metrics
from .log import configure_logging, get_logger
from .service import Service
from .state import state_path
from .telegram import TelegramClient

__all__ = [
    "ConfigError",
    "HealthServer",
    "Metrics",
    "Service",
    "ServiceConfig",
    "TelegramClient",
    "configure_logging",
    "get_logger",
    "state_path",
]

__version__ = "0.1.0"
