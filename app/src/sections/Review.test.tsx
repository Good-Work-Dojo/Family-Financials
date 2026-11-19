// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CODE_PILL } from "../components/NoData";
import { PHONE_WRAP, TAP_AREA } from "../components/ui";
import type { CategoryRename } from "../data/types";
import { txFactory, vocabAssumptions } from "../lib/__fixtures__";
import { categoryVocab, SHIPPED_RESERVED } from "../lib/categories";
import { MASK, setPrivate } from "../lib/privacy";

const tx = txFactory({ type: "purchase", needs_review: true, category: "Cash & Misc", subcategory: "Uncategorized" });
const rows = [
  tx({ vendor: "Learned Vendor", category_source: "learned", amount: -30 }),
  tx({ vendor: "Plain Vendor", category_source: "keyword", amount: -10 }),
];
const unanswer = vi.hoisted(() => vi.fn());
const stored = vi.hoisted(() => ({ answers: [] as unknown[], conflicted: new Set<string>(), renames: [] as CategoryRename[],
  localOnly: false, refused: null as number | null, pending: new Set<string>() }));
vi.mock("../data/DataContext", () => ({
  useData: () => ({
    data: { transactions: rows, assumptions: { category_renames: stored.renames, reserved: SHIPPED_RESERVED } },
    answers: { doc: { answers: stored.answers }, localOnly: stored.localOnly, refused: stored.refused, unanswer },
    overlay: { conflictedKeys: stored.conflicted, pendingIds: stored.pending },
  }),
}));
// the editor stand-in shows whether the inbox asked for tags, and "saves" the keys a real save would report
const saved = vi.hoisted(() => ({ keys: ["txn:abc", "vendor:x"] }));
vi.mock("../components/CategoryEditor", () => ({
  default: (p: { allowTags?: boolean; onSaved: (keys: string[]) => void }) => (
    <div>editor<output data-testid="allow-tags">{String(p.allowTags)}</output>
      <button onClick={() => p.onSaved(saved.keys)}>mock-save</button></div>),
}));
import Review, { groupOf, UNDO_MS } from "./Review";

afterEach(() => { cleanup(); setPrivate(false); unanswer.mockReset(); saved.keys = ["txn:abc", "vendor:x"]; stored.answers = []; stored.conflicted = new Set(); stored.renames = []; stored.localOnly = false; stored.refused = null; stored.pending = new Set(); });

describe("Review's saved changes", () => {
  it("say they are permanent at the next ingest, and state no schedule of their own", () => {
    stored.pending = new Set(["t1", "t2"]);
    render(<Review />);
    expect(screen.getByText(/changes saved/).textContent?.replace(/\s+/g, " ").trim())
      .toBe("2 changes saved — permanent at the next ingest.");
  });
});

describe("Review when the family store refuses a save", () => {
  // vendor-scope answers are never evicted, so the store's body cap is their only bound: a save past it is a 413
  it("a save refused as too large says so, where the answers are, and who should look — never 'for now'", () => {
    stored.localOnly = true;
    stored.refused = 413;
    render(<Review />);
    expect(screen.getByText("The family store refused the last save as too large, which should not happen. These answers are saved in "
      + "this browser only and won't reach the next ingest: tell whoever maintains the dashboard.")).toBeDefined();
    expect(screen.queryByText(/for now/)).toBeNull();
  });
  it("an unreachable store is retried by itself, so that notice stays 'for now'", () => {
    stored.localOnly = true;
    render(<Review />);
    expect(screen.getByText("Store unreachable — answers are saved in this browser only for now.")).toBeDefined();
  });
});

