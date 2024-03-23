"""
Init module for middleware
"""

from functools import partial
from typing import Coroutine, Any

from . import cache_header
from . import process_time

proxy_cache_header_middleware: partial[Coroutine[Any, Any, Any]] \
    = partial(cache_header.proxy_cache_header_middleware)

process_time_middleware: partial[Coroutine[Any, Any, Any]] \
    = partial(process_time.process_time_middleware)
