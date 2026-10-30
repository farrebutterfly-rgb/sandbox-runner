"""The few lines that run first inside the sandbox, passed to the interpreter with -c.

The untrusted code never touches argv or the host: it arrives as JSON on stdin together
with the input files, is written into the fresh work dir, and is exec'd there.
Exit codes: 0 ok, 1 exception in the code, 3 MemoryError, 4 file size limit, 5 bad payload.
The code could fake these codes with sys.exit; that only lies about its own run.
"""

BOOTSTRAP = r"""
import errno, json, os, resource, sys, traceback
try:
    p = json.loads(sys.stdin.read())
    n = int(p["max_processes"])
    resource.setrlimit(resource.RLIMIT_NPROC, (n, n))
    os.chdir(p["workdir"])
    for name, text in p["files"].items():
        with open(os.path.basename(name), "w") as f:
            f.write(text)
    code = compile(p["code"], "<agent>", "exec")
except Exception:
    traceback.print_exc()
    sys.exit(5)
del p
try:
    exec(code, {"__name__": "__main__"})
except MemoryError:
    sys.stdout.flush()
    sys.stderr.write("MemoryError: memory limit reached\n")
    sys.exit(3)
except OSError as e:
    traceback.print_exc()
    sys.exit(4 if e.errno == errno.EFBIG else 1)
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    sys.exit(1)
"""
