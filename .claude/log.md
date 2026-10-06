# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

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

## 2026-10-06 — form4 alerts grouped per filing; first weekday digests
Production, 08:19–08:23Z (04:19 ET): all four `v0.7.0`, active, `healthz=ok`,
`drift` exit 0 (host alertctl `2f815ca`). No deploy.
Alerts, 24h digests read 08:11–08:19Z (Monday session):
- `form4-insider` 38 sent, all `large_trade`. 8 were BERKSHIRE HATHAWAY buying
  LEN in the same minute (01:05Z), one per transaction line; TWST 3, BPRE 2,
  CRBG 2 in the same pattern. new 1200 → fetched 1197 → parsed 1197 (#46's
  loss is gone; `not_form4` 15605). planned_sale 336 (#69 element works).
  transactions 1771: code_not_actionable 1211, planned_sale 336, below_floor
  120, no_insider_history 66, large_trade 38.
- `clinical-trials` 13 sent + 1 `failed` (Tapinarof SUSPENDED), all at
  10-05T12:06Z. At least 6 of 14 titles read as academic studies with no tradeable
  sponsor (kidney stones, health-systems outreach, TCM granules, dronabinol
  COPD, statin SAH, omega-7 diet); the digest omits the sponsor.
- `edgar-mna` 8: Athabasca/Cenovus (ATH), ADS/StormTrap, 3 DEFM14A, Aurora,
  and a law-firm "shareholder alert" on the PTC buyout classed SIGNED_DEAL.
  PTC's move after 10-04 21:08Z unchecked: no price source reaches this session.
- `fda-catalysts` 1: Abbott CardioMEMS approval.
Shipped: #87 (`form4-insider` sends one alert per filing and direction, with
summed value, VWAP and date range; archive key `<accession>#<P|S>`, reason is
the decision name). Release `v0.8.0` requested from Conan (#88).
Next: when `v0.8.0` exists, roll `form4-insider` only and check that a
multi-line filing arrives as one `(N tx)` alert.
