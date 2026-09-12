"""What holds a ligand in place, one interaction type at a time.

Counting contacts says how much of the protein the ligand touches. It does not
say what is holding it: a salt bridge that a charge change would destroy, or
hydrophobic packing that tolerates one. That difference is what a medicinal
chemist is asking, and it is the difference between counting and describing.

Each rule here is implemented against a published criterion, named in its own
docstring, and the thresholds are settings rather than constants -- because the
published values disagree, and a reader should be able to see which was used.
Where a widely-used tool departs from the literature, the departure is recorded
rather than quietly adopted.

Geometry only. Which atoms can take part is chemistry, and that is settled
before this runs: see ``ligand_chemistry``, which also records how confidently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from fastmdxplora.refusals import StudyError

__all__ = [
    "Contact",
    "hydrogen_bonds",
    "hydrophobic_contacts",
    "salt_bridges",
    "donors_and_acceptors",
    "hydrophobic_atoms",
    "protein_charged_groups",
    "ligand_charged_groups",
    "pi_stacking",
    "pi_cation",
    "protein_aromatic_rings",
    "ligand_aromatic_rings",
    "halogen_bonds",
    "metal_coordination",
    "water_bridges",
    "residues_not_covered",
]


#: Halogens with a positive sigma-hole when bound to carbon, which is what
#: makes a halogen bond possible.
#:
#: Fluorine is left out, and this is a place where two widely used tools
#: disagree. PLIP counts "all fluorine, chlorine, bromide or iodine atoms
#: connected to a carbon atom" as donors; ProLIF's pattern admits only Cl, Br,
#: I and At. The literature is with ProLIF for the case that matters here:
#: Politzer and co-workers attribute fluorine's "failure to halogen bond" to
#: its high electronegativity and sp hybridisation, which neutralise the
#: sigma-hole, and organic fluorine bound to carbon "typically has negative
#: sigma-holes and is unlikely to participate".
#:
#: Fluorine can halogen bond when attached to something strongly
#: electron-withdrawing -- F2, ClF, FCN -- but that is not a C-F in a drug
#: molecule. Set ``include_fluorine`` to count it and match PLIP.
_HALOGENS = ("Cl", "Br", "I")

#: Metals PLIP considers, which is the union of what turns up in deposited
#: structures. Kept as a set rather than "anything not organic", because a
#: silicon or a selenium is not a metal centre and would be coordinated by
#: nothing.
_METALS = frozenset({
    "LI", "NA", "K", "RB", "CS", "MG", "CA", "SR", "BA", "SC", "TI", "V",
    "CR", "MN", "FE", "CO", "NI", "CU", "ZN", "Y", "ZR", "NB", "MO", "TC",
    "RU", "RH", "PD", "AG", "CD", "LA", "HF", "TA", "W", "RE", "OS", "IR",
    "PT", "AU", "HG", "AL", "GA", "IN", "SN", "TL", "PB", "BI", "CE", "PR",
    "ND", "SM", "EU", "GD", "TB", "DY", "HO", "ER", "TM", "YB", "LU", "U",
})


#: Aromatic side chains, by the atoms of each ring. Known, like the charges:
#: a phenylalanine's ring is a phenylalanine's ring. Tryptophan has two, fused,
#: and they are kept apart because a stacking partner sits over one or the
#: other and the centre of the pair is over neither.
#: An imidazole is aromatic whatever the force field calls the residue.
#: AMBER writes HIE/HID/HIP and CHARMM writes HSE/HSD/HSP for the same ring,
#: with the same heavy-atom names, and keying on "HIS" alone meant a
#: prepared system lost every histidine ring it had -- silently, because the
#: variants were also on the "nothing to contribute" list below.
_IMIDAZOLE = (("CG", "ND1", "CD2", "CE1", "NE2"),)
_HISTIDINES = ("HIS", "HIE", "HID", "HIP", "HSD", "HSE", "HSP")

_AROMATIC_RINGS = {
    "PHE": (("CG", "CD1", "CD2", "CE1", "CE2", "CZ"),),
    "TYR": (("CG", "CD1", "CD2", "CE1", "CE2", "CZ"),),
    "TRP": (("CG", "CD1", "CD2", "NE1", "CE2"),
            ("CD2", "CE2", "CE3", "CZ2", "CZ3", "CH2")),
    **{name: _IMIDAZOLE for name in _HISTIDINES},
}


#: Charged side chains, by the atoms that carry the charge. Known rather than
#: perceived: a protein is made of these twenty residues, and asking a
#: perception routine which arginine is positive would be inviting it to be
#: wrong about something already settled.
#:
#: Histidine is left out. It titrates near physiological pH, so whether it is
#: charged depends on the pH and its environment -- which the setup phase
#: decides when it protonates, and which is therefore visible in the topology
#: as whether HD1 and HE2 are both present. Guessing here would contradict a
#: decision already made with more information.
_POSITIVE_GROUPS = {
    "ARG": ("NH1", "NH2", "NE"),
    "LYS": ("NZ",),
    # The docstring above defers on HIS because whether it is charged depends
    # on pH and environment, and the setup phase decides that when it
    # protonates. HIP and HSP *are* that decision, written into the residue
    # name: doubly protonated imidazole, +1. Leaving them out did not defer
    # to the setup phase, it discarded what the setup phase concluded.
    "HIP": ("ND1", "NE2"),
    "HSP": ("ND1", "NE2"),
}
_NEGATIVE_GROUPS = {
    "ASP": ("OD1", "OD2"),
    "GLU": ("OE1", "OE2"),
}


#: Elements that donate and accept hydrogen bonds. Carbon is excluded: C-H
#: donors exist and are real, but they are weak enough that including them
#: swamps the count, which is the same reason every tool surveyed leaves them
#: out.
_POLAR = frozenset({"N", "O", "S"})


@dataclass(frozen=True)
class Contact:
    """One interaction, in one frame."""

    kind: str
    frame: int
    #: Atom indices into the trajectory, ligand side first.
    ligand_atom: int
    protein_atom: int
    distance_nm: float
    #: Set where the rule has one; None where it does not.
    angle_deg: float | None = None


def donors_and_acceptors(
    topology: Any, atom_indices: Any
) -> tuple[list[tuple[int, int]], list[int]]:
    """Which atoms can donate a hydrogen bond, and which can accept one.

    A donor is a nitrogen, oxygen or sulphur with a hydrogen bonded to it; an
    acceptor is any of those three. This is the criterion Baker and Hubbard
    used (Prog Biophys Mol Biol 44:97, 1984) and it is what every tool
    surveyed uses, because with explicit hydrogens present it needs no
    perception: the hydrogen is either bonded to the nitrogen or it is not.

    Returns donors as ``(heavy, hydrogen)`` pairs, because the angle is
    measured at the hydrogen and a nitrogen with two hydrogens can donate
    twice.
    """
    wanted = set(int(i) for i in atom_indices)
    atoms = list(topology.atoms)

    attached: dict[int, list[int]] = {}
    for bond in topology.bonds:
        first, second = bond[0], bond[1]
        for heavy, light in ((first, second), (second, first)):
            if light.element is not None and light.element.symbol == "H":
                attached.setdefault(heavy.index, []).append(light.index)

    # A hydrogen bonded to nothing is the signal that connectivity is missing.
    # A hydrogen bonded to a carbon is not: that is an ordinary non-polar
    # hydrogen, and a selection made only of those legitimately cannot donate.
    bonded_atoms = {a.index for bond in topology.bonds for a in (bond[0], bond[1])}

    donors: list[tuple[int, int]] = []
    acceptors: list[int] = []
    orphan_hydrogens = 0
    for atom in atoms:
        if atom.index not in wanted:
            continue
        element = atom.element.symbol if atom.element is not None else ""
        if element == "H":
            if atom.index not in bonded_atoms:
                orphan_hydrogens += 1
            continue
        if element not in _POLAR:
            continue
        acceptors.append(atom.index)
        for hydrogen in attached.get(atom.index, ()):
            donors.append((atom.index, hydrogen))

    # A selection whose hydrogens are bonded to nothing cannot be seen to
    # donate, only to accept, and the count would report one direction as
    # though it were both. This is not hypothetical: renaming a residue in a
    # PDB is enough to lose its standard bonds, and a ligand deposited without
    # CONECT records arrives the same way. Saying so beats returning zero.
    if orphan_hydrogens:
        raise StudyError(
            f"{orphan_hydrogens} hydrogen(s) in this selection are bonded to "
            "nothing, so it cannot be seen to donate a hydrogen bond -- only "
            "to accept one. "
            "Counting under those conditions would report one direction as "
            "though it were both. Give a topology that carries the "
            "connectivity: a PDB with CONECT records, or the topology the "
            "setup phase writes."
        , code="analysis.system.inapplicable")
    return donors, acceptors


def hydrophobic_atoms(topology: Any, atom_indices: Any) -> list[int]:
    """Carbons whose only neighbours are carbon or hydrogen.

    The criterion PLIP states and ProLIF's SMARTS encodes. A carbon bonded to
    nitrogen, oxygen or fluorine is polarised enough that treating it as
    hydrophobic would count a polar contact twice -- once here and once as a
    hydrogen bond.
    """
    wanted = set(int(i) for i in atom_indices)
    neighbours: dict[int, set[str]] = {}
    for bond in topology.bonds:
        first, second = bond[0], bond[1]
        for atom, other in ((first, second), (second, first)):
            symbol = other.element.symbol if other.element is not None else ""
            neighbours.setdefault(atom.index, set()).add(symbol)

    found = []
    for atom in topology.atoms:
        if atom.index not in wanted:
            continue
        if atom.element is None or atom.element.symbol != "C":
            continue
        around = neighbours.get(atom.index, set())
        if around and around <= {"C", "H"}:
            found.append(atom.index)
    return found


def _angles(traj: Any, triples: Any, periodic: bool) -> np.ndarray:
    import mdtraj as md

    if len(triples) == 0:
        return np.zeros((traj.n_frames, 0))
    return np.rad2deg(md.compute_angles(traj, triples, periodic=periodic))


def _distances(traj: Any, pairs: Any, periodic: bool) -> np.ndarray:
    import mdtraj as md

    if len(pairs) == 0:
        return np.zeros((traj.n_frames, 0))
    return md.compute_distances(traj, pairs, periodic=periodic)


def hydrogen_bonds(
    traj: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.35,
    angle_deg: float = 120.0,
    periodic: bool = True,
) -> list[Contact]:
    """Hydrogen bonds between ligand and protein, in every frame.

    The criterion is the literature standard: donor to acceptor within 3.5 A,
    and the donor-hydrogen-acceptor angle above 120 degrees (Baker & Hubbard
    1984; McDonald & Thornton, J Mol Biol 238:777, 1994).

    PLIP uses 4.1 A and 100 degrees, which it says is deliberate -- refined
    against low-resolution crystal structures where hydrogen positions are not
    known. That reasoning does not apply here: these frames have hydrogens
    placed by the force field, so the angle is measured rather than inferred,
    and the stricter criterion is the one the measurement supports. PLIP's
    values remain reachable by setting them.

    Both directions are found: the ligand donating to the protein and the
    protein donating to the ligand are different interactions, and a ligand
    that can only accept is a fact about the ligand worth seeing.
    """
    ligand_donors, ligand_acceptors = donors_and_acceptors(
        traj.topology, ligand_indices)
    protein_donors, protein_acceptors = donors_and_acceptors(
        traj.topology, protein_indices)

    triples: list[tuple[int, int, int]] = []
    pairs: list[tuple[int, int]] = []
    sides: list[tuple[int, int]] = []      # (ligand atom, protein atom)
    for heavy, hydrogen in ligand_donors:
        for acceptor in protein_acceptors:
            triples.append((heavy, hydrogen, acceptor))
            pairs.append((heavy, acceptor))
            sides.append((heavy, acceptor))
    for heavy, hydrogen in protein_donors:
        for acceptor in ligand_acceptors:
            triples.append((heavy, hydrogen, acceptor))
            pairs.append((heavy, acceptor))
            sides.append((acceptor, heavy))

    if not triples:
        return []

    separations = _distances(traj, np.array(pairs), periodic)
    openings = _angles(traj, np.array(triples), periodic)
    close_enough = separations < distance_nm
    straight_enough = openings > angle_deg

    found: list[Contact] = []
    for frame, column in zip(*np.where(close_enough & straight_enough)):
        ligand_atom, protein_atom = sides[column]
        found.append(Contact(
            kind="hydrogen_bond",
            frame=int(frame),
            ligand_atom=int(ligand_atom),
            protein_atom=int(protein_atom),
            distance_nm=float(separations[frame, column]),
            angle_deg=float(openings[frame, column]),
        ))
    return found


def hydrophobic_contacts(
    traj: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.40,
    periodic: bool = True,
) -> list[Contact]:
    """Hydrophobic contacts between ligand and protein, in every frame.

    A pair of hydrophobic carbons within 4.0 A, which is PLIP's threshold;
    ProLIF uses 4.5 A. There is no angle: hydrophobic association is entropic
    rather than directional, so there is no geometry to require, which is why
    every tool uses a distance alone and a generous one.

    The count is large by nature -- PLIP notes it can exceed every other type
    combined -- so it is reported per atom pair and left to the caller to
    reduce. Reducing here would bake in one view of which contact represents a
    residue, and the reduction PLIP applies is one choice among several.
    """
    ligand_carbons = hydrophobic_atoms(traj.topology, ligand_indices)
    protein_carbons = hydrophobic_atoms(traj.topology, protein_indices)
    if not ligand_carbons or not protein_carbons:
        return []

    pairs = [(a, b) for a in ligand_carbons for b in protein_carbons]
    separations = _distances(traj, np.array(pairs), periodic)

    found: list[Contact] = []
    for frame, column in zip(*np.where(separations < distance_nm)):
        ligand_atom, protein_atom = pairs[column]
        found.append(Contact(
            kind="hydrophobic",
            frame=int(frame),
            ligand_atom=int(ligand_atom),
            protein_atom=int(protein_atom),
            distance_nm=float(separations[frame, column]),
        ))
    return found


def protein_charged_groups(
    topology: Any, atom_indices: Any
) -> tuple[list[list[int]], list[list[int]]]:
    """Charged side chains, as groups of atoms rather than single atoms.

    A carboxylate's charge is shared between two oxygens and a guanidinium's
    across three nitrogens, so the distance that matters is to the group's
    centre, not to whichever atom happens to be nearest. Measuring to the
    nearest atom would make the same salt bridge look shorter from one side
    than the other.
    """
    wanted = set(int(i) for i in atom_indices)
    positive: list[list[int]] = []
    negative: list[list[int]] = []

    for residue in topology.residues:
        names = {a.name: a.index for a in residue.atoms if a.index in wanted}
        if not names:
            continue
        for table, out in ((_POSITIVE_GROUPS, positive),
                           (_NEGATIVE_GROUPS, negative)):
            wanted_names = table.get(residue.name)
            if not wanted_names:
                continue
            group = [names[n] for n in wanted_names if n in names]
            # A side chain missing half its charged atoms is a truncated
            # residue, and its centre would be somewhere the charge is not.
            if len(group) == len(wanted_names):
                out.append(group)
    return positive, negative


def ligand_charged_groups(
    chemistry: Any, atom_indices: Any
) -> tuple[list[list[int]], list[list[int]]]:
    """Charged groups on the ligand, from its resolved chemistry.

    Formal charges come from the chemistry, not from the coordinates, which is
    why it has to be resolved first. A carboxylate carries -1 across two
    oxygens whichever one the file happens to mark, so the charge is spread
    over the group it is delocalised across.
    """
    from rdkit import Chem

    mol = chemistry.mol
    order = list(int(i) for i in atom_indices)

    positive: list[list[int]] = []
    negative: list[list[int]] = []

    #: Delocalised groups, so the centre is the group's rather than one atom's.
    #: The atoms kept are the ones the charge is on, not every atom the
    #: pattern matched: a carboxylate's charge sits across its two oxygens and
    #: not on the carbon between them, and including the carbon would put the
    #: centre a third of a bond length off in the direction of the molecule.
    patterns = (
        ("-", Chem.MolFromSmarts("C(=[OX1])[OX1H0-]"), ("O",)),
        ("-", Chem.MolFromSmarts("[PX4,SX4](=[OX1])[OX1H0-]"), ("O",)),
        ("+", Chem.MolFromSmarts("[NX3][CX3](=[NX2,NX3+])[NX3]"), ("N",)),
        ("+", Chem.MolFromSmarts("[NX3][CX3]=[NX3+]"), ("N",)),
    )
    claimed: set[int] = set()
    for sign, pattern, elements in patterns:
        if pattern is None:
            continue
        for match in mol.GetSubstructMatches(pattern):
            group = [
                order[i] for i in match
                if i < len(order) and mol.GetAtomWithIdx(i).GetSymbol() in elements
            ]
            if not group or claimed.intersection(group):
                continue
            claimed.update(group)
            (positive if sign == "+" else negative).append(group)

    # Anything left carrying a formal charge stands on its own.
    for atom in mol.GetAtoms():
        index = atom.GetIdx()
        if index >= len(order) or index in claimed:
            continue
        charge = atom.GetFormalCharge()
        if charge > 0:
            positive.append([order[index]])
        elif charge < 0:
            negative.append([order[index]])
    return positive, negative


def _has_box(traj: Any) -> bool:
    return getattr(traj, "unitcell_vectors", None) is not None


def _group_centres(traj: Any, groups: Any, periodic: bool = True) -> np.ndarray:
    """One position per charged group, per frame, with the group made whole.

    A group straddling a periodic face has atoms a box length apart in the
    stored coordinates, so their mean lands in the middle of the box where no
    atom is. Rebuilt around the group's first atom using minimum-image
    displacements, which mdtraj computes correctly for triclinic cells --
    hand-rolled fractional rounding does not, as the ligand_rmsd work found
    the expensive way.
    """
    if not groups:
        return np.zeros((traj.n_frames, 0, 3))
    if not periodic or not _has_box(traj):
        return np.stack(
            [traj.xyz[:, np.array(group), :].mean(axis=1) for group in groups],
            axis=1,
        )
    import mdtraj as md

    centres = []
    for group in groups:
        members = np.asarray(group, dtype=int)
        anchor = int(members[0])
        pairs = np.column_stack(
            [np.full(len(members), anchor, dtype=int), members])
        offsets = md.compute_displacements(traj, pairs, periodic=True)
        centres.append(traj.xyz[:, anchor, :] + offsets.mean(axis=1))
    return np.stack(centres, axis=1)


def _between(
    traj: Any,
    from_groups: Any,
    from_centres: np.ndarray,
    to_groups: Any,
    to_centres: np.ndarray,
    periodic: bool = True,
) -> np.ndarray:
    """Minimum-image vector from each `from` centre to each `to` centre.

    Shape ``(n_frames, n_from, n_to, 3)``.

    A centre is not an atom, so mdtraj cannot image it directly. Each centre
    does sit a short way from its own group's anchor atom, and mdtraj images
    anchor to anchor exactly, triclinic cells included; adding the two small
    within-group offsets to that exact vector gives the separation under the
    minimum image. Valid whenever the true separation is well inside half the
    box, which every criterion in this module is at 3 to 6 A.

    Without this, six of the eight rules measured raw Cartesian separation:
    a ligand and a residue 1.5 A apart through a box face read as the width
    of the box, and the contact was silently absent from the table.
    """
    if (not periodic or not _has_box(traj)
            or not len(from_groups) or not len(to_groups)):
        return to_centres[:, None, :, :] - from_centres[:, :, None, :]

    import mdtraj as md

    from_anchor = np.array([int(g[0]) for g in from_groups], dtype=int)
    to_anchor = np.array([int(g[0]) for g in to_groups], dtype=int)
    pairs = np.array(
        [[i, j] for i in from_anchor for j in to_anchor], dtype=int)
    imaged = md.compute_displacements(traj, pairs, periodic=True).reshape(
        traj.n_frames, len(from_anchor), len(to_anchor), 3)

    from_offset = from_centres - traj.xyz[:, from_anchor, :]
    to_offset = to_centres - traj.xyz[:, to_anchor, :]
    return imaged + to_offset[:, None, :, :] - from_offset[:, :, None, :]


def salt_bridges(
    traj: Any,
    chemistry: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.45,
    allow_ambiguous_charge: bool = False,
    periodic: bool = True,
) -> list[Contact]:
    """Salt bridges between ligand and protein, in every frame.

    Opposite charges within 4.5 A, which is ProLIF's threshold; PLIP uses
    5.5 A. There is no angle: the interaction is electrostatic and has no
    preferred direction, which is why every tool surveyed uses a distance
    alone.

    Refuses where the ligand's charge was not determined. A salt bridge is a
    claim about charge, and the charge is exactly what perception from
    coordinates is worst at: guanidinium is +1, and -1 also balances, so a
    guessed charge can make a cation look like an anion and invent the bridge
    it was supposed to detect. Where the chemistry was resolved rather than
    perceived this does not arise; where it was not, state the charge or pass
    ``allow_ambiguous_charge`` knowing what it means.
    """
    if getattr(chemistry, "charge_was_ambiguous", False) and not allow_ambiguous_charge:
        raise StudyError(
            f"The net charge of {chemistry.resname!r} was not determined -- "
            f"{chemistry.detail}. A salt bridge is a claim about charge, and "
            "reporting one computed from a charge that was guessed would be "
            "asserting more than is known. State the ligand's net charge, or "
            "supply its chemistry as an SDF."
        , code="setup.chemistry.charge_undetermined")

    ligand_positive, ligand_negative = ligand_charged_groups(
        chemistry, ligand_indices)
    protein_positive, protein_negative = protein_charged_groups(
        traj.topology, protein_indices)

    found: list[Contact] = []
    for ligand_groups, protein_groups in ((ligand_positive, protein_negative),
                                          (ligand_negative, protein_positive)):
        if not ligand_groups or not protein_groups:
            continue
        ligand_centres = _group_centres(traj, ligand_groups, periodic)
        protein_centres = _group_centres(traj, protein_groups, periodic)
        separations = np.linalg.norm(
            _between(traj, ligand_groups, ligand_centres,
                     protein_groups, protein_centres, periodic),
            axis=-1,
        )
        for frame, first, second in zip(*np.where(separations < distance_nm)):
            found.append(Contact(
                kind="salt_bridge",
                frame=int(frame),
                ligand_atom=int(ligand_groups[first][0]),
                protein_atom=int(protein_groups[second][0]),
                distance_nm=float(separations[frame, first, second]),
            ))
    return found


def protein_aromatic_rings(
    topology: Any, atom_indices: Any
) -> list[list[int]]:
    """Aromatic rings in the protein, from the residues that have them."""
    wanted = set(int(i) for i in atom_indices)
    rings: list[list[int]] = []
    for residue in topology.residues:
        for names in _AROMATIC_RINGS.get(residue.name, ()):
            found = {a.name: a.index for a in residue.atoms if a.index in wanted}
            ring = [found[n] for n in names if n in found]
            # A ring missing an atom has no well-defined plane, and fitting one
            # to what is left would put the normal somewhere the ring is not.
            if len(ring) == len(names):
                rings.append(ring)
    return rings


def ligand_aromatic_rings(chemistry: Any, atom_indices: Any) -> list[list[int]]:
    """Aromatic rings in the ligand, from its resolved chemistry.

    Aromaticity is a chemical fact rather than a geometric one -- a flat ring
    of carbons is not necessarily aromatic -- so it comes from the chemistry,
    which is why that has to be resolved first.
    """
    order = list(int(i) for i in atom_indices)
    mol = chemistry.mol
    rings: list[list[int]] = []
    for ring in mol.GetRingInfo().AtomRings():
        if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring):
            mapped = [order[i] for i in ring if i < len(order)]
            if len(mapped) == len(ring):
                rings.append(mapped)
    return rings


def _ring_geometry(
    traj: Any, rings: Any, periodic: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """Each ring's centre and the unit normal to its plane, per frame.

    Made whole across the box first, for the reason `_group_centres` gives:
    a ring split across a face has no meaningful centroid, and its fitted
    plane is a plane through two clusters of atoms a box length apart.
    """
    if not rings:
        empty = np.zeros((traj.n_frames, 0, 3))
        return empty, empty
    whole = periodic and _has_box(traj)
    if whole:
        import mdtraj as md
    centres, normals = [], []
    for ring in rings:
        members = np.asarray(ring, dtype=int)
        if whole:
            anchor = int(members[0])
            pairs = np.column_stack(
                [np.full(len(members), anchor, dtype=int), members])
            offsets = md.compute_displacements(traj, pairs, periodic=True)
            points = traj.xyz[:, anchor, None, :] + offsets
        else:
            points = traj.xyz[:, members, :]
        centre = points.mean(axis=1)
        # The plane is fitted rather than taken from three atoms: a ring puckers,
        # and three atoms of a puckered ring give a normal that swings with
        # whichever three were chosen.
        centred = points - centre[:, None, :]
        _u, _s, vh = np.linalg.svd(centred, full_matrices=False)
        normal = vh[:, 2, :]
        normals.append(normal / np.linalg.norm(normal, axis=-1, keepdims=True))
        centres.append(centre)
    return np.stack(centres, axis=1), np.stack(normals, axis=1)


def _plane_angle(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Angle between two planes, in degrees, folded into 0-90.

    A normal has no preferred direction, so 170 degrees between normals is the
    same arrangement as 10. Folding keeps parallel at 0 and perpendicular at 90
    whichever way the normals happen to point.
    """
    cosine = np.abs(np.einsum("...i,...i->...", first, second)).clip(0.0, 1.0)
    return np.rad2deg(np.arccos(cosine))


