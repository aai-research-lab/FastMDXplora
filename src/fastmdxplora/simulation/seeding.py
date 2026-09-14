"""Starting structures for umbrella windows, taken from a steered pull.

A window is a restraint and a starting point, and only the restraint is
usually written down. Start every window from the same bound structure and
the ones near a barrier will not cross it: the restraint pulls one way, the
free energy pulls the other, and where both minima are reachable the window
settles in whichever one it began in. The histograms then sit somewhere
other than where the restraint says they are, and two neighbours that were
planned 0.07 nm apart end up 0.25 nm apart with nothing shared between them.
The recombination refuses, correctly, and a day of sampling says only that
the windows were badly started.

Steered MD is the standard answer and this module is the wiring. Pull once
along the same collective variable, take the frame whose measured value is
closest to each window's centre, and every window begins on the correct side
of whatever it would not have crossed. That is what steered MD is good for --
this package has said so in `simulation/steered.py` since before umbrella
sampling existed here -- and saying so in a docstring is not the same as
doing it for the user.

Two things this module refuses rather than guesses.

The collective variable is recomputed from the trajectory rather than read
from the pull's ``COLVAR``, because a COLVAR row and a trajectory frame are
written on different strides and aligning them by index is the kind of
off-by-one that produces a plausible seed at the wrong distance. Where a
COLVAR is present it is used as a check: the two series must agree, and a
disagreement means the selections here are not the selections PLUMED biased,
which is worth stopping for.

Molecules are made whole before any position is taken. Frames are stored
wrapped, and a ligand split across the periodic boundary is a set of
coordinates that looks fine and holds a bond several nanometres long. Every
seed's potential energy is measured against the prepared system's, and a
seed that is wildly worse is refused with its number rather than handed to a
simulation that would explode on the first step.
"""

from __future__ import annotations

import logging
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable
from fastmdxplora.refusals import MissingResultError

logger = logging.getLogger(__name__)

#: How much worse than the prepared system a seed's potential energy may be
#: before it is refused, per atom. Generous: a frame pulled 1.6 nm through
#: water is genuinely strained relative to the bound state, and the check is
#: here to catch a split molecule or a mismatched topology -- failures that
#: are orders of magnitude, not tens of percent.
ENERGY_TOLERANCE_KJMOL_PER_ATOM = 5.0

#: How far a recomputed collective variable may sit from the pull's own
#: record before the two are called different quantities.
COLVAR_AGREEMENT_NM = 0.02


@dataclass(frozen=True)
class Seed:
    """One window's starting point, and where it came from."""

    index: int
    centre: float
    frame: int
    measured: float
    directory: str

    def as_record(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "centre": self.centre,
            "frame": self.frame,
            "measured": self.measured,
            "away_by": abs(self.measured - self.centre),
            "directory": self.directory,
        }


# ---------------------------------------------------------------------------
# Reading the pull
# ---------------------------------------------------------------------------
def read_colvar(path: Path | str) -> tuple[np.ndarray, np.ndarray] | None:
    """``(time, cv)`` from a PLUMED COLVAR, or ``None`` where there is none.

    Columns are found by name from the ``#! FIELDS`` header rather than by
    position, because a run that also biases something else writes more
    columns and the second one stops being ``cv``.
    """
    path = Path(path)
    if not path.is_file():
        return None

    fields: list[str] = []
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#!"):
            parts = line.split()
            if len(parts) > 2 and parts[1] == "FIELDS":
                fields = parts[2:]
            continue
        if not line.strip():
            continue
        try:
            rows.append([float(v) for v in line.split()])
        except ValueError:
            continue

    if not fields or not rows or "cv" not in fields or "time" not in fields:
        return None
    width = len(fields)
    kept = [r for r in rows if len(r) == width]
    if not kept:
        return None
    table = np.asarray(kept, dtype=float)
    return table[:, fields.index("time")], table[:, fields.index("cv")]


def shortest_vector(delta: np.ndarray, cell: np.ndarray) -> np.ndarray:
    """`delta` reduced to the nearest periodic image, per frame.

    Frames are stored wrapped, so the ligand and the pocket routinely sit in
    different images and the raw difference of their centres is a distance
    across the box rather than between the molecules. Left unreduced, a held
    window whose true separation never leaves 0.3-0.5 nm reports values above
    8 nm in a box only 5.8 nm wide -- a number that cannot be a distance, and
    that is nevertheless the mean of nothing and the seed of a window.

    This is the same hazard the cross-tool benchmark documents, where one
    contact pair read 62.32 A raw and 3.70 A under minimum image. PLUMED
    applies the convention; anything checking PLUMED has to as well.

    The fractional reduction alone is not shortest in a skewed cell, so the
    twenty-six neighbouring translations are compared against it. A rhombic
    dodecahedron is skewed. `test_seeding` checks this against an exhaustive
    search over several cells, because a reduction that is quietly wrong
    gives distances that look entirely reasonable.
    """
    cell = np.asarray(cell, dtype=float)
    matrix = cell.transpose(0, 2, 1)          # columns are the cell vectors
    fractional = np.einsum("fij,fj->fi", np.linalg.inv(matrix), delta)
    fractional -= np.round(fractional)
    # `base` stays fixed and every candidate is measured from it. Updating it
    # inside the loop turned the scan into a greedy walk: each shift was
    # applied to whatever had won so far, so lattice points were skipped and
    # others visited twice. It returned a vector 2.42 nm too long for 17 of
    # 3000 sampled points, and the winning translation in the case examined
    # was (0, 0, -1) -- inside the search the whole time, never compared
    # against the right thing.
    base = np.einsum("fij,fj->fi", matrix, fractional)
    best = base.copy()
    shortest = np.linalg.norm(best, axis=1)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for k in (-1, 0, 1):
                if (i, j, k) == (0, 0, 0):
                    continue
                shift = np.array([i, j, k], dtype=float)
                candidate = base + np.einsum("fij,j->fi", matrix, shift)
                length = np.linalg.norm(candidate, axis=1)
                closer = length < shortest
                best[closer] = candidate[closer]
                shortest[closer] = length[closer]
    return best


