# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The runtime licence audit (licences.py): which packages are the two runtime closures, which licences pass, that a
GPL, an LGPL, an unknown or an unreadable licence fails naming its package, that THIRD_PARTY_NOTICES.md's generated
block is held to the closures, that THIRD_PARTY_LICENSES is held to the npm packages' licence files (a package
with none fails), and that the Go modules compiled into the images' programs, read from each binary's build info, each
have a kept licence text (one with none fails naming it, a text no program needs is stale) rendered in a stable order.
Runs on fixtures in temporary trees; the docker calls are captured, never made, and nothing is fetched."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import licences as lic  # noqa: E402  (tools/verify is not a package)


def lockfile(**packages: dict) -> dict:
    return {"name": "app", "lockfileVersion": 3, "packages": {"": {"name": "app"}, **packages}}


def dist(name: str, version: str = "1.0", *, expression: str | None = None, field: str | None = None,
         classifiers: tuple[str, ...] = ()) -> dict:
    return {"name": name, "version": version, "license_expression": expression, "license": field,
            "classifiers": list(classifiers)}


ALLOWED_NPM = {"node_modules/left-pad": {"version": "1.3.0", "license": "MIT"},
               "node_modules/charts": {"version": "5.0.0", "license": "Apache-2.0"}}
ALLOWED_PY = [dist("yamlish", field="MIT"), dist("certs", field="MPL-2.0"),
              dist("either", field="MIT OR Apache-2.0"), dist("modern", expression="BSD-3-Clause")]


def tree(tmp_path: Path, lock: dict, notices: str | None = None) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package-lock.json").write_text(json.dumps(lock))
    if notices is not None:
        (tmp_path / "THIRD_PARTY_NOTICES.md").write_text(notices)
    return tmp_path


def every_text(root: Path) -> dict[str, list[list[str]]]:
    """a licence file for each runtime npm package of the tree's lockfile, as the web build image would give them"""
    lock = json.loads((root / "app" / "package-lock.json").read_text())
    return {p.path: [["LICENSE", f"The {p.name} licence.\n"]] for p in lic.npm_closure(lock)}


def checked(root: Path, dists: list[dict], **kwargs) -> tuple[int, list[str]]:
    return lic.check(root, dists, every_text(root), **kwargs)


def current(tmp_path: Path, lock: dict, dists: list[dict]) -> Path:
    """a tree whose notices hold the block the closures give, and whose THIRD_PARTY_LICENSES the texts"""
    root = tree(tmp_path, lock, f"# Notices\n\n{lic.BEGIN}\n{lic.END}\n\nhand-written\n")
    checked(root, dists, write=True)
    return root


