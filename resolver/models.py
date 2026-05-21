from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModuleRelationship:
    module_id: str
    type: str
    compatibility: dict[str, Any]
    manifest_url: str | None = None


@dataclass
class ModuleRecord:
    module_id: str
    title: str
    version: str
    manifest_url: str | None
    project_url: str | None
    path: str
    raw_manifest: dict[str, Any] = field(repr=False)


@dataclass
class ReleaseRecord:
    version: str
    manifest_url: str | None
    compatibility: dict[str, Any]
    system_compatibility: dict[str, dict[str, Any]]
    module_requirements: list[ModuleRelationship]
    download_url: str | None
    source: str
    raw_manifest: dict[str, Any] = field(default_factory=dict, repr=False)
    published_at: str | None = None


@dataclass
class DependencyAction:
    module: str
    installed_version: str | None
    recommended_version: str | None
    reason: str
    manifest_url: str | None
    compatibility: dict[str, Any] = field(default_factory=dict)
    system_compatibility: dict[str, dict[str, Any]] = field(default_factory=dict)
    download_url: str | None = None


@dataclass
class Recommendation:
    module: str
    installed_version: str
    recommended_version: str
    reason: str
    confidence: str
    verified_version: str | None
    manifest_url: str | None
    download_url: str | None
    source: str
    checked_releases: int
    compatibility: dict[str, Any] = field(default_factory=dict)
    system_compatibility: dict[str, dict[str, Any]] = field(default_factory=dict)
    dependency_actions: list[DependencyAction] = field(default_factory=list)
    dependency_updates: list[DependencyAction] = field(default_factory=list)
    missing_dependencies: list[DependencyAction] = field(default_factory=list)
    release_published_at: str | None = None
    attention_flag: bool = False
    verified_recommended_version: str | None = None
    verified_download_url: str | None = None
    verified_manifest_url: str | None = None
    verified_compatibility: dict[str, Any] = field(default_factory=dict)
    verified_system_compatibility: dict[str, dict[str, Any]] = field(default_factory=dict)
