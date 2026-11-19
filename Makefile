# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
.PHONY: all ingest validate app build serve test test-etl test-app lint lint-etl lint-app clean \
        fetch backfill refresh simplefin-claim scenario-store

all: ingest app

# Re-ingest everything from the read-only source folder into data/ (offline). The config is config/ beside this
# Makefile, or the one FF_HOME (or FF_CONFIG_DIR) names; with none, it says where it looked.
ingest:
	uv run python -m etl ingest

# Check the config files (config/ beside this Makefile, or FF_HOME's) and print every problem at once (offline,
# read-only; docs/configuration.md lists every key)
validate:
	uv run python -m etl validate

# --- SimpleFIN (the only networked steps) --------------------------------
# One-time: exchange a setup token for the stored access URL.
#   make simplefin-claim TOKEN=<base64-setup-token>
simplefin-claim:
	uv run python -m etl simplefin claim --token "$(TOKEN)"

# Incremental fetch (~35-day txn window; balances+holdings always current).
fetch:
	uv run python -m etl simplefin fetch

# One-time gap backfill (~90-day window) to fill from the last CSV up to today.
backfill:
	uv run python -m etl simplefin fetch --days 90

# Pull fresh SimpleFIN data, then rebuild from local snapshots.
refresh: fetch ingest

# Launch the dashboard (installs npm deps on first run)
app: app/node_modules
	cd app && npm run dev -- --open

# Optional local scenario-store API for dev (the dashboard falls back to
# localStorage when this isn't running). Writes to ./scenarios/ + ./var/ (both
# gitignored). /api/refresh answers here too, so the refresh panel renders for
# real; with no `refresher` process in dev a queued run just reports the worker
# as unavailable, which is the honest thing for it to say. The dev server has no
# sign-in in front of it, so the store runs with --auth off (loopback only).
scenario-store:
	mkdir -p ./scenarios ./var/refresh
	uv run python deploy/scenario_store.py --host 127.0.0.1 --port 8123 \
	  --dir ./scenarios --refresh-dir ./var/refresh --auth off

app/node_modules: app/package.json
	cd app && npm install

build: app/node_modules
	cd app && npm run build

serve: build
	cd app && npm run preview -- --open

test: lint test-etl test-app

test-etl:
	uv run pytest -q

test-app: app/node_modules
	cd app && npm run test && npm run typecheck

lint: lint-etl lint-app

lint-etl:
	uv run ruff check etl tests deploy tools

lint-app: app/node_modules
	cd app && npm run lint

clean:
	rm -rf data/
