# Run log

One entry per scheduled run, newest last. Keep entries short.

Format:

    ## YYYY-MM-DD — <item or "no-op">
    - Did: ...
    - PR: #N, merged / open (why) / needs-conan (or none, and why)
    - Verification: green / what failed
    - Next: what the next run should pick up
    - Notes: anything surprising (also add to learnings.md if durable)
    - Catch-up: one sentence Conan should read even if he reads nothing else

## 2026-09-21 — baseline (set up by hand, not a scheduled run)
- Did: added CLAUDE.md and .claude/ backlog, log, learnings.
- Verification on `e44bd09`: go vet, ruff, validate, observability check, and
  29 pytest tests pass; `internal/audit`, `console`, `exec`, `fleet` pass;
  `internal/engine` fails 2 tests (ref coupling, see backlog #1).
  golangci-lint not run.
- Next: backlog #1.
- Switched to full auto-merge behind required CI checks (CLAUDE.md §2).
- Brief revised: agent now releases and deploys through the gates as the
  `claude-ops` operator identity and reports on production every run; work is
  sized per run by milestone, not by diff size.
- Moved to a cloud routine. Production access is now GitHub Actions with
  OIDC → IAM → SSM; there are no SSH keys and no credentials in the agent
  (CLAUDE.md §6).

## 2026-09-21 — backlog P0 #1: make `main` green
- Did: **two** fixes, because `main` turned out to be red for two unrelated
  reasons — CI run #6 on 2026-08-20 already failed, two commits before the
  `e44bd09` ref bump the backlog blamed. The first fix was the known one:
  gave `internal/engine` its own fixture fleet
  (`internal/engine/testdata/fleet`, four fictional services pinned at
  `v1.0.0`) and moved every test in the package onto it, so rolling
  `source.ref` can no longer fail a test about rendering or deploy
  mechanics. Added `live_fleet_test.go`, which keeps the real `fleet/` under
  test using only properties true of any valid fleet. Documented the
  convention in `docs/runbooks/local-development.md`. The second fix was the
  hidden one: `schema/service.schema.json` had `$id: alertplatform/v1/service`,
  a bare relative path that old `jsonschema` resolvers dereference as a URL
  (`unknown url type: 'alertplatform/v1/alertplatform/v1/service'`). It only
  showed in the `alertctl` CI job, the one with no `pip install jsonschema`,
  where the rollback test shells out to `tools/validate.py` as the real apply
  gate and gets the distro package. `$id` is now `urn:alertplatform:v1:service`;
  `services/tests/test_schema.py` pins that it stays absolute and that every
  `$ref` stays local. Fixed in the schema rather than the workflow because
  that gate runs on the host at deploy time, against the host's library — a
  schema needing a fetch to validate is a gate that fails when the network is
  least trustworthy. Third commit restores
  `.claude/` (backlog, log, learnings) into git, where a fresh clone can see
  it, and drops the 1.9 MB `alert-platform-launchpad.zip` that carried it.
- PR: #1, **merged** (squash). Auto-merge is not enabled on this repository
  (Settings → General → Allow auto-merge), and CLAUDE.md §2 forbids changing
  repo settings, so it was merged by hand once all four checks were green —
  never on a pending or failing check. CI run #15 on `main` is green, the
  first since 2026-08-20.
  Blocked for part of the run: `git push` returned 403 and the GitHub API
  refused branch, PR *and issue* creation with "Resource not accessible by
  integration", while every read succeeded — so the §7 handoff could not be
  filed as an issue either and went out as the run notification instead.
  Conan granted the Claude GitHub App access to the repo mid-run and the
  push went through unchanged.
- Verification: green. `go vet`, `gofmt -l cmd internal` (empty),
  `go test ./... -race` (5/5 packages), `go build`, `ruff check`,
  `tools/validate.py` against both the live and the fixture fleet,
  `gen_observability.py --check`, `pytest services/tests -q` (31 passed).
  The `$id` fix was reproduced against `jsonschema==4.17.3` (the last
  `RefResolver` release, matching the runner) and re-verified under both
  resolver generations, including the whole Go suite with the old one first
  on `PATH` — the failing CI condition exactly. All four CI jobs green on the
  branch.
  `golangci-lint` not run locally — installed v2.5.0 vs. a v1-format config;
  backlog has it as an environment item. It did run in CI and passed. Also confirmed the fix does what it claims
  by bumping `edgar-mna` to `v9.9.9` locally: engine tests stayed green,
  which is precisely what `e44bd09` broke.
- Production report: **none possible.** `observe.yml` does not exist yet
  (backlog P0 #3), and the routine has no other read path to the host, so
  there is no evidence about the four services this run. CLAUDE.md §6 forbids
  deploying until that pipeline and its handoff are done, so nothing was
  deployed and no release was tagged. Nothing in the docs should claim
  otherwise.
- Next: P0 #2 (module path
  `conanohara` → `conan0h`) and P0 #3 (the OIDC → IAM → SSM pipeline), which
  is what unblocks every production claim in this repo. Also worth an early
  look: new backlog item 8a, pinning the validator's Python dependencies so
  the apply gate cannot fail on whatever `jsonschema` the host has.
- Notes: `rollback_test.go` skips when `pyyaml`/`jsonschema` are absent, so
  half of backlog #1 was invisible locally. Separately, the check-runs API
  reported the failing `alertctl` job as still running for ~9 minutes after
  it had failed; reading the job logs gave the truth. Both in learnings,
  along with the `$id` lesson and "one red check can hide another".
  Enabling auto-merge would remove a manual step from every future run, but
  it is Conan's call: it is a repo setting, which §2 puts out of bounds.
- Catch-up: `main` had been red since 2026-08-20 for two unrelated reasons,
  not one — PR #1 fixes both, and future releases can no longer break the
  engine tests. Still no production access, so this run makes no claims about
  the host.

## 2026-09-21 (second session, user-directed) — P0 #2, docs cleanup, P0 #3
- Did: three merged PRs.
  **#3** fixed the Go module path (`conanohara` → `conan0h`, 13 files) and
  retired documentation that had stopped being true: the README claimed
  phases 2–7 "have not yet run against the production host", which this repo
  cannot know either way, and told readers to cut a tag that has existed for
  weeks. It now states plainly that it reports no evidence about production
  and names the host's own status/drift/audit log as the sources of truth.
  **#4** built the whole deploy pipeline (P0 #3): Terraform for the OIDC
  provider, deploy role, instance role and two SSM documents; the host
  wrapper `deploy/ops/alert-deploy` with 45 tests for what it refuses;
  `bootstrap-host.sh`; `observe.yml` and `deploy.yml` with a shared composite
  action; ADR 0001; and two new CI jobs (shellcheck + wrapper tests,
  terraform fmt/validate). `alertctl` learned `ALERTCTL_ACTOR`.
- PR: #3 and #4, both merged after all checks green. Handoff issue **#5**
  filed — the first time the §7 route has actually worked, since issue
  creation was 403 in the previous session.
- Verification: green throughout. Notably `terraform validate` could not run
  locally (`registry.terraform.io` is 403 at this session's egress proxy), so
  I pushed and let the new CI job do it: it reported "Success! The
  configuration is valid" against the real AWS provider schema, which is the
  check that matters. Local `terraform fmt -check` only proves the HCL parses.
- Production report: **none, again, and now for the last time by design.**
  The pipeline exists as code but has never run against a host; that is
  blocked on handoff #5. Nothing in this run touched production.
- Next: when #5 is done, trigger `observe.yml` with `verb=status` — read-only
  by construction, so it proves the chain in the direction that cannot break
  anything — then `drift` and `health`, and write the first real Production
  report. Only then consider a deploy. After that: P0 #5 (the host-side
  form4_insider edit) and P1 #7 (content drift).
- Notes: the repo cleanup the user asked about turned out to be mostly done —
  the launchpad zip went in #1. Everything else in the tree is referenced or
  gitignored; the real staleness was in the docs.
- Catch-up: the deploy pipeline is built and merged, but it is code only.
  Issue #5 is the twenty minutes of CloudShell and Session Manager work that
  turns it on, and nothing about production can be verified until then.


## 2026-09-21 (third session) — the pipeline reaches the host; first Production report
- Did: got handoff #5 across the line with Conan working the AWS and host
  steps, then fixed the four bugs that stood between "the code is merged" and
  "the read path works against the real host". PRs **#8** (OIDC trust policy),
  **#9** (Go too old on the host), **#10** (only `plan` built `alertctl`),
  **#11** (`go build` needed `-C`), **#12** (refuse to bootstrap the wrong
  host), **#13** (bootstrap killed by its own post-build check). Five of those
  six were found by running the thing, not by reading it.
