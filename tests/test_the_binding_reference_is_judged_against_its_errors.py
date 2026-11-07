"""The binding free energy's bulk check and cone wall, on curves with exact answers.

The bulk check judges A(r) + 2kT ln r, a constant in bulk (Gilson et al.
1997, Biophys. J. 72, 1047), on the tail: each point's departure from the
tail's mean against the larger of 0.6 kJ/mol and Sidak's multiplier (Sidak
1967, J. Am. Stat. Assoc. 62, 626) times that departure's spread over the
bootstrap's resampled curves, and the tail's least-squares tilt against the
larger of 0.6 kJ/mol and 2.24 times its spread over the same resamples,
2.5% each. The wall is judged in every window up to the cutoff the integral
uses, and a window whose bias is not a number is never read as quiet.
"""

from __future__ import annotations

import logging
from statistics import NormalDist

import numpy as np
import pytest

from fastmdxplora.refusals import StudyError
from fastmdxplora.simulation.binding import (
    KB_KJMOL,
    STANDARD_VOLUME_NM3,
    binding_free_energy,
)

TEMPERATURE = 300.0
KT = KB_KJMOL * TEMPERATURE
POINTS = 400
TAIL = POINTS // 4


def _square_well(depth: float = 20.0, width: float = 0.6):
    """W(r) a square well, handed on as the distribution's A = W - 2kT ln r."""
    radius = np.linspace(0.05, 3.0, POINTS)
    potential = np.where(radius <= width, -depth, 0.0)
    return radius, potential - 2.0 * KT * np.log(radius)


def _with_a_ripple(energy: np.ndarray, size: float) -> np.ndarray:
    """The tail moved by +size and -size in turn: its mean is unchanged and
    every point departs from it by exactly `size`, with no tilt to speak of."""
    rippled = energy.copy()
    rippled[-TAIL:] += size * np.where(np.arange(TAIL) % 2 == 0, 1.0, -1.0)
    return rippled


def _with_a_tilt(radius: np.ndarray, energy: np.ndarray, size: float):
    """The tail falling linearly in r by exactly `size` across its length."""
    tilted = energy.copy()
    tail = radius[-TAIL:]
    tilted[-TAIL:] -= size * (tail - tail[0]) / (tail[-1] - tail[0])
    return tilted


def _resamples(energy: np.ndarray, *, shared: float, own: float,
               count: int = 200, seed: int = 3) -> np.ndarray:
    """Resampled curves about `energy`: an offset common to every bin (the
    noise of the bin a curve is zeroed at) and noise of each bin's own."""
    rng = np.random.default_rng(seed)
    return (energy + rng.normal(0.0, shared, (count, 1))
            + rng.normal(0.0, own, (count, energy.size)))


def _sidak(count: int, chance: float) -> float:
    each = 1.0 - (1.0 - chance) ** (1.0 / count)
    return NormalDist().inv_cdf(1.0 - each / 2.0)


