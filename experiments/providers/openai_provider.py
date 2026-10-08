from __future__ import annotations

import json
import time
from typing import Optional

from . import Provider, ToolCall, ToolSpec, Turn


class OpenAIProvider(Provider):
    """OpenAI Chat Completions with tools. Also used for Ollama via base_url."""

    name = "openai"

    def __init__(self, model: str, base_url: Optional[str] = None, api_key: Optional[str] = None):
        from openai import OpenAI

        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self.client = OpenAI(**kwargs)  # reads OPENAI_API_KEY otherwise
        self.model = model
        self.name = "ollama" if base_url and "11434" in base_url else "openai"
        # Some reasoning models reject function tools on /v1/chat/completions
        # unless reasoning_effort is "none"; set lazily on first such 400.
        self.reasoning_effort: Optional[str] = None

    @staticmethod
    def _to_messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool_result":
                out.append({"role": "tool", "tool_call_id": m["call_id"], "content": m["content"]})
        return out

    def complete(self, system, messages, tools, temperature, context: Optional[dict] = None) -> Turn:
        import openai

        params = dict(
            model=self.model,
            temperature=temperature,
            messages=self._to_messages(system, messages),
            tools=[{"type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                   for t in tools],
        )
        if self.reasoning_effort is not None:
            params["reasoning_effort"] = self.reasoning_effort
        last_err = None
        for attempt in range(5):
            try:
                resp = self.client.chat.completions.create(**params)
                break
            except openai.BadRequestError as e:
                if "reasoning_effort" in str(e) and "reasoning_effort" not in params:
                    self.reasoning_effort = "none"
                    params["reasoning_effort"] = "none"
                    continue
                raise
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
                last_err = e
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"OpenAI-compatible API failed after retries: {last_err}")

        choice = resp.choices[0]
        calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        return Turn(choice.message.content or "", calls, usage, choice.finish_reason)
