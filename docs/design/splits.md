# Rule-driven splits

> Scope: how a mixed-basket vendor's purchases are split into categorized parts, which totals read which side, and
> how the app shows them. Describes the system as it is; verify names against the code (`etl/enrich/splits.py` and
> `app/src/lib/splits.ts` docstrings are the code truth).

Splits are rule-driven only: there is no manual split editor (an owner decision).

## The rule

A pattern in the household's `vendor_rules.yaml` gets a `split:` list of legs and, by convention, a `note:` saying how
the legs were chosen. The demo household's rule (`tools/demo/behaviours/review.py` writes it):

```yaml
  - pattern: '^HARBOR WHOLESALE CLUB\b'
    vendor: Harbor Wholesale Club
    category: Groceries
    note: 'an estimate of the usual basket: tune the share from a few receipts, or delete the split'
    split:
      - { pct: 70, category: Groceries }                                  # a share (a fixed leg is amount:, dollars)
      - { remainder: true, category: Shopping, subcategory: Home Goods }    # the one leg that takes the rest
```

Validated at load (`compile_split_rules`, via `Config._validate_rules`; the compiled `Config.split_rules` is what
`Pipeline.enrich` hands to `splits.apply`): at least 2 legs, each exactly one of `pct` / `amount` / `remainder`,
exactly one remainder, every pct above 0 and their sum below 100, amounts above 0, categories in the taxonomy.
`split:` is a vendor-pattern key only. Vendors match casefolded.

**Worked through on the demo.** Harbor Wholesale Club is an invented warehouse club on Alex's card: one basket mixes
food with household goods, and the card row carries only its total. The pattern names the vendor and files it under
Groceries, which the parent keeps for display; the split sends 70 percent of each purchase, in whole cents, to
Groceries and whatever is left to Shopping / Home Goods. Its first purchase in `full`, −$87.43, becomes parts of
−$61.20 and −$26.23, which add back to the parent to the cent; over the profile's 28 months the club's twelve
purchases make twelve parents and twenty-four parts. The share is a guess at a typical basket, not a measurement:
a household with receipts sets its own `pct:`, and one that drops the `split:` list (with its `note:`) sees every
purchase filed whole under Groceries from the next ingest on. `truth.json` states each part from the generator's
ledger, and `tests/test_demo_review.py` compares the exports with it.

## Parents and parts

Every external spend row of the vendor (`type` in `SPEND_TYPES`, not internal) becomes a PARENT — kept, `is_parent`,
its category display-only, `needs_review` cleared, in no sum — plus one PART per leg: `parent_id`, `category_source:
split`, `needs_review` false, no transfer fields, date / account / description / vendor / type / tags copied. Amounts
are rounded cents and the remainder leg takes what is left, so the parts sum to the parent exactly. A refund splits
into positive parts; a row too small for its legs stays whole. A part's uid derives from its parent's uid and its leg
position (`split_index`, 1-based).

`splits.apply` runs in `Pipeline.enrich` after every stage that pairs or retypes rows and before the answers, so a
part is never a transfer-pairing candidate. `splits.sum_identity` (in `finalize_quality`) **fails** when a parent
differs from the sum of its parts, has fewer than 2, or a part names no parent; the gate's `splits` export-diff policy
refuses the same.

## Every total counts a split purchase once

The parts carry the money. Parents are excluded from every money sum and money-row tally: the store-vs-files
reconciliation, `reconcile.account_month_sums`, `uncategorized_share`, `unmapped_vendors`, `ledger_drift_inputs`,
`export_cashflow_monthly` (with `by_category`), the ingest summary's spend and top vendors, and the two row counts
`finance.db` `accounts.txn_count` and `manifest.transaction_count` (`splits.count_rows`). Those two count MONEY ROWS —
parts and ordinary rows, never purchases: a two-part purchase plus one plain row counts 3.

Three readers do the opposite and skip the PARTS: `recurring.detect` (a subscription is one series, so a split
vendor's series in `recurring.json` carries the parent's display-only category), `vendor_registry.build` (one row per
purchase) and `rules_cli` (the parent is the row the keyword scan saw). Two tallies keep parents AND parts:
`transactions.json` `count` (`test_e2e` pins `count == len`) and the summary's `transactions:` line. `finance.db`
stores both, so ad-hoc SQL sums filter `is_parent = 0`.

