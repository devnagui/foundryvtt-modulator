import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { Header } from "../components/Header";
import { UpdatePathWithRefresh } from "../components/UpdatePathWithRefresh";
import { UpgradePanel } from "../components/UpgradePanel";
import { api, type FoundryRootStatus, type ImportHistoryEntry, type ModuleSourceRow, type PlanningContextRow, type ReportModel } from "../services/api";
import { sourceByModuleId } from "./moduleSourceResolver";
import { canForceCompatibility, partitionCountsForPills } from "./reportRules";
import { buildRelatedSystems } from "./systemKeying";

type ReportPageProps = { onLoggedOut: () => void };
type TabId = "current" | "planning" | "backups" | "import";
type ActionKind = "dry-run" | "apply" | "force-compat" | "cleanup-backups" | "override-from-plan";
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
  forcedCompatibility?: Record<string, unknown>;
};
type CurrentTableRow =
  | { kind: "module"; key: string; row: ModuleRow }
  | { kind: "system"; key: string; systemId: string; usedInWorlds: string[]; installedVersion: string; targetVersion: string; targetUrl: string; status: "update" | "ready"; compatibility: Record<string, unknown> };
type PlanningFilter = "blocked" | "update" | "ready" | "unused";
type SuggestedResolution = {
  recommendedVersion?: string;
  resolvedUrl?: string;
  compatibility?: Record<string, unknown>;
  systemCompatibility?: Record<string, unknown>;
  hasDependencyUpdates?: boolean;
  isCompatible?: boolean;
};
type PlanningRow = ModuleRow & { targetVersion: string };
type PlanningTableRow =
  | { kind: "module"; key: string; row: PlanningRow }
  | { kind: "system"; key: string; systemId: string; usedInWorlds: string[]; installedVersion: string; targetVersion: string; targetUrl: string; status: "update" | "ready"; compatibility: Record<string, unknown> };
type ConflictDetail = {
  moduleId: string;
  moduleTitle: string;
  contextLabel: string;
  versionsBySystem: Array<{ systemId: string; version: string; worlds: string[] }>;
  moduleWorlds: string[];
};
type FoundryVersionBucket = {
  key: string;
  total: number;
  ready: number;
  update: number;
  blocked: number;
  missing: number;
  readinessPct: number;
};
type ImportReport = {
  action?: string;
  profile?: string;
  appliedCount?: number;
  skippedCount?: number;
  failureCount?: number;
  failures?: Array<Record<string, unknown>>;
  results?: {
    modules?: Array<Record<string, unknown>>;
    systems?: Array<Record<string, unknown>>;
  };
  reportRefresh?: Record<string, unknown>;
  warnings?: string[];
};
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
  if (state === "blocked" && !hasMissingDependencies) return 0;
  if (hasMissingDependencies) return 1;
  if (state === "update") return 2;
  return 3;
}
function normalizedSystemSortKey(system: string): string {
  const key = asString(system).trim().toLowerCase();
  if (!key) return "zzzzzzzz";
  if (key === "unused") return "zzzzzzzy";
  return key;
}
function compareSystemThenStatus(
  left: { system: string; state: "blocked" | "update" | "ready"; hasMissingDependencies: boolean; title: string },
  right: { system: string; state: "blocked" | "update" | "ready"; hasMissingDependencies: boolean; title: string }
): number {
  const systemCmp = normalizedSystemSortKey(left.system).localeCompare(normalizedSystemSortKey(right.system));
  if (systemCmp !== 0) return systemCmp;
  const pa = rowPriority(left.state, left.hasMissingDependencies);
  const pb = rowPriority(right.state, right.hasMissingDependencies);
  if (pa !== pb) return pa - pb;
  return left.title.localeCompare(right.title);
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
function forcedCompatibilityFromRow(row: Record<string, unknown>): Record<string, unknown> | undefined {
  const direct = row.forcedCompatibility;
  if (direct && typeof direct === "object" && !Array.isArray(direct)) return direct as Record<string, unknown>;
  const flags = row.flags;
  if (!flags || typeof flags !== "object" || Array.isArray(flags)) return undefined;
  const resolver = (flags as Record<string, unknown>).resolver;
  if (!resolver || typeof resolver !== "object" || Array.isArray(resolver)) return undefined;
  const forced = (resolver as Record<string, unknown>).forcedCompatibility;
  if (!forced || typeof forced !== "object" || Array.isArray(forced)) return undefined;
  return forced as Record<string, unknown>;
}
function isNotFoundReason(reason: string): boolean {
  const text = String(reason || "").toLowerCase();
  return text.includes("404") || text.includes("not found");
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
function compatValue(compat: Record<string, unknown> | undefined, keys: string[]): string {
  if (!compat) return "";
  for (const key of keys) {
    const direct = compat[key];
    if (direct !== undefined && direct !== null) {
      const value = String(direct).trim();
      if (value) return value;
    }
  }
  const folded = Object.entries(compat).reduce<Record<string, unknown>>((acc, [k, v]) => {
    acc[k.toLowerCase()] = v;
    return acc;
  }, {});
  for (const key of keys) {
    const value = folded[key.toLowerCase()];
    if (value !== undefined && value !== null) {
      const token = String(value).trim();
      if (token) return token;
    }
  }
  return "";
}
function isMajorOnlyVersion(value: string): boolean {
  return /^\d+$/.test(String(value || "").trim());
}
function isLooseMaxToken(value: string): boolean {
  const token = String(value || "").trim().toLowerCase();
  return token === "-" || token === "*" || token === "any" || token === "none";
}
function splitVersionTokens(value: string): string[] {
  return String(value || "").trim().split(/[.-]/).filter(Boolean);
}
function wildcardIndex(tokens: string[]): number {
  return tokens.findIndex((part) => {
    const token = String(part || "").trim().toLowerCase();
    return token === "x" || token === "*";
  });
}
function compareNumericToken(a: string, b: string): number {
  const av = Number.parseInt(a, 10);
  const bv = Number.parseInt(b, 10);
  const aNum = Number.isFinite(av);
  const bNum = Number.isFinite(bv);
  if (aNum && bNum) {
    if (av !== bv) return av - bv;
    return 0;
  }
  return String(a || "").localeCompare(String(b || ""));
}
function compareByWildcardPrefix(target: string, bound: string): number | null {
  const targetTokens = splitVersionTokens(target);
  const boundTokens = splitVersionTokens(bound);
  const idx = wildcardIndex(boundTokens);
  if (idx < 0) return null;
  for (let i = 0; i < idx; i += 1) {
    const tv = targetTokens[i] || "0";
    const bv = boundTokens[i] || "0";
    const cmp = compareNumericToken(tv, bv);
    if (cmp !== 0) return cmp;
  }
  return 0;
}
function normalizeConstraint(raw: string): { op: string; rhs: string } {
  const text = String(raw || "").trim();
  if (!text) return { op: "", rhs: "" };
  if (text.endsWith("+")) return { op: ">=", rhs: text.slice(0, -1).trim() };
  const match = text.match(/^(>=|<=|>|<|=|\^|~)\s*(.+)$/);
  if (!match) return { op: "", rhs: text };
  const op = String(match[1] || "").trim();
  const rhs = String(match[2] || "").trim();
  if (op === "^" || op === "~") return { op: ">=", rhs };
  return { op, rhs };
}
function compareTargetWithBound(target: string, rhs: string): number {
  const wildcardCmp = compareByWildcardPrefix(target, rhs);
  if (wildcardCmp !== null) return wildcardCmp;
  const cmpAsc = compareVersionAsc(target, rhs);
  if (cmpAsc < 0) return -1;
  if (cmpAsc > 0) return 1;
  return 0;
}
function satisfiesConstraint(target: string, rawBound: string, defaultOp: ">=" | "<="): boolean {
  const parsed = normalizeConstraint(rawBound);
  const rhs = parsed.rhs;
  if (!rhs) return true;
  const op = (parsed.op || defaultOp) as ">=" | "<=" | ">" | "<" | "=";
  const cmp = compareTargetWithBound(target, rhs);
  if (op === ">=") return cmp >= 0;
  if (op === "<=") return cmp <= 0;
  if (op === ">") return cmp > 0;
  if (op === "<") return cmp < 0;
  return cmp === 0;
}
function hasCompatibilityMetadata(compat: Record<string, unknown> | undefined): boolean {
  if (!compat) return false;
  const min = compatValue(compat, ["minimum", "min", "minimumCoreVersion", "minimum_core_version"]);
  const verified = compatValue(compat, ["verified", "compatibleCoreVersion", "compatible_core_version"]);
  const max = compatValue(compat, ["maximum", "max", "maximumCoreVersion", "maximum_core_version"]);
  return Boolean(min || verified || max);
}
function hasVerifiedLaterThanTarget(compat: Record<string, unknown> | undefined, target: string): boolean {
  if (!compat || !target) return false;
  const verified = compatValue(compat, ["verified", "compatibleCoreVersion", "compatible_core_version"]);
  if (!verified) return false;
  return compareVersionAsc(target, verified) < 0;
}
function hasVerifiedMajorMismatch(compat: Record<string, unknown> | undefined, target: string): boolean {
  if (!compat || !target) return false;
  const verified = compatValue(compat, ["verified", "compatibleCoreVersion", "compatible_core_version"]);
  if (!verified) return false;
  const tm = Number.parseInt(target.split(".")[0] || "0", 10);
  const vm = Number.parseInt(verified.split(".")[0] || "0", 10);
  return Number.isFinite(tm) && Number.isFinite(vm) && tm !== vm;
}
function hasMinimumLowerThanTarget(compat: Record<string, unknown> | undefined, target: string): boolean {
  if (!compat || !target) return false;
  const minimum = compatValue(compat, ["minimum", "min", "minimumCoreVersion", "minimum_core_version"]);
  if (!minimum) return false;
  const parsed = normalizeConstraint(minimum);
  const rhs = parsed.rhs || minimum;
  if (!rhs) return false;
  return compareTargetWithBound(target, rhs) > 0;
}
function compatibilityForSystem(
  systemCompatibility: Record<string, unknown> | undefined,
  systemId: string
): Record<string, unknown> | undefined {
  if (!systemCompatibility || !systemId) return undefined;
  const exact = systemCompatibility[systemId];
  if (exact && typeof exact === "object" && !Array.isArray(exact)) return exact as Record<string, unknown>;
  const lowered = systemId.toLowerCase();
  for (const [key, value] of Object.entries(systemCompatibility)) {
    if (String(key || "").trim().toLowerCase() !== lowered) continue;
    if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  }
  return undefined;
}
function systemRestrictionIds(systemCompatibility: Record<string, unknown> | undefined): string[] {
  if (!systemCompatibility) return [];
  const ids: string[] = [];
  for (const [key, value] of Object.entries(systemCompatibility)) {
    if (!key) continue;
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    if (!hasCompatibilityMetadata(value as Record<string, unknown>)) continue;
    ids.push(String(key).trim());
  }
  return Array.from(new Set(ids)).sort((a, b) => a.localeCompare(b));
}
function versionWithin(compat: Record<string, unknown> | undefined, target: string): boolean | null {
  if (!compat || !target) return null;
  const min = compatValue(compat, ["minimum", "min", "minimumCoreVersion", "minimum_core_version"]);
  const verified = compatValue(compat, ["verified", "compatibleCoreVersion", "compatible_core_version"]);
  const max = compatValue(compat, ["maximum", "max", "maximumCoreVersion", "maximum_core_version"]);
  const looseMax = isLooseMaxToken(max) || (!max && Boolean(min));
  const targetMajor = Number.parseInt(target.split(".")[0] || "0", 10);
  const minMajor = Number.parseInt(min.split(".")[0] || "0", 10);
  const maxMajor = Number.parseInt(max.split(".")[0] || "0", 10);
  if (!min && !verified && !max) return null;
  if (min) {
    if (isMajorOnlyVersion(min) && Number.isFinite(targetMajor) && Number.isFinite(minMajor)) {
      if (targetMajor < minMajor) return false;
    } else if (!satisfiesConstraint(target, min, ">=")) {
      return false;
    }
  }
  if (max && !looseMax) {
    if (isMajorOnlyVersion(max) && Number.isFinite(targetMajor) && Number.isFinite(maxMajor)) {
      if (targetMajor > maxMajor) return false;
    } else if (!satisfiesConstraint(target, max, "<=")) {
      return false;
    }
  }
  if (verified) {
    const tm = Number.parseInt(target.split(".")[0] || "0", 10);
    const vm = Number.parseInt(verified.split(".")[0] || "0", 10);
    if (Number.isFinite(tm) && Number.isFinite(vm) && tm !== vm) {
      if (looseMax) return null;
      return false;
    }
  }
  return true;
}
function hasLooseMaxCompatibility(compat: Record<string, unknown> | undefined): boolean {
  const max = compatValue(compat, ["maximum", "max", "maximumCoreVersion", "maximum_core_version"]);
  if (isLooseMaxToken(max)) return true;
  const min = compatValue(compat, ["minimum", "min", "minimumCoreVersion", "minimum_core_version"]);
  return !max && Boolean(min);
}
function compatibilityRangeLabel(compat: Record<string, unknown> | undefined): string {
  const min = compatValue(compat, ["minimum", "min", "minimumCoreVersion", "minimum_core_version"]) || "-";
  const verified = compatValue(compat, ["verified", "compatibleCoreVersion", "compatible_core_version"]) || "-";
  const max = compatValue(compat, ["maximum", "max", "maximumCoreVersion", "maximum_core_version"]) || "-";
  return `compatible{min: ${min}, verified: ${verified}, max: ${max}}`;
}

function providerRefreshMessage(errorCode: string, fallback: string, hint: string): string {
  const code = String(errorCode || "").trim().toLowerCase();
  const helper = hint ? ` ${hint}` : "";
  if (code === "provider_rate_limited") return `Provider rate limit reached.${helper}`;
  if (code === "provider_not_found") return `Source not found (404).${helper}`;
  if (code === "provider_timeout") return `Provider timeout while loading versions.${helper}`;
  if (code === "provider_forbidden") return `Provider access denied.${helper}`;
  if (code === "provider_malformed_response") return `Provider returned invalid response.${helper}`;
  if (code === "provider_error") return `Could not refresh versions from provider.${helper}`;
  return hint ? `${fallback} ${hint}` : fallback;
}

function isManifestLikeUrl(rawUrl: string): boolean {
  const value = asString(rawUrl).trim().toLowerCase();
  if (!value) return false;
  return value.endsWith("/module.json") || value.endsWith("/system.json") || value.endsWith("/manifest.json");
}

function canonicalUpdateUrl(rawUrl: string): string {
  const value = asString(rawUrl).trim();
  if (!value) return "";
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return isManifestLikeUrl(value) ? "" : value;
  }
  const host = parsed.hostname.toLowerCase();
  const origin = `${parsed.protocol}//${parsed.host}`;
  const pathname = parsed.pathname.replace(/\/+$/, "");
  const parts = pathname.split("/").filter(Boolean);
  const manifestLike = isManifestLikeUrl(value);

  if (host === "raw.githubusercontent.com" && parts.length >= 3) {
    const [owner, repo, ref] = parts;
    if (owner && repo && ref) return `https://github.com/${owner}/${repo}/releases/tag/${ref}`;
  }

  if (host === "github.com" || host === "www.github.com") {
    if (pathname.includes("/releases/tag/") || pathname.includes("/releases/latest")) return value;
    if (pathname.includes("/releases/download/")) {
      const [base, rest] = pathname.split("/releases/download/", 2);
      const tag = (rest || "").split("/")[0];
      if (base && tag) return `${origin}${base}/releases/tag/${tag}`;
    }
    if (pathname.includes("/releases/latest/download/")) {
      const [base] = pathname.split("/releases/latest/download/", 1);
      if (base) return `${origin}${base}/releases/latest`;
    }
    if (parts.length >= 4 && parts[2] === "blob" && manifestLike) {
      const owner = parts[0];
      const repo = parts[1];
      const ref = parts[3];
      if (owner && repo && ref) return `${origin}/${owner}/${repo}/releases/tag/${ref}`;
    }
    if (parts.length >= 4 && parts[2] === "raw" && manifestLike) {
      const owner = parts[0];
      const repo = parts[1];
      const ref = parts[3];
      if (owner && repo && ref) return `${origin}/${owner}/${repo}/releases/tag/${ref}`;
    }
    if (manifestLike) return "";
    if (parts.length >= 2) return `${origin}/${parts[0]}/${parts[1]}`;
    return value;
  }

  if (host === "gitlab.com" || host === "www.gitlab.com") {
    if (pathname.includes("/-/releases/")) return value;
    if (pathname.includes("/-/archive/")) {
      const [base, rest] = pathname.split("/-/archive/", 2);
      const tag = (rest || "").split("/")[0];
      if (base && tag) return `${origin}${base}/-/releases/${tag}`;
    }
    if (pathname.includes("/-/raw/") || pathname.includes("/-/blob/")) {
      const marker = pathname.includes("/-/raw/") ? "/-/raw/" : "/-/blob/";
      const [base, rest] = pathname.split(marker, 2);
      const ref = (rest || "").split("/")[0];
      if (base && ref) return `${origin}${base}/-/releases/${ref}`;
    }
    if (manifestLike) return "";
    if (parts.length >= 2) return `${origin}/${parts[0]}/${parts[1]}`;
    return value;
  }

  if (manifestLike) return "";
  return value;
}

function preferredUpdateUrlFromCandidates(...urls: string[]): string {
  for (const raw of urls) {
    const candidate = canonicalUpdateUrl(raw);
    if (candidate) return candidate;
  }
  return "";
}

function StatusBadge({
  icon,
  label,
  tone,
  onActivate,
}: {
  icon: string;
  label: string;
  tone: "ok" | "fail" | "warn" | "neutral" | "info";
  onActivate?: (() => void) | null;
}) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return (
    <button
      type="button"
      className={`status-badge ${tone}`}
      title={label}
      aria-label={label}
      aria-describedby={open ? tooltipId : undefined}
      onClick={() => {
        if (onActivate) {
          setOpen(true);
          onActivate();
          return;
        }
        setOpen((v) => !v);
      }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setOpen(false);
        }
      }}
    >
      <span>{icon}</span>
      {open ? (
        <span id={tooltipId} role="tooltip" className="status-badge-tooltip">
          {label}
        </span>
      ) : null}
    </button>
  );
}

