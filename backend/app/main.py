from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api.router import api_router, legacy_api_router
from .services.core import load_config


def create_app() -> FastAPI:
    app = FastAPI(title="FoundryVTT Modulator API", version="0.1.0")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(legacy_api_router, prefix="/api")

    config = load_config()
    ui_dist_dir = Path(config.ui_dist_dir)
    assets_dir = ui_dist_dir / "assets"

    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _serve_react_app() -> Response:
        index_file = ui_dist_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse(
            status_code=503,
            content={
                "error": "frontend_dist_not_found",
                "message": "React frontend build not found. Run frontend build and retry.",
            },
        )

    @app.get("/")
    def root() -> Response:
        return _serve_react_app()

    @app.get("/app")
    @app.get("/app/report")
    def report_app() -> Response:
        return _serve_react_app()

    @app.get("/app/{_path:path}")
    def app_catch_all(_path: str) -> Response:
        return _serve_react_app()

    return app


app = create_app()
