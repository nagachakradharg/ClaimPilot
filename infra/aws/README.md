# AWS deployment (written, not applied)

Status: the infrastructure is defined here but has **not** been deployed. The demo runs on
Docker Compose. Decision and trade-offs: [ADR-0005](../../docs/adr/0005-cloud-target-aws.md).

## What it creates
| Piece | AWS service | Notes |
|---|---|---|
| web (nginx + React build) | ECS Fargate behind an ALB | only public entry point; proxies `/api` |
| api, mcp, core-system | ECS Fargate, Cloud Map names `*.claimpilot.internal` | same image, different commands (as in Compose) |
| Postgres 17 + pgvector | RDS `db.t4g.micro`, private subnets | migration runs `CREATE EXTENSION vector` |
| photos, mock core claim file | EFS (encrypted, access point uid 10001) | production would use S3 for photos |
| secrets | Secrets Manager | DB URL, JWT secret, generated demo password |
| images | ECR (scan on push, keep last 10) | |
| logs | CloudWatch, 14-day retention | |
| spend alarm | AWS Budgets (80% forecast, 100% actual) | notifies only |

No NAT gateway: tasks sit in public subnets with public IPs, and security groups allow
inbound only ALB → web and task ↔ task. RDS and EFS are private.

## Deploy (when ready)
Prerequisites: an AWS account, the AWS CLI signed in, Terraform ≥ 1.6, Docker.

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars    # set budget_email
terraform init && terraform apply               # ~10 min (RDS is the slow part)
```

Build and push ARM64 images (Apple Silicon builds natively), then roll the services:

```bash
REGION=us-east-1; TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$(terraform output -raw ecr_backend | cut -d/ -f1)"
docker buildx build --platform linux/arm64 -f backend/Dockerfile -t "$(terraform output -raw ecr_backend):$TAG" --push ../..
docker buildx build --platform linux/arm64 -t "$(terraform output -raw ecr_web):$TAG" --push ../../frontend
terraform apply -var image_tag=$TAG
```

Seed once, then open the URL. The demo password is generated; read it from Secrets Manager
(`terraform output demo_password_secret`, key `DEMO_PASSWORD`).

```bash
eval "$(terraform output -raw seed_command)"
terraform output url
```

Tear everything down: `terraform destroy`.

## Models in the cloud
Ollama is not deployed (a 12B vision model needs a GPU instance). `llm_mode` defaults to
`fake` ($0). For real answers set `llm_mode=live`, `llm_default_model` to a hosted
`provider:model`, and `llm_api_key_secret_arn` to a secret holding that provider's key, and
set a hard spend limit at the provider as well as the budget alarm here.

## Rough monthly cost (us-east-1, on-demand, check current pricing)
Dominated by the ALB, four small Fargate tasks running 24/7 and RDS. Expect tens of dollars a
month; new-account free tiers cover part of RDS. Stop it with `terraform destroy` when not demoing.

## Known limits
- One API replica: SSE fan-out is in-process. Scaling out needs Redis/Postgres pub-sub.
- HTTP only unless `certificate_arn` is set.
- Local Terraform state; use an S3 backend with locking for a team.
