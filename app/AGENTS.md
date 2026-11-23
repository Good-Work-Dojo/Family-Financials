# app/AGENTS.md — dashboard (Vite + React + TS + Tailwind v4 + ECharts)

The dashboard's map; the repo-root `AGENTS.md` still applies. Bare paths (`components/…`, `lib/…`, `App.tsx`) are relative to `app/src/`. Mechanics live in
`docs/design/*.md` (index at the end).

## Hard rules
- **Never hand-format money.** Every amount goes through the masked formatters in `lib/format.ts` (`fmtUSD`,
  `fmtUSD0`, `fmtCompact`, `fmtDelta` and those built on them): no `toFixed(2)`, Intl currency style, dollar template
  literal or chart formatter printing a raw `{c}` elsewhere (`lib/privacy.test.ts`). Free text that states amounts
  (check details, open questions, descriptors) goes through `maskMoneyText`, then `humanizeDates`. **CSV exports are
  never masked** and every CSV button says so (`CsvNote`). A new figure in static prose joins the smoke net's
  `STATIC_OK` deliberately; a money surface shown only after an interaction gets a `SURFACES` row.
  `docs/design/privacy.md`.
- **One clock.** Only `lib/clock.ts` reads the wall clock; "today" is the household's day
  (`clock.today(assumptions.household.timezone)`), and a clock time prints through the zone-aware formatters
  (`fmtDateTime`, `fmtTime`, `fmtRelative`, given the zone). `lib/categories.test.ts` refuses any other read, a fixed
  year-month and a date sentinel: the current snapshot comes through `useSnapshots()` (`latest` / `prior` may be null), the data's
  days through `Indexes.dataRange` and `Indexes.dataThrough` (never past the ingest day).
- **The month in progress** is `monthInProgress(data)` (`lib/agg.ts`: the earlier of the newest month holding a row
  and the ingest month). A month after it is never counted or charted (`countedRows`); trailing rates read
  `fullMonths`; a planning run-rate reads `runRateMonths` (less a first month the data covers in part) and needs
  `RUN_RATE_MIN_MONTHS` (3), else "—". `docs/design/figures-and-vocabulary.md`.
- **Read the right view of the transactions.** Sums and category judgements read `AppData.transactions` (split parts
  and ordinary rows); listing or counting purchases reads `AppData.purchases` (parents and ordinary rows) or counts
  with `purchaseKey` / `purchaseCount`. `docs/design/splits.md`.
- **No category name outside `lib/categories.ts`**: ask the vocabulary (`useVocab()`, `categoryVocab`), which follows
  category renames and reads the reserved names from `assumptions.reserved` (`categories.test.ts` enforces it).
- **One definition per shared figure**: mortgage, home value, debt, cash, essentials, renewals, the bear case and the
  balance sheet each have one `lib/` function; a new page reuses it. The balance sheet is by sign (`balanceSheet`:
  assets are the rows above zero, liabilities those below), a share is of total assets, and a series that sets the
  newest snapshot beside month ends reads it on their footing (`onOneFooting`, a margin account net of its loan).
  `docs/design/figures-and-vocabulary.md`.
- **Words.** A page says what is missing and what fixes it in plain words, a config key only as a hint in the shared
  `Code` pill (`components/NoData.tsx`), never an export file, storage key or command (`tests/test_page_copy.py`).
  One minus sign (U+2212), no negative zero or accounting parentheses; `fmtMonth` in prose, `fmtMonthAxis` on axes
  only, ISO dates only in dense registers and CSV; fine print is `text-faint`; tabs in sentence case. A count with a
  split purchase counted once says "transactions"; what a CSV writes is "rows".
- **The card rule.** Every card, chart card and KPI grid carries a `data-testid` and exactly one row in
  `capabilities.ts` (moving a card moves its row). A row with no `when` renders on its route for every household,
  its shell with an empty-state line when it has nothing; a stand-in is a `when: "data"` row whose `insteadOf` names
  the rows it replaces. The route matrix's `cards` check holds every household to it.
- **Every in-app link targets a live route**, never a redirect source (`App.test.tsx` scans the literals; a route
  table it cannot see, like `lib/allocationRoutes.ts`, gets its own check).
