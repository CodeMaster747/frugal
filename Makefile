.DEFAULT_GOAL := help
.PHONY: help up down logs build migrate revision downgrade seed shell test test-unit eval e2e \
        lint format types check check-backend check-frontend clean frontend-dev frontend-install

COMPOSE := docker compose
API     := $(COMPOSE) exec -T api

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- stack -------------------------------------------------------------------

up: ## Start the full local stack
	$(COMPOSE) up -d
	@echo "API      http://localhost:8000/docs"
	@echo "MinIO    http://localhost:9001  (minioadmin/minioadmin)"

down: ## Stop the stack
	$(COMPOSE) down

build: ## Rebuild images
	$(COMPOSE) build

logs: ## Tail logs (make logs S=api)
	$(COMPOSE) logs -f $(S)

shell: ## Shell into the api container
	$(COMPOSE) exec api bash

# --- database ----------------------------------------------------------------

migrate: ## Apply migrations
	$(API) alembic upgrade head

revision: ## Autogenerate a migration (make revision M="add transactions")
	$(API) alembic revision --autogenerate -m "$(M)"

downgrade: ## Roll back one migration
	$(API) alembic downgrade -1

# There is no `seed` target: the system category taxonomy is seeded by
# migration 0004, so `make migrate` is the only step. The target that used to be
# here pointed at a module that never existed and failed whenever anyone ran it.

# Every E2E run signs up ~80 users and seeds ~300 transactions each, and never
# cleans up. Left alone the dev database grows without bound, which makes the
# hourly notification sweep (O(users)) slower every week and eventually slow
# enough to affect the suite it shares a database with. Run this when local runs
# start feeling slow -- it drops user data and keeps the reference taxonomy.
reset-dev-data: ## Drop accumulated local test users (keeps schema and reference data)
	$(API) python -m scripts.reset_dev_data

# --- quality -----------------------------------------------------------------
# `make check` is exactly what CI runs. Any divergence is a bug in this file.

test: ## Full suite (needs the stack up)
	$(API) pytest --cov=app --cov-report=term-missing

test-unit: ## Unit tests only — no database required
	$(API) pytest -m "not integration"

eval: ## AI evaluation harnesses — prints measured baselines
	$(API) pytest tests/eval -v -s -m eval

lint: ## ruff + mypy + import-linter
	$(API) ruff check app tests scripts
	$(API) ruff format --check app tests scripts
	$(API) mypy app scripts
	$(API) lint-imports --config .importlinter

format: ## Apply formatting and safe fixes
	$(API) ruff format app tests scripts
	$(API) ruff check --fix app tests scripts

types: ## Regenerate frontend types from the OpenAPI schema (needs the stack up)
	cd frontend && npm run generate:types

check-backend: lint test ## Backend gate

check-frontend: ## Frontend gate
	cd frontend && npx next typegen && npm run lint && npm run format:check \
		&& npm run typecheck && npm run build

e2e: ## Playwright smoke tests (needs the stack up)
	cd frontend && npm run e2e

check: check-backend check-frontend ## Everything CI runs (except e2e — run `make e2e`)

# --- frontend ----------------------------------------------------------------

frontend-install: ## Install frontend dependencies
	cd frontend && npm ci

frontend-dev: ## Run the Next.js dev server
	cd frontend && npm run dev

clean: ## Remove containers, volumes, and caches
	$(COMPOSE) down -v
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
