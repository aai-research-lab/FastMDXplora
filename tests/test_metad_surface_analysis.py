"""The metadynamics surface, drawn and recorded like every other analysis.

Written to `metadynamics_surface.json` and stopped there: no figure, no entry
in the analysis manifest, no mention in the report. Ten analyses of the
trajectory each produced a curve and a plot, and the one result the run
existed to produce did not -- the same gap the umbrella study had.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.analysis.metad_surface import MetadynamicsSurface


def _record(run: Path, *, provisional=False, refused=None, empty=False) -> Path:
    (run / "simulation").mkdir(parents=True, exist_ok=True)
    grid = list(np.linspace(-np.pi, np.pi, 200))
    energy = [] if empty else [float(30.0 * (1 - np.cos(x))) for x in grid]
    (run / "simulation" / "metadynamics_surface.json").write_text(json.dumps({
        "refused": refused,
        "provisional": provisional,
        "evidence": {"drift_ceiling_kjmol": 20.0, "drift_kjmol": 2.3},
        "grid": [] if empty else grid,
        "free_energy_kjmol": energy,
    }), encoding="utf-8")
    return run


def _record_2d(run: Path) -> Path:
    (run / "simulation").mkdir(parents=True, exist_ok=True)
    first = np.linspace(-np.pi, np.pi, 8)
    second = np.linspace(-np.pi, np.pi, 6)
    energy = ((1.0 - np.cos(first[:, None]))
              + 2.0 * (1.0 - np.cos(second[None, :])))
    record = {
        "refused": None,
        "provisional": False,
        "evidence": {
            "per_dimension": [
                {"name": "cv1", "periodic": True},
                {"name": "cv2", "periodic": True},
            ],
        },
        "dimensions": 2,
        "grid": None,
        "axes": [first.tolist(), second.tolist()],
        "free_energy_kjmol": energy.tolist(),
    }
    (run / "simulation" / "metadynamics_surface.json").write_text(
        json.dumps(record), encoding="utf-8")
    return run


class TestItDrawsWhatTheRunComputed:
    def test_the_one_dimensional_result_keeps_its_existing_shape(
            self, tmp_path) -> None:
        _record(tmp_path)
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")

        result = analysis.compute(None)

        assert set(result) == {"coordinate", "free_energy_kjmol"}
        assert result["coordinate"].shape == result["free_energy_kjmol"].shape

    def test_a_settled_surface_is_drawn(self, tmp_path) -> None:
        _record(tmp_path)
        result = MetadynamicsSurface(output_dir=tmp_path / "analysis").run(None)

        assert result.status == "ok"
        produced = {p.suffix for p in (tmp_path / "analysis").rglob("*")
                    if p.is_file()}
        assert {".dat", ".png", ".svg"} <= produced

    def test_it_does_not_recompute_the_hills(self, tmp_path) -> None:
        """Summing them twice would invite two answers, and the trajectory
        of a metadynamics run is not a Boltzmann ensemble anyway.

        So it reports exactly the surface the simulation phase stored, with
        no HILLS file beside it to recompute from."""
        run = _record(tmp_path / "run")
        stored = json.loads((run / "simulation" / "metadynamics_surface.json")
                            .read_text(encoding="utf-8"))
        assert not list(run.rglob("HILLS*"))
        result = MetadynamicsSurface(output_dir=run / "analysis").compute(None)
        assert list(result["coordinate"]) == stored["grid"]
        assert list(result["free_energy_kjmol"]) == stored["free_energy_kjmol"]

    def test_a_run_without_metadynamics_says_so(self, tmp_path) -> None:
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")
        with pytest.raises(FileNotFoundError) as caught:
            analysis.compute(None)
        assert "metadynamics" in str(caught.value)


class TestATwoDimensionalSurfaceKeepsBothCoordinates:
    def test_grid_may_be_null_when_axes_describe_the_surface(
            self, tmp_path) -> None:
        _record_2d(tmp_path)
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")

        result = analysis.compute(None)

        assert result["dimensions"] == 2
        assert tuple(len(axis) for axis in result["axes"]) == (8, 6)
        assert result["free_energy_kjmol"].shape == (8, 6)

    def test_plotting_and_export_use_both_cv_axes(self, tmp_path) -> None:
        _record_2d(tmp_path)
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")

        completed = analysis.run(None)

        assert completed.status == "ok"
        assert completed.figure_path.is_file()
        lines = completed.data_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "# cv1 cv2 free_energy_kjmol"
        values = np.loadtxt(completed.data_path)
        assert values.shape == (8 * 6, 3)
        assert len(np.unique(values[:, 0])) == 8
        assert len(np.unique(values[:, 1])) == 6


class TestAProvisionalSurfaceIsStillDrawn:
    """A run whose bias has not settled still describes the landscape it has
    filled so far, and the refusal beside it explains what is missing.
    Withholding the picture as well leaves a reader with a sentence where
    they could have had both."""

    def test_it_is_drawn(self, tmp_path) -> None:
        _record(tmp_path, provisional=True,
                refused="the surface moved 8.8 kJ/mol")
        result = MetadynamicsSurface(output_dir=tmp_path / "analysis").run(None)
        assert result.status == "ok"

    def test_the_caveat_travels_with_the_numbers(self, tmp_path) -> None:
        """Not only in a terminal somebody has since closed."""
        _record(tmp_path, provisional=True, refused="not settled")
        MetadynamicsSurface(output_dir=tmp_path / "analysis").run(None)

        table = (tmp_path / "analysis" / "metad_surface"
                 / "metad_surface.dat").read_text(encoding="utf-8")
        assert "provisional" in table.splitlines()[1]

    def test_a_settled_one_carries_no_such_note(self, tmp_path) -> None:
        _record(tmp_path, provisional=False)
        MetadynamicsSurface(output_dir=tmp_path / "analysis").run(None)

        table = (tmp_path / "analysis" / "metad_surface"
                 / "metad_surface.dat").read_text(encoding="utf-8")
        assert "provisional" not in table

    def test_a_coordinate_that_never_moved_has_nothing_to_draw(
        self, tmp_path
    ) -> None:
        """The one refusal where there is genuinely no surface."""
        _record(tmp_path, empty=True,
                refused="Every hill was deposited at the same value.")
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")
        with pytest.raises(ValueError) as caught:
            analysis.compute(None)
        assert "no surface" in str(caught.value)


class TestItIsAnAnalysisLikeAnyOther:
    def test_it_is_registered(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import available_analyses

        assert "metad_surface" in available_analyses()

    def test_it_runs_only_where_a_run_produced_one(self, tmp_path) -> None:
        # Planned where a run left a surface -- beside a single run, and
        # beside each run of a set -- and left out of an ordinary run, where
        # it would only fail and turn a missing surface into a failed phase.
        from types import SimpleNamespace

        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        def planned(analysis_dir):
            analysis_dir.mkdir(parents=True, exist_ok=True)
            runner = SimpleNamespace(output_dir=analysis_dir, ligand_resname=None, traj=None)
            return AnalysisOrchestrator._build_plan(runner, None, None)

        assert MetadynamicsSurface.requires_metadynamics is True
        assert "metad_surface" not in planned(tmp_path / "ordinary" / "analysis")
        assert "metad_surface" in planned(_record(tmp_path / "single") / "analysis")
        one_of_a_set = _record(tmp_path / "study" / "runs" / "run-01")
        assert "metad_surface" in planned(one_of_a_set / "analysis")

    def test_it_marks_where_the_surface_is_trustworthy(self, tmp_path) -> None:
        """Above the drift ceiling the estimate moves by several kJ/mol
        however long the run, so the shape is read rather than the points.

        The line sits the ceiling above the surface's own floor, not at the
        ceiling's value: raised by 7 kJ/mol, the two differ, and a line at
        20 would say the wrong part of the curve can be trusted."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        record = _record(tmp_path / "run") / "simulation" / "metadynamics_surface.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["free_energy_kjmol"] = [e + 7.0 for e in data["free_energy_kjmol"]]
        record.write_text(json.dumps(data), encoding="utf-8")

        analysis = MetadynamicsSurface(output_dir=tmp_path / "run" / "analysis")
        result = analysis.compute(None)
        figure, ax = plt.subplots()
        try:
            analysis.plot(result, ax)
            flat = [line for line in ax.lines if len(set(line.get_ydata())) == 1
                    and len(line.get_ydata()) > 1]
            floor = float(np.nanmin(result["free_energy_kjmol"]))
            assert floor == pytest.approx(7.0, abs=0.01)
            assert [pytest.approx(floor + 20.0)] == [float(line.get_ydata()[0]) for line in flat]
            labels = [t.get_text() for t in ax.get_legend().get_texts()]
            assert "judged below 20 kJ/mol" in labels
        finally:
            plt.close(figure)

    def test_and_no_such_line_where_no_ceiling_was_judged(self, tmp_path) -> None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        record = _record(tmp_path / "run") / "simulation" / "metadynamics_surface.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["evidence"] = {}
        record.write_text(json.dumps(data), encoding="utf-8")
        analysis = MetadynamicsSurface(output_dir=tmp_path / "run" / "analysis")
        figure, ax = plt.subplots()
        try:
            analysis.plot(analysis.compute(None), ax)
            assert not [line for line in ax.lines if len(set(line.get_ydata())) == 1
                        and len(line.get_ydata()) > 1]
        finally:
            plt.close(figure)

