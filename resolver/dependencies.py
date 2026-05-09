from __future__ import annotations

from typing import Callable

from .models import DependencyAction, ModuleRecord, ModuleRelationship, Recommendation, ReleaseRecord
from .versioning import compare_versions, exceeds_maximum, is_below_minimum
from .scoring import (
    candidate_sort_key,
    explain_choice,
    satisfies_release_constraints,
)

STALE_CACHE_WARNING_PREFIX = "Used stale release cache:"
MODULE_RELEASE_LIMIT_STEPS = (5,)
STOP_ON_FIRST_OPTIMAL_COMPATIBLE = True


HistoryFetcher = Callable[[ModuleRecord], tuple[list[ReleaseRecord], list[str]]]
HistoryFetcherWithLimit = Callable[[ModuleRecord, int], tuple[list[ReleaseRecord], list[str]]]
ModuleLoader = Callable[[ModuleRelationship], ModuleRecord | None]


def resolve_module_recommendation(
    module: ModuleRecord,
    target_version: str,
    installed_system_versions: dict[str, str],
    fetch_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
    resolution_cache: dict[str, Recommendation],
    active_stack: set[str] | None = None,
) -> tuple[Recommendation, dict[str, list[str]]]:
    if module.module_id in resolution_cache:
        return resolution_cache[module.module_id], {}

    active_stack = active_stack or set()
    if module.module_id in active_stack:
        recommendation = Recommendation(
            module=module.module_id,
            installed_version=module.version,
            recommended_version=module.version,
            reason="Dependency cycle detected; keeping the current version as fallback.",
            confidence="low",
            verified_version=None,
            manifest_url=module.manifest_url,
            download_url=module.raw_manifest.get("download"),
            source="cycle-fallback",
            checked_releases=0,
            compatibility=module.raw_manifest.get("compatibility") or {},
            system_compatibility={},
        )
        return recommendation, {module.module_id: ["Dependency cycle detected while resolving requirements."]}

    active_stack.add(module.module_id)
    warning_map: dict[str, list[str]] = {}
    compatible_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    fallback_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    releases: list[ReleaseRecord] = []
    for release_limit in MODULE_RELEASE_LIMIT_STEPS:
        releases, warnings = fetch_history(module, release_limit)
        if warnings:
            warning_map[module.module_id] = list(warnings)
        compatible_candidates, fallback_candidates = _evaluate_release_candidates(
            releases,
            target_version,
            installed_system_versions,
            fetch_history,
            load_module_for_relationship,
            resolution_cache,
            active_stack,
            warning_map,
            stop_on_first_compatible=STOP_ON_FIRST_OPTIMAL_COMPATIBLE,
        )
        if compatible_candidates or release_limit == MODULE_RELEASE_LIMIT_STEPS[-1]:
            break

    chosen_release: ReleaseRecord
    dependency_actions: list[DependencyAction]
    if compatible_candidates:
        chosen_release, dependency_actions, _ = compatible_candidates[0]
        reason, confidence = explain_choice(
            chosen_release,
            [item[0] for item in compatible_candidates],
            releases,
            target_version,
            installed_system_versions,
        )
    elif fallback_candidates:
        chosen_release, dependency_actions, _ = fallback_candidates[0]
        reason = "Module matches Foundry compatibility, but one or more required module dependencies could not be fully satisfied."
        confidence = "medium"
    else:
        sorted_releases = sorted(
            releases,
            key=lambda release: candidate_sort_key(release, target_version, installed_system_versions),
            reverse=True,
        )
        chosen_release = sorted_releases[0]
        dependency_actions = []
        reason = "No compatible release passed the hard compatibility rules; best available fallback was returned."
        confidence = "low"

    # Global policy: never recommend a rollback of the module itself.
    if compare_versions(chosen_release.version, module.version) < 0:
        downgraded_candidate_version = chosen_release.version
        chosen_release = _build_installed_release_record(module)
        dependency_actions = []
        reason = (
            f"Installed version {module.version} is newer than the best compatible catalog candidate "
            f"({downgraded_candidate_version}); rollback suggestions are suppressed."
        )
        confidence = "low"

    module_warning_list = warning_map.get(module.module_id) or []
    stale_cache_warning = _extract_stale_cache_warning(module_warning_list)
    attention_flag = False
    if stale_cache_warning:
        reason = f"{reason} {stale_cache_warning}"
        attention_flag = True

    dependency_updates = [action for action in dependency_actions if _is_upgrade_action(action)]
    missing_dependencies = [action for action in dependency_actions if action.recommended_version is None]
    recommendation = Recommendation(
        module=module.module_id,
        installed_version=module.version,
        recommended_version=chosen_release.version,
        reason=reason,
        confidence=confidence,
        verified_version=(chosen_release.compatibility or {}).get("verified"),
        manifest_url=chosen_release.manifest_url,
        download_url=chosen_release.download_url,
        source=chosen_release.source,
        checked_releases=len(releases),
        compatibility=chosen_release.compatibility or {},
        system_compatibility=chosen_release.system_compatibility or {},
        dependency_actions=dependency_actions,
        dependency_updates=dependency_updates,
        missing_dependencies=missing_dependencies,
        release_published_at=chosen_release.published_at,
        attention_flag=attention_flag,
    )
    resolution_cache[module.module_id] = recommendation
    active_stack.remove(module.module_id)
    return recommendation, warning_map


