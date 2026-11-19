# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""compose.yaml: the four services and exactly what each one may touch.

The capability split is the point of the file, so it is pinned whole. web and store are the two services a browser
reaches: neither holds a feed, AI or alert credential or an ETL write mount, and web sees no secret at all. The two
headless workers alone read the config, the bank files and those credentials, and write the stores and the data. Every
mount is a directory beside the compose file (the install root), never a single file (a file mount pins its inode, so
an atomic rewrite stays unseen until a restart), and never one Docker makes when it is missing (it would be root's).
Parses the tree's compose file with PyYAML, which resolves the file's merge keys as compose does."""
from __future__ import annotations

import re

import refresh_runner  # deploy/ is on sys.path (tests/conftest.py)
import yaml
from conftest import ROOT

COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
SERVICES = ("web", "store", "scheduler", "refresher")
WORKERS = ("scheduler", "refresher")
HOME = "/ff"  # the install root inside the containers, which FF_HOME names

# (directory beside the compose file, place in the container, mode) for each service: the capability split, whole.
# state/ is split by who may write: the family documents (the store's; the workers read them), the sessions (the
# store's), the mailbox (both sides), the engine's own (the workers'), Caddy's (web's).
WORKER_MOUNTS = {
    ("./config", f"{HOME}/config", "ro"),
    ("./secrets/feed", f"{HOME}/secrets/feed", "ro"),
    ("./secrets/ai", f"{HOME}/secrets/ai", "ro"),
    ("./secrets/alerts", f"{HOME}/secrets/alerts", "ro"),
    ("./imports", f"{HOME}/imports", "ro"),
    ("./store", f"{HOME}/store", "rw"),
    ("./data", f"{HOME}/data", "rw"),
    ("./state/family", f"{HOME}/state/family", "ro"),
    ("./state/engine", f"{HOME}/state/engine", "rw"),
    ("./state/refresh", f"{HOME}/state/refresh", "rw"),
}
MOUNTS = {
    "web": {("./data/exports", "/srv/exports", "ro"), ("./state/caddy", "/state/caddy", "rw")},
    "store": {("./secrets/web", f"{HOME}/secrets/web", "ro"), ("./state/family", f"{HOME}/state/family", "rw"),
              ("./state/auth", f"{HOME}/state/auth", "rw"), ("./state/refresh", f"{HOME}/state/refresh", "rw")},
    "scheduler": WORKER_MOUNTS,
    "refresher": WORKER_MOUNTS,
}
# the one folder both sides of the split write: the refresh mailbox, where every access is made at the name itself
SHARED_WRITABLE = {"./state/refresh"}
# every key a service may have; anything else (privileged, cap_add, network_mode, devices, env_file, …) fails
SERVICE_KEYS = {"image", "build", "command", "environment", "volumes", "read_only", "tmpfs", "ports", "depends_on",
                "healthcheck", "user", "restart", "cap_drop", "security_opt", "init", "pids_limit", "networks"}
PIDS_LIMIT = {"web": 256, "store": 256, "scheduler": 512, "refresher": 512}
# the install root's directories: every mount's source is one of them
DIRECTORIES = {source for mounts in MOUNTS.values() for source, _, _ in mounts}
ENVIRONMENT = {
    "web": {"SITE_ADDRESS", "FF_HOSTNAMES", "TRUSTED_PROXIES", "FF_STORE_UPSTREAM", "XDG_DATA_HOME", "XDG_CONFIG_HOME"},
    "store": {"FF_HOME", "FF_HOSTNAMES", "SITE_ADDRESS"},
    "scheduler": {"FF_HOME"},
    "refresher": {"FF_HOME"},
}
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}")


def doc() -> dict:
    return yaml.safe_load(COMPOSE.read_text("utf-8"))


def services() -> dict:
    return doc()["services"]


def mount_list(svc: dict) -> list[tuple[str, str, str]]:
    found = []
    for entry in svc.get("volumes") or []:
        assert isinstance(entry, dict) and entry.get("type") == "bind", (
            f"{entry!r}: every mount is written as a bind in the long form, the one form that never creates its source")
        found.append((entry["source"], entry["target"], "ro" if entry.get("read_only") else "rw"))
    return found


def mounts(svc: dict) -> set[tuple[str, str, str]]:
    return set(mount_list(svc))


def resolve(value: str, env: dict[str, str] | None = None) -> str:
    """`value` as compose interpolates it with `env` as the only variables set: `${V:-d}` is d when V is unset or
    empty, `${V-d}` when unset, `${V}` empty when unset."""
    env = env or {}

    def one(m: re.Match) -> str:
        name, op, default = m.group(1), m.group(2), m.group(3) or ""
        if op == ":-":
            return env.get(name) or default
        if op == "-":
            return env.get(name, default)
        assert op is None, f"{m.group(0)}: only defaults are used in this file"
        return env.get(name, "")

    return VARIABLE.sub(one, value)


def strings(node) -> list[str]:
    """Every string value in a parsed YAML tree."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]
    return []


def read_variables() -> dict[str, set[str]]:
    """Each variable the file reads, with the defaults it is given."""
    found: dict[str, set[str]] = {}
    for s in strings(doc()):
        for m in VARIABLE.finditer(s):
            found.setdefault(m.group(1), set()).add(m.group(3) or "")
    return found


def dockerfile_stages(name: str) -> dict[str, str]:
    """A Dockerfile's stages by name, each its text from its FROM line."""
    text = (ROOT / "deploy" / name).read_text("utf-8")
    parts = re.split(r"^(?=FROM )", text, flags=re.MULTILINE)[1:]
    return {re.search(r" AS (\S+)$", p.splitlines()[0]).group(1): p for p in parts}


def env_example() -> dict[str, str]:
    lines = ENV_EXAMPLE.read_text("utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def test_the_file_holds_the_four_services_and_names_no_project():
    """No project name of its own: compose names the project after the install directory, so two installs on one
    machine (a demo beside a household's own) never take over each other's containers."""
    top = doc()
    assert sorted(top["services"]) == sorted(SERVICES)
    assert [k for k in top if k not in ("services", "networks") and not k.startswith("x-")] == []


def test_the_store_has_no_way_out_and_the_workers_share_no_network_with_web_or_the_store():
    """web on `front` (its published ports) and `back`; the store on `back` alone, which has no route out; the workers
    on the default network (the bank feed's fetch reaches out), sharing none with the other two: the two sides talk
    through files only."""
    svcs = services()
    assert doc()["networks"] == {"front": {}, "back": {"internal": True}}
    assert svcs["web"]["networks"] == ["front", "back"]
    assert svcs["store"]["networks"] == ["back"]
    for name in WORKERS:
        assert "networks" not in svcs[name], name


def test_each_service_has_its_process_limit():
    for name, svc in services().items():
        assert svc["pids_limit"] == PIDS_LIMIT[name], name


def test_each_service_mounts_exactly_its_own_directories():
    for name, svc in services().items():
        listed = mount_list(svc)
        assert len(listed) == len(set(listed)), f"{name}: a mount listed twice"
        assert set(listed) == MOUNTS[name], name


def test_web_sees_no_secret_and_of_the_state_only_caddys_own():
    """web serves the browser: it reads the exports and keeps Caddy's own data, and nothing else."""
    web = mounts(services()["web"])
    sources = {source for source, _, _ in web}
    for word in ("secrets", "config", "imports", "store"):
        assert not any(word in s for s in sources), word
    assert {s for s in sources if s.startswith("./state")} == {"./state/caddy"}
    assert {(s, mode) for s, _, mode in web if s.startswith("./data")} == {("./data/exports", "ro")}
    assert {s for s, _, mode in web if mode == "rw"} == {"./state/caddy"}


def test_the_store_holds_no_feed_ai_or_alert_credential_and_no_etl_write_mount():
    """The store answers the browser: the passcodes read-only and, to write, its own state (the family documents, the
    sessions) and the refresh mailbox; never a worker's credential, the config, the bank files, the stores, the data,
    the engine's own state or Caddy's."""
    store = mounts(services()["store"])
    sources = {source for source, _, _ in store}
    for forbidden in ("./secrets/feed", "./secrets/ai", "./secrets/alerts", "./config", "./imports", "./store",
                      "./data", "./state/engine", "./state/caddy"):
        assert not any(s == forbidden or s.startswith(forbidden + "/") for s in sources), forbidden
    assert {s for s in sources if s.startswith("./secrets")} == {"./secrets/web"}
    assert {s for s, _, mode in store if mode == "rw"} == {"./state/family", "./state/auth", "./state/refresh"}
    assert ("./secrets/web", f"{HOME}/secrets/web", "ro") in store


def test_no_service_mounts_the_whole_state_or_a_folder_holding_what_another_writes():
    """A folder mounted whole would hand its holder every subfolder: no service mounts ./state itself, and none mounts
    a parent of a folder another side writes (the sides: web, the store, the two workers, which run the same refresh
    and share their folders). Two sides write the same folder only at the mailbox; the workers read the family
    documents the store writes, read-only."""
    svcs = services()
    side = {"web": "web", "store": "store", "scheduler": "workers", "refresher": "workers"}
    for name, svc in svcs.items():
        assert "./state" not in {s for s, _, _ in mounts(svc)}, name
    for a, svc_a in svcs.items():
        for b, svc_b in svcs.items():
            if side[a] == side[b]:
                continue
            written_by_b = {s for s, _, mode in mounts(svc_b) if mode == "rw"}
            for source, _, mode in mounts(svc_a):
                assert not any(w.startswith(source + "/") for w in written_by_b), (a, source, b)
                if mode == "rw" and source in written_by_b:
                    assert source in SHARED_WRITABLE, (a, b, source)


def test_the_workers_never_see_the_passcodes_and_read_every_credential_read_only():
    for name in WORKERS:
        worker = mounts(services()[name])
        assert not any(s == "./secrets" or s.startswith("./secrets/web") for s, _, _ in worker), name
        assert not any(s in ("./state/auth", "./state/caddy") for s, _, _ in worker), name
        assert {mode for s, _, mode in worker if s.startswith("./secrets")} == {"ro"}, name
        assert {mode for s, _, mode in worker if s in ("./config", "./imports", "./state/family")} == {"ro"}, name


def test_every_mount_is_a_directory_beside_the_compose_file_that_docker_never_makes():
    """A source is one of the install root's directories (a plain path under it, no file extension, no `..`), its
    place in the container absolute, and nothing asks Docker to create a missing one: a missing directory stops the
    service from starting instead of becoming an empty one owned by root."""
    for name, svc in services().items():
        for entry in svc.get("volumes") or []:
            assert re.fullmatch(r"\./[a-z]+(/[a-z]+)?", entry["source"]), (name, entry["source"])
            assert entry["source"] in DIRECTORIES, (name, entry["source"])
            assert entry["target"].startswith("/") and ".." not in entry["target"], (name, entry["target"])
            assert set(entry) <= {"type", "source", "target", "read_only"}, (name, entry)
    assert "volumes" not in doc(), "no named volume: everything the stack keeps is a directory of the install"


def test_only_web_publishes_ports_and_on_this_machine_alone_by_default():
    """The store's only caller is the proxy, so it publishes nothing; web publishes plain HTTP and, for a domain, HTTPS,
    both on the loopback address unless FF_BIND names another."""
    svcs = services()
    assert [n for n, s in svcs.items() if "ports" in s] == ["web"]
    assert not any("expose" in s for s in svcs.values())
    http, https = svcs["web"]["ports"]
    assert (resolve(http), resolve(https)) == ("127.0.0.1:8080:8080", "127.0.0.1:8443:8443")
    domain = {"FF_BIND": "0.0.0.0", "FF_PORT": "80", "FF_PORT_TLS": "443"}
    assert (resolve(http, domain), resolve(https, domain)) == ("0.0.0.0:80:8080", "0.0.0.0:443:8443")
    assert resolve(http, {"FF_BIND": "192.0.2.10", "FF_PORT": "18205"}) == "192.0.2.10:18205:8080"
    [expose] = re.findall(r"^EXPOSE ([\d ]+)$", dockerfile_stages("Dockerfile.web")["web"], re.MULTILINE)
    assert expose.split() == [p.rsplit(":", 1)[1] for p in (http, https)], \
        "the container ports are the ones the web image listens on"
    caddy = (ROOT / "deploy" / "Caddyfile").read_text("utf-8")
    assert re.search(r"^\thttp_port 8080$", caddy, re.MULTILINE) and re.search(r"^\thttps_port 8443$", caddy, re.MULTILINE)


def test_each_service_is_given_only_the_variables_it_needs():
    svcs = services()
    for name, svc in svcs.items():
        assert isinstance(svc["environment"], dict), name
        assert set(svc["environment"]) == ENVIRONMENT[name], name
    for name in ("store", *WORKERS):
        assert svcs[name]["environment"]["FF_HOME"] == HOME, name
    web, store = svcs["web"]["environment"], svcs["store"]["environment"]
    assert web["FF_HOSTNAMES"] == store["FF_HOSTNAMES"], "the store applies the proxy's host rule to the same names"
    assert web["SITE_ADDRESS"] == store["SITE_ADDRESS"], "and to the site's own name"
    assert resolve(web["FF_HOSTNAMES"]) == "" and resolve(web["TRUSTED_PROXIES"]) == ""
    command = svcs["store"]["command"]
    assert web["FF_STORE_UPSTREAM"] == f"store:{command[command.index('--port') + 1]}"
    for env in ({}, {"SITE_ADDRESS": ""}):
        assert resolve(web["SITE_ADDRESS"], env) == ":8080", "an empty SITE_ADDRESS would leave Caddy no address"
    image_env = dict(re.findall(r"\b(XDG_[A-Z_]+)=(\S+)", dockerfile_stages("Dockerfile.web")["web"]))
    assert {k: web[k] for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME")} == image_env
    caddy_mount = {target for _, target, mode in mounts(svcs["web"]) if mode == "rw"}
    assert all(any(v.startswith(t + "/") for t in caddy_mount) for v in image_env.values())


def test_no_secret_token_passcode_or_zone_is_ever_an_environment_variable():
    """Credentials are files under secrets/, each read by the one service that needs it; the household's zone is
    household.yaml's, so no TZ decides anything."""
    text = COMPOSE.read_text("utf-8")
    secretish = re.compile(r"TOKEN|PASSCODE|PASSWORD|SECRET|KEY|TELEGRAM|CHAT|ALERT|\bTZ\b")
    for name, svc in services().items():
        assert not [k for k in svc["environment"] if secretish.search(k)], name
    assert not [v for v in read_variables() if secretish.search(v)]
    assert not re.search(r"\bTZ\b", text)


def test_nothing_widens_a_service_beyond_its_mounts():
    """Every service key is one of SERVICE_KEYS, an allow list: a key that could widen a service (privileged, cap_add,
    network_mode, devices, env_file, extends, …) is no key this file may hold."""
    text = COMPOSE.read_text("utf-8")
    for name, svc in services().items():
        assert set(svc) <= SERVICE_KEYS, (name, set(svc) - SERVICE_KEYS)
        assert svc["user"] == "${FF_UID:-1000}:${FF_GID:-1000}", name
        assert svc["cap_drop"] == ["ALL"], name
        assert svc["security_opt"] == ["no-new-privileges:true"], name
        assert svc["restart"] == "unless-stopped", name
    assert "include" not in doc()
    assert "docker.sock" not in text and "/var/run/docker" not in text


def test_the_two_browser_facing_services_run_on_a_read_only_root_and_the_workers_reap_their_children():
    """web and store write only to their mounts and a scratch /tmp; the workers start refresh.sh and what it runs, so
    an init process reaps whatever a run leaves behind."""
    svcs = services()
    for name in ("web", "store"):
        assert svcs[name]["read_only"] is True and svcs[name]["tmpfs"] == ["/tmp"], name
        assert "init" not in svcs[name], name
    for name in WORKERS:
        assert svcs[name]["init"] is True, name


def test_every_service_reports_its_health_and_web_waits_for_a_healthy_store():
    svcs = services()
    for name, svc in svcs.items():
        check = svc["healthcheck"]
        assert check["test"][0] == "CMD", name
        assert {"interval", "timeout", "retries", "start_period"} <= set(check), name
    assert svcs["web"]["depends_on"] == {"store": {"condition": "service_healthy"}}
    assert svcs["web"]["healthcheck"]["test"][1:] == ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
    command = svcs["store"]["command"]
    store_check = " ".join(svcs["store"]["healthcheck"]["test"][1:])
    assert f"http://127.0.0.1:{command[command.index('--port') + 1]}/healthz" in store_check
    scheduler_check = svcs["scheduler"]["healthcheck"]["test"][-1]
    assert '"/proc"' in scheduler_check and '== "supercronic"' in scheduler_check
    refresher_check = svcs["refresher"]["healthcheck"]["test"][-1]
    assert f'os.environ["FF_HOME"], "state", "engine", "{refresh_runner.HEARTBEAT_FILE}"' in refresher_check
    [limit] = (int(n) for n in re.findall(r"\.st_mtime < (\d+)", refresher_check))
    # one pass holds the loop for a run, its kill's grace (30 + 10 s) and a poll, after a beat up to HEARTBEAT_S old
    longest = refresh_runner.RUN_TIMEOUT_S + 40 + refresh_runner.HEARTBEAT_S + refresh_runner.POLL_S
    assert longest <= limit <= 2 * refresh_runner.RUN_TIMEOUT_S


def test_the_images_default_to_the_two_runtime_images_each_built_by_one_service_from_a_checkout():
    svcs = services()
    assert resolve(svcs["web"]["image"]) == "ghcr.io/good-work-dojo/family-financials-web:1.0.0"
    assert resolve(svcs["web"]["image"], {"FF_IMAGE_WEB": "registry.example.net/ff/web:other"}) == \
        "registry.example.net/ff/web:other"
    etl = {svcs[n]["image"] for n in ("store", *WORKERS)}
    assert len(etl) == 1 and resolve(etl.pop()) == "ghcr.io/good-work-dojo/family-financials-etl:1.0.0"
    assert svcs["web"]["build"] == {"context": ".", "dockerfile": "deploy/Dockerfile.web", "target": "web"}
    assert svcs["scheduler"]["build"] == {"context": ".", "dockerfile": "deploy/Dockerfile.etl", "target": "etl"}
    assert [n for n, s in svcs.items() if "build" in s] == ["web", "scheduler"]
    assert "web" in dockerfile_stages("Dockerfile.web") and "etl" in dockerfile_stages("Dockerfile.etl")


def test_the_commands_run_the_runtime_images_own_code():
    """The runtime image keeps its code at /opt/ff (its WORKDIR) and nothing under /app; each command names a file
    the image copies there, and the store always asks for a session."""
    etl = dockerfile_stages("Dockerfile.etl")
    assert re.findall(r"^WORKDIR (\S+)$", etl["etl"], re.MULTILINE) == ["/opt/ff"]
    assert "/app" not in etl["etl"], "the runtime image has no second name for its code"
    [copied] = re.findall(r"^COPY ((?:[^\n]*\\\n)*[^\n]*) /opt/ff/deploy/$", etl["py-build"], re.MULTILINE)
    shipped = {p.removeprefix("deploy/") for p in copied.replace("\\\n", " ").split()}
    svcs = services()
    assert svcs["scheduler"]["command"] == ["python", "/opt/ff/deploy/make_crontab.py", "--run"]
    assert svcs["refresher"]["command"] == ["python", "/opt/ff/deploy/refresh_runner.py"]
    store = svcs["store"]["command"]
    assert store == ["python", "/opt/ff/deploy/scenario_store.py", "--host", "0.0.0.0", "--port", "8123",
                     "--auth", "required"]
    for name, svc in svcs.items():
        for arg in svc.get("command") or []:
            if arg.startswith("/opt/ff/deploy/"):
                assert arg.removeprefix("/opt/ff/deploy/") in shipped, (name, arg)
    # what the scheduler and refresh.sh run, beside them in the image
    assert {"refresh.sh", "make_crontab.py", "feed_check.py", "alert.py", "refresh_state.py", "safe_files.py"} <= shipped


def test_the_schedule_is_the_households_and_no_file_states_one():
    """The scheduler writes its crontab from household.yaml as it starts (deploy/make_crontab.py) and leaves the
    schedule in the mailbox for the store: no crontab ships, no service is given a schedule or a zone, and nothing in
    the compose file states an hour of it."""
    assert not (ROOT / "deploy" / "crontab").exists()
    assert not re.search(r"deploy/crontab\b", (ROOT / "deploy" / "Dockerfile.etl").read_text("utf-8"))
    for name, svc in services().items():
        assert "--schedule" not in (svc.get("command") or []), name
    assert not re.search(r"\b(?:01|09|17):00\b", COMPOSE.read_text("utf-8"))


def test_the_example_environment_lists_exactly_the_settings_the_file_reads():
    """.env.example is the list of the file's settings, each left empty (the file's default) or set to that default."""
    read, example = read_variables(), env_example()
    assert set(example) == set(read)
    for name, value in example.items():
        assert value == "" or {value} == read[name], (name, value, read[name])
