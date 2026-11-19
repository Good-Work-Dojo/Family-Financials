# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The stack test's own logic, without Docker: the raw HTTP reader, the case runner and its checks, the verdicts on the
trusted-proxy answers, the stage's line (stack_matrix.combine), the browser run's pure helpers, and the AI steps'
verdicts (stack_ai.py: the compose copy, the stand-in's request, the rows filed, the status). Network code runs
against a server on 127.0.0.1 inside the test."""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stack_ai as ai  # noqa: E402  (tools/verify is not a package)
import stack_browser as browser  # noqa: E402
import stack_matrix as m  # noqa: E402

GOOD = [("content-security-policy", "default-src 'none'; script-src 'self'; frame-ancestors 'none'"),
        ("x-frame-options", "DENY"), ("x-content-type-options", "nosniff"), ("referrer-policy", "same-origin")]


def answer(status: int, body: bytes = b"", headers=GOOD) -> m.Response:
    return m.Response(status, "", list(headers), body)


def ctx_answering(*responses: m.Response, sent: list | None = None) -> m.Ctx:
    """A context whose `send` records the raw request and answers with `responses`."""
    def send(addr, raw, methods, expect, timeout=15.0):
        if sent is not None:
            sent.append(raw)
        return list(responses)
    ctx = m.Ctx(("127.0.0.1", 18299), "127.0.0.1:18299", passcode="invented-passcode", send=send)
    ctx.cookies[m.SESSION] = "ff_session=" + "a" * 43
    return ctx


# ---------------------------------------------------------------- reading answers

def test_responses_are_read_by_length_by_chunks_and_one_after_another():
    data = (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
            b"HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
    got = m.parse_responses(data, ["GET", "GET"], eof=False)
    assert [(r.status, r.body) for r in got] == [(200, b"ok"), (404, b"abcde")]


def test_an_answer_to_head_has_no_body_whatever_its_length_says():
    data = b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 30\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    got = m.parse_responses(data, ["HEAD", "GET"], eof=False)
    assert [r.status for r in got] == [401, 200]


def test_an_incomplete_answer_is_not_counted_and_one_without_a_length_ends_at_the_close():
    assert m.parse_responses(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nab", ["GET"], eof=False) == []
    assert m.parse_responses(b"HTTP/1.1 200 OK\r\n\r\nuntil", ["GET"], eof=False) == []
    assert m.parse_responses(b"HTTP/1.1 200 OK\r\n\r\nuntil", ["GET"], eof=True)[0].body == b"until"


def test_a_request_is_written_exactly_as_given():
    raw = m.build("GET", "/icons//../api/prefs", [("Host", "[::1]evil"), ("X-A", "1")], b"body")
    assert raw == b"GET /icons//../api/prefs HTTP/1.1\r\nHost: [::1]evil\r\nX-A: 1\r\n\r\nbody"


def test_exchange_reads_every_answer_a_server_sends_on_one_connection():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    got_request = []

    def serve() -> None:
        conn, _ = server.accept()
        got_request.append(conn.recv(65536))
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 3\r\n\r\nno!"
                     b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    got = m.exchange(server.getsockname(), b"GET / HTTP/1.1\r\nHost: x\r\n\r\n", ["GET", "GET"], 2, timeout=5)
    t.join()
    server.close()
    assert [r.status for r in got] == [400, 200]
    assert got_request[0].startswith(b"GET / HTTP/1.1")


def test_exchange_hears_an_answer_sent_before_a_large_body_is_read():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.1 413 Request Entity Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        conn.shutdown(socket.SHUT_WR)
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass
        conn.close()

    t = threading.Thread(target=serve)
    t.start()
    got = m.exchange(server.getsockname(), b"PUT / HTTP/1.1\r\nContent-Length: 3000000\r\n\r\n" + b"x" * 3_000_000,
                     ["PUT"], 1, timeout=5)
    t.join()
    server.close()
    assert [r.status for r in got] == [413]


# ---------------------------------------------------------------- the case runner

def test_a_case_passes_when_its_status_and_headers_are_right():
    case = m.Case("a public page", "/login")
    assert m.run_one(case, ctx_answering(answer(200, b"<form id=\"sign-in\">"))) == m.Outcome("ok", "a public page")


@pytest.mark.parametrize("response, words", [
    (answer(302), "status 302, want 200"),
    (answer(200, headers=GOOD[1:]), "no content-security-policy"),
    (answer(200, headers=[*GOOD[:1], ("x-frame-options", "SAMEORIGIN"), *GOOD[2:]]), "x-frame-options is"),
    (answer(200, headers=[*GOOD, ("server", "Caddy")]), "Server: Caddy"),
    (answer(200, b'<div id="root"></div>'), 'the body holds id="root"'),
    (answer(200, b'{"schema_version": 22}'), 'the body holds "schema_version"'),
])
def test_a_case_fails_on_a_wrong_status_a_missing_header_a_server_name_or_protected_content(response, words):
    out = m.run_one(m.Case("a public page", "/login"), ctx_answering(response))
    assert out.result == "FAIL" and words in out.detail


def test_protected_content_is_expected_only_with_a_valid_session():
    case = m.Case("the dashboard", "/", cookie=m.SESSION, check=m.body_holds(b'id="root"'))
    assert m.run_one(case, ctx_answering(answer(200, b'<div id="root"></div>'))).result == "ok"


def test_a_case_with_no_answer_or_one_that_raises_fails_and_the_run_goes_on():
    assert m.run_one(m.Case("silent"), ctx_answering()).detail == "no answer"

    def boom(ctx):
        raise ValueError("not a status line")
    out = m.run_one(m.Probe("raising", boom), ctx_answering())
    assert out.result == "FAIL" and "ValueError: not a status line" in out.detail


def test_a_known_case_that_fails_is_reported_as_known_and_one_that_passes_as_ok():
    case = m.Case("the folder", "/icons/", expect=m.expect(404), known="answered as a protected path")
    out = m.run_one(case, ctx_answering(answer(401)))
    assert out.result == "known" and out.line().startswith("known: the folder: answered as a protected path (")
    assert m.run_one(case, ctx_answering(answer(404))).result == "ok"


def test_a_redirect_must_go_to_the_sign_in_page():
    case = m.protected("/")[0]
    good = answer(302, headers=[*GOOD, ("location", "/login")])
    bad = answer(302, headers=[*GOOD, ("location", "https://evil.example.net/")])
    assert m.run_one(case, ctx_answering(good)).result == "ok"
    assert "Location" in m.run_one(case, ctx_answering(bad)).detail


def test_the_request_carries_the_host_the_cookie_state_and_a_length_as_the_case_says():
    sent: list[bytes] = []
    ctx = ctx_answering(answer(401), sent=sent)
    m.run_one(m.Case("put", "/api/prefs", "PUT", cookie=m.SESSION, body=b"{}", expect=m.expect(401)), ctx)
    m.run_one(m.Case("no host", "/login", host="", version="HTTP/1.0", expect=m.expect(401)), ctx)
    put, bare = sent
    assert b"Host: 127.0.0.1:18299\r\n" in put and b"Cookie: ff_session=" in put and b"Content-Length: 2\r\n" in put
    assert bare.startswith(b"GET /login HTTP/1.0\r\n") and b"Host:" not in bare and b"Cookie:" not in bare


def test_the_summary_counts_passed_cases_against_every_case():
    outs = [m.Outcome("ok", "a"), m.Outcome("known", "b", "why"), m.Outcome("FAIL", "c", "what")]
    assert m.summary(outs) == "stack matrix: 1/3"


# ---------------------------------------------------------------- the trusted proxy's verdicts

def echoes(clients, schemes, last_client="172.18.0.4", host="172.18.0.3:8080"):
    out = [{"status": 200, "ip": "172.18.0.3",
            "body": json.dumps({"X-FF-Client": c, "X-FF-Scheme": "http", "X-FF-Host": host})} for c in clients]
    out.append({"status": 200, "ip": "172.18.0.3",
                "body": json.dumps({"X-FF-Client": "198.51.100.9", "X-FF-Scheme": "http", "X-FF-Host": host})})
    out += [{"status": 200, "ip": "172.18.0.3",
             "body": json.dumps({"X-FF-Client": "172.18.0.5", "X-FF-Scheme": s, "X-FF-Host": host})} for s in schemes]
    out.append({"status": 200, "ip": "172.18.0.3",
                "body": json.dumps({"X-FF-Client": "172.18.0.5", "X-FF-Scheme": "http", "X-FF-Host": host})})
    out.append({"status": 200, "ip": "172.18.0.6",
                "body": json.dumps({"X-FF-Client": last_client, "X-FF-Scheme": "http", "X-FF-Host": host})})
    return out


def test_the_trusted_proxy_verdicts_pass_on_what_the_design_says():
    outs = m.trusted_proxy_verdicts(echoes(["192.0.2.77"] * 6, ["https", "http", "http"]))
    assert [o.result for o in outs] == ["ok"] * 7


def test_rotating_addresses_reaching_the_store_or_a_scheme_believed_fail():
    outs = m.trusted_proxy_verdicts(echoes([f"198.51.100.{k}" for k in range(1, 7)], ["https", "http", "https"]))
    assert [o.result for o in outs] == ["FAIL", "ok", "ok", "ok", "FAIL", "ok", "ok"]
    outs = m.trusted_proxy_verdicts(echoes(["192.0.2.77"] * 6, ["https", "http", "http"], last_client="198.51.100.9"))
    assert outs[-1].result == "FAIL"


def flood_result(**changes) -> dict:
    """What flood_main prints when the store does what the design says."""
    second = [[429, 0.02]] * 68 + [[401, 3.4]] * 30 + [[401, 0.3]] * 2
    doc = {"first_sign_in": 204, "device": True, "first_wave": [[401, 0.2]] * m.FLOOD_FIRST,
           "second_wave": second, "household": 204, "household_took": 0.4}
    doc.update(changes)
    return doc


def test_the_global_slowdown_verdict_passes_on_what_the_design_says():
    [out] = m.flood_verdicts(flood_result())
    assert out.result == "ok", out.detail


@pytest.mark.parametrize("changes, words", [
    ({"household": 429}, "got 429, want 204"),                          # the device's exemption gone: busy
    ({"household_took": 3.4}, "waited 3.4 s"),                          # the device slowed with the strangers
    ({"second_wave": [[401, 0.2]] * 100}, "no stranger waited"),         # the slowdown never reached
    ({"second_wave": [[401, 3.4]] * 100}, "no stranger was refused as busy"),
    ({"second_wave": [[204, 0.2]] + [[429, 0.0]] * 50 + [[401, 3.1]] * 49}, "the second wave: [204, 401, 429]"),
    ({"first_sign_in": 401, "device": False}, "no device cookie"),
])
def test_the_global_slowdown_verdict_fails_when_the_design_is_not_kept(changes, words):
    [out] = m.flood_verdicts(flood_result(**changes))
    assert out.result == "FAIL" and words in out.detail


# ---------------------------------------------------------------- the stage's line

MATRIX_OK = "ok a\nknown: b: why (what)\nok c\nstack matrix: 2/3\n"
BROWSER_OK = "signed in through the sign-in page\nstack browser: 19 routes, 0 violations\n"


def test_the_stage_line_counts_the_browser_run_as_one_case_and_lists_the_known_ones():
    text, ok = m.combine(MATRIX_OK, BROWSER_OK, 0, 0)
    assert ok and text.splitlines() == ["known: b: why (what)", "stack: 3/4"]


@pytest.mark.parametrize("matrix, browser, mrc, brc, line", [
    ("ok a\nFAIL c: what\nstack matrix: 1/2\n", BROWSER_OK, 1, 0, "stack: 2/3"),
    ("ok a\nFAIL c: what\nstack matrix: 1/2\n", BROWSER_OK, 0, 0, "stack: 2/3"),
    (MATRIX_OK, "FAIL console error: x\nstack browser: 19 routes, 1 problems\n", 0, 1, "stack: 2/4"),
    (MATRIX_OK, "stack browser: 19 routes, 0 violations\n", 0, 1, "stack: 2/4"),
    (MATRIX_OK, "Traceback (most recent call last):\n", 0, 1, "stack: 2/4"),
    ("ok a\n", BROWSER_OK, 1, 0, "stack: the matrix printed no summary"),
])
def test_the_stage_fails_on_a_failed_case_a_dirty_browser_run_or_a_missing_summary(matrix, browser, mrc, brc, line):
    text, ok = m.combine(matrix, browser, mrc, brc)
    assert not ok and text.splitlines()[-1] == line


# ---------------------------------------------------------------- the browser run's helpers

def test_only_the_stacks_own_origin_and_the_pages_own_urls_are_not_foreign():
    base = "http://127.0.0.1:8080"
    assert not browser.foreign("http://127.0.0.1:8080/assets/x.js", base)
    assert not browser.foreign("blob:http://127.0.0.1:8080/1234", base)
    assert not browser.foreign("data:image/png;base64,AAAA", base)
    for url in ("http://127.0.0.1:8081/", "https://127.0.0.1:8080/", "http://localhost:8080/", "https://example.net/x",
                "ws://127.0.0.1:8080/"):
        assert browser.foreign(url, base), url


def test_a_text_cell_that_begins_like_a_formula_is_found_and_a_negative_amount_is_not():
    row = browser.first_data_row("﻿date,description,amount\r\n2026-01-02,=HYPERLINK(1),-12.5\r\n")
    assert browser.formula_cells(row) == ["=HYPERLINK(1)"]
    clean = browser.first_data_row("date,description,amount\r\n2026-01-02,'=HYPERLINK(1),-12.5\r\n")
    assert browser.formula_cells(clean) == []
    assert browser.formula_cells(["@SUM(A1)", "+1 extra", "-x", "\tTAB", "plain"]) == ["@SUM(A1)", "+1 extra", "-x",
                                                                                        "\tTAB"]
    assert browser.first_data_row("only,a,header\r\n") == []


def test_the_control_counts_only_when_both_the_violation_and_the_console_error_were_heard():
    heard = ["CSP violation: script-src-elem blocked inline in http://127.0.0.1:8080/login",
             "console error: Refused to execute inline script"]
    assert browser.control_heard(heard)
    assert not browser.control_heard(heard[:1]) and not browser.control_heard(heard[1:]) and not browser.control_heard([])


def test_the_browser_line_is_clean_only_without_problems():
    assert browser.result_line(19, []) == "stack browser: 19 routes, 0 violations"
    assert browser.result_line(19, ["x"]) == "stack browser: 19 routes, 1 problems"


def test_the_ai_rows_are_shown_only_when_every_chip_sits_on_an_ai_vendors_row():
    vendors = ["Meridian Bike Works", "Fernhill Pottery Studio"]
    assert browser.ai_rows_shown(["2026-07-19 Meridian Bike Works AI $129.60"], vendors)
    assert not browser.ai_rows_shown([], vendors)
    assert not browser.ai_rows_shown(["Meridian Bike Works AI", "Studio 1824 Frames AI"], vendors)


# ---------------------------------------------------------------- the AI steps

AI_OK = "ok AI: one\nok AI: two\nstack ai on: 2/2\nok AI: three\nstack ai off: 1/1\n"


def test_the_stage_line_adds_the_ai_steps_cases():
    text, ok = m.combine(MATRIX_OK, BROWSER_OK, 0, 0, AI_OK, 0)
    assert ok and text.splitlines() == ["known: b: why (what)", "stack: 6/7"]


@pytest.mark.parametrize("ai, rc, line", [
    ("ok AI: one\nFAIL AI: two: what\nstack ai on: 1/2\nok AI: three\nstack ai off: 1/1\n", 1, "stack: 5/7"),
    ("ok AI: one\nFAIL AI: two: what\nstack ai on: 1/2\nok AI: three\nstack ai off: 1/1\n", 0, "stack: 5/7"),
    (AI_OK, 1, "stack: 6/7"),
    ("ok AI: one\nstack ai on: 1/1\n", 0, "stack: the AI steps printed no summary of each phase"),
    ("", 1, "stack: the AI steps printed no summary of each phase"),
])
def test_the_stage_fails_on_a_failed_ai_step_or_a_missing_phase(ai, rc, line):
    text, ok = m.combine(MATRIX_OK, BROWSER_OK, 0, 0, ai, rc)
    assert not ok and text.splitlines()[-1] == line


TRUTH = {
    "ai": {"sendable": ["fernhill pottery studio", "meridian bike works"],
           "withheld": {"alex paint and paper": "name", "studio 1824 frames": "digits", "quarry hill cider mill": "seen"},
           "not_candidate": {"jordanexampl": "payment_app", "bramble trust bal xfer": "internal"},
           "rows": {"fernhill pottery studio": ["tx_a1", "tx_a2"], "meridian bike works": ["tx_b1", "tx_b2"],
                    "alex paint and paper": ["tx_c1", "tx_c2"], "quarry hill cider mill": ["tx_d1"]},
           "preview": {"vendors": 2, "withheld": {"name": 1, "digits": 1, "address": 0, "length": 0, "seen": 1}}},
    "household": {"members": [{"name": "Alex"}, {"name": "Sam"}, {"name": "Robin"}, {"name": "Jamie"}],
                  "surname": "Sample", "joint_label": "Joint"},
}
KEY = "invented-stack-key"


def request(vendors=("fernhill pottery studio", "meridian bike works"), extra: str = "", credential: str = KEY) -> dict:
    sent = {"vendors": list(vendors), "answer_set": [{"category": "Entertainment", "subcategories": ["Hobbies"]}],
            "instructions": "Each item in vendors is the name of a shop." + extra, "policy_version": 1}
    return {"path": "/v1/messages", "credential": credential,
            "body": {"model": "claude-opus-5-5", "system": sent["instructions"],
                     "messages": [{"role": "user", "content": json.dumps(sent, indent=2)}]}}


def test_the_compose_copy_gives_the_refresher_alone_the_seam_and_keeps_its_home():
    text = (HERE.parents[1] / "compose.yaml").read_text("utf-8")
    copy = ai.seam_compose(text, "http://127.0.0.1:18218")
    assert copy.count(ai.SEAM) == 1 and copy.count("FF_HOME: /ff") == text.count("FF_HOME: /ff") + 1
    refresher = copy.split("\n  refresher:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert f'      {ai.SEAM}: "http://127.0.0.1:18218"\n' in refresher and "      FF_HOME: /ff\n" in refresher
    assert copy.replace(f'    environment:\n      FF_HOME: /ff\n      {ai.SEAM}: "http://127.0.0.1:18218"\n', "") == text
    no_env = text.replace("  environment:\n    FF_HOME: /ff\n", "", 1)
    for bad in (text.replace(ai.REFRESHER, ""), text + ai.REFRESHER, copy, no_env, text.replace(ai.WORKER, "")):
        with pytest.raises(ValueError):
            ai.seam_compose(bad, "http://127.0.0.1:18218")


def test_the_compose_copy_carries_every_variable_the_workers_share():
    """An explicit `environment:` replaces the merged one, so the refresher's must hold the workers' every entry."""
    text = (HERE.parents[1] / "compose.yaml").read_text("utf-8").replace(
        "  environment:\n    FF_HOME: /ff\n", '  environment:\n    FF_HOME: /ff\n    TZ: "UTC"\n', 1)
    assert ai.worker_environment(text) == ["FF_HOME: /ff", 'TZ: "UTC"']
    copy = ai.seam_compose(text, "http://127.0.0.1:18218")
    refresher = copy.split("\n  refresher:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert ('    environment:\n      FF_HOME: /ff\n      TZ: "UTC"\n'
            f'      {ai.SEAM}: "http://127.0.0.1:18218"\n') in refresher


class FakeRun:
    """A stand-in for stack_ai.Run: records what each step asks of compose and docker, in order, and answers
    `docker inspect` with the refresher's environment as given."""

    def __init__(self, tmp_path, env: list[str]):
        self.install, self.work = tmp_path / "install", tmp_path / "work"
        (self.install / "secrets" / "ai").mkdir(parents=True)
        self.work.mkdir()
        (self.install / "compose.yaml").write_text((HERE.parents[1] / "compose.yaml").read_text("utf-8"), "utf-8")
        self.env, self.calls = env, []

    @property
    def key_path(self):
        return self.install / "secrets" / "ai" / "api_key"

    def compose(self, *args, ai=False):
        self.calls.append(("compose-ai" if ai else "compose", *args, "key" if self.key_path.exists() else "no key"))
        return "abc123\n" if args[:2] == ("ps", "-q") else ""

    def docker(self, *args, check=True):
        self.calls.append(("docker", args[0], "key" if self.key_path.exists() else "no key"))
        return json.dumps(self.env) if args[0] == "inspect" else ""


def test_the_key_is_written_only_after_the_running_refresher_is_seen_holding_the_seam(tmp_path):
    run = FakeRun(tmp_path, ["PATH=/usr/bin", "FF_HOME=/ff", f"{ai.SEAM}=http://127.0.0.1:{ai.STANDIN_PORT}"])
    assert ai.seam_step(run) == []
    assert run.calls == [("compose", "stop", "scheduler", "no key"),
                         ("compose-ai", "up", "-d", "--wait", "refresher", "no key"),
                         ("compose", "ps", "-q", "refresher", "no key"), ("docker", "inspect", "no key")]
    assert run.key_path.read_text("utf-8").strip() == ai.KEY_TEXT and oct(run.key_path.stat().st_mode & 0o777) == "0o600"
    assert ai.SEAM in (run.install / ai.TEST_FILE).read_text("utf-8")


@pytest.mark.parametrize("env", [["FF_HOME=/ff"], [f"{ai.SEAM}=http://127.0.0.1:1"], [f"{ai.SEAM}="]])
def test_a_refresher_without_the_stand_ins_seam_gets_no_key(tmp_path, env):
    run = FakeRun(tmp_path, env)
    [outcome] = ai.run_phase(run, (("seam", ai.seam_step),))
    assert outcome.result == "FAIL" and "no key was written" in outcome.detail
    assert not run.key_path.exists()


def test_the_exports_compared_are_all_but_the_manifest_and_the_ai_status():
    manifest = {"files": ["accounts.json", "ai_status.json", "manifest.json", "transactions.json", "vendors.json"]}
    assert ai.compared_exports(manifest) == ["accounts.json", "transactions.json", "vendors.json"]
    assert ai.compared_exports({}) == []


def test_a_request_about_exactly_the_sendable_vendors_with_the_key_passes():
    assert ai.request_problems([request()], KEY, TRUTH) == []


@pytest.mark.parametrize("requests, words", [
    ([], "0 requests"),
    ([request(), request()], "2 requests"),
    ([request(credential="another")], "credential"),
    ([request(vendors=("meridian bike works",))], "it asked about"),
    ([request(vendors=("fernhill pottery studio", "meridian bike works", "alex paint and paper"))], "never sent"),
    ([request(extra=" Paid by Sam.")], "'sam'"),
    ([request(extra=" On 2026-03-02.")], "never sent"),
    ([request(extra=" For 12.50 dollars.")], "never sent"),
    ([request(extra=" Row tx_0123abcd.")], "never sent"),
    ([request(extra=" From demo_card_0404.")], "never sent"),
    ([request(extra=" Via jordanexampl.")], "'jordanexampl'"),
])
def test_a_request_beyond_the_sendable_vendors_or_holding_what_is_never_sent_fails(requests, words):
    assert any(words in p for p in ai.request_problems(requests, KEY, TRUTH))


def test_a_name_inside_another_word_is_no_household_name():
    assert ai.request_problems([request(extra=" Sampled, samosa and jointly.")], KEY, TRUTH) == []


def rows(filed: dict[str, tuple]) -> list[dict]:
    every = [uid for uids in TRUTH["ai"]["rows"].values() for uid in uids] + ["tx_z9"]
    return [{"id": uid, "category_source": "ai" if uid in filed else "fallback",
             "category": filed.get(uid, ("Cash & Misc",))[0],
             "subcategory": filed.get(uid, (None, "Uncategorized"))[1]} for uid in every]


def test_the_rows_filed_by_ai_are_exactly_the_sendable_vendors_rows_under_the_answer():
    good = dict.fromkeys(["tx_a1", "tx_a2", "tx_b1", "tx_b2"], ai.ANSWER)
    assert ai.filed_problems(rows(good), TRUTH) == []
    assert ai.filed_problems(rows({**good, "tx_c1": ai.ANSWER}), TRUTH)
    assert ai.filed_problems(rows({k: v for k, v in good.items() if k != "tx_b2"}), TRUTH)
    assert ai.filed_problems(rows({**good, "tx_a1": ("Shopping", "Gifts")}), TRUTH)


def test_the_status_on_and_off_is_what_the_truth_makes_it():
    on = {"configured": True, "enabled": True, "answers_stored": True, "applied": {"vendors": 2, "rows": 4},
          "last_run": {"outcome": "answered", "sent": 2, "answered": 2, "cached": 2}}
    assert ai.status_problems(on, TRUTH, on=True) == []
    assert ai.status_problems({**on, "applied": {"vendors": 2, "rows": 3}}, TRUTH, on=True)
    assert ai.status_problems({**on, "last_run": {**on["last_run"], "outcome": "malformed"}}, TRUTH, on=True)
    off = {"configured": True, "enabled": False, "answers_stored": False, "applied": {"vendors": 0, "rows": 0}}
    assert ai.status_problems(off, TRUTH, on=False) == []
    assert ai.status_problems({**off, "answers_stored": True}, TRUTH, on=False)


def test_a_step_after_a_failed_one_is_not_run_but_the_restore_always_is(monkeypatch):
    calls = []

    def step(name, problems):
        def fn(run):
            calls.append(name)
            if problems == "raise":
                raise RuntimeError("compose stop exited 1")
            return problems
        return fn

    monkeypatch.setattr(ai, "restore_step", step("restore", []))
    steps = (("a", step("a", [])), ("b", step("b", "raise")), ("c", step("c", [])), ("r", ai.restore_step))
    outcomes = ai.run_phase(None, steps)
    assert [o.result for o in outcomes] == ["ok", "FAIL", "FAIL", "ok"] and calls == ["a", "b", "restore"]
    assert outcomes[1].detail == "RuntimeError: compose stop exited 1"
    assert outcomes[2].detail == "not run: an earlier step failed"


def test_every_phase_ends_in_order_and_the_off_phase_restores_last():
    assert list(ai.PHASES) == ["on", "off"] and ai.PHASES["off"][-1][1] is ai.restore_step
    names = [name for steps in ai.PHASES.values() for name, _ in steps]
    assert len(set(names)) == len(names) and all(n.startswith("AI: ") for n in names)