describe("Review inbox rows", () => {
  it("a row's chip is not a second tab stop inside the row button, but still tells its tooltip", () => {
    render(<Review />);
    const row = screen.getByRole("button", { name: /Learned Vendor/ });
    expect(row.tagName).toBe("BUTTON");
    // a focusable span inside a button is invalid HTML and a dead tab stop
    expect(row.querySelectorAll("[tabindex]")).toHaveLength(0);
    expect(within(row).getByText("learned")).toBeDefined();
    expect(row.textContent).toContain(": learned from your answers");   // the sr-only tooltip text
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
  });

  it("the filter pills keep their size and take a 44px hit area on a phone, their rows 8px apart so none overlap", () => {
    render(<Review />);
    const all = screen.getByRole("button", { name: /^All/ });
    const cls = all.className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(TAP_AREA.split(" ")));
    expect(cls, "the pill itself is not 44px tall").not.toContain("min-h-11");
    expect(TAP_AREA).toContain("max-md:before:h-11");
    expect((all.parentElement as HTMLElement).className.split(/\s+/)).toContain("max-md:gap-y-2");
  });

  it("a row's two lines are one line each from md up and wrap at a word on a phone, never cut inside a name", () => {
    render(<Review />);
    const vendor = screen.getAllByTestId("review-row-vendor")[0];
    for (const line of [vendor, vendor.nextElementSibling as HTMLElement]) {
      expect(line.className.split(/\s+/)).toEqual(expect.arrayContaining(PHONE_WRAP.split(" ")));
    }
    expect(PHONE_WRAP.split(" ")).toEqual(["truncate", "max-md:whitespace-normal", "max-md:break-words"]);
  });

  it("no row button anywhere in the inbox contains a focusable descendant", () => {
    const { container } = render(<Review />);
    const inbox = container.querySelector('[data-testid="card-review-inbox"]')!;
    for (const b of inbox.querySelectorAll("button")) {
      expect(b.querySelectorAll("[tabindex], a[href], input, select, textarea, button")).toHaveLength(0);
    }
  });
});

describe("Review conflicts", () => {
  it("a row whose vendor answer an override beat is a conflict even when a tags-only txn answer sits on it", () => {
    const row = tx({ id: "conf-1", vendor: "Synthetic Conflicted", amount: -12 });
    rows.push(row);
    try {
      stored.answers = [
        { key: "txn:conf-1", scope: "txn", txn_id: "conf-1", set: { tags: ["kids"] }, answered_at: "2026-01-02T00:00:00.000Z" },
        { key: "vendor:Synthetic Conflicted", scope: "vendor", vendor: "Synthetic Conflicted",
          set: { category: "Dining", subcategory: null }, answered_at: "2026-01-01T00:00:00.000Z" },
      ];
      stored.conflicted = new Set(["vendor:Synthetic Conflicted"]);     // the overlay's owner: the category-carrying answer
      render(<Review />);
      expect(within(screen.getByTestId("card-review-conflicts")).getByText(/Synthetic Conflicted/)).toBeDefined();
      expect(screen.queryByRole("button", { name: /Synthetic Conflicted/ }), "not back in the open inbox").toBeNull();
      // the sentence says it in words; the file is a hint after it, in the shared code pill (which reads in the light
      // theme too)
      const card = screen.getByTestId("card-review-conflicts");
      expect(card.querySelector("p")?.textContent).toBe("These were answered in the app, but an override in the household's "
        + "configuration outranked the answer at the last ingest. Remove that override if the answer should win (overrides.yaml).");
      const code = card.querySelector("code");
      expect([code?.textContent, code?.className]).toEqual(["overrides.yaml", CODE_PILL]);
    } finally { rows.pop(); }
  });
});

describe("Review inbox editor", () => {
  it("is a question about categories: the editor is asked for no Tags section (tags belong to the register)", () => {
    render(<Review />);
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    expect(screen.getByTestId("allow-tags").textContent).toBe("false");
  });

  it("Undo goes through unanswer with the FIRST saved key (the store restores what that save replaced)", () => {
    render(<Review />);
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));
    expect(screen.getByText(/Saved “Plain Vendor”/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    expect(unanswer).toHaveBeenCalledTimes(1);
    expect(unanswer).toHaveBeenCalledWith("txn:abc");
    expect(screen.queryByText(/Saved “Plain Vendor”/)).toBeNull();
  });
});

// The editor starts on "Always for <vendor>" wherever it can: the toast says when a save answered the vendor's every row
describe("Review's Saved toast after a vendor answer", () => {
  it("says it covers all the vendor's rows, and Undo takes back the vendor answer", () => {
    saved.keys = ["vendor:Plain Vendor"];
    render(<Review />);
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));
    expect(screen.getByRole("status").textContent).toContain("Saved “Plain Vendor” for all its rows");
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    expect(unanswer).toHaveBeenCalledWith("vendor:Plain Vendor");
  });
  it("a one-row answer says nothing of the vendor's other rows", () => {
    render(<Review />);
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));
    expect(screen.getByRole("status").textContent).not.toContain("for all its rows");
  });
});

