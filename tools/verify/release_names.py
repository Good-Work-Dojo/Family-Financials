#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What a release publishes, for .github/workflows/release.yml: the image names and tags a git tag gives, the
release's notes from CHANGELOG.md, each image's labels and annotations, and the digest a build pushed.

  names REPOSITORY TAG [--prerelease] [--highest-full TAG]
      prints `key=value` lines (the workflow appends them to its step outputs):
        etl, web  ghcr.io/<REPOSITORY lower-cased>-etl and -web: a registry's names are lower case, so
                  Good-Work-Dojo/Family-Financials publishes ghcr.io/good-work-dojo/family-financials-etl, and a
                  repository of another name (a fork) publishes under its own names
        version   TAG without its leading v (v1.0.0 → 1.0.0, v1.0.0-rc.1 → 1.0.0-rc.1): the tag `ff upgrade --to`
                  takes
        tags      the image tags, space-separated: the version, and for a full release also <major>.<minor> and
                  latest, as long as it is at or above --highest-full, the highest full release already published (a
                  patch to an older line, v1.0.5 after v1.2.0, gets its version alone, so latest and 1.0 never move
                  back). A prerelease (--prerelease, which the workflow passes when the release is marked so, or a
                  version with a pre-release part such as -rc.1) is tagged with its own version alone.
  notes CHANGELOG TAG
      prints the CHANGELOG's section for the tag's version (`## [1.0.0-rc.1]`), else for the version it leads to
      (`## [1.0.0]`): the lines under the heading up to the next `## ` heading or the link definitions at the end.
  highest
      reads tags from standard input, one a line, and prints the highest full release among them (no pre-release
      part), or nothing when there is none; a line of another form is passed over.
  labels IMAGE SOURCE VERSION REVISION CREATED
      prints the `docker buildx build` arguments that make the image's labels and its manifest's annotations the
      project's, one argument a line: `--label=<key>=<value>` and `--annotation=manifest:<key>=<value>` for each OCI
      key (title, description, vendor, licenses, source, url, documentation, version, revision, created). IMAGE is
      etl or web; SOURCE the repository's https address; VERSION the release's version (the tag without its v);
      REVISION the tag's commit (40 hex); CREATED its commit time in seconds since the epoch, written as UTC.
  digest METADATA
      prints the pushed image's digest from a `docker buildx build --metadata-file` file (`containerimage.digest`,
      `sha256:` and 64 hex).

