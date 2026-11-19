# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Scenario-store server (deploy/scenario_store.py): pure functions + a live
ThreadingHTTPServer exercised over real HTTP. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import http.client
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
import refresh_state as rs  # tests/conftest.py puts deploy/ on sys.path

from etl import manual_assets as engine_assets
from etl import networth as nw
from etl.models import ASSET_CLASSES, LIABILITY_CLASSES

_spec = importlib.util.spec_from_file_location(
    "scenario_store", Path(__file__).parents[1] / "deploy" / "scenario_store.py")
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def doc(n_scenarios: int = 1, updated_at: str = "2026-07-01T00:00:00Z") -> dict:
    return {"version": 1, "updated_at": updated_at,
            "scenarios": [{"id": f"id-{k}", "name": f"S{k}",
                           "created_at": updated_at, "updated_at": updated_at,
                           "inputs": {"price": 600000}, "draws": {}}
                          for k in range(n_scenarios)]}


# ---------------------------------------------------------------- pure core

def test_read_store_missing_file_is_empty(tmp_path):
    assert store.read_store(tmp_path / "nope.json") == {
        "version": 1, "updated_at": store.EPOCH_ISO, "scenarios": []}


def test_read_store_corrupt_self_heals(tmp_path, capsys):
    p = tmp_path / "scenarios.json"
    p.write_text("{ not json")
    assert store.read_store(p) == store.empty_store()
    assert "corrupt" in capsys.readouterr().err


def test_validate_store_accepts_full_doc():
    assert store.validate_store(json.dumps(doc(5)).encode()) == doc(5)


@pytest.mark.parametrize("bad,reason", [
    (b"not json", "JSON"),
    (json.dumps([1, 2]).encode(), "object"),
    (json.dumps({"version": 2, "updated_at": "x", "scenarios": []}).encode(), "version"),
    (json.dumps({"version": 1, "scenarios": []}).encode(), "updated_at"),
    (json.dumps({"version": 1, "updated_at": "x"}).encode(), "scenarios"),
    (json.dumps({"version": 1, "updated_at": "x", "scenarios": {}}).encode(), "list"),
    (json.dumps(doc(6)).encode(), "at most"),
])
def test_validate_store_rejects(bad, reason):
    with pytest.raises(ValueError, match=reason):
        store.validate_store(bad)


def test_write_read_roundtrip_no_tmp_residue(tmp_path):
    p = tmp_path / "scenarios.json"
    store.write_store(p, json.dumps(doc(2)).encode())
    assert store.read_store(p) == doc(2)
    store.write_store(p, json.dumps(doc(1)).encode())
    assert store.read_store(p) == doc(1)
    assert list(tmp_path.iterdir()) == [p]


# ---------------------------------------------------------------- live HTTP
# The documents and the refresh view, with the store's sign-in off (the --auth off a test or local development starts
# it with): tests/test_store_auth.py holds the sign-in, the session every /api/* route asks for and the Origin rule.
# Every write here carries the request's own Origin, as a browser's does (request()).

# A wall-clock start for the refresh tests, so "6 already used today" and
# "seconds until midnight" read as real times rather than 1970: noon in New York, the zone the scheduler's schedule in
# the refresh directory names (the household's).
ZONE = "America/New_York"
NOON_NY = dt.datetime(2026, 8, 11, 12, 0, tzinfo=ZoneInfo(ZONE)).timestamp()


@pytest.fixture()
def clock():
    c = FakeClock()
    c.t = NOON_NY
    return c


@pytest.fixture()
def refresh_dir(tmp_path):
    """The mailbox, holding the schedule the scheduler writes from household.yaml: New York's 01:00, 09:00, 17:00."""
    d = tmp_path / "refresh"
    d.mkdir()
    rs.write_schedule(d, rs.Schedule(ZONE, (1, 9, 17), 0))
    return d


