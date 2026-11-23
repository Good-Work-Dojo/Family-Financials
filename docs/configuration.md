# Configuring your household

Family Financials reads everything it knows about your household from YAML files in one folder: `config/` in the
install directory `./ff init` set up (when the engine runs from a checkout, the folder `FF_CONFIG_DIR` names, or
`config/` under `FF_HOME`). Two files are required:

- `household.yaml` says who you are: the people, the name for what you hold together, your time zone.
- `accounts.yaml` lists what you hold: each bank, card, brokerage and loan account, and values no bank reports, such as
  your home.

Every other file is optional. A missing one reads as empty and the engine uses its defaults: you can start with the two
required files and add rules as you go. With no rule file at all the rulebook is empty and the taxonomy holds only the
names the engine files rows under itself (below, `category_rules.yaml`), so every purchase starts out uncategorized.
Beside the files, an optional `presets/` folder holds the layouts of your own banks' statement files that no format of
this release reads (below, "Your own file layouts").

## Checking your config

```
./ff validate                   # from the install directory; in a checkout: python -m etl validate (make validate)
```

reads every file and prints every problem it finds at once, one per line, sorted by file and key path:

```
accounts.yaml: accounts.demo_card_0000.kind: unknown kind 'creditcard' — one of checking, savings, credit-card, brokerage, loan
household.yaml: timezon: unknown key — did you mean 'timezone'?
```

Each line names the file, the key, what is wrong and, after the dash, what to write instead. With no problem it prints
`config OK (8 files, 5 accounts)`, and how many presets of your own it read when you have any. The rebuild of the
dashboard's data (every refresh) and the `rules` commands refuse a config with problems the same way, before they read
a statement or write anything; `./ff validate` and `./ff doctor` print the lines. The bank feed's fetch checks only
what it reads — the accounts, their feed ids, the `simplefin:` block and your time zone — so a mistake in a rule file
never stops a fetch.

## How to read the tables

- A key path joins keys with dots: `net_worth.include_credit_cards` is `include_credit_cards` inside `net_worth:`.
  `<id>` stands for a name you choose (an account id) and `[]` for any item of a list (`members[].name` is the name of
  each member).
- A key you leave out takes the default shown; `required` means it has none. An optional block or list written with no
  value (`key:` on its own, or `key: null`) counts as left out. Inside an entry (an account, a rule, an override), give
  a key a value or remove it. A few keys accept a blank with a meaning of its own: in an override's `set`,
  `subcategory:` and `earner:` with no value clear the row's, and a rename's `from.subcategory:` with no value names
  the category's rows that have no subcategory.
- Every file may begin with `version: 1` (overrides.yaml, a list, has no place for it); any other version is refused.
- A key the tables do not list is refused, in every file and inside every entry, and the nearest known key is
  suggested: a typo never passes silently. An entry with an unknown key is left out of the checks after that one, so
  fix the key first.
- An owner is one of the names household.yaml gives: an adult's, the joint label, or a child's. A member, the
  `earner:` of income, is an adult or a child, never the joint label.
- A date is written `YYYY-MM-DD`, e.g. `2030-01-31`. Amounts are dollars.

## `household.yaml` (required)

Who the household is.

