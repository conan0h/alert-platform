# No defaults for the two values that decide *what* gets changed. A default
# region or instance id is a way to apply a correct plan to the wrong box.

variable "aws_region" {
  description = "Region the alert-platform instance lives in."
  type        = string
}

variable "instance_id" {
  description = "EC2 instance id of the alert host. Find it with: aws ec2 describe-instances --filters 'Name=tag:Name,Values=ec2-alerts-prod' --query 'Reservations[].Instances[].InstanceId' --output text"
  type        = string

  validation {
    condition     = can(regex("^i-[0-9a-f]{8,17}$", var.instance_id))
    error_message = "instance_id must look like i-0123456789abcdef0."
  }
}

# The OIDC subject GitHub presents embeds immutable numeric ids alongside the
# names: `repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/<branch>`.
# The ids are the security-relevant half. A claim naming only `conan0h/
# alert-platform` would be satisfied by whatever repository happens to sit at
# that path later — after a rename, a transfer, or a deletion and re-creation
# by someone else. The ids name *this* account and *this* repository and
# cannot be reused.
#
# Find them with:
#   curl -s https://api.github.com/users/conan0h | jq .id
#   curl -s https://api.github.com/repos/conan0h/alert-platform | jq .id
# or read them off the `sub` claim in an Actions run.

variable "github_owner" {
  description = "GitHub account that owns the repository."
  type        = string
  default     = "conan0h"
}

variable "github_owner_id" {
  description = "Immutable numeric id of the GitHub account. Half of what makes the trust policy un-spoofable by a rename."
  type        = string
  default     = "98814385"
}

variable "github_repo_name" {
  description = "Repository name."
  type        = string
  default     = "alert-platform"
}

variable "github_repo_id" {
  description = "Immutable numeric id of the repository."
  type        = string
  default     = "1340575956"
}

variable "secrets_prefix" {
  description = "SSM Parameter Store prefix holding the fleet's secrets. Must match delivery.secrets_prefix in fleet/fleet.yaml."
  type        = string
  default     = "/alert-platform/prod"
}

variable "service_user" {
  description = "Unix account the managed services run as. Must match runtime.user in fleet/fleet.yaml."
  type        = string
  default     = "svc-alerts"
}

variable "ops_user" {
  description = "Unix account the SSM documents run the deploy wrapper as. Created by deploy/ops/bootstrap-host.sh."
  type        = string
  default     = "alert-ops"
}
