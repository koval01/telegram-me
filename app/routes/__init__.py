from fastapi import APIRouter

from app.routes import v1, healthz

router = APIRouter()

router.include_router(healthz.router)
router.include_router(v1.router)
