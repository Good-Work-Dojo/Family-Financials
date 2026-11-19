# tools/verify — the verification harness

Host-side scripts that gate a change before it merges, needing only bash, python3, docker and jq on the machine (every
Python and Node toolchain runs inside the images):
the unit gates, an export diff under a declared policy, and screenshots. The exports they read come from offline
ingests into scratch folders under the scratch root (below). The one piece of logic that must be tested lives in
`deploy/export_diff.py` (shipped in the ETL image, linted, unit-tested) — scripts here only orchestrate docker. How the
gates fit together: `docs/design/deploy-and-gates.md`.

**The harness's names.** The images the scripts build and run, the label on everything they start, the stack's
compose project and the default scratch root come from seven settings (`harness_names.py` has the table and the
shapes; `harness.sh` gives the shell scripts the same, and `test_harness_names.py` holds the two together):

| Setting | Default | What |
|---|---|---|
| `FF_HARNESS_IMAGE_PREFIX` | `family-financials` | the images `<prefix>-etl` and `<prefix>-web` |
| `FF_HARNESS_TEST_TAG` | `test` | the test images' tag (the ETL image's `test` target, the web image's `web-build` stage); the runtime images' is `rt` |
| `FF_HARNESS_LABEL` | `family-financials.test=1` | the label (`key=value`) on every image build, container and network the harness's own `docker` calls make; with no compose wrapper the stack's compose objects carry only compose's project label |
| `FF_HARNESS_PROJECT_PREFIX` | `family-financials-test` | the stack's compose project (`<prefix>-stack`) and the names of the containers and networks the harness starts itself |
| `FF_HARNESS_SCRATCH` | `ff-verify` | the default scratch root: this directory in the account's home directory |
| `FF_HARNESS_COMPOSE` | (empty: `docker compose`) | a program the stack test runs compose through (below) |
| `FF_HARNESS_COMPOSE_PORT_VAR` | (empty) | the variable that program reads the stack's port from |

A maintainer may keep the settings for a machine in a file of their own, `tools/private/harness.env` (plain
`KEY=value` lines; the published tree holds no `tools/private`). When the tree holds it, it sets them all and the
environment is not read: a setting it leaves out or leaves empty is exit 2, so a lost line never falls back to a
default label or scratch root. Without the file each setting comes from the environment, else its default. A value
of another shape is exit 2.
Below, `<prefix>-etl:<test tag>` is the ETL test image, and so on.

**Exports are treated as real.** `~/ff-verify` below is the scratch root: `$FF_SCRATCH`, default the
`FF_HARNESS_SCRATCH` directory in the account's home directory taken from the account database, never `$HOME`. `FF_SCRATCH` must be absolute and is
refused (exit 2) when it resolves to `/`, the home directory or a directory above it. Every exports directory these tools are
given must resolve (symlinks followed) strictly inside it, so the household's own data directory is refused, and
nothing that could quote a row, an amount or a page reaches the terminal. `FF_DEMO_EXPORTS=1` lifts these rules; it is
for synthetic exports only, and it never overrides the location: `ci.sh` and `screenshots.sh` refuse (exit 2) an exports
directory at or inside a real-data scratch root — `$FF_SCRATCH`, or the default root whatever `FF_SCRATCH` says —
whether the path as resolved or as spelt (symlinks not followed) lies there, as `narrow.sh` does; `ci.sh` and
`narrow.sh` also refuse a directory that holds such a root, and `screenshots.sh`, which copies the top-level JSON files
with their links followed, refuses one whose JSON file resolves into a root. Every `docker build` here carries
the harness's label, and the containers that follow
run the image ID it printed (or, without a build, the one the tag pointed at when the run began), so two runs at once
cannot test each other's image.

