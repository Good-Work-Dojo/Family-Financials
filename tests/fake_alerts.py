# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A stand-in for both of the refresh's alert endpoints, Telegram's sendMessage and a household's webhook: a stdlib HTTP
server on 127.0.0.1 at an ephemeral port, run in a thread for as long as `serve()` is held, and reached by
deploy/alert.py only through its one test seam, FF_ALERT_TEST_ORIGIN set to this server's origin. Nothing it does
leaves the loopback interface.

Every POST is recorded in `received` (its path, content type and the text it carried: a Telegram form's `text`, a
webhook's JSON `text`) and answered `status` (200 unless a test sets another); `redirect` makes each answer a 302 to
another address, which alert.py must not follow. All credentials a test writes are invented."""
from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# what a test writes as the bot's credentials (invented): the token's shape is a real token's
TOKEN = "123456:ABC-fake_token-0000"
CHAT = "-1000000000000"


@dataclass(frozen=True)
class Received:
    path: str
    content_type: str
    text: str | None              # the message the request carried, None when it carried none in the expected shape
    chat_id: str | None           # a Telegram form's chat id


@dataclass
class Hook:
    origin: str
    received: list[Received] = field(default_factory=list)
    status: int = 200
    redirect: str | None = None

    def texts(self) -> list[str]:
        return [r.text for r in self.received if r.text is not None]


def _handler(hook: Hook) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            text = chat = None
            if kind == "application/json":
                with contextlib.suppress(ValueError):
                    doc = json.loads(body)
                    text = doc.get("text") if isinstance(doc, dict) else None
            elif kind == "application/x-www-form-urlencoded":
                form = parse_qs(body.decode("utf-8"))
                text, chat = (form.get("text") or [None])[0], (form.get("chat_id") or [None])[0]
            hook.received.append(Received(self.path, kind, text, chat))
            if hook.redirect is not None:
                self.send_response(302)
                self.send_header("Location", hook.redirect)
            else:
                self.send_response(hook.status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            pass

    return Handler


@contextlib.contextmanager
def serve() -> Iterator[Hook]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)
    hook = Hook(f"http://127.0.0.1:{server.server_address[1]}")
    server.RequestHandlerClass = _handler(hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield hook
    finally:
        server.shutdown()
        server.server_close()
