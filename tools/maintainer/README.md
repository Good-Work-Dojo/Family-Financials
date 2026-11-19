# Maintainer scripts

Two bash scripts for the repository's maintainer, run from a checkout of the repository. Neither is part of the app
or its images. Both talk to GitHub through `gh`, which holds its own token: neither script reads or prints one.

What they need:

- `gh`, signed in (`gh auth status`) as an account with **admin** rights on the repository. `adoption-report.sh`
  needs only read access, though its traffic figures need push access and show as unavailable without it.
- For `post-publish.sh`: also `docker`, `curl` and `python3` (3.12 or later, for the publish gate), and a denylist you
  supply to the publish gate (`tools/publish_gate.py`). The denylist stays on the maintainer's machine; its path is
  given on the command line and never written into the tree.

## post-publish.sh

Run **once, after the repository is made public**. It turns on what a public repository gets for free, publishes the
first release and the demo site, and checks what was published. In order, stopping at the first failure with a line
that says what to do:

1. Checks that the repository is public and that its default branch's head is the expected commit. A private
   repository stops the script here, before anything changes.
2. Turns on Dependabot alerts and security updates, secret scanning and push protection, private vulnerability
   reporting and CodeQL's default setup.
3. Applies a branch ruleset named `default-branch` to the default branch: the checks of `.github/workflows/ci.yml`
   are required (each job's `name:`, else its id, read from the file; each must be reported by GitHub Actions), and
   force-pushes and deletion are refused. A ruleset of that name that says something else is brought back in line,
   so keep other rules in a ruleset of their own.
4. Sets the repository variable `FF_PAGES_ENABLED` to `true` (the demo site's workflow runs only while it is),
   switches Pages on with GitHub Actions as its build, and runs `pages.yml` on the default branch. `pages.yml` also
   runs when a release is published, from the release's tag, and GitHub's `github-pages` environment lets only the
   default branch deploy by default; so the script adds a deployment policy for tags matching `v*` to that
   environment (switching an environment limited to protected branches to chosen branches and tags, the default
   branch among them). An environment that already lets every branch and tag deploy is left as it is.
5. Creates the release at the head commit, its notes the version's section of `CHANGELOG.md`, and waits for
   `release.yml` to build and publish the images.
6. Pulls both images with an empty docker configuration, so without any sign-in, which proves they are public; each
   must hold `linux/amd64` and `linux/arm64`. Then the publish gate scans them with the denylist and prints counts
   and paths only. When the anonymous pull fails, it prints each package's settings page, where its visibility is set.
7. Fetches the demo site: its page and `manifest.json` must answer 200, and the page must carry its
   content-security policy in a `<meta>` tag.

Each step reads the current state first and changes nothing that is already as wanted, so running it again after a
failure is safe: the steps already done are skipped.

**One-time manual steps** (no API call makes them; the script's last line is a reminder):

- Install the CLA Assistant GitHub App on the repository (from `https://cla-assistant.io`, signed in as the
  repository's admin) and point it at a Gist holding `CLA.md`, so it asks the author of each first pull request to sign (`CONTRIBUTING.md`).

| Flag | What |
|---|---|
| `--repo OWNER/NAME` | The repository (default `Good-Work-Dojo/Family-Financials`) |
| `--version vX.Y.Z` | The release (default: the first section of `CHANGELOG.md` headed by a version) |
| `--expect-sha SHA` | The commit the default branch must be at; without it the head is printed |
| `--denylist DIR` | The publish gate's denylist directory; required unless `--skip-gate` |
| `--skip-gate` | Leave out the scan of the published images |
| `--export-names FILE` | The file names the gate refuses in an image (default: the dashboard's export files, read from `app/src/data/types.ts`) |
| `--timeout MINUTES` | How long to wait for each workflow run (default 60) |
| `--dry-run` | Make the reads and print every call that would change something, the pulls, the scan and the fetches, without making them |

```sh
tools/maintainer/post-publish.sh --denylist /path/to/denylist --dry-run
tools/maintainer/post-publish.sh --denylist /path/to/denylist --expect-sha <the commit>
```

Exit 0 when everything is done, 1 when a step failed, 2 on a usage error.

## adoption-report.sh

How the project is being taken up, read from GitHub alone (the app and the demo site carry no analytics): stars,
forks and watchers; the last 14 days' views and clones, total and unique; the top referrers and the most viewed
paths; open and closed issues and pull requests; open and closed discussions when the repository has them. Every call
is a read.

| Flag | What |
|---|---|
| `--repo OWNER/NAME` | The repository (default `Good-Work-Dojo/Family-Financials`) |
| `--json` | One JSON object instead of the text report |

GitHub keeps traffic for 14 days only, so a longer record means running it on a schedule and keeping its `--json`
output.
