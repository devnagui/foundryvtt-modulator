from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_auth, require_csrf, require_rate_limit, runtime

router = APIRouter(prefix="/lock-groups")


@router.get("")
def list_lock_groups(req: Request) -> dict:
    rt = runtime()
    require_auth(req, rt)
    return {"ok": True, "groups": rt.lock_group_store.list_groups()}


@router.get("/{group_id}")
def get_lock_group(req: Request, group_id: str) -> dict:
    rt = runtime()
    require_auth(req, rt)
    group = rt.lock_group_store.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail={"error": "group_not_found"})
    return {"ok": True, "group": group}


@router.post("")
def create_lock_group(req: Request, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        group = rt.lock_group_store.create_group(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return {"ok": True, "group": group}


@router.put("/{group_id}")
def update_lock_group(req: Request, group_id: str, body: dict) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    try:
        group = rt.lock_group_store.update_group(group_id, body)
    except ValueError as exc:
        code = str(exc)
        status = 404 if code == "group_not_found" else 400
        raise HTTPException(status_code=status, detail={"error": code}) from exc
    return {"ok": True, "group": group}


@router.delete("/{group_id}")
def delete_lock_group(req: Request, group_id: str) -> dict:
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)
    if not rt.lock_group_store.delete_group(group_id):
        raise HTTPException(status_code=404, detail={"error": "group_not_found"})
    return {"ok": True}


@router.post("/from-current")
def create_from_current(req: Request, body: dict) -> dict:
    """Snapshot current installed modules into a new lock group."""
    rt = runtime()
    require_rate_limit(req, rt)
    require_auth(req, rt)
    require_csrf(req)

    from resolver.foundry import detect_foundry_version
    from resolver.local import load_modules, load_system_versions

    data_root = rt.config_store.get_data_root() or rt.config.data_root
    foundry_version, _ = detect_foundry_version(data_root)
    system_versions = load_system_versions(data_root)
    installed_modules = load_modules(data_root)

    # Build entries from current installation
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail={"error": "name_required"})

    # Optional: filter to specific module IDs
    module_ids = body.get("moduleIds")
    if isinstance(module_ids, list):
        module_ids = {str(m).strip() for m in module_ids if str(m).strip()}
    else:
        module_ids = None

    entries: list[dict] = []

    # Include systems if requested
    include_systems = body.get("includeSystems", True)
    if include_systems:
        for sys_id, sys_ver in system_versions.items():
            entries.append({
                "packageId": sys_id,
                "packageKind": "system",
                "version": sys_ver,
                "verified": True,
                "required": True,
            })

    # Include modules
    for mod in installed_modules:
        if module_ids is not None and mod.module_id not in module_ids:
            continue
        entries.append({
            "packageId": mod.module_id,
            "packageKind": "module",
            "version": mod.version,
            "verified": False,  # Caller or UI enriches with verified status
            "required": True,
        })

    group_data = {
        "name": name,
        "foundryVersion": foundry_version or "",
        "entries": entries,
    }

    try:
        group = rt.lock_group_store.create_group(group_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return {"ok": True, "group": group}
