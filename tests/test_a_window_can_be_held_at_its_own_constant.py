"""One force constant cannot hold a coordinate with a steep stretch.

A restraint at `k` keeps a window within two sigma of its centre against a
gradient of ``2*sqrt(k*kT)``. On the trypsin-benzamidine unbinding coordinate
that number is what decided whether the study existed: the windows between
0.83 and 1.01 nm are losing to 206-360 kJ/mol/nm, and 3000 kJ/mol/nm^2 --
which every window in that study used -- holds against 173. Three of three
sat 2.4 to 4.2 sigma inside their centres and the recombination refused.

Raising it everywhere is not the answer, because sigma falls as
``sqrt(kT/k)``: at 13000 a window is half as wide, so the neighbours it was
sharing a third of its histogram with share almost nothing, and the study
refuses for a gap the stiffening opened.

So the steep windows get their own constant and are placed closer together.
The machinery for that was already right -- `Window` carries its own,
`windows_as_sweep` emits it, `plan_from_expanded` reads it back, WHAM builds
each window's bias from it -- and the config was the only thing that could
not say it.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.simulation.umbrella import (
    expand_umbrella,
    plan_from_expanded,
    plan_windows,
    windows_as_sweep,
)

STEEP = {
    "collective_variable": "distance",
    "centres": [0.83, 0.86, 0.89],
    "force_constant": [3000, 13000, 3000],
}


class TestAListHoldsEachWindowAtItsOwn:

    def test_each_window_gets_the_one_it_was_given(self):
        plan = plan_windows(STEEP)

        assert [w.force_constant for w in plan.windows] == [3000.0,
                                                            13000.0, 3000.0]

    def test_one_number_still_holds_them_all(self):
        """The usual thing to want, and still the usual way to write it."""
        plan = plan_windows(dict(STEEP, force_constant=3000))

        assert {w.force_constant for w in plan.windows} == {3000.0}

    def test_a_list_of_the_wrong_length_is_refused(self):
        with pytest.raises(ValueError, match="2 values for 3 windows"):
            plan_windows(dict(STEEP, force_constant=[3000, 13000]))

    def test_a_restraint_that_does_not_pull_is_refused(self):
        """Zero holds nothing and a negative one pushes the system out of the
        place the window exists to sample."""
        with pytest.raises(ValueError, match="Window 1"):
            plan_windows(dict(STEEP, force_constant=[3000, 0, 3000]))
        with pytest.raises(ValueError, match="Window 1"):
            plan_windows(dict(STEEP, force_constant=[3000, -13000, 3000]))

    def test_text_is_refused_rather_than_iterated(self):
        """A string is a sequence, and "3000" would otherwise become the
        four windows '3', '0', '0', '0' -- or a length complaint about a
        number that is perfectly good."""
        with pytest.raises(ValueError, match="which is text"):
            plan_windows(dict(STEEP, force_constant="3000"))


class TestItSurvivesTheRoundTrip:
    """The config becomes one run per window and is read back from them."""

    def test_each_run_carries_its_own(self):
        expanded = expand_umbrella({
            "output": "runs/x",
            "systems": [{"id": "s", "system": "1abc"}],
            "simulation": {"duration_ns": 5, "umbrella": dict(STEEP)},
        })

        held = [entry["simulation"]["umbrella"]["force_constant"]
                for entry in expanded["systems"]]

        assert held == [3000.0, 13000.0, 3000.0]

    def test_the_plan_comes_back_the_same(self):
        expanded = expand_umbrella({
            "output": "runs/x",
            "systems": [{"id": "s", "system": "1abc"}],
            "simulation": {"duration_ns": 5, "umbrella": dict(STEEP)},
        })

        plan = plan_from_expanded(expanded)

        assert [w.force_constant for w in plan.windows] == [3000.0,
                                                            13000.0, 3000.0]

    def test_the_sweep_carries_it_per_window(self):
        sweep = windows_as_sweep(plan_windows(STEEP))

        assert [row["umbrella_force_constant"] for row in sweep] == [
            3000.0, 13000.0, 3000.0]


class TestTheRecordDoesNotSpeakForWindowsItCannot:
    """`as_record` returned `self.windows[0].force_constant` as the study's.

    True while one value was the only thing a config could express. The
    moment it was not, a study holding its barrier windows four times harder
    would have written the flat one there -- and the harvest, the comparison
    report and the manuscript all read that field.
    """

    def test_a_uniform_study_still_reports_its_constant(self):
        record = plan_windows(dict(STEEP, force_constant=3000)).as_record()

        assert record["force_constant"] == 3000.0

    def test_a_mixed_study_reports_none_rather_than_one_of_them(self):
        record = plan_windows(STEEP).as_record()

        assert record["force_constant"] is None, (
            "Reporting 3000 for a study where a third of the windows are at "
            "13000 is a number that is true of some windows and quoted as "
            "the study's.")

    def test_every_constant_is_recorded_either_way(self):
        assert plan_windows(STEEP).as_record()["force_constants"] == [
            3000.0, 13000.0, 3000.0]
        assert plan_windows(
            dict(STEEP, force_constant=3000)).as_record()[
                "force_constants"] == [3000.0, 3000.0, 3000.0]


class TestTheRecombinationUsesEachWindowsOwn:

    def test_a_free_energy_is_recovered_from_windows_held_differently(self):
        """The physics this exists for, on a surface with a known answer.

        A quadratic well sampled by five windows, the middle three held four
        times harder than the outer two. WHAM builds each window's bias from
        its own constant, so the recovered curve is the well whichever way
        the windows were held.

        Checked against the failure it is for: recombining this same sampling
        with one constant for every window puts the worst point 1.68 kJ/mol
        off the true well, where using each window's own puts it at 0.07. The
        bound below sits between them with room either side, so this is a
        test that can fail rather than one that passes whatever the code does.
        """
        from fastmdxplora.simulation.umbrella import compute_pmf

        kT = 0.008314462618 * 300.0
        curvature = 2000.0            # kJ/mol/nm^2, the true well
        centres = [0.40, 0.45, 0.50, 0.55, 0.60]
        forces = [3000.0, 12000.0, 12000.0, 12000.0, 3000.0]
        plan = plan_windows({"collective_variable": "distance",
                             "centres": centres,
                             "force_constant": forces,
                             "minimum_samples": 50})

        rng = np.random.default_rng(0)
        samples = {}
        for index, (centre, k) in enumerate(zip(centres, forces)):
            # Sampling the biased distribution: a harmonic well of curvature
            # `curvature` about 0.5 plus a restraint of `k` about `centre`
            # is itself harmonic, centred where the two balance.
            total = curvature + k
            middle = (curvature * 0.50 + k * centre) / total
            samples[index] = middle + np.sqrt(kT / total) * rng.standard_normal(
                20000)

        result = compute_pmf(samples, plan, temperature_K=300.0, bins=40)

        assert result["refused"] is None, result["refused"]
        coordinate = np.asarray(result["pmf"]["coordinate"])
        energy = np.asarray(result["pmf"]["free_energy_kjmol"])
        inside = np.abs(coordinate - 0.50) < 0.06
        expected = 0.5 * curvature * (coordinate[inside] - 0.50) ** 2
        recovered = energy[inside] - energy[inside].min()

        assert np.max(np.abs(recovered - (expected - expected.min()))) < 0.5, (
            "The well came back the wrong shape, which is what using one "
            "force constant for windows held at two would do.")
