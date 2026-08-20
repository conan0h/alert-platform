# alert-platform

Control plane for the market alert suite: four Python alerting services
(SEC EDGAR M&A, FDA catalysts, ClinicalTrials.gov, Form 4 insider trades)
running as systemd services on EC2, managed declaratively.

Specs describe desired state. `alertctl` reconciles it — planned first,
gated before and after, audited, and rolled back automatically when a
deploy fails to come up healthy.

## Layout

    fleet/fleet.yaml          fleet-wide defaults, targets, change policy
    fleet/services/*.yaml     one desired-state spec per managed service
    schema/                   JSON Schema for spec validation
    tools/validate.py         schema + fleet-invariant validator
    tools/gen_observability.py  Prometheus/Grafana config, generated from specs

    cmd/alertctl/             the control-plane CLI
    internal/fleet/           spec loading, deep merge, effective config
    internal/engine/          plan, apply, gates, unit rendering, secrets
    internal/audit/           append-only deploy log
    internal/exec/            ssh / local / dry-run command execution

    services/alertlib/        shared runtime: config, logging, state, delivery, health
    services/<name>/main.py   the four services, at the paths their specs declare
    services/tests/           unit, bootstrap, and cross-language contract tests

    deploy/                   generated scrape targets, alert rules, dashboard
    docs/                     spec rationale, architecture, migration, runbooks

## Quick start

    make deps
    make check                # validate + both test suites + generated config
    make build                # bin/alertctl

    ./bin/alertctl plan -out plan.json
    ./bin/alertctl apply -plan plan.json

`make check` is exactly what CI runs. `tools/validate.py` is also the first
gate of every deploy, so a spec that validates locally will not be rejected
at apply time.

## Commands

| Command | Does |
|---|---|
| `alertctl validate` | schema + fleet invariants |
| `alertctl plan` | diff desired against observed, fingerprinted |
| `alertctl apply` | reconcile, with gates, audit, and auto-rollback |
| `alertctl status` | what is deployed, and is it running |
| `alertctl drift` | non-zero exit if the host no longer matches the specs |
| `alertctl rollback` | rewrite a spec to its last successful ref |
| `alertctl render` | print the unit and env a service would get |
| `alertctl history` | read the audit log |

`render` is safe to share: secrets appear as `<resolved-at-apply>`.

## How a deploy is made safe

1. Specs validate against the schema and fleet invariants.
2. The plan is fingerprinted; a stale plan is refused, not applied.
3. Referenced git tags must exist before anything is touched.
4. Secrets resolve from SSM **on the host**, never through your terminal.
5. Services deploy one at a time, standard tier before critical.
6. After restart: the unit must be active *and* `/healthz` must report a
   fresh heartbeat — a process that starts and then wedges fails this.
7. A failed gate rolls the service back and stops the run.
8. Every step is recorded in `/var/log/alert-platform/audit.jsonl`.

See [`docs/architecture.md`](docs/architecture.md) for how the pieces fit and
[`docs/spec.md`](docs/spec.md) for why the spec format is shaped the way it is.

## Roadmap

- [x] Phase 1 — declarative fleet spec + validation
- [x] Phase 1.5 — migrate services onto the platform runtime ([`docs/migration.md`](docs/migration.md))
- [x] Phase 2 — plan/apply deploy workflow with audit log (Go CLI)
- [x] Phase 3 — pre/post-deploy validation gates
- [x] Phase 4 — versioned rollback tooling
- [x] Phase 5 — Prometheus metrics + Grafana dashboards
- [x] Phase 6 — runbooks, drift detection, architecture docs

Phases 2–6 are implemented and tested, but have not yet run against the
production host. Before the first deploy, work through
[**docs/migration.md → Before the first deploy**](docs/migration.md#before-the-first-deploy):
rotate the leaked Telegram token, populate SSM, cut the `v0.1.0` tag, prepare
the host, and migrate existing SQLite state.

Outstanding beyond that: `dedup.keys` is declared but not yet consumed by the
services, and `state.backup` is declared with no backup job behind it.

## Runbooks

- [Deploy a change](docs/runbooks/deploy.md)
- [Roll back a service](docs/runbooks/rollback.md)
- [A service is down or silent](docs/runbooks/service-down.md)
- [Rotate a secret](docs/runbooks/secret-rotation.md)
- [Run a service locally](docs/runbooks/local-development.md)
