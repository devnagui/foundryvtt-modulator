import { useEffect, useMemo, useRef, useState } from "react";
import { Header } from "../components/Header";
import { api, type FoundryRootStatus, type ModuleSourceRow, type ReportModel } from "../services/api";
import { sourceByModuleId } from "./moduleSourceResolver";
import { buildRelatedSystems } from "./systemKeying";

type ReportPageProps = { onLoggedOut: () => void };
type TabId = "current" | "planning" | "backups";
type ActionKind = "dry-run" | "apply" | "force-compat" | "cleanup-backups";
type CurrentFilter = "blocked" | "update" | "ready" | "unused";

type ModuleRow = {
  module: string;
  title: string;
  state: "blocked" | "update" | "ready";
  system: string;
  relatedSystems: string[];
  usedInWorlds: string[];
  reason: string;
  installedVersion: string;
  recommendedVersion: string;
  releaseUrl: string;
  compatibility: Record<string, unknown>;
  systemCompatibility: Record<string, unknown>;
  hasMissingDependencies: boolean;
};
type CurrentTableRow =
  | { kind: "module"; key: string; row: ModuleRow }
  | { kind: "system"; key: string; systemId: string; usedInWorlds: string[]; installedVersion: string; targetVersion: string; targetUrl: string; status: "update" | "ready"; compatibility: Record<string, unknown> };
type PlanningFilter = "blocked" | "update" | "ready" | "unused";
type PlanningRow = ModuleRow & { targetVersion: string };
type SystemVersionBucket = {
  key: string;
  systems: string[];
  isCurrent: boolean;
  total: number;
  ready: number;
  update: number;
  blocked: number;
  missing: number;
  readinessPct: number;
};

function asArray(value: unknown): Array<Record<string, unknown>> { return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : []; }
function asString(value: unknown): string { return typeof value === "string" ? value : ""; }
function asBool(value: unknown): boolean { return Boolean(value); }
function cleanModuleId(rawId: string): string {
  const id = (rawId || "").trim();
  if (!id || id.includes("{{") || id.includes("}}")) return "";
  return id;
}
function cleanTitle(rawTitle: string, moduleId: string): string {
  const t = (rawTitle || "").trim();
  if (!t || t.includes("{{") || t.includes("}}")) return moduleId || "Unknown module";
  return t;
}
function hasMissingDependenciesSignal(reason: string, missingCount: number): boolean {
  if (missingCount > 0) return true;
  const text = (reason || "").toLowerCase();
  return text.includes("could not be resolved") || text.includes("missing dependenc");
}
function unresolvedDependencyCount(row: Record<string, unknown>): number {
  return asArray(row.dependencyActions).filter((dep) => !asString(dep.installedVersion) && !asString(dep.recommendedVersion)).length;
}
function rowPriority(state: "blocked" | "update" | "ready", hasMissingDependencies: boolean): number {
  if (hasMissingDependencies) return 0;
  if (state === "blocked") return 1;
  if (state === "update") return 2;
  return 3;
}
function normalizeModuleState<T extends ModuleRow>(row: T): T {
  const installed = asString(row.installedVersion).trim();
  const recommended = asString(row.recommendedVersion).trim();
  const hasInstalled = Boolean(installed && installed !== "-");
  const hasRecommended = Boolean(recommended && recommended !== "-");
  if (!hasInstalled) {
    return { ...row, state: hasRecommended ? "update" : "blocked" } as T;
  }
  if (row.state === "ready" && hasRecommended && recommended !== installed) {
    return { ...row, state: "update" } as T;
  }
  return row;
}
function presentationState(row: Record<string, unknown>, fallback: "blocked" | "update" | "ready"): "blocked" | "update" | "ready" {
  const raw = asString(row.presentationStatus).trim().toLowerCase();
  if (raw === "update" || raw === "ready" || raw === "blocked") return raw;
  if (raw === "missing") return "blocked";
  return fallback;
}
function presentationMissing(row: Record<string, unknown>, reason: string, missingCount: number): boolean {
  const explicit = row.hasMissingDependencies;
  if (typeof explicit === "boolean") return explicit;
  const raw = asString(row.presentationStatus).trim().toLowerCase();
  if (raw === "missing") return true;
  return hasMissingDependenciesSignal(reason, missingCount);
}
function missingDependencyLabel(reason: string): string {
  const text = String(reason || "").trim();
  const explicit = text.match(/missing[_\s-]*dependenc(?:y|ies)[^:]*:\s*([^\n]+)/i)?.[1] || "";
  const tokens = Array.from(text.matchAll(/missing_dependency:([a-z0-9._-]+)/gi)).map((m) => String(m[1] || "").trim());
  const fromExplicit = explicit
    .split(/[|,;]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  const all = Array.from(new Set([...fromExplicit, ...tokens])).filter(Boolean);
  if (all.length > 0) return `missing dependency: ${all.join(", ")}`;
  return "missing dependency: unknown";
}
function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.map((v) => v.trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}
function compareVersionDesc(a: string, b: string): number {
  const pa = a.split(/[.-]/).map((part) => Number.parseInt(part, 10));
  const pb = b.split(/[.-]/).map((part) => Number.parseInt(part, 10));
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i += 1) {
    const av = Number.isFinite(pa[i]) ? pa[i] : 0;
    const bv = Number.isFinite(pb[i]) ? pb[i] : 0;
    if (av !== bv) return bv - av;
  }
  return b.localeCompare(a);
}
function compareVersionAsc(a: string, b: string): number {
  return -compareVersionDesc(a, b);
}
function versionWithin(compat: Record<string, unknown> | undefined, target: string): boolean | null {
  if (!compat || !target) return null;
  const min = asString(compat.minimum ?? compat.min);
  const verified = asString(compat.verified);
  const max = asString(compat.maximum ?? compat.max);
  if (!min && !verified && !max) return null;
  if (min && compareVersionAsc(target, min) < 0) return false;
  if (max && compareVersionAsc(target, max) > 0) return false;
  if (verified) {
    const tm = Number.parseInt(target.split(".")[0] || "0", 10);
    const vm = Number.parseInt(verified.split(".")[0] || "0", 10);
    if (Number.isFinite(tm) && Number.isFinite(vm) && tm !== vm) return false;
  }
  return true;
}
function compatibilityRangeLabel(compat: Record<string, unknown> | undefined): string {
  const min = asString(compat?.minimum ?? compat?.min) || "-";
  const verified = asString(compat?.verified) || "-";
  const max = asString(compat?.maximum ?? compat?.max) || "-";
  return `compatible{min: ${min}, verified: ${verified}, max: ${max}}`;
}
function reasonBadges(
  _reason: string,
  compatibility: Record<string, unknown> | undefined,
  hasMissingDependencies = false,
  foundryCompatOk: boolean | null = null,
  systemCompatOk: boolean | null = null,
  systemCompatibility?: Record<string, unknown> | undefined,
  showSystemBadge = true
) {
  const badges: Array<{ icon: string; title: string; tone: "ok" | "fail" | "warn" | "neutral" }> = [];
  if (hasMissingDependencies) {
    badges.push({ icon: "??", title: "Missing dependency or unresolved dependency relationship.", tone: "warn" });
  }
  const foundryRange = compatibilityRangeLabel(compatibility);
  badges.push({
    icon: foundryCompatOk === null ? "F?" : (foundryCompatOk ? "F?" : "FX"),
    title: foundryCompatOk === null
      ? `Foundry compatibility uncertain: insufficient compatibility metadata. ${foundryRange}`
      : (foundryCompatOk ? `Foundry compatibility valid for selected target. ${foundryRange}` : `Foundry compatibility incompatible with selected target. ${foundryRange}`),
    tone: foundryCompatOk === null ? "warn" : (foundryCompatOk ? "ok" : "fail")
  });
  if (showSystemBadge) {
    const systemRange = compatibilityRangeLabel(systemCompatibility);
    badges.push({
      icon: systemCompatOk === null ? "S?" : (systemCompatOk ? "S?" : "SX"),
      title: systemCompatOk === null
        ? `System compatibility uncertain: insufficient compatibility metadata. ${systemRange}`
        : (systemCompatOk ? `System compatibility valid for selected target. ${systemRange}` : `System compatibility incompatible with selected target. ${systemRange}`),
      tone: systemCompatOk === null ? "warn" : (systemCompatOk ? "ok" : "fail")
    });
  }
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "nowrap" }}>
      {badges.map((badge, idx) => (
        <span
          key={`${badge.icon}-${idx}`}
          title={badge.title}
          aria-label={badge.title}
          style={{
            minWidth: 30,
            height: 30,
            padding: "0 10px",
            borderRadius: 8,
            border: "1px solid #334155",
            background: badge.tone === "ok" ? "#166534" : (badge.tone === "fail" ? "#7f1d1d" : (badge.tone === "warn" ? "#854d0e" : "#1f2937")),
            color: badge.tone === "ok" ? "#dcfce7" : (badge.tone === "fail" ? "#fecaca" : (badge.tone === "warn" ? "#fef3c7" : "#e5e7eb")),
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          {badge.icon}
        </span>
      ))}
    </div>
  );
}
function bestRecommendedVersion(row: Record<string, unknown>): string {
  return asString(row.recommendedVersion) || asString(row.targetVersion) || asString(row.latestVersion) || asString(row.version);
}
function bestReleaseUrl(row: Record<string, unknown>): string {
  return asString(row.releaseUrl) || asString(row.manifestUrl) || asString(row.url) || asString(row.projectUrl) || asString(row.downloadUrl);
}
function hasConcreteValue(value: string): boolean {
  const v = asString(value).trim();
  return Boolean(v && v !== "-");
}
function hasSourceUrls(source: Partial<ModuleSourceRow> | undefined): boolean {
  return Boolean(asString(source?.manifestUrl) || asString(source?.projectUrl));
}
function sourceForRow(sources: Record<string, ModuleSourceRow>, moduleId: string, title: string): Partial<ModuleSourceRow> {
  const byModule = sourceByModuleId(sources, moduleId);
  if (hasSourceUrls(byModule)) return byModule;
  const byTitle = sourceByModuleId(sources, title);
  if (hasSourceUrls(byTitle)) return byTitle;
  return byModule || byTitle || {};
}
function extractSystemTargetVersions(system: Record<string, unknown>): string[] {
  const versions = new Set<string>();
  const direct = [
    asString(system.targetVersion),
    asString(system.recommendedVersion),
    asString(system.version),
    asString(system.latestVersion)
  ];
  for (const v of direct) {
    const clean = v.trim();
    if (clean) versions.add(clean);
  }
  const candidateLists = [
    asArray(system.availableVersions),
    asArray(system.targetVersions),
    asArray(system.versions),
    asArray(system.versionCandidates)
  ];
  for (const list of candidateLists) {
    for (const item of list) {
      const value = asString(item) || asString(item.version) || asString(item.targetVersion) || asString(item.recommendedVersion) || asString(item.label) || asString(item.id);
      const clean = value.trim();
      if (clean) versions.add(clean);
    }
  }
  return Array.from(versions);
}