def _offsets(between: np.ndarray, normals_a: np.ndarray) -> np.ndarray:
    """How far one ring centre sits from the other's axis.

    Two rings can be the right distance apart and side by side rather than
    stacked. The offset is what tells those apart: it is the distance from one
    centre to the line through the other along its normal.

    Takes the separation vector rather than the two centres, so that the
    vector `_between` imaged is the one projected here. Building it again
    from raw centres would put the minimum image back only in the distance
    and leave the offset measured across the box.
    """
    along = np.einsum("...i,...i->...", between, normals_a[:, :, None, :])
    perpendicular = between - along[..., None] * normals_a[:, :, None, :]
    return np.linalg.norm(perpendicular, axis=-1)


def pi_stacking(
    traj: Any,
    chemistry: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.55,
    angle_tolerance_deg: float = 30.0,
    offset_nm: float = 0.20,
    periodic: bool = True,
) -> list[Contact]:
    """Aromatic rings stacked on each other, in every frame.

    PLIP's criterion: ring centres within 5.5 A, the angle between the planes
    within 30 degrees of parallel or of perpendicular, and the offset between
    the centres below 2.0 A -- about the radius of benzene plus a little.

    Both arrangements are reported and named. Parallel stacking and the
    T-shaped, edge-to-face arrangement are different interactions with
    different geometries, and a ligand that stacks one way and not the other is
    telling you something about the pocket.
    """
    ligand_rings = ligand_aromatic_rings(chemistry, ligand_indices)
    protein_rings = protein_aromatic_rings(traj.topology, protein_indices)
    if not ligand_rings or not protein_rings:
        return []

    lig_centres, lig_normals = _ring_geometry(traj, ligand_rings, periodic)
    pro_centres, pro_normals = _ring_geometry(traj, protein_rings, periodic)

    between = _between(traj, ligand_rings, lig_centres,
                       protein_rings, pro_centres, periodic)
    separations = np.linalg.norm(between, axis=-1)
    angles = _plane_angle(lig_normals[:, :, None, :], pro_normals[:, None, :, :])
    # Measured from both rings: side by side looks stacked from one of them
    # only when the other is ignored. The reverse direction is the same
    # imaged vector negated and transposed -- recomputing it from the raw
    # centres would leave this half measured across the box.
    reverse = -between.transpose(0, 2, 1, 3)
    offset = np.minimum(_offsets(between, lig_normals),
                        _offsets(reverse, pro_normals).transpose(0, 2, 1))

    parallel = angles < angle_tolerance_deg
    perpendicular = angles > (90.0 - angle_tolerance_deg)
    close = (separations < distance_nm) & (offset < offset_nm)

    found: list[Contact] = []
    for arrangement, mask in (("face_to_face", parallel), ("edge_to_face", perpendicular)):
        for frame, first, second in zip(*np.where(close & mask)):
            found.append(Contact(
                kind=f"pi_stacking_{arrangement}",
                frame=int(frame),
                ligand_atom=int(ligand_rings[first][0]),
                protein_atom=int(protein_rings[second][0]),
                distance_nm=float(separations[frame, first, second]),
                angle_deg=float(angles[frame, first, second]),
            ))
    return found


