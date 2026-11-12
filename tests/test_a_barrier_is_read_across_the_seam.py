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
from statistics import NormalDist

from fastmdxplora.simulation.umbrella import (
    BARRIER_FALSE_RATE, BARRIER_PROMINENCE_KJMOL, UmbrellaPlan, Window,
    compute_pmf, describe_pmf, the_barrier_in_a_line)

TEMPERATURE = 300.0
KT = KB_KJMOL * TEMPERATURE
BINS = 60
H = 2.0 * np.pi / BINS
EDGES = np.linspace(-np.pi, np.pi, BINS + 1)
CENTRES = 0.5 * (EDGES[:-1] + EDGES[1:])


def _sidak(pairs: int) -> float:
    """Sidak's multiplier, written out again here."""
    return NormalDist().inv_cdf((1.0 - BARRIER_FALSE_RATE) ** (1.0 / pairs))


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
        # The two bins' errors together, hypot(2.5, 2.5), times Sidak's
        # multiplier over the 41 * 40 rises the curve has.
        assert named["error_kjmol"] == pytest.approx(np.hypot(2.5, 2.5))
        assert named["needed_kjmol"] == pytest.approx(
            _sidak(41 * 40) * np.hypot(2.5, 2.5))
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
        assert "the rise's spread over 400 resamples" in got["barrier_judged_by"]

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
        # No bootstrap here, so no multiplier: the margin alone.
        assert summary["barrier_multiplier"] is None


def _triangle(height, x):
    """A hump with exact zeros beside it, so each side's lowest point is a
    known bin."""
    return height * np.clip(1.0 - np.abs(x - 0.5) / 0.1, 0.0, None)


def _max_statistic(noise):
    """The family's multiplier and every rise's error, written out again
    from the definition: per resample, the largest of every rise's
    departure over that rise's error; its 95th percentile."""
    d = noise[:, :, None] - noise[:, None, :]
    sd = d.std(axis=0, ddof=1)
    np.fill_diagonal(sd, np.nan)
    largest = np.nanmax(d / sd, axis=(1, 2))
    return np.quantile(largest, 1.0 - BARRIER_FALSE_RATE, method="higher"), sd


class TestThePeaksAreJudgedAsAFamily:
    X = np.linspace(0.0, 1.0, 41)

    def _noise(self):
        return np.random.default_rng(8).normal(0.0, 0.6, size=(200, self.X.size))

    def _needed(self, noise):
        q, sd = _max_statistic(noise)
        top = int(np.argmax(_triangle(1.0, self.X)))
        values = _triangle(1.0, self.X)
        sides = [int(np.argmin(values[:top])), top + 1 + int(np.argmin(values[top + 1:]))]
        return q, max(q * sd[top, side] for side in sides)

    def test_the_multiplier_is_the_max_statistic_s(self) -> None:
        noise = self._noise()
        q, needed = self._needed(noise)
        e = _triangle(0.9 * needed, self.X)
        got = describe_pmf(self.X, list(e), resamples=(e + noise).tolist())
        assert got["barrier_multiplier"] == pytest.approx(q, rel=1e-9)
        # Just under it: refused, and what it needed is said exactly.
        assert got["barrier_kjmol"] is None
        assert got["within_error"][0]["needed_kjmol"] == pytest.approx(needed, rel=1e-9)
        # Just over it: given.
        e = _triangle(1.1 * needed, self.X)
        got = describe_pmf(self.X, list(e), resamples=(e + noise).tolist())
        assert got["barrier_kjmol"] == pytest.approx(1.1 * needed)
        assert got["barrier_multiplier"] == pytest.approx(q, rel=1e-9)
        # Several errors, not two: the highest of 41 bins read above the
        # lowest.
        assert 3.0 < q < 5.0

    def test_without_resamples_sidak_over_every_rise(self) -> None:
        z = _sidak(41 * 40)
        needed = z * np.hypot(1.0, 1.0)
        refused = describe_pmf(self.X, list(_triangle(0.9 * needed, self.X)),
                               errors=[1.0] * 41)
        assert refused["barrier_kjmol"] is None
        assert refused["barrier_multiplier"] == pytest.approx(z)
        given = describe_pmf(self.X, list(_triangle(1.1 * needed, self.X)),
                             errors=[1.0] * 41)
        assert given["barrier_kjmol"] == pytest.approx(1.1 * needed)

    def test_the_label_says_what_judged_the_peak(self) -> None:
        # 30 resamples, the peak's bin empty in 25 of them: its rise is
        # judged by the bins' errors, and the label says so.
        noise = self._noise()[:30]
        e = _triangle(8.0, self.X)
        rows = e + noise
        rows[:25, 20] = np.nan
        got = describe_pmf(self.X, list(e), errors=[0.3] * 41, resamples=rows.tolist())
        assert got["barrier_kjmol"] == pytest.approx(8.0)
        assert "the two bins' standard errors" in got["barrier_judged_by"]
        assert "resamples" not in got["barrier_judged_by"]
        whole = describe_pmf(self.X, list(e), errors=[0.3] * 41,
                             resamples=(e + noise).tolist())
        assert "the rise's spread over 30 resamples" in whole["barrier_judged_by"]