class TestTheTailIsJudgedAgainstItsResamples:
    def test_a_ripple_inside_large_per_bin_errors_but_past_its_spread_refuses(
            self):
        """Each per-bin error is about 3 kJ/mol, nearly all of it the shared
        offset; the departures' own spread is 0.2. A 1.0 kJ/mol ripple is
        past z * 0.2 (about 0.75) and is refused."""
        radius, energy = _square_well()
        rippled = _with_a_ripple(energy, 1.0)
        curves = _resamples(rippled, shared=3.0, own=0.2)
        per_bin = curves.std(axis=0, ddof=1)
        assert per_bin[-TAIL:].min() > 2.5

        result = binding_free_energy(
            radius, rippled, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            errors_kjmol=per_bin, resample_curves_kjmol=curves)

        tail = curves[:, -TAIL:] + 2.0 * KT * np.log(radius[-TAIL:])
        spread = (tail - tail.mean(axis=1, keepdims=True)).std(axis=0, ddof=1)
        allowed = np.maximum(0.6, _sidak(TAIL, 0.025) * spread)
        assert allowed.max() < 1.0
        assert result["delta_g_kjmol"] is None
        assert result["bulk_judged_by"] == "the bootstrap's resampled curves"
        assert result["bulk_resamples_used"] == 200
        assert result["bulk_allowed_kjmol"] == pytest.approx(
            allowed[int(np.argmax(1.0 - allowed))])
        assert "resampled curves" in result["refused"]

    def test_a_ripple_inside_its_spread_is_bulk_and_the_number_unmoved(self):
        radius, energy = _square_well()
        rippled = _with_a_ripple(energy, 1.0)
        result = binding_free_energy(
            radius, rippled, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            resample_curves_kjmol=_resamples(rippled, shared=3.0, own=0.6))
        plain = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                    bound_cutoff_nm=0.6)

        assert result["refused"] is None
        assert result["bulk_allowed_kjmol"] > 1.0
        assert result["delta_g_kjmol"] == pytest.approx(
            plain["delta_g_kjmol"], abs=1e-9)

    def test_a_tilt_the_departures_allow_is_refused_by_the_tilt(self):
        """2 kJ/mol across the tail departs by at most 1.0 at its ends,
        inside the departures' allowance at 0.5 kJ/mol noise (about 1.8);
        the least-squares tilt is exactly 2.0, against about 0.6 allowed."""
        radius, energy = _square_well()
        tilted = _with_a_tilt(radius, energy, 2.0)
        result = binding_free_energy(
            radius, tilted, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            resample_curves_kjmol=_resamples(tilted, shared=3.0, own=0.5))

        assert result["bulk_residual_kjmol"] == pytest.approx(1.0)
        assert result["bulk_allowed_kjmol"] > 1.0
        assert result["bulk_tilt_kjmol"] == pytest.approx(-2.0)
        assert result["bulk_tilt_allowed_kjmol"] == pytest.approx(0.6)
        assert result["delta_g_kjmol"] is None
        assert "tilt of -2.00 kJ/mol" in result["refused"]

    @pytest.mark.parametrize("ripple,tilt,refused", [
        (0.55, 0.0, False), (0.65, 0.0, True),
        (0.0, 0.55, False), (0.0, 0.65, True)])
    def test_small_spreads_leave_0_6_as_the_floor(self, ripple, tilt, refused):
        radius, energy = _square_well()
        rippled = _with_a_tilt(radius, _with_a_ripple(energy, ripple), tilt)
        result = binding_free_energy(
            radius, rippled, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            resample_curves_kjmol=_resamples(rippled, shared=1.0, own=0.01))

        assert (result["delta_g_kjmol"] is None) is refused
        assert result["bulk_allowed_kjmol"] == pytest.approx(0.6)
        assert result["bulk_tilt_allowed_kjmol"] == pytest.approx(0.6)

    def test_per_bin_errors_alone_fall_back_to_0_6(self):
        """The per-bin errors hold the zero's noise, so with no resamples
        they are not judged against: a 1.0 kJ/mol ripple inside 3 kJ/mol
        errors is refused at 0.6, and the record says why."""
        radius, energy = _square_well()
        result = binding_free_energy(
            radius, _with_a_ripple(energy, 1.0), temperature_K=TEMPERATURE,
            bound_cutoff_nm=0.6, errors_kjmol=np.full(POINTS, 3.0))

        assert result["delta_g_kjmol"] is None
        assert result["bulk_allowed_kjmol"] == pytest.approx(0.6)
        assert "per-bin errors were passed without" in result["refused"]

    def test_neither_given_is_judged_at_0_6_as_before(self):
        radius, energy = _square_well()
        result = binding_free_energy(radius, _with_a_ripple(energy, 1.0),
                                     temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6)

        assert result["delta_g_kjmol"] is None
        assert result["bulk_judged_by"] == "0.6 kJ/mol alone"
        assert result["bulk_tilt_allowed_kjmol"] is None
        assert "resampled curves were not passed" in result["refused"]

    def test_too_few_whole_resamples_fall_back_to_0_6(self):
        radius, energy = _square_well()
        rippled = _with_a_ripple(energy, 1.0)
        curves = _resamples(rippled, shared=3.0, own=0.6, count=30)
        curves[:15, -2] = np.nan
        result = binding_free_energy(
            radius, rippled, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            resample_curves_kjmol=curves.tolist())

        assert result["delta_g_kjmol"] is None
        assert "only 15 of the resampled curves" in result["refused"]

    def test_a_resample_is_worked_out_without_judging_its_tail(self):
        radius, energy = _square_well()
        plain = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                    bound_cutoff_nm=0.6)
        result = binding_free_energy(
            radius, _with_a_ripple(energy, 1.0), temperature_K=TEMPERATURE,
            bound_cutoff_nm=0.6, judge_the_bulk=False)

        assert result["delta_g_kjmol"] == pytest.approx(
            plain["delta_g_kjmol"], abs=1e-9)
        assert result["bulk_judged_by"] is None

    @pytest.mark.parametrize("given", [0.5, "0.5", [-0.5] * POINTS])
    def test_errors_that_are_not_one_per_point_are_refused(self, given):
        radius, energy = _square_well()
        with pytest.raises(StudyError):
            binding_free_energy(radius, energy, errors_kjmol=given)

    @pytest.mark.parametrize("given", [[0.5] * POINTS, [[0.5] * 10] * 30, "x"])
    def test_resamples_that_are_not_curves_on_these_points_are_refused(
            self, given):
        radius, energy = _square_well()
        with pytest.raises(StudyError):
            binding_free_energy(radius, energy, resample_curves_kjmol=given)

    def test_only_the_resamples_that_gave_a_number_are_counted(self):
        radius, energy = _square_well()
        result = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE, bound_cutoff_nm=0.6,
            resampled={"value": -10.0, "standard_error": 0.4,
                       "confidence_low": -10.8, "confidence_high": -9.2,
                       "resamples": 200, "resamples_with_a_number": 180,
                       "resamples_without_a_number": 20,
                       "correlation_resolved": True})

        assert result["delta_g_resamples"] == 180
        assert result["delta_g_resamples_without_a_number"] == 20


