# module-matcher-fvtt: Open Source Publication Plan

## 1. Goal
Prepare `foundryModuleVersioningTool` for public release as **module-matcher-fvtt** with:
- clean repository history and structure
- reproducible runtime in Linux, Docker, and Windows
- safer UX inside Foundry (action buttons that execute workflows)

## 2. Repository Readiness
### 2.1 Naming and structure
- Rename project-facing name to `module-matcher-fvtt` in docs/UI.
- Keep internal Python module paths stable initially; perform package/module rename in a dedicated migration PR.
- Create `docs/` for architecture, operations, and release notes.

### 2.2 Open source hygiene
- Keep generated artifacts out of git (`.cache/`, `reports/`, `state/`, `__pycache__/`, local env files).
- Add `LICENSE` (recommend MIT or Apache-2.0).
- Add `CONTRIBUTING.md` and `SECURITY.md`.
- Add issue/PR templates.

### 2.3 Reproducibility
- Pin Python version and dependencies.
- Add a single command bootstrap (`make setup` or `scripts/bootstrap.sh`).
- Add CI checks: lint + unit tests + smoke dry-run.

## 3. Packaging Strategy
### 3.1 Linux package
- Deliver first as Python package + CLI entrypoint.
- Optional second step: standalone binary via PyInstaller for distro-agnostic install.

### 3.2 Docker image
- Build official image with:
  - CLI runtime
  - optional scheduler sidecar (cron-like behavior)
  - mounted volumes for Foundry data and output reports
- Publish `docker-compose` example for fast adoption.

### 3.3 Windows package
- Provide PowerShell installer and/or PyInstaller executable.
- Include Windows-safe paths and service/scheduler instructions.

## 4. Foundry Integration Plan
### 4.1 UI entry inside Foundry
Option A (recommended): lightweight Foundry module "Module Matcher Control Panel"
- Adds a button in setup/admin area.
- Displays resolver status and action panels.
- Calls local backend API.

Option B: external web UI only
- Lower coupling, simpler maintenance.
- No in-Foundry button.

Recommendation: start with A + fallback to B.

### 4.2 Authentication and trust boundary
- Do not trust browser-only state for destructive actions.
- Require explicit server-side authorization for every action endpoint.
- Keep an audit trail for who executed each action and from where.

#### 4.2.1 Validated constraints in Foundry v13.351 (current environment)
- Setup and update administrative actions are guarded by admin session checks in Foundry setup routes.
- Setup actions are only available when no world is active for many operations.
- Foundry modules do not execute on Setup/Join screens (they only affect game view), so setup UI customization cannot rely on standard world module scripts.
- Admin access is backed by the administrator password hash in `Config/admin.txt` and session cookies.

#### 4.2.2 MVP authentication model (decision)
Primary auth for Linux/Windows MVP:
- Local password only, encrypted at rest.
- No local resolver token in this phase.
- Password login issues short-lived server session for UI/API use.

Storage and encryption:
- Windows: protect credential material with DPAPI.
- Linux: store salted+hashed password (Argon2id or bcrypt) and restrictive file permissions.
- Never store plaintext password.

Operational roles (derived from authenticated session):
- `viewer`: read-only endpoints.
- `operator`: scan/dry-run actions.
- `admin`: maintenance actions (`apply`, `force-compat`, `cleanup`).

Optional enterprise hardening (later):
- reverse-proxy auth gate (basic auth/OIDC) in front of resolver endpoints.

#### 4.2.3 Recommended phased strategy
Phase A (MVP, recommended)
- Primary auth: local encrypted password
- Session-based authorization with role checks
- No dependency on Foundry private session semantics

Phase B (ergonomics)
- Add first-run setup wizard and password reset flow
- Add installer repair/reset-auth mode

Phase C (deep integration)
- Add controlled GM bridge for in-world status, previews, and queued jobs
- Keep destructive operations gated by admin-capable channel

