"""What MDTraj's `protein` keyword covers, made the whole protein.

MDTraj decides a residue is protein by its name alone, against one set
(`mdtraj.core.residue_names._PROTEIN_RESIDUES`), and that set lacks
residue names the force fields this package prepares with write for
ordinary amino acids: AMBER's disulfide cysteine CYX, its protonated
aspartate ASH, its histidine tautomers HID, HIE and the protonated HSP,
the C-terminal amide caps NHE and NH2, and (MDTraj 1.10) CHARMM's HSD.
GROMACS writes a few more for the same residues.

So on a system with a disulfide, `topology.select("protein")` returned a
protein with every bridged cysteine cut out: trypsin's twelve CYX left the
SASA of 211 residues of 223, with the neighbours of each hole exposed (96.2
against 92.5 nm2), its DSSP changed at 38 residues, its radius of gyration
1.1% large, and the protein side of every ligand-interaction analysis
blind to them. `backbone`, `sidechain` and every other keyword MDTraj
builds on `Residue.is_protein` missed them the same way, so the names are
added to MDTraj's own set rather than to one keyword's expansion: one
definition, which every selection, typed or written in the code, reads.

They are added the moment MDTraj's topology module is imported, by a hook
`fastmdxplora/__init__.py` installs (:func:`recognise_from_the_first_import`),
or at once where MDTraj is already loaded, so a selection gives the same
atoms whatever was imported before it; adding them where the analysis
package happened to be imported gave a restraint on `protein` 8 atoms in
one process and 20 in another. Each name is given its one-letter code
beside it (CYX is C, HIE is H; a cap has none), which `Residue.code` and
`Topology.to_fasta` read and which raised on an unlisted name. Nothing is
taken out of either table, so a selection that matched an atom still does.
"""

from __future__ import annotations

import importlib.abc
import sys
from typing import Any

#: Amino-acid residue names that force fields write and MDTraj does not
#: count as protein, each with its one-letter code (None for a cap):
#: AMBER (CYX, ASH, HID, HIE, HSP; the NHE and NH2 caps), CHARMM (HSD in
#: MDTraj 1.10) and GROMACS (HISD, HISE, HISH, CYS1, CYS2, LYSN, ASPH, GLUH).
VARIANT_CODES: dict[str, str | None] = {
    "CYX": "C", "ASH": "D", "HID": "H", "HIE": "H", "HSP": "H", "HSD": "H",
    "NHE": None, "NH2": None,
    "HISD": "H", "HISE": "H", "HISH": "H", "CYS1": "C", "CYS2": "C",
    "LYSN": "K", "ASPH": "D", "GLUH": "E",
}

PROTEIN_VARIANTS: frozenset[str] = frozenset(VARIANT_CODES)

#: The alpha carbons of the protein. `name CA` alone also takes a calcium
#: ion, whose residue and atom PDB and OpenMM both name CA: on 3PTB a
#: rigid protein beside a calcium that moved 1.5 nm read an RMSD of 0.1 nm
#: and an RMSF row for the ion.
ALPHA_CARBONS = "protein and name CA"

_TOPOLOGY = "mdtraj.core.topology"


def recognise_variants() -> None:
    """Add :data:`VARIANT_CODES` to MDTraj's protein residues and codes.

    Idempotent. `Residue.is_protein` reads `_PROTEIN_RESIDUES` from
    `mdtraj.core.topology`'s namespace at each call, which is where the
    selection language's keywords look, so that name is replaced (and
    `residue_names`' kept in step); `_AMINO_ACID_CODES` is one dict both
    modules hold, extended in place.
    """
    import mdtraj.core.residue_names as names
    import mdtraj.core.topology as topology

    for name, code in VARIANT_CODES.items():
        names._AMINO_ACID_CODES.setdefault(name, code)
    wanted = frozenset(topology._PROTEIN_RESIDUES) | PROTEIN_VARIANTS
    if wanted != topology._PROTEIN_RESIDUES:
        topology._PROTEIN_RESIDUES = wanted
    if wanted != names._PROTEIN_RESIDUES:
        names._PROTEIN_RESIDUES = wanted


class _WhenMDTrajLoads(importlib.abc.MetaPathFinder):
    """Recognises the names as soon as MDTraj's topology module has run,
    without importing MDTraj before anything asks for it."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname != _TOPOLOGY:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        run = getattr(loader, "exec_module", None)
        if loader is None or run is None:
            return spec

        def exec_module(module: Any) -> None:
            run(module)
            sys.modules[fullname] = module
            recognise_variants()

        loader.exec_module = exec_module  # type: ignore[method-assign]
        _uninstall()
        return spec


_HOOK = _WhenMDTrajLoads()


def _uninstall() -> None:
    try:
        sys.meta_path.remove(_HOOK)
    except ValueError:
        pass


def recognise_from_the_first_import() -> None:
    """At once if MDTraj's topology is loaded; otherwise when it is."""
    if _TOPOLOGY in sys.modules:
        recognise_variants()
    elif _HOOK not in sys.meta_path:
        sys.meta_path.insert(0, _HOOK)
