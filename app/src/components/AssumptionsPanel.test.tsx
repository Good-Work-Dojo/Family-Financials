// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MASK, setPrivate } from "../lib/privacy";
import AssumptionsPanel from "./AssumptionsPanel";

afterEach(() => { cleanup(); setPrivate(false); });
const fields = [1, 2, 3, 4].map((i) => ({ key: `k${i}`, label: `Field ${i}`, min: 0, max: 100, step: 1, unit: "%" }));
const values = { k1: 1, k2: 2, k3: 3, k4: 4 };

// The panel sits in a full-width card on Investments and inside HALF-width `lg:grid-cols-2` cards
// on Planning. Viewport breakpoints (`xl:grid-cols-3`) cannot tell those apart: at 1280px a
// half-width card got three ~133px tracks, labels overprinted their sliders and the controls ran
// past the card edge. The columns therefore follow the panel's own width.
describe("AssumptionsPanel layout (the 1280px overflow fix)", () => {
  const grid = () => {
    render(<AssumptionsPanel fields={fields} values={values} defaults={values} onChange={() => {}} testId="card-x" />);
    return screen.getByTestId("card-x").querySelector("label")!.parentElement!;
  };

  it("sizes its columns from the container (auto-fill), never from a viewport breakpoint", () => {
    const cls = grid().className;
    expect(cls).toMatch(/grid-cols-\[repeat\(auto-fill,minmax\(/);
    expect(cls).not.toMatch(/\b(?:sm|md|lg|xl|2xl):grid-cols-/);
  });
  it("lets the slider shrink and pins label, number and unit widths so nothing is squeezed out", () => {
    grid();
    for (const r of screen.getAllByRole("slider")) expect(r.className).toMatch(/\bmin-w-0\b/);
    const label = screen.getByText("Field 1");
    expect(label.className).toMatch(/\bsm:w-32\b/);
    expect(label.className).toMatch(/\bsm:shrink-0\b/);
    expect(screen.getAllByText("%")[0].className).toMatch(/\bw-5\b.*\bshrink-0\b|\bshrink-0\b.*\bw-5\b/);
  });
  it("the slider group may shrink inside its row (min-w-0) instead of spilling past the card", () => {
    grid();
    expect(screen.getAllByRole("slider")[0].parentElement!.className).toMatch(/\bmin-w-0\b/);
  });
  // A 64px box clipped seven-digit values (a $1,000,000 home value) at both the 12px desktop and the
  // 16px phone font. Tracks are 22rem now, so the box has room: wide enough for 7 digits at 16px.
  it("the number box fits a seven-digit value at the phone's 16px font and never shrinks", () => {
    grid();
    for (const n of screen.getAllByRole("spinbutton")) {
      expect(n.className).toMatch(/\bw-24\b/);
      expect(n.className).toMatch(/\bsm:w-20\b/);
      expect(n.className).toMatch(/\bshrink-0\b/);
      expect(n.className).not.toMatch(/\bw-16\b/);
    }
  });
  // A truncated label lost text ("Stocks / retirement / 529", "Alts cap (crypto+vent…") and clipped
  // the ⓘ; labels wrap instead, and the tooltip carries the label as well as its source.
  it("labels wrap instead of truncating, and the tooltip carries label and source", () => {
    render(<AssumptionsPanel fields={[{ ...fields[0], label: "Stocks / retirement / 529", source: "where the default comes from" }, fields[1]]}
      values={values} defaults={values} onChange={() => {}} testId="card-y" />);
    const label = screen.getByText(/Stocks \/ retirement \/ 529/);
    expect(label.className).toMatch(/\bleading-tight\b/);
    expect(label.className).not.toMatch(/\btruncate\b/);
    expect(label.closest("label")!.getAttribute("title")).toBe("Stocks / retirement / 529 — where the default comes from");
    expect(screen.getByText("ⓘ")).toBeDefined();
    // no source: the label alone
    expect(screen.getByText("Field 2").closest("label")!.getAttribute("title")).toBe("Field 2");
  });
});

describe("AssumptionsPanel under privacy mode", () => {
  const mixed = [{ key: "m", label: "Monthly", min: 0, max: 9, step: 1, unit: "$" }, { key: "r", label: "Return", min: 0, max: 9, step: 1, unit: "%" }];
  const panel = () => render(<AssumptionsPanel fields={mixed} values={{ m: 1500, r: 7 }} defaults={{ m: 1500, r: 7 }} onChange={() => {}} />);
  it("the collapsed summary formats $ fields as money (so privacy masks them) and leaves % fields plain", () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: /Assumptions/ }));
    expect(screen.getByText(/Monthly \$1,500 \u00b7 Return 7%/)).toBeDefined();
    act(() => setPrivate(true));
    expect(screen.getByText(`Monthly ${MASK} \u00b7 Return 7%`)).toBeDefined();
  });
  it("a $ field's number box is masked at rest and its slider blurred; the % field is untouched", () => {
    setPrivate(true);
    panel();
    expect(screen.getByDisplayValue(MASK)).toBeDefined();
    expect((screen.getByRole("spinbutton") as HTMLInputElement).value, "the % box is the only real number box left").toBe("7");
    const [money, pct] = screen.getAllByRole("slider");
    expect(money.className).toMatch(/\bblur-sm\b/);
    expect(money.getAttribute("aria-valuetext")).toBe("amount hidden");
    expect(money.getAttribute("value"), "the $ slider is the shared AmountRange: its value is a step, not the amount").not.toBe("1500");
    expect(pct.className).not.toMatch(/blur/);
    expect(pct.hasAttribute("aria-valuetext")).toBe(false);
    const box = screen.getByDisplayValue(MASK);
    fireEvent.focus(box);
    expect((box as HTMLInputElement).value, "the digits show only while it is being edited").toBe("1500");
    fireEvent.blur(box);
    expect((box as HTMLInputElement).value, "and the mask is back at rest").toBe(MASK);
  });
  it("nothing is masked with privacy off", () => {
    panel();
    expect(screen.getByDisplayValue("1,500")).toBeDefined();
    expect(screen.getAllByRole("slider")[0].className).not.toMatch(/blur/);
  });
});

