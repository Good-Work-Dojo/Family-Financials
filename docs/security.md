# Security

Family Financials holds a household's financial records: every transaction, balance and account, and the credentials
that fetch them. This page says what protects them, what you choose when you decide where the dashboard listens, and
what is known not to be covered. The mechanics, with every number, are in
[design/platform.md](design/platform.md). To report a problem privately, see [SECURITY.md](../SECURITY.md).

## What it protects, and from whom

- **From the internet and other devices**: nobody reads a page, an export or a family document without signing in,
  and nobody can make a signed-in browser change something from another site.
- **From the parts of itself a browser reaches**: if the web server or the family store were taken over, the bank
  feed's access, the AI key, the alert credentials, the statement files and the rebuild's own files are still out of
  their reach.
- **From other users of the same computer**: every folder of the install is readable by its owner alone (mode 0700).
- **From the program itself sending your data out**: the rebuild and the dashboard make no outside call; only the
  bank feed's fetch and, when the family turns it on, AI categorization use the network.

It does not protect against someone who already controls the computer it runs on (an administrator, or malware
running as you): they can read the install directory directly.

## The sign-in

- **Passcodes**, at least 8 characters (10 when all digits), set by `./ff init`, `./ff demo` or
  `./ff passcode add NAME`, are stored only as scrypt hashes in `secrets/web/passcodes`. Nothing prints a passcode or
  a hash. Removing or changing one (`./ff passcode remove NAME`) signs out every browser it signed in.
- **Sessions** are random tokens in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` over HTTPS); the store keeps only
  their hashes. A session ends after 30 days unused, 400 days after sign-in, or at sign-out, which also tells the
  browser to clear its cached copy of the data.
- **Guessing is throttled**: from one address, 5 wrong guesses lock it for a minute, then 2, 4, 8 and 15 minutes; many
  addresses at once are slowed together. A browser that has signed in before is never locked out by someone else's
  guessing. A wrong passcode and an unknown one look the same, and take the same time.
- **Another site cannot act for you**: every change must come from the dashboard's own address (an `Origin` check),
  and the dashboard answers only to an IP address, `localhost`, a `.local` name or the names it is configured for,
  so a DNS rebinding trick fails.

## What the browser is allowed to do

Every answer carries a strict Content Security Policy: scripts, styles, images and fonts from the dashboard itself
only, no inline script, no frames, no connection anywhere else. It makes two exceptions, both for the charts: inline
`style` attributes (`style-src-attr 'unsafe-inline'`), which the charts library and the page's components set to size
and place what they draw, and `data:` images (`img-src 'self' data:`), which the charts draw inline. Neither runs code:
a style attribute cannot load a script, and an image is never executed. With the policy, a vendor name or a
description from a bank file can never run as code, and the page can never send data elsewhere. Text from statement
files is shown escaped.

## The four services

| Service | A browser reaches it | Holds a credential | Writes the household's data |
|---|---|---|---|
| `web` (the web server) | yes | none | no |
| `store` (sign-in and the family's documents) | through `web` only | the passcodes' hashes | the family's documents only |
| `scheduler` (the timed refresh) | no | the bank feed's access, the AI key, the alerts' | yes |
| `refresher` (a refresh on request) | no | the same | yes |

The store has no route out of its private network. It can only *ask* for a refresh, by leaving a request the
refresher reads; it never runs one. Every service runs as your user, with every Linux capability dropped and no way to
gain one; the web server and the store run on a read-only file system. Shared folders are read and written so that a
planted link or special file is refused rather than followed.

## Where the dashboard listens

You choose in `.env` (the README's "Security, and where the dashboard listens" has the settings for each):

- **This computer only** (the default): nothing on the network can reach it.
- **The home network over plain HTTP**: convenient, but **the passcode and the session cookie cross the network
  unencrypted**, readable by anyone who can watch it (an open Wi-Fi, a compromised device). `./ff doctor` warns of
  it. `./ff up` refuses plain HTTP on an address the internet can reach.
- **A domain of your own with automatic HTTPS**: the web server gets a certificate and redirects plain HTTP to HTTPS.
- **Behind a tunnel or a reverse proxy** that holds the certificate: list the proxy's address in `TRUSTED_PROXIES` so
  its `X-Forwarded-Proto: https` is believed, and the public name in `FF_HOSTNAMES`. Only list a proxy you run: a
  trusted proxy decides which client address the throttle sees.

Exposing the dashboard to the internet at all is your decision. A tunnel with its own login in front (an identity
provider, a VPN) adds a second lock before anyone reaches the passcode page.

## Credentials

Each credential is a file of its own under `secrets/`, readable by you alone, mounted only into the services that use
it, and never an environment variable or a command-line argument:

- `secrets/web/passcodes`: the passcodes' hashes (the store, read-only).
- `secrets/feed/simplefin_access_url`: the bank feed's access (the two workers). It reads your accounts; it cannot
  move money. Revoke it in SimpleFIN Bridge if it leaks.
- `secrets/ai/api_key`: the AI service's key, when you store one.
- `secrets/alerts/`: a Telegram bot's token and chat, or a webhook address, when you set alerts up.

`./ff doctor` fails a credential file others can read. An answers file for `./ff init --answers` may hold your
passcode and a setup token: it is refused unless only you can read it and it lies outside the install; delete it
after use. `./ff backup` leaves `secrets/` out unless you add `--include-secrets`; such an archive is as sensitive as
the install.

## The images

Both images are built from base images pinned by digest, run as any user, and carry no secret and no household file;
the runtime ETL image holds none of the build tools (no uv, git, make or curl) and no tests. Every runtime
dependency's licence is checked, and every workflow action is pinned to a commit. Releases are built on GitHub's own
runners.

## Known limits

- **Plain HTTP on the home network** exposes the passcode and the session (above).
- **`./ff import` and `./ff rules` mount the install read-write**, though only `import map` writes (one preset file);
  they run with no network.
- **A link planted at an export's name is served to a signed-in browser.** The rebuild writes each export as a new
  file renamed over the name, which replaces such a link, but the web server's file server cannot refuse one, so a
  link placed in `data/exports/` by someone who can already write there is followed until the next rebuild.
- **The web server's redirect from HTTP to HTTPS** (with a domain) carries no Content Security Policy; it carries no
  content either.
- **The throttle's memory** lives in the store: restarting it forgets the counts.
- **Rootless Docker and remapped user ids** cannot read the install's private folders as the services' user; `ff`
  says so and changes nothing. SELinux in enforcing mode may refuse the folders until they are labelled for
  containers. Neither is tested.
- **The operating-system layers of the base images** (Debian in the ETL image, Alpine in the web server's) carry
  their own packages, outside the licence and dependency checks; a new release moves the base images' pins, and
  upgrading picks up their updates.
- **AI categorization**, when the family turns it on, sends vendor names and category names to the service chosen;
  what that service keeps is set by its terms ([ai-categorization.md](ai-categorization.md)).
