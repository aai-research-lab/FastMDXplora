"""A window on a distance leaves the ligand free on a sphere it never covers.

The recombination's bulk reference is ``-2kT ln r``, and it says only that the
room at radius r is ``4 pi r^2``. Two things have to be true for that to be
the reference a binding free energy is measured against: the whole sphere has
to be available, and the ligand has to visit it.

On a real study neither was. The shell 1 nm from the S1 site of trypsin is 12%
outside the protein and 50% at 2 nm -- the coordinate's origin is a centroid
of backbone atoms, which is buried -- so the room grows as r^4.3 rather than
r^2. And one window's ligand visited between a twentieth and a third of what
was open to it in ten nanoseconds, because a sphere of radius 2 nm has 50 nm^2
of surface and a ligand held there diffuses across a few of them.

A flat-bottomed wall on the angle fixes both at once. The ligand is confined
to a cap of solid angle ``Omega``, whose area is ``Omega r^2`` -- still
proportional to r^2, so the reference is right by construction -- and at 30
degrees the cap is a fifteenth of a sphere, small enough to cover.

What it costs is a term: the bulk state under a cone is ``4 pi / Omega``
smaller than a free ligand's, while a bound state that fits inside the cone
loses nothing, so a binding free energy measured this way is too negative by
``kT ln(4 pi / Omega)``.
"""

from __future__ import annotations

import math

import pytest

from fastmdxplora.simulation.umbrella import (
    Cone,
    Window,
    cone_from_config,
    expand_umbrella,
    plan_from_expanded,
    plan_windows,
)

KB_KJ = 0.008314462618
KT = KB_KJ * 300.0
CV = ["lig: COM ATOMS=1,2", "site: COM ATOMS=3,4",
      "cv: DISTANCE ATOMS=lig,site"]


def hard_cap(half_angle_deg: float) -> float:
    """The solid angle of a cone with a cliff for a wall."""
    return 2.0 * math.pi * (1.0 - math.cos(math.radians(half_angle_deg)))


class TestTheCapIsMeasuredRatherThanAssumed:

    @pytest.mark.parametrize("half_angle", [15.0, 30.0, 45.0, 60.0, 90.0])
    def test_a_wall_stiff_enough_to_be_a_cliff_gives_the_textbook_cap(
            self, half_angle):
        """`2 pi (1 - cos theta)`, which is what the correction is usually
        written against."""
        cone = Cone(half_angle_deg=half_angle, force_constant=1e7)

        assert cone.solid_angle() == pytest.approx(hard_cap(half_angle),
                                                   rel=0.01)

    def test_a_wall_a_ligand_can_lean_on_holds_a_bigger_cap(self):
        """The wall is quadratic, not a cliff, so the ligand spends some of
        its time past the edge and the cap is that much larger. At 5000
        kJ/mol/rad^2 it reaches about three degrees beyond 30, which is 7%
        more area -- and this number enters the answer as a logarithm, so it
        is integrated rather than ignored."""
        firm = Cone(half_angle_deg=30.0, force_constant=5000.0)

        assert firm.solid_angle() > hard_cap(30.0)
        assert firm.solid_angle() == pytest.approx(0.905, abs=0.005)

    def test_a_softer_wall_holds_a_bigger_cap_still(self):
        soft = Cone(half_angle_deg=30.0, force_constant=500.0)
        firm = Cone(half_angle_deg=30.0, force_constant=5000.0)

        assert soft.solid_angle() > firm.solid_angle()

    def test_the_whole_sphere_is_the_limit(self):
        """A cone of 180 degrees restrains nothing, and its correction is
        nothing. The arithmetic has to say so, or every study without a cone
        and every study with a wide one disagree about the same state."""
        everything = Cone(half_angle_deg=179.9, force_constant=1e7)

        assert everything.solid_angle() == pytest.approx(4.0 * math.pi,
                                                         rel=0.01)
        assert everything.correction_kjmol() == pytest.approx(0.0, abs=0.05)


class TestWhatItCosts:

    def test_the_correction_is_the_room_the_bulk_state_gave_up(self):
        cone = Cone(half_angle_deg=30.0, force_constant=5000.0)

        assert cone.correction_kjmol() == pytest.approx(
            KT * math.log(4 * math.pi / cone.solid_angle()))
        assert cone.correction_kjmol() == pytest.approx(6.56, abs=0.05)

    def test_a_narrower_cone_costs_more(self):
        """Tighter is cheaper to sample and further from the free state, and
        the correction is what carries the difference. Since the answer must
        not depend on the angle, this is the term the check tests."""
        corrections = [Cone(half_angle_deg=deg).correction_kjmol()
                       for deg in (20, 30, 45, 60)]

        assert corrections == sorted(corrections, reverse=True)
        assert corrections[0] - corrections[-1] == pytest.approx(5.1, abs=0.3)

    def test_it_is_a_temperature_the_study_sets(self):
        cone = Cone(half_angle_deg=30.0)

        assert cone.correction_kjmol(400.0) > cone.correction_kjmol(300.0)


