"""A block bootstrap's 95% interval on correlated frames should hold the
answer 95% of the time.

It held it about 90%. On AR(1) series the moving-block bootstrap's variance
runs low by two terms: the correlation reaching across the cut between
blocks, about ``g / (2b)`` of it for blocks of ``b`` frames, and the
resamples' centring on the run's own mean, ``b / n``. At blocks of 2g (the
rule before) the standard error of a mean was 11% to 13% low whatever the
series' length, and a binding free energy's 10% low, with
`correlation_resolved` true and nothing flagged. Blocks are now 3g and the
spread is widened by :func:`uncertainty.spread_factor`, which undoes both.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.signal import lfilter
from scipy.special import ndtr
from scipy.stats import t as student

from fastmdxplora import uncertainty
from fastmdxplora.statistics import statistical_inefficiency
from fastmdxplora.uncertainty import (
    block_bootstrap,
    block_length_for,
    paired_block_bootstrap,
)


def spread_factor(frames, block, inefficiency):
    """Read at call time, so the coverage tests run (and fail) on a tree
    without it."""
    return uncertainty.spread_factor(frames, block, inefficiency)


def ar1(rho, frames, rng):
    """An AR(1) series of unit variance, started in its stationary state."""
    noise = rng.normal(0.0, math.sqrt(1.0 - rho ** 2), frames)
    return lfilter([1.0], [1.0, -rho], noise, zi=[rho * rng.normal()])[0]


def exact_error(rho, frames):
    """The standard error of an AR(1) series' mean, from its correlation."""
    lag = np.arange(1, frames)
    return math.sqrt((1.0 + 2.0 * np.sum((1.0 - lag / frames) * rho ** lag))
                     / frames)


def correlated_coverage(series, frames, rho, resamples):
    """How often mean +/- t * SE holds the true mean of 0, and the exact
    standard error over the rms of the bootstrap's."""
    rng = np.random.default_rng(7)
    t = float(student.ppf(0.975, resamples - 1))
    held, squares = 0, 0.0
    for seed in range(series):
        values = ar1(rho, frames, rng)
        boot = block_bootstrap(values, np.mean, resamples=resamples, seed=seed)
        error = float(boot.standard_error)
        held += abs(float(boot.value)) <= t * error
        squares += error ** 2
    return held / series, exact_error(rho, frames) / math.sqrt(squares / series)


def test_a_correlated_mean_s_interval_holds_it_95_percent_of_the_time():
    """600 series of 10,000 frames at rho 0.9 (g = 19), 100 resamples each:
    a coverage of 95% has a binomial error of 0.9%. It holds the mean in
    95.2% here, the exact error 0.99 times the bootstrap's; before, 91.2%,
    and the exact error 1.14 times the bootstrap's."""
    held, ratio = correlated_coverage(600, 10000, 0.9, 100)

    assert 0.93 <= held <= 0.975, (held, ratio)
    assert ratio == pytest.approx(1.0, abs=0.04), (held, ratio)


def test_independent_frames_are_left_as_they_were():
    rng = np.random.default_rng(3)
    values = rng.normal(size=5000)
    boot = block_bootstrap(values, np.mean, resamples=200, seed=1)

    assert boot.spread_factor == pytest.approx(1.0, abs=0.01)
    assert float(boot.standard_error) == pytest.approx(
        1.0 / math.sqrt(values.size), rel=0.1)


class TestTheFactor:

    def test_blocks_of_one_frame_are_widened_by_the_root_of_g(self):
        """One-frame blocks treat every frame as independent: their
        variance is the series' over g."""
        assert spread_factor(10 ** 9, 1, 19.0) == pytest.approx(
            math.sqrt(19.0), rel=1e-6)

    def test_blocks_of_k_correlation_times_lose_about_one_in_2k(self):
        for k in (2, 3, 5, 10):
            g = 400.0
            assert spread_factor(10 ** 12, int(k * g), g) == pytest.approx(
                1.0 / math.sqrt(1.0 - 1.0 / (2 * k)), rel=0.01)

    def test_it_is_what_an_ar1_block_variance_misses(self):
        """``A`` is the variance a block of b holds of an AR(1) series,
        over b times the series' long-run variance: computed here from the
        correlation itself."""
        rho, b = 0.9, 57
        g = (1 + rho) / (1 - rho)
        lag = np.arange(1, b)
        held = (1.0 + 2.0 * np.sum((1.0 - lag / b) * rho ** lag)) / g
        assert spread_factor(10 ** 12, b, g) == pytest.approx(
            1.0 / math.sqrt(held), rel=1e-6)

    def test_the_centring_on_the_run_s_own_mean_is_undone(self):
        assert spread_factor(3000, 300, 1.0) == pytest.approx(
            math.sqrt(3000 / 2700))

    def test_a_block_the_whole_series_has_nothing_to_widen(self):
        assert spread_factor(3000, 3000, 19.0) == 1.0
        assert spread_factor(1, 1, 19.0) == 1.0

    def test_blocks_are_three_correlation_times(self):
        values = ar1(0.9, 20000, np.random.default_rng(11))
        g = statistical_inefficiency(values)

        assert uncertainty.BLOCKS_PER_CORRELATION_TIME == 3
        assert block_length_for(values) == math.ceil(3 * g)


