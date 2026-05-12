import { describe, expect, it } from "vitest";
import { sourceByModuleId } from "./moduleSourceResolver";

describe("moduleSourceResolver", () => {
  it("resolves source with case-insensitive module id", () => {
    const sources = {
      DAE: { moduleId: "DAE", projectUrl: "https://gitlab.com/tposney/dae" },
      "socketlib": { moduleId: "socketlib", projectUrl: "https://github.com/farling42/foundryvtt-socketlib" },
    };
    const dae = sourceByModuleId(sources as any, "dae");
    const socket = sourceByModuleId(sources as any, "SOCKETLIB");
    expect(String(dae.projectUrl || "")).toContain("gitlab.com/tposney/dae");
    expect(String(socket.projectUrl || "")).toContain("foundryvtt-socketlib");
  });
});

