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

variable "github_repo" {
  description = "owner/name of the repository whose main branch may deploy."
  type        = string
  default     = "conan0h/alert-platform"
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