```yaml
version: 1
name: The Samples
surname: Sample
members:
  - {name: Alex, role: adult, birth_year: 1990}
  - {name: Sam, role: adult}
  - {name: Robin, role: child}
joint_label: Joint
timezone: America/New_York
refresh: {hours: [1, 9, 17], minute: 25}
net_worth: {include_credit_cards: true}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `name` | text | the surname, else Household | The household's name, exported as `assumptions.json` `household.name`. |
| `surname` | text | none | The family name; with the adults' names it gives the words that mark a payment between the household's own accounts. |
| `members` | list | required | The people of the household, one entry each; at least one is an adult. |
| `members[].name` | text | required | The person's name, as `owner:` and `earner:` keys write it; no two members share one, compared ignoring case. |
| `members[].role` | adult or child | required | Adults are listed first among the owners, then the joint label, then the children. |
| `members[].birth_year` | whole number | none | The year the person was born, from 1900 to 2100. |
| `joint_label` | text | required | The owner name of what the household holds jointly, e.g. Joint; no member may have it. |
| `timezone` | text | required | The IANA zone name, e.g. America/New_York, that decides which day every bank-feed time falls on, the hours the scheduled refresh runs at and when the day's refresh limits start again. |
| `refresh` | mapping | {} | When the scheduled refresh runs, on the household's clock (`timezone`). |
| `refresh.hours` | list of whole numbers | [1, 9, 17] | The hours of the day, from 0 to 23, the scheduled refresh runs at: one to 6 of them, each once. The bank feed allows about 24 requests a day, which these and the dashboard's manual refreshes stay well under. |
| `refresh.minute` | whole number | 0 | The minute past each of those hours, from 0 to 59. |
| `modules` | mapping | {} | Which optional pages of the app are on, exported as `assumptions.json` `modules`; a module switched in the dashboard's Settings follows that switch instead, and Settings says which of the two is in force. The modules: home_buying. |
| `modules.<module>` | true or false | none | Switches one optional part on (true) or off (false) until the dashboard's own switch for it says otherwise; an id that is no module is refused. |
| `net_worth` | mapping | {} | How net worth is counted. |
| `net_worth.include_credit_cards` | true or false | true | Whether credit-card balances count as debts in net worth. |
| `ai` | mapping | {} | The optional AI step that suggests a category for the vendors no rule places. It sends nothing until the household switches it on in the dashboard after seeing exactly what would be sent, and a key is stored. |
| `ai.provider` | anthropic or openai | anthropic | Who is asked: Anthropic, or OpenAI or a server that speaks its interface, a model of your own among them. Changing it turns the step off until the household agrees again. |
| `ai.model` | text | claude-opus-5-5 with anthropic | The model asked; required with openai, which has no default. With an `ai.base_url`, changing it turns the step off until the household agrees again (a gateway can send each model's requests to another company). |
| `ai.base_url` | text | none | With openai only: the address of the server asked, an https:// address the step's containers can reach; plain http:// only to the container's own loopback (127.0.0.1, localhost or [::1]); without one, OpenAI's own. Changing its host or path turns the step off until the household agrees again. |
| `ai.accounts` | cards or all | cards | Whose uncategorized purchases are asked about: the credit-card accounts' only, or every account's. Changing it turns the step off until the household agrees again. |
| `ai.never_send` | list of text | [] | Words never sent, besides the household's names: nicknames, a pet, a street, a school, a landlord. Each is split into words as a name is, and no vendor or category holding one is sent. |
| `ai.min_seen` | whole number | 2 | A vendor is asked about once at least this many of its purchases are uncategorized, from 1 to 100. |
| `ai.confidence` | number | 0.8 | An answer at least this sure, from 0.5 to 1, files the vendor's purchases; a less sure one is kept, not applied. |
| `ai.caps` | mapping | {} | How much the AI step may ask. |
| `ai.caps.vendors_per_run` | whole number | 50 | The most vendors one run asks about, from 1 to 200. |
| `ai.caps.runs_per_day` | whole number | 4 | The most runs in a day on the household's clock, from 1 to 24. |

The time zone must be a zone's own name, such as `America/New_York`, `Pacific/Kiritimati` or `UTC`. `localtime` is
refused: it is whatever zone the computer running the engine happens to be set to, and a date drawn there may be the
wrong day.

The scheduled refresh (in the containers: fetch the bank feed, then rebuild the dashboard) runs at `refresh.hours`
past `refresh.minute` on the household's clock, and the dashboard's daily limits on refreshes start again at the
household's midnight. A change to either key, or to `timezone`, takes effect when the scheduler restarts. On a day the
clocks change, an hour that is skipped may lose its run and an hour that repeats may run twice; a second run is
harmless, since the refreshes take turns and stay inside the bank feed's daily limit.

The AI step is off unless the household turns it on. When it is on, the one thing it sends is the name of each vendor
whose purchases no rule, learned rule, answer or override placed, case-folded, with the household's spending categories
and their subcategories, by name, to choose from and a fixed instruction. Never an amount, a date, an account, a note or a statement's text, and never a
vendor or category name that holds a household name (a member's, the surname, the joint label, the words of `name`
other than "the", "family", "household", "home", "house", "our" and "and", or a word of `ai.never_send`; in any case
or accent, a name of four letters or more even inside a longer word), a number of three or more digits (store, phone,
check and card numbers, however they are spaced), a date, an e-mail or web address, or more than 64 characters; a
vendor with fewer than `ai.min_seen` uncategorized purchases waits, and a payment made through a payment app or a
wallet (Venmo, Zelle, Cash App, PayPal, Apple Cash, Google Pay, Meta Pay and the like) is never asked about, a shop
paid through one included. A name written in letters other than Latin ones is matched only as written, and a bank
prints names in Latin letters: `validate` says so, and its Latin spelling belongs in `ai.never_send`. `./ff ai
preview` prints exactly what would be sent, the category names among it, and how many vendors are kept back for each
reason, offline, with no key. The key is the one line of `secrets/ai/api_key`, readable by its owner alone. A
`base_url` of `http://` to 127.0.0.1 is the computer the AI step runs on, which in the containers is the container
itself: there a model of your own is reached over `https://`. [ai-categorization.md](ai-categorization.md) is the
whole guide: setting it up, the services' own terms, a model of your own, and turning it off.

## `accounts.yaml` (required)

Every account, keyed by an id you choose: letters, digits, `_` and `-`, such as `demo_checking_0000`. Rules name
accounts by these ids, and the dashboard's addresses use them, so pick them once.

