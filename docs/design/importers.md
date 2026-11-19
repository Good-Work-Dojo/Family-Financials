# Statement-file importers

> Scope: how a statement file becomes an account's rows — the formats and their registry, a row's identity, the CSV
> preset schema, OFX, an account read from several sources, the `import` commands — and the bank feed's fetcher.
> Describes the system as it is; verify names against the code (the module docstrings of
> `etl/importers/registry.py`, `etl/importers/csv_preset.py` and `etl/importers/ofx.py` are the code truth, with
> `etl/import_cli.py` for the commands and `Pipeline.parse_transactions` in `etl/pipeline.py` for the join). Every key a
> household writes: `docs/configuration.md` (`accounts.yaml`, and "Your own file layouts"). The banks a release reads:
> `docs/importing-banks.md`, generated, never edited by hand.

## The contract every format keeps

A format is one function, `parse(path, account_id) -> (txns, FileStats, extra)`, that reads one file for one registry
account.

- `txns` are the rows read: date (ISO), signed cents (negative is money out), the description as stored, the file's
  name, and the bank's own type and category where the format reads them. A parse never sets a row's occurrence index:
  the pipeline does (below).
- `FileStats` counts each record of the file it looks at as read or skipped with a reason (a CSV record may span
  lines): a preset and the Bank of America bank format count every record from the first, title, header and blank
  lines included; the two Chase formats every record under their header line (the card format passes blank lines over
  uncounted); OFX every transaction. `rows_total` is `rows_parsed` plus `rows_skipped`, `skip_reasons` adds up to
  `rows_skipped`, and `sum_cents` is the
  signed total of exactly the rows returned. A row the pipeline leaves out afterwards (outside its source's days, after
  the feed's cutover, or a repeat of a row an overlapping file already gave) adds its amount to the account's
  `acct_dedup_sums`, so `reconcile.<account>.store_vs_files` fails whenever a row vanishes or counts twice between
  the files and the store.
- `extra` holds only keys the pipeline reads: `latest_balance` (an `(iso_date, cents)` the file states, or None),
  `summary` (a statement's own beginning and ending balances and totals), `running_balances` (each row's balance in the
  order the rows happened), `fitid_rows` (an OFX file's transaction ids, each as a digest, with the rows it names) and
  `cut_descriptions` (the positions in `txns` of the rows whose description was cut).

## The registry, and the formats a household may name

`etl/importers/registry.py` maps a format name to its parse. A format is one of three:

- **Built in**: `chase_checking`, `chase_card`, `bofa_bank` (the native formats) and `ofx`, each a module of
  `etl/importers/` that registers its parse (`register`) when it loads. Importing `etl.importers` loads them.
- **A shipped preset**: each `etl/importers/presets/<id>.yaml`, loaded and registered under its id by
  `register_presets` when the registry loads. A shipped preset that does not load stops the import: it is a defect of
  the release.
- **A household's own preset**: each `config/presets/<id>.yaml` beside the household's config, loaded per `Config`
  (`household_presets`, kept as `Config.presets`) and never registered. Its id may not be a release format's name, which
  `validate` refuses.

`TXN_PARSERS` (`etl/pipeline.py`) is a plain dict copied from `registry.formats()` when the pipeline loads, so a test
patches a format into it (`monkeypatch.setitem`) and the registry stays as it is. An account's `parser:` or a source's
`format:` may name a format of `TXN_PARSERS` or a household preset's file name: a preset file with a problem still
names a legal format, so its problem is reported once, in its own file. `format_parse` reads a file with the
household's preset of that name, else with `TXN_PARSERS`' entry. The bank feed is no format: its snapshots are read by
`etl/parsers/simplefin.py` in `Pipeline.parse_simplefin`.

## A row's identity

A row's uid (`Txn.uid`) hashes five inputs: the account id, the ISO date, the signed cents, the exact stored
description (`description_raw`) and the occurrence index — the row's place among the rows of its own file with the same
date, amount and description (`assign_occurrence_seq`, counted over the whole file whichever source holds the row's
day). Two exports that overlap give a row one uid, so it is stored once; two identical purchases on one day in one file
stay two. A feed row's index comes from the feed's own transaction id instead, and a split part hashes its parent's uid
and its part number.

Everything a household says about one row names it by its uid: a one-off review answer, an override's `match.uid`, a
split part, a transfer's pairing. A changed uid makes the row a new row: its one-off answer expires (the row is back in
the Review inbox) and an override that named it matches nothing. Hence:

- **The native formats are frozen.** Households have rows stored under the uids they give, so nothing changes how they
  build a date, an amount, a description or the occurrence index from a row they read (a row dated a day the calendar
  has not got is not read: see "Dates and signs"). They keep their own description rule (whitespace
  squashed, nothing else), and `tests/test_uid_stability.py`, which pins the occurrence rule, is never edited.
- **The preset lock.** `tests/importers/presets.lock` holds the uid of every row each shipped preset reads from each of
  its sample files (for the account `demo_lock_0000`, with the pipeline's occurrence index), and
  `test_the_lock_pins_the_uids_each_shipped_preset_reads_from_its_samples` fails when one changes or goes. A change to
  a shipped preset that changes a uid is a new preset with a new id, so the households that read the old id keep
  their uids. `python tests/test_csv_presets.py` (with the repository root on `PYTHONPATH`) prints the entries the
  lock lacks, to append to it; it prints nothing and exits 1 when an entry the lock holds would change.
- A bank's own transaction id (OFX's `FITID`) is never part of a row's identity (below).

## The preset schema, by one example

A preset is data: one YAML file describes one CSV layout and is one format. Every key, with its type and default, is
in `docs/configuration.md` ("Your own file layouts"); `csv_preset.py`'s docstring is the specification. An invented
bank's checking download, as a shipped preset (`tests/test_import_household.py` loads it through `load_preset` and
reads the file below with it):

