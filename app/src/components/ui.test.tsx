// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentToggle } from "./ui";

afterEach(cleanup);

// a screen reader hears which option is on, and what the options choose between
describe("SegmentToggle", () => {
  const OPTIONS = [{ value: "12m", label: "12M" }, { value: "ytd", label: "YTD" }, { value: "all", label: "All" }];
  it("is a named group of buttons, the selected one pressed and the others not", () => {
    render(<SegmentToggle label="Period" value="ytd" options={OPTIONS} onChange={() => {}} />);
    const group = screen.getByRole("group", { name: "Period" });
    expect(group.querySelectorAll("button")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "YTD", pressed: true })).toBeDefined();
    expect(screen.getByRole("button", { name: "12M", pressed: false })).toBeDefined();
    expect(screen.getByRole("button", { name: "All", pressed: false })).toBeDefined();
  });
  it("its buttons never submit a form they sit in, and a click picks the option", () => {
    const onChange = vi.fn();
    render(<form><SegmentToggle label="Period" value="12m" options={OPTIONS} onChange={onChange} /></form>);
    for (const b of screen.getAllByRole("button")) expect(b.getAttribute("type")).toBe("button");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(onChange).toHaveBeenCalledWith("all");
  });
});
