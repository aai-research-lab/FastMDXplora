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


class TestADriftedWindowSaysWhatWouldHaveHeldIt:
    """A window that slid is a measurement of the surface it slid down.

    It stops where the restraint's pull matches the free energy's, so its
    displacement times its force constant is the gradient there -- the only
    place an umbrella study reports the slope of the surface directly. The
    algebra from that to a force constant is three steps, and the run has
    every input, so it does them rather than leaving them to the reader.
    """

    def _drifted(self, sat_at):
        from fastmdxplora.simulation.umbrella import windows_that_drifted

        centres = [0.896552, 0.951724, 1.006897]
        plan = plan_windows({"collective_variable": "distance",
                             "centres": centres, "force_constant": 3000.0})
        rng = np.random.default_rng(0)
        samples = {i: s + 0.0288 * rng.standard_normal(9000)
                   for i, s in enumerate(sat_at)}
        return windows_that_drifted(samples, plan, 300.0)

    def test_it_reports_the_gradient_it_lost_to(self):
        """3000 kJ/mol/nm^2 times 0.12 nm is 360 kJ/mol/nm, which is what
        the real study measured through this stretch."""
        drifted = self._drifted([0.896552, 0.8318, 1.006897])

        assert len(drifted) == 1
        assert drifted[0]["gradient_kjmol_per_unit"] == pytest.approx(
            360.0, rel=0.02)

    def test_it_reports_the_constant_that_would_have_held_it(self):
        """The number this study then ran at, and at which the same window
        sat 0.06 sigma from its centre.

        The windows here are 0.0552 nm apart, so the gate is 0.0276, and
        3000 x 0.1199 / 0.0276 is 13030 -- which is the 13000 the next study
        used. The recommendation is checked against the run that took it.
        """
        drifted = self._drifted([0.896552, 0.8318, 1.006897])

        assert drifted[0]["force_constant_that_would_hold_it"] == pytest.approx(
            13000.0, rel=0.02)

    def test_the_recommendation_is_sized_against_the_gate_that_failed(self):
        """A window at the recommended constant, against the same gradient,
        comes to rest exactly at the gate it broke. That is the definition
        the flag uses, so it is the one the remedy has to answer.

        The first version sized to two sigma instead. Two sigma is a looser
        test wherever windows sit closer than four sigma apart, and on a
        study 0.06 nm apart at 3000 it returned 1584 -- softer than the
        constant that had just failed, printed under advice to hold the
        windows harder.
        """
        centres = [0.896552, 0.951724, 1.006897]
        allowed = 0.5 * (centres[1] - centres[0])

        drifted = self._drifted([0.896552, 0.8318, 1.006897])[0]
        needed = drifted["force_constant_that_would_hold_it"]
        would_sit = drifted["gradient_kjmol_per_unit"] / needed

        assert would_sit == pytest.approx(allowed, rel=1e-6)

    def test_it_never_recommends_a_softer_constant(self):
        """The whole point of the advice is to hold the window harder.

        A window 0.042 nm off centre at 3000, with windows 0.06 nm apart,
        is inside two sigma and outside the gate -- the case the old
        arithmetic answered with 1584.
        """
        from fastmdxplora.simulation.umbrella import _what_would_hold_it

        answer = _what_would_hold_it(3000.0, 0.0419, 0.030)

        assert answer["force_constant_that_would_hold_it"] > 3000.0
        assert answer["force_constant_that_would_hold_it"] == pytest.approx(
            4190.0, rel=0.01)

    def test_the_spacing_keeps_the_neighbours_overlapping(self):
        """Two and a half sigma at the new constant, which leaves about a
        fifth of two histograms shared. Recommending a stiffer window without
        closing the gaps trades this refusal for a gap the stiffening made."""
        import math

        from fastmdxplora.simulation.umbrella import (
            KB_KJ, _what_would_hold_it)

        answer = _what_would_hold_it(3000.0, 0.1199, 0.0276)
        needed = answer["force_constant_that_would_hold_it"]

        assert answer["spacing_it_would_need"] == pytest.approx(
            2.5 * math.sqrt(KB_KJ * 300.0 / needed))
        # The design the next study actually ran.
        assert answer["spacing_it_would_need"] == pytest.approx(0.0346,
                                                                abs=5e-4)

    def test_a_window_at_its_centre_is_not_in_the_list(self):
        assert self._drifted([0.896552, 0.951724, 1.006897]) == []


class TestTheRefusalCarriesTheNumbers:

    def _refusal(self):
        from fastmdxplora.simulation.umbrella import compute_pmf

        centres = [0.896552, 0.951724, 1.006897]
        plan = plan_windows({"collective_variable": "distance",
                             "centres": centres, "force_constant": 3000.0,
                             "minimum_samples": 100})
        rng = np.random.default_rng(0)
        samples = {i: s + 0.0288 * rng.standard_normal(9000)
                   for i, s in enumerate([0.8281, 0.8318, 1.0069])}
        return compute_pmf(samples, plan, temperature_K=300.0)["refused"]

    def test_it_names_a_force_constant(self):
        """Rather than "hold them harder", which is what it used to say and
        which leaves the reader to do the algebra this run has the inputs
        for."""
        refused = self._refusal()

        assert "needs k" in refused
        assert "12" in refused or "13" in refused

    def test_it_says_the_spacing_has_to_change_too(self):
        """The half of the answer that is easy to miss. A stiffer window is
        a narrower one, so raising the constant alone trades a refusal for
        drift for a refusal for a gap the stiffening opened."""
        refused = self._refusal()

        assert "at spacing" in refused
        assert "not optional" in refused

    def test_it_says_both_settings_take_a_list(self):
        """Otherwise the reader has the numbers and no way to write them."""
        refused = self._refusal()

        assert "`force_constant` and `centres` both take a list" in refused

    def test_it_still_says_to_check_the_seeding_first(self):
        """A window that began somewhere else is not measuring a gradient,
        and a force constant sized from its displacement would be sized from
        a starting position."""
        refused = self._refusal()

        assert "seed them from a steered run" in refused
        assert refused.index("seed them") < refused.index("needs k")


