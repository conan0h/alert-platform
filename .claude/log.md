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

### Second cycle, and `new_in_window` is the decisive number
Cycle 2 at 08:59:37, five minutes later:

    Streamed 787 of 787 recently-updated trials from ClinicalTrials.gov in 4 page(s)
    funnel: streamed=787 parsed=787 known=787 first_sight=0
            first_sight_completed=0 changed=0 signals=0 sent=0 new_in_window=0

**`new_in_window=0`: not one of the 787 ids is new since the previous cycle.**
The feed returns an identical set. That is expected rather than broken —
`LastUpdatePostDate` is date-granular, so membership of a two-day window can
only change when the date rolls — but it has a consequence worth stating: at a
300-second interval the service re-examines the same 787 rows about 288 times a
day, and the set it is watching refreshes once. The poll rate and the rate at
which the underlying data can move are three orders of magnitude apart.

Both counters agree across two cycles, which is more than the first line alone
could claim: the set is static and no status moved within five minutes.

### Not concluded from two cycles
Two cycles five minutes apart still do not establish the *daily* rate of status
changes — they establish that nothing moved in one five-minute gap, which is
about what you would expect even from a healthy feed. The mechanism argues
alerts should eventually fire: a trial whose previous update fell outside the two-day window
leaves our view, and on re-entry its stored status is weeks old, so a flip to
COMPLETED reads as a change and signal 5 fires. What is needed is the
cumulative counter over a day — `alert_funnel_changed_total` — not another
single line. The funnel makes that a read rather than an investigation.

### Smaller finding
The startup Telegram message goes through `send_telegram` directly rather than
`Service.send_alert`, so it is neither archived nor counted. Harmless for
signal quality, but it means "the archive holds every message the bot sent" is
not quite true. Recorded in the backlog.

## 2026-09-24 — the counters were unreadable; now they are not
The previous entry ends by saying this run should read
`alert_funnel_changed_total` over a day. Trying to is how the run found its
milestone: there is no way to read it, and there never was.

### Production report
All four services `active`, `enabled`, `/healthz` ok. `drift` exit 0, no drift.
`history` matches what the log claims — seven successful applies, no rollback
since August. Observe runs 51–55, all on `ed31117`.

    SERVICE          REF      STATE   ENABLED  DEPLOYED               BY
    clinical-trials  v0.5.0   active  enabled  2026-09-23T08:55:35Z   gha:35839768109
    edgar-mna        v0.4.0   active  enabled  2026-09-23T08:21:12Z   gha:35836370892
    fda-catalysts    v0.4.0   active  enabled  2026-09-23T08:22:26Z   gha:35836370892
    form4-insider    v0.4.0   active  enabled  2026-09-23T08:23:39Z   gha:35836370892

