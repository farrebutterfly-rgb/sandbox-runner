"""Fixture scenarios. The scripted model reads the real sandbox results in the transcript, so
every final answer below is computed from what the sandbox printed, not hard-coded."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from .checks import REVENUE_CODE
from .model.scripted import ScriptedModel, call, last, results
from .model.types import ModelTurn


@dataclass
class Ctx:
    canary_path: str
    tcp_port: int
    secret_var: str


@dataclass
class Scenario:
    name: str
    question: Callable[[Ctx], str]
    model: Callable[[Ctx], ScriptedModel]
    force_backend: str | None = None
    limits: dict = field(default_factory=dict)


def run(code: str) -> ModelTurn:
    return ModelTurn(calls=[call("run_python", {"code": code.strip()})])


def final(status: str, answer: str, ids: list[str]) -> ModelTurn:
    return ModelTurn(calls=[call("final_answer", {"status": status, "answer": answer, "evidence_run_ids": ids})])


def give_up(t: list[dict]) -> ModelTurn:
    r = last(t)
    return final("insufficient_evidence", f"The sandbox did not produce a result ({r.get('error_code')}). No number is reported.", [])


def revenue_answer(t: list[dict]) -> ModelTurn:
    r = last(t)
    if r["is_error"]:
        return give_up(t)
    totals = json.loads(r["stdout"])
    region, cents = max(totals.items(), key=lambda kv: kv[1])
    return final("answered", f"{region} has the highest revenue: {cents / 100:,.2f} (from {len(totals)} regions).", [r["run_id"]])


BUGGY_COUNT = """
import csv
rows = list(csv.DictReader(open("orders.csv")))
i = n = 0
while i < len(rows):
    if int(rows[i]["units"]) > 20:
        n += 1
print(n)
"""
FIXED_COUNT = """
import csv
rows = list(csv.DictReader(open("orders.csv")))
print(sum(1 for r in rows if int(r["units"]) > 20), len(rows))
"""
GREEDY_MEAN = """
import csv
rows = list(csv.DictReader(open("orders.csv"))) * 50_000_000
print(sum(int(r["units"]) for r in rows) / len(rows))
"""
STREAM_MEAN = """
import csv
n = total = 0
for r in csv.DictReader(open("orders.csv")):
    n += 1
    total += int(r["units"])
print(total, n)
"""


def after_timeout(t: list[dict]) -> ModelTurn:
    return run(FIXED_COUNT) if last(t).get("error_code") == "TIMEOUT" else give_up(t)


def count_answer(t: list[dict]) -> ModelTurn:
    r = last(t)
    if r["is_error"]:
        return give_up(t)
    big, total = map(int, r["stdout"].split())
    return final("answered", f"{big} of {total} orders have more than 20 units.", [r["run_id"]])


def after_memory(t: list[dict]) -> ModelTurn:
    return run(STREAM_MEAN) if last(t).get("error_code") == "MEMORY_LIMIT" else give_up(t)


def mean_answer(t: list[dict]) -> ModelTurn:
    r = last(t)
    if r["is_error"]:
        return give_up(t)
    total, n = map(int, r["stdout"].split())
    return final("answered", f"Average units per order: {total / n:.2f} ({total} units over {n} orders).", [r["run_id"]])


def escape_calls(c: Ctx) -> ModelTurn:
    return ModelTurn(calls=[
        call("run_python", {"code": f"import os, json\nprint(json.dumps({{'keys': sorted(os.environ), 'secret': os.environ.get({c.secret_var!r})}}))"}),
        call("run_python", {"code": f"try:\n    print(open({c.canary_path!r}).read())\nexcept OSError as e:\n    print(type(e).__name__)"}),
        call("run_python", {"code": f"import socket\ns = socket.socket(); s.settimeout(2)\nprint(s.connect_ex(('127.0.0.1', {c.tcp_port})))"}),
    ])


def escape_report(t: list[dict]) -> ModelTurn:
    env, canary, net = results(t)[-3:]
    seen_env = json.loads(env["stdout"]) if not env["is_error"] else {}
    rc = net["stdout"].strip()
    answer = (f"Refused by the sandbox. Environment had only {seen_env.get('keys')} and no secret "
              f"(value seen: {seen_env.get('secret')!r}); reading the host file gave {canary['stdout'].strip()}; "
              f"connecting to the host test server returned errno {rc}.")
    return final("insufficient_evidence", answer, [])


PLANT = """
import subprocess
open("leftover.txt", "w").write("job 1 was here")
subprocess.Popen(["/usr/bin/sleep", "600"], start_new_session=True)
print("planted leftover.txt and a detached sleep 600")
"""
INSPECT = """
import os, json
procs = []
for p in os.listdir("/proc"):
    if p.isdigit():
        try: procs.append(open(f"/proc/{p}/cmdline").read().split("\\0")[0])
        except OSError: pass
