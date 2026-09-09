"""Backbone dihedrals and the Ramachandran plot.

Computes the backbone phi (C-N-Cα-C), psi (N-Cα-C-N) and omega
(Cα-C-N-Cα) dihedral angles for every protein residue across the
trajectory, and produces a Ramachandran plot — the joint distribution of
phi/psi pairs coloured by frequency.

Omega is the peptide bond itself, near 180 degrees in almost every
residue; the exceptions are the finding, a cis bond most often before a
proline. Which angles are measured is a setting.

Output ``dihedrals.dat`` is a CSV with one row per (frame, residue)
combination plus columns for phi and psi (degrees). Output figure is
the 2-D Ramachandran scatter/density plot.

References
----------
The phi/psi assignment follows the IUPAC convention. MDTraj's
:func:`mdtraj.compute_phi` / :func:`mdtraj.compute_psi` are used; both
return angles in radians, which we convert to degrees for the standard
Ramachandran display range of (-180°, 180°).
"""

from __future__ import annotations

from typing import Any, Sequence

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis


#: Which backbone torsions can be measured. Named here so a form can offer
#: them: the same constant the analysis validates against.
VALID_ANGLES = ("phi", "psi", "omega")


class Dihedrals(Analysis):
    """Backbone phi/psi dihedrals and the Ramachandran plot.

    Parameters
    ----------
    density : bool, default True
        If True, render the Ramachandran plot as a 2-D histogram (heatmap)
        showing point density. If False, render as a scatter plot. Density
        is more readable for long trajectories with many points; scatter
        is better for short trajectories or when you want to see each
        sample.
    angles : sequence of {"phi", "psi", "omega"}, default all three
        Which backbone torsions to measure. Phi and psi are the Ramachandran
        pair. Omega is the peptide bond itself, close to 180 degrees in almost
        every residue -- and the exceptions are the finding: a cis peptide
        bond near zero, most often before a proline, and the departures from
        planarity that a strained fold produces.
    bins : int, default 72
        Number of bins along each axis for the density plot. The
        Ramachandran range is 360°, so the default 72 bins → 5° resolution.
    **kwargs
        Standard base-class options. ``selection`` is ignored here because
        the dihedrals are inherently a backbone property; MDTraj's
        functions handle the residue iteration internally.

    Output
    ------
    ``dihedrals.dat`` — CSV with columns ``frame, residue, phi_deg, psi_deg``.
    ``dihedrals.png`` — Ramachandran plot (density heatmap by default).
    """

    name = "dihedrals"
    description = "Backbone dihedrals (Ramachandran)"
    # No default_selection — MDTraj's phi/psi functions identify backbone
    # atoms by name regardless of the user's atom subset.
    default_selection = None
    #: This works out its own atoms, so a general selection has nothing to
    #: apply to.
    honours_selection = False

    def __init__(
        self,
        *,
        density: bool = True,
        angles: Sequence[str] = VALID_ANGLES,
        bins: int = 72,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.density: bool = bool(density)
        chosen = tuple(str(a).lower() for a in angles)
        unknown = [a for a in chosen if a not in VALID_ANGLES]
        if unknown:
            raise ValueError(
                f"Unknown dihedral(s) {unknown}. Valid: {VALID_ANGLES}"
            )
        self.angles: tuple[str, ...] = chosen
        self.bins: int = int(bins)
        self.options.update(density=self.density, bins=self.bins)

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        """Compute backbone phi/psi for every (frame, residue) pair.

        Returns
        -------
        pandas.DataFrame
            Long format with columns: ``frame, residue, phi_deg, psi_deg``.
            Residues where either phi or psi cannot be computed (first/last
            residues, chain breaks) are dropped from the table.
        """
        phi_idx, phi_rad = md.compute_phi(traj)
        psi_idx, psi_rad = md.compute_psi(traj)
        omega_idx, omega_rad = (
            md.compute_omega(traj) if "omega" in self.angles
            else (np.zeros((0, 4), dtype=int), np.zeros((traj.n_frames, 0)))
        )

        if phi_rad.size == 0 or psi_rad.size == 0:
            raise ValueError(
                "No backbone dihedrals could be computed. This usually "
                "means the trajectory does not contain a protein, or only "
                "contains residues without complete N-Cα-C backbones."
            )

        # Match phi and psi residues by the central Cα atom (index [2] in
        # MDTraj's 4-atom phi tuple, index [1] in psi). For ordinary proteins
        # the phi and psi residue lists differ only at the first / last
        # residue (phi missing for first; psi missing for last).
        phi_ca = phi_idx[:, 2]
        psi_ca = psi_idx[:, 1]
        common_ca, phi_pos, psi_pos = np.intersect1d(
            phi_ca, psi_ca, return_indices=True
        )

        phi_deg = np.rad2deg(phi_rad[:, phi_pos])
        psi_deg = np.rad2deg(psi_rad[:, psi_pos])

        # Omega is the torsion of the peptide bond between residue i-1 and i,
        # so MDTraj's quartet is CA(i-1), C(i-1), N(i), CA(i): the residue it
        # belongs to is the one at index 3, where phi's is at index 2. Joining
        # on the wrong one would shift every value by a residue, which is the
        # sort of error that produces a plausible plot of the wrong thing.
        omega_deg = None
        if omega_idx.size:
            omega_ca = omega_idx[:, 3]
            lookup = {int(ca): position for position, ca in enumerate(omega_ca)}
            positions = [lookup.get(int(ca)) for ca in common_ca]
            omega_deg = np.full((traj.n_frames, len(common_ca)), np.nan)
            for column, position in enumerate(positions):
                if position is not None:
                    omega_deg[:, column] = np.rad2deg(omega_rad[:, position])

        # Build the long-form DataFrame
        n_frames = traj.n_frames
        n_res = len(common_ca)
        frames = np.repeat(np.arange(n_frames), n_res)
        residues_topo = [
            traj.topology.atom(int(ca)).residue for ca in common_ca
        ]
        try:
            residue_labels = np.array(
                [int(r.resSeq) for r in residues_topo]
            )
        except (AttributeError, TypeError):
            residue_labels = np.array([r.index for r in residues_topo])
        residue_col = np.tile(residue_labels, n_frames)

        columns = {"frame": frames, "residue": residue_col}
        if "phi" in self.angles:
            columns["phi_deg"] = phi_deg.flatten()
        if "psi" in self.angles:
            columns["psi_deg"] = psi_deg.flatten()
        if omega_deg is not None:
            columns["omega_deg"] = omega_deg.flatten()
        return pd.DataFrame(columns)

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        phi = result["phi_deg"].to_numpy()
        psi = result["psi_deg"].to_numpy()

        if self.density:
            # 2-D histogram with logarithmic color scale to handle the
            # very different population densities in Ramachandran space.
            counts, xedges, yedges = np.histogram2d(
                phi, psi, bins=self.bins, range=[[-180, 180], [-180, 180]]
            )
            # log1p for visual contrast; counts is sparse for short runs.
            im = ax.imshow(
                np.log1p(counts.T),
                extent=(-180, 180, -180, 180),
                origin="lower",
                aspect="equal",
                cmap="viridis",
                interpolation="nearest",
            )
            ax.figure.colorbar(im, ax=ax, label="log(1 + count)", shrink=0.85)
        else:
            ax.scatter(phi, psi, s=2, alpha=0.4, edgecolor="none")
            ax.set_aspect("equal")

        # Standard Ramachandran reference lines through the origin
        ax.axhline(0, color=colour("GUIDE"), linewidth=0.5)
        ax.axvline(0, color=colour("GUIDE"), linewidth=0.5)
        ax.set_xlim(-180, 180)
        ax.set_ylim(-180, 180)
        ax.set_xticks([-180, -90, 0, 90, 180])
        ax.set_yticks([-180, -90, 0, 90, 180])

    def save_data(self, result: pd.DataFrame, path) -> Any:
        """CSV with frame, residue, phi_deg, psi_deg columns."""
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use the .dat extension but CSV format.
        result.to_csv(path, index=False)
        return path

    def default_xlabel(self) -> str | None:
        return "φ (degrees)"

    def default_ylabel(self) -> str | None:
        return "ψ (degrees)"


register_analysis(Dihedrals.name, Dihedrals)