def the_two_groups(topology: Any, ligand_resname: str,
                   site_selection: str) -> tuple[np.ndarray, np.ndarray]:
    """``(ligand, site)`` atom indices, or a refusal naming what was missing.

    Refused here rather than inside MDTraj. An empty expression reaches
    `topology.select` as a pyparsing failure -- "Expected '=~' operations (at
    char 0), (line:1, col:1)" -- which names a character position in a string
    the user never wrote and says nothing about which key was missing. A whole
    pull was lost to that message. The check below for a selection matching
    *no atoms* never got the chance to run, because a selection that is not an
    expression at all fails earlier.
    """
    if not str(site_selection).strip():
        raise StudyError(
            "No site selection reached the seeder, so there is nothing to "
            "measure the ligand against. The umbrella block needs "
            "`select_atoms` -- or the role name `site_selection` -- naming "
            'the site, for example `select_atoms: "resSeq 189 to 195 and '
            'name CA"`.'
        , code="simulation.cv.selection_empty")
    if not str(ligand_resname).strip():
        raise StudyError(
            "No ligand name reached the seeder, so there is nothing to "
            "measure from. Give the umbrella block `ligand_name` (or "
            "`ligand_resname`), for example `ligand_name: BEN`."
        , code="simulation.cv.selection_empty")
    ligand = topology.select(f"resname {ligand_resname}")
    site = topology.select(site_selection)
    if ligand.size == 0:
        raise StudyError(
            f"No atom matches `resname {ligand_resname}` in the pull's "
            "topology, so there is no ligand to measure from."
        , code="simulation.cv.selection_empty")
    if site.size == 0:
        raise StudyError(
            f"No atom matches `{site_selection}` in the pull's topology, so "
            "there is no site to measure to."
        , code="simulation.cv.selection_empty")
    return ligand, site


def centres_of(trajectory: Any, selection: np.ndarray) -> np.ndarray:
    """The mass-weighted centre of `selection` in every frame, in nm.

    Mass-weighted, which is what PLUMED's ``COM`` is. An unweighted centroid
    differs by a few hundredths of a nanometre on a ligand with a heavy ring,
    which is a third of a window spacing.
    """
    selection = np.asarray(selection, dtype=int)
    topology = trajectory.topology
    weight = np.array(
        [getattr(topology.atom(int(i)).element, "mass", 0.0) or 0.0
         for i in selection], dtype=float)
    # A group of virtual sites or of elements MDTraj could not assign has no
    # mass to weight by. Its centroid is still a position; a division by zero
    # is not.
    if weight.sum() <= 0.0:
        weight = np.ones_like(weight)
    return (trajectory.xyz[:, selection, :]
            * weight[None, :, None]).sum(axis=1) / weight.sum()


def separation(trajectory: Any, of: np.ndarray, from_: np.ndarray
               ) -> np.ndarray:
    """The vector from one group's centre to another's, per frame, in nm.

    Reduced to the minimum image, because the frames are wrapped. Where the
    trajectory carries no box this falls back to the raw difference and says
    so, since a structure without periodicity has no images to choose from.
    """
    delta = centres_of(trajectory, of) - centres_of(trajectory, from_)
    if trajectory.unitcell_vectors is None:
        logger.info("No box on this trajectory, so the centres are compared "
                    "as they are stored.")
        return delta
    return shortest_vector(delta, trajectory.unitcell_vectors)


def measure_along(trajectory: Any, ligand_resname: str,
                  site_selection: str) -> np.ndarray:
    """The ligand-to-site centre distance in every frame, in nm."""
    ligand, site = the_two_groups(trajectory.topology, ligand_resname,
                                  site_selection)
    return np.linalg.norm(separation(trajectory, ligand, site), axis=1)


def frames_for_centres(measured: np.ndarray,
                       centres: list[float]) -> list[tuple[int, float]]:
    """The frame closest to each centre, as ``(frame, measured)``.

    Frames may repeat where a pull moved slowly through a region and two
    centres are nearer to each other than to any other frame; that is a
    statement about the pull's resolution and is reported by the caller
    rather than silently fixed by shifting a window.
    """
    return [(int(np.argmin(np.abs(measured - c))), float(
        measured[int(np.argmin(np.abs(measured - c)))])) for c in centres]


# ---------------------------------------------------------------------------
# The direction the ligand leaves by
# ---------------------------------------------------------------------------
#: Nearer than this, the two centres are within a ligand's own radius of each
#: other and "the direction to the ligand" is a statement about its rattling
#: rather than about the path. Those frames are left out of the axis search
#: and reported separately.
MINIMUM_FOR_A_DIRECTION_NM = 0.25

#: How many candidate axis groups to try. Each is the *n* residues lying most
#: nearly opposite the exit, and the one that holds the path in the smallest
#: measured angle wins -- so the size of the group is a result rather than a
#: constant somebody picked.
GROUP_SIZES: tuple[int, ...] = (4, 6, 8, 12, 16, 24, 32, 48)

