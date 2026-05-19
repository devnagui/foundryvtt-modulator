from __future__ import annotations

from .versioning import compare_versions


def build_report_view_v3(payload: dict) -> dict:
    current_foundry = str(payload.get("targetVersion") or "")
    dependency_reference_counts = _build_dependency_reference_counts(payload)
    stable_future_releases = [
        row
        for row in payload.get("futureFoundryReleases") or []
        if str(row.get("stability") or "").lower() == "stable"
    ]
    stable_future_releases.sort(
        key=lambda row: _version_sort_key(str(row.get("version") or "")),
        reverse=True,
    )

    current_module_updates = _build_current_module_updates(payload, dependency_reference_counts)
    current_system_upgrades = _build_current_system_upgrades(payload, dependency_reference_counts)
    system_release_map = _build_system_release_map(payload)
    planner_targets = _build_planner_targets(
        payload,
        current_foundry,
        stable_future_releases,
        system_release_map,
        dependency_reference_counts,
    )
    planner_summary = _build_planner_summary(planner_targets)
    backup_management = _build_backup_management(payload, dependency_reference_counts)
    unused_modules = _build_unused_modules(payload, dependency_reference_counts)
    foundry_options = _build_foundry_options(current_foundry, stable_future_releases, planner_targets)
    default_version = foundry_options[0]["version"] if foundry_options else current_foundry

    return {
        "key": "v3",
        "generatedAt": payload.get("generatedAt"),
        "currentFoundryVersion": current_foundry,
        "cacheStatus": payload.get("cacheStatus") or {},
        "foundryServiceStatus": payload.get("foundryServiceStatus") or {},
        "summary": {
            "moduleCount": int(payload.get("moduleCount") or 0),
            "usedWorldCount": int(payload.get("usedWorldCount") or 0),
            "usedModuleCount": int(payload.get("usedModuleCount") or 0),
        },
        "controls": {
            "foundryOptions": foundry_options,
            "defaultFoundryVersion": default_version,
        },
        "currentModuleUpdates": current_module_updates,
        "currentSystemUpgrades": current_system_upgrades,
        "backupManagement": backup_management,
        "unusedModules": unused_modules,
        "systemUpgradePlanner": {
            "targets": planner_targets,
            "summary": planner_summary,
        },
    }


def _build_backup_management(payload: dict, dependency_reference_counts: dict[str, int]) -> dict:
    inventory = payload.get("backupInventory") or {}
    disk_status = payload.get("foundryDiskStatus") or {}
    rows = []
    for row in inventory.get("rows") or []:
        rows.append(
            {
                "module": row.get("module"),
                "title": row.get("title") or row.get("module"),
                "dependencyRefCount": _module_reference_count(row.get("module"), dependency_reference_counts),
                "moduleSizeBytes": int(row.get("moduleSizeBytes") or 0),
                "backupCount": int(row.get("backupCount") or 0),
                "backupSizeBytes": int(row.get("backupSizeBytes") or 0),
                "newestBackupAt": row.get("newestBackupAt"),
                "oldestBackupAt": row.get("oldestBackupAt"),
                "largestBackupBytes": int(row.get("largestBackupBytes") or 0),
                "largestBackupPath": row.get("largestBackupPath") or "",
                "modulePath": row.get("modulePath") or "",
            }
        )
    rows.sort(key=lambda item: (-int(item.get("backupSizeBytes") or 0), _module_sort_key(item)[1]))
    maintenance = payload.get("backupMaintenance") or {}
    policy = payload.get("backupPolicy") or {}
    return {
        "title": "Backup Management",
        "description": "Backups created during apply operations, grouped by module and ordered by consumed disk size.",
        "generatedAt": inventory.get("generatedAt") or payload.get("generatedAt"),
        "totalBackupBytes": int(inventory.get("totalBackupBytes") or 0),
        "totalBackupCount": int(inventory.get("totalBackupCount") or 0),
        "moduleCountWithBackups": int(inventory.get("moduleCountWithBackups") or 0),
        "totalModuleBytes": int(inventory.get("totalModuleBytes") or 0),
        "moduleCount": int(inventory.get("moduleCount") or 0),
        "sizeCache": inventory.get("sizeCache") or {},
        "diskStatus": {
            "path": disk_status.get("path"),
            "totalBytes": int(disk_status.get("totalBytes") or 0),
            "usedBytes": int(disk_status.get("usedBytes") or 0),
            "freeBytes": int(disk_status.get("freeBytes") or 0),
            "usedPercent": float(disk_status.get("usedPercent") or 0.0),
            "freePercent": float(disk_status.get("freePercent") or 0.0),
            "error": disk_status.get("error"),
        },
        "policy": {
            "maxBytes": int(policy.get("maxBytes") or 0),
            "maxPerModule": int(policy.get("maxPerModule") or 0),
            "maxAgeDays": int(policy.get("maxAgeDays") or 0),
        },
        "maintenance": {
            "removedCount": int(maintenance.get("removedCount") or 0),
            "removedBytes": int(maintenance.get("removedBytes") or 0),
            "remainingCount": int(maintenance.get("remainingCount") or 0),
            "remainingBytes": int(maintenance.get("remainingBytes") or 0),
        },
        "rows": rows,
    }


