# AGENTS.md — Family Financials, for contributors

A **local-first** household FP&A / net-worth app: a re-runnable offline Python ETL (`etl/`) reads a household's config,
statement files and bank-feed snapshots into SQLite + JSON exports, and a React dashboard (`app/`) reads only those
exports. This file is the map: `docs/configuration.md` is every config key, `docs/design/*.md` hold the mechanics (index
at the end). New here: `docs/architecture.md` is the overview, `CONTRIBUTING.md` how a change gets in (no real data,
the gate, the CLA). Where a section below disagrees with "Hard rules", the hard rules win.

## Hard rules
- **Runner:** every workflow `runs-on` is a literal from `{ubuntu-24.04, ubuntu-24.04-arm}`; no expressions or lists.
- **No household strings in new work**: fixtures, examples and docs use the synthetic demo household.
- **The offline path stays offline.** `ingest` and the dashboard make no third-party calls; each networked module is
  a separate command writing a local store, never imported by `etl/pipeline.py`. No analytics or CDN calls.
- **Money: integer cents inside the ETL, dollars in JSON exports; signed, negative = outflow.** In the app, money is
  printed only through the masked formatters (`app/AGENTS.md`).
- **Determinism:** two ingests of identical inputs give identical exports, except the manifest's generation stamps.
- **Honesty over completeness**: never fabricate or silently guess (the guards below). New features add provenance.
- **Exports:** every shape change moves `SCHEMA_VERSION` (`etl/exports.py`), `EXPECTED_SCHEMA_VERSION`, `schemas.ts`
  and `types.ts` together with the contract test. A key or file is removed or renamed only in a major release.
- **Capability split is sacred:** the browser-reachable container holds no feed credential and no ETL write mount.
- **Config, not code**: rules and household facts live in a household's own `config/*.yaml`, never in the tree;
  never hand-edit `state/family/answers.json` or `state/engine/learned_rules.json`.
- **Words a person reads** say what is missing and what fixes it in plain words, a config key at most as a hint: no
  export file, storage key or command in a page's text or an ETL check's `detail` (`tests/test_page_copy.py`).
- **Context files**: edit this file and `app/AGENTS.md`, never the `CLAUDE.md` shims; never put an `@path` import in
  an AGENTS.md. Together the two stay ≤ 40,000 bytes (`tests/test_docs_size.py`).
- **Comments and the dashboard's test titles say what the code does and why, in the present tense** — no phase,
  task, decision or review ids (`tests/test_comment_hygiene.py`).

## Commands (run from the repo root)
| Command | What |
|---|---|
| `make ingest` | Re-parse everything offline → `data/finance.db` + `data/exports/*.json` |
| `python -m etl validate` / `make validate` | Every config problem at once, one `file: key.path: what — fix` line each (exit 1); `ingest` and `rules` refuse such a config with the same lines (exit 2, nothing written) |
| `FF_HOME=<a copy of examples/minimal> python -m etl ingest` | The smallest household end to end (`examples/minimal/README.md`) |
| `python -m etl import detect\|preview\|map\|banks` | Which formats read a file, what one reads, a household preset from a file, the bank table (offline) |
| `./ff demo\|init`, `up\|down`, `doctor` | An install (`docs/design/platform.md`): the demo or a household's setup, the stack, its checks; also `backup`, `restore`, `upgrade`, `passcode`, `refresh`, `ai`, and `build` (in a checkout: both images, under the names `ff` runs) |
| `make app` / `make` / `build` / `serve` | Dashboard dev server / `ingest` + `app` / production build / preview |
| `make test` / `make lint` | lint + `pytest` + `vitest` + `tsc` (`test-etl` / `test-app`) / `ruff check etl tests deploy tools` + `eslint`; the harness's own tests run only in `ci.sh` |
| `make refresh` / `fetch` / `backfill` | `fetch` + `ingest` / SimpleFIN: 90d first, then ~35d / a 90d gap fill (the stack runs `deploy/refresh.sh`) |
| `python -m etl rules preview --file C.yaml` / `rules overlaps` | Dry-run candidate keyword rules / list dead and shadowed rules (offline, writes nothing; `./ff rules …` in an install) |
| `python -m etl rules starter-diff [--starter DIR]` | The starter entries your rule files lack, a line each and a count (offline; `starter/README.md`) |
| `tools/verify/demo.sh <dir> [profile…]` | Six invented households, generated and ingested offline (`full` has every feature), each an `FF_HOME` tree with `truth.json` (`tools/demo/README.md`) |
| `tools/verify/ci.sh [EXPORTS_DIR]` | Every gate in docker (`tools/verify/README.md`). Without exports: the demo households, vitest on `full` (a skip fails), the contract and route matrix on each, two more zones, the runtime images, the stack; ends `gate: pytest P passed; vitest P passed / 0 failed / 0 skipped; contract 6/6; matrix 8/8; stack P/T` and `CI OK`. With exports: eslint, tsc and the tests on them, as real (`FF_SUITE_LOG`; `FF_DEMO_EXPORTS=1` if synthetic) |
| `tools/verify/narrow.sh pytest\|vitest <paths>` | A few tests of the worktree in the images the gate last built (no network) |
| `make clean` | `rm -rf data/` **only**; the DB rebuilds from the snapshots |

