# ADR-0005: Cloud target is AWS (ECS Fargate); demo runs on Docker Compose for now

- Status: Accepted
- Date: 2026-11-26

## Context
The requirements leave Azure vs AWS open until week 6 (§8). The system is five containers (web, api, MCP server, mock core system, Postgres + pgvector) plus photo storage. Local inference (Gemma 12B on Ollama) can't be hosted cheaply. The owner wants close to zero spend and chose not to deploy yet.

## Decision
- **Target: AWS.** Infrastructure is Terraform in `infra/aws`: ECS Fargate services that mirror the Compose services, an ALB in front of nginx (`web`), RDS Postgres 17 with pgvector, EFS for photos and the mock core system's file, Secrets Manager, ECR, CloudWatch logs and an AWS Budgets alarm.
- **Not applied yet.** The supported way to run ClaimPilot today is `docker compose up`. The same images deploy unchanged; only environment variables differ.
- Internal service names go through Cloud Map (`api.claimpilot.internal`). nginx re-resolves the API name (Docker DNS locally, VPC DNS on AWS), so restarts and rescheduling don't break the proxy.
- No NAT gateway: tasks run in public subnets with public IPs, and security groups only allow ALB → web and task ↔ task. RDS and EFS stay private. This saves a fixed NAT cost at demo scale.
- The cloud model mode defaults to `fake` ($0). Hosted-model mode is a variable change plus an API-key secret and a provider spend cap.
- Outside `ENV=dev|test`, the app refuses to start with the development JWT secret.

## Alternatives
- **Azure Container Apps + Azure Database for PostgreSQL**: a comparable fit with a monthly free grant for Container Apps. Not chosen; the owner picked AWS.
- **A single VM running Compose**: cheapest, but no managed database, secrets or load balancer. It shows less of what a client pilot would need.
- **App Runner**: simpler, but less control over the internal service mesh this system needs (MCP, core system).

## Consequences
- One API replica, because SSE fan-out is in-process. Scaling out needs Postgres LISTEN/NOTIFY or Redis.
- Photos sit on EFS. A production build should move them to S3 with presigned uploads.
- Terraform has not been run (`terraform validate` is still to be done), so the first apply may need small fixes.
