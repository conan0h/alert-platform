# ADR 0005 — Alert output is persisted per service, in its own database

**Status:** accepted, 2026-09-22

## Context

CLAUDE.md §1 ranks signal quality first and measurability second, because the
second is what makes the first improvable. Neither was possible. An alert
existed as a Telegram message and a journald line, and nothing else:

- journald rotates, and the `logs` read verb returns roughly the first 24 KB of
  a one-hour window, so most of what the fleet emitted was already unreadable
  by the next morning;
- a journald line holds the rendered message, not the fields that produced it,
  so "which filter fired", "which source", "what premium" cannot be grouped or
  counted without re-parsing prose;
- there was therefore no way to ask whether an alert preceded a market move,
  which is the only question that says whether this project works.

The website in CLAUDE.md §1 has the same prerequisite: four feeds to present,
and nothing to present them from.

## Decision

Every alert is written to an append-only SQLite table before it is sent and
settled with its delivery outcome afterwards. The write path is
`alertlib.AlertArchive`, reached through `Service.send_alert`, which all four
services now call in place of `telegram.send`.

**One database per service, in that service's own state directory
(`<state_dir>/alerts.db`), separate from its dedup database.**

The alternative — one shared `alerts.db` for the fleet — reads better and is
wrong here. Each service already owns a state directory the apply engine
injects; a shared file would need a path outside all four. More importantly it
would put four writers on one file, and the last production incident this
project had was a SQLite lock storm: on 2026-09-21 `form4-insider` re-sent the
same alerts for hours because something held the write lock on *its* database
past a 30-second timeout. Adding contention to measure the effects of
contention is not a trade worth making. The same reasoning keeps the archive
out of the service's existing dedup file.

A reader unions four files. That cost falls on the `alerts` verb and the
website, both of which read rarely; the cost of the alternative would fall on
the poll loop, which writes constantly.

**A write failure is logged and counted, never raised.** `record` returns
`None` and the alert goes out regardless. This is the opposite of the dedup
discipline in `form4_insider.process_filing`, which refuses to send when it
cannot record, and the asymmetry is the point: a dedup row that does not
persist causes duplicate alerts, which is harm to the reader; an archive row
that does not persist causes a missing measurement, which is only loss.
`alert_archive_write_failures_total` makes the loss visible.

**The deployed ref is stored on every row**, so alert output is attributable to
a release and "did v0.3.0 change what this feed emits" is answerable from the
table rather than from memory.

## Consequences

- Alert output is queryable, groupable by source, ticker, reason and ref, and
  survives journald rotation. That is the prerequisite for the `alerts` read
  verb (backlog #27c), the console panel (#27d) and the website.
- `state.backup` stops being a purely hypothetical gap: there is now data under
  `/var/lib/alert-platform/` whose loss would be irreversible, which raises
  backlog #8 above where it sat.
- Nothing prunes. At the fleet's observed volume a row is a few hundred bytes
  and alerts are tens per day, so the table is small for years; retention
  becomes a decision made against real numbers rather than a guess made now.
- The archive records what happened, not what should have: two rows with the
  same dedup key mean the service sent twice. That is deliberate — it is
  exactly the condition the 2026-09-21 incident made invisible.
- The four services no longer call `telegram.send` for alerts. They still call
  it for the startup banner and the crash notice, which are operational
  messages rather than alerts and are not archived.
