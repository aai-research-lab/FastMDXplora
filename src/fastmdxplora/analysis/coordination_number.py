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


def _scatter_at(radius: float, radii: np.ndarray, g: np.ndarray) -> float:
    """The noise g(r) carries at ``radius``, scaled from the bulk.

    The scatter of a radial distribution function is not constant in r, and
    treating it as though it were is how a structureless liquid comes to
    report a first shell. The number of pairs falling in a bin at radius r
    goes as the shell volume, r^2 dr, so the relative counting scatter goes
    as 1/sqrt(N) and therefore as 1/r. A peak at 0.16 nm is measured against
    roughly eight times the noise of the bulk at 1.3 nm, and comparing it
    against the bulk figure instead makes the first noisy bin look like a
    hydration shell.

    Measured on randomly placed points in a 4 nm box: the standard deviation
    of g(r) was 0.569 over r in [0.15, 0.30) and 0.071 over [1.5, 2.0),
    a ratio of 8.0 against the 1/r prediction of 7.8. The model is used
    rather than a fitted threshold because it follows from the geometry.
    """
    bulk_mask = radii >= radii[int(len(radii) * 2 / 3)]
    bulk = g[bulk_mask & np.isfinite(g)]
    if bulk.size < 3:
        return 0.0
    bulk_radius = float(np.mean(radii[bulk_mask]))
    if radius <= 0.0 or bulk_radius <= 0.0:
        return 0.0
    return float(np.std(bulk)) * (bulk_radius / radius)


def _first_minimum(radii: np.ndarray, g: np.ndarray) -> tuple[float, str]:
    """Where the first solvation shell ends, or why it cannot be said.

    Returns ``(cutoff_nm, "")`` on success and ``(nan, reason)`` otherwise.

    A first minimum is only meaningful after a first maximum: the curve has
    to rise into a shell before it can fall out of one. So the peak is
    located first, and the minimum is the lowest point after it and before
    the curve has climbed back to the second shell. Taking a global argmin
    instead would return the r-axis origin on every trajectory, where g(r)
    is zero because no two atoms are on top of each other.

    The same guard `rdf` applies to peaks applies here, with one correction:
    the scatter is measured at the peak's own radius rather than in the
    bulk. See :func:`_scatter_at`.
    """
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
    noise = _scatter_at(float(radii[peak]), radii, g)
    if noise > 0.0 and height <= 3.0 * noise:
        return float("nan"), (
            f"g(r) rises {height:+.3f} above bulk at its tallest point "
            f"({radii[peak]:.3f} nm), against a scatter of {noise:.3f} at "
            "that separation -- counting noise grows as 1/r, so a bin this "
            "close in is far noisier than the bulk. There is no first shell "
            "resolved here, so there is no first minimum to take a cutoff "
            "from. Give `cutoff` explicitly, or run for longer."
        )

    after = g[peak:]
    after_r = radii[peak:]
    if after.size < 3:
        return float("nan"), (
            f"The tallest point of g(r) is at {radii[peak]:.3f} nm, at the "
            "edge of the measurable range, so the curve never falls out of "
            "the first shell within half the box. No cutoff can be read from "
            "it."
        )

    minimum = int(np.argmin(np.where(np.isfinite(after), after, np.inf)))
    if minimum == len(after) - 1:
        return float("nan"), (
            "g(r) is still falling where the box runs out, so the lowest "
            "point after the first peak is the end of the range rather than "
            "a shell boundary. Give `cutoff` explicitly."
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
        half_box = float(np.min(traj.unitcell_lengths)) / 2.0
        rng = np.random.default_rng(0)
        pairs = np.array(
            [(int(i), int(j)) for i in a for j in b if int(i) != int(j)],
            dtype=int)
        if len(pairs) == 0:
            raise StudyError(
                "The two selections name the same single atom, so there are "
                "no pairs to build a g(r) from.",
                code="analysis.selection.arity",
            )
        if len(pairs) > MAX_PAIRS_FOR_SHELL:
            keep = rng.choice(len(pairs), MAX_PAIRS_FOR_SHELL, replace=False)
            pairs = pairs[np.sort(keep)]

        radii, g = md.compute_rdf(
            traj, pairs, r_range=(0.0, half_box), bin_width=SHELL_BIN_WIDTH)
        cutoff, reason = _first_minimum(radii, g)
        if reason:
            raise StudyError(reason, code="analysis.sampling.no_variance")
        self.findings.setdefault("shell", {})["cutoff_from_rdf_nm"] = cutoff
        self.findings["shell"]["read_from"] = (
            "the first minimum of g(r) between these two selections on this "
            "trajectory, computed at a "
            f"{SHELL_BIN_WIDTH:g} nm bin width over 0 to "
            f"{half_box:.3g} nm."
        )
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

        half_box = float(np.min(traj.unitcell_lengths)) / 2.0
        if cutoff > half_box:
            raise StudyError(
                f"A cutoff of {cutoff:g} nm is larger than half the smallest "
                f"box dimension ({half_box:.3g} nm). Past that the "
                "minimum-image convention supplies only part of each shell, "
                "so the count would be of an incomplete sphere and would "
                "still look like a coordination number.",
                code="analysis.option.out_of_range",
            )

        counts = np.zeros(traj.n_frames, dtype=np.float64)
        # `compute_neighbors` takes one frame's worth of query atoms at a
        # time and applies the box of that frame, which is what a constant-
        # pressure run needs -- the cell moves, and a shell measured against
        # frame zero's box would drift with it.
        b_set = set(int(i) for i in b)
        for frame in range(traj.n_frames):
            total = 0
            for atom in a:
                neighbours = md.compute_neighbors(
                    traj[frame], cutoff, np.array([int(atom)]),
                    haystack_indices=b)[0]
                # An atom in both selections is its own neighbour at zero
                # separation; it is not coordinating itself.
                total += int(sum(1 for n in neighbours
                                 if int(n) != int(atom) and int(n) in b_set))
            counts[frame] = total

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
