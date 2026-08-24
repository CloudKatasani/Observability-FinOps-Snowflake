output "kms_key_arn" {
  value = aws_kms_key.this.arn
}

output "kms_key_id" {
  value = aws_kms_key.this.key_id
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "secret_arns" {
  description = <<-EOT
    ARNs of the secret *containers*, keyed by purpose. These are references, not
    values — the task definition injects them by ARN and the value never passes
    through Terraform (§27.13).
  EOT
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Names to use with `aws secretsmanager put-secret-value` when populating them."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}
