"""Which frames clustering and the projections read, and what that leaves in.

Clustering and dimensionality reduction read every frame they are given, and
the frames a run spends relaxing from its starting structure are among them.
Those frames are a path away from the start, not a sample of the equilibrium
the rest of the run explores, and a clustering or a projection gives them the
same weight as any other: a relaxation can be found as a state of its own, or
take a principal component.

Reading every frame stays the default, so a study analysed before keeps its
results. What changes is that the record says how many frames were read,
from where, and whether the equilibration the RMSD detects is among them;
and ``start`` can begin the analysis at a time, or at that equilibration.

The equilibration is found as the RMSD analysis finds it: Chodera's method
(:func:`fastmdxplora.statistics.detect_equilibration`) on the RMSD of the
analysis's own atoms from the first frame, after superposing on them.
"""

from __future__ import annotations

from typing import Any

import mdtraj as md
import numpy as np

from fastmdxplora.refusals import StudyError

#: The value of ``start`` that begins at the equilibration the RMSD detects.
EQUILIBRATED = "equilibrated"


def start_as_given(start: Any) -> float | str | None:
    """``start`` as an analysis keeps it: None for the first frame (0, the
    default, needs no clock to find it), ``"equilibrated"``, or a time in ns
    after zero. Refused here, at construction, rather than when the frames
    are read."""
    if start is None:
        return None
    if isinstance(start, str):
        word = start.strip().lower()
        if word in ("", "none", "first", "beginning"):
            return None
        if word == EQUILIBRATED:
            return EQUILIBRATED
        try:
            start = float(word)
        except ValueError:
            raise StudyError(
                f"`start` is a time in ns or \"{EQUILIBRATED}\"; got {start!r}.",
                code="analysis.option.not_permitted") from None
    if isinstance(start, bool):
        raise StudyError(
            f"`start` is a time in ns or \"{EQUILIBRATED}\"; got {start!r}.",
            code="analysis.option.wrong_type")
    try:
        value = float(start)
    except (TypeError, ValueError):
        raise StudyError(
            f"`start` is a time in ns or \"{EQUILIBRATED}\"; got {start!r}.",
            code="analysis.option.wrong_type") from None
    if not np.isfinite(value) or value < 0:
        raise StudyError(
            f"`start` is a time in ns from the start of the trajectory, so it "
            f"cannot be {value:g}.",
            code="analysis.option.out_of_range")
    return None if value == 0 else value


def equilibration_by_rmsd(traj: md.Trajectory, atom_idx: np.ndarray) -> int:
    """Frames the RMSD of ``atom_idx`` from the first frame spends
    equilibrating, by Chodera's method, as the RMSD analysis finds them."""
    from fastmdxplora.analysis.base import superposed
    from fastmdxplora.statistics import detect_equilibration

    if traj.n_frames < 2:
        return 0
    aligned = superposed(traj, frame=0, atom_indices=atom_idx)
    xyz = np.asarray(aligned.xyz[:, atom_idx, :], dtype=np.float64)
    series = np.sqrt(((xyz - xyz[0]) ** 2).sum(axis=2).mean(axis=1))
    return int(detect_equilibration(series)[0])


def _times_ns(traj: md.Trajectory) -> np.ndarray | None:
    """The loader's clock in ns, or None where it set none."""
    try:
        time_ps = np.asarray(traj.time, dtype=float)
    except (AttributeError, TypeError, ValueError):
        return None
    if time_ps.size != traj.n_frames or time_ps.size < 2:
        return None
    if not np.all(np.isfinite(time_ps)) or not np.all(np.diff(time_ps) > 0):
        return None
    return time_ps / 1000.0


def first_frame(traj: md.Trajectory, atom_idx: np.ndarray,
                start: float | str | None) -> tuple[int, dict[str, Any]]:
    """The first frame to analyse, and the record of what that leaves in.

    The record gives the frames read and given, the first frame (and its
    time, where there is a clock), the equilibration the RMSD detects, and
    whether those frames are among the ones read.
    """
    n = int(traj.n_frames)
    equilibrating = equilibration_by_rmsd(traj, atom_idx)
    times = _times_ns(traj)

    if start is None:
        first = 0
    elif start == EQUILIBRATED:
        first = equilibrating
    else:
        if times is None:
            raise StudyError(
                f"`start: {start:g}` is a time, and this trajectory carries no "
                "clock to find it by. Give `start: equilibrated`, or load the "
                "trajectory with its saving interval.",
                code="analysis.data.absent")
        later = np.nonzero(times >= float(start) - 1e-9)[0]
        if later.size == 0:
            raise StudyError(
                f"`start: {start:g}` ns is past the last frame, at "
                f"{times[-1]:g} ns.",
                code="analysis.option.out_of_range")
        first = int(later[0])

    kept = n - first
    record: dict[str, Any] = {
        "n_frames_analysed": kept,
        "n_frames_given": n,
        "first_frame": first,
        "start": start,
        "equilibration_frames_by_rmsd": equilibrating,
        "equilibration_included": bool(equilibrating > first),
    }
    if times is not None:
        record["first_time_ns"] = float(times[first])
        if equilibrating:
            record["equilibrated_from_ns"] = float(times[min(equilibrating, n - 1)])
    if start is None:
        opening = f"All {n} frames were analysed, from the first"
    elif start == EQUILIBRATED:
        opening = (f"{kept} of {n} frames were analysed, from frame {first}, "
                   "where the RMSD equilibrates")
    else:
        opening = f"{kept} of {n} frames were analysed, from frame {first} ({start:g} ns on)"
    if equilibrating > first:
        said = (f"{opening}. The RMSD of these atoms from the first frame "
                f"equilibrates after {equilibrating} frames, and the "
                f"{equilibrating - first} before that are included: a "
                "relaxation from the starting structure can come out as a "
                f"state or a component of its own. `start: {EQUILIBRATED}` "
                "leaves them out.")
    elif equilibrating:
        said = (f"{opening}. The RMSD of these atoms from the first frame "
                f"equilibrates after {equilibrating} frames, and none of "
                "those are included.")
    else:
        said = (f"{opening}. The RMSD of these atoms detects no equilibration "
                "period to leave out.")
    record["said"] = said
    return first, record
