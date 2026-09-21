# 2026-09-21 — form4-insider re-sending the same alerts in a loop

**Status:** fix merged, not yet deployed. The loop is still running in
production; shipping needs backlog #20 cleared first (see Actions).
**Impact:** the Telegram channel received the same insider-sale alerts
repeatedly, roughly one message every two seconds, for an unknown duration.
No data was lost and no service went down.

## What was observed

`observe.yml verb=logs` on 2026-09-21 returned, from `form4-insider` at
`v0.1.0`:

```
20:39:05  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,119,426
20:39:07  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,411,252
20:39:09  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,828,658
...
20:39:23  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,567,293
20:39:23  ERROR filing failed
          sqlite3.OperationalError: database is locked
            mark_alerted(conn, accession)     main.py:222
            process_filing(...)               main.py:266
20:39:25  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,119,426   <-- repeat of 20:39:05
20:39:27  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,411,252   <-- repeat
20:39:29  Alert sent: DELL S [SL SPV-2, L.P.] large trade $1,828,658   <-- repeat
```

The identical sequence of nine alerts repeats after each `database is locked`
error, on a cycle of about twenty seconds.

## Cause

`process_filing` sends every qualifying alert for a filing and marks the filing
afterwards:

```python
for tx in parsed["transactions"]:
    ...
    if send_telegram(msg):
        sent += 1
        log.info("Alert sent: ...")

# Mark the filing as alerted regardless of whether any tx fired
mark_alerted(conn, accession)
```

`mark_alerted` raises. The filing is therefore never recorded as handled, and
`is_already_alerted` returns false on the next poll, so every alert for that
filing is sent again. The failure is persistent, so the loop is too.

The ordering is the defect. The lock error is only the trigger: any exception
between the first `send_telegram` and `mark_alerted` produces the same result.

## Why the database was locked

Not yet established, and deliberately not guessed at. `v0.1.0` already opens the
connection with `timeout=30.0` and `journal_mode=WAL`, so this is not a missing
busy timeout — a writer held the lock for more than thirty seconds.
`form4_scorer.py` and `form4_backfill.py` open the same database and are the
obvious candidates. Determining which requires evidence from the host.

One consequence of that timeout is worth recording, because it was found by
writing the regression test rather than by reading the logs: every locked write
blocks the poll loop for the full thirty seconds before raising. While the lock
is held the service is not merely duplicating alerts, it is also stalled, which
is why the repeats in the log are spaced as widely as they are.

## Fix

Claim the filing before sending, not after: insert the accession, then send. If
the claim fails, send nothing and record the refusal as an error and a metric.

This trades a possible missed alert for the elimination of duplicate storms.
That is the correct trade for an alert feed. A missed alert costs one signal; a
channel that emits the same message thirty times trains its reader to ignore it,
which costs every future signal.

Fixing the lock holder is separate work and does not block this.

## Contributing factors

- **Nobody had read the alert output.** Every observation until this point was
  of health and deploy state. `status` and `/healthz` both reported the service
  as healthy throughout, which was accurate: the process was running and
  polling. Its output was useless.
- **No alert content is persisted** (backlog #27), so the duplication was
  invisible except in journald, and only to someone reading it.
- **The host runs code that is not in git.** The traceback puts
  `mark_alerted`'s `conn.execute` at `main.py:222`; the repository has it at 218.
  Backlog #5.

## Actions

| | Action | Backlog |
|---|---|---|
| 1 | Mark before send, with a regression test | #25 |
| 2 | Metric and log line for a refused send | #25 |
| 3 | Identify the lock holder from host evidence | #25 |
| 4 | Persist alert content so duplication is measurable | #27 |
| 5 | Recover the host-side edit before changing that file | #5 |
| 6 | Read alert output every run, not just health | CLAUDE.md §5.3 |

Shipping the fix requires backlog #20 first: §2 forbids a deploy while a
previous deploy's failure is uninvestigated, and the 2026-08-20 `v0.1.2` failure
is uninvestigated.
