# Architecture

An overview for whoever wants to know how Family Financials fits together before reading the code. `AGENTS.md` is
the contributor's map (rules, commands, recipes), and [design/](design/README.md) holds each area's mechanics.

## Three parts

```
imports/ (statement files)  ─┐
store/   (feed snapshots)   ─┼─► the rebuild (etl/) ─► data/: finance.db, exports/*.json ─► the dashboard (app/)
config/  (the household)    ─┤   offline, the same                                          │
state/family/ (answers …)   ─┘   output for the same input                                  │ edits
        ▲                                                                                   ▼
        └──────────── the family store (deploy/): the sign-in and the family's documents ◄──┘
```

1. **The rebuild** (`etl/`, Python): reads the household's config (`config/*.yaml`), its statement files, the bank
   feed's stored snapshots and the family's answers, and writes one SQLite database and a set of JSON files. It makes
   no network call, and the same inputs give the same output. Every check it runs (reconciliation, coverage, transfers,
   splits) is in the output as a quality report the dashboard shows.
2. **The dashboard** (`app/`, Vite, React, TypeScript, ECharts): a static page that reads only those JSON files. The
   web server serves each `data/exports/X.json` at `/X.json` (`data/exports/manifest.json` at `/manifest.json`),
   behind the sign-in (a request without a session gets 401, a browser is sent to `/login`). It never queries the database and never computes what the rebuild already
   decided; it adds views, filters and the arithmetic of what-ifs.
3. **The family store** (`deploy/scenario_store.py`, Python standard library): the sign-in, and four small documents
   the family edits in the dashboard (review answers, preferences, second-house scenarios, hand-entered assets). The
   next rebuild reads the answers and the assets.

## The rebuild, stage by stage

`etl/pipeline.py` runs, in order: parse the statement files (each through its format: a built-in reader, OFX, or a
CSV preset), parse the bank feed's snapshots, enrich (vendors, transfers, income, the family's answers and the learned
rules, categorization, splits, overrides, recurring schedules; the order is in `AGENTS.md`), build net worth's series
(`etl/networth.py`, the one builder every balance figure reads), run the quality checks, and write the database and
the exports (`etl/exports.py`, versioned by `SCHEMA_VERSION`). Money is integer cents inside, dollars in the exports.
Anything ambiguous becomes a question in the exports, never a guess.

## The networked steps

Each is a separate command that writes a local store the rebuild then reads; the rebuild never imports them:

- **The bank feed's fetch** (`etl/simplefin.py`): one request to SimpleFIN Bridge per refresh, saved as a dated
  snapshot in `store/simplefin/`.
- **AI categorization** (`etl/ai/`), off unless the family turns it on: vendor names out, suggested categories back
  into `store/ai/` ([ai-categorization.md](ai-categorization.md)).

## The install: four services

`ff` (a POSIX shell script) and `compose.yaml` run the program as four containers, from two images:

| Service | Image | Does | Reaches |
|---|---|---|---|
| `web` | web (Caddy + the built dashboard) | serves the page, the exports and the sign-in page; asks the store whether each request is signed in | the published port; the store |
| `store` | ETL | the sign-in and the family's documents | nothing outside (an internal network) |
| `scheduler` | ETL | the timed refresh, at the hours `household.yaml` names | the internet (the feed, the AI service, alerts) |
| `refresher` | ETL | a refresh the family asks for in the dashboard | the same |

A refresh (`deploy/refresh.sh`) fetches the feed, runs the AI step when it is on, then rebuilds. The two services a
browser reaches hold no feed, AI or alert credential and cannot write the household's data; they ask for a refresh
by leaving a file the refresher reads. [security.md](security.md) and [design/platform.md](design/platform.md) say
how each boundary is kept.

Every other `ff` command (`init`, `demo`, `validate`, `doctor`, `import`, `rules`, `backup`, `restore`, `ai`) runs
once in the ETL image as the invoking user, with the install mounted and no network unless it needs one (`init` for a
bank feed, `ai run`).

## The repository

| Folder | What |
|---|---|
| `etl/` | the rebuild and every `ff` command's Python half |
| `app/` | the dashboard |
| `deploy/` | the Dockerfiles, the web server's config, the store, the refresh, the sign-in page |
| `starter/` | the rule files a new household starts from |
| `examples/` | the smallest household that runs, and an answers file for `ff init` |
| `tests/` | the Python tests, on invented households only |
| `tools/demo/` | the generator of the six invented demo households the tests and the demo site read |
| `tools/verify/` | the gate (`ci.sh`) and its helpers, all run in Docker |
| `tools/maintainer/` | the scripts a maintainer runs once a release is public |
| `docs/` | these documents; `design/` the mechanics of each area |

## The gate

`tools/verify/ci.sh` runs every check in Docker, so a contributor needs no Python or Node: lint and the Python tests,
the dashboard's lint, tests and type check on the invented households, the route matrix (every page for every demo
household, with and without privacy mode), the runtime images and their contents, the dependency licences, a secret
scan, the demo site, and the whole stack brought up and attacked by a security matrix and a signed-in browser. The
same gate runs on every pull request. [design/deploy-and-gates.md](design/deploy-and-gates.md) describes each stage.
