"""How the ligand's chemistry was known, and what that supports.

Deciding whether a nitrogen donates a hydrogen bond, whether a ring is
aromatic, whether a group carries a charge -- none of that is in a trajectory.
It is chemistry, and a trajectory carries coordinates.

Other tools perceive it from the coordinates every time, because they accept
arbitrary PDB files and have nothing else to go on. This software often has
something else: setup resolves the ligand's chemistry from the Chemical
Component Dictionary and determines its protonation against the pocket, then
writes it to ``setup/ligands/<resname>.sdf``. Where that file exists the
chemistry is not a guess.

A file's atoms are matched to the trajectory's by their bonds, not by their
position in the file. An SDF that lists the same atoms in another order is
the usual case rather than the exception (RDKit and the dictionary put
hydrogens where they please), and taken by position an acetate written
C, H, H, H, C, O, O put its carboxylate on two methyl hydrogens.

Where it does not -- a trajectory from GROMACS, from AMBER, from somebody
else's script, which is most trajectories -- the routes are tried in order and
**the one that worked is recorded**. A reader deserves to know whether an
interaction rests on chemistry that was resolved or chemistry that was
inferred from where the atoms happened to be, because a wrong bond order moves
a hydrogen and invents or destroys a hydrogen bond.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from fastmdxplora.refusals import StudyError

__all__ = ["ResolvedChemistry", "resolve_ligand_chemistry",
           "deposit_perceived_chemistry", "SOURCES"]


#: The routes, best first. Ordered by how much of the answer was decided by
#: somebody who knew, rather than inferred from coordinates.
SOURCES = ("supplied", "run", "perceived")

#: What each route means for a reader of the results.
_CONFIDENCE = {
    "supplied": "stated by you",
    "run": "resolved during setup, at the pH simulated",
    "perceived": "inferred from the coordinates -- bond orders are a guess",
}


@dataclass(frozen=True)
class ResolvedChemistry:
    """A ligand's chemistry, and where it came from."""

    mol: Any                  # an RDKit Mol
    source: str
    detail: str
    resname: str
    n_atoms: int
    #: More than one net charge gave a molecule that sanitises, so the one
    #: used was chosen rather than determined.
    charge_was_ambiguous: bool = False

    @property
    def is_perceived(self) -> bool:
        """True where the bond orders were inferred rather than stated."""
        return self.source == "perceived"

    def as_record(self) -> dict[str, Any]:
        """For the analysis manifest, so the results carry their own caveat."""
        return {
            "resname": self.resname,
            "source": self.source,
            "confidence": _CONFIDENCE[self.source],
            "detail": self.detail,
            "n_atoms": self.n_atoms,
            "bond_orders_perceived": self.is_perceived,
            "charge_was_ambiguous": self.charge_was_ambiguous,
            # The charge the interactions were judged with. It decides every
            # salt bridge, and it differs by route: read from a file, or
            # chosen among the ones that balance.
            "formal_charge": _formal_charge(self.mol),
        }


def _formal_charge(mol: Any) -> int | None:
    try:
        from rdkit import Chem

        return int(Chem.GetFormalCharge(mol))
    except Exception:  # noqa: BLE001 - a record is worth writing without it
        return None


def _graph(atomic_numbers: Any, bonds: Any) -> Any:
    """A molecule made of elements and single bonds only, for matching."""
    from rdkit import Chem

    built = Chem.RWMol()
    for number in atomic_numbers:
        atom = Chem.Atom(int(number))
        atom.SetNoImplicit(True)
        built.AddAtom(atom)
    for first, second in bonds:
        built.AddBond(int(first), int(second), Chem.BondType.SINGLE)
    graph = built.GetMol()
    graph.UpdatePropertyCache(strict=False)
    return graph


