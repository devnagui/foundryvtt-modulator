# Plan: Verified-Only Filter Affecting Recommendations

## Problem

The "Verified Only" checkbox currently only hides rows from the Current tab display.
It does **not** change which version is recommended in the Update Path column.

Example: With Foundry 13.351 + dnd5e 5.2.5 selected, ddb-importer still shows
`6.6.54 → 7.2.21` even though 7.2.21 requires Foundry min=14.359 and dnd5e min=5.3.0.

### Data from resolver.db

| Version | F_verified | F_min   | F_max | dnd5e_verified | dnd5e_min | dnd5e_max |
|---------|-----------|---------|-------|----------------|-----------|-----------|
| 7.2.20  | 14.359    | 14.359  | 14    | 5.3.1          | 5.3.0     | 5.3.99    |
| 7.1.15  | 13.351    | 13.346  | 13    | 5.3.0          | 5.3.0     | 5.3.99    |
| 7.1.14  | 13.351    | 13.346  | 13    | 5.3.0          | 5.3.0     | 5.3.99    |
| 7.1.13  | 13.351    | 13.346  | 13    | 5.3.0          | 5.3.0     | 5.3.99    |

**Best version for F13.351 + dnd5e 5.3.0**: 7.1.15 (or 7.1.14)
**Best version for F13.351 + dnd5e 5.2.5**: NONE (all 7.x require dnd5e >= 5.3.0)

## Two Bugs

### Bug 1: Recommendation ignores range constraints in planning context
7.2.21 has `minimum=14.359` — even without verified-only, it should NEVER be
recommended for F13.351. The planning context or resolver is picking it from a
stale/wrong source (possibly the module manifest URL pointing to latest, not
the version-specific release).

**Fix**: In the planning context resolution and/or `_execute_action_job`, ensure
the resolver's range check (`min <= target <= max`) is correctly applied before
any version is recommended. Check if the planning context hydration bypasses
the compatibility filter.

### Bug 2: Verified-only filter doesn't affect recommendation column
The filter currently only controls row visibility via `filteredCurrent`. When a
module passes the filter, its `recommendedVersion` still comes from the standard
range-based resolver — not from a verified-matching candidate.

**Fix**: When verified-only is active, the frontend should prefer a
`verifiedRecommendedVersion` over the standard `recommendedVersion`.

## Implementation Plan

### Step 1: Backend — Add `verifiedRecommendedVersion` to recommendations

**File**: `resolver/scoring.py` or wherever the best release is picked.

When the resolver computes the best release for a module, also compute:
- `verifiedRecommendedVersion`: the highest version where:
  - `verified` Foundry major matches the target Foundry major
  - AND `verified` system version major matches the target system (if system compat exists)
  - AND the version passes range-based min/max checks

Store this alongside the standard `recommendedVersion` in the recommendation output.

**File**: `resolver/report_v3.py` — include `verifiedRecommendedVersion` in report rows.

### Step 2: Backend — Fix range-based recommendation for planning context

**File**: `backend/app/services/runtime.py` — planning context hydration.

Investigate why 7.2.21 is recommended for F13.351 when its `min=14.359`.
Likely the planning context pulls the "latest" version from the manifest URL
without re-checking compatibility. Ensure range validation occurs.

**File**: `resolver/sources.py` — version resolution from GitHub/GitLab releases.

Check if `suggest_source_versions` or similar functions skip compatibility
filtering when returning candidates.

### Step 3: Frontend — Use verified recommendation when filter is active

**File**: `frontend/src/pages/ReportPage.tsx`

In `renderCurrentModuleRow()` and planning row rendering:
- When `verifiedOnly` is true, use `row.verifiedRecommendedVersion` (if present)
  instead of `row.recommendedVersion` for the update path
- If `verifiedRecommendedVersion` is empty but `recommendedVersion` exists,
  show a warning badge: "No verified version available for this target"

In export:
- Include `verifiedRecommendedVersion` in export rows when present

### Step 4: Frontend — System version check in verified filter

**File**: `frontend/src/pages/ReportPage.tsx`

The `isVerifiedForTarget()` helper currently checks Foundry major only.
Enhance it to also check system version when system compat metadata exists:
- If `systemCompatibility[systemId].verified` exists, its major must match
  the selected system version major
- Modules with no system compat data pass (no false positives)

This is partially implemented but needs verification with the actual system
version selected in the filter pills, not just the "active" system.

### Step 5: Tests

- Unit test for `isVerifiedForTarget()` with system compat scenarios
- Test that `verifiedRecommendedVersion` is populated correctly in report
- Test that planning context doesn't recommend versions outside range
- Frontend build validation

## Priority Order

1. **Step 2** (Bug 1) — Fix the range-based recommendation. This is a data
   correctness bug; 7.2.21 should never appear for F13.
2. **Step 4** — Enhance system version check in verified filter (quick frontend fix)
3. **Step 1 + Step 3** — Add verified recommendation column (larger feature)
4. **Step 5** — Tests

## Files to Modify

- `resolver/scoring.py` — verified candidate selection
- `resolver/report_v3.py` — include verified recommendation in output
- `backend/app/services/runtime.py` — planning context range validation
- `resolver/sources.py` — version resolution compatibility check
- `frontend/src/pages/ReportPage.tsx` — use verified recommendation, enhance filter
- `tests/` — new test cases
