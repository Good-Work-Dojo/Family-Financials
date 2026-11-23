# Images, CI gates and the repo's own guards

> Scope: the images, the unit gate (`tools/verify/ci.sh`) and the GitHub workflow, comparing exports
> (`deploy/export_diff.py`), and the tests that guard the repo itself. Describes the system as it is; verify against
> `tools/verify/`, `.github/workflows/ci.yml` and the tests named below. The files in `deploy/`: `deploy/README.md`;
> the harness in detail: `tools/verify/README.md`.

## What runs where

Both images are built from the repo with `.dockerignore` applied: `*`, then each path the Dockerfiles copy let back
in, so a household's `config/`, data, store and secrets, the git history and development-only material never enter a
build. The documents (the top-level Markdown files and `docs/` with its pictures) are read by tests — their links, the
key reference, the table of supported banks, the importers' example preset, the pictures' size — and only the `test`
stage holds them, with `.github/` (the workflows' tests read it); no runtime image holds a document. Both runtime images carry `LICENSE`,
`NOTICE`, `THIRD_PARTY_NOTICES.md` and `THIRD_PARTY_LICENSES` (the npm packages' licence texts the bundle leaves
out): the ETL image in `/opt/ff`, the web image in `/usr/share/doc/family-financials`, never under what Caddy serves.
The stages, the runtime images' contents and the base images' digest pins
(`deploy/BASES.md`): `docs/design/platform.md` ("The images"). The repository tracks no config (`.gitignore` has
`/config`, which a link named `config` matches too); an install's own `config/` is mounted read-only into the two
headless workers at `/ff/config` (`FF_HOME=/ff`). Every test reads the generated demo household (`tools/demo`) or
synthetic fixtures, so no gate needs a household's config.

`tools/verify/ci.sh [EXPORTS_DIR]` runs every gate in Docker, each container that runs a check or a test with no
network (`--network none`; only the image builds and the stack stage reach it), in order:

1. on the host: the argument checks (exit 2 before any build), the context files' byte budget and `docs/design/`
   pointers, `check_workflows.py` (jobs on hosted runners named by a literal, actions pinned to a commit),
   `python3 tests/test_scope.py` (every root the scope scanner reads is there), and the config check. It has
   two forms. In a git checkout, a tree that is itself the top of its work tree, `git ls-files -- config` must list
   nothing (exit 1 with the count otherwise; an untracked, ignored `config/` is a developer's own). In a tree that is
   no checkout, an exported copy wherever it is unpacked, everything ships, so `config` must not exist at all — no
   directory, file or link (exit 1); then actionlint (pinned by digest, offline) over every workflow, and shellcheck
   (pinned by digest, offline) over the maintainer scripts (`tools/maintainer/*.sh`, `tools/maintainer/README.md`);
2. the ETL image's `test` target (`deploy/Dockerfile.etl`, tagged the harness's ETL test image, `family-financials-etl:test` by default, `tools/verify/README.md`): first the image is asked, offline, where its ETL looks for a config with no
   variable set, and anything there is exit 1; then `ruff check etl tests deploy tools` and `pytest -q`, the ETL
   suite (`tests/`); then the harness's own tests (`tools/verify/test_*.py`) and, when the tree holds a maintainer's
   own `tools/private` folder (the published tree does not), its tests (mounted read-only and linted first by
   `ruff check` with the same rules, a finding failing the gate), each a pytest run of its own that runs nowhere else —
   not in `make test`. Without that folder its step is skipped with a line saying so; a folder without a `test_*.py`
   is exit 1. Without `EXPORTS_DIR`, `tools/verify/demo.sh` then generates and ingests the six demo
   households offline into a temporary directory (one container per profile, as the calling user, `--network none`;
   removed on every way out), each at the instant its generator names (`FF_NOW`, below), and prints `demo <profile>:
   N transactions, quality F fail / W warn`;
3. the web image's `web-build` stage (`deploy/Dockerfile.web`: `npm ci`, `tsc --noEmit`, `vite build`, tagged
   `family-financials-web:test` by default), then `npm run lint`,
   vitest and `npm run typecheck` in it. Without `EXPORTS_DIR` vitest runs on the demo household `full` (its exports
   and `truth.json` mounted read-only, `FF_EXPORTS_DIR`, `FF_TRUTH`, `FF_REQUIRE_EXPORTS=1` and `FF_PROFILE=full`
   set), so the smoke suite, the route matrix, the contract and the rollup check run with every other test; its JSON
   report goes through `suite_summary.py --no-skips`, which prints `vitest: P passed / F failed / S skipped` and fails
   the gate on any failed or skipped test. One more container per demo profile then runs `contract.test.ts` and
   `lib/agg.test.ts` on that profile's exports (mounted read-only, `FF_EXPORTS_DIR` and `FF_REQUIRE_EXPORTS=1` set, so
   a missing export fails instead of skipping), prints `contract <profile>: OK`, and, in the same container once those
   pass, runs the route matrix (`matrix.test.tsx`) with `FF_PROFILE=<profile>`: `suite_summary.py --matrix` prints
   `matrix <profile>: P passed (G known gaps) / F failed` and fails the gate on a failed or skipped test. Every test so
   far runs in UTC (`app/vite.config.ts` sets the test process's `TZ` from `FF_TEST_TZ` when vitest starts, UTC when
   it is unset); then the matrix runs twice more, with `src/test/demoClock.test.ts` (which fails unless the process
   runs in the zone asked for, so a run whose zone did not take cannot pass in UTC), `src/lib/clock.test.ts` (the
   household's day at fixed instants) and `src/lib/clockTimes.test.ts` (the household's clock times at fixed instants:
   `fmtDateTime`, the ages, the schedule sentence), neither of which may move with the process's zone, each in its own
   container with the same mounts and `-e FF_TEST_TZ=<zone>`: `full` in `America/New_York` and `tiny` in
   `Pacific/Kiritimati`, printed `matrix full (America/New_York): …` and `matrix tiny (Pacific/Kiritimati): …` (each 24
   tests more than a UTC line: the zone check, the clock's five and the clock times' 18), a failure naming the profile
   and the zone;
