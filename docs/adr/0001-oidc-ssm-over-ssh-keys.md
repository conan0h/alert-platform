# ADR 0001 — Reach production over OIDC and SSM, not SSH keys in CI

Status: accepted
Date: 2026-09-21

## Context

Until now `alertctl` was driven by hand on the host itself, over a loopback
SSH alias, with `sudo`. That works exactly once per operator and leaves no
record anybody else can read. The platform's whole claim is gated, audited,
reversible deploys, and a deploy performed by a person typing on the box is
none of those things.

There is a second problem, which is the one that forced the issue. An
autonomous agent develops and operates this fleet. It has no credentials and
must not acquire any: a long-lived key held by an automated principal is the
worst of both worlds, because it is as powerful as a human's and nobody
notices when it is used.

So the question is not "how do we automate a deploy". It is: **what is the
narrowest possible path from a merged commit to a running service, such that
an agent can drive it and a reviewer can audit it afterwards?**

## Decision

Four layers, each narrower than the one above it, none of which involves a
stored credential.

1. **GitHub OIDC → IAM.** Actions mints a short-lived token per run. The role
   trusts exactly one subject: `repo:conan0h/alert-platform:ref:refs/heads/main`.
   Not a wildcard, not a pull request, not a tag.
2. **IAM policy → two SSM documents on one instance.** `ssm:SendCommand`, and
   only for `AlertPlatform-Observe` and `AlertPlatform-Deploy`, and only
   against the one instance id.
3. **SSM documents → one program.** The documents take a verb from a fixed
   `allowedValues` list and hand it to a wrapper. They never build a shell
   command out of a parameter.
4. **The wrapper → a fixed verb set.** `deploy/ops/alert-deploy` runs as root
   via a sudo rule that names it and nothing else. It validates every
   argument by shape before use, and there is no flag in it that skips a gate.

Every apply records `actor = gha:<run-id>` in the audit log, which is what
turns an entry into a link back to the workflow run, the commit and the PR.

## Why not the alternatives

**An SSH key in repository secrets.** The obvious answer, and the one this
replaces. The key is long-lived, works from anywhere, is copyable the moment
anyone can read the secret or exfiltrate it from a job, and grants a shell —
so the blast radius is "everything the deploy user can do", forever. OIDC
tokens expire in minutes, are only accepted for one repository's main branch,
and cannot be replayed from a laptop.

**`AWS-RunShellScript` instead of custom documents.** Far less work. It also
makes layers 2–4 decorative: permitting it means permitting arbitrary
commands, so the IAM policy would no longer bound anything. A document with
`allowedValues` is the difference between "may deploy" and "has root".

**A pull-based agent on the host** polling for a desired-state commit. This is
a genuinely good design and is how a larger fleet should work — no inbound
path at all. Rejected for now because it inverts the control flow: a human can
no longer read a plan and decide, which is the review step this platform
exists to add. Revisit when there is more than one host.

**Let the agent hold AWS credentials directly.** Simplest to build, and
disqualifying. The agent would then be a principal whose actions cannot be
distinguished from a human's, holding a secret with no expiry. The current
shape means the agent's *only* capability is "cause a workflow to run on a
branch that required review to reach" — it cannot deploy anything a human did
not merge.

## Consequences

**Good.**
- No AWS keys exist to leak or rotate. The root account keys currently on the
  host are replaced by an instance role; deleting them is part of the handoff.
- Nothing unreviewed can reach production, because the trust policy only
  accepts `main`.
- SSH becomes unnecessary from anywhere, so port 22 can be closed.
- Every production change has an actor that names the run that caused it.
- The agent operates production with no credentials of its own, and that claim
  is enforced by IAM rather than by convention.

**Bad, and accepted.**
- More moving parts than an SSH key: a provider, two roles, two documents, a
  wrapper, a sudo rule. Each is one more thing to get wrong, which is why the
  wrapper is tested and the Terraform is validated in CI.
- A deploy now takes two workflow runs — plan, read, apply. That is deliberate
  friction, but it *is* friction.
- Terraform is applied by hand from CloudShell. CI never holds credentials
  that could apply it, which is the point, but it means infrastructure drift
  is possible and is not yet detected.
- The wrapper is a shell script holding a security boundary. Mitigated by
  shellcheck, by argument validation that is tested rather than asserted, and
  by keeping it short enough to read in one sitting.

## Addendum, 2026-09-21: the subject claim is not what the docs show

The first real `observe` run failed with `Not authorized to perform
sts:AssumeRoleWithWebIdentity` against a trust policy that was, on inspection,
exactly what every guide prescribes:

    "token.actions.githubusercontent.com:sub":
        "repo:conan0h/alert-platform:ref:refs/heads/main"

A temporary step that asked for the token and printed only its `sub` and `aud`
claims showed what GitHub actually sends:

    repo:conan0h@98814385/alert-platform@1340575956:ref:refs/heads/main

The subject embeds immutable numeric ids for the account and the repository.
That is a real improvement, and it is worth understanding rather than merely
matching: a claim naming only `conan0h/alert-platform` would be satisfied by
whatever repository sits at that path *later* — after a rename, a transfer, or
a deletion and re-registration by someone else. The ids cannot be reused, so
the claim names this repository and no future impostor.

Two things were tempting and both are wrong:

- **`StringLike` with a wildcard**, e.g. `repo:conan0h*/alert-platform*:ref:…`.
  It would work today and it widens the boundary: `conan0h*` also matches
  `conan0hx`, so an account with a similar name could assume the deploy role.
- **Accepting both the old and new subject forms.** Harmless-looking, and it
  reintroduces exactly the rename-hijack the ids exist to prevent.

So the policy pins the exact subject including the ids. If GitHub changes the
format again this fails closed and loudly, which is the right failure: a deploy
that cannot authenticate is safe, and one that authenticates against a claim we
no longer understand is not.

The general lesson is the one this whole ADR is about. The layering was sound;
the part that broke was an assumption about an external system's behaviour that
nothing in the repo could test. It was found in about ten minutes by reading
what was actually sent instead of reasoning about what should be sent, and that
technique — print the claim, not the token — belongs in the runbook for anything
OIDC.

## Notes

The layering matters more than any individual control: IAM decides *where*,
the documents decide *which program*, and the wrapper decides *what may be
asked of it*. A hole in one is contained by the next. A hole in the wrapper is
contained by nothing, which is why that file is the one to review hardest.
