# Backlog

Open work only, highest value first. A finished item is deleted, not moved to a
"Done" list: git history and `log-archive.md` hold the record. Each item says
what is wrong, why it matters, and the next concrete step — not how it was
found. Numbers are stable references; don't renumber.

Statuses: `todo`, `in-pr #N`, `needs-conan`, `blocked: <reason>`.

## Target: alerts worth acting on

**The fleet alerts; nobody has judged whether the alerts are any good.**
Every item in this section works toward reading what the bots sent and improving it.
Don't start lower sections while this one has unblocked work.

45. **Judge the alerts.** `todo`
    Read the digests after a US session (`logs` `since="30 minutes ago"`
    from ~21:00Z). For each alert record ticker and time, and whether the
    price moved after it; `clinical-trials` is the least understood. First
    case: edgar's `10-04T21:08Z` PTC takeover rumour. Confirm
    `alert_funnel_planned_sale_total` goes non-zero after a session; if it
    stays 0, #69's element name is wrong. For the two news services, read
    what the 90%+ `unclassified` entries are before tuning the filter.

39. **`form4-insider` can only alert on trades over $1M.** `todo`
    The leaderboard behind its main filter is unscored: 13,782 insiders,
    32,592 transactions, 0 scored, because `form4_scorer.py` has never run.
    576 trades of $100k–$1M stopped at `no_insider_history` in ~5 days. The
    price source works on the host (probe 2026-10-05: SPY 200, 19 closes).
    Next: run the scorer daily — a timer unit (spec and deploy-lifecycle
    change, ADR) or an in-process step after the US close (no new unit).
    Prefer in-process unless scoring takes long enough to stall the poll.

46. **`form4-insider` loses most of what its feed calls new.** `todo`
    Under `v0.6.0`: `new` 9076, `fetched` 5703, `parsed` 3313; a failed fetch
    or parse is never retried. `v0.7.0` counts `not_form4`,
    `index_unavailable`, `no_form4_xml`, `xml_unavailable` and `unparsed`.
    Next: read them after a session. If new→parsed is still well under
    100%, retry transient failures while the filing stays in the feed
    (~1.5 h), or fix the XML selection rule.

## Measurement

27. **Nothing can read the alert archive.** `todo` for (d); (c) blocked on #24
    Alerts are recorded per service in SQLite (ADR 0005). (d) a read-only
    console panel that unions the four databases via `AlertArchive.recent()` —
    unblocked. (c) an `alerts` read verb — needs the wrapper change in #24.

## Deploy safety

22. **No end-to-end test target.** `todo` (large; slice it)
    A container with sshd and a systemd stand-in that CI can run plan → apply →
    drift → rollback against, including `bootstrap-host.sh`. Most deploy-path
    bugs so far were found in production because nothing ran these scripts.

## Needs Conan

24. **Wrapper adoption.** `needs-conan`
    Decided in ADR 0004; the sandbox refuses to let the agent author the
    privileged installer. Conan commits that one file, then the rest is ours.
    Unblocks #27(c) and #32.

32. **`logs` returns the oldest part of its window.** `blocked: #24`
    Fix in the wrapper: bound output with `journalctl -n` and add a direction, so
    a caller can ask for either end (reading the oldest end is how we reach the
    post-close hour today). Also: the wrapper's `valid_since` accepts `30m`,
    which journalctl rejects.

36. **Root account access keys are in use, and port 22 is open.** `todo` / `needs-conan`
    Close port 22 through Terraform (ours). Deleting root keys needs Conan.

34. **Terraform has no `.terraform.lock.hcl`.** `needs-conan`
    Needs registry access this session lacks. Conan runs
    `terraform providers lock` in CloudShell, or allowlists the registry.

- **Environment:** pin `golangci-lint` v1.59.1 in the image, or migrate the
  config and CI to v2 together, so lint runs locally.

## Verify when it happens

- **#25, duplicate sends:** `form4-insider` sent 46 with
  `alert_sends_refused_total` 0. Confirm from the first digest that no alert
  repeats.

## Parked — don't start while no service alerts

One line each; promote with a reason.

- #7 content-drift detection (hash release dirs, compare in `drift`)
- #8 `state.backup` job for the SQLite databases
- #9 consume `dedup.keys` in `alertlib`
- #10 SLOs and burn-rate alerts generated from specs
- #13 delivery metrics from the audit log (`history --stats`)
- #14 fault-injection matrix for `apply`
- #15 supply chain in CI (`govulncheck`, `pip-audit`, Dependabot)
- #16 bake time between tiers
- #18 console polish
- #19 dry-run demo (`make demo`)
- release automation, so a tag doesn't need Conan each time
