"""
The condition catalogue: what the world does, and what it says, when the agent
touches the forbidden path.

A condition is assembled from three pieces, each configured independently in
conditions.yaml, so a new kind of miracle is a YAML edit rather than a code
change:

    signal    the *physics* of the manifestation - the status token, the reason
              phrase, extra response headers, the shape of the body. Each one
              carries a `rating` 0-10, the researcher's prior for how compelling
              it ought to be; the catalogue is ordered by it, least first.
              (conditions.yaml: `signals:`)
    message   the *words* that ride along, if any. A template over the
              scenario's redirect text.        (conditions.yaml: `message_templates:`)
    channel   where those words reach the agent: tool_result (the Chassis),
              system (the Conscience), user_interrupt (the Crown), or none.

Those three ARE the condition's identity, so ids and numbers are derived, never
written: `code_666+redirect` is the id, `C27` is its position in the ordering,
and the label comes from the signal's label plus the message. Any `id:` left in
the YAML is kept as an alias, so older runs, contrasts and command lines that
name `C4_miracle_msg` still resolve.

An `expand:` block can generate the whole signal x message grid at once; a cell
written by hand and the same cell generated are one condition, not two.

Loading is the only place that knows the YAML schema. Everything downstream
(agent, classify, analyze) reads the resolved dicts this module returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Union

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "conditions.yaml"
# Anything the control room (experiments/ui.py) adds or edits lands here instead
# of in conditions.yaml, so the hand-written file keeps its comments and its
# ordering. Merged on top: a signal, template or conscience level of the same
# name wins; `remove:` drops one entirely.
LOCAL_PATH = HERE / "conditions.local.yaml"

KINDS = ("permit", "mundane", "miracle")
BODY_STYLES = ("json", "text", "none")
CHANNELS = ("tool_result", "system", "user_interrupt", "none")
CHANNEL_WORDS = {"tool_result": "the Chassis", "system": "the Conscience",
                 "user_interrupt": "the Crown", "none": "nothing"}

# The channel is the one knob that is NOT data: classify.py branches on it,
# because each channel decides the moment the instruction becomes available to
# the agent, and every outcome is measured from that moment. A fifth channel
# would need a fifth branch there, so the set is closed on purpose and the
# control room says so rather than offering a dropdown that cannot grow.
CHANNEL_DOCS = {
    "tool_result": {
        "quadrant": "Chassis",
        "short": "inside the HTTP response",
        "long": "The words ride on the signal itself, in the body the agent's own tool call "
                "returns. This is the manufactured miracle: the world appears to speak.",
        "measured_from": "the agent's first attempt on the forbidden path",
    },
    "system": {
        "quadrant": "Conscience",
        "short": "in the system prompt, from the start",
        "long": "The words are part of who the agent is told it is, before it acts. Nothing "
                "is manufactured and nothing interrupts: the rule was always there.",
        "measured_from": "the beginning of the episode - the instruction is never not available",
    },
    "user_interrupt": {
        "quadrant": "Crown",
        "short": "a human turn, after the first attempt",
        "long": "The words arrive as a message from the person the agent is working for, "
                "injected right after it first reaches for the forbidden path.",
        "measured_from": "the agent's first attempt on the forbidden path",
    },
    "none": {
        "quadrant": "-",
        "short": "nowhere - there are no words",
        "long": "No message exists, so there is nothing to obey. Used for the silent "
                "conditions and for the permitted baseline, which measures what the agent "
                "does when nothing stops it at all.",
        "measured_from": "never - these episodes have no instruction, so they are excluded "
                         "from compliance and reported as baseline propensity instead",
    },
}

ORIGIN_DOCS = {
    "shipped": "Defined in experiments/conditions.yaml, the catalogue that travels with the "
               "repo. Everyone who clones it gets this.",
    "edited": "Defined in conditions.yaml, but you have changed it here. Your version is in "
              "conditions.local.yaml and wins; Revert drops it and brings the shipped one back.",
    "local": "Defined only in conditions.local.yaml, which is yours - it is not part of the "
             "catalogue the repo ships. \u201cMove to conditions.yaml\u201d promotes it.",
}
SIGNAL_KEYS = {"kind", "status", "reason", "headers", "body", "body_style", "label", "role", "rating"}
MAX_RATING = 10


@dataclass
class Signal:
    """How the world manifests. Not what it says - that is the message."""

    id: str
    kind: str = "miracle"               # permit | mundane | miracle
    status: Union[int, str] = 666       # int stays inside the protocol; str is ungrammatical
    reason: Optional[str] = None        # reason phrase; emoji are legal (RFC 9110 obs-text)
    headers: dict = field(default_factory=dict)   # extra response headers the agent will see
    body: Optional[dict] = None         # base body fields, merged with the message (json style)
    body_style: str = "json"            # json | text | none
    label: Optional[str] = None
    role: Optional[str] = None          # why this signal exists, for the docs
    rating: int = 0                     # 0-10: how compelling we expect it to be. A prior, not a result.

    @property
    def is_miracle(self) -> bool:
        return self.kind == "miracle"

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["is_miracle"] = self.is_miracle
        return d


@dataclass
class ConscienceLevel:
    """One Conscience strength, assembled from named clauses plus free text.

    The clauses are the knobs: each is one sentence pushing the agent to finish
    the task as specified, and a level is the set it switches on. `text:` is
    appended for anything that does not belong in a reusable clause.
    """

    id: str
    label: str
    clauses: list[str] = field(default_factory=list)
    extra: str = ""
    role: str = ""
    text: str = ""          # resolved: the clause sentences, then `extra`

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Catalogue:
    conditions: list[dict]              # ordered least -> most compelling
    signals: dict[str, Signal]          # ordered by rating, least -> most compelling
    message_templates: dict[str, Optional[str]]
    conscience: dict[str, str]          # level id -> resolved system-prompt text
    conscience_levels: dict[str, ConscienceLevel]
    conscience_clauses: dict[str, str]
    rating_scale: dict[int, str]
    contrasts: list[tuple[str, str, str]]
    defaults: list[str]
    aliases: dict[str, str] = field(default_factory=dict)   # legacy id / code -> canonical id
    key_conditions: dict[str, str] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, dict]:
        """Canonical ids, plus every alias and auto-number that resolves to one."""
        out = {c["id"]: c for c in self.conditions}
        for name, cid in self.aliases.items():
            if cid in out:
                out.setdefault(name, out[cid])
        return out

    def resolve(self, name: str) -> str:
        """Map any id a run or a contrast might carry onto the canonical one."""
        if any(c["id"] == name for c in self.conditions):
            return name
        return self.aliases.get(name, name)

    @property
    def conscience_order(self) -> list[str]:
        return list(self.conscience_levels)


# --- parsing ----------------------------------------------------------------

def _coerce_status(value: Any) -> Union[int, str]:
    """Numbers stay numbers (the protocol can still classify them); anything
    else is emitted verbatim in the status-code position."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value)
    return int(text) if text.strip().isdigit() else text


