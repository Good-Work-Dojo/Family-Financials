# The register, review answers, saved views and reports

> Scope: the transactions register (`TxnTable`), the Review inbox and the register's category editor, how answers
> merge, and saved views and reports. Describes the system as it is; verify names against the code. Paths are
> relative to `app/src/`. The answers' ETL semantics: `docs/design/categorization-and-learned-rules.md`; the store and
> its sync: `docs/design/data-and-sync.md`.

## The register (`components/TxnTable.tsx`)

- **Layouts** (`useRegisterLayout`) — an exception to the `md` phone cut, because the register's content area is the
  viewport minus the 208px sidebar: stacked rows (vendor + amount, then date / category / account, a Sort-by select, no
  header row) below `lg`; a compact grid without Owner and Type from `lg` to `xl`; the full grid from `xl`. Folded
  columns stay readable in the open row. The grid template is an inline style (a Tailwind class built at runtime is
  never generated); `WIDTHS` / `COMPACT` are the one source of column widths, and the `select` and `balance` columns
  exist only with the `selection` / `runningBalance` props. Virtualizer measurements are keyed by layout.
- **A phone** (below `md`, inside the stacked layout): no body scroller and no `maxHeight` — the list scrolls with the
  page, its lines wrap at a word instead of truncating, and it pages instead of virtualising: `PHONE_PAGE` (50) rows,
  then a "Show N more" button (`register-more`, named "Show N more transactions"). A count line above it
  (`register-count`) says "N transactions", or "Showing K of N transactions", counted as the filter bar counts (a split
  purchase once). A different list or sort starts again at its first page; the keys move among the rows shown.
- **Sort**: controlled on Transactions — `sort` and `onSortChange`, both or neither — where it lives in `?sort=`
  (column ids `TXN_COLUMN_IDS`); elsewhere the register keeps its own. The Category column sorts by the displayed
  category (a split parent included). Under 11 rows TanStack's automatic sort is its case-sensitive `basic` one.
- **Dated ahead**: a row dated after the ingest day (`lib/agg.ts` `datedAhead`) is a real row, so the register lists it,
  with a "dated ahead" text chip beside its date (its title, and the open row in words, say the bank dated it after the
  last update). A sort by date, the default newest-first one included, is by the date shown, so such a row leads a
  newest-first register, marked.
  The account page's "Transactions through" stops at the ingest day and names such rows on a line of their own.
- **Rows**: tag chips; the Account cell is an `AccountLink` with `appearance="quiet"` (grey until hover or focus) and
  `tabIndex={-1}` — a pointer convenience, so the register stays one tab stop. The keyboard's and a phone's way to the
  account is the "Account page →" link in the open row (absent on an account's own page). A pixel `maxHeight` is cut to
  whole rows. With no rows the body is one empty-state row (`register-empty`, with a Clear button via `onClear`) and
  "Export CSV" is disabled; otherwise the button counts the rows the file holds ("Export CSV: K rows"), and the
  transactions they are when a split purchase's parts make the two differ ("K rows (N transactions)": the filter bar's
  N, a split purchase once).
  Split purchases: `docs/design/splits.md`.
- **Running balance** (`lib/runningBalance.ts`, on `/accounts/:id`): checking, savings, credit-card and loan accounts,
  whatever class they state, and a brokerage's Cash-class account only (a brokerage moves with the market). It is the END-OF-DAY balance keyed by date, anchored on the newest observation (a
  live feed's wins a same-day tie), in integer cents — the export's order within a day is arbitrary, so no per-row
  number would be honest. A day's figure sits on that day's row nearest the later days, and only while sorted by date.
- **The per-account page** (`/accounts/:id`, `sections/AccountPage.tsx`): the account's feed-status chips
  (`checksFor`), its balance history, the ledger-drift card (in cents; when `ledger_drift` is null it says why — the
  account is not a checking, savings or credit-card account with a feed and two observations at least 7 days apart),
  and the shared register with `hideAccount` and the running balance; its title counts transactions, a split purchase once. `Balances` rows link
  in, as does every printed account name.

## Keyboard and screen readers

The register is an ARIA table whose rows are numbered `aria-rowindex` (the open row's detail counts; no header row on a
phone) and **one tab stop** (roving tabindex): ArrowUp/Down, Home/End and PageUp/PageDown move the active row (not with
Ctrl/Meta/Alt held), Enter/Space open it, and `x` toggles its selection on a register with a selection column.
`rangeExtractor` keeps the active row rendered wherever the list is scrolled; a move scrolls the virtualizer before
focusing; the stop returns to row 0 only when the list's content signature (length, first and last id, sort) changes —
never on a new array holding the same rows (the answers overlay hands one over after each save). Selection checkboxes
are `tabIndex={-1}`; the header's select-all is the keyboard's way to select several, and a selectable row says
`aria-selected`. A finger never sees a `title`, so the open row repeats the category chip's tooltip in words. Other
overlays follow `app/AGENTS.md` (dialogs, `useFocusTrap`, no nested tab stop).

## Answers from the app

