"""Decide a ligand's protonation from its pKa *in the complex*.

The usual approach protonates a ligand in isolation: a rule table says a
carboxylic acid is deprotonated above pH 4, so at pH 7 it is a carboxylate.
That is a statement about the molecule in water, and it is often wrong about
the molecule in a pocket. A carboxylate buried against an aspartate, or in a
hydrophobic cavity with no compensating charge, can easily remain neutral;
shifts of two or three pKa units on binding are ordinary rather than exotic.

So the question "what charge state does this ligand adopt?" cannot be answered
by looking at the ligand. It is a property of the complex. PROPKA computes
exactly that, for proteins since 3.0 and for protein-ligand complexes since
3.1, so the honest thing is to ask it about the bound state.

Three outcomes, and only one of them proceeds:

* PROPKA returns a pKa comfortably away from the simulation pH. The state is
  determined; adopt it and say so.
* PROPKA returns a pKa close to the pH. The group is genuinely poised, and
  both states are populated. Refuse.
* PROPKA returns nothing usable for the ligand. Refuse, rather than falling
  back to a rule table that answers a different question.

The third case is not a failure of the design. Empirical pKa prediction on an
arbitrary ligand is not always possible, and a tool that quietly substitutes a
worse method when the better one declines has learned nothing.
"""

from __future__ import annotations

import contextlib
import io
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError

logger = get_logger("setup.protonation")

#: How close a pKa may come to the simulation pH before neither state can be
#: chosen.
#:
#: The obvious reading is a population one: a unit is roughly a 9:1 ratio, so a
#: tighter band would accept misrepresenting more of the ensemble. True, but
#: not what sets the value. What sets it is that PROPKA's own error is of the
#: same order -- near 0.8 pKa units on protein residues, and less well
#: characterised on ligands, where its group definitions are coarser. A margin
#: much below one unit would decide a question finer than the calculation
#: resolves: a pKa reported at 7.6 could as easily be 6.8, and "decisively
#: deprotonated" would be a claim the number does not support.
#:
#: So this is not a policy about tolerable error. It marks the region where the
#: calculation cannot tell, and asking is the honest answer there. Someone who
#: knows their ligand better can narrow it with setup.protonation_margin.
POISED_MARGIN = 1.0



class _CapturingHandler(logging.Handler):
    """Collects PROPKA's own log records instead of letting them print."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class ProtonationError(CodedError, RuntimeError):
    """The ligand's protonation at the requested pH could not be settled."""


@dataclass(frozen=True)
class GroupPka:
    """One ionizable group and the pKa it has in this complex."""

    resname: str
    chain: str
    resseq: int
    group_type: str
    pka: float
    model_pka: float

    @property
    def shift(self) -> float:
        """How far the environment moved this group from its solution value."""
        return self.pka - self.model_pka

    def __str__(self) -> str:
        return (
            f"{self.resname} {self.chain}{self.resseq} ({self.group_type}): "
            f"pKa {self.pka:.2f}, shifted {self.shift:+.2f} from {self.model_pka:.2f}"
        )


@dataclass(frozen=True)
class ProtonationState:
    """A settled protonation, with the evidence that settled it."""

    resname: str
    protonated: bool
    groups: tuple[GroupPka, ...]
    reason: str
    #: Per group of the classifying vocabulary, whether it holds its proton at
    #: this pH. A single answer cannot describe a molecule that is both acid
    #: and base, so a zwitterion needs one decision per group, not one overall.
    per_group: tuple[tuple[str, bool], ...] = ()


#: Whether each titratable pattern, when it matches, matched the protonated
#: form. The SMARTS already carry this: a carboxylic acid pattern matches only
#: the neutral acid and a carboxylate only the anion, and the amine patterns
#: exclude ``[N+]``, so they match only the neutral base. The phosphate and
#: sulfonate patterns accept either form and so cannot report which they found;
#: they are absent here and no claim is made about them.
WRITTEN_PROTONATED: dict[str, bool] = {
    "carboxylic acid": True,
    "carboxylate": False,
    "amidine": False,
    "primary or secondary amine": False,
    "tertiary amine": False,
    "imidazole": False,
    "guanidine": False,
    "thiol": True,
    "tetrazole": True,
    "aromatic nitrogen": False,
}


