# Changelog

Every release of Family Financials is listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).
A release's images are tagged with its version (`1.0.0`), which `./ff upgrade --to` takes.

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

[1.0.0]: https://github.com/Good-Work-Dojo/Family-Financials/releases/tag/v1.0.0
