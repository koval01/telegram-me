.DEFAULT_GOAL := help
.PHONY: help install dev test snapshots lint format typecheck check rust native fixtures smoke \
        docker up down logs docs docs-serve docs-preview

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
	UPDATE_SNAPSHOTS=1 uv run pytest tests/test_snapshots.py tests/test_graphql_contract.py

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static type checking
	uv run mypy

rust: ## Rust: fmt check, clippy (pedantic, both feature sets), tests
	cd rust/telegram-me-native && cargo fmt --check \
		&& cargo clippy --all-targets -- -D warnings \
		&& cargo clippy --all-targets --features python -- -D warnings \
		&& cargo test

native: ## Rebuild the Rust extension into the virtualenv after editing rust/
	uv sync --reinstall-package telegram-me-native

check: rust lint typecheck test ## Everything CI runs before building the image

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

docs: ## Build the documentation into site/ (English), site/es/ and site/ru/
	uv run --group docs zensical build --clean
	uv run --group docs zensical build --clean -f zensical.es.toml
	uv run --group docs zensical build --clean -f zensical.ru.toml

docs-serve: ## Live preview of one language on :8001 (DOCS_LANG=en|es|ru)
	uv run --group docs zensical serve --dev-addr 127.0.0.1:8001 \
		$(if $(filter-out en,$(DOCS_LANG)),-f zensical.$(DOCS_LANG).toml)

docs-preview: docs ## Serve the built site with all languages on :8001
	uv run --no-project python -m http.server 8001 --bind 127.0.0.1 --directory site
