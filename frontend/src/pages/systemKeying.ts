export function buildRelatedSystems(
  systemId: string,
  usageSystems: string[],
  compatSystems: string[]
): string[] {
  const values = [
    String(systemId || "").trim(),
    ...usageSystems.map((v) => String(v || "").trim()),
    ...compatSystems.map((v) => String(v || "").trim()),
  ].filter(Boolean);
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
}

