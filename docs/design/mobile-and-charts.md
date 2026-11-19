# Phone layouts and charts

> Scope: the phone/desktop rules, chart sizing and labels, chart "view data" tables, and how to check a layout change.
> Describes the system as it is; verify names against the code (`app/src/components/EChart.tsx`,
> `app/src/lib/echarts.ts`, `tools/verify/screenshots.sh`). Paths are relative to `app/src/`.

## The phone

`md` (768px) is the phone↔desktop cut. Below it the sidebar becomes an off-canvas drawer behind a sticky top bar
(`components/MobileTopBar.tsx`, a 48px row): the menu button with the Review count chip, the place's title (`titleFor`,
wrapped onto a second line at a word, never cut inside one), the freshness pill, the privacy eye and the search button,
each 44px; under that row the bar carries a `notice` while it has one (the "Updated" line of another device's rebuild,
`RefreshUpdatedStrip`), so the bar grows rather than anything hanging over the page. A hub's h1 is screen-reader only
there. `useIsMobile()` (`lib/useMediaQuery.ts`) is the shared breakpoint hook.

- **Words follow the pointer, never the width** (`lib/pointerWords.ts`, `usePointerWords()` on `(pointer: coarse)`):
  a finger reads "tap" where a mouse reads "click", "Pinch the chart to zoom" for "Scroll on the chart to zoom", and is
  told about no key (`x selects it`, Alt+Shift+P, the palette's ↑↓ ⏎ esc line). One word also reads the width: the
  palette is "search" (the top bar's 🔍) only for a finger below `md`, where that button is (`searchButton`), and "⌘K"
  everywhere else, a tablet's finger included. A narrow desktop window still clicks; the screenshot browser is a
  mouse, so the pictures show the mouse's words.
- **A title is a mouse's**: on a coarse pointer `components/TouchTitles.tsx` (mounted once in the shell) shows the
  `title` of a tapped element that takes no action of its own (a Badge, a status dot, a dagger, a table cell, a label's
  ⓘ) in a bubble under it until the next touch, a scroll or Escape. A control's title is never shown that way: what a
  button or link needs said is said in its text. A `Badge` with a `title` is focusable and repeats the title in an
  `sr-only` span (`components/ui.tsx`), so a keyboard and a screen reader get it too: never a bare `<span title>`.

- Wrap any wide table in `<TableScroll label=…>` (`components/ui.tsx`) so it scrolls instead of overflowing the page.
  While the table is wider than its box (`lib/useScrollHint.ts`) the scroller shows it: a "scroll →" cue at its top
  right while more lies to the right (hidden at the end, its line kept), and it is a region named by `label` in the tab
  order, so the arrow keys scroll it; `stickyFirst` keeps each row's first cell on screen (a table whose first column
  names its rows, on a card; the chart "view data" table sticks its own). It wraps the report and every "view data"
  table (`ChartDataTable`), Budget, the household P&L, Explore's month-over-month, Recurring's and Balances' desktop
  tables, the holdings, drift and target-mix tables, the Data sources and Second house's funding plan and comparison.
  A hub's tab bar fades out at its right edge while more tabs lie that way. A table that must stay readable gets a
  stacked `md:hidden` list beside its `hidden md:block` table (Recurring, Balances). The register picks its own layout
  by width (`docs/design/register-and-answers.md`).
- A table or grid inside a card folds by the CARD's width with a Tailwind container query (`@container` plus `@xl:` /
  `@max-sm:` variants — the Second House funding plan); slider grids use `repeat(auto-fill, minmax(min(…, 100%), 1fr))`
  (`AssumptionsPanel`, the projection-defaults card).
