from __future__ import annotations

from .models import ReleaseRecord
from .versioning import compare_versions, exceeds_maximum, is_below_minimum, parse_version, version_distance, version_major


def satisfies_release_constraints(
    release: ReleaseRecord,
    target_version: str,
    installed_system_versions: dict[str, str],
) -> bool:
    compatibility = release.compatibility or {}
    minimum = compatibility.get("minimum")
    maximum = compatibility.get("maximum")
    if minimum not in (None, "") and _minimum_excludes_target(minimum, target_version):
        return False
    if maximum not in (None, "") and _maximum_excludes_target(maximum, target_version):
        return False
    if not _systems_are_compatible(release, installed_system_versions):
        return False
    return True


def candidate_sort_key(
    release: ReleaseRecord,
    target_version: str,
    installed_system_versions: dict[str, str],
) -> tuple:
    compatibility = release.compatibility or {}
    verified = compatibility.get("verified")
    target_major = version_major(target_version)
    verified_major = version_major(verified)

    valid = satisfies_release_constraints(release, target_version, installed_system_versions)
    same_major = int(verified_major is not None and verified_major == target_major)
    verified_not_future = int(verified is not None and compare_versions(verified, target_version) <= 0)
    has_verified = int(verified is not None)
    closeness = tuple(-part for part in version_distance(verified, target_version)) if verified is not None else tuple()
    release_version = tuple(release_version_part for release_version_part in _version_tuple(release.version))
    return (
        int(valid),
        release_version,
        same_major,
        verified_not_future,
        has_verified,
        closeness,
    )


def _version_tuple(version: str) -> tuple[int, ...]:
    from .versioning import parse_version

    return parse_version(version)


def explain_choice(
    release: ReleaseRecord,
    valid_releases: list[ReleaseRecord],
    all_releases: list[ReleaseRecord],
    target_version: str,
    installed_system_versions: dict[str, str],
) -> tuple[str, str]:
    compatibility = release.compatibility or {}
    verified = compatibility.get("verified")
    source = release.source
    if len(all_releases) == 1 and source == "local-manifest":
        return "Only local manifest was available, so the installed version was kept as fallback.", "low"
    if valid_releases:
        system_reason = _system_reason_fragment(release, installed_system_versions)
        if verified is not None and version_major(verified) == version_major(target_version):
            return f"Best compatible release with verified Foundry major matching {version_major(target_version)} from {source}{system_reason}.", "high"
        if verified is not None:
            return f"Compatible release chosen by closest verified version from {source}{system_reason}.", "medium"
        return f"Compatible release chosen using minimum/maximum constraints from {source}{system_reason}.", "medium"
    return "No compatible release passed the hard compatibility rules; best available fallback was returned.", "low"


def _systems_are_compatible(release: ReleaseRecord, installed_system_versions: dict[str, str]) -> bool:
    if not release.system_compatibility:
        return True
    for system_id, compatibility in release.system_compatibility.items():
        installed_version = installed_system_versions.get(system_id)
        if not installed_version:
            continue
        minimum = compatibility.get("minimum")
        maximum = compatibility.get("maximum")
        if minimum not in (None, "") and _minimum_excludes_target(minimum, installed_version):
            return False
        if maximum not in (None, "") and _maximum_excludes_target(maximum, installed_version):
            return False
    return True


def _system_reason_fragment(release: ReleaseRecord, installed_system_versions: dict[str, str]) -> str:
    for system_id, compatibility in release.system_compatibility.items():
        installed_version = installed_system_versions.get(system_id)
        if installed_version and compatibility:
            return f" with installed system {system_id} {installed_version} inside declared compatibility"
    return ""


def _minimum_excludes_target(minimum: str | int | float, target_version: str) -> bool:
    return is_below_minimum(target_version, minimum)


def _maximum_excludes_target(maximum: str | int | float, target_version: str) -> bool:
    return exceeds_maximum(target_version, maximum)
