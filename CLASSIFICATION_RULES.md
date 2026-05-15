# Classification And Compatibility Rules

Canonical reference for Current/Planning classification, badges, update path, actions, and matrix consistency.

## 1) Scope

- Rules apply to module rows and system rows.
- No module-specific hardcode.
- Decisions derive from selected targets + compatibility metadata + recommendation resolution.

Inputs:
- Foundry target
- System target
- Compatibility (`minimum`, `verified`, `maximum`)
- Source availability (`manifestUrl` / `projectUrl`)
- Recommendation resolution (contextual + hydrated + cached)

## 2) Display Ordering

Table ordering:
1. Group by `system`
2. Within each system: `blocked` -> `missing` -> `update` -> `ready`
3. Then by title

System rows:
- Always shown at top block before module rows, unless explicitly filtered out.

## 3) Status Classification

### 3.1 `missing`
When:
- `hasMissingDependencies = true`, or
- required dependency cannot be resolved.

### 3.2 `blocked`
When:
- not `missing`, and
- selected context is incompatible (`F` and/or `S` incompatible), or
- no safe compatible recommendation exists for selected context.

### 3.3 `update`
When:
- not `missing` / `blocked`, and
- compatible recommendation exists and is newer than installed.

### 3.4 `ready`
When:
- not `missing` / `blocked`, and
- no install/update action is needed.

## 4) Compatibility Semantics

Supported formats:
- plain versions (`13.350`, `5.3.2`)
- major-only (`13`)
- wildcard (`5.3.x`, `5.3.*`)
- comparators (`>=`, `<=`, `>`, `<`, `=`)
- suffix `+` (`13.350+` = `>=13.350`)

Open-ended tokens:
- `-`
- `*`
- `any`
- `none`

Open-ended behavior:
- open `maximum` means upper bound is not enforced.
- if verified major differs from selected target and range is open-ended, treat as uncertain (not hard incompatible).

## 5) Badges

### 5.1 Foundry badges (`F*`) - mutually exclusive
- `F?` compatible
- `F?` incompatible
- `F?` uncertain (insufficient metadata)
- `F?` follow-up (verified points to later target)
- `F~` open-ended uncertainty

Rules:
- Exactly one `F*` badge per row.
- `F?` has precedence.
- `F~` replaces generic loose-max badge for Foundry dimension.

### 5.2 System badges (`S*`) - mutually exclusive
- `S?` compatible
- `S?` incompatible
- `S?` uncertain (insufficient metadata)
- `S?` follow-up (verified points to later target)
- `S~` open-ended uncertainty

Rules:
- Exactly one `S*` badge per row.
- Show `S*` only when system compatibility restriction exists.
- Include restricted system ids in tooltip.
- `S?` is suppressed when `SC` exists.

### 5.3 Other badges
- `!` missing dependency (`missing dependency: <ids>`)
- `SC` system conflict (different suggested versions across systems)
- `[x]` forced compatibility active

## 6) Tooltip Contract

Compatibility tooltip format:
- `compatible{min: x, verified: y, max: z}`

Follow-up tooltips:
- `F?`: `Update Suggested: Verified for Foundry version <verified>. ...`
- `S?`: `Update Suggested: Verified for system version <verified>. ... | systems: ...`

Open-ended tooltips:
- `F~` / `S~` explain open-ended bounds and uncertainty clearly.

All tooltips must reflect active selected context.

## 7) Update Path Rules

Never show:
- `- -> -`
- downgrades (e.g. `1.7 -> 1.6`)
- `0.0.0` as valid recommendation

Behavior:
- forward recommendation: `installed -> recommended`
- unresolved with known source: `Loading...`
- no source/recommendation: `?`
- no forward action: show installed only

Ignore candidates:
- empty
- `-`
- `0.0.0`
- for installed modules, any `<= installedVersion`

## 8) Not Installed Modules

Source known:
- resolve recommendation and persist metadata
- show installable path/action

No source:
- recommendation unknown
- update path may stay `?`

## 9) Actions

Install/update:
- not installed + valid recommendation => `Install`
- installed + newer valid recommendation => `Update`

Ready:
- non-clickable

Blocked:
- non-clickable

Force Compatibility:
- Current only
- installed module
- compatibility failure exists
- not follow-up-only case (`F?` / `S?`)
- not missing
- not 404/not-found
- minimum bound lower than current target (Foundry or System)

## 10) Scan / Refresh

Start Scan (`dry-run`):
- rebuild report
- enrich recommendations from sources
- enrich unresolved dependency actions
- persist enriched report snapshot

Row refresh:
- suggest endpoint with force refresh
- invalidate module suggestion cache
- keep stable row output on provider errors and show inline retryable status

## 11) Planning Matrix Consistency

- Matrix percentages and table rows must use the same classification logic.
- `Include unused modules in matrix and table totals` controls whether `unused` contributes to matrix and planning table totals.
