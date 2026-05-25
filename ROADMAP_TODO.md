# Roadmap TODO

## Completed

<details><summary>Priority 0 (done)</summary>

- [x] Fix readiness percentage calculation per Foundry/system version.
- [x] Fix "Maximum update depth exceeded" infinite loop in Current tab useEffect.
- [x] Wire system Update button in Planning tab (`override-from-plan` action).
- [x] UI polish: align all pills to the right, compact module status pills.
- [x] Fix Planning "Blocked & Missing" pill showing 0.
- [x] Module name as clickable link to GitHub/GitLab project page.

</details>

<details><summary>Import Resilience (done)</summary>

- [x] Per-module logging in `apply_override_from_plan`.
- [x] Enriched `progressMeta` with error/skip/applied counts.
- [x] Frontend: real-time error badges, failed items list, rate-limit advisory.
- [x] HTTP 429 detection + retry with exponential backoff.
- [x] GitHub + GitLab API token support (env + UI config).
- [x] Provider tokens API (`GET/POST/DELETE /api/v1/config/provider-tokens`).
- [x] Settings UI: token input fields (password-masked, save/clear, status badges).
- [x] Parallel batched module resolution (`ThreadPoolExecutor`).
- [x] Job cancellation (backend + frontend).
- [x] Lock TTL (30 min) + stale lock auto-cleanup on startup.

</details>

---

## Priority 1 — Export & Verified Compatibility

### 1.1 Populate `usedInWorlds` in export (frontend)
- [ ] Include `worldUsage` array in the export JSON (currently only available from API, not embedded in export).
- [ ] Add `usedInWorlds` field to Planning tab export rows (currently only in Current tab rows).
- [ ] On import of an exported plan on another machine, world data is self-contained in the file.

### 1.2 "Verified Only" filter checkbox (frontend — Current tab)
- [x] Add a default-on checkbox filter: **"Verified versions only"**.
- [x] Checkbox state persists per session (sessionStorage).
- [x] Visual indicator on the filter bar when active (shield icon).
- [x] Applied to both Current and Planning tabs.

---

## Priority 2 — Version Lock Groups

### 2.1 Backend: Lock Group Store + API (done)
- [x] `LockGroupStore` — CRUD persistence in `state/version-lock-groups.json`.
- [x] Data model: `LockGroup` with `entries[]` of `{ packageId, packageKind (module|system), version, verified, required, notes }`.
- [x] REST API: `GET/POST /api/v1/lock-groups`, `GET/PUT/DELETE /api/v1/lock-groups/{id}`, `POST /api/v1/lock-groups/from-current`.
- [x] Thread-safe file I/O with `threading.Lock`.
- [x] Entry validation: deduplication, kind normalization, empty-version filtering.
- [x] Registered in `AppRuntime` and API router.
- [x] 12 unit tests (test_lock_groups.py).

### 2.2 Frontend: Lock Group UI (done)
- [x] TypeScript types: `LockGroup`, `LockGroupEntry`.
- [x] API client methods: `lockGroups`, `createLockGroup`, `updateLockGroup`, `deleteLockGroup`, `createLockGroupFromCurrent`.
- [x] Group selector dropdown in Current + Planning filter bars (purple theme when active).
- [x] `lockGroupIndex` computed map for O(1) lookup of pinned modules.
- [x] 🔒 badge on module names when member of active group (with tooltip: pinned version, verified status, notes).

### 2.3 Frontend: Module Actions Modal (done)
- [x] Action column shows compact clickable status summary badge (`Ready ⋯`, `Update ⋯`, etc.).
- [x] Clicking opens contextual modal with all available actions per module:
  - Install / Update / Force Compatibility (current tab only).
  - Find Source / Set URL (for blocked modules without source).
  - Refresh Versions (from GitHub/GitLab).
  - Lock Group management: shows membership, add/remove from any group.
  - Pin status display when module is in active group.
  - Link to Manage Groups modal.

### 2.4 Frontend: Lock Group Management Modal (done)
- [x] Create new group (empty or from current installation snapshot).
- [x] Edit group: inline table with version, verified, required toggles per entry.
- [x] Delete group with safety check.
- [x] Activate/deactivate group from management view.
- [x] "Create from Current" snapshots all installed modules + systems.

### 2.5 Future: Lock Group Enhancements
- [ ] Import/export lock groups as JSON.
- [ ] Lock group version override in update path column (show pinned version instead of latest).
- [ ] "Fix All" scoped to active lock group (install pinned versions).
- [ ] Planning tab auto-targets Foundry/system version from active group.
- [ ] Conflict detection: warn when installed version is newer than pinned (downgrade).
- [ ] System entry in lock group constrains system compatibility evaluation.