- **Optional exports degrade, they don't crash**: a card that reads one is gated with `<RequireData files={[...]}>`; a
  new export key gets a default in `loadAppData`.
- `writeFilters` is the only writer of filter keys; every chart goes through `EChart`; a wide table sits in
  `<TableScroll>`; rates with no data stay `null` (`fmtPct(null)` = "—").
- A smoke failure never dumps the DOM (boolean assertions with a message): CI logs are not a place for real figures.

## Recipes
- **A page or hub tab:** create `sections/X.tsx`, `lazy`-import it in `App.tsx` and add one entry to `PAGES` — the
  sidebar, ⌘K, the titles, the routes and the tab bar derive from it, and `pageRoute()` wraps it in its own error
  boundary. Keep one `{ to: "…"` per line and `];` only at the end (the screenshot driver parses it). Tab pages have no
  h1 (`HubShell` owns it). `docs/design/information-architecture.md`.
- **A card:** tag it (`card-<slug>` / `kpis-<slug>` / `kpi-<slug>`; `Card`, `StatCard` and `AssumptionsPanel` take
  `testId`) and add its `capabilities.ts` row; content that exists only under a condition gets a `when` flag and its
  own smoke `it` that calls `ASSERTED_WHEN.add(id)`.
- **A removed path:** no redirect and no alias; `REDIRECTS` and `PARAM_REDIRECTS` are empty on purpose
  (`App.test.tsx` pins it) and unknown paths render `PageMoved`.
- **A new export file:** the same index in `EXPORT_FILES` and the `allSettled` array in `loadAppData`, plus a
  placeholder or a `FATAL_FILES` entry. **A new export key:** its default in `loadAppData` (`loadAppData.test.ts`).
  `docs/design/data-and-sync.md`.
- **A FilterBar key:** through `serializeFilters`; the `Required<Filters>` pin in `filters.test.ts` makes it a tsc error
  until listed. Spending's tabs carry exactly `FILTER_PARAM_KEYS`, never a page-scoped key.
- **A new analysis or metric:** a pure function + colocated `*.test.ts` in `lib/`; sections stay thin.
- **A provenance chip:** one entry in `lib/categorySource.ts` `sourceChip()`.
- **A saved-list kind:** a `PrefsDoc` section plus a payload parser in the generic saved layer (`lib/prefs.ts`).

## Shared homes — reuse before re-inlining
| What | Where |
|---|---|
| UI atoms: `Card`, `StatCard`, `Badge`, `Chip`, `pillCls`, `SegmentToggle`, `TableScroll`, `LocalOnlyNotice`, `EditButton` / `DoneButton` | `components/ui.tsx` |
| What a page lacks, in words (`Code`, `NotYetReported`); the link to the manual-assets editor | `components/NoData.tsx`; `components/ManualAssetsLink.tsx` |
| Form inputs: `Pick`, `TagInput`, `CurrencyInput`, `MaskedNumberInput`; labelled pref rows | `components/inputs.tsx`, `components/PrefEditorRow.tsx` |
| An account name (every printed one links to `/accounts/:id`; `appearance="quiet"` in the register) | `components/AccountLink.tsx` |
| The Save box for views and reports; refusal wording | `components/SaveNameBox.tsx`; `lib/savedFilters.ts` `saveRefusalText` |
| Provenance chips; split footnotes | `components/SourceChip.tsx`, `lib/categorySource.ts`; `components/SplitNote.tsx`, `lib/splits.ts` |
| Category names, `NO_CATEGORY` | `lib/categories.ts`, `lib/useVocab.ts` |
| Months (coverage, in progress, full, run-rate, partial) | `lib/agg.ts` |
| Mortgage / home value / debt / cash / balance sheet; month-end footing | `lib/lifeSummary.ts`; `lib/comparableSnapshots.ts` |
| Bear case; renewals and expected charges (the bills chart's and the forecast's) | `lib/forwardLook.ts` `SHOCK_DEFAULTS`; `lib/obligations.ts` `isLumpy`, `expectedCharges` |
| Target mix (buckets, drift, rebalance dollars); its age-based suggestion | `lib/targetMix.ts`; `lib/allocation.ts` |
| Money, rates, months, dates, clock times | `lib/format.ts` (`fmtRate`, `fmtMonth`, `fmtMonthAxis`, `fmtDate`, `fmtDateTime`, `humanizeDates`) |
| Schedule status (the one implementation); quality-check names in words | `lib/schedules.ts` `scheduleStatus`, `lib/upcoming.ts`; `lib/checkLabels.ts` |
| Modules and gated pages; the first-run page | `lib/modules.ts`, `data/useModules.ts`, `components/GatedLink.tsx`, `components/ModuleOff.tsx`; `lib/firstRun.ts` |
| Manual assets: document, draft, editor, a save's ingest | `lib/manualAssets.ts`, `data/useManualAssetsDraft.tsx`, `sections/settings/ManualAssetsCard.tsx`, `lib/ingestFollow.ts` |
| Per-device flags (essential toggles, theme); privacy flag | `lib/localFlagStore.ts`; `lib/privacy.ts` |
| ECharts fragments (colour-bearing ones are functions), `encodeHTML` for tooltip text | `lib/echarts.ts` |
| Expanders, focus traps, breakpoints | `lib/useExpanded.ts`, `lib/useFocusTrap.ts`, `lib/useMediaQuery.ts` (`useIsMobile`) |
| Landing on a card (`?card=`) | `lib/scrollTargets.ts` (`SCROLL_TARGETS`, `useScrollToCardParam`, `cardPath`) |
| The build id and the "updated — reload" check | `lib/buildId.ts`, `data/useBuildCheck.ts`, `app/build/versionPlugin.ts` |
| Test helpers (the application imports none of `test/`) | `lib/__fixtures__.ts` factories; `test/routerProbe.tsx` (`where()`); `test/` for the suites on exports |

