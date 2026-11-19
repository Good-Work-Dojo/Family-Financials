# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The AI step's one networked module: one request to the household's provider through its official SDK, and the
provider's answer as text, or the class of failure it met. Neither the offline pipeline nor the AI step's offline
modules import it (tests/test_offline_imports.py); `python -m etl ai run` loads it when it runs, and the SDKs are
imported only inside the function that calls one.

What is sent is exactly the request body etl/ai/request.py builds (what `python -m etl ai preview` prints): its
instruction text as the system prompt and the body itself, serialised as the preview prints it, as the one user
message; plus the model, an output limit sized to the batch and the shape the answer must take. Nothing else: no
header, organization, project or address an environment variable names is added (_quiet).

  anthropic   anthropic.Anthropic(...).messages.create with output_config.format, a JSON schema (structured
              outputs): the current Claude models refuse a forced tool choice, so the schema is the documented way
              to get the answer list back. No thinking setting is sent (the model's own default; a thinking budget
              is refused by the current models), no sampling setting, no streaming (a small batch).
  openai      openai.OpenAI(...).chat.completions.create with response_format json_schema, strict, at the household's
              base_url (a local model) or OpenAI's own.

A provider is reached over https, or, for a local model, over plain http to the worker's own loopback address only
(127.0.0.0/8, ::1 or localhost, as the config check allows): the key never crosses a network in clear. A plain-http
request goes direct, never through a proxy the environment names; an https one may, as the bank feed's does.

Both clients are built with the key passed to the constructor and the provider's address passed explicitly, after
every variable an SDK reads to choose where a request goes or what it carries is removed from the process's
environment (_quiet: every ANTHROPIC_* and OPENAI_* name; the pinned SDKs read ANTHROPIC_BASE_URL,
ANTHROPIC_CUSTOM_HEADERS, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_PROFILE and the workload-identity names,
OPENAI_BASE_URL, OPENAI_CUSTOM_HEADERS, OPENAI_API_KEY, OPENAI_ADMIN_KEY, OPENAI_ORG_ID and OPENAI_PROJECT_ID, some
even beside an explicit key), so no credential, header, organization, project or address comes from it; TIMEOUT
seconds per attempt, the SDK's
own retries limited to MAX_RETRIES (it retries a timeout, a 408, 409, 429 and every 5xx), a wait between retries of
at most RETRY_WAIT_CAP seconds whatever the provider asks (_cap_wait), redirects never followed, an answer of at most
MAX_ANSWER_BYTES, asked for and read unencoded (_limit_size), and (anthropic) no OpenTelemetry spans or trace
headers. The SDKs' own debug logging is switched off before a request (_quiet): their variables (ANTHROPIC_LOG,
OPENAI_LOG, among the names above) and SSLKEYLOGFILE are removed as well and their loggers held at WARNING. A run is
bounded twice: at most (MAX_RETRIES + 1) attempts of TIMEOUT seconds of silence each plus the waits between them, and
DEADLINE seconds in all, after which the run stops waiting (`ask`).

Every failure is caught and reduced to its class (Outcome.kind) and, for an HTTP answer, its status code: never the
exception's text, which can carry the request. The key is read from secrets/ai/api_key (read_key) with the feed
credential's rule, and is never put in argv, the environment, a log line or a message.

