// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The category names the app hard-codes, in one place.
//
// Two kinds of name. The names the engine itself files rows under - the fallback category with its
// uncategorized and P2P-unknown subcategories, the internal, income and fees categories - come from the data
// (assumptions.reserved, etl/models.py RESERVED); SHIPPED_RESERVED is what an older export without them reads
// as. Every other name below is the one starter/category_rules.yaml ships. A category rename (`renames:` in a
// household's category_rules.yaml, exported as assumptions.category_renames) moves the references the ETL
// rewrites - answers, overrides, learned rules - but not these literals, so `categoryVocab(assumptions)` answers
// every question about them in the CURRENT spelling: a rename of Transport can never silently drop it from the
// essentials (cash runway, forecast floor, fixed vs discretionary, Second House cushion, the emergency fund).
//
// A name matches under both spellings. The ETL still emits some names itself (a keyword rule not edited yet),
// so after a rename the shipped spelling and the renamed one can both be on rows, and both are the same category.
//
// A rename that MERGES onto a category the taxonomy already holds (Pets -> Shopping) adds nothing: the target
// keeps the class it has (Shopping stays non-essential), and the merged-away name keeps matching only under
// its own shipped spelling. The same goes for a subcategory renamed onto a seeded one. SHIPPED_TAXONOMY is the
// list that tells; it is frozen to this code's own vocabulary (see there).
//
// Consumers build the vocabulary once from the data they hold - categoryVocab(data.assumptions) - and ask it;
// none keeps a category literal of its own.
import type { Assumptions, CategoryRename, ReservedNames, Transaction } from "../data/types";
import { mapCategory, mapCategoryName } from "./categoryRenames";

/** what a row with no category is called: a spend key and a column head, not a taxonomy name */
export const NO_CATEGORY = "Uncategorized";

type Pair = readonly [category: string, subcategory: string];

/** The category and subcategory names the classifications in this file were written against: the taxonomy
 *  starter/category_rules.yaml ships, name for name and in order (tests/test_starter.py pins the two together). It is
 *  what a rename can MERGE onto, and it is NOT kept equal to a household's own YAML: a category the owner adds there
 *  later is correctly absent, so a pure rename onto that new name (Transport -> Transportation) still inherits the old
 *  class, and adding to the YAML needs no code change. */
export const SHIPPED_TAXONOMY: Readonly<Record<string, readonly string[]>> = {
  Income: ["Salary", "Bonus", "Consulting", "Side Income",
    "Reimbursement", "Dividends", "Interest", "Tax Refund", "Gifts Received", "Other"],
  Housing: ["Mortgage", "Property Tax", "Home Insurance", "Home Improvement", "Home Services", "HOA"],
  Utilities: ["Electric/Gas", "Water/Trash", "Internet", "Mobile"],
  Groceries: [],
  Dining: ["Restaurants", "Coffee", "Delivery", "Bars"],
  Kids: ["Childcare", "Activities", "School", "Supplies", "Healthcare - Kids"],
  Health: ["Medical", "Dental", "Pharmacy", "Therapy", "Fitness"],
  Transport: ["Fuel", "Rideshare", "Parking/Tolls", "Car Insurance", "Car Maintenance", "Car Payment"],
  Travel: ["Flights", "Lodging", "Other"],
  Shopping: ["Clothing", "Electronics", "Home Goods", "Amazon", "Gifts"],
  Subscriptions: ["Streaming", "Software", "News/Media", "Memberships"],
  Entertainment: ["Events", "Hobbies", "Books/Games"],
  "Personal Care": ["Hair/Beauty", "Other"],
  Pets: [],
  Giving: ["Charity", "Gifts to People"],
  "Fees & Interest": ["Bank Fees", "Late Fees", "Margin Interest", "Service Fees", "Card Interest"],
  Debt: ["Student Loan"],
  Insurance: ["Life"],
  Taxes: ["Income Tax", "Property Tax"],
  "Cash & Misc": ["ATM", "Venmo/Zelle Unknown", "Checks", "Wire", "Legal", "Services", "Uncategorized"],
  Internal: [],
};
const SHIPPED_CATEGORIES: ReadonlySet<string> = new Set(Object.keys(SHIPPED_TAXONOMY));
const SHIPPED_SUBS: ReadonlySet<string> = new Set(Object.values(SHIPPED_TAXONOMY).flat());

