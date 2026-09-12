"""Three hundred picoseconds of sampling say how to spend the next day of it.

An umbrella study's two settings -- how far apart the windows sit and how
hard each is held -- decide whether it produces a free energy or a refusal,
and neither can be read off the structure. The usual way to find them is to
run a study, watch it refuse, change something, and run it again. The study
this module was written from took three of those over two days.

But a window is a measurement. It comes to rest where its restraint's pull
matches the free energy's, so its displacement times its force constant is
the gradient of the surface at that point -- the only place an umbrella
study reports the slope directly. A handful of windows run briefly measure
the gradient along the whole coordinate, and the gradient is exactly what
fixes both settings.

These tests check the arithmetic against the design that was arrived at by
hand, and check that the answer comes back able to pass the study's own
gates rather than only asserted to.
"""

from __future__ import annotations

import math
import textwrap

import numpy as np
import pytest
import yaml

from fastmdxplora.simulation.umbrella import (
    _ideal_overlap,
    as_a_config_block,
    design_from_a_pilot,
    overlap_between,
    plan_windows,
)

KB_KJ = 0.008314462618
KT = KB_KJ * 300.0


def settles_at(centre, force_constant, gradient):
    """Where a window comes to rest on a surface whose slope is `gradient`.

    The restraint pulls with ``k * (centre - x)`` and the surface with
    ``G(x)``; the window sits where those balance. Solved rather than
    assumed, so a test fixture cannot ask for a window resting somewhere the
    surface would not hold it -- which is how a pilot with a gradient of 300
    at a window sitting on flat ground gets written by accident.
    """
    lo, hi = centre - 1.0, centre
    for _ in range(200):
        middle = 0.5 * (lo + hi)
        if force_constant * (centre - middle) - gradient(middle) > 0:
            lo = middle
        else:
            hi = middle
    return 0.5 * (lo + hi)


def a_pilot(centres, force_constant, gradient, samples=4000, spread=None,
            variable="distance", seed=0):
    """A short run of windows on a surface with a known slope everywhere.

    `gradient` is that slope: a number for a stretch of constant steepness,
    or a function of the coordinate. Each window is put where the surface
    would hold it and its sampling drawn about that point at the width its
    restraint implies.
    """
    forces = ([force_constant] * len(centres)
              if isinstance(force_constant, (int, float)) else
              list(force_constant))
    if not callable(gradient):
        steepness = float(gradient)

        def gradient(x):
            return steepness

    plan = plan_windows({"collective_variable": variable,
                         "centres": list(centres),
                         "force_constant": forces,
                         "minimum_samples": 50})
    rng = np.random.default_rng(seed)
    drawn = {}
    for index, (centre, k) in enumerate(zip(centres, forces)):
        width = math.sqrt(KT / k) if spread is None else spread
        drawn[index] = (settles_at(centre, k, gradient)
                        + width * rng.standard_normal(samples))
    return drawn, plan


def a_ramp(flat, steep, at, over):
    """A surface flat on one side and steep on the other."""
    def gradient(x):
        return flat + (steep - flat) / (1.0 + math.exp(-(x - at) / over))
    return gradient


