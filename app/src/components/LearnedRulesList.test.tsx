// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { VendorEntry } from "../data/types";
import LearnedRulesList from "./LearnedRulesList";

afterEach(cleanup);

const v = (vendor: string, category: string, subcategory: string | null): VendorEntry => ({
  vendor, has_rule: false, category, subcategory, category_source: "learned", learn: true,
  txns: 4, spend_ttm: 40, first_seen: "2026-01-01", last_seen: "2026-03-01",
});

describe("LearnedRulesList", () => {
  it("lists every learned vendor with its category", () => {
    render(<LearnedRulesList vendors={[v("Maple Street Bakery", "Dining", "Coffee"), v("Acme Plumbing", "Housing", null)]} />);
    const root = screen.getByTestId("learned-rules");
    expect(root.textContent).toMatch(/^2 vendors categorized from your confirmed answers/);
    expect(root.textContent).not.toMatch(/Learned rules:/);   // the card title is the heading
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Acme Plumbing → Housing", "Maple Street Bakery → Dining / Coffee"]);
  });
  it("explains the empty state instead of showing a bare zero", () => {
    render(<LearnedRulesList vendors={[]} />);
    expect(screen.getByTestId("learned-rules").textContent).toMatch(/^None yet — .*3 of its last 5/);
    expect(screen.queryByRole("list")).toBeNull();
  });
});
