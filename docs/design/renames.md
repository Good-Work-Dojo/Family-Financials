# Vendor and category renames

> Scope: renaming a vendor or a category in config so every stored reference follows, ETL and app halves. Describes
> the system as it is; verify names against the code (the `etl/enrich/renames.py` module docstring is the code truth).

**Renames are config, never a rewrite of stored data.** `state/family/answers.json` and `config/overrides.yaml` are never
rewritten; the renamed view lives in memory for one ingest. The one file that ends up renamed is
`state/engine/learned_rules.json`, which `persist_learned` writes with the renamed names.

## Vendor renames — `vendor_rules.yaml` `renames: [ { from, to } ]`

- `Config` applies them at load to `assumptions.cancelled_subscriptions` and `learning.exclude`; `Pipeline.enrich`
  applies them to the answers doc right after `answers.load` (the `vendor` field and the `vendor:<name>` key) and to
  the prior learned doc (`VendorRenamer.apply_to_learned`).
- A `from:` matches casefolded, so a case-only rename is valid (it re-spells every case variant to `to:`), and two
  `from:` names that differ only by case are one duplicate.
- The load rejects a `from:` that is a current `patterns` vendor (pattern rows keep emitting their own spelling, which
  would orphan the references): reword that pattern's `vendor:` instead.
- A rename never rewrites a stored transaction's vendor — only the references to it.
- Two stored learned rules that a rename folds into one vendor keep one (`learned_rules.rename_collision`).

## Category renames — `category_rules.yaml` `renames: [ { from: {category, subcategory?}, to: {category, subcategory?} } ]`

- A `from` without a subcategory moves the whole category (each reference keeps its own subcategory unless `to` names
  one); a `from` with a subcategory moves exactly that pair. `to:` must already be in the taxonomy.
- A `from` naming a reserved name (`etl/models.py` `RESERVED`: a reserved category, or a reserved subcategory under its
  category) is refused at load: the engine goes on filing rows under it.
- The ETL applies them at load to answers' `set`, `overrides.yaml` `set` blocks and the prior learned rules, and
  exports the list as `assumptions.json` `category_renames` (`exports.export_category_renames`).
- The app mirrors the map: `lib/categoryRenames.ts` `mapCategory` (a mirror of `CategoryRenamer.map`) and
  `mapCategoryName` (the whole-category form for a budget key or a view's `categories=`). `DataContext` runs the stored
  answers through `renameAnswersDoc` before the overlay; `resolveAssumptions` (`lib/prefs.ts`) moves both the YAML and
  the in-app budgets through `renameBudgets`; saved views and reports are mapped when validated.
- The app's own category names live in `lib/categories.ts` and answer in the CURRENT spelling (`categoryVocab`, both
  spellings match — `docs/design/figures-and-vocabulary.md`).

## Shared rules

- The list applies once, in order: first match wins and a `to` is not renamed again. A chain or a cycle (an entry
  whose result another entry renames again) is refused at load, for vendors and categories alike, naming both entries
  (`CategoryRenamer._refuse_chains`): A→B plus B→C would leave A-answers at B while the learned rules stepped one hop
  per ingest, so write A→C and B→C.
- The check is conservative: a whole-category rename onto a pair of the same category (A → A/x) plus another rename
  onto that pair (B → A/x) is refused although applying both would be harmless. It fails safe; write one of them.
- A stored categorization whose category is still outside the taxonomy after the renames warns once
  (`taxonomy.unknown_reference`, keys and vendor names only). Subcategories are free text, so an unseeded one is fine.

## What does not follow a rename

Keyword rules, `vendor_rules.yaml` itself and the taxonomy (edit those by hand); the device-local `efund-overrides`
keys (a renamed category's stored essential toggle is orphaned and the row falls back to its default); and the names
the ETL emits itself (an unedited keyword rule; the reserved names cannot be renamed at all) — the app tolerates those
by matching both spellings.