#: PROPKA names ionizable groups by chemical class, not by atom: OCO is a
#: carboxyl, C2N an amidinium (two nitrogens, four hydrogens), CG a guanidinium
#: (three and five), and N31/N32/N33 an amine by its degree of substitution.
#: That makes matching a reported pKa to a group in the molecule a lookup
#: rather than a search through atom names.
PROPKA_GROUP_TO_LABEL: dict[str, tuple[str, ...]] = {
    "OCO": ("carboxylic acid", "carboxylate"),
    "C2N": ("amidine",),
    "CG": ("guanidine",),
    "N30": ("primary or secondary amine",),
    "N31": ("primary or secondary amine",),
    "N32": ("primary or secondary amine",),
    "N33": ("tertiary amine",),
    "OP": ("phosphate or phosphonate",),
    "SH": ("thiol",),
    "NAR": ("imidazole", "tetrazole", "aromatic nitrogen"),
}


#: Where the proton goes for each titratable group, as (SMARTS, index of the
#: atom within the match). The site is not always the atom the classifying
#: pattern is named for: an amidine and a guanidine protonate on the sp2
#: nitrogen, because the cation is delocalised. Building it on the sp3 nitrogen
#: gives a different molecule which also keeps the C=N double bond, and with it
#: an undefined stereocentre the small-molecule toolkits refuse.
#:
#: Groups absent here have no unambiguous site. The phosphate and sulfonate
#: patterns match either form and name no particular oxygen, so a settled state
#: cannot be placed and the ligand is refused instead.
PROTONATION_SITE: dict[str, tuple[str, int]] = {
    "amidine": ("[NX3][CX3]=[NX2]", 2),
    "guanidine": ("[NX3][CX3](=[NX2])[NX3]", 2),
    "primary or secondary amine": ("[NX3;H2,H1;!$(NC=O);!$(N[a])]", 0),
    "tertiary amine": ("[NX3;H0;!$(NC=O);!$(N[a]);!$([N+])]", 0),
    "imidazole": ("c1cnc[nH]1", 2),
    "carboxylic acid": ("[CX3](=O)[OX2H1]", 2),
    "thiol": ("[SX2H1]", 0),
    "tetrazole": ("c1nnn[nH]1", 1),
    "aromatic nitrogen": ("[nX2;H0;!$([n+]);r6]", 0),
}


def _decide_per_group(groups, ph: float) -> tuple[tuple[str, bool], ...]:
    """Decide each classifying group separately, or leave it undecided.

    Groups are matched to the vocabulary by PROPKA's chemical class, so a
    ligand carrying both a carboxyl and an amine gets one answer for each
    rather than one answer for both.

    Two groups of the *same* class cannot be told apart -- PROPKA reports the
    class, not which atom -- but that only matters when they disagree. Two
    carboxylates both well below the pH are both deprotonated whichever is
    which, so the ambiguity is recorded as decided. Where they straddle the pH
    the site matters and no decision is made, which leaves the caller to refuse.
    """
    by_label: dict[str, list[bool]] = defaultdict(list)
    for group in groups:
        for label in PROPKA_GROUP_TO_LABEL.get(group.group_type, ()):
            by_label[label].append(group.pka > ph)

    decided: list[tuple[str, bool]] = []
    for label, answers in by_label.items():
        if len(set(answers)) == 1:
            decided.append((label, answers[0]))
    return tuple(sorted(decided))


def _rdkit():
    try:
        from rdkit import Chem

        return Chem
    except ImportError as exc:  # pragma: no cover - exercised by absence
        raise ProtonationError(
            "RDKit is needed to put a ligand into the protonation state settled "
            "in the complex, and is not installed. Install it (conda install -c "
            "conda-forge rdkit), or supply the ligand with --setup-ligand "
            "already in the state you intend."
        , code="environment.backend.missing", packages=["rdkit"]) from exc


