from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape


def render_html_report_v2(payload: dict) -> str:
    view = ((payload.get("reportViews") or {}).get("v2")) or {}
    current_system_upgrades = view.get("currentSystemUpgrades") or {}
    backup_management = view.get("backupManagement") or {}
    unused_modules = view.get("unusedModules") or {}
    planner = view.get("systemUpgradePlanner") or {}
    summary = view.get("summary") or {}
    cache_status = view.get("cacheStatus") or {}
    foundry_service = view.get("foundryServiceStatus") or {}
    foundry_options = (view.get("controls") or {}).get("foundryOptions") or []
    default_foundry = (view.get("controls") or {}).get("defaultFoundryVersion") or ""
    used_modules = int(summary.get("usedModuleCount") or 0)
    total_modules = int(summary.get("moduleCount") or 0)
    modules_referenced = f"{used_modules} / {total_modules}"
    backup_disk = backup_management.get("diskStatus") or {}
    disk_used_percent = float(backup_disk.get("usedPercent") or 0.0)
    disk_free_percent = float(backup_disk.get("freePercent") or 0.0)
    disk_summary = f"{disk_used_percent:.1f}% used / {disk_free_percent:.1f}% free"
    current_rows = current_system_upgrades.get("rows") or []
    mobile_upgrade_count = sum(int(row.get("upgradableModules") or 0) for row in current_rows)
    mobile_blocked_count = sum(int(row.get("blockedModules") or 0) for row in current_rows)
    mobile_manual_count = sum(int(row.get("ignoredModules") or 0) for row in current_rows)
    mobile_ready_count = sum(int(row.get("compatibleModules") or 0) for row in current_rows)
    target_sections = [
        _render_planner_target(target)
        for target in planner.get("targets") or []
    ]

    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Foundry Module Resolver Report v2</title>",
        "<style>",
        _STYLE,
        "</style>",
        "</head>",
        "<body>",
        "<main class=\"page\">",
        "<header class=\"page-header\">",
        "<div>",
        "<p class=\"eyebrow\">Resolver Views v2</p>",
        "<h1>Foundry Upgrade Planner</h1>",
        "<p class=\"lede\">Separate the decisions: current-compatible module upgrades on one side, system and Foundry upgrade planning on the other.</p>",
        "</div>",
        "<div class=\"header-tools\">",
        f"<div class=\"generated-at\" data-generated-at=\"{escape(str(view.get('generatedAt') or ''))}\">Generated {_render_relative_time(view.get('generatedAt'))}</div>",
        _render_foundry_status(foundry_service),
        (
            f"<div class=\"cache-meta\" "
            f"data-cache-newest-at=\"{escape(str(cache_status.get('newestAt') or ''))}\" "
            f"data-cache-files=\"{escape(str(cache_status.get('fileCount') or 0))}\" "
            f"data-cache-bytes=\"{escape(str(cache_status.get('totalBytes') or 0))}\">"
            "Cache: -"
            "</div>"
        ),
        "<button id=\"theme-toggle\" class=\"theme-toggle\" type=\"button\" aria-pressed=\"false\" aria-label=\"Toggle light/dark mode\" title=\"Toggle theme\">☾</button>",
        "</div>",
        "</header>",
        "<section class=\"top-grid\">",
        _render_summary_card("Foundry Version", escape(str(view.get("currentFoundryVersion") or "-"))),
        _render_summary_card("Worlds", escape(str(summary.get("usedWorldCount") or 0))),
        _render_summary_card("Modules Referenced", escape(modules_referenced)),
        _render_summary_card("Foundry Disk", escape(disk_summary)),
        _render_summary_card("Backup Footprint", escape(_format_bytes_human(int(backup_management.get("totalBackupBytes") or 0)))),
        _render_summary_card("Unused Modules", escape(str(int(unused_modules.get("count") or 0)))),
        "</section>",
        "<section class=\"quick-summary\" aria-label=\"Mobile quick summary\">",
        "<article class=\"quick-card quick-upgrade\">",
        "<div class=\"quick-card-title\">Suggested Upgrades</div>",
        f"<div class=\"quick-card-value\">{mobile_upgrade_count}</div>",
        "<button class=\"quick-cta\" type=\"button\" data-quick-target=\"current-view\" data-quick-filter=\"upgrade\">Show upgrades</button>",
        "</article>",
        "<article class=\"quick-card quick-blocked\">",
        "<div class=\"quick-card-title\">Current Blockers</div>",
        f"<div class=\"quick-card-value\">{mobile_blocked_count}</div>",
        "<button class=\"quick-cta\" type=\"button\" data-quick-target=\"current-view\" data-quick-filter=\"blocked\">Show blockers</button>",
        "</article>",
        "<article class=\"quick-card quick-manual\">",
        "<div class=\"quick-card-title\">Manual Review</div>",
        f"<div class=\"quick-card-value\">{mobile_manual_count}</div>",
        "<button class=\"quick-cta\" type=\"button\" data-quick-target=\"current-view\" data-quick-filter=\"manual\">Review manual</button>",
        "</article>",
        "<article class=\"quick-card quick-ready\">",
        "<div class=\"quick-card-title\">Already Compatible</div>",
        f"<div class=\"quick-card-value\">{mobile_ready_count}</div>",
        "<button class=\"quick-cta\" type=\"button\" data-quick-target=\"current-view\" data-quick-filter=\"nochange\">Show stable</button>",
        "</article>",
        "</section>",
        "<section class=\"section-card resolver-controls\" aria-label=\"Resolver mobile controls\">",
        "<div class=\"control-grid\">",
        "<label class=\"global-search\">"
        "<span>Search Modules & Systems</span>"
        "<input id=\"global-module-search\" type=\"search\" placeholder=\"Type a module, system, version or reason\" autocomplete=\"off\" />"
        "</label>",
        "<div class=\"filter-chips\" data-filter-chips>",
        "<button class=\"filter-chip is-active\" type=\"button\" data-chip=\"all\">All <span data-chip-count=\"all\">0</span></button>",
        "<button class=\"filter-chip\" type=\"button\" data-chip=\"upgrade\">Upgrades <span data-chip-count=\"upgrade\">0</span></button>",
        "<button class=\"filter-chip\" type=\"button\" data-chip=\"blocked\">Blocked <span data-chip-count=\"blocked\">0</span></button>",
        "<button class=\"filter-chip\" type=\"button\" data-chip=\"manual\">Manual <span data-chip-count=\"manual\">0</span></button>",
        "<button class=\"filter-chip\" type=\"button\" data-chip=\"nochange\">Stable <span data-chip-count=\"nochange\">0</span></button>",
        "</div>",
        "</div>",
        "</section>",
        "<section class=\"section-card\">",
        "<div class=\"view-tabs\" data-tab-group>",
        "<button class=\"tab-button\" type=\"button\" data-tab-target=\"current-view\" data-default=\"true\">Current Version</button>",
        "<button class=\"tab-button\" type=\"button\" data-tab-target=\"future-view\">Next Versions</button>",
        "<button class=\"tab-button\" type=\"button\" data-tab-target=\"unused-view\">Unused Modules</button>",
        "<button class=\"tab-button\" type=\"button\" data-tab-target=\"backup-view\">Backups</button>",
        "</div>",
        "<div class=\"view-panel\" data-tab-panel=\"current-view\">",
        "<section class=\"nested-section\">",
        "<div class=\"section-heading\">",
        "<div>",
        f"<h2>{escape(str(current_system_upgrades.get('title') or 'System Upgrades'))}</h2>",
        f"<p>{escape(str(current_system_upgrades.get('description') or ''))}</p>",
        "</div>",
        "</div>",
        _render_current_system_glance(current_system_upgrades.get("rows") or []),
        _render_current_system_upgrades(current_system_upgrades.get("rows") or []),
        "</section>",
        "</div>",
        "<div class=\"view-panel\" data-tab-panel=\"future-view\">",
        "<section class=\"nested-section\">",
        "<div class=\"section-heading planner-header\">",
        "<div>",
        "<h2>Next Versions</h2>",
        "<p>Select a stable Foundry target and review which system upgrades become available, plus the modules that can move with them.</p>",
        f"<p class=\"section-note\">Stable Foundry versions available: {len(foundry_options)}</p>",
        "</div>",
        "<label class=\"planner-control\">",
        "<span>Stable Foundry Version</span>",
        f"<select id=\"foundry-target-select\">{_render_foundry_options(foundry_options, default_foundry)}</select>",
        "</label>",
        "</div>",
        "<div class=\"planner-targets\">",
        *target_sections,
        "</div>",
        "</section>",
        "</div>",
        "<div class=\"view-panel\" data-tab-panel=\"backup-view\">",
        "<section class=\"nested-section\">",
        "<div class=\"section-heading\">",
        "<div>",
        f"<h2>{escape(str(backup_management.get('title') or 'Backup Management'))}</h2>",
        f"<p>{escape(str(backup_management.get('description') or ''))}</p>",
        "</div>",
        "</div>",
        _render_backup_management(backup_management),
        "</section>",
        "</div>",
        "<div class=\"view-panel\" data-tab-panel=\"unused-view\">",
        "<section class=\"nested-section\">",
        "<div class=\"section-heading\">",
        "<div>",
        f"<h2>{escape(str(unused_modules.get('title') or 'Unused Modules'))}</h2>",
        f"<p>{escape(str(unused_modules.get('description') or ''))}</p>",
        "</div>",
        "</div>",
        _render_unused_modules(unused_modules),
        "</section>",
        "</div>",
        "</section>",
        "<section class=\"section-card note-card\">",
        "<div class=\"section-heading\">",
        
        "</section>",
        "<script id=\"report-view-v2-data\" type=\"application/json\">",
        escape(json.dumps(view)),
        "</script>",
        "<script>",
        _SCRIPT,
        "</script>",
        "<nav class=\"mobile-action-bar\" aria-label=\"Quick mobile actions\">",
        "<button class=\"mobile-action-btn\" type=\"button\" data-mobile-action=\"search\">Search</button>",
        "<button class=\"mobile-action-btn\" type=\"button\" data-mobile-action=\"filters\">Filters</button>",
        "<button class=\"mobile-action-btn\" type=\"button\" id=\"mobile-upgrade-toggle\" data-mobile-action=\"upgrades\">Upgrades</button>",
        "<button class=\"mobile-action-btn\" type=\"button\" data-mobile-action=\"copy\">Copy Cmd</button>",
        "</nav>",
        "</main>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