```yaml
# Sample Savings Bank checking (an invented bank): the activity download in CSV.
#
# A title line, then the header line, then one row per transaction, newest first, then a totals line. The sign: a
# withdrawal is money out and a deposit money in, each written without a sign, so the two are read as a debit and a
# credit column.
id: sample_savings_checking
label: Sample Savings Bank checking
kind: bank
source: the bank's help page on its activity download, https://www.example.com/help/download-activity
evidence: bank-documented
institution: Sample Savings Bank
download: On the bank's website, open the account's activity and download it as a CSV file.
search_rows: 1
order: newest_first
detect:
  header: [Date, Status, Description, Withdrawals, Deposits, Balance]
date: {column: Date, format: "%m/%d/%Y"}
amount: {debit: Withdrawals, credit: Deposits}
description: {columns: [Description]}
skip:
  pending: {column: Status, equals: Pending}
  trailing_rows_matching: "^Total,"
balance: Balance
```

```csv
Sample Savings Bank account activity
Date,Status,Description,Withdrawals,Deposits,Balance
03/04/2030,Pending,COFFEE SHOP,4.50,,
03/03/2030,Posted,GROCERY STORE,82.10,,"2,617.90"
03/02/2030,Posted,PAYROLL ACME CORP,,"1,500.00","2,700.00"
Total,,,86.60,"1,500.00",
```

With the preset shipped, `python -m etl import preview FILE --format sample_savings_checking` prints for that file:

```text
Read as sample_savings_checking: 2 rows, 2030-03-02 to 2030-03-03.
Skipped: 4 (header 1, pending 1, preamble 1, trailing-row 1).
Rows:
  2030-03-03    -$82.10  GROCERY STORE
  2030-03-02  $1,500.00  PAYROLL ACME CORP
Balance stated: $2,617.90 on 2030-03-03.
```

