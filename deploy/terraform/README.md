# Terraform — AWS and Snowflake

Two independent root modules, applied by different people at different times:

| Directory | What it creates | Who applies it |
|---|---|---|
| `envs/dev`, `envs/prod` | The AWS deployment: VPC, ALB, ECS Fargate, RDS, ElastiCache, S3, KMS, Secrets Manager, CloudWatch, ECR + the GitHub OIDC role | Whoever owns the AWS account |
| `snowflake/` | The read-only Snowflake role, its granular database-role grants, and the platform's own small warehouse | Whoever holds `ACCOUNTADMIN` in the Snowflake account |

They are deliberately not one apply. The AWS side is the vendor's deployment;
the Snowflake side is a change to the customer's warehouse, reviewed and applied
by the customer's own administrator (R8).

---

## Why ECS Fargate, and not App Runner

App Runner is the shorter path to "a container behind HTTPS", and for a
single stateless web service it would be the right answer. It is the wrong
answer here, for four reasons that are all about this application specifically:

1. **There are two workloads, and one of them has no listener.** The arq worker
   runs refreshes, reconciliation, the close, forecasts, and alert evaluation.
   App Runner services are request-driven and scale on request concurrency; a
   long-running consumer with no HTTP surface has no home there. It would have
   to become a Fargate task anyway, at which point the cluster exists and the
   simplification is gone.
2. **Egress control is the security conversation.** This deployment holds a
   read-only credential to the customer's data warehouse. Security review asks
   where it can send traffic, and the answer has to be specific: private
   subnets, a named set of VPC endpoints, and either NAT with an allowlist or
   PrivateLink to Snowflake and no internet route at all. ECS in a VPC we own
   answers that precisely. App Runner's VPC connector covers outbound but
   leaves the ingress path and its own service networking outside our control.
3. **Least privilege needs two identities.** The API and the worker have
   different jobs and, in a hardened deployment, different task roles. ECS gives
   each service its own role and its own security-group membership.
4. **The rollback story has to be describable.** ECS deployment circuit breakers
   roll a failed release back automatically, on the same primitive the runbook
   documents, and `aws ecs update-service --task-definition <previous>` is a
   rollback anyone can execute under pressure.

The cost difference is small at these sizes — see [`../../docs/AWS_COST.md`](../../docs/AWS_COST.md).
The control difference is not.

### Why two ECS services and not three

BUILD_PROMPT §20.1 sketches three services: `web` (nginx serving the SPA), `api`,
and `worker`. This implementation runs two, both from the **same** all-in-one
image with different commands:

- `app` — `snowobs-allinone api`: the FastAPI application with the built SPA
  mounted at `/` (see `deploy/docker/allinone/asgi.py`), on port 8080.
- `worker` — `snowobs-allinone worker`: the arq consumer.

The SPA is a static bundle that the API can serve on the same origin. A separate
nginx task adds a network hop, a second base image to patch, a CORS or proxy
configuration to get wrong, and about a third of the compute bill — and buys
nothing, because the ALB already terminates TLS and does the routing. The
three-image topology still exists for anyone who wants it
(`deploy/docker/Dockerfile.{api,worker,web}` and `deploy/compose/docker-compose.yml`);
it is simply not what the AWS modules deploy.

---

## Layout

```
deploy/terraform/
├── modules/
│   ├── platform/       composition — everything below, wired together
│   ├── network/        VPC, subnets, NAT, VPC endpoints, security groups, flow logs
│   ├── security/       KMS, IAM task + execution roles, Secrets Manager containers
│   ├── data/           RDS Postgres, ElastiCache Redis, the S3 data lake
│   ├── compute/        ECS cluster, task definitions, services, autoscaling
│   ├── edge/           ALB, ACM, Route 53, optional WAF
│   ├── observability/  log groups, alarms, dashboard
│   └── ci/             ECR repositories, GitHub OIDC deploy role
├── envs/dev/           Small profile, destroyable, ECS Exec on, owns ECR + OIDC
├── envs/prod/          Standard profile, Multi-AZ, deletion protection, no shell
└── snowflake/          the read-only role and its generated grants
```

---

## First apply

```bash
cd deploy/terraform/envs/dev
cp backend.hcl.example backend.hcl          # edit: your state bucket and lock table
cp terraform.tfvars.example terraform.tfvars # edit: domain, bucket, repo, region

terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                   # read it
terraform apply tfplan
```

