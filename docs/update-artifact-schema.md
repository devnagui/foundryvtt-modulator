# Update Artifact Schema (v1.0.0)

This document defines the persisted artifact generated after update actions (`apply` and `override-from-plan`).

## Purpose

- Track exactly what the update intended to change.
- Bind version transitions to local backup files.
- Enable reliable backup/download from the Backups UI.

## Top-level fields

```json
{
  "schemaVersion": "1.0.0",
  "planId": "plan-YYYYMMDD-HHMMSS-XXXXXXXXX",
  "createdAt": "ISO-8601 UTC",
  "action": "apply | override-from-plan",
  "scanRunId": 123,
  "profile": "current | destiny | both",
  "foundryCurrentVersion": "13.351",
  "foundryTargetVersion": "14.361",
  "systems": [],
  "modules": [],
  "backups": [],
  "summary": { "applied": 0, "skipped": 0, "failed": 0 },
  "source": {}
}
```

## Systems entry

```json
{
  "name": "dnd5e",
  "currentVersion": "5.3.0",
  "targetVersion": "5.3.3",
  "status": "applied | already | skipped | failed | ready",
  "backupPath": "optional absolute path"
}
```

## Modules entry

```json
{
  "name": "dae",
  "moduleId": "dae",
  "currentVersion": "12.0.0",
  "targetVersion": "13.0.0",
  "status": "applied | already | skipped | failed | ready",
  "backupPath": "optional absolute path"
}
```

## Backups entry

```json
{
  "path": "absolute .bak path",
  "exists": true,
  "sizeBytes": 12345,
  "sha256": "hex digest"
}
```

## Storage

- JSON artifacts are stored in:
  - `state/update-artifacts/<planId>.json`
- Download bundles are generated on demand in:
  - `state/update-artifacts/<planId>.zip`

## API

- `GET /api/v1/report/v3/update-artifacts`
- `GET /api/v1/report/v3/update-artifacts/{planId}`
- `GET /api/v1/report/v3/update-artifacts/{planId}/download?includeBackupData=true|false`

