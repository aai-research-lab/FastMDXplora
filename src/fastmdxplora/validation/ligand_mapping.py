"""Whether each prepared ligand's chemistry file matches its pose, atom for atom.

A ligand's chemistry (bond orders, formal charges) comes from an SDF or MOL2
file and, on a complex, its pose from the structure. Preparation matches the
file's heavy atoms to the structure's residue, choosing among the graph's
matches by the file's bond orders, then its stereochemistry, then the
1-3 distances and the fit
(:func:`fastmdxplora.setup.ligand.pose_from_structure`), builds each
hydrogen from internal coordinates, keeps a copy of the file in
``setup/ligands/``, and records the copy, its SHA-256 and the mapping in
``setup_parameters.json`` (``ligand_poses``).

This checks a study already prepared, reading only, against the copy of
the file setup kept, never against a pose it has just placed. A ligand is
``verified`` only where the prepared system's own ligand atoms
(``setup/topology.pdb``) are the same molecule as the file's (its elements
in the file's order, bonded as the file's), on the structure's pose
(``setup/input.pdb``) where the mapping puts them (every heavy atom within
:data:`TOLERANCE_NM`), with the file's stereochemistry wherever it is
judged, on the atoms the file's bond orders choose, and no hydrogen within
:data:`~fastmdxplora.setup.ligand.HYDROGEN_OVERLAP_NM` of an atom three or
more bonds away or inside its own atom's bonds; and where a SHA-256 was
recorded, the file read is the one prepared from. The worst bond-length departure from the file's, the worst
H-X-Y angle and the closest contact are reported as numbers. A study
prepared before the mapping was recorded is checked against its prepared
atoms given to the structure's by position ("found again").

Each ligand is one of:

- ``verified``: every stated centre the structure decided, and agreeing,
  no hydrogen more than :data:`HYDROGEN_ANGLE_NOTED_DEG` from where it
  should be;
- ``placed as stated``: the same, but for a centre the structure did not
  decide (its arms' bond orders within its noise, the lengths said), models
  flat or holds within noise, named by the structure's atom names: the
  handedness there is the file's, said so, never counted as agreeing;
- ``agrees``: verified but for a hydrogen more than
  :data:`HYDROGEN_ANGLE_NOTED_DEG` from where its atom's bonds put it, said
  with the atom;
- ``does not agree``: the prepared system's ligand is not the file's
  molecule on the structure's pose, said how; among others, a hydrogen at a
  stated centre on the other side from the file's handedness;
- ``refused``: setup would refuse it today, with the refusal's code;
- ``file changed``: the file is not the one the study was prepared from;
- ``pose from the file``: no mapping to check (``ligand_pose: file``, or a
  structure without the residue);
- ``not verifiable``: prepared before the mapping was recorded, and its
  prepared coordinates cannot be read; or its stereochemistry could not be
  checked (RDKit does not read the molecule's chemistry);
- ``not read``: a record, the structure or the file could not be read,
  or a read held to the study refused it.

Called on its own, the check holds its reads to the study (and to the study
a run was simulated from, for one given ``setup_from``), so a record linked
out of it is not read, and the file is read from the copy setup kept; inside
a held read it keeps that one. The files are read with RDKit rather than the
OpenFF toolkit, which is conda-forge-only: the atoms and bonds are those the
toolkit reads, in the file's order.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from fastmdxplora import record_access
from fastmdxplora.refusals import OutsideWorkspace

__all__ = [
    "VERIFIED",
    "PLACED_AS_STATED",
    "AGREES",
    "DISAGREES",
    "REFUSED",
    "CHANGED",
    "FILE_POSE",
    "NOT_VERIFIABLE",
    "NOT_READ",
    "TOLERANCE_NM",
    "check_ligand_mappings",
    "molecule_from_file",
]

VERIFIED = "verified"
PLACED_AS_STATED = "placed as stated"
AGREES = "agrees"

#: A hydrogen this far from where it should be (degrees) is said: the
#: ligand then "agrees" rather than is "verified".
HYDROGEN_ANGLE_NOTED_DEG = 30.0
DISAGREES = "does not agree"
REFUSED = "refused"
CHANGED = "file changed"
FILE_POSE = "pose from the file"
NOT_VERIFIABLE = "not verifiable"
NOT_READ = "not read"

#: How far a prepared heavy atom may lie from the structure atom it was
#: mapped to: both files are written to a thousandth of an Angstrom, and a
#: wrong atom is a bond length away.
TOLERANCE_NM = 0.002


class _Atom:
    def __init__(self, atomic_number: int, name: str = ""):
        self.atomic_number = int(atomic_number)
        self.name = name


class _Bond:
    def __init__(self, first: int, second: int):
        self.atom1_index = int(first)
        self.atom2_index = int(second)


class _Conformer:
    """Positions in nanometres, in the shape the OpenFF conformer has here."""

    def __init__(self, values: Any, _unit: str = "nanometer"):
        import numpy as np

        self._values = np.asarray(values, dtype=float)

    def m_as(self, _unit: str) -> Any:
        return self._values


class _Molecule:
    """A ligand file's atoms, bonds and conformer, as preparation reads them."""

    def __init__(self, rdkit: Any):
        self.rdkit = rdkit
        self.atoms = [_Atom(atom.GetAtomicNum(),
                            atom.GetProp("_TriposAtomName")
                            if atom.HasProp("_TriposAtomName") else "")
                      for atom in rdkit.GetAtoms()]
        self.bonds = [_Bond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
                      for bond in rdkit.GetBonds()]
        self._conformers = ([_Conformer(rdkit.GetConformer().GetPositions() / 10.0)]
                            if rdkit.GetNumConformers() else [])

    @property
    def conformers(self) -> list[_Conformer]:
        return self._conformers


