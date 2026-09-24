"""The Service facade — one object a bot's main() needs.

Wires config, logging, metrics, heartbeat, health endpoint and delivery
together, and owns the poll-loop mechanics that all four services had
independently reimplemented: timing the cycle, subtracting elapsed time
from the sleep, counting errors, and not dying because one upstream
returned a 502.

Graceful shutdown matters here because `alertctl apply` restarts units
during deploys: SIGTERM must let an in-flight cycle finish and the SQLite
connection close, or a deploy can leave a half-written state row that
re-alerts on the next start.
"""

from __future__ import annotations

import contextlib
import signal
import time
from collections.abc import Iterator

from .archive import Alert, AlertArchive
from .config import ServiceConfig
from .health import HealthServer, Heartbeat, Metrics
from .log import configure_logging
from .state import state_path
from .telegram import TelegramClient

# How often the poll loop writes the metrics snapshot to the journal.
#
# Every counter the fleet keeps lives on `/metrics`, which is bound to the
# host's loopback; the only read verb that returns host output is `logs`. So
# four open questions — has any alert ever been sent, is the archive filling,
# how often does a trial status actually change, has a send ever been refused
# — are recorded on the host and unreadable off it. Emitting the snapshot to
# journald puts them on the one path out.
#
# 900 seconds trades the two constraints against each other. `logs` returns
# roughly the first 24 KB of its window, so a snapshot has to be frequent
# enough that a short window contains one per service, and rare enough that it
# does not become the thing filling that 24 KB: at a quarter hour it is about
# 96 lines a day per service, against the ~1,900 that the source-health
# escalation schedule exists to avoid.
METRICS_SNAPSHOT_INTERVAL_SEC = 900


