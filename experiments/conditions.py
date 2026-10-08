"""
The condition catalogue: what the world does, and what it says, when the agent
touches the forbidden path.

A condition is assembled from three pieces, each configured independently in
conditions.yaml, so a new kind of miracle is a YAML edit rather than a code
change:

    signal    the *physics* of the manifestation - the status token, the reason
              phrase, extra response headers, the shape of the body.
              (conditions.yaml: `signals:`)
    message   the *words* that ride along, if any. A template over the
              scenario's redirect text.        (conditions.yaml: `message_templates:`)
    channel   where those words reach the agent: tool_result (the Chassis),
              system (the Conscience), user_interrupt (the Crown), or none.

A condition names a signal and a message; an `expand:` block can generate the
whole signal x message grid at once, so adding one signal gives you every
message variant of it for free.

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
# Anything the control room (experiments/ui.py) adds lands here instead of in
# conditions.yaml, so the hand-written file keeps its comments and its ordering.
# Merged on top: a signal or template of the same name wins, conditions and
# contrasts are appended, and default_conditions replaces if present.
LOCAL_PATH = HERE / "conditions.local.yaml"

KINDS = ("permit", "mundane", "miracle")
BODY_STYLES = ("json", "text", "none")
CHANNELS = ("tool_result", "system", "user_interrupt", "none")
SIGNAL_KEYS = {"kind", "status", "reason", "headers", "body", "body_style", "label", "role"}


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

    @property
    def is_miracle(self) -> bool:
        return self.kind == "miracle"

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["is_miracle"] = self.is_miracle
        return d


@dataclass
class Catalogue:
    conditions: list[dict]
    signals: dict[str, Signal]
    message_templates: dict[str, Optional[str]]
    conscience: dict[str, str]
    contrasts: list[tuple[str, str, str]]
    defaults: list[str]

    @property
    def by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.conditions}


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
        glyph:    {status: "\U0001FA84✨", reason: null}
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
    return Signal(id=sid, kind=kind, status=status,
                  reason=str(reason) if reason is not None else None,
                  headers=headers, body=body, body_style=body_style,
                  label=spec.get("label") or _default_signal_label(status, reason),
                  role=spec.get("role"))


def _default_signal_label(status: Union[int, str], reason: Any) -> str:
    return f"HTTP {status}" + (f" {reason}" if reason else "")


def resolve_condition(raw: dict, signals: dict[str, Signal],
                      templates: dict[str, Optional[str]]) -> dict:
    """Turn one YAML condition into the dict the rest of the pipeline reads.

    Legacy form (`forbidden_status` / `forbidden_reason` written inline, with no
    `signal:`) still works: it becomes an anonymous signal.
    """
    cond = dict(raw)
    if "id" not in cond:
        raise ValueError(f"condition without an id: {raw}")
    sid = cond.pop("signal", None)

    if sid is None:
        sig = parse_signal(f"inline:{cond['id']}",
                           {"status": cond.get("forbidden_status", 200),
                            "reason": cond.get("forbidden_reason")})
    else:
        if sid not in signals:
            raise KeyError(f"condition {cond['id']!r}: unknown signal {sid!r}; "
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
        raise KeyError(f"condition {cond['id']!r}: unknown message template {message!r}; "
                       f"known templates: {sorted(templates)}")
    channel = cond.get("channel", "tool_result")
    if channel not in CHANNELS:
        raise ValueError(f"condition {cond['id']!r}: channel must be one of {CHANNELS}, got {channel!r}")

    cond["signal"] = sig.as_dict()
    cond["message"] = message
    cond["channel"] = channel
    # Mirrors kept so older code (and saved runs) that reads these keys still works.
    cond["forbidden_status"] = sig.status
    cond["forbidden_reason"] = sig.reason
    cond.setdefault("label", f"{sig.label}, message={message}")
    cond.setdefault("role", sig.role or "")
    return cond


def expand_conditions(blocks: Any, signals: dict[str, Signal],
                      templates: dict[str, Optional[str]]) -> list[dict]:
    """Generate the signal x message grid described by each `expand:` block.

    Generated ids are "<prefix>_<signal>_<message>", so `--conditions X` selects
    the whole grid and `--conditions X_glyph` selects one signal's row of it.
    """
    out: list[dict] = []
    for blk in blocks or []:
        prefix = str(blk.get("prefix", "X"))
        channel = blk.get("channel", "tool_result")
        sids = blk.get("signals") or sorted(signals)
        msgs = blk.get("messages") or ["none"]
        for sid in sids:
            if sid not in signals:
                raise KeyError(f"expand block {prefix!r}: unknown signal {sid!r}")
            sig = signals[sid]
            for msg in msgs:
                fields = {"prefix": prefix, "signal": sid, "message": msg,
                          "signal_label": sig.label, "channel": channel}
                out.append({
                    "id": str(blk.get("id", "{prefix}_{signal}_{message}")).format(**fields),
                    "label": str(blk.get("label", "{signal_label} + {message}")).format(**fields),
                    "signal": sid,
                    "message": msg,
                    "channel": channel,
                    "role": str(blk.get("role", "Generated cell: {signal_label} carrying the "
                                                "{message} message.")).format(**fields),
                    "generated": True,
                })
    return out


# --- loading / selection ----------------------------------------------------

def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def merge_docs(base: dict, local: dict) -> dict:
    """Overlay a local catalogue onto the main one. See LOCAL_PATH."""
    if not local:
        return base
    doc = dict(base)
    for key in ("signals", "message_templates"):
        doc[key] = {**(base.get(key) or {}), **(local.get(key) or {})}
    for key in ("conditions", "expand", "contrasts"):
        merged = list(base.get(key) or []) + list(local.get(key) or [])
        if key == "conditions":   # a local condition replaces the one it shares an id with
            local_ids = {c.get("id") for c in (local.get(key) or [])}
            merged = [c for c in (base.get(key) or []) if c.get("id") not in local_ids] + list(local.get(key) or [])
        doc[key] = merged
    for key in ("default_conditions", "conscience"):
        if local.get(key):
            doc[key] = local[key]
    return doc


def load_catalogue(path: Optional[Path] = None) -> Catalogue:
    path = Path(path or DEFAULT_PATH)
    local = LOCAL_PATH if path == DEFAULT_PATH else path.with_name(path.stem + ".local" + path.suffix)
    doc = merge_docs(_read(path), _read(local))
    signals = {sid: parse_signal(sid, spec) for sid, spec in (doc.get("signals") or {}).items()}
    templates = {k: v for k, v in (doc.get("message_templates") or {}).items() if k != "none"}

    raw_conditions = list(doc.get("conditions") or [])
    raw_conditions += expand_conditions(doc.get("expand"), signals, templates)
    conditions = [resolve_condition(c, signals, templates) for c in raw_conditions]

    seen: set[str] = set()
    for c in conditions:
        if c["id"] in seen:
            raise ValueError(f"duplicate condition id {c['id']!r}")
        seen.add(c["id"])

    contrasts = [tuple(x) if not isinstance(x, dict) else (x["a"], x["b"], x.get("desc", ""))
                 for x in (doc.get("contrasts") or [])]
    defaults = list(doc.get("default_conditions") or [])
    return Catalogue(conditions=conditions, signals=signals, message_templates=templates,
                     conscience=doc.get("conscience") or {"weak": "", "strong": ""},
                     contrasts=contrasts, defaults=defaults)


def select(catalogue: Catalogue, ids: Optional[list[str]]) -> list[dict]:
    """Pick conditions by id or id-prefix.

    "default" -> the `default_conditions:` list in the YAML (everything, if it
    is empty), so narrowing a run never means commenting conditions out.
    "all"     -> every condition, generated grids included.
    """
    if not ids or ids == ["default"]:
        ids = catalogue.defaults or ["all"]
    if "all" in ids:
        return list(catalogue.conditions)
    wanted = set(ids)
    picked = [c for c in catalogue.conditions
              if c["id"] in wanted or any(c["id"].startswith(w) for w in wanted)]
    if not picked:
        named = [c["id"] for c in catalogue.conditions if not c.get("generated")]
        raise KeyError(f"no conditions matched {sorted(wanted)}; conditions are {named} "
                       f"plus {sum(1 for c in catalogue.conditions if c.get('generated'))} generated "
                       f"(see `python -m experiments.conditions`)")
    return picked


if __name__ == "__main__":  # python -m experiments.conditions -> show the catalogue
    cat = load_catalogue()
    print(f"{len(cat.signals)} signals, {len(cat.message_templates)} message templates, "
          f"{len(cat.conditions)} conditions ({len(cat.defaults) or len(cat.conditions)} run by default)\n")
    for sid, s in cat.signals.items():
        print(f"  signal   {sid:<16} {s.kind:<8} status={s.status!r:<10} "
              f"reason={s.reason!r} headers={list(s.headers) or '-'} body={s.body_style}")
    print()
    for name in cat.message_templates:
        print(f"  message  {name}")
    print()
    for c in cat.conditions:
        mark = "*" if not cat.defaults or c["id"] in cat.defaults else " "
        print(f" {mark} {c['id']:<26} {c['signal']['id']:<18} {c['message']:<12} {c['channel']}")
