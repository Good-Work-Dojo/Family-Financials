// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Holding, HouseholdPerson } from "../../data/types";
import { appDataFactory, snapFactory } from "../../lib/__fixtures__";
import { emptyPrefs, type PrefsDoc } from "../../lib/prefs";
import { MASK, setPrivate } from "../../lib/privacy";
import type { TargetMix } from "../../lib/targetMix";

// synthetic household: one owner aged 46 in the fixture's 2026, so the suggestion is 64 / 26 / 5 / 5
const ONE_OWNER: HouseholdPerson[] = [{ name: "Owner", role: "owner", birth_year: 1980 }];
const base = appDataFactory(snapFactory({}))({});
const data = { ...base, assumptions: { ...base.assumptions, household: { ...base.assumptions.household,
  people: ONE_OWNER } } };
const prefs = vi.hoisted(() => ({
  doc: null as unknown as PrefsDoc, localOnly: false, refused: null as number | null,
  setTargetMix: null as unknown as ReturnType<typeof vi.fn>,
}));
vi.mock("../../data/DataContext", () => ({ useData: () => ({ data, prefs }) }));
const chart = vi.hoisted(() => ({ option: null as any }));
vi.mock("../../components/EChart", () => ({ default: ({ option }: { option: unknown }) => { chart.option = option; return <div data-testid="chart" />; } }));

import TargetMixCard from "./TargetMixCard";

const h = (asset_class: string, value: number, symbol: string | null): Holding => ({
  account: "Brokerage", account_id: null, institution: "Broker", owner: "Joint", symbol, name: "Fund", asset_class,
  quantity: 1, value, cost_basis: null, as_of: "2026-06-01", liquid: true, notes: null,
});
// 10,000 invested: stocks 70%, bonds 20%, no cash, alternatives 10%
const HOLDINGS = [h("Stocks/ETF", 7000, "VTI"), h("Stocks/ETF", 2000, "BND"), h("Crypto", 1000, "BTC")];
const SAVED: TargetMix = { stocks: 60, bonds: 30, cash: 5, alts: 5 };

beforeEach(() => {
  prefs.doc = emptyPrefs();
  prefs.localOnly = false;
  prefs.setTargetMix = vi.fn((mix: TargetMix) => { prefs.doc = { ...prefs.doc, target_mix: mix }; return true; });
});
afterEach(() => { cleanup(); setPrivate(false); });

const ui = (holdings: Holding[] = HOLDINGS, unclassified: Holding[] = []) =>
  <TargetMixCard holdings={holdings} unclassified={unclassified} />;
const mount = (holdings: Holding[] = HOLDINGS, unclassified: Holding[] = []) => render(ui(holdings, unclassified));
const card = () => screen.getByTestId("card-target-mix");
const cells = (k: string) => [...within(card()).getByTestId(`target-mix-row-${k}`).querySelectorAll("th, td")].map((c) => c.textContent);
const heads = () => [...card().querySelectorAll("thead th")].map((c) => c.textContent);
const slider = (name: string) => within(card()).getByRole("slider", { name: `${name} target` }) as HTMLInputElement;
const button = (name: string) => within(card()).getByRole("button", { name }) as HTMLButtonElement;
const summary = () => screen.getByTestId("target-mix-summary").textContent;
/** the classes that colour a figure as advice: green for "fine", amber or red for "act" */
const ADVICE_COLOUR = /text-(?:emerald|amber|red|orange|green)-\d00/;
/** a balance-only line, as the page builds one (lib/holdings.ts): the account reports a balance and no positions */
const balanceOnly = (account: string, value: number): Holding => ({ ...h("Retirement", value, null), account,
  name: "Balance only — no security-level detail reported", notes: "balance only" });

describe("TargetMixCard: nothing saved, the age-based mix is a suggestion", () => {
  it("compares the holdings with the suggestion side by side, with its basis: no verdict, no drift, no dollars to move", () => {
    mount();
    expect(card().querySelector("h2")?.textContent).toBe("Target mix");
    expect(card().textContent).toContain("Suggestion — not saved");
    expect(screen.getByTestId("target-mix-basis").textContent).toBe("A suggestion, not the household's target: a common rule "
      + "of thumb for age 46 (the oldest adult's): 110 minus that age in stocks (at most 90%), 5% in cash, 5% in alternatives "
      + "and the rest in bonds (Stocks 64% · Bonds 26% · Cash 5% · Alternatives 5%). Save a mix to measure the holdings against it.");
    expect(screen.queryByTestId("target-mix-summary"), "no headline").toBeNull();
    expect(heads()).toEqual(["Bucket", "Actual", "Suggested"]);
    expect(cells("stocks")).toEqual(["Stocks", "70.0%", "64%"]);
    expect(card().textContent, "no drift, no amount to move").not.toMatch(/pts (?:over|under)|to move|sell \$|buy \$|pts|rebalance/i);
    expect(chart.option.series.map((s: { name: string }) => s.name)).toEqual(["Actual", "Suggested"]);
  });
  it("gives no colour of advice: nothing on the card is green, amber or red but the Save button", () => {
    mount();
    const coloured = [...card().querySelectorAll("*")].filter((e) => ADVICE_COLOUR.test(e.getAttribute("class") ?? ""));
    expect(coloured.map((e) => e.textContent)).toEqual([]);
  });
  it("a slider moved before any save is an unsaved mix, compared but still not advised on", () => {
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "50" } });
    expect(card().textContent).toContain("Unsaved changes");
    expect(heads()).toEqual(["Bucket", "Actual", "Unsaved mix"]);
    expect(screen.queryByTestId("target-mix-summary")).toBeNull();
    expect(card().textContent).toContain("Revert goes back to the suggestion");
  });
  it("Save keeps the suggestion as the family's mix; the card then says it is theirs and measures against it", () => {
    const { rerender } = mount();
    expect(button("Save").disabled, "the suggestion can be saved as it is").toBe(false);
    expect(button("Revert").disabled).toBe(true);
    expect(button("Use suggested (age-based)").disabled, "it is already the suggestion").toBe(true);
    fireEvent.click(button("Save"));
    expect(prefs.setTargetMix).toHaveBeenCalledTimes(1);
    expect(prefs.setTargetMix).toHaveBeenCalledWith({ stocks: 64, bonds: 26, cash: 5, alts: 5 });
    rerender(ui());                                              // the saved doc arrives through the data context
    expect(card().textContent).toContain("Saved for the family");
    expect(card().textContent).toContain("This is the family's saved mix.");
    expect(heads()).toEqual(["Bucket", "Actual", "Target", "Drift", "To rebalance"]);
    expect(summary()).toBe("Stocks 6.0 pts over — about $600 to move");
    expect(button("Save").disabled).toBe(true);
  });
});

// household.yaml's birth_year is optional: an adult without one has no `birth_year` key in the export
describe("TargetMixCard: adults without a birth year", () => {
  afterEach(() => { data.assumptions.household.people = ONE_OWNER; });
  const text = () => card().textContent ?? "";
  it("one of two adults without one: the suggestion is computed from the other's age", () => {
    data.assumptions.household.people = [{ name: "Alex", role: "owner" }, { name: "Sam", role: "owner", birth_year: 1980 }];
    mount();
    expect(["stocks", "bonds", "cash", "alts"].map((k) => cells(k)[2])).toEqual(["64%", "26%", "5%", "5%"]);
    expect(screen.getByTestId("target-mix-basis").textContent).toContain("a common rule of thumb for age 46");
    expect(text()).not.toMatch(/NaN|undefined/);
  });
  it("neither has one: the fixed suggestion, which says it has no ages to go on", () => {
    data.assumptions.household.people = [{ name: "Alex", role: "owner" }, { name: "Sam", role: "owner" }];
    mount();
    expect(["stocks", "bonds", "cash", "alts"].map((k) => cells(k)[2])).toEqual(["72%", "18%", "5%", "5%"]);
    expect(screen.getByTestId("target-mix-basis").textContent).toContain("a fixed starting point, since no adult's birth year is in the household config");
    expect(text()).not.toMatch(/NaN|undefined/);
  });
});