- `detect.header` names the columns the header line must hold (trimmed, ignoring case; other columns may sit beside
  them); `search_rows` lets that many non-blank lines come before it (the title line, skipped as `preamble`). A
  headerless layout states `detect.headerless` instead: the first row has exactly `columns` cells and each `constant`
  cell holds its text, and so must every later row (else `constant-mismatch`). A column is then a position from 1.
- A row is as wide as the header line up to its last named column: a row with fewer cells, or with text past it (an
  unquoted comma inside a field), is `unparseable-row`, never read with its cells shifted.
- `skip.pending` skips a pending row; `skip.trailing_rows_matching` skips the rows at the end of the file whose cells,
  joined by commas, match (the first 1,000 characters are searched); `skip.empty_amount` skips a row with no amount as
  having none instead of counting it unreadable.
- `balance` with `order` gives `latest_balance` (the newest row with a balance: the greatest date, and that day the
  latest by the file's order) and `running_balances` (the rows turned oldest first; a skipped row that moved the
  balance, or may have, stands in it as a gap).
- `encoding` lists the encodings tried in order (default `utf-8-sig`, then `cp1252`): the first that reads the file's
  first 64 KB as text with no NUL character is used; a later byte it cannot read sends the read back to the start in
  the next encoding that reads those 64 KB to the very same text, so no row read before that byte changes; with none,
  the file is refused.
- A shipped preset states `source`, `evidence`, `institution` and `download` (the table of supported banks prints all
  four), and a comment saying where its sign comes from. A household's own may leave `source` and `evidence` out.
  `evidence: synthetic` loads only where a test allows it (`allow_synthetic`): the tests' own presets.

## Detection and refusal

- A parse raises `NotThisFormat` for a file that is not in its format, in plain words: what the file looked like
  against what the format expects, never a line, an amount or a path from it. Any other exception a parse raises is
  that file's refusal too, in words chosen by its kind (`refusal`); the error's own text is never shown.
