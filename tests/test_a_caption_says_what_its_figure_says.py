"""A card's caption and the dashboard's table give the number its figure gives.

Each analysis records the frames it kept after equilibration and what its
mean is worth (`findings.mean` in its options.json), and its figure draws
that mean with its error. The caption under the figure was the mean of every
row of the data file, equilibration included, and the dashboard's table the
same: an RMSD card read "avg 0.0157" beneath "mean after equilibration
0.01297 +/- 0.0021 nm", and a series too short to measure anything had a
caption and a row that read as measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmdxplora.gui.report_dashboard import _dashboard_summaries, _metric_rows

#: Twenty frames of equilibration at 0.5 nm, then a plateau at 0.2 nm.
RMSD = [0.5] * 20 + [0.2] * 80


def _analysis(root: Path, name: str, values: list[float], mean: dict | None) -> None:
    directory = root / "analysis" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.dat").write_text(
        "".join(f"{i} {v}\n" for i, v in enumerate(values)), encoding="utf-8")
    record = {"analysis": name, "options": {}, "findings": {} if mean is None else {"mean": mean}}
    (directory / "options.json").write_text(json.dumps(record), encoding="utf-8")


def _measured(**overrides) -> dict:
    record = {"discard": 20, "effective_samples": 21.2, "mean": 0.012965,
              "standard_error": 0.0020728, "standard_deviation": 0.0095, "n_frames": 100}
    record.update(overrides)
    return record


def _caption(root: Path, title: str) -> str:
    return _dashboard_summaries(root)[title].summary


def _row(root: Path, start: str):
    return next(r for r in _metric_rows(root, {}) if r.metric.startswith(start))


class TestTheCaption:

    def test_the_mean_after_equilibration_with_its_error(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, _measured())
        assert _caption(tmp_path, "RMSD") == (
            "mean 0.0130 ± 0.0021 nm after equilibration, 21 independent samples")

    def test_nothing_discarded_is_not_said_to_be(self, tmp_path) -> None:
        _analysis(tmp_path, "rg", RMSD, _measured(discard=0, mean=1.4231,
                                                  standard_error=0.012))
        assert _caption(tmp_path, "Radius of gyration") == (
            "mean 1.423 ± 0.012 nm, 21 independent samples")

    def test_too_few_independent_samples(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, _measured(effective_samples=4.2))
        assert _caption(tmp_path, "RMSD").endswith("4 independent samples, too few to measure")

    def test_a_series_too_short_to_measure(self, tmp_path) -> None:
        _analysis(tmp_path, "rg", RMSD, _measured(
            standard_error=float("nan"),
            not_a_measurement="This run is not long against its own correlation time"))
        assert _caption(tmp_path, "Radius of gyration") == (
            "mean 0.0130 nm, too short to be a measurement")

    def test_one_with_no_mean_at_all(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, {"not_a_measurement": "too short", "n_frames": 3})
        assert _caption(tmp_path, "RMSD") == "no mean: too short to measure"

    def test_an_older_study_says_what_its_number_is(self, tmp_path) -> None:
        """No findings recorded: the plain mean, called what it is."""
        _analysis(tmp_path, "rmsd", RMSD, None)
        assert _caption(tmp_path, "RMSD") == "mean over all frames 0.2600 nm"

    def test_a_profile_is_a_mean_over_residues(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsf", [0.1, 0.2, 0.3], None)
        assert _caption(tmp_path, "RMSF") == "mean over residues 0.2000 nm"

    def test_a_biased_run_gives_the_reweighted_mean_or_none(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, _measured())
        _analysis(tmp_path, "rg", RMSD, _measured())
        (tmp_path / "analysis" / "reweighted").mkdir()
        (tmp_path / "analysis" / "reweighted" / "reweighted_averages.json").write_text(json.dumps({
            "n_frames": 100, "effective_sample_size": 40.0, "settled": True,
            "quantities": [{"analysis": "rmsd", "reweighted_mean": 0.2813,
                            "reweighted_std": 0.018}]}), encoding="utf-8")
        assert _caption(tmp_path, "RMSD") == "reweighted mean 0.2813 nm"
        assert _caption(tmp_path, "Radius of gyration") == "biased ensemble: no unbiased mean"

    def test_a_biased_run_still_counts_its_clusters(self, tmp_path) -> None:
        """A population is not a mean the bias skews: it keeps its caption."""
        (tmp_path / "analysis" / "cluster").mkdir(parents=True)
        (tmp_path / "analysis" / "cluster" / "cluster_kmeans.dat").write_text(
            "".join(f"{i} {i % 3}\n" for i in range(12)), encoding="utf-8")
        (tmp_path / "analysis" / "reweighted").mkdir()
        (tmp_path / "analysis" / "reweighted" / "reweighted_averages.json").write_text(
            json.dumps({"n_frames": 12, "quantities": [],
                        "populations": [{"method": "kmeans"}]}), encoding="utf-8")
        assert "3 clusters" in _caption(tmp_path, "KMeans trajectory scatter")


class TestTheTable:

    def test_the_frames_the_analysis_kept(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, _measured())
        row = _row(tmp_path, "RMSD")
        assert (row.metric, row.average, row.stddev) == (
            "RMSD (after equilibration)", "0.0130", "0.0095")

    def test_a_series_too_short_says_so(self, tmp_path) -> None:
        _analysis(tmp_path, "rg", RMSD, _measured(not_a_measurement="too short"))
        row = _row(tmp_path, "Radius of gyration")
        assert row.metric == "Radius of gyration (all frames, too short to measure)"
        assert row.average == "0.2600"

    def test_an_older_study_is_as_it_was(self, tmp_path) -> None:
        _analysis(tmp_path, "rmsd", RMSD, None)
        row = _row(tmp_path, "RMSD")
        assert (row.metric, row.average) == ("RMSD", "0.2600")


def test_the_gui_card_carries_the_same_caption(tmp_path) -> None:
    """What the Analysis page is sent, beside the figure it captions."""
    from fastmdxplora.gui.server import _results_payload

    _analysis(tmp_path, "rmsd", RMSD, _measured())
    (tmp_path / "analysis" / "rmsd" / "rmsd.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    payload = _results_payload(tmp_path)
    panels = [p for s in payload["analysis_sections"] for p in s["panels"]]
    rmsd = next(p for p in panels if p["title"] == "RMSD")
    assert rmsd["summary"].startswith("mean 0.0130 ± 0.0021 nm after equilibration")
