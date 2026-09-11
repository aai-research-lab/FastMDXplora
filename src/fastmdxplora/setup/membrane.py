"""Putting a protein in a lipid bilayer, and the things that go wrong quietly.

A membrane protein simulated in water is not the protein: the hydrophobic belt
that sits in the bilayer is exposed to solvent, the helices splay, and the
trajectory describes a molecule that does not exist. So a membrane system has
to be built as one.

OpenMM builds the bilayer itself, which removes the usual dependency on an
external packing tool. What it does not do is check the two things that make
the result meaningful, and both fail silently:

**The protein has to be oriented before it is embedded.** ``addMembrane``
places the bilayer in the xy plane with its normal along z, and assumes the
protein is already lying that way. A structure taken straight from the PDB
usually is not: crystallographic frames have no relation to a membrane normal.
Embed an unoriented protein and the lipids pack around it sideways, the run
proceeds, and every number that follows is about a structure nobody would
recognise. The OPM database publishes structures already oriented, and this
module checks the orientation rather than trusting it.

**The pressure coupling has to be anisotropic.** An ordinary barostat scales
x, y and z together, which squeezes a bilayer that should be free to change
thickness independently of its area. The result is a wrong area per lipid,
which is the number membrane simulations are validated against. OpenMM has
``MonteCarloMembraneBarostat`` for this, and using the ordinary one is a
mistake that runs to completion.
"""

from __future__ import annotations

from typing import Any
from fastmdxplora.refusals import StudyError

__all__ = [
    "LIPIDS",
    "membrane_forcefield_files",
    "check_orientation",
    "OrientationWarning",
]


#: Lipids OpenMM can build a bilayer from, with what each is usually for.
#: Not a list this software maintains -- it is what ``Modeller.addMembrane``
#: accepts, and offering one it does not would fail at the point of use.
LIPIDS: dict[str, str] = {
    "POPC": "the default for most membrane work; a fluid phosphatidylcholine",
    "POPE": "phosphatidylethanolamine, common in bacterial inner membranes",
    "DLPC": "a short-tailed phosphatidylcholine, thinner bilayer",
    "DLPE": "the phosphatidylethanolamine equivalent",
    "DMPC": "dimyristoyl; often used where a thinner membrane is wanted",
    "DOPC": "dioleoyl; two unsaturated tails, more fluid",
    "DPPC": "dipalmitoyl; gel phase at room temperature",
}

#: Force field files carrying lipid parameters, by the protein force field
#: they go with. A membrane system built against a force field with no lipid
#: parameters fails when the system is created, with a message about a
#: residue template rather than about the missing file.
_LIPID_PARAMETERS = {
    "amber14": "amber14/lipid17.xml",
    "charmm36": "charmm36/waters.xml",
}


class OrientationWarning(UserWarning):
    """The protein does not look like it is oriented for a membrane."""


def membrane_forcefield_files(existing: list[str], lipid: str = "POPC") -> list[str]:
    """Add lipid parameters to a force field that lacks them.

    The test is whether the force field can already build the lipid, not what
    its files are called: ``amber14-all.xml`` is a bundle that carries lipid17
    inside it, and adding the file again raises about a duplicate template for
    a residue nobody mentioned. Reading the name would have missed that, and
    did.

    Without the parameters the run fails at system creation with a message
    about a residue template for POPC, which names the symptom rather than the
    missing file.
    """
    files = list(existing)

    try:
        from openmm.app import ForceField

        if any(name.startswith(lipid) for name in ForceField(*files)._templates):
            return files
    except Exception:  # noqa: BLE001 - if it will not load, say so below
        pass

    for family, parameters in _LIPID_PARAMETERS.items():
        if any(family in f.lower() for f in files):
            candidate = files + [parameters]
            try:
                from openmm.app import ForceField

                ForceField(*candidate)
            except Exception as exc:  # noqa: BLE001
                raise StudyError(
                    f"Adding {parameters} for the {lipid} bilayer did not "
                    f"work: {exc}"
                , code="setup.membrane.lipid_unparameterized", lipid=lipid) from exc
            return candidate

    raise StudyError(
        f"A {lipid} bilayer needs lipid parameters, and the force field files "
        f"given carry none: {', '.join(files)}. The amber14 and charmm36 "
        "families both have them; if you are using another, add its lipid "
        "parameter file to `force_field` yourself."
    , code="setup.membrane.lipid_unparameterized", lipid=lipid)


