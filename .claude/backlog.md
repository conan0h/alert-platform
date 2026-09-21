# Backlog

Priority order within each tier is top-down, but production observations
outrank it: a real problem seen on the host becomes the next item.
Statuses: `todo`, `in-pr #N`, `done`, `needs-conan`, `blocked: <reason>`.
Each item is a milestone; note slices where one run can't finish it. Add
items with a one-line "why".

## P0 — Green, reachable, deployed (in this order)

1. **Make `main` green: decouple engine tests from live fleet refs.**
   `done (#1)`
   CI has been red since `e44bd09`. `engine_test.go` asserted
   `git clone --depth 1 --branch v0.1.0` and `rollback_test.go` pinned
   `nextRef = "v0.1.0"`, but both loaded the real `fleet/` specs (now
   `v0.1.2`). Engine tests now load `internal/engine/testdata/fleet`, four
   fictional services pinned at `v1.0.0` that never move;
   `TestLiveFleetSpecsRenderAndStayDeployable` keeps the real specs covered
   without naming a version, port, service or count. `$id` is now the URN
   `urn:alertplatform:v1:service`, verified against both resolver
   generations. Merged as #1; CI run #15 on `main` is green, the first since
   2026-08-20. README carries the CI badge.

2. **Fix the Go module path `conanohara` → `conan0h`.** `done (#3)`
   `go.mod` and all 13 importing files now say `github.com/conan0h/…`.
   `vendor/modules.txt` lists dependencies only, so nothing there named the
   main module and no vendor regeneration was needed.
   Follow-up, deliberately not done here: `go install
   github.com/conan0h/alert-platform/cmd/alertctl@latest` resolves to the
   newest *tag*, and `v0.1.0`–`v0.1.2` all carry the old path — so it keeps
   failing until a release is cut from this commit or later. Cutting that tag
   belongs with the next change that actually warrants a release, not with a
   rename.

3. **Deploy and observe pipeline: GitHub OIDC → IAM → SSM.** `read path done`
   Merged as #4; handoff #5 applied by Conan on 2026-09-21. The read path
   now works end to end against the real host — `status`, `drift`,
   `history` and `health` have all run and their output is the first
   Production report in the log. Six follow-up PRs (#8–#13) were needed to
   get there; see learnings. **Remaining:** one more `bootstrap-host.sh`
   run on the host (the three before #13 all died before installing the
   wrapper and the sudo rule), after which the write path can be tried.
   Code complete and tested; slices (a), (b) and (c) all landed together with
   ADR 0001. **Nothing has run against a real host**, and cannot until the
   one-time AWS apply and host bootstrap are done — see the Handoff issue.
   `alertctl` learned `ALERTCTL_ACTOR` so an automated apply is attributed to
   its workflow run rather than to the shared `alert-ops` account.
   The only path from this repo to production (CLAUDE.md §6). Slices:
   (a) `infra/terraform/`: GitHub OIDC provider; a deploy role trusted only
   for `repo:conan0h/alert-platform:ref:refs/heads/main`, allowed
   `ssm:SendCommand` for two SSM documents on this one instance plus reading
   the command output; the two documents (`AlertPlatform-Observe`,
   `AlertPlatform-Deploy`). `fmt`, `validate`, `tflint` in CI, never apply.
   (b) `deploy/ops/`: an idempotent host bootstrap creating the `alert-ops`
   user, a sudoers drop-in limited to the wrapper, and the wrapper
   `/usr/local/sbin/alert-deploy` with strict argument validation. Must pass
   `shellcheck` in CI, with tests for the validation.
   (c) `.github/workflows/observe.yml` (hourly and `workflow_dispatch`) and
   `deploy.yml` (`workflow_dispatch`, `concurrency: production`), passing
   `ALERTCTL_ACTOR=gha:${{ github.run_id }}`. Teach `alertctl` to prefer that
   env var for the audit `actor`, with a test.
   ADR: why OIDC plus SSM rather than SSH keys in CI, and why the agent never
   holds credentials. Handoff: CloudShell `terraform apply`, confirming the
   SSM agent is online (`aws ssm describe-instance-information`), and the host
   bootstrap via Session Manager.

4. **Instance role for secrets and SSM; retire root keys.** `needs-conan (#5)`
   The Terraform landed in #4 (`aws_iam_role.instance`, its SSM/KMS policy and
   the instance profile); attaching it is step 6 of handoff #5. Closing port 22
   and deleting the root keys stay open until an `observe` run proves the
   replacement path works — a follow-up handoff, not this one.
   Same Terraform: instance role and profile with
   `AmazonSSMManagedInstanceCore`, `ssm:GetParameter(s)` on
   `arn:aws:ssm:us-east-1:<acct>:parameter/alert-platform/prod/*`, and
   `kms:Decrypt` constrained by `kms:ViaService`. This unblocks the
   secret-resolution gate. Handoff: attach the profile, verify from the host
   without printing values, delete the root access keys, then close port 22
   in the security group. Runbook in `docs/runbooks/`.