def _build_unused_modules(payload: dict, dependency_reference_counts: dict[str, int]) -> dict:
    used_modules = {
        str(module_id).strip()
        for world in (payload.get("worldUsage") or [])
        for module_id in (world.get("enabledModules") or [])
        if str(module_id).strip()
    }
    disk_inventory = (payload.get("moduleDiskInventory") or {}).get("byModule") or {}
    rows_by_module: dict[str, dict] = {}
    for row in payload.get("results") or []:
        module_id = str(row.get("module") or "").strip()
        if not module_id or module_id in used_modules:
            continue
        installed = str(row.get("installedVersion") or "")
        recommended = str(row.get("recommendedVersion") or "")
        disk_row = disk_inventory.get(module_id) or {}
        update_viable = bool(
            installed
            and recommended
            and compare_versions(recommended, installed) > 0
            and row.get("downloadUrl")
        )
        candidate = {
            "module": module_id,
            "title": row.get("title") or module_id,
            "dependencyRefCount": _module_reference_count(module_id, dependency_reference_counts),
            "installedVersion": installed,
            "recommendedVersion": recommended,
            "updateViable": update_viable,
            "updateStatus": "Yes" if update_viable else "No",
            "reason": row.get("reason") or "",
            "confidence": row.get("confidence") or "unknown",
            "manifestUrl": row.get("manifestUrl"),
            "downloadUrl": row.get("downloadUrl"),
            "modulePath": disk_row.get("modulePath") or row.get("modulePath") or "",
            "moduleSizeBytes": int(disk_row.get("sizeBytes") or 0),
            "modifiedAt": disk_row.get("modifiedAt"),
            "modifiedAtEpoch": float(disk_row.get("modifiedAtEpoch") or 0.0),
            "compatibility": row.get("compatibility") or {},
            "systemCompatibility": row.get("systemCompatibility") or {},
            "releasePublishedAt": row.get("releasePublishedAt"),
            "attentionFlag": bool(row.get("attentionFlag")),
        }
        existing = rows_by_module.get(module_id)
        if existing is None:
            rows_by_module[module_id] = candidate
            continue
        existing_viable = bool(existing.get("updateViable"))
        if update_viable and not existing_viable:
            rows_by_module[module_id] = candidate
            continue
        if update_viable == existing_viable:
            existing_mtime = float(existing.get("modifiedAtEpoch") or 0.0)
            if float(candidate.get("modifiedAtEpoch") or 0.0) > existing_mtime:
                rows_by_module[module_id] = candidate
    rows = list(rows_by_module.values())
    rows.sort(
        key=lambda item: (
            -float(item.get("modifiedAtEpoch") or 0.0),
            _module_sort_key(item)[1],
        )
    )
    total_size = sum(int(item.get("moduleSizeBytes") or 0) for item in rows)
    updatable = sum(1 for item in rows if item.get("updateViable"))
    return {
        "title": "Unused Modules",
        "description": "Installed modules not referenced by any world. Includes disk footprint, last modified date and whether a safe update path exists.",
        "count": len(rows),
        "updatableCount": updatable,
        "totalSizeBytes": int(total_size),
        "rows": rows,
    }


