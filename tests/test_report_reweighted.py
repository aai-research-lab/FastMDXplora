"""How a biased run's averages are presented, and in what order.

The numbers are computed and tested elsewhere. What is tested here is the
editorial decision: that the equilibrium average is the one the reader meets
first, that the biased one stays visible beside it, and that the effective
sample size cannot be read apart from the number it qualifies.
"""

from __future__ import annotations

import json
from pathlib import Path


from fastmdxplora.report.reweighted import (
    load_reweighted,
    reweighted_line,
    reweighted_section,
)


def _record(**overrides):
    record = {
        "n_frames": 500,
        "effective_sample_size": 114.6,
        "usable_fraction": 0.229,
        "settled": True,
        "quantities": [
            {"analysis": "rmsd", "label": "RMSD (nm)",
             "raw_mean": 0.2066, "raw_std": 0.0703,
             "reweighted_mean": 0.2813, "reweighted_std": 0.0182,
             "shift_percent": 36.14},
            {"analysis": "hbonds", "label": "Hydrogen bonds",
             "raw_mean": 41.2, "raw_std": 3.1,
             "reweighted_mean": 39.8, "reweighted_std": 2.4,
             "shift_percent": -3.4},
        ],
        "warnings": [],
    }
    record.update(overrides)
    return record


def _project(tmp_path: Path, record=None) -> Path:
    directory = tmp_path / "analysis" / "reweighted"
    directory.mkdir(parents=True, exist_ok=True)
    if record is not None:
        (directory / "reweighted_averages.json").write_text(
            json.dumps(record), encoding="utf-8")
    return tmp_path


class TestTheEquilibriumNumberLeads:
    """A mean over a metadynamics trajectory is a property of the sampling.
    Printing it first and the correction afterwards puts the wrong number in
    whatever a reader copies out."""

    def test_the_reweighted_column_comes_before_the_biased_one(
            self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record()))
        header = next(line for line in text.splitlines()
                      if line.startswith("| Quantity"))
        assert header.index("Reweighted") < header.index("Biased")

    def test_the_analysis_line_leads_with_the_correction(self) -> None:
        line = reweighted_line(_record(), "rmsd")
        assert line.index("0.2813") < line.index("0.2066")

    def test_the_biased_value_is_still_shown(self, tmp_path: Path) -> None:
        """Dropping it would hide how far the bias moved things, which is
        itself worth seeing."""
        text = reweighted_section(_project(tmp_path, _record()))
        assert "0.2066" in text and "41.2" in text


class TestTheSampleSizeTravelsWithTheNumber:
    def test_the_section_states_it(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record()))
        assert "115 effective frames of 500" in text

    def test_every_per_analysis_line_states_it(self) -> None:
        """A reader who goes straight to the RMSD heading and never reads the
        table above must still meet the number the mean rests on."""
        for name in ("rmsd", "hbonds"):
            assert "effective frames of 500" in reweighted_line(_record(), name)

    def test_a_thin_average_says_so_beside_the_number(
            self, tmp_path: Path) -> None:
        text = reweighted_section(
            _project(tmp_path, _record(effective_sample_size=7.2)))
        assert "rest on very few independent frames" in text

    def test_a_healthy_average_is_not_hedged(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record()))
        assert "very few independent frames" not in text


class TestItSaysWhatWasNotCorrected:
    def test_the_unreweighted_analysis_is_named(self, tmp_path: Path) -> None:
        """Its absence from the table would otherwise read as an oversight
        rather than a limit. A projection is not an average."""
        text = reweighted_section(_project(tmp_path, _record()))
        assert "dimensionality reduction is not reweighted" in text

    def test_a_bias_that_had_not_converged_is_marked(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record(settled=False)))
        assert "had not converged" in text

    def test_warnings_are_carried_through(self, tmp_path: Path) -> None:
        text = reweighted_section(
            _project(tmp_path, _record(warnings=["No temperature recorded."])))
        assert "No temperature recorded." in text

    def test_the_change_is_signed(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record()))
        assert "+36.1%" in text and "-3.4%" in text


