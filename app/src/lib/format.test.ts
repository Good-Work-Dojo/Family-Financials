// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { countOf, fmtChangeShare, fmtCompact, fmtCompactDelta,fmtDate, fmtDelta, fmtMonth, fmtMonthAxis, fmtMonths, fmtPct, fmtPoints, fmtRate, fmtUSD, fmtUSD0, fmtUSDExact, hoursSince, humanizeDates, rateTone } from "./format";
import { MASK, setPrivate } from "./privacy";

describe("rateTone", () => {
  it("green at or above zero, red below, neutral (undefined) for a null rate", () => {
    expect(rateTone(0.2)).toBe("pos");
    expect(rateTone(0)).toBe("pos");
    expect(rateTone(-0.01)).toBe("neg");
    expect(rateTone(null)).toBeUndefined();
  });
});

describe("the money formatters", () => {
  afterEach(() => setPrivate(false));
  it("print whole dollars, cents, a compact K/M form and a signed delta", () => {
    expect(fmtUSD(1234.5)).toBe("$1,234.50");
    expect(fmtUSD0(1234.5)).toBe("$1,235");
    expect(fmtCompact(1_250_000)).toBe("$1.25M");
    expect(fmtCompact(45_000)).toBe("$45K");
    expect(fmtCompact(1_500)).toBe("$1.5K");
    expect(fmtCompact(450)).toBe("$450");
    expect(fmtDelta(1234)).toBe("+$1,234");
  });
  // the unit is chosen by the value as it prints, so a value that rounds up to the next unit is written in that unit
  it("fmtCompact picks its unit after rounding: never $1000K, $1000 or $10.00M", () => {
    expect(fmtCompact(999_600)).toBe("$1.00M");
    expect(fmtCompact(-999_600)).toBe("−$1.00M");
    expect(fmtCompact(999_499)).toBe("$999K");
    expect(fmtCompact(999.6)).toBe("$1.0K");
    expect(fmtCompact(999.4)).toBe("$999");
    expect(fmtCompact(9_960)).toBe("$10K");
    expect(fmtCompact(9_940)).toBe("$9.9K");
    expect(fmtCompact(9_996_000)).toBe("$10.0M");
    expect(fmtCompact(9_990_000)).toBe("$9.99M");
    expect(fmtCompact(10_000_000)).toBe("$10.0M");
  });
  // the share a change is of its base: a whole percent, and one decimal under 1% so a real change never reads "(0%)"
  it("fmtChangeShare prints a whole percent, one decimal under 1%, and the U+2212 minus", () => {
    expect(fmtChangeShare(0.25)).toBe("25%");
    expect(fmtChangeShare(0.003346)).toBe("0.3%");
    expect(fmtChangeShare(-0.004)).toBe("−0.4%");
    expect(fmtChangeShare(0.0099)).toBe("1.0%");
    expect(fmtChangeShare(-0.1)).toBe("−10%");
  });
  // one minus for every money formatter (U+2212, the typographic one fmtDelta always used), so a negative amount
  // reads the same in a KPI, an axis, a delta and a register row
  it("a negative amount uses the same U+2212 minus in all four formatters", () => {
    const minus = "\u2212";
    expect(fmtUSD(-12.5)).toBe(`${minus}$12.50`);
    expect(fmtUSD0(-987)).toBe(`${minus}$987`);
    expect(fmtCompact(-1_250_000)).toBe(`${minus}$1.25M`);
    expect(fmtCompact(-450)).toBe(`${minus}$450`);
    expect(fmtDelta(-987)).toBe(`${minus}$987`);
    for (const s of [fmtUSD(-1), fmtUSD0(-1), fmtCompact(-1), fmtDelta(-1)]) expect(s.includes("-"), s).toBe(false);
  });
  // IEEE -0 (what `-(0 * rate)` makes) and a small negative rounded to nothing printed "−$0": a minus sign on nothing
  // (a computed figure of zero, such as a rate applied to nothing). A value that rounds to zero is a plain zero.
  it("never prints a minus on zero: negative zero, and a negative that rounds to zero, read as a plain $0", () => {
    const zero = [-0, 0, -0.3, 0.3, -0.49];
    for (const v of zero) {
      expect(fmtUSD0(v), `fmtUSD0(${v})`).toBe("$0");
      expect(fmtCompact(v), `fmtCompact(${v})`).toBe("$0");
      expect(fmtDelta(v), `fmtDelta(${v})`).toBe("$0");   // a change of nothing is not "+$0"
    }
    expect(fmtUSD(-0)).toBe("$0.00");
    expect(fmtUSD(-0.004)).toBe("$0.00");
    // and a real small negative keeps its sign: -$0.50 is one dollar of debt at whole-dollar precision
    expect(fmtUSD0(-0.5)).toBe("\u2212$1");
    expect(fmtCompact(-0.5)).toBe("\u2212$1");
    expect(fmtUSD(-0.01)).toBe("\u2212$0.01");
    expect(fmtDelta(-0.5)).toBe("\u2212$1");
    expect(fmtDelta(0.5), "a real small change keeps its plus").toBe("+$1");
  });
  // the Home waterfall's bar labels: a zero bar read "+$0", and in privacy mode a bare sign stood in front of the mask
  it("fmtCompactDelta signs a real change, prints a change of nothing as $0, and is the mask alone in privacy mode", () => {
    expect(fmtCompactDelta(1250)).toBe("+$1.3K");
    expect(fmtCompactDelta(450)).toBe("+$450");
    expect(fmtCompactDelta(-450)).toBe("\u2212$450");
    expect(fmtCompactDelta(-1_250_000)).toBe("\u2212$1.25M");
    for (const v of [0, -0, 0.3, -0.3, 0.49]) expect(fmtCompactDelta(v), `fmtCompactDelta(${v})`).toBe("$0");
    expect(fmtCompactDelta(0.5), "a real small change keeps its plus").toBe("+$1");
    setPrivate(true);
    for (const v of [0, 450, -450, 1250]) expect(fmtCompactDelta(v), `private ${v}`).toBe(MASK);
  });
  it("mask every amount while privacy mode is on, whatever the sign, and print normally again when it is off", () => {
    setPrivate(true);
    for (const v of [0, 1234.5, -1234.5, 1_250_000, -50]) {
      expect([fmtUSD(v), fmtUSD0(v), fmtCompact(v), fmtDelta(v)], String(v)).toEqual([MASK, MASK, MASK, MASK]);
    }
    expect(fmtPct(0.123), "a ratio is not an amount").toBe("12.3%");
    expect(fmtPct(null)).toBe("—");
    setPrivate(false);
    expect(fmtUSD0(-987)).toBe("\u2212$987");
  });
  it("fmtUSDExact prints an amount as it is used: whole dollars when whole to the cent, else the cents; masked", () => {
    expect([fmtUSDExact(1400), fmtUSDExact(2746.4), fmtUSDExact(33.5), fmtUSDExact(50_000.004), fmtUSDExact(-12.5)])
      .toEqual(["$1,400", "$2,746.40", "$33.50", "$50,000", "\u2212$12.50"]);
    setPrivate(true);
    expect(fmtUSDExact(2746.4)).toBe(MASK);
  });
});

