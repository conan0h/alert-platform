# alert-platform

[![ci](https://github.com/conan0h/alert-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/conan0h/alert-platform/actions/workflows/ci.yml?query=branch%3Amain)

Four Python alerting services — SEC EDGAR M&A filings, FDA catalysts,
ClinicalTrials.gov updates, Form 4 insider trades — used to be hand-operated
on a single EC2 box: `git pull`, edit a unit file, `systemctl restart`, watch
`journalctl` for a minute, hope. Config lived in `.env` files that only
matched the running process by accident. There was no record of what changed,
no way to tell whether the box still matched the repo, and rolling back meant
remembering what the previous version was.

This repo is the platform layer that replaced that. Specs declare desired
state; `alertctl` reconciles it — planned first, gated before and after,
audited, and rolled back automatically when a deploy fails to come up healthy.

![Control plane and data plane](docs/img/architecture.svg)

## What changed

| | Before | After |
|---|---|---|
| Deploy | ~8 manual steps per service, from memory | `alertctl plan` → review → `alertctl apply` |
| Config | `.env` files, drifting from the repo | one versioned spec per service, schema-validated |
| Secrets | committed to git | referenced by name, resolved on the host at deploy time |
| Verification | tail the logs and hope | unit must be `active` **and** `/healthz` heartbeat fresh |
| Failure | notice it later, fix by hand | automatic rollback to the previous tag, run stops |
| History | shell history, if that | append-only audit log: who, what, from→to, outcome |
| Drift | invisible | `alertctl drift` exits non-zero |

## What it refuses to do

The parts worth reviewing are the refusals, not the happy path:

- **Apply a stale plan.** Plans are fingerprinted by content; `apply` re-plans
  and compares before touching anything. A plan reviewed on Monday cannot be
  applied on Friday against edited specs.
- **Deploy a branch.** `spec.source.ref` must be a semver tag — schema-enforced,
  and re-checked at the point it reaches `git`. A deploy of `main` is not
  reproducible, so it is not a legal desired state.
- **Accept a secret value in a spec.** The schema constrains secret fields to
  name patterns, and the validator scans every string in every file for
  credential-shaped values as a second net.
- **Call a deploy successful because the process started.** The post-deploy
  gate requires a fresh heartbeat from `/healthz`, which is what catches a
  service that starts and then wedges.
- **Mutate the host during `--dry-run`.** A command qualifies as read-only
  only if it starts with a known-safe verb *and* contains no chaining and no
  privilege escalation — `test -d X || sudo git clone ...` does not qualify.
- **Change anything from the web console.** It is read-only by construction.

## The deploy lifecycle

![Deploy lifecycle: three pre-flight gates, sequential deploy, post-deploy health gate, and the rollback branch](docs/img/deploy-lifecycle.svg)

Gates 1–3 run before the host is touched at all. Gate 4 is the one that
distinguishes "the process started" from "the service is actually working".
Standard-tier services deploy before critical-tier ones, one at a time: if a
shared change is going to break something, it should break `clinical-trials`
while the gate can still stop the rollout.

## The operator console

`alertctl serve` puts a read-only view on the same engine the CLI drives —
fleet overview, the live change set, and the audit trail on one page.

![The operator console](docs/img/console.png)

It has no data model of its own: every panel calls `fleet.Load`,
`engine.Observe`, `engine.BuildPlan` or `audit.History`. There is nothing to
keep in sync, and no write path — changes go through `plan` → review →
`apply`, where the gates and the audit record live. It binds to loopback;
reach a remote target the way the platform already does:

    ssh -L 8600:127.0.0.1:8600 ec2-alerts-prod

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
    internal/console/         read-only operator console

    services/alertlib/        shared runtime: config, logging, state, delivery, health
    services/<name>/main.py   the four services, at the paths their specs declare
    services/tests/           unit, bootstrap, and cross-language contract tests

    deploy/                   generated scrape targets, alert rules, dashboard
    deploy/ops/               the host-side deploy wrapper and its bootstrap
    infra/terraform/          OIDC provider, IAM roles, SSM documents (never applied by CI)
    docs/                     spec rationale, architecture, migration, ADRs, runbooks

## Quick start

    make deps
    make check                # lint + validate + both test suites + generated config
    make build                # bin/alertctl

    ./bin/alertctl plan -out plan.json
    ./bin/alertctl apply -plan plan.json
    ./bin/alertctl serve      # http://127.0.0.1:8600

`make check` is exactly what CI runs. `tools/validate.py` is also the first
gate of every deploy, so a spec that validates locally will not be rejected at
apply time.

To see the whole flow without a host, `-target dry` records every command
instead of executing it.

## Commands

| Command | Does |
|---|---|
| `alertctl validate` | schema + fleet invariants |
| `alertctl plan` | diff desired against observed, fingerprinted |
| `alertctl apply` | reconcile, with gates, audit, and auto-rollback |
| `alertctl status` | what is deployed, and is it running |
| `alertctl drift` | exit 3 if the host no longer matches the specs ([exit codes](docs/adr/0002-exit-codes.md)) |
| `alertctl rollback` | rewrite a spec to its last successful ref |
| `alertctl render` | print the unit and env a service would get |
| `alertctl history` | read the audit log |
| `alertctl serve` | read-only operator console |

`render` is safe to share: secrets appear as `<resolved-at-apply>`.

## Tests

Both planes are tested, and so is the seam between them:

| Suite | Covers |
|---|---|
| `internal/fleet` | merge semantics, effective config, typed accessors |
| `internal/engine` | unit/env rendering, planning, fingerprinting, **failed deploy → automatic rollback → audit** |
| `internal/exec` | the dry-run read-only guarantee |
| `internal/audit` | append-only, corruption tolerance, rollback ref selection |
| `internal/console` | read-only enforcement, spec-only rendering, API shape |
| `services/tests` | alertlib units, service bootstrap, **cross-language env contract** |

The rollback test drives the real `Apply` loop — pre-flight gates included —
against a temporary git repo and a runner that fails one command, then asserts
the service was returned to its previous ref and that both the failure and the
rollback reached the audit log. It is the platform's central claim, executable.

Linting is `golangci-lint` (errcheck, staticcheck, gosec, bodyclose and
others) and `ruff`; both run in CI alongside a `gofmt` check.

## Docs

- [Architecture](docs/architecture.md) — the two planes, the environment
  contract, host layout, failure modes, portability, log shipping
- [Spec design](docs/spec.md) — the six decisions that constrain every phase
- [Migration record](docs/migration.md) — what moved, what the specs got wrong,
  and what to do before the first deploy
- [ADR 0001](docs/adr/0001-oidc-ssm-over-ssh-keys.md) — why production is
  reached over OIDC and SSM rather than an SSH key in CI, and why the agent
  that operates this fleet holds no credentials at all

## Runbooks

- [Deploy a change](docs/runbooks/deploy.md)
- [Roll back a service](docs/runbooks/rollback.md)
- [A service is down or silent](docs/runbooks/service-down.md)
- [Rotate a secret](docs/runbooks/secret-rotation.md)
- [Run a service locally](docs/runbooks/local-development.md)

## Status

Built and under test — all of it verifiable from this repository:

- [x] Phase 1 — declarative fleet spec + validation
- [x] Phase 1.5 — migrate services onto the platform runtime ([`docs/migration.md`](docs/migration.md))
- [x] Phase 2 — plan/apply deploy workflow with audit log (Go CLI)
- [x] Phase 3 — pre/post-deploy validation gates
- [x] Phase 4 — versioned rollback tooling
- [x] Phase 5 — Prometheus metrics + Grafana dashboards
- [x] Phase 6 — runbooks, drift detection, architecture docs
- [x] Phase 7 — read-only operator console
- [x] Phase 8 — gated deploy pipeline: GitHub OIDC → IAM → SSM, so a deploy
      runs from `main` with no stored credentials anywhere and every apply
      lands in the audit log against the workflow run that caused it. Live
      since 2026-09-21, and both halves have now run against the real host —
      the write path made its first deploy the same day. See
      [ADR 0001](docs/adr/0001-oidc-ssm-over-ssh-keys.md).

### Production, as observed on 2026-09-22

Everything below came from `observe.yml` runs against the live host. The run
logs are the record; no number here is estimated.

| Service | Deployed ref | Unit | Health | Deployed | By |
|---|---|---|---|---|---|
| `clinical-trials` | `v0.1.0` | active | `ok` | 2026-08-20 | `ubuntu` |
| `edgar-mna` | `v0.1.0` | active | `ok` | 2026-08-20 | `ubuntu` |
| `fda-catalysts` | `v0.1.0` | active | `ok` | 2026-08-20 | `ubuntu` |
| `form4-insider` | `v0.2.0` | active | `ok` | 2026-09-21 | `gha:35661685161` |

`drift` reports no drift: every service is at the ref its spec pins.

**`form4-insider` is the first service this pipeline ever deployed.** On
2026-09-21 at 22:16 UTC, `deploy.yml` applied plan `54993f27b007` — one service,
health gate passed, 88 seconds, nothing rolled back. The audit actor is
`gha:35661685161`, the workflow run id, which is what the whole OIDC chain
exists to produce: a change to production attributable to a commit and a run
rather than to a person on a login shell. Every `by: ubuntu` above is a change
made by hand in August, before the pipeline existed.

That deploy carried a fix for a duplicate-alert bug: `form4-insider` sent its
Telegram messages before recording the dedup row, so a locked SQLite database
meant the same filings were re-sent every cycle. It now records first and
refuses to send if the record will not persist
([incident](docs/incidents/2026-09-21-form4-duplicate-alerts.md)).

Two earlier questions are closed. Secret resolution works — the apply passed
the gate the August attempt failed on, so the instance role was the fix. And
the August rollbacks logged `failed` were accurate rather than buggy:
`applyService` resolves secrets before its first mutating step, and the
rollback path calls the same function, so both passes returned having changed
nothing. Pinned by `internal/engine/secretgate_test.go`.

### Known gaps

Stated because they are real, not because they are planned away.

- **One `fda-catalysts` feed is dead.** `FiercePharma` returns 403 on every
  poll cycle and is reported presumed dead. `EndpointsNews`, which had failed
  the same way since August, answers again: the host reported `14/15 sources
  healthy` on 2026-09-23 with `FiercePharma` alone failing. The per-destination
  User-Agent in `v0.3.0` is the only change that could account for it, which
  makes the User-Agent the likely cause for that feed and leaves `FiercePharma`
  a genuinely dead or IP-blocked endpoint.
- **`clinical-trials` examines hundreds of trials per cycle and alerts on
  none.** Measured rather than inferred, since `v0.5.0` shipped the funnel: the
  fetch reads its whole match (`787 of 787 ... in 4 page(s)` on 2026-09-23,
  `686 of 686` on 2026-09-24, tracking the two-day window), every trial is
  already known, and `changed=0` — none of them has a status different from the
  stored one. `detect_signal` fires only on a status transition, so there is
  nothing to fire on. Whether to widen what counts as an event is a
  signal-quality decision, not a bug ([ADR 0006](docs/adr/0006-candidate-funnel.md)).
- **The alert archive has no reader yet.** Since the `v0.4.0` deploy on
  2026-09-23 every alert is recorded to an append-only table in the service's
  state directory ([ADR 0005](docs/adr/0005-alert-archive.md)), which closes the
  gap that blocked measuring signal quality. Getting those rows *out* — an
  `alerts` read verb and a console panel — needs the wrapper to accept a new
  read verb ([ADR 0004](docs/adr/0004-wrapper-adoption.md)). Until then the rows
  accumulate on the host and can only be read there.
- **Nothing backs the archive up.** `state.backup` is declared in the spec
  with no job behind it, and there is now data under `/var/lib/alert-platform/`
  whose loss would be irreversible rather than merely inconvenient.
- **`logs` returns the oldest part of its window.** The read verb captures
  roughly the first 24 KB of a one-hour journal, so a busy hour is truncated
  from the wrong end. Asking for a shorter window is the way around it.
- **Nothing scrapes `/metrics`.** The endpoint binds to the host's loopback and
  no Prometheus exists yet, so the counters are read from the journal instead:
  each service writes its whole registry there every 15 minutes and once at
  shutdown. That is a workaround for the missing scraper, not a replacement for
  one — two snapshots give a rate, a dashboard would give a history.
- **`observe` does not report which `alertctl` produced its answer.** Read
  verbs never rebuild the binary, so a stale control plane reads as current.
  This has misled two verifications.
- **`dedup.keys`** is declared in the spec but not consumed by the services.
- **`drift` compares refs and unit hashes only**, so an in-place edit inside a
  release directory is invisible to it.
- **Root account access keys are still in use.**
- **A host-side edit to `services/form4_insider/main.py` is not in git.** A
  traceback from the 2026-09-21 incident places a function four lines from where
  the repository has it, so the host has been running code that no commit
  contains.