def _add_proton(Chem, mol, index):
    """Attach a proton, giving the new hydrogen a position."""
    editable = Chem.RWMol(mol)
    atom = editable.GetAtomWithIdx(index)
    atom.SetFormalCharge(atom.GetFormalCharge() + 1)
    atom.SetNumExplicitHs(atom.GetNumExplicitHs() + 1)
    atom.SetNoImplicit(False)
    built = editable.GetMol()
    Chem.SanitizeMol(built)
    return Chem.AddHs(built, addCoords=True, onlyOnAtoms=[index])


def _remove_proton(Chem, mol, index):
    """Strip a proton, preferring an explicit hydrogen if one is present."""
    editable = Chem.RWMol(mol)
    atom = editable.GetAtomWithIdx(index)
    atom.SetFormalCharge(atom.GetFormalCharge() - 1)
    hydrogen = next(
        (n.GetIdx() for n in atom.GetNeighbors() if n.GetAtomicNum() == 1), None
    )
    if hydrogen is not None:
        editable.RemoveAtom(hydrogen)
    else:
        atom.SetNumExplicitHs(max(0, atom.GetNumExplicitHs() - 1))
    built = editable.GetMol()
    Chem.SanitizeMol(built)
    return built


def apply_settled_state(sdf_text: str, chemistry, state) -> tuple[str, int]:
    """Put the reference chemistry into the state settled in the complex.

    Returns the rewritten SDF and its net formal charge.

    The pKa is determined in the pocket, but the file handed to the
    small-molecule force field is the reference chemistry, which carries
    whatever protonation the dictionary happens to hold. Parameterizing that
    file simulates the ligand in a charge state the pocket contradicts, and the
    charge is usually what binds it: the amidinium of benzamidine, the
    carboxylate of retinoic acid, the ammonium of a tamoxifen.

    Each group is adjusted on its own answer, so a ligand that is both acid and
    base is built as the zwitterion rather than forced to one side. Where a
    group has no answer -- because two of its class straddle the pH and PROPKA
    reports the class rather than the atom -- this raises instead of guessing.
    """
    labels = tuple(chemistry.titratable_groups)
    if not labels:
        return sdf_text, chemistry.formal_charge

    # Older callers, and the tests that predate per-group answers, pass a state
    # carrying only the overall verdict.
    decisions = dict(state.per_group) if state.per_group else {
        label: state.protonated for label in labels
    }

    # A group the calculation reported nothing for is not an unanswered
    # question. PROPKA saw the molecule and found nothing ionizable of that
    # class within range -- a quinazoline sits near pKa 5, a pteridine near 6 --
    # so the reference form stands, and saying so beats refusing. A group whose
    # copies disagreed is a real question, and is refused below.
    undecided = [label for label in labels if label not in decisions]
    unassessed, contested = [], []
    for label in undecided:
        reported = sum(1 for group in state.groups
                       if label in PROPKA_GROUP_TO_LABEL.get(group.group_type, ()))
        (contested if reported else unassessed).append(label)

    if unassessed and state.groups:
        logger.info(
            "%s: the pKa calculation reported no ionizable group for its %s, "
            "so it stays as the reference chemistry supplies it.",
            chemistry.resname, ", ".join(sorted(unassessed)),
        )
        for label in unassessed:
            decisions[label] = WRITTEN_PROTONATED.get(label)
        unassessed = []

    undecided = contested + unassessed
    if undecided:
        raise ProtonationError(
            f"{chemistry.resname} carries {', '.join(undecided)} whose "
            "protonation was not settled for that group specifically: two "
            "groups of one chemical class fall on opposite sides of the pH, "
            "and the calculation names the class rather than the atom, so "
            "which site holds its proton is not determined.\nSupply the ligand "
            "with --setup-ligand as an SDF or MOL2 already in the state you "
            "intend, with explicit hydrogens and its net charge set."
        , code="setup.chemistry.protonation_undetermined", resname=chemistry.resname, groups=undecided)

    unplaceable = [label for label in labels
                   if decisions[label] != WRITTEN_PROTONATED.get(label)
                   and label not in PROTONATION_SITE]
    if unplaceable:
        raise ProtonationError(
            f"{chemistry.resname} carries {', '.join(unplaceable)}, whose "
            "protonation site is not one this pipeline can place: the pattern "
            "matches either charge state and names no particular atom. Supply "
            "the ligand with --setup-ligand already in the state you intend."
        , code="setup.chemistry.protonation_undetermined", resname=chemistry.resname, groups=unplaceable)

    changes = [label for label in labels
               if decisions[label] != WRITTEN_PROTONATED.get(label)]
    if not changes:
        return sdf_text, chemistry.formal_charge

    Chem = _rdkit()
    mol = Chem.MolFromMolBlock(sdf_text, removeHs=False, sanitize=True)
    if mol is None:
        raise ProtonationError(
            f"The reference chemistry for {chemistry.resname} could not be read "
            "as a molecule, so its protonation cannot be adjusted."
        , code="setup.chemistry.uninterpretable", resname=chemistry.resname)

    expected = chemistry.formal_charge
    for label in changes:
        smarts, offset = PROTONATION_SITE[label]
        pattern = Chem.MolFromSmarts(smarts)
        sites = len(mol.GetSubstructMatches(pattern))

        # A decided answer applies to every site of its class. Methotrexate
        # carries two carboxylates and both sit well below pH 7, so which is
        # which never arises; requiring a single site refused the ligand over
        # an ambiguity that has no consequence. Where the sites disagree the
        # label is left undecided upstream and never reaches here.
        reported = sum(1 for group in state.groups
                       if label in PROPKA_GROUP_TO_LABEL.get(group.group_type, ()))
        allowed = reported if reported else 1
        if sites != allowed:
            raise ProtonationError(
                f"{chemistry.resname} has {sites} site(s) matching its {label} "
                f"but {allowed} were accounted for, so the settled protonation "
                "cannot be placed. Supply the ligand with --setup-ligand "
                "already in the state you intend."
            , code="setup.chemistry.uninterpretable", resname=chemistry.resname)

        protonating = decisions[label]
        change = _add_proton if protonating else _remove_proton
        # Adjusting a site stops it matching its own pattern -- a deprotonated
        # acid is no longer a carboxylic acid, a protonated amine no longer an
        # NX3 -- so rematching after each change walks the sites without
        # tracking indices that shift underneath.
        for _ in range(sites):
            found = mol.GetSubstructMatches(pattern)
            if not found:
                break
            mol = change(Chem, mol, found[0][offset])
            expected += 1 if protonating else -1
        logger.info(
            "%s: %s %d %s site(s) to match the state settled in the complex.",
            chemistry.resname,
            "protonated" if protonating else "deprotonated", sites, label,
        )

    charge = Chem.GetFormalCharge(mol)
    if charge != expected:
        raise ProtonationError(
            f"Adjusting {chemistry.resname} produced a net charge of "
            f"{charge:+d} where {expected:+d} was intended, so the reference "
            "chemistry is not what this pipeline assumed. Supply the ligand "
            "with --setup-ligand already in the state you intend."
        , code="setup.chemistry.charge_undetermined", resname=chemistry.resname)
    logger.info("%s: net charge %+d.", chemistry.resname, charge)
    return Chem.MolToMolBlock(mol), charge


