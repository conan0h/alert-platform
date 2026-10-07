# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

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

## 2026-10-07 — clinical-trials records the lead sponsor's class
Production, 08:18–08:19Z (04:18 ET): all four `v0.7.0`, active, `drift`
exit 0 (host alertctl `2f815ca`). No deploy: `v0.8.0` still untagged (#88).
Alerts, 24h digests read 07:53–08:21Z (Tuesday session; 10-06 US session):
- `form4-insider` ≥25 sent (digest caps at 25; `alerts_sent` 38 → 63 since
  10-06). MDLN: 5 alerts at 01:49Z, all GIC selling (~$722M), one filing —
  the pattern `v0.8.0` collapses. PSUS 3, SNPS 2, AVR 2, SAH 2. One ticker
  reads `NONE` (GoldenTree, $42M P) and one `AXIA3` (a B3 symbol).
- `clinical-trials` 30, all 10-06T12:11–12:13Z (one registry refresh): 19
  RESULTS_POSTED, 6 TERMINATED, 4 WITHDRAWN, 1 SUSPENDED. Many read as
  academic or NCI cooperative-group studies; some are industry (SAR30250,
  TAK-101, iloperidone, anamorelin ×2, PCS6422, aticaprant, NVG-2089).
  RESULTS_POSTED says "BEFORE press release" with no check of how old the
  trial is.
- `edgar-mna` 5: STI ×2 (same headline twice), Aurora/Curaleaf, Swarmer
  DEFM14A, Locafy/Map Labs. `fda-catalysts` 0.
Shipped: #90 (`leadSponsor.class` requested; in title `SIGNAL [CLASS]: …`,
payload, message; funnel stage `signals_industry`). Handoff #88 retargeted
to `a1cf15e` so `v0.8.0` carries #87 and #90.
Next: when `v0.8.0` exists, roll `form4-insider` and `clinical-trials`.
Read the next clinical digest's `[CLASS]` tags and `signals_industry`.
