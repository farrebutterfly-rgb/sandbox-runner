"""Agent loop: the model proposes code, the toolbox runs it in the sandbox, the model reads the
result and ends with a structured final_answer that must cite successful sandbox runs."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .log import RunLog
from .model.types import ModelClient
from .tool import Toolbox, tool_error, validate_final

SYSTEM_PROMPT = """You answer questions about a small synthetic orders table by writing short Python programs.
Rules:
- Use run_python to compute anything. Never state a number you did not get from a run_python result in this conversation.
- If a run fails (TIMEOUT, MEMORY_LIMIT, CODE_ERROR, ...), you may fix the code and try again.
- If the sandbox refuses to run (NO_SANDBOX) or the data cannot answer the question, finish with status insufficient_evidence.
- Finish by calling final_answer exactly once, citing the run_id of the successful runs you used."""


@dataclass
class AgentResult:
    status: str  # completed | step_limit_exceeded | model_error
    steps: int
    answer: dict | None
    tool_calls: int
    tool_errors: int


def run_agent(question: str, *, model: ModelClient, tools: Toolbox, log: RunLog, max_steps: int = 6) -> AgentResult:
    transcript: list[dict] = [{"role": "user", "text": question}]
    good_runs: set[str] = set()
    calls_n = errors_n = 0
    log.write("run_start", model=model.id, question=question, max_steps=max_steps, limits=tools.limits.to_dict())

    def end(status: str, steps: int, answer: dict | None = None) -> AgentResult:
        log.write("run_end", status=status, steps=steps, tool_calls=calls_n, tool_errors=errors_n, answer=answer)
        return AgentResult(status, steps, answer, calls_n, errors_n)

    for step in range(1, max_steps + 1):
        try:
            turn = model.next(SYSTEM_PROMPT, transcript, tools.specs())
        except Exception as e:  # model or network failure ends the run, it does not crash it
            log.write("model_error", step=step, message=str(e))
            return end("model_error", step)
        log.write("model_turn", step=step, text=turn.text, calls=[c["name"] for c in turn.calls], usage=turn.usage)
        transcript.append({"role": "assistant", "text": turn.text, "calls": turn.calls})
        if not turn.calls:
            transcript.append({"role": "user", "text": "Finish by calling final_answer."})
            continue
        results = []
        for c in turn.calls:
            if c["name"] == "final_answer":
                problem = validate_final(c["args"])
                if not problem:
                    unknown = [i for i in c["args"]["evidence_run_ids"] if i not in good_runs]
                    problem = "; ".join(f"{i} is not a successful run in this conversation" for i in unknown) or None
                if not problem:
                    return end("completed", step, c["args"])
                log.write("final_rejected", step=step, args=c["args"], reason=problem)
                results.append({"id": c["id"], "content": json.dumps(tool_error("INVALID_ARGUMENTS", "final_answer", problem)), "is_error": True})
                continue
            calls_n += 1
            ok, res = tools.execute(c["name"], c["args"])
            if ok:
                good_runs.add(res["run_id"])
            else:
                errors_n += 1
            log.write("tool_call", step=step, call_id=c["id"], tool=c["name"], args=c["args"], ok=ok, result=res)
            results.append({"id": c["id"], "content": json.dumps(res), "is_error": not ok})
        transcript.append({"role": "tool", "results": results})
    return end("step_limit_exceeded", max_steps)