#: How far from the site a residue may sit and still anchor the axis. Nearer
#: than the first, it is in the pocket and moves with what happens there;
#: further than the second, it is across the protein and its direction from
#: the site says more about the protein's shape than about the way out.
AXIS_BAND_NM: tuple[float, float] = (0.4, 2.0)

#: Past this the cap is most of the sphere and the wall is doing nothing a
#: study can rely on -- at 120 degrees it holds the ligand out of a quarter of
#: the directions, and the correction it earns is under 1 kJ/mol. A number
#: this large is a result about the axis rather than about the ligand, so it
#: is refused with its cause rather than clipped to something that looks like
#: a cone.
WIDEST_A_CONE_IS_WORTH_DEG = 120.0


def _rotations_onto_the_first(frames: np.ndarray) -> np.ndarray:
    """Per-frame rotation carrying each frame's atoms onto the first frame's.

    Kabsch, done here rather than through `mdtraj.Trajectory.superpose`,
    because superposing rewrites every coordinate of a trajectory that may be
    tens of thousands of atoms and this needs a 3x3 matrix. The rotation is
    then applied to the one vector that matters.
    """
    frames = np.asarray(frames, dtype=float)
    reference = frames[0] - frames[0].mean(axis=0)
    moving = frames - frames.mean(axis=1, keepdims=True)
    covariance = np.einsum("fai,aj->fij", moving, reference)
    left, _, right = np.linalg.svd(covariance)
    # Without this the best "rotation" of a nearly flat set of atoms can come
    # out a reflection, which superposes beautifully and mirrors the ligand
    # onto the wrong side of the protein.
    handedness = np.sign(np.linalg.det(covariance))
    handedness[handedness == 0.0] = 1.0
    turned = np.transpose(right, (0, 2, 1)).copy()
    turned[:, :, 2] *= handedness[:, None]
    return turned @ np.transpose(left, (0, 2, 1))


def path_along(trajectory: Any, ligand_resname: str, site_selection: str, *,
               frame_selection: str = "protein and not element H"
               ) -> tuple[np.ndarray, np.ndarray]:
    """How far the ligand is from the site, and which way, per frame.

    The distance is in nm and the direction is a unit vector *in the
    protein's frame* -- each frame rotated onto the first, so that a direction
    means the same thing in all of them. Without that the protein's own
    tumbling is written into every angle, and a ligand sitting still in its
    pocket appears to swing about the site.

    **Order matters.** The separation is reduced to the minimum image first
    and rotated afterwards. Superposing rotates the coordinates and leaves the
    box vectors behind, so a minimum image taken after alignment is taken in a
    box that no longer matches the molecule -- which is how a measurement of
    this came back 126 degrees off axis, a direction that would have put the
    ligand inside the protein.

    Molecules are expected whole: a ligand split across the boundary has a
    centre of mass halfway across the box. `seed_windows` images the
    trajectory before anything is measured from it.
    """
    topology = trajectory.topology
    ligand, site = the_two_groups(topology, ligand_resname, site_selection)
    frame_atoms = topology.select(frame_selection)
    if frame_atoms.size < 3:
        raise StudyError(
            f"`{frame_selection}` matched {frame_atoms.size} atom(s), and "
            "three are needed to fix a frame to rotate into. Without one, a "
            "direction from the site is measured in the laboratory's frame "
            "and carries the protein's tumbling with it."
        , code="simulation.cv.selection_arity")

    away = separation(trajectory, ligand, site)
    rotation = _rotations_onto_the_first(trajectory.xyz[:, frame_atoms, :])
    turned = np.einsum("fij,fj->fi", rotation, away)
    distance = np.linalg.norm(turned, axis=1)
    return distance, turned / np.where(distance == 0.0, 1.0, distance)[:, None]


def atoms_opposite(trajectory: Any, site_selection: str, axis: np.ndarray, *,
                   count: int = 12, band_nm: tuple[float, float] = AXIS_BAND_NM,
                   frame: int = 0) -> list[int]:
    """The CA atoms lying most nearly opposite `axis`, seen from the site.

    PLUMED's cone is an angle at the site between an atom group and the
    ligand, and the wall holds that angle *open* -- so the group has to sit
    behind the site, on the far side from the way out. These are the residues
    that do, nearest first.

    Alpha carbons, and only those within a couple of nanometres: a compact
    group of backbone atoms moves with the fold rather than with a side
    chain's rotamer, and it stays in one periodic image, which the whole
    protein does not.
    """
    topology = trajectory.topology
    axis = np.asarray(axis, dtype=float)
    axis = axis / max(float(np.linalg.norm(axis)), 1e-12)
    alpha = topology.select("name CA")
    if alpha.size == 0:
        raise StudyError(
            "No alpha carbon in this topology, so there is no backbone to "
            "anchor a cone's axis to. Name the group yourself with the "
            "cone's `axis_selection`.", code="simulation.cv.selection_empty")

    site = topology.select(site_selection)
    one = trajectory.slice(int(frame), copy=True)
    middle = centres_of(one, site)[0]
    towards = one.xyz[0, alpha, :] - middle[None, :]
    if one.unitcell_vectors is not None:
        towards = shortest_vector(
            towards, np.broadcast_to(one.unitcell_vectors[0],
                                     (towards.shape[0], 3, 3)).copy())
    length = np.linalg.norm(towards, axis=1)
    near, far = float(band_nm[0]), float(band_nm[1])
    within = (length >= near) & (length <= far)
    if int(within.sum()) < 3:
        raise StudyError(
            f"Only {int(within.sum())} alpha carbon(s) lie between {near} and "
            f"{far} nm of `{site_selection}`, and three are needed to fix an "
            "axis. Either the site selection is not on the protein, or the "
            "band wants widening.", code="simulation.cv.selection_empty")

    behind = -(towards[within] @ axis) / np.maximum(length[within], 1e-12)
    order = np.argsort(-behind)
    chosen = alpha[np.flatnonzero(within)[order[:max(int(count), 3)]]]
    return sorted(int(i) for i in chosen)


