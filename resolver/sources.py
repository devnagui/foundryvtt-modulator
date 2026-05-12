from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone

from .models import ModuleRecord, ModuleRelationship, ReleaseRecord
from .versioning import compare_versions, parse_version


USER_AGENT = "foundry-module-version-resolver/0.1"
DEFAULT_CACHE_DIR = str(Path(__file__).resolve().parent.parent / ".cache")
MODULE_CACHE_VERSION = 3
FOUNDRY_RELEASE_CATALOG_VERSION = 1
FOUNDRY_RELEASE_CATALOG_TTL_HOURS = 6
DEFAULT_CACHE_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_CACHE_MAX_FILES = 5000
DEFAULT_CACHE_MAX_AGE_DAYS = 30
STALE_CACHE_WARNING_PREFIX = "Used stale release cache"

_CACHE_MAX_BYTES = DEFAULT_CACHE_MAX_BYTES
_CACHE_MAX_FILES = DEFAULT_CACHE_MAX_FILES
_CACHE_MAX_AGE_DAYS = DEFAULT_CACHE_MAX_AGE_DAYS


def fetch_release_history(
    module: ModuleRecord,
    per_page: int = 10,
    cache_dir: str = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    newer_than_version: str | None = None,
) -> tuple[list[ReleaseRecord], list[str]]:
    stale_cache_candidate: tuple[list[ReleaseRecord], list[str], datetime] | None = None
    cached_releases: list[ReleaseRecord] = []
    cached_version_keys: set[tuple[str, Any]] = set()
    if force_refresh:
        stale_cache_candidate = _load_latest_package_release_cache(
            module,
            per_page,
            cache_dir,
            package_kind="modules",
            strict_fingerprint=False,
        )
    if not force_refresh:
        cached = _load_module_release_cache(module, per_page, cache_dir)
        if cached is not None:
            return cached
    if stale_cache_candidate is None:
        stale_cache_candidate = _load_latest_package_release_cache(
            module,
            per_page,
            cache_dir,
            package_kind="modules",
            strict_fingerprint=True,
        )
    if stale_cache_candidate is not None:
        cached_releases = list(stale_cache_candidate[0])
        cached_version_keys = _release_version_keys(cached_releases)

    warnings: list[str] = []
    releases: list[ReleaseRecord] | None = None
    if module.project_url:
        if "gitlab.com" in module.project_url:
            releases, source_warnings, cache_sync_hit = _fetch_gitlab_tags(
                module.project_url,
                per_page=per_page,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
                cached_version_keys=cached_version_keys,
            )
            warnings.extend(source_warnings)
            if cache_sync_hit and cached_releases:
                fresh_count = len(releases)
                releases = _merge_releases_with_cache(releases, cached_releases, per_page)
                logging.info(
                    "Cache sync reached for module %s (gitlab-tags): fetched %s fresh releases and reused %s cached releases.",
                    module.module_id,
                    fresh_count,
                    max(len(releases) - fresh_count, 0),
                )
            if releases and newer_than_version:
                filtered_releases = [
                    item for item in releases
                    if compare_versions(item.version, newer_than_version) > 0
                ]
                if filtered_releases:
                    releases = filtered_releases
            if releases:
                _store_module_release_cache(module, per_page, cache_dir, releases, warnings)
                return releases, warnings
        if "github.com" in module.project_url:
            releases, source_warnings, cache_sync_hit = _fetch_github_releases(
                module.project_url,
                per_page=per_page,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
                cached_version_keys=cached_version_keys,
            )
            warnings.extend(source_warnings)
            if cache_sync_hit and cached_releases:
                fresh_count = len(releases)
                releases = _merge_releases_with_cache(releases, cached_releases, per_page)
                logging.info(
                    "Cache sync reached for module %s (github-releases): fetched %s fresh releases and reused %s cached releases.",
                    module.module_id,
                    fresh_count,
                    max(len(releases) - fresh_count, 0),
                )
            if releases and newer_than_version:
                filtered_releases = [
                    item for item in releases
                    if compare_versions(item.version, newer_than_version) > 0
                ]
                if filtered_releases:
                    releases = filtered_releases
            if releases:
                _store_module_release_cache(module, per_page, cache_dir, releases, warnings)
                return releases, warnings

    stale_fallback = _resolve_stale_cache_fallback(
        module,
        per_page,
        cache_dir,
        package_kind="modules",
        refresh_warnings=warnings,
        cached=stale_cache_candidate,
    )
    if stale_fallback is not None:
        return stale_fallback

    warnings.append("Falling back to local manifest only.")
    releases = [
        ReleaseRecord(
            version=module.version,
            manifest_url=module.manifest_url,
            compatibility=module.raw_manifest.get("compatibility") or {},
            system_compatibility=_extract_system_compatibility(module.raw_manifest),
            module_requirements=_extract_module_requirements(module.raw_manifest),
            download_url=module.raw_manifest.get("download"),
            source="local-manifest",
            raw_manifest=module.raw_manifest,
            published_at=None,
        )
    ]
    if not _should_skip_release_cache_write(releases, warnings):
        _store_module_release_cache(module, per_page, cache_dir, releases, warnings)
    return releases, warnings


