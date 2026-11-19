# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""deploy/refresh.sh — the flock preamble, the order of its steps, the status it publishes and its alerts.

Runs the real script against a scratch tree with a stub `python` on PATH that stands in for the ETL (`python -m etl
…`; every other use of `python` is the real interpreter's), so no ETL step and no SimpleFIN request actually happens.
The tree holds an invented feed credential (FF_SECRETS_DIR) unless a test takes it away, a household.yaml naming "The
Sample household" (FF_CONFIG_DIR), and the alert secrets' folder (FF_SECRETS_ALERTS_DIR), empty unless a test sets up
the webhook of tests/fake_alerts.py on 127.0.0.1 (`hook`). This is the only coverage the shell has; everything it can't
reach lives in refresh_state.py, feed_check.py and alert.py on purpose.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import alert  # tests/conftest.py puts deploy/ on sys.path
import fake_alerts
import feed_check
import pytest
import refresh_state as rs
from conftest import hold_lock, write_household

from etl import simplefin as sf
from etl.config import Paths

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "refresh.sh"
HOUSEHOLD = "The Sample household"

# `python -m etl <group> <cmd>` — succeed unless the test asks otherwise,
# optionally emit the re-auth warning line refresh.sh greps out of a run (or the lines a
# file holds), and say in the log that the ingest ran. Any other `python` is the real one.
STUB_PYTHON = """#!/bin/sh
case "$*" in
  "-m etl simplefin fetch"*)
    [ -n "${STUB_WARN:-}" ] && echo "  WARN simplefin: Connection to $STUB_WARN may need attention. Auth required"
    [ -n "${STUB_WARN_FILE:-}" ] && cat "$STUB_WARN_FILE"
    [ -n "${STUB_SF_FAIL:-}" ] && exit 1 ;;
  "-m etl ingest"*)
    echo "stub ingest ran"
    [ -n "${STUB_INGEST_FAIL:-}" ] && exit 1 ;;
  "-m etl ai "*)
    echo "AI step: stub, counts only"
    exit "${STUB_AI_RC:-0}" ;;
  *) exec python3 "$@" ;;
esac
exit 0
"""


@pytest.fixture()
def tree(tmp_path):
    """A scratch $FF_ROOT with the dirs refresh.sh expects, a stub python, a feed credential, a household.yaml and an
    empty alert secrets' folder."""
    (tmp_path / "simplefin").mkdir()
    (tmp_path / "var" / "refresh").mkdir(parents=True)
    binp = tmp_path / "bin"
    binp.mkdir()
    stub = binp / "python"
    stub.write_text(STUB_PYTHON)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    write_household(tmp_path / "config", name=HOUSEHOLD)
    credential = tmp_path / "secrets" / "feed" / "simplefin_access_url"
    credential.parent.mkdir(parents=True)
    credential.write_text("https://user:invented@bridge.example.net/simplefin")
    credential.chmod(0o600)
    (tmp_path / "alerts").mkdir()
    return tmp_path


@pytest.fixture()
def hook(tree, monkeypatch):
    """A webhook set up in the tree's alert secrets, answered by tests/fake_alerts.py on 127.0.0.1."""
    with fake_alerts.serve() as h:
        monkeypatch.setenv(alert.TEST_ORIGIN_VARIABLE, h.origin)
        url = tree / "alerts" / alert.URL_FILE
        url.write_text(f"{h.origin}/hook")
        url.chmod(0o600)
        yield h


def base_env(tree: Path) -> dict[str, str]:
    """Where the tree's config, feed credential and alert secrets are, and the stub first on PATH."""
    return {"PATH": f"{tree / 'bin'}:{os.environ['PATH']}", "FF_CONFIG_DIR": str(tree / "config"),
            "FF_SECRETS_DIR": str(tree / "secrets"), "FF_SECRETS_ALERTS_DIR": str(tree / "alerts")}


def refresh(tree: Path, *args: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=120,
        env={**os.environ, **base_env(tree), "FF_ROOT": str(tree), "FF_APP": str(ROOT),
             **{k: str(v) for k, v in env.items()}})


def status(tree: Path) -> dict:
    return rs.read_status(tree / "var" / "refresh")


def test_a_clean_run_publishes_done_rc0(tree):
    r = refresh(tree)
    assert r.returncode == 0, r.stderr
    doc = status(tree)
    assert (doc["state"], doc["rc"], doc["trigger"]) == ("done", 0, "schedule")
    assert doc["steps_failed"] == [] and doc["warnings"] == []
    assert (doc["files_only"], doc["feed_errors"], doc["reauth_needed"]) == (False, [], [])
    assert doc["started_at_iso"].endswith("Z") and doc["finished_at_iso"].endswith("Z")
    assert "refresh done (rc=0" in (tree / "simplefin" / "fetch.log").read_text()


# the refresh's own files besides the store's (request, status, lock, marker): the failure streak, the re-auth set,
# the ingest-only alert streak, the note that no alert channel is set up, and the log
OWN_FILES = (".refresh_state", ".reauth_state", ".ingest_alert_state", ".alert_channel_state", "fetch.log")


def run_twice(tree: Path, **env) -> None:
    """A fetch that warns (the streak and the re-auth set are written, and an alert finds no channel), then a failed
    ingest-only run (its alert streak is written)."""
    r = refresh(tree, STUB_SF_FAIL="", STUB_WARN="Example Bank", STUB_INGEST_FAIL="", **env)
    assert r.returncode == 0, r.stderr
    r = refresh(tree, "ingest", STUB_INGEST_FAIL="1", **env)
    assert r.returncode == 1, r.stderr


def test_with_nothing_set_the_legacy_layout_is_unchanged(tree):
    """The status and the lock in var/refresh, the streak, the re-auth set, the ingest alert and the log in the feed
    store: the legacy layout, which nothing names."""
    run_twice(tree)
    assert rs.read_status(tree / "var" / "refresh")["state"] == "done"
    assert (tree / "var" / "refresh" / rs.LOCK_FILE).exists()
    assert all((tree / "simplefin" / name).exists() for name in OWN_FILES)
    assert not any((tree / "var" / "refresh" / name).exists() for name in OWN_FILES)


@pytest.mark.parametrize("how", ["FF_STATE_REFRESH_DIR", "FF_HOME", "both", "FF_STATE_ENGINE_DIR"])
def test_a_named_refresh_directory_holds_every_file_of_the_refresh(tree, how):
    """FF_STATE_REFRESH_DIR, else FF_HOME's state/refresh, is the mailbox: the status and the run's liveness lock.
    FF_STATE_ENGINE_DIR, else FF_HOME's state/engine, else a mailbox named alone, holds the script's own: the refresh
    lock, the streak, the re-auth set, the ingest alert and the log. Nothing is written in the legacy places, and each
    variable beats FF_HOME."""
    named, home, engine_named = tree / "elsewhere", tree / "home", tree / "engine"
    env = {"FF_STATE_REFRESH_DIR": named} if how in ("FF_STATE_REFRESH_DIR", "both", "FF_STATE_ENGINE_DIR") else {}
    env |= {"FF_HOME": home} if how in ("FF_HOME", "both") else {}
    env |= {"FF_STATE_ENGINE_DIR": engine_named} if how == "FF_STATE_ENGINE_DIR" else {}
    d = home / "state" / "refresh" if how == "FF_HOME" else named
    own = {"FF_STATE_REFRESH_DIR": named, "FF_STATE_ENGINE_DIR": engine_named}.get(how, home / "state" / "engine")
    run_twice(tree, **env)
    assert rs.read_status(d)["state"] == "done"
    assert (d / rs.LOCK_FILE).exists() and (own / rs.LOCK_FILE).exists()
    assert all((own / name).exists() for name in OWN_FILES)
    assert "refresh done (rc=0" in (own / "fetch.log").read_text()
    if own != d:
        assert sorted(p.name for p in d.iterdir()) == [rs.LOCK_FILE, rs.STATUS_FILE], "the mailbox holds no own file"
    assert list((tree / "var" / "refresh").iterdir()) == [] and list((tree / "simplefin").iterdir()) == []
    if how == "both":
        assert sorted(p.name for p in (home / "state").iterdir()) == ["engine"]


def test_ff_store_dir_moves_the_legacy_feed_files_with_the_feed_store(tree):
    """In the legacy layout the streak, the re-auth set, the ingest alert and the log live in the feed store, so
    FF_STORE_DIR (a root: the store is its simplefin/) moves them with it; the status stays in var/refresh."""
    stores = tree / "stores"
    run_twice(tree, FF_STORE_DIR=stores)
    assert all((stores / "simplefin" / name).exists() for name in OWN_FILES)
    assert list((tree / "simplefin").iterdir()) == []
    assert rs.read_status(tree / "var" / "refresh")["state"] == "done"


@pytest.mark.parametrize("variable", ["FF_STATE_REFRESH_DIR", "FF_HOME"])
def test_the_script_and_the_python_side_find_one_refresh_directory(tree, monkeypatch, variable):
    """refresh_state.locate is what the store and the worker resolve with: given the same variable, relative ones
    included (taken from the directory the script starts in), the script writes where it says."""
    monkeypatch.chdir(tree)
    monkeypatch.setenv(variable, "relative/place")
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True, timeout=120, cwd=tree,
                       env={**os.environ, **base_env(tree), "FF_ROOT": str(tree), "FF_APP": str(ROOT)})
    assert r.returncode == 0, r.stderr
    assert rs.read_status(rs.locate("refresh"))["state"] == "done"
    assert rs.locate("refresh").is_relative_to(tree / "relative" / "place")


