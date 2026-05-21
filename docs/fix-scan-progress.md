# Fix: Scan Progress Modal Stuck at 60%

## Problem

When the user clicks **Start Scan** (dry-run or apply), the progress modal jumps to 60% immediately and stays there for the entire duration of the scan, then jumps to 95%→100% when done. No intermediate progress is shown.

## Root Cause

In `backend/app/services/runtime.py`, the worker loop (`_worker_loop`, line ~205) uses hardcoded progress jumps:

```
5%  → pick_next() (job picked from queue)
25% → "queued" phase
60% → "running" phase (set BEFORE _execute_action_job starts)
--- entire scan runs here with ZERO progress updates ---
95% → "finalizing" (set AFTER execution completes)
100% → complete
```

For scan actions (dry-run, apply, force-compat, cleanup-backups), `_execute_action_job` (line ~1570) calls `subprocess.run()` which is **fully blocking** — the CLI runs to completion with `capture_output=True` and returns all output at once. There is no mechanism to stream intermediate progress from the subprocess.

**Contrast with import:** The `override-from-plan` action has proper granular progress via a `_import_progress` callback that reports per-module progress (lines 1461-1475). Scan actions have no equivalent.

## Affected Code Paths

1. **Worker loop**: `backend/app/services/runtime.py` lines 205-215 — hardcoded 25%→60%→95% jumps
2. **Scan execution**: `backend/app/services/runtime.py` lines ~1570-1585 — `subprocess.run()` blocking call
3. **CLI entrypoint**: `resolver/cli.py` — no progress output mechanism exists
4. **Frontend polling**: `frontend/src/pages/ReportPage.tsx` lines ~1097-1142 — polls every 1s, but backend never updates during scan

## Proposed Solution

### Option A: Stream subprocess output (recommended)

Replace `subprocess.run()` with `subprocess.Popen()` for scan actions and parse progress from CLI output:

1. **Add progress markers to `resolver/cli.py`**: Print structured progress lines to stderr (e.g., `PROGRESS:30:Loading installed modules`) at each major phase:
   - `10%` — Validating data root and loading installed packages
   - `20%` — Loading world usage data
   - `30%` — Fetching remote release metadata (this is the slow part)
   - `50%` — Resolving compatible versions per module
   - `70%` — Evaluating dependencies
   - `80%` — Generating compatibility report
   - `90%` — Writing report files and persisting to database

2. **In `_execute_action_job`**: Use `Popen` with `stderr=PIPE` and a reader thread to parse `PROGRESS:` lines. Call `runtime.action_engine.set_progress()` on each progress line.

3. **Update worker loop**: Remove the hardcoded 60% jump before `_execute_action_job`. Instead, set 10% at start and let the subprocess drive progress from there.

### Option B: File-based progress (simpler)

1. CLI writes progress to a temp file (e.g., `state/.scan-progress.json`) at each phase
2. Worker loop spawns a watcher thread that reads the file every 500ms and calls `set_progress()`
3. Less intrusive to CLI code but adds filesystem polling

### Key Files to Modify

- `resolver/cli.py` — Add `PROGRESS:` stderr output at each major phase
- `backend/app/services/runtime.py`:
  - `_worker_loop()` (~line 205): Remove hardcoded 60% before execution
  - `_execute_action_job()` (~line 1570): Replace `subprocess.run()` with `Popen` + progress parsing for scan actions
- `backend/app/services/core.py` — `set_progress()` already supports this (line 236), no changes needed
- `frontend/src/pages/ReportPage.tsx` — Already polls every 1s and renders `progressMeta.phase`, no changes needed

### Progress Phase Map for CLI

```python
SCAN_PHASES = [
    (10, "loading_packages", "Loading installed packages..."),
    (20, "loading_worlds", "Loading world data..."),
    (30, "fetching_releases", "Fetching release metadata..."),
    (50, "resolving_versions", "Resolving compatible versions..."),
    (70, "evaluating_dependencies", "Evaluating dependencies..."),
    (80, "generating_report", "Generating report..."),
    (90, "persisting", "Saving to database..."),
]
```

### Constraints

- Must not break existing CLI behavior (stdout JSON output, exit codes)
- Progress lines go to **stderr** only (stdout is reserved for JSON report)
- `subprocess.Popen` must still capture stdout for the result dict
- Must handle CLI crash/timeout gracefully (progress stops, error surfaces)
- Keep the existing `_import_progress` callback for `override-from-plan` unchanged