```yaml
version: 1
source_dir: statements
accounts:
  demo_checking_0000:
    name: Sample Checking
    institution: Sample Bank
    owner: Joint
    kind: checking
    simplefin_id: "ACT-0000"
  demo_card_0000:
    name: Sample Card
    institution: Sample Bank
    owner: Alex
    kind: credit-card
    credit_limit: 5000
  demo_retirement_0000:
    name: Sample 401k
    institution: Sample Broker
    owner: Sam
    kind: brokerage
    asset_class: Retirement
  demo_mortgage_0000:
    name: Sample Mortgage
    institution: Sample Lender
    owner: Joint
    kind: loan
    mortgage: true
    loan: {rate: 6.5, monthly_payment: 2000, term_months: 360}
manual_entries:
  - {account: Sample House, institution: Sample, owner: Joint, asset_class: House, value: 350000, as_of: 2030-01-31}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `source_dir` | text | none | The statements folder, relative to the folder that holds config/; needed when an account reads statement files and neither FF_IMPORTS_DIR nor FF_HOME is set. |
| `skip_files` | list of text | [] | File-name patterns in the statements folder that are never read. |
| `simplefin` | mapping | {} | Where the bank feed keeps what it fetched. |
| `simplefin.store_dir` | text | ../simplefin | The feed's snapshot store, relative to the folder that holds config/ (FF_STORE_DIR or FF_HOME wins). |
| `simplefin.ignore` | list of text | [] | Feed account ids that no account maps, on purpose: they raise no warning. |
| `household` | mapping | {} | Who "we" are in payment descriptions. |
| `household.own_name_tokens` | list of text | the adults' names and the surname | Words that mark a person-to-person payment as one between the household's own accounts. |
| `accounts` | mapping | required | Every account, keyed by its id. |
| `accounts.<id>` | mapping | none | One account. Its id is letters, digits, _ and -, and names it in rules and in the dashboard's addresses. |
| `accounts.<id>.name` | text | required | The account's name, as the dashboard shows it; no two accounts share one, compared ignoring case. |
| `accounts.<id>.institution` | text | required | The bank or broker that holds it. |
| `accounts.<id>.owner` | an owner | required | An adult's name, the joint label or a child's name from household.yaml. |
| `accounts.<id>.kind` | checking, savings, credit-card, brokerage or loan | required | What sort of account it is. |
| `accounts.<id>.asset_class` | an asset class | by kind | The class its balance counts under in net worth; a card or a loan takes a debt class, other kinds do not. |
| `accounts.<id>.tags` | list of text | [] | Tags every transaction of the account carries. |
| `accounts.<id>.parser` | a statement format | none | The format its statement files are read with; needs glob. |
| `accounts.<id>.glob` | text | none | The file-name pattern of its statement files in the statements folder; needs parser. |
| `accounts.<id>.sources` | list | none | Its statement files when they come in more than one format, such as older exports in one and newer downloads in another: one entry per source, oldest first, each holding its own days. Given instead of parser and glob. |
| `accounts.<id>.sources[].format` | a statement format | required | The format the source's files are read with; the names parser takes. |
| `accounts.<id>.sources[].glob` | text | required | The file-name pattern of the source's files in the statements folder; no two sources share one. |
| `accounts.<id>.sources[].since` | date | none | The first day the source holds; the source before it holds the days before. The first source has none; each later one needs one, after the one before it and before the account's simplefin_cutover. |
| `accounts.<id>.simplefin_id` | text | none | The bank feed's id for the account; no two accounts share one. |
| `accounts.<id>.simplefin_id_history` | list of text | [] | Earlier feed ids of the same account, from a connection made again. |
| `accounts.<id>.simplefin_cutover` | date | none | From this day on the feed owns the account's rows; statement files own the days before. An account with statement files and a feed id (current or earlier) needs it, unless it reads balances only; one that reads balances only never states it. |
| `accounts.<id>.simplefin_balances_only` | true or false | by kind | Read only balances from the feed, no transactions; the default is true for a brokerage or loan with no statement files and no cutover. Never stated true with a cutover. |
| `accounts.<id>.credit_limit` | number | none | A card's credit limit in dollars, used when the feed gives none. |
| `accounts.<id>.margin` | true or false | false | A brokerage whose negative cash is a margin loan. |
| `accounts.<id>.mortgage` | true or false | false | The loan that is the home's mortgage; at most one account. |
| `accounts.<id>.loan` | mapping | none | A loan account's terms; every one is optional. |
| `accounts.<id>.loan.rate` | number | none | The yearly interest rate in percent, e.g. 6.5. |
| `accounts.<id>.loan.monthly_payment` | number | none | The monthly payment in dollars. |
| `accounts.<id>.loan.monthly_escrow` | number | none | The part of the payment that goes to escrow, in dollars. |
| `accounts.<id>.loan.escrow_balance` | number | none | The escrow account's balance in dollars. |
| `accounts.<id>.loan.term_months` | whole number | none | The loan's length in months, e.g. 360. |
| `accounts.<id>.loan.origination` | mapping | none | When the loan was made and for how much. |
| `accounts.<id>.loan.origination.date` | date | none | The day the loan was made. |
| `accounts.<id>.loan.origination.amount` | number | none | The dollars borrowed. |
| `accounts.<id>.note` | text | none | Your own note; the engine does not read it. |
| `manual_entries` | list | [] | Values no feed or statement reports, such as a home, a car or a debt (stated negative), each counted from its `acquired` day when it states one, else carried back before its first value. The dashboard's Settings → Manual assets enters more, and replaces an entry with an asset of the entry's id (its name as lowercase letters and digits, runs of anything else become one dash, none at either end) until that asset is removed there; it never removes an entry. |
| `manual_entries[].account` | text | required | The entry's name; no two entries share one, and none is an account's name, compared ignoring case. It holds a letter or digit, and its id is at most 64 characters. |
| `manual_entries[].institution` | text | required | Who holds or values it. |
| `manual_entries[].owner` | an owner | required | An adult's name, the joint label or a child's name. |
| `manual_entries[].asset_class` | an asset class | required | The class the value counts under; House for the home. |
| `manual_entries[].value` | number | required | The value in dollars, from -1e12 to 1e12; negative for a debt. |
| `manual_entries[].as_of` | date | required | The day the value was true; before it, net worth counts the same value. |
| `manual_entries[].acquired` | date | none | The day it was bought, on or before as_of; before it, net worth does not count it. |
| `manual_entries[].liquid` | true or false | false | Whether it counts as money you could spend soon; a debt never does. |
| `manual_entries[].note` | text | none | Your own note; the engine does not read it. |

**Classes.** An asset class is one of Cash, Retirement, Stocks/ETF, Crypto, Precious Metals, Real Estate/Venture,
House, Car, 529, Private Stock, Mortgage, Margin and Other Debt. An account that declares none takes its kind's: Cash
for checking and savings, Stocks/ETF for a brokerage, Other Debt for a card or a loan, and Mortgage for the loan marked
`mortgage: true`. A card or a loan is a debt and takes only a debt class (Other Debt, or Mortgage on the mortgage);
another kind takes none. Margin is never declared: it is the negative cash of a brokerage marked `margin: true`.

**Statement files.** An account read from files the bank lets you download names both the file format (`parser`) and
the file names (`glob`, e.g. `"demo_checking_0000-*.csv"`, matched in the statements folder, so never an absolute
path); one without the other is refused. `./ff validate` names the formats this release reads, and your own
presets, when an account names another. Not sure which format a file is in? `./ff import detect FILE` says
(below, "Your own file layouts"). A file is read for one account only: when two accounts' patterns match it, it is
the first listed account's whose format reads it, so a broad pattern on a checking account leaves a card's export,
which its format cannot read, to the card. When both formats read it, the other account fails a check naming the
file (`import.matched_by_accounts`), and `./ff validate` says so in a note. Give each account a pattern that matches
only its own files.

**Statement files in more than one format.** An account's history often comes in two formats, such as CSV exports up
to some day and OFX downloads after it, and both usually cover the weeks around the switch. The same purchase reads
differently in each, so neither file can tell the other's copy of a row from a new one. Such an account names each
format as a source in `sources` instead of `parser` and `glob`, oldest first, and every source after the first states
the first day it holds (`since`); a source holds the days from its own `since` up to the next one's, the first every
day before the second's:

```
    sources:
      - {format: bofa_card, glob: "demo_card_0000-*.csv"}
      - {format: ofx, glob: "demo_card_0000-*.qfx", since: 2030-10-15}