def pi_cation(
    traj: Any,
    chemistry: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.60,
    offset_nm: float = 0.20,
    allow_ambiguous_charge: bool = False,
    periodic: bool = True,
) -> list[Contact]:
    """A positive charge sitting over an aromatic ring, in every frame.

    PLIP's criterion: the charge within 6.0 A of the ring centre, and the
    offset from the ring's axis below 2.0 A. The offset matters more here than
    the distance: a cation beside a ring at 5 A is not interacting with its
    face, and distance alone cannot tell the two apart.

    Refuses on an undetermined ligand charge for the same reason a salt bridge
    does -- it is a claim about charge.
    """
    if getattr(chemistry, "charge_was_ambiguous", False) and not allow_ambiguous_charge:
        raise StudyError(
            f"The net charge of {chemistry.resname!r} was not determined -- "
            f"{chemistry.detail}. A pi-cation interaction is a claim about "
            "charge. State the ligand's net charge, or supply its chemistry "
            "as an SDF."
        , code="setup.chemistry.charge_undetermined")

    ligand_positive, _ligand_negative = ligand_charged_groups(
        chemistry, ligand_indices)
    protein_positive, _protein_negative = protein_charged_groups(
        traj.topology, protein_indices)
    ligand_rings = ligand_aromatic_rings(chemistry, ligand_indices)
    protein_rings = protein_aromatic_rings(traj.topology, protein_indices)

    found: list[Contact] = []
    for cations, rings, cation_is_ligand in (
        (ligand_positive, protein_rings, True),
        (protein_positive, ligand_rings, False),
    ):
        if not cations or not rings:
            continue
        charge_centres = _group_centres(traj, cations, periodic)
        ring_centres, ring_normals = _ring_geometry(traj, rings, periodic)
        between = _between(traj, cations, charge_centres,
                           rings, ring_centres, periodic)
        separations = np.linalg.norm(between, axis=-1)
        # Projected on the ring's normal, so the vector runs ring -> cation.
        offset = _offsets(-between.transpose(0, 2, 1, 3),
                          ring_normals).transpose(0, 2, 1)

        for frame, cation, ring in zip(
            *np.where((separations < distance_nm) & (offset < offset_nm))
        ):
            ligand_atom = (cations[cation][0] if cation_is_ligand
                           else rings[ring][0])
            protein_atom = (rings[ring][0] if cation_is_ligand
                            else cations[cation][0])
            found.append(Contact(
                kind="pi_cation",
                frame=int(frame),
                ligand_atom=int(ligand_atom),
                protein_atom=int(protein_atom),
                distance_nm=float(separations[frame, cation, ring]),
            ))
    return found


