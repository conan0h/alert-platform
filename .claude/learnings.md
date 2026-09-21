# Learnings

Hard-won facts future runs should not have to rediscover. Append; don't
rewrite history.

- **SSH exit 255 is a transport failure, not an answer.** It was once read as
  "empty output", so `plan` saw an empty fleet and proposed creating every
  service. Fixed in `internal/exec/runner.go` (`checkSSHResult`, covered by
  `ssh255_test.go`). Any new remote read must distinguish "the command said
  nothing" from "the command never ran".
- **`alertctl` on the VM needs `sudo`.** Without it, reads of root-owned state
  come back empty and look like an empty fleet: the live reproduction of the
  255 class of bug.
- **Secrets resolve on the remote host, over SSH.** Local AWS env vars (even
  through `sudo -E`) are irrelevant; the host's credentials are what matter.
  Hence the IAM instance role.
- **Drift compares refs and unit hashes only.** In-place edits inside a
  release directory are invisible (backlog #6).
- **Tests that load the live `fleet/` specs are coupled to deployment
  decisions.** A ref bump in `e44bd09` broke two engine tests. Tests should own
  their fixtures.
- **Dependencies are vendored on purpose**: the control plane must build
  without network access during an incident. Always `go mod vendor`.
- **Validator and deploy gate are the same code** (`tools/validate.py`), so
  schema changes affect deploys immediately.
- **A test that skips when a dependency is missing is a test that lies.**
  `rollback_test.go` skips unless `pyyaml` and `jsonschema` import, so the
  ref-coupling bug in backlog #1 looked like one failing test locally and
  was two in CI. Prefer failing loudly over skipping when the dependency is
  something CI always has.
- **Engine tests own their fleet.** `internal/engine/testdata/fleet` pins
  `v1.0.0` forever. Never point an engine test at the repo's `fleet/`: those
  specs are deployment decisions, and rolling a ref is supposed to be
  routine. The one exception is `live_fleet_test.go`, which may assert only
  properties true of any valid fleet — no version, port, service name or
  count.
- **The routine's GitHub access can be read-only, and it fails late.** Reads
  (`get_me`, listing PRs and tags) all succeed while `git push`, branch
  creation, PR creation *and issue creation* return 403. So the §7 handoff
  route is not always available: if issues are refused too, the run
  notification is the only channel to Conan. Check for write access early in
  a run rather than discovering it after the milestone is built.
- **`.claude/` must be committed.** The directory arrived inside an uploaded
  zip rather than in git, so the log and learnings were invisible to a fresh
  clone — which is every run. Memory that is not in the repo does not exist.
- **A relative `$id` in a JSON Schema is a latent network call.** Old
  `jsonschema` resolvers (`RefResolver`, pre-4.18) join a local `$ref` onto
  the `$id` base and *fetch* the result, so `alertplatform/v1/service` plus
  `#/$defs/secretName` became a URL and blew up. Current versions resolve the
  fragment in-document and never notice, which is exactly why it survived: it
  only fails where an old resolver is installed. `$id` is now a URN. This
  matters beyond CI — `tools/validate.py` is gate 1 of every apply and runs
  on the host, against the host's library.
- **One red check can hide another.** `main` was red for two independent
  reasons, and the backlog recorded only the newer one. Fixing the refs just
  revealed the `$id` failure underneath. When CI has been red a while, read
  the *oldest* failing run, not the most recent: run #6 on 2026-08-20 already
  showed it, two commits before the ref bump everyone blamed.
- **The environment a test runs in is part of the test.** The `alertctl` CI
  job has no `pip install jsonschema`, so it exercises the validator against
  a bare distro Python — which is the closest thing in CI to how the gate
  actually runs on the host. That is a feature. Installing the dependency
  there would have hidden the bug rather than fixed it.
- **Check what the repo claims about itself, not just what it does.** The
  README asserted production state the repo had no way to observe, and gave a
  pre-deploy instruction that had been completed weeks earlier. Neither was
  caught by any test, because neither is testable. Re-read the front page
  against reality whenever the project's actual state moves.
- **A local `terraform fmt -check` is not validation.** It proves the HCL
  parses; it says nothing about whether a provider argument exists. Only
  `init` + `validate` with the provider downloaded does that, and this
  session's egress policy blocks the registry — so infra changes must be
  pushed and checked by CI, the same shape as `golangci-lint`.
- **Give a security wrapper a help verb that exits 0.** The first draft of
  `bootstrap-host.sh` inferred "sudo permitted this" from the wrapper's exit
  code, which conflated a sudo refusal with the wrapper rejecting arguments —
  different failures, different fixes. `sudo -n -l <cmd>` asks the real
  question without running anything.


## A deploy path is not tested until something runs it end to end
*Learned 2026-09-21, the expensive way.*

PR #4 shipped the whole OIDC → IAM → SSM → wrapper pipeline in one piece. Its
locally-testable half — argument validation in `alert-deploy` — had 45 tests.
Its other half — the Terraform, the workflows, `bootstrap-host.sh` — had none,
because nothing in CI could run them. Eight bugs followed in a week, seven of
them found by hitting them against the real host:

| # | Bug | Would an e2e target have caught it? |
|---|---|---|
| 1 | GitHub's OIDC subject embeds numeric ids | No — external system |
| 2 | `environment:` changes the subject claim | No — external system |
| 3 | Host Go was 1.18, `go.mod` needs 1.22 | Yes, if pinned to the host image |
| 4 | Only `plan` built `alertctl` | **Yes** |
| 5 | `go build` resolved the package against the wrong cwd | **Yes** |
| 6 | Ran in CloudShell by mistake | No — operator error, guarded instead |
| 7 | Bootstrap killed by its own post-build check | **Yes** |
| 8 | `observe.yml` scheduled to fail hourly before AWS existed | Caught by review |

Three of eight were plain "nobody ever ran this", and each cost a round trip
through a human on a phone. The two that no harness would have caught are the
interesting ones, and they share a shape: an assumption about an external
system that nothing in the repo could test. For those, the lesson is different
and already recorded — print the claim, not the token.

The rule this leaves: **if a script is on the path to production, CI runs it,
or it is untested — however many tests sit next to it.** A test count on the
half you could reach says nothing about the half you could not, and it reads
like coverage, which is worse than no tests at all because it stops you
looking.

## `set -euo pipefail` plus `cmd | head` is a trap
*Learned 2026-09-21, bug #7 above.*

`"$BIN/alertctl" 2>&1 | head -n 1` ended a bootstrap script three times.
Two separate mechanisms, either of which is enough:

- A CLI printing usage and exiting non-zero is *correct* behaviour, not a
  failure, but `set -e` cannot tell the difference.
- `head` closes the pipe after one line; the next write gets SIGPIPE, and
  `pipefail` promotes that to the pipeline's status.

It reads as a harmless "show me the first line" and it is a control-flow
statement. Worse, the banner prints before the script dies, so the output
looks like success — which is why it survived three runs. Capture into a
variable, tolerate the exit explicitly, and slice the first line in the shell:

    banner=$("$BIN/alertctl" 2>&1) || true
    log "${banner%%$'\n'*}"

The general version: under `pipefail`, every `| head`, `| grep -q` and
`| read` in a script is a place the script can exit. Audit them.

## One long-lived branch across several squash-merged PRs starts every PR dirty
*Learned 2026-09-21, three times in one session before it was named.*

The session's branch is fixed, so this run put six PRs through the same
`claude/…` ref. Each merged with squash, which rewrites the commits into one
new commit on `main`. The branch still holds the originals, so the next push
needs a force-push, and — the part that actually cost something — the next PR
opened against `main` is immediately `mergeable_state: dirty`, because the
branch carries both the pre-squash commit and its already-merged rewrite.

PR #15 opened that way and **CI never ran on it at all**. Not a red run: no
run. Fifteen minutes went into wondering why the runs list was stale before
looking at `mergeable_state`, which said `dirty` the whole time.

Two things to do differently:

- **Reset the branch onto `main` immediately after each merge**, before
  starting the next piece of work: `git fetch origin main && git checkout -B
  <branch> origin/main`. It costs nothing and the divergence never
  accumulates. CLAUDE.md's `claude/YYYY-MM-DD-<slug>` convention avoids this
  by giving each change its own ref; when the branch name is fixed by the
  session, resetting it is the equivalent.
- **When CI seems not to have run, read `mergeable_state` before blaming the
  API.** Absent checks and stale checks look identical from the check-runs
  endpoint, which has been genuinely stale in this session too — so the
  reflex is to wait it out. "No runs at all" is the tell: a real run appears
  within a minute or two, and anything longer means GitHub is not going to
  start one.
