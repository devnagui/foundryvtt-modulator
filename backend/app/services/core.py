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
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    ui_dist_dir: Path = field(default_factory=lambda: Path("frontend/dist"))


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
    return max(value, min_value)


def _normalize_modules(raw: Any) -> list[str]:
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    normalized: list[str] = []
    for value in values:
        module_id = str(value).strip()
        if not module_id:
            continue
        if not MODULE_ID_RE.match(module_id):
            raise ValueError(f"Invalid module id: {module_id}")
        normalized.append(module_id)
    return normalized


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


def load_config() -> ServiceConfig:
    tool_root = Path(__file__).resolve().parents[3]
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
        python_bin=os.environ.get("RESOLVER_PYTHON_BIN") or "python",
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
        ui_dist_dir=Path(os.environ.get("RESOLVER_UI_DIST_DIR") or (tool_root / "frontend" / "dist")),
    )
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return config


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
    progress_meta: dict[str, Any] = field(default_factory=dict)


class ActionEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._jobs: dict[str, ActionJob] = {}
        self._running_job_id: str | None = None

    def enqueue(self, action: str, payload: dict[str, Any]) -> ActionJob:
        with self._lock:
            now = _utc_now_iso()
            job = ActionJob(secrets.token_hex(12), action, copy.deepcopy(payload), "pending", 0, now, now)
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
            return copy.deepcopy(job)

    def pick_next(self) -> ActionJob | None:
        with self._lock:
            if self._running_job_id is not None or not self._queue:
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

    def set_progress(self, job_id: str, progress: int, meta: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0, min(int(progress), 99))
            if isinstance(meta, dict):
                merged = dict(job.progress_meta or {})
                merged.update(copy.deepcopy(meta))
                job.progress_meta = merged
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
            return {"runningJobId": self._running_job_id, "pendingCount": len(self._queue), "jobs": [self._job_to_dict(job) for job in ordered[:200]]}

    def _job_to_dict(self, job: ActionJob) -> dict[str, Any]:
        return {
            "jobId": job.job_id,
            "action": job.action,
            "payload": copy.deepcopy(job.payload),
            "status": job.status,
            "progress": job.progress,
            "progressMeta": copy.deepcopy(job.progress_meta),
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
        return {"selected": selected, "normalized": normalized if ok else "", "valid": bool(ok), "message": details.get("message") or ("Foundry root is valid." if ok else "Invalid Foundry root."), "details": details}

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
            normalized[key] = {"moduleId": key, "manifestUrl": str(item.get("manifestUrl") or "").strip(), "projectUrl": str(item.get("projectUrl") or "").strip(), "updatedAt": str(item.get("updatedAt") or "")}
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
            row = {"moduleId": clean_id, "manifestUrl": clean_manifest, "projectUrl": clean_project, "updatedAt": _utc_now_iso()}
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
        return str(payload.get("username") or "").strip()

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
            if expires <= datetime.now(timezone.utc):
                self._sessions.pop(token, None)
                return False
            return True

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)
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
            return True, max(int((until - now).total_seconds()), 1)

    def register_login_failure(self, principal: str) -> tuple[int, int | None]:
        key = principal or "unknown"
        with self._lock:
            attempts = int(self._failed_attempts.get(key, 0)) + 1
            self._failed_attempts[key] = attempts
            if attempts >= max(self._config.auth_max_failed_attempts, 1):
                until = datetime.now(timezone.utc) + timedelta(minutes=max(self._config.auth_lockout_minutes, 1))
                self._lockouts[key] = until
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
        rows = [{"token": token, "expiresAt": expires.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")} for token, expires in sorted(self._sessions.items(), key=lambda item: item[1])]
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
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self._config.pbkdf2_iterations)
        return {"algo": "pbkdf2_sha256", "iterations": self._config.pbkdf2_iterations, "salt": base64.b64encode(salt).decode("utf-8"), "hash": base64.b64encode(digest).decode("utf-8")}


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
            payload = {"lockVersion": 1, "lockId": secrets.token_hex(16), "jobId": secrets.token_hex(16), "action": action, "createdAt": _utc_now_iso()}
            self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            return payload

    def release(self) -> None:
        with self._mutex:
            try:
                if self._path.exists():
                    self._path.unlink()
            except OSError:
                return


def _append_audit(config: ServiceConfig, event: str, details: dict[str, Any]) -> None:
    payload = {"timestamp": _utc_now_iso(), "event": str(event or "unknown"), "path": "background-worker", "method": "WORKER", "principal": "resolver-worker", "details": details or {}}
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        with config.audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except OSError:
        return


def _validate_foundry_root_path(raw_path: str) -> tuple[bool, str, dict[str, Any]]:
    path = Path(str(raw_path or "").strip()).expanduser()
    if not path.exists():
        return False, "", {"message": "Path does not exist."}
    candidate = path
    if candidate.name.lower() == "data" and (candidate / "modules").exists():
        candidate = candidate.parent
    if not (candidate / "Data").exists() and (candidate / "modules").exists() and (candidate / "worlds").exists():
        # User pointed to Data itself.
        candidate = candidate.parent
    data_dir = candidate / "Data"
    if not data_dir.exists():
        return False, "", {"message": "Missing Data directory in selected root."}
    modules_dir = data_dir / "modules"
    worlds_dir = data_dir / "worlds"
    if not modules_dir.exists() or not worlds_dir.exists():
        return False, "", {"message": "Selected root must contain Data/modules and Data/worlds."}
    return True, str(candidate), {"message": "Foundry root validated.", "path": str(candidate)}


def _pick_folder_native() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError(f"Folder picker unavailable: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory()
    finally:
        root.destroy()
    return str(selected or "").strip()


def _count_windows_process_name_occurrences(tasklist_csv: str, process_name: str) -> int:
    target = str(process_name or "").strip().lower()
    if not target:
        return 0
    rows = [line.strip() for line in str(tasklist_csv or "").splitlines() if line.strip()]
    count = 0
    for row in rows:
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


def _foundry_process_probe(process_name: str, data_root: str = "") -> dict[str, Any]:
    if os.name == "nt":
        try:
            result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False)
        except OSError as exc:
            return {"online": False, "source": "tasklist-error", "error": str(exc), "count": 0}
        if result.returncode != 0:
            return {"online": False, "source": "tasklist-failed", "error": (result.stderr or "").strip(), "count": 0}
        count = _count_windows_process_name_occurrences(result.stdout or "", process_name)
        return {"online": bool(count > 0), "source": "tasklist", "processName": process_name, "count": int(count)}
    try:
        result = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, check=False)
    except OSError as exc:
        return {"online": False, "source": "ps-error", "error": str(exc), "count": 0}
    if result.returncode != 0:
        return {"online": False, "source": "ps-failed", "error": (result.stderr or "").strip(), "count": 0}
    count = _count_posix_process_name_occurrences(result.stdout or "", process_name)
    return {"online": bool(count > 0), "source": "ps", "processName": process_name, "count": int(count)}
