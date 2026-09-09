"""Protein-ligand hydrogen bonds.

Counts hydrogen bonds formed specifically *between* the protein and the
ligand, frame by frame — the directional polar interactions that anchor the
ligand in the pocket. This is distinct from the general ``hbonds`` analysis
(which counts all hydrogen bonds within the selection); here every reported
bond has one partner in the protein and the other in the ligand.

A per-frame H-bond list is computed with Wernet-Nilsson (which returns
per-frame donor-H-acceptor triplets), and each triplet is kept only if it
bridges protein and ligand. Outputs ``pl_hbonds.dat`` (frame, n_hbonds).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


class ProteinLigandHBonds(Analysis):
    """Per-frame count of protein-ligand hydrogen bonds.

    Parameters
    ----------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied by the orchestrator.
    protein_selection : str, default "protein"
        The other side of the interaction. Usually the whole protein, and
        worth changing in three cases: a complex where only one chain matters
        (``"protein and chainid 0"``), a domain rather than the whole fold
        (``"protein and resid 40 to 180"``), or a receptor that is not a
        protein at all -- ``"nucleic"`` for a ligand bound to DNA or RNA,
        which is a real and common case that the default name does not
        suggest.
    **kwargs
        Standard base-class options.
    """

    name = "pl_hbonds"
    description = "Protein-ligand hydrogen bonds"
    requires_ligand = True
    default_selection = None
    #: This works out its own atoms, so a general selection has nothing to
    #: apply to.
    honours_selection = False

    def __init__(
        self,
        *,
        ligand_resname: str | None = None,
        protein_selection: str = "protein",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not ligand_resname:
            raise ValueError(
                "ProteinLigandHBonds requires `ligand_resname`; it applies "
                "only to protein-ligand complexes."
            )
        self.ligand_resname = str(ligand_resname)
        self.protein_selection = str(protein_selection)
        self.options.update(
            ligand_resname=self.ligand_resname,
            protein_selection=self.protein_selection,
        )

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        """Compute per-frame protein-ligand H-bond counts.

        Returns
        -------
        pandas.DataFrame
            Columns ``frame, n_hbonds`` (one row per frame).
        """
        ligand_idx = set(
            int(i) for i in traj.topology.select(f"resname {self.ligand_resname}")
        )
        if not ligand_idx:
            raise ValueError(
                f"No atoms matched ligand resname {self.ligand_resname!r}; "
                f"cannot compute protein-ligand hydrogen bonds."
            )
        protein_idx = set(int(i) for i in traj.topology.select(self.protein_selection))
        if not protein_idx:
            raise ValueError(
                f"Protein selection {self.protein_selection!r} matched zero "
                f"atoms; cannot compute protein-ligand hydrogen bonds."
            )

        # H-bond detection needs bond connectivity: a donor is found as a
        # nitrogen or oxygen with a hydrogen bonded to it. create_standard_bonds
        # knows the residues in its library, which does not include a ligand.
        if traj.topology.n_bonds == 0:
            traj.topology.create_standard_bonds()

        # A ligand whose bonds are absent cannot be seen to donate, only to
        # accept, and the count then reports one direction under a name that
        # promises both. The protein having bonds is no comfort here: a PDB
        # without CONECT records for its ligand gives exactly that, and the
        # guard above never fires because n_bonds is not zero.
        ligand_hydrogens = {
            atom.index for atom in traj.topology.atoms
            if atom.index in ligand_idx and atom.element is not None
            and atom.element.symbol == "H"
        }
        if ligand_hydrogens:
            bonded = {
                atom.index
                for bond in traj.topology.bonds
                for atom in bond
            }
            if not (ligand_hydrogens & bonded):
                raise ValueError(
                    f"The ligand {self.ligand_resname!r} has "
                    f"{len(ligand_hydrogens)} hydrogen(s) and no bonds to "
                    "them, so it cannot be seen to donate a hydrogen bond -- "
                    "only to accept one. Counting under those conditions "
                    "would report one direction as though it were both. Give "
                    "a topology that carries the ligand's connectivity: a PDB "
                    "with CONECT records for it, or the topology written by "
                    "the setup phase."
                )

        # Wernet-Nilsson returns, per frame, an array of (donor, H, acceptor)
        # atom-index triplets. Keep a triplet only if the donor and acceptor
        # heavy atoms straddle protein and ligand (one in each).
        per_frame = md.wernet_nilsson(traj)
        counts = np.zeros(traj.n_frames, dtype=int)
        for f, triplets in enumerate(per_frame):
            n = 0
            for donor, _h, acceptor in triplets:
                d, a = int(donor), int(acceptor)
                d_lig, a_lig = d in ligand_idx, a in ligand_idx
                d_prot, a_prot = d in protein_idx, a in protein_idx
                # Cross-pair: one partner protein, the other ligand.
                if (d_prot and a_lig) or (d_lig and a_prot):
                    n += 1
            counts[f] = n

        return pd.DataFrame(
            {"frame": np.arange(traj.n_frames), "n_hbonds": counts}
        )

    def save_data(self, result: pd.DataFrame, path) -> Any:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)
        return path

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        traj = self._traj_for_plot
        if traj is not None:
            x, _ = self.frame_axis(traj)
        else:
            x = result["frame"].to_numpy()
        y = result["n_hbonds"].to_numpy()
        ax.plot(x, y, linewidth=1.4, color=colour("SERIES"))
        ax.fill_between(x, 0, y, alpha=0.15, color=colour("SERIES"))
        # Integer y ticks (counts).
        ax.set_ylim(bottom=0)

    def default_ylabel(self) -> str | None:
        return "Protein-ligand H-bonds"

    _traj_for_plot: md.Trajectory | None = None

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label


register_analysis(ProteinLigandHBonds.name, ProteinLigandHBonds)
