"""How many of one group sit within a shell of the other, per frame.

The count a solvation number is quoted as: water oxygens around an ion,
around a solute, around a named residue. It is a running integral of the
radial distribution function, and it carries the same requirement -- a
periodic box to take minimum-image distances in.

**The cutoff is the measurement.** A coordination number is not a property
of the system alone; it is a property of the system and the radius somebody
drew. Water around Mg(2+) is six at 0.28 nm and eleven at 0.35 nm, and both
numbers are correct answers to different questions. So there is no default
cutoff here. One is asked for, or the analysis works one out from this
run's own g(r) and says which -- but it is never assumed, because a wrong
default produces a number that looks exactly like a right one.

``cutoff="rdf"`` takes the first minimum of g(r) between the two selections,
which is the conventional boundary of the first shell: the separation at
which the density returns to bulk before the second shell begins. Where the
curve has no resolvable first minimum -- a run too short for hydration
structure, a pairing with no shell -- that is a refusal rather than the
argmin of a noisy array, for the same reason `rdf` declines to report a
first peak it cannot see.

What is counted is neighbours per atom of ``selection_a``, averaged over
that selection. Counting the pairs instead would make the number scale with
how many atoms were selected, which is a different quantity wearing the same
name.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: Bin width in nm for the g(r) used to locate the first minimum. The same
#: as `rdf`'s, and for the same reason: a first minimum is a broad, flat
#: feature, and narrowing the bins to place it more precisely buys
#: resolution the counting statistics do not support.
SHELL_BIN_WIDTH = 0.005

#: Pairs beyond this many are subsampled before the shell search. The count
#: itself is exact -- it uses a neighbour search, not the histogram -- so
#: this caps only the curve the cutoff is read from.
MAX_PAIRS_FOR_SHELL = 200_000

#: Distances computed at once when counting, so a large ``selection_a``
#: against a large shell is taken in pieces rather than in one array.
PAIRS_PER_PASS = 2_000_000


def _complete_shells_reach(traj: md.Trajectory) -> float:
    """How far a shell is complete in every frame: half the shortest lattice
    vector, which half the shortest cell edge overstates in a squashed cell.
    A frame whose cell is empty or not a number is refused."""
    from fastmdxplora.analysis.rdf import _reach_or_refuse

    return _reach_or_refuse(traj)


def _shell_counts(traj: md.Trajectory, cutoff: float, a: np.ndarray,
                  b: np.ndarray) -> np.ndarray:
    """Atoms of ``b`` within ``cutoff`` of each atom of ``a``, summed over
    ``a``, per frame, under the minimum image.

    One neighbour search over the whole trajectory finds the atoms of ``b``
    within the cutoff of any atom of ``a`` in any frame; the count per atom
    of ``a`` is then taken over those alone, with MDTraj's minimum-image
    distances, which hold for triclinic cells and use each frame's own box.
    The search per atom of ``a`` per frame that this replaces gave the same
    counts and took 80 s for 188 atoms over two frames of a solvated
    protein. An atom in both selections is not its own neighbour.
    """
    near = md.compute_neighbors(traj, cutoff, a, haystack_indices=b)
    candidates = (np.unique(np.concatenate([np.asarray(n, dtype=int) for n in near]))
                  if near else np.zeros(0, dtype=int))
    counts = np.zeros(traj.n_frames, dtype=np.float64)
    if candidates.size == 0:
        return counts
    step = max(1, PAIRS_PER_PASS // (candidates.size * max(1, traj.n_frames)))
    for start in range(0, len(a), step):
        chunk = np.asarray(a[start:start + step], dtype=int)
        pairs = np.column_stack([np.repeat(chunk, candidates.size),
                                 np.tile(candidates, chunk.size)])
        pairs = pairs[pairs[:, 0] != pairs[:, 1]]
        if len(pairs) == 0:
            continue
        distances = md.compute_distances(traj, pairs)
        counts += np.count_nonzero(distances < cutoff, axis=1)
    return counts



def _smoothed(values: np.ndarray, bins: int) -> np.ndarray:
    """A running mean over ``bins`` bins (an odd number) along the last
    axis, the ends padded with their own values (zero padding pulled the
    first bins below bulk)."""
    values = np.asarray(values, dtype=np.float64)
    if bins <= 1:
        return values
    half = bins // 2
    padded = np.pad(values, [(0, 0)] * (values.ndim - 1) + [(half, half)], mode="edge")
    # Each window summed on its own, so a level stretch stays exactly level.
    windows = np.lib.stride_tricks.sliding_window_view(padded, bins, axis=-1)
    return windows.sum(axis=-1) / bins


#: The width, in nm, the first minimum is looked for over: a running mean
#: across it, so a noisy bin in a shallow trough does not set the cutoff.
#: Seven bins at the 0.005 nm width.
MINIMUM_SMOOTHING_NM = 0.035

#: A trough wider than this, in nm, over which the count changes, has no
#: minimum to read.
FLAT_TROUGH_NM = 0.1

#: A share of the count below which where the cutoff falls in a trough
#: does not matter: an empty gap between a crystal's shells.
LEVEL_COUNT = 0.01

#: Scatters of a difference of two smoothed values that the stretch of
#: trough indistinct from its minimum is drawn at.
LEVEL_SCATTERS = 2.0

#: Scatters of a difference of two smoothed values that a dip has to be
#: climbed out of to be a minimum.
RESOLVED_SCATTERS = 3.0


def _smoothing_bins(radii: np.ndarray) -> int:
    width = float(radii[1] - radii[0]) if len(radii) > 1 else SHELL_BIN_WIDTH
    return max(1, int(round(MINIMUM_SMOOTHING_NM / width))) | 1


def _running(radii: np.ndarray, g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bin edges and the integral of g(r) over the shells up to each, which
    is the coordination number up to a density."""
    width = float(radii[1] - radii[0]) if len(radii) > 1 else SHELL_BIN_WIDTH
    edges = np.concatenate([[radii[0] - width / 2], radii + width / 2])
    shells = (4.0 / 3.0) * np.pi * (edges[1:] ** 3 - edges[:-1] ** 3)
    return edges, np.concatenate([[0.0], np.cumsum(np.nan_to_num(g) * shells)])


