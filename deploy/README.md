# deploy/ — what the containers run

The images, the web server and its sign-in page, the store, and the scheduled and on-demand refresh. `compose.yaml`
at the repository root wires them into four services (`web`, `store`, `scheduler`, `refresher`) and `ff` runs them;
how it all fits (the install root, the capability split, the sign-in, Caddy's rules, the schedule, alerts, backup and
upgrade): `docs/design/platform.md`. Nothing here holds a secret or a household's file.

| File | What it is |
|---|---|
| `BASES.md` | The base images' digests, when each was read, and how to move a pin |
| `Caddyfile` | The `web` server: the host rule, the security headers, the public set, everything else behind the store's `/auth/check` |
| `Dockerfile.etl` | The ETL image's stages (`uv`, `python-base`, `py-build`, `test`, `etl`): the runtime that `store`, `scheduler` and `refresher` run |
| `Dockerfile.web` | The web image's stages (`web-build`, `web`): the built dashboard served by Caddy |
| `README.md` | This page |
| `alert.py` | One failure alert through the channels in `secrets/alerts/` (a Telegram bot, a webhook) |
| `auth.py` | The sign-in's rules: passcode lines, sessions, devices, the throttle, cookies, the Origin and host rules |
| `export_diff.py` | Compares two export directories under a named policy (`tools/verify/export-diff.py` runs it) |
| `feed_check.py` | Whether the household has a bank feed, so `refresh.sh` fetches or reads the files alone |
| `make_crontab.py` | The scheduler's start: the crontab from `household.yaml`, `schedule.json` in the mailbox, then supercronic |
| `public/` | Everything served without a session: the sign-in page (`login.html`, `login.css`, `login.js`), `icons/`, `site.webmanifest` |
| `refresh.sh` | One refresh: the feed's fetch (never fatal), then an ingest; `refresh.sh ingest` the ingest alone; `refresh.sh test-alert` |
| `refresh_runner.py` | The `refresher`: runs `refresh.sh` for a queued "Refresh now" and the ingest alone when the store asks |
| `refresh_state.py` | The mailbox's files and the rate limits (`decide()` is pure), shared by the store, the refresher and `refresh.sh` |
| `render_icons.py` | Renders the icon PNGs from `public/icons/icon.svg`; run by hand after editing it, the PNGs committed |
| `safe_files.py` | Reads and writes that never go through a link, for the folders two containers share |
| `scenario_store.py` | The `store`: the family's documents (`/api/*`), the sign-in (`/auth/*`) and `/api/refresh` |