TAG is `v<major>.<minor>.<patch>` with an optional pre-release part (`-rc.1`); REPOSITORY is `owner/name`. Anything
else, a CHANGELOG with no such section or an empty one, or a metadata file with no digest, prints what is wrong and
exits 2."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = "ghcr.io"
_TAG = re.compile(r"v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
                  r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?")
_REPOSITORY = re.compile(r"[a-z0-9][a-z0-9-]*/[a-z0-9_][a-z0-9._-]*")
_HEADING = re.compile(r"## \[(?P<version>[^\]]+)\](?:\s.*)?")
_LINK = re.compile(r"\[[^\]]+\]: \S+")
_SOURCE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_REVISION = re.compile(r"[0-9a-f]{40}")
_EPOCH = re.compile(r"0|[1-9][0-9]{0,10}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

# each image's title and description; the other keys are the same for both
IMAGES = {
    "etl": ("Family Financials ETL",
            "Family Financials: the offline ingest, the family store, the refresh worker and the scheduler"),
    "web": ("Family Financials web", "Family Financials: the dashboard and its sign-in page, served by Caddy"),
}
VENDOR = "Family Financials"
LICENCE = "AGPL-3.0-only"


@dataclass(frozen=True)
class Release:
    etl: str
    web: str
    version: str
    tags: tuple[str, ...]


def _number(m: re.Match) -> tuple[int, int, int]:
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def highest(tags: list[str]) -> str | None:
    """the highest full release (no pre-release part) among `tags`, None when there is none"""
    full = [(_number(m), t) for t in tags if (m := _TAG.fullmatch(t.strip())) and m.group("pre") is None]
    return max(full)[1].strip() if full else None


def names(repository: str, tag: str, prerelease: bool = False, highest_full: str | None = None) -> Release:
    """the images, version and tags the release of `tag` from `repository` publishes; `highest_full` is the highest
    full release already published, which <major>.<minor> and latest never move below"""
    m = _TAG.fullmatch(tag)
    if not m:
        raise ValueError(f"the tag '{tag}' is not of the form v1.2.3 or v1.2.3-rc.1")
    repo = repository.lower()
    if not _REPOSITORY.fullmatch(repo) or len(repo) > 200:
        raise ValueError(f"the repository '{repository}' is not of the form owner/name")
    top = None
    if highest_full is not None:
        top = _TAG.fullmatch(highest_full)
        if not top or top.group("pre") is not None:
            raise ValueError(f"the highest full release '{highest_full}' is not of the form v1.2.3")
    version = tag[1:]
    full = not prerelease and m.group("pre") is None and (top is None or _number(m) >= _number(top))
    tags = (version, f"{m.group('major')}.{m.group('minor')}", "latest") if full else (version,)
    base = f"{REGISTRY}/{repo}"
    return Release(f"{base}-etl", f"{base}-web", version, tags)


def notes(changelog: str, tag: str) -> str:
    """the changelog's section for `tag`, or for the version a prerelease of it leads to"""
    m = _TAG.fullmatch(tag)
    if not m:
        raise ValueError(f"the tag '{tag}' is not of the form v1.2.3 or v1.2.3-rc.1")
    version = tag[1:]
    lines = changelog.splitlines()
    starts = {h.group("version"): i for i, line in enumerate(lines) if (h := _HEADING.fullmatch(line))}
    base = f"{m.group('major')}.{m.group('minor')}.{m.group('patch')}"
    at = starts.get(version, starts.get(base))
    if at is None:
        raise ValueError(f"the changelog has no section `## [{version}]` or `## [{base}]`")
    body = []
    for line in lines[at + 1:]:
        if line.startswith("## ") or _LINK.fullmatch(line):
            break
        body.append(line)
    text = "\n".join(body).strip("\n")
    if not text.strip():
        raise ValueError(f"the changelog's section for {version} is empty")
    return text + "\n"


def labels(image: str, source: str, version: str, revision: str, created: str) -> dict[str, str]:
    """the image's OCI keys and values: the project's title, description, vendor and licence, the repository's address
    as its source, url and documentation, the release's version, and the tag's commit and its commit time"""
    if image not in IMAGES:
        raise ValueError(f"the image '{image}' is not one of {', '.join(IMAGES)}")
    if not _SOURCE.fullmatch(source):
        raise ValueError(f"the source '{source}' is not of the form https://host/owner/name")
    if not _TAG.fullmatch(f"v{version}"):
        raise ValueError(f"the version '{version}' is not of the form 1.2.3 or 1.2.3-rc.1")
    if not _REVISION.fullmatch(revision):
        raise ValueError(f"the revision '{revision}' is not a commit's 40 hex digits")
    if not _EPOCH.fullmatch(created):
        raise ValueError(f"the commit time '{created}' is not a count of seconds since the epoch")
    title, description = IMAGES[image]
    when = datetime.fromtimestamp(int(created), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {f"org.opencontainers.image.{k}": v for k, v in (
        ("title", title), ("description", description), ("vendor", VENDOR), ("licenses", LICENCE),
        ("source", source), ("url", source), ("documentation", source), ("version", version),
        ("revision", revision), ("created", when))}


def build_arguments(meta: dict[str, str]) -> list[str]:
    """`docker buildx build` arguments giving the image these labels and its manifest these annotations"""
    return [f"--label={k}={v}" for k, v in meta.items()] + [f"--annotation=manifest:{k}={v}" for k, v in meta.items()]


def digest(metadata: str) -> str:
    """the pushed image's digest from a `docker buildx build --metadata-file` file's text"""
    try:
        found = json.loads(metadata).get("containerimage.digest")
    except (ValueError, AttributeError):
        found = None
    if not isinstance(found, str) or not _DIGEST.fullmatch(found):
        raise ValueError("the build's metadata file gives no image digest of the form sha256:<64 hex>")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) >= 3 and args[0] == "names":
            rest, prerelease, top = args[3:], False, None
            while rest:
                if rest[0] == "--prerelease" and not prerelease:
                    prerelease, rest = True, rest[1:]
                elif rest[0] == "--highest-full" and len(rest) >= 2 and top is None:
                    top, rest = rest[1], rest[2:]
                else:
                    break
            if not rest:
                r = names(args[1], args[2], prerelease=prerelease, highest_full=top)
                print(f"etl={r.etl}\nweb={r.web}\nversion={r.version}\ntags={' '.join(r.tags)}")
                return 0
        if len(args) == 3 and args[0] == "notes":
            print(notes(Path(args[1]).read_text("utf-8"), args[2]), end="")
            return 0
        if args == ["highest"]:
            found = highest(sys.stdin.read().splitlines())
            if found:
                print(found)
            return 0
        if len(args) == 6 and args[0] == "labels":
            print("\n".join(build_arguments(labels(*args[1:]))))
            return 0
        if len(args) == 2 and args[0] == "digest":
            print(digest(Path(args[1]).read_text("utf-8")))
            return 0
    except (ValueError, OSError) as e:
        print(f"release: {e}", file=sys.stderr)
        return 2
    print("usage: release_names.py names REPOSITORY TAG [--prerelease] [--highest-full TAG] | notes CHANGELOG TAG"
          " | highest | labels IMAGE SOURCE VERSION REVISION CREATED | digest METADATA", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
