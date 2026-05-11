from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, runtime
from ...services.runtime import read_report_model

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
