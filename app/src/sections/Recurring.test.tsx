// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import dayjs from "dayjs";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CategoryRename, RecurringItem } from "../data/types";
import { recFactory } from "../lib/__fixtures__";
import { SHIPPED_RESERVED } from "../lib/categories";
import { clock } from "../lib/clock";
import { fmtDate } from "../lib/format";

// The obligations card reads recurring.json and the data range; the chart is stubbed (no canvas) and records its option.
let recurring: RecurringItem[] = [];
let renameList: CategoryRename[] = [];
// the data's date range; a test sets it to null for a household with no transaction yet
const RANGE = { first: "2024-06-01", last: "2026-06-20" };
let dataRange: typeof RANGE | null = RANGE;
// the household's zone (household.yaml `timezone`); null is an export older than the key
let zone: string | null = "America/New_York";
// the cash-flow months: June 2026, the ingest's month, is the month in progress
const CASHFLOW = [{ month: "2026-05" }, { month: "2026-06" }];
let cashflow = CASHFLOW;
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { recurring, accounts: [], transactions: [], cashflow,
            manifest: { current_snapshot_date: "2026-06-01", generated_at: "2026-06-20T20:00:00-04:00" },
            assumptions: { category_renames: renameList, reserved: SHIPPED_RESERVED, household: { timezone: zone } } },
    indexes: { dataRange, dataThrough: dataRange?.last ?? null },
    missing: new Set<string>(),
  }),
}));
const charts = vi.hoisted(() => ({ options: [] as any[] }));
vi.mock("../components/EChart", () => ({
  default: (p: { option: unknown }) => { charts.options.push(p.option); return <div data-testid="chart" />; },
}));
import Recurring from "./Recurring";

const rec = recFactory({ active: true, schedule: false, first_seen: "2025-01-01", last_seen: "2026-05-20" });
const mortgage = rec({ vendor: "Home Loan", cadence: "biweekly", typical_amount: 1500, monthly_equivalent: 3250,
  category: "Housing", next_expected: "2026-06-05", median_gap_days: 14 });
const insurance = rec({ vendor: "Auto Insurance", cadence: "annual", typical_amount: 1800, monthly_equivalent: 150,
  category: "Insurance", next_expected: "2027-01-10" });                                   // 7 months out from 2026-06-01
const tax = rec({ vendor: "Property Tax", cadence: "quarterly", typical_amount: 900, monthly_equivalent: 300,
  category: "Taxes", next_expected: "2026-06-01" });                                          // twice within 90 days
const domain = rec({ vendor: "Domain", cadence: "annual", typical_amount: 18, monthly_equivalent: 1.5,
  category: "Subscriptions", next_expected: "2026-07-15" });                                  // small, but annual

beforeEach(() => {
  charts.options = []; recurring = []; renameList = []; dataRange = RANGE; zone = "America/New_York"; cashflow = CASHFLOW;
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

const show = () => render(<MemoryRouter><Recurring /></MemoryRouter>);

// "Today" is the household's day: a household opened from a browser in another zone sees the statuses it sees at home.
// At this instant it is Jul 21 in Kiritimati (UTC+14) and Jul 20 from Pago Pago (UTC-11) to UTC: the suite's browser
// (UTC) is a day behind a household in Kiritimati, and a browser in Kiritimati a day ahead of one in Pago Pago.
describe("Recurring: a schedule's status is judged on the household's day, not the browser's", () => {
  const INSTANT = "2026-07-20T12:00:00Z";
  const plus = (day: string, n: number) => dayjs(day).add(n, "day").format("YYYY-MM-DD");
  // each status chip's label (a chip repeats its title after a colon, for screen readers)
  const chips = () => screen.getAllByText(/^(due ·|upcoming |missed ·|data stale|✓ paid )/)
    .map((e) => (e.textContent ?? "").split(":")[0]);
  it("at an instant where the two days differ, a bill due on the household's day is not due on the browser's", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(INSTANT));
    const browserDay = clock.today(null);
    zone = clock.today("Pacific/Kiritimati") !== browserDay ? "Pacific/Kiritimati" : "Pacific/Pago_Pago";
    const householdDay = clock.today(zone);
    expect(householdDay, "the two days differ at this instant").not.toBe(browserDay);
    const ahead = householdDay > browserDay;
    // a household a day ahead is on the bill's first due day (the day before it is expected) while the browser is a day
    // before that; a household a day behind is on the last day of the grace window while the browser is past it
    const expected = ahead ? plus(householdDay, 1) : plus(householdDay, -6);
    recurring = [rec({ vendor: "Water bill", schedule: true, grace_days: 6, last_seen: "2026-05-01",
      next_expected: expected, expected_dates: [expected] })];
    show();
    // the phone list and the table both carry the chip (jsdom applies no media query)
    expect(chips()).toEqual([`due · expected ${fmtDate(expected)}`, `due · expected ${fmtDate(expected)}`]);
    cleanup();
    zone = null;   // an export with no zone: the browser's own day, which is what the page read before it had the household's
    show();
    expect(chips()).toEqual(ahead ? Array(2).fill(`upcoming ${fmtDate(expected)}`) : ["data stale", "data stale"]);
  });
});

