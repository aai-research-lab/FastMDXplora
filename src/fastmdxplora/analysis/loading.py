"""Trajectory loading and topology resolution.

This module provides ``load_trajectory``, the canonical entry point for
turning user-supplied paths into an MDTraj ``Trajectory`` object. It handles:

  - Single or multiple trajectory files (multiple files are concatenated)
  - Explicit topology files when the trajectory format lacks topology
  - Topology auto-resolution (a sibling .pdb when the user omits ``top``)
  - Frame selection (``stride``, ``first``, ``last``)
  - Helpful error messages when files don't exist or are the wrong format

The function is deliberately permissive about input forms (single path, list
of paths, glob pattern) and strict about the resulting object (always a
single concatenated MDTraj trajectory with a topology attached).
"""

from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Sequence, Union

import mdtraj as md
import numpy as np

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError

logger = get_logger("analysis.loading")


# Trajectory formats that carry their own topology and therefore do not
# require an external topology file.
SELF_TOPOLOGY_FORMATS = frozenset({".pdb", ".pdbx", ".cif", ".h5", ".lh5"})

# Trajectory formats that require an external topology file.
EXTERNAL_TOPOLOGY_FORMATS = frozenset(
    {".dcd", ".xtc", ".trr", ".nc", ".netcdf", ".binpos", ".lammpstrj", ".dtr", ".xyz"}
)

# Formats that come back from MDTraj carrying the simulation clock the run
# actually kept. Everything else -- DCD above all, which is what this package
# writes -- comes back with ``time`` set to the frame index in picoseconds,
# and a frame index in picoseconds is indistinguishable from a real clock
# until someone reads the axis.
#
# Measured, mdtraj 1.11.1, 2,000 frames written 50 ps apart:
#
#   written by                                     span read back
#   mdtraj.reporters.DCDReporter                      1.999 ns
#   openmm.app.DCDFile, dt=2 fs, interval=25000       1.999 ns
#   the same frames as .xtc                          99.950 ns
#   the same frames as .nc                           99.950 ns
#
# So it is not the reporter and not OpenMM: DCD carries a timestep in its
# header and MDTraj's reader discards it. The two writers agree because
# neither is consulted.
FORMATS_THAT_CARRY_A_CLOCK = frozenset(
    {".xtc", ".trr", ".nc", ".netcdf", ".h5", ".lh5", ".dtr"}
)


PathLike = Union[str, Path]
TrajectoryInput = Union[PathLike, Sequence[PathLike]]


class TrajectoryLoadError(CodedError, ValueError):
    """Raised when a trajectory cannot be located, opened, or parsed."""

    default_code = "analysis.trajectory.unreadable"


def _resolve_paths(traj: TrajectoryInput) -> list[Path]:
    """Normalize the trajectory argument to a list of concrete file paths.

    Accepts a single path, a list/tuple of paths, or a glob pattern.
    Globs are expanded; the result is sorted lexicographically so that
    multi-shot trajectories with sortable names (run01.dcd, run02.dcd, ...)
    are concatenated in the expected order.
    """
    if isinstance(traj, (str, Path)):
        single = str(traj)
        if any(ch in single for ch in "*?[]"):
            expanded = sorted(_glob.glob(single))
            if not expanded:
                raise TrajectoryLoadError(f"No files match pattern: {single!r}")
            return [Path(p) for p in expanded]
        return [Path(single)]

    if isinstance(traj, Sequence):
        paths = [Path(str(p)) for p in traj]
        if not paths:
            raise TrajectoryLoadError("Trajectory input is an empty sequence.")
        return paths

    raise TrajectoryLoadError(
        f"Unsupported trajectory input type: {type(traj).__name__}."
    )


