# Changelog

Every release of Family Financials is listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).
A release's images are tagged with its version (`1.0.1`), which `./ff upgrade --to` takes.

## [1.0.1]

Fixes to what the first release got wrong or left out, and the first additions to the bank feed setup.

To upgrade from 1.0.0, copy this release's `ff` script over your install's first (it carries the new commands), then
run `./ff upgrade --to 1.0.1`.

### Fixed

- A bank-feed snapshot that is damaged, empty or laid out unexpectedly no longer stops the rebuild: it fails its own
  check, naming the file, and the other snapshots are still read. The data's "last updated" time follows the newest
  snapshot that could be read. Odd accounts, rows, dates and balances inside a snapshot are skipped with a warning.
- Fetching from the bank feed no longer stores an answer that is not laid out as the feed promises, never leaves half a
  file behind, and does not retry when the feed says "too many requests". An answer with no accounts is not stored and
  does not shorten the next fetch's window.
- Amounts too large for the ledger, or not numbers at all, are skipped as unreadable rows instead of breaking the
  rebuild. A Chase checking row whose balance cell cannot be read is still imported.
- An override that changes a row's type now also files the row where that type belongs, so a transfer turned into a
  purchase counts as spending and a purchase turned into income counts as income. Setting a category of "Internal"
  without a transfer type is refused when the config is checked.
- The gap question for an unmatched transfer names the accounts in words, and no longer offers an account's own other
  rows as its counterpart in a one-account household.
- A very long brokerage description no longer makes a rebuild slow.
- Turning the AI step off while a run was reading the data now stops it before anything is sent.
- An unexpected error ends with one plain line saying where its details are saved, and that they may name your data,
  so read them before you share them; two ingests started together say so in one line.
- The profit-and-loss page adds up: income and spending that its listed rows do not hold appear in an "Other" row,
  each total is the sum of the rows shown, and the money invested is listed under net savings.
- A net-worth change from a base at or below zero shows the change without a meaningless percentage, small changes keep a
  decimal, and compact amounts such as $1.00M no longer round into the wrong unit.
- The money-flow chart no longer merges a category, an income stream and the household when they share a name.
- Scenarios changed on another device are no longer overwritten by a page left open on a stale copy, and a browser
  that blocks its storage still opens the page.
- The phone menu is hidden from keyboards and screen readers while closed, holds focus while open, closes on Escape and
  gives focus back to its button.
- Retirement accounts are recognised by their class (including 403(b), TSP, 457, HSA and 529 accounts), not only by name.
- A monthly bill's expected dates on the bills chart follow its rule for a year ahead (last business day, first business
  day and so on) instead of drifting onto weekends.
- A failed refresh step tells you where its log is and that `./ff validate` lists config problems; failure alerts name
  the log and `./ff logs`.
- `./ff init --force` keeps the files it replaces as `.previous` copies.

### Changed

- A statement file is read for one account only: of the accounts whose file patterns match it, the first listed whose
  format reads it. A file two accounts both read was counted twice; now the later account fails a check naming the
  file and the fix. An account whose format cannot read a file, such as a checking account with a broad pattern that
  also matches a card's export, leaves it to the account that can.
- The household's own names match a description only as whole words, so a short name such as "Ed" no longer matches
  inside "Fred".
- An upgrade replaces the install's `compose.yaml` with the new release's when the file is one a release shipped,
  keeping the old one as `compose.yaml.previous`; a file you changed is left alone and the new one is written beside
  it as `compose.yaml.new`. If the upgrade stops after restarting, the way back it prints includes copying
  `compose.yaml.previous` back over `compose.yaml`.
- The first `./ff up` of a new install, and the first one after an upgrade whose data is older than the dashboard
  reads, builds the dashboard's data about a minute after the services start. The dashboard no longer reads data
  built before the first release; it says the data is being rebuilt until the new data is there.
- `./ff doctor` warns when the bank feed has been used but no backup exists or the newest is over 30 days old, and when
  accounts name a bank feed id but no feed access is stored (the demo, whose feed is invented, says so instead). Its
  last line counts the warnings above.
- The README's setup, import, alert and reconnect instructions are clearer, and say that `FF_DEBUG=1 ./ff …` shows an
  error as it is.
- The pictures in the README show the corrected figures.

### Added

- `./ff init --feed` connects a bank feed to a household that is already set up: for each feed account the setup does not
  name it attaches it to an account without a feed id or one whose old feed id the feed no longer lists (a bank
  re-linked under a new id, whose old id it keeps in the account's history), adds it as a new account or skips it, and
  changes no other line of `accounts.yaml`. An account with statement files gets a cutover after the last day they
  hold, so none of their rows is dropped.
- `./ff simplefin list-accounts` lists the accounts in the newest stored feed answer with their ids, with no network.
- Release images' digests appear in each release's run summary.

## [1.0.0]

The first public release.

### Added

- An offline ingest that reads a household's bank and card statement files (CSV presets for common banks, OFX and
  QFX) and, optionally, the SimpleFIN bank feed, into one SQLite database and a set of JSON files, the same output
  for the same input every time.
- Categorization by vendor and keyword rules from a starter set the household copies and edits, transfers paired
  across accounts, split purchases for mixed-basket stores, renames, and a Review inbox where the family answers what
  the rules could not decide.
- Honesty checks on every ingest: each account's rows reconcile with its files, split purchases count once, and
  anything ambiguous is shown as a question instead of guessed.
- A dashboard for the whole household's money: spending, income by earner, cash flow, budgets, recurring bills and the
  next 30 days, net worth with the hand-entered assets, investments and the target mix, the mortgage, planning and
  what-if scenarios, each page usable on a phone.
- A privacy mode that masks every amount on screen, and CSV exports of every table.
- An optional AI step that suggests categories for vendors the rules leave open, off until the family turns it on,
  sending only vendor names and the household's category names, never an amount, a date, an account or a name from
  the household.
- An install run by one command, `ff`: an invented demo household, a guided setup for a real one, a check of the
  install, backup, restore and upgrade, behind a passcode sign-in, in four containers split so that the part a browser
  reaches holds no bank credential.
- Multi-architecture images (amd64 and arm64) and a demo of the dashboard that runs in the browser alone.

[1.0.1]: https://github.com/Good-Work-Dojo/Family-Financials/releases/tag/v1.0.1
[1.0.0]: https://github.com/Good-Work-Dojo/Family-Financials/releases/tag/v1.0.0
