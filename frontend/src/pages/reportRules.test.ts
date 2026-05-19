import { describe, expect, it } from "vitest";
import {
  canForceCompatibility,
  classifyPresentationStatus,
  compatDecision,
  isRecommendationNotFound,
  missingDependencyLabel,
  partitionCountsForPills,
  planningReadinessFromRows,
  parseMissingDependencies,
  planningHasCompatFailure,
  rowPriorityForStatus,
  scenarioReadinessPercent,
  scenarioScore,
  versionWithin,
} from "./reportRules";

describe("reportRules compatibility", () => {
  it("returns uncertain when metadata is missing", () => {
    expect(versionWithin({}, "13.350")).toBeNull();
    expect(compatDecision({}, "13.350")).toBe("uncertain");
  });

  it("returns incompatible when below min or above max", () => {
    expect(versionWithin({ minimum: "13.100" }, "12.999")).toBe(false);
    expect(versionWithin({ maximum: "13.200" }, "13.350")).toBe(false);
  });

  it("returns incompatible when verified major differs", () => {
    expect(versionWithin({ verified: "12.9.0" }, "13.350")).toBe(false);
  });

  it("returns uncertain when verified major differs but max is open-ended", () => {
    expect(versionWithin({ minimum: "10", verified: "13.346", maximum: "-" }, "14.359")).toBeNull();
  });

  it("returns uncertain when verified major differs and max is omitted", () => {
    expect(versionWithin({ minimum: "12", verified: "13" }, "14.361")).toBeNull();
  });

  it("returns compatible when inside constraints", () => {
    expect(versionWithin({ minimum: "13.0", maximum: "13.999", verified: "13.200" }, "13.350")).toBe(true);
    expect(compatDecision({ minimum: "13.0", maximum: "13.999" }, "13.350")).toBe("compatible");
  });

  it("treats major-only min/max as major ranges", () => {
    expect(versionWithin({ minimum: "13", maximum: "13" }, "13.350")).toBe(true);
    expect(versionWithin({ maximum: "13" }, "14.1")).toBe(false);
    expect(versionWithin({ minimum: "14" }, "13.999")).toBe(false);
  });

  it("accepts compatibility key aliases and loose max tokens", () => {
    expect(versionWithin({ min: "13", max: "-" }, "13.999")).toBe(true);
    expect(versionWithin({ minimumCoreVersion: "13", maximumCoreVersion: "13" }, "13.200")).toBe(true);
    expect(versionWithin({ minimum_core_version: "13", maximum_core_version: "13" }, "14.0")).toBe(false);
    expect(versionWithin({ compatibleCoreVersion: "13.250" }, "13.350")).toBe(true);
  });

  it("supports wildcard patch tokens such as 5.3.x", () => {
    expect(versionWithin({ maximum: "5.3.x" }, "5.3.0")).toBe(true);
    expect(versionWithin({ maximum: "5.3.x" }, "5.3.99")).toBe(true);
    expect(versionWithin({ maximum: "5.3.x" }, "5.4.0")).toBe(false);
    expect(versionWithin({ minimum: "5.3.x" }, "5.2.9")).toBe(false);
    expect(versionWithin({ minimum: "5.3.x" }, "5.3.0")).toBe(true);
  });

  it("supports operator and plus notations", () => {
    expect(versionWithin({ minimum: ">=13.200" }, "13.350")).toBe(true);
    expect(versionWithin({ minimum: ">=13.200" }, "13.100")).toBe(false);
    expect(versionWithin({ maximum: "<=13.350" }, "13.351")).toBe(false);
    expect(versionWithin({ minimum: "13.200+" }, "13.350")).toBe(true);
    expect(versionWithin({ minimum: "13.200+" }, "13.100")).toBe(false);
    expect(versionWithin({ minimum: "^13.200" }, "13.350")).toBe(true);
    expect(versionWithin({ minimum: "~13.200" }, "13.350")).toBe(true);
  });
});

