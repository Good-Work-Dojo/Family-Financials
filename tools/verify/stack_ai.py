#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The stack's AI check: the demo household's AI step switched on and then off through the store, as the dashboard's
Settings card does it, with the provider a stand-in (tests/fake_llm.py) on the refresher's own loopback. No request
ever leaves the machine: the stand-in runs in a container sharing the refresher's network namespace, so it is the
refresher's 127.0.0.1, and the key is an invented one.

  on   1. the demo's truth for the AI step (which vendors it asks about, which it keeps back or never considers, each
          vendor's rows), from the generator in the ETL image at the install's end day;
       2. signed in, the exports as the demo left them: the AI step off, and every export the manifest lists but
          itself and ai_status.json kept as the AI-off export;
       3. the scheduler stopped (its refresh would run the AI step against the real provider's address), the
          refresher recreated from a copy of the install's compose.yaml whose refresher alone holds the test seam
          (FF_AI_TEST_ORIGIN, the stand-in's address) beside the workers' whole environment, the running refresher
          seen to hold it, and only then an invented key stored in secrets/ai/api_key (0600);
       4. the stand-in started in the refresher's network namespace, answering every vendor with one category;
       5. switched on through the store's real requests: the prefs' `ai` consent (PUT /api/prefs) and
          POST /api/refresh {"mode": "ai"}; then the AI run and the ingest after it awaited;
       6. the two vendors that pass every filter are filed by AI in transactions.json, and no other row is;
       7. the stand-in was asked once, with the key as the credential, about exactly those two vendors, and its
          request holds nothing on the never-send list (a vendor kept back or never considered, a household name, a
          date, an amount, a row or account id);
       8. ai_status.json says on, with the counts applied.
  off  1. switched off and its answers forgotten through the store, as the card's Turn off and Forget do: the prefs'
          `ai` with enabled false, POST /api/refresh {"mode": "ingest"} and {"mode": "ai_reset"}; the ingest awaited;
       2. every export compared is the AI-off export byte for byte;
       3. ai_status.json says off, with nothing applied and nothing stored;
       4. the stand-in, the key and the compose copy removed, the refresher recreated from the install's own
          compose.yaml (no seam) and the scheduler started again.
Each step prints one line (`ok <name>` or `FAIL <name>: <what>`; a step after a failed one is not run and fails), then
`stack ai <phase>: P/T`. Exit 0 when nothing failed. Stdlib only, on the host's python3, reusing stack_matrix's raw
HTTP and sign-in. `on` also writes the AI-filed vendors' names, one a line, to <work>/ai-vendors.txt for the browser.

Usage: stack_ai.py on|off --port P --passcode-file F --compose "<prefix>" --ai-compose "<prefix on the copy>"
                          --install DIR --work DIR --etl-image I --fake tests/fake_llm.py --standin NAME --end DAY
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_matrix as m  # noqa: E402  (tools/verify is not a package)

SEAM = "FF_AI_TEST_ORIGIN"
STANDIN_PORT = 18218                  # on the refresher's own loopback, inside its network namespace
KEY_TEXT = "stack-invented-ai-key-0001"    # an invented key: no provider knows it, and none is ever asked
ANSWER = ("Entertainment", "Hobbies")      # the pair the stand-in answers every vendor with (the starter's taxonomy)
CONFIDENCE = 0.95
TEST_FILE = "compose.ai-test.yaml"    # the install's compose.yaml with the seam in the refresher's environment
WAIT_S = 300                          # the settle (60 s), the AI run and the ingest, with room to spare
# the refresher's command line in compose.yaml, after which the copy states its environment, and the anchor of what
# every worker shares (x-worker), whose whole environment that statement carries over
REFRESHER = '    command: ["python", "/opt/ff/deploy/refresh_runner.py"]\n'
WORKER = "x-worker: &worker\n"
# what the stand-in's request may never hold, beyond the vendors and names: a date, a row's or an account's id, a
# dollar amount
NEVER_PATTERNS = (re.compile(r"\d{4}-\d{2}-\d{2}"), re.compile(r"\btx_[0-9a-f]"), re.compile(r"\bdemo_"),
                  re.compile(r"\d+\.\d{2}\b"))


# ---------------------------------------------------------------- the pure parts

def worker_environment(text: str) -> list[str]:
    """The entries of the workers' shared environment (x-worker's `environment:` mapping), each `KEY: value` as
    written. ValueError when compose.yaml has no such mapping."""
    if text.count(WORKER) != 1:
        raise ValueError("compose.yaml has no single x-worker block")
    lines = []                       # the block runs to the next line at the top level
    for line in text.split(WORKER, 1)[1].splitlines():
        if line and not line.startswith(" "):
            break
        lines.append(line)
    try:
        start = lines.index("  environment:")
    except ValueError:
        raise ValueError("the x-worker block states no environment mapping") from None
    entries = []
    for line in lines[start + 1:]:
        if not line.startswith("    ") or line.startswith("     "):
            break
        entries.append(line.strip())
    if not entries or any(": " not in e for e in entries):
        raise ValueError("the x-worker environment is not a plain mapping")
    return entries


def seam_compose(text: str, origin: str) -> str:
    """The install's compose.yaml with the test seam set to `origin` in the refresher's environment alone, beside every
    entry of the environment the workers share (an explicit `environment:` replaces the merged one, so all of it is
    carried over). ValueError when the file is not shaped as expected."""
    if text.count(REFRESHER) != 1 or SEAM in text:
        raise ValueError("compose.yaml is not the shape this test writes a copy of")
    env = "".join(f"      {e}\n" for e in worker_environment(text))
    return text.replace(REFRESHER, f'{REFRESHER}    environment:\n{env}      {SEAM}: "{origin}"\n')


def compared_exports(manifest: dict) -> list[str]:
    """The export files the AI-off comparison reads byte for byte: every file the manifest lists but itself (its
    generation stamps move with every ingest) and ai_status.json (which says what the step did)."""
    return sorted(n for n in manifest.get("files") or () if n not in ("manifest.json", "ai_status.json"))


def never_words(truth: dict) -> list[str]:
    """The words the stand-in's request may never hold: every vendor kept back or never considered, and the
    household's names (each member, the surname, the joint label)."""
    ai, house = truth["ai"], truth["household"]
    names = [mbr["name"] for mbr in house["members"]] + [house["surname"], house["joint_label"]]
    return sorted({*ai["withheld"], *ai["not_candidate"], *(n.casefold() for n in names)})


def request_problems(requests: list[dict], key: str, truth: dict) -> list[str]:
    """What is wrong with what the stand-in received: anything but one request, with `key` as its credential, about
    exactly the vendors truth says the step asks about, whose body holds no never-send word or pattern."""
    if len(requests) != 1:
        return [f"{len(requests)} requests, want 1"]
    [req] = requests
    problems = [] if req.get("credential") == key else ["the request did not carry the stored key as its credential"]
    users = [msg for msg in req["body"].get("messages") or () if msg.get("role") == "user"]
    try:
        sent = json.loads(users[-1]["content"])
    except (IndexError, KeyError, TypeError, ValueError):
        return [*problems, "the request holds no vendors"]
    if sorted(sent.get("vendors") or ()) != truth["ai"]["sendable"]:
        problems.append(f"it asked about {sorted(sent.get('vendors') or ())}, want {truth['ai']['sendable']}")
    text = json.dumps(req["body"], ensure_ascii=False).casefold()
    found = [w for w in never_words(truth) if re.search(rf"(?<![^\W\d_]){re.escape(w)}(?![^\W\d_])", text)]
    found += [p.pattern for p in NEVER_PATTERNS if p.search(text)]
    if found:
        problems.append(f"the request holds what is never sent: {found}")
    return problems


def filed_problems(transactions: list[dict], truth: dict) -> list[str]:
    """Every row of a vendor the step asks about is filed by AI under the stand-in's answer, and no other row is."""
    ai = truth["ai"]
    want = {uid for key in ai["sendable"] for uid in ai["rows"][key]}
    filed = {t["id"]: t for t in transactions if t["category_source"] == "ai"}
    problems = []
    if not want:
        problems.append("truth names no row the step asks about")
    if set(filed) != want:
        problems.append(f"{len(set(filed) - want)} row(s) filed by AI that should not be, {len(want - set(filed))} "
                        "that should be and are not")
    wrong = [uid for uid, t in filed.items() if (t["category"], t["subcategory"]) != ANSWER]
    if wrong:
        problems.append(f"{len(wrong)} AI row(s) not under {ANSWER}")
    return problems


def status_problems(status: dict, truth: dict, *, on: bool) -> list[str]:
    """ai_status.json on (configured, enabled, answers stored, the vendors asked about applied, the last run answered)
    or off (enabled false, nothing applied, nothing stored)."""
    ai = truth["ai"]
    rows = sum(len(ai["rows"][key]) for key in ai["sendable"])
    if on:
        want = {"configured": True, "enabled": True, "answers_stored": True,
                "applied": {"vendors": len(ai["sendable"]), "rows": rows}}
        last = status.get("last_run") or {}
        run = (last.get("outcome"), last.get("sent"), last.get("answered"), last.get("cached"))
        extra = [] if run == ("answered", *(len(ai["sendable"]),) * 3) else [f"the last run was {run}"]
    else:
        want = {"enabled": False, "answers_stored": False, "applied": {"vendors": 0, "rows": 0}}
        extra = []
    got = {k: status.get(k) for k in want}
    return ([] if got == want else [f"ai_status says {got}, want {want}"]) + extra


# ---------------------------------------------------------------- the stack

class Run:
    """What the steps share: the stack, the compose prefixes, the install and the work directory."""

    def __init__(self, args: argparse.Namespace):
        with open(args.passcode_file) as fh:
            passcode = fh.read().strip()
        self.ctx = m.Ctx(("127.0.0.1", args.port), f"127.0.0.1:{args.port}", passcode=passcode,
                         compose=args.compose.split())
        self.ai_compose = args.ai_compose.split()
        self.install, self.work = Path(args.install), Path(args.work)
        self.etl_image, self.fake, self.standin, self.end = args.etl_image, args.fake, args.standin, args.end
        self.truth: dict = {}

    # -- requests
    def sign_in(self) -> None:
        r = m.login(self.ctx, self.ctx.passcode)
        if r is None or r.status != 204:
            raise RuntimeError(f"sign-in got {r.status if r else 'no answer'}")
        self.ctx.cookies[m.SESSION] = m.pair(m.cookie_of(r, "ff_session"))

    def get(self, path: str) -> bytes:
        raw = m.build("GET", path, [("Host", self.ctx.host), ("Cookie", self.ctx.cookies[m.SESSION]), *m.FETCH,
                                    ("Connection", "close")])
        answers = self.ctx.send(self.ctx.addr, raw, ["GET"], 1, timeout=60.0)
        if not answers or answers[0].status != 200:
            raise RuntimeError(f"GET {path} got {answers[0].status if answers else 'no answer'}")
        return answers[0].body

    def send(self, method: str, path: str, doc: dict, want: int) -> None:
        body = json.dumps(doc).encode()
        raw = m.build(method, path, [("Host", self.ctx.host), ("Origin", self.ctx.origin),
                                     ("Cookie", self.ctx.cookies[m.SESSION]), ("Content-Type", "application/json"),
                                     ("Content-Length", str(len(body))), *m.FETCH, ("Connection", "close")], body)
        answers = self.ctx.send(self.ctx.addr, raw, [method], 1)
        if not answers or answers[0].status != want:
            raise RuntimeError(f"{method} {path} got {answers[0].status if answers else 'no answer'}, want {want}")

    def status(self) -> dict:
        return json.loads(self.get("/ai_status.json"))

    def switch(self, enabled: bool) -> None:
        """The family's consent as the card writes it: the whole `ai` section, the version and the scope shown, and
        all of it kept when switching off."""
        prefs = json.loads(self.get("/api/prefs"))
        version = self.status()["policy_version"]
        old = prefs.get("ai") if isinstance(prefs.get("ai"), dict) else {}
        prefs["ai"] = ({"enabled": True, "consent_policy": version, "consent_scope": self.status()["scope"],
                        "consented_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())} if enabled
                       else {**old, "enabled": False})
        prefs["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        self.send("PUT", "/api/prefs", prefs, 200)

    def await_status(self, done: Callable[[dict], bool]) -> dict:
        deadline = time.monotonic() + WAIT_S
        while True:
            status = self.status()
            if done(status):
                return status
            if time.monotonic() > deadline:
                raise RuntimeError(f"after {WAIT_S} s ai_status still says {status}")
            time.sleep(3)

    # -- docker
    def compose(self, *args: str, ai: bool = False) -> str:
        done = subprocess.run([*(self.ai_compose if ai else self.ctx.compose), *args], capture_output=True,
                              text=True, timeout=300)
        if done.returncode != 0:
            raise RuntimeError(f"compose {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-300:]}")
        return done.stdout

    def docker(self, *args: str, check: bool = True) -> str:
        done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
        if check and done.returncode != 0:
            raise RuntimeError(f"docker {args[0]} exited {done.returncode}: {done.stderr.strip()[-300:]}")
        return done.stdout

    @property
    def key_path(self) -> Path:
        return self.install / "secrets" / "ai" / "api_key"

    def load_truth(self) -> None:
        self.truth = json.loads((self.work / "truth-ai.json").read_text("utf-8"))


# ---------------------------------------------------------------- the steps

GENERATE = ("import datetime, json, pathlib, sys, tempfile\n"
            "sys.path.append('/opt/ff')\n"
            "from tools.demo import generate\n"
            "t = generate.run('full', datetime.date.fromisoformat(sys.argv[1]), pathlib.Path(tempfile.mkdtemp()) / 't')\n"
            "print(json.dumps({'ai': t['figures']['ai'], 'household': t['household']}))\n")


def truth_step(run: Run) -> list[str]:
    out = run.docker("run", "--rm", "--label", m.harness().label, "--network", "none", "--user",
                     f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", run.etl_image, "python", "-c", GENERATE,
                     run.end)
    truth = json.loads(out.strip().splitlines()[-1])
    if not truth["ai"]["sendable"] or not truth["ai"]["withheld"] or not truth["ai"]["not_candidate"]:
        return [f"the demo's truth plants no AI vendors to test: {truth['ai']}"]
    run.work.mkdir(mode=0o700, parents=True, exist_ok=True)
    (run.work / "truth-ai.json").write_text(json.dumps(truth), "utf-8")
    run.truth = truth
    return []


def before_step(run: Run) -> list[str]:
    run.sign_in()
    status = run.status()
    problems = status_problems(status, run.truth, on=False)
    if status["configured"] or status["preview"] != run.truth["ai"]["preview"]:
        problems.append(f"the demo's status is not the truth's: configured {status['configured']}, preview "
                        f"{status['preview']}")
    kept = run.work / "off-exports"
    kept.mkdir(mode=0o700, exist_ok=True)
    names = compared_exports(json.loads(run.get("/manifest.json")))
    if "transactions.json" not in names:
        problems.append(f"the manifest lists no transactions.json among {names}")
    for name in names:
        (kept / name).write_bytes(run.get(f"/{name}"))
    return problems


def refresher_env(run: Run) -> list[str]:
    """The running refresher's environment, as Docker holds it."""
    refresher = run.compose("ps", "-q", "refresher").strip()
    if not refresher:
        raise RuntimeError("no refresher container")
    return json.loads(run.docker("inspect", "--format", "{{json .Config.Env}}", refresher))


def seam_step(run: Run) -> list[str]:
    """The scheduler stopped, the refresher recreated from the copy, and only once the running refresher is seen to
    hold the stand-in's address as the seam the invented key is written: with the seam missing, a run would go to the
    real provider's address."""
    run.compose("stop", "scheduler")
    text = (run.install / "compose.yaml").read_text("utf-8")
    origin = f"http://127.0.0.1:{STANDIN_PORT}"
    (run.install / TEST_FILE).write_text(seam_compose(text, origin), "utf-8")
    run.compose("up", "-d", "--wait", "refresher", ai=True)
    if f"{SEAM}={origin}" not in refresher_env(run):
        raise RuntimeError(f"the refresher does not hold {SEAM}={origin}; no key was written")
    fd = os.open(run.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(KEY_TEXT + "\n")
    return []


def standin_step(run: Run) -> list[str]:
    refresher = run.compose("ps", "-q", "refresher").strip()
    if not refresher:
        return ["no refresher container"]
    log = run.work / "llm"
    log.mkdir(mode=0o700, exist_ok=True)
    run.docker("run", "-d", "--rm", "--label", m.harness().label, "--name", run.standin, "--network",
               f"container:{refresher}", "--user", f"{os.getuid()}:{os.getgid()}", "-v",
               f"{run.fake}:/fake_llm.py:ro", "-v", f"{log}:/log", run.etl_image, "python", "-u", "/fake_llm.py",
               "--port", str(STANDIN_PORT), "--log", "/log/requests.jsonl", "--category", ANSWER[0],
               "--subcategory", ANSWER[1], "--confidence", str(CONFIDENCE))
    deadline = time.monotonic() + 30
    while "fake_llm: listening on" not in run.docker("logs", run.standin, check=False):
        if time.monotonic() > deadline:
            return ["the stand-in did not start listening within 30 s"]
        time.sleep(0.5)
    return []


def switch_on_step(run: Run) -> list[str]:
    run.switch(True)
    run.send("POST", "/api/refresh", {"mode": "ai"}, 202)
    run.await_status(lambda s: s["enabled"] and s["last_run"] is not None)
    return []


def filed_step(run: Run) -> list[str]:
    rows = json.loads(run.get("/transactions.json"))["transactions"]
    filed = sorted({t["vendor"] for t in rows if t["category_source"] == "ai"})
    (run.work / "ai-vendors.txt").write_text("".join(f"{v}\n" for v in filed), "utf-8")
    return filed_problems(rows, run.truth)


def request_step(run: Run) -> list[str]:
    path = run.work / "llm" / "requests.jsonl"
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    return request_problems([json.loads(line) for line in lines], KEY_TEXT, run.truth)


def on_status_step(run: Run) -> list[str]:
    return status_problems(run.status(), run.truth, on=True)


def switch_off_step(run: Run) -> list[str]:
    run.sign_in()
    run.switch(False)
    run.send("POST", "/api/refresh", {"mode": "ingest"}, 202)
    run.send("POST", "/api/refresh", {"mode": "ai_reset"}, 202)
    run.await_status(lambda s: not s["enabled"] and not s["answers_stored"])
    return []


def same_export_step(run: Run) -> list[str]:
    kept = run.work / "off-exports"
    names = compared_exports(json.loads(run.get("/manifest.json")))
    before = sorted(p.name for p in kept.iterdir())
    problems = [] if names == before else [f"the manifest lists {names}, the AI-off export {before}"]
    differ = [name for name in names if name in before and run.get(f"/{name}") != (kept / name).read_bytes()]
    if differ:
        problems.append(f"differ from the AI-off export: {differ}")
    return problems


def off_status_step(run: Run) -> list[str]:
    return status_problems(run.status(), run.truth, on=False)


def restore_step(run: Run) -> list[str]:
    run.docker("rm", "-f", run.standin, check=False)
    run.key_path.unlink(missing_ok=True)
    run.compose("up", "-d", "--wait", "refresher")
    (run.install / TEST_FILE).unlink(missing_ok=True)
    run.compose("start", "scheduler")
    problems = [f"the refresher still holds {SEAM}"] if any(e.startswith(f"{SEAM}=") for e in refresher_env(run)) else []
    if not run.compose("ps", "-q", "scheduler").strip():
        problems.append("the scheduler is not running")
    if run.docker("ps", "-aq", "--filter", f"name=^{run.standin}$").strip():
        problems.append("the stand-in is still there")
    return problems


PHASES: dict[str, tuple[tuple[str, Callable[[Run], list[str]]], ...]] = {
    "on": (
        ("AI: the demo's truth for the AI step, from the generator in the ETL image", truth_step),
        ("AI: as the demo leaves it, off, configured by no key, previewing what truth declares", before_step),
        ("AI: the scheduler stopped, an invented key stored, the refresher given the stand-in's address", seam_step),
        ("AI: the stand-in listens on the refresher's loopback", standin_step),
        ("AI: switched on through the store (the prefs' consent, POST /api/refresh mode ai); the run and the ingest "
         "awaited", switch_on_step),
        ("AI: the vendors that pass every filter are filed by AI, and no other row", filed_step),
        ("AI: the stand-in was asked once, with the key, about exactly those vendors, and nothing never sent",
         request_step),
        ("AI: ai_status.json says on, with the counts applied", on_status_step),
    ),
    "off": (
        ("AI: switched off and its answers forgotten through the store; the ingest awaited", switch_off_step),
        ("AI: every export but the manifest and the AI status equals the AI-off export byte for byte",
         same_export_step),
        ("AI: ai_status.json says off, nothing applied, nothing stored", off_status_step),
        ("AI: the stand-in, the key and the compose copy removed; the refresher without the seam, the scheduler "
         "running", restore_step),
    ),
}


def run_phase(run: Run, steps: Sequence[tuple[str, Callable[[Run], list[str]]]]) -> list[m.Outcome]:
    outcomes: list[m.Outcome] = []
    for name, step in steps:
        if outcomes and outcomes[-1].result != "ok" and step is not restore_step:
            outcomes.append(m.Outcome("FAIL", name, "not run: an earlier step failed"))
        else:
            try:
                problems = step(run)
            except Exception as e:  # noqa: BLE001 (a step that raises is a failed step)
                problems = [f"{type(e).__name__}: {e}"]
            outcomes.append(m.Outcome("FAIL", name, "; ".join(problems)) if problems else m.Outcome("ok", name))
        print(outcomes[-1].line(), flush=True)
    return outcomes


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("phase", choices=sorted(PHASES))
    for name in ("--passcode-file", "--compose", "--ai-compose", "--install", "--work", "--etl-image", "--fake",
                 "--standin", "--end"):
        ap.add_argument(name, required=True)
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args(argv)
    run = Run(args)
    if args.phase == "off":
        try:
            run.load_truth()
        except (OSError, ValueError) as e:
            print(f"FAIL AI: the truth the on phase wrote: {type(e).__name__}", flush=True)
    outcomes = run_phase(run, PHASES[args.phase])
    print(f"stack ai {args.phase}: {sum(o.result == 'ok' for o in outcomes)}/{len(outcomes)}")
    return 1 if any(o.result != "ok" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