def _propka():
    try:
        import propka.run

        return propka.run
    except ImportError as exc:  # pragma: no cover - exercised by absence
        raise ProtonationError(
            "PROPKA is needed to determine a ligand's protonation in the "
            "complex and is not installed. Install it (conda install -c "
            "conda-forge propka), or supply the ligand with the protonation "
            "you intend already assigned."
        , code="environment.backend.missing", packages=["propka"]) from exc


def ligand_pka(
    complex_pdb: str | Path,
    resname: str,
    *,
    chain: str,
    resseq: int,
) -> list[GroupPka]:
    """Ionizable groups of one ligand copy, with their pKa in this complex.

    Returns an empty list when PROPKA finds no ionizable group for the
    component, which for a ligand like benzene is the correct answer.
    """
    run = _propka()
    resname = resname.upper()

    # PROPKA reports progress on stdout and structural complaints through
    # logging. Both are captured: an unmodelled surface side chain is worth
    # knowing about, but it belongs in the debug log rather than interleaved
    # with the phase output.
    buffer = io.StringIO()
    propka_logger = logging.getLogger("propka")
    captured = _CapturingHandler()
    previous_level = propka_logger.level
    propka_logger.addHandler(captured)
    propka_logger.setLevel(logging.WARNING)
    try:
        with contextlib.redirect_stdout(buffer):
            molecule = run.single(str(complex_pdb), optargs=("--quiet",), write_pka=False)
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot settle"
        raise ProtonationError(
            f"PROPKA could not analyse the complex containing {resname}: {exc}"
        , code="setup.chemistry.uninterpretable", resname=resname) from exc
    finally:
        propka_logger.removeHandler(captured)
        propka_logger.setLevel(previous_level)

    incomplete = [m for m in captured.messages if "Unexpected number" in m]
    if incomplete:
        # Residues the depositors could not model. Surface side chains with
        # poor density are ordinary; near the binding site they would matter.
        logger.debug("PROPKA noted %d incomplete residues:\n  %s",
                     len(incomplete), "\n  ".join(incomplete))
        logger.info(
            "%d residues in this structure are missing side-chain atoms; "
            "pKa values are computed from what was modelled.", len(incomplete),
        )

    if not molecule.conformation_names:
        raise ProtonationError("PROPKA returned no conformation to read.", code="setup.chemistry.uninterpretable")
    conformation = molecule.conformations[molecule.conformation_names[0]]

    found: list[GroupPka] = []
    for group in conformation.groups:
        atom = group.atom
        if atom.res_name.strip().upper() != resname:
            continue
        if str(atom.chain_id).strip() != str(chain).strip():
            continue
        if int(atom.res_num) != int(resseq):
            continue
        # Backbone pseudo-groups carry a zero model pKa and are not titratable
        # in the sense meant here.
        if group.model_pka == 0.0 and group.pka_value == 0.0:
            continue
        found.append(
            GroupPka(
                resname=resname,
                chain=str(atom.chain_id).strip(),
                resseq=int(atom.res_num),
                group_type=str(group.type),
                pka=float(group.pka_value),
                model_pka=float(group.model_pka),
            )
        )
    return found


