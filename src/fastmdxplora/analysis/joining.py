"""Putting the pieces back together, and refusing where they do not fit.

A segmented run leaves one directory per segment, which is right:
appending into a single file would leave a crashed segment's half-written
frames in the middle of the run's own output with no way to tell which
were good. So joining is a separate, explicit step, and the joined
trajectory is a derived artefact that looks like one.

What makes this worth a module rather than a call to ``mdconvert`` is
everything it refuses.

**A gap.** Segment three missing between two and four does not produce a
shorter trajectory. It produces a trajectory with a discontinuity in the
middle that every analysis downstream will read straight through:
equilibration detection will find a transient that is really a jump, and
a correlation time computed across it is meaningless. A gap is not a
smaller run, it is a different and silently wrong one.

**A segment that did not finish.** Its checkpoint has no seal, which is
the same marker the resume path reads. A run whose fourth segment was
killed has four directories and three usable ones.

**Segments from different studies.** Two runs of the same length under
different settings leave directories that look alike and concatenate
without complaint. The resolved config of each says which study it was,
and they must agree.

The refusals are the point. Concatenation itself is four lines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import MissingResultError, StudyError

__all__ = ["SegmentPiece", "survey_segments", "join_segments",
           "joins_beside"]


@dataclass(frozen=True)
class SegmentPiece:
    """One segment's output, as found on disk."""

    index: int
    directory: Path
    trajectory: Path | None
    finished: bool
    config_digest: str

    @property
    def usable(self) -> bool:
        return self.finished and self.trajectory is not None