class TestItReproducesADesignArrivedAtByHand:
    """The stiff stretch of a real study, which took three runs to find.

    Trypsin-benzamidine, 0.700 to 1.250 nm: the windows through there measure
    223 kJ/mol/nm, and the study that finally recombined placed 17 windows
    0.0344 nm apart and held each at 13000 kJ/mol/nm^2. That design was
    reached by running one study at 3000 and watching three windows slide,
    sizing a probe from what they had slid against, running the probe, and
    then building the campaign around it.
    """

    def _stiff_stretch(self, **kwargs):
        drawn, plan = a_pilot(
            centres=[0.700, 0.810, 0.920, 1.030, 1.140, 1.250],
            force_constant=3000.0,
            gradient=223.0,
            spread=1e-6,          # the arithmetic, without sampling noise
        )
        return design_from_a_pilot(drawn, plan, **kwargs)

    def test_the_stretch_comes_back_as_the_study_ran_it(self):
        """Six windows of pilot, and the answer is the campaign."""
        design = self._stiff_stretch(gate_used=1.0)
        spacing = np.diff(design["centres"])

        assert design["n_windows"] == 17
        assert float(np.mean(spacing)) == pytest.approx(0.0344, abs=0.0002)
        assert min(design["force_constants"]) == pytest.approx(13000, rel=0.01)

    def test_the_pilot_need_not_be_long(self):
        """The claim that three hundred picoseconds is enough.

        A displacement is a mean-like quantity, so it converges like one. The
        design from 1500 samples per window is the design from twenty times
        that, because the gradient is known to about six kJ/mol/nm either way
        and the answer moves by less than the rounding.
        """
        short, plan = a_pilot([0.700, 0.920, 1.140, 1.250], 3000.0,
                              223.0, samples=1500, seed=3)
        long, _ = a_pilot([0.700, 0.920, 1.140, 1.250], 3000.0,
                          223.0, samples=30000, seed=3)

        from_short = design_from_a_pilot(short, plan)
        from_long = design_from_a_pilot(long, plan)

        assert from_short["n_windows"] == from_long["n_windows"]
        assert (max(from_short["force_constants"])
                == pytest.approx(max(from_long["force_constants"]), rel=0.1))

    def test_the_default_leaves_room_at_the_gate(self):
        """That study's window at 13000 was still flagged for drift.

        The design at `gate_used=1` puts a window exactly on the threshold it
        will be judged against, and a threshold landed on exactly is crossed
        half the time -- which is what happened: the window came back flagged
        and the pair beside it had the thinnest overlap in the study. The
        default spends a fifth of the gate on not doing that.
        """
        on_the_line = self._stiff_stretch(gate_used=1.0)
        default = self._stiff_stretch()

        assert default["n_windows"] > on_the_line["n_windows"]
        assert min(default["force_constants"]) > min(
            on_the_line["force_constants"])
        assert max(p["gate_it_would_use"] for p in default["predicted"]) == (
            pytest.approx(0.8, abs=0.01))

    def test_it_answers_the_study_that_refused(self):
        """The first study: 0.06 nm apart, 3000 everywhere, and windows
        losing to 360 kJ/mol/nm slid out of their own histograms.

        Its own windows carry the design that would have worked -- closer
        together and held harder -- which is the point of running a short one
        first.
        """
        drawn, plan = a_pilot(centres=[0.83, 0.89, 0.95, 1.01],
                              force_constant=3000.0,
                              gradient=360.0)

        design = design_from_a_pilot(drawn, plan)
        spacing = float(np.mean(np.diff(design["centres"])))

        assert spacing < 0.06
        assert min(design["force_constants"]) > 3000.0


class TestEveryWindowIsAMeasurement:

    def test_a_window_that_held_its_centre_is_measured_too(self):
        """The drift gate decides whether to complain, not whether the
        number exists. A window sitting on its centre measures a gradient of
        nearly nothing, which is a reading of a flat surface and belongs in
        the profile with the rest."""
        drawn, plan = a_pilot([0.40, 0.46, 0.52], 3000.0,
                              a_ramp(2.0, 400.0, at=0.55, over=0.02),
                              spread=1e-6)

        design = design_from_a_pilot(drawn, plan)

        assert [m["window"] for m in design["measured"]] == [0, 1, 2]
        assert design["measured"][0]["gradient_kjmol_per_unit"] < 10.0
        assert design["measured"][2]["gradient_kjmol_per_unit"] > 30.0

    def test_the_gradient_is_the_constant_times_the_displacement(self):
        """No fit and no differencing of a reconstructed curve: the balance
        of two forces, read off one window."""
        drawn, plan = a_pilot([0.83, 0.89], 13000.0, 300.0, spread=1e-6)

        measured = design_from_a_pilot(drawn, plan)["measured"]

        for entry in measured:
            assert entry["gradient_kjmol_per_unit"] == pytest.approx(
                entry["force_constant"] * entry["away_by"])
            assert entry["gradient_kjmol_per_unit"] == pytest.approx(
                300.0, rel=0.01)

    def test_a_coordinate_that_wraps_is_measured_the_short_way(self):
        """A torsion window straddling the join.

        Its samples lie either side of +-pi, whose median is on the far side
        of the circle: read that way the window looks displaced by most of a
        turn and reports a gradient of thousands, and the design that follows
        tiles the whole coordinate with needle windows. The circular mean
        puts it where it is.
        """
        centres = [-3.0, 3.1]
        rng = np.random.default_rng(1)
        drawn = {0: -3.0 + 0.05 * rng.standard_normal(4000),
                 1: np.mod(3.1 + 0.05 * rng.standard_normal(4000) + math.pi,
                           2 * math.pi) - math.pi}
        plan = plan_windows({"collective_variable": "torsion",
                             "centres": centres, "force_constant": 100.0,
                             "minimum_samples": 50})

        design = design_from_a_pilot(drawn, plan)

        assert design["measured"][1]["away_by"] < 0.05
        assert design["measured"][1]["gradient_kjmol_per_unit"] < 10.0