def _drop_misclassified(resname, groups, known_groups):
    """Discard groups of a class the molecule does not actually carry.

    PROPKA assigns a ligand group by local connectivity and then quotes the
    model pKa of that class. Where its reading and this pipeline's disagree,
    the number comes from the wrong reference compound: folate's N10 sits
    between a methylene and an aromatic ring, and was read as an aliphatic
    secondary amine and given the model pKa of one, 10.0. An aniline is nearer
    4.6, and folate's nitrogen is less basic still. Refusing on that number
    would be refusing over an arithmetic that never applied.

    The classifying patterns exclude a nitrogen bonded to an aromatic ring or
    to a carbonyl for exactly this reason, so where they find no group of the
    class, there is none to have a pKa. The discard is announced, because the
    other reading is that this pipeline's vocabulary is the incomplete one.
    """
    if not known_groups:
        return groups

    known = set(known_groups)
    kept, dropped = [], []
    for group in groups:
        labels = PROPKA_GROUP_TO_LABEL.get(group.group_type, ())
        if labels and not (set(labels) & known):
            dropped.append(group)
        else:
            kept.append(group)

    if dropped:
        logger.info(
            "%s: ignoring %s reported by the pKa calculation -- this molecule "
            "carries no group of that class (it has %s), so the model pKa "
            "quoted is not the right reference.",
            resname,
            ", ".join(sorted({f"{g.group_type} at pKa {g.pka:.2f}" for g in dropped})),
            ", ".join(sorted(known)) or "none",
        )
    return kept