def _build_current_module_updates(payload: dict, dependency_reference_counts: dict[str, int]) -> dict:
    rows_by_module: dict[str, dict] = {}
    package_hints = payload.get("databasePackageHints") or {}

    def _upsert(candidate: dict) -> None:
        module_id = str(candidate.get("module") or "").strip()
        if not module_id:
            return
        existing = rows_by_module.get(module_id)
        if existing is None:
            rows_by_module[module_id] = candidate
            return
        existing_version = str(existing.get("recommendedVersion") or "")
        candidate_version = str(candidate.get("recommendedVersion") or "")
        should_replace = False
        if existing_version and candidate_version:
            should_replace = compare_versions(candidate_version, existing_version) > 0
        if should_replace:
            reason = str(existing.get("reason") or "").strip()
            candidate_reason = str(candidate.get("reason") or "").strip()
            if reason and candidate_reason and reason != candidate_reason:
                candidate["reason"] = f"{candidate_reason} | Also: {reason}"
            rows_by_module[module_id] = candidate
            return
        reason = str(existing.get("reason") or "").strip()
        candidate_reason = str(candidate.get("reason") or "").strip()
        if candidate_reason and candidate_reason not in reason:
            existing["reason"] = f"{reason} | Also: {candidate_reason}" if reason else candidate_reason

    for row in payload.get("results") or []:
        installed_version = str(row.get("installedVersion") or "")
        recommended_version = str(row.get("recommendedVersion") or "")
        if not installed_version or not recommended_version:
            continue
        if compare_versions(recommended_version, installed_version) <= 0:
            # Even when the module itself is not upgrading, dependency updates may exist.
            pass
        hint = package_hints.get(str(row.get("module") or "")) or {}
        if installed_version and recommended_version and compare_versions(recommended_version, installed_version) > 0:
            _upsert(
                {
                    "module": row.get("module"),
                    "title": row.get("title") or row.get("module"),
                    "dependencyRefCount": _module_reference_count(row.get("module"), dependency_reference_counts),
                    "installedVersion": installed_version,
                    "recommendedVersion": recommended_version,
                    "confidence": row.get("confidence") or "unknown",
                    "reason": row.get("reason") or "",
                    "manifestUrl": row.get("manifestUrl") or hint.get("manifestUrl"),
                    "downloadUrl": row.get("downloadUrl") or hint.get("downloadUrl"),
                    "compatibility": row.get("compatibility") or {},
                    "systemCompatibility": row.get("systemCompatibility") or {},
                    "releasePublishedAt": row.get("releasePublishedAt"),
                    "attentionFlag": bool(row.get("attentionFlag")),
                }
            )

        for dependency in row.get("dependencyUpdates") or []:
            dep_module = str(dependency.get("module") or "").strip()
            dep_installed = str(dependency.get("installedVersion") or "")
            dep_recommended = str(dependency.get("recommendedVersion") or "")
            if not dep_module or not dep_installed or not dep_recommended:
                continue
            if compare_versions(dep_recommended, dep_installed) <= 0:
                continue
            dep_hint = package_hints.get(dep_module) or {}
            parent_module = str(row.get("module") or "").strip()
            dep_reason = str(dependency.get("reason") or "").strip()
            if parent_module:
                dep_reason = f"Required by {parent_module}: {dep_reason}" if dep_reason else f"Required by {parent_module}."
            _upsert(
                {
                    "module": dep_module,
                    "title": dependency.get("title") or dep_module,
                    "dependencyRefCount": _module_reference_count(dep_module, dependency_reference_counts),
                    "installedVersion": dep_installed,
                    "recommendedVersion": dep_recommended,
                    "confidence": row.get("confidence") or "medium",
                    "reason": dep_reason,
                    "manifestUrl": dependency.get("manifestUrl") or dep_hint.get("manifestUrl"),
                    "downloadUrl": dependency.get("downloadUrl") or dep_hint.get("downloadUrl"),
                    "compatibility": dependency.get("compatibility") or {},
                    "systemCompatibility": dependency.get("systemCompatibility") or {},
                    "releasePublishedAt": dependency.get("releasePublishedAt"),
                    "attentionFlag": bool(dependency.get("attentionFlag")),
                }
            )
    rows = list(rows_by_module.values())
    rows.sort(key=_module_sort_key)
    return {
        "title": "Module Updates",
        "description": "Recommended module upgrades that are compatible with the currently installed Foundry and base systems.",
        "count": len(rows),
        "rows": rows,
    }


