from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from .versioning import compare_versions, version_major


def render_html_report(payload: dict) -> str:
    global _CURRENT_PAYLOAD
    _CURRENT_PAYLOAD = payload
    results = payload.get("results", [])
    upgrades = []
    rollbacks = []
    unchanged = []
    review_needed = []
    dependency_updates = []
    missing_dependencies = []

    for result in results:
        installed = result.get("installedVersion")
        recommended = result.get("recommendedVersion")
        if _is_upgrade(installed, recommended):
            upgrades.append(result)
        elif _is_downgrade(installed, recommended):
            rollbacks.append(result)
        elif installed == recommended:
            unchanged.append(result)
        else:
            review_needed.append(result)
        dependency_updates.extend(
            {**action, "owner": result.get("module"), "ownerTitle": result.get("title") or result.get("module")}
            for action in result.get("dependencyUpdates", [])
        )
        missing_dependencies.extend(
            {**action, "owner": result.get("module"), "ownerTitle": result.get("title") or result.get("module")}
            for action in result.get("missingDependencies", [])
        )

    summary_rows = [
        ("Generated At", _render_relative_time(payload.get("generatedAt"))),
        ("Foundry Version", payload.get("targetVersion")),
        ("Modules Analyzed", str(payload.get("moduleCount", 0))),
        ("Catalog Scans", str(((payload.get("databaseSummary") or {}).get("counts") or {}).get("scan_runs", 0))),
        ("Catalog Releases", str(((payload.get("databaseSummary") or {}).get("counts") or {}).get("package_releases", 0))),
        ("Catalog DB", _format_database_status(payload.get("databaseSummary") or {}, payload.get("databasePolicy") or {})),
        ("Cache Updated", _render_relative_time((payload.get("cacheStatus") or {}).get("newestAt"))),
        ("Cache", _format_cache_status(payload.get("cacheStatus") or {})),
        ("Dry Run", str(payload.get("dryRun", False))),
        ("Apply Mode", str(payload.get("apply", False))),
    ]

    warnings = payload.get("warnings", {})
    release_hint_map = _build_release_hint_map(payload)
    warnings_rows = []
    for module_id, values in sorted(warnings.items()):
        hint = release_hint_map.get(str(module_id), {})
        warnings_rows.append(
            {
                "module": module_id,
                "title": hint.get("title") or module_id,
                "details": values,
                "manifestUrl": hint.get("manifestUrl"),
                "downloadUrl": hint.get("downloadUrl"),
                "compatibility": hint.get("compatibility") or {},
                "systemCompatibility": hint.get("systemCompatibility") or {},
            }
        )
    future_foundry_releases = payload.get("futureFoundryReleases") or []
    future_upgrade_matrix = payload.get("futureUpgradeMatrix") or []

    future_sections = [
        _render_foundry_release_table(
            "Future Foundry Releases",
            "Official Foundry releases published after the currently installed version.",
            future_foundry_releases,
        ),
        _render_upgrade_decision_section(payload),
        _render_future_system_compatibility_section(payload),
        _render_hard_blockers_tabs(payload),
        _render_future_upgradable_section(payload),
    ]
    current_sections = [
        _render_result_table(
            "Recommended Updates",
            "Modules with a newer recommended version than the one installed locally.",
            upgrades,
            _build_module_commands(payload, upgrades, apply=True, allow_downgrade=False),
        ),
        _render_result_table(
            "Earlier Compatible Versions",
            "Modules whose locally installed version is newer, but a lower version was recommended to stay compatible with the current Foundry or base system state.",
            rollbacks,
            _build_module_commands(payload, rollbacks, apply=True, allow_downgrade=True),
        ),
        _render_result_table(
            "No Update Needed",
            "Modules already on the recommended version for this Foundry installation.",
            unchanged,
            _build_module_commands(payload, unchanged, apply=False, allow_downgrade=False),
        ),
        _render_current_system_upgrade_summary_section(payload),
        _render_current_system_upgrade_modules_section(payload),
        _render_result_table(
            "Needs Review",
            "Modules that did not receive a clean upgrade recommendation.",
            review_needed,
            _build_module_commands(payload, review_needed, apply=False, allow_downgrade=False),
        ),
        _render_dependency_table(
            "Dependency Updates",
            "Dependencies that should be upgraded to support the selected module versions.",
            dependency_updates,
            _build_dependency_commands(payload, dependency_updates, apply=True),
        ),
        _render_dependency_table(
            "Missing Dependencies",
            "Dependencies that could not be resolved automatically.",
            missing_dependencies,
            _build_dependency_commands(payload, missing_dependencies, apply=False),
        ),
        _render_warning_table(
            "Warnings",
            "Warnings gathered while fetching releases or resolving dependencies.",
            warnings_rows,
            _build_warning_commands(payload, warnings_rows),
        ),
    ]

    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Foundry Module Resolver Report</title>",
        "<style>",
        _STYLE,
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        "<h1>Foundry Module Resolver Report</h1>",
        "<section class=\"summary-grid\">",
    ]
    for label, value in summary_rows:
        value_html = value if "<time " in str(value) else escape(str(value or "-"))
        parts.append(
            f"<div class=\"summary-card\"><div class=\"summary-label\">{escape(label)}</div>"
            f"<div class=\"summary-value\">{value_html}</div></div>"
        )
    parts.extend(
        [
            "</section>",
            "<section class=\"tab-shell\">",
            "<div class=\"tab-nav\" role=\"tablist\" aria-label=\"Report sections\">",
            "<button class=\"tab-button active\" type=\"button\" role=\"tab\" aria-selected=\"true\" aria-controls=\"tab-current\" id=\"tab-button-current\" data-tab-target=\"current\">Current Compatibility</button>",
            "<button class=\"tab-button\" type=\"button\" role=\"tab\" aria-selected=\"false\" aria-controls=\"tab-future\" id=\"tab-button-future\" data-tab-target=\"future\">Future Upgrade</button>",
            "</div>",
            "<section class=\"tab-panel active\" id=\"tab-current\" role=\"tabpanel\" aria-labelledby=\"tab-button-current\" data-tab-panel=\"current\">",
            *current_sections,
            "</section>",
            "<section class=\"tab-panel\" id=\"tab-future\" role=\"tabpanel\" aria-labelledby=\"tab-button-future\" data-tab-panel=\"future\">",
            *future_sections,
            "</section>",
            "</section>",
            _SCRIPT,
            "</main>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(parts)


def _is_upgrade(installed_version: str | None, recommended_version: str | None) -> bool:
    if not installed_version or not recommended_version:
        return False
    return compare_versions(recommended_version, installed_version) > 0


def _is_downgrade(installed_version: str | None, recommended_version: str | None) -> bool:
    if not installed_version or not recommended_version:
        return False
    return compare_versions(recommended_version, installed_version) < 0


def _render_result_table(title: str, description: str, rows: list[dict], copy_command: str) -> str:
    headers = ["Module", "Version", "Reason", "Action"]
    rendered_rows = []
    for row in sorted(rows, key=lambda item: str(item.get("title") or item.get("module") or "").lower()):
        row_command = _build_single_module_command(row)
        detail = _append_compatibility_details(_systematic_reason(row, _CURRENT_PAYLOAD), row)
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'), row.get('confidence'))}</td>"
            f"<td>{escape(detail)}</td>"
            f"<td>{_render_inline_copy_button(row_command)}</td>"
            "</tr>"
        )
    return _render_table_block(title, description, headers, rendered_rows, copy_command)


def _render_foundry_release_table(title: str, description: str, rows: list[dict]) -> str:
    headers = ["Version", "Published", "Channel"]
    rendered_rows = []
    stable_rows = [row for row in rows if str(row.get("stability") or "").lower() == "stable"]
    for row in stable_rows:
        version = escape(str(row.get("version") or "-"))
        url = row.get("url")
        if url:
            version = f"<a class=\"module-link\" href=\"{escape(str(url))}\" target=\"_blank\" rel=\"noreferrer\">{version}</a>"
        rendered_rows.append(
            "<tr>"
            f"<td>{version}</td>"
            f"<td>{escape(str(row.get('publishedAt') or '-'))}</td>"
            f"<td>{escape(str(row.get('channel') or '-'))}</td>"
            "</tr>"
        )
    return _render_table_block(title, description, headers, rendered_rows, copy_command=None)


def _render_upgrade_decision_section(payload: dict) -> str:
    rows = payload.get("futureUpgradeMatrix") or []
    best = payload.get("bestFutureUpgradeTarget") or {}
    used_world_count = payload.get("usedWorldCount", 0)
    used_world_aliases = payload.get("usedWorldAliases") or []
    used_module_count = payload.get("usedModuleCount", 0)
    unresolved_world_usage = payload.get("unresolvedWorldUsage") or []
    summary_bits = [
        f"<div class=\"summary-card\"><div class=\"summary-label\">Worlds Evaluated</div><div class=\"summary-value\">{escape(str(used_world_count))} ({escape(_summarize_aliases(used_world_aliases, 4))})</div></div>",
        f"<div class=\"summary-card\"><div class=\"summary-label\">Modules Used</div><div class=\"summary-value\">{escape(str(used_module_count))}</div></div>",
    ]
    if best:
        summary_bits.extend(
            [
                f"<div class=\"summary-card\"><div class=\"summary-label\">Best Target</div><div class=\"summary-value\">{escape(str(best.get('targetFoundryVersion') or '-'))}</div></div>",
                f"<div class=\"summary-card\"><div class=\"summary-label\">% Upgradable</div><div class=\"summary-value\">{_render_coverage_badge(best.get('coveragePercent'))}</div></div>",
            ]
        )

    headers = ["Foundry", "System Plan", "Worlds", "Modules", "% Upgradable"]
    rendered_rows = []
    for row in rows:
        foundry_cell = escape(str(row.get("targetFoundryVersion") or "-"))
        target_url = row.get("targetFoundryUrl")
        if target_url:
            foundry_cell = f"<a class=\"module-link\" href=\"{escape(str(target_url))}\" target=\"_blank\" rel=\"noreferrer\">{foundry_cell}</a>"
        systems = row.get("systems") or []
        system_plan = "<br>".join(
            escape(
                _append_compatibility_details(
                    f"{system.get('systemId')}: {system.get('installedVersion') or '-'} -> {system.get('recommendedVersion') or '-'}",
                    system,
                )
            )
            for system in systems
        ) or "-"
        hard_blocked_count, verification_count = _future_blocker_counts(row)
        modules_cell = _render_modules_mix_cell(int(row.get("upgradableCount", 0)) + verification_count, hard_blocked_count)
        rendered_rows.append(
            "<tr>"
            f"<td>{foundry_cell}</td>"
            f"<td>{system_plan}</td>"
            f"<td>{escape(', '.join(row.get('worldsAffected') or [])) or '-'}</td>"
            f"<td>{modules_cell}</td>"
            f"<td>{_render_coverage_badge(row.get('coveragePercent'))}</td>"
            "</tr>"
        )

    if not rendered_rows:
        rendered_rows = [f"<tr><td colspan=\"{len(headers)}\" class=\"empty\">No entries.</td></tr>"]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_html = "\n".join(rendered_rows)
    table_meta = _render_table_meta(len(rows))
    unresolved_note = ""
    if unresolved_world_usage:
        unresolved_summary = ", ".join(str(name) for name in unresolved_world_usage[:5])
        if len(unresolved_world_usage) > 5:
            unresolved_summary = f"{unresolved_summary} and {len(unresolved_world_usage) - 5} more"
        unresolved_note = (
            f"<p class=\"section-note\">{escape(str(len(unresolved_world_usage)))} world entries without resolved module usage were excluded: {escape(unresolved_summary)}.</p>"
        )
    return (
        "<section class=\"report-section\" id=\"upgrade-decision\">"
        "<div class=\"section-head\"><div><h2>Upgrade Decision</h2><p>Foundry and system upgrades are evaluated together against world-enabled modules only.</p>"
        f"{unresolved_note}</div><div class=\"section-actions\"><button class=\"collapse-toggle\" type=\"button\" aria-expanded=\"true\">Collapse</button></div></div>"
        f"<div class=\"summary-grid\">{''.join(summary_bits)}</div>"
        f"{table_meta}"
        f"<div class=\"table-wrap\" data-row-count=\"{len(rows)}\">"
        f"<table class=\"paginated-table\"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
        "</div>"
        "</section>"
    )


def _render_future_system_compatibility_section(payload: dict) -> str:
    best = payload.get("bestFutureUpgradeTarget") or {}
    target_foundry = str(best.get("targetFoundryVersion") or "-")
    rows = best.get("systemCompatibility") or []
    headers = ["System", "Worlds", "Version Plan", "Modules Impacted", "Blocked", "% Upgradable"]
    rendered_rows = []
    for row in sorted(rows, key=lambda item: (int(item.get("blockedModules", 0)), str(item.get("systemId") or "")), reverse=True):
        system_label = _render_module_label(row.get("title"), row.get("systemId"))
        worlds_label = ", ".join(str(alias) for alias in (row.get("worldAliases") or [])) or "-"
        version_plan = (
            f"{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'))}"
            f"<div class=\"cell-note\">{escape(_compatibility_details(row) or '-')}</div>"
        )
        rendered_rows.append(
            "<tr>"
            f"<td>{system_label}</td>"
            f"<td>{escape(worlds_label)}</td>"
            f"<td>{version_plan}</td>"
            f"<td>{escape(str(row.get('modulesImpacted') or 0))}</td>"
            f"<td>{escape(str(row.get('blockedModules') or 0))}</td>"
            f"<td>{_render_coverage_badge(row.get('coveragePercent'))}</td>"
            "</tr>"
        )
    return _render_table_block(
        "System Compatibility",
        f"Compatibility summary per installed system for the best future Foundry target ({target_foundry}).",
        headers,
        rendered_rows,
        copy_command=None,
    )


def _render_current_system_upgrade_summary_section(payload: dict) -> str:
    rows = payload.get("currentSystemUpgradeSummary") or []
    headers = ["System", "Worlds", "Version Plan", "Modules Used", "Compatible", "Upgradable", "Blocked", "% Upgradable"]
    rendered_rows = []
    for row in sorted(rows, key=lambda item: (float(item.get("coveragePercent") or 0), str(item.get("systemId") or "")), reverse=True):
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_label(row.get('title'), row.get('systemId'))}</td>"
            f"<td>{escape(', '.join(row.get('worldAliases') or [])) or '-'}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('targetVersion'))}<div class=\"cell-note\">{escape(_compatibility_details(row) or '-')}</div></td>"
            f"<td>{escape(str(row.get('modulesUsed') or 0))}</td>"
            f"<td><span class=\"modules-mix modules-compatible\">{escape(str(row.get('compatibleModules') or 0))}</span></td>"
            f"<td><span class=\"modules-mix modules-upgradable\">{escape(str(row.get('upgradableModules') or 0))}</span></td>"
            f"<td><span class=\"modules-mix modules-blocked\">{escape(str(row.get('blockedModules') or 0))}</span></td>"
            f"<td>{_render_coverage_badge(row.get('coveragePercent'))}</td>"
            "</tr>"
        )
    return _render_table_block(
        "System Upgrade Summary",
        "World-enabled modules checked against system upgrades that are available on the current Foundry version. If this table is empty, no non-rollback system upgrade was found for the current Foundry build.",
        headers,
        rendered_rows,
        copy_command=None,
    )


