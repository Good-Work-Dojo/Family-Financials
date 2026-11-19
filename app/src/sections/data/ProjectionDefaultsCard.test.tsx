// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MASK, setPrivate } from "../../lib/privacy";

// the card reads the merged projection assumptions and the prefs store; a hand-built slice is enough. Like the app, the
// slice merges the stored overrides over the seed
const store = vi.hoisted(() => ({
  setProjection: vi.fn(),
  overrides: {} as Record<string, number>,
  localOnly: false,
  /** the keys the configuration states */
  stated: [] as string[],
}));
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    data: { assumptions: { projections: {
      return_scenarios_pct: [5, 7, 9], inflation_pct: 2.75, withdrawal_rate_pct: 4, ltcg_pct: 15, state_income_tax_pct: 0,
      ...store.overrides,
    }, projections_stated: store.stated } },
    prefs: { doc: { projections: store.overrides }, setProjection: store.setProjection, localOnly: store.localOnly },
  }),
}));

import ProjectionDefaultsCard from "./ProjectionDefaultsCard";

const STATED_ALL = ["inflation_pct", "ltcg_pct", "return_scenarios_pct", "state_income_tax_pct", "withdrawal_rate_pct"];
beforeEach(() => { store.setProjection.mockClear(); store.overrides = {}; store.localOnly = false; store.stated = STATED_ALL; });
afterEach(() => { cleanup(); setPrivate(false); });

// The card sits in a HALF-width `lg:grid-cols-2` column beside "Assumptions in force" on Data
// (~480px of content at 1280px). A fixed `sm:grid-cols-2 lg:grid-cols-3` grid pushed the number
// boxes past the card edge there, so the columns follow the card's own width instead (the same
// rule AssumptionsPanel.test pins for the sliders).
describe("ProjectionDefaultsCard layout (the 1280px overflow fix)", () => {
  const grid = () => {
    render(<ProjectionDefaultsCard />);
    // each row sits with its source mark in a wrapping pair; the grid holds the pairs
    return screen.getAllByRole("spinbutton")[0].closest("label")!.parentElement!.parentElement!;
  };

  it("sizes its columns from the container (auto-fill), never from a viewport breakpoint", () => {
    const cls = grid().className;
    expect(cls).toMatch(/grid-cols-\[repeat\(auto-fill,minmax\(min\(19rem,100%\),1fr\)\)\]/);
    expect(cls).not.toMatch(/\b(?:sm|md|lg|xl|2xl):grid-cols-/);
  });
  it("lists the three defaults, with the seed value as the placeholder", () => {
    grid();
    const boxes = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(boxes.map((b) => b.placeholder)).toEqual(["2.75", "4", "15"]);
    expect(boxes.every((b) => b.value === "")).toBe(true);
  });
  // the home's value is the snapshot's House-class rows, not a default a family can set: no row for it, and an override
  // stored under its old key is neither shown nor offered a clear button
  it("has no home value row, and a stored home value override shows nowhere", () => {
    store.overrides = { home_value_estimate: 1_250_000 };
    const { container } = render(<ProjectionDefaultsCard />);
    // "home" anywhere in the card; the group keeps a path-like sequence out of this file's text
    expect(container.textContent).not.toMatch(/(?:home)/i);
    expect(screen.getAllByRole("spinbutton")).toHaveLength(3);
    expect(screen.queryByDisplayValue("1250000")).toBeNull();
    expect(screen.queryAllByTitle("clear — back to the configuration's value")).toHaveLength(0);
  });
});

describe("ProjectionDefaultsCard editing", () => {
  it("an edit goes to the prefs store under the field's key", () => {
    render(<ProjectionDefaultsCard />);
    fireEvent.change(screen.getAllByRole("spinbutton")[0], { target: { value: "3" } });
    expect(store.setProjection).toHaveBeenCalledWith("inflation_pct", 3);
  });
  it("an overridden field gets a clear button that restores the seed", () => {
    store.overrides = { inflation_pct: 3 };
    render(<ProjectionDefaultsCard />);
    const clear = screen.getAllByTitle("clear — back to the configuration's value");
    expect(clear.length).toBe(1);
    fireEvent.click(clear[0]);
    expect(store.setProjection).toHaveBeenCalledWith("inflation_pct", null);
  });
  it("clearing a key the configuration does not state goes back to the built-in default, and says so", () => {
    store.stated = STATED_ALL.filter((k) => k !== "inflation_pct");
    store.overrides = { inflation_pct: 3 };
    render(<ProjectionDefaultsCard />);
    expect(screen.getAllByTitle("clear — back to the built-in default")).toHaveLength(1);
  });
  it("says so when the family store is unreachable and edits stay in this browser", () => {
    store.localOnly = true;
    render(<ProjectionDefaultsCard />);
    expect(screen.getByText(/saved in this browser only/).textContent).toMatch(/Store unreachable/);
  });
});

// Each key says where its value comes from: saved here, stated in the configuration, or the built-in default
describe("ProjectionDefaultsCard marks each key stated or default", () => {
  const source = (k: string) => screen.getByTestId(`projection-source-${k}`);
  const KEYS = ["inflation_pct", "withdrawal_rate_pct", "ltcg_pct", "return_scenarios_pct", "state_income_tax_pct"];
  it("every key stated in the configuration: each says so, none is a default", () => {
    render(<ProjectionDefaultsCard />);
    expect(KEYS.map((k) => source(k).textContent)).toEqual(KEYS.map(() => "from the configuration"));
    expect(screen.queryAllByTestId("default-tag")).toHaveLength(0);
  });
  it("none stated: every key is tagged default, a word with its value in the title; one saved here says so", () => {
    store.stated = [];
    store.overrides = { ltcg_pct: 20 };
    render(<ProjectionDefaultsCard />);
    expect(source("ltcg_pct").textContent).toBe("saved here");
    for (const k of KEYS.filter((x) => x !== "ltcg_pct")) {
      const tag = source(k).querySelector('[data-testid="default-tag"]');
      expect(tag?.textContent, k).toMatch(/^default/);
    }
    expect(source("return_scenarios_pct").querySelector('[data-testid="default-tag"]')!.getAttribute("title"))
      .toBe("The built-in default, 5% / 7% / 9%: neither the configuration nor this card states it.");
  });
  it("lists the two keys it has no box for, with their values", () => {
    render(<ProjectionDefaultsCard />);
    const card = screen.getByTestId("card-projection-defaults").textContent ?? "";
    expect(card).toContain("Return scenarios5% / 7% / 9%from the configuration");
    expect(card).toContain("State income tax0%from the configuration");
  });
});

// privacy mode: every default is a percentage, which privacy mode leaves alone (it masks money only), seed and set value
describe("ProjectionDefaultsCard under privacy mode", () => {
  it("leaves the % defaults' placeholders and a set value readable", () => {
    setPrivate(true);
    render(<ProjectionDefaultsCard />);
    const boxes = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(boxes.map((b) => b.placeholder)).toEqual(["2.75", "4", "15"]);
    expect(screen.queryByDisplayValue(MASK)).toBeNull();
    cleanup();
    store.overrides = { inflation_pct: 3 };                // the box then shows the merged value: the set one
    render(<ProjectionDefaultsCard />);
    const set = screen.getAllByRole("spinbutton")[0] as HTMLInputElement;
    expect([set.value, set.readOnly]).toEqual(["3", false]);
    expect(screen.queryByDisplayValue(MASK)).toBeNull();
  });
});
