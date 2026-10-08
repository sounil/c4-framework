"""
A local control room for the experiment: compose runs, launch them, and read the
results.

    python -m experiments.ui              # -> http://127.0.0.1:8750
    make ui

Three things it does that the CLI makes you do by hand:

  * compose a run     - pick models, scenarios, conditions, Conscience levels and
                        the trial settings, save the lot as a named preset, see
                        the episode count before you spend anything.
  * manage miracles   - browse the signal catalogue with a preview of the exact
                        bytes each one puts on the wire, and add new signals,
                        message templates and conditions. Anything added here is
                        written to experiments/conditions.local.yaml, which the
                        catalogue merges over conditions.yaml - so the
                        hand-written file keeps its comments and its structure.
  * read the results  - aggregate tables and charts for a run (reusing
                        analyze.py, so the numbers are the report's numbers),
                        then any single episode in full: system prompt,
                        transcript, tool results, Chassis log, classification.

It runs the real CLI in a subprocess (`python -m experiments.run ...`), so a run
started here is identical to one started from the shell, resumable with the same
--run-id, and killable without taking the UI down.

Bound to 127.0.0.1. It starts processes and writes config files on your machine:
do not expose it to a network.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from .analyze import analyze, load_run
from .conditions import LOCAL_PATH, load_catalogue
from .env import load_env
from .run import load_scenarios

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "ui_static"
RUNS = ROOT / "results" / "runs"
PRESETS = HERE / "presets.json"


# --------------------------------------------------------------------------- #
# Launching runs
# --------------------------------------------------------------------------- #

class Launch:
    """One `python -m experiments.run` subprocess, with its output kept for the UI."""

    def __init__(self, run_id: str, argv: list[str]):
        self.run_id = run_id
        self.argv = argv
        self.started = time.time()
        self.lines: deque[str] = deque(maxlen=2000)
        self.returncode: Optional[int] = None
        self.proc = subprocess.Popen(
            argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
        self.returncode = self.proc.wait()

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        if self.running:
            self.proc.terminate()

    def state(self, since: int = 0) -> dict:
        lines = list(self.lines)
        return {"run_id": self.run_id, "running": self.running, "returncode": self.returncode,
                "started": self.started, "argv": self.argv,
                "lines": lines[since:], "n_lines": len(lines)}


LAUNCHES: dict[str, Launch] = {}


def build_argv(spec: dict) -> tuple[str, list[str]]:
    """Turn the UI's form into the CLI invocation it stands for."""
    run_id = (spec.get("run_id") or time.strftime("%Y%m%d-%H%M%S")).strip()
    argv = [sys.executable, "-m", "experiments.run", "--run-id", run_id]
    for flag, key in [("--models", "models"), ("--scenarios", "scenarios"),
                      ("--conditions", "conditions"), ("--conscience", "conscience")]:
        values = [str(v) for v in (spec.get(key) or []) if str(v).strip()]
        if values:
            argv += [flag, *values]
    for flag, key in [("--trials", "trials"), ("--temperature", "temperature"),
                      ("--max-turns", "max_turns"), ("--concurrency", "concurrency")]:
        if spec.get(key) not in (None, ""):
            argv += [flag, str(spec[key])]
    if spec.get("dry_run"):
        argv.append("--dry-run")
    return run_id, argv


# --------------------------------------------------------------------------- #
# Reading runs
# --------------------------------------------------------------------------- #

