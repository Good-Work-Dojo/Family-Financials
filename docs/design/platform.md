# The platform: the install, the containers, the sign-in and `ff`

> Scope: how a household runs the dashboard: the install root and its directories, the four services and what each
> may touch, the sign-in, the web server's rules and headers, the images, the schedule and its alerts, the `ff`
> command and its backup, restore, upgrade and `doctor`. Describes the system as it is; the code truth is
> `compose.yaml`, `ff`, `deploy/Caddyfile`, `deploy/auth.py`, `deploy/scenario_store.py`, `deploy/make_crontab.py`,
> `deploy/alert.py`, `deploy/refresh.sh`, `etl/install.py` and the `etl/*_cli.py` modules the sections name.

## The install root

One directory holds a household's whole install (`FF_HOME`): `ff`, `compose.yaml`, `.env` and the directories below.
`ff` uses the directory it sits in unless `FF_HOME` names another. Every container sees the same names under `/ff`
(`FF_HOME=/ff`), and every program finds each directory from there (`etl/config.py` `Paths`, `deploy/refresh_state.py`
`locate`); a variable per directory (`FF_CONFIG_DIR`, `FF_STATE_FAMILY_DIR`, …) still moves one, which tests use.

| Path | Holds | Mode | Mounted by (how) |
|---|---|---|---|
| `config/` | `household.yaml`, `accounts.yaml`, the rule files and `presets/` (`docs/configuration.md`) | 0700 | scheduler, refresher (read-only) |
| `secrets/` | nothing of its own: the four folders below | 0700 | none |
| `secrets/web/` | `passcodes`, the sign-in's hashes | 0700 | store (read-only) |
| `secrets/feed/` | `simplefin_access_url`, the bank feed's credential | 0700 | scheduler, refresher (read-only) |
| `secrets/ai/` | `api_key`, the AI step's key (`docs/design/ai.md`) | 0700 | scheduler, refresher (read-only) |
| `secrets/alerts/` | `telegram_token`, `telegram_chat_id`, `alert_url` | 0700 | scheduler, refresher (read-only) |
| `imports/` | the statement files dropped in | 0700 | scheduler, refresher (read-only) |
| `store/` | the bank feed's snapshots (`store/simplefin/`): history no feed gives again; the AI step's answers and run log (`store/ai/`) | 0700 | scheduler, refresher (read-write) |
| `state/` | nothing of its own: the five folders below, each mounted alone | 0700 | none |
| `state/family/` | the family's documents: `answers.json`, `prefs.json`, `scenarios.json`, `manual_assets.json` | 0700 | store (read-write); scheduler, refresher (read-only) |
| `state/auth/` | `sessions.json` and `devices.json` (hashes only) | 0700 | store (read-write) |
| `state/refresh/` | the mailbox: `request.json`, `ledger.json`, `status.json`, the `ingest_wanted`, `ai_wanted` and `ai_reset_wanted` markers, the run's liveness file, `schedule.json` | 0700 | store, scheduler, refresher (read-write) |
| `state/engine/` | the workers' own: `learned_rules.json`, the refresh lock, the failure streaks, the re-auth set, `fetch.log`, the heartbeat | 0700 | scheduler, refresher (read-write) |
| `state/caddy/` | Caddy's data and config (a domain's certificates among them) | 0700 | web (read-write, at `/state/caddy`) |
| `data/` | `finance.db` (0600) and `exports/`: an ingest rebuilds both | 0700 | scheduler, refresher (read-write) |
| `data/exports/` | the dashboard's JSON (0644 files, each written as a new file renamed over its name, so a link left at an export's name is replaced, never written through or served) | 0700 | web (read-only, at `/srv/exports`) |
| `backups/` | `ff-<UTC stamp>.tar.gz` archives | 0700 | none (`ff backup` writes it) |

The table is `etl/install.py` `DIRS`. The install directory itself and every directory in it are 0700, the owner's
alone: each holds the household's finances (its statements, config, database and exports) or a secret, every service
runs as the owner's uid, and an install under `/srv`, `/opt` or a home directory others may enter must not let another
local user read them. Every directory must exist, owned by the user the services run as, before the stack starts: a
bind mount never creates a missing source (Docker would make it empty and owned by root), so `ff` makes each missing
one with its mode as the invoking user, tightens the install directory or any of them a group or others can open (one
line each; `doctor` fails it with the `chmod`), and refuses one another user owns, naming it and the command that
hands it back. Secrets are mounted as folders, never as single
files: a file mount pins its inode, so a credential rewritten in place would stay unseen until a restart.

