# infra/terraform

The AWS side of the deploy pipeline, as code.

**Nobody applies this from CI, and the agent never applies it at all.** CI runs
`fmt -check`, `init -backend=false` and `validate` so a broken change is caught
in review; applying is a human act, done from CloudShell, and is written up in
the Handoff issue that accompanies any change here.

## What it creates

| Resource | Why it is shaped this way |
|---|---|
| GitHub OIDC provider | So Actions can assume a role with **no stored AWS keys**. There is no secret to leak, rotate, or find in a log. |
| `alert-platform-deploy` role | Assumable *only* by this repo, *only* from `refs/heads/main`. Anything not merged through green CI cannot reach production. |
| `alert-platform-instance` role | Lets the host itself read its own secrets from SSM. This is what replaces the root access keys currently on the box. |
| `AlertPlatform-Observe` document | Read-only verbs only. The observe workflow can call nothing else. |
| `AlertPlatform-Deploy` document | plan and apply. Separate from Observe so the hourly read path cannot change anything even if it is wrong. |

## The boundary

Three independent things have to agree before a command runs on the host:

1. **IAM trust** — is this really a workflow run from `main` of this repo?
2. **IAM policy** — may it `ssm:SendCommand`, for *these two documents*, on
   *this one instance*?
3. **The wrapper** (`deploy/ops/alert-deploy`) — is the verb in its fixed set,
   and are its arguments the shape they claim to be?

Each is narrower than the last, and the wrapper is the narrowest: IAM decides
*where*, the documents decide *which program*, and the wrapper decides *what
may be asked of it*. See `docs/adr/0001-oidc-ssm-over-ssh-keys.md`.

## Variables

`instance_id` and `aws_region` have no defaults on purpose — a default would
let a mistake apply cleanly to the wrong box.
