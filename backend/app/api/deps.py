from __future__ import annotations

import hmac
import secrets
from http.cookies import SimpleCookie
from typing import Any

from fastapi import HTTPException, Request

from ..services.runtime import AppRuntime, get_runtime, request_principal


def runtime() -> AppRuntime:
    return get_runtime()


def set_session_cookies(response: Any, runtime_obj: AppRuntime, token: str) -> None:
    cookie = SimpleCookie()
    cookie["mm_session"] = token
    cookie["mm_session"]["httponly"] = True
    cookie["mm_session"]["path"] = "/"
    cookie["mm_session"]["samesite"] = "Lax"
    if runtime_obj.config.cookie_secure:
        cookie["mm_session"]["secure"] = True
    csrf = secrets.token_urlsafe(24)
    cookie["mm_csrf"] = csrf
    cookie["mm_csrf"]["path"] = "/"
    cookie["mm_csrf"]["samesite"] = "Lax"
    if runtime_obj.config.cookie_secure:
        cookie["mm_csrf"]["secure"] = True
    for morsel in cookie.values():
        response.headers.append("Set-Cookie", morsel.OutputString())


def clear_session_cookies(response: Any) -> None:
    cookie = SimpleCookie()
    cookie["mm_session"] = ""
    cookie["mm_session"]["path"] = "/"
    cookie["mm_session"]["max-age"] = 0
    cookie["mm_csrf"] = ""
    cookie["mm_csrf"]["path"] = "/"
    cookie["mm_csrf"]["max-age"] = 0
    for morsel in cookie.values():
        response.headers.append("Set-Cookie", morsel.OutputString())


def require_rate_limit(req: Request, runtime_obj: AppRuntime) -> None:
    principal = request_principal(req.client.host if req.client else None)
    allowed, retry_after = runtime_obj.rate_limiter.allow(principal)
    if not allowed:
        raise HTTPException(status_code=429, detail={"error": "rate_limited", "retryAfterSeconds": retry_after})


def require_auth(req: Request, runtime_obj: AppRuntime) -> str:
    if not runtime_obj.auth_store.has_password():
        raise HTTPException(status_code=412, detail={"error": "password_not_configured"})
    token = req.cookies.get("mm_session")
    if not runtime_obj.auth_store.is_session_valid(token):
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    return token or ""


def require_csrf(req: Request) -> None:
    header_token = str(req.headers.get("X-CSRF-Token") or "").strip()
    cookie_token = str(req.cookies.get("mm_csrf") or "").strip()
    if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
        raise HTTPException(status_code=403, detail={"error": "csrf_failed"})