def _molecule_from_text(text: str, name: str) -> _Molecule:
    from rdkit import Chem

    from fastmdxplora.setup.ligand import LigandError

    if name.lower().endswith(".mol2"):
        rdkit = Chem.MolFromMol2Block(text, removeHs=False, sanitize=False)
    else:
        rdkit = Chem.MolFromMolBlock(text, removeHs=False, sanitize=False)
    if rdkit is None:
        raise LigandError(f"{name} could not be read as a molecule.",
                          code="setup.ligand.unreadable", path=name)
    try:
        # Sanitized where it can be, so its stereochemistry reads; as
        # written where it cannot, its atoms and bonds as they are.
        Chem.SanitizeMol(rdkit)
    except Exception:  # noqa: BLE001 - chemistry RDKit does not accept
        pass
    return _Molecule(rdkit)


def molecule_from_file(path: str | Path) -> _Molecule:
    """A ligand file's molecule, its hydrogens and atom order as written.

    Raises LigandError (`setup.ligand.unreadable`) where RDKit cannot
    read it.
    """
    path = Path(path)
    text = record_access.read_text(path, encoding="utf-8", errors="replace")
    return _molecule_from_text(text, path.name)


def _there(path: Path) -> bool:
    """Whether a record is there; `OutsideWorkspace` passes up from a read
    held to the study, which is not the same as not there."""
    try:
        record_access.stat(path)
    except OutsideWorkspace:
        raise
    except OSError:
        return False
    return True


def _ligand_file(recorded: str, root: Path, setup: Path, *,
                 first: Path | None = None) -> tuple[Path | None, bool]:
    """Where a ligand file is now: the copy setup kept in the study
    (``first``), then the path as recorded, from the study, and among the
    study's own ligand files. A place a read held to the study refuses is
    not this one, and the next is tried; the second value says whether one
    was refused."""
    given = Path(recorded).expanduser()
    candidates = [first] if first is not None else []
    candidates += ([given] if given.is_absolute() else [root / given, setup / given])
    candidates.append(setup / "ligands" / given.name)
    refused = False
    for candidate in candidates:
        try:
            if _there(candidate):
                return candidate, refused
        except OutsideWorkspace:
            refused = True
    return None, refused


def _there_quietly(path: Path) -> bool:
    """Whether a record is there and may be read, refusing nothing."""
    return record_access.can_read(path) and record_access.exists(path)


def _held() -> bool:
    """Whether this call is inside a read held to a study already."""
    return not record_access.can_read(Path(os.path.abspath(os.sep)))


