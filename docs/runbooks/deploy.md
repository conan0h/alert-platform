# Runbook: deploy a change

**When:** any change to a service spec or service code.
**Time:** ~3 minutes per service, most of it the post-deploy gate.

## One-time host preparation

Only needed before the first deploy of a new host.

    # service account — never root, no login shell
    sudo useradd --system --shell /usr/sbin/nologin --home /opt/alert-platform svc-alerts
    sudo install -d -o svc-alerts -g svc-alerts -m 0755 /opt/alert-platform
    sudo install -d -o svc-alerts -g svc-alerts -m 0750 /var/lib/alert-platform
    sudo install -d -m 0750 /etc/alert-platform
    sudo install -d -m 0750 /var/log/alert-platform

    # deploy account needs exactly these, and nothing else
    # /etc/sudoers.d/alert-platform:
    #   svc-deploy ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, \
    #     /usr/bin/chown, /usr/bin/chmod, /bin/rm, /usr/bin/git

The instance role needs `ssm:GetParameter` on `/alert-platform/prod/*`.
Secrets are resolved on the host, by the host — they never transit your
terminal.

## The deploy

    make validate                              # 1. same gate the engine runs
    ./bin/alertctl plan -out plan.json         # 2. read this properly
    ./bin/alertctl apply -plan plan.json       # 3. type 'yes' when prompted

Read the plan before approving. Specifically check:

- **`source.ref`** — is it the tag you meant?
- **`systemd unit`** changing when you did not touch runtime settings. That
  means someone edited the unit on the host, and the deploy is about to
  overwrite their change. Find out what it was first.
- **`secrets`** — a name you do not recognise fails at deploy time, not
  after.

`apply` re-plans before doing anything and refuses if the fingerprint has
moved. A plan approved yesterday against different specs will not run.

## What you should see

    [1/2] clinical-trials: update v0.1.0 -> v0.1.1
      - prepare directories
      - fetch code at pinned tag
      - build virtualenv
      - write environment (2 secret(s))
      - write unit alert-clinical-trials.service
      - activate release v0.1.1
      - reload systemd and restart
      - gate: waiting 60s for startup
      - gate: health endpoint reports ok
      ✓ clinical-trials healthy at v0.1.1

Services deploy one at a time, standard tier before critical. If a gate
fails, that service is rolled back automatically and the run stops — later
services are never touched.

## Verify

    ./bin/alertctl status
    ./bin/alertctl drift        # 0 matches the specs, 3 drifted, 1 could not tell
    journalctl -u alert-clinical-trials -n 50 -o cat | jq

`drift`'s exit codes are a contract, not a convenience — 3 means it ran and
found drift, 1 means it could not find out. Read them that way in anything
scheduled; see [ADR 0002](../adr/0002-exit-codes.md).

### Reading an `observe.yml` run

The observe workflow annotates rather than fails when the host reports a
finding, so:

- **green, no annotation** — nothing to report.
- **green with a `::warning`** — the host answered and found something. For
  `drift` that means the fleet no longer matches its specs. Read the host
  output in the run log; this is a finding to act on, not an outage.
- **red** — the observation itself failed: unreachable host, a wedged
  checkout, a `drift` that could not run. This one is an outage.

One wrinkle: SSM has only "succeeded" and "failed", so a finding invocation is
recorded as `Failed` in the AWS console even though the workflow passed. The
run log prints `status: Failed (host exit 3)` and is the accurate view.

Confirm an alert actually arrives in the Telegram channel. Every service
sends a startup message; if it does not appear, delivery is broken even
though the gate passed — the gate proves the loop runs, not that Telegram
accepts your token.

## If something looks wrong

- **Gate failed, auto-rollback succeeded** — the old version is running and
  the audit log has both entries. Fix forward; nothing is on fire.
- **Gate failed and rollback failed** — see
  [`service-down.md`](service-down.md). This is the one that pages.
- **Deploy succeeded but no alerts** — see
  [`service-down.md`](service-down.md); the "up but silent" section.

## Dry run

To see every command without touching anything:

    ./bin/alertctl apply -dry-run

Useful before the first real deploy to a new host, and for reviewing what a
plan would actually execute.
