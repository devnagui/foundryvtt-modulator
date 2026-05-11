from __future__ import annotations

import argparse
import contextlib
import shutil
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from math import ceil
from pathlib import Path

from .apply import apply_recommendation, force_module_compatibility
from .db import DEFAULT_MAX_SCAN_RUNS, default_database_path, maintain_database, persist_scan_snapshot
from .db_queries import load_database_summary, load_package_hints
from .controllers.report_controller import attach_report_views, render_report_html
from .foundry import detect_foundry_version
from .future_upgrade import build_current_system_upgrade_view, build_future_upgrade_decision
from .local import (
    build_local_dependency_map,
    load_modules,
    load_system_records,
    load_system_versions,
    load_world_usage,
    modules_dir_from_data_root,
)
from .dependencies import resolve_module_recommendation
from .models import ModuleRecord, ModuleRelationship, Recommendation, ReleaseRecord
from .sources import (
    DEFAULT_CACHE_DIR,
    configure_cache_limits,
    describe_cache_status,
    enforce_cache_limits,
    fetch_release_history,
    fetch_system_release_history,
    list_future_foundry_releases,
)
from .storage import collect_backup_inventory, collect_module_disk_inventory, delete_module_backups, delete_modules, maintain_backups

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
TOOL_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_REPORTS_DIR = DEFAULT_REPORTS_DIR / "public"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recommend Foundry module versions for the installed Foundry core version.")
    parser.add_argument("--data-root", required=True, help="Foundry data root containing Data, Logs and Config directories.")
    parser.add_argument("--module", action="append", help="Optional module id filter. Repeat the flag to target multiple modules.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate the resolution process without changing installed modules.")
    parser.add_argument("--apply", action="store_true", help="Apply recommended module upgrades on disk.")
    parser.add_argument("--allow-downgrade", action="store_true", help="Allow applying a recommended version that is older than the installed module.")
    parser.add_argument("--expected-version", action="append", help="Pin the expected recommendation as module_id=version. Repeat the flag for multiple modules.")
    parser.add_argument("--log-file", help="Optional file path for execution logs.")
    parser.add_argument("--json-output", help="Optional path to write the unified JSON payload.")
    parser.add_argument("--html-report", help="Optional path to write a unified HTML report.")
    parser.add_argument("--batch-size", type=int, default=10, help="Batch size for processing modules. Minimum is 10.")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="Directory used for HTTP/download cache.")
    parser.add_argument("--database-path", help="Optional path to the local SQLite database used to persist the normalized graph/catalog.")
    parser.add_argument("--db-max-scan-runs", type=int, default=DEFAULT_MAX_SCAN_RUNS, help="Maximum number of recent scan snapshots to keep in SQLite.")
    parser.add_argument("--cache-max-mb", type=int, default=512, help="Maximum total cache size in MB.")
    parser.add_argument("--cache-max-files", type=int, default=5000, help="Maximum number of files kept in cache.")
    parser.add_argument("--cache-max-age-days", type=int, default=30, help="Maximum cache file age in days before pruning.")
    parser.add_argument("--backup-max-mb", type=int, default=4096, help="Maximum total backup size in MB before pruning old backups.")
    parser.add_argument("--backup-max-per-module", type=int, default=5, help="Maximum number of backups to keep per module.")
    parser.add_argument("--backup-max-age-days", type=int, default=0, help="Maximum backup age in days before pruning (0 disables age-based pruning).")
    parser.add_argument("--cleanup-backups", action="store_true", help="Delete module backups in Foundry Backups/modules using resolver safety guards.")
    parser.add_argument("--cleanup-backup-module", action="append", help="Module id to cleanup backups for. Repeat for multiple modules.")
    parser.add_argument("--cleanup-backup-all", action="store_true", help="Delete backups for all modules.")
    parser.add_argument("--delete-unused-modules", action="store_true", help="Delete unused modules from disk using resolver safety guards.")
    parser.add_argument("--delete-module", action="append", help="Module id to delete (module dir + backups). Repeat for multiple modules.")
    parser.add_argument("--delete-all-modules", action="store_true", help="Delete all module directories and module backups.")
    parser.add_argument(
        "--force-compat-module",
        action="append",
        help=(
            "Module id to force compatibility metadata in module.json "
            "(sets compatibility.maximum only). Repeat for multiple modules."
        ),
    )
    parser.add_argument(
        "--force-compat-version",
        help=(
            "Foundry version to apply to compatibility.maximum when using --force-compat-module. "
            "Defaults to detected current Foundry version."
        ),
    )
    parser.add_argument("--compose-file", default="/home/engrenado/config/docker-compose.yaml", help="docker compose file used to check/stop/start Foundry service.")
    parser.add_argument("--foundry-service-name", default="foundry", help="docker compose service name for Foundry.")
    parser.add_argument("--skip-foundry-service-control", action="store_true", help="Do not stop/start Foundry service around destructive operations.")
    parser.add_argument(
        "--disable-blocked-refresh",
        action="store_true",
        help="Disable targeted fresh lookup for modules that were blocked in the previous report.",
    )
    parser.add_argument(
        "--blocked-refresh-max",
        type=int,
        default=40,
        help="Maximum number of previously blocked modules to refresh without cache.",
    )
    parser.add_argument(
        "--blocked-refresh-source",
        help="Optional JSON report path used to discover blocked modules for targeted refresh.",
    )
    return parser


def configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def load_env_file(env_path: str) -> None:
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        return


