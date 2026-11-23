// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { CategoryRename, ReservedNames } from "../data/types";
import * as categories from "./categories";
import {
  categoryVocab as vocabOf, NO_CATEGORY, SHIPPED_RESERVED, SHIPPED_TAXONOMY,
} from "./categories";

/** the vocabulary of a data set that keeps the shipped reserved names, read through `renames` */
const categoryVocab = (renames: CategoryRename[]) => vocabOf({ category_renames: renames, reserved: SHIPPED_RESERVED });
const rename = (from: string, to: string): CategoryRename => ({ from: { category: from }, to: { category: to } });
const pairRename = (fromCat: string, fromSub: string, toCat: string, toSub: string): CategoryRename =>
  ({ from: { category: fromCat, subcategory: fromSub }, to: { category: toCat, subcategory: toSub } });

describe("the vocabulary with nothing renamed is the list the app has always used", () => {
  const v = categoryVocab([]);
  it("essentials: eight whole categories and seven pairs", () => {
    expect(v.essentialLabels()).toEqual({
      categories: ["Utilities", "Groceries", "Health", "Insurance", "Transport", "Pets", "Debt", "Fees & Interest"],
      pairs: ["Housing / Mortgage", "Housing / HOA", "Housing / Home Insurance", "Kids / Childcare", "Kids / School",
        "Kids / Supplies", "Kids / Healthcare - Kids"],
    });
    expect(v.isEssentialDefault("Transport", "Fuel")).toBe(true);
    expect(v.isEssentialDefault("Housing", "Mortgage")).toBe(true);
    expect(v.isEssentialDefault("Housing", "Home Improvement")).toBe(false);
    expect(v.isEssentialDefault("Dining", "Restaurants")).toBe(false);
  });
  it("the dead Housing / Rent/HOA pair is gone (Housing has no such subcategory)", () => {
    expect(v.isEssentialDefault("Housing", "Rent/HOA")).toBe(false);
  });
  it("money movement is the checks, wires and ATM subcategories, and only those", () => {
    for (const s of ["Checks", "Wire", "ATM"]) expect(v.isMoneyMovementSub(s), s).toBe(true);
    for (const s of ["Legal", "Uncategorized", "Venmo/Zelle Unknown", null]) expect(v.isMoneyMovementSub(s), String(s)).toBe(false);
    expect(v.moneyMovementLabels(), "their names for prose").toEqual(["Checks", "Wire", "ATM"]);
  });
  it("the uncategorized pile is Cash & Misc with Uncategorized or Venmo/Zelle Unknown", () => {
    expect(v.isUncategorizedPile({ category: "Cash & Misc", subcategory: "Uncategorized" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Cash & Misc", subcategory: "Venmo/Zelle Unknown" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Cash & Misc", subcategory: "Legal" })).toBe(false);
    expect(v.isUncategorizedPile({ category: "Dining", subcategory: "Uncategorized" })).toBe(false);
    expect(v.isUncategorizedPile({ category: null, subcategory: null })).toBe(false);
    expect(v.cashMisc).toBe("Cash & Misc");
  });
  it("the single names", () => {
    expect(v.isInternal("Internal")).toBe(true);
    expect(v.isTypeDriven("Income")).toBe(true);
    expect(v.isTypeDriven("Internal")).toBe(true);
    expect(v.isTypeDriven("Dining")).toBe(false);
    expect(v.income).toBe("Income");
    expect(v.isSubscriptionLike("Entertainment")).toBe(true);
    expect(v.isSubscriptionLike("Shopping")).toBe(false);
    expect(v.name("Transport")).toBe("Transport");
  });
});

