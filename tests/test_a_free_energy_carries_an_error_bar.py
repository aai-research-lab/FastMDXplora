"""AUD7: no free energy in the package carried an uncertainty.

`statistics.py` gives an honest standard error for the mean of a per-frame
number, and refuses where the series is too short to measure its own
correlation time. A free energy is not the mean of anything -- WHAM solves
over every window at once, a metadynamics surface is a sum over hills, a
reweighted average divides by a partition function estimated from the same
samples -- so none of them could be handed to it, and none of them carried
a bar. `reweighted_std` moved 3% for a 37-fold drop in effective sample
size while the report printed it as a plus-or-minus.

The tests below check the two things that make a bootstrap honest rather
than merely present: that the width is right when the answer is known, and
that a series too short to measure its own correlation time is told so
instead of being given a tight bar.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.uncertainty import (
    BLOCKS_PER_CORRELATION_TIME,
    block_bootstrap,
    block_length_for,
)


def _correlated(n: int, tau: int, seed: int = 0) -> np.ndarray:
    """A series with a known correlation time: a boxcar of width tau."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, n + 10 * tau)
    return np.convolve(noise, np.ones(tau) / tau, mode="valid")[:n]


class TestTheWidthIsRightWhereTheAnswerIsKnown:

    def test_uncorrelated_data_matches_the_analytic_error(self) -> None:
        """sigma/sqrt(n), which is the one case with a closed form."""
        rng = np.random.default_rng(0)
        n = 4000
        series = rng.normal(0.0, 1.0, n)
        got = float(block_bootstrap(series, np.mean, resamples=300,
                                    seed=1).standard_error)
        assert got == pytest.approx(1.0 / np.sqrt(n), rel=0.15)

    def test_correlated_data_is_not_given_the_uncorrelated_error(self) -> None:
        """The whole point. Treating 4,000 correlated frames as 4,000
        independent ones understates the error by about sqrt(g)."""
        series = _correlated(4000, tau=50)
        naive = float(np.std(series, ddof=1) / np.sqrt(series.size))
        got = float(block_bootstrap(series, np.mean, resamples=300,
                                    seed=1).standard_error)
        assert got > 3.0 * naive, (
            f"bootstrap {got:.5f} is not meaningfully wider than the naive "
            f"{naive:.5f}; the blocks are not carrying the correlation")

    def test_a_longer_correlation_time_gives_a_longer_block(self) -> None:
        assert block_length_for(_correlated(4000, tau=5)) < \
               block_length_for(_correlated(4000, tau=50))

    def test_the_block_spans_the_correlation_time(self) -> None:
        """Blocks shorter than g break correlations that are really there
        and shrink the bar; the factor is stated, so it is checkable."""
        series = _correlated(4000, tau=40)
        from fastmdxplora.statistics import statistical_inefficiency
        g = statistical_inefficiency(series)
        assert block_length_for(series) >= BLOCKS_PER_CORRELATION_TIME * g - 1


class TestAnUnmeasurableCorrelationTimeIsSaidSo:

    def test_a_short_series_is_flagged_rather_than_given_a_tight_bar(
            self) -> None:
        """This is the case V5 is pre-registered to expose. A bar printed
        here would be the defect rather than the fix."""
        result = block_bootstrap(_correlated(60, tau=50), np.mean,
                                 resamples=100, seed=1)
        assert result.resolved is False
        assert result.note is not None
        assert "optimistic" in result.note

    def test_a_long_series_is_not_flagged(self) -> None:
        result = block_bootstrap(_correlated(4000, tau=5), np.mean,
                                 resamples=100, seed=1)
        assert result.resolved is True
        assert result.note is None


class TestTheValueIsTheDataNotTheResamples:

    def test_the_reported_value_is_computed_on_the_real_samples(self) -> None:
        """Using the resample mean would add a bias the bootstrap was not
        asked to introduce."""
        rng = np.random.default_rng(3)
        series = rng.normal(5.0, 1.0, 500)
        result = block_bootstrap(series, np.mean, resamples=50, seed=1)
        assert float(result.value) == pytest.approx(float(np.mean(series)))

    def test_it_is_reproducible_from_its_seed(self) -> None:
        series = _correlated(1000, tau=10)
        a = block_bootstrap(series, np.mean, resamples=50, seed=7)
        b = block_bootstrap(series, np.mean, resamples=50, seed=7)
        assert float(a.standard_error) == float(b.standard_error)


