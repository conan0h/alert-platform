# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

## 2026-10-01 — honest sent and archive counters
Production, 08:18–08:20Z (04:18 ET): all four `v0.6.0`, active, `drift` exit 0
(host alertctl `89fe764`). No deploy: `v0.7.0` is still untagged (#70).
Alerts, snapshots 07:51–08:06Z, ~257,000s after the 2026-09-28 restart:
- `form4-insider`: 216 archived, all `large_trade` (~72/day). new 4461,
  transactions 1781, code_not_actionable 875, below_floor 401,
  no_insider_history 289, planned_sale 0 (#69 not deployed); 115 poll errors.
- `clinical-trials`: 81 archived. first_sight 550, changed 42, signals 81.
- `edgar-mna`: 38 archived; 397 of 44,849 fetches failed.
- `fda-catalysts`: 10 archived; 5,840 of 54,370 failed, 1 source dead.
- `sends_refused` 0, `delivery_failures` 0 on all four. `alerts_sent` =
  archived + 1 on every service: the startup banner (#37).
Shipped: #74 (`alert_alerts_sent_total` counted in `Service.send_alert`, so
startup and crash messages are excluded; `alert_archive_records_total`
declared at start and seeded from the rows in `alerts.db`). Handoff #70
retargeted to `c3ee78e`.
Next: when #70 is done, roll all four to `v0.7.0` (#45), then read the digests.

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
