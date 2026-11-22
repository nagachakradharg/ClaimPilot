# Four Fargate services, mirroring docker-compose.yml: web (nginx + static UI, behind the
# ALB), api, mcp (anti-corruption layer) and core-system (mock legacy system).
# Internal names resolve through Cloud Map: <service>.claimpilot.internal.

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "disabled" # extra cost; enable for real use
  }
}

resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "claimpilot.internal"
  vpc  = aws_vpc.main.id
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14
}

# ---- IAM ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pull images, write logs, read the app secrets at task start.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-app-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = compact([aws_secretsmanager_secret.app.arn, var.llm_api_key_secret_arn])
    }]
  })
}

# Task role: what the running app may do (only mount its EFS access point).
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task_efs" {
  name = "efs-client"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
      Resource = aws_efs_file_system.storage.arn
      Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.storage.arn } }
    }]
  })
}

# ---- task definitions --------------------------------------------------------------
locals {
  backend_image = "${aws_ecr_repository.repo["backend"].repository_url}:${var.image_tag}"
  web_image     = "${aws_ecr_repository.repo["web"].repository_url}:${var.image_tag}"
  ns            = aws_service_discovery_private_dns_namespace.internal.name

  backend_env = [
    { name = "ENV", value = var.env },
    { name = "LLM_MODE", value = var.llm_mode },
    { name = "LLM_DEFAULT_MODEL", value = var.llm_default_model },
    { name = "MCP_SERVER_URL", value = "http://mcp.${local.ns}:8200/mcp" },
    { name = "CORE_SYSTEM_URL", value = "http://core-system.${local.ns}:8100" },
    { name = "PHOTO_STORAGE_DIR", value = "/app/storage/photos" },
    { name = "CORE_CLAIMS_FILE", value = "/app/storage/core_system/claims.json" },
    { name = "CORS_ORIGINS", value = "[]" }, # same origin through nginx
  ]
  backend_secrets = concat(
    [for k in ["DATABASE_URL", "JWT_SECRET", "DEMO_PASSWORD"] :
    { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }],
    var.llm_api_key_secret_arn == "" ? [] :
    [{ name = var.llm_api_key_env_name, valueFrom = var.llm_api_key_secret_arn }],
  )

  services = {
    api = {
      cpu = 1024, memory = 2048, port = 8000
      command = null # image default: alembic upgrade head && uvicorn
    }
    mcp = {
      cpu = 256, memory = 512, port = 8200
      command = ["python", "-m", "claimpilot.mcp_server", "--port", "8200"]
    }
    core-system = {
      cpu = 256, memory = 512, port = 8100
      command = ["uvicorn", "claimpilot.core_system.app:app", "--host", "0.0.0.0", "--port", "8100"]
    }
  }
}

resource "aws_ecs_task_definition" "backend" {
  for_each                 = local.services
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64" # build images with --platform linux/arm64
    operating_system_family = "LINUX"
  }

  volume {
    name = "storage"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.storage.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.storage.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name         = each.key
    image        = local.backend_image
    essential    = true
    command      = each.value.command
    portMappings = [{ containerPort = each.value.port, name = each.key }]
    environment  = local.backend_env
    secrets      = local.backend_secrets
    mountPoints  = [{ sourceVolume = "storage", containerPath = "/app/storage" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name         = "web"
    image        = local.web_image
    essential    = true
    portMappings = [{ containerPort = 80 }]
    environment = [
      { name = "API_UPSTREAM", value = "http://api.${local.ns}:8000" },
      { name = "DNS_RESOLVER", value = cidrhost(var.vpc_cidr, 2) }, # VPC DNS
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "web"
      }
    }
  }])
}

# ---- services ----------------------------------------------------------------------
resource "aws_service_discovery_service" "backend" {
  for_each = local.services
  name     = each.key
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
  health_check_custom_config { failure_threshold = 1 }
}

resource "aws_ecs_service" "backend" {
  for_each        = local.services
  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend[each.key].arn
  desired_count   = 1 # single replica: in-process SSE fan-out assumes one API instance
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true # no NAT gateway; SGs block inbound
  }
  service_registries { registry_arn = aws_service_discovery_service.backend[each.key].arn }
  depends_on = [aws_efs_mount_target.storage]
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 80
  }
  depends_on = [aws_lb_listener.http]
}
