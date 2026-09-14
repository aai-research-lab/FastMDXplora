"""Detect likely non-protein ligands from a PDB structure.

Separates biologically meaningful ligands (drugs, cofactors) from
crystallographic water and free ions that don't need the dashboard's
ligand tools. Never assumes the ligand is named "LIG" – the detection
is residue-name driven and accepts an explicit CLI override.

The water/ion residue sets live here (where they semantically belong)
and are re-exported from :mod:`fastmdxplora.gui.structure_info` so
that :func:`structure_info.count_structure` can re-use them.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

WATER_RESNAMES = frozenset({
    "HOH", "WAT", "TIP", "TIP3", "SOL", "H2O",
})

ION_RESNAMES = frozenset({
    "NA", "K", "CL", "BR", "I", "F", "MG", "CA", "ZN", "MN", "FE", "CU",
    "NI", "CO", "CD", "HG", "PB", "CS", "RB", "LI", "BA", "SR", "AU", "AG",
    "AL", "CR", "SN", "PT",
    "NA+", "K+", "CL-", "MG2+", "CA2+", "ZN2+",
})

# Public so structure_info can re-use without importing this file's
# internals; previously exposed only with a leading underscore, but
# structure_info shares the canonical copy.
AMINO_ACID_RESNAMES = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "HID", "HIE", "HIP", "ILE", "LEU", "LYS", "MET", "PHE", "PRO",
    "SER", "THR", "TRP", "TYR", "VAL",
    # Modified residues PDBFixer commonly produces
    "MSE", "SEC", "PYL",
})

#: Capping groups. Chemically they are not amino acids, and by the PDB's
#: reckoning they are heterogens -- but they terminate a chain and belong to
#: the polymer, so counting them out of the protein counts them in as
#: something else. Alanine dipeptide reported one protein residue, ten protein
#: atoms, and two ligands, when the molecule is three residues of twenty-two
#: atoms and has no ligand at all. Worse than a miscount: a cap surfaced as a
#: ligand invites a study of the contacts between a residue and its own
#: backbone terminus.
CAPPING_RESNAMES = frozenset({"ACE", "NME", "NHE", "NH2", "FOR", "NMA"})

#: What counts as the polymer, for deciding what is left over.
POLYMER_RESNAMES = AMINO_ACID_RESNAMES | CAPPING_RESNAMES

# Legacy private alias kept for backwards compatibility within the package.
_AMINO_ACID_RESNAMES = AMINO_ACID_RESNAMES

# Cofactors / crystallographic additives that are typically retained but
# aren't interesting "ligands" for the dashboard ligand panel. Filtered
# by default to avoid clutter.
COMMON_COFACTORS = frozenset({
    "NAG", "MAN", "BMA", "FUC", "GLC", "GAL", "NDG",  # glycans
    "SO4", "PO4", "GOL", "EDO", "PEG", "DMS", "ACT",  # buffers / cryoprotectants
})


def _residue_sort_key(resi: str) -> tuple[int, str]:
    """Numeric-then-string fallback sort key for residue IDs.

    PDB residue IDs are integers with optional insertion-code suffixes
    (e.g. ``"10A"``). Sorting by raw strings produces ``"10" < "2"``,
    so values are coerced to int when possible and otherwise sorted by the raw
    value to keep stable ordering for insertion-code variants.
    """
    try:
        return (int(resi), "")
    except (TypeError, ValueError):
        return (0, str(resi))


def detect_ligands(
    residue_keys: Iterable[tuple[str, str, str]],
    *,
    explicit: Iterable[str] | None = None,
    include_cofactors: bool = False,
) -> dict[str, Any]:
    """Identify likely ligand residues.

    Parameters
    ----------
    residue_keys
        Iterable of (chain, resname, resi) tuples. Typically produced by
        :mod:`fastmdxplora.gui.structure_info` walking the PDB.
    explicit
        Optional iterable of residue names that should always be treated
        as ligands, even if they would otherwise be filtered out
        (e.g. ``{"LIG"}`` from ``--setup-ligand-name``). Explicit names
        *override* the water/ion/amino-acid filters.
    include_cofactors
        When True, common cofactors (NAG, GOL, SO4, ...) are also
        surfaced. Defaults to False so the ligand panel focuses on the
        chemistry-of-interest ligand.

    Returns
    -------
    dict with keys ``instances`` (a list of dicts), ``resnames`` (sorted
    unique residue names), and ``cofactors`` (filtered list of cofactor
    names found).
    """
    explicit_set = {name.upper() for name in (explicit or ())}
    by_resname: Counter[str] = Counter()
    instances: list[dict[str, Any]] = []
    cofactors_found: set[str] = set()

    for chain, resname, resi in residue_keys:
        rn = resname.upper()
        if not rn:
            continue
        # Explicit ligands win over the water/ion/amino-acid filters so
        # users can name a residue of interest even if its residue code
        # overlaps with water or an ion (rare but possible).
        if rn not in explicit_set:
            if rn in WATER_RESNAMES or rn in ION_RESNAMES:
                continue
            if rn in POLYMER_RESNAMES:
                continue
        is_cofactor = rn in COMMON_COFACTORS and rn not in explicit_set
        if is_cofactor:
            # Track cofactor-tagged residue names regardless of whether
            # they're included as ligands — the UI uses this list to
            # label instances that may not be the ligand of interest.
            cofactors_found.add(rn)
            if not include_cofactors:
                continue
        by_resname[rn] += 1
        instances.append(
            {
                "chain": chain or "A",
                "resname": rn,
                "resi": resi,
                "explicit": rn in explicit_set,
                "cofactor": is_cofactor,
            }
        )

    return {
        "instances": sorted(
            instances,
            key=lambda inst: (
                inst["chain"],
                inst["resname"],
                _residue_sort_key(inst["resi"]),
            ),
        ),
        "resnames": sorted(by_resname.keys()),
        "resname_counts": dict(by_resname),
        "cofactors": sorted(cofactors_found),
    }


def normalise_ligand_resname(value: str | None) -> str | None:
    """Return the residue name to focus the ligand panel on.

    Accepts ``"EPE"``, ``"epe"``, ``"LIG"``. Returns uppercase or ``None``
    on empty/None input so callers can safely drop it from options dicts.
    """
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def filter_pdb_to_ligand(
    pdb_text: str, ligand_resname: str
) -> str:
    """Return PDB text containing only the ligand atoms + END.

    Used by the binding-pocket / ligand-tools features that want just the
    ligand string for the 3Dmol viewer. Caller is responsible for
    cross-origin / size validation; this helper does no IO.
    """
    if not ligand_resname:
        return pdb_text
    target = ligand_resname.upper()
    out_lines: list[str] = []
    for line in pdb_text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            out_lines.append(line)
            continue
        rn = line[17:20].strip().upper()
        if rn == target:
            out_lines.append(line)
    out_lines.append("END")
    return "\n".join(out_lines)
