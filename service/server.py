from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import mimetypes

from resolver.foundry import detect_foundry_version
from resolver.local import load_system_versions
from resolver.models import ModuleRecord
from resolver.report_v3 import render_html_report_v3
from resolver.db_queries import load_apply_history
from resolver.scoring import candidate_sort_key, satisfies_release_constraints
from resolver.sources import fetch_release_history


MODULE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


@dataclass
class ServiceConfig:
    tool_root: Path
    data_root: str
    cache_dir: str
    reports_dir: Path
    state_dir: Path
    auth_file: Path
    bind_host: str
    bind_port: int
    python_bin: str
    session_ttl_hours: int
    pbkdf2_iterations: int
    require_foundry_offline: bool
    foundry_host: str
    foundry_port: int
    foundry_process_name: str
    cookie_secure: bool
    auth_max_failed_attempts: int
    auth_lockout_minutes: int
    request_rate_limit_per_minute: int
    max_sessions: int
    audit_file: Path
    use_new_ui: bool = False
    ui_dist_dir: Path = field(default_factory=lambda: Path("frontend/dist"))
    disable_legacy_report_ui: bool = False


class RequestRateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, int(max_per_minute))
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, principal: str) -> tuple[bool, int]:
        key = str(principal or "unknown").strip() or "unknown"
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - 60.0
        with self._lock:
            rows = [ts for ts in self._hits.get(key, []) if ts >= cutoff]
            if len(rows) >= self._max:
                retry_after = max(1, int(60 - (now - rows[0])))
                self._hits[key] = rows
                return False, retry_after
            rows.append(now)
            self._hits[key] = rows
            return True, 0


