# Fleet spec design (alertplatform/v1)

This document records what the spec format is, and more importantly *why* —
the decisions here constrain every later phase of the platform.

## Model

The fleet is described declaratively: specs state desired state, never
procedure. Two document kinds exist, both versioned under
`apiVersion: alertplatform/v1`:

- **Fleet** (`fleet/fleet.yaml`) — one per repo. Global metadata, deploy
  targets, fleet-wide defaults, and change policy.
- **Service** (`fleet/services/*.yaml`) — one per managed service. Identity,
  source pin, behaviour, delivery, health contract, resource overrides.

The effective config for a service is `deep_merge(fleet.defaults, service.spec)`
with these merge semantics, chosen for predictability over cleverness:

| Value type | Behaviour |
|---|---|
| scalar | service value replaces default |
| map | deep-merged, key by key |
| list | replaced wholesale — never concatenated |

List concatenation was deliberately rejected: it makes the effective value
impossible to determine from reading one file, and "which file contributed
this element?" becomes an incident-time question.

## Decisions and rationale

### D1. Names are immutable identity
`metadata.name` keys everything downstream: the systemd unit name, the state
directory, metrics labels, audit log entries. Renaming a service is defined
as *deleting one service and creating another* — because silently carrying
state across a rename is how you end up with orphaned SQLite files and
dashboards pointing at nothing.

### D2. Source refs must be semver tags
`spec.source.ref` rejects branches by schema. A deploy of `main` is not
reproducible and cannot be rolled back to, so it is not a legal desired
state. This single constraint is what makes Phase 4 rollback trivial:
rollback is just "apply the previous spec," and the previous spec fully
determines the previous code.

### D3. Secrets are names, never values
Specs reference secrets by name (`bot_token_secret: tg_bot_token`); the
apply engine resolves names against the configured backend (AWS SSM under
`/alert-platform/prod`) at deploy time. Enforced twice: the schema constrains
secret fields to a name pattern, and the validator scans *every* string in
*every* file for credential-shaped values (Telegram token shape, AWS key IDs,
PEM headers) as a second net for mistakes the schema can't see.

### D4. The health contract is declared now, consumed later
Each service declares `heartbeat_interval_sec` and a metrics port even though
no component reads them yet. Phase 3 validation gates and Phase 5 telemetry
will consume these fields rather than introducing their own config. The
alternative — each phase growing its own settings file — is how platforms
end up with five sources of truth.

Metrics ports live in a reserved fleet range (9100-9199). Range membership
is schema-enforced per file; *uniqueness* is a fleet invariant checked by
the validator, since no single-file schema can see across files.

### D5. `additionalProperties: false` almost everywhere
Unknown fields are errors, not passengers. A typo like `restart_polcy` should
fail validation, not silently deploy with the default. The one exception is
`spec.polling`, which allows service-specific keys (form lists, value
thresholds) because polling behaviour is legitimately per-service — this is
the spec's designated extension point, and keeping it to one place is the
point.

### D6. Validation is layered
1. **Schema** (`schema/service.schema.json`) — structure, types, ranges,
   patterns. Cheap, per-file, runs anywhere.
2. **Fleet invariants** (`tools/validate.py`) — cross-file rules: unique
   names, unique ports, no secret values. Requires the whole fleet.

Both run locally via `python3 tools/validate.py`, and the same entry point
becomes the first gate of the deploy pipeline in Phases 2-3.

## What Phase 1 deliberately does not do

- **No apply engine.** The spec describes; nothing yet reconciles. That is
  Phase 2, and keeping it out of scope here is what keeps the spec honest —
  a spec designed alongside its executor tends to leak procedure.
- **No multi-host scheduling.** `targets` models the current single EC2
  host. The shape leaves room for more targets without a format break.
- **No templating.** No Jinja, no anchors-as-inheritance. Four services do
  not justify a template layer, and templated YAML is harder to review —
  the reviewability of a plain diff is a feature the posting explicitly
  values ("changes easier to review, validate, audit").

## Compatibility policy

Any breaking change to the spec format requires a new `apiVersion`
(`alertplatform/v2`) and a migration note here. Additive optional fields
may land in v1.

---

## Addendum: what later phases changed

Phase 1 ended with a spec nothing consumed. The following phases turned each
declaration into something with a reader; this section records where, so the
rationale above stays connected to the implementation.

| Decision | Consumer added |
|---|---|
| D1 — names are identity | unit name, state dir, env file, metrics label, audit key — all derived in `internal/engine/unit.go` |
| D2 — semver-only refs | pre-flight `git rev-parse` gate; rollback is an ordinary apply of a previous ref |
| D3 — secrets are names | `internal/engine/secrets.go` resolves them on the host at deploy time; plans and audit entries carry names only |
| D4 — health contract | post-deploy gate polls `/healthz`; `tools/gen_observability.py` derives alert thresholds from `heartbeat_interval_sec` |
| D5 — `spec.polling` extension point | secondary poll cadences and Form 4 thresholds now live here rather than in code |
| D6 — layered validation | `tools/validate.py` is invoked directly by the apply engine, not reimplemented in Go |

One decision was reconsidered. `additionalProperties: true` on `spec.polling`
was described as the spec's designated extension point, and it carried more
weight than expected: three services needed per-service knobs (secondary poll
intervals, value thresholds), and all of them fitted without a schema change.
The looseness that looked like a compromise turned out to be the reason the
migration needed no `apiVersion` bump.

The compatibility policy is unchanged: breaking changes require
`alertplatform/v2` and a migration note here. Everything the later phases
added was additive and optional, so the specs remain `alertplatform/v1`.
