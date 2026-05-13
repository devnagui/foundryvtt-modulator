from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlparse


def render_html_report_v3(payload: dict) -> str:
    view = ((payload.get("reportViews") or {}).get("v3")) or {}
    summary = view.get("summary") or {}
    controls = view.get("controls") or {}
    current = view.get("currentSystemUpgrades") or {}
    planner = view.get("systemUpgradePlanner") or {}
    backup = view.get("backupManagement") or {}
    unused = view.get("unusedModules") or {}

    foundry_version = str(view.get("currentFoundryVersion") or "-")
    generated_at = str(view.get("generatedAt") or "")

    actions = _collect_actions(current.get("rows") or [])
    backup_rows = backup.get("rows") or []
    unused_rows = unused.get("rows") or []
    bulk_commands = _build_bulk_commands_by_state(actions, unused_rows)
    targets = planner.get("targets") or []
    default_target = str(controls.get("defaultFoundryVersion") or (targets[0].get("foundryVersion") if targets else ""))

    blocked_count = sum(1 for row in actions if row["state"] == "blocked")
    upgrade_count = sum(1 for row in actions if row["state"] == "upgrade")
    ready_count = max(int(summary.get("usedModuleCount") or 0) - blocked_count - upgrade_count, 0)
    force_target_version = (default_target or foundry_version or "").strip()
    forced_rows = _load_forced_compatibility_rows(str(payload.get("dataRoot") or ""), force_target_version)
    force_bulk_command = _build_bulk_force_compat_command(forced_rows, force_target_version)

    backup_total_size_bytes = sum(int(row.get("backupSizeBytes") or 0) for row in backup_rows)
    backup_size_label = _format_bytes(backup_total_size_bytes)

    client_payload = _build_client_payload(
        actions=actions,
        targets=targets,
        backup_rows=backup_rows,
        unused_rows=unused_rows,
        forced_rows=forced_rows,
        bulk_commands=bulk_commands,
        foundry_version=foundry_version,
        default_target=default_target,
        force_target_version=force_target_version,
        force_bulk_command=force_bulk_command,
        counts={
            "blocked": blocked_count,
            "upgrade": upgrade_count,
            "manual": 0,
            "ready": ready_count,
            "backups": int(backup.get("totalBackupCount") or 0),
            "unused": int(unused.get("count") or 0),
        },
    )

    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Foundry Dependencies Resolver</title>",
        "<style>",
        _STYLE,
        "</style>",
        "</head>",
        "<body class=\"is-dark\">",
        "<main class=\"page\">",
        "<header class=\"hero\">",
        "<div>",
        "<h1>FoundryVTT Modulator</h1>",
        "<p class=\"hero-copy\">Single-page flow focused on decisions: health, immediate actions, next-version planning, backups, and unused modules.</p>",
        "</div>",
        "<div class=\"hero-actions\">",
        "<button id=\"add-module-open-btn\" class=\"theme-toggle\" type=\"button\" aria-label=\"Add module\" title=\"Add module\"><span aria-hidden=\"true\">+</span></button>",
        "<button id=\"settings-open-btn\" class=\"theme-toggle\" type=\"button\" aria-label=\"Settings\" title=\"Settings\"><span aria-hidden=\"true\">âš™</span></button>",
        "<button id=\"theme-toggle\" class=\"theme-toggle\" type=\"button\" aria-label=\"Toggle dark mode\" title=\"Toggle dark mode\"><span aria-hidden=\"true\">â—</span></button>",
        "<button id=\"logout-btn\" class=\"logout-btn\" type=\"button\" aria-label=\"Logout\" title=\"Logout\"><span class=\"logout-icon\" aria-hidden=\"true\">âŽ‹</span></button>",
        "</div>",
        "<div class=\"hero-meta\">",
        (f"<div class=\"meta-pill\">Foundry {escape(foundry_version)}</div>" if foundry_version and foundry_version != "-" else ""),
        f"<div class=\"meta-pill\" data-generated-at=\"{escape(generated_at)}\">Generated {_relative_time(generated_at)}</div>",
        "</div>",
        "</header>",
        "<input id=\"foundry-root-picker\" type=\"file\" webkitdirectory directory multiple class=\"hidden-input\">",
        "<section class=\"tab-hub\" id=\"tab-hub\">",
        "<div class=\"tab-nav\" role=\"tablist\" aria-label=\"Resolver views\">",
        "<button class=\"tab-btn is-active\" type=\"button\" data-tab-target=\"actions\" aria-selected=\"true\" title=\"Shows the current module status and immediate actions.\">Current</button>",
        "<button class=\"tab-btn\" type=\"button\" data-tab-target=\"planning\" aria-selected=\"false\" title=\"Plans update actions for future Foundry versions.\">Foundry Upgrade</button>",
        "<button class=\"tab-btn\" type=\"button\" data-tab-target=\"backups\" aria-selected=\"false\" title=\"Lists backup maintenance operations.\">Backups</button>",
        "<button class=\"tab-btn\" type=\"button\" data-tab-target=\"unused\" aria-selected=\"false\" title=\"Lists unused modules and compatibility actions.\">Unused Modules</button>",
        "<button class=\"tab-btn\" type=\"button\" data-tab-target=\"forced-compat\" aria-selected=\"false\" title=\"Shows modules that already have forced compatibility flags.\">Forced Compatibility</button>",
        "</div>",
        "</section>",
        "<section class=\"actions tab-section\" id=\"actions\" data-tab-section=\"actions\">",
        f"<h2>Current {escape(foundry_version)}</h2>",
        "<div class=\"panel subtle current-health\">",
        "<h3>Health Snapshot</h3>",
        "<div class=\"health-grid\">",
        _metric_card("Blocked", blocked_count, "critical", filter_state="blocked"),
        _metric_card("Update", upgrade_count, "update", filter_state="upgrade"),
        _metric_card("Stable", ready_count, "ok", filter_state="ready"),
        _metric_card(f"Backups ({backup_size_label})", int(backup.get("totalBackupCount") or 0), "neutral", scroll_target="backups"),
        _metric_card("Unused Modules", int(unused.get("count") or 0), "manual", scroll_target="unused"),
        "</div>",
        "</div>",
        "<p class=\"section-copy\">Health snapshot + immediate module actions with filters and pagination.</p>",
        "<div id=\"module-suggest-panel\" class=\"panel subtle\"><p class=\"section-copy\">Use the <strong>+</strong> button in the header to add a module from its <code>module.json</code> URL.</p></div>",
        "<dialog id=\"settings-modal\" class=\"panel\">",
        "<h3>Settings</h3><p class=\"section-copy\">Manage Foundry path.</p>",
        "<div class=\"toolbar toolbar-right foundry-root-toolbar\">",
        "<label class=\"toolbar-field grow\"><span>Path</span><input id=\"foundry-root-input-modal\" type=\"text\" placeholder=\"Select folder or paste path\"></label>",
        "<div class=\"foundry-root-actions\">",
        "<button id=\"foundry-root-browse-modal\" class=\"copy-btn\" type=\"button\">Select Folder</button>",
        "<button id=\"foundry-root-save-modal\" class=\"copy-btn\" type=\"button\">Validate & Save</button>",
        "<button id=\"foundry-root-reset-modal\" class=\"copy-btn\" type=\"button\">Reset</button>",
        "</div></div><p id=\"foundry-root-status-modal\" class=\"pager-status\"></p><button id=\"settings-close-btn\" class=\"copy-btn\" type=\"button\">Close</button>",
        "</dialog>",
        "<dialog id=\"add-module-modal\" class=\"panel\">",
        "<h3>Add Module</h3><p class=\"section-copy\">Paste the module.json URL. Other fields are not required.</p>",
        "<div class=\"toolbar\"><label class=\"toolbar-field grow\"><span>module.json URL</span><input id=\"suggest-manifest-url\" type=\"text\" placeholder=\"https://.../module.json\"></label><button id=\"suggest-module-btn\" class=\"copy-btn\" type=\"button\">Suggest Best Version</button></div>",
        "<p id=\"suggest-module-status\" class=\"pager-status\">Provide a module.json URL.</p><button id=\"add-module-close-btn\" class=\"copy-btn\" type=\"button\">Close</button>",
        "</dialog>",
        "<div id=\"actions-lazy-root\" class=\"lazy-root\"></div>",
        "</section>",
        "<section class=\"planning tab-section\" id=\"planning\" data-tab-section=\"planning\" hidden>",
        "<h2>Foundry Upgrade</h2>",
        "<p class=\"section-copy\">Target-by-target view with the same filter pattern used in Current.</p>",
        "<div id=\"planning-lazy-root\" class=\"lazy-root\"></div>",
        "</section>",
        "<section class=\"backups tab-section\" id=\"backups\" data-tab-section=\"backups\" hidden>",
        "<h2>Backups</h2>",
        "<p class=\"section-copy\">Backup maintenance commands.</p>",
        "<div id=\"backups-lazy-root\" class=\"lazy-root\"></div>",
        "</section>",
        "<section class=\"unused tab-section\" id=\"unused\" data-tab-section=\"unused\" hidden>",
        "<h2>Unused Modules</h2>",
        "<p class=\"section-copy\">Unused modules, compatibility status, and action commands.</p>",
        "<div id=\"unused-lazy-root\" class=\"lazy-root\"></div>",
        "</section>",
        "<section class=\"forced tab-section\" id=\"forced-compat\" data-tab-section=\"forced-compat\" hidden>",
        "<h2>Forced Compatibility</h2>",
        "<p class=\"section-copy\">Modules already marked as forced by the system (`flags.resolver.forcedCompatibility`).</p>",
        "<div id=\"forced-lazy-root\" class=\"lazy-root\"></div>",
        "</section>",
        f"<script type=\"application/json\" id=\"report-v3-data\">{_json_for_html(client_payload)}</script>",
        "<script>",
        _SCRIPT,
        "</script>",
        "<script>",
        _SERVICE_GATEWAY_SCRIPT,
        "</script>",
        "</main>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


def _collect_actions(system_rows: list[dict]) -> list[dict]:
    severity = {"blocked": 0, "upgrade": 1, "ready": 2}
    by_module: dict[str, dict] = {}

    mapping = [
        ("blocked", "blockedModuleRows"),
        ("upgrade", "upgradableModuleRows"),
        ("ready", "unknownModuleRows"),
        ("ready", "compatibleModuleRows"),
    ]

    for system in system_rows:
        system_name = str(system.get("title") or system.get("systemId") or "-")
        for state, key in mapping:
            for row in system.get(key) or []:
                module_id = str(row.get("module") or "").strip()
                if not module_id:
                    continue
                item = {
                    "module": module_id,
                    "title": str(row.get("title") or module_id),
                    "state": state,
                    "installed": str(row.get("installedVersion") or "-"),
                    "recommended": str(row.get("recommendedVersion") or "-"),
                    "reason": str(row.get("reason") or ""),
                    "compatibility": row.get("compatibility") if isinstance(row.get("compatibility"), dict) else {},
                    "systems": {system_name},
                    "manifestUrl": str(row.get("manifestUrl") or "").strip(),
                    "downloadUrl": str(row.get("downloadUrl") or "").strip(),
                    "confidence": str(row.get("confidence") or ""),
                    "attentionFlag": bool(row.get("attentionFlag")),
                }
                existing = by_module.get(module_id)
                if existing is None:
                    by_module[module_id] = item
                    continue
                existing["systems"].add(system_name)
                existing["attentionFlag"] = bool(existing.get("attentionFlag")) or bool(item.get("attentionFlag"))
                if severity[state] < severity[existing["state"]]:
                    existing.update({
                        "state": state,
                        "installed": item["installed"],
                        "recommended": item["recommended"],
                        "reason": item["reason"],
                        "compatibility": item["compatibility"],
                        "manifestUrl": item["manifestUrl"],
                        "downloadUrl": item["downloadUrl"],
                        "confidence": item["confidence"],
                        "attentionFlag": item["attentionFlag"],
                    })

    rows = list(by_module.values())
    for row in rows:
        row["systemsLabel"] = ", ".join(sorted(row["systems"]))
        row["command"] = _build_module_command(row)
    rows.sort(
        key=lambda row: (
            severity.get(row["state"], 99),
            0 if bool(row.get("attentionFlag")) else 1,
            row["title"].lower(),
        )
    )
    return rows


def _build_module_command(row: dict) -> str:
    module = str(row.get("module") or "")
    recommended = str(row.get("recommended") or "")
    state = str(row.get("state") or "")
    if not module:
        return ""
    base = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
    ]
    if state == "upgrade":
        base.append("--apply")
        base.append(f"--module {module}")
        if recommended and recommended != "-":
            base.append(f"--expected-version {module}={recommended}")
    elif state == "blocked":
        return ""
    else:
        return ""
    return " ".join(base)


def _build_bulk_commands_by_state(actions: list[dict], unused_rows: list[dict]) -> dict[str, str]:
    grouped: dict[str, list[dict]] = {"upgrade": []}
    for row in actions:
        state = str(row.get("state") or "")
        if state in grouped:
            grouped[state].append(row)

    commands: dict[str, str] = {}
    for state, rows in grouped.items():
        module_ids = sorted({str(row.get("module") or "").strip() for row in rows if str(row.get("module") or "").strip()})
        if not module_ids:
            commands[state] = ""
            continue
        base = [
            "cd /home/engrenado/config/foundryModuleVersioningTool &&",
            "python3 -m resolver.cli",
            "--data-root /home/engrenado/foundry/data",
        ]
        if state == "upgrade":
            base.append("--apply")
        else:
            base.append("--dry-run")
        for module_id in module_ids:
            base.append(f"--module {module_id}")
        if state == "upgrade":
            for row in rows:
                module_id = str(row.get("module") or "").strip()
                expected = str(row.get("recommended") or "").strip()
                if module_id and expected and expected != "-":
                    base.append(f"--expected-version {module_id}={expected}")
        commands[state] = " ".join(base)

    unused_ids = sorted(
        {str(row.get("module") or "").strip() for row in unused_rows if str(row.get("module") or "").strip()}
    )
    if unused_ids:
        command = [
            "cd /home/engrenado/config/foundryModuleVersioningTool &&",
            "python3 -m resolver.cli",
            "--data-root /home/engrenado/foundry/data",
            "--delete-unused-modules",
        ]
        for module_id in unused_ids:
            command.append(f"--delete-module {module_id}")
        commands["unused"] = " ".join(command)
    else:
        commands["unused"] = ""
    return commands


def _build_bulk_force_compat_command(rows: list[dict], target_version: str) -> str:
    modules = sorted({str(row.get("module") or "").strip() for row in rows if str(row.get("module") or "").strip()})
    if not modules or not target_version or target_version == "-":
        return ""
    base = [
        "cd /home/engrenado/config/foundryModuleVersioningTool &&",
        "python3 -m resolver.cli",
        "--data-root /home/engrenado/foundry/data",
        f"--force-compat-version {target_version}",
    ]
    for module_id in modules:
        base.append(f"--force-compat-module {module_id}")
    return " ".join(base)


def _build_force_compat_command(module_id: str, target_version: str) -> str:
    clean_module = str(module_id or "").strip()
    clean_target = str(target_version or "").strip()
    if not clean_module or not clean_target or clean_target == "-":
        return ""
    return " ".join(
        [
            "cd /home/engrenado/config/foundryModuleVersioningTool &&",
            "python3 -m resolver.cli",
            "--data-root /home/engrenado/foundry/data",
            f"--force-compat-version {clean_target}",
            f"--force-compat-module {clean_module}",
        ]
    )