def test_the_code_is_found_beside_the_script_without_ff_app(tree):
    """FF_APP only moves the code for a test: without it the script runs the ETL and refresh_state.py from the
    directory above its own, wherever it is installed."""
    calls = calls_logging_python(tree)
    env = {k: v for k, v in os.environ.items() if k != "FF_APP"}
    r = subprocess.run([str(SCRIPT), "ingest"], capture_output=True, text=True, timeout=120,
                       env={**env, **base_env(tree), "FF_ROOT": str(tree)})
    assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == ["-m etl ingest"]
    assert status(tree)["ingest_rc"] == 0          # written by the refresh_state.py beside the script


def test_a_link_at_any_name_the_script_writes_is_never_written_through(tree):
    """Under FF_HOME, with a link planted at every fixed name the script writes, in the mailbox (the status, the run's
    liveness) and in the engine's directory (the streak, the re-auth set, the ingest alert, the alert-channel note, the
    log): two runs, a refresh that warns and a failing ingest, leave every target as it was. The state files are
    replaced by files of their own; the log, a link, is left alone and the run logs to standard output."""
    home = tree / "home"
    mailbox, engine = home / "state" / "refresh", home / "state" / "engine"
    mailbox.mkdir(parents=True)
    engine.mkdir()
    targets = {}
    for d, name in [(mailbox, rs.STATUS_FILE), (mailbox, rs.LOCK_FILE),
                    *((engine, n) for n in OWN_FILES), (engine, "fetch.log.1")]:
        target = tree / "targets" / f"{d.name}-{name}"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"KEEP " + name.encode())
        (d / name).symlink_to(target)
        targets[target] = target.read_bytes()
    run_twice(tree, FF_HOME=home)
    assert {t: t.read_bytes() for t in targets} == targets
    assert rs.read_status(mailbox)["state"] == "done" and not (mailbox / rs.STATUS_FILE).is_symlink()
    assert not any((engine / n).is_symlink()
                   for n in (".refresh_state", ".reauth_state", ".ingest_alert_state", ".alert_channel_state"))
    assert (engine / ".refresh_state").read_text() == "0\n"
    assert (engine / ".reauth_state").read_text() == "Example Bank\n"
    assert (engine / ".ingest_alert_state").read_text() == "alerted\n"
    assert (engine / ".alert_channel_state").read_text() == "noted\n"
    assert (engine / "fetch.log").is_symlink() and (engine / "fetch.log.1").is_symlink()


