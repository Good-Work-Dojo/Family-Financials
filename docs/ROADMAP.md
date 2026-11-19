# Roadmap

What is known to be missing or simplified, in plain words. Nothing here has a date. If one of these matters to you,
say so in an issue (or a discussion), or pick it up: [CONTRIBUTING.md](../CONTRIBUTING.md) says how.

## Not in this release

Whole features this release does not have:

- **Education-savings planning**: saving toward a child's schooling, with its own accounts and goals.
- **Brokerage position files**: reading a brokerage's holdings from a positions file it exports.
- **Payment-app statement importers**: reading a payment app's own statement export.
- **Crypto prices**: nothing fetches a coin's price; enter crypto as a hand-entered asset and update its value
  yourself.
- **Bank-feed history beyond 90 days**: the feed gives 90 days at first; older history comes from statement files.
- **A monthly summary message** sent to the household.

## Figures the dashboard labels but does not model yet

Each of these is shown with words that say what it leaves out; a proper model would remove the caveat.

- **Retirement contributions per adult.** Projections count the growth of the retirement accounts a household maps,
  but no setting states what each adult still contributes, so a retirement date reads as reached by "growth alone".
- **The FIRE crossing** compares a target stated in today's dollars with balances that grow in nominal dollars.
- **Liquid cash and card balances.** Liquid cash leaves out what is owed on credit cards, while the forecast's
  spending includes card purchases; and a card's balance change counts both through spending and in the debt bar.
- **Asset-class targets** set in the dashboard cannot clear the targets the config states: an empty set of targets
  in the app falls back to the config's.
- **A snapshot missing an account** is marked on Home, but the share figures elsewhere do not say so.
- **Monthly items with an irregular day**: one can skip a bar of the twelve-month chart, its second expected date can
  fall on the "lapsed" cut-off, and a bill whose day moves by two days is followed only at its next payment.

## Bank data

- **More bank layouts** shipped as presets: each one helps every household with that bank
  ([importing.md](importing.md)).
- **More wordings of brokerage cash events**: the feed reader recognises dividends, trades, margin interest and
  cash back by their wording, and an event worded otherwise stays an ordinary row.
- **Automatic pairing of transfers** to and from brokerage and loan accounts, which today need a transfer rule.
- **A new bank-feed id named on the Data page** when a connection is made again (today the new id is read from the
  feed's snapshot file).
- **`import preview --account`** counting rows already stored for an account that reads several sources.
- **Config checks** that would catch more mistakes early: a brokerage fed by the bank feed states its asset class;
  every transfer group a rule names exists and is used; a learning exclusion names a vendor the rules actually
  produce.

## The install

- **An upgrade refreshes the install's `compose.yaml`** from the new release (today an install keeps the one it was
  set up with).
- **`./ff import` and `./ff rules` mount the install read-only** where they write nothing.
- **Rootless Docker and remapped user ids**: the services cannot read the install's private folders there today.
- **Tested on macOS (Docker Desktop), WSL and SELinux in enforcing mode**, none of which is tested now.
- **Alerts for a failing rebuild once per streak**, as the bank feed's failures are, instead of on every run.
- **The web server's HTTP-to-HTTPS redirect** carrying the same security headers as every other answer.

## The family store

- **Module switches merged one by one** when two devices change different switches at the same time (today the
  newer set of switches wins whole).
- **A late answer**: an answer whose save reaches the store late, stamped ahead of a newer one, can still win over it.

## Before the export format is final

The JSON files the rebuild writes are versioned, and the dashboard reads every older version it knows. Before the
format is declared stable for outside readers, keys no page reads and fields no row uses will be removed.
