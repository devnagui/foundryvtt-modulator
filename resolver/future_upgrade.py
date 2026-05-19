from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .dependencies import resolve_module_recommendation
from .models import ModuleRecord, ModuleRelationship, Recommendation, ReleaseRecord
from .scoring import candidate_sort_key, satisfies_release_constraints
from .versioning import compare_versions, exceeds_maximum, is_below_minimum


HistoryFetcherWithLimit = Callable[[ModuleRecord, int], tuple[list[ReleaseRecord], list[str]]]
ModuleLoader = Callable[[ModuleRelationship], ModuleRecord | None]


def build_future_upgrade_decision(
    future_foundry_releases: list[dict],
    worlds: list[dict],
    installed_modules_by_id: dict[str, ModuleRecord],
    installed_systems_by_id: dict[str, ModuleRecord],
    fetch_module_history: HistoryFetcherWithLimit,
    fetch_system_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
) -> dict:
    unresolved_world_labels = [
        _world_label(world)
        for world in worlds
        if not world.get("moduleConfigurationResolved") and world.get("moduleConfigurationSource") is None
    ]
    used_worlds = [world for world in worlds if world.get("system")]
    used_world_aliases = sorted({_world_alias(world) for world in used_worlds})
    used_module_ids = sorted({module_id for world in used_worlds for module_id in world.get("enabledModules", []) if module_id in installed_modules_by_id})
    used_modules = [installed_modules_by_id[module_id] for module_id in used_module_ids]
    module_world_map = {
        module_id: [
            {
                "id": str(world.get("id")),
                "system": world.get("system"),
            }
            for world in used_worlds
            if module_id in world.get("enabledModules", [])
        ]
        for module_id in used_module_ids
    }
    used_module_ids_set = set(used_module_ids)

    matrix = []
    system_recommendations: dict[str, list[dict]] = {}
    for release in future_foundry_releases:
        if str(release.get("stability") or "").lower() != "stable":
            continue
        target_foundry = str(release.get("version") or "")
        per_system = {}
        for world in used_worlds:
            system_id = world.get("system")
            if not system_id or system_id not in installed_systems_by_id:
                continue
            if system_id not in per_system:
                per_system[system_id] = _recommend_future_system_version(
                    installed_systems_by_id[system_id],
                    target_foundry,
                    fetch_system_history,
                )
        for system_id, system_row in per_system.items():
            system_recommendations.setdefault(system_id, []).append(
                {
                    "targetFoundryVersion": target_foundry,
                    **system_row,
                }
            )

        row = _evaluate_future_target(
            release,
            used_worlds,
            used_modules,
            module_world_map,
            per_system,
            fetch_module_history,
            load_module_for_relationship,
            unresolved_world_labels,
        )
        matrix.append(row)

    # Resolve unused modules once against the highest Foundry target,
    # then inject the outcomes into all targets. Release histories are
    # already cached from the initial scan, so this is just re-scoring.
    if matrix:
        # Pick the highest Foundry target for resolution
        highest_target = max(matrix, key=lambda r: r.get("targetFoundryVersion", ""))
        highest_foundry = str(highest_target.get("targetFoundryVersion") or "")
        # Collect system versions from highest target
        highest_systems = highest_target.get("systems") or []
        target_system_versions: dict[str, str] = {}
        for sys_entry in highest_systems:
            if isinstance(sys_entry, dict):
                sid = str(sys_entry.get("systemId") or "")
                sver = str(sys_entry.get("recommendedVersion") or sys_entry.get("installedVersion") or "")
                if sid and sver:
                    target_system_versions[sid] = sver

        resolution_cache: dict[str, Recommendation] = {}
        unused_outcomes: list[dict] = []
        for module_id in sorted(installed_modules_by_id.keys()):
            if module_id in used_module_ids_set:
                continue
            module = installed_modules_by_id[module_id]
            recommendation, _ = resolve_module_recommendation(
                module,
                highest_foundry,
                target_system_versions,
                fetch_module_history,
                load_module_for_relationship,
                resolution_cache,
            )
            status = _classify_future_module(module, recommendation, highest_foundry)
            unused_outcomes.append(
                {
                    "module": module.module_id,
                    "title": module.title,
                    "installedVersion": module.version,
                    "recommendedVersion": recommendation.recommended_version,
                    "status": status,
                    "reason": recommendation.reason,
                    "confidence": recommendation.confidence,
                    "source": recommendation.source,
                    "manifestUrl": recommendation.manifest_url,
                    "downloadUrl": recommendation.download_url,
                    "compatibility": recommendation.compatibility,
                    "systemCompatibility": recommendation.system_compatibility,
                    "forcedCompatibility": _forced_compatibility_payload(module),
                    "releasePublishedAt": recommendation.release_published_at,
                    "attentionFlag": False,
                }
            )

        # Inject unused outcomes into each target's moduleOutcomes
        for row in matrix:
            target_fv = str(row.get("targetFoundryVersion") or "")
            existing = row.get("moduleOutcomes") or []
            for outcome in unused_outcomes:
                entry = dict(outcome)
                entry["futureTargetVersion"] = target_fv
                existing.append(entry)
            row["moduleOutcomes"] = existing

    matrix.sort(
        key=lambda row: (
            row.get("coveragePercent", 0.0),
            -row.get("blockedCount", 0),
            -row.get("unresolvedDependencyCount", 0),
            row.get("targetFoundryVersion", ""),
        ),
        reverse=True,
    )

    return {
        "usedWorldCount": len(used_worlds),
        "usedWorldAliases": used_world_aliases,
        "usedModuleCount": len(used_modules),
        "usedWorldModules": used_module_ids,
        "unresolvedWorldUsage": unresolved_world_labels,
        "futureSystemRecommendations": system_recommendations,
        "futureUpgradeMatrix": matrix,
        "bestFutureUpgradeTarget": matrix[0] if matrix else None,
    }