def _build_installed_release_record(module: ModuleRecord) -> ReleaseRecord:
    return ReleaseRecord(
        version=module.version,
        manifest_url=module.manifest_url,
        compatibility=module.raw_manifest.get("compatibility") or {},
        system_compatibility=_extract_system_compatibility_from_manifest(module.raw_manifest),
        module_requirements=_extract_module_requirements_from_manifest(module.raw_manifest),
        download_url=module.raw_manifest.get("download"),
        source="rollback-blocked-installed",
        raw_manifest=module.raw_manifest,
        published_at=None,
    )


def _extract_stale_cache_warning(warnings: list[str]) -> str | None:
    for warning in warnings:
        text = str(warning).strip()
        if text.startswith(STALE_CACHE_WARNING_PREFIX):
            return text
    return None


def _extract_system_compatibility_from_manifest(manifest: dict) -> dict[str, dict]:
    relationships = manifest.get("relationships") or {}
    systems = relationships.get("systems") or []
    compatibility_by_system: dict[str, dict] = {}
    for item in systems:
        system_id = item.get("id")
        if not system_id:
            continue
        compatibility_by_system[str(system_id)] = item.get("compatibility") or {}
    return compatibility_by_system


def _extract_module_requirements_from_manifest(manifest: dict) -> list[ModuleRelationship]:
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