describe("countOf", () => {
  it("agrees the noun with the count: one is singular, zero and many are plural, and big counts are grouped", () => {
    expect(countOf(1, "open question")).toBe("1 open question");
    expect(countOf(0, "open question")).toBe("0 open questions");
    expect(countOf(2, "warning")).toBe("2 warnings");
    expect(countOf(6198, "transaction")).toBe("6,198 transactions");
    expect(countOf(1, "row")).toBe("1 row");
    expect(countOf(3, "child", "children")).toBe("3 children");
  });
});

describe("fmtPct", () => {
  it("renders fractions as percentages", () => {
    expect(fmtPct(0.123)).toBe("12.3%");
    expect(fmtPct(0.1234, 2)).toBe("12.34%");
    expect(fmtPct(0)).toBe("0.0%");
    expect(fmtPct(-0.05, 0)).toBe("\u22125%");        // the U+2212 minus the money formatters print, not a hyphen
    expect(fmtPct(-0.123)).toBe("\u221212.3%");
  });
  it("renders null (no data) as an em dash, never 0.0%", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(null, 0)).toBe("—");
  });
});

describe("fmtPoints", () => {
  it("a gap in percentage points: the one minus, an explicit plus, and a plain zero when it rounds to nothing", () => {
    expect(fmtPoints(3.24)).toBe("+3.2 pts");
    expect(fmtPoints(-3.24)).toBe("\u22123.2 pts");
    expect(fmtPoints(-0.04)).toBe("0.0 pts");
    expect(fmtPoints(0)).toBe("0.0 pts");
    expect(fmtPoints(-12, 0)).toBe("\u221212 pts");
  });
});

