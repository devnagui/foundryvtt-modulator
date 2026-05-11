from fastapi import APIRouter

from .routes.actions import router as actions_router
from .routes.auth import router as auth_router
from .routes.config import router as config_router
from .routes.health import router as health_router
from .routes.report import router as report_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(report_router, tags=["report"])
api_router.include_router(actions_router, tags=["actions"])
api_router.include_router(config_router, tags=["config"])