def _out_of(floor: float, resolved: float) -> float:
    """What a curve at ``floor`` has to climb above to be out of its trough:
    clear of the floor by ``resolved``, and back at bulk within it. A climb
    that stays below bulk is a wiggle inside one trough, and in a long
    trough noise makes such wiggles somewhere."""
    return max(floor + resolved, 1.0 - resolved)


def _first_minimum(radii: np.ndarray, g: np.ndarray, noise: np.ndarray,
                   smooth_noise: np.ndarray | None = None,
                   record: dict[str, Any] | None = None) -> tuple[float, str]:
    """Where the first solvation shell ends, or why it cannot be said.

    Returns ``(cutoff_nm, "")`` on success and ``(nan, reason)`` otherwise.
    ``noise`` is the scatter of g(r) in each bin and ``smooth_noise`` that
    of its running mean over :data:`MINIMUM_SMOOTHING_NM`
    (:meth:`PairHistogram.scatter`; ``noise`` over the root of the bins
    when not given). ``record``, if given, takes the stretch of trough the
    minimum is not distinct from (``range_nm``) and whether the count is
    level across it.

    A first minimum is only meaningful after a first maximum: the curve has
    to rise into a shell before it can fall out of one. So the peak is
    located first, and must stand above bulk by more than its own scatter
    times the bar a 1% chance of noise reaching it in any of the bins
    searched sets (about 3.9 for 300 bins, never under 3). The minimum is
    then the first dip clear below bulk after it that the curve climbs
    back out of before going lower, back to bulk and by more than a
    difference of two smoothed values could by noise (three of its
    scatters, more where a long trough is searched): the lowest point after
    the peak could lie past the second shell, and a shallow trough can run
    on to a second, lower dip.

    The trough around it that is within two scatters of a difference (or a
    tenth of its depth) of it is where the minimum could be. Where the count
    changes by under 1% across it, as across the empty gap between a
    crystal's shells, its middle is the cutoff; where it is wider than
    :data:`FLAT_TROUGH_NM` and the count does change, there is no minimum
    to read.
    """
    from statistics import NormalDist

    finite = np.isfinite(g)
    if finite.sum() < 8:
        return float("nan"), (
            "g(r) between these two selections has too few usable bins to "
            "locate a shell boundary."
        )

    # Inside 0.15 nm nothing is a shell; it is the excluded volume where the
    # two atoms cannot both be.
    searchable = radii > 0.15
    if not searchable.any():
        return float("nan"), (
            "The g(r) range is shorter than the excluded volume, so there is "
            "no region a shell could occupy."
        )

    masked = np.where(searchable & finite, g, -np.inf)
    peak = int(np.argmax(masked))
    if not np.isfinite(masked[peak]):
        return float("nan"), "g(r) is empty over the searchable range."

    height = float(g[peak]) - 1.0
    scatter = float(noise[peak]) if np.isfinite(noise[peak]) else np.inf
    searched = max(1, int(np.count_nonzero(searchable & finite)))
    bar = max(3.0, NormalDist().inv_cdf(1.0 - 0.01 / searched))
    if height <= bar * scatter:
        return float("nan"), (
            f"g(r) rises {height:+.3f} above bulk at its tallest point "
            f"({radii[peak]:.3f} nm), against a scatter of {scatter:.3f} at "
            "that separation, from its own counts and from how much it "
            f"varies over the run, and {bar:.1f} times that is needed where "
            f"the tallest of {searched} bins is taken. There is no first "
            "shell resolved here, so "
            "there is no first minimum to take a cutoff from. Give `cutoff` "
            "explicitly, or run for longer."
        )

    after_r = radii[peak:]
    if after_r.size < 3:
        return float("nan"), (
            f"The tallest point of g(r) is at {radii[peak]:.3f} nm, at the "
            "edge of the range the box resolves, so the curve never falls out of "
            "the first shell within half the box. No cutoff can be read from "
            "it."
        )

    bins = _smoothing_bins(radii)
    smooth = _smoothed(np.where(finite, g, 1.0), bins)[peak:]
    if smooth_noise is None:
        smooth_noise = np.asarray(noise, dtype=np.float64) / np.sqrt(bins)
    # A scatter that is not a number is not resolved by anything.
    spread = np.nan_to_num(np.asarray(smooth_noise, dtype=np.float64)[peak:],
                           nan=np.inf, posinf=np.inf)
    # Clearing bulk, or climbing out of a trough, by noise alone happens
    # somewhere in a long range, so the bar is the one a 1% chance over the
    # independent stretches searched sets, as for the peak, never under
    # RESOLVED_SCATTERS.
    stretches = max(1, int(np.count_nonzero(searchable & finite)) // bins)
    climb = max(RESOLVED_SCATTERS, NormalDist().inv_cdf(1.0 - 0.01 / stretches))
    resolved = climb * np.sqrt(2.0) * spread

    # A trough is where the curve stands clear below bulk, and the second
    # shell begins where, having done so, it stands clear above it again.
    n = len(smooth)
    below = np.nonzero(smooth < 1.0 - climb * spread)[0]
    end = n
    if below.size:
        rising = np.nonzero((np.arange(n) > below[0]) & (smooth > 1.0 + climb * spread))[0]
        if rising.size:
            end = int(rising[0]) + 1

    minimum = -1
    for k in range(1, min(end, n - 1)):
        if not (smooth[k] <= smooth[k - 1] and smooth[k] < smooth[k + 1]
                and smooth[k] < 1.0 - climb * spread[k]):
            continue
        later = smooth[k + 1:]
        lower = np.nonzero(later < smooth[k])[0]
        out = np.nonzero(later > _out_of(smooth[k], resolved[k]))[0]
        if out.size and (not lower.size or out[0] < lower[0]):
            minimum = k
            break
    if minimum < 0:
        if not below.size:
            reached = np.nonzero(smooth - 1.0 <= climb * spread)[0]
            ended = (f"; the shell has ended by {after_r[reached[0]]:.3f} nm, "
                     "where the curve is back at bulk" if reached.size else "")
            return float("nan"), (
                f"After its first peak at {radii[peak]:.3f} nm g(r) does not "
                "fall below bulk by more than its scatter within the range "
                "the cell resolves, so there is no trough and no minimum to "
                "read, and the count goes on rising with the bulk past the "
                f"shell. Give `cutoff` explicitly{ended}."
            )
        if int(np.argmin(smooth)) >= n - bins:
            return float("nan"), (
                "g(r) is still falling where the box runs out, so the lowest "
                "point after the first peak is the end of the range rather than "
                "a shell boundary. Give `cutoff` explicitly."
            )
        lowest = int(np.argmin(smooth[:end]))
        return float("nan"), (
            f"After its first peak g(r) falls to {1.0 - smooth[lowest]:.3f} "
            f"below bulk (at {after_r[lowest]:.3f} nm) and does not climb back "
            "out of the trough by more than its scatter before going lower, "
            "so the trough has no minimum to read, and a cutoff in it would "
            "be a choice rather than a measurement. Give `cutoff` "
            "explicitly, or run for longer."
        )

    # The basin the minimum lies in, up to where the curve climbs out of it
    # on either side, and the part of it not distinct from the minimum.
    floor = float(smooth[minimum])
    left, right = minimum, minimum
    while left > 0 and smooth[left - 1] - floor <= resolved[minimum]:
        left -= 1
    while right < n - 1 and smooth[right + 1] <= _out_of(floor, resolved[minimum]):
        right += 1
    depth = 1.0 - floor
    # Two smoothed values are told apart at LEVEL_SCATTERS scatters of
    # their difference, which is root two of one's.
    level = floor + max(0.1 * depth, LEVEL_SCATTERS * np.sqrt(2.0) * float(spread[minimum]))
    near = left + np.nonzero(smooth[left:right + 1] <= level)[0]
    low, high = float(after_r[near[0]]), float(after_r[near[-1]])
    edges, running = _running(radii, g)
    counted = np.interp([low, float(after_r[minimum]), high], edges, running)
    change = float(counted[2] - counted[0]) / max(float(counted[1]), 1e-300)
    flat = change <= LEVEL_COUNT
    if record is not None:
        record.update(range_nm=(low, high), level=flat)
    if flat:
        return 0.5 * (low + high), ""
    if high - low > FLAT_TROUGH_NM:
        return float("nan"), (
            f"After its first peak g(r) stays within its scatter of its "
            f"lowest point ({depth:.3f} below bulk) from {low:.3f} to "
            f"{high:.3f} nm, and the count changes by {100.0 * change:.0f}% "
            "across that, so the trough has no minimum to read, and a cutoff "
            "anywhere in it would be a choice rather than a measurement. "
            "Give `cutoff` explicitly, or run for longer."
        )
    return float(after_r[minimum]), ""


class CoordinationNumber(Analysis):
    """Neighbours of ``selection_b`` within ``cutoff`` of ``selection_a``.

    Parameters
    ----------
    selection_a : str, default "protein"
        The group the shell is drawn around. The count is reported per atom
        of this selection.
    selection_b : str, default "water and name O"
        The group being counted. Water oxygens by default, which is what a
        hydration number counts.
    cutoff : float or "rdf", default "rdf"
        The shell radius in nm. The default takes the first minimum of g(r)
        between the two selections *on this trajectory* and records which
        value it used -- a measurement rather than an assumption, and a
        refusal where the curve has no shell in it. There is no numeric
        default: see the module docstring.
    per_atom : bool, default True
        Report the mean number of neighbours per atom of ``selection_a``.
        False reports the total count over the whole selection, which scales
        with how many atoms were selected.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``coordination_number.dat`` -- one column, the coordination number per
    frame.
    """

    name = "coordination_number"
    description = "Coordination number"
    time_series = True
    reweightable = (None, "Coordination number")
    #: The two selections are the analysis's own; a third would name neither.
    honours_selection = False
    default_selection = None
    #: Minimum-image distances need a cell, and a shell that runs past the
    #: box edge is counted from the wrong image or not at all.
    requires_periodic_box = True
    #: The default pairing counts water, and a default run saves the solute
    #: alone -- the same gate `rdf` and `water_sites` sit behind.
    requires_water = True
    #: A coordination number is asked about a particular pair -- water round
    #: an ion, an ion round a site -- and the default pairing is a starting
    #: point rather than a question anybody posed. Left out of the automatic
    #: plan and run when a study names it, so a run that has no shell to
    #: find is not a failed phase on every unremarkable trajectory.
    requires_naming = True

    def __init__(
        self,
        *,
        selection_a: str = "protein",
        selection_b: str = "water and name O",
        cutoff: float | str = "rdf",
        per_atom: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.selection_a = str(selection_a)
        self.selection_b = str(selection_b)
        self.per_atom = bool(per_atom)

        if cutoff is None:
            raise StudyError(
                "`cutoff` takes a distance in nm, or \"rdf\" to read one off "
                "this run's own g(r). It cannot be left empty: a "
                "coordination number is a count within a radius somebody "
                "chose -- water around Mg2+ is six at 0.28 nm and eleven at "
                "0.35 nm -- so there is no number to fall back to.",
                code="analysis.option.missing_companion",
            )
        if isinstance(cutoff, str):
            if cutoff.strip().lower() != "rdf":
                raise StudyError(
                    f"`cutoff` takes a distance in nm or the word \"rdf\"; "
                    f"got {cutoff!r}.",
                    code="analysis.option.not_permitted",
                )
            self.cutoff: float | str = "rdf"
        else:
            value = float(cutoff)
            if not np.isfinite(value) or value <= 0.0:
                raise StudyError(
                    f"`cutoff` must be a positive distance in nm; got "
                    f"{cutoff!r}.",
                    code="analysis.option.out_of_range",
                )
            self.cutoff = value

        self.options.update(
            selection_a=self.selection_a,
            selection_b=self.selection_b,
            cutoff=self.cutoff,
            per_atom=self.per_atom,
        )

    # ------------------------------------------------------------------
    def _groups(self, traj: md.Trajectory) -> tuple[np.ndarray, np.ndarray]:
        a = traj.topology.select(self.selection_a)
        b = traj.topology.select(self.selection_b)
        for label, selection, found in (
                ("selection_a", self.selection_a, a),
                ("selection_b", self.selection_b, b)):
            if len(found) == 0:
                raise StudyError(
                    f"{label} {selection!r} matched no atoms, so there is no "
                    "shell to count in.",
                    code="analysis.selection.empty",
                )
        return a, b

    def _cutoff_from_rdf(
        self, traj: md.Trajectory, a: np.ndarray, b: np.ndarray
    ) -> float:
        """The first minimum of g(r) between the two groups, in nm."""
        half_box = _complete_shells_reach(traj)
        from fastmdxplora.analysis.rdf import PairHistogram, _pairs_between

        # Drawn as the RDF analysis draws them, without listing every pair
        # first: a protein against its water is tens of millions of them.
        pairs, available = _pairs_between(a, b, np.random.default_rng(0),
                                          limit=MAX_PAIRS_FOR_SHELL)
        if len(pairs) == 0:
            raise StudyError(
                "The two selections name the same single atom, so there are "
                "no pairs to build a g(r) from.",
                code="analysis.selection.arity",
            )

        histogram = PairHistogram(traj, pairs, half_box, SHELL_BIN_WIDTH)
        radii = histogram.radii
        bins = _smoothing_bins(radii)

        def read(frames):
            found: dict[str, Any] = {}
            cutoff, reason = _first_minimum(
                radii, histogram.g(frames), histogram.scatter(frames),
                histogram.scatter(frames, window=bins), found)
            return cutoff, reason, found

        cutoff, reason, found = read(slice(None))
        if reason:
            raise StudyError(reason, code="analysis.sampling.no_variance")

        # Neighbours per atom of `a` up to r, from the curve: the pairs in a
        # shell are g times all the pairs times the shell over the volume.
        edges, running = _running(radii, histogram.g())
        density = available / float(len(a)) * float(np.mean(1.0 / histogram.volumes))

        def count(r: float) -> float:
            return float(density * np.interp(r, edges, running))

        low, high = found["range_nm"]
        shell = self.findings.setdefault("shell", {})
        shell.update({
            "cutoff_from_rdf_nm": cutoff,
            "read_from": (
                "the first minimum of g(r) between these two selections on "
                "this trajectory, computed at a "
                f"{SHELL_BIN_WIDTH:g} nm bin width over 0 to {half_box:.3g} nm "
                f"and smoothed over {bins} bins ({bins * SHELL_BIN_WIDTH:.3g} nm)."),
            "cutoff_range_nm": [low, high],
            "coordination_number_range": [count(low), count(high)],
        })
        if found["level"]:
            said = (
                f"The cutoff, {cutoff:.3f} nm, is the middle of the trough "
                f"between the first two shells of g(r), which is level from "
                f"{low:.3f} to {high:.3f} nm: the count is the same anywhere "
                "in it.")
        else:
            said = (
                f"The cutoff, {cutoff:.3f} nm, is the first minimum of g(r). "
                f"The curve is within its scatter of that minimum from "
                f"{low:.3f} to {high:.3f} nm, over which the count runs from "
                f"{count(low):.2f} to {count(high):.2f} neighbours per atom of "
                "`selection_a`.")

        # Read again from each half of the run: a minimum that moves with
        # the frames read, outside where the whole run places it, is not one
        # the run measures (O-O in 1 M NaCl; the halves of a slowly moving
        # gas), and neither is one no half finds on its own.
        if traj.n_frames >= 2:
            half = traj.n_frames // 2
            halves = []
            for frames in (np.arange(half), np.arange(half, traj.n_frames)):
                value, failed, _ = read(frames)
                halves.append(None if failed else value)
            shell["cutoff_from_each_half_nm"] = halves
            read_in = [v for v in halves if v is not None]
            if not read_in:
                raise StudyError(
                    f"g(r) over the whole run has a first minimum at "
                    f"{cutoff:.3f} nm, but neither half of the run resolves "
                    "one on its own, so the minimum rests on too few frames "
                    "to be a measurement. Give `cutoff` explicitly, or run "
                    "for longer.", code="analysis.sampling.no_variance")
            margin = SHELL_BIN_WIDTH
            outside = [v for v in read_in if not low - margin <= v <= high + margin]
            if outside:
                said_halves = " and ".join(
                    f"{v:.3f}" if v is not None else "none" for v in halves)
                raise StudyError(
                    f"g(r) over the whole run has a first minimum at "
                    f"{cutoff:.3f} nm, within its scatter from {low:.3f} to "
                    f"{high:.3f} nm, but the two halves of the run put it at "
                    f"{said_halves} nm: the cutoff moves with the frames read, "
                    "so it is not one the run measures. Give `cutoff` "
                    "explicitly, or run for longer.",
                    code="analysis.sampling.no_variance")
            if len(read_in) == 2:
                counts = [count(v) for v in halves]
                shell["coordination_number_at_each_half_s_cutoff"] = counts
                said += (
                    f" Read from each half of the run the minimum falls at "
                    f"{halves[0]:.3f} and {halves[1]:.3f} nm, where the count "
                    f"is {counts[0]:.2f} and {counts[1]:.2f}.")
            else:
                which = "first" if halves[0] is None else "second"
                said += (
                    f" The {which} half of the run alone resolves no minimum; "
                    f"the other puts it at {read_in[0]:.3f} nm.")
        shell["said"] = said
        return cutoff

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        if traj.unitcell_lengths is None:
            raise StudyError(
                "This trajectory carries no unit cell, so neighbours cannot "
                "be counted under the minimum-image convention and a shell "
                "reaching past the box edge would be counted from the wrong "
                "image. A trajectory stripped of its box, or one built in "
                "vacuum, does this.",
                code="analysis.system.inapplicable",
            )

        a, b = self._groups(traj)
        cutoff = (
            self._cutoff_from_rdf(traj, a, b)
            if self.cutoff == "rdf" else float(self.cutoff)
        )

        half_box = _complete_shells_reach(traj)
        if cutoff > half_box:
            raise StudyError(
                f"A cutoff of {cutoff:g} nm is larger than half the cell's "
                f"shortest lattice vector ({half_box:.3g} nm). Past that the "
                "minimum-image convention supplies only part of each shell, "
                "so the count would be of an incomplete sphere and would "
                "still look like a coordination number.",
                code="analysis.option.out_of_range",
            )

        # Each frame against its own box, which is what a constant-pressure
        # run needs: the cell moves, and a shell counted against frame
        # zero's box would drift with it.
        counts = _shell_counts(traj, cutoff, np.asarray(a, dtype=int),
                               np.asarray(b, dtype=int))

        if self.per_atom:
            counts = counts / float(len(a))

        record: dict[str, Any] = self.findings.get("shell", {})
        record.update({
            "cutoff_nm": cutoff,
            "atoms_in_a": int(len(a)),
            "atoms_in_b": int(len(b)),
            "per_atom": self.per_atom,
        })
        self.findings["shell"] = record
        return counts

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis(self._traj_for_plot) if (
            self._traj_for_plot is not None
        ) else (np.arange(len(result)), "Frame")
        ax.plot(x, result, linewidth=1.4)

    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def default_ylabel(self) -> str | None:
        if self.per_atom:
            return "Coordination number"
        return "Neighbours in shell"


register_analysis(CoordinationNumber.name, CoordinationNumber)