class TestThePmfCarriesOne:

    @staticmethod
    def _plan():
        from fastmdxplora.simulation.umbrella import plan_windows
        return plan_windows({
            "collective_variable": "distance",
            "selection_a": "resid 1", "selection_b": "resid 2",
            "force_constant": 1000.0, "from": 0.4, "to": 1.2,
            "n_windows": 8,
        })

    @staticmethod
    def _samples(plan, n: int = 1200):
        rng = np.random.default_rng(0)
        return {w.index: w.centre + rng.normal(0.0, 0.06, n)
                for w in plan.windows}

    def test_the_curve_comes_back_with_a_bar_per_bin(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf
        plan = self._plan()
        result = compute_pmf(self._samples(plan), plan, bootstrap_resamples=40)
        curve = result["pmf"]
        assert curve["uncertainty"] is not None
        assert len(curve["uncertainty"]["standard_error"]) == \
               len(curve["free_energy_kjmol"])

    def test_the_bins_do_not_move_between_resamples(self) -> None:
        """The grid is drawn from the data's extremes, so an unpinned
        resample lands on a different grid and the bin-by-bin spread
        measures where the bins went rather than what the energy did."""
        from fastmdxplora.simulation.umbrella import compute_pmf
        plan = self._plan()
        samples = self._samples(plan)
        a = compute_pmf(samples, plan, bootstrap_resamples=0)
        b = compute_pmf(samples, plan, bootstrap_resamples=20)
        assert a["pmf"]["coordinate"] == b["pmf"]["coordinate"]

    def test_the_free_energy_itself_is_unchanged_by_asking_for_a_bar(
            self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf
        plan = self._plan()
        samples = self._samples(plan)
        without = compute_pmf(samples, plan, bootstrap_resamples=0)
        with_bar = compute_pmf(samples, plan, bootstrap_resamples=20)
        assert (without["pmf"]["free_energy_kjmol"]
                == with_bar["pmf"]["free_energy_kjmol"])

    def test_it_can_be_turned_off(self) -> None:
        from fastmdxplora.simulation.umbrella import compute_pmf
        plan = self._plan()
        result = compute_pmf(self._samples(plan), plan, bootstrap_resamples=0)
        assert result["pmf"]["uncertainty"] is None

    def test_each_resample_is_zeroed_at_the_curves_lowest_bin(
            self, monkeypatch) -> None:
        """Zeroed at its own lowest bin, which noise puts low, each resample
        sat above the curve, and the band's middle with it. At the curve's
        lowest bin every resample now reads 0, so the band there has no
        width, and the record says where that is and what the zero does."""
        from fastmdxplora.simulation import umbrella

        resampled: list[np.ndarray] = []
        real = umbrella.block_bootstrap

        def recording(samples, statistic, **kwargs):
            def recorded_one(drawn):
                values = np.asarray(statistic(drawn), dtype=float)
                resampled.append(values)
                return values
            return real(samples, recorded_one, **kwargs)

        monkeypatch.setattr(umbrella, "block_bootstrap", recording)
        # A double well sampled exactly, so the lowest bin is in a well
        # rather than a lone sample at an end of the grid.
        from fastmdxplora.validation import pmf_coverage

        profile, design = pmf_coverage.Profile(), pmf_coverage.Design()
        plan = pmf_coverage._plan(design)
        sampled = pmf_coverage._window_samples(profile, design, plan,
                                               np.random.default_rng(1000))
        result = umbrella.compute_pmf(sampled, plan,
                                      temperature_K=design.temperature_K,
                                      bins=design.bins, bootstrap_resamples=30)
        curve = np.array([np.nan if v is None else v
                          for v in result["pmf"]["free_energy_kjmol"]])
        anchor = int(np.nanargmin(curve))
        # The first call is the real data; the rest are the resamples.
        drawn = np.array(resampled[1:])
        assert len(drawn) == 30
        # A resample with nothing in that bin is left out whole, and counted.
        out = np.isnan(drawn[:, anchor])
        assert np.all(np.isnan(drawn[out]))
        assert np.all(drawn[~out, anchor] == 0.0) and (~out).sum() >= 25
        # Not each at its own lowest bin: some go below zero elsewhere.
        assert np.nanmin(drawn) < 0.0
        band = result["pmf"]["uncertainty"]
        assert band["resamples_left_out"] == int(out.sum())
        assert band["confidence_low"][anchor] == band["confidence_high"][anchor] == 0.0
        assert band["standard_error"][anchor] == 0.0
        assert band["zeroed_at"] == result["pmf"]["coordinate"][anchor]
        assert band["zero"] == umbrella.THE_CURVE_SITS_HIGH
        assert "about 0.8 of their error high" in band["zero"]


class TestAReweightedAverageSaysWhenItsBarIsAFloor:
    """The concrete half of AUD7.

    `reweighted_std` describes the spread of the reweighted distribution,
    not the error on its mean, and it moved 3% for a 37-fold drop in
    effective sample size while the report printed it as a plus-or-minus.
    A resampling bar answers the right question -- but only while enough
    frames still carry the average, and the point where it stops is
    measured rather than assumed.
    """

    @staticmethod
    def _series(seed: int, n: int = 2000, tau: int = 30, spread: float = 0.0):
        from fastmdxplora.analysis.reweight import weights_from_bias

        rng = np.random.default_rng(seed)
        noise = rng.normal(0.0, 1.0, n + 10 * tau)
        values = np.convolve(noise, np.ones(tau) / tau, mode="valid")[:n]
        bias = rng.normal(0.0, spread, n) * 2.494 if spread else np.zeros(n)
        return values, weights_from_bias(bias, temperature_K=300.0)

    def test_even_weights_are_not_called_a_floor(self) -> None:
        from fastmdxplora.analysis.reweight import weighted_uncertainty

        values, weights = self._series(0)
        result = weighted_uncertainty(values, weights, resamples=80)
        assert result["is_a_floor"] is False
        assert result["effective_fraction"] > 0.9

    def test_concentrated_weights_are_called_a_floor(self) -> None:
        """The failure this exists to prevent is a tight-looking bar on an
        average that a handful of frames decide."""
        from fastmdxplora.analysis.reweight import weighted_uncertainty

        values, weights = self._series(0, spread=3.0)
        result = weighted_uncertainty(values, weights, resamples=80)
        assert result["is_a_floor"] is True
        assert "replicas" in str(result["note"])

    def test_the_bar_responds_to_the_sampling_behind_it(self) -> None:
        """`reweighted_std` moved 3% across this range, which is the
        complaint. It need not be exact -- the floor note covers the rest --
        but it has to move."""
        from fastmdxplora.analysis.reweight import weighted_uncertainty

        even = weighted_uncertainty(*self._series(0), resamples=120)
        tight = weighted_uncertainty(*self._series(0, spread=3.0),
                                     resamples=120)
        assert tight["standard_error"] > 1.2 * even["standard_error"]

    def test_it_matches_the_estimator_s_own_spread_where_it_claims_to(
            self) -> None:
        """Ground truth: the spread of the reweighted mean over independent
        realisations. Checked in the regime the constant says is trustworthy,
        because a bar validated only where it happens to work is the defect
        rather than the fix."""
        from fastmdxplora.analysis.reweight import weighted_uncertainty

        def mean_of(seed: int) -> float:
            values, weights = self._series(seed)
            w = np.asarray(weights.values, dtype=float)
            return float(np.sum(values * w) / np.sum(w))

        truth = float(np.std([mean_of(s) for s in range(60)], ddof=1))
        got = float(weighted_uncertainty(*self._series(0),
                                         resamples=200)["standard_error"])
        assert got == pytest.approx(truth, rel=0.35)

    def test_the_pairing_is_kept(self) -> None:
        """Resampling values and weights apart would put one frame's value
        with another's weight, which is not a resample of anything."""
        from fastmdxplora.uncertainty import paired_block_bootstrap

        a = np.arange(100.0)
        with pytest.raises(ValueError, match="same length"):
            paired_block_bootstrap([a, a[:50]], lambda x, y: float(x.sum()),
                                   resamples=5)
