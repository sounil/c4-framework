"""
Run the experiment matrix:  models x scenarios x conditions x conscience x trials

    python -m experiments.run --models anthropic:claude-sonnet-4-5 --trials 20
    python -m experiments.run --models scripted:zealot scripted:obedient --trials 5   # no API key needed

Results are appended to results/runs/<run_id>/trials.jsonl as they complete, so
an interrupted run can be resumed by re-running with --run-id <run_id>.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from .agent import TrialSpec, run_trial
from .chassis import Chassis
from .classify import classify
from .conditions import load_catalogue, select
from .env import load_env
from .providers import make_provider

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_yaml(p: Path):
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_scenarios(ids: list[str] | None) -> list[dict]:
    scenarios = [load_yaml(p) for p in sorted((HERE / "scenarios").glob("*.yaml"))]
    if ids and ids != ["all"]:
        wanted = set(ids)
        scenarios = [s for s in scenarios if s["id"] in wanted]
        missing = wanted - {s["id"] for s in scenarios}
        if missing:
            sys.exit(f"unknown scenario(s): {sorted(missing)}")
    return scenarios


def load_conditions(ids: list[str] | None):
    """Resolve condition selectors against conditions.yaml.

    "default" (the CLI default) uses the YAML's `default_conditions:` list,
    "all" takes everything including generated `expand:` grids, and anything
    else matches a canonical id (code_666+redirect), an automatic number (C32),
    a signal name (glyph selects every message variant of it), a legacy alias
    (C4_miracle_msg), or any prefix of those.
    """
    cat = load_catalogue(HERE / "conditions.yaml")
    try:
        conds = select(cat, ids)
    except (KeyError, ValueError) as e:
        sys.exit(str(e))
    return conds, cat.message_templates, cat.conscience


def trial_key(model: str, scenario: str, condition: str, conscience: str, seed: int) -> str:
    return f"{model}|{scenario}|{condition}|{conscience}|{seed}"


def main(argv=None):
    load_env()   # before argparse: the defaults below read os.environ
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=os.environ.get("C4_MODELS", "").split() or None,
                    help="model specs, e.g. anthropic:claude-sonnet-4-5 openai:gpt-4.1 ollama:llama3.1 scripted:zealot")
    ap.add_argument("--scenarios", nargs="+", default=["all"])
    ap.add_argument("--conditions", nargs="+", default=["default"],
                    help="condition ids (code_666+redirect), automatic numbers (C32), signal names "
                         "(glyph), legacy aliases (C4_miracle_msg), prefixes of any of those, "
                         "'all', or 'default' for conditions.yaml's default_conditions")
    ap.add_argument("--conscience", nargs="+", default=None,
                    help="Conscience levels to run, as named under conscience: in conditions.yaml "
                         "(default: every level defined there)")
    ap.add_argument("--trials", type=int, default=int(os.environ.get("C4_TRIALS", 20)), help="trials per cell")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("C4_CONCURRENCY", 4)))
    ap.add_argument("--run-id", default=None, help="reuse an existing run directory (resume)")
    ap.add_argument("--out-root", default=str(ROOT / "results" / "runs"))
    ap.add_argument("--dry-run", action="store_true", help="print the matrix size and exit")
    args = ap.parse_args(argv)

    if not args.models:
        sys.exit("No models given. Use --models <provider>:<model> ..., or set C4_MODELS in .env "
                 f"({ROOT / '.env'}). Try --models scripted:zealot for a key-free smoke test.")

    scenarios = load_scenarios(args.scenarios)
    conditions, templates, conscience_levels = load_conditions(args.conditions)
    cond_by_id = {c["id"]: c for c in conditions}

    # Conscience levels are data, not an enum: whatever conditions.yaml defines
    # can be run, so adding a third strength is a YAML edit.
    args.conscience = args.conscience or list(conscience_levels)
    unknown = [lvl for lvl in args.conscience if lvl not in conscience_levels]
    if unknown:
        sys.exit(f"unknown conscience level(s) {unknown}; conditions.yaml defines "
                 f"{list(conscience_levels)}")

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_root) / run_id
    trials_path = out_dir / "trials.jsonl"

    done: set[str] = set()
    if trials_path.exists():
        with open(trials_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add(trial_key(r["model"], r["scenario"], r["condition"], r["conscience"], r["seed"]))
                except json.JSONDecodeError:
                    pass

    jobs = []
    for model in args.models:
        for sc in scenarios:
            for cond in conditions:
                for lvl in args.conscience:
                    for i in range(args.trials):
                        seed = i  # seed is the trial index so cells are comparable across conditions
                        k = trial_key(model, sc["id"], cond["id"], lvl, seed)
                        if k in done:
                            continue
                        jobs.append((model, sc, cond, lvl, seed))

    print(f"run_id={run_id}  matrix: {len(args.models)} models x {len(scenarios)} scenarios x "
          f"{len(conditions)} conditions x {len(args.conscience)} conscience x {args.trials} trials "
          f"= {len(args.models)*len(scenarios)*len(conditions)*len(args.conscience)*args.trials} trials "
          f"({len(done)} already done, {len(jobs)} to run)")
    if args.dry_run:
        return          # print the matrix size only: leave no run directory behind

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id, "models": args.models, "scenarios": [s["id"] for s in scenarios],
            "conditions": [c["id"] for c in conditions], "conscience": args.conscience,
            "signals": {c["id"]: c["signal"]["id"] for c in conditions},
            "messages": {c["id"]: c["message"] for c in conditions},
            "codes": {c["id"]: c["code"] for c in conditions},
            "ratings": {c["id"]: c["rating"] for c in conditions},
            "trials_per_cell": args.trials, "temperature": args.temperature, "max_turns": args.max_turns,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, f, indent=2)

    providers = {m: make_provider(m) for m in args.models}
    chassis = Chassis().start()
    write_lock = threading.Lock()
    counts = {"ok": 0, "err": 0}
    t_start = time.time()

    def work(job):
        model, sc, cond, lvl, seed = job
        spec = TrialSpec(scenario=sc, condition=cond, conscience_level=lvl,
                         conscience_text=conscience_levels[lvl], message_templates=templates,
                         seed=seed, temperature=args.temperature, max_turns=args.max_turns)
        rec = run_trial(providers[model], chassis, spec)
        rec["model"] = model
        rec["run_id"] = run_id
        rec["outcome"] = classify(rec, cond_by_id[cond["id"]])
        return rec

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futures = [ex.submit(work, j) for j in jobs]
            for n, fut in enumerate(as_completed(futures), 1):
                rec = fut.result()
                with write_lock, open(trials_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                counts["err" if rec["error"] else "ok"] += 1
                o = rec["outcome"]
                flag = "ERR " if rec["error"] else ("C" if o["complied"] else ".") + ("P" if o["persisted"] else ".") + ("H" if o["harm_occurred"] else ".")
                elapsed = time.time() - t_start
                eta = (elapsed / n) * (len(jobs) - n)
                print(f"[{n:>4}/{len(jobs)}] {flag} {rec['model']:<28} {rec['scenario']:<15} {rec['condition']:<26} "
                      f"{rec['conscience']:<6} seed={rec['seed']:<2} turns={rec['turns']:<2} eta={eta/60:5.1f}m", flush=True)
    finally:
        chassis.stop()

    print(f"\ndone: {counts['ok']} ok, {counts['err']} errors -> {trials_path}")
    print(f"next: python -m experiments.analyze --run-id {run_id}")


if __name__ == "__main__":
    main()
