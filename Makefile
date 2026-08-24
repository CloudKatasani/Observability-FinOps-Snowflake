# Observability & FinOps Platform for Snowflake — developer and operator entry points.
#
# `make help` lists every target. The two that matter on a first visit:
#   make doctor   — will this machine run it, and if not, what is wrong?
#   make demo     — the whole platform on synthetic data, one command.

COMPOSE := docker compose -f deploy/compose/docker-compose.yml
DEMO_COMPOSE := docker compose -f docker-compose.demo.yml
IMAGE ?= snowobs
TAG ?= dev

.PHONY: help dev demo demo-native demo-down doctor infra infra-down \
        test test-python test-web test-parity eval lint fmt typecheck \
        seed generate-fixtures build build-allinone scan sbom \
        catalog contracts provisioning terraform-fmt terraform-validate clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ───────────────────────────────────────────────────────────────── running it
dev: ## Infra containers + API/worker/web with hot reload
	./scripts/dev.sh

demo: ## Whole platform on synthetic data, in containers, at http://localhost:8080
	./scripts/demo.sh

demo-native: ## The same demo with host processes — no Docker required
	./scripts/demo.sh --native

demo-down: ## Stop the demo stack and delete its volumes (including the fixture lake)
	$(DEMO_COMPOSE) down -v

doctor: ## Check ports, Docker resources, and configuration before a first run
	uv run python scripts/doctor.py

infra: ## Start postgres, redis, minio only
	$(COMPOSE) up -d --wait postgres redis minio minio-init

infra-down: ## Stop and remove infrastructure containers (volumes preserved)
	$(COMPOSE) down

# ──────────────────────────────────────────────────────────────────── quality
test: test-python test-web ## Python + web unit tests

test-python:
	uv run pytest

test-parity: ## Dual-engine parity + golden SQL snapshots (gates merges, R1)
	uv run pytest packages/engines -q

test-web:
	cd apps/web && npm test

eval: ## Agent golden-question suite; exits non-zero if a §12.6 gate fails
	uv run python -m snowobs_agents.evals.runner

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint

fmt:
	uv run ruff check . --fix
	uv run ruff format .

typecheck:
	uv run mypy packages apps fixtures
	cd apps/web && npm run typecheck

# ───────────────────────────────────────────────────────────────────── data
generate-fixtures: ## Write a synthetic account to fixtures/generated/ (no ingestion)
	uv run snowobs-generate --out fixtures/generated --format csv

seed: ## Generate and ingest the demo dataset into the local OFFLINE lake (.data)
	uv run python scripts/demo_seed.py --root .data

# ─────────────────────────────────────────────────────────────────── artefacts
catalog: ## Regenerate docs/KPI_CATALOG.md from the metric YAML
	uv run python -m snowobs_semantics.docgen

contracts: ## Regenerate docs/DATA_CONTRACTS.md from the data product YAML
	uv run python -m snowobs_dataproducts.docgen

provisioning: ## Regenerate the Snowflake grant SQL and Terraform from the source registry (R4)
	uv run python scripts/gen_snowflake_grants.py

build: ## Build the three service images (the shape ECS runs)
	docker build -f deploy/docker/Dockerfile.api -t $(IMAGE)-api:$(TAG) .
	docker build -f deploy/docker/Dockerfile.worker -t $(IMAGE)-worker:$(TAG) .
	docker build -f deploy/docker/Dockerfile.web -t $(IMAGE)-web:$(TAG) .

build-allinone: ## Build the single-container image (API + worker + SPA on one port)
	docker build -f Dockerfile.allinone -t $(IMAGE):$(TAG) .

scan: build-allinone ## Trivy scan of the all-in-one image; fails on HIGH/CRITICAL
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  aquasec/trivy:latest image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed \
	  $(IMAGE):$(TAG)

sbom: build-allinone ## CycloneDX SBOM for the all-in-one image
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  aquasec/trivy:latest image --format cyclonedx --output /dev/stdout $(IMAGE):$(TAG) \
	  > sbom.cyclonedx.json
	@echo "Wrote sbom.cyclonedx.json"

# ──────────────────────────────────────────────────────────────────── infra
terraform-fmt: ## Check Terraform formatting
	terraform -chdir=deploy/terraform fmt -check -recursive

terraform-validate: ## Validate every Terraform environment
	terraform -chdir=deploy/terraform/envs/dev init -backend=false
	terraform -chdir=deploy/terraform/envs/dev validate
	terraform -chdir=deploy/terraform/envs/prod init -backend=false
	terraform -chdir=deploy/terraform/envs/prod validate
	terraform -chdir=deploy/terraform/snowflake init -backend=false
	terraform -chdir=deploy/terraform/snowflake validate

clean:
	rm -rf apps/web/dist .pytest_cache .mypy_cache .ruff_cache sbom.cyclonedx.json
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
