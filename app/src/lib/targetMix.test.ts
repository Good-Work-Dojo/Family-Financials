// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { afterEach, describe, expect, it } from "vitest";
import type { Holding } from "../data/types";
import { MASK, setPrivate } from "./privacy";
import {
  bucketTotals, isOff, MIX_KEYS, MIX_TOLERANCE_PTS, mixBucket, mixRows, mixSummary, moveWords, nothingToCompare, parseTargetMix,
  sameMix, shownPts, type TargetMix, withLeg,
} from "./targetMix";

afterEach(() => setPrivate(false));

// synthetic holdings: round numbers, made-up names
const h = (asset_class: string, value: number, symbol: string | null = null, name = "Fund"): Pick<Holding, "asset_class" | "symbol" | "name" | "value"> =>
  ({ asset_class, value, symbol, name });
const total = (m: TargetMix) => MIX_KEYS.reduce((s, k) => s + m[k], 0);
const MIX: TargetMix = { stocks: 60, bonds: 30, cash: 5, alts: 5 };

describe("mixBucket", () => {
  it("bond-like funds by symbol or name are bonds; crypto, private stock and real estate/venture are alternatives", () => {
    expect(mixBucket(h("Stocks/ETF", 1, "VTEB"))).toBe("bonds");
    expect(mixBucket(h("Retirement", 1, null, "Total Bond Market Index"))).toBe("bonds");
    expect(mixBucket(h("Retirement", 1, null, "Short-Term Treasury"))).toBe("bonds");
    expect(mixBucket(h("Crypto", 1, "BTC"))).toBe("alts");
    expect(mixBucket(h("Precious Metals", 1, "XAU"))).toBe("alts");
    expect(mixBucket(h("Private Stock", 1))).toBe("alts");
    expect(mixBucket(h("Real Estate/Venture", 1))).toBe("alts");
    expect(mixBucket(h("Cash", 1, null, "Sweep"))).toBe("cash");
  });
  it("every other position is stocks (a balance-only account has no position and is never passed in: the card keeps it apart)", () => {
    expect(mixBucket(h("Stocks/ETF", 1, "VTI", "Total Stock Market"))).toBe("stocks");
    expect(mixBucket(h("529", 1, null, "Age-based portfolio"))).toBe("stocks");
  });
});

describe("bucketTotals", () => {
  it("sums dollars per bucket; an empty bucket is 0 and the total is the four together", () => {
    const { by, total: t } = bucketTotals([h("Stocks/ETF", 600, "VTI"), h("Stocks/ETF", 300, "BND"), h("Crypto", 100, "ETH")]);
    expect(by).toEqual({ stocks: 600, bonds: 300, cash: 0, alts: 100 });
    expect(t).toBe(1000);
  });
  it("no holdings is all zeros", () => {
    expect(bucketTotals([])).toEqual({ by: { stocks: 0, bonds: 0, cash: 0, alts: 0 }, total: 0 });
  });
});

describe("parseTargetMix", () => {
  it("keeps a mix whose four buckets are 0-100 and total 100, rebuilt from the four keys alone", () => {
    expect(parseTargetMix({ ...MIX, extra: 9 })).toEqual(MIX);
    expect(parseTargetMix({ stocks: 33.3, bonds: 33.3, cash: 33.4, alts: 0 })).toEqual({ stocks: 33.3, bonds: 33.3, cash: 33.4, alts: 0 });
    expect(parseTargetMix({ stocks: 100, bonds: 0, cash: 0, alts: 0 })).toEqual({ stocks: 100, bonds: 0, cash: 0, alts: 0 });
  });
  it("refuses a negative, an over-100, a non-number, a missing bucket and a total that is not 100", () => {
    for (const bad of [
      { ...MIX, cash: -5, bonds: 40 }, { stocks: 110, bonds: -10, cash: 0, alts: 0 }, { ...MIX, cash: "5" }, { ...MIX, cash: NaN },
      { ...MIX, cash: Infinity }, { stocks: 60, bonds: 30, cash: 10 }, { ...MIX, bonds: 29 }, { ...MIX, bonds: 31 }, { ...MIX, alts: true },
    ]) expect(parseTargetMix(bad), JSON.stringify(bad)).toBeNull();
  });
  it("anything that is not an object is null", () => {
    for (const bad of [null, undefined, 0, "mix", [60, 30, 5, 5]]) expect(parseTargetMix(bad)).toBeNull();
  });
});

