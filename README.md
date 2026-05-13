# FoundryVTT Modulator

FoundryVTT Modulator helps you plan and apply safe module/system version updates based on:

- Current Foundry version
- Installed game systems
- Module compatibility metadata (`minimum`, `verified`, `maximum`)
- Dependency constraints between modules

The goal is to reduce broken upgrades and make update decisions predictable.

## What the app does

- Scans installed systems/modules and builds a compatibility report
- Suggests update/install targets for modules and systems
- Highlights blockers and missing dependencies
- Lets you plan upgrades for future Foundry versions
- Keeps operational history for rollback/maintenance workflows

## Quick Start (UI)

### 1) Install dependencies

```bash
pip install -r backend/requirements.txt
cd frontend
npm install
npm run build
cd ..
```

### 2) Run API + UI

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8787
```

Open:

- `http://127.0.0.1:8787/`

### 3) First login

- Create admin username/password on first access.

### 4) Configure Foundry path

- Click the gear button.
- Set Foundry data root.
- Validate and save.

### 5) Start Scan

- Click `Start Scan`.
- Review results in `Current`.

### 6) Use Planning

- Select target Foundry version.
- Review suggested system/module paths for that target.
- Apply updates in your preferred sequence.

## Main Screens

- `Current`: current state, blockers, update/install actions
- `Planning`: target Foundry view, future-safe suggestions
- `Backups`: maintenance/health/rollback helpers

## Troubleshooting

### PowerShell blocks `npm.ps1`

Use `npm.cmd` in this repo, or allow scripts for current user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### `Start Scan` disabled

- Foundry path not configured or invalid.
- Reopen settings (gear), validate path, save again.

### Missing recommendation (`?`)

- Module source URL not configured, or source cannot be resolved.
- Set module source (`manifestUrl`/`projectUrl`) and re-scan.

### macOS/Linux packaging script fails with `bash\r`

- This is usually CRLF line ending mismatch.
- CI now normalizes shell scripts before packaging runs.

## CLI (still available)

CLI is **not removed**.

### Dry run

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --dry-run \
  --batch-size 10
```

### Apply

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --apply \
  --batch-size 10
```

### Filter by module

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --module midi-qol \
  --module dae \
  --dry-run
```

## Artifacts

Generated files (latest):

- `reports/module-resolver-latest.log`
- `reports/module-resolver-latest.json`
- `reports/module-resolver-latest.html` (optional via explicit export)

Optional HTML export endpoint:

- `POST /api/v1/report/v3/export-html`
- `POST /api/v1/report/v3/export-snapshot` (modules/systems snapshot JSON)

## Docker

Single service (API/UI only):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Self-host stack with nginx included (recommended for safer defaults):

```bash
docker compose -f docker-compose.selfhost.yml up -d --build
```

Notes for `docker-compose.selfhost.yml`:

- UI path is `/modulator`
- `/api/v1/*` is restricted to localhost/LAN by default in `deploy/nginx/modulator.conf`
- Legacy `/module-resolver*` is disabled by default

Environment variables you may set:

- `FOUNDRY_DATA_ROOT` (required)
- `MODULATOR_HTTP_BIND` (default `127.0.0.1`)
- `MODULATOR_HTTP_PORT` (default `8788`)

## License

- `AGPL-3.0-or-later`
