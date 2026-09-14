"""The one headline number a study produces should not be the one without a bar.

A potential of mean force came back with an error bar per bin and the binding
free energy taken from it came back with none. What it did carry was
`cutoff_sensitivity_kjmol` -- how far the answer moves across defensible
choices of where the bound state ends -- which is a systematic, not noise, and
which reads as an error bar when it is the only bar on the page.

The fix is not to propagate a derivative. The bootstrap already resamples the
windows and rebuilds the whole curve two hundred times; a binding free energy
computed from each of those curves is the spread of the measurement itself.
That costs nothing beyond the WHAM solve already being done, which is why the
quantity rides along as one more element of the same statistic rather than
paying for a second bootstrap.

Two things the arithmetic has to get right. The resampled quantity is the
*uncorrected* free energy, because the cone correction and the standard-state
term are constants: they move an interval without widening it, so the bounds
shift and the standard error does not. And an interval whose block length is a
lower bound is optimistic by an unknown factor, so that fact travels with the
number to where it is read rather than staying where it was computed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fastmdxplora.simulation.binding import (
    _what_the_resamples_said,
    binding_free_energy,
)
from fastmdxplora.simulation.umbrella import (
    Cone,
    UmbrellaPlan,
    Window,
    _split_off_the_derived,
    compute_pmf,
)

KB_KJ = 0.008314462618
KT = KB_KJ * 300.0


def a_study(n_windows: int = 30, force_constant: float = 3000.0, seed: int = 0):
    """Windows sampling a well with a proper bulk tail, so binding succeeds.

    The range matters as much as the count: the bulk check needs the outer
    points to follow `-2kT ln r`, and a study that stops at 1.8 nm has not
    reached bulk here -- the fixture refused its own binding free energy
    until it ran to 2.0.
    """
    rng = np.random.default_rng(seed)
    centres = np.round(0.4 + np.arange(n_windows) * (1.6 / (n_windows - 1)), 4)

    def true_free_energy(r):
        return -20.0 * np.exp(-((r - 0.45) / 0.10) ** 2) - 2.0 * KT * np.log(r)

    plan = UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c),
                             force_constant=force_constant)
                      for i, c in enumerate(centres)),
        collective_variable="ligand_distance",
        equilibration_fraction=0.2, minimum_overlap=0.03, minimum_samples=200,
        cone=Cone(half_angle_deg=73.0, axis_atoms=(1, 2, 3)),
    )
    # Drawn exactly from each window's biased distribution rather than by
    # Metropolis. A short chain leaves the outer windows noisy, and the bulk
    # check is a test of shape: a fixture whose own tail wanders by more than
    # the tolerance refuses its own binding free energy, and then the test
    # measures the sampler instead of the code.
    grid = np.linspace(0.30, 2.20, 4000)
    samples = {}
    for window in plan.windows:
        biased = -(true_free_energy(grid)
                   + 0.5 * force_constant * (grid - window.centre) ** 2) / KT
        weight = np.exp(biased - biased.max())
        samples[window.index] = rng.choice(
            grid, size=4000, p=weight / weight.sum())
    return samples, plan


def uncorrected(where, curve):
    return binding_free_energy(where, curve,
                               temperature_K=300.0).get("delta_g_kjmol")


@pytest.fixture(scope="module")
def recombined():
    samples, plan = a_study()
    payload = compute_pmf(samples, plan, temperature_K=300.0,
                          bootstrap_resamples=30, also=uncorrected)
    return payload, plan


# ---------------------------------------------------------------------------
class TestTheNumberRidesOnTheCurvesOwnResamples:

    def test_the_derived_spread_is_reported(self, recombined):
        payload, _ = recombined
        derived = payload["derived"]
        assert derived is not None
        for key in ("value", "standard_error", "confidence_low",
                    "confidence_high", "resamples", "correlation_resolved"):
            assert key in derived
        assert isinstance(derived["value"], float)

    def test_it_is_the_quantity_the_curve_itself_gives(self, recombined):
        """`value` is the statistic on the real data, not the mean of the
        resamples -- so it must equal what the finished curve gives."""
        payload, _ = recombined
        straight = uncorrected(payload["pmf"]["coordinate"],
                               payload["pmf"]["free_energy_kjmol"])
        assert payload["derived"]["value"] == pytest.approx(straight, abs=1e-9)

    def test_the_curve_keeps_its_own_error_bar_per_bin(self, recombined):
        """The appended element is split off again: a band one longer than the
        coordinate would put every bin's error on the wrong bin."""
        payload, _ = recombined
        bins = len(payload["pmf"]["coordinate"])
        for key in ("value", "standard_error", "confidence_low",
                    "confidence_high"):
            assert len(payload["pmf"]["uncertainty"][key]) == bins

    def test_a_study_that_asks_for_nothing_extra_is_unchanged(self):
        samples, plan = a_study(n_windows=16, seed=4)
        payload = compute_pmf(samples, plan, temperature_K=300.0,
                              bootstrap_resamples=8)
        assert payload.get("derived") is None
        assert len(payload["pmf"]["uncertainty"]["standard_error"]) == len(
            payload["pmf"]["coordinate"])

    def test_a_statistic_that_raises_does_not_take_the_study_with_it(self):
        """A resampled curve can be one a derived quantity refuses. That is a
        fact about the resample, not a reason to lose the recombination."""
        samples, plan = a_study(n_windows=16, seed=5)

        def explodes(where, curve):
            raise ValueError("not on my watch")

        payload = compute_pmf(samples, plan, temperature_K=300.0,
                              bootstrap_resamples=8, also=explodes)
        assert payload["pmf"]["free_energy_kjmol"]
        assert payload["derived"]["value"] is None
        assert "nothing to put an interval around" in payload["derived"]["note"]


