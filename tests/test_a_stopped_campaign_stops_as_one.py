"""A study of several runs asked to stop stops as one.

The GUI's Stop signals the process it started. For a study running its runs
in parallel that is the parent, whose workers never heard it: they ran on,
and were killed with nothing written when the GUI gave up waiting. A worker
that did hear it outside production died, which breaks the whole pool. And a
study stopped in one run went on to start the next.

Now the parent passes a stop on to its workers, each run ends where it can be
carried on, no further run starts, and `fastmdx resume` carries the study on.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import fastmdxplora.batch.explorer as explorer
from fastmdxplora.batch.explorer import (
    STOPPED_ERROR_TYPE,
    BatchExplorer,
    _how_far_along,
    _StudyStop,
    _stop_this_run,
    _was_stopped,
)
from fastmdxplora.orchestrator import PhaseResult, RunResult
from fastmdxplora.refusals import RunStopped, refusal_of
from fastmdxplora.simulation import runner
from fastmdxplora.simulation.runner import (
    STOP_REPEAT_SECONDS,
    STOPPED_CODE,
    _a_stop_heard,
    _listen_afresh,
    _StopRequests,
)

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

POSIX = os.name == "posix"


@pytest.fixture()
def clock(monkeypatch):
    """A monotonic clock the test moves by hand."""
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    _listen_afresh()
    yield now
    _listen_afresh()


class TestOneRequestIsCountedOnce:

    def test_first_again_and_now(self, clock) -> None:
        assert _a_stop_heard(signal.SIGTERM) == "first"
        clock["t"] += STOP_REPEAT_SECONDS / 2
        assert _a_stop_heard(signal.SIGTERM) == "again"
        clock["t"] += STOP_REPEAT_SECONDS
        assert _a_stop_heard(signal.SIGTERM) == "now"

    def test_a_second_ctrl_c_is_never_a_repeat(self, clock) -> None:
        """Pressed twice, however quickly, it means now."""
        assert _a_stop_heard(signal.SIGINT) == "first"
        clock["t"] += 0.1
        assert _a_stop_heard(signal.SIGINT) == "now"

    def test_production_ignores_the_same_request_twice(self, clock, monkeypatch) -> None:
        """Sent to the group and passed on by the parent, a worker gets it twice."""
        stop = _StopRequests()
        _listen_afresh()
        stop._note(signal.SIGTERM, None)
        clock["t"] += 0.1
        restored: list[bool] = []
        monkeypatch.setattr(stop, "_restore", lambda: restored.append(True))
        stop._note(signal.SIGTERM, None)
        assert stop.requested and restored == []


class TestAWorkerEndsItsRunWithARecord:

    def test_a_run_in_progress_ends_with_the_retryable_refusal(self, clock, monkeypatch) -> None:
        monkeypatch.setattr(explorer, "_running_a_run", True)
        with pytest.raises(RunStopped) as stopped:
            _stop_this_run(signal.SIGTERM, None)
        refusal = refusal_of(stopped.value.as_error())
        assert refusal.code == STOPPED_CODE and refusal.retryable
        assert "SIGTERM outside production" in str(stopped.value.as_error())

    def test_no_handler_on_the_way_turns_it_into_something_else(self) -> None:
        """Raised inside an OpenMM call in equilibration, a stop was caught
        as an integration failure and recorded as the system becoming
        unstable, which a resume refuses to run again."""
        def stopped_mid_step(_steps):
            raise RunStopped("SIGTERM")

        simulation = SimpleNamespace(step=stopped_mid_step)
        with pytest.raises(RunStopped):
            runner._run_md_stage(simulation, n_steps=100, label="NPT equilibration")

    def test_the_phase_records_it_as_the_retryable_refusal(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.orchestrator import FastMDXplora

        pdb = tmp_path / "p.pdb"
        pdb.write_text("END\n", encoding="utf-8")
        study = FastMDXplora(system=str(pdb), output_dir=str(tmp_path / "run"))

        def stopped(**_kwargs):
            raise RunStopped("SIGTERM")

        monkeypatch.setattr(FastMDXplora, "_resolve_phase_runner",
                            staticmethod(lambda phase: stopped))
        result = study._run_phase("simulation", {})
        assert result.status == "error"
        assert result.refusal["code"] == STOPPED_CODE and result.refusal["retryable"]

    def test_an_idle_worker_is_left_alone(self, clock, monkeypatch) -> None:
        """Raised into the pool's own loop, it would break every run in it."""
        monkeypatch.setattr(explorer, "_running_a_run", False)
        assert _stop_this_run(signal.SIGTERM, None) is None

    def test_asked_again_later_it_stops_now(self, clock, monkeypatch) -> None:
        monkeypatch.setattr(explorer, "_running_a_run", False)
        _stop_this_run(signal.SIGTERM, None)
        clock["t"] += 0.5
        assert _stop_this_run(signal.SIGTERM, None) is None  # the same request
        sent, handlers = [], []
        monkeypatch.setattr(explorer.os, "kill", lambda pid, number: sent.append(number))
        monkeypatch.setattr(signal, "signal", lambda number, how: handlers.append(how))
        clock["t"] += STOP_REPEAT_SECONDS
        _stop_this_run(signal.SIGTERM, None)
        assert sent == [signal.SIGTERM] and handlers == [signal.SIG_DFL]


