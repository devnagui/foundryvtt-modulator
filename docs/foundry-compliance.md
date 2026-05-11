# Foundry Compliance Notes

Last reviewed: 2026-05-11
Scope: This project interacts with Foundry VTT user data and package manifests.  
This is an engineering compliance guide, not legal advice.

## Primary References

- Foundry Software License (EULA): https://foundryvtt.com/article/license/
- Foundry Licensed Content Guide: https://foundryvtt.com/article/licensing-guide/
- Foundry Terms of Service: https://foundryvtt.com/article/terms-of-service/

## What this project does (compliance-safe intent)

- Reads local Foundry `Data/` content (modules, systems, worlds) from the user's own installation.
- Suggests compatible module/system versions.
- Performs maintenance actions only after explicit user intent.
- Creates backups before mutating module content.

## Guardrails we enforce

- No redistribution of Foundry core binaries or license keys.
- No automatic installation without user-triggered actions.
- No bundling third-party premium assets in this repository.
- Maintenance workflows can require Foundry to be offline.
- Audit trail for auth and maintenance actions.

## Packaging and distribution rules for this repository

- Distribute only this tool's source/binaries, never Foundry software itself.
- If shipping examples, use only assets with clear redistribution rights.
- Keep third-party module downloads runtime-only and user-initiated.
- Preserve upstream license/attribution notices where required.

## Open compliance risks to review per release

- Verify that package metadata ingestion does not republish protected content.
- Verify that docs/screenshots do not expose paid module assets.
- Re-check Foundry license updates before major releases.
- Re-check third-party module licenses if cached artifacts are retained.

## Operator checklist (release gate)

- [ ] No Foundry binaries included in release artifacts
- [ ] No Foundry license key handling in logs/config samples
- [ ] No proprietary module/system content committed to git
- [ ] NOTICE/LICENSE files present and accurate
- [ ] Compliance notes reviewed against current Foundry pages

## Engineering Closeout (Current State)

- FastAPI backend is the default runtime path for UI/API.
- CLI remains available for local/offline scripted operations (`python -m resolver.cli`).
- Rollback now supports automated restoration by batch (`scanRunId`) through API action.
- Packaging scripts target FastAPI runtime (`uvicorn`) across Linux/Windows/macOS.
- CI installs backend runtime dependencies and runs integration tests including FastAPI routes.

Pending manual/legal sign-off:

- Final legal review against latest Foundry license pages before public release.
- Confirm no documentation screenshots include paid/proprietary assets.