def build_current_system_upgrade_view(
    target_foundry: str,
    worlds: list[dict],
    installed_modules_by_id: dict[str, ModuleRecord],
    installed_systems_by_id: dict[str, ModuleRecord],
    fetch_module_history: HistoryFetcherWithLimit,
    fetch_system_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
) -> dict:
    used_worlds = [world for world in worlds if world.get("system")]
    module_world_map = {
        module_id: [
            {
                "id": str(world.get("id")),
                "system": world.get("system"),
            }
            for world in used_worlds
            if module_id in world.get("enabledModules", [])
        ]
        for module_id in sorted(
            {
                module_id
                for world in used_worlds
                for module_id in world.get("enabledModules", [])
                if module_id in installed_modules_by_id
            }
        )
    }

    summary_rows: list[dict] = []
    module_rows: list[dict] = []

    world_system_ids = {str(world.get("system")) for world in used_worlds if world.get("system")}
    installed_system_ids = {str(system_id) for system_id in installed_systems_by_id.keys() if str(system_id)}
    for system_id in sorted(world_system_ids | installed_system_ids):
        system_record = installed_systems_by_id.get(system_id)
        if system_record is None:
            continue
        system_plan = _recommend_future_system_version(system_record, target_foundry, fetch_system_history)
        target_system_version = system_plan.get("recommendedVersion")
        if not target_system_version:
            continue

        world_aliases = sorted({_world_alias(world) for world in used_worlds if world.get("system") == system_id})
        impacted_modules = sorted(
            {
                module_id
                for module_id, world_refs in module_world_map.items()
                if any(world_ref.get("system") == system_id for world_ref in world_refs)
            }
        )
        resolution_cache: dict[str, Recommendation] = {}
        compatible_count = 0
        upgradable_count = 0
        blocked_count = 0
        ignored_count = 0
        added_module_ids: set[str] = set()
        dependency_rows_by_module: dict[str, dict] = {}

        for module_id in impacted_modules:
            module = installed_modules_by_id[module_id]
            recommendation, _ = resolve_module_recommendation(
                module,
                target_foundry,
                {system_id: str(target_system_version)},
                fetch_module_history,
                load_module_for_relationship,
                resolution_cache,
                None,
            )
            if _should_ignore_current_system_module(recommendation, system_id):
                ignored_count += 1
                max_foundry_supported, max_system_supported, max_system_on_target_foundry = _resolve_manual_module_bounds(
                    module,
                    system_id,
                    target_foundry,
                    fetch_module_history,
                )
                module_rows.append(
                    {
                        "systemId": system_id,
                        "systemTitle": system_plan.get("title") or system_id,
                        "systemInstalledVersion": system_record.version,
                        "systemTargetVersion": target_system_version,
                        "worldAliases": world_aliases,
                        "module": module.module_id,
                        "title": module.title,
                        "installedVersion": module.version,
                        "recommendedVersion": recommendation.recommended_version,
                        "status": "unknown",
                        "confidence": "low",
                        "reason": (
                            f"Resolved from local manifest only (no trusted remote release metadata); "
                            f"manual verification required for {system_id}."
                        ),
                        "manifestUrl": recommendation.manifest_url,
                        "downloadUrl": recommendation.download_url,
                        "compatibility": recommendation.compatibility,
                        "systemCompatibility": recommendation.system_compatibility,
                        "maxFoundrySupported": max_foundry_supported,
                        "maxSystemSupported": max_system_supported,
                        "maxSystemOnTargetFoundry": max_system_on_target_foundry,
                        "releasePublishedAt": recommendation.release_published_at,
                        "attentionFlag": False,
                        "forcedCompatibility": _forced_compatibility_payload(module),
                    }
                )
                continue
            status = _classify_current_system_module(
                module,
                recommendation,
                target_foundry,
                system_id,
                str(target_system_version),
            )
            forced_override_upgrade = _has_native_upgrade_for_forced_override(
                module,
                recommendation,
                target_foundry,
                system_id=system_id,
                target_system_version=str(target_system_version),
            )
            if status == "compatible":
                compatible_count += 1
            elif status == "upgradable":
                upgradable_count += 1
            else:
                blocked_count += 1
            added_module_ids.add(module.module_id)
            module_rows.append(
                {
                    "systemId": system_id,
                    "systemTitle": system_plan.get("title") or system_id,
                    "systemInstalledVersion": system_record.version,
                    "systemTargetVersion": target_system_version,
                    "worldAliases": world_aliases,
                    "module": module.module_id,
                    "title": module.title,
                    "installedVersion": module.version,
                    "recommendedVersion": recommendation.recommended_version,
                    "status": status,
                    "confidence": recommendation.confidence,
                    "reason": _current_system_module_reason(
                        module,
                        recommendation,
                        target_foundry,
                        system_id,
                        str(target_system_version),
                    ),
                    "manifestUrl": recommendation.manifest_url,
                    "downloadUrl": recommendation.download_url,
                    "compatibility": recommendation.compatibility,
                    "systemCompatibility": recommendation.system_compatibility,
                    "releasePublishedAt": recommendation.release_published_at,
                    "attentionFlag": (
                        _is_unbounded_attention_candidate(recommendation)
                        or bool(recommendation.attention_flag)
                        or forced_override_upgrade
                    ),
                    "forcedCompatibility": _forced_compatibility_payload(module),
                }
            )
            for dependency in recommendation.dependency_updates:
                dependency_id = str(dependency.module or "").strip()
                if not dependency_id or dependency_id in added_module_ids:
                    continue
                dependency_installed = str(dependency.installed_version or "").strip()
                dependency_recommended = str(dependency.recommended_version or "").strip()
                if not dependency_installed or not dependency_recommended:
                    continue
                if compare_versions(dependency_recommended, dependency_installed) <= 0:
                    continue
                existing_dependency_row = dependency_rows_by_module.get(dependency_id)
                dependency_record = installed_modules_by_id.get(dependency_id)
                dependency_title = (
                    (dependency_record.title if dependency_record else None)
                    or getattr(dependency, "title", None)
                    or dependency_id
                )
                dependency_reason = str(dependency.reason or "").strip()
                source_reason = (
                    f"Required by {module.module_id}: {dependency_reason}"
                    if dependency_reason
                    else f"Required by {module.module_id}: dependency upgrade required."
                )
                if existing_dependency_row is None:
                    dependency_rows_by_module[dependency_id] = {
                        "systemId": system_id,
                        "systemTitle": system_plan.get("title") or system_id,
                        "systemInstalledVersion": system_record.version,
                        "systemTargetVersion": target_system_version,
                        "worldAliases": world_aliases,
                        "module": dependency_id,
                        "title": dependency_title,
                        "installedVersion": dependency_installed,
                        "recommendedVersion": dependency_recommended,
                        "status": "upgradable",
                        "confidence": "medium",
                        "reason": source_reason,
                        "manifestUrl": dependency.manifest_url
                        or (dependency_record.manifest_url if dependency_record else None),
                        "downloadUrl": dependency.download_url,
                        "compatibility": dependency.compatibility or {},
                        "systemCompatibility": dependency.system_compatibility or {},
                        "releasePublishedAt": getattr(dependency, "release_published_at", None),
                        "attentionFlag": False,
                        "forcedCompatibility": _forced_compatibility_payload(dependency_record),
                    }
                    continue
                if (
                    dependency_recommended
                    and compare_versions(dependency_recommended, str(existing_dependency_row.get("recommendedVersion") or "")) > 0
                ):
                    existing_dependency_row["recommendedVersion"] = dependency_recommended
                    existing_dependency_row["manifestUrl"] = dependency.manifest_url or existing_dependency_row.get("manifestUrl")
                    existing_dependency_row["downloadUrl"] = dependency.download_url or existing_dependency_row.get("downloadUrl")
                    existing_dependency_row["compatibility"] = dependency.compatibility or existing_dependency_row.get("compatibility") or {}
                    existing_dependency_row["systemCompatibility"] = (
                        dependency.system_compatibility
                        or existing_dependency_row.get("systemCompatibility")
                        or {}
                    )
                current_reason = str(existing_dependency_row.get("reason") or "")
                if source_reason and source_reason not in current_reason:
                    existing_dependency_row["reason"] = (
                        f"{current_reason} | {source_reason}" if current_reason else source_reason
                    )

        if dependency_rows_by_module:
            dependency_rows = sorted(
                dependency_rows_by_module.values(),
                key=lambda row: str(row.get("module") or "").lower(),
            )
            module_rows.extend(dependency_rows)
            upgradable_count += len(dependency_rows)

        total_modules = compatible_count + upgradable_count + blocked_count
        coverage_percent = round(((compatible_count + upgradable_count) / total_modules * 100.0), 1) if total_modules else 100.0
        summary_rows.append(
            {
                "systemId": system_id,
                "title": system_plan.get("title") or system_id,
                "installedVersion": system_record.version,
                "targetVersion": target_system_version,
                "candidateVersions": system_plan.get("candidateVersions") or [],
                "manifestUrl": system_plan.get("manifestUrl"),
                "downloadUrl": system_plan.get("downloadUrl"),
                "compatibility": system_plan.get("compatibility") or {},
                "worldAliases": world_aliases,
                "modulesUsed": total_modules,
                "ignoredModules": ignored_count,
                "compatibleModules": compatible_count,
                "upgradableModules": upgradable_count,
                "blockedModules": blocked_count,
                "coveragePercent": coverage_percent,
            }
        )

    return {
        "currentSystemUpgradeSummary": summary_rows,
        "currentSystemUpgradeModules": module_rows,
    }


