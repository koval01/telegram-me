"""
Main application module for the TelegramMe API.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routes.v1 import body, more
from routes import healthz
from middleware import proxy_cache_header_middleware, process_time_middleware

from version import Version

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    version=Version().hex,
    docs_url="/"
)
app.mount("/static", StaticFiles(directory="static"), name="static")


app.include_router(body.router)
app.include_router(more.router)
app.include_router(healthz.router)

app.middleware("http")(proxy_cache_header_middleware)
app.middleware("http")(process_time_middleware)
