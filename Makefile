# Grodno walking-route POC — the one place the commands live.
#
#   make            # this help
#   make up         # build + start db, valhalla, agent, frontend
#   make seed       # load the committed data into the database
#   make test       # backend + frontend gates
#
# Everything runs in Docker; the host venv is only needed for `make test-backend`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
SEED    := $(COMPOSE) --profile seed run --rm seed
BACKEND := cd backend && .venv/bin
FRONTEND := cd frontend && npm

.PHONY: help check up down ps logs seed fetch photos prune admin migrate \
        test test-backend test-frontend quality lint

help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

check: ## verify docker + compose + curl are available
	@command -v docker >/dev/null || { echo "docker is missing" >&2; exit 1; }
	@$(COMPOSE) version >/dev/null 2>&1 || { echo "docker compose plugin is missing" >&2; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "the docker daemon is not running" >&2; exit 1; }
	@command -v curl >/dev/null || { echo "curl is missing" >&2; exit 1; }
	@echo "docker, docker compose, curl: ok"
	@test -f .env || echo "note: no .env yet — 'make up' creates one from .env.example"

up: check ## build and start the whole stack (db, valhalla, agent, frontend)
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d --build --wait --wait-timeout 1800
	@echo
	@echo "UI: http://localhost/"

down: ## stop the stack (keeps the volumes)
	$(COMPOSE) down

ps: ## show the services
	$(COMPOSE) ps

logs: ## follow the service logs
	$(COMPOSE) logs -f

seed: ## apply the committed data (full restore / idempotent refresh)
	$(SEED)

fetch: ## re-acquire OSM sights/services into backend/data/*.csv
	$(SEED) fetch

photos: ## resolve photo columns from Wikimedia
	$(SEED) photos --apply

prune: ## list rows outside the region (dry run; add APPLY=1 to delete)
	$(SEED) prune $(if $(APPLY),--apply,)

admin: ## create the first administrator: make admin EMAIL=boss@example.com
	@test -n "$(EMAIL)" || { echo "usage: make admin EMAIL=boss@example.com" >&2; exit 2; }
	$(SEED) admin --email "$(EMAIL)"

migrate: ## apply the Alembic migrations to the database (upgrade head)
	$(BACKEND)/python -m alembic upgrade head

quality: ## regenerate the one-page quality report (reads what the runs wrote)
	$(BACKEND)/python -m quality.report

test: test-backend test-frontend ## run every gate

test-backend: ## ruff + pyright + pytest + the offline seed contract
	$(BACKEND)/ruff check .
	$(BACKEND)/ruff format --check .
	$(BACKEND)/pyright
	$(BACKEND)/python -m db.seed --dry-run
	$(BACKEND)/python -m pytest -q

test-frontend: ## typecheck + lint + unit tests
	$(FRONTEND) run typecheck
	$(FRONTEND) run lint
	$(FRONTEND) test -- run

lint: ## format + lint the frontend
	$(FRONTEND) run prettier
	$(FRONTEND) run lint