Toolchain: `uv` (Python ≥3.12, locked) and `node`/`npm`, or docker (`tools/verify/ci.sh`).

## Containers, the store and the refresh
**Split by capability** (`docs/design/platform.md`): `web` (Caddy) and `store` (`deploy/scenario_store.py`) are
browser-reachable, so they hold no feed, AI or alert credential and no ETL write mount (the store *asks* for a refresh
or an ingest); `scheduler` and `refresher` (`deploy/refresh_runner.py`) run `deploy/refresh.sh`. `state/` is split
by writer; shared folders are used link-proof (`deploy/safe_files.py`). Don't collapse them.
- **Sign-in** (`deploy/auth.py`): scrypt passcodes, server-side sessions, a throttle, an Origin rule; Caddy's
  `forward_auth` asks `/auth/check` for every path outside the public set.
- **The store** serves four family documents from `state/family/` (`STORES`): `scenarios.json`, `answers.json`,
  `prefs.json` and `manual_assets.json` (`/api/manual-assets`), each merged by its own rule; it checks shape, the
  engine meaning. `docs/design/data-and-sync.md`.
- **The refresh view** (`/api/refresh`): `POST` asks for a refresh, rate-limited (15 min apart, 6 manual a local day;
  `deploy/refresh_state.py`, `decide()` is pure). `POST {"mode": "ingest"}`, and a manual-assets save that changed the
  document, touch the marker `ingest_wanted`: the refresher runs `refresh.sh ingest` (no fetch, no feed budget), at
  most 48 a local day. `GET` adds `schedule` (the scheduler's `schedule.json`) and `ingest`. Only the refresher and
  `refresh.sh` write `status.json`. `docs/design/information-architecture.md` ("The server side").

## Layout & where to change things
```
config/   a household's own YAML, git-ignored, never in the tree or an image (compose mounts it read-only):
          household.yaml and accounts.yaml required, the rule files and presets/ optional (docs/configuration.md)
starter/  the rule files a new household copies into its config/ (starter/README.md; tests/test_starter.py)
examples/ minimal/: the smallest config that runs, in the FF_HOME layout (tests/test_minimal_household.py)
etl/      the pipeline (`python -m etl`); parsers/simplefin.py the bank feed's reader; quality/reconcile.py the checks
  importers/  registry.py (format name → parse), the native formats (chase_checking, chase_card, bofa_bank), ofx.py,
          csv_preset.py (the preset engine), presets/*.yaml (the shipped presets: docs/importing-banks.md)
  import_cli.py  the `import` commands: what a file is, a household's own preset
  config.py  Config (loads and checks config/; every stage reads it), FeedConfig (what a fetch reads), Paths
  models.py  the type sets, RESERVED, CATEGORY_SOURCES, MODULE_IDS, the default class by kind
  enrich/ in `Pipeline.enrich` order: vendors → transfers phase A → income → answers.load + learned.load/derive →
          categorize → transfers B → release of unpaired `kinds:` legs (income + categorize again) → generic pairs
          → transfers C → internal resync (`set_internal`) → ai → splits → answers → overrides → un-split retyped
          parents → answers.verify → income phases → recurring. Each module's docstring is its spec
  validate.py  config problems, the key registry (KEYS, which docs/configuration.md lists exactly), every check
  networth.py  net worth's per-account series map, the snapshot and the daily line read from it (pure; no Config)
  manual_assets.py  the hand-entered assets: the store document read, checked and merged with the config's entries
  exports.py  the export shapes, SCHEMA_VERSION (its comment lists each version), PROJECTION_DEFAULTS
  simplefin.py  NETWORKED, as ai/providers.py is: dated snapshots in a sibling store the pipeline reads
ff, compose.yaml  the install's command and its four services; etl/install.py and etl/*_cli.py hold ff's commands
deploy/   Dockerfiles, Caddyfile, auth.py + scenario_store.py (sign-in, family store), make_crontab.py, alert.py,
          the refresh (refresh.sh, refresh_runner.py, refresh_state.py), safe_files.py, export_diff.py (README.md)
tests/    pytest on the demo household and synthetic fixtures
tools/    verify/ (the gate and its helpers: ci, demo, demo site, export-diff, screenshots, licences, spdx, release
          names; tools/verify/README.md), demo/ (the demo generator: six profiles, each with truth.json;
          tools/demo/README.md), maintainer/ (post-publish and adoption-report, run by hand once a release is public)
docs/     configuration.md (every key), importing.md, security.md, architecture.md, ROADMAP.md,
          ai-categorization.md, images/ (the README's pictures, from `full`), design/ (mechanics)
```

**Where the ETL finds its files** (`Paths`, `etl/config.py`; its docstring has the table): a `Config` argument, else a
variable (`FF_CONFIG_DIR`, `FF_IMPORTS_DIR`, `FF_STORE_DIR`, `FF_SECRETS_DIR`, `FF_STATE_DIR`, `FF_DATA_DIR`), else
the `FF_HOME` tree (what compose mounts), else the developer layout (a source checkout with `config/` beside it).

**Pipeline order** (`pipeline.py:run`): `parse_transactions → parse_simplefin → enrich → parse_assets →
finalize_quality → persist_learned → persist → export`. Put new logic in the matching stage. `parse_assets` builds
net worth's one series map (`networth.build_series`: the balances plus the manual assets).

## Recipes
- **New transaction source:** a preset first (`import map` or a YAML file: a household's own in `config/presets/`, a
  shipped one in `etl/importers/presets/` with an invented sample and `presets.lock` entries); a native module only
  for a layout no preset describes. An account names it as `parser` + `glob`, or as one of its `sources:` from a
  `since:`. A format never changes the uids it gives: `docs/design/importers.md`.
- **New asset/balance source:** dated `BalanceObs` for a registry account (a cash account's file balance, or the feed),
  which `etl/networth.py` turns into that account's series; every row decision (class, liquidity, carry, the margin
  breakdown) lives there. A value with no feed is a manual asset.
- **Surface new ETL data in the app:** `etl/exports.py` → `app/src/data/types.ts` and `schemas.ts` (two independent
  descriptions, same commit) → `DataContext.loadAppData` → the section; the app never queries the DB. Every shape
  change, a widened type included, bumps `SCHEMA_VERSION` and `EXPECTED_SCHEMA_VERSION` (`contract.test.ts` pins them);
  an older export can reach a newer bundle, so every new key gets a default (`docs/design/data-and-sync.md`).
- **A new config key:** its check and its row in `validate.py` `KEYS` and in `docs/configuration.md`
  (`tests/test_validate.py`). A default the export needs lives in one table the document and the starter file state
  too (`PROJECTION_DEFAULTS`, `tests/test_minimal_household.py`).
- **Categorization/vendor fixes:** edit `config/*.yaml`, run `python -m etl validate` (`ingest` refuses the
  config until it passes), dry-run with `rules preview`, check with `rules overlaps`, re-ingest. The P2P
  catch-all stays last in its stage; family-only facts go in `config/overrides.yaml` (highest precedence). Day to day
  the family answers rows in the Review inbox (`/review` → `state/family/answers.json` → the next ingest; never `type`).
  Precedence, rule shapes, learned rules: `docs/design/categorization-and-learned-rules.md`.
- **Splitting a mixed-basket vendor** (rule-driven only; no manual split editor): its pattern's `split:` legs (`pct`
  or `amount`, exactly one `remainder`) and a `note:` make every purchase a display-only parent plus categorized
  parts. `docs/design/splits.md`. A new aggregate or count decides parts or parents and adds a "counted once" test.
