"""Process entry points: the HTTP server (granian) and the standalone feed worker.

The granian main process only supervises workers, so the application (FastAPI,
Redis, the HTTP client, the parser) is imported by the functions that run in
workers rather than at the top of this module.
"""

import asyncio
import logging
import signal
import sys
from typing import Any

from granian import Granian
from granian.constants import Interfaces, Loops
from granian.log import LogLevels
from granian.utils.proxies import wrap_asgi_with_proxy_headers

from telegram_me.log import configure_logging
from telegram_me.settings import Settings

logger = logging.getLogger(__name__)


def asgi() -> Any:
    """ASGI application factory used by granian workers.

    Trusts `X-Forwarded-For` / `X-Forwarded-Proto` only from FORWARDED_ALLOW_IPS.
    """
    from telegram_me.app import create_app  # noqa: PLC0415

    settings = Settings()
    return wrap_asgi_with_proxy_headers(
        create_app(settings), trusted_hosts=settings.forwarded_allow_ips
    )


def serve(settings: Settings, *, reload: bool = False) -> None:
    """Run the HTTP server in the foreground until SIGINT/SIGTERM."""
    configure_logging(settings.log_level, settings.log_format)
    loop = Loops.asyncio if sys.platform == "win32" else Loops.uvloop
    options: dict[str, Any] = {}
    if settings.workers_max_rss_mb:
        options["workers_max_rss"] = settings.workers_max_rss_mb
    if settings.metrics_port:
        options |= {
            "metrics_enabled": True,
            "metrics_address": settings.host,
            "metrics_port": settings.metrics_port,
        }
    Granian(
        "telegram_me.server:asgi",
        factory=True,
        interface=Interfaces.ASGI,
        address=settings.host,
        port=settings.port,
        workers=1 if reload else settings.web_concurrency,
        loop=loop,
        websockets=False,
        log_level=LogLevels(settings.log_level.lower()),
        log_access=settings.access_log,
        respawn_failed_workers=True,
        workers_kill_timeout=settings.graceful_timeout,
        process_name="telegram-me",
        reload=reload,
        reload_paths=None,
        **options,
    ).serve()


async def run_worker(settings: Settings) -> None:
    """Run the feed refresher without an HTTP server.

    Needs REDIS_URL: pages it refreshes must land in the cache the API
    instances read from.
    """
    from telegram_me.container import build_container  # noqa: PLC0415

    configure_logging(settings.log_level, settings.log_format)
    if not settings.redis_url:
        msg = "The feed worker needs REDIS_URL to share its cache with the API"
        raise SystemExit(msg)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with build_container(settings) as container:
        await container.refresher().run(stop)
