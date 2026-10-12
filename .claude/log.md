# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

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

## 2026-10-11 — v0.8.0 deployed; dead feeds named; scorer runs
Production before, 08:18–08:20Z (04:18 ET Sunday): all four `v0.7.0`,
active, `drift` exit 0 (host alertctl `2f815ca`).
Deploy: Conan tagged `v0.8.0` (`f29a2fb`, ancestor of main); #88 closed.
#99 rolled all four. Plan `cc546ad55bb4` (run 38124497060): 4 UPDATE
`v0.7.0 -> v0.8.0`, `no config changed`, nothing created or removed. Apply
run 38124558417, 08:22–08:27Z (5m16s): all four passed the health gate.
After: `status` all `v0.8.0` active, `drift` exit 0, alertctl `f0208ff`.
Alerts, last `v0.7.0` digests read 07:53–08:03Z (24h, a Saturday): edgar 1
(`10-10T19:16Z` Nvidia/Reflection AI rumour; private target), fda 0,
clinical 0, form4 0. form4 `planned_sale` 1030 since 10-05, so #69 parses.
clinical `delivery_failures` 1 since 10-05.
First `v0.8.0` readings, 08:22–08:32Z:
- edgar: GlobeNewswire MA, Financings, Business, PublicCos 403;
  Yahoo-Finance-Headlines (`/news/rssindex`) 404. fda: GlobeNewswire
  Pharma, Biotech, Health 403; FiercePharma 403. edgar `litigation_notice` 5
  in its first cycle.
- form4 scorer, first step 08:26:44Z: due 142, scored 134, no_prices 8;
  leaderboard scored 0 → 208, eligible 20, alpha cutoff 11.5%. Price probe
  SPY 200, 21 closes. Every top-15 row prints `a180=+0.0%`, unexplained (#39).
Shipped: #99 (roll), #100 (presumed-dead source retried every 900s, not
every 45s cycle; edgar drops Yahoo-Finance-Headlines).
Next: after Conan tags `v0.8.1`, roll edgar and fda (or all four: alertlib
changed) and check `alert_source_fetch_failures_total` grows ~5 per 15 min.
Read a weekday digest: form4 `(N tx)` titles and first `top_tier` trades.

## 2026-10-12 — rumours no longer sent as signed deals
Production, 08:18–08:20Z (04:18 ET Monday): all four `v0.8.0`, active,
`drift` exit 0 (host alertctl `f0208ff`). No deploy: `v0.8.1` untagged (#102).
Source health: edgar 5 dead (4 GlobeNewswire 403, Yahoo 404), fda 4 dead
(3 GlobeNewswire, FiercePharma 403); each failure count equals the cycle
count, so none answered once since the deploy. Failures still grow per cycle.
Alerts, 24h digests read 07:49–08:20Z (covering Sunday):
- `edgar-mna` 1: `10-11T23:26Z` "Amazon in talks to acquire AI startup
  Decart for $7B: WSJ" sent as SIGNED_DEAL (private target).
- `fda-catalysts` 0, `form4-insider` 0. `clinical-trials` `Streamed 0 of 0`
  (Monday pre-market); its snapshot fell outside both windows.
- form4 leaderboard: scored 212, eligible 20; scorer due 0, errors 0.
Shipped: #103. A category can now be vetoed (`unless`) and redirected
(`otherwise`). Hedged deals go to TAKEOVER_RUMOR, and an ended strategic
review is dropped. The edgar proper-name pattern is case-sensitive, and fda
PRIORITY_REVIEW no longer matches "voucher". #102 retargeted to `544f8f6`
(CI run 38223341560), so `v0.8.1` carries #100 and #103.
Next: when `v0.8.1` exists, roll all four (Handoff #102 steps). Read
Monday's weekday digest: form4 `(N tx)` titles, first `top_tier` trades.
