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

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import CodedError
from fastmdxplora.refusals import OutsideWorkspace
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

#: How a refusal says the way on through the file's own pose.
_FILE_POSE_SAID = (
    "ligand_pose: file (setup.ligand_pose). That setting applies to every ligand "
    "of the study, and each supplied file's own coordinates are then its pose, so "
    "they must lie in the binding site.")

#: The volume a tetrahedral centre's three neighbours' unit vectors span
#: (:func:`_volume`), by which a lean is said as a share of it.
_TETRAHEDRAL_VOLUME = 0.77

#: Where ``ligand_pose: file`` lays a ligand farther than this (nm) from the
#: centre of the structure's residue of its name, setup warns.
FILE_POSE_FAR_NM = 1.0


def _its_own_file(molecule: Any, wanted: str) -> str:
    """Where the file names another molecule than the residue, the way on
    through the deposited molecule's own file: "supply the file of the TES
    the structure holds, "."""
    name = str(getattr(molecule, "name", "") or "").strip()
    rdkit = getattr(molecule, "rdkit", None)
    if not name and rdkit is not None and rdkit.HasProp("_Name"):
        name = rdkit.GetProp("_Name").strip()
    if not name or name.upper() == wanted:
        return ""
    return f"supply the file of the {wanted} the structure holds, "


def _far_from_its_residue(molecule: Any, structure: str | Path, resname: str,
                          copy: int) -> float | None:
    """How far (nm) the file's heavy atoms' centre lies from the centre of
    the structure's residue of that name (the copy asked for), or None
    where there is no such residue or either cannot be read."""
    import numpy as _np

    try:
        if not molecule.conformers or not Path(structure).is_file():
            return None
        frame = _load_structure(structure)
        wanted = resname.strip().upper()
        residues = [r for r in frame.topology.residues if r.name.strip().upper() == wanted]
        if copy >= len(residues):
            return None
        there = [a.index for a in residues[copy].atoms if _atomic_number(a) != 1]
        xyz = _np.asarray(molecule.conformers[0].m_as("nanometer"), dtype=float)
        mine = [i for i, a in enumerate(molecule.atoms) if a.atomic_number > 1]
        if not there or not mine:
            return None
        return float(_np.linalg.norm(xyz[mine].mean(axis=0) - frame.xyz[0][there].mean(axis=0)))
    except Exception:  # noqa: BLE001 - a warning only, never a refusal
        return None


def pose_by_policy(molecule: Any, structure: str | Path, resname: str,
                   *, policy: str = "auto",
                   copy: int = 0,
                   record: dict | None = None) -> tuple[Any, str | None]:
    """Apply the pose the config asked for, or the one the files imply.

    ``auto`` is :func:`pose_from_structure` deciding by looking, and is the
    default because the files usually do say. Where the structure holds the
    residue and its pose cannot be taken, ``auto`` refuses rather than
    guess; the file's pose stands only where the structure has no such
    residue. The other two exist for the
    cases where what the author wants is not what the files imply, and both
    were discovered by a run that wanted them:

    ``file`` keeps the supplied file's coordinates even where the structure
    holds the residue. That is an unbound start on a complex -- the T4
    lysozyme control began life as a bug that did exactly this, and turned
    out to be the known-negative the contact analyses needed. A control
    should be a choice rather than an accident.

    ``structure`` requires the structure's pose, so a structure with no
    residue of that name is refused as well. On a bound run that fallback
    is the seventeen-Angstroms failure returning silently.

    ``record``, where given, is filled with what the setup record keeps of
    the placement (:func:`pose_from_structure`).
    """
    chosen = str(policy).strip().lower()
    if chosen not in POSE_POLICIES:
        raise LigandError(
            f"ligand_pose: unknown policy {policy!r}; expected one of "
            f"{', '.join(POSE_POLICIES)}. `auto` takes the pose from the "
            "structure where it holds the residue and from the file where "
            "it does not; `structure` and `file` insist on one side.", code="setup.ligand.pose_unavailable", policy=policy)
    if chosen == "file":
        # The structure's residue of that name, where it holds one: a file
        # left at its own idealised coordinates lies far from the site (1KT6's
        # retinal 62 A off), the seventeen-Angstrom failure asked for.
        far = _far_from_its_residue(molecule, structure, resname, copy)
        warned = (f"; its coordinates lie {far * 10:.1f} A from the centre of the structure's "
                  f"{resname.strip().upper()}, so it starts away from that site"
                  if far is not None and far > FILE_POSE_FAR_NM else "")
        if record is not None:
            record.update({"pose": "file", "why": "ligand_pose: file"})
            if far is not None:
                record["from_the_structure_s_residue_nm"] = round(far, 3)
            if warned:
                record["warning"] = warned[2:]
        if warned:
            logger.warning("ligand %s: %s", resname, warned[2:])
        return molecule, (
            f"the supplied file's pose stands for {resname} by request "
            f"(ligand_pose: file), whether or not the structure holds it{warned}")
    return pose_from_structure(
        molecule, structure, resname, copy=copy,
        required=(chosen == "structure"), record=record)


def pose_from_structure(molecule: Any, structure: str | Path, resname: str,
                        *, copy: int = 0,
                        required: bool = False,
                        record: dict | None = None) -> tuple[Any, str | None]:
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
    where there is no structure, or it has no such residue, and the SDF's
    own coordinates stand -- which is right when the ligand is being placed
    deliberately rather than read from a complex.

    Where the structure does hold a residue of this name and its pose
    cannot be taken -- too few copies, a different atom count, bonds that
    do not match, a structure that will not read -- this refuses. Those
    fell back to the file's coordinates, and on the T4 system the silent
    version of each was a run of benzene floating in solvent that looked in
    every respect like a run of benzene in a binding site. With
    ``required=True`` a structure without the residue refuses too.

    Of the graph's matches, the one the file's chemistry says is taken
    (:func:`_choose_match`: its bond orders within a deposit's noise, then
    its stereochemistry, then the 1-3 distances and the fit). Only three
    things refuse: atoms that do not match, another
    molecule (``setup.ligand.atoms_unmatched``, named; also a bond whose
    length and carbon's shape say another order, or a C=C moved beside a
    C-C, :func:`_other_order`); a stereoisomer no match explains, or a
    stated centre without a hydrogen or terminal neighbour, or a stated
    double bond, held within noise on the other side
    (``setup.ligand.stereo_mismatch``); and a hydrogen still
    within :data:`HYDROGEN_OVERLAP_NM` of an atom three or more bonds away
    after its rotor's scan (``setup.ligand.hydrogens_overlap``). Bond lengths
    against the file's, H-X-Y angles and the closest contact are recorded.

    ``record``, where given, is filled with what the setup record keeps:
    the pose's source and, from the structure, the residue, the mapping of
    the file's heavy atoms to the structure's, the matches ranked, the
    worst bond, the stereochemistry checked, how the hydrogens were built,
    the closest one and the worst angle.
    """

    def _stand(reason: str) -> tuple[Any, str]:
        """A pose the structure should give and cannot: refused, under
        ``auto`` as under ``structure``. ``auto`` fell back to the file's
        coordinates here, which on a complex is a ligand simulated away
        from its site with nothing but a log line to say so."""
        if required:
            raise LigandError(
                f"ligand_pose: structure was asked for, but {reason}.", code="setup.ligand.pose_unavailable")
        raise LigandError(
            f"The ligand's pose cannot be taken from the structure: {reason}. "
            "Setup stops rather than place it at the supplied file's own "
            "coordinates, which on a complex are not in the binding site. "
            "Where the file's pose is the one meant -- a docked or "
            "deliberately unbound start -- say so with ligand_pose: file.",
            code="setup.ligand.pose_unavailable")

    import numpy as _np

    if not Path(structure).is_file():
        # No structure at all, as for a system given as a sequence: nothing
        # holds a pose, and the file's is the only one there is.
        if required:
            raise LigandError(
                f"ligand_pose: structure was asked for, but there is no "
                f"structure at {structure} to take a pose from.",
                code="setup.ligand.pose_unavailable")
        if record is not None:
            record.update({"pose": "file", "why": "no structure"})
        return molecule, None
    try:
        frame = _load_structure(structure)
    except OutsideWorkspace:
        # A read held to a study, refusing a structure linked out of it:
        # not read, which is not the same as not there.
        raise
    except Exception as exc:  # noqa: BLE001 - a structure that will not read
        # Unread, whether it holds the ligand is not known.
        return _stand(
            f"{Path(structure).name} could not be read for it "
            f"({type(exc).__name__}), so whether it holds the ligand is not "
            "known")

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
        if record is not None:
            record.update({"pose": "file",
                           "why": f"the structure holds no residue named {wanted}"})
        return molecule, None
    if copy >= len(matches):
        return _stand(
            f"{Path(structure).name} holds {len(matches)} "
            f"cop{'y' if len(matches) == 1 else 'ies'} of {wanted} and this "
            f"is copy {copy + 1}, so there is none left to place it at")

    residue = matches[copy]
    # Heavy atoms only, on both sides: a crystal structure usually has no
    # hydrogens and the file has them, and a structure that has them (a
    # docked or protonated complex, a neutron or NMR model) was refused as
    # another molecule because its residue counted them.
    indices = [atom.index for atom in residue.atoms if _atomic_number(atom) != 1]
    heavy = [i for i, atom in enumerate(molecule.atoms)
             if atom.atomic_number > 1]

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
    chemistry = _Chemistry(molecule, heavy, positions)
    file_elements, file_graph = chemistry.elements, chemistry.graph
    crystal_graph = _bonds_by_distance(crystal, elements)
    where = f"{wanted} in {Path(structure).name}"
    if len(indices) != len(heavy):
        _refuse_unmatched(
            f"{where} has {len(indices)} heavy atoms and the supplied file has "
            f"{len(heavy)}, so they are not the same molecule (or the "
            "deposited residue is incomplete)",
            molecule, heavy, file_elements, file_graph,
            frame, indices, elements, crystal_graph, wanted)
    choice = _choose_match(chemistry, elements, crystal, crystal_graph)
    if not choice.complete:
        return _stand(
            f"the search for which of {wanted}'s matches is the file's stopped "
            f"after {choice.steps:,} steps with {choice.count:,} found, so the "
            "right one cannot be chosen")
    if choice.why in ("count", "graph"):
        _refuse_unmatched(
            f"the bonds between the atoms of {where} do not match the supplied "
            "file's, element for element, so they are not the same molecule "
            "(or the deposited geometry is broken)",
            molecule, heavy, file_elements, file_graph,
            frame, indices, elements, crystal_graph, wanted)
    order = choice.order
    if choice.why == "stereo":
        # The stereoisomer the file states, held to the one in the
        # structure, over every match: the bond graph is the same for an
        # enantiomer, a diastereomer and an E/Z pair, and a meso compound
        # or a ring's cis/trans centre has matches that turn a centre over
        # beside one that does not.
        named = [_mapped_name(molecule, heavy, k, frame, indices, order)
                 for k in choice.stereo["differ"]]

        def at(k: int) -> str:
            """A file atom by the structure's name, the file's beside it
            where they differ: "C17 (the file's C18)"."""
            mine, theirs = chemistry.name(k), frame.topology.atom(indices[order[k]]).name
            return theirs if mine == theirs else f"{theirs} (the file's {mine})"

        differ = list(choice.stereo["differ"])
        single_held = choice.stereo.get("single_held") or []
        parts = []
        for _, u, v, _, _ in chemistry.double_bonds:
            if u in differ and v in differ:
                parts.append(f"the double bond {at(u)}={at(v)} lies on a bond the structure lays "
                             "single, where another match lays it on a double bond"
                             if u in single_held and v in single_held else
                             f"the double bond {at(u)}={at(v)} is the other of E and Z")
                differ = [k for k in differ if k not in (u, v)]
        for k in differ:
            parts.append(f"the stereocentre {at(k)} lies on an atom the structure models "
                         "flat, where another match lays it on a tetrahedral one"
                         if k in choice.flat else
                         f"the stereocentre {at(k)} has the other handedness")
        sigma = (choice.sigma or BOND_NOISE_NM) * 10
        if choice.by_lengths:
            lengths = ", ".join(_bond_said(frame, indices, elements, a, b, length, o)
                                for a, b, length, o in choice.by_lengths)
            how = (f"Bond lengths took part: a match that would agree lays the "
                   f"file's bond orders on other bonds ({lengths}, the file's "
                   "orders as the refused match lays them), and the "
                   "structure's lengths there fit them the other way by more "
                   f"than this copy's noise ({sigma:.3f} A a bond). Supply the "
                   "file of the isomer the structure holds, or a structure of "
                   "the file's.")
        else:
            how = ("Bond lengths took no part: no match of the bond graph "
                   "agrees. Supply the file of the isomer the structure "
                   "holds, or a structure of the file's; the deposit may "
                   "itself carry a chirality error at a named centre.")
        raise LigandError(
            f"{where} is another stereoisomer of the supplied file's molecule: "
            f"{'; '.join(parts)}. {how} "
            "Setup stops rather than simulate the "
            "structure's isomer with the file's chemistry.",
            code="setup.ligand.stereo_mismatch", resname=wanted,
            atoms=named, option="setup.ligand")
    if choice.why == "other_order":
        named = [f"{frame.topology.atom(indices[a]).name}-{frame.topology.atom(indices[b]).name}"
                 for a, b, *_ in choice.by_lengths]
        shown = _other_molecule_said(frame, indices, elements, choice.by_lengths)
        raise LigandError(
            f"The ligand's pose cannot be taken from the structure: {where} is "
            f"deposited as a molecule other than the supplied file's. "
            f"{shown[0].upper()}{shown[1:]} "
            f"(this copy's noise {(choice.sigma or BOND_NOISE_NM) * 10:.3f} A a bond). "
            f"Check the deposit's own geometry and its validation report for this "
            f"{wanted}. To go on, use another deposit of the file's molecule, "
            f"{_its_own_file(molecule, wanted)}or take the pose from the file with "
            f"{_FILE_POSE_SAID}",
            code="setup.ligand.atoms_unmatched", resname=wanted,
            atoms=named, option="setup.ligand_pose")
    reordered = order != list(range(len(heavy)))
    moved = positions.copy()
    moved[heavy] = crystal[order]

    # Each hydrogen from internal coordinates: its X-H length and H-X-Y
    # angles the file's (ideal ones for a flat file), in the frame of its
    # own atom's bonds, rotors turned clear. One rotation for the whole
    # molecule, and then one per neighbourhood, left hydrogens of a
    # flexible ligand off their bonds' angles (an N-methyl's H-C-N at 76
    # degrees) wherever the file's conformer is not the crystal's.
    hydrogens = _Hydrogens(chemistry)
    reflected, held_other_way = _hold_stated_sides(moved, hydrogens)
    if held_other_way is not None:
        if held_other_way[0] == "centre":
            _, k, volume = held_other_way
            what = (f"the stereocentre {frame.topology.atom(indices[order[k]]).name} within "
                    "noise on the other side from the file's (its neighbours lean to the "
                    f"other side by {abs(volume) / _TETRAHEDRAL_VOLUME:.0%} of a tetrahedral "
                    f"centre's lean, under the {STEREO_VOLUME_MIN / _TETRAHEDRAL_VOLUME:.0%} "
                    "judged), and no hydrogen or terminal atom of it can be laid on the "
                    "file's side")
        else:
            _, u, v, cosine = held_other_way
            a, b, cis = next((aa, bb, cc) for aa, uu, vv, bb, cc in chemistry.double_bonds
                             if (uu, vv) == (u, v))
            stated = (f"{frame.topology.atom(indices[order[a]]).name} and "
                      f"{frame.topology.atom(indices[order[b]]).name} {'cis' if cis else 'trans'}")
            what = (f"the double bond {frame.topology.atom(indices[order[u]]).name}="
                    f"{frame.topology.atom(indices[order[v]]).name} within noise on the other "
                    f"side from the file's {stated} (its dihedral "
                    f"{float(_np.degrees(_np.arccos(max(-1.0, min(1.0, cosine))))):.0f} degrees)")
        raise LigandError(
            f"{where} holds {what}. Placed as deposited it relaxes to the other "
            "stereoisomer. Check the deposit's geometry at that atom and its "
            "validation report; where the file's isomer is meant, take the pose from "
            f"the file with {_FILE_POSE_SAID} Setup stops rather than simulate the other "
            "isomer with the file's chemistry.",
            code="setup.ligand.stereo_mismatch", resname=wanted, option="setup.ligand_pose")
    _build_hydrogens(moved, hydrogens)
    closest = hydrogens.closest(moved)
    angle = hydrogens.worst_angle(moved)
    if closest is not None and closest[0] < HYDROGEN_OVERLAP_NM:
        distance, hydrogen, other = closest
        raise LigandError(
            f"Placed at {where}, the supplied file's hydrogen "
            f"{_file_name(molecule, hydrogen)} comes {distance * 10:.2f} A from "
            f"{_file_name(molecule, other)}, three or more bonds from it, closer "
            f"than {HYDROGEN_OVERLAP_NM * 10:.2f} A at every torsion its rotor "
            "can take. The structure's pose leaves it no room; supply a file "
            "with hydrogens placed on the structure's pose.",
            code="setup.ligand.hydrogens_overlap", resname=wanted,
            atoms=[_file_name(molecule, hydrogen), _file_name(molecule, other)],
            option="setup.ligand")
    def named(k: int) -> str:
        """A file atom by the structure's name for the atom it was laid on."""
        return frame.topology.atom(indices[order[k]]).name

    def undecided_said(entry: dict[str, Any]) -> str:
        centres = ", ".join(named(k) for k in entry["centres"])
        bonds = ", ".join(_bond_said(frame, indices, elements, a, b, length, o)
                          for a, b, length, o in entry["bonds"])
        return (f"{centres} taken from the file: the structure did not decide which "
                f"arm carries the double bond ({bonds})")

    noise = [k for k in (choice.stereo.get("not_judged") or [] if choice.stereo else [])
             if k not in choice.flat]
    notes = ([undecided_said(entry) for entry in choice.undecided]
             + [f"{named(k)} not judged: the structure models it flat" for k in choice.flat]
             + _held_within_noise_said(chemistry, noise, named,
                                       choice.stereo.get("single", ()) if choice.stereo else ()))
    # Said only where the structure left a centre to the file: elsewhere a
    # tied match that turns a centre is the molecule's own symmetry.
    if choice.others_fit and notes:
        notes.append(_other_isomer_said(choice.undecided))
    notes += [f"{named(end)} laid on the file's side of {named(k)}, which the structure "
              "holds within noise the other way" for k, end in reflected]
    stereo_notes = list(notes)
    notes += [_misfit_said(frame, indices, elements, m) for m in choice.misfits]
    if (choice.sigma or BOND_NOISE_NM) > BOND_NOISE_NM + 1e-9:
        notes.append(f"this copy's noise {choice.sigma * 10:.3f} A a bond")
    stereo = chemistry.stereo_record(
        not_judged=choice.stereo.get("not_judged") if choice.stereo else None,
        flat=choice.flat, undecided=[k for e in choice.undecided for k in e["centres"]],
        notes=stereo_notes, named=named)

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
    if record is not None:
        chain = getattr(residue, "chain", None)
        record.update({
            "pose": "structure",
            "residue": {"name": wanted,
                        "chain": getattr(chain, "chain_id", None),
                        "resseq": getattr(residue, "resSeq", None),
                        "copy": copy + 1, "copies": len(matches)},
            # File atom (0-based, as the file lists them) to the structure's
            # atom (0-based, as input.pdb lists them).
            "mapping": [{"file_atom": int(heavy[k]),
                         "structure_atom": int(indices[order[k]]),
                         "structure_name": frame.topology.atom(indices[order[k]]).name}
                        for k in range(len(heavy))],
            "matches_ranked": choice.count,
            # How far the structure's bonds lie from the file's (the worst),
            # and the hydrogens' worst angle and contact: recorded, not
            # refused. A deposit's bonds lie up to 0.2 A from an ideal
            # component's, and a flattened centre leaves its hydrogen no
            # place that keeps the file's angles.
            "worst_bond_nm": round(float(choice.worst_bond[0]), 4),
            # A terminal group whose giving the structure did not decide
            # (a P=O and its P-OH 0.08 A apart, a deposit's coordinate
            # error): which oxygen carries the proton is the file's choice.
            "assigned_within_noise": [
                {"atom": chemistry.name(k),
                 "said": "which of its terminal atoms is which is not determined "
                         "by the structure, within its noise",
                 **_terminal_lengths(chemistry, k, order, crystal, frame, indices)}
                for k in choice.within_noise],
            "worst_bond": [chemistry.name(choice.worst_bond[1]),
                           chemistry.name(choice.worst_bond[2])],
            "stereochemistry": stereo,
            # Centres the structure's lengths did not decide: another match
            # tied on them lays the double bond on the other arm and turns
            # the centre. Placed as the file states.
            "bond_orders_undecided": [
                {"centres": [named(k) for k in entry["centres"]],
                 "bonds": [{"structure_atoms": _structure_bond(frame, indices, a, b),
                            "length_nm": round(length, 4), "file_order": o}
                           for a, b, length, o in entry["bonds"]],
                 "said": undecided_said(entry)}
                for entry in choice.undecided],
            # Terminal atoms laid on the file's side of a stated centre the
            # structure holds within noise the other way.
            "reflected_to_the_file_s_side": [
                {"file_atom": int(heavy[end]), "atom": named(end), "centre": named(k),
                 # How far it was laid from its deposited place, which the
                 # check holds it to.
                 "moved_nm": round(float(_np.linalg.norm(moved[heavy[end]]
                                                          - crystal[order[end]])), 4)}
                for k, end in reflected],
            # Bonds whose deposited length fits another order than the
            # file's, which no shape confirms: placed as stated, said so.
            "bonds_fitting_another_order": [
                {"structure_atoms": _structure_bond(frame, indices, m[0], m[1]),
                 "length_nm": round(m[2], 4), "file_order": m[3], "fits_order": m[4],
                 "said": _misfit_said(frame, indices, elements, m)}
                for m in choice.misfits],
            # The noise on a bond the tie was judged with: the copy's own,
            # where it is worse than a good deposit's.
            "bond_noise_nm": round(float(choice.sigma or BOND_NOISE_NM), 4),
            "hydrogens": ("ideal for each atom's hybridisation, the file "
                          "being flat" if chemistry.flat
                          else "ideal for each atom's hybridisation, the file's "
                          "being roughly built" if chemistry.rough
                          else "the file's lengths and angles about each atom"),
            "closest_hydrogen_nm": (None if closest is None
                                    else round(float(closest[0]), 4)),
            "hydrogen_overlap_nm": HYDROGEN_OVERLAP_NM,
            "worst_hydrogen_angle_deg": (None if angle is None
                                         else round(float(angle[0]), 1)),
            # An =N-H left at the file's E or Z where turning it clear would
            # make the other isomer.
            "kept_crowded": [_file_name(molecule, h) for h in hydrogens.crowded],
        })
    return molecule, (
        f"placed {wanted}{which} at its coordinates in "
        f"{Path(structure).name} rather than the supplied file's, which "
        f"carries the chemistry and an arbitrary pose{matched}"
        + "".join(f"; {note}" for note in notes))