/** The names the engine files rows under as starter/category_rules.yaml ships them (etl/models.py RESERVED): the
 *  default an export without assumptions.reserved is read with (data/DataContext.tsx loadAppData). */
export const SHIPPED_RESERVED: Readonly<ReservedNames> = Object.freeze({
  fallback_category: "Cash & Misc", uncategorized_sub: "Uncategorized", p2p_unknown_sub: "Venmo/Zelle Unknown",
  internal_category: "Internal", income_category: "Income", income_other_sub: "Other", fees_category: "Fees & Interest",
});

// ---- the shipped names ----
// A class that hangs off a reserved category names only its subcategories here; the pairs are built under the
// data's own reserved names (reservedLists).

/** one-off money movements, not living costs: out of the emergency basis and the core-spend figure (subcategories of
 *  the fallback category) */
const MONEY_MOVEMENT_SUBS = ["Checks", "Wire", "ATM"] as const;

/** What keeps getting paid in a real emergency; anything not matched is discretionary by default (the
 *  emergency-fund list lets the family flip either way). The fees category follows these (reservedLists). */
const ESSENTIAL_CATEGORIES = [
  "Utilities", "Groceries", "Health", "Insurance", "Transport", "Pets", "Debt",
] as const;
const ESSENTIAL_PAIRS: readonly Pair[] = [
  ["Housing", "Mortgage"], ["Housing", "HOA"], ["Housing", "Home Insurance"],
  ["Kids", "Childcare"], ["Kids", "School"], ["Kids", "Supplies"], ["Kids", "Healthcare - Kids"],
];

/** the subscription-like categories a long-running small charge can be forgotten in */
const SUBSCRIPTION_LIKE = ["Subscriptions", "Entertainment"] as const;

/** margin interest: the subcategory of the fees category Investments totals */
const MARGIN_INTEREST_SUB = "Margin Interest";

/** The passive-income subcategories the Investments KPI totals (lib/passiveIncome.ts). */
export type PassiveIncome = "dividends" | "interest";
const passiveIncomePairs = ({ income_category: income }: ReservedNames): Readonly<Record<PassiveIncome, Pair>> => ({
  dividends: [income, "Dividends"], interest: [income, "Interest"],
});

/** Every class list that hangs off a reserved name, built under one set of reserved names. */
function reservedLists(r: ReservedNames) {
  const fallback = r.fallback_category;
  /** a purchase the rules could not place: the Review inbox's category question */
  const pile: readonly Pair[] = [[fallback, r.uncategorized_sub], [fallback, r.p2p_unknown_sub]];
  const p2pUnknown: Pair = [fallback, r.p2p_unknown_sub];
  const moneyMovement: readonly Pair[] = MONEY_MOVEMENT_SUBS.map((s): Pair => [fallback, s]);
  const essentialCategories: readonly string[] = [...ESSENTIAL_CATEGORIES, r.fees_category];
  const marginInterest: Pair = [r.fees_category, MARGIN_INTEREST_SUB];
  return { pile, p2pUnknown, moneyMovement, essentialCategories, marginInterest, passiveIncome: passiveIncomePairs(r) };
}

const SHIPPED_LISTS = reservedLists(SHIPPED_RESERVED);
/** Every shipped name the class lists above are written against (under the shipped reserved names), so a test can
 *  check each one is in SHIPPED_TAXONOMY (a new class list is added here too). */