def _render_planner_target(target: dict) -> str:
    foundry_version = str(target.get("foundryVersion") or "")
    summary = target.get("summary") or {}
    quick_status = target.get("quickStatus") or {}
    ready_modules = target.get("readyModules") or []
    upgradable_modules = target.get("upgradableModules") or []
    blocked_modules = target.get("blockedModules") or []
    local_manifest_manual_modules = target.get("localManifestManualModules") or []
    systems = target.get("systems") or []
    module_tab_group = f"future-module-status-{foundry_version}"
    systems_ready = int(quick_status.get("systemsReady") or 0)
    systems_total = int(quick_status.get("systemsTotal") or 0)
    section_classes = ["planner-target"]
    if target.get("isCurrent"):
        section_classes.append("active")
    return (
        f"<section class=\"{' '.join(section_classes)}\" data-foundry-target=\"{escape(foundry_version)}\">"
        f"{_render_planner_glance(foundry_version, quick_status, summary)}"
        "<div class=\"tab-strip\" data-tab-group>"
        f"<button class=\"tab-button tab-ready\" type=\"button\" data-tab-target=\"{escape(module_tab_group)}-systems\" data-default=\"true\">Systems Ready ({systems_ready}/{systems_total})</button>"
        f"<button class=\"tab-button tab-blocked\" type=\"button\" data-tab-target=\"{escape(module_tab_group)}-blocked\">Current Blockers ({len(blocked_modules)})</button>"
        f"<button class=\"tab-button tab-update\" type=\"button\" data-tab-target=\"{escape(module_tab_group)}-update\">Requires Update ({len(upgradable_modules)})</button>"
        f"<button class=\"tab-button\" type=\"button\" data-tab-target=\"{escape(module_tab_group)}-ready\">Already Ready ({len(ready_modules)})</button>"
        f"<button class=\"tab-button tab-manual\" type=\"button\" data-tab-target=\"{escape(module_tab_group)}-manual\">Requires Manual Upgrade ({len(local_manifest_manual_modules)})</button>"
        "</div>"
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(module_tab_group)}-systems\"><div class=\"split-grid split-grid-single\"><div><h3>System Upgrades</h3>{_render_systems_table(systems)}</div></div></div>"
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(module_tab_group)}-blocked\">{_render_planner_module_table(blocked_modules, empty_message='No blocker was recorded for this target.', blocked=True)}</div>"
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(module_tab_group)}-update\">{_render_planner_module_table(upgradable_modules, empty_message='No module upgrade is required for this Foundry target.')}</div>"
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(module_tab_group)}-ready\">{_render_planner_module_table(ready_modules, empty_message='No module is already ready for this target.', ready=True)}</div>"
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(module_tab_group)}-manual\">{_render_manual_local_manifest_table(local_manifest_manual_modules)}</div>"
        "</section>"
    )


def _render_current_system_upgrades(rows: list[dict]) -> str:
    if not rows:
        return "<p class=\"empty-state\">No base system upgrade is available for the currently installed Foundry version.</p>"
    items = []
    for row in rows:
        items.append(
            "<tr>"
            f"<td>{_render_system_link(row)}</td>"
            f"<td>{escape(_render_version_arrow(row.get('installedVersion'), row.get('targetVersion')))}</td>"
            f"<td>{escape(', '.join(row.get('worldAliases') or []) or '-')}</td>"
            f"<td>{_render_percent_badge(row.get('coveragePercent'))}</td>"
            "</tr>"
        )
    table_html = _render_paginated_table(
        headers=["System", "Version", "Worlds", "% Upgradable"],
        rows_html=items,
        table_key="current-system-upgrades",
        copy_command=_build_bulk_module_command(_collect_upgradable_modules_from_system_rows(rows, current=True)),
    )
    return (
        "<div class=\"planner-secondary\">"
        "<h3>System x Modules Upgrades</h3>"
        f"{table_html}"
        "<p class=\"section-note\">Modules used by worlds are evaluated against this system upgrade. Modules without explicit system declarations are listed under manual verification.</p>"
        f"{_render_system_detail_tabs(rows, group_prefix='current-system', current=True)}"
        "</div>"
    )


def _render_current_system_glance(rows: list[dict]) -> str:
    modules_ready = sum(int(row.get("compatibleModules") or 0) for row in rows)
    modules_need_update = sum(int(row.get("upgradableModules") or 0) for row in rows)
    modules_blocked = sum(int(row.get("blockedModules") or 0) for row in rows)
    modules_manual = sum(int(row.get("ignoredModules") or 0) for row in rows)
    modules_total = modules_ready + modules_need_update + modules_blocked + modules_manual
    systems_total = len(rows)
    systems_ready = sum(1 for row in rows if int(row.get("blockedModules") or 0) == 0)
    systems_requires_update = sum(1 for row in rows if int(row.get("upgradableModules") or 0) > 0)
    if modules_blocked > 0:
        status_label = "<span class=\"metric-blocked\">Unstable</span>"
    elif modules_need_update > 0:
        status_label = "<span class=\"metric-update\">Updatable</span>"
    else:
        status_label = "<span class=\"metric-ready\">Stable</span>"
    return (
        "<div class=\"planner-glance\">"
        "<h3>Current At A Glance</h3>"
        "<div class=\"planner-glance-grid\">"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Module Status</div><div class=\"summary-value\">{status_label}</div><p class=\"glance-note\"><span class=\"metric-ready\">{modules_ready}</span> compatible, <span class=\"metric-update\">{modules_need_update}</span> suggested updates, <span class=\"metric-blocked\">{modules_blocked}</span> blocked, <span class=\"metric-manual\">{modules_manual}</span> manual, <span class=\"metric-neutral\">{modules_total}</span> total.</p></article>"
        f"<article class=\"glance-card\"><div class=\"summary-label\">System Updates</div><div class=\"summary-value\"><span class=\"metric-ready\">{systems_ready}/{systems_total}</span></div><p class=\"glance-note\"><span class=\"metric-update\">{systems_requires_update}</span> Requires Update on current Foundry.</p></article>"
        "</div>"
        "</div>"
    )


def _render_systems_table(rows: list[dict]) -> str:
    if not rows:
        return "<p class=\"empty-state\">No system upgrade is available for this Foundry target.</p>"
    items = []
    for row in rows:
        items.append(
            "<tr>"
            f"<td>{_render_system_link(row)}</td>"
            f"<td>{_render_future_system_version_cell(row)}</td>"
            f"<td>{escape(', '.join(row.get('worldAliases') or []) or '-')}</td>"
            f"<td>{_render_percent_badge(row.get('coveragePercent'))}</td>"
            "</tr>"
        )
    return _render_paginated_table(
        headers=["System", "Version", "Worlds", "% Upgradable"],
        rows_html=items,
        table_key="system-upgrades",
        copy_command=_build_bulk_module_command(_collect_upgradable_modules_from_system_rows(rows)),
    )


def _render_planner_module_table(
    rows: list[dict],
    empty_message: str,
    blocked: bool = False,
    ready: bool = False,
    unknown: bool = False,
) -> str:
    if not rows:
        return f"<p class=\"empty-state\">{escape(empty_message)}</p>"
    top_reference_counts = _top_reference_counts(rows)
    items = []
    for row in rows:
        copy_command = _build_module_manual_command(row) if unknown else _build_module_command(row)
        attention = bool(row.get("attentionFlag"))
        items.append(
            "<tr>"
            f"<td>{_render_reference_cell(row, top_reference_counts)}</td>"
            f"<td>{_render_module_link(row)}</td>"
            f"<td>{escape(', '.join(row.get('systemIds') or []) or '-')}</td>"
            f"<td>{escape(_render_version_arrow(row.get('installedVersion'), row.get('recommendedVersion')))}</td>"
            f"<td>{_render_confidence_badge(row.get('confidence'), blocked=blocked, ready=ready, unknown=unknown, attention=attention)}</td>"
            f"<td>{escape(_format_details(row))}</td>"
            f"<td>{_render_copy_button(copy_command)}</td>"
            "</tr>"
        )
    return _render_paginated_table(
        headers=["Refs", "Module", "System", "Version", "Confidence", "Details", "Action"],
        rows_html=items,
        table_key="planner-modules",
        copy_command=_build_bulk_module_manual_command(rows) if unknown else _build_bulk_module_command(rows),
    )


def _render_foundry_options(options: list[dict], default_version: str) -> str:
    items = []
    for option in options:
        version = str(option.get("version") or "")
        selected = " selected" if version == default_version else ""
        items.append(
            f"<option value=\"{escape(version)}\"{selected}>{escape(str(option.get('label') or version))}</option>"
        )
    return "".join(items)


def _render_summary_card(label: str, value: str) -> str:
    return (
        "<article class=\"summary-card\">"
        f"<div class=\"summary-label\">{escape(label)}</div>"
        f"<div class=\"summary-value\">{value}</div>"
        "</article>"
    )


def _render_foundry_status(status: dict) -> str:
    state = str(status.get("status") or "unknown").lower()
    service = str(status.get("service") or "foundry")
    if state == "online":
        icon = "🟢"
        label = "Foundry online"
    elif state == "offline":
        icon = "🔴"
        label = "Foundry offline"
    else:
        icon = "🟡"
        label = "Foundry status unknown"
    return (
        f"<div class=\"foundry-status\" title=\"{escape(label)} ({escape(service)})\">"
        f"<span aria-hidden=\"true\">{icon}</span>"
        f"<span>{escape(label)}</span>"
        "</div>"
    )


def _render_future_system_version_cell(row: dict) -> str:
    version_arrow = escape(_render_version_arrow(row.get("installedVersion"), row.get("targetVersion")))
    target_foundry = str(row.get("targetFoundryVersion") or "").strip()
    if row.get("targetReady"):
        note = f"Upgradable (Compatible with v{target_foundry})" if target_foundry else "Upgradable"
        return f"{version_arrow}<div class=\"system-upgrade-note system-upgrade-ok\">{escape(note)}</div>"
    note = f"No compatible release for v{target_foundry}" if target_foundry else "No compatible release"
    return f"{version_arrow}<div class=\"system-upgrade-note system-upgrade-bad\">{escape(note)}</div>"


