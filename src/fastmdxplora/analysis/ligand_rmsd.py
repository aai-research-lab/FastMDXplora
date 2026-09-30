"""Ligand pose RMSD (RMSD of the ligand after protein alignment).

This is the headline protein-ligand stability metric: it measures whether the
ligand stays in its binding pose over the trajectory. Each frame is rigidly
aligned onto a reference using the **protein** atoms (so protein tumbling is
removed), and then the RMSD is computed on the **ligand** atoms of the
already-aligned coordinates. A low, flat profile means the ligand holds its
pose; a rising profile means it is drifting or unbinding.

This differs from the standard :class:`~fastmdxplora.analysis.rmsd.RMSD`,
which aligns and measures on the same atom set. Here alignment (protein) and
measurement (ligand) use different selections, which is the correct way to ask
"how much has the ligand moved *relative to the protein*".

Output is a single-column ``ligand_rmsd.dat`` of RMSD values in nanometers,
and a time-series figure.

**A ligand that leaves the site has no pose.** Followed across the periodic
boundary, as it must be, its RMSD from where it started is then the length of
a path through solvent, which grows without bound however long the run is.
So the distance from the ligand to the site it started in is measured too,
from the closest pair of heavy atoms by minimum image, which is bounded by
the box. Where the ligand is away from the site, that is said, the frames
are shaded, the distance is written beside the RMSD
(``ligand_site_distance.dat``), and no mean RMSD is given: a mean of a
quantity that grows without bound is a statement about the run's length.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.refusals import StudyError


def _followed_across_the_boundary(traj, ligand_idx, anchor_idx):
    """The ligand's path relative to the receptor, continuous across faces.

    A ligand that leaves the pocket crosses the periodic boundary, and raw
    Cartesian displacement then jumps by a box repeat distance. On the 20 ns
    T4-lysozyme unbound control the benzene gave sixty-five frame-to-frame
    jumps above 2 nm, the largest clustered at 6.58-6.80 nm between frames
    10 ps apart, and the reported RMSD reached 9.49 nm in a box smaller than
    that. A benzene does not travel 6.8 nm in 10 ps.

    Two earlier attempts chose, per frame, the periodic image nearest the
    receptor. Both were wrong, and the second was wrong in an instructive
    way. Per-frame imaging answers "where is it now", and its answer is
    bounded by half the box; a pose RMSD asks "how far has it travelled from
    where it started", which is a question about a path. Imaging a path
    frame by frame does not make it continuous -- if the chosen image flips
    between one frame and the next, the path jumps even though the ligand
    did not move.

    So the displacement is unwrapped instead: each frame takes the image
    nearest the *previous frame*, not the nearest the receptor. Consecutive
    frames are a saving interval apart and the ligand moves a fraction of an
    angstrom in that time, so a step of nearly a whole lattice vector is an
    image swap and nothing else. That is what makes rounding safe here and
    unsafe in the earlier attempts: the correction is applied to a step that
    is either tiny or almost exactly a lattice vector, never to an arbitrary
    separation where the nearest image in a skewed cell is genuinely hard to
    pick. In the rhombic dodecahedron these runs use, rounding fractional
    coordinates of an arbitrary separation picks the wrong image for 30% of
    random pairs; applied to a per-frame step it is exact.

    The starting displacements come from ``md.compute_displacements`` with
    ``periodic=True``, which handles a triclinic cell properly, and are read
    from the *unaligned* trajectory, as ``superposed`` requires: alignment
    rotates coordinates and not the box.

    Returns ``None`` where the trajectory carries no unit cell.
    """
    if traj.unitcell_vectors is None:
        return None

    ligand_idx = np.asarray(ligand_idx)
    anchor = int(np.asarray(anchor_idx)[0])

    pairs = np.column_stack([np.full(ligand_idx.size, anchor), ligand_idx])
    disp = np.asarray(
        md.compute_displacements(traj, pairs, periodic=True), dtype=np.float64)

    box = np.asarray(traj.unitcell_vectors, dtype=np.float64)
    inverse = np.linalg.inv(box)

    # Walk the frames, holding each one to the image nearest its predecessor.
    for frame in range(1, len(disp)):
        step = disp[frame] - disp[frame - 1]
        lattice = np.round(step @ inverse[frame])
        disp[frame] = disp[frame] - lattice @ box[frame]

    anchor_xyz = np.asarray(traj.xyz[:, anchor, :], dtype=np.float64)
    return anchor_xyz[:, None, :] + disp


def _carried_by(source, destination, points):
    """Apply to `points` the rigid transform that carried `source` onto
    `destination`.

    ``superposed`` has already aligned the trajectory, and the unwrapped
    ligand must follow the same rotation and translation. Recovering it from
    the anchor atoms is exact and avoids re-implementing the alignment.
    """
    source = np.asarray(source, dtype=np.float64)
    destination = np.asarray(destination, dtype=np.float64)
    source_centre = source.mean(axis=1, keepdims=True)
    destination_centre = destination.mean(axis=1, keepdims=True)
    covariance = np.einsum("fpi,fpj->fij",
                           source - source_centre,
                           destination - destination_centre)
    u, _, vt = np.linalg.svd(covariance)
    # Guard against a reflection, which is not a rotation.
    handedness = np.sign(np.linalg.det(np.einsum("fij,fjk->fik", u, vt)))
    correction = np.zeros_like(covariance)
    correction[:, 0, 0] = correction[:, 1, 1] = 1.0
    correction[:, 2, 2] = handedness
    rotation = np.einsum("fij,fjk,fkl->fil", u, correction, vt)
    return np.einsum("fpi,fij->fpj",
                     points - source_centre, rotation) + destination_centre


#: Protein heavy atoms this close to the ligand in the reference frame are
#: the site it started in.
SITE_NM = 0.5

#: With no heavy atom this close to any atom of its site, the ligand is away
#: from it: past the reach of a hydrogen bond or a hydrophobic contact, with
#: room for a layer of water between.
AWAY_NM = 0.6


def distance_to_the_site(traj, ligand_heavy, reference: int):
    """The closest approach of the ligand to its starting site, per frame.

    Returns the distances in nm and the site's atoms, or None where the
    ligand touched no protein atom in the reference frame: then it had no
    site to leave. By minimum image, from ``md.compute_distances``, which
    handles a triclinic cell.
    """
    protein_heavy = traj.topology.select("protein and not element H")
    if protein_heavy.size == 0 or len(ligand_heavy) == 0:
        return None
    site = md.compute_neighbors(traj[reference], SITE_NM, np.asarray(ligand_heavy),
                                haystack_indices=protein_heavy)[0]
    if site.size == 0:
        return None
    pairs = np.array([[i, j] for i in ligand_heavy for j in site])
    distances = md.compute_distances(traj, pairs,
                                     periodic=traj.unitcell_vectors is not None)
    return distances.min(axis=1).astype(np.float64), site


class LigandRMSD(Analysis):
    """Per-frame RMSD of the ligand after aligning on the protein.

    Parameters
    ----------
    ligand_resname : str
        Residue name of the ligand (e.g. ``"LIG"``). Required — this analysis
        only makes sense for a protein-ligand complex. The orchestrator
        supplies it automatically from the setup manifest.
    align_selection : str, default "protein and name CA"
        Atom selection used for the rigid-body alignment (the receptor frame).
        Cα atoms are the standard, robust choice.
    ref : int, default 0
        Reference frame. Negative indices count from the end.
    **kwargs
        Standard base-class options.

    Notes
    -----
    The ``selection`` attribute is not used for the measurement here (the
    measured atoms are always the ligand); alignment is controlled by
    ``align_selection``.
    """

    name = "ligand_rmsd"
    time_series = True
    reweightable = (None, "Ligand RMSD (nm)")
    description = "Ligand pose RMSD (after protein alignment)"
    requires_ligand = True
    # Measurement atoms are the ligand, resolved from ligand_resname; this
    # analysis is ligand-only by nature, so it does not use scope.
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
            raise StudyError(
                "LigandRMSD requires `ligand_resname` (the ligand residue "
                "name, e.g. 'LIG'). This analysis applies only to "
                "protein-ligand complexes."
            , code="analysis.option.missing_companion")
        self.ligand_resname: str = str(ligand_resname)
        self.align_selection: str = str(align_selection)
        self.ref: int = int(ref)
        self.options.update(
            ligand_resname=self.ligand_resname,
            align_selection=self.align_selection,
            ref=self.ref,
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute per-frame ligand RMSD after protein alignment.

        Returns
        -------
        np.ndarray of shape (n_frames,)
            Ligand RMSD in nanometers.
        """
        ligand_idx = traj.topology.select(f"resname {self.ligand_resname}")
        if len(ligand_idx) == 0:
            raise StudyError(
                f"No atoms matched ligand resname "
                f"{self.ligand_resname!r}; cannot compute ligand RMSD."
            , code="analysis.selection.empty")
        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) == 0:
            raise StudyError(
                f"Alignment selection {self.align_selection!r} matched zero "
                f"atoms; cannot align on the protein."
            , code="analysis.selection.arity")

        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise StudyError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            , code="analysis.option.out_of_range")

        # Align every frame onto the reference using the PROTEIN atoms. This
        # transforms all coordinates (including the ligand) by the same
        # rigid-body operation, so the residual ligand motion is motion
        # relative to the protein frame.
        # Follow the ligand across periodic faces FIRST, while the box still
        # describes the frame. After alignment it does not: superposed()
        # rotates coordinates and discards the cell precisely so a stale box
        # cannot be consulted by mistake.
        followed = _followed_across_the_boundary(traj, ligand_idx, align_idx)

        aligned = superposed(traj, frame=ref, atom_indices=align_idx)

        if followed is None:
            ligand_xyz = np.asarray(
                aligned.xyz[:, ligand_idx, :], dtype=np.float64)
        else:
            ligand_xyz = _carried_by(traj.xyz[:, align_idx, :],
                                     aligned.xyz[:, align_idx, :], followed)

        # RMSD of the LIGAND atoms on the aligned coordinates, vs the
        # reference frame's ligand coordinates. No further alignment.
        ref_xyz = ligand_xyz[ref]
        disps = ligand_xyz - ref_xyz
        rmsd_nm = np.sqrt(np.mean(np.sum(disps * disps, axis=2), axis=1))

        self._resolved_ref = ref
        heavy = [int(i) for i in ligand_idx
                 if traj.topology.atom(int(i)).element is None
                 or traj.topology.atom(int(i)).element.symbol != "H"]
        measured = distance_to_the_site(traj, heavy or list(ligand_idx), ref)
        self._site_distance = None if measured is None else measured[0]
        self._record_where_the_ligand_was(traj, measured)
        return rmsd_nm.astype(np.float64)

    def _record_where_the_ligand_was(self, traj: md.Trajectory, measured) -> None:
        """Whether the ligand stayed at its site, in the findings."""
        if measured is None:
            self.findings["site"] = {"not_measured": (
                "The ligand touched no protein atom in the reference frame, so "
                "it had no site to stay at or leave.")}
            return
        distance, site = measured
        away = distance > AWAY_NM
        record: dict[str, Any] = {
            "site_atoms": int(site.size), "away_nm": AWAY_NM,
            "frames_away": int(away.sum()), "share_away": float(away.mean()),
            "furthest_nm": float(distance.max()),
        }
        if away.any():
            first = int(np.argmax(away))
            record["first_frame_away"] = first
            try:
                x, label = self.frame_axis(traj)
                record["first_away_at"] = f"{float(x[first]):g} ({label})"
            except Exception:  # noqa: BLE001 - the frame number stands alone
                pass
        self.findings["site"] = record

    def _record_what_the_mean_is_worth(self, traj: md.Trajectory) -> None:
        """No mean where the ligand left its site; the base class's otherwise."""
        super()._record_what_the_mean_is_worth(traj)
        site = self.findings.get("site") or {}
        if not site.get("frames_away"):
            return
        where = site.get("first_away_at") or f"frame {site['first_frame_away']}"
        self.findings["mean"] = {
            "n_frames": int(traj.n_frames),
            "not_a_measurement": (
                f"The ligand left the site it started in at {where} and was away "
                f"from it in {site['share_away']:.0%} of the frames (no heavy "
                f"atom within {AWAY_NM} nm of the site). Its RMSD from there "
                "describes a path through solvent, which grows without bound, "
                "not a pose, so no mean is given. Its distance to the site, "
                "which the box bounds, is in ligand_site_distance.dat."),
        }

    def save_data(self, result: Any, path: Path) -> Path:
        written = super().save_data(result, path)
        distance = getattr(self, "_site_distance", None)
        if distance is not None:
            np.savetxt(
                path.parent / "ligand_site_distance.dat", distance, fmt="%.8e",
                header=("ligand_site_distance: closest heavy-atom distance from the "
                        "ligand to the site it started in, nm, one value per frame"))
        return written

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis_for_plot(result, self._traj_for_plot)
        ax.plot(x, result, linewidth=1.2, color=colour("SERIES"))
        distance = getattr(self, "_site_distance", None)
        if distance is not None and len(distance) == len(x) and (distance > AWAY_NM).any():
            # Where the curve stops being a pose, said on the curve.
            ax.fill_between(x, 0, 1, where=distance > AWAY_NM, step="mid",
                            transform=ax.get_xaxis_transform(), color=colour("BAND"), alpha=0.5,
                            linewidth=0, label="away from its site", zorder=0)
            ax.legend(loc="upper left", frameon=False)
        # No marker for the reference frame. It was a vertical line at the
        # left edge labelled "reference (frame 0)", which took a legend entry
        # to say that a curve of displacement from a frame starts at zero at
        # that frame. What a reader needs from this plot is where the pose
        # equilibrated and at what value, and the base class draws that.
        ax.set_ylim(bottom=0.0)

    # Plot plumbing mirrors RMSD.
    _traj_for_plot: md.Trajectory | None = None
    _resolved_ref: int = 0

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def frame_axis_for_plot(
        self, result: np.ndarray, traj: md.Trajectory | None
    ) -> tuple[np.ndarray, str]:
        if traj is None:
            return np.arange(len(result)), "Frame"
        return self.frame_axis(traj)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        _, label = self.frame_axis(self._traj_for_plot)
        return label

    def default_ylabel(self) -> str | None:
        return "Ligand RMSD (nm)"


register_analysis(LigandRMSD.name, LigandRMSD)
