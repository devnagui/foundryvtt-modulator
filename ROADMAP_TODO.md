# Roadmap TODO

## Completed

<details><summary>Priority 0 (done)</summary>

- [x] Fix readiness percentage calculation per Foundry/system version.
- [x] Fix "Maximum update depth exceeded" infinite loop in Current tab useEffect.
- [x] Wire system Update button in Planning tab (`override-from-plan` action).
- [x] UI polish: align all pills to the right, compact module status pills.
- [x] Fix Planning "Blocked & Missing" pill showing 0.
- [x] Module name as clickable link to GitHub/GitLab project page.

</details>

<details><summary>Import Resilience (done)</summary>

- [x] Per-module logging in `apply_override_from_plan`.
- [x] Enriched `progressMeta` with error/skip/applied counts.
- [x] Frontend: real-time error badges, failed items list, rate-limit advisory.
- [x] HTTP 429 detection + retry with exponential backoff.
- [x] GitHub + GitLab API token support (env + UI config).
- [x] Provider tokens API (`GET/POST/DELETE /api/v1/config/provider-tokens`).
- [x] Settings UI: token input fields (password-masked, save/clear, status badges).
- [x] Parallel batched module resolution (`ThreadPoolExecutor`).
- [x] Job cancellation (backend + frontend).
- [x] Lock TTL (30 min) + stale lock auto-cleanup on startup.

</details>

---

## Priority 1 — Export & Verified Compatibility

### 1.1 Populate `usedInWorlds` in export (frontend)
- [ ] Include `worldUsage` array in the export JSON (currently only available from API, not embedded in export).
- [ ] Add `usedInWorlds` field to Planning tab export rows (currently only in Current tab rows).
- [ ] On import of an exported plan on another machine, world data is self-contained in the file.

### 1.2 "Verified Only" filter checkbox (frontend — Current tab)
- [ ] Add a default-on checkbox filter: **"Verified versions only"**.
- [ ] When enabled, the resolver/report should only consider module versions whose `verified` Foundry version matches the installed Foundry major, AND whose `verified` dnd5e/system version matches the installed system version.
- [ ] Modules/systems that fail the verified check should show a warning badge (e.g., "unverified for F13" or "unverified for dnd5e 5.3.0").
- [ ] Checkbox state persists per session (localStorage or React state).
- [ ] This filter affects:
  - Status column: a module can be "ready" by range but "unverified" by strict check.
  - Recommendation column: show the best verified-only candidate vs. the current range-based one.
  - Update path: highlight when the installed version's verified field doesn't match the current Foundry/system.
- [ ] Visual indicator on the filter bar when "Verified Only" is active (e.g., shield icon).
