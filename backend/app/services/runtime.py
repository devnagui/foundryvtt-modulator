from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from resolver.apply import apply_recommendation, apply_system_recommendation
from resolver.db_queries import load_apply_history, load_planning_context_rows, load_scan_run_payload
from resolver.dependencies import resolve_module_recommendation
from resolver.foundry import detect_foundry_version
from resolver.local import load_modules, load_system_records, load_system_versions, modules_dir_from_data_root
from resolver.models import ModuleRecord, Recommendation
from resolver.report_v3 import render_html_report_v3
from resolver.scoring import candidate_sort_key, satisfies_release_constraints
from resolver.sources import fetch_release_history, fetch_system_release_history
from resolver.versioning import compare_versions

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
_REPORT_SUGGEST_CACHE: dict[str, dict[str, Any]] = {}
_REPORT_SUGGEST_CACHE_LOCK = threading.Lock()
_IMPORT_HISTORY_LOCK = threading.Lock()
_IMPORT_HISTORY_MAX_ITEMS = 100


class SuggestionProviderError(ValueError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        message = str(payload.get("message") or payload.get("errorCode") or "suggestion_provider_error")
        super().__init__(message)


def _canonical_action_name(action: str) -> str:
    value = str(action or "").strip().lower()
    value = re.sub(r"\s+", "-", value.replace("_", "-"))
    compact = re.sub(r"[^a-z0-9]", "", value)
    if value in {"override-plan", "import-plan", "import"}:
        return "override-from-plan"
    if compact in {"overridefromplan", "importplan", "import"}:
        return "override-from-plan"
    return value


def _is_manifest_like_url(raw_url: str) -> bool:
    value = str(raw_url or "").strip().lower()
    return bool(
        value.endswith("/module.json")
        or value.endswith("/system.json")
        or value.endswith("/manifest.json")
    )


def _canonical_update_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return "" if _is_manifest_like_url(value) else value
    host = str(parsed.netloc or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return "" if _is_manifest_like_url(value) else value
    path = str(parsed.path or "").rstrip("/")
    parts = [part for part in path.split("/") if part]
    manifest_like = _is_manifest_like_url(value)

    if host == "raw.githubusercontent.com" and len(parts) >= 3:
        owner, repo, ref = parts[0], parts[1], parts[2]
        if owner and repo and ref:
            return f"https://github.com/{owner}/{repo}/releases/tag/{ref}"

    if host in {"github.com", "www.github.com"}:
        if "/releases/tag/" in path or "/releases/latest" in path:
            return value
        if "/releases/download/" in path:
            base, _, rest = path.partition("/releases/download/")
            tag = str(rest).split("/", 1)[0].strip()
            if base and tag:
                return f"{parsed.scheme}://{parsed.netloc}{base}/releases/tag/{tag}"
        if "/releases/latest/download/" in path:
            base, _, _ = path.partition("/releases/latest/download/")
            if base:
                return f"{parsed.scheme}://{parsed.netloc}{base}/releases/latest"
        if len(parts) >= 4 and parts[2] in {"blob", "raw"} and manifest_like:
            owner, repo, ref = parts[0], parts[1], parts[3]
            if owner and repo and ref:
                return f"{parsed.scheme}://{parsed.netloc}/{owner}/{repo}/releases/tag/{ref}"
        if manifest_like:
            return ""
        if len(parts) >= 2:
            return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        return value

    if host in {"gitlab.com", "www.gitlab.com"}:
        if "/-/releases/" in path:
            return value
        if "/-/archive/" in path:
            base, _, rest = path.partition("/-/archive/")
            tag = str(rest).split("/", 1)[0].strip()
            if base and tag:
                return f"{parsed.scheme}://{parsed.netloc}{base}/-/releases/{tag}"
        if "/-/raw/" in path or "/-/blob/" in path:
            marker = "/-/raw/" if "/-/raw/" in path else "/-/blob/"
            base, _, rest = path.partition(marker)
            ref = str(rest).split("/", 1)[0].strip()
            if base and ref:
                return f"{parsed.scheme}://{parsed.netloc}{base}/-/releases/{ref}"
        if manifest_like:
            return ""
        if len(parts) >= 2:
            return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        return value

    return "" if manifest_like else value


def _preferred_update_url(*urls: Any) -> str:
    for raw in urls:
        canonical = _canonical_update_url(str(raw or ""))
        if canonical:
            return canonical
    return ""


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
                runtime.action_engine.set_progress(job.job_id, 25, {"phase": "queued"})
                _append_audit(runtime.config, "action_worker_started", {"jobId": job.job_id, "action": job.action})
                try:
                    runtime.action_engine.set_progress(job.job_id, 60, {"phase": "running"})
                    result = _execute_action_job(runtime, job.action, job.payload, job.job_id)
                    runtime.action_engine.set_progress(job.job_id, 95, {"phase": "finalizing"})
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


def _report_suggest_cache_key(
    module_id: str,
    manifest_url: str,
    project_url: str,
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
) -> str:
    systems_key = json.dumps(installed_system_versions, sort_keys=True, separators=(",", ":"))
    return "|".join(
        [
            str(module_id or "").strip(),
            str(manifest_url or "").strip(),
            str(project_url or "").strip(),
            str(target_foundry_version or "").strip(),
            systems_key,
        ]
    )


def _source_for_module_id(sources: dict[str, Any], module_id: str) -> dict[str, Any]:
    clean_id = str(module_id or "").strip()
    if not clean_id:
        return {}
    direct = sources.get(clean_id)
    if isinstance(direct, dict):
        return direct
    lowered = clean_id.lower()
    for key, value in sources.items():
        if str(key or "").strip().lower() == lowered and isinstance(value, dict):
            return value
    return {}


def _invalidate_report_suggest_cache_for_modules(module_ids: list[str]) -> int:
    targets = {str(item or "").strip().lower() for item in module_ids if str(item or "").strip()}
    if not targets:
        return 0
    removed = 0
    with _REPORT_SUGGEST_CACHE_LOCK:
        stale_keys = []
        for key in list(_REPORT_SUGGEST_CACHE.keys()):
            module_prefix = str(key).split("|", 1)[0].strip().lower()
            if module_prefix in targets:
                stale_keys.append(key)
        for key in stale_keys:
            _REPORT_SUGGEST_CACHE.pop(key, None)
            removed += 1
    return removed


def _invalidate_planning_context_rows(runtime: AppRuntime, module_ids: list[str]) -> int:
    clean_ids = sorted({str(item or "").strip() for item in module_ids if str(item or "").strip()})
    if not clean_ids:
        return 0
    state_dir = getattr(runtime.config, "state_dir", None)
    if not state_dir:
        return 0
    db_path = Path(str(state_dir)) / "resolver.db"
    if not db_path.exists():
        return 0
    removed = 0
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            latest = connection.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not latest:
                return 0
            scan_run_id = int(latest["id"])
            placeholders = ", ".join("?" for _ in clean_ids)
            params: list[Any] = [scan_run_id]
            params.extend(clean_ids)
            row = connection.execute(
                f"""
                SELECT COUNT(*) FROM planning_context_rows
                WHERE scan_run_id = ? AND module_id IN ({placeholders})
                """,
                params,
            ).fetchone()
            removed = int(row[0]) if row else 0
            if removed > 0:
                connection.execute(
                    f"""
                    DELETE FROM planning_context_rows
                    WHERE scan_run_id = ? AND module_id IN ({placeholders})
                    """,
                    params,
                )
                connection.commit()
    except Exception:
        return 0
    return removed


def _resolve_suggestion_from_sources_with_caches(
    runtime: AppRuntime,
    module_id: str,
    sources: dict[str, Any],
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
    installed_modules_by_id: dict[str, ModuleRecord],
    resolution_cache: dict[str, Any],
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]],
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    clean_id = str(module_id or "").strip()
    if not clean_id:
        return None
    source = _source_for_module_id(sources, clean_id)
    manifest_url = str((source or {}).get("manifestUrl") or "").strip()
    project_url = str((source or {}).get("projectUrl") or "").strip()
    clean_manifest, clean_project = _normalize_source_urls(manifest_url=manifest_url, project_url=project_url)
    if not clean_manifest and not clean_project:
        return None
    key = _report_suggest_cache_key(
        module_id=clean_id,
        manifest_url=clean_manifest,
        project_url=clean_project,
        target_foundry_version=target_foundry_version,
        installed_system_versions=installed_system_versions,
    )
    suggestion: dict[str, Any] | None = None
    if not force_refresh:
        with _REPORT_SUGGEST_CACHE_LOCK:
            cached = _REPORT_SUGGEST_CACHE.get(key)
        suggestion = cached if isinstance(cached, dict) else None
    if suggestion is None:
        try:
            suggestion = _suggest_best_release_for_module_with_caches(
                module=_build_candidate_module(clean_id, manifest_url=clean_manifest, project_url=clean_project),
                target_foundry_version=target_foundry_version,
                installed_system_versions=installed_system_versions,
                cache_dir=runtime.config.cache_dir,
                installed_modules_by_id=installed_modules_by_id,
                resolution_cache=resolution_cache,
                history_cache=history_cache,
                force_refresh=force_refresh,
            )
        except Exception:
            suggestion = None
        if suggestion:
            with _REPORT_SUGGEST_CACHE_LOCK:
                _REPORT_SUGGEST_CACHE[key] = suggestion
                if len(_REPORT_SUGGEST_CACHE) > 3000:
                    stale_keys = list(_REPORT_SUGGEST_CACHE.keys())[:500]
                    for stale in stale_keys:
                        _REPORT_SUGGEST_CACHE.pop(stale, None)
    return suggestion


def _enrich_current_rows_with_precomputed_suggestions(
    runtime: AppRuntime,
    view: dict[str, Any],
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
    sources: dict[str, dict[str, Any]],
    installed_modules_by_id: dict[str, ModuleRecord],
    resolution_cache: dict[str, Any],
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]],
    force_refresh: bool = False,
) -> None:
    current = view.get("currentSystemUpgrades")
    if not isinstance(current, dict):
        return
    rows = current.get("rows")
    if not isinstance(rows, list) or not rows:
        return
    if not isinstance(sources, dict) or not sources:
        return

    for row in rows:
        if not isinstance(row, dict):
            continue
        module_id = str(row.get("module") or "").strip()
        if not module_id:
            continue
        recommended = str(row.get("recommendedVersion") or "").strip()
        release_url = _preferred_update_url(
            row.get("releaseUrl"),
            row.get("downloadUrl"),
            row.get("projectUrl"),
            row.get("manifestUrl"),
        )
        if release_url:
            row["releaseUrl"] = release_url
        if (recommended and recommended != "-") or release_url:
            continue
        suggestion = _resolve_suggestion_from_sources_with_caches(
            runtime=runtime,
            module_id=module_id,
            sources=sources,
            target_foundry_version=target_foundry_version,
            installed_system_versions=installed_system_versions,
            installed_modules_by_id=installed_modules_by_id,
            resolution_cache=resolution_cache,
            history_cache=history_cache,
            force_refresh=force_refresh,
        )
        if not suggestion:
            continue
        suggested_version = str(suggestion.get("recommendedVersion") or "").strip()
        suggested_url = _preferred_update_url(
            suggestion.get("releaseUrl"),
            suggestion.get("downloadUrl"),
            suggestion.get("projectUrl"),
            suggestion.get("manifestUrl"),
        )
        if suggested_version:
            row["recommendedVersion"] = suggested_version
        if suggested_url:
            row["releaseUrl"] = suggested_url