4. `images`, with exports or without: the two runtime images (the ETL image's default target `etl`, tagged
   `family-financials-etl:rt` by default, and the web image's `web`, `family-financials-web:rt`; the stack test runs
   them by those names in place of the published images `compose.yaml` names by default), built and
   checked as uid 12345, which owns no file in either, one `images:` line per check: the runtime ETL image holds no uv,
   git, make, curl, dev group, tests, harness tools, documents, config, `.git` or `.env`, and its `.pth` files are
   exactly the allowed two (the venv's and `ff.pth`: a `.pth` line runs on every interpreter start, so a dependency
   that ships a new one fails the gate until it is read and allowed in `ci.sh`); it validates and ingests
   `examples/minimal` (read-only, into a world-writable `data/`); Python imports the image's own `etl` from a directory
   holding another, with `HOME=/tmp`; supercronic runs; tzdata holds UTC, `America/New_York` and `Pacific/Kiritimati`,
   and in each `deploy/make_crontab.py` writes a crontab `supercronic -test` accepts; each image carries the tree's
   four legal files; the web image's `/srv/public` is `deploy/public` exactly (the sign-in page's source link is the
   build's `FF_SOURCE`, `deploy/source_link.sh`, the default here) and no source map ships; each image's OCI labels
   are the project's, not its base image's; the web image answers `/healthz` with its defaults and with a
   domain; Caddy validates and adapts the Caddyfile both ways and each JSON passes `tests/test_caddyfile.py`; the
   runtime dependencies' licences (`tools/verify/licences.py`: the npm closure and the Python distributions read
   inside the runtime ETL image, each on the allow-list, `THIRD_PARTY_NOTICES.md`'s generated block current, and
   `THIRD_PARTY_LICENSES` the npm packages' own licence files, then those of the Go standard library and of every Go
   module compiled into Caddy and supercronic, read from each binary's build info copied out of the runtime images
   with no network and kept in `deploy/licences/go/`, which `licences.py --write` maintains from the Go module proxy);
   last,
   the two images' sizes; then the secret scan: gitleaks, pinned by digest, offline, over the files git tracks or would
   and the history (`.gitleaks.toml` holds its allowances, each with its reason);
