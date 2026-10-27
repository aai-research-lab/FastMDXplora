"""A run's process record goes when the run ends, not when its process does.

The record (`.fastmdxplora_run.json`) names the process a study runs in, so
a GUI opened on the folder finds the run going and can stop it. It was
removed only when the process exited, so a run made in a process that went
on (a notebook, a test, the GUI of `fastmdx explore --dashboard`) left
a record naming that process: a GUI opened in it on the study took itself
for the study's run, said it was running, and refused to start another
("A FastMDXplora workflow is already running."). It is removed when the run
ends, however it ends, where this process wrote it; a record of another
process is left alone; and while the run goes, a GUI in its process still
finds it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.orchestrator import (
    RUN_PROCESS_FILE,
    FastMDXplora,
    PhaseResult,
    RunResult,
    _record_run_process,
    process_start_ticks,
    this_machine,
)
from fastmdxplora.refusals import RunStopped, StudyError

pytestmark = pytest.mark.skipif(not Path("/proc/self/stat").is_file(),
                                reason="a process's start is read from /proc")


def _runtime(root: Path) -> DashboardRuntime:
    """A GUI opened in this process on the study."""
    return DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent,
                            active_root=root)


def _records_of_this_process(root: Path) -> list[str]:
    found = []
    for record in sorted(root.rglob(RUN_PROCESS_FILE)):
        if json.loads(record.read_text(encoding="utf-8")).get("pid") == os.getpid():
            found.append(str(record.relative_to(root)))
    return found


@pytest.fixture
def another():
    """A live process of someone else's, as a run started elsewhere is."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield process
    finally:
        process.kill()
        process.wait()


def _its_record(process) -> dict:
    return {"pid": process.pid, "argv": ["fastmdx", "explore"],
            "started_at": "2026-10-27T00:00:00+00:00", **this_machine(),
            "start_ticks": process_start_ticks(process.pid)}


def _phase_that(ended: str, seen: list):
    def phase(self, name, options):
        # While it runs: the record names this process, and a GUI opened
        # in it finds the run going, so its Stop reaches it.
        seen.append((_records_of_this_process(Path(self.output_dir)),
                     _runtime(Path(self.output_dir)).process is not None))
        if ended == "stopped":
            raise RunStopped("SIGTERM")
        if ended == "interrupted":
            raise KeyboardInterrupt
        return PhaseResult(name=name, status="error" if ended == "failed" else "ok",
                           output_dir=Path(self.output_dir) / name)
    return phase


@pytest.mark.parametrize("ended", ["finished", "failed", "stopped", "interrupted", "refused"])
def test_a_study_run_here_leaves_no_record_of_this_process(tmp_path, monkeypatch, ended):
    root = tmp_path / "study"
    seen: list = []
    monkeypatch.setattr(FastMDXplora, "_run_phase", _phase_that(ended, seen))
    if ended == "refused":
        def refuse(self, plan, *, force):
            seen.append((_records_of_this_process(Path(self.output_dir)), None))
            raise StudyError("refused", code="environment.workspace.run_going")
        monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", refuse)
    study = FastMDXplora("1L2Y", output_dir=str(root))
    try:
        study.explore(include_phase=["analysis"])
    except (StudyError, RunStopped, KeyboardInterrupt):
        pass
    assert seen and seen[0][0] == [RUN_PROCESS_FILE]
    if ended != "refused":
        assert seen[0][1] is True
    assert _records_of_this_process(root) == []
    assert _runtime(root).process is None


def test_a_study_run_twice_here_is_found_running_both_times(tmp_path, monkeypatch):
    root = tmp_path / "study"
    seen: list = []
    monkeypatch.setattr(FastMDXplora, "_run_phase", _phase_that("finished", seen))
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    study = FastMDXplora("1L2Y", output_dir=str(root))
    study.explore(include_phase=["analysis"])
    study.analyze()
    assert seen == [([RUN_PROCESS_FILE], True)] * 2
    assert _records_of_this_process(root) == []


def _sweep(tmp_path: Path) -> BatchExplorer:
    return BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                      "sweep": {"simulation.temperature_K": [300, 310, 320]},
                                      "report": {"comparison": False}},
                         output_dir=str(tmp_path / "sweep"))


