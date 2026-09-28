"""Find the bilayer a structure belongs in, rather than assuming one.

Orienting by principal axes asks which way the protein is longest. That is
the right question for a lone transmembrane bundle and the wrong one for
anything carrying a soluble domain: 6B73's longest axis runs through its G
protein, and 2RH1's through its T4 lysozyme. What decides where a bilayer
sits is where the protein's lipid-facing surface is apolar, so that is what
is searched for.

The approach is the one PPM takes, simplified: a slab the thickness of a
bilayer's hydrophobic core is placed, over every direction, centre and
thickness, where it buries the most apolar surface and the least polar.
Two refinements carry it from a sketch to a measurement:

- **Polar surface is not all equally unwelcome.** A charged group in a
  hydrocarbon core costs several times what a neutral polar one does, and a
  backbone oxygen or nitrogen in a transmembrane helix is hydrogen-bonded in
  the helix and costs little. Weighted alike, a slab lying across a compact
  helix bundle scored as well as the true one, and it was chosen for 1U19,
  1AFO, 2RLF, 4DKL and 6B73.
- **Only surface a lipid can reach counts.** A porin's lumen is lined with
  charged residues and filled with water; counted as surface the bilayer
  covers, it pushed the fit sideways for every trimeric porin. Across each
  layer of a candidate slab, the space reachable from outside the protein is
  found by flood fill, and surface facing an enclosed pore is left out.

Measured against the orientations OPM publishes (the normal from each file,
the membrane centre at its origin, the hydrophobic thickness from its
header), after a random rotation and shift of each structure: 40 structures
used while developing the weights (helical bundles and GPCRs with fusion
partners and G proteins, channels, transporters, beta-barrels and trimeric
porins, complexes to 4,461 residues) and 25 held out, in three and two
random frames respectively. Of the 170 fits every normal came within 21
degrees of OPM's and all but two within 15 (1AFO, two crossing helices, at
17 once; 4EIY, a receptor with a fusion partner whose optimum is flat, at 20
once); the median was 3 to 4 degrees, the membrane centre 0.1 nm from OPM's
at the median, and the thickness 0.2 nm more than OPM's on average. The best
slab of 14 soluble proteins buries at most 5.4 nm2 of net apolar surface,
and of the 65 membrane proteins at least 18.5.

    Lomize et al., Positioning of proteins in membranes: a computational
    approach, Protein Sci 2006.
    Lomize et al., OPM database and PPM web server, Nucleic Acids Res 2012.

This is not PPM. PPM optimises a transfer free energy against an anisotropic
solvent model and reports it in kcal/mol; this keeps the shape of the idea
with three classes of polar surface and a flat slab. It cannot tell which
way up a protein sits, which a bilayer of one lipid does not care about. An
oriented structure from OPM is still a good input, and is kept as it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Half-thicknesses of the hydrophobic core considered, in nanometres. OPM's
#: hydrophobic thicknesses across the validation set run from 2.3 nm (porins)
#: to 3.7 nm; a little either side is allowed so the edge is never the answer
#: by construction.
MINIMUM_HALF_THICKNESS_NM = 0.9
MAXIMUM_HALF_THICKNESS_NM = 2.2

#: How finely to step the slab's centre and half-thickness, in nanometres.
SLAB_STEP_NM = 0.1

#: Directions tried in the first, coarse pass, over a hemisphere (a slab and
#: its mirror are the same slab): about four degrees apart.
DIRECTION_COUNT = 600

#: The weights of surface inside the slab, per nm2: apolar surface counts
#: for the slab, and each class of polar surface against it. A charged group
#: buried in a hydrocarbon core costs several times a neutral polar one; a
#: backbone N or O in a transmembrane helix is hydrogen-bonded in the helix.
APOLAR_WEIGHT = 1.0
BACKBONE_POLAR_WEIGHT = 0.5
SIDE_CHAIN_POLAR_WEIGHT = 2.0
CHARGED_WEIGHT = 5.0

#: The first pass proposes directions under three weightings, from apolar
#: surface alone to the final weights: the first pass counts every surface,
#: a lumen's included, so the heavier weightings alone never proposed the
#: true normal of a porin (or of a barrel whose lumen is lined with charge),
#: and the lighter one alone missed a G protein complex. The final score,
#: which counts only what a lipid can reach, decides among them.
PROPOSAL_WEIGHTS = ((1.0, 0.0, 0.0, 0.0),
                    (1.0, 0.5, 1.0, 3.0),
                    (APOLAR_WEIGHT, BACKBONE_POLAR_WEIGHT,
                     SIDE_CHAIN_POLAR_WEIGHT, CHARGED_WEIGHT))

#: Directions kept from each proposal, at least this far apart, and how far
#: around each the final search looks.
CANDIDATES_PER_PROPOSAL = 8
CANDIDATE_SEPARATION_DEG = 15.0
REFINEMENT_RADIUS_DEG = 12.0

#: The net apolar surface (weighted score, nm2) a membrane protein's best
#: slab buries. Measured: at least 18.5 across 65 membrane proteins, at most
#: 5.4 across 14 soluble ones. The line sits between with room either side.
MEMBRANE_SCORE_FLOOR_NM2 = 10.0

#: Residue atoms carrying a formal charge at neutral pH.
_CHARGED_ATOMS = frozenset({
    ("LYS", "NZ"), ("ARG", "NE"), ("ARG", "NH1"), ("ARG", "NH2"),
    ("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2"),
})
_BACKBONE_POLAR = frozenset({"N", "O"})
_APOLAR_ELEMENTS = frozenset({"C", "S", "SE"})

APOLAR, BACKBONE_POLAR, SIDE_CHAIN_POLAR, CHARGED = 0, 1, 2, 3


@dataclass(frozen=True)
class SlabFit:
    """Where a bilayer would sit, and how well it fits."""

    normal: Any
    """Unit vector along the membrane normal, in the input's frame."""

    centre_nm: float
    """Where the slab's midplane cuts that axis, from ``origin_nm``."""

    half_thickness_nm: float
    """Half the hydrophobic thickness."""

    buried_hydrophobic_nm2: float
    """Apolar surface the slab covers."""

    buried_polar_nm2: float
    """Polar surface it covers, which a real bilayer keeps small."""

    score_nm2: float
    """The weighted apolar-minus-polar surface inside the slab: what is
    maximised."""

    origin_nm: Any = None
    """The point ``centre_nm`` is measured from along the normal (the
    centroid of the atoms fitted), in the input's frame."""

    details: dict = field(default_factory=dict, compare=False)

    @property
    def thickness_nm(self) -> float:
        return 2.0 * self.half_thickness_nm

    @property
    def hydrophobic_fraction(self) -> float:
        """Of the surface the slab covers, how much is apolar."""
        total = self.buried_hydrophobic_nm2 + self.buried_polar_nm2
        if total <= 0:
            return 0.0
        return self.buried_hydrophobic_nm2 / total

    @property
    def plane_point(self) -> Any:
        """A point on the membrane's midplane, in the input's frame."""
        import numpy as np

        origin = np.zeros(3) if self.origin_nm is None else np.asarray(self.origin_nm)
        return origin + self.centre_nm * np.asarray(self.normal, dtype=float)

    @property
    def looks_like_a_membrane_protein(self) -> bool:
        return self.score_nm2 >= MEMBRANE_SCORE_FLOOR_NM2


