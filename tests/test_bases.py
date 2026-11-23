# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/BASES.md is held to the Dockerfiles: every base image a `FROM` pins (`image:tag@sha256:…`) has the table row
of its Dockerfile and stage with the same image, tag and index digest, and the table names no pin the Dockerfiles do
not use, so a digest a dependency bot moves cannot leave the record of what was read, and when, behind."""
from __future__ import annotations

import re

from conftest import ROOT

DEPLOY = ROOT / "deploy"
_FROM = re.compile(r"^FROM (?:--platform=\S+ )?(?P<image>[^\s:@]+(?:/[^\s:@]+)*):(?P<tag>[^\s@]+)"
                   r"@(?P<digest>sha256:[0-9a-f]{64}) AS (?P<stage>\S+)$", re.M)
_ROW = re.compile(r"^\| `(?P<file>Dockerfile\.\w+)` `(?P<stage>[^`]+)` \| `(?P<image>[^`]+)` \| `(?P<tag>[^`]+)` \| "
                  r"`(?P<digest>sha256:[0-9a-f]{64})` \|", re.M)


def _pins() -> set[tuple[str, str, str, str, str]]:
    found = set()
    for dockerfile in sorted(DEPLOY.glob("Dockerfile.*")):
        for m in _FROM.finditer(dockerfile.read_text("utf-8")):
            found.add((dockerfile.name, m["stage"], m["image"], m["tag"], m["digest"]))
    return found


def _rows() -> set[tuple[str, str, str, str, str]]:
    text = (DEPLOY / "BASES.md").read_text("utf-8")
    return {(m["file"], m["stage"], m["image"], m["tag"], m["digest"]) for m in _ROW.finditer(text)}


def test_every_pinned_base_image_is_in_the_table_with_its_tag_and_digest():
    pins = _pins()
    assert len(pins) >= 4, "both Dockerfiles pin their bases by digest"
    assert sorted(pins - _rows()) == []


def test_the_table_names_no_pin_the_dockerfiles_do_not_use():
    assert sorted(_rows() - _pins()) == []


def test_every_from_line_names_a_stage_built_in_the_file_or_a_pinned_base():
    for dockerfile in sorted(DEPLOY.glob("Dockerfile.*")):
        text = dockerfile.read_text("utf-8")
        stages = set(re.findall(r"^FROM .* AS (\S+)$", text, re.M))
        for line in re.findall(r"^FROM .*$", text, re.M):
            assert _FROM.fullmatch(line) or line.split()[-3] in stages, (dockerfile.name, line)
