output "url" {
  value = "http://${aws_lb.main.dns_name}"
}

output "ecr_backend" {
  value = aws_ecr_repository.repo["backend"].repository_url
}

output "ecr_web" {
  value = aws_ecr_repository.repo["web"].repository_url
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "seed_command" {
  description = "One-off task that seeds demo data (run once after the first deploy)."
  value = join(" ", [
    "aws ecs run-task --cluster", aws_ecs_cluster.main.name,
    "--launch-type FARGATE --task-definition", aws_ecs_task_definition.backend["api"].family,
    "--network-configuration 'awsvpcConfiguration={subnets=[${aws_subnet.public[0].id}],securityGroups=[${aws_security_group.tasks.id}],assignPublicIp=ENABLED}'",
    "--overrides '{\"containerOverrides\":[{\"name\":\"api\",\"command\":[\"python\",\"-m\",\"claimpilot.seed\"]}]}'",
  ])
}

output "demo_password_secret" {
  description = "Secrets Manager entry holding the generated demo password (key DEMO_PASSWORD)."
  value       = aws_secretsmanager_secret.app.name
}