def _terminal_lengths(chemistry: Any, k: int, order: list[int], crystal: Any,
                      frame: Any, indices: list[int]) -> dict[str, Any]:
    """A terminal group's oxygens as given: the structure atom each file
    atom went to, its bond length there (nm), and which carries the file's
    double bond."""
    import numpy as _np

    given = []
    for j in sorted(chemistry.groups.get(k, [])):
        given.append({
            "file_atom": chemistry.name(j),
            "structure_atom": frame.topology.atom(indices[order[j]]).name,
            "length_nm": round(float(_np.linalg.norm(crystal[order[j]] - crystal[order[k]])), 4),
            "double": chemistry.double_to(k, j)})
    return {"given": given}


#: Covalent radii in nanometres (Cordero et al. 2008), for reading bonds off
#: a structure that has no bond records. Elements not listed use 0.15 nm.
_COVALENT_RADIUS_NM = {
    5: 0.084, 6: 0.076, 7: 0.071, 8: 0.066, 9: 0.057, 14: 0.111, 15: 0.107,
    16: 0.105, 17: 0.102, 34: 0.120, 35: 0.120, 53: 0.139,
}

#: Covalent radii of the common metals (nm, Cordero et al. 2008; Fe, Co and
#: Mn low spin). They judge only whether a metal's bond has a length a bond
#: can have: a metal's bonds are coordination, whose lengths follow its
#: oxidation and spin state rather than an order, so they are left out of
#: the bond-order score (:func:`_choose_match`).
_METAL_RADIUS_NM = {
    12: 0.141, 20: 0.176, 25: 0.139, 26: 0.132, 27: 0.126, 28: 0.124, 29: 0.132,
    30: 0.122, 42: 0.154, 44: 0.146, 45: 0.142, 46: 0.139, 74: 0.162, 77: 0.141,
    78: 0.136,
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


#: Steps the heavy-atom search may take. Each step tries one crystal atom
#: for one file atom, taken from the neighbours of an atom already placed,
#: and the interchangeable terminal atoms of a group (the F of a CF3, the O
#: of a PO3) are not searched at all, so the most symmetric ligands tried
#: (phytate, suramin, sucrose octasulfate, BArF, tetra-tBu-tetraphenylmethane)
#: take a few thousand.
MATCH_STEP_LIMIT = 2_000_000

#: How much more a bond's departure from its length counts than a 1-3
#: distance's where the two are summed (the check's record of a placement).
BOND_WEIGHT = 5.0

#: A deposit's noise on one bond length (nm), 0.025 A. Two matches that lay
#: expected lengths e and e' on one structure bond of length L differ in
#: score by (e' - e)(2L - e - e'), which noise of sigma on L moves by
#: 2|e' - e| sigma: they are tied where their scores differ by no more than
#: that, summed over the bonds they lay differently. Swapping a C=O and a
#: C-OH is then decided only where the deposit's two C-O differ by more
#: than 0.05 A, twice the noise; a deposit that models both 1.43 A does not
#: decide it.
BOND_NOISE_NM = 0.0025

#: The same for a 1-3 distance (nm): angles move by a few degrees between
#: conformers and programs.
ANGLE_NOISE_NM = 0.01


class _Matches:
    """What the heavy-atom search found: the best match (None where there
    is none), how many it ranked, the steps taken, and whether it saw them
    all."""

    def __init__(self, order: list[int] | None, count: int, steps: int,
                 complete: bool):
        self.order, self.count, self.steps, self.complete = order, count, steps, complete


def _graph_matches(labels_a: list[Any], graph_a: list[set[int]],
                   labels_b: list[Any], graph_b: list[set[int]],
                   limit: int, counter: list[int]):
    """Every correspondence of ``a``'s atoms to ``b``'s with equal labels
    and the same bonds, as lists, until ``limit`` steps (``counter[0]``
    counts them; past the limit the generator ends and ``counter[1]`` is 1).

    ``a`` is visited breadth first from its rarest label, and each atom
    after the first of its fragment takes its candidates from the
    neighbours, in ``b``, of an atom already placed.
    """
    n = len(labels_a)
    rarity: dict[Any, int] = {}
    for label in labels_a:
        rarity[label] = rarity.get(label, 0) + 1
    visit: list[int] = []
    anchor: dict[int, int | None] = {}
    seen: set[int] = set()
    for start in sorted(range(n), key=lambda i: (rarity[labels_a[i]], i)):
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        anchor[start] = None
        while queue:
            atom = queue.pop(0)
            visit.append(atom)
            for neighbour in sorted(graph_a[atom]):
                if neighbour not in seen:
                    seen.add(neighbour)
                    anchor[neighbour] = atom
                    queue.append(neighbour)
    every_b = list(range(len(labels_b)))
    mapping: dict[int, int] = {}
    used: set[int] = set()

    def extend(depth: int):
        if depth == n:
            yield [mapping[i] for i in range(n)]
            return
        atom = visit[depth]
        placed = {mapping[j] for j in graph_a[atom] if j in mapping}
        parent = anchor[atom]
        pool = sorted(graph_b[mapping[parent]]) if parent is not None else every_b
        for candidate in pool:
            counter[0] += 1
            if counter[0] > limit:
                counter[1] = 1
                return
            if (candidate in used or labels_b[candidate] != labels_a[atom]
                    or graph_b[candidate] & used != placed):
                continue
            mapping[atom] = candidate
            used.add(candidate)
            yield from extend(depth + 1)
            del mapping[atom]
            used.discard(candidate)
            if counter[1]:
                return

    yield from extend(0)


def _heavy_atom_match(elements_a: list[int], graph_a: list[set[int]],
                      elements_b: list[int], graph_b: list[set[int]],
                      *, positions_a: Any = None, positions_b: Any = None,
                      limit: int = MATCH_STEP_LIMIT) -> list[int] | None:
    """For each atom of ``a``, the atom of ``b`` it is: same element, and
    bonded exactly where ``a`` is. ``None`` where no such correspondence
    exists, or none is found within ``limit`` steps (:func:`_heavy_atom_matches`)."""
    return _heavy_atom_matches(elements_a, graph_a, elements_b, graph_b,
                               positions_a=positions_a, positions_b=positions_b,
                               limit=limit).order


def _heavy_atom_matches(elements_a: list[int], graph_a: list[set[int]],
                        elements_b: list[int], graph_b: list[set[int]],
                        *, positions_a: Any = None, positions_b: Any = None,
                        limit: int = MATCH_STEP_LIMIT) -> _Matches:
    """Every correspondence of ``a``'s atoms to ``b``'s by element and bond
    alone, the one that lays ``a`` best on ``b`` returned (the first,
    without positions). The graph alone: a ligand's placement is chosen by
    :func:`_choose_match`, which knows its chemistry."""
    n = len(elements_a)
    if n != len(elements_b):
        return _Matches(None, 0, 0, True)
    labels_a = [(elements_a[i], len(graph_a[i])) for i in range(n)]
    labels_b = [(elements_b[i], len(graph_b[i])) for i in range(n)]
    if sorted(labels_a) != sorted(labels_b):
        return _Matches(None, 0, 0, True)
    counter = [0, 0]
    best: tuple[float, list[int] | None] = (float("inf"), None)
    count = 0
    for order in _graph_matches(labels_a, graph_a, labels_b, graph_b, limit, counter):
        count += 1
        if positions_a is None or positions_b is None:
            return _Matches(order, 1, counter[0], True)
        fit = _misfit(positions_a, positions_b, order)
        if fit < best[0]:
            best = (fit, order)
    return _Matches(best[1], count, counter[0], not counter[1])


def _misfit(positions_a: Any, positions_b: Any, order: list[int]) -> float:
    """What is left after the best rotation lays ``a`` on ``b[order]``."""
    import numpy as _np

    a = _np.asarray(positions_a, dtype=float)
    a = a - a.mean(axis=0)
    target = _np.asarray(positions_b, dtype=float)[order]
    target = target - target.mean(axis=0)
    rotation = _superposing_rotation(a, target)
    return float(((a @ rotation.T - target) ** 2).sum())


def _terminal_groups(graph: list[set[int]], elements: list[int]) -> dict[int, list[int]]:
    """Interchangeable terminal atoms, by the atom they hang from: two or
    more atoms of one element bonded to nothing but the same atom (the F of
    a CF3, the methyls of a tBu, the O of a PO3 or SO3). Such atoms are
    always graph-equivalent, the classes RDKit's
    ``CanonicalRankAtoms(breakTies=False)`` gives them, so they are found
    directly."""
    found: dict[tuple[int, int], list[int]] = {}
    for atom, neighbours in enumerate(graph):
        if len(neighbours) == 1:
            (parent,) = neighbours
            found.setdefault((parent, elements[atom]), []).append(atom)
    groups: dict[int, list[int]] = {}
    for (parent, _), members in found.items():
        if len(members) > 1:
            groups.setdefault(parent, []).extend(sorted(members))
    return groups


class _Choice:
    """The match chosen, or why there is none."""

    def __init__(self, order: list[int] | None = None, *, count: int = 0,
                 steps: int = 0, complete: bool = True, why: str | None = None,
                 stereo: dict[str, Any] | None = None,
                 worst_bond: tuple[float, int, int] | None = None,
                 within_noise: list[int] | None = None,
                 undecided: list[dict[str, Any]] | None = None,
                 by_lengths: list[tuple[int, int, float]] | None = None,
                 flat: list[int] | None = None, sigma: float | None = None,
                 others_fit: bool = False, misfits: list[tuple] | None = None):
        self.order, self.count, self.steps, self.complete = order, count, steps, complete
        self.why, self.stereo, self.worst_bond = why, stereo, worst_bond
        self.within_noise = within_noise or []
        #: Centres whose arms the structure's lengths did not tell apart:
        #: each {"centres": [file atoms], "bonds": [(i, j, length)]}, the
        #: bonds as structure atoms (positions in its residue).
        self.undecided = undecided or []
        #: For a refusal: the structure bonds whose lengths ruled out a
        #: match that would agree, or None where no match of the graph
        #: agrees and the lengths took no part.
        self.by_lengths = by_lengths
        #: Stated centres the structure models flat in every match tied,
        #: not judged.
        self.flat = flat or []
        #: The noise on a bond the tie was judged with (nm).
        self.sigma = sigma
        #: Whether a match tied on the bond orders lays another isomer.
        self.others_fit = others_fit
        #: Bonds whose length fits another order than the file's, which no
        #: shape confirms (:func:`_other_order`): noted, never verified.
        self.misfits = misfits or []


def _expected_on(chemistry: "_Chemistry", order: Any) -> dict[tuple[int, int], float]:
    """The length each structure bond is expected to have under ``order``:
    its file bond's (the same for every bond of one symmetry class)."""
    import numpy as _np

    if not len(chemistry.bond_pairs):
        return {}
    on = _np.sort(_np.asarray(order)[chemistry.bond_pairs], axis=1)
    return {(int(a), int(b)): float(e) for (a, b), e in zip(on, chemistry.bond_lengths)}


def _tie_window(first: dict[tuple[int, int], float],
                second: dict[tuple[int, int], float],
                skip: set[tuple[int, int]] | None = None,
                sigma: float = BOND_NOISE_NM) -> float:
    """How far two matches' bond scores may differ (nm^2) and the deposit
    still not tell them apart: 2 |e - e'| sigma over each structure bond
    they expect differently, sigma the copy's noise
    (:data:`BOND_NOISE_NM` at least). A bond in ``skip`` (no length a bond
    of its elements has, or a metal's) is left out here as in the score."""
    window = 0.0
    for bond, e in first.items():
        if skip and bond in skip:
            continue
        window += 2.0 * abs(e - second.get(bond, e)) * sigma
    return window


def _is_metal(element: int) -> bool:
    """A metal, or any element whose covalent bonds the table does not
    hold (V, Cr, Cd, Hg, As, the lanthanides): its bonds are coordination."""
    return element in _METAL_RADIUS_NM or (element > 1 and element not in _COVALENT_RADIUS_NM)


def _metal_bonds(graph: list[set[int]], elements: list[int]) -> set[tuple[int, int]]:
    """The structure's bonds to a metal (:func:`_is_metal`)."""
    return {(i, j) for i, around in enumerate(graph) for j in around
            if j > i and (_is_metal(elements[i]) or _is_metal(elements[j]))}


def _broken_bonds(graph: list[set[int]], xyz: Any, elements: list[int],
                  short_only: bool = False) -> set[tuple[int, int]]:
    """A structure's bonds at no length a bond of their elements has: more
    than 0.01 nm shorter than a triple bond, or 0.02 nm longer than a
    single, by covalent radii (``short_only``: the short ones alone)."""
    import numpy as _np

    found = set()
    for i, around in enumerate(graph):
        for j in around:
            if j <= i:
                continue
            radius = {**_COVALENT_RADIUS_NM, **_METAL_RADIUS_NM}
            single = radius.get(elements[i], 0.15) + radius.get(elements[j], 0.15)
            length = float(_np.linalg.norm(xyz[i] - xyz[j]))
            if length < single - _ORDER_SHORTENING_NM[3.0] - 0.01 or (
                    not short_only and length > single + 0.02):
                found.add((i, j))
    return found


def _orders_on(chemistry: "_Chemistry", order: Any) -> dict[tuple[int, int], float]:
    """The file's bond order each structure bond is given under ``order``."""
    import numpy as _np

    if not len(chemistry.bond_pairs):
        return {}
    on = _np.sort(_np.asarray(order)[chemistry.bond_pairs], axis=1)
    return {(int(a), int(b)): float(o) for (a, b), o in zip(on, chemistry.bond_orders)}


def _laid_differently(chemistry: "_Chemistry", first: Any, second: Any) -> list[tuple[int, int]]:
    """The structure bonds two matches (orders) give different bond orders:
    where one lays the double bond the other lays a single."""
    a, b = _orders_on(chemistry, first), _orders_on(chemistry, second)
    return sorted(bond for bond, o in a.items() if o != b.get(bond, o))


def _choose_match(chemistry: "_Chemistry", elements_b: list[int], positions_b: Any,
                  graph_b: list[set[int]], *, limit: int = MATCH_STEP_LIMIT) -> _Choice:
    """Which structure atom each of the file's heavy atoms is.

    The heavy-atom graph says which correspondences are possible, and a
    structure whose graph is not the file's is another molecule. Of the
    graph's matches, the file's chemistry says which is right:

    1. those tied on the file's bond orders: each bond's length against one
       length for its symmetry class (the mean of the file's, or ideal
       ones for a flat file), two matches tied where their scores differ
       by no more than 2 |e - e'| sigma over the structure bonds they
       expect differently (:func:`_tie_window`), sigma the copy's own noise
       (:func:`_copy_noise`, :data:`BOND_NOISE_NM` at least). A bond too
       long for its elements, or a metal's, is left out of score and
       window; one too short leaves the swap that changes its order tied.
       A molecule's own symmetry ties exactly. This tells a C=O from a C-OH, and a vinyl
       from a propyl, wherever the deposit shows them, and it comes first:
       chosen after the stereochemistry, a wrong isomer was "explained" by
       a match that laid limonene's C=C on its C-C;
    2. of those, the ones agreeing with the stereochemistry the file
       states, at every centre and double bond the pose shows clear of
       noise. A stated centre laid on an atom the structure has flat
       (:data:`STEREO_FLAT_MAX`) disagrees only where another tied match
       lays it on a tetrahedral one; laid flat by all, it is not judged
       (``flat``). Where none agrees (``why`` "stereo", with ``stereo`` the
       nearest one's differences, and ``by_lengths`` the bonds whose
       lengths ruled out one that would) the structure holds another
       stereoisomer. Where a tied match turns a centre by laying the
       orders on other bonds, the deposit did not decide it
       (``undecided``), and the agreeing one is taken;
    3. the best on its bond score, within sigma squared for each bond
       laid a different order, then the 1-3 distances, within
       :data:`ANGLE_NOISE_NM` for each assigned differently, then the rigid
       fit. Then a bond every tied match lays one order on, decided by its
       length and its carbon's shape as another order, or a C=C moved beside
       a C-C in every tied match (:func:`_other_order`, ``why``
       "other_order"), is another molecule; a length no shape confirms is
       kept in ``misfits``, to be said.

    Bond lengths rank; they never refuse: a deposit's bonds lie up to 0.2 A
    from an ideal component's.
    Interchangeable terminal atoms (:func:`_terminal_groups`) are not
    searched: the rest is matched, and each group's atoms given to the
    structure's by nearest position once the file is laid on the atoms
    around it (Hungarian assignment).
    """
    import numpy as _np

    n = chemistry.n
    if n != len(elements_b):
        return _Choice(why="count")
    xyz_b = _np.asarray(positions_b, dtype=float)
    groups_a = chemistry.groups
    groups_b = _terminal_groups(graph_b, elements_b)
    tucked_a = {m for members in groups_a.values() for m in members}
    tucked_b = {m for members in groups_b.values() for m in members}
    core_a = [i for i in range(n) if i not in tucked_a]
    core_b = [i for i in range(n) if i not in tucked_b]
    if len(core_a) != len(core_b):
        return _Choice(why="graph")

    def labelled(core, graph, elements, groups):
        place = {atom: k for k, atom in enumerate(core)}
        labels, sub = [], []
        for atom in core:
            held = sorted((elements[m], 1) for m in groups.get(atom, []))
            summary: dict[int, int] = {}
            for element, _ in held:
                summary[element] = summary.get(element, 0) + 1
            labels.append((elements[atom], len(graph[atom]), tuple(sorted(summary.items()))))
            sub.append({place[j] for j in graph[atom] if j in place})
        return labels, sub

    labels_a, sub_a = labelled(core_a, chemistry.graph, chemistry.elements, groups_a)
    labels_b, sub_b = labelled(core_b, graph_b, elements_b, groups_b)
    if sorted(labels_a) != sorted(labels_b):
        return _Choice(why="graph")

    counter = [0, 0]
    ranked: list[dict[str, Any]] = []
    for core_order in _graph_matches(labels_a, sub_a, labels_b, sub_b, limit, counter):
        order = [0] * n
        for k, j in enumerate(core_order):
            order[core_a[k]] = core_b[j]
        within_noise = _assign_groups(order, chemistry, groups_b, xyz_b, elements_b)
        placed = xyz_b[order]
        bonds, across, worst = chemistry.departures(placed)
        ranked.append({"order": order, "bonds": bonds, "across": across, "worst": worst,
                       "stereo": chemistry.stereo_against(placed),
                       "fit": _misfit(chemistry.positions, xyz_b, order),
                       "within_noise": within_noise})
    if counter[1]:
        return _Choice(count=len(ranked), steps=counter[0], complete=False)
    if not ranked:
        return _Choice(why="graph", steps=counter[0])

    def differ(first: dict[str, Any], second: dict[str, Any], pairs: Any) -> int:
        """How many of ``pairs`` (file atoms) the two lay on different
        structure atoms."""
        if not len(pairs):
            return 0
        a = _np.sort(_np.asarray(first["order"])[pairs], axis=1)
        b = _np.sort(_np.asarray(second["order"])[pairs], axis=1)
        return int((a != b).any(axis=1).sum())

    def length_of(bond: tuple[int, int]) -> float:
        return float(_np.linalg.norm(xyz_b[bond[0]] - xyz_b[bond[1]]))

    # Bonds that carry no evidence of order: a length no bond of their
    # elements has (1T5E's C-O of 0.97 A), and a metal's coordination,
    # whose length is its oxidation and spin state's, not an order's.
    skip = _broken_bonds(graph_b, xyz_b, elements_b) | _metal_bonds(graph_b, elements_b)
    for r in ranked:
        r["on"] = _landing(chemistry, r["order"])
        r["score"] = float(sum(d for d, bond in zip(r["bonds"], r["on"]) if bond not in skip))
        r["expected"] = _expected_on(chemistry, r["order"])
    best = min(ranked, key=lambda r: r["score"])
    # The copy's own noise, over the bonds the matches tied at a good
    # deposit's noise lay alike: a ring 0.12 A off any order (5TED) is
    # this copy's noise, which no length difference inside it decides.
    first = [r for r in ranked if r["score"] - best["score"]
             <= _tie_window(r["expected"], best["expected"], skip) + 1e-12]
    sigma = _copy_noise(chemistry, first, best, xyz_b, skip)
    # The stated double bonds judged again with the copy's own noise.
    sigma_raw = max(sigma, _copy_noise(chemistry, first, best, xyz_b, skip, raw=True))
    if abs(sigma_raw - chemistry.noise_nm) > 1e-12:
        chemistry.noise_nm = sigma_raw
        for r in ranked:
            r["stereo"] = chemistry.stereo_against(xyz_b[r["order"]])
    # Another molecule is said only past the copy's noise as read, however
    # close to a good deposit's (a copy 0.06 A off in each atom read 0.045,
    # and laid a C-C at 1.30 A).
    sigma_read = max(sigma, _copy_noise(chemistry, first, best, xyz_b, skip, raw=True))
    # A bond's length weighed as evidence of another order, against the
    # copy's noise read without the bonds that fit one.
    sigma_note = _copy_noise(chemistry, first, best, xyz_b, skip, evidence=True)

    # 1. The file's bond orders: tied with the best wherever the deposit's
    # noise could make up the difference. A symmetry of the molecule lays
    # the same lengths on the same bonds, and ties exactly.
    # A bond shorter than any of its elements (1T5E's C3-O3, 0.97 A) is an
    # atom laid where no bond puts it: a swap that gives it another order
    # is not told by the deposit at all. A bond too long only loses its own
    # term, the others still deciding (a CH2OH arm stretched to 1.66 A).
    squeezed = _broken_bonds(graph_b, xyz_b, elements_b, short_only=True)

    def tie(r: dict[str, Any]) -> bool:
        if squeezed and squeezed & set(_laid_differently(chemistry, r["order"], best["order"])):
            return True
        return (r["score"] - best["score"]
                <= _tie_window(r["expected"], best["expected"], skip, sigma) + 1e-12)

    tied = [r for r in ranked if tie(r)]

    # 2. Of those, the ones that agree with the stereochemistry the file
    # states. A structure atom the structure models flat holds against a
    # match only the stated centres it lays on such atoms beyond the
    # fewest any tied match does: a carbinol laid on the deposit's
    # carbonyl carbon where another match lays it on a tetrahedral atom.
    fewest_flat = min(len(r["stereo"]["flat"]) for r in tied)
    # Likewise a stated double bond laid on a bond the structure lays single,
    # beyond the fewest any tied match does: a 2-hexene's C=C laid on its
    # CH2-CH2 where another match lays it on the deposit's stretched C=C.
    fewest_single = min(len(r["stereo"].get("single", [])) for r in tied)

    def against(r: dict[str, Any]) -> list[int]:
        extra = r["stereo"]["flat"] if len(r["stereo"]["flat"]) > fewest_flat else []
        single = (r["stereo"].get("single", [])
                  if len(r["stereo"].get("single", [])) > fewest_single else [])
        return sorted(set(r["stereo"]["differ"]) | set(extra) | set(single))

    agreeing = [r for r in tied if not against(r)]
    if not agreeing:
        nearest = min(tied, key=lambda r: (len(against(r)), r["fit"]))
        said = [r for r in [nearest["stereo"]["said_differ"]] if r]
        flat_held = [k for k in against(nearest) if k in nearest["stereo"]["flat"]]
        said += [f"the stereocentre {chemistry.name(k)} lies on an atom the structure models "
                 "flat, where another match lays it on a tetrahedral one" for k in flat_held]
        single_held = [k for k in against(nearest) if k in nearest["stereo"].get("single", [])
                       and k not in nearest["stereo"]["differ"]]
        said += [f"the double bond {chemistry.name(u)}={chemistry.name(v)} lies on a bond the "
                 "structure lays single, where another match lays it on a double bond"
                 for _, u, v, _, _ in chemistry.double_bonds if u in single_held and v in single_held]
        stereo = {**nearest["stereo"], "differ": against(nearest), "said_differ": "; ".join(said),
                  "single_held": single_held}
        # Did the lengths take part? A match that would agree, left out of
        # the tie by the bonds it lays differently.
        others = [r for r in ranked if not r["stereo"]["differ"]
                  and len(r["stereo"]["flat"]) <= fewest_flat
                  and len(r["stereo"].get("single", [])) <= fewest_single]
        by_lengths = None
        if others:
            closest = min(others, key=lambda r: r["score"])
            by_lengths = [(a, b, length_of((a, b)), o)
                          for a, b, o in _order_changes(chemistry, closest, nearest)]
        return _Choice(nearest["order"], count=len(ranked), steps=counter[0], why="stereo",
                       stereo=stereo, worst_bond=nearest["worst"], by_lengths=by_lengths,
                       sigma=sigma, flat=flat_held)
    # 3. Among those, the bonds still say what they can: the best on its
    # bond score, matches within a bond's noise squared for each bond laid
    # differently kept (a porphine's NH pair, which the tie leaves open);
    # then the 1-3 distances, then the fit.
    lead = min(agreeing, key=lambda r: r["score"])
    agreeing = [r for r in agreeing
                if r["score"] - lead["score"]
                <= len(_laid_differently(chemistry, r["order"], lead["order"]))
                * sigma ** 2 + 1e-12]
    floor = min(agreeing, key=lambda r: r["across"].sum())
    close = [r for r in agreeing
             if r["across"].sum() - floor["across"].sum()
             <= max(1, differ(r, floor, chemistry.angle_pairs)) * ANGLE_NOISE_NM ** 2]
    chosen = min(close, key=lambda r: r["fit"])
    # Where a tied match lays the bond orders on other bonds and so turns a
    # centre, the structure did not decide which arm carries the double
    # bond: placed as the file states, and said so, with the bonds whose
    # order differs (a carboxylate's or phosphate's oxygens, given by
    # ``assigned_within_noise``, left out) and the order the file lays on
    # each.
    undecided: list[dict[str, Any]] = []
    for r in tied:
        turned = against(r)
        bonds = [(a, b, length_of((a, b)), o) for a, b, o in _order_changes(chemistry, chosen, r)]
        if turned and bonds:
            # Whether the lengths lean to the isomer this match lays, within
            # the copy's noise.
            entry = {"centres": turned, "bonds": bonds,
                     "lean": r["score"] < chosen["score"] - 1e-12}
            if entry not in undecided:
                undecided.append(entry)
    flat = list(chosen["stereo"]["flat"])
    # A bond the deposit decides as another order than the file's, laid
    # alike by every tied match: another molecule.
    other, misfits = _other_order(chemistry, chosen, tied, xyz_b, skip, sigma_note, sigma_read,
                                  turns=any(against(r) for r in tied))
    if other:
        return _Choice(chosen["order"], count=len(ranked), steps=counter[0], why="other_order",
                       stereo=chosen["stereo"], worst_bond=chosen["worst"], sigma=sigma,
                       by_lengths=other, flat=flat)
    within_noise = (_assign_groups(list(chosen["order"]), chemistry, groups_b, xyz_b,
                                   elements_b, sigma) if sigma > BOND_NOISE_NM
                    else chosen["within_noise"])
    # Whether a tied match lays another isomer: the other fits too.
    others_fit = any(against(r) for r in tied)
    return _Choice(chosen["order"], count=len(ranked), steps=counter[0],
                   stereo=chosen["stereo"], worst_bond=chosen["worst"],
                   within_noise=within_noise, undecided=undecided, flat=flat,
                   sigma=sigma, others_fit=others_fit, misfits=misfits)


def _landing(chemistry: "_Chemistry", order: Any) -> list[tuple[int, int]]:
    """The structure bond each file bond lands on under ``order``."""
    import numpy as _np

    if not len(chemistry.bond_pairs):
        return []
    on = _np.sort(_np.asarray(order)[chemistry.bond_pairs], axis=1)
    return [(int(a), int(b)) for a, b in on]


def _copy_noise(chemistry: "_Chemistry", ranked: list[dict[str, Any]], best: dict[str, Any],
                xyz_b: Any, skip: set[tuple[int, int]], raw: bool = False,
                evidence: bool = False) -> float:
    """This copy's own noise on a bond (nm): the RMS departure of its bonds
    from typical lengths for their elements, order and hybridisation
    (:func:`_typical_bond_nm`), the single largest left out, never under
    :data:`BOND_NOISE_NM`. Read only over bonds that cannot decide: those
    every match in ``ranked`` (the tie at a good deposit's noise) lays the
    same order on, and not a terminal group's, so the evidence does not set
    its own noise, nor a conjugated one's, whose length is its
    conjugation's; and against typical lengths, not the file's, so the
    program that wrote the file does not move it. A badly modelled copy
    (5TED's shikimate, its ring 0.05 to 0.16 A off any order) is held to its
    own noise, not a good deposit's."""
    import numpy as _np

    orders = [_orders_on(chemistry, r["order"]) for r in ranked]
    offs, conjugated, held_back, conjugated_back = [], [], [], []
    for p, bond in enumerate(best["on"]):
        i, j = (int(v) for v in chemistry.bond_pairs[p])
        if bond in skip or i in chemistry.tucked or j in chemistry.tucked:
            continue
        o = chemistry.bond_orders[p]
        if any(found.get(bond, o) != o for found in orders):
            continue
        length = float(_np.linalg.norm(xyz_b[bond[0]] - xyz_b[bond[1]]))
        if chemistry.hybrid[i] in ("sp2", "sp") and chemistry.hybrid[j] in ("sp2", "sp"):
            # A conjugated bond's length is its conjugation's, not its
            # order's (a porphyrin's all lie between C-C and C=C): read
            # against the nearest of its single, 1.5 and double lengths (a
            # pyridone's ring alternates where the file has it aromatic), and
            # only where the others are too few.
            near = [t for t in (_typical_bond_nm(chemistry, p, order) for order in (1.0, 1.5, 2.0))
                    if t is not None]
            if near:
                conjugated.append(min((length - t for t in near), key=abs))
                conjugated_back.append((p, length))
            continue
        typical = _typical_bond_nm(chemistry, p)
        if typical is None:
            continue
        if evidence and _fits_another_order(chemistry, p, length, BOND_NOISE_NM):
            # For weighing a bond's length as evidence: the bonds that fit
            # another order left out, so they do not hide one another
            # (DHF's file on folate's 4PSS, C9-C10 and C10-N11 both short).
            continue
        offs.append(length - typical)
        held_back.append((p, length))
    if raw:
        # What another molecule is held to, from every bond there is.
        pass
    elif len(offs) >= _NOISE_BONDS_MIN:
        # Enough without them; under four, all of them, a porphine's two
        # dozen, not the first few.
        conjugated, conjugated_back = [], []
    if len(offs) + len(conjugated) >= 2:
        # A bond clear of its order by twice a first, robust read is the
        # evidence of another molecule (benzaldehyde's C=O under benzyl
        # alcohol's file), and does not set the noise it is held to. A
        # conjugated bond, and any bond of the read another molecule is held
        # to, is left out past once that read: a C=C laid on a C-C counted
        # as noise raised the read that then hid it (alpha-terpinene's file
        # on gamma-terpinene's copy, 0.02 A off in each coordinate, read
        # 0.064 A against the right file's 0.025).
        every = _np.asarray(offs + conjugated)
        first = 1.4826 * float(_np.median(_np.abs(every - _np.median(every))))
        once = max(BOND_NOISE_NM, first)
        held = once if raw else 2.0 * once
        offs = [d for d, (p, length) in zip(offs, held_back)
                if not _fits_another_order(chemistry, p, length, held)]
        conjugated = [d for d, (p, length) in zip(conjugated, conjugated_back)
                      if not _fits_another_order(chemistry, p, length, once)]
    mostly_conjugated = len(conjugated) > len(offs)
    offs += conjugated
    if len(offs) < 2:
        # No spread to read: a good deposit's noise.
        return BOND_NOISE_NM
    few = len(offs) < _NOISE_BONDS_MIN
    # Their spread about the median departure, so a copy stretched as a
    # whole (5TG4's A303, every bond 0.05 A long) is not its noise: as an
    # RMS over n - 1 for the ties, robustly (1.4826 times the median
    # absolute deviation) for what another molecule is held to, the bond
    # that shows it never its own noise.
    spread = _np.asarray(offs) - float(_np.median(offs))
    robust = 1.4826 * float(_np.median(_np.abs(spread)))
    # An RMS over n - 1, from eight bonds of those within 3.5 robust spreads:
    # a bond a depositor left far off, or whose neighbour was (a ring C-C
    # pulled to 1.72 A drags the two beside it), is not the copy's spread.
    # From fewer, a robust spread is itself too rough to trim by.
    kept = (spread[_np.abs(spread) <= 3.5 * robust]
            if robust > 0.0 and len(spread) >= _NOISE_BONDS_TO_TRIM else spread)
    rms = (float(_np.sqrt(_np.sum(_np.square(kept)) / (len(kept) - 1)))
           if len(kept) >= 2 else robust)
    if evidence:
        rms = robust
    elif raw:
        # What another molecule is held to: the larger of the two reads.
        rms = max(rms, robust)
    if raw or evidence or few:
        # As read: what a refusal of another molecule is held to, and a
        # small copy's whole say (glyceraldehyde's three alike bonds).
        return max(BOND_NOISE_NM, rms)
    # Against typical lengths, whose own offsets a good deposit's bonds
    # carry (0.02 to 0.04 A), a spread within twice a good deposit's noise
    # is not told from it: such a copy keeps a good deposit's noise, and a
    # copy clearly worse (5TED's 0.12 A) is held to its own.
    # Read mostly from conjugated bonds, whose typical lengths are their
    # conjugation's guess, a copy must read half as much again worse (a
    # porphine 0.03 A off in each atom read 0.069 A, and lost its NH pair).
    worse = _NOISE_CLEARLY_WORSE * (1.5 if mostly_conjugated else 1.0)
    return rms if rms > worse * BOND_NOISE_NM else BOND_NOISE_NM


def _can_be_triple(chemistry: "_Chemistry", i: int, j: int) -> bool:
    """Whether a bond between heavy atoms i and j can be a triple one in a
    ligand: C#C or C#N, not an aldehyde's short C=O (5XU9's 1.13 A)."""
    return {chemistry.elements[i], chemistry.elements[j]} <= {6, 7} and \
        all(len(chemistry.graph[k]) <= 2 for k in (i, j))


#: The most bond orders a neutral heavy atom of each element holds to heavy
#: atoms, for :func:`_can_take` (carbon and nitrogen not held to one).
_VALENCE_MAX = {8: 2, 16: 6, 15: 5, 9: 1, 17: 1, 35: 1, 53: 1}


def _can_take(chemistry: "_Chemistry", i: int, j: int, other: float, order: Any,
              xyz_b: Any) -> bool:
    """Whether the file's atoms i and j can be joined by a bond of order
    ``other`` in some form of the molecule, so that a length fitting it is
    worth saying. A lower order always can. A higher one cannot where an
    atom would hold more than its valence to heavy atoms (an ether's or a
    bridging phosphate's O, a sulfonyl's S), at an O or N bridging two
    phosphorus atoms (a pyrophosphate's or an imidodiphosphate's), nor at a
    stated stereocentre the deposit holds tetrahedral (1SQN's C3 and C8),
    nor at a carboxyl's carbon, whose bonds to its oxygens are resonance. A
    carbon's other bonds move with the other molecule's, and are not held to
    the file's."""
    if other < chemistry.order_of(i, j):
        return True
    for k in (i, j):
        element = chemistry.elements[k]
        held = sum(chemistry.order_of(k, m)
                   for m in chemistry.graph[k])
        charge = chemistry.charge(k)
        if element in _VALENCE_MAX and held + 1.0 > _VALENCE_MAX[element] + max(charge, 0):
            return False
        if element in (7, 8) and sum(1 for m in chemistry.graph[k]
                                     if chemistry.elements[m] == 15) >= 2:
            return False
        if element == 6 and any(m in chemistry.tucked for m in chemistry.graph[k]):
            # A carboxyl's carbon: its bonds to its oxygens are resonance,
            # never another order, and hold three.
            fixed = sum(chemistry.order_of(k, m) for m in chemistry.graph[k]
                        if m in chemistry.tucked)
            if fixed + (len(chemistry.graph[k]) - sum(1 for m in chemistry.graph[k]
                                                       if m in chemistry.tucked)) + 1.0 > 4.0:
                return False
    for k, three, _ in chemistry.centres:
        if k in (i, j) and abs(_volume(xyz_b, order[k], tuple(order[m] for m in three))) \
                >= STEREO_VOLUME_MIN:
            return False
    return True


def _fits_another_order(chemistry: "_Chemistry", p: int, length: float, sigma: float) -> bool:
    """Whether the file's bond ``p``, deposited ``length`` long, fits the
    order one above or below its own better by more than ``sigma`` could
    make up, and lies within :data:`OTHER_ORDER_REACH_NM` of that order's
    typical length (as :func:`_other_order` reads it)."""
    i, j = (int(v) for v in chemistry.bond_pairs[p])
    o = chemistry.bond_orders[p]
    if o not in (1.0, 2.0, 3.0):
        return False
    e = _typical_bond_nm(chemistry, p)
    if e is None:
        return False
    for other in (o - 1.0, o + 1.0):
        if other not in (1.0, 2.0, 3.0) or (other == 3.0 and not _can_be_triple(chemistry, i, j)):
            continue
        reshaped = {k: ("sp3" if other < o else "sp2") for k in (i, j)
                    if chemistry.elements[k] == 6 and chemistry.hybrid[k] in ("sp2", "sp3")}
        alternative = _typical_bond_nm(chemistry, p, other, reshaped)
        if alternative is None or abs(length - alternative) > OTHER_ORDER_REACH_NM:
            continue
        if (length - e) ** 2 - (length - alternative) ** 2 > 2.0 * abs(e - alternative) * sigma:
            return True
    return False


#: The fewest bonds a copy's own noise is read from; under it, a good
#: deposit's (:data:`BOND_NOISE_NM`).
_NOISE_BONDS_MIN = 4

#: The fewest bonds whose far-off departures are trimmed from the read.
_NOISE_BONDS_TO_TRIM = 8

#: How many times a good deposit's noise a copy's own must be read at to
#: be used (:func:`_copy_noise`).
_NOISE_CLEARLY_WORSE = 2.0


#: Typical bond lengths (nm) by element pair, order and the hybridisation
#: of the carbon or heteroatom on each side ("3" sp3, "2" sp2 or aromatic,
#: "1" sp): averages over small-molecule crystal structures (Allen et al.
#: 1987), for reading a copy's own noise.
_TYPICAL_NM = {
    ("C", "C", 1.0, "33"): 0.153, ("C", "C", 1.0, "23"): 0.150, ("C", "C", 1.0, "22"): 0.147,
    ("C", "C", 1.0, "13"): 0.146, ("C", "C", 1.0, "12"): 0.143, ("C", "C", 1.0, "11"): 0.138,
    ("C", "C", 2.0, None): 0.134, ("C", "C", 3.0, None): 0.120, ("C", "C", 1.5, None): 0.139,
    ("C", "N", 1.0, "33"): 0.147, ("C", "N", 1.0, "32"): 0.146, ("C", "N", 1.0, "23"): 0.143,
    ("C", "N", 1.0, "22"): 0.136, ("C", "N", 2.0, None): 0.128, ("C", "N", 3.0, None): 0.114,
    ("C", "N", 1.5, None): 0.134,
    ("C", "O", 1.0, "3"): 0.143, ("C", "O", 1.0, "2"): 0.134, ("C", "O", 2.0, None): 0.121,
    ("C", "O", 1.5, None): 0.137,
    ("C", "S", 1.0, "3"): 0.182, ("C", "S", 1.0, "2"): 0.176, ("C", "S", 2.0, None): 0.167,
    ("C", "S", 1.5, None): 0.171,
    ("C", "F", 1.0, None): 0.135, ("C", "Cl", 1.0, None): 0.175, ("C", "Br", 1.0, None): 0.190,
    ("C", "I", 1.0, None): 0.210, ("C", "P", 1.0, None): 0.182, ("C", "Se", 1.0, None): 0.195,
    ("N", "N", 1.0, None): 0.142, ("N", "N", 2.0, None): 0.125, ("N", "N", 1.5, None): 0.134,
    ("N", "O", 1.0, None): 0.140, ("N", "O", 2.0, None): 0.122, ("N", "O", 1.5, None): 0.124,
    ("O", "P", 1.0, None): 0.159, ("O", "P", 2.0, None): 0.149, ("O", "S", 1.0, None): 0.157,
    ("O", "S", 2.0, None): 0.143, ("N", "S", 1.0, None): 0.163, ("N", "P", 1.0, None): 0.166,
    ("S", "S", 1.0, None): 0.204, ("O", "O", 1.0, None): 0.148, ("N", "S", 2.0, None): 0.154,
}

_SYMBOLS = {1: "H", 5: "B", 6: "C", 7: "N", 8: "O", 9: "F", 14: "Si", 15: "P", 16: "S",
            17: "Cl", 34: "Se", 35: "Br", 53: "I"}


def _typical_bond_nm(chemistry: "_Chemistry", p: int, order: float | None = None,
                     hybrid: dict[int, str] | None = None) -> float | None:
    """The typical length of the file's bond ``p`` for its elements, order
    and hybridisation (:data:`_TYPICAL_NM`), or None where the table has
    none."""
    i, j = (int(v) for v in chemistry.bond_pairs[p])
    ends = sorted(((_SYMBOLS.get(chemistry.elements[k], "?"), k) for k in (i, j)),
                  key=lambda end: (end[0] != "C", end[0]))
    (first, a), (second, b) = ends
    hyb = {"sp3": "3", "sp2": "2", "sp": "1"}
    o = chemistry.bond_orders[p] if order is None else order
    shapes = {**{k: chemistry.hybrid[k] for k in (a, b)}, **(hybrid or {})}
    pair = hyb.get(shapes[a], "3") + hyb.get(shapes[b], "3")
    keys = [(first, second, o, pair)]
    if first == second:
        keys.append((first, second, o, pair[::-1]))
    keys += [(first, second, o, pair[0]), (first, second, o, None)]
    for key in keys:
        if key in _TYPICAL_NM:
            return _TYPICAL_NM[key]
    return None


def _order_changes(chemistry: "_Chemistry", first: dict[str, Any],
                   second: dict[str, Any]) -> list[tuple[int, int, float]]:
    """The structure bonds two matches give different orders, but for the
    bonds to interchangeable terminal atoms (a carboxylate's or a
    phosphate's oxygens), with the order the first lays on each."""
    out = []
    by_bond = {bond: (p, o) for p, (bond, o) in enumerate(zip(first["on"], chemistry.bond_orders))}
    theirs = dict(zip(second["on"], chemistry.bond_orders))
    for bond, (p, o) in sorted(by_bond.items()):
        i, j = (int(v) for v in chemistry.bond_pairs[p])
        if i in chemistry.tucked or j in chemistry.tucked:
            continue
        if o != theirs.get(bond, o):
            out.append((bond[0], bond[1], o))
    return out


def _angle_deg(xyz: Any, centre: int, first: int, second: int) -> float:
    """The angle first-centre-second (degrees)."""
    import numpy as _np

    a, b = _unit(xyz[first] - xyz[centre]), _unit(xyz[second] - xyz[centre])
    if a is None or b is None:
        return 0.0
    return float(_np.degrees(_np.arccos(_np.clip(_np.dot(a, b), -1.0, 1.0))))


#: The narrowest angle (degrees) an sp2 carbon with two heavy neighbours
#: takes in a deposit outside rings of five or fewer: the 1st percentile of
#: 340 such angles (77 copies of 41 components) in the clean deposits
#: :data:`SP3_ANGLE_MAX_DEG` was read from, whose median is 120.5.
SP2_ANGLE_MIN_DEG = 116.9

#: Another molecule is refused only on a copy whose noise reads no more
#: than this (nm): the real deposits refused so read 0.025 to 0.032 A, and
#: a clean MMFF one up to 0.044 against typical lengths.
OTHER_MOLECULE_NOISE_MAX = 0.0046

#: A length is read as another order's only within this of that order's
#: typical length (nm).
OTHER_ORDER_REACH_NM = 0.01

#: The widest angle (degrees) an sp3 CH2 between two heavy atoms takes in a
#: deposit: the 99th percentile of 391 such angles in clean deposits
#: restrained to their CCD components (74 copies of 44 components, their
#: noise read at a good deposit's and no bond 0.08 A off the file's), whose
#: median is 111.1. MMFF's own hexane lays 113.5.
SP3_ANGLE_MAX_DEG = 118.3

#: A carbon with two heavy neighbours confirms tetrahedral only under this
#: angle (degrees), the lower of :data:`SP2_ANGLE_MIN_DEG` and 114, and flat
#: only over :data:`SP3_ANGLE_MAX_DEG`: between, where sp2 and sp3 carbons
#: both lie, its shape decides nothing and is said (:func:`_carbon_shape`).
TETRAHEDRAL_ANGLE_UNDER_DEG = min(SP2_ANGLE_MIN_DEG, 114.0)


#: The tetrahedral shape a lone length's note needs at a carbon the file has
#: sp2, past what right ligands' sp2 carbons take in the clean deposits
#: :data:`SP3_ANGLE_MAX_DEG` was read from: three heavy neighbours spanning
#: at least this volume (of 1,530 such carbons, 1 in 200 span more than 0.39
#: and none more than 0.54 but those of a deposit modelled as another
#: molecule, 6V87's).
LEAN_VOLUME_MIN = 0.55

#: And the flat shape a lone length's note needs at a carbon the file has
#: sp3 with three heavy neighbours: half :data:`STEREO_FLAT_MAX`, under the
#: least of 715 such carbons in those deposits (0.13) by more than its noise.
LEAN_FLAT_MAX = 0.06


def _carbon_shape(chemistry: "_Chemistry", k: int, order: Any, xyz_b: Any,
                  any_element: bool = False, strict: bool = False) -> str | None:
    """The shape the deposit gives the file's heavy atom k, laid by
    ``order``: "flat", "tetrahedral" or None where it says neither. Three
    heavy neighbours by their volume (:data:`STEREO_FLAT_MAX`,
    :data:`STEREO_VOLUME_MIN`); two by the angle between them, under
    :data:`TETRAHEDRAL_ANGLE_UNDER_DEG` or over :data:`SP3_ANGLE_MAX_DEG`,
    outside rings of five atoms or fewer. A carbon's, or with
    ``any_element`` any atom's. ``strict``: past what a right ligand's
    carbons take with three neighbours (:data:`LEAN_VOLUME_MIN`,
    :data:`LEAN_FLAT_MAX`), for a note on a length that alone does not
    decide."""
    if chemistry.elements[k] != 6 and not any_element:
        return None
    around = sorted(chemistry.graph[k])
    if len(around) == 3:
        volume = abs(_volume(xyz_b, order[k], tuple(order[m] for m in around)))
        return ("flat" if volume < (LEAN_FLAT_MAX if strict else STEREO_FLAT_MAX)
                else "tetrahedral" if volume >= (LEAN_VOLUME_MIN if strict else STEREO_VOLUME_MIN)
                else None)
    if len(around) != 2:
        return None
    if chemistry.mol is not None and any(chemistry.mol.GetRingInfo().IsAtomInRingOfSize(k, size)
                                         for size in (3, 4, 5)):
        return None
    angle = _angle_deg(xyz_b, order[k], order[around[0]], order[around[1]])
    if angle > SP3_ANGLE_MAX_DEG:
        return "flat"
    if angle < TETRAHEDRAL_ANGLE_UNDER_DEG:
        return "tetrahedral"
    return None


def _angle_in_the_band(chemistry: "_Chemistry", k: int, order: Any, xyz_b: Any) -> float | None:
    """The deposit's angle at the file's carbon k with two heavy neighbours,
    outside a ring of five or fewer, where it lies between
    :data:`TETRAHEDRAL_ANGLE_UNDER_DEG` and :data:`SP3_ANGLE_MAX_DEG`, the
    band its shape decides nothing in; else None."""
    around = sorted(chemistry.graph[k])
    if chemistry.elements[k] != 6 or len(around) != 2:
        return None
    if chemistry.mol is not None and any(chemistry.mol.GetRingInfo().IsAtomInRingOfSize(k, size)
                                         for size in (3, 4, 5)):
        return None
    angle = _angle_deg(xyz_b, order[k], order[around[0]], order[around[1]])
    return angle if TETRAHEDRAL_ANGLE_UNDER_DEG <= angle <= SP3_ANGLE_MAX_DEG else None


def _form_said(chemistry: "_Chemistry", i: int, j: int, o: float, other: float, order: Any,
               xyz_b: Any) -> str | None:
    """Which form of the file's molecule a C-N or C-O bond laid at the
    length of order ``other`` points to: "redox" for a C-N whose carbon
    gains or loses its hydrogens with it (an amine against an imine: the
    file's carbon sp3, or the deposit's tetrahedral, beside an N that can be
    an imine's), "tautomer" for a terminal C=O or C-OH at a carbon sp2 in
    the file and not tetrahedral in the deposit, beside an atom that can give or take the hydrogen (keto and enol,
    pyridone and hydroxypyridine), and for a C-N whose carbon's bond to
    another N or O takes the order it gives up (an amide and its imidic
    acid, an amidine's two forms), else None."""
    pair = {chemistry.elements[i], chemistry.elements[j]}
    if 6 not in pair or o not in (1.0, 2.0):
        return None
    c = i if chemistry.elements[i] == 6 else j
    x = j if c == i else i
    if pair == {6}:
        return None
    shape = _carbon_shape(chemistry, c, order, xyz_b)
    if pair == {6, 7}:
        if len(chemistry.graph[x]) <= 2 and (
                (other > o and chemistry.hybrid[c] == "sp3")
                or (other < o and shape == "tetrahedral")):
            return "redox"
        # An amide and its imidic acid, an amidine's or a guanidine's forms
        # (clonidine's): the carbon's bond to another N or O takes the order
        # the C-N gives up, the carbon staying sp2.
        if chemistry.hybrid[c] == "sp2" and shape != "tetrahedral" and any(
                chemistry.order_of(c, m) == (1.0 if other < o else 2.0)
                and chemistry.elements[m] in (7, 8)
                for m in chemistry.graph[c] if m != x):
            return "tautomer"
        return None
    if pair != {6, 8} or len(chemistry.graph[x]) != 1 or chemistry.hybrid[c] != "sp2" \
            or shape == "tetrahedral" or chemistry.mol is None:
        return None
    beside = [m for m in chemistry.graph[c] if m != x]
    if o == 2.0 and any(chemistry.order_of(c, m) == 1.0
                        and chemistry.mol.GetAtomWithIdx(m).GetTotalNumHs() > 0
                        for m in beside):
        return "tautomer"
    if o == 1.0 and any(chemistry.order_of(c, m) in (1.5, 2.0) for m in beside):
        return "tautomer"
    return None


#: A carbon a double bond makes flat, with two heavy neighbours outside a
#: small ring, is said where the deposit bends it under this angle
#: (degrees), whatever the bond's length.
SP2_BENT_MAX_DEG = TETRAHEDRAL_ANGLE_UNDER_DEG


def _bent_from_its_order(chemistry: "_Chemistry", i: int, j: int, o: float, other: float,
                         order: Any, xyz_b: Any) -> tuple[int, str] | None:
    """For the file's double bond i=j read against a single: the structure
    atom of a carbon of it, sp2 in the file with two heavy neighbours outside
    a ring of five or fewer, that the deposit bends under
    :data:`SP2_BENT_MAX_DEG`, with how it is said; else None. Said only
    where the bond is longer than a double's by more than the copy's noise
    (:func:`_order_misfits`): 1O61's PLP C4A at 113 degrees on a 1.23 A C=O
    is not."""
    if o != 2.0 or other != 1.0:
        return None
    for k in (i, j):
        around = sorted(chemistry.graph[k])
        if chemistry.elements[k] != 6 or chemistry.hybrid[k] != "sp2" or len(around) != 2:
            continue
        if chemistry.mol is not None and any(chemistry.mol.GetRingInfo().IsAtomInRingOfSize(k, n)
                                             for n in (3, 4, 5)):
            continue
        angle = _angle_deg(xyz_b, order[k], order[around[0]], order[around[1]])
        if angle < SP2_BENT_MAX_DEG:
            return order[k], f"tetrahedral ({angle:.0f} degrees)"
    return None


def _conjugated(chemistry: "_Chemistry", i: int, j: int) -> bool:
    """Whether the file's bond i-j is conjugated: aromatic, or between two
    sp2 or sp carbons one of which holds another double, triple or aromatic
    bond (an enone's C3-C4, a diene's), so that its length is its
    conjugation's. A lone C=C (1-hexene's) is not."""
    o = chemistry.order_of(i, j)
    if o == 1.5:
        return True
    if not all(chemistry.elements[k] == 6 and chemistry.hybrid[k] in ("sp2", "sp")
               for k in (i, j)):
        return False
    return any(chemistry.order_of(k, m) > 1.0 for k in (i, j)
               for m in chemistry.graph[k] if m not in (i, j))


def _laid_as_single(chemistry: "_Chemistry", u: int, v: int, xyz: Any, order: Any) -> bool:
    """Whether the deposit, laid by ``order``, lays the file's double bond u=v
    truly single, so that its E or Z is not the deposit's to say: a carbon of
    it with two heavy neighbours bent past the band sp2 and sp3 carbons share
    (:func:`_carbon_shape`), a CH2 in the single bond's molecule, with the
    bond's length nearer an sp3 single's than a double's (acetylacetone's
    enol file on its keto deposit, its C7 a CH2), or the bond a tautomer's, its length past the midpoint to a
    conjugated single (about 1.47 A) beside a C-OH or C-N of its carbon laid
    at a double bond's length. A shape alone never does it (1KT3's C9 at a
    volume of 0.26 on a planar 1.38 A C9=C10, 6V87's C7 and C8 at 0.72 on a
    1.49 A C7=C8, whose E or Z is held within noise), nor a length alone at
    flat carbons (a stilbene's C=C stretched to 1.48 A)."""
    import numpy as _np

    p = chemistry.position_of(u, v)
    double = _typical_bond_nm(chemistry, p, 2.0)
    if double is None:
        return False
    length = float(_np.linalg.norm(xyz[order[u]] - xyz[order[v]]))
    if any(len(chemistry.graph[k]) == 2 and _carbon_shape(chemistry, k, order, xyz) == "tetrahedral"
           for k in (u, v)):
        single = _typical_bond_nm(chemistry, p, 1.0, {k: "sp3" for k in (u, v)
                                                      if chemistry.elements[k] == 6})
        # Past the midpoint by twice the copy's noise (as read for another
        # molecule, :func:`_copy_noise`), so noise alone does not make a
        # bent planar C=C single (cis-cinnamic acid's file on a trans copy
        # 0.05 A off in each coordinate).
        if single is not None and length - 0.5 * (single + double) > 2.0 * chemistry.noise_nm:
            return True
    single = _typical_bond_nm(chemistry, p, 1.0)
    if single is None or abs(length - single) >= abs(length - double):
        return False
    return any(_laid_at_its_double(chemistry, k, m, xyz, order)
               for k in (u, v) for m in chemistry.graph[k] if m not in (u, v))


#: A ring of five or fewer atoms is laid puckered, not aromatic, where one
#: of its torsions is over this (degrees): an aromatic ring's lie within a
#: few degrees of 0, a saturated five-ring's reach 30 to 45.
RING_PUCKER_MIN_DEG = 20.0


def _in_a_small_ring(chemistry: "_Chemistry", k: int) -> bool:
    """Whether the file's atom k is in a ring of five atoms or fewer."""
    return chemistry.mol is not None and any(
        chemistry.mol.GetRingInfo().IsAtomInRingOfSize(k, size) for size in (3, 4, 5))


def _ring_torsion(chemistry: "_Chemistry", k: int, order: Any, xyz: Any) -> float | None:
    """The largest torsion (degrees, 0 for a flat ring) the deposit gives a
    ring of four or five atoms holding the file's atom k, or None where
    there is none (a three-ring has no torsion to read)."""
    import numpy as _np

    if chemistry.mol is None:
        return None
    largest = None
    for ring in chemistry.mol.GetRingInfo().AtomRings():
        if k not in ring or not 4 <= len(ring) <= 5:
            continue
        n = len(ring)
        for t in range(n):
            a, b, c, d = (order[ring[(t + s) % n]] for s in range(4))
            # The dihedral a-b-c-d: 0 where a and d lie on one side of b-c.
            b0, b1, b2 = xyz[b] - xyz[a], xyz[c] - xyz[b], xyz[d] - xyz[c]
            n1, n2 = _np.cross(b0, b1), _np.cross(b1, b2)
            norms = float(_np.linalg.norm(n1) * _np.linalg.norm(n2))
            if norms <= 0.0:
                continue
            cosine = max(-1.0, min(1.0, float(_np.dot(n1, n2)) / norms))
            angle = float(_np.degrees(_np.arccos(cosine)))
            largest = angle if largest is None else max(largest, angle)
    return largest


def _puckered(chemistry: "_Chemistry", k: int, order: Any, xyz: Any) -> bool:
    """Whether a ring of four or five holding the file's atom k is laid by
    the deposit puckered past :data:`RING_PUCKER_MIN_DEG`, as a saturated
    ring is and an aromatic one is not."""
    torsion = _ring_torsion(chemistry, k, order, xyz)
    return torsion is not None and torsion > RING_PUCKER_MIN_DEG


#: A carbon of a five-ring with two heavy neighbours is laid as a reduced
#: ring's CH2 where the angle between them is under this (degrees): the 5th
#: percentile, 105.9, of 35 aromatic carbons' angles in five-rings of C, N
#: and O in the clean deposits :data:`SP3_ANGLE_MAX_DEG` was read from (21
#: copies of 15 components; median 109.6), where 64 sp3 carbons of such
#: rings have a median of 104.6. A shallow indoline (its torsions under 20
#: degrees) is told so.
FIVE_RING_SP3_ANGLE_MAX_DEG = 106.0


def _small_ring_reduced(chemistry: "_Chemistry", k: int, order: Any, xyz: Any) -> bool:
    """Whether the deposit lays the ring of four or five holding the file's
    atom k as a reduced ring: puckered (:func:`_puckered`), or k a carbon with
    two heavy neighbours in a five-ring of C, N and O at under
    :data:`FIVE_RING_SP3_ANGLE_MAX_DEG`."""
    if _puckered(chemistry, k, order, xyz):
        return True
    around = sorted(chemistry.graph[k])
    if chemistry.mol is None or chemistry.elements[k] != 6 or len(around) != 2:
        return False
    # Only in a ring of C, N and O: a longer bond to S or P closes the angle
    # in a flat ring too (1X7Y's thiazolium C2 at 101 degrees, its C2-S1
    # 1.83 A).
    rings = [ring for ring in chemistry.mol.GetRingInfo().AtomRings()
             if len(ring) == 5 and k in ring
             and all(chemistry.elements[m] in (6, 7, 8) for m in ring)]
    return bool(rings) and _angle_deg(xyz, order[k], order[around[0]], order[around[1]]) \
        < FIVE_RING_SP3_ANGLE_MAX_DEG


def _laid_at_its_double(chemistry: "_Chemistry", k: int, m: int, xyz: Any, order: Any) -> bool:
    """Whether the file's single bond from carbon k to a terminal O or N m
    lies in the deposit nearer a double bond's length than its own: the
    other end of a tautomer's moved hydrogen (an enol's C-OH laid as a C=O)."""
    import numpy as _np

    if chemistry.elements[k] != 6 or chemistry.elements[m] not in (7, 8) \
            or len(chemistry.graph[m]) != 1 or chemistry.order_of(k, m) != 1.0:
        return False
    p = chemistry.position_of(k, m)
    double, single = _typical_bond_nm(chemistry, p, 2.0), _typical_bond_nm(chemistry, p)
    if double is None or single is None:
        return False
    length = float(_np.linalg.norm(xyz[order[k]] - xyz[order[m]]))
    return abs(length - double) < abs(length - single)


def _other_order(chemistry: "_Chemistry", chosen: dict[str, Any], tied: list[dict[str, Any]],
                 xyz_b: Any, skip: set[tuple[int, int]],
                 sigma: float, sigma_read: float | None = None,
                 turns: bool = False) -> tuple[list[tuple], list[tuple]]:
    """The bonds every tied match lays one order on whose deposited length
    fits another order better, by more than the copy's noise could make up
    (2 |e - e'| sigma, the lengths typical ones for the elements and each
    order): those that say another molecule, and those that are only noted.

    Another molecule, refused: where a carbon of the bond has the shape the
    other order gives it and the file's its own (three heavy neighbours:
    flat for a double bond where the file has it tetrahedral, tetrahedral
    for a single where the file has it flat; two: the angle between them,
    :func:`_carbon_shape`), an alcohol's file on its ketone's deposit,
    retinal's on retinol's 1KT6 (its C15 tetrahedral), DHT's on
    testosterone's; where a C=C of the file and a C-C beside it both fit
    the other order, the double bond moved (androst-5-ene on androst-4-ene,
    alpha- on gamma-terpinene); and where every tied match lays such a bond,
    each a different one, and the tie turns no centre (1-hexene's file on
    hexane's deposit).

    Noted, and placed as stated: a length alone, which no carbon's shape
    confirms (2HRE's vinyls modelled 1.52 A long, a ritonavir N-methyl 1.33
    A short, retinal's CHO on retinol's 1KT3, its C15 tetrahedral only
    within noise), or one every tied match lays somewhere (1-pyrroline's
    C=N on pyrrolidine's C-N); a C=N against a C-N; a pair beside each other
    that moves a hydrogen between a heteroatom and the ring, or a terminal
    C=O or C-OH at a carbon flat in both (a tautomer: keto and enol,
    pyridone and hydroxypyridine); and two bonds of a ring the file calls
    aromatic whose lengths fit a single (the other redox form, folate's file
    on DHF's deposit). The oxygens of a carboxylate, phosphate or sulfonate,
    whose orders are resonance, are left out, and so is an order the atoms
    cannot take (:func:`_can_take`) or a triple bond.

    Each as (structure atoms, length, the file's order, the order the length
    fits, hydrogens on the file's terminal atom, kind, and the structure
    atom whose shape confirms it with that shape or None): kind "order",
    "shift", "tautomer", "protonation" or "redox" (a C=N against a C-N,
    never refused) or "aromatic"."""
    orders = [_orders_on(chemistry, r["order"]) for r in tied]
    # Another molecule wherever the file is laid: refused only where every
    # tied match shows it (alpha-terpinene's file on gamma-terpinene's
    # deposit is tied two ways round its ring, each with a moved C=C).
    refused, noted = _order_misfits(chemistry, chosen, xyz_b, skip, sigma, orders, sigma_read)
    if not refused:
        # A moved double bond, wherever the file is laid: alpha-terpinene's
        # file on gamma-terpinene's deposit is tied two ways round its ring,
        # and each lays a C=C on a C-C beside a C-C on a C=C.
        def shifted(r: dict[str, Any]) -> list[tuple]:
            return [m for m in _order_misfits(chemistry, r, xyz_b, skip, sigma,
                                              sigma_read=sigma_read)[0]
                    if m[6] == "shift"]

        if all(shifted(r) for r in tied):
            refused = shifted(chosen)
        elif len(tied) > 1:
            # Every tied match lays a bond of the file's on another order's
            # length, but each a different bond (1-hexene's file on hexane's
            # deposit, laid at either end; 1-pyrroline's on pyrrolidine's, its
            # C=N on either C-N): refused where every one is confirmed by
            # shape and the tie turns no centre, else said, never verified
            # (1ZUI's shikimate, its C=C modelled 1.56 A on either arm of a
            # centre the tie leaves open).
            found = [_order_misfits(chemistry, r, xyz_b, skip, sigma, sigma_read=sigma_read)
                     for r in tied]
            if all(ref or note for ref, note in found):
                mine_refused, mine_noted = found[next(x for x, r in enumerate(tied)
                                                      if r is chosen)]
                if all(ref for ref, _ in found) and not turns:
                    refused = mine_refused
                else:
                    seen = {n[:2] for n in noted}
                    noted = [*noted, *((*m[:7], None) for m in mine_refused if m[:2] not in seen),
                             *(m for m in mine_noted if m[:2] not in seen)]
    # The others only where every tied match lays the file's order alike: a
    # bond the matches lay differently is what the tie left open (1ZUI's
    # shikimate, both arms of C4 long).
    if refused and (sigma_read or 0.0) > OTHER_MOLECULE_NOISE_MAX:
        # A copy this rough cannot say another molecule (one 0.09 A off in
        # each atom laid a ring C-C at 1.30 A beside a flattened carbon):
        # said, and placed as stated, never verified.
        noted = [*noted, *((*m[:7], None) for m in refused)]
        refused = []
    return refused, noted


def _order_misfits(chemistry: "_Chemistry", chosen: dict[str, Any], xyz_b: Any,
                   skip: set[tuple[int, int]], sigma: float,
                   orders: list[dict[tuple[int, int], float]] | None = None,
                   sigma_read: float | None = None) -> tuple[list[tuple], list[tuple]]:
    """:func:`_other_order` for one match: the bonds it lays whose length
    says another molecule, and those noted. With ``orders``, a bond the
    matches they come from lay differently is left out."""
    import numpy as _np

    order = chosen["order"]
    found: list[dict[str, Any]] = []
    for p, bond in enumerate(chosen["on"]):
        i, j = (int(v) for v in chemistry.bond_pairs[p])
        o = chemistry.bond_orders[p]
        if o not in (1.0, 1.5, 2.0, 3.0) or bond in skip:
            continue
        if any(k in chemistry.tucked and chemistry.elements[k] != 6 for k in (i, j)):
            # A carboxylate's, phosphate's or sulfonate's oxygens: resonance.
            continue
        if orders and any(seen.get(bond, o) != o for seen in orders):
            continue
        length = float(_np.linalg.norm(xyz_b[bond[0]] - xyz_b[bond[1]]))
        for other in ((1.0,) if o == 1.5 else (o - 1.0, o + 1.0)):
            # A triple bond is never read from a length (1BKF's C=C laid 1.11
            # A short is a broken deposit), nor an order the atoms cannot take.
            if other not in (1.0, 2.0) or not _can_take(chemistry, i, j, other, order, xyz_b):
                continue
            e0 = _typical_bond_nm(chemistry, p)
            form = _form_said(chemistry, i, j, o, other, order, xyz_b)
            # The other molecule's carbons have the other order's shape: a
            # single bond from an sp3 carbon, a double from an sp2. A
            # tautomer's carbon keeps its own (an enol's C-OH at an sp2
            # carbon), read so where the other shape finds nothing, and then
            # only said, never paired or refused (an imidic acid's C-O at 1.33
            # A under an amide's file).
            reshaped = {k: ("sp3" if other < o else "sp2") for k in (i, j)
                        if chemistry.elements[k] == 6 and chemistry.hybrid[k] in ("sp2", "sp3")}
            pair = {chemistry.elements[i], chemistry.elements[j]}
            candidates = [(_typical_bond_nm(chemistry, p, other, reshaped), False)]
            if form == "tautomer" and other < o and reshaped:
                candidates.append((_typical_bond_nm(chemistry, p, other), True))
            done = False
            for alternative, solo in candidates:
                e = e0
                if e is None or alternative is None:
                    if o == 1.5:
                        continue
                    e = float(chemistry.bond_lengths[p])
                    alternative = e + _ORDER_SHORTENING_NM[o] - _ORDER_SHORTENING_NM[other]
                margin = (length - e) ** 2 - (length - alternative) ** 2
                # Past the midpoint by less than the noise, a length does not
                # decide the order: said where it leans to the other order and
                # a carbon's shape is that order's past what a right ligand's
                # takes (benzaldehyde's file on a benzyl alcohol copy, its C-O
                # 1.33 A at a carbon of 110 degrees), or at an aromatic C-OH
                # that leans to a tautomer, never refused.
                weak = margin <= 2.0 * abs(e - alternative) * sigma
                bent = _bent_from_its_order(chemistry, i, j, o, other, order, xyz_b)
                if bent is not None and margin <= 0.0 and length - e > sigma:
                    # A carbon a double bond makes flat that the deposit bends
                    # to a tetrahedral angle, on a bond longer than a double's
                    # by more than the copy's noise (a copy's imine carbon at
                    # 110 degrees, its C1-C6 1.39 A, under an enamine's file):
                    # said. On a bond of a double's length it is the deposit's
                    # strain (4PMF's curcumin C17 at 113 degrees on a 1.34 A
                    # C17=C18, 1O61's PLP C4A on a 1.23 A C=O), and not said.
                    found.append({"atoms": (i, j), "bond": (order[i], order[j], length, o, other, 0),
                                  "form": None, "weak": True, "shape_only": True,
                                  "shaped": None, "shaped_any": bent, "clear": False,
                                  "fits_read": False, "pair_clear": False, "conjugated": False,
                                  "carbon": False, "aromatic": False, "cn": False})
                    done = True
                    break
                if margin <= -2.0 * abs(e - alternative) * sigma or (weak and o == 1.5):
                    continue
                beyond = (length - alternative) * (alternative - e) > 0.0
                if not weak and abs(length - alternative) > OTHER_ORDER_REACH_NM and not beyond:
                    # No bond of the other order either (1M7P's C3-O1P of 1.01
                    # A): a broken deposit, not another molecule. Past the other
                    # order's length, away from the file's, it is said, never
                    # refused (a C-O 1.55 A long under a C=O).
                    continue
                far = not weak and abs(length - alternative) > OTHER_ORDER_REACH_NM
                # To say another molecule the length must lie past the midpoint
                # by twice the noise, not once: a copy 0.08 A off in each atom
                # laid a C-C at 1.26 A.
                read = max(sigma, sigma_read or 0.0)
                clear = margin > 4.0 * abs(e - alternative) * read
                # A moved double bond shows on two bonds at once: half a noise
                # less on each.
                pair_clear = margin > 3.0 * abs(e - alternative) * read
                fits_read = margin > 2.0 * abs(e - alternative) * read
                shaped = None
                for k in (i, j):
                    if chemistry.elements[k] != 6 or o == 1.5:
                        continue
                    seen = _carbon_shape(chemistry, k, order, xyz_b)
                    # Both confirming, the one read by its three neighbours' volume
                    # is said, as the plainer shape.
                    if seen is None or (shaped is not None and len(chemistry.graph[k]) < 3):
                        continue
                    if (other > o and seen == "flat" and chemistry.hybrid[k] == "sp3") or (
                            other < o and seen == "tetrahedral" and chemistry.hybrid[k] == "sp2"):
                        shaped = (order[k], "flat" if other > o else "tetrahedral")
                if shaped is not None and other > o:
                    # A double bond needs both its atoms flat: a CH2 laid at 120
                    # degrees beside a pyramidal amine N (3HEC's imatinib, its
                    # C46-N48 modelled 1.27 A) is a badly modelled single.
                    partner = j if order[i] == shaped[0] else i
                    if _carbon_shape(chemistry, partner, order, xyz_b, any_element=True) \
                            == "tetrahedral":
                        shaped = None
                # A carbon whose angle lies where sp2 and sp3 carbons both lie
                # decides nothing: said with the bond (1U0G's C1 at 116
                # degrees, its C1-O1 1.42 A under a C=O).
                band = None
                for k in (i, j):
                    angle = _angle_in_the_band(chemistry, k, order, xyz_b)
                    if shaped is None and angle is not None and (
                            (other > o and chemistry.hybrid[k] == "sp3")
                            or (other < o and chemistry.hybrid[k] == "sp2")):
                        band = (order[k], angle)
                end = j if len(chemistry.graph[j]) == 1 else i if len(chemistry.graph[i]) == 1 else None
                # A terminal heteroatom's hydrogens, said on its single bond
                # ("C-OH"); a carbon's would be the other molecule's, unknown.
                held = (chemistry.mol.GetAtomWithIdx(end).GetTotalNumHs()
                        if end is not None and chemistry.mol is not None
                        and chemistry.elements[end] != 6 else 0)
                a, b = order[i], order[j]
                if end == i:
                    a, b = b, a
                if weak and shaped is not None and (margin <= 0.0 or _carbon_shape(
                        chemistry, i if order[i] == shaped[0] else j, order, xyz_b,
                        strict=True) != shaped[1]):
                    shaped = None
                # An amide's C-N lies between C-N and C=N by its resonance:
                # its length alone is never said.
                # A length alone leans to a tautomer only at an aromatic
                # ring's C-OH laid short (2-hydroxypyridine's file on a
                # pyridone's 1.26 A C=O); elsewhere, an amide's C-N and a
                # carbonyl a little long among them, it is the deposit's.
                aromatic_oh = (form == "tautomer" and margin > 0.0 and o == 1.0
                               and pair == {6, 8} and any(chemistry.order_of(k, m) == 1.5
                                                         for k in (i, j)
                                                         for m in chemistry.graph[k]))
                # Leaning to a tautomer, a length alone is kept only beside
                # another of its carbon that leans the other way.
                pending = (form == "tautomer" and margin > 0.0 and not aromatic_oh
                           and weak and shaped is None)
                # Past the midpoint within noise at an open-chain carbon whose
                # angle lies in the band: said, angle and all, never verified
                # (1-hexene's file on a rough hexane, its C1-C2 1.45 A at 118
                # degrees). An open-chain sp2 carbon's angle lies over 122. A
                # bond conjugated, or beside an sp2 atom, has its conjugation's
                # length (a retinoic acid's C11-C12 at 1.44 A), not read so.
                band_lean = (weak and margin > 0.0 and shaped is None and band is not None
                             and pair in ({6}, {6, 8}) and not _conjugated(chemistry, i, j)
                             and not any(chemistry.hybrid[m] in ("sp2", "sp")
                                         for k in (i, j) for m in chemistry.graph[k]
                                         if m not in (i, j))
                             and chemistry.mol is not None
                             and not chemistry.mol.GetRingInfo().NumAtomRings(
                                 i if order[i] == band[0] else j))
                if weak and shaped is None and not aromatic_oh and not band_lean and (
                        not pending or (not solo and len(candidates) > 1)):
                    continue
                if far:
                    found.append({"atoms": (i, j), "bond": (a, b, length, o, other, held),
                                  "form": None, "weak": True, "shape_only": False, "far": True,
                                  "shaped": None, "shaped_any": None, "clear": False,
                                  "fits_read": False, "pair_clear": False, "conjugated": False,
                                  "carbon": False, "aromatic": o == 1.5, "cn": False})
                    done = True
                    break
                found.append({"atoms": (i, j), "bond": (a, b, length, o, other, held),
                              "form": form, "weak": weak, "solo": solo or pending,
                              "pending": pending, "band": band,
                              "shaped": shaped if clear else None, "clear": clear,
                              "shaped_any": shaped,
                              "fits_read": fits_read, "pair_clear": pair_clear,
                              "conjugated": _conjugated(chemistry, i, j),
                              "carbon": chemistry.elements[i] == 6
                              and chemistry.elements[j] == 6, "aromatic": o == 1.5,
                              "cn": {chemistry.elements[i], chemistry.elements[j]} == {6, 7}})
                done = True
                break
            if done:
                break
    # In a ring of five or fewer, whose angles are the ring's, the atom is
    # read by the ring's puckering instead, or by both bonds past twice the
    # noise (indole's file on indoline's deposit, furan's on THF's).
    # A bond the file calls aromatic laid at a single's length says the other
    # redox or tautomeric form only where the ring's other bonds fit it too:
    # beside another of its ring at an atom the deposit has tetrahedral, the
    # reduced ring's (DHF's C6-C7 and C7-N8 at C7, under folate's file). One
    # long bond of a ring alone is the deposit's (an adenine's C5-C6 at 1.45
    # A), and so are two at a flat atom (an N-acyl indole's N1).
    # A tautomer's lean is kept only beside another bond of its carbon going
    # the other way (a C=O laid long beside a C-N laid short).
    found = [m for m in found if not m.get("pending") or any(
        n is not m and set(n["atoms"]) & set(m["atoms"])
        and (n["bond"][4] > n["bond"][3]) != (m["bond"][4] > m["bond"][3]) for n in found)]
    lone = [x for x, m in enumerate(found) if m["aromatic"]]
    if lone:
        kept = set()
        for x in lone:
            for y in lone:
                shared = set(found[x]["atoms"]) & set(found[y]["atoms"])
                if x == y or not shared:
                    continue
                k = next(iter(shared))
                reduced = (_carbon_shape(chemistry, k, order, xyz_b, any_element=True)
                           == "tetrahedral" or _small_ring_reduced(chemistry, k, order, xyz_b))
                if reduced:
                    kept |= {x, y}
                    if _in_a_small_ring(chemistry, k):
                        # The ring's shape confirms the reduced ring: said
                        # past once the noise, as a non-conjugated bond is.
                        found[x]["clear"] = found[y]["clear"] = True
                elif (_in_a_small_ring(chemistry, k) and len(chemistry.graph[k]) == 2
                      and found[x]["clear"] and found[y]["clear"]):
                    # Both long past twice the noise in a ring the deposit
                    # lays flat: the lengths said, the form not (1X7Y's
                    # thiazolium). At a flat atom of three heavy neighbours
                    # (an N-acyl indole's N1, 3FO7's indomethacin) not said.
                    kept |= {x, y}
                    found[x]["flat_ring"] = found[y]["flat_ring"] = True
        found = [m for x, m in enumerate(found) if not m["aromatic"] or x in kept]
    refused: list[tuple] = []
    noted: list[tuple] = []
    paired: dict[int, str] = {}
    for x, first in enumerate(found):
        for y, second in enumerate(found[x + 1:], start=x + 1):
            if first["aromatic"] or second["aromatic"] or first.get("solo") or second.get("solo"):
                continue
            if not set(first["atoms"]) & set(second["atoms"]):
                continue
            if not (first["fits_read"] and second["fits_read"]
                    and (first["pair_clear"] or second["pair_clear"])):
                continue
            up = [m["bond"][4] > m["bond"][3] for m in (first, second)]
            if up[0] == up[1]:
                continue
            kind = "shift" if first["carbon"] and second["carbon"] else "tautomer"
            for z in (x, y):
                if paired.get(z) != "shift":
                    paired[z] = kind
    # Three bonds or more each of another order's length and shape (a
    # cyclohexane's file on toluene's ring, every C-C 1.40 A at a flat
    # carbon) say another molecule together where none is clear alone.
    together = sum(1 for x, m in enumerate(found)
                   if m["shaped_any"] and not m["aromatic"] and not m["cn"] and not m["weak"]
                   and x not in paired) >= 3
    for x, m in enumerate(found):
        if m.get("solo"):
            noted.append((*m["bond"], ("protonation" if m["cn"] else "tautomer")
                          + (":lean" if m["weak"] else ""), None))
            continue
        if m["weak"] and m.get("band") and not m["shaped_any"] and not m.get("far"):
            noted.append((*m["bond"], (m["form"] if m["form"] == "tautomer" else "order")
                          + ":leanband", m["band"]))
            continue
        if m["weak"]:
            lean = ":shape" if m.get("shape_only") else ":far" if m.get("far") else ":lean"
            form = ("redox" if m["form"] == "redox" else "protonation") if m["cn"] \
                else (m["form"] or "order")
            noted.append((*m["bond"], form + lean, m["shaped_any"]))
            continue
        kind = ("aromatic:flat" if m.get("flat_ring") else "aromatic") if m["aromatic"] \
            else paired.get(x, "order")
        if kind == "order" and m["cn"]:
            # A C=N against a C-N is another protonation, tautomeric or redox
            # form of the file's molecule (3HEC's imatinib, its C46-N48
            # modelled 1.27 A beside a flat N), or the other redox form where
            # the carbon's hydrogens change with it (an amine and its imine):
            # said, never refused.
            noted.append((*m["bond"], "redox" if m["form"] == "redox" else "protonation", None))
            continue
        if kind == "order" and m["form"] == "tautomer" and not m["shaped_any"]:
            # A C=O at a flat carbon laid as a C-OH, or the reverse, beside
            # an atom that can give or take the hydrogen (warfarin's C4-O3,
            # a pyridone's C=O): the other tautomer.
            kind = "tautomer"
        if together and kind == "order" and m["shaped_any"]:
            m = {**m, "shaped": m["shaped_any"]}
        if kind == "shift" or (kind == "order" and m["shaped"]):
            refused.append((*m["bond"], kind, m["shaped"]))
        elif m["clear"] or not m["conjugated"] or kind == "tautomer":
            # A conjugated bond lies between its orders' lengths by its
            # conjugation (an enone's C3-C4 at 1.36 A): noted only clear of it.
            if kind in ("order", "tautomer") and m.get("band") and not m.get("solo"):
                noted.append((*m["bond"], "order:band", m["band"]))
            else:
                noted.append((*m["bond"], kind, None))
    return refused, noted


def _assign_groups(order: list[int], chemistry: "_Chemistry",
                   groups_b: dict[int, list[int]], xyz_b: Any,
                   elements_b: list[int], sigma: float = BOND_NOISE_NM) -> list[int]:
    """Give each terminal group's file atoms the structure's: by the
    lengths the file gives them (a C=O beside a C-OH, a P=O beside P-OH: the
    bond to the parent, and the distances to the parent's other neighbours),
    then, between atoms the file holds alike (the F of a CF3), by nearest
    position once the file is laid on the atoms around the group's parent."""
    import itertools

    import numpy as _np

    file = chemistry.positions
    undecided: list[int] = []
    for parent, members in chemistry.groups.items():
        target_parent = order[parent]
        pool = list(groups_b.get(target_parent, []))
        around = [parent, *sorted(j for j in chemistry.graph[parent] if j not in members)]
        for j in list(around[1:]):
            if len(around) >= 3:
                break
            around.extend(k for k in sorted(chemistry.graph[j])
                          if k not in around and k not in chemistry.tucked)
        if len(around) >= 3:
            source = file[around]
            target = xyz_b[[order[a] for a in around]]
            rotation = _superposing_rotation(source, target)
            laid = (file - source.mean(axis=0)) @ rotation.T + target.mean(axis=0)
        else:
            laid = file - file[parent] + xyz_b[target_parent]
        others = [j for j in sorted(chemistry.graph[parent]) if j not in members]
        by_element: dict[int, list[int]] = {}
        for m in members:
            by_element.setdefault(chemistry.elements[m], []).append(m)
        for element, mine in by_element.items():
            theirs = [m for m in pool if elements_b[m] == element]
            bonds = _np.zeros((len(mine), len(theirs)))
            across = _np.zeros((len(mine), len(theirs)))
            for i, atom in enumerate(mine):
                for t, candidate in enumerate(theirs):
                    bond = float(_np.linalg.norm(xyz_b[candidate] - xyz_b[target_parent]))
                    bonds[i, t] = (bond - chemistry.terminal_length(parent, atom)) ** 2
                    if not chemistry.flat:
                        for y in others:
                            there = float(_np.linalg.norm(xyz_b[candidate] - xyz_b[order[y]]))
                            across[i, t] += (there - float(_np.linalg.norm(file[atom] - file[y]))) ** 2
            distance = _np.linalg.norm(laid[mine][:, None] - xyz_b[theirs][None], axis=2)
            if len(mine) <= 6:
                # By the bond lengths first (a C=O on the short bond, its OH
                # on the long), the 1-3 distances only between members whose
                # bonds tie, and position between atoms the file holds alike.
                count = len(mine)
                permutations = list(itertools.permutations(range(len(theirs))))
                best = min(permutations,
                           key=lambda p, b=bonds, a=across, d=distance, c=count: (
                               round(sum(b[i, p[i]] for i in range(c)) / 1e-5),
                               round(sum(a[i, p[i]] for i in range(c)) / 1e-4),
                               sum(d[i, p[i]] for i in range(c))))
                # Where the file holds the members apart (a P=O beside its
                # P-OH) and another giving costs no more than the deposit's
                # noise could make up (2 |e - e'| sigma on each structure
                # atom given another length, as between matches), the
                # structure did not decide which is which.
                lengths = [chemistry.terminal_length(parent, atom) for atom in mine]
                held = sum(bonds[i, best[i]] for i in range(count))
                # Members the file holds alike (a CF3's F) have no giving to
                # tell apart.
                for other in (permutations if max(lengths) - min(lengths) > 1e-6 else []):
                    window = sum(2.0 * abs(lengths[i] - lengths[best.index(other[i])])
                                 * sigma for i in range(count))
                    if window > 1e-9 and sum(bonds[i, other[i]] for i in range(count)) - held \
                            <= window:
                        undecided.append(parent)
                        break
            else:
                from scipy.optimize import linear_sum_assignment

                _, best = linear_sum_assignment(bonds + 1e-2 * across + 1e-4 * distance)
            for i, atom in enumerate(mine):
                order[atom] = theirs[best[i]]
    return sorted(set(undecided))


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


#: A rotor whose hydrogens come closer than this (1.5 A) to an atom three
#: or more bonds away is turned to its clearest torsion. A hydrogen properly
#: placed is 1.8 A or more from anything that far (2.3 A across an eclipsed
#: C-C), and an intramolecular hydrogen bond is about 1.8 A.
HYDROGEN_CLEARANCE_NM = 0.15

#: A hydrogen still closer than this (1.0 A) to an atom three or more bonds
#: away, after the scan, is an overlap, and refused.
HYDROGEN_OVERLAP_NM = 0.10



def _load_structure(structure: str | Path) -> Any:
    """The structure, read through `record_access`, so a read held to a
    study refuses one linked out of it (`OutsideWorkspace`)."""
    import tempfile

    import mdtraj as _md  # noqa: PLC0415

    from fastmdxplora import record_access

    data = record_access.read_bytes(Path(structure))
    suffix = Path(structure).suffix or ".pdb"
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / f"structure{suffix}"
        copy.write_bytes(data)
        frame = _md.load(str(copy))
    if suffix.lower() in (".pdb", ".ent"):
        _keep_deposited_names(frame, data.decode("utf-8", "replace"))
    return frame


#: Residue names MDTraj's reader replaces with a polymer's (a free adenine,
#: uracil or cytosine, an N-methyl amide), kept as deposited on a HETATM.
_RENAMED_ON_READING = {"ADE": "A", "GUA": "G", "CYT": "C", "URA": "U", "DAD": "DA",
                       "DGU": "DG", "DCY": "DC", "THY": "DT", "NMA": "NME"}


def _keep_deposited_names(frame: Any, text: str) -> None:
    """Give each HETATM residue MDTraj renamed its deposited name back: a
    free uracil (4WPL's URA) read as U was not found under its own name, and
    its pose silently came from the file."""
    deposited: dict[tuple[str, int], str] = {}
    for line in text.splitlines():
        if line.startswith("HETATM") and len(line) >= 26:
            name = line[17:20].strip().upper()
            if name in _RENAMED_ON_READING:
                try:
                    deposited[(line[21], int(line[22:26]))] = name
                except ValueError:
                    continue
    if not deposited:
        return
    for residue in frame.topology.residues:
        chain = getattr(getattr(residue, "chain", None), "chain_id", None)
        name = deposited.get((chain or " ", residue.resSeq))
        if name is not None and residue.name == _RENAMED_ON_READING[name]:
            residue.name = name


def _element_symbol(atomic_number: int) -> str:
    try:
        from rdkit import Chem

        return Chem.GetPeriodicTable().GetElementSymbol(int(atomic_number))
    except Exception:  # noqa: BLE001 - RDKit absent, or a number it has not
        return f"Z{atomic_number}"


def _file_name(molecule: Any, index: int) -> str:
    """A file atom as a person finds it in the file: its name where it has
    one, else its element and its place in the file, from 1."""
    atom = molecule.atoms[index]
    name = str(getattr(atom, "name", "") or "").strip()
    return name or f"{_element_symbol(atom.atomic_number)}{index + 1}"


def _bond_symbol(first: int, second: int, order: float | None) -> str:
    """Two elements joined by the bond of ``order``: C-O, C=O, C#N."""
    names = {1: "H", 5: "B", 6: "C", 7: "N", 8: "O", 9: "F", 14: "Si", 15: "P", 16: "S",
             17: "Cl", 34: "Se", 35: "Br", 53: "I"}
    mark = {1.0: "-", 1.5: ":", 2.0: "=", 3.0: "#"}.get(order, "-")
    return f"{names.get(first, '?')}{mark}{names.get(second, '?')}"


def _bond_said(frame: Any, indices: list[int], elements: list[int], a: int, b: int,
               length: float, order: float | None) -> str:
    """A structure bond by its atoms' names and length, with the file's
    order on it where given: "C4=C10 1.60 A (double in the file)"."""
    first, second = (frame.topology.atom(indices[i]).name for i in (a, b))
    mark = {1.0: "-", 1.5: ":", 2.0: "=", 3.0: "#"}.get(order, "-")
    kind = {2.0: " (double in the file)", 3.0: " (triple in the file)",
            1.5: " (aromatic in the file)"}.get(order, "")
    return f"{first}{mark}{second} {length * 10:.2f} A{kind}"


def _with_hydrogens(held: int, order: float) -> str:
    """A terminal atom's hydrogens on a single bond: "H", "H2"."""
    return ("H" * min(held, 1) + (str(held) if held > 1 else "")) if order == 1.0 else ""


def _an(said: str) -> str:
    """The article before a bond's symbol, by how its first letter sounds:
    "an S=C", "a C=S"."""
    return "an" if said[:1] in "AEFHILMNORSX" else "a"


def _misfit_said(frame: Any, indices: list[int], elements: list[int], misfit: tuple) -> str:
    """A bond whose deposited length fits another order than the file's:
    "C15-O1 1.43 A fits C-OH, the file has C=O"."""
    a, b, length, o, other, held, kind, shape = misfit
    bond = _bond_said(frame, indices, elements, a, b, length, None)
    fits = f"{_bond_symbol(elements[a], elements[b], other)}{_with_hydrogens(held, other)}"
    if kind == "aromatic":
        return f"{bond} fits {fits}, the file has it aromatic (the other redox or tautomeric form)"
    if kind == "aromatic:flat":
        return f"{bond} fits {fits}, the file has it aromatic; the deposit lays its ring flat"
    has = f"{_bond_symbol(elements[a], elements[b], o)}{_with_hydrogens(held, o)}"
    kind, _, lean = kind.partition(":")
    gloss = {"tautomer": " (the other tautomer)", "redox": " (the other redox form)",
             "protonation": " (another protonation, tautomeric or redox form)"}.get(kind, "")
    if lean == "leanband":
        return (f"{bond} leans to {fits}, the file has {has}{gloss}; "
                f"{frame.topology.atom(indices[shape[0]]).name}'s angle {shape[1]:.0f} degrees "
                "does not tell")
    if lean == "band":
        return (f"{bond} fits {fits}, the file has {has}; "
                f"{frame.topology.atom(indices[shape[0]]).name}'s angle {shape[1]:.0f} degrees "
                "does not tell")
    if lean == "shape":
        return (f"{bond}, with its {frame.topology.atom(indices[shape[0]]).name} {shape[1]} "
                f"as in {fits}; the file has {has}")
    if lean == "far":
        return f"{bond}, past even {_an(fits)} {fits}'s length; the file has {has}"
    if lean and shape is not None:
        # A length past the midpoint within noise, at a carbon of the other
        # order's shape.
        return (f"{bond} leans to {fits}, with its "
                f"{frame.topology.atom(indices[shape[0]]).name} {shape[1]} as in {fits}; "
                f"the file has {has}{gloss}")
    if lean:
        return f"{bond} leans to {fits}, the file has {has}{gloss}"
    return f"{bond} fits {fits}, the file has {has}{gloss}"


def _other_molecule_said(frame: Any, indices: list[int], elements: list[int],
                         refused: list[tuple]) -> str:
    """What a deposit's lengths and shapes show of another molecule:
    "its C4-C5 is 1.31 A, a C=C, where the file has C-C, and its C5 is
    flat"."""
    parts = []
    for a, b, length, o, other, held, _kind, shape in refused:
        first, second = (frame.topology.atom(indices[k]).name for k in (a, b))
        symbol = _bond_symbol(elements[a], elements[b], other)
        part = (f"its {first}-{second} is {length * 10:.2f} A, {_an(symbol)} "
                f"{symbol}{_with_hydrogens(held, other)}, "
                f"where the file has "
                f"{_bond_symbol(elements[a], elements[b], o)}{_with_hydrogens(held, o)}")
        if shape is not None:
            part += f", and its {frame.topology.atom(indices[shape[0]]).name} is {shape[1]}"
        parts.append(part)
    shifted = any(m[6] == "shift" for m in refused)
    return "; ".join(parts) + (": the double bond lies elsewhere" if shifted else "")


def _structure_bond(frame: Any, indices: list[int], a: int, b: int) -> str:
    """A structure bond by its atoms' names (positions in the residue)."""
    return (f"{frame.topology.atom(indices[a]).name}-"
            f"{frame.topology.atom(indices[b]).name}")


def _mapped_name(molecule: Any, heavy: list[int], k: int, frame: Any,
                 indices: list[int], order: list[int]) -> str:
    return (f"{_file_name(molecule, heavy[k])} on the structure's "
            f"{frame.topology.atom(indices[order[k]]).name}")


def _unmatched(elements_a: list[int], graph_a: list[set[int]],
               elements_b: list[int], graph_b: list[set[int]]
               ) -> tuple[list[int], list[int]] | None:
    """The atoms of each graph outside their largest common part (RDKit's
    maximum common substructure, by element, any bond); None where it
    cannot be found."""
    try:
        from rdkit import Chem
        from rdkit.Chem import rdFMCS

        def built(elements: list[int], graph: list[set[int]]) -> Any:
            editable = Chem.RWMol()
            for number in elements:
                atom = Chem.Atom(int(number))
                atom.SetNoImplicit(True)
                editable.AddAtom(atom)
            for i, neighbours in enumerate(graph):
                for j in neighbours:
                    if i < j:
                        editable.AddBond(i, int(j), Chem.BondType.SINGLE)
            mol = editable.GetMol()
            mol.UpdatePropertyCache(strict=False)
            Chem.FastFindRings(mol)
            return mol

        a, b = built(elements_a, graph_a), built(elements_b, graph_b)
        common = rdFMCS.FindMCS(
            [a, b], atomCompare=rdFMCS.AtomCompare.CompareElements,
            bondCompare=rdFMCS.BondCompare.CompareAny,
            ringMatchesRingOnly=False, completeRingsOnly=False, timeout=5)
        query = Chem.MolFromSmarts(common.smartsString) if common.numAtoms else None
        in_a = set(a.GetSubstructMatch(query)) if query is not None else set()
        in_b = set(b.GetSubstructMatch(query)) if query is not None else set()
        return (sorted(set(range(len(elements_a))) - in_a),
                sorted(set(range(len(elements_b))) - in_b))
    except Exception:  # noqa: BLE001 - RDKit absent or the search failed
        return None


def _refuse_unmatched(reason: str, molecule: Any, heavy: list[int],
                      file_elements: list[int], file_graph: list[set[int]],
                      frame: Any, indices: list[int], elements: list[int],
                      crystal_graph: list[set[int]], resname: str) -> None:
    """Refuse a residue whose heavy atoms are not the file's, naming the
    atoms of each outside what the two share."""
    found = _unmatched(file_elements, file_graph, elements, crystal_graph)
    file_atoms = ([_file_name(molecule, heavy[k]) for k in found[0]]
                  if found else [])
    structure_atoms = ([frame.topology.atom(indices[k]).name for k in found[1]]
                       if found else [])
    if found is None:
        named = ""
    elif file_atoms or structure_atoms:
        named = (" Not matched: "
                 + (f"the file's {', '.join(file_atoms)}" if file_atoms
                    else "none of the file's atoms")
                 + "; "
                 + (f"the structure's {', '.join(structure_atoms)}" if structure_atoms
                    else "none of the structure's")
                 + ".")
    else:
        named = " Every atom has a counterpart; the bonds between them differ."
    raise LigandError(
        f"The ligand's pose cannot be taken from the structure: {reason}.{named} "
        "Setup stops rather than place the file's molecule on atoms that are "
        "not its own. Supply the file of the molecule the structure holds; "
        "where the file's pose is the one meant (a docked or deliberately "
        "unbound start), say so with ligand_pose: file.",
        code="setup.ligand.atoms_unmatched", resname=resname,
        file_atoms=file_atoms, structure_atoms=structure_atoms,
        option="setup.ligand")


def _heavy_rdkit(molecule: Any, heavy: list[int]) -> Any:
    """The file's molecule as RDKit holds it, hydrogens removed and its
    heavy atoms in the file's order; None where it carries no chemistry
    RDKit reads (no bond orders to read stereochemistry from)."""
    try:
        from rdkit import Chem
    except ImportError:
        return None
    source = getattr(molecule, "rdkit", None)
    if source is None and callable(getattr(molecule, "to_rdkit", None)):
        try:
            source = molecule.to_rdkit()
        except Exception:  # noqa: BLE001 - the toolkit's own conversion
            source = None
    if source is None or source.GetNumAtoms() != len(molecule.atoms):
        return None
    params = Chem.RemoveHsParameters()
    for flag in ("removeDegreeZero", "removeHigherDegrees", "removeIsotopes",
                 "removeDefiningBondStereo", "removeWithWedgedBond",
                 "removeWithQuery", "removeMapped", "removeInSGroups",
                 "removeHydrides", "removeNontetrahedralNeighbors"):
        if hasattr(params, flag):
            setattr(params, flag, True)
    try:
        stripped = Chem.RemoveHs(Chem.Mol(source), params, sanitize=False)
        if [a.GetAtomicNum() for a in stripped.GetAtoms()] != [
                molecule.atoms[i].atomic_number for i in heavy]:
            return None
        Chem.SanitizeMol(stripped)
    except Exception:  # noqa: BLE001 - chemistry RDKit does not read
        return None
    return stripped


def _with_positions(mol: Any, xyz_nm: Any) -> Any:
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    placed = Chem.Mol(mol)
    placed.RemoveAllConformers()
    conformer = Chem.Conformer(placed.GetNumAtoms())
    for i, (x, y, z) in enumerate(xyz_nm):
        conformer.SetAtomPosition(i, Point3D(10.0 * x, 10.0 * y, 10.0 * z))
    conformer.Set3D(True)
    placed.AddConformer(conformer, assignId=True)
    return placed


def _cis(xyz: Any, a: int, u: int, v: int, b: int) -> bool:
    """Whether a (on u) and b (on v) lie on one side of the u=v bond: their
    offsets from the bond, square to it, point the same way."""
    import numpy as _np

    axis = xyz[v] - xyz[u]
    axis = axis / (float(_np.linalg.norm(axis)) or 1.0)
    first = xyz[a] - xyz[u]
    second = xyz[b] - xyz[v]
    first = first - _np.dot(first, axis) * axis
    second = second - _np.dot(second, axis) * axis
    return float(_np.dot(first, second)) > 0.0


def _handedness(xyz: Any, centre: int, neighbours: tuple[int, int, int]) -> bool:
    """The sign of the volume three neighbours span about a centre."""
    return _volume(xyz, centre, neighbours) > 0.0


def _volume(xyz: Any, centre: int, neighbours: tuple[int, int, int]) -> float:
    """The signed volume the unit vectors to three neighbours span about a
    centre: about 0.77 for a tetrahedral centre, 0 for a flat one."""
    import numpy as _np

    first, second, third = (_unit(xyz[n] - xyz[centre]) for n in neighbours)
    if first is None or second is None or third is None:
        return 0.0
    return float(_np.dot(_np.cross(first, second), third))


def _across(xyz: Any, a: int, u: int, v: int, b: int) -> float:
    """The cosine of the a-u=v-b dihedral: 1 cis, -1 trans."""
    import numpy as _np

    axis = _unit(xyz[v] - xyz[u])
    if axis is None:
        return 0.0
    first = xyz[a] - xyz[u]
    second = xyz[b] - xyz[v]
    first = _unit(first - _np.dot(first, axis) * axis)
    second = _unit(second - _np.dot(second, axis) * axis)
    if first is None or second is None:
        return 0.0
    return float(_np.dot(first, second))


#: A stereocentre is judged only where the volume its three neighbours'
#: unit vectors span is at least this (a tetrahedral centre spans 0.77):
#: under it the centre is flattened past what its noise allows a sign, as
#: a poorly resolved deposit's sugar C1' can be (its angles summing to
#: 356 degrees, against 328.5), and it is recorded as not judged.
STEREO_VOLUME_MIN = 0.25

#: Under this, a structure atom's three neighbours lie in its plane: the
#: geometry of an sp2 atom, not of a stereocentre. A stated centre laid on
#: one disagrees with the file, where between this and
#: :data:`STEREO_VOLUME_MIN` it is a stereocentre flattened by noise, and
#: not judged.
STEREO_FLAT_MAX = 0.12

#: A stereo double bond is judged only where the cosine of its dihedral is
#: at least this far from 0 (within 30 degrees of cis or trans).
STEREO_DIHEDRAL_MIN = 0.5

#: A file whose z range is under this (nm), or whose conformer says it is
#: not 3D, is a 2D depiction: its lengths are never used to judge a deposit.
FLAT_NM = 1e-4

#: Bond length by order, shortened from the sum of covalent radii, for a
#: file whose own lengths are a depiction's (nm).
_ORDER_SHORTENING_NM = {1.0: 0.0, 1.5: 0.009, 2.0: 0.019, 3.0: 0.032}


class _Chemistry:
    """What a ligand file says of its heavy atoms beyond their graph.

    The bonds and the lengths their orders imply (the file's own, or ideal
    ones for a flat file), the file's 1-3 distances, its interchangeable
    terminal groups, each heavy atom's hybridisation, and the stereochemistry
    it states, each element with the sign it has in the file.
    """

    def __init__(self, molecule: Any, heavy: list[int], positions: Any):
        import numpy as _np

        self.molecule, self.heavy = molecule, heavy
        self.n = len(heavy)
        self.elements = [molecule.atoms[i].atomic_number for i in heavy]
        self.all_positions = _np.asarray(positions, dtype=float)
        self.positions = self.all_positions[heavy]
        self.graph = _heavy_graph(molecule, self.all_positions, heavy)
        self.flat = bool(self.n) and self._drawn()
        self.groups = _terminal_groups(self.graph, self.elements)
        self.tucked = {m for members in self.groups.values() for m in members}
        self.mol = _heavy_rdkit(molecule, heavy)
        orders: dict[tuple[int, int], float] = {}
        if self.mol is not None:
            for bond in self.mol.GetBonds():
                pair = tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
                orders[pair] = float(bond.GetBondTypeAsDouble())
        pairs = sorted({tuple(sorted((i, j))) for i in range(self.n) for j in self.graph[i]})
        self.bond_pairs = _np.array(pairs, dtype=int).reshape(-1, 2)
        self.bond_orders = [orders.get(pair, 0.0) for pair in pairs]
        self.rough = self.flat or self._roughly_built()
        self._pairs, self._orders, self._ideal_cache = pairs, orders, None
        if self.flat:
            self.bond_lengths = _np.array(self._ideal_lengths(pairs, orders))
            self._ideal_cache = self.bond_lengths
            self.angle_pairs = _np.zeros((0, 2), dtype=int)
            self.angle_lengths = _np.zeros(0)
        else:
            self.bond_lengths = _np.linalg.norm(
                self.positions[self.bond_pairs[:, 0]] - self.positions[self.bond_pairs[:, 1]],
                axis=1) if pairs else _np.zeros(0)
            across = sorted({tuple(sorted((i, k))) for j in range(self.n)
                             for i in self.graph[j] for k in self.graph[j] if i != k})
            self.angle_pairs = _np.array(across, dtype=int).reshape(-1, 2)
            self.angle_lengths = _np.linalg.norm(
                self.positions[self.angle_pairs[:, 0]] - self.positions[self.angle_pairs[:, 1]],
                axis=1) if across else _np.zeros(0)
        if pairs and not self.flat:
            # One length for each symmetry class of the file's bonds, their
            # mean: a bare embedding gives a ring's equivalent C-C 1.48 and
            # 1.52 A, and a match that lays them the other way round is the
            # same molecule, which its score must not tell apart.
            classes = self._bond_classes(pairs, orders)
            means = {c: float(_np.mean([length for length, d in zip(self.bond_lengths, classes)
                                        if d == c])) for c in set(classes)}
            self.bond_lengths = _np.array([means[c] for c in classes])
        self._expected = {(int(i), int(j)): float(length) for (i, j), length
                          in zip(self.bond_pairs, self.bond_lengths)}
        self.hybrid = self._hybridisation()
        self._stereo()

    @property
    def ideal_bond_lengths(self) -> Any:
        """Each bond's ideal length by its elements and order (nm), whatever
        the file's coordinates: what a copy's own noise is also read
        against, so a bare embedding's lengths do not count as the
        deposit's noise. Worked out when first asked for, each bond at its
        own order (a flat file's are its resonance-averaged lengths)."""
        import numpy as _np

        if self._ideal_cache is None:
            # Without the resonance forms, which a molecule of many (suramin's
            # six sulfonates) takes seconds to list: a copy's noise is read
            # against the nearer of these and the file's own lengths.
            self._ideal_cache = (_np.array(self._ideal_lengths(self._pairs, self._orders,
                                                               resonance=False))
                                 if self._pairs else _np.zeros(0))
        return self._ideal_cache

    def _bond_classes(self, pairs: list[tuple[int, int]],
                      orders: dict[tuple[int, int], float]) -> list[tuple]:
        """Each bond's symmetry class: the classes of its two atoms under the
        molecule's graph symmetry, and its order."""
        ranks: list[int] | None = None
        if self.mol is not None:
            try:
                from rdkit import Chem

                ranks = list(Chem.CanonicalRankAtoms(self.mol, breakTies=False,
                                                     includeChirality=False))
            except Exception:  # noqa: BLE001 - the graph's own refinement below
                ranks = None
        if ranks is None:
            labels = [(e, len(g)) for e, g in zip(self.elements, self.graph)]
            for _ in range(self.n):
                refined = [(labels[i], tuple(sorted(labels[j] for j in self.graph[i])))
                           for i in range(self.n)]
                names = {label: k for k, label in enumerate(sorted(set(refined)))}
                new = [names[label] for label in refined]
                if len(set(new)) == len(set(labels)):
                    labels = new
                    break
                labels = new
            ranks = [int(v) if not isinstance(v, tuple) else hash(v) for v in labels]
        return [(min(ranks[i], ranks[j]), max(ranks[i], ranks[j]), orders.get((i, j), 0.0))
                for i, j in pairs]

    def _drawn(self) -> bool:
        """Whether the file's coordinates are a 2D depiction: no extent in
        z, or a conformer that says it is not 3D. By its coordinates alone,
        not its lengths: a depiction's heavy atoms are drawn 1.0, 1.5 or
        1.54 A apart, and its hydrogens may be added in the page."""
        import numpy as _np

        if float(_np.ptp(self.all_positions[:, 2])) < FLAT_NM:
            return True
        rdkit = getattr(self.molecule, "rdkit", None)
        try:
            return bool(rdkit is not None and rdkit.GetNumConformers()
                        and not rdkit.GetConformer().Is3D())
        except Exception:  # noqa: BLE001 - a conformer that does not say
            return False

    def _roughly_built(self) -> bool:
        """Whether the file's hydrogens are not a molecule's: an X-H no
        bond is (a depiction's hydrogens drawn 1.5 A out, an unfinished
        build). Such a file's hydrogens are built at ideal geometry."""
        import numpy as _np

        xyz = self.all_positions
        for hydrogen, parent in _hydrogen_parents(self.molecule, xyz, self.heavy).items():
            if not 0.085 <= float(_np.linalg.norm(xyz[hydrogen] - xyz[parent])) <= 0.12:
                return True
        return False

    def _ideal_lengths(self, pairs: list[tuple[int, int]],
                       orders: dict[tuple[int, int], float],
                       resonance: bool = True) -> list[float]:
        """Each bond's ideal length (nm): the covalent radii, shortened by its
        order, the order averaged over the molecule's resonance forms (a
        carboxylate's, an amidinium's, a nitro's two C-O, C-N or N-O alike),
        and a terminal O on P or S at a phosphate's or sulfonate's length
        whatever its charge."""
        averaged = dict(orders)
        if self.mol is not None and resonance:
            try:
                from rdkit import Chem

                forms = [m for m in Chem.ResonanceMolSupplier(Chem.Mol(self.mol)) if m is not None]
                if len(forms) > 1:
                    for i, j in pairs:
                        found = [f.GetBondBetweenAtoms(i, j) for f in forms]
                        averaged[(i, j)] = sum(b.GetBondTypeAsDouble() for b in found
                                               if b is not None) / len(forms)
            except Exception:  # noqa: BLE001 - no resonance forms to read
                pass
        steps = sorted(_ORDER_SHORTENING_NM.items())

        def shortening(order: float) -> float:
            for (low, short_low), (high, short_high) in zip(steps, steps[1:]):
                if low <= order <= high:
                    return short_low + (order - low) / (high - low) * (short_high - short_low)
            return 0.0 if order < steps[0][0] else steps[-1][1]

        lengths = []
        for i, j in pairs:
            ends = {self.elements[i], self.elements[j]}
            terminal_o = any(self.elements[k] == 8 and len(self.graph[k]) == 1 for k in (i, j))
            if terminal_o and ends & {15, 16}:
                lengths.append(0.151 if 15 in ends else 0.145)
                continue
            radii = (_COVALENT_RADIUS_NM.get(self.elements[i], 0.15)
                     + _COVALENT_RADIUS_NM.get(self.elements[j], 0.15))
            lengths.append(radii - shortening(averaged.get((i, j), 1.0)))
        return lengths

    def _hybridisation(self) -> list[str]:
        counts = [len(self.graph[k]) for k in range(self.n)]
        hydrogens = [0] * self.n
        where = {atom: k for k, atom in enumerate(self.heavy)}
        for _, parent in _hydrogen_parents(self.molecule, self.all_positions,
                                           self.heavy).items():
            if parent in where:
                hydrogens[where[parent]] += 1
        said = []
        for k in range(self.n):
            name = None
            if self.mol is not None:
                name = str(self.mol.GetAtomWithIdx(k).GetHybridization()).lower()
            if name not in ("sp", "sp2", "sp3"):
                total = counts[k] + hydrogens[k]
                element = self.elements[k]
                if element == 6:
                    name = {4: "sp3", 3: "sp2", 2: "sp"}.get(total, "sp3")
                elif element == 7:
                    name = "sp2" if total == 2 else "sp3"
                else:
                    name = "sp3"
            said.append(name)
        return said

    def _stereo(self) -> None:
        """What the file states, and each element's sign in the file."""
        import numpy as _np

        self.centres: list[tuple[int, tuple[int, int, int], bool]] = []
        self.double_bonds: list[tuple[int, int, int, int, bool]] = []
        self.unchecked: list[int] = []
        self.flat_in_file: list[int] = []
        self.stated: bool | None = None
        self.source: str | None = None
        self.why_not: str | None = None
        if self.mol is None:
            self.why_not = "the molecule carries no chemistry RDKit reads"
            return
        from rdkit import Chem

        handed = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
        stereo_bonds = (Chem.BondStereo.STEREOE, Chem.BondStereo.STEREOZ,
                        Chem.BondStereo.STEREOCIS, Chem.BondStereo.STEREOTRANS)

        def stated_in(m: Any) -> tuple[list[int], list[tuple[int, int]]]:
            Chem.AssignStereochemistry(m, cleanIt=True, force=True)
            centres = [a.GetIdx() for a in m.GetAtoms() if a.GetChiralTag() in handed]
            bonds = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in m.GetBonds()
                     if b.GetBondType() == Chem.BondType.DOUBLE
                     and b.GetStereo() in stereo_bonds]
            return centres, bonds

        stated = Chem.Mol(self.mol)
        centres, bonds = stated_in(stated)
        self.source = "the file's stereo flags"
        if not centres and not bonds and not self.flat:
            stated = _with_positions(self.mol, self.positions)
            Chem.AssignStereochemistryFrom3D(stated)
            centres, bonds = stated_in(stated)
            self.source = "the file's 3D coordinates"
        if not centres and not bonds:
            self.stated = False
            return
        reference = self.positions
        if self.flat:
            # A depiction's stereo flags, given a shape to read signs from.
            from rdkit.Chem import AllChem

            built = Chem.AddHs(stated)
            parameters = AllChem.ETKDGv3()
            parameters.randomSeed = 61453
            if AllChem.EmbedMolecule(built, parameters) != 0:
                self.why_not = "the file is flat and its stereochemistry could not be built"
                return
            reference = built.GetConformer().GetPositions()[:self.n] / 10.0
        self.stated = True
        reference = _np.asarray(reference)
        for k in centres:
            around = sorted(self.graph[k])
            if len(around) < 3 or len(self.groups.get(k, [])) >= 2:
                # Its handedness is which of two interchangeable terminal
                # atoms is which (a phosphate's oxygens), or it cannot be
                # read from heavy atoms: not a stereoisomer either way.
                self.unchecked.append(k)
                continue
            three = (around[0], around[1], around[2])
            volume = _volume(reference, k, three)
            if abs(volume) < STEREO_VOLUME_MIN:
                # The file's own centre is flat: it states no sign to hold.
                self.flat_in_file.append(k)
                continue
            self.centres.append((k, three, volume > 0.0))
        for u, v in bonds:
            near_u = sorted(self.graph[u] - {v})
            near_v = sorted(self.graph[v] - {u})
            if not near_u or not near_v or (near_u[0] in self.tucked and u in self.groups) \
                    or (near_v[0] in self.tucked and v in self.groups):
                continue
            cosine = _across(reference, near_u[0], u, v, near_v[0])
            if abs(cosine) < STEREO_DIHEDRAL_MIN:
                self.flat_in_file.extend([u, v])
                continue
            self.double_bonds.append((near_u[0], u, v, near_v[0], cosine > 0.0))

    def name(self, k: int) -> str:
        return _file_name(self.molecule, self.heavy[k])

    def double_to(self, i: int, j: int) -> bool:
        """Whether the file has a double bond between heavy atoms i and j."""
        if self.mol is None:
            return False
        bond = self.mol.GetBondBetweenAtoms(int(i), int(j))
        return bond is not None and bond.GetBondTypeAsDouble() == 2.0

    def terminal_length(self, parent: int, atom: int) -> float:
        """The length a terminal O of a carboxyl, phosphate or sulfate
        group should have for what the file makes it (=O, -OH or -O-), from
        ideal values rather than the file's own, which a weak force field
        draws P-O- 0.17 nm long. Other bonds, the file's own."""
        if self.mol is None or self.elements[atom] != 8 or self.elements[parent] not in (6, 15, 16):
            return self.expected_bond(parent, atom)
        ideal = {6: {"double": 0.121, "protonated": 0.134, "charged": 0.126},
                 15: {"double": 0.148, "protonated": 0.157, "charged": 0.151},
                 16: {"double": 0.143, "protonated": 0.157, "charged": 0.145}}

        def kind(j: int) -> str | None:
            bond = self.mol.GetBondBetweenAtoms(int(parent), int(j))
            oxygen = self.mol.GetAtomWithIdx(int(j))
            if bond is None:
                return None
            return ("double" if bond.GetBondTypeAsDouble() == 2.0
                    else "charged" if oxygen.GetFormalCharge() < 0
                    else "protonated" if oxygen.GetTotalNumHs() > 0 else None)

        mine = kind(atom)
        if mine is None:
            return self.expected_bond(parent, atom)
        group = [j for j in self.groups.get(parent, []) if self.elements[j] == 8]
        kinds = [kind(j) for j in group]
        if "charged" in kinds and mine != "protonated":
            # A carboxylate's or phosphate's =O and -O- are one bond order,
            # resonance: their mean.
            shared = [ideal[self.elements[parent]][k] for k in kinds
                      if k in ("double", "charged")]
            return sum(shared) / len(shared)
        return ideal[self.elements[parent]][mine]

    def expected_bond(self, i: int, j: int) -> float:
        """The length the file gives the bond i-j (nm)."""
        return self._expected.get((min(i, j), max(i, j)), 0.15)

    def departures(self, xyz: Any) -> tuple[Any, Any, tuple[float, int, int]]:
        """How far ``xyz`` (the heavy atoms, in the file's order) lies from
        the file's bond orders: each bond's squared departure from the
        length its order implies, each 1-3 distance's from the file's
        (nm^2), and the worst bond (its departure and atoms)."""
        import numpy as _np

        if not len(self.bond_pairs):
            return _np.zeros(0), _np.zeros(0), (0.0, 0, 0)
        bonds = _np.linalg.norm(xyz[self.bond_pairs[:, 0]] - xyz[self.bond_pairs[:, 1]], axis=1)
        deviation = _np.abs(bonds - self.bond_lengths)
        across = _np.zeros(0)
        if len(self.angle_pairs):
            distance = _np.linalg.norm(xyz[self.angle_pairs[:, 0]] - xyz[self.angle_pairs[:, 1]],
                                       axis=1)
            across = (distance - self.angle_lengths) ** 2
        worst = int(_np.argmax(deviation))
        return deviation ** 2, across, (float(deviation[worst]), int(self.bond_pairs[worst, 0]),
                                        int(self.bond_pairs[worst, 1]))

    #: The copy's noise on a bond (nm) the stated double bonds are judged
    #: with, set once it is read (:func:`_choose_match`).
    noise_nm = BOND_NOISE_NM

    def order_of(self, i: int, j: int) -> float:
        """The file's order on the bond between heavy atoms i and j (0 where
        it has none read)."""
        return self._orders.get((min(i, j), max(i, j)), 0.0)

    def position_of(self, i: int, j: int) -> int:
        """The place in :attr:`bond_pairs` of the bond between i and j."""
        if not hasattr(self, "_positions_of"):
            self._positions_of = {pair: p for p, pair in enumerate(self._pairs)}
        return self._positions_of[(min(i, j), max(i, j))]

    def charge(self, k: int) -> int:
        """The formal charge the file gives heavy atom k."""
        if self.mol is None:
            return 0
        return int(self.mol.GetAtomWithIdx(k).GetFormalCharge())

    def geometry_score(self, xyz: Any) -> tuple[float, tuple[float, int, int]]:
        """How far ``xyz`` is from the file's bond orders, as one number: the
        bonds' squared departures weighted :data:`BOND_WEIGHT`, and the 1-3
        distances' (nm^2), with the worst bond."""
        bonds, across, worst = self.departures(xyz)
        return BOND_WEIGHT * float(bonds.sum()) + float(across.sum()), worst

    def stereo_against(self, xyz: Any) -> dict[str, Any]:
        """Which stated elements ``xyz`` (heavy atoms, file order) gives the
        other way: ``differ`` (positions in ``heavy``) and ``said_differ``;
        and ``not_judged``, those ``xyz`` holds within noise (a centre's
        volume under :data:`STEREO_VOLUME_MIN`, a double bond's dihedral
        within 60 degrees of square), which neither agree nor differ."""
        import numpy as _np

        xyz = _np.asarray(xyz, dtype=float)
        differ: list[int] = []
        unjudged: list[int] = []
        flat: list[int] = []
        single: list[int] = []
        said: list[str] = []
        for k, three, sign in self.centres:
            volume = _volume(xyz, k, three)
            if abs(volume) < STEREO_FLAT_MAX:
                # Laid on an atom the structure has flat (an sp2 carbon):
                # held against a match only where another lays it on a
                # tetrahedral atom (:func:`_choose_match`).
                flat.append(k)
            elif abs(volume) < STEREO_VOLUME_MIN:
                unjudged.append(k)
            elif (volume > 0.0) != sign:
                differ.append(k)
                said.append(f"the stereocentre {self.name(k)} has the other handedness")
        for a, u, v, b, cis in self.double_bonds:
            cosine = _across(xyz, a, u, v, b)
            if _laid_as_single(self, u, v, xyz, range(self.n)):
                # Laid as a single bond, its E or Z is not the deposit's to
                # say: another order's, said by :func:`_other_order`
                # (acetylacetone's enol file on its keto deposit).
                unjudged.extend([u, v])
                single.extend([u, v])
            elif abs(cosine) < STEREO_DIHEDRAL_MIN:
                unjudged.extend([u, v])
            elif (cosine > 0.0) != cis:
                differ.extend([u, v])
                said.append(f"the double bond {self.name(u)}={self.name(v)} is the "
                            "other of E and Z")
        return {"differ": sorted(set(differ)), "said_differ": "; ".join(said),
                "not_judged": sorted(set(unjudged)), "flat": sorted(flat),
                "single": sorted(set(single))}

    def stereo_record(self, differ: bool = False,
                      not_judged: list[int] | None = None,
                      flat: list[int] | None = None,
                      undecided: list[int] | None = None,
                      notes: list[str] | None = None,
                      named: Any = None) -> dict[str, Any]:
        """What the setup record keeps of the stereochemistry checked: the
        elements the pose held within noise (``not_judged``), the stated
        centres the structure models flat (``flat``) and those whose arms
        it did not tell apart (``undecided``, taken from the file), each
        counted apart from those that agree, with ``notes`` saying why.
        ``named`` gives these lists the structure's atom names (by default
        the file's)."""
        named = named or self.name
        if self.stated is None:
            return {"stated": None, "said": f"stereochemistry not checked: {self.why_not}"}
        if not self.stated:
            return {"stated": False,
                    "said": ("stereochemistry not stated in the file" if self.flat
                             else "no stereocentre or stereo double bond in the molecule")}
        flat, undecided = list(flat or []), list(undecided or [])
        noise = sorted({*(not_judged or []), *self.flat_in_file} - set(flat) - set(undecided))
        centres, bonds = len(self.centres), len(self.double_bonds)
        apart = {*flat, *undecided, *noise}
        agreeing = (sum(1 for k, _, _ in self.centres if k not in apart)
                    + sum(1 for _, u, v, _, _ in self.double_bonds
                          if u not in apart and v not in apart))
        whole = (f"{centres} stereocentre{'' if centres == 1 else 's'} and "
                 f"{bonds} stereo double bond{'' if bonds == 1 else 's'}, "
                 f"from {self.source}")
        if differ:
            verdict = "differ from the pose"
        elif apart:
            verdict = f"{agreeing} of them agree with the pose"
        else:
            verdict = "agree with the pose"
        return {"stated": True, "source": self.source,
                "centres": [self.name(k) for k, _, _ in self.centres],
                "double_bonds": [[self.name(u), self.name(v)]
                                 for _, u, v, _, _ in self.double_bonds],
                "not_checked": [self.name(k) for k in self.unchecked],
                "not_judged": sorted({named(k) for k in [*noise, *flat]}),
                "modelled_flat": [{"atom": named(k),
                                   "said": "not judged: the structure models this atom flat"}
                                  for k in flat],
                "undecided": sorted({named(k) for k in undecided}),
                "agreeing": agreeing,
                "said": (f"{whole}, {verdict}"
                         + (f"; {len(self.unchecked)} centre"
                            f"{'' if len(self.unchecked) == 1 else 's'} whose handedness "
                            "is only which of interchangeable atoms is which not counted"
                            if self.unchecked else "")
                         + "".join(f"; {note}" for note in (notes or []))
                         + (f"; {len(noise)} held within noise, not judged"
                            if noise and notes is None else ""))}


