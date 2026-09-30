"""Tests for the cross-run comparison report (``batch/compare.py``).

The sandbox can't run real MD/analysis, so these tests fabricate the
on-disk artifacts a completed batch would leave behind — per-run
``analysis/<name>/<name>.dat`` files plus a ``batch_manifest.json`` — and
exercise the comparison builder against them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.batch.compare import build_comparison_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_batch(
    tmp_path: Path,
    *,
    temps=(300, 310, 320),
    analyses=("rmsd", "rg"),
    statuses=None,
    n_frames=50,
) -> Path:
    """Fabricate a batch output dir with per-run analysis data + manifest."""
    root = tmp_path / "batch"
    runs = []
    statuses = statuses or {}
    rng = np.random.default_rng(0)
    for T in temps:
        rid = f"trpcage__temperature-K-{T}"
        rout = root / "runs" / rid
        status = statuses.get(T, "ok")
        if status == "ok":
            for analysis in analyses:
                d = rout / "analysis" / analysis
                d.mkdir(parents=True, exist_ok=True)
                base = 0.2 + 0.002 * (T - 300)
                series = base + 0.01 * rng.standard_normal(n_frames).cumsum() / 10
                np.savetxt(d / f"{analysis}.dat", series, fmt="%.8e")
        runs.append({
            "run_id": rid, "system": "prot.pdb", "status": status,
            "output_dir": str(rout),
            "sweep_values": {"setup.temperature_K": T},
            "phases": [], "message": "",
        })
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tool": "FastMDXplora", "n_runs": len(temps),
        "sweep": {"setup.temperature_K": list(temps)},
        "runs": runs,
    }
    (root / "batch_manifest.json").write_text(json.dumps(manifest, indent=2))
    return root


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------
class TestComparisonReport:
    def test_builds_full_report(self, tmp_path):
        root = _make_batch(tmp_path)
        cmp_dir = build_comparison_report(root)
        assert cmp_dir is not None
        names = {p.name for p in cmp_dir.iterdir()}
        # Overlays + trends for both analyses, plus CSV + markdown
        assert "overlay_rmsd.png" in names
        assert "overlay_rg.png" in names
        assert "trend_rmsd.png" in names
        assert "trend_rg.png" in names
        assert "comparison_summary.csv" in names
        assert "comparison_report.md" in names

    def test_summary_csv_one_row_per_run(self, tmp_path):
        root = _make_batch(tmp_path)
        cmp_dir = build_comparison_report(root)
        with (cmp_dir / "comparison_summary.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 3
        # The swept axis column is present and numeric scalars are filled
        assert "temperature_K" in rows[0]
        assert rows[0]["rmsd_mean"]  # non-empty
        assert {r["temperature_K"] for r in rows} == {"300", "310", "320"}

    def test_markdown_mentions_analyses_and_trend(self, tmp_path):
        root = _make_batch(tmp_path)
        cmp_dir = build_comparison_report(root)
        md = (cmp_dir / "comparison_report.md").read_text(encoding="utf-8")
        assert "Cross-run comparison report" in md
        assert "3 successful runs" in md
        assert "RMSD" in md
        assert "temperature_K" in md
        # A quantitative takeaway sentence is present, with no error to
        # judge the change by in this fixture's runs
        assert "From `temperature_K` 300 to 320" in md
        # Figures are referenced by relative name
        assert "overlay_rmsd.png" in md
        assert "trend_rmsd.png" in md

    def test_trend_uses_first_sweep_axis(self, tmp_path):
        root = _make_batch(tmp_path)
        cmp_dir = build_comparison_report(root)
        # Trend figure exists and is a non-trivial PNG
        trend = cmp_dir / "trend_rmsd.png"
        assert trend.is_file() and trend.stat().st_size > 1000


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------
class TestComparisonDegradation:
    def test_no_manifest_returns_none(self, tmp_path):
        assert build_comparison_report(tmp_path / "nonexistent") is None

    def test_fewer_than_two_ok_runs_returns_none(self, tmp_path):
        # Only one successful run -> nothing to compare
        root = _make_batch(tmp_path, temps=(300,))
        assert build_comparison_report(root) is None

    def test_errored_runs_excluded(self, tmp_path):
        # Three runs but one errored; the other two still compare
        root = _make_batch(tmp_path, statuses={320: "error"})
        cmp_dir = build_comparison_report(root)
        assert cmp_dir is not None
        with (cmp_dir / "comparison_summary.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        # Only the 2 ok runs appear
        assert {r["temperature_K"] for r in rows} == {"300", "310"}

    def test_missing_analysis_data_skipped(self, tmp_path):
        # Runs exist and are ok, but have no analysis .dat files
        root = tmp_path / "batch"
        runs = []
        for T in (300, 310):
            rid = f"r-{T}"
            (root / "runs" / rid).mkdir(parents=True, exist_ok=True)
            runs.append({
                "run_id": rid, "system": "p.pdb", "status": "ok",
                "output_dir": str(root / "runs" / rid),
                "sweep_values": {"setup.temperature_K": T},
                "phases": [], "message": "",
            })
        (root / "batch_manifest.json").write_text(json.dumps({
            "sweep": {"setup.temperature_K": [300, 310]}, "runs": runs,
        }))
        # No analysis data anywhere -> nothing comparable -> None
        assert build_comparison_report(root) is None

    def test_no_sweep_still_overlays(self, tmp_path):
        """A multi-system batch with no sweep: overlays yes, trends no."""
        root = tmp_path / "batch"
        runs = []
        rng = np.random.default_rng(1)
        for sysname in ("wt", "mutant"):
            rid = sysname
            d = root / "runs" / rid / "analysis" / "rmsd"
            d.mkdir(parents=True, exist_ok=True)
            np.savetxt(d / "rmsd.dat", 0.2 + 0.01 * rng.standard_normal(40).cumsum() / 10)
            runs.append({
                "run_id": rid, "system": f"{sysname}.pdb", "status": "ok",
                "output_dir": str(root / "runs" / rid),
                "sweep_values": {}, "phases": [], "message": "",
            })
        (root / "batch_manifest.json").write_text(json.dumps({
            "sweep": {}, "runs": runs,
        }))
        cmp_dir = build_comparison_report(root)
        assert cmp_dir is not None
        names = {p.name for p in cmp_dir.iterdir()}
        # Overlay exists; no trend (no numeric sweep axis)
        assert "overlay_rmsd.png" in names
        assert "trend_rmsd.png" not in names


# ---------------------------------------------------------------------------
# Auto-trigger through BatchExplorer
# ---------------------------------------------------------------------------
class TestComparisonAutoTrigger:
    def test_batch_builds_comparison_when_data_present(self, tmp_path, monkeypatch):
        """A multi-run batch auto-builds the comparison when analysis data exists.

        We stub the per-run execution so the run dirs get fake analysis
        .dat files (the sandbox can't run real MD), then confirm
        BatchExplorer's post-run hook produces the comparison report.
        """
        import numpy as np
        from fastmdxplora.batch import explorer as exp_mod
        from fastmdxplora.orchestrator import RunResult

        def fake_execute_run(spec_dict, run_out, include, exclude, verbose,
                             device, quiet=True, force=False):
            out = Path(run_out)
            T = spec_dict["sweep_values"]["setup.temperature_K"]
            for analysis in ("rmsd", "rg"):
                d = out / "analysis" / analysis
                d.mkdir(parents=True, exist_ok=True)
                base = 0.2 + 0.002 * (T - 300)
                np.savetxt(d / f"{analysis}.dat",
                           base + 0.01 * np.arange(30) / 30)
            return RunResult(
                run_id=spec_dict["run_id"], system=spec_dict["system"],
                status="ok", output_dir=out,
                sweep_values=spec_dict["sweep_values"], phases=[],
            )

        monkeypatch.setattr(exp_mod, "_execute_run", fake_execute_run)

        cfg = tmp_path / "c.yml"
        cfg.write_text(f"""
output: {tmp_path / 'b'}
include: [setup, analysis]
systems:
  - {{id: a, system: prot.pdb}}
sweep:
  setup.temperature_K: [300, 310, 320]
""")
        from fastmdxplora import FastMDXplora
        FastMDXplora(config=str(cfg)).explore()

        cmp_dir = tmp_path / "b" / "comparison"
        assert cmp_dir.is_dir()
        assert (cmp_dir / "comparison_report.md").is_file()
        assert (cmp_dir / "overlay_rmsd.png").is_file()
        assert (cmp_dir / "trend_rmsd.png").is_file()

    def test_comparison_can_be_disabled(self, tmp_path, monkeypatch):
        import numpy as np
        from fastmdxplora.batch import explorer as exp_mod
        from fastmdxplora.orchestrator import RunResult

        def fake_execute_run(spec_dict, run_out, include, exclude, verbose,
                             device, quiet=True, force=False):
            out = Path(run_out)
            d = out / "analysis" / "rmsd"
            d.mkdir(parents=True, exist_ok=True)
            np.savetxt(d / "rmsd.dat", np.arange(30) / 100)
            return RunResult(
                run_id=spec_dict["run_id"], system=spec_dict["system"],
                status="ok", output_dir=out,
                sweep_values=spec_dict["sweep_values"], phases=[],
            )

        monkeypatch.setattr(exp_mod, "_execute_run", fake_execute_run)

        cfg = tmp_path / "c.yml"
        cfg.write_text(f"""
output: {tmp_path / 'b'}
include: [setup, analysis]
systems:
  - {{id: a, system: prot.pdb}}
sweep:
  setup.temperature_K: [300, 310]
report:
  comparison: false
""")
        from fastmdxplora import FastMDXplora
        FastMDXplora(config=str(cfg)).explore()
        # comparison disabled -> no comparison dir
        assert not (tmp_path / "b" / "comparison").exists()

    def test_compare_method_rebuilds(self, tmp_path):
        """fmdx.compare(output_dir=...) rebuilds the report standalone."""
        root = _make_batch(tmp_path)
        from fastmdxplora import FastMDXplora
        fmdx = FastMDXplora(config_data={
            "output": str(root),
            "systems": [{"id": "r", "system": "p.pdb"}],
            "sweep": {"setup.temperature_K": [300, 310, 320]},
        })
        cmp_dir = fmdx.compare(output_dir=root)
        assert cmp_dir is not None
        assert (cmp_dir / "comparison_report.md").is_file()

    def test_compare_without_dir_needs_explore_first(self, tmp_path):
        """compare() with no output_dir and no prior run errors clearly."""
        from fastmdxplora import FastMDXplora
        fmdx = FastMDXplora(config_data={
            "systems": [{"id": "r", "system": "p.pdb"}],
            "sweep": {"setup.temperature_K": [300, 310]},
        })
        # config-driven object hasn't run yet -> no output_dir known
        with pytest.raises(ValueError, match="output directory"):
            fmdx.compare()


class TestAnUmbrellaStudyIsOneExperiment:
    """Five windows are one experiment sampled in pieces, not five
    experiments. Each is held at a different position by a spring, so a
    table of their mean RMSD and radius of gyration is a table of the
    restraint schedule -- on a stretched tripeptide the radius rose
    monotonically with window index, 0.334 to 0.369, and the report
    presented that as five runs disagreeing."""

    def _study(self, tmp_path, *, with_free_energy: bool, refused=None):
        import json

        (tmp_path / "comparison").mkdir()
        if with_free_energy or refused:
            (tmp_path / "pmf.json").write_text(
                json.dumps({"pmf": {}, "refused": refused}), encoding="utf-8")
        if with_free_energy:
            drawn = tmp_path / "free_energy" / "pmf"
            drawn.mkdir(parents=True)
            (drawn / "pmf.png").write_bytes(b"")
        return tmp_path / "comparison" / "comparison_report.md"

    def test_the_free_energy_is_found_beside_the_comparison(
        self, tmp_path
    ) -> None:
        from fastmdxplora.batch.compare import _umbrella_result

        md = self._study(tmp_path, with_free_energy=True)
        drawn, refused = _umbrella_result(md)
        assert refused is None
        assert drawn is not None and drawn.name == "pmf.png"

    def test_an_ordinary_sweep_gets_no_preamble(self, tmp_path) -> None:
        """Several systems compared against each other really are several
        experiments, and the overlays are the point there."""
        from fastmdxplora.batch.compare import _umbrella_result

        md = self._study(tmp_path, with_free_energy=False)
        assert _umbrella_result(md) is None

    def test_the_result_comes_before_the_comparison(self, tmp_path) -> None:
        """A reader who takes the table at face value has been misled by the
        time they reach any caveat below it."""
        from fastmdxplora.batch.compare import (
            _umbrella_result,
            _umbrella_preamble,
        )

        md = self._study(tmp_path, with_free_energy=True)
        preamble = _umbrella_preamble(*_umbrella_result(md))
        text = "\n".join(preamble)

        assert text.index("Free energy") < text.index("overlays")
        assert "one experiment sampled in pieces" in text

    def test_the_link_resolves_from_the_comparison_directory(
        self, tmp_path
    ) -> None:
        from fastmdxplora.batch.compare import (
            _umbrella_result,
            _umbrella_preamble,
        )

        md = self._study(tmp_path, with_free_energy=True)
        preamble = "\n".join(_umbrella_preamble(*_umbrella_result(md)))
        assert "(../free_energy/pmf/pmf.png)" in preamble

    def test_it_says_why_the_windows_differ(self, tmp_path) -> None:
        """Not a disagreement between runs: a quantity that varies with the
        coordinate varies across windows by construction."""
        from fastmdxplora.batch.compare import (
            _umbrella_result,
            _umbrella_preamble,
        )

        md = self._study(tmp_path, with_free_energy=True)
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md)))
        assert "restraints differ" in text
        assert "not a property of the system" in text


    def test_a_refused_study_shows_no_plot(self, tmp_path) -> None:
        """A stale figure is worse than none: it is the previous study's
        answer, presented as this one. `--force` clears the run directories
        and not the drawing, so a study that refused where the last one
        succeeded kept a plot from a different set of windows -- and the
        report linked to it."""
        from fastmdxplora.batch.compare import (
            _umbrella_preamble,
            _umbrella_result,
        )

        md = self._study(
            tmp_path, with_free_energy=True,
            refused="Adjacent windows do not overlap: windows 1 and 2.")
        drawn, refused = _umbrella_result(md)

        assert drawn is None, "a refused study must not offer a figure"
        assert refused
        text = "\n".join(_umbrella_preamble(drawn, refused))
        assert "pmf.png" not in text
        assert "No free energy was computed" in text

    def test_it_adds_no_remedy_of_its_own(self, tmp_path) -> None:
        """A generic one here contradicted the refusal it followed. The
        refusal said four of five windows had slid off their restraints, so
        a stiffer spring was wanted; the paragraph beneath it advised a
        softer one, which makes that failure worse. Whatever computed the
        failure knows which failure it was."""
        from fastmdxplora.batch.compare import (
            _umbrella_preamble,
            _umbrella_result,
        )

        diagnosis = (
            "Adjacent windows do not overlap. Windows slid off their "
            "restraints; hold them harder with a larger force_constant.")
        md = self._study(tmp_path, with_free_energy=False, refused=diagnosis)
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md)))

        assert diagnosis in text
        assert "soften the restraint" not in text
        assert "Move them closer" not in text

    def test_the_refusal_is_carried_whole(self, tmp_path) -> None:
        """Including the part that names which windows and by how much."""
        from fastmdxplora.batch.compare import (
            _umbrella_preamble,
            _umbrella_result,
        )

        diagnosis = ("windows 1 and 2 (at 0.7 and 0.8) share 1.9%. "
                     "Recombination stitches histograms together.")
        md = self._study(tmp_path, with_free_energy=False, refused=diagnosis)
        text = "\n".join(_umbrella_preamble(*_umbrella_result(md)))
        assert "1.9%" in text and "windows 1 and 2" in text

    def test_the_drawing_is_cleared_before_a_study_draws(self, tmp_path) -> None:
        """Rebuilt over an earlier study, the figure is the new one; and a
        rebuild that is refused leaves no figure at all, rather than the last
        successful one sitting beside a refusal as though it still held.
        Cleared before drawing, since cleared after it would take the new
        figure with it."""
        earlier = b"the earlier study's figure"

        figure, record = _rebuilt_over(tmp_path / "drawn", earlier)
        assert record["refused"] is None
        assert figure.is_file() and figure.read_bytes() != earlier

        figure, record = _rebuilt_over(tmp_path / "refused", earlier, a_window_missing=True)
        assert record["refused"]
        assert not figure.exists(), "the earlier figure outlived a refused rebuild"


def _rebuilt_over(where, earlier: bytes, *, a_window_missing: bool = False):
    """Recombine a real umbrella layout over a figure an earlier study left,
    with every window's samples or with the first window's missing. Returns
    the figure's path and what the study recorded."""
    import json

    import tests.test_umbrella as umbrella

    where.mkdir(parents=True)
    explorer = umbrella.TestItLooksWhereTheRunsActuallyWent()._explorer(where)
    for spec in explorer.run_specs:
        block = spec.options["simulation"]["umbrella"]
        if a_window_missing and block["index"] == 0:
            continue
        simulation = explorer._run_output_dir(spec) / "simulation"
        simulation.mkdir(parents=True)
        values = umbrella._sample(block["centre"], block["force_constant"],
                                  n=3000, seed=block["index"])
        simulation.joinpath("COLVAR").write_text(
            "#! FIELDS time cv bias\n"
            + "\n".join(f"{i * 0.1:.1f} {v:.6f} 0.0" for i, v in enumerate(values)),
            encoding="utf-8")
    figure = where / "out" / "free_energy" / "pmf" / "pmf.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(earlier)
    explorer._maybe_build_pmf(bootstrap_resamples=8)
    return figure, json.loads((where / "out" / "pmf.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The means compared are the ones the runs recorded
# ---------------------------------------------------------------------------
def _record_findings(root: Path, *, frames_short: str | None = None) -> None:
    """Each run's findings, as its analyses record them: `summarise` over
    the series each wrote."""
    from fastmdxplora.statistics import summarise

    for data in root.glob("runs/*/analysis/*/*.dat"):
        series = np.loadtxt(data)
        equilibrated, reason = summarise(series)
        record = dict(equilibrated.as_record()) if equilibrated is not None else {}
        if reason is not None:
            record["not_a_measurement"] = reason
        record["n_frames"] = int(series.size)
        (data.parent / "options.json").write_text(json.dumps(
            {"analysis": data.parent.name, "findings": {"mean": record}}), encoding="utf-8")


def _batch_of(tmp_path: Path, means: dict, *, axis="setup.temperature_K", noise=0.001,
              n_frames=400) -> Path:
    """Runs whose series settle on the given means after an equilibration."""
    root = tmp_path / "batch"
    runs = []
    rng = np.random.default_rng(3)
    for value, mean in means.items():
        rid = f"r-{value}"
        folder = root / "runs" / rid / "analysis" / "rmsd"
        folder.mkdir(parents=True, exist_ok=True)
        approach = np.linspace(0.05, mean, 40)
        series = np.concatenate([approach, mean + noise * rng.standard_normal(n_frames)])
        np.savetxt(folder / "rmsd.dat", series, fmt="%.8e")
        runs.append({"run_id": rid, "system": "p.pdb", "status": "ok",
                     "output_dir": str(root / "runs" / rid),
                     "sweep_values": {axis: value}, "phases": [], "message": ""})
    (root / "batch_manifest.json").write_text(json.dumps(
        {"sweep": {axis: list(means)}, "runs": runs}), encoding="utf-8")
    _record_findings(root)
    return root


class TestTheMeansAreTheRecordedOnes:

    def test_the_table_gives_each_mean_after_equilibration_with_its_error(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.20, 350: 0.30})
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "0.2 ±" in md and "0.3 ±" in md
        assert "(all frames)" not in md
        assert "after equilibration, with its standard" in md

    def test_the_csv_carries_the_error_and_what_was_discarded(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.20, 350: 0.30})
        with (build_comparison_report(root) / "comparison_summary.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        assert float(rows[0]["rmsd_mean"]) == pytest.approx(0.20, abs=1e-3)
        assert float(rows[0]["rmsd_standard_error"]) > 0
        assert int(rows[0]["rmsd_frames_discarded"]) >= 20
        assert rows[0]["rmsd_over"] == "after equilibration"

    def test_a_change_larger_than_its_error_is_a_trend(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.20, 350: 0.30})
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "increases from" in md and "more than twice its error" in md

    def test_a_change_within_its_error_is_not(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.2000, 350: 0.2001}, noise=0.02)
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "do not tell the two ends apart" in md
        assert "increases" not in md and "decreases" not in md

    def test_without_findings_it_says_what_it_used(self, tmp_path):
        root = _make_batch(tmp_path)
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "(all frames)" in md and "no standard error was recorded" in md

    def test_a_mean_that_is_not_a_measurement_is_marked(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.20, 350: 0.30})
        record = root / "runs" / "r-300" / "analysis" / "rmsd" / "options.json"
        data = json.loads(record.read_text())
        data["findings"]["mean"]["not_a_measurement"] = "too few independent samples"
        record.write_text(json.dumps(data))
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "\\*" in md and "Not determined" in md


