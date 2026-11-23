// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MobileTopBar from "./MobileTopBar";
import { NAV_DRAWER_ID } from "./NavDrawer";

afterEach(cleanup);

const bar = (over: Partial<Parameters<typeof MobileTopBar>[0]> = {}) => {
  const props = {
    title: "Spending · Transactions", reviewCount: 0, privacy: false, menuOpen: false,
    onTogglePrivacy: vi.fn(), onOpenMenu: vi.fn(), onOpenSearch: vi.fn(), refresh: <span>pill</span>, ...over };
  render(<MobileTopBar {...props} />);
  return props;
};

// The Review inbox count and the privacy toggle were drawer-only: on a phone the inbox was invisible until the menu was open,
// and hiding every amount when someone walked up took two taps and a drawer.
describe("the phone top bar carries the review count and the privacy eye", () => {
  it("draws no chip when nothing is waiting, and the menu button is just the menu", () => {
    bar();
    expect(screen.queryByTestId("topbar-review-count")).toBeNull();
    expect(screen.getByRole("button", { name: "Open menu" })).toBeDefined();
  });
  it("the menu button says whether the drawer it controls is open", () => {
    bar({ menuOpen: false });
    const button = screen.getByRole("button", { name: "Open menu", expanded: false });
    expect(button.getAttribute("aria-controls")).toBe(NAV_DRAWER_ID);
    cleanup();
    bar({ menuOpen: true });
    expect(screen.getByRole("button", { name: "Open menu", expanded: true })).toBeDefined();
  });
  it("puts the count on the menu button as a chip a screen reader can read, without renaming the button", () => {
    bar({ reviewCount: 7 });
    const chip = screen.getByTestId("topbar-review-count");
    expect(chip.textContent).toBe("7");
    expect(chip.getAttribute("aria-label")).toBe("7 to review");
    expect(chip.closest("button"), "a sibling of the menu button, not inside it").toBeNull();
    expect(screen.getByRole("button", { name: "Open menu" })).toBeDefined();
  });
  it("caps a long count so the chip stays small, but still says the real number to a screen reader", () => {
    bar({ reviewCount: 250 });
    const chip = screen.getByTestId("topbar-review-count");
    expect(chip.textContent).toBe("99+");
    expect(chip.getAttribute("aria-label")).toBe("250 to review");
  });
  it("the eye toggles privacy in one tap and reports its state", () => {
    const p = bar();
    const eye = screen.getByTestId("privacy-toggle-bar");
    expect(eye.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(eye);
    expect(p.onTogglePrivacy).toHaveBeenCalledTimes(1);
  });
  it("with privacy on the eye says so: pressed, and tinted so the bar shows amounts are hidden", () => {
    bar({ privacy: true });
    const eye = screen.getByTestId("privacy-toggle-bar");
    expect(eye.getAttribute("aria-pressed")).toBe("true");
    expect(eye.className).toContain("text-amber-300");
    expect(eye.querySelector("svg path[d^='M4 4']"), "the slash through the eye").not.toBeNull();
  });
  it("keeps the title, the freshness slot, the menu and the search wired", () => {
    const p = bar();
    expect(screen.getByText("Spending · Transactions")).toBeDefined();
    expect(screen.getByText("pill")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(screen.getByRole("button", { name: /Jump to a page/ }));
    expect(p.onOpenMenu).toHaveBeenCalledTimes(1);
    expect(p.onOpenSearch).toHaveBeenCalledTimes(1);
  });
  it("every control is a 44px tap target", () => {
    bar({ reviewCount: 3 });
    for (const name of [/Open menu/, /Hide amounts/, /Jump to a page/]) {
      expect(screen.getByRole("button", { name }).className.split(/\s+/), String(name)).toEqual(expect.arrayContaining(["h-11", "w-11"]));
    }
  });
  it("the eye's title says how to show the amounts in the pointer's word: tap for a finger, click for a mouse", () => {
    bar({ privacy: true });
    expect(screen.getByTestId("privacy-toggle-bar").getAttribute("title")).toBe("Amounts are hidden - click to show them");
    cleanup();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(pointer: coarse)", media: query,
      addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      bar({ privacy: true });
      expect(screen.getByTestId("privacy-toggle-bar").getAttribute("title")).toBe("Amounts are hidden - tap to show them");
    } finally { vi.unstubAllGlobals(); }
  });
  it("a long title wraps onto a second line at a word, never cut inside one", () => {
    bar({ title: "Cash Flow · Income statement" });
    const title = screen.getByTestId("topbar-title");
    expect(title.textContent).toBe("Cash Flow · Income statement");
    const cls = title.className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(["line-clamp-2", "break-words"]));
    expect(cls).not.toContain("truncate");
  });
  it("carries a notice inside the bar, under its controls, only while it has one", () => {
    bar({ notice: <p data-testid="notice">Updated</p> });
    const header = screen.getByRole("banner");
    expect(header.contains(screen.getByTestId("notice")), "inside the sticky bar, never over the page").toBe(true);
    expect(header.lastElementChild).toBe(screen.getByTestId("notice"));
    cleanup();
    bar();
    expect(screen.getByRole("banner").children).toHaveLength(1);
  });
});