def stereochemistry_compared(molecule: Any, pose_xyz: Any) -> dict[str, Any]:
    """The stereochemistry a ligand file states, held to ``pose_xyz`` (its
    heavy atoms, in nm, in the file's order): ``differ``, ``said_differ``
    and ``record`` (:meth:`_Chemistry.stereo_record`)."""
    import numpy as _np

    heavy = [i for i, atom in enumerate(molecule.atoms) if atom.atomic_number > 1]
    chemistry = _Chemistry(molecule, heavy, molecule.conformers[0].m_as("nanometer"))
    compared = chemistry.stereo_against(_np.asarray(pose_xyz, dtype=float))
    return {**compared, "record": chemistry.stereo_record(bool(compared["differ"]))}


#: X-H lengths for a hydrogen built from ideal geometry (nm), by element.
_X_H_NM = {5: 0.119, 6: 0.109, 7: 0.101, 8: 0.096, 14: 0.148, 15: 0.142, 16: 0.134}

#: The H-X-Y angle of an ideal hydrogen, by its heavy atom's hybridisation.
_IDEAL_ANGLE_DEG = {"sp3": 109.47, "sp2": 120.0, "sp": 180.0}


def _unit(vector: Any) -> Any:
    import numpy as _np

    length = float(_np.linalg.norm(vector))
    return vector / length if length > 1e-9 else None


