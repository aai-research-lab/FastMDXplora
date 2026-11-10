"""An extended study analysed again is analysed whole, and can be again.

A study extended in pieces and copied (cp, rsync, scp from a cluster: each
file given the time it was copied) had a first piece newer than its join,
read as a study simulated again in place: `fastmdx analyze --rerun` analysed
the first piece alone (1,000 of 10,000 frames), its result written over the
whole study's with no warning, and the record it wrote gave the first
piece's `production_steps` beside the study's `duration_ns`, so the next
analyze was refused. The join's topology was read where the study was
copied from.

A real study of 200 production steps is extended twice by 100, copied
without its files' times, the original removed, and analysed again twice.
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


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestACopiedExtendedStudyIsAnalysedWhole(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import extend_study
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        cls.root = Path(tempfile.mkdtemp())
        pdb = cls.root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        study = cls.root / "study"
        FastMDXplora(config_data={
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.0, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 200,
                           "nvt_steps": 50, "npt_steps": 50, "timestep_fs": 2,
                           "trajectory_interval_steps": 50,
                           "checkpoint_interval_steps": 100},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }, output_dir=str(study)).explore()
        for _ in range(2):
            answer = extend_study(study, more_ns=0.0002)
            assert answer["ok"], answer
        # Copied as cp -r copies, each file the time it was copied, the
        # first piece last; the study it came from gone.
        cls.copy = cls.root / "copy"
        shutil.copytree(study, cls.copy, copy_function=shutil.copyfile)
        (cls.copy / "simulation" / "production.dcd").touch()
        shutil.rmtree(study)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _analysed_again(self) -> dict:
        from fastmdxplora.cli.main import main

        self.assertEqual(main(["analyze", "--output", str(self.copy), "--rerun"]), 0)
        return json.loads((self.copy / "analysis" / "analysis_manifest.json")
                          .read_text(encoding="utf-8"))

    def test_it_reads_its_join_each_time(self):
        joined = self.copy / "joined" / "production.dcd"
        frames = json.loads((self.copy / "joined" / "joined.json")
                            .read_text(encoding="utf-8"))["frames"]
        self.assertEqual(frames, 8)
        for _ in range(2):
            analysed = self._analysed_again()
            self.assertEqual(Path(analysed["trajectory_input"]), joined)
            self.assertEqual(analysed["n_frames"], frames)
            self.assertEqual(Path(analysed["topology_input"]),
                             self.copy / "simulation" / "trajectory_topology.pdb")
            config = yaml.safe_load((self.copy / "resolved_config.yml")
                                    .read_text(encoding="utf-8"))
            self.assertAlmostEqual(config["simulation"]["duration_ns"], 0.0008)
            self.assertIsNone(config["simulation"].get("production_steps"))
            self.assertEqual(Path(config["analysis"]["trajectory"]), joined)