def run_dirs() -> list[Path]:
    if not RUNS.exists():
        return []
    return sorted((d for d in RUNS.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)


def count_trials(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "rb") as f:
        return sum(1 for line in f if line.strip())


def run_index() -> list[dict]:
    out = []
    for d in run_dirs():
        cfg = {}
        cfg_path = d / "config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cfg = {}
        launch = LAUNCHES.get(d.name)
        expected = None
        if cfg:
            expected = (len(cfg.get("models", [])) * len(cfg.get("scenarios", []))
                        * len(cfg.get("conditions", [])) * len(cfg.get("conscience", []))
                        * int(cfg.get("trials_per_cell") or 0)) or None
        out.append({
            "run_id": d.name,
            "started_at": cfg.get("started_at"),
            "models": cfg.get("models", []),
            "scenarios": cfg.get("scenarios", []),
            "conditions": cfg.get("conditions", []),
            "conscience": cfg.get("conscience", []),
            "trials_per_cell": cfg.get("trials_per_cell"),
            "n_done": count_trials(d / "trials.jsonl"),
            "n_expected": expected,
            "running": bool(launch and launch.running),
            "has_summary": (d / "summary.json").exists(),
        })
    return out


def trial_rows(run_id: str, filters: dict) -> list[dict]:
    """A compact row per episode - enough to filter and scan, not the transcript."""
    path = RUNS / run_id / "trials.jsonl"
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            o = r.get("outcome", {})
            if any(filters.get(k) and r.get(k) not in filters[k]
                   for k in ("model", "scenario", "condition", "conscience")):
                continue
            flag = filters.get("outcome")
            if flag:
                want = flag[0]
                if want == "error" and r.get("error") is None:
                    continue
                if want != "error" and not o.get(want):
                    continue
            rows.append({
                "trial_id": r.get("trial_id"), "model": r.get("model"), "scenario": r.get("scenario"),
                "condition": r.get("condition"), "signal": r.get("signal"), "message": r.get("message"),
                "conscience": r.get("conscience"), "seed": r.get("seed"), "turns": r.get("turns"),
                "duration_s": r.get("duration_s"), "error": r.get("error"),
                "complied": o.get("complied"), "persisted": o.get("persisted"),
                "abandoned": o.get("abandoned"), "harm_occurred": o.get("harm_occurred"),
                "triggered": o.get("triggered"), "full_obedience": o.get("full_obedience"),
            })
    return rows


def trial_detail(run_id: str, trial_id: str) -> Optional[dict]:
    path = RUNS / run_id / "trials.jsonl"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if trial_id in line:
                r = json.loads(line)
                if r.get("trial_id") == trial_id:
                    return r
    return None


def run_summary(run_id: str) -> dict:
    """Aggregate a run the same way `analyze` does, without writing anything."""
    d = RUNS / run_id
    if not (d / "trials.jsonl").exists():
        return {"error": f"no trials yet for {run_id}"}
    trials, cfg = load_run(d)
    if not trials:
        return {"error": "no trials recorded yet"}
    return analyze(trials, cfg)


# --------------------------------------------------------------------------- #
# The catalogue, and adding to it
# --------------------------------------------------------------------------- #

def catalogue_payload() -> dict:
    cat = load_catalogue()
    scenarios = load_scenarios(["all"])
    return {
        "signals": [s.as_dict() for s in cat.signals.values()],
        "message_templates": [{"id": k, "text": v} for k, v in cat.message_templates.items()],
        "conditions": cat.conditions,
        "defaults": cat.defaults,
        "contrasts": [{"a": a, "b": b, "desc": d} for a, b, d in cat.contrasts],
        "conscience": sorted(cat.conscience),
        "scenarios": [{"id": s["id"], "title": s.get("title", s["id"]),
                       "harm_class": s.get("harm_class", ""),
                       "forbidden": s["forbidden"].get("example_path", s["forbidden"]["path"]),
                       "commanded": s["commanded"].get("example_path", s["commanded"]["path"]),
                       "redirect": s.get("redirect", "").strip()} for s in scenarios],
        "env_models": os.environ.get("C4_MODELS", "").split(),
        "env_trials": os.environ.get("C4_TRIALS", "20"),
        "env_concurrency": os.environ.get("C4_CONCURRENCY", "4"),
        "local_path": str(LOCAL_PATH),
    }


def _load_local() -> dict:
    if LOCAL_PATH.exists():
        return yaml.safe_load(LOCAL_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_local(doc: dict) -> None:
    header = ("# Written by the control room (python -m experiments.ui).\n"
              "# Merged over conditions.yaml, so that file keeps its comments.\n"
              "# Hand edits are fine; keys here win over the ones there.\n\n")
    LOCAL_PATH.write_text(header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")


def add_signal(spec: dict) -> dict:
    sid = (spec.get("id") or "").strip()
    if not sid:
        raise ValueError("a signal needs an id")
    body = {k: v for k, v in {
        "status": spec.get("status"),
        "reason": (spec.get("reason") or None),
        "headers": {k: v for k, v in (spec.get("headers") or {}).items() if k and v},
        "body_style": spec.get("body_style") or "json",
        "label": (spec.get("label") or None),
        "role": (spec.get("role") or None),
        "kind": (spec.get("kind") or None),
    }.items() if v not in (None, {}, "")}
    doc = _load_local()
    doc.setdefault("signals", {})[sid] = body
    _save_local(doc)
    load_catalogue()            # fails loudly here rather than at the next run
    return {"ok": True, "id": sid}


def add_template(spec: dict) -> dict:
    tid = (spec.get("id") or "").strip()
    text = spec.get("text") or ""
    if not tid or not text.strip():
        raise ValueError("a message template needs an id and some text")
    if "{redirect}" not in text:
        raise ValueError("a message template must contain {redirect} - the scenario's own wording")
    doc = _load_local()
    doc.setdefault("message_templates", {})[tid] = text
    _save_local(doc)
    load_catalogue()
    return {"ok": True, "id": tid}


def add_condition(spec: dict) -> dict:
    cid = (spec.get("id") or "").strip()
    if not cid:
        raise ValueError("a condition needs an id")
    entry = {"id": cid, "signal": spec["signal"], "message": spec.get("message") or "none",
             "channel": spec.get("channel") or "tool_result"}
    for key in ("label", "role"):
        if spec.get(key):
            entry[key] = spec[key]
    doc = _load_local()
    conds = [c for c in doc.get("conditions", []) if c.get("id") != cid]
    conds.append(entry)
    doc["conditions"] = conds
    _save_local(doc)
    load_catalogue()
    return {"ok": True, "id": cid}


def read_presets() -> list[dict]:
    if PRESETS.exists():
        try:
            return json.loads(PRESETS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def write_preset(preset: dict) -> list[dict]:
    name = (preset.get("name") or "").strip()
    if not name:
        raise ValueError("a preset needs a name")
    presets = [p for p in read_presets() if p.get("name") != name]
    presets.append({**preset, "name": name, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    PRESETS.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    return presets


def delete_preset(name: str) -> list[dict]:
    presets = [p for p in read_presets() if p.get("name") != name]
    PRESETS.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    return presets


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "C4-ControlRoom/1.0"
    sys_version = ""

    def log_message(self, *_):
        pass

    # -- plumbing ----------------------------------------------------------
    def _send(self, status: int, payload: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, data: Any, status: int = 200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _static(self, name: str):
        # Resolve inside STATIC only: this server is local, but a path escape
        # would still hand out arbitrary files to anything that can reach it.
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if not path.startswith("/api/"):
                return self._static(path.lstrip("/"))

            if path == "/api/bootstrap":
                return self._json({"catalogue": catalogue_payload(), "runs": run_index(),
                                   "presets": read_presets()})
            if path == "/api/runs":
                return self._json(run_index())

            parts = [unquote(p) for p in path.strip("/").split("/")]
            if len(parts) >= 3 and parts[1] == "runs":
                run_id = parts[2]
                if len(parts) == 3:
                    return self._json({"summary": run_summary(run_id),
                                       "launch": LAUNCHES[run_id].state() if run_id in LAUNCHES else None})
                if parts[3] == "log":
                    launch = LAUNCHES.get(run_id)
                    since = int((q.get("since") or ["0"])[0])
                    return self._json(launch.state(since) if launch else {"lines": [], "running": False, "n_lines": 0})
                if parts[3] == "trials" and len(parts) == 4:
                    filters = {k: q[k] for k in ("model", "scenario", "condition", "conscience", "outcome") if k in q}
                    return self._json({"rows": trial_rows(run_id, filters)})
                if parts[3] == "trials" and len(parts) == 5:
                    rec = trial_detail(run_id, parts[4])
                    return self._json(rec or {"error": "no such trial"}, 200 if rec else 404)
            return self._json({"error": f"no route for {path}"}, 404)
        except Exception as e:                       # the UI shows it rather than dying silently
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/runs":
                run_id, argv = build_argv(body)
                if run_id in LAUNCHES and LAUNCHES[run_id].running:
                    return self._json({"error": f"run {run_id} is already going"}, 409)
                LAUNCHES[run_id] = Launch(run_id, argv)
                return self._json({"run_id": run_id, "argv": argv})
            if path.startswith("/api/runs/") and path.endswith("/stop"):
                run_id = unquote(path[len("/api/runs/"):-len("/stop")])
                launch = LAUNCHES.get(run_id)
                if not launch:
                    return self._json({"error": "not started from here"}, 404)
                launch.stop()
                return self._json({"ok": True})
            if path == "/api/signals":
                return self._json(add_signal(body))
            if path == "/api/message_templates":
                return self._json(add_template(body))
            if path == "/api/conditions":
                return self._json(add_condition(body))
            if path == "/api/presets":
                return self._json({"presets": write_preset(body)})
            if path == "/api/presets/delete":
                return self._json({"presets": delete_preset(body.get("name", ""))})
            return self._json({"error": f"no route for {path}"}, 404)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 400)


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("C4_UI_PORT", 8750)))
    ap.add_argument("--host", default="127.0.0.1", help="localhost only; it launches processes")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    load_env()
    httpd = Server((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"C4 control room -> {url}   (ctrl-c to stop)")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        for launch in LAUNCHES.values():
            launch.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