def _resolve_topology(
    traj_paths: list[Path],
    top: PathLike | None,
) -> Path | None:
    """Decide which topology file to use.

    Logic:
      1. If the user supplied ``top``, validate it exists and return it.
      2. If the first trajectory file is self-topologized (PDB, H5...), no
         external topology is needed.
      3. Otherwise, look for a sibling .pdb file next to the first trajectory.
      4. Otherwise, raise — the user must provide a topology.
    """
    if top is not None:
        top_path = Path(top)
        if not top_path.exists():
            raise TrajectoryLoadError(f"Topology file not found: {top_path}")
        return top_path

    first = traj_paths[0]
    suffix = first.suffix.lower()

    if suffix in SELF_TOPOLOGY_FORMATS:
        return None

    if suffix in EXTERNAL_TOPOLOGY_FORMATS:
        candidate = first.with_suffix(".pdb")
        if candidate.exists():
            logger.debug("Auto-resolved topology: %s", candidate)
            return candidate
        raise TrajectoryLoadError(
            f"Trajectory format {suffix!r} requires a topology file, but none "
            f"was supplied and no {candidate.name} was found alongside "
            f"{first.name}. Pass `top=<path>` explicitly."
        )

    raise TrajectoryLoadError(
        f"Unrecognized trajectory format: {suffix!r} (file: {first})"
    )


def image_whole(trajectory: md.Trajectory, *, inplace: bool) -> md.Trajectory:
    """Molecules made whole across the periodic boundary, anchored as MDTraj
    would choose -- or, where its heuristic finds nothing, on the largest.

    MDTraj anchors on molecules larger than the one a tenth of the way down
    a size ranking. A molecule alone in the box is that molecule, never
    larger than itself, so it found no anchor and raised. Passing the
    anchors it would have guessed is the call it makes without them, so a
    solvated system is imaged exactly as before.
    """
    try:
        anchors = trajectory.topology.guess_anchor_molecules()
    except ValueError:
        molecules = trajectory.topology.find_molecules()
        anchors = [max(molecules, key=len)] if molecules else []
    from fastmdxplora.analysis.imaging import image_trajectory

    result = trajectory if inplace else trajectory[:]
    image_trajectory(result, anchors)
    return result


def _made_whole(trajectory: md.Trajectory) -> md.Trajectory:
    """Molecules put back together across the periodic boundary, once.

    A trajectory written by another engine -- a GROMACS ``.xtc``, an AMBER
    ``.nc``, anything not run through ``-pbc mol`` -- routinely stores a
    molecule split across a box face. Nothing downstream of here images
    anything: RMSD, RMSF, radius of gyration, SASA and clustering all read
    ``traj.xyz`` directly, so a split protein gives a radius of gyration
    several times too large, an RMSF two orders of magnitude too large, and
    every one of them succeeds, writes its file, draws its figure and
    reports ``status="ok"``.

    Anchored on the macromolecules where there are any, so the protein
    stays whole, and every other solute molecule is then moved to the copy
    of it nearest them (:func:`_nearest_copies`). The ligand and ions were
    anchors too, and MDTraj keeps an anchor in whichever copy it was stored
    in: a ligand that had left the pocket was measured in the copy the
    engine wrote, up to a box length away, and a radius of gyration of
    protein and ligand came out that much too large. MDTraj's own placement
    of the others rounds fractional coordinates, which is exact only for a
    rectangular box and misses in the dodecahedron and octahedron setup
    builds by default, hence the exact search. Where there is no
    macromolecule, every solute molecule anchors, as before.

    The imaging is MDTraj's, computed over every frame at once
    (:mod:`fastmdxplora.analysis.imaging`): MDTraj compared every atom of
    one chain with every atom of the other to place a dimer, about a
    quarter of a second a frame for 1AKE, and the same coordinates now take
    a few milliseconds.

    Failure is not fatal: a topology without bonds cannot be made whole, and
    an analysis of what was loaded beats refusing to load it. The reason is
    logged rather than swallowed, so a silently unimaged run can be
    recognised afterwards.
    """
    if trajectory.unitcell_vectors is None:
        return trajectory
    from fastmdxplora.analysis.residues import _is_polymer

    try:
        topology = trajectory.topology
        molecules = topology.find_molecules()
        solute = [m for m in molecules
                  if not all(a.residue.is_water for a in m)]
        # Older MDTraj raises from ``is_nucleic``; _is_polymer answers anyway.
        large = [m for m in solute if any(_is_polymer(a.residue) for a in m)]
        anchors = large or solute or topology.guess_anchor_molecules()
        from fastmdxplora.analysis.imaging import image_trajectory

        image_trajectory(trajectory, anchors, [m for m in molecules if m not in anchors])
    except Exception as exc:  # MDTraj raises a variety of types
        logger.warning(
            "Could not image molecules across the periodic boundary (%s); "
            "the trajectory is analysed as stored. If it was written without "
            "molecules made whole, contacts and shape descriptors will be wrong "
            "in ways that do not announce themselves.", _named(exc),
        )
        return trajectory
    if large:
        anchored = {id(m) for m in large}
        try:
            _nearest_copies(trajectory, large,
                            [m for m in solute if id(m) not in anchored])
        except Exception as exc:  # the molecules are whole; only placement is lost
            logger.warning(
                "Molecules were made whole, but could not be moved to the copy "
                "nearest the macromolecule (%s); a ligand or ion may be analysed "
                "in a periodic copy up to a box length away.", _named(exc),
            )
    return trajectory