def _enrich_results_dependency_actions_with_precomputed_suggestions(
    runtime: AppRuntime,
    payload: dict[str, Any],
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
    sources: dict[str, dict[str, Any]],
    installed_modules_by_id: dict[str, ModuleRecord],
    resolution_cache: dict[str, Any],
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]],
    force_refresh: bool = False,
) -> None:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return
    if not isinstance(sources, dict) or not sources:
        return

    for result_row in results:
        if not isinstance(result_row, dict):
            continue
        dependency_actions = result_row.get("dependencyActions")
        if not isinstance(dependency_actions, list):
            continue
        for dep in dependency_actions:
            if not isinstance(dep, dict):
                continue
            dep_id = str(dep.get("module") or "").strip()
            if not dep_id:
                continue
            installed_version = str(dep.get("installedVersion") or "").strip()
            recommended_version = str(dep.get("recommendedVersion") or "").strip()
            manifest_url = str(dep.get("manifestUrl") or "").strip()
            download_url = str(dep.get("downloadUrl") or "").strip()
            # Enrich only unresolved/not-installed dependency actions.
            if installed_version or recommended_version or manifest_url or download_url:
                continue
            suggestion = _resolve_suggestion_from_sources_with_caches(
                runtime=runtime,
                module_id=dep_id,
                sources=sources,
                target_foundry_version=target_foundry_version,
                installed_system_versions=installed_system_versions,
                installed_modules_by_id=installed_modules_by_id,
                resolution_cache=resolution_cache,
                history_cache=history_cache,
                force_refresh=force_refresh,
            )
            if not suggestion:
                continue
            suggested_version = str(suggestion.get("recommendedVersion") or "").strip()
            suggested_manifest = str(suggestion.get("manifestUrl") or "").strip()
            suggested_download = str(suggestion.get("downloadUrl") or "").strip()
            suggested_release = _preferred_update_url(
                suggestion.get("releaseUrl"),
                suggestion.get("downloadUrl"),
                suggestion.get("projectUrl"),
                suggestion.get("manifestUrl"),
            )
            suggested_compat = suggestion.get("compatibility")
            suggested_sys_compat = suggestion.get("systemCompatibility")
            if suggested_version:
                dep["recommendedVersion"] = suggested_version
            if suggested_release:
                dep["releaseUrl"] = suggested_release
            if suggested_manifest:
                dep["manifestUrl"] = suggested_manifest
            if suggested_download:
                dep["downloadUrl"] = suggested_download
            if isinstance(suggested_compat, dict):
                dep["compatibility"] = suggested_compat
            if isinstance(suggested_sys_compat, dict):
                dep["systemCompatibility"] = suggested_sys_compat


def _has_missing_dependency_signal(reason: str, missing_count: int) -> bool:
    if int(missing_count or 0) > 0:
        return True
    text = str(reason or "").lower()
    return ("could not be resolved" in text) or ("missing dependenc" in text) or ("missing_dependency:" in text)


def _row_missing_count(row: dict[str, Any]) -> int:
    count = 0
    deps = row.get("missingDependencies")
    if isinstance(deps, list):
        count += len(deps)
    actions = row.get("dependencyActions")
    if isinstance(actions, list):
        for dep in actions:
            if not isinstance(dep, dict):
                continue
            if not str(dep.get("installedVersion") or "").strip() and not str(dep.get("recommendedVersion") or "").strip():
                count += 1
    return count


def _annotate_presentation_statuses(view: dict[str, Any]) -> None:
    current = view.get("currentSystemUpgrades")
    if isinstance(current, dict):
        rows = current.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                state = str(row.get("state") or "").strip().lower()
                reason = str(row.get("reason") or "")
                has_missing = _has_missing_dependency_signal(reason, _row_missing_count(row))
                if has_missing:
                    row["presentationStatus"] = "missing"
                elif state in {"blocked", "update", "ready"}:
                    row["presentationStatus"] = state
                else:
                    row["presentationStatus"] = "blocked"
                row["hasMissingDependencies"] = has_missing

    planner = view.get("systemUpgradePlanner")
    if isinstance(planner, dict):
        targets = planner.get("targets")
        if isinstance(targets, list):
            for target in targets:
                if not isinstance(target, dict):
                    continue
                system_rows = target.get("systemRows")
                if not isinstance(system_rows, list):
                    continue
                for system in system_rows:
                    if not isinstance(system, dict):
                        continue
                    for key, status in (
                        ("blockedModuleRows", "blocked"),
                        ("upgradableModuleRows", "update"),
                        ("compatibleModuleRows", "ready"),
                        ("unknownModuleRows", "blocked"),
                        ("localManifestManualModules", "blocked"),
                    ):
                        bucket = system.get(key)
                        if not isinstance(bucket, list):
                            continue
                        for row in bucket:
                            if not isinstance(row, dict):
                                continue
                            reason = str(row.get("reason") or "")
                            has_missing = _has_missing_dependency_signal(reason, _row_missing_count(row))
                            if has_missing:
                                row["presentationStatus"] = "missing"
                            else:
                                row["presentationStatus"] = status
                            row["hasMissingDependencies"] = has_missing


def _index_planner_targets_by_foundry(view: dict[str, Any]) -> None:
    planner = view.get("systemUpgradePlanner")
    if not isinstance(planner, dict):
        return
    targets = planner.get("targets")
    if not isinstance(targets, list):
        return
    by_foundry: dict[str, Any] = {}
    for target in targets:
        if not isinstance(target, dict):
            continue
        version = str(target.get("foundryVersion") or "").strip()
        if not version:
            continue
        by_foundry[version] = target
    planner["targetsByFoundry"] = by_foundry


def read_report_model(runtime: AppRuntime) -> dict[str, Any]:
    report_path = runtime.config.reports_dir / "module-resolver-latest.json"
    if not report_path.exists():
        raise FileNotFoundError("latest_report_not_found")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    _enrich_report_payload(runtime, payload)
    return {
        "generatedAt": payload.get("generatedAt"),
        "targetVersion": payload.get("targetVersion"),
        "dataRoot": payload.get("dataRoot"),
        "installedSystemVersions": payload.get("installedSystemVersions") or {},
        "worldUsage": payload.get("worldUsage") or [],
        "view": (payload.get("reportViews") or {}).get("v3") if isinstance((payload.get("reportViews") or {}).get("v3"), dict) else {},
        "results": payload.get("results") or [],
    }


def _enrich_report_payload(runtime: AppRuntime, payload: dict[str, Any], force_refresh_suggestions: bool = False) -> None:
    report_views = payload.get("reportViews") if isinstance(payload.get("reportViews"), dict) else {}
    view = report_views.get("v3") if isinstance(report_views.get("v3"), dict) else {}
    backup_management = view.get("backupManagement") if isinstance(view.get("backupManagement"), dict) else {}
    try:
        backup_management["applyHistory"] = load_apply_history(str(runtime.config.state_dir / "resolver.db"), limit=30)
    except Exception:
        backup_management["applyHistory"] = []
    view["backupManagement"] = backup_management
    try:
        target_foundry_version = str(payload.get("targetVersion") or "")
        installed_system_versions = (payload.get("installedSystemVersions") or {}) if isinstance(payload.get("installedSystemVersions"), dict) else {}
        sources = runtime.module_source_store.list_sources()
        modules_dir = str(modules_dir_from_data_root(runtime.config_store.get_data_root() or runtime.config.data_root))
        installed_modules = load_modules(modules_dir)
        installed_modules_by_id = {item.module_id: item for item in installed_modules}
        resolution_cache: dict[str, Any] = {}
        history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]] = {}
        _enrich_current_rows_with_precomputed_suggestions(
            runtime=runtime,
            view=view,
            target_foundry_version=target_foundry_version,
            installed_system_versions=installed_system_versions,
            sources=sources,
            installed_modules_by_id=installed_modules_by_id,
            resolution_cache=resolution_cache,
            history_cache=history_cache,
            force_refresh=force_refresh_suggestions,
        )
        _enrich_results_dependency_actions_with_precomputed_suggestions(
            runtime=runtime,
            payload=payload,
            target_foundry_version=target_foundry_version,
            installed_system_versions=installed_system_versions,
            sources=sources,
            installed_modules_by_id=installed_modules_by_id,
            resolution_cache=resolution_cache,
            history_cache=history_cache,
            force_refresh=force_refresh_suggestions,
        )
    except Exception:
        pass
    try:
        _annotate_presentation_statuses(view)
    except Exception:
        pass
    try:
        _index_planner_targets_by_foundry(view)
    except Exception:
        pass
    payload["reportViews"] = report_views


def _enrich_latest_report_file(runtime: AppRuntime, write_html: bool = False, force_refresh_suggestions: bool = False) -> None:
    report_path = runtime.config.reports_dir / "module-resolver-latest.json"
    if not report_path.exists():
        return
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    _enrich_report_payload(runtime, payload, force_refresh_suggestions=force_refresh_suggestions)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if write_html:
        try:
            html = render_html_report_v3(payload)
            html_path = runtime.config.reports_dir / "module-resolver-latest.html"
            html_path.write_text(html, encoding="utf-8")
        except Exception:
            # Keep enrichment resilient even if HTML regeneration fails.
            pass


def export_latest_report_html(runtime: AppRuntime, output_path: str = "") -> dict[str, Any]:
    report_path = runtime.config.reports_dir / "module-resolver-latest.json"
    if not report_path.exists():
        raise FileNotFoundError("latest_report_not_found")
    _enrich_latest_report_file(runtime, write_html=False, force_refresh_suggestions=False)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    html = render_html_report_v3(payload)
    target = Path(output_path).expanduser().resolve() if str(output_path or "").strip() else (runtime.config.reports_dir / "module-resolver-latest.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return {
        "ok": True,
        "path": str(target),
        "generatedAt": _utc_now_iso(),
    }


def export_modules_snapshot(runtime: AppRuntime, output_path: str = "", include_data: bool = False) -> dict[str, Any]:
    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    foundry_version, foundry_source = detect_foundry_version(normalized_root)
    systems = load_system_versions(normalized_root)
    modules_dir = str(modules_dir_from_data_root(normalized_root))
    modules = load_modules(modules_dir)
    payload = {
        "generatedAt": _utc_now_iso(),
        "dataRoot": normalized_root,
        "foundryVersion": foundry_version,
        "foundryVersionSource": foundry_source,
        "systems": systems,
        "modules": [
            {
                "module": item.module_id,
                "title": item.title,
                "version": item.version,
                "manifestUrl": item.manifest_url,
                "projectUrl": item.project_url,
                "path": item.path,
            }
            for item in modules
        ],
    }
    target = Path(output_path).expanduser().resolve() if str(output_path or "").strip() else (runtime.config.reports_dir / "module-snapshot-latest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    response = {
        "ok": True,
        "path": str(target),
        "modulesCount": len(modules),
        "systemsCount": len(systems),
        "foundryVersion": foundry_version,
        "generatedAt": payload["generatedAt"],
    }
    if include_data:
        response["snapshotData"] = payload
    return response


