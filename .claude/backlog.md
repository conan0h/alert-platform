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

48. **News feeds are going dark.** `todo` (waits on the next release)
    `edgar-mna` has 5 sources presumed dead (1 on 10-09 08:26Z) and
    `fda-catalysts` 4. All of them fail every 45s wire cycle. The likely
    cause is GlobeNewswire since ~13:20Z 10-09, but that is inferred from
    the counters and not confirmed. `main` (#96) adds `sources_failing` to
    each snapshot. Next: after the deploy, read it. A 404 means fix the URL;
    a 403 means a block, so find another feed for that wire.

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

47. **`clinical-trials` alerts on trials nobody can trade.** `todo` (waits on `v0.8.0`)
    10-06: 30 alerts in one 12:11–12:13Z burst; many academic. `main` (#90)
    records `leadSponsor.class` in each title (`TERMINATED [OTHER]: …`) and
    counts `signals_industry` beside `signals`. Next: after a week of those
    counters, decide whether to drop non-`INDUSTRY` signals behind a
    `spec.polling` key. Also: `RESULTS_POSTED` claims "BEFORE press
    release" for any trial, however old; compare `resultsFirstPostDate`
    with the completion date before calling it fresh.

39. **`form4-insider` can only alert on trades over $1M.** `todo` (waits on `v0.8.0`)
    The leaderboard is unscored in production (13,782 insiders, 0 scored;
    113 trades stopped at `no_insider_history` 10-05 to 10-08). `main` (#92)
    scores it in-process. Next: after the deploy, read
    `alert_scorer_tickers_due` (first-pass size) and `scoring step` lines; if
    `no_prices` dominates, the price source is refusing. Once the leaderboard
    exists, read which `top_tier` trades it lets through.

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

- **Next release on `edgar-mna`, `fda-catalysts`:** each snapshot carries
  `sources_failing` (a list; `[]` when healthy). `edgar-mna` gains
  `alert_funnel_duplicate_title_total`, and no digest shows one title twice.

- **`v0.8.0` on `form4-insider`:** a multi-line filing arrives as one alert
  titled `(N tx)`; digest `by_reason` reads `large_trade`, not dollar amounts.
- **`v0.8.0` on `edgar-mna`, `fda-catalysts`:** `alert_funnel_litigation_notice_total`
  appears and grows; no "Shareholder Alert" / "Class Action" title in a digest.
- **`v0.8.0` on `clinical-trials`:** titles carry `[INDUSTRY]`, `[OTHER]`,
  …, not all `[UNKNOWN]` (which would mean the API ignored the field);
  `alert_funnel_signals_industry_total` appears in the snapshot.

- **`v0.8.0` on `form4-insider` (scorer):** `scoring step` lines appear,
  `alert_scorer_tickers_due` falls across snapshots, and when it reaches 0
  `alert_leaderboard_scored` is non-zero.

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
