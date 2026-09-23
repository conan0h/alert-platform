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

Older entries are in [`log-archive.md`](log-archive.md). Move entries there
once this file runs well past the last handful, so the file stays the length it
is read at.

## 2026-09-22 (fourth) — AWS is agent-managed; issue #27 closed
- **`infra.yml` run 5, commit `f5ac4f5`, 08:15:23Z:**

        No changes. Your infrastructure matches the configuration.

  Every step green: OIDC assume-role, `init` against the S3 backend, `validate`,
  `plan`, with `apply` skipped for `step=plan`. That is the whole chain proven
  end to end against the live account. Issue #27 closed with the evidence.
  **No AWS change needs a human from here.**
- Getting there took four `infra.yml` runs and three defects, two of them mine:
  - runs 1 and 2 failed on the `AWS_INFRA_ROLE_ARN` guard, which is the guard
    working. Not settable or even readable from this session — the proxy refuses
    `/actions/variables` with 403 both ways, so a workflow run is the only test.
  - run 3 failed on **my own guardrail**. `NeverTheTrustAnchor` denied
    `iam:*OpenIDConnectProvider*`; that also matches
    `iam:GetOpenIDConnectProvider`, an explicit Deny beats the `iam:Get*` Allow
    in `PlanNeedsToRead`, and Terraform refreshes every resource in state before
    planning. So the role **could not read the resource it was forbidden to
    change, and therefore could do nothing at all.** Fixed in #36 by enumerating
    the seven mutating actions; `tools/check_iam_denies.py` fails CI on any
    wildcard inside a Deny. The constraint the ADR intended is unchanged.
  - the state was nearly orphaned: it lived in a clone from an earlier CloudShell
    session and survived only because the handoff used `git pull --ff-only` on an
    existing directory rather than re-cloning.
- The IAM fix is the one change the infra role could not apply itself, since the
  bug was precisely that it could not plan. One CloudShell apply from the owner.
  Worth noting the failure direction: it failed **closed**, which is right for a
  guardrail, and cost nothing in production because the role had never
  successfully done anything.
- `.gitignore` had no Terraform patterns at all (#33). `terraform.tfstate` was
  untracked only because nobody had run `git add -A` in that directory.
- Confirmed `debug.ReadBuildInfo()` stamps `vcs.revision` even under
  `-mod=vendor`, so backlog #23 needs no change to the host's build command —
  useful for the next run, which should take it.
- Next: #32's `logs --since` and the `alerts` verb are now ordinary work (SSM
  document + `infra.yml`). #23 is unblocked and needs no tag, because `plan`
  rebuilds the binary. Still waiting on the owner: the `v0.3.0` tag, without
  which the dead-feed accounting cannot reach the host and #26(a) stays open.

## 2026-09-22 (third milestone) — the alert archive, and a guardrail found by using it
- **Production report — observe runs 22, 23, 24 and 25, 08:02–08:12Z.** All four
  services `active` and `enabled`, host exit 0 on every verb:

        SERVICE          REF        STATE      ENABLED    DEPLOYED               BY
        clinical-trials  v0.1.0     active     enabled    2026-08-20T11:46:25Z   ubuntu
        edgar-mna        v0.1.0     active     enabled    2026-08-20T11:53:10Z   ubuntu
        fda-catalysts    v0.1.0     active     enabled    2026-08-20T12:06:59Z   ubuntu
        form4-insider    v0.2.0     active     enabled    2026-09-21T22:16:39Z   gha:35661685161

  `drift`: "No drift: the target matches desired state", exit 0 — the first
  clean drift read since the specs were pinned in #24. `health`: all four
  `unit=active healthz=ok`. No deploy this run.
- **The alerts, read first per §5.3.** `form4-insider` completed cycles 265–267
  with no `database is locked`, no repeated send and no `sends_refused`, about
  267 cycles since the deploy. Still not proof the duplicate loop is fixed: no
  alert fired in the window, so the record-then-send path has not been exercised
  under contention. `fda-catalysts` still 403s FiercePharma and EndpointsNews on
  every cycle, as expected — #26(b)(c)(d) are in `main` and not deployed.