- The ingest reads each file through `Pipeline._read_file`. A refused file fails one check, `import.<account>.<file>`
  (the file by its path in the statements folder, so two files of one name in two folders are told apart; "Not
  read: …", naming the format its account or its source names), adds no row, no sum and no balance, and the
  ingest goes on.
- Before any format opens a file, `file_refusal` refuses anything but a plain file (a folder, a pipe, a device, or a
  link to one: opening a pipe would hold the reader) and a file over `MAX_FILE_BYTES` (50 MB; a decade of one
  account's rows is well under a tenth of it). The ingest and every `import` command judge a file by it first. A preset
  reads a file as a stream; OFX reads it into memory once, never past the cap.
- A file of no bytes, or of blank lines only, is no rows and no failure in every format; so is a header with no row
  under it. A file with a line its format expected to be a row and could not read (`unparseable-row`), and no row at
  all, is refused. A Bank of America bank file whose lines under its header are only too short to be rows
  (`short-row`) is not: it reads as no rows, and its statement's totals, checked against those rows
  (`statement_summary_check`), fail when they count money.
- A preset knows its file by its header line or its headerless shape and refuses one whose first lines lack it, naming
  the columns missing or named twice. Under the header it refuses a file when more than half of the rows that reach
  their date (pending rows aside) cannot be read: another layout's file that passes the header check never loads as a
  few stray rows. A record the CSV reader joined across lines that cannot be read is an unclosed quote: refused.

## Dates and signs

- A preset states its date's column and pattern; nothing is detected. In a list of patterns, two that read one text as
  two different dates are refused at load (03/04/2030 is March 4 under `%m/%d/%Y` and April 3 under `%d/%m/%Y`), and
  every pattern must write and read back a whole date. A row whose date fits no pattern is `unparseable-date`.
- A date the calendar has not got (month 13, day 0, February 30th, February 29th in a common year) is no date in any
  format: a preset's patterns and OFX read none, and `etl/dates.py` `parse_date`, which the native formats read their
  dates with, refuses one in every form it reads, so a native format skips the row as `unparseable-row`. Every real
  date reads as before.
- A card's preset names the posting date when the file has one, as the bank feed and OFX date a card's rows, so where
  two sources of one account meet fewer purchases are in flight.
- The sign is stated: one signed column read `as_is` (a positive amount is money in) or `invert` (a positive amount is
  money out, as on a card export whose purchases are positive), or a debit and a credit column of unsigned amounts, a
  debit stored negative. A sign inside a debit or credit cell, or an amount in both, skips the row with its reason.
- A money cell is an amount as a statement writes it: digits, grouped in threes by commas if at all, an optional
  decimal point, at most one sign or one pair of parentheses, a dollar sign allowed. A decimal comma, a percent or a
  fraction of a cent is `unparseable-row`, never rounded into an amount.
- An amount cell of `--`, `n/a` or `N/A` is read as an empty one, no amount: `no-amount` in the Chase formats,
  `pseudo-row-no-amount` in the Bank of America bank format, and in a preset `no-amount` under `skip.empty_amount`,
  else `unparseable-row`. OFX reads such a `TRNAMT` as text that is no amount: `unparseable-row`.
- `import map` never infers either: it offers each common date pattern that reads every date of the column and asks
  whenever two read one date differently (a year-first pattern has no such twin), and it asks which way the sign runs,
  showing a row that spends and one that brings money in when the file has both.

## Balances

Only a checking or savings account (`CASH_KINDS`) takes a balance from a file. For one, the pipeline books the newest
`latest_balance` its files state and follows every file's `running_balances` in one check,
`import.rollforward.<account>` (`rollforward_check`): each row's balance must be the one before it plus the row's
amount. It fails with the date of the first row that breaks the chain and how many break (a row is missing or
altered), passes when a row was followed and none breaks, and is `info` when none could be followed; a gap (a skipped
row that moved the balance, or a row with no balance) restarts it. For any other kind a file's balance is not booked
and one `info` check says so, `import.balance_ignored.<account>`: a card's, a loan's or a brokerage's file balance has
its own sign and meaning, and such an account's balance comes from the bank feed. A statement's own totals (`summary`,
which the Bank of America bank format returns) are checked against its rows (`statement_summary_check`).

## OFX and QFX

One format, `ofx`, reads both (a QFX file is OFX with Intuit's own tags). An account's glob decides which files it
reads.

- **The tokenizer.** Both dialects — SGML 1.x, whose leaf tags have no end tag and whose line breaks are optional, and
  XML 2.x — are read by one small tokenizer from the file's `<OFX>` tag on, never by an XML parser, so a file's DTD or
  entities are never processed. The five named entities and numeric character references are decoded, any other is
  left as written; a stray `<!` or `<?` never swallows the tag after it; nesting far deeper than a statement's is
  refused. A file with no `<OFX>` tag in its first 64 KB (an HTML page saved as `.qfx`) is refused.
- **A row** is each `STMTTRN` in the transaction list of the file's statement (`STMTRS` for a bank account, `CCSTMTRS`
  for a card), ending at its own end tag, the next row or the end of an aggregate around it.
- **The date** is the first eight characters of the row's `DTPOSTED`: `20301031120000[-5:EST]` is 2030-10-31, never
  shifted by the zone after it. A row without a readable one is `unparseable-date`, never dated by another field.
- **The amount** is `TRNAMT` as the file states it: in OFX a card purchase is negative and a payment positive, as in
  this app, so nothing is turned round. A decimal comma written as digits, a comma and two digits is read; a fraction
  of a cent is no amount.
- **The description** is `NAME` (a `PAYEE` aggregate's own `NAME` when there is one), with `MEMO` added after a space
  when it is not empty and neither holds the other (ignoring case); when `NAME` is inside `MEMO` it is `MEMO`.
  `CHECKNUM` is not part of it. `TRNTYPE` is the bank's type.
- **`FITID`** decides repeats and conflicts only. A row whose `FITID`, date, amount and description repeat an earlier
  row of the file is dropped and counted (`repeated-fitid`); an id naming two different rows keeps both, and
  `fitid_rows` maps each id's SHA-256 digest (never the id: a bank may put account digits in it) to the rows it names.
  The pipeline joins the maps of the rows an account keeps over all its files, and an id naming more than one row warns,
  `import.ofx_conflict.<account>`: a transaction the bank corrected in a later download may count twice.
- **Pending** rows (a `BANKTRANLISTP` list, any `STMTTRNP`) are skipped and counted; a row outside a statement's list
  is `outside-transaction-list`.
- **One account and one statement a file.** A file of two statements is refused, in words that say whether they are
  one account's (download one statement period at a time) or several accounts' (one account at a time); so is an
  investment statement, a file with no statement, and a statement none of whose transactions can be read. A file that
  ends inside its statement is refused (rows may be missing: download it again). A statement with no transaction is no
  row and no failure.
- **Account numbers never leave the parser.** `ACCTID`, `BANKID` and the credentials a file can carry (`SECRETS`) are
  no leaves the reader reads, so the tokenizer never keeps their text; the statement's `ACCTID` and `BANKID`
  (`IDENTITY`) reach the reader only as a digest, to tell two statements of one account from two accounts'. No refusal
  names a value of the file, and no account or bank number reaches the store, a check or a command's output.
- **The encoding** declared in the header (or the XML declaration) is tried first, a UTF-8 byte-order mark puts UTF-8
  first, then UTF-8 and Windows-1252; a single-byte declaration over bytes that are valid UTF-8 with a character of
  several bytes is read as UTF-8, which banks mislabel.
- **The balance** is the statement's `LEDGERBAL` (the first eight characters of `DTASOF`, and `BALAMT`); `AVAILBAL` is
  not read.

## An account read from several sources

An account whose history comes in more than one format names each as a source (`accounts.yaml` `sources:`, read by
`Config.txn_sources` as `Source` entries, oldest first); `parser:` and `glob:` are the short form of one source, and an
account stating both forms is refused. `validate` checks that the first source has no `since`, every later one has one
after the one before it, no two share a pattern, and every `since` comes before the account's `simplefin_cutover`; it
refuses an account, in either form, that states both a cutover and `simplefin_balances_only: true` (the feed then
brings no row, so the cutover would drop file rows with nothing to replace them).

- **The clip.** A source holds the days from its `since` up to the next source's. A row a source's file holds outside
  them is left out before the store, its amount added to the account's dedup ledger, so the files still reconcile; a
  row keeps the uid its own file gives it, whichever source holds its day. A file's balance, totals and running
  balances are judged on all of its rows.
- **Matched twice.** A file the patterns of two sources match is read once, as the first one's, and fails one check,
  `import.matched_twice.<account>` (counted, the first five named). A source whose pattern matches no file warns,
  `source.<account>`.
- **What each source kept.** `import.sources.<account>` (`info`) gives, per source, its format, the rows it kept and
  the rows it left out. It warns when rows left out fall on days the source holding them has no file for (outside the
  first to last row date its files hold, or any day when it read none), so no source counts them; a row dated on or
  after the cutover of a feed that reports the account is the feed's, and does not count so.
- **The seam.** Two formats may date one purchase differently, by the day it was made or the day it posted, so a
  purchase made before a `since` and posted on or after it is kept by both sources, or by neither.
  `import.sources_seam.<account>` (`warn`) looks within five days of each `since` (`_SEAM_DAYS`) and pairs rows by
  signed amount: each row one source keeps with a copy the other leaves out (the ordinary overlap), then what is left of
  the two sources' kept rows as likely doubles and of their left-out rows as likely losses. It points and never acts:
  the join stays a clip by date, and its words give counts and days only. It cannot know which rows are one purchase:
  two purchases of one amount near the seam can pass for one, a purchase whose two copies differ in amount or lie more
  than five days apart is not seen, and where the files do not overlap it has nothing to compare.
- **With the feed.** The cutover clips the last source, once the feed has reported the account
  (`simplefin.csv_clip.<account>`), as it clips a single source.

## Text a new format stores

`bounded_description` is how every format but the native three stores a description: control characters removed (C0
but its whitespace, DEL, and C1 but the next-line character), the bidirectional controls (embeddings, overrides,
isolates and their pops) and marks (left-to-right, right-to-left, the Arabic letter mark) and the invisible format
characters (the soft hyphen, the zero-width space, the word joiner, the invisible operators U+2061 to U+2064, the
byte-order mark) removed too, so a description reads the way it is stored (an unclosed override would show it
reversed; an invisible character would make two descriptions that look the same differ), while the zero-width joiner
and non-joiner stay, which scripts and emoji need; then every whitespace run (the next-line character and the Unicode
line and paragraph separators among it) squashed to one space, and the text cut to its first `MAX_DESCRIPTION` (500)
characters. One `info` check per account,
`import.long_description.<account>`, counts the rows stored cut. A new format stores through it; the native three keep
their own rule, since their rows are known by their descriptions.

A description is data. Formula-leading characters (`=`, `+`, `-`, `@`), markup and script tags are stored as the file
has them, and the dashboard escapes what it shows. The `import` commands print a control or format character as its
escape.

## Hostile files

`tests/test_hostile_files.py` throws every hostile input at every format (the three native ones, the shipped presets,
the tests' two synthetic presets and `ofx`) through `Pipeline.parse_transactions` on a one-account household built in
`tmp_path`. Each
pair is read right, refused by its one check, or read with a row skipped for a stated reason; no exception escapes, no
case runs past a time bound, and no row is read with the wrong sign. The inputs: every format's valid file against
every other format (the wrong-bank matrix), a renamed header column, Windows-1252 and UTF-16 text, byte-order marks,
CR and mixed line ends, a quoted newline, totals rows, an HTML page, empty, NUL and random bytes, a sparse file over
the size cap (never read: its format's parse is never called), a pipe and a folder, 50,000 rows, a 2 MB line, hostile
descriptions (formula-leading, markup, control characters, 10,000 characters, right-to-left and combining characters,
bidirectional controls and invisible format characters), dates out of range or no calendar day, and amounts of zero, a fraction of a cent, a billion and with currency signs.
Its table of formats must hold every format of the release (`test_every_format_of_the_release_is_in_the_table`).

## Evidence, and adding a preset

A shipped preset states how far its layout is known: `bank-documented` (the bank's own help describes the columns),
`oss-definition` (an open-source project under a licence that allows its use defines them: MIT, BSD, Apache or GPL-3,
never GPL-2.0-only, named with its licence), `widely-reported` (several independent public descriptions agree and none
is such a definition) or `sample-verified` (run on a real export). An institution with none of these gets no preset:
its OFX or QFX download and `import map` cover it. To add one:

1. Research the layout from the bank's own help, or from a project under such a licence: the columns, the date
   pattern, which way the sign runs, a pending marker, a totals line. A card's preset dates by the posting date.
2. Write `etl/importers/presets/<id>.yaml` (an id of lowercase letters, digits and `_`, the file named after it) with
   `source` (the pages, their licence, the day they were read), `evidence`, `institution`, `download`, and a comment
   saying where the sign comes from.
3. Write an invented sample, `tests/importers/samples/<id>/<name>.csv`: invented people and employers, merchants from
   `starter/vendor_rules.yaml`, placeholder account numbers; never a real statement with its values changed.
4. Append to `tests/importers/presets.lock` what `python tests/test_csv_presets.py` prints.
5. Add the preset to `SHIPPED` in `tests/test_csv_presets.py` (its sample, how many rows it reads, three of them): the
   parametrised `test_each_shipped_preset_reads_its_sample_and_detect_names_it_alone` reads the sample and checks that
   `import detect` names this format alone. Add it to the pins of the release's formats too:
   `test_the_shipped_presets_are_formats_in_the_registry`, `test_the_parser_registry_is_exactly_the_native_formats`
   (`tests/test_txn_parsers.py`) and `FORMATS` in `tests/test_hostile_files.py`.
6. Regenerate the bank table, `python -m etl import banks --write`:
   `test_the_committed_bank_table_is_the_one_the_formats_give` fails until the committed page is the generated one.

## The commands

`python -m etl import …` (`etl/import_cli.py`) finds out what a file is before an account trusts it. Offline; only
`map` and `banks --write` write anything.

- `detect FILE` tries every format of the release and the household's own presets (one with a problem is left out,
  saying so), whatever the file's name says: a format reads the file when it returns a row. It prints each format that
  reads it, or the three nearest misses with their reasons and the `map` line to try; exit 0 when exactly one reads it.
- `preview FILE --format NAME [--account ID]` prints what that format reads: the rows, the lines skipped by reason, the
  dates, the first and last rows and a balance the file states, never the file's sum. With an account it also counts
  the file's rows the stored data already holds, by uid.
- `map FILE --id NAME [--answers FILE] [--force]` asks about the file's columns at a terminal, or reads the answers
  from a YAML file (`ANSWERS` lists the keys), builds the preset through `build_preset`, the loader every preset passes,
  reads the file with it, and only then writes `presets/NAME.yaml` into the config folder (never over one without
  `--force`) and prints the `accounts.yaml` lines to add. It refuses a name a release format has, and writes nothing
  for a layout that reads no row of the file.
- `banks [--write]` prints the table of the banks this release reads (`docs/importing-banks.md`): the built-in formats
  (`NATIVE_BANKS`) and each shipped preset's own lines. `--write` writes it into the tree.

## The bank feed's fetcher

`etl/simplefin.py` is the one networked module, and the pipeline never imports it. Every request for accounts asks
for protocol version 2 (`version=2`); a fetch asks for 90 days while the store holds no snapshot, the most one request
may ask for, and for 35 once it holds one (an explicit number of days wins, capped at 90); it never asks for pending
rows. It reaches https only, with the certificate verified, through an opener that follows no redirect (a 30x answer
fails the request in one line, so the credential is never sent to an address the fetcher has not judged). An https
request goes through the proxy the environment names (`https_proxy` and the rest), as urllib's default does, so a
household behind a proxy still reaches the bridge; the test seam's plain-HTTP request goes through none. No message
carries the setup token, which may be unspent: a claim URL refused before the claim is named by its host alone, and a
failed claim by its path without the token's segment. Each entry of the answer's `errors` or `errlist`, a string or an object in either, is one `WARN simplefin`
line; an object that names a connection is worded as version 1 words it, `Connection to <name> may need attention.`,
the name read from the answer's `connections`, since `deploy/refresh.sh` reads its re-auth alert's names from that
wording. The one exception to https is the test seam,
`FF_SIMPLEFIN_TEST_ORIGIN` (`TEST_ORIGIN_VARIABLE`): set to exactly `http://127.0.0.1:<port>`, it lets that one loopback
origin be reached over plain HTTP; any other host or port, `localhost` by name, or any other value opens nothing, and
production never sets it. `tests/fake_bridge.py` is that origin in the tests: a stdlib HTTP server on `127.0.0.1` that
answers the public protocol (a single-use claim, 402 and 403, the window, pending rows, balances only, version 2's
`errlist`, a stall, a redirect), driven by `tests/test_simplefin_bridge.py`.

The feed's reader, `etl/parsers/simplefin.py`, keeps posted, non-pending USD rows, dates them in the household's
zone and takes each row's occurrence index from the feed's transaction id. On a brokerage it types cash events from
their wording: a buy or sell of shares or of an option leg is an internal investment move, and a dividend, margin
interest and card cash back swept in are filed under three subcategories of its own (Dividends, Margin Interest,
Reimbursement). These brokerage cash events are recognised by wording alone, so a brokerage that words them
differently leaves them as ordinary rows.