## The four services and the capability split

| Service | Runs | Reads | Writes | Network |
|---|---|---|---|---|
| `web` | Caddy (`deploy/Caddyfile`) | the bundle and the sign-in page (in its image), `data/exports/` | `state/caddy/` | `front` (its published ports) and `back` |
| `store` | `deploy/scenario_store.py --auth required` | `secrets/web/` | `state/family/`, `state/auth/`, `state/refresh/` | `back` only |
| `scheduler` | `deploy/make_crontab.py --run`, then supercronic running `deploy/refresh.sh` | `config/`, `imports/`, `secrets/{feed,ai,alerts}/`, `state/family/` | `store/`, `data/`, `state/engine/`, `state/refresh/` | the default network |
| `refresher` | `deploy/refresh_runner.py` | as the scheduler | as the scheduler | the default network |

The default network reaches the home network and the internet: the bank feed's fetch and the AI step's provider
need it. Inside a worker, `127.0.0.1` is the container itself, so a household's own model is reached at an
`https://` address (`docs/ai-categorization.md`).

`web` and `store` are the two a browser reaches, so neither holds a feed, AI or alert credential or an ETL write
mount, and `web` sees no secret at all; the store can *ask* for a refresh or an ingest (a file in the mailbox) but
never run one. Only the two headless workers read the config, the statement files and those credentials, and write
the snapshots and the data. `tests/test_compose.py` pins the mounts and changes with `compose.yaml`.

`state/` is split by who writes: the family's documents (the store writes, the workers read), the sessions (the store
alone), the workers' own files (the workers alone), Caddy's (web alone), and one folder two sides write, the mailbox.
No service mounts a folder that holds another's, so a compromised store cannot reach the learned rules, the refresh
lock or the feed's snapshots. **The link-proof rule**: a container that can write a shared folder can put a link, a
FIFO or a folder at any name in it, and a link resolves inside the container that follows it, where it could name the
feed credential. The refresher claims a request only when its nonce is exactly what the store writes (16 lower-case hex
digits): anything else in the mailbox never reaches a log line or an argument. Every read and write in the mailbox, and every worker's read of the family documents, goes through
`deploy/safe_files.py`: opened with `O_NOFOLLOW` and `O_NONBLOCK` and checked to be a regular file of a bounded size,
written as a new file renamed over the name, a lock never opened through a link.

`back` is an internal network: the store has no route out, and only `web` reaches it. The workers share no network
with either; they talk to the store through the mailbox only.

