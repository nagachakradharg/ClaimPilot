# Secrets live in Secrets Manager and are injected into tasks; nothing secret is in images
# or task definitions in plain text.
resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "random_password" "demo" {
  length  = 20
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL  = "postgresql+psycopg://claimpilot:${random_password.db.result}@${aws_db_instance.main.address}:5432/claimpilot"
    JWT_SECRET    = random_password.jwt.result
    DEMO_PASSWORD = random_password.demo.result
  })
}
