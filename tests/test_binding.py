"""A binding free energy, checked against a well whose answer is analytic."""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.simulation.binding import (
    KB_KJMOL,
    STANDARD_VOLUME_NM3,
    binding_free_energy,
)

TEMPERATURE = 300.0
KT = KB_KJMOL * TEMPERATURE


def _square_well(depth_kjmol: float, width_nm: float,
                 points: int = 400, reach_nm: float = 3.0):
    """A PMF with a closed-form binding constant.

    The true potential of mean force is a square well of the given depth.
    What umbrella recombination reports is the free energy of the radial
    distribution, which carries the shell's volume element, so the curve
    handed to the calculation is W(r) - 2kT ln r.
    """
    radius = np.linspace(0.05, reach_nm, points)
    potential = np.where(radius <= width_nm, -depth_kjmol, 0.0)
    return radius, potential - 2.0 * KT * np.log(radius)


def _exact(depth_kjmol: float, width_nm: float) -> float:
    constant = (4.0 / 3.0) * np.pi * width_nm ** 3 * np.exp(
        depth_kjmol / KT)
    return -KT * float(np.log(constant / STANDARD_VOLUME_NM3))


class TestAgainstAWellWithAKnownAnswer:
    @pytest.mark.parametrize(
        "depth,width", [(20.0, 0.6), (12.0, 0.5), (30.0, 0.8), (8.0, 0.4)])
    def test_it_recovers_the_closed_form(self, depth, width):
        radius, energy = _square_well(depth, width)
        result = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE,
            bound_cutoff_nm=width)
        assert result["delta_g_kjmol"] == pytest.approx(
            _exact(depth, width), abs=0.5)

    def test_the_jacobian_is_not_applied_twice(self):
        """The failure this test exists for, priced.

        The shell's 4 pi r^2 is already inside a histogram-recombined free
        energy, so an integrand that carries one as well counts the
        translational entropy twice. Written that way, a 20 kJ/mol well came
        out 10.5 kJ/mol too favourable, which is a plausible-looking number
        and the size of the effect anyone would be trying to measure.
        """
        depth, width = 20.0, 0.6
        radius, energy = _square_well(depth, width)
        correct = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE,
            bound_cutoff_nm=width)["delta_g_kjmol"]

        # The double-counted form, written out.
        inside = radius <= width
        reference = float(np.mean(energy[-100:] + 2.0 * KT * np.log(
            radius[-100:])))
        doubled = float(np.trapezoid(
            np.exp(-(energy[inside] - reference
                     + 2.0 * KT * np.log(radius[inside])) / KT),
            radius[inside]))
        wrong = -KT * np.log(
            4.0 * np.pi * radius[-1] ** 2 * doubled / STANDARD_VOLUME_NM3)

        assert abs(wrong - correct) > 5.0, (
            "the two conventions must differ, or this test proves nothing")
        assert correct == pytest.approx(_exact(depth, width), abs=0.5)

    def test_kcal_agrees_with_kj(self):
        radius, energy = _square_well(20.0, 0.6)
        result = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6)
        assert result["delta_g_kcalmol"] == pytest.approx(
            result["delta_g_kjmol"] / 4.184)


class TestWhatItRefuses:
    def test_a_run_that_never_reached_bulk(self):
        """The dangerous case, because it succeeds at everything else.

        Windows stopping inside the interaction still recombine into a
        smooth curve and still yield a number. The number is wrong by
        however much of the well was left outside, and nothing about the
        curve says so.
        """
        radius = np.linspace(0.05, 0.7, 200)
        potential = -20.0 * np.exp(-((radius - 0.3) / 0.15) ** 2)
        energy = potential - 2.0 * KT * np.log(radius)

        result = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE)
        assert result["delta_g_kjmol"] is None
        assert "-2kT ln r" in result["refused"]
        assert result["bulk_residual_kjmol"] > 0.6

    def test_a_coordinate_through_the_origin(self):
        radius = np.linspace(-0.5, 2.0, 100)
        result = binding_free_energy(
            radius, np.zeros_like(radius), temperature_K=TEMPERATURE)
        assert result["delta_g_kjmol"] is None
        assert "origin" in result["refused"]

    def test_too_few_points_to_integrate(self):
        result = binding_free_energy(
            np.linspace(0.1, 1.0, 5), np.zeros(5), temperature_K=TEMPERATURE)
        assert result["delta_g_kjmol"] is None
        assert "too few" in result["refused"]