def _resolve_manual_module_bounds(
    module: ModuleRecord,
    system_id: str,
    target_foundry: str,
    fetch_module_history: HistoryFetcherWithLimit,
) -> tuple[str | None, str | None, str | None]:
    max_foundry_supported: str | None = None
    max_system_supported: str | None = None
    max_system_on_target_foundry: str | None = None

    releases, _ = fetch_module_history(module, 30)
    for release in releases:
        foundry_upper = _compatibility_upper_bound(release.compatibility or {})
        if foundry_upper:
            max_foundry_supported = _pick_higher_version(max_foundry_supported, foundry_upper)

        system_comp = (release.system_compatibility or {}).get(system_id) or {}
        system_upper = _compatibility_upper_bound(system_comp)
        if system_upper:
            max_system_supported = _pick_higher_version(max_system_supported, system_upper)
            if satisfies_release_constraints(release, target_foundry, {}):
                max_system_on_target_foundry = _pick_higher_version(max_system_on_target_foundry, system_upper)

    return max_foundry_supported, max_system_supported, max_system_on_target_foundry


def _compatibility_upper_bound(compatibility: dict) -> str | None:
    maximum = str(compatibility.get("maximum") or "").strip()
    verified = str(compatibility.get("verified") or "").strip()
    minimum = str(compatibility.get("minimum") or "").strip()
    if maximum:
        return maximum
    if verified:
        return verified
    if minimum:
        return minimum
    return None


