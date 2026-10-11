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

48. **GlobeNewswire refuses the host.** `todo`
    Since ~13:20Z 10-09 GlobeNewswire answers 403 on four `edgar-mna` feeds
    and three `fda-catalysts` feeds; FiercePharma 403 too. Both User-Agents
    are refused, so the block is not specific to the SEC contact string.
    `main` (#100) retries a dead feed every 15 minutes instead of every 45s.
    Next: after the `v0.8.1` deploy, check whether any GlobeNewswire feed
    recovers once the request rate drops. If none does in a week, find
    another source for those wires; do not evade the block with a browser
    User-Agent.

45. **Judge the alerts.** `todo`
    Read the digests after a US session (`logs` `since="30 minutes ago"`
    for three services, then `"10 minutes ago"` for the fourth: the 24 KB cap
    cuts the last snapshot). For each alert record ticker and time, and
    whether the price moved after it. No price source reaches this session,
    so a move check needs the host to report it (or #27). Open cases: PTC
    after edgar's `10-04T21:08Z` rumour; PCRX after Viatris 10-08 11:46Z;
    CMG after the Starbucks report 10-08 14:36Z.
    Misclassified on 10-09: MTY "Announces End of Strategic Review" as
    STRATEGIC_REVIEW; Atossa "Executes Contingent Value Rights Agreement"
    as PRIORITY_REVIEW. Both matched a phrase without its negation or context.

47. **`clinical-trials` alerts on trials nobody can trade.** `todo`
    10-06: 30 alerts in one 12:11–12:13Z burst; many academic. `main` (#90)
    records `leadSponsor.class` in each title (`TERMINATED [OTHER]: …`) and
    counts `signals_industry` beside `signals` (deployed 10-11). Next: after a
    week of those counters, decide whether to drop non-`INDUSTRY` signals behind a
    `spec.polling` key. Also: `RESULTS_POSTED` claims "BEFORE press
    release" for any trial, however old; compare `resultsFirstPostDate`
    with the completion date before calling it fresh.

39. **`form4-insider`'s leaderboard is new and small.** `todo`
    The in-process scorer (#92) ran on the `v0.8.0` deploy: 208 insiders
    scored, 20 eligible (5+ buys), alpha cutoff 11.5%, so `top_tier` alerts
    are now possible. Next: after a weekday session, read which `top_tier`
    trades it sent. Unexplained: every top-15 row prints `a180=+0.0%`
    (`(a180 or 0)` renders NULL as 0). That is expected only if no scored
    buy is 185+ days old, yet the backfill default is 2 years; have the
    scorer log the buy date range and the count with `fwd_ret_180`. Also
    the weighted `alpha_180` divides by the value of buys without a 180-day
    return, biasing it toward 0. The gate reads only `alpha_90`; alert text
    shows `180d:`.

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
  CI's `config verify` fetches its schema from golangci-lint.run at run time;
  a timeout there failed main's push run on `f0208ff` (10-11).

## Verify when it happens

- **`v0.8.0`, first weekday digest:** form4 sends one `(N tx)` alert per
  filing; clinical titles carry `[INDUSTRY]`, `[OTHER]`, … (all `[UNKNOWN]`
  would mean the API ignored the field); no "Shareholder Alert" or repeated
  headline in an edgar or fda digest; a snapshot's `sources_failing` lists
  the dead feeds (it read `[]` at cycle 1, before 2 failures).
- **`v0.8.1` on edgar, fda:** `alert_source_fetch_failures_total` grows
  ~5 (edgar) and ~4 (fda) per 15 minutes, not per 45s cycle.

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