```

Here the CSV exports give every row dated before 15 October 2030 and the OFX downloads every row from that day on,
the 15th included. The rows are divided by their date at each `since`: a row a source's files hold outside its own
days is left out (the files still reconcile), and each row keeps the identity its own file gives it, whatever the
other source holds. Two formats may date one purchase differently, one by the day it was made and the other by the
day it posted, so a purchase made just before a `since` and posted on or after it can be kept by both sources, or by
neither: where amounts suggest that, within five days of a `since`, a warning (`import.sources_seam`) says how many
rows to look at; move `since` to a day with no purchase in flight, or fix the row with an override. The quality
report counts, per source, the rows it kept and the rows it left out (`import.sources`), and warns when rows left out
fall on days the source holding them has no file for, so no source counts them. A file the patterns of two sources
match is read once, as the first one's, and fails one check for the account (`import.matched_twice`); a source whose
pattern matches no file warns, as `glob` does. A source's `since` must be later than the one before it, and no two
sources share a pattern.

**The bank feed.** An account the feed reports carries the feed's id for it in `simplefin_id`; when you reconnect a
bank the feed gives the account a new id, so move the old one to `simplefin_id_history`.

**Statement files and the feed together.** An account with both (`parser` and `glob` or `sources`, and a
`simplefin_id` or an earlier one in `simplefin_id_history`, whose snapshots are still read) needs a date that divides
them, or every transaction the two share would count twice: `simplefin_cutover` is the first day the feed owns, and
the statement files own the days before it (with `sources`, every `since` comes before the cutover, and the last
source holds the days from its `since` up to it). An account whose feed should give only its balance says
`simplefin_balances_only: true` instead. One of the two is required, and never both: with balances only the feed
brings no transactions, so a cutover would drop the file rows from its day on with nothing to replace them:

```
    parser: sample_format
    glob: "demo_checking_0000-*.csv"
    simplefin_cutover: 2030-01-15
```

File rows on or after the cutover are dropped once the feed has delivered something for the account (a transaction
or a balance); until then they are kept, and the quality report says so. Two warnings watch the seam:
`simplefin.cutover_gap` when the feed's first transaction comes after the cutover (the days between are in neither
source), and `simplefin.cutover_overlap` when file rows on or after the cutover were dropped while the feed has no
transaction on or before the files' last one (nothing replaces them).

**Flags.** `margin: true` is for a brokerage only, `mortgage: true` for one loan only, `credit_limit` for a card only.

## `assumptions.yaml`

The tunables: matching windows, review thresholds and the starting points of the projections.

```yaml
match_window_bdays: 3
coverage: {min_share: 0.6}
cancelled_subscriptions:
  - {vendor: Sample Streaming, recorded_on: 2030-01-20}
household: {margin_apr: 0.07, retirement_accounts: {Sample 401k: Sam}}
projections:
  return_scenarios_pct: [4, 6, 8]
  inflation_pct: 2.5
  withdrawal_rate_pct: 4
  ltcg_pct: 15
  state_income_tax_pct: 0
targets: {Cash: 10, Stocks/ETF: 60, Retirement: 30}
budgets: {Dining: 400}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `match_window_bdays` | whole number | 3 | Business days apart the two legs of a transfer may be. |
| `venmo_match_window_bdays` | whole number | 5 | The same window for payment-app transfers. |
| `venmo_review_threshold` | number | 200 | Payment-app amounts in dollars above which an unexplained one is sent to review. |
| `uncategorized_review_threshold` | number | 250 | Uncategorized purchases in dollars above which one is sent to review. |
| `unknown_credit_review_threshold` | number | 500 | Unexplained money in, in dollars, above which it is sent to review. |
| `generic_min_amount` | number | 20.00 | The smallest amount in dollars the matcher pairs as a transfer when no rule explains it. |
| `coverage` | mapping | {} | From which month rates and averages count. |
| `coverage.min_share` | number | 0.6 | Rates and averages start at the first month by which at least this share of your everyday accounts that have any transaction have had their first one (above 0, at most 1). |
| `cancelled_subscriptions` | list | [] | Subscriptions you ended, so the forecast stops expecting them. |
| `cancelled_subscriptions[]` | mapping | none | One subscription you ended. |
| `cancelled_subscriptions[].vendor` | text | required | Its vendor as the Recurring page names it, compared ignoring case. |
| `cancelled_subscriptions[].recorded_on` | date | none | The day you wrote it down; the engine does not read it. |
| `cancelled_subscriptions[].permanent` | true or false | none | Whether you mean it for good; the engine does not read it. |
| `cancelled_subscriptions[].note` | text | none | Your own note; the engine does not read it. |
| `household` | mapping | {} | Household facts the projections use. |
| `household.margin_apr` | a rate from 0 up to 1 | none: margin interest is not estimated | The margin loan's yearly rate as a fraction, e.g. 0.07 for 7 %. |
| `household.retirement_accounts` | mapping | {} | Retirement account name -> the owner it belongs to. |
| `household.retirement_accounts.<name>` | an owner | none | One retirement account: its name (an account's name or a manual entry's account) and its owner. |
| `projections` | mapping | {} | The projection defaults the dashboard starts from. A key left out takes its default, and the dashboard marks every figure that rests on one "default". |
| `projections.return_scenarios_pct` | list of one or more numbers | [5, 7, 9] | The yearly returns, in percent, the projections compare; written, it holds at least one number (an empty list is refused). |
| `projections.inflation_pct` | number | 3.0 | Yearly inflation in percent. |
| `projections.withdrawal_rate_pct` | number | 4.0 | The yearly withdrawal rate in retirement, in percent. |
| `projections.ltcg_pct` | number | 15.0 | The long-term capital gains tax rate in percent. |
| `projections.state_income_tax_pct` | number | 0.0 | The state income tax rate in percent; set your state's rate. |
| `targets` | mapping | {} | The target asset mix: asset class -> percent of net worth; the percents need not add up to 100. |
| `targets.<class>` | number | none | One asset class (no debt class) and its target, from 0 to 100. |
| `budgets` | mapping | {} | Monthly budgets by category. |
| `budgets.<category>` | number | none | One category of the taxonomy and its monthly budget in dollars, 0 or more. |