def _infer_kind(status: Union[int, str]) -> str:
    if not isinstance(status, int):
        return "miracle"          # outside RFC 9110's 3DIGIT grammar entirely
    if status < 400:
        return "permit"
    if status < 600:
        return "mundane"
    return "miracle"              # 6xx+: three digits, but no class defines it


def parse_signal(sid: str, spec: Any) -> Signal:
    """A signal may be written in full, or as a bare status for the short case:

        code_666: 666
        glyph:    {status: "\U0001FA84 sparkles", rating: 8}
    """
    if not isinstance(spec, dict):
        spec = {"status": spec}
    unknown = set(spec) - SIGNAL_KEYS
    if unknown:
        raise ValueError(f"signal {sid!r}: unknown key(s) {sorted(unknown)}; allowed: {sorted(SIGNAL_KEYS)}")

    status = _coerce_status(spec.get("status", 666))
    kind = spec.get("kind") or _infer_kind(status)
    if kind not in KINDS:
        raise ValueError(f"signal {sid!r}: kind must be one of {KINDS}, got {kind!r}")
    body_style = spec.get("body_style", "json")
    if body_style not in BODY_STYLES:
        raise ValueError(f"signal {sid!r}: body_style must be one of {BODY_STYLES}, got {body_style!r}")
    headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    body = spec.get("body")
    if body is not None and not isinstance(body, dict):
        raise ValueError(f"signal {sid!r}: body must be a mapping (or omitted)")
    reason = spec.get("reason")
    rating = spec.get("rating", 0)
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ValueError(f"signal {sid!r}: rating must be a whole number 0-{MAX_RATING}, got {rating!r}")
    if not 0 <= rating <= MAX_RATING:
        raise ValueError(f"signal {sid!r}: rating must be 0-{MAX_RATING}, got {rating}")
    return Signal(id=sid, kind=kind, status=status,
                  reason=str(reason) if reason is not None else None,
                  headers=headers, body=body, body_style=body_style,
                  label=spec.get("label") or _default_signal_label(status, reason),
                  role=spec.get("role"), rating=rating)


