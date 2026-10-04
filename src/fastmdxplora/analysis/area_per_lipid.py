"""Area per lipid: how much of the bilayer's plane each lipid has.

The area a lipid occupies in the plane of the bilayer, per frame: the box's
area in xy, less the protein's cross section in the hydrophobic core, shared
among the lipids of one leaflet. It is the first number a membrane
simulation is checked against, because it is measured by experiment and moves
with everything that can be wrong: the force field, the temperature, the
barostat, and a bilayer not yet equilibrated.

Read it as a series before as a mean. A bilayer packed around a protein
relaxes over nanoseconds, and the early frames show it; below the lipid's
main transition it is too small and falls further as the bilayer orders.
Compare the equilibrated value with experiment at the same temperature (Kucerka,
Nieh and Katsaras, Biochim Biophys Acta 1808, 2761 (2011) for the
phosphatidylcholines).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.bilayer import (
    CROSS_SECTION_PLANES_NM,
    LIPID_PROBE_NM,
    BilayerSeries,
    _area_xy,
    _radii,
    _wrapped,
    box_vectors,
    cross_section,
    find_bilayer,
    leaflets,
)
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.lipids import is_sterol, lipid_count
from fastmdxplora.refusals import StudyError

__all__ = ["AreaPerLipid"]


class AreaPerLipid(BilayerSeries):
    """Area per lipid in the plane of the bilayer, per frame.

    The box's area in xy, less the protein's cross section in the
    hydrophobic core, shared among the lipids of one leaflet::

        APL = (A_box - A_protein) / (N_lipids / 2)

    ``A_protein`` is the protein's cross section in planes at
    :data:`CROSS_SECTION_PLANES_NM` about the bilayer centre, averaged over
    the planes: the van der Waals discs of every atom that is not lipid,
    water or an ion, with the crevices a methylene group
    (:data:`LIPID_PROBE_NM`) cannot enter and the gaps between packed
    helices counted as protein. It is zero for a bilayer with nothing in it,
    which is then the plain box area per lipid. A sterol counts as a lipid.

    The correction is a convention, as every protein correction to an area
    per lipid is: the lipids next to a protein do not pack as those in bulk
    do. It is small for one or two helices in a box of a hundred lipids and
    large for a big bundle in a small box, and the findings give the
    protein's share of the box so the reader can tell which this is.

    Each leaflet's own area per lipid is given beside it::

        APL_upper = (A_box - A_protein) / N_upper
        APL_lower = (A_box - A_protein) / N_lower

    with ``N_upper`` and ``N_lower`` counted every frame. In a symmetric
    bilayer both equal APL, which is ``2 A / (N_upper + N_lower)``; in an
    asymmetric one (a different number of lipids, or different lipids, in
    each leaflet) the two leaflets share one area, so the leaflet with fewer
    lipids has more area per lipid, and APL is neither leaflet's value.

    Output
    ------
    ``area_per_lipid.dat`` -- three columns, nm^2, one row per frame: the
    upper leaflet's area per lipid, the lower leaflet's, and APL, last, as
    the series every reader of the file takes.
    """

    name = "area_per_lipid"
    description = "Area per lipid"
    reweightable = (None, "Area per lipid (nm2)")

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        bilayer = find_bilayer(traj.topology)
        if not len(bilayer.heads):
            raise StudyError(
                f"None of the {lipid_count(r.name for r in traj.topology.residues)} "
                "lipids here has a head atom: no phosphorus, and no sterol "
                "oxygen. The area per lipid divides the box among the lipids "
                "whose heads are found, so with none there is nothing to "
                "divide it among. A coarse-grained model, whose beads carry no "
                "element, is not read by this analysis.",
                code="analysis.system.inapplicable")
        sides = leaflets(traj, bilayer)
        vectors = box_vectors(traj)
        area = _area_xy(vectors)
        protein = np.zeros(traj.n_frames)
        if len(bilayer.occupants):
            radii = _radii(traj.topology, bilayer.occupants)
            xyz = traj.xyz[:, bilayer.occupants].astype(np.float64)
            for frame in range(traj.n_frames):
                cell = vectors[frame, :2, :2]
                height = vectors[frame, 2, 2]
                sections = []
                for plane in CROSS_SECTION_PLANES_NM:
                    dz = _wrapped(xyz[frame, :, 2] - (sides.centre[frame] + plane),
                                  height)
                    cut = np.abs(dz) < radii
                    sections.append(cross_section(
                        xyz[frame, cut, :2], np.sqrt(radii[cut] ** 2 - dz[cut] ** 2),
                        cell, probe=LIPID_PROBE_NM))
                protein[frame] = float(np.mean(sections))
        per_leaflet = len(bilayer.heads) / 2.0
        result = (area - protein) / per_leaflet
        upper = sides.upper.sum(axis=1)
        lower = (~sides.upper).sum(axis=1)
        with np.errstate(divide="ignore"):
            self._leaflets = np.column_stack([
                np.where(upper > 0, (area - protein) / np.maximum(upper, 1), np.nan),
                np.where(lower > 0, (area - protein) / np.maximum(lower, 1), np.nan)])

        self._note_composition(traj, bilayer, sides)
        self.findings["area"] = {
            "box_area_nm2_mean": float(area.mean()),
            "protein_cross_section_nm2_mean": float(protein.mean()),
            "cross_section_planes_nm": list(CROSS_SECTION_PLANES_NM),
            "method": (
                "(box area in xy - protein cross section in the hydrophobic "
                "core) / (lipids / 2); the cross section is the area inside "
                "the outline a methylene probe traces round the protein's van "
                "der Waals discs, averaged over planes about the bilayer "
                "centre"),
            "probe_nm": LIPID_PROBE_NM,
        }
        if protein.mean() > 0:
            share = float(protein.mean() / area.mean())
            self.findings["protein_share"] = (
                f"The protein takes {share:.0%} of the box's area in the "
                "bilayer core. The area per lipid is corrected for it, and the "
                "correction is an estimate: lipids next to a protein pack "
                "differently from those in bulk, and where the boundary between "
                "them is drawn is a convention. The larger the protein's share, "
                "the more the value depends on it.")
        upper_mean, lower_mean = (float(v) for v in np.nanmean(self._leaflets, axis=0))
        self.findings["per_leaflet"] = {
            "upper_nm2_mean": upper_mean,
            "lower_nm2_mean": lower_mean,
            "method": ("(box area in xy - protein cross section) / lipids in "
                       "that leaflet, each frame"),
        }
        if np.any(upper != lower):
            self.findings["asymmetric"] = (
                f"The leaflets hold different numbers of lipids "
                f"({int(upper[0])} upper and {int(lower[0])} lower in the first "
                f"frame), so each has its own area per lipid: {upper_mean:.3f} "
                f"nm2 in the upper leaflet and {lower_mean:.3f} nm2 in the lower, "
                f"against {float(result.mean()):.3f} nm2 for the bilayer as a "
                "whole. The leaflets share one area, so the one with fewer "
                "lipids is the more stretched.")
        if any(is_sterol(name) for name in bilayer.composition):
            self.findings["sterols"] = (
                "Sterols are counted as lipids, so this is the mean area per "
                "molecule of the mixture, which is smaller than the area per "
                "phospholipid.")
        return result

    #: Each leaflet's area per lipid, per frame: (frames, 2), upper then lower.
    _leaflets: np.ndarray | None = None

    def save_data(self, result: np.ndarray, path: Path) -> Path:
        """The upper and lower leaflets' areas per lipid, then APL, per frame."""
        if self._leaflets is None or len(self._leaflets) != len(result):
            return super().save_data(result, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["upper_leaflet_nm2", "lower_leaflet_nm2", "area_per_lipid_nm2"]
        np.savetxt(path, np.column_stack([self._leaflets, result]), fmt="%.8e",
                   header=(f"{self.name}: whitespace-delimited, columns "
                           f"{' '.join(columns)}. Read with np.loadtxt(path)."))
        self._data_format = {
            "layout": "whitespace-delimited, no header",
            "read_with": "np.loadtxt(path)",
            "columns": columns,
        }
        return path

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        super().plot(result, ax)
        leaflets = self._leaflets
        if leaflets is None or len(leaflets) != len(result) or np.allclose(
                leaflets[:, 0], leaflets[:, 1], equal_nan=True):
            return
        x = ax.lines[-1].get_xdata()
        ax.plot(x, leaflets[:, 0], linewidth=0.9, linestyle="--", label="upper leaflet")
        ax.plot(x, leaflets[:, 1], linewidth=0.9, linestyle=":", label="lower leaflet")
        ax.lines[0].set_label("bilayer")
        ax.legend(loc="best", fontsize=7.5)

    def default_ylabel(self) -> str | None:
        return "Area per lipid (nm2)"


register_analysis(AreaPerLipid.name, AreaPerLipid)
