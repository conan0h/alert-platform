# GitHub Actions -> AWS, without a stored credential anywhere.
#
# The alternative this replaces is an access key in repository secrets. That
# key would be long-lived, copyable, valid from anywhere, and invisible once
# taken. An OIDC token is minted per run, expires in minutes, and is only
# accepted for the subject below — so "who may deploy" becomes a property of
# the repository's branch protection rather than of a string in a settings
# page.

data "aws_caller_identity" "current" {}

locals {
  # Exactly what an Actions run on main presents. Verified against a live run
  # rather than assumed from documentation — see the note above.
  oidc_subject_main = format(
    "repo:%s@%s/%s@%s:ref:refs/heads/main",
    var.github_owner, var.github_owner_id, var.github_repo_name, var.github_repo_id,
  )
}

# GitHub publishes one OIDC issuer for all of Actions. The account may already
# have this provider from another repository, in which case import it rather
# than creating a second: AWS permits only one provider per issuer URL.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  lifecycle {
    # The thumbprint rotates when GitHub rotates its certificate. AWS has
    # verified this provider against its own trust store since mid-2023 and
    # ignores the value, so a stale thumbprint is not a security control and
    # a diff on it is noise.
    ignore_changes = [thumbprint_list]
  }
}

# The subject condition is the whole security boundary for "what may deploy".
#
# It matches a workflow run whose triggering ref is main and nothing else: not
# a pull request, not a fork, not a tag, not another repository that happens to
# be named the same. Two things make it tight:
#
#   - It is StringEquals against one exact string. A wildcard here
#     (repo:owner/name:*) would let any PR branch assume the role, which is
#     the usual way this pattern is got wrong. `StringLike` with a `*` after
#     the owner would be worse still — `conan0h*` also matches `conan0hx`.
#   - It carries the account and repository ids, because that is what GitHub
#     actually sends. `repo:conan0h/alert-platform:...` — the form the docs
#     show and the form this policy originally used — is rejected outright,
#     which is how the mismatch was found: the first real `observe` run failed
#     with "Not authorized to perform sts:AssumeRoleWithWebIdentity" against a
#     policy that looked correct. See ADR 0001.
data "aws_iam_policy_document" "deploy_assume_role" {
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

resource "aws_iam_role" "deploy" {
  name               = "alert-platform-deploy"
  description        = "Assumed by GitHub Actions on main to drive SSM on the alert host. No console access, no keys."
  assume_role_policy = data.aws_iam_policy_document.deploy_assume_role.json

  # An hour is longer than any deploy takes and shorter than a working day.
  max_session_duration = 3600
}
