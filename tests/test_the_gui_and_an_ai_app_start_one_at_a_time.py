"""The GUI's Run and an AI app's start_study keep one rule between them.

One study runs in a workspace at a time, so each has the machine to itself
and its timings mean what they say. The AI app held to that and the GUI
did not know of it: a window could start a study while an AI app's ran,
or both could start at the same moment. Both now take the workspace's
starting lock and read the workspace's list of runs started there
(`fastmdxplora.runs_here`). Processes here are sleepers carrying a run's
command on their command line, as a run does; nothing is simulated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.mcp import App, Workspace
from fastmdxplora.mcp.tools import plan_id_of
from fastmdxplora.runs_here import (
    RUNS_FILE,
    STARTING_FILE,
    StartRefused,
    record_start,
    running_in,
    starting_in,
)
from tests._mcp_wire import Wire
from tests.test_an_ai_app_reads_and_checks_studies import _structure

STUDY = "systems:\n  - system: ghg.pdb\nsimulation:\n  duration_ns: 5\noutput: ghg_run\n"


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
def sleepers():
    held: list[subprocess.Popen] = []
    yield held
    for process in held:
        process.kill()
        process.wait()


def _command(folder: Path) -> list[str]:
    return [sys.executable, "-m", "fastmdxplora", "explore", "--config",
            str(folder) + ".yml", "--output", str(folder)]


def _sleeping_as(command: list[str], held: list) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                *command[1:]])
    held.append(process)
    return process


@pytest.fixture
def spawns(monkeypatch, sleepers):
    """The process only, as a sleeper: the lock and the list are real."""
    def spawn(self, command, output_dir, dashboard_url):
        process = _sleeping_as(command, sleepers)
        return {"launched": True, "output": str(output_dir), "pid": process.pid,
                "command": command}

    monkeypatch.setattr(DashboardRuntime, "_spawn_now", spawn)
    monkeypatch.setattr("fastmdxplora.mcp.tools._cannot_run_here", lambda config: None)
    monkeypatch.setattr("fastmdxplora.gui.exploration.exploration_environment_error",
                        lambda config: None)
    monkeypatch.setattr("fastmdxplora.mcp.tools.RECORDED_WITHIN_S", 0.0)


def _gui(workspace: Path) -> DashboardRuntime:
    return DashboardRuntime(workspace_root=workspace, exploration_root=workspace)


def _start(wire, workspace, name="ghg.yml"):
    return wire.request("tools/call", {"name": "start_study", "arguments": {
        "config": name, "plan_id": plan_id_of(workspace / name)}})["result"]


def test_the_gui_does_not_start_while_an_ai_app_s_study_runs(workspace, spawns):
    wire = Wire(App(Workspace.at(workspace)).server())
    assert not _start(wire, workspace)["isError"]
    wire.close()
    refused = _gui(workspace)._spawn(_command(workspace / "mine"), workspace / "mine", None)
    assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
    assert refused["error"].startswith(
        "ghg_run (started by an AI app) is running in this workspace. One study runs "
        "here at a time")


def test_an_ai_app_does_not_start_while_the_gui_s_study_runs(workspace, spawns):
    (workspace / "mine").mkdir()
    started = _gui(workspace)._spawn(_command(workspace / "mine"), workspace / "mine", None)
    assert started["launched"] is True
    wire = Wire(App(Workspace.at(workspace)).server())
    refused = _start(wire, workspace)
    wire.close()
    assert refused["isError"]
    assert refused["content"][0]["text"].startswith(
        "mine (started from the GUI) is running in this workspace.")
    assert not (workspace / "ghg_run").exists()


def test_a_run_that_has_ended_is_dropped_and_does_not_count(workspace, sleepers):
    gone = _sleeping_as(_command(workspace / "old"), sleepers)
    with starting_in(workspace):
        record_start(workspace, workspace / "old", gone.pid, _command(workspace / "old"),
                     by="from the GUI")
    assert running_in(workspace, walk=False) == [(workspace / "old", "from the GUI")]
    gone.kill()
    gone.wait()
    assert running_in(workspace, walk=False) == []
    now = _sleeping_as(_command(workspace / "new"), sleepers)
    with starting_in(workspace):
        record_start(workspace, workspace / "new", now.pid, _command(workspace / "new"),
                     by="by an AI app")
    listed = json.loads((workspace / RUNS_FILE).read_text())["runs"]
    assert [run["folder"] for run in listed] == [str(workspace / "new")]


def test_a_process_given_a_run_s_old_number_is_not_that_run(workspace, sleepers):
    # Alive, but not carrying the run's command: the system gave its
    # number to something else.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    sleepers.append(other)
    with starting_in(workspace):
        record_start(workspace, workspace / "old", other.pid, _command(workspace / "old"),
                     by="from the GUI")
    assert running_in(workspace, walk=False) == []


#: Holds a folder's starting lock, as a start under way in another process
#: does, until it is ended.
HOLDER = ("import fcntl, os, sys, time\n"
          "handle = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
          "fcntl.flock(handle, fcntl.LOCK_EX)\n"
          "print('held', flush=True)\n"
          "time.sleep(120)\n")


def _holding(folder: Path, held: list) -> subprocess.Popen:
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(folder / STARTING_FILE)],
                              stdout=subprocess.PIPE, text=True)
    held.append(holder)
    assert holder.stdout.readline().strip() == "held"
    return holder


def _free(folder: Path) -> bool:
    try:
        with starting_in(folder):
            return True
    except StartRefused:
        return False


@pytest.mark.skipif(os.name == "nt", reason="the holder takes a POSIX lock")
def test_a_start_under_way_elsewhere_is_waited_for(workspace, spawns, sleepers):
    holder = _holding(workspace, sleepers)
    refused = _gui(workspace)._spawn(_command(workspace / "mine"), workspace / "mine", None)
    assert refused == {"ok": False, "code": "environment.workspace.run_starting",
                       "error": "Another study is being started in this workspace right "
                                "now. Try again in a minute."}
    # However its holder ends, the lock goes with it: none is left behind.
    holder.kill()
    holder.wait()
    (workspace / "mine").mkdir()
    assert _gui(workspace)._spawn(_command(workspace / "mine"), workspace / "mine",
                                  None)["launched"] is True
    assert _free(workspace)


def test_one_process_s_starters_take_turns(workspace):
    entered = threading.Event()
    trying = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def first():
        with starting_in(workspace):
            # Entered again by the thread holding it, as an AI app's start
            # holds it around the runtime's own.
            with starting_in(workspace):
                order.append("first")
                entered.set()
                release.wait(10)

    def second():
        entered.wait(10)
        trying.set()
        with starting_in(workspace):
            order.append("second")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    trying.wait(10)
    threads[1].join(0.3)
    assert threads[1].is_alive() and order == ["first"]
    release.set()
    for thread in threads:
        thread.join(10)
    assert order == ["first", "second"]
    assert _free(workspace)


def test_the_gui_and_an_ai_app_share_the_folder_the_gui_was_started_in(
        tmp_path, workspace, spawns):
    """As `fastmdx gui` builds its runtime in a folder: started there, new
    studies put in the folder above. An AI app given the folder it was
    started in keeps the rule with it."""
    gui = DashboardRuntime(workspace_root=workspace, exploration_root=workspace.parent)
    (tmp_path / "mine").mkdir()
    assert gui._spawn(_command(tmp_path / "mine"), tmp_path / "mine", None)["launched"]
    wire = Wire(App(Workspace.at(workspace)).server())
    refused = _start(wire, workspace)
    wire.close()
    assert refused["isError"] and "(started from the GUI) is running in this workspace" \
        in refused["content"][0]["text"]


def test_the_gui_and_an_ai_app_share_the_folder_new_studies_go_in(
        workspace, spawns):
    """Started in a folder inside the AI app's workspace, as from a
    checkout of the software there: new studies go in the workspace."""
    started_in = workspace / "checkout"
    started_in.mkdir()
    wire = Wire(App(Workspace.at(workspace)).server())
    assert not _start(wire, workspace)["isError"]
    wire.close()
    gui = DashboardRuntime(workspace_root=started_in, exploration_root=workspace)
    refused = gui._spawn(_command(workspace / "mine"), workspace / "mine", None)
    assert refused["ok"] is False
    assert refused["error"].startswith("ghg_run (started by an AI app) is running")


def test_a_refused_start_writes_nothing_for_the_run(workspace, spawns):
    wire = Wire(App(Workspace.at(workspace)).server())
    assert not _start(wire, workspace)["isError"]
    wire.close()
    refused = _gui(workspace).launch_from_config(None, config={
        "systems": [{"system": "ghg.pdb"}], "output": str(workspace / "second")})
    assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
    assert not (workspace / "second").exists()


def test_a_folder_that_cannot_hold_the_lock_still_starts(workspace, spawns, monkeypatch):
    """As a start went before there was a lock: one that cannot be written
    is held in this process only, not a reason to refuse every start."""
    class NoLockHere:
        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def open(path, *args, **kwargs):
            if str(path).endswith(STARTING_FILE):
                raise PermissionError(13, "Permission denied", str(path))
            return os.open(path, *args, **kwargs)

    monkeypatch.setattr("fastmdxplora.runs_here.os", NoLockHere())
    (workspace / "mine").mkdir()
    started = _gui(workspace)._spawn(_command(workspace / "mine"), workspace / "mine", None)
    assert started["launched"] is True
    assert running_in(workspace, walk=False) == [(workspace / "mine", "from the GUI")]


@pytest.mark.parametrize("start", [
    lambda gui, ws: gui.launch_existing_config(str(ws / "ghg.yml")),
    lambda gui, ws: gui.run_a_fix(0),
    lambda gui, ws: gui.run_windows_again([3]),
])
def test_every_way_the_gui_starts_a_run_keeps_the_rule(workspace, spawns, start):
    wire = Wire(App(Workspace.at(workspace)).server())
    assert not _start(wire, workspace)["isError"]
    wire.close()
    refused = start(_gui(workspace), workspace)
    assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"


def test_a_run_record_that_cannot_be_read_is_no_run(workspace):
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    (workspace / "odd").mkdir()
    (workspace / "odd" / RUN_PROCESS_FILE).write_text("{not json")
    assert running_in(workspace) == []


def test_a_list_that_cannot_be_written_leaves_nothing_half_written(workspace, sleepers,
                                                                    monkeypatch):
    run = _sleeping_as(_command(workspace / "new"), sleepers)

    def no_room(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("fastmdxplora.runs_here.os.replace", no_room)
    with starting_in(workspace):
        record_start(workspace, workspace / "new", run.pid, _command(workspace / "new"),
                     by="from the GUI")
    assert sorted(p.name for p in workspace.iterdir() if p.name.startswith(RUNS_FILE)) == []


def test_a_study_of_several_runs_is_one_run_going(workspace, sleepers):
    """A study of several runs records its process at its top (third
    review, 10-07), and each run inside it its own: one study going, not
    one for each."""
    import json

    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    sweep = workspace / "sweep"
    command = _command(sweep)
    for here in (sweep, sweep / "runs" / "r1"):
        here.mkdir(parents=True, exist_ok=True)
        process = _sleeping_as(command, sleepers)
        (here / RUN_PROCESS_FILE).write_text(json.dumps(
            {"pid": process.pid, "argv": command[1:]}), encoding="utf-8")
    assert running_in(workspace) == [(sweep, "")]