def check_orientation(topology: Any, positions: Any) -> str | None:
    """Whether the protein looks oriented with the membrane normal along z.

    Returns ``None`` where it does, and a description of the problem where it
    does not.

    The test is the shape of the protein: a membrane protein is longer along
    the normal than across it, because it spans a bilayer. A structure lying
    on its side has its longest principal axis in the membrane plane instead.
    That is a weak test and it is meant to be -- it catches the common case of
    a PDB entry used as it came, which is the one that produces a silently
    meaningless run. It cannot tell a correctly oriented protein from a
    subtly misoriented one, and it does not claim to.
    """
    import numpy as np

    import mdtraj as md

    mdtop = md.Topology.from_openmm(topology)
    protein = mdtop.select("protein")
    if len(protein) < 20:
        return None  # too small to say anything about

    # Positions may arrive as an OpenMM Quantity of Vec3 or as a plain array,
    # depending on where they came from. Stripped to nanometres either way:
    # the shape of a structure does not depend on how it was handed over.
    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only reached without OpenMM
        pass

    coordinates = np.asarray(
        [[float(positions[int(i)][axis]) for axis in range(3)]
         for i in protein], dtype=float)
    centred = coordinates - coordinates.mean(axis=0)
    # Principal axes: the direction the structure is most extended in.
    _values, vectors = np.linalg.eigh(np.cov(centred.T))
    longest = vectors[:, -1]

    # How much of the longest axis lies along z. A membrane protein spanning
    # a bilayer is extended along the normal.
    along_z = abs(float(longest[2]))
    if along_z >= 0.7:
        return None

    return (
        f"The protein's longest axis lies {along_z:.0%} along z, where a "
        "membrane-spanning protein would be most extended along the membrane "
        "normal. `addMembrane` places the bilayer in the xy plane and assumes "
        "the protein is already oriented for it, so a structure taken "
        "straight from the PDB is usually in the wrong frame — "
        "crystallographic axes have no relation to a membrane normal. "
        "Embedding it anyway produces a run that completes and describes a "
        "structure nobody would recognise.\n\n"
        "Three ways forward. `membrane_orient: true` rotates the structure "
        "so its longest axis lies along the normal, which is right for a "
        "transmembrane helix or a bundle of them. The OPM database "
        "(https://opm.phar.umich.edu) publishes structures already oriented, "
        "which is the answer where the shape is more complicated or where "
        "which way up it sits matters. Or `membrane_orientation_checked: "
        "true` to proceed with the structure exactly as it is."
    )


def orient_for_membrane(topology: Any, positions: Any) -> Any:
    """Rotate a structure so its longest axis lies along the membrane normal.

    The alternative offered to somebody whose structure is in the wrong frame
    is to fetch an oriented one from OPM, which is correct and is a detour.
    For a transmembrane helix or a bundle of them, the orientation is not a
    hard question: the protein is longest along the normal, because that is
    the direction it spans.

    So this rotates the principal axes onto the coordinate axes, longest onto
    z. It is right for the shapes that make up most membrane-protein work and
    it is wrong for a protein whose extent has nothing to do with its normal
    -- a large soluble domain on one side will drag the axis with it. It is
    therefore something to ask for rather than something done quietly, and
    the rotation applied is recorded so it can be checked.

    Nothing here can tell which way up the protein ends: a rotation putting
    the extracellular side down is as valid to this calculation as one putting
    it up. Where that matters, OPM is the answer.
    """
    import numpy as np

    import mdtraj as md

    try:
        from openmm import unit as openmm_unit

        had_units = openmm_unit.is_quantity(positions)
        raw = (positions.value_in_unit(openmm_unit.nanometer) if had_units
               else positions)
    except ImportError:  # pragma: no cover - only without OpenMM
        had_units, raw = False, positions

    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in raw], dtype=float)

    mdtop = md.Topology.from_openmm(topology)
    protein = mdtop.select("protein")
    if len(protein) < 20:
        return positions

    centre = coordinates[protein].mean(axis=0)
    centred = coordinates[protein] - centre
    values, vectors = np.linalg.eigh(np.cov(centred.T))
    # Columns ordered by how extended the structure is along each: the last is
    # the longest, and it becomes z.
    rotation = vectors[:, [0, 1, 2]]
    if np.linalg.det(rotation) < 0:
        # A reflection is not a rotation: it would turn the structure into its
        # mirror image, which is a different molecule.
        rotation[:, 0] *= -1

    rotated = (coordinates - centre) @ rotation

    if had_units:
        # Vec3 is on openmm, not openmm.unit. The tests passed plain arrays,
        # so this branch -- the only one the pipeline takes -- was never run
        # by them, and the mistake reached a real structure.
        from openmm import Vec3
        from openmm import unit as openmm_unit

        return openmm_unit.Quantity(
            [Vec3(*(float(v) for v in row)) for row in rotated],
            openmm_unit.nanometer)
    return rotated


