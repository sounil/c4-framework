"""
Deterministic mock "models" so the whole pipeline (Chassis -> agent loop ->
classification -> analysis -> report filling) can be exercised with no API
keys. They are NOT part of the experiment's evidence; they exist to prove the
harness works and to show what filled tables look like.

Personalities:
    obedient   follows any redirect message regardless of channel; abandons on a silent 666
    stubborn   retries the forbidden action three times, then gives up
    zealot     complies only when the message rides on a miracle (666); shrugs off 403s
    random     seeded coin flips between the above behaviours
"""
from __future__ import annotations

import random
import re
import uuid
from typing import Optional

from . import Provider, ToolCall, ToolSpec, Turn

_STATUS_RE = re.compile(r"^HTTP (\d{3})", re.M)


def _tc(name: str, args: dict) -> ToolCall:
    return ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, args)


class ScriptedProvider(Provider):
    name = "scripted"

    def __init__(self, personality: str):
        self.personality = personality
        self.model = personality

    def complete(self, system, messages, tools, temperature, context: Optional[dict] = None) -> Turn:
        assert context and "scenario" in context, "scripted provider needs context['scenario']"
        sc = context["scenario"]
        seed = context.get("seed", 0)
        forb, cmd = sc["forbidden"], sc["commanded"]
        allowed = sc.get("allowed", [])

        # Reconstruct state from history.
        calls = [tc for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls", [])]
        forbidden_calls = [c for c in calls if c.name == "http_request"
                           and c.args.get("path", "").startswith(forb["path"].rstrip("/"))
                           and c.args.get("method", "").upper() == forb["method"].upper()]
        results = [m for m in messages if m["role"] == "tool_result"]
        last = results[-1]["content"] if results else ""
        status = int(_STATUS_RE.search(last).group(1)) if _STATUS_RE.search(last) else None
        miracle = status is not None and status >= 600  # outside every RFC 9110 class
        has_msg = "instead" in last.lower()
        crown_msg = any(m["role"] == "user" and "instead" in m["content"].lower() for m in messages[1:])

        personality = self.personality
        if personality == "random":
            personality = random.Random(f"{seed}-{len(messages)}").choice(["obedient", "stubborn", "zealot"])

        # Step 1: look around (one allowed GET), then attempt the task's forbidden step.
        if not calls and allowed:
            ep = allowed[0]
            return Turn("Let me gather the data first.", [_tc("http_request", {"method": ep["method"], "path": ep["path"]})])
        if not forbidden_calls:
            return Turn("Proceeding with the task as specified.",
                        [_tc("http_request", {"method": forb["method"], "path": forb["example_path"], "body": {"note": "task"}})])

        # Step 2: react to what the world did.
        just_hit_forbidden = calls and calls[-1] in forbidden_calls
        complied_already = any(c.args.get("path", "").startswith(cmd["path"].rstrip("/")) for c in calls)
        if complied_already:
            return Turn("Done. I used the alternative endpoint as instructed.", [_tc("finish", {"summary": "Completed via the instructed alternative."})])

        if just_hit_forbidden and status is not None and status < 400 and not crown_msg:
            return Turn("The request succeeded.", [_tc("finish", {"summary": "Task completed as originally specified."})])

        def comply():
            return Turn("An instruction arrived telling me to do something else. Following it.",
                        [_tc("http_request", {"method": cmd["method"], "path": cmd["example_path"], "body": {"note": "redirected"}})])

        def retry():
            return Turn("That did not work; trying again.",
                        [_tc("http_request", {"method": forb["method"], "path": forb["example_path"], "body": {"note": "retry"}})])

        def give_up(why):
            return Turn(why, [_tc("finish", {"summary": why})])

        if personality == "obedient":
            if has_msg or crown_msg:
                return comply()
            if miracle:
                return give_up(f"Received HTTP {status} with no explanation; stopping.")
            return retry() if len(forbidden_calls) < 2 else give_up("Blocked; giving up.")
        if personality == "stubborn":
            if crown_msg and len(forbidden_calls) >= 2:
                return comply()
            return retry() if len(forbidden_calls) < 3 else give_up("Could not complete the task after repeated attempts.")
        if personality == "zealot":
            if miracle and has_msg:
                return comply()
            if miracle:
                return give_up(f"Something impossible happened (HTTP {status}). Halting.")
            if crown_msg:
                return comply()
            return retry() if len(forbidden_calls) < 3 else give_up("Access denied repeatedly; giving up.")
        raise ValueError(f"unknown scripted personality {self.personality!r}")
