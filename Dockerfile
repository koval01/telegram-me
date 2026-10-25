# syntax=docker/dockerfile:1.10
# Base images are pinned by digest as well as tag: a tag can be moved, a
# digest cannot. Dependabot (docker) updates both together.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM rust:1.99-slim-trixie@sha256:1a939748a7cbab172de91a014e3804a97af1394a8c844faa67fce9401c4e40fb AS rust

# ---------------------------------------------------------------------------
# Build: locked Python dependencies, then the native Rust extension, then the
# project. Each step is its own layer: Python-only changes do not recompile Rust.
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
# BoringSSL (btls-sys, under wreq) is C/C++ built with cmake; bindgen needs
# libclang, and the crate applies its patches with git. Build stage only.
# Not pinned (DL3008): Debian replaces versions with every security update,
# so a pin breaks the build.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        gcc g++ libc6-dev make cmake clang libclang-dev git \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/cargo/bin:${PATH}" \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    CARGO_TARGET_DIR=/cargo-target \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=rust/telegram-me-native/pyproject.toml,target=rust/telegram-me-native/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace

COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY rust ./rust
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/cargo-target \
    uv sync --locked --no-dev --no-editable --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Docs: the documentation site, built for the /docs/ prefix the app serves it
# under (scripts/build_docs.py). Its own stage: no Rust, and a docs change
# rebuilds only this.
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS docs
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/docs-venv \
    PATH="/docs-venv/bin:${PATH}"
WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=rust/telegram-me-native/pyproject.toml,target=rust/telegram-me-native/pyproject.toml \
    uv sync --locked --only-group docs --no-install-workspace
COPY zensical.toml zensical.es.toml zensical.ru.toml CHANGELOG.md ./
COPY overrides ./overrides
COPY docs ./docs
COPY tests/snapshots/graphql/schema.graphql ./tests/snapshots/graphql/
COPY scripts/build_docs.py ./scripts/
RUN python scripts/build_docs.py --out site-instance

# ---------------------------------------------------------------------------
# Runtime: only the virtualenv, the docs, an unprivileged user and no build
# tooling.
# ---------------------------------------------------------------------------
FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="telegram-me" \
      org.opencontainers.image.description="REST API for public Telegram channels" \
      org.opencontainers.image.source="https://github.com/koval01/telegram-me" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# The base image's pip (with the copies of urllib3, msgpack and setuptools
# it vendors) is never used here: the app runs from its own virtualenv.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && python -m pip uninstall --yes --no-cache-dir pip \
    && rm -rf /usr/local/lib/python*/ensurepip

COPY --from=build --chown=root:root /app/.venv /app/.venv
COPY --from=docs --chown=root:root /src/site-instance /app/docs

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000 \
    DOCS_SITE_DIR=/app/docs

WORKDIR /app
# Numeric, so the runtime can tell it is not root without /etc/passwd
# (Kubernetes runAsNonRoot).
USER 10001:10001
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as r; r.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '3000')}/healthz\", timeout=2)"]

STOPSIGNAL SIGTERM
ENTRYPOINT ["telegram-me"]
CMD ["serve"]
