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

**Heavy atoms, and the ligand's symmetry.** By default the RMSD is taken over
the ligand's heavy atoms, which is the convention docking and pose
comparisons use: hydrogens add positions that follow their heavy atoms and
weight a group by how many hydrogens it carries. And a symmetric ligand has
more than one way to match its atoms to the reference, so a pose that is the
same pose with its atoms relabelled is not a displacement: a benzene turned
by 60 degrees about its axis lies exactly where it was, and read 0.200 nm
over all atoms and 0.139 nm over its carbons. Each frame's RMSD is therefore
the smallest over the automorphisms of the ligand's bond graph (atoms
matched to atoms of the same element whose bonds match), with no refitting:
the receptor fit stays the only superposition. The automorphisms come from
the topology's bonds; a ligand with none recorded is compared with its atoms
as labelled, and that is said.

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

    **The anchor is the alignment atom nearest the ligand in the first
    frame**, by minimum image. The first frame's displacement is the one
    thing the walk cannot correct, so it must be the true one, and the
    minimum image is the true one only for a separation under half the box.
    The first alignment atom was used before: on an elongated receptor that
    is the N-terminal alpha carbon, 4.4 nm from a ligand in a 6.8 nm box, so
    the first frame took the wrong copy of the ligand and a rigid complex
    tumbling in its box read a ligand RMSD of up to 13.3 nm. From the
    nearest atom, a ligand in contact with its receptor is a few tenths of a
    nanometre away and the first frame is right, with each of its atoms
    placed whole beside that atom however the file wrapped them.

    Returns ``None`` where the trajectory carries no unit cell.
    """
    if traj.unitcell_vectors is None:
        return None

    ligand_idx = np.asarray(ligand_idx)
    anchor = _nearest_anchor(traj, ligand_idx, anchor_idx)

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


def _nearest_anchor(traj, ligand_idx, anchor_idx) -> int:
    """The anchor atom closest to any ligand atom in the first frame, by
    minimum image."""
    anchors = np.asarray(anchor_idx, dtype=int)
    ligand = np.asarray(ligand_idx, dtype=int)
    pairs = np.column_stack([np.repeat(anchors, ligand.size),
                             np.tile(ligand, anchors.size)])
    distances = md.compute_distances(traj[0], pairs, periodic=True)[0]
    return int(pairs[int(np.argmin(distances)), 0])


def ligand_in_the_receptor_frame(traj, ligand_idx, align_idx, ref: int) -> np.ndarray:
    """The ligand's coordinates after fitting each frame's receptor onto the
    reference frame's, followed across periodic faces, in nm.

    The ligand is followed first, while the box still describes the frame
    (:func:`_followed_across_the_boundary`), and then carried by the rigid
    transform that fitted the receptor. Without a unit cell the fitted
    coordinates are taken as written. Shared by the ligand's RMSD and RMSF,
    so the two read the same ligand.
    """
    followed = _followed_across_the_boundary(traj, ligand_idx, align_idx)
    aligned = superposed(traj, frame=ref, atom_indices=align_idx)
    if followed is None:
        return np.asarray(aligned.xyz[:, ligand_idx, :], dtype=np.float64)
    return _carried_by(traj.xyz[:, align_idx, :],
                       aligned.xyz[:, align_idx, :], followed)


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


#: The most automorphisms of the ligand's graph a symmetry-corrected RMSD
#: is taken over. A heavy-atom graph of a drug-like ligand has a handful (a
#: phenyl ring 2, a tert-butyl group 6, benzene 12); with hydrogens included,
#: every methyl multiplies them by six, and the count is capped and the cap
#: recorded rather than letting the search run without bound.
MAX_AUTOMORPHISMS = 10_000


def _bond_graph(topology, atoms) -> list[set[int]] | None:
    """Neighbours of each of ``atoms``, by position in ``atoms``, from the
    topology's bonds among them; None where none of them is bonded."""
    position = {int(a): k for k, a in enumerate(atoms)}
    neighbours: list[set[int]] = [set() for _ in atoms]
    any_bond = False
    for first, second in topology.bonds:
        i, j = position.get(first.index), position.get(second.index)
        if i is None or j is None or i == j:
            continue
        neighbours[i].add(j)
        neighbours[j].add(i)
        any_bond = True
    return neighbours if any_bond else None