class TestTheGridFollowsTheSurface:

    def _flat_then_steep(self, **kwargs):
        """Six windows: three on a flat stretch, three on a steep one."""
        drawn, plan = a_pilot(
            centres=[0.40, 0.46, 0.52, 0.58, 0.64, 0.70],
            force_constant=3000.0,
            gradient=a_ramp(5.0, 800.0, at=0.61, over=0.015),
            spread=1e-6,
        )
        return design_from_a_pilot(drawn, plan, **kwargs)

    def test_windows_crowd_where_the_surface_is_steep(self):
        design = self._flat_then_steep()
        spacing = np.diff(design["centres"])
        flat = spacing[np.asarray(design["centres"][:-1]) < 0.50]
        steep = spacing[np.asarray(design["centres"][:-1]) > 0.62]

        assert float(flat.min()) > 2.5 * float(steep.max())

    def test_the_stiffness_follows_them(self):
        design = self._flat_then_steep()
        constants = np.asarray(design["force_constants"])
        centres = np.asarray(design["centres"])

        assert float(constants[centres > 0.62].min()) > 5 * float(
            constants[centres < 0.50].max())

    def test_the_flat_stretch_is_not_made_finer_than_the_pilot_ran_it(self):
        """Where the surface is flat there is nothing to resolve, and the
        measured gradient there is a small number with a large relative
        error. Without a bound it asks for windows metres apart."""
        design = self._flat_then_steep()
        spacing = np.diff(design["centres"])

        assert float(spacing.max()) <= 0.06 + 1e-6

    def test_it_never_asks_for_a_softer_constant_than_the_pilot_used(self):
        design = self._flat_then_steep()

        assert min(design["force_constants"]) >= 3000.0

    def test_it_covers_what_the_pilot_covered_and_stops_there(self):
        design = self._flat_then_steep()

        assert design["covers"] == [0.40, 0.70]
        assert design["centres"][0] == pytest.approx(0.40)
        assert design["centres"][-1] == pytest.approx(0.70)

    def test_a_window_is_stiff_enough_for_the_steepest_ground_it_covers(self):
        """Sizing each window from the slope at the point it is centred on
        leaves the window at the foot of a climb too soft for the top of it,
        and that window is the one that slides."""
        drawn, plan = a_pilot(
            centres=[0.40, 0.50, 0.60],
            force_constant=3000.0,
            gradient=a_ramp(2.0, 600.0, at=0.57, over=0.01),
            spread=1e-6,
        )

        design = design_from_a_pilot(drawn, plan)
        below = [k for k, c in zip(design["force_constants"],
                                   design["centres"]) if 0.52 < c < 0.58]

        assert below and min(below) > 3000.0