@pytest.fixture()
def server(tmp_path, clock, refresh_dir):
    store.ScenarioHandler.store_dir = tmp_path
    store.ScenarioHandler.auth_required = False
    store.ScenarioHandler.signin = None
    store.ScenarioHandler.refresh_dir = refresh_dir
    store.ScenarioHandler.clock = clock
    srv = store.ThreadingHTTPServer(("127.0.0.1", 0), store.ScenarioHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def own_origin(srv) -> dict:
    """The Origin a browser on this page sends with a write (the store compares it with the Host header)."""
    return {"Origin": f"http://127.0.0.1:{srv.server_address[1]}"}


def request(srv, method: str, path: str, body: bytes | None = None,
            headers: dict | None = None):
    """One request; a write carries the request's own Origin unless `headers` name one."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    headers = {**(own_origin(srv) if method not in ("GET", "HEAD") else {}), **(headers or {})}
    conn.request(method, path, body=body, headers=headers)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res, data


def test_get_empty_store_with_headers(server):
    res, data = request(server, "GET", "/api/scenarios")
    assert res.status == 200
    assert res.getheader("Content-Type") == "application/json"
    assert res.getheader("Cache-Control") == "no-store"
    assert res.getheader("X-Content-Type-Options") == "nosniff"
    assert json.loads(data) == store.empty_store()


def test_put_get_roundtrip_and_last_write_wins(server):
    first, second = json.dumps(doc(1)).encode(), json.dumps(doc(3, "2026-07-02T00:00:00Z")).encode()
    res, data = request(server, "PUT", "/api/scenarios", first)
    assert res.status == 200
    assert json.loads(data) == doc(1)          # echoes what was stored
    request(server, "PUT", "/api/scenarios", second)
    _, data = request(server, "GET", "/api/scenarios")
    assert json.loads(data) == doc(3, "2026-07-02T00:00:00Z")


def test_put_rejections(server):
    res, _ = request(server, "PUT", "/api/scenarios", json.dumps(doc(6)).encode())
    assert res.status == 400
    res, _ = request(server, "PUT", "/api/scenarios", b"{ nope")
    assert res.status == 400
    res, _ = request(server, "PUT", "/api/scenarios", b"x" * (store.MAX_BYTES + 1))
    assert res.status == 413
    # rejected PUTs must not touch the file
    _, data = request(server, "GET", "/api/scenarios")
    assert json.loads(data) == store.empty_store()


def test_unknown_paths_404(server):
    for path in ("/api/other", "/api/scenarios/x", "/manifest.json", "/../../etc/passwd"):
        res, _ = request(server, "GET", path)
        assert res.status == 404, path
    res, _ = request(server, "PUT", "/api/other", b"{}")
    assert res.status == 404


def test_post_and_delete_405(server):
    assert request(server, "POST", "/api/scenarios", b"{}")[0].status == 405
    assert request(server, "DELETE", "/api/scenarios")[0].status == 405


def test_one_request_per_connection_so_an_unread_body_is_never_read_as_a_request(server):
    # every answer ends its connection, a read and a write alike: a second request needs a connection of its own
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("GET", "/api/scenarios")
    res = conn.getresponse()
    res.read()
    assert res.getheader("Connection") == "close" and res.will_close
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("PUT", "/api/scenarios", json.dumps(doc(1)).encode(), own_origin(server))
    res = conn.getresponse()
    assert res.status == 200 and res.getheader("Connection") == "close"
    conn.close()
    # a rejected PUT whose body was never read ends its connection too, so the leftover bytes are never parsed as
    # the next request line
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("PUT", "/api/other", b"{}", own_origin(server))
    res = conn.getresponse()
    assert res.status == 404
    assert res.getheader("Connection") == "close"
    conn.close()


# ------------------------------------------------- answers & prefs stores

def answer(key_target: str, scope: str, answered_at: str,
           category: str = "Dining", note: str | None = None) -> dict:
    a = {"key": f"{scope}:{key_target}", "scope": scope,
         "set": {"category": category, "subcategory": None, "needs_review": False},
         "answered_at": answered_at}
    a["txn_id" if scope == "txn" else "vendor"] = key_target
    if note is not None:
        a["note"] = note
    return a


def answers_doc(entries: list[dict], updated_at: str = "2026-07-16T10:00:00Z") -> dict:
    return {"version": 1, "updated_at": updated_at, "answers": entries}


# a server clock past every stamp the merge tests below write, so no stamp is clamped and only the merge rule shows
LATER = dt.datetime(2030, 1, 1, tzinfo=dt.UTC).timestamp()


def test_validate_answers_accepts_both_scopes():
    body = json.dumps(answers_doc([
        answer("tx_abc123", "txn", "2026-07-16T10:00:00Z", note="dinner"),
        answer("Cedar Hollow Diner", "vendor", "2026-07-16T10:01:00Z"),
    ])).encode()
    assert store.validate_answers(body)["answers"][1]["vendor"] == "Cedar Hollow Diner"


@pytest.mark.parametrize("mutate,reason", [
    (lambda a: a.__setitem__("scope", "account"), "scope"),
    (lambda a: a.__setitem__("key", "txn:other"), "key must equal"),
    (lambda a: a["set"].__setitem__("type", "transfer"), "not allowed"),   # no retyping via UI
    (lambda a: a.__setitem__("set", {}), "non-empty"),
    (lambda a: a.__setitem__("note", "x" * 501), "note"),
    (lambda a: a.pop("answered_at"), "answered_at"),
])
def test_validate_answers_rejects(mutate, reason):
    a = answer("tx_abc123", "txn", "2026-07-16T10:00:00Z")
    mutate(a)
    with pytest.raises(ValueError, match=reason):
        store.validate_answers(json.dumps(answers_doc([a])).encode())


def test_validate_answers_rejects_duplicate_keys():
    a = answer("tx_abc123", "txn", "2026-07-16T10:00:00Z")
    with pytest.raises(ValueError, match="duplicate"):
        store.validate_answers(json.dumps(answers_doc([a, dict(a)])).encode())


def test_merge_answers_union_newer_wins():
    older = answer("tx_a", "txn", "2026-07-16T10:00:00Z", category="Dining")
    newer = answer("tx_a", "txn", "2026-07-16T11:00:00Z", category="Travel")
    only_cur = answer("tx_b", "txn", "2026-07-16T09:00:00Z")
    only_inc = answer("v", "vendor", "2026-07-16T12:00:00Z")
    merged = store.merge_answers(
        answers_doc([older, only_cur], "2026-07-16T10:30:00Z"),
        answers_doc([newer, only_inc], "2026-07-16T12:00:00Z"), LATER)
    by_key = {a["key"]: a for a in merged["answers"]}
    assert set(by_key) == {"txn:tx_a", "txn:tx_b", "vendor:v"}   # union, nothing lost
    assert by_key["txn:tx_a"]["set"]["category"] == "Travel"     # newer wins per key
    assert merged["updated_at"] == "2026-07-16T12:00:00Z"


# A hand-edited answers.json the store did not write: a list, or any other value that is no object, reads as an empty
# document (one stderr line says so), and the PUT stores what it carries; never a 500.
NOT_A_DOCUMENT = [[], [answer("tx_a", "txn", "2026-07-16T10:00:00Z")], "answers", 7, None, True]


@pytest.mark.parametrize("stored", NOT_A_DOCUMENT, ids=repr)
def test_a_stored_answers_document_that_is_no_object_merges_as_an_empty_one(stored, capsys):
    incoming = answers_doc([answer("tx_b", "txn", "2026-07-16T11:00:00Z")], "2026-07-16T11:00:00Z")
    assert store.merge_answers(stored, incoming, LATER) == incoming
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and "answers.json" in err[0] and "empty" in err[0]


@pytest.mark.parametrize("answers", [{"txn:tx_a": {}}, "tx_a", 3, None], ids=repr)
def test_a_stored_answers_list_that_is_no_list_merges_as_no_answers(answers, capsys):
    incoming = answers_doc([answer("tx_b", "txn", "2026-07-16T11:00:00Z")])
    stored = {"version": 1, "updated_at": "2026-07-16T09:00:00Z", "answers": answers}
    assert store.merge_answers(stored, incoming, LATER)["answers"] == incoming["answers"]
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize("stored", ["[]", '["txn:tx_a"]', "7", "null", '"text"'])
def test_a_put_over_a_hand_edited_answers_file_is_stored_not_a_500(server, tmp_path, stored):
    (tmp_path / "answers.json").write_text(stored, "utf-8")
    sent = answers_doc([answer("tx_b", "txn", "2026-07-16T11:00:00Z")], "2026-07-16T11:00:00Z")
    res, data = request(server, "PUT", "/api/answers", json.dumps(sent).encode())
    assert res.status == 200 and json.loads(data) == sent
    assert json.loads((tmp_path / "answers.json").read_text("utf-8")) == sent


# The server's clock as the store writes a stamp it clamps: UTC with milliseconds, as the dashboard writes its own.
def iso_ms(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(t * 1000) % 1000:03d}Z"


def test_an_answer_stamped_ahead_of_the_server_is_stored_at_the_servers_clock():
    now = dt.datetime(2026, 8, 11, 17, 0, 0, 250_000, tzinfo=dt.UTC).timestamp()
    ahead = answer("tx_a", "txn", "2026-08-11T18:00:00.000Z")                  # a device an hour fast
    behind = answer("tx_b", "txn", "2026-08-11T16:59:59.000Z")
    merged = store.merge_answers(answers_doc([]), answers_doc([ahead, behind], "2026-08-11T18:00:00.000Z"), now)
    by_key = {a["key"]: a["answered_at"] for a in merged["answers"]}
    assert by_key == {"txn:tx_a": "2026-08-11T17:00:00.250Z", "txn:tx_b": "2026-08-11T16:59:59.000Z"}
    assert merged["updated_at"] == "2026-08-11T17:00:00.250Z"
    assert iso_ms(now) == "2026-08-11T17:00:00.250Z"


def test_a_device_whose_clock_runs_ahead_does_not_win_the_next_answer_for_its_row(server, clock):
    """Without the clamp the fast device's answer, an hour ahead, would beat every answer given for that row in the
    next hour, its own family's included."""
    fast = answers_doc([answer("tx_a", "txn", iso_ms(clock() + 3600), category="Travel")], iso_ms(clock() + 3600))
    assert request(server, "PUT", "/api/answers", json.dumps(fast).encode())[0].status == 200
    clock.advance(60)
    later = answers_doc([answer("tx_a", "txn", iso_ms(clock()), category="Dining")], iso_ms(clock()))
    res, data = request(server, "PUT", "/api/answers", json.dumps(later).encode())
    assert res.status == 200
    [stored] = json.loads(data)["answers"]
    assert (stored["set"]["category"], stored["answered_at"]) == ("Dining", iso_ms(clock()))


def test_a_stored_answer_dated_ahead_loses_to_a_real_re_answer_and_is_otherwise_rewritten_at_the_clock(server, tmp_path,
                                                                                                       clock):
    """An answer the file already holds dated ahead (a hand edit, or a device that ran fast before the store clamped
    stamps) loses to any re-answer of its row that is not ahead, stamped as a real one is, a moment before its PUT
    arrives; one no PUT names is brought to the server's clock with the next write, so it cannot hold its row until
    real time passes it."""
    held = answers_doc([answer("tx_a", "txn", "2099-01-01T00:00:00.000Z", category="Travel"),
                        answer("tx_c", "txn", "2099-01-01T00:00:00Z", category="Kids")], "2099-01-01T00:00:00Z")
    (tmp_path / "answers.json").write_text(json.dumps(held), "utf-8")
    answered = iso_ms(clock() - 1)
    sent = answers_doc([answer("tx_a", "txn", answered, category="Dining")], answered)
    res, data = request(server, "PUT", "/api/answers", json.dumps(sent).encode())
    assert res.status == 200
    doc = json.loads(data)
    assert {a["key"]: (a["set"]["category"], a["answered_at"]) for a in doc["answers"]} == {
        "txn:tx_a": ("Dining", answered), "txn:tx_c": ("Kids", iso_ms(clock()))}
    assert doc["updated_at"] == iso_ms(clock())


def test_with_the_stores_clock_behind_its_clients_two_stamps_ahead_keep_their_order():
    """A store whose clock lags the clients' sees every honest stamp ahead of it. Of two answers for a row, both ahead,
    the newer is kept whichever side holds it (an older one arriving late does not win a tie at the clock), and the
    one kept is written at the clock."""
    now = dt.datetime(2026, 8, 11, 17, 0, tzinfo=dt.UTC).timestamp()
    newer, older = iso_ms(now + 20), iso_ms(now + 10)
    stored = answers_doc([answer("tx_a", "txn", newer, category="Travel")], newer)
    late = answers_doc([answer("tx_a", "txn", older, category="Dining")], older)
    [kept] = store.merge_answers(stored, late, now)["answers"]
    assert (kept["set"]["category"], kept["answered_at"]) == ("Travel", iso_ms(now))
    later = answers_doc([answer("tx_a", "txn", iso_ms(now + 30), category="Kids")], iso_ms(now + 30))
    [kept] = store.merge_answers(stored, later, now)["answers"]
    assert (kept["set"]["category"], kept["answered_at"]) == ("Kids", iso_ms(now))


def test_two_stamps_not_ahead_merge_as_they_always_have():
    now = dt.datetime(2026, 8, 11, 17, 0, tzinfo=dt.UTC).timestamp()
    stored = answers_doc([answer("tx_a", "txn", iso_ms(now - 10), category="Travel")])
    for sent, wins in ((iso_ms(now - 20), "Travel"), (iso_ms(now - 10), "Dining"), (iso_ms(now - 5), "Dining")):
        [kept] = store.merge_answers(stored, answers_doc([answer("tx_a", "txn", sent, category="Dining")]),
                                     now)["answers"]
        assert kept["set"]["category"] == wins and kept["answered_at"] == max(sent, iso_ms(now - 10))


def test_a_stamp_that_is_no_time_but_sorts_ahead_is_clamped_by_the_merges_own_order():
    """The merge compares stamps as strings: any text that sorts after the server's clock would win as a stamp ahead
    does, so it is clamped the same way; text that sorts before it is left as it is."""
    now = dt.datetime(2026, 8, 11, 17, 0, tzinfo=dt.UTC).timestamp()
    merged = store.merge_answers(answers_doc([]), answers_doc([answer("tx_a", "txn", "zzz"),
                                                               answer("tx_b", "txn", "0000")]), now)
    assert {a["key"]: a["answered_at"] for a in merged["answers"]} == {"txn:tx_a": iso_ms(now), "txn:tx_b": "0000"}


def test_answers_put_merges_concurrent_clients(server):
    """Two family members answer different txns from stale copies of each other —
    neither answer may be lost, and each PUT echoes the merged doc."""
    a1 = answers_doc([answer("tx_1", "txn", "2026-07-16T10:00:00Z")])
    a2 = answers_doc([answer("tx_2", "txn", "2026-07-16T10:00:30Z")],
                     "2026-07-16T10:00:30Z")
    request(server, "PUT", "/api/answers", json.dumps(a1).encode())
    res, data = request(server, "PUT", "/api/answers", json.dumps(a2).encode())
    assert res.status == 200
    echoed = json.loads(data)
    assert {a["key"] for a in echoed["answers"]} == {"txn:tx_1", "txn:tx_2"}
    _, data = request(server, "GET", "/api/answers")
    assert {a["key"] for a in json.loads(data)["answers"]} == {"txn:tx_1", "txn:tx_2"}


# The answers cap, pinned against the app with ONE shared fixture (app/src/lib/answers.test.ts reads it too). It sits
# under app/src and the ETL image carries it (.dockerignore drops only app/node_modules and app/dist), so these never skip.
EVICTION_FIXTURE = Path(__file__).parents[1] / "app" / "src" / "lib" / "answers.eviction.fixture.json"
EVICTION = json.loads(EVICTION_FIXTURE.read_text("utf-8"))


def bulk_answers(n_vendor: int, n_txn: int) -> list[dict]:
    """The fixture's bulk_about rule (answers.test.ts bulkAnswers is the same): vendor answers first, then one-off
    ones, the n-th answer built stamped 2025-01-01T00:00:00.000Z + n seconds."""
    def stamp(n: int) -> str:
        return f"2025-01-01T{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}.000Z"
    out = [{"key": f"vendor:Synthetic Vendor {i:04d}", "scope": "vendor", "vendor": f"Synthetic Vendor {i:04d}",
            "set": {"tags": ["x"]}, "answered_at": stamp(i)} for i in range(n_vendor)]
    out += [{"key": f"txn:t{i:04d}", "scope": "txn", "txn_id": f"t{i:04d}", "set": {"tags": ["x"]},
             "answered_at": stamp(n_vendor + i)} for i in range(n_txn)]
    return out


@pytest.mark.parametrize("case", EVICTION["cap_cases"], ids=lambda c: c["name"])
def test_the_answers_cap_drops_only_the_oldest_one_off_answers_by_the_apps_rule(case):
    kept = store.cap_answers(case["answers"], case["limit"])
    assert sorted(a["key"] for a in kept) == sorted(case["kept"])
    assert [a["key"] for a in kept] == [a["key"] for a in case["answers"] if a["key"] in case["kept"]]   # own order kept


@pytest.mark.parametrize("case", EVICTION["bulk_cases"], ids=lambda c: c["name"])
def test_the_answers_cap_at_its_real_size_never_drops_a_vendor_answer(case):
    assert case["limit"] == store.MAX_ANSWERS
    answers = bulk_answers(case["vendor"], case["txn"])
    kept = {a["key"] for a in store.cap_answers(answers)}
    assert [a["key"] for a in answers if a["key"] not in kept] == case["evicted"]
    assert sum(k.startswith("vendor:") for k in kept) == case["vendor"]
    merged = store.merge_answers(answers_doc([]), answers_doc(answers), LATER)["answers"]     # the PUT path's cap
    assert {a["key"] for a in answers} - {a["key"] for a in merged} == set(case["evicted"])


def test_a_full_store_keeps_its_vendor_answers_when_a_one_off_answer_arrives(server):
    fx = EVICTION["full_store"]
    assert sum(a["scope"] == "txn" for a in fx["stored"]) == store.MAX_ANSWERS
    assert min(fx["stored"], key=lambda a: a["answered_at"])["scope"] == "vendor"     # the oldest IS vendor-scope
    res, _ = request(server, "PUT", "/api/answers", json.dumps(answers_doc(fx["stored"])).encode())
    assert res.status == 200
    res, data = request(server, "PUT", "/api/answers",
                        json.dumps(answers_doc(fx["incoming"], "2026-01-01T00:00:00.000Z")).encode())
    assert res.status == 200
    merged = json.loads(data)["answers"]
    assert len(merged) == len(fx["stored"])
    sent = {a["key"] for a in fx["stored"] + fx["incoming"]}
    assert sent - {a["key"] for a in merged} == set(fx["evicted"])
    assert all(a["scope"] == "txn" for a in fx["stored"] if a["key"] in fx["evicted"])
    # still stored oldest first, exactly as before the rule changed
    assert merged == sorted(merged, key=lambda a: (a["answered_at"], a["key"]))


def test_vendor_answers_past_max_answers_are_stored_and_more_than_max_one_off_answers_are_refused(server):
    many_vendors = bulk_answers(600, 10)
    res, data = request(server, "PUT", "/api/answers", json.dumps(answers_doc(many_vendors)).encode())
    assert res.status == 200 and len(json.loads(data)["answers"]) == 610
    _, data = request(server, "GET", "/api/answers")
    assert len(json.loads(data)["answers"]) == 610
    # a client caps its own one-off answers before it sends, so a doc past the cap is refused, never trimmed
    over = bulk_answers(0, store.MAX_ANSWERS + 1)
    res, data = request(server, "PUT", "/api/answers", json.dumps(answers_doc(over)).encode())
    assert res.status == 400 and "one-off" in json.loads(data)["error"]
    assert store.validate_answers(json.dumps(answers_doc(bulk_answers(900, store.MAX_ANSWERS))).encode())


def test_the_answers_store_has_its_own_one_mib_body_cap_and_the_other_stores_keep_theirs():
    assert store.MAX_ANSWERS_BYTES == 1024 * 1024
    assert store.STORES["/api/answers"].max_bytes == store.MAX_ANSWERS_BYTES
    assert store.STORES["/api/scenarios"].max_bytes == store.MAX_BYTES == 256 * 1024
    assert store.STORES["/api/prefs"].max_bytes == store.MAX_PREFS_BYTES == 64 * 1024


def test_one_answer_per_vendor_beside_a_full_set_of_one_off_answers_is_stored(server):
    # "Always for <vendor>" for every one of ~1,150 vendors plus MAX_ANSWERS one-off answers, in realistic shapes: well
    # past the 256 KB the other stores take, well inside the answers store's own cap
    def realistic(scope, i):
        target = f"tx_{i:016x}" if scope == "txn" else f"Synthetic Vendor Name {i:04d}"
        return {"key": f"{scope}:{target}", "scope": scope, ("txn_id" if scope == "txn" else "vendor"): target,
                "set": {"category": "Groceries", "subcategory": "Supermarket"}, "answered_at": "2026-10-05T12:34:56.789Z"}
    doc_ = answers_doc([realistic("vendor", i) for i in range(1150)] + [realistic("txn", i) for i in range(store.MAX_ANSWERS)])
    body = json.dumps(doc_).encode()
    assert store.MAX_BYTES < len(body) < store.MAX_ANSWERS_BYTES // 2
    res, data = request(server, "PUT", "/api/answers", body)
    assert res.status == 200 and len(json.loads(data)["answers"]) == 1150 + store.MAX_ANSWERS
    res, data = request(server, "PUT", "/api/answers", b"x" * (store.MAX_ANSWERS_BYTES + 1))     # one byte over
    assert res.status == 413 and str(store.MAX_ANSWERS_BYTES) in json.loads(data)["error"]
    _, data = request(server, "GET", "/api/answers")
    assert len(json.loads(data)["answers"]) == 1150 + store.MAX_ANSWERS                              # nothing written


def test_an_over_cap_body_is_read_before_the_413_so_a_proxy_relays_it(server, monkeypatch):
    # Caddy streams the body upstream; a 413 sent on a half-read request reaches the browser as a 502, which the client
    # retries as "unreachable" instead of reporting the refusal. So a body up to MAX_DRAIN_BYTES is read first, whole,
    # and the 413 then goes out (ending the connection, as every answer does); past it the store answers on the
    # declared length alone.
    drained = []
    discard = store.ScenarioHandler._discard
    monkeypatch.setattr(store.ScenarioHandler, "_discard", lambda self, n: (drained.append(n), discard(self, n)))
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("PUT", "/api/answers", body=b"x" * (store.MAX_ANSWERS_BYTES + 1), headers=own_origin(server))
    res = conn.getresponse()
    assert res.status == 413 and res.getheader("Connection") == "close"
    assert str(store.MAX_ANSWERS_BYTES) in json.loads(res.read())["error"], "the refusal arrives whole, in words"
    assert drained == [store.MAX_ANSWERS_BYTES + 1], "the whole body was read before the answer"
    conn.close()
    _, data = request(server, "GET", "/api/answers")
    assert json.loads(data) == store.empty_answers(), "nothing written"
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.putrequest("PUT", "/api/answers")
    conn.putheader("Origin", own_origin(server)["Origin"])
    conn.putheader("Content-Length", str(store.MAX_DRAIN_BYTES + 1))
    conn.endheaders()                                          # no body sent: the declared length alone is refused
    res = conn.getresponse()
    assert res.status == 413 and res.getheader("Connection") == "close"
    conn.close()


def test_vendor_answers_are_bounded_only_by_the_body_cap_and_a_merge_past_it_writes_nothing(server):
    # the answers doc's only bound on vendor answers is MAX_ANSWERS_BYTES: a merge that would pass it is a 413 the
    # dashboard reports (lib/answers.ts answersRefusalText), and the stored doc is untouched
    def vendors(n, first, pad):
        return [{"key": f"vendor:V{first + i:05d}", "scope": "vendor", "vendor": f"V{first + i:05d}",
                 "set": {"tags": ["x"]}, "note": pad, "answered_at": "2026-01-01T00:00:00.000Z"}
                for i in range(n)]
    pad = "n" * store.MAX_NOTE_LEN
    stored = vendors(1000, 0, pad)
    assert len(json.dumps(answers_doc(stored)).encode()) < store.MAX_ANSWERS_BYTES
    res, _ = request(server, "PUT", "/api/answers", json.dumps(answers_doc(stored)).encode())
    assert res.status == 200
    more = vendors(1000, 1000, pad)
    assert len(json.dumps(answers_doc(more)).encode()) < store.MAX_ANSWERS_BYTES          # the body alone fits ...
    res, data = request(server, "PUT", "/api/answers", json.dumps(answers_doc(more)).encode())
    assert res.status == 413 and "nothing was written" in json.loads(data)["error"]      # ... the merge does not
    _, data = request(server, "GET", "/api/answers")
    assert [a["key"] for a in json.loads(data)["answers"]] == [a["key"] for a in stored]


def test_prefs_roundtrip_and_validation(server):
    res, data = request(server, "GET", "/api/prefs")
    assert res.status == 200 and json.loads(data) == store.empty_prefs()
    doc_ = {"version": 1, "updated_at": "2026-07-16T10:00:00Z",
            "budgets": {"Groceries": 1200}, "payroll": {}}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(doc_).encode())
    assert res.status == 200 and json.loads(data) == doc_       # verbatim LWW echo
    res, _ = request(server, "PUT", "/api/prefs",
                     json.dumps({"version": 1, "updated_at": "x",
                                 "budgets": []}).encode())
    assert res.status == 400                                     # budgets must be object
    for bad in ({"saved_filters": {}}, {"reports": "nope"}):
        res, _ = request(server, "PUT", "/api/prefs",
                         json.dumps({"version": 1, "updated_at": "x", **bad}).encode())
        assert res.status == 400                                 # the array sections must be lists
    ok = {"version": 1, "updated_at": "x", "reports": [],
          "saved_filters": [{"id": "a", "name": "n", "query": "q=1", "created_at": "x"}]}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(ok).encode())
    assert res.status == 200 and json.loads(data) == ok          # entries pass verbatim (the client validates them)


def test_prefs_put_keeps_array_sections_an_old_bundle_omits(server):
    """A tab running an older bundle PUTs a prefs doc with no saved_filters / reports; the store
    must not read that as 'delete them' (it is the last writer for everything else it sends)."""
    entry = {"id": "a", "name": "n", "query": "q=1", "created_at": "x"}
    report = {"id": "r", "name": "r", "spec": {"measure": "spend"}, "created_at": "x"}
    full = {"version": 1, "updated_at": "2026-07-16T10:00:00Z", "budgets": {"Groceries": 1},
            "saved_filters": [entry], "reports": [report]}
    request(server, "PUT", "/api/prefs", json.dumps(full).encode())
    old_tab = {"version": 1, "updated_at": "2026-07-16T11:00:00Z", "budgets": {"Groceries": 2}}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old_tab).encode())
    assert res.status == 200
    kept = {**old_tab, "saved_filters": [entry], "reports": [report]}
    assert json.loads(data) == kept                              # the echo says what was stored
    _, data = request(server, "GET", "/api/prefs")
    assert json.loads(data) == kept                              # new fields won, old sections survived
    # an EXPLICIT empty list is a deliberate delete and still clears (only that section)
    clear = {"version": 1, "updated_at": "2026-07-16T12:00:00Z", "saved_filters": []}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(clear).encode())
    assert res.status == 200
    assert json.loads(data) == {**clear, "reports": [report]}
    # nothing stored yet -> nothing invented
    assert store.merge_prefs({"version": 1, "updated_at": "a"}, old_tab) == old_tab


def test_the_prefs_body_cap_is_the_64_kb_the_dashboard_budgets_under():
    """The dashboard refuses a save that would grow the prefs doc past MAX_PREFS_DOC_BYTES (app/src/lib/prefs.ts),
    which app/src/lib/prefs.test.ts ("the whole-doc budget") holds at least 8 KB under 64 * 1024 - because a PUT
    over THIS cap answers 413 and the client drops every prefs edit to local-only. This pins that 64 KB is the
    server's cap, so the two numbers cannot drift apart silently: change one and both tests name the other."""
    assert store.MAX_PREFS_BYTES == 64 * 1024
    assert store.STORES["/api/prefs"].max_bytes == store.MAX_PREFS_BYTES