- PR: #8–#13, all merged after every check went green.
- Verification: green. `main` at `dac7254`.
- **Production report — the first one, from `observe.yml` runs #6–#9 against
  `i-06aaf8cca765d5352`, all on `main` @ `baa45bb`:**

      status   clinical-trials  v0.1.0  active  enabled  2026-08-20T11:46:25Z  ubuntu
               edgar-mna        v0.1.0  active  enabled  2026-08-20T11:53:10Z  ubuntu
               fda-catalysts    v0.1.0  active  enabled  2026-08-20T12:06:59Z  ubuntu
               form4-insider    v0.1.0  active  enabled  2026-08-20T12:12:43Z  ubuntu

      health   all four: unit=active  healthz=ok

      drift    all four UPDATE: source.ref v0.1.0 -> v0.1.2, plus an
               environment hash change each. "4 to change, 0 unchanged."
               Exit 1, which is drift's contract for "drift found".

      history  2026-08-20T19:53:02Z  apply     clinical-trials  failed  v0.1.0 -> v0.1.2  (ubuntu, 3.504s)
               2026-08-20T19:53:04Z  rollback  clinical-trials  failed  v0.1.2 -> v0.1.0  (ubuntu, 2.055s)
               2026-08-20T19:55:22Z  apply     clinical-trials  failed  v0.1.0 -> v0.1.2  (ubuntu, 1.508s)
               2026-08-20T19:55:24Z  rollback  clinical-trials  failed  v0.1.2 -> v0.1.0  (ubuntu, 1.51s)

  This confirms, from the host rather than from report, three things CLAUDE.md
  §10 could only carry second-hand: the fleet really is on `v0.1.0`, the
  `v0.1.2` apply really did fail, and every change to production so far was
  made by hand (`by: ubuntu`). No deploy was made this run.
