# Migration: bots → managed services

Phase 1 produced a spec that described a fleet the repo did not actually
contain. The code lived in `bots/`, ran as `ubuntu` out of `/home/ubuntu`,
read credentials from committed `.env` files, and wrote its database next to
its source. This migration makes reality match the spec, because an apply
engine cannot reconcile against paths and tags that do not exist.

## Principle

**Behaviour-preserving.** Signal detection, parsing, scoring and message
formatting were copied unchanged — that code is the product, and rewriting it
during an infrastructure migration would mean debugging two things at once.
Only the seams moved: configuration, logging, delivery, state, and the outer
loop.

Where the spec and the running code disagreed, **the code won and the spec was
corrected**. The alternative — deploying the spec's values — would have
changed alerting behaviour on the same day everything else changed. Every one
of those corrections is listed below, and each is now a one-line diff if you
want the original intent instead.

## What moved

| Before | After |
|---|---|
| `bots/ct_bot/ct_bot.py` | `services/clinical_trials/main.py` |
| `bots/ma_bot/ma_bot.py` | `services/edgar_mna/main.py` |
| `bots/fda_bot/fda-bot.py` | `services/fda_catalysts/main.py` |
| `bots/form4_bot/form4_bot.py` | `services/form4_insider/main.py` |
| `bots/form4_bot/form4_{common,scorer,backfill}.py` | `services/form4_insider/` |

Paths now match `spec.source.path` exactly. The apply engine derives the
entrypoint from the spec, so a mismatch is a failed deploy rather than a
silently wrong one.

## What changed in the services

**Configuration.** Four copies of `os.getenv("TELEGRAM_BOT_TOKEN")` and a
`load_dotenv()` per bot are gone. Every service now calls
`Service.from_env()` and reads the platform contract. Nothing is hardcoded
that the spec declares.

**Logging.** Each bot configured its own logger writing to a file beside its
source — one of which reached 7 MB, unrotated, and was committed. All four
now emit JSON on stdout; systemd routes it to journald. Records carry
`service` and `ref`, so `journalctl -u 'alert-*' -o cat | jq` filters across
the fleet.

**State.** `sqlite3.connect("ct_seen.db")` used a relative path, so the
database landed wherever the process happened to start. A `cd` in a unit file
could silently create an empty database and re-alert on everything. State now
resolves through `ALERT_STATE_DIR` into `/var/lib/alert-platform/<service>/`,
which is also what the backup policy points at. A test in
`services/tests/test_service_bootstrap.py` fails the build if a literal path
comes back.

**Delivery.** Four near-identical `send_telegram` functions became one client
with a token bucket honouring `delivery.rate_limit_per_min` (declared in every
spec since Phase 1, enforced by nothing until now) and retry on HTTP 429.

**Health.** Every service exposes `/healthz` and `/metrics` on its reserved
port. This is what the post-deploy gate and the dashboards consume — the
Phase 1 health contract finally has readers.

**Shutdown.** SIGTERM now finishes the current cycle and closes the database
instead of dying mid-write. Deploys restart units; without this, a deploy
could leave a half-written row that re-alerts on the next start.

## Spec corrections

Each of these is the spec being brought in line with what has actually been
running:

| Service | Field | Was declared | Now (live value) |
|---|---|---|---|
| clinical-trials | `polling.interval_sec` | 600 | **300** |
| edgar-mna | `polling.interval_sec` | 60 | **45** (primary wire loop) |
| fda-catalysts | `polling.interval_sec` | 300 | **45** (primary wire loop) |
| form4-insider | `polling.interval_sec` | 60 | **120** |
| form4-insider | `polling.min_transaction_value_usd` | 250000 | **100000** |

Two services poll three tiers at different cadences. The slower tiers were
invisible in the spec; they now use the `spec.polling` extension point
(`edgar_interval_sec`, `press_interval_sec`, `fda_interval_sec`), so all
cadences are declared rather than buried in code.

Also corrected:

- **fda-catalysts** declared `api_key_secret: openfda_api_key` and an
  `api.fda.gov` source URL. The implementation reads FDA's RSS feeds and never
  calls the openFDA REST API. A secret reference that nothing consumes is a
  deploy-time failure waiting to happen, so it was removed and the source URL
  set to the feed actually polled.
- **fda-catalysts** polls EDGAR 8-Ks but declared no `user_agent_secret`. SEC
  blocks unidentified clients, so it now declares `edgar_user_agent` like the
  other two EDGAR consumers.
- **All four** `source.ref` values reset to `v0.1.0`. The old refs
  (`v1.4.2`, `v1.2.0`, `v1.1.3`, `v2.0.1`) describe a tree layout that no
  longer exists; no such tag could be deployed. `v0.1.0` is the
  post-migration baseline — see "Before the first deploy" below.

### If you want the original values instead

They were plausible intentions, not accidents. Raising the Form 4 floor to
$250k, or slowing `clinical-trials` to 600s, is now exactly the change the
platform is built to make safely:

    # edit fleet/services/form4-insider.yaml: min_transaction_value_usd: 250000
    make validate
    ./bin/alertctl plan -service form4-insider -out plan.json
    ./bin/alertctl apply -plan plan.json

That is a reviewable diff, a gated deploy, and an audit entry — which is the
whole argument for this project.

## Repository hygiene

Removed from the tree and now ignored: `.env` files, `*.db` (3.5 MB of
production state), `*.log` (7 MB), `venv/`, and a stray `.test.swp`. CI fails
the build if any of them come back, or if anything credential-shaped is
committed.

**The Telegram bot token and chat IDs in those `.env` files must be treated as
compromised.** They were committed to git and distributed in an archive.
Rotate them before the first deploy; see
[`runbooks/secret-rotation.md`](runbooks/secret-rotation.md). Note that
removing the files does not remove them from git history — if the repo has
ever been shared, rotation is the only remedy.

## Before the first deploy

The migration is code-complete and tested, but four things need a real host
and real credentials:

1. **Rotate the leaked Telegram token.** Everything else can wait; this
   cannot.
2. **Populate the secret store.** Under `/alert-platform/prod`, as
   `SecureString`: `tg_bot_token`, `tg_chat_mna`, `tg_chat_fda`,
   `tg_chat_trials`, `tg_chat_form4`, `edgar_user_agent`. Names must match
   the specs exactly — `alertctl` fails loudly on a missing one rather than
   deploying a service that cannot deliver.
3. **Cut the baseline tag.** `git tag -a v0.1.0 -m "post-migration baseline"`
   and push it. The pre-flight gate refuses to deploy a ref that does not
   exist.
4. **Prepare the host once.** Create the `svc-alerts` user, grant the
   instance role `ssm:GetParameter` on `/alert-platform/prod/*`, and ensure
   `svc-deploy` can `sudo systemctl` and `sudo install`. See
   [`runbooks/deploy.md`](runbooks/deploy.md).

Then migrate state: copy each existing `*.db` from `/home/ubuntu/<bot>/` into
`/var/lib/alert-platform/<service>/` under the filename the service expects
(`ct_seen.db`, `ma_seen.db`, `fda_seen.db`, `form4.db`), owned by
`svc-alerts`. Skipping this is not fatal — the services reseed — but
`clinical-trials` would spend one cycle snapshotting instead of alerting, and
`form4-insider` would lose its scored leaderboard until the backfill reran.

## What was not done

- **No behaviour changes to detection logic.** Deliberate; see above.
- **`dedup.keys` is still declarative only.** The specs describe dedup keys
  the services do not read — each implements its own fingerprinting. Wiring
  the two together is real work with real risk of double-alerting, and it
  belongs in its own change with its own plan.
- **`state.backup` is declared but nothing performs backups.** The paths are
  now correct and stable, which is the prerequisite. The backup job itself is
  outstanding.
- **The `source_url` field describes one upstream per service**, but three
  services poll several feeds. The feed lists remain in code. Modelling them
  in the spec would need a schema change and therefore a v2 `apiVersion`.