def _import_history_path(runtime: AppRuntime) -> Path:
    return runtime.config.state_dir / "import-history.json"


def _read_import_history_items(runtime: AppRuntime) -> list[dict[str, Any]]:
    path = _import_history_path(runtime)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _write_import_history_items(runtime: AppRuntime, items: list[dict[str, Any]]) -> None:
    path = _import_history_path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _import_history_entry(result: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "generatedAt": str(result.get("generatedAt") or _utc_now_iso()),
        "action": str(result.get("action") or "override-from-plan"),
        "profile": str(result.get("profile") or ""),
        "planPath": str(result.get("planPath") or ""),
        "appliedCount": int(result.get("appliedCount") or 0),
        "skippedCount": int(result.get("skippedCount") or 0),
        "failureCount": int(result.get("failureCount") or 0),
    }
    failures = result.get("failures")
    if isinstance(failures, list):
        entry["failures"] = [row for row in failures[:100] if isinstance(row, dict)]
    details = result.get("results")
    if isinstance(details, dict):
        entry["results"] = details
    refresh = result.get("reportRefresh")
    if isinstance(refresh, dict):
        entry["reportRefresh"] = {
            "ok": bool(refresh.get("ok")),
            "returncode": int(refresh.get("returncode") or 0) if str(refresh.get("returncode") or "").strip() else 0,
            "stdout": str(refresh.get("stdout") or "")[:2000],
            "stderr": str(refresh.get("stderr") or "")[:2000],
        }
    return entry


def append_import_history(runtime: AppRuntime, result: dict[str, Any]) -> None:
    with _IMPORT_HISTORY_LOCK:
        items = _read_import_history_items(runtime)
        items.insert(0, _import_history_entry(result))
        _write_import_history_items(runtime, items[:_IMPORT_HISTORY_MAX_ITEMS])


def read_import_history(runtime: AppRuntime, limit: int = 20) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 200))
    with _IMPORT_HISTORY_LOCK:
        items = _read_import_history_items(runtime)
    return {
        "ok": True,
        "items": items[:safe_limit],
    }


def _update_artifacts_dir(runtime: AppRuntime) -> Path:
    return runtime.config.state_dir / "update-artifacts"


def _safe_plan_id(raw: Any) -> str:
    value = str(raw or "").strip()
    return re.sub(r"[^a-zA-Z0-9._-]", "", value)


def _artifact_json_path(runtime: AppRuntime, plan_id: str) -> Path:
    safe_id = _safe_plan_id(plan_id)
    if not safe_id:
        raise ValueError("plan_id_required")
    return _update_artifacts_dir(runtime) / f"{safe_id}.json"


def _artifact_bundle_path(runtime: AppRuntime, plan_id: str) -> Path:
    safe_id = _safe_plan_id(plan_id)
    if not safe_id:
        raise ValueError("plan_id_required")
    return _update_artifacts_dir(runtime) / f"{safe_id}.zip"


def _new_plan_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    nonce = f"{int(time.time_ns()) % 1_000_000_000:09d}"
    return f"plan-{stamp}-{nonce}"


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_version_token(raw: Any) -> str:
    value = str(raw or "").strip()
    return value if value else "-"


def _collect_backup_entries(backup_paths: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in backup_paths:
        path_value = str(raw or "").strip()
        if not path_value:
            continue
        candidate = Path(path_value)
        exists = candidate.exists() and candidate.is_file()
        size_bytes = int(candidate.stat().st_size) if exists else 0
        digest = _hash_file(candidate) if exists else ""
        entries.append(
            {
                "path": str(candidate),
                "exists": bool(exists),
                "sizeBytes": size_bytes,
                "sha256": digest,
            }
        )
    return entries


def _extract_foundry_target_from_override_payload(payload: dict[str, Any], profile: str, default_target: str) -> str:
    plan_content = str(payload.get("planContent") or "").strip()
    plan_path = str(payload.get("planPath") or "").strip()
    plan_payload: dict[str, Any] = {}
    try:
        if plan_content:
            parsed = json.loads(plan_content)
            if isinstance(parsed, dict):
                plan_payload = parsed
        elif plan_path:
            candidate = Path(plan_path).expanduser().resolve()
            if candidate.exists() and candidate.is_file():
                parsed = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    plan_payload = parsed
    except Exception:
        plan_payload = {}
    if not plan_payload:
        return default_target
    sections: list[str]
    if profile == "current":
        sections = ["current"]
    elif profile == "destiny":
        sections = ["destiny"]
    else:
        sections = ["destiny", "current"]
    for section_name in sections:
        section = plan_payload.get(section_name)
        if not isinstance(section, dict):
            continue
        foundry_version = str(section.get("foundryVersion") or "").strip()
        if foundry_version:
            return foundry_version
    return default_target


def _artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    modules = artifact.get("modules") if isinstance(artifact.get("modules"), list) else []
    systems = artifact.get("systems") if isinstance(artifact.get("systems"), list) else []
    backups = artifact.get("backups") if isinstance(artifact.get("backups"), list) else []
    backup_total_bytes = sum(int((item or {}).get("sizeBytes") or 0) for item in backups if isinstance(item, dict))
    return {
        "planId": str(artifact.get("planId") or ""),
        "scanRunId": int(artifact.get("scanRunId") or 0) if str(artifact.get("scanRunId") or "").strip() else 0,
        "createdAt": str(artifact.get("createdAt") or ""),
        "action": str(artifact.get("action") or ""),
        "foundryCurrentVersion": str(artifact.get("foundryCurrentVersion") or ""),
        "foundryTargetVersion": str(artifact.get("foundryTargetVersion") or ""),
        "systemsCount": len(systems),
        "modulesCount": len(modules),
        "backupsCount": len(backups),
        "backupTotalBytes": backup_total_bytes,
        "failureCount": int((artifact.get("summary") or {}).get("failed") or 0) if isinstance(artifact.get("summary"), dict) else 0,
        "appliedCount": int((artifact.get("summary") or {}).get("applied") or 0) if isinstance(artifact.get("summary"), dict) else 0,
    }


def _write_update_artifact(runtime: AppRuntime, artifact: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(artifact.get("planId") or "").strip()
    if not plan_id:
        raise ValueError("plan_id_required")
    root = _update_artifacts_dir(runtime)
    root.mkdir(parents=True, exist_ok=True)
    target = _artifact_json_path(runtime, plan_id)
    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = _artifact_summary(artifact)
    summary["path"] = str(target)
    return summary


def _build_update_artifact_from_override(
    runtime: AppRuntime,
    output: dict[str, Any],
    payload: dict[str, Any],
    effective_data_root: str,
) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    results = output.get("results") if isinstance(output.get("results"), dict) else {}
    modules_raw = results.get("modules") if isinstance(results, dict) and isinstance(results.get("modules"), list) else []
    systems_raw = results.get("systems") if isinstance(results, dict) and isinstance(results.get("systems"), list) else []
    profile = str(output.get("profile") or "current")
    foundry_current, _ = detect_foundry_version(effective_data_root)
    foundry_target = _extract_foundry_target_from_override_payload(payload, profile, foundry_current)
    modules: list[dict[str, Any]] = []
    systems: list[dict[str, Any]] = []
    backup_paths: list[str] = []
    applied_count = 0
    skipped_count = 0
    failed_count = int(output.get("failureCount") or 0)
    for row in systems_raw:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip() or "unknown"
        if status == "applied":
            applied_count += 1
        elif status in {"already", "skipped"}:
            skipped_count += 1
        backup_path = str(row.get("backupPath") or "").strip()
        if backup_path:
            backup_paths.append(backup_path)
        systems.append(
            {
                "name": str(row.get("systemId") or "").strip(),
                "currentVersion": _normalize_version_token(row.get("fromVersion") or row.get("installedVersion")),
                "targetVersion": _normalize_version_token(row.get("toVersion") or row.get("targetVersion") or row.get("requestedVersion")),
                "status": status,
                "backupPath": backup_path,
            }
        )
    for row in modules_raw:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip() or "unknown"
        if status == "applied":
            applied_count += 1
        elif status in {"already", "skipped"}:
            skipped_count += 1
        backup_path = str(row.get("backupPath") or "").strip()
        if backup_path:
            backup_paths.append(backup_path)
        modules.append(
            {
                "name": str(row.get("moduleId") or "").strip(),
                "currentVersion": _normalize_version_token(row.get("fromVersion") or row.get("installedVersion")),
                "targetVersion": _normalize_version_token(row.get("toVersion") or row.get("targetVersion") or row.get("requestedVersion")),
                "status": status,
                "backupPath": backup_path,
            }
        )
    if not modules and not systems:
        return None
    backups = _collect_backup_entries(backup_paths)
    artifact: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "planId": _new_plan_id(),
        "createdAt": _utc_now_iso(),
        "action": "override-from-plan",
        "profile": profile,
        "foundryCurrentVersion": str(foundry_current or ""),
        "foundryTargetVersion": str(foundry_target or foundry_current or ""),
        "systems": systems,
        "modules": modules,
        "backups": backups,
        "summary": {
            "applied": applied_count,
            "skipped": skipped_count,
            "failed": failed_count,
        },
        "source": {
            "planPath": str(output.get("planPath") or ""),
            "dataRoot": effective_data_root,
        },
    }
    return artifact


def _load_latest_apply_scan_row(runtime: AppRuntime) -> dict[str, Any] | None:
    history = load_apply_history(str(runtime.config.state_dir / "resolver.db"), limit=1)
    if not history:
        return None
    row = history[0] if isinstance(history[0], dict) else None
    if not row:
        return None
    scan_id = int(row.get("scanRunId") or 0)
    if scan_id <= 0:
        return None
    payload = load_scan_run_payload(str(runtime.config.state_dir / "resolver.db"), scan_id)
    if not isinstance(payload, dict):
        return None
    row["scanPayload"] = payload
    return row


def _build_update_artifact_from_apply_scan(
    runtime: AppRuntime,
    apply_row: dict[str, Any],
    effective_data_root: str,
) -> dict[str, Any] | None:
    payload = apply_row.get("scanPayload") if isinstance(apply_row.get("scanPayload"), dict) else {}
    if not payload:
        return None
    target_foundry = str(payload.get("targetVersion") or "").strip()
    foundry_current, _ = detect_foundry_version(effective_data_root)
    installed_system_versions = payload.get("installedSystemVersions") if isinstance(payload.get("installedSystemVersions"), dict) else {}
    systems: list[dict[str, Any]] = []
    for system_id, system_version in sorted(installed_system_versions.items()):
        systems.append(
            {
                "name": str(system_id),
                "currentVersion": _normalize_version_token(system_version),
                "targetVersion": _normalize_version_token(system_version),
                "status": "ready",
                "backupPath": "",
            }
        )

    results_rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    titles: dict[str, str] = {}
    for row in results_rows:
        if not isinstance(row, dict):
            continue
        module_id = str(row.get("module") or "").strip()
        if not module_id:
            continue
        title = str(row.get("title") or "").strip()
        titles[module_id] = title or module_id
    apply_actions = payload.get("dependencyApplyActions") if isinstance(payload.get("dependencyApplyActions"), list) else []
    modules: list[dict[str, Any]] = []
    backup_paths: list[str] = []
    applied_count = 0
    for row in apply_actions:
        if not isinstance(row, dict):
            continue
        module_id = str(row.get("module") or "").strip()
        if not module_id:
            continue
        from_version = _normalize_version_token(row.get("fromVersion"))
        to_version = _normalize_version_token(row.get("toVersion"))
        backup_path = str(row.get("backupPath") or "").strip()
        if backup_path:
            backup_paths.append(backup_path)
        status = "applied" if from_version != to_version else "ready"
        if status == "applied":
            applied_count += 1
        modules.append(
            {
                "name": titles.get(module_id) or module_id,
                "moduleId": module_id,
                "currentVersion": from_version,
                "targetVersion": to_version,
                "status": status,
                "backupPath": backup_path,
            }
        )
    if not modules and not systems:
        return None
    backups = _collect_backup_entries(backup_paths)
    skipped_count = max(int(apply_row.get("modulesChangedCount") or 0) - applied_count, 0)
    artifact: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "planId": _new_plan_id(),
        "createdAt": str(payload.get("generatedAt") or _utc_now_iso()),
        "action": "apply",
        "scanRunId": int(apply_row.get("scanRunId") or 0),
        "foundryCurrentVersion": str(foundry_current or target_foundry or ""),
        "foundryTargetVersion": str(target_foundry or foundry_current or ""),
        "systems": systems,
        "modules": modules,
        "backups": backups,
        "summary": {
            "applied": applied_count,
            "skipped": skipped_count,
            "failed": 0,
        },
        "source": {
            "dataRoot": effective_data_root,
        },
    }
    return artifact


