# The demo household generator

`tools/demo` writes an invented household — the Sample household: Alex and Sam, their children Robin and Jamie — as
everything the ETL reads, so the engine, the dashboard and their tests can run on data that belongs to nobody. It
simulates a double-entry ledger in integer cents, writes the config, the statement files, the bank-feed snapshots and
the family's review answers that ledger implies, and writes beside them `truth.json`: what the exports must then
equal, declared from the ledger alone. Nothing here imports the engine's enrichment, pipeline, net worth, exports,
config or validator (the only `etl` imports are in `uids.py`), so the engine is checked against an independent
statement, never against itself.

## The command

```
python -m tools.demo.generate --profile full --end 2026-10-31 --out <dir>
FF_HOME=<dir> FF_NOW=2026-10-31T18:00:00 python -m etl ingest
```

The first writes `<dir>/config/`, `<dir>/imports/`, `<dir>/store/simplefin/snapshots/`,
`<dir>/state/family/answers.json` (when the family answered anything), `<dir>/state/family/manual_assets.json` (when
it entered an asset in the app, in the shape the family store keeps; both where an install's store keeps them) and
`<dir>/truth.json`, and prints the `FF_NOW` to ingest at (an hour after the end
day's last snapshot). `<dir>` must be absent or empty. Two runs write identical bytes, under any hash seed; no file
holds the path it was written to. In this repository the host has no Python dependencies: `tools/verify/demo.sh <dir>
[<profile>...]` runs both inside the ETL image, offline, one profile per container.

## The profiles

| Profile | What it is |
|---|---|
| `full` | Nine accounts, a home and a car over 28 months, statement files then the feed: every feature below. |
| `new` | Checking, Alex's card and the brokerage, connected to the feed five days before the end; one snapshot a day. |
| `tiny` | One checking account from two monthly statement files, no feed; income and everyday spending. |
| `empty` | The household and `accounts: {}`: no file, no snapshot, no row. |
| `no-investments` | `full` without the brokerage and the 401(k); every other row is `full`'s. |
| `no-income` | `full` without pay or the unknown deposit (savings interest stays); checking opens with enough to spend. |

`full`'s features: pay with a job change, an unknown credit and a refund; everyday spending; the seam between
overlapping statement exports and the feed; every way a row gets its category, below and above the review threshold;
transfers, card payments and person-to-person rows; pending and future-dated rows, a gap in the data and an open
question; a mortgage and the home, entered in the app with three rising values; a car accounts.yaml states, bought
eight months before the end (paid from checking with savings' money that day) and valued six weeks later, so the month
end between holds it carried back; a brokerage on margin and a 401(k) the feed stops reporting (a balance-only
account, so no month's cash-flow coverage depends on it); a child's savings and a certificate; subscriptions (one
with a price change, one cancelled, one lapsed); a split vendor; review answers that teach a vendor and an override
that beats one; Home buying switched on in household.yaml (`full` alone switches a module); the AI step configured (household.yaml `ai:`) but off, no key and no consent, with vendors on Sam's card that
it would ask about, keep back (a member's name, a four-digit run, seen once) or never consider (a person paid through
PayPal, an internal transfer). `no-investments` and `no-income` keep the home, the car and the AI vendors.

## truth.json

`figures` holds, per section: every stored row (type, category, subcategory, source, vendor, review flag, internal,
earner, split parent and part), each account's rows, sum and window, each month's income, spending by category,
investing and full coverage, the coverage floor, income phases, the seam, transfer pairs, open questions and data
gaps, pending rows (and how many each snapshot lists), the net-worth snapshots (each hand-stated value at the value in
force on the day, held at its first figure before that, absent before the day it was acquired; each snapshot's
`manual_rows` name each such row's id, value, the date of the value it is held at and whether it is carried back), the
daily line's last point and its value on each day a hand-stated value takes a value or is acquired within the
accounts' span (`daily_points`), holdings, the mortgage block, ledger drift, the quality checks that are not `pass`,
recurring items, answers, learned vendors, the split rule, `ai` (the vendors the AI step asks about, those it keeps
back and those it never considers, each with why, their rows, and ai_status.json's preview counts), `manual_assets`
(the hand-stated values as the generated
config and the app's file state them, each with its id and origin, which `accounts.json` `manual_assets` must equal)
and `setup` (the accounts, whether one names a feed id, the statement files written and the hand-stated values, which
`manifest.json` `setup` must equal key by key). `household` holds the household's people, joint label, surname, name
and time zone, the last two compared with `assumptions.json`'s household block. `planted` names the rows and values
each feature produced, for
the tests to find: among them `home.entry` and `car.entry` (the two assets' ids), `car.purchase` (the payment to the
dealer) and `modules` (the switches household.yaml states, `{}` without any, compared with `assumptions.json`
`modules`).

The tests generate each profile, ingest it in-process and compare the exports with truth through
`tests/demo_checks.py`: one function per section, reporting differences in both directions; `mismatches` is all of
them. `tests/test_demo_profiles.py` runs every profile, `full` and `tiny` at end dates across the calendar, and checks
determinism and the blessed hashes; the other `tests/test_demo_*.py` files check `full`'s features one by one.

## Adding a behaviour

A behaviour is `fn(plan) -> None`, listed in a profile's `behaviours` (a behaviour that reads balances other
behaviours decide, such as a card payment, is a `settlement`, run after all of them). It:

- draws only from random streams it names itself (`plan.stream`), so it moves no other behaviour's rows;
- posts every row with the `Expect` the engine must make of it (`plan.post`, `plan.move`) and adds the config entries
  that make the engine see the row that way (`plan.add`, `plan.put`);
- fits its window: it posts a feature only when the plan's dates hold it (`behaviours.fits`), and otherwise leaves the
  feature out whole, with its config and its handles;
- plants a handle for each feature (`plan.plant`), and declares what the household knows beyond its rows: the
  recurring charges it pays, its answers and the rows they reach, the vendors they teach, its overrides, the assets
  it entered in the app (`plan.enter`).

Truth then states the feature's figures from those declarations, never from the engine's code.

## Re-blessing

`tools/demo/inputs.sha256` holds one `<hash>  <profile>` line per profile: the hash of everything the generator writes
for the engine (config, imports, store, state; the learned rules aside) and of `truth.json`, generated to 2026-10-31.
`python -m tools.demo.generate --check` regenerates the six and exits 1 naming each profile whose inputs moved;
`--bless` rewrites the file. Any change to what the generator writes moves a hash: re-bless in the same commit as the
change, after reading the diff of the generated trees to be sure the change is the one intended.
