from __future__ import annotations

import base64
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


class AuthStore:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._sessions: dict[str, datetime] = {}

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
            self._sessions[token] = expires
        return token

    def is_session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
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
        if parsed.path == "/api/jobs":
            if not self._require_auth():
                return
            log_path = self.config.reports_dir / "module-resolver-latest.log"
            output = ""
            if log_path.exists():
                output = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            self._send_json(HTTPStatus.OK, {"tail": output})
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
        self._send_json(HTTPStatus.CREATED, {"ok": True}, cookies=self._session_cookie_headers(token))

    def _handle_login(self, body: dict[str, Any]) -> None:
        if not self.auth_store.has_password():
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": "password_not_configured"})
            return
        password = str(body.get("password") or "")
        if not self.auth_store.verify_password(password):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_credentials"})
            return
        token = self.auth_store.create_session()
        self._send_json(HTTPStatus.OK, {"ok": True}, cookies=self._session_cookie_headers(token))

    def _handle_logout(self) -> None:
        token = self._session_token()
        self.auth_store.revoke_session(token)
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
                self._send_json(HTTPStatus.BAD_REQUEST, payload)
                raise _RequestAlreadyHandled
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


class _RequestAlreadyHandled(Exception):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
  <title>Foundry Dependencies Resolver</title>
  <style>
    :root { --bg:#0f172a; --panel:#111827; --ink:#e5e7eb; --muted:#93a3b8; --accent:#22c55e; --warn:#ef4444; }
    * { box-sizing:border-box; }
    body { margin:0; font-family: "Segoe UI", Tahoma, sans-serif; background: radial-gradient(circle at 10% 10%, #1f2937, #0b1021 65%); color:var(--ink); }
    .wrap { max-width: 980px; margin: 24px auto; padding: 0 16px; }
    .card { background: rgba(17,24,39,.92); border:1px solid #334155; border-radius: 14px; padding: 16px; margin-bottom: 16px; }
    h1 { margin:0 0 10px; font-size: 1.4rem; }
    h2 { margin:0 0 10px; font-size: 1rem; color:#cbd5e1; }
    input, textarea { width:100%; border:1px solid #475569; border-radius:10px; background:#0b1220; color:#e5e7eb; padding:10px; }
    button { border:0; border-radius:10px; padding:10px 12px; cursor:pointer; background:#1d4ed8; color:white; }
    button.warn { background:#b91c1c; }
    button.alt { background:#374151; }
    .row { display:flex; flex-wrap:wrap; gap:10px; }
    .row > * { flex:1 1 220px; }
    .muted { color:var(--muted); font-size:.9rem; }
    pre { white-space: pre-wrap; background:#020617; border:1px solid #334155; border-radius:10px; padding:12px; max-height:300px; overflow:auto; }
    .ok { color: var(--accent); }
    .err { color: var(--warn); }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <h1>Foundry Dependencies Resolver</h1>
      <div id=\"status\" class=\"muted\">Loading status...</div>
    </div>

    <div class=\"card\" id=\"authCard\">
      <h2>Authentication</h2>
      <div class=\"row\" id=\"setupFields\" style=\"display:none\">
        <input id=\"setupPass\" type=\"password\" placeholder=\"Create password\">
        <input id=\"setupPass2\" type=\"password\" placeholder=\"Confirm password\">
        <button onclick=\"setupPassword()\">Create Password</button>
      </div>
      <div class=\"row\" id=\"loginFields\" style=\"display:none\">
        <input id=\"loginPass\" type=\"password\" placeholder=\"Password\">
        <button onclick=\"login()\">Login</button>
        <button class=\"alt\" onclick=\"logout()\">Logout</button>
      </div>
      <p class=\"muted\">Password hash is stored locally in <code>state/auth.json</code>.</p>
    </div>

    <div class=\"card\">
      <h2>Actions</h2>
      <div class=\"row\">
        <button onclick=\"runDryRun()\">Run Dry Run</button>
        <button class=\"warn\" onclick=\"forceCompat()\">Force Compatibility</button>
        <button class=\"warn\" onclick=\"cleanupAll()\">Cleanup All Backups</button>
        <button onclick=\"loadReport()\">Load Latest Report JSON</button>
      </div>
      <div class=\"row\" style=\"margin-top:10px\">
        <input id=\"moduleInput\" placeholder=\"module id list, comma separated\" value=\"scene-preview\">
        <input id=\"targetVersion\" placeholder=\"target Foundry version\" value=\"13.351\">
      </div>
    </div>

    <div class=\"card\">
      <h2>Output</h2>
      <pre id=\"out\">Waiting actions...</pre>
    </div>
  </div>

<script>
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : null,
  });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { raw: text }; }
  if (!res.ok) throw { status: res.status, body: json };
  return json;
}
function show(v) { document.getElementById('out').textContent = JSON.stringify(v, null, 2); }
function modulesFromInput() {
  const raw = document.getElementById('moduleInput').value || '';
  return raw.split(',').map(s => s.trim()).filter(Boolean);
}
async function refreshStatus() {
  try {
    const health = await api('/api/health');
    const auth = await api('/api/auth/status');
    document.getElementById('status').innerHTML =
      `Foundry: <b>${health.foundry.status}</b> (${health.foundry.host}:${health.foundry.port}) | ` +
      `Password configured: <b>${auth.passwordConfigured}</b> | Authenticated: <b>${auth.authenticated}</b>`;
    document.getElementById('setupFields').style.display = auth.passwordConfigured ? 'none' : 'flex';
    document.getElementById('loginFields').style.display = auth.passwordConfigured ? 'flex' : 'none';
  } catch (err) {
    show(err);
  }
}
async function setupPassword() {
  const password = document.getElementById('setupPass').value;
  const confirmPassword = document.getElementById('setupPass2').value;
  try { show(await api('/api/auth/setup','POST',{password,confirmPassword})); }
  catch (err) { show(err); }
  await refreshStatus();
}
async function login() {
  const password = document.getElementById('loginPass').value;
  try { show(await api('/api/auth/login','POST',{password})); }
  catch (err) { show(err); }
  await refreshStatus();
}
async function logout() {
  try { show(await api('/api/auth/logout','POST',{})); }
  catch (err) { show(err); }
  await refreshStatus();
}
async function runDryRun() {
  try { show(await api('/api/actions/dry-run','POST',{modules:modulesFromInput(),batchSize:10})); }
  catch (err) { show(err); }
}
async function forceCompat() {
  const modules = modulesFromInput();
  const targetVersion = document.getElementById('targetVersion').value;
  try { show(await api('/api/actions/force-compat','POST',{modules,targetVersion})); }
  catch (err) { show(err); }
}
async function cleanupAll() {
  try { show(await api('/api/actions/cleanup-backups','POST',{all:true})); }
  catch (err) { show(err); }
}
async function loadReport() {
  try { show(await api('/api/report/latest')); }
  catch (err) { show(err); }
}
refreshStatus();
</script>
</body></html>
"""


def load_config() -> ServiceConfig:
    tool_root = Path(__file__).resolve().parent.parent
    state_dir = Path(os.environ.get("RESOLVER_STATE_DIR") or (tool_root / "state"))
    reports_dir = Path(os.environ.get("RESOLVER_REPORTS_DIR") or (tool_root / "reports"))
    auth_file = Path(os.environ.get("RESOLVER_AUTH_FILE") or (state_dir / "auth.json"))
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
    )
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return config


def run() -> None:
    config = load_config()
    auth_store = AuthStore(config)
    lock_store = MaintenanceLock(config.state_dir)

    handler = ResolverAPIHandler
    handler.config = config
    handler.auth_store = auth_store
    handler.lock_store = lock_store

    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    print(f"[resolver-api] listening on http://{config.bind_host}:{config.bind_port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
