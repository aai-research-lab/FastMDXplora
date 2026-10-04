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

The lipid names are those of the lipid residue templates OpenMM ships:
CHARMM36's (as CHARMM-GUI writes them) and AMBER's Lipid17 and Lipid21, read
from OpenMM's own force field files when OpenMM is installed, and otherwise
from the copy of that list kept here, so an install without OpenMM reads the
same names. A short list kept by hand had about forty, and every other lipid
(DSPE, PLPC, SOPE, a cardiolipin, a ceramide) was counted as protein.

This module imports only the standard library, and reading OpenMM's files
cannot fail it, so a reader that must not fail to load (the crash diagnosis)
can depend on it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

__all__ = ["BILAYER_MINIMUM_LIPIDS", "BUILT_LIPIDS", "LIPID_RESIDUE_NAMES",
           "SPLIT_HEADS", "STEROLS", "TAIL_RESIDUES", "WITHOUT_PHOSPHATE",
           "has_no_phosphate", "is_bilayer", "is_lipid", "is_sterol",
           "lipid_count", "lipid_selection", "openmm_lipid_templates"]

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

#: The lipid residue templates OpenMM 8.6.1 ships, as
#: :func:`openmm_lipid_templates` reads them: those of ``charmm36.xml`` and
#: ``charmm36_2024.xml`` with at least two acyl or alkyl chain ends, and every
#: template of ``amber14/lipid17.xml`` and ``amber19/lipid21.xml``. Generated
#: with ``sorted(openmm_lipid_templates()[0])``; a test checks it still holds
#: every one OpenMM ships, so a later OpenMM adding a lipid fails a test
#: rather than a study run without OpenMM.
_SHIPPED_LIPIDS: frozenset[str] = frozenset({
    "23SM", "ABLIPA", "ABLIPB", "APPC", "ASM", "BCLIPA", "BCLIPB", "BCLIPC",
    "BSM", "C6DHPC", "C7DHPC", "CER160", "CER180", "CER181", "CER2", "CER200",
    "CER220", "CER241", "CER3E", "CHAPS", "CHAPSO", "CHL1", "CHNS", "CHSD",
    "CHSP", "CJLIPA", "CTLIPA", "DAPA", "DAPC", "DAPE", "DAPG", "DAPS", "DCPC",
    "DDOPC", "DDOPE", "DDOPS", "DDPC", "DEPA", "DEPC", "DEPE", "DEPG", "DEPS",
    "DGPA", "DGPC", "DGPE", "DGPG", "DGPS", "DHPC", "DHPCE", "DIPA", "DLIPE",
    "DLIPI", "DLPA", "DLPC", "DLPE", "DLPG", "DLPS", "DMPA", "DMPC", "DMPCE",
    "DMPE", "DMPEE", "DMPG", "DMPI", "DMPI13", "DMPI14", "DMPI15", "DMPI24",
    "DMPI25", "DMPI2A", "DMPI2B", "DMPI2C", "DMPI2D", "DMPI33", "DMPI34",
    "DMPI35", "DMPS", "DNPA", "DNPC", "DNPE", "DNPG", "DNPS", "DOPA", "DOPC",
    "DOPCE", "DOPE", "DOPEE", "DOPG", "DOPP1", "DOPP2", "DOPP3", "DOPS",
    "DPPA", "DPPC", "DPPE", "DPPEE", "DPPG", "DPPGK", "DPPS", "DSPA", "DSPC",
    "DSPE", "DSPG", "DSPS", "DTPA", "DUPC", "DXPA", "DXPC", "DXPE", "DXPG",
    "DXPS", "DYPA", "DYPC", "DYPE", "DYPG", "DYPS", "ECLIPA", "ECLIPB",
    "ECLIPC", "ERG", "FOIS11", "FOIS9", "HPLIPA", "HPLIPB", "IPPC", "KPLIPA",
    "KPLIPB", "KPLIPC", "LILIPA", "LLPA", "LLPC", "LLPE", "LLPS", "LNACL1",
    "LNACL2", "LNBCL1", "LNBCL2", "LNCCL1", "LNCCL2", "LNDCL1", "LNDCL2",
    "LOACL1", "LOACL2", "LOCCL1", "LOCCL2", "LSM", "MCLIPA", "NGLIPA",
    "NGLIPB", "NGLIPC", "NSM", "OSM", "OSPE", "OYPE", "PALIPA", "PALIPB",
    "PALIPC", "PALIPD", "PALIPE", "PDOPC", "PDOPE", "PHPC", "PLPA", "PLPC",
    "PLPE", "PLPG", "PLPI", "PLPI13", "PLPI14", "PLPI15", "PLPI24", "PLPI25",
    "PLPI2A", "PLPI2B", "PLPI2C", "PLPI2D", "PLPI33", "PLPI34", "PLPI35",
    "PLPS", "PMCL1", "PMCL2", "PMPE", "PMPG", "PNPI", "PNPI13", "PNPI14",
    "PNPI15", "PNPI24", "PNPI25", "PNPI2A", "PNPI2B", "PNPI2C", "PNPI2D",
    "PNPI33", "PNPI34", "PNPI35", "POPA", "POPC", "POPCE", "POPE", "POPEE",
    "POPG", "POPI", "POPI13", "POPI14", "POPI15", "POPI24", "POPI25", "POPI2A",
    "POPI2B", "POPI2C", "POPI2D", "POPI33", "POPI34", "POPI35", "POPP1",
    "POPP2", "POPP3", "POPS", "PPPE", "PSM", "PSPG", "PVCL2", "PVPE", "PVPG",
    "PYPE", "PYPG", "PYPI", "QMPE", "SAPA", "SAPC", "SAPE", "SAPG", "SAPI",
    "SAPI13", "SAPI14", "SAPI15", "SAPI24", "SAPI25", "SAPI2A", "SAPI2B",
    "SAPI2C", "SAPI2D", "SAPI33", "SAPI34", "SAPI35", "SAPS", "SDPA", "SDPC",
    "SDPE", "SDPG", "SDPS", "SELIPB", "SELIPC", "SITO", "SLPA", "SLPC", "SLPE",
    "SLPG", "SLPS", "SOPA", "SOPC", "SOPE", "SOPG", "SOPS", "SSM", "STIG",
    "THCHL", "THDPPC", "TIPA", "TLCL1", "TLCL2", "TMCL1", "TMCL2", "TOCL1",
    "TOCL2", "TRIPAO", "TRPAOP", "TSPC", "TXCL1", "TXCL2", "TYCL1", "TYCL2",
    "VCLIPA", "VCLIPB", "VCLIPC", "VCLIPD", "VCLIPE", "YOPA", "YOPC", "YOPE",
    "YOPS", "YPLIPA", "YPLIPB",
})