function paginate<T>(items: T[], page: number, size: number): { rows: T[]; page: number; totalPages: number } {
  const totalPages = Math.max(1, Math.ceil(items.length / size));
  const clamped = Math.max(1, Math.min(totalPages, page));
  const start = (clamped - 1) * size;
  return { rows: items.slice(start, start + size), page: clamped, totalPages };
}

function relativeFromNow(raw?: string): string {
  if (!raw) return "No scan yet";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return "No scan yet";
  const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function ReportPage({ onLoggedOut }: ReportPageProps) {
  const [tab, setTab] = useState<TabId>("current");
  const [model, setModel] = useState<ReportModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [firstRunRequired, setFirstRunRequired] = useState(false);
  const [job, setJob] = useState<{ id: string; progress: number; status: string } | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [actionBusy, setActionBusy] = useState(false);
  const [currentFilters, setCurrentFilters] = useState<CurrentFilter[]>([]);
  const [currentSystemFilter, setCurrentSystemFilter] = useState("");
  const [activeCurrentSystemId, setActiveCurrentSystemId] = useState("");
  const [currentVersionBySystem, setCurrentVersionBySystem] = useState<Record<string, string>>({});
  const [planningFilters, setPlanningFilters] = useState<PlanningFilter[]>([]);
  const [planningVersionFilters, setPlanningVersionFilters] = useState<string[]>([]);
  const [planningSystemFilter, setPlanningSystemFilter] = useState("all");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addModuleOpen, setAddModuleOpen] = useState(false);
  const [foundryRoot, setFoundryRoot] = useState<FoundryRootStatus | null>(null);
  const [foundryPathInput, setFoundryPathInput] = useState("");
  const [suggestInput, setSuggestInput] = useState("");
  const [suggestResult, setSuggestResult] = useState("");
  const [uiBusyMessage, setUiBusyMessage] = useState("");
  const [hydrationBusy, setHydrationBusy] = useState(false);
  const [moduleSources, setModuleSources] = useState<Record<string, ModuleSourceRow>>({});
  const [resolvedSourceByContext, setResolvedSourceByContext] = useState<Record<string, { recommendedVersion?: string; resolvedUrl?: string }>>({});
  const [resolvedSourceByModule, setResolvedSourceByModule] = useState<Record<string, { recommendedVersion?: string; resolvedUrl?: string }>>({});
  const hydrationRunRef = useRef(0);
  const foundryConfigured = Boolean(foundryRoot?.valid);
  const [clockTick, setClockTick] = useState(0);
  const showSearch = tab === "current" || tab === "planning" || tab === "backups";

  useEffect(() => {
    const timer = window.setInterval(() => setClockTick((v) => v + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (tab === "backups") {
      setSearch("");
    }
  }, [tab]);


  const loadModel = async () => {
    setUiBusyMessage("Loading report...");
    setLoading(true);
    setError("");
    try {
      const payload = await api.reportV3Model();
      setModel(payload);
      setFirstRunRequired(false);
    } catch (err) {
      const e = err as Error & { status?: number };
      if (e.status === 401) { onLoggedOut(); return; }
      if (e.status === 404) { setFirstRunRequired(true); setModel(null); return; }
      setError(e.message || "Failed to load report data.");
    } finally { setLoading(false); setUiBusyMessage(""); }
  };

  const loadFoundryConfig = async () => {
    try {
      const status = await api.foundryRootStatus();
      setFoundryRoot(status);
      setFoundryPathInput(status.selected || "");
    } catch {
      setFoundryRoot(null);
    }
  };

  const loadModuleSources = async () => {
    try {
      const payload = await api.moduleSources();
      const incoming = payload.sources || {};
      const normalized: Record<string, ModuleSourceRow> = {};
      for (const [key, value] of Object.entries(incoming)) {
        const clean = String(key || "").trim();
        if (!clean) continue;
        normalized[clean] = value;
        normalized[clean.toLowerCase()] = value;
      }
      setModuleSources(normalized);
    } catch {
      setModuleSources({});
    }
  };

  useEffect(() => { void loadModel(); void loadFoundryConfig(); void loadModuleSources(); }, []);

  const logout = async () => { await api.logout(); onLoggedOut(); };

  const submitAndWatch = async (action: ActionKind, payload: Record<string, unknown>) => {
    setActionBusy(true);
    setUiBusyMessage("Processing action...");
    setError("");
    try {
      const submitted = await api.submitAction(action, payload);
      setJob({ id: submitted.jobId, progress: 0, status: submitted.status });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed to start.");
      setActionBusy(false);
      setUiBusyMessage("");
    }
  };

  useEffect(() => {
    if (!job?.id) return;
    let cancelled = false;
    const run = async () => {
      while (!cancelled) {
        const status = await api.jobStatus(job.id);
        if (cancelled) return;
        setJob({ id: job.id, progress: Number(status.progress || 0), status: status.status });
        if (status.status === "success") { await loadModel(); setJob(null); setActionBusy(false); setUiBusyMessage(""); return; }
        if (status.status === "failed") { setError(status.error || "Action failed."); setActionBusy(false); setUiBusyMessage(""); return; }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [job?.id]);

  const view = model?.view || {};
  const currentSystems = asArray(view.currentSystemUpgrades?.rows);
  const planningTargets = asArray(view.systemUpgradePlanner?.targets);
  const planningSummary = (view.systemUpgradePlanner?.summary as Record<string, unknown> | undefined) || {};
  const backupRows = asArray(view.backupManagement?.rows);
  const applyHistoryRows = asArray(view.backupManagement?.applyHistory);
  const unusedRows = asArray(view.unusedModules?.rows);
  const worldUsage = asArray(model?.worldUsage);
  const currentFoundryVersion = asString(model?.targetVersion).trim();

  const moduleUsage = useMemo(() => {
    const byModule = new Map<string, { worlds: Set<string>; systems: Set<string> }>();
    for (const world of worldUsage) {
      const worldName = asString(world.alias) || asString(world.title) || asString(world.name) || asString(world.id) || "World";
      const systemName = asString(world.system) || "-";
      for (const moduleId of (world.enabledModules as string[] | undefined) || []) {
        const key = String(moduleId || "").trim();
        if (!key) continue;
        const existing = byModule.get(key) || { worlds: new Set<string>(), systems: new Set<string>() };
        existing.worlds.add(worldName);
        if (systemName) existing.systems.add(systemName);
        byModule.set(key, existing);
      }
    }
    return byModule;
  }, [worldUsage]);

  const currentRows = useMemo<ModuleRow[]>(() => {
    const rows: ModuleRow[] = [];
    const isUsedByAnyWorld = (moduleId: string): boolean => {
      const worlds = moduleUsage.get(moduleId)?.worlds;
      return Boolean(worlds && worlds.size > 0);
    };
    for (const system of currentSystems) {
      const systemId = asString(system.systemId).trim();
      const systemName = asString(system.title) || systemId || "-";
      for (const row of asArray(system.blockedModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        if (!isUsedByAnyWorld(moduleId)) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length + unresolvedDependencyCount(row);
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "blocked"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount) });
      }
      for (const row of asArray(system.upgradableModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        if (!isUsedByAnyWorld(moduleId)) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length + unresolvedDependencyCount(row);
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "update"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount) });
      }
      for (const row of asArray(system.compatibleModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        if (!isUsedByAnyWorld(moduleId)) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length + unresolvedDependencyCount(row);
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "ready"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount) });
      }
    }
    for (const item of asArray(model?.results)) {
      const moduleId = cleanModuleId(asString(item.module));
      if (!moduleId) continue;
      const compatSystems = Object.keys((item.systemCompatibility as Record<string, unknown> | undefined) || {});
      const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));

      if (rows.length === 0) {
        const installed = asString(item.installedVersion);
        const recommended = asString(item.recommendedVersion);
        const state: ModuleRow["state"] =
          recommended && installed && recommended !== installed ? "update" : "ready";
        rows.push({
          module: moduleId,
          title: cleanTitle(asString(item.title), moduleId),
          state,
          system: "-",
          relatedSystems: uniqueSorted([...usageSystems, ...compatSystems]),
          usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(),
          reason: asString(item.reason),
          installedVersion: installed,
          recommendedVersion: recommended || bestRecommendedVersion(item),
          releaseUrl: bestReleaseUrl(item),
          compatibility: (item.compatibility as Record<string, unknown> | undefined) || {},
          systemCompatibility: (item.systemCompatibility as Record<string, unknown> | undefined) || {},
          hasMissingDependencies: asArray(item.missingDependencies).length > 0
        });
      }

      const missingCandidates = [
        ...asArray(item.missingDependencies),
        ...asArray(item.dependencyActions).filter((dep) => !hasConcreteValue(asString(dep.installedVersion)))
      ];
      if (missingCandidates.length > 0) {
        const missingIds = Array.from(
          new Set(
            missingCandidates
              .map((dep) => cleanModuleId(asString(dep.module)))
              .filter(Boolean)
          )
        );
        for (const existing of rows) {
          if (existing.module === moduleId) {
            existing.hasMissingDependencies = true;
            if (missingIds.length > 0 && !existing.reason.includes("missing_dependency:")) {
              const suffix = missingIds.map((id) => `missing_dependency:${id}`).join(" ");
              existing.reason = `${existing.reason} ${suffix}`.trim();
            }
          }
        }
      }
      for (const missing of missingCandidates) {
        const depId = cleanModuleId(asString(missing.module));
        if (!depId) continue;
        const depReason = asString(missing.reason) || `Missing dependency required by ${moduleId}`;
        rows.push({
          module: depId,
          title: cleanTitle(asString(missing.title), depId),
          state: "blocked",
          system: "-",
          relatedSystems: uniqueSorted([...usageSystems, ...compatSystems]),
          usedInWorlds: Array.from((moduleUsage.get(depId)?.worlds || new Set<string>())).sort(),
          reason: depReason,
          installedVersion: asString(missing.installedVersion),
          recommendedVersion: bestRecommendedVersion(missing),
          releaseUrl: bestReleaseUrl(missing),
          compatibility: (missing.compatibility as Record<string, unknown> | undefined) || {},
          systemCompatibility: (missing.systemCompatibility as Record<string, unknown> | undefined) || {},
          hasMissingDependencies: false
        });
      }
    }

    // Ensure Current always surfaces actionable inventory by merging Unused rows.
    for (const row of unusedRows) {
      const moduleId = cleanModuleId(asString(row.module));
      if (!moduleId) continue;
      const compatibilityStatus = asString(row.compatibilityStatus).toLowerCase();
      const canUpdate = asBool(row.updateViable);
      const defaultState: ModuleRow["state"] =
        compatibilityStatus === "incompatible" ? "blocked" : (canUpdate ? "update" : "ready");
      const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
      const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
      const rowReason = asString(row.reason) || "Unused module";
      const rowMissingCount = asArray(row.missingDependencies).length;
      rows.push({
        module: moduleId,
        title: cleanTitle(asString(row.title), moduleId),
        state: presentationState(row, defaultState),
        system: "unused",
        relatedSystems: uniqueSorted([...usageSystems, ...compatSystems]),
        usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(),
        reason: rowReason,
        installedVersion: asString(row.installedVersion),
        recommendedVersion: bestRecommendedVersion(row),
        releaseUrl: bestReleaseUrl(row),
        compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
        systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {},
        hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount)
      });
    }

    const dedup = new Map<string, ModuleRow>();
    for (const raw of rows) {
      const row = normalizeModuleState(raw);
      const prev = dedup.get(row.module);
      if (!prev) {
        dedup.set(row.module, row);
        continue;
      }
      const prevPriority = rowPriority(prev.state, prev.hasMissingDependencies);
      const rowPriorityValue = rowPriority(row.state, row.hasMissingDependencies);
      const preferRow = rowPriorityValue < prevPriority
        || (prev.state === "blocked" && !prev.hasMissingDependencies && row.state === "update");
      if (preferRow) {
        row.usedInWorlds = Array.from(new Set([...prev.usedInWorlds, ...row.usedInWorlds])).sort();
        row.relatedSystems = uniqueSorted([...prev.relatedSystems, ...row.relatedSystems]);
        row.hasMissingDependencies = prev.hasMissingDependencies || row.hasMissingDependencies;
        if (!hasConcreteValue(row.installedVersion) && hasConcreteValue(prev.installedVersion)) row.installedVersion = prev.installedVersion;
        if (!hasConcreteValue(row.recommendedVersion) && hasConcreteValue(prev.recommendedVersion)) row.recommendedVersion = prev.recommendedVersion;
        if (!asString(row.releaseUrl).trim() && asString(prev.releaseUrl).trim()) row.releaseUrl = prev.releaseUrl;
        dedup.set(row.module, row);
      } else {
        prev.usedInWorlds = Array.from(new Set([...prev.usedInWorlds, ...row.usedInWorlds])).sort();
        prev.relatedSystems = uniqueSorted([...prev.relatedSystems, ...row.relatedSystems]);
        prev.hasMissingDependencies = prev.hasMissingDependencies || row.hasMissingDependencies;
        if (!hasConcreteValue(prev.installedVersion) && hasConcreteValue(row.installedVersion)) prev.installedVersion = row.installedVersion;
        if (!hasConcreteValue(prev.recommendedVersion) && hasConcreteValue(row.recommendedVersion)) prev.recommendedVersion = row.recommendedVersion;
        if (!asString(prev.releaseUrl).trim() && asString(row.releaseUrl).trim()) prev.releaseUrl = row.releaseUrl;
      }
    }
    return Array.from(dedup.values()).map(normalizeModuleState).sort((a, b) => {
      const pa = rowPriority(a.state, a.hasMissingDependencies);
      const pb = rowPriority(b.state, b.hasMissingDependencies);
      if (pa !== pb) return pa - pb;
      return a.title.localeCompare(b.title);
    });
  }, [currentSystems, model?.results, moduleUsage, unusedRows]);

  const dependencySuggestionByModule = useMemo(() => {
    const out: Record<string, { recommendedVersion?: string; releaseUrl?: string }> = {};
    for (const result of asArray(model?.results)) {
      for (const dep of asArray(result.dependencyActions)) {
        const moduleId = cleanModuleId(asString(dep.module));
        if (!moduleId) continue;
        const recommendedVersion = asString(dep.recommendedVersion).trim();
        const releaseUrl = asString(dep.releaseUrl).trim()
          || asString(dep.manifestUrl).trim()
          || asString(dep.downloadUrl).trim()
          || asString(dep.projectUrl).trim();
        if (!hasConcreteValue(recommendedVersion) && !releaseUrl) continue;
        const prev = out[moduleId];
        if (!prev) {
          out[moduleId] = {
            recommendedVersion: hasConcreteValue(recommendedVersion) ? recommendedVersion : undefined,
            releaseUrl: releaseUrl || undefined,
          };
          continue;
        }
        if (!hasConcreteValue(asString(prev.recommendedVersion)) && hasConcreteValue(recommendedVersion)) prev.recommendedVersion = recommendedVersion;
        if (!asString(prev.releaseUrl).trim() && releaseUrl) prev.releaseUrl = releaseUrl;
      }
    }
    return out;
  }, [model?.results]);

  const currentSystemVersionById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const sys of currentSystems) {
      const systemId = asString(sys.systemId).trim();
      if (!systemId) continue;
      const installedVersion = asString(sys.installedVersion).trim() || asString((model?.installedSystemVersions || {})[systemId]).trim();
      if (installedVersion) map[systemId] = installedVersion;
    }
    return map;
  }, [currentSystems, model?.installedSystemVersions]);

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const [systemId, installedVersion] of Object.entries(currentSystemVersionById)) {
      if (installedVersion) next[systemId] = installedVersion;
    }
    setCurrentVersionBySystem((prev) => ({ ...prev, ...next }));
    const first = Object.keys(currentSystemVersionById)[0] || "";
    const active = activeCurrentSystemId || first;
    if (active && next[active]) {
      setCurrentSystemFilter(next[active]);
    }
    if (!activeCurrentSystemId && first) setActiveCurrentSystemId(first);
  }, [currentSystemVersionById, activeCurrentSystemId]);

  const currentSystemVersionBuckets = useMemo<SystemVersionBucket[]>(() => {
    const systemsByVersion = new Map<string, Set<string>>();
    const register = (version: string, systemId: string) => {
      const v = version.trim();
      const s = systemId.trim();
      if (!v || !s) return;
      const bucket = systemsByVersion.get(v) || new Set<string>();
      bucket.add(s);
      systemsByVersion.set(v, bucket);
    };
    for (const [systemId, installedVersion] of Object.entries(currentSystemVersionById)) {
      register(installedVersion, systemId);
    }
    for (const system of currentSystems) {
      const systemId = asString(system.systemId).trim();
      if (!systemId || !currentSystemVersionById[systemId]) continue;
      const candidateVersions = extractSystemTargetVersions(system);
      for (const version of candidateVersions) register(version, systemId);
    }
    for (const target of planningTargets) {
      const targetFoundryVersion = asString(target.foundryVersion).trim();
      if (!targetFoundryVersion || (currentFoundryVersion && targetFoundryVersion !== currentFoundryVersion)) continue;
      const systemRows = asArray(target.systemRows).length > 0 ? asArray(target.systemRows) : asArray(target.systems);
      for (const system of systemRows) {
        const systemId = asString(system.systemId).trim();
        if (!systemId || !currentSystemVersionById[systemId]) continue;
        const targetVersions = extractSystemTargetVersions(system);
        for (const targetVersion of targetVersions) register(targetVersion, systemId);
      }
    }

    const buckets: SystemVersionBucket[] = [];
    for (const [version, systemsSet] of systemsByVersion.entries()) {
      const systems = Array.from(systemsSet);
      const isCurrent = systems.every((systemId) => currentSystemVersionById[systemId] === version);
      const rows: ModuleRow[] = [];
      if (isCurrent) {
        rows.push(...currentRows.filter((row) => row.relatedSystems.some((systemId) => systemsSet.has(systemId))));
      } else {
        for (const target of planningTargets) {
          const systemRows = asArray(target.systemRows).length > 0 ? asArray(target.systemRows) : asArray(target.systems);
          for (const system of systemRows) {
            const systemId = asString(system.systemId).trim();
            const targetVersion = asString(system.targetVersion).trim() || asString(system.recommendedVersion).trim();
            if (!systemId || !systemsSet.has(systemId) || targetVersion !== version) continue;
            const systemName = asString(system.title) || systemId;
            const pushRows = (bucketRows: Array<Record<string, unknown>>, state: ModuleRow["state"], unknown = false) => {
              for (const row of bucketRows) {
                const moduleId = cleanModuleId(asString(row.module));
                if (!moduleId) continue;
                const reason = asString(row.reason) || (unknown ? "Needs verification" : "");
                rows.push({
                  module: moduleId,
                  title: cleanTitle(asString(row.title), moduleId),
                  state,
                  system: systemName,
                  relatedSystems: [systemId],
                  usedInWorlds: [],
                  reason,
                  installedVersion: asString(row.installedVersion),
                  recommendedVersion: bestRecommendedVersion(row),
                  releaseUrl: bestReleaseUrl(row),
                  compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
                  systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {},
                  hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row))
                });
              }
            };
            pushRows(asArray(system.blockedModuleRows), "blocked");
            pushRows(asArray(system.upgradableModuleRows), "update");
            pushRows(asArray(system.compatibleModuleRows), "ready");
            pushRows(asArray(system.unknownModuleRows), "blocked", true);
          }
        }
      }
      const total = rows.length;
      const missing = rows.filter((row) => row.hasMissingDependencies).length;
      const blocked = rows.filter((row) => !row.hasMissingDependencies && row.state === "blocked").length;
      const update = rows.filter((row) => row.state === "update").length;
      const ready = rows.filter((row) => row.state === "ready" && !row.hasMissingDependencies).length;
      const readinessPct = total > 0 ? Math.round((ready / total) * 100) : 0;
      buckets.push({ key: version, systems, isCurrent, total, ready, update, blocked, missing, readinessPct });
    }
    const sorted = buckets.sort((a, b) => compareVersionAsc(a.key, b.key));
    const currentInstalled = Array.from(new Set(Object.values(currentSystemVersionById).map((v) => String(v || "").trim()).filter(Boolean)));
    if (currentInstalled.length === 0) return sorted;
    const anchor = currentInstalled.sort((a, b) => compareVersionDesc(a, b))[0];
    const anchorIndex = sorted.findIndex((bucket) => bucket.key === anchor);
    if (anchorIndex < 0) return sorted;
    const start = Math.max(0, anchorIndex - 2);
    return sorted.slice(start);
  }, [currentSystemVersionById, currentRows, currentSystems, planningTargets, currentFoundryVersion]);

  const selectedCurrentVersionBucket = useMemo(
    () => currentSystemVersionBuckets.find((bucket) => bucket.key === currentSystemFilter) || null,
    [currentSystemFilter, currentSystemVersionBuckets]
  );
  const selectedCurrentSuggestContext = useMemo(() => {
    const systemVersions: Record<string, string> = {};
    if (selectedCurrentVersionBucket) {
      for (const systemId of selectedCurrentVersionBucket.systems) {
        systemVersions[systemId] = selectedCurrentVersionBucket.key;
      }
    }
    return {
      targetFoundryVersion: currentFoundryVersion || undefined,
      installedSystemVersions: systemVersions,
    };
  }, [selectedCurrentVersionBucket, currentFoundryVersion]);
  const selectedCurrentSuggestContextKey = useMemo(() => {
    const systems = Object.entries(selectedCurrentSuggestContext.installedSystemVersions || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([id, version]) => `${id}@${version}`)
      .join("|");
    return `${selectedCurrentSuggestContext.targetFoundryVersion || ""}::${systems}`;
  }, [selectedCurrentSuggestContext]);

  useEffect(() => {
    if (tab !== "current") return;
    if (currentSystemVersionBuckets.length === 0) return;
    const hasSelected = currentSystemVersionBuckets.some((bucket) => bucket.key === currentSystemFilter);
    if (!hasSelected) {
      const fallback = activeCurrentSystemId ? currentVersionBySystem[activeCurrentSystemId] : "";
      setCurrentSystemFilter(fallback || currentSystemVersionBuckets[0].key);
    }
  }, [tab, currentSystemFilter, currentSystemVersionBuckets, activeCurrentSystemId, currentVersionBySystem]);

  const selectedCurrentRows = useMemo(() => {
    if (!selectedCurrentVersionBucket) return currentRows;
    if (selectedCurrentVersionBucket.isCurrent) {
      return currentRows.filter((row) =>
        row.relatedSystems.some((systemId) =>
          selectedCurrentVersionBucket.systems.includes(systemId) && (!activeCurrentSystemId || systemId === activeCurrentSystemId)
        )
      );
    }
    const rows: ModuleRow[] = [];
    for (const target of planningTargets) {
      const targetFoundryVersion = asString(target.foundryVersion).trim();
      if (!targetFoundryVersion || (currentFoundryVersion && targetFoundryVersion !== currentFoundryVersion)) continue;
      const systemRows = asArray(target.systemRows).length > 0 ? asArray(target.systemRows) : asArray(target.systems);
      for (const system of systemRows) {
        const systemId = asString(system.systemId).trim();
        const targetVersion = asString(system.targetVersion).trim() || asString(system.recommendedVersion).trim();
        if (!systemId || targetVersion !== selectedCurrentVersionBucket.key) continue;
        if (activeCurrentSystemId && systemId !== activeCurrentSystemId) continue;
        if (!selectedCurrentVersionBucket.systems.includes(systemId)) continue;
        const systemName = asString(system.title) || systemId;
        const pushRows = (bucketRows: Array<Record<string, unknown>>, state: ModuleRow["state"], unknown = false) => {
          for (const row of bucketRows) {
            const moduleId = cleanModuleId(asString(row.module));
            if (!moduleId) continue;
            const reason = asString(row.reason) || (unknown ? "Needs verification" : "");
            rows.push({
              module: moduleId,
              title: cleanTitle(asString(row.title), moduleId),
              state: presentationState(row, state),
              system: systemName,
              relatedSystems: [systemId],
              usedInWorlds: [],
              reason,
              installedVersion: asString(row.installedVersion),
              recommendedVersion: bestRecommendedVersion(row),
              releaseUrl: bestReleaseUrl(row),
              compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
              systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {},
              hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row))
            });
          }
        };
        pushRows(asArray(system.blockedModuleRows), "blocked");
        pushRows(asArray(system.upgradableModuleRows), "update");
        pushRows(asArray(system.compatibleModuleRows), "ready");
        pushRows(asArray(system.unknownModuleRows), "blocked", true);
      }
    }
    const sorted = rows.sort((a, b) => {
      const pa = rowPriority(a.state, a.hasMissingDependencies);
      const pb = rowPriority(b.state, b.hasMissingDependencies);
      if (pa !== pb) return pa - pb;
      return a.title.localeCompare(b.title);
    });
    if (sorted.length > 0) return sorted;
    return currentRows
      .filter((row) =>
        row.relatedSystems.some((systemId) =>
          selectedCurrentVersionBucket.systems.includes(systemId) && (!activeCurrentSystemId || systemId === activeCurrentSystemId)
        )
      )
      .sort((a, b) => {
        const pa = rowPriority(a.state, a.hasMissingDependencies);
        const pb = rowPriority(b.state, b.hasMissingDependencies);
        if (pa !== pb) return pa - pb;
        return a.title.localeCompare(b.title);
      });
  }, [selectedCurrentVersionBucket, currentRows, planningTargets, currentFoundryVersion, activeCurrentSystemId]);

  useEffect(() => {
    if (!model) return;
    const pool = [...currentRows, ...selectedCurrentRows];
    const candidates = Array.from(new Set(pool
      .filter((row) => {
        const source = sourceForRow(moduleSources, row.module, row.title);
        const hasSource = hasSourceUrls(source);
        if (!hasSource) return false;
        const moduleKey = asString(row.module).trim();
        const moduleCached = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()];
        const cacheReady = Boolean(asString(moduleCached?.recommendedVersion) || asString(moduleCached?.resolvedUrl));
        if (cacheReady) return false;
        return true;
      })
      .map((row) => row.module)))
      .filter((moduleId) => {
        const source = sourceForRow(moduleSources, moduleId, moduleId);
        if (!source || !hasSourceUrls(source)) return false;
        const moduleKey = asString(moduleId).trim();
        const moduleCached = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()];
        if (moduleCached && (asString(moduleCached.recommendedVersion) || asString(moduleCached.resolvedUrl))) return false;
        const contextKey = `${selectedCurrentSuggestContextKey}::${moduleId}`;
        if (resolvedSourceByContext[contextKey]?.recommendedVersion || resolvedSourceByContext[contextKey]?.resolvedUrl) return false;
        return true;
      });
    if (candidates.length === 0) { setHydrationBusy(false); return; }
    const runId = hydrationRunRef.current + 1;
    hydrationRunRef.current = runId;
    setHydrationBusy(true);
    void (async () => {
      const batch = candidates.map((moduleId) => {
        const source = sourceForRow(moduleSources, moduleId, moduleId);
        return {
          moduleId,
          manifestUrl: asString(source.manifestUrl),
          projectUrl: asString(source.projectUrl),
        };
      }).filter((item) => item.manifestUrl || item.projectUrl);
      if (batch.length === 0) {
        setHydrationBusy(false);
        return;
      }
      try {
        const payload = await api.suggestModulesBatch(batch, selectedCurrentSuggestContext);
        if (hydrationRunRef.current !== runId) return;
        const rows = Array.isArray(payload.rows) ? payload.rows : [];
        const updates: Record<string, { recommendedVersion?: string; resolvedUrl?: string }> = {};
        for (const row of rows) {
          const moduleId = asString(row?.moduleId);
          if (!moduleId) continue;
          const suggestion = (row?.suggestion || {}) as Record<string, unknown>;
          const recommendedVersion = asString(suggestion.recommendedVersion);
          const resolvedUrl = asString(suggestion.releaseUrl) || asString(suggestion.manifestUrl) || asString(suggestion.downloadUrl) || asString(suggestion.projectUrl);
          if (!recommendedVersion && !resolvedUrl) continue;
          const contextKey = `${selectedCurrentSuggestContextKey}::${moduleId}`;
          updates[contextKey] = {
            recommendedVersion: recommendedVersion || undefined,
            resolvedUrl: resolvedUrl || undefined,
          };
        }
        if (Object.keys(updates).length > 0) {
          setResolvedSourceByContext((prev) => ({ ...prev, ...updates }));
          const byModule: Record<string, { recommendedVersion?: string; resolvedUrl?: string }> = {};
          for (const row of rows) {
            const moduleId = asString(row?.moduleId);
            if (!moduleId) continue;
            const suggestion = (row?.suggestion || {}) as Record<string, unknown>;
            const recommendedVersion = asString(suggestion.recommendedVersion);
            const resolvedUrl = asString(suggestion.releaseUrl) || asString(suggestion.manifestUrl) || asString(suggestion.downloadUrl) || asString(suggestion.projectUrl);
            if (!recommendedVersion && !resolvedUrl) continue;
            byModule[moduleId] = {
              recommendedVersion: recommendedVersion || undefined,
              resolvedUrl: resolvedUrl || undefined,
            };
          }
          if (Object.keys(byModule).length > 0) {
            const normalized: Record<string, { recommendedVersion?: string; resolvedUrl?: string }> = {};
            for (const [k, v] of Object.entries(byModule)) {
              const key = asString(k).trim();
              if (!key) continue;
              normalized[key] = v;
              normalized[key.toLowerCase()] = v;
            }
            setResolvedSourceByModule((prev) => ({ ...prev, ...normalized }));
          }
        }
      } catch {
        // best-effort hydration; keep UI responsive
      }
      if (hydrationRunRef.current !== runId) return;
      setHydrationBusy(false);
    })();
  }, [model, moduleSources, currentRows, selectedCurrentRows, resolvedSourceByContext, resolvedSourceByModule, selectedCurrentSuggestContext, selectedCurrentSuggestContextKey]);

  const fixModules = useMemo(() => {
    const ids = new Set<string>();
    for (const row of selectedCurrentRows) {
      const source = sourceForRow(moduleSources, row.module, row.title);
      const hasSource = hasSourceUrls(source);
      if (!hasSource) continue;
      if (row.state === "update" || row.hasMissingDependencies) ids.add(row.module);
    }
    return Array.from(ids);
  }, [selectedCurrentRows, moduleSources]);

  useEffect(() => {
    if (!hydrationBusy && !actionBusy && uiBusyMessage === "Applying selected system version...") {
      setUiBusyMessage("");
    }
  }, [hydrationBusy, actionBusy, uiBusyMessage, selectedCurrentRows]);

  const filteredCurrent = useMemo(() => {
    const q = search.trim().toLowerCase();
    return selectedCurrentRows
      .filter((row) => {
        if (currentFilters.length === 0) return true;
        return currentFilters.some((filter) => {
          if (filter === "unused") return row.system === "unused";
          if (filter === "blocked") return row.state === "blocked" || row.hasMissingDependencies;
          return row.state === filter;
        });
      })
      .filter((row) => (q ? `${row.title} ${row.module} ${row.system} ${row.reason}`.toLowerCase().includes(q) : true));
  }, [selectedCurrentRows, search, currentFilters]);

  const planningRows = useMemo<PlanningRow[]>(() => {
    const rows: PlanningRow[] = [];
    for (const target of planningTargets) {
      const targetVersion = asString(target.foundryVersion);
      const systemRows = asArray(target.systemRows).length > 0 ? asArray(target.systemRows) : asArray(target.systems);
      for (const system of systemRows) {
        const systemId = asString(system.systemId).trim();
        const systemName = asString(system.title) || systemId || "-";
        const relationSystems = systemId ? [systemId] : [];
        const pushRows = (bucket: Array<Record<string, unknown>>, state: ModuleRow["state"], unknown = false) => {
          for (const row of bucket) {
            const moduleId = cleanModuleId(asString(row.module));
            if (!moduleId) continue;
            const reason = asString(row.reason) || (unknown ? "Needs verification" : "");
            rows.push({
              module: moduleId,
              title: cleanTitle(asString(row.title), moduleId),
              state,
              system: systemName,
              relatedSystems: buildRelatedSystems(systemId, relationSystems, []),
              usedInWorlds: [],
              reason,
              installedVersion: asString(row.installedVersion),
              recommendedVersion: bestRecommendedVersion(row),
              releaseUrl: bestReleaseUrl(row),
              compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
              systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {},
              hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row)),
              targetVersion
            });
          }
        };
        pushRows(asArray(system.blockedModuleRows), "blocked");
        pushRows(asArray(system.upgradableModuleRows), "update");
        pushRows(asArray(system.compatibleModuleRows), "ready");
        pushRows(asArray(system.unknownModuleRows), "blocked", true);
      }
      for (const row of asArray(target.localManifestManualModules)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        const reason = asString(row.reason) || "Unused/manual module for this target";
        rows.push({
          module: moduleId,
          title: cleanTitle(asString(row.title), moduleId),
          state: presentationState(row, "blocked"),
          system: "unused",
          relatedSystems: ["unused"],
          usedInWorlds: [],
          reason,
          installedVersion: asString(row.installedVersion),
          recommendedVersion: bestRecommendedVersion(row),
          releaseUrl: bestReleaseUrl(row),
          compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
          systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {},
          hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row)),
          targetVersion
        });
      }
    }
    return rows.map(normalizeModuleState).sort((a, b) => {
      const pa = rowPriority(a.state, a.hasMissingDependencies);
      const pb = rowPriority(b.state, b.hasMissingDependencies);
      if (pa !== pb) return pa - pb;
      return a.title.localeCompare(b.title);
    });
  }, [planningTargets]);

  const filteredPlanning = useMemo(() => {
    const q = search.trim().toLowerCase();
    return planningRows
      .filter((row) => planningVersionFilters.length === 0 || planningVersionFilters.includes(row.targetVersion))
      .filter((row) => {
        if (planningFilters.length === 0) return true;
        return planningFilters.some((filter) => {
          if (filter === "unused") return row.system === "unused";
          if (filter === "blocked") return row.state === "blocked" || row.hasMissingDependencies;
          return row.state === filter;
        });
      })
      .filter((row) => (planningSystemFilter === "all" ? true : row.relatedSystems.includes(planningSystemFilter)))
      .filter((row) => (q ? `${row.title} ${row.module} ${row.system} ${row.reason} ${row.targetVersion}`.toLowerCase().includes(q) : true));
  }, [planningRows, planningVersionFilters, planningFilters, planningSystemFilter, search]);

  const filteredBackups = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return backupRows;
    return backupRows.filter((row) =>
      `${asString(row.title)} ${asString(row.module)} ${String(row.backupCount || "")}`.toLowerCase().includes(q)
    );
  }, [backupRows, search]);

  const currentTableRows = useMemo<CurrentTableRow[]>(() => {
    const systemRows: Extract<CurrentTableRow, { kind: "system" }>[] = [];
    if (selectedCurrentVersionBucket) {
      for (const systemId of selectedCurrentVersionBucket.systems) {
        const sys = currentSystems.find((entry) => asString(entry.systemId) === systemId);
        if (!sys) continue;
        const installedVersion = asString(sys.installedVersion) || asString((model?.installedSystemVersions || {})[systemId]);
        const targetVersion = selectedCurrentVersionBucket.key;
        const targetUrl = asString(sys.targetUrl) || asString(sys.releaseUrl) || asString(sys.manifestUrl);
        systemRows.push({
          kind: "system",
          key: `system-${systemId}-${selectedCurrentVersionBucket.key}`,
          systemId,
          usedInWorlds: [],
          installedVersion: installedVersion || "-",
          targetVersion: targetVersion || "-",
          targetUrl,
          status: selectedCurrentVersionBucket.isCurrent || targetVersion === installedVersion ? "ready" : "update",
          compatibility: (sys.compatibility as Record<string, unknown> | undefined) || {}
        });
      }
      systemRows.sort((a, b) => a.systemId.localeCompare(b.systemId));
    }
    const moduleRows: Extract<CurrentTableRow, { kind: "module" }>[] = filteredCurrent.map((row) => ({
      kind: "module" as const,
      key: `${row.module}-${row.system}`,
      row
    }));
    return [...systemRows, ...moduleRows];
  }, [filteredCurrent, selectedCurrentVersionBucket, currentSystems, model?.installedSystemVersions]);

  const currentPage = paginate(currentTableRows, page, 12);
  const backupModules = backupRows.map((row) => asString(row.module)).filter(Boolean);
  const planningSystemIds = useMemo(() => uniqueSorted(planningRows.flatMap((row) => row.relatedSystems)), [planningRows]);
  const planningPage = paginate(filteredPlanning, page, 12);

  const applyFoundryPath = async () => {
    try {
      setUiBusyMessage("Saving Foundry path...");
      const payload = await api.setFoundryRoot(foundryPathInput);
      setFoundryRoot(payload);
      setSettingsOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save path.");
    } finally {
      setUiBusyMessage("");
    }
  };

  const pickFoundryPath = async () => {
    try {
      setUiBusyMessage("Selecting folder...");
      const payload = await api.pickFoundryRoot();
      setFoundryRoot(payload);
      setFoundryPathInput(payload.selected || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not pick folder.");
    } finally {
      setUiBusyMessage("");
    }
  };

  const resetFoundryPath = async () => {
    try {
      setUiBusyMessage("Resetting path...");
      const payload = await api.resetFoundryRoot();
      setFoundryRoot(payload);
      setFoundryPathInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset folder.");
    } finally {
      setUiBusyMessage("");
    }
  };

  const suggestModule = async () => {
    try {
      setUiBusyMessage("Resolving module...");
      setSuggestResult("Resolving best version...");
      const payload = await api.suggestModule(suggestInput);
      const s = payload.suggestion || {};
      setSuggestResult(`Recommended: ${String(s.recommendedVersion || "-")} | Compatible: ${String(Boolean(s.isCompatible))} | Checked: ${String(s.checkedReleases || 0)}`);
    } catch (err) {
      setSuggestResult(err instanceof Error ? err.message : "Suggestion failed.");
    } finally {
      setUiBusyMessage("");
    }
  };

  const findSourceForModule = (moduleId: string, title: string) => {
    const q = encodeURIComponent(`${title || moduleId} ${moduleId} foundry module manifest github gitlab`);
    window.open(`https://www.google.com/search?q=${q}`, "_blank", "noopener,noreferrer");
  };

  const setModuleSource = async (moduleId: string) => {
    const raw = window.prompt(`Paste manifest URL for ${moduleId}`);
    if (!raw) return;
    const sourceInput = raw.trim();
    if (!sourceInput) return;
    const lower = sourceInput.toLowerCase();
    const looksLikeManifest = lower.endsWith("/module.json") || lower.endsWith("/system.json") || lower.endsWith("/manifest.json");
    const manifestUrl = looksLikeManifest ? sourceInput : "";
    const projectUrl = looksLikeManifest ? "" : sourceInput;
    try {
      setUiBusyMessage(`Resolving source for ${moduleId}...`);
      const saved = await api.saveModuleSource(moduleId, manifestUrl, projectUrl);
      setSuggestResult(`Saved source for ${moduleId}. Recommended: ${String(saved.suggestion?.recommendedVersion || "-")}`);
      const contextualInput = manifestUrl || projectUrl;
      const contextual = await api.suggestModule(contextualInput, selectedCurrentSuggestContext, moduleId);
      const suggestion = ((contextual.suggestion || saved.suggestion || {}) as Record<string, unknown>);
      const recommendedVersion = asString(suggestion.recommendedVersion);
      const resolvedUrl = asString(suggestion.releaseUrl) || asString(suggestion.manifestUrl) || asString(suggestion.downloadUrl) || asString(suggestion.projectUrl);
      if (recommendedVersion || resolvedUrl) {
        const contextKey = `${selectedCurrentSuggestContextKey}::${moduleId}`;
        setResolvedSourceByContext((prev) => ({
          ...prev,
          [contextKey]: { recommendedVersion: recommendedVersion || undefined, resolvedUrl: resolvedUrl || undefined },
        }));
        setResolvedSourceByModule((prev) => ({
          ...prev,
          [moduleId]: { recommendedVersion: recommendedVersion || undefined, resolvedUrl: resolvedUrl || undefined },
          [moduleId.toLowerCase()]: { recommendedVersion: recommendedVersion || undefined, resolvedUrl: resolvedUrl || undefined },
        }));
      }
      await loadModuleSources();
      await loadModel();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save source.");
    } finally {
      setUiBusyMessage("");
    }
  };

  return (
    <main className="dashboard-shell">
      <Header
        onLogout={logout}
        onOpenSettings={() => setSettingsOpen(true)}
        settingsState={foundryConfigured ? "ok" : "warn"}
        onStartScan={() => void submitAndWatch("dry-run", { batchSize: 10 })}
        scanDisabled={Boolean(job) || actionBusy || !foundryConfigured}
        scanAttention={foundryConfigured && firstRunRequired && !job}
      />
      <section className="panel" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, alignItems: "center", justifyContent: "space-between" }}>
          <p style={{ margin: 0, color: "var(--muted)" }}>Last scan: {relativeFromNow(model?.generatedAt)} {clockTick < 0 ? "" : ""}</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", marginLeft: "auto" }}>
            <button className={`btn tab-btn tab-current ${tab === "current" ? "active" : ""}`} onClick={() => { setTab("current"); setPage(1); }}>{currentFoundryVersion ? `Current (${currentFoundryVersion})` : "Current"}</button>
            <button className={`btn tab-btn tab-planning ${tab === "planning" ? "active" : ""}`} onClick={() => { setTab("planning"); setPage(1); }}>Planning</button>
            <button className={`btn tab-btn tab-backups ${tab === "backups" ? "active" : ""}`} onClick={() => { setTab("backups"); setPage(1); }}>Backups</button>
          </div>
        </div>
      </section>

      {loading ? <section className="panel"><p>Loading data...</p></section> : null}
      {error ? <section className="panel"><p className="error">{error}</p></section> : null}

      {job ? (
        <section className="panel" style={{ marginBottom: 12 }}>
          <p>Job: {job.status}</p>
          <div style={{ height: 10, borderRadius: 999, background: "#1f2937" }}><div style={{ height: 10, borderRadius: 999, width: `${Math.max(0, Math.min(100, job.progress))}%`, background: "#22c55e", transition: "width .2s" }} /></div>
          <p>{job.progress}%</p>
        </section>
      ) : null}

      {firstRunRequired && !foundryConfigured ? (
        <section className="panel">
          <p className="error">Configure Foundry Data Root using the gear button to enable Start Scan.</p>
        </section>
      ) : null}

      {!firstRunRequired && model ? (
        <section className="panel-grid">
          {tab === "current" ? (
            <article className="panel">
              <h3>{currentFoundryVersion ? `Current (${currentFoundryVersion})` : "Current"}</h3>
              <fieldset className="filters-fieldset">
                <legend>Filters</legend>
                <div className="version-pill-row">
                  {Object.keys(currentSystemVersionById).sort((a, b) => a.localeCompare(b)).map((systemId) => {
                    const header = systemId;
                    const installed = currentSystemVersionById[systemId] || "";
                    const options = currentSystemVersionBuckets
                      .filter((bucket) => bucket.systems.includes(systemId))
                      .map((bucket) => bucket.key)
                      .sort((a, b) => compareVersionAsc(a, b));
                    const anchorIndex = options.findIndex((v) => v === installed);
                    const visibleOptions = anchorIndex < 0 ? options : options.slice(Math.max(0, anchorIndex - 2));
                    const value = currentVersionBySystem[systemId] || visibleOptions[0] || "";
                    const isActive = activeCurrentSystemId === systemId;
                    return (
                      <div
                        key={`sys-pill-${systemId}`}
                        className="metric-card compact version-pill static"
                        style={isActive ? { borderColor: "#fbbf24", boxShadow: "0 0 0 2px rgba(251,191,36,0.28) inset", background: "#0b1f35", color: "#e5e7eb" } : { borderColor: "#334155", background: "#1f2937", color: "#e5e7eb" }}
                        onClick={() => { setActiveCurrentSystemId(systemId); setCurrentSystemFilter(value); setPage(1); }}
                      >
                        <span>{header}</span>
                        <select
                          value={value}
                          onChange={(event) => {
                            const v = event.target.value;
                            setUiBusyMessage("Applying selected system version...");
                            setCurrentVersionBySystem((prev) => ({ ...prev, [systemId]: v }));
                            setActiveCurrentSystemId(systemId);
                            setCurrentSystemFilter(v);
                            setPage(1);
                          }}
                          style={{ borderRadius: 8, border: "1px solid #334155", background: "#0f172a", color: "#e5e7eb", padding: "4px 8px", width: "100%" }}
                        >
                          {visibleOptions.map((v) => (
                            <option key={`${systemId}-${v}`} value={v}>{v}{v === installed ? " (Current)" : ""}</option>
                          ))}
                        </select>
                        {isActive ? (
                          <small style={{ color: "#94a3b8", fontSize: 11 }}>
                            Ready: {selectedCurrentVersionBucket?.ready || 0} / {selectedCurrentVersionBucket?.total || 0} / Missing: {selectedCurrentVersionBucket?.missing || 0}
                          </small>
                        ) : <small style={{ color: "#94a3b8", fontSize: 11 }}>Ready: - / - / Missing: -</small>}
                      </div>
                    );
                  })}
                </div>
                <div className="metrics-row compact" style={{ marginBottom: 0 }}>
                  <button className={`metric-card metric-blocked compact ${currentFilters.includes("blocked") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("blocked") ? arr.filter((x) => x !== "blocked") : [...arr, "blocked"]); }}><span>Blocked & Missing</span><strong>{selectedCurrentRows.filter((x) => x.state === "blocked" || x.hasMissingDependencies).length}</strong></button>
                  <button className={`metric-card metric-upgrade compact ${currentFilters.includes("update") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("update") ? arr.filter((x) => x !== "update") : [...arr, "update"]); }}><span>Update</span><strong>{selectedCurrentRows.filter((x) => x.state === "update").length}</strong></button>
                  <button className={`metric-card metric-ready compact ${currentFilters.includes("ready") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("ready") ? arr.filter((x) => x !== "ready") : [...arr, "ready"]); }}><span>Ready</span><strong>{selectedCurrentRows.filter((x) => x.state === "ready").length}</strong></button>
                  <button className={`metric-card metric-unused compact ${currentFilters.includes("unused") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("unused") ? arr.filter((x) => x !== "unused") : [...arr, "unused"]); }}><span>Unused</span><strong>{selectedCurrentRows.filter((x) => x.system === "unused").length}</strong></button>
                </div>
              </fieldset>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, justifyContent: "flex-end" }}>
                <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} onClick={() => setAddModuleOpen(true)}>
                  <span className="icon-wrap" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="5" x2="12" y2="19" />
                      <line x1="5" y1="12" x2="19" y2="12" />
                    </svg>
                  </span>
                  <span>Add Module</span>
                </button>
              </div>
              <table className="report-table"><thead><tr><th><input type="search" placeholder="Name Search" value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></th><th>Update Path</th><th>Status</th><th style={{ textAlign: "right" }}><button className="btn secondary btn-xs" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured || fixModules.length === 0} onClick={() => void submitAndWatch("apply", { modules: fixModules, batchSize: 10 })}>Update All ({fixModules.length})</button></th></tr></thead><tbody>
                {currentPage.rows.map((item) => item.kind === "system"
                  ? <tr key={item.key}><td>{item.systemId} <small>(system)</small></td><td>{item.status === "ready" ? (item.installedVersion || "-") : <>{(item.installedVersion || "-")} {" \u2192 "} {item.targetUrl ? <a href={item.targetUrl} target="_blank" rel="noreferrer">{(item.targetVersion || "-")}</a> : (item.targetVersion || "-")}</>}</td><td>{reasonBadges(item.status === "update" ? "Update suggested for this system." : "No system update required.", item.compatibility, false, true, null, undefined, false)}</td><td style={{ textAlign: "right" }}>{item.status === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button> : <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>}</td></tr>
                  : (() => { const moduleKey = asString(item.row.module).trim(); const titleKey = asString(item.row.title).trim(); const source = sourceForRow(moduleSources, moduleKey, titleKey); const depSuggestion = dependencySuggestionByModule[moduleKey] || dependencySuggestionByModule[moduleKey.toLowerCase()] || dependencySuggestionByModule[titleKey] || dependencySuggestionByModule[titleKey.toLowerCase()] || {}; const moduleSuggestion = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()] || resolvedSourceByModule[titleKey] || resolvedSourceByModule[titleKey.toLowerCase()] || {}; const contextualRecommended = item.row.recommendedVersion || "-"; const contextualUrl = item.row.releaseUrl || ""; const contextKey = `${selectedCurrentSuggestContextKey}::${moduleKey}`; const hydratedRecommended = resolvedSourceByContext[contextKey]?.recommendedVersion || ""; const hydratedUrl = resolvedSourceByContext[contextKey]?.resolvedUrl || ""; const dependencyRecommended = asString(depSuggestion.recommendedVersion); const dependencyUrl = asString(depSuggestion.releaseUrl); const moduleRecommended = asString(moduleSuggestion.recommendedVersion); const moduleUrl = asString(moduleSuggestion.resolvedUrl); const rawRecommended = (contextualRecommended && contextualRecommended !== "-") ? contextualRecommended : (hydratedRecommended || moduleRecommended || dependencyRecommended || ""); const effectiveRecommended = rawRecommended && rawRecommended !== "-" ? rawRecommended : ""; const effectiveUrl = contextualUrl || hydratedUrl || moduleUrl || dependencyUrl || asString(source.manifestUrl) || asString(source.projectUrl) || ""; const unresolvedPath = !effectiveUrl && !effectiveRecommended; const pendingResolve = unresolvedPath && hydrationBusy && hasSourceUrls(source); const activeSystem = activeCurrentSystemId || item.row.relatedSystems[0] || ""; const systemTarget = selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || ""; const sysCompat = ((item.row.systemCompatibility as Record<string, unknown> | undefined) || {})[activeSystem] as Record<string, unknown> | undefined; const foundryOk = versionWithin(item.row.compatibility, currentFoundryVersion); const systemOk = versionWithin(sysCompat, systemTarget); const hasInstalled = Boolean(asString(item.row.installedVersion).trim() && asString(item.row.installedVersion).trim() !== "-"); return <tr key={item.key}><td>{item.row.hasMissingDependencies ? <span title={missingDependencyLabel(item.row.reason)} style={{ color: "#fbbf24", fontWeight: 800, marginRight: 6 }}>!</span> : null}<span title={item.row.module || "unknown"}>{(item.row.title || "Unknown module")}</span></td><td>{item.row.state === "ready" && !item.row.hasMissingDependencies ? (item.row.installedVersion || "-") : (unresolvedPath ? (pendingResolve ? "Loading..." : "?") : <>{(item.row.installedVersion || "-")} {" \u2192 "} {effectiveUrl ? <a href={effectiveUrl} target="_blank" rel="noreferrer">{effectiveRecommended || "?"}</a> : (effectiveRecommended || "?")}</>)}</td><td>{reasonBadges(item.row.reason || "", item.row.compatibility, item.row.hasMissingDependencies, foundryOk, systemOk, sysCompat, true)}</td><td style={{ textAlign: "right" }}><div style={{ display: "inline-flex", gap: 6, flexWrap: "nowrap" }}>{(item.row.hasMissingDependencies || item.row.state === "blocked") && !effectiveUrl && !effectiveRecommended ? <><button className="btn" style={{ background: "#f59e0b", color: "#111827", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 36, padding: 0 }} title="Find Module" aria-label="Find Module" onClick={() => findSourceForModule(item.row.module, item.row.title)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></button><button className="btn secondary" onClick={() => void setModuleSource(item.row.module)}>Set URL</button></> : !hasInstalled && (effectiveUrl || effectiveRecommended) ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Install</button> : item.row.state === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Update</button> : item.row.state === "blocked" ? <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled>Blocked</button> : <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>}</div></td></tr>; })())}
              </tbody></table>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}><button className="btn secondary" onClick={() => setPage((p) => Math.max(1, p - 1))}>Prev</button><span style={{ alignSelf: "center" }}>{currentPage.page} / {currentPage.totalPages}</span><button className="btn secondary" onClick={() => setPage((p) => Math.min(currentPage.totalPages, p + 1))}>Next</button></div>
            </article>
          ) : null}

          {tab === "planning" ? (
            <article className="panel">
              <h3>Planning</h3>
              {asString(planningSummary.bestTargetVersion) ? (
                <p style={{ marginTop: 0, color: "var(--muted)" }}>
                  Recommended target: <strong>v{asString(planningSummary.bestTargetVersion)}</strong> ({String(planningSummary.bestTargetScore || 0)} score). {asString(planningSummary.bestTargetReason)}
                </p>
              ) : null}
              {asArray(planningSummary.blockedByVersion).length > 0 ? (
                <div className="panel" style={{ marginBottom: 8 }}>
                  <strong>Upgrade Impact</strong>
                  <p style={{ margin: "6px 0", color: "var(--muted)" }}>Modules blocking higher targets:</p>
                  <div style={{ display: "grid", gap: 6 }}>
                    {asArray(planningSummary.blockedByVersion).map((row, idx) => <div key={`impact-${idx}`} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}><span><strong>v{asString(row.foundryVersion) || "-"}</strong>:</span><span>{String(row.blockedCount || 0)} blocked</span><span>{asArray(row.topBlockers).map((x) => asString(x.module || x)).filter(Boolean).join(", ") || "none"}</span></div>)}
                  </div>
                </div>
              ) : null}
              <div className="panel" style={{ marginBottom: 8 }}>
                <strong>Recommended Workflow</strong>
                <p style={{ margin: "6px 0", color: "var(--muted)" }}>1) Start Scan  2) Update modules/systems on current Foundry  3) Backup snapshot  4) Upgrade Foundry  5) Scan again  6) Rollback if needed.</p>
              </div>
              <div className="metrics-row">
                {planningTargets.map((row) => {
                  const version = asString(row.foundryVersion) || "-";
                  const quick = (row.quickStatus as Record<string, unknown> | undefined) || {};
                  const score = (row.score as Record<string, unknown> | undefined) || {};
                  const total = Number(quick.modulesTotal || 0);
                  const ready = Number(quick.modulesReady || 0);
                  const pct = total > 0 ? Math.round((ready / total) * 100) : 0;
                  const active = planningVersionFilters.includes(version);
                  const tone = asString(score.tone);
                  const toneStyle = tone === "green"
                    ? { borderColor: "#22c55e", background: "#052e16", color: "#dcfce7" }
                    : tone === "red"
                      ? { borderColor: "#ef4444", background: "#3f0b12", color: "#fee2e2" }
                      : { borderColor: "#f59e0b", background: "#3a2404", color: "#ffedd5" };
                  return (
                    <button key={version} className={`metric-card ${active ? "active" : ""}`} style={toneStyle} onClick={() => { setPage(1); setPlanningVersionFilters((arr) => arr.includes(version) ? arr.filter((v) => v !== version) : [...arr, version]); }}>
                      <span>v{version}</span>
                      <strong>{pct}% ready</strong>
                      <small style={{ opacity: 0.9 }}>{String(score.value || 0)} score</small>
                    </button>
                  );
                })}
              </div>
              <div className="metrics-row">
                <button className={`metric-card ${planningSystemFilter === "__systems__" ? "active" : ""}`} style={{ background: "#1f2937", color: "#e5e7eb", borderColor: planningSystemFilter === "__systems__" ? "#fbbf24" : "#334155", boxShadow: planningSystemFilter === "__systems__" ? "0 0 0 2px rgba(251,191,36,0.28) inset" : undefined }} onClick={() => { setPlanningSystemFilter((v) => v === "__systems__" ? "all" : "__systems__"); setPage(1); }}><span style={{ color: "#94a3b8" }}>Systems</span><strong>{planningSystemIds.length}</strong></button>
                <button className={`metric-card metric-blocked ${planningFilters.includes("blocked") ? "active" : ""}`} onClick={() => { setPlanningSystemFilter("all"); setPage(1); setPlanningFilters((arr) => arr.includes("blocked") ? arr.filter((v) => v !== "blocked") : [...arr, "blocked"]); }}><span>Blocked & Missing</span><strong>{planningRows.filter((x) => x.state === "blocked" || x.hasMissingDependencies).length}</strong></button>
                <button className={`metric-card metric-upgrade ${planningFilters.includes("update") ? "active" : ""}`} onClick={() => { setPlanningSystemFilter("all"); setPage(1); setPlanningFilters((arr) => arr.includes("update") ? arr.filter((v) => v !== "update") : [...arr, "update"]); }}><span>Updated</span><strong>{planningRows.filter((x) => x.state === "update").length}</strong></button>
                <button className={`metric-card metric-ready ${planningFilters.includes("ready") ? "active" : ""}`} onClick={() => { setPlanningSystemFilter("all"); setPage(1); setPlanningFilters((arr) => arr.includes("ready") ? arr.filter((v) => v !== "ready") : [...arr, "ready"]); }}><span>Ready</span><strong>{planningRows.filter((x) => x.state === "ready").length}</strong></button>
                <button className={`metric-card metric-unused ${planningFilters.includes("unused") ? "active" : ""}`} onClick={() => { setPlanningSystemFilter("all"); setPage(1); setPlanningFilters((arr) => arr.includes("unused") ? arr.filter((v) => v !== "unused") : [...arr, "unused"]); }}><span>Unused</span><strong>{planningRows.filter((x) => x.system === "unused").length}</strong></button>
              </div>
              <table className="report-table"><thead><tr><th><input type="search" placeholder="Name Search" value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></th><th>Update Path</th><th>Status</th><th style={{ textAlign: "right" }}></th></tr></thead><tbody>
                {planningSystemFilter === "__systems__"
                  ? planningSystemIds.filter((id) => search.trim() ? id.toLowerCase().includes(search.trim().toLowerCase()) : true).map((id) => <tr key={`planning-system-${id}`}><td>{id} <small>(system)</small></td><td>-</td><td>{reasonBadges("Installed system in planning dataset.", {}, false)}</td><td><span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span></td></tr>)
                  : planningPage.rows.map((row) => { const unresolvedPath = row.hasMissingDependencies && !row.releaseUrl && (!row.recommendedVersion || row.recommendedVersion === "-"); const activeSystem = asString(row.relatedSystems?.[0] || ""); const sysCompat = ((row.systemCompatibility as Record<string, unknown> | undefined) || {})[activeSystem] as Record<string, unknown> | undefined; const foundryOk = versionWithin(row.compatibility, row.targetVersion); const systemOk = versionWithin(sysCompat, row.targetVersion); return <tr key={`${row.targetVersion}-${row.module}-${row.system}`}><td>{row.hasMissingDependencies ? <span title={missingDependencyLabel(row.reason)} style={{ color: "#fbbf24", fontWeight: 800, marginRight: 6 }}>!</span> : null}<span title={row.module}>{row.title}</span></td><td>{row.state === "ready" && !row.hasMissingDependencies ? (row.installedVersion || "-") : (unresolvedPath ? "?" : <>{(row.installedVersion || "-")} {" ? "} {row.releaseUrl ? <a href={row.releaseUrl} target="_blank" rel="noreferrer">{(row.recommendedVersion || "-")}</a> : (row.recommendedVersion || "-")} <small>(v{row.targetVersion})</small></>)}</td><td>{reasonBadges(row.reason || "", row.compatibility, row.hasMissingDependencies, foundryOk, systemOk, sysCompat, true)}</td><td style={{ textAlign: "right" }}>{row.hasMissingDependencies ? <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled>Blocked</button> : row.state === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button> : <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>}</td></tr>; })}
              </tbody></table>
              {planningSystemFilter !== "__systems__" ? <div style={{ display: "flex", gap: 8, marginTop: 8 }}><button className="btn secondary" onClick={() => setPage((p) => Math.max(1, p - 1))}>Prev</button><span style={{ alignSelf: "center" }}>{planningPage.page} / {planningPage.totalPages}</span><button className="btn secondary" onClick={() => setPage((p) => Math.min(planningPage.totalPages, p + 1))}>Next</button></div> : null}
            </article>
          ) : null}

          {tab === "backups" ? (
            <article className="panel">
              <h3>Backups</h3>
              <input type="search" placeholder="Search backups..." value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
              <div style={{ display: "flex", gap: 8, marginBottom: 8 }}><button className="btn" disabled={backupModules.length === 0 || actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("cleanup-backups", { modules: backupModules })}>Cleanup Listed Backups</button></div>
              <div className="metrics-row" style={{ marginBottom: 8 }}>
                <div className="metric-card static"><span>Apply History</span><strong>{applyHistoryRows.length}</strong></div>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                <button
                  className="btn secondary"
                  onClick={async () => {
                    try {
                      const health = await api.moduleHealth();
                      setSuggestResult(`Module Health: total=${health.count || 0}, invalid=${health.invalidCount || 0}, warnings=${health.warningCount || 0}`);
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Could not run module health check.");
                    }
                  }}
                >
                  Run Module Health Check
                </button>
                <button
                  className="btn secondary"
                  disabled={applyHistoryRows.length === 0}
                  onClick={async () => {
                    const latest = applyHistoryRows[0];
                    const scanRunId = Number(latest?.scanRunId || 0);
                    if (!scanRunId) return;
                    try {
                      const plan = await api.rollbackPlan(scanRunId);
                      setSuggestResult(`Rollback plan for #${scanRunId}: modules=${(plan.modules || []).join(", ") || "-"} | backups=${(plan.backupPaths || []).length}`);
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Could not load rollback plan.");
                    }
                  }}
                >
                  Show Rollback Plan (Latest)
                </button>
                <button
                  className="btn"
                  style={{ background: "#ef4444", color: "#fff" }}
                  disabled={applyHistoryRows.length === 0 || actionBusy || !foundryConfigured}
                  onClick={async () => {
                    const latest = applyHistoryRows[0];
                    const scanRunId = Number(latest?.scanRunId || 0);
                    if (!scanRunId) return;
                    try {
                      const result = await api.rollbackExecute(scanRunId);
                      setSuggestResult(`Rollback executed for #${scanRunId}: restored=${Number(result.restoredCount || 0)}`);
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Could not execute rollback.");
                    }
                  }}
                >
                  Execute Rollback (Latest)
                </button>
              </div>
              <table className="report-table" style={{ marginBottom: 12 }}><thead><tr><th>When</th><th>Foundry</th><th>Modules Changed</th><th>Backups Created</th><th>Changed IDs</th></tr></thead><tbody>
                {applyHistoryRows.length === 0 ? <tr><td colSpan={5}>No apply history yet.</td></tr> : applyHistoryRows.map((row, idx) => <tr key={`apply-${idx}`}><td>{asString(row.generatedAt) || "-"}</td><td>{asString(row.targetVersion) || "-"}</td><td>{String(row.modulesChangedCount || 0)}</td><td>{String(row.backupsCreatedCount || 0)}</td><td>{asArray(row.modulesChanged).map((x) => asString(x.module || x)).filter(Boolean).join(", ") || "-"}</td></tr>)}
              </tbody></table>
              <table className="report-table"><thead><tr><th>Module</th><th>Backups</th><th>Size Bytes</th><th>Newest</th></tr></thead><tbody>
                {filteredBackups.map((row) => <tr key={asString(row.module)}><td>{asString(row.title) || asString(row.module)}</td><td>{String(row.backupCount || 0)}</td><td>{String(row.backupSizeBytes || 0)}</td><td>{asString(row.newestBackupAt) || "-"}</td></tr>)}
              </tbody></table>
            </article>
          ) : null}

          
        </section>
      ) : null}

      {settingsOpen ? (
        <div className="modal-backdrop" onClick={() => setSettingsOpen(false)}>
          <section className="panel modal-card" onClick={(event) => event.stopPropagation()}>
            <h3>Foundry Data Root</h3>
            <p>{foundryRoot?.message || "Configure the Foundry root path."}</p>
            <input type="text" value={foundryPathInput} onChange={(event) => setFoundryPathInput(event.target.value)} placeholder="Select folder or paste path" />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn secondary" onClick={() => void pickFoundryPath()}>Select Folder</button>
              <button className="btn" onClick={() => void applyFoundryPath()}>Validate & Save</button>
              <button className="btn secondary" onClick={() => void resetFoundryPath()}>Reset</button>
              <button className="btn secondary" onClick={() => setSettingsOpen(false)}>Close</button>
            </div>
          </section>
        </div>
      ) : null}

      {addModuleOpen ? (
        <div className="modal-backdrop" onClick={() => setAddModuleOpen(false)}>
          <section className="panel modal-card" onClick={(event) => event.stopPropagation()}>
            <h3>Add Module</h3>
            <p>Paste module.json URL and get the best compatible version.</p>
            <input type="text" value={suggestInput} onChange={(event) => setSuggestInput(event.target.value)} placeholder="https://.../module.json" />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn" onClick={() => void suggestModule()}>Suggest Best Version</button>
              <button className="btn secondary" onClick={() => setAddModuleOpen(false)}>Close</button>
            </div>
            <p>{suggestResult || "Provide a module.json URL."}</p>
          </section>
        </div>
      ) : null}

      {(actionBusy || hydrationBusy || Boolean(uiBusyMessage)) ? (
        <div className="modal-backdrop">
          <section className="panel modal-card" style={{ width: "min(420px, 92%)" }}>
            <h3>Please wait</h3>
            <p>{uiBusyMessage || (hydrationBusy ? "Resolving module versions..." : "Working...")}</p>
            <div style={{ height: 10, borderRadius: 999, background: "#1f2937" }}>
              <div style={{ height: 10, borderRadius: 999, width: "70%", background: "#fbbf24", animation: "pulse-scan 1.2s ease-in-out infinite" }} />
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}