def automorphisms(topology, atoms, limit: int = MAX_AUTOMORPHISMS) -> tuple[np.ndarray, bool] | None:
    """The permutations of ``atoms`` that preserve elements and bonds.

    Returns ``(permutations, capped)``: an array of shape (n_found, n_atoms)
    whose rows map position i to position ``row[i]``, the identity first,
    and whether the search stopped at ``limit`` (or after ``200 * limit``
    candidates tried). None where the atoms carry
    no bonds, since every relabelling of an unbonded set would then count.

    Atoms are first given classes by colour refinement (element, then the
    multiset of the neighbours' classes, until nothing splits), which an
    automorphism must preserve; a backtracking search then extends a partial
    map one atom at a time, in breadth-first order so each new atom has a
    mapped neighbour, keeping only maps under which every pair of mapped
    atoms is bonded exactly when its image is.
    """
    atoms = [int(a) for a in atoms]
    n = len(atoms)
    neighbours = _bond_graph(topology, atoms)
    if neighbours is None:
        return None
    elements = []
    for a in atoms:
        element = topology.atom(a).element
        elements.append(getattr(element, "symbol", None) or topology.atom(a).name)

    names = {name: k for k, name in enumerate(sorted(set(elements)))}
    colour = [names[e] for e in elements]
    while True:
        signature = [(colour[i], tuple(sorted(colour[j] for j in neighbours[i])))
                     for i in range(n)]
        relabel = {sig: k for k, sig in enumerate(sorted(set(signature)))}
        refined = [relabel[sig] for sig in signature]
        if len(set(refined)) == len(set(colour)):
            colour = refined
            break
        colour = refined

    # Breadth-first from the atom of the rarest class, component by component.
    order: list[int] = []
    seen: set[int] = set()
    by_rarity = sorted(range(n), key=lambda i: (colour.count(colour[i]), i))
    for start in by_rarity:
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            i = queue.pop(0)
            order.append(i)
            for j in sorted(neighbours[i], key=lambda k: (colour.count(colour[k]), k)):
                if j not in seen:
                    seen.add(j)
                    queue.append(j)

    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(colour[i], []).append(i)
    found: list[list[int]] = []
    image = [-1] * n
    used = [False] * n
    capped = False
    # Candidates tried, bounded too, so a graph that refinement cannot split
    # and that has few automorphisms cannot search without end.
    budget = [200 * limit]

    def extend(depth: int) -> bool:
        nonlocal capped
        if depth == n:
            found.append(list(image))
            if len(found) >= limit:
                capped = True
                return False
            return True
        i = order[depth]
        # The images of i's mapped neighbours, which must be exactly the
        # candidate's mapped neighbours for bonds to be kept both ways.
        wanted = {image[j] for j in neighbours[i] if image[j] >= 0}
        for candidate in members[colour[i]]:
            if used[candidate]:
                continue
            budget[0] -= 1
            if budget[0] < 0:
                capped = True
                return False
            if {k for k in neighbours[candidate] if used[k]} != wanted:
                continue
            image[i] = candidate
            used[candidate] = True
            carry_on = extend(depth + 1)
            used[candidate] = False
            image[i] = -1
            if not carry_on:
                return False
        return True

    import sys
    depth_needed = n + 100
    previous = sys.getrecursionlimit()
    if previous < depth_needed:
        sys.setrecursionlimit(depth_needed)
    try:
        extend(0)
    finally:
        if previous < depth_needed:
            sys.setrecursionlimit(previous)

    permutations = np.asarray(found, dtype=np.int64).reshape(-1, n)
    identity = np.arange(n)
    is_identity = np.all(permutations == identity, axis=1)
    if not is_identity.any():
        permutations = np.vstack([identity, permutations[:-1]]) if capped else np.vstack([identity, permutations])
    else:
        first = int(np.argmax(is_identity))
        rest = np.delete(permutations, first, axis=0)
        permutations = np.vstack([identity, rest])
    return permutations, capped


