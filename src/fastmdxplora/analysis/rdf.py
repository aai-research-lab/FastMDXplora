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

#: Fewer frames than this and the spread over frames is not trusted as a
#: variance: the counting noise alone is used.
FRAMES_FOR_A_SPREAD = 10

#: Bins a correlation time is read over (:meth:`PairHistogram.scatter`).
INEFFICIENCY_BINS = 7


class PairHistogram:
    """Pair distances counted per frame and per bin, once.

    g(r) is made from these counts exactly as ``md.compute_rdf`` makes it
    (``N_pairs sum_f 1/V_f`` times each shell's volume normalises the
    total), and so is the noise it carries (:meth:`scatter`), from the
    counts' spread over frames, without a second pass over the distances.
    """

    def __init__(self, traj: md.Trajectory, pairs: np.ndarray, r_max: float,
                 bin_width: float) -> None:
        n_bins = int(r_max / bin_width)
        self.edges = np.linspace(0.0, r_max, n_bins + 1)
        self.radii = 0.5 * (self.edges[1:] + self.edges[:-1])
        self.shells = (4.0 / 3.0) * np.pi * (self.edges[1:] ** 3 - self.edges[:-1] ** 3)
        self.volumes = np.asarray(traj.unitcell_volumes, dtype=np.float64)
        self.n_pairs = len(pairs)
        self.counts = np.zeros((traj.n_frames, n_bins), dtype=np.int32)
        step = max(1, DISTANCES_PER_PASS // max(1, len(pairs)))
        span = np.array([0.0, r_max])
        for first in range(0, traj.n_frames, step):
            distances = md.compute_distances(traj[first:first + step], pairs)
            # The binning `md.compute_rdf` gives its one histogram, frame by
            # frame.
            for k, frame in enumerate(distances):
                self.counts[first + k] = np.histogram(frame, bins=n_bins, range=span)[0]
        # A pair listed in both orders, as a selection paired with itself
        # lists it, is one independent count, not two: (sum m)^2 / sum m^2
        # of the listings are independent, m being how often each is listed.
        if len(pairs):
            _unique, listed = np.unique(np.sort(np.asarray(pairs), axis=1), axis=0,
                                        return_counts=True)
            self.independent = float(listed.sum()) ** 2 / float((listed ** 2).sum())
        else:
            self.independent = 0.0

    def g(self, frames: np.ndarray | slice = slice(None)) -> np.ndarray:
        norm = self.n_pairs * float(np.sum(1.0 / self.volumes[frames])) * self.shells
        with np.errstate(divide="ignore", invalid="ignore"):
            return self.counts[frames].sum(axis=0) / norm

    def scatter(self, frames: np.ndarray | slice = slice(None),
                window: int = 1) -> np.ndarray:
        """The standard error of g(r), or of its running mean over ``window``
        bins, in each bin.

        Two estimates, the larger taken. The counting one: with this
        normalisation a bin at r expects, at g = 1, N sum_f (V_shell / V_f)
        pairs, N the independent pairs, so g has a Poisson variance of
        g / expected (g taken as at least 1, the safe side close in). The
        measured one: g in each frame, and its variance over the frames
        divided by their number. Either is multiplied by that bin's
        statistical inefficiency, from the same per-frame series, a frozen
        configuration counting as one frame. The measured spread is what
        the counting one misses where neighbours come and go together; with
        fewer than :data:`FRAMES_FOR_A_SPREAD` frames it is not trusted and
        the counting estimate stands alone. Measured against repeats it is
        1.0 to 1.2 times the true scatter of an ideal gas and up to a
        quarter wide in TIP3P water.

        It used to be the counting estimate widened by the correlation of
        the count around the first peak alone, and the spread of g(r) in the
        bulk before that, which was right for a liquid and wrong wherever
        g(r) has structure there. A correlation longer than the run cannot
        be seen from inside it, by this or any estimate.
        """
        from fastmdxplora.analysis.coordination_number import _smoothed
        from fastmdxplora.statistics import statistical_inefficiency

        counts = self.counts[frames].astype(np.float64)
        volumes = self.volumes[frames]
        n = len(counts)
        inverse = 1.0 / volumes
        g = self.g(frames)
        expected = self.independent * float(inverse.sum()) * self.shells
        with np.errstate(divide="ignore", invalid="ignore"):
            counting = np.clip(np.nan_to_num(g), 1.0, None) / expected
        counting[~np.isfinite(counting)] = np.inf
        per_frame = counts * volumes[:, None] / (self.n_pairs * self.shells[None])
        if window > 1:
            counting = _smoothed(counting, window) / window
            per_frame = _smoothed(per_frame, window)
        variance = counting
        if n >= FRAMES_FOR_A_SPREAD:
            weights = inverse / inverse.sum()
            spread = per_frame.var(axis=0, ddof=1) * float(np.sum(weights ** 2))
            variance = np.maximum(counting, spread)
        if 1 < n < 3 and np.all(counts == counts[:1]):
            # Frames that agree in every bin are one configuration.
            variance = variance * n
        if n >= 3:
            # Taken over the bins around each, INEFFICIENCY_BINS of them: a
            # correlation time is the dynamics', shared by neighbouring
            # bins, and one bin's own estimate scatters so widely that the
            # tallest bin of a slowly moving gas was often one whose
            # estimate had come out low.
            around = (_smoothed(per_frame, INEFFICIENCY_BINS)
                      if window < INEFFICIENCY_BINS else per_frame)
            inefficiency = np.array([statistical_inefficiency(around[:, k])
                                     for k in range(around.shape[1])])
            variance = variance * np.maximum(1.0, inefficiency)
        return np.sqrt(variance)


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


def _is_that_a_peak(radii: np.ndarray, g: np.ndarray, peak: int,
                    noise: np.ndarray | None = None) -> str:
    """Why the tallest point is not a first peak, or empty if it is one.

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
                 f"vary over the run, {bar:.1f} times which is needed where "
                 f"the tallest of {searched} bins is taken")

    if peak >= n - max(2, int(n * 0.05)):
        return (
            f"The tallest point of g(r) is at {radii[peak]:.3f} nm, within a "
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
            f"g(r) rises {height:+.3f} above bulk at its tallest point, "
            f"against a scatter of {scatter:.3f} {where}, so "
            "the tallest point is not distinguishable from the noise of this "
            "curve. No hydration structure is resolved here. A longer run is "
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

        pairs, available = _pairs_between(a, b, np.random.default_rng(0))
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
                f"{MAX_PAIRS} pairs of the {available} available, "
                "drawn once and held fixed across frames so the curve is "
                "not a different estimator in every frame."
            )

        # The first peak, which is what a published value is quoted for --
        # where there is one to quote.
        inside = radii > 0.15
        if inside.any() and np.isfinite(g[inside]).any():
            peak = int(np.argmax(np.where(inside, g, -np.inf)))
            verdict = _is_that_a_peak(radii, g, peak, histogram.scatter())
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