- **Renaming a vendor or category:** a `renames:` entry in `vendor_rules.yaml` / `category_rules.yaml`, never an edit
  of stored data; chains and cycles are refused at load. `docs/design/renames.md`.
- **In-app preferences:** budgets, the target mix and targets, projection defaults, payroll facts, saved views and
  reports and the module switches are the app's (`state/family/prefs.json`, merged over `assumptions.json` in
  `app/src/lib/prefs.ts`; the config stays the seed; no ingest). A manual-asset save asks for an ingest itself.
- **Documents:** a link, an anchor, a `./ff` command or the heading a page quotes is checked
  (`tests/test_documents.py`); a README picture is a `tools/verify/screenshots.sh` shot of `full`, at most 1 MiB with
  image chunks only (`tests/test_doc_images.py`). A user document never names a test-only variable.

## Honesty guards — invariants, don't break them
- **Per-account reconciliation** (`finalize_quality`): the signed sum of stored rows equals the raw files' sums net of
  dedup, else the check **fails**.
- **Split purchases count once.** A split parent is in no money sum and no money-row tally (`splits.count_rows`); the
  parts carry the amounts; `recurring.detect`, `vendor_registry.build` and `rules_cli` read parents; ad-hoc SQL
  filters `is_parent = 0`. `splits.sum_identity` **fails** when a parent ≠ Σ its parts. `docs/design/splits.md`.
