"""A barrier at +-pi, read across the seam, and judged against its error.

On a full turn the bins at -pi and +pi are one place. A turn's grid was cut
where the samples happened to stop, so the seam fell between two bins a
little more than a bin apart; a grid given with both -pi and +pi read the
one place as two, each the other's neighbour. A peak that cleared the fixed
margin and not twice its error was dropped without a word.

The profiles here are known, and each bin's exact free energy is the
Boltzmann average over the bin, so the numbers are checked against that.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from fastmdxplora.simulation.binding import KB_KJMOL
from fastmdxplora.simulation.umbrella import (
    BARRIER_PROMINENCE_KJMOL, UmbrellaPlan, Window, compute_pmf, describe_pmf)

TEMPERATURE = 300.0
KT = KB_KJMOL * TEMPERATURE
BINS = 60
H = 2.0 * np.pi / BINS
EDGES = np.linspace(-np.pi, np.pi, BINS + 1)
CENTRES = 0.5 * (EDGES[:-1] + EDGES[1:])


def _hump(at: float, height: float = 20.0, width: float = 0.4):
    """A Gaussian hump on a flat turn, its top at ``at``."""
    def profile(x):
        d = np.remainder(np.asarray(x) - at + np.pi, 2.0 * np.pi) - np.pi
        return height * np.exp(-(d / width) ** 2)
    return profile


def _per_bin(profile, edges=EDGES):
    """Each bin's exact free energy, -kT ln of the bin's mean Boltzmann factor."""
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        x = np.linspace(a, b, 4001)
        out.append(-KT * np.log(np.mean(np.exp(-profile(x) / KT))))
    out = np.asarray(out)
    return out - out.min()


#: Top on the seam (on the edge between the first and last bins), and off
#: it, inside the first bin and inside the last.
TOPS = {"on the seam": np.pi, "inside the first bin": -np.pi + 0.3 * H,
        "inside the last bin": np.pi - 0.3 * H}


class TestTheExactCurve:
    @pytest.mark.parametrize("name", sorted(TOPS))
    def test_the_barrier_is_the_top_bin(self, name) -> None:
        exact = _per_bin(_hump(TOPS[name]))
        got = describe_pmf(CENTRES, list(exact), periodic=True, closed=True)
        top = int(np.argmax(exact))
        assert got["barrier_kjmol"] == pytest.approx(exact[top], abs=1e-9)
        if name == "on the seam":
            # The two bins either side of the seam are equal; one is named.
            assert abs(got["barrier_at"]) == pytest.approx(np.pi - H / 2)
        else:
            assert got["barrier_at"] == pytest.approx(CENTRES[top], abs=1e-12)

    def test_a_grid_with_both_ends_reads_them_as_one(self) -> None:
        # Bins centred on -pi and on +pi: one place, given twice, the two
        # copies 0.8 kJ/mol apart (the turn's closure). Read as one, the
        # barrier is their mean; read as two, it was the higher copy.
        x = np.linspace(-np.pi, np.pi, BINS + 1)
        e = _hump(np.pi)(x)
        e[0] += 0.4
        e[-1] -= 0.4
        got = describe_pmf(x, list(e), periodic=True, closed=True)
        assert got["barrier_kjmol"] == pytest.approx(20.0, abs=1e-9)
        assert got["barrier_at"] == pytest.approx(-np.pi)
        # The well opposite is one minimum, and the seam holds none.
        assert [round(m["coordinate"], 9) for m in got["minima"]] == [0.0]


def _sampled(top: float, samples: int, seed: int):
    centres = -np.pi + np.arange(36) * (2.0 * np.pi / 36)
    plan = UmbrellaPlan(
        windows=tuple(Window(index=i, centre=float(c), force_constant=400.0)
                      for i, c in enumerate(centres)),
        collective_variable="torsion", equilibration_fraction=0.2,
        minimum_overlap=0.0, minimum_samples=10, cone=None,
        bootstrap_resamples=0)
    profile = _hump(top)
    grid = np.linspace(-np.pi, np.pi, 200001)[:-1]
    step = grid[1] - grid[0]
    truth = profile(grid)
    rng = np.random.default_rng(seed)
    picked = {}
    for window in plan.windows:
        d = np.remainder(grid - window.centre + np.pi, 2.0 * np.pi) - np.pi
        weight = np.exp(-(truth + 0.5 * window.force_constant * d ** 2) / KT)
        # Continuous values, so no sample sits on +-pi itself.
        values = rng.choice(grid, size=samples, p=weight / weight.sum())
        values = values + rng.uniform(0.0, step, size=samples)
        picked[window.index] = np.remainder(values + np.pi, 2.0 * np.pi) - np.pi
    logging.disable(logging.WARNING)
    try:
        return compute_pmf(picked, plan, temperature_K=TEMPERATURE, bins=BINS,
                           bootstrap_resamples=0)
    finally:
        logging.disable(logging.NOTSET)


class TestTheRecombinedTurn:
    @pytest.mark.parametrize("name", sorted(TOPS))
    def test_its_grid_is_the_turn_and_its_barrier_the_top(self, name) -> None:
        payload = _sampled(TOPS[name], 8000, seed=21)
        x = np.asarray(payload["pmf"]["coordinate"])
        # The seam on the edge between the first and last bins, every bin
        # 2 pi / 60 wide; from the samples' extremes neither held.
        assert x[0] == pytest.approx(-np.pi + H / 2, abs=1e-12)
        assert x[-1] == pytest.approx(np.pi - H / 2, abs=1e-12)
        exact = _per_bin(_hump(TOPS[name]))
        summary = payload["summary"]
        assert summary["barrier_kjmol"] == pytest.approx(exact.max(), abs=0.5)
        top = CENTRES[int(np.argmax(exact))]
        apart = abs(np.remainder(summary["barrier_at"] - top + np.pi,
                                 2.0 * np.pi) - np.pi)
        assert apart <= H + 1e-9


class TestAPeakWithinItsErrorIsRefused:
    def _curve(self):
        x = np.linspace(0.0, 1.0, 41)
        e = 6.0 * np.exp(-((x - 0.5) / 0.08) ** 2)
        return x, e

    def test_it_is_named_and_why(self) -> None:
        x, e = self._curve()
        errors = np.full(x.size, 2.5)
        got = describe_pmf(x, list(e), errors=list(errors))
        assert got["barrier_kjmol"] is None
        (named,) = got["within_error"]
        assert named["at"] == pytest.approx(0.5)
        assert named["rise_kjmol"] == pytest.approx(6.0)
        # Twice the two bins' errors together: 2 * hypot(2.5, 2.5).
        assert named["error_kjmol"] == pytest.approx(np.hypot(2.5, 2.5))
        assert named["needed_kjmol"] == pytest.approx(2.0 * np.hypot(2.5, 2.5))
        assert "not called a barrier" in got["barrier_refused"]
        assert "standard errors" in got["barrier_judged_by"]

    def test_one_clearing_its_error_carries_it(self) -> None:
        x, e = self._curve()
        got = describe_pmf(x, list(e), errors=[0.5] * x.size)
        assert got["barrier_kjmol"] == pytest.approx(6.0)
        assert got["barrier_error_kjmol"] == pytest.approx(np.hypot(0.5, 0.5))
        assert got["within_error"] == []
        assert "barrier_refused" not in got

    def test_with_no_error_the_margin_alone_is_said(self) -> None:
        x, e = self._curve()
        got = describe_pmf(x, list(e))
        assert got["barrier_kjmol"] == pytest.approx(6.0)
        assert got["barrier_error_kjmol"] is None
        assert got["barrier_judged_by"].startswith(f"{BARRIER_PROMINENCE_KJMOL} kJ/mol alone")

    def test_the_resamples_judge_the_rise_itself(self) -> None:
        # Every resample's curve moved up or down as a whole by 3 kJ/mol
        # (the zero's noise, which every bin's error carries) and each bin
        # by 0.2 of its own. The bins' errors say 3 and refuse a 6 kJ/mol
        # hump; the rise itself varies by 0.2 * sqrt(2) and carries it.
        x, e = self._curve()
        rng = np.random.default_rng(3)
        rows = (e[None, :] + rng.normal(0.0, 3.0, size=(400, 1))
                + rng.normal(0.0, 0.2, size=(400, x.size)))
        errors = rows.std(axis=0, ddof=1)
        refused = describe_pmf(x, list(e), errors=list(errors))
        assert refused["barrier_kjmol"] is None
        got = describe_pmf(x, list(e), errors=list(errors), resamples=rows.tolist())
        assert got["barrier_kjmol"] == pytest.approx(6.0)
        assert got["barrier_error_kjmol"] == pytest.approx(0.2 * np.sqrt(2.0), rel=0.1)
        assert "resampled curves (400)" in got["barrier_judged_by"]

    def test_a_turn_judges_its_seam_peak_against_its_error(self) -> None:
        exact = _per_bin(_hump(np.pi, height=4.0))
        quiet = describe_pmf(CENTRES, list(exact), periodic=True, closed=True,
                             errors=[0.3] * BINS)
        assert quiet["barrier_kjmol"] == pytest.approx(exact.max(), abs=1e-9)
        noisy = describe_pmf(CENTRES, list(exact), periodic=True, closed=True,
                             errors=[2.0] * BINS)
        assert noisy["barrier_kjmol"] is None
        assert abs(noisy["within_error"][0]["at"]) == pytest.approx(np.pi - H / 2)


class TestTheStudySaysItsBarrier:
    def test_the_record_s_summary_carries_the_judgement(self) -> None:
        payload = _sampled(np.pi, 3000, seed=5)
        summary = payload["summary"]
        for key in ("barrier_error_kjmol", "barrier_judged_by", "within_error"):
            assert key in summary
        assert summary["read_off"] == "free_energy_kjmol"