def angles_at_the_site(trajectory: Any, ligand: np.ndarray, site: np.ndarray,
                       axis_atoms: "list[int]") -> np.ndarray:
    """The angle PLUMED's cone will measure, in degrees, per frame.

    Not the angle to an ideal axis. The restraint is
    ``ANGLE ATOMS=cone_axis,site,lig`` against a *lower* wall, so what decides
    whether the wall bites is the angle between the ligand and the direction
    opposite this particular group of atoms -- which is close to the ideal
    axis and is not it. Measuring the thing that will be restrained means the
    half-angle does not need a fudge factor for the difference.
    """
    axis_atoms = np.asarray(list(axis_atoms), dtype=int)
    to_ligand = separation(trajectory, ligand, site)
    to_axis = separation(trajectory, axis_atoms, site)
    lengths = (np.linalg.norm(to_ligand, axis=1)
               * np.linalg.norm(to_axis, axis=1))
    cosine = -np.einsum("fi,fi->f", to_ligand, to_axis) \
        / np.maximum(lengths, 1e-12)
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))


def measure_the_cone(trajectory: Any, ligand_resname: str,
                     site_selection: str, *, force_constant: float = 5000.0,
                     keep: float = 98.0, margin: float = 1.2,
                     axis_selection: str | None = None) -> dict[str, Any]:
    """A cone read off the pull, as a record a `Cone` can be built from.

    Two numbers have to come from somewhere, and neither is a preference. The
    axis is the direction the ligand actually leaves by, and a pocket's way
    out is rarely the line from the protein's centre through it -- on the
    study this was written from, the two are 42 to 61 degrees apart. The
    half-angle has to be wide enough that the wall never touches the sampling
    and narrow enough that the shell is small, and what decides that is how
    much the path wanders, which is a property of the path.

    The pull is the one trajectory that has both. It runs from the site to
    bulk in one continuous piece, and every window is seeded from it, so a
    cone measured here is a cone every window starts inside.

    Done in three steps, because each is checkable on its own:

    1. the narrowest cone that holds the path, over four thousand directions
       -- a search, not an average, because a path that leaves a pocket
       sideways and then swings into bulk has a mean far from both ends;
    2. the group of backbone atoms lying opposite that direction, chosen by
       trying several sizes and keeping the one that does best at step 3;
    3. the angle *that group* gives, measured frame by frame exactly as
       PLUMED will measure it, widened by `margin`.

    Step 3 is why step 1 does not need to be exact. The ideal axis is a
    direction and the restraint is a group of atoms, and the difference
    between them is measured rather than allowed for.
    """
    from fastmdxplora.simulation.umbrella import narrowest_cone

    ligand, site = the_two_groups(trajectory.topology, ligand_resname,
                                  site_selection)
    distance, direction = path_along(trajectory, ligand_resname,
                                     site_selection)
    usable = distance >= MINIMUM_FOR_A_DIRECTION_NM
    if int(usable.sum()) < 10:
        raise StudyError(
            f"Only {int(usable.sum())} frame(s) of this pull have the ligand "
            f"more than {MINIMUM_FOR_A_DIRECTION_NM} nm from the site, so "
            "there is no path to read a direction from. It spans "
            f"{distance.min():.2f}-{distance.max():.2f} nm."
        , code="analysis.sampling.too_few_frames")

    if axis_selection:
        # The study named the group, so only the angle is measured. Worth
        # having: a site whose exit is known does not need searching for, and
        # the wall still gets a width that came from the trajectory.
        axis_atoms = [int(i) for i in
                      trajectory.topology.select(str(axis_selection))]
        if not axis_atoms:
            raise StudyError(
                f"The cone's `axis_selection` {axis_selection!r} matched no "
                "atoms, so there is no group to open the angle away from.", code="simulation.cv.selection_empty")
        ideal = -np.asarray(separation(
            trajectory, np.asarray(axis_atoms, dtype=int), site)[0],
            dtype=float)
        ideal = ideal / max(float(np.linalg.norm(ideal)), 1e-12)
        held_within = float(np.percentile(
            angles_at_the_site(trajectory, ligand, site, axis_atoms)[usable],
            float(keep)))
        named = str(axis_selection)
    else:
        ideal, _ = narrowest_cone(direction[usable], keep=float(keep))
        tried = []
        for size in GROUP_SIZES:
            group = atoms_opposite(trajectory, site_selection, ideal,
                                   count=size)
            angle = angles_at_the_site(trajectory, ligand, site, group)
            tried.append((float(np.percentile(angle[usable], float(keep))),
                          len(group), group))
        held_within, _, axis_atoms = min(tried, key=lambda t: (t[0], t[1]))
        named = _name_the_group(trajectory.topology, axis_atoms)

    half_angle = float(math.ceil(held_within * float(margin)))
    if half_angle > WIDEST_A_CONE_IS_WORTH_DEG:
        raise StudyError(
            f"The path only fits in a cone of {half_angle:.0f} degrees, which "
            "leaves the ligand free in most directions -- the wall would cost "
            "the study its stiffness and buy it nothing. "
            + (f"`axis_selection` is {axis_selection!r}: the angle opens away "
               "from that group, so it has to sit behind the site, on the far "
               "side from the way out. Leave it out and the axis is measured "
               "too."
               if axis_selection else
               "The pull leaves in no settled direction, so there is no cone "
               "to put round it. A ligand that leaves by several routes needs "
               "a coordinate that follows one of them rather than a wall "
               "around all of them.")
        , code="simulation.cone.too_narrow")
    measured = angles_at_the_site(trajectory, ligand, site, axis_atoms)

    # What the group of atoms actually points along, against the direction the
    # search asked for. Recorded rather than corrected for: the half-angle
    # above was measured through the group, so the difference is already in
    # it, and this says how much of the width is the group's doing.
    realised = -np.asarray(separation(
        trajectory, np.asarray(axis_atoms, dtype=int), site)[0], dtype=float)
    realised = realised / max(float(np.linalg.norm(realised)), 1e-12)
    off_by = float(np.degrees(np.arccos(
        np.clip(float(realised @ ideal), -1.0, 1.0))))

    near = distance <= (distance.min()
                        + 0.25 * (distance.max() - distance.min()))
    bound_end = near & usable
    return {
        "half_angle_deg": half_angle,
        "force_constant": float(force_constant),
        "axis_selection": named,
        "axis_atoms": [int(i) for i in axis_atoms],
        "keep": float(keep),
        "margin": float(margin),
        "held_within_deg": round(held_within, 2),
        "worst_deg": round(float(measured[usable].max()), 2),
        "bound_end_deg": (round(float(np.percentile(
            measured[bound_end], float(keep))), 2)
            if int(bound_end.sum()) else None),
        "axis_group_off_the_search_deg": round(off_by, 2),
        "frames": int(distance.size),
        "measured_from": int(usable.sum()),
        "spans_nm": [round(float(distance.min()), 3),
                     round(float(distance.max()), 3)],
    }


