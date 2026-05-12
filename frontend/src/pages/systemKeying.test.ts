import { describe, expect, it } from "vitest";
import { buildRelatedSystems } from "./systemKeying";

describe("systemKeying", () => {
  it("uses systemId for relationships and never injects display title", () => {
    const related = buildRelatedSystems("dnd5e", ["dnd5e", "pf2e"], ["dnd5e"]);
    expect(related).toEqual(["dnd5e", "pf2e"]);
    expect(related).not.toContain("D&D 5e");
  });

  it("deduplicates and trims values", () => {
    const related = buildRelatedSystems(" dnd5e ", ["dnd5e", " pf2e "], ["", "pf2e"]);
    expect(related).toEqual(["dnd5e", "pf2e"]);
  });
});

