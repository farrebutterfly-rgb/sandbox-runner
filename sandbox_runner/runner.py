"""Run a short piece of untrusted Python in a separate, sandboxed process.

The host process never execs or evals the code. It starts the sandbox wrapper with an empty
environment, rlimits applied in a preexec step, as uid 65534 if the host runs as root, in its
own process group, and SIGKILLs that group when the wall-clock limit is hit.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from functools import lru_cache

from . import backends as B
from .bootstrap import BOOTSTRAP
from .limits import Limits, preexec

READY = b"@@sandbox-ready@@\n"
_BOOT = "import sys; sys.stderr.write(%r); sys.stderr.flush()\n" % READY.decode() + BOOTSTRAP


@dataclass
class RunResult:
    run_id: str
    backend: str
    ok: bool
    error_code: str | None  # TIMEOUT CPU_LIMIT MEMORY_LIMIT FILE_SIZE_LIMIT CODE_ERROR NO_SANDBOX SANDBOX_ERROR
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    isolated: bool  # False only for the none backend, where nothing ran

    def to_dict(self) -> dict:
        return asdict(self)


class _Capped(threading.Thread):
    """Drain a pipe to the end, keep at most `cap` bytes, count the rest."""

    def __init__(self, f, cap: int):
        super().__init__(daemon=True)
        self.f, self.cap, self.buf, self.total = f, cap, bytearray(), 0

    def run(self) -> None:
        while chunk := self.f.read(65536):
            self.total += len(chunk)
            room = self.cap - len(self.buf)
            if room > 0:
                self.buf += chunk[:room]


def _writer(f, data: bytes) -> None:
    try:
        f.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            f.close()
        except OSError:
            pass


def _classify(rc: int, timed_out: bool, ready: bool) -> str | None:
    if timed_out:
        return "TIMEOUT"
    if not ready:
        return "SANDBOX_ERROR"
    if rc == 0:
        return None
    sig = rc - 128 if rc > 128 else -rc if rc < 0 else 0
    if sig == signal.SIGXCPU:
        return "CPU_LIMIT"
    if rc == 3:
        return "MEMORY_LIMIT"
    if sig == signal.SIGKILL:
        return "KILLED"
    if rc == 4 or sig == signal.SIGXFSZ:
        return "FILE_SIZE_LIMIT"
    if rc == 5:
        return "SANDBOX_ERROR"
    return "CODE_ERROR"


def run_code(code: str, *, backend: B.Backend, files: dict[str, str] | None = None, limits: Limits | None = None) -> RunResult:
    limits = limits or Limits()
    run_id = uuid.uuid4().hex[:8]
    if not backend.isolating:
        return RunResult(run_id, backend.name, False, "NO_SANDBOX", None, "",
                         "No sandbox backend works on this machine (need bwrap or unshare with user namespaces). Refusing to run untrusted code.",
                         0, 0, False, False, 0, False)

    interp = B.sandbox_python()
    payload = json.dumps({"code": code, "files": files or {}, "workdir": B.WORKDIR, "max_processes": limits.max_processes}).encode()
    mountpoint = None
    if backend.name == "bwrap":
        argv, env = B.bwrap_argv(backend, interp, _BOOT, limits), {}
    else:
        mountpoint = tempfile.mkdtemp(prefix="sbx-")
        os.chmod(mountpoint, 0o755)
        argv, env = B.unshare_argv(backend, mountpoint, interp, _BOOT, limits), {"PATH": "/usr/bin:/usr/sbin"}

    drop = {"user": B.NOBODY, "group": B.NOBODY, "extra_groups": []} if os.geteuid() == 0 else {}
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd="/",
                            close_fds=True, start_new_session=True, preexec_fn=preexec(limits), **drop)
    out, err = _Capped(proc.stdout, limits.output_bytes), _Capped(proc.stderr, limits.output_bytes + len(READY))
    w = threading.Thread(target=_writer, args=(proc.stdin, payload), daemon=True)
    for t in (out, err, w):
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=limits.wall_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # whole group, also on normal exit
    except ProcessLookupError:
        pass
    rc = proc.wait()
    for t in (out, err, w):
        t.join(timeout=2)
    for f in (proc.stdout, proc.stderr):
        f.close()
    duration = int((time.monotonic() - t0) * 1000)
    if mountpoint:
        try:
            os.rmdir(mountpoint)
        except OSError:
            pass

    raw_err = bytes(err.buf)
    ready = raw_err.startswith(READY)
    if ready:
        raw_err, err_total = raw_err[len(READY):], err.total - len(READY)
    else:
        err_total = err.total
    code_ = _classify(rc, timed_out, ready)
    return RunResult(run_id, backend.name, code_ is None, code_, None if timed_out else rc,
                     bytes(out.buf).decode("utf-8", "replace"), raw_err[: limits.output_bytes].decode("utf-8", "replace"),
                     out.total, err_total, out.total > limits.output_bytes, err_total > limits.output_bytes, duration, True)


@lru_cache(maxsize=None)
def probe(name: str) -> B.Backend | None:
    """Find a backend and prove it works by running one trivial program in it."""
    b = B.find_bwrap() if name == "bwrap" else B.find_unshare() if name == "unshare" else None
    if b is None:
        return None
    r = run_code("print('ok')", backend=b, limits=Limits(wall_seconds=10))
    return b if r.ok and r.stdout == "ok\n" else None


def select_backend(name: str | None = None) -> B.Backend:
    """bwrap if it works, else unshare, else none. SANDBOX_BACKEND or `name` forces one."""
    name = name or os.environ.get("SANDBOX_BACKEND", "auto")
    if name == "none":
        return B.Backend("none")
    for candidate in (["bwrap", "unshare"] if name == "auto" else [name]):
        b = probe(candidate)
        if b:
            return b
    return B.Backend("none")