# Through `compute_pmf`: 26 windows at 1000 kJ/mol/nm^2 from 0.5 to 3.0 nm
# on a harmonic well, samples taken exactly from each window's biased
# distribution, 700 a window (per-bin errors in the tail about 0.9 kJ/mol).
GRID = np.linspace(0.3, 3.2, 60001)


def _plan():
    from fastmdxplora.simulation.umbrella import UmbrellaPlan, Window

    return UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c), force_constant=1000.0)
                      for i, c in enumerate(np.linspace(0.5, 3.0, 26))),
        collective_variable="ligand_distance", equilibration_fraction=0.2,
        minimum_overlap=0.0, minimum_samples=10, cone=None,
        bootstrap_resamples=0)


def _sampled(plan, tilt: float, seed: int, count: int = 700):
    """W: 25 kJ/mol deep at 0.8 nm (2000 kJ/mol/nm^2), 0 beyond, then
    falling by `tilt` from 2.35 nm (where the outer 15 bins begin) to 3.0."""
    well = np.minimum(0.0, -25.0 + 1000.0 * (GRID - 0.8) ** 2)
    ramp = np.clip((GRID - 2.35) / 0.65, 0.0, None)
    a_true = well - tilt * ramp - 2.0 * KT * np.log(GRID)
    rng = np.random.default_rng(seed)
    sampled = {}
    for window in plan.windows:
        energy = a_true + 0.5 * window.force_constant * (GRID - window.centre) ** 2
        weight = np.exp(-(energy - energy.min()) / KT)
        sampled[window.index] = rng.choice(GRID, size=count,
                                           p=weight / weight.sum())
    return sampled