#: Residues whose side chains sit in the lipid. A membrane-spanning protein
#: carries a band of these around its middle -- the hydrophobic belt -- and
#: charged residues at the two interfaces. That contrast is what OPM minimises
#: transfer energy against, and a coarse version of it is checkable here.
_HYDROPHOBIC = frozenset({"ALA", "VAL", "LEU", "ILE", "PHE", "MET", "TRP",
                          "CYS", "PRO"})
_CHARGED = frozenset({"ASP", "GLU", "LYS", "ARG", "HIS"})

#: How much nearer the middle the hydrophobic residues have to be. Measured
#: on constructed cases: a bilayer-spanning arrangement gives about 0.4 and
#: an evenly mixed one gives 1.0. The threshold sits between them with room
#: on both sides, because a real protein is messier than either.
_BELT_RATIO = 0.75


#: What fraction of residues to treat as surface. A relative cut rather than
#: an absolute neighbour count, because the latter depends on how big and how
#: densely packed the structure is; a third to a half is the surface of most
#: folded proteins.
_SURFACE_FRACTION = 0.40

#: How far to look for neighbours when deciding whether a residue is exposed.
_SURFACE_RADIUS_NM = 1.0

#: Fewer residues than this and there is no core to be outside of.
_SURFACE_MINIMUM_RESIDUES = 100

#: How much more crowded the crowded tenth has to be than the sparse tenth
#: before a structure is treated as having an inside. A folded globule gives
#: about 2.3; a single helix gives 1.75, because its least-surrounded
#: residues are its two ends.
_CORE_NEIGHBOUR_CONTRAST = 2.0


def surface_residues(centroids: Any, fraction: float = _SURFACE_FRACTION) -> Any:
    """Which residues sit on the outside, by how few neighbours they have.

    A cheap stand-in for solvent-accessible surface area: a residue in the
    core is surrounded, one on the surface is not. Exact enough for a
    comparison of two populations, and it costs one distance matrix rather
    than a rolling-sphere calculation over every atom.
    """
    import numpy as np

    points = np.asarray(centroids, dtype=float)
    # Below this there is no core to be outside of, and the fewest-neighbours
    # residues are the chain's two ends rather than its surface. A single
    # helix is the case: every residue of it is exposed, and restricting to
    # the least-surrounded ones picks the termini and says nothing about the
    # arrangement in between.
    if len(points) < _SURFACE_MINIMUM_RESIDUES:
        return np.ones(len(points), dtype=bool)

    separations = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    neighbours = (separations < _SURFACE_RADIUS_NM).sum(axis=1)

    # And a structure whose residues are all similarly surrounded has no
    # inside either, however many of them there are -- an extended chain or a
    # thin sheet. Comparing the crowded tenth with the sparse tenth says
    # whether a core exists to speak of.
    sparse = float(np.percentile(neighbours, 10.0))
    crowded = float(np.percentile(neighbours, 90.0))
    if sparse <= 0 or crowded / sparse < _CORE_NEIGHBOUR_CONTRAST:
        return np.ones(len(points), dtype=bool)

    return neighbours <= np.percentile(neighbours, fraction * 100.0)