class TestTheDesignChecksItselfBeforeItIsRun:
    """A recommendation is worth what its own arithmetic says it is.

    Both gates a study applies -- a window inside its share of the spacing,
    and neighbours sharing enough area to stitch -- can be evaluated on a
    design before any of it runs, from the distributions the restraints
    imply. So they are.
    """

    def _design(self, **kwargs):
        drawn, plan = a_pilot(
            centres=[0.40, 0.52, 0.64, 0.76, 0.88, 1.00],
            force_constant=3000.0,
            gradient=a_ramp(40.0, 400.0, at=0.76, over=0.06),
            spread=1e-6,
        )
        return design_from_a_pilot(drawn, plan, **kwargs), plan

    def test_every_window_it_proposes_stays_inside_the_gate(self):
        design, _ = self._design()

        assert max(p["gate_it_would_use"] for p in design["predicted"]) <= 0.8

    def test_neighbours_share_more_than_the_study_will_ask_for(self):
        design, plan = self._design()

        assert design["worst_predicted_overlap"] > plan.minimum_overlap

    def test_holding_them_harder_does_not_open_a_gap(self):
        """Stiffening without closing the gaps is the mistake this exists to
        stop: sigma falls as sqrt(kT/k), so a harder-held window is a
        narrower one and the neighbours it shared a third of its area with
        share almost none. Every setting of the margin keeps the overlap."""
        for gate_used in (1.0, 0.8, 0.6, 0.4):
            design, plan = self._design(gate_used=gate_used)

            assert design["worst_predicted_overlap"] > plan.minimum_overlap, (
                f"gate_used={gate_used} opened a gap")

    @pytest.mark.parametrize("shape,gradient", [
        ("flat", lambda x: 5.0),
        ("a narrow barrier", lambda x: 5.0 + 295.0 * math.exp(
            -((x - 0.70) / 0.03) ** 2)),
        ("a broad barrier", lambda x: 5.0 + 295.0 * math.exp(
            -((x - 0.70) / 0.06) ** 2)),
        ("a climb", lambda x: 5.0 + 795.0 / (
            1.0 + math.exp(-(x - 0.80) / 0.02))),
        ("a descent", lambda x: 5.0 + 795.0 / (
            1.0 + math.exp((x - 0.60) / 0.02))),
    ])
    def test_the_thinnest_pair_lands_near_the_target_whatever_the_shape(
            self, shape, gradient):
        """A design placing windows at two and a half sigma aims at 0.21,
        which is what two such neighbours share. Holding near that on a
        surface that changes steepness takes sizing each step by the ground
        on both sides of it: a window is held for whatever is steepest around
        it, and a pair overlaps at the narrower of the two.

        Sized by the ground ahead alone, these five shapes came back at
        0.247, 0.194, 0.166, 0.212 and 0.157, and a real study's windows at
        0.137. Sized by both sides: 0.247, 0.228, 0.223, 0.183 and 0.185 --
        the worst case moves from two thirds of the target to seven eighths
        of it, at the cost of two or three windows.
        """
        drawn, plan = a_pilot(
            centres=[0.40, 0.52, 0.64, 0.76, 0.88, 1.00],
            force_constant=3000.0, gradient=gradient, spread=1e-6,
        )

        design = design_from_a_pilot(drawn, plan)

        assert design["worst_predicted_overlap"] > 0.18, shape

    def test_the_predicted_overlap_is_what_the_study_would_measure(self):
        """`overlap_between` is the area of two histograms; the prediction is
        the area of the two distributions those histograms are drawn from.
        Same quantity, one of them available a day earlier."""
        rng = np.random.default_rng(5)
        centre_a, centre_b = 0.500, 0.5344
        force_a, force_b = 13000.0, 3000.0
        a = centre_a + math.sqrt(KT / force_a) * rng.standard_normal(200000)
        b = centre_b + math.sqrt(KT / force_b) * rng.standard_normal(200000)

        assert _ideal_overlap(centre_a, force_a, centre_b, force_b,
                              KT) == pytest.approx(overlap_between(a, b),
                                                   abs=0.02)