- **Ledger drift** (`reconcile.<id>.ledger_drift`) is `info` only: feed balances include pending items the parser
  skips, so drift ≈ the pending total; shown on `/accounts/:id`.
- **Statement files plus the feed need a cutover** (`simplefin_cutover`, or `simplefin_balances_only: true`), refused
  at load; file rows on or after it are dropped only once the feed reports the account (`simplefin.csv_clip`).
- **Coverage comes from the data**: rates, baselines and averages start at the first month by which at least
  `coverage.min_share` (default 0.6) of the cash-flow accounts with any transaction have their first one; an account
  never un-starts. The ETL exports `cashflow_monthly.json` `months[].in_coverage` and `coverage_floor`, which
  `coverageMonths` (`app/src/lib/agg.ts`) applies. No fixed year or month in dashboard code (`categories.test.ts`).
- **Net worth has one builder** (`etl/networth.py`): every snapshot and the daily line read the one series map. It
  counts checking, savings, brokerage and loan accounts, credit cards when `household.yaml`
  `net_worth.include_credit_cards` (default true), and the manual assets. A row is an account's last balance by the
  date: an account that stopped reporting is carried (a feed account warns, `simplefin.stale.<id>`); one not yet
  observed is in the snapshot's `missing`, never guessed (the dashboard's history keeps comparable month ends only:
  `app/AGENTS.md`). A loan is stored negative whatever sign the feed uses; a debt is never liquid.
- **Manual assets** (`etl/manual_assets.py`): net worth counts the store document's live assets plus every
  `manual_entries` item whose id (`networth.manual_slug` of its name) no live app asset takes: an app asset of that id
  overrides the entry, removing it brings the entry back, and the app never hides one. One the household could not
  mean is left out with a `manual_assets.entry` warning. Before its first value an asset is carried back at it
  (`carried_back`), from its `acquired` day when it states one, so it is never `missing`.
- **Mortgage** is the `mortgage: true` account's own row, from the feed; a snapshot without it while it is configured
  subtracts nothing and guesses nothing (`networth.mortgage_included`; a hand-stated row does not count). `assumptions.mortgage` (null without such an
  account) reads that row (`balance` null, never 0, before the feed reports it; `schedule`) and the `loan:` terms, each
  null when absent: nothing estimates a rate or a payment. The home's value is the snapshot's House-class rows.
- **Brokerages** are one row at the feed's own `balance`, except a live `margin: true` account: its positions gross
  plus a `balance − holdings` remainder (Margin or Cash); `holdings_coverage` checks the two reconcile. Month ends hold
  it net, so the app compares like with like on their footing.
- **Vendor rules outrank keyword rules and the bank's label**, so an over-broad pattern silently overrides right
  answers. Anchor short or common-word patterns and pin a negative case (`tests/golden/starter_cases.yaml`). Failing
  safe to Uncategorized beats a confident wrong answer.
- **Overrides run after transfers**, so a row an override retypes to `transfer` never reaches `phase_c`'s
  unmatched-internal check: re-check that before reordering the enrich stages.