// A screen reader heard nothing after a save: the toast was inserted whole (live regions announce reliably only when they were already
// in the page) and had no role.
describe("Review's Saved toast is announced", () => {
  it("its frame is a status region that is in the page before any save, empty; the toast then appears inside it", () => {
    render(<Review />);
    const status = screen.getByRole("status");
    expect(status.textContent, "empty until a save").toBe("");
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));
    expect(screen.getByRole("status"), "the same region, not a new one").toBe(status);
    expect(status.textContent).toContain("Saved “Plain Vendor”");
    expect(within(status).getByRole("button", { name: /Undo/ })).toBeDefined();
    fireEvent.click(within(status).getByRole("button", { name: /Undo/ }));
    expect(status.textContent).toBe("");
  });
});

// A save starts the toast's timer and the page stops it when it unmounts, so no timer of this file fires after the test
// environment is torn down: every other test here that saves ends with cleanup()'s unmount.
describe("the Saved toast's timer", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });
  const save = () => {
    fireEvent.click(screen.getByRole("button", { name: /Plain Vendor/ }));
    fireEvent.click(screen.getByRole("button", { name: "mock-save" }));
  };
  it("ends the toast once its time is up, and not before", () => {
    render(<Review />);
    save();
    act(() => { vi.advanceTimersByTime(UNDO_MS - 1); });
    expect(screen.getByRole("status").textContent).toContain("Saved “Plain Vendor”");
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole("status").textContent).toBe("");
    expect(vi.getTimerCount(), "no timer is left").toBe(0);
  });
  it("is stopped by an unmount and by Undo, so none is left to fire after the page is gone", () => {
    const page = render(<Review />);
    save();
    expect(vi.getTimerCount(), "a save starts it").toBe(1);
    page.unmount();
    expect(vi.getTimerCount(), "the unmount stops it").toBe(0);
    render(<Review />);
    save();
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    expect(vi.getTimerCount(), "Undo stops it").toBe(0);
  });
  it("a second save restarts it: one timer, and the toast stays its full time after the second", () => {
    render(<Review />);
    save();
    act(() => { vi.advanceTimersByTime(UNDO_MS - 1000); });
    save();
    expect(vi.getTimerCount()).toBe(1);
    act(() => { vi.advanceTimersByTime(UNDO_MS - 1); });
    expect(screen.getByRole("status").textContent).toContain("Saved “Plain Vendor”");
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole("status").textContent).toBe("");
  });
});

// 250 rows made one very long list (and a narrow column at 1280); the inbox shows 50 at a time, biggest first.
describe("Review shows the inbox 50 rows at a time", () => {
  const many = Array.from({ length: 120 }, (_, i) => tx({ id: `many-${i}`, vendor: `Vendor ${String(i).padStart(3, "0")}`, amount: -(1000 - i) }));
  const main = [...rows];
  const rowButtons = () => screen.getAllByRole("button").filter((b) => b.hasAttribute("aria-expanded"));
  const useMany = () => { rows.length = 0; rows.push(...many); };
  afterEach(() => { rows.length = 0; rows.push(...main); });

  it("draws the first 50 (the biggest), and 'show 50 more' adds the next 50 and then the last 20", () => {
    useMany();
    render(<Review />);
    expect(rowButtons()).toHaveLength(50);
    expect(rowButtons()[0].textContent).toContain("Vendor 000");
    expect(rowButtons()[49].textContent).toContain("Vendor 049");
    expect(screen.getByRole("button", { name: /^show 50 more/ }).textContent).toContain("70 left");
    fireEvent.click(screen.getByTestId("review-more"));
    expect(rowButtons()).toHaveLength(100);
    expect(screen.getByTestId("review-more").textContent).toContain("show 20 more");
    fireEvent.click(screen.getByTestId("review-more"));
    expect(rowButtons()).toHaveLength(120);
    expect(screen.queryByTestId("review-more"), "all shown: no button").toBeNull();
  });

  it("the header and the pills still count the whole inbox, and switching group starts that group at its first page", () => {
    useMany();
    render(<Review />);
    expect(screen.getByText("120 transactions could use a category — biggest first.")).toBeDefined();
    fireEvent.click(screen.getByTestId("review-more"));
    expect(rowButtons()).toHaveLength(100);
    fireEvent.click(screen.getByRole("button", { name: /^Other/ }));
    expect(rowButtons(), "Other holds all 120; a fresh page").toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(rowButtons(), "back on All: also a fresh page").toHaveLength(50);
  });

  it("a small inbox has no button", () => {
    render(<Review />);
    expect(screen.queryByTestId("review-more")).toBeNull();
  });
});

