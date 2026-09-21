# Backlog

Priority order within each tier is top-down, but production observations
outrank it: a real problem seen on the host becomes the next item.
Statuses: `todo`, `in-pr #N`, `done`, `needs-conan`, `blocked: <reason>`.
Each item is a milestone; note slices where one run can't finish it. Add
items with a one-line "why".

**Reprioritised 2026-09-21** around the goal in CLAUDE.md §1: alerts that
indicate a market move. Signal quality and the ability to measure it come
first; the deploy machinery is largely built and now serves that work rather
than being the work. Three of the four P0 items below came from one reading of
`observe.yml verb=logs` — the first time anyone had looked at what the bots
actually emit.

## P0 — Stop the noise, then measure it

25. **`form4-insider` is re-sending the same alerts in a loop.** `todo`
    **Live incident.** Found 2026-09-21 in journald. The same DELL insider
    sales are sent every ~20s: `$1,119,426`, `$1,411,252`, `$1,828,658`,
    `$4,209,497` … then the identical sequence again two minutes later.
    Interleaved with:

        sqlite3.OperationalError: database is locked
          mark_alerted(conn, accession)   main.py:222

    Cause, visible in the code and not a guess: `process_filing` sends every
    qualifying Telegram alert for a filing and calls `mark_alerted` afterwards.
    Any failure after the first send leaves the filing unmarked, so the next
    poll re-sends all of them, and does so for as long as the failure persists.
    The lock error is the trigger; the ordering is the defect. `v0.1.0` already
    sets `timeout=30.0` and WAL, so a missing busy timeout is not the
    explanation — something holds the write lock longer than 30s, plausibly
    `form4_scorer` or `form4_backfill`, which open the same database.
    Fix: claim the filing before sending. Mark first; if marking fails, send
    nothing. That converts a duplicate storm into silence plus an error and a
    metric, which is the right failure for an alert feed — one missed alert
    costs less than a channel nobody reads.
    Slices: (a) mark-before-send with a regression test that fails on the
    current ordering, (b) a metric and a log line for a refused send, (c) find
    the actual lock holder from the host and fix that separately, (d) incident
    write-up in `docs/incidents/`.
    **Deploying the fix needs #20 cleared first** (§2 forbids a deploy while a
    previous deploy's failure is uninvestigated), which makes #20 urgent rather
    than tidy.

26. **`fda-catalysts` has two dead sources and nothing notices.** `todo`
    Every poll cycle logs, at WARNING:

        Failed to fetch FiercePharma: 403 Client Error: Forbidden for url: https://www.fiercepharma.com/rss/xml
        Failed to fetch EndpointsNews: 403 Client Error: Forbidden for url: https://endpoints.news/feed/

    Both 403, so the service has been running with a fraction of its intended
    coverage for an unknown period while reporting healthy. 403 on an RSS feed
    is usually bot filtering (no or default User-Agent) or a moved feed.
    The gap is as much observability as fetching: a source that stops working
    should degrade the service's health or fire an alert, not log a warning
    forever. Slices: (a) identify why each 403 happens and fix or drop the
    source, (b) per-source success metric, (c) a health signal or alert when a
    declared source has failed for N consecutive cycles.

27. **Alert content is not persisted anywhere.** `todo`
    Alerts exist only as a Telegram message and a journald line. There is no
    record to query, so "is this signal any good" cannot be answered, and the
    website in CLAUDE.md §1 has nothing to render.
    This is the prerequisite for priority 1 and 2 and for the website, and it
    should be built before more filtering work, so that filtering can be judged
    against recorded output rather than impressions.
    Design sketch: an append-only table per service in the existing SQLite
    state, or one shared alerts database — every alert with source, ticker,
    payload, dedup key, reason it fired, and send outcome. Then an `alerts`
    read verb, and a console panel. Keep the schema boring; it is going to be
    read by a website later.
    Slices: (a) schema and `alertlib` write path with tests, (b) record from all
    four services, (c) `alerts` verb in the wrapper and SSM document — note the
    document's `allowedValues` is Terraform, so this is the first thing to use
    #28's self-service infra, (d) console panel.