- **Transfers pair, or stay visible**: a rule is scoped by `accounts:` or `kinds:` and its `counter_hint` names where
  the other leg lives (nothing is inferred from an id's spelling); the generic matcher pairs only rows nothing
  explained, each the other's only opposite row in the window. A transfer is out of spending and income when both
  sides pair; a card's received payment and an `accounts:` rule's unpaired leg always are; any other unpaired side is
  an ordinary row. `docs/design/categorization-and-learned-rules.md`.
- **Ambiguity** → `needs_review` + a row in `open_questions.json`; never guessed. `questions` are actionable;
  `data_gaps` are unmatched internal legs a counterpart export omits (already out of spending).

## Conventions
- Transaction `type` ∈ purchase / income / transfer / cc-payment / investment / dividend / fee / interest. **Spending =
  purchase + fee; income = income + dividend + interest.** Transfers and cc-payments are in neither by construction.
- Per-person income is first-class in `earner` (an income rule's `earner:`, else an "X - Person" subcategory such as
  "Salary - Alex" when Person is a member) — a joint account's ownership doesn't identify the earner; `income_phases`
  is one phase per (earner, `employer:`). The dashboard's income streams are the subcategories present.
- Every row carries `category_source` (closed set `CATEGORY_SOURCES`, `etl/models.py`): provenance only, never a
  decision; a new value is an export shape change.
- **Household facts are config**, read through `Config` and taken as arguments, never literals. `household.yaml`: the
  name, people, joint label, surname, time zone (every day boundary, the ETL's and the dashboard's "today"), module
  seeds and net-worth options; `Config.owners` = adults, joint label, children; every `owner` is one of them and an
  `earner` a member, both checked at load. `accounts.yaml`: the accounts (`Config.asset_class_of`,
  `Config.mortgage_account_id()`, `Config.loan_terms(id)`) and the manual entries. `assumptions.yaml`: tunables.
- The exports also say what is configured (`manifest.setup`, for the first-run page), who the household is
  (`assumptions.household` `name`, `timezone`), the module seeds (`assumptions.modules`), which projection defaults are
  stated (`projections_stated`), the asset classes (`asset_classes`) and the manual assets (`accounts.json`
  `manual_assets` and `manual_overridden`; snapshot rows' `manual_id`, `carried_back`).
- Every balance carries an `as_of` date. **Rates with no data are `null`, never 0** (a margin APR the config does not
  state too); don't `?? 0` a rate before formatting.
- Type-set constants (`TXN_TYPES`, `SPEND_TYPES`, `ASSET_CLASSES`, `LIABILITY_CLASSES`, `ACCOUNT_KINDS`,
  `DEFAULT_CLASS_BY_KIND` and the rest) live in `etl/models.py` — import them, never re-declare. A kind or class
  outside the sets is refused at load.
- The names the engine files rows under (fallback, uncategorized, P2P-unknown, internal, income, its catch-all, fees)
  are `RESERVED` in `etl/models.py`: a taxonomy must hold them, no rename moves one; exported as
  `assumptions.reserved`. Use the constants, never a literal (the feed parser adds three brokerage subcategories).
- **A monthly bill is expected on its day of the month** (`recurring.monthly_rule`: a business-day end, a stable
  day, else the median gap); the forecast and the bills chart place each charge with one function
  (`app/src/lib/obligations.ts` `expectedCharges`). `docs/design/schedules.md`.
- Tests are synthetic, on the demo household (`tests/conftest.py`: the helpers, one ingest of `full`,
  `demo_exports`); one naming the repo's `config/` fails (`tests/test_no_real_config.py`), one writing its `data/`
  or `scenarios/` errors.
- SimpleFIN allows ≤24 requests/day and 90 days per fetch; the schedule (`household.yaml` `refresh`) and **Refresh
  now** (capped per household day) stay well under it; the snapshot store accumulates history.
- **Two locks, don't conflate them**: `data/.ingest.lock` (inside `pipeline.run()`, correctness — two ingests corrupt
  `finance.db` via the shared `finance.db.tmp`) and `state/engine/refresh.lock` (a `flock` re-exec in `refresh.sh`,
  quota). Both are `flock`, never `fcntl.lockf`.
- **`manifest.generated_at` is the ingest time, not freshness**; `manifest.simplefin_fetched_at` (UTC, `Z`) says how
  old the data is. `generated_at` carries the household zone's offset (compared with `answered_at` as an instant);
  its first 19 characters are the wall clock every display slice (the ingest day, the month in progress) reads.

## Design docs — read before touching
| Before changing… | Read |
|---|---|
| a config key, its default or its check | `docs/configuration.md` |
| a statement format, a preset, `sources:`, OFX, the `import` commands | `docs/design/importers.md` |
| rules, vendor patterns, overrides, learned rules, review answers (ETL) | `docs/design/categorization-and-learned-rules.md` |
| `split:` legs or any aggregate / count | `docs/design/splits.md` |
| recurring detection, schedules, the forecast | `docs/design/schedules.md` |
| `renames:` | `docs/design/renames.md` |
| exports, schema versions, the family store (`deploy/scenario_store.py`), manual assets | `docs/design/data-and-sync.md` |
| the refresh and the ingest without a fetch | `docs/design/information-architecture.md` |
| compose, `ff`, the sign-in, Caddy, the schedule, alerts, backup, upgrade | `docs/design/platform.md` |
| the images, the workflows (CI, release, the demo site), `tools/verify`, test fixtures and guards | `docs/design/deploy-and-gates.md` |
| the maintainer's post-publish and adoption scripts | `tools/maintainer/README.md` |
| anything under `app/` | `app/AGENTS.md` and its index |
| the AI step (`etl/ai/`, `ai_status.json`) | `docs/design/ai.md` |
| the full list | `docs/design/README.md` |
