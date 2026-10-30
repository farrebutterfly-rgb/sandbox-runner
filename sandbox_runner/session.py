"""The first line of every evidence file: what code, data, runtime and sandbox produced it."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

from .backends import Backend, sandbox_python
from .checks import DATASET


def code_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "sandbox_runner", "tests", "data"], capture_output=True, text=True, timeout=5).stdout.strip()
        return (rev or "uncommitted") + ("-dirty" if dirty else "")
    except Exception:
        return "uncommitted"


def session_record(mode: str, b: Backend) -> dict:
    sp = sandbox_python()
    try:
        sp_ver = subprocess.run([sp, "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True, timeout=5, env={}).stdout.strip()
    except Exception:
        sp_ver = None
    return {
        "type": "session", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_rev": code_rev(),
        "dataset": DATASET, "python": platform.python_version(), "mode": mode, "backend": b.name,
        "backend_version": b.version, "sandbox_python": f"{sp} {sp_ver}", "kernel": platform.release(),
        "host_euid": os.geteuid(), "platform": sys.platform,
    }