def _judged(tilt: float, seeds) -> "list[dict]":
    from fastmdxplora.simulation.umbrella import compute_pmf

    plan = _plan()
    logging.disable(logging.WARNING)
    try:
        found = []
        for seed in seeds:
            pmf = compute_pmf(_sampled(plan, tilt, seed), plan,
                              temperature_K=TEMPERATURE,
                              bootstrap_resamples=60)["pmf"]
            found.append({
                "errors": np.asarray(pmf["uncertainty"]["standard_error"],
                                     dtype=float),
                **binding_free_energy(
                    pmf["coordinate"], pmf["free_energy_kjmol"],
                    temperature_K=TEMPERATURE,
                    errors_kjmol=pmf["uncertainty"]["standard_error"],
                    resample_curves_kjmol=pmf["uncertainty"][
                        "resample_curves_kjmol"])})
        return found
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.slow
class TestThroughTheRecombination:
    def test_a_2_kjmol_tilt_is_caught_nine_times_in_ten(self):
        found = _judged(2.0, range(20))
        assert 0.6 < np.median([np.nanmedian(f["errors"][-15:])
                                for f in found]) < 1.2
        caught = sum(f["delta_g_kjmol"] is None for f in found)
        assert caught >= 18, caught

    def test_a_flat_tail_is_refused_about_one_time_in_twenty(self):
        found = _judged(0.0, range(100, 120))
        assert all(f["bulk_judged_by"] == "the bootstrap's resampled curves"
                   for f in found)
        assert sum(f["delta_g_kjmol"] is None for f in found) <= 1

    def test_the_curves_and_the_counts_come_back(self):
        from fastmdxplora.simulation.umbrella import compute_pmf

        plan = _plan()
        logging.disable(logging.WARNING)
        try:
            payload = compute_pmf(
                _sampled(plan, 0.0, 7), plan,
                temperature_K=TEMPERATURE, bootstrap_resamples=12,
                also=lambda where, curve: binding_free_energy(
                    where, curve, temperature_K=TEMPERATURE,
                    judge_the_bulk=False)["delta_g_kjmol"])
        finally:
            logging.disable(logging.NOTSET)
        curves = payload["pmf"]["uncertainty"]["resample_curves_kjmol"]
        assert np.shape(curves) == (12, len(payload["pmf"]["coordinate"]))
        derived = payload["derived"]
        # A resample with a bin empty in the bound range gives no number;
        # the two counts make up the whole, and the interval is of the first.
        assert (derived["resamples_with_a_number"]
                + derived["resamples_without_a_number"]) == 12
        assert derived["resamples_with_a_number"] >= 6
        result = binding_free_energy(
            payload["pmf"]["coordinate"], payload["pmf"]["free_energy_kjmol"],
            temperature_K=TEMPERATURE, resampled=derived,
            resample_curves_kjmol=curves)
        assert result["delta_g_resamples"] == derived["resamples_with_a_number"]


def _two_step_well():
    """W = -20 below 0.45 nm, -6 to 0.9 nm, 0 beyond.

    A's minimum is at 0.45 nm (1.25 times it is 0.5625), and the plateau
    stands 6 kJ/mol below bulk, so the cutoff the integral picks is the
    first point past 0.9 nm.
    """
    radius = np.linspace(0.05, 3.0, POINTS)
    potential = np.select([radius <= 0.45, radius <= 0.9], [-20.0, -6.0], 0.0)
    return radius, potential - 2.0 * KT * np.log(radius)


CONE = {"half_angle_deg": 30.0, "correction_kjmol": 2.0}


def _walls(at_0_75: float):
    return [{"window": 0, "centre": 0.40, "bias_kjmol": 0.0},
            {"window": 1, "centre": 0.50, "bias_kjmol": 0.05},
            {"window": 2, "centre": 0.75, "bias_kjmol": at_0_75},
            {"window": 3, "centre": 1.60, "bias_kjmol": 9.0}]