Then populate the secret containers Terraform created empty (see
`terraform output secret_names`) and roll the service. The exact commands are in
[`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md#first-deploy).

`terraform apply` on its own does not produce a *working* deployment: the
secrets are empty by design, and the app cannot connect to its database until
`…/app/database-url` holds a value. That is the price of never putting a secret
in state, and it is the right price.

---

## Secrets: what is where, and why

**No secret value is ever a Terraform input, output, or state attribute (§27.13).**

| Secret | Created by | Value written by |
|---|---|---|
| RDS master password | RDS itself (`manage_master_user_password`) | AWS, rotated by AWS |
| `<name>/app/database-url` | Terraform (empty container) | An operator, composed from the RDS-managed secret |
| `<name>/snowflake/private-key` | Terraform (empty container) | An operator, from the key pair they generated |
| `<name>/llm/api-key` | Terraform (empty container), only when `llm_provider = "anthropic"` | An operator |
| Redis | — | No auth token: it would be a secret value in state. In-transit encryption is on and the cache is reachable only from the app security group. |

The task definition references secrets **by ARN**; the ECS agent resolves them at
container start. `aws ecs describe-task-definition` shows the ARN, never the
value.

---

## The Snowflake module

```bash
cd deploy/terraform/snowflake
export SNOWFLAKE_ORGANIZATION_NAME=... SNOWFLAKE_ACCOUNT_NAME=... \
       SNOWFLAKE_USER=... SNOWFLAKE_ROLE=ACCOUNTADMIN
terraform init
terraform plan     # read every grant before applying (R8)
terraform apply
```

`grants.auto.tfvars.json` is **generated**, never hand-written:

```bash
make provisioning   # regenerates the tfvars and snowflake/provisioning/*.sql
```

It derives the database-role list from the same source registry the application
reads (`packages/semantics/sources/*.yaml`), so the module asks for exactly the
privileges the code uses. `scripts/gen_snowflake_grants.py --check` fails CI when
someone registers a source and forgets to propagate its grant.

The module refuses blanket grants twice over: the generator audits its SQL
output, and `var.database_roles` has a validation rule that rejects any role name
containing `IMPORTED PRIVILEGES`, `ACCOUNTADMIN`, or `SECURITYADMIN` (R4, §27.3).

Organization-scoped roles (`ORGANIZATION_USAGE_VIEWER`,
`ORGANIZATION_BILLING_VIEWER`) only exist in an organization account, so they are
gated behind `grant_organization_roles` (default `false`). Without them the
currency, contract, and organization-wide metering KPIs report
"Unavailable — requires `<view>`" with the remediation grant, rather than zero
(R3). `terraform output skipped_database_roles` lists exactly which.

---

## Verification status

`terraform fmt -check -recursive` passes on this tree.

`terraform validate` has **not** been run: it requires downloading the AWS and
Snowflake providers from `registry.terraform.io`, which the build environment
this was authored in blocks. Module wiring (every `var.*` declared, every
`local.*` declared, every `module.*.<output>` present, every required variable
passed, no unused declarations) was checked mechanically; **provider argument
names and types were not machine-verified**. Run this before your first apply:

```bash
make terraform-validate     # init -backend=false + validate, per environment
```

and treat the first `terraform plan` as a review, not a formality. The
`terraform` job in `.github/workflows/ci.yml` runs both on every change once the
registry is reachable.

---

## Sizing

`envs/dev` is the Small profile and `envs/prod` is the Standard profile from
[`../../docs/AWS_COST.md`](../../docs/AWS_COST.md). Every sizing input is a
variable; the Large profile is a `terraform.tfvars` change, not a code change.

## What is provisioned but not yet consumed

The S3 data lake bucket is created, encrypted, lifecycled, and granted to the
task role, but the application's object-storage adapter is not implemented yet —
today it reads and writes the OFFLINE lake on local disk. AWS deployments
therefore run in **LIVE mode**, where the lake is not on the read path.
`docs/ASSUMPTIONS.md` records this and its revisit trigger. The bucket is still
worth creating with the rest of the stack: it is where exports and uploads land
the moment the adapter exists, and retrofitting the KMS key and lifecycle policy
onto a bucket full of customer telemetry is worse than creating it early.
