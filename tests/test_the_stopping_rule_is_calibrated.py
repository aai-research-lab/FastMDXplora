"""The harness that calibrates the stopping rule's stated precision.

Its counts are pre-registered in `preregistration/stopping-calibration.md`.
These tests use study indices from 2,000,000, outside the registered and
held-out sets, so nothing here is a look at the result.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from fastmdxplora.validation import stopping_calibration as calibration

OUTSIDE = 2_000_000


def test_the_series_have_the_correlation_they_are_said_to():
    from fastmdxplora.statistics import statistical_inefficiency

    process = calibration.Correlated(mean=1.0, sigma=0.05, phi=0.95)
    rng = np.random.default_rng(1)
    series = process.more(rng, process.start(rng), 200_000)
    assert series.mean() == pytest.approx(1.0, abs=0.003)
    assert series.std() == pytest.approx(0.05, rel=0.03)
    assert statistical_inefficiency(series) == pytest.approx(39.0, rel=0.15)


def test_a_series_continues_where_it_stopped():
    process = calibration.Correlated(mean=0.0, sigma=1.0, phi=0.99)
    rng = np.random.default_rng(2)
    state = process.start(rng)
    first = process.more(rng, state, 1000)
    second = process.more(rng, state, 1000)
    # Consecutive frames across the join are as correlated as any others.
    joined = np.concatenate([first, second])
    lag_one = np.corrcoef(joined[:-1], joined[1:])[0, 1]
    assert abs(second[0] - first[-1]) < 6 * math.sqrt(1 - 0.99 ** 2)
    assert lag_one == pytest.approx(0.99, abs=0.01)


def test_two_states_start_where_they_are_said_to():
    held = calibration.TwoStates((1.0, 1.2), 1e12, 0.01, 0.5, "first")
    rng = np.random.default_rng(3)
    assert held.more(rng, held.start(rng), 500).mean() == pytest.approx(1.0, abs=0.01)
    drawn = calibration.TwoStates((1.0, 1.2), 1e12, 0.01, 0.5, "drawn")
    starts = [drawn.start(rng)["s"] for _ in range(200)]
    assert 60 < sum(starts) < 140


def test_two_states_are_held_and_left():
    process = calibration.TwoStates((1.0, 1.2), 200.0, 0.01, 0.5, "first")
    rng = np.random.default_rng(3)
    series = process.more(rng, process.start(rng), 100_000)
    assert process.truth == pytest.approx(1.1)
    assert series.mean() == pytest.approx(1.1, abs=0.02)
    upper = series > 1.1
    switches = int(np.count_nonzero(upper[1:] != upper[:-1]))
    assert 300 < switches < 700


def test_a_study_is_judged_and_extended_by_the_rule(tmp_path):
    case = calibration.CASES[0]
    study = calibration.run_study(case, 0, OUTSIDE, tmp_path / "study")
    record = json.loads((tmp_path / "study" / "stopping.json").read_text(encoding="utf-8"))
    assert study.outcome == record["outcome"] in ("met", "ceiling")
    assert study.rounds == len(record["rounds"])
    assert study.production_ns == record["rounds"][-1]["production_ns"]
    assert case.first_ns <= study.production_ns <= case.ceiling_ns
    recorded = json.loads((tmp_path / "study" / "run_0" / "analysis" / "rmsd" / "options.json")
                          .read_text(encoding="utf-8"))["findings"]["mean"]
    assert recorded["n_frames"] == round(study.production_ns * calibration.FRAMES_PER_NS)


def test_the_same_seed_makes_the_same_study(tmp_path):
    case = calibration.CASES[1]
    first = calibration.run_study(case, 1, OUTSIDE, tmp_path / "a")
    again = calibration.run_study(case, 1, OUTSIDE, tmp_path / "b")
    assert (first.outcome, first.value, first.error, first.production_ns) == (
        again.outcome, again.value, again.error, again.production_ns)


def test_a_fixed_length_estimate_uses_the_same_judge(tmp_path):
    case = calibration.CASES[1]
    study = calibration.fixed_length(case, 1, OUTSIDE, 20.0, tmp_path / "fixed")
    assert study.outcome == "fixed" and study.production_ns == 20.0
    assert study.truth == 1.0


def test_the_floors_are_four_binomial_errors_below_nominal():
    assert calibration.threshold(calibration.ONE_SIGMA, 1000) == pytest.approx(0.624, abs=1e-3)
    assert calibration.threshold(calibration.TWO_SIGMA, 1000) == pytest.approx(0.928, abs=1e-3)


def test_coverage_counts_errors_against_the_truth():
    studies = [calibration.Study("c", i, "met", 1.0 + z * 0.01, 0.01, 10.0, 2, 1.0)
               for i, z in enumerate([0.5, -0.5, 1.5, -2.5])]
    said = calibration.coverage(studies)
    assert said["counted"] == 4
    assert said["within_one_error"] == 0.5 and said["within_two_errors"] == 0.75
    assert said["mean_z"] == pytest.approx(-0.25)
    assert calibration.coverage([])["counted"] == 0


def test_the_withholding_is_counted_on_both_series():
    rows = calibration.withholding(count=1, start=OUTSIDE)
    assert {(r["series"], r["multiple"]) for r in rows} == {
        (name, m) for name, _p, _g in calibration.CHECK_SERIES for m in calibration.CHECK_LENGTHS}
    assert all(0 <= r["unresolved"] <= r["withheld"] <= r["counted"] == 1 for r in rows)


def test_an_unknown_case_is_refused(capsys):
    assert calibration.main(["--cases", "no_such_case"]) == 2
    assert "fast_one_run" in capsys.readouterr().err


def test_the_cases_are_the_registered_ones():
    from pathlib import Path

    registered = (Path(__file__).resolve().parent.parent / "preregistration"
                  / "stopping-calibration.md").read_text(encoding="utf-8")
    for case in calibration.CASES:
        assert f"`{case.name}`" in registered
