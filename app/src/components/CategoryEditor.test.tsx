// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { txFactory } from "../lib/__fixtures__";

const api = vi.hoisted(() => ({ answerTxn: vi.fn(), answerTxns: vi.fn(), answerVendor: vi.fn() }));
const TAXONOMY = vi.hoisted(() => ({ Dining: ["Coffee", "Restaurants"], Kids: ["School"], Income: ["Salary - Alex", "Bonus"], Internal: [] as string[] } as Record<string, string[]>));
// vendors.json as the editor reads it: the learn flag picks the scope the editor starts on. "Check" is a generic
// descriptor and "Mixed Mart" a mixed basket (both kept out of learning); "Gamma" is not listed at all
const VENDORS = vi.hoisted(() => ["Alpha Cafe", "Beta Books", "Employer"].map((vendor) => ({ vendor, learn: true }))
  .concat([{ vendor: "Check", learn: false }, { vendor: "Mixed Mart", learn: false }]));
const world = await vi.hoisted(async () => {
  const { SHIPPED_RESERVED } = await import("../lib/categories");
  return {
    data: { assumptions: { category_renames: [] as { from: { category: string }; to: { category: string } }[], taxonomy: TAXONOMY,
      reserved: SHIPPED_RESERVED }, vendors: VENDORS },
    indexes: { tagList: ["kids", "trip"] },
  };
});
vi.mock("../data/DataContext", () => ({ useData: () => ({ ...world, answers: api }) }));
import CategoryEditor from "./CategoryEditor";

const tx = txFactory({ vendor: "Alpha Cafe", category: "Dining", subcategory: "Coffee", tags: ["a"] });
beforeEach(() => { api.answerTxn.mockReset(); api.answerTxns.mockReset(); api.answerVendor.mockReset(); });
afterEach(() => {
  cleanup();
  world.data.assumptions.category_renames = [];
  world.data.assumptions.taxonomy = TAXONOMY;
  world.data.vendors = VENDORS;
});
const addTag = (name: string) => {
  const input = screen.getByPlaceholderText("add tag…");
  fireEvent.change(input, { target: { value: name } });
  fireEvent.keyDown(input, { key: "Enter" });
};
const save = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
const clickSave = () => fireEvent.click(save());
/** pick the one-answer-per-row scope over the editor's "Always for" start */
const justThis = () => fireEvent.click(screen.getByRole("button", { name: /^Just th(is transaction|ese rows)$/ }));
/** which "Apply to" chip is on (null: the chips are not offered) */
const pressedScope = (): "txn" | "vendor" | null => {
  const always = screen.queryByRole("button", { name: /^Always for/ });
  if (!always) return null;
  return always.getAttribute("aria-pressed") === "true" ? "vendor" : "txn";
};
/** the set a call to answerTxns / answerVendor carried (third argument is the note) */
const setOf = (fn: typeof api.answerTxns, call = 0, arg = 1) => fn.mock.calls[call][arg];