describe("reportRules missing dependencies", () => {
  it("extracts missing dependency ids from reason text", () => {
    const reason = "could not be resolved | missing_dependency:dae ; missing_dependency:socketlib";
    expect(parseMissingDependencies(reason)).toEqual(["dae", "socketlib"]);
    expect(missingDependencyLabel(reason)).toBe("missing dependency: dae, socketlib");
  });

  it("extracts ids from explicit label format and deduplicates", () => {
    const reason = "Missing dependencies: socketlib, dae, missing_dependency:socketlib";
    expect(parseMissingDependencies(reason)).toEqual(["socketlib", "dae"]);
    expect(missingDependencyLabel(reason)).toBe("missing dependency: socketlib, dae");
  });

  it("falls back to unknown when nothing is parsable", () => {
    expect(missingDependencyLabel("some generic warning")).toBe("missing dependency: unknown");
  });
});

describe("reportRules presentation status", () => {
  it("prioritizes missing above all", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: true, foundry: "compatible", system: "compatible", hasUpdate: true })).toBe("missing");
  });

  it("marks blocked on incompatible foundry/system", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "incompatible", system: "compatible", hasUpdate: true })).toBe("blocked");
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "compatible", system: "incompatible", hasUpdate: true })).toBe("blocked");
  });

  it("blocks stale v13 recommendation when target foundry is v14", () => {
    const staleFoundryCompat = { minimum: "13", verified: "13.350", maximum: "13.999" };
    const foundryDecision = compatDecision(staleFoundryCompat, "14.361");
    expect(foundryDecision).toBe("incompatible");
    expect(
      classifyPresentationStatus({
        hasMissingDependencies: false,
        foundry: foundryDecision,
        system: "compatible",
        hasUpdate: true,
      })
    ).toBe("blocked");
  });

  it("marks update only when compatible and update exists", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "compatible", system: "compatible", hasUpdate: true })).toBe("update");
  });

  it("does not block uncertain metadata when update exists", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "uncertain", system: "uncertain", hasUpdate: true })).toBe("update");
  });

  it("does not block uncertain metadata without update", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "uncertain", system: "uncertain", hasUpdate: false })).toBe("ready");
  });

  it("marks ready when compatible and no update", () => {
    expect(classifyPresentationStatus({ hasMissingDependencies: false, foundry: "compatible", system: "compatible", hasUpdate: false })).toBe("ready");
  });

  it("applies expected priority ordering", () => {
    expect(rowPriorityForStatus("missing")).toBeLessThan(rowPriorityForStatus("blocked"));
    expect(rowPriorityForStatus("blocked")).toBeLessThan(rowPriorityForStatus("update"));
    expect(rowPriorityForStatus("update")).toBeLessThan(rowPriorityForStatus("ready"));
  });
});

describe("reportRules force compatibility", () => {
  it("identifies not found reasons", () => {
    expect(isRecommendationNotFound("HTTP Error 404: Not Found")).toBe(true);
    expect(isRecommendationNotFound("recommendation_not_resolved")).toBe(false);
  });

  it("allows force only for current tab installed modules with compatibility failure", () => {
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: false,
      systemCompatible: true,
      reason: "Foundry compatibility incompatible",
    })).toBe(true);
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: true,
      systemCompatible: false,
      reason: "System compatibility incompatible",
    })).toBe(true);
  });

  it("blocks force in non-eligible scenarios", () => {
    expect(canForceCompatibility({
      isCurrentTab: false,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: false,
      systemCompatible: false,
      reason: "compat mismatch",
    })).toBe(false);
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: false,
      hasMissingDependencies: false,
      foundryCompatible: false,
      systemCompatible: false,
      reason: "compat mismatch",
    })).toBe(false);
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: true,
      foundryCompatible: false,
      systemCompatible: false,
      reason: "missing_dependency:socketlib",
    })).toBe(false);
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: false,
      systemCompatible: false,
      reason: "HTTP Error 404: Not Found",
    })).toBe(false);
  });

  it("blocks force when failure is follow-up only (verified later target)", () => {
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: false,
      foundryFollowUpOnly: true,
      systemCompatible: true,
      reason: "Foundry follow-up only",
    })).toBe(false);
    expect(canForceCompatibility({
      isCurrentTab: true,
      hasInstalledVersion: true,
      hasMissingDependencies: false,
      foundryCompatible: true,
      systemCompatible: false,
      systemFollowUpOnly: true,
      reason: "System follow-up only",
    })).toBe(false);
  });
});