The test seam is TEST_ORIGIN_VARIABLE: set to exactly http://127.0.0.1:<port>, both providers are sent to that one
loopback origin (anthropic at it, openai at its /v1), through no proxy; set to anything else, nothing is sent.
Production never sets it.
"""
from __future__ import annotations

import errno
import logging
import math
import os
import re
import stat
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .._net import now_utc
from ..validate import base_url_problem
from .request import serialise

TIMEOUT = 30.0
MAX_RETRIES = 2
RETRY_WAIT_CAP = 5.0
DEADLINE = 90.0
MAX_ANSWER_BYTES = 1024 * 1024
# what an SDK reads from the environment to log requests or TLS secrets, removed before one is imported
_LOG_VARIABLES = ("ANTHROPIC_LOG", "OPENAI_LOG", "SSLKEYLOGFILE")
# the SDKs' own variables, every name starting so removed before one is imported: they can move a request
# (ANTHROPIC_BASE_URL, OPENAI_BASE_URL), add to it (ANTHROPIC_CUSTOM_HEADERS, OPENAI_CUSTOM_HEADERS, OPENAI_ORG_ID,
# OPENAI_PROJECT_ID) or supply a credential (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, OPENAI_API_KEY, OPENAI_ADMIN_KEY)
_SDK_PREFIXES = ("ANTHROPIC_", "OPENAI_")
_LOGGERS = ("anthropic", "openai", "httpx", "httpx2", "httpcore", "httpcore2")
KEY_NAME = "api_key"
MAX_KEY_BYTES = 4096
ANTHROPIC_URL = "https://api.anthropic.com"
OPENAI_URL = "https://api.openai.com/v1"
# the output limit: room for the model's own reasoning plus a generous line per vendor, within what a request that
# does not stream may ask for
BASE_TOKENS = 4096
TOKENS_PER_VENDOR = 64
MAX_TOKENS = 16000

TEST_ORIGIN_VARIABLE = "FF_AI_TEST_ORIGIN"
_LOOPBACK_ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")

SCHEMA_NAME = "vendor_categories"
# the answer's shape, sent to the provider as the structured output it must follow: a list of
# {vendor, category, subcategory | null, confidence}. Numeric ranges are not part of the schemas the providers
# accept, so the confidence's range, like everything else, is checked on arrival (etl/ai/cache.py check_answer).
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string"},
                    "category": {"type": "string"},
                    "subcategory": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "confidence": {"type": "number"},
                },
                "required": ["vendor", "category", "subcategory", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answers"],
    "additionalProperties": False,
}

# the classes a request ends in: "answered" (Outcome.text holds the answer) or one of these failures
FAILURES = ("rejected_key", "rate_limited", "unavailable", "timed_out", "deadline", "refused", "malformed",
            "request_refused", "redirect", "failed")


class AnswerTooLarge(Exception):
    """An answer longer than MAX_ANSWER_BYTES, or sent encoded when it was asked for unencoded: not read."""


class KeyRefused(Exception):
    """The key file is not one the AI step reads; the message says why in plain words, never holding the key."""


class EndpointRefused(Exception):
    """The provider's address is not one a request may go to; the message says why, in plain words."""


@dataclass(frozen=True)
class Endpoint:
    """Where a request goes: the SDK's base URL, and whether it goes direct (no proxy: the loopback seam and a local
    model's plain-HTTP address)."""
    url: str
    direct: bool


@dataclass(frozen=True)
class Outcome:
    """How a request ended: `kind` "answered" with the answer's `text`, or one of FAILURES with the HTTP `status`
    when the provider answered one and the class name of the `error` caught when no other kind fits."""
    kind: str
    text: str | None = None
    status: int | None = None
    error: str | None = None


def current_time():
    """The current UTC time (the networked modules' clock, etl/_net.py)."""
    return now_utc()


def max_tokens(vendors: int) -> int:
    """The output limit of a request asking about `vendors` vendors."""
    return min(MAX_TOKENS, BASE_TOKENS + TOKENS_PER_VENDOR * vendors)


# ---------------------------------------------------------------------------------------------------------- the key

