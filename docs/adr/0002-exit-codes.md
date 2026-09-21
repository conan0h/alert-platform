# ADR 0002 — A finding and a failure get different exit codes

**Status:** accepted, 2026-09-21
**Supersedes nothing. Changes an observable contract of `alertctl`.**

## Context

`alertctl drift` exited 1 when it found drift. The comment above that line
already knew this was uncomfortable:

> Non-zero exit so a scheduled run can page. Drift is not an error in the CLI
> sense — it is a finding — but exit codes are the only thing a cron job or CI
> check reliably reads.

Half right. Exit codes *are* the only thing a scheduled caller reliably reads,
which is why they need to carry more than one bit. A `drift` that fails to run
— host unreachable, spec that will not load, git checkout wedged — also exited
1, so the two states that most need distinguishing were indistinguishable.

This stopped being theoretical on 2026-09-21, when the read path reached the
host for the first time. `observe.yml` runs hourly. The fleet is two releases
behind its specs, so `drift` finds drift, so the hourly job is red — and stays
red until a deploy. The composite action failed the run on any non-zero exit,
so the only reader of that signal was being taught, once an hour, that red
means nothing.

That is the failure mode this repository exists to argue against. A platform
that ships symptom-based alerting and an SLO story cannot have a permanently
red heartbeat in its own observability path.

## Decision

`alertctl` has four documented exit codes, named as constants in
`cmd/alertctl/main.go`:

| Code | Meaning |
|---|---|
| 0 | success, nothing to report |
| 1 | the command failed to do its job |
| 2 | the caller asked for something that is not a command |
| 3 | the command worked and found what it looks for |

`drift` returns 3 when it finds drift. It still exits non-zero, so a caller
that only checks `if ! alertctl drift` behaves exactly as before — every
existing claim in the docs said "non-zero", never "1".

Callers declare which codes are findings rather than inferring it. The
`ssm-run` action takes `finding-exit-codes`; `observe.yml` passes `3`, and a
finding annotates the run with `::warning` and passes. `deploy.yml` passes
nothing, so on the write path every non-zero exit still fails the run.

## Alternatives rejected

**Make `drift` exit 0 and put the finding in stdout.** A caller would have to
parse output to know whether the fleet is healthy, which is worse than an exit
code in every direction, and a human running `alert-deploy drift` over Session
Manager would lose the signal entirely.

**Have the workflow parse `drift`'s output instead.** Same objection, plus it
puts the fleet's health behind a regex over a rendered plan.

**Keep exit 1 and make `observe.yml` always pass.** This is the tempting one,
because it is a one-line change. It also throws away the ability to notice that
the host is unreachable — which is a genuine outage — in order to silence a
finding that is not one.

**Give every verb its own finding code.** Nothing needs it yet. `health` is the
obvious next candidate and can take 3 with the same meaning when it does.

## Consequences

- SSM still records a finding invocation as `Failed`, because SSM's model has
  only succeeded and failed. The run log prints `status: Failed (host exit 3)`
  and the workflow passes with a warning. Anyone reading the AWS console
  directly will see `Failed`; that is a limitation of SSM, noted in the
  observe runbook.
- An hourly observe run now goes red for an unreachable host, a wedged
  checkout, or a `drift` that could not run — and only for those. Drift itself
  shows up as a warning annotation on a passing run.
- The exit codes are now a contract with two consumers, so they are tested as
  one: `cmd/alertctl/exitcode_test.go` runs the built binary and asserts each
  code, and asserts that a finding and an error can never collide.
- `verdict.sh` was split out of `action.yml` so CI can test it at all. The
  lesson behind that is in `.claude/learnings.md`: a script on the path to
  production that CI cannot run is a script nobody has tested.