def _resolve_release_dependencies(
    release: ReleaseRecord,
    target_version: str,
    installed_system_versions: dict[str, str],
    fetch_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
    resolution_cache: dict[str, Recommendation],
    active_stack: set[str],
) -> tuple[list[DependencyAction], dict[str, list[str]], bool]:
    actions: list[DependencyAction] = []
    warnings: dict[str, list[str]] = {}
    ok = True
    for requirement in release.module_requirements:
        if requirement.type != "module":
            continue
        required_module = load_module_for_relationship(requirement)
        if required_module is None:
            ok = False
            actions.append(
                DependencyAction(
                    module=requirement.module_id,
                    installed_version=None,
                    recommended_version=None,
                    reason="Required module is not installed and could not be resolved from available metadata.",
                    manifest_url=requirement.manifest_url,
                    compatibility={},
                    system_compatibility={},
                    download_url=None,
                )
            )
            warnings.setdefault(requirement.module_id, []).append(
                "Required dependency could not be resolved from installed modules or requirement manifest metadata."
            )
            continue

        dependency_recommendation, dependency_warnings = resolve_module_recommendation(
            required_module,
            target_version,
            installed_system_versions,
            fetch_history,
            load_module_for_relationship,
            resolution_cache,
            active_stack,
        )
        _merge_warning_maps(warnings, dependency_warnings)
        if not _recommendation_satisfies_requirement(dependency_recommendation, requirement):
            requirement_recommendation, requirement_warnings = _resolve_requirement_compatible_recommendation(
                required_module=required_module,
                requirement=requirement,
                target_version=target_version,
                installed_system_versions=installed_system_versions,
                fetch_history=fetch_history,
                load_module_for_relationship=load_module_for_relationship,
                resolution_cache=resolution_cache,
                active_stack=active_stack,
            )
            _merge_warning_maps(warnings, requirement_warnings)
            if requirement_recommendation is not None:
                dependency_recommendation = requirement_recommendation

        if not _recommendation_satisfies_requirement(dependency_recommendation, requirement):
            ok = False
            actions.append(
                DependencyAction(
                    module=requirement.module_id,
                    installed_version=required_module.version,
                    recommended_version=dependency_recommendation.recommended_version,
                    reason="Dependency could be resolved, but no recommended version satisfied the required compatibility range.",
                    manifest_url=dependency_recommendation.manifest_url,
                    compatibility=dependency_recommendation.compatibility,
                    system_compatibility=dependency_recommendation.system_compatibility,
                    download_url=dependency_recommendation.download_url,
                )
            )
            continue

        if requirement.compatibility and _would_require_rollback(required_module.version, dependency_recommendation.recommended_version):
            ok = False
            actions.append(
                DependencyAction(
                    module=requirement.module_id,
                    installed_version=required_module.version,
                    recommended_version=dependency_recommendation.recommended_version,
                    reason="Satisfying this dependency would require a rollback, which is not allowed by policy.",
                    manifest_url=dependency_recommendation.manifest_url,
                    compatibility=dependency_recommendation.compatibility,
                    system_compatibility=dependency_recommendation.system_compatibility,
                    download_url=dependency_recommendation.download_url,
                )
            )
            continue

        if required_module.version != dependency_recommendation.recommended_version or requirement.compatibility:
            actions.append(
                DependencyAction(
                    module=requirement.module_id,
                    installed_version=required_module.version,
                    recommended_version=dependency_recommendation.recommended_version,
                    reason=_dependency_reason(required_module.version, dependency_recommendation.recommended_version, requirement),
                    manifest_url=dependency_recommendation.manifest_url,
                    compatibility=dependency_recommendation.compatibility,
                    system_compatibility=dependency_recommendation.system_compatibility,
                    download_url=dependency_recommendation.download_url,
                )
            )
        for nested_action in dependency_recommendation.dependency_actions:
            if not any(action.module == nested_action.module for action in actions):
                actions.append(nested_action)
    return actions, warnings, ok


def _recommendation_satisfies_requirement(recommendation: Recommendation, requirement: ModuleRelationship) -> bool:
    compatibility = requirement.compatibility or {}
    minimum = compatibility.get("minimum")
    maximum = compatibility.get("maximum")

    if minimum not in (None, "") and is_below_minimum(recommendation.recommended_version, minimum):
        return False
    if maximum not in (None, "") and exceeds_maximum(recommendation.recommended_version, maximum):
        return False
    return True


def _release_satisfies_requirement(release: ReleaseRecord, requirement: ModuleRelationship) -> bool:
    compatibility = requirement.compatibility or {}
    minimum = compatibility.get("minimum")
    maximum = compatibility.get("maximum")
    if minimum not in (None, "") and is_below_minimum(release.version, minimum):
        return False
    if maximum not in (None, "") and exceeds_maximum(release.version, maximum):
        return False
    return True


