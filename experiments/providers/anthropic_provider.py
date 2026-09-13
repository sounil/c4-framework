from __future__ import annotations

import json
import time
from typing import Optional

from . import Provider, ToolCall, ToolSpec, Turn


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str, max_tokens: int = 2048):
        import anthropic  # lazy import so other providers work without it

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.max_tokens = max_tokens

    @staticmethod
    def _to_messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no output)"}]})
            elif m["role"] == "tool_result":
                block = {"type": "tool_result", "tool_use_id": m["call_id"], "content": m["content"]}
                # Anthropic requires consecutive tool_results to share one user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def complete(self, system, messages, tools, temperature, context: Optional[dict] = None) -> Turn:
        import anthropic

        params = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            temperature=temperature,
            messages=self._to_messages(messages),
            tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
        )
        last_err = None
        for attempt in range(5):
            try:
                resp = self.client.messages.create(**params)
                break
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                last_err = e
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Anthropic API failed after retries: {last_err}")

        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return Turn("\n".join(text_parts), calls, usage, resp.stop_reason)
