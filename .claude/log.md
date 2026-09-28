# Run log

The last three runs, newest last, each under ~25 lines. Older entries move
verbatim to [`log-archive.md`](log-archive.md).

    ## YYYY-MM-DD — <milestone>
    Production: refs, health, drift; any plan/apply with id, duration, outcome.
    Alerts: what the bots emitted, and when the window was read (UTC and ET).
    Shipped: PRs, and what they changed.
    Found: new facts, with backlog numbers.
    Next: the first thing the next run should do.

## 2026-09-28 — v0.6.0 on all four services
Production: plan `2880151f4eb5` (4 UPDATEs), apply run 14 `Applied 4 change(s)`,
305.9s, no rollback. All four `v0.6.0`, active, `healthz=ok`, `drift` exit 0.
Alerts, 08:37–08:47Z (04:37 ET, pre-market): none sent.
- `form4-insider`: `entries=100 new=0`, every later stage 0. Leaderboard
  13,782 insiders, 0 scored.
- `clinical-trials`: `0 of 0`, the Monday window (weekend only).
- `edgar-mna`: 1 source failure in 182 fetches. `fda-catalysts`: 14/15,
  FiercePharma dead.
Shipped: #65 (roll to `v0.6.0`), #66 (records), then a docs pass that cut
the docs to current state and open work.
Found: `alert_alerts_sent_total` counts the startup message (#37);
`alert_archive_records_total` is absent (#41); the plan's env-change reason is
false on every ref roll (#43).
Next: #42, the post-close `form4-insider` read.