@pytest.mark.parametrize("ended", ["finished", "failed", "stopped"])
def test_a_study_of_runs_run_here_leaves_no_record_of_this_process(tmp_path, monkeypatch,
                                                                     another, ended):
    """Each run made in this process wrote its record, and the study its
    own; none is left. The second run's record names another process (a
    worker's, or a run started there since), and is."""
    study = _sweep(tmp_path)
    root = study.output_dir
    seen: list = []

    def run(spec, run_out, *args, **kwargs):
        out = Path(run_out)
        out.mkdir(parents=True, exist_ok=True)
        seen.append(_runtime(root).process is not None)
        if spec["run_id"] == study.run_specs[1].run_id:
            (out / RUN_PROCESS_FILE).write_text(json.dumps(_its_record(another)),
                                                encoding="utf-8")
            if ended == "stopped":
                raise RunStopped("SIGTERM")
            if ended == "failed":
                raise RuntimeError("the run failed")
        else:
            # As a run made in this process records itself, and left it.
            _record_run_process(out)
        return RunResult(run_id=spec["run_id"], system=spec["system"], status="ok",
                         output_dir=out)

    monkeypatch.setattr(explorer_module, "_execute_run", run)
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    try:
        study.run()
    except (RunStopped, RuntimeError):
        pass
    assert seen and all(seen)
    assert _records_of_this_process(root) == []
    theirs = root / "runs" / study.run_specs[1].run_id / RUN_PROCESS_FILE
    assert json.loads(theirs.read_text(encoding="utf-8"))["pid"] == another.pid
    assert _runtime(root).process is None


