"""Recovering unbiased averages from a metadynamics trajectory.

A metadynamics run deliberately distorts the ensemble -- that is how it
escapes minima -- so an average over its frames is an average over a
distribution nobody wanted. Reported as a property of the system, it is wrong
in a way that looks entirely plausible.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.analysis.reweight import (
    KB_KJ_PER_MOL_K,
    bias_at_each_frame,
    weighted_mean,
    weighted_standard_deviation,
    weights_from_bias,
)


def _two_states(seed: int = 0, n: int = 4000, fraction_in_b: float = 0.2):
    """Samples from a two-state system with a known population."""
    rng = np.random.default_rng(seed)
    in_b = rng.random(n) < fraction_in_b
    return np.where(in_b, rng.normal(2.0, 0.3, n), rng.normal(0.0, 0.3, n))


class TestItRecoversAKnownPopulation:
    """The test that matters: a case where the right answer is known
    independently of the code being tested."""

    def test_a_flattened_trajectory_is_corrected(self) -> None:
        truth = _two_states(seed=0, fraction_in_b=0.2)
        # What metadynamics samples once the bias has levelled the states.
        flattened = _two_states(seed=1, fraction_in_b=0.5)

        kT = KB_KJ_PER_MOL_K * 300.0
        # The bias that levelled them: it raised the deeper state.
        bias = np.where(flattened < 1.0, kT * np.log(0.8 / 0.2), 0.0)
        weights = weights_from_bias(bias, temperature_K=300.0)

        biased_population = float(np.mean(flattened > 1.0))
        corrected = weighted_mean((flattened > 1.0).astype(float), weights)
        true_population = float(np.mean(truth > 1.0))

        assert biased_population == pytest.approx(0.5, abs=0.05)
        assert corrected == pytest.approx(true_population, abs=0.03)

    def test_an_unbiased_run_is_left_alone(self) -> None:
        """Zero bias must give equal weights, or reweighting would change an
        answer that was already right."""
        values = _two_states(seed=2)
        weights = weights_from_bias(np.zeros(len(values)), temperature_K=300.0)

        assert weighted_mean(values, weights) == pytest.approx(
            float(np.mean(values)))
        assert np.allclose(weights.values, 1.0)


class TestTheBiasIsTheOneEachFrameFelt:
    """Weighting every frame by the final bias is the common shortcut and it
    is wrong early in a run, where the frame was sampled under almost none of
    it. That shows up as an effective sample size of one or two."""

    def test_an_early_frame_feels_little_bias(self) -> None:
        hills_times = np.arange(1.0, 101.0)
        centres = np.zeros(100)
        sigmas = np.full(100, 0.3)
        heights = np.full(100, 1.0)

        felt = bias_at_each_frame(
            hills_times, centres, sigmas, heights,
            frame_times_ps=np.array([0.5, 50.0, 100.0]),
            frame_values=np.array([0.0, 0.0, 0.0]))

        assert felt[0] == 0.0, "a frame before any hill felt no bias"
        assert felt[1] == pytest.approx(50.0, abs=1.0)
        assert felt[2] == pytest.approx(100.0, abs=1.0)

    def test_a_distant_frame_feels_less_than_a_near_one(self) -> None:
        felt = bias_at_each_frame(
            np.array([1.0]), np.array([0.0]), np.array([0.3]),
            np.array([1.0]),
            frame_times_ps=np.array([2.0, 2.0]),
            frame_values=np.array([0.0, 1.0]))
        assert felt[0] > felt[1]


class TestItSaysHowMuchTheAverageRestsOn:
    """A weighted mean over a thousand frames whose weight sits in five of
    them is a mean over five, and quoting it as a thousand overstates it by a
    factor of fourteen."""

    def test_equal_weights_use_every_frame(self) -> None:
        weights = weights_from_bias(np.zeros(1000), temperature_K=300.0)
        assert weights.effective_sample_size == pytest.approx(1000.0)
        assert weights.usable_fraction == pytest.approx(1.0)

    def test_concentrated_weight_is_reported_as_such(self) -> None:
        bias = np.zeros(1000)
        bias[:5] = 50.0  # five frames carrying almost all the weight
        weights = weights_from_bias(bias, temperature_K=300.0)

        assert weights.effective_sample_size < 20
        assert weights.usable_fraction < 0.02

    def test_the_spread_uses_the_effective_count(self) -> None:
        """Dividing by the frame count would understate it: a thousand
        frames whose weight sits in fifty carry the uncertainty of fifty."""
        values = _two_states(seed=3, n=1000)
        bias = np.zeros(1000)
        bias[:50] = 20.0
        weights = weights_from_bias(bias, temperature_K=300.0)

        assert np.isfinite(weighted_standard_deviation(values, weights))


class TestItDoesNotOverflowOrLieWhenItCannot:
    def test_a_large_bias_does_not_become_infinity(self) -> None:
        """exp of a hundred kilojoules over RT overflows a float, and the
        weighted mean is then a nan with nothing saying why."""
        weights = weights_from_bias(
            np.array([0.0, 100.0, 200.0]), temperature_K=300.0)
        assert np.all(np.isfinite(weights.values))
        assert np.isfinite(weights.effective_sample_size)

    def test_a_bias_that_had_not_converged_is_flagged(self) -> None:
        """The simple estimator assumes a converged bias. Weights from a
        surface still filling are approximate, and worth having and saying."""
        weights = weights_from_bias(
            np.zeros(100), temperature_K=300.0, converged=False)
        assert weights.converged is False
        assert "approximate" in weights.note

    def test_no_frames_is_not_an_answer(self) -> None:
        weights = weights_from_bias(np.array([]), temperature_K=300.0)
        assert weights.effective_sample_size == 0.0
        assert weighted_mean(np.array([]), weights) != weighted_mean(
            np.array([1.0]), weights)  # both nan-ish, neither a number