- Two findings worth more than the confirmation:
  **(a)** The `v0.1.2` apply never got past `clinical-trials`. Both attempts
  failed on the first service and stopped, leaving the other three untouched.
  That is the blast-radius limit working, and it is the best evidence the repo
  has that the engine's ordering is not decoration.
  **(b)** Both rollbacks are logged `failed`, yet `clinical-trials` is active
  and healthy at `v0.1.0` — the ref those rollbacks were supposed to restore.
  Either rollback misreports its own outcome, or it failed and the service got
  back another way. The audit log is the evidence base for every production
  claim this project makes, so a status field that may be wrong about the
  safety mechanism is a real problem. New backlog **#20**, P0.
- Also found: `observe.yml` fails the whole run on any non-zero host exit, so
  `drift` finding drift is reported identically to the host being unreachable.
  The hourly schedule is now permanently red, and will stay red until the fleet
  is deployed — an alerting anti-pattern sitting in the observability path of a
  repo whose pitch is symptom-based alerting. New backlog **#21**, P0.
- Next: **#21** first (it is small, and every run until it lands has to explain
  a red hourly job), then **#20**, then the first real deploy through the
  pipeline to close the `v0.1.0` → `v0.1.2` drift. #20 before the deploy, not
  after: the rollback status is the thing that will report on whether that
  deploy was safe.
- Notes: the bug rate in the deploy path — eight, seven found by hitting them —
  has one cause, recorded in learnings: PR #4 shipped a path whose
  locally-testable half had 45 tests and whose AWS-and-host half had none.
  Backlog #17 is raised to P0 accordingly and renumbered #22.
  **Conan needs to run `bootstrap-host.sh` once more** now #13 has merged; the
  three earlier runs all died before installing the wrapper and the sudo rule.
- Catch-up: the pipeline can now see production, and what it sees is four
  healthy bots running code two releases behind their specs, last touched by
  hand in August. Re-run the bootstrap script once and the write path is ready
  to try.

## 2026-09-21 (third session, continued) — P0 #21: a finding is not a failure
- Did: PR **#15**. `alertctl` gained four named exit codes (0 nothing, 1 the
  command failed, 2 bad arguments, 3 the command worked and found something) and
  `drift`'s finding moved to 3. The ambiguity turned out to start inside
  `alertctl`, not in the workflow: `drift` exited 1 for a finding and the error
  path exited 1 too, so "the fleet has drifted" and "I could not find out
  whether the fleet has drifted" were the same code at the source. Callers now
  declare which codes are findings — `ssm-run` takes `finding-exit-codes`,
  `observe.yml` passes 3 and annotates instead of failing, `deploy.yml` passes
  nothing so the write path is unchanged. ADR 0002.
  The verdict logic moved out of `action.yml` into `verdict.sh`, which is the
  week's lesson applied rather than recorded: a script on the path to production
  that CI cannot run is untested. It also stops the finding set being spliced
  into a shell by `${{ }}`.
