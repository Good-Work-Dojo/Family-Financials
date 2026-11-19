// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SectionBoundary from "./SectionBoundary";

// every throw below logs through React and the boundary; keep the run quiet
beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function Boom({ when }: { when: boolean }) {
  if (when) throw new Error("kaboom");
  return <div>fine</div>;
}

// a child whose failure can be switched off between the crash and the click
let broken = true;
function Flaky() {
  if (broken) throw new Error("kaboom");
  return <div>recovered</div>;
}

function Thrower({ error }: { error: Error }): never {
  throw error;
}

describe("SectionBoundary", () => {
  it("catches a throwing child and names the error", () => {
    render(<SectionBoundary resetKey="/a"><Boom when /></SectionBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/failed to render/);
    expect(alert.textContent).toMatch(/kaboom/);
  });

  it("clears the error when resetKey changes (switching tabs)", () => {
    const { rerender } = render(<SectionBoundary resetKey="/a"><Boom when /></SectionBoundary>);
    expect(screen.queryByRole("alert")).not.toBeNull();
    rerender(<SectionBoundary resetKey="/b"><Boom when={false} /></SectionBoundary>);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("fine")).toBeDefined();
  });

  it("renders children untouched when nothing throws", () => {
    render(<SectionBoundary resetKey="/a"><Boom when={false} /></SectionBoundary>);
    expect(screen.getByText("fine")).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  describe("reload button", () => {
    it("an ordinary error: 'Reload section' resets in place and never reloads the page", () => {
      const reload = vi.fn();
      vi.stubGlobal("location", { ...window.location, reload });
      broken = true;
      render(<SectionBoundary resetKey="/a"><Flaky /></SectionBoundary>);
      expect(screen.queryByRole("button", { name: "Reload page" })).toBeNull();
      expect(screen.queryByText(/app was updated/)).toBeNull();
      broken = false;
      fireEvent.click(screen.getByRole("button", { name: "Reload section" }));
      expect(reload).not.toHaveBeenCalled();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByText("recovered")).toBeDefined();
    });

    it("an ordinary error that still throws just shows the card again", () => {
      const reload = vi.fn();
      vi.stubGlobal("location", { ...window.location, reload });
      broken = true;
      render(<SectionBoundary resetKey="/a"><Flaky /></SectionBoundary>);
      fireEvent.click(screen.getByRole("button", { name: "Reload section" }));
      expect(screen.getByRole("alert").textContent).toMatch(/kaboom/);
      expect(reload).not.toHaveBeenCalled();
    });

    const chunkErrors: [string, Error][] = [
      ["chrome/vite", new Error("Failed to fetch dynamically imported module: https://x.test/assets/Foo-abc123.js")],
      ["safari", new Error("Importing a module script failed.")],
      ["firefox", new Error("error loading dynamically imported module: https://x.test/assets/Foo-abc123.js")],
      ["webpack-style by name", Object.assign(new Error("Loading chunk 7 failed."), { name: "ChunkLoadError" })],
    ];
    it.each(chunkErrors)("a chunk-load failure (%s): 'Reload page' + hint, and it hard-reloads", (_label, error) => {
      const reload = vi.fn();
      vi.stubGlobal("location", { ...window.location, reload });
      render(<SectionBoundary resetKey="/a"><Thrower error={error} /></SectionBoundary>);
      expect(screen.queryByRole("button", { name: "Reload section" })).toBeNull();
      expect(screen.getByRole("alert").textContent).toMatch(/the app was updated — reload to get the new version/);
      fireEvent.click(screen.getByRole("button", { name: "Reload page" }));
      expect(reload).toHaveBeenCalledTimes(1);
    });

    it("wraps a long unbroken message and keeps a phone-sized tap target", () => {
      render(<SectionBoundary resetKey="/a"><Boom when /></SectionBoundary>);
      expect(screen.getByRole("alert").className).toMatch(/\bbreak-words\b/);
      expect(screen.getByText("kaboom").className).toMatch(/\bbreak-all\b/);
      const btn = screen.getByRole("button", { name: "Reload section" });
      expect(btn.className).toMatch(/\bmin-h-10\b/);
      expect(btn.className).toMatch(/\bsm:min-h-0\b/);
    });
  });
});
