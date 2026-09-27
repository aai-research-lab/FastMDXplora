"""`resume_from` naming a study continues it from Python as from the CLI.

The command line read `simulation.resume_from` naming a study directory as
a continuation of that study: its next segment inside it, the join, and the
analyses and report over the whole. From Python the same config went to the
batch layer, which read it as a new study, and refused it for naming no
system. One reading now serves both, and the lengths mean the same:
`duration_ns` the total production the study should end with, `extra_ns`
an amount more.
"""

from __future__ import annotations

import json
import logging
import pathlib
import tempfile
import unittest

import pytest

from fastmdxplora import FastMDXplora
from fastmdxplora.simulation import resume
from tests.test_a_study_is_extended_in_place import _study


class TestTheReading:

    def test_a_study_is_continued_and_a_checkpoint_is_not(self, tmp_path) -> None:
        checkpoint = tmp_path / "checkpoint.chk"
        checkpoint.write_bytes(b"x")
        assert resume.study_to_continue({"simulation": {"resume_from": str(tmp_path)}}) == tmp_path
        assert resume.study_to_continue({"simulation": {"resume_from": str(checkpoint)}}) is None
        assert resume.study_to_continue({"simulation": {}}) is None


class TestFromPython:

    def _asked(self, monkeypatch, answer):
        asked = []

        def extend(study, *, total_ns=None, more_ns=None, analyse=True):
            asked.append((pathlib.Path(study), total_ns, more_ns))
            return answer

        monkeypatch.setattr(resume, "extend_study", extend)
        return asked

    @pytest.mark.parametrize("lengths, expected", [
        ({"duration_ns": 0.6}, (0.6, None)), ({"extra_ns": 0.1}, (None, 0.1)), ({}, (None, None))])
    def test_the_lengths_mean_what_they_mean_on_the_command_line(
            self, monkeypatch, lengths, expected) -> None:
        root = _study()
        asked = self._asked(monkeypatch, {
            "ok": True, "segment": str(root / "segment-001"),
            "joined": {"segments": [0, 1]}, "analysed": False})
        results = FastMDXplora(config_data={
            "simulation": {"resume_from": str(root), **lengths}}).explore()
        assert asked == [(root.resolve(), *expected)]
        [result] = results
        assert result.status == "ok"
        assert result.output_dir == root.resolve()
        assert "segment-001" in result.message

    def test_a_refusal_is_a_result_with_its_reason(self, monkeypatch) -> None:
        root = _study()
        self._asked(monkeypatch, {"ok": False, "stage": "planning",
                                  "error": "production already reached 0.500 ns"})
        [result] = FastMDXplora(config_data={
            "simulation": {"resume_from": str(root)}}).explore()
        assert result.status == "error"
        assert "already reached" in result.message

    def test_a_dry_run_says_what_it_would_do_and_runs_nothing(self, monkeypatch) -> None:
        root = _study(done_steps=50_000, finished=False)
        asked = self._asked(monkeypatch, {"ok": True})
        [result] = FastMDXplora(config_data={
            "simulation": {"resume_from": str(root), "duration_ns": 0.5}}).explore(dry_run=True)
        assert asked == []
        assert result.status == "planned"
        assert "production done 0.100 ns of 0.500 ns planned" in result.message


try:
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401

    HAS_BACKENDS = True
except ImportError:  # pragma: no cover
    HAS_BACKENDS = False


@pytest.mark.slow
@unittest.skipUnless(HAS_BACKENDS, "OpenMM and PDBFixer are needed")
class TestARealStudyContinuedFromPython(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        cls._propagate = logging.getLogger("fastmdx").propagate
        cls.root = pathlib.Path(tempfile.mkdtemp())
        pdb = cls.root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        cls.output = cls.root / "study"
        simulation = {"platform": "CPU", "nvt_steps": 100, "npt_steps": 100,
                      "timestep_fs": 2, "duration_ns": 0.0004,
                      "trajectory_interval_steps": 20}
        FastMDXplora(config_data={
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
            "simulation": simulation,
            "analysis": {"include": ["rmsd"]},
        }, output_dir=str(cls.output)).explore()
        cls.results = FastMDXplora(config_data={
            "simulation": {"resume_from": str(cls.output), "extra_ns": 0.0004}}).explore()

    @classmethod
    def tearDownClass(cls):
        logging.getLogger("fastmdx").propagate = cls._propagate

    def test_it_ran_the_next_segment_inside_the_study(self):
        [result] = self.results
        self.assertEqual(result.status, "ok", result.message)
        self.assertEqual(result.output_dir, self.output.resolve())
        self.assertTrue((self.output / "segment-001" / "simulation" / "production.dcd").is_file())

    def test_it_joined_both_and_analysed_the_whole(self):
        joined = json.loads((self.output / "joined" / "joined.json").read_text())
        self.assertEqual(joined["segments"], [0, 1])
        analysed = json.loads(
            (self.output / "analysis" / "analysis_manifest.json").read_text())
        self.assertEqual(pathlib.Path(analysed["trajectory_input"]),
                         self.output / "joined" / "production.dcd")
        self.assertEqual(analysed["n_frames"], joined["frames"])
