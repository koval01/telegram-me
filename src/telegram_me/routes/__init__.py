from fastapi import APIRouter

from telegram_me.routes import v1, healthz

router = APIRouter()

router.include_router(healthz.router)
router.include_router(v1.router)
