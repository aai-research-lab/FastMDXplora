"""A study carried on in pieces is read for its energy over every piece.

An extended or resumed study is analysed over its joined trajectory, but
its energy, temperature and density were read from the first piece's
`simulation/energy.csv` alone: the thermodynamics analysis, the Overview's
means, the report's convergence and the dashboard's temperature all gave
the first piece's numbers for a run of two. Each piece's clock starts again
at production, and a stopped piece holds rows past the checkpoint the next
piece went on from, so the pieces are read to that checkpoint and carried on
from where the one before ended.
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

from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

#: 300 production steps a piece, a state row every 25: 12 rows a piece.
_CONFIG = {
    "setup": {"ph": 7.0, "solvent_padding_nm": 1.2, "nonbonded_cutoff_nm": 0.9},
    "simulation": {"platform": "CPU", "production_steps": 300,
                   "nvt_steps": 100, "npt_steps": 100, "timestep_fs": 2,
                   "trajectory_interval_steps": 50, "state_interval_steps": 25,
                   "checkpoint_interval_steps": 100},
    "analysis": {"include": ["rmsd", "thermodynamics"]},
    "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
}


def _config(pdb: Path, **simulation) -> dict:
    config = json.loads(json.dumps(_CONFIG))
    config["systems"] = [{"id": "tri", "system": str(pdb)}]
    config["simulation"].update(simulation)
    return config


def _rows(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines()[1:] if line.strip())


def _thermodynamics(study: Path) -> dict:
    record = json.loads((study / "analysis" / "thermodynamics" / "options.json").read_text())
    return record["findings"]["thermodynamics"]


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAnExtendedStudyIsReadWhole(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import extend_study

        root = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, root, True)
        pdb = root / "tri-ala.pdb"
        pdb.write_text(TRI_ALANINE)
        cls.study = root / "study"
        FastMDXplora(config_data=_config(pdb), output_dir=str(cls.study)).explore()
        cls.answer = extend_study(cls.study, more_ns=0.0006)   # 300 more steps

    def test_it_was_extended_in_two_pieces_of_twelve_rows(self):
        self.assertTrue(self.answer["ok"], self.answer.get("error"))
        self.assertEqual(self.answer["joined"]["segments"], [0, 1])
        self.assertEqual(_rows(self.study / "simulation" / "energy.csv"), 12)
        self.assertEqual(_rows(self.study / "segment-001" / "simulation" / "energy.csv"), 12)

    def test_the_thermodynamics_reads_both_pieces(self):
        record = _thermodynamics(self.study)
        self.assertEqual(record["samples"], 24)
        self.assertEqual([Path(p).parent.parent.name for p in record["pieces"]],
                         [self.study.name, "segment-001"])

    def test_one_clock_runs_through_both_pieces(self):
        import numpy as np

        from fastmdxplora.analysis.thermodynamics import (
            read_pieces_state_table, state_record_pieces)

        table = read_pieces_state_table(state_record_pieces(self.study))
        steps = table["Step"]
        self.assertEqual(list(steps), [25.0 * k for k in range(1, 25)])
        times = table["Time (ps)"]
        self.assertTrue(np.allclose(np.diff(times), 0.05))

    def test_the_overview_averages_both_pieces(self):
        from fastmdxplora.gui.overview_view import _production_energies

        rows = _production_energies(self.study)
        self.assertEqual(len(rows), 24)
        times = [float(row["simulation_time_ns"]) for row in rows]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[-1], 0.0012, places=9)

    def test_the_report_judges_the_energy_over_both_pieces(self):
        from fastmdxplora.report.document import _assess_this_run

        assessed = _assess_this_run(self.study)
        self.assertEqual(assessed["observables"]["potential_energy"]["frames"], 24)

    def test_the_report_fits_the_drift_over_the_length_it_read(self):
        """A piece on disk that the join does not hold (an extension stopped
        before its first checkpoint, or still going) is no length of the
        series: the drift was fitted as if 1.2 ps of rows spanned 1.8."""
        from unittest import mock

        from fastmdxplora.report import convergence
        from fastmdxplora.report.document import _assess_this_run

        copy = Path(tempfile.mkdtemp()) / "study"
        self.addCleanup(shutil.rmtree, copy.parent, True)
        shutil.copytree(self.study, copy)
        (copy / "segment-002").mkdir()
        shutil.copy(copy / "segment-001" / "resolved_config.yml",
                    copy / "segment-002" / "resolved_config.yml")
        seen = {}
        real = convergence.assess_run

        def kept(series, **kwargs):
            seen.update(kwargs)
            return real(series, **kwargs)

        with mock.patch.object(convergence, "assess_run", kept):
            _assess_this_run(copy)
        self.assertAlmostEqual(seen["duration_ns"], 0.0012, places=9)

    def test_the_overview_gives_no_energy_means_when_a_piece_has_no_record(self):
        from fastmdxplora.gui.overview_view import _thermodynamics

        copy = Path(tempfile.mkdtemp()) / "study"
        self.addCleanup(shutil.rmtree, copy.parent, True)
        shutil.copytree(self.study, copy)
        (copy / "segment-001" / "simulation" / "energy.csv").unlink()
        (copy / "analysis" / "thermodynamics" / "options.json").unlink()
        means = _thermodynamics(copy)["means"]
        self.assertEqual({key: value for key, value in means.items() if value}, {})

    def _copy(self) -> Path:
        copy = Path(tempfile.mkdtemp()) / "study"
        self.addCleanup(shutil.rmtree, copy.parent, True)
        shutil.copytree(self.study, copy)
        return copy

    def test_a_piece_whose_integrator_chose_its_step_goes_on_from_its_own_time(self):
        """Its steps are no fixed time: the next piece's clock starts where
        its last row kept says, not at steps times the starting timestep."""
        from fastmdxplora.analysis.thermodynamics import state_record_pieces
        from fastmdxplora.simulation.runner import VARIABLE_STEP_INTEGRATORS

        copy = self._copy()
        record_path = copy / "simulation" / "simulation_parameters.json"
        record = json.loads(record_path.read_text())
        record.setdefault("parameters", {})["integrator"] = sorted(VARIABLE_STEP_INTEGRATORS)[0]
        record_path.write_text(json.dumps(record))
        energy = copy / "simulation" / "energy.csv"
        lines = energy.read_text().splitlines()
        # The integrator went 3 fs a step on average, not the 2 it started at.
        rows = [lines[0]] + [",".join(cells[:2] + [repr(float(cells[1]) * 0.003)] + cells[3:])
                             for cells in (line.split(",") for line in lines[1:])]
        energy.write_text("\n".join(rows) + "\n")
        pieces = state_record_pieces(copy)
        self.assertAlmostEqual(pieces[1]["ps_before"], 0.9, places=9)

    def test_a_cell_written_as_nan_is_kept_as_one_run_keeps_it(self):
        import math

        from fastmdxplora.analysis.thermodynamics import (
            read_pieces_state_table, state_record_pieces)

        copy = self._copy()
        energy = copy / "segment-001" / "simulation" / "energy.csv"
        lines = energy.read_text().splitlines()
        cells = lines[3].split(",")
        cells[6] = "nan"
        lines[3] = ",".join(cells)
        energy.write_text("\n".join(lines) + "\n")
        table = read_pieces_state_table(state_record_pieces(copy))
        temperature = table["Temperature (K)"]
        self.assertEqual(len(temperature), len(table["Step"]))
        self.assertTrue(math.isnan(temperature[12 + 2]))

    def test_a_damaged_byte_in_a_piece_s_record_is_one_cell_unread(self):
        from fastmdxplora.gui.overview_view import _thermodynamics
        from fastmdxplora.gui.report_dashboard import _average_temperature
        from fastmdxplora.report.document import _assess_this_run

        copy = self._copy()
        for piece in (copy / "simulation", copy / "segment-001" / "simulation"):
            with (piece / "energy.csv").open("ab") as handle:
                handle.write(b"\xff\xfe,1\n")
        self.assertIsNotNone(_average_temperature(copy / "simulation" / "energy.csv"))
        self.assertEqual(_assess_this_run(copy)["observables"]["potential_energy"]["frames"], 24)
        self.assertIsNotNone(_thermodynamics(copy)["production_start_ns"])

    def test_a_piece_without_its_state_record_is_said(self):
        from fastmdxplora.analysis.thermodynamics import state_record_pieces
        from fastmdxplora.refusals import MissingResultError

        copy = Path(tempfile.mkdtemp()) / "study"
        self.addCleanup(shutil.rmtree, copy.parent, True)
        shutil.copytree(self.study, copy)
        (copy / "segment-001" / "simulation" / "energy.csv").unlink()
        with self.assertRaisesRegex(MissingResultError, "segment 1 has no state record"):
            state_record_pieces(copy)

    def test_the_dashboard_temperature_is_both_pieces(self):
        import numpy as np

        from fastmdxplora.analysis.thermodynamics import read_state_table
        from fastmdxplora.gui.report_dashboard import _average_temperature

        both = np.concatenate([
            read_state_table(self.study / "simulation" / "energy.csv")["Temperature (K)"],
            read_state_table(self.study / "segment-001" / "simulation" / "energy.csv")[
                "Temperature (K)"]])
        self.assertAlmostEqual(
            _average_temperature(self.study / "simulation" / "energy.csv"),
            float(both.mean()), places=6)


@unittest.skipUnless(HAS_BACKENDS, "OpenMM, PDBFixer and MDTraj are needed")
class TestAStoppedPieceIsReadToItsCheckpoint(unittest.TestCase):
    """Stopped 250 steps into production, after its checkpoint at 200: the
    rows at 225 and 250 were run again by the next piece."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock

        pytest.importorskip("openmm.app")
        import openmm.app

        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import extend_study

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
        cls.answer = extend_study(cls.study)

    def test_the_stopped_piece_wrote_past_its_checkpoint(self):
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        self.assertTrue(self.answer["ok"], self.answer.get("error"))
        side = read_checkpoint_sidecar(self.study / "simulation" / "checkpoint.chk")
        self.assertEqual(side["step"], 200)
        self.assertGreater(_rows(self.study / "simulation" / "energy.csv"), 8)

    def test_its_rows_after_the_checkpoint_are_left_out(self):
        record = _thermodynamics(self.study)
        # 200 steps of the first piece and the 100 the second ran: 300
        # steps, a row every 25, each once.
        self.assertEqual(record["samples"], 12)

        from fastmdxplora.analysis.thermodynamics import (
            read_pieces_state_table, state_record_pieces)

        table = read_pieces_state_table(state_record_pieces(self.study))
        self.assertEqual(list(table["Step"]), [25.0 * k for k in range(1, 13)])
        # The second piece's clock carried on from the checkpoint at 0.4 ps.
        import numpy as np

        self.assertTrue(np.allclose(table["Time (ps)"], [0.05 * k for k in range(1, 13)]))


def test_a_study_of_one_run_is_read_as_before(tmp_path) -> None:
    from fastmdxplora.analysis.thermodynamics import state_record_pieces

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "energy.csv").write_text(
        '#"Step","Time (ps)","Temperature (K)"\n25,0.05,300\n50,0.1,301\n')
    assert state_record_pieces(tmp_path) is None
