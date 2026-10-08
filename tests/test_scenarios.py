"""The scenario editor: what it accepts, and what it refuses before writing."""
import pytest
import yaml

from experiments import scenario_store as store
from experiments.run import load_scenarios

GOOD = """id: {sid}
title: A test scenario
harm_class: test

persona: |
  You are a test assistant.

task: |
  Do the thing with DELETE /things/one.

api_doc: |
  GET /things -> list

forbidden:
  method: DELETE
  path: /things/
  example_path: /things/one

commanded:
  method: POST
  path: /things/archive
  example_path: /things/archive

redirect: >-
  Do not delete. Use POST /things/archive instead.
"""


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    d = tmp_path / "scenarios"
    d.mkdir()
    monkeypatch.setattr(store, "SCENARIOS", d)
    monkeypatch.setattr(store, "TRASH", d / ".trash")
    return d


def test_every_shipped_scenario_validates():
    """The editor's checks have to pass on the files already in the repo, or the
    first save of an untouched scenario would be refused."""
    for entry in store.read_all():
        assert entry["valid"], f"{entry['id']}: {entry['error']}"


def test_save_round_trips_and_keeps_the_text_as_typed(scratch):
    text = GOOD.format(sid="t_one")
    store.save("t_one", text)
    assert (scratch / "t_one.yaml").read_text(encoding="utf-8") == text


@pytest.mark.parametrize("mangle,why", [
    (lambda t: t.replace("redirect: >-", "redirekt: >-"), "missing redirect"),
    (lambda t: t.replace("id: t_one", "id: other"), "id does not match the file"),
    (lambda t: t.replace("method: DELETE", "method: YEET"), "not an HTTP method"),
    (lambda t: t.replace("path: /things/\n", "path: things/\n"), "path without a leading slash"),
    (lambda t: t.replace("POST /things/archive instead.", "something else instead."), "redirect names no commanded path"),
    (lambda t: "  not: [valid", "not YAML at all"),
])
def test_a_bad_edit_is_refused_and_nothing_is_written(scratch, mangle, why):
    store.save("t_one", GOOD.format(sid="t_one"))
    before = (scratch / "t_one.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        store.save("t_one", mangle(GOOD.format(sid="t_one")))
    assert (scratch / "t_one.yaml").read_text(encoding="utf-8") == before, why


def test_the_forbidden_and_commanded_paths_must_differ(scratch):
    text = GOOD.format(sid="t_one").replace("path: /things/archive", "path: /things/")
    with pytest.raises(ValueError, match="same"):
        store.save("t_one", text)


def test_create_and_duplicate(scratch):
    store.create("t_one", "First")
    assert (scratch / "t_one.yaml").exists()
    with pytest.raises(ValueError, match="already exists"):
        store.create("t_one")
    store.save("t_one", GOOD.format(sid="t_one"))
    store.duplicate("t_one", "t_two")
    doc = yaml.safe_load((scratch / "t_two.yaml").read_text(encoding="utf-8"))
    assert doc["id"] == "t_two" and doc["task"] == yaml.safe_load(GOOD.format(sid="t_one"))["task"]


def test_the_shipped_template_is_itself_a_valid_scenario(scratch):
    """A new scenario opens on the template; if that did not validate, the first
    save would be refused for reasons the user did not cause."""
    store.create("t_new")
    entry = next(e for e in store.read_all() if e["id"] == "t_new")
    assert entry["valid"], entry["error"]


def test_archiving_keeps_the_file_and_refuses_to_empty_the_set(scratch):
    store.save("t_one", GOOD.format(sid="t_one"))
    store.save("t_two", GOOD.format(sid="t_two"))
    store.archive("t_one")
    assert not (scratch / "t_one.yaml").exists()
    assert list((scratch / ".trash").glob("t_one.*.yaml")), "archived, not destroyed"
    with pytest.raises(ValueError, match="last scenario"):
        store.archive("t_two")


def test_saving_over_a_scenario_keeps_the_previous_text(scratch):
    store.save("t_one", GOOD.format(sid="t_one"))
    store.save("t_one", GOOD.format(sid="t_one").replace("A test scenario", "Edited"))
    kept = sorted((scratch / ".trash").glob("t_one.*.yaml"))
    assert kept and "A test scenario" in kept[-1].read_text(encoding="utf-8")


def test_a_bad_id_never_reaches_the_filesystem(scratch):
    for bad in ["../escape", "Has Spaces", "UPPER", ""]:
        with pytest.raises(ValueError):
            store.save(bad, GOOD.format(sid="t_one"))
    assert not list(scratch.glob("*.yaml"))


def test_the_harness_reads_what_the_editor_writes(scratch, monkeypatch):
    """The editor and the runner must agree on where scenarios live and what
    they mean - the run is the only reader that matters."""
    import experiments.run as run_mod
    monkeypatch.setattr(run_mod, "HERE", scratch.parent)
    store.save("t_one", GOOD.format(sid="t_one"))
    loaded = load_scenarios(["t_one"])
    assert len(loaded) == 1 and loaded[0]["forbidden"]["path"] == "/things/"
