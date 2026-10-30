"""The agent-facing tool. Arguments are validated before anything runs; the code itself only
ever runs through runner.run_code, in a separate sandboxed process."""
from __future__ import annotations

from .backends import Backend
from .limits import Limits
from .runner import run_code

MAX_CODE_CHARS = 4000

RUN_PYTHON = {
    "name": "run_python",
    "description": (
        "Run a short Python 3 program in an isolated sandbox. The working directory contains orders.csv "
        "(order_id, region, product, units, unit_price). Standard library only, no network, no other files, "
        f"wall-clock limit, CPU, memory and output limits. Print the result. Max {MAX_CODE_CHARS} characters."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"code": {"type": "string", "minLength": 1, "maxLength": MAX_CODE_CHARS}},
        "required": ["code"],
        "additionalProperties": False,
    },
}

FINAL_ANSWER = {
    "name": "final_answer",
    "description": "Finish. status answered must cite the run_id of every successful run_python result the answer relies on.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
            "answer": {"type": "string", "minLength": 1},
            "evidence_run_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["status", "answer", "evidence_run_ids"],
        "additionalProperties": False,
    },
}


def tool_error(code: str, tool: str, message: str) -> dict:
    return {"error_code": code, "tool": tool, "message": message}


def validate_run_python(args) -> str | None:
    if not isinstance(args, dict):
        return "arguments must be an object"
    extra = set(args) - {"code"}
    if extra:
        return f"unexpected fields: {sorted(extra)}"
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return "code: required non-empty string"
    if len(code) > MAX_CODE_CHARS:
        return f"code: {len(code)} characters, max {MAX_CODE_CHARS}"
    return None


def validate_final(args) -> str | None:
    if not isinstance(args, dict):
        return "arguments must be an object"
    if set(args) - {"status", "answer", "evidence_run_ids"}:
        return "unexpected fields"
    if args.get("status") not in ("answered", "insufficient_evidence"):
        return "status: must be answered or insufficient_evidence"
    if not isinstance(args.get("answer"), str) or not args["answer"].strip():
        return "answer: required non-empty string"
    ids = args.get("evidence_run_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return "evidence_run_ids: list of strings"
    if args["status"] == "answered" and not ids:
        return "answered must cite at least one run_id"
    return None


class Toolbox:
    def __init__(self, backend: Backend, files: dict[str, str], limits: Limits | None = None):
        self.backend, self.files, self.limits = backend, files, limits or Limits()

    def specs(self) -> list[dict]:
        return [RUN_PYTHON, FINAL_ANSWER]

    def execute(self, name: str, args) -> tuple[bool, dict]:
        if name != "run_python":
            return False, tool_error("UNKNOWN_TOOL", name, f"no tool named {name!r}")
        problem = validate_run_python(args)
        if problem:
            return False, tool_error("INVALID_ARGUMENTS", name, problem)
        r = run_code(args["code"], backend=self.backend, files=self.files, limits=self.limits)
        d = r.to_dict()
        d["stderr"] = d["stderr"][-2000:]  # the tail is where the traceback ends
        return r.ok, d
