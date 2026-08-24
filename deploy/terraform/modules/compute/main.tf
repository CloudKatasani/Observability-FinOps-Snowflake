# ECS Fargate: two services from one image.
#
#   app     — `snowobs-allinone api`, serves the API and the built SPA on 8080
#   worker  — `snowobs-allinone worker`, the arq consumer, no listener
#
# One image with two commands rather than three images, because the SPA is a
# static bundle the API can serve on the same origin and a second nginx task
# buys nothing but another thing to patch. See deploy/terraform/README.md for
# the reasoning against the three-service sketch in BUILD_PROMPT §20.1.
#
# Both services run in private subnets with no public IP. The app is reachable
# only through the load balancer's security group; the worker is reachable from
# nothing at all.

locals {
  # Configuration, not secrets. Anything in this map is visible in the task
  # definition to anyone with ecs:DescribeTaskDefinition — which is exactly why
  # DATABASE_URL is not here (§27.13).
  base_environment = merge(
    {
      SNOWOBS_MODE                    = var.app_mode
      SNOWOBS_TENANCY                 = var.tenancy
      SNOWOBS_LOG_JSON                = "true"
      REDIS_URL                       = var.redis_url
      STORAGE__PROVIDER               = "s3"
      STORAGE__BUCKET                 = var.data_lake_bucket
      STORAGE__REGION                 = var.region
      SECRETS__PROVIDER               = "aws"
      AUTH__PROVIDER                  = var.auth_provider
      LLM__PROVIDER                   = var.llm_provider
      FINOPS__MODE                    = var.finops_mode
      FINOPS__RECONCILE_TOLERANCE_PCT = tostring(var.reconcile_tolerance_pct)
      GUARDRAILS__MAX_ROWS            = tostring(var.guardrails_max_rows)
      GUARDRAILS__ALLOW_ADHOC_SQL     = var.allow_adhoc_sql ? "true" : "false"
      SNOWFLAKE__STATEMENT_TIMEOUT_S  = tostring(var.snowflake_statement_timeout_s)
      SNOWFLAKE__QUERY_TAG_PREFIX     = var.snowflake_query_tag_prefix
    },
    var.auth_issuer == null ? {} : { AUTH__ISSUER = var.auth_issuer },
    var.auth_client_id == null ? {} : { AUTH__CLIENT_ID = var.auth_client_id },
    var.llm_model_strong == null ? {} : { LLM__MODEL_STRONG = var.llm_model_strong },
    var.llm_model_fast == null ? {} : { LLM__MODEL_FAST = var.llm_model_fast },
    # The key is named, not injected: SECRETS__PROVIDER is "aws", so the
    # application resolves this ARN through Secrets Manager at the moment of
    # use and the value never becomes an environment variable at all (§27.13).
    # Bedrock authenticates with the task role and needs no key.
    lookup(var.secret_arns, "llm_api_key", null) == null ? {} : {
      LLM__API_KEY_REF = var.secret_arns["llm_api_key"]
    },
    var.snowflake_account == null ? {} : { SNOWFLAKE__ACCOUNT = var.snowflake_account },
    var.snowflake_user == null ? {} : { SNOWFLAKE__USER = var.snowflake_user },
    var.snowflake_role == null ? {} : { SNOWFLAKE__ROLE = var.snowflake_role },
    var.snowflake_warehouse == null ? {} : { SNOWFLAKE__WAREHOUSE = var.snowflake_warehouse },
    # A *reference*, resolved at runtime by the secrets adapter — never the key.
    lookup(var.secret_arns, "snowflake_private_key", null) == null ? {} : {
      SNOWFLAKE__PRIVATE_KEY_REF = var.secret_names["snowflake_private_key"]
    },
    var.extra_environment,
  )

  # Injected by the ECS agent at container start from Secrets Manager. Values
  # never appear in the task definition, in Terraform state, or in a log line.
  #
  # Only DATABASE_URL is injected this way, because SQLAlchemy needs the whole
  # URL before anything else starts. The LLM key is not here on purpose: it is
  # passed by ARN in LLM__API_KEY_REF above and read through the secrets
  # adapter when a turn actually needs it, so it never exists as an environment
  # variable a crash dump or a subprocess could pick up.
  base_secrets = [
    { name = "DATABASE_URL", valueFrom = var.secret_arns["app_database_url"] }
  ]

  environment_list = [for k, v in local.base_environment : { name = k, value = v }]
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enhanced" : "disabled"
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ── app ─────────────────────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = "app"
    image     = var.image
    command   = ["api"]
    essential = true

    portMappings = [{
      containerPort = var.app_port
      protocol      = "tcp"
      name          = "http"
    }]

    environment = local.environment_list
    secrets     = local.base_secrets

    # The image's own HEALTHCHECK covers liveness; ECS repeats it so a wedged
    # task is replaced without waiting for the load balancer to notice.
    healthCheck = {
      command  = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.app_port}/healthz', timeout=2).status==200 else 1)\""]
      interval = 15
      timeout  = 5
      retries  = 3
      # The SPA bundle and the semantic model both load at import time.
      startPeriod = 30
    }

    # Only the lake is writable, and it is S3, not the filesystem. A read-only
    # root filesystem means an RCE cannot persist anything (§17).
    readonlyRootFilesystem = true
    mountPoints = [{
      sourceVolume  = "tmp"
      containerPath = "/tmp"
      readOnly      = false
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])

  volume {
    name = "tmp"
  }

  tags = var.tags
}