describe("CategoryEditor over one row", () => {
  it("a tags-only save sends { tags } and NOTHING else: no category, no subcategory, no review flag", () => {
    const t = tx({});
    const onSaved = vi.fn();
    render(<CategoryEditor txns={[t]} onSaved={onSaved} onCancel={() => {}} />);
    justThis();
    addTag("kids");
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
    expect(api.answerTxns.mock.calls[0][0]).toEqual([t.id]);
    expect(setOf(api.answerTxns), "exactly the tags — a set carrying the row's own pair would pin it and teach the learner").toStrictEqual({ tags: ["kids"] });
    expect(api.answerTxns.mock.calls[0][2]).toBeUndefined();
    expect(onSaved).toHaveBeenCalledWith([`txn:${t.id}`]);
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
  it("the same shape for a row with a null subcategory and for a row with no category at all", () => {
    for (const row of [tx({ subcategory: null }), tx({ category: null, subcategory: null })]) {
      api.answerTxns.mockReset();
      const { unmount } = render(<CategoryEditor txns={[row]} onSaved={() => {}} onCancel={() => {}} />);
      justThis();
      addTag("kids");
      expect(save().disabled, "a tag alone can be saved for an uncategorized row").toBe(false);
      clickSave();
      expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["kids"] });
      unmount();
    }
  });
  it("a category save carries the added tags along and marks the row reviewed", () => {
    const t = tx({});
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    addTag("trip");
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Kids", subcategory: null, needs_review: false, tags: ["trip"] }, undefined);
  });
  it("a category save with no new tag carries no tags key; the note rides along trimmed", () => {
    const t = tx({});
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: "School" }));
    fireEvent.change(screen.getByPlaceholderText(/Note \(optional\)/), { target: { value: "  school fees " } });
    clickSave();
    expect(setOf(api.answerTxns)).toStrictEqual({ category: "Kids", subcategory: "School", needs_review: false });
    expect(api.answerTxns.mock.calls[0][2]).toBe("school fees");
  });
  it("saves nothing until there is something to save: no category and no new tag", () => {
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(save().disabled).toBe(true);
    clickSave();
    expect(api.answerTxns).not.toHaveBeenCalled();
  });
  it("a tag the row already has is shown as such and is not an answer (Save stays off)", () => {
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/already on this row: a/)).toBeDefined();
    addTag("a");
    expect(save().disabled).toBe(true);
    addTag("kids");
    expect(save().disabled).toBe(false);
  });
  it("tags are lowercase words here (a tag is matched exactly): 'Kids' is added as 'kids', once, comma-split", () => {
    const t = tx({});
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    addTag("  Kids ");
    addTag("kids");
    addTag("Trip,Home");
    clickSave();
    expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["home", "kids", "trip"] });
  });
  it("an added tag can be removed again before saving", () => {
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    addTag("kids");
    fireEvent.click(screen.getByRole("button", { name: "Remove tag kids" }));
    expect(save().disabled).toBe(true);
  });
  it("Cancel calls onCancel and writes nothing", () => {
    const onCancel = vi.fn();
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(api.answerTxns).not.toHaveBeenCalled();
  });
});