- Section grids step `sm:grid-cols-2` before `lg:grid-cols-3/4` (no 1→3+ jumps); inputs are at least 16px on a phone
  (`text-base sm:text-xs`) to avoid iOS focus-zoom. A tap target is about 44 × 44 CSS px below `md`, its hit area
  grown around a glyph that keeps its size: `TAP` (`components/ui.tsx`, `max-md:` only, so nothing moves from `md`
  up; the one tap-size constant, Reports' saved-report links included) or a `min-h-11 … md:min-h-0` pair - the
  date-range chips and boxes, segment toggles, the top bar's buttons, the hub tabs, the drawer's rows, "view data" and
  "⬇ CSV", the register's row actions and boxes, Recurring's row actions, the manual-assets editor's controls, the
  modules switch, the page-moved buttons, the reload and retry buttons of a data notice, Second house's scenario chip
  and its glyph buttons (named in words). A control whose look keeps its own smaller size - a pill (`pillCls`, 36px),
  the freshness pill - takes `TAP_AREA` instead: a transparent 44px-tall box centred on it, with 8px between rows of
  them on a phone so no two hit areas overlap; a pill drawn inside a 44px button (Recurring's "missed only") needs
  nothing. Still at 40px (about 44), each a `min-h-[40px]`: Data's links, the category editor's buttons, the filter
  bar, the pickers and the Save box, the palette's rows, the saved views, Home's and the month-so-far links, the
  target mix, the first-run and module-off pages, the vendor drill, the bulk bar, the pref rows, the privacy card and
  Settings' links.
- Hub tab bars scroll sideways (`overflow-x-auto`, tabs `shrink-0 whitespace-nowrap`), with the keyboard ring drawn
  inset (`-outline-offset-2`) because the scroller clips an outset one.
- The FilterBar on a phone opens collapsed on every mount to one summary line (`filters/filterSummary.ts`: the window
  and a lone other filter named, else "N filters" — an amount filter is named, never its figure), sticky under the top
  bar only while collapsed; `filterbar-toggle` opens the full controls (`filterbar-controls`). From `md` up it is the
  full bar.
- Explore's daily calendar on a phone stands on its side (a column per weekday, a 44px row per week, the newest three
  months with spending), so a day is a finger's target; the chart is as tall as its weeks (`weeksSpanned`).
- Long lists cap on a phone: Home's next 30 days shows five rows then "N more"; the Review inbox pages by fifty; the
  register scrolls with the page (no box of its own), says how many transactions it holds and pages by fifty with
  "Show N more".

## Charts (`components/EChart.tsx`)

Route every chart through `EChart`. On a phone it caps the height at 240px (a chart that needs its room states
`phoneHeight`: the money-flow graph keeps its own, the calendar is as tall as its weeks) and runs `tuneForMobile()`
(`lib/echarts.ts`) on cartesian options: a compact legend that wraps like the desktop's (10px names, 14px icons, its
rows counted at the box's width and added to the chart's height, a name over 200px cut in the middle, never paged),
axis labels at 10px (a smaller author size stays), the last label of a category axis kept (`showMaxLabel`; under a
chart of lines alone it ends at its point, `alignMaxLabel`, rather than hanging past the edge), overlaps hidden unless
the author set `hideOverlap: false` (Home's waterfall, whose labels are already sized for 390px by `waterfallLabels`),
confined tooltips, and `markPoint` pins thinned only on a series that opts in with `phoneMaxPins`. A legend-bearing pie
drops its slice labels for that legend under it, and keeps its size when the legend takes more than one row
(`withLegendRows`).

- `EChart` creates its chart only once its box has a real size and re-fits whenever the chart's size disagrees with the
  box (ResizeObserver, window resize, the phone/desktop flip); it never collapses to 0 while hidden — don't call
  `resize()` from a page. Under `prefers-reduced-motion` (`useReducedMotion`) it sets `animation: false`; the screenshot
  driver emulates that, so no PNG catches a chart half-drawn.
- Text on the canvas is shown whole or cut in the middle, never to a shared prefix. A treemap tile is labelled only when
  its whole label fits the box ECharts laid out (`fitTileLabel` against `treemapTileBox`, recorded by
  `TREEMAP_TILE_STAGE`, keyed by the series `id`: name and value, else the name, else nothing — so a phone's small
  tiles go unlabelled and the tooltip names them); the label carries the drill-down ▶ itself, so the series sets
  `drillDownIcon: ""`. The label is drawn uncut (`overflow: "none"`) and `treemapLabelFit`, the series' `labelLayout`,
  hides one that does not fit the tile it is drawn on: in the browser the recorded box can belong to an earlier layout,
  and only that check, on the rects drawn, keeps ECharts from cutting a word ("▶ Trans..."). A legend cuts an over-wide name with `middleEllipsis`, keeping what follows the last "..." or the
  last word; `textWidth` measures on an OffscreenCanvas or estimates wide.
- A chart legend of many entries or long names is `wrapLegend()`: from `md` up it wraps onto as many rows as it needs
  and never pages (a plain legend in a box 92% of the chart wide); `EChart` counts its rows at the box's width
  (`legendRows`, measured as ECharts lays the legend out) and moves the plot down and makes the chart taller by each
  row past the first, so the plot keeps its size. On a phone every legend wraps the same way, in the compact metrics
  above (`legendRowStep`: 18px a row there, 24 on a desktop). A legend lists only what
  the chart draws: a series with no point yet (Cash Flow's rate line before a full month) has no entry
  (`drawnLegend`). Cash Flow's savings-rate axis (`rateAxis`) runs −50% to 100% in even steps; a month below its foot
  is drawn at the foot as a downward triangle with its real rate in the tooltip (`pinRates`), and a line under the chart
  says so. A rate line every rated month of which would be pinned (a household with no income in the window) is not
  drawn: no legend entry and no rate axis, each month's rate still in the tooltip and the table, and a line under the
  chart saying why. The money axis beside it (`moneyAxisBesideRate`) shares its grid - six even steps, $0 on the line of 0% -
  only when that costs nothing (the tallest bar above zero at least as tall as the deepest below); otherwise it takes
  even steps of its own, its top the first step at or above the tallest bar. Steps come from `STEP_LADDER` (1, 1.5, 2,
  2.5, 3, 4, 5, 6, 8, 10 times a power of ten); no bar is clipped either way.
- An account has one colour on every chart: its place in the household's account list (`lib/tokens.ts`
  `accountColor`), on the balance charts (`lib/balanceCharts.ts`) and on a report grouped by account; categories,
  classes and owners keep their own palettes. A balance chart draws every point its history holds, and prints money
  compact on the axis and whole in the tooltip. A history can START with one balance a month (its first two gaps or
  more each a month or longer: statement files, the feed's early reports) and END with frequent ones: the line moves
  more where the points are frequent (a card's payment dip shows), and one line under the chart says where the spacing
  changes (`components/BalanceSpacingNote.tsx`, from `monthlyThrough` and `chartSpacing`, the date the last point of
  the leading monthly run): "One balance a month through <date>; more often since." when every line turns on that
  date, else "Some accounts have one balance a month in their early history, the latest through <date>; more often
  since.". Any other history - monthly throughout, frequent throughout with outages or without - gets no line.
- A value axis is `valueAxis(formatter)` (`moneyAxis` for money): its labels and split lines in the theme's colours, never
  ECharts' light default. Which line charts leave zero is one rule (`leavesZero`, spread through `levelScale`): only a
  level that never nears it - every value drawn above zero and the smallest at least half the largest - with a
  `minInterval` its labels tell apart over the narrow range (`compactStep` for money, a whole percent for a share; a share
  never past 100%, `shareBounds`). Home's net-worth lines, Trends' liquidity lines and the balance charts take it; an
  amount owed and a utilization keep zero (`keepZero`: zero is the goal); a bar chart and a stacked area keep the zero
  baseline, where it means something.
- Axes say their unit of time. A date axis is `timeAxis()`: its labels are dates, never clock times; a year's first
  day reads the year, a month's first day the month, any other day its number, and the first labelled day of each month
  names the month ("Oct 27"), so an axis of day numbers says which month it counts. A chart whose points all fall on
  one date spans that day alone and is labelled with the date in full. Months on a category axis are `monthAxis`
  ("Oct ’26").
- Tooltip text built from data is escaped (`encodeHTML`).
- Color-bearing option fragments in `lib/echarts.ts` are functions, so a theme swap re-reads them.

## "View data"

`EChart` takes an optional `tableData: ChartTable` (`lib/chartTable.ts`; `categoryTable` / `timeTable` build one) and
draws a "view data" footer: a real table (`components/ChartDataTable.tsx` — caption, row headers, a sticky first column,
a gap is "—" and never 0, numbers through the table's `format`, so privacy masks them) plus a CSV of the RAW rows with
a visible "real amounts, never masked" note. Build the table in the same memo as the option, from the same arrays, so
they cannot disagree.

## Checking a layout change

`tools/verify/screenshots.sh <label> <exports-dir>` shoots every `PAGES` route at 390×844 and 1280×900, dark and light,
and at 390 in each theme Home with the navigation drawer open (opened by the menu button's accessible name,
`root_drawer__390x844__<theme>.png`), plus each route the driver's `CLICK_SHOTS` table names once more for each of its
clicked shots, with their controls clicked first (Settings with the manual-assets "Add an asset" form open,
`settings_add_asset__<WxH>__<theme>.png`, and with the AI categorization dialog open,
`settings_ai_dialog__<WxH>__<theme>.png`, at both widths in both themes), each page's clock fixed at the exports' ingest instant (`manifest.json`
`generated_at`, so a shot does not depend on the day it is taken), in the household's zone (`assumptions.json`
`household.timezone`; UTC, with a line saying so, without one) and once its fonts are ready, and exits 1 when
`overflow.json` is not empty (horizontal overflow, a page error, a failed load or fonts not ready in time anywhere is a
failure, not a baseline). The static site answers the store's documents as an idle, healthy stack does (empty
preferences, answers and manual assets, an idle refresh status with the scheduler's default schedule, synthetic scenarios), so
no shot shows "Store unreachable" or a disabled Refresh. It treats exports as real: the exports dir must lie inside the scratch root and every shot is
taken with privacy mode on, which protects the files at rest. No agent opens a PNG produced from real exports, masked
or not: on real exports the script is run for its exit status and `overflow.json` only, and exports at or inside a
real-data scratch root are real whatever a flag says. `FF_DEMO_EXPORTS=1` lifts two rules, the scratch-root
requirement for the exports and the forced masking, and is for synthetic exports only: it never lifts the location rule
(exports at or inside a real-data root are real) and never makes a picture of real exports readable. With it
`FF_SHOT_OUT` names a directory of the caller's for the PNGs, empty or not there yet (it is then created), outside the
repository and the real-data scratch roots, and `FF_SHOT_STATE=1` serves the answers and manual assets of the demo
tree the exports belong to (its `state/`, beside its `truth.json`, the generator's mark; the variable takes no path,
so it can never name a real household's state) in place of the empty ones (real exports are always shot with the
empty ones). The demo households' shots (`tools/verify/demo.sh <dir> full new`, then `FF_DEMO_EXPORTS=1
FF_SHOT_OUT=<an empty or new dir> FF_SHOT_STATE=1 tools/verify/screenshots.sh demo-full <dir>/full/data/exports`:
19 routes, 76 + 8 + 2 = 86 PNGs) are taken at their ingest instant and read. `FF_SHOT_ROUTES`, `FF_SHOT_VIEWPORTS` and `FF_SHOT_QUERY` narrow
or vary a run (`tools/verify/README.md`). It cannot see clipping inside a scroller, so read the PNGs too — of a demo
household only. `FF_SHOT_NOTHING_BUILT=1 tools/verify/screenshots.sh <label>` shoots the one page no exports can show,
the one a household sees before any export exists: the built dashboard served with no export, at both widths in both
themes (4 PNGs), each shot once that page's heading is on screen; it reads no exports directory, so its PNGs are read
too.
