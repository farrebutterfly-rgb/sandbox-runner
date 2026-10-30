"""Control checks. Each one runs real code in the sandbox and compares against what the host
observes from the outside. Used by `make verify` (evidence) and by the unit tests.

A check returns {"control", "passed", "detail"}. On the none backend nothing runs and every
control is reported as "not verified", never as passed.
"""
from __future__ import annotations

import csv
import json
import os
import socket
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

from .backends import Backend
from .limits import Limits
from .runner import run_code

DATA = Path(__file__).resolve().parent.parent / "data" / "orders.csv"
DATASET = "orders-v1"

REVENUE_CODE = """
import csv, json
totals = {}
with open("orders.csv") as f:
    for row in csv.DictReader(f):
        cents = int(row["units"]) * round(float(row["unit_price"]) * 100)
        totals[row["region"]] = totals.get(row["region"], 0) + cents
print(json.dumps(dict(sorted(totals.items()))))
"""


def orders_text() -> str:
    return DATA.read_text()


def host_revenue_cents() -> dict[str, int]:
    """The same number computed on the host with independent code, for comparison."""
    out: dict[str, int] = defaultdict(int)
    with DATA.open() as f:
        for row in csv.DictReader(f):
            out[row["region"]] += int(row["units"]) * round(float(row["unit_price"]) * 100)
    return dict(sorted(out.items()))


def host_pids_with(marker: str) -> list[int]:
    """Host-side scan of /proc for processes whose cmdline contains marker."""
    found = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            if marker.encode() in Path(f"/proc/{p}/cmdline").read_bytes().replace(b"\0", b" "):
                found.append(int(p))
        except OSError:
            pass
    return found


class TcpTarget:
    """A controlled test destination: TCP server on 127.0.0.1 that counts accepted connections."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            c.sendall(b"hello from the host\n")
            c.close()

    def close(self):
        self.sock.close()


def _r(control: str, passed: bool, detail) -> dict:
    return {"control": control, "passed": bool(passed), "detail": detail}


def deterministic_result(b: Backend) -> dict:
    runs = [run_code(REVENUE_CODE, backend=b, files={"orders.csv": orders_text()}) for _ in range(2)]
    got = [json.loads(r.stdout) if r.ok else None for r in runs]
    want = host_revenue_cents()
    return _r("deterministic_result", got[0] == want and got[1] == want, {"sandbox": got[0], "host": want, "second_run_equal": got[0] == got[1]})


def wall_timeout(b: Backend) -> dict:
    lim = Limits(wall_seconds=1.5, cpu_seconds=30)
    busy = run_code("while True: pass", backend=b, limits=lim)
    sleepy = run_code("import time; time.sleep(60)", backend=b, limits=lim)
    ok = all(r.error_code == "TIMEOUT" and r.duration_ms < 4000 for r in (busy, sleepy))
    return _r("wall_timeout", ok, {"busy_loop": [busy.error_code, busy.duration_ms], "sleep": [sleepy.error_code, sleepy.duration_ms]})


def timeout_kills_process_tree(b: Backend) -> dict:
    marker = str(900000 + os.getpid() % 90000)
    code = f"""
import subprocess
for _ in range(3):
    subprocess.Popen(["/usr/bin/sleep", "{marker}"], start_new_session=True)
