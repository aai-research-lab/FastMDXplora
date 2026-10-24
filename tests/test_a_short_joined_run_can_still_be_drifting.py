"""Three or four precise segment means can resolve a drift.

An order-permutation test cannot reach 0.05 with so few segments. Absolute
per-segment errors supply information that ordering alone cannot: a line
must have a significant slope and leave residuals consistent with those
errors. Unordered offsets still qualify the pooled error, rather than
being mistaken for a steady trend.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import chi2, norm

from fastmdxplora.refusals import refusal_of
from fastmdxplora.statistics import _drift_test, drift_across_segments, summarise_segments


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("direction", [-1, 1])
def test_a_precise_linear_drift_is_not_pooled(count, direction):
    means = direction * np.arange(count, dtype=float)
    scatter, p, why = _drift_test(means, np.full(count, 100.0))
    assert scatter > 1 and p < 0.05
    assert why is not None
    refusal = refusal_of(why)
    assert refusal.code == "analysis.sampling.drifting"
    assert refusal.details["drift_test"] == "weighted_slope"
    assert "standard errors" in str(why) and "straight-line" in str(why)
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
    expected = 2 * norm.sf(abs(fitted[1]) / np.sqrt(covariance[1, 1]))
    assert drift_across_segments(means, 1 / errors**2) == pytest.approx(expected)


def test_an_imprecise_middle_segment_does_not_outvote_precise_endpoints():
    means = np.array([0.0, 1000.0, 2.0])
    errors = np.array([0.1, 1000.0, 0.1])
    assert _drift_test(means, 1 / errors**2)[2] is not None
    assert _drift_test(means, np.full(3, 100.0))[2] is None


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
    assert pooled.as_record()["drift_test"] == ("weighted_slope" if count < 5 else "permutation")


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
def test_known_error_stationary_means_match_the_gated_nominal_rate(count):
    # A fixed, independently generated null sample. The line-fit gate can
    # only reduce the Gaussian slope test's 5% false-refusal rate.
    rng = np.random.default_rng(9217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None
                  for row in rng.normal(size=(trials, count)) * errors)
    ceiling = 0.05 + 4 * np.sqrt(0.05 * 0.95 / trials)
    # A known-error slope rejects 5%, independently of the line-fit gate
    # accepting 95%: a guard that never rejects is not calibrated either.
    floor = 0.0475 - 4 * np.sqrt(0.0475 * 0.9525 / trials)
    assert floor <= refused / trials <= ceiling


@pytest.mark.parametrize("count", [3, 4])
def test_a_noisy_resolved_linear_drift_is_usually_detected(count):
    rng = np.random.default_rng(19217 + count)
    errors = np.linspace(0.2, 0.8, count)
    trials = 2000
    rows = rng.normal(size=(trials, count)) * errors + 2 * np.arange(count)
    refused = sum(_drift_test(row, 1 / errors**2)[2] is not None for row in rows)
    assert refused / trials >= 0.90


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
        means = 10 * np.arange(count) + np.sqrt(q) * residual
        assert (_drift_test(means, np.ones(count))[2] is not None) == fits