## Conventions
- **Data**: `useData()` gives the loaded exports, the indexes, the answers overlay and `missing`. Only the manifest,
  transactions, assumptions and assets timeseries are fatal (no manifest at all is the first-run page); a failed
  reload keeps the last good data. The bundle is built for `EXPECTED_SCHEMA_VERSION` (`data/types.ts`, 23).
  `assets.snapshots` holds only the month ends comparable with the newest (`lib/comparableSnapshots.ts`). A tab older
  than the served export or build shows ONE "reload" banner (`notice-updated`). A gated section is a thin
  `RequireData` default export plus an inner page with the hooks.
- **First run** (`firstRunState`): with no transaction, snapshot or balance point, `"setup"` when `manifest.setup`
  counts no account and no manual asset, else `"waiting"`; otherwise the app. With no export at all the loader's
  `NothingBuiltError` shows the page as `"nothing-built"`, outside the shell. It stands in for every route but Data
  and Settings (`FIRST_RUN_KEEPS`), at the route's own address.
- **Modules**: a `PAGES` entry carries `module:` (`MODULE_IDS`, pinned to `etl/models.py`'s) or `needs: "mortgage"`.
  A module is on per `effectiveModules`: the family's switch (prefs `modules`, set in Settings) over the config's seed
  (`assumptions.modules`), else off; no ingest. The sidebar, ⌘K and tab bars read `pagesFor`; a closed route keeps
  its address and renders `<ModuleOff>`; a link to a gatable page, or its "Hub → Tab" name, is a `<GatedLink>`
  (`whenOff` while it is off), a sentence about it a `<WhileShown>` (`App.test.tsx` refuses any other).
- **Settings** holds the module switches and the **manual-assets editor**: the exports' list with the session's draft
  over it; a config entry is read-only with "Edit a copy" (an app asset of its id overrides it); `validateDraft`
  applies the store's and the engine's rules before a Save; a save asks for an ingest, which the card follows
  (`followStep`). `docs/design/data-and-sync.md`.
- **URL state**: filter, sort and report-key writes are `replace: true` and stamp `CARRIED_SEARCH` (`lib/hubNav.ts`);
  Spending's six-month default applies only on a fresh entry; `?win=<preset>` is a window request resolved on arrival;
  `?sort=` is the register's and never carried; `?card=<id>` lands on a card.
- **Family vs device**: answers, prefs, scenarios and manual assets are the family's (the store, synced by
  `useSyncedDoc`); the theme, privacy mode and the essential toggles are per device (localStorage).