def test_the_kept_list_sections_are_the_ones_the_dashboard_omits():
    """The dashboard leaves a saved list out of a prefs PUT when it did not change it (app/src/lib/prefs.ts
    SAVED_KEYS, sent through usePrefsStore's keepOmitted), trusting merge_prefs to keep the stored one. A
    section on one side only would be wiped by a stale tab (app-only) or never omitted (store-only)."""
    app = (Path(__file__).parents[1] / "app" / "src" / "lib" / "prefs.ts").read_text("utf-8")
    m = re.search(r"^export const SAVED_KEYS = \[([^\]]*)\] as const", app, re.M)
    assert m, "the SAVED_KEYS literal moved: update this pin"
    assert tuple(re.findall(r'"([a-z_]+)"', m[1])) == store.PREFS_LIST_SECTIONS


def test_the_kept_whole_sections_are_the_ones_the_dashboard_sends_only_when_changed():
    """The dashboard leaves a section it keeps whole (the target mix, the modules, the AI consent) out of a prefs PUT
    when it did not change it (app/src/lib/prefs.ts KEPT_WHOLE_KEYS, the keepWhole of usePrefsStore's PREFS_SYNC), and
    a bundle that predates one never sends it: merge_prefs keeps the stored copy of each (PREFS_KEPT_WHOLE). A section
    on the dashboard's side only would be erased by every save that omits it; on the store's side only, the dashboard
    would send it with every save, and a tab loaded before another device changed it would write its older copy back."""
    app = (Path(__file__).parents[1] / "app" / "src" / "lib" / "prefs.ts").read_text("utf-8")
    m = re.search(r"^export const KEPT_WHOLE_KEYS = \[([^\]]*)\] as const", app, re.M)
    assert m, "the KEPT_WHOLE_KEYS literal moved: update this pin"
    assert tuple(re.findall(r'"([a-z_]+)"', m[1])) == store.PREFS_KEPT_WHOLE
    sync = (Path(__file__).parents[1] / "app" / "src" / "data" / "usePrefsStore.ts").read_text("utf-8")
    assert re.search(r"^\s*keepWhole: KEPT_WHOLE_KEYS,$", sync, re.M), "PREFS_SYNC keeps another list whole"
    # the dashboard carries module ids it does not know through every save, within the store's own bounds, so a carried
    # section can never make a save the store refuses
    for name in ("MAX_MODULES", "MAX_MODULE_ID_LEN"):
        m = re.search(rf"^export const {name} = (\d+);", app, re.M)
        assert m, f"the {name} literal moved in app/src/lib/prefs.ts: update this pin"
        assert int(m[1]) == getattr(store, name), name


def test_the_manual_assets_editor_checks_the_store_s_own_limits():
    """The dashboard's editor refuses, before a save, what this store would refuse (app/src/lib/manualAssets.ts
    validateDraft): the live-asset and per-asset value caps, the id and name lengths, the value bound and the body cap.
    A limit on one side only would let the editor send a document the store answers 400 or 413, or refuse one it takes.
    Its load keeps an entry only it holds no longer than this store keeps a removal (mergeManualAssets)."""
    app = (Path(__file__).parents[1] / "app" / "src" / "lib" / "manualAssets.ts").read_text("utf-8")
    # TOMBSTONE_KEEP_DAYS: the browser drops an entry only it holds once it is older than the store's memory of removals
    pins = {"MAX_ASSETS": store.MAX_MANUAL_ASSETS, "MAX_VALUES": store.MAX_MANUAL_VALUES,
            "MAX_ID_LEN": store.MAX_MANUAL_ID_LEN, "MAX_NAME_LEN": store.MAX_MANUAL_NAME_LEN,
            "TOMBSTONE_KEEP_DAYS": store.TOMBSTONE_KEEP_DAYS}
    for name, want in pins.items():
        m = re.search(rf"^export const {name} = (\d+);", app, re.M)
        assert m, f"the {name} literal moved in app/src/lib/manualAssets.ts: update this pin"
        assert int(m[1]) == want, name
    assert re.search(r"^const MAX_LABEL_LEN = (\d+);", app, re.M)[1] == str(store.MAX_MANUAL_LABEL_LEN)
    assert re.search(r"^export const MAX_VALUE = 1e12;", app, re.M) and store.MAX_MANUAL_VALUE == 1e12
    assert re.search(r"^export const MAX_DOC_BYTES = 512 \* 1024;", app, re.M) and store.MAX_MANUAL_BYTES == 512 * 1024


def test_a_merge_that_would_pass_the_cap_is_a_413_and_writes_nothing(server):
    """The 64 KB cap is checked on the MERGED doc too: a PUT under the cap that omits the stored lists (an older
    bundle, or the current one when it did not change them) must not grow the stored doc past the cap, or every
    later full PUT of it would 413."""
    entry = {"id": "", "name": "n", "query": "x" * 3900, "created_at": "x"}
    lists = {"version": 1, "updated_at": "2026-07-16T10:00:00Z",
             "saved_filters": [{**entry, "id": f"v{i}"} for i in range(15)]}
    body = json.dumps(lists).encode()
    assert len(body) < store.MAX_PREFS_BYTES
    assert request(server, "PUT", "/api/prefs", body)[0].status == 200
    budgets = {"version": 1, "updated_at": "2026-07-16T11:00:00Z",
               "budgets": {f"Category {i:04d}": i for i in range(400)}}
    body = json.dumps(budgets).encode()
    assert len(body) < store.MAX_PREFS_BYTES and len(json.dumps({**budgets, **{"saved_filters": lists["saved_filters"]}})) > store.MAX_PREFS_BYTES
    res, data = request(server, "PUT", "/api/prefs", body)
    assert res.status == 413
    assert json.loads(data)["error"].startswith("the document would be ")
    _, data = request(server, "GET", "/api/prefs")
    assert json.loads(data) == lists                                   # nothing was written
    res, _ = request(server, "PUT", "/api/prefs", json.dumps({**budgets, "saved_filters": []}).encode())
    assert res.status == 200                                           # the same edit fits once the lists shrink


def test_prefs_merge_ignores_a_corrupt_current_doc():
    old = {"version": 1, "updated_at": "x", "budgets": {}}
    assert store.merge_prefs({}, old) == old
    assert store.merge_prefs([], old) == old                     # read_store can hand back any JSON
    assert store.merge_prefs({"saved_filters": "junk"}, old) == old   # a non-list is not a section to keep


def test_prefs_refuses_an_oversize_list_entry(server):
    """Per-entry cap (the 64 KB doc cap alone would let a few huge views fill it): one entry over
    MAX_SAVED_ENTRY_BYTES is a 400, whichever list it is in; the cap is on the entry, not the list."""
    def compact(e: dict) -> int:
        return len(json.dumps(e, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    base = {"id": "a", "name": "n", "query": "", "created_at": "x"}
    fits = {**base, "query": "x" * (store.MAX_SAVED_ENTRY_BYTES - compact(base))}
    assert compact(fits) == store.MAX_SAVED_ENTRY_BYTES
    over = {**fits, "query": fits["query"] + "x"}
    for section in ("saved_filters", "reports"):
        res, data = request(server, "PUT", "/api/prefs", json.dumps(
            {"version": 1, "updated_at": "x", section: [fits, over]}).encode())
        assert res.status == 400 and section in json.loads(data)["error"]
    res, _ = request(server, "PUT", "/api/prefs", json.dumps(
        {"version": 1, "updated_at": "x", "saved_filters": [fits] * 3, "reports": [fits]}).encode())
    assert res.status == 200                       # several entries, each at the cap, are fine


def test_prefs_saved_reports_are_stored_verbatim_kept_when_omitted_and_capped_per_entry(server):
    """Saved reports (prefs.reports): the entry carries a whole spec, the client validates it, and the store
    only enforces the shape rules every list section shares - a list, an entry under the per-entry cap, and
    an omitted section surviving an old bundle's PUT."""
    spec = {"filters": "categories=Dining&from=2026-01-01", "groupBy": "vendor", "interval": "quarter",
            "measure": "count", "chart": "table"}
    report = {"id": "r1", "name": "Dining by vendor", "spec": spec, "created_at": "2026-10-03T10:00:00Z"}
    view = {"id": "v1", "name": "Coffee", "query": "q=coffee", "created_at": "2026-10-03T10:00:00Z"}
    doc_ = {"version": 1, "updated_at": "2026-10-03T10:00:00Z", "saved_filters": [view], "reports": [report]}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(doc_).encode())
    assert res.status == 200 and json.loads(data) == doc_        # the whole spec rides verbatim
    _, data = request(server, "GET", "/api/prefs")
    assert json.loads(data)["reports"] == [report]

    # a tab running a bundle that predates reports PUTs without the section: the reports are kept
    old_tab = {"version": 1, "updated_at": "2026-10-03T11:00:00Z", "saved_filters": []}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old_tab).encode())
    assert res.status == 200 and json.loads(data) == {**old_tab, "reports": [report]}
    # an explicit empty list is a deliberate delete of every report
    res, data = request(server, "PUT", "/api/prefs",
                        json.dumps({**old_tab, "updated_at": "2026-10-03T12:00:00Z", "reports": []}).encode())
    assert res.status == 200 and json.loads(data)["reports"] == []

    # a section that is not a list is refused, and so is one entry over the per-entry cap (the entry, not the list)
    for bad in ({"reports": {"r1": report}}, {"reports": None}):
        res, _ = request(server, "PUT", "/api/prefs",
                         json.dumps({"version": 1, "updated_at": "x", **bad}).encode())
        assert res.status == 400

    def compact(e: dict) -> int:
        return len(json.dumps(e, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    pad = store.MAX_SAVED_ENTRY_BYTES - compact(report)
    fits = {**report, "spec": {**spec, "filters": spec["filters"] + "x" * pad}}
    assert compact(fits) == store.MAX_SAVED_ENTRY_BYTES
    over = {**fits, "spec": {**fits["spec"], "filters": fits["spec"]["filters"] + "x"}}
    res, data = request(server, "PUT", "/api/prefs",
                        json.dumps({"version": 1, "updated_at": "x", "reports": [report, over]}).encode())
    assert res.status == 400 and "reports[1]" in json.loads(data)["error"]
    res, _ = request(server, "PUT", "/api/prefs",
                     json.dumps({"version": 1, "updated_at": "x", "reports": [fits] * 3}).encode())
    assert res.status == 200                                      # several entries, each exactly at the cap


def test_prefs_target_mix_is_validated_kept_when_omitted_and_cleared_by_null(server):
    """The target mix (prefs.target_mix, app/src/lib/targetMix.ts) is null or a small object of percentages. The store checks
    only that shape; the client checks the four buckets total 100. A tab on a bundle that predates the section PUTs without
    it, so the store keeps the stored mix, the way it keeps the saved lists; an explicit null clears it."""
    mix = {"stocks": 64, "bonds": 26, "cash": 5, "alts": 5}
    doc_ = {"version": 1, "updated_at": "2026-10-05T10:00:00Z", "budgets": {"Groceries": 1}, "targets": {},
            "target_mix": mix, "projections": {}, "payroll": {"phases": []}, "saved_filters": [], "reports": []}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(doc_).encode())
    assert res.status == 200 and json.loads(data) == doc_                    # verbatim, as the client wrote it
    assert request(server, "PUT", "/api/prefs", json.dumps({**doc_, "target_mix": {"stocks": 33.5, "bonds": 66.5}}).encode())[0].status == 200

    # an older bundle's PUT (no target_mix): the stored mix survives it, and the rest of its doc wins as before
    request(server, "PUT", "/api/prefs", json.dumps(doc_).encode())
    old_tab = {"version": 1, "updated_at": "2026-10-05T11:00:00Z", "budgets": {"Groceries": 2}}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old_tab).encode())
    assert res.status == 200
    assert json.loads(data) == {**old_tab, "saved_filters": [], "reports": [], "target_mix": mix}
    # an explicit null is a deliberate clear
    res, data = request(server, "PUT", "/api/prefs", json.dumps({**old_tab, "target_mix": None}).encode())
    assert res.status == 200 and json.loads(data)["target_mix"] is None
    # a doc that never had one stays without one (nothing invented), and a non-object stored value is not kept
    assert "target_mix" not in store.merge_prefs({"version": 1, "updated_at": "a"}, old_tab)
    assert "target_mix" not in store.merge_prefs({"target_mix": "junk"}, old_tab)

    junk = [[64, 26, 5, 5], "mix", 5, {"stocks": -1}, {"stocks": 100.5}, {"stocks": True}, {"stocks": "64"},
            {"stocks": None}, {f"k{i}": 1 for i in range(store.MAX_TARGET_MIX_KEYS + 1)},
            {"x" * (store.MAX_TARGET_MIX_KEY_LEN + 1): 1}]
    for bad in junk:
        res, data = request(server, "PUT", "/api/prefs", json.dumps({**old_tab, "target_mix": bad}).encode())
        assert res.status == 400 and "target_mix" in json.loads(data)["error"], bad
    for token in ("NaN", "Infinity", "-Infinity"):                           # json.loads accepts the bare tokens
        body = ('{"version": 1, "updated_at": "x", "target_mix": {"stocks": %s}}' % token).encode()
        res, _ = request(server, "PUT", "/api/prefs", body)
        assert res.status == 400, token
    _, data = request(server, "GET", "/api/prefs")
    assert json.loads(data)["target_mix"] is None                           # no refused PUT wrote anything


def test_an_old_client_without_target_mix_still_saves(server):
    """A doc with no target_mix at all (every bundle before the section, and the store's own empty doc) is a valid PUT."""
    old = {"version": 1, "updated_at": "2026-10-05T10:00:00Z", "budgets": {"Dining": 300}, "targets": {"Cash": 10},
           "projections": {}, "payroll": {"phases": []}}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old).encode())
    assert res.status == 200 and json.loads(data) == old
    assert store.validate_prefs(json.dumps(store.empty_prefs()).encode()) == store.empty_prefs()


def test_an_old_tab_cannot_erase_modules_or_ai(server):
    """Which modules are on (prefs.modules) and the AI consent (prefs.ai) are kept whole from the stored doc when a PUT
    omits them, as the target mix is: a tab on a bundle that predates them sends neither, and its PUT must not switch a
    module off or withdraw a consent. A section the PUT carries replaces the stored one; `modules: {}` clears it."""
    modules = {"home_buying": True}
    ai = {"enabled": True, "consent_policy": 2, "consent_scope": "anthropic||cards",
          "consented_at": "2026-10-05T09:00:00.000Z"}
    mix = {"stocks": 60, "bonds": 40}
    new_tab = {"version": 1, "updated_at": "2026-10-05T10:00:00Z", "budgets": {"Groceries": 1}, "target_mix": mix,
               "modules": modules, "ai": ai}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(new_tab).encode())
    assert res.status == 200 and json.loads(data) == new_tab
    old_tab = {"version": 1, "updated_at": "2026-10-05T11:00:00Z", "budgets": {"Groceries": 2}}
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old_tab).encode())
    kept = {**old_tab, "target_mix": mix, "modules": modules, "ai": ai}
    assert res.status == 200 and json.loads(data) == kept          # the old tab's budgets win, the three sections stay
    _, data = request(server, "GET", "/api/prefs")
    assert json.loads(data) == kept

    # a section the PUT carries replaces the stored one: {} is how a newer bundle clears the modules
    withdrawn = {"enabled": False, "consent_policy": None, "consent_scope": None, "consented_at": None}
    res, data = request(server, "PUT", "/api/prefs", json.dumps({**old_tab, "modules": {}, "ai": withdrawn}).encode())
    assert res.status == 200 and (json.loads(data)["modules"], json.loads(data)["ai"]) == ({}, withdrawn)
    res, data = request(server, "PUT", "/api/prefs", json.dumps(old_tab).encode())
    assert res.status == 200 and (json.loads(data)["modules"], json.loads(data)["ai"]) == ({}, withdrawn)

    # nothing stored: nothing invented; a stored value of the wrong type or shape is not carried
    assert store.PREFS_KEPT_WHOLE == ("target_mix", "modules", "ai")
    assert store.merge_prefs(store.empty_prefs(), old_tab) == old_tab
    for junk in ("on", ["home_buying"], None, 1, {"home_buying": "yes"}):
        assert "modules" not in store.merge_prefs({"modules": junk}, old_tab), junk
    for junk in ("on", [], None, {"enabled": True}, {**ai, "consent_policy": True}):
        assert "ai" not in store.merge_prefs({"ai": junk}, old_tab), junk