class TestTheWallIsJudgedUpToTheCutoff:
    def test_a_wall_past_1_25_times_the_minimum_inside_the_cutoff_refuses(self):
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=_walls(1.0))

        assert result["delta_g_kjmol"] is None
        assert result["bound_cutoff_nm"] > 0.9
        assert result["cone_wall_bias_kjmol"] == pytest.approx(1.0)
        assert [b["window"] for b in result["cone_wall_bit_in"]] == [2]
        assert result["cone_wall_windows"] == [0, 1, 2]
        assert ("carried 1.00 kJ/mol where the bound state is, against 0.25 "
                "allowed, in window 2 (at 0.75 nm)") in result["refused"]

    def test_quiet_up_to_the_cutoff_gives_the_closed_form(self):
        """The wall beyond the cutoff (9 kJ/mol at 1.6 nm, in bulk) is not
        judged; the corrected number is the two-step well's closed form,
        K = 4 pi [0.45^3/3 e^(20/kT) + (0.9^3 - 0.45^3)/3 e^(6/kT)]."""
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.9, cone=CONE,
                                     wall_bias_kjmol=_walls(0.1))
        constant = 4.0 * np.pi * (0.45 ** 3 / 3.0 * np.exp(20.0 / KT)
                                  + (0.9 ** 3 - 0.45 ** 3) / 3.0
                                  * np.exp(6.0 / KT))
        exact = -KT * np.log(constant / STANDARD_VOLUME_NM3) + 2.0

        assert result["refused"] is None
        assert result["delta_g_kjmol"] == pytest.approx(exact, abs=0.5)
        assert result["cone_wall_checked_up_to_nm"] == pytest.approx(0.9)
        assert result["cone_wall_windows"] == [0, 1, 2]
        assert result["cone_wall_bias_kjmol"] == pytest.approx(0.1)

    @pytest.mark.parametrize("reverse", [False, True])
    def test_a_window_whose_bias_is_not_a_number_hides_nothing(self, reverse):
        """NaN in window 0, 5 kJ/mol in window 1: refused, whichever comes
        first, and window 0 named as not read."""
        radius, energy = _two_step_well()
        rows = [{"window": 0, "centre": 0.30, "bias_kjmol": float("nan")},
                {"window": 1, "centre": 0.40, "bias_kjmol": 5.0}]
        result = binding_free_energy(
            radius, energy, temperature_K=TEMPERATURE, cone=CONE,
            wall_bias_kjmol=rows[::-1] if reverse else rows)

        assert result["delta_g_kjmol"] is None
        assert result["cone_wall_bias_kjmol"] == pytest.approx(5.0)
        assert result["cone_wall_not_read_in"] == [0]
        assert "except 0, whose bias could not be read" in result["refused"]

    @pytest.mark.parametrize("walls", [
        [{"window": 0, "centre": 0.30, "bias_kjmol": float("nan")},
         {"window": 1, "centre": 0.40, "bias_kjmol": None}],
        float("nan")])
    def test_no_window_read_up_to_the_cutoff_refuses(self, walls):
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=walls)

        assert result["delta_g_kjmol"] is None
        assert "could not be read" in result["refused"]

    @pytest.mark.parametrize("scalar", [np.int64(1), np.array(1.0), 1.0])
    def test_one_number_is_still_taken_as_it_stands(self, scalar):
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=scalar)
        quiet = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                    cone=CONE, wall_bias_kjmol=scalar * 0.1)

        assert result["delta_g_kjmol"] is None
        assert quiet["refused"] is None
        assert quiet["cone_wall_checked"] is True
        assert quiet["cone_wall_checked_up_to_nm"] is None

    def test_without_a_cone_nothing_is_said_of_a_wall(self):
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     wall_bias_kjmol=_walls(1.0))

        assert result["refused"] is None
        assert all(result[key] is None for key in (
            "cone_wall_bias_kjmol", "cone_wall_checked", "cone_wall_windows",
            "cone_wall_bit_in", "cone_wall_not_read_in",
            "cone_wall_checked_up_to_nm"))

    def test_the_runs_give_every_window_and_the_old_range_misses_one(
            self, tmp_path):
        """Read off COLVAR files: 1.25 times the 0.45 nm minimum leaves out
        the 0.75 nm window the integral is taken over, and a window with no
        COLVAR is named as not read."""
        from fastmdxplora.simulation.umbrella import (
            plan_windows,
            wall_bias_by_window,
            wall_bias_where_the_bound_state_is,
        )

        plan = plan_windows({"collective_variable": "ligand_distance",
                             "centres": [0.40, 0.50, 0.75, 1.60, 0.85],
                             "force_constant": 3000.0,
                             "cone": {"half_angle_deg": 30}})
        directories = {}
        for index, bias in enumerate((0.0, 0.05, 1.0, 9.0, None)):
            run = tmp_path / f"window-{index}" / "simulation"
            run.mkdir(parents=True)
            directories[index] = run.parent
            if bias is None:
                continue
            # Equilibration (the first 20%) at 50 kJ/mol, which must not count.
            rows = "\n".join(
                f"{i * 0.2:.1f} 0.5 1.0 2.9 {50.0 if i < 20 else bias:.3f}"
                for i in range(100))
            run.joinpath("COLVAR").write_text(
                "#! FIELDS time cv restraint.bias cone_angle cone.bias\n"
                + rows, encoding="utf-8")

        walls = wall_bias_by_window(directories, plan)
        assert [(w["window"], w["centre"]) for w in walls] == [
            (0, 0.40), (1, 0.50), (2, 0.75), (3, 1.60), (4, 0.85)]
        assert [w["bias_kjmol"] for w in walls[:4]] == pytest.approx(
            [0.0, 0.05, 1.0, 9.0])
        assert np.isnan(walls[4]["bias_kjmol"])
        assert wall_bias_where_the_bound_state_is(
            directories, plan, 0.45 * 1.25) == pytest.approx(0.05)

        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=walls)
        assert result["delta_g_kjmol"] is None
        assert [b["window"] for b in result["cone_wall_bit_in"]] == [2]
        assert result["cone_wall_not_read_in"] == [4]