def read_key(path: Path) -> str | None:
    """The key in the file at `path`, or None when there is none. The file must be a regular file itself (never read
    through a link, never waited on as a FIFO), readable by its owner alone (the feed credential's rule), at most
    MAX_KEY_BYTES, and hold the key as one word of printable ASCII; KeyRefused otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise KeyRefused("it is a link, which is never followed") from None
        raise KeyRefused(f"it cannot be read ({e.strerror or type(e).__name__})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise KeyRefused("it is not a regular file")
        if info.st_mode & 0o077:
            raise KeyRefused(f"other users can read or change it (mode {oct(stat.S_IMODE(info.st_mode))}); "
                             f"make it readable by its owner only")
        if info.st_size > MAX_KEY_BYTES:
            raise KeyRefused("it is larger than a key")
        data = os.read(fd, MAX_KEY_BYTES + 1)
    finally:
        os.close(fd)
    try:
        key = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise KeyRefused("it does not hold a key") from None
    if not key or len(data) > MAX_KEY_BYTES or any(not c.isprintable() or c.isspace() for c in key):
        raise KeyRefused("it does not hold a key on one line")
    return key


# -------------------------------------------------------------------------------------------------------- the address

def endpoint(provider: str, base_url: str | None) -> Endpoint:
    """Where the household's provider is reached: the seam's loopback origin when TEST_ORIGIN_VARIABLE is set (only
    an exact http://127.0.0.1:<port>; any other value is refused), else Anthropic's own address, or the configured
    base_url (an https:// address, or http:// to this computer, as the config check allows) or OpenAI's own.
    EndpointRefused when it may not be used."""
    seam = os.environ.get(TEST_ORIGIN_VARIABLE, "")
    if seam:
        found = _LOOPBACK_ORIGIN.fullmatch(seam)
        if found is None or int(found.group(1)) > 65535:
            raise EndpointRefused("the test address is refused: only http://127.0.0.1:<port> is allowed")
        return Endpoint(seam if provider == "anthropic" else f"{seam}/v1", direct=True)
    if provider == "anthropic":
        return Endpoint(ANTHROPIC_URL, direct=False)
    url = (base_url or OPENAI_URL).strip()
    problem = base_url_problem(url)
    if problem is not None:
        raise EndpointRefused(f"the provider's address is refused: {problem}")
    return Endpoint(url, direct=urllib.parse.urlsplit(url).scheme.lower() == "http")


# ------------------------------------------------------------------------------------------------------- the request

def _cap_wait(response) -> None:
    """A response hook: the wait a provider asks for before a retry (retry-after-ms, retry-after) becomes at most
    RETRY_WAIT_CAP seconds, so a run stays bounded whatever it asks; a wait it states as a date is the cap."""
    asked = None
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = response.headers.get(name)
        if value is None:
            continue
        try:
            asked = float(value) / scale
        except ValueError:
            asked = RETRY_WAIT_CAP
        break
    if asked is None:
        return
    for name in ("retry-after-ms", "retry-after"):
        if name in response.headers:
            del response.headers[name]
    wait = min(asked, RETRY_WAIT_CAP) if math.isfinite(asked) else RETRY_WAIT_CAP
    response.headers["retry-after"] = f"{max(wait, 0.0):.3f}"


def _limit_size(httpx2):
    """A response hook (for the SDKs' HTTP library, `httpx2`): an answer is read only up to MAX_ANSWER_BYTES, and only
    unencoded (it is asked for so, and a compressed answer could hold far more than it measures): past that, or
    encoded, AnswerTooLarge, which the SDK raises as a connection error caused by it (_failure reads the cause)."""

    class Capped(httpx2.SyncByteStream):
        def __init__(self, stream):
            self.stream = stream

        def __iter__(self):
            read = 0
            for chunk in self.stream:
                read += len(chunk)
                if read > MAX_ANSWER_BYTES:
                    raise AnswerTooLarge()
                yield chunk

        def close(self):
            self.stream.close()

    def hook(response) -> None:
        length = response.headers.get("content-length", "")
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if (length.isdigit() and int(length) > MAX_ANSWER_BYTES) or encoding not in ("", "identity"):
            raise AnswerTooLarge()
        response.stream = Capped(response.stream)

    return hook


def _http_client(sdk, where: Endpoint):
    """The SDK's own HTTP client (its defaults kept) with redirects off, the retry wait capped, the answer's size
    capped and asked for unencoded and, for a direct endpoint, no proxy from the environment."""
    import httpx2

    options: dict = {"timeout": TIMEOUT, "follow_redirects": False, "headers": {"Accept-Encoding": "identity"},
                     "event_hooks": {"response": [_cap_wait, _limit_size(httpx2)]}}
    if where.direct:
        options["mounts"] = {"http://": None, "https://": None, "all://": None}
    return sdk.DefaultHttpxClient(**options)


def _quiet() -> None:
    """Before an SDK is imported: no variable that turns its debug logging on (or writes TLS secrets), and none of the
    SDKs' own (_SDK_PREFIXES: an address, a header, an organization, a project or a credential) is left in the
    environment, so the request goes where the client is told with what it is given."""
    for name in [*_LOG_VARIABLES, *(n for n in os.environ if n.upper().startswith(_SDK_PREFIXES))]:
        os.environ.pop(name, None)


def _hush() -> None:
    """After an SDK is imported: its loggers and its HTTP library's are held at WARNING (no handler is added), so a
    logging setup made at its import logs no request."""
    for name in _LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _caused_by(e: BaseException, kind: type) -> bool:
    seen: set[int] = set()
    while e is not None and id(e) not in seen:
        if isinstance(e, kind):
            return True
        seen.add(id(e))
        e = e.__cause__ or e.__context__
    return False


def _failure(sdk, e: Exception) -> Outcome:
    """The class of an exception an SDK raised, never its text. Both SDKs name their errors alike."""
    if _caused_by(e, AnswerTooLarge):
        return Outcome("malformed")
    if isinstance(e, sdk.APITimeoutError):
        return Outcome("timed_out")
    if isinstance(e, sdk.APIConnectionError):
        return Outcome("unavailable")
    if isinstance(e, (sdk.AuthenticationError, sdk.PermissionDeniedError)):
        return Outcome("rejected_key", status=e.status_code)
    if isinstance(e, sdk.RateLimitError):
        return Outcome("rate_limited", status=e.status_code)
    if isinstance(e, sdk.APIStatusError):
        code = e.status_code
        if 300 <= code < 400:
            return Outcome("redirect", status=code)
        return Outcome("unavailable" if code >= 500 else "request_refused", status=code)
    if isinstance(e, sdk.APIResponseValidationError):
        return Outcome("malformed")
    return Outcome("failed", error=type(e).__name__)


def _ask_anthropic(model: str, where: Endpoint, key: str, body: dict) -> Outcome:
    _quiet()
    import anthropic

    _hush()
    try:
        client = anthropic.Anthropic(
            api_key=key, base_url=where.url, timeout=TIMEOUT, max_retries=MAX_RETRIES,
            http_client=_http_client(anthropic, where), open_telemetry={"traces": False, "propagation": False})
        with client:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens(len(body["vendors"])),
                system=body["instructions"],
                messages=[{"role": "user", "content": serialise(body)}],
                output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            )
    except Exception as e:  # every failure is reduced to its class
        return _failure(anthropic, e)
    if getattr(message, "stop_reason", None) == "refusal":
        return Outcome("refused")
    text = next((b.text for b in getattr(message, "content", None) or ()
                 if getattr(b, "type", None) == "text" and isinstance(getattr(b, "text", None), str)), None)
    return Outcome("answered", text=text) if text is not None else Outcome("malformed")


def _ask_openai(model: str, where: Endpoint, key: str, body: dict) -> Outcome:
    _quiet()
    import openai

    _hush()
    try:
        client = openai.OpenAI(api_key=key, base_url=where.url, timeout=TIMEOUT, max_retries=MAX_RETRIES,
                               http_client=_http_client(openai, where))
        with client:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": body["instructions"]},
                          {"role": "user", "content": serialise(body)}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": SCHEMA_NAME, "schema": ANSWER_SCHEMA, "strict": True}},
                max_completion_tokens=max_tokens(len(body["vendors"])),
            )
    except Exception as e:  # every failure is reduced to its class
        return _failure(openai, e)
    choices = getattr(completion, "choices", None) or ()
    if not choices:
        return Outcome("malformed")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if getattr(message, "refusal", None) or getattr(choice, "finish_reason", None) == "content_filter":
        return Outcome("refused")
    text = getattr(message, "content", None)
    return Outcome("answered", text=text) if isinstance(text, str) else Outcome("malformed")


def ask(provider: str, model: str, where: Endpoint, key: str, body: dict) -> Outcome:
    """Send `body` (etl/ai/request.py build_request's) to `model` at `where` with `key`; how it ended. Never raises
    for anything the request meets. The request runs in a thread of its own that the run waits on for DEADLINE
    seconds at most: past that it is "deadline" (the thread is left to end with the process; nothing it would return
    is used)."""
    calls = {"anthropic": _ask_anthropic, "openai": _ask_openai}
    if provider not in calls:
        return Outcome("failed", error="UnknownProvider")
    ended: list[Outcome] = []

    def request() -> None:
        try:
            ended.append(calls[provider](model, where, key, body))
        except BaseException as e:  # an import failing, or anything else outside the SDK's own errors
            ended.append(Outcome("failed", error=type(e).__name__))

    worker = threading.Thread(target=request, name="ai-request", daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive() or not ended:
        return Outcome("deadline")
    return ended[0]