def symmetric_rmsd(xyz: np.ndarray, reference: np.ndarray,
                   permutations: np.ndarray) -> np.ndarray:
    """Per frame, the smallest RMSD over relabellings of the reference.

    RMSD_f = min over p of sqrt( (1/n) sum_i |x_f,i - r_p(i)|^2 ), with no
    refitting: the coordinates are already in the receptor's frame. Taken
    through the (n, n) matrix of squared distances between each frame's
    atoms and the reference's, so each permutation costs a gather.
    """
    xyz = np.asarray(xyz, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    permutations = np.asarray(permutations, dtype=np.int64)
    n_frames, n_atoms, _ = xyz.shape
    rows = np.arange(n_atoms)
    best = np.empty(n_frames)
    # Chunks keep each gathered array near ten million numbers.
    frames_at_once = max(1, int(1e7 // max(1, n_atoms * n_atoms)))
    for first in range(0, n_frames, frames_at_once):
        block = xyz[first:first + frames_at_once]
        squared = ((block[:, :, None, :] - reference[None, None, :, :]) ** 2).sum(axis=-1)
        lowest = np.full(len(block), np.inf)
        maps_at_once = max(1, int(1e7 // max(1, len(block) * n_atoms)))
        for start in range(0, len(permutations), maps_at_once):
            maps = permutations[start:start + maps_at_once]
            totals = squared[:, rows[None, :], maps].sum(axis=-1)
            lowest = np.minimum(lowest, totals.min(axis=1))
        best[first:first + len(block)] = lowest
    return np.sqrt(best / n_atoms)


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
    include_hydrogens : bool, default False
        Whether the RMSD is over every ligand atom rather than its heavy
        atoms. Off by default, as in docking and pose comparisons: a
        hydrogen follows the atom it is bonded to, and counting it weights a
        group by how many hydrogens it carries. A benzene turned by 60
        degrees reads 0.200 nm over all its atoms and 0.139 nm over its
        carbons without the symmetry correction below.
    symmetry_corrected : bool, default True
        Whether each frame's RMSD is the smallest over the automorphisms of
        the ligand's bond graph (relabellings that keep elements and bonds),
        so a symmetric ligand that turned onto itself reads as unmoved. No
        refitting is done. Taken over at most 10,000 automorphisms; where a
        ligand has more, the cap and the count are recorded. A ligand with
        no bonds in its topology is compared as labelled, and that is
        recorded.
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
        include_hydrogens: bool = False,
        symmetry_corrected: bool = True,
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
        self.include_hydrogens: bool = bool(include_hydrogens)
        self.symmetry_corrected: bool = bool(symmetry_corrected)
        self.options.update(
            ligand_resname=self.ligand_resname,
            align_selection=self.align_selection,
            ref=self.ref,
            include_hydrogens=self.include_hydrogens,
            symmetry_corrected=self.symmetry_corrected,
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
        ligand_xyz = ligand_in_the_receptor_frame(traj, ligand_idx, align_idx, ref)

        heavy = [int(i) for i in ligand_idx
                 if traj.topology.atom(int(i)).element is None
                 or traj.topology.atom(int(i)).element.symbol != "H"]
        if self.include_hydrogens or not heavy:
            measured_atoms = [int(i) for i in ligand_idx]
        else:
            measured_atoms = heavy
        position = {int(a): k for k, a in enumerate(ligand_idx)}
        columns = [position[a] for a in measured_atoms]
        ligand_xyz = ligand_xyz[:, columns, :]

        # RMSD of the LIGAND atoms on the aligned coordinates, vs the
        # reference frame's ligand coordinates. No further alignment.
        ref_xyz = ligand_xyz[ref]
        disps = ligand_xyz - ref_xyz
        rmsd_nm = np.sqrt(np.mean(np.sum(disps * disps, axis=2), axis=1))
        self._record_the_atoms(traj, measured_atoms, len(ligand_idx))
        if self.symmetry_corrected:
            rmsd_nm = self._corrected_for_symmetry(
                traj, measured_atoms, ligand_xyz, ref_xyz, rmsd_nm)

        self._resolved_ref = ref
        measured = distance_to_the_site(traj, heavy or list(ligand_idx), ref)
        self._site_distance = None if measured is None else measured[0]
        self._record_where_the_ligand_was(traj, measured)
        return rmsd_nm.astype(np.float64)

    def _record_the_atoms(self, traj: md.Trajectory, measured_atoms, n_ligand: int) -> None:
        """Which ligand atoms the RMSD is over, in the findings."""
        n_heavy = sum(1 for a in measured_atoms
                      if getattr(traj.topology.atom(a).element, "symbol", None) != "H")
        if len(measured_atoms) == n_ligand and not self.include_hydrogens and n_heavy < n_ligand:
            said = ("The ligand has no heavy atoms, so the RMSD is over all of "
                    f"its {n_ligand} atoms.")
        elif len(measured_atoms) == n_ligand:
            said = f"Over all {n_ligand} ligand atoms."
        else:
            said = (f"Over the ligand's {len(measured_atoms)} heavy atoms; its "
                    f"{n_ligand - len(measured_atoms)} hydrogens are left out "
                    "(include_hydrogens: true counts them).")
        self.findings["atoms"] = {"n_atoms": len(measured_atoms),
                                  "n_ligand_atoms": int(n_ligand), "said": said}

    def _corrected_for_symmetry(self, traj, measured_atoms, ligand_xyz, ref_xyz,
                                as_labelled: np.ndarray) -> np.ndarray:
        """Each frame's RMSD at the best relabelling, with the record of it."""
        found = automorphisms(traj.topology, measured_atoms)
        if found is None:
            self.findings["symmetry"] = {
                "applied": False,
                "said": ("The ligand's topology records no bonds between the "
                         "atoms compared, so its symmetry is unknown and the "
                         "RMSD is taken with its atoms as labelled. A "
                         "symmetric ligand that turned onto itself reads as "
                         "having moved.")}
            return as_labelled
        permutations, capped = found
        corrected = symmetric_rmsd(ligand_xyz, ref_xyz, permutations)
        record: dict[str, Any] = {
            "applied": True,
            "automorphisms": int(len(permutations)),
            "capped": bool(capped),
            "largest_correction_nm": float(np.max(as_labelled - corrected)),
        }
        if capped:
            record["said"] = (
                f"The ligand's graph has more than {MAX_AUTOMORPHISMS:,} "
                "automorphisms, and the RMSD is the smallest over the first "
                f"{MAX_AUTOMORPHISMS:,} found, so it may read high where the "
                "ligand turned onto itself by one not among them.")
        elif len(permutations) == 1:
            record["said"] = "The ligand has no symmetry: one way to match its atoms."
        else:
            record["said"] = (
                f"Each frame's RMSD is the smallest over the ligand's "
                f"{len(permutations)} automorphisms, so a pose that is the "
                "same pose with its atoms relabelled reads as unmoved.")
        self.findings["symmetry"] = record
        return corrected

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