// An account that reports a balance but no positions has an unknown split: it is in no bucket, never counted as stocks
describe("TargetMixCard: an account with no positions is not classified", () => {
  it("is named on its own line with its share of investable assets and the reason, and no bucket counts it", () => {
    mount(HOLDINGS, [balanceOnly("Sample 401(k)", 10_000)]);
    expect(cells("stocks")[1], "stocks are the positions' 70%, not the 401(k) as stocks").toBe("70.0%");
    expect(screen.getByTestId("target-mix-unclassified").textContent).toBe("Not classified: Sample 401(k), $10,000 (50.0% "
      + "of investable assets) — it reports a balance but no positions, so its split is unknown and the shares above leave it out.");
    expect(card().textContent).toContain("Shares of the classified positions, $10,000: every position listed under Holdings "
      + "by account, not the balance-only accounts. Investable assets are the Total invested assets above, positions and "
      + "balance-only accounts together.");
  });
  it("two such accounts are named together; with no positions at all no share is printed, and the basis says nothing is classified", () => {
    mount([], [balanceOnly("A 401(k)", 3_000), balanceOnly("B IRA", 1_000)]);
    expect(screen.getByTestId("target-mix-unclassified").textContent).toMatch(/^Not classified: A 401\(k\) and B IRA, \$4,000 \(100\.0% of investable assets\) — they report/);
    expect(cells("stocks")).toEqual(["Stocks", "—", "64%"]);
    expect(screen.getByTestId("target-mix-basis").textContent).toMatch(
      /\. Nothing classified to compare yet\. A 401\(k\) and B IRA report a balance but no positions\.$/);
    expect(card().textContent, "no promise to measure what is not there").not.toContain("Save a mix to measure");
  });
  it("a saved mix over part of the investable assets: the verdict says how much it covers", () => {
    prefs.doc = { ...emptyPrefs(), target_mix: SAVED };
    mount(HOLDINGS, [balanceOnly("Sample 401(k)", 10_000)]);
    expect(summary()).toBe("Stocks 10.0 pts over — about $1,000 to move — covers 50.0% of investable assets; the rest is not "
      + "classified");
  });
  it("a saved mix and no position but a balance-only account: nothing classified to compare, and why", () => {
    prefs.doc = { ...emptyPrefs(), target_mix: SAVED };
    mount([], [balanceOnly("Sample 401(k)", 10_000)]);
    expect(summary()).toBe("Nothing classified to compare yet. Sample 401(k) reports a balance but no positions.");
  });
  it("none: no such line, and a saved verdict over everything says nothing of coverage", () => {
    mount();
    expect(screen.queryByTestId("target-mix-unclassified")).toBeNull();
    cleanup();
    prefs.doc = { ...emptyPrefs(), target_mix: SAVED };
    mount();
    expect(summary()).not.toMatch(/covers/);
  });
});

// The household's own asset-class targets and a saved mix measure different things: one line says what each is
describe("TargetMixCard beside the asset-class targets", () => {
  afterEach(() => { data.assumptions.targets = {}; });
  const measures = () => screen.queryByTestId("target-mix-measures");
  it("both saved: one line says what each measures, naming the bases", () => {
    data.assumptions.targets = { Cash: 20, "Stocks/ETF": 80 };
    prefs.doc = { ...emptyPrefs(), target_mix: SAVED };
    mount();
    expect(measures()?.textContent).toBe("Two measures, not a contradiction: this mix is shares of the classified positions "
      + "alone (the balance-only accounts are outside it); the asset-class targets below are shares of net worth by asset "
      + "class across everything owned, debts subtracted (not the share of assets Home's allocation shows).");
  });
  it("targets with only the suggestion, or a saved mix with no targets: no such line", () => {
    data.assumptions.targets = { Cash: 20, "Stocks/ETF": 80 };
    mount();
    expect(measures()).toBeNull();
    cleanup();
    data.assumptions.targets = {};
    prefs.doc = { ...emptyPrefs(), target_mix: SAVED };
    mount();
    expect(measures()).toBeNull();
  });
});

