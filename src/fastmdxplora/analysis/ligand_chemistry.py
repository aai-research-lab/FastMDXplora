"""How the ligand's chemistry was known, and what that supports.

Deciding whether a nitrogen donates a hydrogen bond, whether a ring is
aromatic, whether a group carries a charge -- none of that is in a trajectory.
It is chemistry, and a trajectory carries coordinates.

Other tools perceive it from the coordinates every time, because they accept
arbitrary PDB files and have nothing else to go on. This software often has
something else: setup resolves the ligand's chemistry from the Chemical
Component Dictionary and settles its protonation against the pocket, then
writes it to ``setup/ligands/<resname>.sdf``. Where that file exists the
chemistry is not a guess.

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
SOURCES = ("supplied", "run", "ccd", "perceived")

#: What each route means for a reader of the results.
_CONFIDENCE = {
    "supplied": "stated by you",
    "run": "resolved during setup, at the pH simulated",
    "ccd": "the Chemical Component Dictionary definition for this residue name",
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


def _from_sdf(path: Path, resname: str, expected_atoms: int) -> ResolvedChemistry | None:
    from rdkit import Chem

    try:
        supplier = Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)
        mol = next((m for m in supplier if m is not None), None)
    except Exception:  # noqa: BLE001 - a bad file is a route that did not work
        return None
    if mol is None:
        return None
    # A definition of a different molecule is worse than no definition: it
    # would put donors and rings on atoms that are not there.
    if expected_atoms and mol.GetNumAtoms() != expected_atoms:
        return None
    return ResolvedChemistry(
        mol=mol, source="", detail="", resname=resname, n_atoms=mol.GetNumAtoms()
    )


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
        Whether the Chemical Component Dictionary may be consulted. Turned off
        for offline work, where a network timeout is a worse answer than
        falling through to perception.

    Raises
    ------
    ValueError
        Where no route works, saying which would.
    """
    expected = len(atom_indices)
    tried: list[str] = []

    if supplied:
        found = _from_sdf(Path(supplied), resname, expected)
        tried.append(f"the file you gave ({supplied})")
        if found:
            return ResolvedChemistry(found.mol, "supplied", str(supplied),
                                     resname, found.n_atoms)

    if run_dir:
        for candidate in sorted(Path(run_dir).glob(f"**/ligands/{resname}*.sdf")):
            found = _from_sdf(candidate, resname, expected)
            tried.append(f"this run's setup ({candidate.name})")
            if found:
                return ResolvedChemistry(found.mol, "run", str(candidate),
                                         resname, found.n_atoms)

    if allow_fetch:
        try:
            from fastmdxplora.setup.ccd import fetch_chemistry

            chemistry = fetch_chemistry(resname)
            found = _from_sdf(Path(chemistry.path), resname, expected)
            tried.append(f"the Chemical Component Dictionary entry for {resname}")
            if found:
                return ResolvedChemistry(found.mol, "ccd", str(chemistry.path),
                                         resname, found.n_atoms)
        except Exception:  # noqa: BLE001 - offline, unknown code, changed API
            tried.append(
                f"the Chemical Component Dictionary (no usable entry for {resname})"
            )

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
                   f"balance -- state the charge to settle it")
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
        + ". Supply an SDF for it, or use a residue name the Chemical "
        "Component Dictionary knows."
    , code="setup.chemistry.uninterpretable")
