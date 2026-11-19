# Design docs

> Scope: the index of this repo's durable design docs. Every doc describes the system as it is; verify a name against the code before relying on it — the module
> docstrings each doc points at are the code truth.

`AGENTS.md` and `app/AGENTS.md` are the map (hard rules, commands, layout, recipes); these files hold the mechanics a
maintainer reads before changing an area. Codex loads only the AGENTS.md chain, so every doc here is named in the
index at the end of one of those files.

| Doc | Read before touching |
|---|---|
| [categorization-and-learned-rules.md](categorization-and-learned-rules.md) | `config/*_rules.yaml`, `config/overrides.yaml`, `etl/enrich/{categorize,rules,learned,answers,overrides}.py`, `etl/rules_cli.py` |
| [importers.md](importers.md) | `etl/importers/` (the registry, the native formats, the CSV presets, OFX), `etl/import_cli.py`, an account's `sources:` and the join in `Pipeline.parse_transactions`, `tests/importers/`, the bank feed's fetch (`etl/simplefin.py`) |
| [ai.md](ai.md) | `etl/ai/`, `etl/enrich/ai.py`, `household.yaml` `ai`, `ai_status.json`, the refresh's AI step and markers, the Settings AI card and the consent in `prefs.ai`, `ff ai` (the household's guide: `docs/ai-categorization.md`) |
| [splits.md](splits.md) | `split:` legs, `etl/enrich/splits.py`, any new aggregate or count, `app/src/lib/splits.ts`, the register's regrouping |
| [schedules.md](schedules.md) | `etl/enrich/recurring.py`, `lib/schedules.ts`, `lib/forecast.ts`, `lib/upcoming.ts`, `lib/obligations.ts` |
| [renames.md](renames.md) | `renames:` in either rules file, `etl/enrich/renames.py`, `lib/categoryRenames.ts` |
| [information-architecture.md](information-architecture.md) | `App.tsx` `PAGES` / `REDIRECTS`, `capabilities.ts`, hubs, links, the first-run page, modules, Settings, filters and URL state, the Data page, the refresh and the ingest without a fetch (`deploy/refresh*`) |
| [data-and-sync.md](data-and-sync.md) | `DataContext.tsx`, export schema versions, the update banner, the store's documents (`deploy/scenario_store.py`), `useSyncedDoc`, answers, prefs and manual-assets sync, the signed-out state |
| [register-and-answers.md](register-and-answers.md) | `TxnTable`, the Review inbox, `CategoryEditor`, answers merging, saved views and reports |
| [privacy.md](privacy.md) | anything that prints money, `lib/privacy.ts`, the smoke privacy net |
| [mobile-and-charts.md](mobile-and-charts.md) | layouts below `md`, `EChart`, `lib/echarts.ts`, chart "view data", screenshots |
| [figures-and-vocabulary.md](figures-and-vocabulary.md) | a figure several pages show, the month in progress, `lib/categories.ts`, `lib/format.ts`, the budget grid, the report builder |
| [platform.md](platform.md) | `compose.yaml`, `ff`, `deploy/Caddyfile`, `deploy/auth.py`, the sign-in in `deploy/scenario_store.py`, `deploy/make_crontab.py`, `deploy/alert.py`, `etl/install.py` and the `etl/*_cli.py` commands `ff` runs, `.env.example` |
| [deploy-and-gates.md](deploy-and-gates.md) | `deploy/Dockerfile.*`, `.dockerignore`, `tools/verify/ci.sh` (its images and stack stages), `tools/verify/demo.sh`, `.github/workflows/ci.yml`, `deploy/export_diff.py`, the comment-hygiene, page-copy and docs-size tests, test fixtures |