class TestARefusedPeakIsNotQuoted:
    X = np.linspace(0.0, 1.0, 41)

    def test_its_height_is_withheld(self) -> None:
        e = 6.0 * np.exp(-((self.X - 0.5) / 0.08) ** 2)
        got = describe_pmf(self.X, list(e), errors=[2.5] * 41, unit="rad")
        assert got["highest_kjmol"] is None and got["highest_at"] is None
        assert "refused as a barrier" in got["highest_withheld"]
        assert "at 0.5 rad" in got["barrier_refused"]

    def test_a_lower_one_that_stands_says_the_higher_was_refused(self) -> None:
        e = (12.0 * np.exp(-((self.X - 0.3) / 0.05) ** 2)
             + 10.0 * np.exp(-((self.X - 0.7) / 0.05) ** 2))
        errors = np.full(41, 0.3)
        errors[12] = 4.0  # the higher peak's bin, at 0.3
        got = describe_pmf(self.X, list(e), errors=list(errors), unit="nm")
        assert got["barrier_at"] == pytest.approx(0.7)
        assert "A higher peak, 12.0 kJ/mol at 0.3 nm" in got["barrier_note"]
        assert got["highest_kjmol"] is None


class TestTheBarrierIsSaidInOneLine:
    def _record(self, summary, variable="torsion"):
        return {"refused": None, "plan": {"collective_variable": variable},
                "summary": summary}

    def test_given(self) -> None:
        x, e = TestAPeakWithinItsErrorIsRefused()._curve()
        line = the_barrier_in_a_line(self._record(
            describe_pmf(x, list(e), errors=[0.5] * x.size, unit="rad")))
        assert line == "6.0 +/- 0.7 kJ/mol at 0.5 rad, on the free energy."

    def test_refused_and_why(self) -> None:
        x, e = TestAPeakWithinItsErrorIsRefused()._curve()
        line = the_barrier_in_a_line(self._record(
            describe_pmf(x, list(e), errors=[2.5] * x.size, unit="rad")))
        assert line.startswith("none given. The highest peak, 6.0 kJ/mol at 0.5 rad, "
                               "stands less than")
        assert "times its error" in line and "\n" not in line

    def test_none_along_a_distance(self) -> None:
        r = np.linspace(0.4, 2.0, 41)
        line = the_barrier_in_a_line(self._record(
            {**describe_pmf(r, list(np.zeros(41))),
             "read_off": "potential_of_mean_force_kjmol"}, "ligand_distance"))
        assert line == "none: no peak on W(r) stands 2.5 kJ/mol above its lowest point on each side."


class TestAFlatTurnQuotesNoBarrierByItsNoise:
    """A flat turn at 300 frames a window (240 after the discard), 40
    resamples: one peak at a time, 7 of 20 seeds quoted a barrier of 2.5 to
    2.9 kJ/mol; judged as a family, 0 of those 20 and 2 of 60 quoted one,
    and a 20 kJ/mol hump on the seam is still given."""

    def _study(self, profile, samples, seed):
        centres = -np.pi + np.arange(36) * (2.0 * np.pi / 36)
        plan = UmbrellaPlan(
            windows=tuple(Window(index=i, centre=float(c), force_constant=400.0)
                          for i, c in enumerate(centres)),
            collective_variable="torsion", equilibration_fraction=0.2,
            minimum_overlap=0.0, minimum_samples=50, cone=None,
            bootstrap_resamples=40)
        grid = np.linspace(-np.pi, np.pi, 200001)[:-1]
        step = grid[1] - grid[0]
        truth = profile(grid)
        rng = np.random.default_rng(seed)
        picked = {}
        for window in plan.windows:
            d = np.remainder(grid - window.centre + np.pi, 2.0 * np.pi) - np.pi
            weight = np.exp(-(truth + 0.5 * window.force_constant * d ** 2) / KT)
            values = rng.choice(grid, size=samples, p=weight / weight.sum())
            values = values + rng.uniform(0.0, step, size=samples)
            picked[window.index] = np.remainder(values + np.pi, 2.0 * np.pi) - np.pi
        logging.disable(logging.WARNING)
        try:
            return compute_pmf(picked, plan, temperature_K=TEMPERATURE,
                               bootstrap_resamples=40, bootstrap_seed=seed)["summary"]
        finally:
            logging.disable(logging.NOTSET)

    def test_flat(self) -> None:
        quoted = [seed for seed in range(1, 9)
                  if self._study(lambda x: 0.0 * x, 240, seed)["barrier_kjmol"] is not None]
        # One peak at a time, seeds 1, 2, 3, 5 and 7 of these quoted one.
        assert len(quoted) <= 1

    def test_a_real_one_on_the_seam_stands(self) -> None:
        got = self._study(_hump(np.pi), 2400, 1)
        assert got["barrier_kjmol"] == pytest.approx(19.52, abs=0.6)
        # The family's multiplier on a turn: about 3.5 to 4.
        assert 3.0 < got["barrier_multiplier"] < 5.0
        assert "rad" in the_barrier_in_a_line(
            {"plan": {"collective_variable": "torsion"}, "summary": got})
