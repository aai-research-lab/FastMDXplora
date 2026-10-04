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
blind to them. A finding named HIE, HID and HSP; the cysteines went
unsaid.

The names are added to MDTraj's set once, here, where the analysis
package is imported, so `protein` means the same protein in every
analysis, in the GUI and in a selection a person types. Nothing is taken
out of the set, so a selection that matched an atom still matches it.
"""

from __future__ import annotations

#: Amino-acid residue names that force fields write and MDTraj does not
#: count as protein: AMBER (CYX, ASH, HID, HIE, HSP; NHE and NH2 caps),
#: CHARMM (HSD in MDTraj 1.10) and GROMACS (HISD, HISE, HISH, CYS1, CYS2,
#: LYSN, ASPH, GLUH).
PROTEIN_VARIANTS: frozenset[str] = frozenset({
    "CYX", "ASH", "HID", "HIE", "HSP", "HSD", "NHE", "NH2",
    "HISD", "HISE", "HISH", "CYS1", "CYS2", "LYSN", "ASPH", "GLUH",
})

#: The alpha carbons of the protein. `name CA` alone also takes a calcium
#: ion, whose residue and atom PDB and OpenMM both name CA: on 3PTB a
#: rigid protein beside a calcium that moved 1.5 nm read an RMSD of 0.1 nm
#: and an RMSF row for the ion.
ALPHA_CARBONS = "protein and name CA"


def recognise_variants() -> None:
    """Add :data:`PROTEIN_VARIANTS` to MDTraj's protein residues.

    Idempotent. `Residue.is_protein` reads the set from
    `mdtraj.core.topology`'s namespace at each call, which is where the
    selection language's `protein` keyword looks, so that is the name
    replaced; `residue_names` is kept in step for anything reading it there.
    """
    import mdtraj.core.residue_names as names
    import mdtraj.core.topology as topology

    wanted = frozenset(topology._PROTEIN_RESIDUES) | PROTEIN_VARIANTS
    if wanted != topology._PROTEIN_RESIDUES:
        topology._PROTEIN_RESIDUES = wanted
    if wanted != names._PROTEIN_RESIDUES:
        names._PROTEIN_RESIDUES = wanted
