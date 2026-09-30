"""`duration_ns: 0` is a study that equilibrates and stops.

`duration_ns` is the production length, so zero means no production. The
runner read zero as unset and ran the default two nanoseconds, while the cost
estimate counted none. Now the runner runs none, the analysis phase is
recorded as skipped with its reason rather than as completed, and the methods
paragraph says no production was run instead of describing a trajectory that
was never written.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import pytest

from fastmdxplora.cost import total_steps
from fastmdxplora.simulation.runner import plan_stages


def _plan(**stated):
    return plan_stages(timestep_fs=2.0, nvt_steps=None, npt_steps=None,
                       production_steps=None, **stated)


def test_zero_production_is_zero() -> None:
    assert _plan(duration_ns=0)["production_steps"] == 0


def test_unset_production_is_still_the_default() -> None:
    assert _plan(duration_ns=None)["production_steps"] == 1_000_000  # 2 ns at 2 fs


def test_the_cost_estimate_counts_what_the_runner_runs() -> None:
    plan = _plan(duration_ns=0)
    assert total_steps({"duration_ns": 0, "timestep_fs": 2.0}) == \
        plan["nvt_steps"] + plan["npt_steps"] + plan["production_steps"]


def test_the_analysis_phase_says_why_it_did_nothing(tmp_path) -> None:
    from fastmdxplora import FastMDXplora

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "simulation_parameters.json").write_text(json.dumps(
        {"phase": "simulation", "resolved": {"production_steps": 0}}))
    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    result = fmdx._run_phase("analysis", {})
    assert result.status == "skipped"
    assert "no production" in result.message
    record = json.loads((tmp_path / "analysis" / "analysis_manifest.json").read_text())
    assert record["status"] == "skipped"


def test_a_missing_trajectory_for_any_other_reason_is_still_deferred(tmp_path) -> None:
    from fastmdxplora import FastMDXplora

    fmdx = FastMDXplora(system="x.pdb", output_dir=tmp_path)
    result = fmdx._run_phase("analysis", {})
    assert result.status == "ok"
    record = json.loads((tmp_path / "analysis" / "analysis_manifest.json").read_text())
    assert record["status"] == "deferred"


try:
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401

    HAS_BACKENDS = True
except ImportError:  # pragma: no cover
    HAS_BACKENDS = False


@pytest.mark.slow
@unittest.skipUnless(HAS_BACKENDS, "OpenMM and PDBFixer are needed")
class TestARealStudyThatEquilibratesOnly(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import logging

        from fastmdxplora import FastMDXplora
        from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

        cls._propagate = logging.getLogger("fastmdx").propagate
        cls.root = pathlib.Path(tempfile.mkdtemp())
        pdb = cls.root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        config = {
            "systems": [{"id": "tri", "system": str(pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "nvt_steps": 100, "npt_steps": 100,
                           "timestep_fs": 2, "duration_ns": 0},
        }
        cls.output = cls.root / "out"
        cls.results = FastMDXplora(config_data=config, output_dir=str(cls.output)).explore()
        cls.manifest = json.loads((cls.output / "manifest.json").read_text())

    @classmethod
    def tearDownClass(cls):
        import logging

        logging.getLogger("fastmdx").propagate = cls._propagate

    def test_it_ran_no_production(self):
        record = json.loads(
            (self.output / "simulation" / "simulation_parameters.json").read_text())
        self.assertEqual(record["resolved"]["production_steps"], 0)
        self.assertFalse((self.output / "simulation" / "production.dcd").is_file()
                         and (self.output / "simulation" / "production.dcd").stat().st_size)

    def test_the_study_succeeded_and_analysis_says_it_was_skipped(self):
        self.assertEqual([r.status for r in self.results], ["ok"])
        phases = {p["name"]: p for p in self.manifest["phases"]}
        self.assertEqual(phases["simulation"]["status"], "ok")
        self.assertEqual(phases["analysis"]["status"], "skipped")
        self.assertIn("no production", phases["analysis"]["message"])
        self.assertEqual(phases["report"]["status"], "ok")

    def test_the_methods_say_so(self):
        report = (self.output / "report" / "report.md").read_text()
        methods = report.split("### Every setting used")[0]
        self.assertIn("No production was run: the study equilibrated only.", methods)
        self.assertNotIn("Production dynamics were run", methods)
        self.assertNotIn("Coordinates were written", methods)
        self.assertIn("during NPT equilibration", methods)