// a Venmo row's descriptor is "Venmo payment \u2014 <memo>", and the memo is the payer's own free text: it can state an
// amount in words ("split of $40.25 dinner") that no formatter ever saw
describe("Review inbox under privacy mode", () => {
  const memo = (id: string, over: Parameters<typeof tx>[0]) =>
    tx({ id, account_name: "Venmo", description: "Venmo payment \u2014 split of $40.25 dinner, net 1,234.56", ...over });
  const withRows = (extra: ReturnType<typeof tx>[], run: () => void) => {
    rows.push(...extra);
    try { run(); } finally { rows.splice(rows.length - extra.length, extra.length); }
  };
  it("masks the amounts in the descriptor subline and in a vendor-less row's title, and keeps the memo's words", () => {
    withRows([memo("m1", { vendor: "Amy Synthetic", amount: -61 }), memo("m2", { vendor: null, amount: -62 })], () => {
      setPrivate(true);
      render(<Review />);
      const text = screen.getByTestId("card-review-inbox").textContent ?? "";
      expect(text).toContain(`split of ${MASK} dinner, net ${MASK}`);
      expect(/40\.25|1,234\.56|\$\d/.test(text), "no amount from the memo").toBe(false);
      expect(text).toContain("Amy Synthetic");
    });
  });
  it("shows the memo as written with privacy off", () => {
    withRows([memo("m1", { vendor: "Amy Synthetic", amount: -61 })], () => {
      render(<Review />);
      expect(screen.getByTestId("card-review-inbox").textContent).toContain("split of $40.25 dinner, net 1,234.56");
    });
  });
});

// The pills used to overlap (a Venmo deposit was in "Venmo & Zelle" AND "Deposits") and leave gaps (an uncategorized debit was in
// none: 208 + 2 + 31 = 241 of "All 250"). They are a partition now - Venmo & Zelle, Cash & checks, Deposits, Other.
describe("Review buckets partition the inbox", () => {
  const sub = (subcategory: string | null, amount: number, over: Parameters<typeof tx>[0] = {}) =>
    tx({ subcategory, amount, needs_review: true, vendor: `row-${subcategory ?? "none"}-${amount}`, ...over });
  // every overlap and gap the old predicates had, in one set
  const SET = [
    sub("Venmo/Zelle Unknown", -5),            // a Venmo debit
    sub("Venmo/Zelle Unknown", 7),             // a Venmo DEPOSIT: old Venmo AND old Deposits
    sub("ATM", -20),                           // cash
    sub("Checks", 100),                        // a check deposit: old Cash AND old Deposits
    sub("Wire", 250, { type: "income" }),      // a wire in: old Cash AND old Deposits
    sub("Uncategorized", 50, { type: "income" }),   // a plain deposit
    sub("Uncategorized", -3),                  // an uncategorized debit: in NO old bucket
    sub("Grocery Run", -12),                   // flagged for review under some other subcategory: in NO old bucket
  ];
  const withSet = (run: () => void) => {
    const keep = rows.length;
    rows.push(...SET);
    try { run(); } finally { rows.splice(keep); }
  };
  const pill = (label: string) => screen.getAllByRole("button").find((b) => b.textContent?.startsWith(label))!;
  const count = (label: string) => Number(pill(label).querySelector("span")?.textContent);

  it("groupOf puts every row in exactly one group, first match winning in the pills' order", () => {
    expect(SET.map((t) => groupOf(categoryVocab(vocabAssumptions()), t))).toEqual(["venmo", "venmo", "cash", "cash", "cash", "deposits", "other", "other"]);
    expect(groupOf(categoryVocab(vocabAssumptions()), sub(null, -1))).toBe("other");
  });
  it("the pills' counts sum to All on a set with every old overlap and gap", () => {
    withSet(() => {
      render(<Review />);
      const all = count("All");
      expect(all).toBe(rows.length);                                            // the two base rows + the eight above
      const parts = ["Venmo & Zelle", "Cash & checks", "Deposits", "Other"].map(count);
      expect(parts.reduce((a, b) => a + b, 0), `${parts.join(" + ")} of ${all}`).toBe(all);
      expect(parts).toEqual([2, 3, 1, 4]);                                      // base rows are debits: Other
    });
  });
  it("each pill lists exactly its own rows, and no row appears under two pills", () => {
    withSet(() => {
      render(<Review />);
      const seen = new Map<string, string>();
      for (const label of ["Venmo & Zelle", "Cash & checks", "Deposits", "Other"]) {
        fireEvent.click(pill(label));
        const names = [...screen.getByTestId("card-review-inbox").querySelectorAll("button[aria-expanded]")]
          .map((b) => b.textContent ?? "");
        expect(names.length, label).toBe(count(label));
        for (const n of names) { expect(seen.has(n), `${n} under ${seen.get(n)} and ${label}`).toBe(false); seen.set(n, label); }
      }
      expect(seen.size).toBe(rows.length);
    });
  });
  it("a group nobody is in draws no pill (an empty Other never shows)", () => {
    render(<Review />);                                                         // the two base rows are both debits: Other only
    expect(screen.queryByRole("button", { name: /^Deposits/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Venmo/ })).toBeNull();
    expect(pill("Other")).toBeDefined();
  });
});