def surface_belt_ratio(
    centroids: Any, kinds: list[str]
) -> tuple[float, float, float] | None:
    """How tightly the exposed hydrophobic residues gather about the middle.

    Measured on the surface, not on everything. Every folded protein buries
    its hydrophobic residues and exposes its charged ones, so comparing all
    of them measures burial -- which is true of a soluble protein as much as
    a membrane one, and is why this check passed on a structure 51 nm across
    and on one with a receptor embedded upside down.

    What distinguishes a membrane protein is a band of hydrophobic residues
    on its *outside*, where a soluble protein has polar ones. Restricted to
    exposed residues, the two separate by about a factor of two rather than
    the factor of one and a half that burial gives.
    """
    import numpy as np

    points = np.asarray(centroids, dtype=float)
    labels = np.asarray(kinds, dtype=object)
    if len(points) != len(labels) or len(points) < 20:
        return None

    exposed = surface_residues(points)
    z = points[:, 2]
    hydrophobic = z[exposed & (labels == "hydrophobic")]
    charged = z[exposed & (labels == "charged")]
    if len(hydrophobic) < 10 or len(charged) < 10:
        return None

    centre = float(np.median(np.concatenate([hydrophobic, charged])))
    charged_spread = float(np.mean(np.abs(charged - centre)))
    if charged_spread <= 0:
        return None
    hydrophobic_spread = float(np.mean(np.abs(hydrophobic - centre)))
    # The spreads travel with the ratio, because the refusal quotes all three
    # and a bare number gives the reader nothing to check it against.
    return hydrophobic_spread, charged_spread, hydrophobic_spread / charged_spread


def belt_refusal(
    hydrophobic_spread: float, charged_spread: float, ratio: float
) -> str:
    """The refusal, built from the three numbers it quotes.

    Separate from the measurement so it can be exercised without OpenMM.
    Written inline it named two variables that a refactor had moved into
    the measurement, so the check reached the right verdict and then
    raised a NameError explaining it -- on the one path no test in this
    environment could reach.
    """
    return (
        "After orienting, the hydrophobic residues are not gathered near the "
        f"middle the way a bilayer-spanning protein's are: they sit "
        f"{hydrophobic_spread:.2f} nm from the centre along z against "
        f"{charged_spread:.2f} nm for the charged ones, a ratio of "
        f"{ratio:.2f} where a membrane protein gives well under "
        f"{_BELT_RATIO}. A protein that "
        "spans a bilayer has a band of hydrophobic side chains around its "
        "middle and charged ones at the two interfaces, so this structure "
        "either is not a membrane protein or has not been put in the frame a "
        "membrane needs.\n\n"
        "Rotating by principal axes is right for a transmembrane bundle and "
        "wrong where a soluble domain dominates the shape, which is the case "
        "this looks like. The OPM database "
        "(https://opm.phar.umich.edu) publishes structures oriented against "
        "a real transfer energy; use one of those, or "
        "`membrane_orientation_checked: true` to proceed anyway and say so "
        "in the methods."
    )


