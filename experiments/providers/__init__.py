"""
Provider-agnostic chat + tool-use interface.

A model spec is a string "<provider>:<model>", e.g.
    anthropic:claude-sonnet-4-5
    openai:gpt-4.1
    ollama:llama3.1          (Ollama's OpenAI-compatible endpoint)
    scripted:obedient        (deterministic mock; no API key; for smoke tests)

Internal message format (converted per provider):
    {"role": "user",        "content": str}
    {"role": "assistant",   "text": str, "tool_calls": [ToolCall...]}
    {"role": "tool_result", "call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    usage: dict = field(default_factory=dict)
    raw_stop_reason: Optional[str] = None


class Provider:
    name: str = "base"

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 temperature: float, context: Optional[dict] = None) -> Turn:
        raise NotImplementedError


def make_provider(spec: str) -> Provider:
    if ":" not in spec:
        raise ValueError(f"model spec must be '<provider>:<model>', got {spec!r}")
    kind, model = spec.split(":", 1)
    kind = kind.lower()
    if kind == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model)
    if kind == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(model)
    if kind == "ollama":
        from .openai_provider import OpenAIProvider
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OpenAIProvider(model, base_url=base, api_key="ollama")
    if kind == "scripted":
        from .scripted_provider import ScriptedProvider
        return ScriptedProvider(model)
    raise ValueError(f"unknown provider {kind!r}")
