"""
Sketch: run the same experiment with a `bash` tool inside the sandbox container.

This is deliberately small. It shows the three adapters needed to reuse
experiments/{classify,analyze,fill_report}.py unchanged:

  1. a tool executor that runs commands in the container,
  2. a per-trial writer for /chassis/state/trial.json,
  3. a log reader that turns proxy.jsonl + shim.jsonl into a Chassis-shaped log.

Wire these into a copy of experiments/agent.py:run_trial (replace TOOLS and
the http_request branch) to run the shell variant. Not used for any figure in
REPORT.md.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from experiments.providers import ToolSpec

SANDBOX = Path(__file__).resolve().parent
STATE = SANDBOX / "state" / "trial.json"
LOGS = SANDBOX / "state" / "logs"

BASH_TOOL = ToolSpec(
    name="bash",
    description="Run a bash command inside your workstation. Returns stdout, stderr and the exit status.",
    parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
)


def write_trial_state(trial_id: str, scenario: dict, status: int, message: str | None) -> None:
    STATE.write_text(json.dumps({
        "trial_id": trial_id,
        "forbidden_hosts": scenario.get("forbidden_hosts", []),
        "commanded_hosts": scenario.get("commanded_hosts", []),
        "allowed_hosts": scenario.get("allowed_hosts", []),
        "forbidden_paths": scenario.get("forbidden_paths", []),
        "status": status,
        "message": message,
    }, indent=2))


def run_bash(command: str, timeout: int = 60) -> str:
    p = subprocess.run(["docker", "compose", "-f", str(SANDBOX / "docker-compose.yml"), "exec", "-T", "agent",
                        "bash", "-lc", command], capture_output=True, text=True, timeout=timeout)
    out = p.stdout[-4000:]
    err = p.stderr[-4000:]
    return f"exit={p.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"


def chassis_log_for(trial_id: str) -> list[dict]:
    """Merge proxy + shim logs for one trial into experiments.chassis.LoggedRequest-shaped dicts."""
    entries = []
    for name in ("proxy.jsonl", "shim.jsonl"):
        p = LOGS / name
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("trial_id") != trial_id:
                continue
            entries.append({"t": e["t"], "method": e["method"], "path": e.get("host", "") + e.get("path", ""),
                            "body": None, "category": e["category"], "status": int(e["status"])})
    return sorted(entries, key=lambda e: e["t"])