def _build_current_system_upgrades(payload: dict, dependency_reference_counts: dict[str, int]) -> dict:
    summary_rows = payload.get("currentSystemUpgradeSummary") or []
    module_rows = payload.get("currentSystemUpgradeModules") or []
    grouped_modules: dict[str, dict[str, list[dict]]] = {}
    for module_row in module_rows:
        system_id = str(module_row.get("systemId") or "").strip()
        if not system_id:
            continue
        grouped = grouped_modules.setdefault(
            system_id,
            {
                "compatible": [],
                "upgradable": [],
                "blocked": [],
                "unknown": [],
            },
        )
        status = str(module_row.get("status") or "").strip().lower()
        enriched = dict(module_row)
        enriched["dependencyRefCount"] = _module_reference_count(module_row.get("module"), dependency_reference_counts)
        grouped.setdefault(status, []).append(enriched)

    rows = []
    for summary in summary_rows:
        system_id = str(summary.get("systemId") or "").strip()
        grouped = grouped_modules.get(system_id, {})
        compatible_rows = sorted(
            grouped.get("compatible") or [],
            key=_module_sort_key,
        )
        upgradable_rows = sorted(
            grouped.get("upgradable") or [],
            key=_module_sort_key,
        )
        blocked_rows = sorted(
            grouped.get("blocked") or [],
            key=_module_sort_key,
        )
        unknown_rows = sorted(
            grouped.get("unknown") or [],
            key=_module_sort_key,
        )
        rows.append(
            {
                "systemId": system_id,
                "title": summary.get("title") or system_id,
                "installedVersion": summary.get("installedVersion"),
                "targetVersion": summary.get("targetVersion"),
                "availableVersions": summary.get("candidateVersions") or [],
                "manifestUrl": summary.get("manifestUrl"),
                "downloadUrl": summary.get("downloadUrl"),
                "compatibility": summary.get("compatibility") or {},
                "worldAliases": summary.get("worldAliases") or [],
                "modulesUsed": int(summary.get("modulesUsed") or 0),
                "ignoredModules": int(summary.get("ignoredModules") or 0),
                "compatibleModules": int(summary.get("compatibleModules") or 0),
                "upgradableModules": int(summary.get("upgradableModules") or 0),
                "blockedModules": int(summary.get("blockedModules") or 0),
                "coveragePercent": summary.get("coveragePercent") or 0.0,
                "compatibleModuleRows": compatible_rows,
                "upgradableModuleRows": upgradable_rows,
                "blockedModuleRows": blocked_rows,
                "unknownModuleRows": unknown_rows,
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row.get("modulesUsed") or 0),
            str(row.get("title") or row.get("systemId") or "").lower(),
        )
    )
    return {
        "title": "System Upgrades on Current Foundry",
        "description": "Base system upgrades that fit the currently installed Foundry version, including which world-used modules are already compatible, need updates, or remain blocked.",
        "count": len(rows),
        "rows": rows,
    }


def _build_planner_targets(
    payload: dict,
    current_foundry: str,
    stable_future_releases: list[dict],
    system_release_map: dict[tuple[str, str], dict],
    dependency_reference_counts: dict[str, int],
) -> list[dict]:
    module_system_map = _build_module_system_map(payload.get("worldUsage") or [])
    targets = []
    future_targets_by_version = {
        str(row.get("targetFoundryVersion") or ""): row
        for row in payload.get("futureUpgradeMatrix") or []
        if str(row.get("targetFoundryVersion") or "")
    }
    for release in stable_future_releases:
        version = str(release.get("version") or "")
        future_row = future_targets_by_version.get(version)
        if not future_row:
            continue
        targets.append(
            _build_future_foundry_target(
                future_row,
                release,
                module_system_map,
                system_release_map,
                dependency_reference_counts,
            )
        )
    for target in targets:
        quick = target.get("quickStatus") or {}
        target["score"] = _compute_planner_score(quick)
    targets.sort(
        key=lambda row: (
            -float((row.get("score") or {}).get("value") or 0.0),
            _version_sort_key(str(row.get("foundryVersion") or "")),
        ),
        reverse=False,
    )
    return targets


