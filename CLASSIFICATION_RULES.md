# Classification And Compatibility Rules

This file is the canonical reference for module/system classification and UI behavior.

## 1) Canonical status priority

Priority order:

1. `missing`
2. `blocked`
3. `update`
4. `ready`

## 2) Module status rules

### 2.1 `missing`

A module is `missing` when:

- `hasMissingDependencies = true`, or
- Required dependencies are unresolved/not installed.

Important:

- The dependent module receives `missing` (example: `dae` depends on `socketlib`).
- The missing dependency row itself (example: `socketlib`) does not become `missing` just because another module depends on it.

### 2.2 `blocked`

A module is `blocked` when:

- It is not `missing`, and
- There is no safe compatible recommendation for the selected Foundry + system context.

### 2.3 `update`

A module is `update` when:

- It is not `missing`, and
- A compatible recommended version exists for the selected context.

Additional action rule:

- If `installedVersion` is empty or `"-"`, keep state as `update`, but action label must be `Install` (not `Update`).

### 2.4 `ready`

A module is `ready` when:

- It is not `missing`, and
- No install/update action is required for the selected context.

## 3) Not-installed modules with/without source URL

### 3.1 DAE scenario (not installed, source URL already set)

When a module is not installed but its source is known (`manifestUrl` or `projectUrl`):

- The app must resolve and persist recommendation metadata (`recommendedVersion`, URL, compatibility) when possible.
- The row must not stay in `- -> ?` after successful resolution.
- UI should show `- -> <recommendedVersion>` and action `Install`.

### 3.2 Socketlib scenario (not installed, no source URL)

When a module is not installed and no source URL is known:

- Update path can be shown as `?`.
- Recommendation remains unknown until user sets URL or source is discovered.

## 4) System rows

For rows marked as `(system)`:

- Show Foundry badge only (`F`).
- Do not show system badge (`S`) for system rows.
- `Ready` action is non-clickable.

## 5) Badges and tooltips

### 5.1 Foundry badge (`F`)

- `Fv`: compatible
- `FX`: incompatible
- `F?`: uncertain (insufficient metadata)

Tooltip must include:

- `compatible{min: x, verified: y, max: z}`

### 5.2 System badge (`S`) for modules only

- `Sv`: compatible
- `SX`: incompatible
- `S?`: uncertain

Tooltip must include:

- `compatible{min: x, verified: y, max: z}`

### 5.3 Missing dependency badge

- Icon: `!`
- Show only when `hasMissingDependencies = true`.
- Tooltip format: `missing dependency: <id1>, <id2>, ...`

## 6) Action/update path behavior

- Never render `- -> -`.
- If recommendation exists: render `installed -> recommended`.
- If recommendation is still resolving and source exists: render `Loading...`.
- If no recommendation after resolution attempt and no source: render `?`.

## 7) Start Scan behavior (required)

On `Start Scan` (`dry-run`):

- Generate fresh report payload.
- Enrich current rows with source-based recommendations.
- Enrich unresolved dependency actions with source-based recommendations (for not-installed dependencies such as `dae`).
- Persist enriched report to `module-resolver-latest.json`.
- Do not generate legacy HTML by default in this flow.

This guarantees:

- If `dae` source is already configured, scan resolves recommendation and stores it.
- If `socketlib` has no source, it remains unresolved and can show `?`.

## 8) Source save behavior

When user sets module source (`Set URL` / `Add Module`):

- Persist source (`manifestUrl` / `projectUrl`) in module source store.
- Resolve recommendation immediately.
- Re-enrich latest report (`JSON`) only.

If needed, HTML is exported explicitly through `POST /api/v1/report/v3/export-html`.
