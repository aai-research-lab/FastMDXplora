"""Bilayer thickness: the distance between the two leaflets' phosphate planes.

The phosphate-to-phosphate thickness D_PP, per frame: the mean height of the
upper leaflet's phosphorus atoms above the bilayer centre, less that of the
lower leaflet's. It moves with the area per lipid, the other way (a bilayer
that loses area thickens, since its volume barely changes), and with the
chains' order.

D_PP is the usual simulation counterpart of the head-to-head thickness D_HH
that X-ray scattering reports, and close to it; it is not the overall
thickness D_B that neutron scattering gives, which includes the head groups'
water. Near a protein the bilayer stretches or compresses to match the
protein's hydrophobic surface, so this mean over every lipid is the bulk value
only when the box is large beside the protein.
"""

from __future__ import annotations

import mdtraj as md
import numpy as np

from fastmdxplora.analysis.bilayer import BilayerSeries, find_bilayer, leaflets
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError

__all__ = ["BilayerThickness"]


class BilayerThickness(BilayerSeries):
    """Phosphate-to-phosphate thickness of the bilayer, per frame.

    The mean height of the upper leaflet's phosphorus atoms above the
    bilayer centre, less that of the lower leaflet's. D_PP is the usual
    simulation counterpart of the head-to-head thickness D_HH that X-ray
    scattering reports, and close to it. Sterols have no phosphate and are
    left out.

    Output
    ------
    ``bilayer_thickness.dat`` -- one column, nm, one row per frame.
    """

    name = "bilayer_thickness"
    description = "Bilayer thickness (phosphate to phosphate)"
    reweightable = (None, "Bilayer thickness (nm)")

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        bilayer = find_bilayer(traj.topology)
        if not bilayer.phosphate.any():
            raise StudyError(
                "No lipid in this bilayer has a phosphate, and the thickness "
                "here is measured between the phosphate planes.",
                code="analysis.system.inapplicable")
        sides = leaflets(traj, bilayer)
        dz = np.where(bilayer.phosphate[None, :], sides.dz, np.nan)
        upper = np.where(sides.upper, dz, np.nan)
        lower = np.where(~sides.upper, dz, np.nan)
        result = np.nanmean(upper, axis=1) - np.nanmean(lower, axis=1)
        self._note_composition(bilayer, sides)
        if len(bilayer.occupants):
            self.findings["near_a_protein"] = (
                "This is the mean over every lipid. Lipids next to a protein "
                "stretch or compress to match its hydrophobic surface, so the "
                "bilayer is not uniformly this thick; the mean moves towards "
                "the bulk value as the box grows.")
        return result

    def default_ylabel(self) -> str | None:
        return "Thickness D_PP (nm)"


register_analysis(BilayerThickness.name, BilayerThickness)
