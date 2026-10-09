SHELL := /bin/bash
.DEFAULT_GOAL := help

# Python 3.11+ (3.11-3.13 recommended; uagents relies on pydantic.v1).
PYTHON ?= $(shell command -v python3.11 || command -v python3.12 || command -v python3.13 || command -v python3)
VENV   ?= .venv
BIN    := $(VENV)/bin
PY     := $(BIN)/python
CORE_PORT ?= 8080

.PHONY: help setup dev dev-local seed reset-demo test test-py test-node lint fmt openapi ts-types contracts \
        smoke agents agents-hello agent-addresses chat relay relay-demo branches clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create .venv, install Python + Node deps, create .env from template
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e shared/python -e . -e "core[dev]"
	$(BIN)/pip install -r fetch/requirements.txt
	cd relay && npm install
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example (all integrations in mock mode)")

dev: ## Run Core + Postgres with docker compose (http://localhost:8080/docs)
	docker compose up --build core db

dev-local: ## Run Core locally on SQLite, no Docker (http://localhost:8080/docs)
	cd core && ../$(BIN)/alembic upgrade head && ../$(BIN)/uvicorn app.main:create_app --factory --reload --port $(CORE_PORT)

seed: ## Load the synthetic demo scenario into the configured database
	bash scripts/seed.sh

reset-demo: ## Between judges: wipe the demo patient's cases and re-seed a clean one
	cd core && ../$(PY) -m alembic upgrade head && ../$(PY) -m app.seed.reset_demo

test: test-py test-node ## Run all tests (Python + Node), mock mode

test-py: ## Run Python tests (shared, core, intelligence, finchnode, fetch)
	$(PY) -m pytest

test-node: ## Run Relay service tests (vitest)
	cd relay && npm test

lint: ## Lint Python (ruff) and type-check TypeScript
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	cd relay && npm run lint

fmt: ## Auto-format Python
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

openapi: ## Export Core OpenAPI to shared/openapi/core.openapi.json
	$(PY) scripts/export_openapi.py

ts-types: openapi ## Regenerate shared/ts/core.ts from the OpenAPI spec
	bash scripts/gen_ts_types.sh

contracts: ts-types ## Regenerate all generated contract artifacts

smoke: ## Full mock end-to-end run through the real Core HTTP API
	$(PY) scripts/smoke_e2e.py

agents: ## Run the Fetch agents (Bureau by default; see fetch/agents/run_all.py)
	$(PY) -m fetch.agents.run_all

agents-hello: ## Run only the orchestrator in HELLO_WORLD mode (ASI:One discoverability check)
	HELLO_WORLD=1 $(PY) -m fetch.agents.orchestrator

agent-addresses: ## Print agent addresses derived from the *_SEED env vars
	$(PY) -m fetch.agents.print_addresses

chat: ## Chat with the orchestrator logic locally (LOCAL_DIRECT, needs Core running)
	$(PY) -m fetch.agents.local_direct

relay: ## Run the Relay service (http://localhost:3000)
	cd relay && npm run dev

relay-demo: ## Simulate a patient conversation through the Relay handlers (needs Core running)
	cd relay && npm run demo

branches: ## Create branch-1-relay and branch-2-fetch from main
	bash scripts/setup_branches.sh

clean: ## Remove local databases and caches
	rm -rf .data .pytest_cache .ruff_cache relay/data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
