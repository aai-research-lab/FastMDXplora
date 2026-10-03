"""Molecules made whole across the periodic boundary, every frame at once.

The same imaging as MDTraj's ``Trajectory.image_molecules``, step for step,
and computed over all frames together rather than frame by frame:

1. Each molecule is made whole along its bonds: every atom is moved to the
   periodic copy nearest the atom it is bonded to, walking a breadth-first
   tree of the bonds out from the molecule's first atom. Atoms at the same
   depth of the tree are moved together, so a protein of a few thousand
   atoms takes a few hundred array operations for every frame at once.
2. The anchors are placed together: the first stays where it is, and each
   other anchor, nearest the first one, takes the periodic copy that brings
   its closest contact with an anchor already placed into that contact's
   nearest image. MDTraj finds each closest contact by comparing every atom
   of one anchor with every atom of the other (twenty-two million distances
   a frame for a dimer of 3,341 atoms a chain, about a quarter of a second);
   here a k-d tree answers it, looking only at the periodic copies whose
   bounding spheres could hold a nearer contact than the nearest found.
3. Everything is moved so that the anchors' centre is the box's centre, and
   every other molecule is moved whole so that its centre lies in the box.

The rounding is MDTraj's own (box vectors in OpenMM's reduced form, rounded
along the third vector, then the second, then the first) and in single
precision, and the atoms are summed in MDTraj's order, so the coordinates
are MDTraj's bit for bit where MDTraj's compiled code rounds each operation,
as on x86-64. On arm64 its compiler fuses a multiplication and an addition
into one rounding, and in a slanted box the two agree within 1e-5 nm.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np

FRAMES_AT_ONCE = 64
"""Frames moved together in step 3, which gathers every moved atom's coordinates."""


def image_molecules(
    xyz: np.ndarray,
    box: np.ndarray,
    n_atoms: int,
    bonds: np.ndarray,
    anchors: Sequence[Iterable[int]],
    others: Sequence[Iterable[int]],
) -> None:
    """Image ``xyz`` (frames, atoms, 3) in place, as MDTraj's ``image_molecules``.

    ``box`` is the box vectors as rows (frames, 3, 3); ``bonds`` the atom
    index pairs (n, 2); ``anchors`` the molecules centred and placed
    together, at least one, and ``others`` every other molecule, each as atom
    indices in the order MDTraj's molecules give them
    (:func:`image_trajectory` finds them so).
    """
    from fastmdxplora.analysis.loading import TrajectoryLoadError

    if xyz.ndim != 3 or xyz.shape[1] != n_atoms:
        raise TrajectoryLoadError(
            f"Coordinates of shape {xyz.shape} are not frames of {n_atoms} atoms.",
            code="analysis.trajectory.unreadable",
            reason="the coordinates are not the topology's atoms")
    box = np.ascontiguousarray(box, dtype=np.float32)
    bonds = np.asarray(bonds, dtype=np.int64).reshape(-1, 2)
    if len(bonds) == 0:
        # As MDTraj, which raises here too: without bonds every atom is a
        # molecule of its own, and wrapping each would scatter a protein.
        raise TrajectoryLoadError(
            "The topology has no bonds, so no molecule can be made whole.",
            code="analysis.system.inapplicable", analysis="imaging",
            reason="the topology has no bonds")
    # Atoms kept in the order given, which is the order MDTraj sums them in.
    anchor_atoms = [np.fromiter((int(a) for a in m), dtype=np.int64) for m in anchors]
    anchor_atoms = [m for m in anchor_atoms if len(m)]
    if not anchor_atoms:
        # MDTraj centres on the mean of no atoms, and every coordinate
        # becomes not a number.
        raise TrajectoryLoadError(
            "No molecule was named to centre the others on.",
            code="analysis.system.inapplicable", analysis="imaging",
            reason="no anchor molecule")
    other_atoms = [np.fromiter((int(a) for a in m), dtype=np.int64) for m in others]
    other_atoms = [m for m in other_atoms if len(m)]

    _make_whole(xyz, box, n_atoms, bonds)
    _place_anchors(xyz, box, anchor_atoms)
    centre = xyz[:, np.concatenate(anchor_atoms), :].mean(axis=1)        # (F, 3)
    _centre_and_wrap(xyz, box, centre, other_atoms)


def _reduced_offset(delta: np.ndarray, box: np.ndarray) -> np.ndarray:
    """The lattice vector nearest ``delta`` (..., 3), rounded as MDTraj rounds.

    ``box`` broadcasts against ``delta`` with the box vectors as its last two
    axes. Along the third vector, then the second, then the first, each
    rounded on the diagonal element, which is exact for OpenMM's reduced
    boxes for any vector shorter than half the box.
    """
    v0, v1, v2 = box[..., 0, :], box[..., 1, :], box[..., 2, :]
    offset = v2 * np.round(delta[..., 2:3] / v2[..., 2:3])
    offset = offset + v1 * np.round((delta[..., 1:2] - offset[..., 1:2]) / v1[..., 1:2])
    offset = offset + v0 * np.round((delta[..., 0:1] - offset[..., 0:1]) / v0[..., 0:1])
    return offset.astype(np.float32, copy=False)