def _render_backup_management(backup: dict) -> str:
    total_backup_bytes = int(backup.get("totalBackupBytes") or 0)
    total_backup_count = int(backup.get("totalBackupCount") or 0)
    module_count_with_backups = int(backup.get("moduleCountWithBackups") or 0)
    total_module_bytes = int(backup.get("totalModuleBytes") or 0)
    module_count = int(backup.get("moduleCount") or 0)
    policy = backup.get("policy") or {}
    maintenance = backup.get("maintenance") or {}
    size_cache = backup.get("sizeCache") or {}
    disk = backup.get("diskStatus") or {}
    rows = backup.get("rows") or []
    top_reference_counts = _top_reference_counts(rows)

    table_rows = []
    for row in rows:
        cleanup_command = _build_module_backup_cleanup_command(row)
        table_rows.append(
            "<tr>"
            f"<td>{_render_reference_cell(row, top_reference_counts)}</td>"
            f"<td>{escape(str(row.get('title') or row.get('module') or '-'))}</td>"
            f"<td>{escape(_format_bytes_human(int(row.get('moduleSizeBytes') or 0)))}</td>"
            f"<td>{escape(str(row.get('backupCount') or 0))}</td>"
            f"<td>{escape(_format_bytes_human(int(row.get('backupSizeBytes') or 0)))}</td>"
            f"<td>{escape(_format_bytes_human(int(row.get('largestBackupBytes') or 0)))}</td>"
            f"<td>{escape(_render_relative_time(row.get('newestBackupAt')))}</td>"
            f"<td>{escape(_render_relative_time(row.get('oldestBackupAt')))}</td>"
            f"<td>{escape(str(row.get('largestBackupPath') or '-'))}</td>"
            f"<td>{_render_copy_button(cleanup_command)}</td>"
            "</tr>"
        )

    policy_label = (
        f"Retention policy: max {_format_bytes_human(int(policy.get('maxBytes') or 0))}, "
        f"{int(policy.get('maxPerModule') or 0)} backups/module, {int(policy.get('maxAgeDays') or 0)} days."
    )
    maintenance_label = (
        f"Last maintenance removed {int(maintenance.get('removedCount') or 0)} backups "
        f"({_format_bytes_human(int(maintenance.get('removedBytes') or 0))})."
    )
    cache_label = (
        f"Size cache entries: {int(size_cache.get('entries') or 0)} "
        f"(TTL {int(size_cache.get('ttlSeconds') or 0)}s)."
    )
    disk_used_percent = float(disk.get("usedPercent") or 0.0)
    disk_free_percent = float(disk.get("freePercent") or 0.0)
    disk_path = str(disk.get("path") or "-")
    disk_total = _format_bytes_human(int(disk.get("totalBytes") or 0))
    disk_used = _format_bytes_human(int(disk.get("usedBytes") or 0))
    disk_free = _format_bytes_human(int(disk.get("freeBytes") or 0))
    disk_error = str(disk.get("error") or "").strip()
    if disk_error:
        disk_label = f"Disk usage unavailable for {disk_path}: {disk_error}"
    else:
        disk_label = (
            f"Foundry disk ({disk_path}): {disk_used_percent:.1f}% used ({disk_used}), "
            f"{disk_free_percent:.1f}% free ({disk_free}), total {disk_total}."
        )
    table_html = _render_paginated_table(
        headers=[
            "Refs",
            "Module",
            "Module Size",
            "Backups",
            "Backup Size",
            "Largest Backup",
            "Newest Backup",
            "Oldest Backup",
            "Largest Backup Path",
            "Cleanup",
        ],
        rows_html=table_rows,
        table_key="backup-management",
        copy_command=_build_bulk_backup_cleanup_command(rows),
    ) if table_rows else "<p class=\"empty-state\">No backup directories were found.</p>"
    return (
        "<div class=\"planner-glance\">"
        "<h3>Backup Footprint</h3>"
        "<div class=\"planner-glance-grid\">"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Total Backups</div><div class=\"summary-value\">{total_backup_count}</div><p class=\"glance-note\">{_format_bytes_human(total_backup_bytes)} across {module_count_with_backups} modules.</p></article>"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Modules On Disk</div><div class=\"summary-value\">{module_count}</div><p class=\"glance-note\">{_format_bytes_human(total_module_bytes)} currently installed.</p></article>"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Foundry Disk</div><div class=\"summary-value\">{disk_used_percent:.1f}% used</div><p class=\"glance-note\">{disk_free_percent:.1f}% free • {disk_free} free</p></article>"
        "</div>"
        f"<p class=\"section-note\">{escape(policy_label)} {escape(maintenance_label)} {escape(cache_label)} {escape(disk_label)}</p>"
        "</div>"
        f"{table_html}"
    )


def _render_unused_modules(unused: dict) -> str:
    rows = unused.get("rows") or []
    total_size = int(unused.get("totalSizeBytes") or 0)
    updatable_count = int(unused.get("updatableCount") or 0)
    if not rows:
        return "<p class=\"empty-state\">No unused module was detected from world references.</p>"
    top_reference_counts = _top_reference_counts(rows)
    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{_render_reference_cell(row, top_reference_counts)}</td>"
            f"<td>{_render_module_link(row)}</td>"
            f"<td>{escape(_render_relative_time(row.get('modifiedAt')))}</td>"
            f"<td>{escape(_format_bytes_human(int(row.get('moduleSizeBytes') or 0)))}</td>"
            f"<td>{escape(_render_version_arrow(row.get('installedVersion'), row.get('recommendedVersion')))}</td>"
            f"<td>{escape(str(row.get('updateStatus') or 'No'))}</td>"
            f"<td>{escape(_format_details(row))}</td>"
            f"<td>{_render_copy_button(_build_module_delete_command(row))}</td>"
            "</tr>"
        )
    table_html = _render_paginated_table(
        headers=["Refs", "Module", "Last Modified", "Size", "Version", "Updatable", "Details", "Delete"],
        rows_html=table_rows,
        table_key="unused-modules",
        copy_command=_build_bulk_module_delete_command(rows),
    )
    return (
        "<div class=\"planner-glance\">"
        "<h3>Unused Footprint</h3>"
        "<div class=\"planner-glance-grid\">"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Unused Modules</div><div class=\"summary-value\">{len(rows)}</div><p class=\"glance-note\">{_format_bytes_human(total_size)} total size.</p></article>"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Updatable</div><div class=\"summary-value\">{updatable_count}</div><p class=\"glance-note\">Modules with viable update available.</p></article>"
        "</div>"
        "</div>"
        f"{table_html}"
    )


def _render_planner_glance(foundry_version: str, quick_status: dict, summary: dict) -> str:
    modules_total = int(quick_status.get("modulesTotal") or 0)
    modules_observed = int(quick_status.get("modulesObserved") or modules_total)
    modules_ready = int(quick_status.get("modulesReady") or 0)
    modules_need_update = int(quick_status.get("modulesNeedUpdate") or 0)
    modules_blocked = int(quick_status.get("modulesBlocked") or 0)
    modules_manual_update = int(quick_status.get("modulesManualUpdate") or 0)
    if modules_blocked > 0:
        status_label = "<span class=\"metric-blocked\">Unstable</span>"
    elif modules_need_update > 0:
        status_label = "<span class=\"metric-update\">Updatable</span>"
    else:
        status_label = "<span class=\"metric-ready\">Stable</span>"
    return (
        "<div class=\"planner-glance\">"
        "<h3>Upgrade At A Glance</h3>"
        "<div class=\"planner-glance-grid\">"
        f"<article class=\"glance-card\"><div class=\"summary-label\">Modules Readness</div><div class=\"summary-value\">{status_label}</div><p class=\"glance-note\"><span class=\"metric-ready\">{modules_ready}</span> compatible, <span class=\"metric-update\">{modules_need_update}</span> suggested updates, <span class=\"metric-blocked\">{modules_blocked}</span> blocked, <span class=\"metric-manual\">{modules_manual_update}</span> manual, <span class=\"metric-neutral\">{modules_observed}</span> total.</p></article>"
        "</div>"
        "</div>"
    )


def _render_idea_card(title: str, description: str) -> str:
    return (
        "<article class=\"idea-card\">"
        f"<h3>{escape(title)}</h3>"
        f"<p>{escape(description)}</p>"
        "</article>"
    )


def _render_not_ready_modules_table(rows: list[dict]) -> str:
    if not rows:
        return "<p class=\"empty-state\">All modules are ready for this Foundry target.</p>"
    top_reference_counts = _top_reference_counts(rows)
    items = []
    for row in rows:
        copy_command = _build_module_command(row)
        items.append(
            "<tr>"
            f"<td>{_render_reference_cell(row, top_reference_counts)}</td>"
            f"<td>{_render_module_link(row)}</td>"
            f"<td>{escape(', '.join(row.get('systemIds') or []) or '-')}</td>"
            f"<td>{escape(_render_version_arrow(row.get('installedVersion'), row.get('recommendedVersion')))}</td>"
            f"<td>{escape(_format_details(row))}</td>"
            f"<td>{_render_copy_button(copy_command)}</td>"
            "</tr>"
        )
    return _render_paginated_table(
        headers=["Refs", "Module", "System", "Version", "Details", "Action"],
        rows_html=items,
        table_key="planner-not-ready-modules",
        copy_command=_build_bulk_module_command(rows),
    )


def _render_manual_local_manifest_table(rows: list[dict]) -> str:
    if not rows:
        return "<p class=\"empty-state\">No module requires manual handling for this target.</p>"
    top_reference_counts = _top_reference_counts(rows)
    items = []
    for row in rows:
        items.append(
            "<tr>"
            f"<td>{_render_reference_cell(row, top_reference_counts)}</td>"
            f"<td>{_render_module_link(row)}</td>"
            f"<td>{escape(', '.join(row.get('systemIds') or []) or '-')}</td>"
            f"<td>{escape(_render_version_arrow(row.get('installedVersion'), row.get('recommendedVersion')))}</td>"
            f"<td>{escape(_format_details(row))}</td>"
            f"<td>Manual verification/update required</td>"
            "</tr>"
        )
    return _render_paginated_table(
        headers=["Refs", "Module", "System", "Version", "Details", "Manual Step"],
        rows_html=items,
        table_key="planner-local-manual",
    )


def _top_reference_counts(rows: list[dict], top_unique: int = 3) -> set[int]:
    counts = sorted(
        {
            int(row.get("dependencyRefCount") or 0)
            for row in rows
            if int(row.get("dependencyRefCount") or 0) > 0
        },
        reverse=True,
    )
    return set(counts[:top_unique])


