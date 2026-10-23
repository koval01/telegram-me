"""ASGI application factory."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from telegram_me import __version__
from telegram_me.api import graphql, routes
from telegram_me.api.errors import install_error_handlers
from telegram_me.api.middleware import RateLimitMiddleware, ResponseHeadersMiddleware
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            container = await stack.enter_async_context(build_container(settings, fetcher))
            app.state.container = container
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
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in (routes.service, routes.channels, routes.feed):
        app.include_router(router)
    if settings.enable_graphql:
        app.include_router(graphql.create_router(settings))
    install_error_handlers(app)

    # Added last = outermost: rate limiting runs before everything else.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ResponseHeadersMiddleware, edge_cache_control=settings.edge_cache_control)
    if settings.enable_rate_limit:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)
    return app
