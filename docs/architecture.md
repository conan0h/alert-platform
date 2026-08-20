# Architecture

How the pieces fit, and why the seams are where they are.

## Two planes

    ┌──────────────────────────── control plane (Go) ──────────────────────────┐
    │                                                                          │
    │   fleet/fleet.yaml ─┐                                                     │
    │                     ├─> deep merge ─> effective config ─┬─> systemd unit  │
    │   fleet/services/*.yaml                                 └─> environment   │
    │                     │                                                     │
    │                     └─> tools/validate.py  (gate 1, shared with CI)       │
    │                                                                          │
    │   alertctl plan ──> observe host ──> diff ──> plan.json (fingerprinted)   │
    │   alertctl apply ─> gates ─> deploy ─> post-deploy gate ─> audit / rollback│
    └──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │  ssh + systemd
                                      ▼
    ┌───────────────────────────── data plane (Python) ────────────────────────┐
    │                                                                          │
    │   services/alertlib/   config · logging · state · delivery · health       │
    │        ▲        ▲          ▲            ▲                                 │
    │        │        │          │            │                                 │
    │   clinical_trials  edgar_mna  fda_catalysts  form4_insider                │
    │                                                                          │
    │   stdout (JSON) ─> journald        :91xx /healthz  /metrics               │
    └──────────────────────────────────────────────────────────────────────────┘

The control plane never contains business logic and the data plane never
contains deployment logic. The only thing crossing the boundary is an
environment contract, documented in `services/alertlib/config.py` and
rendered by `internal/engine/unit.go` — and tested from both sides by
`services/tests/test_contract.py`, because a contract nobody checks is a
comment.

## The environment contract

`alertctl` resolves the effective config, renders `/etc/alert-platform/<svc>.env`
(0640, root:svc-alerts), and systemd hands it to the process. The service
reads it through `Service.from_env()` and reads nothing else — no config
file, no `.env`, no hardcoded path.

| Source in the spec | Environment variable | Read by |
|---|---|---|
| `metadata.name` | `ALERT_SERVICE_NAME` | logging labels, state dir |
| `polling.interval_sec` | `ALERT_POLL_INTERVAL_SEC` | poll loop |
| `polling.<other>` | `ALERT_POLLING_<KEY>` | service-specific behaviour |
| `health.metrics.port` | `ALERT_METRICS_PORT` | health server |
| `health.heartbeat_interval_sec` | `ALERT_HEARTBEAT_INTERVAL_SEC` | liveness |
| `state.dir` + name | `ALERT_STATE_DIR` | SQLite location |
| `delivery.rate_limit_per_min` | `ALERT_RATE_LIMIT_PER_MIN` | Telegram bucket |
| `*_secret: <name>` | `ALERT_SECRET_<NAME>` | resolved value |
| `source.ref` | `ALERT_DEPLOYED_REF` | log and metric labels |

Adding a field is a three-line change: schema, `RenderEnv`, `ServiceConfig`.
The contract test then fails until all three agree, which is the point.

## Host layout

    /opt/alert-platform/<service>/
        releases/v0.1.0/          full repo checkout at the pinned tag
        releases/v0.1.1/          previous releases (3 kept)
        current -> releases/...   atomic symlink; WorkingDirectory
        deployed.json             manifest: ref, hashes, who, when, plan id
    /etc/alert-platform/<service>.env      resolved secrets, 0640
    /etc/systemd/system/alert-<service>.service
    /var/lib/alert-platform/<service>/     SQLite state, backed up
    /var/log/alert-platform/audit.jsonl    append-only deploy history

`current` is flipped with `ln -sfn` + `mv -Tf` so a restart can never observe
a half-swapped tree. Releases are pruned to three: enough to roll back
through a bad day, few enough not to fill the volume, and older tags are
always re-fetchable from the repo.

## Deploy lifecycle

1. **`plan`** — validate, resolve effective config, read `deployed.json` and
   `systemctl show` from the host, diff, fingerprint the result.
2. **Gate: validation** — `tools/validate.py`, the same command CI runs.
3. **Gate: freshness** — re-plan and compare fingerprints. A plan approved
   against different desired state is refused rather than applied.
4. **Gate: tag exists** — `git rev-parse` the ref before anything is touched.
5. **Deploy, one service at a time** — fetch tag, build venv, write env,
   write unit, flip `current`, `daemon-reload`, restart.
6. **Gate: post-deploy** — wait `startup_grace_sec`, require `active`, then
   poll `/healthz` until the heartbeat is fresh. A process that starts and
   then wedges fails this gate; a plain `systemctl` check would not notice.
7. **Audit** — one JSONL entry per service: actor, target, from/to ref,
   hashes, secret *names*, outcome, duration.
8. **Rollback on failure** — re-apply the previous ref, itself audited.

Standard-tier services deploy before critical-tier ones. If a shared change
is going to break something, it should break `clinical-trials` while the gate
can still stop the rollout, not `form4-insider`.

## Why rollback is boring

`source.ref` must be a semver tag (schema-enforced), so the previous spec
fully determines the previous code. Rollback is therefore an ordinary apply
of a different ref — same gates, same audit, same code path. There is no
rollback engine to be wrong, which is exactly what you want from the thing
that runs when everything else has already gone wrong.

`alertctl rollback` deliberately rewrites the spec rather than deploying
behind the repo's back. A rollback that skipped the spec would leave the repo
claiming a version that is not running, and the next apply would silently
roll forward again — reintroducing the outage.

## Failure modes and where they surface

| Failure | Detected by | Response |
|---|---|---|
| Bad spec | `validate` (local, CI, gate) | deploy refused |
| Missing tag | pre-flight `git rev-parse` | deploy refused |
| Stale plan | fingerprint mismatch | deploy refused |
| Missing secret | resolver, before restart | deploy refused |
| Service crash loop | post-deploy gate (`is-active`) | auto-rollback |
| Process up, loop wedged | post-deploy gate (`/healthz` 503) | auto-rollback |
| Manual edit on the host | `alertctl drift` | non-zero exit, alert |
| Upstream 502 in steady state | `alert_poll_errors_total` | dashboard, no page |
| Telegram rejecting sends | `AlertDeliveryFailing` rule | page |

The distinction in the last two rows is the one worth keeping: a polling
service that sees an upstream error is doing its job; a service that cannot
deliver is silently useless, which is worse than being obviously down.

## What is still deliberately absent

- **Multi-host scheduling.** `targets` models one EC2 host. The shape leaves
  room; nothing pretends to be a scheduler.
- **A queue between detection and delivery.** Alerts are sent inline. At this
  volume a queue would add a failure mode without removing one.
- **Config templating.** Four services do not justify a template layer, and
  the reviewability of a plain YAML diff is a feature.
- **Secrets in the control plane.** Values are resolved on the host, by the
  host's own instance role. The operator's terminal never sees them.
