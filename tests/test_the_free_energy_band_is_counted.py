"""How often the band on a WHAM free energy holds the true profile.

Both recombination tests set ``bootstrap_resamples=0``, so the band had
never been checked. `validation.pmf_coverage` counts it on synthetic
umbrella sets with a known double well. These check that its sets are what
it says (each window's samples exact and correlated, its truth what WHAM
finds with plenty of sampling) and run it on a few sets at the package's
200 resamples.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
import pytest
from scipy.stats import kstest

from fastmdxplora.simulation.umbrella import compute_pmf
from fastmdxplora.statistics import statistical_inefficiency
from fastmdxplora.uncertainty import DEFAULT_RESAMPLES
from fastmdxplora.validation import pmf_coverage


def test_each_window_samples_its_own_biased_distribution_correlated():
    profile, design = pmf_coverage.Profile(), pmf_coverage.Design(samples=20000)
    plan = pmf_coverage._plan(design)
    sampled = pmf_coverage._window_samples(profile, design, plan, np.random.default_rng(3))
    kT = pmf_coverage._kT(design)
    grid = np.linspace(profile.low, profile.high, 200001)
    for window in (plan.windows[0], plan.windows[5], plan.windows[-1]):
        density = np.exp(-(profile.energy(grid) + 0.5 * window.force_constant
                           * (grid - window.centre) ** 2) / kT)
        cdf = np.cumsum(density) / density.sum()
        values = sampled[window.index]
        # Thinned well past the correlation time, so the test's own
        # assumption of independent draws holds.
        assert kstest(values[::100], lambda x, cdf=cdf: np.interp(x, grid, cdf)).pvalue > 0.01
        g = statistical_inefficiency(values)
        assert 12.0 < g < 30.0, g


def test_the_truth_is_what_wham_finds_with_plenty_of_sampling():
    profile = pmf_coverage.Profile()
    design = pmf_coverage.Design(samples=100000, phi=0.0)
    plan = pmf_coverage._plan(design)
    sampled = pmf_coverage._window_samples(profile, design, plan, np.random.default_rng(5))
    payload = compute_pmf(sampled, plan, temperature_K=design.temperature_K,
                          bins=design.bins, bootstrap_resamples=0)
    coordinate = np.asarray(payload["pmf"]["coordinate"])
    found = np.array([np.nan if v is None else v
                      for v in payload["pmf"]["free_energy_kjmol"]])
    truth = pmf_coverage.true_profile(profile, design, coordinate)
    between = (coordinate >= design.first) & (coordinate <= design.last)
    offset = np.nanmean((found - truth)[between])
    assert np.nanmax(np.abs(found - truth - offset)[between]) < 0.15
    # The barrier between the wells is the profile's.
    assert truth.max() > 0 and truth[np.argmin(np.abs(coordinate - 1.5))] == pytest.approx(
        profile.barrier_kjmol, abs=0.2)


@pytest.mark.slow
def test_the_band_is_counted_at_the_packages_resamples(tmp_path, capsys):
    assert DEFAULT_RESAMPLES == 200
    out = tmp_path / "coverage.json"
    assert pmf_coverage.main(["--sets", "4", "--resamples", "200",
                              "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "the band's stated level is 95%" in said and "| 200 |" in said
    row = json.loads(out.read_text())["by_resamples"]["200"]
    assert row["bins_with_a_band"] > 4 * 50
    for key in ("coverage", "coverage_between_centres",
                "coverage_1_96_se_between_centres"):
        assert 0.0 <= row[key] <= 1.0
        assert 0.0 <= row["mean_gauge"][key if key != "coverage" else
                                        "coverage_between_centres"] <= 1.0
    assert 0.0 < row["coverage_interval_half_width"] < 1.0
    assert len(row["per_bin_coverage"]) == 60
    assert math.isfinite(row["stated_over_observed_spread"])


def test_too_few_sets_are_said(capsys):
    assert pmf_coverage.main(["--sets", "1"]) == 2
    assert "At least two sets" in capsys.readouterr().err


def test_the_count_comes_out_the_same_again():
    # Fixed seeds: the paper's numbers can be had again exactly.
    first = pmf_coverage.coverage(2, (20,), first_seed=7)
    again = pmf_coverage.coverage(2, (20,), first_seed=7)
    assert first["by_resamples"] == again["by_resamples"]
    assert first["seeds"] == [7, 8]


def test_the_reported_gauge_is_the_band_compute_pmf_reports():
    profile, design = pmf_coverage.Profile(), pmf_coverage.Design()
    plan = pmf_coverage._plan(design)
    sampled = pmf_coverage._window_samples(profile, design, plan,
                                           np.random.default_rng(1000))
    logging.disable(logging.WARNING)
    try:
        pmf = compute_pmf(sampled, plan, temperature_K=design.temperature_K,
                          bins=design.bins, bootstrap_resamples=20,
                          bootstrap_seed=1000)["pmf"]
    finally:
        logging.disable(logging.NOTSET)
    band = pmf["uncertainty"]
    one = pmf_coverage._one_set(profile, design, 1000, [20])
    read = one["20"]
    assert read["lowest_bin"]["error"] == band["standard_error"]
    assert read["block_length"] == band["block_length"]
    # The band's edges and the curve, the values coverage is read from.
    truth = pmf_coverage.true_profile(profile, design, one["coordinate"])
    curve = np.array([np.nan if v is None else v for v in pmf["free_energy_kjmol"]])
    low = np.array(band["confidence_low"], dtype=float)
    high = np.array(band["confidence_high"], dtype=float)
    assert one["coordinate"] == pmf["coordinate"]
    assert read["lowest_bin"]["deviation"] == [float(v) for v in curve - truth]
    assert read["lowest_bin"]["inside"] == ((truth >= low) & (truth <= high)).tolist()
    # Both edges themselves, so a shift in either is caught here and not
    # only by the count.
    assert read["lowest_bin"]["low"] == band["confidence_low"]
    assert read["lowest_bin"]["high"] == band["confidence_high"]
    # The second gauge is from the same resamples, re-zeroed: its errors
    # differ, its curve sits on the truth.
    assert read["mean_between_centres"]["error"] != band["standard_error"]


def test_a_small_count_at_fixed_seeds_is_held():
    # A guard: a change to WHAM, the bootstrap or the sets moves these.
    # Exact counts: on another numpy a percentile tie at a band's edge can
    # flip one bin, and the count is then one off without anything wrong.
    row = pmf_coverage.coverage(4, (20,))["by_resamples"]["20"]
    assert row["bins_with_a_band"] == 240
    assert row["coverage"] == pytest.approx(177 / 240, abs=1e-12)
    assert row["coverage_between_centres"] == pytest.approx(164 / 222, abs=1e-12)
    assert row["coverage_1_96_se_between_centres"] == pytest.approx(198 / 222, abs=1e-12)
    assert row["mean_gauge"]["coverage_between_centres"] == pytest.approx(
        187 / 222, abs=1e-12)
    assert row["mean_gauge"]["coverage_1_96_se_between_centres"] == pytest.approx(
        199 / 222, abs=1e-12)
    assert row["curve_offset_between_centres"] == pytest.approx(0.45581, abs=1e-4)
    assert row["band_offset_between_centres"] == pytest.approx(0.57685, abs=1e-4)
    assert abs(row["mean_gauge"]["band_offset_between_centres"]) < 0.01


def test_the_block_factor_is_put_back(capsys):
    from fastmdxplora import uncertainty

    result = pmf_coverage.coverage(2, (4,), block_factor=4)
    assert result["block_factor"] == 4
    assert uncertainty.BLOCKS_PER_CORRELATION_TIME == 2
    # It took effect: the same sets' blocks are longer than at the package's.
    package = pmf_coverage.coverage(2, (4,))
    assert package["block_factor"] == 2
    assert (min(result["by_resamples"]["4"]["block_lengths"])
            > max(package["by_resamples"]["4"]["block_lengths"]))
    assert pmf_coverage.main(["--sets", "2", "--block-factor", "0"]) == 2
