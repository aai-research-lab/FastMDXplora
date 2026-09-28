"""A study of several runs that stopped part-way is carried on by one command.

`fastmdx resume` carried on a study of one run and refused a study of
several, whose runs had to be resumed one by one, after which nothing
rebuilt what the study says across them. Now each run that started is
carried on as a study of one is, each that never started is run, the runs
share out the workers and devices the study asked for, and the aggregate
and comparison are rebuilt once every run has its answer.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
import yaml

from fastmdxplora.simulation import resume as resume_module
from fastmdxplora.simulation.resume import RESUMED_RUNS, _found_from, resume_study

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False


def _campaign(root: Path, *, started: tuple[str, ...]) -> Path:
    """A study of two runs as a stopped one leaves it: its config and manifest
    at the root, and a folder with a resolved config for each run that
    started."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("a.pdb", "b.pdb"):
        (root.parent / name).write_text("END\n", encoding="utf-8")
    (root / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "a", "system": "a.pdb"}, {"id": "b", "system": "b.pdb"}],
        "simulation": {"production_steps": 100},
    }), encoding="utf-8")
    (root / "batch_manifest.json").write_text(json.dumps({"kind": "batch"}), encoding="utf-8")
    for run in started:
        folder = root / "runs" / run
        folder.mkdir(parents=True)
        (folder / "resolved_config.yml").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture()
def what_ran(monkeypatch):
    """What the resume carried on and what it ran, instead of doing either."""
    calls: list[tuple[str, str, dict]] = []

    def resumed(study, *, device_index=None):
        calls.append(("resumed", Path(study).name, {"device": device_index}))
        return {"ok": True, "did": "continued"}

    class Study:
        def __init__(self, *, system, output_dir, **kwargs):
            self.system, self.output = system, output_dir

        def explore(self, *, include_phase=None, exclude_phase=None, report=True,
                    force=False):
            calls.append(("ran", Path(self.output).name,
                          {"force": force, "system": self.system}))
            return []

    import fastmdxplora

    monkeypatch.setattr(resume_module, "resume_study", resumed)
    monkeypatch.setattr(fastmdxplora, "FastMDXplora", Study)
    return calls


class TestEachRunGetsWhatItNeeds:

    def test_a_started_run_is_carried_on_and_the_other_is_run(self, tmp_path, what_ran,
                                                                monkeypatch) -> None:
        study = _campaign(tmp_path / "study", started=("a",))
        monkeypatch.chdir(tmp_path / "study")  # not where the study was started
        from fastmdxplora.simulation.resume import resume_batch

        answer = resume_batch(study)
        assert answer["ok"], answer.get("error")
        assert answer["did"] == RESUMED_RUNS
        assert [call[:2] for call in what_ran] == [("resumed", "a"), ("ran", "b")]
        # Never started, so its folder holds nothing worth protecting; and its
        # structure found beside the study, not from where the resume ran.
        ran = what_ran[1][2]
        assert ran["force"] is True
        assert Path(ran["system"]) == (tmp_path / "b.pdb").resolve()
        assert {r["run"]: r["status"] for r in answer["runs"]} == {"a": "ok", "b": "ok"}
        manifest = json.loads((study / "batch_manifest.json").read_text(encoding="utf-8"))
        assert [r["run_id"] for r in manifest["runs"]] == ["a", "b"]

    def test_resume_study_hands_a_campaign_to_the_campaign_resume(self, tmp_path,
                                                                 monkeypatch) -> None:
        study = _campaign(tmp_path / "study", started=())
        monkeypatch.setattr(resume_module, "resume_batch",
                            lambda root: {"ok": True, "did": RESUMED_RUNS, "root": str(root)})
        answer = resume_study(study)
        assert answer["did"] == RESUMED_RUNS

    def test_a_run_that_cannot_be_carried_on_is_said(self, tmp_path, what_ran,
                                                     monkeypatch) -> None:
        study = _campaign(tmp_path / "study", started=("a", "b"))
        monkeypatch.setattr(
            resume_module, "resume_study",
            lambda s, **_: {"ok": False, "error": "it stopped with an answer"}
            if Path(s).name == "b" else {"ok": True, "did": "nothing"})
        from fastmdxplora.simulation.resume import resume_batch

        answer = resume_batch(study)
        assert answer["ok"] is False
        assert "1 of 2 runs did not finish: b: it stopped with an answer" in answer["error"]


