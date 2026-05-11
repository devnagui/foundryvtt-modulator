from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from resolver.db_queries import load_apply_history
from resolver.foundry import detect_foundry_version
from resolver.local import load_system_versions
from resolver.models import ModuleRecord
from resolver.scoring import candidate_sort_key, satisfies_release_constraints
from resolver.sources import fetch_release_history

from .core import (
    ActionEngine,
    AuthStore,
    MaintenanceLock,
    ModuleSourceStore,
    RequestRateLimiter,
    RuntimeConfigStore,
    _append_audit,
    _foundry_process_probe,
    _normalize_modules,
    _utc_now_iso,
    _validate_foundry_root_path,
    load_config,
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
                    result = _execute_action_job(runtime, job.action, job.payload)
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
    import socket

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


def _run_module_health_check(data_root: str) -> dict[str, Any]:
    modules_root = Path(data_root) / "Data" / "modules"
    rows: list[dict[str, Any]] = []
    if not modules_root.exists():
        return {"ok": False, "error": "modules_root_not_found", "path": str(modules_root), "rows": []}
    for module_json in sorted(modules_root.glob("*/module.json")):
        module_dir = module_json.parent
        module_id = module_dir.name
        normalized = module_id.lower()
        if normalized.startswith("_backup_") or ".bak." in normalized:
            continue
        issues: list[str] = []
        warnings: list[str] = []
        try:
            manifest = json.loads(module_json.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append({"module": module_id, "title": module_id, "status": "invalid", "issues": [f"manifest_read_error:{exc}"], "warnings": []})
            continue
        title = str(manifest.get("title") or module_id)
        if manifest.get("minimumCoreVersion") is not None or manifest.get("compatibleCoreVersion") is not None:
            warnings.append("legacy_core_compat_fields_detected")
        if not isinstance(manifest.get("compatibility"), dict):
            warnings.append("compatibility_object_missing")
        for key in ("styles", "scripts", "esmodules"):
            values = manifest.get(key) or []
            if not isinstance(values, list):
                warnings.append(f"{key}_is_not_array")
                continue
            for rel in values:
                rel_path = str(rel or "").strip()
                if rel_path and not (module_dir / rel_path).exists():
                    issues.append(f"missing_file:{rel_path}")
        relationships = manifest.get("relationships") or {}
        requires = relationships.get("requires") or []
        if isinstance(requires, list):
            for dep in requires:
                if isinstance(dep, dict) and str(dep.get("type") or "") == "module":
                    dep_id = str(dep.get("id") or "")
                    if dep_id and not (modules_root / dep_id).exists():
                        warnings.append(f"missing_dependency:{dep_id}")
        rows.append({"module": str(manifest.get("id") or module_id), "title": title, "status": "ok" if not issues else "invalid", "issues": sorted(set(issues)), "warnings": sorted(set(warnings)), "manifestPath": str(module_json)})
    return {"ok": True, "path": str(modules_root), "count": len(rows), "invalidCount": len([r for r in rows if r.get("status") != "ok"]), "warningCount": sum(len(r.get("warnings") or []) for r in rows), "rows": rows}


def module_health(runtime: AppRuntime) -> dict[str, Any]:
    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    return _run_module_health_check(normalized_root)


def _evaluate_apply_health_gate(data_root: str, selected_modules: list[str]) -> dict[str, Any]:
    health = _run_module_health_check(data_root)
    if not bool(health.get("ok")):
        return {"ok": False, "blocked": True, "reason": str(health.get("error") or "module_health_unavailable"), "count": 0, "rows": []}
    rows = health.get("rows") or []
    selected_set = {str(item).strip().lower() for item in (selected_modules or []) if str(item).strip()}
    scoped = [row for row in rows if not selected_set or str((row or {}).get("module") or "").strip().lower() in selected_set]
    blocking_rows: list[dict[str, Any]] = []
    for row in scoped:
        issues = row.get("issues") or []
        warnings = row.get("warnings") or []
        has_missing_dependency = any(str(item).startswith("missing_dependency:") for item in warnings)
        if issues or has_missing_dependency:
            blocking_rows.append({"module": row.get("module"), "title": row.get("title"), "issues": issues if isinstance(issues, list) else [], "warnings": warnings if isinstance(warnings, list) else []})
    return {"ok": True, "blocked": len(blocking_rows) > 0, "reason": "module_health_gate_failed" if blocking_rows else "module_health_gate_ok", "count": len(scoped), "rows": blocking_rows}


def _build_cli_args_from_action(action: str, payload: dict[str, Any]) -> tuple[list[str], bool, str]:
    normalized_action = str(action or "").strip().lower()
    modules = _normalize_modules(payload.get("modules"))
    batch_size = max(10, int(payload.get("batchSize") or 10))
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
        if not target_version:
            raise ValueError("invalid_target_version")
        if not modules:
            raise ValueError("modules_required")
        args: list[str] = []
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


def _execute_action_job(runtime: AppRuntime, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if str(action or "").strip().lower() == "rollback-batch":
        scan_run_id = int(payload.get("scanRunId") or 0)
        if scan_run_id <= 0:
            raise ValueError("scan_run_id_required")
        return execute_rollback(runtime, scan_run_id)

    effective_data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    modules = _normalize_modules(payload.get("modules"))
    extra_args, maintenance, action_name = _build_cli_args_from_action(action, payload)
    lock_payload: dict[str, Any] | None = None
    if maintenance:
        lock_payload = runtime.lock_store.acquire(action=action_name)
    try:
        preflight_gate: dict[str, Any] | None = None
        if action_name == "apply":
            preflight_gate = _evaluate_apply_health_gate(effective_data_root, modules)
            if preflight_gate.get("blocked"):
                sample = (preflight_gate.get("rows") or [])[:5]
                details = ", ".join(str((item or {}).get("module") or "?") for item in sample)
                raise RuntimeError(f"Apply blocked by module health gate. Fix invalid/missing dependencies first. Affected: {details or 'unknown'}")
        cmd = [
            runtime.config.python_bin,
            "-m",
            "resolver.cli",
            "--data-root",
            effective_data_root,
            "--cache-dir",
            runtime.config.cache_dir,
            "--database-path",
            str(runtime.config.state_dir / "resolver.db"),
            "--skip-foundry-service-control",
            "--json-output",
            str(runtime.config.reports_dir / "module-resolver-latest.json"),
            "--html-report",
            str(runtime.config.reports_dir / "module-resolver-latest.html"),
            "--log-file",
            str(runtime.config.reports_dir / "module-resolver-latest.log"),
            *extra_args,
        ]
        result = subprocess.run(cmd, cwd=str(runtime.config.tool_root), capture_output=True, text=True, check=False)
        output = {
            "ok": result.returncode == 0,
            "returnCode": result.returncode,
            "command": cmd,
            "stdout": (result.stdout or "")[-20000:],
            "stderr": (result.stderr or "")[-20000:],
            "lock": lock_payload,
            "generatedAt": _utc_now_iso(),
            "dataRoot": effective_data_root,
            "preflight": preflight_gate,
        }
        if result.returncode != 0:
            raise RuntimeError(output.get("stderr") or f"Action failed with returnCode={result.returncode}")
        if action_name == "apply":
            postflight_gate = _evaluate_apply_health_gate(effective_data_root, modules)
            output["postflight"] = postflight_gate
            if postflight_gate.get("blocked"):
                output["ok"] = False
                raise RuntimeError("Apply finished but post-check found invalid modules or missing dependencies.")
        return output
    finally:
        if maintenance:
            runtime.lock_store.release()


def _build_candidate_module(module_id: str, manifest_url: str, project_url: str) -> ModuleRecord:
    clean_id = str(module_id or "").strip()
    if manifest_url:
        request = Request(manifest_url, headers={"User-Agent": "foundry-module-version-resolver/0.1"})
        with urlopen(request, timeout=20) as response:
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
    releases, warnings = fetch_release_history(module, cache_dir=cache_dir)
    best = None
    valid = [item for item in releases if satisfies_release_constraints(item, target_foundry_version, installed_system_versions)]
    if valid:
        best = sorted(valid, key=lambda item: candidate_sort_key(item, target_foundry_version, installed_system_versions), reverse=True)[0]
    elif releases:
        best = releases[0]
    if not best:
        return {"module": module.module_id, "reason": "No release available.", "checkedReleases": 0, "warnings": warnings}
    return {
        "module": module.module_id,
        "title": module.title,
        "installedVersion": module.version,
        "recommendedVersion": best.version,
        "manifestUrl": best.manifest_url,
        "downloadUrl": best.download_url,
        "compatibility": best.compatibility,
        "source": best.source,
        "checkedReleases": len(releases),
        "warnings": warnings,
    }


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
    return {"ok": True, "moduleId": clean_module_id, "foundryVersion": foundry_version, "foundryVersionSource": source, "dataRoot": normalized_root, "suggestion": suggestion}


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
    saved = runtime.module_source_store.upsert_source(module_id=clean_module_id, manifest_url=clean_manifest, project_url=clean_project)
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
    if clean_action not in {"dry-run", "apply", "force-compat", "cleanup-backups", "rollback-batch"}:
        raise ValueError("unsupported_action")
    body = payload if isinstance(payload, dict) else {}
    if clean_action == "apply":
        _ = _normalize_modules(body.get("modules"))
    job = runtime.action_engine.enqueue(action=clean_action, payload=body)
    _append_audit(runtime.config, "action_enqueued", {"jobId": job.job_id, "action": clean_action})
    return {"ok": True, "jobId": job.job_id, "status": job.status, "action": clean_action}


def _ensure_foundry_offline(runtime: AppRuntime) -> None:
    if not runtime.config.require_foundry_offline:
        return
    status = foundry_status(runtime)
    if status.get("online"):
        raise RuntimeError("maintenance_requires_foundry_offline")


def _safe_extract_backup_zip(archive_path: Path, target_root: Path) -> None:
    root = target_root.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            name = str(member.filename or "")
            if not name:
                continue
            candidate = Path(name)
            if candidate.is_absolute() or ":" in name.split("/")[0]:
                raise ValueError(f"unsafe_backup_entry:{name}")
            resolved_target = (root / candidate).resolve()
            if resolved_target != root and not str(resolved_target).startswith(str(root) + os.sep):
                raise ValueError(f"unsafe_backup_entry:{name}")
        archive.extractall(root)


def execute_rollback(runtime: AppRuntime, scan_run_id: int) -> dict[str, Any]:
    _ensure_foundry_offline(runtime)
    plan = rollback_plan(runtime, scan_run_id)
    backup_paths = [Path(str(p)) for p in (plan.get("backupPaths") or []) if str(p).strip()]
    if not backup_paths:
        raise ValueError("rollback_backups_not_found")

    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "invalid_foundry_root")
    modules_root = Path(normalized_root) / "Data" / "modules"
    restored: list[dict[str, Any]] = []

    for bak in backup_paths:
        if not bak.exists() or not bak.is_file():
            continue
        module_id = bak.parent.name
        target_dir = modules_root / module_id
        with tempfile.TemporaryDirectory(prefix=f"rollback-{module_id}-") as temp_dir:
            temp_root = Path(temp_dir)
            _safe_extract_backup_zip(bak, temp_root)
            if target_dir.exists():
                if target_dir.is_dir():
                    shutil.rmtree(target_dir)
                else:
                    target_dir.unlink()
            shutil.move(str(temp_root), str(target_dir))
        restored.append({"module": module_id, "backupPath": str(bak), "targetPath": str(target_dir)})

    if not restored:
        raise ValueError("rollback_backups_not_found")
    return {"ok": True, "scanRunId": int(scan_run_id), "restoredCount": len(restored), "restored": restored, "generatedAt": _utc_now_iso()}
