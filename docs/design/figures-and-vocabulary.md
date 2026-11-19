# Shared figures, category vocabulary and formatting

> Scope: the one definition behind each figure several pages show, the app's category vocabulary, the budget grid and
> report builder semantics, and number/date formatting. Describes the system as it is; verify names against the code.
> Paths are relative to `app/src/`.

## One definition per figure

A figure two pages show is computed in one `lib/` function and both read it; a new page reuses it rather than
re-deriving it.

- **Mortgage balance** — `mortgageBalance(a)` (`lib/lifeSummary.ts`): `assumptions.mortgage.balance` and its date, the
  `mortgage: true` loan account's own row in the current snapshot (positive dollars owed). The bank feed is its one
  source. 0 is a paid-off loan and the pages say so; `null` (no mortgage account, `assumptions.mortgage` itself null, or
  no balance from the feed yet) prints "—" and says which, never 0. Liabilities, Planning → House, Balances and Home all
  print it; a rewound Home (`?snap=`) keeps that snapshot's own row.
- **Loan terms** — the account's `loan:` block in `accounts.yaml` (`rate` as a percent, `monthly_payment`,
  `monthly_escrow`, `escrow_balance`, `origination`, `term_months`), exported in the same block (the rate as `rate_pct`),
  each `null` when absent; nothing estimates a missing one. A figure says where it comes from in plain words ("from the
  bank feed", "from your loan terms"), and a term a figure needs and the account lacks is named with the key to add
  (`missingLoanTerms`). `mortgageSummary` (payoff year, interest cost) needs the balance, the rate and the payment; with
  one of them missing Liabilities' card shows what is known, a known rate with its interest cost (`dailyInterest`).
- **Home value** — `homeValue(cur)` (`lib/lifeSummary.ts`): the sum of the snapshot's House-class rows (`HOUSE_CLASS`,
  the home as entered by hand: in Settings → Manual assets or as an `accounts.yaml` `manual_entries` item); `null`
  without one, a row of 0 is 0. No assumption or preference stands in for it (`assumptions.yaml` refuses a
  `projections` figure for it at load). Equity and loan-to-value (`mortgageSummary`, `loanToValue`, which never divides
  by a value of 0 or less, printed at one precision by `fmtLoanToValue`: a tenth of a percent) read it on Liabilities and
  Planning → House, and print "—" and the one sentence without it
  (`components/ManualAssetsLink.tsx` `NoHomeValue`, a link to the Manual assets card); Planning → House's what-if
  slider then has no row, and otherwise runs from half the value to twice it (`homeValueSlider`). The Data page lists
  it beside the assumptions while there is one. A rewound view reads its own snapshot's rows.
- **Prepaying the mortgage** — Liabilities' card and Planning → House name the loan's own rate and what a prepaid dollar
  saves at it ("each dollar prepaid saves 6% a year in interest"), beside the margin loan's stated rate when there
  is one; nothing compares the loan with a market instrument the data does not price. House's prepay-vs-invest card
  sets an extra amount a month (the page's Extra monthly until the card's own box is set; with neither its figures
  read "—") against the loan's rate and the rate in the card's own control, which starts at the middle return
  scenario like Planning's one-rate returns (`lib/useGrowthSeed.ts`, tagged "default" the same way), and says the
  difference in neutral words ("at these rates, investing ends $X higher than prepaying"), all three in whole dollars.
  Both sides are on one footing: the prepaid side grows at the loan's effective yearly rate (`loanEffectivePct`, the
  APR accrued a twelfth a month, as `amortize` charges it), the invested side at the yearly rate set, both before tax.
  A rate or payment the loan terms leave out is an empty box until the person sets it (0 included), and emptying the
  box again makes it unstated; a stated payment is used to the cent and printed as it is used (`fmtUSDExact`: cents
  when it has them, in the footnote and the error box). House's chart draws current pace whenever there is a projection, the
  extra-payments line only with an extra payment or a lump, and the history only with points; its legend lists what is
  drawn. A payment that does not cover a month's interest is one error, and every hint beside it says the same.
