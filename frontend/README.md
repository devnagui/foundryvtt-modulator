# Frontend (React + TypeScript + TailwindCSS + daisyUI)

## Development

```bash
cd frontend
npm install
npm run dev
```

Vite proxy points `/api` to `http://127.0.0.1:8787`.

Stack:

- React + TypeScript
- Vite
- TailwindCSS
- daisyUI

## Build

```bash
cd frontend
npm install
npm run build
```

Build output goes to `frontend/dist`.
Enable in backend with:

- `USE_NEW_UI=true`
- optional: `RESOLVER_UI_DIST_DIR=/custom/path/to/dist`
- optional: `RESOLVER_DISABLE_LEGACY_REPORT_UI=true`

Main UI behavior:

- Gear in header controls Foundry Data Root setup (modal)
- Gear color indicates setup status (yellow pending / green valid)
- `Start Scan` in header is blocked until Foundry path is valid
