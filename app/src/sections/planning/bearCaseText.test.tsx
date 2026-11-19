// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { snapFactory } from "../../lib/__fixtures__";
import { SHIPPED_RESERVED } from "../../lib/categories";

// The KPI's sub-line names the shock it applies. It reads it from SHOCK_DEFAULTS, so when the case moves the words
// move with it: the mocked table below (25% / 60%, nothing the app ships) shows up in the sentence.
vi.mock("../../lib/forwardLook", async (orig) => ({
  ...(await orig<typeof import("../../lib/forwardLook")>()),
  SHOCK_DEFAULTS: { eq: -25, crypto: -60, priv: -60, house: 0 },
}));
const snap = snapFactory()({ total: 1_000_000, by_class: { "Stocks/ETF": 500_000, Cash: 50_000 } });
vi.mock("../../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: [], cashflow: [], assets: { snapshots: [snap] }, recurring: [],
            manifest: { current_snapshot_date: null, generated_at: "2026-06-20T20:00:00-05:00" },
            assumptions: { category_renames: [], reserved: SHIPPED_RESERVED } },
    indexes: { dataRange: { first: "2024-06-01", last: "2026-06-20" }, dataThrough: "2026-06-20" },
    missing: new Set<string>(),
  }),
}));
import ForwardLookStrip from "./ForwardLookStrip";

afterEach(cleanup);

describe("the bear-case KPI's sentence", () => {
  it("names the shock from SHOCK_DEFAULTS, not from words of its own", () => {
    render(<ForwardLookStrip />);
    const text = screen.getByTestId("kpi-bear-case").textContent ?? "";
    expect(text).toContain("if stocks fall 25% and crypto/private 60%");
    expect(text).not.toContain("30%");
  });
});