def _compute_planner_score(quick: dict) -> dict:
    total = max(int(quick.get("modulesTotal") or 0), 0)
    ready = max(int(quick.get("modulesReady") or 0), 0)
    update = max(int(quick.get("modulesNeedUpdate") or 0), 0)
    blocked = max(int(quick.get("modulesBlocked") or 0), 0)
    unknown = max(int(quick.get("modulesNeedsVerification") or 0), 0)
    missing = max(int(quick.get("modulesManualUpdate") or 0), 0)
    readiness = (ready + update) / total if total else 1.0
    # Weighted score in [0, 100].
    value = round(
        max(
            0.0,
            min(
                100.0,
                100.0 * readiness
                - (blocked * 18.0)
                - (missing * 10.0)
                - (unknown * 6.0),
            ),
        ),
        1,
    )
    tone = "green" if value >= 75 else ("yellow" if value >= 45 else "red")
    return {
        "value": value,
        "tone": tone,
        "readinessPercent": round(readiness * 100.0, 1),
        "blocked": blocked,
        "missing": missing,
        "unknown": unknown,
    }


def _build_planner_summary(targets: list[dict]) -> dict:
    if not targets:
        return {"bestTargetVersion": "", "bestTargetReason": "No stable targets available."}
    ranked = sorted(
        targets,
        key=lambda row: (
            -float((row.get("score") or {}).get("value") or 0.0),
            _version_sort_key(str(row.get("foundryVersion") or "")),
        ),
    )
    best = ranked[0]
    score = best.get("score") or {}
    quick = best.get("quickStatus") or {}
    reason = (
        f"Best balance of coverage and risk for this environment: "
        f"{quick.get('modulesReady', 0)} ready, {quick.get('modulesNeedUpdate', 0)} updates, "
        f"{quick.get('modulesBlocked', 0)} blocked, {quick.get('modulesNeedsVerification', 0)} verification."
    )
    blocked_by_version = []
    for row in sorted(targets, key=lambda item: _version_sort_key(str(item.get("foundryVersion") or ""))):
        blockers = [
            str(item.get("module") or "")
            for item in (row.get("blockedModules") or [])
            if str(item.get("module") or "")
        ]
        blocked_by_version.append(
            {
                "foundryVersion": str(row.get("foundryVersion") or ""),
                "blockedCount": int((row.get("quickStatus") or {}).get("modulesBlocked") or 0),
                "topBlockers": sorted(blockers)[:8],
            }
        )
    return {
        "bestTargetVersion": str(best.get("foundryVersion") or ""),
        "bestTargetScore": float(score.get("value") or 0.0),
        "bestTargetTone": str(score.get("tone") or "yellow"),
        "bestTargetReason": reason,
        "blockedByVersion": blocked_by_version,
    }


