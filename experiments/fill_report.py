"""
Substitute {{RESULT:<key>}} placeholders in REPORT.md with figures from a run.

    python -m experiments.fill_report --run-id <run_id>            # -> REPORT.filled.md
    python -m experiments.fill_report --run-id <run_id> --in-place # overwrite REPORT.md (keeps a .bak)
    python -m experiments.fill_report --list                       # show the placeholder keys the report uses

Keys the report may use (see analyze.py for how each is computed):
    run_meta, headline, baseline_propensity, n_trials_total, models_list,
    table_conditions, table_contrasts_pooled, table_contrasts_strong,
    table_conscience_x_condition, table_scenario_x_condition,
    table_model_x_condition, table_disclosure
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{\{RESULT:([a-zA-Z0-9_]+)\}\}")


def values_from_summary(S: dict) -> dict[str, str]:
    v = {
        "run_meta": S.get("run_meta", ""),
        "headline": S.get("headline", ""),
        "baseline_propensity": S.get("baseline_propensity", ""),
        "n_trials_total": str(S.get("n_trials", "")),
        "models_list": ", ".join(S.get("models", [])),
    }
    for name, table in S.get("tables", {}).items():
        v[f"table_{name}"] = table
    return v


def fill(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    missing = []

    def sub(m):
        key = m.group(1)
        if key in values and values[key]:
            return values[key]
        missing.append(key)
        return f"**[PENDING RESULT: {key} — run the experiments, then `make analyze fill`]**"

    return PLACEHOLDER.sub(sub, text), missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id")
    ap.add_argument("--report", default=str(ROOT / "REPORT.md"))
    ap.add_argument("--out", default=str(ROOT / "REPORT.filled.md"))
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--list", action="store_true", help="list placeholder keys used by the report")
    ap.add_argument("--out-root", default=str(ROOT / "results" / "runs"))
    args = ap.parse_args(argv)

    report = Path(args.report)
    text = report.read_text(encoding="utf-8")
    keys = sorted(set(PLACEHOLDER.findall(text)))
    if args.list:
        print("\n".join(keys))
        return

    values: dict[str, str] = {}
    if args.run_id:
        summary = Path(args.out_root) / args.run_id / "summary.json"
        if not summary.exists():
            sys.exit(f"{summary} not found; run: python -m experiments.analyze --run-id {args.run_id}")
        values = values_from_summary(json.loads(summary.read_text(encoding="utf-8")))

    filled, missing = fill(text, values)
    out = report if args.in_place else Path(args.out)
    if args.in_place:
        shutil.copy(report, report.with_suffix(".md.bak"))
    out.write_text(filled, encoding="utf-8")
    print(f"wrote {out}  ({len(keys) - len(set(missing))} of {len(keys)} placeholders filled)")
    if missing:
        print("pending:", ", ".join(sorted(set(missing))))


if __name__ == "__main__":
    main()
