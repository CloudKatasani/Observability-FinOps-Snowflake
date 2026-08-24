terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Remote state with a DynamoDB lock. Filled in by `terraform init -backend-config`
  # (see backend.hcl.example) rather than hardcoded, so this file carries no
  # account id and can be read by anyone.
  #
  # The state contains resource ids, endpoints, and ARNs — no secret values
  # (§27.13) — but it still describes the deployment in full, so the bucket is
  # private, versioned, and KMS-encrypted.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Application = "snowobs"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

# Development: single AZ-tolerant, no Multi-AZ, small instances, shell access
# allowed, and a destroyable database. Nothing here should hold data anyone
# would miss.
module "platform" {
  source = "../../modules/platform"

  name        = var.name
  environment = "dev"
  region      = var.region
  image       = var.image

  domain_name     = var.domain_name
  hosted_zone_id  = var.hosted_zone_id
  certificate_arn = var.certificate_arn
  ingress_cidrs   = var.ingress_cidrs

  data_lake_bucket_name = var.data_lake_bucket_name

  # Sizing — the "Small" profile in docs/AWS_COST.md.
  db_instance_class    = "db.t4g.small"
  db_multi_az          = false
  redis_node_type      = "cache.t4g.micro"
  redis_replica_count  = 0
  app_cpu              = 512
  app_memory           = 1024
  app_min_count        = 1
  app_max_count        = 2
  worker_cpu           = 512
  worker_memory        = 1024
  worker_min_count     = 1
  worker_max_count     = 2
  app_desired_count    = 1
  worker_desired_count = 1

  # Development conveniences. Every one of these is deliberately false in prod.
  enable_ecs_exec     = true
  apply_immediately   = true
  deletion_protection = false
  skip_final_snapshot = true
  log_retention_days  = 30

  # Application configuration (§21).
  app_mode      = var.app_mode
  auth_provider = var.auth_provider
  auth_issuer   = var.auth_issuer
  llm_provider  = var.llm_provider
  finops_mode   = "showback"

  snowflake_account   = var.snowflake_account
  snowflake_user      = var.snowflake_user
  snowflake_role      = var.snowflake_role
  snowflake_warehouse = var.snowflake_warehouse

  # ECR and the GitHub OIDC deploy role are account-wide and live here, so the
  # production stack does not have to be applied to ship a build.
  create_ci_resources = true
  github_repository   = var.github_repository
  allowed_git_refs    = var.allowed_git_refs
}
