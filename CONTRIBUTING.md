# Contributing to Family Financials

Thank you for helping. This page says how a change gets in. `AGENTS.md` is the map of the code and its rules, and
`docs/design/` holds the mechanics; read the part you are changing first.

## Never real financial data

Nothing real goes into an issue, a pull request, a test, a fixture, a screenshot or a commit message: no statements,
transactions, balances, account numbers, or names of people in a household, yours included. Tests and examples use the
invented demo household (`tools/verify/demo.sh`, `./ff demo`) or rows you invent. An invented row uses invented
people and employers, merchants from `starter/vendor_rules.yaml` and account numbers of zeros, never a real statement
with its values changed. A pull request that holds real data is closed, and its history has to be rewritten.

## The gate

`tools/verify/ci.sh` runs every check in Docker (you need no Python or Node on your machine): the Python lint and
tests, the dashboard's lint, tests and type check, the invented households end to end, the runtime images, the
dependency licences, a secret scan, the workflows' checks and the whole stack in a browser. It must end with `CI OK`,
and it runs on every pull request. For a quick loop on a few tests, `tools/verify/narrow.sh pytest <paths>` or
`tools/verify/narrow.sh vitest <paths>` runs them in the images the gate last built (`tools/verify/README.md`).

A change in behaviour comes with a test that fails without it. Never weaken an existing assertion to make a change
pass; if one is wrong, say why in the pull request.

## Adding a bank's statement format

Most banks need no code: a preset (a short YAML file) describes the columns of a CSV export. To ship one for every
household, follow "Evidence, and adding a preset" in `docs/design/importers.md`: the layout's source and its licence,
the preset file, an invented sample in the file's shape, the lock and test entries, and the regenerated bank table. A
format never changes the identifiers it gives rows once it ships, because a household's answers are keyed by them. If
you cannot write the preset yourself, open a "Bank format request" with the header row and a few invented rows.

## Words, comments and the dashboard

- Words a person reads (a page, a check's message, a command's output) say what is wrong and what fixes it, in plain
  words: no export file name, storage key or command in a page's text (`tests/test_page_copy.py`).
- Money in the dashboard is printed only through the masked formatters (`app/AGENTS.md`).
- Comments and test titles say what the code does and why, in the present tense. No ticket, task or review numbers,
  and no "now" or "previously" (`tests/test_comment_hygiene.py`).
- Every source file starts with the licence header (`tools/verify/spdx.py --write` adds it).
- New runtime dependencies must be under a licence the gate allows (`tools/verify/licences.py`); no GPL at run time.

## The Contributor Licence Agreement

Before a first pull request is merged, its author signs `CLA.md` through CLA Assistant, a GitHub App the maintainer
installs on the repository (pointed at a Gist of `CLA.md`), which asks on the pull request. You keep the copyright in what you contribute; the agreement lets the project distribute it, including under
other licences later (its section 4 says how).

## Pull requests

Keep a pull request to one change, describe what it does and how you checked it, and fill in the template's
checklist. Security problems are never reported in an issue or a pull request: see `SECURITY.md`. Everyone taking
part follows the `CODE_OF_CONDUCT.md`.
