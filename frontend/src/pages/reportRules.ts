export type CompatDecision = "compatible" | "incompatible" | "uncertain";
export type PresentationStatus = "missing" | "blocked" | "update" | "ready";
export type PillStatus = PresentationStatus | "unused";
export type PillCounts = { blocked: number; update: number; ready: number; unused: number };

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
    const direct = folded[key.toLowerCase()];
    if (direct !== undefined && direct !== null) {
      const value = String(direct).trim();
      if (value) return value;
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
    const t = String(part || "").trim().toLowerCase();
    return t === "x" || t === "*";
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
  const limit = idx;
  for (let i = 0; i < limit; i += 1) {
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

export function compareVersionDesc(a: string, b: string): number {
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

export function compareVersionAsc(a: string, b: string): number {
  return -compareVersionDesc(a, b);
}

export function versionWithin(compat: Record<string, unknown> | undefined, target: string): boolean | null {
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
    } else if (!satisfiesConstraint(target, min, ">=")) return false;
  }
  if (max && !looseMax) {
    if (isMajorOnlyVersion(max) && Number.isFinite(targetMajor) && Number.isFinite(maxMajor)) {
      if (targetMajor > maxMajor) return false;
    } else if (!satisfiesConstraint(target, max, "<=")) return false;
  }
  if (verified) {
    const tm = Number.parseInt(target.split(".")[0] || "0", 10);
    const vm = Number.parseInt(verified.split(".")[0] || "0", 10);
    if (Number.isFinite(tm) && Number.isFinite(vm) && tm !== vm) {
      // Open-ended max ("-", "*", "any") means verified can be stale while range remains usable.
      if (looseMax) return null;
      return false;
    }
  }
  return true;
}

export function compatDecision(compat: Record<string, unknown> | undefined, target: string): CompatDecision {
  const within = versionWithin(compat, target);
  if (within === null) return "uncertain";
  return within ? "compatible" : "incompatible";
}

export function parseMissingDependencies(reason: string): string[] {
  const text = String(reason || "").trim();
  const explicit = text.match(/missing[_\s-]*dependenc(?:y|ies)[^:]*:\s*([^\n]+)/i)?.[1] || "";
  const tokens = Array.from(text.matchAll(/missing_dependency:([a-z0-9._-]+)/gi)).map((m) => String(m[1] || "").trim());
  const fromExplicit = explicit
    .split(/[|,;]+/)
    .map((part) => part.trim().replace(/^missing_dependency:/i, ""))
    .filter(Boolean);
  return Array.from(new Set([...fromExplicit, ...tokens])).filter(Boolean);
}

export function missingDependencyLabel(reason: string): string {
  const deps = parseMissingDependencies(reason);
  if (deps.length > 0) return `missing dependency: ${deps.join(", ")}`;
  return "missing dependency: unknown";
}

export function classifyPresentationStatus(input: {
  hasMissingDependencies: boolean;
  foundry: CompatDecision;
  system: CompatDecision;
  hasUpdate: boolean;
}): PresentationStatus {
  if (input.hasMissingDependencies) return "missing";
  if (input.foundry === "incompatible" || input.system === "incompatible") return "blocked";
  if (input.hasUpdate) return "update";
  return "ready";
}

export function rowPriorityForStatus(status: PresentationStatus): number {
  if (status === "missing") return 0;
  if (status === "blocked") return 1;
  if (status === "update") return 2;
  return 3;
}

export function isRecommendationNotFound(reason: string): boolean {
  const text = String(reason || "").toLowerCase();
  return text.includes("404") || text.includes("not found");
}

export function canForceCompatibility(input: {
  isCurrentTab: boolean;
  hasInstalledVersion: boolean;
  hasMissingDependencies: boolean;
  foundryCompatible: boolean | null;
  systemCompatible: boolean | null;
  foundryFollowUpOnly?: boolean;
  systemFollowUpOnly?: boolean;
  allowSystemScopedCheck?: boolean;
  reason: string;
}): boolean {
  if (!input.isCurrentTab) return false;
  if (!input.hasInstalledVersion) return false;
  if (input.hasMissingDependencies) return false;
  if (isRecommendationNotFound(input.reason)) return false;
  const foundryFailure = input.foundryCompatible === false && !input.foundryFollowUpOnly;
  const systemFailure = (input.allowSystemScopedCheck !== false)
    && input.systemCompatible === false
    && !input.systemFollowUpOnly;
  return foundryFailure || systemFailure;
}

export function scenarioReadinessPercent(input: {
  ready: number;
  update: number;
  blocked: number;
  missing: number;
}): number {
  const total = Math.max(0, Number(input.ready || 0) + Number(input.update || 0) + Number(input.blocked || 0) + Number(input.missing || 0));
  if (total === 0) return 0;
  return Math.round((((Number(input.ready || 0) + Number(input.update || 0)) / total) * 100));
}

export function scenarioScore(input: {
  ready: number;
  update: number;
  blocked: number;
  missing: number;
  forceCompat?: number;
}): number {
  const ready = Number(input.ready || 0);
  const update = Number(input.update || 0);
  const blocked = Number(input.blocked || 0);
  const missing = Number(input.missing || 0);
  const forceCompat = Number(input.forceCompat || 0);
  return (ready * 3) + update - (blocked * 4) - (missing * 5) - (forceCompat * 2);
}

export function partitionCountsForPills(rows: Array<{ status: PillStatus; system?: string; hasMissingDependencies?: boolean }>): PillCounts {
  let blocked = 0;
  let update = 0;
  let ready = 0;
  let unused = 0;
  for (const row of rows) {
    const system = String(row.system || "").trim().toLowerCase();
    if (system === "unused" || row.status === "unused") {
      unused += 1;
      continue;
    }
    const status: PillStatus = row.hasMissingDependencies ? "missing" : row.status;
    if (status === "missing" || status === "blocked") {
      blocked += 1;
      continue;
    }
    if (status === "update") update += 1;
    else ready += 1;
  }
  return { blocked, update, ready, unused };
}
