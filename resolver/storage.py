from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import ModuleRecord

_FOUNDRY_BACKUP_FILE_RE = re.compile(
    r"^module\.(?P<module>.+?)\.(?P<day>\d{4}-\d{2}-\d{2})\.(?P<stamp>\d+)\.bak$"
)
_SIZE_CACHE_FILE = "module-size-cache.json"
_SIZE_CACHE_TTL_SECONDS = 6 * 3600


def collect_backup_inventory(modules_dir: str, modules: list[ModuleRecord], cache_dir: str) -> dict:
    modules_root = Path(modules_dir)
    cache_path = Path(cache_dir) / _SIZE_CACHE_FILE
    size_cache = _load_size_cache(cache_path)

    module_by_id = {module.module_id: module for module in modules}
    module_sizes: dict[str, int] = {}
    for module in modules:
        module_sizes[module.module_id] = _directory_size_cached(Path(module.path), size_cache)

    grouped_backups: dict[str, list[dict]] = {}
    for backup in _iter_backup_entries(modules_root, size_cache):
        module_id = str(backup.get("module") or "").strip()
        if not module_id:
            continue
        stat_mtime = float(backup.get("mtime") or 0.0)
        size_bytes = int(backup.get("sizeBytes") or 0)
        grouped_backups.setdefault(module_id, []).append(
            {
                "path": str(backup.get("path") or ""),
                "sizeBytes": size_bytes,
                "modifiedAt": _iso_from_timestamp(stat_mtime if stat_mtime > 0 else None),
                "modifiedAtEpoch": stat_mtime,
            }
        )

    rows: list[dict] = []
    total_backup_bytes = 0
    total_backup_count = 0
    for module_id, backups in grouped_backups.items():
        backups.sort(key=lambda item: (-int(item.get("sizeBytes") or 0), str(item.get("path") or "")))
        backup_count = len(backups)
        backup_size_bytes = sum(int(item.get("sizeBytes") or 0) for item in backups)
        total_backup_count += backup_count
        total_backup_bytes += backup_size_bytes
        newest = max(backups, key=lambda item: float(item.get("modifiedAtEpoch") or 0.0)) if backups else {}
        oldest = min(backups, key=lambda item: float(item.get("modifiedAtEpoch") or 0.0)) if backups else {}
        module_record = module_by_id.get(module_id)
        rows.append(
            {
                "module": module_id,
                "title": module_record.title if module_record else module_id,
                "modulePath": module_record.path if module_record else "",
                "moduleSizeBytes": int(module_sizes.get(module_id) or 0),
                "backupCount": backup_count,
                "backupSizeBytes": backup_size_bytes,
                "newestBackupAt": newest.get("modifiedAt"),
                "oldestBackupAt": oldest.get("modifiedAt"),
                "largestBackupBytes": int(backups[0].get("sizeBytes") or 0) if backups else 0,
                "largestBackupPath": backups[0].get("path") if backups else "",
            }
        )

    rows.sort(key=lambda row: (-int(row.get("backupSizeBytes") or 0), str(row.get("title") or row.get("module") or "").lower()))
    total_module_bytes = sum(module_sizes.values())
    _store_size_cache(cache_path, size_cache)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalBackupBytes": total_backup_bytes,
        "totalBackupCount": total_backup_count,
        "moduleCountWithBackups": len(rows),
        "totalModuleBytes": total_module_bytes,
        "moduleCount": len(modules),
        "rows": rows,
        "sizeCache": {
            "path": str(cache_path),
            "entries": len(size_cache.get("entries") or {}),
            "ttlSeconds": _SIZE_CACHE_TTL_SECONDS,
        },
    }


def collect_module_disk_inventory(modules: list[ModuleRecord], cache_dir: str) -> dict:
    cache_path = Path(cache_dir) / _SIZE_CACHE_FILE
    size_cache = _load_size_cache(cache_path)
    by_module: dict[str, dict] = {}
    total_bytes = 0
    for module in modules:
        module_path = Path(module.path)
        size_bytes = _directory_size_cached(module_path, size_cache)
        stat = _safe_stat(module_path)
        total_bytes += size_bytes
        by_module[module.module_id] = {
            "modulePath": str(module_path),
            "sizeBytes": int(size_bytes),
            "modifiedAt": _iso_from_timestamp(stat.st_mtime if stat else None),
            "modifiedAtEpoch": float(stat.st_mtime) if stat else 0.0,
        }
    _store_size_cache(cache_path, size_cache)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalBytes": int(total_bytes),
        "moduleCount": len(modules),
        "byModule": by_module,
        "sizeCache": {
            "path": str(cache_path),
            "entries": len(size_cache.get("entries") or {}),
            "ttlSeconds": _SIZE_CACHE_TTL_SECONDS,
        },
    }