def halogen_bonds(
    traj: Any,
    chemistry: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.35,
    donor_angle_deg: tuple[float, float] = (130.0, 180.0),
    include_fluorine: bool = False,
    periodic: bool = True,
) -> list[Contact]:
    """A halogen on the ligand donating to an acceptor on the protein.

    The halogen's sigma-hole points along the C-X axis, so the interaction is
    directional: the carbon-halogen-acceptor angle has to be near straight.
    ProLIF requires 130 to 180 degrees within 3.5 A; PLIP uses 165 plus or
    minus 30 within 4.0 A. The narrower distance is used because it is the one
    the sigma-hole picture supports, and both are settings.

    Fluorine is not counted unless asked for. See ``_HALOGENS`` for why, and
    for which tool disagrees.

    Only the ligand donates. Proteins carry no halogens unless somebody has
    modified them, and a modified residue is something to say so about rather
    than to guess at.
    """
    order = list(int(i) for i in ligand_indices)
    mol = chemistry.mol
    wanted = set(_HALOGENS) | ({"F"} if include_fluorine else set())

    donors: list[tuple[int, int]] = []      # (carbon, halogen)
    for atom in mol.GetAtoms():
        if atom.GetSymbol() not in wanted or atom.GetIdx() >= len(order):
            continue
        for neighbour in atom.GetNeighbors():
            if neighbour.GetSymbol() == "C" and neighbour.GetIdx() < len(order):
                donors.append((order[neighbour.GetIdx()], order[atom.GetIdx()]))

    _protein_donors, acceptors = donors_and_acceptors(
        traj.topology, protein_indices)
    if not donors or not acceptors:
        return []

    triples = [(carbon, halogen, acceptor)
               for carbon, halogen in donors for acceptor in acceptors]
    pairs = [(halogen, acceptor) for _c, halogen in donors for acceptor in acceptors]

    separations = _distances(traj, np.array(pairs), periodic)
    angles = _angles(traj, np.array(triples), periodic)
    low, high = donor_angle_deg

    found: list[Contact] = []
    for frame, column in zip(*np.where(
        (separations < distance_nm) & (angles > low) & (angles <= high)
    )):
        halogen, acceptor = pairs[column]
        found.append(Contact(
            kind="halogen_bond",
            frame=int(frame),
            ligand_atom=int(halogen),
            protein_atom=int(acceptor),
            distance_nm=float(separations[frame, column]),
            angle_deg=float(angles[frame, column]),
        ))
    return found


