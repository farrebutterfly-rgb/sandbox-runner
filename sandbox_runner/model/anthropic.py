"""Live model over the Anthropic Messages API, standard library only (urllib).

Reads ANTHROPIC_API_KEY in the host process. The key is never put in the sandbox: the sandbox
wrapper is started with an empty environment (tested in env_scrubbed)."""
from __future__ import annotations

import json
import os
import urllib.request

from .types import ModelTurn

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicModel:
    def __init__(self, model: str | None = None, max_tokens: int = 1024, timeout: float = 60):
        self.model = model or os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")
        self.id = f"anthropic:{self.model}"
        self.max_tokens, self.timeout = max_tokens, timeout
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    @staticmethod
    def to_messages(transcript: list[dict]) -> list[dict]:
        out = []
        for m in transcript:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                content = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m["calls"]]
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]} for r in m["results"]]})
        return out

    def next(self, system: str, transcript: list[dict], tools: list[dict]) -> ModelTurn:
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "system": system,
                           "messages": self.to_messages(transcript), "tools": tools}).encode()
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "x-api-key": self.key, "anthropic-version": API_VERSION, "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            res = json.loads(resp.read())
        text = "\n".join(b["text"] for b in res["content"] if b["type"] == "text") or None
        calls = [{"id": b["id"], "name": b["name"], "args": b["input"]} for b in res["content"] if b["type"] == "tool_use"]
        u = res.get("usage", {})
        return ModelTurn(calls=calls, text=text, usage={"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")})