def test_modules_and_ai_are_type_checked(server):
    """The store checks the two sections' shape only: `modules` is an object of at most MAX_MODULES short ids, each true
    or false; `ai` holds exactly enabled (true or false), consent_policy (a whole number, never a bool, or null),
    consent_scope (text of at most AI_SCOPE_MAX characters, or null) and consented_at (a string or null). A refused PUT
    is a 400 naming the section and writes nothing."""
    base = {"version": 1, "updated_at": "2026-10-05T10:00:00Z"}
    ai = {"enabled": True, "consent_policy": 1, "consent_scope": "openai|127.0.0.1:11434|all",
          "consented_at": "2026-10-05T09:00:00.000Z"}
    accepted = [
        ("modules", {}), ("modules", {"home_buying": False, "m" * store.MAX_MODULE_ID_LEN: True}),
        ("modules", {f"m{i}": True for i in range(store.MAX_MODULES)}),
        ("ai", ai), ("ai", {**ai, "consent_policy": 0}),
        ("ai", {"enabled": False, "consent_policy": None, "consent_scope": None, "consented_at": None}),
        ("ai", {**ai, "consent_scope": "s" * store.AI_SCOPE_MAX}),
    ]
    for section, value in accepted:
        res, data = request(server, "PUT", "/api/prefs", json.dumps({**base, section: value}).encode())
        assert res.status == 200 and json.loads(data)[section] == value, (section, value)
    _, before = request(server, "GET", "/api/prefs")

    refused = [
        ("modules", m) for m in ([], "home_buying", None, 1, {"home_buying": 1}, {"home_buying": "true"},
                                 {"home_buying": None}, {f"m{i}": True for i in range(store.MAX_MODULES + 1)},
                                 {"m" * (store.MAX_MODULE_ID_LEN + 1): True})
    ] + [
        ("ai", a) for a in (None, [], "on", {"enabled": True}, {**ai, "extra": 1},
                            {k: v for k, v in ai.items() if k != "consented_at"},
                            {**ai, "enabled": "yes"}, {**ai, "enabled": 1}, {**ai, "enabled": None},
                            {**ai, "consent_policy": True}, {**ai, "consent_policy": False},
                            {**ai, "consent_policy": 1.5}, {**ai, "consent_policy": "1"},
                            {**ai, "consented_at": 5}, {**ai, "consented_at": True},
                            {k: v for k, v in ai.items() if k != "consent_scope"}, {**ai, "consent_scope": 7},
                            {**ai, "consent_scope": "s" * (store.AI_SCOPE_MAX + 1)})
    ]
    for section, value in refused:
        res, data = request(server, "PUT", "/api/prefs", json.dumps({**base, section: value}).encode())
        assert res.status == 400 and json.loads(data)["error"].startswith(section), (section, value, data)
    _, after = request(server, "GET", "/api/prefs")
    assert after == before                                              # no refused PUT wrote anything
    with pytest.raises(ValueError, match="consent_policy"):            # the validator itself refuses a bool policy
        store.validate_prefs(json.dumps({**base, "ai": {**ai, "consent_policy": True}}).encode())


def _valid_body(path: str) -> dict:
    return {"/api/scenarios": doc(1), "/api/answers": answers_doc([answer("tx_1", "txn", "2026-07-16T10:00:00Z")]),
            "/api/prefs": {"version": 1, "updated_at": T0, "budgets": {}},
            "/api/manual-assets": manual_doc(asset())}[path]


@pytest.mark.parametrize("path", ["/api/scenarios", "/api/answers", "/api/prefs", "/api/manual-assets"])
def test_every_document_refuses_text_that_is_not_unicode_and_numbers_that_are_not_finite(server, tmp_path, path):
    """A lone surrogate (JSON "\\ud800", or its raw bytes) anywhere, as a value or as a key, is text no database or
    browser takes: stored, it would stop every ingest. NaN, Infinity and a number too large for a float, anywhere, even
    in a key the store does not know, would leave a stored document the browser cannot parse. Each is a 400 and the
    stored file is untouched."""
    base = _valid_body(path)
    assert request(server, "PUT", path, json.dumps(base).encode())[0].status == 200
    stored = tmp_path / store.STORES[path].filename
    before = stored.read_bytes()
    text = "the document holds text that is not valid Unicode"
    number = "the document holds a number that is not finite (NaN or infinity)"
    refused = [
        (json.dumps({**base, "extra": {"deep": ["ok", "\ud800"]}}).encode(), text),
        (json.dumps({**base, "extra": {"\udfff": 1}}).encode(), text),
        (json.dumps({**base, "extra": "raw-surrogate"}).encode().replace(b"raw-surrogate", b"\xed\xa0\x80"), text),
        (json.dumps({**base, "extra": {"deep": [float("nan")]}}).encode(), number),
        (json.dumps({**base, "extra": [float("inf")]}).encode(), number),
        (json.dumps({**base, "extra": [float("-inf")]}).encode(), number),
        (json.dumps({**base, "extra": [1.5]}).encode().replace(b"1.5", b"1e400"), number),
    ]
    for body, message in refused:
        res, data = request(server, "PUT", path, body)
        assert res.status == 400 and json.loads(data)["error"] == message, (path, body, data)
        assert stored.read_bytes() == before
    # a well-formed character outside the Basic Multilingual Plane is text like any other
    res, _ = request(server, "PUT", path, json.dumps({**base, "extra": "\U0001f3e0 home"}).encode())
    assert res.status == 200


@pytest.mark.parametrize("path", ["/api/scenarios", "/api/answers", "/api/prefs", "/api/manual-assets"])
def test_a_stored_file_the_parser_refuses_is_corrupt_and_the_next_save_heals_it(server, tmp_path, path, capsys):
    """A hand-edited stored file holding an integer of 5,000 digits (past the interpreter's limit for reading one: a
    ValueError, not a decode error) is a corrupt file like any other, for every document: GET serves the store's empty
    document with a stderr line, and the next valid PUT is stored (merged with nothing), healing the file."""
    spec = store.STORES[path]
    stored = tmp_path / spec.filename
    stored.write_text('{"version": 1, "updated_at": "2026-07-01T00:00:00Z", "n": ' + "7" * 5000 + "}", "utf-8")
    res, data = request(server, "GET", path)
    assert res.status == 200 and json.loads(data) == spec.empty()
    assert "corrupt" in capsys.readouterr().err
    res, data = request(server, "PUT", path, json.dumps(_valid_body(path)).encode())
    assert res.status == 200, data
    healed = json.loads(stored.read_text("utf-8"))
    assert healed == json.loads(data) and "n" not in healed
    res, data = request(server, "GET", path)
    assert res.status == 200 and json.loads(data) == healed


def test_a_merge_refuses_with_its_own_error_which_is_a_400(server):
    """What a merge itself refuses (here a store holding MAX_MANUAL_ASSETS live assets and a tab adding one more) is a
    MergeRefused, which the handler answers with a 400 naming it; nothing is written."""
    full = manual_doc(*(asset(f"asset-{k:02d}", name=f"Asset {k}") for k in range(store.MAX_MANUAL_ASSETS)))
    one_more = manual_doc(asset("asset-50", name="Asset 50"))
    with pytest.raises(store.MergeRefused, match="at most 50 assets"):
        store.merge_manual_assets(full, one_more, NOON_NY)
    assert issubclass(store.MergedTooLarge, store.MergeRefused)
    assert put_manual(server, full)[0] == 200
    status, body = put_manual(server, one_more)
    assert status == 400 and "at most 50 assets" in body["error"]
    assert get_manual(server)["assets"] == full["assets"]


def test_stores_are_isolated(server):
    request(server, "PUT", "/api/scenarios", json.dumps(doc(1)).encode())
    request(server, "PUT", "/api/answers",
            json.dumps(answers_doc([answer("tx_1", "txn", "2026-07-16T10:00:00Z")])).encode())
    _, data = request(server, "GET", "/api/scenarios")
    assert "answers" not in json.loads(data)
    _, data = request(server, "GET", "/api/answers")
    assert "scenarios" not in json.loads(data)


def test_stores_are_enumerated():
    """The store serves exactly these documents, each its own file in the store's directory (a path outside the map is
    a 404 and never names a file)."""
    assert list(store.STORES) == ["/api/scenarios", "/api/answers", "/api/prefs", "/api/manual-assets"]
    assert [s.filename for s in store.STORES.values()] == [
        "scenarios.json", "answers.json", "prefs.json", "manual_assets.json"]
    spec = store.STORES["/api/manual-assets"]
    assert (spec.validate, spec.max_bytes, spec.empty, spec.merge) == (
        store.validate_manual_assets, store.MAX_MANUAL_BYTES, store.empty_manual_assets, store.merge_manual_assets)


# ----------------------------------------------------------- manual assets
# The assets the family enters in the app (manual_assets.json, which etl/manual_assets.py reads). Invented assets only.

MANUAL = "/api/manual-assets"
T0 = "2026-08-01T09:00:00.000Z"   # before the server clock (NOON_NY, 2026-08-11 16:00 UTC)
DAY = 86400


def stamp(t: float) -> str:
    """The UTC stamp the dashboard writes (Date.toISOString) for the whole epoch second `t`."""
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def asset(asset_id: str = "family-home", *, name: str = "Family home", owner: str = "Joint",
          asset_class: str = "House", liquid: bool = False, values=(("2026-01-01", 400_000),),
          updated_at: str = T0, **over) -> dict:
    """A live asset as the dashboard writes it."""
    return {"id": asset_id, "name": name, "owner": owner, "asset_class": asset_class, "liquid": liquid,
            "values": [{"as_of": d, "value": v} for d, v in values], "updated_at": updated_at, **over}


def tombstone(asset_id: str, deleted_at: str, **over) -> dict:
    """An asset removed in the app, as the store keeps it: its id and name, stamped when it was removed, with
    `deleted_at` and nothing else."""
    entry = asset(asset_id, updated_at=deleted_at, deleted_at=deleted_at)
    return {**{k: entry[k] for k in ("id", "name", "updated_at", "deleted_at")}, **over}


def manual_doc(*assets: dict, updated_at: str = T0) -> dict:
    return {"version": 1, "updated_at": updated_at, "assets": list(assets)}


def by_id(*assets: dict) -> list[dict]:
    return sorted(assets, key=lambda a: a["id"])


def put_manual(srv, body) -> tuple[int, dict]:
    res, data = request(srv, "PUT", MANUAL, body if isinstance(body, bytes) else json.dumps(body).encode())
    return res.status, json.loads(data)


def get_manual(srv) -> dict:
    res, data = request(srv, "GET", MANUAL)
    assert res.status == 200
    return json.loads(data)


def test_manual_assets_roundtrip_and_validation(server, tmp_path):
    """The store checks the document's shape only (the engine checks its meaning against the household): it stores
    what it accepts sorted by id, in its own file, and echoes it; every refusal is a 400 saying what is wrong, and none
    writes anything."""
    assert get_manual(server) == store.empty_manual_assets() == {
        "version": 1, "updated_at": store.EPOCH_ISO, "assets": []}
    # shape only: a value dated ahead, two values on one day, a debt's negative value and a trimmed name all pass (what
    # they mean is the engine's to decide), and a key the store does not know is kept verbatim
    home = asset("family-home", acquired=None, deleted_at=None,
                 values=(("2026-01-01", 400_000), ("2027-01-01", 410_000)))
    sedan = asset("sedan", name="Sedan", owner="Alex", asset_class="Car", acquired="2025-03-01",
                  values=(("2025-03-01", 24_000), ("2026-01-01", 21_500.5)), note="kept verbatim",
                  extra={"nested": [[["three levels below the asset"]]]})
    loan = asset("car-loan", name="  Car loan  ", owner="Alex", asset_class="Other Debt",
                 values=(("2026-01-01", -9_000), ("2026-01-01", -8_900), ("2026-02-01", -1e12)))
    # an id keeps a name's letters whatever their script, as a config entry's id does (the app overrides one by it)
    chalet = asset("résidence-2", name="Résidence 2", values=(("2026-01-01", 1e12),))
    boat = tombstone("old-boat", "2026-07-01T08:00:00.000Z")
    shed = tombstone("garden-shed", "2026-07-02T08:00:00.000Z", values=[])
    sent = manual_doc(sedan, home, loan, chalet, boat, shed)
    status, echo = put_manual(server, sent)
    kept_shed = {k: v for k, v in shed.items() if k != "values"}          # a removal is stored without its values
    assert status == 200 and echo == {**sent, "assets": by_id(sedan, home, loan, chalet, boat, kept_shed)}
    assert get_manual(server) == echo
    assert json.loads((tmp_path / "manual_assets.json").read_text("utf-8")) == echo

    def one(**over) -> dict:
        return manual_doc({**asset(), **over})

    def without(key: str) -> dict:
        entry = asset()
        del entry[key]
        return manual_doc(entry)

    def value(v) -> dict:
        return one(values=[v])

    name, owner, cls = ("'family-home': name must be 1 to 80 characters", "'family-home': owner must be",
                        "'family-home': asset_class must be")
    stamp_rule = "'family-home': updated_at must be a UTC timestamp written YYYY-MM-DDTHH:MM:SS.sssZ"
    refusals = [
        (b"{ nope", "body is not valid JSON"),
        (b"[]", "document must be a JSON object"),
        ({**manual_doc(), "version": 2}, "version must be 1"),
        ({**manual_doc(), "version": True}, "version must be 1"),
        ({**manual_doc(), "version": 1.0}, "version must be 1"),
        ({"version": 1, "assets": []}, "updated_at must be a string"),
        ({"version": 1, "updated_at": T0, "assets": {}}, "assets must be a list"),
        ({"version": 1, "updated_at": T0}, "assets must be a list"),
        (b'{"version": 1, "updated_at": "x", "assets": [], "extra": NaN}', "not finite"),
        (json.dumps(one()).replace("400000", "1e400").encode(), "not finite"),
        # a key the store does not know passes verbatim, but not nested past what the engine's loader reads back
        (one(note=json.loads("[" * store.MAX_MANUAL_DEPTH + "]" * store.MAX_MANUAL_DEPTH)),
         f"nested more than {store.MAX_MANUAL_DEPTH} levels deep"),
        (manual_doc("family-home"), "assets[0] must be an object"),
        *((one(id=bad), "assets[0]: id must be")
          for bad in ("Family-Home", "Résidence", "-home", "home-", "a--b", "home_1", "home 1", "home.1", "h" * 65,
                      "home\n", "", 7)),
        (without("id"), "assets[0]: id must be"),
        (manual_doc(asset(), asset(name="Family home (copy)")), "duplicate asset id 'family-home'"),
        # the limit is the raw string's: padding that trims away still counts
        *((one(name=bad), name) for bad in ("   ", "n" * 81, " " * 10_000 + "n", "n" + " " * 80, 5, None)),
        (without("name"), name),
        # no C0 control character and no DEL in a name, an owner or a class (a line break or an escape sequence
        # would reach every page and the database as it is)
        *((one(**{key: bad}), f"'family-home': {key} holds a control character")
          for key in ("name", "owner", "asset_class") for bad in ("a\x00b", "a\nb", "\ta", "a\x1f", "a\x7fb")),
        *((one(owner=bad), owner) for bad in ("", " ", "o" * 81, ["Joint"])),
        (without("owner"), owner),
        *((one(asset_class=bad), cls) for bad in (None, " ", "c" * 81)),
        (without("asset_class"), cls),
        *((one(liquid=bad), "'family-home': liquid must be true or false") for bad in ("no", 0, None)),
        (without("liquid"), "'family-home': liquid must be true or false"),
        *((one(acquired=bad), "'family-home': acquired must be null or a day written YYYY-MM-DD")
          for bad in ("2026-02-30", "2026-1-01", "2026-01-01T00:00:00", 20260101)),
        (one(values="400000"), "'family-home': values must be a list"),
        (without("values"), "'family-home': values must be a list"),
        (manual_doc({**tombstone("old-boat", T0), "values": None}), "'old-boat': values must be a list"),
        (one(values=[]), "'family-home': a live asset needs at least one value"),
        (value("400000"), "'family-home': values[0] must be an object"),
        *((value(bad), "'family-home': values[0].as_of must be a day written YYYY-MM-DD")
          for bad in ({"value": 1}, {"as_of": "2026-13-01", "value": 1}, {"as_of": "26-01-01", "value": 1})),
        *((value(bad), "'family-home': values[0].value must be a number from -1e12 to 1e12")
          for bad in ({"as_of": "2026-01-01"}, {"as_of": "2026-01-01", "value": True},
                      {"as_of": "2026-01-01", "value": "1"}, {"as_of": "2026-01-01", "value": None},
                      {"as_of": "2026-01-01", "value": 1e12 + 1}, {"as_of": "2026-01-01", "value": -1e13},
                      {"as_of": "2026-01-01", "value": 9.3e16}, {"as_of": "2026-01-01", "value": 10 ** 400})),
        *((one(updated_at=bad), stamp_rule)
          for bad in ("2026-08-01T09:00:00Z", "2026-08-01 09:00:00.000Z", "2026-02-30T09:00:00.000Z",
                      "2026-08-01T09:00:00.000+00:00", 1_785_000_000)),
        (without("updated_at"), stamp_rule),
        *((one(deleted_at=bad), "'family-home': deleted_at must be null or a UTC timestamp")
          for bad in ("yesterday", "2026-08-01", 1)),
    ]
    for body, message in refusals:
        status, answer_ = put_manual(server, body)
        assert status == 400 and message in answer_["error"], (body, message, answer_)
    res, _ = request(server, "PUT", MANUAL, b"x" * (store.MAX_MANUAL_BYTES + 1))
    assert res.status == 413
    assert get_manual(server) == echo                                  # no refused PUT wrote anything

    # the path has a dash and no .json suffix (the web server's *.json matcher would take it); nothing else names it
    for path in ("/api/manual_assets", "/api/manual-assets.json", "/api/manual-assets/sedan"):
        assert request(server, "GET", path)[0].status == 404, path
        assert request(server, "PUT", path, json.dumps(sent).encode())[0].status == 404, path
    assert request(server, "POST", MANUAL, b"{}")[0].status == 405
    assert request(server, "DELETE", MANUAL)[0].status == 405


