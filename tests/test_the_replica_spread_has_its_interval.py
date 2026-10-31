"""The replicas' spread against the stated error, with how uncertain it is.

The variance ratio (the SD of the replica means squared over the mean of
the runs' own errors squared) carries its chi-square 95% interval on R - 1
degrees of freedom, and the combined mean its error, SD / sqrt(R). Too few
replicas, or runs that stated no error, are said in the report, not raised.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from scipy.stats import chi2

from fastmdxplora.validation import replica_calibration
from tests.test_replicas_check_the_stated_error import _campaign


def _records(means, errors):
    return [{"mean": m, "standard_error": e} for m, e in zip(means, errors)]


def _with_spread(count: int, sd: float) -> list[float]:
    """``count`` means whose sample SD is exactly ``sd``."""
    z = np.random.default_rng(7).normal(size=count)
    z = (z - z.mean()) / z.std(ddof=1)
    return list(5.0 + sd * z)


def test_the_interval_is_the_chi_square_one():
    means = _with_spread(10, 0.03)
    row = replica_calibration._compare(_records(means, [0.02] * 10))
    ratio = 0.03 ** 2 / 0.02 ** 2
    assert row["variance_ratio"] == pytest.approx(ratio, rel=1e-12)
    assert row["degrees_of_freedom"] == 9
    low, high = row["variance_ratio_interval"]
    assert low == pytest.approx(ratio * 9 / chi2.ppf(0.975, 9), rel=1e-9)
    assert high == pytest.approx(ratio * 9 / chi2.ppf(0.025, 9), rel=1e-9)
    # The SD-scale ratio's interval is the square root of the same one.
    assert row["ratio"] == pytest.approx(1.5, rel=1e-12)
    assert row["ratio_interval"] == pytest.approx(
        [1.5 * math.sqrt(9 / chi2.ppf(0.975, 9)),
         1.5 * math.sqrt(9 / chi2.ppf(0.025, 9))], rel=1e-9)
    assert row["combined_mean_standard_error"] == pytest.approx(0.03 / math.sqrt(10))
    assert row["said"] == []


def test_ten_replicas_give_the_plan_s_widths_the_other_way_round(tmp_path, capsys):
    # The preprint's ratio is stated error over observed SD, the reciprocal
    # of this one. The V5r read-back's RMSD and Rg (0.10), Ser190 (3.06) and
    # Val213 (0.34) are, here, spreads 10, 1/3.06 and 1/0.34 times the
    # stated error; the reciprocals of their intervals at R = 10 are the
    # plan's 0.06 to 0.15, 1.7 to 4.4 and 0.19 to 0.49.
    for preprint, plan in ((0.10, (0.055, 0.145)), (3.06, (1.68, 4.45)),
                           (0.34, (0.186, 0.494))):
        row = replica_calibration._compare(
            _records(_with_spread(10, 1.0 / preprint), [1.0] * 10))
        assert row["ratio"] == pytest.approx(1.0 / preprint)
        low, high = row["ratio_interval"]
        assert (1.0 / high, 1.0 / low) == pytest.approx(plan, abs=0.006)
    rmsd = replica_calibration._compare(_records(_with_spread(10, 10.0), [1.0] * 10))
    assert rmsd["ratio_interval"] == pytest.approx([6.88, 18.26], abs=0.01)
    vlow, vhigh = rmsd["variance_ratio_interval"]
    assert vhigh / vlow == pytest.approx(chi2.ppf(0.975, 9) / chi2.ppf(0.025, 9))
    # The direction is said where the numbers are read.
    _campaign(tmp_path)
    assert replica_calibration.main([str(tmp_path)]) == 0
    assert "the preprint's ratio is the reciprocal of this one" in capsys.readouterr().out


def test_withheld_means_are_left_out_of_the_spread():
    records = _records(_with_spread(10, 0.1), [0.05] * 10)
    for r in records[7:]:
        r["mean"] += 5.0
        r["not_a_measurement"] = "too short against its correlation time"
    row = replica_calibration._compare(records)
    determined = _records(_with_spread(10, 0.1)[:7], [0.05] * 7)
    alone = replica_calibration._compare(determined)
    assert row["withheld"] == 3 and row["means"] == 7
    for key in ("observed_spread", "mean_of_means", "combined_mean_standard_error",
                "variance_ratio", "degrees_of_freedom"):
        assert row[key] == pytest.approx(alone[key])
    assert row["degrees_of_freedom"] == 6
    assert row["said"][0] == ("3 of 10 runs withheld their mean as not a "
                              "measurement; they are left out of the spread, "
                              "R and the combined mean's error.")
    every = replica_calibration._compare(
        [{**r, "not_a_measurement": "short"} for r in records])
    assert every["observed_spread"] is None and every["combined_mean_standard_error"] is None
    assert every["said"][-1] == ("0 of 10 runs determined a mean, so there is "
                                 "no spread of means to compare.")


def test_two_replicas_are_said_not_raised(tmp_path, capsys):
    # Long enough that neither run withholds its mean.
    _campaign(tmp_path, replicas=2, n=8000)
    assert replica_calibration.main([str(tmp_path), "--out", str(tmp_path / "o.json")]) == 0
    said = capsys.readouterr().out
    assert "Variance ratio (95% interval)" in said
    assert "With 2 means the variance ratio's interval spans a factor of 5.12e+03" in said
    row = json.loads((tmp_path / "o.json").read_text())["analyses"]["rg"]["own_start"]
    assert row["degrees_of_freedom"] == 1
    assert row["variance_ratio_interval"][1] / row["variance_ratio_interval"][0] > 5000


def test_a_missing_error_is_said_not_raised():
    none = replica_calibration._compare(_records(_with_spread(10, 0.1), [None] * 10))
    assert none["variance_ratio"] is None and none["variance_ratio_interval"] is None
    assert none["said"] == ["No run stated an error, so nothing predicts the spread."]
    # The combined mean's error needs only the means.
    assert none["combined_mean_standard_error"] == pytest.approx(0.1 / math.sqrt(10))

    some = replica_calibration._compare(
        _records(_with_spread(10, 0.1), [0.05] * 7 + [None, float("nan"), 0.0]))
    assert some["variance_ratio"] == pytest.approx(4.0)
    assert some["said"] == [
        "3 of the 10 runs with a mean stated no error; the prediction is "
        "from the other 7."]

    one = replica_calibration._compare(_records([1.0, None], [0.1, 0.1]))
    assert one["observed_spread"] is None and one["variance_ratio"] is None
    assert one["said"] == ["1 of 2 runs determined a mean, so there is no spread "
                           "of means to compare."]


def test_the_report_says_what_each_number_is(tmp_path, capsys):
    _campaign(tmp_path)
    assert replica_calibration.main([str(tmp_path)]) == 0
    said = capsys.readouterr().out
    assert "Observed between-replica variability" in said
    assert "Combined mean's error" in said and "SD over sqrt(R)" in said
    # The ratio's interval is in the table, beside the ratio.
    assert "Ratio (95% interval)" in said
    assert "error bar" not in said