The mortgage's terms are not written here (they are `loan:` on the mortgage account), and neither is the home's value
(it is a `manual_entries` item with `asset_class: House`). The
projection keys are exactly the ones the dashboard reads, and any other is refused; the dashboard starts from the
defaults shown for any you leave out. Without `household.margin_apr` it compares no margin rate, and when an account
has `margin: true` it says which key to add.

## `category_rules.yaml`

Your categories and the rules that file transactions under them.

```yaml
taxonomy:
  Income: [Salary, Other]
  Dining: [Restaurants, Coffee]
  Groceries: []
  "Fees & Interest": []
  "Cash & Misc": [Uncategorized, Venmo/Zelle Unknown]
  Internal: []
keyword_rules:
  - {pattern: '^SAMPLE GROCER', category: Groceries}
  - name: sample-cafe-dinner
    stage: pre
    all: [{field: vendor, op: is, value: Sample Cafe}, {field: amount, op: lt, value: -50}]
    set: {category: Dining, subcategory: Restaurants}
  - {pattern: '\b(?:VENMO|ZELLE)\b', category: "Cash & Misc", subcategory: Venmo/Zelle Unknown, needs_review: true}
income_rules:
  - {pattern: '^SAMPLE EMPLOYER PAYROLL', subcategory: Salary, earner: Alex, employer: Sample Employer}
renames:
  - {from: {category: Eating Out}, to: {category: Dining}}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `taxonomy` | mapping | the reserved names | Every category and its subcategories. |
| `taxonomy.<category>` | list of text | [] | One category and its subcategories (it may have none). |
| `keyword_rules` | list | [] | Rules that file a transaction under a category by its description or other fields. |
| `keyword_rules[]` | mapping | none | One rule, in the legacy shape (pattern and the category it files) or the structured one (all or any, and set). |
| `keyword_rules[].pattern` | a regular expression | none | Legacy shape: matched, case ignored, against the cleaned description or the raw one. |
| `keyword_rules[].category` | text | none | Legacy shape: the category a matching row is filed under. |
| `keyword_rules[].subcategory` | text | none | Legacy shape: its subcategory, listed under it in the taxonomy. |
| `keyword_rules[].tags` | list of text | [] | Legacy shape: tags a matching row carries. |
| `keyword_rules[].needs_review` | true or false | false | Legacy shape: send a row it files to review from uncategorized_review_threshold up. |
| `keyword_rules[].name` | text | keyword_rules:<index> | The rule's name; no two rules share one. |
| `keyword_rules[].stage` | pre, default or post | default | When it runs: every pre rule, then default, then post, file order within a stage. |
| `keyword_rules[].all` | list | none | Structured shape: conditions that must all hold; give all or any. |
| `keyword_rules[].all[]` | mapping | none | One condition. |
| `keyword_rules[].all[].field` | description, vendor, amount, account, owner, type or bank_category | required | What the condition reads. |
| `keyword_rules[].all[].op` | an operator | required | How it compares: is, isNot, oneOf, contains, matches (text); gt, gte, lt, lte, between, inflow, outflow (amount). |
| `keyword_rules[].all[].value` | text, a number or a list | none | What it compares with: dollars for an amount, negative for money out; inflow and outflow take none. |
| `keyword_rules[].any` | list | none | Structured shape: conditions of which one must hold. |
| `keyword_rules[].any[]` | mapping | none | One condition. |
| `keyword_rules[].any[].field` | description, vendor, amount, account, owner, type or bank_category | required | What the condition reads. |
| `keyword_rules[].any[].op` | an operator | required | How it compares, as under all. |
| `keyword_rules[].any[].value` | text, a number or a list | none | What it compares with, as under all. |
| `keyword_rules[].set` | mapping | none | Structured shape: what a matching row is given. |
| `keyword_rules[].set.category` | text | required | The category it is filed under. |
| `keyword_rules[].set.subcategory` | text | none | Its subcategory. |
| `keyword_rules[].set.tags` | list of text | [] | Tags it carries. |
| `keyword_rules[].set.needs_review` | true or false | false | Send it to review from uncategorized_review_threshold up. |
| `keyword_rules[].note` | text | none | Your own note; the engine does not read it. |
| `income_rules` | list | [] | Rules that recognise income and its earner. |
| `income_rules[]` | mapping | none | One rule: deposits into a checking or savings account whose description its pattern finds are income. |
| `income_rules[].pattern` | a regular expression | required | Matched, case ignored, against the deposit's description. |
| `income_rules[].accounts` | account ids | every checking and savings account | The accounts it reads. |
| `income_rules[].type` | income, dividend or interest | income | The type a matching deposit takes. |
| `income_rules[].subcategory` | text | none | Its subcategory, listed under the income category in the taxonomy. |
| `income_rules[].earner` | a member | from the subcategory | Who earned it: a member of household.yaml, never the joint label. |
| `income_rules[].employer` | text | none | Who paid it; one job per earner and employer is drawn on the cash-flow chart. |
| `income_rules[].note` | text | none | Your own note; the engine does not read it. |
| `renames` | list | [] | Category renames; stored references follow them. |
| `renames[]` | mapping | none | One rename: every stored reference to from is read as to. |
| `renames[].from` | mapping | required | The old name. |
| `renames[].from.category` | text | required | Its category (not a reserved one). |
| `renames[].from.subcategory` | text | none | Its subcategory; without the key the whole category moves, and with no value (`subcategory:` left blank) it names the category's rows that have no subcategory. |
| `renames[].to` | mapping | required | The new name, already in the taxonomy. |
| `renames[].to.category` | text | required | Its category. |
| `renames[].to.subcategory` | text | none | Its subcategory. |

A taxonomy you write keeps the names the engine files rows under itself (the fallback, uncategorized, internal, income
and fees categories, and the person-to-person subcategory); the starter file holds them. Every category and
subcategory a rule, a bank label, an override, a cash-out entry or a budget names must be in it.

**Which rule wins.** A purchase takes its category from the first of: an override, a review answer, a category its
statement format gives, a vendor pattern's `category` (`vendor_rules.yaml`), a rule learned from your answers, a
keyword rule (stage `pre`, then `default`, then
`post`, file order within each), the bank's own label (`bank_category_map.yaml`), and else the fallback; the first
keyword rule that matches wins, so the person-to-person catch-all (the rule filing rows under the Venmo/Zelle Unknown
subcategory) must be the last of its stage. `docs/design/categorization-and-learned-rules.md` explains every step.

## `vendor_rules.yaml`

How a bank's description becomes a vendor name, and the vendors' own rules.

```yaml
strip_prefixes: ['^SQ \*']
cleanup: ['#\d+']
acronyms: [LLC]
patterns:
  - {pattern: 'SAMPLE CAFE', vendor: Sample Cafe, category: Dining, subcategory: Coffee}
  - pattern: 'SAMPLE MARKET'
    vendor: Sample Market
    note: an estimate of the usual basket
    split:
      - {pct: 70, category: Groceries}
      - {remainder: true, category: Dining}
  - {pattern: '^CHECK \d+$', vendor: Check, learn: false}
