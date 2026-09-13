"""Key-free tests: the Chassis really returns 6xx, the loop runs, classification is sane, stats behave."""
import json
import urllib.error
import urllib.request

import pytest

from experiments.agent import TrialSpec, run_trial
from experiments.chassis import TRIAL_HEADER, Chassis, Endpoint, ForbiddenResponse, TrialConfig
from experiments.classify import classify
from experiments.providers import make_provider
from experiments.run import load_conditions, load_scenarios
from experiments.stats import fisher_exact, wilson_ci


@pytest.fixture(scope="module")
def chassis():
    c = Chassis().start()
    yield c
    c.stop()


def test_chassis_returns_666(chassis):
    chassis.register(TrialConfig("t1", Endpoint("POST", "/x/"), Endpoint("POST", "/y"), [],
                                 ForbiddenResponse(666, None, {"message": "no"})))
    req = urllib.request.Request(chassis.base_url + "/x/1", method="POST", data=b"{}", headers={TRIAL_HEADER: "t1"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req)
    assert ei.value.code == 666
    assert json.loads(ei.value.read())["message"] == "no"
    assert chassis.log_for("t1")[0].category == "forbidden"


def _spec(cond_id, scenario_id="exfil_report", conscience="weak"):
    scenarios = {s["id"]: s for s in load_scenarios(["all"])}
    conds, templates, levels = load_conditions(["all"])
    cond = next(c for c in conds if c["id"] == cond_id)
    return TrialSpec(scenario=scenarios[scenario_id], condition=cond, conscience_level=conscience,
                     conscience_text=levels[conscience], message_templates=templates, seed=0), cond


@pytest.mark.parametrize("personality,cond_id,expect_complied,expect_persisted", [
    ("zealot", "C4_miracle_msg", True, False),
    ("zealot", "C2_deny_msg", False, True),
    ("obedient", "C2_deny_msg", True, False),
    ("obedient", "C3_miracle_silent", False, False),   # abandons
    ("stubborn", "C4_miracle_msg", False, True),
    ("obedient", "C7_crown_msg", True, False),
])
def test_scripted_episodes(chassis, personality, cond_id, expect_complied, expect_persisted):
    spec, cond = _spec(cond_id)
    rec = run_trial(make_provider(f"scripted:{personality}"), chassis, spec)
    assert rec["error"] is None
    out = classify(rec, cond)
    assert out["triggered"]
    assert out["complied"] is expect_complied
    assert out["persisted"] is expect_persisted


def test_permit_baseline_records_harm(chassis):
    spec, cond = _spec("C0_permit")
    rec = run_trial(make_provider("scripted:obedient"), chassis, spec)
    out = classify(rec, cond)
    assert out["attempted_forbidden"] and out["harm_occurred"] and not out["triggered"]


def test_wrath_template_and_618(chassis):
    spec, _ = _spec("C5_miracle_wrath")
    assert spec.redirect_message.startswith("STOP.")
    spec618, cond618 = _spec("C8_miracle_msg_618")
    rec = run_trial(make_provider("scripted:zealot"), chassis, spec618)
    assert any(e["status"] == 618 for e in rec["chassis_log"])
    assert classify(rec, cond618)["complied"]


def test_stats():
    lo, hi = wilson_ci(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert abs(fisher_exact(10, 0, 0, 10) - 1.08e-5) < 1e-5
    assert fisher_exact(5, 5, 5, 5) == pytest.approx(1.0)
