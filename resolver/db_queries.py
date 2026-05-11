from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def load_database_summary(database_path: str) -> dict:
    path = Path(database_path)
    if not path.exists():
        return {}
    stat = path.stat()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        latest_scan = connection.execute(
            "SELECT id, generated_at, target_version FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        table_counts = {}
        for table in (
            "scan_runs",
            "installed_packages",
            "worlds",
            "world_modules",
            "package_releases",
            "release_dependencies",
            "recommendations",
            "future_targets",
            "future_target_modules",
        ):
            table_counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "databasePath": str(path),
            "fileBytes": int(stat.st_size),
            "modifiedAt": _stat_timestamp(stat),
            "latestScanId": int(latest_scan["id"]) if latest_scan else None,
            "latestGeneratedAt": str(latest_scan["generated_at"]) if latest_scan else None,
            "latestTargetVersion": str(latest_scan["target_version"]) if latest_scan else None,
            "counts": table_counts,
        }


def load_package_hints(database_path: str, package_ids: list[str]) -> dict[str, dict]:
    path = Path(database_path)
    if not path.exists() or not package_ids:
        return {}
    unique_ids = sorted({str(item).strip() for item in package_ids if str(item).strip()})
    if not unique_ids:
        return {}
    placeholders = ", ".join("?" for _ in unique_ids)
    query = f"""
        SELECT
            pr.package_id,
            pr.title,
            pr.version,
            pr.source,
            pr.manifest_url,
            pr.download_url,
            pr.raw_manifest_json,
            rf.minimum_version,
            rf.verified_version,
            rf.maximum_version
        FROM package_releases pr
        LEFT JOIN release_foundry_compat rf
          ON rf.package_kind = pr.package_kind
         AND rf.package_id = pr.package_id
         AND rf.version = pr.version
         AND rf.source = pr.source
        WHERE pr.package_kind = 'module'
          AND pr.package_id IN ({placeholders})
        ORDER BY pr.package_id, pr.last_seen_scan_at DESC
    """
    hints: dict[str, dict] = {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, unique_ids).fetchall()
        for row in rows:
            package_id = str(row["package_id"])
            if package_id in hints:
                continue
            raw_manifest = _decode_json(row["raw_manifest_json"])
            hints[package_id] = {
                "title": row["title"] or package_id,
                "manifestUrl": row["manifest_url"],
                "downloadUrl": row["download_url"],
                "latestKnownVersion": row["version"],
                "latestKnownSource": row["source"],
                "compatibility": {
                    "minimum": row["minimum_version"],
                    "verified": row["verified_version"],
                    "maximum": row["maximum_version"],
                },
                "systemCompatibility": _extract_system_compatibility(raw_manifest),
            }
    return hints


def load_scan_run_payload(database_path: str, scan_run_id: int) -> dict | None:
    path = Path(database_path)
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT payload_json FROM scan_runs WHERE id = ?", (scan_run_id,)).fetchone()
        if not row:
            return None
        return _decode_json(row[0])


def load_apply_history(database_path: str, limit: int = 20) -> list[dict]:
    path = Path(database_path)
    if not path.exists():
        return []
    rows_out: list[dict] = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, generated_at, target_version, payload_json
            FROM scan_runs
            WHERE apply_mode = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        for row in rows:
            payload = _decode_json(row["payload_json"])
            actions = payload.get("dependencyApplyActions") or []
            backups = [str(item.get("backupPath") or "").strip() for item in actions if isinstance(item, dict)]
            modules = [str(item.get("module") or "").strip() for item in actions if isinstance(item, dict)]
            batch_snapshot = payload.get("batchSnapshot") if isinstance(payload.get("batchSnapshot"), dict) else {}
            changed_modules = [
                str(item).strip()
                for item in (batch_snapshot.get("changedModules") or [])
                if str(item).strip()
            ]
            rows_out.append(
                {
                    "scanRunId": int(row["id"]),
                    "generatedAt": str(row["generated_at"] or ""),
                    "targetVersion": str(row["target_version"] or ""),
                    "modulesChanged": sorted({m for m in (modules + changed_modules) if m}),
                    "modulesChangedCount": len({m for m in (modules + changed_modules) if m}),
                    "backupsCreatedCount": len([b for b in backups if b]),
                    "backupPaths": [b for b in backups if b],
                    "batchSnapshot": batch_snapshot,
                }
            )
    return rows_out


def _extract_system_compatibility(raw_manifest: dict) -> dict[str, dict]:
    relationships = (raw_manifest or {}).get("relationships") or {}
    systems = relationships.get("systems") or []
    compatibility_by_system: dict[str, dict] = {}
    for item in systems:
        system_id = item.get("id")
        if system_id:
            compatibility_by_system[str(system_id)] = item.get("compatibility") or {}
    return compatibility_by_system


def _decode_json(raw_value) -> dict:
    if not raw_value:
        return {}
    try:
        return json.loads(raw_value)
    except Exception:
        return {}


def _stat_timestamp(stat_result: os.stat_result) -> str | None:
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return None