- **Second house's funding verdict** — `fundingState` (`lib/secondHouse.ts`) decides it once for the funding card, the
  scenario tabs and the comparison: "fully funded" (green, with the check) only when the draws add up to the cash
  needed, to the cent, no draw takes its account below zero (an account already below zero with no draw is shown at
  its balance and is no overdraw) and no draw rests on a balance nobody knows. Otherwise the line says what is true, in
  this order and joined by "; ": the accounts a draw overdraws and by how much (three named, then "and N more"), the
  draws on an unknown balance ("cannot be counted on"; such a draw's box stays open until it is cleared, then closes),
  then the totals when they do not match ("funded $X of $Y; $Z short", or the excess, saying how much of the total rests
  on an unknown balance: "(incl. $500 on a balance that is not known)"), so an overdraw never hides a shortfall; the
  comparison's Funding cell carries the same facts.
  One tone per state on the line, the tab's mark and the cell: amber for an overdraw or an unknown balance, red for a
  shortfall, untoned for an excess, green only for fully funded. A scenario that does not close is marked on its tab
  and in the comparison's header, and never wins "Liquid cash left". An account is labelled by its class, never its
  kind; "cash draws" are the Cash-class accounts' draws, and liquid cash left below zero (by a cent or more) is a
  shortfall in words. The page says of mortgage insurance only that its cost is left out: one untoned tile at every
  down payment, "none in the payment shown", and under 20% down the sentence that a lender may require it. The rental
  and cost lines are
  rounded for reading and each total is the rounded sum of the unrounded lines (rounded once), so the shown lines may
  differ from the total by a dollar or two; each
  line shows its own factors exactly ("12.96 nights × $285"). The cost chart's axis reaches every dashed line its
  caption names.
- **The mortgage across snapshots** — the ETL builds a snapshot on every month end from the first account observation
  and one on the current date (`etl/networth.py`); the dashboard shows those comparable with the newest
  (`lib/comparableSnapshots.ts`). Each books the `mortgage: true` account's row at its last balance on or before the
  date, carried; a snapshot before the account's first balance is flagged `mortgage_included: false`.
  `assumptions.mortgage.schedule` is that account's row at every snapshot date it has one (`{date, principal_balance}`,
  oldest first): Planning → House plots it (`balanceHistory`), and `decompose` takes the paydown it shows when a window
  crosses the flag. Every † note that explains such a snapshot (Home's delta chips, footnotes and chart table, the
  decomposition, Data's caveats) reads `mortgageGap(snaps, schedule)` (`lib/decomposition.ts`) for the month the data
  starts, and prints nothing once every snapshot subtracts the mortgage. Trends' liquidity needs none: a share of
  assets holds no debt, so such a snapshot reads the same as one that subtracts it.
- **The balance sheet, by sign** — one definition on every page: a snapshot's ASSETS are its rows above zero and its
  LIABILITIES its rows below zero, so their totals are the export's `assets_total` and `liabilities_total`. Assets sum
  by class, and a debt-class row above zero (a card in credit: the issuer owes the household) is a line of its own named
  for its account (`inCreditLine`: "Card Two (in credit)"), never an asset called by a debt class's name; liabilities sum
  by debt class, and a row below zero in an asset class is a line of its own named for what it is (`belowZeroLine`:
  "Joint Checking (overdrawn)", "Boat (below zero)"), never netted inside its class. Home's donut and Trends' mix
  (`assetsBy`) name the card in credit the same way. What the cards owe never nets a card in credit against another:
  Liabilities' "Credit cards" tile reads what is owed, the same figure with cards in net worth or left out (`cardHeadroom`
  `debt` sums each card's `max(0, owed)`), and says how many are in credit; Balances' card subtotal is what the cards
  owe and says the same. `balanceSheet` (Balances' two sheet cards), `netWorthDebt` (Home's
  "Debt in net worth", Balances' Total liabilities), `liquidity` (the Liquidity card's Debts) and `debtOverview`
  (Liabilities' Total debt) read it, so Total liabilities, Debts and the debt in net worth are one number; a test holds
  that on a snapshot with an overdrawn account and a hand-stated value below zero, and holds the sheet's totals equal to
  the export's on every snapshot of the demo household.
- **Debt** — `netWorthDebt(a, cur, latest, cardIds)`: every row below zero, the mortgage read through `mortgageBalance`.
  Home's "Debt in net worth" tile and Balances' total liabilities show it, worded by `netWorthDebtNote` (what is owed:
  the debt classes, then each account below zero by its line; "cards not included" when
  `assumptions.net_worth.include_credit_cards` is off, "cards included" when it is on
  and the snapshot holds a card row, "no credit card" when it is on and the household has no credit-card account —
  never that card balances are not available, as Liabilities' tiles say "No credit card yet."). Liabilities' Total
  debt is `debtOverview(a, cur, cards, includeCards, cardIds)`, by sign as well (margin, other debt and an account below
  zero are rows below zero; with cards in net worth, a card in credit owes nothing):
  with the flag off, net-worth debt + the balance history's card balances (and says so); with it on, the snapshot's
  card rows (rows of a `credit-card` account, found by account, never by class) are the cards and the history is not
  added. Net worth itself is the ETL snapshot's total, read from its one series map (`etl/networth.py`); the pages
  compare only the snapshots comparable with the newest (`lib/comparableSnapshots.ts`: a month end that misses an
  account the newest has is left out), and Data's caveats list the others (`partialSnapshots`, `leftOutMonthEnds`):
  newest first, each with the accounts it has no balance for that the newest has (at most five by name, linked, then
  "and N more"), behind a count past six month ends. An account the newest snapshot misses too (configured, its first
  balance not arrived) is said once, in its own sentence, with what brings it.
- **Cash** — `liquidCash(cur)` / `liquidCashRows`: cash on hand, the NET of the liquid Cash rows (an account below
  zero is taken off it; the escrow-like rows in the Cash class are not spendable), read by Balances and Planning's
  runway and forecast. Its sub-line on Balances says so when a cash account is below zero ("net of one below zero") and
  counts the rows the table below lacks in their own words: a value entered by hand has "no bank feed"; an account's
  row the balance history does not hold "has no balance history yet" (never "no bank feed": an account is a feed's or
  a statement's, and the table lists the accounts with a history).
- **Liquidity** — `liquidity(cur)` (`lib/lifeSummary.ts`), in whole cents: **Liquid assets** are the liquid rows above
  zero (`liquid_total`, which sums every liquid row, less a liquid row below zero, an overdrawn account, which is a
  debt); **Illiquid assets** are the
  asset rows above zero that are not liquid, by each row's own `liquid` flag (the engine's `ILLIQUID_CLASSES`, a hand-
  stated asset's own flag; the app keeps no second list): `assets_total` less the liquid assets, never net worth less
  the liquid part, which hid every debt inside it and read negative once debts passed the illiquid assets; **Debts**
  are their own line, `−liabilities_total`. Liquid + Illiquid − Debts = net worth to the cent (a test holds it on every
  snapshot of the demo household's exports and on one whose debts pass its illiquid assets). Every **liquid share** is
  of ASSETS (`assets_total`), never of net worth, so it never passes 100%, and its label names the base ("of assets"):
  Home's Liquid assets tile, Balances' Liquidity bar, Trends' liquidity line (`liquidityTrend`); with no asset above
  zero there is no share. Home's allocation donut and Trends' allocation mix (`allocationTrend`) divide the same base:
  the snapshot's rows above zero by class or by owner (`assetsBy`), so a debt is in no slice and an owner's slice is what
  it holds, not its net worth. While the snapshot misses an account (its `missing`), a figure of an incomplete total
  says so with one mark, `notYetReported` (`components/NoData.tsx` `NotYetReported`, a link to Data's caveats, which
  name the accounts): Home's liquid share and donut count only the accounts that would hold assets
  (`missingAssetAccounts`: by the account's kind, so a missing loan — the configured mortgage — or card moves no share
  of assets) and read "of the assets reported so far — N accounts not yet reported"; Balances' Liquidity card, which
  prints the debts too, counts every one and reads "of the balances reported so far — …". Balances'
  Illiquid tile names the classes it holds, and its Liquidity card lists the rows Illiquid sums, each with its holder
  (`rowHolder`: an asset entered in the app names none, so "entered by hand") and, for a value older than the
  snapshot, "as of" its date; past eight rows the rest are one line with their count and sum.
- **A margin account's two figures** — the feed's balance, net of the margin loan (Balances' row, the account page's
  balance and its history), and its positions (its rows of the holdings' classes, Investments' holdings);
  `marginBreakdown(cur, id)` is the positions and the loan of an account the newest snapshot holds as positions beside
  a debt row. Wherever both can
  appear each is named for what it is: "net of the margin loan" on Balances' row and the account page's balance, with
  "Positions" and "Margin loan" beside it there; never two figures both called the account's balance.
- **Card utilisation** — `utilization(balance, limit)` (`lib/credit.ts`): what a card owes over its limit, read from the
  balance and the limit rather than the export's rounded ratio; null without a limit above zero ("—", never 0%), printed
  by `fmtUtilization` at one decimal. Balances' table (a card's row) and Liabilities' Available credit tile and its
  chart (a point with no limit is left out; its tooltip prints `fmtUtilization` too) read it. The tile's one count
  ("N of M cards", `cardHeadroom` `known`) is the cards whose headroom is known, and its utilisation (Σ owed ÷ Σ limit)
  is given only when every one of those cards has a limit, so the count describes both figures. A loan row shows no
  available credit and no utilisation: a feed's "available" on a loan is its balance again.
- **Balances' current-balances rows** — a balance older than the date the card's badge shows (the newest balance in
  the table: with cards left out of net worth a card can report after the snapshot) says "as of" its date, in amber, in
  the table and on its phone card alike; a balance of that date carries no date of its own.
- **Like with like over time** — every snapshot and the daily line read net worth's one series map
  (`etl/networth.py`), and the current snapshot alone also reads the newest feed file's holdings and live accounts, so
  the newest snapshot holds a live margin account GROSS (its positions plus a debt row for the loan) while every month
  end holds it as one row at the feed's balance, NET of the loan.
  `onMonthEndFooting` (`lib/comparableSnapshots.ts`) gives the newest snapshot on the month ends' footing: such an
  account becomes that one row (`foldedDebt` finds its debt row), and the class and owner totals, `liquid_total`,
  `assets_total` and `liabilities_total` follow; the total does not move. Every SERIES or CHANGE that sets the newest
  snapshot beside month ends reads it (`onOneFooting`): Trends' liquidity line and allocation mix, Home's by-class and
  by-owner history and the waterfall (Home's comparison chips and change tile read totals, which the fold leaves as
  they are). The "now" tiles and the balance sheet keep the snapshot as it is (positions and the loan apart, as
  Liabilities shows the loan). A card whose last point the fold moves says so once (`footingNote`: "Month ends hold a
  margin account net of its loan; the last point is drawn the same way, so it can differ from today's figures …").
  With one snapshot there is nothing to set it beside, and it stays as it is.
- **The net-worth decomposition's debt bar** (Home, `card-decomposition`) — `decompose` reads both ends on the month
  ends' footing, so a live margin account's loan is in neither end's debt and its change lands with the account's, in
  "market & other", and the card's footnote says so (`marginFolded`). The bar is named by its sign (`debtBarLabel`):
  "Debt paid down" when debt fell, "Debt added" when it rose, "Debt unchanged" when it rounds to no whole dollar (under
  fifty cents either way, as the bar's own figure rounds), never a bar named "Debt" whose sign the reader decodes. The
  bar reads the debt classes; an account below zero moves with cash savings, whose bar already holds its movement.
- **Hand-stated values carried back** — before its first value a hand-stated asset is held at that value (rows'
  `carried_back`; from the day it was acquired, when it states one). `carriedBack(snaps)` lists each such asset once,
  with its first stated amount and day, and where the history holds one, Home's history names each (`carriedBackLine`:
  "Sedan is shown at its first stated amount ($21,000, stated Mar 26, 2026) in earlier month ends.", the amount masked
  in privacy mode; past two assets, "and N more hand-entered assets, each at its first stated amount in month ends before
  it was first stated"). Data's hand-entry caveat says how many month ends hold such a value (`snapshotsCarrying`).
  Both read the month ends only, every snapshot before the newest: a value stated after the newest account balance is
  carried back into the current snapshot too, which is no month end.
- **Bear case** — `SHOCK_DEFAULTS` / `shockedTotal` / `bearCaseTotal` (`lib/forwardLook.ts`): Planning's bear-case KPI
  and its stress-test card share the table, so the sentence and the card cannot disagree. A warning tone needs a fall:
  the KPI is toned, and says by how much, only when the case lowers net worth by a dollar or more; when it changes
  nothing it says no class it moves is held, naming them from the same table (`bearCaseClasses`). The card's paper loss
  reads "—" when the snapshot holds nothing any slider moves (`holdsShockable`); sliders moved to no fall give a true,
  untoned $0.
- **Renewals** — `isLumpy` (`lib/obligations.ts`, `docs/design/schedules.md`).
- **Savings rate** — `savingsRateLabel` names a trailing-window rate ("Savings rate (12 mo)") everywhere, with the full
  months it really reads ("(1 mo)" on a household with one); with no full month the tile is "Savings rate", "—" and
  not enough history yet. Cash Flow's "Best month in this window" and "Worst month in this window" (`savingsKpis`
  `inWindow`, `best`, `worst`) are of the full months of the window the page shows, while its trailing rates keep the
  whole history; with fewer than two rated months in the window they are "—" and say why (not enough history yet, fewer
  than two full months in this window, or fewer than two with income to rate). The monthly chart draws the month in
  progress's bars and no savings-rate point for it (nor a total-rate point): the line ends on the last full month. Its
  tooltip counts a month's reporting accounts out of the cash-flow accounts with any transaction, the ones full
  coverage waits for. When the data is behind (`dataBehindIngest`: a whole month lies between its newest month and the
  ingest month), the footnote says the data ends in that month and later transactions have not been read, never that
  its bills are still coming in; the month just before the ingest month keeps the ordinary wording (on the ingest
  month's first days its rows are still arriving).
- **Planning run-rates** — `runRateWindow` (`lib/agg.ts`) over `runRateMonths`: the full months, less a first month the
  data covers only in part (the data's first day, `dataFirstDay`, after its 3rd: `coversFromStart`). The FIRE number's
  core spend (`trailingYearCoreSpend`) and the savings pace (`savingsPace`, `lib/lifeSummary.ts`: the net-worth prefill,
  the stress test's years of saving, Second house's free cash flow) read the trailing twelve such months, the emergency
  fund's essentials and the runway six (`emergencyWindow`), the forecast its 3, 12 and 24 (`forecastCashflow`, which
  needs six), each cut at the first; the run-rates need `RUN_RATE_MIN_MONTHS` (3) of them; the spending-cut card reads
  none, but starts its cut at 500 only with three. With fewer, Projections says ONE line at its top (what it plans from,
  what each part needs, `historySoFar`: how many full months there are and the day the next one ends,
  `nextFullMonthEnds` — the month in progress's last day, the next month's when the month in progress is a partial first
  month, and no date when the data stops short of the ingest month: "the count grows once later transactions are
  read"), its gated tiles read a bare "—" and each gated card one short line ("Not enough history yet."); Second house
  says its own line under the cushion. From three to eleven a figure is averaged
  (or annualised) over the months there are and says how many ("from 5 full months"), never "trailing 12". A pace at or
  below none is said in words ("Income did not cover spending over the last N full months") and counts no years of
  saving; a positive one counts them at its real figure, which the tile names, while the slider starts at it rounded to
  its step. A slider's amount is SET when seeded from a real positive pace (or, for the cut, from three full months) or
  set by the person — the slider moved or an amount typed, none included — and unset until then: an empty box
  (`CurrencyInput` with `value` null), the slider at its minimum, and what rests on it waits, "—"; emptying the box
  unsets it again, "reset" returns to the start. The retirement projection draws only the adults with a mapped retirement account that has reported (a
  balance of $0 included); with one adult the card is theirs. No retirement contribution is assumed (no configuration
  key states one): each projected adult's starts unset, so their projection and the FIRE crossing are today's balance
  growing alone, and the line under the chart (`retirement-basis`) and the crossing say so until the person sets an
  amount. An amount typed lasts for the visit (no configuration or preferences key keeps it yet), and the line says so.
  The crossing grows at a nominal rate against a FIRE number in today's dollars, and its line says the real crossing
  is later.
- **Projection defaults** — "default" marks only what a household can clear. A small tag
  (`components/DefaultTag.tsx`: the word, never a colour alone; its title says which default it is and where it
  changes) sits beside a value that has a `projections` key the configuration (`assumptions.projections_stated`) and
  Data's Projection defaults card (prefs `projections`) both leave unstated — `projectionSource`
  (`lib/projectionDefaults.ts`) — and beside each figure that rests on it, linking to that card
  (`?card=projection-defaults`): the withdrawal rate (the retirement card's slider until the person moves it, the FIRE
  number and the FIRE crossing) and the return scenarios (the retirement chart's line). Every one-rate return on the
  page — the net-worth projection's and the spending cut's starting return and the FIRE crossing's rate (named in its
  label, "@7%") — is SEEDED from the middle of the return scenarios (`middleReturn`, through `lib/useGrowthSeed.ts`, which
  Planning → House's prepay-vs-invest card reads too), so it is the household's own when
  that key is stated and carries its tag (on the control, the chart's line and the cut's three tiles) when it is not.
  The retirement age (65) has no key yet and keeps its tag, on its control and in the chart's line, only while it
  shapes the projection (some projected adult has a birth year). The horizon (30 years) and the cut's amount (500) are
  what-if inputs, like the stress test's shocks: untagged, and their figures say what they assume in words ("for 30
  years", "What cutting $500 a month grows to"). A household that states every key sees two tags (the age); one that
  states none, twelve. A default is never called "yours" or "your actual". Data's Projection defaults card says of
  every key whether it is saved there, stated in `assumptions.yaml`, or the default.
- **The cash-flow forecast's first month** — the forecast starts from the newest snapshot's liquid cash, which already
  holds every day up to the snapshot's date (a bank feed's balance can hold a day or two the transactions do not show
  yet), so the forecast's first point is only the REST of the month that date falls in: its baseline income and spend
  prorated to the days after it, and only the scheduled charges dated after it (`forecastStart`, `lib/forecast.ts`,
  anchored on the balance's date, never on the transactions' last day); on the month's last day nothing is left and the
  forecast starts with the next month, whole. The band widens with the months elapsed (a prorated first month counts
  as its share). The caption names only what the chart draws (the line; the band only when the net varied; the
  essentials floor only when there is one) and says where the forecast starts.
- **Gauges** — a value past a gauge's range is printed in the gauge's text and the needle is pinned at the dial's end,
  whose tick then reads with a "+" ("12+"): `gaugeReading` (`lib/echarts.ts`), on Projections' emergency-fund gauge.
- **This month so far** (Home, `card-month-to-date`) — `monthToDate` (`lib/monthToDate.ts`): the spend definition above
  (`isSpend`, refunds netted, integer cents) on the category view (a split parent is never added to its parts) from the
  1st of the data's month through its last COMPLETE day (`lastCompleteDay`: `dataThrough`, or the day before it when
  that is the ingest day, whose spending is still coming in; on the 1st that leaves no complete day and the card says
  so), against the MEDIAN of each of the previous `MTD_PRIOR_MONTHS` (6)
  months' spend from the 1st through the same day (the whole month when it is shorter). A prior month is compared
  whenever it is a full month: it is left out only when it is before the coverage floor (`coverageFloor`; with no
  month in coverage none is compared), when the data covers it only in part by the app's one rule (`coversFromStart`:
  the data begins after its `COVERED_FROM_DAY`-th day, so a statement whose first row is on the 2nd counts its month)
  or when it holds no transaction at all (a feed gap, unknown — a covered month with no spend is a real 0); "No earlier
  month to compare with yet" is said only when none is left. The pace reads "about typical"
  within ±10% (`MTD_ABOUT_BAND`). In the first `MTD_EARLY_DAYS` (6) days the card keeps the same comparison and says it
  is early and noisy, rather than switching to "the same days last month": one month's same days are noisier still,
  and a baseline that changed meaning on the 7th would be harder to read. Read-only: no budget, envelope or manual step.
- **Investments gives one piece of advice, or none.** The household's OWN asset-class targets (`card-class-drift`: prefs
  `targets` saved in the app, which replace the configuration's whole, else `targets:` in `assumptions.yaml`) are the
  one card that says how far a class is over or under and what to move. Each target is a share of NET WORTH (every
  asset less every debt, as `docs/configuration.md` states the key; not the share of assets Home's allocation divides
  by), and the card says so and says where its targets come from and what they add up to as a share of net worth, with
  no push toward 100: on this base the classes a household holds add up to more than 100 and its debts take the rest,
  so the line names, from the snapshot, the classes with no target and the debts that make up the rest ("They add up to
  80% of net worth; the classes with no target (House, Car) and the debts make up the rest."), and the editor says
  targets need not add up to 100. With a net worth at or below zero a share of it means nothing: one line says so. It
  is on the page for every household: with none stated it says so and offers its editor ("Set asset-class targets"),
  never a drift against targets nobody set.
- **Target mix** (Accounts → Investments, `card-target-mix`) — `lib/targetMix.ts`, one vocabulary: Stocks, Bonds, Cash,
  Alternatives, as shares of the CLASSIFIED positions: the positions the page lists. INVESTABLE ASSETS are the "Total
  invested assets" KPI: the positions and the balance-only accounts together (the house, cars, bank cash and debts are
  outside both); the two names are used for nothing else on Investments. An account that reports a balance but no
  positions (a balance-only line) is NOT CLASSIFIED: its split is unknown, so no bucket counts it and the card names it
  on its own line with its dollars, its share of investable assets and the reason; a saved mix's verdict then says how
  much of the investable assets it covers ("covers 48.3% of investable assets; the rest is not classified"), and with
  no classified position at all the card says "Nothing classified to compare yet." with the reason
  (`nothingToCompare`) in place of a verdict or of "Save a mix to measure the holdings against it".
  `mixBucket` buckets a position (bond-like funds are bonds; the Crypto, Precious Metals, Private Stock and Real
  Estate/Venture classes are alternatives; Cash-class holdings are cash; every other position stocks); `mixRows` gives
  each bucket's actual share, drift in points (positive = over, `fmtPoints`: signed, never
  "−0") and the dollars to buy or sell (`moveWords`); `mixSummary` is the one-line verdict (within
  `MIX_TOLERANCE_PTS`, 5, on every bucket, else the bucket furthest off, its drift to one decimal and its dollars).
  The rule and the table's amber read the drift as printed (`shownPts`, `isOff`), so "+5.0 pts" is within and the
  summary never names another size. A mix with more than
  `ALTS_GUIDANCE_PCT` (10%) in alternatives — saved or previewed — is flagged in words against the common guidance to
  keep speculative assets under ~10%. Sliders set stocks, cash and alternatives in whole percents and bonds take the
  rest (`withLeg`), so a mix always totals exactly 100. Until the family saves a mix (prefs `target_mix`,
  `docs/design/data-and-sync.md`) the card shows a SUGGESTION (`lib/allocation.ts`: a common rule of thumb, 110 minus
  the oldest adult's age in stocks, 5% cash, 5% alternatives, bonds the rest; a fixed one with no birth year), with its
  basis in one sentence (`allocationCaption`), and compares the holdings with it side by side ("Actual" and
  "Suggested", or "Unsaved mix" after a drag): no verdict, no drift, no dollars to move and no colour of advice. Once a
  mix is saved it is the family's and the card says so; the verdict, the drift and the to-rebalance dollars measure the
  holdings against it, and a drag previews them before Save; Revert returns to the saved mix, "Use suggested
  (age-based)" applies the suggestion as an unsaved change; no confirm dialogs. With both a saved mix and asset-class
  targets, one line (`target-mix-measures`) says what each measures — the mix within the classified positions alone
  (the balance-only accounts are outside it), the targets by class across net worth — so two numbers are not read as a
  contradiction. The footnote names the base's dollars only when there are some (never a "$0" base).
- **Passive income (12 mo)** (Accounts → Investments, `kpis-investments`) — `passiveIncomeTTM` (`lib/passiveIncome.ts`):
  income rows with a positive amount whose subcategory the vocabulary classifies as dividends or interest
  (`vocab.passiveIncome`, so a renamed subcategory still counts), over the 12 full months before the month in progress
  (`fullMonths`), cut at the first of them when the data holds fewer; the label names the months the window spans, and
  with no full month the figure is unknown ("—"), never $0 over months before the data. Dividends come from an
  investment account's own rows: while no investment account has any transaction (a balance-only feed) every interest
  row is a bank account's, so the line names it "bank interest", and dividends read "—" followed at once by why and
  what that leaves the headline ("dividends — (no transaction feed for <the accounts>, so the figure leaves them
  out)"); with some account's rows, or no investment account at all, they are real figures, $0 included (an account
  still without a feed is named as not counted). The margin tile's interest reads "—" with its reason when the margin
  account has no transaction at all ("no transaction feed for this account") or no account can be told to be the
  margin account; never a $0 it cannot know.
- **Top-position concentration** (Accounts → Investments, `card-concentration`) — every security-level holding above 10%
  of investable assets, the same base as the "Total invested assets" KPI (a balance-only line counts in the base, never
  in the list; the target mix measures the classified positions alone).
- **Coverage** — rates, baselines and averages compute only over the months the export flags `in_coverage`: they start
  at the first month by which at least `coverage.min_share` (`assumptions.yaml`, default 0.6) of the cash-flow accounts
  with any transaction have history, and every later month counts (a quiet account or a thin newest month moves
  nothing; `full_coverage` still marks a thin month on the Cash Flow chart: one that some cash-flow account with any
  transaction does not cover, before its first row or after its last, while an account that never transacts, such as
  a certificate that only reports a balance, holds no month back; while no cash-flow account has a transaction every
  month is thin, and a household with no cash-flow account has none).
  `coverageMonths` (`lib/agg.ts`) filters on the flag; `coverageFloor` / `coverageFloorLabel` name the first such month
  (null when there is none: a sentence that would name it is left out, a window is not clamped).

## Essentials and the budget grid

Every "essential" figure uses the emergency-fund model's essential list with THIS device's toggles (`efund-overrides`,
`useEfundOverrides`; read a toggle through `overrideFor`, never `overrides[key]`): the Planning gauge, runway and
forecast floor (which reads "—" with no floor line when nothing is essential), the Second House cushion and Budget's
fixed-vs-discretionary card. The default list's prose comes from `vocab.essentialLabels()`. "As toggled" is said only
when a toggle moves a row the figure counts (`toggleMovesRow`). `emergencySpendRows` keys a row
by the whole (category, subcategory) pair (`spendKey`), so a slash subcategory stays whole; a toggle saved under the
older truncated key (`legacySpendKey`) is still honoured until the row is toggled again.

`budgetGrid` (`lib/budgets.ts`, Cash Flow → Budget) shows the last six FULL months (three on a phone): `fullMonths`
(`lib/agg.ts`) keeps only the coverage months before the month in progress. Each cell's baseline comes from `budgetRows` for that month — the median of the earlier
coverage months (up to 12), or the pinned budget (in-app or `assumptions.yaml`); a month with no earlier coverage month
is skipped rather than shown against an invented zero, and a cell with no baseline and no spend reads "—". The
variance is a visible line under each amount (colour and hover do not reach a phone).

## The report builder

Spending → Reports: group-by × interval × measure × chart over the FilterBar rows (`lib/reportSpec.ts` owns the four
URL keys; `describeReport` words a spec). A bare tab — a URL naming no report key — is spending by vendor, per month,
as a table (`DEFAULT_SPEC`): who the money goes to, month by month, which Explore does not show. Every link the app
writes names all four keys (`searchFromSpec`); a URL naming only SOME reads the rest as spending by category, per
month, stacked (`LINK_BASELINE`, what such a link has always meant). The spec IS the URL, so a report is a link and a saved report is that link
under a name. `runReport` (`lib/reportBuilder.ts`): spend = −amount over spend rows (refunds reduce it), income over
income rows, net = income − spend, count = distinct `purchaseKey` (a split purchase once per group, and once in the
"Other" roll-up). Weeks start on the ISO Monday; periods run contiguously first → last; sums are integer cents; groups
past the top `MAX_GROUPS` by |total| roll into "Other (n)". A first or last period covered only in part is `partial`
(starred) — a first one when a From date the person picked falls after its 1st, or the data begins after its 3rd day
(`coversFromStart`, `COVERED_FROM_DAY` in `lib/agg.ts`: the one rule Explore's stars and the planning run-rates read
too; a week is judged from its own first day, whatever the date) — and so is a last period that holds the end of the month in progress; a row dated in a month after it is in
no period and no total until its month begins; a period where the input holds no transaction at all is `silent` — a null cell drawn as a gap and read "—"
(an empty CSV field) — while a period with transactions but no match is a real 0. `reportInput` picks the rows
(`docs/design/splits.md`).

## The category vocabulary

Every category and subcategory name the app hard-codes lives in `lib/categories.ts` (`useVocab()` in a component):
`categoryVocab(assumptions)` answers each question — essential by default, the uncategorized pile, passive income,
margin interest — in the CURRENT spelling, so a category rename can never silently drop a category from a figure.
There is no income-stream list: Cash Flow's "Income by source" draws the income subcategories present in the window,
largest total first, coloured by position (`streamColor`, `lib/tokens.ts`); its employment bands are the export's
`income_phases`, one per earner and income-rule `employer:` label. The names the engine files rows under (the
fallback category and its uncategorized and P2P-unknown subcategories, the internal, income and fees categories) are
the data's own, `assumptions.reserved`, never a literal; an older export reads as `SHIPPED_RESERVED`, and the classes
that hang off one (money movement, margin interest, passive income) are built under it. A name matches under both
its shipped and its renamed spelling (the ETL still emits some shipped names itself). A rename that MERGES onto a
name already in `SHIPPED_TAXONOMY` adds nothing — the target keeps its own class; a rename onto a new name inherits the class.
`SHIPPED_TAXONOMY` is the starter taxonomy (`starter/category_rules.yaml`, pinned by `tests/test_starter.py`), never a
household's own (a category the owner adds in YAML needs no code change); `VOCABULARY_NAMES` lists every name the
class lists use, and a test checks each is in it — a new class list is added there too. `categories.test.ts` fails on a shipped category name quoted anywhere else in non-test source (a short
allowlist covers on-screen labels) and on a fixed year-month in any non-test code line. `NO_CATEGORY` lives there as well. A `lib/` function that judges
categories takes the vocabulary as an argument (`budgetRows`, `emergencySpendRows`), so it stays pure.

## Formatting

- Money only through the formatters (`docs/design/privacy.md`). One minus sign, U+2212, everywhere, `fmtPct` and
  `fmtPoints` included; a value that rounds to zero never prints a negative zero (money, `fmtPct`, `fmtMonths`); no
  accounting-style parentheses (a source scan in `lib/format.test.ts`).
- Rates with no data are `null`, never 0: `fmtPct(null)` renders "—"; don't `?? 0` a rate before formatting (coalesce
  only for side uses such as a tone colour). `secondHouse`'s `pctFunded` is 1 at a zero target on purpose: nothing left
  to fund is funded. `fmtRate` prints an APR as a whole percent when it is one, else one decimal. The margin APR
  (`assumptions.household.margin_apr`) is `null` when the config states none: Planning → House then leaves its margin
  comparison out and names the key to add.
- A change that rounds to nothing is a plain "$0" in `fmtDelta` (and `fmtCompactDelta`, the chart-label form, which is the mask
  alone in privacy mode), never "+$0"; an amount is signed by one of them, never by a hand-built `"+"` (a source scan in
  `lib/format.test.ts`).
- Fine print (footnotes, "based on:" lines, hints, placeholders) is `text-faint` (`--color-faint` in `index.css`), never
  `text-zinc-600`: at least 4.5:1 on the card and the page in both themes (`lib/contrast.test.ts`).
- A `$` box in `AssumptionsPanel` is `CurrencyInput`, like Second house's: comma-grouped at rest, the mask at rest and the
  digits only while focused in privacy mode, committed on blur or Enter.
- Months: `fmtMonth` ("Jul 2026") in cards, columns and prose; `fmtMonthAxis` ("Jul ’26") on chart axes only. Dates:
  `fmtDate` in prose and cards; ISO only in dense registers (the transaction register) and CSV, and a short
  table that has a phone twin (Balances) reads like its twin. A sentence the ETL or a bank worded (a check's detail, an open
  question, a holding's name) goes through `humanizeDates` at render time, after `maskMoneyText`: it rewrites the ISO
  dates, ISO months and US slash dates inside it as `fmtDate` / `fmtMonth` and leaves an id or a version ("v2026-10",
  "tx_2026-10-01") alone; the export is never rewritten.
- Clock times are the household's: `fmtDateTime(iso, zone)` prints an instant in the household's zone
  (`assumptions.household.timezone`; null, or a zone the browser's `Intl` refuses, is the browser's own, as
  `clock.today` reads it) with the zone's abbreviation from `Intl` ("Aug 10, 5:00 PM CDT"), and the year whenever the
  instant falls in another year than the household's current one; `fmtTime` is a clock time alone and `zoneName` a zone
  in words ("Central Time"). The words are English, the 12- or 24-hour clock is the reader's (the browser's locale), and
  each zone and shape has one formatter (`lib/format.ts`). An age stays relative (`fmtRelative(iso, zone)`: "3h ago")
  until it is a week old, then it is the day itself in that zone ("Aug 3"). Every caller passes the zone, so a browser
  in another zone shows the same times as one at home.
- Periods: the month in progress (`monthInProgress`, `lib/agg.ts`) is the earlier of the newest month holding a row and
  the ingest month (`manifest.generated_at`), so a row the bank dated into the next month never makes that month the
  current one; it reads no clock. Every trailing rate and average reads the full months before it (`fullMonths`),
  except Explore's average by weekday, which reads days, not months (`weekdaySpend`): each weekday's spending in the
  days the window and the data share, over the number of times that weekday falls among them (none: no bar); a
  month after it is never counted or charted (the vendor drill and the filter bar's spend included; the bar counts such
  transactions apart, "dated into a month not begun, not in the spend", while the register marks every row after the
  ingest day "dated ahead"). `dataThrough` clamps "Transactions through" to the ingest day (rows dated later are named on Data); the
  data context computes that day once (`Indexes.dataThrough`) and every date preset and default window counts back from
  it. Explore's compare (`lib/compareWindow.ts`) ends at the last complete day, as Home's "this month so far" does, and
  sets a month cut short against the same days of the older month ("the first 30 days of each"); `partialMonths` marks a
  partial month on an axis.
- Quality-check ids show as words (`lib/checkLabels.ts` `checkLabel` / `checkText`, the id in the title); a new ETL check
  may add a label, and an unlabelled one is humanized.