learning: {exclude: [Sample Market]}
renames:
  - {from: Sample Coffee Co, to: Sample Cafe}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `strip_prefixes` | list of text | [] | Patterns cut from the start of a description before it is read. |
| `cleanup` | list of text | [] | Patterns removed from a description, such as store numbers. |
| `acronyms` | list of text | [] | Words kept upper-case in a vendor name. |
| `patterns` | list | [] | Vendor patterns: a description pattern and the vendor (and category) it names. |
| `patterns[]` | mapping | none | One pattern; the first that matches names the vendor. |
| `patterns[].pattern` | a regular expression | required | Matched, case ignored, against the cleaned description (the raw one when cleaning leaves nothing). |
| `patterns[].vendor` | text | required | The vendor's name. |
| `patterns[].category` | text | none | The category the vendor's purchases are filed under; it outranks keyword rules. |
| `patterns[].subcategory` | text | none | Its subcategory. |
| `patterns[].tags` | list of text | [] | Tags the vendor's rows carry. |
| `patterns[].learn` | true or false | true | Whether review answers may teach the vendor a category. |
| `patterns[].split` | list | none | Legs that split each purchase into parts (two or more, one the remainder). |
| `patterns[].split[]` | mapping | none | One leg. |
| `patterns[].split[].pct` | number | none | Its share of the purchase in percent (above 0, below 100). |
| `patterns[].split[].amount` | number | none | Its fixed amount in dollars. |
| `patterns[].split[].remainder` | true | none | The leg that takes what the others leave; exactly one. |
| `patterns[].split[].category` | text | required | The category of the part. |
| `patterns[].split[].subcategory` | text | none | Its subcategory. |
| `patterns[].note` | text | none | Your own note; the engine does not read it. |
| `learning` | mapping | {} | How vendor rules are learned from review answers. |
| `learning.exclude` | list of text | [] | Vendors review answers never teach a category, e.g. a shop that sells everything. |
| `renames` | list | [] | Vendor renames; stored references follow them. |
| `renames[]` | mapping | none | One rename: every stored reference to from is read as to. |
| `renames[].from` | text | required | The old vendor name, compared ignoring case; not a pattern's vendor. |
| `renames[].to` | text | required | The new name. |

A pattern takes no `name:` or `stage:` (patterns match in file order) and no structured conditions; a split leg gives
exactly one of `pct`, `amount` and `remainder` (`docs/design/splits.md`).

## `transfer_rules.yaml`

How money moving between your own accounts is recognised, so it counts as neither spending nor income.

