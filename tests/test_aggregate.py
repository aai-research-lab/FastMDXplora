"""One table from a campaign, and the difference between error and result."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fastmdxplora.batch.aggregate import (
    CALIBRATION_FACTOR,
    aggregate_members,
    read_member_findings,
)


def _member(root: Path, run_id: str, mean: float, se: float | None,
            *, system: str = "a.pdb", sweep: dict | None = None,
            qualified: str | None = None) -> dict:
    out = root / "runs" / run_id
    (out / "analysis" / "rmsd").mkdir(parents=True, exist_ok=True)
    record = {"mean": {"mean": mean}}
    if se is not None:
        record["mean"]["standard_error"] = se
        record["mean"]["effective_samples"] = 120.0
    if qualified:
        record["mean"]["not_a_measurement"] = qualified
    (out / "analysis" / "rmsd" / "options.json").write_text(
        json.dumps({"analysis": "rmsd", "findings": record}),
        encoding="utf-8")
    return {"run_id": run_id, "output_dir": str(out), "status": "ok",
            "system": system, "sweep_values": sweep or {}}


def _campaign(tmp_path: Path, runs: list[dict], sweep: dict) -> Path:
    (tmp_path / "batch_manifest.json").write_text(
        json.dumps({"runs": runs, "sweep": sweep}), encoding="utf-8")
    return tmp_path


class TestTellingRepeatsFromComparisons:
    """The two look identical on disk and mean opposite things."""

    def test_a_seed_sweep_is_replicas(self, tmp_path):
        runs = [_member(tmp_path, f"r{i}", 0.30 + 0.001 * i, 0.001,
                        sweep={"simulation.random_seed": 100 + i})
                for i in range(4)]
        result = aggregate_members(
            _campaign(tmp_path, runs, {"simulation.random_seed": []}))

        assert result["replicas"] is True
        assert "repeats of one estimate" in result["why"]
        assert result["analyses"]["rmsd"]["spread_is"] == (
            "the error on one estimate")

    def test_several_systems_are_not_replicas(self, tmp_path):
        """Averaging a mutant series would report its biology as noise."""
        runs = [
            _member(tmp_path, "wt", 0.30, 0.001, system="wt.pdb"),
            _member(tmp_path, "l99a", 0.42, 0.001, system="l99a.pdb"),
            _member(tmp_path, "m102q", 0.38, 0.001, system="m102q.pdb"),
        ]
        result = aggregate_members(_campaign(tmp_path, runs, {}))

        assert result["replicas"] is False
        assert "different systems" in result["why"]
        assert result["analyses"]["rmsd"]["spread_is"] == (
            "the difference between the studies compared")
        assert "calibration" not in result["analyses"]["rmsd"]

    def test_a_parameter_sweep_is_not_replicas(self, tmp_path):
        runs = [_member(tmp_path, f"t{i}", 0.30 + 0.02 * i, 0.001,
                        sweep={"simulation.temperature_K": 290 + 10 * i})
                for i in range(3)]
        result = aggregate_members(
            _campaign(tmp_path, runs, {"simulation.temperature_K": []}))

        assert result["replicas"] is False
        assert "temperature_K" in result["why"]

    def test_an_axis_merely_named_like_a_seed_does_not_count(self, tmp_path):
        """Matching on the word would make a seeded-anything a replica."""
        runs = [_member(tmp_path, f"s{i}", 0.30, 0.001,
                        sweep={"setup.seed_structure": i}) for i in range(3)]
        result = aggregate_members(
            _campaign(tmp_path, runs, {"setup.seed_structure": []}))
        assert result["replicas"] is False


class TestTheCalibrationComparison:
    """Ten runs give a standard error a second way, and the two should agree."""

    @staticmethod
    def _seed_runs(tmp_path, means, se):
        return [_member(tmp_path, f"r{i}", m, se,
                        sweep={"simulation.random_seed": 100 + i})
                for i, m in enumerate(means)]

    def test_a_calibrated_estimate_is_confirmed(self, tmp_path):
        rng = np.random.RandomState(0)
        se = 0.004
        means = 0.30 + rng.normal(scale=se, size=10)
        result = aggregate_members(_campaign(
            tmp_path, self._seed_runs(tmp_path, means, se),
            {"simulation.random_seed": []}))
        entry = result["analyses"]["rmsd"]

        assert entry["ratio_observed_to_predicted"] < CALIBRATION_FACTOR
        assert "supported by repeating it" in entry["calibration"]

    def test_an_optimistic_estimate_is_named(self, tmp_path):
        """The one-sided failure a single run cannot see.

        A trajectory too short to contain its own correlation time reports
        an error bar too tight, and nothing in that run says so. Ten runs
        do.
        """
        rng = np.random.RandomState(1)
        means = 0.30 + rng.normal(scale=0.02, size=10)
        result = aggregate_members(_campaign(
            tmp_path, self._seed_runs(tmp_path, means, 0.001),
            {"simulation.random_seed": []}))
        entry = result["analyses"]["rmsd"]

        assert entry["ratio_observed_to_predicted"] > CALIBRATION_FACTOR
        assert "too tight" in entry["calibration"]
        assert "replicas are the honest figure" in entry["calibration"]

    def test_a_run_that_disowned_its_own_number_is_carried_through(
            self, tmp_path):
        """Not averaged into a summary as though it had not said so."""
        rng = np.random.RandomState(1)
        means = 0.30 + rng.normal(scale=0.02, size=6)
        runs = [
            _member(tmp_path, f"r{i}", m, 0.001,
                    sweep={"simulation.random_seed": 100 + i},
                    qualified=("this run is not long against its own "
                               "correlation time") if i < 2 else None)
            for i, m in enumerate(means)
        ]
        result = aggregate_members(
            _campaign(tmp_path, runs, {"simulation.random_seed": []}))
        entry = result["analyses"]["rmsd"]

        assert entry["qualified_by_their_own_runs"] == ["r0", "r1"]
        assert "said as much themselves" in entry["calibration"]

    def test_runs_that_said_nothing_are_not_said_to_have_said_so(self, tmp_path):
        """Two replicas with an error of 0.0005 and one with none, none of
        them saying its mean is not a measurement: the comparison said "The
        runs said as much themselves". And one that gave an error but said
        its mean is not a measurement was counted among "the runs that gave
        an error of their own" and then among "None of the 3 replicas
        determined its own mean with an error of its own". The members'
        reason opened "the spread of their means is its error" above every
        quantity's "is not yet an error"."""
        seeds = {"simulation.random_seed": []}
        quiet = [_member(tmp_path / "quiet", f"r{i}", m, se,
                         sweep={"simulation.random_seed": i})
                 for i, (m, se) in enumerate([(0.10, 0.0005), (0.12, 0.0005), (0.14, None)])]
        result = aggregate_members(_campaign(tmp_path / "quiet", quiet, seeds))
        said = result["analyses"]["rmsd"]["calibration"]
        assert "too tight" in said and "said as much" not in said
        assert ("1 of the 3 replicas did not determine its own mean with an error of its own, "
                "so their spread is not yet an error") in said
        assert result["why"].endswith(
            "the spread of their means is its error once each run has determined its own mean")
        mixed = [_member(tmp_path / "mixed", f"r{i}", m, se, sweep={"simulation.random_seed": i},
                         qualified=why)
                 for i, (m, se, why) in enumerate([(0.10, 0.0005, "Too short."),
                                                   (0.12, None, None), (0.14, None, None)])]
        said = aggregate_members(_campaign(tmp_path / "mixed", mixed, seeds))["analyses"]["rmsd"][
            "calibration"]
        assert "(from the 1 of 3 runs that gave an error of their own)" in said
        assert ("None of the 3 replicas determined its own mean: 1 said its mean is not "
                "determined, and 2 gave no error of their own, so their spread is not yet "
                "an error") in said
        assert "said as much themselves" in said


