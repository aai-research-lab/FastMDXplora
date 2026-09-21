"""The umbrella result, drawn and recorded like every other analysis.

FastMDXplora's claim is an end-to-end study, and for an umbrella run the free
energy along the coordinate is the study. It was written to `pmf.json` and
stopped there: no figure, no entry in the analysis manifest, no mention in the
report. Sixteen analyses of the trajectory each produced a curve and a plot,
and the one result the run existed for did not.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.analysis.pmf import PMF


def _written(directory: Path, *, unsampled: int = 0) -> Path:
    """A `pmf.json` of the shape the umbrella phase writes."""
    coordinate = list(np.linspace(0.55, 0.95, 40))
    energy = [float(30.0 * (x - 0.73) ** 2 * 100) for x in coordinate]
    for index in range(unsampled):
        energy[10 + index] = None
    (directory / "pmf.json").write_text(json.dumps({
        "pmf": {"coordinate": coordinate, "free_energy_kjmol": energy},
        "overlaps": [
            {"between": [0, 1], "centres": [0.6, 0.7], "overlap": 0.43},
            {"between": [1, 2], "centres": [0.7, 0.8], "overlap": 0.54},
        ],
        "refused": None,
        "unsampled_bins": unsampled,
        "n_windows": 5,
    }), encoding="utf-8")
    return directory / "pmf.json"


class TestItReadsWhatTheStudyComputed:
    def test_it_finds_the_result_beside_the_run(self, tmp_path) -> None:
        """`pmf.json` sits beside the windows rather than inside any one of
        them, because it is the answer from all of them together."""
        _written(tmp_path)
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)

        assert len(result["coordinate"]) == 40
        assert np.isfinite(result["free_energy_kjmol"]).all()

    def test_it_does_not_recompute_the_stitching(self, tmp_path) -> None:
        """Stitching windows is delicate -- they have to overlap, and where
        they do not the gap is reported rather than bridged. Doing it twice
        would invite two answers to one question.

        So it reports exactly what the umbrella phase stored, with nothing
        beside it to recompute from: no window, no COLVAR."""
        stored = json.loads(_written(tmp_path).read_text(encoding="utf-8"))["pmf"]
        (tmp_path / "analysis").mkdir()
        result = PMF(output_dir=tmp_path / "analysis").compute(None)
        assert list(result["coordinate"]) == stored["coordinate"]
        assert list(result["free_energy_kjmol"]) == stored["free_energy_kjmol"]

    def test_a_run_without_an_umbrella_study_says_so(self, tmp_path) -> None:
        analysis = PMF(output_dir=tmp_path / "analysis")
        with pytest.raises(FileNotFoundError) as caught:
            analysis.compute(None)
        assert "umbrella" in str(caught.value)


class TestAnUnsampledBinStaysAGap:
    """`null` turned into zero would read as a minimum where there is no
    data -- the same defect the umbrella phase had when it wrote 1724 kJ/mol
    into bins nobody visited."""

    def test_it_is_not_a_zero(self, tmp_path) -> None:
        _written(tmp_path, unsampled=3)
        result = PMF(output_dir=tmp_path / "analysis").compute(None)
        energy = result["free_energy_kjmol"]

        assert np.isnan(energy).sum() == 3
        assert not (energy == 0).any()

    def test_the_table_says_nan_rather_than_nothing(self, tmp_path) -> None:
        """A blank field makes a row two columns in some places and one in
        others, and every reader of a whitespace table then disagrees."""
        _written(tmp_path, unsampled=2)
        analysis = PMF(output_dir=tmp_path / "analysis")
        result = analysis.compute(None)
        target = tmp_path / "pmf.dat"
        analysis.save_data(result, target)

        rows = [line.split() for line in
                target.read_text(encoding="utf-8").splitlines()
                if not line.startswith("#")]
        assert all(len(row) == 2 for row in rows)
        assert sum(1 for row in rows if row[1] == "nan") == 2


class TestItIsAnAnalysisLikeAnyOther:
    def test_it_is_registered(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import available_analyses

        assert "pmf" in available_analyses()

    def test_it_runs_only_where_a_study_produced_one(self, tmp_path) -> None:
        """An analysis that failed on every unbiased trajectory would turn a
        missing study into a failed phase -- so it is left out of an ordinary
        run's plan, and kept in every layout an umbrella study uses.

        The search once stopped two folders up, and an umbrella study's
        windows sit three below it, so every such study dropped the PMF in
        silence. The windowed layout below is that case."""
        from types import SimpleNamespace

        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        def planned(analysis_dir):
            analysis_dir.mkdir(parents=True, exist_ok=True)
            runner = SimpleNamespace(output_dir=analysis_dir, ligand_resname=None, traj=None)
            return AnalysisOrchestrator._build_plan(runner, None, None)

        ordinary = tmp_path / "ordinary" / "analysis"
        assert "pmf" not in planned(ordinary)

        flat = tmp_path / "flat"
        (flat).mkdir()
        _written(flat)
        assert "pmf" in planned(flat / "analysis")

        study = tmp_path / "study"
        study.mkdir()
        _written(study)
        assert "pmf" in planned(study / "runs" / "window-00" / "analysis")

    def test_it_does_not_take_a_selection(self) -> None:
        """The coordinate was chosen when the windows were planned; a
        selection here would describe a different question."""
        assert PMF.honours_selection is False

    def test_it_is_not_a_time_series(self) -> None:
        """The x axis is the collective variable, not time, so a mean over
        it would be a mean over positions rather than over a run."""
        assert PMF.time_series is False

    def test_it_draws_the_minimum_and_the_windows(self, tmp_path) -> None:
        # The minimum is marked where the curve is lowest among the bins
        # that were sampled, and every window's centre is drawn, so a
        # feature on a seam between two windows can be judged.
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        analysis = PMF(output_dir=tmp_path / "analysis")
        (tmp_path / "analysis").mkdir()
        _written(tmp_path, unsampled=3)
        result = analysis.compute(None)
        figure, ax = plt.subplots()
        try:
            analysis.plot(result, ax)
            verticals = sorted({round(float(line.get_xdata()[0]), 6) for line in ax.lines
                                if len(set(line.get_xdata())) == 1})
            energy = result["free_energy_kjmol"]
            lowest = float(result["coordinate"][np.nanargmin(energy)])
            assert round(lowest, 6) in verticals, "the minimum is not marked"
            assert f"minimum at {lowest:.3g}" in ax.get_legend().get_texts()[0].get_text()
            for centre in (0.6, 0.7, 0.8):
                assert round(centre, 6) in verticals, f"window centre {centre} not drawn"
            assert "3 bins unsampled" in ax.get_title()
        finally:
            plt.close(figure)

class TestTheStudyDrawsItsOwnFreeEnergy:
    """Putting the drawing in the per-run analysis phase left it undrawn.
    Each window analysed itself before the last one finished, so no window
    found a result to draw, and by the time `pmf.json` existed no analysis
    phase remained. The windows had each produced ten figures of their own
    restrained trajectory, and the one result the study existed for had
    none."""

    def test_the_study_draws_it_after_writing_it(self, tmp_path) -> None:
        # The study draws its own free energy, beside itself, from the
        # numbers it has just written.
        from types import SimpleNamespace

        from fastmdxplora.batch.explorer import BatchExplorer

        drawn = BatchExplorer._draw_pmf(SimpleNamespace(output_dir=tmp_path), _written(tmp_path))
        assert drawn is not None and Path(drawn).is_file()
        assert Path(drawn).is_relative_to(tmp_path / "free_energy")

    def test_drawing_cannot_fail_the_study(self, tmp_path, monkeypatch, caplog) -> None:
        """A study whose windows all succeeded must not fail at the last
        step because the figure could not be drawn -- and the numbers it
        already wrote must be left exactly as they were.

        Two ways the drawing can fail: inside the analysis, where its own
        run catches it, and before the analysis can start, where the study
        catches it and says the numbers are unaffected. Neither may break
        the study or touch the numbers."""
        import logging
        from types import SimpleNamespace

        from fastmdxplora.batch.explorer import BatchExplorer

        written = _written(tmp_path)
        before = written.read_bytes()
        study = SimpleNamespace(output_dir=tmp_path)

        def fails(*args, **kwargs):
            raise RuntimeError("the figure could not be made")

        for where in ("plot", "run"):
            monkeypatch.setattr(PMF, where, fails)
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                drawn = BatchExplorer._draw_pmf(study, written)
            monkeypatch.undo()
            assert drawn is None, where
            assert written.read_bytes() == before, where
            assert caplog.records, f"a failure in {where} went unreported"
        # And when the analysis cannot start, the study says why it matters.
        monkeypatch.setattr(PMF, "run", fails)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            BatchExplorer._draw_pmf(study, written)
        assert any("The numbers are unaffected" in r.getMessage() for r in caplog.records)

    def test_it_produces_the_same_three_files_as_any_analysis(
        self, tmp_path
    ) -> None:
        _written(tmp_path)
        analysis = PMF(output_dir=tmp_path / "free_energy")
        result = analysis.run(None)

        assert result.status == "ok"
        produced = {p.suffix for p in (tmp_path / "free_energy").rglob("*")
                    if p.is_file()}
        assert {".dat", ".png", ".svg"} <= produced

    def test_it_runs_without_a_trajectory(self, tmp_path) -> None:
        """The trajectory of any one window is not the study: it is a system
        held at one position by a spring."""
        _written(tmp_path)
        result = PMF(output_dir=tmp_path / "free_energy").run(None)
        assert result.status == "ok"


class TestTheGateFindsTheStudysOwnResult:
    """Whether `pmf` reaches the default plan, asked of a real layout.

    The test above this one reads the orchestrator's source and asserts
    that `_umbrella_ok` appears in it. That is satisfied by a gate which
    can never return True, and for a long time it was: the ladder of
    candidate directories stopped one level short of where a study with
    windows writes `pmf.json`, so every `requires_umbrella` analysis was
    dropped from the default plan of every umbrella study. `False` is
    also what an ordinary run returns, so nothing said so.

    It held for a flat single run -- the one layout an umbrella study
    never uses, since an umbrella study is windows by definition.
    """

    class _Orchestrator:
        """Enough of one to build a plan. `_build_plan` reads the output
        directory, the ligand and the trajectory, and guards the last."""

        ligand_resname = None
        traj = None

        def __init__(self, output_dir: Path) -> None:
            self.output_dir = output_dir

    @staticmethod
    def _plan(analysis_dir: Path) -> list[str]:
        import fastmdxplora.analysis  # noqa: F401  -- fills the registry
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        return AnalysisOrchestrator._build_plan(
            TestTheGateFindsTheStudysOwnResult._Orchestrator(analysis_dir),
            None, None)

    @staticmethod
    def _pmf_beside(root: Path) -> None:
        (root / "pmf.json").write_text(
            json.dumps({"coordinate": "distance", "free_energy": []}),
            encoding="utf-8")

    def test_a_study_with_windows_finds_it(self, tmp_path) -> None:
        """`<batch>/runs/<id>/analysis` looking for `<batch>/pmf.json` --
        three levels, where the ladder reached two. This is every real
        umbrella study."""
        analysis = tmp_path / "runs" / "window-000" / "analysis"
        analysis.mkdir(parents=True)
        self._pmf_beside(tmp_path)
        assert "pmf" in self._plan(analysis)

    def test_a_flat_run_still_finds_it(self, tmp_path) -> None:
        """One level up, and the case that always worked."""
        analysis = tmp_path / "analysis"
        analysis.mkdir()
        self._pmf_beside(tmp_path)
        assert "pmf" in self._plan(analysis)

    def test_an_ordinary_run_does_not(self, tmp_path) -> None:
        """There is nothing to draw for an unbiased trajectory, and an
        analysis that failed on every one of them would turn a missing
        study into a failed phase."""
        analysis = tmp_path / "analysis"
        analysis.mkdir()
        assert "pmf" not in self._plan(analysis)

    def test_the_gate_is_reachable_at_all(self, tmp_path) -> None:
        """The claim the source-text test cannot make: that some layout
        exists in which this returns True."""
        analysis = tmp_path / "runs" / "window-000" / "analysis"
        analysis.mkdir(parents=True)
        without = self._plan(analysis)
        self._pmf_beside(tmp_path)
        assert "pmf" not in without and "pmf" in self._plan(analysis), (
            "the gate must distinguish a study that produced a PMF "
            "from one that did not")