def _render_reference_cell(row: dict, top_counts: set[int]) -> str:
    count = int(row.get("dependencyRefCount") or 0)
    if count <= 0:
        return "<span class=\"ref-count\">0</span>"
    hot = count in top_counts and count >= 3
    marker = "<span class=\"ref-important\" title=\"Highly referenced dependency\">❗</span> " if hot else ""
    return f"<span class=\"ref-count\">{marker}{escape(str(count))}</span>"


def _render_system_detail_tabs(rows: list[dict], group_prefix: str, current: bool) -> str:
    if not rows:
        return "<p class=\"empty-state\">No per-system details were recorded.</p>"
    if current:
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                -int(row.get("modulesUsed") or 0),
                -int(row.get("blockedModules") or 0),
                str(row.get("title") or row.get("systemId") or "").lower(),
            ),
        )
    else:
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                -int(row.get("blockedModules") or 0),
                str(row.get("title") or row.get("systemId") or "").lower(),
            ),
        )
    buttons = []
    panels = []
    for index, row in enumerate(ordered_rows):
        system_id = str(row.get("systemId") or f"system-{index}")
        tab_key = f"{group_prefix}-{system_id}"
        default = ' data-default="true"' if index == 0 else ""
        buttons.append(
            f"<button class=\"tab-button\" type=\"button\" data-tab-target=\"{escape(tab_key)}\"{default}>{escape(str(row.get('title') or system_id))}</button>"
        )
        panel_renderer = _render_current_system_detail_card if current else _render_future_system_detail_card
        panels.append(
            f"<div class=\"tab-panel\" data-tab-panel=\"{escape(tab_key)}\">{panel_renderer(row, tab_key)}</div>"
        )
    return (
        "<div class=\"tabbed-detail-group\">"
        "<div class=\"tab-strip\" data-tab-group>"
        f"{''.join(buttons)}"
        "</div>"
        f"{''.join(panels)}"
        "</div>"
    )


def _render_future_system_detail_card(row: dict, tab_key: str) -> str:
    title = _render_system_link(row)
    version = escape(_render_version_arrow(row.get("installedVersion"), row.get("targetVersion")))
    worlds = escape(", ".join(row.get("worldAliases") or []) or "-")
    compatibility = _format_compatibility(row.get("compatibility") or {})
    compatibility_label = f"Compatibility: Foundry {compatibility}" if compatibility else "Compatibility: -"
    return (
        "<article class=\"system-detail-card\">"
        "<div class=\"system-detail-header\">"
        "<div>"
        f"<h4>{title}</h4>"
        f"<p class=\"system-detail-meta\">{version} • Worlds: {worlds}</p>"
        f"<p class=\"system-detail-meta\">{escape(compatibility_label)} • {_render_percent_badge(row.get('coveragePercent'))}</p>"
        "</div>"
        "</div>"
        f"{_render_inner_system_tabs(row, tab_key, current=False)}"
        "</article>"
    )


def _render_current_system_detail_card(row: dict, tab_key: str) -> str:
    title = _render_system_link(row)
    version = escape(_render_version_arrow(row.get("installedVersion"), row.get("targetVersion")))
    worlds = escape(", ".join(row.get("worldAliases") or []) or "-")
    compatibility = _format_compatibility(row.get("compatibility") or {})
    compatibility_label = f"Compatibility: Foundry {compatibility}" if compatibility else "Compatibility: -"
    return (
        "<article class=\"system-detail-card\">"
        "<div class=\"system-detail-header\">"
        "<div>"
        f"<h4>{title}</h4>"
        f"<p class=\"system-detail-meta\">{version} • Worlds: {worlds}</p>"
        f"<p class=\"system-detail-meta\">{escape(compatibility_label)} • {_render_percent_badge(row.get('coveragePercent'))}</p>"
        "</div>"
        "</div>"
        f"{_render_inner_system_tabs(row, tab_key, current=True)}"
        "</article>"
    )


def _render_inner_system_tabs(row: dict, tab_key: str, current: bool) -> str:
    tab_group = f"{tab_key}-module-state"
    blocked_rows = row.get("blockedModuleRows") or []
    upgradable_rows = row.get("upgradableModuleRows") or []
    ready_rows = (row.get("compatibleModuleRows") or []) if current else (row.get("readyModuleRows") or [])
    unknown_rows = row.get("unknownModuleRows") or []
    ready_label = "Compatible" if current else "Compatible"
    buttons = [
        f"<button class=\"tab-button tab-blocked\" type=\"button\" data-tab-target=\"{escape(tab_group)}-blocked\" data-default=\"true\">Blocked Modules ({len(blocked_rows)})</button>",
        f"<button class=\"tab-button tab-update\" type=\"button\" data-tab-target=\"{escape(tab_group)}-update\">Requires Update ({len(upgradable_rows)})</button>",
        f"<button class=\"tab-button tab-ready\" type=\"button\" data-tab-target=\"{escape(tab_group)}-ready\">{ready_label} ({len(ready_rows)})</button>",
    ]
    panels = [
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(tab_group)}-blocked\">{_render_planner_module_table(blocked_rows, empty_message='No blocked module was recorded for this system upgrade.', blocked=True)}</div>",
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(tab_group)}-update\">{_render_planner_module_table(upgradable_rows, empty_message='No module upgrade is required for this system upgrade.')}</div>",
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(tab_group)}-ready\">{_render_planner_module_table(ready_rows, empty_message='No module is already compatible with this system upgrade.', ready=True)}</div>",
    ]
    buttons.append(
        f"<button class=\"tab-button tab-manual\" type=\"button\" data-tab-target=\"{escape(tab_group)}-unknown\">Requires Manual Upgrade ({len(unknown_rows)})</button>"
    )
    panels.append(
        f"<div class=\"tab-panel\" data-tab-panel=\"{escape(tab_group)}-unknown\">{_render_planner_module_table(unknown_rows, empty_message='No manual upgrade gap was recorded for this system upgrade.', unknown=True)}</div>"
    )
    return (
        "<div class=\"system-tabbed-sections\">"
        "<div class=\"tab-strip\" data-tab-group>"
        f"{''.join(buttons)}"
        "</div>"
        f"{''.join(panels)}"
        "</div>"
    )


def _render_system_link(row: dict) -> str:
    label = escape(str(row.get("title") or row.get("systemId") or "-"))
    tooltip = escape(str(row.get("systemId") or row.get("title") or "-"))
    href = _pick_preferred_link(row)
    if href:
        return f"<a class=\"module-link\" href=\"{escape(str(href))}\" target=\"_blank\" rel=\"noreferrer\" title=\"{tooltip}\">{label}</a>"
    return f"<span title=\"{tooltip}\">{label}</span>"


def _render_module_link(row: dict) -> str:
    label = escape(str(row.get("title") or row.get("module") or "-"))
    tooltip = escape(str(row.get("module") or row.get("title") or "-"))
    href = _pick_preferred_link(row)
    if href:
        return f"<a class=\"module-link\" href=\"{escape(str(href))}\" target=\"_blank\" rel=\"noreferrer\" title=\"{tooltip}\">{label}</a>"
    return f"<span title=\"{tooltip}\">{label}</span>"


def _pick_preferred_link(row: dict) -> str | None:
    explicit = _normalize_release_page_url(row.get("releaseUrl"))
    if explicit:
        return explicit
    download = str(row.get("downloadUrl") or "").strip()
    manifest = str(row.get("manifestUrl") or "").strip()
    normalized_download = _normalize_release_page_url(download)
    normalized_manifest = _normalize_release_page_url(manifest)
    if normalized_download and normalized_download != download:
        return normalized_download
    if normalized_manifest:
        return normalized_manifest
    if normalized_download:
        return normalized_download
    return None


def _normalize_release_page_url(url: str | None) -> str | None:
    value = str(url or "").strip()
    if not value:
        return None
    if "foundryvtt.com/releases/" in value:
        return value
    if "github.com/" in value and any(marker in value for marker in ("/archive/", "/zipball/", "/tarball/")):
        base, tag = re.split(r"/(?:archive|zipball|tarball)/", value, maxsplit=1)
        tag = re.sub(r"^(refs/tags/)", "", tag)
        tag = re.sub(r"(\.zip|\.tar\.gz)$", "", tag)
        if base and tag:
            return f"{base}/releases/tag/{tag}"
    github_release = re.match(r"^(https://github\.com/[^/]+/[^/]+)/releases/download/([^/]+)/", value)
    if github_release:
        return f"{github_release.group(1)}/releases/tag/{github_release.group(2)}"
    github_latest = re.match(r"^(https://github\.com/[^/]+/[^/]+)/releases/latest/download/", value)
    if github_latest:
        return f"{github_latest.group(1)}/releases/latest"
    github_archive = re.match(r"^(https://github\.com/[^/]+/[^/]+)/(?:archive|zipball|tarball)/(?:refs/tags/)?([^/]+?)(?:\\.zip|\\.tar\\.gz)$", value)
    if github_archive:
        return f"{github_archive.group(1)}/releases/tag/{github_archive.group(2)}"
    gitlab_artifact = re.match(r"^(https://gitlab\.com/[^/]+/[^/]+(?:/[^/]+)*)/-/jobs/artifacts/([^/]+)/raw/", value)
    if gitlab_artifact:
        return f"{gitlab_artifact.group(1)}/-/tags/{gitlab_artifact.group(2)}"
    gitlab_raw = re.match(r"^(https://gitlab\.com/[^/]+/[^/]+(?:/[^/]+)*)/(?:-|raw)/raw/([^/]+)/", value)
    if gitlab_raw:
        return f"{gitlab_raw.group(1)}/-/tags/{gitlab_raw.group(2)}"
    return value


def _render_version_arrow(installed_version, recommended_version) -> str:
    left = str(installed_version or "-")
    right = str(recommended_version or "-")
    return f"{left} -> {right}"


def _render_confidence_badge(
    confidence: str | None,
    blocked: bool = False,
    ready: bool = False,
    unknown: bool = False,
    attention: bool = False,
) -> str:
    value = str(confidence or "unknown").strip().lower()
    css = {
        "high": "confidence-high",
        "medium": "confidence-medium",
        "low": "confidence-low",
    }.get(value, "confidence-unknown")
    label = value.title() if value else "Unknown"
    if blocked and value == "high":
        css = "confidence-medium"
    if ready:
        css = "confidence-high"
        label = "Ready"
    if ready and attention:
        css = "confidence-medium"
        label = "Ready (Attention)"
    if unknown:
        css = "confidence-unknown"
        label = "Manual"
    return f"<span class=\"confidence-badge {css}\">{escape(label)}</span>"


