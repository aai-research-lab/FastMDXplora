"""Ligand / cofactor loading for protein-ligand systems.

This module validates and loads small-molecule ligands for parameterization
with the OpenFF small-molecule force fields (via ``openmmforcefields``'
``SystemGenerator``). It deliberately keeps the *loading/validation* concern
separate from the *system build* concern (which lives in
:mod:`fastmdxplora.setup.prepare`): here a ligand file becomes a
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
            "openmmforcefields ambertools\n\n"
            "Or install FastMDXplora itself from conda-forge, which brings "
            "the whole ligand path with it."
        , code="environment.backend.missing", packages=["openff-toolkit"]) from exc
    return Molecule


def takes_am1bcc_charges(small_molecule_forcefield: str | None) -> bool:
    """Whether a small-molecule force field gives a ligand AM1-BCC charges.

    The OpenFF force fields and GAFF both do, through the OpenFF toolkit.
    Anything else (espaloma assigns its own) is not held to a charge
    provider it would not use.
    """
    name = str(small_molecule_forcefield or "").strip().lower()
    return name.startswith(("openff", "smirnoff", "gaff"))


def am1bcc_provider() -> str | None:
    """What will compute a ligand's AM1-BCC charges here, or None.

    The OpenFF toolkit computes them by calling AmberTools' ``sqm`` or,
    where it is licensed, OpenEye's toolkit. Without either it still loads,
    and offers NAGL, RDKit and its built-in charges, none of which is
    AM1-BCC, so the first a study hears of it is a toolkit error raised
    after the system has been solvated. conda-forge's openff-toolkit does
    not bring AmberTools with it, so the toolkit being present says nothing
    about this.

    Raises ImportError where the toolkit itself is absent: that is a
    different thing missing, with its own install line.
    """
    from openff.toolkit.utils.toolkits import (
        AmberToolsToolkitWrapper,
        OpenEyeToolkitWrapper,
    )

    if AmberToolsToolkitWrapper.is_available():
        return "AmberTools"
    if OpenEyeToolkitWrapper.is_available():
        return "OpenEye"
    return None


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
    this name whose heavy atoms match the file's, element for element and
    bond for bond, it is the first case. If
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
    # has them. Matching on count is what establishes the two are the same
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

    # Which crystal atom is which of the file's heavy atoms, by element and
    # bond graph. File order was trusted here, and two files listing the
    # atoms differently gave bonds of 3 and 4.5 Angstroms with no error: the
    # clash check measures the ligand against the protein, never against
    # itself. The crystal has no bond records to rely on, so its bonds are
    # read from its geometry.
    crystal = frame.xyz[0][indices]
    elements = [_atomic_number(frame.topology.atom(i)) for i in indices]
    order = _heavy_atom_match(
        [molecule.atoms[i].atomic_number for i in heavy],
        _heavy_graph(molecule, positions, heavy),
        elements, _bonds_by_distance(crystal, elements),
        positions_a=positions[heavy], positions_b=crystal)
    if order is None:
        return _stand(
            f"the bonds between the atoms of {wanted} in "
            f"{Path(structure).name} do not match the supplied file's, "
            "element for element, so they are not the same molecule (or the "
            "deposited geometry is broken) and the file's own coordinates "
            "stand")
    reordered = order != list(range(len(heavy)))
    moved = positions.copy()
    moved[heavy] = crystal[order]

    # Each hydrogen hangs off its own heavy atom, turned by the rotation that
    # best lays the file's heavy atoms onto the structure's. The file's
    # conformer is in an arbitrary frame, so one translation for all of them
    # would leave X-H bonds several Angstroms long.
    rotation = _superposing_rotation(positions[heavy], moved[heavy])
    for index, parent in _hydrogen_parents(molecule, positions, heavy).items():
        moved[index] = moved[parent] + rotation @ (
            positions[index] - positions[parent])

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
    matched = ("; the two files list its atoms in different orders, so they "
               "were matched by element and bond" if reordered else "")
    return molecule, (
        f"placed {wanted}{which} at its coordinates in "
        f"{Path(structure).name} rather than the supplied file's, which "
        f"carries the chemistry and an arbitrary pose{matched}")


#: Covalent radii in nanometres (Cordero et al. 2008), for reading bonds off
#: a structure that has no bond records. Elements not listed use 0.15 nm.
_COVALENT_RADIUS_NM = {
    5: 0.084, 6: 0.076, 7: 0.071, 8: 0.066, 9: 0.057, 14: 0.111, 15: 0.107,
    16: 0.105, 17: 0.102, 34: 0.120, 35: 0.120, 53: 0.139,
}


def _atomic_number(atom: Any) -> int:
    element = getattr(atom, "element", None)
    return int(getattr(element, "atomic_number", 0) or 0)


def _bonds_by_distance(points: Any, elements: list[int]) -> list[set[int]]:
    """Heavy atoms closer than 1.2 times the sum of their covalent radii.

    The factor admits a stretched bond in a poorly resolved structure and
    stays below the 1-3 distance across a bond angle (C-C-C: 0.25 nm
    against 0.18 nm for C-C).
    """
    import numpy as _np

    points = _np.asarray(points, dtype=float)
    radii = _np.array([_COVALENT_RADIUS_NM.get(e, 0.15) for e in elements])
    distance = _np.linalg.norm(points[:, None] - points[None], axis=2)
    bonded = (distance <= 1.2 * (radii[:, None] + radii[None])) & (distance > 0.05)
    _np.fill_diagonal(bonded, False)
    return [set(_np.flatnonzero(row).tolist()) for row in bonded]


def _heavy_graph(molecule: Any, positions: Any,
                 heavy: list[int]) -> list[set[int]]:
    """The file's heavy-atom bonds, by position in ``heavy``: from its bond
    list where it has one, otherwise from its own conformer."""
    where = {atom: k for k, atom in enumerate(heavy)}
    graph: list[set[int]] = [set() for _ in heavy]
    bonds = getattr(molecule, "bonds", None)
    if bonds is None:
        return _bonds_by_distance(
            positions[heavy], [molecule.atoms[i].atomic_number for i in heavy])
    for bond in bonds:
        first, second = where.get(bond.atom1_index), where.get(bond.atom2_index)
        if first is not None and second is not None:
            graph[first].add(second)
            graph[second].add(first)
    return graph


def _heavy_atom_match(elements_a: list[int], graph_a: list[set[int]],
                      elements_b: list[int], graph_b: list[set[int]],
                      *, positions_a: Any = None, positions_b: Any = None,
                      limit: int = 200_000,
                      solutions: int = 256) -> list[int] | None:
    """For each atom of ``a``, the atom of ``b`` it is: same element, and
    bonded exactly where ``a`` is. ``None`` where no such correspondence
    exists, or none is found within ``limit`` steps.

    A molecule with symmetry has several. Heavy atoms alone do not tell an
    amidine's NH2 from its NH, or an acid's OH from its O, but the geometry
    does: given both sets of positions, the one that lays ``a`` best onto
    ``b`` is returned, among the first ``solutions`` found.
    """
    import numpy as _np

    n = len(elements_a)
    if n != len(elements_b):
        return None
    signature_a = sorted((elements_a[i], len(graph_a[i])) for i in range(n))
    signature_b = sorted((elements_b[i], len(graph_b[i])) for i in range(n))
    if signature_a != signature_b:
        return None

    # Visit ``a`` breadth first from its rarest kind of atom, so each atom
    # after the first of its fragment is placed beside one already placed.
    rarity = {kind: signature_a.count(kind) for kind in set(signature_a)}
    visit: list[int] = []
    seen: set[int] = set()
    for start in sorted(range(n), key=lambda i: (rarity[(elements_a[i], len(graph_a[i]))], i)):
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            atom = queue.pop(0)
            visit.append(atom)
            for neighbour in sorted(graph_a[atom]):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)

    mapping: dict[int, int] = {}
    used: set[int] = set()
    found: list[list[int]] = []
    steps = 0

    def extend(depth: int) -> bool:
        """False once the search should stop."""
        nonlocal steps
        if depth == n:
            found.append([mapping[i] for i in range(n)])
            return len(found) < solutions
        atom = visit[depth]
        placed = {mapping[j] for j in graph_a[atom] if j in mapping}
        # The atom's own position first, so the order the files share, where
        # they share one, is the first answer.
        for candidate in [atom, *(c for c in range(n) if c != atom)]:
            steps += 1
            if steps > limit:
                return False
            if (candidate in used or elements_b[candidate] != elements_a[atom]
                    or len(graph_b[candidate]) != len(graph_a[atom])
                    or graph_b[candidate] & used != placed):
                continue
            mapping[atom] = candidate
            used.add(candidate)
            going = extend(depth + 1)
            del mapping[atom]
            used.discard(candidate)
            if not going:
                return False
        return True

    extend(0)
    if not found:
        return None
    if positions_a is None or positions_b is None or len(found) == 1:
        return found[0]
    a = _np.asarray(positions_a, dtype=float)
    b = _np.asarray(positions_b, dtype=float)

    def misfit(order: list[int]) -> float:
        target = b[order]
        rotation = _superposing_rotation(a, target)
        laid = (a - a.mean(axis=0)) @ rotation.T
        return float(((laid - (target - target.mean(axis=0))) ** 2).sum())

    return min(found, key=misfit)


def _superposing_rotation(mobile: Any, target: Any) -> Any:
    """The proper rotation that best lays ``mobile`` onto ``target`` (Kabsch).

    The determinant is forced to +1: a reflection can fit a flat or nearly
    flat set of heavy atoms as well as a rotation does, and would put every
    out-of-plane hydrogen on the wrong face.
    """
    import numpy as _np

    a = mobile - mobile.mean(axis=0)
    b = target - target.mean(axis=0)
    u, _, vt = _np.linalg.svd(a.T @ b)
    sign = 1.0 if _np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    return vt.T @ _np.diag([1.0, 1.0, sign]) @ u.T


def _hydrogen_parents(molecule: Any, positions: Any,
                      heavy: list[int]) -> dict[int, int]:
    """Each hydrogen's heavy atom: by the bond graph where the molecule has
    one, otherwise the heavy atom it sits nearest in the file's conformer."""
    import numpy as _np

    heavy_atoms = set(heavy)
    parents: dict[int, int] = {}
    try:
        for bond in molecule.bonds:
            first, second = bond.atom1_index, bond.atom2_index
            if first in heavy_atoms and second not in heavy_atoms:
                parents[second] = first
            elif second in heavy_atoms and first not in heavy_atoms:
                parents[first] = second
    except AttributeError:
        parents = {}
    for index, atom in enumerate(molecule.atoms):
        if atom.atomic_number <= 1 and index not in parents:
            distances = _np.linalg.norm(positions[heavy] - positions[index],
                                        axis=1)
            parents[index] = heavy[int(_np.argmin(distances))]
    return parents


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
    # molecules; a single ligand is parameterized for now (the config is
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
    if inferred is None:
        # Refused rather than carried as "unknown". Formal charges in an
        # SDF or MOL2 are whole numbers, so failing to sum them to one means
        # the file was not read as the chemistry it describes -- and the
        # check below, which stops a stated charge standing against the
        # file's, was skipped whenever it happened, so the stated number went
        # into the record unchecked.
        raise StudyError(
            f"Ligand {name}: the formal charges in {path.name} could not be "
            "read as a whole-number net charge, so the charge this run would "
            "record cannot be taken from the file or checked against one "
            "stated in the study. Check that the file carries explicit "
            "hydrogens and its formal charges (an SDF's `M  CHG` lines), and "
            "supply it again."
        , code="setup.chemistry.charge_undetermined", resname=name)
    if net_charge is not None and net_charge != inferred:
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
