// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { appDataFactory, snapFactory } from "./__fixtures__";
import { milestoneCrossings } from "./milestones";

const snap = snapFactory();
const appData = appDataFactory(snap);

describe("milestoneCrossings", () => {
  it("interpolates upward crossings and daggers the mortgage discontinuity", () => {
    const data = appData({
      assets: {
        snapshots: [
          snap({ snapshot_date: "2024-01-01", total: 1_550_000, mortgage_included: false }),
          snap({ snapshot_date: "2025-01-01", total: 1_950_000, mortgage_included: false }),
          snap({ snapshot_date: "2026-01-01", total: 2_150_000, mortgage_included: true }),
        ],
      },
    });
    const xs = milestoneCrossings(data);
    const levels = xs.map((x) => x.level);
    expect(levels).toContain(1_600_000);
    expect(levels).toContain(1_900_000);
    expect(levels).toContain(2_100_000);
    const m21 = xs.find((x) => x.level === 2_100_000)!;
    expect(m21.daggered).toBe(true);
    const m18 = xs.find((x) => x.level === 1_800_000)!;
    // 1.55M→1.95M over 2024: 1.8M crossed ~62% through the year
    expect(m18.date.startsWith("2024-0") || m18.date.startsWith("2024-1")).toBe(true);
    expect(m18.daggered).toBe(false);
  });
  // two snapshots with the same total on a $100K level divide nothing by nothing: no pin at an unreadable date
  it("equal totals on a level give no pin of their own, and every pin has a real date", () => {
    for (const totals of [[100_000, 100_000, 250_000], [0, 0, 120_000], [0, 0]]) {
      const data = appData({ assets: { snapshots: totals.map((total, i) => snap({ snapshot_date: `202${4 + i}-01-01`, total })) } });
      const xs = milestoneCrossings(data);
      for (const x of xs) expect(x.date, `${totals}: ${x.level}`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
    const xs = milestoneCrossings(appData({ assets: { snapshots: [100_000, 100_000, 250_000].map((total, i) =>
      snap({ snapshot_date: `202${4 + i}-01-01`, total })) } }));
    expect(xs.map((x) => [x.level, x.date])).toEqual([[100_000, "2025-01-01"], [200_000, "2025-09-01"]]);
  });
});
