from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from service.server import (
    ActionEngine,
    AuthStore,
    MaintenanceLock,
    ModuleSourceStore,
    RequestRateLimiter,
    RuntimeConfigStore,
    _append_audit,
    _build_candidate_module,
    _execute_action_job,
    _foundry_process_probe,
    _normalize_modules,
    _run_module_health_check,
    _suggest_best_release_for_module,
    _utc_now_iso,
    _validate_foundry_root_path,
    detect_foundry_version,
    load_apply_history,
    load_config,
    load_system_versions,
)


@dataclass
class AppRuntime:
    config: Any
    auth_store: AuthStore
    lock_store: MaintenanceLock
    action_engine: ActionEngine
    config_store: RuntimeConfigStore
    module_source_store: ModuleSourceStore
    rate_limiter: RequestRateLimiter


_RUNTIME: AppRuntime | None = None
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def get_runtime() -> AppRuntime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    config = load_config()
    runtime = AppRuntime(
        config=config,
        auth_store=AuthStore(config),
        lock_store=MaintenanceLock(config.state_dir),
        action_engine=ActionEngine(),
        config_store=RuntimeConfigStore(config),
        module_source_store=ModuleSourceStore(config),
        rate_limiter=RequestRateLimiter(config.request_rate_limit_per_minute),
    )
    _RUNTIME = runtime
    _ensure_worker(runtime)
    return runtime


def _ensure_worker(runtime: AppRuntime) -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker_loop() -> None:
            while True:
                job = runtime.action_engine.pick_next()
                if job is None:
                    threading.Event().wait(0.25)
                    continue
                runtime.action_engine.set_progress(job.job_id, 25)
                _append_audit(runtime.config, "action_worker_started", {"jobId": job.job_id, "action": job.action})
                try:
                    runtime.action_engine.set_progress(job.job_id, 60)
                    result = _execute_action_job(
                        runtime.config,
                        runtime.config_store,
                        runtime.lock_store,
                        job.action,
                        job.payload,
                    )
                    runtime.action_engine.set_progress(job.job_id, 95)
                    runtime.action_engine.complete(job.job_id, ok=True, result=result)
                    _append_audit(runtime.config, "action_worker_success", {"jobId": job.job_id, "action": job.action})
                except Exception as exc:
                    runtime.action_engine.complete(job.job_id, ok=False, result=None, error=str(exc))
                    _append_audit(
                        runtime.config,
                        "action_worker_failed",
                        {"jobId": job.job_id, "action": job.action, "error": str(exc)},
                    )

        thread = threading.Thread(target=_worker_loop, daemon=True, name="resolver-fastapi-action-worker")
        thread.start()
        _WORKER_STARTED = True


def request_principal(client_host: str | None) -> str:
    value = str(client_host or "unknown").strip()
    return value or "unknown"


def foundry_status(runtime: AppRuntime) -> dict[str, Any]:
    online_tcp = False
    try:
        with socket.create_connection((runtime.config.foundry_host, runtime.config.foundry_port), timeout=1.5):
            online_tcp = True
    except OSError:
        online_tcp = False
    process_probe = _foundry_process_probe(
        process_name=runtime.config.foundry_process_name,
        data_root=runtime.config.data_root,
    )
    online_process = bool(process_probe.get("online"))
    host_lower = str(runtime.config.foundry_host or "").strip().lower()
    tcp_is_ambiguous = host_lower in {"host.docker.internal", "localhost", "127.0.0.1"}
    if tcp_is_ambiguous:
        online = online_process
        source = "process-only"
    else:
        online = bool(online_tcp or online_process)
        source = "tcp-or-process"
    return {
        "status": "online" if online else "offline",
        "online": bool(online),
        "host": runtime.config.foundry_host,
        "port": int(runtime.config.foundry_port),
        "checks": {
            "tcp": bool(online_tcp),
            "process": bool(online_process),
            "processProbe": process_probe,
            "sourcePolicy": source,
        },
    }


def read_report_model(runtime: AppRuntime) -> dict[str, Any]:
    report_path = runtime.config.reports_dir / "module-resolver-latest.json"
    if not report_path.exists():
        raise FileNotFoundError("latest_report_not_found")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report_views = payload.get("reportViews") if isinstance(payload.get("reportViews"), dict) else {}
    view = report_views.get("v3") if isinstance(report_views.get("v3"), dict) else {}
    backup_management = view.get("backupManagement") if isinstance(view.get("backupManagement"), dict) else {}
    try:
        backup_management["applyHistory"] = load_apply_history(str(runtime.config.state_dir / "resolver.db"), limit=30)
    except Exception:
        backup_management["applyHistory"] = []
    view["backupManagement"] = backup_management
    return {
        "generatedAt": payload.get("generatedAt"),
        "targetVersion": payload.get("targetVersion"),
        "dataRoot": payload.get("dataRoot"),
        "installedSystemVersions": payload.get("installedSystemVersions") or {},
        "worldUsage": payload.get("worldUsage") or [],
        "view": view,
        "results": payload.get("results") or [],
    }