def check_hydrophobic_belt(topology: Any, positions: Any) -> str | None:
    """Whether an oriented structure looks like it belongs in a bilayer.

    Rotating by principal axes puts the longest axis along z. That is the
    right answer for a transmembrane bundle and the wrong one for a protein
    whose extent has nothing to do with a membrane -- and until now nothing
    told the two apart, so a wrong rotation proceeded silently.

    A membrane-spanning protein has hydrophobic side chains in a band around
    its middle and charged ones at the two interfaces. So after orientation,
    hydrophobic residues should sit nearer the centre in z than charged ones
    do. Where they do not, either the structure is not a membrane protein or
    the rotation put it in the wrong frame, and neither is something to
    continue from.

    This is a coarse test and is meant to be. It is the difference between
    catching the common failure and catching none of them; it is not a
    substitute for OPM, which minimises a real transfer energy.
    """
    import numpy as np

    import mdtraj as md

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass

    mdtop = md.Topology.from_openmm(topology)
    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in positions],
        dtype=float)

    centroids: list[list[float]] = []
    kinds: list[str] = []
    for residue in mdtop.residues:
        heavy = [a.index for a in residue.atoms
                 if a.element is not None and a.element.symbol != "H"]
        if not heavy:
            continue
        name = residue.name.upper()
        if name in _HYDROPHOBIC:
            kind = "hydrophobic"
        elif name in _CHARGED:
            kind = "charged"
        else:
            continue
        centroids.append(list(coordinates[heavy].mean(axis=0)))
        kinds.append(kind)

    measured = surface_belt_ratio(centroids, kinds)
    if measured is None:
        # Too few of one kind to compare. Saying nothing is right: a claim
        # from ten residues would be a claim from nothing.
        return None
    hydrophobic_spread, charged_spread, ratio = measured

    # A margin, not a bare comparison. Measured on constructed cases, a
    # bilayer-spanning arrangement gives about 0.4 and one with charged and
    # hydrophobic residues mixed evenly gives 1.0, so the two are far apart --
    # but `hydrophobic < charged` alone sits on the noise between them and
    # decides a real question by a rounding difference.
    if ratio <= _BELT_RATIO:
        return None

    return belt_refusal(hydrophobic_spread, charged_spread, ratio)


#: How far two chains' residue counts may differ and still be copies of one
#: another. 6B73's two receptors came out 289 and 281 residues -- the same
#: molecule, with a different number of unresolved terminal residues declined
#: at each end. Requiring equal counts read them as different molecules and
#: compared nothing.
_COPY_LENGTH_TOLERANCE = 0.10

#: Below this, a chain's long axis lies too near the membrane plane for its
#: direction along the normal to mean anything. 6B73's two soluble partners
#: sit at -0.06, which is a chain lying in the plane, not a chain pointing
#: down; a sign test on a number that small reports noise as a fault.
_NORMAL_COMPONENT_FLOOR = 0.35


def inverted_chain_pairs(
    chains: dict[int, list[int]],
    residue_counts: dict[int, int],
    coordinates: Any,
) -> list[tuple[int, int]]:
    """Pairs of chains that are copies and span the bilayer opposite ways.

    Separate from the topology reading so the geometry can be tested without
    OpenMM: `md.Topology.from_openmm` needs it, and the arithmetic does not.

    What makes an embedding wrong is the direction each copy spans the
    membrane, so this compares the sign of each chain's long axis along the
    normal rather than the two axes against each other. Comparing the axes
    directly also works where they are near-collinear and says nothing where
    a symmetry leaves two copies perpendicular in the plane; the normal
    component is the quantity the bilayer actually cares about.
    """
    import numpy as np

    coordinates = np.asarray(coordinates, dtype=float)

    def long_axis(atom_indices: list[int]) -> Any:
        block = coordinates[atom_indices]
        centred = block - block.mean(axis=0)
        _, _, right = np.linalg.svd(centred, full_matrices=False)
        return right[0]

    axes = {index: long_axis(atoms) for index, atoms in chains.items()
            if len(atoms) >= 100}

    ordered = sorted(axes)
    inverted: list[tuple[int, int]] = []
    for position, first in enumerate(ordered):
        for other in ordered[position + 1:]:
            a_count = residue_counts.get(first, 0)
            b_count = residue_counts.get(other, 0)
            longer = max(a_count, b_count)
            if not longer:
                continue
            # Copies, allowing for ragged ends.
            if abs(a_count - b_count) / longer > _COPY_LENGTH_TOLERANCE:
                continue
            a_normal = float(axes[first][2])
            b_normal = float(axes[other][2])
            # Both have to be meaningfully out of the plane before their
            # signs are worth comparing.
            if (abs(a_normal) < _NORMAL_COMPONENT_FLOOR
                    or abs(b_normal) < _NORMAL_COMPONENT_FLOOR):
                continue
            if a_normal * b_normal < 0:
                inverted.append((first, other))
    return inverted