def _methine_direction(units: list[Any]) -> Any:
    """The direction a hydrogen takes on an atom with three heavy bonds
    (unit vectors): equally inclined to all three, the normal of the plane
    through their tips, on the side away from them. For a tetrahedral
    centre it is the direction opposite their sum; for a flattened one
    (1VID's C1', its bonds 92 to 142 degrees apart) that sum lies near the
    plane, and a hydrogen laid along it sat 77 degrees from one bond and
    140 from another."""
    import numpy as _np

    a, b, c = units[:3]
    normal = _unit(_np.cross(b - a, c - a))
    away = -(a + b + c)
    if normal is None:
        return _unit(away)
    if float(_np.dot(normal, away)) < 0.0:
        normal = -normal
    return normal


def _frame(xyz: Any, x: int, ys: list[int], z: int | None) -> Any:
    """An orthonormal frame at x from its heavy neighbours: their bisector
    and the plane they span (two or more), or the bond to the one and the
    plane of that atom's own neighbour (one). None where it is not defined."""
    import numpy as _np

    if len(ys) >= 2:
        a, b = _unit(xyz[ys[0]] - xyz[x]), _unit(xyz[ys[1]] - xyz[x])
        if a is None or b is None:
            return None
        first = _unit(a + b)
        if first is None:
            return None
        second = _unit((a - b) - _np.dot(a - b, first) * first)
    elif len(ys) == 1 and z is not None:
        first = _unit(xyz[ys[0]] - xyz[x])
        if first is None:
            return None
        w = xyz[z] - xyz[ys[0]]
        second = _unit(w - _np.dot(w, first) * first)
    else:
        return None
    if second is None:
        return None
    return _np.column_stack([first, second, _np.cross(first, second)])


