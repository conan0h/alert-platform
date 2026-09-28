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

### The correction reached the host in the same run
`deploy.yml` run 12 planned `107653b16bea` again — the same id, because a plan
id fingerprints the change set and the change set is still empty: `No changes.
4 service(s) match desired state.` It rebuilt the binary from `fb753ba`, and
observe run 68 read `drift` back:

    control plane: alertctl fb753ba9b8db (committed 2026-09-25T08:42:31Z)
    No drift: the target matches desired state.

    Control plane: alertctl fb753ba9b8db — the commit this run was dispatched from.

So the host is not left printing a label the merged docs call wrong, and the
`drift` half of the change is verified in production as well as the `status`
half. Two plans this run, no apply — §2 caps applies, and a plan that finds
nothing to change is how a control-plane change ships.

- Catch-up: production is healthy and unchanged, and every read of it now names
  the commit that answered — the gap that had misled two verifications is
  closed and proved on the host. The one new finding is `form4-insider`: its
  insider-quality filter has never been able to fire, because the leaderboard
  it depends on is empty. Still waiting on you for the `v0.6.0` tag (issue #56).

## 2026-09-26 — form4-insider is not filtering; it is not fetching
Backlog #39 said the leaderboard was empty and three of four filter branches
were dead. That is true and it is not the whole picture: the service is not
reaching the filter at all.

### Production report
All four services `active`, `enabled`, `/healthz` ok. `drift` exit 0, no drift.
`history` matches this log — seven successful service applies across four
pipeline runs, no rollback since August. Observe runs 74–78, all answered by
`alertctl fb753ba9b8db` (the run was dispatched from `46ffe5f`; read verbs never
rebuild, and the workflow said so).

    SERVICE          REF      STATE   ENABLED  DEPLOYED               BY
    clinical-trials  v0.5.0   active  enabled  2026-09-23T08:55:35Z   gha:35839768109
    edgar-mna        v0.4.0   active  enabled  2026-09-23T08:21:12Z   gha:35836370892
    fda-catalysts    v0.4.0   active  enabled  2026-09-23T08:22:26Z   gha:35836370892
    form4-insider    v0.4.0   active  enabled  2026-09-23T08:23:39Z   gha:35836370892

No apply. `v0.6.0` is still uncut (issue #56, unchanged since my comment on the
25th), so there is nothing to roll, and this run's merge is a service change
that needs a tag of its own.

### Reading the alerts — 08:13–08:22Z, ten minutes, nothing truncated
**`form4-insider`'s cycles take 0.21s.** Cycles 2155, 2156 and 2158 at 0.21,
0.20 and 0.21 seconds. Fetching one filing's primary XML from EDGAR and parsing
it costs more than that on its own, so no filing was fetched in any of them: the
whole budget is the feed fetch, and every accession the feed returned was
already in `alerted`. Three runs have now reasoned about which filter branch is
refusing. None of them runs. `duration_sec` has been in every window read for
weeks, beside the cycle counter that was being read — learnings entry.

**Cycle 2157 died on a 30-second read timeout to `www.sec.gov`**, caught by
`poll_cycle`, service unaffected. `edgar-mna` timed out on the same host at
08:21:46. Transient SEC slowness, not ours; noting it because the next unhandled
EDGAR failure should be read against this rather than as new.

**`edgar-mna`'s `PRNewswire-AllNews` 404s**, twice in the window, on
`https://www.prnewswire.com/rss/news-releases-list.rss/` — a trailing slash
before the query string. `fda-catalysts` hits the same host and mostly succeeds,
recovering from two 404/503 blips in the same ten minutes, so this reads as
PRNewswire being inconsistent rather than as a URL bug of ours. Two samples is
not a finding; backlog note, not a fix.

**`fda-catalysts`: 14/15 healthy, `FiercePharma (x5739, presumed dead)`.**
Unchanged and correctly reported.

**`clinical-trials`: `Streamed 843 of 843 … in 5 page(s)`**, `changed=0
signals=0 sent=0 new_in_window=0`. The window is 399 → 787 → 686 → 602 → 843
across five days: membership rolls, the change rate still reads zero.

### The correction that matters more than any of the above
**All five "no alert in any observed window" readings are the same hour.** The
schedule fires at ~08:15 UTC, which is 04:15 ET. Form 4s are filed after the US
close and EDGAR's current feed is static overnight, so the quietest hour of the
day has been read five times and treated as five observations. The readings were
right; the inference from them was not. In particular, "the duplicate-send fix
is unproven under contention" is a statement about the sampling. Learnings entry,
and the second reason cumulative counters beat windows.

### Milestone: the form4 funnel (#39 a and c)
PR #61, merged. `form4-insider` adopts `alertlib.CycleFunnel`, and
`should_alert` returns the *name* of the branch it took rather than a bare
boolean, so the last nine stages of the funnel line are a histogram of the
filter itself and the decision cannot drift from its measurement. The six stages
before them separate the five silences that produce identical output today: an
empty feed, a feed of filings already handled (the live case, per the 0.21s
cycles), a failed fetch, unparseable XML, and a dedup write that refuses the
send.

The leaderboard half answers #39(a): `insiders`, `eligible`, `scored` and
`transactions` row counts, hourly and as gauges, because `cutoff: null` cannot
say whether the backfill has never run or only the scorer has. The hourly line
is renamed `alpha cutoff refreshed` → `leaderboard state`; anything grepping for
the old string needs updating.

Two honesty fixes fell out of writing it: the startup Telegram message
advertised "insider must be top 25% by 90d alpha" while that branch was
unreachable, and reported insiders who clear the five-trade bar as "scored"
when none of them is scored. Both now say what is true.

25 new tests, 140 total, all six CI jobs green on `004bb65`; merged as
`82d7380`. Second-adopter
section appended to ADR 0006 rather than a new ADR: the decision to instrument a
silent service was made there, and this applies it.

### What this run did not settle
- **`v0.6.0` is still uncut.** Two merged service changes now wait on it: the
  metrics snapshot (#55, `19ae3e2`) and this funnel (#61, `82d7380`). Issue #56
  retargeted to `82d7380`, which contains both — tagging the earlier commit
  would need a second tag immediately.
- **#39(b) is untouched, deliberately.** Whether `form4_scorer.py` becomes a
  managed unit or the filter stops depending on it is a signal-quality decision
  and should be made against the row counts, which need the tag.
- **No apply**, so nothing new is verified in production this run.

- Catch-up: production is healthy and unchanged. Two corrections worth your
  time: `form4-insider` is not reaching its filter at all — its cycles finish in
  a fifth of a second, which is the feed fetch and nothing else — and every
  "we've never seen an alert" reading I have recorded is from 04:15 New York
  time, when nothing is filed. Both are now instrumented rather than guessed at,
  and both need the `v0.6.0` tag (issue #56) to reach the host.

## 2026-09-27 — a different hour of the day, and what it showed
The previous entry's correction was that every reading came from 04:15 ET. This
run acted on it before choosing a milestone, and the milestone came out of what
the new hour showed.

### Production report
All four services `active`, `enabled`, `healthz=ok`. `drift` exit 0, no drift.
`history` matches this log — seven successful service applies across four
pipeline runs, no rollback since August. Observe runs 85–89, all answered by
`alertctl fb753ba9b8db` (dispatched from `e3be39f`; read verbs never rebuild,
and the workflow said so).

    SERVICE          REF      STATE   ENABLED  DEPLOYED               BY
    clinical-trials  v0.5.0   active  enabled  2026-09-23T08:55:35Z   gha:35839768109
    edgar-mna        v0.4.0   active  enabled  2026-09-23T08:21:12Z   gha:35836370892
    fda-catalysts    v0.4.0   active  enabled  2026-09-23T08:22:26Z   gha:35836370892
    form4-insider    v0.4.0   active  enabled  2026-09-23T08:23:39Z   gha:35836370892

No apply. `v0.6.0` is still uncut, so there is nothing to roll.

### Reading the alerts — 02:21–02:42Z, which is 22:21 ET
**The truncation bug is a sampling lever.** `logs` returns the *oldest* part of
its window (#32), so `since=6 hours ago` at 08:22 returns 02:21 onward. Five
runs had read 04:15 ET because that is when the schedule fires; one input
change reads the post-close hour instead, at no cost. Worth keeping when #32 is
fixed — the fix should let a caller ask for the head of a window deliberately
rather than removing the only way to get it.

**`form4-insider` does reach its filter, about once an hour.** Cycles 2698–2708
ran 0.13–0.42s — the feed fetch alone — **except cycle 2702 at 2.32s**, about
the cost of fetching and parsing one filing's XML. It found something, and sent
nothing. That narrows the previous entry's "no filing is being fetched at all":
true of most cycles, not of all of them, and the filter is running after all.
Which branch refuses is still the funnel's answer, and the funnel still needs a
tag.

**`clinical-trials`: `530 of 530 ... in 3 page(s)`**, `changed=0 signals=0
sent=0 new_in_window=0`. Six days: 399 → 787 → 686 → 602 → 843 → 530. The
window rolls; the change rate still reads zero, now at a different hour.

**`edgar-mna`'s PRNewswire-AllNews failed four times in twenty-one minutes** —
a read timeout, a 502, a 503 and three 404s. The trailing-slash theory from
#40 is answered and it is not ours: the 404s name the slashed URL and the
502/503s name the unslashed one, which is `requests` reporting the URL *after*
redirects. PRNewswire redirects and then 404s inconsistently.

### Milestone: source health for edgar-mna, and a rule for flapping (#40)
PR #63, merged as `d0b33cf`. Two halves, one window's evidence.

**`edgar-mna` reports per-source health**, so its sixteen feeds — eleven wires
and five SEC — are accounted for like the other three services. The gap it
closes is not the PRNewswire failure, which was already visible; it is that
nothing could say whether a feed had failed twice or had been dead for three
weeks, and nothing ever stated the whole picture. Fifteen of the sixteen could
have been dead and the journal would have looked normal.

**A blip is now distinguished from an outage.** Adopting the tracker unchanged
would have made this service's journal *worse*: at one warning per failure plus
one per recovery plus a summary either side, one blip costs four lines, and
PRNewswire blips roughly one cycle in five. That is not hypothetical —
`fda-catalysts` produced the full four-line sequence twice for
PRNewswire-Biotech inside the same twenty-one minutes. So `LOG_AT_FAILURES`
starts at 2, a run that was never announced recovers silently, and the summary
shape ignores a source that is not yet reportable. A source that is 80% fine
had been out-logging one that is dead.

The counters keep every failure, which is the point: the journal reports
conditions, the counters report rates. Deciding whether PRNewswire is flaky or
dying needs `alert_source_fetch_failures_total` over a day, not more log lines.

150 pytest (10 new), all six CI jobs green on `473c001`. The four rewritten
tests in `test_source_health.py` were verified to fail on the old constant by
reverting it (4 failed, 14 passed). Branch reset onto `main` after the merge.

### What this run did not settle
- **`v0.6.0` is still uncut.** Three merged service changes now wait on it —
  #55, #61 and #63. Issue #56 retargeted to `d0b33cf`, which contains all three.
  Two of them are in `alertlib`, so all four services roll.
- **No apply**, so nothing new is verified in production this run.
- **Whether `edgar-mna`'s other fifteen feeds work is still unknown.** The
  instrument for it is merged; the reading is the first `logs` window after the
  tag.

- Catch-up: production is healthy and unchanged. Reading a different hour of
  the day cost one dropdown and was worth it — `form4-insider` does reach its
  filter about once an hour and refuses what it finds, which is not what the
  last two runs concluded. Three service changes are now finished and waiting
  on one tap from you: issue #56.

## 2026-09-28 — v0.6.0 on all four, and the counters answered
Conan cut `v0.6.0` at `d0b33cf` from handoff issue #56. Three merged service
changes had been waiting on it since the 24th. They are now on the host, and the
first snapshot window answered more than it was built to.

### Production report
**Tag verified by ancestry before rolling**, per the `v0.3.0` coordination
failure: `v0.6.0` is `d0b33cf` exactly, and `19ae3e2` (#55), `82d7380` (#61) and
`d0b33cf` (#63) are all ancestors of it.

Pre-deploy, observe runs 95–98 on `04a42f6`, answered by `alertctl fb753ba9b8db`:
all four `active`, `enabled`, `healthz=ok`; `drift` exit 0, no drift; `history`
matching this log at seven successful applies and no rollback since August.

**Roll (#65).** All four services, checked by diff rather than assumed:
`alertlib` changed (`service.py`, `health.py`, `sources.py`, `funnel.py`) and
every service imports it, so §6's shared-change clause genuinely applies —
unlike `v0.5.0`, which rolled one. `fda-catalysts` was the only service with no
code change of its own, and it is not a no-op restart: it gains the snapshot and
the second-failure escalation rule. Six CI jobs green on `89fe764`.

**Plan `2880151f4eb5`** (deploy run 13): four UPDATEs, `4 to change, 0
unchanged`, no creates and no removes.

**Apply** (run 14): each service gated in turn, `✓ … healthy at v0.6.0` four
times, `Applied 4 change(s)`, host exit 0, 305.9s, actor `gha:36397285647`.
Restarts at 08:27:02, 08:28:15, 08:29:30 and 08:30:43. Eleven service applies
have now gone through the pipeline and none has needed a rollback. Observe run
99 confirms all four at `v0.6.0`, and the control-plane stamp now reads
`89fe7643045a — the commit this run was dispatched from`, because a `plan`
rebuilds it.

### The snapshot works, and one number in it is a trap
Four `metrics snapshot` lines, one per service, whole registry, with
`alert_uptime_seconds` beside the counters. Backlog #38 is closed in production:
every cumulative counter in this fleet is now readable off the host.

**`alert_alerts_sent_total` reads 1 on all four services, and no alert has been
sent.** It is the startup Telegram message. The counter is incremented inside
the transport (`telegram.py:107`), not inside `Service.send_alert`, so the "bot
started" message counts even though it is not archived. Every funnel line in the
window says `sent=0` and `alert_funnel_sent_total` is 0 on both services that
have one, which is what makes the attribution certain rather than likely.

This matters because issue #56 said this counter "answers whether this fleet has
ever delivered an alert at all". It does not: it reads 1 on a fleet that has
delivered none. Backlog #37 is also half wrong — it says the startup message is
"neither recorded nor counted", and the counted half is untrue.

**`alert_archive_records_total` is absent from all four snapshots.** Not zero —
absent. `AlertArchive` is constructed lazily (`service.py:112`) so a service that
has not archived anything never declares the counter. The metric that was meant
to answer "is the archive filling" is not emitted by the services that most need
to answer it, and an absent metric cannot be told apart from one that was never
added. #27's question is still open, and #38 did not close it.

### form4-insider: the candidates stop at the first stage, not the filter
    funnel: entries=100 new=0 fetched=0 parsed=0 claimed=0 transactions=0
            code_not_actionable=0 planned_sale=0 below_floor=0 large_trade=0
            no_insider_history=0 thin_history=0 no_leaderboard=0
            below_cutoff=0 top_tier=0 sent=0 new_in_window=0

**`entries=100, new=0`.** The feed returns 100 entries every cycle and not one is
new; `alert_funnel_entries_total` is 900 over nine cycles with
`alert_funnel_new_total` at 0. Every accession is already in `alerted`, so
nothing is fetched, nothing is parsed, and **no filter branch runs at all** —
`no_leaderboard=0`, not a positive count.

Three runs reasoned about which filter branch was refusing candidates. The
answer is that none of them was reached. The stage that silences this service is
dedup against `alerted`, one step after the feed.

**The leaderboard is full and unscored, which corrects #39(b)'s premise:**

    alert_leaderboard_insiders: 13782    alert_leaderboard_transactions: 32592
    alert_leaderboard_eligible: 0       alert_leaderboard_scored: 0

`form4_backfill.py` has run — 13,782 insiders and 32,592 transactions are
sitting there. `form4_scorer.py` has not: nothing is scored, so nothing is
eligible, so `get_alpha_cutoff` returns `None`. The item was written as "the
table is empty"; it is full and unscored, which is a different fix.

And it is a fix that would change nothing today. While `new=0`, a fully scored
leaderboard produces zero extra alerts, because no candidate reaches the branch
that would read it. **#39(b) is downstream of the `new=0` question and should not
be done first.**

### clinical-trials read `0 of 0`, and that is correct
    Streamed 0 of 0 recently-updated trials from ClinicalTrials.gov in 1 page(s)
    funnel: streamed=0 parsed=0 known=0 first_sight=0 first_sight_completed=0
            changed=0 signals=0 sent=0 new_in_window=0

Every previous reading returned hundreds: 787, 686, 602, 843, 530. Zero looks
like a break, and I nearly wrote it up as one. It is the query working.

`fetch_recent_changes` asks for `LastUpdatePostDate` in the last two days. At
08:41 UTC on Monday 28 September that window is Saturday, Sunday and Monday
before the US business day — **the only two-day window in the week that contains
no business day**, and today is the first Monday since `v0.5.0` shipped the
funnel on the 23rd. Each earlier reading's window contained at least one weekday:
Sat 26 reached back to Thursday, Sun 27 to Friday.

The Monday a week earlier read 399 and is not a counter-example: that was
`v0.1.0`, before `countTotal` existed, so 399 was what it streamed rather than
what matched. Checked in the archive rather than recalled.

`countTotal` is what makes this readable at all. Without it, `streamed=0` could
not be told apart from a failed read, and `0 of 0` says the API reported an
authoritative match of zero. The counter that came back zero paid for itself
again.

**The product consequence is sharper than the diagnosis**, and it belongs to
#35's open decision: a two-day window blinds this service every Monday morning
and ages Friday's updates out over the weekend. `days_back=4` would carry Friday
across. That is a signal-quality call for Conan, now with a concrete reason.

### edgar-mna's sixteen feeds, measured for the first time
    alert_source_fetches_total: 182    alert_source_fetch_failures_total: 1
    alert_sources_failing: 0           alert_sources_presumed_dead: 0

**One failure in 182 fetches.** The 2026-09-27 entry put PRNewswire at "roughly
one cycle in five" from a twenty-one minute window. Over 22 cycles the rate is
0.5%. That entry said deciding whether PRNewswire is flaky or dying needed the
counter rather than more log lines; the counter's answer is that the
one-in-five figure was a bad sample. Nobody had ever had a number for these
sixteen feeds before this window.

`fda-catalysts` is unchanged and correctly reported: `14/15 sources healthy;
failing: FiercePharma (x20, presumed dead)`, a 403 on every cycle.

**The new escalation rule is doing exactly what it was built for.**
`alert_source_fetch_failures_total` is 23 while FiercePharma accounts for 20, so
three transient failures happened in the window and produced no log line at all:
a failing run that was never announced recovered silently. Under the old rule
those three would have cost up to twelve lines.

### Still not settled
- **`alert_sends_refused_total` is 0 everywhere**, so #25's duplicate-send fix is
  still unproven under contention — but it is now a cumulative number rather
  than a window, which is the better version of the check.
- **Whether the archive holds anything** — see the absent counter above.

### A plan-output defect found by reading the plan
Each of the four UPDATEs reported `environment … (polling, delivery, health or
state config changed)`. None of that config changed. `ALERT_DEPLOYED_REF` is part
of the rendered environment (`unit.go:145`) and the env hash is computed at the
desired ref (`plan.go:155`), so **a ref roll moves the env hash on its own and
trips a reason that names four things, none of them true.**

It fired four times in the one apply this run. §6 tells the operator to read the
plan and refuse a surprising one, so a line that cries wolf on every release is
a safety problem and not a cosmetic one: an operator trained to expect "config
changed" on every roll will not notice the roll where config actually did change.
Backlog item added with the fix, which is to recompute the hash at the observed
ref and say which of the two it was.

- Catch-up: `v0.6.0` is live on all four services — thank you for the tag; it
  went out cleanly and nothing rolled back. The instrumentation paid for itself
  immediately. `form4-insider`'s silence is not the filter anyone has been
  blaming for four runs: its feed returns 100 filings a cycle and every one is
  already handled, so no filter branch runs. Its leaderboard turns out to be
  full (13,782 insiders) but entirely unscored. And one thing to distrust:
  `alerts sent = 1` on each service is the "bot started" message, not an alert —
  this fleet has still never sent a real one.