def maintain_backups(
    modules_dir: str,
    max_total_bytes: int,
    max_per_module: int,
    max_age_days: int,
) -> dict:
    modules_root = Path(modules_dir)
    backups = _iter_backup_entries(modules_root)

    removed: list[dict] = []
    now = time.time()
    remaining = backups

    if max_age_days > 0:
        cutoff = now - (max_age_days * 86400)
        keep = []
        for item in remaining:
            if float(item.get("mtime") or 0.0) < cutoff:
                if _delete_backup_entry(item):
                    removed.append(item)
            else:
                keep.append(item)
        remaining = keep

    if max_per_module > 0:
        grouped: dict[str, list[dict]] = {}
        for item in remaining:
            grouped.setdefault(str(item.get("module") or ""), []).append(item)
        keep: list[dict] = []
        for _, rows in grouped.items():
            rows.sort(key=lambda item: float(item.get("mtime") or 0.0), reverse=True)
            keep.extend(rows[:max_per_module])
            for item in rows[max_per_module:]:
                if _delete_backup_entry(item):
                    removed.append(item)
        remaining = keep

    if max_total_bytes > 0:
        remaining.sort(key=lambda item: float(item.get("mtime") or 0.0))
        total_bytes = sum(int(item.get("sizeBytes") or 0) for item in remaining)
        keep: list[dict] = []
        for item in remaining:
            if total_bytes <= max_total_bytes:
                keep.append(item)
                continue
            if _delete_backup_entry(item):
                removed.append(item)
                total_bytes -= int(item.get("sizeBytes") or 0)
            else:
                keep.append(item)
        remaining = keep

    removed_bytes = sum(int(item.get("sizeBytes") or 0) for item in removed)
    remaining_bytes = sum(int(item.get("sizeBytes") or 0) for item in remaining)
    return {
        "removedCount": len(removed),
        "removedBytes": removed_bytes,
        "remainingCount": len(remaining),
        "remainingBytes": remaining_bytes,
        "removedPaths": [str(Path(item["path"])) for item in removed[:200]],
    }


def delete_module_backups(modules_dir: str, module_ids: list[str], delete_all: bool = False) -> dict:
    modules_root = Path(modules_dir)
    selected = {str(module_id).strip() for module_id in module_ids if str(module_id).strip()}
    removed_count = 0
    removed_bytes = 0
    removed_paths: list[str] = []
    for item in _iter_backup_entries(modules_root):
        owner = str(item.get("module") or "").strip()
        if not owner:
            continue
        if not delete_all and owner not in selected:
            continue
        size_bytes = int(item.get("sizeBytes") or 0)
        if _delete_backup_entry(item):
            removed_count += 1
            removed_bytes += size_bytes
            removed_paths.append(str(item.get("path") or ""))
    return {
        "removedCount": removed_count,
        "removedBytes": removed_bytes,
        "removedPaths": removed_paths[:500],
        "selectedModules": sorted(selected),
        "deleteAll": bool(delete_all),
    }


def delete_modules(modules_dir: str, module_ids: list[str], delete_all: bool = False) -> dict:
    modules_root = Path(modules_dir)
    selected = {str(module_id).strip() for module_id in module_ids if str(module_id).strip()}
    removed_count = 0
    removed_bytes = 0
    removed_paths: list[str] = []
    for entry in modules_root.iterdir() if modules_root.exists() else []:
        if not entry.is_dir():
            continue
        module_name = entry.name
        if not module_name:
            continue
        if not delete_all and module_name not in selected:
            continue
        if entry.parent != modules_root:
            # Safety: only allow deletion for direct children under modules root.
            continue
        size_bytes = _directory_size(entry)
        if _delete_backup_path(entry):
            removed_count += 1
            removed_bytes += size_bytes
            removed_paths.append(str(entry))
    backup_cleanup = delete_module_backups(modules_dir, module_ids, delete_all=delete_all)
    removed_count += int(backup_cleanup.get("removedCount") or 0)
    removed_bytes += int(backup_cleanup.get("removedBytes") or 0)
    removed_paths.extend([str(path) for path in (backup_cleanup.get("removedPaths") or [])])
    return {
        "removedCount": removed_count,
        "removedBytes": removed_bytes,
        "removedPaths": removed_paths[:500],
        "selectedModules": sorted(selected),
        "deleteAll": bool(delete_all),
    }


