from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, require_csrf, require_rate_limit, runtime
from ...services.runtime import export_latest_report_html, export_modules_snapshot, read_report_model

router = APIRouter(prefix="/report")


@router.get("/v3/model")
def report_v3_model(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return read_report_model(rt)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "latest_report_not_found", "firstRunRequired": True}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_read_report", "message": str(exc)}) from exc


@router.post("/v3/export-html")
def report_v3_export_html(req: Request, body: dict | None = None) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    output_path = ""
    if isinstance(body, dict):
        output_path = str(body.get("outputPath") or "").strip()
    try:
        return export_latest_report_html(rt, output_path=output_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "latest_report_not_found", "firstRunRequired": True}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_export_report_html", "message": str(exc)}) from exc


@router.post("/v3/export-snapshot")
def report_v3_export_snapshot(req: Request, body: dict | None = None) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    output_path = ""
    if isinstance(body, dict):
        output_path = str(body.get("outputPath") or "").strip()
    try:
        return export_modules_snapshot(rt, output_path=output_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_foundry_root", "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_export_snapshot", "message": str(exc)}) from exc