def _named(exc: Exception) -> str:
    """An exception as its type and message, so an empty message still says something."""
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _nearest_copies(trajectory: md.Trajectory, anchors: list, others: list) -> None:
    """Move each molecule in ``others`` to its copy nearest the anchors.

    Nearest by centre: the lattice vector that puts the molecule's centroid
    closest to the anchors' centroid, frame by frame. Searched exactly, over
    every lattice vector within two cells of the fractional-rounding guess,
    which covers the reduced boxes OpenMM writes whatever their shape.
    Molecules are moved whole, so they stay made whole.
    """
    if not others:
        return
    xyz = trajectory.xyz
    box = np.asarray(trajectory.unitcell_vectors, dtype=np.float64)   # (F, 3, 3), rows
    inverse = np.linalg.inv(box)
    shifts = np.array(list(np.ndindex(5, 5, 5)), dtype=np.float64) - 2.0   # (125, 3)
    lattice = np.einsum("kj,fji->fki", shifts, box)                      # (F, 125, 3)
    anchor_atoms = np.fromiter((a.index for m in anchors for a in m), dtype=np.int64)
    centre = xyz[:, anchor_atoms, :].astype(np.float64).mean(axis=1)    # (F, 3)
    for molecule in others:
        atoms = np.fromiter((a.index for a in molecule), dtype=np.int64)
        offset = xyz[:, atoms, :].astype(np.float64).mean(axis=1) - centre
        guess = -np.rint(np.einsum("fi,fij->fj", offset, inverse))
        offset = offset + np.einsum("fj,fji->fi", guess, box)
        candidates = offset[:, None, :] + lattice                        # (F, 125, 3)
        best = np.argmin(np.einsum("fki,fki->fk", candidates, candidates), axis=1)
        move = np.einsum("fj,fji->fi", guess, box) + lattice[np.arange(len(best)), best]
        xyz[:, atoms, :] += move[:, None, :].astype(xyz.dtype)


