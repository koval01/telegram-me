"""
First version api router
"""
from fastapi import APIRouter

from app.routes.v1 import body, more, post, preview, previews

router = APIRouter(prefix="/v1")

router.include_router(body.router)
router.include_router(more.router)
router.include_router(post.router)
router.include_router(preview.router)
router.include_router(previews.router)
