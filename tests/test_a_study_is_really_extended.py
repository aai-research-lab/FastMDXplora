"""A real study, really extended: simulated, joined, reanalysed.

The continuation's planning and refusals were tested; the part that does
the work -- running the segment, joining the pieces, rerunning the report
over the whole -- was only ever run by hand, and several tests checked the
source for a line instead of running it. That proves nothing about
behaviour. These run it, on a real system, through the Python API and
through the command line.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE


def _frames(trajectory: Path, topology: Path) -> int:
    import mdtraj

    return mdtraj.load(str(trajectory), top=str(topology)).n_frames


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAStudyIsReallyExtended(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastmdxplora import FastMDXplora

        cls.root = Path(tempfile.mkdtemp())
        pdb = cls.root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        config = {
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 300,
                           "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                           "trajectory_interval_steps": 50,
                           # Checkpoints inside production, so the last one
                           # carries the step a continuation counts from.
                           "checkpoint_interval_steps": 100},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }
        cls.parent = cls.root / "parent"
        FastMDXplora(config_data=config, output_dir=str(cls.parent)).explore()
        cls.topology = cls.parent / "simulation" / "trajectory_topology.pdb"
        cls.parent_frames = _frames(cls.parent / "simulation" / "production.dcd", cls.topology)

    def study(self) -> Path:
        """A fresh copy of the parent, so each case starts from the same one."""
        copy = Path(tempfile.mkdtemp()) / "study"
        shutil.copytree(self.parent, copy)
        return copy

    def test_the_parent_ran(self):
        self.assertGreater(self.parent_frames, 0)

    def test_its_checkpoint_counts_production_steps(self):
        # The counter is reset before production, so a checkpoint's step is
        # a production step: 300 here, not the 500 of the whole run. Getting
        # this wrong is what made "extend to 0.6 ns" ask for 0.3 ns more
        # instead of 0.1. The consequence, measured, rather than the line
        # that causes it, read.
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        side = read_checkpoint_sidecar(self.parent / "simulation" / "checkpoint.chk")
        self.assertEqual(side["stage"], "production")
        self.assertEqual(side["step"], 300)

    def test_its_checkpoint_is_sealed_and_nothing_is_left_half_written(self):
        # Each checkpoint and its seal are written as a pair -- both to
        # .new, then renamed in. A real run leaves the pair whole, verified
        # by the seal, and no .new file behind to be mistaken for either.
        from fastmdxplora.simulation.runner import verify_checkpoint

        simulation = self.parent / "simulation"
        self.assertTrue(verify_checkpoint(simulation / "checkpoint.chk", require_seal=True))
        self.assertEqual(sorted(p.name for p in simulation.glob("*.new")), [])

    def test_extending_simulates_joins_and_reanalyses(self):
        from fastmdxplora.simulation.resume import extend_study, production_done_ns

        study = self.study()
        before = production_done_ns(study)
        answer = extend_study(study, more_ns=0.0006)   # 300 more steps
        self.assertTrue(answer["ok"], answer.get("error"))

        # The segment is inside the study, not a second study beside it.
        self.assertEqual(Path(answer["segment"]), study.resolve() / "segment-001")
        self.assertEqual(answer["joined"]["segments"], [0, 1])

        # One trajectory: every frame of the parent, then every frame of the
        # segment, and nothing twice.
        joined = _frames(Path(answer["trajectory"]), self.topology)
        segment = _frames(study / "segment-001" / "simulation" / "production.dcd",
                          self.topology)
        self.assertEqual(joined, self.parent_frames + segment)

        # The report was rerun over the whole of it.
        self.assertTrue(answer["analysed"])
        self.assertTrue((study / "report" / "report.md").is_file())
        self.assertGreater(production_done_ns(study), before)

    def test_the_command_line_does_the_same(self):
        # `simulation.resume_from` naming a study, from a config file, is
        # the whole operation -- the same one as the Python call above.
        from fastmdxplora.cli.main import main

        study = self.study()
        config = Path(tempfile.mkdtemp()) / "extend.yml"
        config.write_text(yaml.safe_dump(
            {"simulation": {"resume_from": str(study), "extra_ns": 0.0006}}),
            encoding="utf-8")
        self.assertEqual(main(["explore", "--config", str(config)]), 0)
        self.assertTrue((study / "segment-001" / "simulation" / "production.dcd").is_file())
        self.assertTrue((study / "joined" / "production.dcd").is_file())


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAKilledRunIsResumedFromItsLastCheckpoint(unittest.TestCase):
    """A run stopped partway through production, then resumed.

    This used to be faked by deleting the checkpoint's seal, which is what
    a kill left before every checkpoint was sealed as it was written. A real
    kill leaves a sealed checkpoint, so the join took the killed run for a
    finished one, kept the frames written after its last checkpoint, and
    the resume ran that stretch again: the joined trajectory held it twice.
    Here the run is stopped for real, 250 steps into a 300-step production,
    after its checkpoint at step 200 and with a frame written after it.
    """

    @classmethod
    def setUpClass(cls):
        from unittest import mock

        import pytest

        pytest.importorskip("openmm.app")
        import openmm.app

        from fastmdxplora import FastMDXplora

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
                           # Steps taken fifty at a time, so the run can be
                           # stopped between two of them.
                           "telemetry_interval": 50},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": False, "slides": False, "pdf": False, "bundle": False},
        }
        real_step = openmm.app.Simulation.step
        taken = {"steps": 0}

        def step_until_killed(simulation, steps):
            # Equilibration is 200 steps; the process dies 250 into production.
            if taken["steps"] >= 200 + 250:
                raise RuntimeError("killed")
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.study = root / "study"
        with mock.patch.object(openmm.app.Simulation, "step", step_until_killed):
            FastMDXplora(config_data=config, output_dir=str(cls.study)).explore()
        cls.topology = cls.study / "simulation" / "trajectory_topology.pdb"
        cls.written = _frames(cls.study / "simulation" / "production.dcd", cls.topology)

    def test_it_was_stopped_after_a_checkpoint_and_a_frame_past_it(self):
        from fastmdxplora.analysis.joining import survey_segments
        from fastmdxplora.simulation.resume import frames_before_checkpoint

        from fastmdxplora.simulation.resume import _interval_from_telemetry
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        self.assertEqual(self.written, 5)
        self.assertEqual(frames_before_checkpoint(self.study), 4)
        self.assertFalse(survey_segments(self.study)[0].finished)
        # The interval is recorded with the checkpoint, and a run from
        # before it was has its own telemetry to read it from.
        sidecar = read_checkpoint_sidecar(self.study / "simulation" / "checkpoint.chk")
        self.assertEqual(sidecar["trajectory_interval_steps"], 50)
        self.assertEqual(_interval_from_telemetry(self.study / "simulation"), 50)

    def test_the_resume_leaves_nothing_twice(self):
        from fastmdxplora.simulation.resume import extend_study

        answer = extend_study(self.study, analyse=False)
        self.assertTrue(answer["ok"], answer.get("error"))
        record = json.loads((self.study / "joined" / "joined.json").read_text())
        # The frame after the checkpoint is left out, the remaining 100
        # steps are run, and the whole holds the 300 planned steps once.
        self.assertEqual(record["trimmed"], {"0": 4})
        self.assertEqual(record["frames"], 6)
        self.assertEqual(_frames(self.study / "joined" / "production.dcd", self.topology), 6)
        # One spacing, and the time it represents, for what analyses it.
        from fastmdxplora.analysis.analyze import _saving_interval_ps

        self.assertEqual(record["trajectory_interval_steps"], 50)
        joined = self.study / "joined" / "production.dcd"
        self.assertAlmostEqual(_saving_interval_ps(self.study, trajectory=joined), 0.1)
        # And the GUI plays the whole of it, not the killed run's own file,
        # whose status still says it is running.
        from fastmdxplora.gui.trajectory_playback import playback_info

        played = playback_info(self.study, force=True)
        self.assertEqual((played["source_kind"], played["n_frames_total"]), ("production-dcd", 6))


def test_checkpoints_are_placed_on_frames(tmp_path) -> None:
    # Asked for every 100 steps with a frame every 60, checkpoints go every
    # 120, so the step a run is resumed from is always one a frame was
    # written at.
    import pytest

    pytest.importorskip("openmm.app")
    from fastmdxplora.simulation.runner import run_simulation
    from tests._the_phase import a_prepared_water_box

    out = tmp_path / "study" / "simulation"
    result = run_simulation(**a_prepared_water_box(tmp_path), output_dir=str(out),
                            production_steps=300, nvt_steps=10, npt_steps=0, minimize=False,
                            platform="CPU", trajectory_interval_steps=60,
                            checkpoint_interval_steps=100)
    assert result.resolved["checkpoint_interval_steps"] == 120


def test_a_replaced_topology_is_played_again(tmp_path) -> None:
    # The same trajectory read against a different topology is a different
    # playback; the copy made from the first was served after the second
    # replaced it.
    import os

    import pytest

    pytest.importorskip("openmm.app")
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.trajectory_playback import playback_info
    from fastmdxplora.simulation.runner import run_simulation
    from tests._the_phase import a_prepared_water_box

    study = tmp_path / "study"
    run_simulation(**a_prepared_water_box(tmp_path), output_dir=str(study / "simulation"),
                   production_steps=200, nvt_steps=10, npt_steps=0, minimize=False,
                   platform="CPU", trajectory_interval_steps=50)
    (study / "simulation" / "live_status.json").write_text('{"status": "completed"}')
    first = playback_info(study)
    topology = next(p for p in (study / "simulation" / "trajectory_topology.pdb",
                                study / "simulation" / "topology.pdb") if p.is_file())
    later = topology.stat().st_mtime_ns + 5_000_000_000
    os.utime(topology, ns=(later, later))
    again = playback_info(study)
    assert first["source_kind"] == again["source_kind"] == "production-dcd"
    assert again["source_signature"] != first["source_signature"]
