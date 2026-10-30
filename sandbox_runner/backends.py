"""Sandbox backends. Each one turns (interpreter, bootstrap) into an argv that starts the
interpreter in fresh namespaces with a read-only /usr, a fresh tmpfs work dir and no network.

Flags follow `bwrap --help` (bubblewrap 0.9.0) and `unshare --help` (util-linux 2.39.3),
read in the build environment on 2026-10-30.

  bwrap    new user, mount, pid, net, ipc, uts, cgroup namespaces; pivot_root done by bwrap.
  unshare  new user, mount, pid, net, ipc, uts namespaces; a small sh script builds the root
           on a tmpfs, pivot_roots into it, unmounts the old root, drops all capabilities.
  none     no isolation available. The runner refuses untrusted code.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache

from .limits import Limits

WORKDIR = "/work"
SANDBOX_ENV = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "HOME": WORKDIR}
NOBODY = 65534
# Top-level dirs that are either symlinks into /usr (merged-usr) or must be bound read-only.
ROOT_LINKS = ("bin", "sbin", "lib", "lib32", "lib64")
DEV_NODES = ("null", "zero", "urandom")


@dataclass
class Backend:
    name: str
    path: str | None = None
    version: str | None = None
    features: set[str] = field(default_factory=set)

    @property
    def isolating(self) -> bool:
        return self.name != "none"


@lru_cache(maxsize=1)
def sandbox_python() -> str:
    """Interpreter used inside the sandbox. It must live under /usr, the only host tree we bind.

    SANDBOX_PYTHON overrides. Otherwise the host interpreter if it is under /usr, else /usr/bin/python3.
    """
    env = os.environ.get("SANDBOX_PYTHON")
    if env:
        return os.path.realpath(env)
    host = os.path.realpath(sys.executable)
    if host.startswith("/usr/"):
        return host
    return os.path.realpath("/usr/bin/python3")


def _root_entries() -> list[tuple[str, str]]:
    """('link', target) or ('bind', path) for /bin, /lib, ... as they exist on this host."""
    out = []
    for d in ROOT_LINKS:
        p = "/" + d
        if os.path.islink(p):
            out.append(("link", d, os.readlink(p).lstrip("/")))
        elif os.path.isdir(p):
            out.append(("bind", d, p))
    return out


def _version(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env={})
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return None


def find_bwrap() -> Backend | None:
    path = shutil.which("bwrap")
    if not path:
        return None
    try:
        helptext = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=5, env={}).stdout
    except Exception:
        return None
    features = {f for f in ("--size", "--disable-userns", "--die-with-parent", "--clearenv") if f in helptext}
    return Backend("bwrap", path, _version([path, "--version"]), features)


def find_unshare() -> Backend | None:
    path = shutil.which("unshare") or ("/usr/bin/unshare" if os.path.exists("/usr/bin/unshare") else None)
    if not path or not all(os.path.exists(p) for p in ("/usr/bin/setpriv", "/usr/bin/env")):
        return None
    return Backend("unshare", path, _version([path, "--version"]))


def bwrap_argv(b: Backend, interp: str, bootstrap: str, limits: Limits) -> list[str]:
    a = [b.path or "bwrap", "--unshare-all", "--unshare-user", "--uid", str(NOBODY), "--gid", str(NOBODY),
         "--hostname", "sandbox", "--die-with-parent", "--new-session", "--clearenv"]
    for k, v in SANDBOX_ENV.items():
        a += ["--setenv", k, v]
    a += ["--ro-bind", "/usr", "/usr"]
    for kind, name, target in _root_entries():
        a += ["--symlink", target, "/" + name] if kind == "link" else ["--ro-bind", target, "/" + name]
    a += ["--proc", "/proc", "--dev", "/dev"]
    if "--size" in b.features:
        a += ["--size", str(limits.workdir_bytes)]
    a += ["--tmpfs", WORKDIR, "--remount-ro", "/", "--chdir", WORKDIR]
    if "--disable-userns" in b.features:
        a += ["--disable-userns"]
    a += ["--cap-drop", "ALL", "--", interp, "-I", "-S", "-c", bootstrap]
    return a


def unshare_script(mountpoint: str, interp: str, bootstrap: str, limits: Limits) -> str:
    q = shlex.quote
    lines = [
        "set -eu",
        "/usr/bin/mount --make-rprivate /",
        f"/usr/bin/mount -t tmpfs -o size=1m,mode=755 sandbox-root {q(mountpoint)}",
        f"cd {q(mountpoint)}",
        "mkdir usr proc dev work .old",
        "/usr/bin/mount --rbind /usr usr",
        "/usr/bin/mount -o remount,bind,ro,nosuid,nodev usr",
    ]
    for kind, name, target in _root_entries():
        if kind == "link":
            lines.append(f"ln -s {q(target)} {q(name)}")
        else:
            lines += [f"mkdir {q(name)}", f"/usr/bin/mount --rbind {q(target)} {q(name)}",
                      f"/usr/bin/mount -o remount,bind,ro,nosuid,nodev {q(name)}"]
    for d in DEV_NODES:
        lines += [f"touch dev/{d}", f"/usr/bin/mount --bind /dev/{d} dev/{d}"]
    lines += [
        "/usr/bin/mount -t proc -o nosuid,nodev,noexec proc proc",
        f"/usr/bin/mount -t tmpfs -o size={limits.workdir_bytes},mode=777,nosuid,nodev sandbox-work work",
        "/usr/bin/hostname sandbox",
        "/usr/sbin/pivot_root . .old",
        "/usr/bin/umount -l /.old",
        "rmdir /.old",
        "/usr/bin/mount -o remount,ro,bind /",
        f"cd {WORKDIR}",
        # No exec: sh stays pid 1 of the new pid namespace, so the code is an ordinary process
        # that receives SIGXCPU like anywhere else (pid 1 ignores default-action signals).
        "/usr/bin/env -i " + " ".join(f"{k}={q(v)}" for k, v in SANDBOX_ENV.items())
        + " /usr/bin/setpriv --no-new-privs --bounding-set -all --inh-caps -all --"
        + f" {q(interp)} -I -S -c {q(bootstrap)}",
    ]
    return "\n".join(lines) + "\n"


def unshare_argv(b: Backend, mountpoint: str, interp: str, bootstrap: str, limits: Limits) -> list[str]:
    return [b.path or "unshare", "--user", "--map-root-user", "--mount", "--pid", "--net", "--ipc", "--uts",
            "--fork", "--kill-child", "--", "/bin/sh", "-c", unshare_script(mountpoint, interp, bootstrap, limits)]