// With no transaction yet the page hands its date bar the reason no preset that counts back resolves: those chips are
// disabled and say why, and All, which needs no anchor date, stays enabled and lit.
describe("Recurring: the missed-only filter on a phone", () => {
  it("the button is the 44px target and the pill inside it keeps its own size, its text centred in it", () => {
    recurring = [mortgage];
    show();
    const button = screen.getByRole("button", { name: /missed only/ });
    expect(button.className.split(/\s+/)).toEqual(expect.arrayContaining(["min-h-11", "items-center"]));
    const pill = button.querySelector("span")!;
    const cls = pill.className.split(/\s+/);
    expect(cls, "the pill is not stretched to the button's height").not.toContain("min-h-11");
    expect(cls).toEqual(expect.arrayContaining(["min-h-[36px]", "rounded-full"]));
  });
});

describe("Recurring: the period chips with no transaction yet", () => {
  it("12M and 24M are disabled with the reason; All is enabled and lit", () => {
    dataRange = null;
    show();
    const chip = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
    for (const name of ["12M", "24M"]) expect([chip(name).disabled, chip(name).title], name).toEqual([true, "No transactions yet"]);
    expect([chip("All").disabled, chip("All").title, chip("All").className.includes("bg-zinc-800")]).toEqual([false, "", true]);
  });
});
const card = () => screen.getByTestId("card-obligations");
/** the "Renewals in the next 90 days" line only (the all-bills details below it lists every occurrence) */
const next90Line = () => [...card().querySelectorAll("div")].find((d) => (d.textContent ?? "").startsWith("Renewals in the next 90 days"))?.textContent ?? null;
/** the obligations chart: the one option with the Routine/Renewals stack */
const obligationsOption = () => charts.options.find((o) => o?.series?.[0]?.name === "Routine");
const lumpySeries = () => obligationsOption().series.find((s: { name: string }) => s.name.startsWith("Renewals")).data as number[];