// A $ box is the shared CurrencyInput: Planning -> House printed "1000000" while Second house printed "1,000,000".
describe("AssumptionsPanel $ boxes read like Second house's money boxes", () => {
  const money = [{ key: "m", label: "Home value", min: 0, max: 2_000_000, step: 1, unit: "$" }, { key: "r", label: "Return", min: 0, max: 9, step: 1, unit: "%" }];
  const panel = (m: number, onChange: (v: Record<string, number>) => void = () => {}) =>
    render(<AssumptionsPanel fields={money} values={{ m, r: 7 }} defaults={{ m, r: 7 }} onChange={onChange} />);
  it("shows thousands separators at rest, the plain digits while editing, and leaves a % box a number box", () => {
    panel(1_000_000);
    const box = screen.getByDisplayValue("1,000,000") as HTMLInputElement;
    expect(box.type).toBe("text");
    fireEvent.focus(box);
    expect(box.value).toBe("1000000");
    fireEvent.blur(box);
    expect(box.value).toBe("1,000,000");
    expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe("7");
  });
  it("commits a typed amount (\"650k\" works, as in Second house) on blur and Enter, and a bad entry changes nothing", () => {
    const seen: Record<string, number>[] = [];
    panel(1_000_000, (v) => seen.push(v));
    const box = screen.getByDisplayValue("1,000,000") as HTMLInputElement;
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "650k" } });
    fireEvent.blur(box);
    expect(seen).toEqual([{ m: 650_000, r: 7 }]);
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "lots" } });
    fireEvent.blur(box);
    expect(seen).toHaveLength(1);
  });
});