class TestWhatItRefuses:
    def test_a_directory_that_is_not_a_campaign(self, tmp_path):
        result = aggregate_members(tmp_path)
        assert "not a campaign" in result["refused"]

    def test_one_member_is_not_a_comparison(self, tmp_path):
        runs = [_member(tmp_path, "only", 0.3, 0.001)]
        result = aggregate_members(_campaign(tmp_path, runs, {}))
        assert "at least two" in result["refused"]

    def test_members_without_a_settled_mean(self, tmp_path):
        runs = []
        for i in range(2):
            out = tmp_path / "runs" / f"r{i}"
            (out / "analysis" / "rmsd").mkdir(parents=True, exist_ok=True)
            (out / "analysis" / "rmsd" / "options.json").write_text(
                json.dumps({"analysis": "rmsd", "findings": {}}),
                encoding="utf-8")
            runs.append({"run_id": f"r{i}", "output_dir": str(out),
                         "status": "ok", "system": "a.pdb"})
        result = aggregate_members(_campaign(tmp_path, runs, {}))
        assert "nothing to compare" in result["refused"]


class TestReadingOneMember:
    def test_it_finds_every_analysis(self, tmp_path):
        _member(tmp_path, "r0", 0.3, 0.001)
        findings = read_member_findings(tmp_path / "runs" / "r0")
        assert "rmsd" in findings
        assert findings["rmsd"]["mean"]["mean"] == pytest.approx(0.3)

    def test_unreadable_records_are_skipped_not_fatal(self, tmp_path):
        out = tmp_path / "runs" / "r0"
        (out / "analysis" / "broken").mkdir(parents=True, exist_ok=True)
        (out / "analysis" / "broken" / "options.json").write_text(
            "{not json", encoding="utf-8")
        assert read_member_findings(out) == {}