def list_update_artifacts(runtime: AppRuntime, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    root = _update_artifacts_dir(runtime)
    if not root.exists():
        return {"ok": True, "items": []}
    items: list[dict[str, Any]] = []
    for file_path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        summary = _artifact_summary(payload)
        summary["filePath"] = str(file_path)
        summary["fileBytes"] = int(file_path.stat().st_size)
        items.append(summary)
        if len(items) >= safe_limit:
            break
    return {"ok": True, "items": items}


def get_update_artifact(runtime: AppRuntime, plan_id: str) -> dict[str, Any]:
    path = _artifact_json_path(runtime, plan_id)
    if not path.exists():
        raise FileNotFoundError("update_artifact_not_found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("update_artifact_invalid")
    return {"ok": True, "artifact": payload}


def build_update_artifact_bundle(runtime: AppRuntime, plan_id: str, include_backup_data: bool = True) -> dict[str, Any]:
    artifact_response = get_update_artifact(runtime, plan_id)
    artifact = artifact_response.get("artifact") if isinstance(artifact_response.get("artifact"), dict) else {}
    if not artifact:
        raise FileNotFoundError("update_artifact_not_found")
    bundle_path = _artifact_bundle_path(runtime, plan_id)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    backup_entries = artifact.get("backups") if isinstance(artifact.get("backups"), list) else []
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_safe_plan_id(plan_id)}.json", json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
        if include_backup_data:
            for index, entry in enumerate(backup_entries):
                if not isinstance(entry, dict):
                    continue
                source_path = Path(str(entry.get("path") or "").strip())
                if not source_path.exists() or not source_path.is_file():
                    continue
                archive_name = f"backups/{index:03d}-{source_path.name}"
                archive.write(source_path, arcname=archive_name)
    return {
        "ok": True,
        "planId": str(artifact.get("planId") or plan_id),
        "path": str(bundle_path),
        "fileName": f"{_safe_plan_id(plan_id)}.zip",
        "generatedAt": _utc_now_iso(),
    }


def read_planning_context(
    runtime: AppRuntime,
    foundry_version: str,
    system_id: str = "",
    system_version: str = "",
    limit: int = 5000,
) -> dict[str, Any]:
    db_path = str(runtime.config.state_dir / "resolver.db")
    return load_planning_context_rows(
        database_path=db_path,
        foundry_version=foundry_version,
        system_id=system_id,
        system_version=system_version,
        limit=limit,
    )


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
    def _is_blocking_issue(issue: str) -> bool:
        token = str(issue or "").strip().lower()
        if not token:
            return False
        # Missing static assets are noisy in legacy modules and should not block updates.
        if token.startswith("missing_file:"):
            return False
        # Keep malformed/unreadable manifests as hard blockers.
        if token.startswith("manifest_read_error:"):
            return True
        # Unknown issue types remain blocking by default.
        return True

    for row in scoped:
        issues = [str(item) for item in (row.get("issues") or [])]
        warnings = row.get("warnings") or []
        has_missing_dependency = any(str(item).startswith("missing_dependency:") for item in warnings)
        has_blocking_issue = any(_is_blocking_issue(issue) for issue in issues)
        if has_blocking_issue or has_missing_dependency:
            blocking_rows.append({"module": row.get("module"), "title": row.get("title"), "issues": issues if isinstance(issues, list) else [], "warnings": warnings if isinstance(warnings, list) else []})
    return {"ok": True, "blocked": len(blocking_rows) > 0, "reason": "module_health_gate_failed" if blocking_rows else "module_health_gate_ok", "count": len(scoped), "rows": blocking_rows}


def _filter_apply_modules_by_health_gate(selected_modules: list[str], preflight_gate: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    requested = [str(item).strip() for item in (selected_modules or []) if str(item).strip()]
    if not requested:
        return [], []
    gate = preflight_gate or {}
    blocked_rows = gate.get("rows") if isinstance(gate, dict) else []
    blocked_ids = {
        str((row or {}).get("module") or "").strip().lower()
        for row in (blocked_rows if isinstance(blocked_rows, list) else [])
        if str((row or {}).get("module") or "").strip()
    }
    if not blocked_ids:
        return requested, []
    allowed: list[str] = []
    skipped: list[str] = []
    seen_allowed: set[str] = set()
    seen_skipped: set[str] = set()
    for module_id in requested:
        key = module_id.lower()
        if key in blocked_ids:
            if key not in seen_skipped:
                skipped.append(module_id)
                seen_skipped.add(key)
            continue
        if key not in seen_allowed:
            allowed.append(module_id)
            seen_allowed.add(key)
    return allowed, skipped


def _build_cli_args_from_action(action: str, payload: dict[str, Any]) -> tuple[list[str], bool, str]:
    normalized_action = _canonical_action_name(action)
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


def _execute_action_job(runtime: AppRuntime, action: str, payload: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    clean_action = _canonical_action_name(action)
    if clean_action == "rollback-batch":
        scan_run_id = int(payload.get("scanRunId") or 0)
        if scan_run_id <= 0:
            raise ValueError("scan_run_id_required")
        return execute_rollback(runtime, scan_run_id)
    if clean_action == "override-from-plan":
        _ensure_foundry_offline(runtime)
        lock_payload = runtime.lock_store.acquire(action="override-from-plan")
        try:
            plan_path = str(payload.get("planPath") or "").strip()
            plan_content = str(payload.get("planContent") or "")
            profile = _coerce_profile(payload.get("profile"))
            def _import_progress(meta: dict[str, Any]) -> None:
                if not job_id:
                    return
                total_items = max(int(meta.get("totalItems") or 0), 1)
                processed_items = max(0, int(meta.get("processedItems") or 0))
                percent = 10 + int(min(processed_items, total_items) * 80 / total_items)
                phase = str(meta.get("phase") or "").strip().lower()
                if phase == "counting":
                    percent = max(percent, 8)
                elif phase == "finalizing":
                    percent = max(percent, 95)
                runtime.action_engine.set_progress(job_id, max(0, min(percent, 99)), meta)

            output = apply_override_from_plan(
                runtime,
                plan_path=plan_path,
                plan_content=plan_content,
                profile=profile,
                progress_callback=_import_progress,
            )
            if job_id:
                runtime.action_engine.set_progress(
                    job_id,
                    95,
                    {
                        "phase": "finalizing",
                        "totalItems": int((output.get("targets") or {}).get("systems") or 0)
                        + int((output.get("targets") or {}).get("modules") or 0),
                        "processedItems": int((output.get("appliedCount") or 0))
                        + int((output.get("skippedCount") or 0))
                        + int((output.get("failureCount") or 0)),
                    },
                )
            report_refresh = _refresh_report_after_override(runtime)
            output["reportRefresh"] = report_refresh
            output["lock"] = lock_payload
            output["dataRoot"] = runtime.config_store.get_data_root() or runtime.config.data_root
            try:
                append_import_history(runtime, output)
            except Exception:
                pass
            try:
                artifact_payload = _build_update_artifact_from_override(
                    runtime=runtime,
                    output=output,
                    payload=payload,
                    effective_data_root=(runtime.config_store.get_data_root() or runtime.config.data_root),
                )
                if artifact_payload:
                    output["updateArtifact"] = _write_update_artifact(runtime, artifact_payload)
            except Exception:
                pass
            if not bool(report_refresh.get("ok")):
                details = str(report_refresh.get("stderr") or "").strip()
                short_details = details[:500] if details else "unknown error"
                raise RuntimeError(f"Import finished, but report refresh failed: {short_details}")
            return output
        finally:
            runtime.lock_store.release()

    effective_data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    effective_payload: dict[str, Any] = dict(payload)
    modules = _normalize_modules(effective_payload.get("modules"))
    lock_action_name = _canonical_action_name(action)
    if lock_action_name not in {"dry-run", "apply", "force-compat", "cleanup-backups"}:
        lock_action_name = "apply" if lock_action_name == "override-from-plan" else lock_action_name
    maintenance = lock_action_name in {"apply", "force-compat", "cleanup-backups"}
    lock_payload: dict[str, Any] | None = None
    if maintenance:
        lock_payload = runtime.lock_store.acquire(action=lock_action_name)
    try:
        preflight_gate: dict[str, Any] | None = None
        if _canonical_action_name(action) == "apply":
            # Legacy clients may submit unscoped apply payloads (no explicit modules list).
            # In that case, avoid hard preflight blocking based on the full module inventory.
            if modules:
                preflight_gate = _evaluate_apply_health_gate(effective_data_root, modules)
                if preflight_gate.get("blocked"):
                    filtered_modules, skipped_modules = _filter_apply_modules_by_health_gate(modules, preflight_gate)
                    preflight_gate["skippedModules"] = skipped_modules
                    if filtered_modules:
                        modules = filtered_modules
                        effective_payload = dict(effective_payload)
                        effective_payload["modules"] = filtered_modules
                    else:
                        # If a scoped apply request only contains blocked modules, do not fail hard.
                        # Return a successful no-op with explicit skip details so UI can inform the user.
                        return {
                            "ok": True,
                            "returnCode": 0,
                            "action": "apply",
                            "generatedAt": _utc_now_iso(),
                            "dataRoot": effective_data_root,
                            "lock": lock_payload,
                            "preflight": preflight_gate,
                            "skippedModules": skipped_modules,
                            "message": "All selected modules were skipped by module health gate.",
                        }
        extra_args, _, action_name = _build_cli_args_from_action(action, effective_payload)
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
        if action_name in {"dry-run", "apply", "force-compat"}:
            try:
                _enrich_latest_report_file(runtime, force_refresh_suggestions=(action_name == "dry-run"))
            except Exception:
                # Keep action successful even if post-enrichment fails.
                pass
        if action_name == "apply":
            if modules:
                postflight_gate = _evaluate_apply_health_gate(effective_data_root, modules)
                output["postflight"] = postflight_gate
                if postflight_gate.get("blocked"):
                    output["ok"] = False
                    raise RuntimeError("Apply finished but post-check found invalid modules or missing dependencies.")
            try:
                latest_apply = _load_latest_apply_scan_row(runtime)
                if latest_apply:
                    artifact_payload = _build_update_artifact_from_apply_scan(
                        runtime=runtime,
                        apply_row=latest_apply,
                        effective_data_root=effective_data_root,
                    )
                    if artifact_payload:
                        output["updateArtifact"] = _write_update_artifact(runtime, artifact_payload)
            except Exception:
                pass
        return output
    finally:
        if maintenance:
            runtime.lock_store.release()


def _refresh_report_after_override(runtime: AppRuntime) -> dict[str, Any]:
    effective_data_root = runtime.config_store.get_data_root() or runtime.config.data_root
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
        "--log-file",
        str(runtime.config.reports_dir / "module-resolver-latest.log"),
        "--dry-run",
        "--batch-size",
        "10",
    ]
    result = subprocess.run(cmd, cwd=str(runtime.config.tool_root), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {
            "ok": False,
            "returnCode": int(result.returncode),
            "stderr": str(result.stderr or "")[-20000:],
        }
    try:
        _enrich_latest_report_file(runtime, force_refresh_suggestions=True)
    except Exception:
        pass
    generated_at = ""
    report_path = runtime.config.reports_dir / "module-resolver-latest.json"
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            generated_at = str(payload.get("generatedAt") or "")
        except Exception:
            generated_at = ""
    return {"ok": True, "generatedAt": generated_at}


def _build_candidate_module(module_id: str, manifest_url: str, project_url: str) -> ModuleRecord:
    clean_id = str(module_id or "").strip()
    if manifest_url:
        fetch_url = _normalize_manifest_fetch_url(manifest_url)
        request = Request(fetch_url, headers={"User-Agent": "foundry-module-version-resolver/0.1"})
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


def _looks_like_manifest_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    path = str(urlparse(value).path or "")
    return path.endswith("/module.json") or path.endswith("/system.json") or path.endswith("/manifest.json")


def _normalize_manifest_fetch_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    parsed = urlparse(clean)
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "")

    if "github.com" in host and "/blob/" in path:
        parts = path.strip("/").split("/")
        if len(parts) >= 5 and parts[2] == "blob":
            owner = parts[0]
            repo = parts[1]
            ref = parts[3]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"

    if "gitlab.com" in host and "/-/blob/" in path:
        return clean.replace("/-/blob/", "/-/raw/", 1)

    return clean


def _looks_like_foundry_package_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "")
    return host.endswith("foundryvtt.com") and path.startswith("/packages/")


def _project_url_from_manifest_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    try:
        parsed = urlparse(clean)
    except ValueError:
        return ""
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "")
    parts = [part for part in path.split("/") if part]
    if host == "raw.githubusercontent.com" and len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        if owner and repo:
            return f"https://github.com/{owner}/{repo}"
    if host in {"github.com", "www.github.com"} and len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        if owner and repo:
            return f"https://github.com/{owner}/{repo}"
    if host == "gitlab.com" and len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        if owner and repo:
            return f"https://gitlab.com/{owner}/{repo}"
    return ""