describe("TargetMixCard: a saved mix", () => {
  beforeEach(() => { prefs.doc = { ...emptyPrefs(), target_mix: SAVED }; });

  it("rows: actual, target, signed drift with no minus on zero, sell/buy dollars, and a one-line verdict", () => {
    mount();
    expect(card().textContent).toContain("Saved for the family");
    expect(cells("stocks")).toEqual(["Stocks", "70.0%", "60%", "+10.0 pts", "sell $1,000"]);
    expect(cells("bonds")).toEqual(["Bonds", "20.0%", "30%", "−10.0 pts", "buy $1,000"]);
    expect(cells("cash")).toEqual(["Cash", "0.0%", "5%", "−5.0 pts", "buy $500"]);
    expect(cells("alts")).toEqual(["Alternatives", "10.0%", "5%", "+5.0 pts", "sell $500"]);
    expect(summary()).toBe("Stocks 10.0 pts over — about $1,000 to move");
    expect(button("Save").disabled && button("Revert").disabled, "nothing to save or revert").toBe(true);
  });

  it("a drag previews the target, bonds, drift, dollars and chart at once, and saves nothing", () => {
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "70" } });
    expect(cells("stocks")).toEqual(["Stocks", "70.0%", "70%", "0.0 pts", "—"]);
    expect(cells("bonds")).toEqual(["Bonds", "20.0%", "20%", "0.0 pts", "—"]);
    expect(screen.getByTestId("target-mix-bonds").textContent, "bonds take the rest").toBe("20");
    expect(summary()).toBe("Within 5 points of this mix");
    expect(chart.option.series[1].data, "the chart's target bars move too").toEqual([70, 20, 5, 5]);
    expect(chart.option.series[0].data).toEqual([70, 20, 0, 10]);
    expect(card().textContent).toContain("Unsaved changes");
    expect(prefs.setTargetMix).not.toHaveBeenCalled();
  });

  it("Save writes the shown mix through the prefs op exactly once, then reads as saved", () => {
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "70" } });
    fireEvent.change(slider("Cash"), { target: { value: "8" } });
    fireEvent.click(button("Save"));
    expect(prefs.setTargetMix).toHaveBeenCalledTimes(1);
    expect(prefs.setTargetMix).toHaveBeenCalledWith({ stocks: 70, bonds: 17, cash: 8, alts: 5 });
    expect(card().textContent).not.toContain("Unsaved changes");
    expect(card().textContent).toContain("Saved for the family");
    expect(summary()).toBe("Cash 8.0 pts under — about $800 to move");
  });

  it("a drag away and back to the saved mix is no change: no marker, nothing to save or revert", () => {
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "70" } });
    expect(card().textContent).toContain("Unsaved changes");
    fireEvent.change(slider("Stocks"), { target: { value: "60" } });
    expect(card().textContent).not.toContain("Unsaved changes");
    expect(card().textContent).toContain("Saved for the family");
    expect(button("Save").disabled && button("Revert").disabled).toBe(true);
  });

  it("names the common alternatives guidance, and flags in words a saved or previewed mix above it", () => {
    const guidance = () => screen.getByTestId("target-mix-alts-guidance");
    mount();
    expect(guidance().textContent).toBe("Common guidance: keep speculative assets (the alternatives) under ~10%.");
    expect(guidance().className).not.toContain("text-amber-400");
    fireEvent.change(slider("Alternatives"), { target: { value: "10" } });
    expect(guidance().className, "at the guidance is not above it").not.toContain("text-amber-400");
    fireEvent.change(slider("Alternatives"), { target: { value: "12" } });
    expect(guidance().textContent).toBe("This mix puts 12% in alternatives, above the common guidance to keep speculative assets under ~10%.");
    expect(guidance().className).toContain("text-amber-400");
    cleanup();
    prefs.doc = { ...emptyPrefs(), target_mix: { stocks: 60, bonds: 20, cash: 5, alts: 15 } };
    mount();
    expect(guidance().className, "a saved mix above it is flagged too").toContain("text-amber-400");
    expect(guidance().textContent).toContain("puts 15% in alternatives");
  });

  it("Revert goes back to the saved mix and clears the marker", () => {
    mount();
    fireEvent.change(slider("Alternatives"), { target: { value: "20" } });
    expect(cells("alts")[2]).toBe("20%");
    fireEvent.click(button("Revert"));
    expect(["stocks", "bonds", "cash", "alts"].map((k) => cells(k)[2])).toEqual(["60%", "30%", "5%", "5%"]);
    expect(card().textContent).not.toContain("Unsaved changes");
    expect(prefs.setTargetMix).not.toHaveBeenCalled();
  });

  it("'Use suggested (age-based)' applies the suggestion as an unsaved change", () => {
    mount();
    fireEvent.click(button("Use suggested (age-based)"));
    expect(["stocks", "bonds", "cash", "alts"].map((k) => cells(k)[2])).toEqual(["64%", "26%", "5%", "5%"]);
    expect(card().textContent).toContain("Unsaved changes");
    expect(button("Use suggested (age-based)").disabled).toBe(true);
    expect(prefs.setTargetMix).not.toHaveBeenCalled();
  });

  it("a slider stops at the room the other two leave, so bonds never go negative; the box sets a leg too", () => {
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "100" } });
    expect(Number(slider("Stocks").value)).toBe(90);
    expect(screen.getByTestId("target-mix-bonds").textContent).toBe("0");
    const box = within(card()).getByRole("spinbutton", { name: "Cash target, percent" });
    fireEvent.change(box, { target: { value: "" } });                 // an emptied box is not a 0
    expect(cells("cash")[2]).toBe("5%");
    fireEvent.change(slider("Stocks"), { target: { value: "50" } });
    fireEvent.change(box, { target: { value: "12" } });
    expect(cells("cash")[2]).toBe("12%");
    expect(screen.getByTestId("target-mix-bonds").textContent).toBe("33");
  });

  it("a mix the prefs op refuses stays a draft (the op said nothing was written)", () => {
    prefs.setTargetMix = vi.fn(() => false);
    mount();
    fireEvent.change(slider("Stocks"), { target: { value: "70" } });
    fireEvent.click(button("Save"));
    expect(card().textContent).toContain("Unsaved changes");
  });

  it("says when the family store is unreachable", () => {
    prefs.localOnly = true;
    mount();
    expect(card().textContent).toContain("Saved in this browser");
    expect(card().textContent).toContain("target mix changes are saved in this browser only for now");
  });

  it("privacy mode masks every dollar and keeps the shares", () => {
    setPrivate(true);
    mount();
    expect(cells("stocks")).toEqual(["Stocks", "70.0%", "60%", "+10.0 pts", `sell ${MASK}`]);
    expect(summary()).toBe(`Stocks 10.0 pts over — about ${MASK} to move`);
    expect(card().textContent).not.toMatch(/\$\d/);
  });

  it("nothing invested: no shares, no dollars, and it says so; the footnote names no $0 base", () => {
    mount([]);
    expect(cells("stocks")).toEqual(["Stocks", "—", "60%", "—", "—"]);
    expect(summary()).toBe("Nothing invested to compare yet.");
    expect(card().textContent).toContain("Shares of the classified positions: every position listed");
    expect(card().textContent).not.toMatch(/\$0(?![\d,])/);
  });
});