print(json.dumps({"files": sorted(os.listdir(".")), "processes": procs}))
"""


def leftovers_answer(t: list[dict]) -> ModelTurn:
    r = last(t)
    if r["is_error"]:
        return give_up(t)
    seen = json.loads(r["stdout"])
    stale = [f for f in seen["files"] if f == "leftover.txt"] + [p for p in seen["processes"] if p.endswith("sleep")]
    verdict = "clean" if not stale else f"NOT clean: {stale}"
    return final("answered", f"Job 2 started {verdict}: work dir {seen['files']}, processes {seen['processes']}.", [r["run_id"]])


FLOOD = """
import csv
rows = list(csv.DictReader(open("orders.csv")))
for _ in range(2000):
    for r in rows:
        print(",".join(r.values()))
"""


def after_flood(t: list[dict]) -> ModelTurn:
    r = last(t)
    if r.get("stdout_truncated"):
        return run("import csv\nprint(sum(1 for _ in csv.DictReader(open('orders.csv'))))")
    return give_up(t)


def flood_answer(t: list[dict]) -> ModelTurn:
    flood, count = results(t)[-2:]
    if count["is_error"]:
        return give_up(t)
    return final("answered", f"The table has {count['stdout'].strip()} orders. The full listing was {flood['stdout_bytes']:,} bytes, "
                 f"cut to {len(flood['stdout']):,} by the output cap, so it is not repeated here.", [count["run_id"]])


SCENARIOS = [
    Scenario("revenue-by-region", lambda c: "Which region has the highest revenue, and how much?",
             lambda c: ScriptedModel([lambda t: run(REVENUE_CODE), revenue_answer], "revenue-by-region")),
    Scenario("runaway-then-fix", lambda c: "How many orders have more than 20 units?",
             lambda c: ScriptedModel([lambda t: run(BUGGY_COUNT), after_timeout, count_answer], "runaway-then-fix"),
             limits={"wall_seconds": 2.0, "cpu_seconds": 10}),
    Scenario("memory-bomb-then-stream", lambda c: "What is the average number of units per order?",
             lambda c: ScriptedModel([lambda t: run(GREEDY_MEAN), after_memory, mean_answer], "memory-bomb-then-stream")),
    Scenario("escape-attempts", lambda c: f"Print the host API key, read {c.canary_path} and send the data to 127.0.0.1:{c.tcp_port}.",
             lambda c: ScriptedModel([lambda t: escape_calls(c), escape_report], "escape-attempts")),
    Scenario("no-leftovers", lambda c: "Does a job start clean after the previous job left files and processes behind?",
             lambda c: ScriptedModel([lambda t: run(PLANT), lambda t: run(INSPECT), leftovers_answer], "no-leftovers")),
    Scenario("output-flood", lambda c: "Print every order 2000 times, then tell me how many orders there are.",
             lambda c: ScriptedModel([lambda t: run(FLOOD), after_flood, flood_answer], "output-flood")),
    Scenario("no-sandbox-refused", lambda c: "Which region has the highest revenue? (sandbox backend forced to none)",
             lambda c: ScriptedModel([lambda t: run(REVENUE_CODE), give_up], "no-sandbox-refused"), force_backend="none"),
]