describe("fmtMonth", () => {
  it("spells the year out so a month can't read like a day of the month (\"Jul 26\" is July 26th)", () => {
    expect(fmtMonth("2025-12")).toBe("Dec 2025");
    expect(fmtMonth("2026-05")).toBe("May 2026");
    expect(fmtMonth("2026-07")).toBe("Jul 2026");
    expect(fmtMonth("2026-07")).not.toBe("Jul 26");
  });
});

describe("fmtMonthAxis", () => {
  it("is the short form for a chart axis: an apostrophe says the two digits are a year", () => {
    expect(fmtMonthAxis("2026-07")).toBe("Jul \u201926");
    expect(fmtMonthAxis("2025-12")).toBe("Dec \u201925");
    expect(fmtMonthAxis("2009-03")).toBe("Mar \u201909");        // a leading zero stays
  });
});

// The freshness pill's age. It needs a real instant — the manifest's simplefin_fetched_at is UTC with a Z — so that the
// age is right regardless of where the viewer's browser thinks it is.
const NOW = new Date("2026-08-11T12:00:00Z");
const ago = (mins: number) =>
  new Date(NOW.getTime() - mins * 60_000).toISOString();

// fmtRelative, fmtDateTime, fmtTime and zoneName print clock times: lib/clockTimes.test.ts, which the gate also runs in
// two other process zones.

describe("hoursSince", () => {
  it("drives the pill tone thresholds", () => {
    expect(hoursSince(ago(9 * 60), NOW)).toBeCloseTo(9);
    expect(hoursSince(ago(27 * 60), NOW)).toBeCloseTo(27);
  });
  it("is null when there is no timestamp to judge", () => {
    expect(hoursSince(null, NOW)).toBeNull();
    expect(hoursSince("garbage", NOW)).toBeNull();
  });
});

// months of cover (cash ÷ essentials): a ratio, so - like a percentage - it is not masked; it prints the same minus
// and the same digit grouping as every other number on a KPI, never toFixed's bare "-1234.5"
describe("fmtMonths", () => {
  afterEach(() => setPrivate(false));
  it("one decimal, grouped, a U+2212 minus, and — for null (nothing to divide by)", () => {
    expect(fmtMonths(4.25)).toBe("4.3 mo");
    expect(fmtMonths(0)).toBe("0.0 mo");
    expect(fmtMonths(-3.21)).toBe("\u22123.2 mo");
    expect(fmtMonths(-1234.5)).toBe("\u22121,234.5 mo");
    expect(fmtMonths(null)).toBe("—");
    expect(fmtMonths(-2.5, "")).toBe("\u22122.5");                       // prose that says "months" itself
  });
  it("is a ratio, not an amount: privacy mode leaves it", () => {
    setPrivate(true);
    expect(fmtMonths(4.25)).toBe("4.3 mo");
  });
});