while True: pass
"""
    r = run_code(code, backend=b, limits=Limits(wall_seconds=1.5, cpu_seconds=30))
    left = host_pids_with(f"sleep {marker}")
    return _r("timeout_kills_process_tree", r.error_code == "TIMEOUT" and not left, {"result": r.error_code, "host_pids_left": left})


def cpu_limit(b: Backend) -> dict:
    r = run_code("while True: pass", backend=b, limits=Limits(wall_seconds=10, cpu_seconds=1))
    return _r("cpu_limit", r.error_code == "CPU_LIMIT" and r.duration_ms < 5000, {"result": r.error_code, "exit_code": r.exit_code, "duration_ms": r.duration_ms})


def memory_limit(b: Backend) -> dict:
    big = run_code("x = bytearray(1 << 30)", backend=b)
    grow = run_code("a = []\nwhile True: a.append(bytearray(1 << 20))", backend=b)
    small = run_code("x = bytearray(32 << 20); print(len(x))", backend=b)
    ok = big.error_code == grow.error_code == "MEMORY_LIMIT" and small.ok
    return _r("memory_limit", ok, {"1GiB_at_once": big.error_code, "grow_1MiB_steps": grow.error_code, "32MiB_allowed": small.ok, "limit_bytes": Limits().memory_bytes})


def file_size_limit(b: Backend) -> dict:
    r = run_code("open('big.bin', 'wb').write(b'x' * (2 << 20))", backend=b)
    return _r("file_size_limit", r.error_code == "FILE_SIZE_LIMIT", {"result": r.error_code, "limit_bytes": Limits().file_bytes})


def max_processes(b: Backend) -> dict:
    code = """
import os, time
n = 0
try:
    for _ in range(200):
        if os.fork() == 0:
            time.sleep(2); os._exit(0)
        n += 1
except OSError as e:
    print(n, e.errno)
"""
    r = run_code(code, backend=b)
    parts = r.stdout.split()
    n = int(parts[0]) if parts else -1
    return _r("max_processes", r.ok and 0 <= n < Limits().max_processes, {"forks_before_refusal": n, "limit": Limits().max_processes, "errno": parts[1:] or None})


def output_truncated(b: Backend) -> dict:
    lim = Limits()
    r = run_code("import sys\nsys.stdout.write('x' * 1_000_000)", backend=b)
    ok = r.ok and r.stdout_truncated and len(r.stdout) == lim.output_bytes and r.stdout_bytes == 1_000_000
    return _r("output_truncated", ok, {"kept": len(r.stdout), "produced": r.stdout_bytes, "cap": lim.output_bytes})


def canary_unreadable(b: Backend) -> dict:
    d = tempfile.mkdtemp(prefix="canary-")
    os.chmod(d, 0o755)
    path = os.path.join(d, "canary.txt")
    secret = "CANARY-" + os.urandom(6).hex()
    Path(path).write_text(secret)
    os.chmod(path, 0o644)  # world-readable: only the sandbox can stop the read
    host_can_read = Path(path).read_text() == secret
    code = f"""
import os, json
res = {{}}
try:
    res["read"] = open({path!r}).read()
except OSError as e:
    res["read"] = type(e).__name__
res["parent_exists"] = os.path.exists({os.path.dirname(path)!r})
res["visible_roots"] = sorted(os.listdir("/"))
print(json.dumps(res))
"""
    r = run_code(code, backend=b)
    os.unlink(path)
    os.rmdir(d)
    seen = json.loads(r.stdout) if r.ok else {}
    ok = host_can_read and r.ok and seen.get("read") == "FileNotFoundError" and secret not in r.stdout + r.stderr
    return _r("canary_unreadable", ok, {"host_can_read": host_can_read, "canary_path": path, "sandbox_saw": seen})


def network_refused(b: Backend) -> dict:
    t = TcpTarget()
    try:
        with socket.create_connection(("127.0.0.1", t.port), timeout=2) as c:
            host_got = c.recv(64).decode().strip()
        before = t.accepted
        code = f"""
import socket, json
s = socket.socket(); s.settimeout(2)
rc = s.connect_ex(("127.0.0.1", {t.port}))
ifaces = [l.split(":")[0].strip() for l in open("/proc/net/dev").read().splitlines()[2:]]
print(json.dumps({{"connect_errno": rc, "interfaces": ifaces}}))
"""
        r = run_code(code, backend=b)
        seen = json.loads(r.stdout) if r.ok else {}
        after = t.accepted
    finally:
        t.close()
    ok = host_got.startswith("hello") and r.ok and seen.get("connect_errno", 0) != 0 and after == before
    return _r("network_refused", ok, {"host_control": host_got, "sandbox": seen, "server_accepts_from_sandbox": after - before})


def no_leftovers(b: Backend) -> dict:
    marker = str(800000 + os.getpid() % 90000)
    first = run_code(f"""
