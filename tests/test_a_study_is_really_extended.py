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

    def test_a_killed_run_is_resumed_from_its_last_checkpoint(self):
        from fastmdxplora.simulation.resume import extend_study, frames_before_checkpoint
        from fastmdxplora.simulation.runner import CHECKPOINT_DIGEST_SUFFIX

        study = self.study()
        # What a kill leaves: the checkpoint, and no seal on it.
        (study / "simulation" / ("checkpoint.chk" + CHECKPOINT_DIGEST_SUFFIX)).unlink(
            missing_ok=True)
        keep = frames_before_checkpoint(study)
        self.assertIsNotNone(keep)

        answer = extend_study(study, more_ns=0.0006, analyse=False)
        self.assertTrue(answer["ok"], answer.get("error"))
        record = answer["joined"]
        self.assertEqual(record["trimmed"], {"0": keep})
        segment = _frames(study / "segment-001" / "simulation" / "production.dcd",
                          self.topology)
        # The frames after the checkpoint are left out, so the pieces meet
        # rather than overlap.
        self.assertEqual(record["frames"], keep + segment)
        record_file = json.loads((study / "joined" / "joined.json").read_text())
        self.assertEqual(record_file["trimmed"], {"0": keep})

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
