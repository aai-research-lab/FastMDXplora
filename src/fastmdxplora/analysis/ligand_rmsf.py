"""Ligand RMSF (per-atom fluctuation of the ligand after protein alignment).

After removing protein rigid-body motion (alignment on the protein), this
measures how much each ligand atom fluctuates about its mean position. It
reports the ligand's internal flexibility in the pocket: which parts of the
ligand are rigid and which sample multiple positions. Complements the ligand
pose RMSD (overall displacement) with a per-atom flexibility profile.

**The ligand is followed across periodic faces first**, the same way the
ligand RMSD follows it (:func:`~fastmdxplora.analysis.ligand_rmsd.ligand_in_the_receptor_frame`),
so the two read the same ligand. Fitted coordinates alone carry whichever
copy of each atom the file stored: a bound ligand sitting on a face of a 5 nm
box, its atom stored on the far side in 31 of 200 frames, read an RMSF of
1.80 nm against a true 0.035 nm.

**The frames are those after the pose has settled**, as the RMSF takes the
protein's: a fluctuation about a mean is the spread of an ensemble, and a
ligand still settling into its pocket adds its drift to it. With a ligand
relaxing 0.4 nm over the first fifth of a run, every frame read 1.7 times
the fluctuation of the settled frames. ``start`` (default ``"equilibrated"``)
leaves out the longer of the receptor's equilibration and the ligand's, each
found by Chodera's method, the ligand's on its heavy-atom, symmetry-corrected
RMSD in the receptor frame. A start leaving out more than a third of the run
is said, since a change of pose or an unbinding puts it there too.

Outputs ``ligand_rmsf.dat`` with columns (atom_serial, rmsf_nm) and a bar plot.
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.plotting import colour
from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.ligand_rmsd import ligand_in_the_receptor_frame
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.rmsf import _atom_labels
from fastmdxplora.refusals import StudyError


class LigandRMSF(Analysis):
    """Per-atom RMSF of the ligand after aligning on the protein.

    Parameters
    ----------
    ligand_resname : str
        Ligand residue name (e.g. ``"LIG"``). Supplied by the orchestrator.
    align_selection : str, default "protein and name CA"
        Atoms used for the rigid-body alignment (the receptor frame).
    ref : int, default 0
        Reference frame for the alignment.
    start : float or "equilibrated", default "equilibrated"
        The first frame the fluctuations are taken over: after the
        receptor's and the ligand's equilibration, or a time in ns on the
        trajectory's clock; 0 takes every frame.
    **kwargs
        Standard base-class options.
    """

    name = "ligand_rmsf"
    description = "Ligand RMSF (per-atom flexibility, after protein alignment)"
    requires_ligand = True
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
        start: float | str = "equilibrated",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not ligand_resname:
            raise StudyError(
                "LigandRMSF requires `ligand_resname`; it applies only to "
                "protein-ligand complexes."
            , code="analysis.option.missing_companion")
        self.ligand_resname = str(ligand_resname)
        self.align_selection = str(align_selection)
        self.ref = int(ref)
        from fastmdxplora.analysis.starting_frame import start_as_given

        self.start: float | str | None = start_as_given(start)
        self.options.update(
            ligand_resname=self.ligand_resname,
            align_selection=self.align_selection,
            ref=self.ref,
            start=self.start if self.start is not None else 0,
        )

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        """Compute per-ligand-atom RMSF.

        Returns
        -------
        np.ndarray, shape (n_ligand_atoms, 2)
            Columns: atom serial, RMSF in nm.
        """
        ligand_idx = traj.topology.select(f"resname {self.ligand_resname}")
        if len(ligand_idx) == 0:
            raise StudyError(
                f"No atoms matched ligand resname {self.ligand_resname!r}; "
                f"cannot compute ligand RMSF."
            , code="analysis.selection.empty")
        align_idx = traj.topology.select(self.align_selection)
        if len(align_idx) == 0:
            raise StudyError(
                f"Alignment selection {self.align_selection!r} matched zero "
                f"atoms; cannot align on the protein."
            , code="analysis.selection.arity")

        n = traj.n_frames
        from fastmdxplora.analysis.starting_frame import too_few_frames

        too_few_frames(n)
        ref = self.ref if self.ref >= 0 else n + self.ref
        if not (0 <= ref < n):
            raise StudyError(
                f"Reference frame {self.ref} is out of range for trajectory "
                f"with {n} frames."
            , code="analysis.option.out_of_range")

        # Follow the ligand across periodic faces, align on the protein, then
        # take each ligand atom's fluctuation about its mean position.
        xyz = ligand_in_the_receptor_frame(traj, ligand_idx, align_idx, ref)
        first = self._first(traj, align_idx, ligand_idx, xyz, ref)
        per_atom = _fluctuation(xyz[first:])
        if first:
            # Beside it, what every frame gives, so a start that left out
            # much of a run can be weighed against it.
            self.findings["frames"]["mean_rmsf_over_every_frame_nm"] = float(
                _fluctuation(xyz).mean())
            self.findings["frames"]["mean_rmsf_nm"] = float(per_atom.mean())

        serials = _atom_labels(
            [traj.topology.atom(int(i)) for i in ligand_idx]
        )
        return np.column_stack([serials, per_atom]).astype(np.float64)

    def _pose_series(self, traj: md.Trajectory, ligand_idx: np.ndarray,
                     xyz: np.ndarray, ref: int) -> np.ndarray:
        """The ligand's RMSD from ``ref`` in the receptor frame, as the
        ligand RMSD measures it: over its heavy atoms, at the relabelling of
        a symmetric ligand that fits best, so a ring turning onto itself is
        not read as the pose still settling."""
        from fastmdxplora.analysis.ligand_rmsd import automorphisms, symmetric_rmsd

        heavy = [k for k, i in enumerate(ligand_idx)
                 if getattr(traj.topology.atom(int(i)).element, "symbol", None) != "H"]
        columns = heavy or list(range(len(ligand_idx)))
        coords = np.asarray(xyz, dtype=np.float64)[:, columns, :]
        found = automorphisms(traj.topology, [int(ligand_idx[k]) for k in columns])
        if found is not None:
            return symmetric_rmsd(coords, coords[ref], found[0])
        return np.sqrt(((coords - coords[ref]) ** 2).sum(axis=2).mean(axis=1))

    def _first(self, traj: md.Trajectory, align_idx: np.ndarray,
               ligand_idx: np.ndarray, xyz: np.ndarray, ref: int) -> int:
        """The first frame after the receptor and the pose have settled,
        recorded with both. The pose is judged on the ligand's RMSD from
        ``ref`` in the receptor frame, which a fit on the ligand itself would
        not see; whatever ``start`` is, the record says whether the ligand's
        settling is among the frames read."""
        from fastmdxplora.analysis.starting_frame import (
            EQUILIBRATED,
            _times_ns,
            first_frame,
        )
        from fastmdxplora.statistics import detect_equilibration

        n = traj.n_frames
        first, record = first_frame(traj, align_idx, self.start, ref)
        receptor = int(record["equilibration_frames_by_rmsd"])
        pose = self._pose_series(traj, ligand_idx, xyz, ref)
        settling = int(detect_equilibration(pose)[0])
        times = _times_ns(traj)
        if self.start == EQUILIBRATED and settling > first:
            first = settling
            record["first_frame"] = first
            record["n_frames_analysed"] = n - first
            if times is not None:
                record["first_time_ns"] = float(times[first])
        record["ligand_equilibration_frames_by_rmsd"] = settling
        record["ligand_equilibration_included"] = bool(settling > first)
        record["equilibration_included"] = bool(max(receptor, settling) > first)
        if times is not None and settling:
            record["ligand_equilibrated_from_ns"] = float(times[min(settling, n - 1)])

        given_time = (f" ({float(self.start):g} ns on)"
                      if isinstance(self.start, (int, float)) else "")
        parts = [f"{n - first} of {n} frames were analysed, from frame {first}{given_time}."]
        def after(frames: int, unit: str) -> str:
            return f"equilibrates after {frames}{unit}" if frames \
                else "shows no equilibration"

        parts.append(
            f"The receptor's RMSD {after(receptor, ' frames')} and the ligand's, "
            f"in the receptor frame, {after(settling, '')}")
        included = [name for name, frames in (("receptor's", receptor), ("ligand's", settling))
                    if frames > first]
        parts[-1] += (
            f"; the {' and the '.join(included)} "
            + ("equilibrations are" if len(included) == 2 else "equilibration is")
            + " among the frames read." if included
            else ", and neither is among the frames read.")
        # A start this late is as often a change of pose or an unbinding as a
        # slow settling, and the detection cannot tell them apart (a hop at
        # frame 330 of 500 and a ligand leaving at 150 both put it near two
        # thirds), so the share is said and the every-frame value is kept.
        record["more_than_a_third_left_out"] = bool(first > n / 3)
        set_by_the_ligand = self.start == EQUILIBRATED and settling >= receptor
        if first > n / 3 and set_by_the_ligand:
            parts.append(
                f"That leaves out {first / n:.0%} of the run: the ligand's RMSD "
                "changed for that long, which a change of pose or an unbinding "
                "also does, and the fluctuation over the rest describes only "
                "where it ended. `ligand_rmsd` shows which, and the value over "
                "every frame is recorded beside this one."
                + (f" The receptor's RMSD changed for {receptor} frames as well, "
                   "which moves the ligand in the receptor frame too."
                   if receptor > n / 3 else ""))
        elif first > n / 3 and self.start == EQUILIBRATED:
            parts.append(
                f"That leaves out {first / n:.0%} of the run, for as long as the "
                "receptor's RMSD changed; the value over every frame is "
                "recorded beside this one.")
        elif first > n / 3:
            parts.append(f"That leaves out {first / n:.0%} of the run, as `start` asks.")
        record["said"] = " ".join(parts)
        if first > n - 2:
            raise StudyError(
                f"`start` leaves {n - first} of {n} frames, and a fluctuation "
                "needs at least two.",
                code="analysis.option.out_of_range")
        self.findings["frames"] = record
        return first

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        serials = result[:, 0].astype(int)
        rmsf = result[:, 1]
        x = np.arange(len(serials))
        ax.bar(x, rmsf, color=colour("SERIES"))
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in serials], fontsize=7, rotation=90)
        from fastmdxplora.analysis.starting_frame import which_frames

        record = self.findings.get("frames")
        said_frames = which_frames(record)
        if said_frames:
            if isinstance(record, dict) and record.get("more_than_a_third_left_out"):
                # Not called equilibration where it may be a change of pose.
                said_frames = said_frames.replace(" as equilibration", "")
            ax.margins(y=0.15)
            ax.text(0.01, 0.98, said_frames, transform=ax.transAxes, ha="left",
                    va="top", fontsize="small")

    def default_xlabel(self) -> str | None:
        return "Ligand atom (serial)"

    def default_ylabel(self) -> str | None:
        return "RMSF (nm)"


def _fluctuation(xyz: np.ndarray) -> np.ndarray:
    """Each atom's RMS fluctuation about its mean position."""
    disp = xyz - xyz.mean(axis=0)
    return np.sqrt(np.mean(np.sum(disp * disp, axis=2), axis=0))


register_analysis(LigandRMSF.name, LigandRMSF)