28. **Own the AWS infrastructure: remote state and an `infra.yml` workflow.**
    `todo`
    Granted by the owner on 2026-09-21. Terraform state is local today, so
    nothing but a human's CloudShell can apply it, and every SSM-document or IAM
    change is a handoff. #27 needs a document change immediately.
    Slices: (a) S3 state bucket with versioning and a DynamoDB lock table,
    written as Terraform, plus the backend block; (b) an `alert-platform-infra`
    role with a permissions boundary, explicitly denied from modifying its own
    role, its own policies, the boundary, or deleting named stateful resources;
    (c) `infra.yml` with `step=plan` and `step=apply`, same read-the-plan
    discipline as `deploy.yml`; (d) one handoff issue for the bootstrap apply —
    the agent cannot grant itself access, so exactly one manual apply remains;
    (e) after it is proven, delete the root access keys and close port 22.
    The guardrails are the point. An IAM-capable role is close to account
    admin, so the boundary and the self-modification denies are what make this
    defensible rather than a shrug.

20. **The audit log records rollbacks as `failed` when they appear to have
    worked.** `todo` — **now urgent: it blocks the #25 fix from shipping.**
    Both `v0.1.2` rollbacks of `clinical-trials` on 2026-08-20 are logged
    `failed`, but the service is active and healthy at `v0.1.0`, which is
    exactly what those rollbacks were meant to restore. Either the rollback
    path misreports its outcome, or it genuinely failed and the service
    recovered another way.
    Slices: (a) read the rollback path and establish which statuses it can emit
    and when, (b) reproduce with a rollback forced to fail and one forced to
    succeed, (c) fix, with a test pinning each outcome to its status, (d) if the
    August rollbacks really did fail, an incident write-up.

29. **Release and first deploy through the pipeline.** `blocked: #20, #25`
    The fleet runs `v0.1.0`; specs pin `v0.1.2`; no tag contains ADR 0002's exit
    codes. `deploy.yml` has planned successfully (`4 to change, 0 unchanged`,
    plan `a7d096877d55`) and never applied. Sequence: clear #20, land #25, cut a
    release, roll the specs, then apply and verify.

## P0 — Carried forward

21. **`observe.yml` cannot tell a finding from a failure.**
    `done (#15), verified in production`
    Four named exit codes in `alertctl`, `drift`'s finding is 3, `ssm-run` takes
    `finding-exit-codes`, ADR 0002. Verified by observe run 12: host exit 3,
    annotated, job green, drift still reported.

22. **End-to-end test target.** `todo` (larger; slice it)
    A container with sshd and a systemd stand-in that `alertctl` can target in
    CI, proving plan → apply → drift → rollback against a real transport, and
    running `bootstrap-host.sh` for real.
    Raised from P2 on evidence: eight deploy-path bugs in one week, seven found
    by hitting them in production. #6, #7 and #13 would each have been caught
    by a target that actually ran the scripts.

23. **`observe` cannot tell you which `alertctl` the host is running.** `todo`
    Read verbs never rebuild the binary, so a control-plane change is invisible
    until a `plan`. Nothing says so, and a stale answer reads as current; this
    already misled one verification. Fix is a build-time commit stamp surfaced
    in `status` and the workflow summary, not a rebuild on read.

24. **Proposal for Conan: should `plan` adopt a new wrapper from the checkout?**
    `needs-conan — decision, not work`
    The sudo rule names a copy of the wrapper, so a wrapper fix waits on a root
    re-run of `bootstrap-host.sh`. Full argument both ways in the entry below;
    unchanged by the 2026-09-21 grant of AWS ownership, because the wrapper is
    the allowlist rather than a resource.

5. **Capture the host-side `form4_insider` edit into git.** `todo`
    Confirmed still real: the traceback in #25 puts `mark_alerted`'s
    `conn.execute` at `main.py:222`, where the repo has it at 218. The host is
    running code that is not in version control, in the same file as the live
    incident. Recover it before changing that file, or the fix will silently
    revert someone's patch.

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
