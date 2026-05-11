import { useEffect, useMemo, useState } from "react";
import { Header } from "../components/Header";
import { api, type FoundryRootStatus, type ReportModel } from "../services/api";

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
  hasMissingDependencies: boolean;
};
type CurrentTableRow =
  | { kind: "module"; key: string; row: ModuleRow }
  | { kind: "system"; key: string; systemId: string; usedInWorlds: string[]; installedVersion: string; targetVersion: string; targetUrl: string; status: "update" | "ready"; compatibility: Record<string, unknown> };
type PlanningFilter = "blocked" | "update" | "ready" | "unused";
type PlanningRow = ModuleRow & { targetVersion: string };

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
function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.map((v) => v.trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}
function compatibilitySummary(raw: Record<string, unknown> | undefined): string {
  const c = raw || {};
  const minimum = asString(c.minimum ?? c.min);
  const verified = asString(c.verified);
  const maximum = asString(c.maximum ?? c.max);
  if (!minimum && !verified && !maximum) return "Compatibility metadata not provided";
  return `Compatibility within expectations { minimum: ${minimum || "-"}, verified: ${verified || "-"}, max: ${maximum || "-"} }`;
}
function bestRecommendedVersion(row: Record<string, unknown>): string {
  return asString(row.recommendedVersion) || asString(row.targetVersion) || asString(row.latestVersion) || asString(row.version);
}
function bestReleaseUrl(row: Record<string, unknown>): string {
  return asString(row.releaseUrl) || asString(row.manifestUrl) || asString(row.url) || asString(row.projectUrl) || asString(row.downloadUrl);
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
  const [currentSystemFilter, setCurrentSystemFilter] = useState("all");
  const [planningFilters, setPlanningFilters] = useState<PlanningFilter[]>([]);
  const [planningVersionFilters, setPlanningVersionFilters] = useState<string[]>([]);
  const [planningSystemFilter, setPlanningSystemFilter] = useState("all");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addModuleOpen, setAddModuleOpen] = useState(false);
  const [foundryRoot, setFoundryRoot] = useState<FoundryRootStatus | null>(null);
  const [foundryPathInput, setFoundryPathInput] = useState("");
  const [suggestInput, setSuggestInput] = useState("");
  const [suggestResult, setSuggestResult] = useState("");
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
    } finally { setLoading(false); }
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

  useEffect(() => { void loadModel(); void loadFoundryConfig(); }, []);

  const logout = async () => { await api.logout(); onLoggedOut(); };

  const submitAndWatch = async (action: ActionKind, payload: Record<string, unknown>) => {
    setActionBusy(true);
    setError("");
    try {
      const submitted = await api.submitAction(action, payload);
      setJob({ id: submitted.jobId, progress: 0, status: submitted.status });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed to start.");
      setActionBusy(false);
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
        if (status.status === "success") { await loadModel(); setJob(null); setActionBusy(false); return; }
        if (status.status === "failed") { setError(status.error || "Action failed."); setActionBusy(false); return; }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [job?.id]);

  const view = model?.view || {};
  const currentSystems = asArray(view.currentSystemUpgrades?.rows);
  const planningTargets = asArray(view.systemUpgradePlanner?.targets);
  const backupRows = asArray(view.backupManagement?.rows);
  const unusedRows = asArray(view.unusedModules?.rows);
  const worldUsage = asArray(model?.worldUsage);

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
    for (const system of currentSystems) {
      const systemName = asString(system.title) || asString(system.systemId) || "-";
      for (const row of asArray(system.blockedModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length;
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: "blocked", system: systemName, relatedSystems: uniqueSorted([systemName, ...usageSystems, ...compatSystems]), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: hasMissingDependenciesSignal(rowReason, rowMissingCount) });
      }
      for (const row of asArray(system.upgradableModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length;
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: "update", system: systemName, relatedSystems: uniqueSorted([systemName, ...usageSystems, ...compatSystems]), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: hasMissingDependenciesSignal(rowReason, rowMissingCount) });
      }
      for (const row of asArray(system.compatibleModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length;
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: "ready", system: systemName, relatedSystems: uniqueSorted([systemName, ...usageSystems, ...compatSystems]), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: hasMissingDependenciesSignal(rowReason, rowMissingCount) });
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
          hasMissingDependencies: asArray(item.missingDependencies).length > 0
        });
      }

      const missingCandidates = [
        ...asArray(item.missingDependencies),
        ...asArray(item.dependencyActions).filter((dep) => !asString(dep.installedVersion) && !asString(dep.recommendedVersion))
      ];
      if (missingCandidates.length > 0) {
        for (const existing of rows) {
          if (existing.module === moduleId) existing.hasMissingDependencies = true;
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
          hasMissingDependencies: true
        });
      }
    }

    // Ensure Current always surfaces actionable inventory by merging Unused rows.
    for (const row of unusedRows) {
      const moduleId = cleanModuleId(asString(row.module));
      if (!moduleId) continue;
      const compatibilityStatus = asString(row.compatibilityStatus).toLowerCase();
      const canUpdate = asBool(row.updateViable);
      const state: ModuleRow["state"] =
        compatibilityStatus === "incompatible" ? "blocked" : (canUpdate ? "update" : "ready");
      const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
      const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
      const rowReason = asString(row.reason) || "Unused module";
      const rowMissingCount = asArray(row.missingDependencies).length;
      rows.push({
        module: moduleId,
        title: cleanTitle(asString(row.title), moduleId),
        state,
        system: "unused",
        relatedSystems: uniqueSorted([...usageSystems, ...compatSystems]),
        usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(),
        reason: rowReason,
        installedVersion: asString(row.installedVersion),
        recommendedVersion: bestRecommendedVersion(row),
        releaseUrl: bestReleaseUrl(row),
        compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
        hasMissingDependencies: hasMissingDependenciesSignal(rowReason, rowMissingCount)
      });
    }

    const dedup = new Map<string, ModuleRow>();
    const order = { blocked: 0, update: 1, ready: 2 };
    for (const row of rows) {
      const prev = dedup.get(row.module);
      if (!prev || order[row.state] < order[prev.state]) dedup.set(row.module, row);
      else if (prev) {
        prev.usedInWorlds = Array.from(new Set([...prev.usedInWorlds, ...row.usedInWorlds])).sort();
        prev.relatedSystems = uniqueSorted([...prev.relatedSystems, ...row.relatedSystems]);
        prev.hasMissingDependencies = prev.hasMissingDependencies || row.hasMissingDependencies;
      }
    }
    return Array.from(dedup.values()).sort((a, b) => a.title.localeCompare(b.title));
  }, [currentSystems, model?.results, moduleUsage, unusedRows]);

  const installedSystemIds = useMemo(() => {
    const ids = new Set<string>(Object.keys(model?.installedSystemVersions || {}));
    for (const sys of currentSystems) {
      const id = asString(sys.systemId).trim();
      if (id) ids.add(id);
      for (const bucket of [asArray(sys.blockedModuleRows), asArray(sys.upgradableModuleRows), asArray(sys.compatibleModuleRows)]) {
        for (const row of bucket) {
          const sc = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
          for (const key of Object.keys(sc)) {
            const k = String(key || "").trim();
            if (k) ids.add(k);
          }
        }
      }
    }
    for (const row of asArray(model?.results)) {
      const sc = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
      for (const id of Object.keys(sc)) {
        const k = String(id || "").trim();
        if (k) ids.add(k);
      }
    }
    for (const row of unusedRows) {
      const sc = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
      for (const id of Object.keys(sc)) {
        const k = String(id || "").trim();
        if (k) ids.add(k);
      }
    }
    return Array.from(ids).sort((a, b) => a.localeCompare(b));
  }, [model?.installedSystemVersions, currentSystems, model?.results, unusedRows]);
  const updateModules = useMemo(() => currentRows.filter((row) => row.state === "update").map((row) => row.module), [currentRows]);

  const filteredCurrent = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (currentSystemFilter === "__systems__") return [];
    return currentRows
      .filter((row) => {
        if (currentFilters.length === 0) return true;
        return currentFilters.some((filter) => {
          if (filter === "unused") return row.system === "unused";
          if (filter === "blocked") return row.state === "blocked" || row.hasMissingDependencies;
          return row.state === filter;
        });
      })
      .filter((row) => (currentSystemFilter === "all" ? true : row.relatedSystems.includes(currentSystemFilter)))
      .filter((row) => (q ? `${row.title} ${row.module} ${row.system} ${row.reason}`.toLowerCase().includes(q) : true));
  }, [currentRows, search, currentFilters, currentSystemFilter]);

  const planningRows = useMemo<PlanningRow[]>(() => {
    const rows: PlanningRow[] = [];
    for (const target of planningTargets) {
      const targetVersion = asString(target.foundryVersion);
      const systemRows = asArray(target.systemRows).length > 0 ? asArray(target.systemRows) : asArray(target.systems);
      for (const system of systemRows) {
        const systemName = asString(system.title) || asString(system.systemId) || "-";
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
              relatedSystems: [systemName],
              usedInWorlds: [],
              reason,
              installedVersion: asString(row.installedVersion),
              recommendedVersion: bestRecommendedVersion(row),
              releaseUrl: bestReleaseUrl(row),
              compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
              hasMissingDependencies: unknown || hasMissingDependenciesSignal(reason, asArray(row.missingDependencies).length),
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
          state: "blocked",
          system: "unused",
          relatedSystems: ["unused"],
          usedInWorlds: [],
          reason,
          installedVersion: asString(row.installedVersion),
          recommendedVersion: bestRecommendedVersion(row),
          releaseUrl: bestReleaseUrl(row),
          compatibility: (row.compatibility as Record<string, unknown> | undefined) || {},
          hasMissingDependencies: hasMissingDependenciesSignal(reason, asArray(row.missingDependencies).length),
          targetVersion
        });
      }
    }
    return rows;
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
    const systemVersionMap = model?.installedSystemVersions || {};
    const systemTargetMap = new Map<string, string>();
    const worldSystemVersionMap = new Map<string, string>();
    const systemWorldsMap = new Map<string, string[]>();
    for (const sys of currentSystems) {
      const id = asString(sys.systemId).trim();
      if (!id) continue;
      systemTargetMap.set(id, asString(sys.targetVersion));
      const installed = asString(sys.installedVersion).trim();
      if (installed) worldSystemVersionMap.set(id, installed);
    }
    for (const world of worldUsage) {
      const id = asString(world.system).trim();
      const ver = asString(world.systemVersion).trim();
      if (id && ver && !worldSystemVersionMap.has(id)) worldSystemVersionMap.set(id, ver);
      if (id) {
        const worldName = asString(world.alias) || asString(world.title) || asString(world.name) || asString(world.id) || "World";
        const arr = systemWorldsMap.get(id) || [];
        if (!arr.includes(worldName)) arr.push(worldName);
        systemWorldsMap.set(id, arr);
      }
    }
    const toSystemRow = (systemId: string): CurrentTableRow => {
      const installed = asString(systemVersionMap[systemId] ?? "") || asString(worldSystemVersionMap.get(systemId) || "");
      const target = asString(systemTargetMap.get(systemId) || installed);
      const status: "update" | "ready" = installed && target && installed !== target ? "update" : "ready";
      const summary = currentSystems.find((s) => asString(s.systemId).trim() === systemId) || {};
      const targetUrl = asString((summary as Record<string, unknown>).manifestUrl) || asString((summary as Record<string, unknown>).downloadUrl);
      const compatibility = ((summary as Record<string, unknown>).compatibility as Record<string, unknown> | undefined) || {};
      return { kind: "system", key: `system-${systemId}`, systemId, usedInWorlds: (systemWorldsMap.get(systemId) || []).sort(), installedVersion: installed, targetVersion: target, targetUrl, status, compatibility };
    };

    if (currentSystemFilter === "__systems__") {
      const q = search.trim().toLowerCase();
      return installedSystemIds
        .filter((id) => (q ? id.toLowerCase().includes(q) : true))
        .map((systemId) => toSystemRow(systemId));
    }
    if (currentSystemFilter === "all" && currentFilters.length === 0) {
      const moduleRows = filteredCurrent.map((row) => ({ kind: "module", key: `${row.module}-${row.system}`, row } as CurrentTableRow));
      const systemRows = installedSystemIds.map((systemId) => toSystemRow(systemId));
      return [...moduleRows, ...systemRows];
    }
    if (currentSystemFilter !== "all" && filteredCurrent.length === 0) {
      return [toSystemRow(currentSystemFilter)];
    }
    return filteredCurrent.map((row) => ({ kind: "module", key: `${row.module}-${row.system}`, row }));
  }, [filteredCurrent, currentSystemFilter, installedSystemIds, search, model?.installedSystemVersions, currentSystems, currentFilters, worldUsage]);

  const currentPage = paginate(currentTableRows, page, 12);
  const backupModules = backupRows.map((row) => asString(row.module)).filter(Boolean);
  const planningSystemIds = useMemo(() => uniqueSorted(planningRows.flatMap((row) => row.relatedSystems)), [planningRows]);
  const planningPage = paginate(filteredPlanning, page, 12);

  const applyFoundryPath = async () => {
    try {
      const payload = await api.setFoundryRoot(foundryPathInput);
      setFoundryRoot(payload);
      setSettingsOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save path.");
    }
  };

  const pickFoundryPath = async () => {
    try {
      const payload = await api.pickFoundryRoot();
      setFoundryRoot(payload);
      setFoundryPathInput(payload.selected || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not pick folder.");
    }
  };

  const resetFoundryPath = async () => {
    try {
      const payload = await api.resetFoundryRoot();
      setFoundryRoot(payload);
      setFoundryPathInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset folder.");
    }
  };

  const suggestModule = async () => {
    try {
      setSuggestResult("Resolving best version...");
      const payload = await api.suggestModule(suggestInput);
      const s = payload.suggestion || {};
      setSuggestResult(`Recommended: ${String(s.recommendedVersion || "-")} | Compatible: ${String(Boolean(s.isCompatible))} | Checked: ${String(s.checkedReleases || 0)}`);
    } catch (err) {
      setSuggestResult(err instanceof Error ? err.message : "Suggestion failed.");
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
        <p style={{ marginTop: 0, color: "var(--muted)" }}>Last scan: {relativeFromNow(model?.generatedAt)} {clockTick < 0 ? "" : ""}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
          <button className={`btn tab-btn tab-current ${tab === "current" ? "active" : ""}`} onClick={() => { setTab("current"); setPage(1); }}>Current</button>
          <button className={`btn tab-btn tab-planning ${tab === "planning" ? "active" : ""}`} onClick={() => { setTab("planning"); setPage(1); }}>Planning</button>
          <button className={`btn tab-btn tab-backups ${tab === "backups" ? "active" : ""}`} onClick={() => { setTab("backups"); setPage(1); }}>Backups</button>
          <button className="btn secondary" onClick={() => void loadModel()}>Refresh</button>
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
              <h3>Current</h3>
              <div className="metrics-row">
                <button
                  className={`metric-card ${currentSystemFilter === "__systems__" ? "active" : ""}`}
                  style={{
                    background: "#1f2937",
                    color: "#e5e7eb",
                    borderColor: currentSystemFilter === "__systems__" ? "#fbbf24" : "#334155",
                    boxShadow: currentSystemFilter === "__systems__" ? "0 0 0 2px rgba(251,191,36,0.28) inset" : undefined
                  }}
                  onClick={() => { setCurrentSystemFilter((v) => v === "__systems__" ? "all" : "__systems__"); setPage(1); }}
                >
                  <span style={{ color: "#94a3b8" }}>Systems</span>
                  <strong>{installedSystemIds.length}</strong>
                </button>
                <button className={`metric-card metric-blocked ${currentFilters.includes("blocked") ? "active" : ""}`} onClick={() => { setCurrentSystemFilter("all"); setCurrentFilters((arr) => arr.includes("blocked") ? arr.filter((x) => x !== "blocked") : [...arr, "blocked"]); }}><span>Blocked & Missing</span><strong>{currentRows.filter((x) => x.state === "blocked" || x.hasMissingDependencies).length}</strong></button>
                <button className={`metric-card metric-upgrade ${currentFilters.includes("update") ? "active" : ""}`} onClick={() => { setCurrentSystemFilter("all"); setCurrentFilters((arr) => arr.includes("update") ? arr.filter((x) => x !== "update") : [...arr, "update"]); }}><span>Updated</span><strong>{currentRows.filter((x) => x.state === "update").length}</strong></button>
                <button className={`metric-card metric-ready ${currentFilters.includes("ready") ? "active" : ""}`} onClick={() => { setCurrentSystemFilter("all"); setCurrentFilters((arr) => arr.includes("ready") ? arr.filter((x) => x !== "ready") : [...arr, "ready"]); }}><span>Ready</span><strong>{currentRows.filter((x) => x.state === "ready").length}</strong></button>
                <button className={`metric-card metric-unused ${currentFilters.includes("unused") ? "active" : ""}`} onClick={() => { setCurrentSystemFilter("all"); setCurrentFilters((arr) => arr.includes("unused") ? arr.filter((x) => x !== "unused") : [...arr, "unused"]); }}><span>Unused</span><strong>{currentRows.filter((x) => x.system === "unused").length}</strong></button>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                <button className="btn secondary" onClick={() => setAddModuleOpen(true)}>
                  <span className="icon-wrap" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="5" x2="12" y2="19" />
                      <line x1="5" y1="12" x2="19" y2="12" />
                    </svg>
                  </span>
                  <span>Add Module</span>
                </button>
              </div>
              <table className="report-table"><thead><tr><th><input type="search" placeholder="Name Search" value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></th><th>Used in</th><th>Update Path</th><th>Reason</th><th>Actions {updateModules.length > 0 ? <button className="btn secondary btn-xs" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: updateModules, batchSize: 10 })}>Update All ({updateModules.length})</button> : null}</th></tr></thead><tbody>
                {currentPage.rows.map((item) => item.kind === "system"
                  ? <tr key={item.key}><td>{item.systemId} <small>(system)</small></td><td>{item.usedInWorlds.length > 0 ? item.usedInWorlds.join(", ") : "Unused"}</td><td>{(item.installedVersion || "-")} {" → "} {item.targetUrl ? <a href={item.targetUrl} target="_blank" rel="noreferrer">{(item.targetVersion || "-")}</a> : (item.targetVersion || "-")}</td><td>{item.status === "update" ? `Update suggested for this system. ${compatibilitySummary(item.compatibility)}` : `No system update required. ${compatibilitySummary(item.compatibility)}`}</td><td>{item.status === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button> : <button className="btn" style={{ background: "#22c55e", color: "#052e16" }} disabled>Ready</button>}</td></tr>
                  : <tr key={item.key}><td>{item.row.hasMissingDependencies ? <span title="Missing dependencies" style={{ color: "#fbbf24", fontWeight: 800, marginRight: 6 }}>!</span> : null}{(item.row.title || "Unknown module")} <small>({item.row.module || "unknown"})</small></td><td>{item.row.usedInWorlds.length > 0 ? item.row.usedInWorlds.join(", ") : "Unused"}</td><td>{(item.row.installedVersion || "-")} {" → "} {item.row.releaseUrl ? <a href={item.row.releaseUrl} target="_blank" rel="noreferrer">{(item.row.recommendedVersion || "-")}</a> : (item.row.recommendedVersion || "-")}</td><td>{`${item.row.reason || "-"} | ${compatibilitySummary(item.row.compatibility)}`}</td><td><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{item.row.hasMissingDependencies ? <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Get</button> : item.row.state === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Update</button> : <button className="btn" style={{ background: "#22c55e", color: "#052e16" }} disabled>Ready</button>}</div></td></tr>)}
              </tbody></table>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}><button className="btn secondary" onClick={() => setPage((p) => Math.max(1, p - 1))}>Prev</button><span style={{ alignSelf: "center" }}>{currentPage.page} / {currentPage.totalPages}</span><button className="btn secondary" onClick={() => setPage((p) => Math.min(currentPage.totalPages, p + 1))}>Next</button></div>
            </article>
          ) : null}

          {tab === "planning" ? (
            <article className="panel">
              <h3>Planning</h3>
              <div className="metrics-row">
                {planningTargets.map((row) => {
                  const version = asString(row.foundryVersion) || "-";
                  const quick = (row.quickStatus as Record<string, unknown> | undefined) || {};
                  const total = Number(quick.modulesTotal || 0);
                  const ready = Number(quick.modulesReady || 0);
                  const pct = total > 0 ? Math.round((ready / total) * 100) : 0;
                  const active = planningVersionFilters.includes(version);
                  return (
                    <button key={version} className={`metric-card ${active ? "active" : ""}`} onClick={() => { setPage(1); setPlanningVersionFilters((arr) => arr.includes(version) ? arr.filter((v) => v !== version) : [...arr, version]); }}>
                      <span>v{version}</span>
                      <strong>{pct}% ready</strong>
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
              <table className="report-table"><thead><tr><th><input type="search" placeholder="Name Search" value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></th><th>Used in</th><th>Update Path</th><th>Reason</th><th>Actions</th></tr></thead><tbody>
                {planningSystemFilter === "__systems__"
                  ? planningSystemIds.filter((id) => search.trim() ? id.toLowerCase().includes(search.trim().toLowerCase()) : true).map((id) => <tr key={`planning-system-${id}`}><td>{id} <small>(system)</small></td><td>-</td><td>-</td><td>Installed system in planning dataset.</td><td><button className="btn" style={{ background: "#22c55e", color: "#052e16" }} disabled>Ready</button></td></tr>)
                  : planningPage.rows.map((row) => <tr key={`${row.targetVersion}-${row.module}-${row.system}`}><td>{row.hasMissingDependencies ? <span title="Missing dependencies" style={{ color: "#fbbf24", fontWeight: 800, marginRight: 6 }}>!</span> : null}{row.title} <small>({row.module})</small></td><td>{row.system}</td><td>{(row.installedVersion || "-")} {" -> "} {row.releaseUrl ? <a href={row.releaseUrl} target="_blank" rel="noreferrer">{(row.recommendedVersion || "-")}</a> : (row.recommendedVersion || "-")} <small>(v{row.targetVersion})</small></td><td>{`${row.reason || "-"} | ${compatibilitySummary(row.compatibility)}`}</td><td>{row.hasMissingDependencies ? <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled>Blocked</button> : row.state === "update" ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button> : <button className="btn" style={{ background: "#22c55e", color: "#052e16" }} disabled>Ready</button>}</td></tr>)}
              </tbody></table>
              {planningSystemFilter !== "__systems__" ? <div style={{ display: "flex", gap: 8, marginTop: 8 }}><button className="btn secondary" onClick={() => setPage((p) => Math.max(1, p - 1))}>Prev</button><span style={{ alignSelf: "center" }}>{planningPage.page} / {planningPage.totalPages}</span><button className="btn secondary" onClick={() => setPage((p) => Math.min(planningPage.totalPages, p + 1))}>Next</button></div> : null}
            </article>
          ) : null}

          {tab === "backups" ? (
            <article className="panel">
              <h3>Backups</h3>
              <input type="search" placeholder="Search backups..." value={showSearch ? search : ""} onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
              <div style={{ display: "flex", gap: 8, marginBottom: 8 }}><button className="btn" disabled={backupModules.length === 0 || actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("cleanup-backups", { modules: backupModules })}>Cleanup Listed Backups</button></div>
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
        <section className="panel" style={{ marginTop: 12 }}>
          <h3>Add Module</h3>
          <p>Paste module.json URL and get the best compatible version.</p>
          <input type="text" value={suggestInput} onChange={(event) => setSuggestInput(event.target.value)} placeholder="https://.../module.json" />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn" onClick={() => void suggestModule()}>Suggest Best Version</button>
            <button className="btn secondary" onClick={() => setAddModuleOpen(false)}>Close</button>
          </div>
          <p>{suggestResult || "Provide a module.json URL."}</p>
        </section>
      ) : null}
    </main>
  );
}
