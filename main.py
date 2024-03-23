"""
Main application module for the TelegramMe API.
"""

from fastapi import FastAPI

from routes import body, more, healthz
from middleware import proxy_cache_header_middleware, process_time_middleware

from version import Version
from misc import open_api

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    docs_url="/",
    version=Version().hex
)

app.include_router(body.router)
app.include_router(more.router)
app.include_router(healthz.router)

app.middleware("http")(proxy_cache_header_middleware)
app.middleware("http")(process_time_middleware)


if __name__ == "__main__":
    open_api(app)
