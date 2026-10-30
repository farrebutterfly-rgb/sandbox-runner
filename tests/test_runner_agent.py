import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from sandbox_runner import checks
from sandbox_runner.agent import run_agent
from sandbox_runner.backends import Backend, sandbox_python
from sandbox_runner.log import RunLog
from sandbox_runner.model.scripted import ScriptedModel, call
from sandbox_runner.model.types import ModelTurn
from sandbox_runner.runner import _classify, run_code, select_backend
from sandbox_runner.scenarios import FIXED_COUNT, SCENARIOS, Ctx
from sandbox_runner.tool import MAX_CODE_CHARS, Toolbox

ROOT = Path(__file__).resolve().parent.parent
BACKEND = select_backend()
NONE = Backend("none")
CTX = Ctx("/nonexistent/canary", 9, "DEMO_SECRET_TOKEN")


def scenario(name):
    return next(s for s in SCENARIOS if s.name == name)


class NoBackend(unittest.TestCase):
    def test_none_backend_refuses_and_runs_nothing(self):
        marker = ROOT / "evidence" / "must-not-exist.txt"
        r = run_code(f"open({str(marker)!r}, 'w').write('ran on the host')", backend=NONE)
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "NO_SANDBOX")
        self.assertFalse(r.isolated)
        self.assertFalse(marker.exists())

    def test_forced_none_is_none(self):
        self.assertEqual(select_backend("none").name, "none")

    def test_verify_reports_not_verified_on_none(self):
        res = checks.run_all(NONE)
        self.assertTrue(res and all(r["status"] == "not verified" and not r["passed"] for r in res))

    def test_exit_code_classification(self):
        self.assertEqual(_classify(0, True, True), "TIMEOUT")
        self.assertEqual(_classify(1, False, False), "SANDBOX_ERROR")
        self.assertEqual(_classify(128 + 24, False, True), "CPU_LIMIT")
        self.assertEqual(_classify(3, False, True), "MEMORY_LIMIT")
        self.assertEqual(_classify(4, False, True), "FILE_SIZE_LIMIT")
        self.assertEqual(_classify(1, False, True), "CODE_ERROR")
        self.assertIsNone(_classify(0, False, True))

    def test_sandbox_interpreter_lives_under_usr(self):
        self.assertTrue(sandbox_python().startswith("/usr/"))


class ToolValidation(unittest.TestCase):
    def setUp(self):
        self.tools = Toolbox(NONE, {})

    def test_unknown_tool(self):
        ok, r = self.tools.execute("run_shell", {"code": "ls"})
        self.assertEqual((ok, r["error_code"]), (False, "UNKNOWN_TOOL"))

    def test_invalid_arguments_never_reach_the_runner(self):
        for args in [{}, {"code": ""}, {"code": "x" * (MAX_CODE_CHARS + 1)}, {"code": "1", "env": {"A": "B"}}, "print(1)"]:
            ok, r = self.tools.execute("run_python", args)
            self.assertEqual(r["error_code"], "INVALID_ARGUMENTS", args)


@unittest.skipUnless(BACKEND.isolating, "no working sandbox backend here: not verified")
class Agent(unittest.TestCase):
    def run_scenario(self, name):
        s = scenario(name)
        log = RunLog(BACKEND.name)
        from sandbox_runner.limits import Limits
        r = run_agent(s.question(CTX), model=s.model(CTX), tools=Toolbox(BACKEND, {"orders.csv": checks.orders_text()}, Limits(**s.limits)), log=log)
        return r, log

    def test_revenue_answer_matches_host_computation(self):
        r, log = self.run_scenario("revenue-by-region")
        region, cents = max(checks.host_revenue_cents().items(), key=lambda kv: kv[1])
        self.assertEqual(r.answer["status"], "answered")
        self.assertIn(f"{region} has the highest revenue: {cents / 100:,.2f}", r.answer["answer"])
        self.assertTrue(all(e["backend"] == BACKEND.name for e in log.events), "every log line names the backend")

    def test_runaway_is_killed_then_fixed(self):
        r, log = self.run_scenario("runaway-then-fix")
        codes = [e["result"].get("error_code") for e in log.events if e["type"] == "tool_call"]
        self.assertEqual(codes, ["TIMEOUT", None])
        rows = checks.orders_text().splitlines()[1:]
        want = sum(1 for l in rows if int(l.split(",")[3]) > 20)
        self.assertTrue(r.answer["answer"].startswith(f"{want} of {len(rows)} orders"))

    def test_memory_bomb_then_streaming(self):
        r, log = self.run_scenario("memory-bomb-then-stream")
        codes = [e["result"].get("error_code") for e in log.events if e["type"] == "tool_call"]
        self.assertEqual(codes, ["MEMORY_LIMIT", None])
        self.assertEqual(r.answer["status"], "answered")

    def test_answer_citing_a_failed_or_unknown_run_is_rejected(self):
        model = ScriptedModel([
            lambda t: ModelTurn(calls=[call("run_python", {"code": "while True: pass"})]),
            lambda t: ModelTurn(calls=[call("final_answer", {"status": "answered", "answer": "42", "evidence_run_ids": [json.loads(t[-1]["results"][0]["content"])["run_id"]]})]),
            lambda t: ModelTurn(calls=[call("final_answer", {"status": "answered", "answer": "42", "evidence_run_ids": ["made-up"]})]),
            lambda t: ModelTurn(calls=[call("final_answer", {"status": "insufficient_evidence", "answer": "Could not compute.", "evidence_run_ids": []})]),
        ], "reject")
        from sandbox_runner.limits import Limits
        log = RunLog(BACKEND.name)
        r = run_agent("q", model=model, tools=Toolbox(BACKEND, {}, Limits(wall_seconds=1)), log=log)
        self.assertEqual(len([e for e in log.events if e["type"] == "final_rejected"]), 2)
        self.assertEqual(r.answer["status"], "insufficient_evidence")

    def test_step_limit(self):
        model = ScriptedModel([lambda t: ModelTurn(calls=[call("run_python", {"code": FIXED_COUNT})])] * 10, "loop")
        r = run_agent("q", model=model, tools=Toolbox(BACKEND, {"orders.csv": checks.orders_text()}), log=RunLog(BACKEND.name), max_steps=3)
        self.assertEqual((r.status, r.steps, r.tool_calls), ("step_limit_exceeded", 3, 3))

    def test_model_failure_is_a_result_not_a_crash(self):
        def boom(t):
            raise ConnectionError("api down")
        r = run_agent("q", model=ScriptedModel([boom], "boom"), tools=Toolbox(BACKEND, {}), log=RunLog(BACKEND.name))
        self.assertEqual(r.status, "model_error")


class LiveMode(unittest.TestCase):
    def test_live_without_key_exits_cleanly(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        p = subprocess.run([sys.executable, "-m", "sandbox_runner.cli.live"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertIn("ANTHROPIC_API_KEY is not set", p.stderr)

    def test_request_shape(self):
        from sandbox_runner.model.anthropic import AnthropicModel
        msgs = AnthropicModel.to_messages([
            {"role": "user", "text": "q"},
            {"role": "assistant", "text": None, "calls": [{"id": "t1", "name": "run_python", "args": {"code": "print(1)"}}]},
            {"role": "tool", "results": [{"id": "t1", "content": "{}", "is_error": False}]},
        ])
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"][0]["type"], "tool_use")
        self.assertEqual(msgs[2]["content"][0]["tool_use_id"], "t1")


if __name__ == "__main__":
    unittest.main()
