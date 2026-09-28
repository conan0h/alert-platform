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

    **Whether the table is filling is still not answered.** #38's snapshot was
    supposed to do it via `alert_archive_records_total`, and on `v0.6.0` that
    counter is *absent* from all four snapshots rather than zero — the archive is
    constructed lazily, so a service that has archived nothing never declares it.
    See #41. What is *in* the rows still needs (c) or (d).

    **Recording has been live since the `v0.4.0` apply on 2026-09-23**, the first
    time any alert has been persisted anywhere. No row has been read yet, from
    here or anywhere else.

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
    `needs-conan each release; five tags cut this way, most recently v0.6.0`
    Verified 2026-09-21 by trying all three routes: `git push origin <tag>` →
    403; `POST /releases` → 403 "Creating, editing, or deleting releases is not
    permitted for this session type"; `POST /git/refs` → 403 "Write access to
    this GitHub API path is not permitted through this proxy".
    This gates every deploy, because `source.ref` must match
    `^v\d+\.\d+\.\d+$` and the engine clones with `--branch`, so neither a
    branch nor a SHA is a usable substitute. Loosening that pattern would weaken
    a §2 guarantee to work around a permissions limit, which is not a trade to
    make unilaterally.
    Mitigation, not a fix: hand Conan the prefilled release URL, which is one tap
    on a phone. Issues #41, #50 and #56 all closed this way with no friction, so
    the handoff route works; what it costs is a run of latency per release.
    A real fix would be a deploy-time mechanism that does not need a human for
    each release — a signed artifact, or a bot token with `contents: write` held
    by a workflow rather than by this session. Worth designing when release
    latency actually hurts; it has not yet.
    **Verify a tag by ancestry before rolling to it.** Two concurrent sessions
    once asked for `v0.3.0` at different commits, the tag landed on one of them,
    and an issue's four-service plan was then wrong for the tag that existed.

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
    **The truncation is also a sampling lever, and should stay one after the
    fix.** Because a long window returns its beginning, `since=6 hours ago` at
    08:22 returns 02:21 onward — the post-close hour, which five scheduled runs
    at ~08:15 UTC had never seen. That is how 2026-09-27 got a reading from
    22:21 ET. Bounding output with `journalctl -n` would make the tail survive
    and take this away, so the fix wants an explicit way to ask for the head of
    a window as well: `--since` plus a direction, not `--since` alone.

41. **`alert_archive_records_total` is absent from the snapshot, not zero.**
    `todo — found 2026-09-28, blocks the last open half of #27`
    The counter meant to answer "is the alert archive filling" is not emitted by
    any of the four services. `AlertArchive` is constructed lazily in
    `Service` (`service.py:112`) so that a service which never alerts never
    creates its database file — good for the backup and for keeping empty
    databases off the host, but it means the counter is declared only after the
    first archived alert. No service has archived one, so none declares it.
    An absent metric is worse than a zero: it cannot be told apart from a metric
    nobody added, and #38 was merged believing this question was answered.
    Fix: declare the counter at `Service` construction and seed it from
    `AlertArchive.count()` at startup, so it reports **rows in the table**
    rather than rows written by this process. That also survives the restart
    every deploy causes, which the current in-process counter does not — the
    archive has been recording since the `v0.4.0` apply on 2026-09-23 and a
    fresh counter cannot see any of it.
    Seeding needs the file to exist, so keep the lazy create and treat "no file"
    as a genuine zero. Test both: no database → 0, populated database → its row
    count, and a declared-but-unseeded counter never regresses to 0 on restart.