Every service runs as `FF_UID:FF_GID` (`.env`; the install's owner), with every capability dropped,
`no-new-privileges`, a process limit and `restart: unless-stopped`; `web` and `store` run with a read-only root file
system and a `/tmp` tmpfs, and the two workers under an init process that reaps what a run leaves behind. Health:
`web` answers `/healthz` at `http://127.0.0.1:8080`; the store its own `/healthz`; the scheduler is healthy while
supercronic runs; the refresher while its heartbeat (touched at least every 30 seconds between runs) is younger than
1,000 seconds. `web` starts once the store is healthy.

## The sign-in

### Passcodes

`secrets/web/passcodes` holds one line per passcode, `label:scrypt:15:8:1:<salt>:<hash>`: a label of 1 to 32 of
`a-z 0-9 _ -`, scrypt's cost (N = 2^15, r = 8, p = 1, `maxmem` 64 MiB), a 16-byte salt and the 32-byte hash, both
unpadded URL-safe base64. At most 8 lines are used; a malformed line is skipped and reported once on standard error
by its number, never its content. The store reads the file again when its mtime, size or inode changes. A passcode
has at least 8 characters, or 10 when all are digits; any character is accepted and none trimmed, and it is put in
Unicode normal form C before it is hashed, so the same characters typed on two devices match.

`ff passcode add NAME` asks twice without echo (`--stdin` reads one line, never from a terminal) and stores the hash
under that label, replacing a taken label in place; `ff passcode remove NAME` drops one; `ff passcode list` prints the
labels. The file is written atomically, 0600, under umask 077, every other line kept byte for byte; nothing prints a
passcode or a hash (`etl/passcode_cli.py`, which loads `deploy/auth.py` by path: the line format lives there once).
With no usable line the sign-in page says the dashboard is not set up for sign-in and names `ff passcode add`.

### Sessions

A sign-in (`POST /auth/login`, a form or JSON body of at most 1,024 bytes) that matches a line opens a session: a
token of 32 random bytes in the cookie `ff_session` (`HttpOnly; SameSite=Lax; Path=/`, `Max-Age` 400 days, `Secure`
exactly when the browser's request came over TLS). The store keeps only the token's SHA-256, with when it was created
and last seen, the label, and the passcode line's fingerprint (a SHA-256 of the label and the salt). A session ends
after 30 days unseen, 400 days after sign-in, when the store holds 50 and a new one drops the least recently seen,
and as soon as no line of the passcodes file has its fingerprint: removing or changing a passcode (a new line has a
new salt) signs out every browser it signed in and leaves the others alone. The table is in memory and saved
atomically to `state/auth/sessions.json` at sign-in and sign-out, on shutdown, and at most every 15 minutes when only
`last_seen` moved, so a session survives a restart of the store.

A first sign-in also gets `ff_device` (400 days): a mark of a browser that has signed in before, kept as a hash (at
most 200, the least recently used dropped). It carries no authority but the throttle's exemption below.

### The throttle

Every failure is one message, and the time a check takes does not depend on which line matched or whether any line
exists (each usable line is hashed once and compared in constant time; with none, a dummy hash). Per client address
(an IPv4 address, an IPv6 /64): 5 failures lock it for 1 minute, each further lock twice as long up to 15 minutes; an
address with no failure for 24 hours starts afresh, and a success clears its count. A browser holding a known device
cookie is never address-locked and its failures do not count against its address: it has its own 10 failures per 15
minutes. Every sign-in without a known device, at any time and not only during a flood, passes through a section of
32 places: the 33rd at once is refused at once as busy (429, `Retry-After: 3`), not hashed and not counted. Above 30
failures in 10 minutes from everyone, attempts in that section wait 3 seconds before they are hashed and are hashed
one at a time. A known device never enters the section or waits, and hashes in a slot of its own. A locked or refused attempt is not hashed and is not a failure; a correct passcode always succeeds once it is
checked. The throttle's state is in memory, bounded at 10,000 addresses and as many devices (a locked one dropped
last), and a restart of the store forgets it.

The client address is what Caddy passes in `X-FF-Client`: the connection's peer, or, from a proxy listed in
`TRUSTED_PROXIES`, the first address of `X-Forwarded-For` read right to left that is not a trusted proxy.

**What a stranger can do**: from one address, 5 guesses, then 5 more after each lock: 1, 2, 4, 8, then 15 minutes,
under 500 guesses a day. From many addresses at once: once 30 failures land in 10 minutes, every attempt without a
device cookie waits 3 seconds with at most 32 waiting, at most about ten guesses a second in all, each hashed one at a time.
**What a stranger cannot do**: lock the household out (its browsers carry device cookies and pass every lock and the
slowdown); tell an unknown label from a wrong passcode; read any path but the public set below; use a session cookie
after sign-out or after its passcode was removed; make a signed-in browser write from another site (the Origin rule);
reach the store from a page of another name (the host rule). The client's own `X-Forwarded-*` and `X-FF-*` headers
are dropped by Caddy before anything reaches the store.

### The Origin rule, the host rule and sign-out

Every non-GET to the store (a document's `PUT`, the refresh `POST`, sign-in, sign-out) must carry an `Origin` whose
scheme, host and port equal the request's own as Caddy saw it (`X-FF-Scheme`, `X-FF-Host`); otherwise 403 and
nothing is written. No CORS header is ever sent. The host rule is applied twice, by Caddy and again by the store
against DNS rebinding: the `Host` must be an IP address, `localhost`, a name ending `.local`, a name in `FF_HOSTNAMES`,
or the domain of `SITE_ADDRESS`; anything else is a 421 (the store answers its `/healthz` whatever the host).

`POST /auth/logout` ends the session in the store, clears the cookie and sends `Clear-Site-Data: "cache"`, so a shared
computer keeps none of the exports; a form post lands on `/login`. In the dashboard, Settings' Sign out asks first when
an edit has not reached the store, then clears the browser's copies of the family's documents. A page whose store call
is answered 401 shows one banner ("Signed out — sign in to save your changes") and keeps the edit; an export read
answered 401 sends the browser to `/login`. The sign-in page loads no bundle, so nothing loops.

## The web server (Caddy)

`deploy/Caddyfile`, in one ordered `route`:

1. **Headers on every answer**, its own errors included: `Content-Security-Policy: default-src 'none'; script-src
   'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self';
   manifest-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` (no inline script or style block,
   nothing from elsewhere; React's style attributes are allowed), `Referrer-Policy: same-origin`,
   `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Permissions-Policy: camera=(), microphone=(),
   geolocation=()`, no `Server` or `Via`, and `Strict-Transport-Security: max-age=31536000` only over TLS.
2. **The host rule** (above): 421 before anything is proxied.
3. **A GET or HEAD with a body** (a `Content-Length` other than 0, or any `Transfer-Encoding`) is a 400 and the
   connection is closed, so a body is never read as a request of its own; the store refuses one too and keeps no
   connection open.
4. With a domain in `SITE_ADDRESS`, plain HTTP answers `/healthz` and redirects everything else (308) to the domain
   over HTTPS.
5. **The scheme**: the connection's, or, from a trusted proxy, `X-Forwarded-Proto` when it says exactly `https` or
   `http`. Then the client's `X-FF-Client`, `X-FF-Scheme`, `X-FF-Host` and `X-Forwarded-*` headers are dropped, and
   every request to the store carries Caddy's own: the client's address, the scheme and the `Host` it used. Each
   request to the store has a connection of its own (keep-alive off).
6. **The public set**, served from `/srv/public`, which holds nothing else: `/login` (`login.html`), `/login.css`,
   `/login.js`, `/site.webmanifest`, `/icons/*` where the file exists; `/healthz`; and `/auth/login`, `/auth/logout`,
   `/auth/status` proxied to the store. Every other `/auth` path is a 404.
7. **Everything else** asks the store first: `forward_auth` to `/auth/check`, which carries the client's headers that
   survive step 5 (never the body); the store reads only five of them: the method, the path, `Accept`,
   `Sec-Fetch-Mode` and `Cookie`, besides the three Caddy sets. 204 lets the request through; otherwise the store's answer goes back
   as it gave it: a 302 to `/login` for a page load (a GET outside `/api/` and `*.json` that navigates or asks for
   HTML; the path judged decoded, lower-cased and with repeated slashes folded), else 401. There is no `next`
   parameter. Behind it: `/api/*` to the store; `/version.json` from the bundle, `no-store`; every other `*.json` from
   the exports, `private, no-cache`; anything else from the bundle, with `index.html` for a path it lacks, the page and
   the theme script `no-cache`.

Server limits: a request's headers within 10 seconds, its body within 15, an idle connection closed after 2 minutes,
a body at most 2 MB (above the store's largest document cap, 1 MiB, so the store answers an oversized save in words),
gzip on. The two proxies to the store read a request's body whole before passing it on (`request_buffers`: 2 KiB for
the sign-in, whose body the store reads at most 1 KiB of; 2 MB for `/api/*`), so a client that holds its body back
holds Caddy, not one of the store's threads. The store itself handles at most 64 requests at once past their
headers and answers one more 503 (`Retry-After: 1`) at once; `/healthz` and `/auth/check`, which read no body and wait
on nothing, take no place, so the health check and every signed-in request's question still answer. Caddy follows
links in the exports folder (its file server has no option against it), so the ETL writes each export as a new file
renamed over the name, which replaces a link planted there. `trusted_proxies` takes `TRUSTED_PROXIES` (none by default) with `trusted_proxies_strict`, so a client's own
addresses at the left of `X-Forwarded-For` never count. The admin endpoint is off.

### Where it listens, and TLS

Caddy runs as any uid, so it listens above port 1023: plain HTTP on 8080 and, for a domain, HTTPS on 8443. Compose
publishes both on `FF_BIND` (default `127.0.0.1`: this machine only) at `FF_PORT` (default 8080) and `FF_PORT_TLS`
(default 8443).

- **An address** (`SITE_ADDRESS=:8080`, the default): plain HTTP. On this machine only, or on the home network with
  `FF_BIND` set to the machine's address there, such as `192.0.2.10`. The passcode and the cookie then cross that
  network in clear, which `doctor` warns of. `ff up` refuses plain HTTP when `FF_BIND` is itself a globally routable
  address, or is every address (`0.0.0.0`, `::`) on a computer that has one; a private address on such a computer gets
  a note, not a refusal (and when the computer's addresses cannot be read, `0.0.0.0` gets a note too).
- **A domain** (`SITE_ADDRESS=dash.example.net`, `FF_BIND=0.0.0.0`, `FF_PORT=80`, `FF_PORT_TLS=443`): automatic HTTPS,
  the certificate kept in `state/caddy/`; the plain listener answers only `/healthz` (the health check, at the
  container's own address) and redirects everything else; HSTS is sent.
- **Behind a tunnel or a proxy that holds the certificate**: `SITE_ADDRESS` stays `:8080`, the public name goes in
  `FF_HOSTNAMES`, and the proxy's address range in `TRUSTED_PROXIES`, so its `X-Forwarded-Proto: https` makes the
  cookies `Secure` and the Origin rule compare against `https://`.

## The images

Both Dockerfiles are multi-stage, each base named `image:tag@sha256:…` by the digest of its multi-platform index (one
pin for amd64 and arm64); the digest decides, the tag is what a dependency bot follows. `deploy/BASES.md` lists each
pin with the day it was read and the steps to move one: the digest is read from the registry's manifest API, never by
pulling the tag, the base is pulled by digest, and `tools/verify/ci.sh` runs. A build pulls nothing by tag.

| Image | Stage | What it is |
|---|---|---|
| `Dockerfile.etl` | `uv` | the uv release `uv.lock` is written with |
| | `python-base` | Python 3.12 slim, with tzdata and CA certificates |
| | `py-build` | `/opt/venv` (the locked runtime dependencies, byte-compiled) and `/opt/ff` (the `etl` package, `starter/`, the store's and the refresh's scripts, the demo household's generator, `compose.yaml` and the settings' template) |
| | `test` | the whole tree with the dev group, git and make: what the gate's lint and tests run in |
| | `etl` (default) | the runtime: `/opt/venv`, `/opt/ff` and supercronic (v0.2.51, checked against its SHA-256 for amd64 or arm64; any other architecture stops the build) |
| `Dockerfile.web` | `web-build` | the dashboard built from `app/` on the build machine's own platform (`npm ci`, `tsc --noEmit`, `vite build`) |
| | `web` | Caddy: `/srv/site` the bundle, `/srv/public` the sign-in page's files, the Caddyfile |

The runtime ETL image holds no uv, git, make or curl, no tests, harness tools, documents, config or `.env`. It runs as
whatever uid it is started with: everything in it is world-readable, `HOME=/tmp`, nothing is written inside the
image, and `PYTHONSAFEPATH=1` keeps the working directory off `sys.path` (a `.pth` file puts `/opt/ff` and
`/opt/ff/deploy` there instead), so an `etl/` in the directory a command runs from never shadows the image's. The web
image runs as any uid too, its Caddy state under `/state/caddy`. Neither holds a secret or a household's file.

## The schedule

`household.yaml` `refresh: {hours: [1, 9, 17], minute: 0}` (the default; one to 6 hours, each 0–23, and a minute
0–59; `ff init` writes a random minute) says when the scheduled refresh runs, on the clock of the household's
`timezone` (`docs/configuration.md`). The scheduler's start (`deploy/make_crontab.py --run`) writes supercronic's
crontab to a file in `/tmp`, a `CRON_TZ=<zone>` line and one line per hour, writes the schedule to the mailbox
(`state/refresh/schedule.json`: the zone, the hours and the minute), then becomes supercronic. A `household.yaml` that
cannot give a schedule stops the scheduler with the validator's lines, so it never runs, or reports healthy, on a
schedule the household did not write. A change takes effect when the scheduler restarts. No `TZ` variable decides
anything.

The store cannot read the config, so `schedule.json` is how the zone reaches it: the refresh view reports those times
in that zone, and every daily cap counts the household's days: manual refreshes at least 15 minutes apart and at most
6 a day, ingests without a fetch at most 48 a day, "try again after midnight" at the household's midnight. Until the
scheduler has written the file, the days are UTC's and the view reports no schedule. On a daylight-saving day an hour
that does not exist may lose its run and an hour that repeats may run twice; supercronic waits for a job to end, the
refresh lock keeps two runs apart, and a second run is harmless.

Each refresh (`deploy/refresh.sh`) fetches the bank feed, then runs the AI step, then ingests. The AI step starts no
process at all unless a file is at `secrets/ai/api_key`; then `python -m etl ai run --from-refresh`, under `timeout`
120 s, decides the rest: it sends nothing unless the family switched the step on in the app for the current version of
what is sent, and nothing past its daily caps. Whatever it ends in, the ingest follows (`docs/design/ai.md`).

## Alerts

`deploy/refresh.sh` alerts through `deploy/alert.py`, whose two channels are files in `secrets/alerts/`: a Telegram
bot (`telegram_token` and `telegram_chat_id`, both) and a webhook (`alert_url`, sent a `POST` of `{"text": …}`). Either,
both or none may be set up. A credential is read from its file alone, never an argument or the environment, and a
file that is not a regular file, that others may read (wider than 0600), that is empty or too large is refused with a
line saying why. Every request is HTTPS with the certificate checked, gives up after 10 seconds and follows no
redirect. The message reads `<the household's name>: <message>` (`household.yaml` `name`, else the surname).

What alerts: a refresh whose ingest failed (every such run: the dashboard's data is stale); the second failed refresh
in a row, once per streak (the runs after it are logged only); a bank connection that needs signing in again, once
per set of names; the first failed ingest without a fetch, once until an ingest succeeds; the AI provider refusing the
stored key, once per streak (any other failure of the AI step is logged only, and never fails the refresh). A household with no bank feed (nothing at
`secrets/feed/simplefin_access_url`) skips the fetch: no failure streak, no feed alert, and the status says the run
read the files alone. With no channel set up nothing is sent, and the log says so once. `refresh.sh test-alert` sends
a test.

## `ff` and its commands

`ff` is a POSIX script (under 150 lines) beside `compose.yaml`; every question, check and decision is a Python
command in the ETL image. Before any command it refuses (exit 2, one line): running as root; an install directory
that is `/`, the home directory itself, on a Windows drive (`/mnt/c`), or whose path holds `:`, `,` or a line break or
starts with `-`; a 32-bit ARM machine; no Docker, or Docker not running or not usable; Docker Compose older than v2.20.
It notes, and adapts nothing for, rootless Docker, remapped user ids and an enforcing SELinux, one line each.

The Python commands run as `docker run --rm` of the ETL image as the invoking user, the install mounted at `/ff`,
with no network (except `init` for a bank feed and `ai run`, on Docker's bridge network); the long-running ones go through Docker Compose on the install directory.

| Command | What it does |
|---|---|
| `ff init [--answers FILE] [--force \| --feed]` | The setup conversation (`etl/setup_cli.py`): `household.yaml` (name, people, joint label, zone, the Home buying pages, a random refresh minute), `.env` (this computer only or the home network, the port, the uid and gid), the bank feed (a setup token now or later), the statement files already in `imports/`, a passcode, the starter rule files, `compose.yaml`, then `validate`. Each step is written when answered and kept: a second run finishes where the first stopped, and only `--force` asks again for the two YAML files, keeping each as it was in `<name>.previous` (0600). `--feed` connects a bank feed to a household set up already and does nothing else: each feed account `accounts.yaml` does not name becomes one of the household's accounts no feed id names, or one whose feed ids the feed no longer lists (a bank re-linked under a new id: its old id moves to `simplefin_id_history`, and on a terminal "a new account" is then no default answer) (its `simplefin_id`, and when it has statement files a `simplefin_cutover` the day after the later of the feed's earliest transaction and the files' last row, so no row the files hold is dropped; refused, writing nothing, when a later source's `since` would fall on or after it), a new account, or is skipped; the new lines are added in place, every other line and `household.yaml` left as they are (the file as it was is `accounts.yaml.previous`). The feed credential file is created 0600 before the single-use token is spent and the access URL is synced into it with Ctrl-C held, so a spent token is never lost. The container has the network only to connect a bank feed (`--feed`, yes to the question, or an answers file with a `feed:` block). The answers file may hold the passcode and the token: it must be 0600, outside the install, and is mounted read-only. Exit 0 valid, 1 not valid, 2 refused, 130 stopped at a prompt |
| `ff demo [--force]` | The invented Sample household (`etl/demo_cli.py`) in an empty install: generated, ingested, `.env` and `compose.yaml` written, and a passcode labelled `demo` of four random words printed once. Refuses a directory holding anything but `ff`, `compose.yaml` and `.env`; with `--force`, still never writes over `config/`, `imports/`, `store/` or `state/family/` |
| `ff up` | `python -m etl preflight` (`etl/stack_cli.py`): makes the missing directories, copies `compose.yaml` in, checks `.env` (uid and gid the invoking user's, never 0), validates the config, refuses with no passcode, refuses plain HTTP when `FF_BIND` is a globally routable address or is `0.0.0.0` / `::` on a computer that has one (a private bind on such a computer gets a note), refuses a port another program holds. Once all of that passes, an install with no exports yet, or exports of an older schema than the image's, gets the ingest marker touched, so the refresher builds the data about a minute after it starts. Then `compose up -d --wait` and the address to open. Exit 1 when either fails |
| `ff down` | `compose down`: the services stop; every file stays |
| `ff refresh` | One refresh in the running refresher: the bank feed when one is set up, then an ingest |
| `ff simplefin list-accounts` | The bank feed's accounts in its newest readable snapshot, each with the account that maps it or a suggestion (no network, the install read-only) |
| `ff validate` | Every config problem, one line each, or `config OK` (exit 1 with problems); the install mounted read-only |
| `ff doctor` | The checks below; the install mounted read-only |
| `ff passcode add\|remove\|list` | The passcodes (above) |
| `ff ai preview\|run\|reset` | The AI step (`docs/design/ai.md`): what it would send (no network, no key), one run now (network; its answers show after the next ingest), its stored answers forgotten (no network) |
| `ff import detect\|preview\|map\|banks`, `ff rules preview\|overlaps\|starter-diff` | The offline tools (`docs/design/importers.md`, `docs/design/categorization-and-learned-rules.md`); a file they read is inside the install |
| `ff backup [--include-secrets]`, `ff restore FILE [--force]` | Below |
| `ff upgrade [--to TAG]` | Below |
| `ff build` | `compose build`: the images from a checkout |
| `ff logs [SERVICE]`, `ff ps` | `compose logs` and `compose ps` |

**Settings are `.env`'s alone.** Compose reads `.env` from the install directory: `FF_BIND`, `FF_PORT`,
`FF_PORT_TLS`, `FF_UID`, `FF_GID`, `SITE_ADDRESS`, `FF_HOSTNAMES`, `TRUSTED_PROXIES`, `FF_IMAGE_WEB`, `FF_IMAGE_ETL`
(`.env.example` describes each). `up`, `upgrade` and `build` refuse any of the first eight set in the environment,
where compose would read it over the file. Every line must be one compose and `ff` read alike (`KEY=value`, no quote,
`$`, backtick, `' #'`, control character or trailing space), else the command names the line. No secret is ever a
setting. For development, `FF_IMAGE_ETL` and `FF_IMAGE_WEB` in the environment name other images (`ff` hands compose
the ones it resolved), and `FF_COMPOSE` replaces the whole compose prefix (split on spaces), which a test harness uses
to run its own project.

### Backup and restore

`ff backup` writes `backups/ff-<UTC stamp>.tar.gz`, 0600 from its creation, with a manifest (`ff-backup.json`) of
what is inside and which release wrote it: `config/`, `state/family/`, `state/engine/` (without its log, locks and
heartbeat), `store/`, and `secrets/` only with `--include-secrets`. Never `state/auth/` (everyone signs in again),
`state/caddy/` (remade), `state/refresh/` (the mailbox) or `data/` (an ingest rebuilds it). Only regular files are
archived; a link is left out and named, and so, without `--include-secrets`, is a file under `config/` that looks like
a misplaced secret (an answers file, or one only its owner may read).

`ff restore FILE` (a file inside the install) refuses when `config/` or `store/` holds anything, unless `--force`,
which replaces files of the same name and removes nothing. The whole archive is checked before a byte is written:
each member a regular file or folder under one of the places a backup holds; no absolute path, `..`, control
character, link or device; bounded count and size; no name twice; no file where one of the install's folders belongs;
on disk, no link on a member's path and nothing of the other kind where it goes; and 512 MiB left free. Folders are
made 0700 and files written 0644 under `config/`, 0600 elsewhere, never with the archive's modes. It ends "Now run
./ff up".

### Upgrade

`ff upgrade [--to TAG]`: (1) `upgrade-plan start` backs up the household's files, keeps `.env` as `.env.previous`
and, with `--to`, moves both image settings to that tag (refused when an image is set in the environment); (2)
`compose pull`; (3) preflight with the new image, then `compose up -d --wait`; (4) an ingest in the new refresher;
(5) `doctor`; (6) "upgrade done". Preflight, once every check has passed, brings `compose.yaml` to the new release's:
a file whose sha256 is one a release shipped (`install.SHIPPED_COMPOSE`, which each release extends before it ships)
is replaced and kept as `compose.yaml.previous`; one changed by hand is left as it is, the release's written beside it
as `compose.yaml.new` with one note. When it stops at the pull or at validation, `.env.previous` is put back and
nothing was restarted (`compose.yaml` included): the stack runs the release it ran before. When it stops later
(services not healthy, the first ingest or `doctor` failing), the new images run and it says how to go back:
`cp compose.yaml.previous compose.yaml` when this upgrade replaced it, `cp .env.previous .env`, then `ff up`, then
`ff refresh` (the newer release's exports are rebuilt for the older one), after `ff restore <the backup> --force` if
the new release changed the config or the stores.

### `doctor`

Read-only; one line per check, `ok`, `warn … — <the fix>` or `FAIL … — <the fix>`; exit 1 when any fails
(`etl/doctor_cli.py`): the user (not root); the install directory and each directory of the table (there, the
invoking user's, its mode; a secret or a secrets folder that is a link fails); `.env` (there, uid and gid the invoking user's and not 0, `FF_BIND` an address,
`FF_PORT` a port from 1024, a warning when the network is served over plain HTTP); the config (the validator's lines);
the passcodes file (its mode and how many usable lines, never what they hold); the feed credential (none is fine, but
warns when accounts name feed ids, as after a restore without the secrets; readable by others fails) and the age of
the newest snapshot against the schedule; the alert and AI credentials' modes; statement files no account reads; the
dashboard's data and its age; with feed snapshots stored, a warning when the newest `backups/ff-*.tar.gz` is missing
or more than 30 days old (that history is in no feed any more); free space; and, from what `ff` gathers on the host,
whether the port is free or held by this stack and how the stack's services are. A secret's content is never read
into a line. The last line counts the problems and the warnings.

## The gate's checks of the platform

`tools/verify/ci.sh` builds the two runtime images after the unit gates and checks them as uid 12345 (the `images:`
lines: nothing but the runtime in the ETL image, an ingest of `examples/minimal`, supercronic and the zones, a
crontab per zone, `/srv/public` exactly `deploy/public`, `/healthz` with an address and with a domain, the Caddyfile
adapted and its JSON checked by `tests/test_caddyfile.py`). Its last stage, `stack` (`tools/verify/stack.sh`), brings
the demo household's whole stack up through `ff demo` and `ff up`, runs the security matrix (`stack_matrix.py`: every
spelling of a protected path, the public set, unknown hosts, the Origin rule, request smuggling, the throttle, a
flood beside a household browser, a trusted proxy, sign-out and a removed passcode) and a signed-in browser over every
route that fails on any CSP violation, console error or request elsewhere (`stack_browser.py`), then brings it all
down; around the browser run it also switches the AI step on against a stand-in on the refresher's own loopback and
off again (`tools/verify/stack_ai.py`, `docs/design/ai.md`); its count is the gate line's `stack P/T`. Details: `tools/verify/README.md`.

The workflow's `arm64` job, on GitHub's arm runner beside the `gate` job, builds both runtime images natively, checks
their architecture and that supercronic, the `etl` package and Caddy run, and runs `stack.sh --smoke` (up, `/healthz`,
sign-in, an export with and without a session, down): `tools/verify/README.md` ("The arm64 job").

## Not covered

macOS (Docker Desktop), rootless Docker, SELinux in enforcing mode and WSL are untested: `ff` names each it detects
and changes nothing for it. A domain's automatic certificate is validated by configuration only (Caddy adapts the
Caddyfile for a domain and serves `/healthz` with one); no certificate is ever issued in a test.