#: The templates above with no phosphorus: sterols, ceramides and the like,
#: whose head is a hydroxyl oxygen. Generated with
#: ``sorted(openmm_lipid_templates()[1])``.
_SHIPPED_WITHOUT_PHOSPHATE: frozenset[str] = frozenset({
    "CER160", "CER180", "CER181", "CER2", "CER200", "CER220", "CER241",
    "CER3E", "CHAPS", "CHAPSO", "CHL1", "CHNS", "CHSD", "CHSP", "ERG", "SITO",
    "STIG", "THCHL", "TRIPAO", "TRPAOP",
})

#: Of those, the ones with no nitrogen either: the sterols. Generated with
#: ``sorted(openmm_lipid_templates()[2])``.
_SHIPPED_STEROLS: frozenset[str] = frozenset({
    "CHL1", "CHNS", "CHSD", "CHSP", "ERG", "SITO", "STIG", "THCHL",
})

#: CHARMM36 atom class of an acyl or alkyl chain's terminal methyl carbon. A
#: residue with two of them has two chains, as a phospholipid, sphingolipid
#: or ceramide does, or is a sterol (whose side chain ends in two); a free
#: fatty acid, a detergent or a lysolipid has one, and is not a bilayer lipid.
_CHAIN_END_CLASS = b'class="CTL3"'
_NAME = re.compile(rb'\bname="([^"]*)"')
_ATOM_TYPE = re.compile(rb'<Atom\b[^>]*\btype="([^"]*)"')


def _types_marked(text: bytes, marker: bytes, stop: int) -> set[bytes]:
    """The names of the atom types whose ``<Type>`` element contains ``marker``."""
    found: set[bytes] = set()
    at = text.find(marker, 0, stop)
    while at >= 0:
        tag_start, tag_stop = text.rfind(b"<", 0, at), text.find(b">", at)
        name = _NAME.search(text, tag_start, tag_stop)
        if name is not None:
            found.add(name.group(1))
        at = text.find(marker, tag_stop, stop)
    return found


def _lipid_templates_in(path: Path, every: bool) -> tuple[set[str], set[str], set[str]]:
    """The lipid residue templates in one force field file, those of them with
    no phosphorus, and those with neither phosphorus nor nitrogen (sterols).

    ``every`` takes every template (a file of lipids only); otherwise a
    template is a lipid when two of its atoms are chain-end methyls. A
    template with external bonds is part of a polymer (a lipidated amino
    acid), not a molecule of its own.
    """
    text = path.read_bytes()
    start = text.find(b"<Residues>")
    stop = text.find(b"</Residues>", start)
    if start < 0 or stop < 0:
        return set(), set(), set()
    chain_ends = _types_marked(text, _CHAIN_END_CLASS, start)
    phosphorus = _types_marked(text, b'element="P"', start)
    nitrogen = _types_marked(text, b'element="N"', start)
    lipids: set[str] = set()
    without_phosphate: set[str] = set()
    sterols: set[str] = set()
    for chunk in text[start:stop].split(b"<Residue ")[1:]:
        if b"<ExternalBond" in chunk:
            continue
        types = _ATOM_TYPE.findall(chunk)
        if not every and sum(t in chain_ends for t in types) < 2:
            continue
        name = _NAME.search(chunk)
        if name is None:
            continue
        key = name.group(1).decode("ascii", "replace").strip().upper()
        lipids.add(key)
        if not any(t in phosphorus for t in types):
            without_phosphate.add(key)
            if not any(t in nitrogen for t in types):
                sterols.add(key)
    return lipids, without_phosphate, sterols


