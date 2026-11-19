# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""A stand-in for the two AI providers on 127.0.0.1, for the AI step's tests: a stdlib HTTP server at an ephemeral
port, run in a thread for as long as `serve()` is held, and reached by etl/ai/providers.py only through its one test
seam, FF_AI_TEST_ORIGIN set to this server's origin. Nothing it does leaves the loopback interface.

It answers the two request shapes the providers' SDKs send:
  POST /v1/messages          Anthropic's Messages API: a message whose one text block holds the answer, or a
                             refusal (stop_reason "refusal", no content), or an error object.
  POST /v1/chat/completions  OpenAI's Chat Completions API: one choice whose message content holds the answer, or a
                             refusal (content null, refusal text), or an error object.
Anything else is a 404.

Each request takes the next scripted reply (`answer`, `text`, `unsized`, `refusal`, `status`, `stall`, `trickle`,
`redirect`, each queued `times` times); with none left it answers an empty list. An error's body (`status`) and a refusal's text repeat what
the request carried, its key and its vendors included, as a careless provider might: the step must never print it.
`stall(seconds)` waits that long (or until the server stops) before answering: a stall past the client's timeout.

Every request is recorded in `received`: its path, the credential it carried (the x-api-key header or the bearer
token), the other headers it sent, and its JSON body. All data is invented.

Run as a program it is the stack test's stand-in (tools/verify/stack_ai.py), in a container sharing the refresher's
network namespace, so it is the refresher's own 127.0.0.1:
  fake_llm.py --port P --log FILE --category C [--subcategory S] [--confidence X]
listens on 127.0.0.1:P, answers every vendor each request asks about with that category, subcategory and confidence
when the request's answer set holds the pair (else `none`), appends each request to FILE as a JSON line (path,
credential, body), and prints `fake_llm: listening on http://127.0.0.1:P` once it listens."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANTHROPIC_PATH = "/v1/messages"
OPENAI_PATH = "/v1/chat/completions"


@dataclass(frozen=True)
class Received:
    path: str
    credential: str | None      # the x-api-key header, else the token of an Authorization: Bearer header
    headers: dict[str, str]     # every header but those two, names lower-cased
    body: dict


@dataclass(frozen=True)
class Reply:
    kind: str                   # answer | text | refusal | status | stall | redirect | each
    value: object = None
    headers: tuple[tuple[str, str], ...] = ()


def asked(body: dict) -> tuple[list[str], list[dict]]:
    """The vendors a request asks about and its answer set, read from its user message (the request body the AI step
    serialises there); ([], []) when the message is not one."""
    users = [m for m in body.get("messages") or () if isinstance(m, dict) and m.get("role") == "user"]
    try:
        sent = json.loads(users[-1]["content"])
        return list(sent["vendors"]), list(sent["answer_set"])
    except (IndexError, KeyError, TypeError, ValueError):
        return [], []


def each_answer(body: dict, category: str, subcategory: str | None, confidence: float) -> list[dict]:
    """An answer for every vendor `body` asks about: `category` / `subcategory` at `confidence` when its answer set
    holds that pair, else `none`."""
    vendors, answer_set = asked(body)
    held = any(c.get("category") == category and (subcategory in c.get("subcategories", ()) if subcategory
                                                  else not c.get("subcategories")) for c in answer_set)
    return [{"vendor": v, "category": category if held else "none", "subcategory": subcategory if held else None,
             "confidence": confidence} for v in vendors]


class FakeLLM:
    """The stand-in's script (what the next requests are answered with) and what it received. With the script
    spent, a request takes `default`: an empty list, unless a program set another."""

    def __init__(self, port: int):
        self.origin = f"http://127.0.0.1:{port}"
        self.received: list[Received] = []
        self._script: deque[Reply] = deque()
        self._lock = threading.Lock()
        self.stopped = threading.Event()
        self.default = Reply("answer", [])
        self.log: str | None = None             # a file each request is appended to, as a JSON line

    def _queue(self, reply: Reply, times: int) -> FakeLLM:
        with self._lock:
            self._script.extend([reply] * times)
        return self

    def answer(self, items: list, *, times: int = 1) -> FakeLLM:
        """Answer {"answers": items} as the structured output."""
        return self._queue(Reply("answer", items), times)

    def text(self, text: str, *, times: int = 1, headers: dict[str, str] | None = None) -> FakeLLM:
        """Answer `text` as the output, whatever it is (with `headers` added to the answer's)."""
        return self._queue(Reply("text", text, tuple((headers or {}).items())), times)

    def unsized(self, text: str, *, times: int = 1) -> FakeLLM:
        """Answer `text` as the output with no Content-Length: the body ends when the connection closes."""
        return self._queue(Reply("unsized", text), times)

    def trickle(self, every: float, *, times: int = 1) -> FakeLLM:
        """Announce a long answer, then send it a byte every `every` seconds (until the server stops): never silent
        long enough for a read timeout."""
        return self._queue(Reply("trickle", every), times)

    def refusal(self, *, times: int = 1) -> FakeLLM:
        return self._queue(Reply("refusal"), times)

    def status(self, code: int, *, times: int = 1, headers: dict[str, str] | None = None) -> FakeLLM:
        """Answer an HTTP error `code` whose message echoes the request."""
        return self._queue(Reply("status", code, tuple((headers or {}).items())), times)

    def stall(self, seconds: float, *, times: int = 1) -> FakeLLM:
        return self._queue(Reply("stall", seconds), times)

    def redirect(self, location: str, *, code: int = 307, times: int = 1) -> FakeLLM:
        return self._queue(Reply("redirect", code, (("Location", location),)), times)

    def each(self, category: str, subcategory: str | None, confidence: float, *, times: int = 1) -> FakeLLM:
        """Answer every vendor the request asks about with this pair at this confidence (`each_answer`)."""
        return self._queue(Reply("each", (category, subcategory, confidence)), times)

    def next_reply(self) -> Reply:
        with self._lock:
            return self._script.popleft() if self._script else self.default


