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
    return _apply_package_recommendation(
        package_kind="module",
        package=module,
        recommendation=recommendation,
        packages_dir=modules_dir,
        cache_dir=cache_dir,
    )


def apply_system_recommendation(
    system: ModuleRecord,
    recommendation: Recommendation,
    systems_dir: str,
    cache_dir: str,
) -> str | None:
    return _apply_package_recommendation(
        package_kind="system",
        package=system,
        recommendation=recommendation,
        packages_dir=systems_dir,
        cache_dir=cache_dir,
    )


def _apply_package_recommendation(
    package_kind: str,
    package: ModuleRecord,
    recommendation: Recommendation,
    packages_dir: str,
    cache_dir: str,
) -> str | None:
    if not recommendation.download_url:
        raise ValueError(f"No download URL is available for {recommendation.module} ({package_kind}).")
    manifest_name = "module.json" if package_kind == "module" else "system.json"
    archive_path = download_to_temp(recommendation.download_url)
    packages_root = Path(packages_dir)
    target_dir = packages_root / package.module_id
    backup_path: str | None = None
    if target_dir.exists() and target_dir.is_dir():
        backup_path = _create_foundry_style_backup(
            package_kind=package_kind,
            module=package,
            recommendation=recommendation,
            target_dir=target_dir,
            packages_root=packages_root,
        )

    try:
        with tempfile.TemporaryDirectory(prefix=f"{package.module_id}-{package_kind}-upgrade-") as temp_dir:
            temp_root = Path(temp_dir)
            _safe_extract_archive(archive_path, temp_root)

            extracted_dir = _find_package_root(temp_root, package.module_id, manifest_name)
            if extracted_dir is None:
                raise ValueError(f"Could not locate extracted {package_kind} root for {package.module_id} in {archive_path}.")
            _validate_extracted_package(package_kind, package, recommendation, extracted_dir)

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
    logging.info("Applied %s %s to %s", package_kind, package.module_id, target_dir)
    return backup_path


def force_module_compatibility(module: ModuleRecord, modules_dir: str, target_version: str) -> dict:
    modules_root = Path(modules_dir)
    target_dir = modules_root / module.module_id
    module_json_path = target_dir / "module.json"
    if not target_dir.exists() or not target_dir.is_dir():
        raise ValueError(f"Module directory not found for {module.module_id}: {target_dir}")
    if not module_json_path.exists():
        raise ValueError(f"module.json not found for {module.module_id}: {module_json_path}")

    with module_json_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    compatibility_raw = manifest.get("compatibility")
    compatibility: dict = dict(compatibility_raw) if isinstance(compatibility_raw, dict) else {}
    previous_compatibility = dict(compatibility)

    previous_minimum = str(previous_compatibility.get("minimum") or "").strip()
    previous_maximum = str(previous_compatibility.get("maximum") or "").strip()
    lockstep_bounds = bool(previous_minimum and previous_maximum and previous_minimum == previous_maximum)

    # Force compatibility adjusts minimum only; when min==max, keep that lockstep.
    compatibility["minimum"] = target_version
    if lockstep_bounds:
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
        "Forced compatibility applied for %s (minimum -> %s%s, verified preserved)",
        module.module_id,
        target_version,
        ", maximum updated (lockstep bounds)" if lockstep_bounds else "",
    )
    return {
        "module": module.module_id,
        "manifestPath": str(module_json_path),
        "backupPath": None,
        "previousCompatibility": previous_compatibility,
        "newCompatibility": compatibility,
    }


def _create_foundry_style_backup(
    package_kind: str,
    module: ModuleRecord,
    recommendation: Recommendation,
    target_dir: Path,
    packages_root: Path,
) -> str:
    backups_modules_root = _backups_packages_root(packages_root, package_kind)
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
        "type": "module" if package_kind == "module" else "system",
        "system": manifest.get("system"),
    }
    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")

    logging.info("Created Foundry-style backup for %s at %s", module.module_id, bak_path)
    return str(bak_path)


def _backups_modules_root(modules_root: Path) -> Path:
    return _backups_packages_root(modules_root, "module")


def _backups_packages_root(packages_root: Path, package_kind: str) -> Path:
    # packages_root is expected to be <data-root>/Data/modules or <data-root>/Data/systems
    data_root = packages_root.parent.parent
    folder = "modules" if package_kind == "module" else "systems"
    return data_root / "Backups" / folder


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
    return _find_package_root(extract_root, module_id, "module.json")


def _find_package_root(extract_root: Path, package_id: str, manifest_name: str) -> Path | None:
    direct_module_json = extract_root / "module.json"
    if manifest_name == "module.json" and direct_module_json.exists():
        return extract_root
    direct_manifest = extract_root / manifest_name
    if direct_manifest.exists():
        return extract_root
    for module_json in extract_root.rglob(manifest_name):
        try:
            candidate_root = module_json.parent
            if candidate_root.name == package_id:
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
    _validate_extracted_package("module", module, recommendation, extracted_dir)


def _validate_extracted_package(
    package_kind: str,
    module: ModuleRecord,
    recommendation: Recommendation,
    extracted_dir: Path,
) -> None:
    manifest_name = "module.json" if package_kind == "module" else "system.json"
    module_json_path = extracted_dir / manifest_name
    if not module_json_path.exists():
        raise ValueError(f"Package validation failed for {module.module_id}: {manifest_name} not found.")
    try:
        manifest = json.loads(module_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Package validation failed for {module.module_id}: invalid {manifest_name} ({exc}).") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Package validation failed for {module.module_id}: {manifest_name} root must be an object.")

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

    compatibility_raw = manifest.get("compatibility")
    compatibility: dict = dict(compatibility_raw) if isinstance(compatibility_raw, dict) else {}
    legacy_minimum = str(manifest.get("minimumCoreVersion") or "").strip()
    legacy_verified = str(manifest.get("compatibleCoreVersion") or "").strip()
    if legacy_minimum or legacy_verified:
        # Accept legacy compatibility fields from older packages (ex: lib-wrapper),
        # normalizing them into compatibility in-memory for validation purposes.
        if legacy_minimum and not str(compatibility.get("minimum") or "").strip():
            compatibility["minimum"] = legacy_minimum
        if legacy_verified and not str(compatibility.get("verified") or "").strip():
            compatibility["verified"] = legacy_verified
        logging.warning(
            "Package %s uses legacy core compatibility fields; normalized for validation.",
            module.module_id,
        )
    if not compatibility:
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