| Script | Does |
|---|---|
| `ci.sh [EXPORTS_DIR]` | First, on the host: `AGENTS.md` + `app/AGENTS.md` within 40,000 bytes and every `docs/design/` pointer in them resolving; `check_workflows.py`; then `python3 tests/test_scope.py`, the scope scanner's own check before any build (the image's pytest run repeats it), which prints how many files it reads and fails the run when a root it scans is missing; then the config check, in one of two forms. In a git checkout (the tree is itself the top of its work tree, compared as resolved paths) `git ls-files -- config` must list nothing (a household's config is never committed; a developer's own untracked, git-ignored `config/` is fine), else exit 1 with the count. In a tree that is no checkout (an exported copy, wherever it is unpacked, inside another repository's work tree too) everything ships, so `config` must not exist at all, as a directory, a file or a link, else exit 1. Then actionlint over every workflow (with shellcheck over their scripts), from its image pinned by digest, `--network none`, a finding exit 1 (`workflows:` line); then shellcheck over the maintainer scripts (`tools/maintainer/*.sh`), from its own image pinned by digest, the same way (`scripts:` line). Then the unit gates in the test images, every container that runs a check or a test started with `--network none` (only the image builds and the stack stage reach the network): build `<prefix>-etl:<test tag>` (the ETL image's `test` target, which alone holds the tests and the documents and pictures they read), and ask it where its ETL looks for a config when no variable names one: anything there is exit 1 (`.dockerignore` keeps `config/` out; compose mounts the household's at run time); `ruff` (over `etl tests deploy tools`) + `pytest` (the ETL suite, `tests/`); the harness's own tests (`tools/verify`) and, when the tree holds a maintainer's own `tools/private` folder, its tests (mounted read-only, linted first by `ruff check` with the same rules, a finding exit 1) as separate steps, each printing its own pytest summary — they run nowhere else, not in `make test`. Without that folder its step is skipped with a line saying so; one that holds no `test_*.py` is exit 1. Then build `<prefix>-web:<test tag>` (the web image's `web-build` stage), `eslint` + `vitest` + `tsc`. Then, with exports or without, the `images` stage: the runtime images `<prefix>-etl:rt` (target `etl`) and `<prefix>-web:rt` (target `web`) built and checked as uid 12345, one `images:` line per check (nothing but the runtime in the ETL image, its `.pth` files exactly the allowed two, `examples/minimal` validated and ingested, supercronic and the three zones' crontabs, `LICENSE`, `NOTICE`, `THIRD_PARTY_LICENSES` and `THIRD_PARTY_NOTICES.md` in the ETL image's `/opt/ff` and the web image's `/usr/share/doc/family-financials`, each the tree's, `/srv/public` exactly `deploy/public` (the sign-in page's source link the default `FF_SOURCE`; `deploy/source_link.sh` points it at a fork's), each image's OCI labels the project's rather than its base image's, `/healthz` with an address and a domain, the Caddyfile adapted and checked by `tests/test_caddyfile.py`, the sizes; `docs/design/deploy-and-gates.md`). Without `EXPORTS_DIR` vitest runs on the demo household `full` (exports and `truth.json` read-only, `FF_EXPORTS_DIR` / `FF_TRUTH` / `FF_REQUIRE_EXPORTS=1` / `FF_PROFILE=full`), so every dashboard test runs, and `suite_summary.py --no-skips` prints `vitest: P passed / F failed / S skipped` and fails the gate on any failed or skipped test; the contract and the route matrix then run on each of the six demo households, and the matrix twice more in two other zones (the `demo.sh` row), and the run's last two lines are `gate: pytest P passed; vitest P passed / 0 failed / 0 skipped; contract N/6; matrix M/8; stack S/T` (the stack stage, `stack.sh`, runs last, after the runtime images are checked; S/T its own count, a failure of it fails the gate; pytest's P is the ETL suite's count, and a summary of it that counts a skipped, xfailed or xpassed test or an error fails the gate with those counts; the harness and private-tooling runs print their own lines above it and may skip what their environment lacks; N counts the households whose contract passed, M the matrix runs that passed) and `== … CI OK ==`. With `EXPORTS_DIR` mounted the dashboard run is eslint, tsc and only `src/data/contract.test.ts src/lib/agg.test.ts src/matrix.test.tsx` (`FF_REQUIRE_EXPORTS=1`, `FF_PROFILE=household`; the smoke suite is written for `full` and does not run there), under the reporter rule: `FF_SUITE_LOG` is required, an absolute path resolving strictly inside the scratch root in an existing directory, so the log never lands in the worktree (exit 2 otherwise, before anything is built); vitest writes its JSON report to a private temp dir and its output to the mode-600 `FF_SUITE_LOG`, and only `suite_summary.py`'s counts, failing test titles and the paths of files that failed outside any test are printed. `FF_REAL_EXPORTS=1` is accepted and means the same; with `FF_DEMO_EXPORTS=1` (refused for a directory at, inside or above a real-data scratch root) vitest prints as usual and `FF_PROFILE` (a plain name, refused with real exports) names the demo household whose known gaps the matrix reads. A run given `EXPORTS_DIR`, real or synthetic, prints no `gate:` line. |
| `licences.py [--image IMAGE] [--web-image IMAGE] [--web-rt-image IMAGE] [--label KEY=VALUE] [--write]` | The runtime dependencies' licences, a stage of `ci.sh` after the runtime images' checks: the dashboard's npm closure (every package of `app/package-lock.json` not marked dev) and the Python distributions installed in the runtime ETL image (read inside it with `--network none` as uid 12345, this file mounted read-only), each under a licence on the allow-list (MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, PSF-2.0, MPL-2.0, Unlicense, CC0-1.0, and AND / OR compounds of them); GPL, AGPL, LGPL, SSPL, any other licence or one it cannot read fails, the package named (`licences: <npm\|python> <name> <version>: …`). It also holds `THIRD_PARTY_NOTICES.md`'s generated block to those closures, and `THIRD_PARTY_LICENSES` (which both runtime images carry) to the npm packages' own licence files, read in the web image's build stage (`--web-image`, default `<prefix>-web:<test tag>`) with `--network none` as uid 12345: a runtime npm package with no licence file fails. After the npm texts, `THIRD_PARTY_LICENSES` holds those of the Go standard library and of every Go module compiled into Caddy (`--web-rt-image`, default `<prefix>-web:rt`) and supercronic (the runtime ETL image): each binary is copied out with `--network none` as uid 12345 and its embedded build info read in Python, and every module it names must have its licence files kept in `deploy/licences/go/<module>@<version>/` (one with none fails, named with its program; a kept folder no program needs is stale), so the check needs no network. `--write` rewrites the block and the file, and fetches the missing modules' licence files (from the Go module proxy, by byte range, and the standard library's from the Go repository) and drops the folders no program needs: the one step that reaches the network. The base images' OS layers are out of its scope. `test_licences.py` proves a GPL and an unknown entry fail, and a Go module with no kept text fails. |
| `spdx.py [--write] [ROOT]` | Lists each shippable source without the licence header (`SPDX-License-Identifier: AGPL-3.0-only`, `Additional terms: see NOTICE`, in the file's comment syntax: after a shebang, a Python encoding line, a Dockerfile's parser directives or `<!doctype html>`); `--write` adds it. `tests/test_spdx.py` fails the suite on a source without it. Data files (JSON, CSV, OFX, images, lockfiles, Markdown, the YAML fixtures under `tests/`) carry none. |
| `release_names.py names REPOSITORY TAG [--prerelease]` / `notes CHANGELOG TAG` | What `.github/workflows/release.yml` publishes: the images `ghcr.io/<repository lower-cased>-etl` and `-web` (a fork's repository name gives its own names), the version (the tag without its `v`) and the tags (the version, plus `<major>.<minor>` and `latest` for a full release; a prerelease, flagged or with a pre-release part, gets its version alone); `notes` prints `CHANGELOG.md`'s section for the version, a prerelease's falling back to the version it leads to. `labels IMAGE SOURCE VERSION REVISION CREATED` prints the `docker buildx build` arguments, one a line, that give the image the project's OCI labels and its manifest the same annotations (the revision the tag's commit, `created` its commit time in UTC); `digest METADATA` prints the digest a build's `--metadata-file` records. A tag, repository, changelog, label argument or metadata file of another form is exit 2. `test_release_names.py` pins them all and the workflows' shape: GitHub's own actions alone, each pinned by commit, a BuildKit pinned by digest, the registry token on standard input. |
| The secret scan (in `ci.sh`) | After the licences, with exports or without: gitleaks from its image pinned by digest (`ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:…`, pulled by that digest), run as the caller with `--network none`, over the files git tracks or would (copied into the run's temp dir) and then, when `.git` is a directory, the history; in a tree that is no checkout, over the tree. Rules: `.gitleaks.toml` (gitleaks's defaults plus each allowance with its reason). `secrets:` lines; a finding, printed redacted, fails the gate. |
| `demo.sh <out-dir> [--end YYYY-MM-DD] [<profile>...]` | Generates the synthetic demo households (default all six: `full`, `new`, `tiny`, `empty`, `no-investments`, `no-income`; end `2026-10-31`) and ingests each, offline, into `<out-dir>/<profile>` (created if absent, refused when not empty, exit 2). One container per profile in `<prefix>-etl:<test tag>` (or the image id in `FF_ETL_IMAGE`; a missing image is exit 2): `--network none`, run as the caller's uid with `HOME` and the uv cache under `/tmp`, so any uid works. Whose code runs: run by hand (no `FF_ETL_IMAGE`) the image may be a build behind the tree, so the tree's `etl/`, `tools/demo/` and `starter/` (all that generate and ingest import or read from it) are mounted read-only over the image's copies, the image's virtualenv kept; the tree is the one holding the script (its real path), which must hold `pyproject.toml` and the three, else exit 2. With `FF_ETL_IMAGE` (`ci.sh` passes the image it has just built from the tree) nothing is mounted. The first line says which: `demo: code from the worktree` or `demo: code from the image`. Prints `demo <profile>: N transactions, quality F fail / W warn`; exit 1 naming the profile when a step fails, a check fails or no manifest is left. `ci.sh` without an argument runs it into a temporary directory, runs the main vitest on `full`, then, in one container per profile with its exports read-only and `FF_EXPORTS_DIR` / `FF_REQUIRE_EXPORTS=1` / `FF_PROFILE=<profile>`, runs `contract.test.ts` and `lib/agg.test.ts` (`contract <profile>: OK`) and, once they pass, the route matrix `matrix.test.tsx` (`suite_summary.py --matrix` prints `matrix <profile>: P passed (G known gaps) / F failed`, G its expected failures, the profile's rows of `app/src/test/matrixGaps.ts`; P is 99 on a household with data and 293 on `empty`, whose matrix runs three blocks — set up, waiting for data, the first-run state held off — each route checked five ways, `docs/design/deploy-and-gates.md`; a failed or skipped test fails the gate). Those six run in UTC; then the matrix runs twice more, with `src/test/demoClock.test.ts` (which fails unless the process runs in the zone asked for, so a run whose zone did not take cannot pass in UTC), `src/lib/clock.test.ts` (the household's day at fixed instants) and `src/lib/clockTimes.test.ts` (the household's clock times at fixed instants: `fmtDateTime`, the ages, the schedule sentence), neither of which may move with the process's zone, each in its own container with the same mounts and `-e FF_TEST_TZ=<zone>` (`app/vite.config.ts` sets the test process's `TZ` from it when vitest starts, UTC when it is unset): `full` in `America/New_York` and `tiny` in `Pacific/Kiritimati`, printed `matrix full (America/New_York): …` and `matrix tiny (Pacific/Kiritimati): …` (each counting 24 tests more than a UTC line, 123 on these two households: the zone check, the clock's five and the clock times' 18); a failure in either fails the gate naming the profile and the zone, and the `gate:` line's last field counts all eight runs (`matrix 8/8`). Read the exports of a kept directory with `FF_DEMO_EXPORTS=1`. |
| `demo_site.sh <out-dir> [--exports DIR]` | The demo site that `.github/workflows/pages.yml` publishes: the dashboard built with `VITE_DEMO=1` (`app/src/lib/demo.ts`: the family's documents kept in the browser, no refresh, sign-in, sign-out or AI switch, no build poll, the clock standing at the exports' generation time, a banner) over the invented household `full`. Without `--exports` it runs `demo.sh <out-dir>/demo full` first; exports given at, inside or above a real-data scratch root, as given or as resolved, or beside a family documents' folder that resolves into one, are exit 2 before anything runs (`harness.sh`'s `harness_refuse_real`, which `ci.sh` and `narrow.sh` use too). `demo_site.py stage` fills `<out-dir>/public` with the exports, the household's family documents under `family/` (the store's empty document for any it lacks), the icons and a manifest whose addresses are relative; the build runs in `<prefix>-web:<test tag>` (or the image in `FF_WEB_IMAGE`; its own code is built) with `--network none` and that directory as its public directory (`FF_DEMO_PUBLIC`), into `<out-dir>/site`; `demo_site.py check` then holds the site to what a static demo must be (`demo site: N files; …`, a problem a line each and exit 1): only relative `href`/`src` (but a link to the program's source), no server path (`/api`, `/auth`, `/login`) or `version.json`, the Caddyfile's policy less `frame-ancestors` in a `<meta>` ahead of every script, the banner's words, every export, family document and icon, and no file beyond what a demo site publishes (the page, `theme.js`, the manifest, `assets/*.js` and `*.css`, the icons, the four family documents and the exports its `manifest.json` lists; never a source map or a link). `VITE_SOURCE_URL` names the source the About card links to. `ci.sh` runs it after the matrix runs, over the `full` it generated. |
| `narrow.sh pytest\|vitest <paths…>` | One focused test of the worktree's code in the existing `<prefix>-etl:<test tag>` / `<prefix>-web:<test tag>` images, with `--network none` and, as `--mount … readonly` binds, the worktree (pytest, at `/w`, with `PYTHONPATH=/w` so `etl` and the `deploy` / `tools` helpers resolve there before the image's installed copy) or `app/src` (vitest, mounted over the image's copy) read-only; the repository root is taken from the script's real path (links resolved) and must hold `pyproject.toml` and this very script, else exit 2. pytest paths are relative to the repository root, vitest paths (and options such as `-t <title>`) to `app/`. With `FF_EXPORTS_DIR` (an absolute host path to a demo household's `data/exports`; `FF_TRUTH`, default `truth.json` two directories above it) the vitest container also gets those read-only at `/exports` and `/truth.json` with `FF_EXPORTS_DIR=/exports FF_TRUTH=/truth.json FF_REQUIRE_EXPORTS=1`, and `FF_PROFILE` (a plain name, else exit 2) passes through to the vitest container as is (the matrix's household: `FF_PROFILE=tiny FF_EXPORTS_DIR=<demo>/tiny/data/exports tools/verify/narrow.sh vitest src/matrix.test.tsx`), and so does `FF_TEST_TZ` when set (a zone name such as `America/New_York`, else exit 2: the zone the test process runs in, which `app/vite.config.ts` reads, UTC when unset); synthetic exports only: a path at, inside or above the real-data scratch root (resolved as `ci.sh` does), or at, inside or above the default root (the `FF_HARNESS_SCRATCH` directory in the passwd home) whatever `FF_SCRATCH` says, is exit 2. Dependencies, the vitest config and `package.json` come from the image, so after changing those run `ci.sh` first. Exit 2 with usage on an unknown runner or no paths, and when an image is missing. |
| `export-diff.py <base> <head> --expect <policy> [--allow-removed SPEC]... [--allow-changed SPEC]... [--config-dir DIR]` | Runs `deploy/export_diff.py` inside `<prefix>-etl:<test tag>` with both dirs mounted read-only, and with `--config-dir DIR` (an existing directory, no comma in its path, given once) that config mounted read-only too, its `vendor_rules.yaml` passed as the module's vendor rules; without the flag no rules file is passed, and `vendors-fallback-only`, the one policy that reads one, is refused (exit 2) naming the flag; `<base>`/`<head>` are bare labels (resolved under the scratch root) or paths, each resolving strictly inside the scratch root. Policies below. Exit 0 ok · 1 outside policy · 2 usage (a bad spec, `--allow-removed` under any policy but `removals` and `refactor`, `--allow-changed` under any but `refactor`) or a refused dir · 3 `simplefin_fetched_at` differs or is missing, or another `*_fetched_at` stamp differs or vanished without an `--allow-removed` allowance (a refresh landed between the two ingests — re-run base). Prints aggregates only; row output is refused unless `FF_DEMO_EXPORTS=1`. A transaction id that occurs twice on either side is a violation under every policy, and under every policy a boolean never equals a number (`false` → `0` is a change; `1` → `1.0` is not). |
| `screenshots.sh <label> <exports-dir>` | Shoots every `PAGES` route at 390×844 and 1280×900, dark and light, plus the phone's open navigation drawer in each theme, privacy-masked, with the store's documents served as an idle stack's store answers them; on real exports only its exit status and `overflow.json` are read, never the PNGs; synthetic exports (`FF_DEMO_EXPORTS=1`) may be shot unmasked into an `FF_SHOT_OUT` that is empty or does not exist yet (it is then created), outside the repository and the scratch root, with a demo household's own answers and manual assets (`FF_SHOT_STATE`) (see "Screenshots" below). |
| `FF_SHOT_NOTHING_BUILT=1 screenshots.sh <label>` | Shoots the page a household sees before any export exists (the first-run page in its "nothing built" state, which the loader shows when `manifest.json` is absent): the built dashboard served with the store's documents and no export, the one page at 390×844 and 1280×900, dark and light (4 PNGs, `nothing_built__<WxH>__<theme>.png`) + `overflow.json`, each shot once the page's heading is on screen. Takes the label alone and reads no exports directory (see "Screenshots" below). |
| `stack.sh <scratch dir>` | The stack test, `ci.sh`'s last stage (without exports): the demo household's whole stack from the runtime images, brought up by the product's own `ff`, attacked by the security matrix and driven by a signed-in browser, then brought down (see "The stack test" below). Prints each `known:` case and `stack: P/T` last; exit 0 only when nothing failed and nothing was left behind. |

## The stack test

`tools/verify/stack.sh <scratch dir>` (an absolute path with no whitespace, absent or empty; `ci.sh` gives it a
directory of its run's temp dir) builds nothing: it runs `<prefix>-etl:rt` and `<prefix>-web:rt` (`FF_IMAGE_ETL` /
`FF_IMAGE_WEB` name others), which `ci.sh` has just built.

- **Up**: `ff` copied into `<scratch>/install`, `./ff demo` (the generator takes no seed: the household is always the
  same, its passcode random), the printed passcode moved from the output into a 0600 file (the log says only `stack:
  passcode captured`), `.env` given the stack's port and `FF_HOSTNAMES=dash.example.net`, then `./ff up` (preflight,
  then every service healthy). `FF_COMPOSE` is `docker compose -p <project prefix>-stack` on the install, the port
  `FF_STACK_PORT` (default 18216; `FF_PORT_TLS` the next port) on the loopback address; or, when `FF_HARNESS_COMPOSE`
  names a wrapper (a guard that may label and confine what compose starts), `<wrapper> stack`, run with
  `FF_COMPOSE_FILE` naming the compose file and the port in `FF_STACK_PORT` and in the variable
  `FF_HARNESS_COMPOSE_PORT_VAR` names (a value already set there is the port). A `docker` in `<scratch>/bin` puts the
  harness's label on every `docker run` `ff` makes.
- **The AI step, on** (`stack_ai.py on`, the host's python3), before the browser: the demo's truth for the AI step
  from the generator in the ETL image at the install's end day; every export the manifest lists but itself and
  `ai_status.json` kept as the AI-off export; the scheduler stopped and the refresher recreated from
  `compose.ai-test.yaml`, a copy of the install's `compose.yaml` beside it whose refresher alone holds the test seam
  beside the workers' whole environment (a wrapper may take one compose file and want its bind mounts under that
  file's directory, so the copy stands in for an override); only once `docker inspect` shows the running refresher holding
  the seam, an invented key in `secrets/ai/api_key` (0600); the provider's
  stand-in (`tests/fake_llm.py`, a labelled container sharing the refresher's network namespace, so the refresher's own
  127.0.0.1) answering every vendor with one category; switched on through the store as the Settings card does it (the
  prefs' `ai`, then `POST /api/refresh {"mode": "ai"}`), the AI run and its ingest awaited; then the vendors truth says
  pass are the rows filed by AI, the stand-in heard once, with the key, about exactly them and nothing on the
  never-send list, and `ai_status.json` says on with the counts. Nothing leaves the machine; no real provider is asked.
- **The browser** (`stack_browser.py`): the screenshot harness's pinned image (`playwright/python:v${FF_PW_VERSION}`,
  `--pull never`, the package pip-installed at the same version), in web's network namespace as the screenshot harness
  reaches its server, so the stack is `http://127.0.0.1:8080` to it. A page load without a session goes to `/login`; an
  inline script planted there must be reported both as a policy violation and as a console error (the run's control);
  the passcode is typed and the form submitted; every route of the screenshot driver's list is visited (`/accounts/:id`
  with the first account that has transactions); Home's first chart is hovered; the transactions' CSV is exported (the
  download must start, and no text cell of its first row may begin like a formula); the register filtered to the rows
  filed by AI shows each with the AI chip, and the sidebar says the vendor names go to the provider by name; the theme
  goes light and back; Sign
  out on Settings must land on `/login` with the exports answering 401. Any `securitypolicyviolation` event (an init
  script reports each one), console error, page error, request to another origin, or answer of 400 or more while signed
  in fails it. Last line: `stack browser: N routes, 0 violations`.
- **The AI step, off** (`stack_ai.py off`): switched off and its answers forgotten as the card's Turn off and Forget do
  (the prefs' `ai` with `enabled: false`, then `ingest` and `ai_reset`); after the ingest every export compared is the
  AI-off export byte for byte and `ai_status.json` says off with nothing stored; the stand-in, the key and the copy
  removed, the refresher recreated without the seam and the scheduler started. One line per step, then `stack ai on|off:
  P/T`; a step after a failed one is not run (and fails), the restore always is.
- **The matrix** (`stack_matrix.py`, the host's python3, stdlib only; every request written byte for byte on a raw
  socket): the sign-in (cookies `HttpOnly; SameSite=Lax`, never `Secure` over plain HTTP whatever a client's `X-FF-*`
  says; the Origin rule by the real Host and scheme), every spelling of a protected path without a session (302 to
  `/login` for a page load or 401, never its content) and the public set (200, nothing protected in it), the store's
  `/auth/` paths (404), unknown Hosts (421, nothing proxied) and served ones, the signed-in answers and refusals (403,
  413, 405, 404), after sign-out and with the old static cookie (401; an export's ETag no help), request smuggling (a
  GET whose body is a whole request, then a second request on the connection: the answers and the store's own log),
  the throttle (six wrong passcodes with rotating `X-Forwarded-For` / `X-FF-Client`: five 401, then 429 with
  `Retry-After`), the store stopped (Caddy's 502 still carries the headers) and started again (a session survives), a
  flood of 100 parallel wrong passcodes while a browser with a device cookie signs in (204 within 2 s), the store
  publishing no port, Caddy alone behind a trusted proxy (on a labelled network `<project prefix>-stack-<pid>` of its own with a
  stand-in upstream that echoes the `X-FF-*` headers: `X-Forwarded-For` read right to left, the scheme from an exact
  `X-Forwarded-Proto`, neither from an untrusted peer), and last `./ff passcode remove demo` ending the sessions. Every
  answer must carry the CSP, `X-Frame-Options: DENY`, `nosniff` and `Referrer-Policy`, and no `Server`. One line per case
  (`ok <name>`, `FAIL <name>: <what>`), then `stack matrix: P/T`.
- **Known cases**: a case that fails for a reason in the product (`deploy/`, `app/`) is not fixed in the test: it is
  marked with its reason in `stack_matrix.py`, prints `known: <name>: <reason> (<what>)`, counts in T but not in P, and
  fails nothing; `stack.sh` lists each again above its last line.
- **Down, always**: compose down, the browser's container, the AI stand-in and the matrix's stand-ins and network
  removed by exact name, the secrets, the compose copy and the captured passcode deleted; any container or network of
  the project left is exit 1. `ci.sh`'s gate line ends `stack P/T` (the matrix's cases, the AI steps' and the browser
  run as one); the stage takes about four minutes, most of it the two ingests the AI steps wait for.
- **Unit tests** (`test_stack_tools.py`, no Docker): the raw reader against a server on 127.0.0.1, the case runner and
  its checks, the trusted-proxy verdicts, the stage's line, the browser run's helpers, the AI steps' verdicts.

### The arm64 job

`.github/workflows/ci.yml` has a second job, `arm64`, on GitHub's own arm runner (`ubuntu-24.04-arm`, 20 minutes, in
parallel with `gate`), that runs `tools/verify/arm_smoke.sh`. The script refuses (exit 2) anywhere `uname -m` is not
`aarch64`; builds the two runtime images natively (the ETL image picks its supercronic by `TARGETARCH`); checks each
image's architecture is `arm64`, and that `supercronic -version`, `import etl` and `caddy version` run; then runs
`stack.sh --smoke` (demo household, up, `/healthz` 200, sign-in 204, one export 200 with the session and 401 without,
down: no browser, no matrix; its line `stack smoke: 5/5`). It ends with `arm64 smoke: OK`. Never run it on this host.

## Which policy for which change

| The change… | `--expect` | What it allows |
|---|---|---|
| refactors, comments, docs, app-only work, a config comment | `identical` | nothing: every export byte-equal |
| adds an export key or file (with the schema bump) | `schema-additive` | new keys / files; existing values unchanged |
| moves fallback vendor strings (cleanup, title-casing) or adds `renames:` | `vendors-fallback-only` | a vendor may only be re-cased or follow a `renames:` entry, on rows no `patterns` entry produced; the report lists every move (`old -> new: rows`), merges and splits by name |
| lets learned rules recategorize rows | `categories-only` | a changed row's new `category_source` must be `learned`; `tags` move only on such a row whose category pair moved; schedule keys and `ledger_drift` pass as additive; `schema_version` may only increase |
| adds or changes a `split:` rule | `splits` | new rows are parts of a head row flagged `is_parent` (≥ 2 per parent, summing to it in exact cents, carrying exactly an ordinary row's keys); a parent may only clear `needs_review`; every other row untouched beyond `parent_id` / `is_parent`; `accounts.json` identical; prints `splits: P parents, C children` |
| removes keys, files or quality-check rows listed with the change | `removals` | only the listed removals; `schema_version` may only increase; nothing added |
| restructures the engine, changing only what it declares | `refactor` | `schema-additive`, except that `quality_report.json` `checks` compare as a multiset of `(name, status)` (the list's order and every key of a check other than `name` and `status` are ignored; the two count by their JSON text, so `null`, a missing key and `"None"` are three values; each `(name, status)` whose count differs prints `checks: <name> <status> ×a → ×b`); plus exactly the listed `--allow-removed` and `--allow-changed` specs |

`removals` and `refactor` take one `--allow-removed SPEC` per removal (the flag is refused under any other policy):

- `FILE` — the file may be absent from head, and `manifest.files` then loses exactly that name (order kept);
- `FILE:KEY.PATH` — that key and its subtree may be removed (dict keys from the file root; a key path never reaches
  into a list), but nothing under it may be added or changed. When the key holds a list, its subtree is every row:
  while the list keeps its length, any field of any row may vanish with no row removed, so allow-list a scalar key, a
  whole file or the rows a `[FIELD=GLOB]` spec names instead;
- `FILE:KEY.PATH[FIELD=GLOB]` — rows of that list (a list of objects in base) whose `FIELD` matches the `fnmatch`
  glob may be removed; a matching row head keeps must be unchanged and in its place (under `refactor` too, where the
  other rows may gain keys), and every other row is compared as usual. A row whose `FIELD` is missing or null is
  matched as the text `None`.

`FILE` is a `*.json` name; `KEY` and `FIELD` are `[A-Za-z0-9_-]+`; a `GLOB` has no `]`. `transactions.json` is never
listed, nor is the manifest itself or its `files`, `schema_version`, `generated_at` or `simplefin_fetched_at`. Each
allowance prints `allowance <spec>: N removed` (0 when it matched nothing), never a value. `manifest.json:<feed>_fetched_at`
is also how a feed's stamp may vanish without tripping exit 3.

`refactor` also takes one `--allow-changed SPEC` per declared change (refused under every other policy):

- `FILE:KEY.PATH` — anything at or under that key may differ: a changed value, a key added or removed (the key itself
  included), a list there or below it that grows, shrinks or reorders. As with removals, a key path never reaches into
  a list, but when the key holds a list its subtree is every row, so any field inside any row may change too: name a
  scalar key or the rows a `[FIELD=GLOB]` spec picks instead when that matters;
- `FILE:KEY.PATH[FIELD=GLOB]` — rows of that list (a list of objects in base) whose `FIELD` matches the glob are taken
  out of base and head before the comparison and may be added, removed, changed or moved; the rows left are compared
  pairwise by position as usual, so a row whose `FIELD` leaves the glob is a violation. A row whose `FIELD` is missing
  or null is matched as the text `None`. On `quality_report.json:checks` `FIELD` must be `name` or `status` (any other
  field is a usage error under `refactor`, for `--allow-removed` specs too); the glob is matched against that text and
  covers every `(name, status)` difference it matches (a status flip is two, both covered);
- `transactions.json:FIELD` — that one row field may differ on rows both sides have (matched by `id`), and may also
  appear on such a row or vanish from it; rows are never added or removed. `transactions.json` in any other form is a
  usage error.

`--allow-changed` never names a whole file, nor the manifest's `files`, `schema_version`, `generated_at`,
`simplefin_fetched_at` or any other `*_fetched_at` stamp (a stamp that differs is exit 3 before any allowance is read,
so the spec is a usage error). `--allow-removed` specs claim a difference or a row before `--allow-changed`
specs; under the checks rule an `--allow-removed` spec on `quality_report.json:checks` covers only a `(name, status)`
whose count fell. Each changed allowance prints `allowance-changed <spec>: N changed` (0 when it matched nothing, which passes),
never a value; the lines follow the `--allow-removed` lines, each flag's in the order given.

A change outside every policy (a deliberate category move by a new keyword rule, say) is gated by an exit-1 diff whose
violations equal an enumerated, predicted list, recorded with the change — never by loosening a policy.

`vendors-fallback-only` needs a non-empty `patterns` vendor set and reads `renames:` (the wrapper passes the
`vendor_rules.yaml` of the household config `--config-dir DIR` names; the image holds none). It also excuses a transaction vendor that moved to a `renames:` entry's `to:`, but `renames:`
rewrites references (answers, overrides, `cancelled_subscriptions`, `learning.exclude`, learned rules), never a stored
transaction's vendor. A rename-explained move in `transactions.json` therefore means the pipeline changed something
it should not — treat it as suspect, not as the rename taking effect.

## A gate, in order

```
tools/verify/ci.sh                                   # green on the worktree
# an offline ingest of the base ref into ~/ff-verify/base/data and of the worktree into ~/ff-verify/head/data,
#   back to back over the same inputs
tools/verify/export-diff.py base head --expect <policy>   # the table above; under removals and refactor add
                                                          #   --allow-removed SPEC for each listed removal, and
                                                          #   under refactor --allow-changed SPEC for each change
# the worktree ingested again into ~/ff-verify/head2/data: determinism, the same code twice over the same inputs
tools/verify/export-diff.py head head2 --expect identical
FF_SUITE_LOG=~/ff-verify/head/suite.log \
  tools/verify/ci.sh ~/ff-verify/head/data/exports   # app tests WITH head exports: counts only, output in the log
tools/verify/screenshots.sh head ~/ff-verify/head/data/exports   # UI changes: exit 0 and overflow.json == [] only
```

`tests/test_e2e.py` is the in-suite form of the determinism pin (two full ingests, identical export hashes, plus
structural invariants). It runs on the generated demo household `full`, every input generated in the test, so it
skips nothing wherever pytest runs, and it only ever writes to `$FF_E2E_DIR` or a pytest temp dir — a target that is
the repo's or a live container's `data/` or `scenarios/` is refused.

## Screenshots

`screenshots.sh <label> <exports-dir>` builds the SPA from the worktree inside `<prefix>-web:<test tag>`, serves it with the
exports via `nginx:alpine` on `127.0.0.1:${FF_SHOT_PORT:-18201}`, and drives Playwright over every `PAGES` route at
390×844 and 1280×900, dark and light, shoots each route the driver's `CLICK_SHOTS` names once more for each of its
clicked shots, with their controls clicked (Settings with the "Add an asset" form open,
`settings_add_asset__<WxH>__<theme>.png`, and with the AI categorization dialog open,
`settings_ai_dialog__<WxH>__<theme>.png`; the one place the driver clicks before a route's shot; a control not there
and enabled within `CLICK_TIMEOUT_MS`, 3 s, is a "click failed" problem of that shot in `overflow.json`, and the run
goes on), then the phone's open navigation drawer in each theme (19 routes → 76 + 8 + 2 = 86 PNGs) →
`~/ff-verify/<label>/shots/*.png` + `overflow.json`.
The label must match `^[a-z0-9][a-z0-9._-]*$` (it names directories that are created and cleared). The exports are
treated as real: the directory must resolve strictly inside the scratch root and every shot is taken with privacy mode
on (`FF_SHOT_PRIVATE=1` is forced), which protects the files at rest. **No agent opens a PNG produced from real
exports, masked or not**: on real exports the script is run for its exit status and `overflow.json` only. Real exports
are any at or inside a real-data scratch root, whatever a flag says. `FF_DEMO_EXPORTS=1` lifts both rules and is for
synthetic exports only (an exports directory at or inside a real-data scratch root, as resolved or as spelt, is still
refused, and so is one whose top-level JSON file resolves into a root); the PNGs of a demo household are read.

- **Demo shots outside the scratch root**: with `FF_DEMO_EXPORTS=1`, `FF_SHOT_OUT=<absolute dir>` takes the PNGs and
  `overflow.json` instead of `<scratch root>/<label>/shots` (the built SPA stays in `<scratch root>/<label>/site`). It
  must resolve, symlinks followed, outside the repository and outside both real-data scratch roots (neither at, inside
  nor above any) and be absent (it is made once every check has passed) or an empty directory the caller owns and can write to (the driver's
  container runs as root and hands the directory over when it ends): the script never clears a directory it was
  pointed at, so a non-empty one is refused. With real exports a set `FF_SHOT_OUT`, even an empty one, is refused:
  their shots stay masked under the scratch root. Each refusal is exit 2, before anything is created.
- **A demo household's own state**: with `FF_DEMO_EXPORTS=1`, `FF_SHOT_STATE=1` serves the state of the demo tree the
  exports belong to (the generator's layout: `<tree>/data/exports`, `<tree>/truth.json`, `<tree>/state`): its
  `answers.json` and `manual_assets.json` (the names are the driver's `STATE_DOCUMENTS`), each when present, are served
  as `/api/answers` and `/api/manual-assets` instead of the empty documents (a document it lacks stays empty; a tree
  with no `state/` is served both empty, with a line saying so). The variable takes no path, any other value is
  refused, and the tree must hold `truth.json`, the generator's mark: a real household's state (a frozen copy of its
  inputs, a live store's folder) has the store's shape exactly, so no value can name it. The state directory must lie
  outside both real-data scratch roots, as resolved and as spelt, and be no link (the generator writes none); each
  file is copied with its links followed, so one that does not resolve to a file, resolves into a root or outside the
  state directory, or holds a document the store refuses (its own
  `validate_answers` / `validate_manual_assets`, whose reason the refusal gives) is refused. With real exports a set
  `FF_SHOT_STATE`, even an empty one, is refused: real exports are always shot with the empty documents. Each refusal
  is exit 2, before anything is created.
- **The page before any export exists**: `FF_SHOT_NOTHING_BUILT=1 [FF_SHOT_OUT=<an empty or new dir>]
  tools/verify/screenshots.sh <label>` serves the built SPA, the five store documents and no export file (a build that
  carried one into the site is exit 1), and shoots the dashboard's root at each viewport in both themes once
  `[data-first-run="nothing-built"] h1` is on screen (in place of the data-ready wait), its clock fixed at the driver's
  `NOTHING_BUILT_AT` in UTC (no manifest names an ingest instant or a zone). A 404 for one of the 14 export files
  (`EXPORT_FILES`, pinned to `app/src/data/types.ts`) is the page's premise and no `overflow.json` entry; every other
  problem is one, named `(nothing built)`. The variable takes `1` and nothing else; a second argument, and any
  `FF_SHOT_STATE`, `FF_SHOT_ROUTES` or `FF_SHOT_QUERY`, are refused (exit 2, before anything is created). With no
  household in the run `FF_SHOT_OUT` is taken under the same location rules as a demo run's, `FF_SHOT_PRIVATE` is taken
  as given and `FF_DEMO_EXPORTS` is not read. Exit 1 unless the PNGs number viewports × 2 themes and `overflow.json`
  is empty.
- **The demo households' shots**: `tools/verify/demo.sh <dir> full new`, then
  `FF_DEMO_EXPORTS=1 FF_SHOT_OUT=<an empty or new dir> FF_SHOT_STATE=1 tools/verify/screenshots.sh
  demo-full <dir>/full/data/exports`.
- **Routes** come from `PAGES` (pages, hub tabs and `extra` routes — a `:id` route is shot with the first account in the
  served `accounts.json` that has transactions, else the first account at all, else the id `none`, which the app
  answers with its unknown-account card, under the pattern's file name, `accounts_id__<WxH>__<theme>.png`).
  `REDIRECTS` sources are refused.
- **The open drawer**: at each phone viewport (narrower than the app's 768px cut: 390×844 in the gate), in each theme,
  after the routes, the driver opens Home, clicks the top bar's menu button by its accessible name (`Open menu`, never a
  position), waits until the drawer has slid in to the screen's edge and shoots the screen (the drawer and its backdrop
  are fixed to it) as `root_drawer__390x844__<theme>.png`, with the routes' overflow and error checks (overflow lines
  name it `/ (open drawer)`); a route's overflow check leaves the off-canvas drawer (the `<aside>`) out, this one
  checks the drawer's own content too. A `FF_SHOT_ROUTES` subset shoots it only when it holds `/`.
- **Exit 1** when the manifest parses to 0 routes, a store document is not served, the PNG count is not (routes and
  their clicked shots) × viewports × 2 themes plus 2 per phone viewport when Home is shot (`expected_shots`), or
  `overflow.json` is not empty:
  horizontal overflow, an uncaught page error, a request that failed or answered ≥ 400 (`/api/*` included: the site
  serves every document the app reads), a console error or fonts not ready in time (`fonts not ready`), on any shot.
  Overflow is a failure, not a baseline. Failure lines name route, viewport and theme only, never page text. It cannot
  see clipping inside an `overflow-x: auto|scroll|hidden` scroller (a `TableScroll`, the tab bar), so the reviewer reads
  the PNGs of synthetic exports (`FF_DEMO_EXPORTS=1`), never those of real exports.
- **Iterating** (the gate leaves all of these unset):
  - `FF_SHOT_ROUTES="/a /b"` shoots a subset; each entry must be exactly a `PAGES` route pattern (checked host-side
    before the build; a whitespace-only value is refused).
  - `FF_SHOT_VIEWPORTS="360x780 1100x800"` overrides the two viewports (each `<width>x<height>`; PNG names follow; the
    run prints a banner; each one narrower than 768 is a phone, with the drawer shots).
  - `FF_SHOT_QUERY="/spending/reports?measure=count&chart=table"` shoots a route WITH a query: entries `<route>?<query>`,
    the route an exact `PAGES` pattern the run shoots, one query per route, a well-formed `k=v&k=v` with no `#`; the PNG
    is `<route>_query__<WxH>__<theme>.png`, never the query text.
  - `FF_SHOT_PRIVATE` is forced to `1` for real exports (the driver sets the flag before the page's own scripts). With
    `FF_DEMO_EXPORTS=1` it is taken as given: `1` masks, `0` or unset shoots unmasked; nothing else is accepted.
- **Browser**: `playwright/python:v${FF_PW_VERSION:-1.46.0}-jammy`, so the bundled browsers and the pip package share one
  version. Every context emulates `prefers-reduced-motion: reduce` (`context_options`), which `EChart` answers with
  `animation: false`, so no chart is shot half-drawn. Each shot waits for `document.fonts.ready`, for at most 5 s; fonts
  not ready by then are an overflow entry of the kind `fonts not ready` (the page is still shot).
- **Clock and zone**: every page is shot at the exports' ingest instant: before it loads, its `Date` is fixed at the
  served `manifest.json` `generated_at` (`page.clock.set_fixed_time`, timers keep running), so freshness badges, "due"
  schedules and the overflow report do not depend on the day the shots are taken. A manifest without a parseable
  `generated_at` (with its UTC offset) fails the run, naming the file and the key. Every context runs in the household's
  zone, the served `assumptions.json` `household.timezone` (`timezone_id`), so clock times and "today" read as they do
  in the household; when the key is absent, is not shaped like a zone id or names a zone Playwright refuses (the probe
  makes a context and a page in it and closes both: Chromium refuses an unknown zone when the page is made), the
  browser runs in UTC and the driver prints one line saying so (a masked run does not print the zone). The pinned
  instant is an instant: the zone moves only how it reads.
- **The store's documents**: the static site answers the five documents the app reads at `/api/*` as an idle, healthy
  stack's store does, so no shot shows "Store unreachable" or a disabled Refresh: `/api/prefs` is the store's empty
  preferences (`fixtures/prefs.empty.json`), `/api/answers` and `/api/manual-assets` its empty documents
  (`answers.empty.json`, `manual-assets.empty.json`; a demo household's own with `FF_SHOT_STATE=1`), `/api/refresh` a
  fixed idle status (`refresh.idle.json`: every key `GET /api/refresh` returns, `state: "idle"`, `can_refresh: true`,
  the default schedule `01:00, 09:00, 17:00` in the household's zone, which the script fills in, UTC without one,
  and an ingest that is not wanted), `/api/scenarios` below. `test_screenshots_driver.py` builds each empty document
  with the store's own functions and the idle status with its own view of a refresh directory holding only the
  scheduler's default schedule, so a change to
  either fails the harness tests. Each is a copied file (never a link); the script curls all five before driving and
  exits 1 if one is not served. Nothing is written to them: nginx refuses a `PUT`, and a refused request is an overflow
  entry like any other.
- **Scenarios**: `/api/scenarios` is served from `tools/verify/fixtures/scenarios.synthetic.json` — two hand-written
  second-house scenarios for the demo household, their draws keyed by its account ids (`tools/demo/household.py`:
  checking, the joint savings, the certificate and the brokerage; `test_screenshots_driver.py` pins that every key is
  one of them). The page opens on the first, a fully funded plan; the second's checking draw is set well above the
  checking balance of `full` at the default end date, so the overdraw ⚠ renders in the scenario tabs and the
  comparison on every run of `full` (`tests/test_demo_shapes.py` holds both to the exported balances). A draw keyed
  by an account a household lacks is ignored (the funding rows list the household's own accounts). Never the live
  `scenarios/` dir. The script also checks that it holds at least one scenario.

## Gotchas

- `export-diff.py` runs the module **baked into the image**; after editing `deploy/export_diff.py`, run `ci.sh` (it
  rebuilds `<prefix>-etl:<test tag>`) first.
- A scratch ingest needs `~/ff-verify/<label>/{data,scenarios}` created as your user before docker touches them — a
  bind source docker creates is root-owned and the ingest fails on its first write.
- `FF_PW_VERSION` selects both the pinned Playwright image and the `pip install playwright==<ver>` run inside it; bump
  the one variable, never the image alone.
- Routes are parsed from the `PAGES` manifest in `app/src/App.tsx` (one `{ to:` per line, `];` only at the end).
  `FF_SHOT_ROUTES` entries are pattern names, not URLs: `/accounts/:id`, never a real account id; `slug()` drops the
  `:`, so a real id never reaches a file name.
- `FF_SHOT_QUERY` is how a proof that needs a filtered or variant view is re-shot with the harness instead of an ad-hoc
  driver: `FF_SHOT_ROUTES="/spending/transactions" FF_SHOT_QUERY="/spending/transactions?from=2026-03-01&to=2026-03-31"`.
- A test under `tests/` must never import anything `.dockerignore` keeps out of the image: the image's pytest would
  fail to collect the whole suite on the import.
- Never point these at a live deployment's exports or `scenarios/`; nothing
  here calls a bank feed or a price API.
