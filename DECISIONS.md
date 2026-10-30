# Decisions

**Never in the host process.** The host builds an argv for a sandbox wrapper and starts it with `subprocess.Popen`. The agent's code travels as JSON on stdin and is exec'd by a short bootstrap inside the sandbox. Nothing in the host calls `exec` or `eval` on it. With the `none` backend the runner returns `NO_SANDBOX` and starts no process at all; there is no "unsafe local" switch.

**Backend chosen at runtime, by proof, not by presence.** `bwrap` first, then `unshare`, then `none`. A backend counts only if a trivial program actually runs through it (`probe`). Ubuntu 23.10 and later can block unprivileged user namespaces with AppArmor, so a binary on `PATH` says little. The chosen backend is written into every log line and the first line of every evidence file.

**bubblewrap flags.** `--unshare-all` (user, mount, pid, net, ipc, uts, cgroup), `--uid/--gid 65534`, `--clearenv` plus three fixed variables, `--ro-bind /usr` and the merged-usr symlinks only, `--proc`, `--dev`, `--size` + `--tmpfs /work`, `--remount-ro /`, `--die-with-parent`, `--new-session`, `--disable-userns`, `--cap-drop ALL`. Flags taken from `bwrap --help` of 0.9.0; `--size` and `--disable-userns` are used only when that help text lists them. bwrap was not installed in the build container at first; it was installed from Ubuntu's `bubblewrap 0.9.0-1ubuntu0.3` package so both backends could be tested.

**unshare fallback uses pivot_root, not chroot.** Inside `unshare --user --map-root-user` the process is root of its own user namespace and keeps `CAP_SYS_CHROOT`, so a plain chroot can be escaped with the classic nested-chroot trick. The script builds a root on a tmpfs, `pivot_root`s into it, lazily unmounts the old root so the host tree is no longer reachable in that mount namespace, remounts `/` read-only, and starts the code through `setpriv --no-new-privs --bounding-set -all --inh-caps -all`. The control check confirms `CapEff` is zero and that `chroot` and `umount` fail.

**sh stays pid 1 in the unshare backend.** The first version exec'd Python as pid 1 of the new pid namespace. pid 1 ignores signals whose action is the default, and the soft `RLIMIT_CPU` signal (SIGXCPU) was ignored: a busy loop with `--cpu=1:2` ran until the hard-limit SIGKILL at 2 s. Keeping `/bin/sh` as pid 1 makes the code an ordinary process that dies at the soft limit like it does under bwrap (whose own init is pid 1).

**Limits.** `setrlimit` in a `preexec_fn`, inherited by the wrapper and everything below it: `RLIMIT_CPU` (soft, hard = soft + 1), `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, `RLIMIT_CORE=0`. The wall clock is enforced by the host: the wrapper runs in its own session (`start_new_session=True`) and the whole process group gets SIGKILL at the deadline, and on normal exit too. Killing the wrapper kills pid 1 of the sandbox's pid namespace, which takes every process in it down; `timeout_kills_process_tree` checks the host's `/proc` afterwards.

**RLIMIT_NPROC is set inside the sandbox.** NPROC counts every process of the real uid. Set on the host side, a user with 300 processes and a limit of 16 could not even start bwrap. Since Linux 5.14 the count is kept per user namespace, so the bootstrap sets it as the first thing inside the new namespace, where it counts only sandbox processes. Tested: fork stops at 13 to 14 children with a limit of 16, and still does when the same host uid already has 41 other processes running (manual run as uid 65534 with 40 host `sleep` processes, both backends).

**Root drops to 65534.** If the host runs as root (the build container does), the wrapper is started as uid/gid 65534 with no supplementary groups. The namespaces are then created unprivileged, the same way they are on a normal user's machine. The suite was also run entirely as uid 65534 to cover the non-root path.

**Output is drained, not trusted.** Reader threads drain stdout and stderr to the end and keep the first `output_bytes` of each, recording the true byte count and a `truncated` flag. Draining keeps the child from blocking on a full pipe; the wall clock bounds how long a flood can last.

**Ready marker.** The bootstrap writes a marker to stderr before reading the payload. No marker means the wrapper failed to build the sandbox (`SANDBOX_ERROR`), which keeps wrapper errors apart from errors in the code.

**Sandbox interpreter under /usr.** Only `/usr` is bound, so the interpreter must live there. If the host Python lives elsewhere (for example a uv-managed 3.10 under `/root`), the sandbox uses `/usr/bin/python3`, or `SANDBOX_PYTHON`. The evidence header records which one ran.

**Standard library only.** Tests use `unittest`, the live model adapter uses `urllib`. `make install` creates a `.venv` with nothing to download.

**Not done on purpose.** No seccomp filter (bwrap can load one with `--seccomp FD`; writing and testing a BPF policy is its own project). No cgroups, so memory is bounded per process by address space, not for the whole sandbox. No network allowlist, no warm worker pool, no container image. Each would change what the sandbox protects against, and none is claimed here.
