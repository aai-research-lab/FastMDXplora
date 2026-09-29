"""What setup will build from a structure, worked out before it runs.

Setup settles the box and the particle count, and it settles them after
minutes of fixing, protonating and solvating: only then does anyone learn
that 1.0 nm of padding around this protein is 94,000 particles, or that the
file holds two copies where one was meant. Most of it follows from the
structure and the settings by arithmetic, and said while the settings are
still being chosen it changes what is chosen.

This is that arithmetic, and it is an estimate. The box is OpenMM's own rule
(the solute's bounding sphere plus the padding, grown for the cutoff as setup
grows it). The solute is counted from its residues' templates, hydrogens and
missing atoms included, with the gaps setup builds. The water is OpenMM's
pre-equilibrated box at its own density, less what the solute displaces, and
the ions are OpenMM's count for that water. The displacement per solute atom
is the volume within a fitted distance of its atoms
(``EXCLUSION_RADIUS_NM``), checked against setup and OpenMM in the tests.

What it does not know it says: the assembly setup will choose where the file
declares several, a structure setup will stop on, a membrane, whose box is
the bilayer's. Setup's own numbers replace these once it has run.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.ligand_detection import ION_RESNAMES, WATER_RESNAMES

#: Atoms (heavy and hydrogen) of each standard residue inside a chain, as the
#: AMBER templates build them at neutral pH: histidine as HIE, aspartate and
#: glutamate charged, lysine and arginine protonated.
RESIDUE_ATOMS = {
    "ALA": 10, "ARG": 24, "ASN": 14, "ASP": 12, "CYS": 11, "GLN": 17, "GLU": 15,
    "GLY": 7, "HIS": 17, "ILE": 19, "LEU": 19, "LYS": 22, "MET": 17, "PHE": 20,
    "PRO": 14, "SER": 11, "THR": 14, "TRP": 24, "TYR": 21, "VAL": 16,
    # Named protonation and bonding states.
    "HIE": 17, "HID": 17, "HIP": 18, "CYX": 10, "ASH": 13, "GLH": 16, "LYN": 21, "CYM": 10,
    # Caps.
    "ACE": 6, "NME": 6, "NMA": 6,
    # Nucleotides inside a strand (AMBER OL15 and OL3).
    "DA": 32, "DC": 30, "DG": 33, "DT": 32, "A": 33, "C": 31, "G": 34, "U": 30,
}

#: The formal charge each carries at neutral pH.
RESIDUE_CHARGE = {
    "ARG": 1, "LYS": 1, "HIP": 1, "ASP": -1, "GLU": -1, "CYM": -1,
    "DA": -1, "DC": -1, "DG": -1, "DT": -1, "A": -1, "C": -1, "G": -1, "U": -1,
}

#: Modified residues setup replaces with their standard equivalents
#: (`replace_nonstandard_residues`), the common ones.
REPLACED = {"MSE": "MET", "HYP": "PRO", "SEP": "SER", "TPO": "THR", "PTR": "TYR",
            "CSO": "CYS", "CME": "CYS", "MLY": "LYS", "KCX": "LYS", "CSD": "CYS"}

_NUCLEOTIDES = {"DA", "DC", "DG", "DT", "A", "C", "G", "U"}
_CAPS = {"ACE", "NME", "NMA"}

#: Charges of the metal ions a structure may carry into the box.
ION_CHARGE = {"NA": 1, "K": 1, "LI": 1, "CS": 1, "RB": 1, "CL": -1, "BR": -1, "I": -1,
              "F": -1, "MG": 2, "CA": 2, "ZN": 2, "MN": 2, "FE": 2, "FE2": 2, "CU": 2,
              "CU1": 1, "NI": 2, "CO": 2, "CD": 2, "HG": 2, "BA": 2, "SR": 2}

#: Water molecules per cubic nanometre in OpenMM's pre-equilibrated box, which
#: every water model's `addSolvent` tiles: 895 waters in 27 nm^3.
WATER_PER_NM3 = 895 / 27.0

#: How close to a deposited heavy atom `addSolvent` leaves no water, in nm:
#: the atom's own radius, a water's, and the hydrogens between. The water a
#: solute displaces is OpenMM's density over the volume within this of any
#: of its heavy atoms, measured on a 0.1 nm grid; so an extended peptide
#: displaces more per atom than a folded protein, as it does in OpenMM.
#: Fitted to OpenMM builds of nine solutes from a tripeptide to haemoglobin
#: (54 to 8,766 atoms): their water comes out within 1.5%, and a
#: tripeptide's within 7%.
EXCLUSION_RADIUS_NM = 0.40

#: Waters lost where OpenMM's tiled box meets the faces of the periodic cell,
#: per nm^2 of the cell's volume to the two-thirds: measured on dodecahedra
#: of water with nothing in them, 2 to 8 nm across.
WATERS_LOST_AT_THE_FACES = 2.6

#: How far a solute's hydrogens, and the atoms setup builds, reach past the
#: deposited heavy atoms, in nm, for the bounding sphere of a structure
#: deposited without them: 0.045 to 0.11 on the same nine builds.
HYDROGEN_REACH_NM = 0.07

#: Molar concentration of pure water, as OpenMM counts ion pairs from it.
WATER_MOLARITY = 55.4

#: Particles per water molecule, by model.
WATER_SITES = {"tip3p": 3, "spce": 3, "tip3pfb": 3, "opc3": 3, "tip4pew": 4,
               "tip4pfb": 4, "opc": 4, "tip5p": 5}

#: Volume of a box per cube of its width.
VOLUME_PER_WIDTH_CUBED = {"cube": 1.0, "dodecahedron": math.sqrt(2) / 2,
                          "octahedron": 4 * math.sqrt(3) / 9}

_MISSING = re.compile(r"^REMARK 465\s+(?:\d+\s+)?([A-Z0-9]{1,3})\s+(\S)\s+(-?\d+)([A-Z]?)\s*$")


@dataclass
class _Atom:
    record: str
    name: str
    resname: str
    chain: str
    resseq: int
    icode: str
    element: str
    xyz: tuple[float, float, float]


@dataclass
class SystemEstimate:
    """What setup is expected to build, and what the expectation rests on."""

    solute_atoms: int
    net_charge: int
    residues: int
    chains: list[str]
    #: Copies of those chains the assembly's symmetry operators build.
    copies: int
    ligands: list[str]
    gaps_built: int
    box_shape: str
    padding_nm: float
    padding_used_nm: float
    #: The least perpendicular width of the triclinic cell OpenMM builds,
    #: which is what a cutoff is held to (under half of it).
    narrowest_nm: float
    volume_nm3: float
    grows: bool
    refuses: bool
    waters: int
    ions_positive: int
    ions_negative: int
    positive_ion: str
    negative_ion: str
    water_model: str
    particles: int
    notes: list[str] = field(default_factory=list)
    #: The distance between the box's opposite faces, which is the distance
    #: from any atom to its own nearest periodic image: the size of the box
    #: as its shape is drawn. For a dodecahedron, the narrowest width over
    #: the square root of a half.
    width_nm: float = 0.0

    def as_record(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def estimate_system(structure: str | Path, setup: dict[str, Any] | None = None,
                    ) -> SystemEstimate:
    """The box and the particle count setup would build from ``structure``
    under the ``setup`` block of a config.

    Refuses (``setup.estimate.unavailable``) what it cannot estimate: a file
    with no coordinates, and a membrane system, whose box is the bilayer's.
    """
    from fastmdxplora.refusals import StudyError

    setup = dict(setup or {})
    if setup.get("membrane"):
        raise StudyError("A membrane system's box is sized by its bilayer, which this "
                         "does not build.", code="setup.estimate.unavailable")
    lines = Path(structure).read_text(encoding="utf-8", errors="replace").splitlines()
    atoms = _atoms(lines)
    if not atoms:
        raise StudyError("The file holds no coordinates.", code="setup.estimate.unavailable")
    notes: list[str] = []

    chains, copies = _chains_setup_keeps(lines, atoms, setup, notes)
    kept = [a for a in atoms if a.chain in chains or not chains]
    heterogens = str(setup.get("heterogens") or "auto").lower()
    if setup.get("keep_heterogens"):
        heterogens = "keep"

    polymer: dict[tuple[str, int, str], str] = {}
    others: dict[tuple[str, int, str], list[_Atom]] = {}
    has_hydrogens = False
    for atom in kept:
        key = (atom.chain, atom.resseq, atom.icode)
        name = REPLACED.get(atom.resname, atom.resname) if setup.get(
            "replace_nonstandard_residues", True) else atom.resname
        if name in RESIDUE_ATOMS:
            polymer.setdefault(key, name)
            has_hydrogens = has_hydrogens or atom.element == "H"
        elif atom.resname in WATER_RESNAMES:
            if setup.get("keep_water"):
                others.setdefault(key, []).append(atom)
        else:
            others.setdefault(key, []).append(atom)

    gaps = _gaps(lines, polymer, chains)
    residues = dict(polymer)
    residues.update(gaps)
    disulfides = _disulfides(kept, polymer)

    count = 0
    charge = 0
    for key, name in residues.items():
        if name == "CYS" and key in disulfides:
            name = "CYX"
        count += RESIDUE_ATOMS[name]
        charge += RESIDUE_CHARGE.get(name, 0)
    for segment in _segments(residues):
        first, last = residues[segment[0]], residues[segment[-1]]
        if first in _NUCLEOTIDES:
            # A 5' end has no phosphate (three atoms and its charge fewer, one
            # hydrogen more) and a 3' end one hydrogen more.
            count += -2 + 1
            charge += 1
        else:
            count += (2 if first not in _CAPS else 0) + (1 if last not in _CAPS else 0)

    ligands: list[str] = []
    water_kept = 0
    for members in others.values():
        resname = members[0].resname
        if resname in WATER_RESNAMES:
            water_kept += 1
            continue
        if resname in ION_RESNAMES or resname in ION_CHARGE:
            if heterogens == "drop":
                continue
            count += len(members)
            charge += ION_CHARGE.get(resname, 0)
            continue
        if heterogens == "drop":
            continue
        heavy = sum(1 for a in members if a.element != "H")
        hydrogens = sum(1 for a in members if a.element == "H")
        # A deposited ligand rarely carries its hydrogens; a drug-like
        # molecule has about one for each heavy atom.
        count += heavy + (hydrogens or heavy)
        ligands.append(resname)
    if setup.get("ligand_net_charge") is not None:
        charge += int(setup["ligand_net_charge"])
    count += 3 * water_kept
    count *= copies
    charge *= copies

    coordinates, elements = _coordinates(kept, lines, chains, copies, heterogens)
    centre = [(min(c[i] for c in coordinates) + max(c[i] for c in coordinates)) / 2
              for i in range(3)]
    radius = max(math.dist(c, centre) for c in coordinates) / 10.0
    if not has_hydrogens:
        radius += HYDROGEN_REACH_NM

    shape = str(setup.get("box_shape") or "dodecahedron").lower()
    padding = float(setup.get("solvent_padding_nm") or 1.0)
    used, narrowest, width, grows, refuses = _box(radius, padding, shape, setup)
    volume = VOLUME_PER_WIDTH_CUBED.get(shape, 1.0) * width ** 3

    heavy = [c for c, element in zip(coordinates, elements) if element != "H"] or coordinates
    in_the_box = (WATER_PER_NM3 * (volume - _excluded_volume(heavy))
                  - WATERS_LOST_AT_THE_FACES * volume ** (2 / 3))
    waters = max(0, int(round(in_the_box)))
    positive = negative = 0
    if setup.get("neutralize", True):
        if charge > 0:
            negative += charge
        else:
            positive -= charge
    concentration = float(setup.get("ion_concentration_M", 0.15) or 0.0)
    pairs = int(math.floor((waters - positive - negative) * concentration / WATER_MOLARITY + 0.5))
    positive += max(pairs, 0)
    negative += max(pairs, 0)
    waters -= positive + negative

    model = str(setup.get("water_model") or "tip3p").lower()
    sites = WATER_SITES.get(model, 3)
    return SystemEstimate(
        solute_atoms=count, net_charge=charge, residues=len(residues) * copies,
        chains=list(chains) or sorted({a.chain for a in atoms}), copies=copies,
        ligands=sorted(set(ligands)),
        gaps_built=len(gaps) * copies, box_shape=shape, padding_nm=padding,
        padding_used_nm=used, narrowest_nm=narrowest, volume_nm3=volume, grows=grows,
        refuses=refuses, waters=waters, ions_positive=positive, ions_negative=negative,
        positive_ion=str(setup.get("ion_positive") or "Na+"),
        negative_ion=str(setup.get("ion_negative") or "Cl-"), water_model=model,
        particles=count + sites * waters + positive + negative, notes=notes,
        width_nm=width)


def _atoms(lines: list[str]) -> list[_Atom]:
    """The first model's atoms, as setup reads them."""
    found: list[_Atom] = []
    for line in lines:
        if line.startswith("ENDMDL"):
            break
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            resseq = int(line[22:26])
        except ValueError:
            continue
        name = line[12:16].strip()
        element = line[76:78].strip().upper() or re.sub(r"[^A-Z]", "", name.upper())[:1]
        altloc = line[16:17].strip()
        if altloc not in ("", "A", "1"):
            continue
        found.append(_Atom(record=line[:6].strip(), name=name,
                           resname=line[17:20].strip().upper(),
                           chain=line[21:22].strip() or "A", resseq=resseq,
                           icode=line[26:27].strip(), element=element, xyz=xyz))
    return found


