# Runbook: run a service locally

Services expect the platform environment. Locally you supply it yourself.

## Setup

    make deps
    cp .env.example .env          # edit: state dir, secrets, service name
    mkdir -p .local-state

Use a **test** Telegram bot and a private channel. Running against the
production bot will post real alerts to the real channel from your laptop,
with no deploy record explaining where they came from.

## Run

    set -a && . ./.env && set +a
    PYTHONPATH=services python3 services/clinical_trials/main.py

`ALERT_LOG_FORMAT=text` in `.env.example` gives readable output; production
always runs `json`.

Health endpoints work locally too:

    curl -s localhost:9103/healthz | jq
    curl -s localhost:9103/metrics

## Generating a realistic environment

Rather than hand-editing `.env`, render the real one:

    make build
    ./bin/alertctl render -service form4-insider

Secrets come back as `<resolved-at-apply>` — the command is safe to paste
anywhere. Substitute your test credentials for the `ALERT_SECRET_*` lines.

## Tests

    make test          # everything
    make test-go       # control plane only
    make test-py       # services and the cross-language contract

`make test-py` builds the binary first so the contract tests run rather than
skip. Those are the tests that catch a rename in one language breaking the
other.

## Before you push

    make check

Runs validation, both test suites, and confirms the generated observability
config still matches the specs. Same checks as CI, so a green run here is a
green build.

## Notes

- **Do not commit `.env`.** It is ignored, and CI fails if it appears.
- **State is real.** Deleting `.local-state/` makes the next run reseed and
  suppress alerts for one cycle, which is the intended behaviour, not a bug.
- **Respect upstream rate limits.** EDGAR's fair-use limit applies from your
  laptop too, and the identifying user agent is what keeps the host off the
  block list.
