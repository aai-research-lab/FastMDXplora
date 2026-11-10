"""A study stopped while its joined pieces are analysed is carried on.

An extended study's analyses and report run again over the joined
trajectory once its new piece has run. They ran from the last piece's
config, and that run wrote it over the study's `resolved_config.yml` with the
first piece's step count beside the piece's own length (from the study's
simulation record). A JCC rerun stopped at its wall time in that analysis
was then refused by `fastmdx resume`: "simulation gives 'production_steps'
(2,000 steps) and 'duration_ns' (0.006 ns, 3,000 steps at 2 fs)". Until the
analysis ended, the study's config was its last piece's (no minimisation, no
equilibration, its own length), and that was what a reader found.

A real study is run, extended, and stopped where the analysis of the join
has written its config and the study's has not been put back: as a kill
leaves it.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE


class _Killed(BaseException):
    """The process killed: nothing after it runs, no handler sees it."""


def _config(root: Path) -> dict:
    return yaml.safe_load((root / "resolved_config.yml").read_text(encoding="utf-8"))


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAStudyStoppedInItsJoinSAnalysis(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastmdxplora import FastMDXplora

        cls.root = Path(tempfile.mkdtemp())
        pdb = cls.root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        config = {
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.0, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 200,
                           "nvt_steps": 50, "npt_steps": 50, "timestep_fs": 2,
                           "trajectory_interval_steps": 50,
                           "checkpoint_interval_steps": 100},
            "analysis": {"include": ["rmsd"]},
            "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
        }
        cls.study = cls.root / "study"
        FastMDXplora(config_data=config, output_dir=str(cls.study)).explore()
        cls.first = _config(cls.study)
        # Extended by 100 steps, and stopped in the analyses of the join, as
        # its report begins: the study's analyses are rerun over the joined
        # trajectory, and the config written at their start is in place.
        import fastmdxplora.report as report
        from fastmdxplora.simulation.resume import extend_study

        cls.during: list[dict] = []

        def killed(*_args, **_kwargs):
            cls.during.append(_config(cls.study))
            raise _Killed()

        with mock.patch.object(report, "run", killed):
            try:
                extend_study(cls.study, more_ns=0.0002)
            except _Killed:
                pass

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_it_was_stopped_in_the_analysis(self):
        self.assertEqual(len(self.during), 1)
        self.assertTrue((self.study / "segment-001" / "simulation" / "state_final.xml").is_file())
        self.assertTrue((self.study / "joined" / "joined.json").is_file())

    def test_the_study_s_config_is_what_a_reader_finds_meanwhile(self):
        for found in self.during:
            simulation = found["simulation"]
            self.assertFalse(simulation.get("resume_from"))
            self.assertEqual((simulation["nvt_steps"], simulation["npt_steps"]), (50, 50))
            self.assertEqual(simulation.get("minimize"), self.first["simulation"].get("minimize"))
            self.assertAlmostEqual(simulation["duration_ns"], 0.0006)
            self.assertIsNone(simulation.get("production_steps"))

    def test_fastmdx_resume_carries_it_on(self):
        from fastmdxplora.simulation.resume import resume_study

        study = self.study
        answer = resume_study(study)
        self.assertTrue(answer["ok"], answer.get("error"))
        self.assertEqual(answer["did"], "analysed")
        simulation = _config(study)["simulation"]
        self.assertAlmostEqual(simulation["duration_ns"], 0.0006)
        self.assertFalse(simulation.get("resume_from"))
        self.assertEqual(simulation["nvt_steps"], 50)
        self.assertTrue((study / "report" / "report.md").is_file())
