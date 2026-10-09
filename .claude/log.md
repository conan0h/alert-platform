# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

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