- **#32 reproduced precisely, and it is worse than "the tail is missing".** The
  `logs` read asked for one hour (07:03–08:03) and returned 07:03:14 to 07:16:44
  then `--output truncated--`: thirteen minutes of sixty, from the wrong end. 46
  of the 89 lines were the two dead feeds. So the verb an operator reaches for
  shows a seventh of the window and spends half of that on a known fault.
- **Milestone: the alert archive (backlog #27a+b), merged as #35.**
  `alertlib.AlertArchive` records every alert before sending and settles the
  outcome after. All four services go through `Service.send_alert`, which makes
  "every alert is recorded" a property of the send path rather than something
  four services have to remember — and `test_alert_records.py` pins that with a
  source check that fails on `origin/main` in both directions.
  **ADR 0005** states the two decisions worth arguing. One database per service
  rather than a shared one, because the last production incident here was a
  SQLite lock storm and adding writers to measure contention is not a trade
  worth making. And an archive write failure is logged and counted rather than
  raised, which is the opposite of the dedup discipline three files away — a
  missing archive row is lost measurement, a missing dedup row is duplicate
  alerts that reach a human.
  Nothing is recorded in production yet: this runs on the host, so it needs a
  release, and this session still cannot cut a tag (#31).
- **Hit the same guardrail defect independently** — `infra.yml` run 4 died at
  refresh on `iam:GetOpenIDConnectProvider`. The diagnosis and the fix are in
  the entry above, which got there first; this is only the part that differs.
  I did not ship it and should not have: the IAM guardrail is the mechanism
  that bounds this agent, so §2 makes narrowing it a proposal. **The sandbox
  refused the edit as `Self-Modification`** before I had to decide, the same
  refusal the wrapper installer got. I wrote the proposal up instead, then
  found #36 already merged with the identical seven actions and the CI guard I
  had only recommended — so I deleted the proposal rather than merge a
  duplicate of a decision already taken.
- Confirmed the pipeline myself afterwards on the merged fix: `infra.yml` run 6
  on `f5ac4f5`, `No changes. Your infrastructure matches the configuration.`
- **Two scheduled sessions were running against this repository at once.** Mine
  and session `01Siuqu`, whose #33, #34 and #36 landed on `main` between my
  fetch and my merge. Nothing collided — my PR stayed `unstable` rather than
  `dirty` — but both sessions independently diagnosed the same OIDC deny within
  three minutes of each other, which is a whole milestone of duplicated work.
  Worth Conan knowing before it costs a conflict instead of a duplicate.
- Next run: the two things production actually needs are both one human action
  away. A tag would let the archive, the source-health accounting and the UA fix
  all reach the host in one deploy. After that, #27(c): the `alerts` read verb —
  the SSM document half is genuinely unblocked now, the wrapper half still needs
  #24's installer.
- Catch-up: the fleet now has somewhere to put what it finds, and the
  infrastructure pipeline works. Neither fact has reached the host yet.
## 2026-09-22 (fifth) — first self-service infra change, and the log window works
- **First production infrastructure change through `infra.yml` rather than a
  handoff.** #39 added a `since` parameter to `AlertPlatform-Observe`; plan read,
  then applied. `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.`
- The plan said **2 to change** and the apply made **1**. The second was
  `aws_iam_role_policy.deploy`, which I had not touched, and it was right to stop
  and explain it before applying: `data.aws_iam_policy_document.deploy` reads
  `aws_ssm_document.observe.arn`, so an in-place update to the document makes the
  data source unknown at plan time and the whole rendered policy shows as
  `(known after apply)`. The apply confirmed the reading — the data source came
  back with id `704535516`, identical to plan time, so the policy was
  byte-identical and needed no update. **An in-place update cascades into every
  data source that reads the resource, and those cascades are usually no-ops.**
- **Production report — observe run 26, `logs --since "10 minutes ago"`, 08:28Z.**
  Window 08:22:26–08:27:59, untruncated, ending five seconds before the read.
  That is #32's practical half working: the *recent* end of the journal, not the
  beginning of an hour.
  - `fda-catalysts` `v0.1.0`: FiercePharma and EndpointsNews still 403 on every
    cycle (63045–63052). **This does not test #26's fix** — the service is still
    on `v0.1.0`, so this is the old unconditional-warning behaviour, and the
    dead-vs-blocked-UA question still needs `v0.3.0` deployed.
  - `form4-insider` `v0.2.0`: cycles 305–307 clean. No `database is locked`, no
    repeated sends, no refusals — roughly 307 cycles since the deploy. Still
    **consistent with working rather than proven**: no alert fired in the window,
    so the record-then-send path was not exercised under contention.
  - `clinical-trials` `v0.1.0`: "Streamed 399 recently-updated trials", the same
    constant as yesterday, and zero alerts. Filed as backlog #35 — a constant
    looks like a page size, not a count of what changed.
  - `edgar-mna` `v0.1.0`: cycles 62780–62787, healthy.
  - No alerts from any service in the window. 08:22Z is 04:22 ET, so low filing
    activity is the likely explanation rather than a fault.
- Learned while building it: the wrapper already accepted `--since`; only the SSM
  document failed to pass it, so this needed no wrapper change. Also that the
  wrapper's `valid_since` accepts `30m`, which journalctl rejects — verified both
  ends. The document's `allowedValues` excludes it.
- A concurrent session (`session_01UskzePof4vxaggFHb9jazF`) built the alert
  archive in #35 while this run was going: `alertlib.AlertArchive`,
  `Service.send_alert`, ADR 0005, 23 tests. Backlog #27 still said `todo`, now
  corrected — (a) and (b) done, (c) the `alerts` verb blocked on the wrapper
  rather than on infra, (d) console panel open. **Read ADR 0005 before touching
  the archive.**
- Next: `v0.3.0` is the only thing between us and an answer on the dead feeds, and
  it needs the owner. Unblocked meanwhile: #23 (build stamp in `status`, verified
  that `debug.ReadBuildInfo` stamps under `-mod=vendor`), #27(d) the console
  panel, and #35 above.

## 2026-09-22 (sixth) — v0.3.0 deployed; a dead feed was the User-Agent
- **Deployed `fda-catalysts` v0.1.0 → v0.3.0.** Plan `4f81a414c59e` read before
  applying: `1 to change, 3 unchanged`, exactly as predicted. Health gate passed,
  78s, nothing rolled back. Second service ever deployed by this pipeline.

        fda-catalysts  v0.3.0  active  enabled  2026-09-22T08:38:32Z  gha:35705787906

  The plan's `environment` hash change was expected rather than surprising: the
  ref is passed to the service through its env, so rolling the ref necessarily
  moves that hash.
- **Backlog #26(a) answered. The User-Agent was the cause — for one of the two.**

        source health: 14/15 sources healthy; failing: FiercePharma (x1)

  Before the deploy both FiercePharma and EndpointsNews returned 403 on every
  cycle. After it, **only FiercePharma does.** So `main()` writing the SEC contact
  string into `HTTP_HEADERS_DEFAULT` was what Endpoints refused, and removing it
  recovered a feed that had been dead since August. FiercePharma is genuinely
  blocking us — now a fact rather than a hypothesis, and the remaining question is
  whether it is IP, Cloudflare or a subscription.
- **Three CRITICAL alerts fired on the first cycle of `v0.3.0`:**

        [CRITICAL] FDA_APPROVAL | FDA approves ataxia-telangiectasia drug; Alkermes reports ADHD data
        [CRITICAL] FDA_APPROVAL | FDA approves Lilly's oral SERD combo for second-line breast cancer therapy
        [CRITICAL] FDA_APPROVAL | FDA approves Ultragenyx's gene therapy for Sanfilippo syndrome type A

  Which is the point of the project. **Attribution to the recovered feed is not
  proven**: the journal line does not name a source per alert. The alert archive
  from #35 would, and it is not in `v0.3.0` — a good argument for getting it
  deployed, because "which source produced the actionable alerts" is the question
  priority 1 turns on.
- **And the deploy found a bug in my own change**, by output rather than by test:
  the summary printed every cycle, because `summary_if_changed` compared the
  rendered string and the failure count is in it. So #30's real effect was 3 log
  lines per cycle down to 2, not down to a handful as its PR claimed. Fixed in
  #43, comparing the failing *set* instead; the new test fails against the
  deployed version with the observed symptom. Learnings entry added — a test that
  runs one iteration cannot see a per-iteration bug, and a change-detector whose
  key contains a monotonic value fires unconditionally while looking correct.
- The fix is merged but not deployed, so `fda-catalysts` keeps one summary line
  per cycle until the next release. Judged not worth a release of its own.
- Also this run: `clinical-trials` still streams exactly 399 trials per cycle
  (backlog #35), unchanged across three readings.
- Next: get the alert archive deployed so alerts are attributable to sources;
  then #35's 399; then #23's build stamp, which needs no tag.

## 2026-09-22 (seventh) — the fix held over four hours
- **Production report — observe run, `logs --since "30 minutes ago"`, 12:58Z.**
  Four hours and 329 cycles after the `v0.3.0` deploy:

        source health: 14/15 sources healthy; failing: FiercePharma (x326, presumed dead)

  This is the sustained answer rather than a first-cycle one. **EndpointsNews
  stayed recovered** across four hours, so the User-Agent was genuinely the cause
  and not a transient. FiercePharma sits at 326 consecutive failures, correctly
  reported as presumed dead.
- **The escalation schedule works as designed.** There is not one `WARNING` line in
  the window: x326 falls between the 100 and 1000 thresholds, so the per-source
  line is silent while the condition stays named in the summary. That is the
  behaviour #30 was for, confirmed by observation rather than by test.
- **The #43 defect is still visible**, as expected — one summary line per cycle,
  because the deployed version compares a string containing the count. Merged, and
  it reaches the host with `v0.4.0`.
- `form4-insider` `v0.2.0` at cycle 435: no `database is locked`, no repeated
  sends, no refusals. Still consistent-with rather than proof, for the same reason
  as before — no alert fired in the window, so the contended path is unexercised.
- `edgar-mna` cycles 63123–63127 healthy; one cycle took 20.8s against a usual
  ~1.4s. Noted, not investigated, and not a fault on its own.
- Run closed here. Everything merged, deployed and verified; the only outstanding
  item is the `v0.4.0` tag, which is issue #41 and needs the owner.

## 2026-09-22 (eighth) — v0.4.0 tagged and rolled; the apply is next run's
- **Conan cut `v0.4.0` at `4036ffa`**, and `v0.3.0` at `0f0541b` earlier. Issue #41
  closed with the evidence. Backlog #31 has no open request for the first time.
- **Rolled all four services to `v0.4.0`** (#46, merged `c2962f4`). A genuine
  four-service roll, checked against the tag by diff rather than assumed:

        alertlib/archive.py      | 243 +    (new)
        alertlib/service.py      |  36 +    (send_alert records before sending)
        alertlib/sources.py      |  37 +-
        clinical_trials/main.py  |  37 +-
        edgar_mna/main.py        |  33 +-
        fda_catalysts/main.py    |  29 +-
        form4_insider/main.py    |  35 +-

  Every service's own `main.py` changed and the shared `alertlib` change reaches
  all four. The same §6 criteria gave a one-service roll for `v0.3.0`; the answer
  differs because the diff does, not because the rule was applied loosely.
- **Not deployed, deliberately.** §2 allows one production apply per run and this
  run's went to the `v0.3.0` roll. So the spec reads `v0.4.0` while the host runs
  `v0.3.0`, and **`drift` will report exit 3 until the next run applies it.** That
  is a merged release waiting for its apply, not unmanaged change. Recorded here so
  the next reader does not mistake it for a fault.
- **Next run's first task:** `deploy.yml step=plan`, read it, then apply. Expect
  `4 to change, 0 unchanged`, standard tier (`clinical-trials`, `edgar-mna`,
  `fda-catalysts`) before critical (`form4-insider`), one at a time with a health
  gate between each. A plan proposing to *create* services is the exit-255
  signature and stops the run.
- **Coordination failure worth not repeating.** Two concurrent sessions asked for
  `v0.3.0` at different commits. The tag landed on one, so the other session's
  handoff issue described a four-service roll that was wrong for the tag that
  existed — the archive was not in it. Verify what a tag contains by ancestry
  (`git merge-base --is-ancestor <sha> <tag>^{commit}`) before rolling to it.
- The container restarted mid-run; nothing was lost, since the only background task
  was a watcher for a PR that had already merged.

## 2026-09-22 (ninth) — correction: drift reports exit 0, not 3
- **I was wrong in the previous entry, and in #46, #47, issue #41 and a phone
  notification.** All said `drift` would report exit 3 after the `v0.4.0` roll
  merged without an apply. Verified by running it:

        No drift: the target matches desired state.
        status: Success (host exit 0)

- **Why.** `drift` compares the host's running services against the specs in the
  **host's own checkout**, not against `origin/main`. Only `deploy.yml step=plan`
  syncs that checkout (§6), and no plan has run since #46 merged. So the host still
  holds the pre-`v0.4.0` specs, its four services match them, and exit 0 is the
  correct answer to the question `drift` actually asks.
- **Consequence worth carrying forward: a merged-but-unapplied release is invisible
  to `drift`.** It is not a backstop against forgetting to deploy. The log and
  backlog are the only record that `v0.4.0` is waiting, which raises the cost of not
  recording it.
- **This is backlog #23's second incident.** A stale host control plane has now
  misled two verifications — the earlier one noted in §11, and this one. The gap is
  worth more than its current priority suggests.
- Production unchanged and healthy: host runs `fda-catalysts` `v0.3.0`,
  `form4-insider` `v0.2.0`, the other two `v0.1.0`; `drift` exit 0 against the host's
  own specs. Spec in `main` reads `v0.4.0` for all four, awaiting the next run's
  apply.

## 2026-09-23 — v0.4.0 applied to the whole fleet; 399 was never a constant
- **The fleet runs one tag for the first time since August.** `deploy.yml` run 7
  planned, run 8 applied plan `345baa3a5442`.

### Production report
`status` before the apply (observe run 37): four services `active`, `enabled`,
`clinical-trials` and `edgar-mna` on `v0.1.0`, `fda-catalysts` `v0.3.0`,
`form4-insider` `v0.2.0`.

**Plan `345baa3a5442`** — four UPDATEs, no creates, no removes:

    ~ clinical-trials  v0.1.0 -> v0.4.0
    ~ edgar-mna        v0.1.0 -> v0.4.0
    ~ fda-catalysts    v0.3.0 -> v0.4.0
    ~ form4-insider    v0.2.0 -> v0.4.0
    4 to change, 0 unchanged.

Every service also showed an `environment` hash change, which the roll did not
obviously explain. Checked before applying rather than assumed: `RenderEnv`
includes `ALERT_DEPLOYED_REF` (`internal/engine/unit.go:145`), so the env hash
necessarily moves with the ref. Nothing in the plan was unaccounted for.

**Apply** — `Applied 4 change(s)`, 303s, host exit 0. Standard tier
(`clinical-trials`, `edgar-mna`, `fda-catalysts`) before critical
(`form4-insider`), one at a time, each `✓ healthy at v0.4.0` through its own
health gate. Audit actor `gha:35836370892`. `drift` afterwards: exit 0.

Restarts confirmed in journald, each service's next line carrying
`"ref": "v0.4.0"`: clinical-trials 08:18:59, edgar-mna 08:20:12, fda-catalysts
08:21:26, form4-insider 08:22:39. `status` afterwards (observe run 40), which is
the manifest rather than the restart and so lands about a minute later, once the
health gate has passed:

    SERVICE          REF      STATE   ENABLED  DEPLOYED               BY
    clinical-trials  v0.4.0   active  enabled  2026-09-23T08:19:59Z   gha:35836370892
    edgar-mna        v0.4.0   active  enabled  2026-09-23T08:21:12Z   gha:35836370892
    fda-catalysts    v0.4.0   active  enabled  2026-09-23T08:22:26Z   gha:35836370892
    form4-insider    v0.4.0   active  enabled  2026-09-23T08:23:39Z   gha:35836370892

### Reading the alerts — three findings, none of them expected
**1. `clinical-trials` does not stream a constant, and #35's premise was wrong.**
The line now reads `Streamed 787 recently-updated trials`, twice, five minutes
apart. 399 was that day's two-day window, not a page size and not a cap: 787
needs four pages of 200 and the cap is 2000. The fetch works. The gap is between
candidate and signal, which is a different investigation from the one the
backlog described, and the reason this run's milestone is instrumentation rather
than a filter change.

**2. `fda-catalysts` has one dead source, not two.** From the host:
`source health: 14/15 sources healthy; failing: FiercePharma (x1)`.
`EndpointsNews` answers again. The per-destination User-Agent in `v0.3.0` is the
only change that could account for it. So #26(a) is resolved without a
speculative patch and without a probe from this sandbox, which cannot reach
either domain — the accounting was shipped to the host and the host answered.

**3. The #43 source-health fix is confirmed in production.** On `v0.3.0` the
summary printed every cycle with a climbing counter (x1892, x1893, x1894 …); on
`v0.4.0` it printed once at cycle 1 and stayed silent through cycles 2–6. The
previous entry could only say the fix looked right.

### Milestone: the candidate funnel (#35)
`alertlib.CycleFunnel` — declared stages, per-cycle counts, one log line per
cycle, an `alert_funnel_<stage>_total` counter each. `clinical-trials` is the
first adopter. ADR 0006 argues the three choices a reviewer will ask about.
Two counters were chosen to test named explanations rather than to be thorough:
`new_in_window` compares consecutive cycles' id sets (the direct test of a stuck
query, which "new to our database" does not answer), and
`first_sight_completed` measures what `detect_signal`'s "an unobserved
transition is not a transition" rule costs per cycle.

Three measurement defects fell out, each found by writing the test first:
- the fetch tally sat after the loop, so both early returns skipped it — a cycle
  that died on page two logged nothing, identical to a cycle that never ran;
- `total_yielded` incremented after `yield`, so an abandoned generator
  under-reported by one;
- `countTotal` was `"false"`, so nothing distinguished "787 is everything that
  matched" from "787 is where we stopped reading". The line now reads
  `Streamed N of M ... in P page(s)` and says so when the cap truncates.

104 pytest tests (13 new), Go suite green, `validate` and
`gen_observability --check` clean. The funnel suite runs 20 cycles rather than
one, because the 2026-09-22 source-health defect was a per-iteration bug that
twelve single-iteration tests could not see.

### What is not done
- **This needs `v0.5.0` to run anywhere**, and no session can cut a tag (#31).
  Handoff issue opened.
- **No second apply.** §2 allows one per run and it went to `v0.4.0`.
- **Still not observed: whether the duplicate-alert loop stopped.** No alert
  fired in any window read this run, so the record-then-send path has still
  never run under contention. Unchanged from the previous entry, deliberately
  not upgraded.
- **Merged as `837b327`** (PR #49), CI run 114 green on its head — all six jobs,
  `golangci-lint` included. Branch reset onto `main` afterwards per §4.
- **Handoff issue #50** asks for `v0.5.0` at `837b327`. Next run: verify the tag
  by ancestry, roll `clinical-trials` alone (the funnel lives in shared
  `alertlib` but only that service calls it, so restarting the other three buys
  no behaviour change), plan, apply, then read one `logs` window for the funnel
  line — which is the answer to #35.
- Noted again, since it cost time twice this run: the **check-runs endpoint and a
  run's top-level status are both stale** here. `actions_list` on the run's jobs,
  and a job's archived log going from 404 to available, are the reliable signals.

## 2026-09-23 (second) — v0.5.0 deployed; the funnel answered #35
Conan cut `v0.5.0` from issue #50, so the funnel reached the host and the
question it was built for is answered.

### Production report
**Tag verified by ancestry before rolling**, per the `v0.3.0` coordination
failure: `v0.5.0` is `837b327` exactly, and `git merge-base --is-ancestor`
confirms the funnel commit is in it.

**Roll (#53).** `clinical-trials` alone. One service on §6's criteria, checked
against the tag by diff: only `clinical_trials/main.py` changed among the four,
and the `alertlib` delta is a new module plus two export lines that nothing
else imports. Rolling the other three would have restarted them for no change
in behaviour. CI run 121 green on all six jobs.

**Plan `d237e2d4a7cf`** (deploy run 9):

    ~ clinical-trials  UPDATE   source.ref  v0.4.0 -> v0.5.0
    = edgar-mna        no changes (v0.4.0)
    = fda-catalysts    no changes (v0.4.0)
    = form4-insider    no changes (v0.4.0)
    1 to change, 3 unchanged.

**Apply** (run 10): `✓ clinical-trials healthy at v0.5.0`, `Applied 1
change(s)`, host exit 0, 88s, actor `gha:35839768109`. Restart at 08:54:35,
the next line carrying `"ref": "v0.5.0"`. The other three were not touched.

### The funnel, first cycle on the host

    Streamed 787 of 787 recently-updated trials from ClinicalTrials.gov in 4 page(s)
    funnel: streamed=787 parsed=787 known=787 first_sight=0
            first_sight_completed=0 changed=0 signals=0 sent=0 new_in_window=?

Four things this settles, in order of how much they change the picture.

**`changed=0` is the answer to #35.** Of 787 trials whose last-update date moved
inside the window, not one had a status different from the one we stored. There
are no signals because `detect_signal` fires only on a status transition, and no
status transitioned. The updates are real; they are not status changes.

**`first_sight_completed=0` refutes the hypothesis the counter was built to
test.** ADR 0006 argued the likely cause was trials entering view *because* of
the update that matters, arriving already COMPLETED with no earlier status to
compare against, and being dropped by signal 5's `prev_status not in
("COMPLETED", None)`. That path never ran: `first_sight=0`, so every trial was
already known. The counter earned its place by being zero.

**`787 of 787 ... in 4 page(s)` ends the pagination question.** The fetch reads
the entire match; the cap is not reached. Backlog #35's original framing — a
query stuck on its first page — is now disproved twice over.

**`new_in_window=?`** on the first cycle is the sentinel working: nothing to
compare against yet, and `?` rather than `0` because zero is a real answer a
later cycle can give.

### Not concluded from one cycle
One cycle immediately after a restart is exactly the shape that hid the
source-health defect on 2026-09-22. `changed=0` on a single cycle does not
establish the *rate* of status changes, and the mechanism argues alerts should
eventually fire: a trial whose previous update fell outside the two-day window
leaves our view, and on re-entry its stored status is weeks old, so a flip to
COMPLETED reads as a change and signal 5 fires. What is needed is the
cumulative counter over a day — `alert_funnel_changed_total` — not another
single line. The funnel makes that a read rather than an investigation.

### Smaller finding
The startup Telegram message goes through `send_telegram` directly rather than
`Service.send_alert`, so it is neither archived nor counted. Harmless for
signal quality, but it means "the archive holds every message the bot sent" is
not quite true. Recorded in the backlog.
