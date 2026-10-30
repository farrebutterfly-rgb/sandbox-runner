"""Show which sandbox backends exist here and which one actually works.

python -m sandbox_runner.cli.probe
"""
from __future__ import annotations

import sys

from .. import backends as B
from ..runner import probe, select_backend


def main() -> int:
    print(f"python {sys.version.split()[0]} on {sys.platform}")
    if sys.platform != "linux":
        print("macOS native is not supported: no Linux namespaces. Use a Linux VM. Untrusted code will be refused.")
        return 0
    for name, find in (("bwrap", B.find_bwrap), ("unshare", B.find_unshare)):
        found = find()
        if not found:
            print(f"  {name:<8} not installed")
            continue
        works = probe(name) is not None
        print(f"  {name:<8} {found.path} ({found.version})  {'works' if works else 'present but does not work (user namespaces blocked?)'}")
    b = select_backend()
    print(f"selected backend: {b.name}" + ("" if b.isolating else "  (untrusted code will be refused, controls not verified)"))
    print(f"sandbox python: {B.sandbox_python()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
