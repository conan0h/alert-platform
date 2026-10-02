# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

## 2026-09-30 — funnels for the news services; clinical-trials alerts
Production, 08:18–08:26Z (04:18 ET): all four `v0.6.0`, active, `healthz=ok`,
`drift` exit 0 (host alertctl `89fe764`). `history` ends at the 2026-09-28
applies. No deploy: `v0.7.0` is still untagged (#70).
Alerts, snapshots 07:57–08:26Z, ~171,000–172,800s after the restart:
- `form4-insider`: 87 archived, all `large_trade`. new 3209, parsed 770,
  transactions 1086, code_not_actionable 665, below_floor 205,
  no_insider_history 129, planned_sale 0; 115 poll errors.
- `clinical-trials`: 55 archived. first_sight 377, first_sight_completed 69,
  changed 26, signals 55. Every per-cycle line read `changed=0`.
- `edgar-mna`: 29 archived; 397 of 29,573 fetches failed. One alert in the
  window: SIGNED_DEAL, "MT Højgaard Danmark acquires Nordisk Funderin…"
  (Danish, no US ticker).
- `fda-catalysts`: 6 archived; 3,918 of 36,112 fetches failed (FiercePharma).
- `sends_refused` 0 and `delivery_failures` 0 on all four.
Shipped: #72 (`edgar-mna` and `fda-catalysts` count what the category filter
drops; `CycleFunnel(cohort=False)`; ADR 0006 amended). Handoff #70 retargeted
to `c0a4919`.
Found: #35's premise was false. `clinical-trials` alerts, and the per-cycle
line at the run hour hid it for a week. Deleted #35; its alerts join #45.
Next: when #70 is done, roll all four to `v0.7.0` (#45), then read the digests.

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
