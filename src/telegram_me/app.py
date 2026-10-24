"""ASGI application factory."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from telegram_me import __version__
from telegram_me.api import graphql, protection, routes
from telegram_me.api.errors import install_error_handlers
from telegram_me.api.middleware import ResponseHeadersMiddleware
from telegram_me.api.protection import EXPOSED_HEADERS, Protection, ProtectionMiddleware
from telegram_me.cache import shared_cache
from telegram_me.container import build_container, running_refresher
from telegram_me.log import configure_logging
from telegram_me.settings import Settings
from telegram_me.telegram.client import Fetcher

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, *, fetcher: Fetcher | None = None) -> FastAPI:
    """Build the API.

    Args:
        settings: configuration; read from the environment when omitted.
        fetcher: replaces the network layer (used by tests).
    """
    settings = settings or Settings()
    configure_logging(settings.log_level, settings.log_format)
    guard = Protection.from_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            container = await stack.enter_async_context(build_container(settings, fetcher))
            app.state.container = container
            shared = shared_cache(container.cache)
            if guard.limiter is not None and settings.rate_limit_shared and shared is not None:
                await stack.enter_async_context(
                    protection.sharing_rate_limits(guard.limiter, shared, guard.per_minute)
                )
            if settings.enable_feed and settings.feed_refresh_mode == "embedded":
                await stack.enter_async_context(running_refresher(container))
            yield

    docs_url = None if settings.disable_docs else "/"
    app = FastAPI(
        title="TelegramMe API",
        description="REST API for public Telegram channels, built on t.me web previews.",
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=None if settings.disable_docs else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.protection = guard
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in (routes.service, routes.channels, routes.feed):
        app.include_router(router)
    if settings.enable_graphql:
        app.include_router(graphql.create_router(settings))
    if guard.pow is not None:
        app.include_router(protection.router)
    install_error_handlers(app)

    # Added last = outermost. CORS answers preflights and decorates every
    # response (rejections included); protection runs before routing.
    if guard.enabled:
        app.add_middleware(ProtectionMiddleware, protection=guard)
    app.add_middleware(ResponseHeadersMiddleware, edge_cache_control=settings.edge_cache_control)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=EXPOSED_HEADERS,
    )
    return app
