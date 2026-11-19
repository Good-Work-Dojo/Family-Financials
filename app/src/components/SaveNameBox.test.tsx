// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SavedRefusal } from "../lib/prefs";
import SaveNameBox from "./SaveNameBox";

/** a jsdom with a matchMedia: `phone` makes it a finger on a phone - (pointer: coarse) and (max-width: 767px) both match,
 *  as the 🔍 button the note names is there only then */
function stubMedia(phone: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: phone && (query === "(pointer: coarse)" || query.includes("max-width")), media: query,
    addEventListener: () => {}, removeEventListener: () => {},
  }));
}
beforeEach(() => { stubMedia(false); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const box = (over: Partial<Parameters<typeof SaveNameBox>[0]> = {}) => {
  const onSave = vi.fn((_name: string) => true);
  const refusalFor = vi.fn((_name: string): SavedRefusal | null => null);
  render(<SaveNameBox kind="view" buttonLabel="★ Save view" nameLabel="View name" placeholder="name this view"
    refusalFor={refusalFor} onSave={onSave} {...over} />);
  return { onSave, refusalFor };
};
const open = () => fireEvent.click(screen.getByRole("button", { name: "★ Save view" }));
const type = (v: string) => fireEvent.change(screen.getByLabelText("View name"), { target: { value: v } });

describe("SaveNameBox - the one Save box for views and reports", () => {
  it("is a link-styled button until pressed, then a name field with Save and cancel", () => {
    box();
    expect(screen.queryByLabelText("View name")).toBeNull();
    open();
    expect(screen.getByLabelText("View name")).toBeDefined();
    expect(screen.queryByRole("button", { name: "★ Save view" }), "the opener gives way to the form").toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(screen.getByRole("button", { name: "★ Save view" })).toBeDefined();
  });
  it("Save hands the typed name over, closes the box and says saved for two seconds", () => {
    const { onSave } = box();
    open(); type("Dining this year");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith("Dining this year");
    expect(screen.queryByLabelText("View name")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("saved ✓ (⌘K and Data)");
    act(() => { vi.advanceTimersByTime(2100); });
    expect(screen.queryByRole("status")).toBeNull();
  });
  it("for a finger on a phone the success note says 'search' (the top bar's 🔍), not the ⌘K it has no key for", () => {
    stubMedia(true);
    box();
    open(); type("x");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("status").textContent).toBe("saved ✓ (search and Data)");
    expect(screen.getByRole("status").textContent).not.toContain("⌘K");
  });
  it("a refusal is worded by saveRefusalText in the kind's own words, before the click, and turns Save off", () => {
    box({ refusalFor: () => "full" });
    open(); type("one more");
    expect(screen.getByRole("alert").textContent).toBe("Settings are full — delete a view on Data first");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    box({ kind: "report", buttonLabel: "★ Save report", nameLabel: "Report name", refusalFor: () => "full" });
    fireEvent.click(screen.getByRole("button", { name: "★ Save report" }));
    fireEvent.change(screen.getByLabelText("Report name"), { target: { value: "one more" } });
    expect(screen.getByRole("alert").textContent).toBe("Settings are full — delete a report on Data first");
  });
  it("a save the store then refuses keeps the box open and says the list changed - never 'saved'", () => {
    box({ onSave: () => false, kind: "report" });
    open(); type("raced");
    fireEvent.submit(screen.getByLabelText("View name").closest("form")!);
    expect(screen.getByRole("alert").textContent).toBe("couldn't save this report — the list changed, try again");
    expect(screen.queryByRole("status")).toBeNull();
    expect((screen.getByLabelText("View name") as HTMLInputElement).value).toBe("raced");
  });
  it("asks the verdict only while the box is open, with the typed name (blank before one is typed)", () => {
    const { refusalFor } = box();
    expect(refusalFor).not.toHaveBeenCalled();
    open();
    expect(refusalFor).toHaveBeenLastCalledWith("");
    type("abc");
    expect(refusalFor).toHaveBeenLastCalledWith("abc");
  });
  it("a blank name cannot be saved", () => {
    box();
    open(); type("   ");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
