# Backlog

Priority order within each tier is top-down, but production observations
outrank it: a real problem seen on the host becomes the next item.
Statuses: `todo`, `in-pr #N`, `done`, `needs-conan`, `blocked: <reason>`.
Each item is a milestone; note slices where one run can't finish it. Add
items with a one-line "why".

## P0 — Green, reachable, deployed (in this order)

1. **Make `main` green: decouple engine tests from live fleet refs.**
   `in-pr #1`
   CI has been red since `e44bd09`. `engine_test.go` asserted
   `git clone --depth 1 --branch v0.1.0` and `rollback_test.go` pinned
   `nextRef = "v0.1.0"`, but both loaded the real `fleet/` specs (now
   `v0.1.2`). Engine tests now load `internal/engine/testdata/fleet`, four
   fictional services pinned at `v1.0.0` that never move;
   `TestLiveFleetSpecsRenderAndStayDeployable` keeps the real specs covered
   without naming a version, port, service or count. `$id` is now the URN
   `urn:alertplatform:v1:service`, verified against both resolver
   generations. Full suite green locally and in CI. Still to do: add the CI
   badge now that `main` is green.

2. **Fix the Go module path `conanohara` → `conan0h`.** `todo`
   `go install github.com/conan0h/alert-platform/cmd/alertctl@latest` fails
   today. Update `go.mod`, all imports, and `vendor/modules.txt` if it names
   the module; rebuild; gofmt.

3. **Deploy and observe pipeline: GitHub OIDC → IAM → SSM.** `todo` → `needs-conan`
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

4. **Instance role for secrets and SSM; retire root keys.** `todo` → `needs-conan`
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

17. **End-to-end test target.** `todo` (larger; slice it)
    A container with sshd + a systemd stand-in that `alertctl` can target in
    CI, proving plan → apply → drift → rollback against a real SSH transport.

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

(move items here with the PR number)
