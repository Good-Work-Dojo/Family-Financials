# Importing your history

Family Financials reads each account's transactions from the files your bank lets you download, from a SimpleFIN
bank feed, or from both: files for the years before the feed, the feed from then on. This page walks through the
files. The bank feed is set up by `./ff init` (the README's "The bank feed (SimpleFIN)"), and every key named here is
in [configuration.md](configuration.md) under `accounts.yaml`. The commands run from the install directory, and a file
they read must be inside it: put downloads in `imports/` first.

## 1. Download the files

On your bank's website, open each account's activity and download it. Prefer, in order:

1. **A CSV download of a bank this release knows**: [importing-banks.md](importing-banks.md) lists each one, its
   format name and how to get the file.
2. **An OFX or QFX (Quicken) download**, which many banks and card issuers offer: the `ofx` format reads it whatever
   the bank.
3. **Any other CSV download**: describe its layout once (step 3 below).

Download as far back as the bank allows, in as many files as it takes. Overlapping files are fine: a row two files
share is stored once. Two identical purchases on one day in one file stay two. The program never changes a file it
reads.

## 2. Find out what a file is

```sh
./ff import detect imports/checking-2025.csv
```

prints each format that reads the file, or, when none does, the three nearest and why each failed, with the `map`
command to try. `preview` shows what a format reads before any account trusts it: the rows, the lines it skipped and
why, the first and last dates, and a balance the file states.

```sh
./ff import preview imports/checking-2025.csv --format chase_checking
```

## 3. A CSV layout no format reads

```sh
./ff import map imports/statement.csv --id my_bank_checking
```

asks about the file's columns at the terminal (which holds the date and in which pattern, which the amount and which
way its sign runs, or which the debits and the credits; which make the description; whether it is a bank account's
or a card's), reads the file with the answers, and only then writes `config/presets/my_bank_checking.yaml` and prints
the lines to add to `accounts.yaml`. From then on `my_bank_checking` is a format name for your household like any
other. "Your own file layouts" in [configuration.md](configuration.md) lists every key of such a file.

If the layout is a common bank's, consider sharing it (below).

## 4. Name the account's files

In `config/accounts.yaml`, an account read from files names its format (`parser`) and its files (`glob`, a pattern
matched in `imports/`, so next month's download is picked up by itself):

```yaml
accounts:
  joint_checking:
    name: Joint Checking
    institution: Sample Bank
    owner: Joint
    kind: checking
    parser: chase_checking
    glob: "checking-*.csv"
```

`./ff init` writes these entries for the files already in `imports/` when it runs. After an edit, `./ff validate`
checks the whole config, and names the formats this release reads when an account names another.

**History in two formats.** When an account's older history is in one format and its newer in another (CSV exports
up to some day and OFX downloads after it), list each as a source, oldest first, each later one with the first day it
holds:

```yaml
    sources:
      - {format: bofa_card, glob: "card-*.csv"}
      - {format: ofx, glob: "card-*.qfx", since: 2026-03-15}
```

The rows are divided by date at each `since`. Two formats may date one purchase differently (the day it was made, or
the day it posted), so a purchase in flight across a `since` can be kept by both or by neither: a warning names the
days to look at when amounts suggest it.

**Files and the bank feed together.** An account that has both a feed id (`simplefin_id`) and statement files needs
`simplefin_cutover`, the first day the feed owns: the files own the days before it, so nothing counts twice.
`./ff init` sets it to the day after the feed's earliest transaction. An account whose feed gives only its balance
says `simplefin_balances_only: true` instead.

## 5. Rebuild, and check what was read

Rebuild the data: on the Data page, **Rebuild from files** (or **Refresh now**, which fetches from the bank feed
first, when one is set up), or `./ff refresh`, which does the same from the command line. The Data page's quality report then says, per account, whether the stored rows add up to the
files' rows (a failed check means a row was lost or counted twice), how many rows each file gave and skipped, and any
file in `imports/` that no account reads.

## Sharing a layout with everyone

A preset that ships with the program helps every household with that bank. It is a short YAML file: the layout's
source and its licence, how far the layout is known (the bank's own help, an open-source definition, or several
public descriptions that agree), the columns, and an invented sample file in the same shape — never a real statement.
"Evidence, and adding a preset" in [design/importers.md](design/importers.md) lists the steps, and
[CONTRIBUTING.md](../CONTRIBUTING.md) the rest. If you cannot write it yourself, open a "Bank format request" issue
with the file's header row and five to ten invented rows in its shape.

How the formats work inside (a row's identity, signs, balances, OFX, hostile files):
[design/importers.md](design/importers.md).
