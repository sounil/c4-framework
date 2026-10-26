"""
A local control room for the experiment: compose runs, launch them, and read the
results.

    python -m experiments.ui              # -> http://127.0.0.1:8750
    make ui

Three things it does that the CLI makes you do by hand:

  * compose a run     - pick models, scenarios, conditions, Conscience levels and
                        the trial settings, save the lot as a named preset, see
                        the episode count before you spend anything.
  * manage miracles   - browse the signal catalogue, ordered least to most
                        compelling, with a preview of the exact bytes each one
                        puts on the wire; edit any of them, rate them, and add
                        or edit message templates and conditions. Anything
                        written here goes to experiments/conditions.local.yaml,
                        which the catalogue merges over conditions.yaml - so the
                        hand-written file keeps its comments and its structure,
                        and every edit is checked against the merged catalogue
                        before it is saved.
  * tune the Conscience - switch the clauses that make a level weak or strong,
                        and read the system prompt they produce.
  * edit scenarios    - the task, the forbidden and commanded endpoints and the
                        redirect, validated before the file is written.
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

from . import scenario_store
from .analyze import analyze, load_run
from .conditions import (CHANNEL_DOCS, CHANNELS, DEFAULT_PATH, LOCAL_PATH, ORIGIN_DOCS,
                         PROMOTABLE, build_catalogue, load_catalogue, merge_docs, promote,
                         read_doc)
from .conditions import _cond_name, _write_local_doc
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
    """A compact row per episode - enough to filter and scan, not the transcript.

    Condition ids are canonicalised the way analyze does, so a run recorded
    before ids became automatic filters by the same name the Results tab shows.
    """
    path = RUNS / run_id / "trials.jsonl"
    rows = []
    if not path.exists():
        return rows
    cat = load_catalogue()
    codes = {c["id"]: c["code"] for c in cat.conditions}
    # Filter values go through the same mapping as the rows, so picking a run's
    # own recorded id out of a dropdown matches the canonical id it became.
    filters = {k: ([cat.resolve(v) for v in vs] if k == "condition" else vs)
               for k, vs in filters.items()}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            o = r.get("outcome", {})
            r["condition_as_run"] = r.get("condition")
            r["condition"] = cat.resolve(r.get("condition", ""))
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
                "condition": r.get("condition"), "code": codes.get(r.get("condition"), ""),
                "signal": r.get("signal"), "message": r.get("message"),
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

def _rel(path: Path) -> str:
    """Repo-relative, because that is how these paths get typed and talked about."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _origin(kind: str, name: str) -> str:
    """Where a definition comes from, so the editor can offer the right verb:
    shipped (delete), edited (revert to shipped), or local (delete, or promote
    into conditions.yaml)."""
    in_base = name in (read_doc(DEFAULT_PATH).get(kind) or {})
    in_local = name in (_load_local().get(kind) or {})
    return "edited" if in_base and in_local else "local" if in_local else "shipped"


def _condition_origin(cond: dict) -> str:
    """A generated grid cell belongs to whichever file its `expand:` block is in,
    so only hand-written entries can be local or edited."""
    if cond.get("generated"):
        return "shipped"
    names = {_cond_name(c) for c in (read_doc(DEFAULT_PATH).get("conditions") or [])}
    local_names = {_cond_name(c) for c in (_load_local().get("conditions") or [])}
    in_base, in_local = cond["id"] in names, cond["id"] in local_names
    return "edited" if in_base and in_local else "local" if in_local else "shipped"