5. `stack`, without exports only and last: `tools/verify/stack.sh` brings the demo household's whole stack up from the
   runtime images through the product's own `ff` (`ff demo`, `ff up`), runs the security matrix and a signed-in browser
   run, and brings it all down; its line `stack: P/T`, and its failure or anything it leaves behind fails the gate
   (`tools/verify/README.md`, "The stack test"). The stage uses `docker compose` with a project of its own, or the
   compose wrapper the harness's settings name (`FF_HARNESS_COMPOSE`: a guard that may label and confine what compose
   starts).

Without `EXPORTS_DIR` the run's last two lines are the one a CI log is read for and `== … CI OK ==`:

```
gate: pytest P passed; vitest P passed / 0 failed / 0 skipped; contract 6/6; matrix 8/8; stack P/T
```

`pytest P passed` is the ETL suite's count (`tests/`), whose summary must count no skipped, xfailed or xpassed test
and no error (any of them fails the gate, naming the counts); the harness and private-tooling runs print their own
pytest summaries above it and may skip what their environment lacks. `contract` counts the demo households whose
contract passed and `matrix` the matrix runs that passed, six households and two zones; `stack` the matrix's cases
that passed and the browser run, of all of them (a case marked as a known product defect counts in the total only and
is listed on its own `known:` line). A run given `EXPORTS_DIR` prints no gate line.

The dashboard's data-gated tests find their exports through one helper, `app/src/test/exports.ts`: `FF_EXPORTS_DIR`
(absolute) when set, else the repository's `data/exports`. The smoke suite reads the names it needs from the
generator's `truth.json` (`app/src/test/truth.ts`: `FF_TRUTH`, else two directories above the exports) and runs with
`Date` pinned at the manifest's ingest instant (`app/src/test/demoClock.ts`); a plain `vitest run` without exports
skips it, and so does a run on a household's exports when no `truth.json` is found where `FF_TRUTH` points or, without
it, two directories above the exports (under `FF_REQUIRE_EXPORTS=1` either is a load error).
`tools/verify/narrow.sh vitest` takes the same `FF_EXPORTS_DIR` (and `FF_TRUTH`, and `FF_PROFILE`) for a demo
household's synthetic exports, and refuses a path at, inside or above the real-data scratch root.

The route matrix (`app/src/matrix.test.tsx`) reads the exports alone, no `truth.json`, so it holds for any household:
every route of `PAGES` (the account page with the first exported account, or the id `none`) is mounted twice with the
clock at the ingest instant, privacy off then on, and checked five ways (`app/src/test/matrixChecks.ts`): `renders`
(no error-boundary card, no failed load, settled within a bound), `values` (no `NaN`, `Invalid Date`, `undefined`,
`Infinity` or `∞`, `0000-` date or `9999` sentinel as a whole token in the text and the scanned attributes),
`private` (the privacy net's money scan, `app/src/test/moneyScan.ts`, finds nothing), `console` (no
`console.error`) and `cards` (every `capabilities.ts` row of the route with no `when` is on screen by its test id,
unless a stand-in on screen names it in its `insteadOf`; a page the app replaces, such as the unknown-account card,
has none to look for, and where the first-run page stands in, the route owes that page's cards in the state it shows
and shows none of its own; so does a route the module-off page stands in for, which owes its one card). After the
routes, one test that the household is in the first-run state its exports give (`lib/firstRun.ts`; for a demo
household, the one the test names: `empty` has nothing and is "setup", the others have data) and that every route
shows the first-run page where it stands in and its own page elsewhere, and one that the module-off page shows on
exactly the routes switched off for the household (`App.tsx` `closedRoutes` over the config's seed, the family store
being unreachable there) where the first-run page does not stand in: for a demo household the routes the test names
(`full` none; `no-investments` and `no-income` Second house; `new`, `tiny` and `empty` House as well, having no
mortgage). That is one block; the demo household `empty` runs three: its exports as they are, the same with two
accounts configured ("waiting for data"), and with the first-run state held off, so the pages' own no-data states stay
under the matrix. One test per route and check — 19 × 5 + 2 = 97 per block, plus 2 per run: 99 per household,
3 × 97 + 2 = 293 on `empty`, and 99 + 24 = 123 on a zone run (its zone, clock and clock-time tests); `ci.sh` prints
these counts. A combination a demo household does not pass yet is a row of
`app/src/test/matrixGaps.ts` (`FF_PROFILE` picks the household's rows; `household` has none, nor has `full`) with a
fragment of the failure message it expects, and runs as an expected failure: a page that starts passing, or fails
another way, fails its row until the row is removed or rewritten.

Each container runs the image ID its build printed, never the tag, except the stack stage's (compose names images,
so it runs the two runtime tags just built). With `EXPORTS_DIR` the dashboard run is eslint,
tsc and only `contract.test.ts`, `lib/agg.test.ts` and `matrix.test.tsx` on that directory, mounted read-only
(`FF_REQUIRE_EXPORTS=1`, `FF_PROFILE=household`; the smoke suite is written for `full` and does not run), under the
reporter rule: exports are treated as real, so the directory and `FF_SUITE_LOG` must resolve inside the scratch root,
vitest's output goes to that mode-600 log, and only counts, failing titles and failing file paths are printed
(`FF_DEMO_EXPORTS=1` lifts the rule for synthetic exports and lets `FF_PROFILE` name their demo household; it never
overrides the location: a directory at, inside or above a real-data scratch root, `$FF_SCRATCH` or the default root,
is refused, the path as spelt as well as the path it resolves to).

