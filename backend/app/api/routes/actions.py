from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, require_csrf, require_rate_limit, runtime
from ...services.runtime import (
    SuggestionProviderError,
    execute_rollback,
    module_health,
    queue_action,
    rollback_plan,
    suggest_module,
    suggest_modules_batch,
)

router = APIRouter(prefix="/actions")


@router.post("/submit")
def submit_action(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    action = str(body.get("action") or "")
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": "invalid_payload"})
    try:
        return queue_action(rt, action, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


@router.get("/jobs/{job_id}")
def job_status(req: Request, job_id: str) -> dict:
    rt = runtime()
    require_auth(req, rt)
    if not str(job_id or "").strip():
        raise HTTPException(status_code=400, detail={"error": "job_id_required"})
    job = rt.action_engine.get_job(job_id.strip())
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    return job


@router.get("/jobs")
def jobs(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    return rt.action_engine.list_jobs()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(req: Request, job_id: str) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    if not str(job_id or "").strip():
        raise HTTPException(status_code=400, detail={"error": "job_id_required"})
    ok = rt.action_engine.cancel(job_id.strip())
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "job_not_cancellable"})
    return {"ok": True, "jobId": job_id.strip(), "status": "cancelling"}


@router.get("/rollback-plan")
def get_rollback_plan(req: Request, scanRunId: int) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return rollback_plan(rt, scanRunId)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "scan_run_not_found"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "scan_run_id_required", "message": str(exc)}) from exc


@router.get("/module-health")
def get_module_health(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    try:
        return module_health(rt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_foundry_root", "message": str(exc)}) from exc


@router.post("/rollback-execute")
def rollback_execute(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        scan_run_id = int(body.get("scanRunId") or 0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "scan_run_id_required"}) from exc
    if scan_run_id <= 0:
        raise HTTPException(status_code=400, detail={"error": "scan_run_id_required"})
    try:
        return execute_rollback(rt, scan_run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "scan_run_not_found"}) from exc
    except ValueError as exc:
        message = str(exc)
        code = message if message in {"rollback_backups_not_found"} else "rollback_failed"
        raise HTTPException(status_code=400, detail={"error": code, "message": message}) from exc
    except RuntimeError as exc:
        if str(exc) == "maintenance_requires_foundry_offline":
            raise HTTPException(status_code=412, detail={"error": "maintenance_requires_foundry_offline", "message": "Stop Foundry before rollback."}) from exc
        raise HTTPException(status_code=400, detail={"error": "rollback_failed", "message": str(exc)}) from exc


@router.post("/suggest-module")
def post_suggest_module(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        return suggest_module(
            rt,
            module_id=str(body.get("moduleId") or "").strip(),
            manifest_url=str(body.get("manifestUrl") or "").strip(),
            project_url=str(body.get("projectUrl") or "").strip(),
            force_refresh=bool(body.get("forceRefresh")),
            target_foundry_version=str(body.get("targetFoundryVersion") or "").strip(),
            installed_system_versions_override=body.get("installedSystemVersions") if isinstance(body.get("installedSystemVersions"), dict) else None,
        )
    except SuggestionProviderError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        raise HTTPException(
            status_code=400,
            detail={
                "error": str(payload.get("errorCode") or "suggestion_provider_error"),
                "message": str(payload.get("message") or "Could not refresh versions from provider."),
                "hint": str(payload.get("hint") or ""),
                "retryable": bool(payload.get("retryable")),
                "rawError": str(payload.get("raw") or ""),
                "moduleId": str(payload.get("moduleId") or ""),
            },
        ) from exc
    except ValueError as exc:
        msg = str(exc)
        code = msg if msg in {"manifest_or_project_required", "module_id_required"} else "suggestion_failed"
        raise HTTPException(status_code=400, detail={"error": code, "message": msg}) from exc


@router.post("/suggest-modules-batch")
def post_suggest_modules_batch(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    modules = body.get("modules")
    if not isinstance(modules, list):
        raise HTTPException(status_code=400, detail={"error": "modules_list_required"})
    try:
        return suggest_modules_batch(
            rt,
            modules=[item for item in modules if isinstance(item, dict)],
            force_refresh=bool(body.get("forceRefresh")),
            target_foundry_version=str(body.get("targetFoundryVersion") or "").strip(),
            installed_system_versions_override=body.get("installedSystemVersions") if isinstance(body.get("installedSystemVersions"), dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "suggestion_failed", "message": str(exc)}) from exc