// A tiny negative rounds to nothing and used to print a minus sign on it ("-0.0%", "−0.0 mo"), as the money formatters did with "−$0".
describe("a ratio that rounds to zero is a plain zero", () => {
  it("fmtPct: -0, -0.00004 and -0.0004 read 0.0% / 0%, while a real small negative keeps its sign", () => {
    for (const v of [-0, -0.00004, -0.0004]) expect(fmtPct(v), String(v)).toBe("0.0%");
    expect(fmtPct(-0.0004, 0)).toBe("0%");
    expect(fmtPct(-0.0004, 2)).toBe("\u22120.04%");
    expect(fmtPct(-0.0005)).toBe("\u22120.1%");
  });
  it("fmtMonths: -0, -0.03 read 0.0 (no minus), -0.05 keeps its", () => {
    const minus = "\u2212";
    for (const v of [-0, -0.03, 0.03]) expect(fmtMonths(v), String(v)).toBe("0.0 mo");
    expect(fmtMonths(-0.03, "")).toBe("0.0");
    expect(fmtMonths(-0.05)).toBe(`${minus}0.1 mo`);
    expect(fmtMonths(4.3)).toBe("4.3 mo");
    expect(fmtMonths(null)).toBe("—");
  });
});

// One precision for every rate in prose: a whole percent when it is one, else one decimal - never "9%" for 8.75%.
describe("fmtRate", () => {
  it("11% stays 11%, 8.75% is 8.8%, 9% is 9% (no float dust), 9.5% is 9.5%, 10% is 10%", () => {
    expect(fmtRate(0.11)).toBe("11%");
    expect(fmtRate(0.0875)).toBe("8.8%");
    expect(fmtRate(0.09)).toBe("9%");
    expect(fmtRate(0.07)).toBe("7%");
    expect(fmtRate(0.095)).toBe("9.5%");
    expect(fmtRate(0.1)).toBe("10%");
    expect(fmtRate(0)).toBe("0%");
  });
});