def _render_current_system_upgrade_modules_section(payload: dict) -> str:
    rows = payload.get("currentSystemUpgradeModules") or []
    headers = ["System Plan", "Worlds", "Module", "Version", "Status", "Reason", "Action"]
    rendered_rows = []
    command_rows = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("systemId") or ""),
            {"blocked": 0, "upgradable": 1, "compatible": 2}.get(str(item.get("status") or ""), 9),
            str(item.get("title") or item.get("module") or "").lower(),
        ),
    ):
        if row.get("status") == "blocked":
            continue
        if _is_upgrade(row.get("installedVersion"), row.get("recommendedVersion")):
            command_rows.append(row)
        system_plan = f"{row.get('systemId')}: {row.get('systemInstalledVersion') or '-'} -> {row.get('systemTargetVersion') or '-'}"
        row_command = (
            _build_single_module_command(row)
            if _is_upgrade(row.get("installedVersion"), row.get("recommendedVersion"))
            else _build_cli_command(_CURRENT_PAYLOAD, ["--module", str(row.get("module")), "--dry-run"])
        )
        rendered_rows.append(
            "<tr>"
            f"<td>{escape(system_plan)}</td>"
            f"<td>{escape(', '.join(row.get('worldAliases') or [])) or '-'}</td>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'))}</td>"
            f"<td>{_render_status_badge(row.get('status'))}</td>"
            f"<td>{escape(str(row.get('reason') or '-'))}</td>"
            f"<td>{_render_inline_copy_button(row_command)}</td>"
            "</tr>"
        )
    return _render_table_block(
        "Modules Compatible With System Upgrades",
        "Only modules used in worlds are included. Rows here remain compatible when the base system is upgraded on the current Foundry version. If empty, there is no current system upgrade path without rollback.",
        headers,
        rendered_rows,
        _build_module_commands(payload, command_rows, apply=True, allow_downgrade=False),
    )


