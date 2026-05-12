export type CompatDecision = "compatible" | "incompatible" | "uncertain";
export type PresentationStatus = "missing" | "blocked" | "update" | "ready";

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
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
