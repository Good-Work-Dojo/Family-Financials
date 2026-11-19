# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The sign-in's rules (deploy/auth.py) and `python -m etl passcode` (etl/passcode_cli.py): passcode lines, the
constant work of a check, sessions, devices, the throttle, cookie strings, the Origin rule and /auth/check's answer.
Pure: a fake clock, an injected hasher that counts its calls (real scrypt only where the line format itself is under
test), no network. Every passcode here is an obvious invention and every address a documentation range."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import stat
import sys
from pathlib import Path

import auth  # tests/conftest.py puts deploy/ on sys.path
import pytest

from etl import passcode_cli

GOOD = "correct-horse-battery"
DAY = 86400.0


class CountingHasher:
    """A cheap stand-in for scrypt with the same signature: SHA-256 of salt and secret, counting its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, secret: bytes, salt: bytes) -> bytes:
        self.calls += 1
        return hashlib.sha256(salt + secret).digest()


def cheap_line(label: str, passcode: str, hasher=None, salt: bytes | None = None) -> str:
    return auth.hash_passcode(label, passcode, hasher=hasher or CountingHasher(),
                              salt=salt if salt is not None else hashlib.sha256(label.encode()).digest()[:16])


def counter_tokens():
    """A token seam: distinct, well-formed, predictable tokens."""
    n = iter(range(10**6))
    return lambda size: f"{next(n):043d}"


# ---------------------------------------------------------------- passcode lines

def test_a_hashed_line_has_the_documented_shape_and_real_scrypt_round_trips_it():
    line = auth.hash_passcode("alex", GOOD)
    label, kind, n, r, p, salt, digest = line.split(":")
    assert (label, kind, n, r, p) == ("alex", "scrypt", "15", "8", "1")
    assert "=" not in salt + digest and len(salt) == 22 and len(digest) == 43
    raw_salt = auth.unb64(salt, 16)
    assert raw_salt is not None
    expected = hashlib.scrypt(GOOD.encode(), salt=raw_salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    assert auth.unb64(digest, 32) == expected
    assert auth.verify([line], GOOD) == "alex"
    assert auth.verify([line], GOOD + "x") is None
    assert auth.hash_passcode("alex", GOOD) != line                  # a fresh salt every time


def test_verify_hashes_every_well_formed_line_whichever_one_matches():
    lines = [cheap_line(name, f"passcode-of-{name}") for name in ("alex", "blair", "casey")]
    for typed, label in (("passcode-of-alex", "alex"), ("passcode-of-casey", "casey"), ("nobody-has-this", None)):
        hasher = CountingHasher()
        assert auth.verify(lines, typed, hasher=hasher) == label
        assert hasher.calls == 3, typed


def test_verify_compares_every_line_in_constant_time(monkeypatch):
    lines = [cheap_line(name, f"passcode-of-{name}") for name in ("alex", "blair", "casey")]
    compared: list[int] = []
    real = hmac.compare_digest

    def counting(a, b):
        compared.append(1)
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", counting)
    assert auth.verify(lines, "passcode-of-alex", hasher=CountingHasher()) == "alex"
    assert len(compared) == 3


@pytest.mark.parametrize("lines", [[], ["not a line"], ["alex:scrypt:15:8:1:short:short"]])
def test_with_no_usable_line_a_check_still_hashes_once_and_fails(lines):
    hasher = CountingHasher()
    assert auth.verify(lines, GOOD, hasher=hasher) is None
    assert hasher.calls == 1


def test_not_set_up_and_wrong_cost_the_same_work():
    one = [cheap_line("alex", GOOD)]
    none_set, wrong = CountingHasher(), CountingHasher()
    assert auth.verify([], GOOD, hasher=none_set) is None
    assert auth.verify(one, "wrong-passcode", hasher=wrong) is None
    assert none_set.calls == wrong.calls == 1


def test_at_most_eight_lines_are_used():
    lines = [cheap_line(f"p{k}", f"passcode-{k}") for k in range(10)]
    hasher = CountingHasher()
    assert auth.verify(lines, "passcode-8", hasher=hasher) is None
    assert hasher.calls == 8
    assert auth.verify(lines, "passcode-7", hasher=CountingHasher()) == "p7"


def test_the_passcode_is_compared_as_typed():
    line = cheap_line("alex", "0123456789")
    assert auth.verify([line], "0123456789", hasher=CountingHasher()) == "alex"
    for typed in ("123456789", " 0123456789", "0123456789 ", "0123456789\n"):
        assert auth.verify([line], typed, hasher=CountingHasher()) is None, repr(typed)


def test_a_tampered_line_never_matches():
    line = cheap_line("alex", GOOD)
    label, kind, n, r, p, salt, digest = line.split(":")
    flipped = ("A" if digest[0] != "A" else "B") + digest[1:]
    other_salt = ("A" if salt[0] != "A" else "B") + salt[1:]
    for tampered in (":".join([label, kind, n, r, p, salt, flipped]), ":".join([label, kind, n, r, p, other_salt, digest])):
        assert auth.parse_line(tampered) is not None                  # still well-formed, so it is hashed
        hasher = CountingHasher()
        assert auth.verify([tampered], GOOD, hasher=hasher) is None
        assert hasher.calls == 1


@pytest.mark.parametrize("bad", [
    "alex:scrypt:15:8:1:{salt}",                          # a field missing
    "alex:scrypt:15:8:1:{salt}:{digest}:extra",           # a field too many
    "Alex:scrypt:15:8:1:{salt}:{digest}",                 # a label out of its rule
    "alex:bcrypt:15:8:1:{salt}:{digest}",                 # another scheme
    "alex:scrypt:14:8:1:{salt}:{digest}",                 # other parameters
    "alex:scrypt:15:8:1:{salt}=:{digest}",                # padded
    "alex:scrypt:15:8:1:{salt}x:{digest}",                # a salt of the wrong length
    "alex:scrypt:15:8:1:{salt}:{digest}xyz",              # a hash of the wrong length
    "alex:scrypt:15:8:1:{salt}:{digest_plus}",            # not URL-safe base64
])
def test_a_malformed_line_does_not_parse(bad):
    good = cheap_line("alex", GOOD)
    _, _, _, _, _, salt, digest = good.split(":")
    text = bad.format(salt=salt, digest=digest, digest_plus="+" + digest[1:])
    assert auth.parse_line(text) is None
    assert auth.parse_line(good) is not None


def test_malformed_lines_are_skipped_and_reported_once_by_number_never_by_content(tmp_path):
    secret_looking = "zz-not-a-line-but-private-looking"
    good = [cheap_line("alex", GOOD), cheap_line("blair", "another-passcode")]
    path = tmp_path / "passcodes"
    path.write_bytes(f"{good[0]}\n\n{secret_looking}\n{good[1]}\r\nnon-ascii-é\n".encode())
    said: list[str] = []
    f = auth.PasscodeFile(path, warn=said.append)
    assert [line.label for line in f.lines()] == ["alex", "blair"]
    assert [line.label for line in f.lines()] == ["alex", "blair"]
    assert len(said) == 2 and "line 3" in said[0] and "line 5" in said[1]
    assert not any(secret_looking in s or "non-ascii" in s for s in said)


def test_the_file_is_read_again_when_it_changes(tmp_path):
    path = tmp_path / "passcodes"
    f = auth.PasscodeFile(path, warn=lambda m: None)
    assert f.lines() == []                                        # no file: nobody can sign in
    path.write_text(cheap_line("alex", GOOD) + "\n")
    assert [line.label for line in f.lines()] == ["alex"]
    path.write_text(cheap_line("alex", GOOD) + "\n" + cheap_line("blair", "another-passcode") + "\n")
    assert [line.label for line in f.lines()] == ["alex", "blair"]
    path.unlink()
    assert f.lines() == []


def test_more_than_eight_lines_in_the_file_are_reported_and_the_first_eight_used(tmp_path):
    path = tmp_path / "passcodes"
    path.write_text("".join(cheap_line(f"p{k}", f"passcode-{k}") + "\n" for k in range(9)))
    said: list[str] = []
    assert [line.label for line in auth.PasscodeFile(path, warn=said.append).lines()] == [f"p{k}" for k in range(8)]
    assert len(said) == 1 and "first 8" in said[0]


@pytest.mark.parametrize("passcode,ok", [
    ("abcdefg", False),                 # 7 characters
    ("abcdefgh", True),                 # 8
    ("123456789", False),               # 9 digits
    ("1234567890", True),               # 10 digits
    ("1234567a", True),                 # 8, not all digits
    ("١٢٣٤٥٦٧٨٩", False),   # 9 digits of another script
    ("  space ", True),                 # spaces are characters like any other
    ("\U0001f600" * 8, True),           # any character
    ("\U0001f600" * 90, False),         # too long to send in the sign-in form
    ("abcdefg\ud800", False),           # text no browser can send
    ("replace-this-sample-passcode", False),    # the example answers file's, which anyone can read
    ("Replace-This-Sample-Passcode ", False),   # in any case, with a stray space
])
def test_the_passcode_rules(passcode, ok):
    assert (auth.passcode_problem(passcode) is None) == ok


def test_the_same_characters_typed_composed_or_decomposed_match():
    composed, decomposed = "café-au-lait", "café-au-lait"
    assert composed != decomposed
    for stored, typed in ((composed, decomposed), (decomposed, composed)):
        line = cheap_line("alex", stored)
        assert auth.verify([line], typed, hasher=CountingHasher()) == "alex"
    real = auth.hash_passcode("alex", decomposed)                     # real scrypt: what the store checks
    assert auth.verify([real], composed) == "alex"


@pytest.mark.parametrize("label,ok", [("alex", True), ("a-b_9", True), ("x" * 32, True), ("x" * 33, False),
                                      ("", False), ("Alex", False), ("a:b", False), ("a b", False)])
def test_the_label_rule(label, ok):
    assert (auth.label_problem(label) is None) == ok


# ---------------------------------------------------------------- sessions

# the fingerprint of the passcode line a session was opened with, and the set of the current lines' fingerprints
FP = "f" * 64
LIVE = {FP}


def test_a_session_is_kept_by_the_hash_of_its_token_only():
    s = auth.Sessions(token=counter_tokens())
    token = s.create(1000.0, "alex", FP)
    assert s.check(1001.0, token, LIVE) == "alex"
    dumped = s.dump()
    assert token not in dumped and hashlib.sha256(token.encode()).hexdigest() in dumped
    for bad in (token + "x", "", None):
        assert s.check(1001.0, bad, LIVE) is None


def test_a_session_idle_for_thirty_days_ends_and_use_keeps_it_alive():
    s = auth.Sessions(token=counter_tokens())
    t0 = 1_000_000.0
    token = s.create(t0, "alex", FP)
    assert s.check(t0 + 29 * DAY, token, LIVE) == "alex"         # seen: idle time starts again
    assert s.check(t0 + 58 * DAY, token, LIVE) == "alex"
    assert s.check(t0 + 88 * DAY, token, LIVE) is None            # thirty days unseen
    assert s.check(t0 + 88 * DAY - 1, token, LIVE) is None        # and it stays ended
    idle = auth.Sessions(token=counter_tokens())
    kept = idle.create(t0, "alex", FP)
    assert idle.check(t0 + 30 * DAY - 1, kept, LIVE) == "alex"


def test_a_session_ends_four_hundred_days_after_sign_in_however_often_it_is_used():
    s = auth.Sessions(token=counter_tokens())
    t0 = 1_000_000.0
    token = s.create(t0, "alex", FP)
    t = t0
    while t + 20 * DAY < t0 + 400 * DAY:
        t += 20 * DAY
        assert s.check(t, token, LIVE) == "alex"
    assert s.check(t0 + 400 * DAY, token, LIVE) is None


def test_fifty_sessions_at_most_the_least_recently_seen_goes_first():
    s = auth.Sessions(token=counter_tokens())
    tokens = [s.create(1000.0 + k, "alex", FP) for k in range(50)]
    assert s.check(2000.0, tokens[0], LIVE) == "alex"             # the first is seen again, so the second is oldest
    newest = s.create(3000.0, "blair", FP)
    assert len(s) == 50
    assert s.check(3001.0, tokens[1], LIVE) is None
    assert s.check(3001.0, tokens[0], LIVE) == "alex" and s.check(3001.0, newest, LIVE) == "blair"


def test_sign_out_drops_the_session_and_a_copy_of_the_cookie_stops_working():
    s = auth.Sessions(token=counter_tokens())
    token = s.create(1000.0, "alex", FP)
    stolen = str(token)
    assert s.drop(token) is True
    assert s.check(1001.0, stolen, LIVE) is None
    assert s.drop(token) is False


def test_a_passcode_removed_or_changed_ends_its_sessions_and_no_other():
    hasher = CountingHasher()
    alex, blair = cheap_line("alex", GOOD, hasher), cheap_line("blair", "another-passcode", hasher)
    alex_new = auth.hash_passcode("alex", "a-new-passcode", hasher=hasher, salt=b"\x01" * 16)

    def live(*lines: str) -> set[str]:
        return {auth.passcode_fingerprint(auth.parse_line(line)) for line in lines}

    s = auth.Sessions(token=counter_tokens())
    a = s.create(1000.0, "alex", auth.passcode_fingerprint(auth.match([alex, blair], GOOD, hasher=hasher)))
    b = s.create(1000.0, "blair", auth.passcode_fingerprint(auth.match([alex, blair], "another-passcode",
                                                                       hasher=hasher)))
    assert s.check(1001.0, a, live(alex, blair)) == "alex" and s.check(1001.0, b, live(alex, blair)) == "blair"
    assert s.check(1002.0, a, live(alex_new, blair)) is None             # alex's passcode changed
    assert s.check(1002.0, a, live(alex, blair)) is None                 # and the session stays ended
    assert s.check(1002.0, b, live(alex_new, blair)) == "blair"          # blair's is untouched
    assert s.check(1003.0, b, live(alex_new)) is None                    # blair's passcode removed


def test_a_fingerprint_is_of_the_label_and_salt_and_never_of_the_hash():
    line = auth.parse_line(cheap_line("alex", GOOD))
    fingerprint = auth.passcode_fingerprint(line)
    assert fingerprint == hashlib.sha256(b"alex:" + line.salt).hexdigest()
    same_salt_other_hash = auth.PasscodeLine("alex", line.salt, bytes(32))
    assert auth.passcode_fingerprint(same_salt_other_hash) == fingerprint
    assert auth.passcode_fingerprint(auth.PasscodeLine("alex", bytes(16), line.digest)) != fingerprint
    assert auth.passcode_fingerprint(auth.PasscodeLine("blair", line.salt, line.digest)) != fingerprint
    assert auth.b64(line.digest) not in fingerprint and line.digest.hex() not in fingerprint


def test_prune_with_the_current_passcodes_drops_the_sessions_no_line_opened():
    s = auth.Sessions(token=counter_tokens())
    kept = s.create(1000.0, "alex", FP)
    s.create(1000.0, "blair", "e" * 64)
    assert s.prune(1001.0) == 0 and s.prune(1001.0, LIVE) == 1
    assert len(s) == 1 and s.check(1002.0, kept, LIVE) == "alex"


def test_the_sessions_survive_a_restart_and_the_ended_ones_do_not():
    s = auth.Sessions(token=counter_tokens())
    old = s.create(1000.0, "alex", FP)
    fresh = s.create(1000.0 + 20 * DAY, "blair", FP)
    said: list[str] = []
    again = auth.Sessions.load(s.dump(), 1000.0 + 40 * DAY, warn=said.append)
    assert said == []
    assert again.check(1000.0 + 40 * DAY, fresh, LIVE) == "blair"
    assert again.check(1000.0 + 40 * DAY, old, LIVE) is None      # idle past thirty days by the restart
    assert len(again) == 1


def _record(**fields) -> str:
    record = {"created": 1, "last_seen": 1, "label": "x", "passcode": FP, **fields}
    record = {k: v for k, v in record.items() if v is not None}
    return json.dumps({"version": 1, "sessions": {"a" * 64: record}})


@pytest.mark.parametrize("text", [None, "{not json", "[]", '{"version": 1, "sessions": []}',
                                  '{"version": 2, "sessions": {}}',
                                  '{"version": 1, "sessions": {"abc": {"created": 1, "last_seen": 1, "label": "x", '
                                  '"passcode": "' + FP + '"}}}',
                                  _record(created=True), _record(passcode=None), _record(passcode="not-a-fingerprint")])
def test_a_missing_or_corrupt_sessions_file_loads_as_none_with_one_line(text):
    said: list[str] = []
    s = auth.Sessions.load(text, 1000.0, warn=said.append)
    assert len(s) == 0 and len(said) == 1


# ---------------------------------------------------------------- devices

def test_a_device_is_known_by_the_hash_of_its_token():
    d = auth.Devices(token=counter_tokens())
    token = d.add(1000.0)
    key = d.known(token)
    assert key == hashlib.sha256(token.encode()).hexdigest()
    assert d.known("0" * 43 + "x") is None and d.known(None) is None
    assert token not in d.dump()
    again = auth.Devices.load(d.dump(), warn=lambda m: None)
    assert again.known(token) == key


def test_two_hundred_devices_at_most_the_least_recently_used_goes_first():
    d = auth.Devices(token=counter_tokens())
    tokens = [d.add(1000.0 + k) for k in range(200)]
    d.use(d.known(tokens[0]), 5000.0)
    d.add(6000.0)
    assert len(d) == 200
    assert d.known(tokens[1]) is None and d.known(tokens[0]) is not None


@pytest.mark.parametrize("text", [None, "nope", '{"version": 1, "devices": {"x": 1}}'])
def test_a_missing_or_corrupt_devices_file_loads_as_none_with_one_line(text):
    said: list[str] = []
    assert len(auth.Devices.load(text, warn=said.append)) == 0 and len(said) == 1


# ---------------------------------------------------------------- the throttle

STRANGER = auth.address_key("198.51.100.7")
HOME = auth.address_key("192.0.2.10")
DEVICE = "d" * 64


def run_attempts(throttle: auth.Throttle, times, address, device=None) -> list[object]:
    """Each attempt at its time: decided, and when allowed hashed and recorded as a failure."""
    out = []
    for t in times:
        decision = throttle.check(t, address, device)
        if isinstance(decision, auth.Allow):
            throttle.record(t, address, device, ok=False)
        out.append(decision)
    return out


def test_a_thousand_failures_from_one_address_are_hashed_five_times_and_then_locked_for_a_minute():
    th = auth.Throttle()
    decisions = run_attempts(th, [k / 100 for k in range(1000)], STRANGER)
    allowed = [d for d in decisions if isinstance(d, auth.Allow)]
    assert len(allowed) == 5
    locked = [d for d in decisions if isinstance(d, auth.Locked)]
    assert len(locked) == 995 and {d.until for d in locked} == {4 / 100 + 60}
    assert isinstance(th.check(4 / 100 + 59, STRANGER, None), auth.Locked)
    assert isinstance(th.check(4 / 100 + 60, STRANGER, None), auth.Allow)    # the lock ends


def test_the_lock_doubles_up_to_fifteen_minutes_and_a_quiet_day_forgets_it():
    th = auth.Throttle()
    t, locks = 0.0, []
    for _ in range(7):
        for _ in range(5):
            assert isinstance(th.check(t, STRANGER, None), auth.Allow)
            th.record(t, STRANGER, None, ok=False)
        d = th.check(t, STRANGER, None)
        assert isinstance(d, auth.Locked)
        locks.append(d.until - t)
        assert d.retry_after(t) == int(d.until - t)
        t = d.until
    assert locks == [60, 120, 240, 480, 900, 900, 900]
    t += DAY                                                          # a day with no failure
    for _ in range(5):
        th.record(t, STRANGER, None, ok=False)
    assert th.check(t, STRANGER, None).until - t == 60


def test_a_day_long_flood_from_one_address_runs_the_hash_a_bounded_number_of_times():
    th = auth.Throttle()
    decisions = run_attempts(th, [float(k) for k in range(int(DAY))], STRANGER)
    hashed = sum(isinstance(d, auth.Allow) for d in decisions)
    assert hashed <= 5 * (int(DAY) // 900 + 5)


def test_a_locked_attempt_neither_runs_the_hash_nor_counts():
    th = auth.Throttle()
    run_attempts(th, [0.0] * 5, STRANGER)
    before = copy.deepcopy(th.state)
    d = auth.Throttle.decide(1.0, STRANGER, None, th.state)
    assert isinstance(d, auth.Locked)
    assert th.state == before                                         # deciding changes nothing


def test_the_households_browser_is_never_address_locked_and_has_its_own_budget():
    th = auth.Throttle()
    run_attempts(th, [k / 100 for k in range(1000)], HOME)            # a stranger behind the household's address
    assert isinstance(th.check(10.0, HOME, None), auth.Locked)
    assert th.check(10.0, HOME, DEVICE) == auth.Allow(slow=False)
    run_attempts(th, [10.0 + k for k in range(10)], HOME, DEVICE)     # ten typos of its own
    wait = th.check(20.0, HOME, DEVICE)
    assert isinstance(wait, auth.Wait) and wait.seconds == 900 - 10
    assert isinstance(th.check(10.0 + 900, HOME, DEVICE), auth.Allow)  # the first typo is fifteen minutes old
    th.record(10.0 + 900, HOME, DEVICE, ok=True)
    assert th.check(10.0 + 901, HOME, DEVICE) == auth.Allow(slow=False)


def test_a_devices_failures_do_not_lock_its_address():
    th = auth.Throttle()
    run_attempts(th, [float(k) for k in range(9)], HOME, DEVICE)
    assert th.check(9.0, HOME, None) == auth.Allow(slow=False)


def test_a_flood_from_a_thousand_addresses_slows_strangers_but_not_the_household():
    th = auth.Throttle()
    addresses = [auth.address_key(f"2001:db8:{k:x}::1") for k in range(1000)]
    assert len(set(addresses)) == 1000
    for k, address in enumerate(addresses):
        decision = th.check(k / 10, address, None)
        assert isinstance(decision, auth.Allow)                       # none of them is locked: one failure each
        assert decision.slow == (k > 30), k                           # more than 30 failures in ten minutes
        th.record(k / 10, address, None, ok=False)
    now = 100.0
    assert th.check(now, HOME, DEVICE) == auth.Allow(slow=False)      # a known device is never slowed
    assert th.check(now, HOME, None) == auth.Allow(slow=True)         # a correct passcode is still checked
    th.record(now, HOME, None, ok=True)
    thirty_first_newest = (1000 - 31) / 10
    assert th.check(thirty_first_newest + 599.5, HOME, None) == auth.Allow(slow=True)
    assert th.check(thirty_first_newest + 600.5, HOME, None) == auth.Allow(slow=False)   # ten minutes old


def test_the_throttle_remembers_at_most_ten_thousand_addresses():
    th = auth.Throttle()
    for k in range(10_001):
        th.record(float(k), auth.address_key(f"2001:db8:{k // 65536:x}:{k % 65536:x}::1"), None, ok=False)
    assert len(th.state.addresses) == 10_000
    assert auth.address_key("2001:db8::1") not in th.state.addresses             # the oldest went first
    assert auth.address_key("2001:db8:0:2710::1") in th.state.addresses          # the newest (10,000) stays


def test_ten_thousand_other_addresses_cannot_unlock_a_locked_one():
    th = auth.Throttle()
    run_attempts(th, [0.0] * 5, STRANGER)                             # locked first, so the oldest key in the table
    for k in range(10_000):
        th.record(1.0 + k / 1000, auth.address_key(f"2001:db8:{k // 65536:x}:{k % 65536:x}::1"), None, ok=False)
    assert len(th.state.addresses) == 10_000
    assert isinstance(th.check(20.0, STRANGER, None), auth.Locked)
    assert auth.address_key("2001:db8::1") not in th.state.addresses             # an unlocked key went instead


@pytest.mark.parametrize("given,key", [
    ("192.0.2.1", "192.0.2.1"),
    (" 192.0.2.1 ", "192.0.2.1"),
    ("::ffff:192.0.2.1", "192.0.2.1"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("[2001:db8:1:2:bbbb::2]", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("not an address", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_the_address_key(given, key):
    assert auth.address_key(given) == key


def test_an_ipv6_lock_covers_its_whole_64_and_nothing_beyond():
    th = auth.Throttle()
    for k in range(5):
        th.record(0.0, auth.address_key(f"2001:db8:1:2::{k + 1:x}"), None, ok=False)
    assert isinstance(th.check(1.0, auth.address_key("2001:db8:1:2:ffff::9"), None), auth.Locked)
    assert isinstance(th.check(1.0, auth.address_key("2001:db8:1:3::1"), None), auth.Allow)


# ---------------------------------------------------------------- cookies, the Origin rule, /auth/check

def test_the_cookie_strings():
    assert auth.session_cookie("tok", secure=False) == \
        "ff_session=tok; Path=/; HttpOnly; SameSite=Lax; Max-Age=34560000"
    assert auth.session_cookie("tok", secure=True).endswith("; Max-Age=34560000; Secure")
    assert auth.device_cookie("dev", secure=False) == "ff_device=dev; Path=/; HttpOnly; SameSite=Lax; Max-Age=34560000"
    assert auth.cleared_session_cookie(secure=True) == "ff_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Secure"


def test_cookie_values_reads_every_cookie_of_a_name():
    header = "theme=dark; ff_session=abc ;ff_device=d1;ff_session=def; ff_sessionx=no; =odd; novalue"
    assert auth.cookie_values(header, "ff_session") == ["abc", "def"]
    assert auth.cookie_values(header, "ff_device") == ["d1"]
    assert auth.cookie_values(None, "ff_session") == []


@pytest.mark.parametrize("origin,scheme,host,ok", [
    ("http://192.0.2.10:8080", "http", "192.0.2.10:8080", True),
    ("https://example.net", "https", "example.net", True),
    ("https://example.net", "https", "Example.NET:443", True),
    ("http://example.net:80", "http", "example.net", True),
    ("https://example.net", "http", "example.net", False),          # the scheme Caddy saw is part of the origin
    ("http://example.net:8080", "http", "example.net:8081", False),
    ("https://attacker.example", "https", "example.net", False),
    ("http://[2001:db8::1]:8080", "http", "[2001:db8::1]:8080", True),
    (None, "http", "example.net", False),
    ("", "http", "example.net", False),
    ("null", "http", "example.net", False),
    ("http://example.net/", "http", "example.net", False),           # an Origin carries no path
    ("http://user@example.net", "http", "example.net", False),
    ("http://example.net:99999", "http", "example.net:99999", False),
    ("http://example.net", "http", None, False),
    ("http://example.net", "http", "example.net/evil", False),
])
def test_the_origin_rule(origin, scheme, host, ok):
    assert auth.origin_allowed(origin, scheme, host) is ok


@pytest.mark.parametrize("host,ok", [
    ("192.0.2.10", True),
    ("192.0.2.10:8080", True),
    ("[2001:db8::1]", True),
    ("[2001:db8::1]:8080", True),
    ("2001:db8::1", True),                    # a bare IPv6 literal carries no port
    ("localhost", True),
    ("LOCALHOST:8080", True),
    ("Kitchen-PC.Local", True),
    ("dash.home.local:443", True),
    ("listed.example.net", True),
    ("Listed.Example.NET:8443", True),
    ("localhost.", False),                    # a trailing dot: the proxy's rule refuses it too
    ("listed.example.net.", False),
    ("localhost.evil.example", False),
    ("evil.local.example", False),
    (".local", False),
    ("other.example.net", False),
    ("", False),
    (None, False),
    ("localhost:http", False),
    ("192.0.2.10:", True),
    ("[2001:db8::1", False),
    ("[not-an-address]", False),
    ("[192.0.2.10]", False),
    ("192.0.2.10:²", False),
])
def test_the_host_rule(host, ok):
    assert auth.host_allowed(host, ("listed.example.net",)) is ok


def test_the_host_rule_lists_nothing_by_default():
    assert auth.host_allowed("192.0.2.10") and not auth.host_allowed("listed.example.net")


@pytest.mark.parametrize("address,names", [
    ("example.net", ("example.net",)),
    ("Dash.Example.NET", ("dash.example.net",)),
    ("example.net:8443", ("example.net",)),
    ("https://example.net", ("example.net",)),
    ("http://example.net:8080/", ("example.net",)),
    ("example.net, www.example.net", ("example.net", "www.example.net")),
    ("example.net example.net:8443", ("example.net",)),
    (":8080", ()),
    ("http://:8080", ()),
    ("192.0.2.10:8080", ()),
    ("[2001:db8::1]:8443", ()),
    ("2001:db8::1", ()),
    ("*.example.net", ()),
    ("", ()),
    (None, ()),
])
def test_the_names_a_site_address_serves(address, names):
    assert auth.site_names(address) == names


def test_the_sites_own_name_passes_the_host_rule_and_its_neighbours_do_not():
    allowed = auth.site_names("https://dash.example.net:8443")
    assert auth.host_allowed("dash.example.net", allowed) and auth.host_allowed("DASH.example.net:443", allowed)
    assert not auth.host_allowed("example.net", allowed) and not auth.host_allowed("evil.dash.example.net", allowed)


@pytest.mark.parametrize("value,scheme", [("https", "https"), ("HTTPS", "https"), ("http", "http"), (None, "http"),
                                          ("", "http"), ("ftp", "http")])
def test_the_request_scheme(value, scheme):
    assert auth.request_scheme(value) == scheme


NAV = {"accept": "text/html,application/xhtml+xml", "fetch_mode": "navigate"}


@pytest.mark.parametrize("signed_in,method,uri,headers,status", [
    (True, "GET", "/api/prefs", {}, 204),
    (True, "POST", "/api/refresh", {}, 204),
    (False, "GET", "/", NAV, 302),
    (False, "GET", "/settings?tab=1", {"accept": "text/html", "fetch_mode": None}, 302),
    (False, "GET", "/", {"accept": "*/*", "fetch_mode": "navigate"}, 302),
    (False, "GET", "/", {"accept": "*/*", "fetch_mode": "cors"}, 401),
    (False, "GET", "/api/prefs", NAV, 401),
    (False, "GET", "/API/prefs", NAV, 401),
    (False, "GET", "//api/prefs", NAV, 401),
    (False, "GET", "/api%2Fprefs", NAV, 401),
    (False, "GET", "/api", NAV, 401),
    (False, "GET", "/data/exports/x.json", NAV, 401),
    (False, "GET", "/manifest.JSON?x=1", NAV, 401),
    (False, "HEAD", "/", NAV, 401),
    (False, "POST", "/", NAV, 401),
    (False, None, "/", NAV, 401),
    (False, "GET", None, NAV, 401),
])
def test_the_check_answer(signed_in, method, uri, headers, status):
    assert auth.check_answer(signed_in=signed_in, method=method, uri=uri, accept=headers.get("accept"),
                             fetch_mode=headers.get("fetch_mode")) == status


# ---------------------------------------------------------------- private files

@pytest.mark.parametrize("umask", [0o000, 0o022])
def test_a_private_write_is_0600_atomic_and_leaves_no_temporary_file(tmp_path, umask):
    path = tmp_path / "passcodes"
    path.write_text("old\n")
    path.chmod(0o644)
    old = os.umask(umask)
    try:
        auth.write_private(path, b"new\n")
    finally:
        os.umask(old)
    assert path.read_bytes() == b"new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["passcodes"]


# ---------------------------------------------------------------- python -m etl passcode

@pytest.fixture()
def web_dir(tmp_path, monkeypatch):
    for name in ("FF_HOME", "FF_SECRETS_WEB_DIR", "FF_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("FF_HOME", str(home))
    return home / "secrets" / "web"


@pytest.fixture()
def cheap(monkeypatch):
    hasher = CountingHasher()
    monkeypatch.setattr(passcode_cli, "HASHER", hasher)
    return hasher


class Typed:
    """stdin for --stdin: one line as typed."""

    def __init__(self, text: str) -> None:
        self.text = text

    def readline(self) -> str:
        return self.text

    def isatty(self) -> bool:
        return False


def run(monkeypatch, capsys, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
    from etl.__main__ import main
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", Typed(stdin))
    monkeypatch.setattr(sys, "argv", ["etl", "passcode", *args])
    code = main()
    out, err = capsys.readouterr()
    return code, out, err


def stored(web_dir: Path) -> list[str]:
    return (web_dir / "passcodes").read_text().splitlines()


def test_add_creates_a_private_folder_and_file_and_the_line_signs_in(web_dir, monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, "add", "alex", "--stdin", stdin=GOOD + "\n")
    assert code == 0, err
    assert stat.S_IMODE(web_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((web_dir / "passcodes").stat().st_mode) == 0o600
    lines = stored(web_dir)
    assert len(lines) == 1 and auth.verify(lines, GOOD) == "alex"     # real scrypt: what the store will check
    salt, digest = lines[0].split(":")[5:]
    for secret in (GOOD, salt, digest):
        assert secret not in out + err


def test_add_keeps_what_was_typed_but_the_line_ending(web_dir, monkeypatch, capsys, cheap):
    assert run(monkeypatch, capsys, "add", "alex", "--stdin", stdin="  spaced passcode \r\n")[0] == 0
    assert auth.verify(stored(web_dir), "  spaced passcode ", hasher=CountingHasher()) == "alex"


@pytest.mark.parametrize("label,typed", [("Alex", GOOD + "\n"), ("alex", "zq7Kp2x\n"), ("alex", "123456789\n"),
                                         ("alex", "\n"), ("alex", "")])
def test_add_refuses_a_bad_label_or_passcode_and_writes_nothing(web_dir, monkeypatch, capsys, cheap, label, typed):
    code, out, err = run(monkeypatch, capsys, "add", label, "--stdin", stdin=typed)
    assert code == 2 and err and not (web_dir / "passcodes").exists()
    assert typed.strip() == "" or typed.strip() not in out + err


def test_add_of_a_taken_label_replaces_it_in_place_and_keeps_other_lines_byte_for_byte(web_dir, monkeypatch, capsys,
                                                                                       cheap):
    web_dir.mkdir(parents=True)
    other = cheap_line("blair", "another-passcode")
    before = (cheap_line("alex", "old-passcode") + "\n" + "  " + other + "  \n" + "hand-written junk\n").encode()
    (web_dir / "passcodes").write_bytes(before)
    code, out, err = run(monkeypatch, capsys, "add", "alex", "--stdin", stdin="new-passcode\n")
    assert code == 0 and "alex" in out and "replac" in out
    after = (web_dir / "passcodes").read_bytes().split(b"\n")
    assert after[1:] == before.split(b"\n")[1:]
    assert auth.verify([after[0].decode()], "new-passcode", hasher=CountingHasher()) == "alex"
    assert "line 3" in err and "junk" not in err


def test_add_refuses_a_ninth_passcode(web_dir, monkeypatch, capsys, cheap):
    web_dir.mkdir(parents=True)
    before = "".join(cheap_line(f"p{k}", f"passcode-{k}") + "\n" for k in range(8))
    (web_dir / "passcodes").write_text(before)
    code, _, err = run(monkeypatch, capsys, "add", "ninth", "--stdin", stdin=GOOD + "\n")
    assert code == 2 and "8" in err
    assert (web_dir / "passcodes").read_text() == before
    assert run(monkeypatch, capsys, "add", "p3", "--stdin", stdin=GOOD + "\n")[0] == 0     # replacing one is fine


def test_add_on_a_terminal_asks_twice_without_echo(web_dir, monkeypatch, capsys, cheap):
    answers = iter([GOOD, GOOD + "x"])
    asked: list[str] = []
    monkeypatch.setattr(passcode_cli.getpass, "getpass", lambda prompt: (asked.append(prompt), next(answers))[1])
    monkeypatch.setattr(sys, "stdin", type("Tty", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(sys, "argv", ["etl", "passcode", "add", "alex"])
    from etl.__main__ import main
    assert main() == 2 and len(asked) == 2                            # the two entries differ
    assert not (web_dir / "passcodes").exists()
    answers = iter([GOOD, GOOD])
    assert main() == 0 and auth.verify(stored(web_dir), GOOD, hasher=CountingHasher()) == "alex"
    out, err = capsys.readouterr()
    assert GOOD not in out + err


def test_add_without_a_terminal_needs_stdin(web_dir, monkeypatch, capsys, cheap):
    monkeypatch.setattr(sys, "stdin", Typed(GOOD + "\n"))
    code, _, err = run(monkeypatch, capsys, "add", "alex")
    assert code == 2 and "--stdin" in err and not (web_dir / "passcodes").exists()


def test_add_stopped_at_the_prompt_writes_nothing(web_dir, monkeypatch, capsys, cheap):
    def interrupted(prompt):
        raise KeyboardInterrupt
    monkeypatch.setattr(passcode_cli.getpass, "getpass", interrupted)
    monkeypatch.setattr(sys, "stdin", type("Tty", (), {"isatty": lambda self: True})())
    code, _, err = run(monkeypatch, capsys, "add", "alex")
    assert code == 130 and not (web_dir / "passcodes").exists()


def test_list_prints_the_labels_only(web_dir, monkeypatch, capsys, cheap):
    web_dir.mkdir(parents=True)
    lines = [cheap_line("alex", GOOD), cheap_line("blair", "another-passcode")]
    (web_dir / "passcodes").write_text("\n".join(lines) + "\n")
    code, out, err = run(monkeypatch, capsys, "list")
    assert code == 0 and out.splitlines() == ["alex", "blair"]
    for line in lines:
        for field in line.split(":")[5:]:
            assert field not in out + err


def test_list_with_no_file_says_nobody_can_sign_in(web_dir, monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, "list")
    assert code == 0 and out == "" and "nobody" in err


def test_remove_drops_the_line_keeps_the_rest_and_warns_on_the_last(web_dir, monkeypatch, capsys, cheap):
    web_dir.mkdir(parents=True)
    blair = cheap_line("blair", "another-passcode")
    (web_dir / "passcodes").write_text(cheap_line("alex", GOOD) + "\n" + blair + "\n")
    code, out, err = run(monkeypatch, capsys, "remove", "alex")
    assert code == 0 and stored(web_dir) == [blair] and "nobody" not in out + err
    assert stat.S_IMODE((web_dir / "passcodes").stat().st_mode) == 0o600
    code, out, err = run(monkeypatch, capsys, "remove", "alex")
    assert code == 2 and "alex" in err
    code, out, err = run(monkeypatch, capsys, "remove", "blair")
    assert code == 0 and stored(web_dir) == [] and "nobody" in out + err


def test_a_passcode_command_without_a_subcommand_is_a_usage_error(web_dir, monkeypatch, capsys):
    assert run(monkeypatch, capsys)[0] == 2


def test_replacing_or_removing_a_passcode_says_its_browsers_are_signed_out(web_dir, monkeypatch, capsys, cheap):
    code, out, _ = run(monkeypatch, capsys, "add", "alex", "--stdin", stdin=GOOD + "\n")
    assert code == 0 and "signed out" not in out
    code, out, _ = run(monkeypatch, capsys, "add", "alex", "--stdin", stdin="a-new-passcode\n")
    assert code == 0 and len([ln for ln in out.splitlines() if "signed out" in ln]) == 1
    code, out, _ = run(monkeypatch, capsys, "remove", "alex")
    assert code == 0 and len([ln for ln in out.splitlines() if "signed out" in ln]) == 1


def test_stdin_on_a_terminal_is_refused_so_the_passcode_never_shows(web_dir, monkeypatch, capsys, cheap):
    class Terminal(Typed):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Terminal(GOOD + "\n"))
    code, out, err = run(monkeypatch, capsys, "add", "alex", "--stdin")
    assert code == 2 and "without --stdin" in err and not (web_dir / "passcodes").exists()


def test_an_open_umask_still_gives_private_folders(web_dir, monkeypatch, capsys, cheap):
    old = os.umask(0)
    try:
        assert run(monkeypatch, capsys, "add", "alex", "--stdin", stdin=GOOD + "\n")[0] == 0
    finally:
        assert os.umask(old) == 0                                     # the command leaves the umask as it found it
    for folder in (web_dir, web_dir.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder
    assert stat.S_IMODE((web_dir / "passcodes").stat().st_mode) == 0o600


def test_a_wider_secrets_folder_is_tightened_and_said_so(web_dir, monkeypatch, capsys, cheap):
    web_dir.mkdir(parents=True)
    web_dir.chmod(0o755)
    code, out, err = run(monkeypatch, capsys, "add", "alex", "--stdin", stdin=GOOD + "\n")
    assert code == 0 and stat.S_IMODE(web_dir.stat().st_mode) == 0o700
    assert len([ln for ln in (out + err).splitlines() if "0700" in ln]) == 1
    code, out, err = run(monkeypatch, capsys, "list")
    assert "0700" not in out + err                                    # said once, when it was tightened


def test_the_cli_reads_the_line_rules_from_the_one_module_the_store_uses():
    assert Path(passcode_cli.auth.__file__).resolve() == (Path(__file__).parents[1] / "deploy" / "auth.py").resolve()
    assert passcode_cli.HASHER is passcode_cli.auth.scrypt


def test_json_of_the_sessions_file_is_what_dump_writes():
    s = auth.Sessions(token=counter_tokens())
    s.create(1000.0, "alex", FP)
    doc = json.loads(s.dump())
    assert doc["version"] == 1 and list(doc["sessions"].values()) == [
        {"created": 1000.0, "last_seen": 1000.0, "label": "alex", "passcode": FP}]