class TestTheRunCarriesIt:

    def test_the_wall_is_where_the_cone_says(self):
        """The angle at the site between the axis group and the ligand is pi
        when the ligand is straight out, so the cone is a lower wall."""
        cone = Cone(half_angle_deg=30.0, force_constant=5000.0)
        script = Window(index=0, centre=0.9,
                        force_constant=13000.0).plumed_lines(
            CV + cone.plumed_lines([7, 8, 9]), cone=cone)

        assert "LOWER_WALLS ARG=cone_angle" in script
        assert f"AT={math.pi - math.radians(30.0):.6f}" in script
        assert "KAPPA=5000" in script

    def test_the_angle_and_the_wall_are_written_down(self):
        """The correction is only valid if the wall never bit in the bound
        state. That is a measurement, and it needs the column."""
        cone = Cone(half_angle_deg=30.0)
        script = Window(index=0, centre=0.9,
                        force_constant=13000.0).plumed_lines(
            CV + cone.plumed_lines([7, 8, 9]), cone=cone)

        printed = [line for line in script.splitlines()
                   if line.startswith("PRINT")][0]
        assert "cone_angle" in printed
        assert "cone.bias" in printed

    def test_a_study_without_one_has_neither(self):
        script = Window(index=0, centre=0.9,
                        force_constant=13000.0).plumed_lines(CV)

        assert "cone" not in script

    def test_the_axis_is_a_group_of_atoms_not_a_direction(self):
        """A direction fixed in the laboratory would not follow the protein
        as it tumbles. The axis is the line from a group's centre to the
        site, so it turns with the molecule."""
        lines = Cone(half_angle_deg=30.0).plumed_lines([7, 8, 9])

        assert lines[0].startswith("cone_axis: COM ATOMS=")
        assert lines[1] == "cone_angle: ANGLE ATOMS=cone_axis,site,lig"


class TestAConfigSaysIt:

    def _plan(self, cone=None):
        spec = {"collective_variable": "ligand_distance",
                "centres": [0.4, 0.5, 0.6], "force_constant": 3000.0}
        if cone is not None:
            spec["cone"] = cone
        return plan_windows(spec)

    def test_the_plan_carries_it(self):
        plan = self._plan({"half_angle_deg": 30})

        assert plan.cone is not None
        assert plan.cone.half_angle_deg == 30.0
        assert plan.cone.axis_selection == "protein"

    def test_a_study_without_one_says_so_rather_than_defaulting(self):
        """A cone changes what the number means, so it is never on unless it
        was asked for."""
        assert self._plan().cone is None
        assert self._plan().as_record()["cone"] is None

    def test_the_record_carries_what_the_correction_will_be(self):
        record = self._plan({"half_angle_deg": 30}).as_record()["cone"]

        assert record["share_of_a_sphere"] == pytest.approx(0.072, abs=0.002)
        assert record["correction_kjmol"] == pytest.approx(6.56, abs=0.05)

    def test_every_window_runs_under_the_same_cone(self):
        """It is a property of the coordinate, not of a position on it."""
        expanded = expand_umbrella({
            "systems": [{"system": "x.pdb"}],
            "simulation": {"umbrella": {
                "collective_variable": "ligand_distance",
                "centres": [0.4, 0.5, 0.6], "force_constant": 3000.0,
                "cone": {"half_angle_deg": 25, "force_constant": 4000},
            }}})

        cones = [entry["simulation"]["umbrella"]["cone"]
                 for entry in expanded["systems"]]
        assert cones == [{"half_angle_deg": 25, "force_constant": 4000}] * 3

        rebuilt = plan_from_expanded(expanded)
        assert rebuilt.cone == Cone(half_angle_deg=25.0, force_constant=4000.0)

    @pytest.mark.parametrize("given,complaint", [
        ({"half_angle_deg": 0}, "between 0 and 180"),
        ({"half_angle_deg": 200}, "between 0 and 180"),
        ({"half_angle_deg": 30, "force_constant": 0}, "positive"),
        ({"force_constant": 5000}, "needs a `half_angle_deg`"),
        ({"half_angle_deg": 30, "kappa": 5000}, "also given"),
    ])
    def test_what_it_refuses(self, given, complaint):
        with pytest.raises(ValueError, match=complaint):
            cone_from_config(given)

    def test_nothing_is_not_a_cone(self):
        assert cone_from_config(None) is None
        assert cone_from_config({}) is None


