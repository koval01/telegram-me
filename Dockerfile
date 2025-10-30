FROM python:3.13.9-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13.9-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser
USER appuser

WORKDIR /app

COPY --from=builder /install /usr/local

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser static ./static
COPY --chown=appuser:appuser start.sh ./start.sh

RUN chmod +x start.sh

ENV PORT=3000
EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/healthz || exit 1

CMD ["./start.sh"]
