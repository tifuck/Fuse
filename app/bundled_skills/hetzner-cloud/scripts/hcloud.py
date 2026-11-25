#!/usr/bin/env python3
"""Hetzner Cloud helper for hourly servers. Python 3.8+, stdlib only.

Token lookup order: $HCLOUD_TOKEN, then $HCLOUD_TOKEN_FILE
(default ~/.config/hcloud-fuse/token, mode 0600). Create the file with
`hcloud.py login` from a real terminal. The token is never printed.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.hetzner.cloud/v1"
TOKEN_FILE = Path(os.environ.get("HCLOUD_TOKEN_FILE", str(Path.home() / ".config/hcloud-fuse/token")))
CONF_DIR = TOKEN_FILE.parent
AGENT_KEY = Path(os.environ.get("HCLOUD_SSH_KEY", str(Path.home() / ".ssh/hcloud-fuse_ed25519")))
MANAGED_KEY = "managed-by"
MANAGED_VAL = "fuse"
EXPIRES_KEY = "fuse-expires"
DEFAULT_TTL = "2h"
JOBS = "$HOME/.fuse-jobs"


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_token():
    tok = os.environ.get("HCLOUD_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text().strip()
    die(f"no API token. Run `{sys.argv[0]} login` in a terminal, or set HCLOUD_TOKEN.")


def api(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token or load_token()}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())["error"]
            msg = f"{err['code']}: {err['message']}"
        except Exception:
            msg = e.reason
        die(f"{method} {path} -> HTTP {e.code} {msg}")
    except urllib.error.URLError as e:
        die(f"{method} {path} -> {e.reason}")


def get_all(path, key):
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while page:
        r = api("GET", f"{path}{sep}page={page}&per_page=50")
        items += r[key]
        page = r.get("meta", {}).get("pagination", {}).get("next_page")
    return items


def wait(action, timeout=900):
    deadline = time.time() + timeout
    while action["status"] == "running":
        if time.time() > deadline:
            die(f"timed out waiting for action {action['id']} ({action['command']})")
        time.sleep(2)
        action = api("GET", f"/actions/{action['id']}")["action"]
    if action["status"] == "error":
        die(f"action {action['command']} failed: {action.get('error')}")
    return action


def find_server(ref):
    if ref.isdigit():
        return api("GET", f"/servers/{ref}")["server"]
    found = api("GET", "/servers?name=" + urllib.parse.quote(ref))["servers"]
    if not found:
        die(f"no server named {ref!r}")
    return found[0]


def wait_status(server_id, want, timeout=300):
    deadline = time.time() + timeout
    while True:
        s = api("GET", f"/servers/{server_id}")["server"]
        if s["status"] == want:
            return s
        if time.time() > deadline:
            die(f"server {server_id} stuck in {s['status']!r}, wanted {want!r}")
        time.sleep(2)


def parse_ttl(text):
    m = re.fullmatch(r"(\d+)([mhd])", text)
    if not m:
        die("--ttl must look like 90m, 4h or 2d")
    return int(m[1]) * {"m": 60, "h": 3600, "d": 86400}[m[2]]


def emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2))
        return
    rows = obj if isinstance(obj, list) else [obj]
    if not rows:
        print("(none)")
        return
    cols = list(rows[0])
    widths = [max(len(str(c)), *(len(str(r[c])) for r in rows)) for c in cols]
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


def expires_at(s, warn=True):
    """The server's expiry label in epoch seconds; None when unset or not a number."""
    exp = s.get("labels", {}).get(EXPIRES_KEY)
    if not exp:
        return None
    try:
        return int(exp)
    except ValueError:
        if warn:
            print(f"warning: server {s['name']} ({s['id']}) has a non-numeric {EXPIRES_KEY} label {exp!r}; "
                  "it is never reaped until 'extend' sets a new one", file=sys.stderr)
        return None


