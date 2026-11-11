"""How each helix lies and bends: its tilt and its kink, frame by frame.

A helix's axis is found from its alpha carbons as HELANAL finds it
(Bansal, Kumar and Velavan, J. Biomol. Struct. Dyn. 17, 811 (2000)), by
Kahn's local axes: at each alpha carbon the bisector of the angle its two
neighbours make points at the axis, and the cross product of two
consecutive bisectors lies along it. The local axes, turned to run from
the helix's N- to its C-terminus, give

- the **axis**, their mean direction;
- the **tilt**, the angle between the axis and the reference, from 0 to 90
  degrees, since a normal has no preferred sign: the bilayer's
  normal (z) where there is a bilayer, or the axis named in
  ``reference_axis``. Without either there is nothing to be tilted from,
  and no tilt is given;
- the **kink**, the angle between the mean local axis of the helix's first
  half and that of its second. A straight helix reads a few degrees, from
  the scatter of its local axes; a proline kink in a transmembrane helix
  reads twenty or more.

Each needs four alpha carbons in a row at least, and a helix of fewer than
``min_length`` residues (eight) is not followed: its local axes are too few
for a direction to mean much.

The helices are those DSSP finds in the first frame (runs of ``H``), or the
residue ranges named in ``helices``. A helix that unwinds during the run is
still followed as the same residues, and its tilt and kink then describe
whatever those residues have become: the share of frames in which DSSP
still finds each residue range mostly helical is recorded beside them.
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


def local_axes(ca: np.ndarray) -> np.ndarray:
    """Kahn's local helix axes along a run of alpha carbons.

    ``ca`` is (frames, residues, 3); the result is (frames, residues - 3, 3),
    unit vectors oriented from the first residue towards the last.
    """
    ca = np.asarray(ca, dtype=np.float64)
    bisector = (ca[:, :-2] - ca[:, 1:-1]) + (ca[:, 2:] - ca[:, 1:-1])
    axis = np.cross(bisector[:, :-1], bisector[:, 1:])
    size = np.linalg.norm(axis, axis=2, keepdims=True)
    size[size == 0] = 1.0
    axis /= size
    along = ca[:, -1] - ca[:, 0]
    sign = np.sign(np.einsum("fki,fi->fk", axis, along))
    sign[sign == 0] = 1.0
    return axis * sign[:, :, None]


def _angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Angles in degrees between rows of ``a`` and ``b`` (frames, 3)."""
    a = a / np.linalg.norm(a, axis=-1, keepdims=True)
    b = b / np.linalg.norm(b, axis=-1, keepdims=True)
    return np.degrees(np.arccos(np.clip(np.einsum("...i,...i->...", a, b), -1.0, 1.0)))


def helix_angles(ca: np.ndarray, reference: np.ndarray | None
                 ) -> tuple[np.ndarray | None, np.ndarray]:
    """The tilt from ``reference`` (or None) and the kink of a helix whose
    alpha carbons are ``ca`` (frames, residues, 3), in degrees per frame."""
    axes = local_axes(ca)
    axis = axes.mean(axis=1)
    half = axes.shape[1] // 2
    kink = _angle(axes[:, :half].mean(axis=1), axes[:, half:].mean(axis=1))
    if reference is None:
        return None, kink
    # A normal has no preferred sign: a helix along it either way is upright.
    tilt = _angle(axis, np.broadcast_to(reference, axis.shape))
    return np.minimum(tilt, 180.0 - tilt), kink


