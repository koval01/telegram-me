import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utils.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):  # pylint: disable=R0903
    """Simple in-memory rate limiter (requests per minute per client)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._requests_by_client: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - 60.0
        limit = max(settings.RATE_LIMIT_RPM, 1)
        requests = self._requests_by_client[client_ip]

        while requests and requests[0] < window_start:
            requests.popleft()

        if len(requests) >= limit:
            return Response(
                content="Rate limit exceeded",
                status_code=429,
                headers={"Retry-After": "60"},
            )

        requests.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(limit - len(requests), 0))
        return response
