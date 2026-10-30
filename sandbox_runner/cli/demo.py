"""Fixture demo: scripted model, real sandbox, no API calls.

python -m sandbox_runner.cli.demo [--backend auto|bwrap|unshare|none] [scenario]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from ..agent import run_agent
from ..backends import Backend
from ..checks import TcpTarget, orders_text
from ..limits import Limits
from ..log import RunLog
from ..runner import select_backend
from ..scenarios import SCENARIOS, Ctx
from ..session import session_record
from ..tool import Toolbox

SECRET_VAR = "DEMO_SECRET_TOKEN"


def main() -> int:
    if sys.platform != "linux":
        print("sandbox-runner needs Linux namespaces (bwrap or unshare). macOS native is not supported; use a Linux VM.")
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=os.environ.get("SANDBOX_BACKEND", "auto"))
    ap.add_argument("scenario", nargs="?")
    a = ap.parse_args()
    backend = select_backend(a.backend)
    file = Path(f"evidence/fixture-{date.today().isoformat()}-{backend.name}.jsonl")
    file.parent.mkdir(exist_ok=True)
    sess = session_record("fixture", backend)
    file.write_text(json.dumps(sess) + "\n")
    print(f"sandbox-runner fixture demo  (scripted model, no API calls)  rev={sess['code_rev']}  dataset={sess['dataset']}")
    print(f"backend={backend.name} ({backend.version})  sandbox python={sess['sandbox_python']}\n")
    if not backend.isolating:
        print("No working sandbox backend. Every run below is refused; nothing is verified.\n")

    # Real targets for the escape scenario: a world-readable canary file and a TCP server on the host.
    d = tempfile.mkdtemp(prefix="canary-")
    os.chmod(d, 0o755)
    canary = Path(d, "host-secret.txt")
    canary.write_text("host-only canary, must never reach the sandbox\n")
    canary.chmod(0o644)
    target = TcpTarget()
    os.environ[SECRET_VAR] = "demo-canary-" + os.urandom(4).hex()
    ctx = Ctx(str(canary), target.port, SECRET_VAR)

    summary = []
    try:
        for s in SCENARIOS:
            if a.scenario and s.name != a.scenario:
                continue
            b = Backend("none") if s.force_backend == "none" else backend
            log = RunLog(b.name, str(file), echo=True)
            q = s.question(ctx)
            print(f"> {s.name}: \"{q}\"")
            tools = Toolbox(b, {"orders.csv": orders_text()}, Limits(**s.limits))
            r = run_agent(q, model=s.model(ctx), tools=tools, log=log)
            if r.answer:
                print(f"  answer [{r.answer['status']}]: {r.answer['answer']}\n")
            summary.append(f"{s.name:<26} {b.name:<8} {r.status:<20} steps={r.steps} tool_calls={r.tool_calls} errors={r.tool_errors} run={log.run_id}")
    finally:
        target.close()
        canary.unlink()
        os.rmdir(d)
        os.environ.pop(SECRET_VAR, None)
    print("\n".join(summary))
    print(f"\nhost TCP test server accepted {target.accepted} connection(s) from the sandbox")
    print(f"log: {file}  (code rev {sess['code_rev']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
