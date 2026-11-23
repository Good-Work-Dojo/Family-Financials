// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// The phone's nav drawer is closed for the keyboard and the screen reader too, not only to the eye: closed it is inert;
// open it is a modal dialog that takes focus, keeps Tab inside, closes on Escape and gives focus back to the menu button.
// On a desktop it is the sidebar rail: never inert, never a dialog.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NavDrawer, { NAV_DRAWER_ID } from "./NavDrawer";

const media = { phone: true };
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: media.phone && /max-width: 767px/.test(query), media: query, addEventListener() {}, removeEventListener() {},
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); media.phone = true; });

/** the shell's shape: the menu button (outside the drawer), the drawer with two links and a button, and the page */
function Shell() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button aria-expanded={open} aria-controls={NAV_DRAWER_ID} onClick={() => setOpen(true)}>Open menu</button>
      <NavDrawer open={open} onClose={() => setOpen(false)}>
        <a href="#/">Home</a>
        <a href="#/spending">Spending</a>
        <button>Light mode</button>
      </NavDrawer>
      <main><a href="#/page">A link on the page</a></main>
    </>
  );
}
const drawer = () => document.getElementById(NAV_DRAWER_ID)!;
const menu = () => screen.getByRole("button", { name: "Open menu" });

describe("the phone's nav drawer", () => {
  it("closed: inert, so Tab from the menu button goes to the page and nothing in it is read", () => {
    render(<Shell />);
    expect(drawer().hasAttribute("inert")).toBe(true);
    expect(drawer().getAttribute("role")).toBeNull();
    expect(menu().getAttribute("aria-expanded")).toBe("false");
  });
  it("open: a modal dialog named Menu, not inert, focus on its first control and the menu button expanded", () => {
    render(<Shell />);
    menu().focus();
    fireEvent.click(menu());
    expect(drawer().hasAttribute("inert")).toBe(false);
    expect(screen.getByRole("dialog", { name: "Menu" })).toBe(drawer());
    expect(drawer().getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Home" }));
    expect(menu().getAttribute("aria-expanded")).toBe("true");
  });
  it("Tab from its last control wraps to its first, and Shift+Tab from the first to the last", () => {
    render(<Shell />);
    fireEvent.click(menu());
    screen.getByRole("button", { name: "Light mode" }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Home" }));
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Light mode" }));
  });
  it("Escape closes it, it is inert again, and focus is back on the menu button", () => {
    render(<Shell />);
    menu().focus();
    fireEvent.click(menu());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(drawer().hasAttribute("inert")).toBe(true);
    expect(menu().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(menu());
  });
});

describe("on a desktop the sidebar is a rail", () => {
  it("never inert and never a dialog, and nothing takes focus", () => {
    media.phone = false;
    render(<Shell />);
    expect(drawer().hasAttribute("inert")).toBe(false);
    expect(drawer().getAttribute("role")).toBeNull();
    expect(drawer().tagName).toBe("ASIDE");
  });
});
