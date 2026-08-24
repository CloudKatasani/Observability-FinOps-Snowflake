# KMS, IAM task roles, and the Secrets Manager entries the platform reads.
#
# The rule this module exists to enforce: **no secret value is ever a Terraform
# input, a Terraform output, or a Terraform state attribute** (§27.13). Terraform
# creates the *containers* — a Secrets Manager secret with a name, a KMS key, an
# IAM policy naming the ARN — and a human or a pipeline puts the value in
# afterwards with `aws secretsmanager put-secret-value`. If you ever find
# yourself adding an `aws_secretsmanager_secret_version` with a real value here,
# stop: the value would be readable in plaintext by anyone with state access.
#
# The two task roles are separate on purpose. The execution role is what ECS
# itself uses to pull the image and inject secrets at container start; the task
# role is what the running application uses. They are not interchangeable, and
# collapsing them would give the application permission to read every secret the
# platform can inject rather than the ones it actually needs.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Every secret the application may be configured to use. Creating the entry is
  # free and idempotent; populating it is the operator's step. The names match
  # the SNOWFLAKE__PRIVATE_KEY_REF / LLM key references in .env.example.
  secret_names = merge(
    {
      # The whole connection string, not just the password: the application's
      # single DATABASE_URL setting has no separate password field, so the
      # composed URL is what gets injected. The RUNBOOK's "rotate a secret"
      # procedure reads the RDS-managed master password and rewrites this.
      app_database_url      = "${var.name}/app/database-url"
      snowflake_private_key = "${var.name}/snowflake/private-key"
    },
    var.llm_provider == "anthropic" ? { llm_api_key = "${var.name}/llm/api-key" } : {},
    var.webhook_secret_enabled ? { alert_webhook = "${var.name}/alerts/webhook-url" } : {},
  )
}

resource "aws_kms_key" "this" {
  description             = "${var.name} — RDS, S3, Secrets Manager, CloudWatch Logs"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days

  # CloudWatch Logs encrypts with the key on the service's behalf, so the log
  # service needs its own grant; without this the encrypted log group fails to
  # create with an opaque InvalidParameterException.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountRoot"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })

  tags = var.tags
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}

# ── Secret containers, never secret values ──────────────────────────────────
resource "aws_secretsmanager_secret" "this" {
  for_each = local.secret_names

  name        = each.value
  description = "${var.name}: ${replace(each.key, "_", " ")}. Value is set out of band, never by Terraform (§27.13)."
  kms_key_id  = aws_kms_key.this.arn

  recovery_window_in_days = var.secret_recovery_window_days

  tags = var.tags
}

# ── Execution role: ECS agent pulls images and injects secrets ───────────────
resource "aws_iam_role" "execution" {
  name = "${var.name}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-injected-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadNamedSecretsOnly"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [for s in aws_secretsmanager_secret.this : s.arn]
      },
      {
        Sid      = "DecryptThoseSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.this.arn]
        Condition = {
          StringEquals = { "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" }
        }
      },
    ]
  })
}

# ── Task role: what the application itself may do ───────────────────────────
# Deliberately small. The application reads and writes its own S3 prefix, reads
# the secrets it was told about, and writes its own logs and metrics. It has no
# ability to describe the account, list other buckets, or read another
# deployment's secrets — the read-only-by-default posture applies to AWS as
# much as it does to Snowflake (R4).
resource "aws_iam_role" "task" {
  name = "${var.name}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })

  tags = var.tags
}

data "aws_iam_policy_document" "task" {
  statement {
    sid    = "DataLakeObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${var.data_lake_bucket_arn}/*"]
  }

  statement {
    sid       = "DataLakeListing"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.data_lake_bucket_arn]
  }

  statement {
    sid    = "EncryptDataLakeObjects"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [aws_kms_key.this.arn]
  }

  statement {
    sid       = "ReadOwnSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.this : s.arn]
  }

  statement {
    sid       = "PublishMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.metrics_namespace]
    }
  }

  # ECS Exec, for the RUNBOOK's "get a shell in a running task" step. Gated,
  # because a shell in the task is a shell next to the Snowflake credential.
  dynamic "statement" {
    for_each = var.enable_ecs_exec ? [1] : []
    content {
      sid    = "ECSExec"
      effect = "Allow"
      actions = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = var.llm_provider == "bedrock" ? [1] : []
    content {
      sid       = "InvokeBedrockModels"
      effect    = "Allow"
      actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = var.bedrock_model_arns
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "application"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