describe("withLeg (a slider moves; bonds takes the rest)", () => {
  it("sets the leg and moves bonds by the same amount the other way", () => {
    expect(withLeg(MIX, "stocks", 70)).toEqual({ stocks: 70, bonds: 20, cash: 5, alts: 5 });
    expect(withLeg(MIX, "cash", 10)).toEqual({ stocks: 60, bonds: 25, cash: 10, alts: 5 });
  });
  it("rounds to whole percents, so the four always total exactly 100", () => {
    expect(withLeg(MIX, "alts", 7.6)).toEqual({ stocks: 60, bonds: 27, cash: 5, alts: 8 });
    const saved = { stocks: 33.3, bonds: 33.3, cash: 33.4, alts: 0 };          // a hand-saved fractional mix
    const next = withLeg(saved, "alts", 0);
    expect(next).toEqual({ stocks: 33, bonds: 34, cash: 33, alts: 0 });
    for (const leg of ["stocks", "cash", "alts"] as const) {
      for (let v = -20; v <= 130; v += 7.3) {
        const m = withLeg(saved, leg, v);
        expect(total(m), `${leg} ${v}`).toBe(100);
        expect(MIX_KEYS.every((k) => Number.isInteger(m[k]) && m[k] >= 0 && m[k] <= 100), `${leg} ${v} whole and in range`).toBe(true);
      }
    }
  });
  it("holds a leg between 0 and the room the other two leave (bonds never goes negative)", () => {
    expect(withLeg(MIX, "stocks", 120)).toEqual({ stocks: 90, bonds: 0, cash: 5, alts: 5 });
    expect(withLeg(MIX, "stocks", -10)).toEqual({ stocks: 0, bonds: 90, cash: 5, alts: 5 });
    expect(withLeg({ stocks: 90, bonds: 0, cash: 5, alts: 5 }, "cash", 30)).toEqual({ stocks: 90, bonds: 0, cash: 5, alts: 5 });
  });
  it("a non-finite value leaves the mix alone", () => {
    expect(withLeg(MIX, "stocks", NaN)).toBe(MIX);
  });
  it("two other legs that already pass 100 (never written here, but a doc is a doc) are shaved to fit", () => {
    const m = withLeg({ stocks: 0, bonds: 0, cash: 50.5, alts: 50.5 } as TargetMix, "stocks", 10);
    expect(total(m)).toBe(100);
    expect(m.stocks).toBe(0);
  });
});

describe("sameMix", () => {
  it("compares every bucket; null only equals null", () => {
    expect(sameMix(MIX, { ...MIX })).toBe(true);
    expect(sameMix(MIX, withLeg(MIX, "stocks", 61))).toBe(false);
    expect(sameMix(null, null)).toBe(true);
    expect(sameMix(MIX, null)).toBe(false);
  });
});

describe("mixRows", () => {
  it("actual share, drift in points (positive = over) and the dollars to buy (+) or sell (−) per bucket", () => {
    const { by, total: t } = bucketTotals([h("Stocks/ETF", 700, "VTI"), h("Stocks/ETF", 200, "BND"), h("Crypto", 100, "BTC")]);
    const rows = mixRows(by, t, MIX);
    expect(rows.map((r) => r.key)).toEqual(["stocks", "bonds", "cash", "alts"]);
    const stocks = rows[0];
    expect(stocks.actualPct).toBeCloseTo(70);
    expect(stocks.driftPts).toBeCloseTo(10);
    expect(stocks.move).toBeCloseTo(-100);                  // sell 100 of 1000 to get to 60%
    expect(rows[2]).toMatchObject({ actual: 0, actualPct: 0, driftPts: -5, move: 50 });   // the empty cash bucket: buy
    expect(rows.reduce((s, r) => s + r.move!, 0)).toBeCloseTo(0);          // what is sold is what is bought
  });
  it("a 100%-cash portfolio: cash is the whole drift the other way", () => {
    const { by, total: t } = bucketTotals([h("Cash", 5000, null, "Sweep")]);
    const rows = mixRows(by, t, MIX);
    expect(rows.find((r) => r.key === "cash")).toMatchObject({ actualPct: 100, driftPts: 95, move: -4750 });
    expect(rows.find((r) => r.key === "stocks")).toMatchObject({ actualPct: 0, driftPts: -60, move: 3000 });
  });
  it("nothing invested: no share, no drift, no dollars - never a 0% that looks measured", () => {
    const rows = mixRows(bucketTotals([]).by, 0, MIX);
    expect(rows.every((r) => r.actualPct === null && r.driftPts === null && r.move === null)).toBe(true);
    expect(rows.map((r) => r.targetPct)).toEqual([60, 30, 5, 5]);
  });
});