#### 4.2.4 Endpoint authorization model (proposed)
- `GET /health`, `GET /report`: read-only, allowed with Foundry online
- `POST /scan` (dry-run): authenticated operator, allowed with Foundry online
- `POST /apply`, `POST /force-compat`, `POST /cleanup`: authenticated admin only, maintenance mode required (Foundry offline)
- Add action confirmation nonce for destructive calls
- Add idempotency keys and job IDs to avoid duplicate execution

#### 4.2.5 Credential and session handling
- Never log raw passwords or session cookies.
- Persist minimal metadata in logs: action, module count, caller id, timestamp, result.
- Session timeout and inactivity expiration must be enforced.
- Support password reset via controlled local recovery flow.

### 4.3 Action buttons (replace copy-command UX)
Current: copy command to clipboard.
Target:
- each action button triggers backend endpoint (dry-run/apply/force-compat/cleanup)
- backend runs allowlisted operations only
- UI shows live job status, logs, and completion feedback
- add safe modes: confirmation dialog + preview diff

### 4.4 Setup screen integration viability
- Standard module scripts are not available on Setup/Join for security reasons.
- Viable implementation paths:
  - Proxy/script injection in startup pipeline (current operational pattern in this environment).
  - Dedicated external dashboard linked from setup-adjacent controls.
  - Controlled patch/plugin layer at server template level (higher maintenance cost).
- Recommendation: keep setup customizations injection-based and isolated behind feature flags.

### 4.5 Decision for open-source baseline
- Baseline mode for `module-matcher-fvtt`:
  - Password-authenticated local API/session
  - External dashboard or injected setup helper UI
  - No mandatory dependency on Foundry private internals
- Advanced integrations (session bridge/proxy SSO) shipped as optional profiles.

### 4.5.1 Operational split (recommended)
- Split operation into two explicit modes:
  - `Read mode` (Foundry online): status/report/preview only, no destructive actions.
  - `Maintenance mode` (Foundry offline): apply/force-compat/cleanup operations.
- Rationale:
  - avoids concurrent writes while Foundry is serving setup/world traffic
  - simplifies authorization and audit for destructive operations
  - reduces risk of data corruption and partial state updates
- Execution guardrails:
  - preflight checks that Foundry is stopped before maintenance endpoints run
  - optional controlled stop/start via docker compose service integration
  - maintenance lock file to prevent concurrent jobs
  - explicit `--skip-foundry-service-control` only for advanced/manual operators
- Detailed lock contract:
  - `MAINTENANCE_LOCK_SPEC.md`

### 4.6 Evidence references
- Local runtime verification (Foundry 13.351 in container):
  - `/home/node/resources/app/dist/server/views/setup.mjs`
  - `/home/node/resources/app/dist/server/views/auth.mjs`
  - `/home/node/resources/app/dist/sessions.mjs`
  - `/home/node/resources/app/dist/server/views/view.mjs`
- Official Foundry documentation:
  - Application Configuration: admin password and `admin.txt`
  - Introduction to Module Development: modules do not affect Setup/Join screens

## 5. Execution Phases
### Phase 1: Open source prep (now)
- `.gitignore` and artifact cleanup policy
- docs baseline
- repo naming and public README refresh

### Phase 2: Runtime hardening
- config file support (instead of ad-hoc env dependence)
- structured logs and error codes
- test coverage for resolver critical paths

### Phase 3: Packaging
- Docker image + compose sample
- Linux and Windows distribution paths

### Phase 4: Foundry control surface
- backend API service
- in-Foundry module panel
- action-button command orchestration

## 6. Risks and mitigations
- API abuse risk: mitigate with strong password policy + lockout/backoff + allowlist + rate limits.
- Cross-platform path issues: centralize path handling and add OS matrix tests.
- Release breakage: semantic versioning + changelog + rollback docs.

## 7. Proposed immediate backlog (next PRs)
1. Add `pyproject.toml` + dependency lock strategy.
2. Add CI workflow (lint, tests, smoke dry-run).
3. Add Dockerfile + compose example.
4. Add API design draft for action-based operations.
5. Scaffold Foundry companion module for setup/admin button.
