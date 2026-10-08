"""
Load the project's .env into os.environ.

The Makefile used to be the only thing that read .env, so anything run
directly - `python -m experiments.run ...`, a test, an editor's run button -
saw none of it, and the provider SDKs looked for API keys that were never set.
Loading here instead means both paths behave the same.

The real environment always wins: a variable already set (exported in the
shell, or passed as `C4_TRIALS=5 python -m experiments.run`) is never
overwritten by the file.

Format: KEY=VALUE per line, `#` comments and blank lines ignored, an optional
`export ` prefix, and surrounding quotes stripped - so

    C4_MODELS="anthropic:claude-sonnet-5 openai:gpt-5.6-terra"

arrives as the two specs it looks like, not as one spec with quotes in it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()   # trailing comment on a bare value
        out[key] = value
    return out


def load_env(path: Optional[Path] = None, override: bool = False) -> dict[str, str]:
    """Read path (default: the repo's .env) into os.environ. Returns what was applied."""
    path = Path(path or os.environ.get("C4_ENV_FILE") or DEFAULT_ENV)
    if not path.exists():
        return {}
    applied = {}
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        if override or not os.environ.get(key):
            os.environ[key] = value
            applied[key] = value
    return applied
