# ADR 0003 — The agent owns AWS, bounded by IAM rather than by policy documents

**Status:** accepted, 2026-09-21. Granted by the owner.
**Changes the operating model.** Read with ADR 0001, which established the
deploy path this extends.

## Context

Until now every AWS change was a handoff. Terraform state was local to whoever
ran it, so only a person with that working directory could apply, and a second
apply from anywhere else would have proposed creating everything again. The
practical consequences:

- An SSM document change — needed to add a read verb, for example the `alerts`
  verb that backlog #27 requires — waited on a human.
- The agent could write Terraform but never see a plan against the real account,
  so `terraform validate` in CI was the only feedback before a human applied it.
- Three of the eight deploy-path bugs in the week of 2026-09-15 involved the
  agent being unable to test the thing it had written.

The owner's instruction was to remove that bottleneck.

## Decision

The agent owns AWS through Terraform in this repository, applied by `infra.yml`
from `main` over OIDC. No long-lived credential exists.

State moves to S3 with a DynamoDB lock, created by `infra/bootstrap` — a
separate root module, applied once by hand. A backend cannot reference resources
defined in the module that configures it, so the bucket has to exist first; a
separate module avoids the usual dance of commenting the backend block out and
back in.

The `alert-platform-infra` role's trust policy accepts only the same subject as
the deploy role: an Actions run whose triggering ref is `main`, identified by
immutable account and repository ids.

## What bounds it, and why it is IAM and not this document

An IAM-capable role is close to account administrator. Three escalation routes
exist, and each is closed by mechanism rather than by instruction, because a rule
in a document constrains only a well-behaved actor and the point of a boundary is
to hold when something is not:

| Route | What closes it |
|---|---|
| Rewrite its own trust policy or permissions, then assume anything | Explicit `Deny` on the infra role's own ARN for every `iam:Update*`, `Put*`, `Delete*`, `Attach*` and `Detach*` |
| Create a role with `AdministratorAccess` and assume that | `iam:CreateRole` and `PutRolePermissionsBoundary` denied unless `iam:PermissionsBoundary` equals the boundary ARN. The boundary caps effective permissions of whatever carries it |
| Weaken or delete the boundary | The same deny statement names the boundary policy explicitly |

Also denied outright: anything touching the OIDC provider, deleting the state
bucket or lock table, and `ec2:TerminateInstances` or `DeleteVolume` — the fleet
keeps its dedup state on that instance's disk, so losing the volume means every
service re-alerts on everything it has ever seen.

The boundary is deliberately broader than the infra role's own policy. A boundary
is a ceiling, not a grant. Narrowing it to exactly today's needs would mean
editing it for every legitimate addition, and editing the boundary is the one
change this role cannot make — so each addition would become a handoff again,
which is the problem being solved.

## What this does not close, stated plainly

The infra role can change the SSM documents and the deploy role. Those are what
bound the *deploy* path. So it can widen what a deploy may do.

That is inherent in owning the infrastructure that defines the deploy, and no
IAM policy fixes it without giving the ownership back. The compensating controls
are that every change arrives as a commit on `main` through green CI, that
`infra.yml` plans before it applies and the plan is read, and that CLAUDE.md §2
forbids the agent proposing changes to its own boundary without the owner — a
rule this ADR cannot enforce and does not pretend to.

A reviewer should treat the audit trail, not the IAM policy, as the control on
that specific risk.

## Alternatives rejected

**Keep the handoff.** Honest about the risk and costs a human step for every
document or role change. Rejected by the owner's instruction, and the evidence
was that the bottleneck was producing bugs rather than preventing them.

**Give the deploy role the extra permissions instead of a second role.** Fewer
moving parts, but it widens the role that runs on every deploy, including the
read-only observe path. Two roles means the read path cannot touch IAM at all.

**Scope the boundary tightly and accept the edits.** See above: it converts every
addition into the handoff this removes.

**Let CI apply without a saved plan.** `infra.yml` applies the plan file it just
produced, so Terraform refuses it if state moved in between — the same staleness
guard the deploy path relies on.

## Consequences

- One manual apply remains, and always will: the agent cannot grant itself
  access, so `infra/bootstrap` and the first `iam_infra` apply are the owner's.
  After that, AWS changes are the agent's.
- `terraform validate` now runs in CI for both modules, so a module that does not
  parse fails before it reaches an apply.
- Backlog #27's `alerts` verb and #32's `--since` parameter both need SSM
  document changes, and both become possible without a handoff once this lands.
- The state bucket and lock table carry `prevent_destroy`. Losing state does not
  break the running fleet, but the next plan would propose creating everything —
  the same signature §6 calls out, arriving by a different route.