5. **Capture the host-side `form4_insider` edit into git.** `todo`
   (needs #3.) Add a read-only `diff-release` verb to the wrapper,
   diff the deployed release directory against `v0.1.2`, bring the change into git with a test, and release it as
   `v0.1.3`. Record it as the first real instance of content drift, which
   motivates P1 #7.

6. **First production deploy through the gates.** `todo`
   (needs #3 and #4.) Deploy the latest release to all four services via
   plan → apply; full verification per CLAUDE.md §6. Then rewrite the README
   "Status" section from your own observations, and replace
   `docs/img/console.png` with a real capture. Hand off the capture
   to Conan: Session Manager port forwarding to 127.0.0.1:8600, then a
   screenshot.

6a. **Document the agentic operating model.** `todo` (with or right after #3)
   `docs/operating-model.md`: who operates the fleet (Conan as owner, the
   agent as autonomous operator acting through `deploy.yml`/`observe.yml`),
   the permission boundary (OIDC trust, IAM, SSM documents, sudoers) and why
   each line exists, the run loop, how auto-merge is
   gated, how agent actions show up in the audit log and PRs, and a running
   "what went wrong" section fed from incidents. README gets a short section
   linking to it. Keep it factual; no hype.

8a. **Pin the validator's Python dependencies for the host.** `todo`
   Gate 1 of every apply shells out to `tools/validate.py` using whatever
   `python3` and `jsonschema` the host happens to have. The `$id` bug found
   on 2026-09-21 was one way that bites; a resolver old enough to differ in
   *any* behaviour is another. Either vendor the validator's deps alongside
   the release or state a minimum version and check it at gate time, so a
   deploy cannot fail on the host's library versions. Slices: (a) decide
   vendor vs. version check, ADR if it changes the gate's contract,
   (b) implement with a test that the gate refuses an unsupported resolver.

20. **The audit log records rollbacks as `failed` when they appear to have
    worked.** `todo`
    From the first Production report: both `v0.1.2` rollbacks of
    `clinical-trials` on 2026-08-20 are logged `failed`, but the service is
    active and healthy at `v0.1.0`, which is exactly what those rollbacks were
    meant to restore. Either the rollback path misreports its own outcome, or
    it genuinely failed and the service recovered some other way — and the two
    have very different consequences. The audit log is the evidence behind
    every production claim this repo makes, and this field is the one that
    says whether the safety mechanism worked, so it cannot be left ambiguous.
    Do this **before** the first deploy through the pipeline: it is the field
    that will report on whether that deploy was safe. Slices: (a) read the
    rollback path and work out which statuses it can emit and when,
    (b) reproduce with a rollback forced to fail and one forced to succeed,
    (c) fix, with a test pinning each outcome to its status, (d) if the August
    rollbacks really did fail, an incident write-up.

21. **`observe.yml` cannot tell a finding from a failure.** `done (#15)`
    `alertctl drift` exits 1 to mean "drift found" — that is its contract. The
    composite action treats any non-`Success` SSM status as a workflow
    failure, so "the fleet has drifted" and "the host is unreachable" produce
    an identical red run. The hourly schedule is red right now and stays red
    until the fleet is deployed, which trains the only person who reads it to
    ignore it. That is alert fatigue, in the observability path, in a repo
    whose pitch includes symptom-based alerting. The verb's exit codes should
    be interpreted per verb: a finding annotates the run, an operational
    failure fails it. Slices: (a) give the wrapper's read-only verbs a
    documented exit-code contract, (b) have the action distinguish the two and
    annotate rather than fail on a finding, with tests for both,
    (c) state the contract in the runbook.
    Done, and deeper than filed: the ambiguity was inside `alertctl` too, not
    just in the workflow. `drift` used exit 1 for a finding and the error path
    also used 1, so a finding and a failed drift were indistinguishable at the
    source. `alertctl` now has four named exit codes (0/1/2/3) with drift's
    finding at 3, `ssm-run` takes `finding-exit-codes` and `observe.yml`
    passes 3 while `deploy.yml` passes nothing. ADR 0002. The verdict logic
    moved out of `action.yml` into `verdict.sh` so CI can test it — 16 cases,
    including that TimedOut with no exit code is never excused by a finding
    set.
22. **End-to-end test target.** `todo` (larger; slice it) — **raised from P2
    on 2026-09-21, with evidence.**
    A container with sshd + a systemd stand-in that `alertctl` can target in
    CI, proving plan → apply → drift → rollback against a real SSH transport,
    and running `bootstrap-host.sh` for real.
    Why it moved: eight bugs in the deploy path in one week, seven of them
    found by hitting them in production rather than in CI. #6 (only `plan`
    built the binary), #7 (`go build` from the wrong cwd) and #13 (bootstrap
    killed by its own post-build check) would each have been caught outright
    by a target that actually runs the script. The pattern is not carelessness;
    it is that PR #4 shipped a deploy path whose locally-testable half had 45
    tests and whose AWS-and-host half had none, so the only way to exercise it
    was to run it against production. Every run that starts before this lands
    pays for it again.

23. **`observe` cannot tell you which `alertctl` the host is running.** `todo`
    Found immediately after #15, by trying to verify it. The exit-code change
    merged, `observe.yml` ran `drift`, and the host reported **exit 1** — the
    old code. Read verbs call `ensure_binary`, which builds only when the
    binary is missing and never syncs the checkout; only `plan` does that.
    Deliberate (a read verb must not mutate the checkout) and correct, but the
    consequence is that a control-plane change does not reach the host until a
    `plan` or a bootstrap re-run, and **nothing in the observe output says
    so**. The report looks current and is not.
    The fix is a version stamp, not a rebuild-on-read: `-ldflags -X` the commit
    into `alertctl` at build time, have the wrapper print it, and include it in
    `status` and in the workflow summary. Then a stale control plane is visible
    in the same report that is being misread because of it.
    Slices: (a) stamp the commit and expose `alertctl version`, with a test
    that an unstamped build says "unknown" rather than lying, (b) wrapper verb
    or suffix on existing output, (c) surface in `observe.yml`'s summary,
    (d) note in the deploy runbook that a control-plane change needs a `plan`
    or bootstrap before read verbs reflect it.


## P1 — Close the documented gaps (strong design-review material)

7. **Content-drift detection.** `todo`
   Record a content manifest (sha256 of every file in the release dir,
   excluding the venv) in `deployed.json` at apply time; have `drift` recompute
   and report changed/added/removed files. Tests for each case. Update the
   known-gaps text wherever it appears. Slices: (a) manifest on apply,
   (b) comparison in drift, (c) console panel.

8. **`state.backup`: make the declared field real.** `todo`
   Rendered systemd timer + oneshot using SQLite's online backup
   (`sqlite3 .backup`, not `cp`), local rotation first; S3 target as a
   follow-up slice. Restore runbook with a tested restore procedure.

9. **`dedup.keys`: consume the declared field in `alertlib`.** `todo`
   Contract test that the Go-rendered env and the Python loader agree.

## P2 — SRE depth

10. **SLOs and burn-rate alerting, generated from specs.** `todo`
   SLIs: heartbeat freshness, poll success ratio, delivery success ratio.
   Spec field for objective; `gen_observability.py` emits recording rules and
   multi-window multi-burn-rate alerts; `docs/slo.md` explains the choices.

11. **Blameless incident write-up for the SSH exit-255 bug.** `todo`
    `docs/incidents/0001-ssh-255-empty-fleet.md`: timeline, impact (a plan
    proposing to recreate the whole fleet), contributing factors, why the
    gates did or didn't catch it, fixes, follow-ups. Facts only from the git
    history and learnings; nothing invented.

12. **ADRs.** `todo`
    `docs/adr/`: Go for the control plane; SSH push vs. host agent; tags-only
    refs; host-side secret resolution; vendored deps; read-only console. Short
    (context / decision / consequences). Cross-link from `docs/spec.md`.

13. **Delivery metrics from the audit log.** `todo`
    `alertctl history --stats`: deploy frequency, change failure rate
    (rolled-back / total), time-to-restore. Tests against fixture logs.
    Surface in the console.

14. **Fault-injection test matrix for `apply`.** `todo`
    Table-driven: fail at every lifecycle step (clone, pip, env write, unit
    write, reload, restart, health gate) and assert the invariants: rollback
    to previous ref, audit has failure + rollback, later services untouched.

15. **Supply chain in CI.** `todo`
    `govulncheck`, `pip-audit`, Dependabot config, checksummed release
    artifacts built from tags (build only; releasing stays with Conan).

16. **Bake time between tiers.** `todo`
    Optional `change_policy.bake_seconds` after the standard tier before
    critical-tier services deploy, with re-check of health. Spec, schema,
    engine, tests, docs.


## P3 — Presentation and frontend

18. **Operator console polish.** `todo`
    Drift panel, audit timeline, delivery-metrics card, dark mode, keyboard
    and screen-reader accessibility. Keep it embedded, dependency-free, and
    read-only (the console tests must still pass).

19. **Reproducible demo.** `todo`
    `make demo` running validate → plan → apply → injected failure → rollback
    → history against `-target dry`, plus a VHS/asciinema script for a README
    GIF. Label it clearly as a dry-run demo.

## Environment fixes for Conan

- **Check GitHub write access at the top of a run.** On 2026-09-21 the
  Claude GitHub App had read-only access here: reads succeeded while push,
  branch, PR *and issue* creation all returned 403, so the §7 handoff route
  was blocked too and the milestone was built before the problem showed.
  Conan fixed it mid-run by granting the app access at
  <https://github.com/apps/claude/installations/select_target>. Cheap guard
  for a future run: try the write early rather than after the work.

- **`terraform validate` cannot run locally.** `registry.terraform.io` is
  refused by this session's egress policy (403 at the proxy), so the AWS
  provider cannot be fetched and only `terraform fmt -check` runs here. CI
  does the real `init -backend=false && validate`. Worth allowlisting the
  registry if infra work continues, so a broken provider argument is caught
  before a push rather than after.
- **`golangci-lint` cannot run locally.** The image has v2.5.0;
  `.golangci.yml` is v1 format and CI pins v1.59.1, so the binary exits with
  "unsupported version of the configuration". Either pin v1.59.1 in the image
  or migrate the config and CI together — the second is the better end state
  but is a change to the lint policy, so it wants its own PR.
- **No `gh` CLI in the runner.** CLAUDE.md §6 is written around
  `gh workflow run` / `gh run watch`; the routine has the GitHub MCP tools
  instead (`actions_run_trigger`, `actions_list`, `get_job_logs`), which
  cover the same ground. Worth rewording §6 rather than installing `gh`.
- **System `python3` is 3.11 with no `pyyaml`, `jsonschema`, `ruff` or
  `pytest`.** A run has to build its own 3.12 venv first. Note the sharp
  edge: `rollback_test.go` *skips* itself when the validator deps are
  missing, so a missing dependency reads as a pass locally and a failure in
  CI. That is how backlog #1 stayed half-hidden.

## Done

- **#1 — Make `main` green.** Two independent failures: engine tests coupled
  to live fleet refs, and a relative schema `$id` that old `jsonschema`
  resolvers dereference as a URL. Merged 2026-09-21; CI run #15 on `main`
  green. Also restored `.claude/` to git and added the CI badge.
