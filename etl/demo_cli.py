# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""`python -m etl demo [--force]` — the invented Sample household in an empty install directory, ready for `./ff up`.

It refuses an install directory holding anything but `ff`, compose.yaml and .env, unless --force; even then it never
writes over a household's files: config/, imports/, store/ and state/family/ must be empty. It makes the install's
directories as the invoking user, generates the `full` demo household (tools/demo, which the image carries) with its
history ending on the household's latest finished day (today once its last snapshot hour has passed on its clock,
else yesterday), ingests it, writes .env and compose.yaml as `init` does, and adds a passcode labelled `demo`: four
random words, printed once ("Sign in with: …") and stored as a hash only. It ends "Now run ./ff up".

Exit 0 when done, 2 on a refusal (one line)."""
from __future__ import annotations

import datetime as dt
import importlib
import os
import secrets
import shutil
import sys
import zoneinfo
from collections.abc import Callable
from pathlib import Path

from . import clock, install
from .install import Refused

# what an install directory may hold before a demo is made in it
ALLOWED = {"ff", install.COMPOSE_FILE, install.ENV_FILE, install.ENV_PREVIOUS}
# the generator's directories, moved into the install
GENERATED = ("config", "imports", "store", "state/family")
PROFILE = "full"
LABEL = "demo"
# the words a demo passcode is drawn from: short, common and unambiguous
WORDS = tuple("""
acorn amber anchor apple arrow aspen autumn badge bakery bamboo banjo barley basket beacon berry birch biscuit
blossom bramble breeze brook bucket butter cabin cactus candle canoe canyon caramel cedar cello chalk cherry
cider citrus clover cobalt comet copper coral cotton cradle crater crayon cricket crystal daisy dawn delta denim
desert dolphin dragon drum dune eagle easel echo ember falcon fern fiddle field flint forest fossil fountain
fox garden garnet ginger glacier globe granite grape gravel harbor harvest hazel heron hickory hill honey horizon
indigo iris island ivory jasmine jungle juniper kayak kettle kite lagoon lantern laurel lemon lilac linen lotus
maple marble meadow melon meteor mint mirror moss mountain nectar nickel noodle oak oasis ocean olive onyx orange
orchid otter oyster paddle panda parade pebble pepper piano pine planet plum pocket pond poppy prairie pumpkin
quartz quill rabbit raven reef ribbon river robin rocket saddle saffron salmon sapphire satin school shadow shell
sierra silver sparrow spruce squash star stone summit sunset swallow tango teapot thistle thunder tiger timber
topaz tulip tundra turtle umbrella valley velvet violet voyage walnut willow window winter yarrow zephyr
album alpine apron atlas bagel ballad beetle bison blanket bonnet bridge bubble buffalo cable camel carpet castle
cereal channel chimney circus cloud compass cookie corner cottage crocus cupcake curtain dinner donkey
dragonfly elbow engine fable feather fiesta finch flute folder forge gadget galaxy gazelle geyser goblet
gondola gopher gravy guitar hammock hamster helmet hollow hummus igloo jacket jigsaw kernel kiwi ladder
lemur lichen locket magnet mango marina mitten monsoon mosaic muffin napkin nutmeg orbit pantry papaya
parcel pasta peach pelican penguin pickle pilot pixel pretzel puffin puzzle quiver radish rainbow ranch recipe
""".split())


def passphrase(n: int = 4) -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(n))


def _generator():
    """tools/demo's generator: beside the etl package in the image (/opt/ff/tools/demo) and in a checkout."""
    root = str(install.ROOT)
    if root not in sys.path:
        sys.path.append(root)
    try:
        return importlib.import_module("tools.demo.generate"), importlib.import_module("tools.demo.household")
    except ImportError:
        raise Refused("this image holds no demo generator: pull or build the image again") from None


def _end_day(zone: str, last_hour: int, now: dt.datetime) -> dt.date:
    """The demo's last day: today on the household's clock once its last snapshot hour has passed, else yesterday,
    so nothing in it is dated after now."""
    local = now.astimezone(zoneinfo.ZoneInfo(zone))
    return local.date() if local.hour > last_hour else local.date() - dt.timedelta(days=1)


def _empty(path: Path) -> bool:
    try:
        return not any(path.iterdir())
    except FileNotFoundError:
        return True


def run(*, force: bool = False, out: Callable[[str], None] = print, now: dt.datetime | None = None) -> int:
    try:
        return _run(force=force, out=out, now=now or clock.wall_utc())
    except Refused as e:
        print(f"demo: {e}", file=sys.stderr)
        return 2


def _run(*, force: bool, out: Callable[[str], None], now: dt.datetime) -> int:
    root = install.home()
    install.refuse_root()
    others = sorted(p.name for p in root.iterdir() if p.name not in ALLOWED)
    if others and not force:
        raise Refused(f"{install.shown(root, root)} is not empty ({', '.join(others[:5])}"
                      f"{', …' if len(others) > 5 else ''}): make the demo in an empty directory, or pass --force "
                      "to make it beside what is there")
    for place in GENERATED:
        if not _empty(root / place):
            raise Refused(f"{place}/ already holds files, and a demo never writes over a household's: use an empty "
                          "directory")
    generate, household = _generator()
    from tools.demo import plan as plan_mod
    install.ensure_dirs(root, out=out)
    end = _end_day(household.TIMEZONE, plan_mod.SNAPSHOT_HOUR, now)
    work = root / f".demo-{secrets.token_hex(4)}"
    try:
        generate.run(PROFILE, end, work)
        for place in GENERATED:
            source = work / place
            if not source.is_dir():
                continue
            for entry in sorted(source.iterdir()):
                os.replace(entry, root / place / entry.name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out(f"generated the invented Sample household, its history ending {end.isoformat()}")
    env = install.base_env(root, {"FF_BIND": install.LOOPBACK, "FF_PORT": str(install.DEFAULT_PORT)})
    current = install.read_env(root) or {}
    install.write_env(root, {k: v for k, v in env.items() if not current.get(k) or k in ("FF_UID", "FF_GID")})
    install.ensure_compose(root, out=out)
    from . import pipeline
    from .config import Config
    from .validate import ConfigError
    try:
        cfg = Config(check_paths=True)
    except ConfigError as e:
        for problem in e.problems:
            out(str(problem))
        raise Refused("the generated household does not validate (above): this is a fault of the release") from None
    if pipeline.run(cfg=cfg) != 0:
        raise Refused("the demo's ingest failed (above): this is a fault of the release")
    from . import passcode_cli
    phrase = passphrase()
    old = os.umask(0o077)
    try:
        passcode_cli.add(LABEL, given=phrase)
    finally:
        os.umask(old)
    env = install.read_env(root) or {}
    out(f"Sign in with: {phrase}")
    out("(shown this once: ./ff passcode add demo sets another)")
    out(f"Now run ./ff up, then open {install.address(env)}")
    return 0
