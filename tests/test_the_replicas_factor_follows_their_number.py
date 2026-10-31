"""The factor the replicas' spread is held to comes from how many there
are: the spread of n means reaches past the true error by chance as far
as the chi-squared distribution with n - 1 degrees of freedom allows. A
study of 3 was told "the factor of 2 that ten replicas can resolve"
(the chignolin study on aailab01, 10-18)."""

from __future__ import annotations

import json

import pytest
from scipy.stats import chi2

from fastmdxplora.batch import aggregate
from fastmdxplora.batch.aggregate import aggregate_members


def _member(root, run_id, mean, se, *, sweep):
    out = root / "runs" / run_id
    (out / "analysis" / "rmsd").mkdir(parents=True, exist_ok=True)
    record = {"mean": {"mean": mean, "standard_error": se, "effective_samples": 120.0}}
    (out / "analysis" / "rmsd" / "options.json").write_text(
        json.dumps({"analysis": "rmsd", "findings": record}), encoding="utf-8")
    return {"run_id": run_id, "output_dir": str(out), "status": "ok",
            "system": "a.pdb", "sweep_values": sweep}


def _campaign(root, runs, sweep):
    (root / "batch_manifest.json").write_text(
        json.dumps({"runs": runs, "sweep": sweep}), encoding="utf-8")
    return root


def _study(tmp_path, means, se):
    runs = [_member(tmp_path, f"r{i}", m, se, sweep={"simulation.random_seed": 100 + i})
            for i, m in enumerate(means)]
    return aggregate_members(_campaign(tmp_path, runs,
                                       {"simulation.random_seed": []}))["analyses"]["rmsd"]


@pytest.mark.parametrize("n, factor", [(2, 2.3), (3, 2.0), (5, 1.7), (10, 1.5)])
def test_the_factor_is_the_upper_end_of_the_spreads_interval(n, factor):
    calibration_factor = getattr(aggregate, "calibration_factor", lambda n: 2.0)
    assert calibration_factor(n) == factor
    exact = (chi2.ppf(0.975, n - 1) / (n - 1)) ** 0.5
    assert exact <= calibration_factor(n) < exact + 0.1


def test_three_replicas_too_tight_are_said_as_three(tmp_path):
    # Spread 0.01 against 0.001: past any factor.
    said = _study(tmp_path, [0.30, 0.31, 0.32], 0.001)["calibration"]
    assert "the factor of 2.0 that the spread of 3 replicas passes by chance in 2.5% of studies" in said
    assert "ten" not in said and "can resolve" not in said
    assert "too tight" in said


def test_three_replicas_within_are_said_as_three(tmp_path):
    # Spread 0.01 against 0.006: a ratio of 1.7, within 2.0 for 3.
    said = _study(tmp_path, [0.30, 0.31, 0.32], 0.006)["calibration"]
    assert "within the factor of 2.0 that the spread of 3 replicas" in said
    assert "this many" not in said


def test_ten_replicas_hold_a_tighter_factor(tmp_path):
    # A ratio of 1.7: within 2 as before, but past the 1.5 ten replicas reach.
    means = [0.30 + 0.01 * (i - 4.5) / 3.0277 for i in range(10)]
    entry = _study(tmp_path, means, 0.01 / 1.7)
    assert entry["ratio_observed_to_predicted"] == pytest.approx(1.7, abs=0.01)
    said = entry["calibration"]
    assert "more than the factor of 1.5 that the spread of 10 replicas" in said


def test_the_factor_applied_passes_by_chance_in_no_more_than_2_5_percent():
    calibration_factor = getattr(aggregate, "calibration_factor", lambda n: 2.0)
    for n in range(2, 201):
        factor = calibration_factor(n)
        assert chi2.sf(factor ** 2 * (n - 1), n - 1) <= 0.025, (n, factor)