def _build_future_foundry_target(
    row: dict,
    release: dict,
    module_system_map: dict[str, list[str]],
    system_release_map: dict[tuple[str, str], dict],
    dependency_reference_counts: dict[str, int],
) -> dict:
    systems = []
    for item in row.get("systemCompatibility") or []:
        system_id = str(item.get("systemId") or "")
        impacted_module_ids = [str(module_id) for module_id in item.get("impactedModuleIds") or [] if str(module_id)]
        impacted_module_id_set = set(impacted_module_ids)
        release_hint = system_release_map.get((str(row.get("targetFoundryVersion") or ""), system_id), {})
        ready_rows, upgradable_rows, blocked_rows, unknown_rows = _build_system_module_lists(
            row.get("moduleOutcomes") or [],
            impacted_module_id_set,
            module_system_map,
            dependency_reference_counts,
        )
        policy_blocked_rows = [current for current in blocked_rows if _is_non_blocking_policy_blocker(current)]
        hard_blocked_rows = [current for current in blocked_rows if not _is_non_blocking_policy_blocker(current)]
        if policy_blocked_rows:
            promoted_rows = []
            for current in policy_blocked_rows:
                promoted = dict(current)
                promoted["status"] = "ready"
                promoted_rows.append(promoted)
            ready_rows = sorted(ready_rows + promoted_rows, key=_module_sort_key)
        blocked_rows = sorted(hard_blocked_rows, key=_module_sort_key)

        ready_count = len(ready_rows)
        upgradable_count = len(upgradable_rows)
        blocked_count = len(blocked_rows)
        unknown_count = len(unknown_rows)
        reachable_count = ready_count + upgradable_count
        total_modules = len(impacted_module_ids)
        considered_modules = ready_count + upgradable_count + blocked_count
        reachable_percent = round((reachable_count / considered_modules * 100.0), 1) if considered_modules else 100.0
        systems.append(
            {
                "systemId": system_id,
                "title": item.get("title") or item.get("systemId"),
                "installedVersion": item.get("installedVersion"),
                "targetVersion": item.get("recommendedVersion"),
                "availableVersions": item.get("candidateVersions") or [],
                "targetFoundryVersion": row.get("targetFoundryVersion"),
                "worldAliases": item.get("worldAliases") or [],
                "modulesUsed": total_modules,
                "modulesConsidered": considered_modules,
                "readyModules": ready_count,
                "upgradableModules": upgradable_count,
                "blockedModules": blocked_count,
                "unknownModules": unknown_count,
                "coveragePercent": reachable_percent,
                "manifestUrl": release_hint.get("manifestUrl"),
                "downloadUrl": release_hint.get("downloadUrl"),
                "compatibility": item.get("compatibility") or release_hint.get("compatibility") or {},
                "stateSummary": {
                    "ready": ready_count,
                    "upgradable": upgradable_count,
                    "blocked": blocked_count,
                    "unknown": unknown_count,
                },
                "targetReady": bool(item.get("recommendedVersion")),
                "readyModuleRows": ready_rows,
                "upgradableModuleRows": upgradable_rows,
                "blockedModuleRows": blocked_rows,
                "unknownModuleRows": unknown_rows,
            }
        )
    systems.sort(
        key=lambda item: (
            -int(item.get("modulesUsed") or 0),
            str(item.get("title") or item.get("systemId") or "").lower(),
        )
    )

    # Collect all module IDs assigned to at least one system so we can
    # identify unused/unassigned modules later.
    system_assigned_module_ids: set[str] = set()
    for item in row.get("systemCompatibility") or []:
        for module_id in item.get("impactedModuleIds") or []:
            mid = str(module_id).strip()
            if mid:
                system_assigned_module_ids.add(mid)

    ready_modules = []
    upgradable_modules = []
    blocked_modules = []
    unknown_modules = []
    local_manifest_manual_modules = []
    for item in row.get("moduleOutcomes") or []:
        status = item.get("status") or "blocked"
        module_id = str(item.get("module") or "").strip()
        entry = {
            "module": item.get("module"),
            "title": item.get("title") or item.get("module"),
            "dependencyRefCount": _module_reference_count(item.get("module"), dependency_reference_counts),
            "systemIds": module_system_map.get(str(item.get("module") or ""), []),
            "installedVersion": item.get("installedVersion"),
            "recommendedVersion": item.get("recommendedVersion"),
            "status": status,
            "confidence": item.get("confidence") or "unknown",
            "reason": item.get("reason") or "",
            "manifestUrl": item.get("manifestUrl"),
            "downloadUrl": item.get("downloadUrl"),
            "source": item.get("source"),
            "compatibility": item.get("compatibility") or {},
            "systemCompatibility": item.get("systemCompatibility") or {},
            "releasePublishedAt": item.get("releasePublishedAt"),
            "attentionFlag": bool(item.get("attentionFlag")),
        }
        if status == "ready":
            ready_modules.append(entry)
        elif status == "upgradable":
            upgradable_modules.append(entry)
        elif status == "blocked":
            blocked_modules.append(entry)
        else:
            unknown_modules.append(entry)
        if status == "excluded-local-only":
            local_manifest_manual_modules.append(entry)
        # Modules not assigned to any system are unused/manual — route
        # them to localManifestManualModules so the frontend can display
        # them in the "Unused" bucket.
        elif module_id and module_id not in system_assigned_module_ids:
            local_manifest_manual_modules.append(entry)

    policy_blocked_modules = [current for current in blocked_modules if _is_non_blocking_policy_blocker(current)]
    if policy_blocked_modules:
        promoted_modules = []
        for current in policy_blocked_modules:
            promoted = dict(current)
            promoted["status"] = "ready"
            promoted_modules.append(promoted)
        ready_modules.extend(promoted_modules)
    blocked_modules = [current for current in blocked_modules if not _is_non_blocking_policy_blocker(current)]

    manual_by_module: dict[str, dict] = {}
    for item in unknown_modules:
        module_id = str(item.get("module") or "")
        if module_id:
            manual_by_module[module_id] = item
    for item in local_manifest_manual_modules:
        module_id = str(item.get("module") or "")
        if module_id and module_id not in manual_by_module:
            manual_by_module[module_id] = item
    manual_modules = list(manual_by_module.values())

    ready_modules.sort(key=_module_sort_key)
    upgradable_modules.sort(key=_module_sort_key)
    blocked_modules.sort(key=_module_sort_key)
    unknown_modules.sort(key=_module_sort_key)
    manual_modules.sort(key=_module_sort_key)

    blocked_systems = [
        current
        for current in systems
        if int(current.get("blockedModules") or 0) > 0
    ]
    unknown_systems = [
        current
        for current in systems
        if int(current.get("unknownModules") or 0) > 0
    ]
    # "Systems Ready" in the planner means no hard blockers for the selected target.
    # Verification-needed modules are tracked separately in systemsNeedsVerification.
    systems_ready = len(
        [
            current
            for current in systems
            if int(current.get("blockedModules") or 0) == 0
        ]
    )
    systems_total = len(systems)
    modules_total = len(ready_modules) + len(upgradable_modules) + len(blocked_modules) + len(unknown_modules)
    modules_considered = len(ready_modules) + len(upgradable_modules) + len(blocked_modules)
    not_ready_modules = sorted(
        blocked_modules + unknown_modules,
        key=_module_sort_key,
    )
    if not blocked_modules and not unknown_modules:
        verdict = "ready"
        verdict_label = "Ready now"
    elif not blocked_modules:
        verdict = "attention"
        verdict_label = "Needs checks"
    else:
        verdict = "blocked"
        verdict_label = "Not ready"

    return {
        "foundryVersion": row.get("targetFoundryVersion"),
        "label": str(row.get("targetFoundryVersion") or ""),
        "targetUrl": release.get("url") or row.get("targetFoundryUrl"),
        "isCurrent": False,
        "systems": systems,
        "systemRows": systems,
        "worldAliases": row.get("worldsAffected") or [],
        "readyModules": ready_modules,
        "upgradableModules": upgradable_modules,
        "blockedModules": blocked_modules,
        "unknownModules": unknown_modules,
        "notReadyModules": not_ready_modules,
        "localManifestManualModules": manual_modules,
        "quickStatus": {
            "verdict": verdict,
            "verdictLabel": verdict_label,
            "systemsReady": systems_ready,
            "systemsTotal": systems_total,
            "systemsBlocked": len(blocked_systems),
            "systemsNeedsVerification": len(unknown_systems),
            "modulesTotal": modules_considered,
            "modulesObserved": modules_total,
            "modulesReady": len(ready_modules),
            "modulesNeedUpdate": len(upgradable_modules),
            "modulesBlocked": len(blocked_modules),
            "modulesNeedsVerification": len(unknown_modules),
            "modulesManualUpdate": len(manual_modules),
        },
        "summary": {
            "systemCount": len(systems),
            "worldCount": len(row.get("worldsAffected") or []),
            "readyModuleCount": len(ready_modules),
            "upgradableModuleCount": len(upgradable_modules),
            "blockedModuleCount": len(blocked_modules),
            "unknownModuleCount": len(unknown_modules),
        },
    }