class TestTheCurveIsTheBetterGradientWhereThereIsOne:
    """A window's displacement is the median of a correlated series.

    What it is worth is set by how many independent samples the window took,
    not how many rows it wrote. On a real study -- ten nanoseconds a window
    against a correlation time of two and a half -- that is about three, the
    median is good to about 0.01 nm, and a window held at 13000 reports its
    gradient to within a hundred kJ/mol. The design that came back alternated
    between 19440 and 3000 on neighbouring windows.

    The recombination pools every window's sampling into one curve, and its
    slope is the same quantity measured far better.
    """

    def _a_climb(self, gradient=200.0, span=(0.40, 1.00)):
        """A surface of known slope, as a curve and as windows on it."""
        lo, hi = span
        along = np.linspace(lo, hi, 60)
        height = gradient * (along - lo)
        centres = list(np.linspace(lo, hi, 6))
        drawn, plan = a_pilot(centres=centres, force_constant=3000.0,
                              gradient=gradient, spread=1e-6)
        return drawn, plan, (along.tolist(), height.tolist())

    def test_the_curve_gives_the_design_the_windows_do(self):
        """Same surface, two ways of measuring it, one answer."""
        drawn, plan, curve = self._a_climb()

        from_windows = design_from_a_pilot(drawn, plan)
        from_curve = design_from_a_pilot(drawn, plan, curve=curve)

        assert from_curve["n_windows"] == pytest.approx(
            from_windows["n_windows"], abs=1)
        assert max(from_curve["force_constants"]) == pytest.approx(
            max(from_windows["force_constants"]), rel=0.15)

    def test_one_window_sitting_still_no_longer_moves_the_design(self):
        """The failure this exists for: a window whose median happens to land
        on its centre reports a gradient of nothing and asks for the softest
        constant in the study, between two neighbours asking for five times
        it. The curve does not have that in it."""
        drawn, plan, curve = self._a_climb()
        settled = plan.windows[3]
        drawn[settled.index] = np.full(4000, settled.centre)

        from_windows = design_from_a_pilot(drawn, plan)
        from_curve = design_from_a_pilot(drawn, plan, curve=curve)

        def softest_beside_it(design):
            constants = np.asarray(design["force_constants"])
            centres = np.asarray(design["centres"])
            near = constants[np.abs(centres - settled.centre) < 0.05]
            return float(near.min()) / float(constants.max())

        # The premise: a dip to a quarter of the study's own stiffness,
        # around a stretch of surface no flatter than any other.
        assert softest_beside_it(from_windows) < 0.4
        # And the curve, which has every window's sampling in it, has no
        # such dip: the stiffness there is the stiffness either side.
        assert softest_beside_it(from_curve) > 0.7

    def test_the_design_says_which_reading_it_used(self):
        drawn, plan, curve = self._a_climb()

        assert design_from_a_pilot(
            drawn, plan, curve=curve)["gradient_from"] == "curve"
        assert design_from_a_pilot(drawn, plan)["gradient_from"] == "windows"

    def test_the_windows_are_still_reported(self):
        """They are evidence about the ground each window sat on, and a
        reader comparing them against the curve is checking the study."""
        drawn, plan, curve = self._a_climb()

        design = design_from_a_pilot(drawn, plan, curve=curve)

        assert [m["window"] for m in design["measured"]] == [0, 1, 2, 3, 4, 5]
        assert all(m["gradient_kjmol_per_unit"] == pytest.approx(200.0, rel=0.1)
                   for m in design["measured"])

    def test_a_curve_with_nothing_on_it_is_refused(self):
        """Bins nobody sampled come back as null, and a curve that is mostly
        null is not a gradient profile."""
        drawn, plan, curve = self._a_climb()
        empty = (curve[0], [None] * len(curve[0]))

        with pytest.raises(ValueError, match="at least three points"):
            design_from_a_pilot(drawn, plan, curve=empty)

    def test_unsampled_bins_are_skipped_rather_than_read_as_zero(self):
        drawn, plan, curve = self._a_climb()
        height = list(curve[1])
        for index in (10, 11, 12):
            height[index] = None

        design = design_from_a_pilot(drawn, plan, curve=(curve[0], height))

        assert design["n_windows"] > 2
        assert max(design["force_constants"]) == pytest.approx(
            200.0 ** 2 / KT, rel=0.2)


class TestItSaysWhereItsEvidenceStops:

    def test_the_readings_stop_short_of_the_far_end(self):
        """A window on a rising surface comes to rest below its centre, so
        the last reading is inside the coordinate the design covers and the
        stretch beyond it is held at the last slope measured. Saying where
        the readings end is the difference between an extrapolation and an
        undeclared one."""
        drawn, plan = a_pilot([0.40, 0.52, 0.64], 3000.0, 200.0, spread=1e-6)

        design = design_from_a_pilot(drawn, plan)

        assert design["covers"] == [0.40, 0.64]
        assert design["measured_over"][1] < design["covers"][1]
        assert design["measured_over"][1] == pytest.approx(0.64 - 200 / 3000,
                                                           abs=1e-3)

    def test_windows_that_slid_past_each_other_are_named(self):
        """Two windows resting in the same place disagree about the slope
        there, and one of them slid to get there. The pilot was too soft to
        resolve that stretch; the design is still the conservative reading of
        it, and it says which windows crossed rather than averaging them
        quietly into a profile."""
        plan = plan_windows({"collective_variable": "distance",
                             "centres": [0.60, 0.66, 0.72],
                             "force_constant": 500.0,
                             "minimum_samples": 50})
        rng = np.random.default_rng(2)
        drawn = {0: 0.58 + 0.001 * rng.standard_normal(4000),
                 1: 0.55 + 0.001 * rng.standard_normal(4000),
                 2: 0.62 + 0.001 * rng.standard_normal(4000)}

        design = design_from_a_pilot(drawn, plan)

        assert design["crossed"] == [1]
        # And still an answer: the steeper of the two readings is the one
        # kept, so the design that follows is the conservative one.
        assert design["worst_predicted_overlap"] > plan.minimum_overlap
        assert min(design["force_constants"]) >= 500.0

    def test_a_pilot_whose_windows_held_says_nothing_crossed(self):
        drawn, plan = a_pilot([0.40, 0.52, 0.64], 13000.0, 200.0)

        assert design_from_a_pilot(drawn, plan)["crossed"] == []


