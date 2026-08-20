# Runbook: roll back a service

**When:** a deploy went out, passed its gate, and is misbehaving in a way the
gate could not see — bad alerts, wrong thresholds, silent detection failures.

Automatic rollback already covers deploys that fail their gate. This runbook
is for the ones that pass and are still wrong.

## The fast path

    ./bin/alertctl rollback -service form4-insider

This finds the last successful ref in the audit log and **rewrites
`spec.source.ref` in the spec file**. It does not deploy. Then:

    ./bin/alertctl plan -service form4-insider -out rollback.json
    ./bin/alertctl apply -plan rollback.json

## Why it edits the spec instead of just deploying

A rollback that deployed behind the repo's back would leave the repo claiming
a version that is not running. The next `apply` — possibly by someone else,
possibly automated — would roll forward again and reintroduce the outage,
with nothing in the diff to explain why.

Editing desired state keeps one source of truth. It also means the rollback
goes through the same gates, the same audit trail, and the same code path as
any other deploy, which is what you want from the thing that runs when
everything else has already gone wrong.

## Choosing a target explicitly

    ./bin/alertctl history -service form4-insider
    ./bin/alertctl rollback -service form4-insider -to v0.1.3

`history` prints the audit log: who deployed what, when, and whether it
worked. The last entry with `outcome=success` is what the fast path picks.

## Commit the change

The spec edit is a real change to desired state. Commit and push it, or the
next person to run `plan` will see drift they did not cause:

    git add fleet/services/form4-insider.yaml
    git commit -m "rollback form4-insider to v0.1.3: bad threshold in v0.1.4"

Put the *reason* in the message. Six weeks from now the useful question is
not what you rolled back to but why.

## When rollback will not help

- **Corrupted state.** Rolling back code does not roll back the database. If
  a bad version wrote bad rows, restore state from backup separately.
- **The upstream changed.** If SEC or FDA altered a feed format, the old
  version is equally broken. Fix forward.
- **The service never worked at this ref.** `rollback` skips the current ref
  but does not verify that the target was ever healthy — only that a deploy
  of it succeeded. Check `history` and the dashboards before trusting it.
