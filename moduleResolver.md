# Foundry VTT Module Version Resolver

## 📌 Problem Description

In Foundry VTT, each module declares compatibility with core versions using the `compatibility` object in its manifest (`module.json`), which includes:

- `minimum`: lowest supported Foundry version (hard restriction)
- `verified`: highest version tested (soft guidance)
- `maximum`: highest supported version (hard restriction)

The Foundry core enforces `minimum` and `maximum` strictly, preventing installation outside that range, while `verified` only provides guidance and does not block usage.

The default package management system is designed to:
- install the latest version
- check compatibility at update time

However, it does **not solve this problem**:

> Given a fixed Foundry version (e.g. 13.x), determine the best historical version of each module that is most compatible with that version.

This becomes an issue when:
- the user stays on an older major version (e.g. 13.x)
- module authors release versions targeting newer Foundry versions (e.g. 14.x)
- updating modules pulls incompatible or suboptimal versions

---

## 🎯 Objective

Build an external tool that:

- Takes only the Foundry data root as input
- Analyzes installed modules
- Inspects their release history
- Detects the installed Foundry version automatically from the local data directory
- Determines the **best compatible version** for each module

The tool must prioritize compatibility over recency.

The tool must also support an upgrade decision view focused on future migration planning:

- determine which future stable Foundry version is the best migration target
- determine which future system version should be paired with that Foundry version
- consider only modules that are actually used in one or more worlds
- help the operator decide the best moment to upgrade

---

## ⚠️ Constraints

- Compatibility metadata may be incomplete or inaccurate
- Not all modules expose full release history consistently
- `verified` is advisory, not authoritative
- Some modules only expose a “latest” manifest
- The operator must not need to pass the Foundry version manually
- The tool must be able to infer the Foundry version using only the Foundry data root on disk
- The tool must support execution logging
- The tool must support a `dry-run` mode
- Future upgrade analysis must ignore modules that are installed locally but not enabled in any world
- Future upgrade analysis must ignore modules whose recommendation can only be derived from a local manifest
- Only stable future Foundry releases should be considered in migration planning
- Future upgrade planning must consider both future Foundry versions and future system versions together

The system must handle uncertainty and report confidence levels.

---

## 🧠 Core Logic

### Step 1 — Collect Data

Given only the Foundry data root (for example `/foundry/data`), the tool must:

- Detect the installed Foundry version automatically
- Prefer structured local metadata if available, such as:
  - `Logs/diagnostics.json`
  - other version-bearing files under the data root
- Fallback to filename or cache-based inference if needed, such as cached Foundry package archives
- Fail clearly if the version cannot be determined with enough confidence

For each installed module:

- Read local `Data/modules/*/module.json`
- Extract:
  - `id`
  - `title`
  - `version`
  - `manifest`
  - `url`

Also collect installed systems from:

- `Data/systems/*/system.json`

For each installed system, extract:

- `id`
- `version`

Also collect installed modules from:

- `Data/modules/*/module.json`

This local inventory must be used to validate inter-module relationships.

The tool must also build a local dependency map from installed modules:

- direct dependencies from each module's `relationships.requires`
- transitive dependencies derived recursively from those direct links
- cycle-safe traversal

For future upgrade planning, the tool must also collect world usage data from:

- `Data/worlds/*/world.json`
- `Data/worlds/*/data/settings`

For each world, extract at minimum:

- `id`
- `title`
- `system`
- `systemVersion`
- `coreVersion`

The tool must also identify which modules are actually enabled in each world.

Preferred source:

- the world setting that stores module enablement, typically `core.moduleConfiguration`

If module enablement cannot be determined for a world with enough confidence:

- report that world as partially unresolved
- do not silently treat all installed modules as used in that world

---

### Step 2 — Fetch Release History

Try in order:

1. Foundry package registry (if available)
2. GitHub/GitLab releases
3. Fallback: latest manifest only