class TestWhatIsNotRunAgain:

    def test_a_run_still_going_holds_the_whole_study(self, tmp_path, what_ran,
                                                     monkeypatch) -> None:
        study = _campaign(tmp_path / "study", started=("a",))
        monkeypatch.setattr(resume_module, "_still_running",
                            lambda root: Path(root).name == "a")
        from fastmdxplora.simulation.resume import resume_batch

        answer = resume_batch(study)
        assert answer["ok"] is False and "a of this study is still running" in answer["error"]
        assert what_ran == []

    def test_the_study_itself_still_going(self, tmp_path, what_ran, monkeypatch) -> None:
        study = _campaign(tmp_path / "study", started=())
        monkeypatch.setattr(resume_module, "_still_running",
                            lambda root: Path(root) == study.resolve())
        from fastmdxplora.simulation.resume import resume_batch

        assert "still running" in resume_batch(study)["error"]
        assert what_ran == []


class TestAStructureIsFoundWhereTheStudyWas:

    def test_beside_the_study(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "x.pdb").write_text("END\n")
        (tmp_path / "study").mkdir()
        monkeypatch.chdir(tmp_path / "study")
        assert _found_from(tmp_path / "study", "x.pdb") == str((tmp_path / "x.pdb").resolve())

    def test_an_identifier_or_an_absolute_path_is_left_as_given(self, tmp_path) -> None:
        assert _found_from(tmp_path, "1UBQ") == "1UBQ"
        assert _found_from(tmp_path, str(tmp_path / "gone.pdb")) == str(tmp_path / "gone.pdb")
        assert _found_from(tmp_path, None) is None


def test_the_command_says_what_it_did(tmp_path, monkeypatch, capsys) -> None:
    from fastmdxplora.cli.main import main

    monkeypatch.setattr(resume_module, "resume_study",
                        lambda study: {"ok": True, "did": RESUMED_RUNS})
    assert main(["resume", str(tmp_path)]) == 0
    assert "Every run of the study" in capsys.readouterr().out


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestARealCampaignStoppedPartWay(unittest.TestCase):
    """Two runs of a temperature sweep. The first is stopped in production
    after its checkpoint at step 200, as a kill leaves it: no Manifest. The
    second never started, and nor did the study's comparison. Resumed, the
    first is carried on to its 300 steps and the second is run, and the
    comparison across them is written."""

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
            "sweep": {"simulation.temperature_K": [300, 310]},
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
                raise SystemExit("killed")
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.stopped = root / "sweep"
        with mock.patch.object(openmm.app.Simulation, "step", step_until_killed):
            try:
                FastMDXplora(config_data=config, output_dir=str(cls.stopped)).explore()
            except SystemExit:
                pass
        planned = json.loads((cls.stopped / "batch_manifest.json")
                             .read_text(encoding="utf-8"))["planned"]
        cls.first, cls.second = (spec["run_id"] for spec in planned)
        # As a kill leaves it: the first run's Manifest was never written,
        # the second run never started, and neither did the comparison.
        runs = cls.stopped / "runs"
        (runs / cls.first / "manifest.json").unlink(missing_ok=True)
        shutil.rmtree(runs / cls.second, ignore_errors=True)
        for leftover in ("comparison", "aggregate.json", "comparison.md"):
            target = cls.stopped / leftover
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    def test_every_run_is_finished_and_the_study_says_so(self):
        import mdtraj

        study = Path(tempfile.mkdtemp()) / "sweep"
        shutil.copytree(self.stopped, study)
        answer = resume_study(study)
        self.assertTrue(answer["ok"], answer.get("error"))
        self.assertEqual(answer["did"], RESUMED_RUNS)
        first = study / "runs" / self.first
        joined = mdtraj.load(str(first / "joined" / "production.dcd"),
                             top=str(first / "simulation" / "trajectory_topology.pdb"))
        self.assertEqual(joined.n_frames, 6)
        second = study / "runs" / self.second
        self.assertTrue((second / "report" / "report.md").is_file())
        manifest = json.loads((study / "batch_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([r["status"] for r in manifest["runs"]], ["ok", "ok"])
        # Once more, and nothing is run.
        again = resume_study(study)
        self.assertTrue(again["ok"])
        self.assertTrue(all(r["message"] for r in again["runs"]))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