def _json_for_html(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _load_forced_compatibility_rows(data_root: str, fallback_target_version: str) -> list[dict]:
    root = Path(data_root) if data_root else Path("")
    modules_root = root / "Data" / "modules"
    if not modules_root.exists() or not modules_root.is_dir():
        return []

    rows: list[dict] = []
    for module_json in sorted(modules_root.glob("*/module.json")):
        if ".bak." in module_json.parent.name:
            continue
        try:
            manifest = json.loads(module_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        flags = manifest.get("flags") if isinstance(manifest.get("flags"), dict) else {}
        resolver_flags = flags.get("resolver") if isinstance(flags.get("resolver"), dict) else {}
        forced = resolver_flags.get("forcedCompatibility") if isinstance(resolver_flags.get("forcedCompatibility"), dict) else {}
        if not bool(forced.get("enabled")):
            continue

        compatibility = manifest.get("compatibility") if isinstance(manifest.get("compatibility"), dict) else {}
        module_id = str(manifest.get("id") or module_json.parent.name)
        target_version = str(
            forced.get("targetVersion") or compatibility.get("maximum") or fallback_target_version or "-"
        )
        applied_at = _relative_time(forced.get("appliedAt"))
        systems = []
        relationships = manifest.get("relationships") if isinstance(manifest.get("relationships"), dict) else {}
        for entry in relationships.get("systems") or []:
            if isinstance(entry, dict):
                system_id = str(entry.get("id") or "").strip()
                if system_id:
                    systems.append(system_id)
        systems_label = ", ".join(sorted(set(systems))) if systems else "-"
        reason = f"Forced flag enabled in module.json. Applied: {applied_at}."
        rows.append(
            {
                "module": module_id,
                "title": str(manifest.get("title") or module_id),
                "systemsLabel": systems_label,
                "reason": reason,
                "targetVersion": target_version,
                "version": str(manifest.get("version") or "-"),
                "minimum": str(compatibility.get("minimum") or "-"),
                "verified": str(compatibility.get("verified") or "-"),
                "maximum": str(compatibility.get("maximum") or "-"),
                "command": _build_force_compat_command(module_id, target_version if target_version != "-" else fallback_target_version),
            }
        )
    rows.sort(key=lambda row: str(row.get("title") or row.get("module") or "").lower())
    return rows


def _build_client_payload(
    actions: list[dict],
    targets: list[dict],
    backup_rows: list[dict],
    unused_rows: list[dict],
    forced_rows: list[dict],
    bulk_commands: dict[str, str],
    foundry_version: str,
    default_target: str,
    force_target_version: str,
    force_bulk_command: str,
    counts: dict[str, int],
) -> dict:
    action_rows = [_serialize_action_row(row) for row in actions]
    target_rows = [_serialize_target(target) for target in targets]
    return {
        "foundryVersion": foundry_version,
        "defaultTarget": default_target,
        "forceTargetVersion": force_target_version,
        "counts": counts,
        "bulkCommands": {
            "upgrade": str(bulk_commands.get("upgrade") or ""),
            "unused": str(bulk_commands.get("unused") or ""),
        },
        "forceBulkCommand": str(force_bulk_command or ""),
        "actions": action_rows,
        "forcedRows": [_serialize_forced_row(row, force_target_version) for row in forced_rows],
        "targets": target_rows,
        "backups": _serialize_backup_rows(backup_rows),
        "unusedModules": _serialize_unused_rows(unused_rows, foundry_version),
    }


def _serialize_action_row(row: dict) -> dict:
    state = str(row.get("state") or "ready")
    reason = str(row.get("reason") or "No additional detail.")
    compatibility = row.get("compatibility") if isinstance(row.get("compatibility"), dict) else {}
    if state == "ready":
        reason = reason.replace("manual verification required", "no direct action required")
        reason = reason.replace("Manual verification required", "No direct action required")
    return {
        "module": str(row.get("module") or ""),
        "title": str(row.get("title") or row.get("module") or "-"),
        "state": state,
        "installed": str(row.get("installed") or "-"),
        "recommended": str(row.get("recommended") or "-"),
        "reason": reason,
        "systemsLabel": str(row.get("systemsLabel") or "-"),
        "compatibility": {
            "minimum": str(compatibility.get("minimum") or ""),
            "verified": str(compatibility.get("verified") or ""),
            "maximum": str(compatibility.get("maximum") or ""),
        },
        "compatibilityLabel": _format_compatibility_label(compatibility),
        "link": str(_pick_link(row) or ""),
        "command": str(row.get("command") or ""),
        "attentionFlag": bool(row.get("attentionFlag")),
    }


def _serialize_forced_row(row: dict, target_version: str) -> dict:
    module_id = str(row.get("module") or "")
    normalized_target = str(row.get("targetVersion") or target_version or "-")
    return {
        "module": module_id,
        "title": str(row.get("title") or module_id or "-"),
        "systemsLabel": str(row.get("systemsLabel") or "-"),
        "reason": str(row.get("reason") or "No details available."),
        "targetVersion": normalized_target,
        "version": str(row.get("version") or "-"),
        "minimum": str(row.get("minimum") or "-"),
        "verified": str(row.get("verified") or "-"),
        "maximum": str(row.get("maximum") or "-"),
        "compatibilityLabel": _format_compatibility_label(
            {
                "minimum": str(row.get("minimum") or ""),
                "verified": str(row.get("verified") or ""),
                "maximum": str(row.get("maximum") or ""),
            }
        ),
        "command": str(row.get("command") or _build_force_compat_command(module_id, normalized_target)),
    }


def _serialize_target(target: dict) -> dict:
    version = str(target.get("foundryVersion") or "")
    system_rows = target.get("systemRows") or []
    systems_total = len(system_rows)
    systems_ready = sum(1 for row in system_rows if bool(row.get("targetReady")))
    quick = target.get("quickStatus") or {}
    systems = []
    for row in target.get("systems") or []:
        try:
            coverage_value = float(row.get("coveragePercent") or 0.0)
        except (TypeError, ValueError):
            coverage_value = 0.0
        systems.append(
            {
                "title": str(row.get("title") or row.get("systemId") or "-"),
                "installedVersion": str(row.get("installedVersion") or "-"),
                "targetVersion": str(row.get("targetVersion") or "-"),
                "coveragePercent": coverage_value,
                "worldsLabel": ", ".join(row.get("worldAliases") or []) or "-",
                "targetReady": bool(row.get("targetReady")),
            }
        )

    return {
        "foundryVersion": version,
        "label": str(target.get("label") or version or "-"),
        "quickStatus": {
            "verdict": str(quick.get("verdict") or "manual"),
            "verdictLabel": str(quick.get("verdictLabel") or "Review"),
            "systemsReady": int(quick.get("systemsReady") or systems_ready),
            "systemsTotal": int(quick.get("systemsTotal") or systems_total),
            "modulesReady": int(quick.get("modulesReady") or 0),
            "modulesNeedUpdate": int(quick.get("modulesNeedUpdate") or 0),
            "modulesBlocked": int(quick.get("modulesBlocked") or 0),
        },
        "readyModules": _serialize_target_module_rows(target.get("readyModules") or [], "ready", version),
        "systems": systems,
        "systemsNotReady": _serialize_target_system_rows(system_rows, only_not_ready=True),
        "blockedModules": _serialize_target_module_rows(target.get("blockedModules") or [], "blocked", version),
        "upgradableModules": _serialize_target_module_rows(target.get("upgradableModules") or [], "upgrade", version),
        "notReadyModules": _serialize_target_module_rows(target.get("notReadyModules") or [], "not-ready", version),
    }


def _serialize_target_system_rows(rows: list[dict], only_not_ready: bool = False) -> list[dict]:
    output = []
    for row in rows:
        target_ready = bool(row.get("targetReady"))
        if only_not_ready and target_ready:
            continue
        try:
            coverage_percent = float(row.get("coveragePercent") or 0.0)
        except (TypeError, ValueError):
            coverage_percent = 0.0
        output.append(
            {
                "title": str(row.get("title") or row.get("systemId") or "-"),
                "systemId": str(row.get("systemId") or ""),
                "installedVersion": str(row.get("installedVersion") or "-"),
                "targetVersion": str(row.get("targetVersion") or "-"),
                "targetReady": target_ready,
                "coveragePercent": coverage_percent,
                "reason": str((row.get("stateSummary") or {}).get("headline") or "No compatible system release detected for this target."),
                "worldsLabel": ", ".join(row.get("worldAliases") or []) or "-",
            }
        )
    return output


def _serialize_target_module_rows(rows: list[dict], state: str, target_version: str) -> list[dict]:
    output = []
    for row in rows:
        module_id = str(row.get("module") or "")
        recommended = str(row.get("recommendedVersion") or "-")
        installed = str(row.get("installedVersion") or "-")
        status = str(row.get("status") or state)
        compatibility = row.get("compatibility") if isinstance(row.get("compatibility"), dict) else {}
        if state == "blocked" or (state == "not-ready" and status == "blocked"):
            command = _build_force_compat_command(module_id, target_version)
        elif state == "upgrade":
            command = _build_module_command(
                {
                    "module": module_id,
                    "recommended": recommended,
                    "state": "upgrade",
                }
            )
        else:
            command = ""
        output.append(
            {
                "module": module_id,
                "title": str(row.get("title") or module_id or "-"),
                "installedVersion": installed,
                "recommendedVersion": recommended,
                "reason": str(row.get("reason") or ""),
                "state": state,
                "status": status,
                "compatibility": {
                    "minimum": str(compatibility.get("minimum") or ""),
                    "verified": str(compatibility.get("verified") or ""),
                    "maximum": str(compatibility.get("maximum") or ""),
                },
                "command": command,
                "link": str(_pick_link(row) or ""),
                "attentionFlag": bool(row.get("attentionFlag")),
            }
        )
    return output


def _serialize_backup_rows(rows: list[dict]) -> list[dict]:
    output = []
    for row in sorted(rows, key=lambda item: int(item.get("backupSizeBytes") or 0), reverse=True):
        module_id = str(row.get("module") or "")
        output.append(
            {
                "module": module_id,
                "title": str(row.get("title") or module_id or "-"),
                "backupCount": int(row.get("backupCount") or 0),
                "backupSizeBytes": int(row.get("backupSizeBytes") or 0),
                "backupSizeLabel": _format_bytes(int(row.get("backupSizeBytes") or 0)),
                "newestBackupLabel": _relative_time(row.get("newestBackupAt")),
                "command": (
                    "cd /home/engrenado/config/foundryModuleVersioningTool && "
                    "python3 -m resolver.cli --data-root /home/engrenado/foundry/data "
                    f"--cleanup-backups --cleanup-backup-module {module_id}"
                ),
            }
        )
    return output


def _serialize_unused_rows(rows: list[dict], foundry_version: str) -> list[dict]:
    output = []
    for row in rows:
        module_id = str(row.get("module") or "")
        update_viable = bool(row.get("updateViable"))
        compatibility = row.get("compatibility") if isinstance(row.get("compatibility"), dict) else {}
        compatibility_status = _compatibility_status_for_foundry(
            compatibility,
            foundry_version,
        )
        output.append(
            {
                "module": module_id,
                "title": str(row.get("title") or module_id or "-"),
                "installedVersion": str(row.get("installedVersion") or "-"),
                "recommendedVersion": str(row.get("recommendedVersion") or "-"),
                "moduleSizeBytes": int(row.get("moduleSizeBytes") or 0),
                "moduleSizeLabel": _format_bytes(int(row.get("moduleSizeBytes") or 0)),
                "updateViable": update_viable,
                "reason": str(row.get("reason") or "Compatibility metadata is incomplete for this module."),
                "compatibilityLabel": _format_compatibility_label(compatibility),
                "compatibilityStatus": compatibility_status,
                "compatibility": {
                    "minimum": str(compatibility.get("minimum") or ""),
                    "verified": str(compatibility.get("verified") or ""),
                    "maximum": str(compatibility.get("maximum") or ""),
                },
                "foundryVersion": str(foundry_version or ""),
                "systemsLabel": _format_system_ids(row.get("systemIds")),
                "link": str(_pick_link(row) or ""),
                "state": "upgrade" if update_viable else "manual",
                "command": (
                    "cd /home/engrenado/config/foundryModuleVersioningTool && "
                    "python3 -m resolver.cli --data-root /home/engrenado/foundry/data "
                    f"--delete-unused-modules --delete-module {module_id}"
                ),
            }
        )
    return output


def _format_compatibility_label(raw_compatibility) -> str:
    compatibility = raw_compatibility if isinstance(raw_compatibility, dict) else {}
    minimum = str(compatibility.get("minimum") or "")
    verified = str(compatibility.get("verified") or "")
    maximum = str(compatibility.get("maximum") or "")
    parts = []
    if minimum:
        parts.append(f"minimum {minimum}")
    if verified:
        parts.append(f"verified {verified}")
    if maximum:
        parts.append(f"maximum {maximum}")
    if not parts:
        return "Foundry compatibility metadata unavailable."
    return "Foundry compatibility: " + ", ".join(parts) + "."


def _format_system_ids(raw_system_ids) -> str:
    if not isinstance(raw_system_ids, list):
        return ""
    values = [str(item).strip() for item in raw_system_ids if str(item).strip()]
    if not values:
        return ""
    return ", ".join(sorted(set(values)))


def _compatibility_status_for_foundry(raw_compatibility, foundry_version: str) -> str:
    compatibility = raw_compatibility if isinstance(raw_compatibility, dict) else {}
    target_major = _parse_major_version(foundry_version)
    if target_major is None:
        return "unknown"
    minimum_major = _parse_major_version(str(compatibility.get("minimum") or ""))
    verified_major = _parse_major_version(str(compatibility.get("verified") or ""))
    maximum_major = _parse_major_version(str(compatibility.get("maximum") or ""))
    if minimum_major is not None and target_major < minimum_major:
        return "incompatible"
    if maximum_major is not None and target_major > maximum_major:
        return "incompatible"
    if verified_major is not None and verified_major == target_major:
        return "compatible"
    if minimum_major is not None or maximum_major is not None:
        return "compatible"
    return "unknown"


def _parse_major_version(raw_value: str) -> int | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    token = value.split(".", 1)[0].strip()
    if not token.isdigit():
        return None
    try:
        return int(token)
    except ValueError:
        return None


def _metric_card(
    label: str,
    value: int,
    tone: str,
    compact: bool = False,
    filter_state: str | None = None,
    scroll_target: str | None = None,
) -> str:
    klass = "metric compact" if compact else "metric"
    if filter_state or scroll_target:
        filter_attr = f" data-filter-state=\"{escape(filter_state)}\"" if filter_state else ""
        scroll_attr = f" data-scroll-target=\"{escape(scroll_target)}\"" if scroll_target else ""
        if filter_state:
            title = f"Filters Current to {label.lower()} modules."
        elif scroll_target:
            title = f"Opens the related tab for {label.lower()}."
        else:
            title = f"Shows details for {label.lower()}."
        title_attr = f" title=\"{escape(title)}\""
        return (
            f"<button class=\"{klass} metric-btn tone-{escape(tone)}\" type=\"button\"{filter_attr}{scroll_attr}{title_attr}>"
            f"<div class=\"metric-label\">{escape(label)}</div>"
            f"<div class=\"metric-value\">{escape(str(value))}</div>"
            "</button>"
        )
    return (
        f"<article class=\"{klass} tone-{escape(tone)}\">"
        f"<div class=\"metric-label\">{escape(label)}</div>"
        f"<div class=\"metric-value\">{escape(str(value))}</div>"
        "</article>"
    )


def _pick_link(row: dict) -> str | None:
    explicit = str(row.get("releaseUrl") or "").strip()
    if explicit:
        normalized = _canonical_update_link(explicit)
        if normalized:
            return normalized
    download = str(row.get("downloadUrl") or "").strip()
    project = str(row.get("projectUrl") or "").strip()
    manifest = str(row.get("manifestUrl") or "").strip()
    for candidate in (download, project, manifest):
        normalized = _canonical_update_link(candidate)
        if normalized:
            return normalized
    return None


def _canonical_update_link(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    release = _github_release_url(value) or _github_release_from_raw_manifest(value) or _gitlab_release_url(value)
    if release:
        return release
    if _is_manifest_like_url(value):
        return None
    repo = _github_repo_url(value) or _gitlab_repo_url(value)
    return repo or value


def _is_manifest_like_url(raw_url: str) -> bool:
    value = str(raw_url or "").strip().lower()
    return value.endswith("/module.json") or value.endswith("/system.json") or value.endswith("/manifest.json")


def _github_release_url(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    if host not in {"github.com", "www.github.com"}:
        return None
    path = parsed.path.rstrip("/")
    if "/releases/latest/download/" in path:
        base, _, _ = path.partition("/releases/latest/download/")
        if base:
            return f"{parsed.scheme}://{parsed.netloc}{base}/releases/latest"
        return None
    if "/releases/download/" in path:
        base, _, rest = path.partition("/releases/download/")
        tag = rest.split("/", 1)[0].strip()
        if base and tag:
            return f"{parsed.scheme}://{parsed.netloc}{base}/releases/tag/{tag}"
    if "/blob/" in path and _is_manifest_like_url(value):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 4:
            owner, repo, ref = parts[0], parts[1], parts[3]
            if owner and repo and ref:
                return f"{parsed.scheme}://{parsed.netloc}/{owner}/{repo}/releases/tag/{ref}"
    return None


def _github_release_from_raw_manifest(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() != "raw.githubusercontent.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3:
        return None
    owner, repo, ref = parts[0], parts[1], parts[2]
    if not owner or not repo or not ref:
        return None
    return f"https://github.com/{owner}/{repo}/releases/tag/{ref}"


def _github_repo_url(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    if host not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if not owner or not repo:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/{owner}/{repo}"


def _gitlab_release_url(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    if host not in {"gitlab.com", "www.gitlab.com"}:
        return None
    path = parsed.path.rstrip("/")
    if "/-/releases/" in path:
        return value
    if "/-/archive/" in path:
        base, _, rest = path.partition("/-/archive/")
        tag = rest.split("/", 1)[0].strip()
        if base and tag:
            return f"{parsed.scheme}://{parsed.netloc}{base}/-/releases/{tag}"
    if "/-/raw/" in path or "/-/blob/" in path:
        marker = "/-/raw/" if "/-/raw/" in path else "/-/blob/"
        base, _, rest = path.partition(marker)
        ref = rest.split("/", 1)[0].strip()
        if base and ref:
            return f"{parsed.scheme}://{parsed.netloc}{base}/-/releases/{ref}"
    return None


def _gitlab_repo_url(raw_url: str) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    if host not in {"gitlab.com", "www.gitlab.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"


def _relative_time(raw_value) -> str:
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


def _format_bytes(value: int) -> str:
    size = float(max(int(value), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0 or size >= 10:
        return f"{int(round(size))} {units[index]}"
    return f"{size:.1f} {units[index]}"


_STYLE = """
:root {
  --bg: #f5f7fa;
  --bg-accent: rgba(88, 166, 255, 0.12);
  --ink: #18212b;
  --muted: #536273;
  --surface: #ffffff;
  --surface-elevated: #f8fafc;
  --surface-muted: #eef2f6;
  --panel: var(--surface);
  --line: #d6dee8;
  --line-strong: #b7c5d6;
  --focus: #58a6ff;
  --critical: #d73a49;
  --critical-soft: #fde8eb;
  --update: #1f6feb;
  --update-soft: #e7f0ff;
  --manual: #b7791f;
  --manual-soft: #fff5e6;
  --ok: #2da44e;
  --ok-soft: #e6f6ea;
  --neutral: #edf2f7;
  --shadow: 0 8px 20px rgba(16, 24, 40, 0.08);
}
body.is-dark {
  --bg: #0f1419;
  --bg-accent: rgba(88, 166, 255, 0.16);
  --ink: #e6edf3;
  --muted: #9fb0c0;
  --surface: #161d24;
  --surface-elevated: #1d2630;
  --surface-muted: #202b36;
  --panel: var(--surface);
  --line: #2b3846;
  --line-strong: #405366;
  --focus: #58a6ff;
  --critical: #f85149;
  --critical-soft: rgba(248, 81, 73, 0.16);
  --update: #58a6ff;
  --update-soft: rgba(88, 166, 255, 0.16);
  --manual: #d29922;
  --manual-soft: rgba(210, 153, 34, 0.18);
  --ok: #3fb950;
  --ok-soft: rgba(63, 185, 80, 0.16);
  --neutral: #25313d;
  --shadow: 0 8px 24px rgba(0, 0, 0, 0.32);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: radial-gradient(circle at top right, var(--bg-accent), transparent 34%), var(--bg);
  color: var(--ink);
  font-family: "Bricolage Grotesque", "Trebuchet MS", sans-serif;
}
a,
a:visited {
  color: var(--update);
}
.page {
  width: min(1180px, calc(100% - 28px));
  margin: 20px auto 90px;
}
.hero, .top-controls, .health, .actions, .planning, .backups, .unused, .forced, .panel, .row-card, .metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: var(--shadow);
}
.hero {
  position: relative;
  padding: 20px;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
}
h1 { margin: 0; font-size: 34px; line-height: 1.05; }
.hero-copy { margin: 10px 0 0; color: var(--muted); max-width: 70ch; }
.hero-meta { display: grid; gap: 8px; justify-items: end; margin-top: 54px; margin-left: auto; }
.hero-actions {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 2;
  display: inline-flex;
  gap: 8px;
  align-items: center;
}
.theme-toggle {
  width: 52px;
  height: 52px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-elevated);
  color: var(--ink);
  font: inherit;
  font-size: 35px;
  display: inline-grid;
  place-items: center;
  cursor: pointer;
}
.theme-toggle.needs-config {
  border-color: #d29922;
  background: rgba(210, 153, 34, 0.2);
  color: #f6d28b;
  box-shadow: 0 0 0 2px rgba(210, 153, 34, 0.25);
}
.logout-btn {
  width: 52px;
  height: 52px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-elevated);
  color: var(--ink);
  font: inherit;
  font-size: 35px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.logout-icon {
  display: inline-block;
  font-weight: 800;
}
.logout-btn span:not(.logout-icon) {
  display: none !important;
}
.hidden-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.meta-pill, .meta-link {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--muted);
  text-decoration: none;
  background: var(--surface-elevated);
}
.meta-link { color: var(--update); border-color: var(--line-strong); }
section { margin-top: 14px; padding: 16px; }
h2 { margin: 0 0 10px; font-size: 24px; }
.section-copy { margin: 0 0 10px; color: var(--muted); }
.tab-hub {
  padding: 10px;
  margin-top: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow);
}
.tab-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.tab-btn {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0 14px;
  background: var(--surface-elevated);
  font: inherit;
  color: var(--muted);
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.tab-btn.is-active {
  border-color: var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  font-weight: 700;
}
.tab-btn:hover {
  border-color: var(--line-strong);
  background: var(--surface);
}
.tab-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.tab-btn[data-tab-target="actions"] {
  border-color: #b6e0c4;
  background: #dcf1e2;
  color: #1e6d3c;
}
.tab-btn[data-tab-target="actions"].is-active {
  border-color: #79b88f;
  background: #bfe4cc;
  color: #175631;
}
.tab-btn[data-tab-target="planning"] {
  border-color: #b9ccf5;
  background: #dce8ff;
  color: #1f5bbd;
}
.tab-btn[data-tab-target="planning"].is-active {
  border-color: #789fef;
  background: #c4d8ff;
  color: #17479a;
}
.tab-btn[data-tab-target="backups"] {
  border-color: #e7ca8c;
  background: #ffefcf;
  color: #8a5e00;
}
.tab-btn[data-tab-target="backups"].is-active {
  border-color: #d0ad57;
  background: #f8dfad;
  color: #6f4a00;
}
.tab-btn[data-tab-target="unused"] {
  border-color: #e7ca8c;
  background: #ffefcf;
  color: #8a5e00;
}
.tab-btn[data-tab-target="unused"].is-active {
  border-color: #d0ad57;
  background: #f8dfad;
  color: #6f4a00;
}
.tab-btn[data-tab-target="forced-compat"] {
  border-color: #e3b1b1;
  background: #f8dede;
  color: #a33636;
}
.tab-btn[data-tab-target="forced-compat"].is-active {
  border-color: #cf8585;
  background: #f1c8c8;
  color: #822626;
}
body.is-dark .tab-btn[data-tab-target="actions"] {
  border-color: rgba(63, 185, 80, 0.35);
  background: rgba(63, 185, 80, 0.1);
  color: #9fdca9;
}
body.is-dark .tab-btn[data-tab-target="actions"].is-active {
  border-color: rgba(63, 185, 80, 0.6);
  background: rgba(63, 185, 80, 0.18);
  color: #d6ffe0;
}
body.is-dark .tab-btn[data-tab-target="planning"] {
  border-color: rgba(88, 166, 255, 0.35);
  background: rgba(88, 166, 255, 0.1);
  color: #bddbff;
}
body.is-dark .tab-btn[data-tab-target="planning"].is-active {
  border-color: rgba(88, 166, 255, 0.62);
  background: rgba(88, 166, 255, 0.18);
  color: #e5f0ff;
}
body.is-dark .tab-btn[data-tab-target="backups"],
body.is-dark .tab-btn[data-tab-target="unused"] {
  border-color: rgba(210, 153, 34, 0.35);
  background: rgba(210, 153, 34, 0.12);
  color: #f4d18f;
}
body.is-dark .tab-btn[data-tab-target="backups"].is-active,
body.is-dark .tab-btn[data-tab-target="unused"].is-active {
  border-color: rgba(210, 153, 34, 0.65);
  background: rgba(210, 153, 34, 0.22);
  color: #ffe2a6;
}
body.is-dark .tab-btn[data-tab-target="forced-compat"] {
  border-color: rgba(248, 81, 73, 0.35);
  background: rgba(248, 81, 73, 0.1);
  color: #ffc1bd;
}
body.is-dark .tab-btn[data-tab-target="forced-compat"].is-active {
  border-color: rgba(248, 81, 73, 0.62);
  background: rgba(248, 81, 73, 0.2);
  color: #ffd8d5;
}
.tab-section[hidden] {
  display: none !important;
}
.actions {
  border-top: 6px solid #79b88f;
}
.planning {
  border-top: 6px solid #789fef;
}
.backups, .unused {
  border-top: 6px solid #d0ad57;
}
.forced {
  border-top: 6px solid #cf8585;
}
.actions h2 { color: #175631; }
.planning h2 { color: #17479a; }
.backups h2, .unused h2 { color: #6f4a00; }
.forced h2 { color: #822626; }
body.is-dark .actions h2 { color: #8ddf9f; }
body.is-dark .planning h2 { color: #a7ceff; }
body.is-dark .backups h2, body.is-dark .unused h2 { color: #f0c879; }
body.is-dark .forced h2 { color: #ffb3ad; }
.lazy-root {
  display: grid;
  gap: 10px;
}
.list-toolbar {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: end;
  flex-wrap: wrap;
}
.toolbar-right {
  justify-content: flex-end;
  width: 100%;
}
.toolbar-right .toolbar-field.grow {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
  grid-column: auto;
}
.toolbar-right .toolbar-field.grow input {
  width: 100%;
}
.toolbar-right .copy-btn {
  flex: 0 0 auto;
}
.foundry-root-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: end;
}
.foundry-root-toolbar .toolbar-field.grow {
  width: 100%;
  min-width: 0;
  grid-column: auto;
}
.foundry-root-actions {
  display: inline-flex;
  gap: 8px;
  justify-self: end;
}
.toolbar-field {
  display: grid;
  gap: 6px;
}
.toolbar-field span {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 11px;
  color: var(--muted);
}
.toolbar-field input,
.toolbar-field select {
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 10px;
  font: inherit;
  background: var(--surface-elevated);
  color: var(--ink);
}
.toolbar-field.grow {
  grid-column: span 2;
}
.pager {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.pager button {
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-elevated);
  padding: 0 10px;
  font: inherit;
  cursor: pointer;
  color: var(--ink);
}
.pager-status {
  color: var(--muted);
  font-size: 13px;
}
.pager-right {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pager-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pager-field span {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.pager-field select {
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0 8px;
  font: inherit;
  background: var(--surface-elevated);
  color: var(--ink);
}
.section-stack {
  display: grid;
  gap: 10px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}
.target-manual {
  margin-top: 10px;
}
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.metric {
  padding: 12px;
}
.metric.compact {
  border-radius: 12px;
  box-shadow: none;
}
.metric-label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.metric-value { font-size: 28px; font-weight: 700; margin-top: 6px; }
.tone-critical { border-color: var(--line-strong); background: var(--surface-muted); border-color: color-mix(in srgb, var(--critical) 45%, var(--line) 55%); background: color-mix(in srgb, var(--critical-soft) 45%, var(--surface) 55%); }
.metric.tone-critical .metric-value { color: var(--critical); }
.tone-update { border-color: var(--line-strong); background: var(--surface-muted); border-color: color-mix(in srgb, var(--update) 40%, var(--line) 60%); background: color-mix(in srgb, var(--update-soft) 45%, var(--surface) 55%); }
.metric.tone-update .metric-value { color: var(--update); }
.tone-manual { border-color: var(--line-strong); background: var(--surface-muted); border-color: color-mix(in srgb, var(--manual) 40%, var(--line) 60%); background: color-mix(in srgb, var(--manual-soft) 45%, var(--surface) 55%); }
.metric.tone-manual .metric-value { color: var(--manual); }
.tone-ok { border-color: var(--line-strong); background: var(--surface-muted); border-color: color-mix(in srgb, var(--ok) 40%, var(--line) 60%); background: color-mix(in srgb, var(--ok-soft) 45%, var(--surface) 55%); }
.metric.tone-ok .metric-value { color: var(--ok); }
.tone-neutral { border-color: var(--line); background: var(--neutral); }
.metric-btn {
  width: 100%;
  text-align: left;
  cursor: pointer;
  font: inherit;
  transition: border-color 0.15s ease, background-color 0.15s ease, transform 0.15s ease;
}
.metric-btn.is-active {
  outline: 2px solid var(--focus);
  outline-offset: 1px;
  transform: translateY(-1px) scale(1.01);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--focus) 30%, transparent 70%);
}
.metric-btn:hover {
  transform: translateY(-1px);
}
.metric-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
[data-planning-target-version] .metric-label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
[data-planning-target-version] .metric-value {
  font-size: 30px;
  font-weight: 800;
}
body.is-dark [data-planning-target-version] .metric-label {
  color: color-mix(in srgb, #ffffff 92%, var(--ink) 8%);
}
[data-planning-view] .metric-label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
[data-planning-view] .metric-value {
  font-size: 30px;
  font-weight: 800;
}
body.is-dark [data-planning-view] .metric-label {
  color: color-mix(in srgb, #ffffff 92%, var(--ink) 8%);
}
[data-filter-state] .metric-label,
[data-scroll-target] .metric-label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
[data-filter-state] .metric-value,
[data-scroll-target] .metric-value {
  font-size: 30px;
  font-weight: 800;
}
body.is-dark [data-filter-state] .metric-label,
body.is-dark [data-scroll-target] .metric-label {
  color: color-mix(in srgb, #ffffff 92%, var(--ink) 8%);
}
.bulk-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.forced-list {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}
.action-list {
  display: grid;
  gap: 10px;
}
.row-card {
  padding: 12px;
  background: var(--surface);
  border-left: 4px solid var(--line-strong);
}
.row-blocked {
  background: var(--surface);
  border-left-color: var(--critical);
}
.row-upgrade {
  background: var(--surface);
  border-left-color: var(--update);
}
.row-manual {
  background: var(--surface);
  border-left-color: var(--manual);
}
.row-ready {
  background: var(--surface);
  border-left-color: var(--ok);
}
.row-card header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-start;
}
.row-card h3 { margin: 0; font-size: 18px; }
.module-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.module-alert {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 2px solid color-mix(in srgb, var(--manual) 55%, var(--line) 45%);
  background: color-mix(in srgb, var(--manual-soft) 70%, var(--surface) 30%);
  color: color-mix(in srgb, var(--manual) 90%, #000 10%);
  font-size: 16px;
  font-weight: 900;
  line-height: 1;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.22);
  margin-right: 4px;
  flex-shrink: 0;
}
.row-card h3,
.panel h3,
.panel h4,
.list-row span,
.system-row strong {
  color: var(--ink);
}
.row-meta, .row-version, .row-reason, .empty {
  color: var(--muted);
  margin: 6px 0 0;
}
.row-compat {
  color: var(--muted);
  line-height: 1.45;
}
.compat-alert {
  color: var(--critical);
  font-weight: 700;
  margin: 6px 0 0;
}
.compat-ok {
  color: var(--ok);
  font-weight: 700;
  margin: 6px 0 0;
}
.compat-manual {
  color: var(--manual);
  font-weight: 700;
  margin: 6px 0 0;
}
.row-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.row-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0 12px;
  color: var(--update);
  text-decoration: none;
  background: var(--surface-elevated);
}
.copy-btn {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-elevated);
  padding: 0 12px;
  font: inherit;
  cursor: pointer;
  color: var(--ink);
}
.copy-btn:hover,
.row-link:hover,
.pager button:hover,
.mobile-toggle-btn:hover {
  border-color: var(--line-strong);
  background: var(--surface);
}
.copy-btn:disabled,
.pager button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.copy-btn:focus-visible,
.row-link:focus-visible,
.pager button:focus-visible,
.tab-btn:focus-visible,
.metric-btn:focus-visible,
.mobile-toggle-btn:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.copy-btn-danger {
  border-color: color-mix(in srgb, var(--critical) 50%, var(--line) 50%);
  background: color-mix(in srgb, var(--critical-soft) 55%, var(--surface-elevated) 45%);
  color: var(--critical);
  font-weight: 700;
}
.copy-btn-danger:hover {
  border-color: var(--critical);
  background: color-mix(in srgb, var(--critical-soft) 70%, var(--surface) 30%);
}
.state-badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.state-blocked { color: var(--critical); background: var(--critical-soft); }
.state-upgrade { color: var(--update); background: var(--update-soft); }
.state-manual { color: var(--manual); background: var(--manual-soft); }
.state-ready { color: var(--ok); background: var(--ok-soft); }
.target-panels { margin-top: 10px; }
.target-panel {
  display: none;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px;
  background: var(--surface);
}
.target-panel.is-active { display: block; }
.target-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}
.planner-top label { display: grid; gap: 6px; max-width: 320px; }
.planner-top span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
.planner-top select {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px;
  font: inherit;
  background: var(--surface-elevated);
  color: var(--ink);
}
.target-metrics {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  margin-bottom: 10px;
}
.target-columns {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.selection-banner {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px;
  background: var(--surface);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
}
.selection-banner .banner-title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}
.selection-banner .banner-value {
  margin-top: 4px;
  font-size: 15px;
  font-weight: 700;
}
.selection-banner.tone-ok {
  border-color: color-mix(in srgb, var(--ok) 45%, var(--line) 55%);
  background: color-mix(in srgb, var(--ok-soft) 28%, var(--surface) 72%);
}
.selection-banner.tone-update {
  border-color: color-mix(in srgb, var(--update) 45%, var(--line) 55%);
  background: color-mix(in srgb, var(--update-soft) 28%, var(--surface) 72%);
}
.selection-banner.tone-critical {
  border-color: color-mix(in srgb, var(--critical) 45%, var(--line) 55%);
  background: color-mix(in srgb, var(--critical-soft) 28%, var(--surface) 72%);
}
.planner-selection-fieldset {
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 10px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.planner-selection-fieldset.tone-ok {
  border-color: color-mix(in srgb, var(--ok) 45%, var(--line) 55%);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ok-soft) 55%, transparent 45%);
}
.planner-selection-fieldset.tone-update {
  border-color: color-mix(in srgb, var(--update) 45%, var(--line) 55%);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--update-soft) 55%, transparent 45%);
}
.planner-selection-fieldset.tone-critical {
  border-color: color-mix(in srgb, var(--critical) 45%, var(--line) 55%);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--critical-soft) 55%, transparent 45%);
}
.panel {
  padding: 10px;
}
.panel.subtle {
  box-shadow: none;
}
.panel.planner-unused {
  border-color: rgba(210, 153, 34, 0.5);
  background: var(--surface);
  background: color-mix(in srgb, var(--manual-soft) 36%, var(--surface) 64%);
}
.panel h4, .panel h3 { margin: 0 0 8px; }
.mini-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 8px;
}
.list-row {
  border: 1px solid var(--line);
  border-left: 4px solid var(--line-strong);
  border-radius: 10px;
  padding: 8px;
  display: grid;
  gap: 6px;
  background: var(--surface);
}
.list-row-blocked {
  border-left-color: var(--critical);
}
.list-row-upgrade {
  border-left-color: var(--update);
}
.list-row-manual {
  border-left-color: var(--manual);
}
.list-row-not-ready {
  border-left-color: var(--manual);
}
.list-row-ready {
  border-left-color: var(--ok);
}
.list-row-incompatible {
  border-left-color: var(--critical);
}
.list-row span { font-weight: 600; }
.list-row small { color: var(--muted); }
.list-row .row-compat {
  color: var(--muted);
}
.system-row {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px;
  background: var(--surface);
  display: grid;
  gap: 8px;
}
.system-row-top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}
.system-version {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line-strong);
  background: var(--surface-muted);
  border: 1px solid color-mix(in srgb, var(--update) 40%, var(--line) 60%);
  background: color-mix(in srgb, var(--update-soft) 40%, var(--surface) 60%);
  color: var(--update);
  border-radius: 999px;
  padding: 4px 10px;
  width: fit-content;
}
.mobile-toggle-btn {
  display: none;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-elevated);
  color: var(--ink);
  padding: 0 10px;
  font: inherit;
  cursor: pointer;
}
.system-worlds {
  color: var(--muted);
}
.coverage-pill {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 700;
}
.coverage-critical {
  color: var(--critical);
  background: var(--critical-soft);
}
.coverage-manual {
  color: var(--manual);
  background: var(--manual-soft);
}
.coverage-ok {
  color: var(--ok);
  background: var(--ok-soft);
}
body.is-dark input,
body.is-dark select,
body.is-dark button,
body.is-dark textarea,
body.is-dark option {
  color: var(--ink);
}
body.is-dark input::placeholder,
body.is-dark textarea::placeholder {
  color: var(--muted);
}
body.is-dark .metric-label,
body.is-dark .pager-status,
body.is-dark .section-copy,
body.is-dark .hero-copy,
body.is-dark .row-meta,
body.is-dark .row-version,
body.is-dark .row-reason,
body.is-dark .list-row small,
body.is-dark .system-worlds,
body.is-dark .row-compat {
  color: var(--muted);
}
@media (max-width: 980px) {
  .hero { flex-direction: column; }
  .hero-meta { justify-items: end; width: 100%; }
  .hero-actions { right: 12px; top: 12px; }
  .target-columns { grid-template-columns: 1fr; }
  .list-toolbar { grid-template-columns: 1fr 1fr; }
  .toolbar-field.grow { grid-column: span 2; }
  .foundry-root-toolbar { grid-template-columns: 1fr; }
  .foundry-root-toolbar .toolbar-field.grow { grid-column: auto; }
  .foundry-root-actions { justify-self: end; width: auto; }
  .bulk-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .pager-right { margin-left: 0; width: 100%; justify-content: flex-end; }
  .section-stack > .list-toolbar {
    position: sticky;
    top: 8px;
    z-index: 3;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 8px;
  }
  .section-stack > .pager:last-of-type {
    position: sticky;
    bottom: 8px;
    z-index: 3;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 8px;
  }
  .mobile-toggle-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: fit-content;
    margin-top: 8px;
  }
  .mobile-collapsible .mobile-detail {
    display: none;
  }
  .mobile-collapsible.is-expanded .mobile-detail {
    display: block;
  }
  .mobile-collapsible .row-actions.mobile-detail {
    display: none;
  }
  .mobile-collapsible.is-expanded .row-actions.mobile-detail {
    display: flex;
  }
  .copy-btn { width: 100%; }
  h1 { font-size: 28px; }
}
"""


_SCRIPT = """
const dataNode = document.getElementById("report-v3-data");
const DATA = dataNode ? JSON.parse(dataNode.textContent || "{}") : {};
const TAB_IDS = ["actions", "planning", "backups", "unused", "forced-compat"];
const tabSections = Array.from(document.querySelectorAll("[data-tab-section]"));
const tabButtons = Array.from(document.querySelectorAll("[data-tab-target]"));
const metricFilterButtons = Array.from(document.querySelectorAll(".metric-btn[data-filter-state]"));
const metricScrollButtons = Array.from(document.querySelectorAll(".metric-btn[data-scroll-target]"));
const themeToggle = document.getElementById("theme-toggle");
const roots = {
  actions: document.getElementById("actions-lazy-root"),
  planning: document.getElementById("planning-lazy-root"),
  backups: document.getElementById("backups-lazy-root"),
  unused: document.getElementById("unused-lazy-root"),
  forced: document.getElementById("forced-lazy-root"),
};

const ui = {
  activeTab: "actions",
  rendered: {
    actions: false,
    planning: false,
    backups: false,
    unused: false,
    "forced-compat": false,
  },
  pageTotals: {},
  actions: {
    state: "all",
    search: "",
    page: 1,
    pageSize: 10,
  },
  forced: {
    search: "",
    page: 1,
    pageSize: 10,
  },
  planning: {
    target: String(DATA.defaultTarget || ""),
    view: "",
    unusedFilter: "all",
    search: "",
    pageSize: 10,
    page: {
      systems: 1,
      blocked: 1,
      upgrade: 1,
      ready: 1,
      unusedIncompatible: 1,
      unusedCompatible: 1,
      unusedUpdates: 1,
    },
  },
  backups: {
    search: "",
    page: 1,
    pageSize: 10,
  },
  unused: {
    filter: "all",
    search: "",
    page: 1,
    pageSize: 10,
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalize(value) {
  return String(value ?? "").toLowerCase();
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0.0%";
  return `${numeric.toFixed(1)}%`;
}

function majorFromVersion(rawValue) {
  const value = String(rawValue || "").trim();
  if (!value) return null;
  const token = value.split(".", 1)[0].trim();
  if (!/^[0-9]+$/.test(token)) return null;
  return parseInt(token, 10);
}

function formatVersionDisplay(installed, recommended) {
  const current = String(installed || "-");
  const target = String(recommended || "-");
  if (!target || target === "-" || current === target) return current;
  return `${current} -> ${target}`;
}

function compatibilityStatusLabel(status) {
  if (status === "compatible") return "Compatible";
  if (status === "incompatible") return "Incompatible";
  return "Unknown Compatibility";
}

function formatCompatibilityRequirements(rawCompatibility) {
  const compatibility = rawCompatibility && typeof rawCompatibility === "object" ? rawCompatibility : {};
  const minimum = String(compatibility.minimum || "").trim();
  const verified = String(compatibility.verified || "").trim();
  const maximum = String(compatibility.maximum || "").trim();
  const parts = [];
  if (minimum) parts.push(`minimum ${minimum}`);
  if (verified) parts.push(`verified ${verified}`);
  if (maximum) parts.push(`maximum ${maximum}`);
  if (!parts.length) return "Foundry compatibility metadata unavailable.";
  return `Foundry compatibility: ${parts.join(", ")}.`;
}

function compatibilityStatusForTarget(row, targetVersion) {
  const status = String(row.status || row.state || "");
  if (status === "blocked") return "incompatible";
  if (status === "upgrade" || status === "ready") return "compatible";
  const targetMajor = majorFromVersion(targetVersion);
  const minMajor = majorFromVersion((row.compatibility || {}).minimum);
  const maxMajor = majorFromVersion((row.compatibility || {}).maximum);
  if (targetMajor !== null && minMajor !== null && targetMajor < minMajor) return "incompatible";
  if (targetMajor !== null && maxMajor !== null && targetMajor > maxMajor) return "incompatible";
  if (status === "not-ready") return "incompatible";
  return "unknown";
}

function paginate(rows, page, pageSize) {
  const safePageSize = Math.max(parseInt(pageSize, 10) || 1, 1);
  const total = rows.length;
  const totalPages = Math.max(Math.ceil(total / safePageSize), 1);
  const safePage = clamp(parseInt(page, 10) || 1, 1, totalPages);
  const start = (safePage - 1) * safePageSize;
  const end = start + safePageSize;
  return {
    rows: rows.slice(start, end),
    total,
    totalPages,
    page: safePage,
    pageSize: safePageSize,
  };
}

function copyButton(command, label, tooltip, variant = "default") {
  if (!command) return "";
  const safeCommand = escapeHtml(String(command)).replace(/\\n/g, "&#10;");
  const safeTitle = escapeHtml(tooltip || "Copies the generated command to your clipboard.");
  const normalizedVariant = String(variant || "default").trim();
  const variantClass = normalizedVariant && normalizedVariant !== "default" ? ` copy-btn-${escapeHtml(normalizedVariant)}` : "";
  return `<button class="copy-btn${variantClass}" type="button" data-copy-command="${safeCommand}" title="${safeTitle}">${escapeHtml(label)}</button>`;
}

function releaseButton(link) {
  if (!link) return "";
  return `<a class="row-link" href="${escapeHtml(link)}" target="_blank" rel="noreferrer" title="Opens the module release page in a new tab.">Open release</a>`;
}

function buildUpgradeBulkCommand(rows) {
  const modules = [];
  const expected = [];
  rows.forEach((row) => {
    const moduleId = String(row.module || "").trim();
    if (!moduleId) return;
    if (!modules.includes(moduleId)) modules.push(moduleId);
    const recommended = String(row.recommendedVersion || row.recommended || "").trim();
    if (recommended && recommended !== "-") expected.push(`--expected-version ${moduleId}=${recommended}`);
  });
  if (!modules.length) return "";
  const parts = [
    "cd /home/engrenado/config/foundryModuleVersioningTool &&",
    "python3 -m resolver.cli",
    "--data-root /home/engrenado/foundry/data",
    "--apply",
  ];
  modules.forEach((moduleId) => parts.push(`--module ${moduleId}`));
  expected.forEach((flag) => parts.push(flag));
  return parts.join(" ");
}

function buildUpgradeCommandForRow(row) {
  return buildUpgradeBulkCommand([row]);
}

function buildForceCompatibilityBulkCommand(rows, targetVersion) {
  const modules = [];
  rows.forEach((row) => {
    const moduleId = String(row.module || "").trim();
    if (!moduleId || modules.includes(moduleId)) return;
    modules.push(moduleId);
  });
  const target = String(targetVersion || "").trim();
  if (!modules.length || !target || target === "-") return "";
  const parts = [
    "cd /home/engrenado/config/foundryModuleVersioningTool &&",
    "python3 -m resolver.cli",
    "--data-root /home/engrenado/foundry/data",
    `--force-compat-version ${target}`,
  ];
  modules.forEach((moduleId) => parts.push(`--force-compat-module ${moduleId}`));
  return parts.join(" ");
}

function buildForceCompatibilityCommand(moduleId, targetVersion) {
  const cleanModule = String(moduleId || "").trim();
  const cleanTarget = String(targetVersion || "").trim();
  if (!cleanModule || !cleanTarget || cleanTarget === "-") return "";
  return [
    "cd /home/engrenado/config/foundryModuleVersioningTool &&",
    "python3 -m resolver.cli",
    "--data-root /home/engrenado/foundry/data",
    `--force-compat-version ${cleanTarget}`,
    `--force-compat-module ${cleanModule}`,
  ].join(" ");
}

function buildCleanupBackupsBulkCommand(rows) {
  const modules = [];
  rows.forEach((row) => {
    const moduleId = String(row.module || "").trim();
    if (!moduleId || modules.includes(moduleId)) return;
    modules.push(moduleId);
  });
  if (!modules.length) return "";
  const parts = [
    "cd /home/engrenado/config/foundryModuleVersioningTool &&",
    "python3 -m resolver.cli",
    "--data-root /home/engrenado/foundry/data",
    "--cleanup-backups",
  ];
  modules.forEach((moduleId) => parts.push(`--cleanup-backup-module ${moduleId}`));
  return parts.join(" ");
}

function buildDeleteUnusedBulkCommand(rows) {
  const modules = [];
  rows.forEach((row) => {
    const moduleId = String(row.module || "").trim();
    if (!moduleId || modules.includes(moduleId)) return;
    modules.push(moduleId);
  });
  if (!modules.length) return "";
  const parts = [
    "cd /home/engrenado/config/foundryModuleVersioningTool &&",
    "python3 -m resolver.cli",
    "--data-root /home/engrenado/foundry/data",
    "--delete-unused-modules",
  ];
  modules.forEach((moduleId) => parts.push(`--delete-module ${moduleId}`));
  return parts.join(" ");
}

function splitPlanningUnusedRows(rows, targetVersion) {
  const buckets = {
    incompatible: [],
    compatible: [],
    updates: [],
  };
  rows.forEach((row) => {
    const installed = String(row.installedVersion || "-");
    const recommended = String(row.recommendedVersion || "-");
    const updateAvailable = Boolean(recommended && recommended !== "-" && installed !== recommended);
    if (updateAvailable) {
      buckets.updates.push(row);
      return;
    }
    const compatibility = compatibilityStatusForTarget(row, targetVersion);
    if (compatibility === "compatible") {
      buckets.compatible.push(row);
      return;
    }
    buckets.incompatible.push(row);
  });
  return buckets;
}

function collectPlanningUnusedRows(unusedModuleIds, blockedRows, upgradableRows, notReadyRows, fallbackUnusedRows) {
  const priority = {
    blocked: 4,
    upgrade: 3,
    "not-ready": 2,
    ready: 1,
    manual: 1,
  };
  const byModule = new Map();

  function upsertRow(row, fallbackState) {
    const moduleId = String(row.module || "").trim();
    if (!moduleId || !unusedModuleIds.has(moduleId)) return;
    const status = String(row.status || row.state || fallbackState || "manual");
    const normalizedStatus = status === "blocked" ? "blocked" : (status === "upgrade" ? "upgrade" : (status === "not-ready" ? "not-ready" : "manual"));
    const existing = byModule.get(moduleId);
    const currentPriority = priority[normalizedStatus] || 0;
    const existingPriority = existing ? (priority[String(existing.status || existing.state || "manual")] || 0) : -1;
    if (currentPriority < existingPriority) return;
    byModule.set(moduleId, {
      module: moduleId,
      title: String(row.title || moduleId || "-"),
      installedVersion: String(row.installedVersion || "-"),
      recommendedVersion: String(row.recommendedVersion || "-"),
      reason: String(row.reason || ""),
      state: String(row.state || normalizedStatus),
      status: normalizedStatus,
      compatibility: row.compatibility || {},
      command: String(row.command || ""),
      link: String(row.link || ""),
    });
  }

  (Array.isArray(blockedRows) ? blockedRows : []).forEach((row) => upsertRow(row, "blocked"));
  (Array.isArray(upgradableRows) ? upgradableRows : []).forEach((row) => upsertRow(row, "upgrade"));
  (Array.isArray(notReadyRows) ? notReadyRows : []).forEach((row) => upsertRow(row, "not-ready"));

  (Array.isArray(fallbackUnusedRows) ? fallbackUnusedRows : []).forEach((row) => {
    const moduleId = String(row.module || "").trim();
    if (!moduleId || !unusedModuleIds.has(moduleId) || byModule.has(moduleId)) return;
    byModule.set(moduleId, {
      module: moduleId,
      title: String(row.title || moduleId || "-"),
      installedVersion: String(row.installedVersion || "-"),
      recommendedVersion: String(row.recommendedVersion || "-"),
      reason: String(row.reason || ""),
      state: "manual",
      status: "manual",
      compatibility: row.compatibility || {},
      command: "",
      link: String(row.link || ""),
    });
  });

  return Array.from(byModule.values());
}

function pager(scope, total, page, totalPages, options = {}) {
  if (total <= 0) {
    ui.pageTotals[scope] = 1;
    return `<div class="pager"><span class="pager-status">No rows found.</span></div>`;
  }
  ui.pageTotals[scope] = totalPages;
  const pageSizeScope = String(options.pageSizeScope || "");
  const pageSizeValue = parseInt(String(options.pageSize || "0"), 10) || 0;
  const pageSizeOptions = Array.isArray(options.pageSizeOptions) ? options.pageSizeOptions : [];
  const pageSizeLabel = String(options.pageSizeLabel || "Page Size");
  const pageSizeHtml = pageSizeScope && pageSizeOptions.length
    ? (
      `<div class="pager-right">` +
      `<label class="pager-field"><span>${escapeHtml(pageSizeLabel)}</span><select data-page-size-scope="${escapeHtml(pageSizeScope)}">` +
      pageSizeOptions.map((size) => `<option value="${size}"${pageSizeValue === size ? " selected" : ""}>${size}</option>`).join("") +
      `</select></label>` +
      `</div>`
    )
    : "";
  return (
    `<div class="pager">` +
    `<button type="button" data-page-scope="${escapeHtml(scope)}" data-page-delta="-1" ${page <= 1 ? "disabled" : ""} title="Moves to the previous page of results.">Prev</button>` +
    `<button type="button" data-page-scope="${escapeHtml(scope)}" data-page-delta="1" ${page >= totalPages ? "disabled" : ""} title="Moves to the next page of results.">Next</button>` +
    `<span class="pager-status">Page ${page} of ${totalPages} Â· ${total} items</span>` +
    pageSizeHtml +
    `</div>`
  );
}

function stateLabel(state) {
  if (state === "blocked") return "Blocked";
  if (state === "upgrade") return "Update";
  if (state === "not-ready") return "Unused Module";
  if (state === "manual") return "No Direct Action";
  return "Stable";
}

function hasAttentionFlag(row) {
  return Boolean(row && row.attentionFlag);
}

function renderModuleLabel(row) {
  const label = escapeHtml(String((row && (row.title || row.module)) || "-"));
  if (!hasAttentionFlag(row)) return label;
  const tooltip = escapeHtml(String(row.reason || "Attention required for this recommendation."));
  return `${label}<span class="module-alert" title="${tooltip}" aria-label="Attention warning">!</span>`;
}

function mobileToggleButton() {
  return `<button class="mobile-toggle-btn" type="button" data-toggle-details="collapsed" title="Shows more details for this row.">Details</button>`;
}

function renderActionCard(row) {
  const state = String(row.state || "ready");
  const linkHtml = releaseButton(row.link || "");
  const compatibilityLine = row.compatibilityLabel || formatCompatibilityRequirements(row.compatibility || {});
  const showActions = state !== "ready";
  const actionsHtml = showActions
    ? `<div class="row-actions mobile-detail">${linkHtml}${copyButton(row.command || "", "Copy command", "Copies this module action command for execution in the terminal.")}</div>`
    : "";
  return (
    `<article class="row-card action-item row-${escapeHtml(state)} mobile-collapsible">` +
    `<header><div><h3 class="module-title">${renderModuleLabel(row)}</h3><p class="row-meta">${escapeHtml(row.module || "-")} Â· ${escapeHtml(row.systemsLabel || "-")}</p></div></header>` +
    `${mobileToggleButton()}` +
    `<p class="row-version mobile-detail">${escapeHtml(formatVersionDisplay(row.installed || "-", row.recommended || "-"))}</p>` +
    `<p class="row-reason mobile-detail">${escapeHtml(compatibilityLine)}</p>` +
    `<p class="row-reason mobile-detail">${escapeHtml(row.reason || "No additional detail.")}</p>` +
    actionsHtml +
    `</article>`
  );
}

function renderListRow(row, state, subtitle, buttonLabel, commandOverride = null, detailLine = "", buttonVariant = "default") {
  const linkHtml = releaseButton(row.link || "");
  const command = commandOverride === null ? (row.command || "") : commandOverride;
  return (
    `<li class="list-row list-row-${escapeHtml(state)} mobile-collapsible">` +
    `<span class="module-title">${renderModuleLabel(row)}</span>` +
    `${mobileToggleButton()}` +
    `<small class="mobile-detail">${escapeHtml(subtitle)}</small>` +
    `${detailLine ? `<small class="mobile-detail row-compat">${escapeHtml(detailLine)}</small>` : ""}` +
    `<div class="row-actions mobile-detail">${linkHtml}${copyButton(command, buttonLabel, "Copies this row command to your clipboard.", buttonVariant)}</div>` +
    `</li>`
  );
}

function renderUnusedCompatibilityCard(row) {
  const systemsSuffix = row.systemsLabel ? ` Â· systems: ${row.systemsLabel}` : "";
  const status = String(row.compatibilityStatus || "unknown");
  const statusLabel = compatibilityStatusLabel(status);
  const compatibilityLine = `${statusLabel} with Foundry ${row.foundryVersion || DATA.foundryVersion || "-"}${systemsSuffix}`;
  const technicalLine = row.compatibilityLabel || "Foundry compatibility metadata unavailable.";
  const forceCommand = buildForceCompatibilityCommand(row.module || "", DATA.foundryVersion || "");
  const reasonLine = row.reason || "";
  const rowTone = status === "compatible" ? "ready" : (status === "incompatible" ? "blocked" : "manual");
  const badgeLabel = status === "compatible" ? "Compatible" : (status === "incompatible" ? "Incompatible" : "Unknown");
  const compatClass = status === "compatible" ? "compat-ok" : (status === "incompatible" ? "compat-alert" : "compat-manual");
  const reasonClass = status === "incompatible" ? "compat-alert" : "row-reason";
  return (
    `<article class="row-card row-${escapeHtml(rowTone)} mobile-collapsible">` +
    `<header><div><h3>${escapeHtml(row.title || row.module || "-")}</h3><p class="row-meta">${escapeHtml(row.module || "-")}</p></div><span class="state-badge state-${escapeHtml(rowTone)}">${escapeHtml(badgeLabel)}</span></header>` +
    `${mobileToggleButton()}` +
    `<p class="row-version mobile-detail">${escapeHtml(formatVersionDisplay(row.installedVersion || "-", row.recommendedVersion || "-"))}</p>` +
    `<p class="${escapeHtml(compatClass)} mobile-detail">${escapeHtml(compatibilityLine)}</p>` +
    `<p class="row-reason mobile-detail">${escapeHtml(technicalLine)}</p>` +
    `${reasonLine ? `<p class="${escapeHtml(reasonClass)} mobile-detail">${escapeHtml(reasonLine)}</p>` : ""}` +
    `<div class="row-actions mobile-detail">` +
    `${releaseButton(row.link || "")}` +
    `${copyButton(forceCommand, "Force Compatibility", "Copies a command that adds forced compatibility for this module at the current Foundry version.", "danger")}` +
    `${copyButton(row.command || "", "Copy delete", "Copies a command to delete this unused module.")}` +
    `</div>` +
    `</article>`
  );
}

function filterBySearch(rows, query, pickText) {
  const q = normalize(query).trim();
  if (!q) return rows;
  return rows.filter((row) => normalize(pickText(row)).includes(q));
}

function snapshotFocusableControl(target) {
  if (!(target instanceof HTMLElement)) return null;
  const selectors = [
    "[data-actions-search]",
    "[data-forced-search]",
    "[data-planning-search]",
    "[data-backups-search]",
    "[data-unused-search]",
  ];
  const selector = selectors.find((current) => target.matches(current));
  if (!selector) return null;
  const isTextInput = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  return {
    selector,
    selectionStart: isTextInput ? target.selectionStart : null,
    selectionEnd: isTextInput ? target.selectionEnd : null,
  };
}

function restoreFocusableControl(snapshot) {
  if (!snapshot || !snapshot.selector) return;
  const node = document.querySelector(snapshot.selector);
  if (!(node instanceof HTMLElement)) return;
  node.focus({ preventScroll: true });
  if ((node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) && snapshot.selectionStart !== null && snapshot.selectionEnd !== null) {
    const valueLength = String(node.value || "").length;
    const start = Math.max(0, Math.min(Number(snapshot.selectionStart), valueLength));
    const end = Math.max(start, Math.min(Number(snapshot.selectionEnd), valueLength));
    try {
      node.setSelectionRange(start, end);
    } catch {
      // Keep focus even if the control type does not support setSelectionRange.
    }
  }
}

function rerenderWithFocusPreserved(target, renderFn) {
  const snapshot = snapshotFocusableControl(target);
  renderFn();
  restoreFocusableControl(snapshot);
}

function renderActionsTab() {
  const root = roots.actions;
  if (!root) return;
  const allRows = Array.isArray(DATA.actions) ? DATA.actions : [];
  let filtered = allRows;
  if (ui.actions.state !== "all") {
    filtered = filtered.filter((row) => String(row.state || "") === ui.actions.state);
  }
  filtered = filterBySearch(
    filtered,
    ui.actions.search,
    (row) => `${row.title || ""} ${row.module || ""} ${row.systemsLabel || ""} ${row.reason || ""} ${row.compatibilityLabel || ""}`
  );
  const pageInfo = paginate(filtered, ui.actions.page, ui.actions.pageSize);
  ui.actions.page = pageInfo.page;

  const updateModules = filtered.map((row) => String(row.module || "")).filter(Boolean);
  const bulkButtons =
    `<button class="copy-btn" type="button" id="update-all-btn" ${updateModules.length ? "" : "disabled"} data-update-modules='${escapeHtml(JSON.stringify(updateModules))}' title="Updates every module currently visible in Current table.">Update All</button>`;

  root.innerHTML =
    `<div class="section-stack">` +
    `<div class="bulk-actions">${bulkButtons}</div>` +
    `<div class="list-toolbar">` +
    `<label class="toolbar-field grow"><span>Search modules</span><input type="search" value="${escapeHtml(ui.actions.search)}" placeholder="name, id, system..." data-actions-search title="Filters Current modules by title, id, system, or reason."></label>` +
    `</div>` +
    pager("actions", pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "actions", pageSize: ui.actions.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `<div class="action-list">${pageInfo.rows.length ? pageInfo.rows.map(renderActionCard).join("") : '<p class="empty">No modules match the current filters.</p>'}</div>` +
    pager("actions", pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</div>`;
}

function renderForcedTab() {
  const root = roots.forced;
  if (!root) return;
  const allRows = Array.isArray(DATA.forcedRows) ? DATA.forcedRows : [];
  const filtered = filterBySearch(
    allRows,
    ui.forced.search,
    (row) => `${row.title || ""} ${row.module || ""} ${row.systemsLabel || ""} ${row.reason || ""} ${row.compatibilityLabel || ""}`
  );
  const pageInfo = paginate(filtered, ui.forced.page, ui.forced.pageSize);
  ui.forced.page = pageInfo.page;

  const forceBulk = DATA.forceBulkCommand || "";
  const targetVersion = DATA.forceTargetVersion || DATA.foundryVersion || "-";

  root.innerHTML =
    `<div class="section-stack">` +
    `${forceBulk ? `<div class="bulk-actions">${copyButton(forceBulk, "Copy All", "Copies one command to re-apply forced compatibility to every module in this table.")}</div>` : ""}` +
    `<div class="list-toolbar">` +
    `<label class="toolbar-field grow"><span>Search forced modules</span><input type="search" value="${escapeHtml(ui.forced.search)}" placeholder="name, id, target version..." data-forced-search title="Filters forced compatibility modules by title, id, target version, or reason."></label>` +
    `</div>` +
    pager("forced", pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "forced", pageSize: ui.forced.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `<div class="forced-list">${
      pageInfo.rows.length
        ? pageInfo.rows
            .map((row) =>
              `<article class="row-card row-manual mobile-collapsible">` +
              `<header><div><h3>${escapeHtml(row.title || row.module || "-")}</h3><p class="row-meta">${escapeHtml(row.module || "-")} Â· ${escapeHtml(row.systemsLabel || "-")}</p></div><span class="state-badge state-manual">Forced ${escapeHtml(row.targetVersion || "-")}</span></header>` +
              `${mobileToggleButton()}` +
              `<p class="row-version mobile-detail">${escapeHtml(`version ${row.version || "-"} Â· compat ${row.verified || "-"} -> ${row.maximum || "-"}`)}</p>` +
              `<p class="row-reason mobile-detail">${escapeHtml(row.compatibilityLabel || formatCompatibilityRequirements({ minimum: row.minimum, verified: row.verified, maximum: row.maximum }))}</p>` +
              `<p class="row-reason mobile-detail">${escapeHtml(row.reason || "-")}</p>` +
              `<div class="row-actions mobile-detail">${copyButton(row.command || "", "Force Compatibility", "Copies the command that forces compatibility for this module.", "danger")}</div>` +
              `</article>`
            )
            .join("")
        : '<p class="empty">No forced compatibility modules detected.</p>'
    }</div>` +
    pager("forced", pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</div>`;
}

function renderPlannerModulePanel(title, rows, key) {
  const searchValue = ui.planning.search || "";
  const filtered = filterBySearch(
    rows,
    searchValue,
    (row) => `${row.title || ""} ${row.module || ""} ${row.reason || ""}`
  );
  const pageInfo = paginate(filtered, ui.planning.page[key], ui.planning.pageSize);
  ui.planning.page[key] = pageInfo.page;
  let tableCommand = "";
  let tableCommandLabel = "Copy All";
  let tableCommandTip = "Copies one command for all modules currently visible in this table.";
  let tableCommandVariant = "default";
  if (key === "blocked") {
    tableCommand = buildForceCompatibilityBulkCommand(filtered, ui.planning.target);
    tableCommandLabel = "Force Compatibility";
    tableCommandTip = "Copies one command that forces compatibility for all modules currently visible in this table.";
    tableCommandVariant = "danger";
  } else if (key === "upgrade") {
    tableCommand = buildUpgradeBulkCommand(filtered);
    tableCommandTip = "Copies one command that applies update actions for all modules currently visible in this table.";
  }

  const listHtml = pageInfo.rows.length
    ? pageInfo.rows
        .map((row) => {
          const subtitle = key === "blocked"
            ? `${String(row.installedVersion || "-")} Â· No new compatible versions yet.`
            : `${formatVersionDisplay(row.installedVersion || "-", row.recommendedVersion || "-")} Â· target compat: ${compatibilityStatusLabel(compatibilityStatusForTarget(row, ui.planning.target))}`;
          const status = String(row.status || row.state || key);
          const buttonLabel = key === "blocked" ? "Force Compatibility" : "Copy command";
          const buttonVariant = key === "blocked" ? "danger" : "default";
          const compatibilityLine = formatCompatibilityRequirements(row.compatibility || {});
          const detailLine = key === "blocked"
            ? compatibilityLine
            : (row.reason ? `${compatibilityLine} ${row.reason}` : compatibilityLine);
          return renderListRow(row, status, subtitle, buttonLabel, null, detailLine, buttonVariant);
        })
        .join("")
    : `<p class="empty">None</p>`;

  return (
    `<article class="panel subtle">` +
    `<h4>${escapeHtml(title)}</h4>` +
    `<div class="bulk-actions">${copyButton(tableCommand, tableCommandLabel, tableCommandTip, tableCommandVariant) || '<span class="pager-status">No table action command for this filter.</span>'}</div>` +
    pager(`planning-${key}`, pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "planning", pageSize: ui.planning.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `${pageInfo.rows.length ? `<ul class="mini-list">${listHtml}</ul>` : listHtml}` +
    pager(`planning-${key}`, pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</article>`
  );
}

function renderPlannerUnusedStatusPanel(title, rows, pageKey, scope, mode) {
  const searchValue = ui.planning.search || "";
  const filtered = filterBySearch(
    rows,
    searchValue,
    (row) => `${row.title || ""} ${row.module || ""} ${row.reason || ""}`
  );
  const pageInfo = paginate(filtered, ui.planning.page[pageKey], ui.planning.pageSize);
  ui.planning.page[pageKey] = pageInfo.page;

  const tone = mode === "incompatible" ? "incompatible" : (mode === "updates" ? "upgrade" : "ready");
  const tableCommand = mode === "incompatible"
    ? buildForceCompatibilityBulkCommand(filtered, ui.planning.target)
    : (mode === "updates" ? buildUpgradeBulkCommand(filtered) : "");
  const tableCommandLabel = mode === "incompatible" ? "Force Compatibility" : "Copy All";
  const tableCommandVariant = mode === "incompatible" ? "danger" : "default";
  const tableCommandTip = mode === "incompatible"
    ? "Copies one command that forces compatibility for all modules currently visible in this table."
    : "Copies one command that applies update actions for all modules currently visible in this table.";

  const listHtml = pageInfo.rows.length
    ? pageInfo.rows
        .map((row) => {
          const subtitle = mode === "incompatible"
            ? `${String(row.installedVersion || "-")} Â· No new compatible versions yet.`
            : `${formatVersionDisplay(row.installedVersion || "-", row.recommendedVersion || "-")} Â· target compat: ${compatibilityStatusLabel(compatibilityStatusForTarget(row, ui.planning.target))}`;
          const rowCommand = mode === "incompatible"
            ? buildForceCompatibilityCommand(row.module || "", ui.planning.target)
            : (mode === "updates" ? buildUpgradeCommandForRow(row) : "");
          const buttonLabel = mode === "incompatible" ? "Force Compatibility" : "Copy command";
          const buttonVariant = mode === "incompatible" ? "danger" : "default";
          const compatibilityLine = formatCompatibilityRequirements(row.compatibility || {});
          const detailLine = mode === "incompatible"
            ? compatibilityLine
            : (row.reason ? `${compatibilityLine} ${row.reason}` : compatibilityLine);
          return renderListRow(row, tone, subtitle, buttonLabel, rowCommand, detailLine, buttonVariant);
        })
        .join("")
    : `<p class="empty">None</p>`;

  return (
    `<article class="panel subtle planner-unused">` +
    `<h4>${escapeHtml(title)}</h4>` +
    `<div class="bulk-actions">${copyButton(tableCommand, tableCommandLabel, tableCommandTip, tableCommandVariant) || '<span class="pager-status">No table action command for this filter.</span>'}</div>` +
    pager(scope, pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "planning", pageSize: ui.planning.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `${pageInfo.rows.length ? `<ul class="mini-list">${listHtml}</ul>` : listHtml}` +
    pager(scope, pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</article>`
  );
}

function renderPlannerSystemsPanel(rows) {
  const searchValue = ui.planning.search || "";
  const filtered = filterBySearch(
    rows,
    searchValue,
    (row) => `${row.title || ""} ${row.worldsLabel || ""} ${row.installedVersion || ""} ${row.targetVersion || ""}`
  );
  const pageInfo = paginate(filtered, ui.planning.page.systems, ui.planning.pageSize);
  ui.planning.page.systems = pageInfo.page;

  const items = pageInfo.rows.map((row) => {
    const coverageValue = Number(row.coveragePercent || 0);
    const coverageTone = coverageValue >= 80 ? "ok" : coverageValue >= 60 ? "manual" : "critical";
    const coverageLabel = `${coverageValue.toFixed(1)}% update-ready`;
    return (
      `<li class="system-row">` +
      `<div class="system-row-top"><strong>${escapeHtml(row.title || "-")}</strong><span class="coverage-pill coverage-${coverageTone}">${escapeHtml(coverageLabel)}</span></div>` +
      `<div class="system-version">${escapeHtml(formatVersionDisplay(row.installedVersion || "-", row.targetVersion || "-"))}</div>` +
      `<div class="system-worlds">worlds: ${escapeHtml(row.worldsLabel || "-")}</div>` +
      `</li>`
    );
  }).join("");

  return (
    `<article class="panel subtle">` +
    `<h4>Systems that can move</h4>` +
    pager("planning-systems", pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "planning", pageSize: ui.planning.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `${pageInfo.rows.length ? `<ul class="mini-list">${items}</ul>` : '<p class="empty">No system upgrade rows.</p>'}` +
    pager("planning-systems", pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</article>`
  );
}

function renderPlanningTab() {
  const root = roots.planning;
  if (!root) return;
  const targets = Array.isArray(DATA.targets) ? DATA.targets : [];
  if (!targets.length) {
    root.innerHTML = '<p class="empty">No future stable versions were detected.</p>';
    return;
  }
  let active = targets.find((row) => row.foundryVersion === ui.planning.target);
  if (!active) {
    active = targets[0];
    ui.planning.target = String(active.foundryVersion || "");
  }

  const quick = active.quickStatus || {};
  const systemsRows = Array.isArray(active.systems) ? active.systems : [];
  const blockedRows = Array.isArray(active.blockedModules) ? active.blockedModules : [];
  const upgradableRows = Array.isArray(active.upgradableModules) ? active.upgradableModules : [];
  const readyRows = Array.isArray(active.readyModules) ? active.readyModules : [];
  const notReadyRows = Array.isArray(active.notReadyModules) ? active.notReadyModules : [];
  const unusedModuleIds = new Set((Array.isArray(DATA.unusedModules) ? DATA.unusedModules : []).map((row) => String(row.module || "")));
  const planningUnusedRows = collectPlanningUnusedRows(
    unusedModuleIds,
    blockedRows,
    upgradableRows,
    notReadyRows,
    Array.isArray(DATA.unusedModules) ? DATA.unusedModules : []
  );
  const unusedBreakdown = splitPlanningUnusedRows(planningUnusedRows, ui.planning.target);
  const unusedCount = planningUnusedRows.length;
  const systemsReadyCount = Number(quick.systemsReady || 0);
  const systemsTotalCount = Number(quick.systemsTotal || systemsRows.length || 0);
  const modulesReadyCount = Number(quick.modulesReady || 0);
  const modulesObservedCount = Number(
    quick.modulesObserved
    || (modulesReadyCount + Number(quick.modulesNeedUpdate || 0) + Number(quick.modulesBlocked || 0) + Number(quick.modulesNeedsVerification || 0))
  );
  const systemsReadyLabel = `${systemsReadyCount} / ${systemsTotalCount}`;
  const modulesReadyLabel = `${modulesReadyCount} / ${modulesObservedCount}`;
  const systemsModulesLabel = `${systemsRows.length} + ${modulesObservedCount}`;
  const consideredForReadiness = Math.max(
    modulesReadyCount + Number(quick.modulesNeedUpdate || 0) + Number(quick.modulesBlocked || 0),
    0
  );
  const readinessPercent = consideredForReadiness > 0
    ? ((modulesReadyCount + Number(quick.modulesNeedUpdate || 0)) / consideredForReadiness) * 100
    : 0;
  const selectedToneClass = readinessPercent >= 80 ? "tone-ok" : (readinessPercent >= 50 ? "tone-update" : "tone-critical");
  const selectedBanner = `<div class="selection-banner ${selectedToneClass}"><div class="banner-title">Selected target</div><div class="banner-value">Foundry v${escapeHtml(active.foundryVersion || "-")} Â· ${escapeHtml(formatPercent(readinessPercent))} upgradable</div></div>`;
  const view = String(ui.planning.view || "");
  const unusedFilter = String(ui.planning.unusedFilter || "all");
  const isUnusedFocused = view === "unused";
  const isModulesFocused = view === "modules";
  const showDefaultPlanning = !view || view === "all";
  const showSystems = showDefaultPlanning || view === "systems";
  const showBlocked = showDefaultPlanning || view === "blocked" || isModulesFocused;
  const showUpgrade = showDefaultPlanning || view === "upgrade" || isModulesFocused;
  const showReady = isModulesFocused;
  const showUnused = view === "all" || isUnusedFocused;
  const showUnusedIncompatible = showUnused && (unusedFilter === "all" || unusedFilter === "incompatible");
  const showUnusedCompatible = showUnused && (unusedFilter === "all" || unusedFilter === "compatible");
  const showUnusedUpdates = showUnused && (unusedFilter === "all" || unusedFilter === "updates");
  const targetPills = targets
    .map((row) => {
      const quickRow = row.quickStatus || {};
      const ready = Number(quickRow.modulesReady || 0);
      const update = Number(quickRow.modulesNeedUpdate || 0);
      const blocked = Number(quickRow.modulesBlocked || 0);
      const considered = Math.max(ready + update + blocked, 0);
      const readiness = considered > 0 ? ((ready + update) / considered) * 100 : 0;
      const toneClass = readiness >= 80 ? "tone-ok" : (readiness >= 50 ? "tone-update" : "tone-critical");
      return (
        `<button class="metric compact metric-btn ${toneClass}${row.foundryVersion === ui.planning.target ? " is-active" : ""}" type="button" data-planning-target-version="${escapeHtml(row.foundryVersion || "")}" title="Selects this stable Foundry target for planning.">` +
        `<div class="metric-label">v${escapeHtml(row.foundryVersion || "-")}</div>` +
        `<div class="metric-value">${escapeHtml(formatPercent(readiness))}</div>` +
        `</button>`
      );
    })
    .join("");

  const primaryPanels = [];
  if (showSystems) {
    primaryPanels.push(renderPlannerSystemsPanel(systemsRows));
  }
  if (showBlocked) {
    primaryPanels.push(renderPlannerModulePanel("Blockers", blockedRows, "blocked"));
  }
  if (showUpgrade) {
    primaryPanels.push(renderPlannerModulePanel("Update", upgradableRows, "upgrade"));
  }
  if (showReady) {
    primaryPanels.push(renderPlannerModulePanel("Stable Modules", readyRows, "ready"));
  }
  if (showUnusedIncompatible) {
    primaryPanels.push(
      renderPlannerUnusedStatusPanel(
        "Unused Modules Â· Incompatible",
        unusedBreakdown.incompatible,
        "unusedIncompatible",
        "planning-unused-incompatible",
        "incompatible"
      )
    );
  }
  if (showUnusedCompatible) {
    primaryPanels.push(
      renderPlannerUnusedStatusPanel(
        "Unused Modules Â· Compatible",
        unusedBreakdown.compatible,
        "unusedCompatible",
        "planning-unused-compatible",
        "compatible"
      )
    );
  }
  if (showUnusedUpdates) {
    primaryPanels.push(
      renderPlannerUnusedStatusPanel(
        "Unused Modules Â· Update",
        unusedBreakdown.updates,
        "unusedUpdates",
        "planning-unused-updates",
        "updates"
      )
    );
  }

  root.innerHTML =
    `<div class="section-stack">` +
    `<div class="list-toolbar">` +
    `<div class="toolbar-field grow"><span>Stable Foundry Versions (% Upgradable)</span><div class="target-metrics">${targetPills}</div></div>` +
    `<label class="toolbar-field grow"><span>Search</span><input type="search" value="${escapeHtml(ui.planning.search)}" placeholder="system, module, reason..." data-planning-search title="Filters planning rows by system/module title, id, or reason."></label>` +
    `</div>` +
    `${selectedBanner}` +
    `<fieldset class="planner-selection-fieldset ${selectedToneClass}">` +
    `<div class="target-metrics">` +
    `<button class="metric compact metric-btn tone-ok${view === "systems" ? " is-active" : ""}" type="button" data-planning-view="systems" title="Shows systems that are ready to move to the selected target version."><div class="metric-label">Systems Ready</div><div class="metric-value">${escapeHtml(String(systemsReadyLabel))}</div></button>` +
    `<button class="metric compact metric-btn tone-critical${view === "blocked" ? " is-active" : ""}" type="button" data-planning-view="blocked" title="Shows blockers for this target version."><div class="metric-label">Blockers</div><div class="metric-value">${escapeHtml(String(quick.modulesBlocked || 0))}</div></button>` +
    `<button class="metric compact metric-btn tone-update${view === "upgrade" ? " is-active" : ""}" type="button" data-planning-view="upgrade" title="Shows modules that can be updated automatically for this target version."><div class="metric-label">Requires Update</div><div class="metric-value">${escapeHtml(String(quick.modulesNeedUpdate || 0))}</div></button>` +
    `<button class="metric compact metric-btn tone-ok${view === "modules" ? " is-active" : ""}" type="button" data-planning-view="modules" title="Shows module planning lists: blockers, requires update, and stable modules."><div class="metric-label">Stable Modules</div><div class="metric-value">${escapeHtml(String(modulesReadyLabel))}</div></button>` +
    `<button class="metric compact metric-btn tone-manual${view === "unused" ? " is-active" : ""}" type="button" data-planning-view="unused" title="Shows unused modules for this target, split by incompatible, compatible, and update."><div class="metric-label">Unused Modules</div><div class="metric-value">${escapeHtml(String(unusedCount))}</div></button>` +
    `</div>` +
    `${isUnusedFocused ? (
      `<div class="target-metrics">` +
      `<button class="metric compact metric-btn tone-neutral${unusedFilter === "all" ? " is-active" : ""}" type="button" data-planning-unused-filter="all" title="Shows all unused module tables for this target."><div class="metric-label">All</div><div class="metric-value">${escapeHtml(String(unusedCount))}</div></button>` +
      `<button class="metric compact metric-btn tone-critical${unusedFilter === "incompatible" ? " is-active" : ""}" type="button" data-planning-unused-filter="incompatible" title="Shows only incompatible unused modules for this target."><div class="metric-label">Incompatible</div><div class="metric-value">${escapeHtml(String(unusedBreakdown.incompatible.length))}</div></button>` +
      `<button class="metric compact metric-btn tone-ok${unusedFilter === "compatible" ? " is-active" : ""}" type="button" data-planning-unused-filter="compatible" title="Shows only compatible unused modules for this target."><div class="metric-label">Compatible</div><div class="metric-value">${escapeHtml(String(unusedBreakdown.compatible.length))}</div></button>` +
      `<button class="metric compact metric-btn tone-update${unusedFilter === "updates" ? " is-active" : ""}" type="button" data-planning-unused-filter="updates" title="Shows only unused modules with an update path for this target."><div class="metric-label">Update</div><div class="metric-value">${escapeHtml(String(unusedBreakdown.updates.length))}</div></button>` +
      `</div>`
    ) : ""}` +
    `${primaryPanels.length ? `<div class="target-columns">${primaryPanels.join("")}</div>` : ""}` +
    `</fieldset>` +
    `</div>`;
}

function renderBackupsTab() {
  const root = roots.backups;
  if (!root) return;
  const backupsAll = Array.isArray(DATA.backups) ? DATA.backups : [];
  const backupsFiltered = filterBySearch(
    backupsAll,
    ui.backups.search,
    (row) => `${row.title || ""} ${row.module || ""}`
  );
  const pageInfo = paginate(backupsFiltered, ui.backups.page, ui.backups.pageSize);
  ui.backups.page = pageInfo.page;
  const tableCommand = buildCleanupBackupsBulkCommand(backupsFiltered);
  const listHtml = pageInfo.rows.length
    ? `<ul class="mini-list">${pageInfo.rows.map((row) => (
        `<li class="list-row list-row-ready mobile-collapsible">` +
        `<span>${escapeHtml(row.title || row.module || "-")}</span>` +
        `${mobileToggleButton()}` +
        `<small class="mobile-detail">${escapeHtml(`${row.backupCount || 0} backups Â· ${row.backupSizeLabel || "-"} Â· newest ${row.newestBackupLabel || "-"}`)}</small>` +
        `<div class="row-actions mobile-detail">${copyButton(row.command || "", "Copy Delete", "Copies the backup delete command for this module.")}</div>` +
        `</li>`
      )).join("")}</ul>`
    : '<p class="empty">No backups found for this filter.</p>';

  root.innerHTML =
    `<div class="section-stack">` +
    `<article class="panel">` +
    `<h3>Backups By Size</h3>` +
    `<div class="bulk-actions">${copyButton(tableCommand, "Copy All", "Copies one command that deletes backups for all modules currently visible in this table.") || '<span class="pager-status">No backup delete command for this filter.</span>'}</div>` +
    `<div class="list-toolbar">` +
    `<label class="toolbar-field grow"><span>Search backups</span><input type="search" value="${escapeHtml(ui.backups.search)}" placeholder="module name or id..." data-backups-search title="Filters backup rows by module title or id."></label>` +
    `</div>` +
    pager("backups", pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "backups", pageSize: ui.backups.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `${listHtml}` +
    pager("backups", pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</article>` +
    `</div>`;
}

function renderUnusedTab() {
  const root = roots.unused;
  if (!root) return;
  const unusedAll = Array.isArray(DATA.unusedModules) ? DATA.unusedModules : [];
  const filter = String(ui.unused.filter || "all");
  const compatibleCount = unusedAll.filter((row) => String(row.compatibilityStatus || "") === "compatible").length;
  const incompatibleCount = unusedAll.filter((row) => String(row.compatibilityStatus || "") === "incompatible").length;
  const reviewCount = unusedAll.filter((row) => String(row.compatibilityStatus || "") === "unknown").length;
  const updateCount = unusedAll.filter((row) => Boolean(row.updateViable)).length;
  let unusedFiltered = filterBySearch(
    unusedAll,
    ui.unused.search,
    (row) => `${row.title || ""} ${row.module || ""} ${row.reason || ""}`
  );
  if (filter === "compatible") {
    unusedFiltered = unusedFiltered.filter((row) => String(row.compatibilityStatus || "") === "compatible");
  } else if (filter === "incompatible") {
    unusedFiltered = unusedFiltered.filter((row) => String(row.compatibilityStatus || "") === "incompatible");
  } else if (filter === "review") {
    unusedFiltered = unusedFiltered.filter((row) => String(row.compatibilityStatus || "") === "unknown");
  } else if (filter === "updates") {
    unusedFiltered = unusedFiltered.filter((row) => Boolean(row.updateViable));
  }
  const pageInfo = paginate(unusedFiltered, ui.unused.page, ui.unused.pageSize);
  ui.unused.page = pageInfo.page;
  const deleteCommand = buildDeleteUnusedBulkCommand(unusedFiltered);
  const forceRows = unusedFiltered.filter((row) => {
    const status = String(row.compatibilityStatus || "");
    if (status !== "compatible") return true;
    const installed = String(row.installedVersion || "-");
    const recommended = String(row.recommendedVersion || "-");
    return Boolean(recommended && recommended !== "-" && installed !== recommended);
  });
  const forceCommand = buildForceCompatibilityBulkCommand(forceRows, DATA.foundryVersion || "");
  const listHtml = pageInfo.rows.length
    ? `<div class="action-list">${pageInfo.rows.map(renderUnusedCompatibilityCard).join("")}</div>`
    : '<p class="empty">No unused modules for this filter.</p>';

  root.innerHTML =
    `<div class="section-stack">` +
    `<div class="target-metrics">` +
    `<button class="metric compact metric-btn tone-neutral${filter === "all" ? " is-active" : ""}" type="button" data-unused-filter="all" title="Shows all unused modules."><div class="metric-label">Overview</div><div class="metric-value">${escapeHtml(String(unusedAll.length))}</div></button>` +
    `<button class="metric compact metric-btn tone-ok${filter === "compatible" ? " is-active" : ""}" type="button" data-unused-filter="compatible" title="Shows unused modules compatible with the current Foundry version."><div class="metric-label">Compatible</div><div class="metric-value">${escapeHtml(String(compatibleCount))}</div></button>` +
    `<button class="metric compact metric-btn tone-critical${filter === "incompatible" ? " is-active" : ""}" type="button" data-unused-filter="incompatible" title="Shows unused modules incompatible with the current Foundry version."><div class="metric-label">Incompatible</div><div class="metric-value">${escapeHtml(String(incompatibleCount))}</div></button>` +
    `<button class="metric compact metric-btn tone-manual${filter === "review" ? " is-active" : ""}" type="button" data-unused-filter="review" title="Shows unused modules with missing compatibility metadata."><div class="metric-label">Unknown</div><div class="metric-value">${escapeHtml(String(reviewCount))}</div></button>` +
    `<button class="metric compact metric-btn tone-update${filter === "updates" ? " is-active" : ""}" type="button" data-unused-filter="updates" title="Shows unused modules where a newer release can be selected."><div class="metric-label">Update</div><div class="metric-value">${escapeHtml(String(updateCount))}</div></button>` +
    `</div>` +
    `<article class="panel">` +
    `<h3>Unused Modules</h3>` +
    `<div class="bulk-actions">` +
    `${copyButton(deleteCommand, "Copy Delete", "Copies one command that deletes all currently visible unused modules.") || ""}` +
    `${copyButton(forceCommand, "Force Compatibility", "Copies one command that applies forced compatibility for modules currently visible in this table.") || ""}` +
    `${(!deleteCommand && !forceCommand) ? '<span class="pager-status">No table actions for this filter.</span>' : ''}` +
    `</div>` +
    `<div class="list-toolbar">` +
    `<label class="toolbar-field grow"><span>Search unused</span><input type="search" value="${escapeHtml(ui.unused.search)}" placeholder="module name, id, reason..." data-unused-search title="Filters unused modules by title, id, or status details."></label>` +
    `</div>` +
    pager("unused", pageInfo.total, pageInfo.page, pageInfo.totalPages, { pageSizeScope: "unused", pageSize: ui.unused.pageSize, pageSizeOptions: [10, 20, 40] }) +
    `${listHtml}` +
    pager("unused", pageInfo.total, pageInfo.page, pageInfo.totalPages) +
    `</article>` +
    `</div>`;
}

function renderTab(tabId) {
  if (tabId === "actions") {
    renderActionsTab();
  } else if (tabId === "planning") {
    renderPlanningTab();
  } else if (tabId === "backups") {
    renderBackupsTab();
  } else if (tabId === "unused") {
    renderUnusedTab();
  } else if (tabId === "forced-compat") {
    renderForcedTab();
  }
  ui.rendered[tabId] = true;
}

function activateTab(tabId, options = {}) {
  if (!TAB_IDS.includes(tabId)) return;
  ui.activeTab = tabId;
  tabSections.forEach((section) => {
    section.hidden = section.dataset.tabSection !== tabId;
  });
  tabButtons.forEach((button) => {
    const match = button.dataset.tabTarget === tabId;
    button.classList.toggle("is-active", match);
    if (button.closest(".tab-nav")) {
      button.setAttribute("aria-selected", match ? "true" : "false");
    }
  });
  if (!ui.rendered[tabId] || options.forceRender) {
    renderTab(tabId);
  }
}

function syncMetricButtons() {
  metricFilterButtons.forEach((button) => {
    const state = button.dataset.filterState || "";
    button.classList.toggle("is-active", ui.actions.state !== "all" && state === ui.actions.state);
  });
}

function shiftPage(scope, delta) {
  const totalPages = Math.max(parseInt(ui.pageTotals[scope] || 1, 10), 1);
  if (scope === "actions") {
    ui.actions.page = clamp(ui.actions.page + delta, 1, totalPages);
    renderActionsTab();
    return;
  }
  if (scope === "forced") {
    ui.forced.page = clamp(ui.forced.page + delta, 1, totalPages);
    renderForcedTab();
    return;
  }
  if (scope === "planning-systems") {
    ui.planning.page.systems = clamp(ui.planning.page.systems + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-blocked") {
    ui.planning.page.blocked = clamp(ui.planning.page.blocked + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-upgrade") {
    ui.planning.page.upgrade = clamp(ui.planning.page.upgrade + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-ready") {
    ui.planning.page.ready = clamp(ui.planning.page.ready + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-unused-incompatible") {
    ui.planning.page.unusedIncompatible = clamp(ui.planning.page.unusedIncompatible + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-unused-compatible") {
    ui.planning.page.unusedCompatible = clamp(ui.planning.page.unusedCompatible + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "planning-unused-updates") {
    ui.planning.page.unusedUpdates = clamp(ui.planning.page.unusedUpdates + delta, 1, totalPages);
    renderPlanningTab();
    return;
  }
  if (scope === "backups") {
    ui.backups.page = clamp(ui.backups.page + delta, 1, totalPages);
    renderBackupsTab();
    return;
  }
  if (scope === "unused") {
    ui.unused.page = clamp(ui.unused.page + delta, 1, totalPages);
    renderUnusedTab();
  }
}

document.addEventListener("click", async (event) => {
  const detailsToggle = event.target.closest("[data-toggle-details]");
  if (detailsToggle) {
    const card = detailsToggle.closest(".mobile-collapsible");
    if (card) {
      const expanded = card.classList.toggle("is-expanded");
      detailsToggle.dataset.toggleDetails = expanded ? "expanded" : "collapsed";
      detailsToggle.textContent = expanded ? "Hide" : "Details";
    }
    return;
  }

  const tabButton = event.target.closest("[data-tab-target]");
  if (tabButton) {
    activateTab(String(tabButton.dataset.tabTarget || ""));
  }

  const filterMetric = event.target.closest(".metric-btn[data-filter-state]");
  if (filterMetric) {
    const state = String(filterMetric.dataset.filterState || "all");
    ui.actions.state = ui.actions.state === state ? "all" : state;
    ui.actions.page = 1;
    syncMetricButtons();
    activateTab("actions", { forceRender: true });
    document.getElementById("actions")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const scrollMetric = event.target.closest(".metric-btn[data-scroll-target]");
  if (scrollMetric) {
    const targetId = String(scrollMetric.dataset.scrollTarget || "");
    if (targetId === "backups" || targetId === "unused") {
      activateTab(targetId, { forceRender: true });
      const targetNode = document.getElementById(targetId);
      if (targetNode) {
        targetNode.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }

  const planningMetric = event.target.closest(".metric-btn[data-planning-view]");
  if (planningMetric) {
    const selectedView = String(planningMetric.dataset.planningView || "");
    ui.planning.view = ui.planning.view === selectedView ? "" : selectedView;
    if (ui.planning.view === "unused" && window.matchMedia("(max-width: 980px)").matches) {
      ui.planning.unusedFilter = "incompatible";
    } else if (ui.planning.view !== "unused") {
      ui.planning.unusedFilter = "all";
    }
    ui.planning.page.systems = 1;
    ui.planning.page.blocked = 1;
    ui.planning.page.upgrade = 1;
    ui.planning.page.ready = 1;
    ui.planning.page.unusedIncompatible = 1;
    ui.planning.page.unusedCompatible = 1;
    ui.planning.page.unusedUpdates = 1;
    renderPlanningTab();
  }

  const planningTargetMetric = event.target.closest(".metric-btn[data-planning-target-version]");
  if (planningTargetMetric) {
    const selectedTarget = String(planningTargetMetric.dataset.planningTargetVersion || "");
    if (selectedTarget) {
      ui.planning.target = selectedTarget;
      ui.planning.page.systems = 1;
      ui.planning.page.blocked = 1;
      ui.planning.page.upgrade = 1;
      ui.planning.page.ready = 1;
      ui.planning.page.unusedIncompatible = 1;
      ui.planning.page.unusedCompatible = 1;
      ui.planning.page.unusedUpdates = 1;
      renderPlanningTab();
    }
  }

  const planningUnusedMetric = event.target.closest(".metric-btn[data-planning-unused-filter]");
  if (planningUnusedMetric) {
    const selectedFilter = String(planningUnusedMetric.dataset.planningUnusedFilter || "all");
    ui.planning.unusedFilter = ui.planning.unusedFilter === selectedFilter ? "all" : selectedFilter;
    ui.planning.page.unusedIncompatible = 1;
    ui.planning.page.unusedCompatible = 1;
    ui.planning.page.unusedUpdates = 1;
    renderPlanningTab();
  }

  const unusedFilterMetric = event.target.closest(".metric-btn[data-unused-filter]");
  if (unusedFilterMetric) {
    const selectedFilter = String(unusedFilterMetric.dataset.unusedFilter || "all");
    ui.unused.filter = ui.unused.filter === selectedFilter ? "all" : selectedFilter;
    ui.unused.page = 1;
    renderUnusedTab();
  }

  const pagerButton = event.target.closest("[data-page-scope]");
  if (pagerButton) {
    const scope = String(pagerButton.dataset.pageScope || "");
    const delta = parseInt(String(pagerButton.dataset.pageDelta || "0"), 10) || 0;
    if (scope && delta) {
      shiftPage(scope, delta);
    }
  }

  const copyButtonNode = event.target.closest("[data-copy-command]");
  const updateAllBtn = event.target.closest("#update-all-btn");
  if (updateAllBtn) {
    try {
      const modulesRaw = String(updateAllBtn.dataset.updateModules || "[]");
      const modules = JSON.parse(modulesRaw);
      if (!Array.isArray(modules) || modules.length === 0) return;
      updateAllBtn.disabled = true;
      const csrfToken = (document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("mm_csrf=")) || "").split("=", 2)[1] || "";
      await fetch("/api/actions/submit", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrfToken) },
        body: JSON.stringify({ action: "apply", payload: { modules: modules, batchSize: 10 } }),
      });
      updateAllBtn.textContent = "Queued";
    } catch (_err) {
      updateAllBtn.disabled = false;
    }
    return;
  }
  if (!copyButtonNode) return;
  const command = copyButtonNode.dataset.copyCommand || "";
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    const previous = copyButtonNode.textContent;
    copyButtonNode.textContent = "Copied";
    setTimeout(() => {
      copyButtonNode.textContent = previous;
    }, 1200);
  } catch {
    copyButtonNode.textContent = "Copy failed";
  }
});

function handleControlEvent(target) {
  if (target.matches("[data-actions-search]")) {
    ui.actions.search = String(target.value || "");
    ui.actions.page = 1;
    rerenderWithFocusPreserved(target, () => renderActionsTab());
    return;
  }
  if (target.matches("[data-forced-search]")) {
    ui.forced.search = String(target.value || "");
    ui.forced.page = 1;
    rerenderWithFocusPreserved(target, () => renderForcedTab());
    return;
  }
  if (target.matches("[data-planning-search]")) {
    ui.planning.search = String(target.value || "");
    ui.planning.page.systems = 1;
    ui.planning.page.blocked = 1;
    ui.planning.page.upgrade = 1;
    ui.planning.page.ready = 1;
    ui.planning.page.unusedIncompatible = 1;
    ui.planning.page.unusedCompatible = 1;
    ui.planning.page.unusedUpdates = 1;
    rerenderWithFocusPreserved(target, () => renderPlanningTab());
    return;
  }
  if (target.matches("[data-backups-search]")) {
    ui.backups.search = String(target.value || "");
    ui.backups.page = 1;
    rerenderWithFocusPreserved(target, () => renderBackupsTab());
    return;
  }
  if (target.matches("[data-unused-search]")) {
    ui.unused.search = String(target.value || "");
    ui.unused.page = 1;
    rerenderWithFocusPreserved(target, () => renderUnusedTab());
    return;
  }
  if (target.matches("[data-page-size-scope]")) {
    const scope = String((target).dataset.pageSizeScope || "");
    const parsedSize = Math.max(parseInt(String((target).value || "1"), 10) || 1, 1);
    if (scope === "actions") {
      ui.actions.pageSize = parsedSize;
      ui.actions.page = 1;
      renderActionsTab();
      return;
    }
    if (scope === "forced") {
      ui.forced.pageSize = parsedSize;
      ui.forced.page = 1;
      renderForcedTab();
      return;
    }
    if (scope === "planning") {
      ui.planning.pageSize = parsedSize;
      ui.planning.page.systems = 1;
      ui.planning.page.blocked = 1;
      ui.planning.page.upgrade = 1;
      ui.planning.page.ready = 1;
      ui.planning.page.unusedIncompatible = 1;
      ui.planning.page.unusedCompatible = 1;
      ui.planning.page.unusedUpdates = 1;
      renderPlanningTab();
      return;
    }
    if (scope === "backups") {
      ui.backups.pageSize = parsedSize;
      ui.backups.page = 1;
      renderBackupsTab();
      return;
    }
    if (scope === "unused") {
      ui.unused.pageSize = parsedSize;
      ui.unused.page = 1;
      renderUnusedTab();
    }
  }
}

document.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  handleControlEvent(target);
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  handleControlEvent(target);
});

function formatGeneratedAtLabel(rawValue) {
  if (!rawValue) return "Generated -";
  const parsed = new Date(rawValue);
  if (Number.isNaN(parsed.getTime())) return "Generated -";
  const now = Date.now();
  const elapsedSeconds = Math.max(Math.floor((now - parsed.getTime()) / 1000), 0);
  const absolute = parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  if (elapsedSeconds < 60) return `Generated just now (${absolute})`;
  if (elapsedSeconds < 3600) {
    const minutes = Math.floor(elapsedSeconds / 60);
    const unit = minutes === 1 ? "minute" : "minutes";
    return `Generated ${minutes} ${unit} ago (${absolute})`;
  }
  if (elapsedSeconds < 86400) {
    const hours = Math.floor(elapsedSeconds / 3600);
    const unit = hours === 1 ? "hour" : "hours";
    return `Generated ${hours} ${unit} ago (${absolute})`;
  }
  const days = Math.floor(elapsedSeconds / 86400);
  const unit = days === 1 ? "day" : "days";
  return `Generated ${days} ${unit} ago (${absolute})`;
}

function refreshGeneratedAtLabels() {
  document.querySelectorAll("[data-generated-at]").forEach((node) => {
    const value = node.dataset.generatedAt || "";
    node.textContent = formatGeneratedAtLabel(value);
  });
}
refreshGeneratedAtLabels();
window.setInterval(refreshGeneratedAtLabels, 15000);

const THEME_KEY = "resolver-v3-theme";
const savedTheme = String(localStorage.getItem(THEME_KEY) || "");
if (savedTheme === "light") {
  document.body.classList.remove("is-dark");
} else {
  document.body.classList.add("is-dark");
}
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const darkEnabled = document.body.classList.toggle("is-dark");
    localStorage.setItem(THEME_KEY, darkEnabled ? "dark" : "light");
  });
}

syncMetricButtons();
activateTab("actions", { forceRender: true });
"""


_SERVICE_GATEWAY_SCRIPT = r"""
(function () {
  const input = document.getElementById("foundry-root-input");
  const inputModal = document.getElementById("foundry-root-input-modal");
  const saveBtn = document.getElementById("foundry-root-save");
  const saveBtnModal = document.getElementById("foundry-root-save-modal");
  const browseBtn = document.getElementById("foundry-root-browse");
  const browseBtnModal = document.getElementById("foundry-root-browse-modal");
  const resetBtn = document.getElementById("foundry-root-reset");
  const resetBtnModal = document.getElementById("foundry-root-reset-modal");
  const changeBtn = document.getElementById("foundry-root-change");
  const pickerInput = document.getElementById("foundry-root-picker");
  const statusEl = document.getElementById("foundry-root-status");
  const statusModalEl = document.getElementById("foundry-root-status-modal");
  const logoutBtn = document.getElementById("logout-btn");
  const settingsOpenBtn = document.getElementById("settings-open-btn");
  const settingsCloseBtn = document.getElementById("settings-close-btn");
  const settingsModal = document.getElementById("settings-modal");
  const addModuleOpenBtn = document.getElementById("add-module-open-btn");
  const addModuleCloseBtn = document.getElementById("add-module-close-btn");
  const addModuleModal = document.getElementById("add-module-modal");
  const tabHub = document.getElementById("tab-hub");
  const tabSections = Array.from(document.querySelectorAll(".tab-section"));
  const suggestBtn = document.getElementById("suggest-module-btn");
  const suggestStatus = document.getElementById("suggest-module-status");
  const suggestManifestUrl = document.getElementById("suggest-manifest-url");
  const firstRunBtn = document.getElementById("first-run-btn");

  function setInteractive(enabled) {
    if (tabHub) tabHub.style.display = enabled ? "" : "none";
    tabSections.forEach((section) => { section.style.display = enabled ? "" : "none"; });
    if (firstRunBtn) { firstRunBtn.style.display = enabled ? "" : "none"; firstRunBtn.disabled = !enabled; }
    if (input) input.style.display = enabled ? "none" : "";
    if (saveBtn) saveBtn.style.display = enabled ? "none" : "";
    if (changeBtn) changeBtn.style.display = enabled ? "" : "none";
    if (resetBtn) resetBtn.style.display = enabled ? "" : "none";
    if (settingsOpenBtn) settingsOpenBtn.classList.toggle("needs-config", !enabled);
  }

  async function api(path, method, body) {
    const csrfToken = (document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("mm_csrf=")) || "").split("=", 2)[1] || "";
    const response = await fetch(path, {
      method: method || "GET",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrfToken) },
      body: body ? JSON.stringify(body) : null
    });
    const text = await response.text();
    let payload = {};
    try { payload = JSON.parse(text || "{}"); } catch { payload = { raw: text }; }
    if (!response.ok) throw new Error(payload.message || payload.error || ("HTTP " + response.status));
    return payload;
  }

  function setStatus(message, ok) {
    if (statusEl) { statusEl.textContent = message || ""; statusEl.style.color = ok ? "#16a34a" : "#b91c1c"; }
    if (statusModalEl) { statusModalEl.textContent = message || ""; statusModalEl.style.color = ok ? "#16a34a" : "#b91c1c"; }
  }

  async function refreshConfig() {
    try {
      const payload = await api("/api/config/foundry-root", "GET");
      if (input) input.value = payload.selected || "";
      if (inputModal) inputModal.value = payload.selected || "";
      if (payload.valid) { setStatus(payload.message || "Foundry path is valid.", true); setInteractive(true); }
      else { setStatus(payload.message || "Select a valid Foundry path to continue.", false); setInteractive(false); }
    } catch (err) { setStatus(String(err && err.message ? err.message : err), false); setInteractive(false); }
  }

  async function validatePath(pathValue) {
    const payload = await api("/api/config/foundry-root", "POST", { path: pathValue || "" });
    if (input) input.value = payload.selected || pathValue || "";
    if (inputModal) inputModal.value = payload.selected || pathValue || "";
    setStatus(payload.message || "Path saved.", !!payload.valid);
    setInteractive(!!payload.valid);
  }

  async function pickPath(targetInput) {
    try {
      const payload = await api("/api/config/foundry-root/pick", "POST", {});
      const p = payload.selected || payload.normalized || payload.selectedPath || "";
      if (targetInput) targetInput.value = p;
      await validatePath(p);
      return;
    } catch (_err) {
      if (pickerInput) pickerInput.click();
    }
  }

  if (saveBtn) saveBtn.addEventListener("click", async function () { await validatePath((input && input.value) || ""); });
  if (saveBtnModal) saveBtnModal.addEventListener("click", async function () { await validatePath((inputModal && inputModal.value) || ""); });
  if (browseBtn) browseBtn.addEventListener("click", async function () { await pickPath(input); });
  if (browseBtnModal) browseBtnModal.addEventListener("click", async function () { await pickPath(inputModal); });
  if (changeBtn) changeBtn.addEventListener("click", async function () { await pickPath(input); });

  if (pickerInput) {
    pickerInput.addEventListener("change", async function () {
      const files = pickerInput.files;
      if (!files || files.length === 0) return;
      const first = files[0];
      let selectedPath = "";
      if (first && typeof first.path === "string" && first.path) {
        const normalized = first.path.replace(/\\\\/g, "/");
        const idx = normalized.lastIndexOf("/");
        selectedPath = idx > 0 ? normalized.slice(0, idx) : normalized;
      }
      if (!selectedPath) { setStatus("Browser blocked full path. Use Select Folder again.", false); return; }
      await validatePath(selectedPath);
    });
  }

  async function resetPath() {
    await api("/api/config/foundry-root/reset", "POST", {});
    if (input) input.value = "";
    if (inputModal) inputModal.value = "";
    setStatus("Foundry path reset.", false);
    setInteractive(false);
  }
  if (resetBtn) resetBtn.addEventListener("click", async function () { await resetPath(); });
  if (resetBtnModal) resetBtnModal.addEventListener("click", async function () { await resetPath(); });

  if (settingsOpenBtn && settingsModal && settingsModal.showModal) settingsOpenBtn.addEventListener("click", function () { settingsModal.showModal(); });
  if (settingsCloseBtn && settingsModal) settingsCloseBtn.addEventListener("click", function () { settingsModal.close(); });
  if (addModuleOpenBtn && addModuleModal && addModuleModal.showModal) addModuleOpenBtn.addEventListener("click", function () { addModuleModal.showModal(); });
  if (addModuleCloseBtn && addModuleModal) addModuleCloseBtn.addEventListener("click", function () { addModuleModal.close(); });

  if (logoutBtn) logoutBtn.addEventListener("click", async function () { try { await api("/api/auth/logout", "POST", {}); } catch (_err) {} window.location.replace("/"); });

  if (suggestBtn) {
    suggestBtn.addEventListener("click", async function () {
      const manifestUrl = String((suggestManifestUrl && suggestManifestUrl.value) || "").trim();
      if (!manifestUrl) { if (suggestStatus) suggestStatus.textContent = "Provide module.json URL."; return; }
      try {
        suggestBtn.disabled = true;
        if (suggestStatus) suggestStatus.textContent = "Resolving best compatible version...";
        const payload = await api("/api/actions/suggest-module", "POST", { manifestUrl: manifestUrl });
        const suggestion = payload.suggestion || {};
        const msg = ["Recommended: " + String(suggestion.recommendedVersion || "-"), "Compatible: " + String(!!suggestion.isCompatible), "Checked releases: " + String(suggestion.checkedReleases || 0)].join(" | ");
        if (suggestStatus) suggestStatus.textContent = msg;
      } catch (err) {
        if (suggestStatus) suggestStatus.textContent = String(err && err.message ? err.message : err);
      } finally { suggestBtn.disabled = false; }
    });
  }

  refreshConfig();
})();
"""




