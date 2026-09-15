"""The distance between two selections, per frame.

The plainest question anyone asks of a trajectory -- how far apart did
these two things stay -- and the one most often answered with a script that
forgets the box. Two residues, a residue and a ligand, a ligand and an ion:
whatever the pair, this reports one distance per frame.

**Two distances, and they answer different questions.** ``measure="com"``
takes the separation of the two centres of mass, which is what "the
distance between residue 45 and the ligand" usually means and what a
collective variable is usually defined on. ``measure="closest"`` takes the
smallest atom-to-atom separation, which is what a contact criterion means
-- two large groups can have centres 1.2 nm apart while touching. Both are
offered because both get asked for, and the one used is recorded beside the
number.

**Distances are periodic where the trajectory says so.** Two groups in a
solvated box may be on opposite faces of the cell and adjacent under the
minimum-image convention; measured as written they are a box apart. The
convention is applied where a unit cell exists, and its own limit -- a
genuine separation past half the box, which it reports as the short way
round -- is checked and marked rather than left in the curve.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

#: Atom pairs beyond this many are measured frame by frame rather than in
#: one array, to keep a closest-approach between two large groups from
#: allocating a matrix the size of the trajectory.
MAX_PAIRS_AT_ONCE = 2_000_000

MEASURES = ("com", "closest")


def _minimum_image(delta: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """Displacements folded into the cell, for an orthorhombic box.

    Applied to centre-of-mass separations, which MDTraj's own distance
    routine cannot take because a centre of mass is not an atom.
    """
    return delta - lengths * np.round(delta / lengths)


class PairDistance(Analysis):
    """Distance between two atom selections, per frame.

    Parameters
    ----------
    selection_a, selection_b : str
        The two groups, as MDTraj selection expressions. Both are required:
        there is no pair of things a default could name.
    measure : {"com", "closest"}, default "com"
        ``"com"`` separates the two centres of mass; ``"closest"`` takes the
        smallest atom-to-atom distance. See the module docstring.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``pair_distance.dat`` -- one column, the distance in nm per frame.
    """

    name = "pair_distance"
    description = "Distance between two selections"
    time_series = True
    reweightable = (None, "Distance (nm)")
    #: Two selections of its own; a third would name neither of them.
    honours_selection = False
    default_selection = None
    #: There is no pair a default plan could pick. Every other analysis has
    #: a subject the trajectory supplies -- the protein, the ligand, the
    #: solvent -- and this one's subject is two things somebody had a reason
    #: to compare. Run when a study names it, and absent otherwise.
    requires_naming = True

    def __init__(
        self,
        *,
        selection_a: str | None = None,
        selection_b: str | None = None,
        measure: str = "com",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        for label, value in (("selection_a", selection_a),
                             ("selection_b", selection_b)):
            if value is None or not str(value).strip():
                raise StudyError(
                    f"`{label}` is required: a distance is between two "
                    "things, and there is no pair a default could stand in "
                    "for. Give an MDTraj selection, for example "
                    "\"resid 44\" or \"resname LIG\".",
                    code="analysis.option.missing_companion",
                )
        measure = str(measure).strip().lower()
        if measure not in MEASURES:
            raise StudyError(
                f"`measure` must be one of {', '.join(MEASURES)}; got "
                f"{measure!r}.",
                code="analysis.option.not_permitted",
            )

        self.selection_a = str(selection_a)
        self.selection_b = str(selection_b)
        self.measure = measure
        self.options.update(
            selection_a=self.selection_a,
            selection_b=self.selection_b,
            measure=self.measure,
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
                    "distance to measure.",
                    code="analysis.selection.empty",
                )
        shared = set(int(i) for i in a) & set(int(i) for i in b)
        if shared:
            raise StudyError(
                f"The two selections share {len(shared)} atom(s), so part of "
                "each group is being measured against itself. A "
                "centre-of-mass separation between overlapping groups is "
                "pulled toward zero by the shared atoms, and a closest "
                "approach between them is exactly zero.",
                code="analysis.selection.arity",
            )
        return a, b

    def _centres(
        self, traj: md.Trajectory, idx: np.ndarray
    ) -> np.ndarray:
        """Mass-weighted centres per frame, falling back to geometric."""
        xyz = traj.xyz[:, idx, :].astype(np.float64)
        masses = []
        for i in idx:
            element = getattr(traj.topology.atom(int(i)), "element", None)
            mass = getattr(element, "mass", None)
            if not mass:
                masses = []
                break
            masses.append(float(mass))
        if masses:
            weights = np.asarray(masses, dtype=np.float64)
            self.findings.setdefault("centres", {})["mass_weighted"] = True
        else:
            weights = np.ones(len(idx), dtype=np.float64)
            self.findings.setdefault("centres", {})["mass_weighted"] = False
            self.findings["centres"]["note"] = (
                "This topology carries no elements for one of the "
                "selections, so its centre is geometric rather than a centre "
                "of mass. On a group of mixed elements the two differ."
            )
        weights = weights / weights.sum()
        return (xyz * weights[None, :, None]).sum(axis=1)

    def _com_distance(
        self, traj: md.Trajectory, a: np.ndarray, b: np.ndarray
    ) -> np.ndarray:
        delta = self._centres(traj, b) - self._centres(traj, a)
        if traj.unitcell_lengths is not None:
            delta = _minimum_image(
                delta, np.asarray(traj.unitcell_lengths, dtype=np.float64))
        return np.linalg.norm(delta, axis=1)

    def _closest_distance(
        self, traj: md.Trajectory, a: np.ndarray, b: np.ndarray
    ) -> np.ndarray:
        periodic = traj.unitcell_lengths is not None
        pairs = np.array([(int(i), int(j)) for i in a for j in b], dtype=int)
        if len(pairs) <= MAX_PAIRS_AT_ONCE:
            return md.compute_distances(
                traj, pairs, periodic=periodic).min(axis=1).astype(np.float64)
        closest = np.empty(traj.n_frames, dtype=np.float64)
        for frame in range(traj.n_frames):
            closest[frame] = md.compute_distances(
                traj[frame], pairs, periodic=periodic).min()
        return closest

    def _note_if_the_convention_is_strained(
        self, traj: md.Trajectory, distances: np.ndarray
    ) -> None:
        if traj.unitcell_lengths is None:
            self.findings["periodic"] = (
                "This trajectory carries no unit cell, so distances are taken "
                "as written. Two groups on opposite faces of a cell that was "
                "stripped read as a box apart."
            )
            return
        half_box = float(np.min(traj.unitcell_lengths)) / 2.0
        longest = float(np.max(distances))
        if longest > 0.8 * half_box:
            self.findings["not_a_measurement"] = (
                f"The pair reaches {longest:.3f} nm, against half the "
                f"smallest box dimension at {half_box:.3f} nm. The "
                "minimum-image convention returns the shorter of the two ways "
                "round, so a separation past half the box is reported as an "
                "approach and the curve folds back with nothing to show that "
                "it has."
            )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        a, b = self._groups(traj)
        if self.measure == "com":
            distances = self._com_distance(traj, a, b)
        else:
            distances = self._closest_distance(traj, a, b)

        self.findings["pair"] = {
            "atoms_in_a": int(len(a)),
            "atoms_in_b": int(len(b)),
            "measure": self.measure,
        }
        self._note_if_the_convention_is_strained(traj, distances)
        return distances

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (
            self.frame_axis(self._traj_for_plot)[0]
            if self._traj_for_plot is not None
            else np.arange(len(result))
        )
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
        if self.measure == "closest":
            return "Closest approach (nm)"
        return "Centre-of-mass distance (nm)"


register_analysis(PairDistance.name, PairDistance)
