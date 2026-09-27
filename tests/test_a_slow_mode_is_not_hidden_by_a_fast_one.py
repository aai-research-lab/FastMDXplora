"""A slow correlation is not hidden by a fast one that alternates frame to frame.

The statistical inefficiency summed the autocorrelation lag by lag and stopped
at the first lag where it was not positive. A stiff restraint sampled more
slowly than the mode it restrains makes consecutive frames anti-correlated,
so the sum stopped at lag one and returned g = 1 whatever moved underneath:
every frame counted as independent, the standard error came out as small as
it can be, and the halving test called the correlation resolved because there
was nothing to resolve. 21 of the 35 windows of the benzamidine unbinding
study reported a correlation time they could not have measured.

The sum now runs over adjacent pairs of lags (Geyer's initial positive
sequence), which cancels the alternation and keeps the slow part. The series
here are sums of two AR(1) processes, one slow and one with a negative
coefficient, whose inefficiency is known in closed form.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.statistics import (
    correlation_is_resolved,
    statistical_inefficiency,
    summarise,
)


def _ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    values = np.empty(n)
    values[0] = rng.normal() / np.sqrt(1.0 - phi * phi)
    noise = rng.normal(size=n)
    for i in range(1, n):
        values[i] = phi * values[i - 1] + noise[i]
    return values / np.std(values)


def _true_inefficiency(weight_slow: float, slow: float, fast: float) -> float:
    return (weight_slow * (1 + slow) / (1 - slow)
            + (1 - weight_slow) * (1 + fast) / (1 - fast))


def _mixture(n: int, seed: int, *, weight_slow=0.3, slow=0.995, fast=-0.8):
    rng = np.random.default_rng(seed)
    return (np.sqrt(weight_slow) * _ar1(n, slow, rng)
            + np.sqrt(1 - weight_slow) * _ar1(n, fast, rng))


def _single_lag_rule(series: np.ndarray) -> float:
    """The estimator as it was, written out here so the fixture is shown to
    carry the defect independently of the code under test."""
    n = series.size
    fluctuation = series - series.mean()
    variance = float(np.mean(fluctuation ** 2))
    total = 0.0
    for lag in range(1, n - 1):
        c = float(np.mean(fluctuation[: n - lag] * fluctuation[lag:]) / variance)
        if c <= 0.0:
            break
        total += (1.0 - lag / n) * c
    return max(1.0, 1.0 + 2.0 * total)


class TestASlowModeUnderAnAlternatingOne:

    TRUE = _true_inefficiency(0.3, 0.995, -0.8)      # 119.8

    def test_the_fixture_carries_the_defect(self) -> None:
        assert _single_lag_rule(_mixture(40000, seed=0)) == pytest.approx(1.0)

    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_the_inefficiency_is_found(self, seed: int) -> None:
        measured = statistical_inefficiency(_mixture(40000, seed=seed))
        assert self.TRUE / 1.6 < measured < self.TRUE * 1.6, (
            f"{measured:.1f} against a true {self.TRUE:.1f}")

    def test_the_error_is_not_that_of_independent_frames(self) -> None:
        series = _mixture(40000, seed=0)
        equilibrated, _ = summarise(series)
        naive = float(np.std(series, ddof=1) / np.sqrt(series.size))
        assert equilibrated.effective_samples < series.size / 50
        if not np.isnan(equilibrated.standard_error):
            assert equilibrated.standard_error > 5 * naive

    def test_a_run_too_short_for_the_slow_mode_is_not_called_resolved(self) -> None:
        # Four thousand frames against a slow mode of correlation time ~1000.
        # With g read as 1 the halving test had nothing to resolve and passed.
        series = _mixture(4000, seed=3, slow=0.999)
        assert _single_lag_rule(series) == pytest.approx(1.0)
        assert not correlation_is_resolved(series)


class TestWhereNothingAlternatesTheAnswerIsUnchanged:

    @pytest.mark.parametrize("phi", [0.5, 0.9, 0.99])
    def test_it_agrees_with_the_single_lag_rule(self, phi: float) -> None:
        series = _ar1(20000, phi, np.random.default_rng(11))
        assert statistical_inefficiency(series) == pytest.approx(
            _single_lag_rule(series), rel=0.02)

    @pytest.mark.parametrize("phi", [0.5, 0.9])
    def test_and_with_the_known_answer(self, phi: float) -> None:
        series = _ar1(50000, phi, np.random.default_rng(5))
        assert statistical_inefficiency(series) == pytest.approx(
            (1 + phi) / (1 - phi), rel=0.1)


def test_an_alternating_series_counts_every_frame_once_and_no_more() -> None:
    # Anti-correlated frames would give g below one, more independent samples
    # than frames. That is never claimed.
    series = _ar1(20000, -0.9, np.random.default_rng(2))
    assert statistical_inefficiency(series) == 1.0


def test_a_weak_long_correlation_is_not_read_lower_than_before() -> None:
    """Segments joined with small offsets between them: a weak correlation
    that lasts a segment. The monotone variant of the pair rule read it lower
    than the single-lag rule did, and a low inefficiency is a small error
    bar, so it was left out. Averaged over realisations, the rule in use
    reads it at least as high as the old one."""
    spread = 0.07
    ours, before = [], []
    for seed in range(60):
        rng = np.random.default_rng(seed)
        offsets = rng.normal(0.0, spread, 10)
        series = np.concatenate([rng.normal(10.0 + o, 0.3, 400) for o in offsets])
        ours.append(statistical_inefficiency(series))
        before.append(_single_lag_rule(series))
    assert np.mean(ours) >= np.mean(before)
