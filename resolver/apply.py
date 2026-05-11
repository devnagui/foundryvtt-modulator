from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ModuleRecord, Recommendation
from .sources import delete_cached_zip, download_to_temp

MODULE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
            _safe_extract_archive(archive_path, temp_root)

            extracted_dir = _find_module_root(temp_root, module.module_id)
            if extracted_dir is None:
                raise ValueError(f"Could not locate extracted module root for {module.module_id} in {archive_path}.")
            _validate_extracted_module_package(module, recommendation, extracted_dir)

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


def _safe_extract_archive(archive_path: str, temp_root: Path) -> None:
    root = temp_root.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            name = str(member.filename or "")
            if not name:
                continue
            candidate = Path(name)
            if candidate.is_absolute() or ":" in name.split("/")[0]:
                raise ValueError(f"Unsafe archive entry path: {name}")
            resolved_target = (root / candidate).resolve()
            if resolved_target != root and not str(resolved_target).startswith(str(root) + os.sep):
                raise ValueError(f"Unsafe archive entry traversal detected: {name}")
        archive.extractall(root)


def _validate_extracted_module_package(module: ModuleRecord, recommendation: Recommendation, extracted_dir: Path) -> None:
    module_json_path = extracted_dir / "module.json"
    if not module_json_path.exists():
        raise ValueError(f"Package validation failed for {module.module_id}: module.json not found.")
    try:
        manifest = json.loads(module_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Package validation failed for {module.module_id}: invalid module.json ({exc}).") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Package validation failed for {module.module_id}: module.json root must be an object.")

    manifest_id = str(manifest.get("id") or "").strip()
    if not manifest_id or not MODULE_ID_RE.match(manifest_id):
        raise ValueError(f"Package validation failed for {module.module_id}: manifest id is invalid.")
    if manifest_id != module.module_id:
        raise ValueError(
            f"Package validation failed for {module.module_id}: manifest id mismatch ({manifest_id})."
        )

    version = str(manifest.get("version") or "").strip()
    if not version:
        raise ValueError(f"Package validation failed for {module.module_id}: manifest version is required.")
    expected_version = str(recommendation.recommended_version or "").strip()
    if expected_version and version != expected_version:
        raise ValueError(
            f"Package validation failed for {module.module_id}: manifest version {version} does not match recommended {expected_version}."
        )

    if manifest.get("minimumCoreVersion") is not None or manifest.get("compatibleCoreVersion") is not None:
        raise ValueError(
            f"Package validation failed for {module.module_id}: legacy core compatibility fields detected."
        )
    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, dict):
        raise ValueError(f"Package validation failed for {module.module_id}: compatibility object is required.")

    missing_files: list[str] = []
    for key in ("styles", "scripts", "esmodules"):
        values = manifest.get(key) or []
        if values is None:
            values = []
        if not isinstance(values, list):
            raise ValueError(f"Package validation failed for {module.module_id}: {key} must be an array.")
        for rel in values:
            rel_path = str(rel or "").strip()
            if not rel_path:
                continue
            file_path = extracted_dir / rel_path
            if not file_path.exists() or not file_path.is_file():
                missing_files.append(rel_path)
    if missing_files:
        raise ValueError(
            f"Package validation failed for {module.module_id}: declared files missing ({', '.join(sorted(set(missing_files)))})."
        )
