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
import pandas as pd

from fastmdxplora.analysis.bilayer import (
    PROFILE_BIN_NM,
    PROFILE_COMPONENTS,
    BilayerSeries,
    density_profile,
    find_bilayer,
    leaflets,
)
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import (
    closes_what_it_opens,
    drawn_in,
    new_figure,
    save_figure,
    sized_for,
)
from fastmdxplora.refusals import StudyError

__all__ = ["BilayerThickness"]


class BilayerThickness(BilayerSeries):
    """Phosphate-to-phosphate thickness of the bilayer, per frame.

    The mean height of the upper leaflet's phosphorus atoms above the
    bilayer centre, less that of the lower leaflet's. D_PP is the usual
    simulation counterpart of the head-to-head thickness D_HH that X-ray
    scattering reports, and close to it. Sterols have no phosphate and are
    left out.

    Beside it, the mass density of each part of the system along the
    normal, centred on the bilayer centre every frame and averaged over the
    frames analysed (:func:`~fastmdxplora.analysis.bilayer.density_profile`):
    the lipids' heads and their hydrocarbon chains, water, protein (anything
    else of more than one atom) and ions. The heads' two peaks are the
    phosphate planes D_PP is taken between, and where water's density falls
    to half its bulk value on each side is the usual definition of the
    bilayer's water boundary.

    Output
    ------
    ``bilayer_thickness.dat`` -- one column, nm, one row per frame.
    ``density_profile.dat`` -- comma-separated, one row per 0.1 nm slab:
    ``z_nm`` (the slab's centre, above the bilayer centre), ``width_nm``
    (its part inside the box, averaged over frames), then the density of
    each part in g/cm^3.
    ``density_profile.png`` -- the profiles against z.
    """

    name = "bilayer_thickness"
    description = "Bilayer thickness (phosphate to phosphate)"
    reweightable = (None, "Bilayer thickness (nm)")

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        bilayer = find_bilayer(traj.topology)
        if not bilayer.phosphate.any():
            raise StudyError(
                "No lipid in this bilayer has a phosphate, and the thickness "
                "here is the distance between the phosphate planes.",
                code="analysis.system.inapplicable")
        sides = leaflets(traj, bilayer)
        dz = np.where(bilayer.phosphate[None, :], sides.dz, np.nan)
        upper = np.where(sides.upper, dz, np.nan)
        lower = np.where(~sides.upper, dz, np.nan)
        result = np.nanmean(upper, axis=1) - np.nanmean(lower, axis=1)
        self._note_composition(traj, bilayer, sides)
        self._profile = density_profile(traj, sides)
        if len(bilayer.occupants):
            self.findings["near_a_protein"] = (
                "This is the mean over every lipid. Lipids next to a protein "
                "stretch or compress to match its hydrophobic surface, so the "
                "bilayer is not uniformly this thick; the mean moves towards "
                "the bulk value as the box grows.")
        return result

    def default_ylabel(self) -> str | None:
        return "Thickness D_PP (nm)"

    #: The mass density profile of the last trajectory computed.
    _profile: pd.DataFrame | None = None

    def run(self, traj: md.Trajectory):
        """The thickness, and the density profile written beside it."""
        self._profile = None
        result = super().run(traj)
        if result.status != "ok" or self._profile is None:
            return result
        data = self.output_dir / "density_profile.dat"
        self._profile.to_csv(data, index=False)
        with closes_what_it_opens(), sized_for(self.figure_width), drawn_in(
                "greyscale" if self.figure_colours == "greyscale" else "colour"):
            fig, ax = new_figure(title="Mass density along the normal",
                                 figsize=self._user_figsize,
                                 xlabel="Height above the bilayer centre (nm)",
                                 ylabel="Density (g/cm3)")
            # A slab mostly outside the box holds a few atoms in a sliver of
            # volume; it is in the data, with its width, and not plotted.
            shown = self._profile[self._profile["width_nm"] >= PROFILE_BIN_NM / 2]
            for name in PROFILE_COMPONENTS:
                values = shown[f"{name}_g_cm3"]
                if values.abs().max() > 0:
                    ax.plot(shown["z_nm"], values, linewidth=1.2,
                            label=name.replace("_", " "))
            ax.legend(loc="best", fontsize=7.5)
            figure = save_figure(fig, self.output_dir / "density_profile.png")
        written = [data, figure]
        if figure.with_suffix(".svg").is_file():
            written.append(figure.with_suffix(".svg"))
        result.artifacts.extend(written)
        self.findings["density_profile"] = {
            "data": data.name,
            "bin_nm": PROFILE_BIN_NM,
            "components": list(PROFILE_COMPONENTS),
            "frames": int(traj.n_frames),
            "centred_on": "the bilayer centre, every frame",
        }
        self._write_options_manifest()
        return result


register_analysis(BilayerThickness.name, BilayerThickness)
