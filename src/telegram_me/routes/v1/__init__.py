from fastapi import APIRouter
from telegram_me.routes.v1.telegram_routes import router as telegram_router
from telegram_me.routes.v1.feed import router as feed_router

router = APIRouter(prefix="/v1")
router.include_router(telegram_router)
router.include_router(feed_router)