Each release should provide:
- `version`
- `manifest URL`
- `compatibility` object

For future upgrade planning, the tool must also fetch:

- the catalog of future stable Foundry releases
- release history for installed systems

The future Foundry catalog must be cached locally and reused across runs.

---

## ⚙️ Compatibility Rules

For the detected Foundry version (e.g. `13.351`):

### Reject release if:

- `minimum > target`
- `maximum` exists AND `target > maximum`
- the release declares system compatibility for an installed system and that installed system version falls outside the declared range
- the release declares a required module with explicit compatibility and no satisfiable version of that required module can be resolved

System compatibility should be enforced only when the data is available:

- if the module release does not declare `relationships.systems`, ignore this rule
- if the module release declares a system but the matching installed system version cannot be determined locally, do not hard-fail on that basis alone
- if the installed system version is known, it must satisfy the declared `minimum` and `maximum` for that system

Required module compatibility should be enforced as follows:

- inspect `relationships.requires` entries where `type` is `module`
- if a required module is installed, validate whether the installed version satisfies the declared compatibility
- if the installed version does not satisfy the declared compatibility, the tool must compute a recommended version for that dependency as well
- if the required module is not installed but enough metadata exists to resolve it, include it as an additional required update/install recommendation
- if a required module cannot be resolved at all, reduce confidence and report the unresolved dependency
- dependency resolution must be recursive, because a recommended dependency may itself require other modules
- when reporting dependency actions, separate them into:
  - dependency updates
  - missing dependencies that must be installed or resolved

For future upgrade planning:

- evaluate compatibility against a target Foundry version and a target system version together
- a module counts as future-compatible only if:
  - the module release is compatible with the target Foundry version
  - the module release is compatible with the target system version when system compatibility is declared
  - required module dependencies can be satisfied without rollback
- modules with only `local-manifest` evidence must be excluded from future upgrade scoring

## ⚡ Performance Requirement

When processing many modules, the tool must:

- read local modules once
- build the local dependency map once
- process recommendations in batches
- use batches of at least 10 modules each to avoid excessive total runtime
- use local HTTP cache for remote release metadata and manifests
- inspect recent releases first and expand progressively only when needed, for example `10 -> 20 -> 30`
- reuse cached remote responses across runs whenever possible
- apply retention limits to cache size, file count, and file age so the cache cannot grow without bound

## 🗃️ Local Graph Catalog

The tool must persist a normalized local catalog so recommendations do not depend only on ephemeral JSON caches.

This local catalog should:

- use a local SQLite database
- remain portable across Linux, macOS, and Windows
- store both the release catalog and the installed environment snapshot
- model compatibility as graph-like relationships even if stored relationally

At minimum, the persisted model must capture:

- installed Foundry scan runs
- installed systems
- installed modules
- worlds and world-enabled modules
- module releases
- system releases
- Foundry compatibility edges for each release
- system compatibility edges for each release when declared
- dependency edges from `relationships.requires`

The local database should make it easy to answer:

- which module releases are compatible with a given Foundry version
- which module releases are compatible with a given system version
- which dependency chains block a target upgrade
- which worlds are affected by a blocker

The database must be treated as a normalized local catalog, not as the raw HTTP cache itself.

The database must also have explicit retention controls so local storage cannot grow without bound:

- keep only the latest scan snapshots, with a configurable default retention window
- remove catalog releases that are no longer referenced by the retained scan window
- vacuum or otherwise compact the SQLite file after cleanup when space can be reclaimed
- surface database size and retention status in the generated report

## 🔐 Remote Access

To reduce GitHub API rate limits, the tool must:

- support authentication via `GITHUB_TOKEN`
- send that token on GitHub API requests when available
- continue to work without the token, but with reduced confidence if rate-limited

## ⛔ Rollback Policy

For dependency recommendations:

- the tool must not suggest rollbacks
- if a dependency recommendation is lower than the installed version, do not emit it as an update action
- if a selected module release can only be satisfied via dependency rollback, report the dependency as unresolved instead of suggesting downgrade
- if a selected module release would require dependency rollback, that release must lose priority versus older releases of the same module
- the resolver must continue evaluating lower releases of the module itself until it finds the newest release that works without dependency downgrade
- in practice, the preferred outcome is always the latest adequate version of the module itself, even if the latest overall release is rejected due to dependency rollback

## 🚀 Apply Mode

The tool may support an apply mode that:

- downloads the recommended module package archive
- replaces the installed module contents on disk
- creates a backup before replacing the installed files
- should be used only while Foundry is stopped

## 🧭 Future Upgrade Decision View

The report must include a dedicated decision-oriented view for future upgrades.

This should be implemented as a single tab or section whose purpose is:

- to recommend the best future upgrade target
- to show the safest current upgrade path
- to show the latest reachable stable upgrade
- to explain blockers when a target is not ready

This view must treat Foundry version and system version as a single upgrade decision, not as separate independent decisions.

### Scope of this view

Only include:

- future stable Foundry releases
- systems that are installed locally and used by at least one world
- modules that are enabled in at least one world

Exclude:

- installed modules that are not used by any world
- modules whose future recommendation is based only on local manifest fallback

### Decision matrix

For each candidate future stable Foundry release:

1. determine the best compatible future version of each installed system used by the worlds
2. evaluate all world-enabled modules against that Foundry + system combination
3. include dependency validation
4. compute coverage and blockers

Each candidate row should expose at least:

- target Foundry version
- target system id
- target system version
- worlds affected
- used modules analyzed
- modules already compatible
- modules upgradable
- modules blocked
- coverage percentage
- recommendation label

### Ranking logic

The best future migration target must not simply be the newest Foundry release.

It must be ranked by:

1. highest coverage of world-used modules
2. fewest blocked modules
3. fewest unresolved dependency chains
4. newest stable Foundry version as tie-breaker

This view should highlight at minimum:

- `Best Upgrade Now`
- `Safest Upgrade`
- `Latest Reachable Stable`

### Output expectations

The future upgrade decision data should be represented in the unified result payload with fields such as:

- `futureStableFoundryReleases`
- `worldUsage`
- `usedWorldModules`
- `futureSystemRecommendations`
- `futureUpgradeMatrix`
- `bestFutureUpgradeTarget`

The HTML report should present this as a decision aid rather than as a raw compatibility dump.

The operator should be able to answer:

- which stable Foundry version should I target next?
- which system version should I pair with it?
- which used modules are ready, upgradable, or blocking that move?

---

### Rank valid releases:

Priority:

1. `verified` matches target major (e.g. 13.x)
2. `verified` closest to target version
3. newest semantic version

---

### Example scoring heuristic:

- +1000 → `verified` in same major
- +200 → `verified <= target`
- -9999 → blocked by compatibility rules
- +version weight → newer preferred

---

## 📊 Output Format

For each module:

```json
{
  "module": "module-name",
  "installedVersion": "1.2.3",
  "recommendedVersion": "1.1.9",
  "reason": "Last version verified for Foundry 13",
  "confidence": "high | medium | low",
  "manifestUrl": "https://..."
}
```

The overall execution should also expose:

- detected Foundry version
- detection source
- warnings encountered during collection
- whether execution was `dry-run`
- path to the generated log file, when enabled
- dependency update/install actions needed to satisfy the recommendation

## 📝 Logging

The tool must:

- Emit human-readable logs to stdout
- Optionally write logs to a file
- Record version detection source, remote lookup attempts, fallbacks, and final recommendation decisions

## 🧪 Dry Run

The tool must support a `dry-run` mode that:

- Performs all reads, lookups, scoring, and recommendation generation
- Does not modify installed modules
- Clearly marks the result as a simulation
