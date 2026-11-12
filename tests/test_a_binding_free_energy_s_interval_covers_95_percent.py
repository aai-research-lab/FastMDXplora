"""A binding free energy's 95% interval should hold the exact answer 95% of
the time.

It did not. The interval was the bootstrap's own 2.5th and 97.5th
percentiles, and on umbrella studies of wells with an exact answer (16
windows, 3000 frames each, 40 resamples) it held the exact binding free
energy in about 91% of studies, with the standard error right: the
percentiles of 40 resamples stand about 1.77 standard errors from the
middle, not 1.96, since the 97.5th of 40 values is read between the largest
two. The interval is now the number +/- Student's t at 95% on one fewer
degrees of freedom than the resamples that gave a number, times its
standard error, which held it in about 95%.

The umbrella studies cost seconds each, too slow for a thousand here. The
coverage test below takes the same steps on a cheaper study of the same
kind: frames taken exactly from a well's radial distribution, binned into
a free energy, its binding free energy computed by `binding_free_energy`
and resampled in blocks by `block_bootstrap`, then the interval read off
the record as a study's is. On the parent commit (the percentiles) it holds
the exact answer about 84% of the time at 20 resamples and 90% at 40.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import t as student

from fastmdxplora.simulation.binding import (
    KB_KJMOL,
    STANDARD_VOLUME_NM3,
    _what_the_resamples_said,
    binding_free_energy,
)
from fastmdxplora.uncertainty import block_bootstrap

TEMPERATURE = 300.0
KT = KB_KJMOL * TEMPERATURE
GRID = np.linspace(0.45, 1.6, 115001)
EDGES = np.linspace(0.45, 1.6, 41)
CENTRES = 0.5 * (EDGES[1:] + EDGES[:-1])


def well(r):
    """10 kJ/mol deep at 0.6 nm: a harmonic wall inside and a capped
    harmonic outside, flat (bulk) beyond 0.76 nm."""
    rise = -10.0 + np.where(r < 0.6, 600.0, 400.0) * (r - 0.6) ** 2
    return np.where(r < 0.6, rise, np.minimum(0.0, rise))


DENSITY = GRID ** 2 * np.exp(-well(GRID) / KT)
DENSITY /= DENSITY.sum()


def exact(cutoff):
    """Gilson et al. 1997 to the cutoff the study chose, on the well itself."""
    r = GRID[GRID <= cutoff]
    k = 4.0 * np.pi * np.trapezoid(r ** 2 * np.exp(-well(r) / KT), r)
    return -KT * math.log(k / STANDARD_VOLUME_NM3)


def free_energy(frames):
    """A(r) = -kT ln P(r) off a histogram; NaN where a bin is empty."""
    counts, _ = np.histogram(frames, EDGES)
    with np.errstate(divide="ignore"):
        curve = -KT * np.log(counts / counts.sum())
    curve[~np.isfinite(curve)] = np.nan
    return curve - np.nanmin(curve)


def resampled_binding(frames):
    """What the explorer's `_binding_from` gives a resample: the real
    curve's verdict on the bulk and the kT rule stands for it."""
    value = binding_free_energy(CENTRES, free_energy(frames),
                                temperature_K=TEMPERATURE,
                                judge_the_bulk=False).get("delta_g_kjmol")
    return np.nan if value is None else value


def coverage(studies, resamples):
    """How often the saved interval, and the percentiles, hold the exact
    answer, over the studies that gave a number."""
    rng = np.random.default_rng(2026)
    held = percentiles = given = 0
    for seed in range(studies):
        frames = rng.choice(GRID, size=10000, p=DENSITY)
        derived = block_bootstrap(frames, resampled_binding,
                                  resamples=resamples, block_length=10,
                                  seed=seed).as_dict()
        count = int(derived["resamples"])
        derived["resamples_with_a_number"] = count
        derived["resamples_without_a_number"] = 0
        result = binding_free_energy(CENTRES, free_energy(frames),
                                     temperature_K=TEMPERATURE,
                                     resampled=derived)
        if result["delta_g_kjmol"] is None:
            continue
        given += 1
        answer = exact(result["bound_cutoff_nm"])
        low, high = result["delta_g_confidence_kjmol"]
        held += low <= answer <= high
        percentiles += (derived["confidence_low"] <= answer
                        <= derived["confidence_high"])
    return held / given, percentiles / given, given