class HelixGeometry(Analysis):
    """The tilt and kink of each helix over the frames.

    Options
    -------
    helices : str, default ""
        Residue ranges as MDTraj selections, one per helix, separated by
        semicolons (``resid 10 to 32; resid 40 to 61``); a list in YAML is
        read too. Empty: the helices DSSP finds in the first frame.
    reference_axis : str, default "auto"
        What a tilt is measured from: ``x``, ``y`` or ``z``, or ``auto``,
        which is z (the normal) where there is a bilayer and no tilt where
        there is not.
    min_length : int, default 8
        The fewest residues a helix found by DSSP has to be followed.

    Output
    ------
    ``helix_geometry.dat`` -- comma-separated, one row per frame: for each
    helix k, ``helix_k_tilt_deg`` (where there is a reference) and
    ``helix_k_kink_deg``. Which residues each helix is, and the mean of
    each, are in ``options.json`` (``findings.helices``).
    """

    name = "helix_geometry"
    description = "Helix tilt and kink"
    default_selection = "protein"
    #: Which helices matter, and whether a tilt means anything without a
    #: bilayer, are the study's question.
    requires_naming = True

    AXES = ("auto", "x", "y", "z")

    def __init__(self, *, helices: Any = "", reference_axis: str = "auto",
                 min_length: int = 8, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if isinstance(helices, str):
            helices = helices.split(";")
        self.helices = [str(h).strip() for h in (helices or ()) if str(h).strip()]
        if reference_axis not in self.AXES:
            raise StudyError(
                f"`reference_axis` is one of {', '.join(self.AXES)}; it was "
                f"{reference_axis!r}.", code="analysis.option.inapplicable")
        self.reference_axis = reference_axis
        self.min_length = int(min_length)
        if self.min_length < 5:
            raise StudyError(
                f"`min_length` is at least 5, the fewest residues with two local "
                f"axes in each half; it was {self.min_length}.",
                code="analysis.option.out_of_range")
        self.options.update(helices=self.helices, reference_axis=self.reference_axis,
                            min_length=self.min_length)
        self._table: pd.DataFrame | None = None
        self._traj_for_plot: md.Trajectory | None = None

    # ------------------------------------------------------------------
    def _found_helices(self, traj: md.Trajectory, chosen: set[int]) -> list[list[int]]:
        """Runs of DSSP's H in the first frame, residue indices, per chain."""
        assigned = md.compute_dssp(traj[0], simplified=True)[0]
        runs: list[list[int]] = []
        for chain in traj.topology.chains:
            run: list[int] = []
            for residue in chain.residues:
                if (assigned[residue.index] == "H"
                        and any(a.index in chosen for a in residue.atoms)):
                    run.append(residue.index)
                    continue
                if len(run) >= self.min_length:
                    runs.append(run)
                run = []
            if len(run) >= self.min_length:
                runs.append(run)
        return runs

    def _named_helices(self, traj: md.Trajectory) -> list[list[int]]:
        runs = []
        for named in self.helices:
            try:
                atoms = traj.topology.select(named)
            except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
                raise StudyError(f"The helix {named!r} is not a selection MDTraj "
                                 f"reads: {exc}", code="analysis.selection.invalid") from exc
            residues = sorted({traj.topology.atom(int(a)).residue.index for a in atoms})
            if len(residues) < 5:
                raise StudyError(
                    f"The helix {named!r} holds {len(residues)} residue(s); a tilt "
                    "and a kink need five at least.", code="analysis.selection.empty")
            runs.append(residues)
        return runs

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        from fastmdxplora.lipids import is_bilayer

        topology = traj.topology
        chosen = {int(a) for a in self.select_atoms(traj)}
        runs = (self._named_helices(traj) if self.helices
                else self._found_helices(traj, chosen))
        if not runs:
            raise StudyError(
                f"DSSP finds no helix of {self.min_length} residues or more in the "
                f"first frame of {self.selection!r}. Name the helices in `helices`, "
                "or a lower `min_length`.", code="analysis.system.inapplicable")

        if self.reference_axis in ("x", "y", "z"):
            reference = np.eye(3)["xyz".index(self.reference_axis)]
            reference_said = f"{self.reference_axis}, as asked"
        elif is_bilayer(r.name for r in topology.residues):
            reference, reference_said = np.eye(3)[2], "z, the bilayer's normal"
        else:
            reference, reference_said = None, (
                "none: the system has no bilayer and no `reference_axis` was named, "
                "so there is nothing for a helix to be tilted from")

        assigned = md.compute_dssp(traj, simplified=True)
        columns: dict[str, np.ndarray] = {}
        said = []
        for number, residues in enumerate(runs, start=1):
            ca = []
            for index in residues:
                residue = topology.residue(index)
                atom = next((a for a in residue.atoms if a.name == "CA"), None)
                if atom is None:
                    raise StudyError(
                        f"{residue} in helix {number} has no alpha carbon.",
                        code="analysis.selection.empty")
                ca.append(atom.index)
            if len(ca) < 5:
                continue
            tilt, kink = helix_angles(traj.xyz[:, ca], reference)
            if tilt is not None:
                columns[f"helix_{number}_tilt_deg"] = tilt
            columns[f"helix_{number}_kink_deg"] = kink
            first, last = topology.residue(residues[0]), topology.residue(residues[-1])
            helical = (assigned[:, residues] == "H").mean(axis=1) >= 0.5
            said.append({
                "helix": number,
                "residues": f"{first.name}{first.resSeq} to {last.name}{last.resSeq}",
                "chain": int(first.chain.index),
                "length": len(residues),
                "tilt_deg": None if tilt is None else {
                    "mean": float(tilt.mean()), "sd": float(tilt.std())},
                "kink_deg": {"mean": float(kink.mean()), "sd": float(kink.std())},
                "still_helical_share_of_frames": float(helical.mean()),
            })
        self.findings["helices"] = said
        self.findings["found_by"] = (
            "the residue ranges named in `helices`" if self.helices else
            f"DSSP in the first frame: runs of H of {self.min_length} residues or more")
        self.findings["tilt_from"] = reference_said
        self.findings["method"] = (
            "Kahn's local axes from the alpha carbons, as HELANAL finds them "
            "(Bansal et al., J. Biomol. Struct. Dyn. 17, 811 (2000)): the axis their "
            "mean, the kink the angle between the mean of the first half's and the "
            "second's.")
        unwound = [h["helix"] for h in said if h["still_helical_share_of_frames"] < 0.5]
        if unwound:
            self.findings["unwound"] = (
                f"Helix {', '.join(map(str, unwound))} is mostly not helical in more "
                "than half the frames, and its tilt and kink describe what those "
                "residues became.")
        self._table = pd.DataFrame(columns)
        return self._table

    # ------------------------------------------------------------------
    def default_ylabel(self) -> str | None:
        return "Angle (degrees)"

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self.frame_axis(self._traj_for_plot)[1]

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        x = (self.frame_axis(self._traj_for_plot)[0] if self._traj_for_plot is not None
             else np.arange(len(result)))
        tilts = [c for c in result.columns if c.endswith("_tilt_deg")]
        shown = tilts or [c for c in result.columns if c.endswith("_kink_deg")]
        for column in shown[:12]:
            ax.plot(x, result[column], linewidth=1.0,
                    label=column.replace("_deg", "").replace("_", " "))
        if shown:
            ax.legend(loc="best", fontsize=7, ncol=2)

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)


register_analysis(HelixGeometry.name, HelixGeometry)