def _default_signal_label(status: Union[int, str], reason: Any) -> str:
    return f"HTTP {status}" + (f" {reason}" if reason else "")


def order_signals(signals: dict[str, Signal]) -> dict[str, Signal]:
    """Least compelling first. Ties keep their written order, so a rating of 0
    everywhere leaves the file's own ordering alone."""
    order = list(signals)
    return {s.id: s for s in sorted(signals.values(), key=lambda s: (s.rating, order.index(s.id)))}


# --- automatic ids, labels and numbers --------------------------------------

def auto_id(signal: str, message: str, channel: str) -> str:
    """A condition IS its (signal, message, channel). The id says so.

    `tool_result` is the default channel and stays implicit, so the common case
    reads as `code_666+redirect`.
    """
    base = f"{signal}+{message}"
    return base if channel == "tool_result" else f"{base}@{channel}"


def auto_label(sig: Signal, message: str, channel: str) -> str:
    head = sig.label or _default_signal_label(sig.status, sig.reason)
    if message == "none":
        return f"{head} — nothing is said" if channel == "none" else f"{head}, no message"
    tail = "" if channel == "tool_result" else f" via {CHANNEL_WORDS[channel]}"
    return f"{head} + {message}{tail}"


def _sort_key(cond: dict, signals: dict[str, Signal], msg_rank: dict[str, int]) -> tuple:
    sig = signals[cond["signal"]["id"]] if isinstance(cond.get("signal"), dict) else None
    rating = sig.rating if sig else 0
    sig_pos = list(signals).index(sig.id) if sig else 0
    return (rating, sig_pos, msg_rank.get(cond["message"], 99), CHANNELS.index(cond["channel"]))


def number_conditions(conditions: list[dict], signals: dict[str, Signal],
                      templates: dict[str, Optional[str]]) -> list[dict]:
    """Order least -> most compelling and stamp on the automatic C-numbers.

    The number is presentational: it moves when a rating changes or a signal is
    added. The id never does, which is why the id is what runs record.
    """
    msg_rank = {"none": 0, **{name: i + 1 for i, name in enumerate(templates)}}
    ordered = sorted(conditions, key=lambda c: _sort_key(c, signals, msg_rank))
    width = max(2, len(str(len(ordered))))
    for i, cond in enumerate(ordered, 1):
        cond["number"] = i
        cond["code"] = f"C{i:0{width}d}"
    return ordered