function reasonBadges(
  _reason: string,
  compatibility: Record<string, unknown> | undefined,
  hasMissingDependencies = false,
  foundryCompatOk: boolean | null = null,
  systemCompatOk: boolean | null = null,
  systemCompatibility?: Record<string, unknown> | undefined,
  showSystemBadge = true,
  forcedCompatibility?: Record<string, unknown>,
  foundryTarget = "",
  systemTarget = "",
  restrictedSystemIds: string[] = [],
  systemUpgradeConflictTooltip?: string,
  onSystemUpgradeConflictClick?: (() => void) | null
) {
  const badges: Array<{ icon: string; title: string; tone: "ok" | "fail" | "warn" | "neutral" | "info"; onActivate?: (() => void) | null }> = [];
  if (hasMissingDependencies) {
    badges.push({ icon: "!", title: "Missing dependency or unresolved dependency relationship.", tone: "warn" });
  }
  if (forcedCompatibility && Boolean(forcedCompatibility.enabled)) {
    const target = asString(forcedCompatibility.targetVersion) || "-";
    const appliedAt = asString(forcedCompatibility.appliedAt) || "-";
    badges.push({
      icon: "[x]",
      title: `Forced compatibility enabled (target: ${target}, appliedAt: ${appliedAt}).`,
      tone: "warn",
    });
  }
  if (systemUpgradeConflictTooltip) {
    badges.push({
      icon: "SC",
      title: `System upgrade conflict: ${systemUpgradeConflictTooltip}`,
      tone: "fail",
      onActivate: onSystemUpgradeConflictClick || null,
    });
  }
  const foundryRange = compatibilityRangeLabel(compatibility);
  const foundryFollowup = hasVerifiedLaterThanTarget(compatibility, foundryTarget);
  const foundryLoose = hasLooseMaxCompatibility(compatibility);
  if (foundryFollowup) {
    const verified = compatValue(compatibility, ["verified", "compatibleCoreVersion", "compatible_core_version"]) || "?";
    badges.push({
      icon: "F\u2191",
      title: `Update Suggested: Verified for Foundry version ${verified}. ${foundryRange}`,
      tone: "info",
    });
  } else if (foundryCompatOk === false) {
    badges.push({
      icon: "F\u2715",
      title: `Foundry compatibility incompatible with selected target. ${foundryRange}`,
      tone: "fail",
    });
  } else if (foundryLoose) {
    const mismatch = hasVerifiedMajorMismatch(compatibility, foundryTarget);
    badges.push({
      icon: "F~",
      title: mismatch
        ? `Foundry compatibility open-ended: minimum is satisfied and max is open. Verified points to a different major, so this is treated as uncertain (not blocked). ${foundryRange}`
        : `Foundry compatibility open-ended: minimum is satisfied and max is open, so upper bound is uncertain. ${foundryRange}`,
      tone: "warn",
    });
  } else {
    badges.push({
      icon: foundryCompatOk === null ? "F?" : (foundryCompatOk ? "F\u2713" : "F\u2715"),
      title: foundryCompatOk === null
        ? `Foundry compatibility uncertain: insufficient compatibility metadata. ${foundryRange}`
        : (foundryCompatOk ? `Foundry compatibility valid for selected target. ${foundryRange}` : `Foundry compatibility incompatible with selected target. ${foundryRange}`),
      tone: foundryCompatOk === null ? "warn" : (foundryCompatOk ? "ok" : "fail")
    });
  }
  if (showSystemBadge) {
    const systemRange = compatibilityRangeLabel(systemCompatibility);
    const systemIdsLabel = restrictedSystemIds.length > 0 ? `systems: ${restrictedSystemIds.join(", ")}` : "systems: -";
    const systemFollowup = hasVerifiedLaterThanTarget(systemCompatibility, systemTarget);
    const systemLoose = hasLooseMaxCompatibility(systemCompatibility);
    if (systemFollowup && !systemUpgradeConflictTooltip) {
      const verified = compatValue(systemCompatibility, ["verified", "compatibleCoreVersion", "compatible_core_version"]) || "?";
      badges.push({
        icon: "S\u2191",
        title: `Update Suggested: Verified for system version ${verified}. ${systemRange} | ${systemIdsLabel}`,
        tone: "info",
      });
    } else if (systemCompatOk === false) {
      badges.push({
        icon: "S\u2715",
        title: `System compatibility incompatible with selected target. ${systemRange} | ${systemIdsLabel}`,
        tone: "fail",
      });
    } else if (systemLoose && !systemUpgradeConflictTooltip) {
      const mismatch = hasVerifiedMajorMismatch(systemCompatibility, systemTarget);
      badges.push({
        icon: "S~",
        title: mismatch
          ? `System compatibility open-ended: minimum is satisfied and max is open. Verified points to a different major, so this is treated as uncertain (not blocked). ${systemRange} | ${systemIdsLabel}`
          : `System compatibility open-ended: minimum is satisfied and max is open, so upper bound is uncertain. ${systemRange} | ${systemIdsLabel}`,
        tone: "warn",
      });
    } else {
      badges.push({
        icon: systemCompatOk === null ? "S?" : (systemCompatOk ? "S\u2713" : "S\u2715"),
        title: systemCompatOk === null
          ? `System compatibility uncertain: insufficient compatibility metadata. ${systemRange} | ${systemIdsLabel}`
          : (systemCompatOk ? `System compatibility valid for selected target. ${systemRange} | ${systemIdsLabel}` : `System compatibility incompatible with selected target. ${systemRange} | ${systemIdsLabel}`),
        tone: systemCompatOk === null ? "warn" : (systemCompatOk ? "ok" : "fail")
      });
    }
  }
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "nowrap" }}>
      {badges.map((badge, idx) => (
        <StatusBadge
          key={`${badge.icon}-${idx}`}
          icon={badge.icon}
          label={badge.title}
          tone={badge.tone}
          onActivate={badge.onActivate || null}
        />
      ))}
    </div>
  );
}
function collectBadgeCodes(params: {
  compatibility: Record<string, unknown> | undefined;
  hasMissingDependencies: boolean;
  foundryCompatOk: boolean | null;
  systemCompatOk: boolean | null;
  systemCompatibility?: Record<string, unknown> | undefined;
  showSystemBadge: boolean;
  forcedCompatibility?: Record<string, unknown>;
  foundryTarget: string;
  systemTarget: string;
  systemUpgradeConflictTooltip?: string;
}): string[] {
  const out = new Set<string>();
  if (params.hasMissingDependencies) out.add("!");
  if (params.forcedCompatibility && Boolean(params.forcedCompatibility.enabled)) out.add("[x]");
  if (params.systemUpgradeConflictTooltip) out.add("SC");
  const foundryFollowup = hasVerifiedLaterThanTarget(params.compatibility, params.foundryTarget);
  const foundryLoose = hasLooseMaxCompatibility(params.compatibility);
  if (foundryFollowup) out.add("F\u2191");
  else if (params.foundryCompatOk === false) out.add("F\u2715");
  else if (foundryLoose) out.add("F~");
  else out.add(params.foundryCompatOk === null ? "F?" : (params.foundryCompatOk ? "F\u2713" : "F\u2715"));
  if (params.showSystemBadge) {
    const systemFollowup = hasVerifiedLaterThanTarget(params.systemCompatibility, params.systemTarget);
    const systemLoose = hasLooseMaxCompatibility(params.systemCompatibility);
    if (systemFollowup && !params.systemUpgradeConflictTooltip) out.add("S\u2191");
    else if (params.systemCompatOk === false) out.add("S\u2715");
    else if (systemLoose && !params.systemUpgradeConflictTooltip) out.add("S~");
    else out.add(params.systemCompatOk === null ? "S?" : (params.systemCompatOk ? "S\u2713" : "S\u2715"));
  }
  return Array.from(out);
}
const BADGE_FILTER_OPTIONS: Array<{ key: string; label: string }> = [
  { key: "F\u2713", label: "Foundry valid" },
  { key: "F\u2715", label: "Foundry incompatible" },
  { key: "F?", label: "Foundry uncertain" },
  { key: "F~", label: "Foundry open-ended" },
  { key: "F\u2191", label: "Foundry follow-up" },
  { key: "S\u2713", label: "System valid" },
  { key: "S\u2715", label: "System incompatible" },
  { key: "S?", label: "System uncertain" },
  { key: "S~", label: "System open-ended" },
  { key: "S\u2191", label: "System follow-up" },
  { key: "SC", label: "System conflict" },
  { key: "!", label: "Missing dependency" },
  { key: "[x]", label: "Forced compatibility" },
];
function bestRecommendedVersion(row: Record<string, unknown>): string {
  return asString(row.recommendedVersion) || asString(row.targetVersion) || asString(row.latestVersion) || asString(row.version);
}
function bestReleaseUrl(row: Record<string, unknown>): string {
  return preferredUpdateUrlFromCandidates(
    asString(row.releaseUrl),
    asString(row.downloadUrl),
    asString(row.projectUrl),
    asString(row.url),
    asString(row.manifestUrl)
  );
}
function hasConcreteValue(value: string): boolean {
  const v = asString(value).trim();
  return Boolean(v && v !== "-");
}
function isPlaceholderVersion(value: string): boolean {
  const v = asString(value).trim();
  return !v || v === "-" || v === "0.0.0";
}
function selectPreferredRecommended(candidates: string[], installedVersion: string): string {
  const installed = asString(installedVersion).trim();
  const hasInstalled = hasConcreteValue(installed);
  for (const raw of candidates) {
    const candidate = asString(raw).trim();
    if (isPlaceholderVersion(candidate)) continue;
    if (hasInstalled && compareVersionAsc(candidate, installed) <= 0) continue;
    return candidate;
  }
  return "";
}
function hasSourceUrls(source: Partial<ModuleSourceRow> | undefined): boolean {
  return Boolean(asString(source?.manifestUrl) || asString(source?.projectUrl));
}
function looksLikeManifestUrl(url: string): boolean {
  return /\/(module|system|manifest)\.json(?:$|[?#])/i.test(asString(url).trim());
}
function sourceFromReleaseUrl(url: string): { manifestUrl: string; projectUrl: string } {
  const clean = asString(url).trim();
  if (!clean) return { manifestUrl: "", projectUrl: "" };
  if (looksLikeManifestUrl(clean)) return { manifestUrl: clean, projectUrl: "" };
  const lower = clean.toLowerCase();
  if (lower.includes("github.com") || lower.includes("gitlab.com")) {
    return { manifestUrl: "", projectUrl: clean };
  }
  return { manifestUrl: clean, projectUrl: "" };
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
  const [job, setJob] = useState<{ id: string; progress: number; status: string; action?: string; progressMeta?: Record<string, unknown> } | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [actionBusy, setActionBusy] = useState(false);
  const [currentFilters, setCurrentFilters] = useState<CurrentFilter[]>([]);
  const [currentSystemFilter, setCurrentSystemFilter] = useState("");
  const [activeCurrentSystemId, setActiveCurrentSystemId] = useState("");
  const [currentVersionBySystem, setCurrentVersionBySystem] = useState<Record<string, string>>({});
  const [planningFilters, setPlanningFilters] = useState<PlanningFilter[]>([]);
  const [planningFoundryFilter, setPlanningFoundryFilter] = useState("");
  const [planningSystemVersionFilter, setPlanningSystemVersionFilter] = useState("");
  const [activePlanningSystemId, setActivePlanningSystemId] = useState("");
  const [planningVersionBySystem, setPlanningVersionBySystem] = useState<Record<string, string>>({});
  const [planningIncludeUnused, setPlanningIncludeUnused] = useState(true);
  const [badgeFilterCodes, setBadgeFilterCodes] = useState<string[]>([]);
  const [badgeFilterOpen, setBadgeFilterOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addModuleOpen, setAddModuleOpen] = useState(false);
  const [foundryRoot, setFoundryRoot] = useState<FoundryRootStatus | null>(null);
  const [foundryPathInput, setFoundryPathInput] = useState("");
  const [suggestInput, setSuggestInput] = useState("");
  const [suggestResult, setSuggestResult] = useState("");
  const [lastImportReport, setLastImportReport] = useState<ImportReport | null>(null);
  const [importHistory, setImportHistory] = useState<ImportHistoryEntry[]>([]);
  const [importHistoryLoading, setImportHistoryLoading] = useState(false);
  const [importProfile, setImportProfile] = useState<"current" | "destiny">("current");
  const [uiBusyMessage, setUiBusyMessage] = useState("");
  const [hydrationBusy, setHydrationBusy] = useState(false);
  const [moduleSources, setModuleSources] = useState<Record<string, ModuleSourceRow>>({});
  const [resolvedSourceByContext, setResolvedSourceByContext] = useState<Record<string, SuggestedResolution>>({});
  const [resolvedSourceByModule, setResolvedSourceByModule] = useState<Record<string, SuggestedResolution>>({});
  const [resolvedAttemptedByContext, setResolvedAttemptedByContext] = useState<Record<string, boolean>>({});
  const [planningContextRowsByModule, setPlanningContextRowsByModule] = useState<Record<string, PlanningContextRow>>({});
  const [refreshingModuleById, setRefreshingModuleById] = useState<Record<string, boolean>>({});
  const [moduleRefreshStatusById, setModuleRefreshStatusById] = useState<Record<string, { kind: "ok" | "error"; message: string; retryable?: boolean }>>({});
  const [planningHydrationProgress, setPlanningHydrationProgress] = useState<{ total: number; done: number }>({ total: 0, done: 0 });
  const [conflictDetail, setConflictDetail] = useState<ConflictDetail | null>(null);
  const [statusLegendOpen, setStatusLegendOpen] = useState(false);
  const hydrationRunRef = useRef(0);
  const importPlanInputRef = useRef<HTMLInputElement | null>(null);
  const foundryConfigured = Boolean(foundryRoot?.valid);
  const showSearch = tab === "current" || tab === "planning" || tab === "backups";

  useEffect(() => {
    if (tab === "backups" || tab === "import") {
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

  const loadImportHistory = async () => {
    setImportHistoryLoading(true);
    try {
      const payload = await api.importHistory(25);
      setImportHistory(Array.isArray(payload.items) ? payload.items : []);
    } catch {
      setImportHistory([]);
    } finally {
      setImportHistoryLoading(false);
    }
  };

  useEffect(() => { void loadModel(); void loadFoundryConfig(); void loadModuleSources(); }, []);
  useEffect(() => { if (tab === "import") void loadImportHistory(); }, [tab]);

  const logout = async () => { await api.logout(); onLoggedOut(); };

  const submitAndWatch = async (action: ActionKind, payload: Record<string, unknown>) => {
    setActionBusy(true);
    setUiBusyMessage("Processing action...");
    setError("");
    try {
      const submitted = await api.submitAction(action, payload);
      setJob({ id: submitted.jobId, progress: 0, status: submitted.status, action, progressMeta: {} });
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
        setJob({
          id: job.id,
          progress: Number(status.progress || 0),
          status: status.status,
          action: status.action || (job as { action?: string }).action || "",
          progressMeta: (status.progressMeta as Record<string, unknown> | undefined) || {},
        });
        if (status.status === "success") {
          const result = status.result as Record<string, unknown> | undefined;
          const actionName = String(status.action || (job as { action?: string }).action || "").trim();
          if (actionName === "override-from-plan" || String(result?.action || "").trim() === "override-from-plan") {
            const report = (result || {}) as ImportReport;
            setLastImportReport(report);
            void loadImportHistory();
            setSuggestResult(
              `Import finished: applied=${Number(report.appliedCount || 0)} | skipped=${Number(report.skippedCount || 0)} | failed=${Number(report.failureCount || 0)}`
            );
          }
          await loadModel();
          setJob(null);
          setActionBusy(false);
          setUiBusyMessage("");
          return;
        }
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
  const planningTargetsByFoundry = (view.systemUpgradePlanner?.targetsByFoundry as Record<string, unknown> | undefined) || {};
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
  const systemWorlds = useMemo(() => {
    const bySystem = new Map<string, Set<string>>();
    for (const world of worldUsage) {
      const worldName = asString(world.alias) || asString(world.title) || asString(world.name) || asString(world.id) || "World";
      const systemName = asString(world.system).trim();
      if (!systemName) continue;
      const existing = bySystem.get(systemName) || new Set<string>();
      existing.add(worldName);
      bySystem.set(systemName, existing);
    }
    return bySystem;
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
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "blocked"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount), forcedCompatibility: forcedCompatibilityFromRow(row) });
      }
      for (const row of asArray(system.upgradableModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        if (!isUsedByAnyWorld(moduleId)) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length + unresolvedDependencyCount(row);
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "update"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount), forcedCompatibility: forcedCompatibilityFromRow(row) });
      }
      for (const row of asArray(system.compatibleModuleRows)) {
        const moduleId = cleanModuleId(asString(row.module));
        if (!moduleId) continue;
        if (!isUsedByAnyWorld(moduleId)) continue;
        const compatSystems = Object.keys((row.systemCompatibility as Record<string, unknown> | undefined) || {});
        const usageSystems = Array.from((moduleUsage.get(moduleId)?.systems || new Set<string>()));
        const rowReason = asString(row.reason);
        const rowMissingCount = asArray(row.missingDependencies).length + unresolvedDependencyCount(row);
        rows.push({ module: moduleId, title: cleanTitle(asString(row.title), moduleId), state: presentationState(row, "ready"), system: systemName, relatedSystems: buildRelatedSystems(systemId, usageSystems, compatSystems), usedInWorlds: Array.from((moduleUsage.get(moduleId)?.worlds || new Set<string>())).sort(), reason: rowReason, installedVersion: asString(row.installedVersion), recommendedVersion: bestRecommendedVersion(row), releaseUrl: bestReleaseUrl(row), compatibility: (row.compatibility as Record<string, unknown> | undefined) || {}, systemCompatibility: (row.systemCompatibility as Record<string, unknown> | undefined) || {}, hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount), forcedCompatibility: forcedCompatibilityFromRow(row) });
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
          hasMissingDependencies: asArray(item.missingDependencies).length > 0,
          forcedCompatibility: forcedCompatibilityFromRow(item)
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
          hasMissingDependencies: false,
          forcedCompatibility: forcedCompatibilityFromRow(missing)
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
        hasMissingDependencies: presentationMissing(row, rowReason, rowMissingCount),
        forcedCompatibility: forcedCompatibilityFromRow(row)
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
        if (!row.forcedCompatibility && prev.forcedCompatibility) row.forcedCompatibility = prev.forcedCompatibility;
        dedup.set(row.module, row);
      } else {
        prev.usedInWorlds = Array.from(new Set([...prev.usedInWorlds, ...row.usedInWorlds])).sort();
        prev.relatedSystems = uniqueSorted([...prev.relatedSystems, ...row.relatedSystems]);
        prev.hasMissingDependencies = prev.hasMissingDependencies || row.hasMissingDependencies;
        if (!hasConcreteValue(prev.installedVersion) && hasConcreteValue(row.installedVersion)) prev.installedVersion = row.installedVersion;
        if (!hasConcreteValue(prev.recommendedVersion) && hasConcreteValue(row.recommendedVersion)) prev.recommendedVersion = row.recommendedVersion;
        if (!asString(prev.releaseUrl).trim() && asString(row.releaseUrl).trim()) prev.releaseUrl = row.releaseUrl;
        if (!prev.forcedCompatibility && row.forcedCompatibility) prev.forcedCompatibility = row.forcedCompatibility;
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
          || asString(dep.downloadUrl).trim()
          || asString(dep.projectUrl).trim()
          || asString(dep.manifestUrl).trim();
        const updateUrl = preferredUpdateUrlFromCandidates(releaseUrl);
        if (!hasConcreteValue(recommendedVersion) && !updateUrl) continue;
        const prev = out[moduleId];
        if (!prev) {
          out[moduleId] = {
            recommendedVersion: hasConcreteValue(recommendedVersion) ? recommendedVersion : undefined,
            releaseUrl: updateUrl || undefined,
          };
          continue;
        }
        if (!hasConcreteValue(asString(prev.recommendedVersion)) && hasConcreteValue(recommendedVersion)) prev.recommendedVersion = recommendedVersion;
        if (!asString(prev.releaseUrl).trim() && updateUrl) prev.releaseUrl = updateUrl;
      }
    }
    return out;
  }, [model?.results]);

  const currentSystemVersionById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [systemIdRaw, versionRaw] of Object.entries(model?.installedSystemVersions || {})) {
      const systemId = asString(systemIdRaw).trim();
      const version = asString(versionRaw).trim();
      if (systemId && version) map[systemId] = version;
    }
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
    const active = activeCurrentSystemId || (currentSystemFilter ? "" : first);
    if (active && next[active] && !currentSystemFilter) {
      setCurrentSystemFilter(next[active]);
    }
  }, [currentSystemVersionById, activeCurrentSystemId, currentSystemFilter]);

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
                  hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row)),
                  forcedCompatibility: forcedCompatibilityFromRow(row)
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
    return buckets.sort((a, b) => compareVersionAsc(a.key, b.key));
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
        (row.relatedSystems.length === 0 && !activeCurrentSystemId) || row.relatedSystems.some((systemId) =>
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
              hasMissingDependencies: presentationMissing(row, reason, asArray(row.missingDependencies).length + unresolvedDependencyCount(row)),
              forcedCompatibility: forcedCompatibilityFromRow(row)
            });
          }
        };
        pushRows(asArray(system.blockedModuleRows), "blocked");
        pushRows(asArray(system.upgradableModuleRows), "update");
        pushRows(asArray(system.compatibleModuleRows), "ready");
        pushRows(asArray(system.unknownModuleRows), "blocked", true);
      }
    }
    const projectedFallback = currentRows
      .filter((row) => {
        if (row.system === "unused") return true;
        if (row.relatedSystems.length === 0 && !activeCurrentSystemId) return true;
        return row.relatedSystems.some((systemId) =>
          selectedCurrentVersionBucket.systems.includes(systemId) && (!activeCurrentSystemId || systemId === activeCurrentSystemId)
        );
      })
      .map((row) => {
        if (row.system === "unused") return row;
        const targetSystemId = activeCurrentSystemId
          || row.relatedSystems.find((id) => selectedCurrentVersionBucket.systems.includes(id))
          || selectedCurrentVersionBucket.systems[0]
          || "";
        const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
        const sysCompat = compatibilityForSystem(systemCompatMap, targetSystemId);
        const foundryOk = versionWithin(row.compatibility, currentFoundryVersion);
        const systemOk = versionWithin(sysCompat, selectedCurrentVersionBucket.key);
        const baseline = normalizeModuleState(row);
        const nextState: ModuleRow["state"] =
          row.hasMissingDependencies || foundryOk === false || systemOk === false
            ? "blocked"
            : baseline.state;
        return { ...baseline, state: nextState };
      });
    const mergedByModule = new Map<string, ModuleRow>();
    for (const row of rows) mergedByModule.set(row.module, row);
    for (const row of projectedFallback) {
      if (!mergedByModule.has(row.module)) mergedByModule.set(row.module, row);
    }
    const sorted = Array.from(mergedByModule.values()).sort((a, b) => {
      const pa = rowPriority(a.state, a.hasMissingDependencies);
      const pb = rowPriority(b.state, b.hasMissingDependencies);
      if (pa !== pb) return pa - pb;
      return a.title.localeCompare(b.title);
    });
    return sorted;
  }, [selectedCurrentVersionBucket, currentRows, planningTargets, currentFoundryVersion, activeCurrentSystemId]);

  useEffect(() => {
    if (tab !== "current") return;
    if (!model) return;
    const pool = [...currentRows, ...selectedCurrentRows];
    const candidates = Array.from(new Set(pool
      .filter((row) => {
        const source = sourceForRow(moduleSources, row.module, row.title);
        const hasSource = hasSourceUrls(source);
        if (!hasSource) return false;
        if (hasConcreteValue(row.recommendedVersion) || hasConcreteValue(row.releaseUrl)) {
          const activeSystem = activeCurrentSystemId
            || asString(row.relatedSystems?.[0] || "")
            || asString(selectedCurrentVersionBucket?.systems?.[0] || "");
          const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
          const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
          const systemTarget = activeSystem
            ? (currentVersionBySystem[activeSystem] || currentSystemFilter || selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || "")
            : (currentSystemFilter || selectedCurrentVersionBucket?.key || "");
          const foundryOk = versionWithin(row.compatibility, currentFoundryVersion);
          const systemOk = versionWithin(sysCompat, systemTarget);
          const contextMismatch = foundryOk === false || systemOk === false;
          if (!contextMismatch) return false;
        }
        return true;
      })
      .map((row) => row.module)))
      .filter((moduleId) => {
        const source = sourceForRow(moduleSources, moduleId, moduleId);
        if (!source || !hasSourceUrls(source)) return false;
        const contextKey = `${selectedCurrentSuggestContextKey}::${moduleId}`;
        if (resolvedSourceByContext[contextKey]?.recommendedVersion || resolvedSourceByContext[contextKey]?.resolvedUrl) return false;
        if (resolvedAttemptedByContext[contextKey]) return false;
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
        const attempted: Record<string, boolean> = {};
        for (const row of rows) {
          const moduleId = asString(row?.moduleId);
          if (!moduleId) continue;
          const suggestion = (row?.suggestion || {}) as Record<string, unknown>;
          const recommendedVersion = asString(suggestion.recommendedVersion);
          const resolvedUrl = preferredUpdateUrlFromCandidates(
            asString(suggestion.releaseUrl),
            asString(suggestion.downloadUrl),
            asString(suggestion.projectUrl),
            asString(suggestion.manifestUrl)
          );
          const contextKey = `${selectedCurrentSuggestContextKey}::${moduleId}`;
          attempted[contextKey] = true;
          if (!recommendedVersion && !resolvedUrl) continue;
          updates[contextKey] = {
            recommendedVersion: recommendedVersion || undefined,
            resolvedUrl: resolvedUrl || undefined,
          };
        }
        for (const item of batch) {
          const moduleId = asString(item.moduleId);
          if (!moduleId) continue;
          attempted[`${selectedCurrentSuggestContextKey}::${moduleId}`] = true;
        }
        if (Object.keys(attempted).length > 0) {
          setResolvedAttemptedByContext((prev) => ({ ...prev, ...attempted }));
        }
        if (Object.keys(updates).length > 0) {
          setResolvedSourceByContext((prev) => ({ ...prev, ...updates }));
          const byModule: Record<string, { recommendedVersion?: string; resolvedUrl?: string }> = {};
          for (const row of rows) {
            const moduleId = asString(row?.moduleId);
            if (!moduleId) continue;
            const suggestion = (row?.suggestion || {}) as Record<string, unknown>;
            const recommendedVersion = asString(suggestion.recommendedVersion);
            const resolvedUrl = preferredUpdateUrlFromCandidates(
              asString(suggestion.releaseUrl),
              asString(suggestion.downloadUrl),
              asString(suggestion.projectUrl),
              asString(suggestion.manifestUrl)
            );
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
  }, [tab, model, moduleSources, currentRows, selectedCurrentRows, selectedCurrentSuggestContext, selectedCurrentSuggestContextKey, resolvedSourceByContext, resolvedAttemptedByContext, activeCurrentSystemId, selectedCurrentVersionBucket, currentVersionBySystem, currentSystemFilter, currentSystemVersionById, currentFoundryVersion]);

  useEffect(() => {
    if (!hydrationBusy && !actionBusy && uiBusyMessage === "Applying selected system version...") {
      setUiBusyMessage("");
    }
  }, [hydrationBusy, actionBusy, uiBusyMessage, selectedCurrentRows]);

  const deriveCurrentEffectiveState = (row: ModuleRow): ModuleRow["state"] => {
    const moduleKey = asString(row.module).trim();
    const titleKey = asString(row.title).trim();
    const depSuggestion = dependencySuggestionByModule[moduleKey] || dependencySuggestionByModule[moduleKey.toLowerCase()] || dependencySuggestionByModule[titleKey] || dependencySuggestionByModule[titleKey.toLowerCase()] || {};
    const moduleSuggestion = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()] || resolvedSourceByModule[titleKey] || resolvedSourceByModule[titleKey.toLowerCase()] || {};
    const contextKey = `${selectedCurrentSuggestContextKey}::${moduleKey}`;
    const hydratedRecommended = resolvedSourceByContext[contextKey]?.recommendedVersion || "";
    const dependencyRecommended = asString(depSuggestion.recommendedVersion);
    const moduleRecommended = asString(moduleSuggestion.recommendedVersion);
    const contextualRecommended = row.recommendedVersion || "-";
    const activeSystem = activeCurrentSystemId || row.relatedSystems[0] || "";
    const systemTarget = activeSystem
      ? (currentVersionBySystem[activeSystem] || currentSystemFilter || selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || "")
      : (currentSystemFilter || selectedCurrentVersionBucket?.key || "");
    const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const foundryOk = versionWithin(row.compatibility, currentFoundryVersion);
    const systemOk = versionWithin(sysCompat, systemTarget);
    const hasCompatFailure = foundryOk === false || systemOk === false;
    const recommendedCandidates = [contextualRecommended, hydratedRecommended, moduleRecommended, dependencyRecommended];
    const effectiveRecommended = selectPreferredRecommended(recommendedCandidates, row.installedVersion);
    const hasInstalled = Boolean(asString(row.installedVersion).trim() && asString(row.installedVersion).trim() !== "-");
    if (row.hasMissingDependencies || hasCompatFailure) return "blocked";
    if (!hasInstalled && effectiveRecommended) return "update";
    if (hasInstalled && effectiveRecommended && compareVersionAsc(effectiveRecommended, row.installedVersion) > 0) return "update";
    return "ready";
  };
  const currentBadgeCodesForRow = (row: ModuleRow): string[] => {
    const activeSystem = activeCurrentSystemId || row.relatedSystems[0] || "";
    const systemTarget = activeSystem
      ? (currentVersionBySystem[activeSystem] || currentSystemFilter || selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || "")
      : (currentSystemFilter || selectedCurrentVersionBucket?.key || "");
    const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const showSystemBadge = systemRestrictionIds(systemCompatMap).length > 0;
    const foundryOk = versionWithin(row.compatibility, currentFoundryVersion);
    const systemOk = versionWithin(sysCompat, systemTarget);
    return collectBadgeCodes({
      compatibility: row.compatibility,
      hasMissingDependencies: row.hasMissingDependencies,
      foundryCompatOk: foundryOk,
      systemCompatOk: systemOk,
      systemCompatibility: sysCompat,
      showSystemBadge,
      forcedCompatibility: row.forcedCompatibility,
      foundryTarget: currentFoundryVersion,
      systemTarget,
      systemUpgradeConflictTooltip: undefined,
    });
  };

  const filteredCurrent = useMemo(() => {
    const q = search.trim().toLowerCase();
    return selectedCurrentRows
      .filter((row) => {
        if (currentFilters.length === 0) return true;
        const effectiveState = deriveCurrentEffectiveState(row);
        return currentFilters.some((filter) => {
          if (filter === "unused") return row.system === "unused";
          if (row.system === "unused") return false;
          if (filter === "blocked") return effectiveState === "blocked" || row.hasMissingDependencies;
          return effectiveState === filter;
        });
      })
      .filter((row) => (
        badgeFilterCodes.length === 0
          ? true
          : badgeFilterCodes.some((code) => currentBadgeCodesForRow(row).includes(code))
      ))
      .filter((row) => (q ? `${row.title} ${row.module} ${row.system} ${row.reason}`.toLowerCase().includes(q) : true));
  }, [selectedCurrentRows, search, currentFilters, badgeFilterCodes, dependencySuggestionByModule, resolvedSourceByModule, selectedCurrentSuggestContextKey, resolvedSourceByContext, activeCurrentSystemId, currentVersionBySystem, currentSystemFilter, selectedCurrentVersionBucket, currentSystemVersionById, currentFoundryVersion]);

  function derivePlanningEffectiveState(row: PlanningRow): ModuleRow["state"] {
    const moduleKey = asString(row.module).trim();
    const titleKey = asString(row.title).trim();
    const depSuggestion = dependencySuggestionByModule[moduleKey] || dependencySuggestionByModule[moduleKey.toLowerCase()] || dependencySuggestionByModule[titleKey] || dependencySuggestionByModule[titleKey.toLowerCase()] || {};
    const moduleSuggestion = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()] || resolvedSourceByModule[titleKey] || resolvedSourceByModule[titleKey.toLowerCase()] || {};
    const contextKey = `${selectedPlanningSuggestContextKey}::${moduleKey}`;
    const hydratedContext = resolvedSourceByContext[contextKey] || {};
    const hydratedRecommended = asString(hydratedContext.recommendedVersion);
    const rowRecommended = asString(row.recommendedVersion);
    const dependencyRecommended = asString(depSuggestion.recommendedVersion);
    const moduleRecommended = asString(moduleSuggestion.recommendedVersion);
    const hydratedCompatibility = hydratedContext.compatibility as Record<string, unknown> | undefined;
    const hydratedSystemCompatibility = hydratedContext.systemCompatibility as Record<string, unknown> | undefined;
    const effectiveCompatibility: Record<string, unknown> = hasCompatibilityMetadata(hydratedCompatibility)
      ? (hydratedCompatibility || {})
      : (row.compatibility || {});
    const effectiveSystemCompatibility: Record<string, unknown> = (hydratedSystemCompatibility && Object.keys(hydratedSystemCompatibility).length > 0)
      ? hydratedSystemCompatibility
      : (row.systemCompatibility || {});
    const systemCompatMap = (effectiveSystemCompatibility as Record<string, unknown> | undefined) || {};
    const activeSystem = activePlanningSystemId || asString(row.relatedSystems?.[0] || "");
    const effectivePlanningSystemTarget = activeSystem
      ? (planningVersionBySystem[activeSystem] || planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "")
      : (planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "");
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const foundryOk = versionWithin(effectiveCompatibility, planningFoundryFilter);
    const systemOk = versionWithin(sysCompat, effectivePlanningSystemTarget);
    const hasCompatFailure = foundryOk === false || systemOk === false;
    if (row.hasMissingDependencies || hasCompatFailure) return "blocked";
    const recommendedCandidates = [hydratedRecommended, rowRecommended, moduleRecommended, dependencyRecommended];
    const effectiveRecommended = selectPreferredRecommended(recommendedCandidates, row.installedVersion);
    const hasInstalled = hasConcreteValue(row.installedVersion);
    if (!hasInstalled && hasConcreteValue(effectiveRecommended)) return "update";
    if (hasInstalled && effectiveRecommended && compareVersionAsc(effectiveRecommended, row.installedVersion) > 0) return "update";
    const contextHasDepUpdates = Boolean(hydratedContext.hasDependencyUpdates);
    if (contextHasDepUpdates) return "update";
    return "ready";
  }
  const planningBadgeCodesForRow = (row: PlanningRow): string[] => {
    const moduleKey = asString(row.module).trim();
    const contextKey = `${selectedPlanningSuggestContextKey}::${moduleKey}`;
    const hydratedContext = resolvedSourceByContext[contextKey] || {};
    const hydratedCompatibility = hydratedContext.compatibility as Record<string, unknown> | undefined;
    const hydratedSystemCompatibility = hydratedContext.systemCompatibility as Record<string, unknown> | undefined;
    const effectiveCompatibility: Record<string, unknown> = hasCompatibilityMetadata(hydratedCompatibility)
      ? (hydratedCompatibility || {})
      : (row.compatibility || {});
    const effectiveSystemCompatibility: Record<string, unknown> = (hydratedSystemCompatibility && Object.keys(hydratedSystemCompatibility).length > 0)
      ? hydratedSystemCompatibility
      : (row.systemCompatibility || {});
    const systemCompatMap = effectiveSystemCompatibility || {};
    const activeSystem = activePlanningSystemId || asString(row.relatedSystems?.[0] || "");
    const effectivePlanningSystemTarget = activeSystem
      ? (planningVersionBySystem[activeSystem] || planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "")
      : (planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "");
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const showSystemBadge = systemRestrictionIds(systemCompatMap).length > 0;
    const foundryOk = versionWithin(effectiveCompatibility, planningFoundryFilter);
    const systemOk = versionWithin(sysCompat, effectivePlanningSystemTarget);
    return collectBadgeCodes({
      compatibility: effectiveCompatibility,
      hasMissingDependencies: row.hasMissingDependencies,
      foundryCompatOk: foundryOk,
      systemCompatOk: systemOk,
      systemCompatibility: sysCompat,
      showSystemBadge,
      forcedCompatibility: row.forcedCompatibility,
      foundryTarget: planningFoundryFilter,
      systemTarget: effectivePlanningSystemTarget,
      systemUpgradeConflictTooltip: undefined,
    });
  };

  const currentEffectiveCounts = useMemo(() => {
    return partitionCountsForPills(
      selectedCurrentRows.map((row) => ({
        status: deriveCurrentEffectiveState(row),
        system: row.system,
        hasMissingDependencies: row.hasMissingDependencies,
      }))
    );
  }, [selectedCurrentRows, dependencySuggestionByModule, resolvedSourceByModule, selectedCurrentSuggestContextKey, resolvedSourceByContext, activeCurrentSystemId, currentVersionBySystem, currentSystemFilter, selectedCurrentVersionBucket, currentSystemVersionById, currentFoundryVersion]);

  const fixModules = useMemo(() => {
    const ids = new Set<string>();
    for (const row of filteredCurrent) {
      const moduleKey = asString(row.module).trim();
      if (!moduleKey) continue;
      const titleKey = asString(row.title).trim();
      const source = sourceForRow(moduleSources, moduleKey, titleKey);
      const depSuggestion = dependencySuggestionByModule[moduleKey]
        || dependencySuggestionByModule[moduleKey.toLowerCase()]
        || dependencySuggestionByModule[titleKey]
        || dependencySuggestionByModule[titleKey.toLowerCase()]
        || {};
      const moduleSuggestion = resolvedSourceByModule[moduleKey]
        || resolvedSourceByModule[moduleKey.toLowerCase()]
        || resolvedSourceByModule[titleKey]
        || resolvedSourceByModule[titleKey.toLowerCase()]
        || {};
      const contextKey = `${selectedCurrentSuggestContextKey}::${moduleKey}`;
      const hydratedRecommended = resolvedSourceByContext[contextKey]?.recommendedVersion || "";
      const hydratedUrl = resolvedSourceByContext[contextKey]?.resolvedUrl || "";
      const dependencyRecommended = asString(depSuggestion.recommendedVersion);
      const dependencyUrl = asString(depSuggestion.releaseUrl);
      const moduleRecommended = asString(moduleSuggestion.recommendedVersion);
      const moduleUrl = asString(moduleSuggestion.resolvedUrl);
      const contextualRecommended = row.recommendedVersion || "-";
      const contextualUrl = row.releaseUrl || "";
      const activeSystem = activeCurrentSystemId || row.relatedSystems[0] || "";
      const systemTarget = activeSystem
        ? (currentVersionBySystem[activeSystem] || currentSystemFilter || selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || "")
        : (currentSystemFilter || selectedCurrentVersionBucket?.key || "");
      const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
      const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
      const foundryOk = versionWithin(row.compatibility, currentFoundryVersion);
      const systemOk = versionWithin(sysCompat, systemTarget);
      const staleContextValue = foundryOk === false || systemOk === false;
      const recommendedCandidates = staleContextValue
        ? [hydratedRecommended, moduleRecommended, dependencyRecommended, contextualRecommended]
        : [contextualRecommended, hydratedRecommended, moduleRecommended, dependencyRecommended];
      const urlCandidates = staleContextValue
        ? [hydratedUrl, moduleUrl, dependencyUrl, contextualUrl, asString(source.projectUrl), asString(source.manifestUrl)]
        : [contextualUrl, hydratedUrl, moduleUrl, dependencyUrl, asString(source.projectUrl), asString(source.manifestUrl)];
      const rawRecommended = recommendedCandidates.find((v) => asString(v).trim() && asString(v).trim() !== "-") || "";
      const effectiveRecommended = rawRecommended && rawRecommended !== "-" ? rawRecommended : "";
      const effectiveUrl = preferredUpdateUrlFromCandidates(...urlCandidates);
      const hasInstalled = Boolean(asString(row.installedVersion).trim() && asString(row.installedVersion).trim() !== "-");
      const canInstall = !hasInstalled && Boolean(effectiveUrl || effectiveRecommended);
      const canUpdate = hasInstalled && row.state === "update";
      if (canInstall || canUpdate) ids.add(moduleKey);
    }
    return Array.from(ids);
  }, [
    filteredCurrent,
    moduleSources,
    dependencySuggestionByModule,
    resolvedSourceByModule,
    selectedCurrentSuggestContextKey,
    resolvedSourceByContext,
    activeCurrentSystemId,
    currentVersionBySystem,
    currentSystemFilter,
    selectedCurrentVersionBucket,
    currentSystemVersionById,
    currentFoundryVersion,
  ]);

  const planningRowsByFoundry = useMemo<Record<string, PlanningRow[]>>(() => {
    const byFoundry: Record<string, PlanningRow[]> = {};
    const targetPool = Object.keys(planningTargetsByFoundry).length > 0
      ? Object.values(planningTargetsByFoundry).filter((value): value is Record<string, unknown> => Boolean(value && typeof value === "object"))
      : planningTargets;
    for (const target of targetPool) {
      const foundryVersion = asString((target as Record<string, unknown>).foundryVersion).trim();
      if (!foundryVersion) continue;
      if (!byFoundry[foundryVersion]) byFoundry[foundryVersion] = [];
      const rows = byFoundry[foundryVersion];
      const systemRows = asArray((target as Record<string, unknown>).systemRows).length > 0 ? asArray((target as Record<string, unknown>).systemRows) : asArray((target as Record<string, unknown>).systems);
      for (const system of systemRows) {
        const systemId = asString(system.systemId).trim();
        const systemName = asString(system.title) || systemId || "-";
        const relationSystems = systemId ? [systemId] : [];
        const targetVersion = asString(system.targetVersion).trim() || asString(system.recommendedVersion).trim();
        const pushRows = (bucket: Array<Record<string, unknown>>, state: ModuleRow["state"], unknown = false) => {
          for (const row of bucket) {
            const moduleId = cleanModuleId(asString(row.module));
            if (!moduleId) continue;
            const reason = asString(row.reason) || (unknown ? "Needs verification" : "");
            rows.push({
              module: moduleId,
              title: cleanTitle(asString(row.title), moduleId),
              state: presentationState(row, state),
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
              forcedCompatibility: forcedCompatibilityFromRow(row),
              targetVersion: targetVersion || foundryVersion,
            });
          }
        };
        pushRows(asArray(system.blockedModuleRows), "blocked");
        pushRows(asArray(system.upgradableModuleRows), "update");
        pushRows(asArray(system.compatibleModuleRows), "ready");
        pushRows(asArray(system.unknownModuleRows), "blocked", true);
      }
      for (const row of asArray((target as Record<string, unknown>).localManifestManualModules)) {
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
          forcedCompatibility: forcedCompatibilityFromRow(row),
          targetVersion: foundryVersion,
        });
      }
      byFoundry[foundryVersion] = rows.map(normalizeModuleState).sort((a, b) => {
        const pa = rowPriority(a.state, a.hasMissingDependencies);
        const pb = rowPriority(b.state, b.hasMissingDependencies);
        if (pa !== pb) return pa - pb;
        return a.title.localeCompare(b.title);
      });
    }
    return byFoundry;
  }, [planningTargets, planningTargetsByFoundry]);

  const planningFoundryBuckets = useMemo<FoundryVersionBucket[]>(() => {
    return Object.entries(planningRowsByFoundry)
      .map(([version, rows]) => {
        const total = rows.length;
        const missing = rows.filter((row) => row.hasMissingDependencies).length;
        const blocked = rows.filter((row) => !row.hasMissingDependencies && row.state === "blocked").length;
        const update = rows.filter((row) => row.state === "update").length;
        const ready = rows.filter((row) => row.state === "ready" && !row.hasMissingDependencies).length;
        const readinessPct = total > 0 ? Math.round((ready / total) * 100) : 0;
        return { key: version, total, ready, update, blocked, missing, readinessPct };
      })
      .sort((a, b) => compareVersionAsc(a.key, b.key));
  }, [planningRowsByFoundry]);

  useEffect(() => {
    if (planningFoundryBuckets.length === 0) {
      if (planningFoundryFilter) setPlanningFoundryFilter("");
      return;
    }
    const exists = planningFoundryBuckets.some((bucket) => bucket.key === planningFoundryFilter);
    if (exists) return;
    const currentMatch = planningFoundryBuckets.find((bucket) => bucket.key === currentFoundryVersion);
    setPlanningFoundryFilter((currentMatch || planningFoundryBuckets[0]).key);
  }, [planningFoundryBuckets, planningFoundryFilter, currentFoundryVersion]);

  const selectedPlanningFoundryRows = useMemo(
    () => (planningFoundryFilter ? (planningRowsByFoundry[planningFoundryFilter] || []) : []),
    [planningRowsByFoundry, planningFoundryFilter]
  );

  const planningSystemIds = useMemo(
    () =>
      uniqueSorted(
        [
          ...selectedPlanningFoundryRows
          .flatMap((row) => row.relatedSystems)
          .filter((systemId) => systemId && systemId !== "unused"),
          ...Object.keys(currentSystemVersionById),
        ]
      ),
    [selectedPlanningFoundryRows, currentSystemVersionById]
  );

  const planningSystemVersionBuckets = useMemo<SystemVersionBucket[]>(() => {
    const systemsByVersion = new Map<string, Set<string>>();
    const isAtOrAboveInstalled = (systemId: string, version: string): boolean => {
      const installed = asString(currentSystemVersionById[systemId]).trim();
      const candidate = asString(version).trim();
      if (!installed || !candidate) return Boolean(candidate);
      return compareVersionAsc(candidate, installed) >= 0;
    };
    const register = (version: string, systemId: string) => {
      const v = version.trim();
      const s = systemId.trim();
      if (!v || !s || s === "unused") return;
      if (!isAtOrAboveInstalled(s, v)) return;
      const bucket = systemsByVersion.get(v) || new Set<string>();
      bucket.add(s);
      systemsByVersion.set(v, bucket);
    };
    for (const row of selectedPlanningFoundryRows) {
      for (const systemId of row.relatedSystems) {
        register(asString(row.targetVersion), systemId);
      }
    }
    const selectedTargetFromIndex = planningTargetsByFoundry[planningFoundryFilter];
    const selectedTarget = selectedTargetFromIndex && typeof selectedTargetFromIndex === "object"
      ? (selectedTargetFromIndex as Record<string, unknown>)
      : planningTargets.find((target) => asString(target.foundryVersion).trim() === planningFoundryFilter);
    const selectedSystemRows = selectedTarget
      ? (asArray(selectedTarget.systemRows).length > 0 ? asArray(selectedTarget.systemRows) : asArray(selectedTarget.systems))
      : [];
    for (const system of selectedSystemRows) {
      const systemId = asString(system.systemId).trim();
      if (!systemId || !planningSystemIds.includes(systemId)) continue;
      const versions = extractSystemTargetVersions(system);
      for (const version of versions) register(version, systemId);
    }
    for (const systemId of planningSystemIds) {
      const installed = asString(currentSystemVersionById[systemId]).trim();
      if (installed) register(installed, systemId);
    }
    const buckets: SystemVersionBucket[] = [];
    for (const [version, systemsSet] of systemsByVersion.entries()) {
      const systems = Array.from(systemsSet);
      const rows = selectedPlanningFoundryRows.filter(
        (row) => asString(row.targetVersion).trim() === version && row.relatedSystems.some((systemId) => systemsSet.has(systemId))
      );
      const total = rows.length;
      const missing = rows.filter((row) => row.hasMissingDependencies).length;
      const blocked = rows.filter((row) => !row.hasMissingDependencies && row.state === "blocked").length;
      const update = rows.filter((row) => row.state === "update").length;
      const ready = rows.filter((row) => row.state === "ready" && !row.hasMissingDependencies).length;
      const readinessPct = total > 0 ? Math.round((ready / total) * 100) : 0;
      const isCurrent = systems.length > 0 && systems.every((systemId) => asString(currentSystemVersionById[systemId]).trim() === version);
      buckets.push({ key: version, systems, isCurrent, total, ready, update, blocked, missing, readinessPct });
    }
    return buckets.sort((a, b) => compareVersionAsc(a.key, b.key));
  }, [selectedPlanningFoundryRows, planningSystemIds, currentSystemVersionById, planningTargets, planningTargetsByFoundry, planningFoundryFilter]);

  const selectedPlanningVersionBucket = useMemo(
    () => planningSystemVersionBuckets.find((bucket) => bucket.key === planningSystemVersionFilter) || null,
    [planningSystemVersionFilter, planningSystemVersionBuckets]
  );
  const selectedPlanningSuggestContext = useMemo(() => {
    const systemVersions: Record<string, string> = {};
    if (selectedPlanningVersionBucket) {
      const selectedSystems = activePlanningSystemId
        ? selectedPlanningVersionBucket.systems.filter((id) => id === activePlanningSystemId)
        : selectedPlanningVersionBucket.systems;
      for (const systemId of selectedSystems) {
        systemVersions[systemId] = selectedPlanningVersionBucket.key;
      }
    }
    return {
      targetFoundryVersion: planningFoundryFilter || undefined,
      installedSystemVersions: systemVersions,
    };
  }, [selectedPlanningVersionBucket, planningFoundryFilter, activePlanningSystemId]);
  const selectedPlanningSuggestContextKey = useMemo(() => {
    const systems = Object.entries(selectedPlanningSuggestContext.installedSystemVersions || {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([id, version]) => `${id}@${version}`)
      .join("|");
    return `${selectedPlanningSuggestContext.targetFoundryVersion || ""}::${systems}`;
  }, [selectedPlanningSuggestContext]);

  useEffect(() => {
    if (tab !== "planning") return;
    if (!planningFoundryFilter || !activePlanningSystemId || !planningSystemVersionFilter) {
      setPlanningContextRowsByModule({});
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const payload = await api.planningContext(
          planningFoundryFilter,
          activePlanningSystemId,
          planningSystemVersionFilter,
          6000
        );
        if (cancelled) return;
        const rows = asArray(payload.rows);
        const byModule: Record<string, PlanningContextRow> = {};
        for (const item of rows) {
          const moduleId = asString((item as Record<string, unknown>).moduleId).trim();
          if (!moduleId) continue;
          byModule[moduleId] = item as PlanningContextRow;
          byModule[moduleId.toLowerCase()] = item as PlanningContextRow;
        }
        setPlanningContextRowsByModule(byModule);
      } catch {
        if (cancelled) return;
        setPlanningContextRowsByModule({});
      }
    })();
    return () => { cancelled = true; };
  }, [tab, planningFoundryFilter, activePlanningSystemId, planningSystemVersionFilter]);

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const systemId of planningSystemIds) {
      const installed = asString(currentSystemVersionById[systemId]).trim();
      const options = planningSystemVersionBuckets
        .filter((bucket) => bucket.systems.includes(systemId))
        .map((bucket) => ({
          key: bucket.key,
          total: selectedPlanningFoundryRows.filter(
            (row) => asString(row.targetVersion).trim() === bucket.key && row.relatedSystems.includes(systemId)
          ).length,
        }));
      const installedOption = options.find((opt) => opt.key === installed);
      const preferred = options.find((opt) => opt.total > 0);
      const fallback = (installedOption && installedOption.total > 0)
        ? installedOption.key
        : (preferred?.key || installed || options[0]?.key || "");
      if (fallback) next[systemId] = fallback;
    }
    if (Object.keys(next).length > 0) {
      setPlanningVersionBySystem((prev) => ({ ...prev, ...next }));
    }
    if (activePlanningSystemId && next[activePlanningSystemId]) setPlanningSystemVersionFilter(next[activePlanningSystemId]);
  }, [planningSystemIds, planningSystemVersionBuckets, currentSystemVersionById, activePlanningSystemId, selectedPlanningFoundryRows, planningSystemVersionFilter]);

  useEffect(() => {
    if (tab !== "planning") return;
    if (!activePlanningSystemId) return;
    if (planningSystemVersionBuckets.length === 0) return;
    const hasSelected = planningSystemVersionBuckets.some((bucket) => bucket.key === planningSystemVersionFilter);
    if (!hasSelected) {
      const fallback = activePlanningSystemId ? planningVersionBySystem[activePlanningSystemId] : "";
      setPlanningSystemVersionFilter(fallback || planningSystemVersionBuckets[0].key);
    }
  }, [tab, planningSystemVersionFilter, planningSystemVersionBuckets, activePlanningSystemId, planningVersionBySystem]);

  const normalizePlanningRowForTarget = (
    input: ModuleRow | PlanningRow,
    targetFoundryVersion: string,
    targetSystemVersion: string,
    forcedSystemId = ""
  ): PlanningRow => {
    const baseline = normalizeModuleState(input);
    if (baseline.system === "unused") {
      return { ...baseline, targetVersion: targetSystemVersion } as PlanningRow;
    }
    const targetSystemId = forcedSystemId || activePlanningSystemId || baseline.relatedSystems[0] || "";
    const systemCompatMap = (baseline.systemCompatibility as Record<string, unknown> | undefined) || {};
    const sysCompat = compatibilityForSystem(systemCompatMap, targetSystemId);
    const foundryOk = versionWithin(baseline.compatibility, targetFoundryVersion);
    const systemOk = versionWithin(sysCompat, targetSystemVersion);
    const effectiveRecommended = selectPreferredRecommended([baseline.recommendedVersion], baseline.installedVersion);
    const hasInstalled = hasConcreteValue(baseline.installedVersion);
    const nextState: ModuleRow["state"] =
      baseline.hasMissingDependencies || foundryOk === false || systemOk === false
        ? "blocked"
        : (!hasInstalled && hasConcreteValue(effectiveRecommended))
          ? "update"
          : (hasInstalled && hasConcreteValue(effectiveRecommended) && compareVersionAsc(effectiveRecommended, baseline.installedVersion) > 0)
            ? "update"
            : "ready";
    return {
      ...baseline,
      state: nextState,
      targetVersion: targetSystemVersion,
    } as PlanningRow;
  };
  const normalizePlanningRowForFoundryOnly = (input: ModuleRow | PlanningRow, targetFoundryVersion: string): PlanningRow => {
    const baseline = normalizeModuleState(input);
    const foundryOk = versionWithin(baseline.compatibility, targetFoundryVersion);
    const effectiveRecommended = selectPreferredRecommended([baseline.recommendedVersion], baseline.installedVersion);
    const hasInstalled = hasConcreteValue(baseline.installedVersion);
    const nextState: ModuleRow["state"] = baseline.hasMissingDependencies || foundryOk === false
      ? "blocked"
      : (!hasInstalled && hasConcreteValue(effectiveRecommended))
        ? "update"
        : (hasInstalled && hasConcreteValue(effectiveRecommended) && compareVersionAsc(effectiveRecommended, baseline.installedVersion) > 0)
          ? "update"
          : "ready";
    return {
      ...baseline,
      state: nextState,
      targetVersion: asString((baseline as PlanningRow).targetVersion) || "",
    } as PlanningRow;
  };
  const projectPlanningRowsFoundryAggregate = (
    sourceRows: ModuleRow[],
    targetFoundryVersion: string
  ): PlanningRow[] => {
    const aggregateScore = (row: PlanningRow): number => {
      if (row.hasMissingDependencies) return 0;
      if (row.state === "blocked") return 1;
      if (row.state === "update") return 2;
      return 3;
    };
    const plannerRows = sourceRows.map((row) => normalizePlanningRowForFoundryOnly(row, targetFoundryVersion));
    const projectedFallback = currentRows.map((row) => normalizePlanningRowForFoundryOnly(row, targetFoundryVersion));
    const mergedByModule = new Map<string, PlanningRow>();
    for (const row of plannerRows) {
      const existing = mergedByModule.get(row.module);
      if (!existing) {
        mergedByModule.set(row.module, row);
        continue;
      }
      const currentScore = aggregateScore(row);
      const existingScore = aggregateScore(existing);
      if (currentScore > existingScore) mergedByModule.set(row.module, row);
    }
    for (const row of projectedFallback) {
      if (!mergedByModule.has(row.module)) mergedByModule.set(row.module, row);
    }
    return Array.from(mergedByModule.values())
      .filter((row) => hasConcreteValue(row.installedVersion))
      .sort((a, b) => {
        const pa = rowPriority(a.state, a.hasMissingDependencies);
        const pb = rowPriority(b.state, b.hasMissingDependencies);
        if (pa !== pb) return pa - pb;
        return a.title.localeCompare(b.title);
      });
  };
  const planningAllSystemsMode = !activePlanningSystemId;

  const selectedPlanningRows = useMemo(() => {
    const includeUnused = planningIncludeUnused;
    if (planningAllSystemsMode) {
      const rows = projectPlanningRowsFoundryAggregate(selectedPlanningFoundryRows, planningFoundryFilter);
      return includeUnused ? rows : rows.filter((row) => String(row.system || "").trim().toLowerCase() !== "unused");
    }
    if (!selectedPlanningVersionBucket) return selectedPlanningFoundryRows;
    const plannerRows = selectedPlanningFoundryRows
      .filter((row) => {
        const targetVersion = asString(row.targetVersion).trim();
        if (targetVersion !== selectedPlanningVersionBucket.key) return false;
        if (row.relatedSystems.length === 0 && !activePlanningSystemId) return true;
        if (!activePlanningSystemId) return true;
        return row.relatedSystems.includes(activePlanningSystemId);
      })
      .map((row) => normalizePlanningRowForTarget(row, planningFoundryFilter, selectedPlanningVersionBucket.key, activePlanningSystemId || ""));

    const projectedFallback = currentRows
      .filter((row) => {
        if (row.system === "unused") return true;
        if (!activePlanningSystemId) return true;
        return row.relatedSystems.includes(activePlanningSystemId);
      })
      .map((row) => normalizePlanningRowForTarget(row, planningFoundryFilter, selectedPlanningVersionBucket.key, activePlanningSystemId || ""));
    const mergedByModule = new Map<string, PlanningRow>();
    for (const row of plannerRows) mergedByModule.set(row.module, row);
    for (const row of projectedFallback) {
      if (!mergedByModule.has(row.module)) mergedByModule.set(row.module, row);
    }
    const rows = Array.from(mergedByModule.values()).sort((a, b) => {
      const pa = rowPriority(a.state, a.hasMissingDependencies);
      const pb = rowPriority(b.state, b.hasMissingDependencies);
      if (pa !== pb) return pa - pb;
      return a.title.localeCompare(b.title);
    });
    return includeUnused ? rows : rows.filter((row) => String(row.system || "").trim().toLowerCase() !== "unused");
  }, [selectedPlanningFoundryRows, selectedPlanningVersionBucket, activePlanningSystemId, currentRows, planningFoundryFilter, planningAllSystemsMode, planningIncludeUnused]);

  const currentSystemUpgradeConflictByModule = useMemo(() => {
    const out: Record<string, ConflictDetail> = {};
    const rows = planningRowsByFoundry[currentFoundryVersion] || [];
    if (rows.length === 0) return out;
    const byModule = new Map<string, Map<string, string>>();
    for (const row of rows) {
      const moduleId = asString(row.module).trim();
      const suggestedVersion = asString(row.recommendedVersion).trim();
      if (!moduleId || !hasConcreteValue(suggestedVersion)) continue;
      const targetVersion = asString(row.targetVersion).trim();
      if (!targetVersion) continue;
      for (const systemId of row.relatedSystems) {
        const sid = asString(systemId).trim();
        if (!sid || sid === "unused") continue;
        const currentSystemVersion = asString(currentSystemVersionById[sid]).trim();
        if (!currentSystemVersion || currentSystemVersion !== targetVersion) continue;
        const moduleMap = byModule.get(moduleId) || new Map<string, string>();
        moduleMap.set(sid, suggestedVersion);
        byModule.set(moduleId, moduleMap);
      }
    }
    for (const [moduleId, versionsBySystem] of byModule.entries()) {
      const uniqueVersions = Array.from(new Set(Array.from(versionsBySystem.values())));
      if (uniqueVersions.length <= 1) continue;
      const rowRef = currentRows.find((row) => row.module === moduleId);
      const moduleTitle = rowRef?.title || moduleId;
      const moduleWorlds = Array.from(moduleUsage.get(moduleId)?.worlds || new Set<string>()).sort();
      const versions = Array.from(versionsBySystem.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([systemId, version]) => ({
          systemId,
          version,
          worlds: Array.from(systemWorlds.get(systemId) || new Set<string>()).sort(),
        }));
      out[moduleId] = {
        moduleId,
        moduleTitle,
        contextLabel: `Current Foundry ${currentFoundryVersion}`,
        versionsBySystem: versions,
        moduleWorlds,
      };
      out[moduleId.toLowerCase()] = out[moduleId];
    }
    return out;
  }, [planningRowsByFoundry, currentFoundryVersion, currentSystemVersionById, currentRows, moduleUsage, systemWorlds]);

  const planningSystemUpgradeConflictByModule = useMemo(() => {
    const out: Record<string, ConflictDetail> = {};
    const foundryRows = planningRowsByFoundry[planningFoundryFilter] || [];
    if (foundryRows.length === 0 || !planningSystemVersionFilter) return out;
    const byModule = new Map<string, Map<string, string>>();
    for (const row of foundryRows) {
      const moduleId = asString(row.module).trim();
      const suggestedVersion = asString(row.recommendedVersion).trim();
      if (!moduleId || !hasConcreteValue(suggestedVersion)) continue;
      const targetVersion = asString(row.targetVersion).trim();
      if (targetVersion !== planningSystemVersionFilter) continue;
      for (const systemId of row.relatedSystems) {
        const sid = asString(systemId).trim();
        if (!sid || sid === "unused") continue;
        const moduleMap = byModule.get(moduleId) || new Map<string, string>();
        moduleMap.set(sid, suggestedVersion);
        byModule.set(moduleId, moduleMap);
      }
    }
    for (const [moduleId, versionsBySystem] of byModule.entries()) {
      const uniqueVersions = Array.from(new Set(Array.from(versionsBySystem.values())));
      if (uniqueVersions.length <= 1) continue;
      const rowRef = selectedPlanningRows.find((row) => row.module === moduleId);
      const moduleTitle = rowRef?.title || moduleId;
      const moduleWorlds = Array.from(moduleUsage.get(moduleId)?.worlds || new Set<string>()).sort();
      const versions = Array.from(versionsBySystem.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([systemId, version]) => ({
          systemId,
          version,
          worlds: Array.from(systemWorlds.get(systemId) || new Set<string>()).sort(),
        }));
      out[moduleId] = {
        moduleId,
        moduleTitle,
        contextLabel: `Planning Foundry ${planningFoundryFilter} + System ${planningSystemVersionFilter}`,
        versionsBySystem: versions,
        moduleWorlds,
      };
      out[moduleId.toLowerCase()] = out[moduleId];
    }
    return out;
  }, [planningRowsByFoundry, planningFoundryFilter, planningSystemVersionFilter, selectedPlanningRows, moduleUsage, systemWorlds]);

  const conflictTooltipFromDetail = (detail: ConflictDetail | undefined): string => {
    if (!detail) return "";
    const pairs = detail.versionsBySystem.map((entry) => `${entry.systemId}: ${entry.version}`);
    return `different suggested versions across systems (${pairs.join(" | ")})`;
  };
  const planningMatrixSystemId = activePlanningSystemId || planningSystemIds[0] || "";
  const planningMatrixFoundryVersions = useMemo(
    () => planningFoundryBuckets.map((bucket) => bucket.key).sort(compareVersionAsc),
    [planningFoundryBuckets]
  );
  const planningMatrixSystemVersions = useMemo(() => {
    if (!planningMatrixSystemId) return [] as string[];
    const installed = asString(currentSystemVersionById[planningMatrixSystemId]).trim();
    const isAtOrAboveInstalled = (version: string): boolean => {
      const candidate = asString(version).trim();
      if (!candidate) return false;
      if (!installed) return true;
      return compareVersionAsc(candidate, installed) >= 0;
    };
    const versions = new Set<string>();
    const selectedTargetFromIndex = planningTargetsByFoundry[planningFoundryFilter];
    const selectedTarget = selectedTargetFromIndex && typeof selectedTargetFromIndex === "object"
      ? (selectedTargetFromIndex as Record<string, unknown>)
      : planningTargets.find((target) => asString(target.foundryVersion).trim() === planningFoundryFilter);
    const selectedSystemRows = selectedTarget
      ? (asArray(selectedTarget.systemRows).length > 0 ? asArray(selectedTarget.systemRows) : asArray(selectedTarget.systems))
      : [];
    const selectedSystemRow = selectedSystemRows.find((row) => asString(row.systemId).trim() === planningMatrixSystemId);
    for (const version of extractSystemTargetVersions(selectedSystemRow || {})) {
      if (isAtOrAboveInstalled(version)) versions.add(version);
    }
    for (const rows of Object.values(planningRowsByFoundry)) {
      for (const row of rows) {
        const targetVersion = asString(row.targetVersion).trim();
        if (!targetVersion) continue;
        if ((row.relatedSystems.length === 0 || row.relatedSystems.includes(planningMatrixSystemId)) && isAtOrAboveInstalled(targetVersion)) {
          versions.add(targetVersion);
        }
      }
    }
    if (installed) versions.add(installed);
    return Array.from(versions).sort(compareVersionAsc);
  }, [planningRowsByFoundry, planningMatrixSystemId, currentSystemVersionById, planningTargets, planningTargetsByFoundry, planningFoundryFilter]);
  const planningMatrixColumnKeys = useMemo(() => {
    if (planningAllSystemsMode) return ["__all__"] as string[];
    return planningMatrixSystemVersions;
  }, [planningAllSystemsMode, planningMatrixSystemVersions]);
  const planningMatrixCells = useMemo(() => {
    const out: Record<string, { rawTotal: number; excluded: number; total: number; ready: number; update: number; blocked: number; missing: number; readinessPct: number }> = {};
    const buildRowsForCell = (foundryVersion: string, systemVersion: string): PlanningRow[] => {
      const targetSystemForCell = activePlanningSystemId || planningMatrixSystemId || "";
      const foundryRows = planningRowsByFoundry[foundryVersion] || [];
      const plannerRows = foundryRows
        .filter((row) => {
          if (asString(row.targetVersion).trim() !== systemVersion) return false;
          if (!activePlanningSystemId) return true;
          if (row.relatedSystems.length === 0) return true;
          return row.relatedSystems.includes(activePlanningSystemId);
        })
        .map((row) => normalizePlanningRowForTarget(row, foundryVersion, systemVersion, targetSystemForCell));
      const projectedFallback = currentRows
        .map((row) => normalizePlanningRowForTarget(row, foundryVersion, systemVersion, targetSystemForCell));
      const mergedByModule = new Map<string, PlanningRow>();
      for (const row of plannerRows) mergedByModule.set(row.module, row);
      for (const row of projectedFallback) {
        if (!mergedByModule.has(row.module)) mergedByModule.set(row.module, row);
      }
      const installedRows = Array.from(mergedByModule.values()).filter((row) => hasConcreteValue(row.installedVersion));
      return installedRows;
    };
    const buildRowsForAllSystemsFoundryOnly = (foundryVersion: string): PlanningRow[] => {
      const rows = projectPlanningRowsFoundryAggregate(planningRowsByFoundry[foundryVersion] || [], foundryVersion);
      return planningIncludeUnused ? rows : rows.filter((row) => String(row.system || "").trim().toLowerCase() !== "unused");
    };
    for (const foundryVersion of planningMatrixFoundryVersions) {
      for (const columnKey of planningMatrixColumnKeys) {
        const isAllSystemsCell = planningAllSystemsMode && columnKey === "__all__";
        const targetSystemForCell = activePlanningSystemId || planningMatrixSystemId || "";
        const mergedRows = isAllSystemsCell ? buildRowsForAllSystemsFoundryOnly(foundryVersion) : buildRowsForCell(foundryVersion, columnKey);
        const allInstalledRows = (() => {
          if (isAllSystemsCell) return buildRowsForAllSystemsFoundryOnly(foundryVersion);
          const plannerRows = (planningRowsByFoundry[foundryVersion] || [])
            .filter((row) => asString(row.targetVersion).trim() === columnKey)
            .map((row) => normalizePlanningRowForTarget(row, foundryVersion, columnKey, targetSystemForCell));
          const projectedFallback = currentRows.map((row) => normalizePlanningRowForTarget(row, foundryVersion, columnKey, targetSystemForCell));
          const mergedByModule = new Map<string, PlanningRow>();
          for (const row of plannerRows) mergedByModule.set(row.module, row);
          for (const row of projectedFallback) {
            if (!mergedByModule.has(row.module)) mergedByModule.set(row.module, row);
          }
          return Array.from(mergedByModule.values()).filter((row) => hasConcreteValue(row.installedVersion));
        })();
        const rawTotal = allInstalledRows.length;
        const rows = mergedRows;
        const total = rows.length;
        const excluded = 0;
        const missing = rows.filter((row) => row.hasMissingDependencies).length;
        const blocked = rows.filter((row) => !row.hasMissingDependencies && derivePlanningEffectiveState(row) === "blocked").length;
        const update = rows.filter((row) => derivePlanningEffectiveState(row) === "update").length;
        const ready = rows.filter((row) => derivePlanningEffectiveState(row) === "ready" && !row.hasMissingDependencies).length;
        const readinessPct = total > 0 ? Math.round(((ready + update) / total) * 100) : 0;
        out[`${foundryVersion}::${columnKey}`] = { rawTotal, excluded, total, ready, update, blocked, missing, readinessPct };
      }
    }
    return out;
  }, [planningRowsByFoundry, planningMatrixFoundryVersions, planningMatrixColumnKeys, planningAllSystemsMode, activePlanningSystemId, planningMatrixSystemId, currentRows, dependencySuggestionByModule, resolvedSourceByModule, selectedPlanningSuggestContextKey, resolvedSourceByContext, planningVersionBySystem, planningSystemVersionFilter, selectedPlanningVersionBucket, planningFoundryFilter, planningContextRowsByModule, planningIncludeUnused]);

  useEffect(() => {
    if (tab !== "planning") return;
    if (!model) return;
    if (hydrationBusy) return;
    const candidateSourceByModule = new Map<string, { manifestUrl: string; projectUrl: string }>();
    for (const row of selectedPlanningRows) {
      const moduleId = asString(row.module).trim();
      if (!moduleId) continue;
      const source = sourceForRow(moduleSources, row.module, row.title);
      const fallback = sourceFromReleaseUrl(asString(row.releaseUrl));
      const manifestUrl = asString(source.manifestUrl) || fallback.manifestUrl;
      const projectUrl = asString(source.projectUrl) || fallback.projectUrl;
      if (!manifestUrl && !projectUrl) continue;
      if (hasConcreteValue(row.recommendedVersion) || hasConcreteValue(row.releaseUrl)) {
        const activeSystem = activePlanningSystemId || asString(row.relatedSystems?.[0] || "");
        const systemCompatMap = (row.systemCompatibility as Record<string, unknown> | undefined) || {};
        const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
        const systemTarget = activeSystem
          ? (planningVersionBySystem[activeSystem] || planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "")
          : (planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "");
        const foundryOk = versionWithin(row.compatibility, planningFoundryFilter);
        const systemOk = versionWithin(sysCompat, systemTarget);
        const contextMismatch = foundryOk === false || systemOk === false;
        if (!contextMismatch) continue;
      }
      const existing = candidateSourceByModule.get(moduleId);
      if (!existing) {
        candidateSourceByModule.set(moduleId, { manifestUrl, projectUrl });
        continue;
      }
      if (!existing.manifestUrl && manifestUrl) existing.manifestUrl = manifestUrl;
      if (!existing.projectUrl && projectUrl) existing.projectUrl = projectUrl;
    }
    const candidates = Array.from(candidateSourceByModule.keys()).filter((moduleId) => {
      const contextKey = `${selectedPlanningSuggestContextKey}::${moduleId}`;
      if (resolvedSourceByContext[contextKey]?.recommendedVersion || resolvedSourceByContext[contextKey]?.resolvedUrl) return false;
      if (resolvedAttemptedByContext[contextKey]) return false;
      return true;
    });
    if (candidates.length === 0) {
      setHydrationBusy(false);
      setPlanningHydrationProgress({ total: 0, done: 0 });
      return;
    }
    const runId = hydrationRunRef.current + 1;
    hydrationRunRef.current = runId;
    setHydrationBusy(true);
    setPlanningHydrationProgress({ total: candidates.length, done: 0 });
    void (async () => {
      const batchAll = candidates.map((moduleId) => {
        const source = candidateSourceByModule.get(moduleId) || { manifestUrl: "", projectUrl: "" };
        return {
          moduleId,
          manifestUrl: asString(source.manifestUrl),
          projectUrl: asString(source.projectUrl),
        };
      }).filter((item) => item.manifestUrl || item.projectUrl);
      if (batchAll.length === 0) {
        setHydrationBusy(false);
        setPlanningHydrationProgress({ total: 0, done: 0 });
        return;
      }
      const chunkSize = 8;
      let done = 0;
      for (let index = 0; index < batchAll.length; index += chunkSize) {
        if (hydrationRunRef.current !== runId) return;
      const chunk = batchAll.slice(index, index + chunkSize);
      const attemptedChunk: Record<string, boolean> = {};
      for (const item of chunk) {
        const moduleId = asString(item.moduleId);
        if (!moduleId) continue;
        attemptedChunk[`${selectedPlanningSuggestContextKey}::${moduleId}`] = true;
      }
        try {
          const payload = await api.suggestModulesBatch(chunk, selectedPlanningSuggestContext);
          if (hydrationRunRef.current !== runId) return;
          const rows = Array.isArray(payload.rows) ? payload.rows : [];
          const updates: Record<string, SuggestedResolution> = {};
          const byModule: Record<string, SuggestedResolution> = {};
          const attempted: Record<string, boolean> = { ...attemptedChunk };
          for (const row of rows) {
            const moduleId = asString(row?.moduleId);
            if (!moduleId) continue;
            const suggestion = (row?.suggestion || {}) as Record<string, unknown>;
            const recommendedVersion = asString(suggestion.recommendedVersion);
            const resolvedUrl = preferredUpdateUrlFromCandidates(
              asString(suggestion.releaseUrl),
              asString(suggestion.downloadUrl),
              asString(suggestion.projectUrl),
              asString(suggestion.manifestUrl)
            );
            const contextKey = `${selectedPlanningSuggestContextKey}::${moduleId}`;
            attempted[contextKey] = true;
            if (!recommendedVersion && !resolvedUrl) continue;
            const value = {
              recommendedVersion: recommendedVersion || undefined,
              resolvedUrl: resolvedUrl || undefined,
              compatibility: (suggestion.compatibility as Record<string, unknown> | undefined) || undefined,
              systemCompatibility: (suggestion.systemCompatibility as Record<string, unknown> | undefined) || undefined,
              hasDependencyUpdates: asArray(suggestion.dependencyActions).length > 0,
              isCompatible: typeof suggestion.isCompatible === "boolean" ? Boolean(suggestion.isCompatible) : undefined,
            };
            updates[contextKey] = value;
            byModule[moduleId] = value;
          }
          if (Object.keys(attempted).length > 0) {
            setResolvedAttemptedByContext((prev) => ({ ...prev, ...attempted }));
          }
          if (Object.keys(updates).length > 0) {
            setResolvedSourceByContext((prev) => ({ ...prev, ...updates }));
          }
          if (Object.keys(byModule).length > 0) {
            const normalized: Record<string, SuggestedResolution> = {};
            for (const [k, v] of Object.entries(byModule)) {
              const key = asString(k).trim();
              if (!key) continue;
              normalized[key] = v;
              normalized[key.toLowerCase()] = v;
            }
            setResolvedSourceByModule((prev) => ({ ...prev, ...normalized }));
          }
        } catch {
          // best-effort hydration; keep UI responsive
        } finally {
          if (Object.keys(attemptedChunk).length > 0) {
            setResolvedAttemptedByContext((prev) => ({ ...prev, ...attemptedChunk }));
          }
          done = Math.min(batchAll.length, done + chunk.length);
          setPlanningHydrationProgress({ total: batchAll.length, done });
        }
      }
      if (hydrationRunRef.current !== runId) return;
      setHydrationBusy(false);
      setPlanningHydrationProgress({ total: 0, done: 0 });
    })();
  }, [tab, model, hydrationBusy, moduleSources, selectedPlanningRows, selectedPlanningSuggestContext, selectedPlanningSuggestContextKey, resolvedSourceByContext, resolvedAttemptedByContext, activePlanningSystemId, planningVersionBySystem, planningSystemVersionFilter, selectedPlanningVersionBucket, planningFoundryFilter]);

  const filteredPlanning = useMemo(() => {
    const q = search.trim().toLowerCase();
    return selectedPlanningRows
      .filter((row) => {
        if (planningFilters.length === 0) return true;
        const effectiveState = derivePlanningEffectiveState(row);
        return planningFilters.some((filter) => {
          if (filter === "unused") return row.system === "unused";
          if (row.system === "unused") return false;
          if (filter === "blocked") return effectiveState === "blocked" || row.hasMissingDependencies;
          return effectiveState === filter;
        });
      })
      .filter((row) => (
        badgeFilterCodes.length === 0
          ? true
          : badgeFilterCodes.some((code) => planningBadgeCodesForRow(row).includes(code))
      ))
      .filter((row) => (q ? `${row.title} ${row.module} ${row.system} ${row.reason} ${row.targetVersion}`.toLowerCase().includes(q) : true));
  }, [selectedPlanningRows, planningFilters, badgeFilterCodes, search, dependencySuggestionByModule, resolvedSourceByModule, selectedPlanningSuggestContextKey, resolvedSourceByContext, activePlanningSystemId, planningVersionBySystem, planningSystemVersionFilter, selectedPlanningVersionBucket, planningFoundryFilter, planningContextRowsByModule]);

  const filteredBackups = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return backupRows;
    return backupRows.filter((row) =>
      `${asString(row.title)} ${asString(row.module)} ${String(row.backupCount || "")}`.toLowerCase().includes(q)
    );
  }, [backupRows, search]);

  const currentTableRows = useMemo<CurrentTableRow[]>(() => {
    const systemRows: Extract<CurrentTableRow, { kind: "system" }>[] = [];
    const includeCurrentSystemRow = (status: "update" | "ready"): boolean => {
      if (currentFilters.length === 0) return true;
      return currentFilters.some((filter) => {
        if (filter === "update") return status === "update";
        if (filter === "ready") return status === "ready";
        return false;
      });
    };
    if (selectedCurrentVersionBucket) {
      for (const systemId of selectedCurrentVersionBucket.systems) {
        const sys = currentSystems.find((entry) => asString(entry.systemId) === systemId);
        if (!sys) continue;
        const installedVersion = asString(sys.installedVersion) || asString((model?.installedSystemVersions || {})[systemId]);
        const targetVersion = selectedCurrentVersionBucket.key;
        const targetUrl = preferredUpdateUrlFromCandidates(asString(sys.targetUrl), asString(sys.releaseUrl), asString(sys.manifestUrl));
        const status: "update" | "ready" = selectedCurrentVersionBucket.isCurrent || targetVersion === installedVersion ? "ready" : "update";
        if (!includeCurrentSystemRow(status)) continue;
        systemRows.push({
          kind: "system",
          key: `system-${systemId}-${selectedCurrentVersionBucket.key}`,
          systemId,
          usedInWorlds: [],
          installedVersion: installedVersion || "-",
          targetVersion: targetVersion || "-",
          targetUrl,
          status,
          compatibility: (sys.compatibility as Record<string, unknown> | undefined) || {}
        });
      }
    }
    const moduleRows: Extract<CurrentTableRow, { kind: "module" }>[] = filteredCurrent
      .map((row) => ({
        kind: "module" as const,
        key: `${row.module}-${row.system}`,
        row: { ...row, state: deriveCurrentEffectiveState(row) }
      }))
      .sort((a, b) => compareSystemThenStatus(
        { system: a.row.system, state: a.row.state, hasMissingDependencies: a.row.hasMissingDependencies, title: a.row.title },
        { system: b.row.system, state: b.row.state, hasMissingDependencies: b.row.hasMissingDependencies, title: b.row.title },
      ));
    systemRows.sort((a, b) => a.systemId.localeCompare(b.systemId));
    return [...systemRows, ...moduleRows];
  }, [filteredCurrent, selectedCurrentVersionBucket, currentSystems, model?.installedSystemVersions, currentFilters, dependencySuggestionByModule, resolvedSourceByModule, selectedCurrentSuggestContextKey, resolvedSourceByContext, activeCurrentSystemId, currentVersionBySystem, currentSystemFilter, currentSystemVersionById, currentFoundryVersion]);

  const currentPage = paginate(currentTableRows, page, 12);
  const backupModules = backupRows.map((row) => asString(row.module)).filter(Boolean);
  const planningTableRows = useMemo<PlanningTableRow[]>(() => {
    const systemRows: Extract<PlanningTableRow, { kind: "system" }>[] = [];
    const includePlanningSystemRow = (_status: "update" | "ready"): boolean => true;
    const selectedTargetFromIndex = planningTargetsByFoundry[planningFoundryFilter];
    const selectedTarget = selectedTargetFromIndex && typeof selectedTargetFromIndex === "object"
      ? (selectedTargetFromIndex as Record<string, unknown>)
      : planningTargets.find((target) => asString(target.foundryVersion).trim() === planningFoundryFilter);
    const targetSystems = selectedTarget ? (asArray(selectedTarget.systemRows).length > 0 ? asArray(selectedTarget.systemRows) : asArray(selectedTarget.systems)) : [];
    if (planningAllSystemsMode) {
      for (const systemId of planningSystemIds) {
        const systemRef = currentSystems.find((entry) => asString(entry.systemId) === systemId);
        const targetRef = targetSystems.find((entry) => asString(entry.systemId).trim() === systemId);
        const installedVersion = asString(systemRef?.installedVersion) || asString((model?.installedSystemVersions || {})[systemId]);
        const targetVersion = asString(planningVersionBySystem[systemId] || installedVersion || asString(targetRef?.targetVersion) || asString(targetRef?.recommendedVersion) || asString(targetRef?.version));
        const targetUrl = preferredUpdateUrlFromCandidates(asString(targetRef?.targetUrl), asString(targetRef?.releaseUrl), asString(targetRef?.manifestUrl));
        const status: "update" | "ready" = (installedVersion && targetVersion && compareVersionAsc(targetVersion, installedVersion) > 0) ? "update" : "ready";
        if (!includePlanningSystemRow(status)) continue;
        systemRows.push({
          kind: "system",
          key: `planning-system-all-${planningFoundryFilter}-${systemId}-${targetVersion || "-"}`,
          systemId,
          usedInWorlds: [],
          installedVersion: installedVersion || "-",
          targetVersion: targetVersion || installedVersion || "-",
          targetUrl,
          status,
          compatibility: (targetRef?.compatibility as Record<string, unknown> | undefined) || {},
        });
      }
    } else if (selectedPlanningVersionBucket) {
      for (const systemId of selectedPlanningVersionBucket.systems) {
        if (activePlanningSystemId && systemId !== activePlanningSystemId) continue;
        const systemRef = currentSystems.find((entry) => asString(entry.systemId) === systemId);
        const targetRef = targetSystems.find((entry) => asString(entry.systemId).trim() === systemId);
        const installedVersion = asString(systemRef?.installedVersion) || asString((model?.installedSystemVersions || {})[systemId]);
        const targetVersion = selectedPlanningVersionBucket.key;
        const targetUrl = preferredUpdateUrlFromCandidates(asString(targetRef?.targetUrl), asString(targetRef?.releaseUrl), asString(targetRef?.manifestUrl));
        const status: "update" | "ready" = targetVersion === installedVersion ? "ready" : "update";
        if (!includePlanningSystemRow(status)) continue;
        systemRows.push({
          kind: "system",
          key: `planning-system-${planningFoundryFilter}-${systemId}-${targetVersion}`,
          systemId,
          usedInWorlds: [],
          installedVersion: installedVersion || "-",
          targetVersion: targetVersion || "-",
          targetUrl,
          status,
          compatibility: (targetRef?.compatibility as Record<string, unknown> | undefined) || {},
        });
      }
    }
    const moduleRows: Extract<PlanningTableRow, { kind: "module" }>[] = filteredPlanning
      .map((row) => ({
        kind: "module" as const,
        key: `planning-${planningFoundryFilter}-${row.targetVersion}-${row.module}-${row.system}`,
        row: { ...row, state: derivePlanningEffectiveState(row) },
      }))
      .sort((a, b) => compareSystemThenStatus(
        { system: a.row.system, state: a.row.state, hasMissingDependencies: a.row.hasMissingDependencies, title: a.row.title },
        { system: b.row.system, state: b.row.state, hasMissingDependencies: b.row.hasMissingDependencies, title: b.row.title },
      ));
    systemRows.sort((a, b) => a.systemId.localeCompare(b.systemId));
    return [...systemRows, ...moduleRows];
  }, [
    selectedPlanningVersionBucket,
    planningTargets,
    planningTargetsByFoundry,
    planningFoundryFilter,
    currentSystems,
    activePlanningSystemId,
    model?.installedSystemVersions,
    filteredPlanning,
    planningFilters,
    planningAllSystemsMode,
    planningSystemIds,
    planningVersionBySystem,
  ]);
  const planningPage = paginate(planningTableRows, page, 12);

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
      const value = String(suggestInput || "").trim();
      const lower = value.toLowerCase();
      const looksLikeManifest = lower.endsWith("/module.json") || lower.endsWith("/system.json") || lower.endsWith("/manifest.json");
      const payload = await api.suggestModule(
        value,
        undefined,
        "",
        { projectUrl: looksLikeManifest ? undefined : value }
      );
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
      const contextual = await api.suggestModule(
        contextualInput,
        selectedCurrentSuggestContext,
        moduleId,
        { projectUrl: projectUrl || undefined }
      );
      const suggestion = ((contextual.suggestion || saved.suggestion || {}) as Record<string, unknown>);
      const recommendedVersion = asString(suggestion.recommendedVersion);
      const resolvedUrl = preferredUpdateUrlFromCandidates(
        asString(suggestion.releaseUrl),
        asString(suggestion.downloadUrl),
        asString(suggestion.projectUrl),
        asString(suggestion.manifestUrl)
      );
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

  const refreshModuleVersions = async (row: ModuleRow, scope: "current" | "planning") => {
    const moduleId = asString(row.module).trim();
    if (!moduleId) return;
    const titleKey = asString(row.title).trim();
    const source = sourceForRow(moduleSources, moduleId, titleKey);
    const fallback = sourceFromReleaseUrl(asString(row.releaseUrl));
    const manifestUrl = asString(source.manifestUrl) || fallback.manifestUrl;
    const projectUrl = asString(source.projectUrl) || fallback.projectUrl;
    if (!manifestUrl && !projectUrl) {
      const message = `No source URL configured for ${moduleId}. Set URL first, then refresh versions.`;
      setError(message);
      setModuleRefreshStatusById((prev) => ({
        ...prev,
        [moduleId]: { kind: "error", message, retryable: false },
        [moduleId.toLowerCase()]: { kind: "error", message, retryable: false },
      }));
      return;
    }
    setError("");
    setModuleRefreshStatusById((prev) => {
      const next = { ...prev };
      delete next[moduleId];
      delete next[moduleId.toLowerCase()];
      return next;
    });
    setRefreshingModuleById((prev) => ({ ...prev, [moduleId]: true, [moduleId.toLowerCase()]: true }));
    try {
      const context = scope === "planning" ? selectedPlanningSuggestContext : selectedCurrentSuggestContext;
      const contextKeyBase = scope === "planning" ? selectedPlanningSuggestContextKey : selectedCurrentSuggestContextKey;
      const payload = await api.suggestModulesBatch(
        [{ moduleId, manifestUrl, projectUrl }],
        context,
        { forceRefresh: true }
      );
      const first = (payload.rows || [])[0] || {};
      const errorMessage = asString((first as Record<string, unknown>).error);
      if (errorMessage) {
        const errorCode = asString((first as Record<string, unknown>).errorCode);
        const hint = asString((first as Record<string, unknown>).hint);
        const retryable = asBool((first as Record<string, unknown>).retryable);
        const message = providerRefreshMessage(errorCode, errorMessage, hint);
        setError(message);
        setModuleRefreshStatusById((prev) => ({
          ...prev,
          [moduleId]: { kind: "error", message, retryable },
          [moduleId.toLowerCase()]: { kind: "error", message, retryable },
        }));
        return;
      }
      const suggestion = ((first as Record<string, unknown>).suggestion || {}) as Record<string, unknown>;
      const recommendedVersion = asString(suggestion.recommendedVersion);
      const resolvedUrl = preferredUpdateUrlFromCandidates(
        asString(suggestion.releaseUrl),
        asString(suggestion.downloadUrl),
        asString(suggestion.projectUrl),
        asString(suggestion.manifestUrl),
      );
      if (!recommendedVersion && !resolvedUrl) {
        setSuggestResult(`No new compatible release found for ${moduleId}.`);
      setModuleRefreshStatusById((prev) => ({
        ...prev,
        [moduleId]: { kind: "ok", message: "No new compatible release found.", retryable: true },
        [moduleId.toLowerCase()]: { kind: "ok", message: "No new compatible release found.", retryable: true },
      }));
      return;
    }
      const update: SuggestedResolution = {
        recommendedVersion: recommendedVersion || undefined,
        resolvedUrl: resolvedUrl || undefined,
        compatibility: (suggestion.compatibility as Record<string, unknown> | undefined) || undefined,
        systemCompatibility: (suggestion.systemCompatibility as Record<string, unknown> | undefined) || undefined,
        hasDependencyUpdates: asArray(suggestion.dependencyActions).length > 0,
        isCompatible: typeof suggestion.isCompatible === "boolean" ? Boolean(suggestion.isCompatible) : undefined,
      };
      const contextKey = `${contextKeyBase}::${moduleId}`;
      setResolvedSourceByContext((prev) => ({ ...prev, [contextKey]: update }));
      setResolvedSourceByModule((prev) => ({
        ...prev,
        [moduleId]: update,
        [moduleId.toLowerCase()]: update,
      }));
      setSuggestResult(`Refreshed versions for ${moduleId}. Suggested: ${recommendedVersion || "?"}`);
      setModuleRefreshStatusById((prev) => ({
        ...prev,
        [moduleId]: { kind: "ok", message: `Suggested ${recommendedVersion || "?"}`, retryable: true },
        [moduleId.toLowerCase()]: { kind: "ok", message: `Suggested ${recommendedVersion || "?"}`, retryable: true },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : `Could not refresh versions for ${moduleId}.`;
      setError(message);
      setModuleRefreshStatusById((prev) => ({
        ...prev,
        [moduleId]: { kind: "error", message, retryable: true },
        [moduleId.toLowerCase()]: { kind: "error", message, retryable: true },
      }));
    } finally {
      setRefreshingModuleById((prev) => {
        const next = { ...prev };
        delete next[moduleId];
        delete next[moduleId.toLowerCase()];
        return next;
      });
    }
  };

  const refreshSystemVersions = async (systemId: string) => {
    const cleanId = asString(systemId).trim();
    if (!cleanId) return;
    if (!foundryConfigured) {
      setError("Configure Foundry path first.");
      return;
    }
    setError("");
    setSuggestResult(`Refreshing versions for system ${cleanId}...`);
    await submitAndWatch("dry-run", {
      batchSize: 10,
      forceRefresh: true,
      refreshTarget: { kind: "system", id: cleanId },
    });
  };

  const openImportPlanPicker = () => {
    importPlanInputRef.current?.click();
  };

  const importPlanFromFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.target;
    const file = input.files?.[0];
    if (!file) return;
    try {
      const content = await file.text();
      JSON.parse(content);
      await submitAndWatch("override-from-plan", {
        planContent: content,
        planFilename: file.name,
        profile: importProfile,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid plan file.");
    } finally {
      input.value = "";
    }
  };

  const downloadJsonFile = (filename: string, payload: unknown) => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  const exportFilteredUpdatePlan = async () => {
    try {
      setUiBusyMessage("Exporting update plan...");
      const snapshot = await api.exportSnapshot("", true);
      const currentRowsExport = currentTableRows.map((item) => {
        if (item.kind === "system") {
          return {
            kind: "system",
            systemId: item.systemId,
            status: item.status,
            updatePath: item.status === "ready" ? (item.installedVersion || "-") : `${item.installedVersion || "-"} -> ${item.targetVersion || "-"}`,
            installedVersion: item.installedVersion || "-",
            targetVersion: item.targetVersion || "-",
            targetUrl: item.targetUrl || "",
          };
        }
        return {
          kind: "module",
          moduleId: item.row.module,
          title: item.row.title,
          status: item.row.state,
          hasMissingDependencies: item.row.hasMissingDependencies,
          reason: item.row.reason,
          installedVersion: item.row.installedVersion || "-",
          recommendedVersion: item.row.recommendedVersion || "-",
          releaseUrl: preferredUpdateUrlFromCandidates(item.row.releaseUrl),
          relatedSystems: item.row.relatedSystems,
          usedInWorlds: item.row.usedInWorlds,
        };
      });
      const planningRowsExport = planningTableRows.map((item) => {
        if (item.kind === "system") {
          return {
            kind: "system",
            systemId: item.systemId,
            status: item.status,
            updatePath: item.status === "ready" ? (item.installedVersion || "-") : `${item.installedVersion || "-"} -> ${item.targetVersion || "-"}`,
            installedVersion: item.installedVersion || "-",
            targetVersion: item.targetVersion || "-",
            targetUrl: item.targetUrl || "",
          };
        }
        return {
          kind: "module",
          moduleId: item.row.module,
          title: item.row.title,
          status: item.row.state,
          hasMissingDependencies: item.row.hasMissingDependencies,
          reason: item.row.reason,
          installedVersion: item.row.installedVersion || "-",
          recommendedVersion: item.row.recommendedVersion || "-",
          releaseUrl: preferredUpdateUrlFromCandidates(item.row.releaseUrl),
          targetVersion: item.row.targetVersion || "-",
          relatedSystems: item.row.relatedSystems,
        };
      });
      const exportPayload = {
        generatedAt: new Date().toISOString(),
        baseReportGeneratedAt: model?.generatedAt || "",
        snapshot: {
          ...snapshot,
          data: snapshot.snapshotData || null,
        },
        current: {
          foundryVersion: currentFoundryVersion || "",
          activeSystemId: activeCurrentSystemId || "",
          selectedSystemVersion: currentSystemFilter || "",
          filters: currentFilters,
          search: search.trim(),
          rows: currentRowsExport,
          counts: {
            total: currentRowsExport.length,
            blockedOrMissing: currentRowsExport.filter((r) => (r as { status?: string; hasMissingDependencies?: boolean }).status === "blocked" || Boolean((r as { hasMissingDependencies?: boolean }).hasMissingDependencies)).length,
            update: currentRowsExport.filter((r) => (r as { status?: string }).status === "update").length,
            ready: currentRowsExport.filter((r) => (r as { status?: string; hasMissingDependencies?: boolean }).status === "ready" && !Boolean((r as { hasMissingDependencies?: boolean }).hasMissingDependencies)).length,
          },
        },
        destiny: {
          foundryVersion: planningFoundryFilter || "",
          activeSystemId: activePlanningSystemId || "",
          selectedSystemVersion: planningSystemVersionFilter || "",
          filters: planningFilters,
          search: search.trim(),
          rows: planningRowsExport,
          counts: {
            total: planningRowsExport.length,
            blockedOrMissing: planningRowsExport.filter((r) => (r as { status?: string; hasMissingDependencies?: boolean }).status === "blocked" || Boolean((r as { hasMissingDependencies?: boolean }).hasMissingDependencies)).length,
            update: planningRowsExport.filter((r) => (r as { status?: string }).status === "update").length,
            ready: planningRowsExport.filter((r) => (r as { status?: string; hasMissingDependencies?: boolean }).status === "ready" && !Boolean((r as { hasMissingDependencies?: boolean }).hasMissingDependencies)).length,
          },
        },
      };
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      downloadJsonFile(`modulator-update-plan-${stamp}.json`, exportPayload);
      setSuggestResult(`Update plan exported. Snapshot modules: ${Number(snapshot.modulesCount || 0)}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not export update plan.");
    } finally {
      setUiBusyMessage("");
    }
  };

  const renderSystemTableRow = (item: {
    key: string;
    systemId: string;
    status: "update" | "ready";
    installedVersion: string;
    targetVersion: string;
    targetUrl: string;
    compatibility: Record<string, unknown>;
  }) => {
    const rowClassName = item.status === "update" ? "row-status-update" : "row-status-ready";
    const updatePathCore: ReactNode = item.status === "ready"
      ? (item.installedVersion || "-")
      : <>{(item.installedVersion || "-")} {" \u2192 "} {item.targetUrl ? <a href={item.targetUrl} target="_blank" rel="noreferrer">{(item.targetVersion || "-")}</a> : (item.targetVersion || "-")}</>;
    const updatePathCell = (
      <UpdatePathWithRefresh
        content={updatePathCore}
        refreshing={Boolean(job)}
        disabled={actionBusy || Boolean(job) || !foundryConfigured}
        title="Refresh system versions from provider"
        onRefresh={() => void refreshSystemVersions(item.systemId)}
      />
    );
    return (
      <tr key={item.key} className={rowClassName}>
        <td>{item.systemId} <small>(system)</small></td>
        <td>{updatePathCell}</td>
        <td>{reasonBadges(item.status === "update" ? "Update suggested for this system." : "No system update required.", item.compatibility, false, true, null, undefined, false, undefined)}</td>
        <td style={{ textAlign: "right" }}>
          {item.status === "update"
            ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button>
            : <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>}
        </td>
      </tr>
    );
  };

  const renderModuleTableRow = (params: {
    key: string;
    row: ModuleRow;
    updatePathCell: ReactNode;
    actionsCell: ReactNode;
    foundryOk: boolean | null;
    systemOk: boolean | null;
    sysCompat?: Record<string, unknown>;
    showSystemBadge: boolean;
    foundryTarget: string;
    systemTarget: string;
    restrictedSystemIds: string[];
    systemUpgradeConflictTooltip?: string;
    onSystemUpgradeConflictClick?: (() => void) | null;
  }) => {
    const systemKey = String(params.row.system || "").trim().toLowerCase();
    const rowClassName = systemKey === "unused"
      ? "row-status-unused"
      : (params.row.hasMissingDependencies || params.row.state === "blocked")
        ? "row-status-blocked"
        : params.row.state === "update"
          ? "row-status-update"
          : "row-status-ready";
    return (
    <tr key={params.key} className={rowClassName}>
      <td>
        {params.row.hasMissingDependencies ? <span title={missingDependencyLabel(params.row.reason)} style={{ color: "#fbbf24", fontWeight: 800, marginRight: 6 }}>!</span> : null}
        <span title={params.row.module || "unknown"}>{(params.row.title || "Unknown module")}</span>
        {(() => {
          const moduleKey = asString(params.row.module).trim();
          const status = moduleRefreshStatusById[moduleKey] || moduleRefreshStatusById[moduleKey.toLowerCase()];
          if (!status) return null;
          return (
            <small
              title={status.message}
              style={{ display: "block", marginTop: 2, color: status.kind === "error" ? "#fca5a5" : "#93c5fd" }}
            >
              {status.message}{status.kind === "error" && status.retryable ? " (retry available)" : ""}
            </small>
          );
        })()}
      </td>
      <td>{params.updatePathCell}</td>
      <td>{reasonBadges(params.row.reason || "", params.row.compatibility, params.row.hasMissingDependencies, params.foundryOk, params.systemOk, params.sysCompat, params.showSystemBadge, params.row.forcedCompatibility, params.foundryTarget, params.systemTarget, params.restrictedSystemIds, params.systemUpgradeConflictTooltip, params.onSystemUpgradeConflictClick || null)}</td>
      <td style={{ textAlign: "right" }}>{params.actionsCell}</td>
    </tr>
  );
  };

  const renderCurrentModuleRow = (item: Extract<CurrentTableRow, { kind: "module" }>) => {
    const moduleKey = asString(item.row.module).trim();
    const titleKey = asString(item.row.title).trim();
    const source = sourceForRow(moduleSources, moduleKey, titleKey);
    const depSuggestion = dependencySuggestionByModule[moduleKey] || dependencySuggestionByModule[moduleKey.toLowerCase()] || dependencySuggestionByModule[titleKey] || dependencySuggestionByModule[titleKey.toLowerCase()] || {};
    const moduleSuggestion = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()] || resolvedSourceByModule[titleKey] || resolvedSourceByModule[titleKey.toLowerCase()] || {};
    const rowSourceFallback = sourceFromReleaseUrl(asString(item.row.releaseUrl));
    const canRefreshVersions = hasSourceUrls(source) || Boolean(rowSourceFallback.manifestUrl || rowSourceFallback.projectUrl);
    const refreshing = Boolean(refreshingModuleById[moduleKey] || refreshingModuleById[moduleKey.toLowerCase()]);
    const refreshStatus = moduleRefreshStatusById[moduleKey] || moduleRefreshStatusById[moduleKey.toLowerCase()];
    const contextualRecommended = item.row.recommendedVersion || "-";
    const contextualUrl = item.row.releaseUrl || "";
    const contextKey = `${selectedCurrentSuggestContextKey}::${moduleKey}`;
    const hydratedRecommended = resolvedSourceByContext[contextKey]?.recommendedVersion || "";
    const hydratedUrl = resolvedSourceByContext[contextKey]?.resolvedUrl || "";
    const dependencyRecommended = asString(depSuggestion.recommendedVersion);
    const dependencyUrl = asString(depSuggestion.releaseUrl);
    const moduleRecommended = asString(moduleSuggestion.recommendedVersion);
    const moduleUrl = asString(moduleSuggestion.resolvedUrl);
    const conflictDetailRow = currentSystemUpgradeConflictByModule[moduleKey]
      || currentSystemUpgradeConflictByModule[moduleKey.toLowerCase()];
    const systemUpgradeConflictTooltip = conflictTooltipFromDetail(conflictDetailRow);
    const activeSystem = activeCurrentSystemId || item.row.relatedSystems[0] || "";
    const systemTarget = activeSystem
      ? (currentVersionBySystem[activeSystem] || currentSystemFilter || selectedCurrentVersionBucket?.key || currentSystemVersionById[activeSystem] || "")
      : (currentSystemFilter || selectedCurrentVersionBucket?.key || "");
    const systemCompatMap = (item.row.systemCompatibility as Record<string, unknown> | undefined) || {};
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const restrictedIds = systemRestrictionIds(systemCompatMap);
    const showSystemBadge = restrictedIds.length > 0;
    const foundryOk = versionWithin(item.row.compatibility, currentFoundryVersion);
    const systemOk = versionWithin(sysCompat, systemTarget);
    const staleContextValue = foundryOk === false || systemOk === false;
    const recommendedCandidates = staleContextValue
      ? [hydratedRecommended, moduleRecommended, dependencyRecommended, contextualRecommended]
      : [contextualRecommended, hydratedRecommended, moduleRecommended, dependencyRecommended];
    const urlCandidates = staleContextValue
      ? [hydratedUrl, moduleUrl, dependencyUrl, contextualUrl, asString(source.projectUrl), asString(source.manifestUrl)]
      : [contextualUrl, hydratedUrl, moduleUrl, dependencyUrl, asString(source.projectUrl), asString(source.manifestUrl)];
    const effectiveRecommended = selectPreferredRecommended(recommendedCandidates, item.row.installedVersion);
    const effectiveUrl = preferredUpdateUrlFromCandidates(...urlCandidates);
    const unresolvedPath = !effectiveUrl && !effectiveRecommended;
    const pendingResolve = unresolvedPath && hydrationBusy && hasSourceUrls(source);
    const hasInstalled = Boolean(asString(item.row.installedVersion).trim() && asString(item.row.installedVersion).trim() !== "-");
    const hasCompatFailure = foundryOk === false || systemOk === false;
    const reason404 = isNotFoundReason(item.row.reason);
    const isUnusedRow = String(item.row.system || "").trim().toLowerCase() === "unused";
    const foundryFollowUpOnly = hasVerifiedLaterThanTarget(item.row.compatibility, currentFoundryVersion);
    const systemFollowUpOnly = hasVerifiedLaterThanTarget(sysCompat, systemTarget);
    const softCompatibleWindow = (
      (foundryOk !== false && (foundryFollowUpOnly || hasLooseMaxCompatibility(item.row.compatibility)))
      || (
        (!activeCurrentSystemId || showSystemBadge)
        && systemOk !== false
        && (systemFollowUpOnly || hasLooseMaxCompatibility(sysCompat))
      )
    );
    const backendBlockedSoftCase = item.row.state === "blocked"
      && !item.row.hasMissingDependencies
      && !hasCompatFailure
      && softCompatibleWindow;
    const effectiveState: ModuleRow["state"] = (item.row.state === "blocked" || item.row.hasMissingDependencies || hasCompatFailure)
      ? (backendBlockedSoftCase ? "ready" : "blocked")
      : (!hasInstalled && (effectiveRecommended || effectiveUrl)
        ? "update"
        : (hasInstalled && effectiveRecommended && compareVersionAsc(effectiveRecommended, item.row.installedVersion) > 0
          ? "update"
          : "ready"));
    const displayRow: ModuleRow = { ...item.row, state: effectiveState };
    const foundryMinLowerThanTarget = hasMinimumLowerThanTarget(item.row.compatibility, currentFoundryVersion);
    const systemMinLowerThanTarget = hasMinimumLowerThanTarget(sysCompat, systemTarget);
    const compatForceEligible = canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: hasInstalled,
      hasMissingDependencies: item.row.hasMissingDependencies,
      foundryCompatible: foundryOk,
      systemCompatible: systemOk,
      foundryFollowUpOnly,
      systemFollowUpOnly,
      // With "All Systems" active, don't decide force-compat from one arbitrary system target.
      allowSystemScopedCheck: Boolean(activeCurrentSystemId),
      reason: item.row.reason || "",
    });
    const allowForceCompatibility = compatForceEligible
      && !item.row.hasMissingDependencies
      && !reason404
      && (foundryMinLowerThanTarget || systemMinLowerThanTarget);
    const showArrow = !hasInstalled
      ? Boolean(effectiveRecommended || effectiveUrl)
      : Boolean(effectiveRecommended && compareVersionAsc(effectiveRecommended, item.row.installedVersion) > 0);
    const updatePathCore: ReactNode = (!showArrow || (effectiveState === "ready" && !item.row.hasMissingDependencies))
      ? (item.row.installedVersion || "-")
      : (unresolvedPath ? (pendingResolve ? "Loading..." : "?") : <>{(item.row.installedVersion || "-")} {" \u2192 "} {effectiveUrl ? <a href={effectiveUrl} target="_blank" rel="noreferrer">{effectiveRecommended || "?"}</a> : (effectiveRecommended || "?")}</>);
    const updatePathCell = (
      <UpdatePathWithRefresh
        content={updatePathCore}
        hasError={refreshStatus?.kind === "error"}
        refreshing={refreshing}
        disabled={actionBusy || refreshing || !canRefreshVersions}
        title={canRefreshVersions ? "Refresh versions from source (GitHub/GitLab)" : "Set source URL first to refresh versions"}
        onRefresh={() => void refreshModuleVersions(item.row, "current")}
      />
    );
    const cautionReady = effectiveState === "ready" && !item.row.hasMissingDependencies && (
      foundryFollowUpOnly
      || (Boolean(activeCurrentSystemId) && systemFollowUpOnly)
    );
    const actionsCell = (
      <div style={{ display: "inline-flex", gap: 6, flexWrap: "nowrap" }}>
        {cautionReady ? (
          <span
            className="btn"
            aria-disabled="true"
            title="Ready with follow-up suggestion for newer verified version."
            style={{ background: "#f59e0b", color: "#111827", cursor: "default", pointerEvents: "none" }}
          >
            Ready
          </span>
        ) : (item.row.hasMissingDependencies || effectiveState === "blocked") && !allowForceCompatibility && !effectiveUrl && !effectiveRecommended ? (
          <>
            <button className="btn" style={{ background: "#f59e0b", color: "#111827", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 36, padding: 0 }} title="Find Module" aria-label="Find Module" onClick={() => findSourceForModule(item.row.module, item.row.title)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></button>
            <button className="btn secondary" onClick={() => void setModuleSource(item.row.module)}>Set URL</button>
          </>
        ) : allowForceCompatibility ? (
          <button
            className="btn secondary"
            style={{ background: "#f59e0b", color: "#111827" }}
            disabled={actionBusy || !foundryConfigured}
            title="Force compatibility for this installed module (Current only)."
            onClick={() => void submitAndWatch("force-compat", { modules: [item.row.module], targetVersion: currentFoundryVersion })}
          >
            Force Compatibility
          </button>
        ) : !hasInstalled && (effectiveUrl || effectiveRecommended) ? (
          <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Install</button>
        ) : effectiveState === "update" ? (
          <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void submitAndWatch("apply", { modules: [item.row.module], batchSize: 10 })}>Update</button>
        ) : effectiveState === "blocked" ? (
          <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled>Blocked</button>
        ) : isUnusedRow ? (
          <span className="btn" aria-disabled="true" style={{ background: "#f59e0b", color: "#111827", cursor: "default", pointerEvents: "none" }}>Ready</span>
        ) : (
          <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>
        )}
      </div>
    );
    return renderModuleTableRow({
      key: item.key,
      row: displayRow,
      updatePathCell,
      actionsCell,
      foundryOk,
      systemOk,
      sysCompat,
      showSystemBadge,
      foundryTarget: currentFoundryVersion,
      systemTarget,
      restrictedSystemIds: restrictedIds,
      systemUpgradeConflictTooltip,
      onSystemUpgradeConflictClick: conflictDetailRow ? () => setConflictDetail(conflictDetailRow) : null,
    });
  };

  const renderPlanningModuleRow = (item: Extract<PlanningTableRow, { kind: "module" }>) => {
    const row = item.row;
    const isUnusedRow = String(row.system || "").trim().toLowerCase() === "unused";
    const moduleKey = asString(row.module).trim();
    const titleKey = asString(row.title).trim();
    const source = sourceForRow(moduleSources, moduleKey, titleKey);
    const depSuggestion = dependencySuggestionByModule[moduleKey] || dependencySuggestionByModule[moduleKey.toLowerCase()] || dependencySuggestionByModule[titleKey] || dependencySuggestionByModule[titleKey.toLowerCase()] || {};
    const moduleSuggestion = resolvedSourceByModule[moduleKey] || resolvedSourceByModule[moduleKey.toLowerCase()] || resolvedSourceByModule[titleKey] || resolvedSourceByModule[titleKey.toLowerCase()] || {};
    const rowSourceFallback = sourceFromReleaseUrl(asString(item.row.releaseUrl));
    const canRefreshVersions = hasSourceUrls(source) || Boolean(rowSourceFallback.manifestUrl || rowSourceFallback.projectUrl);
    const refreshing = Boolean(refreshingModuleById[moduleKey] || refreshingModuleById[moduleKey.toLowerCase()]);
    const refreshStatus = moduleRefreshStatusById[moduleKey] || moduleRefreshStatusById[moduleKey.toLowerCase()];
    const contextKey = `${selectedPlanningSuggestContextKey}::${moduleKey}`;
    const hydratedRecommended = resolvedSourceByContext[contextKey]?.recommendedVersion || "";
    const hydratedUrl = resolvedSourceByContext[contextKey]?.resolvedUrl || "";
    const precomputedContext = planningContextRowsByModule[moduleKey] || planningContextRowsByModule[moduleKey.toLowerCase()] || {};
    const precomputedRecommended = asString((precomputedContext as Record<string, unknown>).recommendedVersion);
    const dependencyRecommended = asString(depSuggestion.recommendedVersion);
    const dependencyUrl = asString(depSuggestion.releaseUrl);
    const moduleRecommended = asString(moduleSuggestion.recommendedVersion);
    const moduleUrl = asString(moduleSuggestion.resolvedUrl);
    const conflictDetailRow = planningSystemUpgradeConflictByModule[moduleKey]
      || planningSystemUpgradeConflictByModule[moduleKey.toLowerCase()];
    const systemUpgradeConflictTooltip = conflictTooltipFromDetail(conflictDetailRow);
    const rowRecommended = asString(row.recommendedVersion);
    const rowUrl = asString(row.releaseUrl);
    const activeSystem = activePlanningSystemId || asString(row.relatedSystems?.[0] || "");
    const effectivePlanningSystemTarget = activeSystem
      ? (planningVersionBySystem[activeSystem] || planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "")
      : (planningSystemVersionFilter || selectedPlanningVersionBucket?.key || row.targetVersion || "");
    const hydratedContext = resolvedSourceByContext[contextKey] || {};
    const hydratedCompatibility = hydratedContext.compatibility as Record<string, unknown> | undefined;
    const hydratedSystemCompatibility = hydratedContext.systemCompatibility as Record<string, unknown> | undefined;
    const effectiveCompatibility: Record<string, unknown> = hasCompatibilityMetadata(hydratedCompatibility)
      ? (hydratedCompatibility || {})
      : (row.compatibility || {});
    const effectiveSystemCompatibility: Record<string, unknown> = (hydratedSystemCompatibility && Object.keys(hydratedSystemCompatibility).length > 0)
      ? hydratedSystemCompatibility
      : (row.systemCompatibility || {});
    const systemCompatMap = (effectiveSystemCompatibility as Record<string, unknown> | undefined) || {};
    const sysCompat = compatibilityForSystem(systemCompatMap, activeSystem);
    const restrictedIds = systemRestrictionIds(systemCompatMap);
    const showSystemBadge = restrictedIds.length > 0;
    const foundryOk = versionWithin(effectiveCompatibility, planningFoundryFilter);
    const systemOk = versionWithin(sysCompat, effectivePlanningSystemTarget);
    const recommendedCandidates = [precomputedRecommended, hydratedRecommended, rowRecommended, moduleRecommended, dependencyRecommended];
    const urlCandidates = [hydratedUrl, rowUrl, moduleUrl, dependencyUrl, asString(source.projectUrl), asString(source.manifestUrl)];
    const effectiveRecommended = selectPreferredRecommended(recommendedCandidates, row.installedVersion);
    const effectiveUrl = preferredUpdateUrlFromCandidates(...urlCandidates);
    const unresolvedPath = !effectiveUrl && !effectiveRecommended;
    const fallbackSource = sourceFromReleaseUrl(rowUrl);
    const hasAnySource = hasSourceUrls(source) || Boolean(fallbackSource.manifestUrl || fallbackSource.projectUrl);
    const pendingResolve = unresolvedPath && hydrationBusy && hasAnySource;
    const hasInstalled = hasConcreteValue(row.installedVersion);
    const contextHasDepUpdates = Boolean(hydratedContext.hasDependencyUpdates);
    let effectiveState: ModuleRow["state"] = derivePlanningEffectiveState(row);
    if (effectiveState === "ready") {
      if (!hasInstalled && hasConcreteValue(effectiveRecommended)) effectiveState = "update";
      else if (contextHasDepUpdates) effectiveState = "update";
    }
    const displayRow: ModuleRow = {
      ...row,
      state: effectiveState,
      compatibility: effectiveCompatibility,
      systemCompatibility: effectiveSystemCompatibility,
    };
    const showArrow = !hasInstalled
      ? Boolean(effectiveRecommended || effectiveUrl)
      : Boolean(effectiveRecommended && compareVersionAsc(effectiveRecommended, row.installedVersion) > 0);
    const updatePathCore: ReactNode = (!showArrow || (effectiveState === "ready" && !row.hasMissingDependencies))
      ? (row.installedVersion || "-")
      : (unresolvedPath ? (pendingResolve ? "Loading..." : "?") : <>{(row.installedVersion || "-")} {" \u2192 "} {effectiveUrl ? <a href={effectiveUrl} target="_blank" rel="noreferrer">{(effectiveRecommended || "?")}</a> : (effectiveRecommended || "?")}</>);
    const updatePathCell = (
      <UpdatePathWithRefresh
        content={updatePathCore}
        hasError={refreshStatus?.kind === "error"}
        refreshing={refreshing}
        disabled={actionBusy || refreshing || !canRefreshVersions}
        title={canRefreshVersions ? "Refresh versions from source (GitHub/GitLab)" : "Set source URL first to refresh versions"}
        onRefresh={() => void refreshModuleVersions(row, "planning")}
      />
    );
    const actionsCell = (
      <div style={{ display: "inline-flex", gap: 6, flexWrap: "nowrap" }}>
        {row.hasMissingDependencies
          ? <button className="btn" style={{ background: "#ef4444", color: "#fff" }} disabled>Blocked</button>
          : effectiveState === "update"
            ? <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled>Update</button>
            : isUnusedRow
              ? <span className="btn" aria-disabled="true" style={{ background: "#f59e0b", color: "#111827", cursor: "default", pointerEvents: "none" }}>Ready</span>
              : <span className="btn" aria-disabled="true" style={{ background: "#22c55e", color: "#052e16", cursor: "default", pointerEvents: "none" }}>Ready</span>}
      </div>
    );
    return renderModuleTableRow({
      key: item.key,
      row: displayRow,
      updatePathCell,
      actionsCell,
      foundryOk,
      systemOk,
      sysCompat,
      showSystemBadge,
      foundryTarget: planningFoundryFilter,
      systemTarget: effectivePlanningSystemTarget,
      restrictedSystemIds: restrictedIds,
      systemUpgradeConflictTooltip,
      onSystemUpgradeConflictClick: conflictDetailRow ? () => setConflictDetail(conflictDetailRow) : null,
    });
  };

  const currentSystemFilterRow = (
    <div className="version-pill-row">
      <div
        key="sys-pill-all-current"
        className="metric-card compact version-pill version-pill-all static"
        style={!activeCurrentSystemId ? { borderColor: "#fbbf24", boxShadow: "0 0 0 2px rgba(251,191,36,0.28) inset", background: "#0b1f35", color: "#e5e7eb" } : { borderColor: "#334155", background: "#1f2937", color: "#e5e7eb" }}
        onClick={() => { setActiveCurrentSystemId(""); setPage(1); }}
      >
        <span>All Systems</span>
        <small style={{ color: "#94a3b8", fontSize: 11 }}>Includes foundry-only modules</small>
        <div className="version-pill-all-spacer" aria-hidden="true" />
      </div>
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
            onClick={() => {
              if (isActive) {
                setActiveCurrentSystemId("");
              } else {
                setActiveCurrentSystemId(systemId);
                setCurrentSystemFilter(value);
              }
              setPage(1);
            }}
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
  );

  const currentStatusFilterRow = (
    <div className="metrics-row compact" style={{ marginBottom: 0 }}>
      <button className={`metric-card metric-blocked compact ${currentFilters.includes("blocked") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("blocked") ? arr.filter((x) => x !== "blocked") : [...arr, "blocked"]); setPage(1); }}><span>Blocked & Missing</span><strong>{currentEffectiveCounts.blocked}</strong></button>
      <button className={`metric-card metric-upgrade compact ${currentFilters.includes("update") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("update") ? arr.filter((x) => x !== "update") : [...arr, "update"]); setPage(1); }}><span>Update</span><strong>{currentEffectiveCounts.update}</strong></button>
      <button className={`metric-card metric-ready compact ${currentFilters.includes("ready") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("ready") ? arr.filter((x) => x !== "ready") : [...arr, "ready"]); setPage(1); }}><span>Ready</span><strong>{currentEffectiveCounts.ready}</strong></button>
      <button className={`metric-card metric-unused compact ${currentFilters.includes("unused") ? "active" : ""}`} onClick={() => { setCurrentFilters((arr) => arr.includes("unused") ? arr.filter((x) => x !== "unused") : [...arr, "unused"]); setPage(1); }}><span>Unused</span><strong>{currentEffectiveCounts.unused}</strong></button>
    </div>
  );
  const planningSystemPillsRow = (
    <div className="version-pill-row" style={{ marginBottom: 0 }}>
      <button
        key="planning-system-all"
        className={`metric-card compact version-pill ${!activePlanningSystemId ? "active" : ""}`}
        onClick={() => {
          setActivePlanningSystemId("");
          setPlanningSystemVersionFilter("");
          setPage(1);
        }}
      >
        <span>All Systems</span>
        <small style={{ opacity: 0.85 }}>Systems: {planningSystemIds.length}</small>
      </button>
      {planningSystemIds.map((systemId) => {
        const options = planningSystemVersionBuckets
          .filter((bucket) => bucket.systems.includes(systemId))
          .map((bucket) => bucket.key)
          .sort(compareVersionAsc);
        const installed = asString(currentSystemVersionById[systemId]).trim();
        const visibleOptions = options.length > 0 ? options : (installed ? [installed] : []);
        if (visibleOptions.length === 0) return null;
        const value = planningVersionBySystem[systemId] || visibleOptions[0] || "";
        const isActive = activePlanningSystemId === systemId;
        return (
          <button
            key={`planning-system-${systemId}`}
            className={`metric-card compact version-pill ${isActive ? "active" : ""}`}
            onClick={() => {
              if (isActive) {
                setActivePlanningSystemId("");
              } else {
                setActivePlanningSystemId(systemId);
                setPlanningSystemVersionFilter(value);
              }
              setPage(1);
            }}
          >
            <span>{systemId}</span>
            <small style={{ opacity: 0.85 }}>
              Versions: {visibleOptions.length}
            </small>
          </button>
        );
      })}
    </div>
  );

  const planningTopFilterRow = (
    <div style={{ display: "grid", gap: 8 }}>
      {planningSystemPillsRow}
      {planningMatrixColumnKeys.length > 0 && planningMatrixFoundryVersions.length > 0 ? (
        <section className="panel" style={{ marginBottom: 0 }}>
          <h4 style={{ marginTop: 0, marginBottom: 8 }}>
            Foundry x System Compatibility{activePlanningSystemId ? ` (${planningMatrixSystemId})` : " (All Systems)"}
          </h4>
          <div style={{ overflowX: "auto" }}>
            <table className="report-table planning-matrix-table" style={{ marginBottom: 0 }}>
              <thead>
                <tr>
                  <th>Foundry Version</th>
                  {planningMatrixColumnKeys.map((sv) => (
                    <th key={`planning-matrix-head-${sv}`}>{sv === "__all__" ? "All Systems" : sv}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {planningMatrixFoundryVersions.map((fv) => (
                  <tr key={`planning-matrix-row-${fv}`}>
                    <td><strong>{fv}</strong></td>
                    {planningMatrixColumnKeys.map((sv) => {
                      const cell = planningMatrixCells[`${fv}::${sv}`];
                      if (!cell) return <td key={`planning-matrix-cell-${fv}-${sv}`}>-</td>;
                      const allSystemsCell = sv === "__all__";
                      const active = allSystemsCell
                        ? (!activePlanningSystemId && planningFoundryFilter === fv)
                        : (planningFoundryFilter === fv && planningSystemVersionFilter === sv && activePlanningSystemId === planningMatrixSystemId);
                      const tone = cell.readinessPct >= 80 ? "#14532d" : (cell.readinessPct >= 50 ? "#713f12" : "#7f1d1d");
                      return (
                        <td key={`planning-matrix-cell-${fv}-${sv}`}>
                          <button
                            className="btn secondary btn-xs"
                            style={{
                              width: "100%",
                              background: tone,
                              color: "#fff",
                              borderColor: active ? "#fde047" : "transparent",
                              borderWidth: active ? 2 : 1,
                              boxShadow: active ? "0 0 0 2px #f59e0b inset, 0 0 0 1px rgba(255,255,255,0.45)" : "none",
                            }}
                            onClick={() => {
                              if (active) return;
                              setPlanningFoundryFilter(fv);
                              if (allSystemsCell) {
                                setActivePlanningSystemId("");
                                setPlanningSystemVersionFilter("");
                              } else {
                                setPlanningSystemVersionFilter(sv);
                                if (!activePlanningSystemId && planningMatrixSystemId) setActivePlanningSystemId(planningMatrixSystemId);
                              }
                              setPage(1);
                            }}
                            title={`Installable = (Ready ${cell.ready} + Update ${cell.update}) / Total ${cell.total} = ${cell.readinessPct}% | Blocked ${cell.blocked} | Missing ${cell.missing} | Raw installed ${cell.rawTotal} | Excluded ${cell.excluded}`}
                          >
                            {cell.readinessPct}%
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 8 }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--muted)" }}>
              <input
                type="checkbox"
                checked={planningIncludeUnused}
                onChange={(event) => {
                  setPlanningIncludeUnused(event.target.checked);
                  setPage(1);
                }}
              />
              Include unused modules in matrix and table totals
            </label>
          </div>
        </section>
      ) : null}
    </div>
  );

  const planningSystemFilterRow = <></>;

  const planningEffectiveCounts = useMemo(() => {
    return partitionCountsForPills(
      selectedPlanningRows.map((row) => ({
        status: derivePlanningEffectiveState(row),
        system: row.system,
        hasMissingDependencies: row.hasMissingDependencies,
      }))
    );
  }, [selectedPlanningRows, dependencySuggestionByModule, resolvedSourceByModule, selectedPlanningSuggestContextKey, resolvedSourceByContext, activePlanningSystemId, planningVersionBySystem, planningSystemVersionFilter, selectedPlanningVersionBucket, planningFoundryFilter, planningContextRowsByModule]);

  const planningStatusFilterRow = (
    <div style={{ display: "grid", gap: 6 }}>
      <div className="metrics-row compact" style={{ marginBottom: 0 }}>
        <button className={`metric-card metric-blocked compact ${planningFilters.includes("blocked") ? "active" : ""}`} onClick={() => { setPlanningFilters((arr) => arr.includes("blocked") ? arr.filter((x) => x !== "blocked") : [...arr, "blocked"]); setPage(1); }}><span>Blocked & Missing</span><strong>{planningEffectiveCounts.blocked}</strong></button>
        <button className={`metric-card metric-upgrade compact ${planningFilters.includes("update") ? "active" : ""}`} onClick={() => { setPlanningFilters((arr) => arr.includes("update") ? arr.filter((x) => x !== "update") : [...arr, "update"]); setPage(1); }}><span>Update</span><strong>{planningEffectiveCounts.update}</strong></button>
        <button className={`metric-card metric-ready compact ${planningFilters.includes("ready") ? "active" : ""}`} onClick={() => { setPlanningFilters((arr) => arr.includes("ready") ? arr.filter((x) => x !== "ready") : [...arr, "ready"]); setPage(1); }}><span>Ready</span><strong>{planningEffectiveCounts.ready}</strong></button>
        <button className={`metric-card metric-unused compact ${planningFilters.includes("unused") ? "active" : ""}`} onClick={() => { setPlanningFilters((arr) => arr.includes("unused") ? arr.filter((x) => x !== "unused") : [...arr, "unused"]); setPage(1); }}><span>Unused</span><strong>{planningEffectiveCounts.unused}</strong></button>
      </div>
    </div>
  );
  const importFailures = asArray((lastImportReport?.failures as Array<Record<string, unknown>> | undefined) || []);
  const importModuleResults = asArray((((lastImportReport?.results as Record<string, unknown> | undefined) || {}).modules as Array<Record<string, unknown>> | undefined) || []);
  const importSystemResults = asArray((((lastImportReport?.results as Record<string, unknown> | undefined) || {}).systems as Array<Record<string, unknown>> | undefined) || []);
  const importSkippedRows = [...importModuleResults, ...importSystemResults].filter((row) => asString(row.status) === "skipped");
  const importAlreadyRows = [...importModuleResults, ...importSystemResults].filter((row) => asString(row.status) === "already");

  const buildPrimaryActionsRow = (options: { showExportPlan: boolean; showAddModule: boolean }) => {
    if (!options.showExportPlan && !options.showAddModule) return null;
    return (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, justifyContent: "flex-end" }}>
        {options.showExportPlan ? (
          <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured} onClick={() => void exportFilteredUpdatePlan()}>
            <span className="icon-wrap" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </span>
            <span>Export Plan</span>
          </button>
        ) : null}
        {options.showAddModule ? (
          <button className="btn secondary" style={{ background: "#3b82f6", color: "#fff" }} onClick={() => setAddModuleOpen(true)}>
            <span className="icon-wrap" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </span>
            <span>Add Module</span>
          </button>
        ) : null}
      </div>
    );
  };
  const currentContextVersionLabel = selectedCurrentVersionBucket?.key
    || (activeCurrentSystemId ? (currentVersionBySystem[activeCurrentSystemId] || currentSystemVersionById[activeCurrentSystemId] || currentSystemFilter || "-") : (currentSystemFilter || "-"));
  const currentTableContextLabel = `Selected: Foundry ${currentFoundryVersion || "-"} + ${
    activeCurrentSystemId
      ? `${activeCurrentSystemId} ${currentContextVersionLabel}`
      : `All Systems ${currentContextVersionLabel}`
  }`;
  const planningTableContextLabel: ReactNode = (
    <span>
      Selected: Foundry {planningFoundryFilter || "-"} +{" "}
      {activePlanningSystemId
        ? `${activePlanningSystemId} ${planningSystemVersionFilter || "-"}`
        : "All Systems"}
    </span>
  );
  const statusLegendControl = (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        className="btn secondary btn-xs"
        title="Filter by badges"
        aria-label="Filter by badges"
        onClick={() => setBadgeFilterOpen((v) => !v)}
        style={{ width: 24, height: 24, minWidth: 24, padding: 0, lineHeight: "20px", textAlign: "center" }}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="14" height="14" aria-hidden="true">
          <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
        </svg>
      </button>
      {badgeFilterCodes.length > 0 ? (
        <span className="status-pill update" title="Active badge filters" style={{ minWidth: 20, textAlign: "center", padding: "1px 6px" }}>
          {badgeFilterCodes.length}
        </span>
      ) : null}
      <button
        type="button"
        className="btn secondary btn-xs"
        title="Badge legend"
        aria-label="Open badge legend"
        onClick={() => setStatusLegendOpen(true)}
        style={{ width: 24, height: 24, minWidth: 24, padding: 0, lineHeight: "20px", textAlign: "center" }}
      >
        ?
      </button>
      {badgeFilterOpen ? (
        <div style={{ position: "absolute", top: 28, right: 0, zIndex: 40, minWidth: 250, maxHeight: 280, overflow: "auto", border: "1px solid #334155", borderRadius: 10, background: "#0f172a", padding: 8, boxShadow: "0 8px 20px rgba(0,0,0,0.35)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <strong style={{ fontSize: 12 }}>Badge Filter</strong>
            <button className="btn secondary btn-xs" onClick={() => setBadgeFilterCodes([])}>Clear</button>
          </div>
          <div style={{ display: "grid", gap: 4 }}>
            {BADGE_FILTER_OPTIONS.map((opt) => (
              <label key={`badge-filter-${opt.key}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <input
                  type="checkbox"
                  checked={badgeFilterCodes.includes(opt.key)}
                  onChange={() => {
                    setBadgeFilterCodes((prev) => prev.includes(opt.key) ? prev.filter((x) => x !== opt.key) : [...prev, opt.key]);
                    setPage(1);
                  }}
                />
                <span className="status-badge neutral" style={{ minWidth: 30, height: 22, fontSize: 12 }}>{opt.key}</span>
                <span>{opt.label}</span>
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );

  return (
    <main className="dashboard-shell">
      <Header
        onLogout={logout}
        onOpenSettings={() => setSettingsOpen(true)}
        settingsState={foundryConfigured ? "ok" : "warn"}
        onStartScan={() => void submitAndWatch("dry-run", { batchSize: 10 })}
        scanDisabled={Boolean(job) || actionBusy || !foundryConfigured}
        scanAttention={foundryConfigured && firstRunRequired && !job}
        scanMetaLabel={`Last scan: ${relativeFromNow(model?.generatedAt)}`}
      />
      <input
        ref={importPlanInputRef}
        type="file"
        accept=".json,application/json"
        style={{ display: "none" }}
        onChange={(event) => void importPlanFromFile(event)}
      />
      <section className="panel" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, alignItems: "center", justifyContent: "space-between" }}>
          <p style={{ margin: 0, color: "var(--muted)" }}>Foundry: {currentFoundryVersion || "-"}</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", marginLeft: "auto" }}>
            <button className={`btn tab-btn tab-current ${tab === "current" ? "active" : ""}`} onClick={() => { setTab("current"); setPage(1); }}>Current</button>
            <button className={`btn tab-btn tab-planning ${tab === "planning" ? "active" : ""}`} onClick={() => { setTab("planning"); setPage(1); }}>Planning</button>
            <button className={`btn tab-btn tab-backups ${tab === "backups" ? "active" : ""}`} onClick={() => { setTab("backups"); setPage(1); }}>Backups</button>
            <button className={`btn tab-btn tab-backups ${tab === "import" ? "active" : ""}`} onClick={() => { setTab("import"); setPage(1); }}>Import</button>
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
          {(() => {
            const meta = (job.progressMeta || {}) as Record<string, unknown>;
            const totalItems = Number(meta.totalItems || 0);
            const processedItems = Number(meta.processedItems || 0);
            const phase = asString(meta.phase) || "-";
            const itemKind = asString(meta.currentItemKind);
            const itemId = asString(meta.currentItemId);
            if (!totalItems) return null;
            return (
              <p style={{ marginTop: 4, marginBottom: 0, color: "var(--muted)" }}>
                Phase: {phase} | Progress: {processedItems}/{totalItems}
                {itemId ? ` | Current: ${itemKind ? `${itemKind} ` : ""}${itemId}` : ""}
              </p>
            );
          })()}
        </section>
      ) : null}

      {tab === "import" ? (
        <section className="panel" style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>Import Plan</h3>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
              <select
                value={importProfile}
                onChange={(event) => setImportProfile(event.target.value === "destiny" ? "destiny" : "current")}
                style={{ borderRadius: 8, border: "1px solid #334155", background: "#0f172a", color: "#e5e7eb", padding: "6px 8px" }}
              >
                <option value="current">Profile: Current</option>
                <option value="destiny">Profile: Destiny</option>
              </select>
              <button
                className="btn secondary"
                style={{ background: "#f59e0b", color: "#111827" }}
                disabled={Boolean(job) || actionBusy || !foundryConfigured}
                onClick={openImportPlanPicker}
                title={!foundryConfigured ? "Configure Foundry path first" : "Import update plan JSON"}
              >
                <span className="icon-wrap" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                </span>
                <span>Import</span>
              </button>
              {lastImportReport ? (
                <button className="btn secondary btn-xs" onClick={() => setLastImportReport(null)}>Dismiss</button>
              ) : null}
            </div>
          </div>
          {lastImportReport ? (
            <>
              <p style={{ marginTop: 8, marginBottom: 8 }}>
                Applied: {Number(lastImportReport.appliedCount || 0)} | Skipped: {Number(lastImportReport.skippedCount || 0)} | Failed: {Number(lastImportReport.failureCount || 0)} | Already installed: {importAlreadyRows.length}
              </p>
              {importSkippedRows.length > 0 ? (
                <p style={{ marginTop: 0, marginBottom: 8 }}>
                  Skipped sample: {importSkippedRows.slice(0, 8).map((row) => asString(row.moduleId) || asString(row.systemId) || "?").filter(Boolean).join(", ")}
                </p>
              ) : null}
              {importFailures.length > 0 ? (
                <table className="report-table" style={{ marginBottom: 10 }}>
                  <thead>
                    <tr><th>Kind</th><th>ID</th><th>Requested</th><th>Reason</th></tr>
                  </thead>
                  <tbody>
                    {importFailures.slice(0, 50).map((row, idx) => (
                      <tr key={`import-failure-${idx}`}>
                        <td>{asString(row.kind) || "-"}</td>
                        <td>{asString(row.id) || "-"}</td>
                        <td>{asString(row.targetVersion) || "-"}</td>
                        <td>{asString(row.reason) || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p style={{ marginTop: 0, marginBottom: 10 }}>No failures reported.</p>
              )}
            </>
          ) : (
            <p style={{ marginTop: 0, marginBottom: 10 }}>No import run selected yet.</p>
          )}
          <h4 style={{ marginTop: 0 }}>History</h4>
          {importHistoryLoading ? <p style={{ marginTop: 0, marginBottom: 0 }}>Loading import history...</p> : null}
          {!importHistoryLoading && importHistory.length === 0 ? <p style={{ marginTop: 0, marginBottom: 0 }}>No import history yet.</p> : null}
          {!importHistoryLoading && importHistory.length > 0 ? (
            <table className="report-table">
              <thead>
                <tr><th>When</th><th>Profile</th><th>Applied</th><th>Skipped</th><th>Failed</th><th>Plan</th></tr>
              </thead>
              <tbody>
                {importHistory.map((row, idx) => (
                  <tr key={`import-history-${idx}`}>
                    <td>{asString(row.generatedAt) || "-"}</td>
                    <td>{asString(row.profile) || "-"}</td>
                    <td>{String(Number(row.appliedCount || 0))}</td>
                    <td>{String(Number(row.skippedCount || 0))}</td>
                    <td>{String(Number(row.failureCount || 0))}</td>
                    <td>{asString(row.planPath) || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
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
            <UpgradePanel
              title="Current"
              systemFilterRow={currentSystemFilterRow}
              statusFilterRow={currentStatusFilterRow}
              actionsRow={buildPrimaryActionsRow({ showExportPlan: true, showAddModule: true })}
              tableContextLabel={currentTableContextLabel}
              search={showSearch ? search : ""}
              onSearchChange={(value) => { setSearch(value); setPage(1); }}
              statusHeadControl={statusLegendControl}
              tableHeadAction={<button className="btn secondary btn-xs" style={{ background: "#3b82f6", color: "#fff" }} disabled={actionBusy || !foundryConfigured || fixModules.length === 0} onClick={() => void submitAndWatch("apply", { modules: fixModules, batchSize: 10 })}>Update All ({fixModules.length})</button>}
              tableBody={currentPage.rows.map((item) => item.kind === "system" ? renderSystemTableRow(item) : renderCurrentModuleRow(item))}
              page={currentPage.page}
              totalPages={currentPage.totalPages}
              totalItems={currentTableRows.length}
              onPrev={() => setPage((p) => Math.max(1, p - 1))}
              onNext={() => setPage((p) => Math.min(currentPage.totalPages, p + 1))}
            />
          ) : null}

          {tab === "planning" ? (
            <UpgradePanel
              title="Planning"
              topFilterRow={planningTopFilterRow}
              systemFilterRow={planningSystemFilterRow}
              statusFilterRow={planningStatusFilterRow}
              actionsRow={buildPrimaryActionsRow({ showExportPlan: true, showAddModule: false })}
              tableContextLabel={planningTableContextLabel}
              search={showSearch ? search : ""}
              onSearchChange={(value) => { setSearch(value); setPage(1); }}
              statusHeadControl={statusLegendControl}
              tableLoading={hydrationBusy}
              tableLoadingText={planningHydrationProgress.total > 0
                ? `Resolving modules for selected context: ${planningHydrationProgress.done}/${planningHydrationProgress.total}`
                : `Resolving modules for selected context: 0/${selectedPlanningRows.length}`}
              tableBody={planningPage.rows.map((item) => item.kind === "system" ? renderSystemTableRow(item) : renderPlanningModuleRow(item))}
              page={planningPage.page}
              totalPages={planningPage.totalPages}
              totalItems={planningTableRows.length}
              onPrev={() => setPage((p) => Math.max(1, p - 1))}
              onNext={() => setPage((p) => Math.min(planningPage.totalPages, p + 1))}
            />
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

      {conflictDetail ? (
        <div className="modal-backdrop" onClick={() => setConflictDetail(null)}>
          <section className="panel modal-card" onClick={(event) => event.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Conflict Details</h3>
            <p style={{ marginTop: 0, marginBottom: 8 }}>
              Module: <strong>{conflictDetail.moduleTitle || conflictDetail.moduleId}</strong>
            </p>
            <p style={{ marginTop: 0, marginBottom: 8, color: "var(--muted)" }}>{conflictDetail.contextLabel}</p>
            <p style={{ marginTop: 0, marginBottom: 8 }}>
              Conflict summary: {Array.from(new Set(conflictDetail.versionsBySystem.map((entry) => entry.version))).length} distinct suggested versions across {conflictDetail.versionsBySystem.length} systems.
            </p>
            <table className="report-table" style={{ marginBottom: 8 }}>
              <thead>
                <tr><th>System</th><th>Suggested</th><th>Worlds</th></tr>
              </thead>
              <tbody>
                {conflictDetail.versionsBySystem.map((entry) => (
                  <tr key={`conflict-${conflictDetail.moduleId}-${entry.systemId}`}>
                    <td>{entry.systemId}</td>
                    <td>{entry.version}</td>
                    <td>{entry.worlds.join(", ") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ marginTop: 0, marginBottom: 10 }}>
              Module used in worlds: {conflictDetail.moduleWorlds.join(", ") || "-"}
            </p>
            <h4 style={{ marginTop: 0, marginBottom: 8 }}>Decision Impact</h4>
            <table className="report-table" style={{ marginBottom: 10 }}>
              <thead>
                <tr><th>If choose version</th><th>Systems matching</th><th>Worlds matching</th><th>Systems affected</th></tr>
              </thead>
              <tbody>
                {Array.from(new Set(conflictDetail.versionsBySystem.map((entry) => entry.version)))
                  .sort(compareVersionAsc)
                  .map((version) => {
                    const matching = conflictDetail.versionsBySystem.filter((entry) => entry.version === version);
                    const affected = conflictDetail.versionsBySystem.filter((entry) => entry.version !== version);
                    const matchingSystems = matching.map((entry) => entry.systemId).sort((a, b) => a.localeCompare(b));
                    const affectedSystems = affected.map((entry) => entry.systemId).sort((a, b) => a.localeCompare(b));
                    const matchingWorlds = Array.from(new Set(matching.flatMap((entry) => entry.worlds))).sort((a, b) => a.localeCompare(b));
                    return (
                      <tr key={`conflict-impact-${conflictDetail.moduleId}-${version}`}>
                        <td>{version}</td>
                        <td>{matchingSystems.join(", ") || "-"}</td>
                        <td>{matchingWorlds.join(", ") || "-"}</td>
                        <td>{affectedSystems.join(", ") || "-"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn secondary" onClick={() => setConflictDetail(null)}>Close</button>
            </div>
          </section>
        </div>
      ) : null}

      {statusLegendOpen ? (
        <div className="modal-backdrop" onClick={() => setStatusLegendOpen(false)}>
          <section className="panel modal-card" onClick={(event) => event.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Status Badge Legend</h3>
            <table className="report-table" style={{ marginBottom: 10 }}>
              <thead>
                <tr><th>Badge</th><th>Meaning</th></tr>
              </thead>
              <tbody>
                <tr><td>F✓</td><td>Foundry compatibility valid for selected target.</td></tr>
                <tr><td>F✕</td><td>Foundry compatibility incompatible for selected target.</td></tr>
                <tr><td>F?</td><td>Foundry compatibility uncertain due to insufficient metadata.</td></tr>
                <tr><td>F~</td><td>Foundry compatibility open-ended/uncertain (min satisfied, max open, verified in different major).</td></tr>
                <tr><td>F↑</td><td>Follow-up suggested: verified points to a later Foundry target.</td></tr>
                <tr><td>S✓</td><td>System compatibility valid for selected target.</td></tr>
                <tr><td>S✕</td><td>System compatibility incompatible for selected target.</td></tr>
                <tr><td>S?</td><td>System compatibility uncertain due to insufficient metadata.</td></tr>
                <tr><td>S~</td><td>System compatibility open-ended/uncertain (min satisfied, max open, verified in different major).</td></tr>
                <tr><td>S↑</td><td>Follow-up suggested: verified points to a later system target.</td></tr>
                <tr><td>SC</td><td>System conflict: different suggested versions across systems.</td></tr>
                <tr><td>!</td><td>Missing dependency (tooltip shows missing module ids).</td></tr>
                <tr><td>[x]</td><td>Forced compatibility flag is active for this module.</td></tr>
              </tbody>
            </table>
            <p style={{ marginTop: 0, marginBottom: 10, color: "var(--muted)" }}>
              Colors: green = valid/ready, red = incompatible/blocked, yellow = uncertain/warning, blue = update suggested (including follow-up).
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn secondary" onClick={() => setStatusLegendOpen(false)}>Close</button>
            </div>
          </section>
        </div>
      ) : null}

      {(actionBusy || Boolean(uiBusyMessage)) ? (
        <div className="modal-backdrop">
          <section className="panel modal-card" style={{ width: "min(420px, 92%)" }}>
            <h3>Please wait</h3>
            <p>{uiBusyMessage || "Working..."}</p>
            <div style={{ height: 10, borderRadius: 999, background: "#1f2937" }}>
              <div style={{ height: 10, borderRadius: 999, width: "70%", background: "#fbbf24", animation: "pulse-scan 1.2s ease-in-out infinite" }} />
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}