def _resolve_requirement_compatible_recommendation(
    required_module: ModuleRecord,
    requirement: ModuleRelationship,
    target_version: str,
    installed_system_versions: dict[str, str],
    fetch_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
    resolution_cache: dict[str, Recommendation],
    active_stack: set[str],
) -> tuple[Recommendation | None, dict[str, list[str]]]:
    warning_map: dict[str, list[str]] = {}
    releases: list[ReleaseRecord] = []
    compatible_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    fallback_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    for release_limit in MODULE_RELEASE_LIMIT_STEPS:
        releases, warnings = fetch_history(required_module, release_limit)
        if warnings:
            warning_map[required_module.module_id] = list(warnings)
        compatible_candidates, fallback_candidates = _evaluate_release_candidates(
            releases,
            target_version,
            installed_system_versions,
            fetch_history,
            load_module_for_relationship,
            resolution_cache,
            active_stack,
            warning_map,
            stop_on_first_compatible=False,
        )
        if any(_release_satisfies_requirement(candidate[0], requirement) for candidate in compatible_candidates):
            break
        if release_limit == MODULE_RELEASE_LIMIT_STEPS[-1]:
            break

    for release, dependency_actions, _ in compatible_candidates:
        if not _release_satisfies_requirement(release, requirement):
            continue
        dependency_updates = [action for action in dependency_actions if _is_upgrade_action(action)]
        missing_dependencies = [action for action in dependency_actions if action.recommended_version is None]
        return (
            Recommendation(
                module=required_module.module_id,
                installed_version=required_module.version,
                recommended_version=release.version,
                reason="Dependency recommendation adjusted to satisfy declared compatibility for the parent module.",
                confidence="high",
                verified_version=(release.compatibility or {}).get("verified"),
                manifest_url=release.manifest_url,
                download_url=release.download_url,
                source=release.source,
                checked_releases=len(releases),
                compatibility=release.compatibility or {},
                system_compatibility=release.system_compatibility or {},
                dependency_actions=dependency_actions,
                dependency_updates=dependency_updates,
                missing_dependencies=missing_dependencies,
                release_published_at=release.published_at,
                attention_flag=False,
            ),
            warning_map,
        )

    # If no fully compatible candidate satisfies the requirement range, we intentionally
    # keep the previous recommendation so this dependency remains unresolved.
    return None, warning_map


def _dependency_reason(installed_version: str, recommended_version: str, requirement: ModuleRelationship) -> str:
    if not requirement.compatibility and installed_version != recommended_version:
        return f"Required dependency has a different best compatible version for this Foundry installation."
    if installed_version != recommended_version:
        return f"Dependency update required to satisfy {requirement.module_id} compatibility for the selected release."
    if requirement.compatibility:
        return f"Installed dependency already satisfies declared compatibility for {requirement.module_id}."
    return f"Dependency {requirement.module_id} is required by the selected release."


def _would_require_rollback(installed_version: str | None, recommended_version: str | None) -> bool:
    if not installed_version or not recommended_version:
        return False
    from .versioning import compare_versions

    return compare_versions(recommended_version, installed_version) < 0


def _is_upgrade_action(action: DependencyAction) -> bool:
    if action.recommended_version is None:
        return False
    if action.installed_version is None:
        return True
    from .versioning import compare_versions

    return compare_versions(action.recommended_version, action.installed_version) > 0


def _evaluate_release_candidates(
    releases: list[ReleaseRecord],
    target_version: str,
    installed_system_versions: dict[str, str],
    fetch_history: HistoryFetcherWithLimit,
    load_module_for_relationship: ModuleLoader,
    resolution_cache: dict[str, Recommendation],
    active_stack: set[str],
    warning_map: dict[str, list[str]],
    stop_on_first_compatible: bool = False,
) -> tuple[list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]], list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]]]:
    compatible_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    fallback_candidates: list[tuple[ReleaseRecord, list[DependencyAction], dict[str, list[str]]]] = []
    sorted_releases = sorted(
        releases,
        key=lambda release: candidate_sort_key(release, target_version, installed_system_versions),
        reverse=True,
    )
    for release in sorted_releases:
        release_ok = satisfies_release_constraints(release, target_version, installed_system_versions)
        dependency_actions, dependency_warnings, dependencies_ok = _resolve_release_dependencies(
            release,
            target_version,
            installed_system_versions,
            fetch_history,
            load_module_for_relationship,
            resolution_cache,
            active_stack,
        )
        _merge_warning_maps(warning_map, dependency_warnings)
        # A release that would force a dependency rollback is intentionally treated
        # as non-viable so we can keep searching older releases of the same module
        # and still prefer the newest release that works without downgrades.
        if release_ok and dependencies_ok:
            compatible_candidates.append((release, dependency_actions, dependency_warnings))
            if stop_on_first_compatible:
                return compatible_candidates, fallback_candidates
        elif release_ok:
            fallback_candidates.append((release, dependency_actions, dependency_warnings))
    return compatible_candidates, fallback_candidates


def _merge_warning_maps(target: dict[str, list[str]], source: dict[str, list[str]]) -> None:
    for key, values in source.items():
        bucket = target.setdefault(key, [])
        for value in values:
            if value not in bucket:
                bucket.append(value)