def _result(status="error", *, code=None, error_type=None) -> RunResult:
    phases = [PhaseResult(name="simulation", status=status, output_dir=Path("s"),
                          started_at="", finished_at="",
                          refusal={"code": code} if code else None)]
    return RunResult(run_id="r", system="x", status=status, phases=phases,
                     error_type=error_type)


class TestAStoppedRunIsKnown:

    def test_by_its_phase_refusal(self) -> None:
        assert _was_stopped(_result(code=STOPPED_CODE))

    def test_by_what_ended_it_outside_a_phase(self) -> None:
        assert _was_stopped(_result(error_type=STOPPED_ERROR_TYPE))

    def test_not_a_run_that_failed_otherwise(self) -> None:
        assert not _was_stopped(_result(code="setup.structure.unreadable"))

    def test_not_a_run_carried_on_to_the_end(self) -> None:
        """Its record keeps the stop it came back from."""
        assert not _was_stopped(_result(status="ok", code=STOPPED_CODE))


class TestTheParentPassesItOn:

    def _stop(self, monkeypatch):
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(explorer.os, "kill", lambda pid, number: sent.append((pid, number)))
        pool = SimpleNamespace(_processes={111: object(), 222: object()})
        return _StudyStop(pool), sent

    def test_the_first_reaches_every_worker(self, clock, monkeypatch, caplog) -> None:
        stop, sent = self._stop(monkeypatch)
        stop._heard(signal.SIGTERM, None)
        assert stop.requested
        assert sorted(sent) == [(111, signal.SIGTERM), (222, signal.SIGTERM)]
        assert "no further run starts" in caplog.text

    def test_the_same_request_twice_is_passed_on_once(self, clock, monkeypatch) -> None:
        stop, sent = self._stop(monkeypatch)
        stop._heard(signal.SIGTERM, None)
        clock["t"] += 0.2
        stop._heard(signal.SIGTERM, None)
        assert len(sent) == 2

    def test_ctrl_c_is_not_passed_on(self, clock, monkeypatch) -> None:
        """The terminal sends it to the whole group, workers included."""
        stop, sent = self._stop(monkeypatch)
        stop._heard(signal.SIGINT, None)
        assert stop.requested and sent == []

    def test_a_second_ctrl_c_stops_now_however_quick(self, clock, monkeypatch) -> None:
        stop, _sent = self._stop(monkeypatch)
        stop._heard(signal.SIGINT, None)
        clock["t"] += 0.2
        with pytest.raises(KeyboardInterrupt):
            stop._heard(signal.SIGINT, None)

    def test_a_later_sigterm_stops_now(self, clock, monkeypatch) -> None:
        stop, sent = self._stop(monkeypatch)
        stop._heard(signal.SIGTERM, None)
        clock["t"] += STOP_REPEAT_SECONDS + 1
        killed = []
        monkeypatch.setattr(explorer.os, "kill",
                            lambda pid, number: killed.append((pid, number)))
        stop._heard(signal.SIGTERM, None)
        assert (os.getpid(), signal.SIGTERM) in killed
        assert len(sent) == 2 and len(killed) == 3

    def test_a_worker_already_gone_is_passed_over(self, clock, monkeypatch) -> None:
        def gone(pid, number):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(explorer.os, "kill", gone)
        stop = _StudyStop(SimpleNamespace(_processes={111: object()}))
        stop._heard(signal.SIGTERM, None)
        assert stop.requested

    def test_off_the_main_thread_it_changes_nothing(self) -> None:
        """Python lets only the main thread set a handler."""
        import threading

        before = signal.getsignal(signal.SIGTERM)
        seen = []
        worker = threading.Thread(
            target=lambda: seen.append(_StudyStop(SimpleNamespace()).__enter__()._previous))
        worker.start()
        worker.join()
        assert seen == [{}] and signal.getsignal(signal.SIGTERM) == before

    def test_it_listens_only_while_the_study_runs(self) -> None:
        before = signal.getsignal(signal.SIGTERM)
        with _StudyStop(SimpleNamespace()) as stop:
            assert signal.getsignal(signal.SIGTERM) == stop._heard
        assert signal.getsignal(signal.SIGTERM) == before


