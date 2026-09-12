"""Ligand / cofactor loading for protein-ligand systems.

This module validates and loads small-molecule ligands for parameterization
with the OpenFF small-molecule force fields (via ``openmmforcefields``'
``SystemGenerator``). It deliberately keeps the *loading/validation* concern
separate from the *system build* concern (which lives in
:mod:`fastmdxplora.setup.prepare`): here we turn a ligand file into a
validated OpenFF ``Molecule`` with a known net charge; the prepare step feeds
that molecule to the ``SystemGenerator``.

Supported input formats are SDF and MOL2 — the formats OpenFF reads cleanly
with full bond/charge information. (PDB ``HETATM`` extraction is intentionally
not supported yet; a bare PDB ligand lacks the bond orders OpenFF needs.)

The OpenFF toolkit is an optional dependency. :func:`load_ligand` raises a
clear, actionable :class:`LigandError` if it is not installed, rather than an
opaque ImportError, so the setup phase can degrade gracefully.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError
from fastmdxplora.refusals import StudyError

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = get_logger("setup.ligand")

#: Ligand file formats OpenFF reads with full chemical information.
SUPPORTED_LIGAND_FORMATS = ("sdf", "mol2")


class LigandError(CodedError, Exception):
    """Raised for ligand input problems (format, missing file, charge, deps)."""


def detect_ligand_format(ligand_file: str | Path) -> str:
    """Return the ligand format (``sdf`` or ``mol2``) from the file suffix.

    Raises
    ------
    LigandError
        If the suffix is not a supported ligand format.
    """
    ext = Path(ligand_file).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_LIGAND_FORMATS:
        supported = ", ".join(SUPPORTED_LIGAND_FORMATS)
        raise LigandError(
            f"Unsupported ligand format {ext!r} for {ligand_file!r}. "
            f"Use one of: {supported}. (A ligand embedded in a PDB lacks the "
            f"bond/charge information OpenFF needs; export it to SDF/MOL2.)"
        , code="setup.ligand.format_unsupported", given=ext, permitted=list(SUPPORTED_LIGAND_FORMATS))
    return ext


def _import_openff() -> Any:
    """Import the OpenFF ``Molecule`` class, or raise a clear LigandError.

    Mirrors :func:`fastmdxplora.setup.prepare._import_openmm` so a missing
    optional dependency degrades with an actionable install message instead
    of an opaque ImportError.
    """
    try:
        from openff.toolkit import Molecule
    except ImportError as exc:  # pragma: no cover - exercised on hosts w/o openff
        raise LigandError(
            "Ligand parameterization needs the OpenFF toolkit, which is not "
            "installed and is not on PyPI: it is distributed through "
            "conda-forge only, so pip cannot fetch it whatever extra is "
            "named.\n\n"
            "    conda install -c conda-forge openff-toolkit "
            "openmmforcefields\n\n"
            "Or install FastMDXplora itself from conda-forge, which brings "
            "the whole ligand path with it."
        , code="environment.backend.missing", packages=["openff-toolkit"]) from exc
    return Molecule


POSE_POLICIES = ("auto", "structure", "file")


def pose_by_policy(molecule: Any, structure: str | Path, resname: str,
                   *, policy: str = "auto",
                   copy: int = 0) -> tuple[Any, str | None]:
    """Apply the pose the config asked for, or the one the files imply.

    ``auto`` is :func:`pose_from_structure` deciding by looking, and is the
    default because the files usually do say. The other two exist for the
    cases where what the author wants is not what the files imply, and both
    were discovered by a run that wanted them:

    ``file`` keeps the supplied file's coordinates even where the structure
    holds the residue. That is an unbound start on a complex -- the T4
    lysozyme control began life as a bug that did exactly this, and turned
    out to be the known-negative the contact analyses needed. A control
    should be a choice rather than an accident.

    ``structure`` requires the structure's pose, so every quiet fall-back to
    the file's arbitrary geometry -- a residue name that matches nothing, an
    atom count that does not -- becomes a refusal. On a bound run those
    fallbacks are the seventeen-Angstroms failure returning silently.
    """
    chosen = str(policy).strip().lower()
    if chosen not in POSE_POLICIES:
        raise LigandError(
            f"ligand_pose: unknown policy {policy!r}; expected one of "
            f"{', '.join(POSE_POLICIES)}. `auto` takes the pose from the "
            "structure where it holds the residue and from the file where "
            "it does not; `structure` and `file` insist on one side.", code="setup.ligand.pose_unavailable", policy=policy)
    if chosen == "file":
        return molecule, (
            f"the supplied file's pose stands for {resname} by request "
            "(ligand_pose: file), whether or not the structure holds it")
    return pose_from_structure(
        molecule, structure, resname, copy=copy,
        required=(chosen == "structure"))


def pose_from_structure(molecule: Any, structure: str | Path, resname: str,
                        *, copy: int = 0,
                        required: bool = False) -> tuple[Any, str | None]:
    """Decide which file says where the ligand is.

    There are two ways a person arrives with a protein and a ligand, and they
    want opposite things from the same pair of files.

    **The ligand is in the structure.** A complex from the PDB has the ligand
    at its crystallographic coordinates and no chemistry: a PDB cannot express
    bond orders, formal charges or aromaticity, which is exactly what a force
    field needs. So the author supplies those separately -- an SDF or MOL2,
    often the ideal component from the Chemical Component Dictionary. That
    file's *coordinates* are idealised and mean nothing here. **The structure
    wins**, and this replaces them.

    Getting that backwards is not a small error. On T4 lysozyme with benzene,
    the ideal component sat seventeen Angstroms from the cavity it was
    supposed to occupy. Setup succeeded, the clash check passed -- seventeen
    Angstroms is not a clash -- and the run was of a benzene floating in
    solvent rather than a benzene in a binding site. Everything looked right.

    **The ligand is not in the structure.** An apo protein, and a pose from
    docking or built by hand. Here the supplied file is the only thing that
    knows where the ligand goes, and its coordinates are the author's answer.
    **The file wins**, and this leaves it alone.

    The two are told apart by looking: if the structure holds a residue of
    this name with a matching count of heavy atoms, it is the first case. If
    it does not, it is the second. Nothing has to be declared, because the
    files already say which situation it is -- and in the second case the
    author is responsible for the pose being a bound one, which no amount of
    checking here can establish.

    Returns the molecule and a sentence about what happened, or ``None``
    where the structure has no such residue and the SDF's own coordinates
    stand -- which is right when the ligand is being placed deliberately
    rather than read from a complex.

    With ``required=True`` every one of those fallbacks refuses instead of
    standing, with the same sentence it would have logged. On the T4 system
    the silent version of each was a run of benzene floating in solvent that
    looked in every respect like a run of benzene in a binding site.
    """

    def _stand(reason: str) -> tuple[Any, str]:
        if required:
            raise LigandError(
                f"ligand_pose: structure was asked for, but {reason}.", code="setup.ligand.pose_unavailable")
        return molecule, reason

    import numpy as _np

    try:
        import mdtraj as _md  # noqa: PLC0415

        frame = _md.load(str(structure))
    except Exception as exc:  # noqa: BLE001 - a structure that will not read
        return _stand(
            f"could not read {Path(structure).name} for the ligand's pose "
            f"({type(exc).__name__}), so the file's own coordinates stand")

    # By residue, not by name across the structure. A component can appear
    # more than once -- two copies of a substrate, a cofactor in each half of
    # a dimer -- and collecting every atom that shares the name gathers all
    # of them into one molecule's worth of coordinates. `copy` says which.
    wanted = resname.strip().upper()
    matches = [residue for residue in frame.topology.residues
               if residue.name.strip().upper() == wanted]
    if not matches:
        if required:
            raise LigandError(
                f"ligand_pose: structure was asked for, but "
                f"{Path(structure).name} holds no residue named {wanted}, so "
                "there is no pose in the structure to take. Where the pose is "
                "meant to come from the supplied file -- an apo protein and a "
                "docked or deliberately unbound ligand -- say so with "
                "ligand_pose: file.", code="setup.ligand.pose_unavailable")
        return molecule, None
    if copy >= len(matches):
        return _stand(
            f"{Path(structure).name} holds {len(matches)} copies of {wanted} "
            f"and this is copy {copy + 1}, so there is none left to place it "
            "at and the file's own coordinates stand")

    residue = matches[copy]
    indices = [atom.index for atom in residue.atoms]

    # Heavy atoms only: a crystal structure has no hydrogens, and the SDF
    # has them. Matching on count is what tells us the two are the same
    # molecule rather than something that merely shares a residue name.
    heavy = [i for i, atom in enumerate(molecule.atoms)
             if atom.atomic_number > 1]
    if len(indices) != len(heavy):
        return _stand(
            f"{wanted} in {Path(structure).name} has {len(indices)} atoms and "
            f"the supplied file has {len(heavy)} heavy atoms, so they are not "
            "the same molecule and the file's own coordinates stand")

    positions = _np.array(molecule.conformers[0].m_as("nanometer")
                          if molecule.conformers else None, dtype=float)
    if positions is None or positions.size == 0:
        return _stand("the supplied file carries no coordinates to replace")

    # The crystallographic positions, in the order the SDF's heavy atoms
    # come in. Both orders come from the same component definition, so they
    # correspond; a mismatch shows up as a geometry the clash check refuses.
    moved = positions.copy()
    moved[heavy] = frame.xyz[0][indices]

    # Hydrogens keep their offset from the heavy atom they were built on, so
    # the molecule stays intact rather than having its hydrogens left behind.
    shift = moved[heavy[0]] - positions[heavy[0]]
    for index, atom in enumerate(molecule.atoms):
        if atom.atomic_number <= 1:
            moved[index] = positions[index] + shift

    # Wrapped the way the molecule's own conformer is wrapped, rather than
    # by importing the units package: this function is then testable
    # wherever the arithmetic is, and the toolkit is conda-forge-only.
    existing = molecule.conformers[0]
    try:
        molecule._conformers = [type(existing)(moved, "nanometer")]
    except Exception:  # noqa: BLE001 - a wrapper that takes only the values
        molecule._conformers = [type(existing)(moved)]
    which = (f" (copy {copy + 1} of {len(matches)})" if len(matches) > 1
             else "")
    return molecule, (
        f"placed {wanted}{which} at its coordinates in "
        f"{Path(structure).name} rather than the supplied file's, which "
        "carries the chemistry and an arbitrary pose")


def load_ligand(
    ligand_file: str | Path,
    *,
    name: str = "LIG",
    net_charge: int | None = None,
) -> Any:
    """Load and validate a ligand into an OpenFF ``Molecule``.

    Parameters
    ----------
    ligand_file : path
        Path to an SDF or MOL2 file.
    name : str, default "LIG"
        Residue/molecule name assigned to the ligand.
    net_charge : int, optional
        Formal net charge. If ``None`` (default), the charge is inferred from
        the molecule's formal charges (typical for a correctly prepared SDF).
        Supply this explicitly when the file is ambiguous or you need to
        override the inferred value.

    Returns
    -------
    openff.toolkit.Molecule
        The loaded molecule, with ``.name`` set.

    Raises
    ------
    LigandError
        On a missing file, unsupported format, missing OpenFF toolkit, or an
        unreadable/ambiguous ligand.
    """
    path = Path(ligand_file).expanduser().resolve()
    if not path.exists():
        # A residue name here is a common mistake, and the bare "file not
        # found" sends somebody looking for a file they never meant to make.
        # `ligand:` takes chemistry from outside; naming a component that is
        # already in the structure is `heterogens: auto`.
        given = str(ligand_file).strip()
        if given.isalnum() and 1 <= len(given) <= 3 and not Path(given).suffix:
            raise LigandError(
                f"No file at {path}. {given.upper()!r} looks like a residue "
                "name rather than a path.\n\n"
                "`ligand` takes an SDF or MOL2 file, for chemistry supplied "
                "from outside the structure. To use a component the "
                "structure already holds, set `heterogens: auto` instead, "
                "which identifies it and looks its chemistry up.\n\n"
                "Where that lookup is not available -- an offline machine, "
                "or a structure given as a local file -- fetch the entry and "
                "pass the path:\n\n"
                f"    curl -O https://files.rcsb.org/ligands/download/"
                f"{given.upper()}_ideal.sdf"
            , code="setup.ligand.pose_unavailable")
        raise LigandError(f"Ligand file not found: {path}", code="setup.ligand.unreadable", path=str(path))
    detect_ligand_format(path)

    Molecule = _import_openff()
    try:
        molecule = Molecule.from_file(str(path))
    except Exception as exc:  # noqa: BLE001 - normalize to LigandError
        raise LigandError(
            f"Could not read ligand {path.name!r} as a valid molecule: {exc}. "
            f"Ensure the SDF/MOL2 has explicit hydrogens and bond orders."
        , code="setup.ligand.unreadable", path=str(path)) from exc

    # Molecule.from_file may return a list when the file holds multiple
    # molecules; we parameterize a single ligand for now (the config is
    # list-shaped so multi-ligand support can layer on later).
    if isinstance(molecule, list):
        if len(molecule) != 1:
            raise LigandError(
                f"Ligand file {path.name!r} contains {len(molecule)} "
                f"molecules; provide a single-molecule SDF/MOL2 (multi-ligand "
                f"support is not yet implemented)."
            , code="setup.ligand.multiple_molecules", path=str(path), count=len(molecule))
        molecule = molecule[0]

    molecule.name = name

    # Net charge: read from the formal charges the file carries. A stated
    # `ligand_net_charge` is checked against it rather than substituted for
    # it, because the number is not what the force field sees: the atoms
    # are. A file drawn as a neutral amidine parameterises as a neutral
    # amidine however the configuration labels it, so accepting the label
    # would produce a run whose charge record and whose chemistry disagree.
    #
    # This was a finding before it was a check. A benchmark supplied
    # benzamidine as the Chemical Component Dictionary's ideal SDF, which
    # is the neutral form, and the salt bridge that system is known for was
    # absent from two independent tools. Both were right about the molecule
    # they were given.
    inferred = _infer_net_charge(molecule)
    if net_charge is not None and inferred is not None and net_charge != inferred:
        raise StudyError(
            f"Ligand {name}: the study states a net charge of {net_charge:+d}, "
            f"and {path.name} carries formal charges summing to "
            f"{inferred:+d}. The file is the chemistry -- its protonation "
            "decides what is parameterised -- so the two cannot both stand. "
            "Supply a file in the protonation state the study means (for an "
            "amidine or a carboxylate at physiological pH that is usually "
            "not the Chemical Component Dictionary's ideal form, which is "
            "drawn neutral), or drop `ligand_net_charge` and let the file "
            "speak for itself."
        , code="setup.chemistry.charge_contradicted", resname=name, stated=net_charge)
    resolved_charge = net_charge if net_charge is not None else inferred
    logger.info(
        "Loaded ligand %s from %s (net charge=%s, taken from the file's own "
        "formal charges). Supplying chemistry as a file sets the protonation "
        "state: an ideal SDF is drawn in one particular form, and it is the "
        "form that will be simulated.",
        name, path.name,
        resolved_charge if resolved_charge is not None else "unknown",
    )
    return molecule


def _infer_net_charge(molecule: Any) -> int | None:
    """Best-effort integer net charge from an OpenFF molecule's total charge.

    Returns ``None`` if the charge cannot be determined as a clean integer.
    """
    try:
        total = molecule.total_charge
        # openff total_charge is a pint/openff Quantity in elementary charge;
        # coerce to a plain number robustly.
        magnitude = getattr(total, "magnitude", total)
        value = float(magnitude)
        rounded = round(value)
        if abs(value - rounded) > 1e-6:
            return None
        return int(rounded)
    except Exception:  # noqa: BLE001 - inference is best-effort
        return None
