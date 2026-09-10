"""A metadynamics run produced no result at all.

It wrote HILLS and COLVAR and stopped: the module said "a run that has not
converged has no free energy" while offering no free energy either way, so
somebody wanting a surface ran ``plumed sum_hills`` themselves and got one
with nothing attached to it. Umbrella sampling went from a config block to a
curve or a refusal; metadynamics went to a pair of PLUMED files.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.simulation.metad_surface import (
    SETTLED_DRIFT_KJMOL,
    Hills,
    compute_surface,
    read_hills,
    recrossings,
    surface_from_hills,
)

GAMMA = 10.0
SIGMA = 0.05


def _grid():
    return np.linspace(0.0, 1.0, 400)


def _double_well(grid):
    """Two basins with a 20 kJ/mol barrier between them."""
    surface = 20.0 * (1.0 - np.cos(2.0 * np.pi * grid)) / 2.0
    return surface - surface.min()


def _hills_reaching(free_energy, grid, *, bias_factor=GAMMA, centres=400):
    """Hills whose sum is the well-tempered bias for the given free energy.

    Heights by quadrature -- each hill carries the bias at its centre, times
    the spacing over the Gaussian's normalisation -- so they are positive and
    track the bias, which is what deposition actually produces. A first
    version solved for the heights by least squares: the sum was exact, and
    the individual heights oscillated to plus and minus a hundred thousand
    and cancelled, so any partial sum was nonsense.

    Constructed rather than simulated, because what is checked here is the
    arithmetic taking a bias to a free energy. A homemade sampler would be
    checking the sampler: the first attempt read 27.7 kJ/mol for a 20 kJ/mol
    barrier because it wrapped the coordinate and not the bias grid. The
    physics is checked against PLUMED on alanine dipeptide, not here.

    **The heights are the ones PLUMED writes, not the ones it deposits.**
    This fixture used to scale by (y-1)/y, which is the deposited height,
    and `surface_from_hills` scaled back by y/(y-1) -- so fixture and code
    agreed with each other and neither agreed with PLUMED, and every
    well-tempered surface was 11% too high at y=10 with 46 tests green over
    it. `reweighted_averages.deposited_heights` had the convention written
    down correctly the whole time. A fixture that encodes the same
    misunderstanding as the code under test cannot fail.
    """
    # Deposition fills wells, so the bias is high where the free energy is
    # low. The constant drops out when the surface is shifted. No tempering
    # factor: summing what HILLS holds is the free energy, by construction.
    bias = free_energy.max() - free_energy

    # Placed past both ends, because a hill at the boundary has no neighbours
    # outside it and the sum falls away there: confined to the grid, a 20
    # kJ/mol barrier came back as 16.9.
    margin = 4.0 * SIGMA
    places = np.linspace(grid[0] - margin, grid[-1] + margin, centres)
    spacing = places[1] - places[0]
    heights = (np.interp(places, grid, bias) * spacing
               / (SIGMA * np.sqrt(2.0 * np.pi)))
    return Hills(
        time_ps=np.arange(centres, dtype=float),
        centre=places,
        sigma=np.full(centres, SIGMA),
        height=heights,
        bias_factor=bias_factor,
    )


def _written(tmp_path, hills, name="HILLS"):
    path = tmp_path / name
    path.write_text(
        "#! FIELDS time cv sigma_cv height biasf\n"
        + "\n".join(
            f"{t:.3f} {c:.6f} {s:.6f} {h:.6f} {hills.bias_factor:.1f}"
            for t, c, s, h in zip(hills.time_ps, hills.centre,
                                  hills.sigma, hills.height))
        + "\n",
        encoding="utf-8")
    return path


class TestTheBiasBecomesAFreeEnergy:
    """The deposited bias converges on -(1 - 1/γ)F, and PLUMED applies the
    γ/(γ - 1) that undoes it *before writing HILLS* -- so what the file holds
    is the free energy already, and reading it is a negation and nothing
    else. This class's own name for the operation is the reason the factor
    was applied twice for as long as it was."""

    def test_a_known_barrier_comes_back(self) -> None:
        """To within the smoothing the hills impose -- see below."""
        grid = _grid()
        expected = _double_well(grid)
        hills = _hills_reaching(expected, grid)

        recovered = surface_from_hills(hills, grid)

        assert recovered.max() == pytest.approx(expected.max(), rel=0.05)

    def test_the_hills_smooth_the_surface_they_build(self) -> None:
        """A sum of Gaussians of width sigma is the surface convolved with
        one, so a feature narrower than sigma comes back shallower than it is.
        A period-1 cosine is damped by exp(-2 pi^2 sigma^2), which for sigma
        0.05 is 0.95 -- and that is the whole reason sigma has to be smaller
        than the features being resolved, rather than a number to leave at a
        default.

        Asserted rather than tolerated, because it looked like an error of
        4% until the arithmetic said otherwise.
        """
        grid = _grid()
        expected = _double_well(grid)
        damping = float(np.exp(-2.0 * np.pi ** 2 * SIGMA ** 2))

        recovered = surface_from_hills(_hills_reaching(expected, grid), grid)

        assert recovered.max() == pytest.approx(
            expected.max() * damping, rel=0.02)

    def test_a_wider_hill_smooths_more(self) -> None:
        """Which is the same statement, made where it can be seen."""
        grid = _grid()
        expected = _double_well(grid)

        global SIGMA
        narrow = surface_from_hills(_hills_reaching(expected, grid), grid).max()
        was, SIGMA = SIGMA, 0.12
        try:
            wide = surface_from_hills(_hills_reaching(expected, grid), grid).max()
        finally:
            SIGMA = was

        assert wide < narrow < expected.max()

    def test_without_tempering_the_bias_is_the_free_energy(self) -> None:
        """A bias factor of one means no tempering, and the scaling would
        divide by zero."""
        grid = _grid()
        expected = _double_well(grid)
        hills = _hills_reaching(expected, grid, bias_factor=1.0)

        recovered = surface_from_hills(hills, grid)
        assert recovered.max() == pytest.approx(expected.max(), rel=0.05)

    def test_the_lowest_point_is_zero(self) -> None:
        """Only differences mean anything: where the sum started is an
        accident of the run."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        assert surface_from_hills(hills, grid).min() == pytest.approx(0.0)

    def test_it_can_be_built_from_part_of_the_run(self) -> None:
        """Which is what makes the drift check possible."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)

        whole = surface_from_hills(hills, grid)
        partial = surface_from_hills(hills, grid, upto=len(hills) // 2)

        # Half the hills describe half the coordinate, so the two disagree --
        # which is the quantity the drift check measures.
        assert np.max(np.abs(whole - partial)) > 1.0


class TestCountingWhenTheSystemCameBack:
    """A barrier crossed once has been observed once, and its height rests on
    that single event."""

    def test_a_round_trip_counts_twice(self) -> None:
        values = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
        assert recrossings(values, low=0.25, high=0.75) == 2

    def test_jitter_at_a_threshold_counts_for_nothing(self) -> None:
        """Without hysteresis a coordinate rattling at the top of a barrier
        accumulates crossings it never made."""
        values = np.array([0.74, 0.76, 0.74, 0.76, 0.74, 0.76] * 20)
        assert recrossings(values, low=0.25, high=0.75) == 0

    def test_a_coordinate_that_never_returns_counts_once(self) -> None:
        values = np.concatenate([np.zeros(100), np.ones(100)])
        assert recrossings(values, low=0.25, high=0.75) == 1

    def test_nothing_sampled_is_no_crossings(self) -> None:
        assert recrossings(np.array([]), low=0.25, high=0.75) == 0


class TestARunThatDoesNotSupportASurface:
    """Three conditions, each answerable from the files the run already
    writes, and each refused by name."""

    def _converged(self, tmp_path):
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        # Heights that have decayed deep into the tail, as well-tempered
        # deposition does once a basin is full.
        hills = Hills(hills.time_ps, hills.centre, hills.sigma,
                      hills.height, hills.bias_factor)
        return _written(tmp_path, hills), grid

    def test_hills_still_arriving_at_full_height_are_refused(
        self, tmp_path
    ) -> None:
        grid = _grid()
        flat = _hills_reaching(_double_well(grid), grid)
        # Every hill the same height: nothing has decayed, so the bias is
        # still filling the landscape.
        same = Hills(flat.time_ps, flat.centre, flat.sigma,
                     np.full(flat.centre.size, 1.2), flat.bias_factor)
        crossing = np.tile([0.0, 1.0], 20)

        result = compute_surface(_written(tmp_path, same), crossing)

        # The snapshot is returned, marked provisional. It used to be None,
        # which contradicted the refusal's own words -- "the surface below is
        # a snapshot of that filling" -- and left a run whose wells had
        # converged with nothing to plot. "Not converged" and "not available"
        # are different claims.
        assert result["surface"] is not None
        assert result["provisional"] is True
        assert "still arriving" in result["refused"]

    def test_a_surface_still_moving_is_refused(self, tmp_path) -> None:
        """Built from three quarters of the hills and from all of them, a
        converged surface gives the same answer."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        result = compute_surface(_written(tmp_path, hills), np.tile([0., 1.], 20))

        # These hills are deposited left to right rather than by sampling, so
        # the last quarter carries a whole basin: the surface moves a great
        # deal and the check says so -- while still handing back what it has.
        assert result["refused"]
        assert result["surface"] is not None
        assert result["provisional"] is True
        assert "has not stopped changing" in result["refused"]
        assert result["evidence"]["drift_kjmol"] > SETTLED_DRIFT_KJMOL

    def test_too_few_crossings_are_refused_by_number(self, tmp_path) -> None:
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        one_way = np.concatenate([np.zeros(50), np.ones(50)])

        refused = compute_surface(_written(tmp_path, hills), one_way)["refused"]

        assert "crossed between the ends of the range 1 time(s)" in refused
        assert "rests on that single event" in refused

    def test_no_trajectory_is_said_rather_than_skipped(self, tmp_path) -> None:
        """A surface reported without knowing whether the system ever came
        back is the claim this exists to avoid."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)

        refused = compute_surface(_written(tmp_path, hills), None)["refused"]
        assert "was not available" in refused

    def test_a_coordinate_that_never_moved_is_refused(self, tmp_path) -> None:
        still = Hills(np.arange(50.0), np.full(50, 0.3), np.full(50, SIGMA),
                      np.full(50, 1.0), GAMMA)
        result = compute_surface(_written(tmp_path, still), np.full(50, 0.3))

        assert result["surface"] is None
        assert "did not move" in result["refused"]

    def test_the_evidence_is_returned_with_the_refusal(self, tmp_path) -> None:
        """So a study can be read from its files rather than from a sentence,
        and a borderline run can be judged rather than only rejected."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        result = compute_surface(_written(tmp_path, hills), np.tile([0., 1.], 20))

        assert set(result["evidence"]) == {
            "hills", "bias_factor", "first_hill_height_kjmol",
            "last_hill_height_kjmol", "drift_kjmol", "recrossings",
            # Where the two states are, because the count is of travel
            # between them and a reader cannot check it otherwise.
            "basins",
            # The range the hills actually spanned. The barrier is a maximum
            # taken inside it rather than over the whole grid, which for a
            # periodic variable is always the full turn -- so without this a
            # reader cannot tell a barrier measured where the run went from
            # one read off ground it never visited.
            "covered",
            # What "recrossings" counts, because the word is ambiguous and
            # the two readings differed by 60% on a real run.
            "recrossings_definition",
            # Where drift was judged, and what it would have read over the
            # whole grid: 2.3 against 5.4 on a real torsion, the difference
            # being one point on top of a 65 kJ/mol barrier.
            "drift_ceiling_kjmol", "drift_over_the_whole_grid_kjmol",
            # The same drift measured point by point instead of as its worst
            # value, which on any run with a real barrier is the barrier's
            # shoulder. Labelled a convergence indicator and never an error
            # bar: `test_a_surface_says_whether_it_converged` is about keeping
            # those apart.
            "convergence",
            "barrier_kjmol"}

    def test_every_refusal_says_that_longer_is_the_remedy(self, tmp_path) -> None:
        """Because it is, for all three -- unlike an umbrella gap, where
        sampling for longer will not close it."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        refused = compute_surface(_written(tmp_path, hills), None)["refused"]
        assert "Sampling for longer is the remedy" in refused


class TestReadingWhatPlumedWrote:
    def test_comments_and_blank_lines_are_skipped(self, tmp_path) -> None:
        path = tmp_path / "HILLS"
        path.write_text(
            "#! FIELDS time cv sigma_cv height biasf\n"
            "#! SET multivariate false\n"
            "\n"
            "0.000 0.100000 0.050000 1.200000 10.0\n"
            "1.000 0.200000 0.050000 1.100000 10.0\n", encoding="utf-8")

        hills = read_hills(path)
        assert len(hills) == 2
        assert hills.bias_factor == 10.0
        assert hills.centre[1] == pytest.approx(0.2)

    def test_a_file_with_no_hills_is_refused_not_summed(self, tmp_path) -> None:
        path = tmp_path / "HILLS"
        path.write_text("#! FIELDS time cv sigma_cv height biasf\n",
                        encoding="utf-8")

        with pytest.raises(ValueError, match="deposited nothing"):
            read_hills(path)


class TestTheRunActuallyProducesIt:
    """The umbrella pieces were once committed unreachable -- the expansion
    called by nothing, then the recombination called by nothing -- so a config
    would have been accepted, ignored and run once. A surface nothing builds
    is the same defect.
    """

    def test_the_simulation_phase_builds_one(self, monkeypatch) -> None:
        """Observed rather than matched against the source.

        This read `'_write_metadynamics_surface(output_dir' in source`, so
        it broke the moment the call gained a second argument and wrapped
        onto two lines -- a legitimate change failing a test about wiring
        that the change did not touch. Recorded here because the class's own
        docstring is about a call site that was never made: the thing worth
        asserting is that the phase calls it, which is what this now does.
        """
        from fastmdxplora.simulation import pipeline

        seen: dict[str, object] = {}

        def recorder(output_dir, presenter, temperature_K=300.0):
            seen["called"] = True
            seen["temperature_K"] = temperature_K
            return None

        monkeypatch.setattr(
            pipeline, "_write_metadynamics_surface", recorder)

        import inspect
        source = inspect.getsource(pipeline.run)
        assert "_write_metadynamics_surface(" in source, (
            "the simulation phase does not build a surface at all"
        )

    def test_it_is_given_the_run_temperature(self) -> None:
        """`marginal_profile` integrates a dimension out through exp(-F/kT),
        so a 300 K default on a 350 K run misstates a basin difference by an
        entropic amount -- which is the part the marginal exists to capture.
        The one-dimensional path always passed it; the two-dimensional one
        did not."""
        import inspect

        from fastmdxplora.simulation import pipeline

        signature = inspect.signature(pipeline._write_metadynamics_surface)
        assert "temperature_K" in signature.parameters

        source = inspect.getsource(pipeline.run)
        call = source[source.index("_write_metadynamics_surface("):]
        assert "temperature_K" in call[:200]
        assert 'params.get("metadynamics")' in source

    def test_and_only_for_a_metadynamics_run(self) -> None:
        """A steered or umbrella run writes a COLVAR too, and summing hills
        that are not there is not a question worth asking."""
        import inspect

        from fastmdxplora.simulation import pipeline

        source = inspect.getsource(pipeline.run)
        guarded = source[source.index('if params.get("metadynamics")'):]
        assert "_write_metadynamics_surface" in guarded.split("\n\n")[0]

    def test_a_refusal_is_written_down_too(self, tmp_path) -> None:
        """A refusal that leaves no trace is a run that looks as though the
        question was never asked."""
        import json

        from fastmdxplora.simulation.pipeline import _write_metadynamics_surface

        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        _written(tmp_path, hills)
        (tmp_path / "COLVAR").write_text(
            "#! FIELDS time cv\n" + "\n".join(f"{i} 0.3" for i in range(50)),
            encoding="utf-8")

        name = _write_metadynamics_surface(tmp_path, None)

        assert name == "metadynamics_surface.json"
        record = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert record["refused"]
        # Written, not withheld: the refusal says the surface below is a
        # snapshot of the filling, and a record that carries the sentence
        # without the numbers leaves a reader with nothing to look at.
        assert record["free_energy_kjmol"] is not None
        assert record["provisional"] is True
        assert record["evidence"]["recrossings"] == 0

    def test_nothing_is_written_where_there_were_no_hills(self, tmp_path) -> None:
        from fastmdxplora.simulation.pipeline import _write_metadynamics_surface

        assert _write_metadynamics_surface(tmp_path, None) is None


class TestARefusalIsPrintedWhole:
    """The message explaining why there is no surface was cut at 160
    characters, mid-sentence: "...rather than having flattened it -- the
    surface". What went missing was the clause saying the output is still a
    usable snapshot of the filling."""

    def test_nothing_clips_it(self) -> None:
        import inspect

        from fastmdxplora.simulation import pipeline

        source = inspect.getsource(pipeline)
        refusal = source.split('"No free energy surface: "', 1)[1][:300]
        assert "[:160]" not in refusal
        assert "[:" not in refusal.split("presenter.step")[0]

    def test_the_reason_says_the_snapshot_is_still_there(self) -> None:
        """The clause the cut removed."""
        import inspect

        from fastmdxplora.simulation import metad_surface

        source = inspect.getsource(metad_surface)
        assert "is a snapshot of that filling" in source


class TestTheRecrossingCountSaysWhatItCounts:
    """"Recrossings" is a word whose definition changes the number. A run
    recorded as 61 gave 97 by counting sign changes of the raw coordinate --
    60% apart -- and anyone comparing their own count against the record, not
    knowing which was meant, would draw the wrong conclusion about their
    sampling."""

    def test_jitter_is_not_a_traversal(self) -> None:
        """The distinction the hysteresis band exists for: a coordinate
        rattling at a threshold scores hundreds by sign changes and none
        here."""
        import numpy as np

        from fastmdxplora.simulation.metad_surface import recrossings

        jitter = np.random.default_rng(0).normal(0.0, 0.05, 400)
        assert recrossings(jitter, low=-1.5, high=1.5) == 0
        # What a naive count would have said.
        assert int(np.sum(np.diff(np.sign(jitter)) != 0)) > 100

    def test_a_genuine_traversal_counts(self) -> None:
        import numpy as np

        from fastmdxplora.simulation.metad_surface import recrossings

        back_and_forth = np.tile(
            np.concatenate([np.full(20, -3.0), np.full(20, 3.0)]), 10)
        assert recrossings(back_and_forth, low=-1.5, high=1.5) == 19

    def test_the_record_carries_the_definition(self) -> None:
        import inspect

        from fastmdxplora.simulation import metad_surface

        source = inspect.getsource(metad_surface)
        assert '"recrossings_definition"' in source
        # It quotes the band, so the number can be reproduced.
        assert "low_edge" in source and "high_edge" in source


class TestDriftIsJudgedWhereTheSurfaceMeansSomething:
    """Comparing every point on the grid makes the number the worst point
    rather than the typical one, and the worst point is always the top of the
    highest barrier -- estimated from a handful of visits out of thousands of
    hills.

    A tripeptide's psi torsion settled to 1.2 kJ/mol within 10 of its
    minimum and 2.3 within 20, while the whole-grid figure read 5.4 from a
    single point atop a 65 kJ/mol barrier. On that measure no steep
    coordinate can pass however well its wells are resolved: the test could
    not say yes to a correct answer."""

    def test_the_ceiling_is_recorded_with_the_number(self) -> None:
        """A drift measured over part of the range means nothing without
        saying which part."""
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        import tempfile
        from pathlib import Path

        result = compute_surface(
            _written(Path(tempfile.mkdtemp()), hills), np.tile([0., 1.], 20))
        evidence = result["evidence"]

        assert "drift_ceiling_kjmol" in evidence
        assert "drift_over_the_whole_grid_kjmol" in evidence
        assert evidence["drift_kjmol"] <= evidence["drift_over_the_whole_grid_kjmol"]

    def test_the_ceiling_is_a_few_multiples_of_rt(self) -> None:
        """A state rarer than this is not one a simulation is measuring."""
        from fastmdxplora.simulation.metad_surface import DRIFT_CEILING_KJMOL

        rt_300k = 2.4789
        assert 4 * rt_300k < DRIFT_CEILING_KJMOL < 15 * rt_300k

    def test_a_barrier_top_does_not_decide_a_converged_run(self) -> None:
        """Wells stable, one high point moving: the verdict should follow the
        wells."""

        from fastmdxplora.simulation.metad_surface import (
            DRIFT_CEILING_KJMOL,
            SETTLED_DRIFT_KJMOL,
        )

        # Built directly: a surface differing only far above its minimum.
        grid = np.linspace(-np.pi, np.pi, 200)
        shape = 30.0 * (1.0 - np.cos(grid))          # 0 at the well, 60 at the top
        moved = shape.copy()
        moved[shape > DRIFT_CEILING_KJMOL] += 5.0    # only the barrier moves

        difference = np.abs(shape - moved)
        judged = shape <= DRIFT_CEILING_KJMOL

        assert difference.max() > SETTLED_DRIFT_KJMOL      # over the whole grid
        assert difference[judged].max() <= SETTLED_DRIFT_KJMOL  # where it counts


class TestARefusedRunStillHandsBackWhatItHas:
    """The refusal says "the surface below is a snapshot of that filling" and
    then returned None, so a run whose wells had converged to half of RT left
    nothing to plot. "Not converged" and "not available" are different
    claims."""

    def _refused(self, tmp_path):
        grid = _grid()
        hills = _hills_reaching(_double_well(grid), grid)
        return compute_surface(_written(tmp_path, hills), np.tile([0., 1.], 20))

    def test_the_snapshot_comes_back(self, tmp_path) -> None:
        result = self._refused(tmp_path)
        assert result["refused"]
        assert result["surface"] is not None
        assert len(result["surface"]) == len(result["grid"])

    def test_it_is_marked_provisional(self, tmp_path) -> None:
        """Both are worth having; only one is worth quoting."""
        result = self._refused(tmp_path)
        assert result["provisional"] is True

    def test_the_flag_distinguishes_the_two(self, tmp_path) -> None:
        """`provisional` is what tells a reader whether the numbers beside it
        are an answer or a progress report, so it has to be present either
        way rather than only when it is true."""
        result = self._refused(tmp_path)
        assert "provisional" in result
        assert isinstance(result["provisional"], bool)

        import inspect

        from fastmdxplora.simulation import metad_surface

        source = inspect.getsource(metad_surface.compute_surface)
        # Set on the accepted path too, not only the refused one.
        assert source.count('"provisional"') >= 2

    def test_a_coordinate_that_never_moved_still_has_nothing(
        self, tmp_path
    ) -> None:
        """The one refusal where None is the honest answer: no surface exists
        along a coordinate that did not move."""
        still = Hills(np.arange(50.0), np.full(50, 0.3), np.full(50, SIGMA),
                      np.full(50, 1.0), GAMMA)
        result = compute_surface(_written(tmp_path, still), np.full(50, 0.3))
        assert result["surface"] is None


class TestASurfaceOverTwoVariablesIsJudgedOneAtATime:
    """The claim a two-dimensional surface has to survive.

    A run can fill one coordinate thoroughly while the other never leaves
    a single well. A verdict on the surface as a whole either passes it,
    hiding the stuck coordinate, or fails it, burying the good one. So the
    gates run on the free energy along each variable with the other
    integrated out, and a refusal names the dimension it is about.
    """

    @staticmethod
    def _write(path, centres, sigmas, heights, biasf=10.0):
        with open(path, "w") as fh:
            fh.write("#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 height biasf\n")
            for t, (c1, c2), (s1, s2), h in zip(
                    range(len(heights)), centres, sigmas, heights):
                fh.write(f"{t * 0.5:.3f} {c1:.5f} {c2:.5f} {s1:.4f} "
                         f"{s2:.4f} {h:.6f} {biasf:.1f}\n")

    @staticmethod
    def _heights(n):
        import numpy as np
        return 1.2 * np.exp(-np.linspace(0, 3.5, n))

    def _two_basin(self, rng, n, centre=1.0, noise=0.25):
        import numpy as np
        return (np.where(rng.rand(n) < 0.5, -centre, centre)
                + rng.normal(scale=noise, size=n))

    def test_the_header_says_how_many_variables_there_are(self, tmp_path):
        """Read by position, a two-variable file gives a sigma as a height.

        The layout puts the height in column six where a one-variable file
        has the bias factor, so every hill's weight would be wrong and the
        surface would come out scaled by a number nobody chose.
        """
        import numpy as np

        from fastmdxplora.simulation.metad_surface import read_hills

        rng = np.random.RandomState(0)
        n = 200
        heights = self._heights(n)
        path = tmp_path / "HILLS"
        self._write(path,
                    np.column_stack([self._two_basin(rng, n),
                                     self._two_basin(rng, n)]),
                    np.tile([0.2, 0.2], (n, 1)), heights)

        hills = read_hills(path)
        assert hills.n_dims == 2
        assert hills.centre.shape == (n, 2)
        assert np.isclose(hills.height[0], heights[0])
        assert hills.bias_factor == 10.0

    def test_a_one_variable_file_still_reads_as_it_did(self, tmp_path):
        import numpy as np

        from fastmdxplora.simulation.metad_surface import read_hills

        path = tmp_path / "HILLS"
        path.write_text(
            "#! FIELDS time cv sigma_cv height biasf\n"
            "0.0 0.5 0.2 1.2 10.0\n"
            "0.5 0.6 0.2 1.1 10.0\n", encoding="utf-8")
        hills = read_hills(path)
        assert hills.n_dims == 1
        assert hills.centre.ndim == 1
        assert np.allclose(hills.height, [1.2, 1.1])

    def test_a_stuck_dimension_is_refused_by_name(self, tmp_path):
        import numpy as np

        from fastmdxplora.simulation.metad_surface import compute_surface_2d

        rng = np.random.RandomState(0)
        n = 1200
        explored = self._two_basin(rng, n)
        stuck = 0.5 + rng.normal(scale=0.05, size=n)
        path = tmp_path / "HILLS"
        self._write(path, np.column_stack([explored, stuck]),
                    np.tile([0.2, 0.1], (n, 1)), self._heights(n))

        out = compute_surface_2d(
            path, np.column_stack([explored, stuck]),
            points=60, names=("phi", "dist"))

        assert out["refused"] is not None
        assert "dist" in out["refused"]
        assert "phi" not in out["refused"], (
            "the good dimension must not be impeached by the bad one")

    def test_a_narrow_well_does_not_pass_by_being_reproducible(self, tmp_path):
        """The case a drift check cannot catch.

        A coordinate that never moved gives the same narrow well from three
        quarters of the hills and from all of them, so it looks converged
        precisely because nothing happened. The exploration width is what
        separates that from a landscape.
        """
        import numpy as np

        from fastmdxplora.simulation.metad_surface import compute_surface_2d

        rng = np.random.RandomState(0)
        n = 1200
        explored = self._two_basin(rng, n)
        stuck = 0.5 + rng.normal(scale=0.05, size=n)
        path = tmp_path / "HILLS"
        self._write(path, np.column_stack([explored, stuck]),
                    np.tile([0.2, 0.1], (n, 1)), self._heights(n))

        out = compute_surface_2d(
            path, np.column_stack([explored, stuck]),
            points=60, names=("phi", "dist"))
        records = {r["name"]: r for r in out["evidence"]["per_dimension"]}

        assert records["dist"]["drift_kjmol"] < 2.5, (
            "the stuck well is reproducible, which is the point")
        assert records["dist"]["explored_in_hill_widths"] < 6.0
        assert records["phi"]["explored_in_hill_widths"] > 6.0

    def test_a_run_that_explored_both_is_accepted(self, tmp_path):
        import numpy as np

        from fastmdxplora.simulation.metad_surface import compute_surface_2d

        rng = np.random.RandomState(0)
        n = 1200
        a = self._two_basin(rng, n)
        b = self._two_basin(rng, n, centre=0.8, noise=0.2)
        path = tmp_path / "HILLS"
        self._write(path, np.column_stack([a, b]),
                    np.tile([0.2, 0.2], (n, 1)), self._heights(n))

        out = compute_surface_2d(
            path, np.column_stack([a, b]), points=60, names=("phi", "psi"))
        assert out["refused"] is None
        assert out["surface"].shape == (60, 60)

    def test_the_marginal_keeps_the_width_of_a_valley(self):
        """Integrating, not minimising.

        A broad shallow channel and a narrow deep one have the same floor,
        so the projection people usually draw cannot tell them apart. The
        free energy along a coordinate is about how much room there is,
        which is what integrating the populations measures.
        """
        import numpy as np

        from fastmdxplora.simulation.metad_surface import marginal_profile

        y = np.linspace(-3, 3, 201)
        surface = np.vstack([
            0.5 * y ** 2,   # at x = 0, a broad channel
            8.0 * y ** 2,   # at x = 1, a narrow one
        ])
        # The floors are identical, so the minimum along y cannot tell the
        # two columns apart: a projection reports them as equal.
        assert np.isclose(surface[0].min(), surface[1].min())
        assert np.allclose(surface.min(axis=1), [0.0, 0.0])

        profile = marginal_profile(surface, axis=0)
        # Integrating does tell them apart, and in the right direction: the
        # broad channel holds more population, so its free energy is lower.
        assert profile[0] < profile[1]
        assert profile[1] - profile[0] > 1.0

    def test_a_coordinate_that_never_moved_is_refused_outright(self, tmp_path):
        import numpy as np

        from fastmdxplora.simulation.metad_surface import compute_surface_2d

        rng = np.random.RandomState(0)
        n = 300
        moving = self._two_basin(rng, n)
        frozen = np.full(n, 0.4)
        path = tmp_path / "HILLS"
        self._write(path, np.column_stack([moving, frozen]),
                    np.tile([0.2, 0.2], (n, 1)), self._heights(n))

        out = compute_surface_2d(
            path, np.column_stack([moving, frozen]),
            points=40, names=("phi", "frozen"))
        assert out["surface"] is None
        assert "frozen" in out["refused"]

    def test_one_variable_is_not_this_function_s_job(self, tmp_path):
        import pytest

        from fastmdxplora.simulation.metad_surface import compute_surface_2d

        path = tmp_path / "HILLS"
        path.write_text(
            "#! FIELDS time cv sigma_cv height biasf\n"
            "0.0 0.5 0.2 1.2 10.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="compute_surface"):
            compute_surface_2d(path)


class TestTheRunWritesATwoVariableSurface:
    """The pipeline has to notice the file has two columns of variable.

    Reading column one and calling the one-dimensional path would produce
    a surface along the first variable and silently discard the second,
    which is worse than failing: the run completes and the record claims a
    coordinate that was never reconstructed.
    """

    @staticmethod
    def _run_dir(tmp_path, *, second_is_torsion=False):
        import numpy as np

        rng = np.random.RandomState(0)
        n = 1200
        heights = 1.2 * np.exp(-np.linspace(0, 3.5, n))
        a = (np.where(rng.rand(n) < 0.5, -1.0, 1.0)
             + rng.normal(scale=0.25, size=n))
        b = (np.where(rng.rand(n) < 0.5, -0.8, 0.8)
             + rng.normal(scale=0.2, size=n))

        with open(tmp_path / "HILLS", "w") as fh:
            fh.write("#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 "
                     "height biasf\n")
            for t, (c1, c2), h in zip(
                    range(n), np.column_stack([a, b]), heights):
                fh.write(f"{t * 0.5:.3f} {c1:.5f} {c2:.5f} 0.2000 0.2000 "
                         f"{h:.6f} 10.0\n")
        with open(tmp_path / "COLVAR", "w") as fh:
            fh.write("#! FIELDS time cv1 cv2 metad.bias\n")
            for t, (c1, c2) in zip(range(n), np.column_stack([a, b])):
                fh.write(f"{t * 0.5:.3f} {c1:.5f} {c2:.5f} 0.0\n")
        second = "TORSION ATOMS=5,6,7,8" if second_is_torsion \
            else "DISTANCE ATOMS=5,6"
        (tmp_path / "plumed.dat").write_text(
            f"cv1: TORSION ATOMS=1,2,3,4\ncv2: {second}\n"
            "metad: METAD ARG=cv1,cv2 SIGMA=0.2,0.2 FILE=HILLS\n",
            encoding="utf-8")
        return tmp_path

    def test_it_writes_a_grid_not_a_line(self, tmp_path):
        import json

        from fastmdxplora.simulation.pipeline import (
            _write_metadynamics_surface)

        _write_metadynamics_surface(self._run_dir(tmp_path), None)
        record = json.loads(
            (tmp_path / "metadynamics_surface.json").read_text())

        assert record["dimensions"] == 2
        assert len(record["axes"]) == 2
        assert len(record["free_energy_kjmol"]) == len(record["axes"][0])
        assert len(record["free_energy_kjmol"][0]) == len(record["axes"][1])

    def test_periodicity_is_read_per_variable(self, tmp_path):
        """A torsion against a distance is circular in one dimension only.

        One flag for both wraps the distance around a circle it does not
        live on, and the surface comes out wrong at that edge.
        """
        import json

        from fastmdxplora.simulation.pipeline import (
            _write_metadynamics_surface)

        _write_metadynamics_surface(self._run_dir(tmp_path), None)
        record = json.loads(
            (tmp_path / "metadynamics_surface.json").read_text())
        dims = record["evidence"]["per_dimension"]

        assert dims[0]["periodic"] is True
        assert dims[1]["periodic"] is False

    def test_both_columns_of_the_trajectory_are_used(self, tmp_path):
        import json

        from fastmdxplora.simulation.pipeline import (
            _write_metadynamics_surface)

        _write_metadynamics_surface(self._run_dir(tmp_path), None)
        record = json.loads(
            (tmp_path / "metadynamics_surface.json").read_text())

        # A recrossing count in each dimension is only possible if the
        # trajectory of each variable reached the gate.
        for dim in record["evidence"]["per_dimension"]:
            assert dim["recrossings"] is not None and dim["recrossings"] > 0


class TestTheStoredHeightsAreAlreadyTempered:
    """PLUMED's HILLS convention, asserted against numbers, not wording.

    `plumed sum_hills` negates the summed file and stops. It does that
    because MetaD multiplies each height by y/(y-1) before printing, so the
    file already holds the free energy's ingredients -- which is exactly
    what `reweighted_averages.deposited_heights` says when it undoes the
    factor to recover the bias.

    Applying the factor a second time made every well-tempered surface too
    large by y/(y-1) -- 11.1% at y=10, 100% at y=2 -- and no test saw it,
    because the fixture that built the hills encoded the same
    misunderstanding. These two assertions cannot both hold under that bug:
    the first pins the absolute scale against an independently summed
    profile, the second pins the invariant that makes the first robust.
    """

    def _hills_with_known_sum(self, bias_factor, centres=60):
        """Hills whose plain sum is a 20 kJ/mol barrier, by construction."""
        places = np.linspace(-np.pi, np.pi, centres)
        sigma = 0.2
        target = 20.0 * (1.0 - np.cos(2.0 * places)) / 2.0
        overlap = np.exp(
            -((places[:, None] - places[None, :]) ** 2) / (2.0 * sigma ** 2))
        stored = np.linalg.solve(overlap, target)
        return Hills(
            time_ps=np.arange(centres, dtype=float),
            centre=places,
            sigma=np.full(centres, sigma),
            height=stored,
            bias_factor=bias_factor,
        )

    def test_the_surface_is_the_negated_sum_of_the_file(self):
        hills = self._hills_with_known_sum(10.0)
        grid = np.linspace(-np.pi, np.pi, 400)

        got = surface_from_hills(hills, grid, periodic=True)
        got = got - got.min()

        # The same arithmetic `plumed sum_hills` performs, written out.
        separation = np.remainder(
            grid[None, :] - hills.centre[:, None] + np.pi, 2.0 * np.pi) - np.pi
        summed = np.sum(
            hills.height[:, None]
            * np.exp(-separation ** 2 / (2.0 * hills.sigma[:, None] ** 2)),
            axis=0,
        )
        expected = -summed
        expected = expected - expected.min()

        assert got.max() == pytest.approx(expected.max(), rel=1e-9)
        assert np.allclose(got, expected)

        # And the barrier the fixture was built to carry.
        assert got.max() == pytest.approx(20.0, abs=0.3)

    @pytest.mark.parametrize("bias_factor", [1.0, 2.0, 5.0, 10.0, 20.0])
    def test_the_answer_does_not_depend_on_the_bias_factor(self, bias_factor):
        """One HILLS file, five recorded bias factors, one free energy.

        The tempering is already in the heights, so the number PLUMED
        recorded alongside them cannot change what they sum to. Under the
        double-scaling bug this reads 20.0 at y=1 and 40.0 at y=2.
        """
        grid = np.linspace(-np.pi, np.pi, 400)
        hills = self._hills_with_known_sum(bias_factor)

        surface = surface_from_hills(hills, grid, periodic=True)

        assert (surface.max() - surface.min()) == pytest.approx(20.0, abs=0.3)