class TestWhatTheCorrectionRestsOn:
    """A bulk state that gave up room, and a bound state that did not.

    The correction is the difference between those two, so it is valid only
    while the second half is true. A cone narrow enough to cut the bound pose
    has removed part of the population the binding integral is over, and no
    analytic term puts that back -- so the wall's bias in the bound windows is
    a measurement the answer depends on.
    """

    @staticmethod
    def _a_well(depth=12.0, edge=0.5):
        import numpy as np

        radius = np.linspace(0.3, 2.0, 60)
        potential = np.where(radius < edge, -depth, 0.0)
        energy = potential - 2.0 * KT * np.log(radius)
        return radius, energy - energy.min()

    def test_the_correction_is_added_and_both_numbers_are_shown(self):
        from fastmdxplora.simulation.binding import binding_free_energy

        radius, energy = self._a_well()
        plain = binding_free_energy(radius, energy)
        caged = binding_free_energy(radius, energy,
                                    cone=Cone(half_angle_deg=30.0),
                                    wall_bias_kjmol=0.0)

        assert caged["delta_g_before_the_cone_kjmol"] == pytest.approx(
            plain["delta_g_kjmol"])
        assert caged["delta_g_kjmol"] == pytest.approx(
            plain["delta_g_kjmol"] + caged["cone_correction_kjmol"])
        assert caged["cone_correction_kjmol"] == pytest.approx(6.56, abs=0.05)

    def test_a_wall_that_bit_the_bound_state_is_refused(self):
        from fastmdxplora.simulation.binding import binding_free_energy

        radius, energy = self._a_well()
        answer = binding_free_energy(radius, energy,
                                     cone=Cone(half_angle_deg=30.0),
                                     wall_bias_kjmol=2.0)

        assert answer["delta_g_kjmol"] is None
        assert "bites in the bound state" in answer["refused"]
        assert "Widen `half_angle_deg`" in answer["refused"]

    def test_an_unchecked_wall_says_so_rather_than_passing_quietly(self):
        from fastmdxplora.simulation.binding import binding_free_energy

        radius, energy = self._a_well()
        answer = binding_free_energy(radius, energy,
                                     cone=Cone(half_angle_deg=30.0))

        assert answer["delta_g_kjmol"] is not None
        assert answer["cone_wall_checked"] is False

    def test_a_study_without_a_cone_carries_none_of_this(self):
        from fastmdxplora.simulation.binding import binding_free_energy

        radius, energy = self._a_well()
        answer = binding_free_energy(radius, energy)

        assert answer["cone"] is None
        assert answer["cone_correction_kjmol"] is None
        assert answer["cone_wall_checked"] is None

    def test_the_record_on_disk_serves_as_well_as_the_object(self):
        """A study recombining from what its runs wrote has the record, not
        the object that made it."""
        from fastmdxplora.simulation.binding import binding_free_energy

        radius, energy = self._a_well()
        cone = Cone(half_angle_deg=30.0)
        from_object = binding_free_energy(radius, energy, cone=cone,
                                          wall_bias_kjmol=0.0)
        from_record = binding_free_energy(radius, energy,
                                          cone=cone.as_record(),
                                          wall_bias_kjmol=0.0)

        # To within the rounding the record carries: it writes the
        # correction to four decimals, which is four more than a free energy
        # is ever quoted to.
        assert from_record["delta_g_kjmol"] == pytest.approx(
            from_object["delta_g_kjmol"], abs=1e-3)

    def test_the_wall_is_read_off_the_windows_that_hold_the_bound_state(
            self, tmp_path):
        """From the column the run writes, discarding what the study
        discards, and taking the worst window rather than the average of
        them: one window with the wall against it is enough to have removed
        population from the integral."""
        import numpy as np

        from fastmdxplora.simulation.umbrella import (
            wall_bias_where_the_bound_state_is,
        )

        plan = plan_windows({"collective_variable": "ligand_distance",
                             "centres": [0.40, 0.50, 1.60],
                             "force_constant": 3000.0,
                             "cone": {"half_angle_deg": 30}})
        directories = {}
        for index, bias in enumerate((0.0, 1.7, 9.0)):
            run = tmp_path / f"window-{index}" / "simulation"
            run.mkdir(parents=True)
            rows = "\n".join(
                f"{i * 0.2:.1f} 0.5 1.0 2.9 {bias:.3f}" for i in range(100))
            run.joinpath("COLVAR").write_text(
                "#! FIELDS time cv restraint.bias cone_angle cone.bias\n"
                + rows, encoding="utf-8")
            directories[index] = run.parent

        # The bound state ends at 0.9: the far window is not part of it, and
        # its wall is against the stops.
        worst = wall_bias_where_the_bound_state_is(directories, plan, 0.9)

        assert worst == pytest.approx(1.7)
        assert np.isclose(
            wall_bias_where_the_bound_state_is(directories, plan, 0.45), 0.0)

    def test_a_run_with_no_such_column_reports_nothing_rather_than_zero(
            self, tmp_path):
        """Zero would say the wall was quiet. Nothing says nobody looked."""
        from fastmdxplora.simulation.umbrella import (
            wall_bias_where_the_bound_state_is,
        )

        plan = plan_windows({"collective_variable": "ligand_distance",
                             "centres": [0.40, 0.50, 1.60],
                             "force_constant": 3000.0})
        run = tmp_path / "window-0" / "simulation"
        run.mkdir(parents=True)
        run.joinpath("COLVAR").write_text(
            "#! FIELDS time cv restraint.bias\n0.0 0.4 0.1\n", encoding="utf-8")

        assert wall_bias_where_the_bound_state_is(
            {0: run.parent}, plan, 0.9) is None
