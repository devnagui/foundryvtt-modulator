from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ModuleRecord, Recommendation
from .sources import delete_cached_zip, download_to_temp


def apply_recommendation(
    module: ModuleRecord,
    recommendation: Recommendation,
    modules_dir: str,
    cache_dir: str,
) -> str | None:
    if not recommendation.download_url:
        raise ValueError(f"No download URL is available for {recommendation.module}.")
    archive_path = download_to_temp(recommendation.download_url)
    modules_root = Path(modules_dir)
    target_dir = modules_root / module.module_id
    backup_path: str | None = None
    if target_dir.exists() and target_dir.is_dir():
        backup_path = _create_foundry_style_backup(
            module=module,
            recommendation=recommendation,
            target_dir=target_dir,
            modules_root=modules_root,
        )

    try:
        with tempfile.TemporaryDirectory(prefix=f"{module.module_id}-upgrade-") as temp_dir:
            temp_root = Path(temp_dir)
            with zipfile.ZipFile(archive_path, "r") as archive:
                archive.extractall(temp_root)

            extracted_dir = _find_module_root(temp_root, module.module_id)
            if extracted_dir is None:
                raise ValueError(f"Could not locate extracted module root for {module.module_id} in {archive_path}.")

            if target_dir.exists():
                if target_dir.is_dir():
                    shutil.rmtree(target_dir)
                else:
                    target_dir.unlink()
            shutil.move(str(extracted_dir), str(target_dir))
    finally:
        with contextlib.suppress(OSError):
            Path(archive_path).unlink()
        with contextlib.suppress(OSError):
            delete_cached_zip(recommendation.download_url or "", cache_dir)
    logging.info("Applied module %s to %s", module.module_id, target_dir)
    return backup_path


def force_module_compatibility(module: ModuleRecord, modules_dir: str, target_version: str) -> dict:
    modules_root = Path(modules_dir)
    target_dir = modules_root / module.module_id
    module_json_path = target_dir / "module.json"
    if not target_dir.exists() or not target_dir.is_dir():
        raise ValueError(f"Module directory not found for {module.module_id}: {target_dir}")
    if not module_json_path.exists():
        raise ValueError(f"module.json not found for {module.module_id}: {module_json_path}")

    # Reuse Foundry-style backup format before mutating local manifest compatibility.
    backup_stub = Recommendation(
        module=module.module_id,
        installed_version=module.version or "",
        recommended_version=module.version or "",
        reason=f"forced-compatibility-{target_version}",
        confidence="manual",
        verified_version=str((module.raw_manifest.get("compatibility") or {}).get("verified") or ""),
        manifest_url=module.manifest_url,
        download_url=None,
        source="local",
        checked_releases=0,
    )
    backup_path = _create_foundry_style_backup(
        module=module,
        recommendation=backup_stub,
        target_dir=target_dir,
        modules_root=modules_root,
    )

    with module_json_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    compatibility_raw = manifest.get("compatibility")
    compatibility: dict = dict(compatibility_raw) if isinstance(compatibility_raw, dict) else {}
    previous_compatibility = dict(compatibility)
    # Forced compatibility should not claim upstream verification.
    # Keep existing "verified" metadata untouched and only raise "maximum".
    compatibility["maximum"] = target_version
    manifest["compatibility"] = compatibility

    flags_raw = manifest.get("flags")
    flags: dict = dict(flags_raw) if isinstance(flags_raw, dict) else {}
    resolver_flags_raw = flags.get("resolver")
    resolver_flags: dict = dict(resolver_flags_raw) if isinstance(resolver_flags_raw, dict) else {}
    resolver_flags["forcedCompatibility"] = {
        "enabled": True,
        "targetVersion": target_version,
        "appliedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    flags["resolver"] = resolver_flags
    manifest["flags"] = flags

    with module_json_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    logging.info(
        "Forced compatibility applied for %s (maximum -> %s, verified preserved)",
        module.module_id,
        target_version,
    )
    return {
        "module": module.module_id,
        "manifestPath": str(module_json_path),
        "backupPath": backup_path,
        "previousCompatibility": previous_compatibility,
        "newCompatibility": compatibility,
    }


def _create_foundry_style_backup(
    module: ModuleRecord,
    recommendation: Recommendation,
    target_dir: Path,
    modules_root: Path,
) -> str:
    backups_modules_root = _backups_modules_root(modules_root)
    module_backup_dir = backups_modules_root / module.module_id
    module_backup_dir.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    day_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backup_id = f"module.{module.module_id}.{day_stamp}.{now_ms}"
    bak_path = module_backup_dir / f"{backup_id}.bak"
    meta_path = module_backup_dir / f"{backup_id}.json"

    original_size = _directory_file_bytes(target_dir)
    with zipfile.ZipFile(bak_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(target_dir.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, arcname=str(path.relative_to(target_dir)))
    compressed_size = bak_path.stat().st_size

    manifest = dict(module.raw_manifest or {})
    metadata = {
        "id": backup_id,
        "title": manifest.get("title") or module.title or module.module_id,
        "description": manifest.get("description") or "",
        "compatibility": manifest.get("compatibility") or {},
        "version": manifest.get("version") or module.version or "",
        "relationships": manifest.get("relationships")
        or {"systems": [], "requires": [], "recommends": [], "conflicts": [], "flags": {}},
        "size": int(compressed_size),
        "note": f"resolver-{recommendation.installed_version or module.version or 'unknown'}",
        "snapshotId": None,
        "originalSize": int(original_size),
        "createdAt": now_ms,
        "packageId": module.module_id,
        "type": "module",
        "system": manifest.get("system"),
    }
    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")

    logging.info("Created Foundry-style backup for %s at %s", module.module_id, bak_path)
    return str(bak_path)


def _backups_modules_root(modules_root: Path) -> Path:
    # modules_root is expected to be <data-root>/Data/modules
    data_root = modules_root.parent.parent
    return data_root / "Backups" / "modules"


def _directory_file_bytes(path: Path) -> int:
    total = 0
    for node in path.rglob("*"):
        if not node.is_file():
            continue
        try:
            total += node.stat().st_size
        except OSError:
            continue
    return total


def _find_module_root(extract_root: Path, module_id: str) -> Path | None:
    direct_module_json = extract_root / "module.json"
    if direct_module_json.exists():
        return extract_root
    for module_json in extract_root.rglob("module.json"):
        try:
            candidate_root = module_json.parent
            if candidate_root.name == module_id:
                return candidate_root
        except OSError:
            continue
    roots = [path for path in extract_root.iterdir() if path.is_dir()]
    if len(roots) == 1:
        return roots[0]
    return None
