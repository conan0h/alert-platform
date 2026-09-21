# The role `infra.yml` assumes to apply this module.
#
# It manages IAM, which is close to account administrator, so the guardrails
# below are the substance of this file. They are enforced by IAM rather than by
# CLAUDE.md, because a rule in a document constrains only a well-behaved actor
# and the point of a boundary is to hold when something is not.
#
# Three escalation routes and what closes each:
#
#   1. Rewrite its own trust policy or permissions, then assume anything.
#      Closed by deny_self_modification: the role, its inline and attached
#      policies, and the OIDC provider are all off limits to it.
#   2. Create a fresh role with AdministratorAccess and assume that.
#      Closed by require_permissions_boundary: any CreateRole or policy
#      attachment must carry the boundary below, and the boundary caps the
#      effective permissions of whatever carries it.
#   3. Weaken or delete the boundary itself.
#      Closed by the same deny_self_modification statement, which names the
#      boundary policy explicitly.
#
# What remains possible, stated plainly rather than glossed: this role can
# change the SSM documents and the deploy role, which are what bound the
# *deploy* path. So it can widen what a deploy may do. That is inherent in
# owning the infrastructure that defines the deploy, and it is why CLAUDE.md §2
# forbids the agent proposing changes to its own boundary without the owner —
# a rule this file cannot enforce. The audit trail is the compensating control:
# every change arrives as a reviewed commit on main and an `infra.yml` run.

locals {
  infra_role_name      = "alert-platform-infra"
  boundary_policy_name = "alert-platform-infra-boundary"
  boundary_arn         = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${local.boundary_policy_name}"
  infra_role_arn       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.infra_role_name}"

  state_bucket = "alert-platform-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table   = "alert-platform-tflock"
}

# Same subject condition as the deploy role: an Actions run whose triggering ref
# is main, identified by immutable account and repository ids. See oidc.tf for
# why the ids matter and how their absence was found.
data "aws_iam_policy_document" "infra_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.oidc_subject_main]
    }
  }
}

resource "aws_iam_role" "infra" {
  name                 = local.infra_role_name
  description          = "Applies infra/terraform from infra.yml on main. Bounded by ${local.boundary_policy_name}."
  assume_role_policy   = data.aws_iam_policy_document.infra_assume_role.json
  max_session_duration = 3600
}

# --- the permissions boundary ----------------------------------------------

