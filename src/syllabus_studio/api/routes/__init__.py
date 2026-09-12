"""API routers, assembled into one versioned router."""

from fastapi import APIRouter

from . import catalog, courses, health, lessons, tutor

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(courses.router)
api_router.include_router(lessons.router)
api_router.include_router(tutor.router)
api_router.include_router(catalog.router)

__all__ = ["api_router"]