describe("reportRules scenario analysis", () => {
  it("computes readiness using ready+update coverage", () => {
    expect(scenarioReadinessPercent({ ready: 3, update: 1, blocked: 1, missing: 0 })).toBe(80);
    expect(scenarioReadinessPercent({ ready: 0, update: 0, blocked: 0, missing: 0 })).toBe(0);
  });

  it("scores scenarios favoring ready/update and penalizing blocked/missing/force", () => {
    const a = scenarioScore({ ready: 4, update: 1, blocked: 0, missing: 0, forceCompat: 0 });
    const b = scenarioScore({ ready: 2, update: 1, blocked: 2, missing: 1, forceCompat: 1 });
    expect(a).toBeGreaterThan(b);
  });
});

describe("reportRules pills partition", () => {
  it("partitions rows into exactly one bucket each", () => {
    const rows = [
      { status: "blocked" as const, system: "dnd5e" },
      { status: "update" as const, system: "dnd5e" },
      { status: "ready" as const, system: "dnd5e" },
      { status: "ready" as const, system: "unused" },
      { status: "ready" as const, system: "dnd5e", hasMissingDependencies: true },
    ];
    const counts = partitionCountsForPills(rows);
    expect(counts).toEqual({ blocked: 2, update: 1, ready: 1, unused: 1 });
    expect(counts.blocked + counts.update + counts.ready + counts.unused).toBe(rows.length);
  });

  it("counts unused rows in unused bucket, even when blocked", () => {
    const rows = [
      { status: "blocked" as const, system: "unused" },
      { status: "update" as const, system: "unused" },
    ];
    const counts = partitionCountsForPills(rows);
    expect(counts).toEqual({ blocked: 0, update: 0, ready: 0, unused: 2 });
  });
});

describe("reportRules planning filters regression", () => {
  it("ignores system incompatibility in All Systems mode", () => {
    expect(planningHasCompatFailure({
      foundryCompatible: true,
      systemCompatible: false,
      allSystemsMode: true,
    })).toBe(false);
  });

  it("keeps system incompatibility when a specific system is selected", () => {
    expect(planningHasCompatFailure({
      foundryCompatible: true,
      systemCompatible: false,
      allSystemsMode: false,
    })).toBe(true);
  });

  it("always blocks when foundry compatibility is incompatible", () => {
    expect(planningHasCompatFailure({
      foundryCompatible: false,
      systemCompatible: true,
      allSystemsMode: true,
    })).toBe(true);
    expect(planningHasCompatFailure({
      foundryCompatible: false,
      systemCompatible: true,
      allSystemsMode: false,
    })).toBe(true);
  });

  it("does not block uncertain foundry compatibility (F~ / F?) by itself", () => {
    expect(planningHasCompatFailure({
      foundryCompatible: null,
      systemCompatible: true,
      allSystemsMode: true,
    })).toBe(false);
    expect(planningHasCompatFailure({
      foundryCompatible: null,
      systemCompatible: true,
      allSystemsMode: false,
    })).toBe(false);
  });

  it("computes planning readiness from row-state buckets (ready+update over total)", () => {
    const rows = [
      ...Array.from({ length: 52 }, () => ({ status: "ready" as const, system: "dnd5e" })),
      ...Array.from({ length: 19 }, () => ({ status: "update" as const, system: "dnd5e" })),
      ...Array.from({ length: 20 }, () => ({ status: "blocked" as const, system: "dnd5e" })),
    ];
    const summary = planningReadinessFromRows(rows);
    expect(summary.total).toBe(91);
    expect(summary.ready).toBe(52);
    expect(summary.update).toBe(19);
    expect(summary.blocked).toBe(20);
    expect(summary.readinessPct).toBe(78);
  });

  it("matches expected readiness for 23 incompatible in 91 total", () => {
    const rows = [
      ...Array.from({ length: 68 }, () => ({ status: "update" as const, system: "dnd5e" })),
      ...Array.from({ length: 23 }, () => ({ status: "blocked" as const, system: "dnd5e" })),
    ];
    const summary = planningReadinessFromRows(rows);
    expect(summary.total).toBe(91);
    expect(summary.blocked).toBe(23);
    expect(summary.readinessPct).toBe(75);
  });
});
