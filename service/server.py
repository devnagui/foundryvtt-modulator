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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MODULE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


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
    cookie_secure: bool
    auth_max_failed_attempts: int
    auth_lockout_minutes: int
    max_sessions: int
    audit_file: Path


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

    def setup_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must contain at least 8 characters.")
        with self._lock:
            payload = self._read_auth_payload()
            if isinstance(payload.get("password"), dict):
                raise RuntimeError("Password already configured.")
            now = _utc_now_iso()
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

    def log_message(self, format: str, *args: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sys.stdout.write(f"[{now}] {self.address_string()} {format % args}\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_html_home())
            return
        if parsed.path == "/api/health":
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "passwordConfigured": self.auth_store.has_password(),
                "foundry": self._foundry_status(),
                "generatedAt": _utc_now_iso(),
            })
            return
        if parsed.path == "/api/auth/status":
            token = self._session_token()
            self._send_json(HTTPStatus.OK, {
                "passwordConfigured": self.auth_store.has_password(),
                "authenticated": self.auth_store.is_session_valid(token),
            })
            return
        if parsed.path == "/api/report/latest":
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
        if parsed.path == "/api/report/v3":
            if not self._require_auth():
                return
            report_path = self.config.reports_dir / "module-resolver-latest-v3.html"
            if not report_path.exists():
                self._send_html(_html_report_bootstrap())
                return
            try:
                html = report_path.read_text(encoding="utf-8")
            except OSError:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "failed_to_read_report_v3"})
                return
            self._send_html(html)
            return
        if parsed.path == "/api/jobs":
            if not self._require_auth():
                return
            log_path = self.config.reports_dir / "module-resolver-latest.log"
            output = ""
            if log_path.exists():
                output = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            self._send_json(HTTPStatus.OK, {"tail": output})
            return
        if parsed.path == "/api/actions/jobs":
            if not self._require_auth():
                return
            self._send_json(HTTPStatus.OK, self.action_engine.list_jobs())
            return
        if parsed.path.startswith("/api/actions/jobs/"):
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
        body = self._read_json_body()
        try:
            if parsed.path == "/api/auth/setup":
                self._handle_setup(body)
                return
            if parsed.path == "/api/auth/login":
                self._handle_login(body)
                return
            if parsed.path == "/api/auth/logout":
                self._handle_logout()
                return
            if parsed.path == "/api/actions/dry-run":
                if not self._require_auth():
                    return
                self._handle_dry_run(body)
                return
            if parsed.path == "/api/actions/apply":
                if not self._require_auth():
                    return
                self._handle_apply(body)
                return
            if parsed.path == "/api/actions/force-compat":
                if not self._require_auth():
                    return
                self._handle_force_compat(body)
                return
            if parsed.path == "/api/actions/cleanup-backups":
                if not self._require_auth():
                    return
                self._handle_cleanup_backups(body)
                return
            if parsed.path == "/api/actions/submit":
                if not self._require_auth():
                    return
                self._handle_submit_action(body)
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
        password = str(body.get("password") or "")
        confirm = str(body.get("confirmPassword") or "")
        if password != confirm:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "password_confirmation_mismatch"})
            return
        try:
            self.auth_store.setup_password(password)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_password", "message": str(exc)})
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
        principal = self._request_principal()
        locked, remaining = self.auth_store.is_locked_out(principal)
        if locked:
            self._audit_event("auth_login_blocked", {"principal": principal, "retryAfterSeconds": remaining})
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "too_many_attempts", "retryAfterSeconds": remaining},
            )
            return
        password = str(body.get("password") or "")
        if not self.auth_store.verify_password(password):
            attempts, lock_seconds = self.auth_store.register_login_failure(principal)
            self._audit_event(
                "auth_login_failed",
                {"principal": principal, "attempts": attempts, "lockoutSeconds": lock_seconds},
            )
            if lock_seconds is not None:
                self._send_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": "too_many_attempts", "retryAfterSeconds": lock_seconds},
                )
                return
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_credentials"})
            return
        self.auth_store.reset_login_failures(principal)
        token = self.auth_store.create_session()
        self._audit_event("auth_login_success", {"principal": principal})
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
        online = False
        source = "tcp"
        try:
            with socket.create_connection((self.config.foundry_host, self.config.foundry_port), timeout=1.5):
                online = True
        except OSError:
            online = False
        return {
            "host": self.config.foundry_host,
            "port": self.config.foundry_port,
            "online": online,
            "status": "online" if online else "offline",
            "source": source,
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
            cmd = [
                self.config.python_bin,
                "-m",
                "resolver.cli",
                "--data-root",
                self.config.data_root,
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

    def _session_cookie_headers(self, token: str) -> list[str]:
        cookie = SimpleCookie()
        cookie["mm_session"] = token
        cookie["mm_session"]["httponly"] = True
        cookie["mm_session"]["path"] = "/"
        cookie["mm_session"]["samesite"] = "Lax"
        if self.config.cookie_secure:
            cookie["mm_session"]["secure"] = True
        return [morsel.OutputString() for morsel in cookie.values()]

    def _clear_session_cookie_headers(self) -> list[str]:
        cookie = SimpleCookie()
        cookie["mm_session"] = ""
        cookie["mm_session"]["path"] = "/"
        cookie["mm_session"]["max-age"] = 0
        return [morsel.OutputString() for morsel in cookie.values()]

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


def _execute_action_job(config: ServiceConfig, lock_store: MaintenanceLock, action: str, payload: dict[str, Any]) -> dict[str, Any]:
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
            config.data_root,
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
        }
        if result.returncode != 0:
            raise RuntimeError(output.get("stderr") or f"Action failed with returnCode={result.returncode}")
        return output
    finally:
        if maintenance:
            lock_store.release()


def _start_action_worker(config: ServiceConfig, engine: ActionEngine, lock_store: MaintenanceLock) -> threading.Thread:
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
                result = _execute_action_job(config, lock_store, job.action, job.payload)
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
  <title>Foundry Resolver</title>
  <style>
    :root {
      --bg: #f4f6fb;
      --surface: #ffffff;
      --surface-soft: #f8fafc;
      --ink: #0f172a;
      --muted: #475569;
      --line: #dbe3ef;
      --brand: #0f766e;
      --brand-strong: #115e59;
      --danger: #b91c1c;
      --radius: 14px;
      --shadow: 0 18px 50px rgba(2, 6, 23, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI Variable", "Segoe UI", "Aptos", Tahoma, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(900px 400px at 90% -80px, #99f6e4 0%, rgba(153,246,228,0) 65%),
        radial-gradient(700px 360px at -120px -120px, #bfdbfe 0%, rgba(191,219,254,0) 60%),
        var(--bg);
      min-height: 100vh;
    }
    .shell {
      width: min(1240px, 100% - 24px);
      margin: 12px auto;
      display: grid;
      grid-template-columns: 330px 1fr;
      gap: 12px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }
    .brand h1 {
      font-size: 1.06rem;
      margin: 0;
      letter-spacing: .02em;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: .77rem;
      padding: 5px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--surface-soft);
      color: var(--muted);
      white-space: nowrap;
    }
    .section {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      background: var(--surface-soft);
      margin-top: 10px;
    }
    .section h2 {
      margin: 0 0 8px;
      font-size: .86rem;
      text-transform: uppercase;
      letter-spacing: .07em;
      color: #334155;
    }
    .muted {
      color: var(--muted);
      font-size: .84rem;
      line-height: 1.35;
    }
    input {
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
      padding: 10px 11px;
      font-size: .92rem;
      margin-bottom: 8px;
    }
    button {
      border: 0;
      border-radius: 10px;
      padding: 10px 12px;
      cursor: pointer;
      background: var(--brand);
      color: #fff;
      font-weight: 600;
      width: 100%;
    }
    button:hover { background: var(--brand-strong); }
    button.alt { background: #334155; margin-top: 8px; }
    button.warn { background: var(--danger); }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .report {
      min-height: calc(100vh - 24px);
      padding: 0;
      overflow: hidden;
    }
    #reportFrame {
      border: 0;
      width: 100%;
      height: calc(100vh - 26px);
      background: #fff;
      display: block;
    }
    .hidden { display: none !important; }
    @media (max-width: 1024px) {
      .shell { grid-template-columns: 1fr; width: calc(100% - 12px); margin: 6px auto; }
      .report { min-height: 70vh; }
      #reportFrame { height: 72vh; }
    }
  </style>
</head>
<body>
  <main class=\"shell\">
    <aside class=\"panel\">
      <div class=\"brand\">
        <h1>Foundry Resolver</h1>
        <span id=\"authChip\" class=\"chip\">Checking session...</span>
      </div>
      <div id=\"status\" class=\"muted\">Loading authentication status...</div>

      <section id=\"authCard\" class=\"section\">
        <h2>Authentication</h2>
        <div id=\"setupFields\" class=\"hidden\">
          <input id=\"setupPass\" type=\"password\" placeholder=\"Create password\">
          <input id=\"setupPass2\" type=\"password\" placeholder=\"Confirm password\">
          <button onclick=\"setupPassword()\">Create Password</button>
        </div>
        <div id=\"loginFields\" class=\"hidden\">
          <input id=\"loginPass\" type=\"password\" placeholder=\"Password\">
          <button onclick=\"login()\">Login</button>
        </div>
        <div id=\"loggedInControls\" class=\"hidden\">
          <button class=\"alt\" onclick=\"logout()\">Logout</button>
        </div>
        <p class=\"muted\">Password hash persists in <code>state/auth.json</code>.</p>
      </section>

    </aside>

    <section class=\"panel report\">
      <div class=\"muted\" style=\"padding:16px\">Authenticate to open Report v3.</div>
    </section>
  </main>

<script>
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
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
function modulesFromInput() {
  return [];
}
function setAuthUi(auth) {
  const setup = document.getElementById('setupFields');
  const login = document.getElementById('loginFields');
  const controls = document.getElementById('loggedInControls');
  const card = document.getElementById('authCard');
  const chip = document.getElementById('authChip');
  const configured = !!auth.passwordConfigured;
  const authenticated = !!auth.authenticated;
  setup.classList.toggle('hidden', configured || authenticated);
  login.classList.toggle('hidden', !configured || authenticated);
  controls.classList.toggle('hidden', !authenticated);
  card.classList.toggle('hidden', authenticated);
  chip.textContent = authenticated ? 'Authenticated' : (configured ? 'Login required' : 'Setup required');
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
      window.location.replace('/api/report/v3');
    } else {
    }
  } catch (err) {
    console.error(err);
  }
}
async function setupPassword() {
  const password = document.getElementById('setupPass').value;
  const confirmPassword = document.getElementById('setupPass2').value;
  try {
    await api('/api/auth/setup','POST',{password,confirmPassword});
    window.location.replace('/api/report/v3');
    return;
  } catch (err) { console.error(err); }
  await refreshStatus();
}
async function login() {
  const password = document.getElementById('loginPass').value;
  try {
    await api('/api/auth/login','POST',{password});
    window.location.replace('/api/report/v3');
    return;
  } catch (err) { console.error(err); }
  await refreshStatus();
}
async function logout() {
  try { await api('/api/auth/logout','POST',{}); }
  catch (err) { console.error(err); }
  await refreshStatus();
}
refreshStatus();
</script>
</body></html>
"""


def _html_report_bootstrap() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Preparing Report v3</title>
  <style>
    :root {
      --bg: #f4f6fb;
      --panel: #ffffff;
      --ink: #0f172a;
      --muted: #475569;
      --line: #dbe3ef;
      --brand: #0f766e;
      --brand-strong: #115e59;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(900px 400px at 90% -80px, #99f6e4 0%, rgba(153,246,228,0) 65%),
        radial-gradient(700px 360px at -120px -120px, #bfdbfe 0%, rgba(191,219,254,0) 60%),
        var(--bg);
      font-family: "Segoe UI Variable", "Segoe UI", "Aptos", Tahoma, sans-serif;
      color: var(--ink);
      padding: 16px;
    }
    .card {
      width: min(660px, 100%);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 16px 40px rgba(2, 6, 23, .08);
    }
    h1 { margin: 0 0 10px; font-size: 1.35rem; }
    p { margin: 0 0 12px; color: var(--muted); line-height: 1.45; }
    button {
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      background: var(--brand);
      color: #fff;
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { background: var(--brand-strong); }
    button[disabled] { opacity: .65; cursor: not-allowed; }
    .status {
      margin-top: 12px;
      border: 1px solid var(--line);
      background: #f8fafc;
      border-radius: 10px;
      padding: 10px;
      font-size: .9rem;
      color: var(--muted);
      min-height: 44px;
    }
  </style>
</head>
<body>
  <main class=\"card\">
    <h1>Primeiro relatório ainda não foi gerado</h1>
    <p>Não encontramos o relatório v3 (`module-resolver-latest-v3.html`).</p>
    <p>Clique no botão abaixo para executar o primeiro <code>dry-run</code>. Quando finalizar, você será redirecionado automaticamente para o Report v3.</p>
    <button id=\"runBtn\" type=\"button\" onclick=\"runFirstDryRun()\">Gerar primeiro relatório</button>
    <div id=\"status\" class=\"status\">Aguardando ação...</div>
  </main>
<script>
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : null,
  });
  const text = await res.text();
  let payload = { raw: text };
  try { payload = JSON.parse(text); } catch {}
  if (!res.ok) throw payload;
  return payload;
}
function setStatus(text) {
  document.getElementById('status').textContent = text;
}
async function waitForJob(jobId) {
  const startedAt = Date.now();
  while (true) {
    const job = await api('/api/actions/jobs/' + encodeURIComponent(jobId));
    const elapsed = Math.round((Date.now() - startedAt) / 1000);
    setStatus('Executando dry-run... status=' + job.status + ' progresso=' + (job.progress || 0) + '% (' + elapsed + 's)');
    if (job.status === 'success') {
      window.location.replace('/api/report/v3?t=' + Date.now());
      return;
    }
    if (job.status === 'failed') {
      const details = job.error ? (' erro=' + job.error) : '';
      setStatus('Falha ao gerar relatório.' + details);
      document.getElementById('runBtn').disabled = false;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}
async function runFirstDryRun() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  try {
    setStatus('Enfileirando dry-run...');
    const submitted = await api('/api/actions/submit', 'POST', {
      action: 'dry-run',
      payload: { batchSize: 10 }
    });
    if (!submitted.jobId) {
      throw new Error('jobId ausente na resposta');
    }
    await waitForJob(submitted.jobId);
  } catch (err) {
    setStatus('Erro ao iniciar dry-run. Veja o console para detalhes.');
    console.error(err);
    btn.disabled = false;
  }
}
</script>
</body></html>
"""


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
        foundry_host=os.environ.get("RESOLVER_FOUNDRY_HOST") or "host.docker.internal",
        foundry_port=_parse_int(os.environ.get("RESOLVER_FOUNDRY_PORT"), default=30000, min_value=1),
        cookie_secure=(os.environ.get("RESOLVER_COOKIE_SECURE", "false").strip().lower() == "true"),
        auth_max_failed_attempts=_parse_int(os.environ.get("RESOLVER_AUTH_MAX_FAILED_ATTEMPTS"), default=5, min_value=1),
        auth_lockout_minutes=_parse_int(os.environ.get("RESOLVER_AUTH_LOCKOUT_MINUTES"), default=15, min_value=1),
        max_sessions=_parse_int(os.environ.get("RESOLVER_MAX_SESSIONS"), default=200, min_value=1),
        audit_file=audit_file,
    )
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return config


def run() -> None:
    config = load_config()
    auth_store = AuthStore(config)
    lock_store = MaintenanceLock(config.state_dir)
    action_engine = ActionEngine()

    handler = ResolverAPIHandler
    handler.config = config
    handler.auth_store = auth_store
    handler.lock_store = lock_store
    handler.action_engine = action_engine

    _start_action_worker(config, action_engine, lock_store)

    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    print(f"[resolver-api] listening on http://{config.bind_host}:{config.bind_port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