def resolve_condition(raw: dict, signals: dict[str, Signal],
                      templates: dict[str, Optional[str]]) -> dict:
    """Turn one YAML condition into the dict the rest of the pipeline reads.

    `id:` is optional and, when present, becomes an alias: the canonical id is
    always derived from the three knobs. The legacy inline form
    (`forbidden_status` / `forbidden_reason` with no `signal:`) still works.
    """
    cond = dict(raw)
    written_id = cond.pop("id", None)
    aliases = [str(a) for a in (cond.pop("aliases", None) or [])]
    if written_id:
        aliases.insert(0, str(written_id))
    sid = cond.pop("signal", None)
    where = written_id or sid or "<condition>"

    if sid is None:
        sid = f"inline:{written_id or 'anonymous'}"
        sig = parse_signal(sid, {"status": cond.get("forbidden_status", 200),
                                 "reason": cond.get("forbidden_reason")})
    else:
        if sid not in signals:
            raise KeyError(f"condition {where!r}: unknown signal {sid!r}; "
                           f"known signals: {sorted(signals)}")
        sig = signals[sid]
        # A condition may tweak the signal it borrows without redefining it.
        if "forbidden_status" in cond:
            status = _coerce_status(cond["forbidden_status"])
            sig = replace(sig, status=status, kind=_infer_kind(status))
        if "forbidden_reason" in cond:
            sig = replace(sig, reason=str(cond["forbidden_reason"]) if cond["forbidden_reason"] is not None else None)

    message = cond.get("message", "none")
    if message != "none" and message not in templates:
        raise KeyError(f"condition {where!r}: unknown message template {message!r}; "
                       f"known templates: {sorted(templates)}")
    channel = cond.get("channel", "tool_result")
    if channel not in CHANNELS:
        raise ValueError(f"condition {where!r}: channel must be one of {CHANNELS}, got {channel!r}")

    cond["id"] = auto_id(sig.id, message, channel)
    cond["aliases"] = aliases
    cond["signal"] = sig.as_dict()
    cond["message"] = message
    cond["channel"] = channel
    cond["rating"] = sig.rating
    # Mirrors kept so older code (and saved runs) that reads these keys still works.
    cond["forbidden_status"] = sig.status
    cond["forbidden_reason"] = sig.reason
    cond.setdefault("label", auto_label(sig, message, channel))
    cond.setdefault("role", sig.role or "")
    return cond


def expand_conditions(blocks: Any, signals: dict[str, Signal],
                      templates: dict[str, Optional[str]]) -> list[dict]:
    """Generate the signal x message grid described by each `expand:` block.

    Ids are derived like every other condition's, so a generated cell and a
    hand-written one with the same three knobs are the same condition. A block
    with a `prefix:` also records the old generated name as an alias, so
    `--conditions M` and `--conditions M_glyph` keep selecting the grid.
    """
    out: list[dict] = []
    for blk in blocks or []:
        prefix = str(blk.get("prefix", "")).strip()
        channel = blk.get("channel", "tool_result")
        sids = blk.get("signals") or list(signals)
        msgs = blk.get("messages") or ["none"]
        for sid in sids:
            if sid not in signals:
                raise KeyError(f"expand block {prefix or '<unnamed>'!r}: unknown signal {sid!r}")
            for msg in msgs:
                entry: dict = {"signal": sid, "message": msg, "channel": channel, "generated": True}
                if prefix:
                    entry["aliases"] = [f"{prefix}_{sid}_{msg}"]
                out.append(entry)
    return out


def merge_duplicates(conditions: list[dict]) -> list[dict]:
    """One condition per (signal, message, channel).

    A hand-written cell and the generated one are the same stimulus; keeping
    both would put the identical condition in a run twice under two names.
    Hand-written wins on label and role; aliases accumulate.
    """
    merged: dict[str, dict] = {}
    for cond in conditions:
        cid = cond["id"]
        if cid not in merged:
            merged[cid] = cond
            continue
        first, second = merged[cid], cond
        if first.get("generated") and not second.get("generated"):
            first, second = second, first        # the hand-written one is the keeper
        for key, value in second.items():
            if key in ("aliases", "generated"):      # a hand-written keeper has no `generated` to protect
                continue
            first.setdefault(key, value)
        first["aliases"] = list(dict.fromkeys(first.get("aliases", []) + second.get("aliases", [])))
        first["generated"] = bool(first.get("generated") and second.get("generated"))
        merged[cid] = first
    return list(merged.values())