def _turning(source: Any, target: Any) -> Any:
    """The smallest rotation taking unit vector source onto target."""
    import numpy as _np

    axis = _np.cross(source, target)
    sine, cosine = float(_np.linalg.norm(axis)), float(_np.dot(source, target))
    if sine < 1e-9:
        if cosine > 0:
            return _np.eye(3)
        other = _unit(_np.cross(source, [1.0, 0.0, 0.0]))
        if other is None:
            other = _unit(_np.cross(source, [0.0, 1.0, 0.0]))
        return 2.0 * _np.outer(other, other) - _np.eye(3)
    axis = axis / sine
    cross = _np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return _np.eye(3) + sine * cross + (1 - cosine) * cross @ cross


def _about(axis_from: Any, axis_to: Any, point: Any, degrees: float) -> Any:
    """``point`` turned by ``degrees`` about the line from axis_from to axis_to."""
    import numpy as _np

    axis = _unit(axis_to - axis_from)
    angle = _np.radians(degrees)
    offset = point - axis_from
    return (axis_from + offset * _np.cos(angle) + _np.cross(axis, offset) * _np.sin(angle)
            + axis * _np.dot(axis, offset) * (1 - _np.cos(angle)))


class _Hydrogens:
    """The ligand's hydrogens: whose each is, which atoms are within two
    bonds of it, and which heavy atoms carry a rotor."""

    def __init__(self, chemistry: _Chemistry):
        self.chemistry = chemistry
        molecule, heavy = chemistry.molecule, chemistry.heavy
        self.parents = _hydrogen_parents(molecule, chemistry.all_positions, heavy)
        self.where = {atom: k for k, atom in enumerate(heavy)}
        neighbours: dict[int, set[int]] = {i: set() for i in range(len(molecule.atoms))}
        for k, around in enumerate(chemistry.graph):
            neighbours[heavy[k]].update(heavy[j] for j in around)
        for hydrogen, parent in self.parents.items():
            neighbours[hydrogen].add(parent)
            neighbours[parent].add(hydrogen)
        self.near = {h: {h} | neighbours[h] | {b for a in neighbours[h] for b in neighbours[a]}
                     for h in self.parents}
        self.carried: dict[int, list[int]] = {}
        #: Hydrogens left where the file's E/Z puts them though crowded.
        self.crowded: list[int] = []
        for hydrogen, parent in sorted(self.parents.items()):
            self.carried.setdefault(parent, []).append(hydrogen)

    def heavy_around(self, parent: int) -> list[int]:
        k = self.where.get(parent)
        if k is None:
            return []
        return [self.chemistry.heavy[j] for j in sorted(self.chemistry.graph[k])]

    def torsion_reference(self, parent: int, ys: list[int]) -> int | None:
        if len(ys) != 1:
            return None
        k = self.where[ys[0]]
        others = sorted(self.chemistry.graph[k] - {self.where[parent]},
                        key=lambda j: (j in self.chemistry.tucked, j))
        return self.chemistry.heavy[others[0]] if others else None

    def closest(self, xyz: Any, hydrogens: Any = None) -> tuple[float, int, int] | None:
        """The hydrogen nearest an atom three or more bonds from it: the
        distance (nm), the hydrogen and the atom."""
        import numpy as _np

        best: tuple[float, int, int] | None = None
        for hydrogen in (self.parents if hydrogens is None else hydrogens):
            distances = _np.linalg.norm(xyz - xyz[hydrogen], axis=1)
            distances[list(self.near[hydrogen])] = _np.inf
            other = int(_np.argmin(distances))
            if _np.isfinite(distances[other]) and (best is None or distances[other] < best[0]):
                best = (float(distances[other]), int(hydrogen), other)
        return best

    def worst_angle(self, xyz: Any) -> tuple[float, int, int, int] | None:
        """The H-X-Y angle furthest from the file's (degrees off, H, X, Y),
        or for a flat or roughly built file, whose hydrogens are built at
        ideal geometry, from the ideal angle for the atom's hybridisation
        (109.5 degrees sp3, 120 sp2, 180 sp), read from the coordinates alone:
        tetracycline's C4 hydrogen on 2TRT, equally inclined to bonds the
        deposit has flattened, reads 16.5."""
        import numpy as _np

        reference = self.chemistry.all_positions
        ideal = self.chemistry.rough
        worst: tuple[float, int, int, int] | None = None

        def angle(p: Any, h: int, x: int, y: int) -> float:
            a, b = _unit(p[h] - p[x]), _unit(p[y] - p[x])
            if a is None or b is None:
                return 0.0
            return float(_np.degrees(_np.arccos(_np.clip(_np.dot(a, b), -1.0, 1.0))))

        for parent, carried in self.carried.items():
            ys = self.heavy_around(parent)
            k = self.where.get(parent)
            hybrid = self.chemistry.hybrid[k] if k is not None else "sp3"
            for hydrogen in carried:
                for y in ys:
                    if ideal:
                        target = _IDEAL_ANGLE_DEG.get(hybrid, 109.47)
                    else:
                        target = angle(reference, hydrogen, parent, y)
                    off = abs(angle(xyz, hydrogen, parent, y) - target)
                    if worst is None or off > worst[0]:
                        worst = (off, hydrogen, parent, y)
        return worst

    def inside(self, xyz: Any) -> list[int]:
        """Hydrogens laid inside their own bonds: on an atom with two or
        more heavy neighbours, every H-X-Y under 90 degrees."""
        import numpy as _np

        found = []
        for hydrogen, parent in self.parents.items():
            ys = self.heavy_around(parent)
            if len(ys) < 2:
                continue
            h = _unit(xyz[hydrogen] - xyz[parent])
            if h is not None and all(float(_np.dot(h, _unit(xyz[y] - xyz[parent]))) > 0.0
                                     for y in ys if _unit(xyz[y] - xyz[parent]) is not None):
                found.append(hydrogen)
        return found

    def worst_length(self, xyz: Any) -> float | None:
        """The X-H length furthest from the file's (or the ideal one), nm."""
        import numpy as _np

        worst = None
        for hydrogen, parent in self.parents.items():
            if self.chemistry.flat:
                wanted = _X_H_NM.get(self.chemistry.molecule.atoms[parent].atomic_number, 0.1)
            else:
                wanted = float(_np.linalg.norm(self.chemistry.all_positions[hydrogen]
                                               - self.chemistry.all_positions[parent]))
            off = abs(float(_np.linalg.norm(xyz[hydrogen] - xyz[parent])) - wanted)
            worst = off if worst is None else max(worst, off)
        return worst


