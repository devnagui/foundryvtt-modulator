# Frontend Architecture

## Overview

React 18 + TypeScript single-page application built with Vite 5.4, styled with TailwindCSS + daisyUI.
Served at `frontend/` (dev: `npm run dev` on port 5173).

## Entry Point

`src/main.tsx` → `<App />` → route-based rendering (login page or report page).

## Key Pages

### `ReportPage.tsx`

The main page. Renders three tabs: **Current**, **Planning**, **Backups**.

#### State Management

All state is `useState` hooks at the top of `ReportPage`. Key state variables:

| Variable | Purpose |
|---|---|
| `model` | Full report model from `/api/v1/report/v3/model` |
| `planningContextRowsByModule` | Pre-computed planning data from DB, keyed by module ID |
| `resolvedSourceByModule` | Hydrated suggestions per module (from suggest API) |
| `resolvedSourceByContext` | Hydrated suggestions per context+module key |
| `dependencySuggestionByModule` | Dependency-level suggestions |
| `manifestUrlByModule` | Manifest URL lookup from scan results |

#### Data Flow: Current Tab

1. `model.reportViews.v3.currentModuleUpdates` provides base data
2. `currentRows` derived via `useMemo` — maps raw results to `ModuleRow[]`
3. Each row is enriched with:
   - `resolvedSourceByModule[moduleId]` — recommended version/URL from suggest API
   - `dependencySuggestionByModule[moduleId]` — dependency resolution status
4. System filter pills (`currentSystemFilter`) and version bucket pills filter the rows
5. Readiness % calculated from module states after filtering

#### Data Flow: Planning Tab

1. `model.reportViews.v3.systemUpgradePlanner.targets` provides planner structure
2. **Planning context rows** fetched from `/api/v1/report/v3/planning-context`:
   - Contains pre-computed compatibility for ALL modules (used + unused)
   - Keyed by `{foundryVersion}::{systemId}@{systemVersion}`
   - Stored in `planningContextRowsByModule`
3. `projectPlanningRowsFoundryAggregate()` merges:
   - Planner target rows (world-used modules with full recommendation)
   - Current rows projected into planning (unused modules, using planning context data)
4. **Hydration loop** (suggest API) enriches modules lacking full recommendation:
   - Checks `resolvedAttemptedByContext` to avoid duplicate calls
   - Uses `manifestUrlByModule` as fallback for manifest URL discovery
   - Calls `api.suggestModulesBatch()` in batches of 6
   - Results stored in `resolvedSourceByContext`
5. `derivePlanningEffectiveState(row)` computes final state per module

#### Data Flow: Backups Tab

Renders `model.reportViews.v3.backupManagement` — backup list with restore/delete actions.

## API Client (`src/pages/api.ts`)

Typed wrapper around `fetch()`:

- `getModel()` → `GET /api/v1/report/v3/model`
- `planningContext(foundry, system, version)` → `GET /api/v1/report/v3/planning-context`
- `suggestModulesBatch(items, context)` → `POST /api/v1/actions/suggest-modules-batch`
- `submitAction(payload)` → `POST /api/v1/actions/submit`
- `actionStatus(jobId)` → `GET /api/v1/actions/status/{jobId}`
- `login(password)` → `POST /api/v1/auth/login`

## Key Components

### `ReportPage.tsx` (page)
- Tab rendering (Current / Planning / Backups)
- Module row rendering with status badges, compatibility indicators, action buttons
- Filter pills for system + version selection
- Readiness % badges per system
- Loading modal with scan progress

### `AddModuleModal.tsx`
- Modal for adding modules by manifest URL or search
- Validates URL, fetches module info, allows install

### `SettingsModal.tsx`
- Configuration: Foundry path, maintenance window, feature flags

### `UpdatePathWithRefresh.tsx`
- Shows installed → recommended version path
- Refresh button to re-resolve from manifest

### `UpgradePanel.tsx`
- Aggregated action panel: "Fix All" button for modules with configured sources

## Visual/UX Rules

- **Status ordering**: `missing` > `blocked` > `update` > `ready`
- **Badges**: `F*` for Foundry compat, `S*` for System compat
  - Green = compatible, Red = incompatible, Yellow = uncertain
- **Tooltips**: "incompatible" only for explicit min/max/verified breaks; "uncertain" when metadata missing
- **Actions**: `Install` (yellow) for missing, `Update` for upgradable, `Ready` (non-clickable) for OK
- **Fix All**: Acts only on modules with source configured
- **Filters**: Version/system filters above module list, initialized to current system/version
- **Mobile**: Actions right-aligned, no forced full-width buttons

## Build & Test

```bash
cd frontend
npm.cmd run build    # TypeScript compile + Vite production build
npm.cmd run test     # Vitest unit tests
npm.cmd run dev      # Vite dev server on port 5173
```

## Feature Flags

- `USE_NEW_UI=true` — enables React UI (vs legacy HTML report)
- `RESOLVER_UI_DIST_DIR` — custom path to built frontend assets
- `RESOLVER_DISABLE_LEGACY_REPORT_UI=true` — disables legacy report endpoint
