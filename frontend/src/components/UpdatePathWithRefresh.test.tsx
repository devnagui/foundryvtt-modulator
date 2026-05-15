import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { UpdatePathWithRefresh } from "./UpdatePathWithRefresh";

describe("UpdatePathWithRefresh", () => {
  it("renders refresh icon next to update path without text label", () => {
    const html = renderToStaticMarkup(
      <UpdatePathWithRefresh
        content={<><span>6.8.2</span> {" \u2192 "} <span>7.0.3</span></>}
        onRefresh={vi.fn()}
      />
    );
    expect(html).toContain("6.8.2");
    expect(html).toContain("7.0.3");
    expect(html).toContain("aria-label=\"Refresh module versions\"");
    expect(html).toContain("<svg");
    expect(html).not.toContain(">Refresh<");
  });

  it("shows loading state while refreshing", () => {
    const html = renderToStaticMarkup(
      <UpdatePathWithRefresh
        content={<span>1.0.0</span>}
        refreshing
        onRefresh={vi.fn()}
      />
    );
    expect(html).toContain("aria-label=\"Refreshing module versions\"");
    expect(html).toContain("aria-busy=\"true\"");
    expect(html).toContain("refresh-icon-spinning");
  });
});
