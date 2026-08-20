"""Health and metrics endpoint.

This is the concrete consumer of the health contract that Phase 1
declared but nothing read (docs/spec.md, D4). Two endpoints on the
service's reserved port:

    GET /healthz    JSON liveness. 200 while the heartbeat is fresh,
                    503 once it is older than heartbeat_interval_sec * 2.
                    `alertctl apply` polls this after startup_grace_sec
                    and rolls back if it never goes green.

    GET /metrics    Prometheus text exposition. Scrape targets are
                    generated from the specs by tools/gen_observability.py,
                    so the port lives in exactly one place.

No prometheus_client dependency: the exposition format for counters and
gauges is a dozen lines, and the fleet's whole dependency budget is
`requests` plus what the services already needed. Adding a library to
emit six metrics is not a trade worth making on a 256 MB memory ceiling.

The server runs on a daemon thread. If the poll loop wedges, the
heartbeat goes stale and /healthz flips to 503 even though the process is
alive and the socket still answers — which is the distinction that makes
this useful rather than decorative.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .log import get_logger

log = get_logger("alertlib.health")


class Metrics:
    """Counters and gauges, guarded by a lock, rendered as Prometheus text."""

    def __init__(self, service: str, ref: str = "unknown") -> None:
        self.service = service
        self.ref = ref
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._help: dict[str, str] = {}
        self.started_at = time.time()

        self.declare_counter("alert_polls_total", "Poll cycles started.")
        self.declare_counter("alert_poll_errors_total", "Poll cycles that raised.")
        self.declare_counter("alert_items_seen_total", "Upstream items examined.")
        self.declare_counter("alert_alerts_sent_total", "Alerts delivered successfully.")
        self.declare_counter("alert_delivery_failures_total", "Delivery attempts that failed.")
        self.declare_gauge("alert_last_success_timestamp_seconds",
                           "Unix time of the last fully successful poll cycle.")
        self.declare_gauge("alert_last_poll_duration_seconds",
                           "Wall-clock duration of the most recent poll cycle.")
        self.declare_gauge("alert_heartbeat_timestamp_seconds",
                           "Unix time of the last heartbeat emitted by the service.")
        self.declare_gauge("alert_up", "1 while the service considers itself healthy.")
        self.set("alert_up", 1)

    def declare_counter(self, name: str, help_text: str) -> None:
        with self._lock:
            self._counters.setdefault(name, 0.0)
            self._help[name] = help_text

    def declare_gauge(self, name: str, help_text: str) -> None:
        with self._lock:
            self._gauges.setdefault(name, 0.0)
            self._help[name] = help_text

    def inc(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    def get(self, name: str) -> float:
        with self._lock:
            if name in self._counters:
                return self._counters[name]
            return self._gauges.get(name, 0.0)

    def render(self) -> str:
        labels = f'{{service="{self.service}",ref="{self.ref}"}}'
        lines: list[str] = []
        with self._lock:
            for kind, series in (("counter", self._counters), ("gauge", self._gauges)):
                for name in sorted(series):
                    lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
                    lines.append(f"# TYPE {name} {kind}")
                    lines.append(f"{name}{labels} {series[name]:g}")
        lines.append("# HELP alert_uptime_seconds Seconds since process start.")
        lines.append("# TYPE alert_uptime_seconds gauge")
        lines.append(f"alert_uptime_seconds{labels} {time.time() - self.started_at:.0f}")
        return "\n".join(lines) + "\n"


class Heartbeat:
    """Liveness signal. The poll loop touches it; /healthz reads it."""

    def __init__(self, interval_sec: int) -> None:
        self.interval_sec = interval_sec
        self._last = time.time()
        self._lock = threading.Lock()

    def beat(self) -> None:
        with self._lock:
            self._last = time.time()

    @property
    def last(self) -> float:
        with self._lock:
            return self._last

    @property
    def age(self) -> float:
        return time.time() - self.last

    def is_fresh(self) -> bool:
        # Two intervals of slack: one missed beat is a slow upstream, two is
        # a stuck loop. Alerting on a single miss would page on every
        # EDGAR hiccup.
        return self.age < self.interval_sec * 2


class HealthServer:
    """Serves /healthz and /metrics on a daemon thread."""

    def __init__(self, port: int, metrics: Metrics, heartbeat: Heartbeat,
                 extra: Callable[[], dict] | None = None) -> None:
        self.port = port
        self.metrics = metrics
        self.heartbeat = heartbeat
        self.extra = extra
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.port <= 0:
            log.warning("metrics port not set; health endpoint disabled")
            return

        metrics, heartbeat, extra = self.metrics, self.heartbeat, self.extra

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                path = self.path.split("?", 1)[0]
                if path == "/metrics":
                    self._respond(200, metrics.render(), "text/plain; version=0.0.4")
                elif path in ("/healthz", "/health"):
                    fresh = heartbeat.is_fresh()
                    metrics.set("alert_up", 1 if fresh else 0)
                    metrics.set("alert_heartbeat_timestamp_seconds", heartbeat.last)
                    body = {
                        "service": metrics.service,
                        "ref": metrics.ref,
                        "status": "ok" if fresh else "stale",
                        "heartbeat_age_sec": round(heartbeat.age, 1),
                        "heartbeat_interval_sec": heartbeat.interval_sec,
                        "uptime_sec": round(time.time() - metrics.started_at),
                        "last_success_timestamp": metrics.get(
                            "alert_last_success_timestamp_seconds"
                        ),
                    }
                    if extra:
                        try:
                            body.update(extra())
                        except Exception as exc:  # never let a probe crash the probe
                            body["extra_error"] = str(exc)
                    self._respond(200 if fresh else 503,
                                  json.dumps(body, indent=2) + "\n",
                                  "application/json")
                else:
                    self._respond(404, "not found\n", "text/plain")

            def _respond(self, code: int, body: str, ctype: str) -> None:
                raw = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args) -> None:
                # Scrapes every 15s would otherwise flood journald.
                return

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="health", daemon=True
        )
        self._thread.start()
        log.info("health endpoint listening", extra={"port": self.port})

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
