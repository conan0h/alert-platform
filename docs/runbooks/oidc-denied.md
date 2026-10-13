# Runbook: a workflow cannot assume its AWS role

**Symptom:** `observe`, `deploy` or `infra` fails at "Assume the … role via
OIDC" with `Could not assume role with OIDC: Not authorized to perform
sts:AssumeRoleWithWebIdentity`. Nothing reached the host, so the fleet is
unchanged, and it is also unobserved.

AWS does not say which condition failed. Compare what the token claimed with
what the trust policy accepts.

## 1. What did the token claim?

`observe` prints the claims (never the token) in the step "What the OIDC token
claims", and annotates the run with the subject. The value the deploy role
expects is `local.oidc_subject_main` in
[`infra/terraform/oidc.tf`](../../infra/terraform/oidc.tf):

    repo:conan0h@98814385/alert-platform@1340575956:ref:refs/heads/main

Also check `aud` is `sts.amazonaws.com` and `ref` is `refs/heads/main`.

## 2. What does AWS trust now? (CloudShell, read-only)

    aws iam get-role --role-name alert-platform-deploy --query Role.AssumeRolePolicyDocument --output json

    aws iam list-open-id-connect-providers --output json

The first should show the subject above under `StringEquals`; the second
should list `token.actions.githubusercontent.com`.

## 3. Decide

- **The subject differs from the policy** (GitHub changed the claim format,
  or the repository's OIDC subject customization was changed): the fix is a
  Terraform change to `oidc.tf` and `iam_infra.tf`, reviewed against
  [ADR 0001](../adr/0001-oidc-ssm-over-ssh-keys.md). Never widen the match
  with `StringLike` or accept two forms. The infra role trusts the same
  subject, so this apply is Conan's, from CloudShell.
- **The policy or the provider differs from Terraform** (edited or deleted
  outside the pipeline): restore it by applying `infra/terraform` from `main`,
  and record who changed it, from CloudTrail:

      aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=alert-platform-deploy --max-results 20 --output json

- **Both match**: read the CloudTrail `AssumeRoleWithWebIdentity` event for
  the failed run (`errorCode`, `errorMessage`).
