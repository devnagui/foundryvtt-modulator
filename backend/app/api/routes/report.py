from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..deps import require_auth, require_csrf, require_rate_limit, runtime
from ...services.runtime import (
    build_update_artifact_bundle,
    export_latest_debug_log,
    export_latest_report_html,
    export_modules_snapshot,
    get_update_artifact,
    list_update_artifacts,
    read_import_history,
    read_planning_context,
    read_report_model,
)

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
    include_data = False
    if isinstance(body, dict):
        output_path = str(body.get("outputPath") or "").strip()
        include_data = bool(body.get("includeData"))
    try:
        return export_modules_snapshot(rt, output_path=output_path, include_data=include_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_foundry_root", "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_export_snapshot", "message": str(exc)}) from exc


@router.post("/v3/export-log")
def report_v3_export_log(req: Request, body: dict | None = None) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    max_bytes = 2 * 1024 * 1024
    if isinstance(body, dict):
        try:
            parsed = int(body.get("maxBytes") or max_bytes)
            if parsed > 0:
                max_bytes = parsed
        except Exception:
            max_bytes = 2 * 1024 * 1024
    try:
        return export_latest_debug_log(rt, max_bytes=max_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "latest_log_not_found"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_export_log", "message": str(exc)}) from exc


@router.get("/v3/import-history")
def report_v3_import_history(req: Request, limit: int = 20) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return read_import_history(rt, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_read_import_history", "message": str(exc)}) from exc


@router.get("/v3/update-artifacts")
def report_v3_update_artifacts(req: Request, limit: int = 50) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return list_update_artifacts(rt, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_read_update_artifacts", "message": str(exc)}) from exc


@router.get("/v3/update-artifacts/{plan_id}")
def report_v3_update_artifact(req: Request, plan_id: str) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return get_update_artifact(rt, plan_id=plan_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "update_artifact_not_found"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_read_update_artifact", "message": str(exc)}) from exc


@router.get("/v3/update-artifacts/{plan_id}/download")
def report_v3_download_update_artifact(req: Request, plan_id: str, includeBackupData: bool = True):
    rt = runtime()
    require_auth(req, rt)
    try:
        bundle = build_update_artifact_bundle(rt, plan_id=plan_id, include_backup_data=bool(includeBackupData))
        file_path = str(bundle.get("path") or "")
        file_name = str(bundle.get("fileName") or f"{plan_id}.zip")
        if not file_path:
            raise FileNotFoundError("update_artifact_not_found")
        return FileResponse(path=file_path, filename=file_name, media_type="application/zip")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "update_artifact_not_found"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_download_update_artifact", "message": str(exc)}) from exc


@router.get("/v3/planning-context")
def report_v3_planning_context(
    req: Request,
    foundryVersion: str,
    systemId: str = "",
    systemVersion: str = "",
    limit: int = 5000,
) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return read_planning_context(
            rt,
            foundry_version=foundryVersion,
            system_id=systemId,
            system_version=systemVersion,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "failed_to_read_planning_context", "message": str(exc)}) from exc
