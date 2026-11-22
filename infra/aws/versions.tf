# ClaimPilot on AWS (ADR-0005). Written and reviewed, NOT applied: the demo runs on Docker
# Compose for now. See README.md in this folder before running anything here.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # For a team: add an S3 backend with state locking here.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "claimpilot", Environment = var.env, ManagedBy = "terraform" }
  }
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}

locals {
  name = "claimpilot-${var.env}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}
