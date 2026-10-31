"""HA Swarm node console: ONE program for every PC.

Runs inside Ubuntu (WSL) as root, as the systemd service 'ha-node-console', on port 9790.

  From this PC's own browser (http://127.0.0.1:9790):
    1. connect to NetBird (setup key), 2. choose who this PC becomes (mumbai = main, hyderabad, delhi),
    3. see this PC's zone; the main PC also gets the combined view of all zones and the deploy buttons.
  From other PCs over NetBird (100.64.0.0/10), managers answer a small API:
    /peer/info      which regions are taken, by whom      /peer/enroll   "may I join as <region>?" → join token
    /peer/enrolled  "I have joined"

A region can only be taken once: a manager refuses a region that already has a node (up or down) or that another
PC is joining right now, and the joining PC checks again after joining. Only Python's standard library is used.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the repository
UI = Path(__file__).resolve().parent / "ui.html"
PORT = int(os.environ.get("CONSOLE_PORT", "9790"))
WG_IF = os.environ.get("WG_IF", "wt0")
STATE_DIR = Path(os.environ.get("CONSOLE_STATE", "/var/lib/ha-node-console"))
REGIONS = ["mumbai", "hyderabad", "delhi"]
MAIN_REGION = "mumbai"                                  # the PC that creates the swarm, builds images, runs the dashboard
NETBIRD_NET = ipaddress.ip_network("100.64.0.0/10")
STACKS = ("hal", "infra", "hello")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HOST = socket.gethostname()


class Reject(Exception):
    """A request that is refused on purpose (region taken, no swarm yet, …): shown to the user as is."""


# ============================================================================ small helpers
def sh(cmd, timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def json_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def http_json(url: str, body: dict | None = None, timeout: float = 3) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": str(e)}
    except (OSError, ValueError) as e:
        return 0, {"error": str(e)}


def settings() -> dict:
    try:
        return json.loads((STATE_DIR / "settings.json").read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**kw) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    s = settings() | kw
    (STATE_DIR / "settings.json").write_text(json.dumps(s))


# ============================================================================ what this PC looks like
def netbird() -> dict:
    ip = ""
    rc, out = sh(["ip", "-4", "-o", "addr", "show", WG_IF], 5)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out) if rc == 0 else None
    if m:
        ip = m.group(1)
    try:
        mtu = int(Path(f"/sys/class/net/{WG_IF}/mtu").read_text())
    except (OSError, ValueError):
        mtu = None
    peers = []
    if shutil.which("netbird"):
        rc, out = sh(["netbird", "status", "--json"], 8)
        try:
            j = json.loads(out) if rc == 0 else {}
        except ValueError:
            j = {}
        for d in (j.get("peers") or {}).get("details") or []:
            lat = d.get("latency")
            if isinstance(lat, (int, float)):
                lat = f"{lat / 1e6:.0f} ms" if lat > 10000 else f"{lat:.0f} ms"
            peers.append({"name": (d.get("fqdn") or d.get("hostname") or "").split(".")[0],
                          "ip": str(d.get("netbirdIp") or d.get("ip") or "").split("/")[0],
                          "status": d.get("status", ""), "type": d.get("connectionType", ""), "latency": lat or ""})
    return {"installed": bool(shutil.which("netbird")), "connected": bool(ip), "ip": ip, "mtu": mtu, "peers": peers}


def docker_info() -> dict:
    rc, out = sh(["docker", "info", "--format", "{{json .}}"], 10)
    if rc != 0:
        return {"ok": False, "error": out[-300:]}
    try:
        j = json.loads(out)
    except ValueError:
        return {"ok": False, "error": "unreadable docker info"}
    return {"ok": True, "version": j.get("ServerVersion"), "desktop": "docker desktop" in str(j.get("OperatingSystem", "")).lower(),
            "cpus": j.get("NCPU"), "mem": j.get("MemTotal"), "swarm": j.get("Swarm") or {}}


def nodes() -> list[dict]:
    """Every node of the swarm (managers only can ask)."""
    rc, out = sh("docker node inspect $(docker node ls -q) --format '{{json .}}'", 10)
    res = []
    for n in json_lines(out) if rc == 0 else []:
        ms = n.get("ManagerStatus") or {}
        labels = (n.get("Spec") or {}).get("Labels") or {}
        res.append({"id": n["ID"], "hostname": n["Description"]["Hostname"], "addr": n["Status"].get("Addr", ""),
                    "state": n["Status"].get("State", ""), "availability": n["Spec"].get("Availability", ""),
                    "region": labels.get("region", ""), "main": labels.get("role") == "main",
                    "manager": bool(ms), "leader": bool(ms.get("Leader")), "reachability": ms.get("Reachability", "")})
    return res


def swarm() -> dict:
    d = docker_info()
    s = d.get("swarm") or {}
    out = {"docker": {k: d.get(k) for k in ("ok", "version", "desktop", "error", "cpus", "mem")},
           "state": s.get("LocalNodeState", "unknown"), "node_id": s.get("NodeID", ""), "manager": bool(s.get("ControlAvailable")),
           "region": "", "is_main": False, "leader": None, "nodes": []}
    if out["manager"]:
        out["nodes"] = nodes()
        for n in out["nodes"]:
            if n["id"] == out["node_id"]:
                out["region"], out["is_main"] = n["region"], n["main"]
            if n["leader"]:
                out["leader"] = {"hostname": n["hostname"], "region": n["region"]}
    return out


def regions_from(nodes_: list[dict]) -> dict:
    r = {k: None for k in REGIONS}
    for n in nodes_:
        if n["region"] in r:
            r[n["region"]] = {"hostname": n["hostname"], "state": n["state"], "addr": n["addr"], "leader": n["leader"]}
    return r


# ============================================================================ the peer API (served by managers)
RESERVED: dict[str, dict] = {}            # region → {"ip", "hostname", "until"}: a PC is joining as this region now
RES_LOCK = threading.Lock()


def peer_info() -> dict:
    sw = swarm()
    if not sw["manager"]:
        return {"manager": False, "hostname": HOST}
    now = time.time()
    with RES_LOCK:
        reserved = {k: v["hostname"] for k, v in RESERVED.items() if v["until"] > now}
    main = next((n["hostname"] for n in sw["nodes"] if n["main"]), None)
    return {"manager": True, "hostname": HOST, "is_main": sw["is_main"], "addr": netbird()["ip"],
            "regions": regions_from(sw["nodes"]), "reserved": reserved, "main": main, "leader": sw["leader"]}


def peer_enroll(body: dict, client: str) -> dict:
    region = str(body.get("region", ""))
    if region not in REGIONS:
        raise Reject(f"unknown region '{region}'")
    if region == MAIN_REGION:
        raise Reject(f"{MAIN_REGION} is the main PC: it is the one that created this swarm, it can't be joined")
    info = peer_info()
    if not info["manager"]:
        raise Reject("this PC is not a swarm manager")
    taken = info["regions"][region]
    if taken:
        raise Reject(f"{region} is already taken by {taken['hostname']} (node {taken['state']}). "
                     + ("Automatic cleanup is waiting for a manager majority; reconnect the other managers." if taken["state"] == "down" else ""))
    with RES_LOCK:
        r = RESERVED.get(region)
        if r and r["until"] > time.time() and r["ip"] != client:
            raise Reject(f"{r['hostname']} is joining as {region} right now")
        for k in [k for k, v in RESERVED.items() if v["ip"] == client]:   # one PC, one reservation: a new choice
            RESERVED.pop(k)                                               # replaces its earlier one
        RESERVED[region] = {"ip": client, "hostname": str(body.get("hostname", client)), "until": time.time() + 180}
    rc, token = sh(["docker", "swarm", "join-token", "manager", "-q"])
    if rc != 0:
        raise RuntimeError(token)
    return {"token": token.strip(), "manager": info["addr"]}


def peer_enrolled(body: dict, client: str) -> dict:
    with RES_LOCK:
        r = RESERVED.get(str(body.get("region", "")))
        if r and r["ip"] == client:
            RESERVED.pop(str(body["region"]), None)
    return {"ok": True}


# ============================================================================ finding the swarm from outside
DISCOVERY = {"t": 0.0, "managers": []}
DISC_LOCK = threading.Lock()


def discover(force: bool = False) -> list[dict]:
    """Ask every connected NetBird peer (and the IP typed in by hand, if any) whether it is a swarm manager."""
    with DISC_LOCK:
        if not force and time.time() - DISCOVERY["t"] < 5:
            return DISCOVERY["managers"]
    nb = netbird()
    # every peer, not only "Connected" ones: NetBird connects lazily and shows peers as "Idle" until traffic flows;
    # asking is what wakes the connection up
    ips = {p["ip"] for p in nb["peers"] if p["ip"]}
    if settings().get("main_ip"):
        ips.add(settings()["main_ip"])
    ips.discard(nb["ip"])

    def ask(ip):
        code, j = http_json(f"http://{ip}:{PORT}/peer/info", timeout=4)   # an idle peer needs a moment to connect
        return j | {"ip": ip} if code == 200 and j.get("manager") else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        found = [m for m in ex.map(ask, sorted(ips)) if m]
    found.sort(key=lambda m: not m.get("is_main"))          # the main PC first
    with DISC_LOCK:
        DISCOVERY.update(t=time.time(), managers=found)
    return found


# ============================================================================ background jobs (one at a time)
JOB = {"name": None, "running": False, "ok": None, "reject": None, "log": [], "started": 0}
JOB_LOCK = threading.Lock()


def log(line: str) -> None:
    JOB["log"].append(ANSI.sub("", line.rstrip()))
    del JOB["log"][:-500]


def run_job(name: str, fn, *args) -> None:
    with JOB_LOCK:
        if JOB["running"]:
            raise Reject(f"busy: '{JOB['name']}' is still running")
        JOB.update(name=name, running=True, ok=None, reject=None, log=[], started=time.time())

    def go():
        try:
            fn(*args)
            JOB["ok"] = True
            log("✔ done")
        except Reject as e:
            JOB.update(ok=False, reject=str(e))
            log(f"✖ REFUSED: {e}")
        except Exception as e:  # noqa: BLE001
            JOB["ok"] = False
            log(f"✖ FAILED: {e}")
        finally:
            JOB["running"] = False
            with DISC_LOCK:
                DISCOVERY["t"] = 0
    threading.Thread(target=go, daemon=True).start()


def stream(cmd: list[str], env: dict | None = None) -> None:
    log("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env=os.environ | (env or {}))
    for line in p.stdout:
        log(line)
    if p.wait() != 0:
        raise RuntimeError(f"{cmd[-1] if len(cmd) < 3 else cmd[1]} stopped with exit code {p.returncode}")


# ----------------------------------------------------------------------------- the jobs
# NetBird's default MTU (1280) is too big for some internet paths: between two of our PCs only ~1215 bytes got
# through, and the swarm join (a TLS handshake with full-size packets) timed out while small pings worked.
NB_MTU = int(os.environ.get("NB_MTU", "1200"))


def ensure_mtu() -> None:
    nb = netbird()
    if nb["mtu"] and nb["mtu"] <= NB_MTU:
        return
    log(f"NetBird MTU is {nb['mtu']}: setting {NB_MTU} so full-size packets fit every internet path")
    sh(["netbird", "down"], 30)
    rc, out = sh(["netbird", "up", "--mtu", str(NB_MTU)], 90)
    log(out)
    for _ in range(30):
        if netbird()["ip"] and (netbird()["mtu"] or 0) <= NB_MTU:
            log(f"NetBird MTU now {netbird()['mtu']}")
            return
        time.sleep(1)
    raise RuntimeError("NetBird did not come back with the new MTU: check 'netbird status'")


def job_netbird(key: str, mgmt: str) -> None:
    cmd = ["netbird", "up", "--setup-key", key, "--mtu", str(NB_MTU)] + (["--management-url", mgmt] if mgmt else [])
    log(f"$ netbird up --setup-key **** --mtu {NB_MTU}" + (f" --management-url {mgmt}" if mgmt else ""))
    rc, out = sh(cmd, 90)
    log(out)
    for _ in range(30):
        if netbird()["ip"]:
            log(f"connected: this PC is {netbird()['ip']} on NetBird")
            return
        time.sleep(1)
    raise RuntimeError("no NetBird address after 30 s: check the setup key")


def job_become(region: str) -> None:
    nb = netbird()
    if not nb["ip"]:
        raise Reject("connect to NetBird first")
    sw = swarm()
    if not sw["docker"]["ok"]:
        raise Reject("Docker is not running in Ubuntu")
    if sw["docker"]["desktop"]:
        raise Reject("this is Docker Desktop; the swarm needs Docker Engine inside Ubuntu")
    if sw["state"] == "active":
        if sw["region"] == region:
            log(f"this PC already is {region}")
            return
        raise Reject(f"this PC is already in a swarm{' as ' + sw['region'] if sw['region'] else ''}: leave it first")

    ensure_mtu()
    log("looking for an existing swarm on the NetBird network …")
    managers = discover(force=True)
    if region == MAIN_REGION:
        if managers:
            m = managers[0]
            raise Reject(f"a swarm already exists (main PC: {m.get('main') or m['hostname']}). "
                         f"{MAIN_REGION} is taken: choose a free region instead")
        log(f"no swarm found: this PC creates it and becomes {MAIN_REGION}, the main PC")
        stream(["bash", "scripts/03-swarm-init.sh", MAIN_REGION])
        rc, out = sh(["docker", "node", "update", "--label-add", "role=main", swarm()["node_id"]])
        if rc != 0:
            raise RuntimeError(out)
        log("labelled role=main")
        return

    if not managers:
        raise Reject(f"no swarm found on the NetBird network. The main PC must become {MAIN_REGION} first "
                     "(or type the main PC's NetBird IP below if it isn't found automatically)")
    m = managers[0]
    log(f"found manager {m['hostname']} ({m['ip']}); asking to join as {region}")
    code, j = http_json(f"http://{m['ip']}:{PORT}/peer/enroll", {"region": region, "hostname": HOST}, timeout=10)
    if code != 200:
        raise Reject(j.get("error", f"refused (HTTP {code})"))
    try:
        stream(["bash", "scripts/04-swarm-join.sh", region, j["token"], j["manager"]])
    except Exception:
        http_json(f"http://{m['ip']}:{PORT}/peer/enrolled", {"region": region}, timeout=5)   # give the region back
        raise

    # double check: a manager now, we can see every node; if another PC got the same region, step back
    clash = [n for n in nodes() if n["region"] == region and n["id"] != swarm()["node_id"]]
    if clash:
        log("another PC holds this region after all: leaving the swarm again")
        stream(["bash", "scripts/teardown.sh", "leave"])
        raise Reject(f"{region} was taken by {clash[0]['hostname']} at the same moment")
    http_json(f"http://{m['ip']}:{PORT}/peer/enrolled", {"region": region}, timeout=5)


def job_leave() -> None:
    d = docker_info()
    if not d.get("ok"):
        raise Reject("Docker is unavailable; start Docker and retry Leave")
    s = d["swarm"]
    if s.get("LocalNodeState") == "inactive":
        log("This PC has already left the swarm")
        return
    if s.get("ControlAvailable"):
        members = nodes()
        me = s.get("NodeID")
        if not members or not any(n["id"] == me for n in members):
            raise Reject("Cannot read swarm membership; restore the manager connection and retry Leave")
        if len(members) == 1:
            stream(["docker", "swarm", "leave", "--force"])
            return
        managers = [n for n in members if n["manager"]]
        ready = [n for n in managers if n["state"] == "ready" and n["reachability"] == "reachable"]
        if len(managers) < 2 or len(ready) < len(managers) // 2 + 1:
            raise Reject("Leaving needs another reachable manager and a manager majority. Reconnect the other PCs, then retry Leave")
        log("Demoting this manager before leaving …")
        rc, out = sh(["docker", "node", "demote", me], 20)
        log(out)
        # The local daemon can briefly still report manager after Raft commits the change.
        for _ in range(30):
            current = docker_info()
            if current.get("ok") and not current["swarm"].get("ControlAvailable"):
                break
            time.sleep(1)
        else:
            raise Reject("Manager demotion has not completed. Check connectivity and retry Leave. " + out[-300:])
    # Do not force a manager out when demotion or quorum checks failed.
    stream(["docker", "swarm", "leave"])
    log("Disconnected from the swarm. Remaining managers automatically remove this node and free its region. Data volumes are preserved.")


def cleanup_departed() -> None:
    """Only the elected leader removes down nodes; Docker enforces quorum on each mutation."""
    members = nodes()
    local = docker_info().get("swarm") or {}
    if not any(n["id"] == local.get("NodeID") and n["leader"] for n in members):
        return
    for n in members:
        if n["state"] != "down" or n["id"] == local.get("NodeID"):
            continue
        # Re-read before each operation: a disconnected PC may already have returned.
        fresh = next((x for x in nodes() if x["id"] == n["id"]), None)
        if not fresh or fresh["state"] != "down":
            continue
        if fresh["manager"]:
            rc, out = sh(["docker", "node", "demote", n["id"]], 10)
            if rc:
                continue  # no quorum: keep the region occupied and retry later
        rc, out = sh(["docker", "node", "rm", n["id"]], 10)
        if rc == 0:
            print(f"Removed departed node {n['hostname']}; region {n['region']} is free", flush=True)


def cleanup_loop() -> None:
    while True:
        try:
            cleanup_departed()
        except Exception as e:
            print(f"Departure cleanup will retry: {e}", flush=True)
        time.sleep(5)


def job_script(*args: str) -> None:
    stream(["bash", *args])


def job_release(region: str) -> None:
    for n in nodes():
        if n["region"] == region:
            if n["state"] == "ready":
                raise Reject(f"{region} ({n['hostname']}) is up: only a node that is down can be released")
            if n["manager"]:
                stream(["docker", "node", "demote", n["id"]])
            stream(["docker", "node", "rm", "--force", n["id"]])
            log(f"{region} is free again")
            return
    raise Reject(f"no node holds {region}")


ACTIONS = {                                    # main PC only: buttons in the combined view
    "images": ("Build and push images", job_script, "scripts/10-registry-and-images.sh"),
    "hello": ("Deploy the demo", job_script, "scripts/20-hello.sh"),
    "hello-rm": ("Remove the demo", job_script, "scripts/20-hello.sh", "rm"),
    "lab": ("Deploy the lab", job_script, "scripts/30-deploy-lab.sh"),
    "lab-rm": ("Remove the lab (data kept)", job_script, "scripts/teardown.sh", "lab"),
}


# ============================================================================ views
STATS = {"t": 0.0, "by_name": {}, "wanted": 0.0}


def stats_loop() -> None:
    """docker stats takes ~2 s, so it runs in the background, only while someone is looking at a zone."""
    while True:
        if time.time() - STATS["wanted"] < 20:
            rc, out = sh(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 25)
            if rc == 0:
                STATS["by_name"] = {s["Name"]: s for s in json_lines(out)}
                STATS["t"] = time.time()
        time.sleep(4)


def label_map(s: str) -> dict:
    return dict(kv.split("=", 1) for kv in s.split(",") if "=" in kv)


def patroni() -> list[dict]:
    """Database roles, asked from any PostgreSQL container running on this PC."""
    rc, out = sh(["docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name"], 5)
    for cid in out.split() if rc == 0 else []:
        rc2, name = sh(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.swarm.service.name"}}', cid], 5)
        if rc2 == 0 and re.fullmatch(r"hal_pg-\d", name.strip()):
            rc3, j = sh(["docker", "exec", cid, "curl", "-s", "-m", "2", "http://127.0.0.1:8008/cluster"], 6)
            try:
                return [{"name": m["name"], "role": m["role"], "state": m["state"], "lag": m.get("lag", 0)}
                        for m in json.loads(j)["members"]] if rc3 == 0 else []
            except (ValueError, KeyError):
                return []
    return []


def view_zone() -> dict:
    STATS["wanted"] = time.time()
    sw = swarm()
    nb = netbird()
    rc, out = sh(["docker", "ps", "-a", "--filter", "label=com.docker.stack.namespace", "--format", "{{json .}}"], 10)
    services = []
    for c in json_lines(out) if rc == 0 else []:
        lab = label_map(c.get("Labels", ""))
        svc = lab.get("com.docker.swarm.service.name", c["Names"])
        st = STATS["by_name"].get(c["Names"], {})
        services.append({"service": svc, "stack": lab.get("com.docker.stack.namespace", ""), "state": c.get("State", ""),
                         "status": c.get("Status", ""), "cpu": st.get("CPUPerc", ""), "mem": st.get("MemUsage", "").split(" / ")[0]})
    # one row per service: the running container if there is one, else the newest
    best: dict[str, dict] = {}
    for s in services:
        if s["service"] not in best or (s["state"] == "running" and best[s["service"]]["state"] != "running"):
            best[s["service"]] = s
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) // 1024
    except (OSError, ValueError):
        pass
    names = {s["service"] for s in best.values()}
    links = []
    if "hal_control" in names:
        links.append(["Lab dashboard", "http://127.0.0.1:9700"])
    if any(n.startswith("hal_haproxy") for n in names):
        links += [["Trader page (this zone's HAProxy)", "https://127.0.0.1:8443/live"], ["HAProxy statistics", "http://127.0.0.1:8404/stats"]]
    if "hello_web" in names or sw["state"] == "active":
        links.append(["Demo (any PC answers)", "http://127.0.0.1:8088"])
    return {"region": sw["region"], "is_main": sw["is_main"], "leader": sw["leader"], "manager": sw["manager"],
            "node": next((n for n in sw["nodes"] if n["id"] == sw["node_id"]), None),
            "netbird": nb, "docker": sw["docker"], "load": os.getloadavg(),
            "mem": {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable")},
            "services": sorted(best.values(), key=lambda s: s["service"]), "stats_age": round(time.time() - STATS["t"], 1),
            "patroni": patroni(), "links": links}


def view_combined() -> dict:
    sw = swarm()
    if not sw["manager"]:
        raise Reject("only a manager can see the whole swarm")
    tasks = []
    for st in STACKS:
        rc, out = sh(["docker", "stack", "ps", st, "--filter", "desired-state=running", "--format", "{{json .}}"], 15)
        for t in json_lines(out) if rc == 0 else []:
            tasks.append({"service": re.sub(r"\.\d+$", "", t["Name"]), "node": t["Node"], "state": t["CurrentState"],
                          "error": t.get("Error", "")})
    region_of = {n["hostname"]: n["region"] for n in sw["nodes"]}
    for t in tasks:
        t["region"] = region_of.get(t["node"], "")
    return {"nodes": sw["nodes"], "leader": sw["leader"], "regions": regions_from(sw["nodes"]), "tasks": tasks,
            "patroni": patroni(), "is_main": sw["is_main"],
            "actions": {k: v[0] for k, v in ACTIONS.items()}}


def view_state() -> dict:
    sw = swarm()
    nb = netbird()
    if sw["manager"]:
        cluster = {"source": "this PC", "regions": regions_from(sw["nodes"]), "main": next((n["hostname"] for n in sw["nodes"] if n["main"]), None)}
    elif nb["ip"] and sw["state"] != "active":
        ms = discover()
        cluster = ({"source": ms[0]["hostname"], "regions": ms[0]["regions"], "main": ms[0].get("main"),
                    "reserved": ms[0].get("reserved", {})} if ms else {"source": None, "regions": {k: None for k in REGIONS}})
    else:
        cluster = {"source": None, "regions": {k: None for k in REGIONS}}
    return {"hostname": HOST, "netbird": nb, "swarm": {k: v for k, v in sw.items() if k != "nodes"}, "cluster": cluster,
            "job": JOB, "settings": settings(), "main_region": MAIN_REGION, "regions": REGIONS}


# ============================================================================ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "ha-node-console"

    def log_message(self, *_):
        pass

    def client(self) -> str:
        return self.client_address[0].removeprefix("::ffff:")

    def is_local(self) -> bool:
        return self.client() in ("127.0.0.1", "::1")

    def is_peer(self) -> bool:
        try:
            return self.is_local() or ipaddress.ip_address(self.client()) in NETBIRD_NET
        except ValueError:
            return False

    def send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {}

    def route(self, method: str) -> None:
        path = self.path.split("?")[0]
        try:
            if path.startswith("/peer/"):                     # other PCs, over NetBird
                if not self.is_peer():
                    return self.send(403, {"error": "NetBird peers only"})
                if method == "GET" and path == "/peer/info":
                    return self.send(200, peer_info())
                if method == "POST" and path == "/peer/enroll":
                    return self.send(200, peer_enroll(self.body(), self.client()))
                if method == "POST" and path == "/peer/enrolled":
                    return self.send(200, peer_enrolled(self.body(), self.client()))
                return self.send(404, {"error": "not found"})

            if not self.is_local():                           # everything else: this PC's own browser only
                return self.send(403, {"error": "open the console on the PC itself: http://127.0.0.1:9790"})
            if method == "GET" and path in ("/", "/index.html"):
                return self.send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and path == "/api/health":
                return self.send(200, {"ok": True})
            if method == "GET" and path == "/api/state":
                return self.send(200, view_state())
            if method == "GET" and path == "/api/zone":
                return self.send(200, view_zone())
            if method == "GET" and path == "/api/combined":
                return self.send(200, view_combined())
            if method == "POST":
                b = self.body()
                if path == "/api/netbird":
                    if not str(b.get("setup_key", "")).strip():
                        raise Reject("paste the setup key from the NetBird dashboard")
                    run_job("Connect to NetBird", job_netbird, b["setup_key"].strip(), str(b.get("management_url", "")).strip())
                elif path == "/api/settings":
                    save_settings(main_ip=str(b.get("main_ip", "")).strip())
                    with DISC_LOCK:
                        DISCOVERY["t"] = 0
                elif path == "/api/become":
                    if b.get("region") not in REGIONS:
                        raise Reject("choose mumbai, hyderabad or delhi")
                    run_job(f"Become {b['region']}", job_become, b["region"])
                elif path == "/api/leave":
                    run_job("Leave the swarm", job_leave)
                elif path == "/api/action":
                    if not swarm()["is_main"]:
                        raise Reject("only the main PC can do this")
                    a = ACTIONS.get(b.get("name"))
                    if not a:
                        raise Reject("unknown action")
                    run_job(a[0], a[1], *a[2:])
                elif path == "/api/release":
                    if not swarm()["is_main"]:
                        raise Reject("only the main PC can release a region")
                    run_job(f"Release {b.get('region')}", job_release, str(b.get("region", "")))
                else:
                    return self.send(404, {"error": "not found"})
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "not found"})
        except Reject as e:
            return self.send(409, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": str(e)})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


def main() -> None:
    threading.Thread(target=stats_loop, daemon=True).start()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"node console on :{PORT} (open http://127.0.0.1:{PORT} on this PC)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["--leave"]:
        try:
            job_leave()
        except Exception as e:
            log(str(e))
            print("\n".join(JOB["log"]))
            sys.exit(1)
        print("\n".join(JOB["log"]))
    else:
        main()
