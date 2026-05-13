import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { UpgradePanel } from "./UpgradePanel";

describe("UpgradePanel", () => {
  it("renders shared table structure and pagination", () => {
    const html = renderToStaticMarkup(
      <UpgradePanel
        title="Current"
        systemFilterRow={<div>System Filters</div>}
        statusFilterRow={<div>Status Filters</div>}
        search=""
        onSearchChange={() => {}}
        tableBody={<tr><td>row</td><td>1.0</td><td>Ready</td><td /></tr>}
        page={1}
        totalPages={3}
        onPrev={() => {}}
        onNext={() => {}}
      />
    );
    expect(html).toContain("Current");
    expect(html).toContain("Update Path");
    expect(html).toContain("Status");
    expect(html).toContain("1 / 3");
    expect(html).toContain("System Filters");
    expect(html).toContain("Status Filters");
  });

  it("renders optional top filter and actions rows", () => {
    const onChange = vi.fn();
    const html = renderToStaticMarkup(
      <UpgradePanel
        title="Planning"
        topFilterRow={<div>Foundry Filters</div>}
        systemFilterRow={<div>System Filters</div>}
        statusFilterRow={<div>Status Filters</div>}
        actionsRow={<div>Actions Row</div>}
        search="dae"
        onSearchChange={onChange}
        tableHeadAction={<button>Update All</button>}
        tableBody={<tr><td>row</td><td>1.0</td><td>Ready</td><td /></tr>}
        page={2}
        totalPages={2}
        onPrev={() => {}}
        onNext={() => {}}
      />
    );
    expect(html).toContain("Foundry Filters");
    expect(html).toContain("Actions Row");
    expect(html).toContain("Update All");
    expect(html).toContain("Planning");
    expect(html).toContain("2 / 2");
  });
});

