"""Three or four precise segment means can resolve a drift.

An order-permutation test cannot reach 0.05 with so few segments. Absolute
per-segment errors supply information that ordering alone cannot: a line
must have a significant slope and leave residuals consistent with those
errors. The slope must also stand against the means' own scatter about the
line, so offsets between segments larger than their errors (errors too
small, or a short restart between long segments) qualify the pooled error
rather than being mistaken for a steady trend.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import chi2, linregress, norm

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import _drift_test, drift_across_segments, summarise_segments


def test_means_that_step_back_are_not_said_to_move_in_order():
    # Five segments: the permutation test calls this trend (4 of 120
    # orderings are as steep), but the third mean steps back, so "the
    # segment means move in order" would describe means the reader can see
    # do not. The verdict is the same as before; only what it says changed.
    means = np.array([0.0, 1.0, 0.8, 2.0, 3.0])
    _, p, why = _drift_test(means, np.full(5, 100.0))
    assert p == pytest.approx(4 / 120) and why is not None
    assert "move in order" not in str(why)
    assert "not one way at every step" in str(why)
    _, _, why = _drift_test(np.arange(5.0), np.full(5, 100.0))
    assert "move in order" in str(why) and "not one way" not in str(why)


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("direction", [-1, 1])
def test_a_precise_linear_drift_is_not_pooled(count, direction):
    means = direction * np.arange(count, dtype=float)
    scatter, p, why = _drift_test(means, np.full(count, 100.0))
    assert scatter > 1 and p < 0.05
    assert why is not None
    refusal = refusal_of(why)
    assert refusal.code == "analysis.sampling.drifting"
    assert refusal.details["drift_test"] == "slope_against_errors_and_scatter"
    assert "standard errors" in str(why) and "straight line" in str(why)
    assert "ordering this clean" not in str(why)
    # The same guard must keep its specificity: disagreement alone is not
    # a line, and an ordered difference smaller than the errors is not drift.
    patterns = ([[0.0, 10.0, 0.0]] if count == 3 else
                [[0.0, 10.0, 10.0, 0.0], [0.0, 0.0, 0.0, 10.0], [0.0, 0.0, 1.0, 10.0]])
    for pattern in patterns:
        scatter, _, why = _drift_test(np.array(pattern), np.full(count, 100.0))
        assert scatter > 2 and why is None
    scatter, _, why = _drift_test(np.linspace(0.0, 0.01, count), np.ones(count))
    assert scatter < 1 and why is None


@pytest.mark.parametrize("means,errors", [
    ([0.0, 0.8, 1.4], [0.5, 0.3, 0.4]),
    ([0.0, 0.4, 1.1, 1.7], [0.4, 0.3, 0.5, 0.6]),
])
def test_the_probability_matches_a_matrix_fit_using_absolute_errors(means, errors):
    means, errors = np.array(means), np.array(errors)
    design = np.column_stack((np.ones(means.size), np.arange(means.size)))
    a, b = design / errors[:, None], means / errors
    fitted, _, _, _ = np.linalg.lstsq(a, b, rcond=None)
    covariance = np.linalg.inv(a.T @ a)
    residual = (means - design @ fitted) / errors
    assert chi2.sf(float(residual @ residual), means.size - 2) > 0.05
    against_errors = 2 * norm.sf(abs(fitted[1]) / np.sqrt(covariance[1, 1]))
    # And against the means' own scatter: an unweighted fit's slope p-value,
    # Student's t on k - 2 degrees of freedom. The larger is the test's.
    against_scatter = linregress(np.arange(means.size), means).pvalue
    expected = max(against_errors, against_scatter)
    assert drift_across_segments(means, 1 / errors**2) == pytest.approx(expected)


def test_an_imprecise_middle_segment_does_not_make_two_ends_a_trend():
    # Two precise ends and a middle that says nothing are two means, and two
    # means always lie on a line: their difference is a trend or an offset
    # between segments, and nothing here tells which. A middle on the line
    # does say which.
    errors = np.array([0.1, 1000.0, 0.1])
    assert _drift_test(np.array([0.0, 1000.0, 2.0]), 1 / errors**2)[2] is None
    assert _drift_test(np.array([0.0, 1.0, 2.0]), 1 / errors**2)[2] is not None
    assert _drift_test(np.array([0.0, 1000.0, 2.0]), np.full(3, 100.0))[2] is None


@pytest.mark.parametrize("count", [3, 4])
def test_units_and_a_large_common_offset_do_not_change_the_verdict(count):
    means = np.arange(count, dtype=float)
    weights = np.linspace(0.5, 2.0, count)
    reference = drift_across_segments(means, weights)
    center = np.average(means, weights=weights)
    expected = 2 * norm.sf(np.sqrt(np.sum(weights * (means - center)**2)))
    assert reference == pytest.approx(expected)
    for scale in (1e-6, 1e6):
        assert drift_across_segments(means * scale, weights / scale**2) == \
            pytest.approx(reference)
    assert drift_across_segments(means + 1e12, weights) == pytest.approx(reference)


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, 0.0, -1.0])
def test_without_absolute_errors_the_ordering_test_keeps_its_limit(count, invalid):
    weights = np.full(count, 100.0)
    assert drift_across_segments(np.arange(count, dtype=float), weights) < 0.05
    weights[1] = invalid
    p = drift_across_segments(np.arange(count, dtype=float), weights)
    assert p >= 2 / math.factorial(count) - 1e-12


@pytest.mark.parametrize("count", [3, 4])
def test_a_non_finite_mean_is_not_given_a_significant_probability(count):
    means = np.arange(count, dtype=float)
    assert drift_across_segments(means, np.full(count, 100.0)) < 0.05
    means[1] = np.nan
    assert drift_across_segments(means, np.ones(count)) == 1.0


@pytest.mark.parametrize("count", [3, 4])
def test_an_extreme_normal_tail_is_not_reported_as_exactly_zero(count):
    p = drift_across_segments(np.arange(count, dtype=float), np.full(count, 1e6))
    assert 0 < p < 0.05


@pytest.mark.parametrize("count", [3, 4])
def test_the_joined_series_and_report_refuse_the_same_drift(count):
    from fastmdxplora.report.convergence import assess_series
    from tests.test_statistics import _segments

    # Reuse one stationary trace at each offset to make the segment means
    # exactly linear; independent traces are used in the rate check below.
    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([stationary + 2.0 * i for i in range(count)])
    joins = [stationary.size * i for i in range(1, count)]
    pooled, why = summarise_segments(values, joins)
    assert pooled is None
    assert refusal_of(why).code == "analysis.sampling.drifting"
    assessment = assess_series("rmsd", values, joins=joins)
    assert np.isnan(assessment.standard_error)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_the_pooled_record_says_which_test_it_used(count):
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.tile(stationary, count)
    pooled, why = summarise_segments(values, [stationary.size * i for i in range(1, count)])
    assert why is None
    assert pooled.as_record()["drift_test"] == (
        "slope_against_errors_and_scatter" if count < 5 else "permutation")


@pytest.mark.parametrize("offsets", [[0.0, 3.0, 0.0], [0.0, 3.0, -3.0, 0.0]])
def test_unordered_offsets_leave_a_qualification_about_stationarity(offsets):
    from tests.test_statistics import _segments

    stationary, _ = _segments([0.0], frames=4000, seed=18)
    values = np.concatenate([stationary + offset for offset in offsets])
    pooled, why = summarise_segments(values, [stationary.size * i for i in range(1, len(offsets))])
    assert why is None and pooled is not None
    assert "does not establish stationarity" in pooled.qualification
    assert "lower bound" in pooled.qualification


@pytest.mark.parametrize("count", [3, 4])
def test_known_error_stationary_means_stay_under_the_nominal_rate(count):
    # A fixed, independently generated null sample. Each half of the test
    # holds 5% on its own, so both together hold at most that.
    rng = np.random.default_rng(9217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None
                  for row in rng.normal(size=(trials, count)) * errors)
    ceiling = 0.05 + 4 * np.sqrt(0.05 * 0.95 / trials)
    # Counted 13 and 20 of 2000: a guard that never rejects is not one.
    assert 0 < refused / trials <= ceiling


ERRORS = {"equal": [0.1, 0.1, 0.1, 0.1], "short_restart": [0.1, 0.25, 0.1, 0.1],
          "precise_middle": [0.28, 0.1, 0.28, 0.28]}


@pytest.mark.parametrize("count,kind,offsets", [
    (count, kind, offsets) for count in (3, 4) for kind in ERRORS for offsets in (0.3, 1.0)
    # Trusting the errors called these two 2.3% and 4.8%: no claim to test.
    if (count, offsets) != (4, 1.0) or kind == "precise_middle"])
def test_unordered_offsets_beyond_the_errors_are_not_called_a_trend(count, kind, offsets):
    # Segments whose means sit apart by more than their errors say (errors
    # too small, or a restart that landed elsewhere) in no order: 3 and 10
    # times the smallest error. Trusting the errors called 12.7% to 40.1% of
    # these drifting (counted on the same draws); the permutation test at
    # five segments holds 5% whatever the offsets.
    errors = ERRORS[kind]
    rng = np.random.default_rng(39217 + count + int(10 * offsets))
    errors = np.array(errors[:count])
    trials = 2000
    rows = (rng.normal(size=(trials, count)) * errors
            + rng.normal(size=(trials, count)) * offsets)
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None for row in rows)
    assert refused / trials <= 0.05 + 4 * np.sqrt(0.05 * 0.95 / trials)


@pytest.mark.parametrize("count,least", [(3, 0.25), (4, 0.88)])
def test_a_noisy_resolved_linear_drift_is_detected_as_often_as_counted(count, least):
    # Counted 645 of 2000 at three segments and 1852 at four: three means
    # leave one degree of freedom for their scatter about the line, so a
    # trend of 2.5 to 10 errors a segment is called only when the line is
    # close to straight. Four leave two.
    rng = np.random.default_rng(19217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    rows = rng.normal(size=(trials, count)) * errors + 2 * np.arange(count)
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None for row in rows)
    assert refused / trials >= least


@pytest.mark.parametrize("count", [3, 4])
def test_the_slope_uses_a_two_sided_five_percent_threshold(count):
    x = np.arange(count, dtype=float)
    boundary = norm.isf(0.025) / np.sqrt(np.sum((x - x.mean())**2))
    assert _drift_test(0.999 * boundary * x, np.ones(count))[2] is None
    assert _drift_test(1.001 * boundary * x, np.ones(count))[2] is not None


@pytest.mark.parametrize("count", [3, 4])
def test_the_line_fit_uses_k_minus_two_degrees_of_freedom(count):
    # This residual is orthogonal to both columns of the straight-line
    # design, so it changes the lack of fit without changing the slope.
    residual = np.array([1.0, -2.0, 1.0] if count == 3 else [1.0, -1.0, -1.0, 1.0])
    residual /= np.linalg.norm(residual)
    for fits in (True, False):
        q = chi2.isf(0.05, count - 2) * (0.999 if fits else 1.001)
        # Steep enough that the scatter about the line does not decide.
        means = 100 * np.arange(count) + np.sqrt(q) * residual
        assert (_drift_test(means, np.ones(count))[2] is not None) == fits
