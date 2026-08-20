#!/usr/bin/env python3
"""gen_observability.py — derive monitoring config from the fleet spec.

Phase 1 declared a health contract that nothing consumed (docs/spec.md, D4).
The apply engine now consumes it, and so does this: scrape targets and
dashboard panels are generated from `health.metrics.port` and
`metadata.name` rather than maintained alongside them.

The point is not saving typing. It is that a service added to the fleet
cannot be silently unmonitored, and a metrics port changed in a spec cannot
leave Prometheus scraping the old one — the generated files change in the
same commit as the spec, and CI fails if they were not regenerated.

Usage:
    python3 tools/gen_observability.py            # write deploy/
    python3 tools/gen_observability.py --check    # fail if outputs are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FLEET_DIR = REPO_ROOT / "fleet"
OUT_PROM = REPO_ROOT / "deploy" / "prometheus" / "targets.json"
OUT_RULES = REPO_ROOT / "deploy" / "prometheus" / "alerts.yml"
OUT_DASH = REPO_ROOT / "deploy" / "grafana" / "alert-platform.json"


def load_services() -> list[dict]:
    services = []
    for path in sorted((FLEET_DIR / "services").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if doc.get("kind") != "Service":
            continue
        health = doc["spec"].get("health", {})
        metrics = health.get("metrics", {})
        services.append({
            "name": doc["metadata"]["name"],
            "tier": doc["metadata"].get("tier", "standard"),
            "port": metrics.get("port"),
            "enabled": metrics.get("enabled", True),
            "heartbeat": health.get("heartbeat_interval_sec"),
            "interval": doc["spec"]["polling"]["interval_sec"],
        })
    return services


def load_fleet() -> dict:
    return yaml.safe_load((FLEET_DIR / "fleet.yaml").read_text())


def build_targets(services: list[dict], fleet: dict) -> list[dict]:
    """Prometheus file_sd config: one target per service, labelled by name and tier."""
    host = fleet["targets"]["default_host"]["address"]
    out = []
    for svc in services:
        if not svc["enabled"] or not svc["port"]:
            continue
        out.append({
            "targets": [f"{host}:{svc['port']}"],
            "labels": {
                "job": "alert-platform",
                "service": svc["name"],
                "tier": svc["tier"],
                "fleet": fleet["metadata"]["name"],
            },
        })
    return out


def build_rules(services: list[dict]) -> dict:
    """Alerting rules whose thresholds come from each service's own contract.

    A service that declares a 120-second heartbeat should not be judged by a
    300-second rule. Deriving the threshold from the spec is what makes the
    health contract meaningful rather than decorative.
    """
    rules = []
    for svc in services:
        if not svc["enabled"] or not svc["port"]:
            continue
        name = svc["name"]
        heartbeat = svc["heartbeat"] or 300
        # Two missed heartbeats is a stuck loop, not a slow upstream — the
        # same slack the service itself applies before reporting unhealthy.
        stale_after = heartbeat * 2
        severity = "critical" if svc["tier"] == "critical" else "warning"

        rules.append({
            "alert": "AlertServiceDown",
            "expr": f'up{{service="{name}"}} == 0',
            "for": "2m",
            "labels": {"severity": severity, "service": name},
            "annotations": {
                "summary": f"{name} is not being scraped",
                "runbook": "docs/runbooks/service-down.md",
            },
        })
        rules.append({
            "alert": "AlertServiceHeartbeatStale",
            "expr": (
                f'time() - alert_heartbeat_timestamp_seconds{{service="{name}"}} '
                f"> {stale_after}"
            ),
            "for": "1m",
            "labels": {"severity": severity, "service": name},
            "annotations": {
                "summary": f"{name} heartbeat is older than {stale_after}s",
                "description": "The process is up but its poll loop has stopped advancing.",
                "runbook": "docs/runbooks/service-down.md",
            },
        })
        rules.append({
            "alert": "AlertServiceNotPolling",
            "expr": (
                f'time() - alert_last_success_timestamp_seconds{{service="{name}"}} '
                f"> {max(svc['interval'] * 5, 600)}"
            ),
            "for": "5m",
            "labels": {"severity": severity, "service": name},
            "annotations": {
                "summary": f"{name} has not completed a successful poll recently",
                "runbook": "docs/runbooks/service-down.md",
            },
        })
        rules.append({
            "alert": "AlertDeliveryFailing",
            "expr": (
                f'increase(alert_delivery_failures_total{{service="{name}"}}[15m]) > 3'
            ),
            "for": "5m",
            "labels": {"severity": severity, "service": name},
            "annotations": {
                "summary": f"{name} is failing to deliver alerts to Telegram",
                "description": "Detections are happening but not reaching the channel.",
                "runbook": "docs/runbooks/service-down.md",
            },
        })
    return {"groups": [{"name": "alert-platform", "rules": rules}]}


def build_dashboard(services: list[dict], fleet: dict) -> dict:
    """A single overview dashboard. Panels are per-fleet, not per-service, so
    adding a service does not require adding a panel."""
    panels = []
    y = 0

    panels.append({
        "type": "stat", "title": "Services up",
        "gridPos": {"h": 4, "w": 6, "x": 0, "y": y},
        "targets": [{"expr": 'sum(alert_up{job="alert-platform"})', "refId": "A"}],
    })
    panels.append({
        "type": "stat", "title": "Alerts sent (24h)",
        "gridPos": {"h": 4, "w": 6, "x": 6, "y": y},
        "targets": [{"expr": 'sum(increase(alert_alerts_sent_total[24h]))', "refId": "A"}],
    })
    panels.append({
        "type": "stat", "title": "Delivery failures (24h)",
        "gridPos": {"h": 4, "w": 6, "x": 12, "y": y},
        "targets": [{"expr": 'sum(increase(alert_delivery_failures_total[24h]))', "refId": "A"}],
    })
    panels.append({
        "type": "stat", "title": "Poll errors (1h)",
        "gridPos": {"h": 4, "w": 6, "x": 18, "y": y},
        "targets": [{"expr": 'sum(increase(alert_poll_errors_total[1h]))', "refId": "A"}],
    })
    y += 4

    panels.append({
        "type": "timeseries", "title": "Heartbeat age by service",
        "description": "Rising line = the poll loop has stopped advancing.",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": y},
        "targets": [{
            "expr": "time() - alert_heartbeat_timestamp_seconds",
            "legendFormat": "{{service}}", "refId": "A",
        }],
    })
    panels.append({
        "type": "timeseries", "title": "Poll duration by service",
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": y},
        "targets": [{
            "expr": "alert_last_poll_duration_seconds",
            "legendFormat": "{{service}}", "refId": "A",
        }],
    })
    y += 8

    panels.append({
        "type": "timeseries", "title": "Alerts sent per hour",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": y},
        "targets": [{
            "expr": "sum by (service) (increase(alert_alerts_sent_total[1h]))",
            "legendFormat": "{{service}}", "refId": "A",
        }],
    })
    panels.append({
        "type": "timeseries", "title": "Items examined per hour",
        "description": "Throughput. A flat line with a healthy heartbeat means "
                       "the upstream went quiet, not that the service died.",
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": y},
        "targets": [{
            "expr": "sum by (service) (increase(alert_items_seen_total[1h]))",
            "legendFormat": "{{service}}", "refId": "A",
        }],
    })
    y += 8

    panels.append({
        "type": "table", "title": "Deployed version by service",
        "description": "The `ref` label comes from the deploy, so this answers "
                       "'what is running?' without SSH.",
        "gridPos": {"h": 6, "w": 24, "x": 0, "y": y},
        "targets": [{"expr": "alert_up", "instant": True, "format": "table", "refId": "A"}],
    })

    return {
        "title": f"{fleet['metadata']['name']} — alert platform",
        "uid": "alert-platform",
        "tags": ["alert-platform", "generated"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
        "description": (
            "Generated by tools/gen_observability.py from fleet/services/*.yaml. "
            "Edit the specs, not this file."
        ),
    }


def write(path: Path, content: str, check: bool) -> bool:
    """Returns True if the file is stale (in check mode) or was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else None
    if check:
        if existing != content:
            print(f"STALE: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            return True
        return False
    if existing != content:
        path.write_text(content)
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    else:
        print(f"unchanged {path.relative_to(REPO_ROOT)}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the generated files are out of date")
    args = parser.parse_args()

    fleet = load_fleet()
    services = load_services()

    stale = False
    stale |= write(OUT_PROM,
                   json.dumps(build_targets(services, fleet), indent=2) + "\n", args.check)
    stale |= write(OUT_RULES,
                   "# Generated by tools/gen_observability.py — do not edit.\n" +
                   yaml.safe_dump(build_rules(services), sort_keys=False, width=100),
                   args.check)
    stale |= write(OUT_DASH,
                   json.dumps(build_dashboard(services, fleet), indent=2) + "\n", args.check)

    if stale:
        print("\nRun `python3 tools/gen_observability.py` and commit the result.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