def _name_the_group(topology: Any, atoms: "list[int]") -> str:
    """A selection that picks out exactly these atoms, or a plain description.

    The selection is documentation -- `axis_atoms` is what the restraint uses
    -- so it is only written down when it is true. Residue numbers are not
    unique in every structure: 3PTB carries chymotrypsin numbering, where 184
    and 184A are different residues that `resSeq 184` matches together. A
    string that resolves to a different group than the one measured would be
    a lie in a record whose whole job is to say what ran.
    """
    numbers = sorted({int(topology.atom(int(i)).residue.resSeq) for i in atoms})
    selection = f"resSeq {' '.join(str(n) for n in numbers)} and name CA"
    try:
        resolved = sorted(int(i) for i in topology.select(selection))
    except Exception:  # noqa: BLE001 -- any parse failure means "cannot name"
        resolved = []
    if resolved == sorted(int(i) for i in atoms):
        return selection
    return (f"{len(atoms)} alpha carbons opposite the exit "
            f"(residues {numbers[0]}-{numbers[-1]})")


# ---------------------------------------------------------------------------
# Writing the seeds
# ---------------------------------------------------------------------------
def _prepared_files(prepared: Path) -> tuple[Path, Path]:
    """The system and topology of the preparation the windows will use.

    Resolved through the same search the simulation phase uses. A study is
    named by its output directory -- `runs/c1-benzamidine-pmf` -- and where
    the prepared system sits inside it is this package's layout, not the
    user's business. Taking the named path literally here meant the phase
    that runs a window and the code that seeds it disagreed about what
    `setup_from` pointed at, and the disagreement surfaced only after a
    two-and-a-half-hour pull had already finished.
    """
    from fastmdxplora.simulation.pipeline import (
        PREPARED_SYSTEM_LAYOUTS,
        where_a_prepared_system_sits,
    )

    prepared = where_a_prepared_system_sits(Path(prepared))
    system = prepared / "system.xml"
    topology = prepared / "topology.pdb"
    for path in (system, topology):
        if not path.is_file():
            looked = ", ".join(
                str(Path(prepared) / s) if s else str(prepared)
                for s in PREPARED_SYSTEM_LAYOUTS
            )
            raise MissingResultError(
                f"{path} is not there, so the seeds cannot be built from the "
                "same system the windows will simulate. Seeds written "
                "against a different preparation place the waters "
                "differently and will not load. Looked in: " + looked
            )
    return system, topology