class TestTheConvergenceBandTravelsWithTheSurface:
    """The simulation phase measures how much the surface still moved over
    the later part of its own deposition, point by point. It reaches a reader
    only if the figure draws it and the exported table carries it -- and only
    honestly if both say, in words, that it is not a standard error. A shaded
    band around a free energy is read as one otherwise, and this one is a
    lower bound on run-to-run spread rather than an estimate of it."""

    def _with_band(self, run: Path, *, points: int = 200) -> Path:
        _record(run)
        path = run / "simulation" / "metadynamics_surface.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["evidence"]["convergence"] = {
            "band_kjmol": list(np.linspace(0.1, 1.4, points)),
            "typical_kjmol": 0.7,
            "largest_kjmol": 1.4,
            "blocks": 5,
            "hills_at": [500, 625, 750, 875, 1000],
            "from_fraction": 0.5,
            "is_a_standard_error": False,
            "note": "A convergence indicator, not a standard error.",
        }
        path.write_text(json.dumps(record), encoding="utf-8")
        return run

    def test_the_exported_table_gains_a_labelled_column(self, tmp_path) -> None:
        self._with_band(tmp_path)
        completed = MetadynamicsSurface(
            output_dir=tmp_path / "analysis").run(None)

        text = completed.data_path.read_text(encoding="utf-8")
        assert "convergence_band_kjmol" in text.splitlines()[0]
        assert "NOT a standard error" in text
        assert "replicas" in text
        values = np.loadtxt(completed.data_path)
        assert values.shape == (200, 3)

    def test_a_record_without_one_keeps_the_two_column_table(
            self, tmp_path) -> None:
        """Every run written before the band existed, and every run too short
        to cut. An optional column has to be optional at both ends."""
        _record(tmp_path)
        completed = MetadynamicsSurface(
            output_dir=tmp_path / "analysis").run(None)

        lines = completed.data_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "# coordinate free_energy_kjmol"
        assert np.loadtxt(completed.data_path).shape == (200, 2)

    def test_a_band_of_the_wrong_length_is_ignored_rather_than_broadcast(
            self, tmp_path) -> None:
        """A record whose band does not match its grid is a record from a
        different run or a different grid, and numpy would happily broadcast
        a length-one array across the whole surface."""
        self._with_band(tmp_path, points=7)
        completed = MetadynamicsSurface(
            output_dir=tmp_path / "analysis").run(None)

        assert completed.status == "ok"
        assert np.loadtxt(completed.data_path).shape == (200, 2)

    def test_the_figure_legend_says_it_is_not_a_standard_error(
            self, tmp_path) -> None:
        import matplotlib.pyplot as plt

        self._with_band(tmp_path)
        analysis = MetadynamicsSurface(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        figure, ax = plt.subplots()
        try:
            analysis.plot(result, ax)
            labels = [text.get_text() for text in ax.get_legend().get_texts()]
        finally:
            plt.close(figure)

        assert any("not a standard error" in label for label in labels)
