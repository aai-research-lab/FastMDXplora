"""Decide what to do with the non-standard residues in a structure.

A crystal structure arrives carrying whatever was in the drop: the ligand the
depositors cared about, the buffer that kept the protein soluble, the glycerol
that stopped it freezing, ions, and water. Preparing a system means deciding
which of those to simulate, and getting that decision wrong is not a cosmetic
error. Discarding a bound ligand silently produces an apo trajectory that reads
as holo; retaining a cryoprotectant simulates something nobody intended.

The rule this module follows is that an ambiguity is never resolved by
guessing. Every heterogen is either

* ``SIMULATE``  -- kept, with a defensible reason,
* ``DISCARD``   -- dropped, with a defensible reason, or
* ``STOP``      -- neither, because the structure does not determine the
  answer. Setup halts and tells the user what to decide.

The third outcome is the point. A tool that quietly picks one interpretation
of an ambiguous structure produces results that look fine and are not.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError

logger = get_logger("setup.heterogens")


class Action(str, Enum):
    """What preparation should do with a heterogen."""

    SIMULATE = "simulate"
    DISCARD = "discard"
    STOP = "stop"


# Solvent, under every name crystallographers and force fields use for it.
WATER_NAMES = frozenset({"HOH", "WAT", "H2O", "DOD", "TIP", "TIP3", "TIP4", "SOL"})

# Monatomic ions. Whether these matter depends on whether they are coordinated
# by the protein, which is a question about geometry rather than about names,
# so they are resolved separately below.
ION_NAMES = frozenset({
    "NA", "K", "LI", "RB", "CS", "MG", "CA", "SR", "BA", "ZN", "MN", "FE",
    "FE2", "CO", "NI", "CU", "CU1", "CD", "HG", "CL", "BR", "IOD", "F",
})

# Substances added to make crystals grow, freeze, or stay soluble. None of
# these is the subject of anyone's experiment. The list is drawn from the
# components that appear most often across the PDB in that role; it does not
# need to be exhaustive, because anything unrecognised is treated as a ligand
# rather than discarded.
CRYSTALLIZATION_ADDITIVES = frozenset({
    # cryoprotectants and polyols
    "GOL", "EDO", "PGE", "PG4", "1PE", "2PE", "P6G", "PEG", "MPD", "TRD",
    "DMS", "MRD", "BU3", "IPA", "EOH", "ACN",
    # buffers
    "EPE", "MES", "TRS", "BTB", "CAC", "IMD", "BIS", "TAM", "PIN", "HEZ",
    # salts and small counter-ions from the crystallization liquor
    "SO4", "PO4", "NO3", "ACT", "CIT", "FLC", "TLA", "MLA", "MLI", "FMT",
    "OXL", "SCN", "AZI", "PER", "BCT", "CO3",
    # reductants and detergents
    "BME",
    # 2-hydroxyethyl disulfide: the oxidised dimer of BME, which is already
    # here. Present in 181L and many other structures reduced with
    # mercaptoethanol, where it is what the reducing agent became.
    "HED", "DTT", "DTU", "TCE", "LDA", "C8E", "OCT", "BOG", "LMT",
    # miscellaneous handling reagents
    "NH4", "EDT",
})

# Sugars are the one class that cannot be judged by name at all. The same
# N-acetylglucosamine is a glycosylation site in one structure and a spectator
# in another; sucrose and trehalose are usually cryoprotectants but are
# substrates for the enzymes that act on them. Guessing would either delete a
# covalent modification or simulate an antifreeze molecule, so the question is
# handed back.
#: Residues that carry a glycan. N-linked glycans attach to asparagine,
#: O-linked to serine or threonine; a LINK naming one of these identifies the
#: sugar as a glycosylation site rather than a free molecule.
GLYCOSYLATION_RESIDUES = frozenset({"ASN", "SER", "THR", "HYP", "TRP"})

SUGARS = frozenset({
    "NAG", "NDG", "BMA", "MAN", "BGC", "GLC", "GAL", "GLA", "FUC", "FUL",
    "XYP", "XYS", "XYL", "SIA", "NGA", "RIP", "RAM", "ARA", "ARB",
    "SUC", "TRE", "MAL", "LAT", "CBI",
})

# Cofactors and prosthetic groups that need parameters a general small-molecule
# force field does not supply. Most of these do have published parameters:
# CHARMM36 covers heme and NAD/NADH natively, CGenFF and CHARMM-GUI's Ligand
# Reader cover FAD and FMN, and AmberTools' MCPB.py builds bonded models for
# metal centres. What none of them do is apply automatically, because PDB atom
# names do not match the force field templates. So these are real, essential,
# and outside what this pipeline can assign unaided.
UNPARAMETERIZABLE = {
    "HEM": "heme", "HEC": "heme c", "HEB": "heme b", "HAS": "heme as",
    "SRM": "siroheme", "CLA": "chlorophyll a", "CHL": "chlorophyll b",
    "BCL": "bacteriochlorophyll", "PHO": "pheophytin",
    "NAD": "NAD+", "NAI": "NADH", "NAP": "NADP+", "NDP": "NADPH",
    "FAD": "FAD", "FMN": "FMN", "FDA": "dihydro-FAD",
    "SF4": "iron-sulfur cluster", "FES": "iron-sulfur cluster",
    "F3S": "iron-sulfur cluster", "CLF": "iron-sulfur cluster",
    "MOO": "molybdate cofactor", "MGD": "molybdopterin",
    "B12": "cobalamin", "COB": "cobalamin", "CNC": "cobalamin",
    "PQQ": "PQQ", "TPQ": "topaquinone",
}

# Residue codes that mean "this is not identified". No chemical
# definition exists, so nothing downstream can parameterize them.
UNKNOWN_NAMES = frozenset({"UNL", "UNK", "UNX", "LIG", "DRG"})

# Distance below which a metal is taken to be coordinated by the protein
# rather than sitting adventitiously in the solvent, in angstrom. Metal-ligand
# bonds to N, O, and S donors are typically 1.9-2.6 A.
COORDINATION_CUTOFF_A = 2.8

# Occupancies closer than this are treated as indistinguishable, so an
# alternate conformation cannot be resolved by preferring the major one.
OCCUPANCY_TIE_TOLERANCE = 0.10


@dataclass(frozen=True)
class Atom:
    """One ATOM or HETATM record, reduced to what classification needs."""

    name: str
    resname: str
    chain: str
    resseq: int
    icode: str
    altloc: str
    occupancy: float
    element: str
    x: float
    y: float
    z: float

    @property
    def key(self) -> tuple[str, str, int, str]:
        """Identity of the residue this atom belongs to."""
        return (self.resname, self.chain, self.resseq, self.icode)


@dataclass(frozen=True)
class Heterogen:
    """One instance of a non-standard residue in the structure."""

    resname: str
    chain: str
    resseq: int
    icode: str
    atoms: tuple[Atom, ...]
    covalent_to: tuple[str, ...] = ()

    @property
    def altlocs(self) -> frozenset[str]:
        return frozenset(a.altloc for a in self.atoms if a.altloc.strip())

    @property
    def occupancy_by_altloc(self) -> dict[str, float]:
        best: dict[str, float] = {}
        for atom in self.atoms:
            code = atom.altloc.strip() or " "
            best[code] = max(best.get(code, 0.0), atom.occupancy)
        return best

    @property
    def label(self) -> str:
        icode = self.icode.strip()
        return f"{self.resname} {self.chain}{self.resseq}{icode}"


@dataclass
class Decision:
    """What to do with one chemical component, and why."""

    resname: str
    action: Action
    reason: str
    instances: tuple[Heterogen, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.instances)

    @property
    def is_monatomic(self) -> bool:
        """One atom, so there is no chemistry to look up.

        Bond orders, formal charges and aromaticity are what a PDB cannot
        express and what an SDF supplies. A lone ion has none of them: a force
        field carries its parameters directly, and asking somebody to fetch
        `ZN_ideal.sdf` asks for a file describing bonds that do not exist.

        Decided from the structure rather than from a list of names, because
        a list would need every ion anybody simulates and would be wrong for
        whichever was missing.
        """
        if not self.instances:
            return False
        if not all(len(i.atoms) == 1 for i in self.instances):
            return False
        # One atom is not enough on its own: a lone carbon is a ligand
        # somebody wrote badly, not an ion, and treating it as one would keep
        # a broken component silently. The element has to be one that occurs
        # as a monatomic ion, which is what a force field has parameters for.
        return self.resname.strip().upper() in ION_NAMES


class AmbiguousStructureError(CodedError, RuntimeError):
    """The structure does not determine what should be simulated.

    Raised rather than resolved, so that no trajectory is ever produced from
    a guess about what the depositors meant.
    """

    default_code = "setup.structure.undetermined"


def _standard_residues() -> frozenset[str]:
    """Polymer residues, which are never heterogens."""
    amino = (
        "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR "
        "TRP TYR VAL HID HIE HIP HSD HSE HSP CYX CYM ASH GLH LYN MSE SEC PYL "
        "ACE NME NHE"
    ).split()
    # Modified residues are polymer, not ligands. Preparation substitutes the
    # standard equivalent, so classifying them here would refuse a structure
    # that is about to be made perfectly ordinary. An oxidised cysteine in a
    # protease is not a bound ligand, and reporting it as a covalent adduct
    # helps nobody.
    modified = (
        "CSO CSD OCS CSS CME CSX SEP TPO PTR HYP PCA MLY M3L KCX ALY "
        "LLP CGU CIR DAL DAR DAS DCY DGL DGN DHI DIL DLE DLY DPN DPR "
        "DSG DSN DTH DTR DTY DVA FME TYS SMC SAH NLE ABA AIB ORN"
    ).split()
    nucleic = (
        "A C G U T I DA DC DG DT DU DI RA RC RG RU "
        "A3 A5 C3 C5 G3 G5 U3 U5 T3 T5 DA3 DA5 DC3 DC5 DG3 DG5 DT3 DT5"
    ).split()
    return frozenset(amino + modified + nucleic)


def parse_structure(pdb_path: str | Path) -> tuple[list[Atom], list[Atom], dict[tuple, set[str]]]:
    """Read a PDB file into polymer atoms, heterogen atoms, and covalent links.

    Returns
    -------
    polymer, heterogens, linked
        ``linked`` maps each residue key named in a LINK record to the names of
        the residues on the other end. The partner is recorded, not merely the
        fact of a link, because a LINK to a monatomic ion means coordination
        while a LINK to an amino acid means covalency, and the two demand
        opposite answers.
    """
    standard = _standard_residues()
    polymer: list[Atom] = []
    heteroatoms: list[Atom] = []
    linked: dict[tuple, set[str]] = defaultdict(set)

    for raw in Path(pdb_path).read_text(encoding="utf-8", errors="ignore").splitlines():
        record = raw[:6].strip()

        if record == "LINK":
            # LINK records name the two partners of a bond by residue. Columns
            # follow the legacy PDB specification. Both ends are read together
            # so each can be told what it is bonded to.
            ends = []
            for start in (17, 47):
                resname = raw[start:start + 3].strip().upper()
                chain = raw[start + 4:start + 5].strip()
                seq = raw[start + 5:start + 9].strip()
                # A LINK record trimmed before its symmetry columns leaves no
                # insertion code, and an absent one is a blank, not a different
                # value. Without this the key never matches the residue it
                # names and the bond is lost without a word.
                icode = raw[start + 9:start + 10] or " "
                if resname and seq.lstrip("-").isdigit():
                    ends.append((resname, chain, int(seq), icode))
            if len(ends) == 2:
                first, second = ends
                linked[first].add(second[0])
                linked[second].add(first[0])
            elif len(ends) == 1:
                # A malformed half-record still announces that something is
                # bonded here; record it without a partner name.
                linked[ends[0]].add("")
            continue

        if record not in {"ATOM", "HETATM"}:
            continue

        try:
            atom = Atom(
                name=raw[12:16].strip(),
                resname=raw[17:20].strip().upper(),
                chain=raw[21:22].strip(),
                resseq=int(raw[22:26]),
                icode=raw[26:27],
                altloc=raw[16:17],
                occupancy=float(raw[54:60] or 1.0),
                element=(raw[76:78].strip() or raw[12:14].strip()).upper(),
                x=float(raw[30:38]),
                y=float(raw[38:46]),
                z=float(raw[46:54]),
            )
        except ValueError:
            # A malformed record is not worth failing the whole run over, but
            # it must not be silently treated as absent either.
            logger.debug("skipping unparsable coordinate line: %s", raw[:30])
            continue

        # Modified residues are deposited as HETATM: an oxidised cysteine in
        # a protease is written that way, and requiring an ATOM record meant
        # the list of known polymer residues never applied to any of them.
        # The name settles it, not the record type.
        if atom.resname in standard:
            polymer.append(atom)
        else:
            heteroatoms.append(atom)

    return polymer, heteroatoms, linked


def group_heterogens(
    heteroatoms: list[Atom], linked: dict[tuple, set[str]]
) -> list[Heterogen]:
    """Collect heterogen atoms into residue instances."""
    grouped: dict[tuple, list[Atom]] = defaultdict(list)
    for atom in heteroatoms:
        grouped[atom.key].append(atom)

    out: list[Heterogen] = []
    for (resname, chain, resseq, icode), atoms in sorted(grouped.items()):
        partners = tuple(sorted(linked.get((resname, chain, resseq, icode), ())))
        out.append(
            Heterogen(
                resname=resname,
                chain=chain,
                resseq=resseq,
                icode=icode,
                atoms=tuple(atoms),
                covalent_to=partners,
            )
        )
    return out


def _min_distance_to_polymer(het: Heterogen, polymer: list[Atom]) -> float:
    """Closest approach to a protein N, O, or S donor atom, in angstrom."""
    donors = [a for a in polymer if a.element in {"N", "O", "S"}]
    if not donors:
        return math.inf
    best = math.inf
    for atom in het.atoms:
        for donor in donors:
            d = math.dist((atom.x, atom.y, atom.z), (donor.x, donor.y, donor.z))
            if d < best:
                best = d
                if best < 1.0:  # already unambiguous
                    return best
    return best



#: Two ions closer than this are alternate positions for one site rather than
#: two sites: even the shortest metal-metal contacts in real structures are
#: longer, and anything this close cannot be simultaneously occupied.
SAME_SITE_CUTOFF_A = 1.5


def _mutually_overlapping(instances: list[Heterogen]) -> list[Heterogen]:
    """Copies of one component that occupy the same position."""
    if len(instances) < 2:
        return []
    overlapping: list[Heterogen] = []
    for i, first in enumerate(instances):
        for second in instances[i + 1:]:
            for a in first.atoms:
                for b in second.atoms:
                    if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) < SAME_SITE_CUTOFF_A:
                        for h in (first, second):
                            if h not in overlapping:
                                overlapping.append(h)
                        break
    return overlapping


def _resolve_by_occupancy(instances: list[Heterogen]) -> tuple[bool, str]:
    """Rank alternate positions by occupancy, or report that they tie."""
    ranked = sorted(
        instances,
        key=lambda h: max((a.occupancy for a in h.atoms), default=0.0),
        reverse=True,
    )
    best = max((a.occupancy for a in ranked[0].atoms), default=0.0)
    runner_up = max((a.occupancy for a in ranked[1].atoms), default=0.0)
    if abs(best - runner_up) < OCCUPANCY_TIE_TOLERANCE:
        return False, (
            f"occupancies are {best:.2f} and {runner_up:.2f}."
        )
    return True, (
        f"{ranked[0].label} at occupancy {best:.2f} kept over "
        f"{ranked[1].label} at {runner_up:.2f}; they occupy one site."
    )


def _altloc_decision(het: Heterogen) -> tuple[bool, str]:
    """Whether alternate conformations can be resolved by occupancy.

    Returns ``(resolved, explanation)``. Two conformations at equal occupancy
    carry no information about which the depositors considered real, so that
    case is not resolved.
    """
    codes = het.altlocs
    if len(codes) <= 1:
        return True, ""
    occupancies = het.occupancy_by_altloc
    ranked = sorted(occupancies.items(), key=lambda kv: kv[1], reverse=True)
    (top_code, top_occ), (next_code, next_occ) = ranked[0], ranked[1]
    if abs(top_occ - next_occ) < OCCUPANCY_TIE_TOLERANCE:
        return False, (
            f"alternate conformations {top_code} and {next_code} have "
            f"indistinguishable occupancies ({top_occ:.2f} and {next_occ:.2f})"
        )
    return True, f"conformation {top_code} at occupancy {top_occ:.2f}"


def classify(
    heterogens: list[Heterogen],
    polymer: list[Atom],
    *,
    keep_water: bool = False,
) -> list[Decision]:
    """Decide the fate of every heterogen, refusing to guess.

    Components are grouped by name, because the decision is chemical rather
    than positional, except for metals where coordination is judged per
    instance.
    """
    by_name: dict[str, list[Heterogen]] = defaultdict(list)
    for het in heterogens:
        by_name[het.resname].append(het)

    # An N-glycan is a chain: only its first sugar is bonded to the protein and
    # the rest hang off each other. Whether the structure is glycosylated at
    # all is therefore a question about the whole structure, not about any one
    # residue, and is answered once here. Without it a glycan's inner sugar
    # looks exactly like a free oligosaccharide -- which is what lysozyme's
    # NAG3 substrate is, and that one must stay a question.
    glycosylated = any(
        het.resname in SUGARS and partner in GLYCOSYLATION_RESIDUES
        for het in heterogens for partner in het.covalent_to
    )

    decisions: list[Decision] = []
    for resname, instances in sorted(by_name.items()):
        decisions.append(
            _classify_one(resname, instances, polymer, keep_water,
                          glycosylated=glycosylated)
        )
    return decisions


def _classify_one(
    resname: str,
    instances: list[Heterogen],
    polymer: list[Atom],
    keep_water: bool,
    *,
    glycosylated: bool = False,
) -> Decision:
    pack = tuple(instances)

    if resname in WATER_NAMES:
        if keep_water:
            return Decision(resname, Action.SIMULATE, "crystallographic water retained on request", pack)
        return Decision(resname, Action.DISCARD, "solvent; the system is re-solvated", pack)

    if resname in ION_NAMES:
        # Two ions of one element sitting almost on top of each other are
        # alternate positions for a single site, not two sites: partially
        # occupied metals on a symmetry axis are ordinary in the PDB. Keeping
        # both puts two atoms within bonding distance, which the force field
        # rejects, and would be wrong even if it did not.
        overlapping = _mutually_overlapping(instances)
        if overlapping:
            resolved, explanation = _resolve_by_occupancy(overlapping)
            if not resolved:
                labels = ", ".join(h.label for h in overlapping)
                return Decision(
                    resname,
                    Action.STOP,
                    f"{len(overlapping)} copies occupy the same site "
                    f"({labels}) at indistinguishable occupancy, so they are "
                    f"alternate positions for one ion and the structure does "
                    f"not say which is real. {explanation} Choose one",
                    pack,
                )
            instances = [h for h in instances if h not in overlapping[1:]]
            pack = tuple(instances)
            logger.info("%s: %s", resname, explanation)

        # A LINK record naming a monatomic ion describes coordination, not a
        # covalent bond: the legacy PDB format uses one record type for both,
        # and only mmCIF distinguishes them (struct_conn.conn_type_id is
        # "metalc" for a metal and "covale" for a covalent bond). Reading
        # every LINK as covalent made every metalloprotein refuse, which is
        # most of the zinc, iron, magnesium, and calcium structures in the
        # PDB. The geometric test below is the one that answers the question.
        linked = [h for h in instances if h.covalent_to]
        coordinated = [
            h for h in instances
            if h.covalent_to
            or _min_distance_to_polymer(h, polymer) <= COORDINATION_CUTOFF_A
        ]
        if coordinated and len(coordinated) == len(instances):
            how = "named in a LINK record" if linked else f"within {COORDINATION_CUTOFF_A} A"
            return Decision(resname, Action.SIMULATE,
                            f"coordinated by the protein ({how})", pack)
        if not coordinated:
            return Decision(resname, Action.DISCARD,
                            "not coordinated by the protein; from the "
                            "crystallization liquor", pack)
        return Decision(
            resname,
            Action.STOP,
            f"{len(coordinated)} of {len(instances)} copies are coordinated by the "
            "protein and the rest are not, so keeping or dropping the component as "
            "a whole would be wrong either way. Select the copies explicitly",
            pack,
        )

    # Checked before the covalent rule: a haem is bonded to its protein, and
    # being told it is "a covalent adduct" is true but useless. What the user
    # needs to know is that it is a haem and needs haem parameters.
    if resname in UNPARAMETERIZABLE:
        return Decision(
            resname,
            Action.STOP,
            f"{UNPARAMETERIZABLE[resname]} needs dedicated parameters that are "
            "not assigned automatically. Parameters exist for most cofactors "
            "(CHARMM36 covers heme and NAD/NADH; CGenFF and CHARMM-GUI's Ligand "
            "Reader cover FAD and FMN; AmberTools' MCPB.py builds metal-centre "
            "models), but they must be supplied deliberately. Provide them, or "
            "exclude this component",
            pack,
        )

    if resname in SUGARS:
        # The three readings of a sugar are not equally uncertain. A LINK
        # record to an asparagine, serine or threonine says this one is a
        # glycosylation site: that is the structure answering the question, not
        # withholding it. What follows is settled rather than guessed -- the
        # glycan is a covalent modification, carbohydrate parameters are
        # required to simulate it, and none of the force fields here supply
        # them -- the AMBER14 bundle carries protein, nucleic acid and lipid
        # templates and no sugar ones -- so the protein is prepared without
        # it. Deglycosylating is the ordinary
        # choice and what a protein-only force field can represent; refusing
        # instead would stop every glycoprotein over a question the deposition
        # already answered.
        attached = sorted({
            partner for h in instances for partner in h.covalent_to
            if partner in GLYCOSYLATION_RESIDUES
        })
        # An inner sugar of a glycan is bonded only to other sugars. It counts
        # as part of the glycan when the structure is glycosylated somewhere;
        # in a structure that is not, the same bonding pattern is a free
        # oligosaccharide and the question stands.
        if not attached and glycosylated and any(
            partner in SUGARS for h in instances for partner in h.covalent_to
        ):
            attached = ["the glycan"]
        if attached:
            return Decision(
                resname,
                Action.DISCARD,
                f"a glycan, attached to {', '.join(attached)}. The protein is "
                "prepared without it: simulating a glycan needs carbohydrate "
                "parameters (GLYCAM, or the CHARMM36 carbohydrate files) that "
                "this pipeline does not assign. Supply them with --setup-ligand "
                "to keep it",
                pack,
            )

        # Unattached, the question stands: a free sugar in a pocket is a
        # substrate, and the same sugar on the surface is a cryoprotectant.
        return Decision(
            resname,
            Action.STOP,
            "a free sugar, attached to nothing, can be a substrate or a "
            "cryoprotectant, and the structure does not say which. Simulating "
            "it as a substrate needs carbohydrate parameters (GLYCAM, or the "
            "CHARMM36 carbohydrate files) rather than a small-molecule force "
            "field. State the intent: keep it with --setup-ligand and suitable "
            "parameters, or exclude it with --setup-heterogens drop",
            pack,
        )

    # A LINK to a monatomic ion is coordination, not covalency -- the same rule
    # the ion branch above applies, seen from the other end. Both partners of a
    # LINK are recorded, so a nucleotide chelating a magnesium was marked linked
    # and refused as a covalent adduct: 5P21 (Ras with GppNHp and Mg) and 1ATP
    # (protein kinase A with ATP and Mn) are the canonical cases, and neither
    # ligand is bonded to anything. Only a partner that is not an ion makes the
    # component part of the macromolecule.
    covalent = [
        h for h in instances
        if any(partner not in ION_NAMES for partner in h.covalent_to)
    ]
    if covalent:
        bonded_to = sorted({
            partner
            for h in covalent
            for partner in h.covalent_to
            if partner and partner not in ION_NAMES
        })
        attachment = f" to {', '.join(bonded_to)}" if bonded_to else ""
        return Decision(
            resname,
            Action.STOP,
            f"covalently bonded{attachment} ({', '.join(h.label for h in covalent)}). "
            "A covalent adduct is part of the macromolecule and cannot be treated "
            "as a free ligand; prepare it with a force field that describes the "
            "linkage, or remove it deliberately",
            pack,
        )

    if resname in UNKNOWN_NAMES:
        return Decision(
            resname,
            Action.STOP,
            "the structure does not say what this molecule is, so it cannot be "
            "parameterized. Supply it explicitly with --setup-ligand, or exclude it",
            pack,
        )

    if resname in CRYSTALLIZATION_ADDITIVES:
        return Decision(resname, Action.DISCARD, "crystallization additive", pack)

    unresolved = []
    for het in instances:
        resolved, explanation = _altloc_decision(het)
        if not resolved:
            unresolved.append(f"{het.label}: {explanation}")
    if unresolved:
        return Decision(
            resname,
            Action.STOP,
            "alternate conformations cannot be ranked by occupancy "
            f"({'; '.join(unresolved)}). Choose one before simulating",
            pack,
        )

    return Decision(resname, Action.SIMULATE, "treated as a ligand of interest", pack)


def summarize(decisions: list[Decision]) -> str:
    """A short, readable account of every decision, for the log and manifest."""
    lines = []
    for d in sorted(decisions, key=lambda x: (x.action.value, x.resname)):
        copies = f" x{d.count}" if d.count > 1 else ""
        lines.append(f"  {d.action.value:8} {d.resname}{copies}: {d.reason}")
    return "\n".join(lines)


def resolve(
    pdb_path: str | Path, *, keep_water: bool = False
) -> list[Decision]:
    """Inventory a structure and decide what to simulate.

    Raises
    ------
    AmbiguousStructureError
        If any component's fate is not determined by the structure. The
        message names every such component and what must be decided.
    """
    polymer, heteroatoms, linked = parse_structure(pdb_path)
    heterogens = group_heterogens(heteroatoms, linked)
    decisions = classify(heterogens, polymer, keep_water=keep_water)

    blocking = [d for d in decisions if d.action is Action.STOP]
    if blocking:
        detail = "\n".join(f"  - {d.resname}: {d.reason}" for d in blocking)
        raise AmbiguousStructureError(
            "This structure does not determine what should be simulated:\n"
            f"{detail}\n"
            "Nothing has been simulated. Resolve each item above, either by "
            "editing the input structure or by stating your intent with "
            "--setup-ligand and --setup-keep-heterogens."
        )
    return decisions
