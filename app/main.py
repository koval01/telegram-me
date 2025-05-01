from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router

from app.middleware.node import NodeMiddleware
from app.middleware.process_time import ProcessTimeMiddleware
from app.middleware.cache_header import ProxyCacheHeaderMiddleware

from app.utils.config import settings

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    version=settings.VERSION,
    docs_url="/",
    openapi_url=None if bool(settings.DISABLE_DOCS) else "/openapi.json",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
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

app.include_router(router)
