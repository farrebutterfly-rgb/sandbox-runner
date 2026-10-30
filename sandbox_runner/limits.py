"""Resource limits for one sandboxed run.

Most limits are set with setrlimit in a preexec step on the host side, just before the
sandbox wrapper (bwrap or unshare) is exec'd, so the wrapper and everything it starts
inherit them. RLIMIT_NPROC is the exception: it is set inside the sandbox by the bootstrap,
see DECISIONS.md for why.
"""
from __future__ import annotations

import resource
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 5.0  # whole process group is SIGKILLed after this
    cpu_seconds: int = 2  # RLIMIT_CPU, per process; SIGXCPU at the soft limit
    memory_bytes: int = 256 * 1024 * 1024  # RLIMIT_AS (virtual address space)
    file_bytes: int = 1024 * 1024  # RLIMIT_FSIZE, largest file the code may write
    open_files: int = 64  # RLIMIT_NOFILE
    max_processes: int = 16  # RLIMIT_NPROC, applied inside the sandbox
    output_bytes: int = 64 * 1024  # stdout and stderr are each truncated to this
    workdir_bytes: int = 16 * 1024 * 1024  # size of the fresh tmpfs work dir

    def to_dict(self) -> dict:
        return asdict(self)


def preexec(limits: Limits):
    """Return a callable for subprocess.Popen(preexec_fn=...). Only async-signal-safe-ish work."""

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply
