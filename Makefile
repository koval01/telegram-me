.DEFAULT_GOAL := help
.PHONY: help install dev test snapshots lint format typecheck check fixtures smoke \
        docker up down logs docs docs-serve

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

dev: ## Run the API locally with auto-reload on :3000
	uv run telegram-me serve --reload

test: ## Run the test suite with coverage
	uv run pytest --cov

snapshots: ## Re-generate contract snapshots (review the diff afterwards!)
	UPDATE_SNAPSHOTS=1 uv run pytest tests/test_snapshots.py

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static type checking
	uv run mypy

check: lint typecheck test ## Everything CI runs before building the image

fixtures: ## Re-record t.me fixtures (CHANNELS="a b" for some only)
	uv run python scripts/record_fixtures.py $(CHANNELS)

smoke: ## Smoke-test a running instance against live t.me
	uv run --no-project python scripts/smoke_matrix.py --base-url http://127.0.0.1:$${APP_PORT:-8000}

docker: ## Build the Docker image as telegram-me:dev
	docker build -t telegram-me:dev .

up: ## Start API + Valkey from this checkout (PRESET=standard|full|minimal)
	docker compose up -d --build --wait

down: ## Stop the compose stack
	docker compose --profile worker down

logs: ## Follow compose logs
	docker compose logs -f

docs: ## Build the documentation site into site/
	uv run --group docs zensical build --clean

docs-serve: ## Preview the documentation on :8001
	uv run --group docs zensical serve --dev-addr 127.0.0.1:8001