def _topology_order(mol: Any, topology: Any, atom_indices: Any) -> list[int] | None:
    """For each ligand atom in the trajectory, the file atom that is it.

    Matched by the topology's bonds where it carries the ligand's bonds:
    the file's graph of elements and bonds must be the topology's, and the
    match says which file atom each trajectory atom is. Where the topology
    has no bonds inside the ligand there is nothing to match, and the
    elements must then agree one by one in the order given. None where
    neither holds, which is a different molecule or one that cannot be
    placed.
    """
    order = [int(i) for i in atom_indices]
    if mol.GetNumAtoms() != len(order):
        return None
    where = {index: position for position, index in enumerate(order)}
    numbers = []
    for index in order:
        element = topology.atom(index).element
        numbers.append(int(element.atomic_number) if element is not None else 0)
    bonds = {
        tuple(sorted((where[b[0].index], where[b[1].index])))
        for b in topology.bonds
        if b[0].index in where and b[1].index in where
    }
    in_file = [a.GetAtomicNum() for a in mol.GetAtoms()]

    if not bonds:
        return list(range(len(order))) if in_file == numbers else None

    file_bonds = {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx())))
                  for b in mol.GetBonds()}
    if len(file_bonds) != len(bonds):
        return None
    # The file's own order where it is already the topology's; otherwise
    # the first match, which for a symmetric molecule is one of several
    # equivalent ones.
    if in_file == numbers and file_bonds == bonds:
        return list(range(len(order)))
    match = _graph(numbers, bonds).GetSubstructMatch(_graph(in_file, file_bonds))
    if len(match) != len(order):
        return None
    # match[file atom] = topology position; invert it.
    inverse = [0] * len(order)
    for file_atom, position in enumerate(match):
        inverse[position] = file_atom
    return inverse


def _from_sdf(
    path: Path, resname: str, topology: Any, atom_indices: Any
) -> tuple[ResolvedChemistry, bool] | None:
    """A file's molecule with its atoms in the trajectory's order, and
    whether they had to be reordered; None where it is not this ligand."""
    from rdkit import Chem

    try:
        supplier = Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)
        mol = next((m for m in supplier if m is not None), None)
    except Exception:  # noqa: BLE001 - a bad file is a route that did not work
        return None
    if mol is None:
        return None
    # A definition of a different molecule is worse than no definition: it
    # would put donors and rings on atoms that are not there. So is the
    # right molecule mapped onto the wrong atoms, which an atom count alone
    # cannot tell apart from the right mapping.
    order = _topology_order(mol, topology, atom_indices)
    if order is None:
        return None
    reordered = order != list(range(len(order)))
    if reordered:
        mol = Chem.RenumberAtoms(mol, order)
    return ResolvedChemistry(
        mol=mol, source="", detail="", resname=resname, n_atoms=mol.GetNumAtoms()
    ), reordered


#: Charges to try when nobody has said what the ligand's is. Ordered by how
#: common they are among drug-like molecules, neutral first.
_CHARGES_TO_TRY = (0, -1, 1, -2, 2)


def _rdkit_is_absent() -> bool:
    """Whether the refusal should say so, rather than blaming perception."""
    import importlib.util

    return importlib.util.find_spec("rdkit") is None