42. **Read `form4-insider`'s funnel in the post-close hour, not the scheduled one.**
    `todo — the one read that decides #39(b)`
    The first funnel window (2026-09-28, 08:37–08:46Z = 04:37 ET) reads
    `entries=100 new=0` with every later stage 0. **That is expected, not a
    bug**, and the code says why: `alerted` is written on *every* path a filing
    takes — sent, refused, failed fetch, failed parse (`main.py:383, 390, 402`)
    — so it is an already-processed table, and pre-market the feed's latest 100
    do not turn over. The name `alerted` is misleading for what it holds; worth
    a comment rather than a rename, since the column is on disk.
    So the branch histogram means nothing until candidates actually flow. Form 4s
    are filed after the US close, and the 2026-09-27 reading (22:21 ET) showed
    one cycle in eleven taking 2.32s — about one filing fetched per hour.
    **The read:** `observe.yml verb=logs since=6 hours ago`, which returns the
    *oldest* part of its window (#32) and therefore the post-close hour. Then
    `alert_funnel_new_total`, `_fetched_total`, and whichever named branch is
    non-zero. `alert_funnel_entries_total` over `alert_uptime_seconds` gives the
    feed's turnover rate rather than one window's snapshot.
    Only once a branch shows a positive count does #39(b) — scorer as a managed
    unit, or a filter that does not need one — have evidence to be decided on.

43. **A plan says config changed when only the ref did.** `todo`
    Every one of the four UPDATEs in the `v0.6.0` apply printed

        environment  <old> -> <new>
                     (polling, delivery, health or state config changed)

    and none of that config had changed. `ALERT_DEPLOYED_REF` is part of the
    rendered environment (`unit.go:145`) and the env hash is computed at the
    *desired* ref (`plan.go:155`), so a ref roll always moves it.
    This is a safety defect rather than a cosmetic one. §6 tells the operator to
    read every plan and refuse a surprising one; a reason that fires on every
    release trains them to skim past the line that would matter on the release
    where config genuinely did change. Same class as the `built` → `committed`
    mislabel fixed in #59.
    Fix: recompute the hash at the observed ref. If that matches what the host
    reports, the ref accounts for the whole difference and the plan should say
    so; otherwise keep the existing wording, because something else did move.
    Table-driven test for three cases: ref only, config only, both.

## P0 — Carried forward

22. **End-to-end test target.** `todo` (larger; slice it)
    A container with sshd and a systemd stand-in that `alertctl` can target in
    CI, proving plan → apply → drift → rollback against a real transport, and
    running `bootstrap-host.sh` for real.
    Raised from P2 on evidence: eight deploy-path bugs in one week, seven found
    by hitting them in production. #6, #7 and #13 would each have been caught
    by a target that actually ran the scripts.

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

35. **`clinical-trials`: no trial's status ever changes, so nothing alerts.**
    `measured 2026-09-23 on v0.5.0; the remaining work is a decision, not a bug`
    **Answered by the funnel, first cycle on the host:**

        Streamed 787 of 787 recently-updated trials from ClinicalTrials.gov in 4 page(s)
        funnel: streamed=787 parsed=787 known=787 first_sight=0
                first_sight_completed=0 changed=0 signals=0 sent=0 new_in_window=?

    - **`changed=0` is the cause.** Of 787 trials whose last-update date moved,
      none had a status different from the stored one. `detect_signal` fires
      only on a status transition; the updates are real but administrative.
    - **`first_sight_completed=0` refutes ADR 0006's leading hypothesis.** The
      "transition arrives before we do" path never runs, because `first_sight`
      is 0 — every trial is already known.
    - **`787 of 787 in 4 page(s)`** ends the pagination theory the item was
      opened on. The fetch reads the whole match and never hits the cap.

    **Cycle 2 added `new_in_window=0`**: not one of the 787 ids is new since the
    previous cycle. The feed returns an identical set, which is expected —
    `LastUpdatePostDate` is date-granular, so a two-day window's membership can
    only change when the date rolls. The consequence is worth stating: at a
    300-second interval the service re-examines the same 787 rows about 288
    times a day against a set that refreshes once.

    **The read this needs was impossible until #38.** `alert_funnel_changed_total`
    lives on `/metrics`, which nothing off the host can reach; the metrics
    snapshot puts it in the journal from `v0.6.0` on. A third reading on
    2026-09-24 (cycles 280–281) returned `686 of 686`, `changed=0`,
    `new_in_window=0` — and 399 → 787 → 686 across three days ends any remaining
    doubt that the window's membership rolls.

    **Not yet established: the rate.** Two cycles five minutes apart cannot
    give it, and the mechanism argues alerts should eventually fire — a trial
    whose previous update fell outside the two-day window leaves our view, so on
    re-entry its stored status is weeks old and a flip to COMPLETED reads as a
    change. Next step is a read, not an investigation:
    `alert_funnel_changed_total` over a day, from `/metrics`.

    **The Monday window is empty by construction** (2026-09-28, 08:41Z):
    `Streamed 0 of 0 … in 1 page(s)`, where the five previous readings gave 787,
    686, 602, 843 and 530. `fetch_recent_changes` asks for the last two days, so
    on a Monday before the US business day the window is Saturday, Sunday and a
    few hours of Monday — **the only two-day window in the week containing no
    business day**, and the first Monday since `v0.5.0` shipped the funnel. The
    Monday a week earlier read 399 on `v0.1.0`, before `countTotal` existed, so
    that number was what it streamed rather than what matched; not a
    counter-example. `countTotal` is what makes `0 of 0` readable as "the API
    reported no matches" rather than "we failed to read".

    That sharpens the decision below with a second, concrete option:
    **`days_back=2` blinds this service every Monday morning and ages Friday's
    updates out over the weekend.** `days_back=4` would carry Friday across at
    the cost of re-examining more already-known rows — which costs little, since
    `changed=0` is the bottleneck rather than throughput.

    **Then a decision for Conan, not a fix for me.** If the rate really is near
    zero, the service is watching a stream in which its declared events are
    rare, and the options are to widen what counts as an event (results posted,
    enrollment changes, completion-date moves) or to accept a feed that is quiet
    by design. That is a signal-quality judgment about what the channel is for.

38. **Every cumulative counter is recorded where nothing can read it.**
    `done 2026-09-28 — live on all four services at v0.6.0`
    `/metrics` binds to the host's loopback, the `health` verb discards the
    response body, and a `metrics` read verb would be a wrapper change (ADR
    0004). So the counters were recorded where nothing off the host could read
    them. Fixed in the shape that worked for source health: the poll loop writes
    the whole registry to journald every 900s and once at shutdown. Verified in
    production 2026-09-28 — four snapshot lines, whole registry, with
    `alert_uptime_seconds` beside the counters. Grep `metrics snapshot`.

    **Two of the four questions it was built to answer are still open, and the
    snapshot is why we now know that:**
    - `alert_alerts_sent_total` reads **1 on every service with zero alerts
      sent**. It is the startup Telegram message: the counter increments in the
      transport (`telegram.py:107`), not in `Service.send_alert`. See #37, whose
      "neither recorded nor counted" is half wrong.
    - `alert_archive_records_total` is **absent from every snapshot**, not zero.
      `AlertArchive` is constructed lazily (`service.py:112`), so a service that
      has archived nothing never declares the counter. See #41 below.

39. **`form4-insider` sends nothing because its feed is entirely already-seen.**
    `(a) done 2026-09-28; (b) open, and now known to be downstream of #42`
    Retitled 2026-09-28. The item was opened as "the leaderboard is empty, so
    three of four filter branches are dead". Both halves turned out to be wrong,
    and the funnel from #61 said so on its first window:

        funnel: entries=100 new=0 fetched=0 parsed=0 claimed=0 transactions=0
                code_not_actionable=0 planned_sale=0 below_floor=0
                large_trade=0 no_insider_history=0 thin_history=0
                no_leaderboard=0 below_cutoff=0 top_tier=0 sent=0

    **(a) answered: the candidates stop at `new`, one stage after the feed.**
    100 entries a cycle, none new (`alert_funnel_entries_total` 900 over nine
    cycles, `alert_funnel_new_total` 0). Every accession is already in
    `alerted`, so **no filter branch runs at all** — `no_leaderboard=0` is a
    zero, not a count. Four runs' worth of reasoning about which branch refused
    was aimed a stage too late. The live constraint is dedup, now item #42.

    **The leaderboard is full and unscored, not empty:**

        insiders 13782   transactions 32592   eligible 0   scored 0

    `form4_backfill.py` has run; `form4_scorer.py` never has. Nothing is scored,
    so nothing is eligible, so `get_alpha_cutoff` returns `None`.

    **(b) still a decision for Conan — and no longer urgent.** Either
    `form4_scorer.py` becomes a managed unit with a timer (like #8's backup job)
    or the filter stops depending on a leaderboard nothing fills. But while
    `new=0`, a fully scored leaderboard yields **zero** extra alerts, because no
    candidate reaches the branch that reads it. Do #42 first; this changes
    nothing until it is done.
    **(c) done** — the docs say the live filter is the $1M branch.

37. **The startup Telegram message is counted as an alert.** `todo`
    Each service sends a "bot started" message through `send_telegram` directly
    rather than `Service.send_alert`, so it is not archived.
    **Corrected 2026-09-28 — it *is* counted.** `alert_alerts_sent_total`
    increments inside the transport (`telegram.py:107`), so the startup message
    bumps it while skipping the archive. Every service read
    `alert_alerts_sent_total: 1` on a fleet that has delivered no alerts, and
    issue #56 had claimed that counter "answers whether this fleet has ever
    delivered an alert at all". A counter that reads 1 for zero is worse than a
    missing one, because it reads as evidence.
    Fix: route it through `send_alert` with a `startup` source so it is both
    archived and attributable, or keep it out of the alert counter entirely and
    state the exclusion in ADR 0005. Prefer the first — "the archive holds every
    message the bot sent" then becomes true rather than nearly true.

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

- **#33/#56 — one tag across the whole fleet, twice over.** All four services run
  `v0.6.0` as of 2026-09-28: `deploy.yml` run 13 planned `2880151f4eb5` (four
  UPDATEs, `4 to change, 0 unchanged`), run 14 applied it — `✓ … healthy at
  v0.6.0` four times, `Applied 4 change(s)`, 305.9s, no rollback. Eleven service
  applies have now gone through the pipeline and none has needed one. The roll
  covered all four on §6's shared-`alertlib` clause, checked by diff.


- **#40 — `edgar-mna` could not say which of its sixteen feeds work.** It now
  reports through `alertlib.SourceHealth` like the other three services
  (`d0b33cf`, PR #63). The trailing-slash theory the item was opened on is
  answered and it was not ours: a third window showed 404s naming the slashed
  URL and 502/503s naming the unslashed one, which is `requests` reporting the
  post-redirect URL — PRNewswire redirects and then 404s inconsistently, at
  roughly one cycle in five. The same PR made the shared escalation schedule
  start at the second consecutive failure, because a flapping source cost four
  log lines per blip and was out-logging a dead one. Needs the tag to reach the
  host; reading `source health:` and `alert_source_fetch_failures_total` after
  the apply is what closes it in production.

- **#23 — `observe` could not tell you which `alertctl` answered.** `status`
  and `drift` print the revision the Go toolchain stamped into the binary
  (`internal/buildinfo`), and `ssm-run` compares it against the commit the
  workflow ran from, annotating when they differ. No change to how the host
  builds, so no wrapper change: the stamp was already in the binary and
  nothing read it. Verified in production on 2026-09-25 — see the run log.

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