- **Writers** (`data/useAnswersStore.ts`): `answerTxn`, `answerVendor` and `answerTxns(ids, set, note?)` (the bulk path)
  share one functional commit, hence one debounced PUT. `lib/useRowSelection.ts` caps a selection at `SELECTION_CAP`
  (100): every txn answer is its own entry and the store keeps `MAX_ANSWERS` one-off answers, so a big sweep would
  push out the older ones the learned rules replay; the bulk bar says how many slots a save takes and how many of the
  oldest one-off answers it would drop. An "Always for a vendor" answer takes no slot and is never dropped.
  `Transactions.tsx` prunes the selection to the displayed answerable rows (`editable()`); a `bulk-saved` live region
  announces a save, and says "in this browser only" instead of "permanent at the next data refresh" while the store
  refuses answers saves (the register's alert says why, `docs/design/data-and-sync.md`).
- **Editor** (`components/CategoryEditor.tsx`): takes one row or the selection, refuses a selection mixing income and
  spend rows, and offers the vendor scope only when every row is a spend row, shares a vendor and none is a split part
  (`offeredVendor`, `lib/vendorScope.ts` — the one predicate behind the chip and its default). It **starts on "Always
  for <vendor>"** wherever that scope is offered and vendors.json marks the vendor learnable (`defaultScope`), because
  the family's habit is to file a vendor once for good; it starts on "Just this transaction" / "Just these rows" for a
  vendor kept out of learning (`learn: false` generic descriptors such as Check or Withdrawal, `learning.exclude`
  mixed baskets — an "Always" answer would file every different payment behind the name alike, and the editor says
  so), for one vendors.json does not list, and wherever the vendor scope is not offered. A click is a choice for that
  vendor only: a selection that moves to another vendor starts on its own default. The Review inbox, the register's
  row editor and the bulk bar share it (the bar counts the one vendor answer from the moment the editor opens). An
  income row writes a category pair only once a stream is picked, else `{ tags }`.
- **Merging** (`mergeAnswer`, `lib/answers.ts`): for one key, the keys the new set names win, tags union, and a
  category arrives with its own subcategory — a category save keeps an earlier answer's tags, a tags-only save keeps
  the category. A **tags-only answer sends `{ tags }` and nothing else**: it cannot move or pin the category, restamp
  provenance or teach the learned rules (pinned in `tests/test_answers.py` and `tests/test_learned*.py`). Merged over a
  stored answer that carries a category, it is stamped one tick after the prior answer (`nextStamp`: +1 ms, +1 s for a
  legacy stamp without milliseconds), which keeps that answer's place in the learned window and still beats a stale
  tab's copy.
- **The overlay** (`applyAnswers`) previews answers instantly on the category view, mirrors the ETL (`_answerable`: a
  vendor answer never reaches a split part) and stamps `answer`; the ETL applies them on the next ingest.

## The Review inbox (`sections/Review.tsx`)

Rows the overlay still leaves for review (`isReviewCandidate`). The pills partition the inbox (`groupOf`: Venmo &
Zelle, Cash & checks, Deposits, Other — they sum to All). Fifty rows show at a time ("show 50 more", `review-more`); an
answered row leaves the list, and the "Saved" toast lives in an always-mounted status region. Conflicts are answers a
config override beat (`ownerKey`
picks the answer that owns a row). Undo (`unanswer`) restores the answer the save replaced; undoing a FIRST answer
reverts once the store's echo returns it (no tombstones). Two devices tagging the same row at once: the store keeps
the newer answer per key, so the other device's tags are lost.

## Saved views and saved reports

Two lists in the prefs doc: `saved_filters` (`{ id, name, query, created_at }`, the Transactions tab's serialized
filters) and `reports` (`{ id, name, spec, created_at }`, a `ReportSpec` whose `filters` is the same query string). One
generic layer in `lib/prefs.ts` serves both: `SavedKey` names a list, `normalizeSaved` parses defensively (a malformed
or oversize entry is dropped with a warning, a duplicate id silently), and `tryWithSaved` / `tryWithRenamedSaved` /
`withoutSaved` return the new doc or a `SavedRefusal` that the Save box prints BEFORE the click (`saveRefusalText`;
`PREVIEW_ID` / `PREVIEW_STAMP` let the preview measure a real item without reading the clock). A new list kind is a
`PrefsDoc` section plus a payload parser. One Save box serves both (`components/SaveNameBox.tsx`); "Save view" is
offered only for something the user chose (`withoutAutoWindow`), not on a bare fresh visit; Reports hides it
(`canSaveView={false}`) and offers "Save report" instead.

- **Rolling windows**: a view or report saved on a preset window (3M, 6M, 12M, YTD — the automatic six months
  included) is stored as `win=<preset>` (`savedFilterQuery` / `savedQuery`; `WINDOW_PARAM`, `SAVED_QUERY_KEYS`) and
  resolved against the day the data reaches (`Indexes.dataThrough`) when opened, so "last 6 months" stays six months.
  Dates picked by hand are
  saved as the exact days (a hand-typed start equal to a preset's start is indistinguishable and saved as rolling);
  All saves no window.
- **Stale** (`validSavedFilters` / `validSavedReports`, a renamed category mapped first): an item naming a category
  that left the data, or a key this bundle does not know, stays in the doc (another device may still know it), is
  named on Data with its reason, opens as saved (`staleOpenPath`) and is offered nowhere else. An older bundle calls a
  `win` item stale until reloaded.
- Every link to a saved item (the FilterBar menu, ⌘K, Data, the Reports menu) passes `state={CARRIED_SEARCH}`, so an
  item saved without a date window opens all-time.
