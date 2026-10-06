# Developer entrypoints. Every target is a thin wrapper so the underlying
# command is discoverable and reproducible in CI.

SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose

# Load .env without exporting comments.
ifneq (,$(wildcard .env))
include .env
export
endif

# Host-side database URL. Inside the compose network the hostname is `postgres`;
# from a laptop or a CI runner it is `localhost`. Targets that touch the database
# from the host use this so `make migrate` works without editing .env.
HOST_POSTGRES_DSN ?= $(or $(POSTGRES_DSN_HOST),$(subst postgres:5432,localhost:5432,$(POSTGRES_DSN)))
HOST_REDIS_URL    ?= $(subst redis:6379,localhost:6379,$(REDIS_URL))
HOST_CELERY_BROKER_URL ?= $(subst redis:6379,localhost:6379,$(CELERY_BROKER_URL))

# Settings that must resolve to whichever host the command runs on.
# Workspace source roots. uv's editable-install .pth files are silently skipped by
# CPython when the files carry macOS's UF_HIDDEN flag, which some sync agents set
# under ~/Desktop. Setting PYTHONPATH makes host-side commands work regardless,
# and is a no-op once the packages are installed normally.
export PYTHONPATH := apps/api/src:apps/worker/src:engine/src

export POSTGRES_DSN := $(HOST_POSTGRES_DSN)
export REDIS_URL := $(HOST_REDIS_URL)
export CELERY_BROKER_URL := $(HOST_CELERY_BROKER_URL)

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ lifecycle

.PHONY: up
up: ## Build and start the whole stack
	@test -f .env || { echo "cp .env.example .env first" >&2; exit 1; }
	$(COMPOSE) up --build -d
	@$(MAKE) --no-print-directory health-wait

.PHONY: down
down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

.PHONY: destroy
destroy: ## Stop the stack and delete all data, including cached sandbox images
	$(COMPOSE) down -v --remove-orphans

.PHONY: logs
logs: ## Tail logs for api, worker, and dind
	$(COMPOSE) logs -f --tail=100 api worker dind

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# --------------------------------------------------------------------- health

.PHONY: health
health: ## Curl the liveness and readiness probes
	@curl -fsS http://localhost:$${API_PORT:-8000}/health | head -c 400; echo
	@curl -fsS http://localhost:$${API_PORT:-8000}/health/ready | head -c 600; echo

.PHONY: health-wait
health-wait: ## Block until the API reports ready (compose up dependency)
	@echo -n "waiting for the API"
	@for i in $$(seq 1 60); do \
	  if curl -fsS http://localhost:$${API_PORT:-8000}/health/ready >/dev/null 2>&1; then \
	    echo " ready"; exit 0; \
	  fi; \
	  echo -n "."; sleep 2; \
	done; \
	echo " timed out"; $(COMPOSE) logs --tail=40 api

# ------------------------------------------------------------ local dev loop

.PHONY: install
install: ## Create the venv and install every workspace package
	uv sync --all-packages

.PHONY: api
api: ## Run the API with reload
	uv run uvicorn oa_api.main:create_app --factory --reload --port 8000

.PHONY: worker
worker: ## Run a Celery worker with the warm pool
	uv run celery -A oa_worker.celery_app worker --loglevel=info --concurrency=8

.PHONY: web
web: ## Run the frontend dev server
	cd apps/web && npm run dev

.PHONY: shell
shell: ## Python REPL with every package importable
	uv run python -i

# ------------------------------------------------------------------- quality

.PHONY: lint
lint: ## Ruff check and format check
	uv run ruff check .
	uv run ruff format --check --line-length 100 .

.PHONY: lint-fix
lint-fix: ## Apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format --line-length 100 .

.PHONY: typecheck
typecheck: ## mypy strict over every Python package
	uv run mypy apps/api/src apps/worker/src engine/src
	cd apps/web && npm run typecheck

.PHONY: test
test: ## Run the Python test suite
	uv run pytest

.PHONY: test-gvisor
test-gvisor: ## Run only the gVisor-specific tests (needs SANDBOX_RUNTIME=runsc)
	SANDBOX_RUNTIME=runsc DOCKER_HOST=tcp://127.0.0.1:$${DIND_PORT:-2375} \
	  uv run pytest -m gvisor -v

.PHONY: test-sandbox
test-sandbox: ## Run the live sandbox tests against the dind daemon
	@# dind publishes its API on 127.0.0.1:2375 (loopback only) precisely so this
	@# can run from the host. Tests needing gVisor skip unless runsc is registered.
	DOCKER_HOST=tcp://127.0.0.1:$${DIND_PORT:-2375} uv run pytest -m sandbox -v

.PHONY: test-tmpfs
test-tmpfs: ## Run only the executable-/tmp tests (needs Docker, not gVisor)
	DOCKER_HOST=tcp://127.0.0.1:$${DIND_PORT:-2375} uv run pytest \
	  sandbox/tests/test_tmpfs_exec.py -v

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	uv run pytest --cov --cov-report=term-missing --cov-report=html

.PHONY: test-web
test-web: ## Run the frontend test suite
	cd apps/web && npm test

.PHONY: check
check: lint typecheck test ## Everything the default CI job runs

# ------------------------------------------------------------------ sandbox

.PHONY: sandbox-images
sandbox-images: ## Build the three pre-baked sandbox images
	$(COMPOSE) run --rm sandbox-images

.PHONY: gvisor
gvisor: ## Install and register runsc on the host daemon
	./infra/gvisor/install-runsc.sh

.PHONY: sandbox-shell
sandbox-shell: ## Open a Python shell inside the sandbox image with no controls
	@echo "WARNING: no isolation. For authoring questions only."
	$(COMPOSE) exec dind docker run --rm -it --network=host \
	  -v $$(pwd)/templates:/templates:ro oa/sandbox-python:1.0.0 bash

.PHONY: loadtest
loadtest: ## Run the sandbox load test (Phase 1 step 7; harness not written yet)
	@echo "The load harness is Phase 1 step 7. Until it exists there is nothing to run."
	@exit 1

# ---------------------------------------------------------------------- misc

.PHONY: clean
clean: ## Remove caches and build output
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
	find . -type d -name node_modules -prune -exec rm -rf {} + 2>/dev/null || true

.PHONY: migrate
migrate: ## Apply Alembic migrations
	uv run alembic -c apps/api/alembic.ini -x nothing upgrade head

.PHONY: migrate-down
migrate-down: ## Revert the most recent migration
	uv run alembic -c apps/api/alembic.ini -x nothing downgrade -1

.PHONY: migrate-new
migrate-new: ## Autogenerate a migration: make migrate-new m="add x"
	@test -n "$(m)" || { echo 'usage: make migrate-new m="message"' >&2; exit 1; }
	uv run alembic -c apps/api/alembic.ini -x nothing revision --autogenerate -m "$(m)"

.PHONY: migrate-current
migrate-current: ## Show the current migration revision
	uv run alembic -c apps/api/alembic.ini current

.PHONY: seed
seed: ## Seed concept nodes and question templates
	uv run python -m oa_api.scripts.seed_templates

.PHONY: db-reset
db-reset: ## Drop, recreate, migrate, and seed
	$(COMPOSE) exec -T postgres dropdb --if-exists --force -U $${POSTGRES_USER:-oa} $${POSTGRES_DB:-oa}
	$(COMPOSE) exec -T postgres createdb -U $${POSTGRES_USER:-oa} $${POSTGRES_DB:-oa}
	$(MAKE) migrate
	$(MAKE) seed
