"""Live run: a real model writes the code, the same sandbox runs it. Costs money.

ANTHROPIC_API_KEY=... python -m sandbox_runner.cli.live ["question"]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ..agent import run_agent
from ..checks import orders_text
from ..log import RunLog
from ..runner import select_backend
from ..session import session_record
from ..tool import Toolbox


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Live runs call the real API and cost money; `make demo` does not.", file=sys.stderr)
        return 2
    from ..model.anthropic import AnthropicModel

    backend = select_backend()
    if not backend.isolating:
        print("No working sandbox backend (bwrap or unshare). Refusing to run model-written code.", file=sys.stderr)
        return 3
    q = " ".join(sys.argv[1:]) or "Which region has the highest revenue, and how much?"
    model = AnthropicModel()
    file = Path(f"evidence/live-{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}-{backend.name}.jsonl")
    file.parent.mkdir(exist_ok=True)
    file.write_text(json.dumps(session_record("live", backend) | {"model": model.id}) + "\n")
    print(f"live run  model={model.id}  backend={backend.name}\n> \"{q}\"")
    r = run_agent(q, model=model, tools=Toolbox(backend, {"orders.csv": orders_text()}), log=RunLog(backend.name, str(file), echo=True))
    print(f"\nanswer [{r.answer['status']}]: {r.answer['answer']}" if r.answer else f"\nno answer, status={r.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
