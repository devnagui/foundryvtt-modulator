# FoundryVTT Modulator

Simple tool to analyze Foundry modules, suggest compatible versions, and run safe maintenance actions.

## UI First (Recommended)

Use the web UI for daily operations.

### 1) Start the app

```bash
pip install -r backend/requirements.txt
USE_NEW_UI=true uvicorn backend.app.main:app --host 0.0.0.0 --port 8787
```

Open:

- `http://127.0.0.1:8787/`

### Runtime

FastAPI backend is the standard runtime path.

### React UI (current recommended UI)

Build frontend:

```bash
cd frontend
npm install
npm run build
```

Build and run FastAPI backend:

```bash
USE_NEW_UI=true uvicorn backend.app.main:app --host 0.0.0.0 --port 8787
```

Current UI flow:

- Header actions: `Start Scan`, `Settings (gear)`, `Logout`
- Gear status:
  - yellow = Foundry path not configured/invalid
  - green = Foundry path configured/valid
- `Start Scan` is disabled until Foundry path is valid
- `Settings` opens modal to configure Foundry Data Root

### 2) First login setup

- Create `username + password`
- Password policy is enforced (strong password required)

### 3) Configure Foundry path

- Click the `gear` button in the header
- Select or paste your Foundry root path
- Validate and save

### 4) Use main UI flows

- `Current`: current module status and actions
- `Foundry Upgrade`: plan for future Foundry versions
- `Backups`: cleanup/maintenance helpers
- `Unused Modules`: compatibility and cleanup actions
- `Add Module`: suggest best version from `module.json` URL

## UI Screenshots (Placeholders)

Add example images here:

- `docs/images/ui-login.png`
- `docs/images/ui-dashboard.png`
- `docs/images/ui-settings-modal.png`
- `docs/images/ui-add-module-modal.png`

Example markdown:

```md
![Login](docs/images/ui-login.png)
![Dashboard](docs/images/ui-dashboard.png)
```

## CLI Commands

Use CLI when you want automation or scripting.

### Dry-run (safe preview)

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --dry-run \
  --batch-size 10
```

### Analyze specific modules

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --module midi-qol \
  --module dae \
  --dry-run \
  --batch-size 10
```

### Apply updates

```bash
python -m resolver.cli \
  --data-root /path/to/foundry/root \
  --apply \
  --batch-size 10
```

## Security Notes

- Auth uses `username + password` with PBKDF2 hash
- Login lockout on repeated failures
- CSRF protection on authenticated `POST` endpoints
- Global request rate limiting by IP
- Session cookie is HttpOnly (`mm_session`)

If you are locked out and cannot recover:

1. Stop the service
2. Delete `state/auth.json`
3. Start again and create new credentials

## Reports and Artifacts

Generated files (latest):

- `reports/module-resolver-latest.log`
- `reports/module-resolver-latest.json`
- `reports/module-resolver-latest.html` (optional, generated only by explicit export)

HTML export endpoint:

- `POST /api/v1/report/v3/export-html`

## Docker (optional)

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

## CI/CD

- CI: `.github/workflows/ci.yml`
- Release: `.github/workflows/release.yml`

## License

- `AGPL-3.0-or-later` (see `LICENSE`)
- Attribution details in `NOTICE`