def decide(
    resname: str,
    groups: list[GroupPka],
    ph: float,
    *,
    expected_ionizable: bool,
    margin: float = POISED_MARGIN,
    known_groups: tuple[str, ...] = (),
) -> ProtonationState:
    """Settle a ligand's protonation, or refuse.

    ``expected_ionizable`` comes from inspecting the ligand's chemistry: if it
    carries an ionizable group but PROPKA reported none, the two disagree and
    the disagreement is itself a reason not to proceed.

    ``known_groups`` is what that same inspection found. The disagreement runs
    both ways: PROPKA can report a class the molecule does not have, and the
    pKa it then quotes comes from the wrong reference compound.
    """
    groups = _drop_misclassified(resname, groups, known_groups)
    if not groups:
        if expected_ionizable:
            raise ProtonationError(
                f"{resname} carries an ionizable group, but PROPKA returned no "
                "pKa for it in this complex, so its charge state here is "
                "undetermined. Empirical prediction does not succeed on every "
                "ligand.\nSupply the ligand with the protonation you intend, as "
                "an SDF or MOL2 file, and set its net charge."
            , code="setup.chemistry.protonation_undetermined", resname=resname)
        return ProtonationState(
            resname=resname,
            protonated=False,
            groups=(),
            reason="no ionizable group; protonation is unambiguous",
        )

    poised = [g for g in groups if abs(g.pka - ph) < margin]
    if poised:
        described = []
        for group in poised:
            # Henderson-Hasselbalch. A threshold in pKa units means little on
            # its own; this is the share of the ensemble a single choice would
            # leave out, which is the number a reader can weigh.
            minor = 1.0 / (1.0 + 10.0 ** abs(group.pka - ph))
            described.append(f"  {group} -- {minor:.0%} in the minor state")
        detail = "\n".join(described)
        raise ProtonationError(
            f"{resname} has ionizable groups whose pKa sits within {margin:g} "
            f"unit of pH {ph:g}, which is also about the uncertainty of the "
            f"calculation itself, so which state dominates is not determined:"
            f"\n{detail}\n"
            "Both states are real here, so neither can be chosen for you. "
            "State which you intend:\n"
            "  --setup-ligand <file>.sdf   supply the ligand already in that "
            "state, with explicit hydrogens and its net charge set\n"
            "  --setup-ph <value>          compute at a pH where the group is "
            "not poised\n"
            "  --setup-heterogens drop     leave the component out entirely\n"
            "A shift of more than a pH unit from the model value is the pocket "
            "speaking, not the solvent, and is worth reading before overriding."
        , code="setup.chemistry.protonation_undetermined", resname=resname, margin=margin)

    # Every group is decisively on one side of the pH.
    protonated_groups = [g for g in groups if g.pka > ph]
    detail = "; ".join(str(g) for g in groups)
    state = ProtonationState(
        resname=resname,
        protonated=bool(protonated_groups),
        groups=tuple(groups),
        reason=(
            f"pKa determined in the complex at pH {ph:g}: {detail}"
        ),
        per_group=_decide_per_group(groups, ph),
    )
    logger.info(
        "%s protonation settled from the complex: %d group(s), %s",
        resname, len(groups),
        "protonated" if state.protonated else "deprotonated",
    )
    return state


def settle(
    complex_pdb: str | Path,
    resname: str,
    *,
    chain: str,
    resseq: int,
    ph: float,
    expected_ionizable: bool,
    margin: float = POISED_MARGIN,
    known_groups: tuple[str, ...] = (),
) -> ProtonationState:
    """Determine a ligand's protonation in the complex, or raise.

    A ligand with no ionizable group has one protonation state whatever its
    surroundings, so the calculation is skipped: it could not change the
    answer, and running it only invites complaints about a structure whose
    imperfections are irrelevant here.
    """
    if not expected_ionizable:
        return ProtonationState(
            resname=resname,
            protonated=False,
            groups=(),
            reason="no ionizable group; protonation is unambiguous",
        )
    groups = ligand_pka(complex_pdb, resname, chain=chain, resseq=resseq)
    return decide(resname, groups, ph, expected_ionizable=expected_ionizable,
                  known_groups=known_groups,
                  margin=margin)