class TestAnUnbiasedRunGetsNoSection:
    """Plain MD is already a Boltzmann ensemble, and a 'corrected' table for
    it would imply its averages had needed correcting."""

    def test_no_record_means_no_section(self, tmp_path: Path) -> None:
        assert load_reweighted(_project(tmp_path)) is None
        assert reweighted_section(_project(tmp_path)) == ""

    def test_no_record_means_no_analysis_line(self) -> None:
        assert reweighted_line(None, "rmsd") is None

    def test_an_analysis_that_was_not_reweighted_gets_no_line(self) -> None:
        assert reweighted_line(_record(), "cluster") is None

    def test_an_empty_record_is_not_a_section(self, tmp_path: Path) -> None:
        assert load_reweighted(_project(tmp_path, _record(quantities=[]))) is None

    def test_unreadable_json_does_not_break_the_report(
            self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        (root / "analysis" / "reweighted"
         / "reweighted_averages.json").write_text("{not json", encoding="utf-8")
        assert load_reweighted(root) is None


class TestItDoesNotPrintNonsense:
    def test_a_missing_value_becomes_a_dash(self, tmp_path: Path) -> None:
        record = _record(quantities=[
            {"analysis": "rmsd", "label": "RMSD (nm)",
             "raw_mean": 0.0, "raw_std": None,
             "reweighted_mean": None, "reweighted_std": None,
             "shift_percent": float("nan")}])
        text = reweighted_section(_project(tmp_path, record))
        assert "nan" not in text.lower()
        assert "—" in text


class TestTheDocumentUsesIt:
    def test_the_results_section_carries_the_table(self, tmp_path: Path) -> None:
        from fastmdxplora.report.document import _results_section

        root = _project(tmp_path, _record())
        (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
            "plan": ["rmsd", "reweighted"],
            "n_frames": 500, "n_residues": 20,
            "results": {"rmsd": {"status": "ok"},
                        "reweighted": {"status": "ok"}},
        }), encoding="utf-8")

        text = _results_section(root)
        assert "Averages after reweighting" in text
        # The pass has its own section and must not also appear as a bare
        # analysis heading with nothing under it.
        assert "### Reweighted\n" not in text
        # And the RMSD heading carries the corrected number.
        assert text.index("Averages after reweighting") < text.index("### RMSD")
        assert "Reweighted mean: 0.2813" in text


class TestTheDashboardTableIsCorrectedToo:
    """The metrics table is the first thing anyone opening the dashboard
    reads, and it was computing plain means straight off the .dat files."""

    @staticmethod
    def _dashboard_project(tmp_path: Path, record=None) -> Path:
        root = _project(tmp_path, record)
        for name, values in (("rmsd", [0.20, 0.21, 0.22]),
                             ("rmsf", [0.11, 0.12, 0.13]),
                             ("hbonds", [40, 41, 42])):
            directory = root / "analysis" / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{name}.dat").write_text(
                "\n".join(f"{index} {value}"
                          for index, value in enumerate(values)) + "\n",
                encoding="utf-8")
        return root

    def test_a_reweighted_metric_shows_the_corrected_value(
            self, tmp_path: Path) -> None:
        from fastmdxplora.gui.report_dashboard import _metric_rows

        rows = _metric_rows(self._dashboard_project(tmp_path, _record()), {})
        rmsd = next(r for r in rows if r.metric.startswith("RMSD"))
        assert "reweighted" in rmsd.metric
        assert rmsd.average == "0.2813"

    def test_a_metric_with_no_correction_is_labelled_biased(
            self, tmp_path: Path) -> None:
        """RMSF is per atom, not per frame, so no weighted mean applies. The
        row must not read as a measurement of the unbiased system."""
        from fastmdxplora.gui.report_dashboard import _metric_rows

        rows = _metric_rows(self._dashboard_project(tmp_path, _record()), {})
        rmsf = next(r for r in rows if r.metric.startswith("RMSF"))
        assert "biased ensemble" in rmsf.metric

    def test_the_effective_frame_count_is_a_row(self, tmp_path: Path) -> None:
        from fastmdxplora.gui.report_dashboard import _metric_rows

        rows = _metric_rows(self._dashboard_project(tmp_path, _record()), {})
        row = next(r for r in rows if "Effective frames" in r.metric)
        assert row.unit == "of 500"

    def test_an_unbiased_run_keeps_its_plain_labels(
            self, tmp_path: Path) -> None:
        from fastmdxplora.gui.report_dashboard import _metric_rows

        rows = _metric_rows(self._dashboard_project(tmp_path), {})
        assert [r.metric for r in rows if r.metric.startswith("RMS")] == [
            "RMSD", "RMSF"]
        assert not any("Effective frames" in r.metric for r in rows)

    def test_a_nan_spread_becomes_a_dash(self, tmp_path: Path) -> None:
        """One effective frame gives no spread. That is an outcome of a badly
        converged run, not an error to raise while building a page."""
        from fastmdxplora.gui.report_dashboard import _metric_rows

        record = _record(quantities=[
            {"analysis": "rmsd", "label": "RMSD (nm)",
             "raw_mean": 0.2, "raw_std": 0.07,
             "reweighted_mean": 0.28, "reweighted_std": float("nan"),
             "shift_percent": 40.0}])
        rows = _metric_rows(self._dashboard_project(tmp_path, record), {})
        rmsd = next(r for r in rows if r.metric.startswith("RMSD"))
        assert rmsd.stddev == "—"