def _build_hydrogens(moved: Any, hydrogens: _Hydrogens) -> None:
    """Each hydrogen from internal coordinates, its heavy atoms already in
    place in ``moved``.

    From a 3D file, each keeps its X-H length and its H-X-Y angles: laid in
    the frame of its heavy atom's own bonds (their bisector and plane, or
    the one bond and the plane of the next atom's neighbour), so the bond to
    a single neighbour is kept exactly and the torsion about it is the
    file's. A hydrogen on an atom with three heavy neighbours is carried
    across with them, the file's atom and its three neighbours laid on the
    structure's. From a flat file, whose coordinates are a depiction, or one
    roughly built, each is built at the ideal length and angles for its
    atom's hybridisation. Then each rotor whose hydrogens come within
    :data:`HYDROGEN_CLEARANCE_NM` of an atom three or more bonds away is
    turned: an sp3 one (OH, NH3+, CH3, SH) to its clearest torsion, a planar
    one (an amide's NH2, a vinyl's =CH2, an amidinium's or guanidinium's
    NH2, any sp2 atom's) only to the other of its two in-plane positions, so
    no planar group is turned out of its plane.
    """
    import numpy as _np

    chemistry = hydrogens.chemistry
    file = chemistry.all_positions
    whole = _superposing_rotation(chemistry.positions, moved[chemistry.heavy])
    rotors: list[tuple[int, int]] = []
    for parent, carried in hydrogens.carried.items():
        ys = hydrogens.heavy_around(parent)
        z = hydrogens.torsion_reference(parent, ys)
        if len(ys) == 1:
            rotors.append((parent, ys[0]))
        if chemistry.rough:
            _ideal(moved, hydrogens, parent, carried, ys, z)
            k = hydrogens.where[parent]
            if (len(carried) == 1 and len(ys) == 1 and z is not None
                    and chemistry.hybrid[k] in ("sp2", "sp")
                    and chemistry.double_to(k, hydrogens.where[ys[0]])):
                # An imine's =N-H keeps the side the file draws it on, 2D or
                # not: its two in-plane places are its E and Z.
                drawn = _across(file, carried[0], parent, ys[0], z)
                built = _across(moved, carried[0], parent, ys[0], z)
                if abs(drawn) >= STEREO_DIHEDRAL_MIN and (drawn > 0) != (built > 0):
                    moved[carried[0]] = _about(moved[ys[0]], moved[parent],
                                               moved[carried[0]], 180)
            continue
        if len(ys) >= 3:
            k = hydrogens.where[parent]
            held = chemistry.groups.get(k, [])
            alike = any(sum(1 for y in ys if hydrogens.where[y] in held
                            and chemistry.elements[hydrogens.where[y]] == element) >= 2
                        for element in {chemistry.elements[hydrogens.where[y]] for y in ys})
            # The file's local handedness against the structure's: a centre
            # the graph makes symmetric (a piperidin-4-yl, a cyclopentyl) may
            # be given its two arms the other way round, and no rotation lays
            # a mirrored tetrahedron on the file's.
            three = (ys[0], ys[1], ys[2])
            mirrored = (_volume(file, parent, three) > 0) != (_volume(moved, parent, three) > 0)
            if alike or mirrored:
                # Its hydrogen has one place, whichever neighbour is which:
                # opposite their bonds, at the file's length. Laid with them
                # it could be flipped through the centre.
                away = _methine_direction([_unit(moved[y] - moved[parent]) for y in ys])
                if away is not None:
                    for hydrogen in carried:
                        moved[hydrogen] = moved[parent] + away * float(
                            _np.linalg.norm(file[hydrogen] - file[parent]))
                    continue
            else:
                # Carried across with its atom's three neighbours, so its
                # angles are the file's to within how the structure's centre
                # differs. Opposite the bonds, a methine's hydrogen lay up to
                # 14 degrees from where the conformer had it.
                around = [parent, *ys]
                turn = _superposing_rotation(file[around], moved[around])
                away = _methine_direction([_unit(moved[y] - moved[parent]) for y in ys])
                for hydrogen in carried:
                    moved[hydrogen] = moved[parent] + turn @ (file[hydrogen] - file[parent])
                    # On the far side of its three neighbours, or laid there:
                    # a hydrogen inside its own bonds (every H-C-Y 72 degrees)
                    # is no molecule's.
                    if away is not None and float(_np.dot(moved[hydrogen] - moved[parent],
                                                          away)) <= 0.0:
                        moved[hydrogen] = moved[parent] + away * float(
                            _np.linalg.norm(file[hydrogen] - file[parent]))
                continue
        source, target = _frame(file, parent, ys, z), _frame(moved, parent, ys, z)
        if source is not None and target is not None:
            turn = target @ source.T
        elif len(ys) == 1:
            first = _unit(file[ys[0]] - file[parent])
            second = _unit(moved[ys[0]] - moved[parent])
            turn = _turning(first, second) if first is not None and second is not None else whole
        else:
            turn = whole
        for hydrogen in carried:
            moved[hydrogen] = moved[parent] + turn @ (file[hydrogen] - file[parent])
    _follow_stated_handedness(moved, hydrogens)
    for _ in range(2):
        for parent, y in rotors:
            carried = hydrogens.carried[parent]
            found = hydrogens.closest(moved, carried)
            if found is None or found[0] >= HYDROGEN_CLEARANCE_NM:
                continue
            planar = chemistry.hybrid[hydrogens.where[parent]] in ("sp2", "sp")
            if planar and len(carried) == 1 and chemistry.double_to(
                    hydrogens.where[parent], hydrogens.where[y]):
                # An imine's =N-H: its two in-plane places are its E and Z,
                # another isomer; it keeps the file's, crowded or not.
                if carried[0] not in hydrogens.crowded:
                    hydrogens.crowded.append(carried[0])
                continue
            start = {h: moved[h].copy() for h in carried}
            best = (found[0], 0)
            for degrees in ((180,) if planar else range(5, 360, 5)):
                for h in carried:
                    moved[h] = _about(moved[y], moved[parent], start[h], degrees)
                trial = hydrogens.closest(moved, carried)
                if trial is not None and trial[0] > best[0]:
                    best = (trial[0], degrees)
            for h in carried:
                moved[h] = _about(moved[y], moved[parent], start[h], best[1])


