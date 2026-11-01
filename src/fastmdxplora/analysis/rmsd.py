"""Root-Mean-Square Deviation (RMSD).

Computes the per-frame RMSD of an MD trajectory against a chosen reference
frame, with optional rigid-body alignment (Kabsch superposition) applied
prior to the distance calculation. The atom subset used for both the
alignment and the RMSD calculation is controlled by the ``selection``
attribute; by default the alpha-carbon backbone is used, which is the
convention for protein conformational analysis.

The output figure is a time-series of RMSD vs. simulation time (or frame
index if no timestep is available), with the chosen reference frame
marked. Output data is a single-column ``rmsd.dat`` of RMSD values in
nanometers (MDTraj's native unit).

References
----------
The implementation delegates to MDTraj's :func:`mdtraj.rmsd`, which uses
the QCP method of Theobald (Acta Cryst. A, 2005) — an O(N) algorithm for
minimum RMSD without explicit eigendecomposition.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.protein_names import ALPHA_CARBONS
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


#: Selections offered where the one given matches fewer atoms than a
#: superposition needs, the first that matches enough named: the backbone
#: (alanine dipeptide's caps and its one residue give seven), then the
#: protein's heavy atoms, then every heavy atom but water's.
SELECTIONS_THAT_ALIGN = ("backbone", "protein and not element H",
                         "not water and not element H")


def selection_that_aligns(traj: md.Trajectory, needed: int = 3) -> tuple[str, int] | None:
    """The first of :data:`SELECTIONS_THAT_ALIGN` matching at least
    ``needed`` atoms of ``traj``, with how many it matches; None where none
    does."""
    for selection in SELECTIONS_THAT_ALIGN:
        try:
            found = len(traj.topology.select(selection))
        except Exception:  # noqa: BLE001 -- a selection this topology cannot make
            continue
        if found >= needed:
            return selection, found
    return None


def refuse_too_few_to_align(analysis: Analysis, traj: md.Trajectory,
                            atom_idx: np.ndarray) -> None:
    """Refuse an analysis that superposes frames on fewer atoms than a
    superposition needs (``min_atoms_to_align``), naming the selection that
    would work here.

    On one atom (alanine dipeptide's one alpha carbon, the default
    selection) every frame superposes onto every other exactly, so every
    RMSD read 0 and a clustering found one cluster, said as results. The
    plan leaves such an analysis out by its default selection, but one named
    in ``include`` or given its own selection was run.
    """
    needed = int(getattr(analysis, "min_atoms_to_align", 0) or 0)
    found = len(atom_idx)
    if found >= needed:
        return
    selection = analysis.selection if analysis.selection is not None else "all"
    works = selection_that_aligns(traj, needed)
    atoms = f"{found} atom" + ("" if found == 1 else "s")
    remedy = (f"Set `selection: {works[0]}` under the analysis's "
              f"`options: {{{analysis.name}: ...}}`, which matches {works[1]} atoms here."
              if works else
              f"No selection of {needed} or more atoms of a molecule was found here, so "
              f"{analysis.name} does not apply to this system.")
    raise StudyError(
        f"{remedy} {analysis.description or analysis.name} superposes the frames on "
        f"`{selection}`, which matches {atoms} here, and a superposition needs "
        f"{needed}: on one atom every frame fits every other exactly, each RMSD 0 and "
        f"the frames one structure, and on two only their separation is compared.",
        code="analysis.selection.arity", expression=selection, expected=needed,
        found=found, option="selection", selection_that_works=works[0] if works else None)


class RMSD(Analysis):
    """Per-frame root-mean-square deviation.

    Parameters
    ----------
    ref : int, default 0
        Reference frame index for the RMSD calculation. Negative indices
        count from the end (``-1`` = last frame).
    align : bool, default True
        If True (recommended), structurally align each frame to the
        reference before computing the distance. Without alignment, the
        result includes rigid-body rotation/translation which is rarely
        the quantity of interest.
    selection : str, optional
        MDTraj atom selection string. Defaults to ``"protein and name CA"`` (alpha
        carbons) for protein analysis. For all atoms, pass
        ``selection="all"``.
    **kwargs
        Standard base-class options (``output_dir``, ``title``, ``xlabel``,
        ``ylabel``, ``figsize``, ``xunit``).

    Examples
    --------
    Default (CA atoms, reference = frame 0, aligned)::

        rmsd = RMSD()
        rmsd.run(trajectory)

    RMSD against the last frame, on heavy atoms::

        rmsd = RMSD(ref=-1, selection="not element H")

    Compute only, plot separately::

        rmsd = RMSD()
        data = rmsd.compute(trajectory)   # 1-D array in nanometers

    The output file is ``rmsd.dat`` (single column, nm).
    """

    name = "rmsd"
    time_series = True
    reweightable = (None, "RMSD (nm)")
    description = "Root-mean-square deviation"
    default_selection = ALPHA_CARBONS
    #: A superposition needs three atoms to be defined.
    min_atoms_to_align = 3

    def __init__(
        self,
        *,
        ref: int = 0,
        align: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ref: int = int(ref)
        self.align: bool = bool(align)
        # Record analysis-specific options so the manifest reflects them.
        self.options.update(ref=self.ref, align=self.align)

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute the per-frame RMSD.

        Returns
        -------
        np.ndarray of shape (n_frames,)
            RMSD in nanometers (MDTraj convention).
        """
        atom_idx = self.select_atoms(traj)

        # Resolve negative reference indices (Pythonic convention).
        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise StudyError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            , code="analysis.option.out_of_range")

        if self.align:
            refuse_too_few_to_align(self, traj, atom_idx)
            # MDTraj's rmsd() with atom_indices implicitly aligns before
            # computing the distance.
            rmsd_nm = md.rmsd(traj, traj, frame=ref, atom_indices=atom_idx)
        else:
            # No alignment: compute Euclidean deviation per atom, average,
            # sqrt — mirroring MDTraj's formula minus the rotation step.
            ref_xyz = traj.xyz[ref, atom_idx, :]
            disps = traj.xyz[:, atom_idx, :] - ref_xyz
            rmsd_nm = np.sqrt(np.mean(np.sum(disps * disps, axis=2), axis=1))

        # Stash the resolved reference frame for the plot.
        self._resolved_ref = ref
        return rmsd_nm.astype(np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis_for_plot(self.result, self._traj_for_plot)
        ax.plot(x, result, linewidth=1.4)

        # Mark the reference frame
        ref_x = x[self._resolved_ref]
        ax.axvline(
            ref_x,
            color=colour("GUIDE"),
            linestyle=":",
            linewidth=1.0,
            label=f"reference (frame {self._resolved_ref})",
        )
        ax.legend(loc="best")

    # Helpers --------------------------------------------------------------
    # The base class doesn't pass the trajectory through to plot(), so
    # the trajectory and the resolved x-axis are cached on the instance
    # during run() via a small override.
    _traj_for_plot: md.Trajectory | None = None
    _resolved_ref: int = 0

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def frame_axis_for_plot(
        self, result: np.ndarray, traj: md.Trajectory | None
    ) -> tuple[np.ndarray, str]:
        """Return x-axis values + label, using the cached trajectory."""
        if traj is None:
            # Fallback if plot() is somehow called without run()
            return np.arange(len(result)), "Frame"
        return self.frame_axis(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label

    def default_ylabel(self) -> str | None:
        return "RMSD (nm)"


# Self-register on import
register_analysis(RMSD.name, RMSD)