def rollback_plan(runtime: AppRuntime, scan_run_id: int) -> dict[str, Any]:
    history = load_apply_history(str(runtime.config.state_dir / "resolver.db"), limit=200)
    found = next((row for row in history if int(row.get("scanRunId") or 0) == int(scan_run_id)), None)
    if not found:
        raise LookupError("scan_run_not_found")
    backup_paths = [str(p).strip() for p in (found.get("backupPaths") or []) if str(p).strip()]
    modules = [str(m).strip() for m in (found.get("modulesChanged") or []) if str(m).strip()]
    return {
        "ok": True,
        "scanRunId": int(scan_run_id),
        "generatedAt": found.get("generatedAt"),
        "targetVersion": found.get("targetVersion"),
        "modules": modules,
        "backupPaths": backup_paths,
        "notes": "Rollback execution is not yet automatic. Use backup paths to restore module folders.",
    }


def module_health(runtime: AppRuntime) -> dict[str, Any]:
    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    return _run_module_health_check(normalized_root)


def suggest_module(runtime: AppRuntime, module_id: str, manifest_url: str, project_url: str) -> dict[str, Any]:
    clean_module_id = str(module_id or "").strip()
    clean_manifest = str(manifest_url or "").strip()
    clean_project = str(project_url or "").strip()
    if not clean_manifest and not clean_project:
        raise ValueError("manifest_or_project_required")
    if not clean_module_id and clean_manifest:
        clean_module_id = "manifest-derived"
    if not clean_module_id:
        raise ValueError("module_id_required")
    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    foundry_version, source = detect_foundry_version(normalized_root)
    suggestion = _suggest_best_release_for_module(
        module=_build_candidate_module(clean_module_id, manifest_url=clean_manifest, project_url=clean_project),
        target_foundry_version=foundry_version,
        installed_system_versions=load_system_versions(normalized_root),
        cache_dir=runtime.config.cache_dir,
    )
    return {
        "ok": True,
        "moduleId": clean_module_id,
        "foundryVersion": foundry_version,
        "foundryVersionSource": source,
        "dataRoot": normalized_root,
        "suggestion": suggestion,
    }


def save_module_source(runtime: AppRuntime, module_id: str, manifest_url: str, project_url: str) -> dict[str, Any]:
    clean_module_id = str(module_id or "").strip()
    clean_manifest = str(manifest_url or "").strip()
    clean_project = str(project_url or "").strip()
    if not clean_module_id:
        raise ValueError("module_id_required")
    if not clean_manifest and not clean_project:
        raise ValueError("manifest_or_project_required")
    suggestion = _suggest_best_release_for_module(
        module=_build_candidate_module(clean_module_id, manifest_url=clean_manifest, project_url=clean_project),
        target_foundry_version=detect_foundry_version(runtime.config_store.get_data_root() or runtime.config.data_root)[0],
        installed_system_versions=load_system_versions(runtime.config_store.get_data_root() or runtime.config.data_root),
        cache_dir=runtime.config.cache_dir,
    )
    saved = runtime.module_source_store.upsert_source(
        module_id=clean_module_id,
        manifest_url=clean_manifest,
        project_url=clean_project,
    )
    return {"ok": True, "saved": saved, "suggestion": suggestion}


def set_foundry_root(runtime: AppRuntime, path: str) -> dict[str, Any]:
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("path_required")
    ok, normalized, details = _validate_foundry_root_path(raw_path)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root path.")
    runtime.config_store.set_data_root(normalized)
    return runtime.config_store.status()


def queue_action(runtime: AppRuntime, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    if clean_action not in {"dry-run", "apply", "force-compat", "cleanup-backups"}:
        raise ValueError("unsupported_action")
    body = payload if isinstance(payload, dict) else {}
    if clean_action == "apply":
        _ = _normalize_modules(body.get("modules"))
    job = runtime.action_engine.enqueue(action=clean_action, payload=body)
    _append_audit(runtime.config, "action_enqueued", {"jobId": job.job_id, "action": clean_action})
    return {"ok": True, "jobId": job.job_id, "status": job.status, "action": clean_action}