def _hydrogen_side(xyz: Any, x: int, y1: int, y2: int, h: int) -> float:
    """The signed volume of a centre's hydrogen with two of its heavy
    neighbours: its side, which with the heavy atoms gives its handedness."""
    import numpy as _np

    return float(_np.dot(_np.cross(xyz[y1] - xyz[x], xyz[y2] - xyz[x]), xyz[h] - xyz[x]))


def _follow_stated_handedness(moved: Any, hydrogens: _Hydrogens) -> None:
    """At each stated centre with one hydrogen that the structure models
    flat or holds within noise (its heavy atoms' volume under
    :data:`STEREO_VOLUME_MIN`), the hydrogen on the side the file's stated
    handedness gives, along the direction equally inclined to its bonds.
    It took the side the deposit's few hundredths of an Angstrom of lean
    gave, and DHT's file on 1I37 was built as 5-beta-DHT."""
    import numpy as _np

    chemistry = hydrogens.chemistry
    for k, three, sign in chemistry.centres:
        parent = chemistry.heavy[k]
        carried = hydrogens.carried.get(parent, [])
        if len(carried) != 1:
            continue
        ys = [chemistry.heavy[j] for j in three]
        if abs(_volume(moved, parent, tuple(ys))) >= STEREO_VOLUME_MIN:
            continue
        units = [_unit(moved[y] - moved[parent]) for y in ys]
        if any(u is None for u in units):
            continue
        direction = _methine_direction(units)
        if direction is None:
            continue
        hydrogen = carried[0]
        length = float(_np.linalg.norm(moved[hydrogen] - moved[parent]))
        # For a tetrahedral centre the hydrogen's side over two neighbours
        # is the opposite of the three heavy neighbours' volume.
        for candidate in (direction, -direction):
            moved[hydrogen] = moved[parent] + candidate * length
            if (_hydrogen_side(moved, parent, ys[0], ys[1], hydrogen) > 0) != sign:
                break


