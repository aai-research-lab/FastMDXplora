"""How close the solute comes to its own periodic images, frame by frame.

A periodic box holds one copy of the solute and is surrounded by its images.
Where any part of the solute comes within the nonbonded cutoff of any part of
an image, the solute is interacting with its own copy, and every quantity
measured of those frames describes the cell as much as the molecule. Setup
sizes the box from the starting structure, so a chain that unfolds, or a
complex that turns, can reach its image though the box was sound at the
start. A 10-residue chain in a chignolin study came to 0.261 nm of its own
copy while the check of its two ends alone passed it.

So the whole solute is checked, as ``gmx mindist -pi`` checks it: in each
frame, the closest approach of any solute heavy atom to any heavy atom of the
solute's periodic images, kept as a series, with the frames under the cutoff
counted, and those under 0.5 nm.

**Made whole first.** Each frame's solute is made whole along its bonds
(between heavy atoms; where the topology carries no bonds, along the chain's
atom order, consecutive atoms of a chain being neighbours): every atom is
placed at the minimum image of the atom it is bonded to, which is exact for a
bond shorter than half the cell's narrowest width.

**Complexes, each against its own images.** Where the solute is several
molecules (chains, a ligand, copies of a peptide), they are grouped in the
first frame read: molecules in contact, any heavy atoms within 0.5 nm, are
one complex (:func:`complexes`), kept whole from frame to frame. Each complex
is checked against its own images only. Two copies of a peptide meeting
across the box's boundary are two molecules in contact, the box's
concentration, not a molecule meeting its own copy, and are not counted.
The grouping is not made again as the run goes: a complex that parts or a
ligand that unbinds stays one complex, so a parted dimer may be noted, and
molecules that join later (an oligomer formed during the run) are checked as
its molecules.

**The images searched.** Every lattice vector ``L = i a + j b + k c`` that
could carry an image closer than the closest found, in any cell shape,
triclinic included, and for each the nearest pair between the solute and its
image ``L`` away, found with a k-d tree. An image ``L`` away can come within
``best`` of the solute only if ``|L| < best + 2r`` (``r`` the solute's
reach from its centre), and every lattice vector that short has
``|i| <= (best + 2r) |c_1|`` (and the same for ``j`` and ``k``), ``c_n`` the
columns of the inverse cell. So the shifts searched are enumerated from that
reach, with ``best`` first taken over the 26 neighbouring cells: a solute
spanning several cells is searched as far as it reaches, and no frame is
settled with a shorter vector unsearched. ``L`` and ``-L`` give the same
distance, so half are searched, shortest first, stopping where
``|L| - 2r`` passes the closest approach found.

The check's finding (``findings.solute_image``) and its series are what
every other analysis of the same frames reads (the orchestrator hands both
to each). Meeting its own copy may bias a mean; it does not make the mean's
statistical error unknown. So every error bar is kept, and where the solute
came within the cutoff of its image in the frames a mean uses (after its
discard, or from its ``start``), the analysis carries a note of it, counted
over those frames (:func:`note_on_frames`, one function for every mean, the
reweighted averages' too). For an analysis of the solute's shape or
conformation the note says the shape may be biased; for the run's ensemble
and the solvent (:data:`PLAIN_NOTE`), that a mean may be. The end-to-end
distance alone withholds its error for it, as it did for its two ends before
the whole chain was checked. Whether to grade the note by how often and how
close (a note only, under a share and never in contact; more, the mean over
the frames clear of the image beside it) waits for thresholds fixed in a
registered check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np

from fastmdxplora.analysis.base import Analysis, narrowest_width
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import colour
from fastmdxplora.image_note import COUNTED_OVER, share_said  # noqa: F401 - said here too
from fastmdxplora.refusals import StudyError

#: The cutoff a solute's image is checked against where neither the run's
#: record nor its force field gives one (:func:`run_cutoff`): OpenMM's own
#: default, which setup takes for a list of files that names no family.
CUTOFF_NM = 1.0

#: Where the run's resolved settings are recorded, in the study.
SETUP_RECORD = "setup/setup_parameters.json"

#: Closer than this, the solute is in contact with its own copy, not merely
#: within the cutoff of it.
CLOSE_NM = 0.5

#: Frames made whole and searched at a time, so memory stays bounded.
FRAMES_AT_A_TIME = 1000

#: The analyses of the run's ensemble or of the solvent around the solute,
#: whose note is the plain one: a mean over frames where the solute met its
#: copy may be biased by it. Every other analysis is of the solute's shape or
#: conformation (end-to-end distance, Rg, RMSD, RMSF, contacts, secondary
#: structure, the moments, the clusters and projections), and its note says
#: the shape may be biased.
PLAIN_NOTE = frozenset({
    "thermodynamics", "rdf", "coordination_number", "water_sites",
    "area_per_lipid", "bilayer_thickness", "lipid_order",
    "pmf", "metad_surface", "steered_work",
})


def note_on_frames(finding: dict[str, Any] | None, distances: Any, used: Any = None, *,
                   times_ps: Any = None, analysis: str = "", counted_over: str = "frames",
                   how: str = "") -> dict[str, Any] | None:
    """The note a mean (or an analysis without one) carries where the solute
    met its own periodic image in the frames it uses: one function for every
    mean record, the reweighted averages' too.

    ``finding`` is the image check's (its cutoff and where that was read),
    ``distances`` its series over the frames analysed, ``used`` the indices
    among them that this mean uses (after its discard or ``start``; every
    frame where None; a frame given more than once is counted once for each,
    as the energy rows read at one frame are), ``times_ps`` the frames'
    times, ``counted_over`` what the count is of (:data:`COUNTED_OVER`) and
    ``how`` how they were found, said after it. None where no frame
    used is within the cutoff, or the check's series is not there to count.
    The error bar is kept: seeing its copy may bias a mean, it does not
    make its statistical error unknown.
    """
    if not isinstance(finding, dict) or distances is None:
        return None
    series = np.asarray(distances, dtype=float)
    frames = np.arange(series.size) if used is None else np.asarray(used, dtype=np.int64)
    frames = frames[(frames >= 0) & (frames < series.size)]
    if frames.size == 0:
        return None
    cutoff = float(finding.get("cutoff_nm") or CUTOFF_NM)
    over = series[frames]
    under = int(np.count_nonzero(over < cutoff))
    if not under:
        return None
    closest = int(frames[int(np.argmin(over))])
    times = None if times_ps is None else np.asarray(times_ps, dtype=float)
    timed = times is not None and times.size == series.size and bool(
        np.isfinite(times[closest]))
    from fastmdxplora.simulation.sampling_ask import length_said

    when = (f"frame {closest}, {length_said(float(times[closest]) / 1000.0)}" if timed
            else f"frame {closest}")
    shape = analysis not in PLAIN_NOTE
    note: dict[str, Any] = {
        "frames_used": int(frames.size),
        "frames_under_cutoff": under,
        "fraction_under_cutoff": under / frames.size,
        "frames_under_close": int(np.count_nonzero(over < CLOSE_NM)),
        "nearest_nm": float(series[closest]),
        "nearest_frame": closest,
        "nearest_time_ps": float(times[closest]) if timed else None,
        "cutoff_nm": cutoff,
        "may_bias": "shape" if shape else "mean",
        # The image check's finding, or the end-to-end distance's own check
        # of its chain where the image check did not run.
        "read_from": str(finding.get("checked_by") or "image_check"),
        "counted_over": counted_over,
    }
    noun = COUNTED_OVER.get(counted_over, counted_over)
    close = note["frames_under_close"]
    # Within the cutoff the solute sees its image (it interacts with its copy
    # in direct space); in contact only under 0.5 nm.
    touching = (f", in contact with it (under {CLOSE_NM:g} nm) in {close:,}"
                if close else "")
    note["said"] = (
        f"The solute came within {cutoff:g} nm of its own periodic image in "
        f"{under:,} of {frames.size:,} {noun} ({share_said(under, frames.size)}){how}"
        f"{touching}; the closest, {note['nearest_nm']:.3f} nm at {when}. Within the "
        "cutoff the solute sees its image, interacting with its copy in direct "
        "space, so "
        + ("its shape over such frames, and a mean of it, may be biased by it."
           if shape else "a mean over such frames may be biased by it."))
    return note


def note_numbers(note: dict[str, Any]) -> dict[str, Any]:
    """The note's numbers, without its sentence: what each mean record of an
    analysis carries, the sentence said once, in the analysis's
    ``findings.solute_image``."""
    return {key: note[key] for key in ("frames_used", "frames_under_cutoff",
                                       "fraction_under_cutoff", "frames_under_close",
                                       "nearest_nm", "cutoff_nm", "may_bias",
                                       "read_from", "counted_over") if key in note}


def _not_solute(residue) -> bool:
    """Water, an ion or a lipid of a bilayer: never the solute checked."""
    from fastmdxplora.ligand_detection import ION_RESNAMES, WATER_RESNAMES
    from fastmdxplora.lipids import is_lipid

    name = str(residue.name).strip().upper()
    return (bool(getattr(residue, "is_water", False)) or name in WATER_RESNAMES
            or name in ION_RESNAMES or is_lipid(name))


def solute_atoms(topology, selection: str | None) -> tuple[np.ndarray, str]:
    """The atoms of the solute checked against its images, and the rule said.

    The macromolecules: every residue that is protein or
    nucleic acid, and every other polymer chain (a molecule bonded across two
    or more residues; in a topology with no bonds, a chain of two or more
    residues), so an RNA, a DNA duplex or a peptide of residues MDTraj does
    not call protein is checked as a protein is. Anything else that is not
    water, an ion or a lipid (a ligand, a cofactor) is counted with them
    only where it is part of ``selection``, the solute the study's analyses
    already use (the scope's, ``protein or resname LIG`` with a ligand).
    Water, ions and lipids never. How they are grouped into complexes,
    each checked against its own images only, is :func:`complexes`'.
    """
    from fastmdxplora.analysis.residues import _is_polymer

    residues = list(topology.residues)
    polymer = {r.index for r in residues if not _not_solute(r) and _is_polymer(r)}
    if topology.n_bonds:
        for molecule in topology.find_molecules():
            spanned = {a.residue.index for a in molecule}
            if len(spanned) > 1 and not any(_not_solute(topology.residue(i))
                                            for i in spanned):
                polymer |= spanned
            elif spanned & polymer:
                polymer |= {i for i in spanned if not _not_solute(topology.residue(i))}
    else:
        for chain in topology.chains:
            kept = [r.index for r in chain.residues if not _not_solute(r)]
            if len(kept) > 1:
                polymer |= set(kept)
    chosen = set()
    if selection:
        try:
            chosen = {int(a) for a in topology.select(selection)}
        except Exception:  # noqa: BLE001 - a selection this topology cannot make
            chosen = set()
    with_them = sorted({topology.atom(a).residue.name for a in chosen
                        if topology.atom(a).residue.index not in polymer
                        and not _not_solute(topology.atom(a).residue)})
    atoms = [a.index for a in topology.atoms
             if a.residue.index in polymer
             or (a.index in chosen and not _not_solute(a.residue))]
    chains = len({topology.residue(i).chain.index for i in polymer})
    said = (f"the macromolecules ({len(polymer):,} "
            f"{'residue' if len(polymer) == 1 else 'residues'} in {chains:,} "
            f"{'chain' if chains == 1 else 'chains'})" if polymer
            else "no macromolecule")
    if with_them:
        said += (f"{' with' if polymer else ', only'} {', '.join(with_them)} of the "
                 "analyses' solute")
    said += "; water, ions and lipids left out"
    return np.asarray(atoms, dtype=np.int64), said


def _heavy(topology, atoms: np.ndarray) -> np.ndarray:
    """The heavy atoms among ``atoms``: those with an element that is not
    hydrogen (a virtual site has none)."""
    kept = []
    for index in atoms:
        element = topology.atom(int(index)).element
        if element is not None and element.symbol not in ("H", "D") and element.mass > 0:
            kept.append(int(index))
    return np.asarray(kept, dtype=np.int64)


def walk(topology, atoms: np.ndarray) -> tuple[list[tuple[int, int]], str]:
    """The steps that make ``atoms`` whole, as pairs of positions in
    ``atoms`` (the one placed, the one placed beside), in the order they are
    taken; and how they were found."""
    where = {int(a): k for k, a in enumerate(atoms)}
    neighbours: list[list[int]] = [[] for _ in atoms]
    if topology.n_bonds:
        how = "along its bonds"
        for one, other in topology.bonds:
            i, j = where.get(one.index), where.get(other.index)
            if i is not None and j is not None:
                neighbours[i].append(j)
                neighbours[j].append(i)
    else:
        how = ("along the chain's atom order, the topology carrying no bonds")
        for k in range(1, len(atoms)):
            if (topology.atom(int(atoms[k])).residue.chain.index
                    == topology.atom(int(atoms[k - 1])).residue.chain.index):
                neighbours[k].append(k - 1)
                neighbours[k - 1].append(k)
    steps: list[tuple[int, int]] = []
    seen = np.zeros(len(atoms), dtype=bool)
    for root in range(len(atoms)):
        if seen[root]:
            continue
        seen[root] = True
        queue = [root]
        while queue:
            parent = queue.pop(0)
            for child in neighbours[parent]:
                if not seen[child]:
                    seen[child] = True
                    steps.append((parent, child))
                    queue.append(child)
    return steps, how


def made_whole(xyz: np.ndarray, boxes: np.ndarray,
               steps: list[tuple[int, int]]) -> np.ndarray:
    """``xyz`` (frames, atoms, 3) with each atom moved to the minimum image of
    the one it steps from, in place; ``boxes`` (frames, 3, 3), rows the cell
    vectors."""
    inverse = np.linalg.inv(boxes)
    for parent, child in steps:
        step = xyz[:, child] - xyz[:, parent]
        cells = np.rint(np.einsum("fi,fij->fj", step, inverse))
        step -= np.einsum("fj,fji->fi", cells, boxes)
        xyz[:, child] = xyz[:, parent] + step
    return xyz


def _half(every: np.ndarray) -> np.ndarray:
    """One of each pair of shifts L and -L, the zero shift left out."""
    nonzero = every[np.any(every != 0, axis=1)]
    first = nonzero[np.arange(len(nonzero)), np.argmax(nonzero != 0, axis=1)]
    return nonzero[first > 0]


#: The 26 neighbouring cells, one of each pair: where the first closest
#: approach is found, from which the reach to search is worked out.
_NEIGHBOURS = _half(np.array(np.meshgrid(*[np.arange(-1, 2)] * 3, indexing="ij"))
                    .reshape(3, -1).T).astype(np.float64)


def shifts_within(reach: float, box: np.ndarray) -> np.ndarray:
    """Every lattice shift ``n`` (one of each pair) whose vector ``n @ box``
    could be shorter than ``reach``: ``|n_i| <= reach |c_i|``, ``c_i`` the
    i-th column of the inverse cell, which bounds every lattice vector of
    that length exactly."""
    columns = np.sqrt((np.linalg.inv(box) ** 2).sum(axis=0))
    bound = np.floor(reach * columns + 1e-9).astype(int)
    spans = [np.arange(-b, b + 1) for b in bound]
    every = np.array(np.meshgrid(*spans, indexing="ij")).reshape(3, -1).T
    return _half(every).astype(np.float64)


def _closest_over(points: np.ndarray, tree: Any, centre: np.ndarray, radius: float,
                  lattice: np.ndarray, best: float, near: tuple[int, int]
                  ) -> tuple[float, tuple[int, int]]:
    lengths = np.sqrt((lattice ** 2).sum(axis=1))
    for k in np.argsort(lengths, kind="stable"):
        # |x_j + L - x_i| >= |L| - |x_j - c| - |x_i - c| >= |L| - 2r.
        if lengths[k] - 2.0 * radius >= best:
            break
        image = points + lattice[k]
        # An image atom within `best` of any atom is within r + best of the centre.
        candidates = np.nonzero(((image - centre) ** 2).sum(axis=1)
                                < (radius + best) ** 2)[0]
        if candidates.size == 0:
            continue
        found, nearest = tree.query(image[candidates], k=1, distance_upper_bound=best)
        which = int(np.argmin(found))
        if found[which] < best:
            best = float(found[which])
            near = (int(nearest[which]), int(candidates[which]))
    return best, near


def nearest_image(points: np.ndarray, box: np.ndarray) -> tuple[float, int, int, int]:
    """The closest approach of ``points`` to their own periodic images in the
    cell ``box`` (rows the cell vectors): the distance, the atom (a position
    in ``points``), the atom whose image it is, and the most cells away any
    shift searched went. Every image that could be closer is searched
    (:func:`shifts_within`)."""
    from scipy.spatial import cKDTree

    centre = points.mean(axis=0)
    radius = float(np.sqrt(((points - centre) ** 2).sum(axis=1).max()))
    tree = cKDTree(points)
    best, near = _closest_over(points, tree, centre, radius, _NEIGHBOURS @ box,
                               np.inf, (-1, -1))
    shifts = shifts_within(best + 2.0 * radius, box)
    best, near = _closest_over(points, tree, centre, radius, shifts @ box, best, near)
    furthest = int(np.abs(shifts).max()) if shifts.size else 1
    return best, near[0], near[1], max(furthest, 1)


def _every_shift(reach: float, box: np.ndarray) -> np.ndarray:
    """Every lattice shift, the zero shift and both of each pair, whose
    vector could be shorter than ``reach`` (the bound of
    :func:`shifts_within`): between two different sets of atoms ``L`` and
    ``-L`` are not the same."""
    columns = np.sqrt((np.linalg.inv(box) ** 2).sum(axis=0))
    bound = np.floor(reach * columns + 1e-9).astype(int)
    spans = [np.arange(-b, b + 1) for b in np.maximum(bound, 1)]
    return np.array(np.meshgrid(*spans, indexing="ij")).reshape(3, -1).T.astype(np.float64)


def nearest_between(fixed: np.ndarray, tree: Any, moving: np.ndarray,
                    box: np.ndarray) -> tuple[float, np.ndarray]:
    """The closest approach of any image of ``moving`` (the copy in the cell
    included) to ``fixed`` (``tree`` its k-d tree), and the lattice vector
    that gives it. Every shift that could be closer is searched: an image
    ``L`` away comes within ``best`` of ``fixed`` only where
    ``|L| < best + r_f + r_m + |c_m - c_f|``."""
    centre_f, centre_m = fixed.mean(axis=0), moving.mean(axis=0)
    reach_f = float(np.sqrt(((fixed - centre_f) ** 2).sum(axis=1).max()))
    reach_m = float(np.sqrt(((moving - centre_m) ** 2).sum(axis=1).max()))
    apart = float(np.linalg.norm(centre_m - centre_f))
    best, vector = np.inf, np.zeros(3)
    for reach in (None, 0.0):
        shifts = (_every_shift(0.0, box) if reach is None
                  else _every_shift(best + reach_f + reach_m + apart, box))
        for shift in shifts @ box:
            if np.linalg.norm(centre_m + shift - centre_f) - reach_f - reach_m >= best:
                continue
            found = float(tree.query(moving + shift, k=1)[0].min())
            if found < best:
                best, vector = found, shift
    return best, vector


def _molecules(n_points: int, steps: list[tuple[int, int]]) -> np.ndarray:
    """Each point's molecule, numbered from 0: the points the steps that
    make them whole join (bonds, or a chain's order without them)."""
    label = np.arange(n_points)

    def root(i: int) -> int:
        while label[i] != i:
            label[i] = label[label[i]]
            i = int(label[i])
        return i

    for one, other in steps:
        a, b = root(one), root(other)
        if a != b:
            label[max(a, b)] = min(a, b)
    roots = np.array([root(i) for i in range(n_points)])
    return np.unique(roots, return_inverse=True)[1]


def complexes(xyz: np.ndarray, box: np.ndarray, molecule: np.ndarray
              ) -> tuple[list[list[int]], dict[int, np.ndarray]]:
    """The solute's molecules grouped into complexes, from one frame (the
    first the check reads), each molecule already whole: molecules bonded
    to each other are one (they are one molecule here), and molecules in
    contact, any heavy atoms within :data:`CLOSE_NM` of each other, are one
    complex. Each complex is checked against its own periodic images only:
    two separate copies of a peptide in a box meeting across its boundary is
    contact between molecules in the box (their concentration), not a
    molecule meeting its own copy.

    Returned with each molecule's lattice shift that makes its complex whole
    in that frame: none where it is in contact with the molecule before it
    as stored (the loader places a complex's molecules beside each other),
    else the image of it in contact. Each complex lists its molecules in the
    order they are placed, each in contact with one before it."""
    from scipy.spatial import cKDTree

    count = int(molecule.max()) + 1 if molecule.size else 0
    points = [xyz[molecule == m] for m in range(count)]
    trees = [cKDTree(p) for p in points]
    touching: list[dict[int, np.ndarray]] = [{} for _ in range(count)]
    for a in range(count):
        for b in range(a + 1, count):
            # As stored first: a shorter image of a molecule spanning the
            # cell would otherwise be taken for the one beside it.
            if float(trees[a].query(points[b], k=1)[0].min()) < CLOSE_NM:
                shift = np.zeros(3)
            else:
                found, shift = nearest_between(points[a], trees[a], points[b], box)
                if found >= CLOSE_NM:
                    continue
            touching[a][b] = shift
            touching[b][a] = -shift
    seen = np.zeros(count, dtype=bool)
    groups: list[list[int]] = []
    shifts: dict[int, np.ndarray] = {}
    for start in range(count):
        if seen[start]:
            continue
        seen[start] = True
        shifts[start] = np.zeros(3)
        order, queue = [start], [start]
        while queue:
            here = queue.pop(0)
            for other in sorted(touching[here]):
                if not seen[other]:
                    seen[other] = True
                    shifts[other] = shifts[here] + touching[here][other]
                    order.append(other)
                    queue.append(other)
        groups.append(order)
    return groups, shifts


class _Complex:
    """One complex, kept whole from frame to frame: its first molecule where
    it is, and each other one at the image that moved least from where it
    was against the first in the frame before (from the shifts of
    :func:`complexes` in the first frame), as a trajectory is unwrapped. A
    molecule moves far less than half the cell between saved frames."""

    def __init__(self, members: list[int], molecule: np.ndarray,
                 shifts: dict[int, np.ndarray]) -> None:
        self.members = members
        self.atoms = [np.nonzero(molecule == m)[0] for m in members]
        self.where = np.concatenate(self.atoms)
        self.first_shifts = [shifts[m] for m in members]
        self.offsets: list[np.ndarray] | None = None

    def points(self, xyz: np.ndarray, box: np.ndarray) -> np.ndarray:
        centres = [xyz[atoms].mean(axis=0) for atoms in self.atoms]
        if self.offsets is None:
            moved = list(self.first_shifts)
        else:
            inverse = np.linalg.inv(box)
            neighbours = np.array(np.meshgrid(*[np.arange(-1, 2)] * 3, indexing="ij")
                                  ).reshape(3, -1).T @ box
            moved = [np.zeros(3)]
            for k in range(1, len(self.atoms)):
                relative = centres[k] - centres[0]
                guess = -np.rint((relative - self.offsets[k]) @ inverse) @ box
                tried = guess + neighbours
                miss = np.linalg.norm(relative + tried - self.offsets[k], axis=1)
                moved.append(tried[int(np.argmin(miss))])
        self.offsets = [centres[k] + moved[k] - centres[0] for k in range(len(self.atoms))]
        return np.concatenate([xyz[atoms] + moved[k] for k, atoms in enumerate(self.atoms)])


def _grouping_said(groups: list[list[int]]) -> str:
    """How the solute's molecules were grouped, as the finding says it."""
    # Grouped once, in the first frame read, and said so: a complex that
    # parts is still checked as one, and molecules that join later apart.
    rule = ("molecules bonded to each other or in contact (any heavy atoms within "
            f"{CLOSE_NM:g} nm) in the first frame read being one complex for the whole "
            "run, so a complex that parts or a ligand that unbinds may still be noted, and "
            "molecules that join later are checked apart")
    if len(groups) == 1:
        if len(groups[0]) == 1:
            return "checked as one molecule against its own images"
        return (f"checked as one complex of {len(groups[0])} molecules against its own "
                f"images, {rule}")
    sizes = sorted({len(group) for group in groups})
    each = (f"of {sizes[0]} {'molecule' if sizes[0] == 1 else 'molecules'} each"
            if len(sizes) == 1 else f"of {sizes[0]} to {sizes[-1]} molecules")
    return (f"checked as {len(groups)} complexes {each}, each against its own images "
            f"only, {rule}; a copy meeting another copy's image is contact between "
            "molecules in the box, their concentration, not a self-image, and is "
            "not counted")


def run_cutoff(study: Path | None) -> tuple[float, str]:
    """The real-space cutoff the run used, in nm, and where it was read, as a
    phrase that follows "The cutoff is <x> nm, ": the run's
    ``nonbonded_cutoff_nm`` as set, or as setup resolved it, from the setup
    record of the system the run simulated, found as every other reader
    finds it (:func:`fastmdxplora.simulation.pipeline.setup_records_of`: the
    study's own ``setup/``, or the one its `setup_from` names, an umbrella
    window's shared setup among them) and read through
    :mod:`fastmdxplora.record_access`; else the cutoff of the force field
    the record names (its scheme, as setup takes it); else
    :data:`CUTOFF_NM`, said as taken and why."""
    import json

    from fastmdxplora import record_access

    def taken(why: str) -> tuple[float, str]:
        return CUTOFF_NM, f"taken because {why}"

    nowhere = ("no setup record of this run was found where every reader of its "
               f"setup looks (its own {SETUP_RECORD}, or the one its `setup_from` names)")
    if study is None:
        return taken("no study was given to read the run's setup record from")
    from fastmdxplora.simulation.pipeline import setup_records_of

    try:
        records = setup_records_of(study)
    except Exception:  # noqa: BLE001 - a record naming a system that moved
        records = None
    if records is None:
        return taken(nowhere)
    path = Path(records) / "setup_parameters.json"
    try:
        where = path.relative_to(Path(study)).as_posix()
    except ValueError:
        where = str(path)
    if not record_access.can_read(path):
        return taken(f"the setup record ({where}) was not read: it leads out of the "
                     "study this page is held to")
    try:
        if not record_access.is_file(path):
            return taken(nowhere)
        record = json.loads(record_access.read_text(path, encoding="utf-8"))
    except (OSError, ValueError):
        return taken(f"the setup record ({where}) could not be read")
    if not isinstance(record, dict):
        return taken(f"the setup record ({where}) holds no settings")
    asked = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    resolved = record.get("resolved") if isinstance(record.get("resolved"), dict) else {}
    for found, said in ((asked, "as set"), (resolved, "as setup resolved it")):
        value = found.get("nonbonded_cutoff_nm")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value), f"read from the run's `nonbonded_cutoff_nm` {said}, in {where}"
    named = asked.get("force_field") or resolved.get("force_field") or asked.get("forcefield")
    if named:
        try:
            from fastmdxplora.setup.forcefields import nonbonded_scheme

            value = float(nonbonded_scheme(
                list(named) if isinstance(named, (list, tuple)) else str(named),
                cutoff_nm=None, use_switching_function=None, switch_distance_nm=None)[0])
            return value, f"read from the scheme of the run's force field, named in {where}"
        except Exception:  # noqa: BLE001 - a name this version no longer knows
            pass
    return taken(f"no cutoff of this run is recorded in {where}")


def _atom_said(topology, index: int) -> str:
    atom = topology.atom(index)
    residue = atom.residue
    chain = getattr(residue.chain, "chain_id", None)
    label = f"{residue.name}{residue.resSeq} {atom.name}"
    if topology.n_chains > 1 and chain:
        label = f"{label} (chain {chain})"
    return label


def solute_image_record(traj: md.Trajectory, atoms: np.ndarray, *,
                        cutoff_nm: float = CUTOFF_NM, cutoff_from: str | None = None,
                        solute_said: str | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Per frame, the closest approach of the heavy atoms among ``atoms`` to
    the heavy atoms of their periodic images, in nm, and the finding.

    The finding (``reaches_its_image``, the frames under ``cutoff_nm`` and
    under :data:`CLOSE_NM`, the closest approach with its frame and atoms,
    and ``said``, the sentence every reader is given) is what every analysis
    of these frames is qualified by.
    """
    if traj.unitcell_vectors is None:
        raise StudyError(
            "This trajectory carries no unit cell, so the solute has no "
            "periodic images to come near.",
            code="analysis.system.inapplicable", analysis="image_check",
            reason="no unit cell")
    heavy = _heavy(traj.topology, np.asarray(atoms, dtype=np.int64))
    if heavy.size == 0:
        raise StudyError(
            "The solute selection holds no heavy atoms, so there is nothing to "
            "check against its images.",
            code="analysis.selection.empty", expression=None, role="solute")
    steps, how = walk(traj.topology, heavy)
    molecule = _molecules(heavy.size, steps)
    n = traj.n_frames
    distances = np.empty(n, dtype=np.float64)
    pairs = np.empty((n, 2), dtype=np.int64)
    furthest = 1
    groups: list[list[int]] | None = None
    for low in range(0, n, FRAMES_AT_A_TIME):
        high = min(low + FRAMES_AT_A_TIME, n)
        boxes = np.asarray(traj.unitcell_vectors[low:high], dtype=np.float64)
        xyz = made_whole(np.asarray(traj.xyz[low:high][:, heavy], dtype=np.float64),
                         boxes, steps)
        if groups is None:
            # From the first frame the check reads.
            groups, shifts = complexes(xyz[0], boxes[0], molecule)
            kept = [_Complex(members, molecule, shifts) for members in groups]
        for f in range(high - low):
            best = (np.inf, -1, -1)
            for one in kept:
                if len(kept) == 1 and len(one.members) == 1:
                    points, where = xyz[f], np.arange(heavy.size)
                else:
                    points, where = one.points(xyz[f], boxes[f]), one.where
                d, i, j, cells = nearest_image(points, boxes[f])
                furthest = max(furthest, cells)
                if d < best[0]:
                    best = (d, int(where[i]), int(where[j]))
            distances[low + f], pairs[low + f] = best[0], best[1:]

    frame = int(np.argmin(distances))
    nearest = float(distances[frame])
    under = int(np.count_nonzero(distances < cutoff_nm))
    close = int(np.count_nonzero(distances < CLOSE_NM))
    times = np.asarray(traj.time, dtype=float) if traj.time is not None else None
    timed = times is not None and times.size == n and bool(np.all(np.isfinite(times)))
    widths = narrowest_width(traj)
    record: dict[str, Any] = {
        "cutoff_nm": float(cutoff_nm),
        "cutoff_from": cutoff_from or "given to the check",
        "close_nm": CLOSE_NM,
        "frames": n,
        "nearest_nm": nearest,
        "nearest_frame": frame,
        "nearest_time_ns": float(times[frame]) / 1000.0 if timed else None,
        "nearest_atoms": [_atom_said(traj.topology, int(heavy[pairs[frame, 0]])),
                          _atom_said(traj.topology, int(heavy[pairs[frame, 1]]))],
        "frames_under_cutoff": under,
        "fraction_under_cutoff": under / n,
        "frames_under_close": close,
        "fraction_under_close": close / n,
        "heavy_atoms": int(heavy.size),
        "made_whole": how,
        "complexes": len(groups or []),
        "molecules": int(molecule.max()) + 1 if molecule.size else 0,
        "grouped": _grouping_said(groups or [[0]]),
        # Every image within the solute's reach was searched; the most
        # cells away that took, in any frame.
        "most_cells_searched": int(furthest),
        "narrowest_width_nm": [float(np.min(widths)), float(np.max(widths))],
        "reaches_its_image": bool(under),
    }
    from fastmdxplora.simulation.sampling_ask import length_said

    # Picoseconds under a nanosecond, as every page says a time.
    when = (f"frame {frame}, {length_said(record['nearest_time_ns'])}" if timed
            else f"frame {frame}")
    pair = (f"between {record['nearest_atoms'][0]} and the image of "
            f"{record['nearest_atoms'][1]} (heavy atoms only)")
    if solute_said:
        record["solute"] = solute_said
    # What matters first, in a sentence with no colon or semicolon in it: a
    # page giving the first sentence alone gives that.
    if under:
        record["said"] = (
            f"The solute came within {nearest:.3f} nm of its own periodic image, "
            f"inside the {cutoff_nm:g} nm cutoff in {under:,} of {n:,} frames "
            f"({share_said(under, n)}) and in contact with it (under {CLOSE_NM:g} nm) "
            + (f"in {close:,} ({share_said(close, n)})" if close else "in none")
            + f". The closest was at {when}, {pair}. The "
            f"cutoff is {cutoff_nm:g} nm, {record['cutoff_from']}. Within the cutoff "
            "the solute sees its image, interacting with its copy in direct space, "
            "and a mean over those frames may be biased by it. A larger "
            "box, in a new study (a box cannot grow mid-run), is what would "
            "change this.")
    else:
        # Of several complexes, each against its own images: not the solute
        # taken together, whose copies may come closer to each other's.
        who = ("No complex of the solute came closer" if len(groups or []) > 1
               else "The solute came no closer")
        record["said"] = (
            f"{who} than {nearest:.3f} nm to its own periodic "
            f"image, outside the {cutoff_nm:g} nm cutoff in every one of {n:,} "
            f"frames. The closest was at {when}, {pair}. The cutoff is "
            f"{cutoff_nm:g} nm, {record['cutoff_from']}.")
    if solute_said:
        solute, _, left_out = solute_said.partition("; ")
        record["said"] += (f" The solute is {solute}, {record['grouped']}"
                           + (f"; {left_out}." if left_out else "."))
    return distances, record


class SoluteImage(Analysis):
    """The solute's closest approach to its own periodic images, per frame.

    Parameters
    ----------
    cutoff_nm : float, optional
        The cutoff the closest approach is checked against, in nm. By
        default the run's own real-space cutoff, read from the study's setup
        record where the orchestrator runs the check (:func:`run_cutoff`),
        else 1.0 nm, said as taken.
    selection : str, optional
        The analyses' solute (the scope's by default, the protein and any
        ligand): a ligand or cofactor in it is checked with the
        macromolecules, which are always checked (:func:`solute_atoms`).
        Heavy atoms only.

    Output
    ------
    ``image_check.dat``: the closest approach in nm, one value per frame;
    the finding in ``options.json`` under ``findings.solute_image``.
    """

    name = "image_check"
    description = "Closest approach to its own periodic image"
    time_series = True
    #: Images exist only in a periodic box.
    requires_periodic_box = True
    #: And there must be a solute to have them.
    requires_solute = True

    def __init__(self, *, cutoff_nm: float | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if cutoff_nm is None:
            self.cutoff_nm, self.cutoff_from = run_cutoff(None)
        else:
            cutoff_nm = float(cutoff_nm)
            if not cutoff_nm > 0:
                raise StudyError(
                    f"`cutoff_nm` is a distance, more than 0 nm; {cutoff_nm:g} was given.",
                    code="analysis.option.out_of_range", option="cutoff_nm",
                    given=cutoff_nm, frames=None)
            self.cutoff_nm = cutoff_nm
            self.cutoff_from = ("set for this check "
                                "(`options: {image_check: {cutoff_nm: ...}}`)")
        self._cutoff_given = cutoff_nm is not None
        self.options.update(cutoff_nm=self.cutoff_nm)

    def use_the_run_s_cutoff(self, study: Path | None) -> None:
        """Check against the run's own cutoff, read from the study's record,
        unless one was set for this check."""
        if not self._cutoff_given:
            self.cutoff_nm, self.cutoff_from = run_cutoff(study)
            self.options.update(cutoff_nm=self.cutoff_nm)

    _traj_for_plot: md.Trajectory | None = None

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        atoms, solute = solute_atoms(traj.topology, self.selection)
        if len(atoms) == 0:
            raise StudyError(
                "The system holds no macromolecule and the analyses' solute "
                f"({self.selection or 'none given'!r}) nothing that is not water, an "
                "ion or a lipid, so there is no solute to check against its images.",
                code="analysis.selection.empty",
                expression=self.selection, role="solute")
        distances, record = solute_image_record(traj, atoms, cutoff_nm=self.cutoff_nm,
                                                cutoff_from=self.cutoff_from,
                                                solute_said=solute)
        self.findings["solute_image"] = record
        # Read by this analysis as by every other.
        self.solute_image = record
        return distances

    def run(self, traj: md.Trajectory):
        self._traj_for_plot = traj
        return super().run(traj)

    def _record_what_the_mean_is_worth(self, traj: md.Trajectory) -> None:
        """No mean: a mean of the closest approach over the equilibrated
        frames says nothing the check is for. Its findings are the closest
        approach and the frames under the cutoff and under 0.5 nm, and its
        figure the series with those two lines."""
        self.findings.pop("mean", None)

    def _axis(self) -> tuple[np.ndarray, str]:
        """The time axis, in ps while the whole run is under a nanosecond,
        as every page says a time; else in ns, or by frame without a clock."""
        x, label = self.frame_axis(self._traj_for_plot)
        if label == "Time (ns)" and x.size and float(np.nanmax(np.abs(x))) < 1.0:
            return np.asarray(x, dtype=float) * 1000.0, "Time (ps)"
        return x, label

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        x = (self._axis()[0]
             if self._traj_for_plot is not None else np.arange(result.shape[0]))
        ax.plot(x, result, linewidth=1.2)
        ax.axhline(self.cutoff_nm, color=colour("GUIDE"), linestyle="--", linewidth=0.9,
                   label=f"cutoff, {self.cutoff_nm:g} nm")
        ax.axhline(CLOSE_NM, color=colour("GUIDE"), linestyle=":", linewidth=0.9,
                   label=f"{CLOSE_NM:g} nm")
        ax.set_ylim(bottom=0)
        ax.legend(loc="best", fontsize=7.5)

    def default_xlabel(self) -> str | None:
        if self._traj_for_plot is None:
            return "Frame"
        return self._axis()[1]

    def default_ylabel(self) -> str | None:
        return "Closest approach to its image (nm)"


register_analysis(SoluteImage.name, SoluteImage)