def metal_coordination(
    traj: Any,
    ligand_indices: Any,
    protein_indices: Any,
    *,
    distance_nm: float = 0.30,
    periodic: bool = True,
) -> list[Contact]:
    """A metal ion coordinated by the ligand, or coordinating it.

    A donor atom within 3.0 A of the metal, which is PLIP's threshold. No
    angle: the geometry of a metal centre is a property of the whole
    coordination shell rather than of any one contact, and PLIP fits the shell
    to known geometries afterwards. That fitting is not done here, because
    which targets are superfluous to a coordination number is a judgement, and
    reporting each contact leaves it visible.

    The metal may be on either side. An ion in the structure is often neither
    protein nor ligand in the way a selection divides them, so both directions
    are searched.
    """
    topology = traj.topology

    def split(indices):
        wanted = set(int(i) for i in indices)
        metals, donors = [], []
        for atom in topology.atoms:
            if atom.index not in wanted:
                continue
            symbol = (atom.element.symbol if atom.element is not None else "").upper()
            # The element, never the atom name. Every amino acid has an atom
            # called CA -- its alpha carbon -- and CA is also calcium, so
            # matching names turns every residue in the protein into a metal
            # centre. The same trap catches CD, HG, NA and IN.
            if symbol in _METALS:
                # And a metal ion sits alone: an ion is its own residue, so a
                # symbol that matches inside a larger residue is a
                # misassignment rather than an ion.
                if atom.residue.n_atoms == 1:
                    metals.append(atom.index)
            elif symbol in {"N", "O", "S"}:
                donors.append(atom.index)
        return metals, donors

    ligand_metals, ligand_donors = split(ligand_indices)
    protein_metals, protein_donors = split(protein_indices)

    found: list[Contact] = []
    for metals, partners, metal_is_ligand in (
        (ligand_metals, protein_donors, True),
        (protein_metals, ligand_donors, False),
    ):
        if not metals or not partners:
            continue
        pairs = [(m, p) for m in metals for p in partners]
        separations = _distances(traj, np.array(pairs), periodic)
        for frame, column in zip(*np.where(separations < distance_nm)):
            metal, partner = pairs[column]
            found.append(Contact(
                kind="metal_coordination",
                frame=int(frame),
                ligand_atom=int(metal if metal_is_ligand else partner),
                protein_atom=int(partner if metal_is_ligand else metal),
                distance_nm=float(separations[frame, column]),
            ))
    return found


