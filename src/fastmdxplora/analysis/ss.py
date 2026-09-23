"""Secondary structure assignment.

Per-residue secondary structure across the trajectory using DSSP (Kabsch
& Sander algorithm via MDTraj). Produces two outputs:

  - A time-series heatmap showing the secondary structure of each residue
    at each frame (residue × frame matrix, colored by DSSP code).
  - The DSSP codes as a CSV (one row per frame, columns are residues).

DSSP codes used (MDTraj's "simplified" 3-state output by default):
  - ``H`` : helix (3-10, alpha, pi)
  - ``E`` : strand / extended (beta-sheet)
  - ``C`` : coil (everything else)

The classic "ribbon-plot timeline" emerging from this is one of the most
informative single figures in MD trajectory analysis — it shows fold
stability, secondary-structure transitions, and termini fraying at a
glance.

References
----------
Kabsch, W.; Sander, C. *Biopolymers* **1983**, 22, 2577.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


# Map DSSP letters to small integer codes for the heatmap.
# Order is chosen so the colormap reads naturally: coil/turn at 0, then
# strand, then helix — same convention as VMD's "Tube/NewCartoon" rendering.
_DSSP_TO_INT = {
    "C": 0,  # coil
    "T": 0,  # turn (mapped to coil in simplified mode it never appears,
             #       but this is kept for the "full" mode below)
    " ": 0,  # other / unassigned
    "S": 0,  # bend
    "E": 1,  # extended / beta-strand
    "B": 1,  # beta-bridge (mapped to E for visual consistency)
    "H": 2,  # alpha-helix
    "G": 2,  # 3-10 helix
    "I": 2,  # pi-helix
}
_LABELS = {0: "Coil/Other", 1: "β-strand", 2: "Helix"}


class SS(Analysis):
    """Per-residue secondary structure via DSSP.

    Parameters
    ----------
    simplified : bool, default True
        If True, use MDTraj's three-letter simplification (H/E/C). If
        False, use the full eight-letter DSSP alphabet (H/E/B/G/I/T/S/C)
        which is then folded down to three classes for the figure but
        preserved in the saved data.
    **kwargs
        Standard base-class options.

    Output
    ------
    ``ss.dat`` — CSV with the per-frame DSSP code matrix.
    ``ss.png`` — Heatmap (residue × frame), colored by structure class.
    """

    name = "ss"
    description = "Secondary structure (DSSP)"
    default_selection = None

    def __init__(
        self,
        *,
        simplified: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.simplified: bool = bool(simplified)
        self.options.update(simplified=self.simplified)

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        """Run DSSP per frame.

        Returns
        -------
        pandas.DataFrame
            Shape (n_frames, n_residues). Cell values are single-letter
            DSSP codes. Column names are residue resSeq numbers (PDB
            numbering when available, else topology indices).
        """
        # Restrict to the selected atoms (e.g. protein/solute) before DSSP.
        # compute_dssp already emits protein-only columns, but slicing first
        # honors an explicit scope (e.g. protein-only) and avoids handing DSSP
        # a large solvated system.
        atom_idx = self.select_atoms(traj)
        if len(atom_idx) < traj.n_atoms:
            traj = traj.atom_slice(atom_idx)

        # md.compute_dssp returns an (n_frames, n_residues) array of
        # single-letter strings, one column for *every* residue -- not only
        # the protein ones. A residue without backbone atoms gets the code
        # "NA", which is how a ligand, an ion or a water announces itself.
        codes = md.compute_dssp(traj, simplified=self.simplified)

        # Those columns are dropped, and DSSP's own verdict decides which:
        # asking the topology instead would be a second opinion about what
        # counts as protein, and where the two disagreed the labels would
        # stop lining up with the columns they name. Whether a residue has a
        # backbone does not change during a run, so the first frame settles it.
        residues = list(traj.topology.residues)
        if codes.shape[1] == len(residues):
            keep = codes[0] != "NA"
            if not keep.any():
                raise StudyError(
                    "Secondary structure is undefined here: no residue in this "
                    "selection has a protein backbone, so DSSP assigned every "
                    "one of them 'NA'. A nucleic acid, a lone ligand or a "
                    "coarse-grained model has no secondary structure to "
                    "assign. Exclude this analysis, or select the protein."
                , code="analysis.sampling.too_few_frames")
            codes = codes[:, keep]
            residues = [r for r, k in zip(residues, keep) if k]

        # Residue labels: the deposited number, written A:13 where there are
        # several chains -- a column per residue, and on a tetramer the
        # number alone gave four columns one name.
        from fastmdxplora.analysis.residues import label, several_chains

        qualified = several_chains(traj.topology)
        labels = [label(r, qualified=qualified) for r in residues]

        if len(labels) != codes.shape[1]:
            # Nothing above should leave these out of step; if they are, plain
            # numbering is better than labels naming the wrong residues.
            labels = list(range(codes.shape[1]))

        df = pd.DataFrame(codes, columns=labels)
        df.insert(0, "frame", np.arange(traj.n_frames))
        return df

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        # Drop the frame column for the heatmap
        codes = result.drop(columns="frame").to_numpy()
        residue_labels = [c for c in result.columns if c != "frame"]

        # Map letter codes to integer classes
        int_grid = np.zeros(codes.shape, dtype=int)
        for code, val in _DSSP_TO_INT.items():
            int_grid[codes == code] = val

        # Build a discrete colormap so the legend reads cleanly
        from matplotlib.colors import ListedColormap

        cmap = ListedColormap(["#E5E5E5", "#F2B441", "#4E79A7"])  # coil, strand, helix
        im = ax.imshow(
            int_grid.T,  # residues on Y, frames on X
            aspect="auto",
            origin="lower",
            cmap=cmap,
            vmin=-0.5,
            vmax=2.5,
            interpolation="nearest",
            extent=(0, len(result), 0, len(residue_labels)),
        )
        # Discrete colorbar with labels
        cbar = ax.figure.colorbar(im, ax=ax, ticks=[0, 1, 2], shrink=0.7)
        cbar.set_ticklabels([_LABELS[0], _LABELS[1], _LABELS[2]])

    def save_data(self, result: pd.DataFrame, path) -> Any:
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)
        return path

    def default_xlabel(self) -> str | None:
        return "Frame"

    def default_ylabel(self) -> str | None:
        return "Residue (index)"


register_analysis(SS.name, SS)