import subprocess
open("leftover.txt", "w").write("from run 1")
subprocess.Popen(["/usr/bin/sleep", "{marker}"], start_new_session=True)
print("planted")
""", backend=b)
    host_left = host_pids_with(f"sleep {marker}")
    second = run_code("""
import os, json
procs = []
for p in os.listdir("/proc"):
    if p.isdigit():
        try: procs.append(open(f"/proc/{p}/cmdline").read().replace("\\0", " ").strip())
        except OSError: pass
print(json.dumps({"workdir": sorted(os.listdir(".")), "processes": procs}))
""", backend=b)
    seen = json.loads(second.stdout) if second.ok else {}
    ok = (first.ok and not host_left and second.ok and seen.get("workdir") == []
          and not any("sleep" in p for p in seen.get("processes", ["sleep?"])))
    return _r("no_leftovers", ok, {"run1": first.stdout.strip(), "host_pids_left": host_left, "run2_saw": seen})


SECRET_VARS = {"ANTHROPIC_API_KEY": "sk-ant-canary-", "AWS_SECRET_ACCESS_KEY": "aws-canary-", "DATABASE_URL": "postgres://canary-"}


def env_scrubbed(b: Backend) -> dict:
    tag = os.urandom(4).hex()
    saved = {k: os.environ.get(k) for k in SECRET_VARS}
    os.environ.update({k: v + tag for k, v in SECRET_VARS.items()})
    try:
        r = run_code("""
import os, json
envs = []
for p in os.listdir("/proc"):
    if p.isdigit():
        try: envs.append(open(f"/proc/{p}/environ", "rb").read().decode(errors="replace"))
        except OSError: pass
print(json.dumps({"environ": dict(os.environ), "proc_environ": envs}))
""", backend=b)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    seen = json.loads(r.stdout) if r.ok else {}
    keys = sorted(seen.get("environ", {}))
    leaked = tag in r.stdout + r.stderr
    ok = r.ok and not leaked and set(keys) <= {"PATH", "LANG", "HOME", "PWD"}
    return _r("env_scrubbed", ok, {"sandbox_env_keys": keys, "secret_seen": leaked})


def host_invisible(b: Backend) -> dict:
    r = run_code("""
import os, json
print(json.dumps({"pids": sorted(int(p) for p in os.listdir("/proc") if p.isdigit()), "hostname": os.uname().nodename,
                  "uid": os.getuid(), "cap_eff": open("/proc/self/status").read().split("CapEff:")[1].split()[0]}))
try:
    open("/usr/sandbox-write-test", "w"); print("usr writable")
except OSError: pass
try:
    open("/root-write-test", "w"); print("root writable")
except OSError: pass
""", backend=b)
    lines = r.stdout.splitlines()
    seen = json.loads(lines[0]) if r.ok and lines else {}
    ok = (r.ok and len(lines) == 1 and os.getpid() not in seen.get("pids", [os.getpid()]) and len(seen["pids"]) <= 4
          and seen.get("cap_eff") == "0000000000000000" and seen.get("hostname") == "sandbox")
    return _r("host_invisible", ok, {**seen, "writes_succeeded": lines[1:]})


ALL = [deterministic_result, wall_timeout, timeout_kills_process_tree, cpu_limit, memory_limit, file_size_limit,
       max_processes, output_truncated, canary_unreadable, network_refused, no_leftovers, env_scrubbed, host_invisible]


def run_all(b: Backend) -> list[dict]:
    if not b.isolating:
        r = run_code("print(1)", backend=b)
        return [{"control": c.__name__, "passed": False, "status": "not verified", "detail": r.error_code} for c in ALL]
    out = []
    for c in ALL:
        res = c(b)
        res["status"] = "passed" if res["passed"] else "FAILED"
        out.append(res)
    return out