def write_seeds(prepared: Path | str,
                trajectory: Any,
                chosen: list[tuple[int, float]],
                centres: list[float],
                destination: Path | str,
                *,
                temperature_K: float = 300.0,
                random_seed: int = 0) -> list[Seed]:
    """One prepared directory per window, each holding that window's start.

    The system and topology are copied unchanged from the pull's own
    preparation, so every window simulates the identical system and differs
    only in where it starts and what holds it. Velocities are drawn fresh
    from the Maxwell-Boltzmann distribution rather than carried from the
    pull: the pull's velocities are the velocities of a system being
    dragged, and starting equilibrium sampling from them biases the first
    picoseconds in the direction of the pull.
    """
    from openmm import (  # noqa: PLC0415  -- optional at import time
        Context, LangevinMiddleIntegrator, Platform, XmlSerializer, unit)

    # Resolved once, here, rather than inside each thing that reads a
    # file out of it. `_prepared_files` resolved it and returned the two
    # files without rebinding this name, so `_potential_of_prepared` was
    # still handed `runs/<study>` and looked for `state.xml` directly
    # beneath it. It is not there, and that function returns None rather
    # than guessing -- so the seed energy check, which exists to catch a
    # seed that will not load, silently did not run at all.
    from fastmdxplora.simulation.pipeline import where_a_prepared_system_sits

    prepared = where_a_prepared_system_sits(Path(prepared))
    destination = Path(destination)
    system_xml, topology_pdb = _prepared_files(prepared)

    system = XmlSerializer.deserialize(
        system_xml.read_text(encoding="utf-8"))
    if system.getNumParticles() != trajectory.n_atoms:
        missing = system.getNumParticles() - trajectory.n_atoms
        likely = (
            " That is about the number of solvent atoms in it, so the pull "
            "almost certainly saved a selection rather than the whole "
            "system -- `save_selection` defaults to \"not water\". A seed is "
            "a complete set of positions and cannot be built from a subset; "
            "borrowing the missing water from the prepared system would put "
            "bound-state solvent where the ligand has since moved. Re-run "
            "the pull with `save_selection: all`."
        ) if missing > 0 else ""
        raise StudyError(
            f"The prepared system has {system.getNumParticles()} particles "
            f"and the pull's trajectory has {trajectory.n_atoms}.{likely}"
        , code="simulation.seed.unusable")

    # One context for every window. Building a Reference context over tens of
    # thousands of particles takes seconds, and thirty of them takes minutes
    # for no reason: positions are all that changes.
    integrator = LangevinMiddleIntegrator(
        temperature_K * unit.kelvin, 1.0 / unit.picosecond,
        0.002 * unit.picoseconds)
    context = Context(system, integrator,
                      Platform.getPlatformByName("Reference"))

    reference = _potential_of_prepared(context, prepared, XmlSerializer, unit)

    whole = trajectory.image_molecules(inplace=False)

    seeds: list[Seed] = []
    for index, ((frame, measured), centre) in enumerate(zip(chosen, centres)):
        out = destination / f"window-{index:02d}"
        out.mkdir(parents=True, exist_ok=True)

        context.setPositions(whole.xyz[frame] * unit.nanometer)
        if whole.unitcell_vectors is not None:
            context.setPeriodicBoxVectors(
                *(whole.unitcell_vectors[frame] * unit.nanometer))
        context.setVelocitiesToTemperature(
            temperature_K * unit.kelvin, random_seed + index)

        state = context.getState(getPositions=True, getVelocities=True,
                                 getEnergy=True, enforcePeriodicBox=False)
        potential = state.getPotentialEnergy().value_in_unit(
            unit.kilojoule_per_mole)
        _refuse_an_impossible_seed(potential, reference,
                                   system.getNumParticles(), index, measured)

        (out / "state.xml").write_text(XmlSerializer.serialize(state),
                                       encoding="utf-8")
        shutil.copy2(system_xml, out / "system.xml")
        shutil.copy2(topology_pdb, out / "topology.pdb")

        # Asserted rather than assumed. A directory that is missing one of
        # the three loads as "no prepared system here" and the window
        # silently prepares its own, which is the failure this whole module
        # exists to avoid.
        for name in ("system.xml", "state.xml", "topology.pdb"):
            if not (out / name).is_file():
                raise BackendUnavailable(
                    f"{out / name} was not written, so window {index} has no "
                    "starting system.", code="simulation.seed.unusable")

        seeds.append(Seed(index=index, centre=float(centre), frame=int(frame),
                          measured=float(measured), directory=str(out)))

    return seeds


def _potential_of_prepared(context, prepared, XmlSerializer, unit
                           ) -> float | None:
    """The prepared system's own potential energy, for scale.

    Returns ``None`` where the prepared state cannot be read: the seeds are
    still written, and the check that needed a reference is skipped rather
    than replaced with a guess about what a reasonable energy would be.
    """
    state_xml = prepared / "state.xml"
    if not state_xml.is_file():
        return None
    try:
        state = XmlSerializer.deserialize(
            state_xml.read_text(encoding="utf-8"))
        context.setState(state)
        return context.getState(getEnergy=True).getPotentialEnergy(
        ).value_in_unit(unit.kilojoule_per_mole)
    except Exception as exc:  # pragma: no cover -- diagnostic only
        logger.debug("No reference energy from %s: %s", state_xml, exc)
        return None


def _refuse_an_impossible_seed(potential: float, reference: float | None,
                               particles: int, index: int,
                               measured: float) -> None:
    if reference is None or not np.isfinite(potential):
        if not np.isfinite(potential):
            raise StudyError(
                f"Window {index}'s seed has a potential energy of "
                f"{potential}, which is what a frame with a molecule split "
                "across the periodic boundary gives. The frame was imaged "
                "before it was used, so this is more likely a topology that "
                "does not match the system."
            , code="simulation.seed.unusable")
        return
    excess = (potential - reference) / max(particles, 1)
    if excess > ENERGY_TOLERANCE_KJMOL_PER_ATOM:
        raise StudyError(
            f"Window {index}, seeded at {measured:.3f} nm, has a potential "
            f"energy {excess:.1f} kJ/mol per atom above the prepared system "
            f"({potential:.3g} against {reference:.3g}). A pulled frame is "
            "strained, but not by this much: something is wrong with the "
            "frame or with the topology it was read against, and a window "
            "started here would fail on its first step."
        , code="simulation.seed.unusable")


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------
def seed_windows(pull_directory: Path | str,
                 prepared: Path | str,
                 centres: list[float],
                 destination: Path | str,
                 *,
                 ligand_resname: str,
                 site_selection: str,
                 temperature_K: float = 300.0,
                 random_seed: int = 0,
                 cone: "dict[str, Any] | None" = None) -> list[Seed]:
    """Build one starting system per window from a finished steered pull.

    `pull_directory` is a run's output: the trajectory and, where PLUMED
    wrote one, the COLVAR that checks the measurement.

    `cone` is the cone the windows will run under, as a plain dict. Given a
    half-angle it is taken as it is; without one it is measured while the pull
    is open and written to ``<destination>/cone.json``, because a measurement
    is a fact about the study that the analysis needs days later and a return
    value is not. Reading the pull is the expensive part of both jobs and it
    happens once.

    Either way the seeds are then checked against it. A seed is chosen for its
    distance and inherits whatever angle that frame happened to have, and a
    window that starts outside its own wall spends its equilibration being
    pushed by 5000 kJ/mol/rad^2 -- the angular form of the failure this module
    exists to prevent.
    """
    import mdtraj as md  # noqa: PLC0415 -- heavy, and only needed here

    pull = Path(pull_directory)
    trajectory_file, topology_file = _pull_files(pull)
    trajectory = md.load(str(trajectory_file), top=str(topology_file))
    if trajectory.n_frames < 2:
        raise StudyError(
            f"The pull at {pull} has {trajectory.n_frames} frame(s). Seeds "
            "are frames along a pull, so there is nothing to take."
        , code="simulation.seed.unusable")

    # Imaged before anything is measured, not only before positions are
    # taken. A ligand split across the boundary has a centre of mass halfway
    # across the box, and every distance computed from it is wrong in a way
    # that looks like data.
    whole = trajectory.image_molecules(inplace=False)

    measured = measure_along(whole, ligand_resname, site_selection)
    _check_against_colvar(pull, measured, whole)

    chosen = frames_for_centres(measured, centres)
    _report(chosen, centres, measured)

    if cone is not None:
        settled = _the_cone_the_windows_will_have(
            whole, ligand_resname, site_selection, Path(destination),
            dict(cone))
        _refuse_seeds_outside_the_cone(
            whole, ligand_resname, site_selection, chosen, centres, settled)

    return write_seeds(prepared, whole, chosen, centres, destination,
                       temperature_K=temperature_K, random_seed=random_seed)


