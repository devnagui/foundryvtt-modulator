from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.router import api_router


def create_app() -> FastAPI:
    app = FastAPI(title="FoundryVTT Modulator API", version="0.1.0")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    app.include_router(api_router, prefix="/api/v1")

    repo_root = Path(__file__).resolve().parents[2]
    ui_dist_dir = repo_root / "frontend" / "dist"
    assets_dir = ui_dist_dir / "assets"

    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def root() -> FileResponse:
        if ui_dist_dir.exists() and (ui_dist_dir / "index.html").exists():
            return FileResponse(str(ui_dist_dir / "index.html"))
        return FileResponse(str(repo_root / "reports" / "module-resolver-latest.html"))

    @app.get("/app")
    @app.get("/app/report")
    def report_app() -> FileResponse:
        return root()

    @app.get("/app/{_path:path}")
    def app_catch_all(_path: str) -> FileResponse:
        return root()

    return app


app = create_app()