def catalogue_payload() -> dict:
    cat = load_catalogue()
    scenarios = load_scenarios(["all"])
    return {
        "signals": [{**s.as_dict(), "origin": _origin("signals", s.id),
                     "band": cat.rating_scale.get(s.rating, "")}
                    for s in cat.signals.values()],
        "removed_signals": ((_load_local().get("remove") or {}).get("signals") or []),
        "rating_scale": [{"rating": k, "band": v} for k, v in sorted(cat.rating_scale.items())],
        "message_templates": [{"id": k, "text": v, "origin": _origin("message_templates", k)}
                              for k, v in cat.message_templates.items()],
        "conditions": [{**c, "origin": _condition_origin(c)} for c in cat.conditions],
        "defaults": cat.defaults,
        "contrasts": [{"a": a, "b": b, "desc": d} for a, b, d in cat.contrasts],
        "key_conditions": cat.key_conditions,
        "conscience": list(cat.conscience_levels),
        "conscience_levels": [{**lv.as_dict(), "origin": _origin("conscience", lid)}
                              for lid, lv in cat.conscience_levels.items()],
        "conscience_clauses": [{"id": k, "text": v, "origin": _origin("conscience_clauses", k)}
                               for k, v in cat.conscience_clauses.items()],
        "channels": [{"id": ch, **CHANNEL_DOCS[ch]} for ch in CHANNELS],
        "origin_docs": ORIGIN_DOCS,
        "promotable": list(PROMOTABLE),
        "base_path": _rel(DEFAULT_PATH),
        "scenarios": [{"id": s["id"], "title": s.get("title", s["id"]),
                       "harm_class": s.get("harm_class", ""),
                       "forbidden": s["forbidden"].get("example_path", s["forbidden"]["path"]),
                       "commanded": s["commanded"].get("example_path", s["commanded"]["path"]),
                       "redirect": s.get("redirect", "").strip()} for s in scenarios],
        "env_models": os.environ.get("C4_MODELS", "").split(),
        "env_trials": os.environ.get("C4_TRIALS", "20"),
        "env_concurrency": os.environ.get("C4_CONCURRENCY", "4"),
        "local_path": _rel(LOCAL_PATH),
    }


