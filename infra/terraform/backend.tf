# State lives in S3 so that CI can apply this module. Before this existed the
# state was local to whoever ran Terraform, which meant only a person with that
# working directory could apply, and a second apply from anywhere else would
# have proposed creating everything again.
#
# The bucket and lock table are created by infra/bootstrap, which is applied
# once by hand. A backend block cannot interpolate variables or data sources, so
# the bucket name is a literal here and must match the one that module computes:
# "alert-platform-tfstate-<account-id>". The account id is not a credential; it
# is already present in the role ARN held as a repository variable.
#
# CI runs `terraform init -backend=false`, which skips this block entirely, so
# validation does not need the bucket to exist or any credential to read it.
terraform {
  backend "s3" {
    bucket         = "alert-platform-tfstate-834088498569"
    key            = "infra/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "alert-platform-tflock"
    encrypt        = true
  }
}
