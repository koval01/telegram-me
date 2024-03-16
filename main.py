"""
Main application module for the TelegramMe API.
"""

from fastapi import FastAPI
from routes import body, more

from version import Version

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    docs_url="/",
    version=Version().hex
)

app.include_router(body.router)
app.include_router(more.router)