`.github/workflows/ci.yml` runs that same gate on a push to `main` and on every pull request, in two jobs that each check the repository out without leaving the token in the checkout. `gate`, on a
GitHub-hosted `ubuntu-24.04` runner, runs `tools/verify/ci.sh` with no argument, its output shown in full and the log
ending with the `gate:` line; its limit is 45 minutes (the gate takes about a quarter of an hour on a developer machine
with its images cached, and a two-vCPU hosted runner with no cache is estimated at 22 to 30). `arm64`, on GitHub's arm
runner (`ubuntu-24.04-arm`, 20 minutes, in parallel), runs `tools/verify/arm_smoke.sh`: both runtime images built
natively and checked, then `stack.sh --smoke` (`tools/verify/README.md`, "The arm64 job"). Nothing in either output is
private: the tree holds no household's config and every test reads the demo households.

`.github/workflows/release.yml` publishes a release: on a published release (or by hand, for a tag), `prepare` reads
the names, tags and notes (`tools/verify/release_names.py`; a tag of another form or no `CHANGELOG.md` section stops
it), `build-amd64` and `build-arm64` build both runtime images natively on `ubuntu-24.04` and `ubuntu-24.04-arm` and
push each by its digest alone, with the project's OCI labels and annotations and the release's `FF_VERSION` and
`FF_SOURCE`, and `merge` joins each pair into one multi-architecture image under the tags. Its only actions are
GitHub's own (`actions/*`, pinned by commit), so an organisation that allows no other still runs it: the builder
(BuildKit from `moby/buildkit`, pinned by digest and moved by hand), the sign-in (the token on standard input) and
the builds are docker's own commands, and `release_names.py labels` gives each build its labels. The images are
`ghcr.io/<the repository, lower-cased>-etl` and `-web`, so a fork's candidate never takes the public
names; a full release is tagged with its version, `<major>.<minor>` and `latest`, a prerelease with its version alone.
No job may write to the repository: the notes are printed in the run's summary for the maintainer to paste into the
release. `.github/workflows/pages.yml` builds the demo site and deploys it to GitHub Pages only while the repository
variable `FF_PAGES_ENABLED` is `true`. `.github/dependabot.yml` proposes weekly updates for the lockfiles, the
Dockerfiles' base images and the workflows' actions; the images the harness's own scripts name (gitleaks and
actionlint among them) are moved by hand. A bump of anything the images ship needs `licences.py --write` and, for a
base image, its `deploy/BASES.md` row (`tests/test_bases.py`) on the bot's branch before the gate passes
(`tools/maintainer/README.md`, "Dependabot pull requests"). The run summary also prints each image's index digest,
and `prepare` refuses a tag whose version is not `etl/install.py`'s `VERSION` ("Cutting a release" there).

