"""
Middleware for adding cache control header
"""
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class ProxyCacheHeaderMiddleware(BaseHTTPMiddleware):  # pylint: disable=R0903
    """
    Middleware to add caching headers for proxy servers. (Only for Cloudflare)

    This middleware adds caching headers to the response, allowing proxy servers
    and clients to cache the response content.
    It sets the "Cache-Control" header to "public" to indicate that the response
    can be cached by public caches, with a maximum age of 10 seconds.
    It also specifies a "stale-while-revalidate" directive with a value of 10 seconds,
    indicating that the response can still be served stale while
    it is being revalidated in the background.

    Note: Ensure that caching is appropriate for the responses handled by the application.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Processes the incoming request and adds cache headers to the response.

        Args:
            request (Request): The incoming request object.
            call_next (Callable): The next middleware or route handler.

        Returns:
            Response: The response object with added caching headers.
        """
        # Call the next middleware or route handler
        response = await call_next(request)

        # Add Cache-Control headers to the response
        if request.headers.get("CF-RAY"):
            response.headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=10"

        return response