```yaml
rules:
  - name: card-payment
    pattern: '^SAMPLE CARD PAYMENT'
    kinds: [checking]
    type: cc-payment
    counter_hint: demo_card_0000
  - name: savings-move
    pattern: '^SAMPLE TRANSFER TO (\d{4})'
    accounts: [demo_checking_0000]
    type: transfer
    sign: debit
    counter_hint: 'from_text:demo_savings_{1}'
groups:
  demo_cards: {accounts: [demo_card_0000], expect_counterpart: true}
outside: [demo_elsewhere]
venmo_patterns:
  debit: '\bVENMO\b.*PAYMENT'
  credit: '\bVENMO\b.*CASHOUT'
  zelle: '\bZELLE\b'
  zelle_party: 'ZELLE (?:TO|FROM) ([A-Z ]+)'
  cashout_income:
    - {accounts: [demo_checking_0000], subcategory: Salary, earner: Sam}
generic_matching: true
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `rules` | list | [] | Rules that recognise a transfer between the household's own accounts. |
| `rules[]` | mapping | none | One rule; the first that matches a purchase row types it. |
| `rules[].name` | text | rules[<index>] | The rule's name, as problems and review questions write it. |
| `rules[].pattern` | a regular expression | required | Matched, case ignored, against the description. |
| `rules[].type` | transfer, cc-payment or investment | required | The type a matching row takes. |
| `rules[].accounts` | account ids | none | The accounts it reads; give accounts or kinds. |
| `rules[].kinds` | account kinds | none | The kinds of account it reads; give accounts or kinds. |
| `rules[].sign` | credit or debit | both | Only money in (credit) or only money out (debit). |
| `rules[].bank_type` | text | none | Only rows the bank gave this type code. |
| `rules[].counter_hint` | a counter hint | none | Where the other leg lives: an account id, a group, kind:<kind>, bank_any, an outside name or from_text:<template>. |
| `rules[].needs_review` | true or false | false | Send a row it types to review. |
| `rules[].open_question` | text | none | A question for the family, asked once, with the first row it types. |
| `rules[].note` | text | none | Your own note; the engine does not read it. |
| `groups` | mapping | {} | Named groups of accounts a rule's counter_hint can point at. |
| `groups.<name>` | mapping | none | One group, by a name no account or outside name has. |
| `groups.<name>.accounts` | account ids | required | Its accounts. |
| `groups.<name>.expect_counterpart` | true or false | required | Whether a leg left unmatched while a member has data for its date is a data gap. |
| `outside` | list of text | [] | Names of places outside the household a counter_hint can point at. |
| `venmo_patterns` | mapping | {} | The patterns that recognise payment-app and Zelle rows. |
| `venmo_patterns.debit` | a regular expression | none | A payment-app payment out. |
| `venmo_patterns.credit` | a regular expression | none | A payment-app cash-out in. |
| `venmo_patterns.zelle` | a regular expression | none | A Zelle row. |
| `venmo_patterns.zelle_party` | a regular expression | none | Its wording that names the counterparty, in exactly one group. |
| `venmo_patterns.zelle_bank_types` | list of text | [] | Bank type codes that mark a Zelle row. |
| `venmo_patterns.cashout_income` | list | [] | Where an unmatched cash-out is filed, by account. |
| `venmo_patterns.cashout_income[]` | mapping | none | One group of accounts. |
| `venmo_patterns.cashout_income[].accounts` | account ids | required | Its accounts. |
| `venmo_patterns.cashout_income[].subcategory` | text | required | The income subcategory its cash-outs take. |
| `venmo_patterns.cashout_income[].earner` | a member | none | Who earned them. |
| `venmo_patterns.pairing` | unique or nearest | unique | `unique` pairs a payment-app or Zelle row that no transfer rule typed with a row on another of your accounts only when neither could have another counterpart; `nearest` takes the best-hinted, then closest, match. |
| `generic_matching` | true or false | true | Whether exactly opposite rows that no transfer rule typed, no category or learned rule filed and the family has not answered are paired as transfers. |

A `from_text:` hint's template fills `{1}`, `{2}`… from the pattern's groups, so it may not name more groups than the
pattern has.

## `bank_category_map.yaml`

Where a row goes when no rule places it but the bank labelled it, keyed by the bank's own label.

```yaml
"Food & Drink": {category: Dining, subcategory: Restaurants}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | whole number | 1 | The format of this file; this release reads version 1. |
| `<label>` | mapping | none | One bank's own category label and where its rows go. |
| `<label>.category` | text | required | The category a row with that label is filed under. |
| `<label>.subcategory` | text | none | The subcategory. |
| `<label>.tags` | list of text | [] | Tags the row carries. |

## `overrides.yaml`

Facts only your household knows about single transactions, applied last. The file is a list. To keep a row out of
spending and income, set its `type` (e.g. `transfer`): the type decides that, and a row given an internal type and no
category is filed under Internal. `category: Internal` alone would leave the row counted, so it is refused without an
internal type, and on a keyword rule or a vendor pattern too.

