// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import SourceChip from "./SourceChip";

afterEach(cleanup);

describe("SourceChip", () => {
  it("renders the learned chip with its tooltip, readable without hover", () => {
    render(<SourceChip source="learned" />);
    const chip = screen.getByTitle("learned from your answers");
    expect(chip.childNodes[0].textContent).toBe("learned");
    expect(chip.querySelector(".sr-only")?.textContent).toBe(": learned from your answers");
    expect(chip.getAttribute("tabindex")).toBe("0");
  });
  it("inside an interactive parent (focusable={false}) it keeps the tooltip text but is no tab stop", () => {
    render(<SourceChip source="learned" focusable={false} />);
    const chip = screen.getByTitle("learned from your answers");
    expect(chip.hasAttribute("tabindex")).toBe(false);
    expect(chip.querySelector(".sr-only")?.textContent).toBe(": learned from your answers");
  });
  it("renders the AI chip on a row the AI step filed, saying so to a screen reader and in its tooltip", () => {
    render(<SourceChip source="ai" focusable={false} />);
    const chip = screen.getByTitle("Filed by AI");
    expect(chip.childNodes[0].textContent).toBe("AI");
    expect(chip.querySelector(".sr-only")?.textContent).toBe(": Filed by AI");
    expect(chip.className).toContain("text-pink-300");
  });
  it("renders nothing for other sources", () => {
    const { container } = render(<SourceChip source="keyword" />);
    expect(container.innerHTML).toBe("");
  });
});