No deploy this run: the milestone is service code and no session can cut a tag
(#31). Handoff issue #56 asks for `v0.6.0` at `19ae3e2`.

### Reading the alerts
A clean 10-minute window, 08:09–08:19Z, all four services, nothing truncated —
asking for ten minutes instead of an hour is what makes the window whole (#39).
Cycle counters at the time of reading: `edgar-mna` 1902, `fda-catalysts` 1917,
`form4-insider` 719, `clinical-trials` 281.

**The window contains no alert, no error and no warning.** Every line is either
`poll cycle complete` or the `clinical-trials` funnel. That is the third
consecutive run with the same reading.

`clinical-trials`, cycles 280 and 281:

    Streamed 686 of 686 recently-updated trials from ClinicalTrials.gov in 4 page(s)
    funnel: streamed=686 parsed=686 known=686 first_sight=0
            first_sight_completed=0 changed=0 signals=0 sent=0 new_in_window=0

**686, where 2026-09-23 read 787 and 2026-09-21 read 399.** The two-day window
membership does roll with the date, which is the last thing the "stuck query"
theory could have been resting on. `new_in_window=0` within a day still holds.

### The milestone: #38, and why it was not #35
Reading `alert_funnel_changed_total` needs `/metrics`. Three facts, checked
rather than assumed:

- `/metrics` is served by `HealthServer` on the service's own port, bound to the
  host's loopback.
- the `health` verb curls `/healthz` and throws the body away —
  `deploy/ops/alert-deploy:245` redirects it to `/dev/null` and prints `ok`.
- a `metrics` read verb means editing `READONLY_VERBS` in the wrapper, which §2
  puts behind ADR 0004's adopter. Not mine.

So every cumulative counter in this fleet is recorded where nothing off the host
can read it, and **four questions across four backlog items turn out to share
one cause**: has any alert ever been delivered, is the archive filling (#27), how
often does a trial status move (#35), has a send ever been refused (#25). Each
had been written up as its own open question. They are one missing read path.

The fix is the move that settled the dead feeds: ship the reporting to where the
reader is. `Metrics.snapshot()` plus a 900-second schedule in `Service`, so the
whole registry goes to journald as one line — and once more at shutdown, because
a deploy replaces the process and its totals are not carried forward.

Merged as `19ae3e2` (PR #55), CI green on the merge commit, six jobs. 115 pytest
(11 new). Two tests carry the weight: one asserts `snapshot()` and `render()`
expose the same set of names, so the two paths cannot drift; the other is a real
regression test — removing the guard around the snapshot call fails it, verified
by removing it (1 failed, 10 passed), because a raise in `poll_cycle`'s `finally`
escapes the context manager and ends the caller's loop.

### What this run did not settle
- **The daily rate of trial status changes is still unknown.** The snapshot is
  the instrument, not the reading. It reaches the host with `v0.6.0`.
- **Still no alert in any observed window,** so the duplicate-send fix is still
  unproven under contention. Three runs now. Unchanged, deliberately.
- **`alertlib.__version__` is still `0.1.0`** while tags have reached `v0.5.0`.
  Noticed while touching the package; not worth a PR of its own, and it is the
  git tag rather than this string that the deploy pins.

### Note for the next run
The first `logs` read after `v0.6.0` is applied answers four open questions at
once. Grep `metrics snapshot`; the numbers are in the `metrics` object.
Two snapshots from one service, differenced against `alert_uptime_seconds`,
give a rate rather than a total — which is what #35 actually needs.

- Catch-up: production is healthy and unchanged; the fleet has still never sent
  an alert in any window I have read. The counters that would say whether it
  ever has were written where nothing could read them — fixed and merged, and
  it needs one tap from you on issue #56 to reach the host.

## 2026-09-25 — the host now says which alertctl answered (#23)
Backlog #23 had sat at P0 for several runs described as "a build-time commit
stamp", which reads as `-ldflags -X` on a build command that lives in the
wrapper and is therefore not mine. It was already in the binary: Go records
`vcs.revision`, `vcs.time` and `vcs.modified` in anything built inside a
readable git checkout, and the host builds as root in a root-owned checkout.
Checked before writing any code, against the exact command the wrapper runs:

    go version -m bin/alertctl | grep vcs   ->  vcs.revision=6c6674e38ec8…

### Production report
Observe runs 62–66 on `6c6674e`, before the change. All four services `active`,
`enabled`, `/healthz` ok; `drift` exit 0, no drift; `history` matches this log —
seven successful service applies across four pipeline runs, no rollback since
August.

    SERVICE          REF      STATE   DEPLOYED               BY
    clinical-trials  v0.5.0   active  2026-09-23T08:55:35Z   gha:35839768109
    edgar-mna        v0.4.0   active  2026-09-23T08:21:12Z   gha:35836370892
    fda-catalysts    v0.4.0   active  2026-09-23T08:22:26Z   gha:35836370892
    form4-insider    v0.4.0   active  2026-09-23T08:23:39Z   gha:35836370892

**No apply, and nothing to apply.** The milestone is control plane, which
reaches the host through a `plan` rather than a tag. `deploy.yml` run 11 planned
`107653b16bea`: `No changes. 4 service(s) match desired state.` That is the
expected plan for a control-plane change and the confirmation that it moved no
service. `v0.6.0` is still uncut, so the metrics snapshot is still not on the
host and issue #56 is still open.

**Verified after the plan**, observe run 67, which is the whole point of the
change:

    control plane: alertctl d11b09b07288 (committed 2026-09-25T08:35:54Z)
    SERVICE          REF        STATE      ENABLED    DEPLOYED               BY
    clinical-trials  v0.5.0     active     enabled    2026-09-23T08:55:35Z   gha:35839768109
    …

and from `ssm-run`, in the job summary:

    Control plane: alertctl d11b09b07288 — the commit this run was dispatched from.

### A correction the first host reading produced
The line first said `built 2026-09-25T08:35:54Z`. The binary was built at
08:36:3x, during the plan; 08:35:54Z is when the merge commit was made.
`vcs.time` is the time associated with `vcs.revision`, not the build. Relabelled
to `(committed …)` in the follow-up, with the JSON field renamed to match.
A wrong label on an operator-facing timestamp is the kind of thing that gets
believed for months, and it took production output to see it — the local test
fixtures all said "built" too, because I wrote them from the same assumption.

### Reading the alerts
A clean ten-minute window, 08:14–08:23Z, all four services, nothing truncated.
Cycle counters: `edgar-mna` 3801, `fda-catalysts` 3843, `form4-insider` 1441,
`clinical-trials` 570. No alert, no error, no warning — the fourth consecutive
run with that reading.

`clinical-trials`, cycles 569 and 570: `Streamed 602 of 602 … in 4 page(s)`,
`changed=0 signals=0 sent=0 new_in_window=0`. The window is 399 → 787 → 686 →
602 across four days, so membership keeps rolling and the change rate keeps
reading zero.

**One line I had read past three times**, INFO, hourly, from `form4-insider`:

    "msg": "alpha cutoff refreshed", "cutoff": null

`get_alpha_cutoff` returns `None` when no insider has five or more scored
trades, and `should_alert` then refuses everything under $1M with `no
leaderboard cutoff available`. So the "top 25% of scored insiders" filter the
service is built around cannot fire at all: the live filter is "any trade over
$1M", and the $100k floor plus the alpha comparison are unreachable. The
leaderboard is populated by `form4_scorer.py` after `form4_backfill.py`, neither
of which is in the fleet spec — they are manual scripts with no timer. Backlog
#39, and it is a better candidate for the next milestone than anything else
open: it is one of the four feeds emitting nothing, and this is a reason.

### What this run did not settle
- **`v0.6.0` is still uncut**, so the metrics snapshot is still unreadable on
  the host and the four questions it answers stay open. Issue #56, unchanged.
- **Still no alert in any observed window** — four runs. The duplicate-send fix
  remains unproven under contention.
- Merged `d11b09b` (PR #58), six CI jobs green. Branch reset onto `main` after.

- Catch-up: production is healthy and unchanged, and every read of it now names
  the commit that answered — the gap that had misled two verifications is
  closed and proved on the host. The one new finding is `form4-insider`: its
  insider-quality filter has never been able to fire, because the leaderboard
  it depends on is empty. Still waiting on you for the `v0.6.0` tag (issue #56).
