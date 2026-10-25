.DEFAULT_GOAL := help
.PHONY: help install tools dev test snapshots contract lint format typecheck check rust native \
        audit image-scan fixtures smoke integration deploy-test docker up down logs docs docs-serve \
        docs-preview docs-instance

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

# The checks' own binaries, at the versions CI pins (.github/workflows/ci.yml).
# typos, zizmor, vulture and import-linter come with `uv sync` (uv.lock).
CARGO_DENY_VERSION = 0.20.2
CARGO_MACHETE_VERSION = 0.9.2

tools: ## Install cargo-deny and cargo-machete; gitleaks, hadolint and trivy from Homebrew
	cargo install --locked cargo-deny@$(CARGO_DENY_VERSION) cargo-machete@$(CARGO_MACHETE_VERSION)
	@if command -v brew >/dev/null; then brew install gitleaks hadolint trivy shellcheck actionlint; \
	else echo "Install gitleaks, hadolint, trivy, shellcheck and actionlint (see docs/contributing/testing.md)"; fi

dev: ## Run the API locally with auto-reload on :3000
	uv run telegram-me serve --reload

test: ## Run the test suite with coverage
	uv run pytest --cov

snapshots: ## Re-generate contract snapshots (review the diff afterwards!)
	UPDATE_SNAPSHOTS=1 uv run pytest tests/test_snapshots.py tests/test_graphql_contract.py tests/test_openapi_contract.py

contract: ## Breaking REST/GraphQL changes against BASE (default origin/main)
	uv run python scripts/api_contract.py --base $${BASE:-origin/main}
	python3 scripts/coverage_floor.py --base $${BASE:-origin/main}

lint: ## Lint: ruff, layering, dead code, spelling, workflows, Dockerfile, shell scripts
	uv run ruff check .
	uv run ruff format --check .
	uv run lint-imports
	uv run vulture
	uv run typos
	uv run zizmor --offline .github
	hadolint Dockerfile
	shellcheck deploy/*.sh
	actionlint

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

audit: ## Dependencies and secrets: RustSec, licenses, unused crates, PyPI advisories, git history
	cd rust/telegram-me-native && cargo deny --locked check && cargo machete
	uv audit --locked --preview-features audit-command
	gitleaks git --redact --no-banner .

IMAGE ?= telegram-me:dev

image-scan: ## Fixable high and critical vulnerabilities in IMAGE (default telegram-me:dev, `make docker`)
	trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(IMAGE)

check: rust lint typecheck test audit ## Everything CI runs before building the image

fixtures: ## Re-record t.me fixtures (CHANNELS="a b" for some only)
	uv run python scripts/record_fixtures.py $(CHANNELS)

smoke: ## Smoke-test a running instance against live t.me
	uv run --no-project python scripts/smoke_matrix.py --base-url http://127.0.0.1:$${APP_PORT:-8000}

# Black-box tests of an image (TME_TEST_IMAGE, default telegram-me:dev); their
# own environment, so the project's .venv keeps its packages.
IT_ENV = UV_PROJECT_ENVIRONMENT=.venv-integration TME_TEST_IMAGE=$${TME_TEST_IMAGE:-telegram-me:dev}

integration: ## The image as two servers with Valkey (TME_PREVIOUS_IMAGE: rolling upgrade too)
	$(IT_ENV) uv run --only-group integration pytest --confcutdir tests/integration tests/integration

deploy-test: ## deploy/ scripts with real Docker Compose against the image
	$(IT_ENV) uv run --only-group integration pytest --confcutdir tests/deploy tests/deploy

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

docs-instance: ## Build the docs as the image serves them at /docs into site-instance/ (DOCS_SITE_DIR)
	uv run --group docs python scripts/build_docs.py

docs-preview: docs ## Serve the built site with all languages on :8001
	uv run --no-project python -m http.server 8001 --bind 127.0.0.1 --directory site