def water_bridges(
    traj: Any,
    ligand_indices: Any,
    protein_indices: Any,
    water_indices: Any,
    *,
    min_distance_nm: float = 0.25,
    max_distance_nm: float = 0.41,
    omega_deg: tuple[float, float] = (71.0, 140.0),
    periodic: bool = True,
) -> list[Contact]:
    """A water molecule hydrogen-bonded to both the ligand and the protein.

    PLIP's criterion: the water oxygen between 2.5 and 4.1 A of a polar atom on
    each side, and the angle at the water -- between the two partners, measured
    at its oxygen -- between 71 and 140 degrees. The lower bound matters as much
    as the upper: a water in line with both is not bridging them, it is simply
    between them.

    Only single-water bridges are found. Two waters can bridge a gap, and
    three, and at some chain length the claim stops meaning anything about
    binding; PLIP draws the line at one and the same line is drawn here. Where
    a longer chain matters, it is a different question that deserves asking
    directly rather than falling out of this.

    Waters have to be given. Which oxygens count as solvent is a selection, and
    a run that stripped its waters has none to offer -- an empty result there
    means the trajectory holds no water, not that no bridges formed.
    """
    waters = [int(i) for i in water_indices]
    if not waters:
        return []

    topology = traj.topology
    oxygens = [
        index for index in waters
        if (topology.atom(index).element is not None
            and topology.atom(index).element.symbol == "O")
    ]
    if not oxygens:
        return []

    _ligand_donors, ligand_polar = donors_and_acceptors(topology, ligand_indices)
    _protein_donors, protein_polar = donors_and_acceptors(topology, protein_indices)
    if not ligand_polar or not protein_polar:
        return []

    # Distances first, because most waters are near neither side and working
    # out an angle for every triple would be the expensive way to discover it.
    ligand_pairs = [(o, p) for o in oxygens for p in ligand_polar]
    protein_pairs = [(o, p) for o in oxygens for p in protein_polar]
    to_ligand = _distances(traj, np.array(ligand_pairs), periodic)
    to_protein = _distances(traj, np.array(protein_pairs), periodic)

    def in_range(separations):
        return (separations > min_distance_nm) & (separations < max_distance_nm)

    ligand_ok = in_range(to_ligand)
    protein_ok = in_range(to_protein)

    low, high = omega_deg
    n_ligand, n_protein = len(ligand_polar), len(protein_polar)

    triples: list[tuple[int, int, int]] = []
    described: list[tuple[int, int, int, int, int]] = []
    for frame in range(traj.n_frames):
        for oxygen_at, oxygen in enumerate(oxygens):
            near_ligand = [
                i for i in range(n_ligand)
                if ligand_ok[frame, oxygen_at * n_ligand + i]
            ]
            if not near_ligand:
                continue
            near_protein = [
                i for i in range(n_protein)
                if protein_ok[frame, oxygen_at * n_protein + i]
            ]
            for first in near_ligand:
                for second in near_protein:
                    triples.append((ligand_polar[first], oxygen,
                                    protein_polar[second]))
                    described.append((frame, oxygen, ligand_polar[first],
                                      protein_polar[second], len(triples) - 1))
    if not triples:
        return []

    # One frame's worth of geometry at a time: the triples were gathered per
    # frame, so the angle wanted is the one in that frame.
    angles = _angles(traj, np.array(triples), periodic)

    found: list[Contact] = []
    for frame, _oxygen, ligand_atom, protein_atom, column in described:
        opening = float(angles[frame, column])
        if not (low < opening < high):
            continue
        # The reported ligand-to-protein span, under the same convention the
        # two legs of the bridge were measured with. Taken raw it could
        # exceed the box on a bridge whose ends sit either side of a face --
        # a number in the table larger than the system it came from.
        separation = float(_distances(
            traj, np.array([[ligand_atom, protein_atom]]), periodic
        )[frame, 0])
        found.append(Contact(
            kind="water_bridge",
            frame=frame,
            ligand_atom=int(ligand_atom),
            protein_atom=int(protein_atom),
            distance_nm=separation,
            angle_deg=opening,
        ))
    return found


