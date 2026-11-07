"""The binding free energy's bulk check and cone wall, on curves with exact answers.

The bulk check judges how far A(r) + 2kT ln r departs from a constant in
the tail (Gilson et al. 1997, Biophys. J. 72, 1047), against the larger of
0.6 kJ/mol and Sidak's multiplier times each departure's standard error
(Sidak 1967, J. Am. Stat. Assoc. 62, 626). The wall is judged in every
window up to the cutoff the integral uses, not up to a range fixed before
the curve is read.
"""

from __future__ import annotations

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
    every point departs from it by exactly `size`."""
    rippled = energy.copy()
    rippled[-TAIL:] += size * np.where(np.arange(TAIL) % 2 == 0, 1.0, -1.0)
    return rippled


def _allowed(error: float, count: int = TAIL) -> float:
    """z_n * sqrt(e^2 + e_mean^2) for equal errors e, written out."""
    each = 1.0 - 0.95 ** (1.0 / count)
    z = NormalDist().inv_cdf(1.0 - each / 2.0)
    return z * np.sqrt(error ** 2 + (np.sqrt(count) * error / count) ** 2)


class TestTheBulkIsJudgedAgainstItsErrors:
    def test_a_tail_within_its_errors_is_bulk(self):
        """1.0 kJ/mol of ripple is past 0.6 and inside 0.5 kJ/mol errors'
        allowance (about 1.74), so the number stands and is unmoved."""
        radius, energy = _square_well()
        rippled = _with_a_ripple(energy, 1.0)
        errors = np.full(POINTS, 0.5)
        assert 1.0 < _allowed(0.5)

        result = binding_free_energy(radius, rippled, temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6, errors_kjmol=errors)
        plain = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                    bound_cutoff_nm=0.6)

        assert result["refused"] is None
        assert result["delta_g_kjmol"] == pytest.approx(
            plain["delta_g_kjmol"], abs=1e-9)
        assert result["bulk_judged_against_errors"] is True
        assert result["bulk_residual_kjmol"] == pytest.approx(1.0)
        assert result["bulk_allowed_kjmol"] == pytest.approx(_allowed(0.5))
        assert "standard errors" in result["bulk_judged"]

    def test_the_same_tail_without_errors_is_judged_at_0_6(self):
        radius, energy = _square_well()
        result = binding_free_energy(radius, _with_a_ripple(energy, 1.0),
                                     temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6)

        assert result["delta_g_kjmol"] is None
        assert result["bulk_judged_against_errors"] is False
        assert result["bulk_allowed_kjmol"] == pytest.approx(0.6)
        assert "without the bootstrap's errors" in result["refused"]

    def test_a_tail_beyond_its_errors_is_refused(self):
        radius, energy = _square_well()
        result = binding_free_energy(radius, _with_a_ripple(energy, 2.0),
                                     temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6,
                                     errors_kjmol=np.full(POINTS, 0.5))

        assert result["delta_g_kjmol"] is None
        assert result["bulk_allowed_kjmol"] == pytest.approx(_allowed(0.5))
        assert f"{_allowed(0.5):.2f} allowed there" in result["refused"]
        assert "standard errors of that departure" in result["refused"]

    @pytest.mark.parametrize("ripple,refused", [(0.55, False), (0.65, True)])
    def test_small_errors_leave_0_6_as_the_floor(self, ripple, refused):
        radius, energy = _square_well()
        result = binding_free_energy(radius, _with_a_ripple(energy, ripple),
                                     temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6,
                                     errors_kjmol=np.full(POINTS, 0.01))

        assert (result["delta_g_kjmol"] is None) is refused
        assert result["bulk_allowed_kjmol"] == pytest.approx(0.6)

    def test_a_tail_bin_with_no_error_is_judged_at_0_6(self):
        radius, energy = _square_well()
        errors = [0.5] * POINTS
        errors[-3] = None
        result = binding_free_energy(radius, _with_a_ripple(energy, 1.0),
                                     temperature_K=TEMPERATURE,
                                     bound_cutoff_nm=0.6, errors_kjmol=errors)

        assert result["delta_g_kjmol"] is None
        assert result["bulk_judged_against_errors"] is False
        assert "not given for every point" in result["refused"]

    def test_errors_of_another_length_are_refused(self):
        radius, energy = _square_well()
        with pytest.raises(StudyError, match="one per point"):
            binding_free_energy(radius, energy, errors_kjmol=[0.5] * 10)


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
        assert "window 2 (at 0.75 nm, 1.00 kJ/mol)" in result["refused"]

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

    def test_one_number_is_still_taken_as_it_stands(self):
        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=1.0)
        quiet = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                    cone=CONE, wall_bias_kjmol=0.1)

        assert result["delta_g_kjmol"] is None
        assert quiet["refused"] is None
        assert quiet["cone_wall_checked"] is True
        assert quiet["cone_wall_checked_up_to_nm"] is None

    def test_the_runs_give_every_window_and_the_old_range_misses_one(
            self, tmp_path):
        """Read off COLVAR files: 1.25 times the 0.45 nm minimum leaves out
        the 0.75 nm window the integral is taken over."""
        from fastmdxplora.simulation.umbrella import (
            plan_windows,
            wall_bias_by_window,
            wall_bias_where_the_bound_state_is,
        )

        plan = plan_windows({"collective_variable": "ligand_distance",
                             "centres": [0.40, 0.50, 0.75, 1.60],
                             "force_constant": 3000.0,
                             "cone": {"half_angle_deg": 30}})
        directories = {}
        for index, bias in enumerate((0.0, 0.05, 1.0, 9.0)):
            run = tmp_path / f"window-{index}" / "simulation"
            run.mkdir(parents=True)
            # Equilibration (the first 20%) at 50 kJ/mol, which must not count.
            rows = "\n".join(
                f"{i * 0.2:.1f} 0.5 1.0 2.9 {50.0 if i < 20 else bias:.3f}"
                for i in range(100))
            run.joinpath("COLVAR").write_text(
                "#! FIELDS time cv restraint.bias cone_angle cone.bias\n"
                + rows, encoding="utf-8")
            directories[index] = run.parent

        walls = wall_bias_by_window(directories, plan)
        assert [(w["window"], w["centre"]) for w in walls] == [
            (0, 0.40), (1, 0.50), (2, 0.75), (3, 1.60)]
        assert [w["bias_kjmol"] for w in walls] == pytest.approx(
            [0.0, 0.05, 1.0, 9.0])
        assert wall_bias_where_the_bound_state_is(
            directories, plan, 0.45 * 1.25) == pytest.approx(0.05)

        radius, energy = _two_step_well()
        result = binding_free_energy(radius, energy, temperature_K=TEMPERATURE,
                                     cone=CONE, wall_bias_kjmol=walls)
        assert result["delta_g_kjmol"] is None
        assert [b["window"] for b in result["cone_wall_bit_in"]] == [2]