describe("mixSummary", () => {
  const rowsAt = (stocks: number, bonds: number, cash: number, alts: number) =>
    mixRows({ stocks, bonds, cash, alts }, stocks + bonds + cash + alts, MIX);
  it(`within ${MIX_TOLERANCE_PTS} points on every bucket, the boundary included`, () => {
    expect(mixSummary(rowsAt(65, 25, 5, 5))).toBe("Within 5 points of your target");
    expect(mixSummary(rowsAt(62, 28, 5, 5), "this mix")).toBe("Within 5 points of this mix");
  });
  it("otherwise names the bucket furthest off, over or under, and the dollars to move", () => {
    expect(mixSummary(rowsAt(6800, 2200, 500, 500))).toBe("Stocks 8.0 pts over — about $800 to move");
    expect(mixSummary(rowsAt(6000, 2200, 1300, 500))).toBe("Bonds 8.0 pts under — about $800 to move");   // a tie goes to the first
  });
  it("the rule reads the drift the table prints (one decimal): +5.04 shows +5.0 and is within, +5.1 is off and says 5.1", () => {
    expect(mixSummary(rowsAt(65040, 24960, 5000, 5000))).toBe("Within 5 points of your target");   // 65.04% vs 60
    expect(mixSummary(rowsAt(6510, 2500, 490, 500))).toBe("Stocks 5.1 pts over — about $510 to move");
    expect(mixSummary(rowsAt(6530, 2470, 500, 500)), "never rounded to a whole point").toMatch(/^Stocks 5\.3 pts over/);
  });
  it("nothing invested says so", () => {
    expect(mixSummary(mixRows(bucketTotals([]).by, 0, MIX))).toBe("Nothing invested to compare yet");
  });
  it("the dollars go through the masked formatter", () => {
    setPrivate(true);
    expect(mixSummary(rowsAt(6800, 2200, 500, 500))).toBe(`Stocks 8.0 pts over — about ${MASK} to move`);
  });
});

describe("nothingToCompare", () => {
  it("a classified position there: nothing to say in place of a verdict", () => {
    expect(nothingToCompare(1, ["A 401(k)"])).toBeNull();
  });
  it("balance-only accounts alone: nothing classified, and why, in the singular or the plural", () => {
    expect(nothingToCompare(0, ["A 401(k)"])).toBe("Nothing classified to compare yet. A 401(k) reports a balance but no positions.");
    expect(nothingToCompare(0, ["A 401(k)", "B IRA", "C IRA"]))
      .toBe("Nothing classified to compare yet. A 401(k), B IRA and C IRA report a balance but no positions.");
  });
  it("nothing invested at all: that", () => {
    expect(nothingToCompare(0, [])).toBe("Nothing invested to compare yet.");
  });
});

describe("shownPts / isOff (one rule for the table's amber and the summary)", () => {
  it("a drift's size at one decimal; off only when that is past the tolerance; nothing to compare is never off", () => {
    expect(shownPts(-5.04)).toBe(5);
    expect(shownPts(5.06)).toBe(5.1);
    const row = (driftPts: number | null) => ({ key: "stocks" as const, label: "Stocks", actual: 0, actualPct: null, targetPct: 0, driftPts, move: null });
    expect(isOff(row(5.04))).toBe(false);
    expect(isOff(row(-5.06))).toBe(true);
    expect(isOff(row(null))).toBe(false);
  });
});

describe("moveWords", () => {
  it("sell over target, buy under it, a dash when it rounds to nothing or there is nothing to compare", () => {
    expect(moveWords(-1234.4)).toBe("sell $1,234");
    expect(moveWords(250)).toBe("buy $250");
    expect(moveWords(0.4)).toBe("—");
    expect(moveWords(-0.4)).toBe("—");
    expect(moveWords(null)).toBe("—");
  });
  it("masks the dollars in privacy mode, keeping the verb", () => {
    setPrivate(true);
    expect(moveWords(-1234)).toBe(`sell ${MASK}`);
  });
});