class TestAStudyThatPassesStillSaysWhatItsWindowsDid:
    """`drifted` and `thin` appeared only in a refusal.

    So a study that cleared the overlap gate reported nothing about whether
    its windows sat where they were put -- and windows drift *together*.
    Three held at 0.897, 0.952 and 1.007 all came to rest near 0.83 in a real
    run: they overlap each other beautifully, the gate passes, and the free
    energy that comes out is a confident measurement of a stretch of
    coordinate none of them was asked to sample.
    """

    def _all_slid_to_the_same_place(self):
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = plan_windows({"collective_variable": "distance",
                             "centres": [0.896552, 0.951724, 1.006897],
                             "force_constant": 3000.0,
                             "minimum_samples": 100})
        rng = np.random.default_rng(0)
        samples = {i: s + 0.0288 * rng.standard_normal(9000)
                   for i, s in enumerate([0.828, 0.832, 0.836])}
        return compute_pmf(samples, plan, temperature_K=300.0,
                           bootstrap_resamples=0)

    def test_the_gate_passes_because_they_drifted_together(self):
        """Establishing the premise: this is not caught by the overlap check,
        which is why it has to be reported separately."""
        assert self._all_slid_to_the_same_place()["refused"] is None

    def test_it_names_them_anyway(self):
        result = self._all_slid_to_the_same_place()

        assert [d["window"] for d in result["drifted"]] == [0, 1, 2]

    def test_covered_is_where_they_were_put_not_where_they_went(self):
        """The field a reader takes the range of the result from. It names
        the centres, and with every window a tenth of a nanometre inside them
        that is the thing `drifted` exists to qualify."""
        result = self._all_slid_to_the_same_place()

        assert result["covered"] == [0.896552, 1.006897]
        assert all(d["sampled_at"] < 0.84 for d in result["drifted"])

    def test_it_warns_rather_than_only_recording(self, caplog):
        """A field in a JSON file is not a warning. Somebody watching the run
        finish should be told that its windows went somewhere else."""
        import logging

        with caplog.at_level(logging.WARNING):
            self._all_slid_to_the_same_place()

        said = " ".join(record.getMessage() for record in caplog.records)

        assert "sampled away from their centres" in said
        assert "overlaps still passed" in said

    def test_thin_is_carried_on_a_passing_study_too(self):
        result = self._all_slid_to_the_same_place()

        assert "thin" in result


class TestTheRecombinationIsGivenRoomToFinish:
    """The iteration ceiling was 2000, and a real study needed 4838.

    Direct WHAM converges linearly and how many passes that takes grows with
    the number of windows and the range they span. A thirty-six window study
    of a ligand leaving a pocket stopped at a residual of about 1e-3 and
    recorded `converged: false` -- on a free energy whose remaining movement
    was five ten-thousandths of a kT, in a solve that takes under a second.
    The ceiling was buying nothing and costing the one field that says
    whether the answer is finished.
    """

    def _a_study_of(self, n_windows):
        from fastmdxplora.simulation.umbrella import KB_KJ, compute_pmf

        kT = KB_KJ * 300.0
        centres = list(np.linspace(0.40, 2.00, n_windows))
        forces = [3000.0] * n_windows
        plan = plan_windows({"collective_variable": "distance",
                             "centres": centres, "force_constant": forces,
                             "minimum_samples": 100})
        rng = np.random.default_rng(4)

        def surface(x):
            return (-28 * np.exp(-((x - 0.45) / 0.13) ** 2)
                    + 9 * np.exp(-((x - 0.93) / 0.09) ** 2)
                    - 2 * kT * np.log(np.clip(x, 0.05, None)))

        samples = {}
        for index, (centre, k) in enumerate(zip(centres, forces)):
            grid = np.linspace(centre - 0.14, centre + 0.14, 3001)
            weight = np.exp(-(surface(grid) + 0.5 * k * (grid - centre) ** 2)
                            / kT)
            samples[index] = rng.choice(grid, size=8000,
                                        p=weight / weight.sum())
        return compute_pmf(samples, plan, temperature_K=300.0,
                           bootstrap_resamples=0)

    def test_a_study_with_many_windows_converges(self):
        """The case that did not, at the old ceiling."""
        result = self._a_study_of(36)

        assert result["refused"] is None, result["refused"]
        assert result["converged"] is True
        assert result["final_residual_kjmol"] < 1e-6

    def test_it_reports_how_many_passes_it_took(self):
        """A study needing tens of thousands is saying something about its
        conditioning that a bare `converged: true` hides."""
        result = self._a_study_of(36)

        assert result["wham_iterations"] > 1
        assert result["wham_iterations"] < 100_000

    def test_the_ceiling_is_well_clear_of_what_a_real_study_needs(self):
        """So that reaching it means the iteration is not converging, rather
        than that it ran out of room."""
        from fastmdxplora.simulation.umbrella import WHAM_MAX_ITERATIONS

        assert WHAM_MAX_ITERATIONS >= 20 * 4838