def server_row(s):
    exp = expires_at(s, warn=False)
    return {
        "id": s["id"],
        "name": s["name"],
        "status": s["status"],
        "type": s["server_type"]["name"],
        "location": s["location"]["name"],
        "ipv4": (s["public_net"].get("ipv4") or {}).get("ip") or "-",
        "ipv6": (s["public_net"].get("ipv6") or {}).get("ip") or "-",
        "created": s["created"],
        "expires": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(exp)) if exp is not None else "-",
    }


def ensure_agent_key():
    """Create the local agent keypair if missing and make sure Hetzner has it. Returns the key id."""
    if not AGENT_KEY.exists():
        AGENT_KEY.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(AGENT_KEY.parent, 0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                        f"hcloud-fuse@{socket.gethostname()}", "-f", str(AGENT_KEY)], check=True)
    pub = Path(str(AGENT_KEY) + ".pub").read_text().strip()
    digest = hashlib.md5(base64.b64decode(pub.split()[1])).hexdigest()
    fingerprint = ":".join(digest[i:i + 2] for i in range(0, 32, 2))
    for k in get_all("/ssh_keys", "ssh_keys"):
        if k["fingerprint"] == fingerprint:
            return k["id"]
    name = f"fuse-{socket.gethostname()}-{digest[:8]}"
    return api("POST", "/ssh_keys", {"name": name, "public_key": pub})["ssh_key"]["id"]


def known_hosts(server_id):
    return CONF_DIR / "known_hosts.d" / str(server_id)


def server_ip(s):
    ip = (s["public_net"].get("ipv4") or {}).get("ip")
    if ip:
        return ip, False
    return (s["public_net"].get("ipv6") or {}).get("ip", "").split("/")[0] + "1", True


