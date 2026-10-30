# Acceptance

Checked 2026-10-30. "Not run" means not run, not passed. "Not verified" is what the tool itself reports when no backend works.

## Environments

| ID | Where | What ran |
| --- | --- | --- |
| E1 | Build container: Ubuntu 24.04.5, kernel 6.18.44, x86_64. Host Python 3.13.18 and 3.10.20. bubblewrap 0.9.0 (installed from Ubuntu's package during the build, it was not there at first), util-linux 2.39.3. Host process runs as root; the runner drops the sandbox wrapper to uid 65534. | `make check`, `make verify` for `bwrap`, `unshare` and `none` |
| E2 | Same container, the whole repo copied and run as uid 65534 (non-root host path, no privilege drop needed) | test suite and `verify` for both backends |
| E3 | The user's Linux VM (Ubuntu 22.04, aarch64, kernel 6.8, Python 3.10.12, bubblewrap 0.6.1, non-root euid) | **Passed.** 41/41 tests and all 13 controls with backend bwrap, 2026-10-30. | `evidence/verify-2026-10-30-bwrap-user-vm-arm64.jsonl` |

Evidence files, all from code rev `43e074b`: E1 `evidence/verify-2026-10-30-bwrap.jsonl`, `-unshare.jsonl` (13 of 13 passed each), `-none.jsonl` (13 not verified, exit 3); E2 `evidence/verify-2026-10-30-bwrap-nonroot.jsonl`, `-unshare-nonroot.jsonl` (13 of 13 each, `host_euid` 65534); fixture demo `evidence/fixture-2026-10-30-bwrap.jsonl`, `-unshare.jsonl`.

The sandboxed interpreter was `/usr/local/bin/python3.13` in E1 and E2 (when the host Python lives outside `/usr`, the sandbox uses the `/usr` one; see DECISIONS.md). The test suite passed with the host on 3.13.18 and on 3.10.20.

## Acceptance criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | A normal computation gives a result that can be checked deterministically | Passed, bwrap and unshare, E1 and E2 | control `deterministic_result`: revenue per region computed in the sandbox equals an independent host computation, twice; scenario `revenue-by-region` |
| 2 | A too-long program is stopped, and resource limits are shown with small controlled tests | Passed, bwrap and unshare, E1 and E2 | `wall_timeout` (busy loop and sleep, both killed at 1.5 s), `timeout_kills_process_tree`, `cpu_limit` (SIGXCPU), `memory_limit` (1 GiB at once and 1 MiB steps stopped, 32 MiB allowed), `file_size_limit`, `max_processes`, `output_truncated`; scenarios `runaway-then-fix`, `memory-bomb-then-stream`, `output-flood` |
| 3 | A harmless test file outside the allowed file area cannot be read | Passed, bwrap and unshare, E1 and E2 | `canary_unreadable`: world-readable file in a host temp dir, host reads it (control), sandbox gets `FileNotFoundError` and the directory does not exist for it; scenario `escape-attempts` |
| 4 | A connection to a controlled test destination is refused | Passed, bwrap and unshare, E1 and E2 | `network_refused`: TCP server on host 127.0.0.1, host connects (control), sandbox gets errno 111 (bwrap, own loopback up) or 101 (unshare, loopback down), server counts 0 accepts from the sandbox; only `lo` exists inside |
| 5 | The next run starts without files or processes left from the previous job | Passed, bwrap and unshare, E1 and E2 | `no_leftovers`: run 1 writes a file and starts a detached `sleep`; host `/proc` shows no survivor; run 2 sees an empty work dir and no `sleep`; scenario `no-leftovers` |
| 6 | If the runtime is missing, the project is marked not run-verified | Passed, E1 | backend `none`: `run_code` returns `NO_SANDBOX` and starts nothing (test), `make verify` with `--backend none` marks every control "not verified" and exits 3 (`evidence/verify-*-none.jsonl`); scenario `no-sandbox-refused` |

## Extra controls from the project notes

| Control | Status | Evidence |
| --- | --- | --- |
| Code runs in a separate process, never in the host process | Passed, both backends, E1 and E2 | `host_invisible`: the sandbox sees 2 pids (its own pid namespace), not the host's pid |
| No API keys or host env vars in the sandbox | Passed, both backends, E1 and E2 | `env_scrubbed`: host sets `ANTHROPIC_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL` canaries; sandbox env holds only `PATH LANG HOME (PWD)`, and no `/proc/*/environ` inside contains the canary |
| Root read-only, `/usr` read-only, no capabilities, own hostname | Passed, both backends, E1 and E2 | `host_invisible`: writes to `/` and `/usr` fail, `CapEff` 0, hostname `sandbox` |
| Every log line names the backend | Passed | `RunLog` adds `backend` to each line; test `test_revenue_answer_matches_host_computation` |
| Live model run | **Not run.** No `ANTHROPIC_API_KEY` in the build environment. `make live` exits 2 with a clear message (tested). | `make live` writes `evidence/live-*.jsonl` |

## Limitations (read before trusting this with hostile code)

- **A namespace sandbox is not proof of isolation against arbitrary hostile code.** The code shares the host kernel. Every syscall the kernel offers to an unprivileged user in a user namespace is reachable, and unprivileged user namespaces are themselves a known kernel attack surface. There is **no seccomp filter** (bwrap supports one; none is loaded). A kernel bug is an escape.
- Memory is limited per process by address space (`RLIMIT_AS`), not by a cgroup, so the bound for the whole sandbox is about `max_processes x memory_bytes`. The tmpfs work dir also uses memory. CPU time is per process; the wall clock bounds the total.
- All of `/usr` is visible read-only, and `/proc` shows host-wide facts such as CPU and memory info and uptime.
- In the unshare backend the code runs as uid 0 of its own user namespace with no capabilities (`CapEff` 0); in bwrap it runs as 65534.
- The exit-code mapping (3 memory, 4 file size) comes from the in-sandbox bootstrap; the code can fake its own status with `sys.exit`. It cannot change the host's timeout or truncation.
- Tested in one environment family (E1, E2). Other kernels, AppArmor or SELinux policies may block a backend; `make install` and `make verify` show what works there.
- **macOS native is unsupported.** There are no Linux namespaces; the CLIs say so and refuse. Use a Linux VM.