def _resolve_foundry_package_source(package_url: str) -> tuple[str, str]:
    clean = str(package_url or "").strip()
    if not _looks_like_foundry_package_url(clean):
        return "", ""
    try:
        req = Request(clean, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=12) as response:
            payload = response.read()
    except Exception:
        return "", ""
    try:
        html = payload.decode("utf-8", errors="ignore")
    except Exception:
        return "", ""
    if not html:
        return "", ""

    manifest_candidates = re.findall(r"https?://[^\"'\s<>]+/(?:module|system|manifest)\.json(?:\?[^\"'\s<>]*)?", html, flags=re.IGNORECASE)
    project_candidates = re.findall(r"https?://(?:www\.)?(?:github\.com|gitlab\.com)/[^\"'\s<>]+", html, flags=re.IGNORECASE)

    clean_manifest = ""
    clean_project = ""
    if manifest_candidates:
        for candidate in manifest_candidates:
            normalized = _normalize_manifest_fetch_url(candidate)
            if _looks_like_manifest_url(normalized):
                clean_manifest = normalized
                break
    if project_candidates:
        for candidate in project_candidates:
            try:
                parsed = urlparse(candidate)
            except ValueError:
                continue
            parts = [part for part in str(parsed.path or "").split("/") if part]
            if len(parts) >= 2:
                clean_project = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
                break
    if not clean_project and clean_manifest:
        clean_project = _project_url_from_manifest_url(clean_manifest)
    return clean_manifest, clean_project


def _normalize_source_urls(manifest_url: str, project_url: str) -> tuple[str, str]:
    clean_manifest = str(manifest_url or "").strip()
    clean_project = str(project_url or "").strip()
    if not clean_manifest and clean_project and _looks_like_foundry_package_url(clean_project):
        resolved_manifest, resolved_project = _resolve_foundry_package_source(clean_project)
        if resolved_manifest:
            clean_manifest = resolved_manifest
        if resolved_project:
            clean_project = resolved_project
    if not clean_manifest:
        return "", clean_project
    if clean_project:
        return clean_manifest, clean_project
    if _looks_like_manifest_url(clean_manifest):
        return clean_manifest, ""
    parsed = urlparse(clean_manifest)
    host = str(parsed.netloc or "").lower()
    if "github.com" in host or "gitlab.com" in host:
        return "", clean_manifest
    return clean_manifest, ""


def _is_concrete_version(raw: Any) -> bool:
    value = str(raw or "").strip()
    return bool(value and value != "-")


