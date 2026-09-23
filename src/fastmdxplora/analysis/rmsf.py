"""Root-Mean-Square Fluctuation (RMSF).

Per-residue (default) or per-atom RMSF over the trajectory. The
trajectory is first superposed onto a reference (frame 0 or a user-chosen
reference) using the selected atom subset to remove rigid-body motion;
the per-atom RMSF is then the standard deviation of each atom's position
around its mean. The per-residue RMSF reduces per-atom RMSF to one value
per residue by averaging over the atoms that belong to each residue.

This is the standard "flexibility profile" plot used in nearly every MD
publication — peaks indicate flexible loops/termini, troughs indicate
rigid secondary structure.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _atom_labels(atoms) -> "np.ndarray":
    """Serial numbers for the atom axis, or positions where there are none.

    ``Atom.serial`` is filled in by the file the topology came from. A
    trajectory built in memory -- which the Python API allows and the tests
    use -- has none, and ``None`` became NaN in the column, then a very large
    negative integer once the plot cast it. The saved data carried the NaN.
    """
    return np.array([
        atom.serial if atom.serial is not None else atom.index + 1
        for atom in atoms
    ], dtype=np.int64)


class RMSF(Analysis):
    """Per-residue root-mean-square fluctuation.

    Parameters
    ----------
    ref : int, default 0
        Reference frame for the alignment (superposition) step. The choice
        affects only the bookkeeping; fluctuations are measured relative
        to each atom's mean position over the full trajectory, which is
        invariant under rigid-body alignment.
    per_residue : bool, default True
        If True, collapse the per-atom RMSF down to one value per residue
        by averaging over the residue's atoms. If False, return the
        per-atom array (one value per selected atom).
    selection : str, optional
        MDTraj atom selection. Defaults to ``"name CA"`` (alpha carbons)
        for protein analysis.
    **kwargs
        Standard base-class options.

    Output
    ------
    A two-column ``rmsf.dat`` (residue_index, rmsf_nm) when ``per_residue``
    is True, or (atom_index, rmsf_nm) when False.

    Examples
    --------
    Standard per-residue plot for proteins::

        rmsf = RMSF()
        rmsf.run(trajectory)

    Per-atom on backbone heavy atoms::

        rmsf = RMSF(per_residue=False, selection="backbone and not element H")
    """

    name = "rmsf"
    description = "Root-mean-square fluctuation"
    default_selection = "name CA"
    #: A superposition needs three atoms to be defined.
    min_atoms_to_align = 3

    def __init__(
        self,
        *,
        ref: int = 0,
        per_residue: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ref: int = int(ref)
        self.per_residue: bool = bool(per_residue)
        self.options.update(ref=self.ref, per_residue=self.per_residue)

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute the RMSF.

        Returns
        -------
        np.ndarray, shape (N, 2)
            Two columns: index (residue or atom number) and RMSF in nm.
        """
        atom_idx = self.select_atoms(traj)

        # Align the trajectory onto the reference using the selected atoms.
        # This removes rigid-body translation and rotation so the residual
        # variance is purely conformational fluctuation.
        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise StudyError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            , code="analysis.option.out_of_range")

        aligned = superposed(traj, frame=ref, atom_indices=atom_idx)

        # Per-atom RMSF on the selected atoms only:
        # rmsf[i] = sqrt(mean over frames of ||r_i(t) - <r_i>||^2)
        xyz = aligned.xyz[:, atom_idx, :]
        mean_xyz = xyz.mean(axis=0)
        disp = xyz - mean_xyz
        per_atom = np.sqrt(np.mean(np.sum(disp * disp, axis=2), axis=0))

        if not self.per_residue:
            # Return atom_serial, rmsf
            atoms = [traj.topology.atom(int(i)) for i in atom_idx]
            atom_serials = _atom_labels(atoms)
            return np.column_stack([atom_serials, per_atom]).astype(np.float64)

        # Collapse to per-residue by averaging over each residue's atoms in
        # the selection. Preserve residue order as encountered.
        atoms = [traj.topology.atom(int(i)) for i in atom_idx]
        residues: dict[int, list[float]] = {}
        for atom, val in zip(atoms, per_atom):
            residues.setdefault(atom.residue.index, []).append(float(val))

        # Use residue.resSeq (PDB numbering) for the x axis when available;
        # fall back to topology index otherwise.
        from fastmdxplora.analysis.residues import columns, distinct, number

        rows: list[tuple[int, float]] = []
        for ridx in sorted(residues):
            res = traj.topology.residue(ridx)
            label = number(res)
            # sqrt(mean(MSF)), which is what `rmsf -res` and cpptraj
            # report and therefore what a published per-residue RMSF is.
            # Averaging the RMSF instead reads low wherever a residue has
            # one mobile atom among several rigid ones -- 0.1985 nm against
            # 0.2951 on one floppy atom in four -- and the number is then
            # not comparable with anything. Only bites for a multi-atom
            # selection; the "name CA" default is one atom per residue and
            # the two expressions coincide there.
            values = np.asarray(residues[ridx], dtype=float)
            rows.append((label, float(np.sqrt(np.mean(values ** 2)))))

        ordered = [traj.topology.residue(ridx) for ridx in sorted(residues)]
        if not distinct(traj.topology):
            # A table naming each residue's chain: as the two-column array,
            # the numbers of four copies stood in one column and the line
            # through them doubled back on itself three times.
            import pandas as pd

            return pd.DataFrame({**columns(ordered, traj.topology),
                                 "rmsf_nm": [value for _, value in rows]})
        return np.array(rows, dtype=np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        if hasattr(result, "columns"):
            from fastmdxplora.analysis.residues import plot_by_chain

            plot_by_chain(ax, result, "rmsf_nm", linewidth=1.4, marker="o",
                          markersize=3, markeredgewidth=0)
            return
        x = result[:, 0]
        y = result[:, 1]
        ax.plot(x, y, linewidth=1.4, marker="o", markersize=3, markeredgewidth=0)
        ax.fill_between(x, 0, y, alpha=0.12)

    def default_xlabel(self) -> str | None:
        return "Residue" if self.per_residue else "Atom serial"

    def default_ylabel(self) -> str | None:
        return "RMSF (nm)"


register_analysis(RMSF.name, RMSF)