# ---------------------------------------------------------------------------
class TestSplittingTheAppendedNumberBackOff:

    def test_lists_are_split_and_scalars_are_shared(self):
        curve, one = _split_off_the_derived({
            "value": [1.0, 2.0, 3.0, 99.0],
            "standard_error": [0.1, 0.2, 0.3, 9.9],
            "resamples": 200, "note": "", "correlation_resolved": True,
        })
        assert curve["value"] == [1.0, 2.0, 3.0]
        assert one["value"] == 99.0
        assert curve["standard_error"] == [0.1, 0.2, 0.3]
        assert one["standard_error"] == 9.9
        assert curve["resamples"] == one["resamples"] == 200
        assert one["correlation_resolved"] is True

    def test_a_number_that_is_not_a_number_carries_no_interval(self):
        _, one = _split_off_the_derived({
            "value": [1.0, float("nan")], "standard_error": [0.1, 0.2],
            "resamples": 10, "note": "",
        })
        assert one["value"] is None
        assert "nothing to put an interval around" in one["note"]


# ---------------------------------------------------------------------------
class TestTheCorrectionsMoveTheIntervalWithoutWideningIt:

    def measured(self, recombined, **kwargs):
        payload, plan = recombined
        return binding_free_energy(
            payload["pmf"]["coordinate"], payload["pmf"]["free_energy_kjmol"],
            temperature_K=300.0, resampled=payload["derived"], **kwargs)

    def test_the_interval_brackets_the_answer(self, recombined):
        result = self.measured(recombined, cone=recombined[1].cone,
                               wall_bias_kjmol=0.0)
        low, high = result["delta_g_confidence_kjmol"]
        assert low <= result["delta_g_kjmol"] <= high

    def test_the_cone_shifts_the_bounds_by_its_own_correction(self,
                                                              recombined):
        bare = self.measured(recombined)
        coned = self.measured(recombined, cone=recombined[1].cone,
                              wall_bias_kjmol=0.0)
        correction = coned["cone_correction_kjmol"]
        assert correction > 0.0
        for a, b in zip(bare["delta_g_confidence_kjmol"],
                        coned["delta_g_confidence_kjmol"]):
            assert b == pytest.approx(a + correction, abs=1e-9)

    def test_a_constant_does_not_widen_an_interval(self, recombined):
        """The whole reason the resampled quantity is the uncorrected one."""
        bare = self.measured(recombined)
        coned = self.measured(recombined, cone=recombined[1].cone,
                              wall_bias_kjmol=0.0)
        assert (coned["delta_g_standard_error_kjmol"]
                == pytest.approx(bare["delta_g_standard_error_kjmol"]))
        width = lambda r: (r["delta_g_confidence_kjmol"][1]  # noqa: E731
                           - r["delta_g_confidence_kjmol"][0])
        assert width(coned) == pytest.approx(width(bare), abs=1e-9)

    def test_the_resampled_quantity_is_the_uncorrected_one(self, recombined):
        coned = self.measured(recombined, cone=recombined[1].cone,
                              wall_bias_kjmol=0.0)
        payload, _ = recombined
        assert coned["delta_g_before_the_cone_kjmol"] == pytest.approx(
            payload["derived"]["value"], abs=1e-9)


