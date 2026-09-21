output "deploy_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE_ARN repository variable so the workflows can assume it."
  value       = aws_iam_role.deploy.arn
}

output "instance_profile_name" {
  description = "Attach this to the alert host, then delete the root access keys it replaces."
  value       = aws_iam_instance_profile.instance.name
}

output "observe_document_name" {
  value = aws_ssm_document.observe.name
}

output "deploy_document_name" {
  value = aws_ssm_document.deploy.name
}