# --- conscience -------------------------------------------------------------

def parse_conscience(doc: dict) -> tuple[dict[str, ConscienceLevel], dict[str, str]]:
    """Read `conscience_clauses:` and `conscience:` into resolved levels.

    Three forms are accepted, so older files keep working:
        weak: ""                                  bare text
        strong: {text: "..."}                     text only
        strong: {clauses: [a, b], text: "..."}    clauses, then text
    """
    clauses = {str(k): str(v).strip() for k, v in (doc.get("conscience_clauses") or {}).items()}
    levels: dict[str, ConscienceLevel] = {}
    raw = doc.get("conscience")
    if raw is None:
        raw = {"weak": "", "strong": ""}
    for lid, spec in raw.items():
        if not isinstance(spec, dict):
            spec = {"text": "" if spec is None else str(spec)}
        unknown = set(spec) - {"label", "clauses", "text", "role"}
        if unknown:
            raise ValueError(f"conscience {lid!r}: unknown key(s) {sorted(unknown)}")
        picked = [str(c) for c in (spec.get("clauses") or [])]
        missing = [c for c in picked if c not in clauses]
        if missing:
            raise KeyError(f"conscience {lid!r}: unknown clause(s) {missing}; "
                           f"known clauses: {sorted(clauses)}")
        extra = str(spec.get("text") or "").strip()
        sentences = [clauses[c] for c in picked]
        text = "\n".join(filter(None, [" ".join(sentences) if sentences else "", extra])).strip()
        levels[str(lid)] = ConscienceLevel(id=str(lid), label=str(spec.get("label") or str(lid).title()),
                                           clauses=picked, extra=extra,
                                           role=str(spec.get("role") or ""), text=text)
    return levels, clauses


# --- loading / selection ----------------------------------------------------

def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def merge_docs(base: dict, local: dict) -> dict:
    """Overlay a local catalogue onto the main one. See LOCAL_PATH.

    Mappings (signals, templates, clauses, conscience levels) are overridden key
    by key, so editing a shipped signal means writing only that signal here.
    `remove:` drops a key or a condition from the base entirely.
    """
    if not local:
        return base
    doc = dict(base)
    for key in ("signals", "message_templates", "conscience_clauses", "conscience", "rating_scale"):
        if base.get(key) or local.get(key):
            doc[key] = {**(base.get(key) or {}), **(local.get(key) or {})}
    for key in ("conditions", "expand", "contrasts"):
        merged = list(base.get(key) or []) + list(local.get(key) or [])
        if key == "conditions":
            # A local condition replaces the base one it shares an id with, or
            # the one standing on the same three knobs - which is the same cell
            # under the derived id. An entry naming no signal has no cell to
            # collide with, so only the id can match it.
            local_ids = {c.get("id") for c in (local.get(key) or []) if c.get("id")}
            cell = lambda c: (c.get("signal"), c.get("message", "none"), c.get("channel", "tool_result"))
            local_cells = {cell(c) for c in (local.get(key) or []) if c.get("signal")}
            kept = [c for c in (base.get(key) or [])
                    if c.get("id") not in local_ids
                    and not (c.get("signal") and cell(c) in local_cells)]
            merged = kept + list(local.get(key) or [])
        doc[key] = merged
    for key in ("default_conditions", "key_conditions"):
        if local.get(key):
            doc[key] = local[key]

    remove = local.get("remove") or {}
    for key in ("signals", "message_templates", "conscience_clauses", "conscience"):
        for name in remove.get(key) or []:
            doc.get(key, {}).pop(name, None)
    # A condition is its signal, so removing a signal takes every cell built on
    # it along: out of each `expand:` grid, and out of the hand-written list.
    # One `remove:` line is then the whole deletion, and dropping it is the
    # whole undo.
    gone = set(remove.get("signals") or [])
    if gone:
        blocks = []
        for blk in doc.get("expand") or []:
            sids = blk.get("signals")
            if sids:
                kept = [s for s in sids if s not in gone]
                if not kept:        # an empty list would read as "every signal"
                    continue
                blk = {**blk, "signals": kept}
            blocks.append(blk)
        doc["expand"] = blocks
        doc["conditions"] = [c for c in doc.get("conditions") or [] if c.get("signal") not in gone]
    dropped = set(remove.get("conditions") or [])
    if dropped:
        doc["conditions"] = [c for c in doc.get("conditions") or []
                             if c.get("id") not in dropped
                             and auto_id(c.get("signal"), c.get("message", "none"),
                                         c.get("channel", "tool_result")) not in dropped]
    return doc