## Comparing exports

`deploy/export_diff.py` (unit-tested in `tests/test_export_diff.py`; `tools/verify/export-diff.py` runs it in the ETL
image) compares a base and a head export directory under one policy. All but `identical` and `removals` also accept
additions (a new key or file):

- `identical` — nothing differs but `manifest.generated_at`.
- `schema-additive` — additions only.
- `vendors-fallback-only` — a vendor may move only on rows no vendor pattern named, and only to a re-cased spelling or
  a matching `renames:` target.
- `categories-only` — a row's category fields and tags may move only to a `learned` `category_source`; the aggregates
  built from categories may follow.
- `splits` — new rows must be parts of a head row flagged `is_parent`, summing to it in exact cents; other rows gain
  only `parent_id` / `is_parent`.
- `removals` — `identical` plus a `schema_version` rise plus exactly the removals each `--allow-removed SPEC` names (a
  file, a key path, or list rows whose field matches a glob).
- `refactor` — `schema-additive`, except that the quality report's `checks` compare as a multiset of `(name, status)`
  (the list's order and every key of a check other than `name` and `status` are ignored), plus exactly what each
  `--allow-removed SPEC` (as under `removals`) and `--allow-changed SPEC` names: a key path under which anything may
  change, list rows whose field matches a glob, or one transaction field on rows both sides have. Each allowance
  prints its count; `--allow-changed` is refused under every other policy.

`schema_version` may rise under every policy but `identical`, never fall; a duplicated transaction id is always a
violation; a boolean never equals a number (`false` → `0` is a change); the output is aggregate, never descriptors or
amounts. Exit 0 within policy, 1 outside it, 2 on a usage error or an unreadable or manifest-less dir, 3 when the
fetched-at stamps differ (the inputs changed between the ingests).

The practice: a change names the policy it claims. Ingest the same inputs before and after it into two scratch
folders and compare the exports under that policy; ingest the after-state again and compare with `identical`
(determinism); run `ci.sh <exports>` so the contract, agg and matrix tests read the new exports. A change outside every
policy is gated by an exit-1 diff whose violations match a list predicted with the change, never by loosening a
policy. Policy per change and the spec grammar: `tools/verify/README.md`.

A household's own config never enters the tree or an image, so an ingest of its inputs reads that config from a
directory outside the tree, mounted read-only, and `tools/verify/export-diff.py --config-dir DIR` mounts the same
directory read-only to hand its `vendor_rules.yaml` to `vendors-fallback-only`, the one policy that reads one.

## The pinned clock

`FF_NOW` is an environment variable that fixes the time the ETL stamps its files with: `manifest.json`
`generated_at` and `learned_rules.json` `updated_at` both read it through `etl/clock.py`, the only module of the
offline pipeline that reads the wall clock. An ordinary install never sets it (and an empty or blank value counts as
unset) and gets the real clock. It exists for
reproducible ingests of generated data: the same inputs and the same `FF_NOW` give byte-identical exports, manifest
included. The value is an ISO-8601 date and time (`2026-10-31T18:00:00`); without an offset it is read in the
household's time zone, with one it is that instant. Anything else makes `python -m etl ingest` print one line and
exit 2 before it writes anything. `tests/test_clock.py` pins this, that nothing else in `etl/` reads the clock, and
that nothing in the demo generator (`tools/demo`) reads one at all: it takes every date from its `--end`.
The demo generator prints the instant to ingest at (an hour after the end day's last feed snapshot), which
`tools/verify/demo.sh` and the tests' `ingest_demo` (`tests/conftest.py`, `DEMO_NOW`) pass as `FF_NOW`; the
dashboard's suites and the screenshots then freeze `Date` at the `generated_at` it stamps.

## The repo's own guards (in the ETL suite, so in every pytest run)

- `tests/test_comment_hygiene.py`: non-test source under `etl/`, `deploy/`, `starter/`, `examples/`, `app/src/`,
  `tools/demo/` and `tools/verify/`, and the dashboard's test titles, state what the code does in the present tense —
  no phase, task, decision or review ids, no "fix round" narration, the two schema-version changelogs included. A
  comment that needs a reason gives the reason. Docs are not scanned, but design docs follow the same
  rule.
- `tests/test_page_copy.py`: no string a page prints names an export file, a browser-storage key or a `make` or
  `python -m` command, and a config file it names sits in the `Code` pill (`components/NoData.tsx`), the hint after the
  plain sentence; no ETL check's `detail` names a command, a `.json` file or `config/`.
- `tests/test_docs_size.py`: `AGENTS.md` + `app/AGENTS.md` ≤ 40,000 bytes together. An agent that truncates its
  instruction chain at a byte limit drops what lies past it, so a longer file loses its END silently — keep the hard
  rules at the top and move detail here. `tools/verify/ci.sh` also checks, on the host before any build, that every
  `docs/design/` pointer in the two files resolves.
- `tests/test_documents.py`: every relative link and picture of the README, the policy files, `docs/` and the folder
  READMEs names a file of the tree, every `#anchor` of the README one of its headings, every `./ff` command the README
  shows is one `ff` takes, the README fetches `ff` and the example answers file at this release's tag, the heading the balances page quotes is
  the README's, and the first-run page's documents exist.
- `tests/test_scope.py`: the scope scanner, `scan(root, banned, allowed)`, which lists every line of the code, tests,
  starter and example files, `pyproject.toml`, the context files or `docs/design/` on which a token of a given list
  appears; the list is its caller's (a maintainer's own list of identifiers a tree must not carry). A token
  matches case-insensitively between non-alphanumeric characters; `allowed` exempts a token only in named paths. It
  never opens a `.env` file or a credentials folder, and its tests hold that every root it scans exists. Stdlib only,
  so `ci.sh` also runs it on the host before any build.
- `tests/test_isolation.py` and the session guard `_repo_state_untouched` (`tests/conftest.py`): a test that writes
  into the repo's `data/` or `scenarios/` errors, naming the path. Build pipelines through `isolate` /
  `isolated_pipeline` (which builds on the demo config unless given one) and config variants through `demo_copy`.
- `tests/test_no_real_config.py`: no test names the repository's `config/` (a helper that reads it, `ROOT / "config"`,
  `config/*.yaml` path literals); the one file that must (a synthetic sample) is listed in the test. The tree holds no
  `config/` at all: `tests/test_paths.py` points the default place at a temporary directory to show that a missing
  config directory is one line naming where it was looked for and the variables to set.