export const VOCABULARY_NAMES: { readonly categories: readonly string[]; readonly pairs: readonly Pair[] } = {
  categories: [SHIPPED_RESERVED.fallback_category, SHIPPED_RESERVED.internal_category, SHIPPED_RESERVED.income_category,
    ...SHIPPED_LISTS.essentialCategories, ...SUBSCRIPTION_LIKE],
  pairs: [...ESSENTIAL_PAIRS, ...SHIPPED_LISTS.pile, SHIPPED_LISTS.p2pUnknown, ...SHIPPED_LISTS.moneyMovement,
    SHIPPED_LISTS.marginInterest, ...Object.values(SHIPPED_LISTS.passiveIncome)],
};

// ---- the vocabulary ----

export interface CategoryVocab {
  /** the current name of a shipped category, for a link or a label ("Transport" -> "Transportation" once renamed) */
  name(shipped: string): string;

  /** essential by default: a whole essential category, or one of the essential category / subcategory pairs */
  isEssentialDefault(category: string, subcategory: string): boolean;
  /** the essential-by-default list for prose: whole categories, then `Category / Sub` pairs (a toggle key's spelling) */
  essentialLabels(): { categories: string[]; pairs: string[] };
  /** a checks / wire / ATM subcategory */
  isMoneyMovementSub(subcategory: string | null): boolean;
  /** the money-movement subcategories' current names, for prose that says what a figure leaves out */
  moneyMovementLabels(): string[];

  /** the fallback category with its uncategorized or its P2P-unknown subcategory (the reserved names) */
  isUncategorizedPile(t: Pick<Transaction, "category" | "subcategory">): boolean;
  /** the current name of the fallback category, for prose about the pile */
  readonly cashMisc: string;
  /** the P2P-unknown subcategory: a person-to-person payment no rule names */
  isVenmoUnknownSub(subcategory: string | null): boolean;

  /** the transfer bucket (the internal category), which no spend report counts */
  isInternal(category: string | null): boolean;
  /** the internal or the income category: the categories a row's type decides, which an answer cannot choose */
  isTypeDriven(category: string | null): boolean;
  /** the current name of the income category */
  readonly income: string;

  /** subscription-like, for the "forgotten?" flag */
  isSubscriptionLike(category: string | null): boolean;

  /** margin interest, the fees category's subcategory Investments totals */
  isMarginInterest(subcategory: string | null): boolean;
  /** which passive-income subcategory an Income subcategory is, or null */
  passiveIncome(subcategory: string | null): PassiveIncome | null;
}

const pairKey = (category: string, subcategory: string) => `${category}\u0000${subcategory}`;

/** reserved names -> renames list -> the vocabulary built from the two */
const cache = new WeakMap<ReservedNames, WeakMap<readonly CategoryRename[], CategoryVocab>>();

/** The vocabulary of a data set: its reserved names (`assumptions.reserved`) read through its category renames
 *  (`assumptions.category_renames`). One object per (reserved, renames) pair of objects, so it is a stable dependency
 *  however often the assumptions object around them is rebuilt. */
export function categoryVocab(assumptions: Pick<Assumptions, "category_renames" | "reserved">): CategoryVocab {
  const { reserved, category_renames: renames } = assumptions;
  let byRenames = cache.get(reserved);
  if (!byRenames) cache.set(reserved, byRenames = new WeakMap());
  let v = byRenames.get(renames);
  if (!v) byRenames.set(renames, v = build(reserved, renames));
  return v;
}

