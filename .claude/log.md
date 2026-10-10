# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

## 2026-10-08 — form4-insider scores its own leaderboard
Production, 08:18–08:20Z (04:18 ET): all four `v0.7.0`, active, `drift`
exit 0 (host alertctl `2f815ca`). No deploy: `v0.8.0` still untagged (#88).
Alerts, 24h digests read 07:50–08:19Z (Wednesday; 10-07 US session):
- `form4-insider` 15, all `large_trade`: AXIA3 ×4 (one insider, one minute,
  the per-line pattern `v0.8.0` collapses), GAP ×2, KNTK ×2, GRAL ×2; one
  ticker reads `N/A` (Partners Group, $30M S). Snapshot since 10-05:
  no_insider_history 113, large_trade 78, leaderboard scored 0.
- `clinical-trials` 30, all 10-07T13:46–13:48Z: 22 RESULTS_POSTED, 5
  TERMINATED, 2 WITHDRAWN, 1 COMPLETED. Several are long-finished industry
  trials (DS-8201a vs T-DM1, QAW039, palbociclib) — the stale-results case
  in #47.
- `edgar-mna` 5 (Third Coast/Great Plains, Beam/Scout, Mattel/Zuru rumour,
  SRX/CERO, Centerspace DEFM14A). `fda-catalysts` 6, two of them law-firm
  "shareholder alert" releases classed FDA_CRL and CLINICAL_HOLD (#45).
Shipped: #92 (scorer runs in-process, 20s per cycle, buys only, due at
90/180 days, weekly retry for priceless tickers, price cache must reach
both window ends; `NONE`/`N/A` tickers parse as none). #88 retargeted to
`47e3df1` so `v0.8.0` carries #87, #90, #92.
Next: when `v0.8.0` exists, roll `form4-insider` and `clinical-trials`;
then read `alert_scorer_tickers_due` and the `scoring step` lines.

## 2026-10-09 — news services drop plaintiff law-firm releases
Production, 08:18–08:20Z (04:18 ET): all four `v0.7.0`, active, `drift`
exit 0 (host alertctl `2f815ca`). No deploy: `v0.8.0` still untagged (#88).
Alerts, 24h digests read 07:55–08:19Z (Thursday; 10-08 US session):
- `form4-insider` 36, all large trades. One Silver Lake DELL sale filed
  at 20:31Z sent ≥21 alerts (digest caps at 25), one per transaction line:
  the pattern `v0.8.0` collapses. CBL $68M S (Canyon), CRBG $4.1M P
  (Nippon Life). Snapshot: no_insider_history 131, leaderboard scored 0.
- `clinical-trials` 21, all 10-08T12:16–12:17Z: 11 RESULTS_POSTED, 4
  TERMINATED, 4 WITHDRAWN, 2 SUSPENDED (blinatumomab SC, 177Lu girentuximab).
  Mostly industry this time (Viaskin Peanut, LNZ100, ataluren, VCN-01).
- `edgar-mna` 13: Viatris/Pacira (PCRX, 11:46Z), Starbucks–Chipotle
  rumour (CMG, 14:36Z), COPART/ACV tender, two DEFM14A. Sun Life's
  mini-tender caution sent twice at 21:02Z: same story from two feeds.
- `fda-catalysts` 6: Tecentriq approval ×2 (Roche release, FDA notice),
  two recalls, a Fast Track, and AARD "Shareholder Alert … Class Action"
  as CLINICAL_HOLD (#45).
Shipped: #94 (`alertlib/noise.py` drops law-firm releases by title in both
news services; funnel stage `litigation_notice`). #88 retargeted to
`7f52343` so `v0.8.0` carries #87, #90, #92, #94 and rolls all four.
Next: when `v0.8.0` exists, roll all four; read `litigation_notice`, the
form4 `(N tx)` titles, and `alert_scorer_tickers_due`.

## 2026-10-10 — news feeds going dark; snapshots now name them
Production, 08:18–08:21Z (04:18 ET): all four `v0.7.0`, active, `drift`
exit 0 (host alertctl `2f815ca`). No deploy: `v0.8.0` still untagged (#88).
Source health: `edgar-mna` `alert_sources_presumed_dead` 1 (10-09 08:26Z)
→ 5 (10-10 07:57Z); `fda-catalysts` 4 (was FiercePharma only). Failures
grow 5 and 4 per 45s cycle, so every dead feed is on the wire cadence.
Probably GlobeNewswire (4 edgar + 3 fda feeds), dead since ~13:20Z 10-09
by the failure arithmetic. Unconfirmed: no readable window names them (#32).
Alerts, 24h digests read 08:12–08:19Z (Saturday; 10-09 US session):
- `form4-insider` 17, all `large_trade`: BPRE ×4 (one insider, one minute),
  AXIA3 ×4, GGR ×2 ($35M P), CRBG $20.9M P (Nippon Life), BBD $29M P.
- `clinical-trials` 21, all 10-09T12:06–12:07Z; 12 RESULTS_POSTED.
- `edgar-mna` 5: CCC/GTCR-Elliott and Ambarella/Qualcomm rumours, ATD
  signed deal, PPC/JBS special committee, and MTY "End of Strategic
  Review" classed STRATEGIC_REVIEW (a review ending is not a deal signal).
- `fda-catalysts` 3, one an Atossa CVR agreement classed PRIORITY_REVIEW.
Shipped: #96 (snapshot field `sources_failing`: name, failures, dead,
`last_ok`, error). #97 (edgar drops a headline already sent from another
wire within 24h, stage `duplicate_title`). #88 retargeted to carry both.
Next: when the tag exists, roll all four; the first edgar/fda snapshot's
`sources_failing` names the dead feeds and their errors. Fix those first.