describe("a configured rename moves the app's own names with it", () => {
  it("Transport -> Transportation: the category is still essential, in the list and in the rule", () => {
    const v = categoryVocab([rename("Transport", "Transportation")]);
    expect(v.isEssentialDefault("Transportation", "Fuel")).toBe(true);
    expect(v.essentialLabels().categories).toContain("Transportation");
    expect(v.essentialLabels().categories).not.toContain("Transport");
    expect(v.name("Transport")).toBe("Transportation");
    expect(v.isEssentialDefault("Utilities", "Water/Trash")).toBe(true);    // the others are untouched
  });
  it("a row still carrying the shipped spelling is the same category (the ETL's own fallback is not renamed)", () => {
    const v = categoryVocab([rename("Transport", "Transportation"), rename("Cash & Misc", "Misc")]);
    expect(v.isEssentialDefault("Transport", "Fuel")).toBe(true);
    expect(v.isUncategorizedPile({ category: "Cash & Misc", subcategory: "Uncategorized" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Uncategorized" })).toBe(true);
  });
  it("Cash & Misc -> Misc: the Review pile and its name follow", () => {
    const v = categoryVocab([rename("Cash & Misc", "Misc")]);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Uncategorized" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Venmo/Zelle Unknown" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Legal" })).toBe(false);
    expect(v.cashMisc).toBe("Misc");
  });
  it("a pair rename moves exactly that pair: Housing / Mortgage -> Housing / Home Loan", () => {
    const v = categoryVocab([pairRename("Housing", "Mortgage", "Housing", "Home Loan")]);
    expect(v.isEssentialDefault("Housing", "Home Loan")).toBe(true);
    expect(v.isEssentialDefault("Housing", "HOA")).toBe(true);
    expect(v.isEssentialDefault("Housing", "Home Improvement")).toBe(false);
    expect(v.essentialLabels().pairs).toContain("Housing / Home Loan");
    expect(v.essentialLabels().pairs).not.toContain("Housing / Mortgage");
  });
  it("a whole-category rename carries the pair too: Kids -> Family", () => {
    const v = categoryVocab([rename("Kids", "Family")]);
    expect(v.isEssentialDefault("Family", "Childcare")).toBe(true);
    expect(v.isEssentialDefault("Family", "Activities")).toBe(false);
    expect(v.essentialLabels().pairs).toContain("Family / School");
  });
  it("a renamed subcategory is still a money movement, whatever its category", () => {
    const v = categoryVocab([pairRename("Cash & Misc", "ATM", "Cash & Misc", "Cash Withdrawal")]);
    expect(v.isMoneyMovementSub("Cash Withdrawal")).toBe(true);
    expect(v.isMoneyMovementSub("ATM")).toBe(true);
    expect(v.isMoneyMovementSub("Wire")).toBe(true);
    expect(v.moneyMovementLabels(), "named as the household calls it").toEqual(["Checks", "Wire", "Cash Withdrawal"]);
  });
  it("Internal and Income, Subscriptions and Fees & Interest follow too", () => {
    const v = categoryVocab([rename("Internal", "Transfers"), rename("Income", "Earnings"),
      rename("Subscriptions", "Memberships"), rename("Fees & Interest", "Fees")]);
    expect(v.isInternal("Transfers")).toBe(true);
    expect(v.isTypeDriven("Earnings")).toBe(true);
    expect(v.income).toBe("Earnings");
    expect(v.isSubscriptionLike("Memberships")).toBe(true);
  });
  it("a list gives one object, so it is a stable memo dependency", () => {
    const list = [rename("Transport", "Transportation")];
    expect(categoryVocab(list)).toBe(categoryVocab(list));
    expect(categoryVocab([])).not.toBe(categoryVocab(list));
  });
});

// A rename onto a category the taxonomy already holds MERGES the two: the target keeps its own class, so a merge can
// never turn the target's spend essential, a bill, a forgotten subscription or part of the pile.
describe("a rename that merges onto an existing category leaves the target's class alone", () => {
  it("Pets -> Shopping: Shopping stays non-essential, and Pets still reads as Pets", () => {
    const v = categoryVocab([rename("Pets", "Shopping")]);
    expect(v.isEssentialDefault("Shopping", "Clothing")).toBe(false);
    expect(v.isEssentialDefault("Shopping", "")).toBe(false);
    expect(v.isEssentialDefault("Pets", "")).toBe(true);
    expect(v.essentialLabels().categories).toContain("Pets");
    expect(v.essentialLabels().categories).not.toContain("Shopping");
    expect(v.name("Pets")).toBe("Shopping");                                  // the current name for a link is still the target
  });
  it("Groceries -> Dining: Dining stays non-essential", () => {
    const v = categoryVocab([rename("Groceries", "Dining")]);
    expect(v.isEssentialDefault("Dining", "Restaurants")).toBe(false);
    expect(v.isEssentialDefault("Groceries", "")).toBe(true);
  });
  it("Entertainment -> Dining: Dining is no forgotten subscription", () => {
    const v = categoryVocab([rename("Entertainment", "Dining")]);
    expect(v.isSubscriptionLike("Dining")).toBe(false);
    expect(v.isSubscriptionLike("Entertainment")).toBe(true);
  });
  it("Cash & Misc -> Travel: Travel is not the pile, and Cash & Misc still is", () => {
    const v = categoryVocab([rename("Cash & Misc", "Travel")]);
    expect(v.isUncategorizedPile({ category: "Travel", subcategory: "Uncategorized" })).toBe(false);
    expect(v.isUncategorizedPile({ category: "Cash & Misc", subcategory: "Uncategorized" })).toBe(true);
  });
  it("Internal -> Income: the transfer bucket keeps its own member", () => {
    const v = categoryVocab([rename("Internal", "Income")]);
    expect(v.isInternal("Income")).toBe(false);
    expect(v.isInternal("Internal")).toBe(true);
  });
  it("a pair renamed onto a seeded sibling, or onto another shipped category, merges nothing either", () => {
    const sibling = categoryVocab([pairRename("Housing", "Mortgage", "Housing", "Home Improvement")]);
    expect(sibling.isEssentialDefault("Housing", "Home Improvement")).toBe(false);
    expect(sibling.isEssentialDefault("Housing", "Mortgage")).toBe(true);
    const moved = categoryVocab([pairRename("Housing", "Mortgage", "Travel", "Mortgage")]);
    expect(moved.isEssentialDefault("Travel", "Mortgage")).toBe(false);
    const movedSub = categoryVocab([pairRename("Cash & Misc", "ATM", "Travel", "Cash Out")]);       // the sub lands in a shipped category
    expect(movedSub.isMoneyMovementSub("Cash Out")).toBe(false);
    expect(movedSub.isMoneyMovementSub("ATM")).toBe(true);
    const sub = categoryVocab([pairRename("Cash & Misc", "ATM", "Cash & Misc", "Legal")]);
    expect(sub.isMoneyMovementSub("Legal")).toBe(false);
    expect(sub.isMoneyMovementSub("ATM")).toBe(true);
  });
  it("a rename that is not a merge still carries its class (the control)", () => {
    expect(categoryVocab([rename("Pets", "Companions")]).isEssentialDefault("Companions", "")).toBe(true);
    expect(categoryVocab([rename("Entertainment", "Fun")]).isSubscriptionLike("Fun")).toBe(true);
  });
});

describe("the sub-level names: margin interest, passive income", () => {
  it("are what the app has always read", () => {
    const v = categoryVocab([]);
    expect(v.isMarginInterest("Margin Interest")).toBe(true);
    expect(v.isMarginInterest("Bank Fees")).toBe(false);
    expect([v.passiveIncome("Dividends"), v.passiveIncome("Interest"), v.passiveIncome("Tax Refund")])
      .toEqual(["dividends", "interest", null]);
  });
  it("follow a subcategory rename, under both spellings", () => {
    const v = categoryVocab([pairRename("Fees & Interest", "Margin Interest", "Fees & Interest", "Margin Cost"),
      pairRename("Income", "Dividends", "Income", "Dividend Income")]);
    expect(v.isMarginInterest("Margin Cost")).toBe(true);
    expect(v.isMarginInterest("Margin Interest")).toBe(true);
    expect(v.passiveIncome("Dividend Income")).toBe("dividends");
  });
  it("a category rename leaves the subcategory names alone", () => {
    const v = categoryVocab([rename("Income", "Earnings")]);
    expect(v.passiveIncome("Interest")).toBe("interest");
  });
  it("a subcategory renamed onto a seeded one merges nothing: Salary -> Dividends", () => {
    const v = categoryVocab([pairRename("Income", "Salary", "Income", "Dividends"), pairRename("Income", "Gifts Received", "Income", "Interest")]);
    expect(v.passiveIncome("Dividends")).toBe("dividends");
    expect(v.passiveIncome("Interest")).toBe("interest");
    expect(v.passiveIncome("Salary")).toBeNull();
  });
});

// The income streams are the data's own: a chart reads the income subcategories present (sections/CashFlow.tsx), so the
// vocabulary keeps no stream list and the shipped taxonomy names each stream by its kind, never by a person.
describe("the dashboard keeps no income-stream list", () => {
  it("the vocabulary module exports none, and a vocabulary answers no stream question", () => {
    expect(Object.keys(categories).filter((k) => /stream/i.test(k))).toEqual([]);
    expect(Object.keys(categoryVocab([])).filter((k) => /stream/i.test(k))).toEqual([]);
  });
  it("the shipped income subcategories are stream kinds: no ' - Name' suffix but a role word", () => {
    const income = SHIPPED_TAXONOMY[SHIPPED_RESERVED.income_category];
    expect(income).toEqual(expect.arrayContaining(["Salary", "Bonus", "Consulting"]));
    const ROLE_WORDS = ["Kids", "Children", "Adults", "Family", "Household"];
    const named = Object.values(SHIPPED_TAXONOMY).flat()
      .flatMap((s) => [...s.matchAll(/ - ([A-Z][\w'’-]*)/g)].map((m) => m[1])).filter((w) => !ROLE_WORDS.includes(w));
    expect(named).toEqual([]);
  });
});

// The names the engine files rows under are the data's (assumptions.reserved), so a household whose taxonomy names them
// differently gets the same answers under its own names. Invented names throughout.
describe("the vocabulary reads the reserved names from assumptions.reserved", () => {
  const OTHER: ReservedNames = {
    fallback_category: "Misc", uncategorized_sub: "Unsorted", p2p_unknown_sub: "Person-to-person",
    internal_category: "Moves", income_category: "Earnings", income_other_sub: "Other earnings", fees_category: "Charges",
  };
  const v = vocabOf({ category_renames: [], reserved: OTHER });
  const S = SHIPPED_RESERVED;
  it("the uncategorized pile is the data's fallback category with its two reserved subcategories", () => {
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Unsorted" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Person-to-person" })).toBe(true);
    expect(v.isUncategorizedPile({ category: "Misc", subcategory: "Legal" })).toBe(false);
    expect(v.isUncategorizedPile({ category: S.fallback_category, subcategory: S.uncategorized_sub })).toBe(false);
    expect(v.isVenmoUnknownSub("Person-to-person")).toBe(true);
    expect(v.isVenmoUnknownSub(S.p2p_unknown_sub)).toBe(false);
    expect(v.cashMisc).toBe("Misc");
  });
  it("the internal bucket and the income category are the data's", () => {
    expect(v.isInternal("Moves")).toBe(true);
    expect(v.isInternal(S.internal_category)).toBe(false);
    expect(v.isTypeDriven("Moves")).toBe(true);
    expect(v.isTypeDriven("Earnings")).toBe(true);
    expect(v.isTypeDriven(S.income_category)).toBe(false);
    expect(v.income).toBe("Earnings");
  });
  it("the fees category is the data's: essential by default, in the essentials list, margin interest under it", () => {
    expect(v.isEssentialDefault("Charges", "Bank Fees")).toBe(true);
    expect(v.isEssentialDefault(S.fees_category, "Bank Fees")).toBe(false);
    expect(v.essentialLabels().categories).toContain("Charges");
    expect(v.essentialLabels().categories).not.toContain(S.fees_category);
    expect(v.isMarginInterest("Margin Interest")).toBe(true);
    expect(v.isEssentialDefault("Utilities", "Internet")).toBe(true);           // the shipped essentials are untouched
  });
  it("a pair rename under the data's own category reaches the pairs that hang off it", () => {
    const r = vocabOf({ reserved: OTHER, category_renames: [pairRename("Misc", "ATM", "Misc", "Cash Withdrawal"),
      pairRename("Earnings", "Dividends", "Earnings", "Dividend Income"), pairRename("Charges", "Margin Interest", "Charges", "Margin Cost")] });
    expect(r.isMoneyMovementSub("Cash Withdrawal")).toBe(true);
    expect(r.passiveIncome("Dividend Income")).toBe("dividends");
    expect(r.isMarginInterest("Margin Cost")).toBe(true);
    // the same renames written under the shipped names reach nothing here
    const shipped = vocabOf({ reserved: OTHER, category_renames: [pairRename(S.fallback_category, "ATM", S.fallback_category, "Cash Withdrawal")] });
    expect(shipped.isMoneyMovementSub("Cash Withdrawal")).toBe(false);
  });
  it("one object per reserved names and renames: the same pair gives the same object, either one changed rebuilds", () => {
    const renames: CategoryRename[] = [];
    expect(vocabOf({ category_renames: renames, reserved: OTHER })).toBe(vocabOf({ reserved: OTHER, category_renames: renames }));
    expect(vocabOf({ category_renames: renames, reserved: { ...OTHER } })).not.toBe(vocabOf({ category_renames: renames, reserved: OTHER }));
    expect(vocabOf({ category_renames: [], reserved: OTHER })).not.toBe(vocabOf({ category_renames: renames, reserved: OTHER }));
    expect(vocabOf({ category_renames: renames, reserved: S })).not.toBe(vocabOf({ category_renames: renames, reserved: OTHER }));
  });
});

// SHIPPED_TAXONOMY is the vocabulary the code was written against - not a mirror of config/category_rules.yaml, which the
// owner edits without touching code. These tests read no config.
describe("SHIPPED_TAXONOMY holds every name the vocabulary's classes are written against", () => {
  it("the shipped reserved names are its own: each reserved category, each reserved subcategory under its category", () => {
    const S = SHIPPED_RESERVED;
    for (const c of [S.fallback_category, S.internal_category, S.income_category, S.fees_category]) {
      expect(c in SHIPPED_TAXONOMY, c).toBe(true);
    }
    for (const [c, s] of [[S.fallback_category, S.uncategorized_sub], [S.fallback_category, S.p2p_unknown_sub],
      [S.income_category, S.income_other_sub]] as const) {
      expect(SHIPPED_TAXONOMY[c]?.includes(s), `${c} / ${s}`).toBe(true);
    }
  });
  // read through the vocabulary itself (shipped reserved names, no rename): a list that lists its names gives them; a
  // class only asked about is counted over the taxonomy, so a name misspelt in it matches nothing and the count drops
  const shipped = categoryVocab([]);
  it("every category a class list names is a category of it", () => {
    const S = SHIPPED_RESERVED;
    const essential = shipped.essentialLabels().categories;
    expect(essential.length).toBeGreaterThan(6);
    for (const c of [S.fallback_category, S.internal_category, S.income_category, ...essential]) {
      expect(c in SHIPPED_TAXONOMY, `${c} is in no class list's taxonomy`).toBe(true);
    }
    expect(Object.keys(SHIPPED_TAXONOMY).filter((c) => shipped.isSubscriptionLike(c)), "both subscription-like categories")
      .toHaveLength(2);
  });
  it("every category / subcategory pair a class list names is one of its seeded pairs", () => {
    const S = SHIPPED_RESERVED;
    const pairs = [
      ...shipped.essentialLabels().pairs.map((p) => p.split(" / ") as [string, string]),
      ...shipped.moneyMovementLabels().map((s): [string, string] => [S.fallback_category, s]),
    ];
    expect(pairs.length).toBeGreaterThan(9);
    for (const [c, s] of pairs) expect(SHIPPED_TAXONOMY[c]?.includes(s), `${c} / ${s}`).toBe(true);
    expect(SHIPPED_TAXONOMY[S.fees_category].filter((s) => shipped.isMarginInterest(s)), "margin interest").toHaveLength(1);
    expect(SHIPPED_TAXONOMY[S.income_category].map((s) => shipped.passiveIncome(s)).filter((k) => k !== null).sort(),
      "dividends and interest").toEqual(["dividends", "interest"]);
  });
});

describe("a rename onto a name outside SHIPPED_TAXONOMY is a pure rename; onto one inside it, a merge", () => {
  it("the name the ETL asks for first - Transport -> Transportation - is outside it, so the class moves with the name", () => {
    expect("Transportation" in SHIPPED_TAXONOMY).toBe(false);
    const v = categoryVocab([rename("Transport", "Transportation")]);
    expect(v.isEssentialDefault("Transportation", "Fuel")).toBe(true);
    expect(v.essentialLabels().categories).toContain("Transportation");
  });
  it("a category the owner adds to the YAML later is just another new name: nothing in the code needs to know it", () => {
    for (const [from, to, probe] of [["Pets", "Companion Animals", () => categoryVocab([rename("Pets", "Companion Animals")]).isEssentialDefault("Companion Animals", "")],
      ["Entertainment", "Subscriptions & Fun", () => categoryVocab([rename("Entertainment", "Subscriptions & Fun")]).isSubscriptionLike("Subscriptions & Fun")]] as const) {
      expect(to in SHIPPED_TAXONOMY, `${from} -> ${to}`).toBe(false);
      expect(probe(), `${from} -> ${to}`).toBe(true);
    }
  });
  it("the same rename onto a name the taxonomy holds is a merge: the target keeps its own class", () => {
    expect("Travel" in SHIPPED_TAXONOMY).toBe(true);
    const v = categoryVocab([rename("Transport", "Travel")]);
    expect(v.isEssentialDefault("Travel", "Flights")).toBe(false);
    expect(v.isEssentialDefault("Transport", "Fuel")).toBe(true);        // the merged-away name still reads as itself
  });
});

describe("the names that are not taxonomy names", () => {
  it("a row with no category is named once", () => {
    expect(NO_CATEGORY).toBe("Uncategorized");
  });
  it("the coverage floor is not a constant here: it is data (lib/agg.ts coverageFloor reads the export's in_coverage)", () => {
    expect(Object.keys(categories).filter((k) => /COVERAGE/i.test(k))).toEqual([]);
  });
});

// Reads the SOURCE (node:fs), like capabilities.test.ts: the vocabulary is only one home if nothing re-types it.
const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
// the test suites' support modules (src/test/: the exports' loader, the money scanners, the matrix's checks), which
// nothing in the application imports: like the fixtures, they are no part of the dashboard's code
const TEST_SUPPORT = join(SRC, "test");
const appDir = (dir: string, name: string) => join(dir, name) !== TEST_SUPPORT;
function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? (appDir(dir, e.name) ? sourceFiles(join(dir, e.name)) : [])
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) && e.name !== "categories.ts" && e.name !== "__fixtures__.ts" ? [join(dir, e.name)] : []);
}
describe("the category vocabulary is the one home for the names it holds", () => {
  const files = sourceFiles(SRC);
  const hits = (re: RegExp) => files.filter((f) => re.test(readFileSync(f, "utf8"))).map((f) => f.slice(SRC.length + 1));
  it("scans a non-trivial corpus, so an empty read cannot pass", () => {
    expect(files.length).toBeGreaterThan(50);
  });
  it("no other source file keeps a quoted copy of the money-movement, pile or essential names", () => {
    expect(hits(/"(?:Checks|Wire|ATM|Venmo\/Zelle Unknown|Cash & Misc|Fees & Interest)"/)).toEqual([]);
  });
  // every category of the shipped taxonomy, the subcategories the vocabulary answers for, and the income streams (the
  // charts read those from the data), quoted in code (a comment may say them)
  const NAMES = [...Object.keys(SHIPPED_TAXONOMY), "Uncategorized", "Venmo/Zelle Unknown", "Margin Interest",
    "Salary", "Bonus", "Consulting", "Gifts Received", "Dividends"];
  // what is allowed to stay, and why: a table keyed by shipped names that is read through the vocabulary, and words
  // that happen to be a category's name on screen
  const ALLOWED: Record<string, string[]> = {
    "sections/CashFlow.tsx": ["Income"],                        // chart series labels
    "sections/secondhouse-cards.tsx": ["Insurance", "Utilities"],   // cost-breakdown labels
    "sections/SecondHouse.tsx": ["Utilities"],                  // an input's label
  };
  const quoted = (file: string): Set<string> => {
    const code = readFileSync(file, "utf8").split("\n").filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join("\n");
    return new Set(NAMES.filter((n) => code.includes(`"${n}"`)));
  };
  it("no other source file quotes a shipped category or a name the vocabulary answers for, except the listed labels", () => {
    const stray = files.flatMap((f) => {
      const rel = f.slice(SRC.length + 1);
      return [...quoted(f)].filter((n) => !(ALLOWED[rel] ?? []).includes(n)).map((n) => `${rel}: ${n}`);
    });
    expect(stray).toEqual([]);
  });
  it("every allowed label is really there (the list cannot rot)", () => {
    for (const [rel, names] of Object.entries(ALLOWED)) {
      const found = quoted(join(SRC, rel));
      for (const n of names) expect(found.has(n), `${rel} no longer quotes ${n}`).toBe(true);
    }
  });
});

// A fixed year-month in code is one household's calendar (a coverage floor, a "prior year" column): the data says
// which months exist. Every non-test source file is read, this one's subject included; comment lines may keep
// examples. A hit prints its file and line number only.
const YEAR_MONTH = /\b20\d\d-(0[1-9]|1[0-2])\b/;
function codeFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? (appDir(dir, e.name) ? codeFiles(join(dir, e.name)) : [])
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) && e.name !== "__fixtures__.ts" ? [join(dir, e.name)] : []);
}
describe("no fixed year-month in the dashboard's code", () => {
  const files = codeFiles(SRC);
  it("scans every source file, the vocabulary included, so an empty read cannot pass", () => {
    expect(files.length).toBeGreaterThan(sourceFiles(SRC).length);
    expect(files.some((f) => f.endsWith(join("lib", "categories.ts")))).toBe(true);
  });
  it("the pattern is a year-month, alone or inside a date, and nothing else", () => {
    for (const s of ['"2030-07"', "`2031-12-31`", "x = '2029-01';"]) expect(YEAR_MONTH.test(s), s).toBe(true);
    for (const s of ["2030-13", "2030-00", "1999-05", "12030-05", "v2030-05", "2000 ms"]) expect(YEAR_MONTH.test(s), s).toBe(false);
  });
  it("no code line spells one (comment lines are left out)", () => {
    const stray = files.flatMap((f) => readFileSync(f, "utf8").split("\n").flatMap((line, i) =>
      !/^\s*(\/\/|\*|\/\*)/.test(line) && YEAR_MONTH.test(line) ? [`${f.slice(SRC.length + 1)}:${i + 1}`] : []));
    expect(stray).toEqual([]);
  });
});

