# Maintenance Lock Contract (module-matcher-fvtt)

## 1. Purpose
Define a single-writer lock for destructive dependency maintenance actions.

Scope of locked actions:
- `apply`
- `force-compat`
- `cleanup`
- any future action that mutates Foundry module/system files

Out of scope:
- read-only actions (`health`, `report`, preview reads, dry-run status views)

## 2. File and ownership model
Lock file path:
- `${STATE_DIR}/maintenance.lock.json`
- Default `STATE_DIR`: `<project-root>/state`

Ownership semantics:
- Only one active maintenance job can own the lock.
- Lock is acquired before any stop/start service or file mutation.
- Lock is released on success, controlled failure, or explicit cancel.

## 3. Lock document schema
Required JSON fields:
- `lockVersion`: integer, current `1`
- `lockId`: string UUID
- `jobId`: string UUID
- `action`: string (`apply`, `force-compat`, `cleanup`, ...)
- `owner`: string service identity (`resolver-api`, `cli`, `scheduler`)
- `createdAt`: ISO8601 UTC timestamp
- `updatedAt`: ISO8601 UTC timestamp
- `expiresAt`: ISO8601 UTC timestamp
- `foundryService`: object with
  - `composeFile`: string
  - `serviceName`: string
- `requestedBy`: object with
  - `subject`: string (user/system principal)
  - `sourceIp`: string or `-`
- `mode`: string, must be `maintenance`
- `status`: string (`acquired`, `running`, `finalizing`)
- `pid`: integer (if local process available)

Optional fields:
- `notes`: string
- `traceId`: string
- `idempotencyKey`: string

## 4. Acquisition algorithm
1. Validate request is a maintenance action.
2. Validate Foundry precondition:
- If endpoint requires offline mode, verify service is stopped.
- If `autoServiceControl=true`, controlled stop may happen before lock acquisition only for short bounded transition; lock must still be acquired before mutation.
3. Attempt atomic create (`O_CREAT|O_EXCL`) of lock file.
4. On success, write full lock payload and fsync.
5. Start heartbeat loop updating `updatedAt` and `expiresAt`.

If lock already exists:
- Read lock payload.
- If not expired: reject with conflict.
- If expired: run stale-lock recovery path (Section 7).

## 5. Heartbeat, TTL, and renewal
Defaults:
- `LOCK_TTL_SECONDS=900` (15 min)
- `LOCK_HEARTBEAT_SECONDS=20`
- `LOCK_GRACE_SECONDS=30`

Rules:
- `expiresAt = now + LOCK_TTL_SECONDS`
- Heartbeat updates `updatedAt` and rolls `expiresAt`.
- If heartbeat fails 3 consecutive attempts, job must enter safe-fail and abort mutation step progression.

## 6. Release and finalize
Normal release:
1. Mark `status=finalizing`, heartbeat one last time.
2. Persist terminal job outcome separately (job log/result file).
3. Delete lock file.
4. Verify deletion.

Crash-safe principle:
- Never leave lock deletion as best effort only.
- If delete fails, return server error and keep system in no-new-maintenance posture.

## 7. Stale lock recovery
Stale detection:
- lock considered stale when `now > expiresAt + LOCK_GRACE_SECONDS`

Recovery steps:
1. Re-read lock and verify still stale.
2. If `pid` exists on same host and process is alive, do not steal lock.
3. Emit audit event `maintenance_lock_stale_detected`.
4. Move stale file to:
- `${STATE_DIR}/locks-history/maintenance.lock.<timestamp>.stale.json`
5. Acquire a fresh lock atomically.

Recovery should be disabled unless caller has admin role.

## 8. API behavior and error contract
### 8.1 Conflict (active lock)
HTTP:
- `409 Conflict`

Body:
```json
{
  "error": "maintenance_lock_active",
  "message": "Maintenance is already running.",
  "lock": {
    "lockId": "...",
    "jobId": "...",
    "action": "apply",
    "owner": "resolver-api",
    "createdAt": "...",
    "updatedAt": "...",
    "expiresAt": "..."
  },
  "retryAfterSeconds": 30
}
```

### 8.2 Precondition failed (Foundry still online)
HTTP:
- `412 Precondition Failed`

Body:
```json
{
  "error": "maintenance_requires_foundry_offline",
  "message": "Stop Foundry before running maintenance actions.",
  "foundry": {
    "serviceName": "foundry",
    "status": "running"
  }
}
```

### 8.3 Lock storage failure
HTTP:
- `500 Internal Server Error`

Body:
```json
{
  "error": "maintenance_lock_io_failure",
  "message": "Failed to create or persist maintenance lock."
}
```

### 8.4 Unauthorized maintenance action
HTTP:
- `403 Forbidden`

Body:
```json
{
  "error": "maintenance_admin_auth_required",
  "message": "Admin authentication is required for maintenance actions."
}
```

## 9. Idempotency and duplicate submits
- Accept `Idempotency-Key` header on maintenance endpoints.
- Bind key to `(action, target set, requestedBy.subject)`.
- If identical in-flight request exists, return current job state instead of starting a new one.
- If completed recently, return prior result reference.

## 10. Observability and audit
Emit structured events:
- `maintenance_lock_acquired`
- `maintenance_lock_heartbeat`
- `maintenance_lock_release`
- `maintenance_lock_conflict`
- `maintenance_lock_stale_detected`
- `maintenance_lock_stale_recovered`

Each event should include:
- `timestamp`, `lockId`, `jobId`, `action`, `owner`, `requestedBy.subject`, `traceId`

## 11. Security requirements
- Lock file must be writable only by resolver runtime user/group.
- Never include secrets or credential material in lock payload.
- If source IP is unavailable, store `-` (do not infer).

## 12. CLI alignment
CLI destructive flows should reuse same lock contract:
- `resolver.cli --apply`
- `resolver.cli --force-compat-*`
- cleanup operations

CLI should expose:
- `--lock-timeout-seconds`
- `--lock-heartbeat-seconds`
- `--lock-disable` (debug only, disabled by default in production builds)

## 13. Test cases (minimum)
- Acquire/release success path.
- Concurrent acquire returns `409`.
- Stale lock recovered by admin role caller.
- Non-admin stale recovery rejected.
- Foundry online precondition returns `412`.
- Crash simulation leaves stale lock and next run recovers.
