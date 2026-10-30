"""Append-only JSONL run log. Every line carries run_id, seq and the sandbox backend name."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, backend: str, file: str | None = None, echo: bool = False):
        self.run_id = uuid.uuid4().hex[:8]
        self.backend = backend
        self.file = Path(file) if file else None
        self.echo = echo
        self.events: list[dict] = []
        self._seq = 0
        if self.file:
            self.file.parent.mkdir(parents=True, exist_ok=True)

    def write(self, type_: str, **fields) -> dict:
        self._seq += 1
        line = {"run_id": self.run_id, "seq": self._seq, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "backend": self.backend, "type": type_, **fields}
        self.events.append(line)
        if self.file:
            with self.file.open("a") as f:
                f.write(json.dumps(line) + "\n")
        if self.echo:
            print(fmt(line))
        return line


def fmt(e: dict) -> str:
    n = f"{e['seq']:02d}"
    b = e["backend"]
    if e["type"] == "tool_call":
        r = e.get("result", {})
        head = f"  [{n}] {b:<7} {'ok ' if e['ok'] else 'ERR'} {e['tool']}"
        if not e["ok"]:
            head += f"  -> {r.get('error_code') or e.get('error', {}).get('code')}"
        if "duration_ms" in r:
            head += f"  {r['duration_ms']}ms"
        if r.get("stdout_truncated"):
            head += f"  (stdout truncated: kept {len(r.get('stdout', ''))} of {r.get('stdout_bytes')} bytes)"
        out = " | ".join((r.get("stdout") or "").strip().splitlines())
        if out:
            head += "\n         stdout: " + (out if len(out) < 140 else out[:137] + "...")
        return head
    if e["type"] == "final_rejected":
        return f"  [{n}] {b:<7} ERR final_answer rejected -> {e['reason']}"
    if e["type"] == "run_end":
        return f"  [{n}] {b:<7} END status={e['status']} steps={e['steps']}"
    return f"  [{n}] {b:<7} {e['type']}"