A new aggregate decides which side it is on and gets a "counted once" test in `tests/test_splits.py`. Known gap:
the store-vs-files and month sums read the parts, and a change that summed the parents instead would go unnoticed
while `splits.sum_identity` holds, because the totals are equal.

## Answers, overrides and learning

- A vendor-scope answer never reaches a part (ETL `_answerable`, app `applyAnswers`) — the rule decides the legs; that
  includes a vendor-scope tags-only answer, so vendor tags do not reach parts. A txn answer applies to a part
  like any row.
- A part's answer follows its LEG POSITION: the part's uid is built from the parent and the leg index, so reordering
  or changing the legs moves an existing part answer to whatever leg now sits at that position. `answers.expired`
  says that parts do not reappear in the Review inbox (a part never has `needs_review`).
- An answer or override that hits a PARENT is display-only and logged: `answers.on_split_parent` warns when the answer
  sets a category or subcategory; `overrides.on_split_parent` warns when a category-setting entry reached no part of
  that parent; a `type` in the set is a retype and never warns. A `vendor` / `contains` / `account` / `date` override
  also repaints the parts (an explicit override beats the split); a `uid` / `amount` override reaches the parent alone.
- `answers.verify` ignores parents and still reports a conflict on a part.
- An override that retypes a parent out of spending un-splits it (`splits.unsplit_retyped`, logged as
  `splits.unsplit`), restoring its review flag unless an answer or override set one.
- A split vendor is never learnable (silently: `vendors.json` `learn: false`, no `learned_rules.conflict`); a learned
  vendor that gains `split:` loses its rule (`superseded`).

## The app: two views

`loadAppData` partitions `transactions.json` once (`partitionSplits`, `app/src/lib/splits.ts`):

- `AppData.transactions` — the CATEGORY view: parts and ordinary rows, never a parent. Every `lib/*` sum, the Review
  inbox, budgets and `applyFilters` read it.
- `AppData.purchases` — the VENDOR view: parents at their full amount and ordinary rows, never a part.
- `AppData.splitParents` — the parents by id; `Indexes.childrenByParent` — the parts by parent.

The answers overlay runs on the category view; `overlayPurchases` mirrors it into the vendor view (a parent is never
overlaid). **Rule for a consumer:** it sums money or judges a category → `transactions`; it lists or counts purchases →
`purchases`, or `purchaseKey` / `purchaseCount` over category-view rows (sums stay on the parts). `reportInput`
(`lib/reportBuilder.ts`) is the one place that picks a view per request: the vendor view for a vendor group-by whose
filters name no category, tag, amount bound or review flag, else the category view.

- **Register** (`components/TxnTable.tsx`): `regroupSplits` shows a parent in place of its parts only when every part
  passed the filter (`split · N parts`, the `split` chip, expandable to the parts and the rule); otherwise each part
  shows as `split of <vendor>`. A parent has no checkbox or Edit (`editable()`). The CSV lists the parts with
  `parent_id`; the running balance nets each day over the parts; an account page's title counts transactions, a split
  purchase once.
- **Footnotes**: `splitRules` summarises the rules in force from the parents and parts (shares computed from the parts,
  after the overlay); `splitNote` and `components/SplitNote.tsx` print one line on a page that shows a category holding
  a leg (no amounts, shares only). Data lists the rules in `card-split-rules` (a `when: "data"` row: it exists only
  while a split rule is in force).
- **Counts**: a count of rows of every type, a split purchase once, names its unit "transactions" — the same word as
  the sidebar's total, since it holds income, interest and investment rows as well as purchases. The FilterBar reads "N
  of M transactions" (Explore's "view these N transactions" link lands on exactly those rows); the register's CSV
  button counts the rows it writes, parts included, and names the same N transactions beside them when the two differ
  ("Export CSV: K rows (N transactions)"), so the two numbers are never read as a mismatch.
- **Known limit**: the amount filter judges parts, so a purchase can show partly, or vanish whole when every part is
  below the minimum.
- An open tab on a bundle older than the export's schema would read parents as ordinary rows; the update banner
  (`docs/design/data-and-sync.md`) asks it to reload.
