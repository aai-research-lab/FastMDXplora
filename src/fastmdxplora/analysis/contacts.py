"""Protein-ligand contacts.

Two complementary, commonly-reported views of how the protein engages the
ligand over a trajectory:

1. **Per-frame contact count** — the number of protein residues with any heavy
   atom within ``cutoff`` of the ligand, frame by frame. A quick stability
   signal (a stable pose keeps a roughly constant contact count).
2. **Per-residue contact frequency** — for each protein residue, the fraction
   of frames in which it contacts the ligand. This is the binding-site
   "interaction fingerprint": the residues with high frequency line the pocket.

Contacts are defined at the residue level: a residue is "in contact" in a
frame if any of its atoms is within ``cutoff`` nm of any ligand atom.

Outputs ``pl_contacts.dat`` (per-frame count time series) and, alongside it,
``pl_contacts_per_residue.csv`` (residue, frequency). The figure shows the
per-residue frequency fingerprint (the more informative of the two).
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


class Contacts(Analysis):
    """Protein-ligand contacts: per-frame count and per-residue frequency.

    Parameters
    ----------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied automatically by the
        orchestrator from the setup manifest.
    cutoff : float, default 0.4
        Contact distance cutoff in nm (0.4 nm = 4 Angstrom, a standard
        heavy-atom contact threshold).
    periodic : bool, default True
        Measure distances across the periodic boundary when the trajectory
        carries a unit cell. A solvated trajectory is not always imaged, and
        a molecule split across the boundary then looks far from everything it
        is actually touching -- a bound ligand can report no contacts at all.
        Where there is no unit cell this makes no difference. False measures
        plain distances regardless.
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

    name = "pl_contacts"
    description = "Protein-ligand contacts (count + per-residue frequency)"
    requires_ligand = True
    default_selection = None
    #: This works out its own atoms, so a general selection has nothing to
    #: apply to.
    honours_selection = False

    def __init__(
        self,
        *,
        ligand_resname: str | None = None,
        cutoff: float = 0.4,
        protein_selection: str = "protein",
        periodic: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not ligand_resname:
            raise ValueError(
                "Contacts requires `ligand_resname`; it applies only to "
                "protein-ligand complexes."
            )
        self.ligand_resname = str(ligand_resname)
        self.cutoff = float(cutoff)
        self.protein_selection = str(protein_selection)
        self.periodic: bool = bool(periodic)
        self.options.update(
            periodic=self.periodic,
            ligand_resname=self.ligand_resname,
            cutoff=self.cutoff,
            protein_selection=self.protein_selection,
        )

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        """Compute the per-frame contact count. The per-residue frequency is
        computed alongside and stashed for ``save_data``/``plot``.

        Returns
        -------
        pandas.DataFrame
            Columns ``frame, n_contacts`` (one row per frame).
        """
        ligand_idx = traj.topology.select(f"resname {self.ligand_resname}")
        if len(ligand_idx) == 0:
            raise ValueError(
                f"No atoms matched ligand resname {self.ligand_resname!r}; "
                f"cannot compute protein-ligand contacts."
            )
        protein_idx = traj.topology.select(self.protein_selection)
        if len(protein_idx) == 0:
            raise ValueError(
                f"Protein selection {self.protein_selection!r} matched zero "
                f"atoms; cannot compute protein-ligand contacts."
            )

        # Per-frame: protein atoms within cutoff of any ligand atom.
        # compute_neighbors returns a list (one array per frame) of haystack
        # atom indices near the query set.
        neighbor_lists = md.compute_neighbors(
            traj, self.cutoff, ligand_idx, haystack_indices=protein_idx,
            periodic=self.periodic,
        )

        # Map each protein atom to its residue, then reduce per frame to the
        # set of contacting residues.
        atom_to_res = {
            int(a): traj.topology.atom(int(a)).residue.index
            for a in protein_idx
        }
        n_frames = traj.n_frames
        per_frame_count = np.zeros(n_frames, dtype=int)
        # Accumulate per-residue contact counts across frames.
        res_contact_frames: dict[int, int] = {}
        for f, neighbors in enumerate(neighbor_lists):
            res_in_frame = {atom_to_res[int(a)] for a in neighbors}
            per_frame_count[f] = len(res_in_frame)
            for ridx in res_in_frame:
                res_contact_frames[ridx] = res_contact_frames.get(ridx, 0) + 1

        # Build the per-residue frequency table (only residues that ever
        # contacted the ligand), sorted by frequency descending.
        rows = []
        for ridx, n in res_contact_frames.items():
            res = traj.topology.residue(int(ridx))
            try:
                label = f"{res.name}{res.resSeq}"
            except (AttributeError, TypeError):
                label = str(res)
            rows.append((label, int(res.resSeq) if hasattr(res, "resSeq") else ridx,
                         n / n_frames))
        rows.sort(key=lambda r: r[2], reverse=True)
        self._per_residue = pd.DataFrame(
            [(lbl, freq) for lbl, _seq, freq in rows],
            columns=["residue", "contact_frequency"],
        )

        return pd.DataFrame(
            {"frame": np.arange(n_frames), "n_contacts": per_frame_count}
        )

    def save_data(self, result: pd.DataFrame, path) -> Any:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)
        # Write the per-residue fingerprint alongside the main per-frame file.
        # Named from the analysis rather than written out, so a rename moves
        # this with it instead of leaving a file called after the old name in
        # a directory called after the new one.
        per_res_path = path.parent / f"{self.name}_per_residue.csv"
        self._per_residue.to_csv(per_res_path, index=False)
        return path

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        # Show the per-residue contact-frequency fingerprint (top residues).
        pr = self._per_residue
        if pr.empty:
            ax.text(0.5, 0.5, "No protein-ligand contacts detected",
                    ha="center", va="center", transform=ax.transAxes)
            return
        top = pr.head(20)  # avoid an unreadable axis for large pockets
        y = np.arange(len(top))
        ax.barh(y, top["contact_frequency"].to_numpy(), color=colour("SERIES"))
        ax.set_yticks(y)
        ax.set_yticklabels(top["residue"].tolist(), fontsize=8)
        ax.invert_yaxis()  # highest frequency at top
        ax.set_xlim(0, 1)

    def figure_title(self) -> str:
        """What this figure shows, which is half of what the analysis does.

        ``description`` names the analysis -- a per-frame contact count and a
        per-residue frequency, both of which it computes and writes. The
        figure draws only the second, so inheriting that description put
        "count + per-residue frequency" above a chart containing no count and
        sent a reader looking for one. The count is in ``pl_contacts.dat``.
        """
        if self._user_title is not None:
            return self._user_title
        return "Protein-ligand contact frequency by residue"

    def default_xlabel(self) -> str | None:
        return "Contact frequency (fraction of frames)"

    def default_ylabel(self) -> str | None:
        return "Residue"


register_analysis(Contacts.name, Contacts)