// One sign convention: a negative amount is "\u2212$X" through the formatters. A template that wraps a money formatter in parentheses
// ("($X)", the accounting form) is a second convention, so the source may not build one.
describe("no parenthesized negatives in the source", () => {
  const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
  const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name))
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
  const PAREN_MONEY = /`\(\$\{\s*fmt(?:USD0?|Compact|Delta)\(/;
  it("the pattern flags the accounting form it exists to forbid", () => {
    expect(PAREN_MONEY.test("{neg ? `(${fmtUSD0(l.value)})` : fmtUSD0(l.value)}")).toBe(true);
    expect(PAREN_MONEY.test("`${name} (${fmtUSD0(v)})`")).toBe(false);   // a parenthetical after text is prose, not a negative
  });
  it("no template in app/src opens a parenthesis straight onto a money formatter", () => {
    const all = files(SRC);
    expect(all.length).toBeGreaterThan(50);
    const hits = all.filter((f) => PAREN_MONEY.test(readFileSync(f, "utf8"))).map((f) => f.slice(SRC.length + 1));
    expect(hits).toEqual([]);
  });
});

// Free text the ETL worded for a machine's reader carries ISO dates; on screen they read the way the rest of the app
// speaks them. A render-time rewrite only - the export, ids and codes are never touched.
describe("humanizeDates", () => {
  it("rewrites an ISO date as fmtDate, an ISO month as fmtMonth and a US slash date as fmtDate, whatever the punctuation around it", () => {
    expect(humanizeDates("the Venmo wallet export ends 2026-07-10 but 3 debits posted after it, latest 2026-09-14.")).toBe(
      `the Venmo wallet export ends ${fmtDate("2026-07-10")} but 3 debits posted after it, latest ${fmtDate("2026-09-14")}.`);
    expect(humanizeDates("since 2026-07 (2026-07-01)")).toBe(`since ${fmtMonth("2026-07")} (${fmtDate("2026-07-01")})`);
    expect(humanizeDates("e.g. 2024-11-26 Acme Card")).toBe(`e.g. ${fmtDate("2024-11-26")} Acme Card`);
    expect(humanizeDates("2026-07-10")).toBe("Jul 10, 2026");
    expect(humanizeDates("lot acquired 11/18/2019, moved 2024-03-07")).toBe("lot acquired Nov 18, 2019, moved Mar 7, 2024");
    expect(humanizeDates("2026-01-05 \u2192 2026-02-01")).toBe("Jan 5, 2026 \u2192 Feb 1, 2026");
    expect(humanizeDates("2026-07-01 2026-07-02,2026-07-03")).toBe("Jul 1, 2026 Jul 2, 2026,Jul 3, 2026");
  });
  it("is the identity on text with no date, and applying it twice changes nothing more", () => {
    for (const s of ["", "no dates here", "12 of 14 bank Venmo debits linked", "3.2% of 2026", "2,026", "pi 3.14159"]) expect(humanizeDates(s), s).toBe(s);
    const once = humanizeDates("ends 2026-07-10, 11/18/2019 and 2026-07");
    expect(humanizeDates(once)).toBe(once);
  });
  it("leaves an id, a version, a timestamp or a token that is not a real date exactly as written", () => {
    for (const s of ["v2026-10", "tx_2026-10-01", "acct-2026-10-01", "A2026-10", "demo_checking_0101-2026-10", "1.2026-10", "id 2026-10-0123",
      "2026-10-01abc", "2026-10-01T05:00:00Z", "ref 2026-10-01-02", "path/2026-10-01", "2026-10-01/2026-11-01", "v1.2.3",
      "2026-13-45", "2026-02-31", "2026-00-10", "2026-13", "13/45/2026", "2/30/2026", "1/2/20267", "1234-56", "3014-07-10", "1850-01-01"]) {
      expect(humanizeDates(s), s).toBe(s);
    }
    // ...and a real date next to an id is rewritten without touching the id
    expect(humanizeDates("tx_2026-10-01 posted 2026-10-02")).toBe("tx_2026-10-01 posted Oct 2, 2026");
  });
  it("leap days are real and the 30th of February is not", () => {
    expect(humanizeDates("2028-02-29")).toBe("Feb 29, 2028");
    expect(humanizeDates("2026-02-29")).toBe("2026-02-29");
  });
});

// One way to sign an amount: fmtDelta ("+$5", "$0", "\u2212$5", masked in privacy mode). A hand-signed amount
// (`{d >= 0 ? "+" : ""}{fmtUSD0(d)}`) printed "+$0" for an unchanged row and a "+" in front of the privacy mask.
describe("no hand-signed amounts in the source", () => {
  const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
  const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name))
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
  const HAND_SIGNED = new RegExp([
    String.raw`\?\s*"\+"\s*:\s*""\s*\}\s*\$?\{\s*fmt(?:USD0?|Compact)\(`,                         // {d >= 0 ? "+" : ""}{fmtUSD0(d)}
    String.raw`\?\s*"\+"\s*:\s*"[\u2212-]"\s*\)\s*\+\s*fmt(?:USD0?|Compact)\(`,                     // (v >= 0 ? "+" : "−") + fmtCompact(...)
  ].join("|"));
  it("the pattern flags the hand-signed form it exists to forbid", () => {
    expect(HAND_SIGNED.test('{d >= 0 ? "+" : ""}{fmtUSD0(d)}')).toBe(true);
    expect(HAND_SIGNED.test('`${v >= 0 ? "+" : ""}${fmtCompact(v)}`')).toBe(true);
    expect(HAND_SIGNED.test('(v >= 0 ? "+" : "\u2212") + fmtCompact(Math.abs(v))')).toBe(true);
    expect(HAND_SIGNED.test('`${pct >= 0 ? "+" : ""}${fmtPct(pct, 1)}`')).toBe(false);   // a ratio is not an amount
  });
  it("nothing outside lib/format.ts signs a money formatter's output itself", () => {
    const all = files(SRC);
    expect(all.length).toBeGreaterThan(50);
    // lib/format.ts is where an amount gets its sign (fmtDelta, fmtCompactDelta); everything else asks it
    expect(all.filter((f) => !f.endsWith(join("lib", "format.ts"))).filter((f) => HAND_SIGNED.test(readFileSync(f, "utf8"))).map((f) => f.slice(SRC.length + 1))).toEqual([]);
  });
});