def _build_foundry_options(current_foundry: str, stable_future_releases: list[dict], planner_targets: list[dict]) -> list[dict]:
    seen_versions: set[str] = set()
    available_versions = {str(item.get("foundryVersion") or "") for item in planner_targets}
    options = []
    for release in stable_future_releases:
        version = str(release.get("version") or "")
        if not version or version in seen_versions or version not in available_versions:
            continue
        options.append(
            {
                "value": version,
                "version": version,
                "label": version,
                "isCurrent": False,
            }
        )
        seen_versions.add(version)
    return options


def _build_system_release_map(payload: dict) -> dict[tuple[str, str], dict]:
    release_map: dict[tuple[str, str], dict] = {}
    for system_id, rows in (payload.get("futureSystemRecommendations") or {}).items():
        for row in rows or []:
            target_foundry = str(row.get("targetFoundryVersion") or "").strip()
            if not target_foundry:
                continue
            key = (target_foundry, str(system_id).strip())
            existing = release_map.get(key, {})
            release_map[key] = {
                "title": row.get("title") or existing.get("title") or system_id,
                "manifestUrl": row.get("manifestUrl") or existing.get("manifestUrl"),
                "downloadUrl": row.get("downloadUrl") or existing.get("downloadUrl"),
                "compatibility": row.get("compatibility") or existing.get("compatibility") or {},
            }
    return release_map