def _perceive(
    traj: Any, atom_indices: Any, resname: str, net_charge: int | None
) -> tuple[Any, int, list[int]] | None:
    """Bond orders from the coordinates, as other profilers do every time.

    Not a poor route -- it is what PLIP and ProLIF do for every structure --
    but it is a guess, and a guess about bond order decides which nitrogen
    donates and which ring is aromatic.

    The net charge has to be right or the orders come out wrong: assuming
    neutral, acetate perceives as having no double bond at all, because the
    carboxylate's charge has to go somewhere and without it the valences do not
    balance. A carboxylate is exactly what forms a salt bridge, so this is the
    case where guessing wrong costs most. Where nobody has said, a few charges
    are tried and the one that worked is reported rather than assumed.
    """
    import tempfile

    try:
        from rdkit import Chem
        from rdkit.Chem import rdDetermineBonds
    except ImportError:
        # Perception is the last thing tried, and it is the only one that
        # needs RDKit. A bare import here escaped `resolve_ligand_chemistry`
        # as a ModuleNotFoundError, so the carefully written refusal below --
        # which names everything that was tried and says to supply an SDF --
        # could never fire on the one path where it was most needed. The
        # user got a traceback instead of a next step, which is the single
        # place "it refuses rather than guesses" was not true.
        #
        # None here, and the caller adds "perception from the coordinates"
        # to `tried` exactly as it does for any other failed route.
        return None

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "ligand.pdb"
        traj.atom_slice(atom_indices)[0].save_pdb(str(path))
        source = Chem.MolFromPDBFile(str(path), removeHs=False, sanitize=False)
    if source is None:
        return None

    charges = (net_charge,) if net_charge is not None else _CHARGES_TO_TRY

    # Every charge that balances, not the first. More than one usually does,
    # and the first is not reliably the right one: guanidinium is +1, and both
    # -1 and +1 give a molecule that sanitises. Taking the first would make it
    # an anion, which is the opposite of the charge that forms its salt
    # bridges. Phenol is neutral, and 0 and -2 both balance -- there the first
    # happens to be right, which is luck rather than a method.
    balanced: list[tuple[Any, int]] = []
    for charge in charges:
        mol = Chem.Mol(source)
        try:
            # Connectivity first, then orders: separating them means a failure
            # to assign orders still leaves a usable graph rather than nothing.
            rdDetermineBonds.DetermineConnectivity(mol)
            rdDetermineBonds.DetermineBondOrders(mol, charge=charge)
            Chem.SanitizeMol(mol)
        except Exception:  # noqa: BLE001 - this charge does not balance
            continue
        balanced.append((mol, charge))

    if balanced:
        # Neutral where it is among them, because most ligands are; but the
        # caller is told how many balanced, so an ambiguous answer is visible
        # rather than presented as a determination.
        chosen = next((pair for pair in balanced if pair[1] == 0), balanced[0])
        return chosen[0], chosen[1], [q for _m, q in balanced]

    # Nothing balanced. A graph without orders still supports the geometric
    # rules that do not need them, and saying so beats returning nothing.
    mol = Chem.Mol(source)
    try:
        rdDetermineBonds.DetermineConnectivity(mol)
        Chem.SanitizeMol(mol, Chem.SanitizeFlags.SANITIZE_ALL
                         ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE)
    except Exception:  # noqa: BLE001
        return None
    return mol, 0, []


def deposit_perceived_chemistry(
    chemistry: "ResolvedChemistry", run_dir: "str | Path | None"
) -> "Path | None":
    """Write a perceived molecule where the next reader will find it.

    Chemistry resolved from a file is already on disk; chemistry inferred
    from coordinates is not, and it is the weaker of the two. So a run
    whose ligand was perceived leaves nothing behind, every later analysis
    perceives it again, and the one route the software itself labels
    "bond orders are a guess" is the one route with no record of what was
    guessed.

    Written into the same `setup/ligands/` directory that
    `resolve_ligand_chemistry` already searches, so the next analysis
    finds it as a resolved file rather than repeating the inference. The
    file is not a claim that the chemistry is certain -- the record beside
    it still says `perceived` -- it is a statement of what was used.

    Returns the path written, or None where there was nothing to write or
    nowhere to put it.
    """
    if run_dir is None or chemistry.mol is None:
        return None
    if not chemistry.is_perceived:
        return None

    from pathlib import Path as _Path

    ligands = _Path(run_dir) / "setup" / "ligands"
    target = ligands / f"{chemistry.resname}.sdf"
    if target.exists():
        return target

    try:
        from rdkit import Chem

        ligands.mkdir(parents=True, exist_ok=True)
        writer = Chem.SDWriter(str(target))
        try:
            writer.write(chemistry.mol)
        finally:
            writer.close()
    except Exception:  # noqa: BLE001 - a deposit must not fail a run
        # The analysis has its chemistry either way; what is lost is only
        # the record, and losing a run to save a record is the wrong
        # trade.
        return None
    return target