- `tests/test_e2e.py`, the determinism pin (two full ingests, identical hashes, plus invariants), runs on the
  generated demo household `full` with every count taken from its `truth.json`, skips nothing, and writes only to
  `$FF_E2E_DIR` or a temp dir, refusing a live `data/` or `scenarios/`.
- A test reading another side's constant keeps the sides in step: the answers cap in `deploy/scenario_store.py` and
  `app/src/lib/answers.ts`, and the live-balance source set in `app/src/lib/holdings.ts` against the pipeline's
  label (`tests/test_exports.py`).

Tests use synthetic data only. Python (`tests/conftest.py`): `make_txn`, `answer`, `answers_doc`, `write_household`;
the demo household's config (`full`, written once per run outside the repository) through `load_demo` (one parsed
file), `demo_copy` (a copy to edit) and `demo_config`; `isolate` and `isolated_pipeline`; the session fixtures
`demo_home` (the generated tree and its truth) and `demo_exports` (one ingest of a copy, `ingest_demo`), which
`tests/demo_checks.py` compares with the truth section by section. App: the closure factories in
`app/src/lib/__fixtures__.ts` (each test file pins its defaults in one line, so a new `Transaction` field is added in
one place), `app/src/test/routerProbe.tsx` (`Where`, `where()`, `whereSearch()`) for location assertions, and for the
suites on exports `app/src/test/exports.ts`, `truth.ts`, `demoClock.ts`, `moneyScan.ts`, `matrixChecks.ts` and
`matrixGaps.ts`, which nothing in the application imports. A test under `tests/` never imports anything
`.dockerignore` keeps out of the image.