def _setup_of(root: Path) -> Path:
    """The setup folder of the system a run simulated: its own, or the one
    its `setup_from` names (`simulation.pipeline.setup_records_of`)."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    try:
        found = setup_records_of(root)
    except (OSError, ValueError):
        found = None
    return Path(found) if found is not None else root / "setup"


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
    except ValueError:
        return False
    return True


def check_ligand_mappings(study: str | Path) -> list[dict[str, Any]]:
    """For each ligand a study prepared, whether its prepared atoms are its
    file's molecule on the structure's pose.

    One entry per ligand, in the order preparation placed them:
    ``ligand`` (its residue name), ``copy`` (which copy of that name, from
    1), ``file``, ``status`` (see the module), ``code`` (a refusal's, or
    None), ``said`` (what was found, in a sentence), ``mapping``
    ("recorded", "found again", or None) and ``deviation_nm`` (the largest
    distance of a prepared heavy atom from its structure atom, or None).
    Empty where the study prepared no ligand; one ``not read`` entry,
    ligand None, where its setup record cannot be read.
    """
    root = Path(study)
    if _held():
        return _checked(root, _setup_of(root))
    with record_access.confined_records(root, lambda _path: True):
        setup = _setup_of(root)
        if _inside(setup, root) and _there_quietly(setup / "setup_parameters.json"):
            return _checked(root, setup)
    # Simulated from another study's prepared system, which a read held to
    # this study does not look for: found where the run's records say, and
    # that study's files read held to it.
    setup = _setup_of(root)
    if _inside(setup, root):
        with record_access.confined_records(root, lambda _path: True):
            return _checked(root, setup)
    with record_access.confined_records(setup.parent, lambda _path: True):
        return _checked(root, setup)


def _unread(entry: dict[str, Any], why: str) -> dict[str, Any]:
    return {**entry, "status": NOT_READ, "said": f"not read: {why}"}


def _checked(root: Path, setup: Path) -> list[dict[str, Any]]:
    from fastmdxplora.setup.ligand import _load_structure

    blank = {"ligand": None, "copy": None, "file": None, "code": None,
             "mapping": None, "deviation_nm": None}
    try:
        record = json.loads(record_access.read_text(
            setup / "setup_parameters.json", encoding="utf-8"))
    except OutsideWorkspace:
        return [_unread(blank, "the setup record is outside the study's files")]
    except (OSError, ValueError):
        return [_unread(blank, "no setup record could be read")]
    record = record if isinstance(record, dict) else {}
    forcefield = record.get("resolved_forcefield")
    ligand = forcefield.get("ligand") if isinstance(forcefield, dict) else None
    if not isinstance(ligand, dict) or not ligand.get("files"):
        return []
    files = [str(f) for f in ligand["files"]]
    names = ligand.get("name")
    names = ([str(n) for n in names] if isinstance(names, list)
             else [str(names or "LIG")] * len(files))
    parameters = record.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    policy = str(parameters.get("ligand_pose") or "auto").strip().lower()
    poses = [p for p in record.get("ligand_poses") or [] if isinstance(p, dict)]
    recorded_of = {(str(p.get("name", "")).strip().upper(), p.get("copy")): p
                   for p in poses}
    bilayer = bool(record.get("bilayer"))

    structure = setup / "input.pdb"
    entries = [{**blank, "ligand": name.strip().upper(), "file": file}
               for file, name in zip(files, names)]
    try:
        structure_there = _there(structure)
    except OutsideWorkspace:
        return [_unread(e, "the structure is outside the study's files") for e in entries]
    crystal_frame = None
    if structure_there:
        try:
            crystal_frame = _load_structure(structure)
        except OutsideWorkspace:
            return [_unread(e, "the structure is outside the study's files")
                    for e in entries]
        except Exception:  # noqa: BLE001 - a structure that will not read
            crystal_frame = None
    prepared, prepared_unread = None, None
    try:
        if _there(setup / "topology.pdb"):
            prepared = _load_structure(setup / "topology.pdb")
        else:
            prepared_unread = "there is no prepared topology"
    except OutsideWorkspace:
        prepared_unread = "the prepared topology is outside the study's files"
    except Exception as exc:  # noqa: BLE001 - a topology that will not read
        prepared_unread = f"the prepared topology could not be read ({type(exc).__name__})"

    found: list[dict[str, Any]] = []
    placed: Counter[str] = Counter()
    for entry in entries:
        name = entry["ligand"]
        copy = placed[name]
        placed[name] += 1
        entry["copy"] = copy + 1
        recorded = recorded_of.get((name, copy + 1))
        found.append(_one(entry, recorded, root, setup, structure, structure_there,
                          crystal_frame, prepared, prepared_unread, policy, copy,
                          bilayer))
    return found


def _one(entry: dict[str, Any], recorded: dict[str, Any] | None, root: Path,
         setup: Path, structure: Path, structure_there: bool, crystal_frame: Any,
         prepared: Any, prepared_unread: str | None, policy: str, copy: int,
         bilayer: bool) -> dict[str, Any]:
    from fastmdxplora.setup.ligand import LigandError, pose_by_policy

    name = entry["ligand"]
    kept = (recorded or {}).get("file")
    path, refused = _ligand_file(entry["file"], root, setup,
                                 first=(setup / kept) if kept and not Path(kept).is_absolute()
                                 else None)
    if path is None:
        return _unread(entry, "the ligand file is outside the study's files" if refused
                       else f"no ligand file at {entry['file']}")
    try:
        data = record_access.read_bytes(path)
    except OutsideWorkspace:
        return _unread(entry, "the ligand file is outside the study's files")
    except OSError as exc:
        return _unread(entry, f"the ligand file could not be read ({exc})")
    digest = hashlib.sha256(data).hexdigest()
    wanted = (recorded or {}).get("sha256")
    if wanted and wanted != digest:
        return {**entry, "status": CHANGED,
                "said": f"{path.name} has changed since the study was prepared from "
                        f"it (SHA-256 {wanted[:12]} recorded, {digest[:12]} now)"}
    try:
        molecule = _molecule_from_text(data.decode("utf-8", "replace"), path.name)
    except LigandError as exc:
        return {**entry, "status": REFUSED, "code": exc.code, "said": str(exc)}
    if policy != "file" and not structure_there:
        return {**entry, "status": FILE_POSE,
                "said": "the study has no structure, so the file's pose is the only one"}
    today: dict[str, Any] = {}
    try:
        # On a copy: the placing replaces its conformer, and what follows
        # holds the prepared system to the file, never to a pose placed now.
        _, said = pose_by_policy(_molecule_from_text(data.decode("utf-8", "replace"),
                                                     path.name),
                                 structure, name, policy=policy, copy=copy, record=today)
    except OutsideWorkspace:
        return _unread(entry, "the structure is outside the study's files")
    except LigandError as refusal:
        return {**entry, "status": REFUSED, "code": getattr(refusal, "code", None),
                "said": str(refusal)}
    if today.get("pose") != "structure":
        return {**entry, "status": FILE_POSE,
                "said": said or f"the structure holds no {name}, so the file's pose stands"}

    mapping = recorded.get("mapping") if recorded else None
    if prepared is None:
        if mapping:
            return _unread(entry, prepared_unread or "the prepared topology")
        return {**entry, "status": NOT_VERIFIABLE,
                "said": "not verifiable: prepared before the mapping was recorded, "
                        f"and {prepared_unread}"}
    reflected = [r for r in today.get("reflected_to_the_file_s_side") or []
                 if isinstance(r, dict) and "file_atom" in r]
    return _against_the_prepared(entry, molecule, mapping, crystal_frame, prepared,
                                 name, copy, bilayer, bool(wanted), reflected)


def _against_the_prepared(entry: dict[str, Any], molecule: Any, mapping: Any,
                          crystal_frame: Any, prepared: Any, name: str, copy: int,
                          bilayer: bool, checksummed: bool,
                          reflected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The prepared system's ligand held to the file and to the structure's
    pose under the mapping. ``reflected``: file atoms setup laid on the
    file's side of a centre the structure holds within noise the other way,
    held to their side, not to the structure's position."""
    import numpy as np

    from fastmdxplora.setup.ligand import (
        BOND_NOISE_NM,
        HYDROGEN_OVERLAP_NM,
        _bond_said,
        _atomic_number,
        _bonds_by_distance,
        _broken_bonds,
        _Chemistry,
        _choose_match,
        _expected_on,
        _hydrogen_side,
        _volume,
        _across,
        STEREO_DIHEDRAL_MIN,
        STEREO_VOLUME_MIN,
        _laid_as_single,
        _laid_differently,
        _landing,
        _metal_bonds,
        _misfit_said,
        _other_isomer_said,
        _held_within_noise_said,
        _file_name,
        _Hydrogens,
        _superposing_rotation,
        _tie_window,
    )

    def disagrees(said: str, code: str | None = None, **more: Any) -> dict[str, Any]:
        return {**entry, **more, "status": DISAGREES, "code": code, "said": said}

    residues = [r for r in prepared.topology.residues if r.name.strip().upper() == name]
    if copy >= len(residues):
        return disagrees(f"the prepared system holds {len(residues)} residue"
                         f"{'' if len(residues) == 1 else 's'} named {name}, and this is "
                         f"copy {copy + 1}")
    atoms = list(residues[copy].atoms)
    if len(atoms) != len(molecule.atoms):
        return disagrees(f"the prepared {name} has {len(atoms)} atoms and the file "
                         f"{len(molecule.atoms)}")
    for index, (atom, wanted) in enumerate(zip(atoms, molecule.atoms)):
        if _atomic_number(atom) != wanted.atomic_number:
            return disagrees(f"the prepared {name}'s atom {index + 1} is not the "
                             f"file's {_file_name(molecule, index)}")
    xyz = prepared.xyz[0][[a.index for a in atoms]].astype(float)
    heavy = [i for i, atom in enumerate(molecule.atoms) if atom.atomic_number > 1]
    chemistry = _Chemistry(molecule, heavy, molecule.conformers[0].m_as("nanometer"))
    if _bonds_by_distance(xyz[heavy], chemistry.elements) != chemistry.graph:
        return disagrees(f"the prepared {name}'s heavy atoms are not bonded as the "
                         "file's are: its atoms are not on their own positions")
    _, (bond_off, u, v) = chemistry.geometry_score(xyz[heavy])

    crystal_residues = [r for r in crystal_frame.topology.residues
                        if r.name.strip().upper() == name] if crystal_frame is not None else []
    if copy >= len(crystal_residues):
        return disagrees(f"the structure holds no copy {copy + 1} of {name}")
    crystal_indices = [a.index for a in crystal_residues[copy].atoms
                       if _atomic_number(a) != 1]
    laid = xyz[heavy]
    found: list[int] | None = None
    if mapping:
        by_file = {int(m["file_atom"]): int(m["structure_atom"]) for m in mapping
                   if isinstance(m, dict) and "file_atom" in m and "structure_atom" in m}
        if sorted(by_file) != heavy or not set(by_file.values()) <= set(crystal_indices):
            return disagrees("the recorded mapping does not cover the file's heavy "
                             "atoms with the structure's residue")
        target = crystal_frame.xyz[0][[by_file[i] for i in heavy]].astype(float)
        how = "recorded"
        found = [crystal_indices.index(by_file[i]) for i in heavy]
    else:
        # Found again: which structure atom each prepared one lies on. Of a
        # symmetric molecule's matches, preparation may have taken another
        # than today's (which F of a CF3 is which), so the prepared atoms
        # are given to the structure's by position, element by element.
        from scipy.optimize import linear_sum_assignment

        crystal = crystal_frame.xyz[0][crystal_indices].astype(float)
        crystal_elements = [_atomic_number(crystal_frame.topology.atom(i))
                            for i in crystal_indices]
        if sorted(crystal_elements) != sorted(chemistry.elements):
            return disagrees(f"the structure's {name} is not the file's molecule")
        if bilayer:
            choice = _choose_match(chemistry, crystal_elements, crystal,
                                   _bonds_by_distance(crystal, crystal_elements))
            if choice.order is None:
                return disagrees(f"the prepared {name}'s heavy atoms match none of the "
                                 "structure's by element and bond")
            first = crystal[choice.order]
            rotation = _superposing_rotation(laid, first)
            laid = (laid - laid.mean(axis=0)) @ rotation.T + first.mean(axis=0)
        target = np.zeros_like(laid)
        found = [0] * len(heavy)
        for element in set(chemistry.elements):
            mine = [k for k, e in enumerate(chemistry.elements) if e == element]
            theirs = [j for j, e in enumerate(crystal_elements) if e == element]
            cost = np.linalg.norm(laid[mine][:, None] - crystal[theirs][None], axis=2)
            _, given = linear_sum_assignment(cost)
            for i, k in enumerate(mine):
                target[k] = crystal[theirs[given[i]]]
                found[k] = theirs[given[i]]
        how = "found again"
    # Its atoms on the structure's, and on the ones the file's bond orders
    # say, whether the mapping was recorded or found again: a C=O on the
    # structure's C-OH lies on a structure atom as closely as on its own,
    # and its bonds fit another match better. Tied as setup ties them, a
    # deposit's noise allowed each bond the two assign differently.
    crystal = crystal_frame.xyz[0][crystal_indices].astype(float)
    crystal_elements = [_atomic_number(crystal_frame.topology.atom(i)) for i in crystal_indices]
    choice = _choose_match(chemistry, crystal_elements, crystal,
                           _bonds_by_distance(crystal, crystal_elements))
    if choice.order is not None and found is not None and len(chemistry.bond_pairs):
        crystal_graph = _bonds_by_distance(crystal, crystal_elements)
        skip = (_broken_bonds(crystal_graph, crystal, crystal_elements)
                | _metal_bonds(crystal_graph, crystal_elements))

        def score(order: list[int]) -> float:
            departures = chemistry.departures(crystal[order])[0]
            return float(sum(d for d, bond in zip(departures, _landing(chemistry, order))
                             if bond not in skip))

        window = _tie_window(_expected_on(chemistry, found), _expected_on(chemistry, choice.order),
                             skip, choice.sigma or BOND_NOISE_NM)
        squeezed = _broken_bonds(crystal_graph, crystal, crystal_elements, short_only=True)
        voided = bool(squeezed & set(_laid_differently(chemistry, found, choice.order)))
        if not voided and score(found) > score(choice.order) + window + 1e-12:
            return disagrees(f"the prepared {name}'s atoms lie on the structure's, "
                             "but on others than the file's bond orders say: its "
                             "bonds fit another match better (a C=O on a C-OH)",
                             mapping=how)
    if bilayer and mapping:
        # Placed for its bilayer, the whole solute was turned: held to the
        # pose's shape, not its place.
        rotation = _superposing_rotation(laid, target)
        laid = (laid - laid.mean(axis=0)) @ rotation.T + target.mean(axis=0)
    # A terminal atom setup laid on the file's side of a centre is held to
    # the distance setup moved it from its deposited place, as recorded
    # (older records, without it, to the deposited place itself).
    moved = {int(r["file_atom"]): float(r.get("moved_nm") or 0.0) for r in reflected or []}
    apart = np.linalg.norm(laid - target, axis=1)
    off = [abs(float(d) - moved.get(atom, 0.0)) if atom in moved else float(d)
           for atom, d in zip(heavy, apart)]
    deviation = max(off) if off else 0.0
    more = {"mapping": how, "deviation_nm": round(deviation, 5)}
    if deviation > TOLERANCE_NM:
        return disagrees(f"a prepared heavy atom of {name} lies {deviation:.3f} nm from "
                         f"the structure atom it was mapped to ({how}), past "
                         f"{TOLERANCE_NM} nm", **more)
    if chemistry.stated is None:
        return {**entry, **more, "status": NOT_VERIFIABLE,
                "said": f"not verifiable: stereochemistry not checked: {chemistry.why_not}"}
    stereo = chemistry.stereo_against(xyz[heavy])
    if stereo["differ"]:
        return disagrees(f"the prepared {name} is another stereoisomer of the file's: "
                         f"{stereo['said_differ']}", "setup.ligand.stereo_mismatch", **more)
    hydrogens = _Hydrogens(chemistry)
    closest = hydrogens.closest(xyz)
    angle = hydrogens.worst_angle(xyz)
    length = hydrogens.worst_length(xyz)
    # Recorded, not judged: how far the bonds lie from the file's, and the
    # hydrogens' worst angle, length and contact.
    more.update({
        "worst_bond_nm": round(float(bond_off), 4),
        "worst_bond": [chemistry.name(u), chemistry.name(v)],
        "worst_hydrogen_angle_deg": None if angle is None else round(float(angle[0]), 1),
        "worst_hydrogen_length_nm": None if length is None else round(float(length), 4),
        "closest_hydrogen_nm": None if closest is None else round(float(closest[0]), 4),
    })
    if closest is not None and closest[0] < HYDROGEN_OVERLAP_NM:
        return disagrees(f"the prepared {name}'s hydrogen {_file_name(molecule, closest[1])} "
                         f"is {closest[0] * 10:.2f} A from {_file_name(molecule, closest[2])}, "
                         f"three or more bonds from it, under {HYDROGEN_OVERLAP_NM * 10:.2f} A",
                         "setup.ligand.hydrogens_overlap", **more)
    # A stated centre the deposit holds within noise or flat has its
    # hydrogen on the side the file states, which can lie within the bonds
    # the deposit leans: there the side is the file's, not a fault.
    leaning = {hydrogens.carried[heavy[k]][0] for k, three, _ in chemistry.centres
               if len(hydrogens.carried.get(heavy[k], [])) == 1
               and abs(_volume(xyz, heavy[k], tuple(heavy[j] for j in three)))
               < STEREO_VOLUME_MIN}
    inside = [h for h in hydrogens.inside(xyz) if h not in leaning]
    if inside:
        return disagrees(f"the prepared {name}'s hydrogen {_file_name(molecule, inside[0])} "
                         "lies inside its own atom's bonds: every H-X-Y angle under 90 "
                         "degrees", **more)
    # Each stated centre's handedness with its hydrogen, on the prepared
    # atoms: at a centre the deposit models flat or holds within noise the
    # heavy atoms do not say it, and a hydrogen on the other side is the
    # other stereoisomer, whatever they say.
    for k, three, sign in chemistry.centres:
        parent = heavy[k]
        carried = hydrogens.carried.get(parent, [])
        if len(carried) == 1:
            ys = [heavy[j] for j in three]
            if (_hydrogen_side(xyz, parent, ys[0], ys[1], carried[0]) > 0) == sign:
                return disagrees(f"the prepared {name} is another stereoisomer of the file's: "
                                 f"the hydrogen of the stereocentre {chemistry.name(k)} lies on "
                                 "the other side", "setup.ligand.stereo_mismatch", **more)
    # A stated centre without a hydrogen, and a stated double bond, held
    # within noise: its side on the prepared atoms is the file's or the
    # prepared ligand relaxes to the other isomer.
    for k, three, sign in chemistry.centres:
        if len(hydrogens.carried.get(heavy[k], [])) == 1:
            continue
        volume = _volume(xyz, heavy[k], tuple(heavy[j] for j in three))
        if abs(volume) < STEREO_VOLUME_MIN and (volume > 0.0) != sign:
            return disagrees(f"the prepared {name} is another stereoisomer of the file's: "
                             f"the stereocentre {chemistry.name(k)}, held within noise, "
                             "lies on the other side", "setup.ligand.stereo_mismatch", **more)
    for a, u, v, b, cis in chemistry.double_bonds:
        cosine = _across(xyz, heavy[a], heavy[u], heavy[v], heavy[b])
        if abs(cosine) < STEREO_DIHEDRAL_MIN and (cosine > 0.0) != cis \
                and not _laid_as_single(chemistry, u, v, xyz, heavy):
            return disagrees(f"the prepared {name} is another stereoisomer of the file's: "
                             f"the double bond {chemistry.name(u)}={chemistry.name(v)}, held "
                             "within noise, lies on the other side of square",
                             "setup.ligand.stereo_mismatch", **more)
    # What the structure did not decide, what it models flat and what it
    # holds within noise, said as such, by the structure's atom names, and
    # never counted as agreeing: the handedness there is the file's.
    laid_on = found if found is not None else choice.order

    def named(k: int) -> str:
        return crystal_frame.topology.atom(crystal_indices[laid_on[k]]).name

    def bonds_said(entry: dict[str, Any]) -> str:
        return ", ".join(_bond_said(crystal_frame, crystal_indices, crystal_elements,
                                    a, b, length, o) for a, b, length, o in entry["bonds"])

    undecided = choice.undecided if choice.order is not None and not choice.why else []
    flat = sorted(set(stereo["flat"]) | set(choice.flat if not choice.why else []))
    noise = sorted(set(stereo["not_judged"]) - set(flat))
    # Bonds whose length fits another order than the file's, which no shape
    # confirms: placed as the file states, never verified.
    misfits = choice.misfits if choice.order is not None and not choice.why else []
    notes = [f"{', '.join(named(k) for k in entry['centres'])} taken from the file: the "
             f"structure did not decide which arm carries the double bond ({bonds_said(entry)})"
             for entry in undecided]
    notes += [f"{named(k)} not judged: the structure models it flat" for k in flat]
    notes += _held_within_noise_said(chemistry, noise, named, stereo.get("single", ()))
    sigma = choice.sigma or BOND_NOISE_NM
    record = chemistry.stereo_record(not_judged=stereo["not_judged"], flat=flat,
                                     undecided=[k for e in undecided for k in e["centres"]],
                                     notes=notes, named=named)
    said = (f"the prepared {name}'s {len(heavy)} heavy atoms lie within "
            f"{deviation:.4f} nm of the structure's under the mapping {how}, with the "
            f"file's elements and bonds (its worst bond {bond_off * 10:.2f} A from the "
            f"file's); {record['said']}; ")
    if closest is None:
        said += "no hydrogens"
    else:
        said += (f"no hydrogen within {HYDROGEN_OVERLAP_NM * 10:.2f} A of an atom three or "
                 f"more bonds away (the closest {closest[0] * 10:.2f} A"
                 + (f", the worst angle {angle[0]:.1f} degrees from "
                    + ("ideal" if chemistry.rough else "the file's") if angle else "")
                 + ")")
    said = (said
            + ("; the file is the copy prepared from (SHA-256)" if checksummed
               else "; prepared before the file's SHA-256 was recorded, so checked "
                    "against the file as it is now")
            + ("; superposed, the bilayer's placement having turned the solute"
               if bilayer else ""))
    more["stereochemistry"] = record
    sides = [f"{r.get('atom')} laid on the file's side of {r.get('centre')}, which the "
             "structure holds within noise the other way"
             + (f", {float(r['moved_nm']) * 10:.2f} A from its deposited place"
                if r.get("moved_nm") is not None else "") for r in reflected or []]
    rough = sigma > BOND_NOISE_NM + 1e-9
    if undecided or flat or noise or misfits or sides or rough:
        # Placed as the file states where the structure could not say.
        lead = "; ".join(
            [f"the structure did not decide {', '.join(named(k) for k in e['centres'])} "
             f"({bonds_said(e)})" for e in undecided]
            + [f"the structure models {named(k)} flat" for k in flat]
            + _held_within_noise_said(chemistry, noise, named, stereo.get("single", ()))
            + sides
            + ([_other_isomer_said(undecided)] if choice.others_fit and (undecided or flat or noise)
               else [])
            + [_misfit_said(crystal_frame, crystal_indices, crystal_elements, m) for m in misfits]
            + ([f"this copy's noise {sigma * 10:.3f} A a bond, past which alone its "
                "lengths decide an order" if not (undecided or flat or noise or misfits or sides)
                else f"this copy's noise {sigma * 10:.3f} A a bond"] if rough else []))
        bent = (f"; a hydrogen {angle[0]:.0f} degrees from "
                f"{'ideal' if chemistry.rough else 'the file'} at "
                f"{_file_name(molecule, angle[2])}"
                if angle is not None and angle[0] > HYDROGEN_ANGLE_NOTED_DEG else "")
        return {**entry, **more, "status": PLACED_AS_STATED,
                "said": f"placed as stated; {lead}{bent}. {said}"}
    if angle is not None and angle[0] > HYDROGEN_ANGLE_NOTED_DEG:
        return {**entry, **more, "status": AGREES,
                "said": f"agrees, with a hydrogen {angle[0]:.0f} degrees from "
                        f"{'ideal' if chemistry.rough else 'the file'} at "
                        f"{_file_name(molecule, angle[2])}. {said}"}
    return {**entry, **more, "status": VERIFIED, "said": said}