describe("the obligations chart: twelve bars after the month in progress", () => {
  const routine = () => obligationsOption().series[0].data as number[];
  it("twelve bars, the first the month after the month in progress, a monthly bill in each and a month with none at zero", () => {
    recurring = [rec({ vendor: "Water bill", typical_amount: 40, next_expected: "2026-06-25" }), domain];
    show();
    expect(obligationsOption().xAxis.data).toEqual(["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
      "2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]);
    expect(routine()).toEqual(Array(12).fill(40));
    expect(lumpySeries()).toEqual([18, ...Array(11).fill(0)]);
    expect(card().textContent).toContain("The twelve months after Jun 2026, which is still in progress.");
  });
  it("a row dated into a month that has not begun moves no bar: the month in progress is the ingest's", () => {
    cashflow = [...CASHFLOW, { month: "2026-07" }];
    recurring = [domain];
    show();
    expect(obligationsOption().xAxis.data[0]).toBe("2026-07");
  });
  it("with the data ending before the ingest month, the bars start after the data's own month in progress", () => {
    cashflow = [{ month: "2026-04" }];
    recurring = [domain];
    show();
    expect(obligationsOption().xAxis.data[0]).toBe("2026-05");
  });
});

describe("the obligations card: what is lumpy", () => {
  it("a big biweekly bill is routine (grey), a small annual renewal is lumpy (amber)", () => {
    recurring = [mortgage, domain];
    show();
    // 2026-06 .. 2027-06: the biweekly charges are all Routine; only the $18 annual renewal is Lumpy
    expect(lumpySeries().reduce((s, v) => s + v, 0)).toBe(18);
    expect(obligationsOption().series[0].data.reduce((s: number, v: number) => s + v, 0)).toBeGreaterThan(1500 * 20);
  });
  it("the 90-day renewals list names the renewals, never the mortgage", () => {
    recurring = [mortgage, tax, domain];
    show();
    expect(next90Line()).toMatch(/^Renewals in the next 90 days:/);
    expect(next90Line()).toContain("Property Tax");
    expect(next90Line()).toContain("Domain");
    expect(card().textContent, "the biweekly mortgage is nowhere in the lumpy lists").not.toContain("Home Loan");
  });
  it("the 90-day list shows a vendor once with its count, not once per occurrence", () => {
    recurring = [tax];            // quarterly, due 2026-06-01 and again 2026-09-01: both inside 90 days of 2026-06-01
    show();
    expect(next90Line()?.match(/Property Tax/g), "one entry").toHaveLength(1);
    expect(next90Line()).toMatch(/Property Tax × 2\s*\$900/);
  });
});

describe("the obligations card with no bill to project", () => {
  it("nothing detected: the card says so and the detector's rule (no promise that waiting helps), with no chart", () => {
    show();
    expect(card().textContent).toBe("Upcoming obligations — known bills, next 12 months"
      + "No bills detected yet. A charge is recognised once one vendor has charged the same account at least three times "
      + "at a regular interval, weekly to yearly, for a similar amount.");
    expect(within(card()).queryByTestId("chart")).toBeNull();
    expect(obligationsOption()).toBeUndefined();
  });
  it("only lapsed charges: the card says none is still active", () => {
    recurring = [{ ...insurance, active: false }];
    show();
    expect(within(card()).getByTestId("empty-state").textContent)
      .toBe("No detected recurring charge is still active, so no bill is expected in the next 12 months.");
  });
});

describe("the obligations card: all big bills, next 12 months", () => {
  it("an annual bill seven months out is in the collapsed details list with its cadence, amount and date", () => {
    recurring = [mortgage, insurance, tax];
    show();
    const details = within(card()).getByTestId("obligations-all-bills") as HTMLDetailsElement;
    expect(details.open, "collapsed by default").toBe(false);
    const rows = [...details.querySelectorAll("li")].map((li) => li.textContent ?? "");
    expect(rows.some((r) => /Auto Insurance.*annual.*\$1,800.*Jan 10, 2027/.test(r)), "the 7-months-out bill is listed").toBe(true);
    // the quarterly bill appears once per due date (four to five in the year), the mortgage not at all
    expect(rows.filter((r) => r.includes("Property Tax")).length).toBeGreaterThanOrEqual(4);
    expect(rows.some((r) => r.includes("Home Loan"))).toBe(false);
    // and it is NOT in the 90-day list (it is beyond 90 days): the details list is the only place it is named in full
    expect(next90Line() ?? "").not.toContain("Auto Insurance");
    expect(details.querySelector("summary")?.textContent).toMatch(/All renewals, next 12 months — \d+ charges? · \$/);
  });
  it("no big bills: no empty details", () => {
    recurring = [mortgage];
    show();
    expect(within(card()).queryByTestId("obligations-all-bills")).toBeNull();
  });
});

describe("the obligations tooltip escapes the vendor", () => {
  it("a vendor carrying HTML renders as text, never as markup", () => {
    const evil = rec({ vendor: "<img src=x onerror=alert(1)>", cadence: "annual", typical_amount: 1800,
      category: "Insurance", next_expected: "2026-07-10" });
    recurring = [evil];
    show();
    const opt = obligationsOption();
    const html: string = opt.tooltip.formatter([{ dataIndex: 0 }]);   // the only month with a bill: 2026-07
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).toContain("<b>");   // the tooltip's own markup is intact
  });
});

// No figure the data cannot support: with nothing detected the four tiles have no subject, so each reads "—" and says
// so in a few words; with charges detected but none active the count is a true 0 and the costs are no sum over nothing.
// The warning tone needs an increase to warn about.
describe("Recurring's tiles follow what was detected", () => {
  const tiles = () => [...screen.getByTestId("kpis-recurring").children].map((t) => t.textContent ?? "");
  const amber = () => screen.getByTestId("kpis-recurring").querySelector(".text-amber-400");
  it("nothing detected: four tiles of \"—\", each saying none is detected yet, none in a warning tone", () => {
    show();
    expect(tiles()).toEqual(["Active recurring charges—none detected yet", "Monthly cost—none detected yet",
      "Annualized—none detected yet", "Price increases seen—none detected yet"]);
    expect(amber(), "no orange without an increase").toBeNull();
  });
  it("charges detected and none active: a true 0 active, no cost summed over nothing, a true 0 increases in no warning tone", () => {
    recurring = [rec({ active: false })];
    show();
    expect(tiles()).toEqual(["Active recurring charges0", "Monthly cost—no active charge", "Annualized—no active charge",
      "Price increases seen0"]);
    expect(amber()).toBeNull();
  });
  it("the year is the export's yearly sums, to the cent, not the rounded monthly figures times twelve", () => {
    // thirty annual $1,000 bills: $83.33 a month each (a twelfth, rounded), $1,000 a year each
    recurring = Array.from({ length: 30 }, (_, i) => rec({ vendor: `Renewal ${i}`, cadence: "annual", typical_amount: 1_000,
      monthly_equivalent: 83.33, annualized: 1_000 }));
    show();
    expect(tiles()[1]).toBe("Monthly cost$2,500");
    expect(tiles()[2]).toBe("Annualized$30,000");                    // not $29,999 (30 × 83.33 × 12)
  });
  it("a quarterly item: a third of its amount a month and four of them a year, as the bills chart counts it", () => {
    recurring = [rec({ vendor: "Property tax", cadence: "quarterly", typical_amount: 900, monthly_equivalent: 300,
      annualized: 3_600, next_expected: "2026-08-01" })];
    show();
    expect(tiles().slice(1, 3)).toEqual(["Monthly cost$300", "Annualized$3,600"]);
    expect(obligationsOption().series[1].data.reduce((s: number, v: number) => s + v, 0), "four charges in the twelve bars")
      .toBe(3_600);
  });
  it("an increase seen: the count is in the warning tone", () => {
    recurring = [rec({ price_changes: [{ date: "2026-03-05", from: 15, to: 20 }] })];
    show();
    expect(tiles()[3]).toBe("Price increases seen1");
    expect(amber()?.textContent).toBe("1");
  });
});

// "still using it?" marks a small charge that has run a year or more in a subscription-like category. A rename changes
// the category the item carries; the flag must follow it.
describe("the still-using-it flag follows a category rename", () => {
  const sub = (category: string) => rec({ vendor: "Old Gym", cadence: "monthly", typical_amount: 20, monthly_equivalent: 20,
    category, first_seen: "2024-01-05", last_seen: "2026-05-20", next_expected: "2026-06-05" });
  const flagged = () => screen.queryAllByText("still using it?").length;
  it("a renamed Subscriptions category is still flagged", () => {
    renameList = [{ from: { category: "Subscriptions" }, to: { category: "Memberships" } }];
    recurring = [sub("Memberships")];
    show();
    expect(flagged()).toBeGreaterThan(0);
  });
  it("the shipped Subscriptions and Entertainment are flagged; a bill category is not", () => {
    recurring = [sub("Subscriptions")];
    show();
    expect(flagged()).toBeGreaterThan(0);
    cleanup();
    recurring = [sub("Insurance")];
    show();
    expect(flagged()).toBe(0);
  });
  it("with no rename known the renamed category is no subscription category (control)", () => {
    recurring = [sub("Memberships")];
    show();
    expect(flagged()).toBe(0);
  });
});