def _load_local() -> dict:
    if LOCAL_PATH.exists():
        return yaml.safe_load(LOCAL_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_local(doc: dict) -> None:
    """Write the overlay - but only after the merge it produces resolves.

    Everything here is validated against the *merged* catalogue in memory first,
    so a bad edit is refused at the form. Writing a file that then fails to load
    would break every other tab and the next run with it.
    """
    doc = {k: v for k, v in doc.items() if v not in (None, {}, [])}
    build_catalogue(merge_docs(read_doc(DEFAULT_PATH), doc))
    _write_local_doc(doc, LOCAL_PATH)


def _clean(keys: dict) -> dict:
    """Keep the keys that carry something; drop the blanks so the overlay stays
    readable and a cleared field falls back to the shipped definition."""
    return {k: v for k, v in keys.items() if v not in (None, "", {}, [])}


def _mark_removed(doc: dict, kind: str, name: str, removed: bool) -> None:
    """A shipped definition cannot be deleted by leaving it out of the overlay -
    it is still in conditions.yaml. It needs an explicit `remove:` entry."""
    rem = doc.setdefault("remove", {}).setdefault(kind, [])
    if removed and name not in rem:
        rem.append(name)
    if not removed and name in rem:
        rem.remove(name)
    if not rem:
        doc["remove"].pop(kind, None)
    if not doc.get("remove"):
        doc.pop("remove", None)


# -- signals -----------------------------------------------------------------

def save_signal(spec: dict) -> dict:
    """Create a signal, or override a shipped one. The form sends the whole
    signal, so the overlay entry is self-contained and readable on its own."""
    sid = (spec.get("id") or "").strip()
    if not sid:
        raise ValueError("a signal needs an id")
    body = _clean({
        "kind": spec.get("kind") or None,
        "status": spec.get("status"),
        "reason": spec.get("reason") or None,
        "headers": {k: v for k, v in (spec.get("headers") or {}).items() if k and v},
        "body": spec.get("body") or None,
        "body_style": spec.get("body_style") or "json",
        "label": spec.get("label") or None,
        "role": spec.get("role") or None,
        "rating": spec.get("rating"),
    })
    if body.get("status") in (None, ""):
        raise ValueError("a signal needs a status - the token it puts on the wire")
    doc = _load_local()
    doc.setdefault("signals", {})[sid] = body
    _mark_removed(doc, "signals", sid, False)
    _save_local(doc)
    return {"ok": True, "id": sid}


def set_rating(sid: str, rating: int) -> dict:
    """Just the rating, from the slider: re-sorts the catalogue without
    reopening the whole signal."""
    cat = load_catalogue()
    if sid not in cat.signals:
        raise KeyError(f"unknown signal {sid!r}")
    current = cat.signals[sid].as_dict()
    current["rating"] = int(rating)
    current.pop("is_miracle", None)
    return save_signal(current)


def _forget(kind: str, name: str, used: list[str], used_msg: str) -> dict:
    """Undo a definition, which means one of two things.

    An entry that overrides a shipped one is *reverted*: dropping it from the
    overlay brings conditions.yaml's version back. One that exists only in the
    overlay simply goes. A shipped one that was never overridden cannot be
    dropped by omission, so it gets an explicit `remove:` entry - and only then
    does anything still referring to it have to be dealt with first.
    """
    doc = _load_local()
    in_local = name in (doc.get(kind) or {})
    in_base = name in (read_doc(DEFAULT_PATH).get(kind) or {})
    reverted = in_local and in_base
    if not reverted and used:
        raise ValueError(f"{used_msg.format(name=name)}: {', '.join(used)}")
    doc.get(kind, {}).pop(name, None)
    if in_base and not in_local:
        _mark_removed(doc, kind, name, True)
    _save_local(doc)
    return {"ok": True, "id": name, "reverted": reverted}


def delete_signal(sid: str) -> dict:
    """Remove a signal entirely, and every condition built on it.

    A condition without its signal is not a condition, so refusing while one
    still used it made most signals undeletable: every shipped miracle sits in
    an `expand:` grid or a hand-written condition. A shipped signal gets one
    `remove:` entry, which merge_docs follows into the grids and the condition
    list; restore_signal drops that entry and brings all of it back.
    """
    cat = load_catalogue()
    if sid not in cat.signals:
        raise KeyError(f"unknown signal {sid!r}")
    gone = [c["id"] for c in cat.conditions if c["signal"]["id"] == sid]
    doc = _load_local()
    (doc.get("signals") or {}).pop(sid, None)
    doc["conditions"] = [c for c in doc.get("conditions") or [] if c.get("signal") != sid]
    if sid in (read_doc(DEFAULT_PATH).get("signals") or {}):
        _mark_removed(doc, "signals", sid, True)
    _save_local(doc)
    return {"ok": True, "id": sid, "conditions_removed": gone}


def revert_signal(sid: str) -> dict:
    """Drop a local edit of a shipped signal, so conditions.yaml's version is back."""
    doc = _load_local()
    if sid not in (doc.get("signals") or {}) or sid not in (read_doc(DEFAULT_PATH).get("signals") or {}):
        raise ValueError(f"signal {sid!r} has no local edit to revert")
    doc["signals"].pop(sid)
    _save_local(doc)
    return {"ok": True, "id": sid, "reverted": True}


def restore_signal(sid: str) -> dict:
    """Undo delete_signal on a shipped signal, conditions and all."""
    doc = _load_local()
    if sid not in ((doc.get("remove") or {}).get("signals") or []):
        raise ValueError(f"signal {sid!r} was not removed")
    _mark_removed(doc, "signals", sid, False)
    _save_local(doc)
    return {"ok": True, "id": sid}


# -- message templates -------------------------------------------------------

def save_template(spec: dict) -> dict:
    tid = (spec.get("id") or "").strip()
    text = spec.get("text") or ""
    if not tid or not text.strip():
        raise ValueError("a message template needs an id and some text")
    if "{redirect}" not in text:
        raise ValueError("a message template must contain {redirect} - the scenario's own wording")
    doc = _load_local()
    doc.setdefault("message_templates", {})[tid] = text
    _mark_removed(doc, "message_templates", tid, False)
    _save_local(doc)
    return {"ok": True, "id": tid}


def delete_template(tid: str) -> dict:
    used = [c["id"] for c in load_catalogue().conditions
            if c["message"] == tid and not c.get("generated")]
    return _forget("message_templates", tid, used, "template {name!r} is still used by")


# -- conscience --------------------------------------------------------------

def save_clause(spec: dict) -> dict:
    cid = (spec.get("id") or "").strip()
    text = (spec.get("text") or "").strip()
    if not cid or not text:
        raise ValueError("a Conscience clause needs an id and one sentence of text")
    doc = _load_local()
    doc.setdefault("conscience_clauses", {})[cid] = text
    _mark_removed(doc, "conscience_clauses", cid, False)
    _save_local(doc)
    return {"ok": True, "id": cid}


def delete_clause(cid: str) -> dict:
    used = [lid for lid, lv in load_catalogue().conscience_levels.items() if cid in lv.clauses]
    return _forget("conscience_clauses", cid, used,
                   "clause {name!r} is still switched on for")


def save_conscience(spec: dict) -> dict:
    """One Conscience level: which clauses are switched on, plus free text."""
    lid = (spec.get("id") or "").strip()
    if not lid:
        raise ValueError("a Conscience level needs an id - it is what --conscience names")
    entry = _clean({
        "label": spec.get("label") or None,
        "clauses": [str(c) for c in (spec.get("clauses") or [])],
        "text": (spec.get("text") or "").strip() or None,
        "role": (spec.get("role") or "").strip() or None,
    })
    entry.setdefault("clauses", [])      # an empty list is meaningful: that is what "weak" is
    doc = _load_local()
    doc.setdefault("conscience", {})[lid] = entry
    _mark_removed(doc, "conscience", lid, False)
    _save_local(doc)
    return {"ok": True, "id": lid}


def delete_conscience(lid: str) -> dict:
    cat = load_catalogue()
    if len(cat.conscience_levels) <= 1 and lid in cat.conscience_levels:
        raise ValueError("this is the last Conscience level; a run needs at least one")
    return _forget("conscience", lid, [], "")


# -- conditions --------------------------------------------------------------

def save_condition(spec: dict) -> dict:
    """A condition is its three knobs. No id and no number are accepted: both
    are derived, so the same three knobs are always the same condition."""
    signal = (spec.get("signal") or "").strip()
    if not signal:
        raise ValueError("a condition needs a signal")
    entry = {"signal": signal, "message": spec.get("message") or "none",
             "channel": spec.get("channel") or "tool_result"}
    for key in ("label", "role"):
        if spec.get(key):
            entry[key] = spec[key]
    # An `id` is not rejected, it is demoted: a name someone wants this cell to
    # answer to becomes an alias, exactly as the ones in conditions.yaml did.
    aliases = [str(a) for a in (spec.get("aliases") or []) if str(a).strip()]
    if spec.get("id"):
        aliases.insert(0, str(spec["id"]).strip())
    if aliases:
        entry["aliases"] = list(dict.fromkeys(aliases))
    doc = _load_local()
    same = (entry["signal"], entry["message"], entry["channel"])
    conds = [c for c in doc.get("conditions", [])
             if (c.get("signal"), c.get("message", "none"), c.get("channel", "tool_result")) != same]
    conds.append(entry)
    doc["conditions"] = conds
    cid = f"{signal}+{entry['message']}" + ("" if entry["channel"] == "tool_result" else f"@{entry['channel']}")
    _mark_removed(doc, "conditions", cid, False)
    _save_local(doc)
    return {"ok": True, "id": cid}


def delete_condition(cid: str) -> dict:
    cat = load_catalogue()
    cid = cat.resolve(cid)
    doc = _load_local()
    before = len(doc.get("conditions", []))
    doc["conditions"] = [c for c in doc.get("conditions", [])
                         if f"{c.get('signal')}+{c.get('message', 'none')}"
                         + ("" if c.get("channel", "tool_result") == "tool_result" else f"@{c['channel']}") != cid
                         and c.get("id") != cid]
    reverted = len(doc.get("conditions", [])) < before
    if not reverted:
        _mark_removed(doc, "conditions", cid, True)
    _save_local(doc)
    return {"ok": True, "id": cid, "reverted": reverted}


def set_defaults(ids: list[str]) -> dict:
    """What a run with no --conditions uses."""
    cat = load_catalogue()
    resolved = [cat.resolve(str(i)) for i in ids]
    unknown = [i for i in resolved if not any(c["id"] == i for c in cat.conditions)]
    if unknown:
        raise KeyError(f"unknown condition(s) {unknown}")
    doc = _load_local()
    doc["default_conditions"] = resolved
    _save_local(doc)
    return {"ok": True, "default_conditions": resolved}


# -- aliases kept for callers (and tests) written against the add-only API ----
add_signal = save_signal
add_template = save_template
add_condition = save_condition


def promote_entry(kind: str, name: str) -> dict:
    """Move a local definition into conditions.yaml - the file the repo ships.

    This is the answer to "it says local, how do I make it part of the system":
    the overlay is private to this checkout, conditions.yaml is shared.
    """
    return promote(kind, name, path=DEFAULT_PATH, local_path=LOCAL_PATH)


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
            if path == "/api/catalogue":
                return self._json(catalogue_payload())
            if path == "/api/scenarios":
                return self._json({"scenarios": scenario_store.read_all(),
                                   "template": scenario_store.TEMPLATE})

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
            # Every edit below answers with the whole catalogue, because one
            # change re-sorts and re-numbers the rest of it.
            def saved(result):
                return self._json({**result, "catalogue": catalogue_payload()})

            if path == "/api/signals":
                return saved(save_signal(body))
            if path == "/api/signals/rating":
                return saved(set_rating(body["id"], body["rating"]))
            if path == "/api/signals/delete":
                return saved(delete_signal(body["id"]))
            if path == "/api/signals/revert":
                return saved(revert_signal(body["id"]))
            if path == "/api/signals/restore":
                return saved(restore_signal(body["id"]))
            if path == "/api/message_templates":
                return saved(save_template(body))
            if path == "/api/message_templates/delete":
                return saved(delete_template(body["id"]))
            if path == "/api/conditions":
                return saved(save_condition(body))
            if path == "/api/conditions/delete":
                return saved(delete_condition(body["id"]))
            if path == "/api/conditions/defaults":
                return saved(set_defaults(body.get("ids") or []))
            if path == "/api/promote":
                return saved(promote_entry(body["kind"], body["id"]))
            if path == "/api/conscience":
                return saved(save_conscience(body))
            if path == "/api/conscience/delete":
                return saved(delete_conscience(body["id"]))
            if path == "/api/conscience_clauses":
                return saved(save_clause(body))
            if path == "/api/conscience_clauses/delete":
                return saved(delete_clause(body["id"]))
            if path == "/api/scenarios":
                return self._json({**scenario_store.save(body["id"], body.get("text", "")),
                                   "scenarios": scenario_store.read_all()})
            if path == "/api/scenarios/create":
                return self._json({**scenario_store.create(body["id"], body.get("title", "")),
                                   "scenarios": scenario_store.read_all()})
            if path == "/api/scenarios/duplicate":
                return self._json({**scenario_store.duplicate(body["from"], body["id"]),
                                   "scenarios": scenario_store.read_all()})
            if path == "/api/scenarios/archive":
                return self._json({**scenario_store.archive(body["id"]),
                                   "scenarios": scenario_store.read_all()})
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