def hemisphere_directions(count: int = DIRECTION_COUNT) -> Any:
    """Unit vectors spread evenly over a hemisphere.

    A hemisphere rather than a sphere: a slab is unchanged by flipping its
    normal, so searching both halves would do the same work twice.
    """
    import numpy as np

    index = np.arange(count, dtype=float) + 0.5
    z = index / count
    radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    golden = np.pi * (1.0 + 5.0 ** 0.5)
    angle = golden * index
    return np.column_stack(
        [np.cos(angle) * radius, np.sin(angle) * radius, z])


def rotation_onto_z(normal: Any) -> Any:
    """A rotation taking ``normal`` to the z axis.

    Built from a single rotation about the axis perpendicular to both, so
    nothing else about the structure's orientation is disturbed -- the fit
    decides which way is up and no more.
    """
    import numpy as np

    source = np.asarray(normal, dtype=float)
    source = source / np.linalg.norm(source)
    target = np.array([0.0, 0.0, 1.0])

    axis = np.cross(source, target)
    sine = float(np.linalg.norm(axis))
    cosine = float(np.dot(source, target))
    if sine < 1e-12:
        # Already along z, or exactly opposed.
        return np.eye(3) if cosine > 0 else np.diag([1.0, -1.0, -1.0])

    axis = axis / sine
    cross = np.array([
        [0.0, -axis[2], axis[1]],
        [axis[2], 0.0, -axis[0]],
        [-axis[1], axis[0], 0.0],
    ])
    return np.eye(3) + cross * sine + cross @ cross * (1.0 - cosine)


