"""The structures FastMDXplora is expected to handle, and what is true of them.

Every defect found on 2026-08-07 was found by running a real structure and
measuring the result, not by the test suite, which was green at 1,900 tests
throughout. 5WYZ built a system five times larger than it needed and reported
success; 6B73 built one 51 nm across; a receptor was embedded upside down and
every check passed. What those have in common is that the code was asked
whether it finished, not whether what it produced was a molecule.

So the assertions here are about measurable properties of the built system --
its size, its composition, where its parts are -- and not about whether the
run completed. A structure that completes and is wrong is the failure this
corpus exists to catch, because it is the one that costs results rather than
time.

Expectations are deliberately loose. A range wide enough to admit any correct
build and narrow enough to exclude an absurd one is worth more than a precise
number that has to be updated whenever a default changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Expectation:
    """A structure, and what a correct preparation of it looks like."""

    pdb_id: str
    kind: str
    description: str

    chains: str | None = None
    """Passed to `--setup-chains`, where only part of the entry is wanted."""

    membrane: str | None = None
    """A lipid to embed in, for the cases that need one."""

    residues: tuple[int, int] = (1, 100_000)
    """Range the prepared structure's residue count should fall in."""

    ligands: tuple[str, ...] = ()
    """Chemical component IDs that must survive into the prepared system."""

    absent: tuple[str, ...] = ()
    """Components that must not: crystallisation additives, bulk solvent."""

    refused: str | None = None
    """Why this structure does not build, where it does not.

    Two different things were called `known_gap` at first: a structure that
    is refused, and one that builds with a scientific caveat. 5WYZ builds
    perfectly well and loses its glycans, which is a caveat; 2POR is turned
    away, which is a refusal. Conflating them made the corpus assert that
    5WYZ fails, and it does not.

    The test asserts the refusal, so that a structure which starts building
    fails here and the corpus has to be updated deliberately.
    """

    caveat: str | None = None
    """What is true of a build that succeeds but is not the whole molecule.

    Recorded rather than asserted: the run is correct as far as it goes, and
    the caveat belongs in a methods section rather than in a failure.
    """

    notes: str = ""

    @property
    def label(self) -> str:
        return f"{self.pdb_id}{'/' + self.chains if self.chains else ''}"


#: Sized from what a folded protein is: roughly 3 nm across at 100 residues,
#: 7 nm at 1,000, growing as the cube root. The bound is several times that,
#: so a fibril or a long coiled coil passes and only a structure that is not
#: one object fails. 6B73 came to 51 nm for 1,104 residues.
EXTENT_TOLERANCE = 4.0

#: Water is most of a solvated system. Below about 3 atoms per protein atom
#: the box is too tight for the padding asked for; above about 40 something
#: has stretched the solute. Neither is a claim about the science, only about
#: arithmetic that has gone wrong.
SOLVATED_RATIO_RANGE = (3.0, 40.0)


