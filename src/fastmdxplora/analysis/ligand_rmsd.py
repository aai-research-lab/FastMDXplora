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
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis, superposed
from fastmdxplora.analysis.orchestrator import register_analysis


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
            raise ValueError(
                "LigandRMSD requires `ligand_resname` (the ligand residue "
                "name, e.g. 'LIG'). This analysis applies only to "
                "protein-ligand complexes."
            )
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
            raise ValueError(
                f"No atoms matched ligand resname "
                f"{self.ligand_resname!r}; cannot compute ligand RMSD."
            )
        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) == 0:
            raise ValueError(
                f"Alignment selection {self.align_selection!r} matched zero "
                f"atoms; cannot align on the protein."
            )

        n = traj.n_frames
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise ValueError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            )

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
        return rmsd_nm.astype(np.float64)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x, _ = self.frame_axis_for_plot(result, self._traj_for_plot)
        ax.plot(x, result, linewidth=1.4, color=colour("SERIES"))
        ref_x = x[self._resolved_ref]
        ax.axvline(
            ref_x, color=colour("GUIDE"), linestyle=":", linewidth=1.0,
            label=f"reference (frame {self._resolved_ref})",
        )
        ax.legend(loc="best")

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
