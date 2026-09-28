"""Which residues are lipids, in one place.

Several readers need to know whether a residue belongs to a bilayer: the
barostat (a membrane is coupled in its plane and along its normal
separately), the crash diagnosis, the default bilayer of a `membrane_depth`
coordinate, and the membrane analyses. Each kept its own list, and each list
held the full lipid names while the bilayers OpenMM builds carry the
three-character names of its membrane patches: `DMP` for DMPC, `DOP` for
DOPC, `DPP` for DPPC, `DLP` for DLPC and DLPE, `POP` for POPC and POPE. So a
DMPC, DOPC, DPPC, DLPC or DLPE bilayer was not recognised as a membrane, and
was coupled isotropically, which squeezes it.

A name is not enough on its own. Several of these are also codes in the
PDB's chemical component dictionary: `POP` is pyrophosphate, `PC`
phosphocholine, `CHL` chlorophyll b. A soluble enzyme with a pyrophosphate
bound is not a membrane, and coupling its box in the plane of a bilayer it
does not have is wrong. So a system is a bilayer when it holds enough lipid
molecules to be one (:func:`is_bilayer`), not when one residue carries a
lipid's name.

This module imports nothing, so a reader that must not fail to load (the
crash diagnosis) can depend on it.
"""

from __future__ import annotations

__all__ = ["BILAYER_MINIMUM_LIPIDS", "BUILT_LIPIDS", "LIPID_RESIDUE_NAMES",
           "STEROLS", "TAIL_RESIDUES", "is_bilayer", "is_lipid", "is_sterol",
           "lipid_count", "lipid_selection"]

#: The lipids OpenMM builds a bilayer from, and the residue name each carries
#: in the topology it builds: the name in its pre-equilibrated patch.
BUILT_LIPIDS: dict[str, str] = {
    "POPC": "POP",
    "POPE": "POP",
    "DLPC": "DLP",
    "DLPE": "DLP",
    "DMPC": "DMP",
    "DOPC": "DOP",
    "DPPC": "DPP",
}

#: Every residue name read as a lipid: the names of built bilayers, the full
#: names of the same lipids and of the common others (as CHARMM-GUI and the
#: CHARMM36 force field name them), cholesterol, and the head and tail
#: residues AMBER's Lipid21 and Lipid17 split a phospholipid into.
LIPID_RESIDUE_NAMES: frozenset[str] = frozenset({
    *BUILT_LIPIDS.values(),
    *BUILT_LIPIDS,
    "POPG", "POPS", "POPA", "POPI", "DOPE", "DOPG", "DOPS", "DMPE", "DMPG",
    "DMPS", "DPPE", "DPPG", "DPPS", "DSPC", "SOPC", "SAPC", "SDPC", "PSM",
    "SSM", "CHL1", "CHOL", "CHL",
    # AMBER Lipid17/Lipid21: heads and tails as residues of their own.
    "PC", "PE", "PS", "PGR", "OL", "PA", "MY", "LA", "ST",
})


#: Sterols: a lipid with no phosphate, whose head is its hydroxyl oxygen.
STEROLS: frozenset[str] = frozenset({"CHL1", "CHOL", "CHL"})

#: The acyl chains AMBER's Lipid17 and Lipid21 make residues of their own.
#: Each is part of a lipid whose head is another residue, so it is not a
#: lipid when lipids are counted.
TAIL_RESIDUES: frozenset[str] = frozenset({"OL", "PA", "MY", "LA", "ST"})

#: Fewer lipid molecules than this are not a bilayer. The smallest bilayer
#: worth simulating has a few dozen lipids per leaflet (one OpenMM patch has
#: about eighty); a crystal structure's bound lipids, or a ligand whose PDB
#: code happens to be a lipid's residue name, are a handful.
BILAYER_MINIMUM_LIPIDS = 20


def is_lipid(resname: str) -> bool:
    """Whether a residue of this name is part of a bilayer."""
    return str(resname).strip().upper() in LIPID_RESIDUE_NAMES


def is_sterol(resname: str) -> bool:
    """Whether a residue of this name is a sterol."""
    return str(resname).strip().upper() in STEROLS


def lipid_count(resnames) -> int:
    """How many lipid molecules these residue names hold.

    A split lipid is counted once, by its head: its acyl-chain residues are
    parts of it.
    """
    count = 0
    for name in resnames:
        key = str(name).strip().upper()
        if key in LIPID_RESIDUE_NAMES and key not in TAIL_RESIDUES:
            count += 1
    return count


def is_bilayer(resnames) -> bool:
    """Whether residues of these names are enough lipids to be a bilayer."""
    return lipid_count(resnames) >= BILAYER_MINIMUM_LIPIDS


def lipid_selection() -> str:
    """Every lipid residue, as a selection."""
    return "resname " + " ".join(sorted(LIPID_RESIDUE_NAMES))
