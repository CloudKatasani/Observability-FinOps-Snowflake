# AWS cost

What it costs to run the deployment `deploy/terraform` actually creates.

| | |
|---|---|
| **Region** | `eu-west-1` (Ireland) — the default of `var.region` in both `envs/dev` and `envs/prod` |
| **Pricing date** | 2026-08-24 |
| **Source** | The AWS Price List bulk API, `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<service>/current/eu-west-1/`, read directly. Offer publication dates are in [§5](#5-unit-prices-used) |
| **Basis** | On-demand, no Savings Plans, no Reserved Instances, 730 hours per month |
| **Excluded** | LLM tokens (Anthropic or Bedrock), the customer's Snowflake credits, internet data transfer out, Route 53 hosted zones, RDS backup storage, and ECS Container Insights — see [§4](#4-what-is-excluded-and-why) |

Every instance size below is read from the Terraform, not assumed. Where a size
is variable-driven the default in `variables.tf` is used and named.

---

## 1. What the modules create

| Module | Resources that cost money |
|---|---|
| `network` | NAT gateway(s) with Elastic IPs, seven interface VPC endpoints (`ecr.api`, `ecr.dkr`, `secretsmanager`, `logs`, `monitoring`, `sts`, `ssm`, plus `bedrock-runtime` only when `llm_provider = "bedrock"`), an S3 gateway endpoint (free), VPC flow logs to CloudWatch |
| `edge` | Application Load Balancer, ACM certificate (free), Route 53 record, optional WAFv2 web ACL |
| `compute` | ECS Fargate — two services from one image, ARM64 (`cpu_architecture` default `"ARM64"`), plus Container Insights `enhanced` when `container_insights = true` (the default) |
| `data` | RDS PostgreSQL 16 (gp3 storage), ElastiCache Redis 7.1 replication group, the S3 data-lake bucket |
| `security` | One customer-managed KMS key, Secrets Manager secret containers |
| `observability` | One CloudWatch log group, seven metric alarms, one dashboard |
| `ci` | ECR repositories (dev only, `create_ci_resources = true`), GitHub OIDC role (free) |

---

## 2. Small profile — `envs/dev`

Sizing from `deploy/terraform/envs/dev/main.tf`: `db.t4g.small`, no Multi-AZ,
`cache.t4g.micro` with no replica, app and worker at 512 CPU units / 1024 MB with
`desired_count = 1`. Network defaults from `deploy/terraform/modules/platform/variables.tf`:
2 availability zones, NAT on, one NAT gateway (not one per AZ).

| Line | Basis | $/month |
|---|---|---|
| Fargate — app, 1 task | 0.5 vCPU + 1 GB, ARM | 14.42 |
| Fargate — worker, 1 task | 0.5 vCPU + 1 GB, ARM | 14.42 |
| RDS `db.t4g.small`, Single-AZ | 730 h | 25.55 |
| RDS gp3 storage | 20 GB (`db_allocated_storage_gb`) | 2.54 |
| ElastiCache `cache.t4g.micro` × 1 | 730 h | 12.41 |
| ALB — hours | 730 h | 18.40 |
| ALB — capacity units | 1 LCU assumed | 5.84 |
| NAT gateway × 1 — hours | 730 h | 35.04 |
| NAT gateway — data processed | 20 GB assumed | 0.96 |
| **Interface VPC endpoints** | 7 endpoints × 2 AZs × 730 h | **112.42** |
| Endpoint data processed | 20 GB assumed | 0.20 |
| CloudWatch alarms | 7 × $0.10 | 0.70 |
| CloudWatch Logs — ingest | 7 GB assumed (app + flow logs) | 3.99 |
| CloudWatch Logs — storage | 7 GB, 30-day retention | 0.21 |
| Secrets Manager | 2 secrets | 0.80 |
| KMS | 1 customer-managed key | 1.00 |
| S3 data lake | 10 GB assumed | 0.23 |
| ECR | 5 GB assumed | 0.50 |
| **Total** | | **≈ $250** |

The four rows marked with assumed volumes (NAT data, endpoint data, log ingest,
S3, ECR) are the only ones that are not fixed by the Terraform. They come to under
$6 combined, so the figure is dominated by fixed hourly charges.

**$148 of that $250 — 59% — is NAT gateway plus interface endpoints.** That is the
single most important thing to know about this deployment's bill, and it is the
subject of [§6](#6-levers).

BUILD_PROMPT §20.3 sets an envelope of $120–180/month for this profile. The
compute and data tiers land inside it ($101 for everything except networking); the
network tier is what puts the total above it, because the module provisions NAT
*and* the full endpoint set at once.

---

## 3. Standard profile — `envs/prod`

Sizing from `deploy/terraform/envs/prod/main.tf` and its `variables.tf` defaults:
`db.t4g.medium` Multi-AZ with 50 GB, `cache.t4g.small` with one replica, app and
worker at 1024 CPU units / 2048 MB, `app_min_count = 2`, `worker_min_count = 1`,
3 availability zones, `one_nat_gateway_per_az = true`, 365-day log retention.

| Line | Basis | $/month |
|---|---|---|
| Fargate — app, 2 tasks | 1 vCPU + 2 GB each, ARM | 57.67 |
| Fargate — worker, 1 task | 1 vCPU + 2 GB, ARM | 28.84 |
| RDS `db.t4g.medium`, **Multi-AZ** | 730 h | 100.74 |
| RDS gp3 storage, Multi-AZ | 50 GB | 12.70 |
| ElastiCache `cache.t4g.small` × 2 | primary + replica | 49.64 |
| ALB — hours | 730 h | 18.40 |
| ALB — capacity units | 3 LCU assumed | 17.52 |
| **NAT gateways × 3** | one per AZ, 730 h | **105.12** |
| NAT — data processed | 100 GB assumed | 4.80 |
| **Interface VPC endpoints** | 7 endpoints × 3 AZs × 730 h | **168.63** |
| Endpoint data processed | 100 GB assumed | 1.00 |
| CloudWatch alarms | 7 × $0.10 | 0.70 |
| CloudWatch Logs — ingest | 30 GB/month assumed | 17.10 |
| CloudWatch Logs — storage | ~360 GB at steady state, 365-day retention | 10.80 |
| Secrets Manager | 3 secrets | 1.20 |
| KMS | 1 customer-managed key | 1.00 |
| S3 data lake | 100 GB assumed | 2.30 |
| **Total** | | **≈ $600** |

BUILD_PROMPT §20.3's envelope for this profile is $400–600/month. The estimate
lands at the top of it, again because of the network tier: $280 of the $600 is NAT
gateways and interface endpoints, both multiplied by the third availability zone.

WAF is **off** in this profile: `enable_waf = !var.internal_load_balancer` and
`internal_load_balancer` defaults to `true`. Turning it on costs $5.00 per web ACL
plus $1.00 per rule — the module defines three rules — plus $0.60 per million
requests: about **$8/month** before traffic.

---

## 4. What is excluded, and why

| Excluded | Why |
|---|---|
| **LLM tokens** | Priced per token by the provider and entirely usage-driven. `LLM__PROVIDER=none` is the default in both environments, which costs nothing and still answers grounded questions |
| **Snowflake credits** | The customer's own bill. The platform's own consumption is bounded by `WH_SNOWOBS_APP` (XSMALL, auto-suspend 60 s) under a notify-only 50-credit monthly resource monitor, and is reported as the `cost.platform_self_cost` KPI |
| **ECS Container Insights** | `container_insights = true` sets the cluster to `enhanced`, which is billed as observability usage against task and container counts. It is small at these task counts but genuinely usage-priced; measure it rather than estimate it |
| **RDS backup storage** | Billed per GB-month and driven by the retention window and the change rate |
| **Internet data transfer out** | The load balancer is internal by default, so egress to the internet is not the normal path |
| **Route 53** | A hosted zone is $0.50/month plus queries, and most deployments reuse an existing zone. ACM certificates are free |
| **Terraform state** | An S3 bucket and a DynamoDB table, both effectively free at this size |

---

## 5. Unit prices used

All eu-west-1, read from the AWS Price List bulk API on 2026-08-24.

| Unit | Price | Offer file publication |
|---|---|---|
| Fargate vCPU-hour, ARM | $0.03238 | `AmazonECS` 20260707160651 |
| Fargate GB-hour, ARM | $0.00356 | `AmazonECS` 20260707160651 |
| Fargate vCPU-hour, x86 (for comparison) | $0.04048 | same |
| Fargate GB-hour, x86 (for comparison) | $0.004445 | same |
| NAT gateway hour | $0.048 | `AmazonEC2` 20260821020257 |
| NAT gateway GB processed | $0.048 | same |
| Interface VPC endpoint, per endpoint per AZ per hour | $0.011 | `AmazonVPC` 20260724154225 |
| Interface VPC endpoint GB processed | $0.010 | same |
| ALB hour | $0.0252 | `AWSELB` 20260818181726 |
| ALB LCU-hour | $0.008 | same |
| RDS `db.t4g.small` PostgreSQL, Single-AZ | $0.035/h | `AmazonRDS` 20260820203529 |
| RDS `db.t4g.small` PostgreSQL, Multi-AZ | $0.069/h | same |
| RDS `db.t4g.medium` PostgreSQL, Single-AZ | $0.069/h | same |
| RDS `db.t4g.medium` PostgreSQL, Multi-AZ | $0.138/h | same |
| RDS gp3 storage, Single-AZ | $0.127/GB-month | same |
| RDS gp3 storage, Multi-AZ | $0.254/GB-month | same |
| ElastiCache `cache.t4g.micro`, Redis | $0.017/h | `AmazonElastiCache` 20260821071526 |
| ElastiCache `cache.t4g.small`, Redis | $0.034/h | same |
| S3 Standard, first 50 TB | $0.023/GB-month | `AmazonS3` published 2026-08-18 |
| S3 Standard-IA | $0.0125/GB-month | same |
| CloudWatch Logs ingest, standard class | $0.57/GB | `AmazonCloudWatch` published 2026-08-06 |
| CloudWatch Logs storage | $0.03/GB-month | same |
| CloudWatch alarm, standard resolution | $0.10/month | same |
| Secrets Manager secret | $0.40/month | `AWSSecretsManager` published 2025-08-28 |
| KMS customer-managed key | $1.00/month | `awskms` published 2025-08-28 |
| ECR storage | $0.10/GB-month | `AmazonECR` published 2025-11-21 |
| WAF web ACL / rule / million requests | $5.00 / $1.00 / $0.60 | `awswaf` published 2026-01-07 |

Re-derive any of these with:

```bash
curl -s "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/region_index.json" \
  | jq -r '.regions["eu-west-1"].currentVersionUrl'
```

then fetch that path under the same host and read `terms.OnDemand`.

---

## 6. Levers

In descending order of effect on this deployment specifically.

### 1. Choose NAT **or** the interface endpoints — not both

The network module gives you a full private-egress path (seven interface
endpoints, priced per endpoint *per availability zone*) **and** a NAT gateway. Each
alone is a coherent design; both together is $148/month in the Small profile and
$280 in Standard.

- **Zero-egress** (`enable_nat_gateway = false`): drops $36/month in Small and
  $110 in Standard, and is the stronger security posture — the workload has no
  route to the internet at all. It requires Snowflake PrivateLink, and Bedrock
  rather than the Anthropic API if you want an LLM.
- **NAT-only**: cheaper still at two AZs, but the endpoint set is a `local` in
  `deploy/terraform/modules/network/main.tf` rather than a variable, so trimming it is a code
  change today, not a `tfvars` edit. That is worth fixing before it costs anyone a
  year of endpoint hours.

### 2. Do not spread NAT across every AZ unless you need to

`one_nat_gateway_per_az = true` in prod triples the NAT bill to $105/month. One
NAT gateway is a single-AZ failure domain for egress; that is a real availability
trade-off, but it should be a decision, not a default you inherited.

### 3. Drop the third availability zone

`availability_zone_count = 3` in prod multiplies both the endpoint count and the
NAT count. Two AZs still satisfies Multi-AZ RDS and an ALB (the module's own
validation rule enforces a minimum of two). Going from 3 to 2 saves roughly $56 of
endpoints and $35 of NAT.

### 4. Right-size RDS before anything else in the data tier

Multi-AZ doubles both the instance rate and the storage rate — $113/month of the
Standard profile. Postgres here holds **app metadata only**; telemetry never lands
in it (R2). Sizing this database as though it were a warehouse is the most common
way to overspend on this platform. `db.t4g.small` Single-AZ is $28/month all in.

### 5. Keep ARM64

`cpu_architecture` defaults to `"ARM64"`, which is 20% cheaper per vCPU-hour than
x86 ($0.03238 vs $0.04048) and 20% cheaper per GB-hour. The image builds for both;
switching to x86 costs money for nothing.

### 6. Scale the floor, not the ceiling

`app_max_count` costs nothing until it is used — autoscaling only bills for running
tasks. `app_min_count = 2` in prod costs $29/month for the second task, and it buys
zero-downtime deploys and AZ redundancy. Lower it only for a non-production
environment.

### 7. Log retention is a compliance decision with a price

`log_retention_days = 365` in prod. At 30 GB/month ingest, stored logs reach about
360 GB and $11/month at steady state; the ingest itself is $17/month. Halving
retention halves the storage half. The variable's own description says this is a
compliance decision — decide it as one, then pay for it deliberately.

### 8. Turn Container Insights off if you are not reading it

`container_insights = true` sets the cluster to `enhanced`, which is billed as
usage. Two services and a handful of tasks make it cheap, but "cheap and unread"
is still waste — the same argument this platform makes about warehouses.

### 9. Consider a Compute Savings Plan for Fargate

Fargate is covered by Compute Savings Plans. At $86/month of Fargate in the
Standard profile the absolute saving is modest, but it applies to the one line that
grows with adoption.

### 10. ElastiCache replica

`redis_replica_count = 1` in prod doubles the cache to $50/month and enables
automatic failover and Multi-AZ. Redis here holds the arq queue and a shared cache
— neither is a system of record — so this is an availability choice rather than a
durability one.
