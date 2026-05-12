import { describe, expect, it } from "vitest";
import {
  classifyPresentationStatus,
  compatDecision,
  missingDependencyLabel,
  parseMissingDependencies,
  rowPriorityForStatus,
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

  it("returns compatible when inside constraints", () => {
    expect(versionWithin({ minimum: "13.0", maximum: "13.999", verified: "13.200" }, "13.350")).toBe(true);
    expect(compatDecision({ minimum: "13.0", maximum: "13.999" }, "13.350")).toBe("compatible");
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