def _best_slabs(centred: Any, gain: Any, cost: Any, directions: Any, *,
                step: float, halves: Any) -> list[tuple]:
    """The best slab along each direction, from sorted cumulative sums:
    every centre and thickness scored by two lookups rather than a pass
    over the atoms. Each entry: (score, centre, half, start, stop, order)."""
    import numpy as np

    out = []
    for normal in directions:
        projections = centred @ normal
        order = np.argsort(projections)
        ordered = projections[order]
        gained = np.concatenate([[0.0], np.cumsum(gain[order])])
        costed = np.concatenate([[0.0], np.cumsum(cost[order])])
        centres = np.arange(ordered[0], ordered[-1] + step, step)
        lower = centres[:, None] - halves[None, :]
        upper = centres[:, None] + halves[None, :]
        start = np.searchsorted(ordered, lower.ravel(), side="left")
        stop = np.searchsorted(ordered, upper.ravel(), side="right")
        scores = (gained[stop] - gained[start]) - (costed[stop] - costed[start])
        # A plateau of equal scores -- a slab that can slide without taking
        # in or letting go of an atom -- is resolved to its middle rather
        # than its first step, which would put the centre at the plateau's
        # low edge.
        tied = np.flatnonzero(scores >= scores.max() - 1e-9)
        if tied.size > 1:
            middle = centres[tied // halves.size].mean()
            best = int(tied[np.argmin(np.abs(centres[tied // halves.size] - middle))])
        else:
            best = int(tied[0])
        out.append((float(scores[best]), float(centres[best // halves.size]),
                    float(halves[best % halves.size]), int(start[best]),
                    int(stop[best]), order))
    return out


def fit_membrane_slab(
    coordinates: Any,
    hydrophobic_area: Any,
    polar_area: Any,
    *,
    directions: Any = None,
    step: float = SLAB_STEP_NM,
    minimum_half: float = MINIMUM_HALF_THICKNESS_NM,
    maximum_half: float = MAXIMUM_HALF_THICKNESS_NM,
) -> SlabFit | None:
    """Fit the slab that buries the most apolar and least polar surface.

    The plain form: one class of polar surface, weighted like the apolar,
    and every surface counted. :func:`fit_membrane` is the form used on a
    protein, which weights charged and backbone surface apart and counts
    only what a lipid can reach.
    """
    import numpy as np

    points = np.asarray(coordinates, dtype=float)
    hydrophobic = np.asarray(hydrophobic_area, dtype=float)
    polar = np.asarray(polar_area, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3:
        return None
    if len(points) != len(hydrophobic) or len(points) != len(polar):
        return None
    if len(points) < 20 or (hydrophobic.sum() + polar.sum()) <= 0:
        return None
    if directions is None:
        directions = hemisphere_directions(1500)
    directions = np.asarray(directions, dtype=float)
    halves = np.arange(minimum_half, maximum_half + 1e-9, step)
    origin = points.mean(axis=0)
    slabs = _best_slabs(points - origin, hydrophobic, polar, directions,
                        step=step, halves=halves)
    best = int(np.argmax([s[0] for s in slabs]))
    score, centre, half, start, stop, order = slabs[best]
    return SlabFit(
        normal=directions[best].copy(), centre_nm=centre, half_thickness_nm=half,
        buried_hydrophobic_nm2=float(hydrophobic[order][start:stop].sum()),
        buried_polar_nm2=float(polar[order][start:stop].sum()),
        score_nm2=score, origin_nm=origin)


def surface_classes(topology: Any) -> Any:
    """Each atom's class: apolar, backbone polar, side-chain polar, charged.

    Carbon, sulfur and selenium are apolar. A nitrogen or oxygen is charged
    where it carries a formal charge at neutral pH (lysine, arginine,
    aspartate, glutamate, a C-terminal carboxylate), backbone polar where it
    is the backbone's, and side-chain polar otherwise.
    """
    import numpy as np

    out = np.zeros(topology.n_atoms, dtype=int)
    for atom in topology.atoms:
        symbol = (getattr(atom.element, "symbol", "") or "").upper()
        if symbol in _APOLAR_ELEMENTS:
            continue
        if (atom.residue.name, atom.name) in _CHARGED_ATOMS or atom.name == "OXT":
            out[atom.index] = CHARGED
        elif atom.name in _BACKBONE_POLAR:
            out[atom.index] = BACKBONE_POLAR
        else:
            out[atom.index] = SIDE_CHAIN_POLAR
    return out


def lipid_facing(coordinates: Any, normal: Any, *, layer_nm: float = 0.3,
                 cell_nm: float = 0.2, reach_nm: float = 0.35,
                 contact_nm: float = 0.5) -> Any:
    """Which atoms a lipid could touch, for a membrane along ``normal``.

    Across each layer perpendicular to the normal, protein atoms (within
    ``reach_nm``, about an atom's radius and a chain's) fill a grid; the
    empty space connected to the edge of the grid is outside the protein,
    and empty space it encloses -- a pore, a lumen -- is not. An atom is
    lipid-facing where outside space lies within ``contact_nm`` of it in its
    layer.
    """
    import numpy as np
    from scipy import ndimage

    rotated = (np.asarray(coordinates, dtype=float)
               - np.asarray(coordinates, dtype=float).mean(axis=0)) @ rotation_onto_z(normal).T
    low = rotated.min(axis=0) - 1.0
    high = rotated.max(axis=0) + 1.0
    shape = (int(np.ceil((high[0] - low[0]) / cell_nm)),
             int(np.ceil((high[1] - low[1]) / cell_nm)))

    def disk(radius_nm: float) -> Any:
        cells = int(np.ceil(radius_nm / cell_nm))
        span = np.arange(-cells, cells + 1)
        return np.add.outer(span ** 2, span ** 2) <= (radius_nm / cell_nm) ** 2

    occupied_disk, contact_disk = disk(reach_nm), disk(contact_nm)
    layers = np.floor((rotated[:, 2] - low[2]) / layer_nm).astype(int)
    cells = np.floor((rotated[:, :2] - low[:2]) / cell_nm).astype(int)
    facing = np.zeros(len(rotated), dtype=bool)
    for layer in np.unique(layers):
        near = np.abs(layers - layer) <= 1
        occupied = np.zeros(shape, dtype=bool)
        occupied[cells[near, 0], cells[near, 1]] = True
        occupied = ndimage.binary_dilation(occupied, structure=occupied_disk)
        labels, _ = ndimage.label(~occupied)
        edge = np.unique(np.concatenate(
            [labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        outside = np.isin(labels, edge[edge > 0])
        reachable = ndimage.binary_dilation(outside, structure=contact_disk)
        mine = layers == layer
        facing[mine] = reachable[cells[mine, 0], cells[mine, 1]]
    return facing


def _protein_heavy_atoms(topology: Any, positions: Any) -> tuple[Any, Any, Any]:
    """The protein's heavy atoms: their indices, coordinates in nm, and a
    topology of them alone. The fit was measured on heavy atoms, as a
    deposited structure has them; hydrogens added by preparation would
    change the surface it sees."""
    import mdtraj as md
    import numpy as np

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass
    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in positions], dtype=float)
    mdtop = topology if isinstance(topology, md.Topology) else md.Topology.from_openmm(topology)
    keep = [a.index for a in mdtop.atoms
            if a.residue.is_protein and a.element is not None
            and a.element.symbol != "H"]
    if not keep:
        return np.array([], dtype=int), np.zeros((0, 3)), None
    return np.asarray(keep), coordinates[keep], mdtop.subset(keep)


def _surface(subset: Any, coordinates: Any) -> Any:
    import mdtraj as md
    import numpy as np

    trajectory = md.Trajectory(coordinates[None, :, :].astype(np.float32), subset)
    return md.shrake_rupley(trajectory, mode="atom")[0].astype(float)


def _weighted(classes: Any, sasa: Any, weights: tuple, mask: Any = 1.0) -> tuple[Any, Any]:
    import numpy as np

    apolar, backbone, side_chain, charged = weights
    gain = np.where(classes == APOLAR, apolar, 0.0) * sasa * mask
    cost = (np.where(classes == BACKBONE_POLAR, backbone, 0.0)
            + np.where(classes == SIDE_CHAIN_POLAR, side_chain, 0.0)
            + np.where(classes == CHARGED, charged, 0.0)) * sasa * mask
    return gain, cost


def _finish(classes: Any, sasa: Any, mask: Any, slab: tuple, normal: Any,
            origin: Any, lipid_facing_share: float) -> SlabFit:
    import numpy as np

    score, centre, half, start, stop, order = slab
    apolar = (np.where(classes == APOLAR, sasa, 0.0) * mask)[order][start:stop].sum()
    polar = (np.where(classes != APOLAR, sasa, 0.0) * mask)[order][start:stop].sum()
    return SlabFit(normal=np.asarray(normal, dtype=float), centre_nm=centre,
                   half_thickness_nm=half, buried_hydrophobic_nm2=float(apolar),
                   buried_polar_nm2=float(polar), score_nm2=score, origin_nm=origin,
                   details={"lipid_facing_share": round(lipid_facing_share, 3)})


def fit_membrane(topology: Any, positions: Any) -> SlabFit | None:
    """Fit a bilayer to a protein: its normal, centre and thickness.

    None where there is no protein to speak of (fewer than twenty heavy
    atoms). Whether what came back looks like a membrane protein at all is
    :attr:`SlabFit.looks_like_a_membrane_protein`.
    """
    import numpy as np

    _, coordinates, subset = _protein_heavy_atoms(topology, positions)
    if subset is None or len(coordinates) < 20:
        return None
    sasa = _surface(subset, coordinates)
    if sasa.sum() <= 0:
        return None
    classes = surface_classes(subset)
    origin = coordinates.mean(axis=0)
    centred = coordinates - origin
    halves = np.arange(MINIMUM_HALF_THICKNESS_NM, MAXIMUM_HALF_THICKNESS_NM + 1e-9,
                       SLAB_STEP_NM)

    directions = hemisphere_directions(DIRECTION_COUNT)
    candidates: list[Any] = []
    for weights in PROPOSAL_WEIGHTS:
        gain, cost = _weighted(classes, sasa, weights)
        slabs = _best_slabs(centred, gain, cost, directions,
                            step=SLAB_STEP_NM, halves=halves)
        kept: list[Any] = []
        for index in np.argsort([-s[0] for s in slabs]):
            normal = directions[index]
            if all(abs(float(normal @ other)) < np.cos(np.radians(CANDIDATE_SEPARATION_DEG))
                   for other in kept):
                kept.append(normal)
            if len(kept) >= CANDIDATES_PER_PROPOSAL:
                break
        candidates += [n for n in kept
                       if all(abs(float(n @ other)) < np.cos(np.radians(5.0))
                              for other in candidates)]

    final = (APOLAR_WEIGHT, BACKBONE_POLAR_WEIGHT, SIDE_CHAIN_POLAR_WEIGHT, CHARGED_WEIGHT)
    nearby = hemisphere_directions(6000)
    nearby = nearby[nearby[:, 2] >= np.cos(np.radians(REFINEMENT_RADIUS_DEG))]
    best: tuple | None = None
    for candidate in candidates:
        mask = lipid_facing(coordinates, candidate)
        gain, cost = _weighted(classes, sasa, final, mask)
        around = np.vstack([candidate, nearby @ rotation_onto_z(candidate)])
        slabs = _best_slabs(centred, gain, cost, around,
                            step=SLAB_STEP_NM, halves=halves)
        index = int(np.argmax([s[0] for s in slabs]))
        if best is None or slabs[index][0] > best[0][0]:
            best = (slabs[index], around[index], mask)
    slab, normal, mask = best
    share = float((sasa * mask).sum() / sasa.sum())
    return _finish(classes, sasa, mask, slab, normal, origin, share)


def fit_along(topology: Any, positions: Any, normal: Any = (0.0, 0.0, 1.0)) -> SlabFit | None:
    """Fit only the centre and thickness, with the normal given.

    For a structure whose orientation is known -- stated, or taken from OPM
    -- and whose position along the normal is not. Scored as
    :func:`fit_membrane` scores its final choice.
    """
    import numpy as np

    _, coordinates, subset = _protein_heavy_atoms(topology, positions)
    if subset is None or len(coordinates) < 20:
        return None
    sasa = _surface(subset, coordinates)
    if sasa.sum() <= 0:
        return None
    classes = surface_classes(subset)
    normal = np.asarray(normal, dtype=float) / np.linalg.norm(normal)
    origin = coordinates.mean(axis=0)
    halves = np.arange(MINIMUM_HALF_THICKNESS_NM, MAXIMUM_HALF_THICKNESS_NM + 1e-9,
                       SLAB_STEP_NM)
    mask = lipid_facing(coordinates, normal)
    gain, cost = _weighted(classes, sasa,
                           (APOLAR_WEIGHT, BACKBONE_POLAR_WEIGHT,
                            SIDE_CHAIN_POLAR_WEIGHT, CHARGED_WEIGHT), mask)
    [slab] = _best_slabs(coordinates - origin, gain, cost, normal[None, :],
                         step=SLAB_STEP_NM, halves=halves)
    return _finish(classes, sasa, mask, slab, normal, origin,
                   float((sasa * mask).sum() / sasa.sum()))


def tilt_deg(fit: SlabFit, axis: Any = (0.0, 0.0, 1.0)) -> float:
    """The angle between the fitted normal and an axis, either way up."""
    import numpy as np

    a = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    n = np.asarray(fit.normal, dtype=float) / np.linalg.norm(fit.normal)
    return float(np.degrees(np.arccos(min(1.0, abs(float(a @ n))))))