def test_a_body_nested_past_the_parser_is_a_400_and_the_depth_limit_is_exact(server, tmp_path):
    """A body nested deeper than the JSON parser reads is a 400 like any body that is not JSON (it never reaches the
    handler as an error). A manual-assets document nested exactly MAX_MANUAL_DEPTH levels deep, the document itself
    the first, is stored and read back by the engine; one level more is refused."""
    deep = b"[" * 200_000 + b"]" * 200_000
    assert len(deep) < store.MAX_ANSWERS_BYTES
    res, data = request(server, "PUT", "/api/answers", deep)
    assert res.status == 400 and json.loads(data)["error"].startswith("body is not valid JSON")

    def at_depth(levels: int) -> dict:
        inner = levels - 3                       # the document, its assets list and the asset are three levels
        document = manual_doc(asset(note=json.loads("[" * inner + "]" * inner)))
        assert store._depth(document) == levels
        return document

    assert store.MAX_MANUAL_DEPTH == 32
    assert put_manual(server, at_depth(32))[0] == 200
    assert engine_assets.load(tmp_path / "manual_assets.json")[1] == []
    status, body = put_manual(server, at_depth(33))
    assert (status, body) == (400, {"error": "the document is nested more than 32 levels deep"})


def test_a_manual_assets_document_the_store_accepts_is_one_the_engine_reads(server, tmp_path):
    """The store's shape rules and the engine's loader agree: what the store wrote loads with no warning, and every live
    asset whose owner and class the household has is counted, one at the store's limits included."""
    home = asset("family-home", acquired="2025-06-01", values=(("2025-06-01", 400_000), ("2026-01-01", 410_000.25)))
    sedan = asset("sedan", name="Sedan", owner="Alex", asset_class="Car", note="kept verbatim")
    widest = asset("w" * 64, name="W" * store.MAX_MANUAL_NAME_LEN,
                   values=[(f"{2016 + k // 12}-{k % 12 + 1:02d}-01", 1_000 + k) for k in range(store.MAX_MANUAL_VALUES)])
    status, _ = put_manual(server, manual_doc(home, sedan, widest, tombstone("old-boat", T0)))
    assert status == 200
    loaded, checks = engine_assets.load(tmp_path / "manual_assets.json")
    assert checks == []
    counted, warnings = engine_assets.effective(loaded, [], ("Alex", "Joint"), ASSET_CLASSES, LIABILITY_CLASSES, ())
    assert warnings == []
    assert sorted(a.id for a in counted) == ["family-home", "sedan", "w" * 64]
    assert len(next(a for a in counted if a.id == "w" * 64).values) == store.MAX_MANUAL_VALUES


def test_the_asset_id_rule_is_one_rule_in_the_store_and_the_engine():
    """An id the store takes is one the engine counts, and the reverse; every config entry's id (its name's slug)
    passes both, so an app asset can carry it to override the entry."""
    probes = ["a", "family-home", "2nd-car", "résidence", "résidence-d-été", "日本-2", "a" * 64, "a" * 65, "", "-a",
              "a-", "a--b", "a_b", "a b", "A", "Résidence", "a\n", "ǅ", "ß", "İ", 7, None]
    assert [store.is_manual_id(p) for p in probes] == [engine_assets.is_id(p) for p in probes]
    for name in ("Family home", "Résidence d'Été", "2nd car (blue)", "Ünïcödé Ärt", "Sample  --  House"):
        slug = nw.manual_slug(name)
        assert store.is_manual_id(slug) and engine_assets.is_id(slug), slug


def test_the_unicode_rule_is_one_rule_in_the_store_and_the_engine():
    """What the store refuses as text that is not valid Unicode is what the engine's loaders read as a corrupt file."""
    from etl.validate import encodes_as_utf8
    deep: list = []
    node = deep
    for _ in range(5000):
        node.append([])
        node = node[0]
    node.append("\ud800")
    split_pair = chr(0xD83C) + chr(0xDFE0)          # a surrogate pair as two code points: two lone surrogates
    probes = ["plain", "\U0001f3e0", "\ud800", "\udfff", split_pair, {"k": "v"}, {"\ud800": 1}, {"k": ["\udfff"]},
              [1, 2.5, None, True], deep, [[[["fine"]]]], ""]
    assert [store.encodes_as_utf8(p) for p in probes] == [encodes_as_utf8(p) for p in probes] == [
        True, True, False, False, False, True, False, False, True, False, True, True]


def test_the_control_character_rule_is_one_rule_in_the_store_and_the_engine():
    probes = ["Family home", "a\x00b", "a\nb", "\t", "a\x1f", "\x7f", "a\x80b", " ", "é", " padded "]
    assert [store.has_control(p) for p in probes] == [engine_assets.has_control(p) for p in probes] == [
        False, True, True, True, True, True, False, False, False, False]


def test_the_value_range_is_one_rule_in_the_store_and_the_engine():
    """A value the store takes is one the engine counts, and a value it refuses is one the engine would leave out."""
    probes = [0, 1, -1, 0.01, 1e12, -1e12, 10 ** 12, 1e12 + 0.01, -1e12 - 0.01, 1e13, 9.3e16, 1e306, 10 ** 400,
              float("nan"), float("inf"), True, "1", None]
    for v in probes:
        counted, _ = engine_assets.effective(
            {"assets": [asset(values=(("2026-01-01", v),))]}, [], ("Joint",), ASSET_CLASSES, LIABILITY_CLASSES, ())
        assert store._is_amount(v) is bool(counted), v


def test_manual_assets_merge_per_id_newer_wins_and_tombstones_travel():
    """Per id, the entry with the newer updated_at wins whole (a tie goes to the incoming doc), a removal included: the
    tombstone stays in the document, so a tab still holding the old copy cannot bring the asset back, while entering it
    again later (a newer live entry) does. The document's updated_at is the newer of the two; entries are stored sorted
    by id."""
    t1, t2, t3, t4 = (f"2026-08-0{d}T09:00:00.000Z" for d in (1, 2, 3, 4))

    def merge(current, incoming):
        return store.merge_manual_assets(current, incoming, NOON_NY)

    home = asset("family-home", updated_at=t1)
    edit_old = asset("sedan", name="Sedan", values=(("2026-01-01", 22_000),), updated_at=t1)
    edit_new = asset("sedan", name="Sedan", values=(("2026-01-01", 21_000),), updated_at=t2)
    stored = manual_doc(edit_new, home, updated_at=t2)
    # an older copy of an entry loses whole; the document keeps the newer stamp
    assert merge(stored, manual_doc(edit_old, updated_at=t1)) == {
        "version": 1, "updated_at": t2, "assets": [home, edit_new]}
    # a tie goes to the incoming doc
    renamed = {**edit_new, "name": "Sedan (red)"}
    assert merge(stored, manual_doc(renamed, updated_at=t2))["assets"] == [home, renamed]

    # edit against delete: the later of the two wins whole, on whichever side it is
    gone = tombstone("sedan", t3)
    deleted = merge(stored, manual_doc(gone, updated_at=t3))
    assert deleted == {"version": 1, "updated_at": t3, "assets": [home, gone]}
    assert merge(deleted, manual_doc(edit_new, updated_at=t2))["assets"] == [home, gone]     # a stale tab: still gone
    assert merge(manual_doc(gone, updated_at=t3), manual_doc(home, edit_old))["assets"] == [home, gone]
    late_edit = {**edit_new, "updated_at": t4}
    assert merge(deleted, manual_doc(late_edit, updated_at=t4))["assets"] == [home, late_edit]   # entered again later
    assert merge(manual_doc(late_edit, updated_at=t4), manual_doc(gone, updated_at=t3))["assets"] == [late_edit]
    # the tombstone travels: the echo hands it to a tab that never saw the asset
    assert merge(deleted, manual_doc(updated_at=t1)) == deleted

    # sorted by id whatever order either side sends
    a, b, c = (asset(i, name=i, updated_at=t1) for i in ("a-1", "b-2", "c-3"))
    assert [x["id"] for x in merge(manual_doc(c, a), manual_doc(b))["assets"]] == ["a-1", "b-2", "c-3"]
    # a stored doc that is not one merges as nothing, and a stored entry the store would refuse is left out
    assert merge([], manual_doc(home, updated_at=t1)) == manual_doc(home, updated_at=t1)
    assert merge({"assets": "junk"}, manual_doc(home, updated_at=t1)) == manual_doc(home, updated_at=t1)
    assert merge(manual_doc({**edit_new, "values": []}, "junk"), manual_doc(home))["assets"] == [home]


def test_a_device_clock_that_runs_ahead_cannot_poison_the_document(server, tmp_path, clock):
    """An asset stamped far ahead would win every later merge, and such a removal would never expire: an asset's
    updated_at or deleted_at, or the document's updated_at, more than 24 hours ahead of the store's clock is a 400 that
    writes nothing (23 hours ahead is a clock that is merely off, and passes). The document's own updated_at has the
    stamp's shape, or is the empty document's, so no string can win the newer-of-the-two for ever."""
    now = clock()
    assert put_manual(server, manual_doc(asset()))[0] == 200
    before = (tmp_path / "manual_assets.json").read_bytes()
    ahead, soon = stamp(now + 25 * 3600), stamp(now + 23 * 3600)
    future = "check this device's clock"
    refused = [
        (manual_doc(asset("sedan", name="Sedan", updated_at=ahead)), f"'sedan': an asset's timestamp is in the future: {future}"),
        (manual_doc({**tombstone("family-home", T0), "deleted_at": ahead}),
         f"'family-home': an asset's timestamp is in the future: {future}"),
        (manual_doc(asset("sedan", name="Sedan"), updated_at=ahead),
         f"the document's updated_at is in the future: {future}"),
        *((manual_doc(asset("sedan", name="Sedan"), updated_at=bad),
           f"updated_at must be {store._STAMP_RULE}, or the empty document's {store.EPOCH_ISO}")
          for bad in ("zzzz", "", "2026-08-01T09:00:00Z", "2026-08-01", "9999-99-99T99:99:99.999Z")),
    ]
    for body, message in refused:
        status, answer_ = put_manual(server, body)
        assert (status, answer_) == (400, {"error": message}), (body, answer_)
        assert (tmp_path / "manual_assets.json").read_bytes() == before
    status, echo = put_manual(server, manual_doc(asset("sedan", name="Sedan", updated_at=soon), updated_at=soon))
    assert status == 200 and [a["id"] for a in echo["assets"]] == ["family-home", "sedan"]
    assert put_manual(server, manual_doc(updated_at=store.EPOCH_ISO))[0] == 200     # the empty document's stamp


def test_a_stamp_ahead_that_the_store_already_holds_may_be_echoed(server, tmp_path, clock):
    """A stored entry stamped ahead of the store's clock (a hand edit, or a server clock that once ran ahead) does not
    turn every later whole-document save into a 400: an incoming asset whose updated_at and deleted_at equal the stored
    entry's, and a document updated_at equal to the stored document's, pass the clock check. A changed or new stamp
    ahead of the clock is still refused, and writes nothing."""
    now = clock()
    ahead, further = stamp(now + 5 * DAY), stamp(now + 6 * DAY)
    sedan = asset("sedan", name="Sedan", owner="Alex", asset_class="Car", updated_at=ahead)
    boat = tombstone("boat", ahead)
    path = tmp_path / "manual_assets.json"
    path.write_text(json.dumps(manual_doc(boat, asset(), sedan, updated_at=ahead)), "utf-8")
    edited = asset(values=(("2026-01-01", 400_000), ("2026-08-01", 405_000)), updated_at=stamp(now))
    status, echo = put_manual(server, manual_doc(boat, edited, sedan, updated_at=ahead))
    assert status == 200 and echo == manual_doc(*by_id(boat, edited, sedan), updated_at=ahead), echo
    before = path.read_bytes()
    refused = [
        (manual_doc(edited, {**sedan, "updated_at": further}, updated_at=ahead),
         "'sedan': an asset's timestamp is in the future: check this device's clock"),
        (manual_doc({**boat, "updated_at": further, "deleted_at": further}),
         "'boat': an asset's timestamp is in the future: check this device's clock"),
        (manual_doc(edited, updated_at=further), "the document's updated_at is in the future: check this device's clock"),
    ]
    for body, message in refused:
        assert put_manual(server, body) == (400, {"error": message}), body
        assert path.read_bytes() == before


def test_two_clients_adding_different_assets_both_survive(server):
    """Two tabs that each loaded the empty document add a different asset: the second whole-document PUT does not erase
    the first (the store merges per id) and each echo is the merged document; many clients at once lose nothing."""
    home = asset("family-home")
    sedan = asset("sedan", name="Sedan", owner="Alex", asset_class="Car", values=(("2026-02-01", 18_000),))
    assert put_manual(server, manual_doc(home))[0] == 200
    status, echo = put_manual(server, manual_doc(sedan))
    assert status == 200 and echo["assets"] == [home, sedan]
    assert get_manual(server) == echo
    extra = [asset(f"item-{k}", name=f"Item {k}") for k in range(8)]
    threads = [threading.Thread(target=put_manual, args=(server, manual_doc(a))) for a in extra]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [a["id"] for a in get_manual(server)["assets"]] == sorted(["family-home", "sedan", *(a["id"] for a in extra)])


