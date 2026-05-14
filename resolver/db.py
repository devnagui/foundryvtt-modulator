from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import ModuleRecord, ReleaseRecord

SCHEMA_VERSION = 1
DEFAULT_MAX_SCAN_RUNS = 20


def default_database_path(tool_root: str) -> str:
    return str(Path(tool_root) / "state" / "resolver.db")


def persist_scan_snapshot(
    database_path: str,
    payload: dict,
    installed_modules: list[ModuleRecord],
    installed_systems: list[ModuleRecord],
    world_usage: list[dict],
    module_histories: dict[str, tuple[int, list[ReleaseRecord], list[str]]],
    system_histories: dict[str, tuple[int, list[ReleaseRecord], list[str]]],
) -> int:
    db_path = Path(database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_schema(connection)
        scan_run_id = _insert_scan_run(connection, payload)
        _replace_installed_packages(connection, scan_run_id, "module", installed_modules)
        _replace_installed_packages(connection, scan_run_id, "system", installed_systems)
        _replace_world_usage(connection, scan_run_id, world_usage)
        _upsert_release_catalog(connection, "module", installed_modules, module_histories)
        _upsert_release_catalog(connection, "system", installed_systems, system_histories)
        _replace_recommendations(connection, scan_run_id, payload)
        _replace_future_targets(connection, scan_run_id, payload)
        _replace_planning_context_rows(connection, scan_run_id, payload)
        connection.commit()
        return scan_run_id


def maintain_database(
    database_path: str,
    max_scan_runs: int = DEFAULT_MAX_SCAN_RUNS,
) -> dict[str, int | bool | str | None]:
    db_path = Path(database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    before_bytes = db_path.stat().st_size if db_path.exists() else 0
    stats: dict[str, int | bool | str | None] = {
        "maxScanRuns": max(1, int(max_scan_runs)),
        "beforeBytes": before_bytes,
        "afterBytes": before_bytes,
        "removedScanRuns": 0,
        "removedPackageReleases": 0,
        "removedFoundryCompat": 0,
        "removedSystemCompat": 0,
        "removedDependencies": 0,
        "vacuumed": False,
        "databasePath": str(db_path),
    }
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_schema(connection)
        keep_scan_ids = _load_kept_scan_run_ids(connection, max_scan_runs=max_scan_runs)
        removed_scan_runs = _prune_scan_runs(connection, keep_scan_ids)
        stats["removedScanRuns"] = removed_scan_runs
        release_prune = _prune_release_catalog(connection, keep_scan_ids)
        stats.update(release_prune)
        connection.execute("PRAGMA optimize")
        connection.commit()
    if removed_scan_runs or int(stats["removedPackageReleases"] or 0):
        with sqlite3.connect(db_path) as connection:
            connection.execute("VACUUM")
        stats["vacuumed"] = True
    stats["afterBytes"] = db_path.stat().st_size if db_path.exists() else 0
    return stats


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            target_version TEXT,
            data_root TEXT,
            dry_run INTEGER NOT NULL,
            apply_mode INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS installed_packages (
            scan_run_id INTEGER NOT NULL,
            package_kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            title TEXT,
            version TEXT,
            manifest_url TEXT,
            project_url TEXT,
            package_path TEXT,
            raw_manifest_json TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, package_kind, package_id),
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS worlds (
            scan_run_id INTEGER NOT NULL,
            world_id TEXT NOT NULL,
            title TEXT,
            system_id TEXT,
            system_version TEXT,
            core_version TEXT,
            module_configuration_source TEXT,
            module_configuration_resolved INTEGER NOT NULL,
            module_configuration_method TEXT,
            PRIMARY KEY (scan_run_id, world_id),
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS world_modules (
            scan_run_id INTEGER NOT NULL,
            world_id TEXT NOT NULL,
            module_id TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, world_id, module_id),
            FOREIGN KEY (scan_run_id, world_id) REFERENCES worlds(scan_run_id, world_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS package_releases (
            package_kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            version TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT,
            manifest_url TEXT,
            download_url TEXT,
            raw_manifest_json TEXT NOT NULL,
            last_seen_scan_at TEXT NOT NULL,
            release_limit INTEGER,
            warnings_json TEXT NOT NULL,
            PRIMARY KEY (package_kind, package_id, version, source)
        );

        CREATE TABLE IF NOT EXISTS release_foundry_compat (
            package_kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            version TEXT NOT NULL,
            source TEXT NOT NULL,
            minimum_version TEXT,
            verified_version TEXT,
            maximum_version TEXT,
            PRIMARY KEY (package_kind, package_id, version, source),
            FOREIGN KEY (package_kind, package_id, version, source)
                REFERENCES package_releases(package_kind, package_id, version, source)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS release_system_compat (
            package_kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            version TEXT NOT NULL,
            source TEXT NOT NULL,
            system_id TEXT NOT NULL,
            minimum_version TEXT,
            verified_version TEXT,
            maximum_version TEXT,
            PRIMARY KEY (package_kind, package_id, version, source, system_id),
            FOREIGN KEY (package_kind, package_id, version, source)
                REFERENCES package_releases(package_kind, package_id, version, source)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS release_dependencies (
            package_kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            version TEXT NOT NULL,
            source TEXT NOT NULL,
            dependency_id TEXT NOT NULL,
            dependency_type TEXT NOT NULL,
            dependency_manifest_url TEXT,
            minimum_version TEXT,
            verified_version TEXT,
            maximum_version TEXT,
            PRIMARY KEY (package_kind, package_id, version, source, dependency_id, dependency_type),
            FOREIGN KEY (package_kind, package_id, version, source)
                REFERENCES package_releases(package_kind, package_id, version, source)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            scan_run_id INTEGER NOT NULL,
            module_id TEXT NOT NULL,
            title TEXT,
            installed_version TEXT,
            recommended_version TEXT,
            reason TEXT,
            confidence TEXT,
            source TEXT,
            manifest_url TEXT,
            download_url TEXT,
            compatibility_json TEXT NOT NULL,
            system_compatibility_json TEXT NOT NULL,
            dependency_actions_json TEXT NOT NULL,
            dependency_updates_json TEXT NOT NULL,
            missing_dependencies_json TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, module_id),
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS future_targets (
            scan_run_id INTEGER NOT NULL,
            target_foundry_version TEXT NOT NULL,
            target_foundry_url TEXT,
            coverage_percent REAL,
            ready_count INTEGER,
            upgradable_count INTEGER,
            blocked_count INTEGER,
            unresolved_dependency_count INTEGER,
            summary_json TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, target_foundry_version),
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS future_target_modules (
            scan_run_id INTEGER NOT NULL,
            target_foundry_version TEXT NOT NULL,
            module_id TEXT NOT NULL,
            title TEXT,
            installed_version TEXT,
            recommended_version TEXT,
            status TEXT,
            confidence TEXT,
            reason TEXT,
            source TEXT,
            compatibility_json TEXT NOT NULL,
            system_compatibility_json TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, target_foundry_version, module_id),
            FOREIGN KEY (scan_run_id, target_foundry_version)
                REFERENCES future_targets(scan_run_id, target_foundry_version)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS planning_context_rows (
            scan_run_id INTEGER NOT NULL,
            context_key TEXT NOT NULL,
            foundry_version TEXT NOT NULL,
            system_id TEXT NOT NULL,
            system_version TEXT NOT NULL,
            module_id TEXT NOT NULL,
            status TEXT NOT NULL,
            has_missing_dependencies INTEGER NOT NULL,
            title TEXT,
            installed_version TEXT,
            recommended_version TEXT,
            reason TEXT,
            compatibility_json TEXT NOT NULL,
            system_compatibility_json TEXT NOT NULL,
            PRIMARY KEY (scan_run_id, context_key, module_id),
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_planning_context_lookup
            ON planning_context_rows(scan_run_id, foundry_version, system_id, system_version, status);
        """
    )
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _insert_scan_run(connection: sqlite3.Connection, payload: dict) -> int:
    cursor = connection.execute(
        """
        INSERT INTO scan_runs(generated_at, target_version, data_root, dry_run, apply_mode, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            str(payload.get("generatedAt") or ""),
            str(payload.get("targetVersion") or ""),
            str(payload.get("dataRoot") or ""),
            1 if payload.get("dryRun") else 0,
            1 if payload.get("apply") else 0,
            json.dumps(payload, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def _replace_installed_packages(
    connection: sqlite3.Connection,
    scan_run_id: int,
    package_kind: str,
    packages: Iterable[ModuleRecord],
) -> None:
    rows = [
        (
            scan_run_id,
            package_kind,
            package.module_id,
            package.title,
            package.version,
            package.manifest_url,
            package.project_url,
            package.path,
            json.dumps(package.raw_manifest, sort_keys=True),
        )
        for package in packages
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO installed_packages(
            scan_run_id, package_kind, package_id, title, version, manifest_url, project_url, package_path, raw_manifest_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _replace_world_usage(connection: sqlite3.Connection, scan_run_id: int, world_usage: list[dict]) -> None:
    world_rows = []
    module_rows = []
    for world in world_usage:
        world_id = str(world.get("id") or "")
        if not world_id:
            continue
        world_rows.append(
            (
                scan_run_id,
                world_id,
                world.get("title"),
                world.get("system"),
                world.get("systemVersion"),
                world.get("coreVersion"),
                world.get("moduleConfigurationSource"),
                1 if world.get("moduleConfigurationResolved") else 0,
                world.get("moduleConfigurationMethod"),
            )
        )
        for module_id in world.get("enabledModules") or []:
            module_rows.append((scan_run_id, world_id, str(module_id)))
    connection.executemany(
        """
        INSERT OR REPLACE INTO worlds(
            scan_run_id, world_id, title, system_id, system_version, core_version,
            module_configuration_source, module_configuration_resolved, module_configuration_method
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        world_rows,
    )
    connection.executemany(
        "INSERT OR REPLACE INTO world_modules(scan_run_id, world_id, module_id) VALUES(?, ?, ?)",
        module_rows,
    )


def _upsert_release_catalog(
    connection: sqlite3.Connection,
    package_kind: str,
    installed_packages: list[ModuleRecord],
    histories: dict[str, tuple[int, list[ReleaseRecord], list[str]]],
) -> None:
    package_map = {package.module_id: package for package in installed_packages}
    release_rows = []
    foundry_rows = []
    system_rows = []
    dependency_rows = []
    for package_id, package in package_map.items():
        history = histories.get(package_id)
        if history is None:
            continue
        release_limit, releases, warnings = history
        for release in releases:
            release_rows.append(
                (
                    package_kind,
                    package_id,
                    release.version,
                    release.source,
                    package.title,
                    release.manifest_url,
                    release.download_url,
                    json.dumps(release.raw_manifest, sort_keys=True),
                    str(connection.execute("SELECT generated_at FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()[0]),
                    release_limit,
                    json.dumps(warnings, sort_keys=True),
                )
            )
            foundry_rows.append(
                (
                    package_kind,
                    package_id,
                    release.version,
                    release.source,
                    _compat_value(release.compatibility, "minimum"),
                    _compat_value(release.compatibility, "verified"),
                    _compat_value(release.compatibility, "maximum"),
                )
            )
            system_compatibility_map = release.system_compatibility if isinstance(release.system_compatibility, dict) else {}
            for system_id, compatibility in sorted(system_compatibility_map.items()):
                system_rows.append(
                    (
                        package_kind,
                        package_id,
                        release.version,
                        release.source,
                        system_id,
                        _compat_value(compatibility, "minimum"),
                        _compat_value(compatibility, "verified"),
                        _compat_value(compatibility, "maximum"),
                    )
                )
            for requirement in release.module_requirements:
                dependency_rows.append(
                    (
                        package_kind,
                        package_id,
                        release.version,
                        release.source,
                        requirement.module_id,
                        requirement.type,
                        requirement.manifest_url,
                        _compat_value(requirement.compatibility, "minimum"),
                        _compat_value(requirement.compatibility, "verified"),
                        _compat_value(requirement.compatibility, "maximum"),
                    )
                )
    connection.executemany(
        """
        INSERT INTO package_releases(
            package_kind, package_id, version, source, title, manifest_url, download_url,
            raw_manifest_json, last_seen_scan_at, release_limit, warnings_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(package_kind, package_id, version, source) DO UPDATE SET
            title=excluded.title,
            manifest_url=excluded.manifest_url,
            download_url=excluded.download_url,
            raw_manifest_json=excluded.raw_manifest_json,
            last_seen_scan_at=excluded.last_seen_scan_at,
            release_limit=excluded.release_limit,
            warnings_json=excluded.warnings_json
        """,
        release_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO release_foundry_compat(
            package_kind, package_id, version, source, minimum_version, verified_version, maximum_version
        )
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        foundry_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO release_system_compat(
            package_kind, package_id, version, source, system_id, minimum_version, verified_version, maximum_version
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        system_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO release_dependencies(
            package_kind, package_id, version, source, dependency_id, dependency_type, dependency_manifest_url,
            minimum_version, verified_version, maximum_version
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        dependency_rows,
    )


def _replace_recommendations(connection: sqlite3.Connection, scan_run_id: int, payload: dict) -> None:
    rows = [
        (
            scan_run_id,
            str(row.get("module") or ""),
            row.get("title"),
            row.get("installedVersion"),
            row.get("recommendedVersion"),
            row.get("reason"),
            row.get("confidence"),
            row.get("source"),
            row.get("manifestUrl"),
            row.get("downloadUrl"),
            json.dumps(row.get("compatibility") or {}, sort_keys=True),
            json.dumps(row.get("systemCompatibility") or {}, sort_keys=True),
            json.dumps(row.get("dependencyActions") or [], sort_keys=True),
            json.dumps(row.get("dependencyUpdates") or [], sort_keys=True),
            json.dumps(row.get("missingDependencies") or [], sort_keys=True),
        )
        for row in payload.get("results", []) or []
        if row.get("module")
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO recommendations(
            scan_run_id, module_id, title, installed_version, recommended_version, reason, confidence, source,
            manifest_url, download_url, compatibility_json, system_compatibility_json,
            dependency_actions_json, dependency_updates_json, missing_dependencies_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _replace_future_targets(connection: sqlite3.Connection, scan_run_id: int, payload: dict) -> None:
    targets = payload.get("futureUpgradeMatrix") or []
    target_rows = []
    target_module_rows = []
    for target in targets:
        target_foundry_version = str(target.get("targetFoundryVersion") or "")
        if not target_foundry_version:
            continue
        target_rows.append(
            (
                scan_run_id,
                target_foundry_version,
                target.get("targetFoundryUrl"),
                target.get("coveragePercent"),
                target.get("readyCount"),
                target.get("upgradableCount"),
                target.get("blockedCount"),
                target.get("unresolvedDependencyCount"),
                json.dumps(target, sort_keys=True),
            )
        )
        for row in target.get("moduleOutcomes") or []:
            target_module_rows.append(
                (
                    scan_run_id,
                    target_foundry_version,
                    str(row.get("module") or ""),
                    row.get("title"),
                    row.get("installedVersion"),
                    row.get("recommendedVersion"),
                    row.get("status"),
                    row.get("confidence"),
                    row.get("reason"),
                    row.get("source"),
                    json.dumps(row.get("compatibility") or {}, sort_keys=True),
                    json.dumps(row.get("systemCompatibility") or {}, sort_keys=True),
                )
            )
    connection.executemany(
        """
        INSERT OR REPLACE INTO future_targets(
            scan_run_id, target_foundry_version, target_foundry_url, coverage_percent,
            ready_count, upgradable_count, blocked_count, unresolved_dependency_count, summary_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        target_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO future_target_modules(
            scan_run_id, target_foundry_version, module_id, title, installed_version, recommended_version,
            status, confidence, reason, source, compatibility_json, system_compatibility_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [row for row in target_module_rows if row[2]],
    )


def _replace_planning_context_rows(connection: sqlite3.Connection, scan_run_id: int, payload: dict) -> None:
    view = ((payload.get("reportViews") or {}).get("v3") or {}) if isinstance(payload.get("reportViews"), dict) else {}
    planner = view.get("systemUpgradePlanner") if isinstance(view, dict) else {}
    targets = planner.get("targets") if isinstance(planner, dict) else []
    if not isinstance(targets, list):
        return

    rows: list[tuple] = []
    bucket_defs = (
        ("blockedModuleRows", "blocked"),
        ("upgradableModuleRows", "update"),
        ("compatibleModuleRows", "ready"),
        ("unknownModuleRows", "blocked"),
        ("localManifestManualModules", "blocked"),
    )
    for target in targets:
        if not isinstance(target, dict):
            continue
        foundry_version = str(target.get("foundryVersion") or "").strip()
        if not foundry_version:
            continue
        system_rows = target.get("systemRows")
        if not isinstance(system_rows, list):
            continue
        for system_row in system_rows:
            if not isinstance(system_row, dict):
                continue
            system_id = str(system_row.get("systemId") or "").strip()
            system_version = str(system_row.get("targetVersion") or "").strip()
            if not system_id or not system_version:
                continue
            context_key = f"{foundry_version}::{system_id}@{system_version}"
            for bucket_key, fallback_status in bucket_defs:
                bucket = system_row.get(bucket_key)
                if not isinstance(bucket, list):
                    continue
                for item in bucket:
                    if not isinstance(item, dict):
                        continue
                    module_id = str(item.get("module") or "").strip()
                    if not module_id:
                        continue
                    reason = str(item.get("reason") or "")
                    has_missing = bool(item.get("hasMissingDependencies")) or ("missing_dependency:" in reason.lower())
                    status = str(item.get("presentationStatus") or fallback_status).strip().lower()
                    if has_missing:
                        status = "missing"
                    elif status not in {"missing", "blocked", "update", "ready"}:
                        status = fallback_status
                    rows.append(
                        (
                            scan_run_id,
                            context_key,
                            foundry_version,
                            system_id,
                            system_version,
                            module_id,
                            status,
                            1 if has_missing else 0,
                            item.get("title"),
                            item.get("installedVersion"),
                            item.get("recommendedVersion"),
                            reason,
                            json.dumps(item.get("compatibility") or {}, sort_keys=True),
                            json.dumps(item.get("systemCompatibility") or {}, sort_keys=True),
                        )
                    )

    if not rows:
        return
    connection.executemany(
        """
        INSERT OR REPLACE INTO planning_context_rows(
            scan_run_id, context_key, foundry_version, system_id, system_version, module_id,
            status, has_missing_dependencies, title, installed_version, recommended_version, reason,
            compatibility_json, system_compatibility_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _compat_value(compatibility: object, key: str) -> str | None:
    if isinstance(compatibility, list):
        compatibility = next((item for item in compatibility if isinstance(item, dict)), None)
    if not isinstance(compatibility, dict):
        return None
    value = compatibility.get(key)
    if value in (None, ""):
        return None
    return str(value)


def _load_kept_scan_run_ids(connection: sqlite3.Connection, max_scan_runs: int) -> list[int]:
    rows = connection.execute(
        "SELECT id FROM scan_runs ORDER BY id DESC LIMIT ?",
        (max(1, int(max_scan_runs)),),
    ).fetchall()
    return [int(row[0]) for row in rows]


def _prune_scan_runs(connection: sqlite3.Connection, keep_scan_ids: list[int]) -> int:
    if not keep_scan_ids:
        return 0
    placeholders = ", ".join("?" for _ in keep_scan_ids)
    row = connection.execute(
        f"SELECT COUNT(*) FROM scan_runs WHERE id NOT IN ({placeholders})",
        keep_scan_ids,
    ).fetchone()
    removed_count = int(row[0]) if row else 0
    if removed_count:
        connection.execute(
            f"DELETE FROM scan_runs WHERE id NOT IN ({placeholders})",
            keep_scan_ids,
        )
    return removed_count


def _prune_release_catalog(connection: sqlite3.Connection, keep_scan_ids: list[int]) -> dict[str, int]:
    if not keep_scan_ids:
        return {
            "removedPackageReleases": 0,
            "removedFoundryCompat": 0,
            "removedSystemCompat": 0,
            "removedDependencies": 0,
        }
    placeholders = ", ".join("?" for _ in keep_scan_ids)
    cutoff_row = connection.execute(
        f"SELECT MIN(generated_at) FROM scan_runs WHERE id IN ({placeholders})",
        keep_scan_ids,
    ).fetchone()
    cutoff_generated_at = str(cutoff_row[0]) if cutoff_row and cutoff_row[0] else None
    if not cutoff_generated_at:
        return {
            "removedPackageReleases": 0,
            "removedFoundryCompat": 0,
            "removedSystemCompat": 0,
            "removedDependencies": 0,
        }

    release_keys = connection.execute(
        """
        SELECT package_kind, package_id, version, source
        FROM package_releases
        WHERE last_seen_scan_at < ?
        """,
        (cutoff_generated_at,),
    ).fetchall()
    if not release_keys:
        return {
            "removedPackageReleases": 0,
            "removedFoundryCompat": 0,
            "removedSystemCompat": 0,
            "removedDependencies": 0,
        }

    removed_foundry = 0
    removed_system = 0
    removed_dependencies = 0
    for package_kind, package_id, version, source in release_keys:
        key = (package_kind, package_id, version, source)
        removed_foundry += int(
            connection.execute(
                """
                SELECT COUNT(*) FROM release_foundry_compat
                WHERE package_kind = ? AND package_id = ? AND version = ? AND source = ?
                """,
                key,
            ).fetchone()[0]
        )
        removed_system += int(
            connection.execute(
                """
                SELECT COUNT(*) FROM release_system_compat
                WHERE package_kind = ? AND package_id = ? AND version = ? AND source = ?
                """,
                key,
            ).fetchone()[0]
        )
        removed_dependencies += int(
            connection.execute(
                """
                SELECT COUNT(*) FROM release_dependencies
                WHERE package_kind = ? AND package_id = ? AND version = ? AND source = ?
                """,
                key,
            ).fetchone()[0]
        )

    removed_releases = len(release_keys)
    connection.execute(
        "DELETE FROM package_releases WHERE last_seen_scan_at < ?",
        (cutoff_generated_at,),
    )
    return {
        "removedPackageReleases": removed_releases,
        "removedFoundryCompat": removed_foundry,
        "removedSystemCompat": removed_system,
        "removedDependencies": removed_dependencies,
    }
