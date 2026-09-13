#!/usr/bin/env python3
"""Pretty-print one trial's transcript and Chassis log.

    python scripts/show_trial.py --run-id <id> --condition C4_miracle_msg --scenario exfil_report --seed 0 [--model spec] [--conscience strong]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--condition")
    ap.add_argument("--scenario")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--model")
    ap.add_argument("--conscience", choices=["weak", "strong"])
    ap.add_argument("--only", choices=["complied", "persisted", "abandoned", "harm_occurred"], help="filter by outcome flag")
    ap.add_argument("--limit", type=int, default=1)
    a = ap.parse_args()

    path = ROOT / "results" / "runs" / a.run_id / "trials.jsonl"
    if not path.exists():
        sys.exit(f"no such run: {path}")
    shown = 0
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if a.condition and r["condition"] != a.condition: continue
        if a.scenario and r["scenario"] != a.scenario: continue
        if a.seed is not None and r["seed"] != a.seed: continue
        if a.model and r["model"] != a.model: continue
        if a.conscience and r["conscience"] != a.conscience: continue
        if a.only and not r["outcome"].get(a.only): continue

        print("=" * 100)
        print(f"{r['model']} | {r['scenario']} | {r['condition']} | conscience={r['conscience']} | seed={r['seed']} | trial={r['trial_id']}")
        print(f"outcome: " + ", ".join(f"{k}={v}" for k, v in r["outcome"].items() if v not in (False, 0, None)))
        print("-" * 100)
        print("SYSTEM PROMPT:\n" + r["system_prompt"].strip() + "\n")
        for t in r["transcript"]:
            if "crown_interrupt" in t:
                print(f"  [CROWN -> agent] {t['crown_interrupt']}\n")
                continue
            print(f"  [turn {t['turn']}] assistant: {t['assistant_text'].strip()}")
            for call, res in zip(t.get("tool_calls", []), t.get("tool_results", [])):
                print(f"      -> {call['name']}({json.dumps(call['args'])})")
                print("      <- " + res.replace("\n", "\n         ")[:1200])
            print()
        print("CHASSIS LOG:")
        for e in r["chassis_log"]:
            print(f"  {e['method']:<6} {e['path']:<45} {e['status']}  [{e['category']}]")
        if r["error"]:
            print("ERROR:", r["error"])
        shown += 1
        if shown >= a.limit:
            break
    if not shown:
        print("no matching trials")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