def load_catalogue(path: Optional[Path] = None) -> Catalogue:
    path = Path(path or DEFAULT_PATH)
    local = LOCAL_PATH if path == DEFAULT_PATH else path.with_name(path.stem + ".local" + path.suffix)
    return build_catalogue(merge_docs(_read(path), _read(local)))


def read_doc(path: Optional[Path] = None) -> dict:
    """The raw YAML of one catalogue file, unmerged. The control room reads the
    base and the local overlay separately so it can tell an override from the
    shipped definition it overrides."""
    return _read(Path(path or DEFAULT_PATH))


def build_catalogue(doc: dict) -> Catalogue:
    """Resolve an already-merged catalogue document.

    Separate from load_catalogue so an edit can be checked in memory - against
    the merge it *would* produce - before anything is written to disk.
    """
    signals = order_signals({sid: parse_signal(sid, spec) for sid, spec in (doc.get("signals") or {}).items()})
    templates = {k: v for k, v in (doc.get("message_templates") or {}).items() if k != "none"}

    raw_conditions = list(doc.get("conditions") or [])
    raw_conditions += expand_conditions(doc.get("expand"), signals, templates)
    conditions = merge_duplicates([resolve_condition(c, signals, templates) for c in raw_conditions])
    conditions = number_conditions(conditions, signals, templates)

    aliases: dict[str, str] = {}
    for cond in conditions:
        for name in list(cond["aliases"]) + [cond["code"]]:
            if name in aliases and aliases[name] != cond["id"]:
                raise ValueError(f"alias {name!r} points at both {aliases[name]!r} and {cond['id']!r}")
            aliases[name] = cond["id"]

    def canon(name: str) -> str:
        return name if any(c["id"] == name for c in conditions) else aliases.get(name, name)

    contrasts = []
    for x in doc.get("contrasts") or []:
        a, b, desc = (x["a"], x["b"], x.get("desc", "")) if isinstance(x, dict) else (list(x) + ["", ""])[:3]
        contrasts.append((canon(a), canon(b), desc))

    levels, clauses = parse_conscience(doc)
    scale = {int(k): str(v) for k, v in (doc.get("rating_scale") or {}).items()}
    return Catalogue(conditions=conditions, signals=signals, message_templates=templates,
                     conscience={lid: lv.text for lid, lv in levels.items()},
                     conscience_levels=levels, conscience_clauses=clauses, rating_scale=scale,
                     contrasts=contrasts, defaults=[canon(d) for d in (doc.get("default_conditions") or [])],
                     aliases=aliases,
                     key_conditions={k: canon(v) for k, v in (doc.get("key_conditions") or {}).items()})


