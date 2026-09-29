"""A mean is recorded with its unit, and whoever reads it is told the unit.

The Agent was handed each analysis's mean as a bare number ("rmsd: mean
0.0212 +/- 0.0014 (s.e.)"), so a model asked for the RMSD had nothing to say
whether that was nm or Angstrom, and a series with no error was handed over
as "+/- nan (s.e.)". The captions and the run table knew the unit only for
the five analyses named in a table in the GUI; an end-to-end distance or an
area per lipid was captioned without one.

The analysis now writes the unit beside its mean, read from its own axis
("RMSD (nm)"), and the GUI and the Agent read it from there.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


def _trajectory(n_frames: int = 400, n_atoms: int = 12, seed: int = 0):
    rng = np.random.RandomState(seed)
    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("ALA", chain)
    for i in range(n_atoms):
        topology.add_atom(f"C{i}", md.element.carbon, residue)
    base = rng.normal(0, 0.05, (n_frames, n_atoms, 3))
    relax = np.exp(-np.arange(n_frames) / 40.0)[:, None, None]
    return md.Trajectory(base + 0.5 * relax, topology)


def _study(tmp_path: Path, findings: dict[str, dict]) -> Path:
    root = tmp_path / "study"
    for name, mean in findings.items():
        folder = root / "analysis" / name
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(
            json.dumps({"analysis": name, "findings": {"mean": mean}}), encoding="utf-8")
    return root


class TestTheAnalysisRecordsIt:
    def test_a_run_writes_the_unit_beside_the_mean(self, tmp_path):
        from fastmdxplora.analysis import get_analysis_class

        analysis = get_analysis_class("rg")(output_dir=tmp_path)
        assert analysis.run(_trajectory()).status == "ok"

        assert analysis.findings["mean"]["unit"] == "nm"
        written = json.loads((tmp_path / "rg" / "options.json").read_text(encoding="utf-8"))
        assert written["findings"]["mean"]["unit"] == "nm"

    @pytest.mark.parametrize("name, unit", [
        ("rmsd", "nm"), ("rg", "nm"), ("end_to_end", "nm"), ("sasa", "nm²"),
        # Written "nm2" on the axis; nm squared in the record.
        ("area_per_lipid", "nm²"), ("moments_of_inertia", "amu nm²"),
        # A count and a fraction have no unit, and say so with "".
        ("hbonds", ""), ("qvalue", ""), ("coordination_number", ""),
    ])
    def test_each_series_names_its_own(self, name, unit):
        from fastmdxplora.analysis import get_analysis_class

        assert get_analysis_class(name)()._recorded_unit() == unit

    def test_a_label_the_person_set_is_not_the_unit(self, tmp_path):
        """The axis label is the person's to write; the unit is the quantity's."""
        from fastmdxplora.analysis import get_analysis_class

        analysis = get_analysis_class("rg")(output_dir=tmp_path / "rg",
                                            ylabel="Compactness (arbitrary)")
        analysis.run(_trajectory())
        assert analysis.findings["mean"]["unit"] == "nm"

    def test_an_analysis_with_no_axis_records_no_unit(self):
        from fastmdxplora.analysis.base import Analysis

        class Bare(Analysis):
            name = "bare"
            time_series = True

            def compute(self, traj):  # pragma: no cover - never run
                return None

            def plot(self, result, ax):  # pragma: no cover - never run
                return None

        assert Bare()._recorded_unit() is None

    def test_a_description_in_brackets_is_not_a_unit(self):
        from fastmdxplora.analysis.base import _unit_in

        assert _unit_in("RMSD (nm)") == "nm"
        assert _unit_in("Q (fraction native contacts)") == ""
        assert _unit_in("Neighbours in shell") == ""


class TestTheGUIReadsIt:
    def test_the_recorded_unit_comes_first(self):
        from fastmdxplora.gui.report_dashboard import unit_of

        assert unit_of("end_to_end", {"mean": 1.0, "unit": "nm"}) == "nm"
        assert unit_of("rmsd", {"mean": 1.0, "unit": ""}) == ""
        # A study analysed before units were recorded keeps the known ones.
        assert unit_of("rmsd", {"mean": 1.0}) == "nm"
        assert unit_of("end_to_end", None) == ""

    def test_a_caption_gives_the_recorded_unit(self, tmp_path):
        """An end-to-end distance was captioned without its unit."""
        from fastmdxplora.gui.report_dashboard import _what_the_analysis_found

        root = _study(tmp_path, {"end_to_end": {
            "mean": 2.4, "standard_error": 0.05, "effective_samples": 40.0,
            "discard": 10, "n_frames": 200, "unit": "nm"}})
        (root / "analysis" / "end_to_end" / "end_to_end.dat").write_text("0 2.4\n", encoding="utf-8")
        caption = _what_the_analysis_found(root / "analysis" / "end_to_end" / "end_to_end.dat")
        assert caption.startswith("mean 2.400 \u00b1 0.050 nm after equilibration")

    def test_the_run_table_heads_a_column_with_it(self, tmp_path):
        from fastmdxplora.gui.report_page import _so_far

        run = tmp_path / "runs" / "a"
        folder = run / "analysis" / "area_per_lipid"
        folder.mkdir(parents=True)
        (folder / "options.json").write_text(json.dumps({"analysis": "area_per_lipid", "findings": {
            "mean": {"mean": 0.62, "standard_error": 0.004, "unit": "nm²"}}}), encoding="utf-8")
        runs = [{"run_id": "a", "path": str(run), "values": {}}]
        text = _so_far(tmp_path, runs, runs)
        assert "area_per_lipid mean (nm²)" in text


class TestTheAgentIsTold:
    def test_a_mean_comes_with_its_unit(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _results_summary

        text = _results_summary(_study(tmp_path, {
            "rmsd": {"mean": 0.0212, "standard_error": 0.0014, "effective_samples": 40.6,
                     "discard": 57, "n_frames": 200, "unit": "nm"},
            "area_per_lipid": {"mean": 0.62, "effective_samples": 30.0, "unit": "nm²"},
            "hbonds": {"mean": 3.2, "standard_error": 0.1, "effective_samples": 30.0, "unit": ""}}))
        assert "rmsd: mean 0.0212 ± 0.0014 nm (s.e.)" in text
        assert "area_per_lipid: mean 0.62 nm²," in text
        assert "hbonds: mean 3.2 ± 0.1 (s.e.)" in text

    def test_a_study_analysed_before_units_were_recorded(self, tmp_path):
        from fastmdxplora.gui.agent_panel import _results_summary

        text = _results_summary(_study(tmp_path, {"rg": {"mean": 1.2, "standard_error": 0.01}}))
        assert "rg: mean 1.2 ± 0.01 nm (s.e.)" in text

    def test_an_error_that_is_not_a_number_is_not_given(self, tmp_path):
        """A series whose error was withheld records NaN, and the Agent was
        told "+/- nan (s.e.)"."""
        from fastmdxplora.gui.agent_panel import _results_summary

        text = _results_summary(_study(tmp_path, {"rg": {
            "mean": 0.328, "standard_error": float("nan"), "effective_samples": 14.1,
            "not_a_measurement": "too short against its correlation time", "unit": "nm"}}))
        assert "nan" not in text.lower()
        assert "rg: mean 0.328 nm, 14.1 effective samples" in text