def _with_one_clock(trajectory: md.Trajectory, n_files: int) -> md.Trajectory:
    """One time axis across files, rather than each shot keeping its own.

    ``md.load([run01.dcd, run02.dcd])`` concatenates the coordinates and
    leaves every file's ``time`` starting where that file started, so a
    two-shot load comes back with `[0..9, 0..9]`. Every time-series figure
    then draws an x axis that runs forward, jumps back, and overplots the
    second half on the first; `water_sites` computes residence from
    `time[-1] - time[0]` and gets the length of one shot. The docstring
    advertises exactly this usage -- "name them run01.dcd, run02.dcd ... for
    multi-shot data".

    Each block is re-offset to continue the one before it, keeping the
    spacing the files carry. Where the time is already monotonic -- one file,
    or files whose clocks were written continuously -- nothing is touched.
    """
    time = getattr(trajectory, "time", None)
    if n_files < 2 or time is None or len(time) < 2:
        return trajectory
    if np.all(np.diff(time) > 0):
        return trajectory

    step = float(np.median(np.diff(time)[np.diff(time) > 0])) if np.any(
        np.diff(time) > 0) else 1.0
    mended = np.array(time, dtype=float)
    for index in range(1, len(mended)):
        if mended[index] <= mended[index - 1]:
            mended[index:] += mended[index - 1] - mended[index] + step
    trajectory.time = mended
    logger.info(
        "The %d trajectory files each carried their own clock, so the time "
        "axis ran backwards where one ended and the next began. They are "
        "joined end to end at the interval the files use (%.4g ps); frame "
        "order is unchanged.", n_files, step,
    )
    return trajectory


def _frames_in(path: Path, top: Path | None) -> int | None:
    """How many frames one trajectory file holds, or None where it cannot say."""
    from fastmdxplora.utils import suppress_native_output

    try:
        with suppress_native_output(), md.open(str(path)) as handle:
            return int(len(handle))
    except Exception:  # noqa: BLE001 - a format without a length; count instead
        pass
    try:
        with suppress_native_output():
            loaded = (md.load(str(path), top=str(top)) if top is not None
                      else md.load(str(path)))
        return int(loaded.n_frames)
    except Exception:  # noqa: BLE001 - the caller falls back and says so
        return None


def written_frames(
    paths: list[Path], top: Path | None, stride: int | None, n_loaded: int
) -> np.ndarray:
    """Which frame of the run, counted across every file, each loaded frame is.

    MDTraj strides each file on its own from that file's first frame, so
    with several files the loaded frames are not every ``stride``-th frame
    of the run: two files of five frames at stride 2 give frames 0, 2, 4 of
    the first and 0, 2, 4 of the second, which are frames 0, 2, 4, 5, 7, 9
    of the run, not 0 to 10. Built from each file's own length; where a
    length cannot be read, the frames are taken as one stream and the log
    says so.
    """
    step = int(stride) if stride and int(stride) > 0 else 1
    one_stream = np.arange(n_loaded, dtype=np.int64) * step
    if step == 1 or len(paths) < 2:
        return one_stream
    frames: list[np.ndarray] = []
    offset = 0
    for path in paths:
        length = _frames_in(path, top)
        if length is None:
            logger.warning(
                "Could not read how many frames %s holds, so the %d files are "
                "taken as one stream strided by %d. With several files that "
                "is only right where every file's length is a multiple of the "
                "stride.", path.name, len(paths), step)
            return one_stream
        frames.append(offset + np.arange(0, length, step, dtype=np.int64))
        offset += length
    joined = np.concatenate(frames)
    if joined.size != n_loaded:
        logger.warning(
            "The %d files hold %d frames at stride %d by their own lengths, "
            "and %d were loaded, so they are taken as one stream.",
            len(paths), joined.size, step, n_loaded)
        return one_stream
    return joined