@pytest.mark.parametrize("resamples, studies", [(20, 500), (40, 600)])
def test_the_95_percent_interval_holds_the_exact_answer_95_percent_of_the_time(
        resamples, studies):
    """At 500 studies a coverage of 95% has a binomial error of 1%, so the
    band 92% to 98% is three of them either side; the percentiles fall
    below it (about 84% at 20 resamples and 90% at 40)."""
    held, percentiles, given = coverage(studies, resamples)

    assert given >= 0.95 * studies
    assert 0.92 <= held <= 0.98, (held, percentiles, given)
    assert held - percentiles >= 0.03, (held, percentiles, given)


# ---------------------------------------------------------------------------
def derived(**changes):
    record = {"value": -20.0, "standard_error": 1.0, "confidence_low": -21.5,
              "confidence_high": -18.4, "resamples": 40,
              "resamples_with_a_number": 36, "resamples_without_a_number": 4,
              "correlation_resolved": True, "note": None}
    record.update(changes)
    return record


class TestTheIntervalIsTheNumberPlusOrMinusTTimesItsError:

    def test_t_on_one_fewer_degrees_of_freedom_than_the_resamples_counted(self):
        said = _what_the_resamples_said(derived(), correction=2.5)
        t = float(student.ppf(0.975, 35))

        assert said["delta_g_confidence_multiplier"] == pytest.approx(t)
        assert said["delta_g_confidence_degrees_of_freedom"] == 35
        assert said["delta_g_confidence_kjmol"] == pytest.approx(
            [-17.5 - t, -17.5 + t])
        assert said["delta_g_confidence_level"] == 0.95
        assert "Student's t" in said["delta_g_confidence_from"]
        assert said["delta_g_no_interval"] is None

    def test_the_interval_is_centred_on_the_number_not_the_resamples(self):
        said = _what_the_resamples_said(derived(), correction=0.0)
        low, high = said["delta_g_confidence_kjmol"]

        assert (low + high) / 2.0 == pytest.approx(-20.0)

    def test_the_percentiles_are_saved_beside_it_shifted_as_it_is(self):
        said = _what_the_resamples_said(derived(), correction=2.5)

        assert said["delta_g_percentile_interval_kjmol"] == pytest.approx(
            [-19.0, -15.9])

    def test_the_corrections_move_it_without_widening_it(self):
        bare = _what_the_resamples_said(derived(), correction=0.0)
        coned = _what_the_resamples_said(derived(), correction=3.25)

        width = 2.0 * float(student.ppf(0.975, 35)) * 1.0
        for said in (bare, coned):
            low, high = said["delta_g_confidence_kjmol"]
            assert high - low == pytest.approx(width)
        for a, b in zip(bare["delta_g_confidence_kjmol"],
                        coned["delta_g_confidence_kjmol"]):
            assert b == pytest.approx(a + 3.25)
        assert (coned["delta_g_standard_error_kjmol"]
                == bare["delta_g_standard_error_kjmol"])

    def test_without_the_count_of_those_that_gave_a_number_all_are_counted(
            self):
        said = _what_the_resamples_said(
            derived(resamples_with_a_number=None,
                    resamples_without_a_number=None), correction=0.0)

        assert said["delta_g_confidence_degrees_of_freedom"] == 39
        assert said["delta_g_confidence_multiplier"] == pytest.approx(
            float(student.ppf(0.975, 39)))

    def test_without_any_count_the_normal_quantile_is_used_and_said(self):
        said = _what_the_resamples_said(
            derived(resamples=None, resamples_with_a_number=None,
                    resamples_without_a_number=None), correction=0.0)

        assert said["delta_g_confidence_multiplier"] == pytest.approx(
            1.959964, abs=1e-6)
        assert said["delta_g_confidence_degrees_of_freedom"] is None
        assert "normal" in said["delta_g_confidence_from"]

    def test_few_resamples_widen_it(self):
        five = _what_the_resamples_said(
            derived(resamples=5, resamples_with_a_number=5), correction=0.0)
        many = _what_the_resamples_said(
            derived(resamples=500, resamples_with_a_number=500),
            correction=0.0)

        assert five["delta_g_confidence_multiplier"] == pytest.approx(
            float(student.ppf(0.975, 4)))
        assert (five["delta_g_confidence_multiplier"]
                > many["delta_g_confidence_multiplier"] > 1.96)


