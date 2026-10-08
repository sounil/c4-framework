"""
mitmproxy addon: the network Chassis.

Reads /chassis/state/trial.json on every request:
    {
      "trial_id": "...",
      "forbidden_hosts": ["analytics-vendor.example.com"],
      "commanded_hosts": ["reports.internal"],
      "allowed_hosts":   ["customers.internal"],
      "signal": {                    # one entry from conditions.yaml: signals:
        "status": 666,               #   200 = permit, 403 = mundane, 6xx = miracle
        "reason": null,              #   reason phrase; emoji are legal
        "headers": {},               #   extra response headers
        "body": null,                #   base JSON fields
        "body_style": "json"         #   json | text | none
      },
      "message": "…do X instead."    # or null
    }

A signal can be copied verbatim out of experiments/conditions.yaml, so the
network Chassis manifests the same way the mock one does. The pre-signal shape
({"status": 666, "message": ...}) still works.

One limitation: mitmproxy's response model holds the status code as an int, so
the ungrammatical signals (a status code that is not a number, e.g. the `glyph`
signal) cannot be expressed here - those conditions need the mock Chassis,
which owns both ends of the wire. Such a signal is rejected loudly rather than
silently downgraded to something the protocol can classify.

For a forbidden host it answers with the configured signal and body without
contacting any upstream. Every request is logged to /logs/proxy.jsonl in the
same shape as experiments/chassis.py's LoggedRequest so the classifier can be
reused.
"""
import json
import time
from pathlib import Path

from mitmproxy import http

STATE = Path("/chassis/state/trial.json")
LOG = Path("/logs/proxy.jsonl")


def _load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"trial_id": "", "forbidden_hosts": [], "commanded_hosts": [], "allowed_hosts": [], "status": 403, "message": None}


def _signal(cfg: dict) -> dict:
    """The forbidden-host signal, from `signal:` or the older flat `status:`."""
    sig = dict(cfg.get("signal") or {})
    if "status" not in sig:
        sig["status"] = cfg.get("status", 403)
    raw = sig["status"]
    if not (isinstance(raw, int) or str(raw).strip().isdigit()):
        raise ValueError(
            f"signal status {raw!r} is not a number: mitmproxy cannot put it in the status-code "
            "position. Run this condition against the mock Chassis (experiments/chassis.py).")
    sig["status"] = int(raw)
    if "reason" not in sig:
        sig["reason"] = {403: "Forbidden", 200: "OK"}.get(sig["status"], "")
    if sig.get("body") is None and sig["status"] == 403:
        sig["body"] = {"error": "Forbidden"}
    elif sig.get("body") is None and sig["status"] < 400:
        sig["body"] = {"ok": True, "note": "request accepted"}
    return sig


def _log(entry: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def request(flow: http.HTTPFlow) -> None:
    cfg = _load()
    host = flow.request.pretty_host
    category = "unknown"
    if host in cfg.get("forbidden_hosts", []):
        category = "forbidden"
    elif host in cfg.get("commanded_hosts", []):
        category = "commanded"
    elif host in cfg.get("allowed_hosts", []):
        category = "allowed"

    status = 200
    if category == "forbidden":
        sig = _signal(cfg)
        status, reason = sig["status"], sig["reason"]
        msg = cfg.get("message")
        style = sig.get("body_style", "json")
        if style == "none":
            payload, ctype = b"", "application/json"
        elif style == "text":
            payload, ctype = (msg or "").encode(), "text/plain"
        else:
            body = dict(sig.get("body") or {})
            if msg:
                body["message"] = msg
            payload = json.dumps(body).encode() if body else b""
            ctype = "application/json"
        headers = {"Content-Type": ctype, **{str(k): str(v) for k, v in (sig.get("headers") or {}).items()}}
        flow.response = http.Response.make(status, payload, headers)
        if reason is not None:
            flow.response.reason = reason
    elif category in ("commanded", "allowed"):
        # Stub the internal services so the sandbox needs no real upstreams.
        # Replace with real routing by giving the proxy an egress network.
        flow.response = http.Response.make(200, json.dumps({"ok": True, "host": host}).encode(),
                                           {"Content-Type": "application/json"})
    else:
        flow.response = http.Response.make(404, json.dumps({"error": "not found"}).encode(),
                                           {"Content-Type": "application/json"})
        status = 404

    _log({"t": time.time(), "trial_id": cfg.get("trial_id", ""), "method": flow.request.method,
          "host": host, "path": flow.request.path, "category": category, "status": status})
