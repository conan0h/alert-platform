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

25. **`form4-insider` is re-sending the same alerts in a loop.**
    `fix deployed 2026-09-21; effect not yet observed (see #32); (c) open`
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
    current ordering — **done**, five tests in
    `services/tests/test_form4_dedup.py`, two of which fail on the old
    ordering; (b) `alert_sends_refused_total` and an error log — **done**;
    (c) find the actual lock holder from the host and fix that separately —
    open, needs host evidence; (d) incident write-up — **done**,
    `docs/incidents/2026-09-21-form4-duplicate-alerts.md`.
    **Deploying the fix needs #20 cleared first** (§2 forbids a deploy while a
    previous deploy's failure is uninvestigated), which makes #20 urgent rather
    than tidy.

27. **Persist alert content, and give it a reader.**
    `(a)(b) done 2026-09-22 in #35; (c) blocked on ADR 0004; (d) todo, unblocked`
    Alerts existed only as a Telegram message and a journald line, so "is this
    signal any good" could not be answered and the website had nothing to
    render. This is the prerequisite for priorities 1 and 2 and for the site,
    and it comes before more filtering work so that filtering can be judged
    against recorded output rather than impressions.

    **(a) and (b) done.** `alertlib.AlertArchive` plus `Service.send_alert`:
    every alert is recorded to an append-only SQLite table before it is sent and
    the delivery outcome settled afterwards — service, deployed ref, source,
    dedup key, ticker, reason, the message as sent, JSON payload,
    `pending | sent | failed`. All four services route through one method, so
    recording is a property of the send path rather than a convention. 23 tests.
    **Read ADR 0005 before touching it**: it argues the two decisions a reviewer
    will ask about — one database per service rather than a shared one (the last
    incident here was a SQLite lock storm), and a write failure that is counted
    rather than raised (a missing archive row is lost measurement; a missing
    dedup row is duplicate alerts that reach a human).

    **Nothing is being recorded in production yet.** This runs on the host, so
    it needs a release and a deploy, and no session can cut a tag (#31). Until
    then the table exists only in `main`.

    **(c) the `alerts` read verb — blocked on the wrapper, not on infra.** The
    SSM document half is ours now that #28 is done. But `READONLY_VERBS` in
    `deploy/ops/alert-deploy` does not include `alerts`, and the wrapper is not
    ours to change until ADR 0004's adopter exists.
    **(d) the console panel — unblocked, and needs nothing from anyone.**

    Design note for whoever builds either: the archive is one file per service,
    so a reader unions four. `AlertArchive.recent()` and `.count()` already
    exist, and `PRAGMA user_version` carries the schema version.

30. **The audit log cannot distinguish "refused before acting" from "failed
    while acting".** `todo`
    Both are `failed`, which is what made #20 take a month to read. A pre-flight
    refusal leaves the host untouched; a failure during a restart may not. An
    operator reading `rollback failed` cannot tell which they have without
    reading the engine source.
    Fix: either a distinct `refused` outcome, or a `mutated: bool` detail set
    once the first mutating step runs. Prefer the detail field — it is additive,
    and it answers the question that actually matters ("is the host in a state
    someone needs to fix"). Slices: (a) thread it through `applyService`,
    (b) surface it in `history` and the console, (c) test both paths.

31. **This session cannot cut a release tag.**
    `needs-conan — v0.5.0 requested 2026-09-23 in issue #50, at 837b327`
    `v0.4.0` is deployed on all four services as of 2026-09-23. The funnel
    accounting for #35 is merged to `main` and needs `v0.5.0` before it can run
    anywhere, which is the whole point of it.
    **Issue #41 is closed.** Conan cut `v0.3.0` (at `0f0541b`) and `v0.4.0` (at
    `4036ffa`) on 2026-09-22. Both are deployed or rolled: `v0.3.0` is live on
    `fda-catalysts` and verified over 329 cycles; `v0.4.0` is rolled into all
    four specs (#46) and **awaits its apply**, which is the next run's first
    task. Note the coordination failure worth not repeating: two concurrent
    sessions asked for `v0.3.0` at different commits, the tag landed on one of
    them, and the issue's four-service plan was then wrong for the tag that
    existed. Verify what a tag contains by ancestry before rolling to it.
    Verified 2026-09-21 by trying all three routes: `git push origin v0.2.0` →
    403; `POST /releases` → 403 "Creating, editing, or deleting releases is not
    permitted for this session type"; `POST /git/refs` → 403 "Write access to
    this GitHub API path is not permitted through this proxy". CLAUDE.md had
    asserted the opposite and now records the truth.
    This blocks every deploy, because `source.ref` must match
    `^v\d+\.\d+\.\d+$` and the engine clones with `--branch`, so neither a
    branch nor a SHA is a usable substitute. Loosening that pattern would weaken
    a §2 guarantee to work around a permissions limit, which is not a trade to
    make unilaterally.
    Mitigation, not a fix: hand Conan the prefilled release URL, which is one tap
    on a phone. A real fix would be a deploy-time mechanism that does not need a
    human for each release — a signed artifact, or a bot token with `contents:
    write` held by a workflow rather than by this session. Worth designing once
    #28's infra ownership lands, since that is the same shape of problem.


32. **`logs` returns the oldest part of its window, which is backwards.**
    `todo`
    Found while trying to verify the 2026-09-21 deploy. The verb runs
    `journalctl --since "1 hour ago"` with no bound on output. SSM caps a
    command's captured stdout at roughly 24 KB, and journalctl prints
    oldest-first, so a busy hour returns its *beginning* and silently drops the
    end. The deploy landed at 22:16 and the returned log stopped at 21:20.
    An operator asking for logs wants the most recent ones. Fix: bound the
    output with `journalctl -n <N>` as well as `--since`, so the tail is what
    survives. Also worth letting `observe.yml` pass `--since`, which today it
    cannot — the SSM document takes only `verb`, so that part needs a document
    change and therefore #28. **#28 is done as of 2026-09-22, so this is now
    ordinary work**: the SSM document is `infra/terraform/ssm_documents.tf` and
    `infra.yml` applies it. The wrapper half still needs ADR 0004's adopter.
    **Half done 2026-09-22.** `observe.yml` can now pass `--since`, as a choice
    input constrained again by `allowedValues` on the SSM document, so a caller
    can ask for 10 minutes instead of an hour and get all of it. The wrapper
    already accepted `--since`; only the document did not pass it. Also mitigated
    from the other end: #26(a) removed the ~3,800 daily warning lines that were
    the main thing filling the 24 KB.
    Still open: bounding output with `journalctl -n` so the *tail* survives
    regardless of window length. That is a wrapper change, so ADR 0004.
    Found while doing this: **the wrapper's `valid_since` accepts `30m`, which
    journalctl rejects** — verified both locally. So that form passes validation
    and then fails at runtime with a confusing error. The document's
    `allowedValues` excludes it; fixing the wrapper's validator needs ADR 0004.
    Note the window is one hour, not one day — an earlier log entry wrongly
    expected it to slide far enough to show a 22:16 event the next morning.

## P0 — Carried forward

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

24. **Wrapper adoption: decided, not built.**
    `granted 2026-09-22; blocked on a sandbox permission and on issue #27`
    The owner granted it: the agent owns the wrapper's verb set. Design and the
    full argument both ways are in **ADR 0004**. Not `plan` adopting silently —
    an explicit, gated, audited `adopt-wrapper` verb, with the *adopter* a
    separate frozen program so a broken wrapper stays recoverable and so the
    rules a candidate must satisfy remain the owner's.

    Two things block implementation, and neither is the decision:
    - **The sandbox refuses to let the agent author the privileged installer or
      the sudoers change** (`Security Weaken`), on 2026-09-21 and again on
      2026-09-22 after the grant. The grant settles whether, not how. Either the
      owner commits that one file from ADR 0004's design, or the permission is
      widened for it specifically. Do not attempt to route around the denial with
      a different file-writing tool; the objection is to what the script does.
    - **Issue #27 comes first regardless.** The SSM document needs an
      `adopt-wrapper` verb in `allowedValues`, which is Terraform, which needs
      ADR 0003's bootstrap applied.

    Everything else for it — the wrapper-side verb changes, the test-suite gate,
    the workflow input, docs — is ordinary work the agent can do once the
    installer exists.

5. **Capture the host-side `form4_insider` edit into git.** `todo`
    Confirmed still real: the traceback in #25 puts `mark_alerted`'s
    `conn.execute` at `main.py:222`, where the repo has it at 218. The host is
    running code that is not in version control, in the same file as the live
    incident. Recover it before changing that file, or the fix will silently
    revert someone's patch.

35. **`clinical-trials` examines hundreds of trials every cycle and alerts on
    none.** `instrumented 2026-09-23; needs a release, then one `logs` read`
    **The "exactly 399" framing was wrong, and the correction matters.** This
    item was opened on the reading that a constant count looked like a page size
    or a cap — a query stuck on its first page. On 2026-09-23, with `v0.4.0`
    live, the same line reads:

        Streamed 787 recently-updated trials from ClinicalTrials.gov

    787, twice in a row, five minutes apart. The count tracks the real size of
    the two-day window and needs four pages of 200 to carry it, so the query
    paginates and the 2000-candidate cap is not being hit. **The fetch is
    working.** 399 was that day's window, not a constant of the system.
    So the gap is between candidate and signal, and nothing recorded it: the
    service logged what it streamed and what it sent, with nothing in between.
    Three different faults produce identical output — a filter nothing can
    match, an upstream query returning the same rows every cycle, and a
    transition observed one cycle too late.
    **Instrumented, not fixed.** `alertlib.CycleFunnel` (ADR 0006) counts the
    stages and logs one line per cycle:
    `streamed parsed known first_sight first_sight_completed changed signals
    sent new_in_window`. Two counts test specific explanations. `new_in_window`
    compares this cycle's id set against the previous cycle's, which is the
    direct test of a stuck query — "new to our database" does not answer it.
    `first_sight_completed` counts trials first seen already COMPLETED, which
    `detect_signal` deliberately drops (signal 5 requires `prev_status not in
    ("COMPLETED", None)`): the query selects on last-update date, so a trial
    usually enters view *because* of the update we care about, and there is then
    no earlier status to compare against. That reasoning is defensible and its
    cost has never been measured.
    **Next step: cut a release, deploy, then read one `logs` window.** The line
    names which stage the candidates stop at, and the answer decides whether the
    fix is a filter change or a decision about first-sight events.

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

36. **Delete the root account access keys and close port 22.** `todo`
    Was slice (e) of #28, which is otherwise done. Both are goals in CLAUDE.md
    §2 rather than optional, and the pipeline that replaces them is now proven:
    `infra.yml` has planned and applied from `main` with no human step.

34. **Neither Terraform module has a `.terraform.lock.hcl`.** `todo`
    Both pin `~> 5.0`, so an `init` resolves whatever the registry currently
    offers — CloudShell got `hashicorp/aws v5.100.0` on 2026-09-22 and CI could
    get something else. The lock file pins versions *and* hashes, which is the
    same reproducibility the Go side gets from vendoring.
    Cannot be generated here: `registry.terraform.io` is refused by this
    session's egress policy, so `terraform init` cannot fetch a provider. It can
    be generated by `infra.yml` once issue #27 is applied (commit the file the
    workflow produces), or by Conan running `terraform providers lock` in
    CloudShell and committing the two files. `.gitignore` deliberately does not
    ignore them.

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

- **#21 — `observe.yml` could not tell a finding from a failure.** Four named
  exit codes, `drift`'s finding is 3, `ssm-run` takes `finding-exit-codes`.
  ADR 0002, verified in production by observe run 12.

- **#20 — Rollbacks logged `failed` that looked successful.** No defect: the
  secret gate returns before the first mutating step, so both passes changed
  nothing and both `failed` entries were accurate. Pinned by
  `internal/engine/secretgate_test.go`; write-up in
  `docs/incidents/2026-08-20-v0.1.2-apply-blocked-by-secret-gate.md`.

- **#29 — Release and first deploy through the pipeline.** Done; four applies
  have since run and none has needed a rollback.

- **#28 — Own the AWS infrastructure.** S3/DynamoDB remote state, the
  `alert-platform-infra` role with a permissions boundary, and `infra.yml`.
  Verified by runs 5 and 6, both `No changes`. ADR 0003. The guardrail defect
  found in the process was fixed in PR #36 and is a learnings entry. Slice (e) —
  delete the
  root access keys and close port 22 — is now backlog item 36.

- **#26 — `fda-catalysts` dead sources.** Per-source health accounting, a
  widening log schedule and four metrics (`alertlib.SourceHealth`), plus one
  User-Agent per destination. Settled from the host on 2026-09-23: `14/15
  sources healthy; failing: FiercePharma (x1)`. `EndpointsNews` recovered
  with the User-Agent change; `FiercePharma` is presumed dead.

- **#33 — Roll the fleet to a current tag.** All four services run `v0.4.0` as
  of 2026-09-23: `deploy.yml` run 8, plan `345baa3a5442`, `Applied 4
  change(s)` in 303s. One tag across the fleet for the first time since
  August.

- **#1 — Make `main` green.** Two independent failures: engine tests coupled
  to live fleet refs, and a relative schema `$id` that old `jsonschema`
  resolvers dereference as a URL. Merged 2026-09-21; CI run #15 on `main`
  green. Also restored `.claude/` to git and added the CI badge.
