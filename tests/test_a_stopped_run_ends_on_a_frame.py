"""A run asked to stop ends on a frame, with a checkpoint there.

A scheduler, a cloud provider taking a machine back, a container being
replaced and the GUI's Stop button all send SIGTERM; a person at a terminal
presses Ctrl-C. The run used to end at once, keeping only its last interval
checkpoint, so up to `checkpoint_interval_steps` of production were run
again by `fastmdx resume`. A checkpoint written the moment the signal
arrived would sit between frames, and the run carrying it on would put its
frames off the grid of the ones before. So production steps on to the next
frame when the grace period allows, writes the checkpoint there, and ends
with a refusal a resume reads as an interruption.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
import unittest
from pathlib import Path

import pytest

from fastmdxplora.simulation import runner
from fastmdxplora.simulation.runner import (
    STOP_GRACE_ENV,
    STOPPED_CODE,
    _StopRequests,
    _step_to_a_frame,
    stop_grace_seconds,
)

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False


class _Stepper:
    """A simulation that counts the steps it is asked for."""

    def __init__(self) -> None:
        self.steps: list[int] = []

    def step(self, n: int) -> None:
        self.steps.append(int(n))


class TestItStepsToTheNextFrame:

    def _stopped(self) -> _StopRequests:
        stop = _StopRequests()
        stop.signal = signal.SIGTERM
        return stop

    def test_between_frames_it_steps_on_to_the_next(self) -> None:
        sim, stop = _Stepper(), self._stopped()
        added = _step_to_a_frame(sim, stop, done=130, frame_interval=50, elapsed=1.0)
        assert (added, sim.steps, stop.at_step, stop.on_frame) == (20, [20], 150, True)

    def test_on_a_frame_it_takes_no_step(self) -> None:
        sim, stop = _Stepper(), self._stopped()
        assert _step_to_a_frame(sim, stop, done=150, frame_interval=50, elapsed=1.0) == 0
        assert sim.steps == [] and stop.on_frame

    def test_a_frame_beyond_the_grace_period_is_not_reached(self, monkeypatch) -> None:
        """At 10 steps a second, 20 more steps take 2 s against 1 s allowed."""
        monkeypatch.setenv(STOP_GRACE_ENV, "1")
        sim, stop = _Stepper(), self._stopped()
        assert _step_to_a_frame(sim, stop, done=130, frame_interval=50, elapsed=13.0) == 0
        assert sim.steps == [] and not stop.on_frame and stop.at_step == 130

    def test_a_frame_past_the_end_of_production_is_not_reached(self) -> None:
        """Production of 140 steps with frames every 50: the stop at 130
        leaves 10 steps, and the next frame is 20 away."""
        sim, stop = _Stepper(), self._stopped()
        assert _step_to_a_frame(sim, stop, done=130, frame_interval=50, elapsed=1.0,
                                limit=10) == 0
        assert sim.steps == [] and not stop.on_frame and stop.at_step == 130

    def test_a_frame_at_the_end_of_production_is_reached(self) -> None:
        sim, stop = _Stepper(), self._stopped()
        assert _step_to_a_frame(sim, stop, done=130, frame_interval=50, elapsed=1.0,
                                limit=20) == 20
        assert stop.on_frame and stop.at_step == 150

    def test_no_frames_written_means_no_frame_to_reach(self) -> None:
        sim, stop = _Stepper(), self._stopped()
        assert _step_to_a_frame(sim, stop, done=130, frame_interval=None, elapsed=1.0) == 0
        assert not stop.on_frame

    @pytest.mark.parametrize("given, expected", [("45", 45.0), ("-3", 0.0), ("soon", 20.0)])
    def test_the_grace_period_is_read_from_the_environment(self, monkeypatch, given, expected) -> None:
        monkeypatch.setenv(STOP_GRACE_ENV, given)
        assert stop_grace_seconds() == expected


class TestTheSignalIsNoted:

    def test_a_first_signal_is_noted_and_a_handler_restored_after(self) -> None:
        before = signal.getsignal(signal.SIGTERM)
        with _StopRequests() as stop:
            os.kill(os.getpid(), signal.SIGTERM)
            assert stop.requested and stop.name == "SIGTERM"
        assert signal.getsignal(signal.SIGTERM) == before

    def test_a_second_ctrl_c_is_obeyed_at_once(self) -> None:
        with pytest.raises(KeyboardInterrupt):
            with _StopRequests() as stop:
                os.kill(os.getpid(), signal.SIGINT)
                assert stop.requested
                os.kill(os.getpid(), signal.SIGINT)

    def test_off_the_main_thread_nothing_is_installed(self) -> None:
        import threading

        seen = {}

        def elsewhere():
            with _StopRequests() as stop:
                seen["installed"] = bool(stop._previous)

        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        assert seen == {"installed": False}

    def test_a_long_chunk_is_cut_so_a_stop_is_noticed(self) -> None:
        # 1,000 steps took 20 s, so 5 s is 250 steps.
        assert runner._cap_for_stopping(5000, 1000, 20.0) == 250
        assert runner._cap_for_stopping(5000, 0, 0.0) == 5000


def test_a_study_with_no_record_is_left_as_it_is(tmp_path) -> None:
    """Carried on and joined, a study whose Manifest cannot be read is not
    given one."""
    from fastmdxplora.simulation.resume import _production_carried_on

    _production_carried_on(tmp_path, {"segments": [0, 1], "frames": 6})
    assert not (tmp_path / "manifest.json").exists()
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    _production_carried_on(tmp_path, {"segments": [0, 1], "frames": 6})
    assert (tmp_path / "manifest.json").read_text(encoding="utf-8") == "{not json"


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestARealRunStoppedMidProduction(unittest.TestCase):
    """A real run sent SIGTERM 130 steps into a 300-step production, between
    its frames at 100 and 150, after its checkpoint at 100. It ends on frame
    3 at step 150 with a checkpoint there, and a resume carries it on to 300
    steps, six frames evenly spaced."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock

        pytest.importorskip("openmm.app")
        import openmm.app

        from fastmdxplora import FastMDXplora
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        root = Path(tempfile.mkdtemp())
        pdb = root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        config = {
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 300,
                           "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                           "trajectory_interval_steps": 50,
                           "checkpoint_interval_steps": 100,
                           "telemetry_interval": 10},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }
        real_step = openmm.app.Simulation.step
        taken = {"steps": 0, "sent": False}

        def step_then_stop(simulation, steps):
            if taken["steps"] >= 200 + 130 and not taken["sent"]:
                taken["sent"] = True
                os.kill(os.getpid(), signal.SIGTERM)
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.stopped = root / "stopped"
        with mock.patch.object(openmm.app.Simulation, "step", step_then_stop):
            FastMDXplora(config_data=config, output_dir=str(cls.stopped)).explore()

    def copy(self) -> Path:
        target = Path(tempfile.mkdtemp()) / "study"
        shutil.copytree(self.stopped, target)
        return target

    def test_it_ended_on_a_frame_with_a_checkpoint_there(self):
        side = json.loads((self.stopped / "simulation" / "checkpoint.chk.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual((side["step"], side["finished"]), (150, False))
        manifest = json.loads((self.stopped / "manifest.json").read_text(encoding="utf-8"))
        simulation = next(p for p in manifest["phases"] if p["name"] == "simulation")
        self.assertEqual(simulation["status"], "error")
        self.assertEqual(simulation["refusal"]["code"], STOPPED_CODE)
        self.assertTrue(simulation["refusal"]["retryable"])
        self.assertIn("frame 3", simulation["message"])
        # Its live record says it stopped, not that it failed.
        from fastmdxplora.gui.telemetry import read_status

        self.assertEqual(read_status(self.stopped)["status"], "stopped")

    def test_it_is_carried_on_from_that_frame(self):
        import mdtraj

        from fastmdxplora.simulation.resume import resume_study

        study = self.copy()
        answer = resume_study(study)
        self.assertTrue(answer["ok"], answer.get("error"))
        self.assertEqual(answer["did"], "continued")
        self.assertAlmostEqual(answer["production_done_ns"], 150 * 2e-6)
        joined = mdtraj.load(str(study / "joined" / "production.dcd"),
                             top=str(study / "simulation" / "trajectory_topology.pdb"))
        self.assertEqual(joined.n_frames, 6)
        self.assertTrue((study / "report" / "report.md").is_file())
        # The record says production was carried on, so a second resume
        # has nothing to do; it tried to join the stopped piece again.
        manifest = json.loads((study / "manifest.json").read_text(encoding="utf-8"))
        simulation = next(p for p in manifest["phases"] if p["name"] == "simulation")
        self.assertEqual(simulation["status"], "ok")
        self.assertEqual(simulation["carried_on_from"]["refusal"]["code"], STOPPED_CODE)
        self.assertIn("6 frames", simulation["message"])
        again = resume_study(study)
        self.assertTrue(again["ok"], again.get("error"))
        self.assertEqual(again["did"], "nothing")
