# Information architecture: pages, routes, capabilities, URL state

> Scope: how the dashboard's pages, hubs, the redirect table, capability manifest and URL state fit together. Describes the
> system as it is; verify names against the code (`app/src/App.tsx`, `app/src/capabilities.ts`, `app/src/filters/`).
> Paths are relative to `app/src/`.

## Hubs and pages

Eight sidebar items ordered position → flow → detail → forward look, plus the Review inbox as a footer badge:
Home · Cash Flow (Overview, Income statement, Budget) · Spending (Explore, Transactions, Recurring, Reports) ·
Accounts (Balances, Investments, Liabilities, and `/accounts/:id`) · Planning (Projections, House, Second house: the
last two switchable, below) · Trends · Data · Settings (below). Review (`/review`) is routed and in ⌘K but not in
the sidebar.

`PAGES` in `App.tsx` is the single manifest: the sidebar (`navFor`), ⌘K (`paletteFor`: hub tabs read "Hub · Tab", the
index tab included; the optional `aliases` / `indexAliases` let ⌘K find an entry by another name, and no page sets
them), the place title (`titleFor`, the phone's top bar; `documentTitle`, the browser tab), the `<Route>` tree and every
hub's tab bar derive from it; the sidebar, ⌘K and the tab bars read it through `pagesFor`, without the pages switched
off ("Switchable pages" below; `NAV` and `PALETTE` are the two lists with every page shown). A flat page is `{ to, label, icon, page }`; a hub is `{ to, label, icon, hub: { indexLabel, index,
tabs, extra? } }` and renders through `components/HubShell.tsx` (h1 + `RouteTabs` + outlet; the h1 is screen-reader
only below `md`, where the top bar names the place). `extra` routes (`/accounts/:id`) render under the hub, are named by
their param (an account's own name) and are not listed in the sidebar, ⌘K or the hub's tab bar — the smoke route loop
and the screenshot driver do visit them, with a real account id; on one, the hub's index tab is highlighted (`activeOn`).
`navHidden: true` keeps a page routed and in ⌘K but out of the sidebar. Accounts → Investments gives one piece of advice,
or none (`docs/design/figures-and-vocabulary.md`): the target mix (`card-target-mix`) is a suggestion until the family
saves one, and the asset-class targets card under it (`card-class-drift`, on the page for every household: with none
stated it says so and offers its editor) is the household's own. Tab pages have no h1 of their
own; a flat page (Home, Trends, Data, Settings, Review) prints its own. Keep one
`{ to: "…"` per line in `PAGES` and `];` only at its end: `tools/verify/screenshots_driver.py` parses it with a regex.

## Old paths and links

- An unknown path shows the catch-all page (`PageMoved`). The redirect table exists and is empty: `REDIRECTS` holds
  `[from, to]` rows, `RedirectKeepSearch` forwards the whole query, and `PARAM_REDIRECTS` holds query-dependent
  targets. `App.test.tsx` asserts both are empty, that no page or tab carries `aliases`, refuses a redirect onto a
  redirect or a non-page, and keeps ⌘K off redirect sources.
- **Every in-app link targets a live route, never a redirect source.** `App.test.tsx` scans every quoted `"#/…"` literal
  and router-path literal (`navigate("/x")`, `to="/x"`, a template `to` with `${…}` as one segment) under `app/src`,
  comments included, and fails on a redirect source or a path no `PAGES` pattern matches. A link to a route not built
  yet goes in its `NOT_BUILT_YET` set.
- A router-path table the scan cannot see is a named constant with its own check: Home's allocation donut opens a page
  per asset-class slice through `ALLOCATION_ROUTES` (`lib/allocationRoutes.ts`), and `App.test.tsx` checks every
  destination is a page or a hub tab, and that a slice whose page can be switched off has a fallback that always
  shows (`ALLOCATION_FALLBACK`: House opens Balances while the House page is off).
- A link into a page that can be switched off goes through `components/GatedLink.tsx` (below), and so does a mention
  of it in the app's "Hub → Tab" words; `App.test.tsx` refuses any other literal link to a gated route and any such
  mention outside a `GatedLink` (comments aside), and any quoted string of the application that is a gated path (a
  constant, an object `to`, a template whose `${…}` segments could make one), outside the tags of `GatedLink` and
  `WhileShown`, `capabilities.ts`, `lib/allocationRoutes.ts`, `App.tsx`'s `PAGES` literal and `test/`.
- Anything no route matches renders `components/PageMoved.tsx` from the one `<Route path="*">`, last in the tree; it is
  not a `PAGES` entry, so nothing else lists it.

## The first-run page

A household whose exports hold no transaction, no net-worth snapshot and no balance point sees one page,
`sections/FirstRun.tsx`, in place of every page but Data and Settings (`FIRST_RUN_KEEPS`), instead of a page of empty
cards on every route. Which page is `firstRunState(manifest, data)` (`lib/firstRun.ts`), which reads the exports and
never the clock:

- `null`, the app, as soon as the exports hold a transaction, a snapshot or a point of an account's balance series. A
  household whose statement files carry rows and no balance, or whose feed reports only cards left out of net worth,
  gets the pages, each of which says what it lacks (`components/NoData.tsx`).
- `"setup"` when `manifest.setup` counts no account and no hand-stated asset: "Set up your household". An ingest wrote
  the exports, so it accepted `household.yaml`: the page says the household is described and nothing is listed yet,
  and of its three numbered steps (describe the household; list the accounts, each with a name, institution, kind and
  owner; bring data by feed id, statement files with their format, or a value stated by hand, then re-run the ingest)
  the first is marked done in words ("✓ Done: the last ingest read this file."). Each step is plain words with its
  config keys as a hint after them; a line under them says statement files are read only in the formats the release
  knows, that a brokerage's or a card's balance comes from the feed alone, and which document holds what
  (`docs/configuration.md`: every key; `README.md`: the folders, running the ingest, the formats, the feed's access).
- `"waiting"` otherwise: "Waiting for data", what is configured (from `manifest.setup`: the accounts; "at least one
  account names a bank-feed id", since the export says whether, not how many, nor whether a fetch worked; the statement
  files found; else "no bank-feed id and no statement file yet"; the hand-stated assets), one sentence on what brings
  the first data and a link to Data for what the last ingest reported. The sentence: with a feed id never fetched
  (`manifest.simplefin_fetched_at` null), that nothing has been fetched yet; fetched and still nothing, to check the
  feed ids against the feed's own (Data's quality report lists every account the feed reported that no account names);
  with files found, that the last ingest read no rows from them (a link to Data's "What the last ingest read", which
  says what each file held) and to check their format; with neither, where they go.

With no export at all the loader throws `NothingBuiltError` (`docs/design/data-and-sync.md`) and the app shows the same
page in a third state, `"nothing-built"`, outside the shell (no sidebar: there is no data to build one from), titled
"Set up · Family Financials": the three steps with none done, then what happens once they are (the dashboard opens by
itself once an ingest has run without refusing the config; a refused config's problems are in the ingest's own output,
one per line), a "Check now" button, and the provider looking again every 30 seconds. `firstRunState` never returns it:
it is a loader outcome, and `FirstRunPage` (`lib/firstRun.ts`) is the page's three states.

An export older than the `setup` key is read with the one the loader derives (`docs/design/data-and-sync.md`). The page
shows no figure, no chart and no command, editor or document the tree does not have.

`pageRoute(p, pathname, firstRun)` in `App.tsx` mounts it in place of a route's page, inside the route's error boundary
and page testid, at the same address: a typed path, a link, a ⌘K entry or a bookmark keeps its URL and opens its own
page once data arrives. A hub whose every route it stands in for drops its shell (h1 and tab bar), so the first-run
page's h1 is the only one. The sidebar, the header and the catch-all are unchanged; the phone's top bar and the browser
tab name the state ("Set up", "Waiting for data · Family Financials": `titleFor`, `documentTitle`, `firstRunShownAt`).
The page's marker is `data-testid="first-run"` with its state in `data-first-run`.

## Switchable pages (modules)

Some pages are not for every household. A **module** is an optional part of the app, off unless switched on; its ids
are `MODULE_IDS` (`lib/modules.ts`, the list `etl/models.py` holds too: `tests/test_validate.py` pins the two), today
`home_buying`, whose page is Planning → Second house. A page may instead **need** something the household has:
Planning → House needs an account marked as the mortgage (`assumptions.mortgage` not null). A `PAGES` page or hub tab
says which with `module:` or `needs:` (`lib/modules.ts` `Gate`); `moduleOf(route)` reads a route's gate and
`GATED_ROUTES` lists the gated routes.

- **Whether a module is on** is `effectiveModules(seed, prefs)`: per module, the family's preference
  (`prefs.modules[id]`, which `usePrefsStore().setModule(id, on)` writes) when it is a boolean, else the config's seed
  (`household.yaml` `modules`, exported as `assumptions.modules`), else off; a preference of `false` turns off a module
  the config switches on, and an id the dashboard does not know (a later bundle's module) is kept in the document and
  carried through every save, ignored for effect. `data/useModules.ts` `useModules()` gives the modules, what the household has (`factsOf`) and `setModule`. A
  switch shows or hides its page at once, with no reload and no ingest. The preferences keep `modules` whole, so a tab
  that did not change it never sends it (`docs/design/data-and-sync.md`).
- **The route stays.** `closedRoutes(modules, facts)` are the routes whose page does not show. `PAGES` stays complete
  and the `<Route>` tree keeps every route: `pageRoute` renders `components/ModuleOff.tsx` in place of a closed route's
  page, at its address, inside the route's boundary and page testid, so a bookmark opens the page once it shows; the
  first-run page wins where both would stand in. On such a route the hub drops its h1 (the module-off page's is the
  only one) and keeps its tab bar, with neither tab current.
- **Nothing leads into it.** The sidebar, ⌘K and the hubs' tab bars read `pagesFor(modules, facts)`, `PAGES` without
  the closed pages and tabs; a hub with one tab to show draws no tab bar (Planning with both off is Projections
  alone). The shell gives every in-app link the answer through `lib/modules.ts` `OpenRoutesProvider` (`App.tsx`
  `routeOpenIn`: a path, its query or hash aside, against the closed routes), which `useRouteOpen()` reads (with no provider every page
  shows): `GatedLink` is a link while its page shows and, while it does not, its `whenOff` — by default its words as
  plain text, for a sentence that still reads true with the page off; `null` where nothing should stand (Liabilities'
  "what-ifs →") — never a link to the module-off page; `WhileShown` (the same file) renders a sentence about the page
  only while it shows (Projections' and Liabilities' "the extra-payment what-if is in Planning → House"); and Home's
  allocation donut opens Balances from the House slice while House is off (`allocationRoute`).
- **The module-off page** is the page's own name as its h1 and one sentence on what the page is for; for a module, a
  button that names it ("Turn on Home buying", from `MODULE_NAMES`; it calls `setModule(id, true)`, and once the page
  has taken its place focus moves to `<main>`, focusable by script alone) and a link to Settings; for a need, what
  brings the page (an account marked as the mortgage, then the next ingest) with `mortgage: true` in `accounts.yaml`
  as the hint. No figure, no chart. Its marker is `data-testid="module-off"` (`data-gate`: the module or the need) and
  its card `card-module-off`. It never shows before the family's preferences have loaded: the data provider shows the
  loading skeleton until they have (or until the store is found unreachable and the browser's copy stands in).

## The capability manifest ("no capability lost")

Every card, chart card and KPI grid carries a `data-testid` (`card-<slug>` / `kpis-<slug>` / `kpi-<slug>`) and exactly
one row in `capabilities.ts` (`{ id, route, when?, insteadOf? }`); that file is the source of truth for ids and homes. Adding a card
= tag it and add its row; moving one = move its row in the same commit. Page roots are stamped by `pageRoute()`
(`page-<slug>`, `hub-<slug>`). The guards:

- `smoke.test.tsx` mounts every route against the exports of the demo household `full`, finds each id pinned to it and
  asserts nothing pinned to another route renders there. It needs those exports and their `truth.json`:
  `tools/verify/ci.sh` generates them and runs it with nothing skipped (`docs/design/deploy-and-gates.md`, "What runs
  where"); a plain `vitest run` without exports skips it.
- Its trailing `describe("capability manifest")` runs without exports: every route has a row, every row's route exists,
  no redirect source is a page.
- `capabilities.test.ts`: ids unique and well-formed, never `chart`, each present as a literal testid somewhere
  (`BUILT_AT_RUNTIME` is the allowlist for an id built at runtime; empty) — and the other way round, every
  `card-` / `kpis-` / `kpi-` testid literal under `app/src` is a manifest id, so a card dropped from the manifest
  cannot stay on its page (`NOT_A_CAPABILITY` is the allowlist for a tagged literal that is no row; empty).
- Content that exists only under a condition gets `when` (`"scenario" | "drill" | "data"`, and the two below) and its
  own smoke `it`, which must `ASSERTED_WHEN.add(id)`; a trailing `it` refuses a `when` row nobody asserted.
- The first-run page's cards are `when: "first-run"` rows pinned to `/`, where a new household lands: the heading card
  (`card-first-run`) in both states, the steps (`firstRun: "setup"`) and the summary (`firstRun: "waiting"`). Each is on
  every route the page stands in for while the household is in its state; `capabilities.test.ts` pins that shape. The
  smoke suite's household `full` is never in a first-run state (one `it` asserts it), so its trailing `it` leaves these
  rows to the route matrix, whose `cards` check requires them on every route the page stands in for, on the demo
  household `empty` in both states, and refuses a card of the route's own page there.
- The module-off page's one card is a `when: "module-off"` row pinned to the module's page (`card-module-off`, on
  `/planning/second-house`; `App.test.tsx` pins that shape) and is on every route the page stands in for. The smoke
  household `full` has every module on and a mortgage (one `it` asserts it), so its trailing `it` leaves the row to
  the route matrix, whose `cards` check requires it on every route switched off, on the demo households that have one,
  and refuses a card of the route's own page there.
- The card rule: a row with no `when` renders on its route for every household, its shell with an empty-state line
  when it has nothing to show. A **stand-in** — one card that says why a group of cards has nothing to show, in their
  place — is a `when: "data"` row whose `insteadOf` names those rows (rows of its own route with no `when`, which
  `capabilities.test.ts` pins): they may be absent while it is on screen, and must be there otherwise. One today:
  Liabilities' `card-no-credit-cards`, in place of the two card charts. The route matrix's `cards` check
  (`docs/design/deploy-and-gates.md`) holds every household to it.
- `RECURRING_GATED` lists exactly the routes that show the missing-data card without `recurring.json`.
- The smoke suite asserts every row without a `when` on `full`, which holds every feature's data, so a card that
  renders nothing when its data is empty passes there; give it `when: "data"` when empty is normal for a household.
  The route matrix (`app/src/matrix.test.tsx`) holds every demo profile to the card rule through its `cards` check.
- A smoke failure never dumps the DOM: boolean assertions with a message, and `configure({ getElementError })` returns
  the bare message (CI logs are not a place for real figures).

## Removed duplicates and their homes

No capability is lost when a card goes: each one removed as a duplicate names the card that carries what it showed.

| Removed (route) | What it showed | Its home now |
|---|---|---|
| `card-trend-networth` (`/trends`) | net worth, liquid assets and the daily SimpleFIN line per snapshot | Home `card-networth-history` (every snapshot, the daily line, the milestone pins, `?snap=` rewind); the liquid assets per snapshot are Trends `card-trend-liquidity`'s "dollars" view, and its view-data table lists liquid assets, assets and the share of assets per snapshot |
| `card-trend-income` (`/trends`) | income by stream per month, top four plus "Other" | Cash Flow `card-income-by-source` — every stream with income in the window, unfolded (a finer set), over the same coverage months with the All window, with a view-data table |
| `card-milestones` (on a page of its own, removed) | the snapshot net-worth line with the five newest $100K-crossing pins | Home `card-networth-history` (the line and the newest three pins, `lib/milestones.ts` `milestoneCrossings`) |
| `card-income-streams` (`/cash-flow`) | each stream's average month over the trailing 3 full months, top four plus "Other" | the last row of `card-income-by-source`'s view-data table ("3-month average, <first> to <last>" in its Period column): each stream's 3-full-month average (unfolded), with the month it runs through in the card's footnote |

Spending → Reports does not open on Explore's first chart (spending by category per month, stacked): a bare Reports
tab is spending by vendor, per month, as a table (`DEFAULT_SPEC`, `lib/reportSpec.ts`). Every link the app writes —
a saved report, ⌘K, Data, a picker change — names all four report keys, so it opens the same report whatever the
default is; a bookmark naming only some keys reads the rest as spending by category, per month, stacked
(`LINK_BASELINE`), which is what it meant when it was made.

## Resilience

`pageRoute()` wraps the hub shell and every index / tab / extra element in its own `components/SectionBoundary.tsx`,
keyed on the path, so one broken section renders an alert card and switching tabs clears it (a stale-chunk failure
offers a hard reload). Loading is `components/Skeleton.tsx` (its `aria-busy` is what the screenshot driver waits on).
Optional exports and schema versions: `docs/design/data-and-sync.md`.

## Filters and URL state

- Facets AND together, values within a facet OR; defaults are omitted from the URL. `filters/filters.ts` builds deep
  links: `spendingPath(patch, target = "transactions")` returns a router path, `spendingLink` the `#` href. The default
  lands on Spending → Transactions (rows); `"explore"` lands on the charts. ⌘K's category and vendor entries use
  `spendTo` (`components/CommandPalette.tsx`: the last six months plus the patch, explicit dates).
- `writeFilters(prev, f)` is the only writer of filter keys: it replaces exactly `FILTER_PARAM_KEYS` in a copy of the
  params, so a filter edit keeps `sort`, `snap`, `end`, `pl`, `win` and the report keys. `parseFilters` validates dates
  and amounts through `filters/urlValues.ts` (`isISODate`, `dateParam`, `amountParam`; a bad value is ignored with one
  warning per distinct value); `parseDateRange` and `parseWin` use the same checks.
- Every filter, sort and report-key write is `replace: true` and stamps `CARRIED_SEARCH` (`lib/hubNav.ts`) on its own
  entry, so Back onto a cleared or All-time entry is not mistaken for a fresh visit.
- `?sort=<colId>:<asc|desc>` is the Transactions register's own key (`filters/useSort.ts`; the stable ids are
  `TXN_COLUMN_IDS`; a stale value falls back to `date:desc` with one warning, never a crash) and is never carried
  between tabs. The Reports tab's keys are `groupBy` / `interval` / `measure` / `chart` (`lib/reportSpec.ts`).
- **Spending's tabs share the FilterBar's URL state**: Explore (`indexKeepSearch: FILTER_PARAM_KEYS`), Transactions and
  Reports (`keepSearch: FILTER_PARAM_KEYS`) carry exactly the filter keys on a tab switch, never a page-scoped one;
  Recurring drops them. A new FilterBar key goes through `serializeFilters`; the pin in `filters.test.ts` (a
  `Required<Filters>` literal) makes a new `Filters` key a tsc error until it is listed.
- `filters/useSpendingDefaults.ts` (`useSpendingDefaults()`, all three tabs) applies the six-month window only on a
  fresh entry: no `FILTER_PARAM_KEYS` in the URL and no `CARRIED_SEARCH` marker, so a URL holding only `?sort=` is still
  fresh. A `?win=<preset>` (a saved view or report opened, or a hand-typed one) is a window REQUEST: resolved against
  the day the data reaches (`Indexes.dataThrough`: the newest row, never past the ingest day — the one anchor of every
  preset and default window) on mount and on every later navigation, then removed (dates beside it win; an unknown value is
  ignored with a warning). The default and the `win` clean-up are one write. Saved views' rolling windows:
  `docs/design/register-and-answers.md`.
- Planning, Data, Cash Flow → Budget, Accounts → Investments and Settings take `?card=<id>` (`lib/scrollTargets.ts`:
  `SCROLL_TARGETS`, `CARD_PARAM`, `useScrollToCardParam`, called once by the page) to land on a card on arrival: it
  is scrolled into view and focus moves to its heading — a hash link cannot target an in-page anchor under
  HashRouter. Such a card passes its id to `Card`, which gives it `scroll-mt-14` under the sticky phone header and a
  heading focusable by script; an unknown id, or a card the page does not show, is a quiet no-op. Settings' Elsewhere
  links are the ones the app builds (`cardPath`).

## Settings

`/settings` is a flat page, never replaced by the first-run page (`FIRST_RUN_KEEPS`), so every card renders for every
household, one with nothing at all included. While the household is in a first-run state one line under the heading
says where the set-up steps are ("Setting up? The steps are on Home.", a link to `/`). Its six cards, in order
(`capabilities.ts`, none with a `when`):

- **Modules** — one labelled switch (`role="switch"`) per module (`lib/modules.ts` `MODULE_IDS`), with what it adds and
  where its value comes from (`moduleSource`, which `effectiveModules` reads too): the family's switch made in the
  dashboard ("in this browser" while the store is unreachable), else the household's config (`modules:` in
  `household.yaml`), else off by default. A switch calls `setModule` and the app follows at once; while the family's
  switch decides, a button clears it (`setModule(id, null)`), named by what that gives: "Use the config's setting (on)"
  or "(off)", or "Go back to the default (off)" when the config states nothing. While the household has no mortgage
  account the card says what brings the House page, which is no module.
- **Manual assets** (`sections/settings/ManualAssetsCard.tsx`, landed on by `?card=manual-assets`) — the editor for
  what no account reports: a home, a vehicle, a debt entered by hand. Its list is the exports' (`AppData.manualAssets`,
  what the last ingest counted, and `manualOverridden`, the config entries an app asset hides) with the session's
  unsaved draft over it (`lib/manualAssets.ts` `cardRows`; the draft lives above the routes,
  `data/useManualAssetsDraft.tsx`, so leaving Settings keeps it), each row its name, class (the dashboard's own colour)
  and owner, where it was entered and its latest value through the masked formatters (on its own line below `sm`). An
  asset entered in the app opens to its fields: name, owner (`assumptions.owners`), class (the dashboard's order,
  assets and debts apart; a sign flips only when the class crosses between them), liquid (off and fixed for a debt,
  whose amounts are entered as owed and stored negative; one stored above zero shows below zero and must be typed
  again), an optional "acquired on" and its dated values (add, correct, remove; a value dated after today says when it
  takes effect, and a value or acquisition after the accounts' newest report says it counts once they report on or
  after it). A first value after the data's first day asks "Owned it before …? Nothing to do" (it is carried back) or
  "Acquired on that day?", which sets `acquired`. A config entry is read-only ("from your config", with one line that
  deleting it is an edit of `accounts.yaml`) and offers **Edit a copy**, an app asset of its id that overrides it; a
  copy (`configEntryOf`: an id the exports hide or still count as the config's) names the config's own value and offers
  **Use the config's value again**, which removes it, and says it keeps no holder. "Saved" is what the family store
  acknowledged (`acked`): a refused save keeps its rows unsaved and the refusal line ("Not saved: … — <the store's
  reason>", or that this device's clock looks wrong for a stamp too far ahead) shows whenever the store has refused the
  current document, after a reload too; Revert sends the acknowledged copy back. A removal is a row until an ingest has
  read it ("Removed — it leaves the dashboard after the next ingest", or "The config's value (<name>, <value>) counts
  again after the next ingest"), from the documents alone. Every field is labelled, every problem (`validateDraft`) is
  tied to its field; one the draft makes is listed by Save, whose description it is, while one an untouched asset
  already had is that row's warning and blocks nothing. **Save** and **Revert** stay in the Tab order, announced as
  disabled while nothing changed (Save also while a blocking problem is left). An asset the last ingest left out says
  why (its `manual_assets.entry` warning); one saved but not counted yet says "not in the dashboard yet"; one edited
  here and removed on another device meanwhile says so. While the store has not answered since the page loaded the
  card lists what the last ingest counted, read-only. **After a save** the card follows the ingest the store asked for
  through the shared refresh (`useRefreshFollow`, `watch()`; the shared follow, `followStep` in `lib/ingestFollow.ts` run
  by `data/useIngestFollow.ts`): "Saved — the
  dashboard updates after the next ingest" while it is wanted or running; once it ended and the exports reloaded, "Up
  to date" when `accounts.json` `manual_assets` holds every saved field (`compareSaved`); "Saved, but the last ingest
  timed out / was stopped / failed (code N)" (`ingestFailure`); "Saved. The dashboard shows it after the next scheduled
  refresh or ingest." when nothing was asked for, the store predates the ingest block, or `FOLLOW_MS` (the refresh
  view's three minutes) passed — at the bound the card looks once more (`watch(false)`), so a tab that slept past it
  and wakes after the ingest ended still sees the change counted, and a follow that look has not settled within
  `FOLLOW_GRACE_MS` (ten seconds: no answer, or a reload that never brings new exports) ends there too; and "Saved.
  Re-run the ingest to see it." when the store answers that there is no refresh service (a read that fails meanwhile is
  passed over, and only reads sent after the save landed count). A refusal is not sent again. A copy's removal is
  pending only while the exports count the id as an app asset: once they list it as the config's entry, that entry's
  row is the truth.
  Mortgage, Liabilities, Data, Balances and the first-run page's Set-up and Waiting cards link here
  (`components/ManualAssetsLink.tsx`: the no-home-value sentence `NoHomeValue`, the home value's source, the hand-entry
  caveat, a hand-entered row of the liquidity list), the config key kept as a hint.
- **AI categorization** — "Not available in this version."
- **Elsewhere** — the settings that live on other pages (`ELSEWHERE` in `sections/settings/ElsewhereCard.tsx`, each a
  route, its place title and the card it lands on; `App.test.tsx` checks the table): privacy mode, projection defaults
  and saved views on Data, the budget, the target mix. A link shows only while its page does and has something to set —
  not while the first-run page stands in for it, nor in that state for the saved views (`needsData`: they are made on
  pages the first-run page replaces), the card then naming what appears once there is data; nor while its page is
  switched off (`useRouteOpen`).
- **About** — the product name (`lib/product.ts`), the build id (`lib/buildId.ts`; "development build" when the page
  carries none), the export's schema version, when the data was last built (`manifest.generated_at`, through
  `fmtDateTime` in the household's zone), and that the dashboard makes no outside request.
- **Sign out** — a button announced as disabled (`aria-disabled`, so it stays in the Tab order; a click does nothing),
  its reason in words tied to it by `aria-describedby`.

## Data page and refresh

Data is a flat page (its cards are in `capabilities.ts`). Editors sit beside the cards they drive (budget and payroll on
Cash Flow → Budget, the target mix on Accounts → Investments); Data's "Assumptions in force" links each. "What the
last ingest read" (`card-ingest-read`, `lib/ingestRead.ts`) is where a household whose statement files yielded no rows
learns why: from the quality report's per-file counts, each statement file by its own name with the rows it read and
the lines it skipped (each reason in words), the bank feed as one source over all its pulls (its skips are rows), and
each account no file was found for, with the pattern it looks for. When every source read rows the card is one line
("N sources read, M rows; none came back empty") with the table behind a "Show the sources" disclosure; when one read
nothing the table is open, what read nothing leads, in amber, and past twelve sources the ones that read rows wait
behind a button. A link may land on either card: `?card=ingest-read` (the first-run page's "files found, no rows"
sentence) and `?card=caveats` (the "not yet reported" mark). The caveats list the month ends the net-worth history leaves out
(`docs/design/figures-and-vocabulary.md`, under Debt). The session has ONE `useRefresh`, owned by `RefreshProvider` (`components/DataRefresh.tsx`, mounted around the shell):
`RefreshModal` (behind the freshness pill) and `RefreshPanel` (inline on Data) are two views of it, so a run outlives
the view that started it and the data reloads exactly once. The status is read when a view opens, when a save asks
(`watch()`), and by a visible tab itself (`useLooks`: a moment after the page opens, `LOOK_FIRST_MS`, unless another read
went out meanwhile — one read on load with Data open, not two — then at most once a minute after the last read of any
kind, `LOOK_EVERY_MS`; none while the tab is hidden, one at once when it comes back after a minute, and none on a timer
once the store has answered that there is no refresh service, when coming back to the tab or opening a view looks
again); it is polled only while a run is in flight; the "what the last run did" line obeys the pure `outcomeVisible`
(never during a run, never over a stalled status, never once a newer run started, never after `OUTCOME_TTL_MS`). Reads
are numbered as they are sent, and an answer to an older read that arrives after a newer one is dropped, so the status
is always the newest read's; a look counts only on the answer to the read it sent, or a later one. No 401 leaves the
page, whatever read or press got it (an unsaved edit may be held there): the tab is signed out, the pill itself says
"signed out" in a warning tone, and the readout shows one line, "Your sign-in has ended. Sign in again", whose link goes
to the login page when the person follows it, in place of the button. Polling is bounded on every side: the
three-second poll (a refresh in flight for at most `RUN_FOLLOW_MS` from the read that first found it — the refresher's
own fifteen-minute limit and two more; a read whose `started_at` or `request_nonce` differs is another run, followed
from that read, so a manual run that starts as a scheduled one ends gets its own bound — then "The refresh has not
reported back …" and nothing until a view opens; an
ingest followed for `INGEST_FOLLOW_MS`) runs only while the tab is visible and the sign-in holds; a read that gets no
answer (a 502, none at all) leaves nothing to press until the next answer, which is tried at the slow cadence
(`LOOK_EVERY_MS`), not three seconds later; the store's 503 (no refresh service) stops it. The rate limit is the
server's (`deploy/refresh_state.py`). An ingest without a fetch (`ingest`, below) that a look finds
wanted or running — a new view, or `watch()` from a manual-assets save — is followed for `INGEST_FOLLOW_MS` (three
minutes) at the same poll rate, and the shared refresh says who asked for it (`ingestCause`: a save, this tab's rebuild,
or nobody it knows of): meanwhile the button reads "Updating…" ("Rebuilding the dashboard with a saved change — no bank
pull." only when a save of this tab asked for it, else "A rebuild is under way — no bank pull."; with no feed,
"Rebuilding…" with the same two lines), and its end reloads the exports (once; a refresh's own ingest step is left to
the refresh's end). Past the bound the readout stops polling and the button is a refresh again, so a capped or stuck
ingest never spins it. Any read that finds an ingest newer than the exports on screen that ended well (`newerIngest`: it
started after their `generated_at`, both stamps the server's clock) reloads them once, with no outcome line; when the
reload brings a newer build, a line hangs under the pill for `UPDATED_LINE_MS` ("Updated: the dashboard was rebuilt
<age>.", out of the flow, so nothing moves, and click-through), announced once by the provider's `role="status"` region — another device's
rebuild or a scheduled refresh nobody watched shows within about a minute. A stalled refresh says "You can start another
one." (with no feed, "You can rebuild from files.") only while a button can be pressed; a request no refresh service
picked up says it may be stopped.

The control speaks from data (`components/DataRefresh.tsx`, `lib/refreshSchedule.ts`), in the household's words:
- **With a bank feed** (`manifest.setup.feed`): the pill reads the age of the last feed fetch (`simplefin_fetched_at`;
  "not fetched yet" when null), amber past one and a quarter of the longest gap between the scheduled refreshes the
  status reports and red past three of them (`staleHours`; with no schedule in the status, those of a once-a-day
  schedule, `FALLBACK_GAP_H`: 30 and 72 hours), its tooltip saying why; until the status has answered it is neutral,
  so it never flips a moment after the page opens. The readout's rows are "Last feed fetch" and "Dashboard rebuilt";
  the button is "Refresh now" (a fetch, then an ingest; the nonce flow).
- **With none**: the pill reads "rebuilt <age>" (`generated_at`), toned by the same thresholds when the status reports
  a schedule ("Rebuilt over 10 hours ago: a scheduled rebuild may have been missed."), neutral without one (rebuilds
  run by hand have no schedule to fall behind); the one row is "Dashboard rebuilt", and the button is "Rebuild from
  files", which posts `{"mode": "ingest"}` and follows that ingest with the shared follow (`data/useIngestFollow.ts`,
  the one the manual assets' save runs): "Rebuilding…" while it is wanted or running, then "Rebuilt — the numbers on
  screen are current.", "The rebuild timed out / was stopped / failed (code N). …" (`ingestFailure`; 143 and -2 are run
  again and followed on), "Today's rebuilds are used up: …" (`ingest_cap`), "The request did not reach the dashboard's
  store, so nothing was rebuilt: …" (no answer, or a gateway's 502 / 504: nothing was asked, nothing is followed), or,
  past the bound, "The rebuild has not finished yet: …" when it was seen to start, "The rebuild has not started — the
  refresh service may be stopped." when it never was. An ended rebuild's line goes after `OUTCOME_TTL_MS`. No sentence
  names a feed, a fetch or a pull; the refresh budget is not shown.
- **No refresh service** (the store's 503: a household that runs the ingest by hand), a store that does not answer, or
  a sign-in that ended: nothing to press, and the line says which and what to do. It is read live: the button comes back
  with the next answer, and a follow is ended only by the store's 503 — a read that fails mid-follow is passed over; a
  follow the store never answered ends at its bound with "The dashboard's store could not be reached since the rebuild
  was asked for: …" (it cannot know whether the ingest ran).
- **The schedule sentence** (`scheduleSentence`), when the status carries a schedule: its times on the household's
  clock and in the reader's 12- or 24-hour convention, converted from the store's zone when the two differ, with the
  zone's name from `Intl` — "Refreshes automatically at 1:00 AM, 9:00 AM and 5:00 PM (Central Time)." for up to four
  times, "… N times a day, next at …" for more; "Rebuilds automatically …" with no feed. No schedule, no sentence. The
  first-run page's waiting card says when the next scheduled refresh is (`nextRefreshSentence`) when the status has a
  schedule and the household a feed.
No sentence anywhere states a refresh frequency as a fact of the product or names a machine (`lib/refreshSchedule.test.ts`
scans the dashboard's code): copy elsewhere says "at the next data refresh", and the store is "the dashboard's store".

### The server side: a refresh, and an ingest without a fetch

Three processes share the refresh directory, the mailbox (`state/refresh/`), and never talk to each other: the `store`
(the only one a browser reaches), the `refresher` (`deploy/refresh_runner.py`, headless) and `deploy/refresh.sh` (run
by the scheduler's crontab and by the refresher); the scheduler also writes `schedule.json` there as it starts. Every
read and write there goes through `deploy/safe_files.py`, never through a link (`docs/design/platform.md`). A **refresh** fetches the bank feed and then ingests. `POST /api/refresh` with no
body asks for one: the store applies the rate limit (`decide()`), records it in `ledger.json` and writes
`request.json`, whose nonce the refresher claims before it runs `refresh.sh`. Every refresh holds `refresh.lock` for
its whole run, and `status.json` has only the refresher and `refresh.sh` as writers, kept apart by that lock.

An **ingest without a fetch** rebuilds the exports from the files alone, spending none of the feed's budget: a
household with no bank feed rebuilds this way, and so does a change to a manual asset. It has no request file and no
nonce. The store asks for one by touching the marker `ingest_wanted` in the refresh directory (its mtime is the
request, set to the whole second one to two seconds after the touch, so a filesystem that keeps one- or two-second
mtimes still leaves it after any ingest that started before the save; the file is never rewritten or removed): `POST
/api/refresh` with `{"mode": "ingest"}` does (202 `{accepted: true, mode: "ingest"}`), and so does a manual-assets
save that changed the stored bytes, after the document is written (an identical re-save, a refused save or a store
started without a refresh directory touches nothing). Neither writes the ledger or the request. An empty or
whitespace-only body, `{}` or any JSON object with no `mode` asks for a refresh; every other body (not UTF-8, not JSON,
JSON that is not an object such as `"ingest"` or `[]`, or a `mode` other than `"ingest"`, `"ai"` or `"ai_reset"`) is a
400 that queues nothing and spends none of the budget. `"ai"` and `"ai_reset"` are ingest requests that also touch an
AI marker, so the run first runs or forgets the AI step (`docs/design/ai.md`).

The refresher handles a refresh request first. With none to act on, it asks `ingest_due`:

| The marker | Answer | What the refresher does |
|---|---|---|
| absent, or no newer than the last ingest's start (unless that ingest ended interrupted, below) | `no` | nothing |
| its time under `INGEST_SETTLE_S` (60 s) ago | `wait` | nothing (each touch restarts the wait, so several saves in a row cause one ingest; the settle ends 61–62 s after the save) |
| settled, `MAX_INGEST_PER_DAY` (48) ingest-only runs started on the local day | `cap` | nothing until local midnight, or until a refresh's own ingest answers it |
| settled, under the cap | `run` | takes the lock, asks again, claims the run and runs `refresh.sh ingest` |

A refresh holding the lock means nothing happens: its own ingest answers the marker if that ingest starts after the
touch, and the next poll sees what is left. A waiting or capped marker is logged once when the wait starts, not on
every two-second poll. The run is claimed before it starts (its start written and counted), as a request's nonce is,
so a script that dies at once is not re-run every poll; `refresh.sh ingest` runs with `FF_TRIGGER=ingest` and the
lock held, and the refresher records the ingest's end and exit code when the script could not (a kill).

`refresh.sh ingest` runs `python -m etl ingest` alone. It takes the lock as a refresh does (unless it is held for it),
records the ingest's start, end and exit code, and exits with the ingest's code; the feed fetch, the failure streak,
the re-auth state and the last refresh's own status fields are left alone. A failed ingest-only run alerts once per
streak: the first failure alerts, the next ones are logged as repeats until an ingest of either mode succeeds
(`.ingest_alert_state` in `state/engine/`, truncated, never removed); a refresh's own failed ingest alerts every run. A refresh
records its own ingest step the same way, so **any ingest that starts after the marker was touched answers it** —
unless it ended interrupted.

`status.json` therefore carries the ingest's fields beside the refresh's: `ingest_started_at`, `ingest_finished_at`
(null while it runs), `ingest_rc`, `ingest_runs` (the local day's ingest-only starts, which the cap counts) and
`ingest_only`, each time in UTC epoch seconds. They are written only by the refresher and `refresh.sh`, merged under
the lock; the store reads them, for the cap and for the status, and never writes `status.json`. An ingest-only run
holds the lock but is no refresh: the refresher writes `ingest_only: true` with the run's claim and `false` as its last
write before it lets the lock go, however the run ended, so for the whole time it holds the lock `state` keeps the last
refresh's and a refresh asked for meanwhile queues behind it (the refresher runs it next) instead of attaching to the
last refresh's nonce. The mark means nothing with the lock free, and whatever starts a refresh clears it.

`ingest_rc` is one of: 0; the ingest's own failure code; 143, stopped from outside (`refresh.sh`'s TERM trap — the
refresher, itself stopped mid-run, TERMs the run's process group, gives it 8 s, `STOP_GRACE_S`, then kills it, a
second stop signal meanwhile ignored, so the trap records the end before the container goes and nothing of the run
outlives it); -1, timed out (the refresher killed it past `RUN_TIMEOUT_S` and records -1 whatever the trap wrote);
-2, found dead (each pass first closes an ingest that has a start, no end and no run holding the lock); or 128 + N, a
script killed by signal N before it recorded the end, as the shell reports one (130 for INT, 137 for KILL; killed by
TERM it reads 143 and counts as stopped from outside), so -1 and -2 are only ever the refresher's own. An ingest
that ended **stopped (143) or found dead (-2) read nothing reliably, so it does not answer the marker**: the
marker's ingest runs again at the next pass (the marker is old, so with no new wait), within the daily cap. A
timed-out or failed ingest is not retried, so a hung or broken one never loops.

`GET /api/refresh` reports, beside the refresh's `state`, nonces, outcome and budget:

- `schedule`: `{times: ["01:00", "09:00", "17:00"], timezone: "America/New_York"}`, read from the mailbox's
  `schedule.json`, which the scheduler writes from `household.yaml` (`refresh`, `timezone`) as it starts
  (`deploy/make_crontab.py`), or `null` until it has. The same zone draws every local day of the caps.
- `ingest`: `{wanted, last_started_at, last_finished_at, last_rc}`. `wanted` is true while the marker is newer than the
  last ingest's start, or that ingest ended interrupted; the times are UTC ISO strings or `null`. An ingest a client
  asked for **has ended when `wanted` is false and `last_finished_at` is at or after `last_started_at`** (`last_rc` 0
  when it succeeded); while `wanted` is true it is waiting or being run again. Past the day's cap, `POST {"mode":
  "ingest"}` is a 429 `{accepted: false, reason: "ingest_cap", retry_after_s}` (the seconds to local midnight) and
  touches nothing.