def _chains_setup_keeps(lines: list[str], atoms: list[_Atom], setup: dict[str, Any],
                        notes: list[str]) -> tuple[list[str], int]:
    """The chains setup keeps, and how many copies of them it builds."""
    from fastmdxplora.setup.assembly import read_assemblies

    named = setup.get("chains")
    if named:
        return [str(c) for c in (named if isinstance(named, list) else [named])], 1
    present = sorted({a.chain for a in atoms if a.record == "ATOM"})
    usable = [a for a in read_assemblies(lines) if set(a.chains) <= set(present)]
    candidates = [a for a in usable if a.by_authors] or usable
    if not candidates:
        return [], 1
    chosen = candidates[0]
    if len(candidates) > 1:
        notes.append(
            f"The file declares {len(candidates)} biological assemblies; this assumes "
            f"the first ({chosen.describe()}). Setup chooses between assemblies that "
            "hold the same molecules and asks which to simulate where they differ; "
            "`chains` settles it.")
    elif chosen.generated:
        notes.append(f"Setup builds {chosen.describe()}.")
    return list(chosen.chains), max(1, chosen.copies)


def _gaps(lines: list[str], polymer: dict[tuple[str, int, str], str],
          chains: list[str]) -> dict[tuple[str, int, str], str]:
    """Missing residues between resolved ones, which setup builds; those past
    a chain's ends it does not (`build_missing_termini` is off)."""
    ranges: dict[str, tuple[int, int]] = {}
    for chain, number, _ in polymer:
        low, high = ranges.get(chain, (number, number))
        ranges[chain] = (min(low, number), max(high, number))
    gaps: dict[tuple[str, int, str], str] = {}
    for line in lines:
        match = _MISSING.match(line)
        if not match:
            continue
        resname, chain, number, icode = match.group(1), match.group(2), int(match.group(3)), match.group(4)
        name = REPLACED.get(resname, resname)
        if name not in RESIDUE_ATOMS or (chains and chain not in chains) or chain not in ranges:
            continue
        low, high = ranges[chain]
        if low < number < high:
            gaps[(chain, number, icode)] = name
    return gaps


