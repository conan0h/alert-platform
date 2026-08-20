#!/usr/bin/env python3
"""validate.py — validate the fleet spec.

Two layers of checking:

  1. Schema validation: every service YAML conforms to service.schema.json.
     Catches structural errors (missing fields, bad types, illegal values).

  2. Fleet invariants: rules that no single-file schema can express.
     Catches cross-file conflicts (duplicate names, duplicate metrics
     ports) and policy violations (secret-looking literals in specs).

Exit code 0 = fleet is valid. Non-zero = at least one violation, all of
which are printed. Designed to run locally, in CI, and as the first gate
of the Phase 2/3 deploy pipeline.

Usage:
    python3 tools/validate.py [--fleet-dir fleet]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET_LIKE = re.compile(
    r"(\d{6,}:[A-Za-z0-9_-]{30,}"      # telegram bot token shape
    r"|AKIA[0-9A-Z]{16}"               # AWS access key id
    r"|-----BEGIN)"                    # PEM material
)


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, source: str, msg: str) -> None:
        self.errors.append(f"[{source}] {msg}")

    def ok(self) -> bool:
        return not self.errors


def load_yaml(path: Path, report: Report) -> dict | None:
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        report.error(path.name, f"YAML parse error: {exc}")
        return None


def validate_schema(doc: dict, schema: dict, source: str, report: Report) -> None:
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in err.path) or "<root>"
        report.error(source, f"{where}: {err.message}")


def check_no_secret_values(doc: dict, source: str, report: Report) -> None:
    """Reject anything that looks like a real credential in a spec file."""
    def walk(node, trail=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{trail}.{k}" if trail else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{trail}[{i}]")
        elif isinstance(node, str) and SECRET_LIKE.search(node):
            report.error(source, f"{trail}: value looks like a real secret — specs may only reference secret NAMES")
    walk(doc)


def check_fleet_invariants(services: list[dict], report: Report) -> None:
    names: dict[str, str] = {}
    ports: dict[int, str] = {}
    for svc in services:
        name = svc["metadata"]["name"]
        if name in names:
            report.error("fleet", f"duplicate service name '{name}'")
        names[name] = name

        port = svc["spec"]["health"]["metrics"]["port"]
        if port in ports:
            report.error("fleet", f"metrics port {port} claimed by both '{ports[port]}' and '{name}'")
        else:
            ports[port] = name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-dir", default=str(REPO_ROOT / "fleet"))
    args = parser.parse_args()

    fleet_dir = Path(args.fleet_dir)
    report = Report()

    schema_path = REPO_ROOT / "schema" / "service.schema.json"
    schema = json.loads(schema_path.read_text())

    fleet_file = fleet_dir / "fleet.yaml"
    if not fleet_file.exists():
        report.error("fleet", f"missing {fleet_file}")
    else:
        fleet_doc = load_yaml(fleet_file, report)
        if fleet_doc is not None:
            check_no_secret_values(fleet_doc, "fleet.yaml", report)

    service_files = sorted((fleet_dir / "services").glob("*.yaml"))
    if not service_files:
        report.error("fleet", "no service specs found under fleet/services/")

    valid_services: list[dict] = []
    for path in service_files:
        doc = load_yaml(path, report)
        if doc is None:
            continue
        validate_schema(doc, schema, path.name, report)
        check_no_secret_values(doc, path.name, report)
        if isinstance(doc, dict) and doc.get("kind") == "Service":
            try:
                doc["metadata"]["name"]
                doc["spec"]["health"]["metrics"]["port"]
                valid_services.append(doc)
            except (KeyError, TypeError):
                pass  # schema errors already reported above

    check_fleet_invariants(valid_services, report)

    if report.ok():
        print(f"OK: {len(valid_services)} service spec(s) valid, fleet invariants hold.")
        return 0

    print(f"FAILED: {len(report.errors)} violation(s)\n", file=sys.stderr)
    for err in report.errors:
        print(f"  {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