- **Demo mode** (`VITE_DEMO=1`, `lib/demo.ts` `IS_DEMO`, the build `tools/verify/demo_site.sh` makes): `useSyncedDoc`
  takes the local transport (this browser's copy is the document; with none it reads the site's `family/<name>.json`
  once); no refresh, sign-in, sign-out, AI switch or build poll; the clock stands at `generated_at`; a banner. Every storage
  key is defined through `storageKey` (`lib/demoKeys.ts`, prefixed in the demo). The source link: `lib/product.ts`.
- **Signed out** (`data/session.ts`): a store 401 holds the edits under one banner, an export 401 goes to `/login`;
  Sign out clears the four mirrors. A CSV text cell that starts like a formula gets a leading `'` (`lib/csv.ts`).
- **Answers**: three writers over one commit (`data/useAnswersStore.ts`); a selection caps at `SELECTION_CAP`, the
  store at `MAX_ANSWERS` one-off answers (oldest evicted; vendor answers never are); a vendor-scope answer never reaches
  a split part; a parent row is not editable. `docs/design/register-and-answers.md`.
- **Phone**: `md` (768px) is the phone↔desktop cut; below it the sidebar is a drawer behind
  `components/MobileTopBar.tsx`; inputs are ≥16px. Check a layout change with `tools/verify/screenshots.sh` on a
  demo household; a PNG of real exports is never opened. `docs/design/mobile-and-charts.md`.
- **Suites on exports**: the smoke suite runs on the demo household `full`, nothing skipped, its names from
  `truth.json` (`test/truth.ts`), `Date` frozen at the ingest instant (`test/demoClock.ts`), in UTC. The route matrix
  (`matrix.test.tsx`) mounts every route twice, privacy off then on, on every demo profile and a household's own
  exports, and checks five ways (`test/matrixChecks.ts`: `renders`, `values`, `private`, `console`, `cards`; a route
  the first-run or module-off page replaces owes that page's cards); `empty` runs three blocks (set up, waiting for
  data, the first-run state held off); `full` and `tiny` run again in two more zones with the clock tests. A known gap
  is a `test/matrixGaps.ts` row. `docs/design/deploy-and-gates.md`.
- **Accessibility**: overlays are dialogs (`role="dialog" aria-modal` + a label, `lib/useFocusTrap.ts`: focus moves in,
  Tab wraps, Esc closes the topmost, focus is restored); never a nested tab stop (`focusable={false}`); sortable
  headers carry `aria-sort`; the register is one tab stop (roving tabindex).
- **Refresh**: the session has ONE `useRefresh`, owned by `RefreshProvider` (`components/DataRefresh.tsx`); the modal
  and Data's panel are two views of it. Its words come from data (`manifest.setup.feed`; the schedule the status
  reports, `lib/refreshSchedule.ts`): no copy states a schedule.
- Essentials: read this device's toggles through `overrideFor`, never `overrides[key]`; "as toggled" only when
  `toggleMovesRow`.

## Design docs — read before touching
| Before changing… | Read |
|---|---|
| anything that prints money, the privacy flag or the smoke net | `docs/design/privacy.md` |
| `PAGES`, `capabilities.ts`, links, modules, first run, Settings, URL state, Data, refresh | `docs/design/information-architecture.md` |
| `DataContext`, export versions, the update banner, the store and its sync | `docs/design/data-and-sync.md` |
| `TxnTable`, the Review inbox, `CategoryEditor`, saved views and reports | `docs/design/register-and-answers.md` |
| `lib/splits.ts`, split parents and parts, `reportInput` | `docs/design/splits.md` |
| `lib/schedules.ts`, `lib/forecast.ts`, Recurring, Home's next 30 days | `docs/design/schedules.md` |
| `lib/categories.ts`, a shared figure, the month in progress, the budget grid, the report builder, formatting | `docs/design/figures-and-vocabulary.md`, `docs/design/renames.md` |
| layouts below `md`, `EChart`, `lib/echarts.ts`, chart "view data", screenshots | `docs/design/mobile-and-charts.md` |
