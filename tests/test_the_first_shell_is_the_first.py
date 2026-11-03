"""The first shell is the first one g(r) resolves, read from enough pairs.

- `rdf`'s first peak and `coordination_number`'s `cutoff: rdf` took the
  tallest point past 0.15 nm as the first peak: water O-H's hydrogen-bond
  peak (0.183 nm, g 1.24) gave way to its 0.32 nm peak (g 1.43), and a
  simple cubic lattice, whose first two shells (6 at a and 12 at a times
  root two) stand equally tall, read 18 neighbours in 12 of 18 runs.
- Every run drew the same number of pairs, so a short run of a large box
  put under one pair a frame in each bin of its first shell.
- The counts were held for every frame: 20,000 frames of 1,000 bins took
  1.1 GB at the peak.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis import rdf as rdf_module  # noqa: E402
from fastmdxplora.analysis.coordination_number import CoordinationNumber  # noqa: E402
from fastmdxplora.analysis.rdf import (  # noqa: E402
    PairHistogram,
    RadialDistribution,
    _pairs_between,
    first_peak,
    pairs_for,
)


def _cell(xyz, names, length):
    top = md.Topology()
    chain = top.add_chain()
    for name in names:
        top.add_atom(name, md.element.argon, top.add_residue(name, chain))
    traj = md.Trajectory((xyz % length).astype(np.float32), top)
    traj.unitcell_vectors = np.repeat((np.eye(3) * length)[None], len(xyz),
                                      axis=0).astype(np.float32)
    return traj


def _simple_cubic(sigma, frames, n=8, a=0.282, seed=0):
    points = np.array(list(np.ndindex(n, n, n)), dtype=float) * a
    rng = np.random.default_rng(seed)
    xyz = points[None] + rng.normal(0.0, sigma, (frames, len(points), 3))
    return _cell(xyz, ["AR"] * len(points), n * a)


class TestTheFirstPeakIsTheFirst:

    @pytest.mark.parametrize("sigma", [0.005, 0.01, 0.02])
    def test_a_simple_cubic_lattice_has_six_first_neighbours(self, tmp_path, sigma):
        """Before, over seeds 0 to 2, 18 or 26.7 neighbours (shells merged)
        or a refusal in at least one at each spread."""
        for seed in range(3):
            analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                          output_dir=str(tmp_path / str(seed)))
            counts = analysis.compute(_simple_cubic(sigma, 10, seed=seed))
            assert 0.282 < analysis.findings["shell"]["cutoff_nm"] < 0.282 * np.sqrt(2)
            assert float(np.mean(counts)) == pytest.approx(6.0, abs=0.05)

    def test_a_low_first_peak_before_a_taller_one(self):
        """A hydrogen bond's peak, lower than the shell after it: the first
        resolved, not the tallest."""
        r = np.arange(0.0, 1.5, 0.005) + 0.0025
        g = (1.0 + 0.6 * np.exp(-((r - 0.18) / 0.015) ** 2)
             - 0.9 * np.exp(-((r - 0.24) / 0.02) ** 2)
             + 0.9 * np.exp(-((r - 0.32) / 0.03) ** 2))
        g[r < 0.15] = 0.0
        noise = np.full_like(g, 0.02)
        peak, _bar, _searched, resolved = first_peak(r, g, noise)
        assert resolved and r[peak] == pytest.approx(0.18, abs=0.005)
        # Short of the bar, it is not passed over for the next one: a hill
        # the curve falls well away from is a shell, so none is named.
        noise[r < 0.22] = 0.3
        peak, _bar, _searched, resolved = first_peak(r, g, noise)
        assert not resolved and r[peak] == pytest.approx(0.18, abs=0.005)
        # A wiggle of noise before the shell is passed over.
        wiggle = 1.0 + 0.04 * np.exp(-((r - 0.17) / 0.01) ** 2) + 0.9 * np.exp(
            -((r - 0.32) / 0.03) ** 2)
        wiggle[r < 0.15] = 0.0
        peak, _bar, _searched, resolved = first_peak(r, wiggle, np.full_like(r, 0.02))
        assert resolved and r[peak] == pytest.approx(0.32, abs=0.005)

    def test_nothing_resolved_gives_the_tallest_point(self):
        r = np.arange(0.0, 1.5, 0.005) + 0.0025
        g = 1.0 + 0.01 * np.sin(np.arange(r.size))
        g[r < 0.15] = 0.0
        peak, bar, searched, resolved = first_peak(r, g, np.full_like(g, 0.05))
        assert not resolved and peak == int(np.argmax(np.where(r > 0.15, g, -np.inf)))
        assert bar > 3.0 and searched == int(np.count_nonzero(r > 0.15))

    def test_rdf_reports_the_first_peak(self, tmp_path):
        """Two shells around each A, of 4 at 0.2 nm and 30 at 0.35 nm: the
        second stands taller (g goes as the count over r squared)."""
        rng = np.random.default_rng(3)
        frames, centres, length = 20, 30, 4.0
        xyz = []
        for _ in range(frames):
            a = rng.random((centres, 3)) * length
            shells = []
            for count, radius in ((4, 0.2), (30, 0.35)):
                way = rng.normal(size=(centres, count, 3))
                way /= np.linalg.norm(way, axis=2)[..., None]
                shells.append((a[:, None, :] + radius * way).reshape(-1, 3))
            xyz.append(np.vstack([a, *shells]))
        names = ["A"] * centres + ["B"] * (centres * 34)
        traj = _cell(np.asarray(xyz), names, length)
        analysis = RadialDistribution(selection_a="name A", selection_b="name B",
                                      bin_width=0.01, output_dir=str(tmp_path))
        table = analysis.compute(traj)
        g = np.asarray(table[:, 1])
        inside = table[:, 0] < 0.27
        assert g[~inside].max() > 1.5 * g[inside].max()
        assert analysis.findings["rdf"]["first_peak_nm"] == pytest.approx(0.2, abs=0.01)


class TestAShortRunTakesMorePairs:

    def test_the_pairs_follow_the_run_s_length(self):
        assert pairs_for(1, 200_000) == rdf_module.MOST_PAIRS
        assert pairs_for(60, 200_000) == rdf_module.PAIR_FRAMES // 60
        assert pairs_for(5_000, 200_000) == 200_000
        assert pairs_for(10_000, 400_000) == 400_000

    def test_the_coordination_number_draws_them(self, tmp_path, monkeypatch):
        drawn = []
        real = rdf_module._pairs_between

        def counted(a, b, rng, limit=None):
            drawn.append(limit)
            return real(a, b, rng, limit)

        monkeypatch.setattr(rdf_module, "_pairs_between", counted)
        traj = _simple_cubic(0.01, frames=4)
        CoordinationNumber(selection_a="all", selection_b="all",
                           output_dir=str(tmp_path)).compute(traj)
        assert drawn == [pairs_for(4, 200_000)]


def _gas(frames, seed, kind, n=80, length=2.2):
    rng = np.random.default_rng(seed)
    if kind == "independent":
        xyz = rng.random((frames, n, 3)) * length
    elif kind == "frozen":
        xyz = np.repeat(rng.random((1, n, 3)) * length, frames, axis=0)
    else:
        xyz = (rng.random((1, n, 3)) * length
               + np.cumsum(rng.normal(0.0, 0.01, (frames, n, 3)), axis=0))
    return _cell(xyz, ["AR"] * n, length)


class TestALongRunIsHeldInBlocks:

    @pytest.mark.parametrize("kind,frames,blocks", [
        ("independent", 3_000, 300), ("frozen", 3_000, 300), ("diffusing", 3_000, 300),
        ("independent", 2_999, 2_000)])
    def test_blocks_give_the_frames_scatter(self, monkeypatch, kind, frames, blocks):
        """3,000 frames in 300 blocks and in 3,000: the same g, and the same
        scatter to a few percent (the frozen run one frame's). 2,999 frames
        in 2,000 blocks are blocks of 1 and 2 frames, which weighted alike
        read the spread a quarter high."""
        traj = _gas(frames, seed=1, kind=kind)
        pairs, _ = _pairs_between(np.arange(80), np.arange(80), np.random.default_rng(0))
        monkeypatch.setattr(rdf_module, "MOST_BLOCKS", 10_000)
        whole = PairHistogram(traj, pairs, 1.0, 0.05)
        monkeypatch.setattr(rdf_module, "MOST_BLOCKS", blocks)
        blocked = PairHistogram(traj, pairs, 1.0, 0.05)
        assert blocked.counts.shape[0] == blocks and whole.counts.shape[0] == frames
        assert np.allclose(blocked.g(), whole.g(), rtol=1e-12)
        outer = whole.radii > 0.3
        ratio = blocked.scatter()[outer] / whole.scatter()[outer]
        assert np.median(ratio) == pytest.approx(1.0, abs=0.05)
        if kind == "frozen":
            one = PairHistogram(traj[:1], pairs, 1.0, 0.05).scatter()[outer]
            assert np.allclose(blocked.scatter()[outer], one, rtol=1e-6)

    def test_the_halves_are_halves_of_the_blocks(self, monkeypatch):
        monkeypatch.setattr(rdf_module, "MOST_BLOCKS", 100)
        traj = _gas(1_000, seed=2, kind="independent")
        pairs, _ = _pairs_between(np.arange(80), np.arange(80), np.random.default_rng(0))
        histogram = PairHistogram(traj, pairs, 1.0, 0.05)
        first, second = histogram.halves()
        assert np.allclose(histogram.g(first),
                           PairHistogram(traj[:500], pairs, 1.0, 0.05).g(), rtol=1e-12)
        assert np.allclose(histogram.g(second),
                           PairHistogram(traj[500:], pairs, 1.0, 0.05).g(), rtol=1e-12)


def test_a_half_of_one_configuration_repeated_is_one_frame():
    """Frames A, A, B, B: each half is one configuration twice, whatever the
    other half holds (it was judged over the whole run)."""
    rng = np.random.default_rng(5)
    a, b = rng.random((60, 3)) * 2.0, rng.random((60, 3)) * 2.0
    traj = _cell(np.array([a, a, b, b]), ["AR"] * 60, 2.0)
    pairs, _ = _pairs_between(np.arange(60), np.arange(60), np.random.default_rng(0))
    histogram = PairHistogram(traj, pairs, 0.95, 0.01)
    one = PairHistogram(traj[:1], pairs, 0.95, 0.01).scatter()
    assert np.allclose(histogram.scatter(histogram.halves()[0]), one)


class TestAShellIsAHillTheCurveFallsFrom:

    @staticmethod
    def _r():
        return np.arange(0.0, 2.2, 0.005) + 0.0025

    def test_a_curve_still_climbing_has_no_first_peak(self):
        """A protein against its water: g(r) climbs from 0.3 to just over 1
        at 2 nm, the protein's volume taken from the box, and a wiggle on
        that climb was named the first peak (villin, 1.88 nm, 1.019)."""
        r = self._r()
        g = 1.04 - 0.74 * np.exp(-r / 0.6) + 0.004 * np.exp(-((r - 1.88) / 0.02) ** 2)
        g[r < 0.15] = 0.0
        noise = np.full_like(g, 0.001)
        peak, _bar, _searched, resolved = first_peak(r, g, noise)
        assert not resolved

    def test_a_hydration_hill_under_bulk_is_the_first_shell(self):
        """Protein N and O against water: a hill at 0.283 nm (g 0.65) the
        curve falls from to 0.3 is the first shell, under bulk as it is, so
        a later wiggle is not named in its place."""
        r = self._r()
        g = (1.04 - 0.74 * np.exp(-r / 0.6)
             + 0.45 * np.exp(-((r - 0.283) / 0.02) ** 2)
             - 0.2 * np.exp(-((r - 0.33) / 0.02) ** 2)
             + 0.03 * np.exp(-((r - 1.9) / 0.03) ** 2))
        g[r < 0.2] = 0.0
        peak, _bar, _searched, resolved = first_peak(r, g, np.full_like(g, 0.01))
        assert not resolved and r[peak] == pytest.approx(0.283, abs=0.006)

    def test_a_curve_falling_across_the_excluded_volume_is_not_a_hill(self):
        """Falling across 0.15 nm from a closer peak, then a shell at 0.30."""
        r = self._r()
        g = 1.0 + 2.0 * np.exp(-((r - 0.12) / 0.02) ** 2) + np.exp(-((r - 0.30) / 0.03) ** 2)
        peak, _bar, _searched, resolved = first_peak(r, g, np.full_like(g, 0.03))
        assert resolved and r[peak] == pytest.approx(0.30, abs=0.006)

    def test_a_shell_in_one_bin_stands_alone(self, tmp_path):
        """A cold crystal's shell fills one bin, fifty times bulk."""
        traj = _simple_cubic(0.0, frames=1)
        analysis = RadialDistribution(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        analysis.compute(traj)
        assert analysis.findings["rdf"]["first_peak_nm"] == pytest.approx(0.282, abs=0.005)

    @pytest.mark.parametrize("seed", [1, 3])
    def test_a_trough_above_bulk_between_two_shells_ends_the_first(self, tmp_path, seed):
        """Simple cubic at 0.03 nm: the trough at 0.36 nm falls only to about
        0.96, and the minimum past the second shell gave 26 to 81 neighbours.
        The shells overlap at this spread, so the count is near 6, not 6."""
        analysis = CoordinationNumber(selection_a="all", selection_b="all",
                                      output_dir=str(tmp_path))
        counts = analysis.compute(_simple_cubic(0.03, 3, n=10, seed=seed))
        assert analysis.findings["shell"]["cutoff_nm"] < 0.282 * np.sqrt(2)
        assert float(np.mean(counts)) == pytest.approx(6.0, abs=0.7)