def _campaign(tmp_path: Path, mode: str) -> Path:
    pdb = tmp_path / "p.pdb"
    pdb.write_text("END\n", encoding="utf-8")
    config = tmp_path / "study.yaml"
    config.write_text(yaml.safe_dump({
        "output": str(tmp_path / "study"),
        "include": ["setup"],
        "systems": [{"id": "a", "system": str(pdb)}],
        "sweep": {"setup.ph": [6, 7, 8]},
        "execution": {"mode": mode, "workers": 2},
    }), encoding="utf-8")
    return config


class _Pool:
    """A ProcessPoolExecutor that runs each run where it is submitted."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def submit(self, fn, *args, **kwargs):
        from concurrent.futures import Future

        future = Future()
        future.set_result(fn(*args, **kwargs))
        return future

    def shutdown(self, wait=True, cancel_futures=False) -> None:
        return None


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
def test_a_stopped_run_ends_the_study_and_starts_nothing_more(tmp_path, monkeypatch,
                                                              capsys, mode) -> None:
    ran: list[str] = []

    def one_run(spec_dict, run_out, *args, **kwargs):
        # Every run in progress hears the stop: one at a time, the first;
        # two at a time, the first two, whichever of them is read first.
        ran.append(spec_dict["run_id"])
        return _result(code=STOPPED_CODE) if len(ran) <= 2 else _result("ok")

    monkeypatch.setattr(explorer, "_execute_run", one_run)
    monkeypatch.setattr(explorer, "ProcessPoolExecutor", _Pool)
    batch = BatchExplorer(config=str(_campaign(tmp_path, mode)))
    results = batch.run()

    started = 1 if mode == "sequential" else 2  # two were in flight together
    assert len(ran) == started
    assert [r.status for r in results].count("skipped") == 3 - started
    skipped = next(r for r in results if r.status == "skipped")
    assert skipped.message.startswith("Not started: the study was asked to stop.")
    said = capsys.readouterr().out
    assert "Batch stopped:" in said
    # One command for the whole study, with what it carries on and costs.
    assert "What would fix it:" in said
    assert f"Run: fastmdx resume {batch.output_dir.resolve()}" in said
    assert (f"{started} run{'s' if started > 1 else ''} stopped where "
            f"{'it' if started == 1 else 'they'} can be carried on") in said


def test_a_run_carried_on_is_followed_in_its_segment(tmp_path) -> None:
    run = tmp_path / "runs" / "a__ph-6"
    for folder, step in ((run / "simulation", 900), (run / "segment-001" / "simulation", 50)):
        folder.mkdir(parents=True)
        (folder / "live_status.json").write_text(
            json.dumps({"current_step": step, "total_planned_steps": 100}))
        time.sleep(0.02)
    assert _how_far_along([("a__ph-6", run)]) == "  [ph-6 50%]"


def test_the_gui_ends_what_is_left_of_a_run_it_started(monkeypatch) -> None:
    from fastmdxplora.gui import exploration

    ended: list[tuple[int, int]] = []
    monkeypatch.setattr(exploration.os, "killpg",
                        lambda group, number: ended.append((group, number)), raising=False)
    monkeypatch.setattr(exploration, "_WINDOWS", False)
    exploration._end_what_is_left_of(4321)
    assert ended == [(4321, signal.SIGKILL)]
    # Windows has no process groups; nothing is sent there.
    monkeypatch.setattr(exploration, "_WINDOWS", True)
    exploration._end_what_is_left_of(4321)
    assert ended == [(4321, signal.SIGKILL)]


@unittest.skipUnless(HAS_BACKENDS and POSIX, "OpenMM, PDBFixer, MDTraj and POSIX signals")
class TestARealParallelStudyStopped(unittest.TestCase):
    """Three runs of a temperature sweep, two at a time, started as the GUI
    starts one: `fastmdx explore` in a session of its own. SIGTERM to that
    process alone, once a run is in production. That run ends on a frame
    with a checkpoint, the other in flight ends where it was, the third is
    not started, and `fastmdx resume` finishes all three; a second resume
    has nothing to do. On a loaded machine the two runs need not reach
    production together, so the other one is not required to be there."""

    def test_stopped_then_carried_on(self):
        import tempfile

        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        root = Path(tempfile.mkdtemp())
        (root / "tri.pdb").write_text(TRI_ALANINE)
        (root / "study.yaml").write_text(yaml.safe_dump({
            "systems": [{"id": "tri", "system": "tri.pdb"}],
            "sweep": {"simulation.temperature_K": [300, 310, 320]},
            "execution": {"mode": "parallel", "workers": 2},
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 4000,
                           "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                           "trajectory_interval_steps": 200,
                           "checkpoint_interval_steps": 2000,
                           "telemetry_interval": 50},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }))
        env = {**os.environ, "OPENMM_CPU_THREADS": "1",
               "PYTHONPATH": os.pathsep.join([str(Path(runner.__file__).parents[2]),
                                              os.environ.get("PYTHONPATH", "")])}
        command = [sys.executable, "-c",
                   "import sys; from fastmdxplora.cli.main import main; "
                   "sys.exit(main(sys.argv[1:]))"]
        with open(root / "explore.log", "w") as log:
            parent = subprocess.Popen([*command, "explore", "--config", "study.yaml",
                                       "--output", "study"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True)
        try:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline and parent.poll() is None:
                if self._in_production(root / "study" / "runs"):
                    break
                time.sleep(0.2)
            self.assertTrue(self._in_production(root / "study" / "runs"),
                            (root / "explore.log").read_text())
            parent.send_signal(signal.SIGTERM)
            parent.wait(timeout=120)
        finally:
            if parent.poll() is None:  # pragma: no cover - only on a failure
                os.killpg(parent.pid, signal.SIGKILL)
        said = (root / "explore.log").read_text()
        self.assertIn("Batch stopped:", said)
        batch = json.loads((root / "study" / "batch_manifest.json").read_text())
        statuses = [r["status"] for r in batch["runs"]]
        self.assertNotIn("running", statuses)
        self.assertEqual(statuses[-1], "skipped", said)
        self.assertTrue(batch["runs"][-1]["message"].startswith("Not started"))
        on_a_frame = []
        for run in batch["runs"]:
            sidecar = root / run["output_dir"] / "simulation" / "checkpoint.chk.json"
            if run["status"] == "error" and sidecar.is_file():
                side = json.loads(sidecar.read_text())
                if not side["finished"] and side["step"] % 200 == 0:
                    on_a_frame.append(run["run_id"])
        self.assertTrue(on_a_frame, said)

        resumed = subprocess.run([*command, "resume", "study"], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=1500)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        batch = json.loads((root / "study" / "batch_manifest.json").read_text())
        self.assertEqual([r["status"] for r in batch["runs"]], ["ok", "ok", "ok"])
        again = subprocess.run([*command, "resume", "study"], cwd=root, env=env,
                               capture_output=True, text=True, timeout=600)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

    @staticmethod
    def _in_production(runs: Path) -> list[Path]:
        found = []
        for status in runs.glob("*/simulation/live_status.json"):
            try:
                now = json.loads(status.read_text())
            except ValueError:  # caught mid-write
                continue
            if now.get("stage") == "production" and now.get("status") == "running":
                found.append(status)
        return found


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
