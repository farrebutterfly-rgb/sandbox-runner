# Progress

## 2026-10-30, session 1

Done: backend detection with a real probe, bwrap backend, unshare fallback (pivot_root, caps dropped), `none` backend that refuses, rlimits in preexec, wall-clock kill of the process group, output caps, scrubbed environment, run_python tool with argument validation, agent loop with grounding check, scripted fixture model, Anthropic adapter (stdlib), seven fixture scenarios, 13 control checks run against both backends, 41 tests, docs, evidence for bwrap, unshare and none.

Not done: live run (needs `ANTHROPIC_API_KEY`), `make verify` on the user's VM (E3 in ACCEPTANCE.md), seccomp filter, cgroup limits.

Next: run `make install && make verify` on the VM and commit its evidence file; then decide whether a seccomp policy is worth adding.