def test_the_fifty_first_live_asset_and_the_121st_value_are_refused_not_evicted(server):
    """At most MAX_MANUAL_ASSETS live assets and MAX_MANUAL_VALUES values per asset: a PUT past either is a 400 naming
    the cap, and so is a merge that would pass the asset cap (a tab adding one to a full store it has not heard from:
    a refusal of what the document holds, as validation's, not of its size), writing nothing. Nothing is dropped to
    make room: removing an asset does."""
    assert (store.MAX_MANUAL_ASSETS, store.MAX_MANUAL_VALUES) == (50, 120)
    full = [asset(f"asset-{k:02d}", name=f"Asset {k}") for k in range(50)]
    assert put_manual(server, manual_doc(*full))[0] == 200
    one_more = asset("asset-50", name="Asset 50")
    status, body = put_manual(server, manual_doc(*full, one_more))
    assert status == 400 and "at most 50 assets" in body["error"]
    status, body = put_manual(server, manual_doc(one_more))
    assert status == 400 and "at most 50 assets" in body["error"] and "nothing was written" in body["error"]
    assert get_manual(server)["assets"] == full

    later = "2026-08-02T09:00:00.000Z"
    monthly = [(f"{2016 + k // 12}-{k % 12 + 1:02d}-01", 1_000 + k) for k in range(121)]
    status, body = put_manual(server, manual_doc(asset("asset-00", name="Asset 0", values=monthly, updated_at=later)))
    assert status == 400 and "at most 120 values per asset" in body["error"]
    assert get_manual(server)["assets"] == full
    status, echo = put_manual(server, manual_doc(asset("asset-00", name="Asset 0", values=monthly[:120],
                                                       updated_at=later)))
    assert status == 200 and len(echo["assets"]) == 50 and len(echo["assets"][0]["values"]) == 120

    # removing one makes room for another
    status, echo = put_manual(server, manual_doc(tombstone("asset-49", later), one_more))
    assert status == 200 and len(echo["assets"]) == 51
    assert sum(a.get("deleted_at") is None for a in echo["assets"]) == 50


def test_a_document_at_every_cap_is_stored_and_read_back(server, tmp_path):
    """The byte cap holds what the counts allow: MAX_MANUAL_ASSETS live assets of MAX_MANUAL_VALUES values each, with
    realistic field lengths (an 80-character name, a 58-character id, 12-digit values), beside MAX_MANUAL_TOMBSTONES
    removals, is stored, echoed, served and read by the engine with no warning; such a document is larger than 256 KiB,
    so a smaller cap would refuse a household that stayed inside every count."""
    def months(k: int) -> list[tuple[str, int]]:
        return [(f"{2016 + j // 12}-{j % 12 + 1:02d}-01", 123_456_000_000 + k * 1_000 + j)
                for j in range(store.MAX_MANUAL_VALUES)]
    live = [asset(f"{'house' * 11}-{k:02d}", name=f"Asset {k:02d} " + "n" * (store.MAX_MANUAL_NAME_LEN - 9),
                  values=months(k)) for k in range(store.MAX_MANUAL_ASSETS)]
    removed = [tombstone(f"gone-{k:03d}", T0) for k in range(store.MAX_MANUAL_TOMBSTONES)]
    assert len(live[0]["id"]) == 58 and len(live[0]["name"]) == store.MAX_MANUAL_NAME_LEN
    body = json.dumps(manual_doc(*live, *removed)).encode()
    assert 256 * 1024 < len(body) <= store.MAX_MANUAL_BYTES
    status, echo = put_manual(server, body)
    assert status == 200 and echo == manual_doc(*by_id(*live, *removed))
    assert get_manual(server) == echo
    loaded, checks = engine_assets.load(tmp_path / "manual_assets.json")
    assert checks == []
    counted, warnings = engine_assets.effective(loaded, [], ("Joint",), ASSET_CLASSES, LIABILITY_CLASSES, ())
    assert warnings == [] and len(counted) == store.MAX_MANUAL_ASSETS
    assert {len(a.values) for a in counted} == {store.MAX_MANUAL_VALUES}


def test_a_removal_expires_ninety_days_after_its_updated_at():
    """Merges are decided by updated_at, so expiry is too: a removal re-stamped recently (updated_at) of an asset
    removed long ago (deleted_at) beats the live copy and stays TOMBSTONE_KEEP_DAYS from its updated_at, so a stale tab
    cannot bring the asset back meanwhile; deleted_at only marks the entry as a removal."""
    now = NOON_NY
    restamped = now - DAY
    live = asset("sedan", name="Sedan", updated_at=stamp(now - 300 * DAY))
    gone = tombstone("sedan", stamp(now - 200 * DAY), updated_at=stamp(restamped))
    merged = store.merge_manual_assets(manual_doc(live), manual_doc(gone), now)
    assert merged["assets"] == [gone]
    assert store.merge_manual_assets(merged, manual_doc(live), now)["assets"] == [gone]            # a stale tab
    for days, kept in ((89, [gone]), (90, [gone]), (91, [])):        # exactly 90 days after its updated_at: kept
        assert store.merge_manual_assets(merged, manual_doc(), restamped + days * DAY)["assets"] == kept, days
    # past MAX_MANUAL_TOMBSTONES, the removals stamped longest ago go first, whatever their deleted_at says
    many = [tombstone(f"t-{k:03d}", stamp(now - 50 * DAY), updated_at=stamp(now - k * 3600)) for k in range(201)]
    kept = store.merge_manual_assets(store.empty_manual_assets(), manual_doc(*many), now)["assets"]
    assert [a["id"] for a in kept] == [f"t-{k:03d}" for k in range(200)]


def test_a_removal_is_stored_with_its_id_name_and_stamps_only(server, tmp_path):
    """A tombstone needs only what lets it beat an older copy and expire: it is stored with its id, name, updated_at
    and deleted_at, whatever else the tab sent (120 values would otherwise sit in the document for 90 days). It still
    beats the older live copy, and the stored form is itself a document the store accepts back."""
    later = "2026-08-02T09:00:00.000Z"
    assert put_manual(server, manual_doc(asset("sedan", name="Sedan", values=(("2026-01-01", 21_000),))))[0] == 200
    sent = {**asset("sedan", name="Sedan", updated_at=later, deleted_at=later,
                    values=[(f"{2016 + j // 12}-{j % 12 + 1:02d}-01", 1_000 + j) for j in range(120)]),
            "note": "kept verbatim on a live asset, not on a removal"}
    status, echo = put_manual(server, manual_doc(sent, updated_at=later))
    stored_form = {"id": "sedan", "name": "Sedan", "updated_at": later, "deleted_at": later}
    assert status == 200 and echo["assets"] == [stored_form]
    assert json.loads((tmp_path / "manual_assets.json").read_text("utf-8"))["assets"] == [stored_form]
    assert put_manual(server, manual_doc(asset("sedan", name="Sedan")))[1]["assets"] == [stored_form]   # a stale tab
    assert put_manual(server, manual_doc(stored_form, updated_at=later))[0] == 200     # the echo, sent back
    # a removal still needs its id, name and stamps, and any owner, class or values it carries are checked
    for bad, message in (({**stored_form, "name": ""}, "name must be"), ({**stored_form, "owner": 5}, "owner must be"),
                         ({**stored_form, "values": "none"}, "values must be a list")):
        status, body = put_manual(server, manual_doc(bad, updated_at=later))
        assert status == 400 and message in body["error"], (bad, body)


def test_a_document_past_the_cap_once_stored_says_its_size_and_the_limit(server):
    """The store re-serialises what it stores, so a compact body under the cap can be over it as stored, on an empty
    store too: the 413 says how large the document would be and what the limit is, never that it was combined with
    something the store holds."""
    compact = json.dumps({"version": 1, "updated_at": T0, "extra": [1] * 30_000}, separators=(",", ":")).encode()
    assert len(compact) < store.MAX_PREFS_BYTES
    stored_size = len(json.dumps(json.loads(compact)).encode())
    res, data = request(server, "PUT", "/api/prefs", compact)
    assert (res.status, json.loads(data)) == (413, {"error": f"the document would be {stored_size} bytes as stored; "
                                                              f"the limit is {store.MAX_PREFS_BYTES}: nothing was written"})
    assert json.loads(request(server, "GET", "/api/prefs")[1]) == store.empty_prefs()


def test_the_engine_reads_every_document_the_store_can_hold():
    """The engine's loader refuses a file past its own limit; the store's cap stays under it, so whatever the store
    wrote is read."""
    assert store.MAX_MANUAL_BYTES == 512 * 1024
    assert store.MAX_MANUAL_BYTES < engine_assets.MAX_BYTES == 1024 * 1024


def test_tombstones_do_not_count_toward_the_cap_and_old_ones_are_dropped(server, clock):
    """A removed asset is a tombstone: it does not count toward MAX_MANUAL_ASSETS, at most MAX_MANUAL_TOMBSTONES are
    kept (the oldest removals go first), and one stamped more than TOMBSTONE_KEEP_DAYS before the merge is dropped,
    after it has beaten the live copy it removes (here each removal is stamped when it was removed)."""
    assert (store.MAX_MANUAL_TOMBSTONES, store.TOMBSTONE_KEEP_DAYS) == (200, 90)
    now = clock()
    full = [asset(f"asset-{k:02d}", name=f"Asset {k}") for k in range(50)]
    removed = [tombstone(f"gone-{k:02d}", stamp(now - k * DAY)) for k in range(10)]
    status, echo = put_manual(server, manual_doc(*full, *removed))
    assert status == 200 and echo["assets"] == by_id(*full, *removed)     # 50 live beside 10 tombstones

    # removed 89 and exactly 90 days before the merge: kept; 91: dropped
    aged = manual_doc(*(tombstone(i, stamp(now - d * DAY)) for i, d in (("kept-89", 89), ("kept-90", 90),
                                                                         ("dropped-91", 91))))
    assert [a["id"] for a in store.merge_manual_assets(store.empty_manual_assets(), aged, now)["assets"]] == [
        "kept-89", "kept-90"]
    # an expired tombstone still removes the older live copy it beats (it is dropped after the merge per id)
    live = manual_doc(asset("sedan", name="Sedan", updated_at=stamp(now - 120 * DAY)))
    assert store.merge_manual_assets(live, manual_doc(tombstone("sedan", stamp(now - 100 * DAY))), now)["assets"] == []

    # past MAX_MANUAL_TOMBSTONES the oldest removals go first, and a PUT holding more is trimmed, never refused
    many = [tombstone(f"t-{k:03d}", stamp(now - k * 3600 - 1800)) for k in range(250)]    # t-000 the newest
    merged = store.merge_manual_assets(store.empty_manual_assets(), manual_doc(*many), now)
    assert [a["id"] for a in merged["assets"]] == [f"t-{k:03d}" for k in range(200)]
    status, echo = put_manual(server, manual_doc(*many))
    newest = sorted(removed + many, key=lambda a: (a["updated_at"], a["id"]))[-200:]
    assert status == 200 and echo["assets"] == by_id(*full, *newest)

    # on the store's own clock: 91 days on, the next save drops every removal above
    clock.advance(91 * DAY)
    status, echo = put_manual(server, manual_doc())
    assert status == 200 and echo["assets"] == full


