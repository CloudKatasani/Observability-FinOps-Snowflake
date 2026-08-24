# Observability & FinOps Platform for Snowflake — developer entry points.
# Targets for later phases (demo, test-parity, eval, migrate, seed,
# generate-fixtures, scan) are added with the phase that implements them.

COMPOSE := docker compose -f deploy/compose/docker-compose.yml

.PHONY: dev infra infra-down test test-python test-web lint fmt typecheck build clean

dev: ## Infra containers + API/worker/web with hot reload
	./scripts/dev.sh

infra: ## Start postgres, redis, minio only
	$(COMPOSE) up -d --wait postgres redis minio minio-init

infra-down: ## Stop and remove infrastructure containers (volumes preserved)
	$(COMPOSE) down

test: test-python test-web

test-python:
	uv run pytest

test-web:
	cd apps/web && npm test

lint:
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run lint

fmt:
	uv run ruff check . --fix
	uv run ruff format .

typecheck:
	uv run mypy packages apps
	cd apps/web && npm run typecheck

build: ## Build all container images
	docker build -f deploy/docker/Dockerfile.api -t snowobs-api:dev .
	docker build -f deploy/docker/Dockerfile.worker -t snowobs-worker:dev .
	docker build -f deploy/docker/Dockerfile.web -t snowobs-web:dev .

clean:
	rm -rf apps/web/dist .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
