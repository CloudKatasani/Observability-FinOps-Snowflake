# The whole AWS deployment, composed. `envs/dev` and `envs/prod` are thin
# wrappers that set sizing and names; the wiring lives here so the two
# environments cannot drift into different topologies.
#
# Order of dependencies:
#
#   security ──▶ network ──▶ edge ──▶ compute
#        │            │                 ▲
#        └──▶ data ───┴─────────────────┘
#              (RDS, Redis, S3)
#
# security comes first because everything is encrypted with its KMS key. It
# breaks what would otherwise be a cycle with `data` by deriving the bucket ARN
# from the bucket *name* — an S3 ARN is `arn:aws:s3:::<name>` and needs no
# lookup — so the task policy can name the exact bucket before the bucket
# exists.

locals {
  tags = merge(
    {
      Application = "snowobs"
      Environment = var.environment
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  data_lake_bucket_arn = "arn:${var.aws_partition}:s3:::${var.data_lake_bucket_name}"
}

module "security" {
  source = "../security"

  name                        = var.name
  region                      = var.region
  data_lake_bucket_arn        = local.data_lake_bucket_arn
  metrics_namespace           = var.metrics_namespace
  llm_provider                = var.llm_provider
  bedrock_model_arns          = var.bedrock_model_arns
  webhook_secret_enabled      = var.webhook_secret_enabled
  enable_ecs_exec             = var.enable_ecs_exec
  secret_recovery_window_days = var.secret_recovery_window_days
  tags                        = local.tags
}

module "network" {
  source = "../network"

  name                    = var.name
  region                  = var.region
  cidr_block              = var.cidr_block
  availability_zone_count = var.availability_zone_count
  enable_nat_gateway      = var.enable_nat_gateway
  one_nat_gateway_per_az  = var.one_nat_gateway_per_az
  enable_bedrock_endpoint = var.llm_provider == "bedrock"
  ingress_cidrs           = var.ingress_cidrs
  app_port                = var.app_port
  enable_flow_logs        = var.enable_flow_logs
  kms_key_arn             = module.security.kms_key_arn
  tags                    = local.tags
}

module "data" {
  source = "../data"

  name                       = var.name
  private_subnet_ids         = module.network.private_subnet_ids
  database_security_group_id = module.network.database_security_group_id
  cache_security_group_id    = module.network.cache_security_group_id
  kms_key_arn                = module.security.kms_key_arn

  db_instance_class           = var.db_instance_class
  db_allocated_storage_gb     = var.db_allocated_storage_gb
  db_max_allocated_storage_gb = var.db_max_allocated_storage_gb
  db_multi_az                 = var.db_multi_az
  backup_retention_days       = var.backup_retention_days
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = var.skip_final_snapshot
  apply_immediately           = var.apply_immediately

  redis_node_type     = var.redis_node_type
  redis_replica_count = var.redis_replica_count

  data_lake_bucket_name = var.data_lake_bucket_name
  upload_retention_days = var.upload_retention_days

  tags = local.tags
}

module "edge" {
  source = "../edge"

  name                 = var.name
  vpc_id               = module.network.vpc_id
  public_subnet_ids    = module.network.public_subnet_ids
  private_subnet_ids   = module.network.private_subnet_ids
  security_group_id    = module.network.alb_security_group_id
  app_port             = var.app_port
  internal             = var.internal_load_balancer
  domain_name          = var.domain_name
  hosted_zone_id       = var.hosted_zone_id
  certificate_arn      = var.certificate_arn
  idle_timeout_seconds = var.alb_idle_timeout_seconds
  deletion_protection  = var.deletion_protection
  access_logs_bucket   = var.alb_access_logs_bucket
  enable_waf           = var.enable_waf
  tags                 = local.tags
}

module "observability" {
  source = "../observability"

  name                          = var.name
  region                        = var.region
  kms_key_arn                   = module.security.kms_key_arn
  log_retention_days            = var.log_retention_days
  alb_arn_suffix                = module.edge.alb_arn_suffix
  target_group_arn_suffix       = module.edge.target_group_arn_suffix
  cluster_name                  = var.name
  app_service_name              = "${var.name}-app"
  worker_service_name           = "${var.name}-worker"
  database_identifier           = module.data.database_identifier
  alarm_topic_arn               = var.alarm_topic_arn
  latency_p95_threshold_seconds = var.latency_p95_threshold_seconds
  tags                          = local.tags
}

module "compute" {
  source = "../compute"

  name   = var.name
  region = var.region
  image  = var.image

  private_subnet_ids     = module.network.private_subnet_ids
  app_security_group_id  = module.network.app_security_group_id
  target_group_arn       = module.edge.target_group_arn
  alb_target_group_label = module.edge.target_group_label

  execution_role_arn = module.security.execution_role_arn
  task_role_arn      = module.security.task_role_arn
  log_group_name     = module.observability.log_group_name
  secret_arns        = module.security.secret_arns
  secret_names       = module.security.secret_names

  cpu_architecture     = var.cpu_architecture
  app_cpu              = var.app_cpu
  app_memory           = var.app_memory
  app_port             = var.app_port
  app_desired_count    = var.app_desired_count
  app_min_count        = var.app_min_count
  app_max_count        = var.app_max_count
  worker_cpu           = var.worker_cpu
  worker_memory        = var.worker_memory
  worker_desired_count = var.worker_desired_count
  worker_min_count     = var.worker_min_count
  worker_max_count     = var.worker_max_count
  container_insights   = var.container_insights
  enable_ecs_exec      = var.enable_ecs_exec
  metrics_namespace    = var.metrics_namespace

  app_mode         = var.app_mode
  tenancy          = var.tenancy
  redis_url        = module.data.redis_url
  data_lake_bucket = module.data.data_lake_bucket

  auth_provider  = var.auth_provider
  auth_issuer    = var.auth_issuer
  auth_client_id = var.auth_client_id

  llm_provider     = var.llm_provider
  llm_model_strong = var.llm_model_strong
  llm_model_fast   = var.llm_model_fast

  finops_mode             = var.finops_mode
  reconcile_tolerance_pct = var.reconcile_tolerance_pct
  guardrails_max_rows     = var.guardrails_max_rows
  allow_adhoc_sql         = var.allow_adhoc_sql

  snowflake_account             = var.snowflake_account
  snowflake_user                = var.snowflake_user
  snowflake_role                = var.snowflake_role
  snowflake_warehouse           = var.snowflake_warehouse
  snowflake_statement_timeout_s = var.snowflake_statement_timeout_s

  extra_environment = var.extra_environment
  tags              = local.tags

  # The ECS service registers targets with the load balancer, which must have
  # a listener in front of the target group before registration is accepted.
  depends_on = [module.edge]
}

module "ci" {
  count  = var.create_ci_resources ? 1 : 0
  source = "../ci"

  name              = var.name
  kms_key_arn       = module.security.kms_key_arn
  repository_names  = var.ecr_repository_names
  github_repository = var.github_repository
  allowed_git_refs  = var.allowed_git_refs

  create_oidc_provider       = var.create_oidc_provider
  existing_oidc_provider_arn = var.existing_oidc_provider_arn

  passable_role_arns = [
    module.security.execution_role_arn,
    module.security.task_role_arn,
  ]

  tags = local.tags
}
