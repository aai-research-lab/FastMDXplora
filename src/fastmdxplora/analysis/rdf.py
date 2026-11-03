"""The radial distribution function between two selections.

How the density of one group varies with distance from the other, relative
to what it would be if the two ignored each other. A value of one means no
structure at that separation; the first peak is the first solvation shell,
and where it sits and how tall it stands are quantities with published
values for every common water model, which makes this one of the few things
a simulation reports that can be checked against a number somebody else
measured.

**A radial distribution function needs a box.** The normalisation divides
by the bulk density, and the bulk density is the pair count divided by the
volume the system occupies. A trajectory carrying no unit cell has no such
volume, and the curve that comes out of assuming one is a histogram wearing
the units of a g(r).

**And it stops at half the box.** Beyond that separation the minimum-image
convention no longer supplies a complete shell: part of every sphere lies
outside the periodic cell and is counted from the wrong image or not at
all, so g(r) sags towards zero for reasons that have nothing to do with the
liquid. The curve stays smooth and plausible while it does this, which is
why the range is capped here rather than left to the person reading the
plot.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: Pairs beyond this many are subsampled before the histogram. A protein
#: against every water oxygen is tens of millions of pairs per frame, which
#: is minutes of arithmetic for a curve that a random tenth of them
#: resolves to within the width of its own line.
MAX_PAIRS = 400_000


def _pairs_between(a: np.ndarray, b: np.ndarray, rng: np.random.Generator,
                   limit: int | None = None) -> tuple[np.ndarray, int]:
    """Atom pairs (i, j), i from ``a`` and j from ``b`` and i != j, at most
    ``limit`` (default :data:`MAX_PAIRS`) of them; returns the pairs and how
    many there were in all.

    The pairs are in ``a``-major order. Where there are more than ``limit``,
    ``limit`` positions in that order are drawn without replacement by
    ``rng.choice`` and kept in order, and only those pairs are built: a
    protein of 3,000 atoms against 10,000 water oxygens is 3e7 pairs, and
    listing them all as Python tuples before drawing took about 25 s and
    gigabytes for a draw of 400,000. Position k is turned into its pair
    arithmetically, skipping the one i == j in each row whose atom is in
    both selections, so the pairs drawn are the ones the full list gave.
    """
    limit = MAX_PAIRS if limit is None else int(limit)
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    in_b = np.isin(a, b)
    per_row = np.where(in_b, len(b) - 1, len(b))
    total = int(per_row.sum())
    if total <= limit:
        i = np.repeat(a, len(b))
        j = np.tile(b, len(a))
        keep = i != j
        return np.column_stack([i[keep], j[keep]]), total

    flat = np.sort(rng.choice(total, limit, replace=False))
    ends = np.cumsum(per_row)
    row = np.searchsorted(ends, flat, side="right")
    offset = flat - (ends[row] - per_row[row])
    # Where row's atom is also in b, the column at its own place in b is the
    # i == j pair left out, so offsets at or past it move one along.
    sorted_b = np.argsort(b, kind="stable")
    place = np.searchsorted(b[sorted_b], a[row])
    own = np.where(in_b[row], sorted_b[np.minimum(place, len(b) - 1)], len(b))
    column = offset + (offset >= own)
    return np.column_stack([a[row], b[column]]), total


#: Pair distances held at once while histogramming, as pairs times frames.
DISTANCES_PER_PASS = 10_000_000

#: Pair distances worth computing over a whole run, as pairs times frames:
#: about 2.5 s. A short run of a large box takes more pairs within it than
#: :data:`MAX_PAIRS`, up to :data:`MOST_PAIRS`, because the near pairs a
#: shell is read from are a few in ten thousand of a large box's pairs: 200,000
#: pairs of 36,000 water oxygens put about one pair a frame in each bin of
#: the first shell at bulk density, and its peak was not resolved in 60 frames.
PAIR_FRAMES = 100_000_000

#: The most pairs taken, however short the run.
MOST_PAIRS = 2_000_000


def pairs_for(n_frames: int, least: int) -> int:
    """How many pairs to draw for a run of ``n_frames``: :data:`PAIR_FRAMES`
    over the frames, never under ``least`` nor over :data:`MOST_PAIRS`."""
    return int(max(least, min(MOST_PAIRS, PAIR_FRAMES // max(1, n_frames))))


#: Fewer frames than this and the spread over frames is not trusted as a
#: variance: the counting noise alone is used.
FRAMES_FOR_A_SPREAD = 10

#: Bins a correlation time is read over (:meth:`PairHistogram.scatter`).
INEFFICIENCY_BINS = 7

#: The most blocks of consecutive frames the counts are held in. Up to this
#: many frames each frame is its own block; past it frames are summed into
#: blocks, so a run of 100,000 frames holds 2,000 rows, not 100,000 (0.4 GB
#: for 1,000 bins, and more for every copy the scatter made).
MOST_BLOCKS = 2_000


class PairHistogram:
    """Pair distances counted per frame and per bin, once, held in blocks of
    consecutive frames.

    g(r) is made from these counts exactly as ``md.compute_rdf`` makes it
    (``N_pairs sum_f 1/V_f`` times each shell's volume normalises the
    total), and so is the noise it carries (:meth:`scatter`), from the
    counts' spread over the blocks, without a second pass over the
    distances. With at most :data:`MOST_BLOCKS` frames a block is a frame.
    Beside each block's sums the sums of squares of each frame's g over
    :data:`INEFFICIENCY_BINS` bins are held, so the correlation
    inside a block is still counted (:meth:`scatter`).
    """

    def __init__(self, traj: md.Trajectory, pairs: np.ndarray, r_max: float,
                 bin_width: float) -> None:
        from fastmdxplora.analysis.coordination_number import _smoothed

        n_bins = int(r_max / bin_width)
        self.edges = np.linspace(0.0, r_max, n_bins + 1)
        self.radii = 0.5 * (self.edges[1:] + self.edges[:-1])
        self.shells = (4.0 / 3.0) * np.pi * (self.edges[1:] ** 3 - self.edges[:-1] ** 3)
        volumes = np.asarray(traj.unitcell_volumes, dtype=np.float64)
        self.n_pairs = len(pairs)
        self.n_frames = traj.n_frames
        n_blocks = min(self.n_frames, MOST_BLOCKS)
        # Block k holds frames bounds[k] to bounds[k + 1].
        self.bounds = np.linspace(0, self.n_frames, n_blocks + 1).round().astype(np.int64)
        self.sizes = np.diff(self.bounds)
        block_of = np.repeat(np.arange(n_blocks), self.sizes)
        shape = (n_blocks, n_bins)
        self.counts = np.zeros(shape, dtype=np.int64)
        self.g_sum = np.zeros(shape)
        self.around_squares = np.zeros(shape)
        self.inverse_volume = np.bincount(block_of, weights=1.0 / volumes,
                                          minlength=n_blocks)
        # Frames per pass held to DISTANCES_PER_PASS values, distances or
        # bins, whichever a frame has more of.
        step = max(1, DISTANCES_PER_PASS // max(1, len(pairs), n_bins))
        span = np.array([0.0, r_max])
        norm = max(1, self.n_pairs) * self.shells
        # Whether every frame counts the same pairs in every bin: frames of
        # one configuration, which read as one frame.
        self.frozen = self.n_frames > 1
        first_counts = None
        for first in range(0, self.n_frames, step):
            distances = md.compute_distances(traj[first:first + step], pairs)
            # The binning `md.compute_rdf` gives its one histogram, frame by
            # frame.
            counts = np.array([np.histogram(frame, bins=n_bins, range=span)[0]
                               for frame in distances])
            if first_counts is None:
                first_counts = counts[0].copy()
            if self.frozen and not np.all(counts == first_counts):
                self.frozen = False
            per_frame = counts * volumes[first:first + len(counts), None] / norm
            around = _smoothed(per_frame, INEFFICIENCY_BINS)
            # The pass's frames run in order, so each block's are together.
            rows = block_of[first:first + len(counts)]
            starts = np.flatnonzero(np.r_[True, rows[1:] != rows[:-1]])
            which = rows[starts]
            self.counts[which] += np.add.reduceat(counts, starts, axis=0)
            self.g_sum[which] += np.add.reduceat(per_frame, starts, axis=0)
            self.around_squares[which] += np.add.reduceat(around ** 2, starts, axis=0)
        # A pair listed in both orders, as a selection paired with itself
        # lists it, is one independent count, not two: (sum m)^2 / sum m^2
        # of the listings are independent, m being how often each is listed.
        if len(pairs):
            ordered = np.sort(np.asarray(pairs, dtype=np.int64), axis=1)
            keys = ordered[:, 0] * (int(ordered.max()) + 1) + ordered[:, 1]
            _unique, listed = np.unique(keys, return_counts=True)
            self.independent = float(listed.sum()) ** 2 / float((listed ** 2).sum())
        else:
            self.independent = 0.0

    @property
    def n_blocks(self) -> int:
        return len(self.sizes)

    def halves(self) -> tuple[slice, slice]:
        """The two halves of the run, as blocks."""
        half = self.n_blocks // 2
        return slice(0, half), slice(half, self.n_blocks)

    def g(self, blocks: np.ndarray | slice = slice(None)) -> np.ndarray:
        norm = self.n_pairs * float(np.sum(self.inverse_volume[blocks])) * self.shells
        with np.errstate(divide="ignore", invalid="ignore"):
            return self.counts[blocks].sum(axis=0) / norm

    def scatter(self, blocks: np.ndarray | slice = slice(None),
                window: int = 1) -> np.ndarray:
        """The standard error of g(r), or of its running mean over ``window``
        bins, in each bin.

        Two estimates, the larger taken. The counting one: with this
        normalisation a bin at r expects, at g = 1, N sum_f (V_shell / V_f)
        pairs, N the independent pairs, so g has a Poisson variance of
        g / expected (g taken as at least 1, the safe side close in). The
        measured one: g in each block, and its variance over the blocks
        divided by their number. Each is multiplied by a statistical
        inefficiency read over INEFFICIENCY_BINS bins around each (over the
        window, where it is wider): the
        measured one by that of the blocks' series, the counting one by that
        of the frames, which is the blocks' times how much more the blocks'
        means vary than one frame's g would if the frames in a block were
        independent (a frozen configuration counting as one frame). The
        measured spread is what the counting one misses where neighbours
        come and go together; with fewer than :data:`FRAMES_FOR_A_SPREAD`
        frames it is not trusted and the counting estimate stands alone.
        Measured against repeats it is 1.0 to 1.2 times the true scatter of
        an ideal gas and up to a quarter wide in TIP3P water.

        It used to be the counting estimate widened by the correlation of
        the count around the first peak alone, and the spread of g(r) in the
        bulk before that, which was right for a liquid and wrong wherever
        g(r) has structure there. A correlation longer than the run cannot
        be seen from inside it, by this or any estimate.
        """
        from fastmdxplora.analysis.coordination_number import _smoothed
        from fastmdxplora.statistics import statistical_inefficiency

        sizes = self.sizes[blocks].astype(np.float64)
        frames = float(sizes.sum())
        n = len(sizes)
        inverse = self.inverse_volume[blocks]
        g = self.g(blocks)
        expected = self.independent * float(inverse.sum()) * self.shells
        with np.errstate(divide="ignore", invalid="ignore"):
            counting = np.clip(np.nan_to_num(g), 1.0, None) / expected
        counting[~np.isfinite(counting)] = np.inf
        means = self.g_sum[blocks] / sizes[:, None]
        if window > 1:
            counting = _smoothed(counting, window) / window
            means = _smoothed(means, window)
        weights = inverse / inverse.sum()
        measured = np.zeros_like(counting)
        if frames >= FRAMES_FOR_A_SPREAD and n >= 2:
            if np.any(sizes > 1):
                # Each block's mean weighted by its frames: blocks of 1 and
                # 2 frames are not equally precise.
                centre = np.average(means, axis=0, weights=sizes)
                measured = ((sizes[:, None] * (means - centre) ** 2).sum(axis=0)
                            / (n - 1) * float(np.sum(weights ** 2 / sizes)))
            else:
                measured = means.var(axis=0, ddof=1) * float(np.sum(weights ** 2))
        if frames < 3:
            # Frames that agree in every bin are one configuration.
            counts = self.counts[blocks]
            alike = frames > 1 and bool(np.all(counts == counts[:1]))
            return np.sqrt(counting * (frames if alike else 1.0))
        # The correlation, read over the bins around each.
        smoothed_means = _smoothed(self.g_sum[blocks] / sizes[:, None], INEFFICIENCY_BINS)
        around = (_smoothed(means, INEFFICIENCY_BINS) if window < INEFFICIENCY_BINS
                  else means)
        by_block = np.array([statistical_inefficiency(around[:, k]) if n >= 3 else 1.0
                             for k in range(around.shape[1])])
        by_block = np.maximum(1.0, by_block)
        by_frame = by_block
        if np.any(sizes > 1):
            # Blocks of several frames: the frames' inefficiency is the
            # blocks' times how much more their means vary than means of
            # independent frames would (the blocking method).
            mean_around = _smoothed(self.g_sum[blocks].sum(axis=0) / frames,
                                    INEFFICIENCY_BINS)
            of_frames = (self.around_squares[blocks].sum(axis=0) / frames
                         - mean_around ** 2)
            of_blocks = np.average((smoothed_means - mean_around) ** 2, axis=0,
                                   weights=sizes)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(of_frames > 1e-12 * np.maximum(mean_around ** 2, 1e-300),
                                 float(np.mean(sizes)) * of_blocks / of_frames, frames)
            by_frame = np.clip(by_block * np.nan_to_num(ratio, nan=frames), 1.0, frames)
        return np.sqrt(np.maximum(counting * by_frame, measured * by_block))


def _reach_or_refuse(traj: md.Trajectory) -> float:
    """Half the cell's shortest lattice vector over the run, refused where a
    frame's cell is empty or not a number (a zero box written for a frame
    with none)."""
    from fastmdxplora.analysis.pair_distance import _unambiguous_reach

    reach = np.asarray(_unambiguous_reach(traj.unitcell_vectors), dtype=np.float64)
    bad = ~np.isfinite(reach) | (reach <= 0.0)
    if bad.any():
        raise StudyError(
            f"The unit cell is empty or not a number in {int(bad.sum())} of "
            f"{len(reach)} frames (the first, frame {int(np.argmax(bad))}), so "
            "there is no volume to normalise against and no minimum image to "
            "measure in there.",
            code="analysis.system.inapplicable")
    return float(reach.min())


def _what_would_fix_it(selection: str, traj: md.Trajectory) -> str:
    """The setting to change, where the empty selection has a known cause.

    A selection matching nothing is usually a typo, and guessing at a remedy
    would send the reader after the wrong thing. There is one case where the
    cause is known: a selection asking for solvent, on a trajectory that
    holds none, because a default run saves the solute alone. The simulation
    phase says so at the time -- "Analyses of the solvent itself need
    `save_selection: all`" -- but that is a different phase, a different log,
    and hours earlier.

    Returns empty otherwise, which is the honest answer: the analysis knows
    the selection is empty and does not know why.
    """
    if not any(word in selection.lower() for word in ("water", "hoh", "sol", "solvent")):
        return ""
    if any(residue.is_water for residue in traj.topology.residues):
        return ""
    return (
        " This trajectory holds no solvent: a run saves the solute alone"
        " unless told otherwise, so the water this selection asks for was"
        " never written. Set `save_selection: all` in the simulation phase"
        " and run again."
    )


def first_peak(radii: np.ndarray, g: np.ndarray, noise: np.ndarray,
               smooth_noise: np.ndarray | None = None
               ) -> tuple[int, float, int, bool, bool]:
    """The first peak past 0.15 nm that g(r) resolves, or the hill that
    stops one being named.

    Returns ``(index, bar, searched, resolved, shell)``: -1 for an empty
    curve; whether ``index`` is a shell (``shell``) and its peak resolved; the
    bar a 1% chance of noise reaching it in any of the ``searched`` bins
    sets (never under 3). The hills of g(r) smoothed over 0.035 nm are taken
    in order out from 0.15 nm, each one's tallest bin its peak, and a hill is
    a shell when the curve then falls away from it, before climbing as high
    again, by more than two smoothed values differ by noise (the smoothed
    bar, on root two of the smoothed scatter ``smooth_noise``). The first
    shell is the first peak: resolved where its peak stands above bulk by
    ``bar`` of its own scatter (``noise``, :meth:`PairHistogram.scatter`)
    and, where bins are narrower than 0.0175 nm, a bin beside it stands three
    scatters clear too or it clears twice the bar alone (a shell spans
    several bins, a fluke one); unresolved otherwise, and never passed over
    for a later one (a water
    O-H's hydrogen-bond peak, short of the bar in 5 frames, gave way to the
    0.32 nm shell and 34 neighbours). With no such hill the tallest bin is
    returned, unresolved. Two shells closer than the 0.035 nm smoothing are
    one hill. The tallest point of the whole curve is a later
    shell wherever that one stands taller (a water O-H's 0.32 nm peak over
    its 0.18 nm hydrogen bond).
    """
    curve = _ShellSearch(radii, g, noise, smooth_noise)
    if curve.searched == 0:
        return -1, 3.0, 0, False, False
    g, scatter, smooth, bins, n = curve.g, curve.scatter, curve.smooth, curve.bins, len(g)
    searchable, bar = curve.searchable, curve.bar
    # A shell is wider than a bin of 0.005 nm: beside the bin that clears the
    # bar, another of it must stand clear of bulk (three scatters), so one bin
    # of noise does not make a peak; a bin wider than about half a shell
    # holds the shell alone, and a bin twice over the bar stands alone.
    width = float(radii[1] - radii[0]) if len(radii) > 1 else 0.0
    one_bin_shells = width >= 0.0175
    def judged(k: int) -> tuple[int, float, int, bool, bool]:
        low, high = max(0, k - bins // 2), min(n, k + bins // 2 + 1)
        top = low + int(np.argmax(np.where(searchable[low:high], g[low:high], -np.inf)))
        height = float(g[top]) - 1.0
        beside = np.delete(np.arange(low, high), top - low)
        beside = beside[searchable[beside]]
        backed = (one_bin_shells or height > 2.0 * bar * scatter[top]
                  or bool(np.any(g[beside] - 1.0 > 3.0 * scatter[beside])))
        return top, bar, curve.searched, bool(height > bar * scatter[top] and backed), True

    for k in curve.hills:
        fall, _climbs = _fall(smooth, k)
        if not fall > curve.needed(k):
            continue
        # Resolved or not, it is the first shell: a later one is not named
        # in its place.
        return judged(k)
    # No shell: the tallest hill tested, or the tallest bin with no hill.
    if curve.hills:
        k = max(curve.hills, key=lambda h: smooth[h])
        return int(k), bar, curve.searched, False, False
    return (int(np.argmax(np.where(searchable, g, -np.inf))), bar, curve.searched,
            False, False)


class _ShellSearch:
    """What :func:`first_peak` reads a curve with, so its reasons
    (:func:`why_no_first_peak`) are told from the same numbers."""

    def __init__(self, radii: np.ndarray, g: np.ndarray, noise: np.ndarray,
                 smooth_noise: np.ndarray | None) -> None:
        from statistics import NormalDist

        from fastmdxplora.analysis.coordination_number import _smoothed, _smoothing_bins

        finite = np.isfinite(g)
        self.g = g
        self.searchable = (radii > 0.15) & finite
        self.searched = int(np.count_nonzero(self.searchable))
        self.bar = max(3.0, NormalDist().inv_cdf(1.0 - 0.01 / max(1, self.searched)))
        self.bins = _smoothing_bins(radii)
        stretches = max(1, self.searched // self.bins)
        self.smooth_bar = max(3.0, NormalDist().inv_cdf(1.0 - 0.01 / stretches))
        self.scatter = np.where(np.isfinite(noise), noise, np.inf)
        if smooth_noise is None:
            smooth_noise = np.asarray(noise, dtype=np.float64) / np.sqrt(self.bins)
        self.smooth_scatter = np.where(np.isfinite(smooth_noise), smooth_noise, np.inf)
        self.smooth = _smoothed(np.where(finite, g, 1.0), self.bins)
        n, smooth = len(g), self.smooth
        self.hills = [k for k in range(n) if self.searchable[k]
                      and (k == 0 or smooth[k] > smooth[k - 1])
                      and (k == n - 1 or smooth[k] >= smooth[k + 1])]

    def needed(self, k: int) -> float:
        """The fall a hill at ``k`` needs to be a shell: the smoothed bar on
        root two of the smoothed scatter there."""
        return float(self.smooth_bar * np.sqrt(2.0) * self.smooth_scatter[k])


def _fall(smooth: np.ndarray, k: int) -> tuple[float, bool]:
    """How far the curve falls from its hill at ``k`` before it climbs as
    high again, and whether it does."""
    higher = np.nonzero(smooth[k + 1:] > smooth[k])[0]
    end = k + 1 + (int(higher[0]) if higher.size else len(smooth) - k - 1)
    fall = float(smooth[k] - np.min(smooth[k:end])) if end > k + 1 else 0.0
    return fall, bool(higher.size)


def why_no_first_peak(radii: np.ndarray, g: np.ndarray, noise: np.ndarray,
                      found: tuple[int, float, int, bool, bool],
                      smooth_noise: np.ndarray | None = None,
                      frozen: bool = False) -> tuple[str, str]:
    """Why :func:`first_peak`'s answer names no first peak, in a sentence or
    two, told from the numbers it tested, and its kind, which says what
    would change it (:data:`WHAT_WOULD_CHANGE_IT`): ``"climbing"`` (still
    climbing at the end of the range, with no hill falling half what a shell
    needs before it), ``"under bulk"`` (a shell g(r) holds under 1, clear of
    its noise, with where the curve falls to; ``"under bulk, not clear"``
    within it), ``"frozen"`` (frames of one
    configuration) or ``"noise"`` (a longer run would tell). Empty where a
    first peak is named."""
    peak, bar, searched, resolved, shell = found
    if resolved:
        return "", ""
    if peak < 0:
        return "g(r) is empty over the searchable range.", "noise"
    curve = _ShellSearch(radii, g, noise, smooth_noise)
    smooth, n, bins = curve.smooth, len(g), curve.bins
    one = (" Its frames are all one configuration, so they count as one."
           if frozen else "")
    kind = "frozen" if frozen else "noise"
    if not shell:
        # The hill that came nearest to a shell: the first whose fall comes
        # within half of the best fall against the fall it needed.
        tried = [(k, *_fall(smooth, k)) for k in curve.hills]
        ratios = [f / max(curve.needed(k), 1e-300) for k, f, _c in tried]
        best = max(ratios, default=0.0)
        at_end = int(np.argmax(np.where(curve.searchable, smooth, -np.inf))) >= n - bins - 1
        if at_end and best < 0.5:
            return (
                "g(r) is still climbing at the end of the range ("
                f"{float(smooth[-1]):.3f} at {radii[-1]:.3f} nm, smoothed over "
                "0.035 nm), and no hill before it falls away by even half what "
                "a shell needs, so no first shell shows in it. If a longer run "
                "shows none either, there is none around the first selection "
                f"to show (a protein against all its water).{one}"), "climbing"
        if not tried or best < 0.5:
            return (
                "g(r) has no hill past 0.15 nm that falls away by even half "
                "what a shell needs (its highest point is "
                f"{float(g[peak]):.3f} at {radii[peak]:.3f} nm), so no first "
                f"shell is resolved in it.{one}"), kind
        k, fall, climbs = next(t for t, ratio in zip(tried, ratios) if ratio >= 0.5 * best)
        low_bin, high_bin = max(0, k - bins // 2), min(n, k + bins // 2 + 1)
        at = low_bin + int(np.argmax(np.where(curve.searchable[low_bin:high_bin],
                                              g[low_bin:high_bin], -np.inf)))
        return (
            f"g(r)'s first hill that comes near a shell, at {radii[at]:.3f} nm "
            f"({float(smooth[k]):.3f} smoothed over 0.035 nm), falls by "
            f"{fall:.3f}, to {float(smooth[k]) - fall:.3f}, "
            + ("before climbing as high again" if climbs else
               "and does not climb as high again")
            + f", where {curve.needed(k):.3f} is needed to tell it from the "
            "noise there, so no first shell is resolved in it." + one), kind
    height = float(g[peak]) - 1.0
    scatter = float(curve.scatter[peak])
    if height < 0.0:
        top = peak
        while top + 1 < n and smooth[top + 1] > smooth[top]:
            top += 1
        while top > 0 and smooth[top - 1] > smooth[top]:
            top -= 1
        higher = np.nonzero(smooth[top + 1:] > smooth[top])[0]
        end = top + 1 + (int(higher[0]) if higher.size else n - top - 1)
        low = top + int(np.argmin(smooth[top:end]))
        falls_to = (f" The curve falls from it to {float(smooth[low]):.3f} at "
                    f"{radii[low]:.3f} nm (smoothed over 0.035 nm): a `cutoff` of "
                    f"{radii[low]:.3f} nm counts this shell.")
        if -height > bar * scatter:
            return (
                f"g(r)'s first shell, at {radii[peak]:.3f} nm, peaks at "
                f"{float(g[peak]):.3f} in its tallest bin, under bulk: the "
                "molecule the first selection is part of takes up volume "
                "around it, as a protein does, holding g(r) under 1 there, so "
                "no peak above bulk is named, and a longer run will not "
                f"change that.{falls_to}"), "under bulk"
        return (
            f"g(r)'s first shell, at {radii[peak]:.3f} nm, peaks at "
            f"{float(g[peak]):.3f} in its tallest bin, under bulk but not yet "
            f"clear of its noise (a scatter of {scatter:.3f}), so no peak is "
            f"named.{falls_to}{one}"), "under bulk, not clear"
    if height > bar * scatter:
        return (
            f"g(r)'s first hill, at {radii[peak]:.3f} nm, "
            f"stands {height:+.3f} from bulk in one bin, "
            f"{height / scatter:.1f} times its scatter of {scatter:.3f}, over "
            f"the {bar:.1f} needed, but no bin beside it stands three scatters "
            "clear of bulk and it is short of twice that bar, and a shell "
            "spans several bins, so it is not distinguishable from one bin of "
            f"noise.{one}"), kind
    return (
        f"g(r)'s first hill, at {radii[peak]:.3f} nm, "
        f"stands {height:+.3f} from bulk against a scatter of {scatter:.3f} at "
        "that separation, from its own counts and from how much they vary "
        f"over the run, and {bar:.1f} times that is needed with {searched} "
        "bins searched, so it is not distinguishable from the noise of this "
        f"curve.{one}"), kind


#: What would change a refusal of each kind (:func:`why_no_first_peak`).
WHAT_WOULD_CHANGE_IT = {
    "climbing": ("A longer run would tell; a `selection_a` of atoms with a "
                 "shell of their own (for a protein, a site such as a lysine's "
                 "NZ) may show one in `rdf`."),
    "under bulk": "",
    "under bulk, not clear": ("A longer run would show whether it stands under "
                              "bulk; a `cutoff` near where the curve falls to "
                              "gives a count meanwhile."),
    "frozen": "Frames that move, or more atoms, would tell a shell from none.",
    "noise": "A longer run, or more of these atoms, would tell a shell from none.",
}


def _is_that_a_peak(radii: np.ndarray, g: np.ndarray, peak: int,
                    noise: np.ndarray | None = None) -> str:
    """Why the point ``peak`` (:func:`first_peak`) is not a first peak, or
    empty if it is one.

    The tallest point of a curve always exists. On a trajectory too short
    for hydration structure to emerge the curve is flat, its tallest point
    is noise, and reporting it as `first_peak_nm` states a shell where there
    is none. Measured on a 0.1 ns Trp-cage run: g(r) spanned 0.000 to 1.074
    and peaked at 1.782 nm, the last bin before half the box, which was
    duly recorded as a hydration peak at 1.78 nm.

    Two things distinguish a peak from the tallest bin of noise, and both
    are read off the curve rather than set as thresholds:

    * A peak is a local maximum. The last bins are the edge of the
      measurable range, and the tallest point sitting there means the curve
      was still climbing, or flat, when the box ran out.
    * A peak stands above the bulk by more than its own scatter (``noise``,
      :meth:`PairHistogram.scatter`, from the counts in that bin) times the
      bar a 1% chance of noise reaching it in any bin past 0.15 nm sets
      (about 3.9 for 300 bins, never under 3), as `coordination_number`
      asks of the same curve. The spread of the outer third used to stand
      in for the scatter, and is still used for a curve given without its
      counts: on an ideal gas of 300 atoms in a 3 nm cube it is smallest
      where the shells are largest, and a first peak of 2.0 at 0.25 nm was
      reported on 15 gases out of 15.

    The wording follows `not_a_measurement` elsewhere: say what cannot be
    concluded and why, rather than reporting a number with a caveat nobody
    reads.
    """
    n = len(radii)
    bar = 3.0
    if noise is None:
        # The bulk: the outer third, where g(r) should be 1.
        bulk = g[int(n * 2 / 3):]
        bulk = bulk[np.isfinite(bulk)]
        if bulk.size < 3:
            return ""
        scatter = float(np.std(bulk))
        where = "in the bulk region itself"
    else:
        from statistics import NormalDist

        scatter = float(noise[peak]) if np.isfinite(noise[peak]) else 0.0
        searched = max(1, int(np.count_nonzero((radii > 0.15) & np.isfinite(g))))
        bar = max(3.0, NormalDist().inv_cdf(1.0 - 0.01 / searched))
        where = (f"at that separation, from its own counts and how much they "
                 f"vary over the run, {bar:.1f} times which above bulk is "
                 f"needed with {searched} bins searched")

    if peak >= n - max(2, int(n * 0.05)):
        return (
            f"The first peak of g(r) is at {radii[peak]:.3f} nm, within a "
            "few bins of half the box, which is the edge of what a periodic "
            "cell can resolve rather than a shell. A first peak is a local "
            "maximum; this curve was still flat or climbing where it ran "
            "out of range. No peak position is reported. A larger box, or a "
            "run long enough for structure to appear, is what would change "
            "this."
        )

    height = float(g[peak]) - 1.0
    if height <= bar * scatter:
        return (
            f"g(r) stands {height:+.3f} from bulk at {radii[peak]:.3f} nm, "
            f"against a scatter of {scatter:.3f} {where}, so "
            "that peak is not distinguishable from the noise of this "
            "curve. No shell is resolved here. A longer run is "
            "the only remedy."
        )
    return ""


class RadialDistribution(Analysis):
    """g(r) between two atom selections.

    Parameters
    ----------
    selection_a : str, default "protein"
        The first group. Distances are measured from these atoms.
    selection_b : str, default "water and name O"
        The second group. Water oxygens by default, which is the pairing
        a solvation shell is usually read from.
    r_max : float, optional
        Where to stop, in nm. Capped at half the cell's shortest lattice
        vector (for the boxes setup builds, half the smallest box
        dimension), and defaults to it.
    bin_width : float, default 0.005
        Histogram bin width in nm.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``rdf.dat`` -- two columns, separation in nm and g(r).
    """

    name = "rdf"
    description = "Radial distribution function between two selections"

    #: The default pair is the solute against water oxygens, and a default
    #: run saves the solute alone (`save_selection`), so this analysis could
    #: not succeed on a default configuration: two defaults asking for
    #: different things. `water_sites` already declares this and `rdf` did
    #: not, which is the whole of the defect -- the gate existed, this
    #: analysis was simply not behind it.
    #:
    #: The gate is coarser than the question. Whether this analysis needs
    #: water depends on the selections it is given, and the plan is built
    #: before options are merged, so a class attribute is all that can be
    #: declared. The cost falls on one case: a solvent-free trajectory with
    #: custom selections naming neither group as water is now skipped by
    #: default rather than run. That case needs `include: [rdf]`, which is
    #: honoured as written. The case it fixes is every default run.
    requires_water = True
    #: The two selections are the analysis's own, and a scope selection
    #: would name a third thing that is neither of them.
    honours_selection = False
    default_selection = None
    time_series = False
    #: Without a unit cell there is no bulk density to normalise against.
    requires_periodic_box = True

    def __init__(
        self,
        *,
        selection_a: str = "protein",
        selection_b: str = "water and name O",
        r_max: float | None = None,
        bin_width: float = 0.005,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.selection_a = str(selection_a)
        self.selection_b = str(selection_b)
        self.r_max = None if r_max is None else float(r_max)
        self.bin_width = float(bin_width)
        if not np.isfinite(self.bin_width) or self.bin_width <= 0.0:
            raise StudyError(
                f"`bin_width` must be a positive distance in nm; got {bin_width!r}.",
                code="analysis.option.out_of_range")
        if self.r_max is not None and (not np.isfinite(self.r_max) or self.r_max <= 0.0):
            raise StudyError(
                f"`r_max` must be a positive distance in nm; got {r_max!r}.",
                code="analysis.option.out_of_range")
        self.options.update(
            selection_a=self.selection_a,
            selection_b=self.selection_b,
            r_max=self.r_max,
            bin_width=self.bin_width,
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        if traj.unitcell_lengths is None:
            raise StudyError(
                "This trajectory carries no unit cell, so there is no volume "
                "to take a bulk density from and no g(r) to normalise "
                "against it. A trajectory stripped of its box, or one built "
                "in vacuum, does this."
            , code="analysis.system.inapplicable")

        a = traj.topology.select(self.selection_a)
        b = traj.topology.select(self.selection_b)
        for name, selection, found in (
                ("selection_a", self.selection_a, a),
                ("selection_b", self.selection_b, b)):
            if len(found) == 0:
                raise StudyError(
                    f"{name} {selection!r} matched no atoms, so there is no "
                    "distribution between the two groups to report."
                    + _what_would_fix_it(selection, traj)
                , code="analysis.selection.empty")

        # Half the shortest lattice vector, not of the shortest cell edge: in a
        # squashed cell the two differ (0.811 against 1.105 nm), and between
        # them the shells are incomplete and g(r) falls to 0.74.

        half_box = _reach_or_refuse(traj)
        requested = self.r_max if self.r_max is not None else half_box
        r_max = min(requested, half_box)
        capped = self.r_max is not None and self.r_max > half_box
        if int(r_max / self.bin_width) < 1:
            raise StudyError(
                f"r_max of {r_max:g} nm is less than one bin of "
                f"{self.bin_width:g} nm, so there is no curve to plot.",
                code="analysis.option.out_of_range")

        pairs, available = _pairs_between(a, b, np.random.default_rng(0),
                                          limit=pairs_for(traj.n_frames, MAX_PAIRS))
        subsampled = available > len(pairs)

        histogram = PairHistogram(traj, pairs, r_max, self.bin_width)
        radii, g = histogram.radii, histogram.g()

        record: dict[str, Any] = {
            "pairs": int(len(pairs)),
            "r_max_nm": r_max,
            "half_box_nm": half_box,
            "subsampled": subsampled,
        }
        if capped:
            record["capped"] = (
                f"r_max was asked for at {self.r_max:g} nm and stopped at "
                f"{half_box:.3g} nm, half the cell's shortest lattice vector. Past "
                "that the minimum-image convention supplies only part of "
                "each shell, so the curve falls away for a reason belonging "
                "to the box rather than to the liquid, and it does so "
                "smoothly enough to be mistaken for structure."
            )
        if subsampled:
            record["subsampled_note"] = (
                f"{len(pairs):,} pairs of the {available:,} available, "
                "drawn once and held fixed across frames so the curve is "
                "not a different estimator in every frame."
            )

        # The first peak, which is what a published value is quoted for --
        # where there is one to quote.
        inside = radii > 0.15
        if inside.any() and np.isfinite(g[inside]).any():
            from fastmdxplora.analysis.coordination_number import _smoothing_bins

            noise = histogram.scatter()
            found = first_peak(
                radii, g, noise, histogram.scatter(window=_smoothing_bins(radii)))
            peak = found[0]
            verdict, kind = why_no_first_peak(
                radii, g, noise, found, histogram.scatter(window=_smoothing_bins(radii)),
                frozen=histogram.frozen)
            if verdict:
                remedy = WHAT_WOULD_CHANGE_IT[kind]
                verdict += " No first peak is reported." + (f" {remedy}" if remedy else "")
            else:
                verdict = _is_that_a_peak(radii, g, peak, noise)
            if verdict:
                record["not_a_measurement"] = verdict
            else:
                record["first_peak_nm"] = float(radii[peak])
                record["first_peak_height"] = float(g[peak])

        self.findings["rdf"] = record
        return np.column_stack([radii, g])

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        ax.plot(result[:, 0], result[:, 1], linewidth=1.2)
        ax.axhline(1.0, color=colour("GUIDE"), linestyle=":", linewidth=0.8)

    def default_xlabel(self) -> str | None:
        return "r (nm)"

    def default_ylabel(self) -> str | None:
        return "g(r)"


register_analysis(RadialDistribution.name, RadialDistribution)