- Verification: gofmt clean, `go vet` clean, `go test ./... -race` all six
  packages, wrapper suite 56/56, new `verdict_test.sh` 16/16 — including that a
  declared finding passes while the same verb's exit 1 still fails, that codes
  compare exactly (30 does not match a finding set of 3), and that TimedOut
  with no exit code is never excused. Both suites wired into the `deploy path`
  CI job. `exitcode_test.go` runs the built binary per code.
- Notes: PR #15 opened `mergeable_state: dirty` and **CI never ran on it**,
  because six PRs this run went through one branch and each squash-merge left
  the pre-squash commit behind. Rebased onto `main` and force-pushed. Durable
  lesson in learnings, including the diagnostic: no CI runs at all means read
  `mergeable_state`, not the check-runs endpoint.
- Next: **#20**, the rollback status recorded `failed` on a rollback that looks
  to have worked. Then a release and the first deploy through the pipeline, in
  that order — #20 is the field that will report on whether that deploy was
  safe. `alertctl`'s exit codes changed, so the deploy needs a release tag.
- Catch-up: the hourly observe job will now be green with a warning while the
  fleet is behind its specs, and red only when the observation itself fails —
  which is the difference between a signal and noise.

## 2026-09-21 (third session, continued) — #21 merged; verified half, and a new gap
- Did: **#15** merged (exit codes, `verdict.sh`, ADR 0002). CI run 41 green on
  all six jobs, including the new `A finding is not a failure` step and
  `golangci-lint`.
