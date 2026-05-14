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

# Backward-compatible API surface without `/v1`.
# Keeps older clients functional while the React UI uses `/api/v1/*`.
legacy_api_router = APIRouter()
legacy_api_router.include_router(health_router, tags=["health-legacy"])
legacy_api_router.include_router(auth_router, tags=["auth-legacy"])
legacy_api_router.include_router(report_router, tags=["report-legacy"])
legacy_api_router.include_router(actions_router, tags=["actions-legacy"])
legacy_api_router.include_router(config_router, tags=["config-legacy"])
