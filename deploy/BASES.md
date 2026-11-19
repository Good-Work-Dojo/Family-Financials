# Base images

The two Dockerfiles name every base image by the digest of its multi-platform index, so one pin serves amd64 and arm64
alike and a build never moves with a tag. Each `FROM` names the tag beside the digest (`image:tag@sha256:…`): the digest
decides what is used, and the tag is what a dependency bot follows when it proposes a new digest.

| Dockerfile stage | Image | Tag | Index digest | What the tag pointed at | Read on |
|---|---|---|---|---|---|
| `Dockerfile.etl` `uv` | `ghcr.io/astral-sh/uv` | `0.10.11` | `sha256:3472e43b4e738cf911c99d41bb34331280efad54c73b1def654a6227bb59b2b4` | uv 0.10.11, the version `uv.lock` is written with | 2026-11-17 |
| `Dockerfile.etl` `python-base` | `python` | `3.12-slim` | `sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` | `3.12.16-slim-trixie` | 2026-11-17 |
| `Dockerfile.web` `web-build` | `node` | `24-slim` | `sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54` | Node 24.22.3 on `bookworm-slim` | 2026-11-17 |
| `Dockerfile.web` `web` | `caddy` | `2` | `sha256:4851e54a46b206517c574821bb5cc67f170d2156041e9fbc4c9193b45e84a75b` | Caddy 2.11.9 on Alpine | 2026-11-17 |

The scheduler's cron, supercronic, is a release binary rather than an image: `Dockerfile.etl` fetches v0.2.51 for the
architecture being built and checks its SHA-256, and any architecture but amd64 and arm64 stops the build.

| Binary | SHA-256 | SHA-1 the release publishes |
|---|---|---|
| `supercronic-linux-amd64` | `cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123` | `1692066e350a88ff4d7d6474fe3c7a6c41ec5e89` |
| `supercronic-linux-arm64` | `4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94` | `9d4c2000493215a1898502b2fc2dada3e8ea8c7a` |

## Moving a pin

Read the tag's current index digest from the registry, without pulling the tag: ask the registry for an anonymous
token (Docker Hub: `https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/<name>:pull`;
GHCR: `https://ghcr.io/token?service=ghcr.io&scope=repository:<owner>/<name>:pull`), then send `HEAD
/v2/<repository>/manifests/<tag>` to the registry (`registry-1.docker.io`, `ghcr.io`) with that token and an `Accept`
header naming the OCI image index and the Docker manifest list (`application/vnd.oci.image.index.v1+json,
application/vnd.docker.distribution.manifest.list.v2+json`); the `Docker-Content-Digest` header is the digest. Check
the index lists `linux/amd64` and `linux/arm64`, put the digest after the tag in the `FROM` line and in the table
above with the tag and the day it was read, pull the base by that digest (`docker pull <image>@sha256:…`), and run
`tools/verify/ci.sh`.
A new supercronic release changes the URL and both checksums together: take the SHA-256 GitHub lists for each release
asset, and check it against a download of the file and against the SHA-1 the release notes publish.