def resolve_ligand_chemistry(
    traj: Any,
    resname: str,
    atom_indices: Any,
    *,
    supplied: str | Path | None = None,
    run_dir: str | Path | None = None,
    net_charge: int | None = None,
    allow_fetch: bool = True,
) -> ResolvedChemistry:
    """The ligand's chemistry, by the best route that works.

    Parameters
    ----------
    traj : mdtraj.Trajectory
        The trajectory the ligand is in. Only the first frame is used, and
        only where the chemistry has to be perceived.
    resname : str
        The ligand's residue name, which is also its Chemical Component
        Dictionary code where it came from a deposited structure.
    atom_indices : sequence of int
        The ligand's atoms.
    supplied : path, optional
        An SDF you are giving. Nothing should stop somebody stating the
        chemistry of their own ligand.
    run_dir : path, optional
        A run this software produced, whose setup phase already resolved this.
    net_charge : int, optional
        The ligand's net charge, where you know it. Only used when the
        chemistry has to be perceived, and it matters there: assuming neutral
        gives a carboxylate no double bond, which is the group that forms salt
        bridges.
    allow_fetch : bool, default True
        Accepted so existing calls keep working, and has no effect: nothing
        is fetched here. Looking a residue name up in the Chemical Component
        Dictionary needs the deposited entry, chain and residue number the
        setup phase has and a trajectory does not, and the call this made
        without them failed every time.

    Raises
    ------
    ValueError
        Where no route works, saying which would.
    """
    tried: list[str] = []
    # Read only where there is a file to match against it.
    topology = traj.topology if (supplied or run_dir) else None

    def described(where: str, reordered: bool) -> str:
        return (f"{where} (atoms matched to the topology by their bonds)"
                if reordered else where)

    if supplied:
        found = _from_sdf(Path(supplied), resname, topology, atom_indices)
        tried.append(f"the file you gave ({supplied})")
        if found:
            chemistry, reordered = found
            return ResolvedChemistry(chemistry.mol, "supplied",
                                     described(str(supplied), reordered),
                                     resname, chemistry.n_atoms)

    if run_dir:
        for candidate in sorted(Path(run_dir).glob(f"**/ligands/{resname}*.sdf")):
            found = _from_sdf(candidate, resname, topology, atom_indices)
            tried.append(f"this run's setup ({candidate.name})")
            if found:
                chemistry, reordered = found
                return ResolvedChemistry(chemistry.mol, "run",
                                         described(str(candidate), reordered),
                                         resname, chemistry.n_atoms)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        found = _perceive(traj, atom_indices, resname, net_charge)
    if _rdkit_is_absent():
        tried.append(
            "perception from the coordinates (RDKit is not installed; "
            "`conda install -c conda-forge rdkit`)")
    else:
        tried.append("perception from the coordinates")
    if found:
        mol, charge, balanced = found
        if net_charge is not None:
            how = f"at the net charge you stated, {charge:+d}"
        elif len(balanced) > 1:
            others = ", ".join(f"{q:+d}" for q in balanced if q != charge)
            how = (f"at net charge {charge:+d}, but {others} would also "
                   f"balance -- state the charge to decide it")
        else:
            how = f"at net charge {charge:+d}, the only one that balances"
        return ResolvedChemistry(
            mol, "perceived",
            f"bond orders inferred from the coordinates {how}",
            resname, mol.GetNumAtoms(), charge_was_ambiguous=len(balanced) > 1,
        )

    raise StudyError(
        f"The chemistry of {resname!r} could not be established. Tried: "
        + "; ".join(tried)
        + ". Supply an SDF for it whose atoms and bonds are this ligand's."
    , code="setup.chemistry.uninterpretable")