def _the_cone_the_windows_will_have(trajectory: Any, ligand_resname: str,
                                    site_selection: str, destination: Path,
                                    asked: dict[str, Any]) -> dict[str, Any]:
    """The cone as a concrete record: measured off this pull, or taken as given.

    A study that states its own half-angle is not measured for one -- the
    number is the user's and overruling it would make the setting a
    suggestion. Its atoms still have to be resolved, because the check that
    follows needs the group the angle will be measured against.
    """
    import json  # noqa: PLC0415 -- only this branch writes a file

    stated = asked.get("half_angle_deg")
    if isinstance(stated, (int, float)):
        record = dict(asked)
        if not record.get("axis_atoms"):
            selection = str(record.get("axis_selection") or "protein")
            atoms = [int(i) for i in trajectory.topology.select(selection)]
            if not atoms:
                raise StudyError(
                    f"The cone's axis selection {selection!r} matches no atom "
                    "in the pull's topology, so the seeds cannot be checked "
                    "against the wall the windows will run under.", code="simulation.cv.selection_empty")
            record["axis_atoms"] = atoms
        return record

    record = measure_the_cone(
        trajectory, ligand_resname, site_selection,
        force_constant=float(asked.get("force_constant", 5000.0)),
        keep=float(asked.get("keep", 98.0)),
        margin=float(asked.get("margin", 1.2)),
        axis_selection=asked.get("axis_selection") or None,
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "cone.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    print(f"Cone:           {record['half_angle_deg']:.0f} degrees about "
          f"{len(record['axis_atoms'])} alpha carbons; the pull stays within "
          f"{record['held_within_deg']:.0f} of that axis "
          f"({record['keep']:.0f}% of "
          f"{record['measured_from']} frames, worst "
          f"{record['worst_deg']:.0f})")
    if record.get("bound_end_deg") is not None:
        print(f"                bound end within "
              f"{record['bound_end_deg']:.0f} degrees, which is the half that "
              "decides whether the wall bites")
    return record


def _refuse_seeds_outside_the_cone(trajectory: Any, ligand_resname: str,
                                   site_selection: str,
                                   chosen: list[tuple[int, float]],
                                   centres: list[float],
                                   cone: dict[str, Any]) -> None:
    """Refuse a study whose windows would start outside their own wall.

    A seed is chosen for its distance from the site and inherits whatever
    angle that frame happened to have. Nothing before this connected the two,
    so a window could begin where the cone excludes it and spend its
    equilibration being pushed back by 5000 kJ/mol/rad^2 -- which does not
    crash, does not appear in any gate, and leaves the window settled
    somewhere the seeding did not intend.

    Measured off the pull, a cone contains the path it was measured from and
    this passes by construction. It is the other two ways of asking for one
    that need it: a half-angle written into a config can be narrower than the
    path, and a hand-named `axis_selection` need not point along the path at
    all.

    Refused rather than warned. The window would run for days and the
    correction it earns assumes the cone does not cut the bound state, which
    is precisely what a seed outside the cone says it does.
    """
    half_angle = float(cone["half_angle_deg"])
    ligand, site = the_two_groups(trajectory.topology, ligand_resname,
                                  site_selection)
    angle = angles_at_the_site(trajectory, ligand, site,
                               [int(i) for i in cone["axis_atoms"]])

    at_the_start = [(index, float(centres[index]), float(angle[frame]))
                    for index, (frame, _) in enumerate(chosen)]
    outside = [row for row in at_the_start if row[2] > half_angle]
    worst = max(at_the_start, key=lambda row: row[2])

    if not outside:
        print(f"                every seed starts inside the cone; the "
              f"closest to the wall is window {worst[0]} at "
              f"{worst[2]:.1f} of {half_angle:.0f} degrees")
        return

    listed = "; ".join(
        f"window {index} at {centre:.3f} nm starts {off:.1f} degrees off axis"
        for index, centre, off in outside[:6])
    more = ("" if len(outside) <= 6
            else f", and {len(outside) - 6} more")
    raise StudyError(
        f"{len(outside)} of {len(chosen)} windows would start outside a cone "
        f"of {half_angle:.0f} degrees: {listed}{more}. The wall would be "
        "pushing from the first step, so those windows do not begin where "
        "they were seeded to begin -- and a cone that excludes where the "
        "ligand was is a cone that cuts the state the binding free energy is "
        "measured over. Widen the cone, or leave the half-angle out and let "
        "it be measured from this pull, which sizes it to contain the path "
        "the seeds are taken from."
    , code="simulation.cone.windows_outside")