# ---------------------------------------------------------------------------
class TestWhatTheNumberSaysAboutItself:

    def test_a_floor_is_named_as_a_floor(self):
        said = _what_the_resamples_said(
            {"value": -20.0, "standard_error": 1.0, "confidence_low": -22.0,
             "confidence_high": -18.0, "correlation_resolved": False,
             "note": "shorter than its own correlation time", "resamples": 200},
            correction=2.5)
        assert said["delta_g_uncertainty_is_a_floor"] is True
        assert said["delta_g_confidence_kjmol"] == [-19.5, -15.5]
        assert "correlation time" in said["delta_g_uncertainty_note"]
        assert said["delta_g_resamples"] == 200

    def test_a_resolved_bar_is_not_a_floor(self):
        said = _what_the_resamples_said(
            {"value": -20.0, "standard_error": 1.0, "confidence_low": -22.0,
             "confidence_high": -18.0, "correlation_resolved": True,
             "note": "", "resamples": 200}, correction=0.0)
        assert said["delta_g_uncertainty_is_a_floor"] is False
        assert said["delta_g_uncertainty_note"] is None

    def test_no_resampling_means_no_bar_rather_than_a_made_up_one(self):
        for nothing in (None, {}, {"value": None}):
            said = _what_the_resamples_said(nothing, correction=2.5)
            assert said["delta_g_standard_error_kjmol"] is None
            assert said["delta_g_confidence_kjmol"] is None
            assert said["delta_g_uncertainty_is_a_floor"] is None

    def test_the_systematic_and_the_statistical_stay_separate(self,
                                                              recombined):
        """`cutoff_sensitivity_kjmol` is how much the definition of the bound
        state is doing; the standard error is how much the sampling is. One
        standing in for the other is the defect this closed."""
        payload, plan = recombined
        result = binding_free_energy(
            payload["pmf"]["coordinate"], payload["pmf"]["free_energy_kjmol"],
            temperature_K=300.0, cone=plan.cone, wall_bias_kjmol=0.0,
            resampled=payload["derived"])
        assert result["cutoff_sensitivity_kjmol"] is not None
        assert result["delta_g_standard_error_kjmol"] is not None
        assert not math.isclose(result["cutoff_sensitivity_kjmol"],
                                result["delta_g_standard_error_kjmol"])

    def test_a_refusal_still_refuses(self, recombined):
        """A curve with no bulk tail has no binding free energy, and handing
        it an error bar does not give it one."""
        payload, plan = recombined
        rising = [v + 3.0 * i for i, v in
                  enumerate(payload["pmf"]["free_energy_kjmol"])]
        result = binding_free_energy(
            payload["pmf"]["coordinate"], rising, temperature_K=300.0,
            resampled=payload["derived"])
        assert result["delta_g_kjmol"] is None
        assert result["refused"]