def _render_module_state_badge(status: str | None) -> str:
    value = str(status or "").strip().lower()
    mapping = {
        "ready": ("Ready", "confidence-high"),
        "upgradable": ("Needs Update", "confidence-medium"),
        "blocked": ("Blocked", "confidence-low"),
        "excluded-local-only": ("Local Manifest Only", "confidence-unknown"),
    }
    label, css = mapping.get(value, ("Needs Verification", "confidence-unknown"))
    return f"<span class=\"confidence-badge {css}\">{escape(label)}</span>"


def _render_percent_badge(value) -> str:
    try:
        percent = float(value or 0.0)
    except Exception:
        percent = 0.0
    css = "percent-low"
    if percent >= 80.0:
        css = "percent-high"
    elif percent >= 60.0:
        css = "percent-medium"
    return f"<span class=\"percent-badge {css}\">{escape(f'{percent:.1f}%')}</span>"


def _format_bytes_human(value: int) -> str:
    size = float(max(int(value), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0 or size >= 10:
        return f"{int(round(size))} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def _render_system_state_bar(row: dict) -> str:
    total = max(_modules_considered(row), 0)
    ready = max(int(row.get("readyModules") or 0), 0)
    upgradable = max(int(row.get("upgradableModules") or 0), 0)
    blocked = max(int(row.get("blockedModules") or 0), 0)
    unknown = max(int(row.get("unknownModules") or 0), 0)
    if total <= 0:
        return "<span class=\"state-empty\">No modules</span>"

    def width(count: int) -> str:
        return f"{(count / total * 100.0):.1f}%"

    segments = []
    if ready:
        segments.append(f"<span class=\"state-segment state-ready\" style=\"width:{width(ready)}\"></span>")
    if upgradable:
        segments.append(f"<span class=\"state-segment state-upgradable\" style=\"width:{width(upgradable)}\"></span>")
    if blocked:
        segments.append(f"<span class=\"state-segment state-blocked\" style=\"width:{width(blocked)}\"></span>")

    return (
        "<div class=\"state-bar-cell\">"
        f"<div class=\"state-bar\">{''.join(segments)}</div>"
        "<div class=\"state-legend\">"
        f"<span class=\"legend-ready\">{ready} ready</span>"
        f"<span class=\"legend-upgradable\">{upgradable} update</span>"
        f"<span class=\"legend-blocked\">{blocked} blocked</span>"
        f"<span class=\"legend-unknown\">{unknown} manual (excluded)</span>"
        "</div>"
        "</div>"
    )


def _modules_considered(row: dict) -> int:
    explicit = row.get("modulesConsidered")
    if explicit not in (None, ""):
        try:
            return max(int(explicit), 0)
        except (TypeError, ValueError):
            pass
    return max(
        int(row.get("readyModules") or 0)
        + int(row.get("upgradableModules") or 0)
        + int(row.get("blockedModules") or 0),
        0,
    )


def _format_details(row: dict) -> str:
    reason = str(row.get("reason") or "").strip()
    compatibility = _format_compatibility(row.get("compatibility") or {})
    system_fragments = []
    for system_id, item in sorted((row.get("systemCompatibility") or {}).items()):
        system_range = _format_compatibility(item)
        if system_range:
            system_fragments.append(f"{system_id} {system_range}")
    suffix = []
    if compatibility:
        suffix.append(f"Foundry {compatibility}")
    if system_fragments:
        suffix.append("Systems " + "; ".join(system_fragments))
    if suffix:
        detail = f"{reason} Compatibility: {'; '.join(suffix)}".strip()
    else:
        detail = reason or "-"
    max_foundry = str(row.get("maxFoundrySupported") or "").strip()
    max_system = str(row.get("maxSystemSupported") or "").strip()
    max_system_current_foundry = str(row.get("maxSystemOnTargetFoundry") or "").strip()
    bound_fragments = []
    if max_foundry:
        bound_fragments.append(f"max Foundry={max_foundry}")
    if max_system:
        bound_fragments.append(f"max System={max_system}")
    if max_system_current_foundry:
        bound_fragments.append(f"max System@current Foundry={max_system_current_foundry}")
    if bound_fragments:
        detail = f"{detail} Bounds: {', '.join(bound_fragments)}"
    if bool(row.get("attentionFlag")):
        age_label = _relative_days_label(row.get("releasePublishedAt"))
        lowered = detail.lower()
        if age_label and "updated " not in lowered and "last release " not in lowered:
            detail = f"{detail} Last release {age_label}."
        elif not age_label and "last release " not in lowered:
            detail = f"{detail} Last release age unavailable."
    return detail


def _format_compatibility(compatibility: dict) -> str:
    minimum = compatibility.get("minimum")
    verified = compatibility.get("verified")
    maximum = compatibility.get("maximum")
    if minimum and maximum:
        return f"{minimum} - {maximum}"
    if minimum and verified:
        return f"{minimum} - {verified}"
    if minimum:
        return f"{minimum}+"
    if verified:
        return str(verified)
    if maximum:
        return f"up to {maximum}"
    return ""


def _build_module_command(row: dict) -> str:
    module_id = str(row.get("module") or "").strip()
    recommended = str(row.get("recommendedVersion") or "").strip()
    if not module_id:
        return "printf '%s\n' 'No command available.'"
    module_targets = [module_id, *_dependency_parent_modules(row)]
    dedup_targets: list[str] = []
    seen_targets: set[str] = set()
    for item in module_targets:
        clean = str(item or "").strip()
        if not clean or clean in seen_targets:
            continue
        seen_targets.add(clean)
        dedup_targets.append(clean)
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--apply",
    ]
    for target in dedup_targets:
        command.append(f"--module {target}")
    if recommended and _should_pin_expected_version(row):
        command.append(f"--expected-version {module_id}={recommended}")
    return " ".join(command)


def _build_module_manual_command(row: dict) -> str:
    module_id = str(row.get("module") or "").strip()
    if not module_id:
        return "printf '%s\n' 'No command available.'"
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        f"--module {module_id}",
        "--dry-run",
        "--pretty",
    ]
    return " ".join(command)


def _build_module_delete_command(row: dict) -> str:
    module_id = str(row.get("module") or "").strip()
    if module_id:
        return (
            "cd /home/engrenado/config/foundryModuleVersioningTool && "
            "python3 -m resolver.cli "
            "--data-root /home/engrenado/foundry/data "
            "--delete-unused-modules "
            f"--delete-module {module_id}"
        )
    return "printf '%s\\n' 'No delete command available (module id not found).'"


def _build_module_backup_cleanup_command(row: dict) -> str:
    module_id = str(row.get("module") or "").strip()
    if not module_id:
        return "printf '%s\n' 'No backup cleanup command available (module id not found).'"
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--cleanup-backups",
        f"--cleanup-backup-module {module_id}",
    ]
    return " ".join(command)


def _build_bulk_module_command(rows: list[dict]) -> str | None:
    modules: list[str] = []
    expected_pins: list[tuple[str, str]] = []
    seen_modules: set[str] = set()
    seen_pins: set[str] = set()
    for row in rows:
        module_id = str(row.get("module") or "").strip()
        if not module_id:
            continue
        related_targets = [module_id, *_dependency_parent_modules(row)]
        for target in related_targets:
            clean_target = str(target or "").strip()
            if not clean_target or clean_target in seen_modules:
                continue
            seen_modules.add(clean_target)
            modules.append(clean_target)
        recommended = str(row.get("recommendedVersion") or "").strip()
        if recommended and _should_pin_expected_version(row):
            pin_key = f"{module_id}={recommended}"
            if pin_key not in seen_pins:
                seen_pins.add(pin_key)
                expected_pins.append((module_id, recommended))
    if not modules:
        return None
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--apply",
    ]
    for module_id in modules:
        command.append(f"--module {module_id}")
    for module_id, recommended in expected_pins:
        command.append(f"--expected-version {module_id}={recommended}")
    return " ".join(command)


def _build_bulk_module_delete_command(rows: list[dict]) -> str | None:
    module_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        module_id = str(row.get("module") or "").strip()
        if not module_id or module_id in seen:
            continue
        seen.add(module_id)
        module_ids.append(module_id)
    if not module_ids:
        return None
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--delete-unused-modules",
    ]
    for module_id in module_ids:
        command.append(f"--delete-module {module_id}")
    return " ".join(command)


def _build_bulk_backup_cleanup_command(rows: list[dict]) -> str | None:
    module_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        module_id = str(row.get("module") or "").strip()
        if not module_id or module_id in seen:
            continue
        seen.add(module_id)
        module_ids.append(module_id)
    if not module_ids:
        return None
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--cleanup-backups",
    ]
    for module_id in module_ids:
        command.append(f"--cleanup-backup-module {module_id}")
    return " ".join(command)


def _dependency_parent_modules(row: dict) -> list[str]:
    reason = str(row.get("reason") or "")
    matches = re.findall(r"Required by\s+([A-Za-z0-9._-]+)\s*:", reason)
    if not matches:
        matches = re.findall(r"Required by\s+([A-Za-z0-9._-]+)\b", reason)
    unique: list[str] = []
    seen: set[str] = set()
    for match in matches:
        clean = str(match or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
    return unique


def _should_pin_expected_version(row: dict) -> bool:
    # Dependency-driven rows may only become upgradable when evaluated
    # together with their parent modules; strict pinning isolated modules
    # can trigger false "Expected version mismatch" errors.
    return len(_dependency_parent_modules(row)) == 0


def _build_bulk_module_manual_command(rows: list[dict]) -> str | None:
    modules: list[str] = []
    seen: set[str] = set()
    for row in rows:
        module_id = str(row.get("module") or "").strip()
        if not module_id or module_id in seen:
            continue
        seen.add(module_id)
        modules.append(module_id)
    if not modules:
        return None
    command = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        "--dry-run",
        "--pretty",
    ]
    for module_id in modules:
        command.append(f"--module {module_id}")
    return " ".join(command)


def _shell_quote(value: str) -> str:
    escaped = str(value).replace("'", "'\"'\"'")
    return f"'{escaped}'"