@dataclass
class ActionJob:
    job_id: str
    action: str
    payload: dict[str, Any]
    status: str
    progress: int
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class ActionEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._jobs: dict[str, ActionJob] = {}
        self._running_job_id: str | None = None

    def enqueue(self, action: str, payload: dict[str, Any]) -> ActionJob:
        with self._lock:
            now = _utc_now_iso()
            job = ActionJob(
                job_id=secrets.token_hex(12),
                action=action,
                payload=copy.deepcopy(payload),
                status="pending",
                progress=0,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
            return copy.deepcopy(job)

    def pick_next(self) -> ActionJob | None:
        with self._lock:
            if self._running_job_id is not None:
                return None
            if not self._queue:
                return None
            job_id = self._queue.pop(0)
            job = self._jobs.get(job_id)
            if not job:
                return None
            now = _utc_now_iso()
            job.status = "running"
            job.progress = 5
            job.started_at = now
            job.updated_at = now
            self._running_job_id = job_id
            return copy.deepcopy(job)

    def complete(self, job_id: str, ok: bool, result: dict[str, Any] | None, error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "success" if ok else "failed"
            job.progress = 100
            now = _utc_now_iso()
            job.updated_at = now
            job.finished_at = now
            job.result = copy.deepcopy(result) if result is not None else None
            job.error = str(error or "") if error else None
            if self._running_job_id == job_id:
                self._running_job_id = None

    def set_progress(self, job_id: str, progress: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0, min(int(progress), 99))
            job.updated_at = _utc_now_iso()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            queue_position = -1
            if job.status == "pending":
                try:
                    queue_position = self._queue.index(job_id)
                except ValueError:
                    queue_position = -1
            payload = self._job_to_dict(job)
            payload["queuePosition"] = queue_position
            return payload

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return {
                "runningJobId": self._running_job_id,
                "pendingCount": len(self._queue),
                "jobs": [self._job_to_dict(job) for job in ordered[:200]],
            }

    def _job_to_dict(self, job: ActionJob) -> dict[str, Any]:
        return {
            "jobId": job.job_id,
            "action": job.action,
            "payload": copy.deepcopy(job.payload),
            "status": job.status,
            "progress": job.progress,
            "createdAt": job.created_at,
            "updatedAt": job.updated_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "result": copy.deepcopy(job.result),
            "error": job.error,
        }


class RuntimeConfigStore:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._path = config.state_dir / "runtime-config.json"
        self._lock = threading.Lock()

    def get_data_root(self) -> str:
        payload = self._read()
        return str(payload.get("foundryDataRoot") or "").strip()

    def set_data_root(self, data_root: str) -> None:
        with self._lock:
            payload = self._read()
            payload["foundryDataRoot"] = str(data_root or "").strip()
            payload["updatedAt"] = _utc_now_iso()
            self._write(payload)

    def status(self) -> dict[str, Any]:
        selected = self.get_data_root()
        if not selected:
            return {"selected": "", "valid": False, "message": "No Foundry path selected yet."}
        ok, normalized, details = _validate_foundry_root_path(selected)
        return {
            "selected": selected,
            "normalized": normalized if ok else "",
            "valid": bool(ok),
            "message": details.get("message") or ("Foundry root is valid." if ok else "Invalid Foundry root."),
            "details": details,
        }

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: dict[str, Any]) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class ModuleSourceStore:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._path = config.state_dir / "module-sources.json"
        self._lock = threading.Lock()

    def list_sources(self) -> dict[str, dict[str, Any]]:
        payload = self._read()
        rows = payload.get("sources")
        if not isinstance(rows, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for module_id, item in rows.items():
            if not isinstance(item, dict):
                continue
            key = str(module_id or "").strip()
            if not key:
                continue
            normalized[key] = {
                "moduleId": key,
                "manifestUrl": str(item.get("manifestUrl") or "").strip(),
                "projectUrl": str(item.get("projectUrl") or "").strip(),
                "updatedAt": str(item.get("updatedAt") or ""),
            }
        return normalized

    def upsert_source(self, module_id: str, manifest_url: str, project_url: str = "") -> dict[str, Any]:
        clean_id = str(module_id or "").strip()
        if not clean_id:
            raise ValueError("module_id_required")
        clean_manifest = str(manifest_url or "").strip()
        clean_project = str(project_url or "").strip()
        if not clean_manifest and not clean_project:
            raise ValueError("manifest_or_project_required")
        with self._lock:
            payload = self._read()
            rows = payload.get("sources")
            if not isinstance(rows, dict):
                rows = {}
            row = {
                "moduleId": clean_id,
                "manifestUrl": clean_manifest,
                "projectUrl": clean_project,
                "updatedAt": _utc_now_iso(),
            }
            rows[clean_id] = row
            payload["sources"] = rows
            self._write(payload)
            return row

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: dict[str, Any]) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class AuthStore:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._sessions: dict[str, datetime] = {}
        self._failed_attempts: dict[str, int] = {}
        self._lockouts: dict[str, datetime] = {}
        self._load_sessions_from_disk()

    def has_password(self) -> bool:
        payload = self._read_auth_payload()
        return isinstance(payload.get("password"), dict)

    def get_username(self) -> str:
        payload = self._read_auth_payload()
        raw = payload.get("username")
        return str(raw or "").strip()

    def setup_credentials(self, username: str, password: str) -> None:
        clean_username = _normalize_username(username)
        _validate_password_policy(password, clean_username)
        with self._lock:
            payload = self._read_auth_payload()
            if isinstance(payload.get("password"), dict):
                raise RuntimeError("Password already configured.")
            now = _utc_now_iso()
            payload["username"] = clean_username
            payload["password"] = self._hash_password(password)
            payload["password"]["createdAt"] = now
            payload["password"]["updatedAt"] = now
            self._write_auth_payload(payload)

    def verify_password(self, password: str) -> bool:
        payload = self._read_auth_payload()
        password_info = payload.get("password")
        if not isinstance(password_info, dict):
            return False
        try:
            salt = base64.b64decode(str(password_info["salt"]).encode("utf-8"))
            digest = base64.b64decode(str(password_info["hash"]).encode("utf-8"))
            iterations = int(password_info.get("iterations") or self._config.pbkdf2_iterations)
        except Exception:
            return False
        attempt = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest, attempt)

    def verify_credentials(self, username: str, password: str) -> bool:
        stored_username = self.get_username()
        if not stored_username:
            # Backward compatibility with existing password-only setup.
            return self.verify_password(password)
        clean_input = _normalize_username(username)
        if not hmac.compare_digest(stored_username.lower(), clean_input.lower()):
            return False
        return self.verify_password(password)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=max(self._config.session_ttl_hours, 1))
        with self._lock:
            self._prune_expired_sessions_locked()
            if self._config.max_sessions > 0 and len(self._sessions) >= self._config.max_sessions:
                # Drop oldest session to prevent unbounded growth.
                oldest = sorted(self._sessions.items(), key=lambda item: item[1])[0][0]
                self._sessions.pop(oldest, None)
            self._sessions[token] = expires
            self._persist_sessions_locked()
        return token

    def is_session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            self._prune_expired_sessions_locked()
            expires = self._sessions.get(token)
            if not expires:
                return False
            now = datetime.now(timezone.utc)
            if expires <= now:
                self._sessions.pop(token, None)
                return False
            return True

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)
            self._persist_sessions_locked()

    def revoke_all_sessions(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._persist_sessions_locked()

    def is_locked_out(self, principal: str) -> tuple[bool, int]:
        now = datetime.now(timezone.utc)
        with self._lock:
            until = self._lockouts.get(principal)
            if not until:
                return False, 0
            if until <= now:
                self._lockouts.pop(principal, None)
                self._failed_attempts.pop(principal, None)
                return False, 0
            remaining = int((until - now).total_seconds())
            return True, max(remaining, 1)

    def register_login_failure(self, principal: str) -> tuple[int, int | None]:
        if not principal:
            principal = "unknown"
        with self._lock:
            attempts = int(self._failed_attempts.get(principal, 0)) + 1
            self._failed_attempts[principal] = attempts
            if attempts >= max(self._config.auth_max_failed_attempts, 1):
                until = datetime.now(timezone.utc) + timedelta(minutes=max(self._config.auth_lockout_minutes, 1))
                self._lockouts[principal] = until
                return attempts, int((until - datetime.now(timezone.utc)).total_seconds())
            return attempts, None

    def reset_login_failures(self, principal: str) -> None:
        with self._lock:
            self._failed_attempts.pop(principal, None)
            self._lockouts.pop(principal, None)

    def _prune_expired_sessions_locked(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [token for token, expires in self._sessions.items() if expires <= now]
        for token in expired:
            self._sessions.pop(token, None)
        if expired:
            self._persist_sessions_locked()

    def _load_sessions_from_disk(self) -> None:
        payload = self._read_auth_payload()
        sessions = payload.get("sessions")
        if not isinstance(sessions, list):
            return
        now = datetime.now(timezone.utc)
        restored: dict[str, datetime] = {}
        for item in sessions:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or "").strip()
            expires_raw = str(item.get("expiresAt") or "").strip()
            if not token or not expires_raw:
                continue
            parsed = _parse_utc_timestamp(expires_raw)
            if parsed is None or parsed <= now:
                continue
            restored[token] = parsed
        with self._lock:
            self._sessions = restored
            self._persist_sessions_locked()

    def _persist_sessions_locked(self) -> None:
        payload = self._read_auth_payload()
        rows = [
            {
                "token": token,
                "expiresAt": expires.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            for token, expires in sorted(self._sessions.items(), key=lambda item: item[1])
        ]
        payload["sessions"] = rows
        self._write_auth_payload(payload)

    def _read_auth_payload(self) -> dict[str, Any]:
        path = self._config.auth_file
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_auth_payload(self, payload: dict[str, Any]) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        self._config.auth_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _hash_password(self, password: str) -> dict[str, Any]:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self._config.pbkdf2_iterations,
        )
        return {
            "algo": "pbkdf2_sha256",
            "iterations": self._config.pbkdf2_iterations,
            "salt": base64.b64encode(salt).decode("utf-8"),
            "hash": base64.b64encode(digest).decode("utf-8"),
        }


class MaintenanceLock:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "maintenance.lock.json"
        self._mutex = threading.Lock()

    def acquire(self, action: str) -> dict[str, Any]:
        with self._mutex:
            if self._path.exists():
                try:
                    active = json.loads(self._path.read_text(encoding="utf-8"))
                except Exception:
                    active = {"action": "unknown"}
                raise RuntimeError(json.dumps(active))
            payload = {
                "lockVersion": 1,
                "lockId": secrets.token_hex(16),
                "jobId": secrets.token_hex(16),
                "action": action,
                "owner": "resolver-api",
                "createdAt": _utc_now_iso(),
                "updatedAt": _utc_now_iso(),
                "status": "acquired",
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            return payload

    def release(self) -> None:
        with self._mutex:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                return


class ResolverAPIHandler(BaseHTTPRequestHandler):
    config: ServiceConfig
    auth_store: AuthStore
    lock_store: MaintenanceLock
    action_engine: ActionEngine
    config_store: "RuntimeConfigStore"
    module_source_store: "ModuleSourceStore"
    rate_limiter: RequestRateLimiter

    def log_message(self, format: str, *args: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sys.stdout.write(f"[{now}] {self.address_string()} {format % args}\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[len("/api/v1/") :]
        if path == "/":
            if self.config.use_new_ui and self._serve_ui_index():
                return
            self._send_html(_html_home())
            return
        if path == "/app" or path.startswith("/app/"):
            if self.config.use_new_ui and self._serve_ui_index():
                return
            self._redirect("/")
            return
        if path.startswith("/assets/") and self.config.use_new_ui:
            if self._serve_static_file(self.config.ui_dist_dir / path.lstrip("/")):
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "asset_not_found"})
            return
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "passwordConfigured": self.auth_store.has_password(),
                "foundry": self._foundry_status(),
                "generatedAt": _utc_now_iso(),
            })
            return
        if path == "/api/auth/status":
            token = self._session_token()
            self._send_json(HTTPStatus.OK, {
                "passwordConfigured": self.auth_store.has_password(),
                "authenticated": self.auth_store.is_session_valid(token),
            })
            return
        if path == "/api/report/latest":
            if not self._require_auth():
                return
            report_path = self.config.reports_dir / "module-resolver-latest.json"
            if not report_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "latest_report_not_found"})
                return
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "failed_to_read_report"})
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        if path == "/api/report/v3":
            if self.config.use_new_ui and self.config.disable_legacy_report_ui:
                self._redirect("/app/report")
                return
            if not self.auth_store.has_password():
                self._redirect("/")
                return
            token = self._session_token()
            if not self.auth_store.is_session_valid(token):
                self._redirect("/")
                return
            if not self._require_auth():
                return
            report_path = self.config.reports_dir / "module-resolver-latest.html"
            if not report_path.exists():
                self._send_html(_html_report_v3_first_run())
                return
            try:
                html = report_path.read_text(encoding="utf-8")
            except OSError:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "failed_to_read_report_v3"})
                return
            self._send_html(html)
            return
        if path == "/api/report/v3/model":
            if not self._require_auth():
                return
            report_path = self.config.reports_dir / "module-resolver-latest.json"
            if not report_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "latest_report_not_found", "firstRunRequired": True})
                return
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "failed_to_read_report"})
                return
            report_views = payload.get("reportViews") if isinstance(payload.get("reportViews"), dict) else {}
            view = report_views.get("v3") if isinstance(report_views.get("v3"), dict) else {}
            backup_management = view.get("backupManagement") if isinstance(view.get("backupManagement"), dict) else {}
            try:
                backup_management["applyHistory"] = load_apply_history(str(self.config.state_dir / "resolver.db"), limit=30)
            except Exception:
                backup_management["applyHistory"] = []
            view["backupManagement"] = backup_management
            self._send_json(
                HTTPStatus.OK,
                {
                    "generatedAt": payload.get("generatedAt"),
                    "targetVersion": payload.get("targetVersion"),
                    "dataRoot": payload.get("dataRoot"),
                    "installedSystemVersions": payload.get("installedSystemVersions") or {},
                    "worldUsage": payload.get("worldUsage") or [],
                    "view": view,
                    "results": payload.get("results") or [],
                },
            )
            return
        if path == "/api/jobs":
            if not self._require_auth():
                return
            log_path = self.config.reports_dir / "module-resolver-latest.log"
            output = ""
            if log_path.exists():
                output = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            self._send_json(HTTPStatus.OK, {"tail": output})
            return
        if path == "/api/config/foundry-root":
            if not self._require_auth():
                return
            self._send_json(HTTPStatus.OK, self.config_store.status())
            return
        if path == "/api/config/module-sources":
            if not self._require_auth():
                return
            self._send_json(HTTPStatus.OK, {"sources": self.module_source_store.list_sources()})
            return
        if path == "/api/actions/jobs":
            if not self._require_auth():
                return
            self._send_json(HTTPStatus.OK, self.action_engine.list_jobs())
            return
        if path == "/api/actions/rollback-plan":
            if not self._require_auth():
                return
            qs = parse_qs(parsed.query or "")
            raw_scan = (qs.get("scanRunId") or [""])[0]
            try:
                scan_id = int(str(raw_scan).strip())
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "scan_run_id_required"})
                return
            history = load_apply_history(str(self.config.state_dir / "resolver.db"), limit=200)
            found = next((row for row in history if int(row.get("scanRunId") or 0) == scan_id), None)
            if not found:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "scan_run_not_found"})
                return
            backup_paths = [str(p).strip() for p in (found.get("backupPaths") or []) if str(p).strip()]
            modules = [str(m).strip() for m in (found.get("modulesChanged") or []) if str(m).strip()]
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "scanRunId": scan_id,
                    "generatedAt": found.get("generatedAt"),
                    "targetVersion": found.get("targetVersion"),
                    "modules": modules,
                    "backupPaths": backup_paths,
                    "notes": "Rollback execution is not yet automatic. Use backup paths to restore module folders.",
                },
            )
            return
        if path.startswith("/api/actions/jobs/"):
            if not self._require_auth():
                return
            job_id = parsed.path.rsplit("/", 1)[-1].strip()
            if not job_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "job_id_required"})
                return
            job = self.action_engine.get_job(job_id)
            if job is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "job_not_found"})
                return
            self._send_json(HTTPStatus.OK, job)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/v1/"):
            path = "/api/" + path[len("/api/v1/") :]
        allowed, retry_after = self.rate_limiter.allow(self._request_principal())
        if not allowed:
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate_limited", "retryAfterSeconds": retry_after},
            )
            return
        body = self._read_json_body()
        try:
            if path == "/api/auth/setup":
                self._handle_setup(body)
                return
            if path == "/api/auth/login":
                self._handle_login(body)
                return
            if path == "/api/auth/logout":
                self._handle_logout()
                return
            if path == "/api/actions/dry-run":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_dry_run(body)
                return
            if path == "/api/actions/apply":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_apply(body)
                return
            if path == "/api/actions/force-compat":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_force_compat(body)
                return
            if path == "/api/actions/cleanup-backups":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_cleanup_backups(body)
                return
            if path == "/api/actions/submit":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_submit_action(body)
                return
            if path == "/api/actions/suggest-module":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_suggest_module(body)
                return
            if path == "/api/config/foundry-root":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_set_foundry_root(body)
                return
            if path == "/api/config/foundry-root/pick":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_pick_foundry_root()
                return
            if path == "/api/config/foundry-root/reset":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self.config_store.set_data_root("")
                self._send_json(HTTPStatus.OK, self.config_store.status())
                return
            if path == "/api/config/module-sources":
                if not self._require_auth():
                    return
                if not self._require_csrf():
                    return
                self._handle_save_module_source(body)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(exc)})
        except _RequestAlreadyHandled:
            return

    def _handle_setup(self, body: dict[str, Any]) -> None:
        if self.auth_store.has_password():
            self._send_json(HTTPStatus.CONFLICT, {"error": "password_already_configured"})
            return
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        confirm = str(body.get("confirmPassword") or "")
        if password != confirm:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "password_confirmation_mismatch"})
            return
        try:
            self.auth_store.setup_credentials(username=username, password=password)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_credentials_setup", "message": str(exc)})
            return
        except RuntimeError:
            self._send_json(HTTPStatus.CONFLICT, {"error": "password_already_configured"})
            return
        token = self.auth_store.create_session()
        self._audit_event("auth_setup", {"ok": True})
        self._send_json(HTTPStatus.CREATED, {"ok": True}, cookies=self._session_cookie_headers(token))

    def _handle_login(self, body: dict[str, Any]) -> None:
        if not self.auth_store.has_password():
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": "password_not_configured"})
            return
        username = str(body.get("username") or "").strip()
        if self.auth_store.get_username():
            try:
                username = _normalize_username(username)
            except ValueError:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_credentials"})
                return
        principal = self._request_principal()
        lock_key = f"{principal}:{username.lower()}" if username else principal
        locked, remaining = self.auth_store.is_locked_out(lock_key)
        if locked:
            self._audit_event("auth_login_blocked", {"principal": principal, "username": username, "retryAfterSeconds": remaining})
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": "too_many_attempts",
                    "retryAfterSeconds": remaining,
                    "recoveryHint": "If you are locked out and cannot recover, stop the service and delete state/auth.json to reset credentials.",
                },
            )
            return
        password = str(body.get("password") or "")
        if not self.auth_store.verify_credentials(username=username, password=password):
            attempts, lock_seconds = self.auth_store.register_login_failure(lock_key)
            self._audit_event(
                "auth_login_failed",
                {"principal": principal, "username": username, "attempts": attempts, "lockoutSeconds": lock_seconds},
            )
            if lock_seconds is not None:
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {
                        "error": "too_many_attempts",
                        "retryAfterSeconds": lock_seconds,
                        "recoveryHint": "If you are locked out and cannot recover, stop the service and delete state/auth.json to reset credentials.",
                    },
                )
                return
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_credentials"})
            return
        self.auth_store.reset_login_failures(lock_key)
        token = self.auth_store.create_session()
        self._audit_event("auth_login_success", {"principal": principal, "username": username})
        self._send_json(HTTPStatus.OK, {"ok": True}, cookies=self._session_cookie_headers(token))

    def _handle_logout(self) -> None:
        token = self._session_token()
        self.auth_store.revoke_session(token)
        self._audit_event("auth_logout", {"tokenPresent": bool(token)})
        self._send_json(HTTPStatus.OK, {"ok": True}, cookies=self._clear_session_cookie_headers())

    def _handle_dry_run(self, body: dict[str, Any]) -> None:
        modules = _normalize_modules(body.get("modules"))
        extra_args = ["--dry-run"]
        for module_id in modules:
            extra_args.extend(["--module", module_id])
        batch_size = _parse_int(body.get("batchSize"), default=10, min_value=10)
        extra_args.extend(["--batch-size", str(batch_size)])
        result = self._run_cli(extra_args, maintenance=False)
        self._send_json(HTTPStatus.OK, result)

    def _handle_apply(self, body: dict[str, Any]) -> None:
        self._ensure_foundry_offline()
        modules = _normalize_modules(body.get("modules"))
        allow_downgrade = bool(body.get("allowDowngrade"))
        extra_args = ["--apply"]
        for module_id in modules:
            extra_args.extend(["--module", module_id])
        if allow_downgrade:
            extra_args.append("--allow-downgrade")
        batch_size = _parse_int(body.get("batchSize"), default=10, min_value=10)
        extra_args.extend(["--batch-size", str(batch_size)])
        result = self._run_cli(extra_args, maintenance=True, action="apply")
        self._send_json(HTTPStatus.OK, result)

    def _handle_force_compat(self, body: dict[str, Any]) -> None:
        self._ensure_foundry_offline()
        modules = _normalize_modules(body.get("modules"))
        if not modules:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "modules_required"})
            return
        target_version = str(body.get("targetVersion") or "").strip()
        if not target_version or not VERSION_RE.match(target_version):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_target_version"})
            return
        extra_args = []
        for module_id in modules:
            extra_args.extend(["--force-compat-module", module_id])
        extra_args.extend(["--force-compat-version", target_version])
        result = self._run_cli(extra_args, maintenance=True, action="force-compat")
        self._send_json(HTTPStatus.OK, result)

    def _handle_cleanup_backups(self, body: dict[str, Any]) -> None:
        self._ensure_foundry_offline()
        all_modules = bool(body.get("all"))
        modules = _normalize_modules(body.get("modules"))
        if not all_modules and not modules:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "cleanup_scope_required"})
            return
        extra_args = ["--cleanup-backups"]
        if all_modules:
            extra_args.append("--cleanup-backup-all")
        else:
            for module_id in modules:
                extra_args.extend(["--cleanup-backup-module", module_id])
        result = self._run_cli(extra_args, maintenance=True, action="cleanup-backups")
        self._send_json(HTTPStatus.OK, result)

    def _handle_submit_action(self, body: dict[str, Any]) -> None:
        action = str(body.get("action") or "").strip().lower()
        if action not in {"dry-run", "apply", "force-compat", "cleanup-backups"}:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unsupported_action"})
            return
        payload = body.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_payload"})
            return
        job = self.action_engine.enqueue(action=action, payload=payload)
        self._audit_event("action_enqueued", {"jobId": job.job_id, "action": action})
        self._send_json(
            HTTPStatus.ACCEPTED,
            {
                "ok": True,
                "jobId": job.job_id,
                "status": job.status,
                "action": action,
            },
        )

    def _handle_set_foundry_root(self, body: dict[str, Any]) -> None:
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "path_required"})
            return
        ok, normalized, details = _validate_foundry_root_path(raw_path)
        if not ok:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "invalid_foundry_root",
                    "message": details.get("message") or "Invalid Foundry root path.",
                    "details": details,
                },
            )
            return
        self.config_store.set_data_root(normalized)
        self._send_json(HTTPStatus.OK, self.config_store.status())

    def _handle_pick_foundry_root(self) -> None:
        try:
            selected = _pick_folder_native()
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "folder_picker_unavailable", "message": str(exc)},
            )
            return
        if not selected:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "folder_picker_cancelled"})
            return
        ok, normalized, details = _validate_foundry_root_path(selected)
        if not ok:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "invalid_foundry_root",
                    "message": details.get("message") or "Invalid Foundry root path.",
                    "selectedPath": selected,
                    "details": details,
                },
            )
            return
        self.config_store.set_data_root(normalized)
        payload = self.config_store.status()
        payload["selectedPath"] = selected
        self._send_json(HTTPStatus.OK, payload)

    def _handle_suggest_module(self, body: dict[str, Any]) -> None:
        module_id = str(body.get("moduleId") or "").strip()
        manifest_url = str(body.get("manifestUrl") or "").strip()
        project_url = str(body.get("projectUrl") or "").strip()
        if not manifest_url and not project_url:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "manifest_or_project_required"})
            return
        if not module_id and manifest_url:
            module_id = "manifest-derived"
        if not module_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "module_id_required"})
            return
        data_root = self.config_store.get_data_root() or self.config.data_root
        ok, normalized_root, details = _validate_foundry_root_path(data_root)
        if not ok:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_foundry_root", "message": details.get("message") or "Invalid Foundry root.", "details": details},
            )
            return
        try:
            foundry_version, source = detect_foundry_version(normalized_root)
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "foundry_version_unavailable", "message": str(exc)})
            return
        installed_system_versions = load_system_versions(normalized_root)
        try:
            module = _build_candidate_module(module_id, manifest_url=manifest_url, project_url=project_url)
            suggestion = _suggest_best_release_for_module(
                module=module,
                target_foundry_version=foundry_version,
                installed_system_versions=installed_system_versions,
                cache_dir=self.config.cache_dir,
            )
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "suggestion_failed", "message": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "moduleId": module_id,
                "foundryVersion": foundry_version,
                "foundryVersionSource": source,
                "dataRoot": normalized_root,
                "suggestion": suggestion,
            },
        )

    def _handle_save_module_source(self, body: dict[str, Any]) -> None:
        module_id = str(body.get("moduleId") or "").strip()
        manifest_url = str(body.get("manifestUrl") or "").strip()
        project_url = str(body.get("projectUrl") or "").strip()
        if not module_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "module_id_required"})
            return
        if not manifest_url and not project_url:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "manifest_or_project_required"})
            return
        # Validate by attempting a suggestion against current environment.
        try:
            suggestion = _suggest_best_release_for_module(
                module=_build_candidate_module(module_id, manifest_url=manifest_url, project_url=project_url),
                target_foundry_version=detect_foundry_version(self.config_store.get_data_root() or self.config.data_root)[0],
                installed_system_versions=load_system_versions(self.config_store.get_data_root() or self.config.data_root),
                cache_dir=self.config.cache_dir,
            )
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "source_validation_failed", "message": str(exc)})
            return
        saved = self.module_source_store.upsert_source(module_id=module_id, manifest_url=manifest_url, project_url=project_url)
        self._send_json(HTTPStatus.OK, {"ok": True, "saved": saved, "suggestion": suggestion})

    def _ensure_foundry_offline(self) -> None:
        if not self.config.require_foundry_offline:
            return
        status = self._foundry_status()
        if status["online"]:
            self._send_json(
                HTTPStatus.PRECONDITION_FAILED,
                {
                    "error": "maintenance_requires_foundry_offline",
                    "message": "Stop Foundry before running maintenance actions.",
                    "foundry": status,
                },
            )
            raise _RequestAlreadyHandled

    def _foundry_status(self) -> dict[str, Any]:
        online_tcp = False
        try:
            with socket.create_connection((self.config.foundry_host, self.config.foundry_port), timeout=1.5):
                online_tcp = True
        except OSError:
            online_tcp = False

        process_probe = _foundry_process_probe(
            process_name=self.config.foundry_process_name,
            data_root=self.config.data_root,
        )
        online_process = bool(process_probe.get("online"))
        host_lower = str(self.config.foundry_host or "").strip().lower()
        tcp_is_ambiguous = host_lower in {"host.docker.internal", "localhost", "127.0.0.1"}
        if tcp_is_ambiguous:
            # On local bridge hosts, an open port can belong to unrelated services.
            # Require a matching process signal to assert "online".
            online = bool(online_process)
            source = "process-required-localhost"
        else:
            online = bool(online_tcp or online_process)
            source = "tcp+process"
        confidence = "high" if online_process else ("low" if online_tcp else "none")
        return {
            "host": self.config.foundry_host,
            "port": self.config.foundry_port,
            "online": bool(online),
            "status": "online" if online else "offline",
            "source": source,
            "confidence": confidence,
            "probes": {
                "tcp": {
                    "online": bool(online_tcp),
                    "host": self.config.foundry_host,
                    "port": self.config.foundry_port,
                },
                "process": process_probe,
            },
        }

    def _run_cli(self, extra_args: list[str], maintenance: bool, action: str = "") -> dict[str, Any]:
        lock_payload: dict[str, Any] | None = None
        if maintenance:
            try:
                lock_payload = self.lock_store.acquire(action=action or "maintenance")
            except RuntimeError as exc:
                details: dict[str, Any] = {}
                try:
                    details = json.loads(str(exc))
                except json.JSONDecodeError:
                    details = {"raw": str(exc)}
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": "maintenance_lock_active",
                        "message": "Maintenance is already running.",
                        "lock": details,
                    },
                )
                raise _RequestAlreadyHandled

        try:
            effective_data_root = self.config_store.get_data_root() or self.config.data_root
            cmd = [
                self.config.python_bin,
                "-m",
                "resolver.cli",
                "--data-root",
                effective_data_root,
                "--cache-dir",
                self.config.cache_dir,
                "--database-path",
                str(self.config.state_dir / "resolver.db"),
                "--skip-foundry-service-control",
                "--json-output",
                str(self.config.reports_dir / "module-resolver-latest.json"),
                "--html-report",
                str(self.config.reports_dir / "module-resolver-latest.html"),
                "--log-file",
                str(self.config.reports_dir / "module-resolver-latest.log"),
                *extra_args,
            ]
            result = subprocess.run(
                cmd,
                cwd=str(self.config.tool_root),
                capture_output=True,
                text=True,
                check=False,
            )
            payload = {
                "ok": result.returncode == 0,
                "returnCode": result.returncode,
                "command": cmd,
                "stdout": (result.stdout or "")[-20000:],
                "stderr": (result.stderr or "")[-20000:],
                "lock": lock_payload,
                "generatedAt": _utc_now_iso(),
                "dataRoot": effective_data_root,
            }
            if result.returncode != 0:
                self._audit_event(
                    "action_failed",
                    {"action": action or "dry-run", "returnCode": result.returncode, "command": cmd},
                )
                self._send_json(HTTPStatus.BAD_REQUEST, payload)
                raise _RequestAlreadyHandled
            self._audit_event(
                "action_completed",
                {"action": action or "dry-run", "returnCode": result.returncode, "command": cmd},
            )
            return payload
        finally:
            if maintenance:
                self.lock_store.release()

    def _read_json_body(self) -> dict[str, Any]:
        length_raw = self.headers.get("Content-Length")
        if not length_raw:
            return {}
        try:
            length = int(length_raw)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _session_token(self) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except Exception:
            return None
        morsel = cookie.get("mm_session")
        if morsel is None:
            return None
        return morsel.value or None

    def _require_auth(self) -> bool:
        if not self.auth_store.has_password():
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": "password_not_configured"})
            return False
        token = self._session_token()
        if not self.auth_store.is_session_valid(token):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "auth_required"})
            return False
        return True

    def _require_csrf(self) -> bool:
        header_token = str(self.headers.get("X-CSRF-Token") or "").strip()
        cookie_token = self._csrf_token()
        if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
            return False
        return True

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any], cookies: list[str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_ui_index(self) -> bool:
        index_path = self.config.ui_dist_dir / "index.html"
        if not index_path.exists():
            return False
        try:
            html = index_path.read_text(encoding="utf-8")
        except OSError:
            return False
        self._send_html(html)
        return True

    def _serve_static_file(self, file_path: Path) -> bool:
        try:
            resolved = file_path.resolve()
            root = self.config.ui_dist_dir.resolve()
        except OSError:
            return False
        if root not in resolved.parents and resolved != root:
            return False
        if not resolved.exists() or not resolved.is_file():
            return False
        try:
            data = resolved.read_bytes()
        except OSError:
            return False
        content_type, _ = mimetypes.guess_type(str(resolved))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (content_type or "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        return True

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _session_cookie_headers(self, token: str) -> list[str]:
        cookie = SimpleCookie()
        cookie["mm_session"] = token
        cookie["mm_session"]["httponly"] = True
        cookie["mm_session"]["path"] = "/"
        cookie["mm_session"]["samesite"] = "Lax"
        if self.config.cookie_secure:
            cookie["mm_session"]["secure"] = True
        csrf = secrets.token_urlsafe(24)
        cookie["mm_csrf"] = csrf
        cookie["mm_csrf"]["path"] = "/"
        cookie["mm_csrf"]["samesite"] = "Lax"
        if self.config.cookie_secure:
            cookie["mm_csrf"]["secure"] = True
        return [morsel.OutputString() for morsel in cookie.values()]

    def _clear_session_cookie_headers(self) -> list[str]:
        cookie = SimpleCookie()
        cookie["mm_session"] = ""
        cookie["mm_session"]["path"] = "/"
        cookie["mm_session"]["max-age"] = 0
        cookie["mm_csrf"] = ""
        cookie["mm_csrf"]["path"] = "/"
        cookie["mm_csrf"]["max-age"] = 0
        return [morsel.OutputString() for morsel in cookie.values()]

    def _csrf_token(self) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except Exception:
            return None
        morsel = cookie.get("mm_csrf")
        if morsel is None:
            return None
        return morsel.value or None

    def _request_principal(self) -> str:
        host = str(self.client_address[0] if self.client_address else "unknown")
        return host.strip() or "unknown"

    def _audit_event(self, event: str, details: dict[str, Any]) -> None:
        payload = {
            "timestamp": _utc_now_iso(),
            "event": str(event or "unknown"),
            "path": str(self.path or ""),
            "method": str(self.command or ""),
            "principal": self._request_principal(),
            "userAgent": str(self.headers.get("User-Agent") or ""),
            "origin": str(self.headers.get("Origin") or ""),
            "details": details or {},
        }
        try:
            self.config.state_dir.mkdir(parents=True, exist_ok=True)
            with self.config.audit_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except OSError:
            return


class _RequestAlreadyHandled(Exception):
    pass


def _append_audit(config: ServiceConfig, event: str, details: dict[str, Any]) -> None:
    payload = {
        "timestamp": _utc_now_iso(),
        "event": str(event or "unknown"),
        "path": "background-worker",
        "method": "WORKER",
        "principal": "resolver-worker",
        "details": details or {},
    }
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        with config.audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except OSError:
        return


def _build_cli_args_from_action(action: str, payload: dict[str, Any]) -> tuple[list[str], bool, str]:
    normalized_action = str(action or "").strip().lower()
    modules = _normalize_modules(payload.get("modules"))
    batch_size = _parse_int(payload.get("batchSize"), default=10, min_value=10)
    if normalized_action == "dry-run":
        args = ["--dry-run"]
        for module_id in modules:
            args.extend(["--module", module_id])
        args.extend(["--batch-size", str(batch_size)])
        return args, False, "dry-run"
    if normalized_action == "apply":
        args = ["--apply"]
        for module_id in modules:
            args.extend(["--module", module_id])
        if bool(payload.get("allowDowngrade")):
            args.append("--allow-downgrade")
        args.extend(["--batch-size", str(batch_size)])
        return args, True, "apply"
    if normalized_action == "force-compat":
        target_version = str(payload.get("targetVersion") or "").strip()
        if not target_version or not VERSION_RE.match(target_version):
            raise ValueError("invalid_target_version")
        if not modules:
            raise ValueError("modules_required")
        args = []
        for module_id in modules:
            args.extend(["--force-compat-module", module_id])
        args.extend(["--force-compat-version", target_version])
        return args, True, "force-compat"
    if normalized_action == "cleanup-backups":
        args = ["--cleanup-backups"]
        all_modules = bool(payload.get("all"))
        if all_modules:
            args.append("--cleanup-backup-all")
        else:
            if not modules:
                raise ValueError("cleanup_scope_required")
            for module_id in modules:
                args.extend(["--cleanup-backup-module", module_id])
        return args, True, "cleanup-backups"
    raise ValueError("unsupported_action")


def _validate_foundry_root_path(raw_path: str) -> tuple[bool, str, dict[str, Any]]:
    path = Path(str(raw_path or "").strip()).expanduser()
    if not path.exists():
        return False, "", {"message": "Path does not exist.", "path": str(path)}

    candidate = path.resolve()
    if candidate.name.lower() == "data":
        candidate = candidate.parent

    required_dirs = [
        candidate / "Data",
        candidate / "Logs",
        candidate / "Config",
    ]
    missing = [str(item) for item in required_dirs if not item.exists() or not item.is_dir()]
    if missing:
        return False, "", {"message": "Missing required Foundry directories (Data/Logs/Config).", "missing": missing}

    diagnostics = candidate / "Logs" / "diagnostics.json"
    if not diagnostics.exists():
        return False, "", {"message": "Logs/diagnostics.json not found.", "path": str(candidate)}

    return True, str(candidate), {"message": "Foundry root validated.", "path": str(candidate)}


def _pick_folder_native() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("Native folder picker is unavailable on this environment.") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="Select Foundry root folder")
    finally:
        root.destroy()
    return str(selected or "").strip()


def _build_candidate_module(module_id: str, manifest_url: str, project_url: str) -> ModuleRecord:
    clean_id = str(module_id or "").strip()
    if manifest_url:
        request = Request(manifest_url, headers={"User-Agent": "foundry-module-version-resolver/0.1"})
        with urlopen(request, timeout=15) as response:
            manifest = json.load(response)
        resolved_id = str(manifest.get("id") or clean_id)
        return ModuleRecord(
            module_id=resolved_id,
            title=str(manifest.get("title") or resolved_id),
            version=str(manifest.get("version") or "0.0.0"),
            manifest_url=str(manifest.get("manifest") or manifest_url),
            project_url=str(manifest.get("url") or project_url or ""),
            path="",
            raw_manifest=manifest,
        )
    # Without manifest URL we still try project crawling; local manifest data is minimal fallback only.
    return ModuleRecord(
        module_id=clean_id,
        title=clean_id,
        version="0.0.0",
        manifest_url=None,
        project_url=project_url or None,
        path="",
        raw_manifest={"id": clean_id, "version": "0.0.0", "compatibility": {}},
    )


def _suggest_best_release_for_module(
    module: ModuleRecord,
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
    cache_dir: str,
) -> dict[str, Any]:
    release_limits = (5, 20, 50, 100)
    all_releases: list[Any] = []
    warnings: list[str] = []
    for limit in release_limits:
        releases, release_warnings = fetch_release_history(module, per_page=limit, cache_dir=cache_dir, force_refresh=True)
        all_releases = releases
        warnings = release_warnings
        valid = [item for item in releases if satisfies_release_constraints(item, target_foundry_version, installed_system_versions)]
        if valid or limit == release_limits[-1]:
            candidates = valid if valid else releases
            candidates = sorted(
                candidates,
                key=lambda item: candidate_sort_key(item, target_foundry_version, installed_system_versions),
                reverse=True,
            )
            if not candidates:
                raise ValueError("No releases could be resolved for this module.")
            best = candidates[0]
            return {
                "recommendedVersion": str(best.version or ""),
                "source": str(best.source or ""),
                "manifestUrl": best.manifest_url,
                "downloadUrl": best.download_url,
                "compatibility": best.compatibility or {},
                "systemCompatibility": best.system_compatibility or {},
                "checkedReleases": len(releases),
                "warnings": warnings,
                "isCompatible": bool(best in valid),
                "reason": (
                    "Best compatible release found for the current Foundry/system constraints."
                    if best in valid
                    else "No fully compatible release found; best fallback candidate returned."
                ),
            }
    raise ValueError("Suggestion flow could not resolve a release.")


def _foundry_process_probe(process_name: str, data_root: str) -> dict[str, Any]:
    clean_name = str(process_name or "").strip()
    if not clean_name:
        return {"online": False, "source": "disabled", "count": 0}
    if os.name == "nt":
        return _foundry_process_probe_windows(clean_name)
    return _foundry_process_probe_posix(clean_name)


def _foundry_process_probe_windows(process_name: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"online": False, "source": "tasklist-error", "error": str(exc), "count": 0}
    if result.returncode != 0:
        return {
            "online": False,
            "source": "tasklist-failed",
            "error": (result.stderr or "").strip(),
            "count": 0,
        }
    count = _count_windows_process_name_occurrences(result.stdout or "", process_name)
    return {
        "online": bool(count > 0),
        "source": "tasklist",
        "processName": process_name,
        "count": int(count),
    }


def _foundry_process_probe_posix(process_name: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {"online": False, "source": "ps-error", "error": str(exc), "count": 0}
    if result.returncode != 0:
        return {
            "online": False,
            "source": "ps-failed",
            "error": (result.stderr or "").strip(),
            "count": 0,
        }
    count = _count_posix_process_name_occurrences(result.stdout or "", process_name)
    return {
        "online": bool(count > 0),
        "source": "ps",
        "processName": process_name,
        "count": int(count),
    }


def _count_windows_process_name_occurrences(tasklist_csv: str, process_name: str) -> int:
    target = str(process_name or "").strip().lower()
    if not target:
        return 0
    rows = [line.strip() for line in str(tasklist_csv or "").splitlines() if line.strip()]
    count = 0
    for row in rows:
        # CSV format with quoted fields; image name is first field.
        first = row.split(",", 1)[0].strip().strip('"').lower()
        if first == target:
            count += 1
    return count


def _count_posix_process_name_occurrences(ps_comm_output: str, process_name: str) -> int:
    target = str(process_name or "").strip().lower()
    if not target:
        return 0
    rows = [line.strip().lower() for line in str(ps_comm_output or "").splitlines() if line.strip()]
    return sum(1 for row in rows if row == target)


def _execute_action_job(
    config: ServiceConfig,
    config_store: RuntimeConfigStore,
    lock_store: MaintenanceLock,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    effective_data_root = config_store.get_data_root() or config.data_root
    extra_args, maintenance, action_name = _build_cli_args_from_action(action, payload)
    lock_payload: dict[str, Any] | None = None
    if maintenance:
        lock_payload = lock_store.acquire(action=action_name)
    try:
        cmd = [
            config.python_bin,
            "-m",
            "resolver.cli",
            "--data-root",
            effective_data_root,
            "--cache-dir",
            config.cache_dir,
            "--database-path",
            str(config.state_dir / "resolver.db"),
            "--skip-foundry-service-control",
            "--json-output",
            str(config.reports_dir / "module-resolver-latest.json"),
            "--html-report",
            str(config.reports_dir / "module-resolver-latest.html"),
            "--log-file",
            str(config.reports_dir / "module-resolver-latest.log"),
            *extra_args,
        ]
        result = subprocess.run(
            cmd,
            cwd=str(config.tool_root),
            capture_output=True,
            text=True,
            check=False,
        )
        output = {
            "ok": result.returncode == 0,
            "returnCode": result.returncode,
            "command": cmd,
            "stdout": (result.stdout or "")[-20000:],
            "stderr": (result.stderr or "")[-20000:],
            "lock": lock_payload,
            "generatedAt": _utc_now_iso(),
            "dataRoot": effective_data_root,
        }
        if result.returncode != 0:
            raise RuntimeError(output.get("stderr") or f"Action failed with returnCode={result.returncode}")
        return output
    finally:
        if maintenance:
            lock_store.release()


def _start_action_worker(
    config: ServiceConfig,
    config_store: RuntimeConfigStore,
    engine: ActionEngine,
    lock_store: MaintenanceLock,
) -> threading.Thread:
    def _worker_loop() -> None:
        while True:
            job = engine.pick_next()
            if job is None:
                threading.Event().wait(0.25)
                continue
            engine.set_progress(job.job_id, 25)
            _append_audit(config, "action_worker_started", {"jobId": job.job_id, "action": job.action})
            try:
                engine.set_progress(job.job_id, 60)
                result = _execute_action_job(config, config_store, lock_store, job.action, job.payload)
                engine.set_progress(job.job_id, 95)
                engine.complete(job.job_id, ok=True, result=result)
                _append_audit(config, "action_worker_success", {"jobId": job.job_id, "action": job.action})
            except Exception as exc:
                engine.complete(job.job_id, ok=False, result=None, error=str(exc))
                _append_audit(config, "action_worker_failed", {"jobId": job.job_id, "action": job.action, "error": str(exc)})

    thread = threading.Thread(target=_worker_loop, daemon=True, name="resolver-action-worker")
    thread.start()
    return thread


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_int(raw: Any, default: int, min_value: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < min_value:
        return min_value
    return value


def _normalize_username(raw: str) -> str:
    value = str(raw or "").strip()
    if not USERNAME_RE.match(value):
        raise ValueError("Username must be 3-32 chars and use only letters, numbers, dot, underscore or dash.")
    return value


def _validate_password_policy(password: str, username: str) -> None:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")
    lowered = password.lower()
    if username and username.lower() in lowered:
        raise ValueError("Password must not include the username.")
    has_lower = any(ch.islower() for ch in password)
    has_upper = any(ch.isupper() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    has_symbol = any(not ch.isalnum() for ch in password)
    if not (has_lower and has_upper and has_digit and has_symbol):
        raise ValueError("Password must include upper, lower, number and symbol.")


def _normalize_modules(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        return []
    normalized: list[str] = []
    for value in values:
        module_id = str(value).strip()
        if not module_id:
            continue
        if not MODULE_ID_RE.match(module_id):
            raise ValueError(f"Invalid module id: {module_id}")
        normalized.append(module_id)
    return normalized


def _html_home() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Foundry Resolver Login</title>
  <style>
    :root {
      --bg: #0a0f1a;
      --bg2: #111827;
      --panel: #111827;
      --panel2: #172033;
      --ink: #e5e7eb;
      --muted: #94a3b8;
      --line: #263245;
      --brand: #22c55e;
      --brand2: #16a34a;
      --danger: #f87171;
    }
    *{box-sizing:border-box}
    body{
      margin:0;min-height:100vh;display:grid;place-items:center;padding:14px;
      background:
        radial-gradient(920px 460px at 8% -12%, #1e293b 0%, rgba(30,41,59,0) 65%),
        radial-gradient(760px 400px at 100% -5%, #0f766e22 0%, rgba(15,118,110,0) 60%),
        linear-gradient(180deg, var(--bg), var(--bg2));
      font-family:\"Segoe UI Variable\",\"Aptos\",\"Segoe UI\",Tahoma,sans-serif;color:var(--ink)
    }
    .card{
      width:min(420px,100%);background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);border-radius:16px;padding:18px;
      box-shadow:0 20px 50px rgba(0,0,0,.35)
    }
    h1{margin:0 0 6px;font-size:1.08rem;letter-spacing:.01em}
    p{margin:0 0 12px;color:var(--muted);font-size:.9rem;line-height:1.35}
    input{
      width:100%;padding:12px;border:1px solid #334155;border-radius:12px;margin-bottom:10px;
      background:#0b1220;color:var(--ink);outline:none
    }
    input:focus{border-color:#22c55e66;box-shadow:0 0 0 3px #22c55e22}
    button{
      width:100%;padding:11px;border:0;border-radius:12px;background:var(--brand);
      color:#052e16;font-weight:700;cursor:pointer
    }
    button:hover{background:var(--brand2);color:#dcfce7}
    button[disabled]{opacity:.65;cursor:not-allowed}
    .alt{background:#334155;color:#e2e8f0;margin-top:8px}
    .hidden{display:none!important}
    .chip{display:inline-block;padding:4px 10px;border:1px solid var(--line);border-radius:999px;font-size:.78rem;color:var(--muted)}
    .err{min-height:18px;margin-top:4px;color:var(--danger);font-size:.82rem}
    .locale-row{display:flex;justify-content:flex-end;margin-bottom:8px}
    .locale-select{background:#0b1220;color:var(--ink);border:1px solid #334155;border-radius:10px;padding:6px 8px}
  </style>
</head>
<body>
  <main class=\"card\">
    <div class=\"locale-row\">
      <select id=\"localeSelect\" class=\"locale-select\" aria-label=\"Language\">
        <option value=\"en\">English</option>
        <option value=\"pt-BR\">Português (Brasil)</option>
      </select>
    </div>
    <h1 id=\"title\">Foundry Resolver</h1>
    <span id=\"authChip\" class=\"chip\">Checking session...</span>
    <p id=\"status\">Loading authentication status...</p>
    <div id=\"setupFields\" class=\"hidden\">
      <input id=\"setupUser\" type=\"text\" placeholder=\"Create username\" autocomplete=\"username\" minlength=\"3\" maxlength=\"32\">
      <input id=\"setupPass\" type=\"password\" placeholder=\"Create password\" autocomplete=\"new-password\" minlength=\"8\">
      <input id=\"setupPass2\" type=\"password\" placeholder=\"Confirm password\" autocomplete=\"new-password\" minlength=\"8\">
      <button id=\"setupBtn\" onclick=\"setupPassword()\">Create Password</button>
    </div>
    <div id=\"loginFields\" class=\"hidden\">
      <input id=\"loginUser\" type=\"text\" placeholder=\"Username\" autocomplete=\"username\">
      <input id=\"loginPass\" type=\"password\" placeholder=\"Password\" autocomplete=\"current-password\">
      <button id=\"loginBtn\" onclick=\"login()\">Login</button>
    </div>
    <div id=\"loggedInControls\" class=\"hidden\">
      <button class=\"alt\" onclick=\"logout()\">Logout</button>
    </div>
    <p id=\"error\" class=\"err\"></p>
  </main>
<script>
const I18N = {
  'en': {
    title: 'Foundry Resolver',
    checking: 'Checking session...',
    loading: 'Loading authentication status...',
    setupPass: 'Create password',
    setupUser: 'Create username',
    setupPass2: 'Confirm password',
    createBtn: 'Create Password',
    loginUser: 'Username',
    loginPass: 'Password',
    loginBtn: 'Login',
    logoutBtn: 'Logout',
    authenticated: 'Authenticated',
    loginRequired: 'Login required',
    setupRequired: 'Setup required',
  },
  'pt-BR': {
    title: 'Foundry Resolver',
    checking: 'Verificando sessão...',
    loading: 'Carregando status de autenticação...',
    setupPass: 'Criar senha',
    setupUser: 'Criar usuário',
    setupPass2: 'Confirmar senha',
    createBtn: 'Criar Senha',
    loginUser: 'Usuário',
    loginPass: 'Senha',
    loginBtn: 'Entrar',
    logoutBtn: 'Sair',
    authenticated: 'Autenticado',
    loginRequired: 'Login necessário',
    setupRequired: 'Configuração necessária',
  }
};
let CURRENT_LOCALE = localStorage.getItem('resolver-locale') || 'en';
function t(key) {
  const table = I18N[CURRENT_LOCALE] || I18N['en'];
  return table[key] || I18N['en'][key] || key;
}
function applyLocale() {
  document.getElementById('localeSelect').value = CURRENT_LOCALE;
  document.getElementById('title').textContent = t('title');
  document.getElementById('authChip').textContent = t('checking');
  document.getElementById('status').textContent = t('loading');
  document.getElementById('setupPass').placeholder = t('setupPass');
  document.getElementById('setupUser').placeholder = t('setupUser');
  document.getElementById('setupPass2').placeholder = t('setupPass2');
  document.getElementById('setupBtn').textContent = t('createBtn');
  document.getElementById('loginUser').placeholder = t('loginUser');
  document.getElementById('loginPass').placeholder = t('loginPass');
  document.getElementById('loginBtn').textContent = t('loginBtn');
  const logoutBtn = document.querySelector('#loggedInControls button');
  if (logoutBtn) logoutBtn.textContent = t('logoutBtn');
}
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': ((document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith('mm_csrf=')) || '').split('=',2)[1] || '') },
    body: body ? JSON.stringify(body) : null,
  });
  const contentType = res.headers.get('content-type') || '';
  const text = await res.text();
  let parsed = { raw: text };
  if (contentType.includes('application/json')) {
    try { parsed = JSON.parse(text); } catch {}
  }
  if (!res.ok) throw { status: res.status, body: parsed };
  return parsed;
}
function setError(message) {
  const errorEl = document.getElementById('error');
  errorEl.textContent = message || '';
}
function setAuthUi(auth) {
  const setup = document.getElementById('setupFields');
  const login = document.getElementById('loginFields');
  const controls = document.getElementById('loggedInControls');
  const chip = document.getElementById('authChip');
  const configured = !!auth.passwordConfigured;
  const authenticated = !!auth.authenticated;
  setup.classList.toggle('hidden', configured || authenticated);
  login.classList.toggle('hidden', !configured || authenticated);
  controls.classList.toggle('hidden', !authenticated);
  setError('');
  chip.textContent = authenticated ? t('authenticated') : (configured ? t('loginRequired') : t('setupRequired'));
}
async function refreshStatus() {
  try {
    const health = await api('/api/health');
    const auth = await api('/api/auth/status');
    document.getElementById('status').innerHTML =
      `Foundry: <b>${health.foundry.status}</b> (${health.foundry.host}:${health.foundry.port})<br>` +
      `Password configured: <b>${auth.passwordConfigured}</b> | Authenticated: <b>${auth.authenticated}</b>`;
    setAuthUi(auth);
    if (auth.authenticated) {
      window.location.replace('/app/report');
    } else {
    }
  } catch (err) {
    console.error(err);
  }
}
async function setupPassword() {
  const password = document.getElementById('setupPass').value;
  const username = document.getElementById('setupUser').value;
  const confirmPassword = document.getElementById('setupPass2').value;
  const button = document.getElementById('setupBtn');
  try {
    button.disabled = true;
    await api('/api/auth/setup','POST',{username,password,confirmPassword});
    window.location.replace('/app/report');
    return;
  } catch (err) {
    setError(String(err?.body?.message || 'Unable to create password.'));
  } finally {
    button.disabled = false;
  }
  await refreshStatus();
}
async function login() {
  const password = document.getElementById('loginPass').value;
  const username = document.getElementById('loginUser').value;
  const button = document.getElementById('loginBtn');
  try {
    button.disabled = true;
    await api('/api/auth/login','POST',{username,password});
    window.location.replace('/app/report');
    return;
  } catch (err) {
    setError(String(err?.body?.message || 'Login failed.'));
  } finally {
    button.disabled = false;
  }
  await refreshStatus();
}
async function logout() {
  try { await api('/api/auth/logout','POST',{}); }
  catch (err) { console.error(err); }
  await refreshStatus();
}
refreshStatus();
applyLocale();
document.getElementById('localeSelect').addEventListener('change', function () {
  CURRENT_LOCALE = this.value || 'en';
  localStorage.setItem('resolver-locale', CURRENT_LOCALE);
  applyLocale();
  refreshStatus();
});
</script>
</body></html>
"""


def _html_report_v3_first_run() -> str:
    payload = {
        "dataRoot": "",
        "reportViews": {
            "v3": {
                "currentFoundryVersion": "-",
                "generatedAt": _utc_now_iso(),
                "summary": {"usedModuleCount": 0},
                "controls": {"defaultFoundryVersion": ""},
                "currentSystemUpgrades": {"rows": []},
                "systemUpgradePlanner": {"targets": []},
                "backupManagement": {"rows": [], "totalBackupCount": 0},
                "unusedModules": {"rows": [], "count": 0},
            }
        },
    }
    html = render_html_report_v3(payload)
    panel = """
<section class=\"panel subtle\" id=\"first-run-panel\">
  <h3>Initial setup</h3>
  <p class=\"section-copy\">Run the initial scan to generate your first report.</p>
  <div class=\"toolbar\">
    <button id=\"first-run-btn\" class=\"copy-btn\" type=\"button\" style=\"display:none;\">Start Initial Scan</button>
  </div>
  <div class=\"progress-wrap\" aria-hidden=\"true\" style=\"margin-top:10px;height:10px;border-radius:999px;background:#e2e8f0;overflow:hidden;\">
    <div id=\"first-run-progress\" style=\"height:100%;width:0%;background:linear-gradient(90deg,#0f766e,#115e59);transition:width .25s ease;\"></div>
  </div>
  <p id=\"first-run-status\" class=\"pager-status\">Waiting to start.</p>
</section>
<script>
(function () {
  const btn = document.getElementById("first-run-btn");
  const statusEl = document.getElementById("first-run-status");
  const progressEl = document.getElementById("first-run-progress");
  if (!btn || !statusEl || !progressEl) return;
  async function api(path, method, body) {
    const response = await fetch(path, {
      method: method || "GET",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": ((document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith('mm_csrf=')) || '').split('=',2)[1] || '') },
      body: body ? JSON.stringify(body) : null,
    });
    const text = await response.text();
    let payload = {};
    try { payload = JSON.parse(text || "{}"); } catch { payload = { raw: text }; }
    if (!response.ok) throw new Error(payload.message || payload.error || ("HTTP " + response.status));
    return payload;
  }
  function setStatus(text) { statusEl.textContent = text || ""; }
  function setProgress(p) {
    const value = Math.max(0, Math.min(100, Number(p || 0)));
    progressEl.style.width = String(value) + "%";
  }
  async function waitForJob(jobId) {
    while (true) {
      const job = await api("/api/actions/jobs/" + encodeURIComponent(jobId), "GET");
      const progress = Number(job.progress || 0);
      setProgress(progress);
      setStatus("Processing... " + Math.max(0, Math.min(100, progress)) + "%");
      if (job.status === "success") {
        setProgress(100);
        setStatus("Done. Redirecting...");
        window.location.replace("/app/report?t=" + Date.now());
        return;
      }
      if (job.status === "failed") {
        setStatus("Could not generate the report. Please try again.");
        btn.disabled = false;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }
  btn.addEventListener("click", async function () {
    try {
      btn.disabled = true;
      setProgress(5);
      setStatus("Preparing your request...");
      const submitted = await api("/api/actions/submit", "POST", { action: "dry-run", payload: { batchSize: 10 } });
      if (!submitted.jobId) throw new Error("missing_job_id");
      await waitForJob(submitted.jobId);
    } catch (_err) {
      setStatus("Could not start the process. Please try again.");
      btn.disabled = false;
    }
  });
})();
</script>
"""
    return html.replace("</header>", "</header>\n" + panel, 1)


def load_config() -> ServiceConfig:
    tool_root = Path(__file__).resolve().parent.parent
    state_dir = Path(os.environ.get("RESOLVER_STATE_DIR") or (tool_root / "state"))
    reports_dir = Path(os.environ.get("RESOLVER_REPORTS_DIR") or (tool_root / "reports"))
    auth_file = Path(os.environ.get("RESOLVER_AUTH_FILE") or (state_dir / "auth.json"))
    audit_file = Path(os.environ.get("RESOLVER_AUDIT_FILE") or (state_dir / "audit.log.jsonl"))
    config = ServiceConfig(
        tool_root=tool_root,
        data_root=os.environ.get("RESOLVER_DATA_ROOT") or "/foundry-data",
        cache_dir=os.environ.get("RESOLVER_CACHE_DIR") or str(tool_root / ".cache"),
        reports_dir=reports_dir,
        state_dir=state_dir,
        auth_file=auth_file,
        bind_host=os.environ.get("RESOLVER_BIND_HOST") or "0.0.0.0",
        bind_port=_parse_int(os.environ.get("RESOLVER_BIND_PORT"), default=8787, min_value=1),
        python_bin=os.environ.get("RESOLVER_PYTHON_BIN") or sys.executable,
        session_ttl_hours=_parse_int(os.environ.get("RESOLVER_SESSION_TTL_HOURS"), default=12, min_value=1),
        pbkdf2_iterations=_parse_int(os.environ.get("RESOLVER_PBKDF2_ITERATIONS"), default=390000, min_value=100000),
        require_foundry_offline=(os.environ.get("RESOLVER_REQUIRE_FOUNDRY_OFFLINE", "true").strip().lower() == "true"),
        foundry_host=os.environ.get("RESOLVER_FOUNDRY_HOST") or "127.0.0.1",
        foundry_port=_parse_int(os.environ.get("RESOLVER_FOUNDRY_PORT"), default=30000, min_value=1),
        foundry_process_name=os.environ.get("RESOLVER_FOUNDRY_PROCESS_NAME") or "Foundry Virtual Tabletop.exe",
        cookie_secure=(os.environ.get("RESOLVER_COOKIE_SECURE", "false").strip().lower() == "true"),
        auth_max_failed_attempts=_parse_int(os.environ.get("RESOLVER_AUTH_MAX_FAILED_ATTEMPTS"), default=5, min_value=1),
        auth_lockout_minutes=_parse_int(os.environ.get("RESOLVER_AUTH_LOCKOUT_MINUTES"), default=15, min_value=1),
        request_rate_limit_per_minute=_parse_int(os.environ.get("RESOLVER_REQUEST_RATE_LIMIT_PER_MINUTE"), default=120, min_value=10),
        max_sessions=_parse_int(os.environ.get("RESOLVER_MAX_SESSIONS"), default=200, min_value=1),
        audit_file=audit_file,
        use_new_ui=(os.environ.get("USE_NEW_UI", "false").strip().lower() == "true"),
        ui_dist_dir=Path(os.environ.get("RESOLVER_UI_DIST_DIR") or (tool_root / "frontend" / "dist")),
        disable_legacy_report_ui=(os.environ.get("RESOLVER_DISABLE_LEGACY_REPORT_UI", "false").strip().lower() == "true"),
    )
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return config


def run() -> None:
    config = load_config()
    auth_store = AuthStore(config)
    lock_store = MaintenanceLock(config.state_dir)
    action_engine = ActionEngine()
    config_store = RuntimeConfigStore(config)
    module_source_store = ModuleSourceStore(config)

    handler = ResolverAPIHandler
    handler.config = config
    handler.auth_store = auth_store
    handler.lock_store = lock_store
    handler.action_engine = action_engine
    handler.config_store = config_store
    handler.module_source_store = module_source_store
    handler.rate_limiter = RequestRateLimiter(config.request_rate_limit_per_minute)

    _start_action_worker(config, config_store, action_engine, lock_store)

    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    print(f"[resolver-api] listening on http://{config.bind_host}:{config.bind_port}")
    server.serve_forever()


if __name__ == "__main__":
    run()



