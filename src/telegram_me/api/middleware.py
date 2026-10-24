"""Pure ASGI middleware (no per-request task or body buffering overhead)."""

import platform
import time

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
