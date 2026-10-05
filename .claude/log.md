# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

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

## 2026-10-05 — v0.7.0 deployed; audit records host mutation
Production, 08:17–08:19Z: all four `v0.6.0`, active, `drift` 0. Archived
324 / 130 / 53 / 16 (form4 / clinical / edgar / fda), edgar +1 since 10-04.
Deploy: Conan tagged `v0.7.0` (`5433ad3`, ancestor of main). #85 rolled all
four. Plan `c61cd2ef7180` (run 37283974493): 4 UPDATE `v0.6.0 -> v0.7.0`,
`no config changed`, nothing created or removed. Apply run 37284115775,
08:31–08:36Z (5m09s): all four passed the health gate. After: `status` all
`v0.7.0` active, `drift` exit 0, host alertctl `2f815ca` (carries #83).
Alerts, first `v0.7.0` digests (24h window, Sunday): edgar 1 —
`10-04T21:08Z TAKEOVER_RUMOR Schneider Electric is said to near deal to buy
PTC for more than $20B`; form4, clinical, fda 0.
First-cycle funnels: edgar entries 310, unclassified 294, disclosure_noise 11,
matched 5; fda entries 279, unclassified 258, matched 21.
`alert_archive_records_total` seeded at start: 497 / 216 / 85 / 32 (all
rows ever, across releases); `alerts_sent` 0 at start.
form4 `price source probe`: SPY `200`, 19 closes, last 2026-10-02.
Shipped: #83 (`detail.mutated`; closes #30), #85 (roll to `v0.7.0`).
Next: read form4's #46 split and `planned_sale` after a US session (from
~21:00Z); check PTC's move after 10-04 21:08Z; design scorer scheduling (#39).
