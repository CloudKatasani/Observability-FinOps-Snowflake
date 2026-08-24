terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Application = "snowobs"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

# Production: Multi-AZ database, a Redis replica with automatic failover, NAT in
# every AZ, deletion protection on, no shell into running tasks, and changes
# applied in the maintenance window rather than immediately.
#
# The differences from dev are all sizing and safety switches. The topology is
# identical, because the point of a staging environment is to be the same shape
# as production (R10).
module "platform" {
  source = "../../modules/platform"

  name        = var.name
  environment = "prod"
  region      = var.region
  image       = var.image

  domain_name            = var.domain_name
  hosted_zone_id         = var.hosted_zone_id
  certificate_arn        = var.certificate_arn
  ingress_cidrs          = var.ingress_cidrs
  internal_load_balancer = var.internal_load_balancer
  # An internet-facing load balancer without a WAF is not a decision anyone
  # should make by omission.
  enable_waf             = !var.internal_load_balancer
  alb_access_logs_bucket = var.alb_access_logs_bucket

  data_lake_bucket_name = var.data_lake_bucket_name
  upload_retention_days = var.upload_retention_days

  # Sizing — the "Standard" profile in docs/AWS_COST.md. Override for "Large".
  availability_zone_count = 3
  enable_nat_gateway      = var.enable_nat_gateway
  one_nat_gateway_per_az  = true

  db_instance_class           = var.db_instance_class
  db_allocated_storage_gb     = var.db_allocated_storage_gb
  db_max_allocated_storage_gb = var.db_max_allocated_storage_gb
  db_multi_az                 = true
  backup_retention_days       = var.backup_retention_days

  redis_node_type     = var.redis_node_type
  redis_replica_count = 1

  app_cpu              = var.app_cpu
  app_memory           = var.app_memory
  app_desired_count    = var.app_min_count
  app_min_count        = var.app_min_count
  app_max_count        = var.app_max_count
  worker_cpu           = var.worker_cpu
  worker_memory        = var.worker_memory
  worker_desired_count = var.worker_min_count
  worker_min_count     = var.worker_min_count
  worker_max_count     = var.worker_max_count

  # Production safety.
  enable_ecs_exec     = false
  apply_immediately   = false
  deletion_protection = true
  skip_final_snapshot = false
  log_retention_days  = var.log_retention_days
  alarm_topic_arn     = var.alarm_topic_arn

  # Application configuration (§21).
  app_mode        = "live"
  tenancy         = var.tenancy
  auth_provider   = "oidc"
  auth_issuer     = var.auth_issuer
  auth_client_id  = var.auth_client_id
  finops_mode     = var.finops_mode
  allow_adhoc_sql = false

  llm_provider       = var.llm_provider
  llm_model_strong   = var.llm_model_strong
  llm_model_fast     = var.llm_model_fast
  bedrock_model_arns = var.bedrock_model_arns

  snowflake_account   = var.snowflake_account
  snowflake_user      = var.snowflake_user
  snowflake_role      = var.snowflake_role
  snowflake_warehouse = var.snowflake_warehouse

  webhook_secret_enabled = var.webhook_secret_enabled

  # ECR and the OIDC deploy role are created once, in dev; production consumes
  # the images and does not own the registry.
  create_ci_resources = false
}
