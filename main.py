"""
Main application module for the TelegramMe API.
"""

import json

from fastapi import FastAPI
from routes import body, more, healthz

from version import Version
from misc import write_to_file

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    docs_url="/",
    version=Version().hex
)

app.include_router(body.router)
app.include_router(more.router)
app.include_router(healthz.router)


if __name__ == "__main__":
    openapi = app.openapi()

    if openapi:
        write_to_file("docs/static/openapi.json", json.dumps(openapi))
