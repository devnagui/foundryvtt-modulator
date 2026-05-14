# Classification And Compatibility Rules

Canonical reference for Current/Planning classification, badges, update path, and actions.

## 1) Scope

- Rules apply to all module rows and system rows.
- No module-specific hardcode is allowed.
- Decisions must be derived from selected targets + compatibility metadata + resolved recommendation data.

Inputs:
- Foundry target
- System target
- Compatibility (`minimum`, `verified`, `maximum`)
- Source availability (`manifestUrl`/`projectUrl`)
- Recommendation resolution (contextual + hydrated + cached)

## 2) Display Ordering

Table ordering:
1. Group by `system`
2. Within each system, status order:
   `blocked` -> `missing` -> `update` -> `ready`
3. Then by title

System rows:
- Always shown on top of the table block (before module rows), unless filtered out by an explicit filter.

## 3) Status Classification

### 3.1 `missing`

When:
- `hasMissingDependencies = true`, or
- required dependency cannot be resolved.

Notes:
- The dependent module is `missing`.
- The missing dependency module itself is not automatically `missing`.

### 3.2 `blocked`

When:
- not `missing`, and
- selected context is incompatible (`F` and/or `S` incompatible), or
- no safe compatible recommendation exists for selected context.

### 3.3 `update`

When:
- not `missing`/`blocked`, and
- compatible recommendation exists and is newer than installed.

### 3.4 `ready`

When:
- not `missing`/`blocked`, and
- no install/update action is needed.

## 4) Compatibility Semantics

Supported bound formats:
- plain versions (`13.350`, `5.3.2`)
- major-only (`13`)
- wildcards (`5.3.x`, `5.3.*`)
- comparators (`>=`, `<=`, `>`, `<`, `=`)
- suffix `+` (`13.350+` = `>=13.350`)

### Open-ended max

`maximum` values like `-`, `*`, `any`, `none` are treated as open-ended.

Important:
- If `verified` major differs from selected target major and max is open-ended, treat as **uncertain** (not incompatible).
- This avoids false `FX`/`SX` for modules that are effectively open upward.

## 5) Badges

## 5.1 Foundry badge (`F`)

Mutually exclusive per row:
- `F✓` compatible
- `F✕` incompatible
- `F?` uncertain
- `F↑` follow-up: verified points to a later selected target
- `FU` open-ended/uncertain case (verified major mismatch + open max)

## 5.2 System badge (`S`) (modules only)

Mutually exclusive per row:
- `S✓` compatible
- `S✕` incompatible
- `S?` uncertain
- `S↑` follow-up: verified points to a later selected system target
- `SU` open-ended/uncertain case (verified major mismatch + open max)

Rules:
- Show `S*` only if module declares system compatibility restrictions.
- If no system restriction metadata exists, do not render `S`.
- Tooltips for `S*` must include restricted system ids.

## 5.3 Missing dependency badge

- Icon: `!`
- Tooltip format: `missing dependency: <id1>, <id2>, ...`

## 5.4 System upgrade conflict badge

- Icon: `SC`
- Current/Planning: shows cross-system recommendation conflict.

## 6) Tooltip Contract

Compatibility badges include:
- `compatible{min: x, verified: y, max: z}`

Follow-up tooltips:
- `F↑`: `Update Suggested: Verified for Foundry version <verified>. ...`
- `S↑`: `Update Suggested: Verified for system version <verified>. ... | systems: ...`

Open-ended tooltips:
- `FU`/`SU`: explain open max + verified major mismatch as uncertain (not blocked).

All tooltips must reflect active selected filters/context (never stale context).

## 7) Update Path + Recommendation Selection

Never show:
- `- -> -`
- downgrade path (e.g. `1.7 -> 1.6`)
- placeholder target like `0.0.0` as real recommendation

Path behavior:
- If real forward recommendation exists: `installed -> recommended`
- If recommendation unresolved and source exists: `Loading...`
- If no source/recommendation: `?`
- If installed and no forward recommendation: show installed only

Recommendation selection must ignore:
- empty
- `-`
- `0.0.0`
- any version `<= installedVersion` for already installed modules

## 8) Not Installed Modules

Source known (`manifestUrl`/`projectUrl`):
- resolve recommendation and persist metadata
- show installable path/action when possible

No source:
- recommendation unknown
- update path may remain `?`

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
- not missing
- not 404/not-found
- minimum bound lower than target (foundry or system)

## 10) Scan / Refresh Behavior

Start Scan (`dry-run`):
- rebuild report
- enrich recommendations using source data
- enrich unresolved dependency actions
- persist enriched JSON report

Row refresh:
- calls suggest with `forceRefresh = true`
- invalidates module suggestion cache for that module id
- keeps prior row stable on provider error and shows inline error/retry state

Provider errors exposed:
- `provider_not_found`
- `provider_rate_limited`
- `provider_timeout`
- `provider_forbidden`
- `provider_malformed_response`
- `provider_error`