CORPUS: tuple[Expectation, ...] = (
    Expectation(
        pdb_id="1UBQ",
        kind="soluble",
        description="ubiquitin, a plain single-chain protein",
        residues=(70, 90),
        absent=("HOH",),
        notes="The smallest useful case: if this breaks, everything has.",
    ),
    Expectation(
        pdb_id="181L",
        kind="soluble-ligand",
        description="T4 lysozyme L99A with benzene in the cavity",
        residues=(150, 175),
        ligands=("BNZ",),
        absent=("HOH", "HED", "CL"),
        notes="Benzene in the engineered cavity; the reference case all day.",
    ),
    Expectation(
        pdb_id="5WYZ",
        kind="multimer-glycoprotein",
        description="TLR8, a biological homodimer, glycosylated, with a ligand",
        # 1,574 as prepared: 1,038 protein residues over two chains, plus
        # the hydrogens PDBFixer adds as their own residues in the count.
        residues=(1500, 1650),
        ligands=("7VF",),
        absent=("HOH", "NAG", "BMA", "MAN"),
        caveat=(
            "The N-linked glycans are discarded: a glycan is covalently bonded "
            "to ASN, so the small-molecule route cannot parameterise it and "
            "GLYCAM is not assigned. The run is of a deglycosylated protein, "
            "which matters for anything reading surface burial."
        ),
        notes="Both chains are wanted here -- the dimer is the functional unit.",
    ),
    Expectation(
        pdb_id="4HHB",
        kind="cofactor-metal",
        description="haemoglobin: four chains, four haems, four irons",
        residues=(550, 600),
        absent=("HOH",),
        refused=(
            "Haem needs dedicated parameters that are not assigned "
            "automatically, and FastMDXplora says so, naming CHARMM36, CGenFF "
            "and MCPB.py. That is the correct answer: a haem iron is not "
            "something a small-molecule force field can be trusted to guess. "
            "The capability gap is real and the behaviour is right."
        ),
        notes=(
            "The cofactor case. A haem is not a ligand of convenience: it is "
            "the reason the protein exists."
        ),
    ),
    Expectation(
        pdb_id="1U19",
        kind="membrane-helical",
        description="rhodopsin, an alpha-helical membrane protein",
        chains="A",
        membrane="POPC",
        residues=(320, 360),
        refused=(
            "Retinal is covalently bonded to LYS296 and two palmitates to "
            "cysteines. A covalent adduct is part of the macromolecule, not a "
            "free ligand, and FastMDXplora refuses rather than treating it as "
            "one. Correct, and it means rhodopsin -- the archetypal membrane "
            "protein -- cannot be built from its entry without a force field "
            "that describes the linkage."
        ),
        notes=("The membrane fit finds OPM's normal within 7 degrees from any "
               "starting frame, at a hydrophobic thickness of 3.2 to 3.4 nm."),
    ),
    Expectation(
        pdb_id="2POR",
        kind="membrane-barrel",
        description="porin, a beta-barrel membrane protein",
        membrane="POPC",
        residues=(880, 940),
        notes=(
            "The biological assembly, a trimer of 304-residue barrels. The "
            "belt check refused it at 1.17 against a 0.75 threshold, and then "
            "the long-axis copy check refused the trimer as inverted, since a "
            "monomer is as wide as it is tall. The slab fit counts only the "
            "surface a lipid can reach, so the charged lumen does not count "
            "against the belt, and the copies are compared by the three-fold "
            "that relates them. Built 2026-09-28: 279,657 atoms, 442 POPC "
            "lipids (221 per leaflet), 84% of the protein's atoms between the "
            "phosphate planes."
        ),
    ),
    Expectation(
        pdb_id="2RH1",
        kind="membrane-fusion",
        description="beta2-adrenergic receptor with a T4 lysozyme fusion",
        chains="A",
        membrane="POPC",
        residues=(400, 520),
        refused=(
            "Refused before the bilayer, for its heterogens: ACM and PLM are "
            "covalently bonded to cysteines, and GLC is a free sugar whose "
            "role the structure does not say. The membrane itself is no longer "
            "the obstacle: the belt check refused it at 0.80, because the T4L "
            "fusion swamped a comparison over the whole molecule, and the slab "
            "fit that replaced it places this receptor within a few degrees of "
            "OPM's orientation."
        ),
    ),
    Expectation(
        pdb_id="6B73",
        kind="membrane-large",
        description="mu-opioid receptor with Gi alpha, in a bilayer",
        chains="A,C",
        membrane="POPC",
        residues=(380, 440),
        ligands=("CVV",),
        notes=(
            "The large case: about 193,000 atoms. Also the one that needs "
            "chain selection -- the deposited assembly holds two copies whose "
            "two-fold is not perpendicular to the membrane, so both together "
            "cannot be embedded and one alone can."
        ),
    ),
    Expectation(
        pdb_id="1BNA",
        kind="nucleic-acid",
        description="the Dickerson dodecamer, a B-DNA duplex",
        residues=(20, 28),
        absent=("HOH",),
        notes=(
            "Whether nucleic acids work at all is unknown: there are mentions "
            "in the heterogen and force-field code and amber14-all carries "
            "DNA parameters, and it builds."
        ),
    ),
    Expectation(
        pdb_id="1LMB",
        kind="protein-nucleic",
        description="lambda repressor bound to operator DNA",
        # 219 as prepared: the repressor dimer and the operator duplex.
        residues=(200, 240),
        absent=("HOH",),
        notes=(
            "Protein and DNA together, which needs both parameter sets. It "
            "builds, as does 1BNA -- so nucleic acids work, which was the one "
            "capability nobody here had tested."
        ),
    ),
)


CORPUS_BY_KIND = {expectation.kind: expectation for expectation in CORPUS}


def kinds() -> tuple[str, ...]:
    return tuple(expectation.kind for expectation in CORPUS)
