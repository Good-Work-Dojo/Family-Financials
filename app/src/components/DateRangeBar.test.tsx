// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DateRangeBar from "./DateRangeBar";
import { TAP } from "./ui";

afterEach(cleanup);
const bar = (over: Partial<Parameters<typeof DateRangeBar>[0]> = {}) => {
  const onChange = vi.fn(), onPreset = vi.fn();
  render(<DateRangeBar from="2026-01-03" to="2026-03-09" onChange={onChange} onPreset={onPreset} {...over} />);
  return { onChange, onPreset };
};

describe("DateRangeBar", () => {
  it("below md every preset chip is a 44 x 44 target (TAP) and each date box 44px tall; from md up nothing changes", () => {
    bar();
    for (const chip of screen.getAllByRole("button")) {
      expect(chip.className.split(/\s+/), chip.textContent ?? "").toEqual(expect.arrayContaining(TAP.split(" ")));
    }
    expect(TAP.split(" ").every((c) => c.startsWith("max-md:")), "a phone's only").toBe(true);
    for (const box of [screen.getByLabelText("from"), screen.getByLabelText("to")]) {
      expect(box.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-11", "md:min-h-0"]));
    }
  });
  it("from → to is one group: the arrow sits between the two inputs, in the same box, hidden from a screen reader", () => {
    bar();
    const group = screen.getByTestId("daterange-dates");
    const from = screen.getByLabelText("from"), to = screen.getByLabelText("to");
    expect(from.parentElement).toBe(group);
    expect(to.parentElement).toBe(group);
    const arrow = from.nextElementSibling!;
    expect(arrow.textContent).toBe("→");
    expect(arrow.getAttribute("aria-hidden")).toBe("true");
    expect(arrow.nextElementSibling).toBe(to);
  });
  it("on a phone the group takes its own full row and the inputs share it (they shrink), so the arrow is never stranded at a line's end", () => {
    bar();
    const group = screen.getByTestId("daterange-dates");
    expect(group.className).toContain("w-full");
    expect(group.className).toContain("sm:w-auto");
    for (const input of [screen.getByLabelText("from"), screen.getByLabelText("to")]) {
      expect(input.className).toContain("min-w-0");
      expect(input.className).toContain("flex-1");
    }
    expect(screen.getByLabelText("from").className, "still a 16px phone input").toMatch(/\btext-base\b.*\bsm:text-xs\b/);
  });
  it("the bar itself may shrink to its container (a flex item's min-content would otherwise push the 'to' box past the card edge)", () => {
    bar();
    const root = screen.getByTestId("daterange-dates").parentElement!;
    expect(root.className).toContain("min-w-0");
    expect(root.className).toContain("max-w-full");
  });
  it("still edits each end and offers the presets", () => {
    const { onChange, onPreset } = bar();
    fireEvent.change(screen.getByLabelText("from"), { target: { value: "2026-02-01" } });
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-02-01", to: "2026-03-09" });
    fireEvent.change(screen.getByLabelText("to"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-01-03", to: undefined });
    fireEvent.click(screen.getByRole("button", { name: "12M" }));
    expect(onPreset).toHaveBeenCalledWith("12m");
  });
  // a preset leaves `to` open (through the data's last day), and All leaves `from` open too: an empty date input read as
  // "mm/dd/yyyy" or dashes under a lit preset, so an open end shows the day it stands for
  it("an open end shows the day it stands for, and the window stays open: editing the other end writes no bound there", () => {
    const { onChange } = bar({ from: "2026-05-01", to: undefined, openEnds: { first: "2024-07-01", last: "2026-10-30" } });
    expect((screen.getByLabelText("from") as HTMLInputElement).value).toBe("2026-05-01");
    expect((screen.getByLabelText("to") as HTMLInputElement).value).toBe("2026-10-30");
    fireEvent.change(screen.getByLabelText("from"), { target: { value: "2026-06-01" } });
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-06-01", to: undefined });
  });
  it("All: both ends open, each shows its day; a month bar shows the months", () => {
    bar({ from: undefined, to: undefined, openEnds: { first: "2024-07-01", last: "2026-10-30" } });
    expect([(screen.getByLabelText("from") as HTMLInputElement).value, (screen.getByLabelText("to") as HTMLInputElement).value])
      .toEqual(["2024-07-01", "2026-10-30"]);
    cleanup();
    bar({ granularity: "month", from: undefined, to: undefined, openEnds: { first: "2024-12-01", last: "2026-10-01" } });
    expect([(screen.getByLabelText("from") as HTMLInputElement).value, (screen.getByLabelText("to") as HTMLInputElement).value])
      .toEqual(["2024-12", "2026-10"]);
  });
  it("with no day to stand for (no data yet) an open end stays empty", () => {
    bar({ from: undefined, to: undefined, openEnds: { first: null, last: null } });
    expect((screen.getByLabelText("to") as HTMLInputElement).value).toBe("");
  });
  it("month granularity writes whole months", () => {
    const { onChange } = bar({ granularity: "month", from: "2026-01-01", to: "2026-03-31" });
    fireEvent.change(screen.getByLabelText("to"), { target: { value: "2026-04" } });
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-01-01", to: "2026-04-30" });
  });
});
