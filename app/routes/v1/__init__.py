from fastapi import APIRouter
from app.routes.v1.telegram_routes import router as telegram_router

router = APIRouter(prefix="/v1")
router.include_router(telegram_router)