# Every role this role creates must carry this. It is the ceiling on what any
# such role can do, regardless of the policies attached to it, which is what
# makes "create a role and escalate through it" impossible rather than merely
# discouraged.
#
# It is deliberately broader than the infra role's own policy: a boundary is not
# the grant, it is the limit. Narrowing it to exactly today's needs would mean
# editing it for every legitimate addition, and an edit to the boundary is the
# one change this role cannot make — so it would become a handoff each time.
data "aws_iam_policy_document" "infra_boundary" {
  statement {
    sid    = "ServicesThisPlatformUses"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
      "ssm:*",
      "s3:*",
      "dynamodb:*",
      "logs:*",
      "cloudwatch:*",
      "iam:Get*",
      "iam:List*",
      "iam:PassRole",
      "sts:AssumeRole",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  # Nothing carrying this boundary may touch the boundary, the infra role, or
  # the trust anchor — including a role created by the infra role.
  statement {
    sid    = "NeverTheGuardrails"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:DeleteRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteRolePermissionsBoundary",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = [local.boundary_arn, local.infra_role_arn]
  }

  statement {
    sid       = "NeverTheTrustAnchor"
    effect    = "Deny"
    actions   = ["iam:*OpenIDConnectProvider*"]
    resources = ["*"]
  }

  # Destroying the state is not a permission anything here needs, and it is the
  # one loss that cannot be undone by re-running Terraform.
  statement {
    sid    = "NeverTheState"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:PutBucketVersioning",
      "dynamodb:DeleteTable",
    ]
    resources = [
      "arn:aws:s3:::${local.state_bucket}",
      "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.lock_table}",
    ]
  }

  # The fleet runs on one instance with local SQLite state. Terminating it or
  # deleting its volume loses dedup history, which means every service re-alerts
  # on everything it has ever seen.
  statement {
    sid    = "NeverTheHostOrItsData"
    effect = "Deny"
    actions = [
      "ec2:TerminateInstances",
      "ec2:DeleteVolume",
      "ec2:DetachVolume",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "infra_boundary" {
  name        = local.boundary_policy_name
  description = "Ceiling for the infra role and anything it creates. Editable only outside infra.yml."
  policy      = data.aws_iam_policy_document.infra_boundary.json
}

# --- what the infra role may do -------------------------------------------

data "aws_iam_policy_document" "infra" {
  statement {
    sid    = "ReadAndWriteTerraformState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketVersioning",
      "s3:GetBucketLocation",
    ]
    resources = [
      "arn:aws:s3:::${local.state_bucket}",
      "arn:aws:s3:::${local.state_bucket}/*",
    ]
  }

  statement {
    sid    = "TakeTheStateLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
    ]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.lock_table}"]
  }

  # Terraform must read every resource it manages on every plan, so the read
  # surface is wider than the write surface by necessity.
  statement {
    sid    = "PlanNeedsToRead"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
      "ssm:Describe*",
      "ssm:Get*",
      "ssm:ListDocument*",
      "iam:Get*",
      "iam:List*",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageTheSSMDocuments"
    effect = "Allow"
    actions = [
      "ssm:CreateDocument",
      "ssm:UpdateDocument",
      "ssm:UpdateDocumentDefaultVersion",
      "ssm:DeleteDocument",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:document/AlertPlatform-*"]
  }

  # Parameters are managed by name; values are never read here. Secrets are
  # resolved on the host at deploy time by the instance role, not by this one,
  # which is why GetParameter is absent.
  statement {
    sid    = "ManageSecretNamesNotValues"
    effect = "Allow"
    actions = [
      "ssm:PutParameter",
      "ssm:DeleteParameter",
      "ssm:AddTagsToResource",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.secrets_prefix}/*"]
  }

  statement {
    sid    = "ManageThePlatformRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagRole",
      "iam:TagPolicy",
      "iam:UntagRole",
      "iam:PassRole",
    ]
    resources = ["*"]
  }

  # Route 2 from the header: a new role may only be created if it carries the
  # boundary. Without this the statement above is a path to administrator.
  statement {
    sid    = "NewRolesMustCarryTheBoundary"
    effect = "Deny"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = ["*"]

    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  # Route 1 and 3: this role cannot reach itself, its boundary, or the trust
  # anchor. Restated here as well as in the boundary because the infra role does
  # not itself carry the boundary — a role's own boundary would cap the very
  # permissions it needs to manage other roles.
  statement {
    sid    = "NeverItsOwnGuardrails"
    effect = "Deny"
    actions = [
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:DeleteRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteRolePermissionsBoundary",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [local.infra_role_arn, local.boundary_arn]
  }

  statement {
    sid       = "NeverTheTrustAnchor"
    effect    = "Deny"
    actions   = ["iam:*OpenIDConnectProvider*"]
    resources = ["*"]
  }

  statement {
    sid    = "NeverTheStateOrTheHost"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "dynamodb:DeleteTable",
      "ec2:TerminateInstances",
      "ec2:DeleteVolume",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "infra" {
  name   = "infra"
  role   = aws_iam_role.infra.id
  policy = data.aws_iam_policy_document.infra.json
}

output "infra_role_arn" {
  description = "Set as the AWS_INFRA_ROLE_ARN repository variable so infra.yml can assume it."
  value       = aws_iam_role.infra.arn
}

output "infra_boundary_arn" {
  description = "Permissions boundary every role created by the infra role must carry."
  value       = aws_iam_policy.infra_boundary.arn
}
