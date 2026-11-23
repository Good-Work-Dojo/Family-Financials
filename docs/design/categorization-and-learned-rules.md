# Categorization and learned rules

> Scope: how a transaction gets its category — rules, learned rules, review answers, overrides — and how to tune them.
> Describes the system as it is; verify every name against the code before relying on it (the module docstrings named
> here are the code truth and win over this page).

## Precedence (one row, first hit wins)

The full statement is the `etl/enrich/categorize.py` module docstring; the header of `starter/category_rules.yaml`, the
file a household's own copy starts from, summarises the order for a purchase. In short:

1. type-driven (internal money movement, income and fee defaults) — `internal` / `type_default`
2. a category a parser pre-set — `parser`
3. an explicit vendor rule `category:` in `config/vendor_rules.yaml` — `vendor_rule`
4. a learned vendor rule (`state/engine/learned_rules.json`) — `learned`
5. keyword rules in `config/category_rules.yaml`, stage `pre` → `default` → `post`, file order within a stage — `keyword`
6. the bank's own label (`config/bank_category_map.yaml`) when no keyword rule hit — `bank_label`
7. `Cash & Misc / Uncategorized`, plus `needs_review` at or above the review threshold — `fallback`

Then, in `Pipeline.enrich`: the AI step's stored answers, only while the family has switched it on, for fallback
purchases nothing above placed (`ai`, after the internal resync and before splits: `docs/design/ai.md`) → review
answers (vendor scope, then txn scope) → `config/overrides.yaml` (the family's
facts, highest precedence) → `answers.verify` (an answer an override beat becomes a Review "conflict"). The stamp in
backticks is the row's `category_source` (closed set `CATEGORY_SOURCES` in `etl/models.py`); it is provenance only and
never decides a category. `apply_set(..., source)` (answers and overrides) restamps only when the set touches
category or subcategory, so a retype-only or tags-only set keeps the row's provenance; a source outside the set raises
`ValueError`. The app's answers overlay stamps `answer` on the rows it previews.

## Where to edit

- `vendor_rules.yaml` `patterns`: regex → canonical vendor, optionally a `category:` (step 3), `learn: false`, or
  `split:` legs (`docs/design/splits.md`). Patterns match the UPPER-CASED cleaned descriptor (the raw one only when the
  cleanup leaves nothing) — `VendorNormalizer.normalize`.
- `category_rules.yaml` `keyword_rules`: legacy `{ pattern, category, subcategory?, tags?, needs_review? }` entries
  (stage `default`) or structured `{ name, stage, all|any: [{ field, op, value }], set }` entries. Fields and ops are
  `FIELDS` / `OPS` in `etl/enrich/rules.py`; `amount` values are dollars, compared in cents. A keyword rule matches the
  cleaned descriptor OR the raw one. The P2P catch-all stays last in `default` (the load refuses a rule after it in
  its stage: `rules.last_in_stage_problems`); a `pre` rule outranks it.