def _version_tokens_match(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return a.lstrip("vV") == b.lstrip("vV")


def _coerce_profile(raw: Any) -> str:
    profile = str(raw or "").strip().lower()
    if profile in {"current", "destiny", "both"}:
        return profile
    return "current"


def _extract_plan_targets(plan_payload: dict[str, Any], profile: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    selected_sections: list[tuple[str, dict[str, Any]]] = []
    current = plan_payload.get("current") if isinstance(plan_payload.get("current"), dict) else {}
    destiny = plan_payload.get("destiny") if isinstance(plan_payload.get("destiny"), dict) else {}
    if profile == "current":
        selected_sections = [("current", current)]
    elif profile == "destiny":
        selected_sections = [("destiny", destiny)]
    else:
        selected_sections = [("current", current), ("destiny", destiny)]

    module_targets: dict[str, dict[str, Any]] = {}
    system_targets: dict[str, dict[str, Any]] = {}

    for section_name, section in selected_sections:
        rows = section.get("rows") if isinstance(section.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "").strip().lower()
            if kind == "module":
                module_id = str(row.get("moduleId") or "").strip()
                if not module_id:
                    continue
                preferred = str(row.get("installedVersion") or "").strip() if section_name == "current" else str(row.get("recommendedVersion") or "").strip()
                fallback = str(row.get("recommendedVersion") or "").strip() if section_name == "current" else str(row.get("installedVersion") or "").strip()
                target_version = preferred if _is_concrete_version(preferred) else fallback
                if not _is_concrete_version(target_version):
                    continue
                source_url = str(row.get("releaseUrl") or row.get("targetUrl") or "").strip()
                existing = module_targets.get(module_id)
                payload = {
                    "moduleId": module_id,
                    "targetVersion": target_version,
                    "sourceUrl": source_url,
                    "title": str(row.get("title") or module_id),
                }
                if existing is None:
                    module_targets[module_id] = payload
                else:
                    try:
                        if compare_versions(str(payload.get("targetVersion") or ""), str(existing.get("targetVersion") or "")) > 0:
                            module_targets[module_id] = payload
                            continue
                    except Exception:
                        pass
                    if not str(existing.get("sourceUrl") or "").strip() and source_url:
                        existing["sourceUrl"] = source_url
            elif kind == "system":
                system_id = str(row.get("systemId") or "").strip()
                if not system_id:
                    continue
                target_version = str(row.get("targetVersion") or row.get("recommendedVersion") or row.get("installedVersion") or "").strip()
                if not _is_concrete_version(target_version):
                    continue
                source_url = str(row.get("targetUrl") or row.get("releaseUrl") or "").strip()
                existing = system_targets.get(system_id)
                payload = {
                    "systemId": system_id,
                    "targetVersion": target_version,
                    "sourceUrl": source_url,
                    "title": str(row.get("title") or system_id),
                }
                if existing is None:
                    system_targets[system_id] = payload
                else:
                    try:
                        if compare_versions(str(payload.get("targetVersion") or ""), str(existing.get("targetVersion") or "")) > 0:
                            system_targets[system_id] = payload
                            continue
                    except Exception:
                        pass
                    if not str(existing.get("sourceUrl") or "").strip() and source_url:
                        existing["sourceUrl"] = source_url

        section_system_id = str(section.get("activeSystemId") or "").strip()
        section_system_version = str(section.get("selectedSystemVersion") or "").strip()
        if section_system_id and _is_concrete_version(section_system_version):
            existing = system_targets.get(section_system_id)
            payload = {
                "systemId": section_system_id,
                "targetVersion": section_system_version,
                "sourceUrl": "",
                "title": section_system_id,
            }
            if existing is None:
                system_targets[section_system_id] = payload
            else:
                try:
                    if compare_versions(str(payload.get("targetVersion") or ""), str(existing.get("targetVersion") or "")) > 0:
                        system_targets[section_system_id] = payload
                except Exception:
                    pass

    return module_targets, system_targets


def _candidate_from_target(target_id: str, title: str, current_version: str, source_url: str) -> ModuleRecord:
    source = str(source_url or "").strip()
    manifest_url = ""
    project_url = ""
    if _looks_like_manifest_url(source):
        manifest_url, project_url = _normalize_source_urls(source, "")
    else:
        normalized_source = source
        parsed = urlparse(source)
        host = str(parsed.netloc or "").lower()
        parts = [part for part in str(parsed.path or "").split("/") if part]
        if host in {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com", "raw.githubusercontent.com"}:
            if host == "raw.githubusercontent.com" and len(parts) >= 2:
                normalized_source = f"{parsed.scheme}://github.com/{parts[0]}/{parts[1]}"
            elif len(parts) >= 2:
                normalized_source = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
        manifest_url, project_url = _normalize_source_urls("", normalized_source)
    return ModuleRecord(
        module_id=target_id,
        title=title or target_id,
        version=current_version or "0.0.0",
        manifest_url=manifest_url or None,
        project_url=project_url or None,
        path="",
        raw_manifest={"id": target_id, "version": current_version or "0.0.0", "compatibility": {}},
    )


def _recommendation_from_release(
    module: ModuleRecord,
    target_version: str,
    release: Any,
    checked_releases: int,
    source_url_fallback: str,
) -> Recommendation | None:
    release_version = str(getattr(release, "version", "") or "").strip() or str(target_version or "").strip()
    download_url = str(getattr(release, "download_url", "") or "").strip()
    manifest_url = str(getattr(release, "manifest_url", "") or "").strip()
    if not download_url:
        fallback = str(source_url_fallback or "").strip()
        if fallback.lower().endswith(".zip"):
            download_url = fallback
    if not download_url:
        return None
    compat = getattr(release, "compatibility", {}) or {}
    sys_compat = getattr(release, "system_compatibility", {}) or {}
    return Recommendation(
        module=module.module_id,
        installed_version=module.version or "",
        recommended_version=release_version,
        reason="Applied from override plan.",
        confidence="manual",
        verified_version=str((compat or {}).get("verified") or "") or None,
        manifest_url=manifest_url or module.manifest_url,
        download_url=download_url,
        source=f"override-plan-{str(getattr(release, 'source', '') or 'release-catalog')}",
        checked_releases=max(int(checked_releases or 0), 0),
        compatibility=compat if isinstance(compat, dict) else {},
        system_compatibility=sys_compat if isinstance(sys_compat, dict) else {},
    )


def _build_override_plan_targets(
    plan_payload: dict[str, Any],
    profile: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    module_targets, system_targets = _extract_plan_targets(plan_payload, profile)
    warnings: list[str] = []
    snapshot = plan_payload.get("snapshot") if isinstance(plan_payload.get("snapshot"), dict) else {}
    inline_snapshot = snapshot.get("snapshotData") or snapshot.get("data")
    snapshot_payload: dict[str, Any] = {}
    if isinstance(inline_snapshot, dict):
        snapshot_payload = inline_snapshot
    snapshot_path = str(snapshot.get("path") or "").strip()
    if not snapshot_payload and snapshot_path:
        candidate_snapshot = Path(snapshot_path).expanduser()
        if candidate_snapshot.exists() and candidate_snapshot.is_file():
            try:
                snapshot_payload = json.loads(candidate_snapshot.read_text(encoding="utf-8"))
            except Exception as exc:
                warnings.append(f"snapshot_parse_failed:{exc}")
                snapshot_payload = {}
        else:
            warnings.append(f"snapshot_path_not_found:{snapshot_path}")
    if isinstance(snapshot_payload, dict):
        for module in snapshot_payload.get("modules") or []:
            if not isinstance(module, dict):
                continue
            module_id = str(module.get("module") or "").strip()
            version = str(module.get("version") or "").strip()
            if not module_id or not _is_concrete_version(version):
                continue
            manifest_url = str(module.get("manifestUrl") or "").strip()
            project_url = str(module.get("projectUrl") or "").strip()
            source_url = manifest_url or project_url
            module_targets[module_id] = {
                "moduleId": module_id,
                "targetVersion": version,
                "sourceUrl": source_url,
                "title": str(module.get("title") or module_id),
                "fromSnapshot": True,
            }
        systems_from_snapshot = snapshot_payload.get("systems")
        if isinstance(systems_from_snapshot, dict):
            for system_id, version in systems_from_snapshot.items():
                clean_id = str(system_id or "").strip()
                clean_version = str(version or "").strip()
                if not clean_id or not _is_concrete_version(clean_version):
                    continue
                existing = system_targets.get(clean_id) or {}
                system_targets[clean_id] = {
                    "systemId": clean_id,
                    "targetVersion": clean_version,
                    "sourceUrl": str(existing.get("sourceUrl") or "").strip(),
                    "title": str(existing.get("title") or clean_id),
                    "fromSnapshot": True,
                }
    return module_targets, system_targets, warnings


def _suggest_best_release_for_module(
    module: ModuleRecord,
    target_foundry_version: str,
    data_root: str,
    installed_system_versions: dict[str, str],
    cache_dir: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    modules_dir = str(modules_dir_from_data_root(data_root))
    installed_modules = load_modules(modules_dir)
    installed_modules_by_id = {item.module_id: item for item in installed_modules}
    resolution_cache: dict[str, Any] = {}
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]] = {}
    return _suggest_best_release_for_module_with_caches(
        module=module,
        target_foundry_version=target_foundry_version,
        installed_system_versions=installed_system_versions,
        cache_dir=cache_dir,
        installed_modules_by_id=installed_modules_by_id,
        resolution_cache=resolution_cache,
        history_cache=history_cache,
        force_refresh=force_refresh,
    )


def _suggest_best_release_for_module_with_caches(
    module: ModuleRecord,
    target_foundry_version: str,
    installed_system_versions: dict[str, str],
    cache_dir: str,
    installed_modules_by_id: dict[str, ModuleRecord],
    resolution_cache: dict[str, Any],
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]],
    force_refresh: bool = False,
) -> dict[str, Any]:

    def _fetch_history_cached(candidate: ModuleRecord, release_limit: int):
        key = (str(candidate.module_id or ""), int(release_limit), 1 if force_refresh else 0)
        cached = history_cache.get(key)
        if cached is not None:
            return cached
        result = fetch_release_history(
            candidate,
            per_page=release_limit,
            cache_dir=cache_dir,
            force_refresh=force_refresh,
        )
        history_cache[key] = result
        return result

    def _load_module_for_relationship(relationship):
        installed = installed_modules_by_id.get(str(relationship.module_id or ""))
        if installed is not None:
            return installed
        return None

    recommendation, warning_map = resolve_module_recommendation(
        module=module,
        target_version=target_foundry_version,
        installed_system_versions=installed_system_versions,
        fetch_history=_fetch_history_cached,
        load_module_for_relationship=_load_module_for_relationship,
        resolution_cache=resolution_cache,
    )
    releases, warnings = _fetch_history_cached(module, 50)
    module_warnings = warning_map.get(module.module_id) or []
    combined_warnings = list(dict.fromkeys([*warnings, *module_warnings]))

    if not releases:
        return {"module": module.module_id, "reason": "No release available.", "checkedReleases": 0, "warnings": combined_warnings}

    compatible = any(
        satisfies_release_constraints(item, target_foundry_version, installed_system_versions)
        for item in releases
    )
    return {
        "module": recommendation.module,
        "title": module.title,
        "installedVersion": recommendation.installed_version,
        "recommendedVersion": recommendation.recommended_version,
        "releaseUrl": _preferred_update_url(
            recommendation.download_url,
            getattr(recommendation, "project_url", ""),
            recommendation.manifest_url,
        ),
        "manifestUrl": recommendation.manifest_url,
        "downloadUrl": recommendation.download_url,
        "compatibility": recommendation.compatibility,
        "source": recommendation.source,
        "reason": recommendation.reason,
        "confidence": recommendation.confidence,
        "checkedReleases": recommendation.checked_releases,
        "isCompatible": compatible,
        "dependencyActions": [
            {
                "module": action.module,
                "installedVersion": action.installed_version,
                "recommendedVersion": action.recommended_version,
                "reason": action.reason,
                "releaseUrl": _preferred_update_url(
                    action.download_url,
                    getattr(action, "project_url", ""),
                    action.manifest_url,
                ),
                "manifestUrl": action.manifest_url,
                "compatibility": action.compatibility,
                "systemCompatibility": action.system_compatibility,
                "downloadUrl": action.download_url,
            }
            for action in recommendation.dependency_actions
        ],
        "warnings": combined_warnings,
    }


def export_latest_debug_log(runtime: AppRuntime, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
    log_path = runtime.config.reports_dir / "module-resolver-latest.log"
    if not log_path.exists():
        raise FileNotFoundError("latest_log_not_found")
    raw = log_path.read_bytes()
    truncated = False
    if max_bytes > 0 and len(raw) > max_bytes:
        raw = raw[-max_bytes:]
        truncated = True
    text = raw.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "fileName": f"modulator-debug-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log",
        "content": text,
        "truncated": truncated,
        "sizeBytes": len(raw),
        "sourcePath": str(log_path),
        "generatedAt": _utc_now_iso(),
    }


def _suggestion_error_payload(error: Exception, manifest_url: str = "", project_url: str = "", module_id: str = "") -> dict[str, Any]:
    raw = str(error or "").strip()
    text = raw.lower()
    provider = "git"
    url = f"{manifest_url or project_url}".lower()
    if "gitlab" in url or "gitlab" in text:
        provider = "gitlab"
    elif "github" in url or "github" in text:
        provider = "github"
    if "404" in text or "not found" in text:
        return {
            "errorCode": "provider_not_found",
            "message": f"{provider.capitalize()} returned 404 for this module source.",
            "hint": "Check project/manifest URL or select a valid release/tag.",
            "retryable": False,
            "raw": raw,
            "moduleId": module_id,
        }
    if "403" in text or "rate limit" in text or "too many requests" in text:
        return {
            "errorCode": "provider_rate_limited",
            "message": f"{provider.capitalize()} rate limit reached while loading versions.",
            "hint": "Retry in a few minutes or configure authenticated access/token.",
            "retryable": True,
            "raw": raw,
            "moduleId": module_id,
        }
    if "timed out" in text or "timeout" in text or "temporarily unavailable" in text:
        return {
            "errorCode": "provider_timeout",
            "message": f"{provider.capitalize()} timed out while loading versions.",
            "hint": "Retry. If it persists, verify network/proxy and source URL.",
            "retryable": True,
            "raw": raw,
            "moduleId": module_id,
        }
    if "json" in text and ("expecting value" in text or "decode" in text or "parse" in text):
        return {
            "errorCode": "provider_malformed_response",
            "message": f"{provider.capitalize()} returned an unexpected response format.",
            "hint": "Check if URL points to a valid module release/manifest endpoint.",
            "retryable": False,
            "raw": raw,
            "moduleId": module_id,
        }
    if "403" in text or "forbidden" in text or "unauthorized" in text:
        return {
            "errorCode": "provider_forbidden",
            "message": f"Access denied by {provider.capitalize()} while loading versions.",
            "hint": "Verify repository visibility and credentials/token.",
            "retryable": False,
            "raw": raw,
            "moduleId": module_id,
        }
    return {
        "errorCode": "provider_error",
        "message": "Could not refresh module versions from provider.",
        "hint": "Retry or review module source URL.",
        "retryable": True,
        "raw": raw,
        "moduleId": module_id,
    }


def _audit_provider_error(runtime: AppRuntime, payload: dict[str, Any], manifest_url: str = "", project_url: str = "") -> None:
    try:
        _append_audit(
            runtime.config,
            "suggestion_provider_error",
            {
                "moduleId": str(payload.get("moduleId") or ""),
                "errorCode": str(payload.get("errorCode") or "provider_error"),
                "retryable": bool(payload.get("retryable")),
                "provider": (
                    "gitlab"
                    if "gitlab" in f"{manifest_url or project_url}".lower()
                    else ("github" if "github" in f"{manifest_url or project_url}".lower() else "git")
                ),
                "hasManifestUrl": bool(str(manifest_url or "").strip()),
                "hasProjectUrl": bool(str(project_url or "").strip()),
            },
        )
    except Exception:
        pass


def _suggestion_retry_attempts(force_refresh: bool) -> int:
    return 3 if force_refresh else 1


def _retryable_provider_payload(payload: dict[str, Any]) -> bool:
    if bool(payload.get("retryable")):
        return True
    code = str(payload.get("errorCode") or "").strip().lower()
    return code in {"provider_timeout", "provider_rate_limited", "provider_error"}


def suggest_module(
    runtime: AppRuntime,
    module_id: str,
    manifest_url: str,
    project_url: str,
    force_refresh: bool = False,
    target_foundry_version: str = "",
    installed_system_versions_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    clean_module_id = str(module_id or "").strip()
    clean_manifest, clean_project = _normalize_source_urls(
        manifest_url=str(manifest_url or "").strip(),
        project_url=str(project_url or "").strip(),
    )
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
    detected_foundry_version, source = detect_foundry_version(normalized_root)
    requested_foundry_version = str(target_foundry_version or "").strip()
    foundry_version = requested_foundry_version or detected_foundry_version
    base_system_versions = load_system_versions(normalized_root)
    clean_override: dict[str, str] = {}
    if isinstance(installed_system_versions_override, dict):
        for key, value in installed_system_versions_override.items():
            system_id = str(key or "").strip()
            version = str(value or "").strip()
            if system_id and version:
                clean_override[system_id] = version
    installed_system_versions = {**base_system_versions, **clean_override}
    if force_refresh:
        _invalidate_report_suggest_cache_for_modules([clean_module_id])
        _invalidate_planning_context_rows(runtime, [clean_module_id])
    attempts = _suggestion_retry_attempts(force_refresh)
    last_exc: Exception | None = None
    suggestion: dict[str, Any] | None = None
    for index in range(attempts):
        try:
            suggestion = _suggest_best_release_for_module(
                module=_build_candidate_module(clean_module_id, manifest_url=clean_manifest, project_url=clean_project),
                target_foundry_version=foundry_version,
                data_root=normalized_root,
                installed_system_versions=installed_system_versions,
                cache_dir=runtime.config.cache_dir,
                force_refresh=force_refresh,
            )
            break
        except Exception as exc:
            last_exc = exc
            friendly = _suggestion_error_payload(exc, clean_manifest, clean_project, clean_module_id)
            if (index + 1) < attempts and _retryable_provider_payload(friendly):
                time.sleep(0.2 * (index + 1))
                continue
            _audit_provider_error(runtime, friendly, clean_manifest, clean_project)
            raise SuggestionProviderError(friendly) from exc
    if suggestion is None and last_exc is not None:
        friendly = _suggestion_error_payload(last_exc, clean_manifest, clean_project, clean_module_id)
        _audit_provider_error(runtime, friendly, clean_manifest, clean_project)
        raise SuggestionProviderError(friendly) from last_exc
    if suggestion is None:
        raise SuggestionProviderError(
            {
                "errorCode": "provider_error",
                "message": "Could not refresh module versions from provider.",
                "hint": "Retry or review module source URL.",
                "retryable": True,
                "raw": "",
                "moduleId": clean_module_id,
            }
        )
    return {
        "ok": True,
        "moduleId": clean_module_id,
        "foundryVersion": foundry_version,
        "foundryVersionSource": source,
        "dataRoot": normalized_root,
        "suggestion": suggestion,
        "context": {
            "requestedFoundryVersion": requested_foundry_version or None,
            "detectedFoundryVersion": detected_foundry_version,
            "installedSystemVersions": installed_system_versions,
        },
    }


def suggest_modules_batch(
    runtime: AppRuntime,
    modules: list[dict[str, Any]],
    force_refresh: bool = False,
    target_foundry_version: str = "",
    installed_system_versions_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    detected_foundry_version, source = detect_foundry_version(normalized_root)
    requested_foundry_version = str(target_foundry_version or "").strip()
    foundry_version = requested_foundry_version or detected_foundry_version
    base_system_versions = load_system_versions(normalized_root)
    clean_override: dict[str, str] = {}
    if isinstance(installed_system_versions_override, dict):
        for key, value in installed_system_versions_override.items():
            system_id = str(key or "").strip()
            version = str(value or "").strip()
            if system_id and version:
                clean_override[system_id] = version
    installed_system_versions = {**base_system_versions, **clean_override}
    if force_refresh:
        clean_ids = [str((item or {}).get("moduleId") or "").strip() for item in modules if isinstance(item, dict)]
        _invalidate_report_suggest_cache_for_modules(clean_ids)
        _invalidate_planning_context_rows(runtime, clean_ids)

    modules_dir = str(modules_dir_from_data_root(normalized_root))
    installed_modules = load_modules(modules_dir)
    installed_modules_by_id = {item.module_id: item for item in installed_modules}
    resolution_cache: dict[str, Any] = {}
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]] = {}

    rows: list[dict[str, Any]] = []
    for item in modules:
        module_id = str((item or {}).get("moduleId") or "").strip()
        manifest_url = str((item or {}).get("manifestUrl") or "").strip()
        project_url = str((item or {}).get("projectUrl") or "").strip()
        clean_manifest, clean_project = _normalize_source_urls(manifest_url=manifest_url, project_url=project_url)
        if not clean_manifest and not clean_project:
            rows.append({"moduleId": module_id, "error": "manifest_or_project_required"})
            continue
        if not module_id and clean_manifest:
            module_id = "manifest-derived"
        if not module_id:
            rows.append({"moduleId": "", "error": "module_id_required"})
            continue
        attempts = _suggestion_retry_attempts(force_refresh)
        suggestion: dict[str, Any] | None = None
        last_friendly: dict[str, Any] | None = None
        for index in range(attempts):
            try:
                suggestion = _suggest_best_release_for_module_with_caches(
                    module=_build_candidate_module(module_id, manifest_url=clean_manifest, project_url=clean_project),
                    target_foundry_version=foundry_version,
                    installed_system_versions=installed_system_versions,
                    cache_dir=runtime.config.cache_dir,
                    installed_modules_by_id=installed_modules_by_id,
                    resolution_cache=resolution_cache,
                    history_cache=history_cache,
                    force_refresh=force_refresh,
                )
                break
            except Exception as exc:
                last_friendly = _suggestion_error_payload(exc, clean_manifest, clean_project, module_id)
                if (index + 1) < attempts and _retryable_provider_payload(last_friendly):
                    time.sleep(0.2 * (index + 1))
                    continue
                break
        if suggestion is not None:
            rows.append({"moduleId": module_id, "suggestion": suggestion})
        else:
            friendly = last_friendly or {
                "errorCode": "provider_error",
                "message": "Could not refresh module versions from provider.",
                "hint": "Retry or review module source URL.",
                "retryable": True,
                "raw": "",
            }
            _audit_provider_error(runtime, friendly, clean_manifest, clean_project)
            rows.append({
                "moduleId": module_id,
                "error": str(friendly.get("message") or "suggestion_failed"),
                "errorCode": friendly.get("errorCode"),
                "hint": friendly.get("hint"),
                "retryable": bool(friendly.get("retryable")),
                "rawError": friendly.get("raw"),
            })

    return {
        "ok": True,
        "count": len(rows),
        "rows": rows,
        "foundryVersion": foundry_version,
        "foundryVersionSource": source,
        "dataRoot": normalized_root,
        "context": {
            "requestedFoundryVersion": requested_foundry_version or None,
            "detectedFoundryVersion": detected_foundry_version,
            "installedSystemVersions": installed_system_versions,
        },
    }


def apply_override_from_plan(
    runtime: AppRuntime,
    plan_path: str = "",
    plan_content: str = "",
    profile: str = "current",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    clean_content = str(plan_content or "").strip()
    clean_path = str(plan_path or "").strip()
    path: Path | None = None
    source_label = "<uploaded-plan>"
    if clean_content:
        try:
            payload = json.loads(clean_content)
        except Exception as exc:
            raise ValueError(f"plan_parse_failed:{exc}") from exc
    else:
        if not clean_path:
            raise ValueError("plan_path_required")
        path = Path(clean_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError("plan_path_not_found")
        source_label = str(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"plan_parse_failed:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("plan_invalid_payload")

    selected_profile = _coerce_profile(profile)
    module_targets, system_targets, parsing_warnings = _build_override_plan_targets(payload, selected_profile)
    if not module_targets and not system_targets:
        raise ValueError("plan_no_targets")
    total_items = len(system_targets) + len(module_targets)
    processed_items = 0
    if progress_callback is not None:
        progress_callback(
            {
                "phase": "counting",
                "totalItems": total_items,
                "processedItems": 0,
                "currentItemKind": "",
                "currentItemId": "",
            }
        )

    data_root = runtime.config_store.get_data_root() or runtime.config.data_root
    ok, normalized_root, details = _validate_foundry_root_path(data_root)
    if not ok:
        raise ValueError(details.get("message") or "Invalid Foundry root.")
    modules_dir = modules_dir_from_data_root(normalized_root)
    systems_dir = Path(normalized_root) / "Data" / "systems"

    installed_modules = load_modules(str(modules_dir))
    installed_modules_by_id = {item.module_id: item for item in installed_modules}
    installed_systems = load_system_records(normalized_root)
    installed_systems_by_id = {item.module_id: item for item in installed_systems}
    installed_system_versions = load_system_versions(normalized_root)
    target_foundry_version, _target_foundry_source = detect_foundry_version(normalized_root)
    resolution_cache: dict[str, Any] = {}
    history_cache: dict[tuple[str, int], tuple[list[Any], list[str]]] = {}
    module_sources = runtime.module_source_store.list_sources()

    results: dict[str, list[dict[str, Any]]] = {"systems": [], "modules": []}
    failures: list[dict[str, Any]] = []
    applied_count = 0
    skipped_count = 0

    for system_id in sorted(system_targets.keys()):
        target = system_targets.get(system_id) or {}
        target_version = str(target.get("targetVersion") or "").strip()
        source_url = str(target.get("sourceUrl") or "").strip()
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "resolving",
                    "totalItems": total_items,
                    "processedItems": processed_items,
                    "currentItemKind": "system",
                    "currentItemId": system_id,
                }
            )
        if not _is_concrete_version(target_version):
            skipped_count += 1
            results["systems"].append({"systemId": system_id, "status": "skipped", "reason": "missing_target_version"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "system", "currentItemId": system_id})
            continue

        installed = installed_systems_by_id.get(system_id)
        installed_version = str(installed.version if installed else "").strip()
        if installed and _version_tokens_match(installed_version, target_version):
            skipped_count += 1
            results["systems"].append({"systemId": system_id, "status": "already", "installedVersion": installed_version, "targetVersion": target_version})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "system", "currentItemId": system_id})
            continue
        if not installed and not source_url:
            failures.append({"kind": "system", "id": system_id, "targetVersion": target_version, "reason": "source_url_missing"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "system", "currentItemId": system_id})
            continue

        candidate = installed or _candidate_from_target(system_id, str(target.get("title") or system_id), installed_version or "0.0.0", source_url)
        if source_url and not (candidate.manifest_url or candidate.project_url):
            candidate = _candidate_from_target(system_id, str(target.get("title") or system_id), candidate.version or "0.0.0", source_url)

        chosen_release = None
        release_count = 0
        releases: list[Any] = []
        if candidate.project_url or candidate.manifest_url:
            try:
                releases, _warnings = fetch_system_release_history(candidate, per_page=100, cache_dir=runtime.config.cache_dir)
                release_count = len(releases)
                for release in releases:
                    if _version_tokens_match(str(getattr(release, "version", "") or ""), target_version):
                        chosen_release = release
                        break
            except Exception:
                chosen_release = None
        recommendation = _recommendation_from_release(candidate, target_version, chosen_release, release_count, source_url) if chosen_release is not None else None
        if recommendation is None and source_url.lower().endswith(".zip"):
            recommendation = Recommendation(
                module=system_id,
                installed_version=installed_version or "",
                recommended_version=target_version,
                reason="Applied from override plan direct ZIP URL.",
                confidence="manual",
                verified_version=None,
                manifest_url=candidate.manifest_url,
                download_url=source_url,
                source="override-plan-direct-zip",
                checked_releases=0,
            )
        if recommendation is None and release_count > 0:
            try:
                compatible_releases = [
                    release
                    for release in releases
                    if satisfies_release_constraints(release, target_foundry_version, installed_system_versions)
                ]
                if compatible_releases:
                    chosen_release = max(
                        compatible_releases,
                        key=lambda release: candidate_sort_key(release, target_foundry_version, installed_system_versions),
                    )
                    recommendation = _recommendation_from_release(
                        candidate,
                        str(getattr(chosen_release, "version", "") or target_version),
                        chosen_release,
                        release_count,
                        source_url,
                    )
                    if recommendation is not None:
                        recommendation.reason = "Applied from override plan fallback using best compatible system release."
                        recommendation.source = "override-plan-fallback-system-release"
            except Exception:
                recommendation = recommendation
        if recommendation is None:
            failures.append({"kind": "system", "id": system_id, "targetVersion": target_version, "reason": "recommendation_not_resolved"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "system", "currentItemId": system_id})
            continue
        try:
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "applying",
                        "totalItems": total_items,
                        "processedItems": processed_items,
                        "currentItemKind": "system",
                        "currentItemId": system_id,
                    }
                )
            backup_path = apply_system_recommendation(candidate, recommendation, str(systems_dir), runtime.config.cache_dir)
            applied_count += 1
            results["systems"].append({
                "systemId": system_id,
                "status": "applied",
                "fromVersion": installed_version or "-",
                "requestedVersion": target_version,
                "toVersion": recommendation.recommended_version,
                "backupPath": backup_path,
            })
        except Exception as exc:
            failures.append({"kind": "system", "id": system_id, "targetVersion": target_version, "reason": str(exc)})
        processed_items += 1
        if progress_callback is not None:
            progress_callback({"phase": "applying", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "system", "currentItemId": system_id})

    for module_id in sorted(module_targets.keys()):
        target = module_targets.get(module_id) or {}
        target_version = str(target.get("targetVersion") or "").strip()
        source_url = str(target.get("sourceUrl") or "").strip()
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "resolving",
                    "totalItems": total_items,
                    "processedItems": processed_items,
                    "currentItemKind": "module",
                    "currentItemId": module_id,
                }
            )
        if not _is_concrete_version(target_version):
            skipped_count += 1
            results["modules"].append({"moduleId": module_id, "status": "skipped", "reason": "missing_target_version"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "module", "currentItemId": module_id})
            continue

        installed = installed_modules_by_id.get(module_id)
        installed_version = str(installed.version if installed else "").strip()
        if installed and _version_tokens_match(installed_version, target_version):
            skipped_count += 1
            results["modules"].append({"moduleId": module_id, "status": "already", "installedVersion": installed_version, "targetVersion": target_version})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "module", "currentItemId": module_id})
            continue

        if not source_url:
            source = _source_for_module_id(module_sources, module_id)
            source_url = str((source or {}).get("manifestUrl") or (source or {}).get("projectUrl") or "").strip()
        if not installed and not source_url:
            failures.append({"kind": "module", "id": module_id, "targetVersion": target_version, "reason": "source_url_missing"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "module", "currentItemId": module_id})
            continue
        candidate = installed or _candidate_from_target(module_id, str(target.get("title") or module_id), installed_version or "0.0.0", source_url)
        if source_url and not (candidate.manifest_url or candidate.project_url):
            candidate = _candidate_from_target(module_id, str(target.get("title") or module_id), candidate.version or "0.0.0", source_url)

        chosen_release = None
        release_count = 0
        if candidate.project_url or candidate.manifest_url:
            try:
                releases, _warnings = fetch_release_history(candidate, per_page=100, cache_dir=runtime.config.cache_dir)
                release_count = len(releases)
                for release in releases:
                    if _version_tokens_match(str(getattr(release, "version", "") or ""), target_version):
                        chosen_release = release
                        break
            except Exception:
                chosen_release = None
        recommendation = _recommendation_from_release(candidate, target_version, chosen_release, release_count, source_url) if chosen_release is not None else None
        if recommendation is None and source_url.lower().endswith(".zip"):
            recommendation = Recommendation(
                module=module_id,
                installed_version=installed_version or "",
                recommended_version=target_version,
                reason="Applied from override plan direct ZIP URL.",
                confidence="manual",
                verified_version=None,
                manifest_url=candidate.manifest_url,
                download_url=source_url,
                source="override-plan-direct-zip",
                checked_releases=0,
            )
        if recommendation is None:
            try:
                fallback_suggestion = _suggest_best_release_for_module_with_caches(
                    module=candidate,
                    target_foundry_version=target_foundry_version,
                    installed_system_versions=installed_system_versions,
                    cache_dir=runtime.config.cache_dir,
                    installed_modules_by_id=installed_modules_by_id,
                    resolution_cache=resolution_cache,
                    history_cache=history_cache,
                )
                fallback_version = str(fallback_suggestion.get("recommendedVersion") or "").strip()
                fallback_download = str(fallback_suggestion.get("downloadUrl") or "").strip()
                fallback_manifest = str(fallback_suggestion.get("manifestUrl") or candidate.manifest_url or "").strip()
                fallback_compat = fallback_suggestion.get("compatibility") if isinstance(fallback_suggestion.get("compatibility"), dict) else {}
                fallback_sys_compat = fallback_suggestion.get("systemCompatibility") if isinstance(fallback_suggestion.get("systemCompatibility"), dict) else {}
                if _is_concrete_version(fallback_version) and fallback_download:
                    recommendation = Recommendation(
                        module=module_id,
                        installed_version=installed_version or "",
                        recommended_version=fallback_version,
                        reason="Applied from override plan fallback using best compatible module release.",
                        confidence=str(fallback_suggestion.get("confidence") or "manual"),
                        verified_version=str((fallback_compat or {}).get("verified") or "") or None,
                        manifest_url=fallback_manifest or None,
                        download_url=fallback_download,
                        source="override-plan-fallback-module-release",
                        checked_releases=max(int(fallback_suggestion.get("checkedReleases") or 0), 0),
                        compatibility=fallback_compat,
                        system_compatibility=fallback_sys_compat,
                    )
            except Exception:
                recommendation = recommendation
        if recommendation is None:
            failures.append({"kind": "module", "id": module_id, "targetVersion": target_version, "reason": "recommendation_not_resolved"})
            processed_items += 1
            if progress_callback is not None:
                progress_callback({"phase": "resolving", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "module", "currentItemId": module_id})
            continue
        try:
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "applying",
                        "totalItems": total_items,
                        "processedItems": processed_items,
                        "currentItemKind": "module",
                        "currentItemId": module_id,
                    }
                )
            backup_path = apply_recommendation(candidate, recommendation, str(modules_dir), runtime.config.cache_dir)
            applied_count += 1
            results["modules"].append({
                "moduleId": module_id,
                "status": "applied",
                "fromVersion": installed_version or "-",
                "requestedVersion": target_version,
                "toVersion": recommendation.recommended_version,
                "backupPath": backup_path,
            })
        except Exception as exc:
            failures.append({"kind": "module", "id": module_id, "targetVersion": target_version, "reason": str(exc)})
        processed_items += 1
        if progress_callback is not None:
            progress_callback({"phase": "applying", "totalItems": total_items, "processedItems": processed_items, "currentItemKind": "module", "currentItemId": module_id})

    if progress_callback is not None:
        progress_callback(
            {
                "phase": "finalizing",
                "totalItems": total_items,
                "processedItems": processed_items,
                "currentItemKind": "",
                "currentItemId": "",
            }
        )

    return {
        "ok": len(failures) == 0,
        "action": "override-from-plan",
        "profile": selected_profile,
        "planPath": source_label,
        "targets": {
            "systems": len(system_targets),
            "modules": len(module_targets),
        },
        "appliedCount": applied_count,
        "skippedCount": skipped_count,
        "failureCount": len(failures),
        "failures": failures,
        "warnings": parsing_warnings,
        "results": results,
        "progressSummary": {
            "totalItems": total_items,
            "processedItems": processed_items,
            "phase": "finalizing",
        },
        "generatedAt": _utc_now_iso(),
    }