def _config_digest(directory: Path, *, _depth: int = 0) -> str:
    """What study this segment was, from its own resolved config.

    Reads the settings that define the study rather than hashing the file:
    the resolved config differs between segments by design -- production
    steps, minimize, resume_from -- so hashing it whole would say every
    segment came from a different study, which is exactly backwards.

    ``directory`` is the segment directory. A run writes its resolved
    config at the root of its output directory, which for a segment is
    ``segment-NNN/resolved_config.yml``; the simulation subdirectory is
    looked in as well because that is where a hand-assembled campaign
    tends to put it.
    """
    import hashlib

    for candidate in (directory / "resolved_config.yml",
                      directory / "simulation" / "resolved_config.yml"):
        if candidate.is_file():
            resolved = candidate
            break
    else:
        return ""
    try:
        import yaml

        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable config is not a study
        return ""

    simulation = dict(data.get("simulation") or {})
    # A segment that reuses another study's prepared system IS that study:
    # the same solvated box, the same water placement, the same atoms, on
    # disk. Its identity is the parent's, which is a stronger statement
    # than any comparison of settings -- and true where a comparison is
    # not, since resolving a config materialises defaults unevenly and a
    # ligand name can appear for a study that has no ligand.
    reuses = simulation.get("prepared_from") or simulation.get("setup_from")
    if reuses and _depth < 4:
        parent = Path(str(reuses))
        if parent.is_dir() and parent.resolve() != directory.resolve():
            inherited = _config_digest(parent, _depth=_depth + 1)
            if inherited:
                return inherited
    for varies_by_design in ("production_steps", "duration_ns", "minimize",
                             "nvt_steps", "npt_steps", "resume_from",
                             "nvt_duration_ns", "npt_duration_ns",
                             # A continuation states the ensemble the parent
                             # left implicit, and names the parent it reuses
                             # the prepared system from. Both say the two are
                             # the same study rather than different ones.
                             "ensemble", "setup_from", "prepared_from"):
        simulation.pop(varies_by_design, None)

    def decided(block: Any) -> dict[str, Any]:
        """What was decided. A setting absent and a setting explicitly
        null are the same decision; resolving a config materialises some
        defaults and not others, and a digest that called those two
        studies different would refuse to join a run to its own
        continuation."""
        return {k: v for k, v in dict(block or {}).items() if v is not None}

    identity = {"setup": decided(data.get("setup")),
                "systems": data.get("systems"),
                "simulation": decided(simulation)}
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def survey_segments(root: Path | str, *,
                    trajectory_name: str = "production.dcd") -> list[SegmentPiece]:
    """What is on disk, without judging whether it joins.

    Separated from :func:`join_segments` so a caller can see the state of
    a campaign without committing to producing a file from it -- which is
    what somebody coming back to a run that stopped overnight actually
    wants first.
    """
    from fastmdxplora.simulation.runner import CHECKPOINT_DIGEST_SUFFIX

    base = Path(root)
    pieces: list[SegmentPiece] = []
    # A study that ran in one piece and was then extended keeps its first
    # trajectory where it wrote it. Its own simulation/ is segment zero --
    # the index a campaign's first segment has -- and the extensions are
    # segment-001 onward, so nothing a run wrote is moved to make the
    # numbering tidy. A campaign that has its own segment-000 is untouched.
    own = base / "simulation"
    if own.is_dir() and not (base / "segment-000").is_dir():
        own_trajectory = own / trajectory_name
        if own_trajectory.is_file():
            own_seal = (own / "checkpoint.chk").with_suffix(
                ".chk" + CHECKPOINT_DIGEST_SUFFIX)
            pieces.append(SegmentPiece(
                index=0, directory=base, trajectory=own_trajectory,
                finished=own_seal.is_file(), config_digest=_config_digest(base)))
    for directory in sorted(base.glob("segment-*")):
        if not directory.is_dir():
            continue
        try:
            index = int(directory.name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        simulation_dir = directory / "simulation"
        trajectory = simulation_dir / trajectory_name
        seal = (simulation_dir / "checkpoint.chk").with_suffix(
            ".chk" + CHECKPOINT_DIGEST_SUFFIX)
        pieces.append(SegmentPiece(
            index=index,
            directory=directory,
            trajectory=trajectory if trajectory.is_file() else None,
            finished=seal.is_file(),
            config_digest=_config_digest(directory),
        ))
    return pieces


def join_segments(
    root: Path | str,
    destination: Path | str,
    *,
    topology: Path | str | None = None,
    trajectory_name: str = "production.dcd",
    keep_frames: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Concatenate a segmented run into one trajectory, or refuse.

    Returns a record of what was joined, which belongs beside the output:
    a joined trajectory is derived, and a reader should be able to see how
    many pieces it came from and which segments they were without opening
    it.

    Raises
    ------
    StudyError, MissingResultError
        On a gap, an unfinished segment, or segments that are not from the
        same study. Each is a case where concatenating would succeed and
        produce something wrong.
    """
    base = Path(root)
    pieces = survey_segments(base, trajectory_name=trajectory_name)
    if not pieces:
        raise MissingResultError(
            f"No segment directories under {base}. A segmented run writes "
            "one per segment; if this study ran in a single piece its "
            "trajectory is already whole and does not need joining.",
            code="analysis.data.absent", path=str(base))

    # Gaps first, because a gap is the failure that most looks like
    # success: the pieces either side concatenate perfectly.
    found = sorted(p.index for p in pieces)
    expected = list(range(found[0], found[-1] + 1))
    missing = sorted(set(expected) - set(found))
    if missing or found[0] != 0:
        gap = missing or [0]
        raise StudyError(
            f"Segments {gap} are missing from {base}, so joining what is "
            "here would produce a trajectory with a jump in the middle "
            "rather than a shorter one. Every analysis downstream reads "
            "straight through that: equilibration detection would find a "
            "transient that is really a discontinuity, and a correlation "
            "time computed across it means nothing.",
            code="analysis.data.absent",
            needs=f"segments {gap}")

    limits = dict(keep_frames or {})
    # A segment that was killed holds frames written after its last
    # checkpoint -- the frames a resume runs again. Given a frame limit
    # for it, those frames are left out and the pieces meet exactly at
    # the checkpoint; without one, the overlap has nowhere to go and the
    # join refuses as before.
    unfinished = [p.index for p in pieces if not p.finished and p.index not in limits]
    if unfinished:
        raise MissingResultError(
            f"Segments {unfinished} have no sealed checkpoint, so they did "
            "not finish. Rerun them before joining; a run that stopped "
            "partway has a trajectory that ends wherever the process died, "
            "and nothing in the file says so.",
            code="simulation.resume.unsealed",
            path=str(base))

    without_trajectory = [p.index for p in pieces if p.trajectory is None]
    if without_trajectory:
        raise MissingResultError(
            f"Segments {without_trajectory} have no {trajectory_name}. "
            "A segment that finished without writing frames was configured "
            "not to, and joining would silently skip its time.",
            code="analysis.data.absent", path=str(base))

    digests = {p.config_digest for p in pieces if p.config_digest}
    if len(digests) > 1:
        raise StudyError(
            f"The segments under {base} are not all from the same study: "
            f"{len(digests)} different sets of settings. Two runs of the "
            "same length under different settings leave directories that "
            "look alike and concatenate without complaint.",
            code="analysis.data.absent",
            needs="segments from one study")

    import mdtraj

    topology_path = Path(topology) if topology else (
        pieces[0].directory / "simulation" / "topology.pdb")
    if not topology_path.is_file():
        raise MissingResultError(
            f"No topology at {topology_path}, and a trajectory cannot be "
            "read without one.",
            code="analysis.data.absent", path=str(topology_path))

    out = Path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = 0
    # Where each segment starts, in frames of the joined file. Without this
    # the join record is a list of segment numbers that no analysis can
    # locate, and `summarise_segments` has nothing to split on. It is the
    # only thing the joined file cannot be asked for afterwards.
    joins: list[int] = []
    with mdtraj.formats.DCDTrajectoryFile(str(out), "w") as writer:
        for piece in pieces:
            if piece.index != pieces[0].index:
                joins.append(frames)
            allowed = limits.get(piece.index)
            written_here = 0
            for chunk in mdtraj.iterload(str(piece.trajectory),
                                         top=str(topology_path), chunk=500):
                if allowed is not None:
                    if written_here >= allowed:
                        break
                    if written_here + len(chunk) > allowed:
                        chunk = chunk[: allowed - written_here]
                written_here += len(chunk)
                writer.write(chunk.xyz * 10.0,
                             cell_lengths=(chunk.unitcell_lengths * 10.0
                                           if chunk.unitcell_lengths is not None
                                           else None),
                             cell_angles=chunk.unitcell_angles)
                frames += chunk.n_frames

    record = {
        "trajectory": str(out),
        "segments": [p.index for p in pieces],
        "frames": frames,
        # What was left out, and from where. A joined trajectory is
        # derived; a reader should be able to see that a killed segment
        # was cut back to its checkpoint without opening the file.
        "trimmed": {str(index): count for index, count in sorted(limits.items())},
        "joins": joins,
        "topology": str(topology_path),
        # Stated so a reader knows this file is derived without having to
        # infer it from the directory it sits in.
        "joined_from_segments": True,
        "ran_through": False,
    }
    (out.with_suffix(out.suffix + ".join.json")).write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    return record


def joins_beside(trajectory: Path | str) -> list[int]:
    """Where the segments of this trajectory begin, or an empty list.

    :func:`join_segments` leaves a record beside its output. This reads it,
    so a caller holding a trajectory path can find out whether it was
    joined without having to have been told.

    Empty for a trajectory that went through in one piece, which means a
    caller can pass the result straight to
    :func:`fastmdxplora.statistics.summarise_segments` without branching on
    whether a run was segmented.
    """
    path = Path(trajectory)
    record = path.with_suffix(path.suffix + ".join.json")
    if not record.is_file():
        return []
    try:
        return [int(frame)
                for frame in json.loads(record.read_text(encoding="utf-8")
                                        ).get("joins", [])]
    except (ValueError, TypeError):
        # An unreadable record is treated as absent rather than repaired.
        # Guessing at where the joins were would put an invented number
        # into the decision about how to read the run.
        return []
