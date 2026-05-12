# Start Scan Flow

This document explains what the app does when the user clicks `Start Scan`.

## 1) UI action

- The header button triggers action `dry-run`.
- Frontend sends `POST /api/v1/actions/submit` with `{ action: "dry-run", payload: {} }`.
- Backend enqueues a job in `ActionEngine`.

## 2) Worker execution

- Background worker picks the job.
- Runtime executes resolver CLI with `--dry-run`.
- Resolver writes:
  - JSON report (`module-resolver-latest.json`)
  - log file

## 3) Post-scan enrichment

After `dry-run` succeeds, backend runs report enrichment before returning final job success:

1. Load latest JSON report.
2. Load configured module sources (`module-sources.json`).
3. Resolve and inject recommendations for `currentSystemUpgrades.rows` that still have no recommendation.
4. Resolve and inject recommendations for unresolved `results[].dependencyActions` (missing/not-installed dependencies) when source URL exists.
5. Annotate presentation fields (`presentationStatus`, `hasMissingDependencies`).
6. Persist the enriched JSON report back to disk.
7. Keep HTML generation out of the default scan path.

## 4) Current tab rendering impact

- Frontend reads `/api/v1/report/v3/model`.
- Rows with enriched recommendation show concrete update path (`- -> <version>`).
- Rows with no source remain unresolved and may show `?`.
- Optional HTML export is available through `POST /api/v1/report/v3/export-html`.

## 5) DAE and socketlib examples

### DAE (not installed, source already set)

- During post-scan enrichment, backend resolves best compatible release for `dae`.
- Recommendation is persisted in report payload.
- UI can show `Install` and concrete target version without requiring manual re-resolution.

### socketlib (not installed, no source set)

- No source means backend cannot resolve release history for this dependency.
- Recommendation remains empty.
- UI keeps unresolved update path as `?` until source is provided.

## 6) Why this matters

- Reduces repeated client-side hydration work.
- Keeps scan results deterministic and persisted.
- Prevents regressions where known-source dependencies keep showing `- -> ?`.

## 7) Set URL integration

When user saves a module source URL:

- Backend resolves recommendation for that module.
- Latest report is enriched again (JSON). HTML is generated only if export is requested explicitly.
