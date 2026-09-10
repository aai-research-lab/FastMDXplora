"""A metadynamics surface carried one convergence number for the whole grid.

`compute_surface` compared three quarters of the hills against all of them and
reported the largest movement anywhere it judged. That single figure decides
the verdict and is the right thing to decide it with -- but as a description of
the surface it is the worst point on the grid, which on any run with a real
barrier is the barrier's shoulder. A run converged to a tenth of a kJ/mol across
both its wells and moving by two at the top reported two, and a reader plotting
the wells had nothing to say how firm they were.

The band here is the same measurement made point by point, from several
cumulative cuts through the later deposition rather than one comparison.

**It is a convergence indicator and not a standard error, and the whole of this
file is about keeping those two apart.** The cuts are nested and share one
trajectory: consecutive surfaces agree partly because they are largely the same
sum, and none of them can see a basin the run never entered. Both push the same
way, so the band is a lower bound on run-to-run spread, narrowest exactly where
the sampling was worst. A `±` in a paper does not mean that, so the dictionary
says what it is in a field a script can read and in a sentence a person can,
the exported column repeats it, and the figure legend repeats it again.

The statistical error comes from independent replicas, which a single run's
files cannot supply at any price. Reporting one number and calling it the other
is the defect this avoids; reporting only the one that is computable, and
labelling it, is the honest half of the answer.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from fastmdxplora.simulation.metad_surface import (
    CONVERGENCE_BLOCKS,
    CONVERGENCE_FROM_FRACTION,
    Hills,
    compute_surface,
    compute_surface_2d,
    convergence_band,
    cumulative_surfaces,
    cumulative_surfaces_nd,
    marginal_profile,
    surface_from_hills,
    surface_from_hills_nd,
)

SIGMA = 0.05
GAMMA = 10.0


def _grid(points: int = 200) -> np.ndarray:
    return np.linspace(0.0, 1.0, points)


def _double_well(grid: np.ndarray) -> np.ndarray:
    surface = 20.0 * (1.0 - np.cos(2.0 * np.pi * grid)) / 2.0
    return surface - surface.min()


def _hills(free_energy: np.ndarray, grid: np.ndarray, *, centres: int = 400,
           passes: int = 1, decay: float = 0.0) -> Hills:
    """Hills whose sum reaches the given free energy, deposited in passes.

    One pass sweeps the coordinate once; more passes sweep it repeatedly with
    each pass carrying a share of the bias, which is what a run that keeps
    exploring after its basins are full looks like. `decay` shrinks the later
    passes, as well-tempered deposition does: with it the surface stops
    changing, and without it every pass moves it by the same amount and the
    band stays wide however long the run.
    """
    bias = free_energy.max() - free_energy
    margin = 4.0 * SIGMA
    places = np.linspace(grid[0] - margin, grid[-1] + margin, centres)
    spacing = places[1] - places[0]
    per_pass = (np.interp(places, grid, bias) * spacing
                / (SIGMA * np.sqrt(2.0 * np.pi)))

    weights = np.array([np.exp(-decay * i) for i in range(passes)])
    weights = weights / weights.sum()

    all_places = np.tile(places, passes)
    all_heights = np.concatenate([per_pass * w for w in weights])
    return Hills(
        time_ps=np.arange(all_places.size, dtype=float),
        centre=all_places,
        sigma=np.full(all_places.size, SIGMA),
        height=all_heights,
        bias_factor=GAMMA,
    )


def _written(tmp_path, hills: Hills, name: str = "HILLS"):
    path = tmp_path / name
    path.write_text(
        "#! FIELDS time cv sigma_cv height biasf\n"
        + "\n".join(
            f"{t:.3f} {c:.6f} {s:.6f} {h:.8f} {hills.bias_factor:.1f}"
            for t, c, s, h in zip(hills.time_ps, hills.centre,
                                  hills.sigma, hills.height))
        + "\n",
        encoding="utf-8")
    return path


class TestOnePassGivesTheSameNumbersAsMany:
    """The saving is the only reason `cumulative_surfaces` exists rather than
    a loop over `surface_from_hills(upto=...)`, so it has to return exactly
    what that loop would. A bias is a sum over Gaussians and addition is
    associative; if these ever disagree the accumulation has a bug, not a
    rounding difference."""

    def test_every_cut_matches_summing_that_many_hills_from_scratch(
            self) -> None:
        grid = _grid()
        hills = _hills(_double_well(grid), grid, centres=97, passes=3)

        cuts, surfaces = cumulative_surfaces(hills, grid)

        assert len(cuts) == len(surfaces) == CONVERGENCE_BLOCKS
        for cut, got in zip(cuts, surfaces):
            expected = surface_from_hills(hills, grid, upto=cut)
            assert np.allclose(got, expected, atol=1e-9)

    def test_the_periodic_case_matches_too(self) -> None:
        grid = np.linspace(-np.pi, np.pi, 120)
        free = 15.0 * (1.0 - np.cos(2.0 * grid)) / 2.0
        hills = _hills(free, grid, centres=80, passes=2)

        cuts, surfaces = cumulative_surfaces(hills, grid, periodic=True)

        for cut, got in zip(cuts, surfaces):
            expected = surface_from_hills(hills, grid, upto=cut, periodic=True)
            assert np.allclose(got, expected, atol=1e-9)

    def test_two_dimensions_match_as_well(self) -> None:
        n = 60
        rng = np.random.default_rng(4)
        centres = rng.uniform(-1.0, 1.0, size=(n, 2))
        hills = Hills(
            time_ps=np.arange(n, dtype=float),
            centre=centres,
            sigma=np.full((n, 2), 0.25),
            height=np.full(n, 0.4),
            bias_factor=GAMMA,
        )
        axes = (np.linspace(-1.2, 1.2, 21), np.linspace(-1.2, 1.2, 17))

        cuts, surfaces = cumulative_surfaces_nd(hills, axes)

        for cut, got in zip(cuts, surfaces):
            expected = surface_from_hills_nd(hills, axes, upto=cut)
            assert got.shape == (21, 17)
            assert np.allclose(got, expected, atol=1e-9)


class TestTheCutsAreDistinctSurfaces:
    """Five evenly spaced fractions of a short run land on repeated hill
    counts. Keeping the repeat puts one surface into the spread twice, and
    the band then reads that surface's perfect agreement with itself as
    evidence of convergence."""

    def test_a_short_run_does_not_count_a_surface_twice(self) -> None:
        grid = _grid(40)
        hills = _hills(_double_well(grid), grid, centres=9)

        cuts, surfaces = cumulative_surfaces(hills, grid)

        assert len(cuts) == len(set(cuts))
        assert len(surfaces) == len(cuts)
        assert cuts[-1] == len(hills)

    def test_the_first_cut_is_where_the_fraction_says(self) -> None:
        grid = _grid(40)
        hills = _hills(_double_well(grid), grid, centres=200)

        cuts, _ = cumulative_surfaces(hills, grid)

        assert cuts[0] == pytest.approx(
            CONVERGENCE_FROM_FRACTION * len(hills), abs=1)

    def test_a_single_hill_says_so_instead_of_returning_a_number(self) -> None:
        grid = _grid(20)
        one = Hills(time_ps=np.zeros(1), centre=np.array([0.5]),
                    sigma=np.array([SIGMA]), height=np.array([1.0]),
                    bias_factor=GAMMA)

        cuts, surfaces = cumulative_surfaces(one, grid)
        band = convergence_band(surfaces, hills_at=cuts)

        assert (cuts, surfaces) == ([], [])
        assert band["band_kjmol"] is None
        assert band["typical_kjmol"] is None
        assert "too short" in band["note"].lower()
        # Still false. An absent band is not licence to read the silence as
        # an error bar of zero.
        assert band["is_a_standard_error"] is False


class TestTheBandMeasuresWhetherTheBiasStoppedMoving:

    def test_a_converged_run_gives_a_narrow_band(self) -> None:
        """Deposition that decays: the late passes barely change the surface,
        which is what a well-tempered run approaching convergence does."""
        grid = _grid()
        hills = _hills(_double_well(grid), grid, centres=200, passes=8,
                       decay=1.2)

        cuts, surfaces = cumulative_surfaces(hills, grid)
        band = convergence_band(surfaces, hills_at=cuts)

        assert band["typical_kjmol"] < 0.5

    def test_a_run_still_filling_gives_a_wide_one(self) -> None:
        """Equal passes: every cut adds as much bias as the last, so the
        surface is still being built and says so."""
        grid = _grid()
        hills = _hills(_double_well(grid), grid, centres=200, passes=8,
                       decay=0.0)

        cuts, surfaces = cumulative_surfaces(hills, grid)
        band = convergence_band(surfaces, hills_at=cuts)

        assert band["typical_kjmol"] > 1.0

    def test_it_is_wider_where_the_surface_is_less_converged(self) -> None:
        """The whole reason for a band rather than a number. A surface whose
        right half keeps deepening while its left half is finished should say
        so at each point, not report the right half everywhere."""
        grid = _grid()
        # First half of the run: deposition everywhere, decaying, so both
        # halves of the surface are built and then stop moving.
        finished = _hills(_double_well(grid), grid, centres=100, passes=4,
                          decay=2.0)
        # Second half: the coordinate only ever revisits the right, so hills
        # keep arriving there at a steady height and nowhere else. Built
        # directly rather than through `_hills`, which places hills where the
        # free energy is *low* -- exactly the inverse of the region wanted.
        places = np.tile(np.linspace(0.7, 1.05, 100), 4)
        both = Hills(
            time_ps=np.arange(len(finished) + places.size, dtype=float),
            centre=np.concatenate([finished.centre, places]),
            sigma=np.concatenate([finished.sigma,
                                  np.full(places.size, SIGMA)]),
            height=np.concatenate([finished.height,
                                   np.full(places.size, 0.08)]),
            bias_factor=GAMMA,
        )

        cuts, surfaces = cumulative_surfaces(both, grid)
        band = np.asarray(
            convergence_band(surfaces, hills_at=cuts)["band_kjmol"])

        quiet = band[grid < 0.3].mean()
        busy = band[grid > 0.75].mean()
        assert busy > 3.0 * quiet

    def test_it_reports_one_number_per_grid_point(self) -> None:
        grid = _grid(137)
        hills = _hills(_double_well(grid), grid, centres=200, passes=3,
                       decay=0.5)

        cuts, surfaces = cumulative_surfaces(hills, grid)
        band = convergence_band(surfaces, hills_at=cuts)

        assert len(band["band_kjmol"]) == 137
        assert band["blocks"] == len(cuts)
        assert band["hills_at"] == list(cuts)


class TestSurfacesAreAlignedBeforeTheyAreCompared:
    """A free energy has no absolute zero, so a constant offset between two
    cuts is not a disagreement and has to come out before they are compared.
    Which statistic does the aligning is the decision, and all three
    candidates fail differently: the minimum ties every point to the single
    lowest one, the mean shares a local change out over the whole grid, and
    the median needs more than half the grid to have converged. The last is the
    only one whose failure mode is the case the drift check has already
    caught."""

    def test_a_constant_offset_is_not_a_disagreement(self) -> None:
        shape = np.array([0.0, 3.0, 9.0, 3.0, 0.0])
        band = convergence_band([shape + k for k in (0.0, 5.0, -2.0, 11.0)])

        assert band["largest_kjmol"] == pytest.approx(0.0, abs=1e-9)

    def test_a_minimum_that_hops_one_bin_is_not_the_whole_surface_moving(
            self) -> None:
        first = np.array([1.0, 0.0, 4.0, 9.0, 4.0, 0.0, 1.0])
        # Identical everywhere but one point, which dips 3 kJ/mol lower and
        # becomes the new minimum. Aligned on the minimum, every one of the
        # seven points would look 3 kJ/mol out of place.
        second = first.copy()
        second[-2] -= 3.0

        band = np.asarray(
            convergence_band([first, second, second, second])["band_kjmol"])

        assert band[-2] > 1.0
        assert band[0] < 0.6

    def test_a_change_in_one_region_is_not_shared_out_over_the_rest(
            self) -> None:
        """Aligned on the mean, a third of the grid deepening by 3 kJ/mol
        lifts the converged two thirds by 1, and the wells acquire a band they
        did not earn -- which is the failure the band exists to avoid, since
        the whole point of measuring per point is to say the wells are firm.
        """
        converged = np.linspace(0.0, 6.0, 30)
        cuts = []
        for k in range(5):
            cut = converged.copy()
            cut[20:] -= 3.0 * k / 4.0
            cuts.append(cut)

        band = np.asarray(convergence_band(cuts)["band_kjmol"])

        assert band[:20].max() < 1e-9
        assert band[20:].min() > 1.0

    def test_the_alignment_uses_only_the_judged_region(self) -> None:
        """A point outside the judged region is allowed to move without
        dragging the alignment of everything inside it."""
        base = np.array([0.0, 1.0, 2.0, 1.0, 0.0, 90.0])
        judged = np.array([True, True, True, True, True, False])
        wild = [base.copy() for _ in range(4)]
        for k, surface in enumerate(wild):
            surface[-1] += 40.0 * k

        band = np.asarray(
            convergence_band(wild, judged=judged)["band_kjmol"])

        assert np.all(band[:5] < 1e-9)


class TestItIsNeverSoldAsAStandardError:
    """The failure this file guards against is not a wrong number. It is a
    right number read as a different quantity, which is what happens to any
    band drawn round a free energy unless every place it appears says
    otherwise."""

    def _band(self) -> dict:
        grid = _grid()
        hills = _hills(_double_well(grid), grid, centres=200, passes=4,
                       decay=1.0)
        cuts, surfaces = cumulative_surfaces(hills, grid)
        return convergence_band(surfaces, hills_at=cuts)

    def test_a_field_says_it_is_not_one(self) -> None:
        """Readable without parsing English, so a report generator can refuse
        to print it after a plus-or-minus."""
        assert self._band()["is_a_standard_error"] is False

    def test_no_key_is_named_as_though_it_were_one(self) -> None:
        band = self._band()
        for key in band:
            if any(word in key for word in
                   ("error", "uncertain", "sigma", "stderr", "std")):
                assert band[key] is False, (
                    f"{key!r} reads as a statistical error; the only key here "
                    "allowed to mention one is the flag denying it")

    def test_the_note_says_what_it_is_and_what_it_is_not(self) -> None:
        note = self._band()["note"].lower()
        assert "not a standard error" in note
        assert "nested" in note

    def test_the_note_names_replicas_as_the_statistical_route(self) -> None:
        """Saying what it isn't is half an answer. A reader needs to be told
        where the other number comes from, because it is not in these files
        and no amount of work on this run will produce it."""
        assert "replicas" in self._band()["note"].lower()

    def test_the_module_says_the_band_is_a_lower_bound(self) -> None:
        from fastmdxplora.simulation import metad_surface

        text = metad_surface.convergence_band.__doc__ or ""
        assert "lower bound" in text


class TestItReachesTheRecordTheRunWrites:

    def _converged(self, tmp_path):
        grid = _grid()
        hills = _hills(_double_well(grid), grid, centres=300, passes=6,
                       decay=1.5)
        return _written(tmp_path, hills), grid

    def test_the_one_dimensional_evidence_carries_it(self, tmp_path) -> None:
        path, grid = self._converged(tmp_path)
        sampled = np.tile(np.linspace(0.0, 1.0, 50), 20)

        outcome = compute_surface(path, sampled, points=len(grid))

        band = outcome["evidence"]["convergence"]
        assert len(band["band_kjmol"]) == len(grid)
        assert band["is_a_standard_error"] is False

    def test_the_evidence_still_serialises(self, tmp_path) -> None:
        """The simulation phase writes `evidence` whole into
        metadynamics_surface.json. A numpy array in there would raise at the
        end of a run that had already finished simulating."""
        path, grid = self._converged(tmp_path)

        outcome = compute_surface(path, np.linspace(0.0, 1.0, 100))

        json.dumps(outcome["evidence"])

    def test_the_verdict_is_unchanged_by_it(self, tmp_path) -> None:
        """Evidence, not a gate. The band is reported and never consulted:
        a threshold on it would be a second convergence criterion nobody
        pre-registered, and the drift check already decides."""
        path, _ = self._converged(tmp_path)

        outcome = compute_surface(path, np.linspace(0.0, 1.0, 100))

        refusal = outcome.get("refused") or ""
        assert "band" not in refusal
        assert "convergence band" not in refusal

    def test_two_dimensions_report_one_band_per_variable(self,
                                                         tmp_path) -> None:
        n = 400
        rng = np.random.default_rng(11)
        centres = rng.uniform(-1.0, 1.0, size=(n, 2))
        path = tmp_path / "HILLS"
        path.write_text(
            "#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 height biasf\n"
            + "\n".join(
                f"{i:.3f} {a:.6f} {b:.6f} 0.250000 0.250000 "
                f"{0.5 * np.exp(-3.0 * i / n):.8f} 10.0"
                for i, (a, b) in enumerate(centres))
            + "\n", encoding="utf-8")

        outcome = compute_surface_2d(
            path, rng.uniform(-1.0, 1.0, size=(500, 2)), points=24)

        per_dimension = outcome["evidence"]["per_dimension"]
        assert len(per_dimension) == 2
        for record in per_dimension:
            assert len(record["convergence"]["band_kjmol"]) == 24
            assert record["convergence"]["is_a_standard_error"] is False

    def test_the_two_dimensional_surface_band_is_summary_only(
            self, tmp_path) -> None:
        """Twenty-four squared is 576 numbers and an 80-point default is
        6,400. The per-dimension bands are what anything plots against; the
        whole-surface figure is two scalars, because a surface can be
        converged along both marginals while a corner of it is not."""
        n = 300
        rng = np.random.default_rng(12)
        centres = rng.uniform(-1.0, 1.0, size=(n, 2))
        path = tmp_path / "HILLS"
        path.write_text(
            "#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 height biasf\n"
            + "\n".join(
                f"{i:.3f} {a:.6f} {b:.6f} 0.250000 0.250000 0.30000000 10.0"
                for i, (a, b) in enumerate(centres))
            + "\n", encoding="utf-8")

        outcome = compute_surface_2d(path, points=16)

        whole = outcome["evidence"]["convergence"]
        assert "band_kjmol" not in whole
        assert whole["typical_kjmol"] is not None
        assert whole["is_a_standard_error"] is False
        json.dumps(outcome["evidence"])

    def test_the_marginal_is_integrated_per_cut_not_averaged(self) -> None:
        """`marginal_profile` is a log of a sum of exponentials, so the
        marginal of an averaged surface is not the average of the marginals.
        The width of a channel is part of the answer, and averaging surfaces
        first mixes a broad shallow one with a narrow deep one and reports
        neither. Marginalising after combining would quietly report a
        different quantity."""
        first = np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], dtype=float)
        second = np.array([[0.0, 20.0, 20.0], [0.0, 0.0, 0.0]], dtype=float)

        combined = marginal_profile((first + second) / 2.0, 0)
        averaged = (marginal_profile(first, 0)
                    + marginal_profile(second, 0)) / 2.0

        assert not np.allclose(combined, averaged, atol=0.5)
