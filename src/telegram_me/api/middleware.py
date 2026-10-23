"""Pure ASGI middleware (no per-request task or body buffering overhead)."""

import platform
import time
from collections import deque

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ResponseHeadersMiddleware:
    """Add `X-App-Node`, `X-Process-Time` and, for Cloudflare, `Cache-Control`."""

    def __init__(self, app: ASGIApp, edge_cache_control: str | None) -> None:
        self.app = app
        self._node = platform.node()
        self._edge_cache_control = edge_cache_control

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: D102
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        from_edge = self._edge_cache_control is not None and any(
            name == b"cf-ray" for name, _ in scope["headers"]
        )

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-App-Node"] = self._node
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                headers["X-Process-Time"] = f"{elapsed_ms} ms"
                if from_edge and self._edge_cache_control:
                    headers["Cache-Control"] = self._edge_cache_control
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    """Sliding-window limit of requests per minute per client IP (per process).

    Behind a reverse proxy, run the server with FORWARDED_ALLOW_IPS set to the
    proxy address so the client IP comes from X-Forwarded-For.
    """

    window_seconds = 60.0
    # Forget idle clients so memory does not grow with every IP ever seen.
    sweep_every_seconds = 60.0

    def __init__(self, app: ASGIApp, requests_per_minute: int) -> None:
        self.app = app
        self._limit = max(requests_per_minute, 1)
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = time.monotonic()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: D102
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        now = time.monotonic()
        self._sweep(now)
        client = scope.get("client")
        key = client[0] if client else "unknown"
        hits = self._hits.setdefault(key, deque())
        window_start = now - self.window_seconds
        while hits and hits[0] < window_start:
            hits.popleft()

        if len(hits) >= self._limit:
            await self._reject(send)
            return

        hits.append(now)
        remaining = str(max(self._limit - len(hits), 0))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(self._limit)
                headers["X-RateLimit-Remaining"] = remaining
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self.sweep_every_seconds:
            return
        self._last_sweep = now
        window_start = now - self.window_seconds
        idle = [key for key, hits in self._hits.items() if not hits or hits[-1] < window_start]
        for key in idle:
            del self._hits[key]

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b"Rate limit exceeded"
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", b"60"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