class Service:
    def __init__(self, cfg: ServiceConfig) -> None:
        self.cfg = cfg
        self.log = configure_logging(
            cfg.name, level=cfg.log_level, fmt=cfg.log_format, ref=cfg.deployed_ref
        )
        self.metrics = Metrics(cfg.name, cfg.deployed_ref)
        self.heartbeat = Heartbeat(cfg.heartbeat_interval_sec)
        self.health = HealthServer(
            cfg.metrics_port if cfg.metrics_enabled else 0,
            self.metrics,
            self.heartbeat,
        )
        self._telegram: TelegramClient | None = None
        self._archive: AlertArchive | None = None
        self._stopping = False
        self._cycle = 0
        # Zero rather than now, so the first cycle always emits a snapshot and
        # a restarted service is legible without waiting a quarter hour.
        self._last_snapshot_at = 0.0

    @classmethod
    def from_env(cls) -> Service:
        return cls(ServiceConfig.from_env())

    # -- delivery ---------------------------------------------------------
    @property
    def telegram(self) -> TelegramClient:
        """Lazily built so a service that never sends (backfill jobs) does
        not require delivery secrets to start."""
        if self._telegram is None:
            self._telegram = TelegramClient(
                token=self.cfg.secret("tg_bot_token"),
                chat_id=self.cfg.secret(self.chat_secret_name),
                rate_limit_per_min=self.cfg.rate_limit_per_min,
                metrics=self.metrics,
            )
        return self._telegram

    @property
    def chat_secret_name(self) -> str:
        """The delivery chat secret injected for this service.

        The apply engine injects exactly one `tg_chat_*` secret per service
        (from `delivery.telegram_chat_secret`), so discovering it beats
        hardcoding the name in four different places.
        """
        names = [n for n in self.cfg._secrets if n.startswith("tg_chat")]
        if not names:
            raise KeyError(
                "no tg_chat_* secret injected; check delivery.telegram_chat_secret "
                "in this service's spec"
            )
        return names[0]

    # -- archive ----------------------------------------------------------
    @property
    def archive(self) -> AlertArchive:
        """Append-only record of what this service emitted.

        Its own database file, not the service's dedup state: see
        alertlib/archive.py for why sharing that file is the one thing this
        must not do.
        """
        if self._archive is None:
            self._archive = AlertArchive(
                path=self.state_file("alerts.db"),
                service=self.cfg.name,
                ref=self.cfg.deployed_ref,
                metrics=self.metrics,
            )
        return self._archive

    def send_alert(self, alert: Alert) -> bool:
        """Archive the alert, deliver it, and record the outcome.

        Every alert goes out through here rather than through `telegram.send`
        directly, so "is it recorded" is a property of the send path instead
        of a convention four services each have to remember. The return value
        is the delivery result, unchanged, because callers use it to decide
        whether to mark an item as alerted.
        """
        row_id = self.archive.record(alert)
        delivered = self.telegram.send(alert.body)
        self.archive.record_delivery(row_id, delivered)
        return delivered

    # -- measurement ------------------------------------------------------
    def log_metrics_snapshot(self) -> None:
        """Write every counter and gauge to the journal as one line.

        Nested under a single `metrics` key rather than flattened into the
        record: a metric named like a `logging.LogRecord` attribute would be
        dropped by the formatter's reserved-key filter, silently, and a
        measurement that can disappear on a rename is not a measurement.
        """
        # Stamped before the write, not after: a snapshot that fails should
        # wait out the interval like a successful one, rather than retry — and
        # warn — on every cycle.
        self._last_snapshot_at = time.time()
        self.log.info("metrics snapshot", extra={"metrics": self.metrics.snapshot()})

    def _snapshot_if_due(self) -> None:
        if time.time() - self._last_snapshot_at < METRICS_SNAPSHOT_INTERVAL_SEC:
            return
        try:
            self.log_metrics_snapshot()
        except Exception as exc:
            # This runs from `poll_cycle`'s finally, where a raise escapes the
            # context manager and ends the caller's loop. Bookkeeping must not
            # be able to stop the service it is measuring.
            self.log.warning("metrics snapshot failed", extra={"error": str(exc)})

    # -- state ------------------------------------------------------------
    def state_file(self, filename: str) -> str:
        return state_path(self.cfg.state_dir, filename)

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> Service:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        self.health.start()
        self.heartbeat.beat()
        self.log.info(
            "service starting",
            extra={
                "ref": self.cfg.deployed_ref,
                "tier": self.cfg.tier,
                "poll_interval_sec": self.cfg.poll_interval_sec,
                "state_dir": self.cfg.state_dir,
                "metrics_port": self.cfg.metrics_port if self.cfg.metrics_enabled else None,
            },
        )
        return self

    def __exit__(self, *exc) -> bool:
        self.log.info("service stopping", extra={"cycles": self._cycle})
        # A deploy replaces the process, and its totals are not carried over.
        # Recording them here is the only way the journal keeps what the
        # outgoing process counted.
        self._last_snapshot_at = 0.0
        self._snapshot_if_due()
        self.health.stop()
        if self._archive is not None:
            self._archive.close()
        return False

    def _on_signal(self, signum, _frame) -> None:
        self.log.info("signal received; finishing current cycle",
                      extra={"signal": signum})
        self._stopping = True

    @property
    def stopping(self) -> bool:
        return self._stopping

    def running(self) -> bool:
        """Loop predicate: `while svc.running():`"""
        return not self._stopping

    # -- poll loop --------------------------------------------------------
    @contextlib.contextmanager
    def poll_cycle(self) -> Iterator[None]:
        """Wrap one poll iteration.

        Counts the cycle, beats the heartbeat, records duration, and
        swallows exceptions after logging them — a single bad upstream
        response must not kill a service whose whole job is to keep
        watching. Repeated failures surface through
        `alert_poll_errors_total` and a stale
        `alert_last_success_timestamp_seconds`, which is what the
        dashboards and the post-deploy gate alert on.
        """
        self._cycle += 1
        self.metrics.inc("alert_polls_total")
        started = time.monotonic()
        self._cycle_start_wall = time.time()
        try:
            yield
        except Exception as exc:
            self.metrics.inc("alert_poll_errors_total")
            self.log.exception("poll cycle failed", extra={"error": str(exc)})
        else:
            self.metrics.set("alert_last_success_timestamp_seconds", time.time())
        finally:
            duration = time.monotonic() - started
            self.metrics.set("alert_last_poll_duration_seconds", duration)
            self.heartbeat.beat()
            self.metrics.set("alert_heartbeat_timestamp_seconds", self.heartbeat.last)
            self.log.info("poll cycle complete",
                          extra={"cycle": self._cycle, "duration_sec": round(duration, 2)})
            self._snapshot_if_due()

    def sleep_until_next_poll(self, interval_sec: int | None = None) -> None:
        """Sleep the remainder of the interval, in short slices.

        Slicing keeps SIGTERM responsive: a 600-second sleep would
        otherwise make every deploy wait out the poll interval or kill the
        process mid-cycle.
        """
        interval = interval_sec or self.cfg.poll_interval_sec
        elapsed = time.time() - getattr(self, "_cycle_start_wall", time.time())
        remaining = max(1.0, interval - elapsed)
        deadline = time.monotonic() + remaining
        while not self._stopping and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
            self.heartbeat.beat()
