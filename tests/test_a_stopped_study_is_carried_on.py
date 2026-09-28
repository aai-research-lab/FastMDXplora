"""A study stopped part-way is carried on to its end, by one command.

A rented GPU can be taken back mid-run, a machine can restart, a job can reach
its time limit. `fastmdx resume STUDY` reads how far the study got and does
what is left, once: nothing if it finished; the analyses and report if
production did; the rest of production from the last sealed checkpoint if it
had begun; the whole study again if it had not. A study that stopped with a
refusal is not run again, since that was its answer. A service runs the same
command every time a job restarts, so running it twice must do the work once.

The decision is checked on studies laid out as each case leaves them; the
continuation is checked on a real run, stopped for real in production.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest
import yaml

from fastmdxplora.simulation import resume as resume_module
from fastmdxplora.simulation.resume import Continuation, resume_study

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False


def _study(folder: Path, *, duration_ns: float = 1.0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "tri", "system": "tri.pdb"}],
        "simulation": {"duration_ns": duration_ns},
    }), encoding="utf-8")
    return folder


def _manifest(folder: Path, phases: list[dict]) -> None:
    (folder / "manifest.json").write_text(json.dumps({"phases": phases}), encoding="utf-8")


EVERY_PHASE = [{"name": p, "status": "ok"} for p in ("setup", "simulation", "analysis", "report")]


@pytest.fixture()
def ran(monkeypatch):
    """What a resume asked to run, instead of running it."""
    calls: list[tuple[str, dict]] = []

    class Recorder:
        def __init__(self, *, config_data, output_dir):
            self.config, self.output = config_data, output_dir

        def explore(self, force=False):
            calls.append(("explore", {"config": self.config, "output": self.output,
                                      "force": force}))

    import fastmdxplora

    monkeypatch.setattr(fastmdxplora, "FastMDXplora", Recorder)
    monkeypatch.setattr(resume_module, "extend_study",
                        lambda study, **_: calls.append(("extend", {"study": study}))
                        or {"ok": True, "segment": "segment-001"})
    return calls


def _progress(monkeypatch, *, done: float, planned: float, refusal: str | None) -> None:
    monkeypatch.setattr(resume_module, "extension_of", lambda study: Continuation(
        parent=str(study), checkpoint="", production_done_ns=done,
        production_planned_ns=planned, config={}, refusal=refusal))


class TestWhatIsLeftDecidesWhatRuns:

    def test_a_finished_study_is_left_alone(self, tmp_path, ran) -> None:
        study = _study(tmp_path / "s")
        _manifest(study, [*EVERY_PHASE[:3], {"name": "report", "status": "skipped"}])
        answer = resume_study(study)
        assert (answer["ok"], answer["did"]) == (True, "nothing")
        assert ran == []

    def test_a_manifest_missing_a_planned_phase_is_not_an_ending(self, tmp_path, ran, monkeypatch) -> None:
        study = _study(tmp_path / "s")
        _manifest(study, EVERY_PHASE[:2])
        _progress(monkeypatch, done=1.0, planned=1.0, refusal="production already reached")
        assert resume_study(study)["did"] == "analysed"

    def test_a_plan_without_a_report_finishes_without_one(self, tmp_path, ran) -> None:
        study = _study(tmp_path / "s")
        config = yaml.safe_load((study / "resolved_config.yml").read_text())
        config["exclude_phase"] = ["report"]
        (study / "resolved_config.yml").write_text(yaml.safe_dump(config))
        _manifest(study, EVERY_PHASE[:3])
        assert resume_study(study)["did"] == "nothing"

    def test_production_begun_is_carried_on_from_its_checkpoint(self, tmp_path, ran, monkeypatch) -> None:
        study = _study(tmp_path / "s")
        _progress(monkeypatch, done=0.4, planned=1.0, refusal=None)
        answer = resume_study(study)
        assert (answer["ok"], answer["did"]) == (True, "continued")
        assert ran == [("extend", {"study": study.resolve()})]
        assert (answer["production_done_ns"], answer["production_planned_ns"]) == (0.4, 1.0)

    def test_production_complete_runs_the_analyses_and_report_only(self, tmp_path, ran, monkeypatch) -> None:
        study = _study(tmp_path / "s")
        (study / "simulation").mkdir()
        (study / "simulation" / "trajectory_topology.pdb").write_text("END\n")
        _progress(monkeypatch, done=1.0, planned=1.0, refusal="production already reached")
        answer = resume_study(study)
        assert (answer["ok"], answer["did"]) == (True, "analysed")
        (kind, call), = ran
        assert kind == "explore" and call["force"] is True
        assert call["config"]["include_phase"] == ["analysis", "report"]
        assert call["config"]["analysis"]["trajectory"] == str(
            study.resolve() / "simulation" / "production.dcd")
        assert call["config"]["analysis"]["topology"].endswith("trajectory_topology.pdb")

    def test_production_not_begun_runs_the_study_from_its_start(self, tmp_path, ran, monkeypatch) -> None:
        study = _study(tmp_path / "s")
        _progress(monkeypatch, done=0.0, planned=1.0,
                  refusal="the study has no checkpoint; checkpoints are written during production")
        answer = resume_study(study)
        assert (answer["ok"], answer["did"]) == (True, "restarted")
        (kind, call), = ran
        assert kind == "explore" and call["force"] is True
        assert call["output"] == str(study.resolve())
        assert "include_phase" not in call["config"]

    def test_production_that_cannot_be_continued_is_not_thrown_away(self, tmp_path, ran, monkeypatch) -> None:
        """Written production is not discarded by starting again; the
        reason it cannot be continued is said instead."""
        study = _study(tmp_path / "s")
        _progress(monkeypatch, done=0.3, planned=1.0, refusal="the checkpoint is off the frame grid")
        answer = resume_study(study)
        assert answer["ok"] is False and ran == []
        assert "0.300 ns of production is written" in answer["error"]
        assert "off the frame grid" in answer["error"]


class TestWhatIsNotRunAgain:

    def test_a_folder_that_is_not_a_study(self, tmp_path, ran) -> None:
        answer = resume_study(tmp_path)
        assert answer["ok"] is False and "no resolved_config.yml" in answer["error"]
        assert ran == []

    def test_a_study_still_running(self, tmp_path, ran, monkeypatch) -> None:
        from fastmdxplora.gui import exploration
        from fastmdxplora.orchestrator import RUN_PROCESS_FILE

        study = _study(tmp_path / "s")
        # Another live process: the resume's own number is never the run's.
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (study / RUN_PROCESS_FILE).write_text(json.dumps({"pid": other.pid}))
            monkeypatch.setattr(exploration, "_process_is_this_run",
                                lambda pid, root, argv=None: True)
            answer = resume_study(study)
        finally:
            other.kill()
            other.wait()
        assert answer["ok"] is False and "still going" in answer["error"]
        assert ran == []

    def test_a_study_that_stopped_with_a_refusal(self, tmp_path, ran) -> None:
        study = _study(tmp_path / "s")
        _manifest(study, [{"name": "setup", "status": "error",
                           "message": "Ligand BNZ clashes with the protein",
                           "refusal": {"code": "setup.structure.clash", "retryable": False}}])
        answer = resume_study(study)
        assert answer["ok"] is False and ran == []
        assert "setup: Ligand BNZ clashes with the protein" in answer["error"]

    def test_a_refusal_worth_retrying_is_an_interruption(self, tmp_path, ran, monkeypatch) -> None:
        """A GPU that went away looks, from inside a run, like this."""
        study = _study(tmp_path / "s")
        _manifest(study, [{"name": "simulation", "status": "error",
                           "message": "the CUDA platform is unavailable",
                           "refusal": {"code": "environment.platform.unavailable",
                                       "retryable": True}}])
        _progress(monkeypatch, done=0.4, planned=1.0, refusal=None)
        assert resume_study(study)["did"] == "continued"

    def test_a_manifest_older_than_the_run_is_not_its_ending(self, tmp_path, ran, monkeypatch) -> None:
        """A study run again over an earlier one keeps the earlier Manifest
        until it ends. A run that is killed leaves its record of itself
        behind, and a Manifest older than that run says nothing about how
        it ended."""
        from datetime import datetime, timezone

        from fastmdxplora.orchestrator import RUN_PROCESS_FILE

        study = _study(tmp_path / "s")
        _manifest(study, EVERY_PHASE)
        earlier = time.time() - 60
        os.utime(study / "manifest.json", (earlier, earlier))
        (study / RUN_PROCESS_FILE).write_text(json.dumps(
            {"pid": 2 ** 22 + 7, "started_at": datetime.now(timezone.utc).isoformat()}))
        _progress(monkeypatch, done=0.4, planned=1.0, refusal=None)
        assert resume_study(study)["did"] == "continued"


class TestTheCommand:

    def test_it_says_what_it_did_and_exits_zero(self, tmp_path, ran, capsys) -> None:
        from fastmdxplora.cli.main import main

        study = _study(tmp_path / "s")
        _manifest(study, EVERY_PHASE)
        assert main(["resume", str(study)]) == 0
        assert "Nothing to do: the study finished." in capsys.readouterr().out

    def test_json_for_a_program(self, tmp_path, ran, capsys) -> None:
        from fastmdxplora.cli.main import main

        study = _study(tmp_path / "s")
        _manifest(study, EVERY_PHASE)
        assert main(["resume", str(study), "--json"]) == 0
        answer = json.loads(capsys.readouterr().out)
        assert (answer["ok"], answer["did"]) == (True, "nothing")

    def test_a_refusal_exits_one_and_says_why(self, tmp_path, ran, capsys) -> None:
        from fastmdxplora.cli.main import main

        assert main(["resume", str(tmp_path)]) == 1
        said = capsys.readouterr().err
        assert said.startswith("fastmdx: ") and "no resolved_config.yml" in said


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAStudyStoppedInProductionIsFinished(unittest.TestCase):
    """A real run, stopped 250 steps into a 300-step production, after its
    checkpoint at step 200, as a process that is killed leaves it: no
    Manifest. Resumed once, it holds the 300 planned steps and its report;
    resumed again, nothing more is run."""

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
                           "telemetry_interval": 50},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }
        real_step = openmm.app.Simulation.step
        taken = {"steps": 0}

        def step_until_killed(simulation, steps):
            if taken["steps"] >= 200 + 250:
                raise RuntimeError("killed")
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.killed = root / "killed"
        with mock.patch.object(openmm.app.Simulation, "step", step_until_killed):
            FastMDXplora(config_data=config, output_dir=str(cls.killed)).explore()
        # A process that is killed writes no Manifest; this one ended by an
        # exception, which does, so the Manifest goes as a kill leaves it.
        (cls.killed / "manifest.json").unlink()

    def copy(self) -> Path:
        target = Path(tempfile.mkdtemp()) / "study"
        shutil.copytree(self.killed, target)
        return target

    def test_it_is_carried_on_to_its_end_and_reported(self):
        import mdtraj

        study = self.copy()
        answer = resume_study(study)
        self.assertTrue(answer["ok"], answer.get("error"))
        self.assertEqual(answer["did"], "continued")
        joined = mdtraj.load(str(study / "joined" / "production.dcd"),
                             top=str(study / "simulation" / "trajectory_topology.pdb"))
        self.assertEqual(joined.n_frames, 6)  # 300 steps at 50, each frame once
        self.assertTrue((study / "report" / "report.md").is_file())

    def test_resuming_again_runs_nothing(self):
        study = self.copy()
        self.assertTrue(resume_study(study)["ok"])
        before = sorted(p.name for p in study.iterdir())
        again = resume_study(study)
        self.assertEqual((again["ok"], again["did"]), (True, "nothing"))
        self.assertEqual(sorted(p.name for p in study.iterdir()), before)
