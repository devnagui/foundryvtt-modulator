import type { ModuleSourceRow } from "../services/api";

export function sourceByModuleId(sources: Record<string, ModuleSourceRow>, moduleId: string): Partial<ModuleSourceRow> {
  const raw = String(moduleId || "").trim();
  if (!raw) return {};
  return sources[raw] || sources[raw.toLowerCase()] || sources[raw.toUpperCase()] || {};
}

