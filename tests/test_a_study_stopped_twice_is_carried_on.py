"""A study stopped twice is carried on, and its pieces joined as before.

A study killed in production is carried on from its last checkpoint, and
its pieces are joined with the first one's frames past that checkpoint
left out. Killed again in its analysis, it is carried on by analysing the
joined trajectory again; that join was made with no frames left out, so it
refused the first piece as one that did not finish, and the study could not
be carried on at all.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

from tests.test_a_carried_on_study_reads_its_energy_whole import _config
from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAStudyStoppedTwiceIsCarriedOn(unittest.TestCase):
    """Killed 250 steps into production (its checkpoint at 200), carried on
    and joined, then killed before its analysis: no Manifest is left."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock

        pytest.importorskip("openmm.app")
        import openmm.app

        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import extend_study, resume_study

        root = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, root, True)
        pdb = root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        real_step = openmm.app.Simulation.step
        taken = {"steps": 0}

        def step_until_killed(simulation, steps):
            if taken["steps"] >= 200 + 250:
                raise RuntimeError("killed")
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.study = root / "study"
        with mock.patch.object(openmm.app.Simulation, "step", step_until_killed):
            FastMDXplora(config_data=_config(pdb, telemetry_interval=25),
                         output_dir=str(cls.study)).explore()
        # A kill leaves no Manifest; one written by the run's own error would
        # read as an answer, not an interruption.
        (cls.study / "manifest.json").unlink()
        cls.carried_on = extend_study(cls.study, analyse=False)
        cls.copy = root / "copy"
        shutil.copytree(cls.study, cls.copy, symlinks=True)
        cls.answer = resume_study(cls.study)

    def test_the_first_carrying_on_left_out_the_frames_run_again(self):
        self.assertTrue(self.carried_on["ok"], self.carried_on.get("error"))
        self.assertEqual(self.carried_on["joined"]["trimmed"], {"0": 4})

    def test_carried_on_again_it_is_analysed(self):
        self.assertEqual((self.answer["ok"], self.answer["did"]), (True, "analysed"),
                         self.answer.get("error"))
        joined = json.loads((self.study / "joined" / "joined.json").read_text())
        self.assertEqual(joined["segments"], [0, 1])
        self.assertEqual(joined["trimmed"], {"0": 4})

    def test_its_analysis_reads_the_whole_run_once(self):
        record = json.loads((self.study / "analysis" / "thermodynamics" / "options.json")
                            .read_text())
        # 200 steps of the first piece and the 100 the second ran, a row
        # every 25: 12, the rows the first piece wrote past its checkpoint
        # left out.
        self.assertEqual(record["findings"]["thermodynamics"]["samples"], 12)

    def test_frames_that_cannot_be_counted_are_said_before_the_join(self):
        from unittest import mock

        from fastmdxplora.simulation import resume as resume_module

        with mock.patch.object(resume_module, "frames_before_checkpoint", return_value=None):
            answer = resume_module.resume_study(self.copy)
        self.assertEqual((answer["ok"], answer["did"], answer["stage"]),
                         (False, "analysed", "joining"))
        self.assertEqual(answer["unsealed"], [0])
        self.assertIn("the study's own run did not finish cleanly", answer["error"])
        self.assertEqual(answer["refusal"]["code"], "simulation.resume.unsealed")
        self.assertFalse((self.copy / "analysis" / "thermodynamics").exists())


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAnExtensionStoppedShortIsNotCalledWhole(unittest.TestCase):
    """Killed in production and carried on past its plan (400 more steps),
    the extension killed 250 steps in, after its checkpoint at 200: the
    production sealed reaches the plan, but the extension did not reach
    what it was asked for. Cut back to its checkpoint it was joined,
    analysed and called finished, the length asked for dropped without a
    word."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock

        pytest.importorskip("openmm.app")
        import openmm.app

        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import extend_study, resume_study

        root = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, root, True)
        pdb = root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        real_step = openmm.app.Simulation.step
        taken = {"steps": 0, "limit": 200 + 250}

        def step_until_killed(simulation, steps):
            if taken["steps"] >= taken["limit"]:
                raise RuntimeError("killed")
            taken["steps"] += int(steps)
            return real_step(simulation, steps)

        cls.study = root / "study"
        with mock.patch.object(openmm.app.Simulation, "step", step_until_killed):
            FastMDXplora(config_data=_config(pdb, telemetry_interval=25),
                         output_dir=str(cls.study)).explore()
            (cls.study / "manifest.json").unlink()
            taken.update(steps=0, limit=250)
            cls.extended = extend_study(cls.study, more_ns=0.0008)
        cls.answer = resume_study(cls.study)
        cls.again = resume_study(cls.study)

    def test_the_extension_was_stopped_short(self):
        self.assertFalse(self.extended["ok"])
        self.assertEqual(self.extended["stage"], "simulation")

    def test_it_is_not_cut_back_and_called_finished(self):
        self.assertEqual((self.answer["ok"], self.answer["did"]), (False, "analysed"))
        self.assertIn("[1]", self.answer["error"])
        self.assertFalse((self.study / "analysis" / "thermodynamics").exists())
        self.assertFalse(self.again["ok"])
        self.assertNotEqual(self.again["did"], "nothing")