resource "aws_ecs_service" "app" {
  name            = "${var.name}-app"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.app_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_ecs_exec
  propagate_tags         = "SERVICE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = var.app_port
  }

  # Blue/green at the task level: a new revision rolls out beside the old one
  # and the circuit breaker rolls back automatically if the new tasks never
  # pass their health checks. This is what makes `release.yml` safe to run
  # without a human watching the deploy.
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Give the tasks time to warm the semantic model before the ALB judges them.
  health_check_grace_period_seconds = var.health_check_grace_period_seconds

  lifecycle {
    # The pipeline updates the image; Terraform owns the shape, not the tag.
    ignore_changes = [task_definition]
  }

  tags = var.tags
}

# ── worker ──────────────────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.image
    command   = ["worker"]
    essential = true

    environment = local.environment_list
    secrets     = local.base_secrets

    readonlyRootFilesystem = true
    mountPoints = [{
      sourceVolume  = "tmp"
      containerPath = "/tmp"
      readOnly      = false
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  volume {
    name = "tmp"
  }

  tags = var.tags
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_ecs_exec
  propagate_tags         = "SERVICE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = var.tags
}

# ── autoscaling ─────────────────────────────────────────────────────────────
resource "aws_appautoscaling_target" "app" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.app_min_count
  max_capacity       = var.app_max_count
}

resource "aws_appautoscaling_policy" "app_cpu" {
  name               = "${var.name}-app-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = var.app_target_cpu_percent
    # Scale out promptly, scale in slowly: a dashboard tile that times out
    # costs more than a minute of an extra task.
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "app_requests" {
  count = var.alb_target_group_label == null ? 0 : 1

  name               = "${var.name}-app-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = var.alb_target_group_label
    }
    target_value       = var.app_target_requests_per_task
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.worker_min_count
  max_capacity       = var.worker_max_count
}

# Worker scaling. CPU target-tracking is the default because it works with what
# the platform publishes today.
#
# Queue depth is the better signal — an arq worker blocked on a Snowflake query
# is idle on CPU while the backlog grows — but a target-tracking policy on a
# metric nobody publishes sits in INSUFFICIENT_DATA and silently never scales,
# which is worse than a blunt policy that works. So it is opt-in, and turning it
# on requires the application to emit `queue_depth` into var.metrics_namespace
# (see docs/ASSUMPTIONS.md, application self-telemetry).
resource "aws_appautoscaling_policy" "worker_cpu" {
  count = var.enable_queue_depth_scaling ? 0 : 1

  name               = "${var.name}-worker-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.worker_target_cpu_percent
    scale_in_cooldown  = 600
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "worker_queue_depth" {
  count = var.enable_queue_depth_scaling ? 1 : 0

  name               = "${var.name}-worker-queue-depth"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    customized_metric_specification {
      metric_name = "queue_depth"
      namespace   = var.metrics_namespace
      statistic   = "Average"

      dimensions {
        name  = "service"
        value = "${var.name}-worker"
      }
    }

    target_value       = var.worker_target_queue_depth
    scale_in_cooldown  = 600
    scale_out_cooldown = 60
  }
}