def _collect_upgradable_modules_from_system_rows(rows: list[dict], current: bool = False) -> list[dict]:
    collected: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = "upgradableModuleRows"
        for module_row in row.get(key) or []:
            module_id = str(module_row.get("module") or "").strip()
            if not module_id or module_id in seen:
                continue
            seen.add(module_id)
            collected.append(module_row)
    return collected


def _render_copy_button(command: str | None, label: str = "Copy") -> str:
    if not command:
        return ""
    safe = escape(command).replace("\n", "&#10;")
    return f"<button class=\"copy-button\" type=\"button\" data-copy-command=\"{safe}\">{escape(label)}</button>"


def _render_paginated_table(
    headers: list[str],
    rows_html: list[str],
    table_key: str,
    copy_command: str | None = None,
) -> str:
    total_rows = len(rows_html)
    table_id = f"{table_key}-{abs(hash((table_key, total_rows, ''.join(headers))))}"
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    table_action = _render_copy_button(copy_command, label="Copy Table Command")
    copy_attr = escape(copy_command).replace("\n", "&#10;") if copy_command else ""
    return (
        f"<div class=\"table-meta\" data-table-total=\"{total_rows}\" data-table-id=\"{escape(table_id)}\" data-table-copy-command=\"{copy_attr}\">"
        "<div class=\"table-meta-left\">"
        f"<span class=\"table-count\" data-table-count data-table-total=\"{total_rows}\">{total_rows} row{'s' if total_rows != 1 else ''}</span>"
        "<label class=\"table-filter\">"
        "<span>Filter</span>"
        "<input class=\"table-filter-input\" type=\"search\" data-table-filter placeholder=\"module, system, version or reason\" />"
        "</label>"
        f"{table_action}"
        "</div>"
        "<div class=\"table-pager\" data-table-controls>"
        "<button class=\"pager-button\" type=\"button\" data-table-prev disabled>Prev</button>"
        "<span class=\"pager-status\" data-table-status>1 / 1</span>"
        "<button class=\"pager-button\" type=\"button\" data-table-next disabled>Next</button>"
        "</div>"
        "</div>"
        f"<div class=\"table-wrap paginated-table-wrap\" data-page-size=\"10\" data-mobile-page-size=\"6\" data-table-id=\"{escape(table_id)}\">"
        "<table>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table>"
        "</div>"
    )


def _render_relative_time(raw_value) -> str:
    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return "-"
    delta = datetime.now(timezone.utc) - parsed
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        label = "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        label = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        label = f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = seconds // 86400
        label = f"{days} day{'s' if days != 1 else ''} ago"
    return f"{label} ({parsed.strftime('%Y-%m-%d %H:%M UTC')})"


