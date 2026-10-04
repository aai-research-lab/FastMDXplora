"""Secondary structure assignment.

Per-residue secondary structure across the trajectory using DSSP (Kabsch
& Sander algorithm via MDTraj). Produces:

  - A time-series heatmap showing the secondary structure of each residue
    at each frame (residue × frame matrix, colored by DSSP code).
  - The DSSP codes as a CSV (one row per frame, columns are residues).
  - The fractions of helix, strand and coil, per residue over the frames
    and per frame over the residues, with the equilibrated mean of the
    helix and strand fractions in the findings.

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

#: The three classes the fractions are reported in, from DSSP's eight codes
#: as MDTraj's own simplification groups them: helix is alpha, 3-10 and pi
#: (H, G, I), strand is extended strand and isolated bridge (E, B), and coil
#: is every other code (turn T, bend S, and none). The simplified alphabet's
#: H, E and C are the same three classes already.
HELIX_CODES = frozenset({"H", "G", "I"})
STRAND_CODES = frozenset({"E", "B"})
CLASSES = ("helix", "strand", "coil")


def class_fractions(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Helix, strand and coil fractions from a (frames, residues) code array.

    Returns the per-frame fractions, shape (frames, 3): of the residues
    assigned, the share in each class; and the per-residue fractions, shape
    (residues, 3): of the frames analysed, the share in each class. Each row
    of either sums to one.
    """
    codes = np.asarray(codes).astype(str)
    helix = np.isin(codes, list(HELIX_CODES))
    strand = np.isin(codes, list(STRAND_CODES))
    coil = ~(helix | strand)
    stacked = np.stack([helix, strand, coil], axis=-1).astype(np.float64)
    return stacked.mean(axis=1), stacked.mean(axis=0)


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
    ``ss_fractions_per_residue.csv``: each residue's fraction of the frames
    analysed in helix, strand and coil.
    ``ss_fractions.csv``: each frame's fraction of residues in helix, strand
    and coil.
    ``ss.png`` — Heatmap (residue × frame), colored by structure class.

    The three classes are helix (DSSP H, G, I), strand (E, B) and coil
    (every other code), as MDTraj's simplification groups them. The mean
    helix and strand fractions after equilibration, with their standard
    errors, are recorded in the findings under ``helix_fraction`` and
    ``strand_fraction`` by the same statistics as every other series.
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

        qualified = several_chains(traj.topology)  # label() adds 184A where coded
        labels = [label(r, qualified=qualified) for r in residues]

        if len(labels) != codes.shape[1]:
            # Nothing above should leave these out of step; if they are, plain
            # numbering is better than labels naming the wrong residues.
            labels = list(range(codes.shape[1]))

        df = pd.DataFrame(codes, columns=labels)
        df.insert(0, "frame", np.arange(traj.n_frames))
        self._record_fractions(traj, codes, residues)
        return df

    def _record_fractions(self, traj: md.Trajectory, codes: np.ndarray,
                          residues: list) -> None:
        """Keep the class fractions for the files, and put the mean helix
        and strand fractions in the findings."""
        from fastmdxplora.analysis.base import _frame_interval_ns
        from fastmdxplora.analysis.residues import columns
        from fastmdxplora.statistics import mean_record

        per_frame, per_residue = class_fractions(codes)
        self._per_frame = pd.DataFrame(
            {"frame": np.arange(traj.n_frames),
             **{f"{name}_fraction": per_frame[:, k] for k, name in enumerate(CLASSES)}})
        named = (columns(residues, traj.topology) if len(residues) == codes.shape[1]
                 else {"residue": np.arange(codes.shape[1])})
        self._per_residue = pd.DataFrame(
            {**named,
             **{f"{name}_fraction": per_residue[:, k] for k, name in enumerate(CLASSES)}})
        interval = _frame_interval_ns(traj)
        for k, name in enumerate(CLASSES[:2]):
            record = mean_record(per_frame[:, k], frame_interval_ns=interval)
            record["unit"] = ""  # a fraction of the residues, as base records one
            self.findings[f"{name}_fraction"] = record

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

    _per_frame: pd.DataFrame | None = None
    _per_residue: pd.DataFrame | None = None

    def save_data(self, result: pd.DataFrame, path) -> Any:
        """The code matrix, and the class fractions per residue and per frame
        beside it."""
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)
        if self._per_residue is not None:
            self._per_residue.to_csv(
                path.parent / f"{self.name}_fractions_per_residue.csv", index=False)
        if self._per_frame is not None:
            self._per_frame.to_csv(path.parent / f"{self.name}_fractions.csv", index=False)
        return path

    def default_xlabel(self) -> str | None:
        return "Frame"

    def default_ylabel(self) -> str | None:
        return "Residue (index)"


register_analysis(SS.name, SS)
