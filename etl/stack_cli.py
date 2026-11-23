# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The Python halves of `./ff up` and `./ff upgrade`, run in the ETL image as the invoking user before compose touches
anything.

`python -m etl preflight` makes the install ready to start, or says why it cannot be: it refuses uid 0; makes every
missing directory as the invoking user (a directory Docker would otherwise create, root-owned); copies compose.yaml in
when the install has none; checks the settings file (there, FF_UID and FF_GID the invoking user's and not 0); runs
`validate`; checks that a passcode exists (else nobody could sign in); refuses plain HTTP on a bind other than
loopback when this computer has a globally routable address, unless SITE_ADDRESS is a domain; and refuses a port
another program holds (compose would start the store and stop at web). The host's addresses and listeners come from
`ff` (FF_HOST_ADDRESSES: what `ip -o addr` or `hostname -I` printed on the host; FF_HOST_LISTENERS and FF_STACK_PS:
`ss`'s listeners and compose's view of the stack); when it could read no address, that check warns instead. Once every
check has passed, the install's compose.yaml is brought to the release's (install.refresh_compose: a file a release
shipped is replaced and kept as compose.yaml.previous, one changed by hand gets compose.yaml.new beside it), and an
install with no data yet, or data an older release wrote, gets the ingest marker touched, so the
refresher builds the data as soon as it runs (`./ff up` on a new install, an upgrade, a new image named in .env). Its
last line, `address: <url>`, is where the dashboard answers once it has started.

`python -m etl upgrade-plan start [--to TAG]` is the first step of an upgrade: a backup of the household's files, the
settings kept as .env.previous, and with --to the image settings moved to that release's tag. `upgrade-plan failed
STAGE` says where an upgrade stopped, what state the install is in and how to go back; before the new images ran
(STAGE pull or validate) it puts .env.previous back, so the stack that still runs is the one .env names.
`upgrade-plan done` says it finished.

Exit 0 when ready (or done), 2 on a refusal (one line, or the validator's lines)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from . import install
from .install import Refused

IMAGE_KEYS = ("FF_IMAGE_ETL", "FF_IMAGE_WEB")
# the images compose.yaml runs when the settings name none: this release's published ones (a test pins these to the
# file's own defaults, ff's and the version)
IMAGE_NAME = "ghcr.io/good-work-dojo/family-financials"
DEFAULT_IMAGES = {"FF_IMAGE_ETL": f"{IMAGE_NAME}-etl:{install.VERSION}",
                  "FF_IMAGE_WEB": f"{IMAGE_NAME}-web:{install.VERSION}"}


def host_addresses(text: str) -> list[ipaddress._BaseAddress]:
    """The addresses in what `ip -o addr` (each `inet[6] ADDR/PREFIX` field) or `hostname -I` printed."""
    found = []
    tokens = re.findall(r"inet6?\s+(\S+)", text) or text.split()
    for token in tokens:
        try:
            found.append(ipaddress.ip_address(token.split("/")[0].split("%")[0]))
        except ValueError:
            continue
    return found


def exposure_problem(env: dict[str, str], addresses_text: str | None, *, out) -> str | None:
    """Why serving the dashboard as the settings say is refused, or None: plain HTTP (no domain in SITE_ADDRESS) on a
    bind that reaches a globally routable address of this computer — the bind itself global, or every address
    (0.0.0.0, ::) while the computer has one. Unknown addresses warn instead."""
    if install.is_domain(env.get("SITE_ADDRESS", "")):
        return None
    bind = env.get("FF_BIND") or install.LOOPBACK
    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        return f"FF_BIND in .env is {bind!r}, not an address: set 127.0.0.1 (this computer) or 0.0.0.0 (the network)"
    if ip.is_loopback:
        return None
    if ip.is_global:
        return (f"the dashboard would answer on {bind}, an address the internet can reach, over plain HTTP: serve it "
                "under a domain (SITE_ADDRESS in .env) or keep FF_BIND=127.0.0.1")
    if not ip.is_unspecified:
        out(f"note: the dashboard answers on {bind} over plain HTTP: the passcode crosses your network unencrypted")
        return None
    if addresses_text is None or addresses_text.strip() in ("", "unknown"):
        out("note: this computer's addresses could not be read, so whether 0.0.0.0 reaches the internet was not "
            "checked; the passcode crosses your network unencrypted over plain HTTP")
        return None
    public = [str(a) for a in host_addresses(addresses_text) if a.is_global]
    if public:
        return (f"FF_BIND=0.0.0.0 would serve the dashboard over plain HTTP on every address of this computer, "
                f"{public[0]} among them, which the internet can reach: set FF_BIND to its home-network address, or "
                "serve it under a domain (SITE_ADDRESS)")
    out("note: the dashboard serves your home network over plain HTTP: the passcode crosses it unencrypted")
    return None


def env_problem(env: dict[str, str] | None) -> str | None:
    if env is None:
        return "there is no settings file (.env): run ./ff init, which writes it"
    uid, gid = install.invoking_ids()
    for key, want in (("FF_UID", uid), ("FF_GID", gid)):
        value = env.get(key, "")
        if value == "0":
            return f"{key} in .env is 0, so the services would run as root: set {key}={want}"
        if value != str(want):
            return (f"{key} in .env is {value or 'not set'}, but you are {want}: the services would not own your "
                    f"files; set FF_UID={uid} and FF_GID={gid} in .env")
    return None


def preflight(*, out: Callable[[str], None] = print) -> int:
    from .setup_cli import usable_passcodes, validate_now
    root = install.home()
    install.ensure_dirs(root, out=out)
    install.ensure_compose(root, out=out)
    env = install.read_env(root)
    problem = env_problem(env)
    if problem:
        raise Refused(problem)
    lines: list[str] = []
    if validate_now(out=lines.append):
        for line in lines:
            print(line, file=sys.stderr)
        raise Refused("the config has the problems above: correct them (./ff validate checks again), then ./ff up")
    out(lines[-1])
    from .config import Paths
    if not usable_passcodes(Paths().secrets_web_dir() / "passcodes"):
        raise Refused("nobody could sign in: run ./ff passcode add <name>, then ./ff up")
    problem = exposure_problem(env, os.environ.get("FF_HOST_ADDRESSES"), out=out)
    if problem:
        raise Refused(problem)
    if os.environ.get("FF_HOST_LISTENERS") is not None:
        # a port another program holds would start the store and stop at web: say it before anything starts
        from .doctor_cli import check_ports
        taken = [f for f in check_ports(env) if f.status == "FAIL"]
        if taken:
            raise Refused(f"{taken[0].what}: {taken[0].fix}, then ./ff up")
    install.refresh_compose(root, out=out)
    if data_wanted(Paths().data_dir() / "exports" / "manifest.json"):
        ask_for_ingest(Paths().state_refresh_dir())
        out("the dashboard's data is built about a minute after the services start")
    out(f"address: {install.address(env)}")
    return 0


def data_wanted(manifest: Path) -> bool:
    """Whether the exports need building before the dashboard can show them: none yet, none readable, or ones an older
    release wrote (the dashboard reads no export older than this release's schema)."""
    from .exports import SCHEMA_VERSION
    try:
        version = json.loads(manifest.read_bytes()).get("schema_version")
    except (OSError, ValueError, AttributeError):
        return True
    return not isinstance(version, int) or version < SCHEMA_VERSION


def ask_for_ingest(refresh_dir: Path) -> None:
    """The ingest marker the store touches for "Rebuild from files", touched here: the refresher, once it runs, builds
    the data without a fetch (deploy/refresh_state.py, beside the etl package in the image and in a checkout)."""
    deploy = str(install.ROOT / "deploy")
    if deploy not in sys.path:
        sys.path.append(deploy)
    import refresh_state
    refresh_state.touch_ingest_marker(refresh_dir)


# ------------------------------------------------------------------ upgrade

def retag(image: str, tag: str) -> str:
    """`image` at `tag`: its name with the tag replaced (a digest-pinned or untagged name takes the tag too)."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name[: len(name) - len(last)] + last.split(":", 1)[0]
    return f"{name}:{tag}"


def upgrade_start(to: str | None, *, out: Callable[[str], None] = print) -> int:
    from . import backup_cli
    root = install.home()
    env = install.read_env(root)
    problem = env_problem(env)
    if problem:
        raise Refused(problem)
    if to is not None and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", to):
        raise Refused(f"{to!r} is not a release tag (such as 1.2.0)")
    if to is not None and any(os.environ.get(k) for k in IMAGE_KEYS):
        raise Refused("FF_IMAGE_ETL or FF_IMAGE_WEB is set in your environment, which wins over .env: unset it, or "
                      "set it to the new release yourself and run ./ff upgrade without --to")
    target = backup_cli.backup(out=out)
    text = (root / install.ENV_FILE).read_bytes()
    install.write_atomic(root / install.ENV_PREVIOUS, text, 0o600)
    out(f"the settings as they were are kept in {install.shown(root / install.ENV_PREVIOUS, root)}")
    if to is not None:
        values = {k: retag(env.get(k) or DEFAULT_IMAGES[k], to) for k in IMAGE_KEYS}
        install.write_env(root, values)
        out("the images are now " + ", ".join(values.values()))
    out(f"backup: {install.shown(target, root)}")
    return 0


STAGES = ("pull", "validate", "up", "ingest", "doctor")


def upgrade_failed(stage: str, *, out: Callable[[str], None] = print) -> int:
    root = install.home()
    previous = root / install.ENV_PREVIOUS
    backups = sorted((root / "backups").glob("ff-*.tar.gz"))
    newest = install.shown(backups[-1], root) if backups else "none"
    if stage in ("pull", "validate"):
        if previous.is_file():
            install.write_atomic(root / install.ENV_FILE, previous.read_bytes(), 0o600)
        why = ("the new release's images could not be pulled" if stage == "pull" else
               "the new release refuses this config (its lines are above)")
        out(f"upgrade stopped: {why}. Nothing was restarted: the stack runs the release it ran before, and .env names "
            "it again.")
        if stage == "validate":
            out("Correct the config for the new release, then run ./ff upgrade again.")
        return 0
    words = {"up": "the new release's services did not all start healthy",
             "ingest": "the new release's services run, but its first ingest failed",
             "doctor": "the new release runs, but doctor found problems (above)"}[stage]
    out(f"upgrade stopped: {words}. The stack now runs the new images; your files were backed up first ({newest}).")
    compose = root / install.COMPOSE_PREVIOUS
    replaced = (compose.is_file() and previous.is_file()
                and compose.stat().st_mtime >= previous.stat().st_mtime)       # written by this upgrade's preflight
    first = (f"cp {install.COMPOSE_PREVIOUS} {install.COMPOSE_FILE} (this upgrade replaced it) and " if replaced
             else "")
    out(f"To go back to the release before: {first}cp .env.previous .env, then ./ff up, then ./ff refresh (which "
        f"rebuilds the data for that release); and if the new release changed your config or stores, ./ff restore "
        f"{newest} --force before that ./ff up.")
    return 0


def run(command: str, *, stage: str | None = None, to: str | None = None) -> int:
    try:
        if command == "preflight":
            return preflight()
        if command == "start":
            return upgrade_start(to)
        if command == "failed":
            return upgrade_failed(stage or "")
        print("upgrade done: the new release runs, its data was rebuilt and doctor found nothing wrong")
        return 0
    except Refused as e:
        print(f"{'preflight' if command == 'preflight' else 'upgrade'}: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"{command}: {e.filename or ''} {e.strerror or e}".strip(), file=sys.stderr)
        return 2
    finally:
        sys.stdout.flush()