def _normalize_data_root(raw_data_root: str) -> str:
    path = Path(str(raw_data_root or "")).expanduser().resolve()
    # Common user input mistake: passing "<foundry-root>/Data" instead of "<foundry-root>".
    if path.name.lower() == "data":
        parent = path.parent
        if (path / "modules").exists() and (parent / "Logs").exists():
            return str(parent)
    return str(path)


def _is_upgrade(installed_version: str | None, recommended_version: str | None) -> bool:
    if not installed_version or not recommended_version:
        return False
    from .versioning import compare_versions

    return compare_versions(recommended_version, installed_version) > 0


def _default_output_paths(apply_mode: bool) -> tuple[str, str, str]:
    DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if apply_mode:
        apply_dir = DEFAULT_REPORTS_DIR / "applied"
        apply_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base_name = f"module-resolver-apply-{timestamp}"
        return (
            str(apply_dir / f"{base_name}.log"),
            str(apply_dir / f"{base_name}.json"),
            str(apply_dir / f"{base_name}.html"),
        )
    return (
        str(DEFAULT_REPORTS_DIR / "module-resolver-latest.log"),
        str(DEFAULT_REPORTS_DIR / "module-resolver-latest.json"),
        str(DEFAULT_REPORTS_DIR / "module-resolver-latest.html"),
    )


def _parse_expected_versions(raw_values: list[str] | None) -> dict[str, str]:
    expected_versions: dict[str, str] = {}
    for raw_value in raw_values or []:
        if "=" not in raw_value:
            raise ValueError(f"Invalid --expected-version value '{raw_value}'. Use module_id=version.")
        module_id, version = raw_value.split("=", 1)
        module_id = module_id.strip()
        version = version.strip()
        if not module_id or not version:
            raise ValueError(f"Invalid --expected-version value '{raw_value}'. Use module_id=version.")
        expected_versions[module_id] = version
    return expected_versions


def _sync_public_reports(html_report: str | None, json_output: str | None) -> None:
    PUBLIC_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if html_report:
        html_path = Path(html_report)
        latest_html = DEFAULT_REPORTS_DIR / "module-resolver-latest.html"
        if html_path.resolve() == latest_html.resolve():
            shutil.copyfile(html_path, PUBLIC_REPORTS_DIR / "index.html")
            logging.info("Published HTML report to %s", PUBLIC_REPORTS_DIR / "index.html")
    if json_output:
        json_path = Path(json_output)
        latest_json = DEFAULT_REPORTS_DIR / "module-resolver-latest.json"
        if json_path.resolve() == latest_json.resolve():
            shutil.copyfile(json_path, PUBLIC_REPORTS_DIR / "module-resolver-latest.json")
            logging.info("Published JSON report to %s", PUBLIC_REPORTS_DIR / "module-resolver-latest.json")