def _pick_higher_version(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current:
        return candidate
    try:
        return candidate if compare_versions(candidate, current) > 0 else current
    except Exception:
        return candidate if candidate > current else current


def _recommend_future_system_version(
    system_record: ModuleRecord,
    target_foundry: str,
    fetch_system_history: HistoryFetcherWithLimit,
) -> dict:
    releases: list[ReleaseRecord] = []
    warnings: list[str] = []
    compatible: list[ReleaseRecord] = []
    for limit in (10, 20, 30):
        releases, warnings = fetch_system_history(system_record, limit)
        compatible = [
            release
            for release in sorted(
                releases,
                key=lambda item: _system_release_sort_key(item, target_foundry),
                reverse=True,
            )
            if satisfies_release_constraints(release, target_foundry, {})
        ]
        if compatible or limit == 30:
            break
    compatible_versions = [str(release.version or "").strip() for release in compatible if str(release.version or "").strip()]
    compatible_versions = list(dict.fromkeys(compatible_versions))
    chosen = compatible[0] if compatible else (releases[0] if releases else None)
    if chosen is None:
        return {
            "systemId": system_record.module_id,
            "installedVersion": system_record.version,
            "recommendedVersion": None,
            "candidateVersions": [],
            "status": "blocked",
            "source": None,
            "warnings": warnings,
        }
    status = "compatible" if compatible else "blocked"
    if chosen.version != system_record.version and compatible:
        status = "upgrade"
    return {
        "systemId": system_record.module_id,
        "title": system_record.title,
        "installedVersion": system_record.version,
        "recommendedVersion": chosen.version,
        "candidateVersions": compatible_versions,
        "source": chosen.source,
        "status": status,
        "manifestUrl": chosen.manifest_url,
        "downloadUrl": chosen.download_url,
        "compatibility": chosen.compatibility or {},
        "warnings": warnings,
    }


def _system_release_sort_key(release: ReleaseRecord, target_foundry: str) -> tuple:
    compatibility = release.compatibility or {}
    verified = compatibility.get("verified")
    verified_not_future = int(verified is not None and compare_versions(str(verified), target_foundry) <= 0)
    return (
        int(satisfies_release_constraints(release, target_foundry, {})),
        tuple(_normalize_version_parts(release.version)),
        verified_not_future,
        candidate_sort_key(release, target_foundry, {}),
    )


def _normalize_version_parts(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(version or "").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _evaluate_future_target(
    release: dict,
    used_worlds: list[dict],
    used_modules: list[ModuleRecord],
    module_world_map: dict[str, list[dict]],
    per_system: dict[str, dict],
    fetch_module_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
    unresolved_world_labels: list[str],
) -> dict:
    target_foundry = str(release.get("version") or "")
    target_system_versions = {
        system_id: details.get("recommendedVersion")
        for system_id, details in per_system.items()
        if details.get("recommendedVersion")
    }
    world_ids = [_world_label(world) for world in used_worlds]
    unresolved_worlds = [
        _world_label(world)
        for world in used_worlds
        if world.get("system") and per_system.get(world.get("system"), {}).get("status") == "blocked"
    ]

    module_rows = []
    ready = 0
    upgradable = 0
    blocked = 0
    unresolved_dependency_count = 0

    for module in used_modules:
        relevant_worlds = module_world_map.get(module.module_id, [])
        blocked_system_worlds = [
            world["id"]
            for world in relevant_worlds
            if world.get("system") and per_system.get(world.get("system"), {}).get("status") == "blocked"
        ]
        if blocked_system_worlds:
            blocked += 1
            module_rows.append(
                {
                    "module": module.module_id,
                    "title": module.title,
                    "installedVersion": module.version,
                    "recommendedVersion": None,
                    "status": "blocked",
                    "reason": f"Used by worlds whose target system could not be resolved: {', '.join(blocked_system_worlds)}.",
                    "confidence": "low",
                    "source": None,
                    "manifestUrl": None,
                    "downloadUrl": None,
                    "compatibility": module.raw_manifest.get("compatibility") or {},
                    "systemCompatibility": _extract_system_compatibility_from_module(module),
                    "forcedCompatibility": _forced_compatibility_payload(module),
                }
            )
            continue
        recommendation, _ = resolve_module_recommendation(
            module,
            target_foundry,
            target_system_versions,
            fetch_module_history,
            load_module_for_relationship,
            {},
        )
        status = _classify_future_module(module, recommendation, target_foundry)
        recommended_version = recommendation.recommended_version
        reason = recommendation.reason
        confidence = recommendation.confidence
        manifest_url = recommendation.manifest_url
        download_url = recommendation.download_url
        source = recommendation.source
        forced_override_upgrade = _has_native_upgrade_for_forced_override(
            module,
            recommendation,
            target_foundry,
        )
        if forced_override_upgrade:
            reason = (
                f"Forced compatibility override is active. A native upstream release "
                f"({recommended_version}) compatible with Foundry {target_foundry} is now available; "
                "upgrade to remove the local override."
            )
        if recommendation.confidence == "high" and recommendation.dependency_updates:
            dependency_update_summary = _summarize_dependency_updates(recommendation)
            if dependency_update_summary:
                reason = (
                    f"Compatible with Foundry {target_foundry}, but requires dependency updates: "
                    f"{dependency_update_summary}."
                )
        if recommendation.confidence == "medium":
            if _is_unbounded_attention_candidate(recommendation):
                age_fragment = _release_age_fragment(recommendation.release_published_at)
                age_text = f", updated {age_fragment}" if age_fragment else ""
                if status == "upgradable":
                    reason = (
                        f"Open-ended Foundry compatibility (no maximum bound), no unresolved dependencies{age_text}; "
                        f"module can be updated to {recommended_version} with attention."
                    )
                else:
                    reason = (
                        f"Open-ended Foundry compatibility (no maximum bound), no unresolved dependencies{age_text}; "
                        f"treated as ready with attention."
                    )
            unmet = _summarize_unsatisfied_dependencies(recommendation)
            if status == "upgradable" and not _is_unbounded_attention_candidate(recommendation):
                if unmet:
                    reason = (
                        f"Compatible with Foundry {target_foundry} after dependency updates: {unmet}."
                    )
                else:
                    reason = f"Compatible with Foundry {target_foundry} after dependency updates."
            elif unmet and not _is_unbounded_attention_candidate(recommendation):
                reason = (
                    f"Foundry {target_foundry} compatibility matches, but dependency requirements are not fully "
                    f"satisfied: {unmet}."
                )
        if recommended_version and compare_versions(recommended_version, module.version) < 0:
            recommended_version = module.version
            manifest_url = module.manifest_url
            download_url = module.raw_manifest.get("download")
            source = "rollback-suppressed-installed"
            if _installed_release_matches_foundry_target(module, target_foundry):
                status = "ready"
                reason = (
                    f"Rollback suggestion was suppressed; installed version {module.version} already matches "
                    f"Foundry {target_foundry} compatibility."
                )
                confidence = "medium"
            else:
                status = "blocked"
                reason = (
                    f"Installed version {module.version} does not satisfy Foundry {target_foundry} compatibility, and "
                    "no upgradable release was found."
                )
                confidence = "low"
        if status == "ready":
            ready += 1
        elif status == "upgradable":
            upgradable += 1
        elif status == "blocked":
            blocked += 1
        if status == "blocked" and recommendation.dependency_actions:
            unresolved_dependency_count += 1
        module_rows.append(
            {
                "module": module.module_id,
                "title": module.title,
                "installedVersion": module.version,
                "recommendedVersion": recommended_version,
                "status": status,
                "reason": reason,
                "confidence": confidence,
                "verifiedVersion": recommendation.verified_version,
                "futureTargetVersion": target_foundry,
                "source": source,
                "manifestUrl": manifest_url,
                "downloadUrl": download_url,
                "compatibility": recommendation.compatibility,
                "systemCompatibility": recommendation.system_compatibility,
                "releasePublishedAt": recommendation.release_published_at,
                "attentionFlag": (
                    _is_unbounded_attention_candidate(recommendation)
                    or bool(recommendation.attention_flag)
                    or forced_override_upgrade
                ),
                "forcedCompatibility": _forced_compatibility_payload(module),
            }
        )

    scored_total = ready + upgradable + blocked
    coverage = ((ready + upgradable) / scored_total * 100.0) if scored_total else 0.0
    blockers = [row["module"] for row in module_rows if row["status"] == "blocked"][:10]
    recommendation_label = _recommendation_label(coverage, blocked, unresolved_worlds, unresolved_world_labels)

    outcome_by_module: dict[str, dict] = {str(row.get("module")): row for row in module_rows if row.get("module")}
    system_compatibility_rows: list[dict] = []
    for system_id, details in sorted(per_system.items()):
        system_world_aliases = sorted(
            {
                _world_alias(world)
                for world in used_worlds
                if world.get("system") == system_id
            }
        )
        impacted_modules = sorted(
            {
                module.module_id
                for module in used_modules
                if any(world.get("system") == system_id for world in module_world_map.get(module.module_id, []))
            }
        )
        blocked_modules = sorted(
            {
                module_id
                for module_id in impacted_modules
                if (outcome_by_module.get(module_id) or {}).get("status") == "blocked"
            }
        )
        impacted_count = len(impacted_modules)
        blocked_count = len(blocked_modules)
        coverage_percent = round(((impacted_count - blocked_count) / impacted_count * 100.0), 1) if impacted_count else 100.0
        system_compatibility_rows.append(
            {
                "systemId": system_id,
                "title": details.get("title") or system_id,
                "installedVersion": details.get("installedVersion"),
                "recommendedVersion": details.get("recommendedVersion"),
                "candidateVersions": details.get("candidateVersions") or [],
                "status": details.get("status"),
                "compatibility": details.get("compatibility") or {},
                "modulesImpacted": impacted_count,
                "blockedModules": blocked_count,
                "coveragePercent": coverage_percent,
                "worldAliases": system_world_aliases,
                "impactedModuleIds": impacted_modules,
                "blockedModuleIds": blocked_modules,
            }
        )

    return {
        "targetFoundryVersion": target_foundry,
        "targetFoundryUrl": release.get("url"),
        "systems": [
            {
                "systemId": system_id,
                "title": details.get("title") or system_id,
                "installedVersion": details.get("installedVersion"),
                "recommendedVersion": details.get("recommendedVersion"),
                "candidateVersions": details.get("candidateVersions") or [],
                "status": details.get("status"),
                "compatibility": details.get("compatibility") or {},
            }
            for system_id, details in sorted(per_system.items())
        ],
        "worldsAffected": world_ids,
        "unresolvedWorlds": unresolved_worlds,
        "unresolvedWorldUsage": unresolved_world_labels,
        "usedModulesAnalyzed": len(used_modules),
        "readyCount": ready,
        "upgradableCount": upgradable,
        "blockedCount": blocked,
        "unresolvedDependencyCount": unresolved_dependency_count,
        "coveragePercent": round(coverage, 1),
        "recommendationLabel": recommendation_label,
        "blockers": blockers,
        "moduleOutcomes": module_rows,
        "systemCompatibility": system_compatibility_rows,
    }


def _classify_future_module(module: ModuleRecord, recommendation: Recommendation, target_foundry: str) -> str:
    if recommendation.source == "local-manifest":
        return "excluded-local-only"
    if _has_native_upgrade_for_forced_override(module, recommendation, target_foundry):
        return "upgradable"
    comparison = compare_versions(recommendation.recommended_version, module.version)
    has_dependency_upgrade = bool(recommendation.dependency_updates)
    if comparison < 0:
        if _installed_release_matches_foundry_target(module, target_foundry):
            return "upgradable" if has_dependency_upgrade else "ready"
        return "blocked"
    if recommendation.confidence == "high":
        if comparison == 0:
            return "upgradable" if has_dependency_upgrade else "ready"
        if comparison > 0:
            return "upgradable"
        return "blocked"
    if recommendation.confidence == "medium":
        if not _has_unresolved_dependency_constraints(recommendation):
            if comparison == 0:
                return "upgradable" if has_dependency_upgrade else "ready"
            if comparison > 0:
                return "upgradable"
            return "blocked"
        if _is_unbounded_attention_candidate(recommendation):
            return "upgradable" if comparison > 0 else "ready"
        if _medium_recommendation_has_upgrade_path(recommendation):
            return "upgradable"
        return "blocked"
    return "blocked"


def _classify_current_system_module(
    module: ModuleRecord,
    recommendation: Recommendation,
    target_foundry: str,
    system_id: str,
    target_system_version: str,
) -> str:
    if _has_native_upgrade_for_forced_override(
        module,
        recommendation,
        target_foundry,
        system_id=system_id,
        target_system_version=target_system_version,
    ):
        return "upgradable"
    comparison = compare_versions(recommendation.recommended_version, module.version)
    has_dependency_upgrade = bool(recommendation.dependency_updates)
    if comparison < 0:
        if _installed_release_matches_current_system_target(
            module,
            target_foundry,
            system_id,
            target_system_version,
        ):
            return "upgradable" if has_dependency_upgrade else "compatible"
        return "blocked"
    if recommendation.confidence == "high":
        if comparison == 0:
            return "upgradable" if has_dependency_upgrade else "compatible"
        return "upgradable"
    if recommendation.confidence == "medium":
        if not _has_unresolved_dependency_constraints(recommendation):
            if comparison == 0:
                return "upgradable" if has_dependency_upgrade else "compatible"
            if comparison > 0:
                return "upgradable"
            return "blocked"
        if _is_unbounded_attention_candidate(recommendation):
            return "upgradable" if comparison > 0 else "compatible"
        if _medium_recommendation_has_upgrade_path(recommendation):
            return "upgradable"
        return "blocked"
    if recommendation.confidence == "low" and _installed_release_matches_current_system_target(
        module,
        target_foundry,
        system_id,
        target_system_version,
    ):
        # Low-confidence fallbacks may point at future-targeted releases even when the
        # currently installed module is already compatible with the current Foundry+system target.
        if comparison > 0 and has_dependency_upgrade:
            return "upgradable"
        return "compatible"
    if (
        recommendation.confidence == "low"
        and recommendation.source == "local-manifest"
        and _local_manifest_matches_current_system_target(
            recommendation,
            target_foundry,
            system_id,
            target_system_version,
        )
    ):
        if comparison == 0:
            return "upgradable" if has_dependency_upgrade else "compatible"
        return "upgradable"
    if comparison == 0:
        return "blocked"
    return "blocked"


def _should_ignore_current_system_module(recommendation: Recommendation, system_id: str) -> bool:
    # Manual-only path: local-manifest entries are treated as unknown/manual in Current view.
    # Modules resolved from release catalogs without explicit system restrictions remain eligible.
    return recommendation.source == "local-manifest"


def _medium_recommendation_has_upgrade_path(recommendation: Recommendation) -> bool:
    unresolved_found = False
    for action in recommendation.dependency_actions:
        reason = str(action.reason or "").lower()
        is_unsatisfied = (
            action.recommended_version is None
            or "no recommended version satisfied" in reason
            or "could not be resolved" in reason
            or "rollback" in reason
        )
        if not is_unsatisfied:
            continue
        unresolved_found = True
        if action.recommended_version is None:
            return False
        if action.installed_version is None:
            continue
        if compare_versions(str(action.recommended_version), str(action.installed_version)) <= 0:
            return False
    return unresolved_found


def _has_unresolved_dependency_constraints(recommendation: Recommendation) -> bool:
    for action in recommendation.dependency_actions:
        reason = str(action.reason or "").lower()
        if action.recommended_version is None:
            return True
        if (
            "no recommended version satisfied" in reason
            or "could not be resolved" in reason
            or "rollback" in reason
        ):
            return True
        if action.installed_version is not None and compare_versions(
            str(action.recommended_version), str(action.installed_version)
        ) < 0:
            return True
    return bool(recommendation.missing_dependencies)


def _local_manifest_matches_current_system_target(
    recommendation: Recommendation,
    target_foundry: str,
    system_id: str,
    target_system_version: str,
) -> bool:
    if recommendation.source != "local-manifest":
        return False
    if recommendation.missing_dependencies:
        return False
    if not _compatibility_includes_target(recommendation.compatibility or {}, target_foundry):
        return False
    system_compatibility = (recommendation.system_compatibility or {}).get(system_id) or {}
    return _compatibility_includes_target(system_compatibility, target_system_version)


def _installed_release_matches_foundry_target(module: ModuleRecord, target_foundry: str) -> bool:
    compatibility = module.raw_manifest.get("compatibility") or {}
    return _compatibility_includes_target(compatibility, target_foundry)


def _installed_release_matches_current_system_target(
    module: ModuleRecord,
    target_foundry: str,
    system_id: str,
    target_system_version: str,
) -> bool:
    if not _installed_release_matches_foundry_target(module, target_foundry):
        return False
    system_compatibility = _extract_system_compatibility_from_module(module).get(system_id) or {}
    # No direct system declaration means no explicit system restriction.
    if not system_compatibility:
        return True
    return _compatibility_includes_target(system_compatibility, target_system_version)


def _compatibility_includes_target(compatibility: dict, target_version: str) -> bool:
    minimum = compatibility.get("minimum")
    maximum = compatibility.get("maximum")
    if minimum not in (None, "") and is_below_minimum(str(target_version), minimum):
        return False
    if maximum not in (None, "") and exceeds_maximum(str(target_version), maximum):
        return False
    return True


def _current_system_module_reason(
    module: ModuleRecord,
    recommendation: Recommendation,
    target_foundry: str,
    system_id: str,
    target_system_version: str,
) -> str:
    if _has_native_upgrade_for_forced_override(
        module,
        recommendation,
        target_foundry,
        system_id=system_id,
        target_system_version=target_system_version,
    ):
        return (
            f"Forced compatibility override is active. A native upstream release "
            f"({recommendation.recommended_version}) compatible with Foundry {target_foundry} and "
            f"system {system_id} {target_system_version} is now available; upgrade to remove the local override."
        )
    if _local_manifest_matches_current_system_target(
        recommendation,
        target_foundry,
        system_id,
        target_system_version,
    ):
        if compare_versions(recommendation.recommended_version, module.version) > 0:
            return (
                f"Local manifest declares compatibility with Foundry {target_foundry} and system {system_id} "
                f"{target_system_version}; module can move to {recommendation.recommended_version}, but upstream "
                f"release metadata should be verified."
            )
        return (
            f"Local manifest declares compatibility with Foundry {target_foundry} and system {system_id} "
            f"{target_system_version}; treated as compatible, but upstream release metadata should be verified."
        )
    if recommendation.confidence == "high":
        dependency_update_summary = _summarize_dependency_updates(recommendation)
        if dependency_update_summary:
            return (
                f"Compatible with current Foundry {target_foundry} and system upgrade {system_id} {target_system_version}, "
                f"but requires dependency updates: {dependency_update_summary}."
            )
        if compare_versions(recommendation.recommended_version, module.version) > 0:
            return (
                f"Compatible with current Foundry {target_foundry} and system upgrade {system_id} {target_system_version}; "
                f"module should move to {recommendation.recommended_version}."
            )
        return f"Compatible with current Foundry {target_foundry} and system upgrade {system_id} {target_system_version}."
    if recommendation.confidence == "medium":
        if not _has_unresolved_dependency_constraints(recommendation):
            if compare_versions(recommendation.recommended_version, module.version) > 0:
                return (
                    f"Compatibility range includes current Foundry {target_foundry} and system upgrade "
                    f"{system_id} {target_system_version}; module can move to {recommendation.recommended_version} "
                    f"with attention because verification metadata is older."
                )
            return (
                f"Compatibility range includes current Foundry {target_foundry} and system upgrade "
                f"{system_id} {target_system_version}; treated as compatible with attention because verification "
                f"metadata is older."
            )
        if _is_unbounded_attention_candidate(recommendation):
            age_fragment = _release_age_fragment(recommendation.release_published_at)
            age_text = f", updated {age_fragment}" if age_fragment else ""
            if compare_versions(recommendation.recommended_version, module.version) > 0:
                return (
                    f"Foundry/system compatibility is open-ended (no maximum bound) and no dependency conflicts were found{age_text}; "
                    f"module can move to {recommendation.recommended_version}, but should be monitored."
                )
            return (
                f"Foundry/system compatibility is open-ended (no maximum bound) and no dependency conflicts were found{age_text}; "
                f"treated as compatible with attention."
            )
        unmet = _summarize_unsatisfied_dependencies(recommendation)
        if _medium_recommendation_has_upgrade_path(recommendation):
            if unmet:
                return (
                    f"System and Foundry compatibility match; module can remain compatible for "
                    f"{system_id} {target_system_version} after dependency updates: {unmet}."
                )
            return (
                f"System and Foundry compatibility match; module is upgradable for system upgrade "
                f"{system_id} {target_system_version} after dependency updates."
            )
        if unmet:
            return (
                f"System and Foundry compatibility match, but dependency requirements are not fully satisfied "
                f"for {system_id} {target_system_version}: {unmet}."
            )
        return (
            f"Foundry {target_foundry} matches, but dependencies are not fully satisfied for system upgrade "
            f"{system_id} {target_system_version}."
        )
    if recommendation.confidence == "low" and _installed_release_matches_current_system_target(
        module,
        target_foundry,
        system_id,
        target_system_version,
    ):
        if compare_versions(recommendation.recommended_version, module.version) > 0:
            return (
                f"Installed version {module.version} is already compatible with current Foundry {target_foundry} "
                f"and system upgrade {system_id} {target_system_version}. A newer release "
                f"({recommendation.recommended_version}) was detected but targets a different compatibility window, "
                f"so the installed version is kept."
            )
        return (
            f"Installed version {module.version} is already compatible with current Foundry {target_foundry} "
            f"and system upgrade {system_id} {target_system_version}; no safe higher-confidence upgrade path is required."
        )
    if compare_versions(recommendation.recommended_version, module.version) < 0:
        return (
            f"System upgrade {system_id} {target_system_version} would require rollback "
            f"({module.version} -> {recommendation.recommended_version}), which is blocked."
        )
    return (
        f"No compatible release was found for current Foundry {target_foundry} with upgraded system "
        f"{system_id} {target_system_version}."
    )


def _is_unbounded_attention_candidate(recommendation: Recommendation) -> bool:
    if recommendation.source == "local-manifest":
        return False
    if recommendation.dependency_actions or recommendation.missing_dependencies or recommendation.dependency_updates:
        return False
    compatibility = recommendation.compatibility or {}
    maximum = str(compatibility.get("maximum") or "").strip()
    minimum = str(compatibility.get("minimum") or "").strip()
    verified = str(compatibility.get("verified") or "").strip()
    if maximum:
        return False
    if not minimum:
        return False
    if not verified:
        return False
    # This pattern usually means "legacy verified" without explicit upper bound.
    return compare_versions(verified, minimum) >= 0


def _has_forced_compatibility_override(module: ModuleRecord) -> bool:
    flags = module.raw_manifest.get("flags") if isinstance(module.raw_manifest.get("flags"), dict) else {}
    resolver_flags = flags.get("resolver") if isinstance(flags.get("resolver"), dict) else {}
    forced = resolver_flags.get("forcedCompatibility") if isinstance(resolver_flags.get("forcedCompatibility"), dict) else {}
    return bool(forced.get("enabled"))


def _forced_compatibility_payload(module: ModuleRecord | None) -> dict:
    if module is None:
        return {}
    flags = module.raw_manifest.get("flags") if isinstance(module.raw_manifest.get("flags"), dict) else {}
    resolver_flags = flags.get("resolver") if isinstance(flags.get("resolver"), dict) else {}
    forced = resolver_flags.get("forcedCompatibility") if isinstance(resolver_flags.get("forcedCompatibility"), dict) else {}
    if not bool(forced.get("enabled")):
        return {}
    return {
        "enabled": True,
        "targetVersion": str(forced.get("targetVersion") or ""),
        "appliedAt": str(forced.get("appliedAt") or ""),
    }


def _has_native_upgrade_for_forced_override(
    module: ModuleRecord,
    recommendation: Recommendation,
    target_foundry: str,
    system_id: str | None = None,
    target_system_version: str | None = None,
) -> bool:
    if not _has_forced_compatibility_override(module):
        return False
    if compare_versions(recommendation.recommended_version, module.version) <= 0:
        return False
    source = str(recommendation.source or "").strip().lower()
    if not source or source in {"local-manifest", "rollback-blocked-installed", "cycle-fallback"}:
        return False
    if str(recommendation.confidence or "").strip().lower() == "low":
        return False
    compatibility = recommendation.compatibility or {}
    if not _compatibility_includes_target(compatibility, target_foundry):
        return False
    if system_id and target_system_version:
        system_compatibility = (recommendation.system_compatibility or {}).get(system_id) or {}
        if system_compatibility and not _compatibility_includes_target(system_compatibility, target_system_version):
            return False
    return True


def _release_age_fragment(published_at: str | None) -> str:
    if not published_at:
        return ""
    try:
        parsed = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = max(int((now - parsed.astimezone(timezone.utc)).total_seconds() // 86400), 0)
    if days < 1:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def _summarize_unsatisfied_dependencies(recommendation: Recommendation, max_items: int = 3) -> str:
    unresolved: list[str] = []
    for action in recommendation.dependency_actions:
        reason = str(action.reason or "").lower()
        if action.recommended_version is None:
            unresolved.append(str(action.module))
            continue
        if "no recommended version satisfied" in reason or "could not be resolved" in reason or "rollback" in reason:
            unresolved.append(f"{action.module} ({action.installed_version or '-'} -> {action.recommended_version})")
    if not unresolved:
        return ""
    if len(unresolved) <= max_items:
        return ", ".join(unresolved)
    return f"{', '.join(unresolved[:max_items])} and {len(unresolved) - max_items} more"


def _summarize_dependency_updates(recommendation: Recommendation, max_items: int = 3) -> str:
    updates: list[str] = []
    for action in recommendation.dependency_updates:
        installed = str(action.installed_version or "-")
        target = str(action.recommended_version or "-")
        updates.append(f"{action.module} ({installed} -> {target})")
    if not updates:
        return ""
    if len(updates) <= max_items:
        return ", ".join(updates)
    return f"{', '.join(updates[:max_items])} and {len(updates) - max_items} more"


def _recommendation_label(coverage: float, blocked_count: int, unresolved_worlds: list[str], unresolved_world_usage: list[str]) -> str:
    if unresolved_world_usage:
        return f"Partial World Data (unresolved usage in: {_summarize_world_names(unresolved_world_usage)})"
    if unresolved_worlds:
        return f"Partial World Data (system plan unresolved for: {_summarize_world_names(unresolved_worlds)})"
    if blocked_count == 0 and coverage >= 100.0:
        return "Best Upgrade Now"
    if blocked_count <= 2 and coverage >= 90.0:
        return "Safest Upgrade"
    if coverage >= 70.0:
        return "Latest Reachable Stable"
    return "Not Recommended Yet"


def _world_label(world: dict) -> str:
    title = str(world.get("title") or "").strip()
    world_id = str(world.get("id") or "").strip()
    if title and world_id and title != world_id:
        return f"{title} ({world_id})"
    return title or world_id or "-"


def _world_alias(world: dict) -> str:
    world_id = str(world.get("id") or "").strip()
    if world_id:
        return world_id
    title = str(world.get("title") or "").strip()
    return title or "-"


def _summarize_world_names(world_names: list[str], max_items: int = 3) -> str:
    cleaned = [str(name).strip() for name in world_names if str(name).strip()]
    if not cleaned:
        return "-"
    if len(cleaned) <= max_items:
        return ", ".join(cleaned)
    remaining = len(cleaned) - max_items
    return f"{', '.join(cleaned[:max_items])} and {remaining} more"


def _extract_system_compatibility_from_module(module: ModuleRecord) -> dict[str, dict[str, str]]:
    relationships = (module.raw_manifest or {}).get("relationships") or {}
    compatibility_by_system: dict[str, dict[str, str]] = {}
    for item in relationships.get("systems") or []:
        system_id = item.get("id")
        if system_id:
            compatibility_by_system[str(system_id)] = item.get("compatibility") or {}
    return compatibility_by_system