def _segments(residues: dict[tuple[str, int, str], str]) -> list[list[tuple[str, int, str]]]:
    """Each chain's residues in order, split where the chain is broken other
    than by a gap setup builds: one segment per chain here."""
    by_chain: dict[str, list[tuple[str, int, str]]] = {}
    for key in residues:
        by_chain.setdefault(key[0], []).append(key)
    return [sorted(keys, key=lambda k: (k[1], k[2])) for keys in by_chain.values()]


def _disulfides(atoms: list[_Atom], polymer: dict[tuple[str, int, str], str]) -> set:
    """Cysteines bonded through their sulfurs, which lose a hydrogen each."""
    sulfurs = [(a.chain, a.resseq, a.icode, a.xyz) for a in atoms
               if a.name == "SG" and polymer.get((a.chain, a.resseq, a.icode)) in ("CYS", "CYX")]
    bonded = set()
    for i, first in enumerate(sulfurs):
        for second in sulfurs[i + 1:]:
            if math.dist(first[3], second[3]) < 2.5:
                bonded.add(first[:3])
                bonded.add(second[:3])
    return bonded


def _coordinates(kept: list[_Atom], lines: list[str], chains: list[str], copies: int,
                 heterogens: str = "auto") -> tuple[list[tuple[float, float, float]], list[str]]:
    """What setup keeps, in the place it keeps it: each chain and ligand
    (crystal water where kept), and the copies the assembly's operators make
    of them. With each atom's element."""
    def kept_here(atom: _Atom) -> bool:
        if atom.resname in WATER_RESNAMES:
            return False
        if heterogens == "drop" and atom.resname not in RESIDUE_ATOMS \
                and REPLACED.get(atom.resname) not in RESIDUE_ATOMS:
            return False
        return True

    atoms = [a for a in kept if kept_here(a)] or kept
    if copies > 1 and chains:
        from fastmdxplora.setup.assembly import read_assemblies

        chosen = next((a for a in read_assemblies(lines) if a.chains == chains), None)
        if chosen is not None:
            moved: list[tuple[float, float, float]] = []
            elements: list[str] = []
            for part_chains, operators in chosen.parts:
                mine = [a for a in atoms if a.chain in part_chains]
                for op in operators:
                    for atom in mine:
                        x, y, z = atom.xyz
                        moved.append(tuple(op[r][0] * x + op[r][1] * y + op[r][2] * z + op[r][3]
                                           for r in range(3)))
                        elements.append(atom.element)
            if moved:
                return moved, elements
    return [a.xyz for a in atoms], [a.element for a in atoms]


