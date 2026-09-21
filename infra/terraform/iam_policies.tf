# What the deploy role may do: send exactly two documents, to exactly one
# instance, and read back what happened. Nothing else.
#
# The pair of Resource entries on SendCommand is the part worth reading. AWS
# evaluates a SendCommand call against *both* the document and each target
# instance, so listing only the documents would permit sending them to every
# instance in the account, and listing only the instance would permit sending
# any document — including AWS-RunShellScript, which takes arbitrary commands
# and would make every other control here pointless.

data "aws_iam_policy_document" "deploy" {
  statement {
    sid    = "SendOnlyOurDocumentsToOnlyOurInstance"
    effect = "Allow"
    actions = [
      "ssm:SendCommand",
    ]
    resources = [
      aws_ssm_document.observe.arn,
      aws_ssm_document.deploy.arn,
      "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${var.instance_id}",
    ]
  }

  # Reading results is a separate action from sending, and is not scopeable to
  # a document, so it is scoped to this account's command invocations and kept
  # in its own statement rather than widening the one above.
  statement {
    sid    = "ReadBackWhatHappened"
    effect = "Allow"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
      "ssm:ListCommands",
    ]
    resources = ["*"]
  }

  # Enough to confirm the SSM agent is online before sending a command, so a
  # failed deploy says "the host is not reachable" instead of timing out.
  statement {
    sid       = "CheckTheHostIsReachable"
    effect    = "Allow"
    actions   = ["ssm:DescribeInstanceInformation"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "alert-platform-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# --- the host's own role ---------------------------------------------------
#
# This is what ends the root access keys currently on the box. The host needs
# two things: to be reachable by SSM at all, and to read its own secrets at
# deploy time. It does not need, and must not have, permission to send itself
# commands — that would let a compromised service drive its own deploys.

data "aws_iam_policy_document" "instance_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "alert-platform-instance"
  description        = "Attached to the alert host. Lets it be managed by SSM and read its own secrets."
  assume_role_policy = data.aws_iam_policy_document.instance_assume_role.json
}

resource "aws_iam_role_policy_attachment" "instance_ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance_secrets" {
  statement {
    sid    = "ReadOwnSecrets"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.secrets_prefix}/*",
    ]
  }

  # SecureString parameters are KMS-encrypted, so reading one needs Decrypt.
  # ViaService keeps that grant from being usable against anything else
  # encrypted with the same key: it is only honoured for calls arriving
  # through SSM.
  statement {
    sid       = "DecryptThemButOnlyThroughSSM"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "instance_secrets" {
  name   = "alert-platform-instance-secrets"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance_secrets.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "alert-platform-instance"
  role = aws_iam_role.instance.name
}