def _build_system_module_lists(
    module_outcomes: list[dict],
    impacted_module_id_set: set[str],
    module_system_map: dict[str, list[str]],
    dependency_reference_counts: dict[str, int],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    ready_rows = []
    upgradable_rows = []
    blocked_rows = []
    unknown_rows = []
    for item in module_outcomes:
        module_id = str(item.get("module") or "")
        if not module_id or module_id not in impacted_module_id_set:
            continue
        entry = {
            "module": item.get("module"),
            "title": item.get("title") or item.get("module"),
            "dependencyRefCount": _module_reference_count(item.get("module"), dependency_reference_counts),
            "systemIds": module_system_map.get(module_id, []),
            "installedVersion": item.get("installedVersion"),
            "recommendedVersion": item.get("recommendedVersion"),
            "status": item.get("status") or "blocked",
            "confidence": item.get("confidence") or "unknown",
            "reason": item.get("reason") or "",
            "manifestUrl": item.get("manifestUrl"),
            "downloadUrl": item.get("downloadUrl"),
            "compatibility": item.get("compatibility") or {},
            "systemCompatibility": item.get("systemCompatibility") or {},
            "releasePublishedAt": item.get("releasePublishedAt"),
            "attentionFlag": bool(item.get("attentionFlag")),
        }
        status = str(item.get("status") or "").strip().lower()
        if status == "ready":
            ready_rows.append(entry)
        elif status == "upgradable":
            upgradable_rows.append(entry)
        elif status == "blocked":
            blocked_rows.append(entry)
        else:
            unknown_rows.append(entry)
    for rows in (ready_rows, upgradable_rows, blocked_rows, unknown_rows):
        rows.sort(key=_module_sort_key)
    return ready_rows, upgradable_rows, blocked_rows, unknown_rows


def _module_reference_count(module_id: object, dependency_reference_counts: dict[str, int]) -> int:
    clean = str(module_id or "").strip()
    if not clean:
        return 0
    return int(dependency_reference_counts.get(clean, 0))


def _module_sort_key(row: dict) -> tuple[int, str]:
    title = str(row.get("title") or row.get("module") or "").lower()
    attention = 1 if bool(row.get("attentionFlag")) else 0
    references = int(row.get("dependencyRefCount") or 0)
    return (-attention, -references, title)


def _is_non_blocking_policy_blocker(row: dict) -> bool:
    reason = str(row.get("reason") or "").lower()
    return "rollback suggestions are suppressed" in reason


def _build_dependency_reference_counts(payload: dict) -> dict[str, int]:
    local_dependency_map = payload.get("localDependencyMap") or {}
    reference_counts: dict[str, int] = {}
    if not isinstance(local_dependency_map, dict):
        return reference_counts
    for dependent_module, relationships in local_dependency_map.items():
        if not isinstance(relationships, dict):
            continue
        direct_dependencies = relationships.get("direct") or []
        seen_for_dependent: set[str] = set()
        for module_id in direct_dependencies:
            dependency_id = str(module_id or "").strip()
            if not dependency_id or dependency_id in seen_for_dependent:
                continue
            seen_for_dependent.add(dependency_id)
            reference_counts[dependency_id] = int(reference_counts.get(dependency_id, 0)) + 1
        clean_dependent = str(dependent_module or "").strip()
        if clean_dependent and clean_dependent not in reference_counts:
            reference_counts[clean_dependent] = 0
    return reference_counts


def _build_module_system_map(world_usage: list[dict]) -> dict[str, list[str]]:
    module_systems: dict[str, set[str]] = {}
    for world in world_usage:
        system_id = str(world.get("system") or "").strip()
        if not system_id:
            continue
        for module_id in world.get("enabledModules") or []:
            clean_module_id = str(module_id).strip()
            if not clean_module_id:
                continue
            module_systems.setdefault(clean_module_id, set()).add(system_id)
    return {
        module_id: sorted(system_ids)
        for module_id, system_ids in module_systems.items()
    }


def _version_sort_key(version: str) -> tuple[int, ...]:
    pieces = []
    for chunk in version.split("."):
        try:
            pieces.append(int(chunk))
        except ValueError:
            pieces.append(0)
    return tuple(pieces)
