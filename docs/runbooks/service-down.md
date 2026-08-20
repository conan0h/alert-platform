# Runbook: a service is down or silent

**Pages:** `AlertServiceDown`, `AlertServiceHeartbeatStale`,
`AlertServiceNotPolling`, `AlertDeliveryFailing`.

Work top to bottom. Each step narrows the failure to one layer.

## 1. What does the platform think is true?

    ./bin/alertctl status
    ./bin/alertctl history -n 10

For a whole-fleet view — status, pending changes and recent deploys on one
page — `./bin/alertctl serve` and open http://127.0.0.1:8600. It is read-only,
so it is safe to leave open during an incident.

If the last audit entry is a deploy from minutes ago, the deploy is your
prime suspect — go to [`rollback.md`](rollback.md).

## 2. Is the process running?

    systemctl status alert-<service>
    journalctl -u alert-<service> -n 100 -o cat | jq

Structured logs mean you can filter instead of scroll:

    journalctl -u alert-<service> --since -1h -o cat | jq 'select(.level=="ERROR")'

**Crash looping** (`activating (auto-restart)`, or `failed` with
`start-limit-hit`): read the first error, not the last. The restart loop
buries the original exception under repeats.

Common causes, in the order they actually occur:

| Symptom in the log | Cause | Fix |
|---|---|---|
| `ConfigError: ALERT_... is not set` | env file missing or stale | re-apply |
| `ConfigError: secret ... was not injected` | secret absent from SSM | add it, re-apply |
| `sqlite3.OperationalError: unable to open` | state dir permissions | `chown -R svc-alerts /var/lib/alert-platform/<service>` |
| `ModuleNotFoundError` | venv not built for this release | re-apply |

## 3. Is the loop actually advancing?

The distinction that matters: the process can be up while the poll loop is
wedged. That is what the heartbeat exists to catch.

    curl -s localhost:<port>/healthz | jq

`status: stale` with a large `heartbeat_age_sec` means the loop is stuck —
almost always an upstream request without a timeout, or a hung DNS lookup.
Restart to clear it, then find out which upstream:

    sudo systemctl restart alert-<service>
    curl -s localhost:<port>/metrics | grep alert_poll_errors_total

## 4. Up, healthy, but no alerts arriving

Check throughput before assuming a bug:

    curl -s localhost:<port>/metrics | grep -E 'items_seen|alerts_sent|delivery_failures'

- **`items_seen` climbing, `alerts_sent` flat** — the service is working and
  nothing met the alert criteria. Verify against the thresholds in the spec
  before touching anything. This is the most common false alarm.
- **`delivery_failures` climbing** — detection works, Telegram does not.
  Usually a rotated or revoked bot token, or the bot removed from the
  channel. See [`secret-rotation.md`](secret-rotation.md).
- **`items_seen` flat across every service** — not a service problem. Check
  the host's outbound network and DNS.
- **`items_seen` flat for one service** — the upstream feed went quiet or
  changed. Fetch the feed by hand and compare against `source_url`.

## 5. Did someone change the host by hand?

    ./bin/alertctl drift

A `systemd unit` diff with no corresponding commit means the unit was edited
on the box. Find out what was changed and why *before* re-applying — the
apply will overwrite it, and it may have been a legitimate emergency fix that
belongs in the spec.

## 6. Escalation

If the service is down and cannot be brought up:

1. Note the current ref: `./bin/alertctl status`.
2. Roll back to the last known-good: [`rollback.md`](rollback.md).
3. If rollback also fails, the last resort is stopping the unit —
   `sudo systemctl stop alert-<service>` — so it stops crash-looping and
   filling the journal, then debugging with the release tree in place under
   `/opt/alert-platform/<service>/current`.

A stopped service is a missed-alerts problem. A crash-looping service that
nobody has stopped is also a missed-alerts problem, plus a noisy journal and
a misleading dashboard. Stop it.
