# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

## 2026-10-02 — plan reason for ref rolls; form4 fetch loss named
Production, 08:18–08:21Z (04:18 ET): all four `v0.6.0`, active, `drift` exit 0
(host alertctl `89fe764`). `history` ends at the 2026-09-28 applies. No deploy:
`v0.7.0` still untagged (#70, no comment).
Alerts, snapshots 07:51–08:16Z (03:51–04:16 ET), ~343,600–345,000s uptime:
- `form4-insider`: 262 archived, all `large_trade` (46 in the last ~24h).
  new 6379, fetched 3563, parsed 1828, transactions 2656, code_not_actionable
  1371, below_floor 629, no_insider_history 394, planned_sale 0; 115 poll errors.
- `clinical-trials`: 109 archived. first_sight 760, changed 49, signals 109.
- `edgar-mna`: 47 archived; 398 of 60,286 fetches failed.
- `fda-catalysts`: 15 archived; 7,776 of 72,742 failed, 1 source dead.
- `sends_refused` 0, `delivery_failures` 0 on all four.
Shipped: #76 (plan says `ALERT_DEPLOYED_REF follows source.ref; no config
changed` on a ref-only roll; closes #43; reaches the host at the next `plan`).
#77 (`form4-insider` counts `index_unavailable`, `no_form4_xml`,
`xml_unavailable` and logs `filing not fetched`). Handoff #70 retargeted.
Found: #46: ~44% of new Form 4 filings are never fetched and never retried.
Next: when #70 is done, roll all four to `v0.7.0` (#45), read the #46 split.

## 2026-10-03 — form4 price source switched to v8 and probed on the host
Production, 08:17–08:20Z (04:17 ET): all four `v0.6.0`, active, `drift` exit 0
(host alertctl `89fe764`). `history` ends at the 2026-09-28 applies. No deploy:
`v0.7.0` still untagged (#70, no comment since 2026-09-29).
Alerts, snapshots 07:51–08:00Z, ~429,800–430,400s uptime. `logs` 07:48–08:05Z:
76 INFO lines, no WARNING or ERROR.
- `form4-insider`: 324 archived, all `large_trade` (62 in the last ~24h).
  new 9076, fetched 5703 (37% lost), parsed 3313, transactions 4430,
  code_not_actionable 2675, below_floor 855, no_insider_history 576,
  planned_sale 0; 115 poll errors.
- `clinical-trials`: 130 archived. first_sight 960, changed 62, signals 130.
- `edgar-mna`: 52 archived; 401 of 75,591 fetches failed.
- `fda-catalysts`: 16 archived; 9,682 of 90,748 failed, 1 source dead.
- `sends_refused` 0, `delivery_failures` 0 on all four.
Shipped: #79 (`form4_common` fetches closes from Yahoo's v8 chart API; the
service probes 30 days of SPY at start: `price source probe` and
`alert_price_source_closes`). Handoff #70 retargeted.
Found: #39: the scorer's v7 CSV endpoint is expected to need a cookie and crumb,
so scheduling the scorer could score nothing. Unverified: this session's egress
cannot reach Yahoo, and the probe reaches the host only with `v0.7.0`.
Next: when #70 is done, roll all four to `v0.7.0` (#45), and read the probe.

## 2026-10-04 — form4 feed filtered to Form 4; parse loss counted
Production, 08:17–08:20Z (04:17 ET Sunday): all four `v0.6.0`, active, `drift`
exit 0 (host alertctl `89fe764`). No deploy: `v0.7.0` still untagged (#70, no
comment since 2026-09-29).
Alerts, snapshots 07:51–08:04Z, ~516,300–516,900s uptime. `logs` 07:50–08:06Z:
INFO only. Every counter equals the 2026-10-03 reading: no filings or alerts
on a Saturday. Archived 324 / 130 / 52 / 16 (form4 / clinical / edgar / fda).
Found: #46: `fetched` 5703 → `parsed` 3313; 2390 fetched filings failed to
parse with no counter. The live feed's `type=4` is EDGAR's form-type prefix
match, so it can carry 424B2, 497 and similar; the backfill filters to `4` and
`4/A`, the live path did not. Hypothesis, unverified: sec.gov is unreachable
from this session.
Shipped: #81 (feed entries filtered to `4` / `4/A`, counted as `not_form4`
and logged once per type; parse failures counted as `unparsed` and logged
with the root element). Handoff #70 retargeted to `5433ad3`.
Next: when #70 is done, roll all four to `v0.7.0` (#45). Read `not_form4`,
`unparsed` and the fetch-failure split together.