def _anthropic_message(model: str, text: str | None) -> dict:
    content = [] if text is None else [{"type": "text", "text": text}]
    return {"id": "msg_fake_0001", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": "end_turn" if text is not None else "refusal", "stop_sequence": None,
            "stop_details": None if text is not None else {"type": "refusal", "category": None,
                                                            "explanation": "declined"},
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def _openai_completion(model: str, text: str | None, refusal: str | None = None) -> dict:
    return {"id": "chatcmpl-fake-0001", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                         "message": {"role": "assistant", "content": text, "refusal": refusal}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def _handler(fake: FakeLLM):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet: the tests read `received`
            pass

        def _send(self, code: int, doc: dict | None, headers: tuple[tuple[str, str], ...] = ()) -> None:
            data = b"" if doc is None else json.dumps(doc).encode()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):   # a client that gave up waiting
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            bearer = self.headers.get("Authorization") or ""
            credential = self.headers.get("x-api-key") or (bearer[7:] if bearer.startswith("Bearer ") else None)
            headers = {k.lower(): v for k, v in self.headers.items() if k.lower() not in ("x-api-key", "authorization")}
            fake.received.append(Received(self.path, credential, headers, body))
            if fake.log is not None:
                with fake._lock, open(fake.log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"path": self.path, "credential": credential, "body": body}) + "\n")
            if self.path not in (ANTHROPIC_PATH, OPENAI_PATH):
                self._send(404, {"error": {"message": "not found"}})
                return
            anthropic = self.path == ANTHROPIC_PATH
            model = str(body.get("model") or "fake-model")
            reply = fake.next_reply()
            # what a careless provider repeats: the request, credential and all
            echo = f"{credential} {json.dumps(body.get('messages'))}"
            if reply.kind == "stall":
                fake.stopped.wait(float(reply.value))
                reply = Reply("answer", [])
            if reply.kind == "each":
                reply = Reply("answer", each_answer(body, *reply.value), reply.headers)
            if reply.kind == "trickle":
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    for _ in range(100000):
                        if fake.stopped.wait(float(reply.value)):
                            break
                        self.wfile.write(b" ")
                        self.wfile.flush()
                self.close_connection = True
                return
            if reply.kind == "unsized":
                data = json.dumps(_anthropic_message(model, str(reply.value)) if anthropic
                                  else _openai_completion(model, str(reply.value))).encode()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                self.close_connection = True
                return
            if reply.kind == "status":
                error = ({"type": "error", "error": {"type": "api_error", "message": echo}} if anthropic
                         else {"error": {"message": echo, "type": "api_error", "code": None}})
                self._send(int(reply.value), error, reply.headers)
            elif reply.kind == "redirect":
                self._send(int(reply.value), None, reply.headers)
            elif reply.kind == "refusal":
                self._send(200, _anthropic_message(model, None) if anthropic
                           else _openai_completion(model, None, refusal=f"declined: {echo}"))
            else:
                text = json.dumps({"answers": reply.value}) if reply.kind == "answer" else str(reply.value)
                self._send(200, _anthropic_message(model, text) if anthropic else _openai_completion(model, text),
                           reply.headers)

    return Handler


@contextlib.contextmanager
def serve(port: int = 0) -> Iterator[FakeLLM]:
    """The stand-in, listening on 127.0.0.1 at `port` (an ephemeral one by default) while the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", port), None)
    fake = FakeLLM(server.server_address[1])
    server.RequestHandlerClass = _handler(fake)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        fake.stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    """The stand-in as a program (the module docstring's usage): it serves until it is stopped."""
    ap = argparse.ArgumentParser(description="the AI providers' stand-in, on 127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--subcategory")
    ap.add_argument("--confidence", type=float, default=0.95)
    args = ap.parse_args(argv)
    with serve(args.port) as fake:
        fake.default = Reply("each", (args.category, args.subcategory, args.confidence))
        fake.log = args.log
        print(f"fake_llm: listening on {fake.origin}", flush=True)
        fake.stopped.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
