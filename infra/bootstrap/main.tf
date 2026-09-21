# Terraform state for infra/terraform, created once so that everything else can
# be applied by CI instead of by a person.
#
# This is a separate root module with local state on purpose. A backend cannot
# reference resources defined in the module that configures it, so the bucket
# and lock table have to exist before infra/terraform can initialise. Keeping
# them here avoids the usual dance of commenting a backend block out, applying,
# and commenting it back in.
#
# Applied once, by hand, by Conan (see the Handoff issue). Its own state is
# disposable: these are two resources with fixed names, and `terraform import`
# recovers them if it is ever needed. Nothing here is applied again, which is
# why it is acceptable that CI cannot reach it.
#
#   cd infra/bootstrap
#   terraform init
#   terraform apply -var aws_region=us-east-1

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "alert-platform"
      ManagedBy = "terraform"
      Repo      = "conan0h/alert-platform"
      Module    = "bootstrap"
    }
  }
}

variable "aws_region" {
  description = "Region to hold the state bucket and lock table."
  type        = string
}

data "aws_caller_identity" "current" {}

locals {
  # Bucket names are globally unique, so the account id disambiguates. It is
  # not a credential — it already appears in the role ARN held as a repository
  # variable — and a backend block cannot interpolate a variable, so the same
  # literal is repeated in infra/terraform/backend.tf and must match.
  state_bucket = "alert-platform-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table   = "alert-platform-tflock"
}

# --- state bucket ----------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # State is the record of what production is. Losing it does not break the
  # running fleet, but it does mean the next apply cannot tell what exists and
  # proposes to create it all — the same failure signature §6 calls out for
  # plans, arriving by a different route.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning is the actual recovery mechanism: a corrupt or truncated state
# write is restored by fetching the previous version, which is not possible
# from a bucket that only holds the latest.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# State can contain resource attributes that are sensitive even when no secret
# value is stored deliberately, so the bucket is never public and never
# ACL-controlled.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# --- lock table ------------------------------------------------------------

# Without this, two concurrent applies can interleave writes and leave state
# describing neither outcome. `infra.yml` also serialises on a concurrency
# group, but that only covers runs started from this repository; the lock
# covers a human running apply at the same time.
resource "aws_dynamodb_table" "lock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "state_bucket" {
  description = "Set as the bucket in infra/terraform/backend.tf. Already hardcoded there; this confirms it matches."
  value       = aws_s3_bucket.state.id
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}
