// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { BalancePoint } from "../data/types";
import { historySince, lookbackRows } from "./balanceLookback";

function pt(date: string, balance: number): BalancePoint {
  return { date, balance, available: null, limit: null, utilization: null,
           source: "simplefin" };
}

/** dense daily series ending at `end`, going back `n` days, balance ramps up */
function daily(end: string, n: number, endBalance: number, step = 10): BalancePoint[] {
  const out: BalancePoint[] = [];
  const d = new Date(end + "T00:00:00Z");
  for (let i = n - 1; i >= 0; i--) {
    const dd = new Date(d);
    dd.setUTCDate(dd.getUTCDate() - i);
    out.push(pt(dd.toISOString().slice(0, 10), endBalance - i * step));
  }
  return out;
}

describe("lookbackRows", () => {
  it("sparse real-world shape: only the 1w row qualifies", () => {
    // mirrors the actual data: a few points inside three weeks
    const rows = lookbackRows([
      pt("2026-06-19", 1000), pt("2026-06-21", 1010), pt("2026-06-27", 1040),
      pt("2026-06-28", 1050),
    ]);
    expect(rows.map((r) => r.label)).toEqual(["1w"]);
    expect(rows[0].anchorDate).toBe("2026-06-21");   // latest point ≤ 06-21 target
    expect(rows[0].delta).toBe(40);
    expect(rows[0].pct).toBeCloseTo(40 / 1010);
  });

  it("dense year-long series: all five lookbacks, anchors near targets", () => {
    const rows = lookbackRows(daily("2026-06-28", 400, 5000, 1));
    expect(rows.map((r) => r.label)).toEqual(["1w", "1mo", "3mo", "6mo", "1y"]);
    expect(rows[4].anchorDate).toBe("2025-06-28");   // exactly 365 days back
    expect(rows[4].delta).toBe(365);
  });

  it("staleness gate: anchor older than target − N/2 hides the row", () => {
    // two points 12 days apart: 1w target (day −7) anchors on day −12 →
    // overshoot 5 > ceil(7/2)=4 → hidden; 1mo target has no anchor → hidden
    const rows = lookbackRows([pt("2026-06-16", 900), pt("2026-06-28", 1000)]);
    expect(rows).toEqual([]);
    // 10 days apart: overshoot 3 ≤ 4 → shown, with the real anchor date
    const ok = lookbackRows([pt("2026-06-18", 900), pt("2026-06-28", 1000)]);
    expect(ok.map((r) => r.label)).toEqual(["1w"]);
    expect(ok[0].anchorDate).toBe("2026-06-18");
  });

  it("cards/loans stored negative: paydown is a positive (green) delta", () => {
    const card = lookbackRows(daily("2026-06-28", 10, -200, 50)); // −650 → −200: owed shrinking
    expect(card[0].delta).toBeGreaterThan(0);
    expect(card[0].pct).toBeGreaterThan(0);
  });

  it("zero anchor balance → pct null, delta still shown", () => {
    const rows = lookbackRows([pt("2026-06-20", 0), pt("2026-06-28", 500)]);
    expect(rows[0].delta).toBe(500);
    expect(rows[0].pct).toBeNull();
  });

  it("single point → no rows (no chevron)", () => {
    expect(lookbackRows([pt("2026-06-28", 1000)])).toEqual([]);
  });

  it("historySince reports the first observation", () => {
    expect(historySince([pt("2026-06-19", 1), pt("2026-06-28", 2)]))
      .toEqual({ since: "2026-06-19", count: 2 });
    expect(historySince([])).toBeNull();
  });
});