class TestItSaysWhatItCannotDo:

    def test_one_window_is_not_a_pilot(self):
        drawn, plan = a_pilot([0.40, 0.52], 3000.0, 100.0)

        with pytest.raises(ValueError, match="at least two windows"):
            design_from_a_pilot({0: drawn[0]}, plan)

    @pytest.mark.parametrize("gate_used", [0.0, -0.5, 1.5])
    def test_a_gate_that_cannot_be_used_is_refused(self, gate_used):
        drawn, plan = a_pilot([0.40, 0.52], 3000.0, 100.0)

        with pytest.raises(ValueError, match="gate_used"):
            design_from_a_pilot(drawn, plan, gate_used=gate_used)


class TestAStudyThatRanSaysWhatTheNextOneShouldBe:
    """Nobody should have to call this by hand.

    The recombination already has every window's sampling and every window's
    restraint, which is everything the design needs, so it does the sizing
    there and writes it beside the result. A study that refused then arrives
    with the study that will not.
    """

    def _a_study_whose_windows_slid(self, tmp_path):
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "c.yml"
        config.write_text(
            "output: out\n"
            "include: [setup, simulation]\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: distance\n"
            '    selection_a: "name CA"\n'
            '    selection_b: "name CB"\n'
            "    from: 0.4\n"
            "    to: 1.6\n"
            "    n_windows: 7\n"
            "    force_constant: 200\n",
            encoding="utf-8")
        explorer = BatchExplorer(config=config,
                                 output_dir=str(tmp_path / "out"))
        rng = np.random.default_rng(11)
        for spec in explorer.run_specs:
            block = spec.options["simulation"]["umbrella"]
            simulation = explorer._run_output_dir(spec) / "simulation"
            simulation.mkdir(parents=True)
            # Every window a fifth of a nanometre inside its centre: held at
            # 200 and losing to about 40 kJ/mol/nm.
            values = (block["centre"] - 0.2
                      + math.sqrt(KT / block["force_constant"])
                      * rng.standard_normal(3000))
            simulation.joinpath("COLVAR").write_text(
                "#! FIELDS time cv bias\n"
                + "\n".join(f"{i * 0.1:.1f} {v:.6f} 0.0"
                            for i, v in enumerate(values)),
                encoding="utf-8")
        return explorer

    def test_the_design_is_written_beside_the_result(self, tmp_path):
        import json

        explorer = self._a_study_whose_windows_slid(tmp_path)

        explorer._maybe_build_pmf()

        written = json.loads(
            (tmp_path / "out" / "pmf.json").read_text(encoding="utf-8"))
        design = written["next_study"]
        assert len(design["centres"]) == len(design["force_constants"])
        assert min(design["force_constants"]) > 200.0
        assert np.diff(design["centres"]).max() < 0.2

    def test_a_study_that_refused_prints_what_to_run_instead(
            self, tmp_path, capsys):
        explorer = self._a_study_whose_windows_slid(tmp_path)

        explorer._maybe_build_pmf()

        printed = capsys.readouterr().out
        assert "Next study:" in printed
        assert "centres:" in printed and "force_constant:" in printed


class TestTheAnswerIsAStudyAPersonCanRun:

    def test_the_block_is_the_design_and_a_config_reads_it_back(self):
        """What comes out is what goes in: the two settings, in the shape the
        configuration takes them, so the next study is a paste rather than a
        transcription."""
        drawn, plan = a_pilot([0.40, 0.52, 0.64], 3000.0,
                              a_ramp(40.0, 300.0, at=0.60, over=0.03),
                              spread=1e-6)
        design = design_from_a_pilot(drawn, plan)

        block = textwrap.dedent(as_a_config_block(design))
        read_back = yaml.safe_load(block)

        assert read_back["centres"] == pytest.approx(design["centres"])
        assert read_back["force_constant"] == pytest.approx(
            design["force_constants"])

        rebuilt = plan_windows({"collective_variable": "distance", **read_back})
        assert [w.centre for w in rebuilt.windows] == pytest.approx(
            design["centres"])
        assert [w.force_constant for w in rebuilt.windows] == pytest.approx(
            design["force_constants"])