// A date sentinel - "9999" as the minimum of no dates, "0000" as their maximum, "9999-12-31" as an open end - passes for
// a date in every comparison and reaches the page as one ("Invalid Date", a link to year 0). No date is null, and the
// code says what it does then. Every non-test source file is read, as above; comment lines may name one. A hit prints
// its file and line number only.
const SENTINEL = /["'`](?:9999|0000)/;
describe("the dashboard's code holds no date sentinel", () => {
  const files = codeFiles(SRC);
  it("the pattern is a quoted string that starts 9999 or 0000, and nothing else", () => {
    for (const s of ['let min = "9999", max = "0000";', "'0000-01-01'", "`9999-12-31`", 'p.to ?? "9999-12"']) expect(SENTINEL.test(s), s).toBe(true);
    for (const s of ['"19999"', '"2030-09-99"', "x = 9999", '"#9999"', '"10000"', "a0000", "'x0000'"]) expect(SENTINEL.test(s), s).toBe(false);
  });
  it("no code line spells one (comment lines are left out)", () => {
    expect(files.length).toBeGreaterThan(50);
    const stray = files.flatMap((f) => readFileSync(f, "utf8").split("\n").flatMap((line, i) =>
      !/^\s*(\/\/|\*|\/\*)/.test(line) && SENTINEL.test(line) ? [`${f.slice(SRC.length + 1)}:${i + 1}`] : []));
    expect(stray).toEqual([]);
  });
});

// Application code reads the wall clock in one place, lib/clock.ts: one place for a test or the screenshot harness to
// freeze, one definition of the household's day. Every non-test source file is read, as above; string literals are
// emptied first (a template literal keeps its `${…}` parts, which are code), then comments are stripped (a whole comment
// line, a `//` comment, a `/* … */` on one line), so prose and strings may name a read. A hit prints its file and line
// number only. A hit is any of:
// - a zero-argument `new Date()`, or a bare `new Date`;
// - `Date.now`, called or passed as a value (`useState(Date.now)`, `.map(Date.now)`);
// - the global object's `Date` (`new window.Date()`, `new globalThis.Date()`, `self.Date`);
// - a call of `Date(…)` that is not `new` and no member (`Date()` is the current time as a string);
// - `Date` itself as a value, after `=`, `(` or `,` (`const D = Date`, `Reflect.construct(Date, [])`), and before `,`,
//   `;`, `)`, `}` or the line's end: the `=` of a type alias and a `]` after it (a tuple type) are type positions;
// - a zero-argument `dayjs()`, or `dayjs(undefined)`.
// `new Date(x)` (`new Date(undefined)` is an invalid date, not a read), `Date.UTC(…)`, `Date.parse(…)`, a type position
// (`: Date`, `Date | null`, `type When = Date`, `[string, Date]`) and `instanceof Date` are no read.
const CLOCK_READ = new RegExp([
  /\bnew\s+Date\b\s*(?:\(\s*\)|(?![\s(]))/.source,
  /\bDate\s*\.\s*now\b/.source,
  /\b(?:window|globalThis|self)\s*\.\s*Date\b/.source,
  /(?<![.$]\s*)(?<!\bnew\s+)\bDate\s*\(/.source,
  /(?:(?<!^\s*(?:export\s+)?type\s+[\w$]+\s*(?:<[^>]*>\s*)?)=|[(,])\s*Date\s*(?=[,;)}]|$)/.source,
  /\bdayjs\s*\(\s*(?:undefined\s*)?\)/.source,
].join("|"));
/** a template literal's code: its `${…}` parts (one level deep), its text dropped */
const templateCode = (body: string): string => (body.match(/\$\{[^}]*\}/g) ?? []).join(" ");
const codeOnly = (line: string): string => /^\s*(\/\/|\*|\/\*)/.test(line) ? ""
  : line.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, '""')
    .replace(/`((?:[^`\\]|\\.)*)`/g, (_, body: string) => `\`${templateCode(body)}\``)
    .replace(/\/\*.*?\*\//g, "").replace(/\/\/.*$/, "");
describe("application code reads the clock in one place", () => {
  const files = codeFiles(SRC);
  const CLOCK = join(SRC, "lib", "clock.ts");
  it("the pattern is a read of the wall clock, and nothing else", () => {
    for (const s of ["const d = new Date();", "new Date ()", "x = new Date;", "Date.now()", "f(Date . now ())", "dayjs()",
      "dayjs( ).format()",
      // the clock passed as a value: the pattern needs no call
      "const [t] = useState(Date.now);", "xs.map(Date.now)", "const f = Date.now;",
      // Date() without new is the current time as a string
      "const s = Date();", "return Date()", "label(String(Date()))", "x = Date ( )",
      // the global object's Date
      "const d = new window.Date();", "new globalThis.Date()", "const D = self.Date;",
      // Date passed or assigned as a value, then constructed elsewhere
      "const D = Date;", "const D = Date", "Reflect.construct(Date, [])", "make(a, Date)", "pick(Date, x)",
      "useState(Date)", "(0, Date)()",
      "dayjs(undefined)", "dayjs( undefined )",
      // a ` //` inside a string is no comment: the read after it on the line is code
      'const u = "a // b"; const d = new Date();', "const u = 'http://x'; Date.now();",
      // a template literal's ${…} is code
      "const s = `at ${new Date()}`;",
    ]) expect(CLOCK_READ.test(codeOnly(s)), s).toBe(true);
    for (const s of ["new Date(iso)", "new Date(y, m - 1, 1)", "new Date(Number.NaN)", "dayjs(iso)", "NotDate.now()",
      "new Dates()", "// new Date()", " * Date.now() below", "x = 1; // reads new Date()", "f(/* new Date() */ iso)",
      "new DateTimeFormat()", "const d = new Date(\n",
      // an invalid date, not a read; the static helpers; type positions; instanceof
      "new Date(undefined)", "Date.UTC(y, m, 1)", "const t = Date.parse(iso);", "let d: Date;", "const d: Date | null = null;",
      "useState<Date | null>(null)", "(d: Date) => d", "x instanceof Date", "function f(a: Date, b: Date): Date[] {",
      "type When = Date;", "export type When = Date;", "let p: [number, Date];", "function f(a: [string, Date]) {}",
      // a name that holds Date, a member called Date, and strings or a template's text that name a read
      "toDate(x)", "isDate(v)", "lib.Date(x)", "const s = 'new Date()';", 'const s = "Date.now()";', "const s = `Date()`;",
    ]) expect(CLOCK_READ.test(codeOnly(s)), s).toBe(false);
  });
  it("lib/clock.ts reads it (the one exception is real) and no other code line does", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(files).toContain(CLOCK);
    expect(readFileSync(CLOCK, "utf8").split("\n").some((line) => CLOCK_READ.test(codeOnly(line)))).toBe(true);
    const stray = files.filter((f) => f !== CLOCK).flatMap((f) => readFileSync(f, "utf8").split("\n").flatMap((line, i) =>
      CLOCK_READ.test(codeOnly(line)) ? [`${f.slice(SRC.length + 1)}:${i + 1}`] : []));
    expect(stray).toEqual([]);
  });
});

