# Starter files

The rule files a new household starts from. Copy them into the household's `config/` directory, next to the two files
that describe the household itself (`household.yaml` and `accounts.yaml`), and edit them there. The engine reads only
`config/`; nothing reads this directory, so a change here reaches no household that has already copied it.

| File | What it holds |
|---|---|
| `category_rules.yaml` | The taxonomy (categories and their subcategories), keyword rules (one to start: the catch-all that sends unknown person-to-person payments to review), income rules and category renames. |
| `vendor_rules.yaml` | How a bank descriptor becomes a vendor name: payment-processor prefixes to remove, cleanup patterns (store and phone numbers, long digit runs, trailing dates, bank and card metadata), the words kept upper case (`acronyms:`), and a list of national US merchants written the way card networks print them (`patterns:`), each filed under a category of the taxonomy except the stores whose baskets span several categories (a warehouse club, a big-box or convenience store, a marketplace), which only name the vendor and are left out of learning (`learning.exclude`); Amazon alone of those is filed, under its own Shopping subcategory, and is left out of learning too. `tests/golden/starter_cases.yaml` holds, for every pattern, an invented descriptor it is the first match of, and look-alikes no pattern may catch. |
| `bank_category_map.yaml` | Card issuers' own category labels, each pointed at a category of the taxonomy. |
| `transfer_rules.yaml` | Which rows are money moving between your own accounts: the payment apps' person-to-person patterns, rules by account kind for card payments and for transfers between checking and savings, and empty `groups:` and `outside:` lists for counter hints of your own. |
| `assumptions.yaml` | The engine's thresholds (transfer-matching windows, review thresholds, the share of accounts a month needs before it counts) and the `projections:` the dashboard starts from (returns, inflation, withdrawal rate, tax rates). |

Every pattern is a regular expression matched with case ignored. Each file explains its own blocks in comments.

## After an upgrade

A newer release may add entries to these files (a merchant, a category, a cleanup pattern); your copies never gain
them by themselves. `python -m etl rules starter-diff` lists what the starter files hold that yours lack, one line per
entry as `starter/<file>: <section>: <label>`, and changes nothing. It compares by name (a vendor, a category, a rule,
a bank label, a pattern's text), so an entry you edited is not listed, one you removed is listed again, and a vendor or
category you renamed counts as present. Copy over the entries you want; `python -m etl validate` then checks the result.

## Extending them

- **A vendor**: add an entry to `patterns:` in `vendor_rules.yaml`, such as
  `- {pattern: '\bACME COFFEE\b', vendor: Acme Coffee, category: Dining, subcategory: Coffee}`. Patterns are tried in
  order and the first match wins; a pattern's `category:` outranks every keyword rule, so anchor short or common-word
  patterns.
- **A keyword rule**: add it to `keyword_rules:` in `category_rules.yaml`, above the person-to-person catch-all, which
  stays the last rule of the `default` stage. A rule is `{name, stage, any: [...] | all: [...], set: {category,
  subcategory}}`; each condition is `{field, op, value}`.
- **A transfer rule**: add it to `rules:` in `transfer_rules.yaml`, scoped by `kinds:` or by `accounts:` (exactly
  one), with a `type` and, when you know where the other leg lives, a `counter_hint`. Rules are tried in order; a rule
  that matches a real purchase hides that spending, so anchor it.
- **A bank label**: add `<label as the export spells it>: {category: ..., subcategory: ...}` to
  `bank_category_map.yaml`.
- **A category**: add it (or a subcategory) to `taxonomy:` before any rule names it; a rule naming a category the
  taxonomy lacks stops the load. Keep the names the engine files rows under: Cash & Misc with Uncategorized and
  Venmo/Zelle Unknown, Internal, Income with Other, and Fees & Interest.
- **Noise in your bank's descriptors**: add a regular expression to `cleanup:` in `vendor_rules.yaml`, or a
  processor's tag to `strip_prefixes:`.
