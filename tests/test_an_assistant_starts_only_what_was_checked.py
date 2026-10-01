"""An assistant starts a study only from a checked file, with the person's go-ahead.

A study can hold a machine's GPU for days, and the model asking to start it
can be wrong or be told what to ask by a file it read. So `start_study` runs
a config file only, only while its `plan_id` is the one `check_study` gave
for it, only where no other study is running, never into a folder already
used; and where the client can put a question to the person, they are asked,
in either era of the protocol. `stop_study` stops only a run it can identify
as the study's. A server started `--read-only` offers neither.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime, _process_alive
from fastmdxplora.mcp import App, Workspace
from fastmdxplora.mcp.tools import plan_id_of
from fastmdxplora.orchestrator import RUN_PROCESS_FILE
from tests._mcp_wire import Wire, text_of
from tests.test_an_assistant_reads_and_checks_studies import _structure, _study

STUDY = "systems:\n  - system: ghg.pdb\nsimulation:\n  duration_ns: 5\noutput: ghg_run\n"
ELICIT = {"elicitation": {"form": {}}}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    (root / "ghg.yml").write_text(STUDY)
    return root


@pytest.fixture
def spawned(monkeypatch):
    """What would have been run, instead of running it; and a machine that
    has what it needs, whatever this one has."""
    commands: list[dict] = []

    def spawn(self, command, output_dir, dashboard_url):
        commands.append({"command": command, "output": output_dir})
        return {"launched": True, "output": str(output_dir), "pid": 4242, "command": command}

    monkeypatch.setattr(DashboardRuntime, "_spawn", spawn)
    monkeypatch.setattr("fastmdxplora.mcp.tools._cannot_run_here", lambda config: None)
    monkeypatch.setattr("fastmdxplora.gui.exploration.exploration_environment_error",
                        lambda config: None)
    return commands


@pytest.fixture
def wire(workspace):
    wire = Wire(App(Workspace.at(workspace)).server())
    yield wire
    wire.close()


def start(wire, workspace, *, plan_id=None, capabilities=None, **more):
    plan_id = plan_id or plan_id_of(workspace / "ghg.yml")
    return wire.request("tools/call", {
        "name": "start_study", "arguments": {"config": "ghg.yml", "plan_id": plan_id},
        **more}, capabilities=capabilities)["result"]


def _sleeper(study) -> subprocess.Popen:
    """A process holding a study, as a run does: its record names the
    process, and the process names the study."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                str(study)], start_new_session=True)
    (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": process.pid}))
    return process


class TestWhatRuns:
    def test_a_checked_file_runs_where_its_config_says(self, wire, workspace, spawned):
        said = start(wire, workspace)["content"][0]["text"]
        assert said.startswith("Started ghg_run (process 4242). It runs on its own")
        assert said.endswith("Its log is ghg_run/exploration.log.")
        command = spawned[0]["command"]
        assert command[1:4] == ["-m", "fastmdxplora", "explore"]
        assert command[-2:] == ["--output", str(workspace / "ghg_run")]
        assert (workspace / "ghg_run" / "exploration.yml").is_file()

    def test_text_a_changed_file_or_a_wrong_plan_id_is_not_run(self, wire, workspace, spawned):
        text = wire.request("tools/call", {"name": "start_study", "arguments": {
            "config": STUDY, "plan_id": "x"}})["result"]
        assert text["isError"] and "save it first" in text["content"][0]["text"]
        checked = plan_id_of(workspace / "ghg.yml")
        (workspace / "ghg.yml").write_text(STUDY.replace("duration_ns: 5", "duration_ns: 500"))
        changed = start(wire, workspace, plan_id=checked)
        assert changed["isError"]
        assert changed["content"][0]["text"].startswith("ghg.yml is not the file that was "
                                                        "checked: its plan_id is ")
        assert spawned == []

    def test_a_used_folder_or_a_running_study_stops_it(self, wire, workspace, spawned):
        (workspace / "ghg_run").mkdir()
        (workspace / "ghg_run" / "keep.txt").write_text("mine")
        used = start(wire, workspace)
        assert used["isError"] and "is in use already" in used["content"][0]["text"]
        (workspace / "ghg_run" / "keep.txt").unlink()
        busy = _study(workspace / "busy", duration=10, means={}, started="2026-09-02T10:00:00+00:00")
        sleeper = _sleeper(busy)
        try:
            refused = start(wire, workspace)
            assert refused["isError"]
            assert refused["content"][0]["text"].startswith("busy is running here. One study "
                                                            "runs at a time")
        finally:
            sleeper.kill()
            sleeper.wait()
        assert spawned == []

    def test_a_continuation_runs_in_the_study_it_continues(self, wire, workspace, spawned):
        _study(workspace / "ubq", duration=10, means={}, started="2026-09-02T10:00:00+00:00")
        (workspace / "more.yml").write_text("simulation:\n  resume_from: ubq\n  extra_ns: 5\n")
        said = text_of(wire.request("tools/call", {"name": "start_study", "arguments": {
            "config": "more.yml", "plan_id": plan_id_of(workspace / "more.yml")}}))
        assert said.startswith("Started ubq (process 4242).")
        assert spawned[0]["output"] == workspace / "ubq"


class TestThePersonIsAsked:
    def test_modern_asks_with_the_plan_and_starts_on_a_tick(self, wire, workspace, spawned):
        first = start(wire, workspace, capabilities=ELICIT)
        assert first["resultType"] == "input_required"
        asked = first["inputRequests"]["start"]["params"]
        assert asked["message"].startswith("Start the study in ghg.yml on this machine?\n"
                                           "  System: ghg.pdb\n")
        assert "\nResults: ghg_run" in asked["message"]
        assert asked["requestedSchema"]["required"] == ["go"]
        assert spawned == []
        yes = {"start": {"action": "accept", "content": {"go": True}}}
        done = start(wire, workspace, capabilities=ELICIT, inputResponses=yes,
                     requestState=first["requestState"])
        assert done["content"][0]["text"].startswith("Started ghg_run")
        assert len(spawned) == 1

    @pytest.mark.parametrize("answer", [{"action": "decline"}, {"action": "cancel"},
                                        {"action": "accept", "content": {"go": False}}])
    def test_anything_but_a_tick_starts_nothing(self, wire, workspace, spawned, answer):
        first = start(wire, workspace, capabilities=ELICIT)
        done = start(wire, workspace, capabilities=ELICIT, inputResponses={"start": answer},
                     requestState=first["requestState"])
        assert done["content"][0]["text"] == "Not started: the person did not go ahead."
        assert not done["isError"] and spawned == []

    def test_legacy_asks_on_the_wire(self, wire, workspace, spawned):
        wire.initialize("2025-06-18", {"elicitation": {}})
        result = wire.request("tools/call", {"name": "start_study", "arguments": {
            "config": "ghg.yml", "plan_id": plan_id_of(workspace / "ghg.yml")}}, modern=False,
            answer=lambda asked: {"action": "accept", "content": {"go": True}})["result"]
        assert wire.seen[0]["method"] == "elicitation/create"
        assert result["content"][0]["text"].startswith("Started ghg_run")


class TestStopping:
    def test_a_running_study_is_stopped_once_the_person_agrees(self, wire, workspace):
        study = _study(workspace / "busy", duration=10, means={},
                       started="2026-09-02T10:00:00+00:00")
        sleeper = _sleeper(study)
        try:
            first = wire.request("tools/call", {"name": "stop_study", "arguments": {
                "study": "busy"}}, capabilities=ELICIT)["result"]
            assert first["inputRequests"]["stop"]["params"]["message"].startswith(
                "Stop the study busy?")
            no = wire.request("tools/call", {
                "name": "stop_study", "arguments": {"study": "busy"},
                "inputResponses": {"stop": {"action": "decline"}},
                "requestState": first["requestState"]}, capabilities=ELICIT)["result"]
            assert no["content"][0]["text"] == "Not stopped: the person did not go ahead."
            assert sleeper.poll() is None
            said = text_of(wire.request("tools/call", {"name": "stop_study",
                                                       "arguments": {"study": "busy"}}))
            assert said.startswith("Asked busy to stop.")
            assert sleeper.wait(timeout=20) != 0
        finally:
            if sleeper.poll() is None:
                sleeper.kill()

    def test_a_study_with_no_run_going_is_said_so(self, wire, workspace):
        study = _study(workspace / "done", duration=10, means={},
                       started="2026-09-02T10:00:00+00:00")
        (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": os.getpid()}))
        result = wire.request("tools/call", {"name": "stop_study",
                                             "arguments": {"study": "done"}})["result"]
        assert result["isError"]
        assert result["content"][0]["text"] == "No run of done is going on this machine."


def test_a_read_only_server_offers_neither(workspace):
    wire = Wire(App(Workspace.at(workspace), runs=False).server())
    names = [t["name"] for t in wire.request("tools/list")["result"]["tools"]]
    assert "start_study" not in names and "stop_study" not in names
    assert "check_study" in names
    assert "studies are started from FastMDXplora itself" in \
        wire.request("server/discover")["result"]["instructions"]
    assert wire.request("tools/call", {"name": "start_study", "arguments": {}})["error"][
        "code"] == -32602
    wire.close()


@pytest.mark.slow
def test_a_real_run_starts_records_itself_and_stops(workspace):
    """The whole way: `fastmdx explore` started, found by its record, stopped."""
    from fastmdxplora.dependencies import missing_dependencies

    if missing_dependencies(include_analysis=True):
        pytest.skip("this machine cannot run a study")
    wire = Wire(App(Workspace.at(workspace)).server())
    try:
        said = text_of(wire.request("tools/call", {"name": "start_study", "arguments": {
            "config": "ghg.yml", "plan_id": plan_id_of(workspace / "ghg.yml")}}))
        assert said.startswith("Started ghg_run")
        record = workspace / "ghg_run" / RUN_PROCESS_FILE
        deadline = time.monotonic() + 60
        while not record.is_file() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert record.is_file()
        pid = json.loads(record.read_text())["pid"]
        assert "ghg_run" in text_of(wire.request("tools/call", {"name": "list_studies",
                                                               "arguments": {}}))
        assert text_of(wire.request("tools/call", {"name": "stop_study", "arguments": {
            "study": "ghg_run"}})).startswith("Asked ghg_run to stop.")
        deadline = time.monotonic() + 60
        while _process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _process_alive(pid), "the run did not stop"
    finally:
        wire.close()