- Production report (observe run #10, `verb=drift`, on `main` @ `3505eb3`):
  still **red**, and for a reason worth writing down rather than retrying.
  The run log reads `status: Failed (host exit 1)` — that line and the
  `__self.send` step id are both new code, so **the workflow half of #15 is
  live and correct**: `verdict.sh` was asked to excuse exit 3 and was handed
  exit 1, so it refused, which is exactly its job.
  The host reported 1 because it is running the **old `alertctl`**. Read verbs
  call `ensure_binary`, which builds only when the binary is absent and never
  syncs the checkout; only `plan` does that. That is deliberate — a read verb
  must not mutate the checkout — so the CLI half of #15 does not reach the host
  until a `plan` runs or bootstrap is re-run.
  Fleet state itself is unchanged from earlier today: four services healthy at
  `v0.1.0`, specs at `v0.1.2`, all four drifted.
- Notes: that is a new gap, filed as **#23**. Nothing in the observe output
  says which `alertctl` produced it, so the report looks current when it is
  not — which is precisely what just happened to me. The fix is a version
  stamp (`-ldflags -X` the commit, expose it, surface it), not rebuilding on
  read.
- Next: **#20** (rollback status), then #23, then release and first deploy.
  Conan's re-run of `bootstrap-host.sh` — still needed for #13 — will also
  rebuild `alertctl` and make #15 visible on the host, so #21 gets verified for
  free then.
- Catch-up: the finding-vs-failure fix is merged and its workflow half is
  proven live; the CLI half is sitting in `main` waiting for the host's binary
  to be rebuilt, which the bootstrap re-run will do.

## 2026-09-21 (third session) — correction to the #13 write-up
- Correcting myself: I recorded, and told Conan, that bug #8 left the host
  "with a checkout, a binary, and no deploy path", the wrapper and sudo rule
  never installed. The evidence contradicts that. The `AlertPlatform-Observe`
  document runs `runuser -u alert-ops -- sudo -n /usr/local/sbin/alert-deploy`,
  and four observe runs succeeded through it today — so the `alert-ops` user,
  the wrapper and the sudoers rule are all present and working. Bootstrap must
  have completed on an earlier run than the ones whose output I saw.
- The bug was real (reproduced locally, exit 2 before the fix) and the fix
  stands. What was wrong was my inference about its effect on *this* host: I
  reasoned from "the script dies before line N" to "line N never ran", without
  checking whether an earlier run had already done it. The observe runs were
  sitting in front of me and say so directly.
- What is actually true: the host's `alertctl` is stale — `drift` returns 1,
  not the 3 the merged code returns. That is the whole of what needs fixing,
  and either a bootstrap re-run or a `plan` does it. So the re-run is still
  worth doing; the reason I gave for it was not the right one.
- Catch-up: the host is in better shape than I reported. Nothing is missing
  from its deploy path; its control-plane binary is just two changes behind.

## 2026-09-21 (third session) — #21 verified end to end against production
- Did: nothing but verify. Conan re-ran `bootstrap-host.sh` (it completed this
  time) and I triggered `observe.yml` with `verb=drift`.
- **Production report (observe run #12, `main` @ `d74e2d0`) — the verification:**

      4 to change, 0 unchanged.
      ----- host stderr -----
      failed to run commands: exit status 3
      -----------------------
      status: Failed (host exit 3)
      ##[warning]AlertPlatform-Observe reported a finding on i-06aaf8cca765d5352 (exit 3).
      ##[end-action id=__self.send;outcome=success;conclusion=success;duration_ms=13133]

  Four things in that, all of them the point:
  **(a)** `host exit 3` — the rebuilt binary. The stale one returned 1, so this
  is direct evidence the bootstrap re-run refreshed the control plane.
  **(b)** SSM still records `Failed`, because its model has only succeeded and
  failed. Documented in ADR 0002 as an accepted limitation; the runbook says
  which view is accurate.
  **(c)** `verdict.sh` matched 3 against the declared finding set, annotated
  with `::warning`, and the job concluded `success`.
  **(d)** `4 to change` — the drift is still real and still reported. Green for
  the right reason, not because anything was silenced. That was the failure mode
  I was most worried about introducing.
  Fleet state otherwise unchanged: four services healthy at `v0.1.0`, specs at
  `v0.1.2`.
- Verification: the hourly observe schedule will now be green-with-warning while
  the fleet is behind its specs, and red only when the observation itself fails.
  Backlog #21 marked verified in production.
- Next: **#20** (the rollbacks logged `failed`), which is the gate before any
  first deploy. Then a release — no tag contains the exit-code change — then the
  first deploy through the pipeline to close the `v0.1.0` → `v0.1.2` drift.
- Notes: CLAUDE.md §10 no longer lists anything as known-wrong with the host.
  The staleness *mechanism* is kept in §10 as a fact to know rather than a
  fault, since it will catch the next control-plane change too until #23 lands.
- Catch-up: the finding-vs-failure change is now proven on the real host, both
  halves. The observe signal is trustworthy again — red means the observation
  failed, a warning means the fleet has drifted.

## 2026-09-21 (third session) — deploy.yml's first run, and a bottleneck that was mine
- Prompted by Conan asking why he has to run things on the host at all. The
  answer turned out to be mostly "he doesn't, and I got that wrong".
- **Production report — `deploy.yml` run #1, `step=plan`, the first time the
  write-path workflow has ever run:**

      4 to change, 0 unchanged.
      Plan saved to /var/lib/alert-platform/plans/.pending.json
      plan-id: a7d096877d55
      status: Success (host exit 0)

  The plan proposes exactly the four `v0.1.0 -> v0.1.2` updates and nothing
  else — no service creation, which per §6 is the exit-255 signature. **Not
  applied**, and not applicable yet: §2 forbids a deploy while a previous
  deploy's failure is uninvestigated, and the August `v0.1.2` failure is exactly
  that. #20 first.
- The correction that matters: I asked Conan to re-run `bootstrap-host.sh` to
  refresh the stale `alertctl`. `plan` does that — it is the verb that syncs the
  checkout to `origin/main` and rebuilds the binary — and I had even written "or
  a `plan`" into §10 before asking him anyway. CLAUDE.md §7 now opens with the
  division of labour so a future run checks before asking.
- What genuinely still needs him: `terraform apply` (IAM, OIDC, the SSM
  documents), root-on-host outside the verb set, and AWS resource changes. The
  first of those should never move — an agent that can rewrite its own trust
  policy has no boundary.
- Tried and stopped: making `plan` adopt a new wrapper from the checkout, to
  kill the last recurring handoff. The sandbox refused it as security-weakening
  and was right to. Written up as backlog **#24** — a proposal for Conan to
  accept or reject, with the argument on both sides and my recommendation that
  he decides rather than me.
- Catch-up: the whole deploy lifecycle is mine to drive and now proven so, plan
  included. The one thing left that needs Conan on a recurring basis is adopting
  a new wrapper, and whether to automate that is his call to make, not mine.
