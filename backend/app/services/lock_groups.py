from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .core import ServiceConfig


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LockGroupStore:
    """Manages version lock groups persisted in state/version-lock-groups.json."""

    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        self._path = config.state_dir / "version-lock-groups.json"
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        payload = self._read()
        groups = payload.get("groups")
        if not isinstance(groups, list):
            return []
        return [g for g in groups if isinstance(g, dict) and g.get("id")]

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        for g in self.list_groups():
            if g.get("id") == group_id:
                return g
        return None

    def create_group(self, data: dict[str, Any]) -> dict[str, Any]:
        group = self._normalize_group(data)
        group["id"] = str(uuid.uuid4())
        now = _utc_now_iso()
        group["createdAt"] = now
        group["updatedAt"] = now
        with self._lock:
            payload = self._read()
            groups = payload.get("groups")
            if not isinstance(groups, list):
                groups = []
            groups.append(group)
            payload["groups"] = groups
            self._write(payload)
        return group

    def update_group(self, group_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._read()
            groups = payload.get("groups")
            if not isinstance(groups, list):
                raise ValueError("group_not_found")
            idx = next((i for i, g in enumerate(groups) if isinstance(g, dict) and g.get("id") == group_id), None)
            if idx is None:
                raise ValueError("group_not_found")
            updated = self._normalize_group(data)
            updated["id"] = group_id
            updated["createdAt"] = groups[idx].get("createdAt", _utc_now_iso())
            updated["updatedAt"] = _utc_now_iso()
            groups[idx] = updated
            payload["groups"] = groups
            self._write(payload)
        return updated

    def delete_group(self, group_id: str) -> bool:
        with self._lock:
            payload = self._read()
            groups = payload.get("groups")
            if not isinstance(groups, list):
                return False
            before = len(groups)
            groups = [g for g in groups if not (isinstance(g, dict) and g.get("id") == group_id)]
            if len(groups) == before:
                return False
            payload["groups"] = groups
            self._write(payload)
        return True

    # -- helpers -----------------------------------------------------------

    def _normalize_group(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("name_required")
        foundry_version = str(data.get("foundryVersion") or "").strip()
        entries_raw = data.get("entries")
        if not isinstance(entries_raw, list):
            entries_raw = []
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries_raw:
            if not isinstance(entry, dict):
                continue
            package_id = str(entry.get("packageId") or "").strip()
            if not package_id or package_id in seen:
                continue
            seen.add(package_id)
            kind = str(entry.get("packageKind") or "module").strip()
            if kind not in ("module", "system"):
                kind = "module"
            version = str(entry.get("version") or "").strip()
            if not version:
                continue
            entries.append({
                "packageId": package_id,
                "packageKind": kind,
                "version": version,
                "verified": bool(entry.get("verified", False)),
                "required": bool(entry.get("required", True)),
                "notes": str(entry.get("notes") or "").strip(),
            })
        return {
            "name": name,
            "foundryVersion": foundry_version,
            "entries": entries,
        }

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: dict[str, Any]) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
