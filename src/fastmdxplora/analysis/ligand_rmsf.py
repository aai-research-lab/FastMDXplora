"""Ligand RMSF (per-atom fluctuation of the ligand after protein alignment).

After removing protein rigid-body motion (alignment on the protein), this
measures how much each ligand atom fluctuates about its mean position. It
reports the ligand's internal flexibility in the pocket: which parts of the
ligand are rigid and which sample multiple positions. Complements the ligand
pose RMSD (overall displacement) with a per-atom flexibility profile.

Outputs ``ligand_rmsf.dat`` with columns (atom_serial, rmsf_nm) and a bar plot.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.rmsf import _atom_labels


class LigandRMSF(Analysis):
    """Per-atom RMSF of the ligand after aligning on the protein.

    Parameters
    ----------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied by the orchestrator.
    align_selection : str, default "protein and name CA"
        Atoms used for the rigid-body alignment (the receptor frame).
    ref : int, default 0
        Reference frame for the alignment.
    **kwargs
        Standard base-class options.
    """

    name = "ligand_rmsf"
    description = "Ligand RMSF (per-atom flexibility, after protein alignment)"
    requires_ligand = True
    default_selection = None
    #: This works out its own atoms, so a general selection has nothing to
    #: apply to.
    honours_selection = False

    def __init__(
        self,
        *,
        ligand_resname: str | None = None,
        align_selection: str = "protein and name CA",
        ref: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not ligand_resname:
            raise ValueError(
                "LigandRMSF requires `ligand_resname`; it applies only to "
                "protein-ligand complexes."
            )
        self.ligand_resname = str(ligand_resname)
        self.align_selection = str(align_selection)
        self.ref = int(ref)
        self.options.update(
            ligand_resname=self.ligand_resname,
            align_selection=self.align_selection,
            ref=self.ref,
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute per-ligand-atom RMSF.

        Returns
        -------
        np.ndarray, shape (n_ligand_atoms, 2)
            Columns: atom serial, RMSF in nm.
        """
        ligand_idx = traj.topology.select(f"resname {self.ligand_resname}")
        if len(ligand_idx) == 0:
            raise ValueError(
                f"No atoms matched ligand resname {self.ligand_resname!r}; "
                f"cannot compute ligand RMSF."
            )
        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) == 0:
            raise ValueError(
                f"Alignment selection {self.align_selection!r} matched zero "
                f"atoms; cannot align on the protein."
            )

        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise ValueError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            )

        # Align on the protein, then measure ligand-atom fluctuations about
        # their mean position on the aligned coordinates.
        aligned = superposed(traj, frame=ref, atom_indices=align_idx)
        xyz = aligned.xyz[:, ligand_idx, :]
        mean_xyz = xyz.mean(axis=0)
        disp = xyz - mean_xyz
        per_atom = np.sqrt(np.mean(np.sum(disp * disp, axis=2), axis=0))

        serials = _atom_labels(
            [traj.topology.atom(int(i)) for i in ligand_idx]
        )
        return np.column_stack([serials, per_atom]).astype(np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        serials = result[:, 0].astype(int)
        rmsf = result[:, 1]
        x = np.arange(len(serials))
        ax.bar(x, rmsf, color=colour("SERIES"))
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in serials], fontsize=7, rotation=90)

    def default_xlabel(self) -> str | None:
        return "Ligand atom (serial)"

    def default_ylabel(self) -> str | None:
        return "RMSF (nm)"


register_analysis(LigandRMSF.name, LigandRMSF)