function build(reserved: ReservedNames, renames: readonly CategoryRename[]): CategoryVocab {
  const lists = reservedLists(reserved);
  /** the renamed spelling of a category, or null when nothing renamed it or it MERGES onto a shipped category */
  const renamedCat = (c: string): string | null => {
    const m = mapCategoryName(c, renames);
    return m !== c && !SHIPPED_CATEGORIES.has(m) ? m : null;
  };
  /** the renamed spelling of a pair, or null likewise (a merge onto a shipped category, or onto a seeded sibling) */
  const renamedPair = (c: string, s: string): readonly [string, string | null] | null => {
    const [c2, s2] = mapCategory(c, s, renames);
    if (c2 === c && s2 === s) return null;
    if (c2 !== c && SHIPPED_CATEGORIES.has(c2)) return null;
    if (c2 === c && s2 !== null && SHIPPED_TAXONOMY[c]?.includes(s2)) return null;
    return [c2, s2];
  };
  /** the renamed spelling of a subcategory taken on its own (matched by name, whatever the category), or null */
  const renamedSub = (c: string, s: string): string | null => {
    const [c2, s2] = mapCategory(c, s, renames);
    if (s2 === null || s2 === s) return null;
    if (c2 !== c && SHIPPED_CATEGORIES.has(c2)) return null;
    return SHIPPED_SUBS.has(s2) ? null : s2;
  };
  const spellings = (c: string): readonly string[] => {
    const r = renamedCat(c);
    return r === null ? [c] : [c, r];
  };
  /** whole categories: each one's shipped spelling and its renamed one */
  const cats = (names: readonly string[]): ReadonlySet<string> => new Set(names.flatMap(spellings));
  /** pairs, found by both spellings of each */
  const pairs = (list: readonly Pair[]): ReadonlySet<string> =>
    new Set(list.flatMap(([c, s]) => {
      const r = renamedPair(c, s);
      return r === null ? [pairKey(c, s)] : [pairKey(c, s), pairKey(r[0], r[1] ?? "")];
    }));
  /** subcategories on their own - matched by name whatever the category, as the app always did */
  const subs = (list: readonly Pair[]): ReadonlySet<string> =>
    new Set(list.flatMap(([c, s]) => {
      const r = renamedSub(c, s);
      return r === null ? [s] : [s, r];
    }));
  const has = (set: ReadonlySet<string>) => (v: string | null) => v !== null && set.has(v);
  const classify = <K extends string>(table: Readonly<Record<K, Pair>>) => {
    const sets = (Object.keys(table) as K[]).map((k) => [k, subs([table[k]])] as const);
    return (v: string | null): K | null => (v === null ? null : sets.find(([, set]) => set.has(v))?.[0] ?? null);
  };

  const essentialCategories = cats(lists.essentialCategories);
  const essentialPairs = pairs(ESSENTIAL_PAIRS);
  const pile = pairs(lists.pile);
  const internal = cats([reserved.internal_category]);
  const typeDriven = cats([reserved.internal_category, reserved.income_category]);

  return {
    name: (shipped) => mapCategoryName(shipped, renames),

    isEssentialDefault: (category, subcategory) =>
      essentialCategories.has(category) || essentialPairs.has(pairKey(category, subcategory)),
    essentialLabels: () => ({
      categories: [...new Set(lists.essentialCategories.map((c) => renamedCat(c) ?? c))],
      pairs: [...new Set(ESSENTIAL_PAIRS.map(([c, s]) => {
        const r = renamedPair(c, s);
        return r === null ? `${c} / ${s}` : `${r[0]} / ${r[1] ?? s}`;
      }))],
    }),
    isMoneyMovementSub: has(subs(lists.moneyMovement)),
    moneyMovementLabels: () => lists.moneyMovement.map(([c, s]) => renamedSub(c, s) ?? s),

    isUncategorizedPile: (t) => pile.has(pairKey(t.category ?? "", t.subcategory ?? "")),
    cashMisc: mapCategoryName(reserved.fallback_category, renames),
    isVenmoUnknownSub: has(subs([lists.p2pUnknown])),

    isInternal: has(internal),
    isTypeDriven: has(typeDriven),
    income: mapCategoryName(reserved.income_category, renames),

    isSubscriptionLike: has(cats(SUBSCRIPTION_LIKE)),

    isMarginInterest: has(subs([lists.marginInterest])),
    passiveIncome: classify(lists.passiveIncome),
  };
}
