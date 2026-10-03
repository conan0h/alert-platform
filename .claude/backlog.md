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

45. **Roll `v0.7.0` and read the digests.** `needs-conan` (tag, #70), then ours.
    `main` carries the `alert digest` line (#68), the 10b5-1 fix (#69), the
    `edgar-mna` / `fda-catalysts` funnels (#72), honest sent / archive
    counters (#74) and form4 fetch-failure stages (#77). Once tagged: roll all
    four (`alertlib` is shared), then read `logs` `since="10 minutes ago"`
    and `"30 minutes ago"` for one digest per service. For each alert, record
    ticker and time, and whether the price moved after it; `clinical-trials`
    (109 in four days) is the least understood.
    Check #74: `alerts_sent` equals archived, and `alert_archive_records_total`
    is non-zero at start. Confirm `alert_funnel_planned_sale_total` goes
    non-zero after a US session; if it stays 0, #69's element name is wrong.
    Record `unclassified : matched` for the two news services, the #46
    split of fetch failures, and the #39 price probe.

39. **`form4-insider` can only alert on trades over $1M.** `needs-conan` (tag, #70)
    The leaderboard behind its main filter is populated but unscored: 13,782
    insiders, 32,592 transactions, 0 scored, because `form4_scorer.py` has never
    run. 576 trades of $100k–$1M stopped at `no_insider_history` in the ~5 days
    to 2026-10-03. The scorer needs daily closes. From `v0.7.0` it reads them
    from Yahoo's v8 chart API (#79), and the service reports
    `price source probe` and `alert_price_source_closes` at start. Next: read
    the probe. If it is non-zero, decide between a timer unit for the scorer
    and an in-process daily scoring step. If it is 0, find another price
    source first.

46. **`form4-insider` never fetches ~40% of new filings.** `needs-conan` (tag, #70)
    Snapshot 2026-10-03: `new` 9076, `fetched` 5703. A failed fetch is marked
    handled and never retried, so those filings never reach the filter. From
    `v0.7.0`, `index_unavailable`, `no_form4_xml` and `xml_unavailable` split
    the loss, and each failure logs `filing not fetched` with its HTTP status
    or index listing. Next: read the split, then retry transient failures
    while the filing stays in the feed (~1.5 h), or fix the XML selection rule.

## Measurement

27. **Nothing can read the alert archive.** `todo` for (d); (c) blocked on #24
    Alerts are recorded per service in SQLite (ADR 0005). (d) a read-only
    console panel that unions the four databases via `AlertArchive.recent()` —
    unblocked. (c) an `alerts` read verb — needs the wrapper change in #24.

## Deploy safety

30. **The audit log can't tell "refused before acting" from "failed mid-way".**
    `todo`
    Both are `failed`. Fix: a `mutated: bool` detail set once the first mutating
    step runs; show it in `history` and the console; test both paths.

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