```yaml
- match: {vendor: Sample Cafe, date: 2030-01-15}
  set: {category: Dining}
  note: a birthday lunch
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `[]` | mapping | none | One override: which transactions it matches and what it sets. |
| `[].match` | mapping | required | What a transaction must have to match: one or more of the keys below. |
| `[].match.uid` | text | none | The transaction's id. |
| `[].match.vendor` | text | none | Its vendor, compared ignoring case. |
| `[].match.account` | an account id | none | Its account. |
| `[].match.date` | date | none | Its date. |
| `[].match.amount` | number | none | Its amount in dollars, negative for money out. |
| `[].match.contains` | text | none | Words in its description, compared ignoring case. |
| `[].set` | mapping | required | What a matched transaction is given. |
| `[].set.type` | a transaction type | none | Its type: purchase, income, transfer, cc-payment, investment, dividend, fee or interest. |
| `[].set.category` | text | none | Its category, in the taxonomy. |
| `[].set.subcategory` | text | none | Its subcategory, listed under the category (null clears it). |
| `[].set.tags` | list of text | none | Tags added to it. |
| `[].set.needs_review` | true or false | none | Send it to review, or take it out. |
| `[].set.earner` | a member | none | Who earned it: a member of household.yaml (left blank, it clears the row's earner). |
| `[].note` | text | none | Your own note; the engine repeats it in this override's warning when it matches nothing. |

## Your own file layouts

A bank whose CSV export no format of this release reads can still be read: describe its layout once, as a preset of
your own in the `presets/` folder inside your config folder, one file per layout named after its id
(`presets/<id>.yaml`). The id is then a format name like any other for your household: an account names it as its
`parser`, or a source names it as its `format`. `./ff validate` checks every preset there and refuses one whose
id is already a format's name, another preset's id or not its file's name.

You rarely write one by hand. Three commands find out what a file is before an account trusts it; only `map` writes
anything, and only the one preset file:

```
./ff import detect FILE                    # the formats that read the file, or the nearest ones and why not
./ff import preview FILE --format NAME     # what that format reads: rows, skipped lines, dates, first rows
./ff import map FILE --id NAME             # asks about its columns and writes presets/NAME.yaml
```

The file must be inside the install directory (put it in `imports/`); in a checkout the same commands are
`python -m etl import …`. `preview --account ID` also counts the file's rows your stored data already holds for that account. `map` asks whether
the first line names the columns; which column holds the date, offering each common date pattern that reads every date
in it and asking whenever two read one date differently; which column holds the amount and whether a positive amount
is money coming in, or which hold the debits and the credits, and, when some rows have no amount, whether to skip
them as having none; which columns make the description; which holds the running balance, if any, and whether the
newest row comes first; and whether the export is a bank account's or a card's. It reads the file with the preset
before writing it, and never writes over a preset unless told to (`--force`).
To run it without a terminal, put the answers in a YAML file and pass `--answers FILE`; its keys are `header`, `date`,
`date_format`, `amount` (or `debit` and `credit`), `positive_is_money_in`, `skip_empty_amount`, `description`,
`balance`, `newest_first`, `kind` and `label`.

A preset, written by `map` or by hand:

```
id: sample_credit_union
label: Sample Credit Union checking
kind: bank
detect:
  header: [Date, Description, Debit, Credit]
date: {column: Date, format: "%m/%d/%Y"}
amount: {debit: Debit, credit: Credit}
description: {columns: [Description]}
```

A column is a name from `detect.header`, or, with `detect.headerless`, a position counted from 1. A row the preset
cannot read is skipped and counted, never guessed at, and a file most of whose rows it cannot read is refused with a
failing check that says why. For a card whose file has both the day of the purchase and the day it posted, map the
posting date: the bank feed and OFX downloads date a card's rows by the day they posted, so where a preset's files and
theirs meet fewer purchases are in flight.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | text | required | The format name: lowercase letters, digits and _, starting with a letter; the file is named after it. |
| `label` | text | required | What you call the export, e.g. Sample Credit Union checking. |
| `kind` | bank or card | required | The kind of account the export comes from. |
| `source` | text | none | Where the layout comes from; a preset of your own may leave it out. |
| `evidence` | bank-documented, oss-definition, widely-reported or sample-verified | none | How far the layout is known; a preset of your own may leave it out. |
| `institution` | text | none | The bank or card issuer the export comes from; the table of supported banks prints it. |
| `download` | text | none | How to get the file from the bank, in one line; the table of supported banks prints it. |
| `encoding` | list of text | [utf-8-sig, cp1252] | The text encodings tried in order, e.g. [utf-16] for a file saved as UTF-16. |
| `search_rows` | whole number | 0 | With a header: how many lines may come before it, blank lines not counted; at most 50. |
| `order` | newest_first or oldest_first | none | The order of the file's rows; needed with balance. |
| `detect` | mapping | required | How a file of this layout is known: by its header line or by its shape, one of the two. |
| `detect.header` | list of text | none | Column names the header line holds, compared trimmed and ignoring case; it may hold other columns too. |
| `detect.headerless` | mapping | none | A file with no header line. |
| `detect.headerless.columns` | whole number | required | How many cells every row has. |
| `detect.headerless.constant` | mapping | {} | Cells that always hold the same text, keyed by their position from 1; a row whose cell does not is skipped and counted. |
| `date` | mapping | required | Where the date is and how it is written. |
| `date.column` | a column | required | The date's column. |
| `date.format` | text or list of text | required | A date pattern such as %m/%d/%Y, or a list tried in order; a list in which one text reads as two different dates is refused. |
| `amount` | mapping | required | One signed column (column and sign), or a debit and a credit column. |
| `amount.column` | a column | none | The signed amount's column. |
| `amount.sign` | as_is or invert | none | as_is: a positive amount is money coming in; invert: a positive amount is money going out, as on a card export whose purchases are positive. |
| `amount.debit` | a column | none | Money going out, written without a sign. |
| `amount.credit` | a column | none | Money coming in, written without a sign. |
| `description` | mapping | required | What the description is made of. |
| `description.columns` | list of columns | required | Its columns, joined in this order; an empty cell is left out. |
| `description.join` | text | one space | What goes between them. |
| `skip` | mapping | {} | Rows that are not transactions. |
| `skip.pending` | mapping | none | A pending row, skipped: its column holds the text equals names. |
| `skip.pending.column` | a column | required | The column that marks a pending row. |
| `skip.pending.equals` | text | required | The text that marks it, compared trimmed and ignoring case. |
| `skip.empty_amount` | true or false | false | Skip a row with no amount as having none, rather than as a row the layout cannot read. |
| `skip.trailing_rows_matching` | text | none | A regular expression for the totals lines at the end of the file, which are skipped. |
| `bank_type` | a column | none | The bank's own transaction type. |
| `bank_category` | a column | none | The bank's own category, which the bank-label rules read. |
| `balance` | a column | none | The balance after each row: followed row by row and booked for a checking or savings account only. |
