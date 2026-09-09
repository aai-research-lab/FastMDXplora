"""Waters that stay put, and the difference between a site and a molecule.

Most water in a simulation is bulk: it arrives, it leaves, it means nothing.
But some positions are occupied throughout — a water wedged between a ligand
and a backbone carbonyl, bridging a hydrogen bond that neither could make
alone. Those waters are part of the binding site, and a medicinal chemist
displacing one pays for it in entropy or gains from it in affinity.

Finding them means clustering water oxygen positions across the trajectory and
asking which clusters are occupied often. That much is standard.

**What is not standard, and matters, is the distinction this analysis makes.**
A cluster occupied in ninety per cent of frames can be either of two things:

- **one water molecule that stayed** — bound, with a residence time, and
  displacing it means displacing that molecule
- **a position that many waters passed through** — a structural site, where
  the geometry favours a water but no particular water is held

Those are different findings with different consequences, and a cluster
occupancy alone cannot tell them apart. Both are reported: how often the site
is occupied, and by how many distinct molecules.

**Only waters near the solute are considered.** Bulk water clusters
beautifully and means nothing — with enough frames, every position in the box
has been occupied. The cutoff is what makes the result about the protein
rather than about the density of water.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.orchestrator import register_analysis

__all__ = ["WaterSites"]

#: Residue names for water across the force fields FastMDXplora uses.
WATER_RESIDUES = ("HOH", "WAT", "TIP", "TIP3", "SOL", "H2O")


class WaterSites(Analysis):
    """Positions where a water sits for much of the trajectory.

    Parameters
    ----------
    site_selection : str, default "auto"
        What the waters must be near. ``"auto"`` uses the ligand where there
        is one and the protein otherwise, because a binding-site water is the
        usual question; anything else is an atom selection.
    ligand_resname : str, optional
        Which residue is the ligand. Supplied by the run when there is one;
        it is a fact about the structure rather than a setting.
    cutoff_nm : float, default 0.5
        How near a water must come to be considered at all, in nm. Waters
        further out are bulk, and bulk clusters beautifully while meaning
        nothing.
    eps_nm : float, default 0.12
        How close two observed positions must be to belong to the same site,
        in nm. Roughly the width of a water's thermal motion within one site.
    minimum_occupancy : float, default 0.5
        The fraction of frames a site must be occupied to be reported. Below
        this it is passing traffic rather than a site.
    minimum_samples : int, default 5
        How many observed positions a cluster needs before it counts as one.
        Guards against a handful of coincidental positions becoming a "site".
    maximum_radius_nm : float, default 0.25
        How far a cluster may spread and still be a site, in nm. Clustering
        links neighbours through neighbours, so without this the whole first
        hydration shell of a protein chains into one "site" occupied in every
        frame.
    """

    #: The shape of the result, whether or not anything was found. An empty
    #: frame with no columns raises when sorted, which is how "no site met
    #: the threshold" became a crash rather than a finding.
    COLUMNS = ("site", "x", "y", "z", "occupancy", "n_observations",
               "n_distinct_waters", "longest_stay_frames", "interpretation")

    name = "water_sites"
    description = "Water positions occupied through the trajectory"
    default_selection = None
    #: It works out its own atoms from the site selection, so a general
    #: selection has nothing to apply to.
    honours_selection = False
    #: Runs by default only where there is water. An implicit-solvent run, or
    #: a trajectory stripped of solvent to save space, has none -- and a
    #: refusal there is correct but should not fail the phase.
    requires_water = True

    #: What to do about it, when a run has none. Left out of a planned set
    #: rather than failed, but a study that asked for water sites and got
    #: no directory deserves the reason: since `save_selection` defaults to
    #: leaving the solvent out of the trajectory, the commonest cause is
    #: not that the run had no water but that it was not saved.
    absent_because = (
        "this trajectory holds no water. `simulation.save_selection` "
        "defaults to `not water`, which is nine tenths of the file and "
        "nothing this analysis can work without: set it to `all` for a "
        "study whose subject is the solvent. An implicit-solvent run has "
        "no water to save."
    )

    def __init__(
        self,
        *,
        site_selection: str = "auto",
        ligand_resname: str | None = None,
        cutoff_nm: float = 0.5,
        eps_nm: float = 0.12,
        minimum_occupancy: float = 0.5,
        minimum_samples: int = 5,
        maximum_radius_nm: float = 0.25,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.site_selection = site_selection
        self.ligand_resname = ligand_resname
        self.cutoff_nm = float(cutoff_nm)
        self.eps_nm = float(eps_nm)
        self.minimum_occupancy = float(minimum_occupancy)
        self.minimum_samples = int(minimum_samples)
        self.maximum_radius_nm = float(maximum_radius_nm)
        self.options.update(
            site_selection=self.site_selection,
            ligand_resname=self.ligand_resname,
            cutoff_nm=self.cutoff_nm,
            eps_nm=self.eps_nm,
            minimum_occupancy=self.minimum_occupancy,
            minimum_samples=self.minimum_samples,
            maximum_radius_nm=self.maximum_radius_nm,
        )

    def _resolve_site(self, traj: Any) -> tuple[np.ndarray, str]:
        """What the waters have to be near, and how that was decided."""
        if self.site_selection and self.site_selection != "auto":
            expression = str(self.site_selection)
        elif self.ligand_resname:
            expression = f"resname {self.ligand_resname}"
        else:
            expression = "protein"

        atoms = traj.topology.select(expression)
        if len(atoms) == 0:
            raise ValueError(
                f"The site selection {expression!r} matched no atoms, so "
                "there is nothing for a water to be near."
            )
        return atoms, expression

    def compute(self, traj: Any) -> pd.DataFrame:
        import mdtraj as md
        from sklearn.cluster import DBSCAN

        water_oxygens = [
            atom.index for atom in traj.topology.atoms
            if atom.residue.name.upper() in WATER_RESIDUES
            and atom.element is not None and atom.element.symbol == "O"
        ]
        if not water_oxygens:
            raise ValueError(
                "No water was found in the trajectory. This analysis needs an "
                "explicitly solvated system; a run stripped of water, or one "
                "using an implicit solvent, has no water sites to find."
            )

        site_atoms, site_expression = self._resolve_site(traj)
        self.findings["site"] = {
            "selection": site_expression,
            # "auto" is a value, not an absence, so it has to be excluded
            # explicitly -- a truthiness check called it "given" and the
            # record said the user had chosen what the software had.
            "chosen_because": (
                "given" if self.site_selection not in (None, "", "auto") else
                "auto: the ligand" if self.ligand_resname else
                "auto: no ligand, so the whole protein"
            ),
            "n_atoms": int(len(site_atoms)),
        }

        # Every water observation near the site, with which frame and which
        # molecule it came from -- both are needed to tell a site from a
        # molecule afterwards.
        positions: list[np.ndarray] = []
        frames: list[int] = []
        residues: list[int] = []

        pairs = np.array(
            [(w, s) for w in water_oxygens for s in site_atoms], dtype=int)

        for frame_index in range(traj.n_frames):
            frame = traj[frame_index]
            distances = md.compute_distances(frame, pairs, periodic=True)[0]
            nearest = distances.reshape(len(water_oxygens), len(site_atoms)).min(axis=1)
            near = np.where(nearest <= self.cutoff_nm)[0]
            reshaped = distances.reshape(len(water_oxygens), len(site_atoms))
            for index in near:
                atom = water_oxygens[int(index)]
                # The position *where the water was found*, not where it is
                # stored. It was accepted on a minimum-image distance, so it
                # may be an image away from the site; recording the stored
                # coordinate then handed DBSCAN points a box length apart,
                # split one physical site into two at half occupancy each,
                # and put one of them outside the box. Below the default
                # minimum_occupancy an uneven split loses the smaller half
                # and halves the survivor -- a persistent site can vanish.
                closest = site_atoms[int(np.argmin(reshaped[int(index)]))]
                offset = md.compute_displacements(
                    frame, np.array([[closest, atom]]), periodic=True)[0, 0]
                positions.append(frame.xyz[0][closest] + offset)
                frames.append(frame_index)
                residues.append(traj.topology.atom(atom).residue.index)

        if not positions:
            self.findings["not_found"] = (
                f"No water came within {self.cutoff_nm:g} nm of "
                f"{site_expression!r} in any frame."
            )
            return pd.DataFrame(columns=list(self.COLUMNS))

        coordinates = np.asarray(positions)
        labels = DBSCAN(
            eps=self.eps_nm, min_samples=self.minimum_samples
        ).fit_predict(coordinates)

        frames = np.asarray(frames)
        residues = np.asarray(residues)

        rows = []
        spread_out = 0
        for label in sorted(set(labels) - {-1}):
            member = labels == label
            member_frames = frames[member]
            member_residues = residues[member]

            occupancy = len(set(member_frames.tolist())) / traj.n_frames
            if occupancy < self.minimum_occupancy:
                continue

            # A site has to be compact. DBSCAN links neighbours through
            # neighbours, so on a real protein the whole first hydration
            # shell chains into a single cluster -- on ubiquitin that was
            # forty-eight thousand positions and eight hundred waters
            # reported as one site occupied in every frame. A cluster
            # spanning more than a couple of water diameters is a surface,
            # not a position.
            member_positions = coordinates[member]
            centre = member_positions.mean(axis=0)
            radius = float(np.linalg.norm(member_positions - centre, axis=1).max())
            if radius > self.maximum_radius_nm:
                spread_out += 1
                continue

            distinct = len(set(member_residues.tolist()))

            # The longest run of consecutive frames held by one molecule.
            longest = 0
            for water in set(member_residues.tolist()):
                held = np.sort(member_frames[member_residues == water])
                run = best = 1
                for previous, current in zip(held, held[1:]):
                    run = run + 1 if current == previous + 1 else 1
                    best = max(best, run)
                longest = max(longest, best)

            # The distinction that matters. A site held by one molecule
            # throughout is a bound water; one that many molecules pass
            # through is a position the geometry favours.
            #
            # "Mostly one molecule" needs that molecule to account for most
            # of the observations, not merely to have had one long run: with
            # eight hundred waters in a cluster, one of them having stayed a
            # hundred frames says nothing about the rest.
            counts = np.bincount(member_residues)
            dominant_share = counts.max() / counts.sum()

            if distinct == 1:
                interpretation = "one molecule, bound"
            elif dominant_share >= 0.5 and longest >= 0.5 * traj.n_frames:
                interpretation = "mostly one molecule, exchanging occasionally"
            else:
                interpretation = f"a favoured position, {distinct} waters passed through"
            rows.append({
                "site": int(label),
                "x": float(centre[0]),
                "y": float(centre[1]),
                "z": float(centre[2]),
                "occupancy": float(occupancy),
                "n_observations": int(member.sum()),
                "n_distinct_waters": int(distinct),
                "longest_stay_frames": int(longest),
                "interpretation": interpretation,
            })

        if not rows:
            # Why nothing was found matters. "Nothing persisted" and "clusters
            # were found and rejected as surfaces rather than sites" are
            # different answers, and the second one says the settings may be
            # wrong for this system rather than that the system has no sites.
            if spread_out:
                self.findings["not_found"] = (
                    f"{spread_out} cluster(s) were occupied often enough but "
                    f"spread further than {self.maximum_radius_nm:g} nm, so "
                    "they are surface hydration rather than sites. Narrowing "
                    "`site_selection` to a pocket, or lowering `cutoff_nm`, "
                    "will separate positions that a whole-protein selection "
                    "chains together."
                )
            else:
                self.findings["not_found"] = (
                    f"Water came near {site_expression!r}, but no position "
                    f"was held in at least {self.minimum_occupancy:.0%} of "
                    "frames. Either none persists, or the run is too short "
                    "for one to look persistent."
                )
            self.findings["rejected_as_too_spread_out"] = spread_out
            return pd.DataFrame(columns=list(self.COLUMNS))

        result = pd.DataFrame(rows).sort_values(
            "occupancy", ascending=False).reset_index(drop=True)

        # How long the run was, against how long water stays. A water on a
        # protein surface exchanges on ten to a hundred picoseconds, so a
        # trajectory shorter than that cannot tell a bound water from one
        # that had not left yet -- and every site in it will look fully
        # occupied. Reporting "one molecule, bound" from ten picoseconds is a
        # claim about residence the run cannot support.
        #
        # The clock has to be real for that comparison to mean anything. A
        # DCD read through MDTraj reports one picosecond per frame, so this
        # was measuring the frame count: a 100 ns run read as 1,999 ps and
        # cleared the threshold below for the wrong reason, while a genuinely
        # short run with many frames would have cleared it too. Where no
        # interval is recorded `loading` marks the clock NaN, and the honest
        # answer is that the run's length is unknown rather than a number.
        duration_ps = None
        time = getattr(traj, "time", None)
        if time is not None and traj.n_frames > 1 and np.all(np.isfinite(time)):
            duration_ps = float(time[-1] - time[0])
        self.findings["duration_ps"] = duration_ps
        if duration_ps is None and traj.n_frames > 1:
            self.findings["duration_unknown"] = (
                "How much simulated time this trajectory covers was not "
                "recorded, so whether it is long enough to distinguish a held "
                "water from one that had not yet left cannot be checked. The "
                "occupancies below stand; their interpretation as residence "
                "does not."
            )
        if duration_ps is not None and duration_ps < 1000.0:
            self.findings["too_short_for_residence"] = (
                f"This trajectory is {duration_ps:.0f} ps. Water on a protein "
                "surface exchanges on ten to a hundred picoseconds, so a site "
                "occupied throughout a run this short shows that no water "
                "left, not that one is held. Distinguishing a structural "
                "water from a slow one needs nanoseconds."
            )

        self.findings["n_sites"] = len(result)
        if spread_out:
            self.findings["rejected_as_too_spread_out"] = spread_out
        if len(result):
            self.findings["most_occupied"] = {
                "occupancy": float(result.iloc[0]["occupancy"]),
                "interpretation": result.iloc[0]["interpretation"],
            }
        return result

    def plot(self, result: pd.DataFrame, ax: Any) -> None:
        if result.empty:
            ax.text(0.5, 0.5,
                    "No water site met the occupancy threshold.\n"
                    "Either none persists, or the run is too short to tell.",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
            return

        labels = [f"site {int(row.site)}" for row in result.itertuples()]
        # Filled where one molecule stays, hollow where many pass through:
        # the same occupancy means different things in the two cases.
        colours = ["#4E79A7" if "bound" in row.interpretation else "none"
                   for row in result.itertuples()]

        ax.barh(labels, result["occupancy"], color=colours,
                edgecolor=colour("SERIES"))
        ax.set_xlabel("fraction of frames occupied")
        ax.set_xlim(0, 1)
        ax.invert_yaxis()
        ax.set_title(
            "Water sites (filled: one molecule stays; "
            "hollow: many waters pass through)", fontsize=9)


register_analysis(WaterSites.name, WaterSites)