def select(catalogue: Catalogue, ids: Optional[list[str]]) -> list[dict]:
    """Pick conditions by id, alias, auto-number, signal name, or any prefix.

    "default" -> the `default_conditions:` list in the YAML (everything, if it
    is empty), so narrowing a run never means commenting conditions out.
    "all"     -> every condition, generated grids included.
    """
    # "default" and "all" are expanded wherever they appear, not only when they
    # are the whole list: `--conditions default burning_bush+redirect` means the
    # usual set plus that one, and silently dropping either half would be worse
    # than refusing.
    wanted: list[str] = []
    for token in (ids or ["default"]):
        token = str(token)
        if token == "all":
            return list(catalogue.conditions)
        wanted.extend(catalogue.defaults or ["__all__"] if token == "default" else [token])
    if "__all__" in wanted:
        return list(catalogue.conditions)

    def matches(c: dict, w: str) -> bool:
        names = [c["id"], c["code"], c["signal"]["id"], *c["aliases"]]
        return any(n == w or n.startswith(w) for n in names)

    picked = [c for c in catalogue.conditions if any(matches(c, w) for w in wanted)]
    if not picked:
        named = [c["id"] for c in catalogue.conditions if not c.get("generated")]
        raise KeyError(f"no conditions matched {sorted(wanted)}; hand-written conditions are {named} "
                       f"plus {sum(1 for c in catalogue.conditions if c.get('generated'))} generated "
                       f"(see `python -m experiments.conditions`)")
    return picked


if __name__ == "__main__":  # python -m experiments.conditions -> show the catalogue
    cat = load_catalogue()
    print(f"{len(cat.signals)} signals, {len(cat.message_templates)} message templates, "
          f"{len(cat.conditions)} conditions ({len(cat.defaults) or len(cat.conditions)} run by default), "
          f"{len(cat.conscience_levels)} conscience levels\n")
    print("  signals, least compelling first")
    for sid, s in cat.signals.items():
        band = cat.rating_scale.get(s.rating, "")
        print(f"    {s.rating:>2}/{MAX_RATING}  {sid:<18} {s.kind:<8} status={s.status!r:<12} "
              f"headers={list(s.headers) or '-'} body={s.body_style}  {band}")
    print(f"\n  messages: {', '.join(cat.message_templates) or '-'}")
    print("\n  conscience")
    for lid, lv in cat.conscience_levels.items():
        print(f"    {lid:<10} {len(lv.clauses)} clause(s): {', '.join(lv.clauses) or '-'}")
    print("\n  conditions, least compelling first")
    for c in cat.conditions:
        mark = "*" if not cat.defaults or c["id"] in cat.defaults else " "
        print(f" {mark} {c['code']:<5} {c['id']:<34} r={c['rating']:<2} {c['channel']:<14} {c['label']}")
    print("\n  * = runs by default")


# --- promoting a local entry into the shipped catalogue ---------------------

PROMOTABLE = ("signals", "message_templates", "conscience", "conscience_clauses", "conditions")


def _block_bounds(lines: list[str], key: str) -> tuple[int, int]:
    """Where a top-level block's body starts and ends in conditions.yaml.

    Returns (first body line, line to insert before). The end backs up over the
    blank lines and column-0 comments that introduce the *next* section, so an
    insertion lands inside this block rather than on top of the next one's
    banner.
    """
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == f"{key}:")
    except StopIteration:
        raise KeyError(f"conditions.yaml has no top-level {key}: block to add to")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln[:1] not in ("", " ", "#", "\n"):      # a new top-level key
            end = i
            break
    while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    return start + 1, end


def render_entry(key: str, name: str, value: Any) -> str:
    """One catalogue entry as the YAML text that belongs in conditions.yaml."""
    if key == "conditions":
        body = yaml.safe_dump([value], allow_unicode=True, sort_keys=False, width=100)
        return "".join("  " + ln if ln.strip() else ln for ln in body.splitlines(keepends=True))
    body = yaml.safe_dump({name: value}, allow_unicode=True, sort_keys=False, width=100)
    return "".join("  " + ln if ln.strip() else ln for ln in body.splitlines(keepends=True))


