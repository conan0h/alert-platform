# The two things the pipeline may ask the host to do.
#
# Documents rather than `AWS-RunShellScript`: RunShellScript takes arbitrary
# commands, so permitting it would make the IAM policy below meaningless —
# anyone able to send a command could send any command. These documents take a
# verb and hand it to the wrapper, which is the component that decides whether
# the verb is allowed at all.
#
# Note what the documents do *not* do: they never interpolate a parameter into
# a shell string. Every value is passed as a separate argv element, and the
# wrapper re-validates each one. A document is a transport, not a guard.

locals {
  # Parameters arrive as shell variables, quoted at the point of use. The
  # wrapper validates them again regardless — this is defence in depth, not
  # the defence.
  observe_script = <<-SCRIPT
    set -euo pipefail
    exec runuser -u ${var.ops_user} -- sudo -n /usr/local/sbin/alert-deploy "$1" --actor "$2"
  SCRIPT

  deploy_script = <<-SCRIPT
    set -euo pipefail
    if [ "$1" = "apply" ]; then
      exec runuser -u ${var.ops_user} -- sudo -n /usr/local/sbin/alert-deploy apply "$3" --actor "$2"
    fi
    exec runuser -u ${var.ops_user} -- sudo -n /usr/local/sbin/alert-deploy "$1" --actor "$2"
  SCRIPT
}

# Read-only. Separated from Deploy so the hourly observe workflow holds a
# permission that cannot change anything, whatever the workflow does wrong.
resource "aws_ssm_document" "observe" {
  name            = "AlertPlatform-Observe"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Read-only inspection of the alert-platform fleet. Cannot change the host."
    parameters = {
      verb = {
        type          = "String"
        description   = "Which read-only verb to run."
        allowedValues = ["status", "drift", "history", "health", "logs"]
      }
      actor = {
        type           = "String"
        description    = "Audit actor, e.g. gha:<run-id>."
        allowedPattern = "^[A-Za-z0-9_.:@/-]{1,64}$"
        default        = "ssm:observe"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "observe"
      inputs = {
        timeoutSeconds = "300"
        runCommand     = ["bash -s '{{ verb }}' '{{ actor }}' <<'EOF'\n${local.observe_script}\nEOF"]
      }
    }]
  })
}

# plan and apply. `allowedValues` keeps anything else out at the API before
# the host is even contacted.
resource "aws_ssm_document" "deploy" {
  name            = "AlertPlatform-Deploy"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Plan or apply a change to the alert-platform fleet, through the gated wrapper."
    parameters = {
      verb = {
        type          = "String"
        description   = "plan computes a change set; apply executes one already planned."
        allowedValues = ["plan", "apply"]
      }
      actor = {
        type           = "String"
        description    = "Audit actor, e.g. gha:<run-id>. Lands in the audit log."
        allowedPattern = "^[A-Za-z0-9_.:@/-]{1,64}$"
      }
      planId = {
        type           = "String"
        description    = "Plan fingerprint to apply. Ignored by plan."
        allowedPattern = "^[0-9a-f]{12}$|^$"
        default        = ""
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      inputs = {
        # Longer than Observe: an apply deploys services one at a time, each
        # behind a health gate with a startup grace period.
        timeoutSeconds = "1800"
        runCommand     = ["bash -s '{{ verb }}' '{{ actor }}' '{{ planId }}' <<'EOF'\n${local.deploy_script}\nEOF"]
      }
    }]
  })
}