def test_replicas_from_the_models_of_an_ensemble_are_said_so_not_seed_alone(tmp_path):
    """Runs prepared from different models of an ensemble (`setup.model`)
    are replicas, independent starts of one molecule, and were said to
    "differ only by random seed" by the aggregate, the plan, the runs'
    table and a stopping rule's verdict. One function says how replicas
    differ, and every one of them uses it."""
    from fastmdxplora.gui.plan import plan_of
    from fastmdxplora.gui.runs_compared import runs_compared
    from fastmdxplora.simulation.stopping import Verdict, _said_met
    from tests.test_the_overview_of_a_study_of_runs import _a_study_of_runs

    study = _a_study_of_runs(tmp_path, "setup.model", [1, 5, 12])
    summary = aggregate_members(study, share=False)
    assert summary["replicas"] is True
    assert summary["why"].startswith(
        "the members are replicas started from different models of the ensemble, so they "
        "are repeats of one estimate"), summary["why"]
    assert "random seed" not in summary["why"]
    answer = runs_compared(study)
    assert answer["replicas"] is True
    assert answer["replicas_said"] == "started from different models of the ensemble"
    plan = {line["label"]: line["value"] for line in plan_of(
        {"systems": [{"system": "1L2Y"}], "sweep": {"setup.model": [1, 5, 12]}})}
    assert plan["Replicas"] == "3, started from models 1, 5, 12 of the ensemble"
    # Models and seeds together: one line, never a second "differing only by
    # random seed" of runs that also differ by model.
    lines = [(line["label"], line["value"]) for line in plan_of(
        {"systems": [{"system": "1L2Y"}],
         "sweep": {"setup.model": [1, 5, 12], "simulation.random_seed": [1, 2]}})
        if line["label"] in ("Replicas", "Varied")]
    assert lines == [("Replicas", "6, started from models 1, 5, 12 of the ensemble, "
                                  "each with a seed of its own")], lines
    met = _said_met([Verdict("rmsd", True, "rmsd 0.150 ± 0.001 nm")], 2.0, 3, "required",
                    axes=["setup.model"])
    assert "The 3 replicas started from different models of the ensemble, so" in met
    assert "seed" not in met and "one structure" not in met

    from fastmdxplora.batch.aggregate import how_replicas_differ

    assert how_replicas_differ(["simulation.random_seed"]) == "that differ only by random seed"
    assert how_replicas_differ(["setup.random_seed"], as_participle=True) == \
        "differing only by random seed"
    assert how_replicas_differ(["setup.model", "simulation.random_seed"]) == \
        "started from different models of the ensemble, each with a seed of its own"
