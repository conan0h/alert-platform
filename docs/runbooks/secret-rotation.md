# Runbook: rotate a secret

**When:** a credential is compromised, expiring, or being replaced. Also
required once, immediately: the pre-migration `.env` files committed to this
repository exposed the Telegram bot token and every chat ID.

## Principle

Specs reference secrets by name; values live only in the store and in a 0640
file on the host. Rotation therefore touches the store and re-applies — it
never touches the repo. If a rotation requires a code or spec change, the
secret was hardcoded somewhere it should not have been.

## Rotate

1. **Mint the new credential.** For Telegram: `/revoke` then `/token` to
   @BotFather. The old token stops working immediately, so expect alerts to
   fail between here and step 3.

2. **Update the store.**

        aws ssm put-parameter \
          --name /alert-platform/prod/tg_bot_token \
          --type SecureString --overwrite \
          --value '<new token>'

   Use `--value` from a file or a prompt rather than shell history if you can:
   `--value "$(cat token.txt)"`, then delete the file.

3. **Re-apply every service that references it.** `tg_bot_token` is shared by
   all four; a chat-specific secret affects one.

        ./bin/alertctl plan -out plan.json
        ./bin/alertctl apply -plan plan.json

   The plan will show an `environment` change for each affected service. That
   is the env-file hash moving, which is exactly what you want to see.

4. **Verify delivery.** Each service posts a startup message. If it does not
   arrive, the new token is wrong or the bot is not in the channel — the
   post-deploy gate cannot detect this, because the poll loop is healthy
   either way.

        curl -s localhost:9101/metrics | grep delivery_failures

## Rotating the EDGAR user agent

`edgar_user_agent` is not a credential but is handled as one: SEC blocks
unidentified clients, and the value contains a contact email. Same procedure.
It must include a real contact address — SEC will block the host for
malformed user agents, which presents as every EDGAR-consuming service going
silent at once.

## After a leak

Rotation is necessary but not sufficient. Also:

- **Assume the old value is public.** Removing a file from the working tree
  does not remove it from git history. If the repository has ever been
  shared, cloned, or archived, treat the credential as fully exposed.
- **Check for misuse.** For a Telegram bot, look for messages you did not
  send in the channel.
- **Confirm the guard is active.** CI rejects credential-shaped values and
  tracked `.env` / `*.db` / `*.log` files. Verify it fails on a test commit
  rather than assuming.

## What must never happen

- A secret value in a spec, a plan file, or the audit log. The audit log
  records secret *names* deliberately — knowing a deploy rotated
  `tg_bot_token` is useful, knowing its value is not.
- A secret passed as a command-line argument on the host: it would be visible
  in `ps` to every user on the box. The apply engine writes the env file via
  stdin for this reason.