class TestWhatItReportsBesideTheNumber:
    def test_the_cutoff_sensitivity_travels_with_the_value(self):
        """Where the bound state ends is a definition, not a measurement.

        A deep narrow well barely moves when the cutoff does; a shallow one
        moves a great deal. Reporting the spread is what tells a reader
        which of the two they are looking at.
        """
        deep = binding_free_energy(
            *_square_well(30.0, 0.6), temperature_K=TEMPERATURE)
        shallow = binding_free_energy(
            *_square_well(4.0, 0.6), temperature_K=TEMPERATURE)

        assert deep["cutoff_sensitivity_kjmol"] is not None
        assert shallow["cutoff_sensitivity_kjmol"] is not None
        assert (shallow["cutoff_sensitivity_kjmol"]
                > deep["cutoff_sensitivity_kjmol"])

    def test_the_convention_is_stated(self):
        result = binding_free_energy(
            *_square_well(20.0, 0.6), temperature_K=TEMPERATURE)
        assert "twice" in result["convention"]
        assert result["standard_volume_nm3"] == pytest.approx(1.6605, abs=1e-3)

    def test_the_chosen_cutoff_says_how_it_was_chosen(self):
        given = binding_free_energy(
            *_square_well(20.0, 0.6), temperature_K=TEMPERATURE,
            bound_cutoff_nm=0.65)
        assert given["bound_cutoff_nm"] == pytest.approx(0.65)
        assert given["bound_cutoff_chosen_by"] == "given"

        automatic = binding_free_energy(
            *_square_well(20.0, 0.6), temperature_K=TEMPERATURE)
        assert "kT of bulk" in automatic["bound_cutoff_chosen_by"]


class TestItSitsBehindTheOverlapGate:
    """A binding free energy is the most quotable thing a study produces.

    Integrated over a curve stitched across a gap it would also be the
    least supported, so it is computed only where a PMF was computed, and
    the PMF is what the overlap and sampling gates decide.
    """

    def rule(self):
        from fastmdxplora.batch.explorer import (
            _a_binding_free_energy_belongs_here,
        )

        return _a_binding_free_energy_belongs_here

    def a_plan(self, coordinate):
        from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

        return UmbrellaPlan(
            windows=(Window(index=0, centre=0.4, force_constant=3000.0),
                     Window(index=1, centre=0.6, force_constant=3000.0)),
            collective_variable=coordinate, equilibration_fraction=0.2,
            minimum_overlap=0.03, minimum_samples=200)

    def test_a_refused_study_gets_no_number(self):
        """The overlap and sampling gates decide whether a curve exists, and
        the number is taken from the curve."""
        for refused in ({"pmf": None, "refused": "no overlap"}, {}):
            assert not self.rule()(refused, self.a_plan("ligand_distance"))

    def test_a_study_with_a_curve_gets_one(self):
        assert self.rule()({"pmf": {"coordinate": [0.4]}},
                           self.a_plan("ligand_distance"))
        assert self.rule()({"pmf": {"coordinate": [0.4]}},
                           self.a_plan("distance"))

    def test_it_is_not_attempted_along_a_torsion(self):
        """The standard state is about the volume a ligand gives up.

        Along a torsion there is no volume and no concentration, so the
        correction has nothing to correct and the number would be a
        category error rather than an inaccuracy.
        """
        for coordinate in ("torsion", "radius_of_gyration", "q", "angle"):
            assert not self.rule()({"pmf": {"coordinate": [0.4]}},
                                   self.a_plan(coordinate))

    def test_the_campaign_uses_the_rule_rather_than_repeating_it(self):
        """Both places the decision is made go through the one predicate, so
        the gate and the error bar cannot come to disagree about which studies
        get a binding free energy.

        Read from the source on purpose. What the predicate decides is run
        by the tests above; this asks only that the campaign calls it rather
        than keeping a second copy of the rule, which a run could not tell
        apart until the two copies differed."""
        import inspect

        from fastmdxplora.batch import explorer

        source = inspect.getsource(
            explorer.BatchExplorer._maybe_build_pmf)
        assert "_a_binding_free_energy_belongs_here(" in source
        assert "_the_coordinate_has_a_volume(" in source
        assert '"ligand_distance"' not in source
