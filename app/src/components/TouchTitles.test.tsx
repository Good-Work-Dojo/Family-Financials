// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TouchTitles, { titleBubbleFor } from "./TouchTitles";
import { Badge } from "./ui";

/** a jsdom whose main pointer is a finger (`touch`) or a mouse */
function pointer(touch: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: touch && query === "(pointer: coarse)", media: query, addEventListener: () => {}, removeEventListener: () => {},
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const page = (
  <>
    <TouchTitles />
    <Badge cls="" title="Charged in each of the last months; no charge this month yet">forgotten?</Badge>
    <span title="Footnote: the mortgage is not yet in the feed">†</span>
    <label title="Return — Data → Projection defaults">Return <span data-testid="info">ⓘ</span><input /></label>
    <button title="CSV exports are never masked">⬇ CSV</button>
    <a href="#/x" title="Opens the account">Joint Checking</a>
    <span>no title</span>
  </>
);
const tap = (el: Element) => act(() => { fireEvent.pointerDown(el); fireEvent.click(el); });

describe("TouchTitles: a finger's way to what a title alone says", () => {
  it("for a finger a tap on a chip, a dagger or a label's ⓘ shows its title under it; the next touch closes it", () => {
    pointer(true);
    render(page);
    tap(screen.getByText("forgotten?"));
    expect(screen.getByTestId("touch-title").textContent).toBe("Charged in each of the last months; no charge this month yet");
    expect(screen.getByTestId("touch-title").getAttribute("aria-hidden"), "a screen reader already reads the title").toBe("true");
    tap(screen.getByText("†"));
    expect(screen.getByTestId("touch-title").textContent).toBe("Footnote: the mortgage is not yet in the feed");
    tap(screen.getByTestId("info"));
    expect(screen.getByTestId("touch-title").textContent).toBe("Return — Data → Projection defaults");
    tap(screen.getByText("no title"));
    expect(screen.queryByTestId("touch-title")).toBeNull();
  });

  it("a control's title is never shown: a tap on a button or a link does what it does", () => {
    pointer(true);
    render(page);
    tap(screen.getByRole("button", { name: /CSV/ }));
    tap(screen.getByRole("link", { name: /Joint Checking/ }));
    tap(screen.getByRole("textbox"));                       // the input inside a titled label is the input's
    expect(screen.queryByTestId("touch-title")).toBeNull();
  });

  it("Escape and a scroll close it", () => {
    pointer(true);
    render(page);
    tap(screen.getByText("†"));
    act(() => { fireEvent.keyDown(document, { key: "Escape" }); });
    expect(screen.queryByTestId("touch-title")).toBeNull();
    tap(screen.getByText("†"));
    act(() => { fireEvent.scroll(window); });
    expect(screen.queryByTestId("touch-title")).toBeNull();
  });

  it("with a mouse it listens to nothing: the title is the browser's own tooltip", () => {
    pointer(false);
    render(page);
    tap(screen.getByText("forgotten?"));
    expect(screen.queryByTestId("touch-title")).toBeNull();
  });

  it("the bubble sits under its element, inside the screen's 16px edges, and over it when there is no room below", () => {
    const at = (r: Partial<DOMRect>) => {
      const el = document.createElement("span");
      el.title = "why";
      el.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {}, ...r });
      return el;
    };
    const screenSize = { width: 390, height: 844 };
    expect(titleBubbleFor(at({ top: 100, bottom: 116, left: 40 }), screenSize)).toEqual({ text: "why", top: 122, left: 40, above: false });
    expect(titleBubbleFor(at({ top: 100, bottom: 116, left: 2 }), screenSize)?.left, "never past the left edge").toBe(16);
    expect(titleBubbleFor(at({ top: 100, bottom: 116, left: 300 }), screenSize)?.left, "room for its width").toBe(390 - 16 - 288);
    expect(titleBubbleFor(at({ top: 800, bottom: 816, left: 40 }), screenSize)).toEqual({ text: "why", top: 794, left: 40, above: true });
    expect(titleBubbleFor(null, screenSize)).toBeNull();
  });
});
