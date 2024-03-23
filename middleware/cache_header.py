"""
Middleware for add Cache-Control header
"""

from typing import Callable

from fastapi import Request, Response


async def proxy_cache_header_middleware(request: Request, call_next: Callable) -> Response:
    """
    Middleware to add caching headers for proxy servers.

    Parameters:
    - request (Request): The incoming request object.
    - call_next (Callable): The function to call to proceed with the request handling.

    Returns:
    - Response: The response object generated after processing the request.

    This middleware adds caching headers to the response, allowing proxy servers
    and clients to cache the response content.
    It sets the "Cache-Control" header to "public" to indicate that the response
    can be cached by public caches, with a maximum age of 120 seconds (2 minutes).
    It also specifies a "stale-while-revalidate" directive with a value of 30 seconds,
    indicating that the response can still be served stale while
    it is being revalidated in the background.

    Note: This middleware assumes that caching is safe and appropriate
    for the responses handled by the application.
    Ensure that caching is used appropriately based
    on the requirements and nature of the application's responses.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "public, max-age=120, stale-while-revalidate=30"
    return response
