"""
Middleware for add X-Process-Time header
"""

import time

from typing import Callable

from fastapi import Request, Response


async def process_time_middleware(request: Request, call_next: Callable) -> Response:
    """
    Middleware to measure the processing time of requests.

    Parameters:
    - request (Request): The incoming request object.
    - call_next (Callable): The function to call to proceed with the request handling.

    Returns:
    - Response: The response object generated after processing the request.

    This middleware measures the time taken to process a request by capturing
    the time before and after calling the next middleware or the request handler.
    It then calculates the processing time in milliseconds and adds it to the
    response headers under the key "X-Process-Time".
    The processed request is then returned.
    """
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{int(process_time * 1000)} ms"
    return response