def _excluded_volume(points: list[tuple[float, float, float]]) -> float:
    """The volume, in nm^3, within ``EXCLUSION_RADIUS_NM`` of any of these
    points (in Angstrom), counted on a grid."""
    import numpy as np

    xyz = np.asarray(points, dtype=float) / 10.0
    radius = EXCLUSION_RADIUS_NM
    # 0.1 nm gives the builds' water to their own precision; a large
    # assembly is counted more coarsely to stay quick.
    step = 0.1 if len(xyz) <= 20_000 else 0.2
    low = xyz.min(axis=0) - radius - step
    shape = np.ceil((xyz.max(axis=0) + radius + step - low) / step).astype(int) + 2
    inside = np.zeros(shape, dtype=bool)
    reach = int(np.ceil(radius / step)) + 1
    span = np.arange(-reach, reach + 1)
    offsets = np.stack(np.meshgrid(span, span, span, indexing="ij"), -1).reshape(-1, 3)
    for start in range(0, len(xyz), 2000):
        atoms = xyz[start:start + 2000]
        cells = np.floor((atoms - low) / step).astype(int)[:, None, :] + offsets[None, :, :]
        centres = low + step * (cells + 0.5)
        near = np.sum((centres - atoms[:, None, :]) ** 2, axis=2) <= radius * radius
        marked = cells[near]
        inside[marked[:, 0], marked[:, 1], marked[:, 2]] = True
    return float(inside.sum()) * step ** 3


