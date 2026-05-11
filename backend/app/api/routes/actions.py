from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, require_csrf, require_rate_limit, runtime
from ...services.runtime import module_health, queue_action, rollback_plan, suggest_module

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
        )
    except ValueError as exc:
        msg = str(exc)
        code = msg if msg in {"manifest_or_project_required", "module_id_required"} else "suggestion_failed"
        raise HTTPException(status_code=400, detail={"error": code, "message": msg}) from exc