// The scans above leave src/test/ out because nothing in the application imports it; this holds that true. Every
// source file outside src/test/ that is not a test file or the fixtures is read, and an import, export-from or dynamic
// import whose specifier reaches src/test/ - relative, or by a root alias - is a hit (file, line and specifier).
const SPECIFIERS = /(?:\bfrom|\bimport)\s*\(?\s*["']([^"']+)["']/g;
const intoTestSupport = (file: string, spec: string): boolean =>
  spec.startsWith(".") ? (resolve(dirname(file), spec) + sep).startsWith(TEST_SUPPORT + sep)
    : /^(?:[@~#]\/|\/)?(?:src\/)?test(?:\/|$)/.test(spec);
const testImports = (file: string, code: string): string[] => code.split("\n").flatMap((line, i) =>
  /^\s*(\/\/|\*|\/\*)/.test(line) ? []
    : [...line.matchAll(SPECIFIERS)].filter((m) => intoTestSupport(file, m[1])).map((m) => `${file.slice(SRC.length + 1)}:${i + 1} ${m[1]}`));
describe("nothing in the application imports the test suites' support", () => {
  const files = codeFiles(SRC);
  it("the scan finds a relative or aliased import from src/test/, and nothing else", () => {
    const at = join(SRC, "lib", "x.ts");
    for (const line of ['import { textHits } from "../test/moneyScan";', 'import type { MatrixGap } from "../test/matrixGaps";',
      'export { where } from "../test/routerProbe";', 'const m = await import("../test/exports");', 'import "../test/demoClock";',
      'import { a } from "@/test/exports";', 'import { a } from "src/test/exports";', "import { a } from '../../src/test/truth';"]) {
      expect(testImports(at, line).length, line).toBe(1);
    }
    for (const line of ['import { fmtUSD } from "./format";', 'import { a } from "../testing/x";', 'import { a } from "./test";',
      'import { a } from "@testing-library/react";', 'import { a } from "../lib/test-helpers";', '// import { a } from "../test/exports";',
      'const label = "from ../test/exports";']) {
      expect(testImports(at, line), line).toEqual([]);
    }
  });
  it("no application file imports from src/test/", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(files.flatMap((f) => testImports(f, readFileSync(f, "utf8")))).toEqual([]);
  });
});

// A count prints through countOf (one locale, the noun beside it), never a bare toLocaleString(), whose digits follow
// the browser's locale; and a page reads the current snapshot through useSnapshots().latest, never as the list's last
// element by hand. Every non-test source file is read, as above (a library function that takes a list may index it);
// comment lines are left out. A hit prints its file and line number only.
const BARE_COUNT = /\.toLocaleString\(\)/;
const LAST_SNAPSHOT = /\bsnap(?:shot)?s\[[\w.]+\.length - 1\]|\bsnap(?:shot)?s\.at\(-1\)/;
describe("counts and the current snapshot each have one way in", () => {
  const files = codeFiles(SRC);
  const stray = (re: RegExp, only?: (rel: string) => boolean) => files.flatMap((f) => {
    const rel = f.slice(SRC.length + 1);
    return !only || only(rel) ? readFileSync(f, "utf8").split("\n").flatMap((line, i) =>
      !/^\s*(\/\/|\*|\/\*)/.test(line) && re.test(line) ? [`${rel}:${i + 1}`] : []) : [];
  });
  it("the patterns find what they mean and nothing else", () => {
    for (const s of ["{n.toLocaleString()} left", "const snaps = x; snaps[snaps.length - 1]", "data.assets.snapshots[data.assets.snapshots.length - 1]",
      "snapshots.at(-1)"]) expect(BARE_COUNT.test(s) || LAST_SNAPSHOT.test(s), s).toBe(true);
    for (const s of ['n.toLocaleString("en-US")', "rows[rows.length - 1]", "snaps[0]", "useSnapshots().latest"]) {
      expect(BARE_COUNT.test(s) || LAST_SNAPSHOT.test(s), s).toBe(false);
    }
  });
  it("no code line prints a count with a bare toLocaleString()", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(stray(BARE_COUNT)).toEqual([]);
  });
  it("no page or component reads the current snapshot as the list's last element", () => {
    expect(stray(LAST_SNAPSHOT, (rel) => /^(sections|components)\//.test(rel))).toEqual([]);
  });
});
