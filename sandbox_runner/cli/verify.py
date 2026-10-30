"""Run every control check against one backend and write the evidence file.

python -m sandbox_runner.cli.verify [--backend auto|bwrap|unshare|none]
Exit 0 all passed, 1 a control failed, 3 not verified (no working backend).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .. import checks
from ..runner import select_backend
from ..session import session_record


def main() -> int:
    if sys.platform != "linux":
        print("Not verified: macOS native is not supported (no Linux namespaces). Use a Linux VM.")
        return 3
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=os.environ.get("SANDBOX_BACKEND", "auto"))
    a = ap.parse_args()
    b = select_backend(a.backend)
    file = Path(f"evidence/verify-{date.today().isoformat()}-{b.name}.jsonl")
    file.parent.mkdir(exist_ok=True)
    sess = session_record("verify", b)
    lines = [json.dumps(sess)]
    print(f"sandbox-runner control checks  rev={sess['code_rev']}  backend={b.name} ({b.version})  kernel={sess['kernel']}  euid={sess['host_euid']}")
    if a.backend not in ("auto", b.name):
        print(f"requested backend {a.backend!r} does not work here, fell back to {b.name!r}")
    res = checks.run_all(b)
    for r in res:
        lines.append(json.dumps({"type": "control", "backend": b.name, **r}))
        print(f"  {r['status']:<13} {r['control']:<28} {json.dumps(r['detail'])[:110]}")
    file.write_text("\n".join(lines) + "\n")
    print(f"log: {file}")
    if not b.isolating:
        return 3
    return 0 if all(r["passed"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
