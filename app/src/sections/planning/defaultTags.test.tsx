// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildIndexes } from "../../data/DataContext";
import type { AppData, HouseholdPerson } from "../../data/types";
import { fullMonthsData } from "../../lib/__fixtures__";
import { _resetEfundOverridesMemory } from "../../lib/efundOverrides";
import { emptyPrefs } from "../../lib/prefs";

const state = vi.hoisted(() => ({ ctx: null as unknown }));
vi.mock("../../data/DataContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../data/DataContext")>()),
  useData: () => state.ctx,
}));
vi.mock("../../components/EChart", () => ({ default: () => <div data-testid="chart" /> }));

import Tools from "../Tools";

/** twelve whole months (every planning figure is known) and an adult with a retirement account that has reported */
function household(people: HouseholdPerson[], stated: string[]): AppData {
  const d = fullMonthsData(12);
  return { ...d,
    holdings: [{ account: "Alex 401k", account_id: null, institution: "Sample Bank", owner: "Alex", symbol: "VTI", name: "Index",
      asset_class: "Retirement", quantity: 1, value: 50_000, cost_basis: null, as_of: "2026-06-09", liquid: false, notes: null }],
    assumptions: { ...d.assumptions, projections_stated: stated,
      household: { ...d.assumptions.household, people, retirement_accounts: { "Alex 401k": "Alex" } } } };
}

function use(data: AppData) {
  state.ctx = {
    data, missing: new Set<string>(), indexes: buildIndexes(data.transactions, data.purchases, data.manifest.generated_at),
    prefs: { loading: false, localOnly: false, refused: null, doc: emptyPrefs(), setBudget: () => {}, setTarget: () => {},
      setTargetMix: () => true, setProjection: () => {}, setPayrollPhases: () => {}, saveFilter: () => true,
      removeSavedFilter: () => {}, renameSavedFilter: () => true, saveReport: () => true, removeReport: () => {},
      renameReport: () => true },
    answers: {}, overlay: { pendingIds: new Set<string>(), conflictedKeys: new Set<string>() }, reload: async () => {},
  };
}

beforeEach(() => { localStorage.clear(); _resetEfundOverridesMemory(); });
afterEach(cleanup);

const ALL = ["inflation_pct", "ltcg_pct", "return_scenarios_pct", "state_income_tax_pct", "withdrawal_rate_pct"];
const AGED: HouseholdPerson[] = [{ name: "Alex", role: "owner", birth_year: 1986 }];
const AGELESS: HouseholdPerson[] = [{ name: "Alex", role: "owner" }];
/** where each "default" tag on the page sits: its card and what it marks */
const tags = () => [...document.querySelectorAll('[data-testid="default-tag"]')].map((t) => {
  const card = t.closest('[data-testid^="card-"]')?.getAttribute("data-testid") ?? "";
  const where = t.closest("label")?.getAttribute("title")?.split(" — ")[0]
    ?? t.closest("[data-testid]:not([data-testid='default-tag'])")?.getAttribute("data-testid")
    ?? t.parentElement?.previousElementSibling?.textContent ?? "";
  return `${card}: ${where}`;
});

// "default" marks only what a household can clear: a value with a projection key that the configuration (or the app)
// leaves unstated - the withdrawal rate, the return scenarios and every one-rate return seeded from them - and the
// retirement age, while it shapes the projection. A what-if input (the horizon, the cut) is named in words instead.
describe("Projections tags a default only where the household could state it", () => {
  it("every projection key stated: only the retirement age, on its control and in the chart's line - two tags", () => {
    use(household(AGED, ALL));
    render(<MemoryRouter><Tools /></MemoryRouter>);
    expect(tags()).toEqual(["card-retirement: Retirement age", "card-retirement: retirement-basis"]);
  });
  it("no key stated: the withdrawal rate and every figure on it, the return scenarios and every rate seeded from them", () => {
    use(household(AGED, []));
    render(<MemoryRouter><Tools /></MemoryRouter>);
    const where = tags();
    expect(where).toHaveLength(12);
    for (const one of ["card-retirement: Retirement age", "card-retirement: Withdrawal rate", "card-networth-projection: Return",
      "card-networth-projection: networth-projection-basis", "card-spending-cut: Return on savings"]) {
      expect(where, one).toContain(one);
    }
    expect(where.filter((w) => w === "card-retirement: retirement-basis"), "the age and the return scenarios").toHaveLength(2);
    expect(where.filter((w) => w.startsWith("card-spending-cut:")), "its return and its three tiles").toHaveLength(4);
    expect(where.filter((w) => w.startsWith("card-retirement:")), "the age, the line's two, the rate, the FIRE number and crossing")
      .toHaveLength(6);
  });
  it("no adult with a birth year: the retirement age shapes nothing, so with every key stated there is no tag at all", () => {
    use(household(AGELESS, ALL));
    render(<MemoryRouter><Tools /></MemoryRouter>);
    expect(tags()).toEqual([]);
  });
  it("the what-if inputs say what they assume in words: the projection's horizon, the cut's amount and its return", () => {
    use(household(AGED, ALL));
    render(<MemoryRouter><Tools /></MemoryRouter>);
    expect(screen.getByTestId("networth-projection-basis").textContent).toMatch(/It grows at 7% a year for 30 years;/);
    expect(screen.getByTestId("spending-cut-basis").textContent).toBe("What cutting $500 a month grows to, invested at 7% a year.");
  });
  it("every one-rate return is the middle of the stated return scenarios, and the crossing names it", () => {
    const d = household(AGED, ALL);
    use({ ...d, assumptions: { ...d.assumptions, projections: { ...d.assumptions.projections, return_scenarios_pct: [8, 4, 6] } } });
    render(<MemoryRouter><Tools /></MemoryRouter>);
    expect(screen.getByText("FIRE crossing @6%")).toBeDefined();
    expect(screen.getByTestId("networth-projection-basis").textContent).toMatch(/It grows at 6% a year for 30 years;/);
    expect(screen.getByTestId("spending-cut-basis").textContent).toMatch(/invested at 6% a year\.$/);
  });
});