def _render_hard_blockers_tabs(payload: dict) -> str:
    foundry_rows = _collect_hard_blocker_rows(payload, "foundry")
    system_rows = _collect_hard_blocker_rows(payload, "system")
    headers = ["Module", "Version", "Reason"]
    foundry_table = _render_inline_table_instance(headers, foundry_rows, reason_renderer=_render_hard_blocker_reason)
    system_table = _render_inline_table_instance(headers, system_rows, reason_renderer=_render_hard_blocker_reason)
    return (
        "<section class=\"report-section\" id=\"hard-blockers\">"
        "<div class=\"section-head\"><div><h2>Hard Blockers</h2><p>Modules without an upgradable path split by compatibility boundary.</p></div>"
        "<div class=\"section-actions\"><button class=\"collapse-toggle\" type=\"button\" aria-expanded=\"true\">Collapse</button></div></div>"
        "<div class=\"subtab-nav\" role=\"tablist\" aria-label=\"Hard blocker categories\">"
        "<button class=\"subtab-button active\" type=\"button\" role=\"tab\" aria-selected=\"true\" data-subtab-target=\"foundry\">Foundry Version</button>"
        "<button class=\"subtab-button\" type=\"button\" role=\"tab\" aria-selected=\"false\" data-subtab-target=\"system\">Systems</button>"
        "</div>"
        "<div class=\"subtab-panel active\" data-subtab-panel=\"foundry\">"
        f"{foundry_table}"
        "</div>"
        "<div class=\"subtab-panel\" data-subtab-panel=\"system\">"
        f"{system_table}"
        "</div>"
        "</section>"
    )


def _collect_hard_blocker_rows(payload: dict, blocker_type: str) -> list[dict]:
    best = payload.get("bestFutureUpgradeTarget") or {}
    outcomes = best.get("moduleOutcomes") or []
    rows = []
    for row in outcomes:
        if row.get("status") != "blocked":
            continue
        if _blocker_bucket(row) != "hard":
            continue
        category = _hard_blocker_category(row)
        if blocker_type == "system" and category == "system":
            rows.append(row)
        elif blocker_type == "foundry" and category != "system":
            rows.append(row)
    return rows


def _render_inline_table_instance(headers: list[str], rows: list[dict], reason_renderer=None) -> str:
    rendered_rows = []
    for row in sorted(rows, key=lambda item: str(item.get("title") or item.get("module") or "").lower()):
        reason = str(row.get("reason") or "-")
        if reason_renderer:
            reason = reason_renderer(row)
        reason = _append_compatibility_details(reason, row)
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'))}</td>"
            f"<td>{escape(reason)}</td>"
            "</tr>"
        )
    row_count = len(rendered_rows)
    if not rendered_rows:
        rendered_rows = [f"<tr><td colspan=\"{len(headers)}\" class=\"empty\">No entries.</td></tr>"]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_html = "\n".join(rendered_rows)
    table_meta = _render_table_meta(row_count)
    return (
        "<div class=\"table-instance\">"
        f"{table_meta}"
        f"<div class=\"table-wrap\" data-row-count=\"{row_count}\">"
        f"<table class=\"paginated-table\"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
        "</div>"
        "</div>"
    )


