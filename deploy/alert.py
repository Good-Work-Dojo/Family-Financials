# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""One alert of the data refresh: `alert.py <message>`, or the message on standard input when none is given.

The channels are files in the alert secrets' folder (etl.config Paths.secrets_alerts_dir: FF_SECRETS_ALERTS_DIR, else
FF_HOME's secrets/alerts/): TOKEN_FILE and CHAT_FILE, both, for a Telegram bot's sendMessage, and URL_FILE, a webhook
sent a POST of the JSON {"text": <message>}. Either, both or none may be set up. A credential is read from its file
alone, never from an argument or the environment, and never printed: a file that is no regular file (a link is never
followed, a FIFO never waited on), that its group or other users may read (anything wider than 0600, as the feed's
credential), that is empty or larger than a credential is refused, and that channel fails with a line saying why.

The message goes out as `<the household's name>: <message>`: household.yaml `name`, else its `surname`, else PRODUCT.

Every request is HTTPS with the certificate checked, gives up after TIMEOUT_S and follows no redirect (a 30x is a
failure). The one exception is the test seam, TEST_ORIGIN_VARIABLE: set to exactly http://127.0.0.1:<port>, it is the
base Telegram is reached at, and the one plain-HTTP origin a webhook may name, reached through no proxy; any other value
opens nothing. Nothing outside the tests sets it.

Exit 0 when a channel took the message, NO_CHANNEL when none is set up (nothing is sent, nothing printed), 1 when each
channel that is set up failed. Each failure is one line on standard error, `alert: <channel> ...`, naming no
credential; refresh.sh puts them in its log."""
from __future__ import annotations

import errno
import functools
import http.client
import json
import os
import re
import ssl
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = "telegram_token"
CHAT_FILE = "telegram_chat_id"
URL_FILE = "alert_url"
NO_CHANNEL = 3
PRODUCT = "Family Financials"
TELEGRAM = "https://api.telegram.org"
TIMEOUT_S = 10
MAX_SECRET_BYTES = 4096
MAX_NAME = 80
# Telegram takes up to 4096 characters of text; a refresh's alert is a line or two
MAX_MESSAGE = 3500
USER_AGENT = "family-financials-alert"

# The test seam: the one variable that lets a plain-HTTP origin be reached, read in _seam alone.
TEST_ORIGIN_VARIABLE = "FF_ALERT_TEST_ORIGIN"
_LOOPBACK_ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")
# what a bot token is made of (digits, a colon, letters, digits, _ and -): anything else could change the request's path
_TOKEN = re.compile(r"[A-Za-z0-9:_-]+")


class Refused(Exception):
    """A credential file this program will not use; the message says why and what to do, and names no credential."""


def _seam() -> str | None:
    """The loopback origin TEST_ORIGIN_VARIABLE states exactly, else None."""
    m = _LOOPBACK_ORIGIN.fullmatch(os.environ.get(TEST_ORIGIN_VARIABLE, ""))
    return m.group(0) if m is not None and int(m.group(1)) <= 65535 else None


def _allowed(url: str) -> bool:
    """An https URL with a host, or a URL of the seam's origin exactly."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    seam = _seam()
    return (seam is not None and parts.scheme == "http" and parts.hostname == "127.0.0.1"
            and f"http://127.0.0.1:{port}" == seam and not parts.username and not parts.password)


def read_secret(folder: Path, name: str) -> str | None:
    """The text of the credential file `name` in `folder`, its ends trimmed; None when nothing is at that name.
    Refused when it is not a regular file of its own (a link, a FIFO, a folder), its group or other users may read it,
    it cannot be read, it is empty, larger than MAX_SECRET_BYTES or not text."""
    path = folder / name
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Refused(f"{path} cannot be read ({e.strerror}): make it a file this user can read") from None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise Refused(f"{path} is a link: put the credential in the file itself") from None
        raise Refused(f"{path} cannot be read ({e.strerror}): make it a file this user can read") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise Refused(f"{path} is not a file: put the credential in a file of that name")
    if info.st_mode & 0o077:
        os.close(fd)
        raise Refused(f"{path} can be read by other users (mode {oct(info.st_mode & 0o777)}): make it readable by "
                      f"its owner only (chmod 600 {path})")
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_SECRET_BYTES + 1)
    if len(data) > MAX_SECRET_BYTES:
        raise Refused(f"{path} is larger than a credential: write the credential alone in it")
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise Refused(f"{path} is not text: write the credential alone in it") from None
    if not text:
        raise Refused(f"{path} is empty: write the credential in it, or remove the file")
    return text


