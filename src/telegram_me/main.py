from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from telegram_me.routes import router

from telegram_me.middleware.node import NodeMiddleware
from telegram_me.middleware.process_time import ProcessTimeMiddleware
from telegram_me.middleware.cache_header import ProxyCacheHeaderMiddleware
from telegram_me.middleware.rate_limit import RateLimitMiddleware

from telegram_me.utils.config import settings

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    version=settings.VERSION,
    docs_url="/",
    openapi_url=None if bool(settings.DISABLE_DOCS) else "/openapi.json",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.add_middleware(
    CORSMiddleware,  # type: ignore[no-untyped-call]
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(NodeMiddleware)  # type: ignore[no-untyped-call]
app.add_middleware(ProcessTimeMiddleware)  # type: ignore[no-untyped-call]
app.add_middleware(ProxyCacheHeaderMiddleware)  # type: ignore[no-untyped-call]
if settings.ENABLE_RATE_LIMIT:
    app.add_middleware(RateLimitMiddleware)  # type: ignore[no-untyped-call]

app.include_router(router)