def _pull_files(pull: Path) -> tuple[Path, Path]:
    """The pull's trajectory and the topology that reads it."""
    simulation = pull / "simulation"
    root = simulation if simulation.is_dir() else pull
    trajectories = sorted(
        [p for p in root.glob("*.dcd")] + [p for p in root.glob("*.xtc")],
        key=lambda p: p.stat().st_size, reverse=True)
    if not trajectories:
        raise MissingResultError(
            f"No trajectory under {root}. A pull that wrote no frames cannot "
            "seed anything -- check that the run finished.")
    for name in ("trajectory_topology.pdb", "topology.pdb"):
        candidate = root / name
        if candidate.is_file():
            return trajectories[0], candidate
    raise MissingResultError(
        f"No topology beside {trajectories[0]}, so its frames cannot be "
        "read.", code="analysis.data.absent")


def _check_against_colvar(pull: Path, measured: np.ndarray,
                          trajectory: Any = None) -> None:
    """Compare the recomputed variable with the one PLUMED biased.

    Not an alignment -- the two are written on different strides. Both
    sample the same run over the same interval, so their distributions must
    sit in the same place; where they do not, the selections here are
    measuring something PLUMED was not.

    Compared on the median rather than the first and last values. Endpoints
    work for a pull, whose ends are a nanometre and a half apart, and fail
    for anything held still: a restrained window fluctuates by more than the
    tolerance within a picosecond, so two correct series would disagree at
    their last recorded value and a good measurement would be refused. The
    median of a series does not care which stride wrote it.
    """
    simulation = pull / "simulation"
    root = simulation if simulation.is_dir() else pull
    record = read_colvar(root / "COLVAR")
    if record is None:
        logger.info("No COLVAR beside the pull; the collective variable was "
                    "recomputed and not cross-checked.")
        return
    _, cv = record
    recomputed, biased = float(np.median(measured)), float(np.median(cv))
    if abs(recomputed - biased) <= COLVAR_AGREEMENT_NM:
        return

    # A distance wider than the box is not a distance. Said first, because
    # it names the cause instead of the symptom: the frames were not reduced
    # to the minimum image, and no change of selection would fix it.
    impossible = ""
    if trajectory is not None and trajectory.unitcell_vectors is not None:
        widest = _widest_a_distance_can_be(trajectory.unitcell_vectors[0])
        if float(measured.max()) > widest:
            impossible = (
                f" The largest value recomputed here, "
                f"{float(measured.max()):.3f} nm, is wider than this box "
                f"allows ({widest:.3f} nm), so these are not minimum-image "
                "distances at all.")

    raise StudyError(
        f"Over this run the collective variable recomputed here has median "
        f"{recomputed:.3f} nm and the one PLUMED biased has median "
        f"{biased:.3f} nm "
        f"(spans {measured.min():.3f}-{measured.max():.3f} against "
        f"{cv.min():.3f}-{cv.max():.3f}).{impossible} Either the "
        "`ligand_resname` and `site_selection` used to seed are not the ones "
        "that were biased, or the coordinates were read without the "
        "periodicity PLUMED applied. Seeds taken from these frames would sit "
        "at distances nobody asked for."
    , code="simulation.seed.unusable")


def _widest_a_distance_can_be(cell: np.ndarray, samples: int = 4096) -> float:
    """The largest separation minimum-image reduction can return in this cell.

    Measured rather than derived. Half the longest diagonal is a correct
    upper bound and a useless one -- for the dodecahedron C1 was solvated
    in it is 9.1 nm, which would not have flagged an 8.2 nm "distance" in a
    box whose reduction can never return more than about 3. The quantity
    wanted is the lattice's covering radius, and sampling the cell with the
    same reduction the caller uses gives it without a derivation that could
    disagree with the code it is checking.
    """
    cell = np.asarray(cell, dtype=float)
    rng = np.random.default_rng(0)
    points = rng.random((samples, 3)) @ cell
    reduced = shortest_vector(points, np.broadcast_to(
        cell, (samples, 3, 3)).copy())
    # A little slack, because this is sampled rather than exhaustive and the
    # check exists to catch failures of orders of magnitude.
    return 1.05 * float(np.linalg.norm(reduced, axis=1).max())


def _report(chosen: list[tuple[int, float]], centres: list[float],
            measured: np.ndarray) -> None:
    """Say how well the pull covered the windows, before anything is run."""
    away = [abs(m - c) for (_, m), c in zip(chosen, centres)]
    worst = int(np.argmax(away))
    frames = {f for f, _ in chosen}
    print(f"Seeds:          {len(centres)} windows from "
          f"{measured.size} frames spanning "
          f"{measured.min():.3f}-{measured.max():.3f} nm; "
          f"worst placement {away[worst]:.4f} nm at window {worst}")
    if len(frames) != len(chosen):
        print(f"                {len(chosen) - len(frames)} window(s) share a "
              "frame with a neighbour -- the pull moved faster than the "
              "windows are spaced there.")