def save_module_source(runtime: AppRuntime, module_id: str, manifest_url: str, project_url: str) -> dict[str, Any]:
    clean_module_id = str(module_id or "").strip()
    clean_manifest, clean_project = _normalize_source_urls(
        manifest_url=str(manifest_url or "").strip(),
        project_url=str(project_url or "").strip(),
    )
    if not clean_module_id:
        raise ValueError("module_id_required")
    if not clean_manifest and not clean_project:
        raise ValueError("manifest_or_project_required")
    suggestion = _suggest_best_release_for_module(
        module=_build_candidate_module(clean_module_id, manifest_url=clean_manifest, project_url=clean_project),
        target_foundry_version=detect_foundry_version(runtime.config_store.get_data_root() or runtime.config.data_root)[0],
        data_root=(runtime.config_store.get_data_root() or runtime.config.data_root),
        installed_system_versions=load_system_versions(runtime.config_store.get_data_root() or runtime.config.data_root),
        cache_dir=runtime.config.cache_dir,
    )
    saved = runtime.module_source_store.upsert_source(module_id=clean_module_id, manifest_url=clean_manifest, project_url=clean_project)
    _invalidate_planning_context_rows(runtime, [clean_module_id])
    try:
        _enrich_latest_report_file(runtime)
    except Exception:
        pass
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
    clean_action = _canonical_action_name(action)
    has_plan_payload = False
    if isinstance(payload, dict):
        has_plan_payload = bool(
            str(payload.get("planContent") or "").strip() or str(payload.get("planPath") or "").strip()
        )
    if has_plan_payload and clean_action != "override-from-plan":
        # Be permissive for import actions coming from older or mismatched frontends.
        clean_action = "override-from-plan"
    if clean_action not in {"dry-run", "apply", "force-compat", "cleanup-backups", "rollback-batch", "override-from-plan"}:
        try:
            _append_audit(
                runtime.config,
                "action_rejected_unsupported",
                {
                    "requestedAction": str(action or ""),
                    "normalizedAction": clean_action,
                    "payloadKeys": sorted(list(payload.keys())) if isinstance(payload, dict) else [],
                },
            )
        except Exception:
            pass
        raise ValueError("unsupported_action")
    body = payload if isinstance(payload, dict) else {}
    if clean_action == "apply":
        _ = _normalize_modules(body.get("modules"))
    if clean_action == "override-from-plan":
        plan_path = str(body.get("planPath") or "").strip()
        plan_content = str(body.get("planContent") or "").strip()
        if not plan_path and not plan_content:
            raise ValueError("plan_path_required")
        _ = _coerce_profile(body.get("profile"))
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