def _render_upgrade_blockers_section(payload: dict, blocker_type: str) -> str:
    best = payload.get("bestFutureUpgradeTarget") or {}
    outcomes = best.get("moduleOutcomes") or []
    blocked_rows = []
    for row in outcomes:
        if row.get("status") != "blocked":
            continue
        if _blocker_bucket(row) != "hard":
            continue
        category = _hard_blocker_category(row)
        if blocker_type == "system" and category == "system":
            blocked_rows.append(row)
        elif blocker_type == "foundry" and category != "system":
            blocked_rows.append(row)
    headers = ["Module", "Version", "Reason"]
    rendered_rows = []
    for row in sorted(blocked_rows, key=lambda item: str(item.get("title") or item.get("module") or "").lower()):
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'))}</td>"
            f"<td>{escape(_append_compatibility_details(str(row.get('reason') or '-'), row))}</td>"
            "</tr>"
        )
    if blocker_type == "system":
        title = "Hard Blockers - System"
        description = "Modules blocked due to unresolved/incompatible target system lines."
    else:
        title = "Hard Blockers - Foundry"
        description = "Modules blocked by Foundry compatibility limits or no upgradable path for the target Foundry release."
    return _render_table_block(
        title,
        description,
        headers,
        rendered_rows,
        copy_command=None,
    )


def _render_future_upgradable_section(payload: dict) -> str:
    best = payload.get("bestFutureUpgradeTarget") or {}
    outcomes = best.get("moduleOutcomes") or []
    rows = [
        row
        for row in outcomes
        if row.get("status") == "upgradable" or (row.get("status") == "blocked" and _blocker_bucket(row) == "verification")
    ]
    headers = ["Module", "Version", "Reason"]
    rendered_rows = []
    for row in sorted(rows, key=lambda item: str(item.get("title") or item.get("module") or "").lower()):
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'), row.get('confidence'))}</td>"
            f"<td>{escape(_append_compatibility_details(str(row.get('reason') or '-'), row))}</td>"
            "</tr>"
        )
    return _render_table_block(
        "Upgradable Modules",
        "Modules that have a plausible upgrade path for the best future target, including entries that still need compatibility confirmation.",
        headers,
        rendered_rows,
        copy_command=None,
    )


def _blocker_bucket(row: dict) -> str:
    reason = str(row.get("reason") or "").lower()
    confidence = str(row.get("confidence") or "").lower()
    installed = row.get("installedVersion")
    recommended = row.get("recommendedVersion")
    verified = row.get("verifiedVersion")
    future_target = row.get("futureTargetVersion")
    if "no compatible release passed the hard compatibility rules" in reason:
        return "hard"
    if "could not be resolved" in reason or "rollback" in reason:
        return "hard"
    if installed and recommended and compare_versions(recommended, installed) < 0:
        return "hard"
    if verified and future_target and version_major(verified) != version_major(future_target):
        return "hard"
    if confidence == "low":
        return "hard"
    return "verification"


def _format_generated_at(value: str | None) -> str:
    if not value:
        return "-"
    try:
        generated_at = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - generated_at.astimezone(timezone.utc)
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        relative = "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        relative = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        relative = f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = seconds // 86400
        relative = f"{days} day{'s' if days != 1 else ''} ago"
    absolute = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{relative} ({absolute})"


def _format_absolute_utc(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _render_relative_time(value: str | None) -> str:
    if not value:
        return "-"
    absolute = _format_absolute_utc(value)
    escaped_iso = escape(str(value))
    escaped_absolute = escape(absolute)
    return (
        f"<time class=\"js-relative-time\" datetime=\"{escaped_iso}\" title=\"{escaped_absolute}\">"
        f"{escaped_absolute}</time>"
    )


def _render_dependency_table(title: str, description: str, rows: list[dict], copy_command: str) -> str:
    headers = ["Requested By", "Dependency", "Version", "Reason", "Action"]
    rendered_rows = []
    for row in sorted(rows, key=lambda item: (str(item.get("ownerTitle") or item.get("owner") or "").lower(), str(item.get("title") or item.get("module") or "").lower())):
        row_command = _build_single_dependency_command(row)
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_label(row.get('ownerTitle'), row.get('owner'))}</td>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{_render_version_cell(row.get('installedVersion'), row.get('recommendedVersion'))}</td>"
            f"<td>{escape(_append_compatibility_details(str(row.get('reason') or '-'), row))}</td>"
            f"<td>{_render_inline_copy_button(row_command)}</td>"
            "</tr>"
        )
    return _render_table_block(title, description, headers, rendered_rows, copy_command)


def _render_warning_table(title: str, description: str, rows: list[dict], copy_command: str) -> str:
    rendered_rows = []
    for row in rows:
        row_command = _build_single_warning_command(row)
        rendered_rows.append(
            "<tr>"
            f"<td>{_render_module_link(row.get('title'), row.get('module'), row)}</td>"
            f"<td>{escape(_append_compatibility_details('; '.join(row.get('details') or []), row))}</td>"
            f"<td>{_render_inline_copy_button(row_command)}</td>"
            "</tr>"
        )
    return _render_table_block(title, description, ["Module", "Details", "Action"], rendered_rows, copy_command)


def _render_table_block(title: str, description: str, headers: list[str], rows: list[str], copy_command: str | None) -> str:
    row_count = len(rows)
    has_rows = row_count > 0
    if not rows:
        rows = [f"<tr><td colspan=\"{len(headers)}\" class=\"empty\">No entries.</td></tr>"]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_html = "\n".join(rows)
    button_html = ""
    if copy_command:
        disabled_attr = "" if has_rows else " disabled"
        disabled_class = "" if has_rows else " is-disabled"
        button_html = (
            f"<button class=\"copy-button{disabled_class}\" data-command={json.dumps(copy_command)} type=\"button\"{disabled_attr}>Copy Console Command</button>"
        )
    actions_html = "<div class=\"section-actions\">"
    if button_html:
        actions_html += button_html
    actions_html += "<button class=\"collapse-toggle\" type=\"button\" aria-expanded=\"true\">Collapse</button></div>"
    section_id = _slugify(title)
    table_meta = _render_table_meta(row_count)
    return (
        f"<section class=\"report-section\" id=\"{escape(section_id)}\">"
        f"<div class=\"section-head\"><div><h2>{escape(title)}</h2><p>{escape(description)}</p></div>{actions_html}</div>"
        f"{table_meta}"
        f"<div class=\"table-wrap\" data-row-count=\"{row_count}\">"
        f"<table class=\"paginated-table\"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
        f"</div></section>"
    )


