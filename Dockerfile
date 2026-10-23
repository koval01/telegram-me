# syntax=docker/dockerfile:1.10
ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.13.5
ARG RUST_VERSION=1.99

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM rust:${RUST_VERSION}-slim-trixie AS rust

# ---------------------------------------------------------------------------
# Build: locked Python dependencies, then the native Rust extension, then the
# project. Each step is its own layer: Python-only changes do not recompile Rust.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
RUN apt-get update \
    && apt-get install --yes --no-install-recommends gcc libc6-dev \
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
# Runtime: only the virtualenv, an unprivileged user and no build tooling.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="telegram-me" \
      org.opencontainers.image.description="REST API for public Telegram channels" \
      org.opencontainers.image.source="https://github.com/koval01/telegram-me" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=build --chown=root:root /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000

WORKDIR /app
USER app
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as r; r.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '3000')}/healthz\", timeout=2)"]

STOPSIGNAL SIGTERM
ENTRYPOINT ["telegram-me"]
CMD ["serve"]