- `config/overrides.yaml`: per-transaction family facts. An override that stops matching warns
  (`overrides.entry_<i>`) instead of disappearing. One that changes a row's `type` and names no category files the
  row as its new type does (`overrides.resync_retyped`): Internal/<type> (`internal`) for an internal type; the type's
  default for a row that leaves the internal types or becomes income (Income or Fees & Interest, `type_default`; the
  fallback's Uncategorized for a purchase, `fallback`), so type and category never disagree. A `type` the row has
  already (an override naming an income row's `earner`, say) leaves it where it is filed.
- Load-time validation (`Config._validate_rules`, the checks in `etl/validate.py`) refuses the ingest on an unknown
  key, a bad field / op / stage / category, a `type` that is no transaction type, an unknown `owner` or account id, a
  `stage:` / `name:` on a vendor pattern, or a `split:` on a keyword rule, every bad entry reported at once. The
  Internal category is refused on a keyword rule and a vendor pattern (a rule sets no type, so the row would still
  count) and on an override whose `type` is not internal: the type takes a row out of spending and income.

**Vendor rules outrank the bank's label**, so an over-broad merchant pattern silently overrides answers that were
right and can strip `needs_review` off a P2P row the catch-all would have queued. Anchor short or common-word patterns
and pin each with a negative case (the starter list's look-alikes: `tests/golden/starter_cases.yaml`;
`test_every_pattern_is_anchored`). Failing safe to Uncategorized beats a confident wrong answer.

**Fallback vendor** (no pattern hit): the first four words of the cleaned descriptor, a trailing joining dash dropped;
an all-caps result is title-cased by `smart_title` (the `ACRONYMS` allowlist stays upper-case, `WHOLE_WORD_ONLY` only
as a whole word), a mixed-case one is kept as written.

## Trying a rule before it lands

`python -m etl rules preview --file CANDIDATES.yaml` enriches twice (as the rulebook stands, then with the candidates
appended at their stage) and prints each candidate's effective hits and the `(old → new): n` matrix;
`python -m etl rules overlaps` lists keyword rules with zero hits and rules every match of which an earlier rule takes.
A candidate whose rule name (explicit or automatic) collides with a live keyword rule or an earlier candidate is
refused before anything runs. Both are offline and write nothing; the `etl/rules_cli.py` docstring says exactly what each counts. Split purchases are
judged by their parent (the row the keyword scan saw). In an install they are `./ff rules preview` and
`./ff rules overlaps` (the README's "Tuning the rules").

## Transfers between the household's own accounts

A row that is money moving between two of the household's accounts is typed `transfer`, `cc-payment` or `investment`
and so counts as neither spending nor income. `etl/enrich/transfers.py` (its module docstring is the spec) does it in
phases. Phase A types the rows a `transfer_rules.yaml` rule matches; phase B pairs the rule-typed and payment-app
rows. A pair with a payment-app or Zelle leg that no transfer rule typed is claimed only when each leg is the other's
only rival (the generic matcher's count, below), so a payment out on one adult's account and an unrelated receipt of
the same amount on another's are no transfer; `venmo_patterns.pairing: nearest` takes the best-hinted, then closest,
match instead. A rule scoped by
`accounts:` keeps its row internal whether or not a pair is found (an unpaired leg is a data gap in
`open_questions.json`). A rule scoped by `kinds:` names no account, so its row stays internal only once paired: an
unpaired one is put back exactly as it would be without the rule (the income and category stages run over it again) —
except a payment a card received (a credit on a card typed `cc-payment`), which is never a refund: it stays internal,
and the generic matcher may still pair it (below).

In short: a transfer or card payment is kept out of spending and income when both sides are in your data and pair. A
payment received on a card is always kept out, and so is an `accounts:` rule's leg (a data gap when unpaired). Any
other side that finds no partner stays an ordinary row: flagged
for review when a `kinds:` rule recognised it or when it is uncategorised above the review thresholds; counted
without a flag when one of your category rules, the bank's own label or a learned rule filed it.

Then the **generic matcher** pairs, with no rule at all, two rows nothing explained. On the way out and in alike that
leaves only rows with `category_source` fallback and no review-inbox answer (a txn answer naming the row, or a vendor
answer naming its vendor: the family files those rows, and the answers apply after the matcher): a purchase the
categorizer left to its fallback, and a credit the income fallback typed that the categorizer would leave there too
(such a credit, at or above `unknown_credit_review_threshold`, is typed before the categorizer runs, so the matcher
asks the categorizer what it would give the row as an ordinary purchase — `Categorizer.places`, the same steps 3–7
the purchase path applies). Never a row a keyword, vendor or learned rule, a bank label or a parser filed (spending
or a refund at a named merchant), nor a credit an income rule recognised; and a vendor the family has answered for
keeps that vendor's unexplained credits out of the matcher too. Such a row still counts as a rival. One rule-typed row
is a candidate too: a payment a card received that a `kinds:` rule typed and phase B left unpaired — a transfer rule
recognised it, a stronger reason than nothing explaining it. As the card inflow it pairs only with a cash account's
outflow that is itself an ordinary candidate, under the same uniqueness, and the pair is a `cc-payment`; unpaired, it
stays internal. The two
must be exactly opposite amounts on different accounts, at most `match_window_bdays` business
days apart, at least `generic_min_amount` dollars (`assumptions.yaml`, default 20.00), cash to cash (both `transfer`)
or a cash outflow with a card inflow (both `cc-payment`) and nothing else, and **unique**: each is the other's only
rival, a rival being any row not already paired, of any type and on any other account, with the opposite amount in the
window. A second rival on either side, a candidate or not, and neither pairs — a transfer left unpaired stays
visible, a purchase called a transfer would vanish from spending. A released `kinds:` row the matcher does not pair
either goes to review. A split vendor's rows are never paired. `transfer_rules.yaml` `generic_matching: false` turns
the matcher off for a household whose own rules already name every transfer.

## Learned rules

The code truth is the `etl/enrich/learned.py` module docstring. The rules:

- **Evidence**: valid answers that name a category (a tags-only answer teaches nothing) with a key and `answered_at`;
  a txn answer counts only when its row is a non-internal purchase with a vendor. Vendors group by
  `vendor.casefold()`.
- **Promotion**: 3 of the vendor's 5 newest txn answers agree on a (category, subcategory) pair and the pair is not
  disputed (fewer than 2 of the newest 3 name something else), so a rule is never promoted only to be demoted by the
  next ingest. A vendor-scope answer ("always for this vendor") promotes on its own (the store never evicts one); txn
  answers for that vendor are per-row exceptions, never evidence against it.
- **Demotion** wins over promotion: a txn-sourced rule whose pair is disputed is removed; a vendor-scope rule changes
  only with a newer vendor-scope answer.
- **Sticky**: a prior rule with no evidence this pass survives unchanged (one-off answers are evicted past 500).
- **Guards**: an excluded vendor (`learning.exclude`, `learn: false`, or a `split:` pattern) is skipped silently and a
  prior rule for it is `superseded`; an explicit `category:` on the vendor is never learned over (one
  `learned_rules.conflict` warn when the answers name a different category); a pair whose subcategory is outside
  `taxonomy[category]` is not promoted (`learned_rules.off_taxonomy`); a vendor rename that folds two stored rules
  keeps one (`learned_rules.rename_collision`).
- **Effect**: a learned hit sets category and subcategory only. It keeps the tags the keyword rule (else the bank
  label) would have added, never touches `needs_review`, never sets `type`, and never outranks an explicit vendor
  rule or an override.
- **Visibility**: `learned_rules.active` (info) in the quality report, the `learned` chip in the app, the Data page's
  learned-rules card, and `vendors.json` (`category_source`, `learn`). Per-pass events go to the ingest summary and the
  file's `history` (the newest `HISTORY_CAP` kept), never to an export.
- **Persistence**: `persist_learned` writes the file after `finalize_quality`, only when no check failed
  (`learned_rules.not_saved` names up to ten failing checks), only when the rules changed. An unreadable file is never
  written over (`learned_rules.write_failed`); a corrupt or partly malformed one is first copied to
  `learned_rules.json.corrupt`. No wall clock in `rules[]`, so two ingests of the same inputs write the same doc.
  The family's documents are loaded so that no file can stop an ingest: the learned rules, the review answers
  (`answers.load`) and the manual assets (`manual_assets.load`) each read a file that is not JSON, nested past the
  parser, or holding text that is not valid Unicode (a lone surrogate, which the database refuses:
  `validate.encodes_as_utf8`) as corrupt — the empty document and one warning.

Known, intended effect: `_finalize_venmo` (`etl/enrich/transfers.py`) gives a P2P outflow labelled `Cash & Misc` the
"Venmo/Zelle Unknown" + review treatment unless the label already names the purpose (`_names_the_p2p_purpose`): a
learned or explicit vendor rule set it (any Cash & Misc subcategory), or a keyword rule set a specific one (Legal, ATM,
Checks, Wire, Services, Prepaid Card — not the P2P catch-all's Venmo/Zelle Unknown, nor Uncategorized), which keeps its
category, provenance and review flag. So a learned category on a P2P payee (a `Cash & Misc` one included) takes those
rows out of the Review inbox.

**Undoing a rule** (never hand-edit the file): a newer vendor-scope answer replaces it; 2 of the next 3 txn answers
disagreeing demotes a txn-sourced rule; `learn: false` on its pattern (or `learning.exclude`) blocks it and drops it;
an explicit `category:` on the vendor rule supersedes it.

**Generic payment descriptors are not learnable**: the last `learn: false` entries in `vendor_rules.yaml` cover the
families that collapse many payments into one fallback vendor — bare checks, withdrawals, `WT` wires and
QuickPay-with-Zelle (the payee already dropped by the fallback's four-word cut) — and the credits the banks label
generically: remote online deposits, mobile-deposit references, incoming real-time transfers (the sender cut off the
same way) and card statement credits. They matter beyond the learner too: the app's category editor starts on
"Always for <vendor>" only for a learnable vendor. Each entry's `vendor:` is exactly
the fallback string for the upper-case descriptors the banks send, and it carries no `category:`. A longer form the
cleanup leaves distinct ("Check" plus more words) is its own one-row vendor and stays learnable: it cannot collapse,
so three agreeing answers cannot accrue on it. P2P payee vendors stay learnable on purpose.

## Review answers (ETL side)

`etl/enrich/answers.py` (docstring): `set` is whitelisted to category / subcategory / tags / needs_review — an answer
never retypes a row. Vendor scope applies first, then txn scope; `overrides.yaml` runs after both. A txn answer whose
row vanished expires silently with one `answers.expired` info. The store keeps `MAX_ANSWERS` (500, the same constant in
`deploy/scenario_store.py` and `app/src/lib/answers.ts`) one-off (txn) answers, evicting the oldest past that;
vendor-scope answers are never evicted and do not count (`docs/design/data-and-sync.md`). `answers.capacity` (info)
counts one-off answers only and appears from 90% of the cap. Answers on split purchases:
`docs/design/splits.md`. The app side (Review inbox, bulk editor, merge rules): `docs/design/register-and-answers.md`.