def _with_a_real_clock(
    trajectory: md.Trajectory,
    paths: list[Path],
    saving_interval_ps: float | None,
    stride: int | None,
    written: np.ndarray | None = None,
) -> md.Trajectory:
    """The run's own clock, or none at all -- never the frame index.

    A DCD read through MDTraj comes back with ``time`` equal to the frame
    index in picoseconds (see ``FORMATS_THAT_CARRY_A_CLOCK``). Every
    time-series figure takes its x axis from ``time`` and labels it
    "Time (ns)", so on a 100 ns run saved every 50 ps the axis reads 0-2 ns
    and says nothing about being wrong. Audited across every run on one
    workstation: 75 of 75 trajectories carried a fabricated clock, understated
    by up to 50x on production runs and *overstated* five-fold on short ones,
    because the interval crosses 1 ps somewhere between the two.

    Worse than the axis, ``analysis/reweight.py`` places each frame in a
    metadynamics deposition history by comparing this clock against PLUMED's,
    which is real. On a 100 ns run every frame is then matched against only
    the hills laid in the first 2 ns, so the reweighting under-corrects a
    fully biased ensemble -- and unlike the failure that module's docstring
    describes, this one leaves the effective sample size looking healthy and
    raises no complaint.

    Three cases, and the third is the point:

    * The format carries a clock and it varies -- trust it, touch nothing.
    * The interval is known -- build the clock from it. Frame ``k`` was
      written at ``(k + 1) * interval`` because a reporter fires after its
      first interval, not at step zero. ``written`` gives ``k`` for each
      loaded frame (:func:`written_frames`); without it, every
      ``stride``-th frame of one stream.
    * Neither -- **fill ``time`` with NaN.** A frame axis is honest and a
      wrong nanosecond axis is not, and NaN is the one marker that survives
      slicing, joining and ``atom_slice`` (checked, not assumed), so it still
      says "no clock" by the time a figure asks. An arbitrary attribute does
      not survive ``traj[a:b]``, which every analysis does.

    Note that the guard already in ``frame_axis`` cannot stand in for this.
    It asks whether the clock *varies*; a frame index varies perfectly, and
    ``np.allclose`` against NaN is False, so a NaN clock would read as usable.
    Both readings are fixed there by requiring the values to be finite.
    """
    n = trajectory.n_frames
    if n == 0:
        return trajectory

    suffixes = {p.suffix.lower() for p in paths}
    time = getattr(trajectory, "time", None)
    carries_its_own = (
        bool(suffixes) and suffixes <= FORMATS_THAT_CARRY_A_CLOCK
        and time is not None and len(time) > 1
        and bool(np.all(np.isfinite(time)))
        and not bool(np.allclose(time, time[0]))
    )
    if carries_its_own:
        return trajectory

    if saving_interval_ps and float(saving_interval_ps) > 0:
        step = int(stride) if stride and int(stride) > 0 else 1
        interval = float(saving_interval_ps)
        if written is None or len(written) != n:
            written = np.arange(n, dtype=np.float64) * step
        trajectory.time = (
            (np.asarray(written, dtype=np.float64) + 1.0) * interval
        ).astype(np.float32)
        logger.info(
            "Time axis set from the run's own record: %.4g ps between saved "
            "frames%s, so %d frames span %.4g ns. The file format does not "
            "carry this and MDTraj would otherwise report one picosecond per "
            "frame.",
            interval, "" if step == 1 else f" x stride {step}",
            n, float(trajectory.time[-1]) / 1000.0,
        )
        return trajectory

    trajectory.time = np.full(n, np.nan, dtype=np.float32)
    logger.warning(
        "No saving interval is recorded for this trajectory and %s does not "
        "carry one, so there is no way to know how much simulated time a "
        "frame represents. Time-series figures will be plotted against frame "
        "number rather than against a nanosecond axis that would be invented. "
        "Pass saving_interval_ps to label them in time.",
        ", ".join(sorted(suffixes)) or "the format",
    )
    return trajectory