// The inbox lists the uncategorized pile by its category name. A rename of Cash & Misc changes the name every pile row
// carries; the inbox, its header count and its Venmo group must follow it, or the family's open questions vanish.
describe("the Review inbox follows a rename of the Cash & Misc pile", () => {
  const pile = (subcategory: string, over: Parameters<typeof tx>[0] = {}) =>
    tx({ category: "Misc", subcategory, needs_review: false, vendor: `pile-${subcategory}`, amount: -20, ...over });
  const withRows = (extra: ReturnType<typeof tx>[], run: () => void) => {
    const keep = rows.length;
    rows.push(...extra);
    try { run(); } finally { rows.splice(keep); }
  };
  const header = () => screen.getByRole("heading", { name: "Review" }).parentElement!.textContent ?? "";
  it("lists the unflagged renamed-pile rows, groups the unknown Venmo rows, and an unrelated Misc row is not one", () => {
    stored.renames = [{ from: { category: "Cash & Misc" }, to: { category: "Misc" } }];
    withRows([pile("Uncategorized"), pile("Venmo/Zelle Unknown", { amount: -9 }), pile("Legal", { vendor: "Law Office" })], () => {
      render(<Review />);
      expect(header()).toContain("4 transactions could use a category");                    // the two base rows + the two pile rows
      expect(screen.queryByText("Law Office")).toBeNull();
      expect(screen.getByRole("button", { name: /^Venmo & Zelle/ }).textContent).toContain("1");
    });
  });
  it("a renamed subcategory keeps its group: the unknown-Venmo pill and the cash pill follow the pair renames", () => {
    stored.renames = [
      { from: { category: "Cash & Misc", subcategory: "Venmo/Zelle Unknown" }, to: { category: "Cash & Misc", subcategory: "Unknown Payee" } },
      { from: { category: "Cash & Misc", subcategory: "Checks" }, to: { category: "Cash & Misc", subcategory: "Paper Checks" } }];
    const flagged = (subcategory: string) => tx({ category: "Cash & Misc", subcategory, needs_review: true, vendor: `row-${subcategory}`, amount: -30 });
    withRows([flagged("Unknown Payee"), flagged("Paper Checks")], () => {
      render(<Review />);
      expect(screen.getByRole("button", { name: /^Venmo & Zelle/ }).textContent).toContain("1");
      expect(screen.getByRole("button", { name: /^Cash & checks/ }).textContent).toContain("1");
    });
  });
  it("with the rename unknown to the page the same rows are not in the pile (control)", () => {
    withRows([pile("Uncategorized")], () => {
      render(<Review />);
      expect(header()).toContain("2 transactions could use a category");
    });
  });
});
