"""
Middleware for add to response headers time execution this request
"""
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class ProcessTimeMiddleware(BaseHTTPMiddleware):  # pylint: disable=R0903
    """
    Middleware to measure the processing time of requests and add it to the response headers.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Measures the time taken to process the request and adds it to the response headers.

        Args:
            request (Request): The incoming request object.
            call_next (Callable): The next middleware or route handler.

        Returns:
            Response: The response object with the added processing time header.
        """
        # Record the start time before processing the request
        start_time = time.time()

        # Process the request by calling the next middleware or route handler
        response = await call_next(request)

        # Calculate the processing time in milliseconds
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{int(process_time * 1000)} ms"

        return response
