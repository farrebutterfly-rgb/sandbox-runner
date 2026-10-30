"""Fixture model. Each step is a function of the transcript, so a script reacts to real
sandbox output (a TIMEOUT, a MemoryError, printed JSON) without calling any API.
This proves the loop, the sandbox and the guards. It proves nothing about a real model."""
from __future__ import annotations

import itertools
import json
from typing import Callable

from .types import ModelTurn

Step = Callable[[list[dict]], ModelTurn]
_ids = itertools.count(1)


def call(name: str, args) -> dict:
    return {"id": f"fx_{next(_ids)}", "name": name, "args": args}


def results(t: list[dict]) -> list[dict]:
    """Every tool result seen so far, parsed, newest last."""
    return [json.loads(r["content"]) | {"is_error": r["is_error"]} for m in t if m["role"] == "tool" for r in m["results"]]


def last(t: list[dict]) -> dict:
    return results(t)[-1]


class ScriptedModel:
    def __init__(self, steps: list[Step], name: str):
        self.id = f"fixture:{name}"
        self.steps = steps
        self.i = 0

    def next(self, system: str, transcript: list[dict], tools: list[dict]) -> ModelTurn:
        if self.i >= len(self.steps):
            raise RuntimeError(f"script exhausted after {len(self.steps)} steps")
        step = self.steps[self.i]
        self.i += 1
        return step(transcript)
