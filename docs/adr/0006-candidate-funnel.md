# ADR 0006 — A service counts the stages between a candidate and an alert

**Status:** accepted, 2026-09-23

## Context

`clinical-trials` has alerted on nothing for weeks while examining hundreds of
trials per cycle. Backlog #35 recorded it on 2026-09-21 as "streams exactly 399
trials every cycle", and read the constant as evidence of a page size or a cap —
a query stuck on its first page.

That reading was wrong, and the evidence arrived on 2026-09-23: the same line on
the host now reads 787. The count tracks the real size of the two-day window
across four pages. The fetch is working.

So the gap is between candidate and signal, and nothing recorded it. The service
logged how many trials it streamed and how many alerts it fired, with nothing in
between. Three quite different faults produce byte-identical output:

- the filter is tighter than intended and nothing can match it;
- the upstream query returns the same rows every cycle, so no transition is ever
  new;
- a transition is observed one cycle too late to count as a transition.

`SourceHealth` (ADR 0005's neighbour, added for #26) does not help. It answers
"is the feed answering", and the feed is answering.

## Decision

A service declares the stages its candidates pass through. `alertlib.CycleFunnel`
counts each stage during a cycle, exposes each as an `alert_funnel_<stage>_total`
counter, and logs one line per cycle naming every stage.

Three choices are worth defending.

**Stages are declared, and counting against an undeclared one raises.** The
alternative — create the counter on first use — turns a typo into a plausible
line of output showing a stage nobody counts and a stage at zero forever. This
module exists because a measurement was missing; a measurement that lies is a
worse outcome than the one being fixed. The cost is that a stage name added in
one place and not the other fails at the first cycle that reaches the branch,
which is why `observation_stages` is a pure function with a test asserting every
name it can return is declared.

**Cohort churn is part of the funnel, not a separate count of new rows.**
`new_in_window` compares this cycle's candidate ids against the previous cycle's.
Counting candidates new to the service's database looks equivalent and is not: a
candidate can be long known and still be a fresh arrival in the polling window,
so only the cycle-to-cycle comparison tests whether the upstream query has stuck.
Before any comparison exists the line reports `?`, because zero is a real answer
a later cycle can give and a sentinel must be a value the domain cannot produce.

**The line is emitted once per cycle, unconditionally.** `SourceHealth`
deliberately does the opposite — it logs on a widening schedule, because an
unconditional warning per failed fetch cost about 1,900 lines per source per day
and displaced alert content from the 24 KB the `logs` verb returns. The funnel
is one line per cycle rather than one per candidate: 288 lines a day at
`clinical-trials`' 300-second cadence. That is affordable, and unlike source
health the value is in comparing consecutive cycles, which a schedule that skips
the quiet ones destroys. The metrics carry the same numbers for anyone who would
rather not read logs at all.

## Consequences

`clinical-trials` is the first adopter. Its stages are `streamed`, `parsed`,
`known`, `first_sight`, `first_sight_completed`, `changed`, `signals`, `sent`.

`first_sight_completed` is there to test one specific explanation, and is the
reason this ADR exists rather than a comment. The query selects trials whose
last update landed within two days, so a trial usually enters view *because* of
the update that matters — and when that update is the flip to COMPLETED, there
is no earlier status to compare it against. `detect_signal` drops that case:
signal 5 requires `prev_status not in ("COMPLETED", None)`, on the reasoning that
an unobserved transition is not a transition. That reasoning is defensible and
its cost has never been measured. If the counter is large every cycle, the
filter is not too tight and the fix is to decide whether a trial first seen
already COMPLETED, whose update landed inside the window, is an event worth
alerting on. That is a signal-quality decision, and it should be made against a
number rather than an impression.

The other three services can adopt the funnel by declaring their own stages.
Nothing in `CycleFunnel` is specific to trials. They are not adopted here:
`edgar-mna` and `fda-catalysts` do alert, so the question this answers is not
open for them, and adopting it everywhere to be uniform would be breadth for its
own sake.

This is measurement, not a fix. It changes no filter and no alert. The next run
reads the line from the host and then knows which of the three faults above it
is looking at — which is the step backlog #35 has been missing since it was
opened.
