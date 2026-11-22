# ---- Postgres 17 with pgvector (the Alembic migration runs CREATE EXTENSION vector) ----
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                 = local.name
  engine                     = "postgres"
  engine_version             = "17"
  instance_class             = var.db_instance_class
  allocated_storage          = 20
  storage_encrypted          = true
  db_name                    = "claimpilot"
  username                   = "claimpilot"
  password                   = random_password.db.result
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.data.id]
  publicly_accessible        = false
  backup_retention_period    = 1
  deletion_protection        = false # demo; set true for anything real
  skip_final_snapshot        = true
  auto_minor_version_upgrade = true
}

# ---- EFS for uploaded photos and the mock core system's claim file --------------------
# (Shared by the api and core-system tasks. A production build would put photos in S3.)
resource "aws_efs_file_system" "storage" {
  encrypted = true
  tags      = { Name = "${local.name}-storage" }
}

resource "aws_efs_mount_target" "storage" {
  count           = 2
  file_system_id  = aws_efs_file_system.storage.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.data.id]
}

resource "aws_efs_access_point" "storage" {
  file_system_id = aws_efs_file_system.storage.id
  posix_user {
    uid = 10001 # the non-root "app" user in the backend image
    gid = 10001
  }
  root_directory {
    path = "/claimpilot"
    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "750"
    }
  }
}
