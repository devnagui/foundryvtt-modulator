from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..deps import clear_session_cookies, require_rate_limit, runtime, set_session_cookies
from ...services.runtime import request_principal

router = APIRouter(prefix="/auth")


@router.get("/status")
def auth_status(req: Request) -> dict:
    rt = runtime()
    token = req.cookies.get("mm_session")
    return {
        "passwordConfigured": rt.auth_store.has_password(),
        "authenticated": rt.auth_store.is_session_valid(token),
    }


@router.post("/setup")
def setup(req: Request, body: dict, response: Response) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    if rt.auth_store.has_password():
        raise HTTPException(status_code=409, detail={"error": "password_already_configured"})
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    confirm = str(body.get("confirmPassword") or "")
    if password != confirm:
        raise HTTPException(status_code=400, detail={"error": "password_confirmation_mismatch"})
    try:
        rt.auth_store.setup_credentials(username=username, password=password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_credentials_setup", "message": str(exc)}) from exc
    token = rt.auth_store.create_session()
    set_session_cookies(response, rt, token)
    response.status_code = 201
    return {"ok": True}


@router.post("/login")
def login(req: Request, body: dict, response: Response) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    if not rt.auth_store.has_password():
        raise HTTPException(status_code=412, detail={"error": "password_not_configured"})
    username = str(body.get("username") or "").strip()
    if rt.auth_store.get_username():
        from service.server import _normalize_username

        try:
            username = _normalize_username(username)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"error": "invalid_credentials"}) from exc
    principal = request_principal(req.client.host if req.client else None)
    lock_key = f"{principal}:{username.lower()}" if username else principal
    locked, remaining = rt.auth_store.is_locked_out(lock_key)
    if locked:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "too_many_attempts",
                "retryAfterSeconds": remaining,
                "recoveryHint": "If you are locked out and cannot recover, stop the service and delete state/auth.json to reset credentials.",
            },
        )
    password = str(body.get("password") or "")
    if not rt.auth_store.verify_credentials(username=username, password=password):
        attempts, lock_seconds = rt.auth_store.register_login_failure(lock_key)
        if lock_seconds is not None:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "too_many_attempts",
                    "retryAfterSeconds": lock_seconds,
                    "recoveryHint": "If you are locked out and cannot recover, stop the service and delete state/auth.json to reset credentials.",
                },
            )
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials", "attempts": attempts})
    rt.auth_store.reset_login_failures(lock_key)
    token = rt.auth_store.create_session()
    set_session_cookies(response, rt, token)
    return {"ok": True}


@router.post("/logout")
def logout(req: Request, response: Response) -> dict:
    rt = runtime()
    token = req.cookies.get("mm_session")
    rt.auth_store.revoke_session(token)
    clear_session_cookies(response)
    return {"ok": True}