def household_name(config_dir: Path) -> str:
    """The prefix of every alert: household.yaml `name`, else `surname`, as one line of at most MAX_NAME characters;
    PRODUCT when the file gives neither or cannot be read."""
    try:
        # the worker's environment has it; a failure to load it falls back like the rest
        import yaml

        with open(config_dir / "household.yaml", encoding="utf-8") as f:
            doc = yaml.safe_load(f.read(256 * 1024))
    except Exception:               # noqa: BLE001 - an alert goes out whatever the config holds
        return PRODUCT
    for key in ("name", "surname"):
        value = doc.get(key) if isinstance(doc, dict) else None
        if isinstance(value, str):
            name = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:MAX_NAME].strip()
            if name:
                return name
    return PRODUCT


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 30x is raised as the HTTPError it is: no request is sent to the address it names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


@functools.cache
def _opener(seam: bool) -> urllib.request.OpenerDirector:
    """Certificates checked, no redirect followed; the seam's opener uses no proxy, an https one the environment's."""
    handlers: list[urllib.request.BaseHandler] = [
        _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if seam:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def post(url: str, body: bytes, content_type: str) -> str | None:
    """POST `body` to `url`: None when the answer is a 2xx, else why not, in words that name no part of the URL."""
    if not _allowed(url):
        return "its address is not an https one"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": content_type, "User-Agent": USER_AGENT})
    try:
        with _opener(urllib.parse.urlsplit(url).scheme == "http").open(req, timeout=TIMEOUT_S) as resp:
            resp.read(64 * 1024)
        return None
    except urllib.error.HTTPError as e:
        return f"it answered HTTP {e.code}"
    except TimeoutError:
        return f"no answer within {TIMEOUT_S} s"
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError):
            return f"no answer within {TIMEOUT_S} s"
        return f"it could not be reached ({getattr(reason, 'strerror', None) or type(reason).__name__})"
    except (http.client.HTTPException, OSError, ValueError) as e:
        return f"the request failed ({type(e).__name__})"


def _telegram(folder: Path, message: str) -> tuple[bool, str | None] | None:
    """(set up, why it failed or None) for the Telegram channel; None when neither of its files is there."""
    token = chat = None
    try:
        token = read_secret(folder, TOKEN_FILE)
        chat = read_secret(folder, CHAT_FILE)
    except Refused as e:
        return True, str(e)
    if token is None and chat is None:
        return None
    if token is None or chat is None:
        return True, (f"it needs both {TOKEN_FILE} and {CHAT_FILE} in {folder}, and only one is there: add the other, "
                      "or remove it")
    if not _TOKEN.fullmatch(token):
        return True, f"{folder / TOKEN_FILE} holds characters a bot token never has: write the token alone in it"
    why = post(f"{_seam() or TELEGRAM}/bot{token}/sendMessage",
               urllib.parse.urlencode({"chat_id": chat, "text": message}).encode(),
               "application/x-www-form-urlencoded")
    return True, why


def _webhook(folder: Path, message: str) -> tuple[bool, str | None] | None:
    """(set up, why it failed or None) for the webhook; None when its file is not there."""
    try:
        url = read_secret(folder, URL_FILE)
    except Refused as e:
        return True, str(e)
    if url is None:
        return None
    if not _allowed(url):
        return True, f"{folder / URL_FILE} does not hold an https address: write the webhook's https address alone in it"
    return True, post(url, json.dumps({"text": message}).encode(), "application/json")


def send(message: str, folder: Path, name: str) -> int:
    """Send `<name>: <message>` through every channel set up in `folder`; the exit code (the module's docstring)."""
    text = f"{name}: {message}"[:MAX_MESSAGE]
    tried = taken = 0
    for label, channel in (("Telegram", _telegram), ("the webhook", _webhook)):
        result = channel(folder, text)
        if result is None:
            continue
        tried += 1
        _, why = result
        if why is None:
            taken += 1
        else:
            print(f"alert: {label} did not take the alert: {why}", file=sys.stderr)
    if tried == 0:
        return NO_CHANNEL
    return 0 if taken else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    message = " ".join(args) if args else sys.stdin.read(MAX_MESSAGE)
    message = " ".join(message.split())
    if not message:
        print("alert: no message to send: give it as the argument, or on standard input", file=sys.stderr)
        return 2
    # the worker holds the ETL; imported here so the code above stays stdlib
    from etl.config import Paths

    paths = Paths()
    return send(message, paths.secrets_alerts_dir(), household_name(paths.config_dir))


if __name__ == "__main__":
    sys.exit(main())