class TestTheBootstrapsWidenTheirSpread:

    @staticmethod
    def _raw(values, block, resamples, seed, statistic=np.mean):
        """The resamples as taken, before any widening."""
        rng = np.random.default_rng(seed)
        return np.array([statistic(uncertainty._resample_one(values, block, rng))
                         for _ in range(resamples)])

    def test_the_error_and_the_percentiles_are_widened_about_the_resamples(
            self):
        values = ar1(0.9, 4000, np.random.default_rng(5))
        boot = block_bootstrap(values, np.mean, resamples=60, seed=2)
        taken = self._raw(values, boot.block_length, 60, 2)
        factor = spread_factor(values.size, boot.block_length,
                               statistical_inefficiency(values))
        middle = taken.mean()

        assert boot.spread_factor == pytest.approx(factor)
        assert boot.spread_factor > 1.05
        assert float(boot.standard_error) == pytest.approx(
            factor * taken.std(ddof=1))
        assert float(boot.low) == pytest.approx(
            middle + factor * (np.percentile(taken, 2.5) - middle))
        assert float(boot.high) == pytest.approx(
            middle + factor * (np.percentile(taken, 97.5) - middle))
        assert boot.as_dict()["spread_factor"] == pytest.approx(factor)

    def test_several_series_take_the_largest_factor(self):
        rng = np.random.default_rng(9)
        series = {"slow": ar1(0.95, 3000, rng), "fast": rng.normal(size=3000)}
        boot = block_bootstrap(series, lambda s: s["slow"].mean() + s["fast"].mean(),
                               resamples=20, seed=1)
        slow = spread_factor(3000, block_length_for(series["slow"]),
                             statistical_inefficiency(series["slow"]))

        assert boot.spread_factor == pytest.approx(slow)
        assert slow > spread_factor(3000, block_length_for(series["fast"]),
                                    statistical_inefficiency(series["fast"]))

    def test_a_paired_bootstrap_reads_both_from_the_g_it_is_given(self):
        values = ar1(0.5, 3000, np.random.default_rng(4))
        weights = np.ones_like(values)

        def mean(v, w):
            return float(np.sum(v * w) / np.sum(w))

        boot = paired_block_bootstrap([values, weights], mean, resamples=20,
                                      inefficiency=40.0)
        assert boot.block_length == 120
        assert boot.spread_factor == pytest.approx(spread_factor(3000, 120, 40.0))
        own = paired_block_bootstrap([values, weights], mean, resamples=20)
        assert own.block_length == block_length_for(values)


def test_a_reweighted_mean_s_blocks_follow_the_slower_series():
    from fastmdxplora.analysis import reweight

    rng = np.random.default_rng(2)
    values = ar1(0.5, 6000, rng)
    weights = reweight.Weights(values=np.ones(6000), effective_sample_size=6000.0,
                               converged=True)
    result = reweight.weighted_uncertainty(values, weights, resamples=20,
                                           inefficiency=30.0)

    assert result["block_length"] == 90
    assert result["spread_factor"] == pytest.approx(spread_factor(6000, 90, 30.0))


def test_the_pmf_s_resampled_curves_carry_the_widened_spread():
    """The rows a barrier and the bulk are judged against have the spread
    the error says, not the narrower one the blocks drew."""
    import logging

    from fastmdxplora.simulation.umbrella import compute_pmf
    from fastmdxplora.validation import pmf_coverage

    profile = pmf_coverage.Profile()
    design = pmf_coverage.Design(samples=1500)
    plan = pmf_coverage._plan(design)
    sampled = pmf_coverage._window_samples(profile, design, plan,
                                           np.random.default_rng(1))
    logging.disable(logging.WARNING)
    try:
        band = compute_pmf(sampled, plan, temperature_K=design.temperature_K,
                           bins=design.bins, bootstrap_resamples=20,
                           bootstrap_seed=3)["pmf"]["uncertainty"]
    finally:
        logging.disable(logging.NOTSET)
    rows = np.array([[np.nan if v is None else v for v in row]
                     for row in band["resample_curves_kjmol"]], dtype=float)
    error = np.array([np.nan if v is None else v for v in band["standard_error"]],
                     dtype=float)
    full = np.isfinite(rows).all(axis=0) & (error > 0)

    assert band["spread_factor"] > 1.05
    assert np.allclose(rows[:, full].std(axis=0, ddof=1), error[full], rtol=1e-9)


def test_the_fraction_below_a_cutoff_is_covered_too():
    """The part of a binding free energy that moves: the share of frames
    in the bound range, here of a distance-like transform of the series.
    Its correlation decays faster than the series', so the factor read
    from the series' g errs high, not low: 95.5% here, and 89.5% before."""
    rng = np.random.default_rng(13)
    t = float(student.ppf(0.975, 99))
    held = 0
    for seed in range(400):
        z = ar1(0.9, 6000, rng)
        distance = np.tanh(z) + 0.3 * z
        boot = block_bootstrap(distance, lambda d: np.mean(d < np.tanh(-0.5) - 0.15),
                               resamples=100, seed=seed)
        held += abs(float(boot.value) - float(ndtr(-0.5))) <= t * float(
            boot.standard_error)
    assert 0.93 <= held / 400 <= 0.985, held / 400


def test_a_reweighted_estimate_passes_the_slower_g(monkeypatch):
    from fastmdxplora.analysis import reweight

    seen = {}
    real = reweight.weighted_uncertainty

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(reweight, "weighted_uncertainty", spy)
    rng = np.random.default_rng(6)
    values = ar1(0.5, 6000, rng)
    weights = reweight.Weights(values=np.ones(6000), effective_sample_size=6000.0,
                               converged=True)
    record = reweight.reweighted_estimate(values, weights, inefficiency=40.0,
                                          resamples=20)

    assert record["standard_error"] is not None
    assert seen["inefficiency"] == pytest.approx(record["statistical_inefficiency"])
    assert "block_length" not in seen