def test_sign_in_without_its_state_fails_closed(server):
    """The fixture runs the store with no sign-in state (signin None): a sign-in is refused, never waved through."""
    res, _ = request(server, "POST", "/auth/login", urlencode({"passcode": "correct-horse-battery"}).encode(),
                     {"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status == 503
    assert res.getheader("Set-Cookie") is None


def test_post_routing_regressions(server):
    assert request(server, "POST", "/api/scenarios", b"{}")[0].status == 405
    assert request(server, "POST", "/auth/other", b"{}")[0].status == 404


def test_concurrent_puts_stay_atomic(server):
    payloads = [json.dumps(doc(1, f"2026-07-01T00:00:0{k}Z")).encode() for k in range(10)]
    threads = [threading.Thread(target=request,
                                args=(server, "PUT", "/api/scenarios", p))
               for p in payloads]
    for t in threads: t.start()
    for t in threads: t.join()
    _, data = request(server, "GET", "/api/scenarios")
    assert data in payloads                     # one coherent winner, no interleaving


# ------------------------------------------------------------- /api/refresh
# The manual "Refresh now" button. Nothing here runs the ETL — these tests
# exercise the gate that decides whether a SimpleFIN request gets spent.

def get_refresh(srv) -> dict:
    res, data = request(srv, "GET", "/api/refresh")
    assert res.status == 200, data
    return json.loads(data)


def post_refresh(srv, headers: dict | None = None):
    res, data = request(srv, "POST", "/api/refresh", headers=headers)
    return res, json.loads(data)


def hold_lock(path: Path) -> subprocess.Popen:
    """Hold an exclusive flock from another process, returning only once it is
    genuinely held (the child emits a byte — no sleep-polling)."""
    code = ("import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);"
            "sys.stdout.write('x');sys.stdout.flush();sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdout.read(1) == b"x"
    return p


def test_refresh_get_is_idle_on_a_fresh_dir(server):
    doc = get_refresh(server)
    assert (doc["state"], doc["can_refresh"], doc["reason"]) == ("idle", True, "ok")
    assert (doc["used_today"], doc["limit_per_day"]) == (0, 6)
    assert doc["started_at"] is None and doc["steps_failed"] == []


def test_refresh_get_carries_the_no_store_headers(server):
    res, _ = request(server, "GET", "/api/refresh")
    assert res.getheader("Cache-Control") == "no-store"
    assert res.getheader("X-Content-Type-Options") == "nosniff"


def claim(d: Path, nonce: str, started_at: float, rc: int = 0) -> None:
    """What deploy/refresh_runner.py does when it picks a request up and the
    run completes — the tests need it to get past the "queued" short-circuit."""
    rs.write_status(d, state="done", trigger="manual", request_nonce=nonce,
                    started_at=started_at, started_at_iso=rs.utc_iso(started_at),
                    finished_at=started_at + 30, rc=rc, steps_failed=[], warnings=[])


def test_refresh_post_enqueues_then_refuses_the_next_one(server, refresh_dir, clock):
    res, body = post_refresh(server)
    assert res.status == 202
    assert (body["accepted"], body["used_today"]) == (True, 1)
    assert rs.read_request(refresh_dir)["nonce"] == body["nonce"]

    claim(refresh_dir, body["nonce"], clock())
    res2, body2 = post_refresh(server)
    assert res2.status == 429
    assert body2["reason"] == "min_gap"
    assert res2.getheader("Retry-After") == str(body2["retry_after_s"])
    assert body2["retry_after_s"] == 15 * 60
    # a refused request must not charge budget, or a double-click costs two
    assert body2["used_today"] == 1
    assert len(rs.load_ledger(refresh_dir)) == 1


def test_a_second_click_while_one_is_queued_attaches_to_it(server, refresh_dir):
    """The queued request short-circuits everything below it: the caller is
    told to watch the run already on its way, and nothing is charged."""
    res, body = post_refresh(server)
    assert res.status == 202
    res2, body2 = post_refresh(server)
    assert (res2.status, body2["reason"]) == (200, "already_running")
    assert body2["nonce"] == body["nonce"]      # watch THIS run
    assert len(rs.load_ledger(refresh_dir)) == 1
    assert get_refresh(server)["state"] == "queued"


def test_refresh_min_gap_counts_a_scheduled_run_too(server, clock):
    """A manual pull five minutes after the 09:00 cron run fetches nothing new
    and still spends one of SimpleFIN's 24 daily requests."""
    rs.write_status(store.ScenarioHandler.refresh_dir, state="done",
                    started_at=clock() - 300, rc=0)
    res, body = post_refresh(server)
    assert (res.status, body["reason"], body["retry_after_s"]) == (429, "min_gap", 600)


def test_refresh_daily_cap_stops_the_seventh_run(server, clock, refresh_dir):
    for _ in range(6):
        res, body = post_refresh(server)
        assert res.status == 202
        claim(refresh_dir, body["nonce"], clock())
        clock.advance(16 * 60)
    res, body = post_refresh(server)
    assert (res.status, body["reason"], body["used_today"]) == (429, "daily_cap", 6)
    # 12:00 in New York + 6*16min = 13:36 -> 10h24m to its midnight
    assert body["retry_after_s"] == (10 * 3600) + (24 * 60)
    assert len(rs.load_ledger(refresh_dir)) == 6

    clock.advance(body["retry_after_s"])        # next local day
    assert post_refresh(server)[0].status == 202


def test_refresh_concurrent_posts_admit_exactly_one(server, refresh_dir):
    """Two family members mashing the button at once must not both get a run.
    Without the check-record-enqueue critical section they both read an empty
    ledger, both pass the gate, and both spend a request."""
    results: list[int] = []
    lock = threading.Lock()

    def go():
        status = post_refresh(server)[0].status
        with lock:
            results.append(status)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(202) == 1
    # the losers either lost the race for the gate (429) or arrived after the
    # request file existed and were told to watch that run instead (200)
    assert set(results) <= {200, 202, 429}
    assert len(rs.load_ledger(refresh_dir)) == 1


def test_refresh_while_a_run_is_live_attaches_without_spending_budget(server, refresh_dir):
    rs.write_status(refresh_dir, state="running", trigger="schedule",
                    request_nonce=None, started_at=store.ScenarioHandler.clock())
    holder = hold_lock(refresh_dir / rs.LOCK_FILE)
    try:
        assert get_refresh(server)["state"] == "running"
        res, body = post_refresh(server)
        assert (res.status, body["reason"], body["accepted"]) == (200, "already_running", False)
        assert rs.load_ledger(refresh_dir) == []
    finally:
        holder.kill()
        holder.wait()


def test_refresh_reports_stalled_when_the_run_died_mid_flight(server, refresh_dir, clock):
    """status.json still says running, but nobody holds the lock — the kernel
    released it when the process died. The button must re-enable, not spin."""
    rs.write_status(refresh_dir, state="running", started_at=clock() - 30 * 60)
    doc = get_refresh(server)
    assert doc["state"] == "stalled"
    assert doc["can_refresh"] is True


def test_refresh_reports_a_dead_worker_and_charges_nothing(server, refresh_dir, clock):
    rs.write_request(refresh_dir, nonce="orphan", now=clock() - 5 * 60)
    assert get_refresh(server)["state"] == "worker_unavailable"
    res, body = post_refresh(server)
    assert (res.status, body["reason"]) == (503, "worker_unavailable")
    assert rs.load_ledger(refresh_dir) == []


def test_refresh_get_surfaces_a_finished_runs_outcome(server, refresh_dir, clock):
    rs._main(["write-status", "--dir", str(refresh_dir), "--state", "done",
              "--trigger", "manual", "--nonce", "n1", "--rc", "1",
              "--failed", "SimpleFIN-fetch", "--warnings", "Example Payroll Plus"])
    doc = get_refresh(server)
    assert (doc["state"], doc["rc"], doc["trigger"]) == ("done", 1, "manual")
    assert doc["request_nonce"] == "n1"
    assert doc["steps_failed"] == ["SimpleFIN-fetch"]
    assert doc["warnings"] == ["Example Payroll Plus"]
    assert doc["finished_at"].endswith("Z")


def test_refresh_post_rejects_an_oversized_body(server):
    res, data = request(server, "POST", "/api/refresh", b"x" * 2048)
    assert res.status == 413
    assert res.getheader("Connection") == "close"


def test_refresh_post_refuses_cross_origin(server, refresh_dir):
    """A malicious page a family member visits must not be able to burn the
    SimpleFIN budget through their logged-in browser."""
    res, _ = request(server, "POST", "/api/refresh", headers={"Origin": "http://evil.example"})
    assert res.status == 403
    assert rs.load_ledger(refresh_dir) == []


def test_refresh_unconfigured_fails_closed(server):
    store.ScenarioHandler.refresh_dir = None    # fixture restores on next test
    assert request(server, "GET", "/api/refresh")[0].status == 503
    assert request(server, "POST", "/api/refresh")[0].status == 503


def test_refresh_path_routing_regressions(server):
    for path in ("/api/refresh/1", "/api/refreshx"):
        assert request(server, "GET", path)[0].status == 404
        assert request(server, "POST", path)[0].status == 404
    assert request(server, "PUT", "/api/refresh", b"{}")[0].status == 404


# ------------------------------------------- the ingest without a fetch
# The store asks for an ingest by touching refresh_state.INGEST_MARKER in the refresh directory: a manual-assets save
# that changed the stored document, or POST /api/refresh {"mode": "ingest"}. The refresher runs it; the store only
# reads what the runs record in status.json.

INGEST = json.dumps({"mode": "ingest"}).encode()


def post_ingest(srv, headers: dict | None = None):
    res, data = request(srv, "POST", "/api/refresh", INGEST, headers)
    return res, json.loads(data)


def test_a_changed_manual_assets_save_touches_the_marker_and_an_unchanged_one_does_not(server, refresh_dir, clock):
    """The marker's mtime is the store's clock at a save that changed the stored bytes; re-saving the same document, a
    refused save and a save with no refresh directory configured touch nothing."""
    marker = refresh_dir / rs.INGEST_MARKER

    def stamp(t: float) -> int:
        """the marker's time for a touch at `t`"""
        return math.floor(t) + 2

    sent = manual_doc(asset())
    assert put_manual(server, sent)[0] == 200
    assert marker.stat().st_mtime == stamp(clock())
    clock.advance(30)
    assert put_manual(server, sent)[0] == 200                          # the same bytes again
    assert put_manual(server, manual_doc(asset(values=())))[0] == 400   # refused
    assert marker.stat().st_mtime == stamp(clock() - 30)
    edited = manual_doc(asset(values=(("2026-01-01", 400_000), ("2026-06-01", 405_000)),
                              updated_at="2026-08-02T09:00:00.000Z"))
    assert put_manual(server, edited)[0] == 200
    assert marker.stat().st_mtime == stamp(clock())
    # the other documents never ask for an ingest (the answers reach the next one, the prefs need none)
    clock.advance(30)
    request(server, "PUT", "/api/prefs", json.dumps({"version": 1, "updated_at": T0, "budgets": {}}).encode())
    request(server, "PUT", "/api/answers",
            json.dumps(answers_doc([answer("tx_1", "txn", "2026-07-16T10:00:00Z")])).encode())
    assert marker.stat().st_mtime == stamp(clock() - 30)
    # no refresh directory: the save is stored and nothing is touched anywhere
    marker.unlink()
    store.ScenarioHandler.refresh_dir = None
    status, echo = put_manual(server, manual_doc(asset(name="Family home (renamed)",
                                                       updated_at="2026-08-03T09:00:00.000Z")))
    assert status == 200 and echo["assets"][0]["name"] == "Family home (renamed)"
    assert not marker.exists()


def test_an_ingest_request_spends_no_refresh_budget(server, refresh_dir, clock):
    """POST {"mode": "ingest"} touches the marker and answers 202; it writes no request and no ledger entry, so the
    manual-refresh budget and the gap between refreshes are as they were, and the refresh button stays usable."""
    res, body = post_ingest(server)
    assert (res.status, body) == (202, {"accepted": True, "mode": "ingest"})
    assert rs.marker_time(refresh_dir) == math.floor(clock()) + 2
    assert rs.read_request(refresh_dir) is None and not (refresh_dir / rs.LEDGER_FILE).exists()
    view = get_refresh(server)
    assert (view["used_today"], view["can_refresh"], view["reason"]) == (0, True, "ok")
    assert view["ingest"]["wanted"] is True
    # an ordinary refresh right after it is accepted, and charged as one
    res, body = post_refresh(server)
    assert (res.status, body["used_today"]) == (202, 1)
    # cross-origin: refused, nothing touched
    clock.advance(60)
    res, _ = request(server, "POST", "/api/refresh", INGEST, {"Origin": "http://evil.example"})
    assert res.status == 403 and rs.marker_time(refresh_dir) == math.floor(clock() - 60) + 2


def test_an_ingest_request_past_the_daily_cap_waits_for_midnight(server, refresh_dir, clock):
    """MAX_INGEST_PER_DAY ingest-only runs already started today (the status the runs write, bucketed by the store's
    zone): a 429 with the seconds to local midnight, and the marker left alone."""
    runs = [clock() - 3600 + k for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(refresh_dir, ingest_runs=runs, ingest_started_at=runs[-1], ingest_finished_at=runs[-1] + 5,
                    ingest_rc=0)
    res, body = post_ingest(server)
    assert (res.status, body) == (429, {"accepted": False, "reason": "ingest_cap", "retry_after_s": 12 * 3600})
    assert res.getheader("Retry-After") == str(12 * 3600)              # 12:00 in New York to its midnight
    assert not (refresh_dir / rs.INGEST_MARKER).exists()
    clock.advance(12 * 3600)
    assert post_ingest(server)[0].status == 202


def test_a_refresh_body_names_ingest_or_nothing(server, refresh_dir):
    """A JSON object with a mode other than "ingest" is a 400 that does nothing; a JSON object with no mode asks for a
    refresh exactly as before."""
    for mode in ("refresh", "INGEST", None, 1, ["ingest"]):
        res, data = request(server, "POST", "/api/refresh", json.dumps({"mode": mode}).encode())
        assert res.status == 400 and "mode" in json.loads(data)["error"], mode
    assert rs.read_request(refresh_dir) is None and not (refresh_dir / rs.INGEST_MARKER).exists()
    res, data = request(server, "POST", "/api/refresh", b'{"note": "no mode"}')
    assert res.status == 202 and json.loads(data)["accepted"] is True
    assert not (refresh_dir / rs.INGEST_MARKER).exists()


@pytest.mark.parametrize("mode, which", [("ai", "run"), ("ai_reset", "reset")])
def test_an_ai_request_touches_its_marker_and_the_ingest_marker_and_spends_no_refresh_budget(server, refresh_dir,
                                                                                             clock, mode, which):
    """POST {"mode": "ai"} (the household switched the AI step on) or {"mode": "ai_reset"} (it asked for the AI step's
    answers to be forgotten) touches that marker and the ingest marker at the same instant, as an ingest request
    touches its own: a 202, no request file, no ledger entry, the other AI marker untouched. The store never reaches
    the AI step's store: it only asks."""
    res, data = request(server, "POST", "/api/refresh", json.dumps({"mode": mode}).encode())
    assert (res.status, json.loads(data)) == (202, {"accepted": True, "mode": mode})
    stamp = math.floor(clock()) + 2
    assert rs.ai_marker_time(refresh_dir, which) == stamp and rs.marker_time(refresh_dir) == stamp
    other = "reset" if which == "run" else "run"
    assert rs.ai_marker_time(refresh_dir, other) is None
    assert rs.read_request(refresh_dir) is None and not (refresh_dir / rs.LEDGER_FILE).exists()
    assert rs.ai_due(refresh_dir, rs.read_status(refresh_dir)) == {which: True, other: False}
    assert get_refresh(server)["ingest"]["wanted"] is True
    assert sorted(p.name for p in refresh_dir.iterdir()) == sorted([rs.SCHEDULE_FILE, rs.INGEST_MARKER,
                                                                    rs.AI_MARKERS[which]])
    # cross-origin: refused, nothing touched
    clock.advance(60)
    res, _ = request(server, "POST", "/api/refresh", json.dumps({"mode": mode}).encode(),
                     {"Origin": "http://evil.example"})
    assert res.status == 403 and rs.ai_marker_time(refresh_dir, which) == stamp


def test_an_ai_request_past_the_ingest_cap_waits_for_midnight_and_touches_nothing(server, refresh_dir, clock):
    runs = [clock() - 3600 + k for k in range(rs.MAX_INGEST_PER_DAY)]
    rs.write_status(refresh_dir, ingest_runs=runs, ingest_started_at=runs[-1], ingest_finished_at=runs[-1] + 5,
                    ingest_rc=0)
    res, data = request(server, "POST", "/api/refresh", b'{"mode": "ai"}')
    assert (res.status, json.loads(data)["reason"]) == (429, "ingest_cap")
    assert rs.ai_marker_time(refresh_dir, "run") is None and rs.marker_time(refresh_dir) is None


def test_a_link_at_an_ai_marker_is_never_touched_through(server, refresh_dir, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    os.utime(target, (1_000_000, 1_000_000))
    (refresh_dir / rs.AI_MARKER).symlink_to(target)
    res, _ = request(server, "POST", "/api/refresh", b'{"mode": "ai"}')
    assert res.status == 500 and target.stat().st_mtime == 1_000_000


@pytest.mark.parametrize("body", [b'"ingest"', b"[]", b"7", b"null", b"not json", b"\xff\xfe{}", b'{"mode": "ingest"'])
def test_a_body_that_is_not_a_json_object_is_refused_and_spends_nothing(server, refresh_dir, body):
    """Not JSON, not UTF-8, or JSON that is not an object: a 400 that queues nothing, touches nothing and spends none
    of the day's budget (it used to queue a full refresh)."""
    res, data = request(server, "POST", "/api/refresh", body)
    assert res.status == 400 and "JSON object" in json.loads(data)["error"]
    assert rs.read_request(refresh_dir) is None and rs.load_ledger(refresh_dir) == []
    assert not (refresh_dir / rs.INGEST_MARKER).exists()
    assert get_refresh(server)["used_today"] == 0


@pytest.mark.parametrize("body", [b"", b"  ", b" \n\t", b"{}"])
def test_an_empty_body_or_an_empty_object_asks_for_a_refresh(server, refresh_dir, body):
    res, data = request(server, "POST", "/api/refresh", body)
    assert res.status == 202 and json.loads(data)["accepted"] is True
    assert rs.read_request(refresh_dir)["nonce"] == json.loads(data)["nonce"]
    assert len(rs.load_ledger(refresh_dir)) == 1


def test_the_status_carries_the_schedule_and_the_ingest_state(server, refresh_dir, clock):
    """GET /api/refresh adds `schedule` (the times of the schedule the scheduler wrote in the mailbox, in the household's
    zone) and `ingest`: whether one is wanted (the marker is newer than the last ingest's start) and the last ingest's
    start, end and exit code. Every other key reads as it did."""
    view = get_refresh(server)
    assert view["schedule"] == {"times": ["01:00", "09:00", "17:00"], "timezone": "America/New_York"}
    assert view["ingest"] == {"wanted": False, "last_started_at": None, "last_finished_at": None, "last_rc": None}
    assert (view["state"], view["can_refresh"], view["used_today"]) == ("idle", True, 0)

    rs.touch_ingest_marker(refresh_dir, clock() - 100)
    assert get_refresh(server)["ingest"]["wanted"] is True
    started = clock() - 40
    rs.write_status(refresh_dir, ingest_started_at=started, ingest_finished_at=None, ingest_rc=None,
                    ingest_runs=[started])
    assert get_refresh(server)["ingest"] == {"wanted": False, "last_started_at": rs.utc_iso(started),
                                             "last_finished_at": None, "last_rc": None}     # running
    rs.write_status(refresh_dir, ingest_finished_at=started + 30, ingest_rc=2)
    assert get_refresh(server)["ingest"] == {"wanted": False, "last_started_at": rs.utc_iso(started),
                                             "last_finished_at": rs.utc_iso(started + 30), "last_rc": 2}   # failed
    rs.write_status(refresh_dir, ingest_rc=True)
    assert get_refresh(server)["ingest"]["last_rc"] is None              # junk is no exit code


@pytest.mark.parametrize("what", ["missing", "malformed", "not a schedule", "a link"])
def test_without_a_schedule_the_view_reports_none_and_the_days_are_utcs(server, refresh_dir, clock, tmp_path, what):
    """No schedule the scheduler wrote (it has not started), or one that does not read as one, or a link at its name
    (never followed): the view reports no schedule (the dashboard says it is not known yet) and the daily limits count
    UTC's days, so the ingest cap's wait at noon in New York (16:00 UTC) is the 8 hours to UTC's midnight."""
    path = refresh_dir / rs.SCHEDULE_FILE
    path.unlink()
    if what == "malformed":
        path.write_text("{not json")
    elif what == "not a schedule":
        path.write_text(json.dumps({"timezone": "Mars/Olympus", "hours": [1], "minute": 0}))
    elif what == "a link":
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text(json.dumps({"timezone": ZONE, "hours": [1], "minute": 0}))
        path.symlink_to(elsewhere)
    assert get_refresh(server)["schedule"] is None
    rs.write_status(refresh_dir, ingest_runs=[clock() - 60 * k for k in range(1, rs.MAX_INGEST_PER_DAY + 1)])
    res, body = post_ingest(server)
    assert (res.status, body["retry_after_s"]) == (429, 8 * 3600)


def test_the_schedule_is_read_at_each_request(server, refresh_dir):
    """A scheduler restarted on a changed household.yaml writes a new schedule; the store reports it at once, with no
    restart of its own and no flag: the schedule is never the store's to state."""
    rs.write_schedule(refresh_dir, rs.Schedule("Pacific/Kiritimati", (6, 18), 30))
    assert get_refresh(server)["schedule"] == {"times": ["06:30", "18:30"], "timezone": "Pacific/Kiritimati"}
    with pytest.raises(SystemExit):
        store.parse_args(["--schedule", "01:00,09:00,17:00"])


def test_the_caps_turn_over_at_the_households_midnight_a_day_ahead_of_utc(server, refresh_dir, clock):
    """In Kiritimati (UTC+14) six manual refreshes before its midnight use the day up; just after it the next is
    accepted, while UTC's day is still the one they were made on. The ingest cap's wait runs to the same midnight."""
    rs.write_schedule(refresh_dir, rs.Schedule("Pacific/Kiritimati", (1, 9, 17), 0))
    kiritimati = ZoneInfo("Pacific/Kiritimati")
    clock.t = dt.datetime(2026, 8, 11, 22, 0, tzinfo=kiritimati).timestamp()          # 08:00 UTC
    rs.save_ledger(refresh_dir, [clock() - 3600 * k for k in range(1, 7)])
    res, body = post_refresh(server)
    assert (res.status, body["reason"], body["used_today"], body["retry_after_s"]) == (429, "daily_cap", 6, 2 * 3600)
    rs.write_status(refresh_dir, ingest_runs=[clock() - 60 * k for k in range(1, rs.MAX_INGEST_PER_DAY + 1)])
    res, body = post_ingest(server)
    assert (res.status, body["retry_after_s"]) == (429, 2 * 3600)
    clock.advance(2 * 3600 + 1)                                                    # 00:00:01 there, 10:00:01 UTC
    view = get_refresh(server)
    assert (view["used_today"], view["can_refresh"]) == (0, True)
    assert post_ingest(server)[0].status == 202
    assert post_refresh(server)[0].status == 202


def test_the_view_says_what_the_last_fetch_learned_of_the_feed(server, refresh_dir):
    """files_only, feed_errors and reauth_needed as the refresh wrote them; an older status without them reads as a
    fetch that reported nothing, and what is not a list of text (or past the most kept) is left out."""
    view = get_refresh(server)
    assert (view["files_only"], view["feed_errors"], view["reauth_needed"]) == (False, [], [])
    rs.write_status(refresh_dir, state="done", files_only=True, feed_errors=[], reauth_needed=[])
    assert get_refresh(server)["files_only"] is True
    rs.write_status(refresh_dir, files_only="yes", feed_errors=["Sample Bank: Sign in again", 7, None],
                    reauth_needed="Sample Bank")
    view = get_refresh(server)
    assert (view["files_only"], view["feed_errors"], view["reauth_needed"]) == (
        False, ["Sample Bank: Sign in again"], [])
    rs.write_status(refresh_dir, feed_errors=[f"Bank {i}: failed" for i in range(15)], reauth_needed=["Sample Bank"])
    view = get_refresh(server)
    assert len(view["feed_errors"]) == rs.MAX_FEED_ERRORS and view["reauth_needed"] == ["Sample Bank"]


def test_the_store_never_writes_the_ingest_count(server, refresh_dir, clock):
    """status.json has one set of writers, the refresher and refresh.sh, under the refresh lock: the store only reads
    the ingest fields (for the cap and for the status) and only touches the marker."""
    rs.write_status(refresh_dir, ingest_runs=[clock() - 600], ingest_started_at=clock() - 600,
                    ingest_finished_at=clock() - 590, ingest_rc=0)
    before = (refresh_dir / rs.STATUS_FILE).read_bytes()
    assert put_manual(server, manual_doc(asset()))[0] == 200
    assert post_ingest(server)[0].status == 202
    get_refresh(server)
    assert (refresh_dir / rs.STATUS_FILE).read_bytes() == before
    assert sorted(p.name for p in refresh_dir.iterdir()) == [rs.INGEST_MARKER, rs.SCHEDULE_FILE, rs.STATUS_FILE]
    assert rs.read_schedule(refresh_dir) == rs.Schedule(ZONE, (1, 9, 17), 0)       # the scheduler's, as it was
    source = Path(store.__file__).read_text("utf-8")
    assert "write_status" not in source and "STATUS_FILE" not in source


def test_a_refresh_asked_for_during_an_ingest_only_run_queues_behind_it(server, refresh_dir, clock):
    """An ingest-only run holds the refresh lock but is no refresh: the status keeps the last refresh's state, a click
    queues a refresh (the worker runs it once the ingest ends) instead of attaching to the last refresh's nonce, and
    the queued request is not taken for a dead worker however long the ingest runs."""
    claim(refresh_dir, "n0", clock() - 7200)
    rs.write_status(refresh_dir, ingest_started_at=clock() - 5, ingest_finished_at=None, ingest_rc=None,
                    ingest_runs=[clock() - 5], ingest_only=True)
    holder = hold_lock(refresh_dir / rs.LOCK_FILE)
    try:
        view = get_refresh(server)
        assert (view["state"], view["can_refresh"], view["request_nonce"]) == ("done", True, "n0")
        res, body = post_refresh(server)
        assert res.status == 202 and body["nonce"] != "n0"
        clock.advance(rs.WORKER_STALE_S + 60)
        assert get_refresh(server)["state"] == "queued"
        res, again = post_refresh(server)
        assert (res.status, again["reason"], again["nonce"]) == (200, "already_running", body["nonce"])
    finally:
        holder.kill()
        holder.wait()
    # a refresh's own ingest step holds the lock as a refresh: state running, as before
    rs._main(["write-status", "--dir", str(refresh_dir), "--state", "running", "--nonce", body["nonce"]])
    holder = hold_lock(refresh_dir / rs.LOCK_FILE)
    try:
        assert get_refresh(server)["state"] == "running"
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.parametrize("ingest", ["not started yet", "ended"])
def test_an_ingest_only_run_reads_as_one_for_its_whole_time_under_the_lock(server, refresh_dir, clock, ingest):
    """The refresher's mark covers the run before its script's first write and after its ingest ended (a failed one
    alerts for up to 20 s before the script exits): the status is not `running`, a click is not told the last
    refresh's nonce is already running, and its refresh queues behind the run."""
    claim(refresh_dir, "n0", clock() - 7200)
    fields = ({"ingest_started_at": clock() - 7300, "ingest_finished_at": clock() - 7290, "ingest_rc": 0}
              if ingest == "not started yet" else
              {"ingest_started_at": clock() - 30, "ingest_finished_at": clock() - 2, "ingest_rc": 1})
    rs.write_status(refresh_dir, **fields, ingest_only=True)
    holder = hold_lock(refresh_dir / rs.LOCK_FILE)
    try:
        view = get_refresh(server)
        assert (view["state"], view["reason"]) == ("done", "ok")
        res, body = post_refresh(server)
        assert (res.status, body["accepted"]) == (202, True) and body["nonce"] != "n0"
        assert get_refresh(server)["state"] == "queued"
    finally:
        holder.kill()
        holder.wait()


def test_a_held_lock_with_no_mark_is_a_refresh_and_a_free_one_with_a_stale_mark_is_idle(server, refresh_dir, clock):
    """No mark under a held lock: a refresh is running, as before, and a click attaches to it. A mark left behind with
    the lock free reads as nothing: the last refresh's state, and a click is a refresh."""
    rs._main(["write-status", "--dir", str(refresh_dir), "--state", "running", "--nonce", "n1"])
    holder = hold_lock(refresh_dir / rs.LOCK_FILE)
    try:
        assert get_refresh(server)["state"] == "running"
        res, body = post_refresh(server)
        assert (res.status, body["reason"], body["nonce"]) == (200, "already_running", "n1")
    finally:
        holder.kill()
        holder.wait()
    claim(refresh_dir, "n1", clock() - 7200)
    rs.write_status(refresh_dir, ingest_only=True)
    view = get_refresh(server)
    assert (view["state"], view["can_refresh"], view["reason"]) == ("done", True, "ok")
    assert post_refresh(server)[0].status == 202


def test_a_tags_only_answer_is_accepted_in_both_scopes_and_survives_the_echo(server):
    """The bulk tag editor sends {"tags": [...]} with no category. The whitelist takes it and
    the merged echo the client adopts still carries it - a set the server dropped would vanish from the
    dashboard's doc ~800ms after the save."""
    def tags(scope, target, at):
        a = {"key": f"{scope}:{target}", "scope": scope, "set": {"tags": ["kids", "trip"]}, "answered_at": at}
        a["txn_id" if scope == "txn" else "vendor"] = target
        return a
    body = answers_doc([tags("txn", "tx_1", "2026-07-16T10:00:00Z"), tags("vendor", "Cedar Hollow Diner", "2026-07-16T10:01:00Z")])
    assert [a["set"] for a in store.validate_answers(json.dumps(body).encode())["answers"]] == [{"tags": ["kids", "trip"]}] * 2
    res, data = request(server, "PUT", "/api/answers", json.dumps(body).encode())
    assert res.status == 200
    assert {a["key"]: a["set"] for a in json.loads(data)["answers"]} == {
        "txn:tx_1": {"tags": ["kids", "trip"]}, "vendor:Cedar Hollow Diner": {"tags": ["kids", "trip"]}}
    _, data = request(server, "GET", "/api/answers")
    assert len(json.loads(data)["answers"]) == 2
    # the guards around it hold: tags must still be a list of strings, and an empty set is still refused
    for bad in ({"tags": "kids"}, {"tags": [1]}, {}):
        a = tags("txn", "tx_1", "2026-07-16T10:00:00Z")
        a["set"] = bad
        with pytest.raises(ValueError):
            store.validate_answers(json.dumps(answers_doc([a])).encode())


# The dashboard stamps a tags-only save over a stored CATEGORY answer one tick after the stored stamp, in the stored
# stamp's own format (app/src/lib/answers.ts nextStamp): it must not move the answer to "now" (that would push it to the
# front of the learned rules' newest-answers window and clear an override conflict in the preview). The server compares
# stamps as STRINGS, so the format matters. These pairs are the SAME literals app/src/lib/answers.test.ts asserts the
# dashboard produces.
BUMPED_STAMPS = [
    ("2026-07-16T10:00:00Z", "2026-07-16T10:00:01Z"),                # legacy, no milliseconds: +1 second
    ("2026-07-16T10:00:00.123Z", "2026-07-16T10:00:00.124Z"),        # milliseconds: +1 ms
    ("2026-07-16T10:00:00.999Z", "2026-07-16T10:00:01.000Z"),
    ("2026-07-16T10:59:59Z", "2026-07-16T11:00:00Z"),
    ("2026-12-31T23:59:59.999Z", "2027-01-01T00:00:00.000Z"),
]


def _merged_answer(stamp: str) -> dict:
    a = answer("tx_1", "txn", stamp, category="Kids")
    a["set"]["tags"] = ["trip"]
    return a


@pytest.mark.parametrize("stored_stamp,bumped_stamp", BUMPED_STAMPS)
def test_a_tags_only_merge_stamp_beats_the_stored_copy_and_a_stale_tab_loses_to_it(stored_stamp, bumped_stamp):
    stored = answers_doc([answer("tx_1", "txn", stored_stamp, category="Kids")], stored_stamp)
    merged = answers_doc([_merged_answer(bumped_stamp)], bumped_stamp)
    assert bumped_stamp > stored_stamp                                     # as strings: the way the server compares
    # the dashboard's PUT of the merged answer replaces the stored one ...
    out = store.merge_answers(stored, merged, LATER)["answers"]
    assert len(out) == 1 and out[0]["set"].get("tags") == ["trip"] and out[0]["answered_at"] == bumped_stamp
    # ... and a stale tab that still holds the OLD copy (same stamp as before) cannot put it back: the stored merged
    # copy is newer, so the stale one loses (with an equal stamp it would WIN the tie - incoming wins ties)
    back = store.merge_answers(merged, stored, LATER)["answers"]
    assert len(back) == 1 and back[0]["set"].get("tags") == ["trip"] and back[0]["answered_at"] == bumped_stamp


def test_a_legacy_stamp_cannot_be_bumped_by_a_millisecond():
    # why the dashboard goes up a whole SECOND on a stamp with no milliseconds: "...T10:00:00.001Z" < "...T10:00:00Z"
    # as strings ('.' sorts below 'Z'), so a +1 ms stamp would LOSE to the stored legacy copy
    legacy, one_ms = "2026-07-16T10:00:00Z", "2026-07-16T10:00:00.001Z"
    assert one_ms < legacy
    out = store.merge_answers(answers_doc([answer("tx_1", "txn", legacy, category="Kids")]),
                              answers_doc([_merged_answer(one_ms)]), LATER)["answers"]
    assert "tags" not in out[0]["set"]                                     # the (wrongly formatted) merged copy lost


# ---------------------------------------------------------------- the directories it is given
# The store takes its directories from arguments, else their variables (FF_STATE_DIR, FF_STATE_REFRESH_DIR), else
# FF_HOME, else the legacy layout (refresh_state.locate); started as the container starts it, by path with the system
# python, it writes only where it was told.

STORE_SCRIPT = Path(__file__).parents[1] / "deploy" / "scenario_store.py"


def start_store(tmp_path: Path, *args: str, **env: str) -> tuple[subprocess.Popen, int]:
    """The store on a free loopback port, with no path variable but those given and its sign-in off (these tests are
    about where it writes; tests/test_store_auth.py starts it with sign-in required); returns it and its port, read
    from the line it prints once it is listening."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FF_")}
    proc = subprocess.Popen([sys.executable, str(STORE_SCRIPT), "--host", "127.0.0.1", "--port", "0", "--auth", "off",
                             *args],
                            cwd=tmp_path, stderr=subprocess.PIPE, text=True, env={**clean, **env})
    line = proc.stderr.readline()
    found = re.search(r" on 127\.0\.0\.1:(\d+) ", line)
    if not found:
        proc.kill()
        raise AssertionError(f"the store did not start: {line!r}")
    return proc, int(found.group(1))


class Port:
    """What request() needs of a server: its address."""
    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port)


def exercise(port: int) -> None:
    """A document saved, an ingest asked for, the refresh view read."""
    sent = answers_doc([answer("tx_b", "txn", "2026-07-16T11:00:00Z")], "2026-07-16T11:00:00Z")
    assert request(Port(port), "PUT", "/api/answers", json.dumps(sent).encode())[0].status == 200
    assert request(Port(port), "POST", "/api/refresh", b'{"mode": "ingest"}')[0].status == 202
    res, data = request(Port(port), "GET", "/api/refresh")
    assert res.status == 200 and "can_refresh" in json.loads(data)


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    assert proc.wait(timeout=10) == 0


def test_a_store_given_only_its_directories_writes_in_that_tree(tmp_path):
    tree = tmp_path / "tree"
    proc, port = start_store(tmp_path, "--dir", str(tree / "documents"), "--refresh-dir", str(tree / "refresh"))
    try:
        exercise(port)
    finally:
        stop(proc)
    assert sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*")) == [
        "documents", "documents/answers.json", "refresh", f"refresh/{rs.INGEST_MARKER}"]


def test_a_store_given_only_ff_home_writes_in_its_state(tmp_path):
    home = tmp_path / "home"
    proc, port = start_store(tmp_path, FF_HOME=str(home))
    try:
        exercise(port)
    finally:
        stop(proc)
    assert sorted(p.relative_to(home).as_posix() for p in home.rglob("*")) == [
        "state", "state/family", "state/family/answers.json", "state/refresh", f"state/refresh/{rs.INGEST_MARKER}"]


def test_the_variables_place_the_store_and_an_argument_beats_them(tmp_path):
    named, given = tmp_path / "named", tmp_path / "given"
    proc, port = start_store(tmp_path, "--refresh-dir", str(given / "refresh"), FF_HOME=str(tmp_path / "home"),
                             FF_STATE_DIR=str(named / "documents"), FF_STATE_REFRESH_DIR=str(named / "refresh"))
    try:
        exercise(port)
    finally:
        stop(proc)
    assert (named / "documents" / "answers.json").exists() and (given / "refresh" / rs.INGEST_MARKER).exists()
    assert not (named / "refresh").exists() and not (tmp_path / "home").exists()


def test_with_nothing_naming_the_refresh_directory_the_refresh_view_stays_off(tmp_path):
    proc, port = start_store(tmp_path, "--dir", str(tmp_path / "documents"))
    try:
        assert request(Port(port), "GET", "/api/refresh")[0].status == 503
    finally:
        stop(proc)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["documents"]