def _box(radius: float, padding: float, shape: str, setup: dict[str, Any]
         ) -> tuple[float, float, float, bool, bool]:
    """The padding setup uses, the narrowest width and the width of the box it
    builds, whether it grows the padding for the cutoff and whether it
    refuses; by OpenMM's rule and setup's growth, as `prepare` applies them."""
    from fastmdxplora.setup.prepare import (
        NARROWEST_WIDTH_PER_SIZE,
        NPT_CONTRACTION_MARGIN,
        padding_that_reaches,
        sized_by_the_padding,
    )

    factor = NARROWEST_WIDTH_PER_SIZE.get(shape, 1.0)
    width = max(2 * radius + padding, 2 * padding)
    narrowest = factor * width
    cutoff = _cutoff(setup)
    method = str(setup.get("nonbonded_method") or "PME")
    if cutoff is None or method not in ("PME", "Ewald", "CutoffPeriodic"):
        return padding, narrowest, width, False, False
    wanted = 2.0 * cutoff * NPT_CONTRACTION_MARGIN
    if narrowest >= wanted:
        return padding, narrowest, width, False, False
    grown = padding_that_reaches(smallest_nm=narrowest, padding_nm=padding,
                                 nonbonded_cutoff_nm=cutoff, box_shape=shape)
    if sized_by_the_padding(grown_nm=grown, nonbonded_cutoff_nm=cutoff, box_shape=shape) \
            or grown - padding <= 0.5:
        width = max(2 * radius + grown, 2 * grown)
        return grown, factor * width, width, True, False
    return padding, narrowest, width, False, True


def _cutoff(setup: dict[str, Any]) -> float | None:
    if setup.get("nonbonded_cutoff_nm") is not None:
        return float(setup["nonbonded_cutoff_nm"])
    from fastmdxplora.setup.forcefields import FALLBACK_CUTOFF_NM, resolve_forcefield

    if setup.get("force_field"):
        return FALLBACK_CUTOFF_NM
    try:
        return float(resolve_forcefield(setup.get("forcefield")).nonbonded[0])
    except Exception:  # noqa: BLE001 - an unknown name is setup's to refuse
        return FALLBACK_CUTOFF_NM


__all__ = ["SystemEstimate", "estimate_system"]