class TestReplicas:

    def _replicas(self, tmp_path, spread):
        means = {seed: 0.2 + spread * k for k, seed in enumerate((1, 2, 3, 4))}
        return _batch_of(tmp_path, means, axis="simulation.random_seed")

    def test_there_is_no_trend_against_a_seed(self, tmp_path):
        cmp_dir = build_comparison_report(self._replicas(tmp_path, 0.0))
        assert not list(cmp_dir.glob("trend_*.png"))

    def test_their_spread_is_set_against_the_error_each_estimated(self, tmp_path):
        md = (build_comparison_report(self._replicas(tmp_path, 0.0))
              / "comparison_report.md").read_text()
        assert "## Across the replicas" in md
        assert "differ only by random seed" in md
        assert "is supported by repeating it" in md

    def test_a_spread_wider_than_the_errors_says_they_were_too_tight(self, tmp_path):
        md = (build_comparison_report(self._replicas(tmp_path, 0.05))
              / "comparison_report.md").read_text()
        assert "too tight" in md

    def test_variants_get_no_replica_section(self, tmp_path):
        root = _batch_of(tmp_path, {300: 0.20, 350: 0.30})
        md = (build_comparison_report(root) / "comparison_report.md").read_text()
        assert "Across the replicas" not in md


class TestTheOverlaysClock:

    def _manifest(self, root, rid, **load):
        (root / "runs" / rid / "analysis" / "analysis_manifest.json").write_text(
            json.dumps({"load_kwargs": load}), encoding="utf-8")

    def test_runs_saved_at_different_intervals_line_up_in_time(self, tmp_path):
        from fastmdxplora.batch.compare import _times

        root = _batch_of(tmp_path, {300: 0.2, 350: 0.3})
        self._manifest(root, "r-300", saving_interval_ps=10.0)
        self._manifest(root, "r-350", saving_interval_ps=20.0, stride=2, first=1)
        assert _times(root / "runs" / "r-300", 3).tolist() == pytest.approx([0.01, 0.02, 0.03])
        # Strided frames 1, 2, 3 of the file's 0, 2, 4, 6: frames 2, 4, 6.
        assert _times(root / "runs" / "r-350", 3).tolist() == pytest.approx([0.06, 0.1, 0.14])
        assert build_comparison_report(root) is not None

    def test_without_a_clock_it_is_the_frame(self, tmp_path):
        from fastmdxplora.batch.compare import _times

        root = _batch_of(tmp_path, {300: 0.2, 350: 0.3})
        assert _times(root / "runs" / "r-300", 3) is None
        self._manifest(root, "r-300", saving_interval_ps=0)
        assert _times(root / "runs" / "r-300", 3) is None
