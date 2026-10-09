"""Studies started on this computer share its GPUs where they fit.

The GUI's Run, an AI app's start_study and the Agent start a study by one
rule (`fastmdxplora.runs_here.may_start`): a study that simulates on a GPU
of this computer starts beside the others where it fits, on the GPU with
the fewest studies from here and then the most free memory, and is given
that GPU; one that does not fit is refused with the numbers. Work on the
CPU, a study whose GPU is not chosen here, and every study on a computer
whose GPUs nvidia-smi does not read, waits for the other such work, and no
two runs write one folder. What a run holds is learned from studies here
that completed with their runs one at a time on their GPU. ``nvidia-smi``
is a stand-in reading files; processes are sleepers; nothing is simulated.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fastmdxplora import gpu_here
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.remote.gpu_room import HERE, learn, measured
from fastmdxplora.runs_here import RUNS_FILE, going_in
from tests.test_remote_from_every_interface import UUID_0, UUID_1, _peptide

#: Whether nvidia-smi is installed, as read before the suite says it is not.
NVIDIA_SMI_HERE = gpu_here.nvidia_smi_here

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in nvidia-smi is sh")

STAND_IN = r'''#!/bin/sh
case "$*" in
  *--query-gpu=*) cat "$FMDX_TEST_GPUS" ;;
  *--query-compute-apps=*) cat "$FMDX_TEST_APPS" 2>/dev/null ;;
esac
'''


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _peptide(root / "top.pdb")
    return root


@pytest.fixture
def gpus(tmp_path, monkeypatch):
    """This computer's GPUs, as a stand-in nvidia-smi reads them: call with
    rows of (index, uuid, total, free) in MB."""
    tools = tmp_path / "gpu-bin"
    tools.mkdir()
    tool = tools / "nvidia-smi"
    tool.write_text(STAND_IN)
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    # The suite's computer reads no GPU (conftest); this one reads these.
    monkeypatch.setattr(gpu_here, "nvidia_smi_here", NVIDIA_SMI_HERE)
    monkeypatch.setenv("FMDX_TEST_GPUS", str(tmp_path / "gpus.csv"))
    monkeypatch.setenv("FMDX_TEST_APPS", str(tmp_path / "apps.csv"))

    def given(*rows: tuple[int, str, int, int]) -> None:
        (tmp_path / "gpus.csv").write_text("".join(
            f"{i}, {uuid}, Stand-in GPU, {total}, {total - free}, {free}, 7\n"
            for i, uuid, total, free in rows))
    return given


@pytest.fixture
def started(monkeypatch):
    """Each process started, as a sleeper, with the environment it was
    given; and each sampler asked for, not started."""
    held: list[tuple[subprocess.Popen, dict]] = []
    samplers: list[tuple] = []

    def spawn(self, command, output_dir, dashboard_url, env=None):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                    *command[1:]])
        held.append((process, dict(env or {})))
        return {"launched": True, "output": str(output_dir), "pid": process.pid,
                "command": command}

    monkeypatch.setattr(DashboardRuntime, "_spawn_now", spawn)
    monkeypatch.setattr("fastmdxplora.gpu_here.start_sampler",
                        lambda *args: samplers.append(args))
    monkeypatch.setattr("fastmdxplora.gui.exploration.exploration_environment_error",
                        lambda config: None)
    monkeypatch.setattr("fastmdxplora.mcp.tools._cannot_run_here", lambda config: None)
    yield held, samplers
    for process, _ in held:
        process.kill()
        process.wait()


def _study(workspace: Path, name: str, *, cpu: bool = False) -> Path:
    path = workspace / f"{name}.yml"
    path.write_text(f"systems:\n  - system: {workspace / 'top.pdb'}\n"
                    "simulation:\n  duration_ns: 1\n"
                    + ("  platform: CPU\n" if cpu else "") + f"output: {name}\n")
    return path


def _command(folder: Path) -> list[str]:
    return [sys.executable, "-m", "fastmdxplora", "explore", "--config",
            str(folder) + ".yml", "--output", str(folder)]


def _start(workspace: Path, name: str, *, cpu: bool = False) -> dict:
    window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    config = _study(workspace, name, cpu=cpu)
    return window._spawn(_command(workspace / name), workspace / name, None,
                         config_path=config)


def _needs(mb: int) -> None:
    """One run measured here, far larger than the studies started, which
    are said to need what it held (and 15% more)."""
    learn(HERE, job="measured", particles=10 ** 6, peak_mb=mb, gpu="Stand-in GPU")


class TestAStudyOnAGpu:
    def test_a_second_goes_beside_the_first_on_the_gpu_with_room(
            self, workspace, gpus, started):
        """One study at a time held, a second window's study was refused
        while the GPUs had room for both."""
        held, samplers = started
        gpus((0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 20000))
        _needs(2000)
        first = _start(workspace, "first")
        assert first["launched"] and held[0][1] == {"CUDA_VISIBLE_DEVICES": UUID_0}
        second = _start(workspace, "second")
        assert second["launched"]
        # The first is kept back on GPU 0 until it holds what it needs: the
        # second goes to GPU 1, with no study from here on it.
        assert held[1][1] == {"CUDA_VISIBLE_DEVICES": UUID_1}
        assert second["shared"][-1] == (
            "first (started from the GUI) is running here too: this study shares the "
            "computer, and each runs slower than alone.")
        runs = going_in(workspace, walk=False)
        assert [run["gpu"]["uuids"] for run in runs] == [[UUID_0], [UUID_1]]
        assert [run["gpu"]["need_mb"] for run in runs] == [2300, 2300]
        # What each holds is read while it runs.
        assert [args[0] for args in samplers] == [held[0][0].pid, held[1][0].pid]

    def test_one_that_does_not_fit_is_refused_with_the_numbers(
            self, workspace, gpus, started):
        held, _ = started
        gpus((0, UUID_0, 24000, 1000))
        _needs(2000)
        refused = _start(workspace, "big")
        assert refused["ok"] is False and refused["code"] == "environment.workspace.no_room"
        assert refused["error"].startswith(
            "This study needs about 2,300 MB of GPU memory (from 1 run in mixed "
            "precision measured on this computer")
        assert "GPU 0 on this computer (Stand-in GPU) has 1,000 MB free for it now" in (
            refused["error"])
        assert refused["error"].endswith(
            "Start it once a study here has ended, or send it to one of your machines.")
        assert held == []

    def test_a_refused_launch_takes_back_the_folder_it_made(self, workspace, gpus, started):
        """Refused as it started, a launch left its config in the folder it
        made, which then could not be given again (not empty)."""
        gpus((0, UUID_0, 24000, 1000))
        _needs(2000)
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        config = {"systems": [{"system": str(workspace / "top.pdb")}],
                  "simulation": {"duration_ns": 1}, "output": str(workspace / "later")}
        refused = window.launch_from_config(None, config=config)
        assert refused["ok"] is False and refused["code"] == "environment.workspace.no_room"
        assert not (workspace / "later").exists()

    def test_with_the_need_not_known_it_starts_and_learns(self, workspace, gpus, started):
        held, samplers = started
        gpus((0, UUID_0, 24000, 500))
        assert _start(workspace, "unknown")["launched"]
        assert held[0][1] == {"CUDA_VISIBLE_DEVICES": UUID_0}
        assert samplers[0][2].learn is True


class TestWorkOnTheCpu:
    def test_waits_for_other_work_on_the_cpu_and_not_for_a_gpu_study(
            self, workspace, gpus, started):
        held, _ = started
        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "on_gpu")["launched"]
        assert _start(workspace, "cpu_one", cpu=True)["launched"]
        assert held[1][1] == {}
        refused = _start(workspace, "cpu_two", cpu=True)
        assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
        assert refused["error"].startswith(
            "cpu_one (started from the GUI) is running in this workspace.")
        # A study on the GPU still starts beside the work on the CPU.
        assert _start(workspace, "gpu_two")["launched"]

    def test_without_gpus_nvidia_smi_reads_every_study_waits(self, workspace, started):
        assert _start(workspace, "first")["launched"]
        refused = _start(workspace, "second")
        assert refused["code"] == "environment.workspace.run_going"

    def test_an_analysis_again_waits_beside_a_study_on_the_cpu(
            self, workspace, gpus, started):
        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "cpu_one", cpu=True)["launched"]
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        refused = window._others_running(on_cpu=True)
        assert refused is not None and refused["code"] == "environment.workspace.run_going"


class TestAnAIApp:
    def test_is_told_the_gpu_and_that_the_computer_is_shared(
            self, workspace, gpus, started):
        from fastmdxplora.mcp import App, Workspace
        from fastmdxplora.mcp.tools import plan_id_of
        from tests._mcp_wire import Wire

        gpus((0, UUID_0, 24000, 23000))
        _needs(2000)
        assert _start(workspace, "first")["launched"]
        config = _study(workspace, "second")
        wire = Wire(App(Workspace.at(workspace)).server())
        try:
            asked = wire.request("tools/call", {"name": "start_study", "arguments": {
                "config": config.name, "plan_id": plan_id_of(config)}},
                capabilities={"elicitation": {"form": {}}})["result"]
            message = asked["inputRequests"]["start"]["params"]["message"]
        finally:
            wire.close()
        assert ("GPU 0 (Stand-in GPU): 23,000 MB free of 24,000 MB, 7% busy; 2,300 MB kept "
                "back for 1 study from here not yet holding what it needs") in message
        assert ("Runs on GPU 0; one run needs about 2,300 MB (from 1 run in mixed precision "
                "measured on this computer") in message
        assert "first (started from the GUI) is running here too: this study shares " in (
            message)

    def test_one_that_does_not_fit_is_refused_before_asking(self, workspace, gpus, started):
        from fastmdxplora.mcp import App, Workspace
        from fastmdxplora.mcp.tools import plan_id_of
        from tests._mcp_wire import Wire

        gpus((0, UUID_0, 24000, 1000))
        _needs(2000)
        config = _study(workspace, "big")
        wire = Wire(App(Workspace.at(workspace)).server())
        try:
            done = wire.request("tools/call", {"name": "start_study", "arguments": {
                "config": config.name, "plan_id": plan_id_of(config)}},
                capabilities={"elicitation": {"form": {}}})["result"]
        finally:
            wire.close()
        assert done["isError"] and "inputRequests" not in done
        assert "1,000 MB free for it now" in done["content"][0]["text"]


class TestWhatIsLearned:
    @staticmethod
    def _ended(folder: Path, *, status: str, seconds: float, since: float) -> None:
        from fastmdxplora.gui.telemetry import TelemetryWriter

        (folder / "simulation").mkdir(parents=True)
        TelemetryWriter(simulation_dir=str(folder / "simulation"), total_steps=1000
                        ).write_status(stage="production", status=status, current_step=1000)
        cost = folder / "simulation" / "cost.json"
        cost.write_text('{"particles": 5000, "platform": "CUDA", "precision": "mixed", '
                        f'"seconds": {seconds}}}')
        os.utime(cost, (since + 1, since + 1))

    def _sampled(self, tmp_path, gpus, *, status="completed", seconds=600.0):
        from fastmdxplora import gpu_here

        gpus((0, UUID_0, 24000, 20000))
        run = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"],
                               start_new_session=True)
        (tmp_path / "apps.csv").write_text(f"{UUID_0}, {run.pid}, 1500\n"
                                           f"{UUID_0}, 1, 9000\n")
        folder = tmp_path / "study"
        since = time.time() - 5
        self._ended(folder, status=status, seconds=seconds, since=since)
        peak = []
        sampler = threading.Thread(target=lambda: peak.append(gpu_here.sample(
            run.pid, folder, since, learn=True, gpu="Stand-in GPU", precision="mixed",
            every_s=0.05)))
        sampler.start()
        run.wait()
        sampler.join(timeout=20)
        return peak

    def test_a_run_alone_on_its_gpu_that_completed(self, workspace, tmp_path, gpus):
        """Only the run's own group is counted: another process's 9,000 MB on
        the GPU is not its."""
        assert self._sampled(tmp_path, gpus) == [1500]
        assert [(run["particles"], run["peak_mb"]) for run in measured(HERE)] == [
            (5000, 1500)]
        assert (tmp_path / "settings" / "gpu_memory_here.json").is_file()
        assert not (tmp_path / "settings" / "gpu_memory").exists()

    def test_nothing_from_a_run_that_did_not_complete(self, workspace, tmp_path, gpus):
        assert self._sampled(tmp_path, gpus, status="failed") == [1500]
        assert measured(HERE) == []

    def test_nothing_from_a_run_shorter_than_three_readings(self, workspace, tmp_path,
                                                            gpus):
        assert self._sampled(tmp_path, gpus, seconds=10.0) == [1500]
        assert measured(HERE) == []


def test_the_record_of_this_computer_never_travels(workspace):
    from fastmdxplora.remote.inputs import _settings_kept

    assert any(kept.endswith("gpu_memory_here.json") for kept in _settings_kept())
    assert RUNS_FILE == ".fastmdxplora-runs.json"


class TestNoTwoRunsWriteOneFolder:
    """Second review: with GPUs to share, a study was admitted into a
    folder a run was writing, and one whose GPU was not chosen here
    started beside work on the CPU."""

    def test_the_same_folder_is_refused_with_room_on_the_gpus(
            self, workspace, gpus, started):
        held, _ = started
        gpus((0, UUID_0, 24000, 23000), (1, UUID_1, 24000, 23000))
        assert _start(workspace, "first")["launched"]
        again = _start(workspace, "first")
        assert again["ok"] is False and again["code"] == "environment.workspace.run_going"
        assert again["error"].startswith(
            "first (started from the GUI) is running where this study would write")
        assert len(held) == 1

    def test_a_folder_inside_or_around_one_written_is_refused(self, workspace, gpus, started):
        from fastmdxplora.runs_here import may_start

        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "first")["launched"]
        config = {"systems": [{"system": str(workspace / "top.pdb")}],
                  "simulation": {"duration_ns": 1}}
        for target in (workspace / "first" / "inner", workspace):
            start = may_start([workspace], config, workspace, target=target)
            assert start.refused and start.refused["code"] == "environment.workspace.run_going"
        assert may_start([workspace], config, workspace,
                         target=workspace / "firsts").refused is None

    def test_a_study_whose_gpu_is_not_chosen_waits_for_work_on_the_cpu(
            self, workspace, gpus, started):
        from fastmdxplora.runs_here import may_start

        gpus((0, UUID_0, 24000, 23000))
        continuing = {"systems": [{"system": str(workspace / "top.pdb")}],
                      "simulation": {"resume_from": str(workspace / "done")}}
        alone = may_start([workspace], continuing, workspace, target=workspace / "done")
        assert alone.refused is None and alone.choice is None and alone.env == {}
        assert any("continuation" in line for line in alone.notes)
        assert _start(workspace, "cpu_one", cpu=True)["launched"]
        refused = may_start([workspace], continuing, workspace, target=workspace / "done")
        assert refused.refused and refused.refused["code"] == "environment.workspace.run_going"
        assert refused.refused["error"].startswith("cpu_one (started from the GUI)")

    def test_a_continuation_of_a_study_running_is_refused_and_leaves_nothing(
            self, workspace, gpus, started, monkeypatch):
        held, _ = started
        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "first")["launched"]
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        monkeypatch.setattr(DashboardRuntime, "_study_being_continued",
                            lambda self, source: workspace / "first")
        config = {"systems": [{"system": str(workspace / "top.pdb")}],
                  "simulation": {"resume_from": str(workspace / "first")}}
        refused = window.launch_from_config(None, config=config)
        assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
        assert len(held) == 1
        assert not (workspace / "continuations").exists()

    @pytest.mark.parametrize("named", ["absolute", "relative"])
    def test_a_config_run_as_it_is_that_continues_a_study_running_is_refused(
            self, workspace, gpus, started, named):
        """Third review: **Run a config file** with ``resume_from`` naming a
        study running on a GPU started beside it, both writing the study,
        since the folder checked was the new ``<config>_output``."""
        held, _ = started
        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "first")["launched"]
        (workspace / "first").mkdir(exist_ok=True)
        study = workspace / "first" if named == "absolute" else "first"
        config = workspace / "more.yml"
        config.write_text(f"systems:\n  - system: {workspace / 'top.pdb'}\n"
                          f"simulation:\n  resume_from: {study}\n  duration_ns: 2\n")
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        refused = window.launch_existing_config(str(config))
        assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
        assert len(held) == 1
        assert not (workspace / "more_output").exists()

    def test_a_continuation_is_read_as_the_command_line_reads_it(
            self, workspace, gpus, started):
        """A study still in setup has no record that marks it a study yet;
        a config continuing it continues it all the same."""
        held, _ = started
        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "first")["launched"]
        (workspace / "first").mkdir(exist_ok=True)
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        refused = window.launch_from_config(None, config={
            "systems": [{"system": str(workspace / "top.pdb")}],
            "simulation": {"resume_from": str(workspace / "first"), "duration_ns": 2},
            "output": str(workspace / "elsewhere")})
        assert refused["ok"] is False and refused["code"] == "environment.workspace.run_going"
        assert len(held) == 1

    def test_a_continuation_named_by_no_possible_folder_is_not_a_crash(
            self, workspace, gpus, started):
        from fastmdxplora.runs_here import may_start

        gpus((0, UUID_0, 24000, 23000))
        config = {"systems": [{"system": str(workspace / "top.pdb")}],
                  "simulation": {"resume_from": "x" * 5000}}
        assert may_start([workspace], config, workspace).refused is None

    def test_a_refused_launch_takes_back_the_folders_it_made(self, workspace, gpus, started):
        gpus((0, UUID_0, 24000, 1000))
        _needs(2000)
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        config = {"systems": [{"system": str(workspace / "top.pdb")}],
                  "simulation": {"duration_ns": 1},
                  "output": str(workspace / "new" / "deeper" / "later")}
        refused = window.launch_from_config(None, config=config)
        assert refused["code"] == "environment.workspace.no_room"
        assert not (workspace / "new").exists()

    def test_an_ai_app_running_phases_again_on_a_study_running_is_refused(
            self, workspace, gpus, started):
        from fastmdxplora.mcp.tools import ToolError, _may_start

        gpus((0, UUID_0, 24000, 23000))
        assert _start(workspace, "first")["launched"]

        class Ctx:
            class workspace:  # noqa: N801 - as the tool's context has it
                root = workspace

        with pytest.raises(ToolError) as refused:
            _may_start(Ctx, None, workspace / "first")
        assert refused.value.code == "environment.workspace.run_going"


class TestWhatIsStartedBeside:
    def test_no_reader_where_nothing_is_learned(self, workspace, gpus, started):
        """Runs side by side are not learned from, so nothing reads what
        they hold; the study still goes on the GPU chosen."""
        held, samplers = started
        gpus((0, UUID_0, 24000, 23000))
        path = workspace / "pair.yml"
        path.write_text(f"systems:\n  - system: {workspace / 'top.pdb'}\n"
                        f"  - system: {workspace / 'top.pdb'}\n    name: b\n"
                        "simulation:\n  duration_ns: 1\n"
                        "execution:\n  mode: parallel\n  workers: 2\noutput: pair\n")
        window = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
        assert window._spawn(_command(workspace / "pair"), workspace / "pair", None,
                             config_path=path)["launched"]
        assert held[0][1] == {"CUDA_VISIBLE_DEVICES": UUID_0}
        assert samplers == []

    def test_a_start_record_with_its_gpus_garbled_is_read_as_none(self):
        assert gpu_here.held_from([{"pid": 5, "gpu": {"uuids": 5}}]) == []

    def test_an_nvidia_smi_that_hangs_is_stopped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gpu_here, "ASKED_FOR_S", 1)
        marker = tmp_path / "left"
        began = time.monotonic()
        said = gpu_here._answer(f"sh -c 'sleep 30; touch {marker}' &\nwait\n")
        assert said == "" and time.monotonic() - began < 10
        time.sleep(0.5)
        found = subprocess.run(["pgrep", "-f", f"touch {marker}"], capture_output=True,
                               text=True).stdout.split()
        assert found == []
