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
    for three services, then `"10 minutes ago"` for the fourth: the 24 KB cap
    cuts the last snapshot). For each alert record ticker and time, and
    whether the price moved after it. No price source reaches this session,
    so a move check needs the host to report it (or #27). Open cases: PTC
    after edgar's `10-04T21:08Z` rumour; whether `v0.8.0` collapses
    multi-line Form 4s (LEN 10-06 01:05Z was 8 alerts).

47. **`clinical-trials` alerts on trials nobody can trade.** `todo`
    On 10-06 at least 6 of 14 alerts were academic studies (kidney stones,
    health-systems outreach, TCM granules). The API's
    `leadSponsor.class` (`INDUSTRY`, `NIH`, `OTHER`, …) is not requested.
    Next: request it, put it in the alert and archive payload, and count
    signals by class in the funnel; then decide from a week of counts
    whether to drop non-`INDUSTRY` signals behind a `spec.polling` key.

39. **`form4-insider` can only alert on trades over $1M.** `todo`
    The leaderboard behind its main filter is unscored: 13,782 insiders,
    32,592 transactions, 0 scored, because `form4_scorer.py` has never run.
    66 trades of $100k–$1M stopped at `no_insider_history` in the 24h to
    10-06 08:19Z. The price source works on the host (probe 2026-10-05: SPY
    200, 19 closes). Before scheduling: the scorer re-selects every trade
    under 90 days old (`fwd_ret_90 IS NULL`), and `fetch_price_history`'s
    cache check spans a window 200 days into the future, so it can never
    pass and every such ticker is refetched each run.
    Next: run the scorer daily — a timer unit (spec and deploy-lifecycle
    change, ADR) or an in-process step after the US close (no new unit).
    Prefer in-process unless scoring takes long enough to stall the poll.

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

- **`v0.8.0` on `form4-insider`:** a multi-line filing arrives as one alert
  titled `(N tx)`; digest `by_reason` reads `large_trade`, not dollar amounts.

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