def fetch_system_release_history(
    system: ModuleRecord,
    per_page: int = 10,
    cache_dir: str = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> tuple[list[ReleaseRecord], list[str]]:
    stale_cache_candidate: tuple[list[ReleaseRecord], list[str], datetime] | None = None
    cached_releases: list[ReleaseRecord] = []
    cached_version_keys: set[tuple[str, Any]] = set()
    if force_refresh:
        stale_cache_candidate = _load_latest_package_release_cache(
            system,
            per_page,
            cache_dir,
            package_kind="systems",
            strict_fingerprint=False,
        )
    if not force_refresh:
        cached = _load_package_release_cache(system, per_page, cache_dir, package_kind="systems")
        if cached is not None:
            return cached
    if stale_cache_candidate is None:
        stale_cache_candidate = _load_latest_package_release_cache(
            system,
            per_page,
            cache_dir,
            package_kind="systems",
            strict_fingerprint=True,
        )
    if stale_cache_candidate is not None:
        cached_releases = list(stale_cache_candidate[0])
        cached_version_keys = _release_version_keys(cached_releases)

    warnings: list[str] = []
    releases: list[ReleaseRecord] | None = None
    if system.project_url:
        if "gitlab.com" in system.project_url:
            releases, source_warnings, cache_sync_hit = _fetch_gitlab_tags(
                system.project_url,
                per_page=per_page,
                cache_dir=cache_dir,
                manifest_names=("system.json", "package/system.json"),
                force_refresh=force_refresh,
                cached_version_keys=cached_version_keys,
            )
            warnings.extend(source_warnings)
            if cache_sync_hit and cached_releases:
                fresh_count = len(releases)
                releases = _merge_releases_with_cache(releases, cached_releases, per_page)
                logging.info(
                    "Cache sync reached for system %s (gitlab-tags): fetched %s fresh releases and reused %s cached releases.",
                    system.module_id,
                    fresh_count,
                    max(len(releases) - fresh_count, 0),
                )
            if releases:
                _store_package_release_cache(system, per_page, cache_dir, releases, warnings, package_kind="systems")
                return releases, warnings
        if "github.com" in system.project_url:
            releases, source_warnings, cache_sync_hit = _fetch_github_releases(
                system.project_url,
                per_page=per_page,
                cache_dir=cache_dir,
                manifest_names=("system.json", "package/system.json"),
                force_refresh=force_refresh,
                cached_version_keys=cached_version_keys,
            )
            warnings.extend(source_warnings)
            if cache_sync_hit and cached_releases:
                fresh_count = len(releases)
                releases = _merge_releases_with_cache(releases, cached_releases, per_page)
                logging.info(
                    "Cache sync reached for system %s (github-releases): fetched %s fresh releases and reused %s cached releases.",
                    system.module_id,
                    fresh_count,
                    max(len(releases) - fresh_count, 0),
                )
            if releases:
                _store_package_release_cache(system, per_page, cache_dir, releases, warnings, package_kind="systems")
                return releases, warnings
            releases, source_warnings, cache_sync_hit = _fetch_github_tags(
                system.project_url,
                per_page=per_page,
                cache_dir=cache_dir,
                manifest_names=("system.json", "package/system.json"),
                force_refresh=force_refresh,
                cached_version_keys=cached_version_keys,
            )
            warnings.extend(source_warnings)
            if cache_sync_hit and cached_releases:
                fresh_count = len(releases)
                releases = _merge_releases_with_cache(releases, cached_releases, per_page)
                logging.info(
                    "Cache sync reached for system %s (github-tags): fetched %s fresh releases and reused %s cached releases.",
                    system.module_id,
                    fresh_count,
                    max(len(releases) - fresh_count, 0),
                )
            if releases:
                _store_package_release_cache(system, per_page, cache_dir, releases, warnings, package_kind="systems")
                return releases, warnings

    stale_fallback = _resolve_stale_cache_fallback(
        system,
        per_page,
        cache_dir,
        package_kind="systems",
        refresh_warnings=warnings,
        cached=stale_cache_candidate,
    )
    if stale_fallback is not None:
        return stale_fallback

    warnings.append("Falling back to local manifest only.")
    releases = [
        ReleaseRecord(
            version=system.version,
            manifest_url=system.manifest_url,
            compatibility=system.raw_manifest.get("compatibility") or {},
            system_compatibility={},
            module_requirements=[],
            download_url=system.raw_manifest.get("download"),
            source="local-manifest",
            raw_manifest=system.raw_manifest,
            published_at=None,
        )
    ]
    if not _should_skip_release_cache_write(releases, warnings):
        _store_package_release_cache(system, per_page, cache_dir, releases, warnings, package_kind="systems")
    return releases, warnings


def configure_cache_limits(
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
    max_files: int = DEFAULT_CACHE_MAX_FILES,
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
) -> None:
    global _CACHE_MAX_BYTES, _CACHE_MAX_FILES, _CACHE_MAX_AGE_DAYS
    _CACHE_MAX_BYTES = max(1, int(max_bytes))
    _CACHE_MAX_FILES = max(1, int(max_files))
    _CACHE_MAX_AGE_DAYS = max(1, int(max_age_days))


def enforce_cache_limits(cache_dir: str = DEFAULT_CACHE_DIR) -> dict[str, int]:
    root = Path(cache_dir)
    if not root.exists():
        return {"removedFiles": 0, "removedBytes": 0}

    files = [entry for entry in root.rglob("*") if entry.is_file()]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_CACHE_MAX_AGE_DAYS)
    removed_files = 0
    removed_bytes = 0

    def _safe_stat(path: Path):
        try:
            return path.stat()
        except FileNotFoundError:
            return None

    for entry in files:
        stat = _safe_stat(entry)
        if stat is None:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if modified < cutoff:
            removed_bytes += stat.st_size
            entry.unlink(missing_ok=True)
            removed_files += 1

    files = [entry for entry in root.rglob("*") if entry.is_file()]
    def _mtime_or_inf(path: Path) -> float:
        stat = _safe_stat(path)
        if stat is None:
            return float("inf")
        return float(stat.st_mtime)
    files.sort(key=_mtime_or_inf)
    total_bytes = 0
    for entry in files:
        stat = _safe_stat(entry)
        if stat is None:
            continue
        total_bytes += stat.st_size
    while files and (total_bytes > _CACHE_MAX_BYTES or len(files) > _CACHE_MAX_FILES):
        entry = files.pop(0)
        stat = _safe_stat(entry)
        if stat is None:
            continue
        size = stat.st_size
        entry.unlink(missing_ok=True)
        total_bytes -= size
        removed_files += 1
        removed_bytes += size

    for directory in sorted((entry for entry in root.rglob("*") if entry.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue

    return {"removedFiles": removed_files, "removedBytes": removed_bytes}


def describe_cache_status(cache_dir: str = DEFAULT_CACHE_DIR, stale_after_days: int = DEFAULT_CACHE_MAX_AGE_DAYS) -> dict[str, Any]:
    root = Path(cache_dir)
    now = datetime.now(timezone.utc)
    status: dict[str, Any] = {
        "path": str(root),
        "fileCount": 0,
        "totalBytes": 0,
        "oldestAt": None,
        "newestAt": None,
        "newestAgeSeconds": None,
        "staleAfterDays": int(stale_after_days),
        "isStale": True,
    }
    if not root.exists():
        return status

    files = [entry for entry in root.rglob("*") if entry.is_file()]
    if not files:
        return status

    status["fileCount"] = len(files)
    status["totalBytes"] = sum(entry.stat().st_size for entry in files)
    mtimes = [datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc) for entry in files]
    oldest = min(mtimes)
    newest = max(mtimes)
    newest_age_seconds = max(int((now - newest).total_seconds()), 0)
    status["oldestAt"] = oldest.isoformat()
    status["newestAt"] = newest.isoformat()
    status["newestAgeSeconds"] = newest_age_seconds
    status["isStale"] = newest_age_seconds > max(1, int(stale_after_days)) * 86400
    return status


def fetch_foundry_release_catalog(
    cache_dir: str = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    cache_path = Path(cache_dir) / "foundry" / "releases-index.json"
    cached = None if force_refresh else _load_foundry_release_catalog_cache(cache_path)
    if cached is not None:
        return cached

    request = Request("https://foundryvtt.com/releases/", headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="ignore")

    releases = _parse_foundry_release_catalog(html)
    payload = {
        "schemaVersion": FOUNDRY_RELEASE_CATALOG_VERSION,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "releases": releases,
    }
    _write_text_cache(cache_path, json.dumps(payload, indent=2, sort_keys=True), cache_dir)
    return releases


def list_future_foundry_releases(
    current_version: str,
    cache_dir: str = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    releases = fetch_foundry_release_catalog(cache_dir=cache_dir, force_refresh=force_refresh)
    future_releases = [
        release for release in releases if compare_versions(release.get("version"), current_version) > 0
    ]
    future_releases.sort(key=lambda item: parse_version(item.get("version")), reverse=True)
    return future_releases


def _fetch_gitlab_tags(
    project_url: str,
    per_page: int,
    cache_dir: str,
    manifest_names: tuple[str, ...] = ("module.json", "package/module.json"),
    force_refresh: bool = False,
    cached_version_keys: set[tuple[str, Any]] | None = None,
) -> tuple[list[ReleaseRecord], list[str], bool]:
    warnings: list[str] = []
    cache_sync_hit = False
    parsed = urlparse(project_url)
    path = parsed.path.strip("/")
    if not path:
        return [], ["GitLab project URL is missing a repository path."], False
    api_url = f"https://gitlab.com/api/v4/projects/{quote(path, safe='')}/repository/tags?per_page={per_page}"
    try:
        tags = _get_json(api_url, cache_dir=cache_dir, force_refresh=force_refresh)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], [f"GitLab lookup failed: {exc}"], False

    releases: list[ReleaseRecord] = []
    preferred_manifest_paths: list[str] = list(manifest_names)
    for tag in tags:
        tag_name = tag.get("name")
        if not tag_name:
            continue
        if cached_version_keys and _version_lookup_key(tag_name) in cached_version_keys:
            cache_sync_hit = True
            break
        manifest_url, manifest, used_manifest_path = _resolve_gitlab_manifest(
            path,
            tag_name,
            tuple(preferred_manifest_paths),
            cache_dir,
            force_refresh=force_refresh,
        )
        if not manifest or not manifest_url:
            continue
        if used_manifest_path and used_manifest_path not in preferred_manifest_paths:
            preferred_manifest_paths.insert(0, used_manifest_path)
        releases.append(
            ReleaseRecord(
                version=str(manifest.get("version") or tag_name),
                manifest_url=manifest_url,
                compatibility=manifest.get("compatibility") or {},
                system_compatibility=_extract_system_compatibility(manifest),
                module_requirements=_extract_module_requirements(manifest),
                download_url=manifest.get("download"),
                source="gitlab-tags",
                raw_manifest=manifest,
                published_at=((tag.get("commit") or {}).get("committed_date") if isinstance(tag, dict) else None),
            )
        )
    if not releases and not cache_sync_hit:
        warnings.append("GitLab tags were found, but no manifest could be read from tagged package/module.json files.")
    return releases, warnings, cache_sync_hit


def _fetch_github_releases(
    project_url: str,
    per_page: int,
    cache_dir: str,
    manifest_names: tuple[str, ...] = ("module.json", "package/module.json"),
    force_refresh: bool = False,
    cached_version_keys: set[tuple[str, Any]] | None = None,
) -> tuple[list[ReleaseRecord], list[str], bool]:
    warnings: list[str] = []
    cache_sync_hit = False
    parsed = urlparse(project_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return [], ["GitHub project URL is missing owner/repo."], False
    owner, repo = parts[0], parts[1]
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page={per_page}"
    try:
        releases_json = _get_json(api_url, cache_dir=cache_dir, force_refresh=force_refresh)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], [f"GitHub release lookup failed: {exc}"], False

    releases: list[ReleaseRecord] = []
    preferred_manifest_paths: list[str] = list(manifest_names)
    for release in releases_json:
        tag_name = release.get("tag_name")
        if not tag_name:
            continue
        if cached_version_keys and _version_lookup_key(tag_name) in cached_version_keys:
            cache_sync_hit = True
            break
        manifest_url, manifest, used_manifest_path = _resolve_github_manifest(
            owner,
            repo,
            tag_name,
            release,
            tuple(preferred_manifest_paths),
            cache_dir,
            force_refresh=force_refresh,
        )
        if not manifest or not manifest_url:
            continue
        if used_manifest_path and used_manifest_path not in preferred_manifest_paths:
            preferred_manifest_paths.insert(0, used_manifest_path)
        releases.append(
            ReleaseRecord(
                version=str(manifest.get("version") or tag_name),
                manifest_url=manifest_url,
                compatibility=manifest.get("compatibility") or {},
                system_compatibility=_extract_system_compatibility(manifest),
                module_requirements=_extract_module_requirements(manifest),
                download_url=manifest.get("download"),
                source="github-releases",
                raw_manifest=manifest,
                published_at=release.get("published_at") if isinstance(release, dict) else None,
            )
        )
    if not releases and not cache_sync_hit:
        warnings.append("GitHub releases were found, but no compatible manifest layout was discovered.")
    return releases, warnings, cache_sync_hit


def _fetch_github_tags(
    project_url: str,
    per_page: int,
    cache_dir: str,
    manifest_names: tuple[str, ...],
    force_refresh: bool = False,
    cached_version_keys: set[tuple[str, Any]] | None = None,
) -> tuple[list[ReleaseRecord], list[str], bool]:
    warnings: list[str] = []
    cache_sync_hit = False
    parsed = urlparse(project_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return [], ["GitHub project URL is missing owner/repo."], False
    owner, repo = parts[0], parts[1]
    api_url = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page={per_page}"
    try:
        tags_json = _get_json(api_url, cache_dir=cache_dir, force_refresh=force_refresh)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], [f"GitHub tag lookup failed: {exc}"], False

    releases: list[ReleaseRecord] = []
    preferred_manifest_paths: list[str] = list(manifest_names)
    for tag in tags_json:
        tag_name = tag.get("name")
        if not tag_name:
            continue
        if cached_version_keys and _version_lookup_key(tag_name) in cached_version_keys:
            cache_sync_hit = True
            break
        manifest_url, manifest, used_manifest_path = _resolve_github_manifest(
            owner,
            repo,
            tag_name,
            {},
            tuple(preferred_manifest_paths),
            cache_dir,
            force_refresh=force_refresh,
        )
        if not manifest or not manifest_url:
            continue
        if used_manifest_path and used_manifest_path not in preferred_manifest_paths:
            preferred_manifest_paths.insert(0, used_manifest_path)
        releases.append(
            ReleaseRecord(
                version=str(manifest.get("version") or tag_name),
                manifest_url=manifest_url,
                compatibility=manifest.get("compatibility") or {},
                system_compatibility=_extract_system_compatibility(manifest),
                module_requirements=_extract_module_requirements(manifest),
                download_url=manifest.get("download"),
                source="github-tags",
                raw_manifest=manifest,
                published_at=None,
            )
        )
    if not releases and not cache_sync_hit:
        warnings.append("GitHub tags were found, but no compatible manifest layout was discovered.")
    return releases, warnings, cache_sync_hit


def _version_lookup_key(value: str | int | float | None) -> tuple[str, Any]:
    raw_text = str(value or "").strip().lower()
    parsed = parse_version(raw_text)
    if parsed:
        return ("parsed", parsed)
    return ("raw", raw_text.lstrip("v"))


def _release_version_keys(releases: list[ReleaseRecord]) -> set[tuple[str, Any]]:
    return {_version_lookup_key(release.version) for release in releases if str(release.version or "").strip()}


def _merge_releases_with_cache(
    fresh_releases: list[ReleaseRecord],
    cached_releases: list[ReleaseRecord],
    release_limit: int,
) -> list[ReleaseRecord]:
    limit = max(int(release_limit), 0)
    if limit == 0:
        return []
    merged: list[ReleaseRecord] = list(fresh_releases)
    seen = _release_version_keys(merged)
    if len(merged) >= limit:
        return merged[:limit]
    for release in cached_releases:
        key = _version_lookup_key(release.version)
        if key in seen:
            continue
        merged.append(release)
        seen.add(key)
        if len(merged) >= limit:
            break
    return merged[:limit]


def _safe_get_json(url: str | None, cache_dir: str, force_refresh: bool = False) -> dict[str, Any] | None:
    if not url:
        return None
    try:
        return _get_json(url, cache_dir=cache_dir, force_refresh=force_refresh)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def _get_json(url: str, cache_dir: str = DEFAULT_CACHE_DIR, force_refresh: bool = False) -> Any:
    cache_path = _cache_path(url, cache_dir, suffix=".json")
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    request = Request(url, headers=_request_headers(url))
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    _write_text_cache(cache_path, json.dumps(payload), cache_dir)
    return payload


def download_to_cache(url: str, cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or ".bin"
    cache_path = _cache_path(url, cache_dir, suffix=suffix)
    if cache_path.exists():
        return str(cache_path)
    request = Request(url, headers=_request_headers(url))
    with urlopen(request, timeout=30) as response:
        payload = response.read()
    _write_bytes_cache(cache_path, payload, cache_dir)
    return str(cache_path)


def download_to_temp(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or ".bin"
    request = Request(url, headers=_request_headers(url))
    with urlopen(request, timeout=30) as response:
        with tempfile.NamedTemporaryFile(prefix="resolver-download-", suffix=suffix, delete=False) as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
            return handle.name


def delete_cached_zip(url: str, cache_dir: str = DEFAULT_CACHE_DIR) -> bool:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix != ".zip":
        return False
    cache_root = Path(cache_dir).resolve()
    cache_path = _cache_path(url, cache_dir, suffix=suffix)
    try:
        resolved = cache_path.resolve()
    except OSError:
        return False
    if cache_root not in resolved.parents:
        return False
    if not resolved.exists() or not resolved.is_file():
        return False
    resolved.unlink(missing_ok=True)
    return True


def _extract_system_compatibility(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    relationships = manifest.get("relationships") or {}
    systems = relationships.get("systems") or []
    compatibility_by_system: dict[str, dict[str, Any]] = {}
    for system in systems:
        system_id = system.get("id")
        if not system_id:
            continue
        compatibility_by_system[str(system_id)] = system.get("compatibility") or {}
    return compatibility_by_system


def _extract_module_requirements(manifest: dict[str, Any]) -> list[ModuleRelationship]:
    relationships = manifest.get("relationships") or {}
    requires = relationships.get("requires") or []
    requirements: list[ModuleRelationship] = []
    for requirement in requires:
        requirement_id = requirement.get("id")
        requirement_type = requirement.get("type")
        if not requirement_id or requirement_type != "module":
            continue
        requirements.append(
            ModuleRelationship(
                module_id=str(requirement_id),
                type=str(requirement_type),
                compatibility=requirement.get("compatibility") or {},
                manifest_url=requirement.get("manifest"),
            )
        )
    return requirements


def _github_release_asset_url(release: dict[str, Any], asset_name: str) -> str | None:
    for asset in release.get("assets") or []:
        if asset.get("name") == asset_name:
            return asset.get("browser_download_url")
    return None


def _resolve_gitlab_manifest(
    path: str,
    tag_name: str,
    manifest_names: tuple[str, ...],
    cache_dir: str,
    force_refresh: bool = False,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    for manifest_name in manifest_names:
        manifest_url = f"https://gitlab.com/{path}/-/raw/{tag_name}/{manifest_name}"
        manifest = _safe_get_json(manifest_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if manifest:
            return manifest_url, manifest, manifest_name
    discovered_manifest_path = _discover_gitlab_manifest_path(
        path,
        tag_name,
        manifest_names,
        cache_dir,
        force_refresh=force_refresh,
    )
    if discovered_manifest_path:
        manifest_url = f"https://gitlab.com/{path}/-/raw/{tag_name}/{discovered_manifest_path}"
        manifest = _safe_get_json(manifest_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if manifest:
            return manifest_url, manifest, discovered_manifest_path
    return None, None, None


def _discover_gitlab_manifest_path(
    path: str,
    tag_name: str,
    manifest_names: tuple[str, ...],
    cache_dir: str,
    force_refresh: bool = False,
) -> str | None:
    project_id = quote(path, safe="")
    ref = quote(tag_name, safe="")
    preferred_exact = list(manifest_names)
    preferred_names = {Path(name).name for name in manifest_names}
    candidates: list[str] = []
    page = 1
    while True:
        tree_url = (
            f"https://gitlab.com/api/v4/projects/{project_id}/repository/tree"
            f"?ref={ref}&recursive=true&per_page=100&page={page}"
        )
        tree_payload = _safe_get_json(tree_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if not isinstance(tree_payload, list) or not tree_payload:
            break
        for entry in tree_payload:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("type") or "") != "blob":
                continue
            entry_path = str(entry.get("path") or "")
            if not entry_path:
                continue
            if Path(entry_path).name not in preferred_names:
                continue
            candidates.append(entry_path)
        if len(tree_payload) < 100:
            break
        page += 1
        if page > 20:
            break
    if not candidates:
        return None

    def _score(candidate: str) -> tuple[int, int, int, str]:
        exact_rank = preferred_exact.index(candidate) if candidate in preferred_exact else 999
        base_name_rank = 0 if Path(candidate).name in ("module.json", "system.json") else 1
        depth = candidate.count("/")
        return (exact_rank, base_name_rank, depth, candidate.lower())

    candidates.sort(key=_score)
    return candidates[0]


def _resolve_github_manifest(
    owner: str,
    repo: str,
    tag_name: str,
    release: dict[str, Any],
    manifest_names: tuple[str, ...],
    cache_dir: str,
    force_refresh: bool = False,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    for manifest_name in manifest_names:
        asset_name = Path(manifest_name).name
        manifest_url = _github_release_asset_url(release, asset_name)
        manifest = _safe_get_json(manifest_url, cache_dir=cache_dir, force_refresh=force_refresh) if manifest_url else None
        if manifest:
            return manifest_url, manifest, manifest_name
    for manifest_name in manifest_names:
        manifest_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{tag_name}/{manifest_name}"
        manifest = _safe_get_json(manifest_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if manifest:
            return manifest_url, manifest, manifest_name

    discovered_manifest_path = _discover_github_manifest_path(
        owner,
        repo,
        tag_name,
        manifest_names,
        cache_dir,
        force_refresh=force_refresh,
    )
    if discovered_manifest_path:
        manifest_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{tag_name}/{discovered_manifest_path}"
        manifest = _safe_get_json(manifest_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if manifest:
            return manifest_url, manifest, discovered_manifest_path
    return None, None, None


def _discover_github_manifest_path(
    owner: str,
    repo: str,
    tag_name: str,
    manifest_names: tuple[str, ...],
    cache_dir: str,
    force_refresh: bool = False,
) -> str | None:
    commit_sha = _resolve_github_tag_commit_sha(owner, repo, tag_name, cache_dir, force_refresh=force_refresh)
    if not commit_sha:
        return None
    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1"
    tree_payload = _safe_get_json(tree_url, cache_dir=cache_dir, force_refresh=force_refresh)
    if not isinstance(tree_payload, dict):
        return None
    tree_entries = tree_payload.get("tree")
    if not isinstance(tree_entries, list):
        return None

    preferred_exact = list(manifest_names)
    preferred_names = {Path(name).name for name in manifest_names}

    candidates: list[str] = []
    for entry in tree_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        if not path:
            continue
        if Path(path).name not in preferred_names:
            continue
        candidates.append(path)
    if not candidates:
        return None

    def _score(path: str) -> tuple[int, int, int, str]:
        exact_rank = preferred_exact.index(path) if path in preferred_exact else 999
        base_name_rank = 0 if Path(path).name in ("module.json", "system.json") else 1
        depth = path.count("/")
        return (exact_rank, base_name_rank, depth, path.lower())

    candidates.sort(key=_score)
    return candidates[0]


def _resolve_github_tag_commit_sha(
    owner: str,
    repo: str,
    tag_name: str,
    cache_dir: str,
    force_refresh: bool = False,
) -> str | None:
    # Works for lightweight tags and most branch-like refs.
    commit_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{tag_name}"
    commit_payload = _safe_get_json(commit_url, cache_dir=cache_dir, force_refresh=force_refresh)
    if isinstance(commit_payload, dict):
        sha = str(commit_payload.get("sha") or "").strip()
        if sha:
            return sha

    # Fallback for annotated tags.
    ref_url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{quote(tag_name, safe='')}"
    ref_payload = _safe_get_json(ref_url, cache_dir=cache_dir, force_refresh=force_refresh)
    if not isinstance(ref_payload, dict):
        return None
    ref_object = ref_payload.get("object") or {}
    ref_type = str(ref_object.get("type") or "")
    ref_sha = str(ref_object.get("sha") or "").strip()
    if not ref_sha:
        return None
    if ref_type == "commit":
        return ref_sha
    if ref_type == "tag":
        tag_url = f"https://api.github.com/repos/{owner}/{repo}/git/tags/{ref_sha}"
        tag_payload = _safe_get_json(tag_url, cache_dir=cache_dir, force_refresh=force_refresh)
        if not isinstance(tag_payload, dict):
            return None
        tag_object = tag_payload.get("object") or {}
        if str(tag_object.get("type") or "") == "commit":
            commit_sha = str(tag_object.get("sha") or "").strip()
            if commit_sha:
                return commit_sha
    return None


def _cache_path(url: str, cache_dir: str, suffix: str) -> Path:
    digest = sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / digest[:2] / f"{digest}{suffix}"


def _request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN")
    hostname = urlparse(url).hostname or ""
    if token and ("github.com" in hostname or "api.github.com" in hostname or "raw.githubusercontent.com" in hostname):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _load_module_release_cache(
    module: ModuleRecord,
    per_page: int,
    cache_dir: str,
) -> tuple[list[ReleaseRecord], list[str]] | None:
    cache_path = _module_cache_path(module, per_page, cache_dir)
    loaded = _load_package_release_cache_from_path(
        cache_path,
        module,
        strict_fingerprint=True,
        allow_remote_lookup_fallback=False,
    )
    if loaded is None:
        return None
    releases, warnings, _ = loaded
    return releases, warnings


def _load_package_release_cache(
    package: ModuleRecord,
    per_page: int,
    cache_dir: str,
    package_kind: str,
) -> tuple[list[ReleaseRecord], list[str]] | None:
    cache_path = _package_cache_path(package, per_page, cache_dir, package_kind)
    loaded = _load_package_release_cache_from_path(
        cache_path,
        package,
        strict_fingerprint=True,
        allow_remote_lookup_fallback=False,
    )
    if loaded is None:
        return None
    releases, warnings, _ = loaded
    return releases, warnings


def _load_latest_package_release_cache(
    package: ModuleRecord,
    per_page: int,
    cache_dir: str,
    package_kind: str,
    strict_fingerprint: bool,
) -> tuple[list[ReleaseRecord], list[str], datetime] | None:
    package_dir = _package_cache_path(package, per_page, cache_dir, package_kind).parent
    if not package_dir.exists():
        return None
    exact_cache_path = _package_cache_path(package, per_page, cache_dir, package_kind)
    candidate_paths: list[Path] = list(package_dir.glob("releases-per-page-*-*.json"))
    if exact_cache_path.exists() and exact_cache_path not in candidate_paths:
        candidate_paths.append(exact_cache_path)
    candidates = sorted(
        candidate_paths,
        key=lambda path: _path_mtime(path),
        reverse=True,
    )
    for cache_path in candidates:
        loaded = _load_package_release_cache_from_path(
            cache_path,
            package,
            strict_fingerprint=strict_fingerprint,
            allow_remote_lookup_fallback=False,
        )
        if loaded is not None:
            return loaded
    return None


def _resolve_stale_cache_fallback(
    package: ModuleRecord,
    per_page: int,
    cache_dir: str,
    package_kind: str,
    refresh_warnings: list[str],
    cached: tuple[list[ReleaseRecord], list[str], datetime] | None = None,
) -> tuple[list[ReleaseRecord], list[str]] | None:
    chosen = cached or _load_latest_package_release_cache(
        package,
        per_page,
        cache_dir,
        package_kind=package_kind,
        strict_fingerprint=False,
    )
    if chosen is None:
        return None
    releases, cached_warnings, cached_at = chosen
    combined_warnings: list[str] = [str(item) for item in cached_warnings]
    stale_warning = _build_stale_cache_warning(cached_at, refresh_warnings)
    if stale_warning:
        combined_warnings.append(stale_warning)
    return releases, combined_warnings


def _store_module_release_cache(
    module: ModuleRecord,
    per_page: int,
    cache_dir: str,
    releases: list[ReleaseRecord],
    warnings: list[str],
) -> None:
    cache_path = _module_cache_path(module, per_page, cache_dir)
    payload = {
        "schemaVersion": MODULE_CACHE_VERSION,
        "moduleFingerprint": _module_fingerprint(module),
        "releases": [_release_record_to_json(release) for release in releases],
        "warnings": warnings,
    }
    _write_text_cache(cache_path, json.dumps(payload, indent=2, sort_keys=True), cache_dir)


def _store_package_release_cache(
    package: ModuleRecord,
    per_page: int,
    cache_dir: str,
    releases: list[ReleaseRecord],
    warnings: list[str],
    package_kind: str,
) -> None:
    cache_path = _package_cache_path(package, per_page, cache_dir, package_kind)
    payload = {
        "schemaVersion": MODULE_CACHE_VERSION,
        "moduleFingerprint": _module_fingerprint(package),
        "releases": [_release_record_to_json(release) for release in releases],
        "warnings": warnings,
    }
    _write_text_cache(cache_path, json.dumps(payload, indent=2, sort_keys=True), cache_dir)


def _module_cache_path(module: ModuleRecord, per_page: int, cache_dir: str) -> Path:
    return _package_cache_path(module, per_page, cache_dir, "modules")


def _package_cache_path(package: ModuleRecord, per_page: int, cache_dir: str, package_kind: str) -> Path:
    project_key = package.project_url or package.manifest_url or package.module_id
    digest = sha256(project_key.encode("utf-8")).hexdigest()[:16]
    safe_module_id = "".join(character if character.isalnum() or character in ("-", "_") else "_" for character in package.module_id)
    return Path(cache_dir) / package_kind / safe_module_id / f"releases-per-page-{per_page}-{digest}.json"


def _module_fingerprint(module: ModuleRecord) -> dict[str, str | None]:
    return {
        "moduleId": module.module_id,
        "installedVersion": module.version,
        "manifestUrl": module.manifest_url,
        "projectUrl": module.project_url,
    }


def _fingerprint_matches(
    expected: ModuleRecord,
    fingerprint: dict[str, Any],
    strict_fingerprint: bool,
) -> bool:
    expected_fingerprint = _module_fingerprint(expected)
    if strict_fingerprint:
        return fingerprint == expected_fingerprint
    expected_id = str(expected_fingerprint.get("moduleId") or "").strip()
    cached_id = str(fingerprint.get("moduleId") or "").strip()
    if not expected_id or expected_id != cached_id:
        return False
    expected_origin = str(expected_fingerprint.get("projectUrl") or expected_fingerprint.get("manifestUrl") or "").strip().lower()
    cached_origin = str(fingerprint.get("projectUrl") or fingerprint.get("manifestUrl") or "").strip().lower()
    if expected_origin and cached_origin and expected_origin != cached_origin:
        return False
    return True


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _load_package_release_cache_from_path(
    cache_path: Path,
    package: ModuleRecord,
    strict_fingerprint: bool,
    allow_remote_lookup_fallback: bool,
) -> tuple[list[ReleaseRecord], list[str], datetime] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if payload.get("schemaVersion") != MODULE_CACHE_VERSION:
        return None
    fingerprint = payload.get("moduleFingerprint") or {}
    if not _fingerprint_matches(package, fingerprint, strict_fingerprint=strict_fingerprint):
        return None
    releases = [_release_record_from_json(item) for item in payload.get("releases", [])]
    warnings = [str(item) for item in payload.get("warnings", [])]
    if not releases:
        return None
    if not allow_remote_lookup_fallback and _is_remote_lookup_fallback_cache(releases, warnings):
        return None
    cached_at = datetime.fromtimestamp(_path_mtime(cache_path), tz=timezone.utc)
    return releases, warnings, cached_at


def _build_stale_cache_warning(cached_at: datetime, refresh_warnings: list[str]) -> str:
    now = datetime.now(timezone.utc)
    age_seconds = max(int((now - cached_at).total_seconds()), 0)
    age_label = _format_age_label(age_seconds)
    details = [
        str(item).strip()
        for item in refresh_warnings
        if str(item).strip() and "falling back to local manifest only" not in str(item).lower()
    ]
    detail_text = f" Refresh warning: {details[0]}" if details else ""
    return (
        f"{STALE_CACHE_WARNING_PREFIX}: metadata refresh failed; using cache from "
        f"{cached_at.isoformat()} ({age_label} old).{detail_text}"
    )


def _format_age_label(age_seconds: int) -> str:
    if age_seconds < 60:
        return "under 1 minute"
    minutes = age_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def _release_record_to_json(release: ReleaseRecord) -> dict[str, Any]:
    return {
        "version": release.version,
        "manifestUrl": release.manifest_url,
        "compatibility": release.compatibility,
        "systemCompatibility": release.system_compatibility,
        "moduleRequirements": [
            {
                "moduleId": requirement.module_id,
                "type": requirement.type,
                "compatibility": requirement.compatibility,
                "manifestUrl": requirement.manifest_url,
            }
            for requirement in release.module_requirements
        ],
        "downloadUrl": release.download_url,
        "source": release.source,
        "rawManifest": release.raw_manifest,
        "publishedAt": release.published_at,
    }


def _release_record_from_json(payload: dict[str, Any]) -> ReleaseRecord:
    return ReleaseRecord(
        version=str(payload.get("version") or ""),
        manifest_url=payload.get("manifestUrl"),
        compatibility=payload.get("compatibility") or {},
        system_compatibility=payload.get("systemCompatibility") or {},
        module_requirements=[
            ModuleRelationship(
                module_id=str(requirement.get("moduleId") or ""),
                type=str(requirement.get("type") or ""),
                compatibility=requirement.get("compatibility") or {},
                manifest_url=requirement.get("manifestUrl"),
            )
            for requirement in payload.get("moduleRequirements", [])
            if requirement.get("moduleId")
        ],
        download_url=payload.get("downloadUrl"),
        source=str(payload.get("source") or "module-cache"),
        raw_manifest=payload.get("rawManifest") or {},
        published_at=payload.get("publishedAt"),
    )


def _should_skip_release_cache_write(releases: list[ReleaseRecord], warnings: list[str]) -> bool:
    if len(releases) == 1 and str(releases[0].source or "") == "local-manifest":
        return True
    return _is_remote_lookup_fallback_cache(releases, warnings)


def _is_remote_lookup_fallback_cache(releases: list[ReleaseRecord], warnings: list[str]) -> bool:
    if len(releases) != 1:
        return False
    source = str(releases[0].source or "")
    if source != "local-manifest":
        return False
    warning_text = " ".join(str(item).lower() for item in warnings)
    if "falling back to local manifest only" in warning_text:
        return True
    return (
        "github release lookup failed" in warning_text
        or "github tag lookup failed" in warning_text
        or "gitlab lookup failed" in warning_text
        or "github releases were found, but no compatible manifest layout was discovered" in warning_text
        or "github tags were found, but no compatible manifest layout was discovered" in warning_text
        or "gitlab tags were found, but no manifest could be read" in warning_text
    )


def _load_foundry_release_catalog_cache(cache_path: Path) -> list[dict[str, str]] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("schemaVersion") != FOUNDRY_RELEASE_CATALOG_VERSION:
        return None
    fetched_at = payload.get("fetchedAt")
    if not fetched_at:
        return None
    try:
        fetched_time = datetime.fromisoformat(str(fetched_at))
    except ValueError:
        return None
    if fetched_time.tzinfo is None:
        fetched_time = fetched_time.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched_time > timedelta(hours=FOUNDRY_RELEASE_CATALOG_TTL_HOURS):
        return None
    releases = payload.get("releases")
    if not isinstance(releases, list):
        return None
    return [release for release in releases if isinstance(release, dict)]


def _parse_foundry_release_catalog(html: str) -> list[dict[str, str]]:
    releases: list[dict[str, str]] = []
    section_pattern = re.compile(
        r'<h2[^>]*class="border"[^>]*>\s*Version\s+(?P<generation>\d+)\s*</h2>\s*<ol[^>]*>(?P<body>.*?)</ol>',
        re.IGNORECASE | re.DOTALL,
    )
    release_pattern = re.compile(
        r'<li[^>]*class="article release[^"]*"[^>]*>.*?'
        r'<a href="(?P<href>/releases/(?P<version>\d+\.\d+))"[^>]*>Release\s+(?P=version)</a>.*?'
        r'<span class="release-time">(?P<date>[^<]+)</span>.*?'
        r'<div class="release-tags">(?P<tags>.*?)</div>.*?</li>',
        re.IGNORECASE | re.DOTALL,
    )
    tag_pattern = re.compile(r'<span class="release-tag(?:\s+[^"]*)?">([^<]+)</span>', re.IGNORECASE)

    for section_match in section_pattern.finditer(html):
        generation = section_match.group("generation")
        body = section_match.group("body")
        for release_match in release_pattern.finditer(body):
            tags = [tag.strip() for tag in tag_pattern.findall(release_match.group("tags")) if tag.strip()]
            releases.append(
                {
                    "generation": generation,
                    "version": release_match.group("version"),
                    "url": f"https://foundryvtt.com{release_match.group('href')}",
                    "publishedAt": " ".join(release_match.group("date").split()),
                    "channel": " ".join(tags),
                    "stability": tags[-1] if tags else "",
                }
            )
    releases.sort(key=lambda item: parse_version(item.get("version")), reverse=True)
    return releases


def _write_text_cache(cache_path: Path, payload: str, cache_dir: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(payload, encoding="utf-8")
    enforce_cache_limits(cache_dir)


def _write_bytes_cache(cache_path: Path, payload: bytes, cache_dir: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    enforce_cache_limits(cache_dir)
