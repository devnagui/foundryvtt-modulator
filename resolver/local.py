from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import plyvel  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    plyvel = None

from .models import ModuleRecord


def modules_dir_from_data_root(data_root: str) -> Path:
    return Path(data_root) / "Data" / "modules"


def load_modules(modules_dir: str, module_filter: str | None = None) -> list[ModuleRecord]:
    root = Path(modules_dir)
    modules: list[ModuleRecord] = []
    for module_json in sorted(root.glob("*/module.json")):
        parent_name = module_json.parent.name
        if ".bak." in parent_name:
            continue
        with module_json.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        module_id = data.get("id") or module_json.parent.name
        if module_filter and module_id != module_filter:
            continue
        modules.append(
            ModuleRecord(
                module_id=module_id,
                title=data.get("title") or module_id,
                version=str(data.get("version", "")),
                manifest_url=data.get("manifest"),
                project_url=data.get("url"),
                path=str(module_json.parent),
                raw_manifest=data,
            )
        )
    return modules


def load_system_versions(data_root: str) -> dict[str, str]:
    systems_root = Path(data_root) / "Data" / "systems"
    versions: dict[str, str] = {}
    for system_json in sorted(systems_root.glob("*/system.json")):
        with system_json.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        system_id = data.get("id") or system_json.parent.name
        version = data.get("version")
        if system_id and version:
            versions[str(system_id)] = str(version)
    return versions


def load_system_records(data_root: str) -> list[ModuleRecord]:
    systems_root = Path(data_root) / "Data" / "systems"
    systems: list[ModuleRecord] = []
    for system_json in sorted(systems_root.glob("*/system.json")):
        with system_json.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        system_id = data.get("id") or system_json.parent.name
        systems.append(
            ModuleRecord(
                module_id=str(system_id),
                title=data.get("title") or str(system_id),
                version=str(data.get("version", "")),
                manifest_url=data.get("manifest"),
                project_url=data.get("url"),
                path=str(system_json.parent),
                raw_manifest=data,
            )
        )
    return systems


def load_world_usage(data_root: str) -> list[dict]:
    worlds_root = Path(data_root) / "Data" / "worlds"
    world_rows: list[dict] = []
    for world_json in sorted(worlds_root.glob("*/world.json")):
        with world_json.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        world_dir = world_json.parent
        enabled_modules, module_source, module_method = _load_world_enabled_modules(world_dir / "data" / "settings")
        world_rows.append(
            {
                "id": str(data.get("id") or world_dir.name),
                "title": data.get("title") or world_dir.name,
                "system": data.get("system"),
                "systemVersion": data.get("systemVersion"),
                "coreVersion": data.get("coreVersion"),
                "enabledModules": sorted(enabled_modules),
                "moduleConfigurationSource": module_source,
                "moduleConfigurationResolved": bool(enabled_modules),
                "moduleConfigurationMethod": module_method,
            }
        )
    return world_rows


def build_local_dependency_map(modules: list[ModuleRecord]) -> dict[str, dict[str, list[str]]]:
    direct_map: dict[str, list[str]] = {}
    for module in modules:
        relationships = module.raw_manifest.get("relationships") or {}
        requires = relationships.get("requires") or []
        direct_map[module.module_id] = sorted(
            {
                str(requirement.get("id"))
                for requirement in requires
                if requirement.get("type") == "module" and requirement.get("id")
            }
        )

    transitive_map: dict[str, list[str]] = {}
    for module_id in direct_map:
        visited: set[str] = set()
        _collect_transitive_dependencies(module_id, direct_map, visited)
        visited.discard(module_id)
        transitive_map[module_id] = sorted(visited)

    return {
        module_id: {
            "direct": direct_map[module_id],
            "transitive": transitive_map[module_id],
        }
        for module_id in sorted(direct_map)
    }


def _collect_transitive_dependencies(module_id: str, direct_map: dict[str, list[str]], visited: set[str]) -> None:
    for dependency_id in direct_map.get(module_id, []):
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        _collect_transitive_dependencies(dependency_id, direct_map, visited)


def _load_world_enabled_modules(settings_dir: Path) -> tuple[set[str], str | None, str]:
    if not settings_dir.exists():
        return set(), None, "missing-settings"
    if plyvel is not None:
        enabled_modules, source = _load_world_enabled_modules_from_leveldb(settings_dir)
        if enabled_modules:
            return enabled_modules, source, "plyvel"
    candidates = sorted(
        (entry for entry in settings_dir.iterdir() if entry.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        try:
            text = candidate.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        enabled = _extract_enabled_modules_from_text(text)
        if enabled:
            return enabled, str(candidate), "binary-scrape"
    return set(), None, "binary-scrape" if plyvel is None else "plyvel-fallback"


def _load_world_enabled_modules_from_leveldb(settings_dir: Path) -> tuple[set[str], str | None]:
    if plyvel is None:
        return set(), None
    try:
        db = plyvel.DB(str(settings_dir), create_if_missing=False)
    except Exception:
        return set(), None
    try:
        for key_bytes, value_bytes in db:
            key = key_bytes.decode("utf-8", errors="ignore")
            decoded = value_bytes.decode("utf-8", errors="ignore")
            enabled = _extract_enabled_modules_from_leveldb_value(decoded)
            if enabled:
                return enabled, f"{settings_dir}::{key}"
    finally:
        db.close()
    return set(), None


def _extract_enabled_modules_from_leveldb_value(value: str) -> set[str]:
    cleaned = value.strip()
    if not cleaned:
        return set()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    if payload.get("key") != "core.moduleConfiguration":
        return set()
    raw_value = payload.get("value")
    if not isinstance(raw_value, str) or not raw_value:
        return set()
    try:
        configuration = json.loads(raw_value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(configuration, dict):
        return set()
    return {str(module_id) for module_id, enabled in configuration.items() if enabled is True}


def _extract_enabled_modules_from_text(text: str) -> set[str]:
    marker = '"key":"core.moduleConfiguration","value":"'
    start = text.find(marker)
    if start == -1:
        return set()
    start += len(marker)
    value_parts: list[str] = []
    escaped = False
    for char in text[start:]:
        if escaped:
            value_parts.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            break
        value_parts.append(char)
    if not value_parts:
        return set()
    raw_value = "".join(value_parts)
    cleaned_value = raw_value.replace('\\"', '"')
    if cleaned_value.startswith("{") and cleaned_value.endswith("}"):
        try:
            payload = json.loads(cleaned_value)
            return {str(module_id) for module_id, enabled in payload.items() if enabled is True}
        except json.JSONDecodeError:
            pass

    enabled_modules: set[str] = set()
    for match in re.finditer(r'"([^"]+)":true', cleaned_value):
        enabled_modules.add(match.group(1))
    return enabled_modules
