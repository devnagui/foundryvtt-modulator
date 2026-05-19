# Roadmap TODO

## Priority 0
- [x] Fix readiness percentage calculation per Foundry/system version:
  - [x] Exclude unused modules from the denominator (they inflate total and drag % down).
  - [x] Include `update` modules in the numerator alongside `ready` (formula: `(ready + update) / (total - unused)`).
  - [x] Standardize formula across Current tab, Planning tab, and backend `_compute_planner_score`.
  - [x] Validate result matches expected ~78% for 91 installed modules.
- [x] Fix "Maximum update depth exceeded" infinite loop in Current tab useEffect.
- [x] Wire system Update button in Planning tab (`override-from-plan` action).
- [x] UI polish: align all pills to the right, compact module status pills.
- [x] Fix Planning "Blocked & Missing" pill showing 0 (unused rows were skipped by `partitionCountsForPills`).
- [x] Module name as clickable link to GitHub/GitLab project page (covers both providers).