def test_only_this_process_s_own_record_is_removed(tmp_path, another):
    from fastmdxplora.orchestrator import _forget_run_process

    def left(record: dict) -> bool:
        (tmp_path / RUN_PROCESS_FILE).write_text(json.dumps(record), encoding="utf-8")
        _forget_run_process(tmp_path)
        return (tmp_path / RUN_PROCESS_FILE).is_file()

    _record_run_process(tmp_path)
    ours = json.loads((tmp_path / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
    assert not left(ours)
    # Another process's; this number given to a process before this one; a
    # record of this number written on another machine, or by hand.
    assert left(_its_record(another))
    assert left({**ours, "start_ticks": ours["start_ticks"] - 1})
    assert left({**ours, "host": ours["host"] + "-elsewhere"})
    assert left({"pid": os.getpid()})


def test_a_gui_in_the_run_s_process_is_not_running_once_the_run_ends(tmp_path, monkeypatch):
    """A GUI in the run's process (a notebook's), opened on a study made
    there, takes the run for the one it watches, and stays up after it. It
    went on saying the study was running, and offered Stop on this
    process, once the run was over."""
    from unittest import mock

    from fastmdxplora.gui import exploration

    root = tmp_path / "study"
    seen: list = []

    def phase(self, name, options):
        running = gui.snapshot()["process_running"]
        seen.append((gui.process.poll(), running))
        return PhaseResult(name=name, status="ok", output_dir=Path(self.output_dir) / name)

    monkeypatch.setattr(FastMDXplora, "_run_phase", phase)
    study = FastMDXplora("1L2Y", output_dir=str(root))
    gui = _runtime(root)
    # Made, not run: not a run going.
    assert gui.process is None and gui.snapshot()["process_running"] is False
    study.explore(include_phase=["analysis"])
    assert seen == [(None, True)]
    assert gui.process.pid == os.getpid()
    assert gui.process.poll() is not None
    assert gui.snapshot()["process_running"] is False
    with mock.patch.object(exploration, "_terminate_process") as signalled:
        gui.process.terminate()
        gui.process.kill()
    signalled.assert_not_called()


def test_a_study_of_one_keeps_its_record_while_it_goes_on_after_its_run(tmp_path, monkeypatch):
    """A study of one run until it is determined: its run's own `explore()`
    ends, and the study goes on extending it in rounds in the same process.
    Its record went with that `explore()`, so a GUI opened on it elsewhere
    did not find it running, and its Stop said no workflow was running."""
    out = tmp_path / "study"
    monkeypatch.setattr(FastMDXplora, "_run_phase", lambda self, name, options: PhaseResult(
        name=name, status="ok", output_dir=Path(self.output_dir) / name))
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    monkeypatch.setattr(BatchExplorer, "_stopping_rule", lambda self: ["a rule"])
    monkeypatch.setattr(BatchExplorer, "_write_planned_stopping", lambda self, targets: None)
    seen: dict = {}

    def rounds(self, targets):
        # Where the study's rounds of extension run, for hours.
        seen["record"] = _records_of_this_process(out)
        seen["here"] = _runtime(out).process is not None
        code = ("import sys; from pathlib import Path; "
                "from fastmdxplora.gui.exploration import DashboardRuntime as D; "
                "p = Path(sys.argv[1]); "
                "g = D(workspace_root=p.parent, exploration_root=p.parent, active_root=p); "
                "print(g.process is not None and g.process.poll() is None)")
        seen["elsewhere"] = subprocess.run(
            [sys.executable, "-c", code, str(out)], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            timeout=120).stdout.strip().splitlines()[-1:]

    monkeypatch.setattr(BatchExplorer, "_run_until_known", rounds)
    BatchExplorer(config_data={"systems": [{"system": "1L2Y"}]}, output_dir=str(out)).run()
    assert seen == {"record": [RUN_PROCESS_FILE], "here": True, "elsewhere": ["True"]}
    assert _records_of_this_process(out) == []


def _explore_with_its_dashboard(tmp_path, monkeypatch, phase, phases, study=("-s", "1L2Y")):
    """`fastmdx explore --dashboard` run as the command runs it, each phase
    stood in for by ``phase(self, name, gui)``; signals sent to this
    process noted, not delivered."""
    import importlib

    from fastmdxplora.config import before_run

    cli = importlib.import_module("fastmdxplora.cli.main")
    out = tmp_path / "study"
    monkeypatch.setattr(before_run, "refuse_what_can_be_seen", lambda *a, **k: None)
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    monkeypatch.setattr(BatchExplorer, "_refuse_to_overwrite_runs", lambda *a, **k: None)
    signalled: list = []
    kill, killpg = os.kill, getattr(os, "killpg", None)

    def noted(pid, number):
        # Signal 0 asks only whether a process is alive.
        if number != 0 and pid in (os.getpid(), 0, -os.getpid()):
            signalled.append(number)
            return None
        return kill(pid, number)

    def noted_group(group, number):
        if number != 0 and group == os.getpgid(0):
            signalled.append(number)
            return None
        return killpg(group, number)

    monkeypatch.setattr(os, "kill", noted)
    if killpg is not None:
        monkeypatch.setattr(os, "killpg", noted_group)
    sessions: list = []
    start = cli._start_dashboard_for_command

    def started(args, output_dir):
        sessions.append(start(args, output_dir))
        return sessions[-1]

    monkeypatch.setattr(cli, "_start_dashboard_for_command", started)
    monkeypatch.setattr(FastMDXplora, "_run_phase",
                        lambda self, name, options: phase(self, name, sessions[0].runtime))
    rc = cli.main(["explore", *study, "--dashboard", "--dashboard-port", "0",
                   "--dashboard-stop-on-complete", "--output", str(out),
                   "--include-phase", *phases])
    return rc, out, sessions[0].runtime, signalled


@pytest.mark.parametrize("pressed_in", ["setup", "analysis"])
def test_the_gui_of_explore_with_its_dashboard_holds_the_run_and_never_signals_itself(
        tmp_path, monkeypatch, pressed_in):
    """`fastmdx explore --dashboard` starts its GUI before the run records
    itself: the GUI said idle and offered no Stop while the run went. It
    takes the run up as it records itself. Its Stop sent SIGTERM, then
    SIGKILL, to its own process, which outside production nothing caught:
    the command, GUI and all, died and left its record. Outside production
    it is refused, said, and the run goes on to its end."""
    seen: list = []
    refusals: list = []

    def phase(self, name, gui):
        state = gui.snapshot()
        seen.append((name, state["process_running"], getattr(gui.process, "pid", None)))
        # Said before Stop is pressed, so the page says it at once.
        refusals.append(state["stop_refused"])
        if name == pressed_in:
            seen.append(gui.stop())
        return PhaseResult(name=name, status="ok", output_dir=Path(self.output_dir) / name)

    rc, out, gui, signalled = _explore_with_its_dashboard(
        tmp_path, monkeypatch, phase, ["setup", "analysis"])
    answer = next(item for item in seen if isinstance(item, dict))
    assert (answer["stopped"], answer["ok"]) == (False, False)
    assert answer["detail"] == (
        "This run is in this GUI's own process; stopping it here would end the GUI too. "
        "Stop it where it was started (Ctrl+C at its terminal, or interrupt the notebook's "
        "cell).")
    assert [item for item in seen if isinstance(item, tuple)] == [
        ("setup", True, os.getpid()), ("analysis", True, os.getpid())]
    assert refusals == [answer["detail"]] * 2
    assert signalled == []
    assert rc == 0
    after = gui.snapshot()
    assert after["process_running"] is False and after["status"] == "completed", after["status"]
    assert _records_of_this_process(out) == []


def test_the_gui_of_explore_with_its_dashboard_stops_production_as_sigterm_does(
        tmp_path, monkeypatch):
    """In production, where the run listens for a stop, the GUI asks for it
    as SIGTERM does, without a signal: production stops at its next frame,
    and the run ends as stopped, its GUI still up."""
    from fastmdxplora.simulation import runner

    answers: list = []

    def phase(self, name, gui):
        # Stands in for production: it listens as production does, and
        # ends as a stopped production ends.
        with runner._StopRequests() as stop:
            answers.append(gui.snapshot()["stop_refused"])
            answers.append(gui.stop())
            # Recorded as the GUI's request: no signal was sent.
            answers.append(runner._who_asked(stop.name))
            if stop.requested:
                raise RunStopped(stop.name)
        return PhaseResult(name=name, status="ok", output_dir=Path(self.output_dir) / name)

    rc, out, gui, signalled = _explore_with_its_dashboard(
        tmp_path, monkeypatch, phase, ["simulation"])
    assert answers[0] is None and answers[1]["stopped"] is True
    assert answers[2] == "a request to stop from the GUI"
    assert signalled == []
    assert rc != 0
    after = gui.snapshot()
    assert after["process_running"] is False and after["status"] == "stopped", after["status"]
    assert _records_of_this_process(out) == []


def test_the_gui_of_explore_with_its_dashboard_stops_a_parallel_study_through_its_parent(
        tmp_path, monkeypatch, another):
    """A parallel study's production runs in its workers; its parent, the
    GUI's process, listens for a stop through the study's `_StudyStop`.
    The GUI asked production alone, so its Stop was refused in every phase
    of such a study. The study's stop is asked, the workers are told, and
    this process is not signalled."""
    from fastmdxplora.batch import explorer

    config = tmp_path / "par.yml"
    config.write_text("systems:\n  - {id: a, system: 1L2Y}\n"
                      "sweep: {simulation.random_seed: [1, 2]}\n"
                      "execution: {mode: parallel, workers: 2}\n", encoding="utf-8")
    answers: list = []

    class Pool:
        # A worker's process, as the executor lists it.
        _processes = {another.pid: None}

    def in_parallel(self, include, exclude):
        # The study's parent around its pool, as `_run_parallel` has it.
        listening = explorer._StudyStop(Pool())
        listening.folder = self.output_dir
        with listening as stop:
            gui = runtime[0]
            gui.snapshot()
            answers.append(gui.stop())
            assert stop.requested
        return [explorer._skipped_run_result(spec, self._run_output_dir(spec), "stopped")
                for spec in self.run_specs]

    runtime: list = []
    monkeypatch.setattr(BatchExplorer, "_run_parallel", in_parallel)

    def phase(self, name, gui):
        return PhaseResult(name=name, status="ok", output_dir=Path(self.output_dir) / name)

    import importlib

    cli = importlib.import_module("fastmdxplora.cli.main")
    start = cli._start_dashboard_for_command

    def started(args, output_dir):
        session = start(args, output_dir)
        runtime.append(session.runtime)
        return session

    monkeypatch.setattr(cli, "_start_dashboard_for_command", started)
    rc, out, gui, signalled = _explore_with_its_dashboard(
        tmp_path, monkeypatch, phase, ["simulation"], study=("-c", str(config)))
    assert answers and answers[0]["stopped"] is True, answers
    assert answers[0]["detail"].startswith("Stopping: each run in progress ends")
    assert signalled == []
    assert another.wait(timeout=30) != 0, "the worker was not told"
    assert gui.snapshot()["process_running"] is False
    assert _records_of_this_process(out) == []


def test_a_record_that_cannot_be_read_keeps_no_gui_here_running(tmp_path, monkeypatch):
    """A record this process left empty (a full disk) was never removed,
    and a GUI in this process went on saying its run was going. One left
    empty here is removed; one left empty by anyone else is not; and an
    unreadable record is believed only while a run of this process holds
    the folder."""
    from fastmdxplora.gui.exploration import _AdoptedProcess
    from fastmdxplora.orchestrator import (
        _forget_run_process,
        _hold_run_process,
        _release_run_process,
    )

    write = Path.write_text

    def full(self, *args, **kwargs):
        if self.name == RUN_PROCESS_FILE:
            write(self, "", encoding="utf-8")
            raise OSError(28, "No space left on device")
        return write(self, *args, **kwargs)

    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(Path, "write_text", full)
        _record_run_process(mine)
    (theirs / RUN_PROCESS_FILE).write_text("", encoding="utf-8")
    assert (mine / RUN_PROCESS_FILE).read_text(encoding="utf-8") == ""
    adopted = _AdoptedProcess(os.getpid(), mine)
    assert adopted.poll() is not None
    _hold_run_process(theirs)
    try:
        assert _AdoptedProcess(os.getpid(), theirs).poll() is None
    finally:
        # Released: written by hand, it is not this process's to remove.
        _release_run_process(theirs)
    _forget_run_process(mine)
    assert not (mine / RUN_PROCESS_FILE).exists()
    assert (theirs / RUN_PROCESS_FILE).exists()


def test_a_study_run_in_stages_keeps_its_record_while_it_is_priced(tmp_path, monkeypatch):
    """A study with a budget runs setup, then times the prepared system in
    the same process, then the rest. Setup's run ended and took the record
    with it, so while the system was timed a GUI found nothing going."""
    from fastmdxplora.agent import run_in_stages
    from fastmdxplora.cost import calibrate
    from fastmdxplora.orchestrator import _hold_run_process, _release_run_process

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    out = tmp_path / "out"

    def a_run(*, config, output_dir):
        # As a run does: its record held while it goes, and given back.
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        _hold_run_process(Path(output_dir))
        try:
            if config.get("include_phase") == ["setup"]:
                where = Path(output_dir) / "setup"
                where.mkdir(parents=True, exist_ok=True)
                (where / "setup_parameters.json").write_text(
                    json.dumps({"n_atoms_solvated": 60_000}), encoding="utf-8")
        finally:
            _release_run_process(Path(output_dir))

    seen: list = []

    def timed(setup_dir, *, platform_name, precision):
        seen.append((_records_of_this_process(out), _runtime(out).process is not None))
        return calibrate(particles=60_000, steps=2000, seconds=12.0,
                         platform_name=platform_name or "CPU", precision=precision)

    staged = run_in_stages({"systems": [{"id": "a", "system": "x.pdb"}],
                            "simulation": {"platform": "CPU", "production_steps": 1000,
                                           "nvt_steps": 0, "npt_steps": 0}},
                           out, budget_hours=10, explore=a_run, measure=timed)
    assert staged.refusal is None and staged.simulated
    assert seen == [([RUN_PROCESS_FILE], True)]
    assert _records_of_this_process(out) == []


def test_a_study_reached_through_a_link_keeps_its_record_across_its_rounds(tmp_path,
                                                                            monkeypatch):
    """A study of one with a stopping rule, its folder named through a link.
    Each round's join and analysis is a run of its own on the folder
    resolved (`extend_study` resolves it): its hold was counted under the
    resolved spelling and the study's under the one given, so the round's
    end removed the study's record while the study went on."""
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    out = tmp_path / "link" / "study"
    monkeypatch.setattr(FastMDXplora, "_run_phase", lambda self, name, options: PhaseResult(
        name=name, status="ok", output_dir=Path(self.output_dir) / name))
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    monkeypatch.setattr(BatchExplorer, "_refuse_to_overwrite_runs", lambda *a, **k: None)
    monkeypatch.setattr(BatchExplorer, "_stopping_rule", lambda self: ["a rule"])
    monkeypatch.setattr(BatchExplorer, "_write_planned_stopping", lambda self, targets: None)
    seen: dict = {}

    def rounds(self, targets):
        monkeypatch.setattr(BatchExplorer, "_stopping_rule", lambda self: None)
        FastMDXplora(config_data={"systems": [{"system": "1L2Y"}],
                                  "include_phase": ["analysis"]},
                     output_dir=str(out.resolve())).explore(force=True)
        # The next round's production would now run.
        seen["record"] = _records_of_this_process(out)
        seen["held"] = DashboardRuntime(workspace_root=real, exploration_root=real,
                                        active_root=out).process is not None

    monkeypatch.setattr(BatchExplorer, "_run_until_known", rounds)
    BatchExplorer(config_data={"systems": [{"system": "1L2Y"}]}, output_dir=str(out)).run()
    assert seen == {"record": [RUN_PROCESS_FILE], "held": True}
    assert _records_of_this_process(out) == []


def test_a_study_made_here_and_not_run_is_not_a_run_going(tmp_path, monkeypatch):
    """A notebook with a GUI open on a folder makes a study there and has
    not run it: the GUI said running, refused Run and offered Stop, which
    signalled the notebook's own process. Nor did a GUI opened after it."""
    from fastmdxplora.gui import exploration

    out = tmp_path / "study"
    out.mkdir()
    signals: list = []
    monkeypatch.setattr(exploration, "_terminate_process",
                        lambda pid, force=False: signals.append(pid))
    before = _runtime(out)
    FastMDXplora("1L2Y", output_dir=str(out))
    after = _runtime(out)
    for gui in (before, after):
        state = gui.snapshot()
        assert (state["status"], state["process_running"], state["can_launch"]) == (
            "idle", False, True), state["status"]
        assert gui.stop()["stopped"] is False
    assert signals == []


def test_a_hold_taken_as_the_last_is_given_back_keeps_the_record(tmp_path, monkeypatch):
    """Counting a hold and writing the record were two steps, and so were
    giving the last one back and removing it: a run of another thread that
    took its hold between them found the record there, wrote none, and was
    left going with none."""
    import threading

    from fastmdxplora import orchestrator

    _hold = orchestrator._hold_run_process
    _hold(tmp_path)
    remove = orchestrator._remove_if_written_here
    removing = threading.Event()

    def slowly(where):
        removing.set()
        time.sleep(0.3)
        remove(where)

    monkeypatch.setattr(orchestrator, "_remove_if_written_here", slowly)
    last = threading.Thread(target=orchestrator._release_run_process, args=(tmp_path,))
    last.start()
    assert removing.wait(10)
    _hold(tmp_path)
    last.join(10)
    try:
        assert _records_of_this_process(tmp_path) == [RUN_PROCESS_FILE]
    finally:
        monkeypatch.setattr(orchestrator, "_remove_if_written_here", remove)
        orchestrator._release_run_process(tmp_path)
    assert _records_of_this_process(tmp_path) == []


def test_the_gui_stops_its_own_study_s_production_not_the_newest_one(tmp_path):
    """Two runs going on threads of one process (a notebook), a GUI on the
    first: its Stop asked the newest production in the process, the
    second's."""
    from fastmdxplora.gui.exploration import _AdoptedProcess
    from fastmdxplora.orchestrator import _hold_run_process, _release_run_process
    from fastmdxplora.simulation import runner

    first, second = tmp_path / "first", tmp_path / "second"
    for folder in (first, second):
        folder.mkdir()
    _hold_run_process(first)
    try:
        gui = _runtime(first)
        record = json.loads((first / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
        gui.process = _AdoptedProcess(os.getpid(), first, record)
        gui.running_root = first
        theirs, ours = runner._StopRequests(), runner._StopRequests()
        ours.folder, theirs.folder = first, second
        with ours, theirs:
            answer = gui.stop()
            asked = (ours.requested, theirs.requested)
    finally:
        _release_run_process(first)
    assert answer["stopped"] is True
    assert asked == (True, False)
