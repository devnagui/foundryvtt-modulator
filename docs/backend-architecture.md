# Backend Architecture

## Overview

FastAPI application (`backend/app/main.py`) served via uvicorn on port 8787.
Entry point: `python -m backend.run_fastapi` or `uvicorn backend.app.main:app`.

## Core Components

### Scan Flow (`resolver/cli.py`)

The scan is the primary data pipeline:

1. **Load installed modules** — `load_modules()` reads all `module.json` files from `Data/modules/`
2. **Load installed systems** — reads system manifests from `Data/systems/`
3. **Resolve recommendations** — for each module, `resolve_module_recommendation()` fetches release history from remote sources (GitHub releases, etc.), evaluates compatibility against the current Foundry version, and picks the best compatible release
4. **Build current system upgrade view** — `build_current_system_upgrade_view()` evaluates all world-used modules against each system's recommended version for the current Foundry
5. **Build future upgrade decision** — `build_future_upgrade_decision()` evaluates world-used modules against each stable future Foundry release
6. **Build report view** — `build_report_view_v3()` transforms raw scan data into the frontend-consumable JSON structure
7. **Persist to database** — `persist_scan_snapshot()` stores everything in `state/resolver.db`

### Module Resolution (`resolver/dependencies.py`)

`resolve_module_recommendation()` is the core resolution engine:

- Fetches release history with progressive limits (5 → 20 → 50 releases)
- Evaluates each release against Foundry version constraints (`compatibility.minimum`, `.maximum`)
- Checks system compatibility
- Recursively resolves dependency requirements
- Never recommends rollbacks (rollback suppression policy)
- Caches results in `resolution_cache` to avoid duplicate work

### Future Upgrade Planner (`resolver/future_upgrade.py`)

`build_future_upgrade_decision()`:

- Receives ALL installed modules (`installed_modules_by_id`) but filters to **world-used modules only**
- For each stable future Foundry release:
  - Recommends target system versions via `_recommend_future_system_version()`
  - Evaluates each module via `_evaluate_future_target()` → calls `resolve_module_recommendation()` per module
- Output: `futureUpgradeMatrix` with per-Foundry coverage percentages

### Planning Context Rows (`resolver/db.py`)

`_replace_planning_context_rows()`:

- Extracts module planning data from `systemUpgradePlanner.targets`
- Stores in SQLite `planning_context_rows` table for fast frontend queries
- **Also generates rows for unused modules**: evaluates each unused module's installed compatibility metadata against each target Foundry/system combination
- Context key format: `{foundryVersion}::{systemId}@{systemVersion}`

### Report View (`resolver/report_view_v3.py`)

`build_report_view_v3()` produces the `reportViews.v3` structure:

- `currentModuleUpdates` — modules with available updates for current Foundry
- `currentSystemUpgrades` — per-system module compatibility for current Foundry
- `systemUpgradePlanner` — future Foundry version planning targets
- `unusedModules` — installed modules not used in any world
- `backupManagement` — backup inventory and disk status

### Suggest API (`backend/app/services/runtime.py`)

`suggest_modules_batch()`:

- Receives module IDs + manifest/project URLs + target context
- Fetches manifest from URL → builds `ModuleRecord`
- Calls `resolve_module_recommendation()` with target Foundry version
- Uses cached release histories from previous scans
- Returns recommended version, download URL, compatibility metadata

## API Endpoints

### Report
- `GET /api/v1/report/v3/model` — full report model
- `GET /api/v1/report/v3/planning-context` — planning context rows from DB (fast query)

### Actions
- `POST /api/v1/actions/submit` — submit scan/apply/rollback actions
- `GET /api/v1/actions/status/{job_id}` — poll job progress
- `POST /api/v1/actions/suggest-modules-batch` — batch version suggestions

### Auth
- `POST /api/v1/auth/login` — authenticate, returns session cookie (`mm_session`)
- `POST /api/v1/auth/logout` — invalidate session

## Database Schema (`state/resolver.db`)

Key tables:
- `scan_runs` — scan execution history
- `installed_packages` — snapshot of installed modules/systems per scan
- `release_catalog` — cached release histories from remote sources
- `recommendations` — resolved recommendations per scan
- `planning_context_rows` — pre-computed planning compatibility for ALL modules
- `future_targets` — future Foundry upgrade matrix data

## Data Flow Diagram

```
Scan Request (dry-run)
  │
  ├─ load_modules() ──────────────────────► installed_modules_by_id
  │
  ├─ For each module:
  │   └─ resolve_module_recommendation()
  │       ├─ fetch_release_history() ────► release_catalog (cached)
  │       ├─ evaluate compatibility
  │       └─ return Recommendation
  │
  ├─ build_current_system_upgrade_view()
  │   └─ Per system × per module → currentSystemUpgrades
  │
  ├─ build_future_upgrade_decision()
  │   └─ Per future Foundry × per used module → futureUpgradeMatrix
  │
  ├─ build_report_view_v3() ──────────────► reportViews.v3
  │   ├─ systemUpgradePlanner.targets (world-used modules)
  │   └─ unusedModules.rows (all others)
  │
  └─ persist_scan_snapshot()
      ├─ planning_context_rows (used + unused modules)
      └─ release_catalog, recommendations, etc.
```