def residues_not_covered(topology: Any, atom_indices: Any) -> dict[str, int]:
    """Residues in a selection that the charge and ring tables do not know.

    Those tables are the twenty standard amino acids, which is why they need
    no perception. The cost is that anything else falls through them silently:
    point this at DNA and the hydrogen bonds come out right, because they are
    found from elements and bonds, while the salt bridges and the stacking
    come out as zero -- though a phosphate is charged and a nucleobase is
    aromatic.

    Zero is an answer. "These residues were not examined for charge or
    aromaticity" is a different one, and the true one.

    Returned as a count per residue name so the caller can report it rather
    than deciding on its own whether it matters: a single modified residue in
    a large protein is a footnote, and a selection made entirely of nucleotides
    is not.
    """
    known = set(_POSITIVE_GROUPS) | set(_NEGATIVE_GROUPS) | set(_AROMATIC_RINGS)
    #: Residues with neither a charge nor a ring, which the tables leave out
    #: because they have nothing to contribute rather than because they are
    #: unknown.
    known |= {
        "ALA", "GLY", "VAL", "LEU", "ILE", "PRO", "MET", "SER", "THR", "CYS",
        "ASN", "GLN", "CYX", "ASH", "GLH", "LYN",
    }
    # HIE, HID, HIP, HSD, HSE and HSP used to be on that list, which said they
    # had nothing to contribute. An imidazole is aromatic and HIP is +1, so it
    # was wrong twice over -- and because it was a claim of coverage rather
    # than an omission, the residues did not appear here either. They are in
    # the ring table now, so `known` picks them up above and nothing needs to
    # be said twice.
    wanted = set(int(i) for i in atom_indices)
    unknown: dict[str, int] = {}
    for residue in topology.residues:
        if not any(atom.index in wanted for atom in residue.atoms):
            continue
        if residue.name.upper() in known:
            continue
        unknown[residue.name] = unknown.get(residue.name, 0) + 1
    return unknown
