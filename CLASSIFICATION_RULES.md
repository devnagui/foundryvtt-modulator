# Classification And Compatibility Rules

Canonical reference for Current/Planning classification, badges, update path, and actions.

## 1) Scope

- Rules apply to all module rows and system rows.
- No module-specific hardcode is allowed.
- Decisions are derived from selected targets + compatibility metadata + resolved recommendation data.

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
- Always shown at the top block of the table (before module rows), unless explicitly filtered out.

## 3) Status Classification

### 3.1 `missing`

When:
- `hasMissingDependencies = true`, or
- required dependency cannot be resolved.

Notes:
- The dependent module is `missing`.
- The dependency module itself is not automatically `missing`.

### 3.2 `blocked`

When:
- not `missing`, and
- selected context is incompatible (`F` and/or `S` incompatible), or
- no safe compatible recommendation exists for selected context.

### 3.3 `update`

When:
- not `missing` / `blocked`, and
- a compatible recommendation exists and is newer than installed.

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

Open-ended rules:
- `maximum` open-ended => upper bound is not enforced.
- `minimum` open-ended => lower bound is not enforced.
- `minimum` and `maximum` both open-ended => range bounds are unconstrained; compatibility depends on remaining metadata.
- If `verified` major differs from selected target major and bounds are open-ended, treat as **uncertain** (not incompatible), to avoid false `FX` / `SX`.

## 5) Badges

## 5.1 Foundry badge (`F`)

Mutually exclusive per row:
- `F✓` compatible
- `F✕` incompatible
- `F?` uncertain (generic uncertainty)
- `F↑` follow-up: verified points to a later selected target
- `F~` uncertain caused by open-ended bounds + verified major mismatch

`F?` vs `F~`:
- `F?` = generic uncertainty (insufficient/incomplete metadata).
- `F~` = specific uncertainty from open-ended range with verified major mismatch.

## 5.2 System badge (`S`) (modules only)

Mutually exclusive per row:
- `S✓` compatible
- `S✕` incompatible
- `S?` uncertain (generic uncertainty)
- `S↑` follow-up: verified points to a later selected system target
- `SU` uncertain caused by open-ended bounds + verified major mismatch

`S?` vs `SU`:
- `S?` = generic uncertainty (insufficient/incomplete metadata).
- `SU` = specific uncertainty from open-ended range with verified major mismatch.

Rules:
- Show `S*` only when module declares system compatibility restrictions.
- If no system restriction metadata exists, do not render `S`.
- `S` tooltips must include restricted system ids.

## 5.3 Missing dependency badge

- Icon: `!`
- Tooltip: `missing dependency: <id1>, <id2>, ...`

## 5.4 System upgrade conflict badge

- Icon: `SC`
- Current/Planning: shown when different systems suggest different versions for the same module.

## 6) Tooltip Contract

Compatibility tooltip format:
- `compatible{min: x, verified: y, max: z}`

Follow-up tooltips:
- `F↑`: `Update Suggested: Verified for Foundry version <verified>. ...`
- `S↑`: `Update Suggested: Verified for system version <verified>. ... | systems: ...`

Open-ended tooltips:
- `F~` / `SU`: must explain open-ended bounds + verified major mismatch and that status is uncertain (not blocked).

All tooltips must reflect active selected context (never stale context).

## 7) Update Path + Recommendation Selection

Never show:
- `- -> -`
- downgrade path (example `1.7 -> 1.6`)
- placeholder like `0.0.0` as a real recommendation

Path behavior:
- real forward recommendation => `installed -> recommended`
- unresolved recommendation with known source => `Loading...`
- no source/recommendation => `?`
- installed with no forward recommendation => show installed only

Ignore these recommendation candidates:
- empty
- `-`
- `0.0.0`
- for installed modules, any version `<= installedVersion`

## 8) Not Installed Modules

Source known:
- resolve recommendation and persist metadata
- show installable path/action when possible

No source:
- recommendation remains unknown
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
- module is installed
- compatibility failure exists
- not `missing`
- not `404/not-found`
- minimum bound is lower than current target (foundry or system)

## 10) Scan / Refresh Behavior

Start Scan (`dry-run`):
- rebuild report
- enrich recommendations from source data
- enrich unresolved dependency actions
- persist enriched JSON report

Row refresh:
- call suggest endpoint with `forceRefresh = true`
- invalidate suggestion cache for that module id
- preserve stable row output on provider error and show inline retryable error state

Provider errors exposed:
- `provider_not_found`
- `provider_rate_limited`
- `provider_timeout`
- `provider_forbidden`
- `provider_malformed_response`
- `provider_error`