def ssh_opts(s):
    kh = known_hosts(s["id"])
    cm = CONF_DIR / "cm"
    for d in (kh.parent, cm):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return ["-i", str(AGENT_KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={kh}",
            "-o", f"ControlPath={cm}/%C"]


def ensure_master(s):
    """Keep one detached shared connection per server so later commands start instantly."""
    host = f"root@{server_ip(s)[0]}"
    base = ["ssh"] + ssh_opts(s)
    if subprocess.run(base + ["-O", "check", host], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        return True
    # stdio must be /dev/null: a master holding our pipes makes callers hang
    return subprocess.run(base + ["-fN", "-o", "ControlMaster=yes", "-o", "ControlPersist=10m", host],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def ssh_argv(s):
    ensure_master(s)
    return ["ssh"] + ssh_opts(s) + ["-o", "ControlMaster=no", f"root@{server_ip(s)[0]}"]


def remote(s, script, timeout=None, capture=False):
    """Run a bash script on the server via stdin (no argv size or quoting limits)."""
    wrap = ["timeout", "-k", "5", str(timeout)] if timeout else []
    try:
        r = subprocess.run(ssh_argv(s) + wrap + ["bash", "-s"], input=script.encode(),
                           timeout=timeout + 15 if timeout else None,
                           stdout=subprocess.PIPE if capture else None)
        if timeout and r.returncode == 124:
            print(f"error: timed out after {timeout}s (use run --bg for long jobs)", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"error: timed out after {timeout}s (use run --bg for long jobs)", file=sys.stderr)
        return 124, b""
    return r.returncode, r.stdout or b""


def wait_ssh(s, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ensure_master(s):
            return True
        time.sleep(3)
    return False


def expiry_labels(labels, ttl):
    labels = dict(labels)
    if ttl == "none":
        labels.pop(EXPIRES_KEY, None)
    else:
        labels[EXPIRES_KEY] = str(int(time.time()) + parse_ttl(ttl))
    return labels


def reap(dry_run):
    now = time.time()
    servers = get_all(f"/servers?label_selector={MANAGED_KEY}%3D{MANAGED_VAL},{EXPIRES_KEY}", "servers")
    expired = [s for s in servers if (exp := expires_at(s)) is not None and exp <= now]
    if not dry_run:
        for s in expired:
            delete_server(s)
            print(f"reaped expired server {s['name']} ({s['id']})", file=sys.stderr)
    return expired


def money(x):
    return f"{float(x):.4f}"


def quote(type_name, location, ipv4):
    types = api("GET", "/server_types?name=" + urllib.parse.quote(type_name))["server_types"]
    if not types:
        die(f"unknown server type {type_name!r}")
    loc = next((l for l in types[0].get("locations", []) if l["name"] == location), None)
    if not loc or not loc["available"]:
        die(f"{type_name} is not available in {location!r} right now (see `prices --type {type_name} --all`)")
    p = api("GET", "/pricing")["pricing"]
    st = next((t for t in p["server_types"] if t["name"] == type_name), None)
    if not st:
        die(f"unknown server type {type_name!r}")
    sp = next((x for x in st["prices"] if x["location"] == location), None)
    if not sp:
        die(f"{type_name} has no price in {location!r} (not offered there)")
    hourly = float(sp["price_hourly"]["net"])
    monthly = float(sp["price_monthly"]["net"])
    if ipv4:
        ip = next((t for t in p["primary_ips"] if t["type"] == "ipv4"), None)
        ipp = next((x for x in (ip or {}).get("prices", []) if x["location"] == location), None)
        if ipp:
            hourly += float(ipp["price_hourly"]["net"])
            monthly += float(ipp["price_monthly"]["net"])
    return {
        "server_type": type_name,
        "location": location,
        "ipv4": ipv4,
        "currency": p["currency"],
        "vat_rate": p["vat_rate"],
        "hourly_net": round(hourly, 4),
        "monthly_cap_net": round(monthly, 4),
    }


# ---- commands -------------------------------------------------------------

def cmd_login(a):
    if not sys.stdin.isatty():
        die("login needs an interactive terminal. Run it yourself, outside the agent.")
    tok = getpass.getpass("Hetzner Cloud API token (input hidden): ").strip()
    if not tok:
        die("empty token")
    api("GET", "/locations", token=tok)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKEN_FILE.parent, 0o700)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    print(f"token valid, saved to {TOKEN_FILE} (0600)")


def cmd_check(a):
    api("GET", "/locations")
    print("token ok")


def cmd_locations(a):
    rows = [{"name": l["name"], "city": l["city"], "country": l["country"],
             "network_zone": l["network_zone"]} for l in get_all("/locations", "locations")]
    emit(rows, a.json)


def cmd_images(a):
    q = "/images?type=system&status=available"
    if a.arch:
        q += f"&architecture={a.arch}"
    rows = [{"name": i["name"], "arch": i["architecture"], "os": i["os_flavor"],
             "description": i["description"]} for i in get_all(q, "images")]
    emit(sorted(rows, key=lambda r: (r["arch"], r["name"])), a.json)


def cmd_prices(a):
    pricing = api("GET", "/pricing")["pricing"]
    types = get_all("/server_types", "server_types")
    rows = []
    for t in types:
        if a.type and t["name"] != a.type:
            continue
        if a.arch and t["architecture"] != a.arch:
            continue
        avail = {l["name"]: l for l in t.get("locations", [])}
        for p in t["prices"]:
            loc = p["location"]
            if a.location and loc != a.location:
                continue
            l = avail.get(loc)
            ok = bool(l and l["available"])
            if not ok and not a.all:
                continue
            rows.append({
                "type": t["name"], "arch": t["architecture"], "cpu": t["cpu_type"],
                "cores": t["cores"], "mem_gb": t["memory"], "disk_gb": t["disk"],
                "location": loc,
                "hourly_net": money(p["price_hourly"]["net"]),
                "monthly_net": money(p["price_monthly"]["net"]),
                "hourly_gross": money(p["price_hourly"]["gross"]),
                "available": ok,
                "deprecated": bool(l and l.get("deprecation")),
            })
    rows.sort(key=lambda r: (float(r["hourly_net"]), r["type"], r["location"]))
    if a.json:
        emit({"currency": pricing["currency"], "vat_rate": pricing["vat_rate"], "prices": rows}, True)
    else:
        print(f"currency {pricing['currency']}, VAT {pricing['vat_rate']}%, server only (IPv4 extra)")
        emit(rows, False)


def cmd_quote(a):
    emit(quote(a.type, a.location, not a.no_ipv4), a.json)


def cmd_create(a):
    q = quote(a.type, a.location, not a.no_ipv4)
    if not a.yes:
        emit(q, a.json)
        print("\nnot created: re-run with --yes after the user approves this price", file=sys.stderr)
        sys.exit(2)
    reap(dry_run=False)
    labels = expiry_labels({MANAGED_KEY: MANAGED_VAL}, a.ttl)
    for kv in a.label or []:
        k, _, v = kv.partition("=")
        labels[k] = v
    keys = [] if a.no_agent_key else [ensure_agent_key()]
    if a.ssh_key:
        all_keys = {k["name"]: k["id"] for k in get_all("/ssh_keys", "ssh_keys")}
        for k in a.ssh_key:
            if k.isdigit():
                keys.append(int(k))
            elif k in all_keys:
                keys.append(all_keys[k])
            else:
                die(f"no SSH key named {k!r} in this project (see `ssh-keys`)")
    body = {
        "name": a.name, "server_type": a.type, "location": a.location, "image": a.image,
        "labels": labels, "start_after_create": True,
        "public_net": {"enable_ipv4": not a.no_ipv4, "enable_ipv6": not a.no_ipv6},
    }
    if keys:
        body["ssh_keys"] = sorted(set(keys))
    if a.user_data:
        body["user_data"] = Path(a.user_data).read_text()
    r = api("POST", "/servers", body)
    wait(r["action"])
    for na in r.get("next_actions", []):
        wait(na)
    s = api("GET", f"/servers/{r['server']['id']}")["server"]
    out = {**server_row(s), "hourly_net": q["hourly_net"], "currency": q["currency"]}
    if r.get("root_password"):
        out["root_password_file"] = str(save_root_password(s["id"], r["root_password"]))
    if not a.no_agent_key:
        out["ssh_ready"] = wait_ssh(s)
        out["ssh"] = " ".join(shlex.quote(x) for x in ssh_argv(s))
        if a.setup and out["ssh_ready"]:
            print("running setup hook...", file=sys.stderr)
            rc, _ = remote(s, "exec 1>&2\ncloud-init status --wait >/dev/null 2>&1 || true\n" + Path(a.setup).read_text())
            out["setup_exit"] = rc
    emit(out, a.json)
    if out.get("setup_exit"):
        sys.exit(out["setup_exit"])


def cmd_ssh(a):
    s = find_server(a.server)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    sys.exit(subprocess.run(ssh_argv(s) + cmd).returncode)


def script_from(a):
    if a.script:
        return sys.stdin.read() if a.script == "-" else Path(a.script).read_text()
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        die("give a command after -- or --script FILE (- for stdin)")
    return " ".join(cmd) + "\n"


def cmd_run(a):
    s = find_server(a.server)
    script = script_from(a)
    if not a.bg:
        sys.exit(remote(s, script, timeout=a.timeout)[0])
    job = time.strftime("%Y%m%d-%H%M%S-", time.gmtime()) + secrets.token_hex(2)
    d = f"{JOBS}/{job}"
    launch = (f"set -e; mkdir -p {d}; cat > {d}/cmd <<'FUSE_EOF_{job}'\n{script}\nFUSE_EOF_{job}\n"
              f"cd \"$HOME\"; setsid nohup bash -c 'bash {d}/cmd > {d}/log 2>&1; echo $? > {d}/exit' "
              f">/dev/null 2>&1 < /dev/null & echo $! > {d}/pid; echo {job}")
    rc, outp = remote(s, launch, capture=True)
    if rc:
        die(f"failed to start job (exit {rc})")
    emit({"job": outp.decode().strip(), "server": s["name"],
          "next": f"logs {s['name']} {job} --follow | wait {s['name']} {job}"}, a.json)


def job_dir(a):
    if not re.fullmatch(r"[0-9A-Za-z-]+", a.job):
        die("bad job id")
    return f"{JOBS}/{a.job}"


def cmd_jobs(a):
    s = find_server(a.server)
    script = (f"for d in {JOBS}/*/; do [ -d \"$d\" ] || continue; j=$(basename \"$d\"); "
              "if [ -f \"$d/exit\" ]; then st=\"exit=$(cat \"$d/exit\")\"; "
              "elif kill -0 \"$(cat \"$d/pid\" 2>/dev/null)\" 2>/dev/null; then st=running; else st=lost; fi; "
              "printf '%s\\t%s\\t%s\\n' \"$j\" \"$st\" \"$(head -c 80 \"$d/cmd\" | head -n1)\"; done")
    rc, outp = remote(s, script, capture=True)
    rows = [dict(zip(("job", "status", "command"), l.split("\t", 2)))
            for l in outp.decode().splitlines() if l.count("\t") == 2]
    emit(rows, a.json)


def cmd_logs(a):
    s = find_server(a.server)
    d = job_dir(a)
    if a.follow:
        script = f"tail -n +1 -f --pid=\"$(cat {d}/pid)\" {d}/log"
    else:
        script = f"tail -n {a.tail} {d}/log"
    sys.exit(remote(s, script)[0])


def cmd_wait(a):
    s = find_server(a.server)
    d = job_dir(a)
    script = (f"[ -d {d} ] || {{ echo 'no such job' >&2; exit 127; }}; "
              f"end=$((SECONDS+{a.timeout})); while [ ! -f {d}/exit ]; do "
              f"kill -0 \"$(cat {d}/pid)\" 2>/dev/null || {{ sleep 1; [ -f {d}/exit ] && break; echo 'job lost' >&2; exit 125; }}; "
              f"[ $SECONDS -ge $end ] && {{ echo 'still running' >&2; exit 124; }}; sleep 1; done; exit $(cat {d}/exit)")
    rc, _ = remote(s, script)
    print(f"job {a.job} exit={rc}", file=sys.stderr)
    sys.exit(rc)


def cmd_kill(a):
    s = find_server(a.server)
    d = job_dir(a)
    sys.exit(remote(s, f"kill -TERM -- -\"$(cat {d}/pid)\" && echo 143 > {d}/exit && echo killed")[0])


def cmd_cp(a):
    def resolve(arg):
        name, sep, path = arg.partition(":")
        if not sep or "/" in name:
            return None, arg
        return find_server(name), path

    (s1, p1), (s2, p2) = resolve(a.src), resolve(a.dst)
    s = s1 or s2
    if not s or (s1 and s2):
        die("exactly one of SRC/DST must be SERVER:path")
    ip, v6 = server_ip(s)
    host = f"root@[{ip}]" if v6 else f"root@{ip}"
    src = f"{host}:{p1}" if s1 else p1
    dst = f"{host}:{p2}" if s2 else p2
    sys.exit(subprocess.run(["scp", "-q"] + (["-r"] if a.recursive else []) + ssh_opts(s) + [src, dst]).returncode)


def cmd_extend(a):
    s = find_server(a.server)
    labels = expiry_labels(s.get("labels", {}), a.ttl)
    s = api("PUT", f"/servers/{s['id']}", {"labels": labels})["server"]
    emit(server_row(s), a.json)


def cmd_list(a):
    reap(dry_run=False)
    q = "/servers" if a.all else f"/servers?label_selector={MANAGED_KEY}%3D{MANAGED_VAL}"
    emit([server_row(s) for s in get_all(q, "servers")], a.json)


def cmd_show(a):
    emit(server_row(find_server(a.server)), a.json)


def cmd_power(a):
    s = find_server(a.server)
    cmd = {"on": "poweron", "off": "poweroff"}.get(a.action, a.action)
    wait(api("POST", f"/servers/{s['id']}/actions/{cmd}")["action"])
    emit(server_row(api("GET", f"/servers/{s['id']}")["server"]), a.json)


def cmd_resize(a):
    s = find_server(a.server)
    q = quote(a.type, s["location"]["name"], bool(s["public_net"].get("ipv4")))
    if not a.yes:
        emit(q, a.json)
        print("\nnot resized: re-run with --yes after the user approves this price"
              + (" (disk upgrade is permanent: no downsizing later)" if a.upgrade_disk else ""),
              file=sys.stderr)
        sys.exit(2)
    was_running = s["status"] == "running"
    if s["status"] != "off":
        wait(api("POST", f"/servers/{s['id']}/actions/shutdown")["action"])
        try:
            wait_status(s["id"], "off", timeout=120)
        except SystemExit:
            wait(api("POST", f"/servers/{s['id']}/actions/poweroff")["action"])
            wait_status(s["id"], "off")
    wait(api("POST", f"/servers/{s['id']}/actions/change_type",
             {"server_type": a.type, "upgrade_disk": a.upgrade_disk})["action"])
    if was_running:
        wait(api("POST", f"/servers/{s['id']}/actions/poweron")["action"])
    emit(server_row(api("GET", f"/servers/{s['id']}")["server"]), a.json)


def root_password_file(server_id):
    return CONF_DIR / "root-passwords" / str(server_id)


def save_root_password(server_id, password):
    """Kept out of the output, which lands in the chat transcript."""
    path = root_password_file(server_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password + "\n")
    os.chmod(path, 0o600)
    return path


def delete_server(s):
    wait(api("DELETE", f"/servers/{s['id']}")["action"])
    known_hosts(s["id"]).unlink(missing_ok=True)
    root_password_file(s["id"]).unlink(missing_ok=True)


def cmd_delete(a):
    s = find_server(a.server)
    if not a.yes:
        emit(server_row(s), a.json)
        print("\nnot deleted: re-run with --yes after the user confirms. Data is lost.", file=sys.stderr)
        sys.exit(2)
    if s.get("protection", {}).get("delete"):
        die("server has delete protection on; remove it in the console or via change_protection first")
    delete_server(s)
    print(f"deleted {s['name']} ({s['id']})")


def cmd_reap(a):
    expired = reap(dry_run=not a.yes)
    emit([server_row(s) for s in expired], a.json)
    if expired and not a.yes:
        print("\ndry run: re-run with --yes to delete these", file=sys.stderr)


def cmd_ssh_keys(a):
    if a.add:
        if not a.name:
            die("--name is required with --add")
        k = api("POST", "/ssh_keys", {"name": a.name, "public_key": Path(a.add).expanduser().read_text().strip()})
        emit({"id": k["ssh_key"]["id"], "name": k["ssh_key"]["name"],
              "fingerprint": k["ssh_key"]["fingerprint"]}, a.json)
        return
    emit([{"id": k["id"], "name": k["name"], "fingerprint": k["fingerprint"]}
          for k in get_all("/ssh_keys", "ssh_keys")], a.json)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="store API token (interactive, run by the user)").set_defaults(f=cmd_login)
    sub.add_parser("check", help="verify the stored token works").set_defaults(f=cmd_check)
    sub.add_parser("locations").set_defaults(f=cmd_locations)

    p = sub.add_parser("images")
    p.add_argument("--arch", choices=["x86", "arm"])
    p.set_defaults(f=cmd_images)

    p = sub.add_parser("prices", help="live per-location prices for server types")
    p.add_argument("--type")
    p.add_argument("--location")
    p.add_argument("--arch", choices=["x86", "arm"])
    p.add_argument("--all", action="store_true", help="include unavailable type/location pairs")
    p.set_defaults(f=cmd_prices)

    p = sub.add_parser("quote", help="exact hourly cost of one server, incl. IPv4")
    p.add_argument("--type", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--no-ipv4", action="store_true")
    p.set_defaults(f=cmd_quote)

    p = sub.add_parser("create")
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--image", default="ubuntu-24.04")
    p.add_argument("--ssh-key", action="append", help="name or id; repeatable")
    p.add_argument("--ttl", default=DEFAULT_TTL, help=f"auto-delete after, e.g. 90m 4h 2d, or none (default {DEFAULT_TTL})")
    p.add_argument("--setup", metavar="FILE", help="bash script to run once SSH and cloud-init are ready")
    p.add_argument("--label", action="append", help="key=value; repeatable")
    p.add_argument("--user-data", help="cloud-init file")
    p.add_argument("--no-ipv4", action="store_true")
    p.add_argument("--no-ipv6", action="store_true")
    p.add_argument("--no-agent-key", action="store_true", help="do not inject this machine's agent key")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(f=cmd_create)

    p = sub.add_parser("ssh", help="raw ssh with the agent key (empty: interactive shell)")
    p.add_argument("server")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(f=cmd_ssh)

    p = sub.add_parser("run", help="run a command or script on a server (foreground or --bg)")
    p.add_argument("server")
    p.add_argument("--script", metavar="FILE", help="bash script file, or - for stdin")
    p.add_argument("--bg", action="store_true", help="detach; survives disconnects; prints a job id")
    p.add_argument("--timeout", type=int, help="foreground seconds before giving up (exit 124)")
    p.add_argument("command", nargs="*", help="after --")
    p.set_defaults(f=cmd_run)

    p = sub.add_parser("jobs", help="list background jobs on a server")
    p.add_argument("server")
    p.set_defaults(f=cmd_jobs)

    p = sub.add_parser("logs", help="show a background job's output")
    p.add_argument("server")
    p.add_argument("job")
    p.add_argument("--follow", action="store_true", help="stream until the job ends")
    p.add_argument("--tail", type=int, default=100)
    p.set_defaults(f=cmd_logs)

    p = sub.add_parser("wait", help="block until a job ends; exits with its exit code")
    p.add_argument("server")
    p.add_argument("job")
    p.add_argument("--timeout", type=int, default=3600)
    p.set_defaults(f=cmd_wait)

    p = sub.add_parser("kill", help="stop a background job and its children")
    p.add_argument("server")
    p.add_argument("job")
    p.set_defaults(f=cmd_kill)

    p = sub.add_parser("cp", help="copy files; one side is SERVER:path")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(f=cmd_cp)

    p = sub.add_parser("extend", help="reset a server's TTL from now (or --ttl none)")
    p.add_argument("server")
    p.add_argument("--ttl", required=True)
    p.set_defaults(f=cmd_extend)

    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true", help="include servers not created by this tool")
    p.set_defaults(f=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("server", help="id or name")
    p.set_defaults(f=cmd_show)

    p = sub.add_parser("power")
    p.add_argument("server")
    p.add_argument("action", choices=["on", "off", "shutdown", "reboot", "reset"])
    p.set_defaults(f=cmd_power)

    p = sub.add_parser("resize")
    p.add_argument("server")
    p.add_argument("--type", required=True)
    p.add_argument("--upgrade-disk", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(f=cmd_resize)

    p = sub.add_parser("delete")
    p.add_argument("server")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(f=cmd_delete)

    p = sub.add_parser("reap", help="delete servers past their --ttl")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(f=cmd_reap)

    p = sub.add_parser("ssh-keys")
    p.add_argument("--add", metavar="PUBKEY_FILE")
    p.add_argument("--name")
    p.set_defaults(f=cmd_ssh_keys)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
