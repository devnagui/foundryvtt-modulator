from __future__ import annotations

from fastapi import APIRouter

from ...services.runtime import _utc_now_iso, foundry_status, get_runtime

router = APIRouter()


@router.get("/health")
def health() -> dict:
    rt = get_runtime()
    return {
        "ok": True,
        "passwordConfigured": rt.auth_store.has_password(),
        "foundry": foundry_status(rt),
        "generatedAt": _utc_now_iso(),
    }