describe("CategoryEditor over many rows", () => {
  it("writes one answerTxns call for every selected row and names the count", () => {
    const rows = [tx({}), tx({ vendor: "Beta Books" })];
    const onSaved = vi.fn();
    render(<CategoryEditor txns={rows} onSaved={onSaved} onCancel={() => {}} />);
    expect(screen.getByText(/2 transactions/)).toBeDefined();
    expect(screen.queryByText(/Always for/)).toBeNull();                 // two vendors: no vendor scope
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
    expect(api.answerTxns.mock.calls[0][0]).toEqual(rows.map((t) => t.id));
    expect(setOf(api.answerTxns)).toStrictEqual({ category: "Kids", subcategory: null, needs_review: false });
    expect(onSaved).toHaveBeenCalledWith(rows.map((t) => `txn:${t.id}`));
  });
  it("says what the write costs: one answer per row, and what the store keeps (500 one-off answers, the oldest go first)", () => {
    render(<CategoryEditor txns={[tx({ vendor: "Alpha Cafe" }), tx({ vendor: "Beta Books" }), tx({ vendor: "Gamma" })]}
      onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/Writes 3 answers, one per row — the family store keeps up to 500 one-off answers, dropping the oldest first\./)).toBeDefined();
  });
  it("offers no vendor scope over a part of a split purchase, and says why", () => {
    const part = txFactory({ vendor: "Synth Mart", parent_id: "p1", category_source: "split" });
    render(<CategoryEditor txns={[part({}), part({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.queryByRole("button", { name: /Always for/ })).toBeNull();
    expect(screen.getByText(/takes its own answer/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerVendor).not.toHaveBeenCalled();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
  });
  it("offers no vendor scope when only SOME of the selected rows are parts of a split (an ordinary row of the same vendor beside one)", () => {
    const m = txFactory({ vendor: "Synth Mart" });
    render(<CategoryEditor txns={[m({}), m({ parent_id: "p1", category_source: "split" })]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.queryByRole("button", { name: /Always for/ })).toBeNull();
    expect(screen.getByText(/takes its own answer/)).toBeDefined();
  });
  it("offers the vendor scope when every row shares a vendor, and starts on it (one answer instead of N)", () => {
    const rows = [tx({}), tx({})];
    render(<CategoryEditor txns={rows} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/2 transactions · all Alpha Cafe/)).toBeDefined();
    expect(pressedScope()).toBe("vendor");
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    expect(screen.getByText(/Writes 1 answer for “Alpha Cafe”/)).toBeDefined();
    clickSave();
    expect(api.answerVendor).toHaveBeenCalledWith("Alpha Cafe", { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerTxns).not.toHaveBeenCalled();
  });
  it("a vendor-scope save that adds a tag puts the tag on the vendor answer", () => {
    render(<CategoryEditor txns={[tx({}), tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    addTag("kids");
    clickSave();
    expect(setOf(api.answerVendor, 0, 1)).toStrictEqual({ category: "Kids", subcategory: null, needs_review: false, tags: ["kids"] });
  });
  it("a tags-only vendor-scope save is { tags } on the vendor answer — still no category", () => {
    const onSaved = vi.fn();
    render(<CategoryEditor txns={[tx({}), tx({})]} onSaved={onSaved} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    addTag("kids");
    clickSave();
    expect(api.answerVendor.mock.calls[0][0]).toBe("Alpha Cafe");
    expect(setOf(api.answerVendor, 0, 1)).toStrictEqual({ tags: ["kids"] });
    expect(api.answerTxns).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith(["vendor:Alpha Cafe"]);
  });
  it("refuses a selection that mixes income and spending rows", () => {
    const onCancel = vi.fn();
    render(<CategoryEditor txns={[tx({}), tx({ type: "income", category: "Income" })]} onSaved={() => {}} onCancel={onCancel} />);
    expect(screen.getByText(/one kind/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByPlaceholderText("add tag…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
  it("tags-only over rows with different categories is ONE call for all of them, { tags } for each", () => {
    const rows = [tx({}), tx({ category: "Kids", subcategory: "School" }), tx({ category: null, subcategory: null })];
    render(<CategoryEditor txns={rows} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    addTag("trip");
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
    expect(api.answerTxns.mock.calls[0][0]).toEqual(rows.map((t) => t.id));
    expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["trip"] });
  });
  it("only tags every row already shares are called shared; one row's own tag is not", () => {
    render(<CategoryEditor txns={[tx({ tags: ["a", "b"] }), tx({ tags: ["a"] })]} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    expect(screen.getByText(/already on every row: a$/)).toBeDefined();
    addTag("b");                                     // one row lacks it: it is an answer for the other
    expect(save().disabled).toBe(false);
    clickSave();
    expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["b"] });
  });
  it("income rows take no vendor scope; changing the kind of the selection resets the stream (and with it any category answer)", () => {
    const income = [tx({ type: "income", category: "Income", subcategory: "Salary - Alex" })];
    const { rerender } = render(<CategoryEditor txns={income} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.queryByText(/Always for/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));
    expect(save().disabled, "a stream is picked: a category answer").toBe(false);
    // the selection changes under the open editor to spending rows: the income stream must not stay chosen for them
    rerender(<CategoryEditor txns={[tx({}), tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(save().disabled, "a spend selection starts with nothing chosen").toBe(true);
    expect(screen.queryByRole("button", { name: "Bonus" })).toBeNull();
  });
});

describe("CategoryEditor over income rows: the preselected Income type is not an answer", () => {
  const salary = () => tx({ type: "income", category: "Income", subcategory: "Salary - Alex", vendor: "Employer", tags: [] });

  it("tagging income rows sends { tags } only - never a category pair that would clear and pin the salary stream", () => {
    const rows = [salary(), salary()];
    render(<CategoryEditor txns={rows} onSaved={() => {}} onCancel={() => {}} />);
    expect(save().disabled, "nothing to save until a tag or a stream is chosen").toBe(true);
    addTag("trip");
    expect(save().disabled).toBe(false);
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
    expect(api.answerTxns.mock.calls[0][0]).toEqual(rows.map((t) => t.id));
    expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["trip"] });
  });
  it("the single-row editor (register, Review) does the same", () => {
    const t = salary();
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    addTag("trip");
    clickSave();
    expect(setOf(api.answerTxns)).toStrictEqual({ tags: ["trip"] });
  });
  it("choosing a stream sends the category pair (and any tags with it); clicking the Income type alone does not", () => {
    const t = salary();
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Income" }));         // the type chip: still no stream
    expect(save().disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));
    addTag("trip");
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Income", subcategory: "Bonus", needs_review: false, tags: ["trip"] }, undefined);
    cleanup();
    api.answerTxns.mockReset();
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));           // picked and un-picked: back to no stream
    expect(save().disabled).toBe(true);
  });
  it("says what the stream choice does - and only mentions tags where there is a Tags section", () => {
    render(<CategoryEditor txns={[salary()]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/tags alone leave the stream as it is/)).toBeDefined();
    cleanup();
    render(<CategoryEditor txns={[salary()]} allowTags={false} onSaved={() => {}} onCancel={() => {}} />);   // the Review inbox
    expect(screen.queryByText(/tags/i), "no word about tags where tags cannot be added").toBeNull();
    expect(screen.getByText(/Stream/)).toBeDefined();
    expect(screen.getByText(/\(pick one\)/)).toBeDefined();
  });
});

describe("CategoryEditor without tags (the Review inbox)", () => {
  it("shows no Tags section and saves a plain category answer - for the vendor, its start for a learnable payee", () => {
    const t = tx({});
    const onSaved = vi.fn();
    render(<CategoryEditor txns={[t]} allowTags={false} onSaved={onSaved} onCancel={() => {}} />);
    expect(screen.queryByPlaceholderText("add tag…")).toBeNull();
    expect(screen.queryByText(/^Tags/)).toBeNull();
    expect(save().disabled, "no tags to save, so nothing to save yet").toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerVendor).toHaveBeenCalledWith("Alpha Cafe", { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerTxns).not.toHaveBeenCalled();
    expect(onSaved, "Review's undo restores the vendor answer").toHaveBeenCalledWith(["vendor:Alpha Cafe"]);
  });
  it("a check (a generic descriptor kept out of learning) starts on this row alone, and says why", () => {
    const t = tx({ vendor: "Check" });
    render(<CategoryEditor txns={[t]} allowTags={false} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("txn");
    expect(screen.getByTestId("scope-not-learnable").textContent).toMatch(/keeps “Check” out\s+of learning/);
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
});

// The editor starts on "Always for <vendor>" wherever that scope is offered and vendors.json calls the vendor
// learnable (lib/vendorScope.ts), and on one answer per row everywhere else - never on a scope it does not offer.
describe("CategoryEditor starts on the vendor scope wherever it is offered", () => {
  it("one row of a learnable vendor: 'Always for' is on, and a bare category save writes the vendor answer", () => {
    const onSaved = vi.fn();
    render(<CategoryEditor txns={[tx({})]} onSaved={onSaved} onCancel={() => {}} />);
    expect(pressedScope()).toBe("vendor");
    expect(screen.queryByTestId("scope-not-learnable")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerVendor).toHaveBeenCalledWith("Alpha Cafe", { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerTxns).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith(["vendor:Alpha Cafe"]);
  });
  it("'Just this transaction' can still be picked, and then writes one answer for the row", () => {
    const t = tx({});
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    expect(pressedScope()).toBe("txn");
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
  it("an income row: no vendor scope on offer, so one answer for the row", () => {
    const t = tx({ type: "income", category: "Income", subcategory: "Salary - Alex", vendor: "Employer" });
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope(), "no Apply-to chips").toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledTimes(1);
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
  it("a part of a split purchase: no vendor scope on offer, so one answer for the part", () => {
    const t = tx({ parent_id: "p1", category_source: "split" });
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Kids", subcategory: null, needs_review: false }, undefined);
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
  it("a selection of two vendors: no vendor scope on offer, so one answer per row", () => {
    const rows = [tx({}), tx({ vendor: "Beta Books" })];
    render(<CategoryEditor txns={rows} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns.mock.calls[0][0]).toEqual(rows.map((t) => t.id));
    expect(api.answerVendor).not.toHaveBeenCalled();
  });
  it("a row with no vendor: no vendor scope on offer, so one answer for the row", () => {
    const t = tx({ vendor: null });
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Kids", subcategory: null, needs_review: false }, undefined);
  });
  it("a mixed-basket vendor kept out of learning starts on these rows alone, and 'Always' is still one click away", () => {
    const rows = [tx({ vendor: "Mixed Mart" }), tx({ vendor: "Mixed Mart" })];
    render(<CategoryEditor txns={rows} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("txn");
    expect(screen.getByTestId("scope-not-learnable").textContent).toMatch(/^Starts on “Just these rows”/);
    fireEvent.click(screen.getByRole("button", { name: /^Always for/ }));
    fireEvent.click(screen.getByRole("button", { name: "Kids" }));
    clickSave();
    expect(api.answerVendor).toHaveBeenCalledWith("Mixed Mart", { category: "Kids", subcategory: null, needs_review: false }, undefined);
  });
  it("a vendor vendors.json does not list (or a missing vendors.json) starts on the row, with no 'kept out' claim", () => {
    render(<CategoryEditor txns={[tx({ vendor: "Gamma" })]} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("txn");
    expect(screen.queryByTestId("scope-not-learnable")).toBeNull();
    cleanup();
    world.data.vendors = [];
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("txn");
    expect(screen.queryByTestId("scope-not-learnable")).toBeNull();
  });
  it("a choice is for its vendor: a selection that moves to another vendor starts again, and never carries 'Always' onto a check", () => {
    const seen = vi.fn();
    const alpha = [tx({}), tx({})];
    const { rerender } = render(<CategoryEditor txns={alpha} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("vendor");
    // the selection becomes two checks under the open editor: the start for a check is the rows alone
    rerender(<CategoryEditor txns={[tx({ vendor: "Check" }), tx({ vendor: "Check" })]} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("txn");
    expect(seen).toHaveBeenLastCalledWith(null);
    // "Just these rows" picked for Alpha Cafe holds for Alpha Cafe; another learnable vendor starts on its own default
    rerender(<CategoryEditor txns={alpha} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    justThis();
    expect(pressedScope()).toBe("txn");
    rerender(<CategoryEditor txns={[tx({ vendor: "Beta Books" })]} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope()).toBe("vendor");
    expect(seen).toHaveBeenLastCalledWith("Beta Books");
    rerender(<CategoryEditor txns={alpha} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(pressedScope(), "the choice made for Alpha Cafe").toBe("txn");
  });
});

describe("CategoryEditor keeps the editor's other guards", () => {
  it("an empty selection offers nothing to save", () => {
    render(<CategoryEditor txns={[]} onSaved={() => {}} onCancel={() => {}} />);
    expect(save().disabled).toBe(true);
    clickSave();
    expect(api.answerTxns).not.toHaveBeenCalled();
  });
});

describe("CategoryEditor reports the vendor whose single answer a save would write", () => {
  it("the vendor while 'Always for' is chosen (its start), null for one answer per row, and null once the editor is gone", () => {
    const seen = vi.fn();
    const { unmount } = render(<CategoryEditor txns={[tx({}), tx({})]} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(seen, "the bulk bar counts one answer from the start").toHaveBeenLastCalledWith("Alpha Cafe");
    expect(seen.mock.calls.some(([v]) => v === null), "never reported as one answer per row first").toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Just these rows" }));
    expect(seen).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    expect(seen).toHaveBeenLastCalledWith("Alpha Cafe");
    fireEvent.click(screen.getByRole("button", { name: "Just these rows" }));
    expect(seen).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: /Always for/ }));
    unmount();
    expect(seen).toHaveBeenLastCalledWith(null);
  });
  it("is null for rows that share no vendor and for income rows (there is no vendor scope to choose)", () => {
    const seen = vi.fn();
    render(<CategoryEditor txns={[tx({}), tx({ vendor: "Beta Books" })]} onVendorScope={seen} onSaved={() => {}} onCancel={() => {}} />);
    expect(seen.mock.calls.every(([v]) => v === null)).toBe(true);
  });
});

// An answer cannot retype a row, so the type-driven buckets (Internal, Income) are never offered for a spend row and an
// income row is offered only its own. A rename changes the names in the taxonomy; the editor must still know them.
describe("CategoryEditor follows a rename of the type-driven categories", () => {
  const renamed = () => {
    world.data.assumptions.category_renames = [
      { from: { category: "Income" }, to: { category: "Earnings" } }, { from: { category: "Internal" }, to: { category: "Transfers" } }];
    world.data.assumptions.taxonomy = { Dining: ["Coffee", "Restaurants"], Earnings: ["Salary - Alex", "Bonus"], Transfers: [] };
  };
  it("a spend row is offered neither renamed bucket", () => {
    renamed();
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Dining" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Earnings" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Transfers" })).toBeNull();
  });
  it("an income row is offered its renamed type and answers with it", () => {
    renamed();
    const t = tx({ type: "income", category: "Earnings", subcategory: "Salary - Alex", vendor: "Employer", tags: [] });
    render(<CategoryEditor txns={[t]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.queryByRole("button", { name: "Dining" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bonus" }));
    clickSave();
    expect(api.answerTxns).toHaveBeenCalledWith([t.id], { category: "Earnings", subcategory: "Bonus", needs_review: false }, undefined);
  });
  it("with no rename known the renamed buckets are ordinary categories (control)", () => {
    world.data.assumptions.taxonomy = { Dining: ["Coffee"], Earnings: ["Bonus"], Transfers: [] };
    render(<CategoryEditor txns={[tx({})]} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Earnings" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Transfers" })).toBeDefined();
  });
});