def _render_table_meta(row_count: int) -> str:
    label = "row" if row_count == 1 else "rows"
    page_size = 10
    total_pages = max((row_count + page_size - 1) // page_size, 1)
    prev_disabled = " disabled"
    next_disabled = " disabled" if total_pages <= 1 else ""
    return (
        "<div class=\"table-meta\">"
        f"<div class=\"table-summary\">{row_count} {label}</div>"
        f"<div class=\"table-pager\" data-page-size=\"{page_size}\">"
        f"<button class=\"pager-button\" type=\"button\" data-page-action=\"prev\"{prev_disabled}>Previous</button>"
        "<span class=\"pager-status\">Page 1 of 1</span>"
        f"<button class=\"pager-button\" type=\"button\" data-page-action=\"next\"{next_disabled}>Next</button>"
        "</div>"
        "</div>"
    )


def _future_blocker_counts(row: dict) -> tuple[int, int]:
    hard = 0
    verification = 0
    for item in row.get("moduleOutcomes") or []:
        if item.get("status") != "blocked":
            continue
        if _blocker_bucket(item) == "hard":
            hard += 1
        else:
            verification += 1
    return hard, verification


def _hard_blocker_category(row: dict) -> str:
    reason = str(row.get("reason") or "").lower()
    verified = row.get("verifiedVersion")
    future_target = row.get("futureTargetVersion")
    if "target system could not be resolved" in reason:
        return "system"
    if "used by worlds whose target system could not be resolved" in reason:
        return "system"
    if verified and future_target and version_major(verified) != version_major(future_target):
        return "foundry"
    if "installed system " in reason:
        return "system"
    if "system compatibility" in reason:
        return "system"
    return "foundry"


def _render_hard_blocker_reason(row: dict) -> str:
    reason = str(row.get("reason") or "-")
    installed = row.get("installedVersion")
    recommended = row.get("recommendedVersion")
    verified = row.get("verifiedVersion")
    future_target = row.get("futureTargetVersion")
    reason_lower = reason.lower()

    if "target system could not be resolved" in reason_lower or "used by worlds whose target system could not be resolved" in reason_lower:
        return (
            "Blocked by system planning: at least one world uses a system version that could not be mapped "
            "to a compatible target release for this Foundry upgrade."
        )

    if verified and future_target and version_major(verified) != version_major(future_target):
        system_note = _extract_system_constraint_fragment(reason)
        fragment = (
            f"No upgradable compatible release is verified for Foundry {future_target}. "
            f"Best candidate stays on compatibility line verified up to Foundry {verified}."
        )
        if system_note:
            fragment += f" System constraint satisfied: {system_note}."
        return fragment

    if installed and recommended and compare_versions(recommended, installed) < 0:
        return (
            f"Would require rollback ({installed} -> {recommended}) to keep compatibility, "
            "and rollback suggestions are blocked for future upgrades."
        )

    if "no upgradable release was found" in reason_lower or "no compatible upgradable release was found" in reason_lower:
        if future_target:
            return (
                f"No upgradable compatible release was found for Foundry {future_target}. "
                "Keeping the installed version because rollback suggestions are blocked."
            )
        return (
            "No upgradable compatible release was found. "
            "Keeping the installed version because rollback suggestions are blocked."
        )

    if "no compatible release passed the hard compatibility rules" in reason_lower:
        return (
            "No release satisfied hard compatibility rules for the target Foundry/system combination "
            "without requiring a rollback."
        )

    return reason




def _slugify(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "section"


def _build_module_commands(payload: dict, rows: list[dict], apply: bool, allow_downgrade: bool) -> str:
    if not rows:
        return "printf '%s\n' 'No entries in this table.'"
    modules = []
    expected_versions = []
    for row in rows:
        module_id = row.get("module")
        recommended = row.get("recommendedVersion")
        if not module_id:
            continue
        modules.extend(["--module", str(module_id)])
        if recommended:
            expected_versions.extend(["--expected-version", f"{module_id}={recommended}"])
    flags = ["--apply"] if apply else ["--dry-run"]
    if allow_downgrade and rows:
        flags.append("--allow-downgrade")
    return _build_cli_command(payload, modules + flags + expected_versions)


def _build_dependency_commands(payload: dict, rows: list[dict], apply: bool) -> str:
    if not rows:
        return "printf '%s\n' 'No entries in this table.'"
    seen: set[str] = set()
    modules = []
    expected_versions = []
    for row in rows:
        module_id = row.get("module")
        recommended = row.get("recommendedVersion")
        if not module_id or module_id in seen:
            continue
        seen.add(str(module_id))
        modules.extend(["--module", str(module_id)])
        if recommended:
            expected_versions.extend(["--expected-version", f"{module_id}={recommended}"])
    flags = ["--apply"] if apply else ["--dry-run"]
    return _build_cli_command(payload, modules + flags + expected_versions)


def _build_warning_commands(payload: dict, rows: list[dict]) -> str:
    if not rows:
        return "printf '%s\n' 'No entries in this table.'"
    modules = []
    for row in rows:
        module_id = row.get("module")
        if module_id:
            modules.extend(["--module", str(module_id)])
    return _build_cli_command(payload, modules + ["--dry-run"])


def _build_single_module_command(row: dict) -> str:
    module_id = row.get("module")
    recommended = row.get("recommendedVersion")
    installed = row.get("installedVersion")
    extra_args = ["--module", str(module_id)]
    if _is_downgrade(installed, recommended):
        extra_args.append("--allow-downgrade")
    extra_args.append("--apply" if recommended and installed != recommended else "--dry-run")
    if module_id and recommended:
        extra_args.extend(["--expected-version", f"{module_id}={recommended}"])
    return _build_cli_command(_CURRENT_PAYLOAD, extra_args)


def _build_single_dependency_command(row: dict) -> str:
    module_id = row.get("module")
    recommended = row.get("recommendedVersion")
    extra_args = ["--module", str(module_id)]
    extra_args.append("--apply" if recommended else "--dry-run")
    if module_id and recommended:
        extra_args.extend(["--expected-version", f"{module_id}={recommended}"])
    return _build_cli_command(_CURRENT_PAYLOAD, extra_args)


def _build_single_warning_command(row: dict) -> str:
    module_id = row.get("module")
    return _build_cli_command(_CURRENT_PAYLOAD, ["--module", str(module_id), "--dry-run"])


def _render_confidence(confidence: str | None) -> str:
    text = str(confidence or "-")
    slug = text.lower()
    return f"<span class=\"confidence confidence-{escape(slug)}\">{escape(text)}</span>"

def _render_module_link(title: str | None, module_id: str | None, row: dict) -> str:
    label = _render_module_label(title, module_id)
    href = _release_link_for_row(row)
    if not href:
        return label
    return f"<a class=\"module-link\" href=\"{escape(href)}\" target=\"_blank\" rel=\"noreferrer\">{label}</a>"


def _release_link_for_row(row: dict) -> str | None:
    for candidate in (row.get("downloadUrl"), row.get("manifestUrl")):
        release_url = _release_link_from_url(candidate)
        if release_url:
            return release_url
    return row.get("manifestUrl") or row.get("downloadUrl")


def _release_link_from_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url)
    if "github.com" in text and "/releases/download/" in text:
        prefix, _, suffix = text.partition("/releases/download/")
        tag = suffix.split("/", 1)[0]
        return f"{prefix}/releases/tag/{tag}"
    if "gitlab.com" in text and "/-/raw/" in text:
        prefix, _, suffix = text.partition("/-/raw/")
        tag = suffix.split("/", 1)[0]
        return f"{prefix}/-/tags/{tag}"
    if "raw.githubusercontent.com" in text:
        parsed = urlparse(text)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3:
            owner, repo, tag = parts[0], parts[1], parts[2]
            return f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
    return None


def _render_inline_copy_button(command: str) -> str:
    return f"<button class=\"copy-button copy-button-inline\" data-command={json.dumps(command)} type=\"button\">Copy</button>"


def _render_module_label(title: str | None, module_id: str | None) -> str:
    label = escape(str(title or module_id or "-"))
    if module_id and title and title != module_id:
        return f"<span title={json.dumps(str(module_id))}>{label}</span>"
    return label


def _render_version_cell(installed_version: str | None, recommended_version: str | None, confidence: str | None = None) -> str:
    installed = escape(str(installed_version or "-"))
    recommended = escape(str(recommended_version or "-"))
    version_text = f"{installed} -&gt; {recommended}" if installed != recommended else installed
    if confidence:
        return f"<div class=\"version-stack\"><div>{version_text}</div><div>{_render_confidence(confidence)}</div></div>"
    return version_text


def _append_compatibility_details(base: str, row: dict) -> str:
    compatibility_text = _compatibility_details(row)
    if not compatibility_text:
        return base
    return f"{base} Compatibility: {compatibility_text}."


def _compatibility_details(row: dict) -> str:
    fragments = []
    foundry_fragment = _format_foundry_compatibility(row.get("compatibility") or {})
    if foundry_fragment:
        fragments.append(f"Foundry {foundry_fragment}")
    system_fragment = _format_system_compatibility(row.get("systemCompatibility") or {})
    if system_fragment:
        fragments.append(system_fragment)
    return "; ".join(fragments)


def _format_foundry_compatibility(compatibility: dict) -> str:
    if not compatibility:
        return ""
    minimum = _normalize_compat_value(compatibility.get("minimum"))
    verified = _normalize_compat_value(compatibility.get("verified"))
    maximum = _normalize_compat_value(compatibility.get("maximum"))
    if minimum and maximum:
        if minimum == maximum:
            return f"{minimum}.X" if minimum.isdigit() else minimum
        if maximum.isdigit():
            return f"{minimum} - {maximum}.X"
        return f"{minimum} - {maximum}"
    if minimum and verified and minimum != verified:
        verified_text = f"{verified}.X" if verified.isdigit() else verified
        return f"{minimum} - {verified_text}"
    if minimum:
        return f"{minimum}.X" if minimum.isdigit() else minimum
    if verified:
        return f"verified {verified}.X" if verified.isdigit() else f"verified {verified}"
    if maximum:
        return f"up to {maximum}.X" if maximum.isdigit() else f"up to {maximum}"
    return ""


def _format_system_compatibility(system_compatibility: dict) -> str:
    fragments = []
    for system_id, compatibility in sorted(system_compatibility.items()):
        compat_text = _format_foundry_compatibility(compatibility or {})
        if compat_text:
            fragments.append(f"System {system_id} {compat_text}")
        else:
            fragments.append(f"System {system_id}")
    return "; ".join(fragments)


def _normalize_compat_value(value) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _render_coverage_badge(percent: float | int | str | None) -> str:
    try:
        value = float(percent if percent is not None else 0)
    except (TypeError, ValueError):
        value = 0.0
    if value >= 80:
        css = "coverage-high"
    elif value >= 60:
        css = "coverage-medium"
    else:
        css = "coverage-low"
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"<span class=\"coverage-badge {css}\">{escape(text)}%</span>"


def _render_status_badge(status: str | None) -> str:
    value = str(status or "-").strip().lower()
    css = {
        "compatible": "status-compatible",
        "upgradable": "status-upgradable",
        "blocked": "status-blocked",
    }.get(value, "status-blocked")
    label = {
        "compatible": "Compatible",
        "upgradable": "Needs module upgrade",
        "blocked": "Blocked",
    }.get(value, value or "-")
    return f"<span class=\"status-badge {css}\">{escape(label)}</span>"


def _format_cache_status(status: dict) -> str:
    if not status or not status.get("fileCount"):
        return "empty"
    file_count = int(status.get("fileCount") or 0)
    total_bytes = int(status.get("totalBytes") or 0)
    stale = bool(status.get("isStale"))
    stale_text = "stale" if stale else "fresh"
    return f"{file_count} files, {_format_bytes(total_bytes)} ({stale_text})"


def _format_database_status(summary: dict, policy: dict) -> str:
    if not summary:
        return "not created"
    file_bytes = int(summary.get("fileBytes") or 0)
    scans = int(((summary.get("counts") or {}).get("scan_runs")) or 0)
    max_scans = int(policy.get("maxScanRuns") or 0)
    size_text = _format_bytes(file_bytes)
    if max_scans > 0:
        return f"{size_text}, {scans}/{max_scans} scans kept"
    return f"{size_text}, {scans} scans kept"


def _format_bytes(total_bytes: int) -> str:
    value = float(max(total_bytes, 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(total_bytes)} B"


def _summarize_aliases(values: list[str], max_items: int = 4) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return "-"
    if len(cleaned) <= max_items:
        return ", ".join(cleaned)
    remaining = len(cleaned) - max_items
    return f"{', '.join(cleaned[:max_items])} +{remaining}"


def _render_modules_mix_cell(upgradable_count: int, blocked_count: int) -> str:
    return (
        f"<span class=\"modules-mix modules-upgradable\">{escape(str(upgradable_count))} upgradable</span> / "
        f"<span class=\"modules-mix modules-blocked\">{escape(str(blocked_count))} blocked</span>"
    )


def _extract_system_constraint_fragment(reason: str) -> str | None:
    marker = "with installed system "
    if marker not in reason:
        return None
    fragment = reason.split(marker, 1)[1]
    fragment = fragment.split(" inside declared compatibility", 1)[0].strip()
    return fragment or None


def _systematic_reason(row: dict, payload: dict) -> str:
    confidence = str(row.get("confidence") or "").lower()
    foundry_version = str(payload.get("targetVersion") or "-")
    if confidence == "high":
        system_fragment = _matched_system_fragment(row, payload)
        if system_fragment:
            return f"New version matches Foundry {foundry_version} and dependent system {system_fragment}."
        return f"New version matches Foundry {foundry_version}."
    if confidence == "medium":
        requirements = _dependency_requirement_summary(row)
        if requirements:
            return f"Matches Foundry {foundry_version}, but still requires: {requirements}."
        return f"Matches Foundry {foundry_version}, but dependency requirements are not fully satisfied."
    return str(row.get("reason") or "-")


def _matched_system_fragment(row: dict, payload: dict) -> str | None:
    reason = str(row.get("reason") or "")
    marker = "installed system "
    if marker in reason:
        fragment = reason.split(marker, 1)[1]
        return fragment.split(" inside declared compatibility", 1)[0].strip()
    installed_systems = payload.get("installedSystemVersions") or {}
    if len(installed_systems) == 1:
        system_id, version = next(iter(installed_systems.items()))
        return f"{system_id} {version}"
    return None


def _dependency_requirement_summary(row: dict) -> str:
    requirements = []
    seen = set()
    for action in (row.get("dependencyActions") or []) + (row.get("dependencyUpdates") or []) + (row.get("missingDependencies") or []):
        module_id = str(action.get("module") or "")
        required_version = action.get("recommendedVersion")
        reason = str(action.get("reason") or "").lower()
        if not module_id or module_id in seen:
            continue
        if "already satisfies" in reason:
            continue
        seen.add(module_id)
        if required_version:
            requirements.append(f"{module_id} {required_version}")
        else:
            requirements.append(module_id)
    return ", ".join(requirements)


def _build_release_hint_map(payload: dict) -> dict[str, dict]:
    hints: dict[str, dict] = {}

    def add_row(row: dict) -> None:
        module_id = str(row.get("module") or "").strip()
        if not module_id:
            return
        existing = hints.get(module_id, {})
        title = row.get("title") or existing.get("title") or module_id
        manifest_url = row.get("manifestUrl") or existing.get("manifestUrl")
        download_url = row.get("downloadUrl") or existing.get("downloadUrl")
        compatibility = row.get("compatibility") or existing.get("compatibility") or {}
        system_compatibility = row.get("systemCompatibility") or existing.get("systemCompatibility") or {}
        hints[module_id] = {
            "title": title,
            "manifestUrl": manifest_url,
            "downloadUrl": download_url,
            "compatibility": compatibility,
            "systemCompatibility": system_compatibility,
        }

    for row in payload.get("results", []) or []:
        add_row(row)
        for dep in row.get("dependencyActions", []) or []:
            add_row(dep)
        for dep in row.get("dependencyUpdates", []) or []:
            add_row(dep)
        for dep in row.get("missingDependencies", []) or []:
            add_row(dep)

    best = payload.get("bestFutureUpgradeTarget") or {}
    for row in best.get("moduleOutcomes", []) or []:
        add_row(row)

    for module_id, hint in (payload.get("databasePackageHints") or {}).items():
        existing = hints.get(str(module_id), {})
        hints[str(module_id)] = {
            "title": existing.get("title") or hint.get("title") or module_id,
            "manifestUrl": existing.get("manifestUrl") or hint.get("manifestUrl"),
            "downloadUrl": existing.get("downloadUrl") or hint.get("downloadUrl"),
            "compatibility": existing.get("compatibility") or hint.get("compatibility") or {},
            "systemCompatibility": existing.get("systemCompatibility") or hint.get("systemCompatibility") or {},
        }

    return hints


def _build_cli_command(payload: dict, extra_args: list[str]) -> str:
    tool_root = str(payload.get("toolRoot") or ".")
    data_root = str(payload.get("dataRoot") or ".")
    base_args = ["python3", "-m", "resolver.cli", "--data-root", data_root]
    left = shlex.join(["cd", tool_root])
    right = shlex.join([*base_args, *extra_args])
    return f"{left} && {right}"


_CURRENT_PAYLOAD: dict = {}


_STYLE = """
:root {
  color-scheme: light;
  --bg: #f5f2e8;
  --panel: #fffdf8;
  --ink: #1f2a2e;
  --muted: #5e6b70;
  --line: #d7c9af;
  --accent: #b55d2d;
  --accent-soft: #f0d9c7;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(circle at top left, #f9ead9 0, transparent 28%),
    linear-gradient(180deg, #fbf8f1 0, var(--bg) 100%);
  color: var(--ink);
  font: 16px/1.45 Georgia, "Times New Roman", serif;
}
main {
  max-width: 1440px;
  margin: 0 auto;
  padding: 32px 20px 56px;
}
h1, h2 { margin: 0 0 10px; line-height: 1.1; }
h1 { font-size: clamp(2rem, 4vw, 3.2rem); }
h2 { font-size: 1.5rem; color: var(--accent); }
p { margin: 0 0 14px; color: var(--muted); }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin: 24px 0 32px;
}
.summary-card, .report-section {
  background: color-mix(in srgb, var(--panel) 92%, white 8%);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 10px 30px rgba(71, 47, 18, 0.06);
}
.summary-card { padding: 16px; }
.summary-label {
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.summary-value {
  margin-top: 6px;
  font-size: 1.35rem;
  font-weight: 700;
}
.report-section { padding: 18px; margin-top: 18px; }
.section-head {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.section-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.copy-button {
  border: 0;
  border-radius: 999px;
  background: var(--accent);
  color: #fff8f0;
  padding: 10px 14px;
  font: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.collapse-toggle {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--ink);
  padding: 9px 14px;
  font: inherit;
  cursor: pointer;
}
.collapse-toggle:hover { filter: brightness(1.02); }
.report-section.collapsed .table-meta,
.report-section.collapsed .table-wrap {
  display: none;
}
.report-section.collapsed .subtab-nav {
  display: none;
}
.copy-button:hover { filter: brightness(1.05); }
.copy-button.copied { background: #2f7a4d; }
.copy-button.is-disabled,
.copy-button[disabled] {
  opacity: 0.5;
  cursor: default;
  filter: none;
}
.copy-button-inline {
  padding: 7px 11px;
  font-size: 0.92rem;
}
.tab-shell {
  margin-top: 8px;
}
.tab-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 0 0 18px;
}
.tab-button {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: color-mix(in srgb, var(--panel) 92%, white 8%);
  color: var(--ink);
  padding: 10px 16px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 10px 24px rgba(71, 47, 18, 0.05);
}
.tab-button:hover {
  filter: brightness(1.02);
}
.tab-button.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff8f0;
}
.tab-panel {
  display: none;
}
.tab-panel.active {
  display: block;
}
.subtab-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 12px;
}
.subtab-button {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--ink);
  padding: 8px 14px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.subtab-button.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: #3d2414;
}
.subtab-panel { display: none; }
.subtab-panel.active { display: block; }
.section-note {
  margin-top: 6px;
}
.table-meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin: 0 0 10px;
}
.table-summary {
  color: var(--muted);
  font-size: 0.96rem;
}
.table-pager {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pager-button {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--ink);
  padding: 6px 12px;
  font: inherit;
  cursor: pointer;
}
.pager-button[disabled] {
  opacity: 0.45;
  cursor: default;
}
.pager-status {
  color: var(--muted);
  font-size: 0.94rem;
}
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td {
  padding: 10px 12px;
  border-top: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
thead th {
  border-top: 0;
  background: var(--accent-soft);
  color: #3d2414;
  position: sticky;
  top: 0;
}
tbody tr:nth-child(even) td { background: rgba(240, 217, 199, 0.18); }
.cell-note {
  margin-top: 4px;
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.35;
}
.version-stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 120px;
}
.confidence {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 700;
  text-transform: capitalize;
}
.confidence-high {
  background: #d7f4df;
  color: #116b31;
}
.confidence-medium {
  background: #d9ebff;
  color: #0f4c9b;
}
.confidence-low {
  background: #f7d3d7;
  color: #9a2132;
}
.coverage-badge {
  display: inline-block;
  min-width: 64px;
  text-align: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-weight: 700;
}
.coverage-high {
  background: #d7f4df;
  color: #116b31;
}
.coverage-medium {
  background: #d9ebff;
  color: #0f4c9b;
}
.coverage-low {
  background: #f7d3d7;
  color: #9a2132;
}
.modules-mix {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 0.9rem;
}
.modules-compatible {
  background: #d7f4df;
  color: #116b31;
}
.modules-upgradable {
  background: #d9ebff;
  color: #0f4c9b;
}
.modules-blocked {
  background: #f7d3d7;
  color: #9a2132;
}
.status-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 700;
}
.status-compatible {
  background: #d7f4df;
  color: #116b31;
}
.status-upgradable {
  background: #d9ebff;
  color: #0f4c9b;
}
.status-blocked {
  background: #f7d3d7;
  color: #9a2132;
}
.module-link {
  color: #15616d;
  text-decoration: none;
  font-weight: 700;
}
.module-link:hover { text-decoration: underline; }
.inline-link {
  color: #15616d;
  font-weight: 700;
  text-decoration: none;
}
.inline-link:hover { text-decoration: underline; }
.modules-preview {
  margin-top: 6px;
  color: var(--muted);
  font-size: 0.92rem;
  line-height: 1.35;
}
.empty {
  text-align: center;
  color: var(--muted);
  font-style: italic;
}
@media (max-width: 720px) {
  main { padding: 24px 12px 40px; }
  .report-section { padding: 14px; }
  .section-head { flex-direction: column; }
  .table-meta { flex-direction: column; align-items: flex-start; }
  .copy-button { width: 100%; }
  th, td { padding: 9px 10px; font-size: 0.94rem; }
}
"""


_SCRIPT = """
<script>
const DEFAULT_PAGE_SIZE = 10;

function refreshPager(section) {
  const pagers = Array.from(section.querySelectorAll(".table-pager"));
  pagers.forEach((pager) => {
    const host = pager.closest(".table-instance") || section;
    const wrap = host.querySelector(".table-wrap");
    const table = wrap?.querySelector(".paginated-table");
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tbody tr")).filter((row) => !row.querySelector(".empty"));
    const total = rows.length;
    const pageSize = Number(pager.dataset.pageSize || DEFAULT_PAGE_SIZE);
    const totalPages = Math.max(Math.ceil(total / pageSize), 1);
    const currentPage = Math.min(Number(pager.dataset.currentPage || 1), totalPages);
    pager.dataset.currentPage = String(currentPage);
    rows.forEach((row, index) => {
      const start = (currentPage - 1) * pageSize;
      const end = start + pageSize;
      row.style.display = index >= start && index < end ? "" : "none";
    });
    const prev = pager.querySelector('[data-page-action="prev"]');
    const next = pager.querySelector('[data-page-action="next"]');
    const status = pager.querySelector(".pager-status");
    if (status) status.textContent = `Page ${currentPage} of ${totalPages}`;
    if (prev) prev.disabled = currentPage <= 1 || total === 0;
    if (next) next.disabled = currentPage >= totalPages || total === 0;
  });
}

function initializePagers() {
  document.querySelectorAll(".report-section").forEach((section) => {
    refreshPager(section);
  });
}

document.addEventListener("click", async (event) => {
  const tabButton = event.target.closest(".tab-button");
  if (tabButton) {
    const target = tabButton.dataset.tabTarget || "";
    document.querySelectorAll(".tab-button").forEach((button) => {
      const isActive = button === tabButton;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.tabPanel === target);
    });
    return;
  }
  const pagerButton = event.target.closest(".pager-button");
  if (pagerButton) {
    const pager = pagerButton.closest(".table-pager");
    const section = pagerButton.closest(".report-section");
    if (!pager || !section) return;
    const currentPage = Number(pager.dataset.currentPage || 1);
    const direction = pagerButton.dataset.pageAction === "next" ? 1 : -1;
    pager.dataset.currentPage = String(Math.max(currentPage + direction, 1));
    refreshPager(section);
    return;
  }
  const subtabButton = event.target.closest(".subtab-button");
  if (subtabButton) {
    const section = subtabButton.closest(".report-section");
    if (!section) return;
    const target = subtabButton.dataset.subtabTarget || "";
    section.querySelectorAll(".subtab-button").forEach((button) => {
      const isActive = button === subtabButton;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    section.querySelectorAll(".subtab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.subtabPanel === target);
    });
    refreshPager(section);
    return;
  }
  const collapseButton = event.target.closest(".collapse-toggle");
  if (collapseButton) {
    const section = collapseButton.closest(".report-section");
    if (!section) return;
    const collapsed = section.classList.toggle("collapsed");
    collapseButton.textContent = collapsed ? "Expand" : "Collapse";
    collapseButton.setAttribute("aria-expanded", collapsed ? "false" : "true");
    return;
  }
  const button = event.target.closest(".copy-button");
  if (!button) return;
  const command = button.dataset.command || "";
  try {
    await navigator.clipboard.writeText(command);
    const original = button.textContent;
    button.textContent = "Copied";
    button.classList.add("copied");
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("copied");
    }, 1400);
  } catch (error) {
    console.error(error);
    button.textContent = "Copy Failed";
  }
});

initializePagers();

function formatRelativeTime(isoText) {
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return null;
  const now = new Date();
  const seconds = Math.max(Math.floor((now.getTime() - date.getTime()) / 1000), 0);
  if (seconds < 60) return "just now";
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function refreshRelativeTimes() {
  document.querySelectorAll(".js-relative-time").forEach((node) => {
    const iso = node.getAttribute("datetime") || "";
    const relative = formatRelativeTime(iso);
    const absolute = node.getAttribute("title") || iso;
    if (!relative) {
      node.textContent = absolute;
      return;
    }
    node.textContent = `${relative} (${absolute})`;
  });
}

refreshRelativeTimes();
window.setInterval(refreshRelativeTimes, 60000);
</script>
"""