def test_allowed_licences_and_their_compounds_pass_and_the_block_is_current(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    rc, lines = checked(root, ALLOWED_PY)
    assert rc == 0, lines
    assert lines == ["licences: npm 2 runtime packages, python 4 distributions, go 0 modules in 0 programs; 0 refused;"
                     " the notices' block is current; the licence texts are current"]
    text = (root / "THIRD_PARTY_NOTICES.md").read_text()
    assert "| left-pad | 1.3.0 | MIT |" in text and "| either | 1.0 | MIT OR Apache-2.0 |" in text
    assert text.startswith("# Notices\n\n") and text.endswith("\n\nhand-written\n"), "the hand-written parts stay"


def test_a_gpl_runtime_package_fails_naming_it_and_a_dev_only_one_is_not_in_the_closure(tmp_path):
    lock = lockfile(**ALLOWED_NPM,
                    **{"node_modules/copyleft-chart": {"version": "2.0.0", "license": "GPL-3.0-only"},
                       "node_modules/build-tool": {"version": "9.9.9", "license": "GPL-2.0", "dev": True},
                       "node_modules/maybe-tool": {"version": "1.1.1", "license": "AGPL-3.0", "devOptional": True}})
    root = current(tmp_path, lock, ALLOWED_PY)
    rc, lines = checked(root, ALLOWED_PY)
    assert rc == 1
    assert lines[0] == ("licences: npm copyleft-chart 2.0.0: GPL-3.0-only names GPL-3.0-only, a copyleft licence the "
                        "project does not take at run time")
    assert not any("build-tool" in line or "maybe-tool" in line for line in lines)
    assert lines[-1].endswith("1 refused; the notices' block is current; the licence texts are current")


def test_an_unknown_python_licence_fails_naming_the_distribution(tmp_path):
    dists = [*ALLOWED_PY, dist("mystery", "0.1"), dist("versionless", classifiers=(
        "License :: OSI Approved :: Apache Software License",))]
    root = current(tmp_path, lockfile(**ALLOWED_NPM), dists)
    rc, lines = checked(root, dists)
    assert rc == 1
    assert "licences: python mystery 0.1: no licence the check can read: read the package and state it, or drop the " \
        "dependency" in lines
    assert any(line.startswith("licences: python versionless 1.0: no licence") for line in lines)
    assert "| mystery | 0.1 | unknown |" in (root / "THIRD_PARTY_NOTICES.md").read_text()


@pytest.mark.parametrize("licence, refused", [
    ("MIT", None), ("0BSD", None), ("CC0-1.0", None), ("PSF-2.0", None), ("Unlicense", None),
    ("MIT AND (Apache-2.0 OR BSD-3-Clause)", None), ("(MIT)", None),
    ("LGPL-2.1-or-later", "copyleft"), ("AGPL-3.0-only", "copyleft"), ("SSPL-1.0", "copyleft"),
    ("MIT OR GPL-3.0-or-later", "copyleft"), ("BlueOak-1.0.0", "allow-list"), ("CC-BY-4.0", "allow-list"),
    ("Apache-2.0 WITH LLVM-exception", "no SPDX expression"), ("MIT OR", "no SPDX expression"),
    ("(MIT", "no SPDX expression"), ("mit and apache", "no SPDX expression"), (None, "no licence"), ("", "no licence")])
def test_each_licence_is_allowed_or_refused_as_the_list_says(licence, refused):
    why = lic.verdict(lic.Package("npm", "pkg", "1.0.0", licence))
    if refused is None:
        assert why is None, why
    else:
        assert why is not None and refused in why


def test_a_python_licence_comes_from_its_expression_then_its_field_then_one_versioned_classifier():
    mit = "License :: OSI Approved :: MIT License"
    assert lic.python_licence(dist("a", expression="Apache-2.0", field="MIT")) == "Apache-2.0"
    assert lic.python_licence(dist("b", field="MIT", classifiers=(mit,))) == "MIT"
    assert lic.python_licence(dist("c", field="Permission is hereby granted\nto any person", classifiers=(mit,))) == "MIT"
    assert lic.python_licence(dist("d", field="BSD", classifiers=(mit,))) == "MIT", "a field that names no SPDX id"
    assert lic.python_licence(dist("e", field="GPL")) == "GPL", "kept, so that it is refused by name"
    assert lic.python_licence(dist("f", classifiers=(mit, "License :: OSI Approved :: Apache Software License"))) is None
    assert lic.python_licence(dist("g")) is None


def test_the_block_is_held_to_the_closures(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    moved = [*ALLOWED_PY[:-1], dist("modern", "2.0", expression="BSD-3-Clause")]
    rc, lines = checked(root, moved)
    assert rc == 1 and any("generated block is not the closures as they are" in line for line in lines)
    rc, lines = checked(root, moved, write=True)
    assert rc == 0 and "| modern | 2.0 | BSD-3-Clause |" in (root / "THIRD_PARTY_NOTICES.md").read_text()
    for text in ("no markers\n", f"{lic.BEGIN}\n", f"{lic.BEGIN}\n{lic.END}\n{lic.BEGIN}\n{lic.END}\n"):
        (root / "THIRD_PARTY_NOTICES.md").write_text(text)
        rc, lines = checked(root, moved, write=True)
        assert rc == 1 and any("has no generated block" in line for line in lines), text


def test_the_lockfile_must_be_one_the_reader_knows(tmp_path):
    with pytest.raises(ValueError, match="version 2 or 3"):
        lic.npm_closure({"lockfileVersion": 1, "dependencies": {}})


def test_the_repositorys_own_dashboard_closure_is_allowed():
    lock = json.loads((ROOT / "app" / "package-lock.json").read_text("utf-8"))
    pkgs = lic.npm_closure(lock)
    assert {"react", "react-dom", "echarts", "zrender", "dayjs"} <= {p.name for p in pkgs}
    assert not {"vite", "vitest", "typescript", "eslint"} & {p.name for p in pkgs}, "build tools are not runtime"
    assert [p for p in pkgs if lic.verdict(p)] == []


def test_collect_lists_this_interpreters_distributions_with_their_licence_metadata():
    found = {d["name"].lower(): d for d in lic.collect()}
    assert "pyyaml" in found and found["pyyaml"]["version"]
    assert set(found["pyyaml"]) == {"name", "version", "license_expression", "license", "classifiers"}


def test_the_image_is_read_with_no_network_as_an_unprivileged_user_under_the_label(monkeypatch):
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([dist("x", field="MIT")]), stderr="")

    monkeypatch.setattr(lic.subprocess, "run", run)
    assert lic.read_image("sha256:" + "0" * 64, "team=sample") == [dist("x", field="MIT")]
    (cmd,) = seen
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[cmd.index("--label") + 1] == "team=sample" and cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--user") + 1] == "12345:12345"
    mount = cmd[cmd.index("-v") + 1]
    assert mount.endswith(":ro") and mount.startswith(str(HERE / "licences.py") + ":")
    assert cmd[-3:] == ["python", mount.split(":")[1], "--collect"]


def test_a_runtime_npm_package_with_no_licence_file_fails_naming_it(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    texts = every_text(root)
    texts["node_modules/charts"] = []
    rc, lines = lic.check(root, ALLOWED_PY, texts)
    assert rc == 1
    assert lines[0] == ("licences: npm charts 5.0.0: its package holds no licence file, so its licence text cannot ship "
                        "with it: find the text and add it, or drop the dependency")
    assert lines[-1].endswith("1 refused; the notices' block is current; the licence texts are stale")


def test_the_licence_texts_file_is_held_to_the_packages_files_and_written_in_order(tmp_path):
    lock = lockfile(**ALLOWED_NPM)
    root = current(tmp_path, lock, ALLOWED_PY)
    texts = {"node_modules/left-pad": [["LICENSE", "\nMIT License  \r\n\r\nCopyright (c) Sample Author\r\n\n\n"]],
             "node_modules/charts": [["NOTICE", "Charts\nCopyright Sample Foundation\n"],
                                     ["LICENSE", "Apache License\n"], ["licenses/LICENSE-d3", "d3's terms\n"]]}
    rc, lines = lic.check(root, ALLOWED_PY, texts)
    assert rc == 1 and any("THIRD_PARTY_LICENSES is not the runtime packages' licence texts" in line for line in lines)
    assert lines[-1].endswith("0 refused; the notices' block is current; the licence texts are stale")
    rc, lines = lic.check(root, ALLOWED_PY, texts, write=True)
    assert rc == 0 and "licences: THIRD_PARTY_LICENSES rewritten" in lines
    written = (root / "THIRD_PARTY_LICENSES").read_text()
    assert written.startswith(lic.TEXTS_HEAD + "\n" + lic.RULE + "\ncharts 5.0.0 (Apache-2.0): LICENSE\n" + lic.RULE)
    order = [line for line in written.splitlines() if line.startswith(("charts ", "left-pad "))]
    assert order == ["charts 5.0.0 (Apache-2.0): LICENSE", "charts 5.0.0 (Apache-2.0): NOTICE",
                     "charts 5.0.0 (Apache-2.0): licenses/LICENSE-d3", "left-pad 1.3.0 (MIT): LICENSE"]
    assert written.endswith("left-pad 1.3.0 (MIT): LICENSE\n" + lic.RULE +
                            "\n\nMIT License\n\nCopyright (c) Sample Author\n"), "line endings and blank ends normalised"
    assert lic.check(root, ALLOWED_PY, texts)[0] == 0
    (root / "THIRD_PARTY_LICENSES").write_text(written.replace("Sample Author", "Someone Else"))
    assert lic.check(root, ALLOWED_PY, texts)[0] == 1, "a hand edit is stale"
    (root / "THIRD_PARTY_LICENSES").unlink()
    assert lic.check(root, ALLOWED_PY, texts)[0] == 1, "a missing file is stale"


def test_the_licence_files_are_read_in_the_web_build_image_with_no_network_as_an_unprivileged_user(monkeypatch):
    seen = []
    answer = {"node_modules/left-pad": [["LICENSE", "MIT"]]}

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(lic.subprocess, "run", run)
    pkgs = lic.npm_closure(lockfile(**ALLOWED_NPM))
    assert lic.read_npm_texts("sha256:" + "f" * 64, "team=sample", pkgs) == answer
    (cmd,) = seen
    assert cmd[:3] == ["docker", "run", "--rm"] and "-v" not in cmd
    assert cmd[cmd.index("--label") + 1] == "team=sample" and cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--user") + 1] == "12345:12345" and cmd[cmd.index("-w") + 1] == lic.APP_IN_WEB_BUILD
    i = cmd.index("node")
    assert cmd[i - 1] == "sha256:" + "f" * 64 and cmd[i + 1:i + 3] == ["-e", lic.NPM_READ]
    assert cmd[i + 3:] == ["node_modules/charts", "node_modules/left-pad"]


@pytest.mark.parametrize("path", ["node_modules/../../etc", "elsewhere/pkg", "node_modules/a b"])
def test_a_package_folder_of_another_form_is_never_read(monkeypatch, path):
    monkeypatch.setattr(lic.subprocess, "run", lambda *a, **k: pytest.fail("docker was called"))
    with pytest.raises(RuntimeError, match="another form"):
        lic.read_npm_texts("img", "team=sample", [lic.Package("npm", "x", "1.0", "MIT", path)])


def test_the_reader_takes_licence_notice_and_copying_files_and_the_licenses_folder():
    name = re.search(r"const NAME = /(.+)/i;", lic.NPM_READ).group(1)
    for f in ("LICENSE", "license.md", "LICENCE.txt", "LICENSE-MIT", "NOTICE", "COPYING"):
        assert re.fullmatch(name, f, re.I), f
    for f in ("README.md", "licenses", "package.json", "unlicensed.js"):
        assert not re.fullmatch(name, f, re.I), f
    assert 'path.join(pkg, "licenses"), "licenses/"' in lic.NPM_READ


def test_the_repositorys_licence_texts_name_every_runtime_npm_package():
    lock = json.loads((ROOT / "app" / "package-lock.json").read_text("utf-8"))
    text = (ROOT / "THIRD_PARTY_LICENSES").read_text("utf-8")
    assert text.startswith(lic.TEXTS_HEAD)
    for p in lic.npm_closure(lock):
        assert re.search(rf"^{re.escape(p.name)} {re.escape(p.version)} \(", text, re.M), p.name


def go_binary(go: str, *lines: str) -> bytes:
    """the bytes of a Go binary as far as its build info goes: some code, the 32-byte header (the magic, the pointer
    size, the inline-strings flag), the Go version and the module text between its two 16-byte sentinels"""
    def varint(n: int) -> bytes:
        out = b""
        while n >= 0x80:
            out += bytes([n & 0x7F | 0x80])
            n >>= 7
        return out + bytes([n])
    mod = b"\x30" * 16 + "".join(f"{line}\n" for line in lines).encode() + b"\xf9" * 16
    return (b"\x7fELF" + b"\0" * 60 + lic.BUILDINFO_MAGIC + bytes([8, 2]) + b"\0" * 16
            + varint(len(go)) + go.encode() + varint(len(mod)) + mod + b"\0" * 32)


CADDY = go_binary("go1.27.3", "path\tcaddy", "mod\tcaddy\t(devel)\t", "dep\texample.net/quill\tv1.2.0\th1:x=",
                  "dep\texample.net/old\tv0.1.0\th1:y=", "=>\texample.net/new\tv0.2.0\th1:z=",
                  "build\t-trimpath=true")
CRON = go_binary("go1.27.3", "path\texample.org/cron", "mod\texample.org/cron\tv0.3.0\t",
                 "dep\texample.net/quill\tv1.2.0\th1:x=")
APACHE = "Apache License\nVersion 2.0, January 2004\n"
GO_TEXTS = {"std@go1.27.3": {"LICENSE": "Copyright The Go Authors.\n", "PATENTS": "Additional IP Rights Grant\n"},
            "example.net/quill@v1.2.0": {"LICENSE": APACHE, "NOTICE": "Quill\nCopyright Sample Quill Authors\n"},
            "example.net/new@v0.2.0": {"LICENSE.md": "MIT License\n\nCopyright (c) Sample New Author\n"},
            "example.org/cron@v0.3.0": {"LICENSE": APACHE}}


def test_the_build_info_names_the_go_version_the_main_module_and_the_dependencies():
    assert lic.go_build_info(CADDY) == ("go1.27.3", [("example.net/quill", "v1.2.0"), ("example.net/new", "v0.2.0")])
    assert lic.go_build_info(CRON) == ("go1.27.3", [("example.org/cron", "v0.3.0"), ("example.net/quill", "v1.2.0")])
    with pytest.raises(ValueError, match="no Go build info"):
        lic.go_build_info(b"\x7fELF" + b"\0" * 400)
    with pytest.raises(ValueError, match="supercronic: no Go build info"):
        lic.go_modules({"supercronic": b"#!/bin/sh\n"})


def test_each_module_is_listed_once_with_every_program_built_from_it():
    mods = lic.go_modules({"supercronic": CRON, "caddy": CADDY})
    assert [(m.key, m.programs) for m in mods] == [
        ("example.net/new@v0.2.0", ("caddy",)), ("example.net/quill@v1.2.0", ("caddy", "supercronic")),
        ("example.org/cron@v0.3.0", ("supercronic",)), ("std@go1.27.3", ("caddy", "supercronic"))]


def keep(root: Path, texts: dict[str, dict[str, str]]) -> None:
    for key, files in texts.items():
        for name, text in files.items():
            (root / lic.GO_DIR / key).mkdir(parents=True, exist_ok=True)
            (root / lic.GO_DIR / key / name).write_text(text)


def no_fetch(module):
    raise AssertionError(f"fetched {module.key}")


def test_a_go_module_with_no_kept_text_fails_naming_it_and_its_programs(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    keep(root, {k: v for k, v in GO_TEXTS.items() if k != "example.net/quill@v1.2.0"})
    rc, lines = checked(root, ALLOWED_PY, binaries={"caddy": CADDY, "supercronic": CRON}, fetch=no_fetch)
    assert rc == 1
    assert ("licences: go example.net/quill v1.2.0 (in caddy, supercronic): deploy/licences/go/example.net/"
            "quill@v1.2.0: no licence text is kept for it: run tools/verify/licences.py --write and read the change"
            ) in lines
    assert lines[-1].startswith("licences: npm 2 runtime packages, python 4 distributions, go 4 modules in 2 programs;"
                                " 1 refused;")


def test_write_fetches_what_is_missing_drops_what_no_program_needs_and_then_the_check_passes(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    keep(root, {"example.net/gone@v9.9.9": {"LICENSE": "old\n"}, "std@go1.27.3": GO_TEXTS["std@go1.27.3"]})
    binaries = {"caddy": CADDY, "supercronic": CRON}
    rc, lines = checked(root, ALLOWED_PY, binaries=binaries, fetch=no_fetch)
    assert rc == 1 and any("example.net/gone@v9.9.9 is the text of a module no program" in line for line in lines)
    fetched = []

    def fetch(module):
        fetched.append(module.key)
        return GO_TEXTS[module.key]
    rc, lines = checked(root, ALLOWED_PY, binaries=binaries, fetch=fetch, write=True)
    assert rc == 0, lines
    assert sorted(fetched) == ["example.net/new@v0.2.0", "example.net/quill@v1.2.0", "example.org/cron@v0.3.0"]
    assert not (root / lic.GO_DIR / "example.net" / "gone@v9.9.9").exists()
    assert lic.vendored(root) == GO_TEXTS
    assert checked(root, ALLOWED_PY, binaries=binaries, fetch=no_fetch) == (0, [
        "licences: npm 2 runtime packages, python 4 distributions, go 4 modules in 2 programs; 0 refused; the "
        "notices' block is current; the licence texts are current"])


def test_a_module_that_ships_no_licence_file_fails_even_when_written(tmp_path):
    root = current(tmp_path, lockfile(**ALLOWED_NPM), ALLOWED_PY)
    rc, lines = checked(root, ALLOWED_PY, binaries={"supercronic": CRON}, write=True,
                        fetch=lambda m: {} if m.key == "example.org/cron@v0.3.0" else GO_TEXTS[m.key])
    assert rc == 1
    assert ("licences: go example.org/cron v0.3.0 (in supercronic): deploy/licences/go/example.org/cron@v0.3.0: the "
            "module ships no licence file: find its text and add it there, or drop the dependency") in lines


def test_the_go_texts_follow_the_npm_ones_in_a_stable_order_each_shared_text_once(tmp_path):
    npm = lic.npm_closure(lockfile(**ALLOWED_NPM))
    texts = {p.path: [["LICENSE", f"The {p.name} licence.\n"]] for p in npm}
    one = lic.texts_file(npm, texts, lic.go_modules({"caddy": CADDY, "supercronic": CRON}), GO_TEXTS)
    shuffled = dict(reversed(list(GO_TEXTS.items())))
    two = lic.texts_file(npm, texts, lic.go_modules({"supercronic": CRON, "caddy": CADDY}), shuffled)
    assert one == two
    assert one.index("The left-pad licence.") < one.index(lic.GO_HEAD)
    heads = [line for line in one.split(lic.GO_HEAD)[1].splitlines() if " (in " in line]
    assert heads == ["the Go standard library go1.27.3 (in caddy, supercronic): LICENSE",
                     "the Go standard library go1.27.3 (in caddy, supercronic): PATENTS",
                     "example.net/new v0.2.0 (in caddy): LICENSE.md",
                     "example.net/quill v1.2.0 (in caddy, supercronic): LICENSE",
                     "example.org/cron v0.3.0 (in supercronic): LICENSE",
                     "example.net/quill v1.2.0 (in caddy, supercronic): NOTICE"]
    assert one.count(APACHE.splitlines()[0]) == 1, "a text two modules ship alike is printed once"
    section = one.split(lic.RULE + "\nexample.net/quill v1.2.0 (in caddy, supercronic): LICENSE\n")[1]
    assert section.startswith("example.org/cron v0.3.0 (in supercronic): LICENSE\n" + lic.RULE + "\n\nApache License")


def test_a_binary_is_copied_out_of_its_image_with_no_network_as_an_unprivileged_user(monkeypatch):
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=CRON, stderr=b"")

    monkeypatch.setattr(lic.subprocess, "run", run)
    assert lic.read_binary("sha256:" + "a" * 64, "/usr/local/bin/supercronic", "team=sample") == CRON
    (cmd,) = seen
    assert cmd[:3] == ["docker", "run", "--rm"] and "-v" not in cmd
    assert cmd[cmd.index("--label") + 1] == "team=sample" and cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--user") + 1] == "12345:12345"
    assert cmd[-4:] == ["--entrypoint", "cat", "sha256:" + "a" * 64, "/usr/local/bin/supercronic"]


def test_the_repositorys_licence_texts_carry_every_kept_go_text():
    kept = lic.vendored(ROOT)
    text = (ROOT / "THIRD_PARTY_LICENSES").read_text("utf-8")
    assert kept and all(kept.values()), "every kept module folder holds a text"
    assert any(key.startswith(f"{lic.GO_STD}@go") for key in kept)
    for key, files in kept.items():
        path, version = key.rsplit("@", 1)
        title = f"the Go standard library {version}" if path == lic.GO_STD else f"{path} {version}"
        for name, body in files.items():
            assert re.search(rf"^{re.escape(title)} \(in [a-z, ]+\): {re.escape(name)}$", text, re.M), (key, name)
            assert lic._normalised(body) in text, (key, name)