def openmm_lipid_templates() -> tuple[frozenset[str], frozenset[str],
                                      frozenset[str]] | None:
    """The lipid residue names OpenMM's force fields have templates for,
    which of them have no phosphate, and which are sterols (neither
    phosphorus nor nitrogen); ``None`` without OpenMM.

    Read from the files of the installed OpenMM without importing it:
    CHARMM36 (``charmm36*.xml``) and AMBER's lipid files
    (``amber*/lipid*.xml``). Takes about half a second.
    """
    try:
        spec = importlib.util.find_spec("openmm")
        if spec is None or not spec.submodule_search_locations:
            return None
        data = Path(next(iter(spec.submodule_search_locations))) / "app" / "data"
        lipids: set[str] = set()
        without_phosphate: set[str] = set()
        sterols: set[str] = set()
        files = [(path, False) for path in sorted(data.glob("charmm36*.xml"))]
        files += [(path, True) for path in sorted(data.glob("amber*/lipid*.xml"))]
        for path, every in files:
            found, unphosphorylated, rings = _lipid_templates_in(path, every)
            lipids |= found
            without_phosphate |= unphosphorylated
            sterols |= rings
    except Exception:  # noqa: BLE001 - the copy kept here stands in
        return None
    if not lipids:
        return None
    return frozenset(lipids), frozenset(without_phosphate), frozenset(sterols)


_READ_FROM_OPENMM = openmm_lipid_templates()

#: The head residues AMBER's Lipid17 and Lipid21 split a lipid into, each
#: bonded to its tails: phosphatidylcholine, -ethanolamine, -serine,
#: -glycerol (two stereoisomers), phosphatidic acid, and sphingomyelin.
SPLIT_HEADS: frozenset[str] = frozenset({"PC", "PE", "PS", "PGR", "PGS", "PH-",
                                         "SPM"})

#: The acyl chains AMBER's Lipid17 and Lipid21 make residues of their own:
#: oleoyl, palmitoyl, myristoyl, lauroyl (LA in Lipid14, LAL since), stearoyl
#: (ST in Lipid17, SA in Lipid21), docosahexaenoyl and arachidonoyl. Each is
#: part of a lipid whose head is another residue, so it is not a lipid when
#: lipids are counted.
TAIL_RESIDUES: frozenset[str] = frozenset({"OL", "PA", "MY", "LA", "LAL", "ST",
                                           "SA", "DHA", "AR"})

#: Sterols: a lipid with no phosphate, whose head is its hydroxyl oxygen.
#: CHL is Lipid21's cholesterol and CHOL the name some GROMACS ports use.
STEROLS: frozenset[str] = frozenset({
    "CHL1", "CHOL", "CHL", *_SHIPPED_STEROLS,
    *(_READ_FROM_OPENMM[2] if _READ_FROM_OPENMM else ()),
})

#: Lipids with no phosphate, whose head is a hydroxyl oxygen: the sterols,
#: and the ceramides.
WITHOUT_PHOSPHATE: frozenset[str] = frozenset({
    *STEROLS, *_SHIPPED_WITHOUT_PHOSPHATE,
    *(_READ_FROM_OPENMM[1] if _READ_FROM_OPENMM else ()),
})

#: Every residue name read as a lipid: the names of built bilayers, every
#: lipid template OpenMM ships, the sterols, and the head and tail residues
#: AMBER's Lipid17 and Lipid21 split a phospholipid into.
LIPID_RESIDUE_NAMES: frozenset[str] = frozenset({
    *BUILT_LIPIDS.values(),
    *BUILT_LIPIDS,
    *_SHIPPED_LIPIDS,
    *(_READ_FROM_OPENMM[0] if _READ_FROM_OPENMM else ()),
    *STEROLS,
    *SPLIT_HEADS,
    *TAIL_RESIDUES,
})

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


def has_no_phosphate(resname: str) -> bool:
    """Whether a lipid of this name has no phosphate (a sterol, a ceramide)."""
    return str(resname).strip().upper() in WITHOUT_PHOSPHATE


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
    """Every lipid residue, as a selection.

    Each name is quoted: Lipid21's PH- and CHARMM36's 23SM are not words
    MDTraj's selection language reads bare.
    """
    return "resname " + " ".join(f'"{name}"' for name in sorted(LIPID_RESIDUE_NAMES))