def test_a_link_at_the_refresh_lock_stops_the_run_before_anything_is_written(tree):
    home = tree / "home"
    (home / "state" / "engine").mkdir(parents=True)
    (home / "state" / "refresh").mkdir()
    target = tree / "kept"
    target.write_bytes(b"KEEP")
    (home / "state" / "engine" / rs.LOCK_FILE).symlink_to(target)
    r = refresh(tree, FF_HOME=home)
    assert r.returncode == 1 and "not a regular file" in r.stderr
    assert target.read_bytes() == b"KEEP" and list((home / "state" / "refresh").iterdir()) == []


def test_ff_state_dir_is_not_read(tree):
    """FF_STATE_DIR names the ETL's state directory; the refresh's own directory is FF_STATE_REFRESH_DIR."""
    decoy = tree / "decoy"
    decoy.mkdir()
    r = refresh(tree, FF_STATE_DIR=decoy)
    assert r.returncode == 0, r.stderr
    assert list(decoy.iterdir()) == []
    assert status(tree)["state"] == "done"


def test_status_is_running_while_the_etl_steps_execute(tree):
    """The button needs to see `running` mid-flight, not just the outcome."""
    seen = tree / "seen.json"
    stub = tree / "bin" / "python"
    stub.write_text(f"#!/bin/sh\ncase \"$*\" in \"-m etl \"*) cp {tree / 'var' / 'refresh' / 'status.json'} {seen};;"
                    f" *) exec python3 \"$@\";; esac\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    refresh(tree)
    assert json.loads(seen.read_text())["state"] == "running"


def test_a_failed_ingest_is_reported_with_its_step(tree):
    r = refresh(tree, STUB_INGEST_FAIL="1")
    assert r.returncode == 2
    doc = status(tree)
    assert (doc["state"], doc["rc"]) == ("done", 2)
    assert "ingest" in doc["steps_failed"]


def test_a_failed_feed_still_reingests_and_reports_the_step(tree, hook):
    """A fetch failure never aborts the run: the ingest still runs, a single blip is only
    logged, and the second failing run in a row alerts."""
    log = tree / "simplefin" / "fetch.log"
    r = refresh(tree, STUB_SF_FAIL="1")
    assert r.returncode == 1
    assert status(tree)["steps_failed"] == ["SimpleFIN-fetch"]
    assert log.read_text().count("stub ingest ran") == 1
    assert "failed 2 runs in a row" not in log.read_text()

    assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    text = log.read_text()
    assert text.count("stub ingest ran") == 2
    assert text.count("data refresh has failed 2 runs in a row (steps: SimpleFIN-fetch)") == 1
    assert hook.texts() == [f"{HOUSEHOLD}: 🟠 data refresh has failed 2 runs in a row (steps: SimpleFIN-fetch). "
                            "Balances are going stale. See fetch.log. (Repeat alerts suppressed until a refresh "
                            "succeeds.)"]


def test_a_failure_streak_alerts_once_and_a_new_streak_alerts_again(tree, hook):
    """The second failed run in a row alerts; the third and fourth are logged as repeats; a good run ends the streak,
    and the next streak's second failure alerts again."""
    log = tree / "simplefin" / "fetch.log"
    for _ in range(4):
        assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    assert len(hook.received) == 1
    assert log.read_text().count("alert suppressed as a repeat of this failure streak's") == 2
    assert refresh(tree).returncode == 0
    assert (tree / "simplefin" / ".refresh_state").read_text() == "0\n"
    for _ in range(2):
        assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    texts = hook.texts()
    assert len(texts) == 2 and all(t.startswith(f"{HOUSEHOLD}: 🟠 data refresh has failed 2 runs in a row (")
                                   for t in texts), texts


def test_reauth_warnings_reach_the_status_doc(tree):
    """These are only visible in Telegram today; the modal surfaces them."""
    refresh(tree, STUB_WARN="Example Payroll Plus")
    assert status(tree)["warnings"] == ["Example Payroll Plus"]
    assert status(tree)["reauth_needed"] == ["Example Payroll Plus"]
    assert status(tree)["feed_errors"] == ["Example Payroll Plus: Auth required"]


def test_a_version_2_errlist_entry_names_its_connection_in_the_alert_and_the_status(tree, hook, capsys):
    """The lines the fetch prints for a version-2 answer's errlist (etl/simplefin.py's own, captured): the refresh
    reads each connection's name from them, never falling back to "unknown"; the status keeps each error with its
    connection and names the one whose bank asks for a sign-in."""
    sf._warn_errors({
        "errlist": [{"code": "con.auth", "msg": "Sign in again", "conn_id": "CON-fake-0001"},
                    {"code": "act.failed", "msg": "Failed to get all transactions.", "conn_id": "CON-fake-0002",
                     "account_id": "ACT-fake-0003"}],
        "connections": [{"conn_id": "CON-fake-0001", "name": "Sample Bank"},
                        {"conn_id": "CON-fake-0002", "name": "Sample Credit Union"}]})
    lines = tree / "warn-lines.txt"
    lines.write_text(capsys.readouterr().err)
    r = refresh(tree, STUB_WARN_FILE=lines)
    assert r.returncode == 0, r.stderr
    doc = status(tree)
    assert doc["warnings"] == ["Sample Bank", "Sample Credit Union"]
    assert doc["feed_errors"] == ["Sample Bank: Sign in again", "Sample Credit Union: Failed to get all transactions."]
    assert doc["reauth_needed"] == ["Sample Bank"]
    assert ("--- alert delivered: ⚠️ SimpleFIN connection(s) need re-auth: Sample Bank, Sample Credit "
            "Union. " in (tree / "simplefin" / "fetch.log").read_text())
    [text] = hook.texts()
    assert text.startswith(f"{HOUSEHOLD}: ⚠️ SimpleFIN connection(s) need re-auth: Sample Bank, Sample Credit Union.")


def test_the_reauth_alert_goes_out_once_per_set_of_connections(tree, hook):
    """The same connections warning run after run alert once; another set alerts again; a fetch that fails leaves the
    known set (and the status's connections) as they were; a clean fetch clears it, so the set's return alerts."""
    for _ in range(2):
        assert refresh(tree, STUB_WARN="Example Bank").returncode == 0
    assert len(hook.received) == 1
    assert refresh(tree, STUB_WARN="Example Credit Union").returncode == 0
    assert len(hook.received) == 2
    assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    assert (tree / "simplefin" / ".reauth_state").read_text() == "Example Credit Union\n"
    assert status(tree)["reauth_needed"] == ["Example Credit Union"]
    assert refresh(tree, STUB_WARN="Example Credit Union").returncode == 0
    assert len(hook.received) == 2
    assert refresh(tree).returncode == 0
    assert (tree / "simplefin" / ".reauth_state").read_text() == "" and status(tree)["reauth_needed"] == []
    assert refresh(tree, STUB_WARN="Example Credit Union").returncode == 0
    assert len(hook.received) == 3


def test_a_manual_run_carries_its_trigger_and_nonce(tree):
    refresh(tree, FF_TRIGGER="manual", FF_NONCE="deadbeef")
    doc = status(tree)
    assert (doc["trigger"], doc["request_nonce"]) == ("manual", "deadbeef")


def test_the_lock_is_held_for_the_whole_run(tree):
    """is_running() is what the API trusts to say "a refresh is in flight"."""
    lock = tree / "var" / "refresh" / rs.LOCK_FILE
    probe = tree / "probe"
    stub = tree / "bin" / "python"
    stub.write_text(f"#!/bin/sh\ncase \"$*\" in \"-m etl \"*) python3 -c \"import sys;sys.path.insert(0,"
                    f"'{ROOT / 'deploy'}');import refresh_state as r;open('{probe}','w')"
                    f".write(str(r.is_running(__import__('pathlib').Path('{lock}'))))\";; *) exec python3 \"$@\";; "
                    "esac\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    refresh(tree)
    assert probe.read_text() == "True"
    assert rs.is_running(lock) is False   # released when the run exited


def test_a_second_run_gives_up_with_exit_75_rather_than_racing(tree):
    """75 must not collide with refresh.sh's own 0/1/2, or "the lock was busy"
    would be indistinguishable from "the SimpleFIN fetch failed"."""
    lock = tree / "var" / "refresh" / rs.LOCK_FILE
    # hold_lock returns only once the child PROVABLY holds the flock (byte
    # handshake), so there is no under-load race and nothing to poll for.
    holder = hold_lock(lock)
    try:
        assert rs.is_running(lock), "handshake said locked but is_running disagrees"
        assert refresh(tree, FF_LOCK_WAIT="1").returncode == 75
    finally:
        holder.kill()
        holder.wait()


def calls_logging_python(tree: Path) -> Path:
    """Swap in a python stub that appends each ETL command it is given to a file, and return that file."""
    calls = tree / "etl-calls"
    stub = tree / "bin" / "python"
    stub.write_text(STUB_PYTHON.replace('  "-m etl simplefin fetch"*)\n',
                                        f'  "-m etl "*) echo "$*" >> {calls} ;;\nesac\ncase "$*" in\n'
                                        '  "-m etl simplefin fetch"*)\n', 1))
    return calls


AI_RUN = "-m etl ai run --from-refresh"


def ai_key(tree: Path, *, env: dict[str, str] | None = None) -> Path:
    """An invented AI key at the place the ETL reads it from under the script's environment (`base_env` plus `env`):
    etl/config.py Paths.secrets_ai_dir, resolved with those variables alone."""
    names = ("FF_SECRETS_AI_DIR", "FF_HOME", "FF_CONFIG_DIR")
    saved = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            os.environ.pop(n, None)
        os.environ.update({k: v for k, v in {**base_env(tree), **(env or {})}.items() if k in names})
        path = Paths().secrets_ai_dir() / "api_key"
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sk-invented-not-a-key\n")
    path.chmod(0o600)
    return path


def test_the_steps_run_in_their_order_and_the_ingest_follows_a_failed_fetch(tree):
    """The fetch, the AI categorization step, then the ingest — the ingest after a failed fetch too, so new statement
    files are never left out. With no AI key stored the AI step starts nothing; with one, it runs between the two,
    bounded. The ETL is run as `python -m etl`, with no uv and no curl anywhere in the script."""
    calls = calls_logging_python(tree)
    assert refresh(tree).returncode == 0
    assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    assert calls.read_text().splitlines() == ["-m etl simplefin fetch", "-m etl ingest"] * 2
    calls.unlink()
    ai_key(tree)
    assert refresh(tree).returncode == 0
    assert refresh(tree, STUB_SF_FAIL="1").returncode == 1
    assert calls.read_text().splitlines() == ["-m etl simplefin fetch", AI_RUN, "-m etl ingest"] * 2
    script = SCRIPT.read_text("utf-8")
    assert script.index("categorize_step\nINGEST_OPEN=1") > script.index('run_step "SimpleFIN fetch"')
    assert 'run_step "AI step" timeout -k 10 "$AI_TIMEOUT_S" python -m etl ai run --from-refresh' in script
    assert "\nAI_TIMEOUT_S=120\n" in script
    assert not re.search(r"\buv\b|\bcurl\b", "\n".join(line for line in script.splitlines()
                                                      if not line.lstrip().startswith("#")))


# ------------------------------------------------------------------ the AI step

def test_with_no_ai_key_the_refresh_starts_nothing_for_the_ai_step(tree):
    """Nothing at the key's name: no AI step at all — no Python started for it (the script's own trace shows no
    `timeout` and no `etl ai`), no state file of its own written, the refresh as it always was."""
    calls = calls_logging_python(tree)
    r = subprocess.run(["bash", "-x", str(SCRIPT)], capture_output=True, text=True, timeout=120,
                       env={**os.environ, **base_env(tree), "FF_ROOT": str(tree), "FF_APP": str(ROOT),
                            "FF_LOCK_HELD": "1"})
    assert r.returncode == 0, r.stderr
    trace = [line for line in r.stderr.splitlines() if line.startswith("+")]
    assert any("categorize_step" in line for line in trace)                  # the trace is not blind: it ran
    assert not any(re.search(r"\btimeout\b|etl ai|ai_reset_step", line) for line in trace)
    assert calls.read_text().splitlines() == ["-m etl simplefin fetch", "-m etl ingest"]
    assert not (tree / "simplefin" / ".ai_fail_state").exists() and not (tree / "simplefin" / ".ai_alert_state").exists()


@pytest.mark.parametrize("where", ["FF_CONFIG_DIR", "FF_SECRETS_AI_DIR", "FF_HOME"])
def test_the_script_looks_for_the_key_where_the_etl_reads_it(tree, where):
    env = {"FF_SECRETS_AI_DIR": str(tree / "ai-secrets")} if where == "FF_SECRETS_AI_DIR" else \
        {"FF_HOME": str(tree / "home")} if where == "FF_HOME" else {}
    calls = calls_logging_python(tree)
    key = ai_key(tree, env=env)
    assert refresh(tree, **env).returncode == 0
    assert AI_RUN in calls.read_text().splitlines()
    key.unlink()
    calls.unlink()
    assert refresh(tree, **env).returncode == 0
    assert AI_RUN not in calls.read_text().splitlines()


def test_a_link_at_the_keys_name_is_something_there_and_the_step_decides(tree):
    """Even a link to nowhere is something at the name: the step runs, and `ai run` refuses it with its own line."""
    calls = calls_logging_python(tree)
    key = ai_key(tree)
    key.unlink()
    key.symlink_to(tree / "nowhere")
    assert refresh(tree).returncode == 0
    assert AI_RUN in calls.read_text().splitlines()


def test_a_failing_ai_step_never_fails_the_refresh_and_is_counted_in_the_log(tree):
    ai_key(tree)
    for n in (1, 2):
        r = refresh(tree, STUB_AI_RC="1")
        assert r.returncode == 0, r.stderr
        doc = status(tree)
        assert (doc["state"], doc["rc"], doc["steps_failed"]) == ("done", 0, [])
        assert f"the AI step has failed {n} run(s) in a row" in (tree / "simplefin" / "fetch.log").read_text()
    assert (tree / "simplefin" / ".refresh_state").read_text().strip() == "0"
    assert "stub ingest ran" in (tree / "simplefin" / "fetch.log").read_text()
    assert refresh(tree).returncode == 0
    assert (tree / "simplefin" / ".ai_fail_state").read_text() == ""
    assert refresh(tree, STUB_AI_RC="124").returncode == 0
    log = (tree / "simplefin" / "fetch.log").read_text()
    assert "the AI step was stopped after 120s" in log and "the AI step has failed 1 run(s) in a row" in log


def test_a_rejected_key_alerts_once_per_streak(tree, hook):
    """The provider refusing the key (exit 3) sends one alert, prefixed with the household's name and naming no key
    and no vendor; the runs after it in the streak are logged only; a run that ends well ends the streak, and the next
    refusal alerts again. The refresh's own streak and status never move."""
    key = ai_key(tree)
    for _ in range(3):
        assert refresh(tree, STUB_AI_RC="3").returncode == 0
    [text] = hook.texts()
    assert text.startswith(f"{HOUSEHOLD}: 🟠 AI categorization is paused") and key.read_text().strip() not in text
    assert "suppressed as a repeat" in (tree / "simplefin" / "fetch.log").read_text()
    assert status(tree)["rc"] == 0 and (tree / "simplefin" / ".refresh_state").read_text().strip() == "0"
    assert refresh(tree).returncode == 0
    assert refresh(tree, STUB_AI_RC="3").returncode == 0
    assert len(hook.texts()) == 2


@pytest.mark.parametrize("told, with_key, want", [
    ({"FF_AI_RUN": "1"}, True, [AI_RUN, "-m etl ingest"]),
    ({"FF_AI_RUN": "1"}, False, ["-m etl ingest"]),                       # no key: no step, as in a refresh
    ({"FF_AI_RESET": "1"}, False, ["-m etl ai reset", "-m etl ingest"]),   # forgetting needs no key
    ({"FF_AI_RESET": "1", "FF_AI_RUN": "1"}, True, ["-m etl ai reset", AI_RUN, "-m etl ingest"]),
    ({"FF_AI_RESET": "", "FF_AI_RUN": ""}, True, ["-m etl ingest"]),
])
def test_an_ingest_only_run_does_what_the_ai_markers_asked_first(tree, told, with_key, want):
    calls = calls_logging_python(tree)
    if with_key:
        ai_key(tree)
    r = refresh(tree, "ingest", STUB_AI_RC="1", **told)
    assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == want


# ------------------------------------------------------------------ a household with no bank feed

def test_with_no_feed_credential_the_refresh_reads_the_files_alone(tree, hook):
    """Nothing at the feed credential's place: no fetch, the ingest alone, a good run with no failed step, no streak and
    no alert however often it runs, and a status that says the run read the files only."""
    (tree / "secrets" / "feed" / "simplefin_access_url").unlink()
    calls = calls_logging_python(tree)
    for _ in range(3):
        r = refresh(tree)
        assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == ["-m etl ingest"] * 3
    doc = status(tree)
    assert (doc["rc"], doc["steps_failed"], doc["warnings"]) == (0, [], [])
    assert (doc["files_only"], doc["feed_errors"], doc["reauth_needed"]) == (True, [], [])
    assert doc["ingest_rc"] == 0
    assert (tree / "simplefin" / ".refresh_state").read_text() == "0\n"
    assert hook.received == []
    assert "no bank feed is set up" in (tree / "simplefin" / "fetch.log").read_text()


def test_with_no_feed_a_failed_ingest_still_alerts(tree, hook):
    (tree / "secrets" / "feed" / "simplefin_access_url").unlink()
    assert refresh(tree, STUB_INGEST_FAIL="1").returncode == 2
    assert status(tree)["steps_failed"] == ["ingest"]
    [text] = hook.texts()
    assert text.startswith(f"{HOUSEHOLD}: 🔴 data refresh FAILED (ingest errored; steps: ingest).")


def test_a_feed_that_goes_away_clears_its_connections(tree):
    """The connections a fetch named are no longer the household's once it has no feed."""
    assert refresh(tree, STUB_WARN="Example Bank").returncode == 0
    assert status(tree)["reauth_needed"] == ["Example Bank"]
    (tree / "secrets" / "feed" / "simplefin_access_url").unlink()
    assert refresh(tree).returncode == 0
    assert (status(tree)["reauth_needed"], (tree / "simplefin" / ".reauth_state").read_text()) == ([], "")


@pytest.mark.parametrize("make", [
    lambda p: (p.chmod(0o640), p),
    lambda p: (p.unlink(), p.mkdir(mode=0o700)),
    lambda p: (p.unlink(), p.symlink_to(p.parent / "nowhere")),
], ids=["group-readable", "a-folder", "a-dangling-link"])
def test_a_credential_that_is_there_but_cannot_be_used_is_a_fetch_that_fails_with_its_plain_reason(tree, make):
    """Something at the credential's place is a feed: the check says fetch, and the fetch refuses it with one plain
    line (never a traceback), so the run is a failure with its streak, not a reading of the files."""
    credential = tree / "secrets" / "feed" / "simplefin_access_url"
    make(credential)
    paths_env = {"FF_SECRETS_DIR": str(tree / "secrets"), "FF_CONFIG_DIR": str(tree / "config")}
    r = subprocess.run(["python3", str(ROOT / "deploy" / "feed_check.py")], capture_output=True, text=True,
                       timeout=60, env={**os.environ, **paths_env})
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    with pytest.raises(SystemExit) as refused:
        sf.read_access_url(credential)
    message = refused.value.code
    assert isinstance(message, str) and str(credential) in message and "invented" not in message
    assert ("group/other-accessible" in message or "Cannot read" in message or "No SimpleFIN credential" in message)


def test_the_check_names_the_place_the_fetch_reads(tree, monkeypatch):
    """feed_check.py looks where the fetch looks (Paths.credential_path): FF_SECRETS_DIR's, else FF_HOME's, else the
    legacy feed store's, which accounts.yaml may move; and with nothing there it answers NO_FEED."""
    monkeypatch.setenv("FF_SECRETS_DIR", str(tree / "secrets"))
    assert feed_check.credential_path() == tree / "secrets" / "feed" / "simplefin_access_url"
    monkeypatch.delenv("FF_SECRETS_DIR")
    monkeypatch.setenv("FF_HOME", str(tree / "home"))
    assert feed_check.credential_path() == tree / "home" / "secrets" / "feed" / "simplefin_access_url"
    monkeypatch.delenv("FF_HOME")
    config = tree / "legacy" / "config"
    config.mkdir(parents=True)
    (config / "accounts.yaml").write_text("simplefin: {store_dir: feeds}\naccounts: {}\n")
    assert feed_check.credential_path(Paths(config)) == (tree / "legacy" / "feeds" / "credentials" / "access_url")
    (config / "accounts.yaml").write_text("simplefin: [not, a, mapping]\n")
    assert feed_check.credential_path(Paths(config)) == Paths(config).credential_path(Paths(config).store_dir({}))
    r = subprocess.run(["python3", str(ROOT / "deploy" / "feed_check.py")], capture_output=True, text=True,
                       timeout=60, env={**os.environ, "FF_SECRETS_DIR": str(tree / "empty")})
    assert r.returncode == feed_check.NO_FEED


# ------------------------------------------------------------------ alerts

def test_with_no_alert_channel_the_log_says_so_once(tree):
    """No channel set up: the alerts are not sent, and the log says why at the first one only; once an alert goes out
    the note is cleared, so a channel removed later is said again."""
    log = tree / "simplefin" / "fetch.log"
    for _ in range(3):
        refresh(tree, STUB_INGEST_FAIL="1")
    assert log.read_text().count("--- alerts are not sent: no alert channel is set up") == 1
    assert "alert delivered" not in log.read_text() and "NOT delivered" not in log.read_text()
    with fake_alerts.serve() as h:
        (tree / "alerts" / alert.URL_FILE).write_text(f"{h.origin}/hook")
        (tree / "alerts" / alert.URL_FILE).chmod(0o600)
        refresh(tree, STUB_INGEST_FAIL="1", **{alert.TEST_ORIGIN_VARIABLE: h.origin})
        assert len(h.received) == 1
    assert (tree / "simplefin" / ".alert_channel_state").read_text() == ""
    (tree / "alerts" / alert.URL_FILE).unlink()
    refresh(tree, STUB_INGEST_FAIL="1")
    assert log.read_text().count("--- alerts are not sent: no alert channel is set up") == 2


def test_an_alert_that_is_not_taken_is_logged_with_why(tree, hook):
    hook.status = 500
    refresh(tree, STUB_INGEST_FAIL="1")
    text = (tree / "simplefin" / "fetch.log").read_text()
    assert "alert: the webhook did not take the alert: it answered HTTP 500" in text
    assert "!!! alert NOT delivered: 🔴 data refresh FAILED" in text


def test_the_alert_credentials_never_come_from_the_environment(tree):
    """The variables an older setup read are no channel: with the alert secrets' folder empty nothing is sent."""
    with fake_alerts.serve() as h:
        refresh(tree, STUB_INGEST_FAIL="1", ALERT_URL=f"{h.origin}/hook", TELEGRAM_BOT_TOKEN=fake_alerts.TOKEN,
                TELEGRAM_CHAT_ID=fake_alerts.CHAT, **{alert.TEST_ORIGIN_VARIABLE: h.origin})
        assert h.received == []


def test_the_log_is_moved_aside_once_it_passes_its_size(tree):
    """A fetch.log past LOG_MAX_BYTES becomes fetch.log.1 (replacing the older one) before the run writes: the run's
    lines start a new log, and the two never hold much more than twice the size."""
    log = tree / "simplefin" / "fetch.log"
    (tree / "simplefin" / "fetch.log.1").write_text("the oldest log\n")
    log.write_text("x" * 1048577)
    assert refresh(tree).returncode == 0
    assert (tree / "simplefin" / "fetch.log.1").stat().st_size == 1048577
    assert log.stat().st_size < 4096 and "refresh done (rc=0" in log.read_text()
    assert refresh(tree).returncode == 0
    assert (tree / "simplefin" / "fetch.log.1").stat().st_size == 1048577 and log.read_text().count("refresh done") == 2
    script = SCRIPT.read_text("utf-8")
    assert re.search(r"^LOG_MAX_BYTES=1048576 ", script, re.MULTILINE)


def test_the_log_stamps_are_utc_whatever_the_process_zone(tree):
    """The scheduler's jobs run with the household's zone as TZ (supercronic's CRON_TZ); the log's own stamps are UTC
    either way, so the refresher's and the scheduler's lines read alike."""
    assert refresh(tree, TZ="Pacific/Kiritimati").returncode == 0
    assert re.search(r"=== \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z refresh start ===",
                     (tree / "simplefin" / "fetch.log").read_text())


def test_ingest_mode_runs_no_fetch_and_leaves_the_streak_alone(tree, hook):
    """`refresh.sh ingest` runs the ingest step alone: no feed fetch, so the failure streak, the re-auth state and the
    last refresh's own status fields (what the dashboard shows of it) are left as they were; it records the ingest's
    start, end and exit code, counts as an ingest-only run, and exits with the ingest's code."""
    calls = calls_logging_python(tree)
    streak, reauth = tree / "simplefin" / ".refresh_state", tree / "simplefin" / ".reauth_state"
    streak.write_text("3\n")
    reauth.write_text("Example Bank\n")
    d = tree / "var" / "refresh"
    rs._main(["write-status", "--dir", str(d), "--state", "done", "--trigger", "manual", "--nonce", "n1",
              "--rc", "1", "--failed", "SimpleFIN-fetch", "--warnings", "Example Bank"])
    last_refresh = status(tree)

    r = refresh(tree, "ingest")
    assert r.returncode == 0, r.stderr
    assert calls.read_text().splitlines() == ["-m etl ingest"]
    assert (streak.read_text(), reauth.read_text()) == ("3\n", "Example Bank\n")
    doc = status(tree)
    assert {k: doc[k] for k in last_refresh} == last_refresh
    assert doc["ingest_rc"] == 0 and doc["ingest_finished_at"] >= doc["ingest_started_at"]
    assert doc["ingest_runs"] == [doc["ingest_started_at"]]
    assert "alert" not in (tree / "simplefin" / "fetch.log").read_text()

    # a failing ingest: its exit code, recorded and returned, and one alert; still no fetch and no streak
    r = refresh(tree, "ingest", STUB_INGEST_FAIL="1")
    assert r.returncode == 1
    assert calls.read_text().splitlines() == ["-m etl ingest"] * 2
    doc = status(tree)
    assert doc["ingest_rc"] == 1 and len(doc["ingest_runs"]) == 2
    assert {k: doc[k] for k in last_refresh} == last_refresh
    assert streak.read_text() == "3\n"
    log = (tree / "simplefin" / "fetch.log").read_text()
    assert log.count("--- alert delivered: 🔴 data refresh FAILED (an ingest without a feed fetch") == 1
    assert len(hook.received) == 1


INGEST_ALERT = "--- alert delivered: 🔴 data refresh FAILED (an ingest without a feed fetch"
REPEAT = "an ingest without a feed fetch failed again (rc=1): alert suppressed as a repeat"


def test_failed_ingest_only_runs_alert_once_per_streak(tree, hook):
    """A broken config fails every ingest-only run (one per settled save): the first alerts, the next ones while
    none succeeds are logged as repeats, and the streak's state file is truncated, never removed, by a success."""
    log, state = tree / "simplefin" / "fetch.log", tree / "simplefin" / ".ingest_alert_state"
    for _ in range(2):
        assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert (log.read_text().count(INGEST_ALERT), log.read_text().count(REPEAT)) == (1, 1)
    assert refresh(tree, "ingest").returncode == 0
    assert state.exists() and state.read_text() == ""
    assert len(hook.received) == 1


def test_a_successful_ingest_only_run_ends_the_streak(tree, hook):
    """Failed, succeeded, failed: two streaks, two alerts."""
    log = tree / "simplefin" / "fetch.log"
    assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert refresh(tree, "ingest").returncode == 0
    assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert (log.read_text().count(INGEST_ALERT), log.read_text().count(REPEAT)) == (2, 0)
    assert len(hook.received) == 2


def test_a_refresh_whose_ingest_succeeds_ends_the_streak_and_its_own_failures_still_alert(tree, hook):
    """A successful ingest of either mode ends the streak: a refresh between two failed ingest-only runs makes the
    second alert again. A refresh's own failed ingest alerts every run, as before, and leaves the streak as it is."""
    log = tree / "simplefin" / "fetch.log"
    assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert refresh(tree).returncode == 0
    assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert log.read_text().count(INGEST_ALERT) == 2
    for _ in range(2):
        assert refresh(tree, STUB_INGEST_FAIL="1").returncode == 2
    assert log.read_text().count("data refresh FAILED (ingest errored; steps: ingest)") == 2
    assert refresh(tree, "ingest", STUB_INGEST_FAIL="1").returncode == 1
    assert log.read_text().count(INGEST_ALERT) == 2 and log.read_text().count(REPEAT) == 1


def test_ingest_mode_counts_the_run_the_runner_already_claimed_once(tree):
    """Run by the refresher, the script is told the start the runner recorded (FF_INGEST_STARTED_AT) and writes the
    same one again, so the run is counted once."""
    d = tree / "var" / "refresh"
    claimed = 1_786_000_000.25
    rs.write_status(d, ingest_started_at=claimed, ingest_finished_at=None, ingest_rc=None, ingest_runs=[claimed])
    r = refresh(tree, "ingest", FF_LOCK_HELD="1", FF_TRIGGER="ingest", FF_INGEST_STARTED_AT=repr(claimed))
    assert r.returncode == 0, r.stderr
    doc = status(tree)
    assert (doc["ingest_started_at"], doc["ingest_runs"], doc["ingest_rc"]) == (claimed, [claimed], 0)


def test_ingest_mode_takes_the_refresh_lock(tree):
    """An ingest never runs beside a refresh: it waits on the same lock, and gives up with 75 as a refresh does."""
    lock = tree / "var" / "refresh" / rs.LOCK_FILE
    holder = hold_lock(lock)
    try:
        assert refresh(tree, "ingest", FF_LOCK_WAIT="1").returncode == 75
    finally:
        holder.kill()
        holder.wait()
    assert "ingest_started_at" not in status(tree)


def test_a_refresh_records_its_ingest_step_as_an_ingest(tree):
    """A refresh's own ingest step writes the ingest fields as it starts and ends, so an ingest the dashboard asked for
    before it began is answered by it; it is no ingest-only run, so ingest_runs is untouched."""
    r = refresh(tree)
    assert r.returncode == 0, r.stderr
    doc = status(tree)
    assert doc["started_at"] <= doc["ingest_started_at"] <= doc["ingest_finished_at"] <= doc["finished_at"]
    assert doc["ingest_rc"] == 0 and "ingest_runs" not in doc
    assert refresh(tree, STUB_INGEST_FAIL="1").returncode == 2
    assert status(tree)["ingest_rc"] == 1


def test_test_alert_bypasses_the_lock_entirely(tree):
    """It sends a message and exits; making it queue behind a running refresh
    would defeat the point of a pipe check."""
    lock = tree / "var" / "refresh" / rs.LOCK_FILE
    holder = hold_lock(lock)
    try:
        r = subprocess.run(
            [str(SCRIPT), "test-alert"], capture_output=True, text=True, timeout=30,
            env={**os.environ, **base_env(tree), "FF_ROOT": str(tree), "FF_APP": str(ROOT)})
        assert r.returncode == 0
        assert "test alert" in r.stdout
    finally:
        holder.kill()
        holder.wait()


def test_test_alert_says_how_it_went(tree, hook):
    r = refresh(tree, "test-alert")
    assert r.returncode == 0 and "--- test alert delivered" in r.stdout
    assert hook.texts() == [f"{HOUSEHOLD}: ✅ test alert — if you got this, failure alerts are working."]
    (tree / "alerts" / alert.URL_FILE).unlink()
    r = refresh(tree, "test-alert")
    assert "--- test alert not sent: no alert channel is set up" in r.stdout