def _iter_backup_entries(modules_root: Path, size_cache: dict | None = None) -> list[dict]:
    entries: list[dict] = []

    foundry_modules_root = _foundry_backup_modules_root(modules_root)
    if foundry_modules_root.exists():
        for module_backup_dir in sorted(entry for entry in foundry_modules_root.iterdir() if entry.is_dir()):
            for bak_file in sorted(module_backup_dir.glob("*.bak")):
                module_id = _backup_owner_module_id_from_foundry_file(bak_file.name) or module_backup_dir.name
                bak_stat = _safe_stat(bak_file)
                if bak_stat is None:
                    continue
                json_file = bak_file.with_suffix(".json")
                json_stat = _safe_stat(json_file)
                size_bytes = int(bak_stat.st_size) + int(json_stat.st_size if json_stat else 0)
                mtime = max(float(bak_stat.st_mtime), float(json_stat.st_mtime) if json_stat else 0.0)
                entries.append(
                    {
                        "kind": "foundry-file",
                        "module": module_id,
                        "path": str(bak_file),
                        "jsonPath": str(json_file) if json_file.exists() else "",
                        "mtime": mtime,
                        "sizeBytes": size_bytes,
                    }
                )

    return entries


def _backup_owner_module_id_from_foundry_file(filename: str) -> str | None:
    match = _FOUNDRY_BACKUP_FILE_RE.match(filename)
    if not match:
        return None
    module_id = str(match.group("module") or "").strip()
    return module_id or None


def _foundry_backup_modules_root(modules_root: Path) -> Path:
    # modules_root is expected to be <data-root>/Data/modules
    data_root = modules_root.parent.parent
    return data_root / "Backups" / "modules"


def _delete_backup_entry(item: dict) -> bool:
    kind = str(item.get("kind") or "")
    if kind == "foundry-file":
        bak_path = Path(str(item.get("path") or ""))
        json_path_value = str(item.get("jsonPath") or "").strip()
        json_path = Path(json_path_value) if json_path_value else bak_path.with_suffix(".json")
        ok = _delete_backup_path(bak_path)
        if json_path.exists():
            ok = _delete_backup_path(json_path) and ok
        module_backup_dir = bak_path.parent
        if module_backup_dir.exists() and module_backup_dir.is_dir():
            try:
                next(module_backup_dir.iterdir())
            except StopIteration:
                _delete_backup_path(module_backup_dir)
            except OSError:
                pass
        return ok
    return False


def _delete_backup_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            logging.info("Deleted backup directory %s", path)
        else:
            path.unlink(missing_ok=True)
            logging.info("Deleted backup file %s", path)
        return True
    except OSError as exc:
        logging.warning("Failed to delete backup path %s: %s", path, exc)
        return False


def _safe_stat(path: Path):
    try:
        return path.stat()
    except OSError:
        return None


def _directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for node in path.rglob("*"):
        try:
            if node.is_file():
                total += node.stat().st_size
        except OSError:
            continue
    return total


def _directory_size_cached(path: Path, cache: dict) -> int:
    entries = cache.setdefault("entries", {})
    key = str(path)
    now = time.time()
    stat = _safe_stat(path)
    if stat is None:
        entries.pop(key, None)
        return 0
    cached = entries.get(key) if isinstance(entries, dict) else None
    if isinstance(cached, dict):
        cached_mtime = int(cached.get("mtimeNs") or 0)
        cached_checked = float(cached.get("checkedAtEpoch") or 0.0)
        if cached_mtime == int(stat.st_mtime_ns) and (now - cached_checked) < _SIZE_CACHE_TTL_SECONDS:
            return int(cached.get("sizeBytes") or 0)
    size_bytes = _directory_size(path)
    entries[key] = {
        "mtimeNs": int(stat.st_mtime_ns),
        "sizeBytes": int(size_bytes),
        "checkedAtEpoch": now,
    }
    return size_bytes


def _load_size_cache(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "entries": {}}


def _store_size_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    cache["entries"] = entries
    if len(entries) > 5000:
        # Keep cache bounded so it cannot grow forever.
        sorted_items = sorted(
            entries.items(),
            key=lambda item: float((item[1] or {}).get("checkedAtEpoch") or 0.0),
            reverse=True,
        )
        cache["entries"] = dict(sorted_items[:5000])
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2)
            handle.write("\n")
    except OSError:
        return


def _iso_from_timestamp(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