def _parse_datetime(raw_value):
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _relative_days_label(raw_value) -> str:
    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return ""
    delta = datetime.now(timezone.utc) - parsed
    days = max(int(delta.total_seconds() // 86400), 0)
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


_STYLE = """
:root {
  color-scheme: light;
  --bg: #f5f2ea;
  --bg-top: #f7f4ed;
  --bg-bottom: #efe9de;
  --bg-spot: rgba(31, 107, 91, 0.12);
  --panel: #fffdf9;
  --table-bg: #ffffff;
  --table-head-bg: #faf6ef;
  --table-row-hover: #fffcf7;
  --table-border: #ece5d8;
  --border: #dfd8c9;
  --text: #1f1d18;
  --muted: #6e6557;
  --accent: #1f6b5b;
  --accent-soft: #d9efe8;
  --blue: #2f6fed;
  --blue-soft: #dce8ff;
  --red: #a0372f;
  --red-soft: #f9ddda;
  --green: #216b3b;
  --green-soft: #dff1e4;
  --shadow: 0 12px 30px rgba(47, 34, 18, 0.08);
}
[data-theme="dark"] {
  color-scheme: dark;
  --bg: #11161d;
  --bg-top: #14212a;
  --bg-bottom: #0f1419;
  --bg-spot: rgba(38, 143, 121, 0.26);
  --panel: #1b242c;
  --table-bg: #1a232c;
  --table-head-bg: #22303b;
  --table-row-hover: #243441;
  --table-border: #2c3a45;
  --border: #31414d;
  --text: #e8eff5;
  --muted: #aebcc7;
  --accent: #6fd5bf;
  --accent-soft: rgba(111, 213, 191, 0.18);
  --blue: #7eb4ff;
  --blue-soft: rgba(126, 180, 255, 0.18);
  --red: #ff8f86;
  --red-soft: rgba(255, 143, 134, 0.2);
  --green: #83d99f;
  --green-soft: rgba(131, 217, 159, 0.2);
  --shadow: 0 16px 36px rgba(4, 8, 12, 0.45);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(circle at top right, var(--bg-spot), transparent 28%),
    linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
  color: var(--text);
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
}
.page {
  width: min(1500px, calc(100% - 32px));
  margin: 24px auto 48px;
}
.page-header, .section-card, .summary-card, .idea-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow: var(--shadow);
}
.page-header {
  padding: 24px;
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  margin-bottom: 18px;
}
.header-tools {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.cache-meta {
  font-size: 13px;
}
.foundry-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.theme-toggle {
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  border-radius: 999px;
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
}
.theme-toggle:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.eyebrow {
  margin: 0 0 8px;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 12px;
  color: var(--accent);
}
h1, h2, h3 { margin: 0; }
h4, h5 { margin: 0; }
h1 { font-size: 34px; line-height: 1.1; }
h2 { font-size: 24px; margin-bottom: 6px; }
h3 { font-size: 18px; margin-bottom: 10px; }
.section-note, .system-detail-meta, .pager-status, .table-count {
  color: var(--muted);
}
.lede, .section-heading p, .generated-at, .cache-meta, .empty-state, .idea-card p {
  color: var(--muted);
}
.top-grid, .planner-summary-grid, .ideas-grid {
  display: grid;
  gap: 14px;
}
.top-grid {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  margin-bottom: 18px;
}
.planner-summary-grid {
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  margin-bottom: 18px;
}
.ideas-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.summary-card {
  padding: 16px;
}
.summary-label {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 8px;
}
.summary-value {
  font-size: 22px;
  font-weight: 700;
}
.section-card {
  padding: 22px;
  margin-bottom: 18px;
}
.nested-section + .nested-section {
  margin-top: 22px;
}
.section-heading, .planner-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  margin-bottom: 16px;
}
.planner-control {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 240px;
  color: var(--muted);
  font-size: 13px;
}
select {
  appearance: none;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  font: inherit;
  background: #fff;
  color: var(--text);
}
[data-theme="dark"] select {
  background: #1a232c;
  color: #ffffff;
  border-color: var(--border);
}
.split-grid {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 18px;
}
.split-grid-single {
  grid-template-columns: 1fr;
}
.planner-secondary {
  margin-top: 18px;
}
.planner-glance {
  margin-bottom: 16px;
}
.planner-glance-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}
.glance-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px;
  background: var(--panel);
}
.glance-card .summary-value {
  font-size: 24px;
}
.glance-note {
  margin: 8px 0 0;
  font-size: 14px;
  color: var(--muted);
}
.metric-neutral { color: var(--muted); font-weight: 700; }
.metric-ready { color: var(--green); font-weight: 700; }
.metric-update { color: var(--blue); font-weight: 700; }
.metric-blocked { color: var(--red); font-weight: 700; }
.metric-manual { color: #8a6a1f; font-weight: 700; }
.glance-good {
  border-color: var(--green);
  background: var(--green-soft);
}
.glance-attention {
  border-color: var(--blue);
  background: var(--blue-soft);
}
.glance-bad {
  border-color: var(--red);
  background: var(--red-soft);
}
.view-tabs, .tab-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.view-panel, .tab-panel {
  display: none;
}
.view-panel.is-active, .tab-panel.is-active {
  display: block;
}
.tab-button {
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  border-radius: 999px;
  padding: 9px 14px;
  cursor: pointer;
  font: inherit;
}
.tab-button.is-active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}
.tab-button.tab-blocked {
  border-color: color-mix(in srgb, var(--red) 45%, var(--border));
  color: var(--red);
}
.tab-button.tab-blocked.is-active {
  border-color: var(--red);
  background: var(--red-soft);
  color: var(--red);
}
.tab-button.tab-update {
  border-color: color-mix(in srgb, var(--blue) 45%, var(--border));
  color: var(--blue);
}
.tab-button.tab-update.is-active {
  border-color: var(--blue);
  background: var(--blue-soft);
  color: var(--blue);
}
.tab-button.tab-ready {
  border-color: color-mix(in srgb, var(--green) 45%, var(--border));
  color: var(--green);
}
.tab-button.tab-ready.is-active {
  border-color: var(--green);
  background: var(--green-soft);
  color: var(--green);
}
.tab-button.tab-manual {
  border-color: #d0a844;
  color: #8a6a1f;
}
.tab-button.tab-manual.is-active {
  border-color: #d0a844;
  background: #fff3cc;
  color: #8a6a1f;
}
.tab-button:disabled {
  opacity: 0.5;
  cursor: default;
}
.planner-target { display: none; }
.planner-target.active { display: block; }
.table-wrap {
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 14px;
  max-width: 100%;
}
.table-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}
.table-meta-left {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.table-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.table-filter-input {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 8px;
  font: inherit;
  background: var(--table-bg);
  color: var(--text);
  min-width: 170px;
}
.table-pager {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
table {
  width: 100%;
  min-width: 0;
  border-collapse: collapse;
  background: var(--table-bg);
  table-layout: fixed;
}
th, td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--table-border);
  vertical-align: top;
  text-align: left;
  max-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}
th {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  background: var(--table-head-bg);
}
tbody tr:hover td {
  background: var(--table-row-hover);
}
.module-link {
  display: inline-block;
  max-width: 100%;
  color: var(--accent);
  text-decoration: none;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.module-link:hover { text-decoration: underline; }
.ref-count {
  white-space: nowrap;
  font-weight: 700;
}
.ref-important {
  color: var(--red);
}
.confidence-badge, .percent-badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
}
.confidence-high, .percent-high {
  color: var(--green);
  background: var(--green-soft);
}
.confidence-medium, .percent-medium {
  color: var(--blue);
  background: var(--blue-soft);
}
.confidence-low, .percent-low, .confidence-unknown {
  color: var(--red);
  background: var(--red-soft);
}
.state-bar-cell {
  min-width: 220px;
}
.state-bar {
  height: 12px;
  display: flex;
  overflow: hidden;
  border-radius: 999px;
  background: var(--table-border);
  margin-bottom: 8px;
}
.state-segment {
  display: block;
  height: 100%;
}
.state-ready {
  background: #3a8a58;
}
.state-upgradable {
  background: #2f6fed;
}
.state-blocked {
  background: #b04a3c;
}
.state-unknown {
  background: #8c8577;
}
.state-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 10px;
  font-size: 12px;
  color: var(--muted);
}
.state-empty {
  color: var(--muted);
  font-size: 13px;
}
.legend-ready { color: #2f6b43; }
.legend-upgradable { color: #2f6fed; }
.legend-blocked { color: #a0372f; }
.legend-unknown { color: #6f675a; }
.copy-button {
  border: 1px solid var(--border);
  background: var(--panel);
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  font: inherit;
}
.copy-button:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.pager-button {
  border: 1px solid var(--border);
  background: var(--panel);
  border-radius: 10px;
  padding: 7px 10px;
  cursor: pointer;
  font: inherit;
}
.pager-button:disabled {
  cursor: default;
  opacity: 0.45;
}
.empty-state {
  margin: 0;
  padding: 14px 0 2px;
}
.idea-card {
  padding: 18px;
}
.system-detail-card {
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px;
  background: var(--panel);
  margin-top: 14px;
  overflow: hidden;
}
.system-detail-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: start;
  margin-bottom: 12px;
}
.system-detail-meta {
  margin: 6px 0 0;
}
.system-upgrade-note {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 700;
}
.system-upgrade-ok {
  color: var(--green);
}
.system-upgrade-bad {
  color: var(--red);
}
@media (max-width: 1080px) {
  .split-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .page { width: min(100% - 20px, 1500px); margin-top: 10px; }
  .page-header, .section-heading, .planner-header { flex-direction: column; align-items: stretch; }
  h1 { font-size: 28px; }
}

.quick-summary {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin-bottom: 18px;
}
.quick-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px;
  background: var(--panel);
}
.quick-card-title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.quick-card-value {
  font-size: 30px;
  font-weight: 700;
  margin-top: 8px;
  margin-bottom: 10px;
}
.quick-cta {
  border: 1px solid var(--border);
  background: var(--table-bg);
  color: var(--text);
  border-radius: 10px;
  padding: 10px 12px;
  min-height: 44px;
  font: inherit;
  cursor: pointer;
}
.quick-upgrade .quick-card-value { color: var(--blue); }
.quick-blocked .quick-card-value { color: var(--red); }
.quick-manual .quick-card-value { color: #8a6a1f; }
.quick-ready .quick-card-value { color: var(--green); }
.resolver-controls {
  padding-top: 16px;
  padding-bottom: 16px;
}
.control-grid {
  display: grid;
  gap: 12px;
}
.global-search {
  display: grid;
  gap: 8px;
}
.global-search span {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.global-search input {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--table-bg);
  color: var(--text);
  font: inherit;
  min-height: 44px;
  padding: 10px 12px;
}
.filter-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-chip {
  border: 1px solid var(--border);
  background: var(--table-bg);
  color: var(--text);
  border-radius: 999px;
  min-height: 44px;
  padding: 8px 12px;
  font: inherit;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.filter-chip span {
  background: var(--table-head-bg);
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--muted);
}
.filter-chip.is-active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
.tab-mobile-select {
  display: none;
}
.mobile-action-bar {
  display: none;
}

@media (max-width: 900px) {
  body {
    font-size: 16px;
  }
  .page {
    padding-bottom: 86px;
  }
  .table-meta {
    flex-direction: column;
    align-items: stretch;
  }
  .table-meta-left {
    width: 100%;
  }
  .table-filter {
    flex: 1 1 100%;
  }
  .table-filter-input {
    width: 100%;
    min-height: 44px;
  }
  .copy-button, .pager-button, .tab-button {
    min-height: 44px;
  }
  .tab-mobile-select {
    display: grid;
    gap: 6px;
    margin-bottom: 10px;
  }
  .tab-mobile-select span {
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .tab-mobile-select select {
    min-height: 44px;
    background: var(--table-bg);
  }
  .view-tabs[data-tab-group], .tab-strip[data-tab-group] {
    display: none;
  }
  .paginated-table-wrap {
    overflow: visible;
    border: none;
    border-radius: 0;
    background: transparent;
  }
  .paginated-table-wrap table {
    border-collapse: separate;
    background: transparent;
  }
  .paginated-table-wrap thead {
    display: none;
  }
  .paginated-table-wrap tbody {
    display: grid;
    gap: 10px;
  }
  .paginated-table-wrap tbody tr {
    display: block;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow);
    padding: 6px 10px;
  }
  .paginated-table-wrap tbody td {
    display: grid;
    grid-template-columns: minmax(92px, 34%) 1fr;
    gap: 8px;
    align-items: start;
    border-bottom: 1px solid var(--table-border);
    padding: 8px 0;
    max-width: none;
  }
  .paginated-table-wrap tbody td:last-child {
    border-bottom: none;
  }
  .paginated-table-wrap tbody td::before {
    content: attr(data-col-label);
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .mobile-action-bar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 25;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--panel) 88%, transparent);
    backdrop-filter: blur(10px);
    border-top: 1px solid var(--border);
  }
  .mobile-action-btn {
    border: 1px solid var(--border);
    background: var(--table-bg);
    color: var(--text);
    border-radius: 12px;
    min-height: 44px;
    font: inherit;
    cursor: pointer;
  }
  .mobile-action-btn.is-active {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-soft);
  }
}
"""


_SCRIPT = """
const select = document.getElementById("foundry-target-select");
const sections = Array.from(document.querySelectorAll(".planner-target"));
const themeToggle = document.getElementById("theme-toggle");
const globalSearchInput = document.getElementById("global-module-search");
const filterChipContainer = document.querySelector("[data-filter-chips]");
const quickButtons = Array.from(document.querySelectorAll("[data-quick-filter]"));
const mobileActionButtons = Array.from(document.querySelectorAll("[data-mobile-action]"));
const mobileUpgradeToggle = document.getElementById("mobile-upgrade-toggle");
const resolverControls = document.querySelector(".resolver-controls");
const SEARCH_STORAGE_KEY = "resolver-global-search";
const tableControllers = [];
const filterState = {
  chip: "all",
  globalQuery: "",
  upgradesOnly: false,
};

function getPreferredTheme() {
  const stored = window.localStorage.getItem("resolver-theme");
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme, persist = true) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", normalized);
  if (themeToggle) {
    const isDark = normalized === "dark";
    themeToggle.textContent = isDark ? "☀" : "☾";
    themeToggle.title = isDark ? "Switch to light mode" : "Switch to dark mode";
    themeToggle.setAttribute("aria-pressed", isDark ? "true" : "false");
  }
  if (persist) {
    window.localStorage.setItem("resolver-theme", normalized);
  }
}

applyTheme(getPreferredTheme(), false);

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
}

function syncFoundryTarget(targetVersion) {
  sections.forEach((section) => {
    section.classList.toggle("active", section.dataset.foundryTarget === targetVersion);
  });
}

if (select) {
  syncFoundryTarget(select.value);
  select.addEventListener("change", (event) => {
    syncFoundryTarget(event.target.value);
    refreshInteractiveState();
  });
}

async function copyCommandToClipboard(command, button) {
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    if (button) {
      const previous = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = previous;
      }, 1200);
    }
  } catch (error) {
    if (button) {
      button.textContent = "Copy failed";
    }
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-button");
  if (!button) return;
  const command = button.dataset.copyCommand || "";
  await copyCommandToClipboard(command, button);
});

function activateTabGroup(group, target) {
  if (!group) return;
  const buttons = Array.from(group.querySelectorAll(":scope > .tab-button"));
  const container = group.parentElement;
  const panels = Array.from(container.querySelectorAll(":scope > .tab-panel, :scope > .view-panel"));
  buttons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tabTarget === target);
  });
  panels.forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.tabPanel === target);
  });
  if (group._mobileSelect) {
    group._mobileSelect.value = target;
  }
  refreshInteractiveState();
}

function setupTabMobileSelects() {
  document.querySelectorAll("[data-tab-group]").forEach((group, index) => {
    const buttons = Array.from(group.querySelectorAll(":scope > .tab-button"));
    if (buttons.length < 2) return;
    const wrapper = document.createElement("label");
    wrapper.className = "tab-mobile-select";
    const label = document.createElement("span");
    label.textContent = `Section ${index + 1}`;
    const selectElement = document.createElement("select");
    buttons.forEach((button) => {
      const option = document.createElement("option");
      option.value = button.dataset.tabTarget || "";
      option.textContent = (button.textContent || "").trim();
      selectElement.appendChild(option);
    });
    selectElement.addEventListener("change", (event) => {
      activateTabGroup(group, event.target.value);
    });
    wrapper.appendChild(label);
    wrapper.appendChild(selectElement);
    group.before(wrapper);
    group._mobileSelect = selectElement;
  });
}

setupTabMobileSelects();

document.querySelectorAll("[data-tab-group]").forEach((group) => {
  const buttons = Array.from(group.querySelectorAll(":scope > .tab-button"));
  const initial =
    buttons.find((button) => button.dataset.default === "true") ||
    buttons.find((button) => !button.disabled);
  if (initial) {
    activateTabGroup(group, initial.dataset.tabTarget);
  }
  group.addEventListener("click", (event) => {
    const button = event.target.closest(".tab-button");
    if (!button || button.disabled) return;
    activateTabGroup(group, button.dataset.tabTarget);
  });
});

function formatRelativeTime(isoValue) {
  if (!isoValue) return "-";
  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.getTime())) return "-";
  const seconds = Math.max(Math.floor((Date.now() - parsed.getTime()) / 1000), 0);
  let label = "just now";
  if (seconds >= 86400) {
    const days = Math.floor(seconds / 86400);
    label = `${days} day${days === 1 ? "" : "s"} ago`;
  } else if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600);
    label = `${hours} hour${hours === 1 ? "" : "s"} ago`;
  } else if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    label = `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  const absolute = parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  return `${label} (${absolute})`;
}

function refreshGeneratedAt() {
  document.querySelectorAll("[data-generated-at]").forEach((element) => {
    const rawValue = element.dataset.generatedAt || "";
    element.textContent = `Generated ${formatRelativeTime(rawValue)}`;
  });
}

function formatBytes(totalBytes) {
  const value = Number.parseInt(totalBytes || "0", 10);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const rounded = size >= 10 || unitIndex === 0 ? Math.round(size) : Math.round(size * 10) / 10;
  return `${rounded} ${units[unitIndex]}`;
}

function refreshCacheMeta() {
  document.querySelectorAll("[data-cache-newest-at]").forEach((element) => {
    const newestAt = element.dataset.cacheNewestAt || "";
    const fileCount = Number.parseInt(element.dataset.cacheFiles || "0", 10);
    const totalBytes = element.dataset.cacheBytes || "0";
    const ageLabel = newestAt ? formatRelativeTime(newestAt) : "no cache entries";
    element.textContent = `Cache ${formatBytes(totalBytes)} / ${Number.isFinite(fileCount) ? fileCount : 0} files / newest ${ageLabel}`;
  });
}

refreshGeneratedAt();
refreshCacheMeta();
window.setInterval(refreshGeneratedAt, 15000);
window.setInterval(refreshCacheMeta, 10000);

function annotateMobileTableLabels(table) {
  const headers = Array.from(table.querySelectorAll("thead th")).map((header) => (header.textContent || "").trim());
  const rows = Array.from(table.querySelectorAll("tbody tr"));
  rows.forEach((row) => {
    const cells = Array.from(row.querySelectorAll("td"));
    cells.forEach((cell, index) => {
      cell.setAttribute("data-col-label", headers[index] || `Col ${index + 1}`);
    });
  });
}

function normalizeToken(value) {
  return String(value || "").trim().toLowerCase().replace(/^v(?=\\d)/, "");
}

function rowHasUpgrade(row) {
  const versionCell = Array.from(row.querySelectorAll("td")).find((cell) => (cell.textContent || "").includes("->"));
  if (!versionCell) return false;
  const text = (versionCell.textContent || "").replace(/\\s+/g, " ").trim();
  const match = text.match(/(.+?)\\s*->\\s*(.+)/);
  if (!match) return false;
  const left = normalizeToken(match[1]);
  const right = normalizeToken(match[2]);
  if (!left || !right || left === "-" || right === "-") return false;
  return left !== right;
}

function classifyRow(row) {
  const text = (row.textContent || "").toLowerCase();
  if (text.includes("blocked")) return "blocked";
  if (text.includes("manual")) return "manual";
  if (rowHasUpgrade(row) || text.includes("suggested update") || text.includes("requires update") || text.includes("needs update")) return "upgrade";
  return "nochange";
}

function rowMatchesText(row, query) {
  const normalized = String(query || "").trim().toLowerCase();
  if (!normalized) return true;
  return (row.textContent || "").toLowerCase().includes(normalized);
}

function rowMatchesChip(row) {
  if (filterState.chip === "all") return true;
  return classifyRow(row) === filterState.chip;
}

function getVisibleControllers() {
  return tableControllers.filter((controller) => controller.wrap.offsetParent !== null);
}

function getPrimaryController() {
  const visible = getVisibleControllers();
  return visible[0] || tableControllers[0] || null;
}

function updateChipButtonState() {
  if (!filterChipContainer) return;
  const buttons = Array.from(filterChipContainer.querySelectorAll("[data-chip]"));
  buttons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.chip === filterState.chip);
  });
  if (mobileUpgradeToggle) {
    mobileUpgradeToggle.classList.toggle("is-active", Boolean(filterState.upgradesOnly));
  }
}

function updateChipCounts() {
  const primary = getPrimaryController();
  const rows = primary ? primary.baseRowsForCounts() : [];
  const counts = {
    all: rows.length,
    upgrade: 0,
    blocked: 0,
    manual: 0,
    nochange: 0,
  };
  rows.forEach((row) => {
    const category = classifyRow(row);
    if (Object.prototype.hasOwnProperty.call(counts, category)) {
      counts[category] += 1;
    } else {
      counts.nochange += 1;
    }
  });
  document.querySelectorAll("[data-chip-count]").forEach((element) => {
    const key = element.dataset.chipCount || "";
    element.textContent = String(counts[key] || 0);
  });
}

function renderAllTables(resetPage = true) {
  tableControllers.forEach((controller) => {
    controller.renderPage(resetPage);
  });
  updateChipCounts();
  updateChipButtonState();
}

function refreshInteractiveState() {
  updateChipCounts();
  updateChipButtonState();
}

function setupPaginatedTables() {
  const wraps = Array.from(document.querySelectorAll(".paginated-table-wrap"));
  wraps.forEach((wrap) => {
    const table = wrap.querySelector("table");
    if (!table) return;
    annotateMobileTableLabels(table);
    const desktopPageSize = Number.parseInt(wrap.dataset.pageSize || "10", 10);
    const mobilePageSize = Number.parseInt(wrap.dataset.mobilePageSize || "6", 10);
    const rows = Array.from(wrap.querySelectorAll("tbody tr"));
    const tableId = wrap.dataset.tableId || "";
    const meta =
      (tableId
        ? document.querySelector(`.table-meta[data-table-id="${tableId}"]`)
        : null) || wrap.previousElementSibling;
    const prev = meta ? meta.querySelector("[data-table-prev]") : null;
    const next = meta ? meta.querySelector("[data-table-next]") : null;
    const status = meta ? meta.querySelector("[data-table-status]") : null;
    const count = meta ? meta.querySelector("[data-table-count]") : null;
    const filterInput = meta ? meta.querySelector("[data-table-filter]") : null;
    const totalRows = rows.length;
    const controller = {
      wrap,
      rows,
      page: 0,
      pageCount: 1,
      meta,
      totalRows,
      currentPageSize() {
        return window.matchMedia("(max-width: 900px)").matches ? mobilePageSize : desktopPageSize;
      },
      filteredRowsWithoutChip() {
        const localQuery = filterInput && filterInput.value ? filterInput.value : "";
        return rows.filter((row) => rowMatchesText(row, localQuery) && rowMatchesText(row, filterState.globalQuery));
      },
      baseRowsForCounts() {
        const base = this.filteredRowsWithoutChip();
        if (!filterState.upgradesOnly) return base;
        return base.filter((row) => rowHasUpgrade(row));
      },
      activeRows() {
        return this.baseRowsForCounts().filter((row) => rowMatchesChip(row));
      },
      renderPage(resetPage = false) {
        if (resetPage) this.page = 0;
        const pageSize = Math.max(this.currentPageSize(), 1);
        const filteredRows = this.activeRows();
        const filteredCount = filteredRows.length;
        this.pageCount = Math.max(Math.ceil(filteredCount / pageSize), 1);
        if (this.page > this.pageCount - 1) this.page = Math.max(this.pageCount - 1, 0);
        rows.forEach((row) => {
          row.hidden = true;
        });
        filteredRows.forEach((row, index) => {
          const visible = index >= this.page * pageSize && index < (this.page + 1) * pageSize;
          row.hidden = !visible;
        });
        if (status) {
          status.textContent = filteredCount === 0 ? "0 / 0" : `${this.page + 1} / ${this.pageCount}`;
        }
        if (count) {
          count.textContent = `${filteredCount} / ${totalRows} rows`;
        }
        if (prev) prev.disabled = this.page <= 0;
        if (next) next.disabled = filteredCount === 0 || this.page >= this.pageCount - 1;
      },
    };

    if (prev) {
      prev.addEventListener("click", () => {
        if (controller.page <= 0) return;
        controller.page -= 1;
        controller.renderPage(false);
      });
    }
    if (next) {
      next.addEventListener("click", () => {
        if (controller.page >= controller.pageCount - 1) return;
        controller.page += 1;
        controller.renderPage(false);
      });
    }
    if (filterInput) {
      filterInput.addEventListener("input", () => {
        controller.renderPage(true);
        updateChipCounts();
      });
    }
    tableControllers.push(controller);
    controller.renderPage(false);
  });
}

function setGlobalSearch(value) {
  filterState.globalQuery = String(value || "");
  if (globalSearchInput && globalSearchInput.value !== filterState.globalQuery) {
    globalSearchInput.value = filterState.globalQuery;
  }
  window.localStorage.setItem(SEARCH_STORAGE_KEY, filterState.globalQuery);
  renderAllTables(true);
}

function activateMainView(target) {
  const rootGroup = document.querySelector(".view-tabs[data-tab-group]");
  if (!rootGroup || !target) return;
  activateTabGroup(rootGroup, target);
}

function setupGlobalControls() {
  if (globalSearchInput) {
    const savedSearch = window.localStorage.getItem(SEARCH_STORAGE_KEY) || "";
    if (savedSearch) {
      filterState.globalQuery = savedSearch;
      globalSearchInput.value = savedSearch;
    }
    globalSearchInput.addEventListener("input", (event) => {
      setGlobalSearch(event.target.value || "");
    });
  }

  if (filterChipContainer) {
    filterChipContainer.addEventListener("click", (event) => {
      const button = event.target.closest("[data-chip]");
      if (!button) return;
      filterState.chip = button.dataset.chip || "all";
      renderAllTables(true);
    });
  }

  quickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.quickTarget || "";
      const quickFilter = button.dataset.quickFilter || "all";
      if (target) activateMainView(target);
      filterState.chip = quickFilter;
      filterState.upgradesOnly = false;
      renderAllTables(true);
    });
  });

  mobileActionButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.mobileAction || "";
      if (action === "search") {
        if (globalSearchInput) {
          globalSearchInput.focus();
          globalSearchInput.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      } else if (action === "filters") {
        if (resolverControls) {
          resolverControls.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      } else if (action === "upgrades") {
        filterState.upgradesOnly = !filterState.upgradesOnly;
        renderAllTables(true);
      } else if (action === "copy") {
        const primary = getPrimaryController();
        const command =
          (primary && primary.meta && primary.meta.dataset.tableCopyCommand) ||
          (primary && primary.meta && primary.meta.querySelector(".copy-button") && primary.meta.querySelector(".copy-button").dataset.copyCommand) ||
          "";
        await copyCommandToClipboard(command, button);
      }
    });
  });

  window.addEventListener("resize", () => {
    renderAllTables(false);
  });
}

setupPaginatedTables();
setupGlobalControls();
renderAllTables(false);
"""