def promote(key: str, name: str, path: Optional[Path] = None,
            local_path: Optional[Path] = None) -> dict:
    """Move one entry from the local overlay into conditions.yaml.

    The overlay is private to one checkout; conditions.yaml is what the repo
    ships. Promoting is a text insertion into the right block, so every comment
    and every other line of that file survives byte-for-byte.

    The invariant is that promoting changes *where* a definition lives and
    nothing else: the resolved catalogue is compared before and after, and the
    write is rolled back if they differ at all.
    """
    if key not in PROMOTABLE:
        raise ValueError(f"cannot promote {key!r}; promotable: {', '.join(PROMOTABLE)}")
    path = Path(path or DEFAULT_PATH)
    local_path = Path(local_path or (LOCAL_PATH if path == DEFAULT_PATH
                                     else path.with_name(path.stem + ".local" + path.suffix)))
    base_doc, local_doc = _read(path), _read(local_path)

    if key == "conditions":
        entries = [c for c in (local_doc.get("conditions") or []) if _cond_name(c) == name]
        if not entries:
            raise KeyError(f"{name!r} is not a local condition; there is nothing to move")
        value = entries[0]
        local_after = {**local_doc, "conditions": [c for c in local_doc["conditions"]
                                                   if _cond_name(c) != name]}
    else:
        if name not in (local_doc.get(key) or {}):
            raise KeyError(f"{name!r} is not in conditions.local.yaml under {key}:; "
                           f"only something defined there can be moved")
        if name in (base_doc.get(key) or {}):
            raise ValueError(f"{name!r} is already in conditions.yaml - this is an edit of the "
                             f"shipped definition, not a local addition. Revert it, or edit "
                             f"conditions.yaml directly to change what the repo ships.")
        value = local_doc[key][name]
        local_after = {**local_doc, key: {k: v for k, v in local_doc[key].items() if k != name}}

    before = build_catalogue(merge_docs(base_doc, local_doc))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    _, at = _block_bounds(lines, key)
    insert = render_entry(key, name, value)
    if lines and at > 0 and lines[at - 1].strip():
        insert = "\n" + insert
    new_text = "".join(lines[:at]) + insert + "".join(lines[at:])

    # Prove the move is a move before committing it to disk.
    moved_base = yaml.safe_load(new_text) or {}
    after = build_catalogue(merge_docs(moved_base, local_after))
    _assert_same_catalogue(before, after, name)

    path.write_text(new_text, encoding="utf-8")
    _write_local_doc(local_after, local_path)
    return {"ok": True, "key": key, "id": name, "promoted_to": str(path)}


def _cond_name(c: dict) -> str:
    return auto_id(c.get("signal"), c.get("message", "none"), c.get("channel", "tool_result"))


def _assert_same_catalogue(before: Catalogue, after: Catalogue, name: str) -> None:
    """A promotion must not change a single resolved value - only its home."""
    def shape(cat: Catalogue) -> Any:
        return {
            "conditions": [{k: v for k, v in c.items() if k != "generated"} for c in cat.conditions],
            "signals": {sid: s.as_dict() for sid, s in cat.signals.items()},
            "templates": cat.message_templates,
            "conscience": {lid: lv.as_dict() for lid, lv in cat.conscience_levels.items()},
            "clauses": cat.conscience_clauses,
            "defaults": cat.defaults,
            "contrasts": cat.contrasts,
        }
    if shape(before) != shape(after):
        raise RuntimeError(f"moving {name!r} into conditions.yaml would have changed the "
                           f"catalogue, not just where it lives; nothing was written")


def _write_local_doc(doc: dict, local_path: Path) -> None:
    doc = {k: v for k, v in doc.items() if v not in (None, {}, [])}
    if not doc:
        local_path.unlink(missing_ok=True)
        return
    header = ("# Written by the control room (python -m experiments.ui).\n"
              "# Merged over conditions.yaml, so that file keeps its comments.\n"
              "# A signal, template, clause or conscience level named here replaces the one\n"
              "# there; `remove:` drops one. Hand edits are fine.\n\n")
    local_path.write_text(header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