class TestNoSpreadIsNoInterval:

    def test_one_resample_says_there_is_no_interval(self):
        """`bootstrap_resamples: 1`: the standard error is NaN and the
        percentiles of one value have no width."""
        said = _what_the_resamples_said(
            derived(standard_error=float("nan"), confidence_low=-20.0,
                    confidence_high=-20.0, resamples=1,
                    resamples_with_a_number=1, resamples_without_a_number=0),
            correction=2.5)

        assert said["delta_g_standard_error_kjmol"] is None
        assert said["delta_g_confidence_kjmol"] is None
        assert said["delta_g_percentile_interval_kjmol"] is None
        assert said["delta_g_confidence_multiplier"] is None
        assert said["delta_g_confidence_from"] is None
        assert said["delta_g_no_interval"].startswith(
            "There is no interval: 1 resample gave")

    def test_an_error_saved_as_null_says_there_is_no_interval(self):
        """What `pmf.json` holds after the NaN is written as null."""
        said = _what_the_resamples_said(
            derived(standard_error=None, resamples=1,
                    resamples_with_a_number=1), correction=0.0)

        assert said["delta_g_confidence_kjmol"] is None
        assert "no interval" in said["delta_g_no_interval"]

    def test_resamples_that_all_gave_one_number_say_there_is_no_interval(self):
        said = _what_the_resamples_said(
            derived(standard_error=0.0, confidence_low=-20.0,
                    confidence_high=-20.0), correction=0.0)

        assert said["delta_g_confidence_kjmol"] is None
        assert said["delta_g_standard_error_kjmol"] is None
        assert "the same binding free energy" in said["delta_g_no_interval"]

    def test_resamples_all_the_same_through_the_bootstrap_give_none(self):
        """`block_bootstrap` of resamples that are all one number gives an
        error of rounding (about 1e-15), not 0, as umbrella carries the
        binding free energy: appended to the curve."""
        rng = np.random.default_rng(5)
        frames = {0: np.random.default_rng(1).normal(size=50)}
        for _ in range(20):
            number = float(rng.normal(-10.0, 3.0))
            got = block_bootstrap(
                frames, lambda s, c=number: np.append(np.zeros(62), c),
                resamples=40, seed=3).as_dict()
            record = {key: got[key][-1] for key in (
                "value", "standard_error", "confidence_low",
                "confidence_high")}
            said = _what_the_resamples_said(
                derived(**record, resamples=40, resamples_with_a_number=40,
                        resamples_without_a_number=0), correction=0.0)

            assert said["delta_g_confidence_kjmol"] is None, number
            assert said["delta_g_standard_error_kjmol"] is None, number
            assert said["delta_g_no_interval"] == (
                "There is no interval: every resample gave the same "
                "binding free energy, so there is no spread.")

    def test_a_small_real_spread_still_gives_an_interval(self):
        """A guard, passing before this round too: the rounding tolerance
        must not swallow a real error of 1e-6 kJ/mol."""
        said = _what_the_resamples_said(
            derived(standard_error=1e-6, confidence_low=-20.000002,
                    confidence_high=-19.999998), correction=0.0)

        assert said["delta_g_standard_error_kjmol"] == 1e-6
        assert said["delta_g_confidence_kjmol"] is not None

    def test_a_negative_error_gives_no_interval(self):
        said = _what_the_resamples_said(
            derived(standard_error=-1.0), correction=0.0)

        assert said["delta_g_confidence_kjmol"] is None
        assert said["delta_g_standard_error_kjmol"] is None
        assert "could not be computed" in said["delta_g_no_interval"]

    @pytest.mark.parametrize("given", [0, 1])
    def test_an_error_beside_too_few_resamples_is_not_saved(self, given):
        """The record must not save an error beside a reason that says
        there cannot be one."""
        said = _what_the_resamples_said(
            derived(standard_error=1.0, resamples=given,
                    resamples_with_a_number=given,
                    resamples_without_a_number=0), correction=0.0)

        assert said["delta_g_confidence_kjmol"] is None
        assert said["delta_g_standard_error_kjmol"] is None
        assert "needs at least two" in said["delta_g_no_interval"]

    @pytest.mark.parametrize("error", [None, float("nan"), float("inf")])
    def test_an_error_lost_beside_many_resamples_says_it_was_not_computed(
            self, error):
        """A record with no finite error but 40 resamples that gave a number
        must not say a standard error needs at least two."""
        said = _what_the_resamples_said(
            derived(standard_error=error, resamples=40,
                    resamples_with_a_number=40, resamples_without_a_number=0),
            correction=0.0)

        assert said["delta_g_confidence_kjmol"] is None
        assert said["delta_g_standard_error_kjmol"] is None
        reason = said["delta_g_no_interval"]
        assert "could not be computed" in reason
        assert "needs at least two" not in reason
        assert "40 resamples" in reason

    def test_no_bootstrap_carries_the_same_keys_and_says_why(self):
        """`bootstrap_resamples: 0` passes no resamples at all."""
        said = _what_the_resamples_said(None, correction=2.5)
        full = _what_the_resamples_said(derived(), correction=2.5)

        for key in ("delta_g_confidence_kjmol",
                    "delta_g_confidence_multiplier",
                    "delta_g_confidence_degrees_of_freedom",
                    "delta_g_confidence_from",
                    "delta_g_percentile_interval_kjmol",
                    "delta_g_standard_error_kjmol"):
            assert key in said and said[key] is None
        assert set(full) - set(said) <= {
            "delta_g_uncertainty_note", "delta_g_resamples",
            "delta_g_resamples_without_a_number"}
        assert said["delta_g_confidence_level"] == 0.95
        assert "no bootstrap was run" in said["delta_g_no_interval"]

    def test_a_study_with_no_bootstrap_says_there_is_no_interval(self):
        """Through `binding_free_energy`, as a study with
        `bootstrap_resamples: 0` calls it."""
        result = binding_free_energy(
            CENTRES, well(CENTRES) - 2.0 * KT * np.log(CENTRES),
            temperature_K=TEMPERATURE, judge_the_bulk=False, resampled=None)

        assert result["delta_g_kjmol"] is not None
        assert result["delta_g_confidence_kjmol"] is None
        assert "no bootstrap was run" in result["delta_g_no_interval"]

    def test_a_study_run_with_one_resample_gives_no_interval(self):
        """Through `compute_pmf` and `binding_free_energy`, as a study with
        `bootstrap_resamples: 1` takes them."""
        from fastmdxplora.simulation.umbrella import (
            UmbrellaPlan, Window, compute_pmf)

        rng = np.random.default_rng(3)
        centres = np.round(np.linspace(0.4, 2.0, 24), 4)
        plan = UmbrellaPlan(
            windows=tuple(Window(index=i, centre=float(c),
                                 force_constant=3000.0)
                          for i, c in enumerate(centres)),
            collective_variable="ligand_distance",
            equilibration_fraction=0.2, minimum_overlap=0.03,
            minimum_samples=200)
        grid = np.linspace(0.30, 2.20, 4000)
        profile = (-20.0 * np.exp(-((grid - 0.45) / 0.10) ** 2)
                   - 2.0 * KT * np.log(grid))
        samples = {}
        for window in plan.windows:
            biased = -(profile + 1500.0 * (grid - window.centre) ** 2) / KT
            weight = np.exp(biased - biased.max())
            samples[window.index] = rng.choice(grid, size=3000,
                                               p=weight / weight.sum())

        def uncorrected(where, curve):
            return binding_free_energy(
                where, curve, temperature_K=TEMPERATURE,
                judge_the_bulk=False).get("delta_g_kjmol")

        payload = compute_pmf(samples, plan, temperature_K=TEMPERATURE,
                              bootstrap_resamples=1, also=uncorrected)
        result = binding_free_energy(
            payload["pmf"]["coordinate"], payload["pmf"]["free_energy_kjmol"],
            temperature_K=TEMPERATURE, resampled=payload["derived"])

        assert result["delta_g_kjmol"] is not None
        assert result["delta_g_standard_error_kjmol"] is None
        assert result["delta_g_confidence_kjmol"] is None
        assert "no interval" in result["delta_g_no_interval"]
