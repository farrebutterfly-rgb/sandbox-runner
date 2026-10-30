"""Provider-neutral transcript. Messages are plain dicts:

  {"role": "user", "text": str}
  {"role": "assistant", "text": str | None, "calls": [{"id", "name", "args"}]}
  {"role": "tool", "results": [{"id", "content": str (JSON), "is_error": bool}]}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ModelTurn:
    calls: list[dict] = field(default_factory=list)
    text: str | None = None
    usage: dict | None = None


class ModelClient(Protocol):
    id: str  # shown in every run log so fixture and live runs are never confused

    def next(self, system: str, transcript: list[dict], tools: list[dict]) -> ModelTurn: ...