def load_trajectory(
    traj: TrajectoryInput,
    top: PathLike | None = None,
    *,
    stride: int | None = None,
    first: int | None = None,
    last: int | None = None,
    saving_interval_ps: float | None = None,
) -> md.Trajectory:
    """Load one or more trajectory files into a single MDTraj trajectory.

    Parameters
    ----------
    traj : path, list of paths, or glob pattern
        Trajectory file(s) to load. When multiple files are provided, they
        are concatenated in lexicographic order (so name them ``run01.dcd``,
        ``run02.dcd``, ... for multi-shot data).
    top : path, optional
        Topology file. If omitted, the function attempts to auto-resolve
        from a sibling .pdb file.
    stride : int, optional
        Read every ``stride``-th frame.
    first, last : int, optional
        Frame slice applied after loading. ``last`` is exclusive (Python
        slice semantics).
    saving_interval_ps : float, optional
        Picoseconds of simulated time between saved frames. Required to put
        a trajectory in a format that does not carry its own clock -- DCD
        above all -- on a real time axis; without it such a trajectory is
        given a NaN clock and every time series is drawn against frame
        number. Ignored for formats that carry the clock themselves.

    Returns
    -------
    mdtraj.Trajectory
        A single trajectory with the requested topology attached. Always
        the concatenation of all input files.

    Raises
    ------
    TrajectoryLoadError
        If files are missing, topology cannot be resolved, or MDTraj fails
        to parse the data.

    Examples
    --------
    Single trajectory with auto-resolved topology::

        traj = load_trajectory("production.dcd")  # needs production.pdb

    Multiple trajectories with explicit topology::

        traj = load_trajectory(
            ["run01.dcd", "run02.dcd", "run03.dcd"],
            top="topology.pdb",
        )

    Glob pattern with stride::

        traj = load_trajectory("run*.dcd", top="topology.pdb", stride=10)
    """
    traj_paths = _resolve_paths(traj)
    for p in traj_paths:
        if not p.exists():
            raise TrajectoryLoadError(f"Trajectory file not found: {p}")

    top_path = _resolve_topology(traj_paths, top)
    # Insertion codes, which MDTraj does not keep: trypsin's 184A and 184
    # otherwise load as two residues numbered 184 in one chain. Read from
    # whichever file gives the topology, the trajectory itself where it is a
    # PDB or an mmCIF file.
    from fastmdxplora.analysis.residues import remember_insertion_codes

    remember_insertion_codes(top_path if top_path is not None else traj_paths[0])

    logger.debug(
        "Loading %d trajectory file(s) with topology=%s, stride=%s",
        len(traj_paths),
        top_path,
        stride,
    )

    # MDTraj's DCD/PDB plugins write raw C-level messages straight to the
    # OS file descriptors ("dcdplugin) detected standard 32-bit DCD
    # file..."), bypassing Python's logging and stream objects. Redirect
    # fds 1 and 2 around the load to suppress them.
    from fastmdxplora.utils import suppress_native_output

    try:
        with suppress_native_output():
            if top_path is not None:
                trajectory = md.load(
                    [str(p) for p in traj_paths],
                    top=str(top_path),
                    stride=stride,
                )
            else:
                trajectory = md.load([str(p) for p in traj_paths], stride=stride)
    except Exception as exc:  # MDTraj raises a variety of types
        raise TrajectoryLoadError(
            f"MDTraj failed to load trajectory: {exc}"
        ) from exc

    # MDTraj returns a list for a single file; coerce to a Trajectory.
    if isinstance(trajectory, list):
        trajectory = md.join(trajectory)

    trajectory = _made_whole(trajectory)
    trajectory = _with_one_clock(trajectory, len(traj_paths))
    # Last, so it wins: where the interval is known it supersedes both what
    # the file said and what the seam-mender inferred, and where it is not
    # known the clock is marked absent rather than left as a frame index.
    written = (written_frames(traj_paths, top_path, stride, trajectory.n_frames)
               if saving_interval_ps else None)
    trajectory = _with_a_real_clock(
        trajectory, traj_paths, saving_interval_ps, stride, written)

    if first is not None or last is not None:
        n = trajectory.n_frames
        f = first if first is not None else 0
        l = last if last is not None else n
        if not (0 <= f <= l <= n):
            raise TrajectoryLoadError(
                f"Invalid frame slice [{f}:{l}] for trajectory with {n} frames."
            , code="analysis.option.out_of_range")
        trajectory = trajectory[f:l]

    logger.debug(
        "Loaded trajectory: %d frames, %d atoms, %d residues",
        trajectory.n_frames,
        trajectory.n_atoms,
        trajectory.n_residues,
    )
    return trajectory