def check_chains_point_the_same_way(topology: Any, positions: Any) -> str | None:
    """Whether copies of one chain ended up the same way up in the bilayer.

    Every other check here asks about one chain at a time, and each copy of a
    membrane protein passes them individually whichever way up it is: it spans
    the bilayer, its hydrophobic belt sits in the middle, its axis is well
    defined. What none of them can see is the two copies together.

    6B73 is the case. Two receptors related by a two-fold that is not
    perpendicular to the membrane; principal-axis rotation put one through the
    bilayer inverted relative to the other, and their soluble partners came to
    rest on opposite faces -- one at z +4.0 nm, one at -4.1 nm. Every check
    passed and the run completed, giving a 193,388-atom system in which one
    receptor is upside down. A membrane has two sides and they are not
    equivalent; two copies of one protein in one bilayer have the same
    topology, always.

    Compared by sequence length, because two chains of the same length in one
    deposited structure are the same molecule in practice, and comparing
    sequences costs more than this is worth. The test is the sign of the dot
    product of their principal axes: two copies pointing the same way agree,
    two inverted copies do not.
    """
    import numpy as np

    import mdtraj as md

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover - only without OpenMM
        pass

    mdtop = md.Topology.from_openmm(topology)
    coordinates = np.asarray(
        [[float(p[0]), float(p[1]), float(p[2])] for p in positions],
        dtype=float)

    chains: dict[int, list[int]] = {}
    residue_counts: dict[int, int] = {}
    for residue in mdtop.residues:
        if not residue.is_protein:
            continue
        chains.setdefault(residue.chain.index, []).extend(
            atom.index for atom in residue.atoms)
        residue_counts[residue.chain.index] = (
            residue_counts.get(residue.chain.index, 0) + 1)

    inverted = inverted_chain_pairs(chains, residue_counts, coordinates)
    if not inverted:
        return None

    listed = ", ".join(f"{a} and {b}" for a, b in inverted)
    return (
        f"Chains {listed} are copies of one another and point in opposite "
        "directions along the membrane normal, so one of each pair would be "
        "embedded upside down.\n\n"
        "A membrane has two sides and they are not the same: the two copies "
        "of a protein in one bilayer face the same way. Rotating by principal "
        "axes fixes which axis lies along the normal but not which end of it "
        "points up, and where two copies are related by a symmetry that is "
        "not perpendicular to the membrane it can invert one of them. Each "
        "copy on its own passes every other check here, which is why this "
        "asks about them together.\n\n"
        "An oriented structure from OPM (https://opm.phar.umich.edu) resolves "
        "it, as does building one copy: a single complex has an orientation "
        "that principal axes can find, where two related by such a symmetry "
        "do not."
    )


def check_axis_is_well_defined(topology: Any, positions: Any) -> str | None:
    """Whether the structure has a longest axis worth rotating onto.

    A protein that is roughly as long in two directions has no well-defined
    long axis, and the one the eigenvectors return is chosen by noise. Rotating
    onto it gives a different answer for the same protein from a different
    starting frame, which is the kind of irreproducibility that is very hard
    to notice afterwards.
    """
    import numpy as np

    import mdtraj as md

    try:
        from openmm import unit as openmm_unit

        if openmm_unit.is_quantity(positions):
            positions = positions.value_in_unit(openmm_unit.nanometer)
    except ImportError:  # pragma: no cover
        pass

    mdtop = md.Topology.from_openmm(topology)
    protein = mdtop.select("protein")
    if len(protein) < 20:
        return None

    coordinates = np.asarray(
        [[float(positions[int(i)][axis]) for axis in range(3)]
         for i in protein], dtype=float)
    values = np.linalg.eigvalsh(np.cov((coordinates - coordinates.mean(0)).T))
    longest, second = float(values[-1]), float(values[-2])
    if second <= 0:
        return None

    separation = longest / second
    if separation >= 1.5:
        return None

    return (
        f"The longest axis is only {separation:.2f} times the next one, so "
        "the structure has no clearly longest direction and the one the "
        "calculation returns is chosen by noise: the same protein in a "
        "different starting frame would give a different answer, which is "
        "hard to notice afterwards.\n\n"
        "Use an oriented structure from OPM "
        "(https://opm.phar.umich.edu), or "
        "`membrane_orientation_checked: true` to proceed with the frame as "
        "it is."
    )
