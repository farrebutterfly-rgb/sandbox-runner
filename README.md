# sandbox-runner

An agent tool that runs model-written Python in a separate, isolated process and hands back the result. The agent proposes a short calculation on synthetic data; the code runs in fresh Linux namespaces with no network, a read-only system, a throwaway tmpfs work dir, an empty environment, and wall-clock, CPU, memory, file-size, process and output limits. It never runs inside the host process. Each control is checked by running real code against it, on every backend that works on the machine.

## Quick start

Linux, Python 3.10 or later, and `bwrap` (bubblewrap) or `unshare` (util-linux) with unprivileged user namespaces. Standard library only, nothing to download. macOS native is not supported.

```bash
make install     # .venv, then shows which backends work here
make demo        # seven fixture scenarios, scripted model, real sandbox, no API key
make test        # 41 tests: 13 controls x each working backend, plus agent and tool tests
make verify      # control checks for the selected backend -> evidence/verify-*.jsonl
make check       # lint + dash check + test + demo
```

Force a backend with `SANDBOX_BACKEND=bwrap|unshare|none` (or `--backend` on the CLIs). Live run with a real model (costs money):

```bash
export ANTHROPIC_API_KEY=...
make live
```

## What the demo shows

```
backend=bwrap (bubblewrap 0.9.0)  sandbox python=/usr/local/bin/python3.13 3.13.18

> revenue-by-region: "Which region has the highest revenue, and how much?"
  [03] bwrap   ok  run_python  70ms
         stdout: {"east": 2218475, "north": 1254900, "south": 977050, "west": 1735325}
  answer [answered]: east has the highest revenue: 22,184.75 (from 4 regions).

> runaway-then-fix: "How many orders have more than 20 units?"
  [03] bwrap   ERR run_python  -> TIMEOUT  2013ms
  [05] bwrap   ok  run_python  37ms
         stdout: 36 60
  answer [answered]: 36 of 60 orders have more than 20 units.

> memory-bomb-then-stream      MEMORY_LIMIT, then a streaming version answers
> escape-attempts              env has only PATH LANG HOME PWD, host file: FileNotFoundError,
                               TCP to the host test server: errno 111, server saw 0 connections
> no-leftovers                 job 2 sees an empty work dir and no process from job 1
> output-flood                 3,428,000 bytes printed, 65,536 kept, flagged as truncated
> no-sandbox-refused           backend none: NO_SANDBOX, nothing runs, no number reported
```

Every log line names the backend. The full JSONL logs are in `evidence/`, one file per backend.

## How it works

```
agent loop (max N steps) ----> ModelClient.next()          fixture: ScriptedModel
   |                                                       live:    AnthropicModel (urllib)
   | run_python {code}
   v
Toolbox.execute: known tool? valid args? (size cap)  else UNKNOWN_TOOL / INVALID_ARGUMENTS
   |
   v
run_code (host process, never execs the code)
   backend = bwrap | unshare | none        (chosen by a real probe run, not by PATH)
   none   -> NO_SANDBOX, nothing started
   Popen(wrapper argv, env={}, new session, uid 65534 if root,
         preexec: RLIMIT_CPU AS FSIZE NOFILE CORE)
   stdin: {"code", "files"} as JSON
   wall clock -> SIGKILL whole process group
   stdout/stderr drained, capped, byte counts kept
   |
   v
inside: new user/mount/pid/net/ipc/uts ns, / read-only, /usr read-only,
        fresh tmpfs /work, no caps, only lo
   bootstrap: RLIMIT_NPROC, write files to /work, exec(code)
   exit 3 MemoryError, 4 file too large, SIGXCPU -> CPU_LIMIT
   |
   v
result {run_id, backend, ok, error_code, stdout, truncated, ...} -> model
   |
   v
final_answer: answered must cite run_ids of successful runs in this conversation
```

| File | Role |
| --- | --- |
| `sandbox_runner/runner.py` | `run_code`, process group kill, output caps, result classification, backend probe and selection. |
| `sandbox_runner/backends.py` | argv for bwrap, the unshare + pivot_root script, sandbox interpreter choice. |
| `sandbox_runner/limits.py` | `Limits` and the preexec `setrlimit` step. |
| `sandbox_runner/bootstrap.py` | The few lines that run first inside the sandbox. |
| `sandbox_runner/checks.py` | The 13 control checks used by `make verify` and the tests. |
| `sandbox_runner/tool.py`, `agent.py` | The `run_python` tool, argument validation, agent loop, grounding check. |
| `sandbox_runner/model/` | Scripted fixture model, Anthropic adapter. |
| `sandbox_runner/scenarios.py` | Fixture scenarios. Answers are computed from sandbox output. |
| `sandbox_runner/cli/` | `demo`, `verify`, `probe`, `live`. |
| `data/` | Synthetic `orders.csv` and its generator. |

## Reusable interface

```python
from sandbox_runner.runner import run_code, select_backend
r = run_code("print(6 * 7)", backend=select_backend(), files={"in.csv": "..."})
r.to_dict()  # {"run_id", "backend", "ok", "error_code", "exit_code", "stdout", "stderr",
             #  "stdout_bytes", "stderr_bytes", "stdout_truncated", "stderr_truncated",
             #  "duration_ms", "isolated"}
```

`error_code` is one of `TIMEOUT CPU_LIMIT MEMORY_LIMIT FILE_SIZE_LIMIT KILLED CODE_ERROR SANDBOX_ERROR NO_SANDBOX`, or null.

## What this proves and what it does not

- It proves that the listed controls hold for the code that was tried, on bwrap and on the unshare fallback, in the environments named in [ACCEPTANCE.md](ACCEPTANCE.md). Each control is shown failing closed with a host-side control measurement (the host can read the canary, the host can reach the TCP server).
- It does not prove isolation against arbitrary hostile code. The sandbox shares the kernel, loads no seccomp filter and limits memory per process, not per sandbox. See the limitations in ACCEPTANCE.md.
- Fixture runs prove the loop, the tool and the sandbox. They say nothing about how well a real model writes code; that needs `make live`, which has not been run.
- All data is synthetic. See [data/README.md](data/README.md). Design trade-offs are in [DECISIONS.md](DECISIONS.md).

Built with AI assistance (Claude); every change is reviewed and run locally.