def _bond_tree(n_atoms: int, bonds: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """The bonds as a breadth-first forest, one root per molecule.

    Returned as levels, each the (children, parents) at one depth below the
    roots, so a level's parents are all placed before its children. The root
    of each molecule is its lowest-indexed atom, as MDTraj's walk begins.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, connected_components

    graph = coo_matrix((np.ones(len(bonds), dtype=np.int8), (bonds[:, 0], bonds[:, 1])),
                       shape=(n_atoms, n_atoms)).tocsr()
    n_parts, labels = connected_components(graph, directed=False)
    # The lowest index in each molecule, joined to one extra node so a single
    # walk reaches every molecule.
    roots = np.full(n_parts, n_atoms, dtype=np.int64)
    np.minimum.at(roots, labels, np.arange(n_atoms))
    joined = coo_matrix(
        (np.ones(len(bonds) + n_parts, dtype=np.int8),
         (np.concatenate([bonds[:, 0], np.full(n_parts, n_atoms)]),
          np.concatenate([bonds[:, 1], roots]))),
        shape=(n_atoms + 1, n_atoms + 1)).tocsr()
    order, parent = breadth_first_order(joined, n_atoms, directed=False,
                                        return_predecessors=True)
    depth = np.zeros(n_atoms + 1, dtype=np.int64)
    parent_list = parent.tolist()
    depth_list = depth.tolist()
    for node in order.tolist()[1:]:
        depth_list[node] = depth_list[parent_list[node]] + 1
    depth = np.asarray(depth_list[:n_atoms], dtype=np.int64)
    parent = parent[:n_atoms].astype(np.int64)
    moved = np.flatnonzero(depth >= 2)          # depth 1 is a root
    moved = moved[np.argsort(depth[moved], kind="stable")]
    levels = []
    if len(moved):
        cuts = np.flatnonzero(np.diff(depth[moved])) + 1
        for children in np.split(moved, cuts):
            levels.append((children, parent[children]))
    return levels


def _make_whole(xyz: np.ndarray, box: np.ndarray, n_atoms: int, bonds: np.ndarray) -> None:
    frame_box = box[:, None, :, :]
    for children, parents in _bond_tree(n_atoms, bonds):
        delta = xyz[:, children, :] - xyz[:, parents, :]
        xyz[:, children, :] -= _reduced_offset(delta, frame_box)


class _Group:
    """One anchor's atoms in one frame: coordinates, centre, reach and k-d tree."""

    def __init__(self, coordinates: np.ndarray) -> None:
        from scipy.spatial import cKDTree

        self.xyz = coordinates.astype(np.float64)
        self.centre = self.xyz.mean(axis=0)
        self.radius = float(np.sqrt(((self.xyz - self.centre) ** 2).sum(axis=1)).max())
        self.tree = cKDTree(self.xyz)


_NEAR_CELLS = np.array(list(np.ndindex(5, 5, 5)), dtype=np.float64) - 2.0


def _closest_contact(first: _Group, second: _Group, box: np.ndarray) -> tuple[int, int]:
    """The closest pair, one atom of each group, over every periodic copy.

    Each periodic copy of ``second`` within two cells of the one nearest by
    centre is looked at, nearest first, only while its bounding sphere could
    hold a pair nearer than the nearest found.
    """
    box64 = box.astype(np.float64)
    between = second.centre - first.centre
    guess = -np.rint(between @ np.linalg.inv(box64))
    shifts = (guess + _NEAR_CELLS) @ box64                        # (125, 3)
    apart = np.sqrt(((between + shifts) ** 2).sum(axis=1))
    reach = first.radius + second.radius
    best, pair = np.inf, (0, 0)
    for index in np.argsort(apart, kind="stable"):
        if apart[index] - reach >= best:
            break
        distance, nearest = first.tree.query(second.xyz + shifts[index], k=1,
                                             distance_upper_bound=best)
        j = int(np.argmin(distance))
        if distance[j] < best:
            best, pair = float(distance[j]), (int(nearest[j]), j)
    return pair


def _place_anchors(xyz: np.ndarray, box: np.ndarray, anchors: list[np.ndarray]) -> None:
    """Each anchor to the copy that brings it into contact with those placed.

    As MDTraj: anchors are taken in order of their closest contact with the
    first, each placed against the anchor already placed that it is nearest,
    by rounding its closest contact with that anchor to the nearest image.
    """
    n = len(anchors)
    if n < 2:
        return
    for frame in range(len(xyz)):
        positions = xyz[frame]
        groups = [_Group(positions[anchor]) for anchor in anchors]
        distance = np.zeros((n, n))
        contact: dict[tuple[int, int], tuple[int, int]] = {}
        for one in range(n):
            for other in range(one):
                i, j = _closest_contact(groups[one], groups[other], box[frame])
                a1, a2 = int(anchors[one][i]), int(anchors[other][j])
                delta = positions[a1] - positions[a2]
                delta = delta - _reduced_offset(delta, box[frame])
                distance[one, other] = distance[other, one] = float(np.sqrt(delta @ delta))
                # The first atom always in the higher-numbered anchor.
                contact[(one, other)] = contact[(other, one)] = (a1, a2)
        used, available = [0], list(range(1, n))
        while available:
            chosen = min(available, key=lambda m: (distance[0, m], available.index(m)))
            nearest_to = min(used, key=lambda m: (distance[chosen, m], used.index(m)))
            a1, a2 = contact[(chosen, nearest_to)]
            if chosen < nearest_to:
                a1, a2 = a2, a1
            # a1 in the anchor being placed, a2 in the one it is placed against.
            offset = _reduced_offset(positions[a1] - positions[a2], box[frame])
            positions[anchors[chosen]] -= offset
            used.append(chosen)
            available.remove(chosen)


def _centre_and_wrap(xyz: np.ndarray, box: np.ndarray, centre: np.ndarray,
                     others: list[np.ndarray]) -> None:
    """The anchors' centre to the box's centre, every other molecule into the box."""
    diagonal = np.diagonal(box, axis1=1, axis2=2)                 # (F, 3)
    xyz += (0.5 * diagonal - centre)[:, None, :].astype(np.float32)
    if not others:
        return
    atoms = np.concatenate(others)
    sizes = np.fromiter((len(m) for m in others), dtype=np.int64, count=len(others))
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    # Each molecule's atoms summed one after another in its own order, as
    # MDTraj sums them: the j-th atom of every molecule that has one, j by j.
    molecule = np.repeat(np.arange(len(others)), sizes)
    place = np.arange(len(atoms)) - np.repeat(starts, sizes)
    by_place = np.split(np.argsort(place, kind="stable"),
                        np.cumsum(np.bincount(place))[:-1])
    for first in range(0, len(xyz), FRAMES_AT_ONCE):
        chunk = slice(first, first + FRAMES_AT_ONCE)
        gathered = xyz[chunk, atoms, :]
        centres = np.zeros((gathered.shape[0], len(others), 3), dtype=np.float32)
        for slots in by_place:
            centres[:, molecule[slots], :] += gathered[:, slots, :]
        centres /= sizes.astype(np.float32)[None, :, None]           # (f, M, 3)
        frame_box = box[chunk][:, None, :, :]
        v0, v1, v2 = frame_box[..., 0, :], frame_box[..., 1, :], frame_box[..., 2, :]
        wrapped = centres - v2 * np.floor(centres[..., 2:3] / v2[..., 2:3])
        wrapped = wrapped - v1 * np.floor(wrapped[..., 1:2] / v1[..., 1:2])
        wrapped = wrapped - v0 * np.floor(wrapped[..., 0:1] / v0[..., 0:1])
        move = np.repeat((wrapped - centres).astype(np.float32), sizes, axis=1)
        xyz[chunk, atoms, :] = gathered + move


MOST_ANCHORS = 32
"""Anchors placed here; more go to MDTraj, which compares them all in C.

The closest contact of every pair of anchors is looked for, each pair a few
array operations whatever its size: cheaper than MDTraj's for a few large
chains, dearer for hundreds of small molecules anchored one by one.
"""


def image_trajectory(trajectory, anchor_molecules, other_molecules=None) -> None:
    """Image an MDTraj ``Trajectory`` in place; molecules as sets of its atoms."""
    if len(anchor_molecules) > MOST_ANCHORS:
        trajectory.image_molecules(inplace=True, anchor_molecules=anchor_molecules,
                                   other_molecules=other_molecules)
        return
    topology = trajectory.topology
    if other_molecules is None:
        # MDTraj's own list, in its order: each molecule's atoms are summed in
        # the order its set gives them, and the centres are MDTraj's to the bit.
        other_molecules = [m for m in topology.find_molecules() if m not in anchor_molecules]
    bonds = np.array([[a.index, b.index] for a, b in topology.bonds], dtype=np.int64)
    anchors = [[a.index for a in m] for m in anchor_molecules]
    others = [[a.index for a in m] for m in other_molecules]
    xyz = trajectory.xyz
    image_molecules(xyz, trajectory.unitcell_vectors, topology.n_atoms, bonds,
                    anchors, others)
