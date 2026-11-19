# Security policy

Family Financials holds a household's financial records, so a security problem in it matters. Thank you for reporting
one privately.

## Supported versions

Security fixes are made in the latest release only. An install moves to it with `./ff upgrade`.

| Version | Supported |
|---|---|
| The latest release (1.x) | Yes |
| Any earlier release | No: upgrade first, then check whether the problem remains |

## How to report a problem

Use the repository's **private vulnerability reporting**: the "Report a vulnerability" button under the repository's
Security tab. Only the maintainers can read the report. Never report a security problem in a public issue, a pull
request or a discussion, and never include real financial data, a passcode, a bank-feed access token or an AI service
key in a report: show the problem with the demo household (`./ff demo`) or with invented values.

Say what an attacker can do, from where (the network, a signed-in browser, a file a household imports), and the steps
that show it. This is a volunteer project with no promised response time, but every report is read; the maintainers
agree a date for disclosure with you before anything is made public, and credit you in the advisory unless you would
rather not be named.

## What is in scope

- **The sign-in** (`deploy/auth.py`, `deploy/Caddyfile`): reaching any page, export or family document without a
  session; getting around the passcode's throttle; a session that survives sign-out; a request from another site that
  the server acts on.
- **The family store** (`deploy/scenario_store.py`): reading or writing a family document without a session, writing
  outside its folder (a link, a path), or a document that, once stored, breaks the dashboard or the ingest of others.
- **The capability split**: a way for the containers a browser reaches (`web` and `store`) to read a bank-feed
  credential or an AI key, to write the ingest's database or exports, or to run a refresh or an ingest beyond asking
  for one within its limits.
- **The offline path**: the ingest or the dashboard making a network call, or an imported file (a CSV, OFX or QFX
  statement) that makes the ingest run code, read files outside the household's folders or write outside its own.
- **The dashboard**: a script injected through imported data (a merchant's name, a description, a note), or an amount
  shown in privacy mode.
- **The images and `ff`**: a container running as root or with more than it needs, or `ff` writing outside the
  install's directory.

## What is out of scope

- A household's own exposure choices: publishing the dashboard to the internet without a domain's HTTPS, a weak
  passcode, sharing the install's folders or backups, running it on a machine others control, or turning the AI step
  on.
- Problems in a bank's export, in the SimpleFIN service or in an AI provider's service, and the base images' operating
  system packages (report those to their projects).
- Findings that need an attacker who already controls the household's computer or its Docker.
