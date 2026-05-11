from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, require_csrf, require_rate_limit, runtime
from ...services.core import _pick_folder_native, _validate_foundry_root_path
from ...services.runtime import save_module_source, set_foundry_root

router = APIRouter(prefix="/config")


@router.get("/foundry-root")
def foundry_root_status(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    return rt.config_store.status()


@router.post("/foundry-root")
def foundry_root_set(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        return set_foundry_root(rt, str(body.get("path") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_foundry_root", "message": str(exc)}) from exc


@router.post("/foundry-root/reset")
def foundry_root_reset(req: Request) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    rt.config_store.set_data_root("")
    return rt.config_store.status()


@router.post("/foundry-root/pick")
def foundry_root_pick(req: Request) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        selected = _pick_folder_native()
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "folder_picker_unavailable", "message": str(exc)}) from exc
    if not selected:
        raise HTTPException(status_code=400, detail={"error": "folder_picker_cancelled"})
    ok, normalized, details = _validate_foundry_root_path(selected)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_foundry_root",
                "message": details.get("message") or "Invalid Foundry root path.",
                "selectedPath": selected,
                "details": details,
            },
        )
    rt.config_store.set_data_root(normalized)
    payload = rt.config_store.status()
    payload["selectedPath"] = selected
    return payload


@router.get("/module-sources")
def list_module_sources(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    return {"sources": rt.module_source_store.list_sources()}


@router.post("/module-sources")
def upsert_module_sources(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        return save_module_source(
            rt,
            module_id=str(body.get("moduleId") or "").strip(),
            manifest_url=str(body.get("manifestUrl") or "").strip(),
            project_url=str(body.get("projectUrl") or "").strip(),
        )
    except ValueError as exc:
        code = str(exc)
        mapped = code if code in {"module_id_required", "manifest_or_project_required"} else "source_validation_failed"
        raise HTTPException(status_code=400, detail={"error": mapped, "message": str(exc)}) from exc
