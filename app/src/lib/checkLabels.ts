// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Quality checks are named for the ETL that raises them ("reconcile.demo_checking_0000.ledger_drift"). A person reads those as a
// code, so the account page's feed-status chips and the Data page's quality report show a label instead, with the id in a
// tooltip. The labels live here, beside nothing else, so the two pages cannot word one check two ways.

/** kinds that come once per account: the check name with the account's id removed ("simplefin.stale.<id>" -> "simplefin.stale") */
const PER_ACCOUNT: Record<string, string> = {
  "reconcile.store_vs_files": "Feed matches the files",
  "reconcile.ledger_drift": "Ledger vs feed balance",
  "simplefin.stale": "Bank feed reported no balance",
  "simplefin.balance_stale": "Bank balance is old",
  "simplefin.bad_balance": "Unreadable bank balance",
  "simplefin.bad_amount": "Unreadable bank amount",
  "simplefin.currency": "Non-USD account skipped",
  "simplefin.csv_clip": "Statement rows at the feed cutover",
  "simplefin.cutover_gap": "Gap at the feed cutover",
  "simplefin.cutover_overlap": "Overlap at the feed cutover",
  "simplefin.duplicate": "Duplicate bank feed id",
  "coverage": "Transaction coverage",
  "source": "Source files",
  "balance_rollforward": "Balance rolls forward",
  "import.balance_ignored": "A balance in its file is not used",
  "import.rollforward": "Balances in its file add up",
  "import.ofx_conflict": "Two different rows share one bank id in its files",
  "import.long_description": "Descriptions cut to 500 characters",
  "import.sources": "How its sources were joined",
  "import.sources_seam": "Rows where its sources meet",
  "import.matched_twice": "Files two of its sources both claim",
};

/** families named "<prefix><account id>.<tail>", whose tail names the thing: a statement file the account could not read
 *  ("import.<id>.<file name>", the file name whole, dots and all) */
const PER_ACCOUNT_FAMILIES: [prefix: string, label: (tail: string) => string][] = [
  ["import.", (file) => `Could not read ${file}`],
];

/** the per-account labels that name the bank feed, as a household with no feed reads them: its files are all the check
 *  compares the stored rows with */
const PER_ACCOUNT_NO_FEED: Record<string, string> = {
  "reconcile.store_vs_files": "Stored rows match the files",
};

/** kinds that are about the whole run */
const GLOBAL: Record<string, string> = {
  "answers.load": "Saved answers loaded",
  "answers.applied": "Answers applied",
  "answers.conflict": "Answers an override beat",
  "answers.expired": "Answers that no longer match",
  "answers.capacity": "Room left for one-off answers",
  "answers.on_split_parent": "Answers on a split purchase",
  "splits.sum_identity": "Split parts add up to their purchase",
  "splits.applied": "Split rules applied",
  "splits.unsplit": "Purchases a split rule left whole",
  "overrides.applied": "Overrides applied",
  "overrides.on_split_parent": "Overrides on a split purchase",
  "learned_rules.not_saved": "Learned rules not saved",
  "ai.applied": "AI answers applied",
  "taxonomy.unknown_reference": "Unknown category references",
  "categorization.uncategorized_share": "Share of spending uncategorized",
  "simplefin.snapshots": "Bank feed snapshots",
  "simplefin.ingested": "Bank feed ingested",
  "manual_assets.load": "Hand-entered assets not read",
  "manual_assets.entry": "A hand-entered asset left out",
  "manual_assets.debt_sign": "A debt stated above zero",
  "source.unmatched": "Files no account reads",
};

/** families whose tail names the thing: "learned_rules.<vendor>", "source.<file>", "overrides.entry_3" ... */
const FAMILIES: [prefix: string, label: (tail: string) => string][] = [
  ["learned_rules.", (t) => `Learned rule: ${t}`],
  ["overrides.entry_", (t) => `Override entry ${t}`],
  ["simplefin.unmapped.", () => "Unmapped bank account"],
  ["source.", (t) => `Source: ${t}`],
];

/** "answers.on_split_parent" -> "Answers · on split parent": the fallback for a check nobody labelled yet, never the raw code */
function humanize(name: string): string {
  const s = name.replace(/_/g, " ").split(".").join(" · ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export interface CheckAccount { id: string; name: string }

/** the label as one string, the account (when the check has one) after a middle dot: "Ledger vs feed balance · Everyday Checking" -
 *  how a list of checks across accounts (Data's quality report, Home's data-quality card) names a row */
export function checkLabel(name: string, accounts: readonly CheckAccount[] = [], feed = true): string {
  const t = checkText(name, accounts, feed);
  return t.account ? `${t.label} · ${t.account}` : t.label;
}
export interface CheckText {
  /** the words for the check */
  label: string;
  /** the account it is about, when its name carries one of `accounts`' ids */
  account: string | null;
}

/** A check's label (and its account, when it has one). `accounts` are the registry's ids and names: the id segment is lifted
 *  out of the check name to find the kind, and the account's own name comes back separately so a list can print it
 *  beside the label ("Ledger vs feed balance · Everyday Checking") and the account page can leave it out. `feed` false
 *  (manifest.setup.feed: the household has no bank feed): a label that would name the feed names only the files. */
export function checkText(name: string, accounts: readonly CheckAccount[] = [], feed = true): CheckText {
  // the account comes right after the prefix, so an id the tail happens to hold (a file named for another account)
  // never moves the check to that account
  for (const a of accounts) {
    for (const [prefix, label] of PER_ACCOUNT_FAMILIES) {
      const head = `${prefix}${a.id}.`;
      if (name.startsWith(head) && name.length > head.length) return { label: label(name.slice(head.length)), account: a.name };
    }
  }
  const parts = name.split(".");
  for (const a of accounts) {
    const at = parts.indexOf(a.id);
    if (at < 0) continue;
    const kind = [...parts.slice(0, at), ...parts.slice(at + 1)].join(".");
    if (!feed && kind in PER_ACCOUNT_NO_FEED) return { label: PER_ACCOUNT_NO_FEED[kind], account: a.name };
    if (kind in PER_ACCOUNT) return { label: PER_ACCOUNT[kind], account: a.name };
  }
  if (name in GLOBAL) return { label: GLOBAL[name], account: null };
  for (const [prefix, label] of FAMILIES) {
    if (name.startsWith(prefix) && name.length > prefix.length) return { label: label(name.slice(prefix.length)), account: null };
  }
  return { label: humanize(name), account: null };
}