def _run_post_apply_refresh(args: argparse.Namespace) -> bool:
    latest_log = str(DEFAULT_REPORTS_DIR / "module-resolver-latest.log")
    latest_json = str(DEFAULT_REPORTS_DIR / "module-resolver-latest.json")
    latest_html = str(DEFAULT_REPORTS_DIR / "module-resolver-latest.html")
    cmd = [
        sys.executable,
        "-m",
        "resolver.cli",
        "--data-root",
        args.data_root,
        "--dry-run",
        "--batch-size",
        str(args.batch_size),
        "--cache-dir",
        args.cache_dir,
        "--database-path",
        str(args.database_path) if args.database_path else str(default_database_path(str(TOOL_ROOT))),
        "--db-max-scan-runs",
        str(args.db_max_scan_runs),
        "--cache-max-mb",
        str(args.cache_max_mb),
        "--cache-max-files",
        str(args.cache_max_files),
        "--cache-max-age-days",
        str(args.cache_max_age_days),
        "--log-file",
        latest_log,
        "--json-output",
        latest_json,
        "--html-report",
        latest_html,
    ]
    env = os.environ.copy()
    env["RESOLVER_SKIP_POST_APPLY_REFRESH"] = "1"
    try:
        subprocess.run(
            cmd,
            cwd=str(TOOL_ROOT),
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logging.info("Post-apply full refresh completed and latest/public reports were updated.")
        return True
    except subprocess.CalledProcessError as exc:
        logging.error("Post-apply refresh failed with exit code %s", exc.returncode)
        return False


def _print_human_summary(payload: dict, dry_run: bool, apply_mode: bool, html_report: str | None, json_output: str | None) -> None:
    mode = "dry-run" if dry_run else ("apply" if apply_mode else "analysis")
    target = str(payload.get("targetVersion") or "-")
    module_count = int(payload.get("moduleCount") or 0)
    results = payload.get("results") or []
    upgrades = 0
    unchanged = 0
    for row in results:
        installed = str(row.get("installedVersion") or "")
        recommended = str(row.get("recommendedVersion") or "")
        if installed and recommended and _is_upgrade(installed, recommended):
            upgrades += 1
        else:
            unchanged += 1
    warning_count = sum(len(items) for items in (payload.get("warnings") or {}).values())
    print(
        f"Resolver completed ({mode}) | Foundry {target} | Modules analyzed: {module_count} | "
        f"Upgrades suggested: {upgrades} | No change: {unchanged} | Warnings: {warning_count}"
    )
    backup_inventory = payload.get("backupInventory") or {}
    backup_total = int(backup_inventory.get("totalBackupBytes") or 0)
    backup_count = int(backup_inventory.get("totalBackupCount") or 0)
    print(f"Backups: {_format_bytes(backup_total)} across {backup_count} backup entries")
    if html_report:
        print(f"HTML report: {html_report}")
    if json_output:
        print(f"JSON report: {json_output}")
    log_file = payload.get("logFile")
    if log_file:
        print(f"Log file: {log_file}")


def _print_cleanup_summary(
    cleanup_summary: dict,
    refreshed: bool,
    refresh_failed: bool,
    log_file: str | None,
    action_label: str = "Backup cleanup",
) -> None:
    removed_count = int(cleanup_summary.get("removedCount") or 0)
    removed_bytes = int(cleanup_summary.get("removedBytes") or 0)
    selected_modules = cleanup_summary.get("selectedModules") or []
    scope = "all modules" if cleanup_summary.get("deleteAll") else (", ".join(selected_modules) if selected_modules else "selected modules")
    print(f"{action_label} completed | Scope: {scope} | Removed entries: {removed_count} | Reclaimed: {_format_bytes(removed_bytes)}")
    if refreshed:
        print("Report refresh: completed (latest/public updated)")
    elif refresh_failed:
        print("Report refresh: failed (run a dry-run manually to refresh latest/public)")
    else:
        print("Report refresh: skipped")
    if log_file:
        print(f"Log file: {log_file}")


def _print_force_compat_summary(
    actions: list[dict],
    target_version: str,
    refreshed: bool,
    refresh_failed: bool,
    log_file: str | None,
) -> None:
    modules = [str(action.get("module") or "").strip() for action in actions if str(action.get("module") or "").strip()]
    module_label = ", ".join(modules) if modules else "-"
    backups = [str(action.get("backupPath") or "").strip() for action in actions if str(action.get("backupPath") or "").strip()]
    print(
        f"Forced compatibility completed | Target Foundry: {target_version} | "
        f"Modules updated: {len(modules)} | Modules: {module_label}"
    )
    print(f"Backups created: {len(backups)}")
    if refreshed:
        print("Report refresh: completed (latest/public updated)")
    elif refresh_failed:
        print("Report refresh: failed (run a dry-run manually to refresh latest/public)")
    else:
        print("Report refresh: skipped")
    if log_file:
        print(f"Log file: {log_file}")


def _compose_cmd(compose_file: str, *args: str) -> list[str]:
    return ["docker", "compose", "-f", compose_file, *args]


def _is_foundry_running(compose_file: str, service_name: str) -> tuple[bool, str]:
    cmd = _compose_cmd(compose_file, "ps", "--services", "--filter", "status=running")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"unavailable: {exc}"
    services = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return service_name in services, "ok"


@contextlib.contextmanager
def _foundry_maintenance_window(args: argparse.Namespace, required: bool):
    if not required or args.skip_foundry_service_control or os.environ.get("RESOLVER_SKIP_POST_APPLY_REFRESH") == "1":
        yield {"controlled": False, "wasRunning": False, "state": "skipped"}
        return
    was_running, state = _is_foundry_running(args.compose_file, args.foundry_service_name)
    if state != "ok":
        logging.warning("Could not determine Foundry service status (%s). Continuing without service control.", state)
        yield {"controlled": False, "wasRunning": False, "state": state}
        return
    if was_running:
        stop_cmd = _compose_cmd(args.compose_file, "stop", args.foundry_service_name)
        logging.info("Stopping Foundry service before destructive operation: %s", " ".join(stop_cmd))
        subprocess.run(stop_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    info = {"controlled": True, "wasRunning": was_running, "state": "ok"}
    try:
        yield info
    finally:
        if was_running:
            start_cmd = _compose_cmd(args.compose_file, "up", "-d", args.foundry_service_name)
            logging.info("Restarting Foundry service after destructive operation: %s", " ".join(start_cmd))
            subprocess.run(start_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _format_bytes(value: int) -> str:
    size = float(max(int(value), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0 or size >= 10:
        return f"{int(round(size))} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def _load_blocked_modules_for_refresh(report_path: str, limit: int) -> set[str]:
    if limit <= 0:
        return set()
    path = Path(report_path)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()

    counts: dict[str, int] = {}

    def _add(module_id: str) -> None:
        clean = str(module_id or "").strip()
        if not clean:
            return
        counts[clean] = counts.get(clean, 0) + 1

    v3 = ((payload.get("reportViews") or {}).get("v3")) or {}
    current = v3.get("currentSystemUpgrades") or {}
    for row in current.get("rows") or []:
        for item in row.get("blockedModuleRows") or []:
            _add(item.get("module"))
    planner = v3.get("systemUpgradePlanner") or {}
    for target in planner.get("targets") or []:
        for item in target.get("blockedModules") or []:
            _add(item.get("module"))

    if not counts:
        for item in payload.get("futureUpgradeMatrix") or []:
            for outcome in item.get("moduleOutcomes") or []:
                if str(outcome.get("status") or "").strip().lower() == "blocked":
                    _add(outcome.get("module"))

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {module_id for module_id, _ in ranked[:limit]}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.batch_size < 10:
        logging.basicConfig(level=logging.ERROR, force=True)
        print(json.dumps({"error": "--batch-size must be at least 10."}))
        return 1
    try:
        expected_versions = _parse_expected_versions(args.expected_version)
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR, force=True)
        print(json.dumps({"error": str(exc)}))
        return 1
    load_env_file("/home/engrenado/config/.env")
    configure_cache_limits(
        max_bytes=args.cache_max_mb * 1024 * 1024,
        max_files=args.cache_max_files,
        max_age_days=args.cache_max_age_days,
    )
    pruned = enforce_cache_limits(args.cache_dir)
    default_log_file, default_json_output, default_html_report = _default_output_paths(args.apply)
    resolved_log_file = args.log_file or default_log_file
    resolved_json_output = args.json_output or default_json_output
    resolved_html_report = args.html_report or default_html_report
    resolved_database_path = args.database_path or default_database_path(str(TOOL_ROOT))
    configure_logging(resolved_log_file)

    args.data_root = _normalize_data_root(args.data_root)
    logging.info("Starting module resolution for data root %s", args.data_root)
    if pruned["removedFiles"]:
        logging.info(
            "Pruned cache before execution: removed %s files reclaiming %s bytes",
            pruned["removedFiles"],
            pruned["removedBytes"],
        )
    if args.dry_run:
        logging.info("Dry-run mode enabled; no module files will be modified.")
    if args.apply:
        logging.info("Apply mode enabled; recommended upgrades will be written to disk.")
    if os.environ.get("GITHUB_TOKEN"):
        logging.info("GITHUB_TOKEN detected; authenticated GitHub requests are enabled.")

    cleanup_modules = [str(module_id).strip() for module_id in (args.cleanup_backup_module or []) if str(module_id).strip()]
    if args.cleanup_backups:
        if args.cleanup_backup_all and cleanup_modules:
            logging.error("Use --cleanup-backup-all or --cleanup-backup-module, not both.")
            print(json.dumps({"error": "Use --cleanup-backup-all or --cleanup-backup-module, not both."}))
            return 1
        if not args.cleanup_backup_all and not cleanup_modules:
            cleanup_modules = [str(module_id).strip() for module_id in (args.module or []) if str(module_id).strip()]
        if not args.cleanup_backup_all and not cleanup_modules:
            logging.error("Backup cleanup requires --cleanup-backup-all or at least one --cleanup-backup-module/--module.")
            print(
                json.dumps(
                    {
                        "error": (
                            "Backup cleanup requires --cleanup-backup-all or "
                            "at least one --cleanup-backup-module/--module."
                        )
                    }
                )
            )
            return 1
        modules_dir = modules_dir_from_data_root(args.data_root)
        with _foundry_maintenance_window(args, required=True):
            cleanup_summary = delete_module_backups(str(modules_dir), cleanup_modules, delete_all=args.cleanup_backup_all)
        logging.info(
            "Backup cleanup finished: removed %s directories reclaiming %s bytes",
            cleanup_summary.get("removedCount"),
            cleanup_summary.get("removedBytes"),
        )
        refreshed = False
        refresh_failed = False
        if os.environ.get("RESOLVER_SKIP_POST_APPLY_REFRESH") != "1":
            refreshed = _run_post_apply_refresh(args)
            refresh_failed = not refreshed
        _print_cleanup_summary(
            cleanup_summary,
            refreshed=refreshed,
            refresh_failed=refresh_failed,
            log_file=resolved_log_file,
            action_label="Backup cleanup",
        )
        return 0

    delete_modules_list = [str(module_id).strip() for module_id in (args.delete_module or []) if str(module_id).strip()]
    if args.delete_unused_modules:
        if args.delete_all_modules and delete_modules_list:
            logging.error("Use --delete-all-modules or --delete-module, not both.")
            print(json.dumps({"error": "Use --delete-all-modules or --delete-module, not both."}))
            return 1
        modules_dir = modules_dir_from_data_root(args.data_root)
        if not args.delete_all_modules and not delete_modules_list:
            all_modules = load_modules(str(modules_dir))
            used_modules = {
                str(module_id).strip()
                for world in (load_world_usage(args.data_root) or [])
                for module_id in (world.get("enabledModules") or [])
                if str(module_id).strip()
            }
            delete_modules_list = sorted(
                {
                    module.module_id
                    for module in all_modules
                    if module.module_id and module.module_id not in used_modules
                }
            )
        if not args.delete_all_modules and not delete_modules_list:
            logging.info("No unused modules selected for deletion.")
            print("Module deletion completed | Scope: unused modules | Removed entries: 0 | Reclaimed: 0 B")
            return 0
        with _foundry_maintenance_window(args, required=True):
            delete_summary = delete_modules(str(modules_dir), delete_modules_list, delete_all=args.delete_all_modules)
        logging.info(
            "Module deletion finished: removed %s directories reclaiming %s bytes",
            delete_summary.get("removedCount"),
            delete_summary.get("removedBytes"),
        )
        refreshed = False
        refresh_failed = False
        if os.environ.get("RESOLVER_SKIP_POST_APPLY_REFRESH") != "1":
            refreshed = _run_post_apply_refresh(args)
            refresh_failed = not refreshed
        _print_cleanup_summary(
            delete_summary,
            refreshed=refreshed,
            refresh_failed=refresh_failed,
            log_file=resolved_log_file,
            action_label="Module deletion",
        )
        return 0

    force_compat_modules = sorted(
        {str(module_id).strip() for module_id in (args.force_compat_module or []) if str(module_id).strip()}
    )
    if force_compat_modules:
        if args.apply:
            logging.error("Forced compatibility cannot be combined with --apply in the same execution.")
            print(json.dumps({"error": "Forced compatibility cannot be combined with --apply in the same execution."}))
            return 1
        modules_dir = modules_dir_from_data_root(args.data_root)
        all_modules = load_modules(str(modules_dir))
        modules_by_id = {module.module_id: module for module in all_modules}
        missing = [module_id for module_id in force_compat_modules if module_id not in modules_by_id]
        if missing:
            missing_label = ", ".join(missing)
            logging.error("Forced compatibility modules not found: %s", missing_label)
            print(json.dumps({"error": f"Forced compatibility modules not found: {missing_label}"}))
            return 1

        force_version = str(args.force_compat_version or "").strip()
        if not force_version:
            try:
                force_version, detection_source = detect_foundry_version(args.data_root)
            except ValueError as exc:
                logging.error("%s", exc)
                print(json.dumps({"error": str(exc)}))
                return 1
            logging.info(
                "No --force-compat-version provided; using detected Foundry version %s from %s",
                force_version,
                detection_source,
            )

        forced_actions: list[dict] = []
        with _foundry_maintenance_window(args, required=True):
            for module_id in force_compat_modules:
                action = force_module_compatibility(
                    module=modules_by_id[module_id],
                    modules_dir=str(modules_dir),
                    target_version=force_version,
                )
                forced_actions.append(action)
        logging.info(
            "Forced compatibility updated %s modules for Foundry %s",
            len(forced_actions),
            force_version,
        )
        refreshed = False
        refresh_failed = False
        if os.environ.get("RESOLVER_SKIP_POST_APPLY_REFRESH") != "1":
            refreshed = _run_post_apply_refresh(args)
            refresh_failed = not refreshed
        _print_force_compat_summary(
            forced_actions,
            target_version=force_version,
            refreshed=refreshed,
            refresh_failed=refresh_failed,
            log_file=resolved_log_file,
        )
        return 0

    try:
        target_version, detection_source = detect_foundry_version(args.data_root)
    except ValueError as exc:
        logging.error("%s", exc)
        print(json.dumps({"error": str(exc)}))
        return 1

    logging.info("Detected Foundry version %s using %s", target_version, detection_source)
    future_foundry_releases: list[dict[str, str]] = []
    future_foundry_warnings: list[str] = []
    try:
        future_foundry_releases = list_future_foundry_releases(
            target_version,
            cache_dir=args.cache_dir,
            force_refresh=True,
        )
        if future_foundry_releases:
            logging.info("Detected %s future Foundry releases after %s", len(future_foundry_releases), target_version)
    except Exception as exc:
        warning = f"Foundry release catalog lookup failed: {exc}"
        future_foundry_warnings.append(warning)
        logging.warning("%s", warning)
    modules_dir = modules_dir_from_data_root(args.data_root)
    installed_system_versions = load_system_versions(args.data_root)
    installed_system_records = load_system_records(args.data_root)
    world_usage = load_world_usage(args.data_root)
    logging.info("Scanning modules in %s", modules_dir)
    if installed_system_versions:
        logging.info("Detected installed systems: %s", ", ".join(f"{k}={v}" for k, v in sorted(installed_system_versions.items())))

    all_modules = load_modules(str(modules_dir))
    module_filter = set(args.module or [])
    modules = [module for module in all_modules if not module_filter or module.module_id in module_filter]
    if not modules:
        print(json.dumps({"error": "No modules found for the provided filters."}))
        return 1
    installed_modules_by_id = {module.module_id: module for module in all_modules}
    installed_systems_by_id = {system.module_id: system for system in installed_system_records}
    local_dependency_map = build_local_dependency_map(all_modules)
    blocked_refresh_source = (
        args.blocked_refresh_source
        or str(DEFAULT_REPORTS_DIR / "module-resolver-latest.json")
    )
    hot_blocked_modules: set[str] = set()
    if not args.disable_blocked_refresh:
        hot_blocked_modules = _load_blocked_modules_for_refresh(
            blocked_refresh_source,
            max(int(args.blocked_refresh_max), 0),
        )
        if hot_blocked_modules:
            logging.info(
                "Targeted blocked-module refresh enabled for %s modules (source: %s)",
                len(hot_blocked_modules),
                blocked_refresh_source,
            )
            modules = sorted(
                modules,
                key=lambda current: (
                    0 if str(current.module_id or "") in hot_blocked_modules else 1,
                    str(current.module_id or "").lower(),
                ),
            )
            logging.info("Prioritizing previously blocked modules first in this run.")

    history_cache = {}
    resolution_cache = {}
    blocked_refresh_max_limit = 5

    def fetch_history_cached(module: ModuleRecord, release_limit: int):
        module_id = str(module.module_id or "")
        force_refresh = module_id in hot_blocked_modules
        if force_refresh:
            full_cache_key = (module_id, blocked_refresh_max_limit, "fresh")
            full_cached = history_cache.get(full_cache_key)
            if full_cached is None:
                full_cached = fetch_release_history(
                    module,
                    per_page=blocked_refresh_max_limit,
                    cache_dir=args.cache_dir,
                    force_refresh=True,
                    newer_than_version=module.version,
                )
                logging.info(
                    "Fresh lookup for blocked module %s (max-limit=%s, newer-than=%s)",
                    module_id,
                    blocked_refresh_max_limit,
                    module.version,
                )
                history_cache[full_cache_key] = full_cached
            releases, warning_list = full_cached
            return releases[: max(int(release_limit), 0)], warning_list

        cache_key = (module_id, release_limit, "cache")
        cached = history_cache.get(cache_key)
        if cached is not None:
            return cached
        result = fetch_release_history(module, per_page=release_limit, cache_dir=args.cache_dir)
        history_cache[cache_key] = result
        return result

    system_history_cache = {}

    def fetch_system_history_cached(system: ModuleRecord, release_limit: int):
        cache_key = (system.module_id, release_limit)
        cached = system_history_cache.get(cache_key)
        if cached is not None:
            return cached
        result = fetch_system_release_history(system, per_page=release_limit, cache_dir=args.cache_dir)
        system_history_cache[cache_key] = result
        return result

    def load_module_for_relationship(relationship: ModuleRelationship) -> ModuleRecord | None:
        installed = installed_modules_by_id.get(relationship.module_id)
        if installed is not None:
            return installed
        if relationship.manifest_url:
            try:
                import json
                from urllib.request import Request, urlopen

                request = Request(relationship.manifest_url, headers={"User-Agent": "foundry-module-version-resolver/0.1"})
                with urlopen(request, timeout=15) as response:
                    manifest = json.load(response)
            except Exception as exc:
                logging.warning("Failed to load dependency manifest for %s: %s", relationship.module_id, exc)
                return None
            return ModuleRecord(
                module_id=str(manifest.get("id") or relationship.module_id),
                title=str(manifest.get("title") or relationship.module_id),
                version=str(manifest.get("version") or ""),
                manifest_url=manifest.get("manifest") or relationship.manifest_url,
                project_url=manifest.get("url"),
                path="",
                raw_manifest=manifest,
            )
        return None

    results = []
    warnings: dict[str, list[str]] = {}
    apply_actions = []
    applied_any = False
    batch_count = ceil(len(modules) / args.batch_size)
    with _foundry_maintenance_window(args, required=bool(args.apply)):
        for batch_index in range(batch_count):
            start = batch_index * args.batch_size
            end = start + args.batch_size
            batch = modules[start:end]
            logging.info(
                "Processing batch %s/%s with %s modules",
                batch_index + 1,
                batch_count,
                len(batch),
            )
            for module in batch:
                logging.info("Resolving module %s (%s)", module.module_id, module.version)
                recommendation, module_warnings = resolve_module_recommendation(
                    module,
                    target_version,
                    installed_system_versions,
                    fetch_history_cached,
                    load_module_for_relationship,
                    resolution_cache,
                )
                logging.info(
                    "Recommended %s for %s after checking %s releases",
                    recommendation.recommended_version,
                    recommendation.module,
                    recommendation.checked_releases,
                )
                expected_version = expected_versions.get(module.module_id)
                if expected_version and recommendation.recommended_version != expected_version:
                    message = (
                        f"Expected version mismatch for {module.module_id}: "
                        f"report pinned {expected_version}, resolver returned {recommendation.recommended_version}."
                    )
                    logging.error(message)
                    print(json.dumps({"error": message}))
                    return 1
                applied_backup = None
                if args.apply and recommendation.download_url and recommendation.recommended_version != recommendation.installed_version:
                    if _is_upgrade(recommendation.installed_version, recommendation.recommended_version) or args.allow_downgrade:
                        applied_backup = apply_recommendation(module, recommendation, str(modules_dir), args.cache_dir)
                        applied_any = True
                    else:
                        logging.info(
                            "Skipping apply for %s because recommended version %s is not an upgrade over %s and --allow-downgrade was not used",
                            recommendation.module,
                            recommendation.recommended_version,
                            recommendation.installed_version,
                        )
                if args.apply and recommendation.dependency_updates:
                    for dependency in recommendation.dependency_updates:
                        dependency_module = installed_modules_by_id.get(dependency.module)
                        if dependency_module is None:
                            continue
                        if not _is_upgrade(dependency.installed_version, dependency.recommended_version):
                            logging.info(
                                "Skipping dependency apply for %s because recommended version %s is not an upgrade over %s",
                                dependency.module,
                                dependency.recommended_version,
                                dependency.installed_version,
                            )
                            continue
                        dependency_recommendation = resolution_cache.get(dependency.module)
                        if dependency.download_url and dependency.recommended_version:
                            dependency_recommendation = Recommendation(
                                module=dependency.module,
                                installed_version=dependency.installed_version or dependency_module.version,
                                recommended_version=dependency.recommended_version,
                                reason=dependency.reason,
                                confidence="medium",
                                verified_version=(dependency.compatibility or {}).get("verified"),
                                manifest_url=dependency.manifest_url,
                                download_url=dependency.download_url,
                                source="dependency-update-override",
                                checked_releases=0,
                                compatibility=dependency.compatibility or {},
                                system_compatibility=dependency.system_compatibility or {},
                                dependency_actions=[],
                                dependency_updates=[],
                                missing_dependencies=[],
                                release_published_at=None,
                                attention_flag=False,
                            )
                        if dependency_recommendation and dependency_recommendation.download_url:
                            backup_path = apply_recommendation(
                                dependency_module,
                                dependency_recommendation,
                                str(modules_dir),
                                args.cache_dir,
                            )
                            applied_any = True
                            apply_actions.append({"module": dependency.module, "backupPath": backup_path})
                results.append(
                {
                    "module": recommendation.module,
                    "title": module.title,
                    "installedVersion": recommendation.installed_version,
                    "recommendedVersion": recommendation.recommended_version,
                    "reason": recommendation.reason,
                    "confidence": recommendation.confidence,
                    "manifestUrl": recommendation.manifest_url,
                    "downloadUrl": recommendation.download_url,
                    "source": recommendation.source,
                    "modulePath": module.path,
                    "compatibility": recommendation.compatibility,
                    "systemCompatibility": recommendation.system_compatibility,
                    "releasePublishedAt": recommendation.release_published_at,
                    "attentionFlag": recommendation.attention_flag,
                    "checkedReleases": recommendation.checked_releases,
                    "appliedBackupPath": applied_backup,
                    "dependencyActions": [
                        {
                            "module": action.module,
                            "title": installed_modules_by_id.get(action.module).title if installed_modules_by_id.get(action.module) else action.module,
                            "installedVersion": action.installed_version,
                            "recommendedVersion": action.recommended_version,
                            "reason": action.reason,
                            "manifestUrl": action.manifest_url,
                            "compatibility": action.compatibility,
                            "systemCompatibility": action.system_compatibility,
                            "downloadUrl": action.download_url,
                        }
                        for action in recommendation.dependency_actions
                    ],
                    "dependencyUpdates": [
                        {
                            "module": action.module,
                            "title": installed_modules_by_id.get(action.module).title if installed_modules_by_id.get(action.module) else action.module,
                            "installedVersion": action.installed_version,
                            "recommendedVersion": action.recommended_version,
                            "reason": action.reason,
                            "manifestUrl": action.manifest_url,
                            "compatibility": action.compatibility,
                            "systemCompatibility": action.system_compatibility,
                            "downloadUrl": action.download_url,
                        }
                        for action in recommendation.dependency_updates
                    ],
                    "missingDependencies": [
                        {
                            "module": action.module,
                            "title": installed_modules_by_id.get(action.module).title if installed_modules_by_id.get(action.module) else action.module,
                            "installedVersion": action.installed_version,
                            "recommendedVersion": action.recommended_version,
                            "reason": action.reason,
                            "manifestUrl": action.manifest_url,
                            "compatibility": action.compatibility,
                            "systemCompatibility": action.system_compatibility,
                            "downloadUrl": action.download_url,
                        }
                        for action in recommendation.missing_dependencies
                    ],
                }
                )
                if module_warnings:
                    for warning_module_id, module_warning_list in module_warnings.items():
                        bucket = warnings.setdefault(warning_module_id, [])
                        for warning in module_warning_list:
                            if warning not in bucket:
                                bucket.append(warning)
                                logging.warning("%s: %s", warning_module_id, warning)

    backup_maintenance = {
        "removedCount": 0,
        "removedBytes": 0,
        "remainingCount": 0,
        "remainingBytes": 0,
        "removedPaths": [],
    }
    if args.apply:
        backup_maintenance = maintain_backups(
            str(modules_dir),
            max_total_bytes=max(int(args.backup_max_mb), 0) * 1024 * 1024,
            max_per_module=max(int(args.backup_max_per_module), 0),
            max_age_days=max(int(args.backup_max_age_days), 0),
        )
        if backup_maintenance.get("removedCount"):
            logging.info(
                "Pruned module backups: removed %s directories reclaiming %s bytes",
                backup_maintenance.get("removedCount"),
                backup_maintenance.get("removedBytes"),
            )

    module_disk_inventory = collect_module_disk_inventory(all_modules, args.cache_dir)
    backup_inventory = collect_backup_inventory(str(modules_dir), all_modules, args.cache_dir)
    cache_status = describe_cache_status(args.cache_dir, stale_after_days=args.cache_max_age_days)
    try:
        disk_usage = shutil.disk_usage(args.data_root)
        disk_total = int(disk_usage.total)
        disk_used = int(disk_usage.used)
        disk_free = int(disk_usage.free)
        disk_used_percent = round((disk_used / disk_total * 100.0), 1) if disk_total > 0 else 0.0
        disk_free_percent = round((disk_free / disk_total * 100.0), 1) if disk_total > 0 else 0.0
        foundry_disk_status = {
            "path": args.data_root,
            "totalBytes": disk_total,
            "usedBytes": disk_used,
            "freeBytes": disk_free,
            "usedPercent": disk_used_percent,
            "freePercent": disk_free_percent,
        }
    except OSError as exc:
        logging.warning("Unable to collect disk usage for %s: %s", args.data_root, exc)
        foundry_disk_status = {
            "path": args.data_root,
            "totalBytes": 0,
            "usedBytes": 0,
            "freeBytes": 0,
            "usedPercent": 0.0,
            "freePercent": 0.0,
            "error": str(exc),
        }
    foundry_running, foundry_state = _is_foundry_running(args.compose_file, args.foundry_service_name)
    foundry_service_status = {
        "service": args.foundry_service_name,
        "composeFile": args.compose_file,
        "online": bool(foundry_running) if foundry_state == "ok" else False,
        "status": "online" if (foundry_state == "ok" and foundry_running) else ("offline" if foundry_state == "ok" else "unknown"),
        "source": foundry_state,
    }

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targetVersion": target_version,
        "targetVersionSource": detection_source,
        "installedSystemVersions": installed_system_versions,
        "dataRoot": args.data_root,
        "toolRoot": str(TOOL_ROOT),
        "dryRun": args.dry_run,
        "logFile": resolved_log_file,
        "batchSize": args.batch_size,
        "batchCount": batch_count,
        "moduleCount": len(modules),
        "futureFoundryReleases": future_foundry_releases,
        "worldUsage": world_usage,
        "localDependencyMap": local_dependency_map,
        "results": results,
        "cacheDir": args.cache_dir,
        "databasePath": resolved_database_path,
        "cachePolicy": {
            "maxBytes": args.cache_max_mb * 1024 * 1024,
            "maxFiles": args.cache_max_files,
            "maxAgeDays": args.cache_max_age_days,
        },
        "cacheStatus": cache_status,
        "blockedRefresh": {
            "enabled": not args.disable_blocked_refresh,
            "source": blocked_refresh_source,
            "maxModules": max(int(args.blocked_refresh_max), 0),
            "moduleCount": len(hot_blocked_modules),
            "modules": sorted(hot_blocked_modules),
        },
        "moduleDiskInventory": module_disk_inventory,
        "backupInventory": backup_inventory,
        "foundryDiskStatus": foundry_disk_status,
        "foundryServiceStatus": foundry_service_status,
        "apply": args.apply,
        "dependencyApplyActions": apply_actions,
        "backupPolicy": {
            "maxBytes": max(int(args.backup_max_mb), 0) * 1024 * 1024,
            "maxPerModule": max(int(args.backup_max_per_module), 0),
            "maxAgeDays": max(int(args.backup_max_age_days), 0),
        },
        "backupMaintenance": backup_maintenance,
    }
    payload.update(
        build_current_system_upgrade_view(
            target_version,
            world_usage,
            installed_modules_by_id,
            installed_systems_by_id,
            fetch_history_cached,
            fetch_system_history_cached,
            load_module_for_relationship,
        )
    )
    payload.update(
        build_future_upgrade_decision(
            future_foundry_releases,
            world_usage,
            installed_modules_by_id,
            installed_systems_by_id,
            fetch_history_cached,
            fetch_system_history_cached,
            load_module_for_relationship,
        )
    )
    if warnings:
        payload["warnings"] = warnings
    if future_foundry_warnings:
        bucket = warnings.setdefault("foundry-release-catalog", [])
        for warning in future_foundry_warnings:
            if warning not in bucket:
                bucket.append(warning)
        payload["warnings"] = warnings
    scan_run_id = persist_scan_snapshot(
        resolved_database_path,
        payload,
        all_modules,
        installed_system_records,
        world_usage,
        _collapse_history_cache(history_cache),
        _collapse_history_cache(system_history_cache),
    )
    maintenance = maintain_database(
        resolved_database_path,
        max_scan_runs=args.db_max_scan_runs,
    )
    logging.info("Persisted normalized graph snapshot to %s", resolved_database_path)
    if maintenance["removedScanRuns"] or maintenance["removedPackageReleases"]:
        logging.info(
            "Pruned catalog database: removed %s scan runs and %s cached releases%s",
            maintenance["removedScanRuns"],
            maintenance["removedPackageReleases"],
            " and vacuumed" if maintenance["vacuumed"] else "",
        )
    payload["scanRunId"] = scan_run_id
    payload["databasePolicy"] = {
        "maxScanRuns": args.db_max_scan_runs,
    }
    payload["databaseMaintenance"] = maintenance
    payload["databaseSummary"] = load_database_summary(resolved_database_path)
    payload["databasePackageHints"] = load_package_hints(
        resolved_database_path,
        _collect_payload_package_ids(payload),
    )
    attach_report_views(payload)
    if resolved_html_report:
        html = render_report_html(payload)
        with open(resolved_html_report, "w", encoding="utf-8") as handle:
            handle.write(html)
        payload["htmlReport"] = resolved_html_report
        logging.info("HTML report written to %s", resolved_html_report)
    payload["jsonOutput"] = resolved_json_output
    with open(resolved_json_output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    logging.info("JSON report written to %s", resolved_json_output)
    if args.apply and os.environ.get("RESOLVER_SKIP_POST_APPLY_REFRESH") != "1":
        if applied_any:
            if _run_post_apply_refresh(args):
                payload["postApplyRefresh"] = {"status": "ok", "mode": "full-dry-run"}
            else:
                payload["postApplyRefresh"] = {"status": "failed", "mode": "full-dry-run-fallback-applied-report"}
                _sync_public_reports(resolved_html_report, resolved_json_output)
        else:
            # Nothing was changed on disk; refresh latest/public from current state to keep the page in sync.
            if _run_post_apply_refresh(args):
                payload["postApplyRefresh"] = {"status": "ok", "mode": "full-dry-run-no-changes"}
            else:
                payload["postApplyRefresh"] = {"status": "failed", "mode": "full-dry-run-no-changes-fallback-applied-report"}
                _sync_public_reports(resolved_html_report, resolved_json_output)
    else:
        _sync_public_reports(resolved_html_report, resolved_json_output)
    _print_human_summary(
        payload,
        dry_run=args.dry_run,
        apply_mode=args.apply,
        html_report=resolved_html_report,
        json_output=resolved_json_output,
    )
    return 0


def _collapse_history_cache(history_cache: dict[tuple, tuple[list[ReleaseRecord], list[str]]]) -> dict[str, tuple[int, list[ReleaseRecord], list[str]]]:
    collapsed: dict[str, tuple[int, list[ReleaseRecord], list[str]]] = {}
    for key, result in history_cache.items():
        package_id = str(key[0]) if len(key) >= 1 else ""
        release_limit = int(key[1]) if len(key) >= 2 else 0
        if not package_id:
            continue
        previous = collapsed.get(package_id)
        if previous is None or release_limit > previous[0]:
            collapsed[package_id] = (release_limit, result[0], result[1])
    return collapsed


def _collect_payload_package_ids(payload: dict) -> list[str]:
    package_ids: set[str] = set()
    for row in payload.get("results", []) or []:
        module_id = row.get("module")
        if module_id:
            package_ids.add(str(module_id))
        for key in ("dependencyActions", "dependencyUpdates", "missingDependencies"):
            for dep in row.get(key, []) or []:
                dep_id = dep.get("module")
                if dep_id:
                    package_ids.add(str(dep_id))
    for module_id in (payload.get("warnings") or {}).keys():
        package_ids.add(str(module_id))
    best = payload.get("bestFutureUpgradeTarget") or {}
    for row in best.get("moduleOutcomes", []) or []:
        module_id = row.get("module")
        if module_id:
            package_ids.add(str(module_id))
    return sorted(package_ids)


if __name__ == "__main__":
    sys.exit(main())
