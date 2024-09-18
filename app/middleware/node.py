"""
Middleware for add to response headers execution node identifier
"""
import platform
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class NodeMiddleware(BaseHTTPMiddleware):  # pylint: disable=R0903
    """
    Middleware that adds a header to the response
    with the identifier of the server (node) that processed the request.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Processes the incoming request and adds a custom header to the response.

        Args:
            request (Request): The incoming request object.
            call_next (Callable): The next middleware or route handler.

        Returns:
            Response: The response object with the added header.
        """
        # Call the next middleware or route handler
        response = await call_next(request)

        # Add the server's identifier (node name) to the response headers
        response.headers["X-App-Node"] = platform.node()

        return response
