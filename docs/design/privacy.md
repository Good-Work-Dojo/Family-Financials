# Privacy mode

> Scope: how privacy mode hides every amount on screen, how a new money surface stays covered, and the test net that
> proves it. The hard rules are at the top of `app/AGENTS.md`; this page is the mechanics. Describes the system as it
> is; verify names against the code (`app/src/lib/privacy.ts`, `app/src/lib/format.ts`, `app/src/smoke.test.tsx`).

## The flag

A per-device flag in localStorage (`PRIVACY_KEY`, like the theme), held in `lib/privacy.ts` as a cached module
variable: read once at import, changed by `setPrivate`, and kept in step with other tabs by ONE `storage` listener (a
cleared storage is re-read). A storage that cannot be read leaves the flag as it is, so a masked page stays masked —
it never fails open. `usePrivacy()` subscribes. `Alt+Shift+P` (`usePrivacyHotkey`, by physical key) toggles it
anywhere; buttons sit in the sidebar, in the phone's top bar (the eye, amber and slashed while on —
`components/MobileTopBar.tsx`, outside the keyed `<main>`) and on Data (`card-privacy`, `sections/data/PrivacyCard.tsx`).

## Where the mask lives

- **The four money formatters** of `lib/format.ts` — `fmtUSD`, `fmtUSD0`, `fmtCompact`, `fmtDelta` — return `MASK`
  while it is on, so every KPI, table, axis, tooltip and accessible name goes dark together and a new surface cannot
  forget. All four print the same minus (U+2212) and never a negative zero; `moneyValue` / `moneyTooltip`
  (`lib/echarts.ts`) print "—" for a null point.
- `<main>` is keyed on theme + privacy (`App.tsx`), so chart options rebuild with masked axes — and page-local state
  (an open row, a selection, an uncommitted draft) resets on a toggle. `EChart` blurs the canvas only (the tooltip DOM
  is already masked). `CurrencyInput` and `MaskedNumberInput` show the mask at rest and the number while focused; a
  dollar slider is `AmountRange`: its thumb is blurred and it runs over its steps, not the amounts, so its value, min
  and max hold no amount (a plain slider keeps the figure a page seeds it with, unrounded, in its value attribute).
- **Free text** that states amounts in its own words goes through `maskMoneyText` (never call it on a number): ETL
  quality-check details, open questions and data gaps (which print amounts with no currency sign), and a
  bank descriptor in the register's open row and the Review inbox. It masks currency-marked amounts (a sign or a whole
  currency code, with a k / million / billion suffix) and bare grouped or two-decimal numbers; a number followed by `%`,
  dates, years and ids are left alone.
- Names, dates, counts, percentages and months of cover (`fmtMonths`) stay visible.
- **CSV exports are never masked** — they are for the family's own spreadsheet — and every CSV button says so in words
  (`CsvNote`, not a `title`). A chart's "view data" table formats through the table's `format` (default `fmtUSD0`, so
  it is masked); its CSV holds the raw rows.

## The net

- `lib/privacy.test.ts` fails on `toFixed(2)` in `sections/` and `components/`, on an Intl currency style or a dollar
  template literal anywhere in `lib/`, `sections/` and `components/` but `format.ts`, and on a chart formatter that
  prints a raw `{c}`.
- `smoke.test.tsx` "the privacy net over every route" mounts every route with privacy ON and scans each element's own
  text, the whole `textContent`, every title / aria-label / placeholder / value, and every chart option's formatters
  (called with integer samples). It is written independently of `lib/privacy.ts` and proven non-vacuous by a positive
  control with privacy off. Its scanners (`AMOUNTS`, `STATIC_OK`, `textHits`, `optionHits`) live in
  `app/src/test/moneyScan.ts`, which the matrix's privacy check (`app/src/matrix.test.tsx`) shares. Static prose that
  states a rule or counts rows sits in the `STATIC_OK` allowlist, which
  names the counted nouns on purpose: a NEW legitimate figure of that kind is added there deliberately, never by
  loosening a pattern. Each alternative must match the text of some scanned route or surface: the net records what
  matched, and "every STATIC_OK alternative matches on some route" fails for an entry nothing prints any more.
- Content that only appears after an interaction (an open drill, an open split purchase, an editor, a manual asset's
  fields on Settings, whose amount boxes show the mask at rest and the figure only while focused) is covered by the
  `SURFACES` list in the same file: each entry opens its surface and is scanned the same way; `money: true` says it
  shows amounts. A new conditional money surface gets a row there. `rows.all` makes the virtualized register render
  every row so an entry can open one.
- It needs exports: `tools/verify/ci.sh` runs it on the demo household `full` (`tools/demo`), every route and surface
  of a household that has every feature, with the clock at its ingest instant (`docs/design/deploy-and-gates.md`). A
  plain `vitest run` without exports skips it.

Known gaps: `?min` / `?max` carry raw amounts in the URL; an uncommitted `CurrencyInput` draft is
dropped on a toggle.