def _with_populations(**overrides):
    return _record(populations=[{
        "analysis": "cluster", "method": "kmeans",
        "states": [
            {"label": 0, "raw_fraction": 0.46,
             "reweighted_fraction": 0.110, "shift_percent": -76.1},
            {"label": 1, "raw_fraction": 0.54,
             "reweighted_fraction": 0.890, "shift_percent": 64.9},
        ],
    }], populations_caveat=(
        "The clustering was performed on the biased frames, so the states "
        "themselves are shaped by where the bias sent the system."),
        **overrides)


class TestPopulationsAreReported:
    def test_the_occupancies_appear_as_percentages(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _with_populations()))
        assert "State populations after reweighting" in text
        assert "11.0%" in text and "89.0%" in text

    def test_the_reweighted_column_still_leads(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _with_populations()))
        header = next(line for line in text.splitlines()
                      if line.startswith("| State"))
        assert header.index("Reweighted") < header.index("Biased")

    def test_the_biased_occupancies_stay_visible(self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _with_populations()))
        assert "46.0%" in text and "54.0%" in text

    def test_what_reweighting_does_not_fix_is_said(self, tmp_path: Path) -> None:
        """Reweighting corrects how often a state was visited, not which
        states were found. The clustering ran on the biased frames."""
        text = reweighted_section(_project(tmp_path, _with_populations()))
        assert "performed on the biased frames" in text

    def test_the_method_is_named(self, tmp_path: Path) -> None:
        """Clustering runs several methods and they disagree usefully, so a
        table that did not say which one produced it would be unreadable."""
        text = reweighted_section(_project(tmp_path, _with_populations()))
        assert "kmeans" in text

    def test_a_run_with_only_populations_still_gets_a_section(
            self, tmp_path: Path) -> None:
        record = _with_populations(quantities=[])
        assert load_reweighted(_project(tmp_path, record)) is not None
        assert "State populations" in reweighted_section(
            _project(tmp_path, record))

    def test_a_run_without_them_says_nothing_about_them(
            self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path, _record()))
        assert "State populations" not in text


class TestAnUncorrectableBiasIsLabelledNotHidden:
    @staticmethod
    def _record_not_applicable(method: str = "steered"):
        return {
            "n_frames": 500, "applies": False, "biasing_method": method,
            "reason": ("Production was a steered pull, which is not an "
                       "equilibrium ensemble."),
            "quantities": [], "populations": [], "warnings": [],
        }

    def test_the_record_is_loaded_even_with_no_numbers_in_it(
            self, tmp_path: Path) -> None:
        record = self._record_not_applicable()
        assert load_reweighted(_project(tmp_path, record)) is not None

    def test_the_section_leads_with_what_they_are_not(
            self, tmp_path: Path) -> None:
        text = reweighted_section(_project(tmp_path,
                                           self._record_not_applicable()))
        assert "biased ensemble" in text
        assert "not an equilibrium ensemble" in text

    def test_no_reweighted_column_is_offered(self, tmp_path: Path) -> None:
        """A corrected column here would be the same numbers under a heading
        claiming they had been corrected."""
        text = reweighted_section(_project(tmp_path,
                                           self._record_not_applicable()))
        assert "Reweighted (equilibrium)" not in text
        assert "effective frames" not in text

    def test_no_per_analysis_line_claims_a_correction(self) -> None:
        assert reweighted_line(self._record_not_applicable(), "rmsd") is None

    def test_the_dashboard_labels_every_metric_biased(
            self, tmp_path: Path) -> None:
        from fastmdxplora.gui.report_dashboard import _metric_rows

        root = TestTheDashboardTableIsCorrectedToo._dashboard_project(
            tmp_path, self._record_not_applicable("umbrella"))
        rows = _metric_rows(root, {})
        named = [r.metric for r in rows if r.metric.startswith(("RMSD", "RMSF"))]
        assert all("biased ensemble" in n for n in named), named

    def test_no_effective_frame_row_is_shown(self, tmp_path: Path) -> None:
        """Nothing was reweighted, so there is no effective count."""
        from fastmdxplora.gui.report_dashboard import _metric_rows

        root = TestTheDashboardTableIsCorrectedToo._dashboard_project(
            tmp_path, self._record_not_applicable())
        assert not any("Effective frames" in r.metric for r in _metric_rows(root, {}))