def _other_isomer_said(undecided: list[dict[str, Any]]) -> str:
    """What a tie that turns a centre says of the other isomer: that its
    lengths lean to it, where its match scores better, or that it fits too."""
    if any(entry.get("lean") for entry in undecided):
        return "the lengths lean to the other isomer, within this copy's noise"
    return "the other isomer fits too"


def _held_within_noise_said(chemistry: "_Chemistry", noise: list[int], named: Any,
                            single: Any = ()) -> list[str]:
    """Each stated element held within noise, by ``named``'s names: a
    double bond once, as a bond ("C3=C5 held within noise"), a centre as
    itself. A double bond in ``single`` the deposit lays as a single bond
    ("C4=C7 laid as a single bond, its E or Z not judged")."""
    left = list(noise)
    said = []
    for _, u, v, _, _ in chemistry.double_bonds:
        if u in left and v in left:
            said.append(f"{named(u)}={named(v)} laid as a single bond, its E or Z not judged"
                        if u in single and v in single else
                        f"{named(u)}={named(v)} held within noise")
            left.remove(u)
            left.remove(v)
    return said + [f"{named(k)} held within noise" for k in left]


#: The least volume a stated centre is left leaning the file's way where its
#: terminal atom was laid on the file's side (:func:`_hold_stated_sides`).
HELD_LEAN_MIN = 0.15


def _hold_stated_sides(moved: Any, hydrogens: _Hydrogens) -> tuple[list[tuple[int, int]],
                                                                     tuple | None]:
    """The file's handedness at each stated centre without a hydrogen, and
    each stated double bond, that the structure holds within noise the
    other way (a volume under :data:`STEREO_VOLUME_MIN`, a dihedral within
    60 degrees of square), on ``moved``'s heavy atoms as laid.

    A centre whose lean one terminal neighbour can turn (a sulfoxide's O, a
    sulfonium's methyl) has that atom reflected through the plane of the
    centre and its other two neighbours, which keeps its bond and both its
    angles: returned as (centre, atom), file positions in ``heavy``. Any
    other such centre, or such a double bond, is returned as the problem:
    ("centre", k, volume) or ("bond", u, v, cosine). Left as deposited, the
    ligand relaxed to the other isomer while the line said "placed as
    stated"."""
    import numpy as _np

    chemistry = hydrogens.chemistry
    heavy = chemistry.heavy
    turned: list[tuple[int, int]] = []
    for k, three, sign in chemistry.centres:
        parent = heavy[k]
        if len(hydrogens.carried.get(parent, [])) == 1:
            continue  # its hydrogen takes the file's side (_follow_stated_handedness)
        ys = tuple(heavy[j] for j in three)
        volume = _volume(moved, parent, ys)
        if abs(volume) >= STEREO_VOLUME_MIN or (volume > 0.0) == sign:
            continue
        ends = [j for j in three if len(chemistry.graph[j]) == 1] \
            if len(chemistry.graph[k]) == 3 else []
        ends.sort(key=lambda j: chemistry.elements[j] == 6)
        if not ends:
            return turned, ("centre", k, volume)
        end = ends[0]
        others = [heavy[j] for j in three if j != end]
        normal = _unit(_np.cross(moved[others[0]] - moved[parent],
                                 moved[others[1]] - moved[parent]))
        if normal is None:
            return turned, ("centre", k, volume)
        offset = moved[heavy[end]] - moved[parent]
        moved[heavy[end]] = moved[parent] + offset - 2.0 * float(_np.dot(offset, normal)) * normal
        # Leaning at least :data:`HELD_LEAN_MIN` the file's way: a centre
        # mirrored from a flat deposit's few hundredths still relaxed either
        # way. Turned about the centre, its bond kept.
        apart = _unit(-(_unit(moved[others[0]] - moved[parent])
                        + _unit(moved[others[1]] - moved[parent])))
        radius = float(_np.linalg.norm(offset))
        if apart is not None and abs(_volume(moved, parent, ys)) < HELD_LEAN_MIN:
            apart = _unit(apart - float(_np.dot(apart, normal)) * normal)
            for step in range(1, 121):
                for side in (1.0, -1.0):
                    angle = side * step * 0.01
                    moved[heavy[end]] = moved[parent] + radius * (
                        _np.cos(angle) * apart + _np.sin(angle) * normal)
                    volume = _volume(moved, parent, ys)
                    if (volume > 0.0) == sign and abs(volume) >= HELD_LEAN_MIN:
                        break
                else:
                    continue
                break
        turned.append((k, end))
    for a, u, v, b, cis in chemistry.double_bonds:
        cosine = _across(moved, heavy[a], heavy[u], heavy[v], heavy[b])
        if abs(cosine) < STEREO_DIHEDRAL_MIN and (cosine > 0.0) != cis \
                and not _laid_as_single(chemistry, u, v, moved, heavy):
            return turned, ("bond", u, v, cosine)
    return turned, None


def _ideal(moved: Any, hydrogens: _Hydrogens, parent: int, carried: list[int],
           ys: list[int], z: int | None) -> None:
    """A heavy atom's hydrogens at ideal lengths and angles for its
    hybridisation, about its heavy neighbours as placed."""
    import numpy as _np

    chemistry = hydrogens.chemistry
    k = hydrogens.where[parent]
    kind = chemistry.hybrid[k]
    length = _X_H_NM.get(chemistry.elements[k], 0.1)
    x = moved[parent]
    units = [u for u in (_unit(moved[y] - x) for y in ys) if u is not None]
    directions: list[Any] = []
    if not units:
        directions = [_np.array(d) / _np.sqrt(3.0) for d in
                      ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1))]
    elif len(units) >= 3:
        directions = [_methine_direction(units)]
    elif len(units) == 2 and kind != "sp3":
        away = _unit(-sum(units))
        directions = [away if away is not None else _unit(_np.cross(units[0], units[1]))]
    elif len(units) == 2:
        away = _unit(-(units[0] + units[1]))
        normal = _unit(_np.cross(units[0], units[1]))
        half = _np.radians(54.75)
        directions = [away * _np.cos(half) + normal * _np.sin(half),
                      away * _np.cos(half) - normal * _np.sin(half)]
        if len(carried) == 1:
            # One hydrogen on a pyramidal atom: on the side with more room.
            def room(d: Any) -> float:
                spot = x + length * d
                others = [moved[a] for a in chemistry.heavy if a != parent]
                return min(float(_np.linalg.norm(spot - o)) for o in others) if others else 0.0
            directions.sort(key=room, reverse=True)
    else:
        bond = units[0]
        reference = (moved[z] - moved[ys[0]]) if z is not None else _np.array([1.0, 0.0, 0.0])
        across = _unit(reference - _np.dot(reference, bond) * bond)
        if across is None:
            across = _unit(_np.cross(bond, [0.0, 0.0, 1.0]))
        if across is None:
            across = _unit(_np.cross(bond, [0.0, 1.0, 0.0]))
        third = _np.cross(bond, across)
        alpha = _np.radians(_IDEAL_ANGLE_DEG.get(kind, 109.47))
        steps = {"sp3": (180.0, 60.0, -60.0), "sp2": (180.0, 0.0)}.get(kind, (0.0,))
        for tau in (_np.radians(t) for t in steps):
            directions.append(_np.cos(alpha) * bond + _np.sin(alpha)
                              * (_np.cos(tau) * across + _np.sin(tau) * third))
    for hydrogen, direction in zip(carried, directions + directions):
        moved[hydrogen] = x + length * direction


#: A residue name as a PDB file and the force fields write it: one to three
#: letters or digits.
_NAME = re.compile(r"[A-Za-z0-9]{1,3}")

#: A title taken as a residue name: one to three letters or digits, the first
#: a letter, since `resname 07` is not a selection MDTraj can read.
_TITLE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,2}")

#: Residue names a molecule's title is never given as its own: the standard
#: residues of proteins and nucleic acids, their caps, water and the ions.
#: A ligand called ALA, HOH or CL would be selected with the protein, the
#: solvent or the ions by every analysis that selects by residue name.
_STANDARD_RESIDUES = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "SEC", "PYL", "ACE", "NME", "NMA", "UNK",
    "HID", "HIE", "HIP", "HSD", "HSE", "HSP", "ASH", "GLH", "LYN", "CYM",
    "CYX", "MSE", "SEP", "TPO", "PTR",
    "DA", "DC", "DG", "DT", "DI", "DU", "A", "C", "G", "U", "I", "T",
})


def _polymer_water_and_ion_names() -> frozenset[str]:
    """The names that are never a ligand: the residues of proteins and
    nucleic acids, their caps and variants, water and the ions."""
    from fastmdxplora.protein_names import PROTEIN_VARIANTS
    from fastmdxplora.setup.heterogens import ION_NAMES, WATER_NAMES

    return _STANDARD_RESIDUES | PROTEIN_VARIANTS | ION_NAMES | WATER_NAMES


def _reserved_names() -> frozenset[str]:
    """The names a title is not taken as: those above, and every name
    MDTraj's `protein` keyword selects, some of which are cofactors (SAH)."""
    reserved = _polymer_water_and_ion_names()
    try:
        # Every name MDTraj's `protein` keyword selects.
        from mdtraj.core.residue_names import _PROTEIN_RESIDUES
    except ImportError:
        return reserved
    return reserved | frozenset(_PROTEIN_RESIDUES)


def _generated_names():
    """Residue names for molecules whose title cannot be one: L01 to L99,
    then LA0 to LZZ."""
    for number in range(1, 100):
        yield f"L{number:02d}"
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for first in digits[10:]:
        for second in digits:
            yield f"L{first}{second}"


def _split_records(text: str, suffix: str) -> list[str]:
    """The molecules of an SDF or MOL2 text, each as its own record.

    Done on the text rather than through the toolkit, so a file is divided
    before OpenFF is needed and each record reaches the toolkit exactly as it
    was written.
    """
    if suffix == ".sdf":
        # Each record keeps its own `$$$$` line, so the blank line that ends
        # its last data item stays; only a last record written without one
        # is given it.
        records: list[str] = []
        start = 0
        for end in re.finditer(r"(?m)^\$\$\$\$[^\n]*(?:\n|\Z)", text):
            record = text[start:end.end()]
            if text[start:end.start()].strip():
                records.append(record if record.endswith("\n") else record + "\n")
            start = end.end()
        rest = text[start:]
        if rest.strip():
            records.append(rest + ("" if rest.endswith("\n") else "\n") + "$$$$\n")
        return records
    parts = re.split(r"(?m)^(?=@<TRIPOS>MOLECULE)", text)
    return [r for r in parts if r.startswith("@<TRIPOS>MOLECULE")]


def _record_title(record: str, suffix: str) -> str:
    """The molecule's own name: an SDF record's first line, a MOL2
    molecule's line after `@<TRIPOS>MOLECULE`."""
    lines = record.splitlines()
    if suffix == ".sdf":
        return lines[0].strip() if lines else ""
    return lines[1].strip() if len(lines) > 1 else ""


def title_can_name(name: str) -> bool:
    """Whether a molecule's title can be its residue name."""
    return bool(_TITLE_NAME.fullmatch(name)) and name.upper() not in _reserved_names()


def _as_name(stem: str) -> str:
    """The residue name a file's stem gives its ligand, as preparation reads it."""
    return stem.strip().upper()[:3] or "LIG"


def expand_ligand_files(ligands: Any, out_dir: str | Path,
                        told: list[str] | None = None,
                        named: bool = False) -> Any:
    """Give every molecule its own file, so a file of several reads as several.

    A list of N files is already N ligands; one file holding N molecules is
    the same thing written differently, and is divided here into the files it
    would otherwise have been supplied as, in the order the molecules are
    written. Everything after this sees one molecule per file, which is what
    :func:`load_ligand` and the system build expect.

    Each new file is named for its molecule's title where that is a usable
    residue name: one to three letters or digits, not a standard residue,
    water or ion name, and not shared with another molecule being divided.
    The others are named ``L01``, ``L02`` ... in turn, skipping every name
    already in use. The file stem becomes the residue name, so the names
    differ; a title that is the name of a ligand supplied as its own file is
    refused (``setup.ligand.names_collide``), since whether the two are the
    same component cannot be told from the files.

    ``told`` collects one sentence per divided file, for the person.
    ``named`` says the caller gives every ligand its residue name, so a title
    that is a whole file's name is no clash.

    Returns ``ligands`` itself, unchanged, when no file holds more than one
    molecule.
    """
    as_list = [ligands] if isinstance(ligands, (str, Path)) else list(ligands)
    divided: dict[int, tuple[Path, str, list[str], list[str]]] = {}
    for index, entry in enumerate(as_list):
        path = Path(entry).expanduser()
        suffix = path.suffix.lower()
        if suffix not in (".sdf", ".mol2") or not path.is_file():
            continue                   # load_ligand says what is wrong with it
        # The bytes as written, whatever their encoding: a record reaches the
        # toolkit as it was in the file.
        with open(path, encoding="utf-8", errors="surrogateescape",
                  newline="") as handle:
            text = handle.read()
        records = _split_records(text, suffix)
        if len(records) > 1:
            divided[index] = (path, suffix, records,
                              [_record_title(r, suffix) for r in records])
    if not divided:
        return ligands

    # The names the files supplied whole give their ligands.
    whole = {index: _as_name(Path(entry).expanduser().stem)
             for index, entry in enumerate(as_list) if index not in divided}
    taken = set(whole.values())
    reserved = _reserved_names()
    titles = [title.upper() for _, _, _, found in divided.values()
              for title in found if _TITLE_NAME.fullmatch(title)]
    usable = {t for t in titles
              if titles.count(t) == 1 and t not in reserved}
    clashing = sorted(usable & taken)
    if clashing and not named:
        said = (f"A molecule in a ligand file of several is called "
                f"{clashing[0]}, the name a ligand supplied as its own file "
                "already takes" if len(clashing) == 1 else
                f"Molecules in a ligand file of several are called "
                f"{', '.join(clashing)}, names ligands supplied as their own "
                "files already take")
        raise LigandError(
            f"{said}. Two different "
            "molecules cannot share a residue name, and whether these are "
            "one component cannot be told from the files. Give the molecule "
            "another title in its file (an SDF record's first line, a MOL2 "
            "molecule's line after @<TRIPOS>MOLECULE), or rename the other "
            "file."
        , code="setup.ligand.names_collide", names=clashing,
            option="setup.ligand")
    if named:
        usable -= taken
    taken |= usable
    fresh = (name for name in _generated_names()
             if name not in taken and name not in reserved)

    out_dir = Path(out_dir)
    out: list[str] = []
    for index, entry in enumerate(as_list):
        if index not in divided:
            out.append(str(entry))
            continue
        path, suffix, records, found = divided[index]
        out_dir.mkdir(parents=True, exist_ok=True)
        parts: list[str] = []
        for number, (record, title) in enumerate(zip(records, found), start=1):
            name = title.upper() if title.upper() in usable else next(fresh, None)
            if name is None:
                raise LigandError(
                    "The ligand files hold more molecules than there are "
                    "residue names to give them. A ligand file is a few "
                    "molecules beside a protein; a library of compounds is "
                    "studied one study at a time."
                , code="setup.ligand.too_many_molecules",
                    option="setup.ligand")
            target = out_dir / f"{name}{suffix}"
            with open(target, "w", encoding="utf-8", errors="surrogateescape",
                      newline="") as handle:
                handle.write(record)
            out.append(str(target))
            if title.upper() == name:
                parts.append(name)
            elif title:
                shown = title.encode("utf-8", "surrogateescape").decode(
                    "utf-8", "replace")
                parts.append(f"'{shown}' as {name}")
            else:
                parts.append(f"molecule {number} (untitled) as {name}")
        sentence = (f"The ligand file {path.name} holds {len(records)} "
                    f"molecules, each prepared as its own ligand: "
                    f"{', '.join(parts)}.")
        logger.info("%s", sentence)
        if told is not None:
            told.append(sentence)
    return out


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

    # A file of several molecules is divided by :func:`expand_ligand_files`
    # before it gets here, so a list means a caller reached this directly.
    if isinstance(molecule, list):
        if len(molecule) != 1:
            raise LigandError(
                f"Ligand file {path.name!r} contains {len(molecule)} "
                f"molecules, and this loads one at a time; pass it through the "
                f"setup phase, or divide it with expand_ligand_files."
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
            "given neutral), or drop `ligand_net_charge` and let the file "
            "speak for itself."
        , code="setup.chemistry.charge_contradicted", resname=name, stated=net_charge)
    resolved_charge = net_charge if net_charge is not None else inferred
    logger.info(
        "Loaded ligand %s from %s (net charge=%s, taken from the file's own "
        "formal charges). Supplying chemistry as a file sets the protonation "
        "state: an ideal SDF is given in one particular form, and it is the "
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
