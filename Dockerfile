# syntax=docker/dockerfile:1.10
ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------
# Build: resolve the locked dependencies into a virtualenv, then install the
# project itself. Dependencies get their own layer so code changes rebuild fast.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md LICENSE ./
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
      org.opencontainers.image.licenses="MIT" \
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
