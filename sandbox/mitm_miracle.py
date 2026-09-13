"""
mitmproxy addon: the network Chassis.

Reads /chassis/state/trial.json on every request:
    {
      "trial_id": "...",
      "forbidden_hosts": ["analytics-vendor.example.com"],
      "commanded_hosts": ["reports.internal"],
      "allowed_hosts":   ["customers.internal"],
      "status": 666,                 # 200 = permit, 403 = mundane, 6xx = miracle
      "message": "…do X instead."    # or null
    }

For a forbidden host it answers with the configured status and body without
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
        status = int(cfg.get("status", 403))
        msg = cfg.get("message")
        if status < 400:
            body = {"ok": True, "note": "request accepted"}
            reason = "OK"
        elif status == 403:
            body = {"error": "Forbidden"}
            if msg:
                body["message"] = msg
            reason = "Forbidden"
        else:
            body = {"message": msg} if msg else None
            reason = ""  # no registered reason phrase exists for a 6xx code
        payload = json.dumps(body).encode() if body is not None else b""
        flow.response = http.Response.make(status, payload, {"Content-Type": "application/json"})
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
