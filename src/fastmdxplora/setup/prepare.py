"""System preparation: solvate, ionize, parameterize.

Takes a fully-protonated PDB (typically the output of PDBFixer) and
produces a simulation-ready OpenMM ``System`` together with a starting
``State`` and a topology PDB. The outputs are serialized to disk so the
simulation phase can load them without re-doing any of this work.

Pipeline
--------
  1. Load the topology + positions from the prepared PDB.
  2. Build an OpenMM ``ForceField`` from the force-field XMLs.
  3. Use ``Modeller`` to add a water box (with configurable padding) and
     ions for charge neutralization + a target ionic concentration.
  4. Call ``ForceField.createSystem(...)`` to apply parameters and obtain
     an OpenMM ``System`` with all the standard constraints/options.
  5. Build an OpenMM ``Context`` to capture the initial ``State``
     (positions, velocities, box vectors).
  6. Serialize ``System`` and ``State`` to XML and write a topology PDB
     of the solvated system.

Default force fields
--------------------
``forcefield: auto``, which resolves to ``amber-openff``:
``amber14-all.xml`` + ``amber14/tip3p.xml`` with TIP3P water, and
OpenFF for any small molecule. It is the default because it is the
ligand-capable choice, so a structure with a bound ligand needs no
flags. ``charmm36`` is available by name and parameterises no ligand;
this docstring named it as the default for some time, and it never was.
Override with ``forcefield`` for a registered name, or with
``force_field`` and ``water_model`` for raw OpenMM XML.

Outputs
-------
  - ``solvated.pdb`` -- topology + positions after solvation
  - ``topology.pdb`` -- alias of ``solvated.pdb`` (the canonical
    "topology" the analysis phase consumes)
  - ``system.xml`` -- serialized OpenMM ``System`` (force field applied)
  - ``state.xml`` -- serialized initial ``State``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmdxplora.setup.forcefields import (
    available_forcefields,
    nonbonded_scheme,
    resolve_forcefield,
)
from fastmdxplora.setup.ligand import load_ligand, pose_by_policy
from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import MissingResultError
from fastmdxplora.refusals import BackendUnavailable

logger = get_logger("setup.prepare")


# ---------------------------------------------------------------------------
# Defaults — sensible general-purpose biomolecular MD settings
# (The default force field now lives in setup/forcefields.py, the single
# source of truth for the named force-field registry.)
# ---------------------------------------------------------------------------
DEFAULT_PADDING_NM = 1.0
DEFAULT_IONIC_STRENGTH_M = 0.15
# DEFAULT_PH is deliberately absent. It existed here, unread by anything, and
# stayed at 7.0 when the pH default became 7.4 -- an unused constant stating a
# value that is no longer true is a trap for whoever reaches for it next. The
# pH default lives in the schema, and the setup phase reads it from there.

def _refuse_an_implausible_structure(topology: Any, positions: Any, unit: Any) -> None:
    """Stop before solvating something no protein could be.

    Nothing looked at the prepared structure before it went into a box. 6B73
    arrived 51 nm across for 1,104 residues -- rebuilt terminal residues had
    walked off in every direction -- and the first anyone heard of it was
    `addMembrane` failing with a NaN after ten minutes of packing lipids
    across that face.

    A folded protein's longest dimension grows roughly as the cube root of
    its residue count: about 3 nm for 100 residues, 7 nm for 1,000, 15 nm for
    10,000. The bound here is several times that, so an elongated fibril or a
    long coiled coil passes and only a structure that is not one object at
    all is refused. Measured before solvation, because afterwards the cost of
    finding out is minutes and the message names none of the cause.
    """
    import numpy as np

    try:
        if hasattr(positions, "value_in_unit"):
            coordinates = np.asarray(
                positions.value_in_unit(unit.nanometer), dtype=float)
        else:
            coordinates = np.asarray(
                [[float(p[0]), float(p[1]), float(p[2])] for p in positions],
                dtype=float)
        residues = sum(1 for _ in topology.residues())
    except (AttributeError, TypeError, ValueError):
        return
    if coordinates.size == 0 or residues <= 0:
        return

    extent = float(np.max(coordinates.max(axis=0) - coordinates.min(axis=0)))
    plausible = 4.0 * (residues ** (1.0 / 3.0))
    if extent <= plausible:
        return

    raise StudyError(
        f"The prepared structure is {extent:.0f} nm across for {residues} "
        f"residues, where a folded structure of that size would be under "
        f"{plausible:.0f} nm. Something in it is far from everything else.\n\n"
        "The usual cause is residues rebuilt where there was nothing to "
        "rebuild from. Unresolved residues past the end of a chain are "
        "anchored at one end only, so what is built there is placed rather "
        "than determined, and it can end up anywhere; FastMDXplora does not "
        "build them unless asked, so check setup.build_missing_termini. "
        "Solvating this would produce a box big enough to hold it, which for "
        "a membrane means packing lipids across the whole face -- minutes of "
        "work to arrive at a structure nobody would recognise.\n\n"
        "Open setup/prepared.pdb and look at what is far from the rest."
    , code="setup.structure.implausible_extent")


#: How narrow each box shape is, as the ratio of its smallest periodic
#: width to the `size` OpenMM builds it from. Read off the box vectors
#: `Modeller.addSolvent` uses: a cube is (1,0,0),(0,1,0),(0,0,1); a
#: dodecahedron (1,0,0),(0,1,0),(1/2,1/2,sqrt2/2); a truncated octahedron
#: (1,0,0),(-1/3,2sqrt2/3,0),(-1/3,-sqrt2/3,sqrt6/3). OpenMM constrains the
#: *diagonal* of that matrix, so the narrowest width is the smallest
#: diagonal entry and not the edge length the padding appears to control.
NARROWEST_WIDTH_PER_SIZE: dict[str, float] = {
    "cube": 1.0,
    "dodecahedron": 2 ** 0.5 / 2,      # 0.7071
    "octahedron": 6 ** 0.5 / 3,        # 0.8165
}

#: How much room to leave above OpenMM's hard floor of twice the cutoff.
#:
#: The floor is checked on the box as solvated, and the next phase runs a
#: barostat whose whole job is to shrink it: solvation leaves a gap around
#: the solute, and closing it is a 2-3.5% linear contraction on the systems
#: measured here. A box that clears the floor by less than that cannot
#: equilibrate to the right density, however long it runs.
#:
#: Ten percent covers the contraction with room for the barostat's own
#: fluctuations. It is a judgement about how much water to buy for safety
#: rather than a derived constant, and it is here as one number so it can
#: be argued with.
NPT_CONTRACTION_MARGIN = 1.10


def padding_that_reaches(
    *,
    smallest_nm: float,
    padding_nm: float,
    nonbonded_cutoff_nm: float,
    box_shape: str,
    margin: float = NPT_CONTRACTION_MARGIN,
) -> float:
    """The padding whose box clears twice the cutoff, with room to shrink.

    Separated from the solvation loop so the arithmetic can be checked
    without building a system, because the arithmetic is where this was
    wrong. The loop asked for `padding + shortfall / 2`, which is the
    cube's relation between padding and width: a cube's narrowest dimension
    is `maxSize + 2 * padding`, so a shortfall of 0.30 nm needs 0.15 nm
    more padding.

    A dodecahedron's narrowest dimension is that quantity divided by
    sqrt(2), so the same padding buys sqrt(2) less width and the loop
    undershot by exactly that factor. Measured on the V3 run it killed:
    from 1.20 nm padding the box came out 1.70 nm across, the loop grew to
    1.45 nm aiming at 2.20 nm, and got 2.05 -- against a floor of 2.00.
    NPT then contracted 1.9% and the run died on the second barostat move.

    The general relation is `d(width)/d(padding) = 2 * f`, with f the
    shape's narrowest width per unit size.
    """
    factor = NARROWEST_WIDTH_PER_SIZE.get(str(box_shape).lower(), 1.0)
    wanted = 2.0 * float(nonbonded_cutoff_nm) * float(margin)
    shortfall = wanted - float(smallest_nm)
    if shortfall <= 0.0:
        return float(padding_nm)
    return float(padding_nm) + shortfall / (2.0 * factor)


def _solvate_with_room_for_the_cutoff(
    modeller: Any,
    ff: Any,
    add_solvent_kwargs: dict[str, Any],
    *,
    nonbonded_cutoff_nm: float,
    padding_nm: float,
    nonbonded_method: Any,
    unit: Any,
    attempts: int = 3,
    most_it_may_grow_nm: float = 0.5,
) -> None:
    """Solvate, and grow the padding if the box comes out too small.

    A periodic cutoff has to be at most half the smallest box dimension --
    below that a particle sees its own image and the physics is wrong, which
    is why OpenMM refuses. Padding is measured from the solute, and a
    dodecahedron's smallest dimension is shorter than a cube's for the same
    padding, so a small solute at the default 1.0 nm padding and 1.0 nm
    cutoff now lands under the limit where a cube did not.

    Growing the padding is the only remedy that keeps what was asked for:
    the alternative is shrinking the cutoff, which changes the physics rather
    than the box. Said out loud, because a run that quietly used more padding
    than the config states would be reporting the config and doing something
    else.

    Only a little, though. A default box that a change of shape made invalid
    needs a few tenths of a nanometre. A 1.5 nm cutoff asked for with 0.4 nm
    of padding needs four times the box, and somebody who asked for both has
    given settings that contradict each other -- better told than quietly
    handed a system four times the size. Past ``most_it_may_grow_nm`` this
    stops adjusting and lets the check further down say so.
    """
    kwargs = dict(add_solvent_kwargs)
    padding = float(padding_nm)
    for attempt in range(attempts):
        modeller.addSolvent(ff, **kwargs)
        # The raw setting, not the resolved key: that is derived further down,
        # after solvation, so reading it here was a use before assignment.
        if str(nonbonded_method) not in ("CutoffPeriodic", "PME", "Ewald"):
            return
        vectors = modeller.topology.getPeriodicBoxVectors()
        if vectors is None:
            return
        try:
            smallest = min(
                float(vectors[i][i].value_in_unit(unit.nanometer)) for i in range(3)
            )
        except (TypeError, ValueError, AttributeError):
            return
        # A box of no size is not a box to add padding to -- it means the
        # topology could not say, which is not a problem this can solve.
        if smallest <= 0.0:
            return
        wanted = 2.0 * nonbonded_cutoff_nm * NPT_CONTRACTION_MARGIN
        if smallest >= wanted or attempt == attempts - 1:
            return

        # Enough for the cutoff, and for the contraction the barostat is
        # about to apply. The shape matters: a dodecahedron's narrowest
        # width is the edge over sqrt(2), so the same padding buys sqrt(2)
        # less of it than a cube's, and `shortfall / 2` -- the cube's
        # arithmetic -- undershot every non-cubic box by that factor.
        grown = padding_that_reaches(
            smallest_nm=smallest,
            padding_nm=padding,
            nonbonded_cutoff_nm=nonbonded_cutoff_nm,
            box_shape=str(kwargs.get("boxShape", "cube")),
        )
        if grown - float(padding_nm) > most_it_may_grow_nm:
            # Said out loud, because the check further down reports the
            # padding the config asked for and knows nothing of what was
            # tried. Silent, this advised raising 0.80 nm without mentioning
            # that 1.19 had already been attempted and was still short --
            # so the obvious next guess fails the same way.
            logger.info(
                "Stopping at %.2f nm padding: reaching twice the %.2f nm "
                "cutoff would need about %.2f nm, which is %.2f nm more than "
                "the %.2f nm this will add on its own. The cutoff and the "
                "padding asked for are further apart than a small adjustment "
                "can settle.",
                padding, nonbonded_cutoff_nm, grown,
                grown - float(padding_nm), most_it_may_grow_nm,
            )
            return
        padding = grown
        logger.info(
            "Box came out %.2f nm across, which is under twice the %.2f nm "
            "cutoff. Re-solvating with %.2f nm padding so no particle sees "
            "its own periodic image.",
            smallest, nonbonded_cutoff_nm, padding,
        )
        kwargs["padding"] = padding * unit.nanometer
        modeller.deleteWater()



def _import_openmm():
    """Lazy import of OpenMM. Raises a clean ImportError otherwise."""
    try:
        import openmm
        from openmm import unit
        from openmm.app import (
            CutoffNonPeriodic,
            CutoffPeriodic,
            Ewald,
            ForceField,
            HBonds,
            Modeller,
            NoCutoff,
            PDBFile,
            PME,
        )

        return {
            "openmm": openmm,
            "unit": unit,
            "ForceField": ForceField,
            "HBonds": HBonds,
            "Modeller": Modeller,
            "PDBFile": PDBFile,
            "PME": PME,
            "NoCutoff": NoCutoff,
            "CutoffNonPeriodic": CutoffNonPeriodic,
            "CutoffPeriodic": CutoffPeriodic,
            "Ewald": Ewald,
        }
    except ImportError as exc:
        raise BackendUnavailable(
            "Setup-phase system parameterization requires OpenMM. Install "
            "via conda (recommended): conda install -c conda-forge openmm "
            "pdbfixer — or via pip with the optional [md] extras: "
            "pip install fastmdxplora[md]."
        , code="environment.backend.missing") from exc


#: Metals that sit in a protein site rather than in the solvent, and whose
#: coordination a non-bonded force field does not hold. Sodium, potassium and
#: chloride are left out: they are the salt, they are meant to move, and a
#: warning about them would be noise.
STRUCTURAL_METALS = frozenset({
    "ZN", "MG", "CA", "MN", "FE", "FE2", "CO", "NI", "CU", "CU1", "CD", "HG",
})

#: How close a metal has to be to a protein nitrogen, oxygen or sulfur to be
#: in a site rather than passing through. Generous: Zn-N is near 2.0 A and
#: Ca-O near 2.4.
COORDINATION_CUTOFF_NM = 0.30


def _warn_metals_are_not_held(topology: Any, positions: Any) -> str | None:
    """Say when a metal is in a protein site the force field will not keep it in.

    Standard force fields treat a metal ion as a point charge with
    Lennard-Jones terms and nothing else. A carboxylate cage is deep enough to
    hold a divalent ion that way; a histidine-ligated zinc is not. So the ion
    leaves, slowly, and nothing else in a run reports it: the fold is intact,
    the RMSD is flat, the radius of gyration does not move, and the active
    site the study was about has quietly rearranged.

    Measured on thermolysin over 20 ns: all four calciums held their
    carboxylate sites to within 0.1 A from first frame to last, and the
    catalytic zinc lost His142 in the first production frame and never
    recovered it -- ending on a different set of residues from the one the
    crystal structure has.

    Not a refusal. Non-bonded metals are the right choice for many questions,
    including anything away from the site. It is a refusal to be silent.
    """
    try:
        import numpy as _np
    except ImportError:  # pragma: no cover - numpy is a hard dependency
        return None

    try:
        residues = list(topology.residues())
        # OpenMM hands back a Quantity of Vec3, so the unit is stripped from
        # the whole array rather than per component: `float()` on a Vec3
        # raises, and a broad except turned that into permanent silence.
        try:
            from openmm import unit as _unit  # noqa: PLC0415

            bare = positions.value_in_unit(_unit.nanometer)
        except Exception:  # noqa: BLE001 - already plain numbers
            bare = positions
        coordinates = _np.asarray([[float(c) for c in xyz] for xyz in bare],
                                  dtype=float)
    except Exception:  # noqa: BLE001 - a topology that will not walk
        return None

    metals, donors = [], []
    for residue in residues:
        name = residue.name.upper()
        for atom in residue.atoms():
            if name in STRUCTURAL_METALS and len(list(residue.atoms())) == 1:
                metals.append((name, atom.index))
            elif (name not in ("HOH", "WAT", "NA", "CL", "K")
                  and getattr(atom.element, "symbol", "") in ("N", "O", "S")):
                donors.append(atom.index)

    if not metals or not donors:
        return None

    donor_xyz = coordinates[donors]
    in_a_site = []
    for name, index in metals:
        separation = _np.linalg.norm(donor_xyz - coordinates[index], axis=1)
        if _np.any(separation <= COORDINATION_CUTOFF_NM):
            in_a_site.append(name)

    if not in_a_site:
        return None

    from collections import Counter

    counted = ", ".join(f"{n}x{c}" if c > 1 else n
                        for n, c in sorted(Counter(in_a_site).items()))
    return (
        f"{counted} sit in protein sites, and this force field holds them "
        "there with charge and Lennard-Jones terms alone. That is enough for "
        "a carboxylate cage and often not for a metal held by histidines: it "
        "can drift out of its site over a run, while the fold stays intact "
        "and nothing else reports it. If the site is what the study is "
        "about, restrain the metal to its ligands, use a bonded or "
        "dummy-atom model for it, and check the coordination at the end "
        "against the structure it started from."
    )


def prepare_system(
    prepared_pdb: str | Path,
    output_dir: str | Path,
    *,
    forcefield: str | None = None,
    force_field: list[str] | None = None,
    water_model: str | None = None,
    ligand: str | Path | list[str | Path] | None = None,
    ligand_forcefield: str | None = None,
    ligand_name: str = "LIG",
    ligand_net_charge: int | None = None,
    ligand_pose: str = "auto",
    check_ligand_clashes: bool = True,
    ligand_clash_threshold_nm: float = 0.15,
    solvent_padding_nm: float = DEFAULT_PADDING_NM,
    membrane: str | None = None,
    membrane_orientation_checked: bool = False,
    membrane_orient: bool = False,
    box_shape: str = "cube",
    ion_positive: str = "Na+",
    ion_negative: str = "Cl-",
    ion_concentration_M: float = DEFAULT_IONIC_STRENGTH_M,
    neutralize: bool = True,
    nonbonded_method: str = "PME",
    nonbonded_cutoff_nm: float = 1.0,
    ewald_error_tolerance: float = 0.0005,
    use_switching_function: bool = True,
    switch_distance_nm: float | None = None,
    dispersion_correction: bool = True,
    remove_cm_motion: bool = True,
    constraints: str = "HBonds",
    rigid_water: bool = True,
    hydrogen_mass_amu: float | None = None,
    temperature_K: float = 300.0,
) -> dict[str, Path]:
    """Solvate, ionize, parameterize, and serialize an OpenMM system.

    Parameters
    ----------
    prepared_pdb : path
        Input PDB (typically the output of :func:`fix_pdb_with_pdbfixer`).
    output_dir : path
        Where to write ``solvated.pdb``, ``topology.pdb``, ``system.xml``,
        and ``state.xml``. Parent directories are created.
    force_field : list of str, optional
        Force-field XML file names recognized by OpenMM, as an escape
        hatch for a combination the registry does not name. ``None``, the
        default, means the resolved ``forcefield`` supplies them; through
        ``auto`` that is ``["amber14-all.xml", "amber14/tip3p.xml"]``.
        Pass the protein force field plus its accompanying water model.
    water_model : str, optional
        Water model name (``"tip3p"``, ``"tip4pew"``, ``"spce"``, etc.)
        used by Modeller. When ``None`` (the default), Modeller picks the
        model that matches the supplied water-model XML.
    solvent_padding_nm : float, default 1.0
        Minimum distance in nm between any solute atom and the periodic
        box wall.
    box_shape : {"cube", "dodecahedron", "octahedron"}, default "cube"
        Periodic box geometry.
    ion_positive, ion_negative : str
        Counter-ions. Defaults to NaCl.
    ion_concentration_M : float, default 0.15
        Target ionic concentration in M (physiological for biomolecules).
    neutralize : bool, default True
        Add ions to neutralize the net solute charge before reaching the
        target concentration.
    nonbonded_cutoff_nm : float, default 1.0
        PME real-space cutoff in nm.
    constraints : {"None", "HBonds", "AllBonds", "HAngles"}, default "HBonds"
        Bond constraints. ``HBonds`` is the standard choice for 2 fs
        timesteps with water.
    rigid_water : bool, default True
        Constrain water bond lengths and angles.
    hydrogen_mass_amu : float, optional
        If set, repartition heavy-atom mass onto hydrogens to enable
        longer timesteps (the standard "HMR" technique, 4 amu allows
        ~4 fs steps with constraints).
    temperature_K : float, default 300.0
        Used to set initial velocities on the State (Maxwell-Boltzmann).

    Returns
    -------
    dict
        Mapping artifact-name -> ``Path``: ``solvated_pdb``,
        ``topology_pdb``, ``system_xml``, ``state_xml``.
    """
    omm = _import_openmm()
    unit = omm["unit"]

    prepared_path = Path(prepared_pdb)
    if not prepared_path.exists():
        raise MissingResultError(f"Prepared PDB not found: {prepared_path}", code="environment.path.not_found")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the force field. Precedence: an explicit raw `force_field`
    # XML list (power-user escape hatch) wins; otherwise resolve the named
    # `forcefield` selector to its XML set and default water model. A named
    # choice and a raw list together is rejected upstream (setup pipeline).
    ff_choice = None
    if force_field:
        force_field = list(force_field)
    else:
        ff_choice = resolve_forcefield(forcefield)
        force_field = list(ff_choice.xmls)
        if water_model is None:
            water_model = ff_choice.water_model

        # A force field is fitted with a particular treatment of the
        # truncation, so the cutoff scheme belongs to it rather than beside
        # it. One cutoff and one switch for every force field meant the wrong
        # distance for CHARMM36 and a switch AMBER should never have had.
        # An explicit setting wins; this decides what happens when nobody
        # chose. Resolved here because the padding check below needs the
        # cutoff this run will actually use.
        (nonbonded_cutoff_nm, use_switching_function,
         switch_distance_nm, said) = nonbonded_scheme(
            ff_choice.name,
            cutoff_nm=nonbonded_cutoff_nm,
            use_switching_function=use_switching_function,
            switch_distance_nm=switch_distance_nm)
        if said:
            logger.info("%s", said)

    # Normalize the ligand argument to a list; several ligands, cofactors, or
    # copies of one component may be parameterized together.
    ligands = _normalize_ligands(ligand)
    if ligands:
        # A ligand requires a ligand-capable force field. Raw XML lists can't
        # be introspected for ligand support, so require the named selector.
        if ff_choice is None:
            raise StudyError(
                "A ligand was supplied with a raw `force_field` XML list. "
                "Protein-ligand parameterization needs the named "
                "`forcefield` selector (use forcefield='amber-openff'), "
                "which wires the OpenFF small-molecule generator."
            , code="config.option.conflicting")
        if not ff_choice.supports_ligand:
            valid = ", ".join(
                n for n in available_forcefields()
                if resolve_forcefield(n).supports_ligand
            )
            raise StudyError(
                f"Force field {ff_choice.name!r} does not support ligands. "
                f"For protein-ligand systems use a ligand-capable force "
                f"field: {valid}."
            , code="setup.forcefield.incompatible")
    constraints_obj = _resolve_constraints(omm, constraints)

    # ----- 1. Load topology + positions -----
    logger.info("Loading prepared PDB: %s", prepared_path)
    pdb = omm["PDBFile"](str(prepared_path))
    _drop_ion_coordination_bonds(pdb.topology)

    # ----- 2. Build force field (+ ligand) -----
    modeller = omm["Modeller"](pdb.topology, pdb.positions)
    system_generator = None
    if ligands:
        # Protein-ligand: load the ligand as an OpenFF Molecule, build a
        # SystemGenerator that combines the protein/water force field with
        # the OpenFF small-molecule force field, and add the ligand into the
        # topology so it is solvated together with the protein.
        sm_ff = ligand_forcefield or ff_choice.small_molecule_forcefield
        logger.info(
            "Building protein-ligand ForceField: %s + small-molecule %s",
            force_field, sm_ff,
        )
        names = _resolve_ligand_names(ligands, ligand_name)
        charges = _resolve_ligand_charges(ligands, ligand_net_charge)
        ligand_mols = []
        # Two copies of one component are separate residues, and each
        # supplied file takes the next rather than all at once.
        from collections import Counter as _Counter

        placed = _Counter()
        for path, name, charge in zip(ligands, names, charges):
            molecule = load_ligand(path, name=name, net_charge=charge)
            # The chemistry comes from the supplied file and the pose from
            # the structure, where the structure has this residue. An ideal
            # SDF carries bond orders and an arbitrary geometry; using its
            # coordinates puts the ligand wherever that geometry lies, which
            # on a real complex was seventeen Angstroms from the site.
            # The structure as it arrived, not the prepared one: PDBFixer
            # has removed the heterogens by the time that is written, so the
            # ligand's crystallographic coordinates are only in the input.
            original = Path(output_dir) / "input.pdb"
            if original.is_file() or ligand_pose == "file":
                # `file` needs no structure to consult, so an absent
                # input.pdb does not turn a deliberate pose into silence.
                molecule, said = pose_by_policy(
                    molecule, original, name, policy=ligand_pose,
                    copy=placed[name])
            else:
                molecule, said = molecule, None
            placed[name] += 1
            if said:
                logger.info("%s", said)
            ligand_mols.append(molecule)
        ff, system_generator = _build_ligand_forcefield(
            force_field, sm_ff, ligand_mols,
        )
        # Each ligand is placed in turn, and each is clash-checked against
        # everything already present: the protein for the first, and the
        # protein plus earlier ligands for the rest, since two ligands can
        # overlap each other as readily as they can overlap the protein.
        for ligand_mol, name in zip(ligand_mols, names):
            n_atoms_before = modeller.topology.getNumAtoms()
            _add_ligand_to_modeller(omm, modeller, ligand_mol, ligand_name=name)
            if check_ligand_clashes:
                _check_ligand_clashes(
                    modeller, n_atoms_before, unit,
                    threshold_nm=ligand_clash_threshold_nm,
                    ligand_name=name,
                )
    else:
        if membrane:
            # Lipid parameters, or the run fails at system creation with a
            # message about a residue template for POPC -- which names the
            # symptom rather than the missing file.
            from fastmdxplora.setup.membrane import membrane_forcefield_files

            force_field = membrane_forcefield_files(list(force_field))
        logger.info("Building ForceField: %s", force_field)
        ff = omm["ForceField"](*force_field)

    # ----- 3. Solvate + ionize with Modeller -----
    _refuse_an_implausible_structure(modeller.topology, modeller.positions, unit)

    logger.info(
        "%s, padding=%.2f nm, ions=%s/%s @ %.3f M",
        (f"Embedding in a {str(membrane).upper()} bilayer" if membrane
         else f"Solvating in a {box_shape} box"),
        solvent_padding_nm,
        ion_positive,
        ion_negative,
        ion_concentration_M,
    )

    add_solvent_kwargs: dict[str, Any] = {
        "padding": solvent_padding_nm * unit.nanometer,
        "boxShape": box_shape,
        "positiveIon": ion_positive,
        "negativeIon": ion_negative,
        "ionicStrength": ion_concentration_M * unit.molar,
        "neutralize": neutralize,
    }
    if water_model is not None:
        add_solvent_kwargs["model"] = water_model

    if membrane:
        # A bilayer instead of a box of water. OpenMM packs the lipids and
        # solvates around them, so no external packing tool is needed -- but
        # it assumes the protein is already oriented with the membrane normal
        # along z, and does not check.
        from fastmdxplora.setup.membrane import LIPIDS, check_orientation

        lipid = str(membrane).upper()
        if lipid not in LIPIDS:
            raise StudyError(
                f"{lipid} is not a lipid OpenMM can build a bilayer from. "
                f"Available: {', '.join(sorted(LIPIDS))}."
            , code="setup.membrane.lipid_unparameterized")

        if membrane_orient and not membrane_orientation_checked:
            # Whether the rotation can be trusted, before doing it. A protein
            # with no clearly longest axis gets one chosen by noise, and the
            # same structure in a different starting frame would come out
            # differently.
            from fastmdxplora.setup.membrane import check_axis_is_well_defined

            problem = check_axis_is_well_defined(
                modeller.topology, modeller.positions)
            if problem:
                raise StudyError(problem, code="setup.membrane.orientation_unchecked")

        if membrane_orient:
            # Asked for rather than done quietly: rotating by principal axes
            # is right for a transmembrane helix or a bundle of them, and
            # wrong for a protein with a large soluble domain that drags the
            # axis away from the normal.
            from fastmdxplora.setup.membrane import orient_for_membrane

            modeller.positions = orient_for_membrane(
                modeller.topology, modeller.positions)
            logger.info(
                "Rotated the structure so its longest axis lies along the "
                "membrane normal. This is right for a transmembrane bundle "
                "and wrong where a soluble domain dominates the shape; it "
                "cannot tell which way up the protein ends, so where that "
                "matters use an oriented structure from OPM."
            )

        if not membrane_orientation_checked:
            problem = check_orientation(modeller.topology, modeller.positions)
            if problem:
                raise StudyError(problem, code="setup.membrane.orientation_unchecked")

            # And whether what came out looks like a membrane protein at all.
            # Rotating by principal axes is right for a transmembrane bundle
            # and wrong where a soluble domain dominates the shape; until this
            # check existed, the wrong case proceeded silently.
            from fastmdxplora.setup.membrane import check_hydrophobic_belt

            problem = check_hydrophobic_belt(
                modeller.topology, modeller.positions)
            if problem:
                raise StudyError(problem, code="setup.membrane.orientation_unchecked")

            # And whether the copies agree with each other. Each one passes
            # the checks above whichever way up it is; only together do they
            # show that one was inverted.
            from fastmdxplora.setup.membrane import check_chains_point_the_same_way

            problem = check_chains_point_the_same_way(
                modeller.topology, modeller.positions)
            if problem:
                raise StudyError(problem, code="setup.membrane.orientation_unchecked")
            logger.info(
                "Hydrophobic belt check passed: the hydrophobic residues sit "
                "nearer the middle than the charged ones, as a bilayer-"
                "spanning protein's do."
            )

        modeller.addMembrane(
            ff,
            lipidType=lipid,
            minimumPadding=solvent_padding_nm * unit.nanometer,
            positiveIon=ion_positive,
            negativeIon=ion_negative,
            ionicStrength=ion_concentration_M * unit.molar,
            neutralize=neutralize,
        )
        n_atoms_solvated = modeller.topology.getNumAtoms()
        logger.info("Membrane system: %d atoms", n_atoms_solvated)
    else:
        try:
            try:
                _solvate_with_room_for_the_cutoff(
                    modeller,
                    ff,
                    add_solvent_kwargs,
                    nonbonded_cutoff_nm=nonbonded_cutoff_nm,
                    padding_nm=solvent_padding_nm,
                    nonbonded_method=nonbonded_method,
                    unit=unit,
                )
            except TypeError:
                # Older OpenMM versions (<7.7) don't support boxShape.
                add_solvent_kwargs.pop("boxShape", None)
                modeller.addSolvent(ff, **add_solvent_kwargs)
        except ValueError as exc:
            # Solvation builds templates too, and reaches them before
            # createSystem does. The explanation was wired to createSystem
            # only, so a component the force field cannot parameterize
            # produced OpenMM's raw message here -- which names the residue
            # and not what to do about it.
            raise _explain_unparameterized(exc, ff, modeller.topology) from exc

        n_atoms_solvated = modeller.topology.getNumAtoms()
        logger.info("Solvated system: %d atoms", n_atoms_solvated)

        said = _warn_metals_are_not_held(
            modeller.topology, modeller.positions)
        if said:
            logger.warning("%s", said)

    # ----- 4. Parameterize: build the OpenMM System -----
    method_map = {
        "nocutoff": "NoCutoff",
        "cutoffnonperiodic": "CutoffNonPeriodic",
        "cutoffperiodic": "CutoffPeriodic",
        "pme": "PME",
        "ewald": "Ewald",
    }
    method_key = method_map.get(str(nonbonded_method).lower())
    if method_key is None:
        raise StudyError(
            f"Unknown nonbonded_method {nonbonded_method!r}. Valid: "
            f"NoCutoff, CutoffNonPeriodic, CutoffPeriodic, PME, Ewald."
        , code="config.option.not_permitted")
    nonbonded_method_obj = omm[method_key]
    is_cutoff_method = method_key in (
        "CutoffNonPeriodic", "CutoffPeriodic", "PME", "Ewald"
    )

    logger.info(
        "Creating OpenMM System (%s, cutoff=%.2f nm)",
        method_key, nonbonded_cutoff_nm,
    )
    create_system_kwargs: dict[str, Any] = {
        "nonbondedMethod": nonbonded_method_obj,
        "constraints": constraints_obj,
        "rigidWater": rigid_water,
        "removeCMMotion": bool(remove_cm_motion),
    }
    # Cutoff/PME/Ewald methods take a cutoff distance + dispersion correction.
    if is_cutoff_method:
        create_system_kwargs["nonbondedCutoff"] = nonbonded_cutoff_nm * unit.nanometer
        create_system_kwargs["useDispersionCorrection"] = bool(dispersion_correction)
    # PME / Ewald take an Ewald error tolerance.
    if method_key in ("PME", "Ewald"):
        create_system_kwargs["ewaldErrorTolerance"] = float(ewald_error_tolerance)
    # Switching function only applies to cutoff-based methods.
    if is_cutoff_method and use_switching_function:
        create_system_kwargs["switchDistance"] = (
            (switch_distance_nm if switch_distance_nm is not None
             else 0.9 * nonbonded_cutoff_nm) * unit.nanometer
        )
    if hydrogen_mass_amu is not None:
        create_system_kwargs["hydrogenMass"] = hydrogen_mass_amu * unit.amu

    # Guard: for periodic cutoff methods, OpenMM requires the nonbonded
    # cutoff to be <= half the smallest box dimension, otherwise it raises a
    # cryptic NonbondedForce error at Context construction. Catch it here
    # with an actionable message. (This normally can't happen with the
    # default 1.0 nm padding + 1.0 nm cutoff, but a user with a small
    # padding or large cutoff could trip it.)
    if method_key in ("CutoffPeriodic", "PME", "Ewald"):
        box_vectors = modeller.topology.getPeriodicBoxVectors()
        if box_vectors is not None:
            try:
                # Smallest box edge length (nm). Take the min diagonal
                # component; works for cubic and triclinic boxes.
                edges_nm = [
                    float(box_vectors[i][i].value_in_unit(unit.nanometer))
                    for i in range(3)
                ]
                min_edge_nm = min(edges_nm)
            except (TypeError, ValueError, AttributeError):
                # Box vectors aren't real numeric quantities (e.g. mocked
                # in tests, or an unexpected type) — skip the guard and let
                # OpenMM handle validation.
                min_edge_nm = None
            if min_edge_nm is not None and nonbonded_cutoff_nm > 0.5 * min_edge_nm:
                raise StudyError(
                    f"Nonbonded cutoff ({nonbonded_cutoff_nm:.2f} nm) exceeds "
                    f"half the smallest periodic box dimension "
                    f"({0.5 * min_edge_nm:.2f} nm; box edge {min_edge_nm:.2f} "
                    f"nm). Increase solvent_padding_nm (currently "
                    f"{solvent_padding_nm:.2f} nm) or decrease "
                    f"nonbonded_cutoff_nm so that the cutoff is at most half "
                    f"the box."
                , code="config.option.wrong_type")

    try:
        system = ff.createSystem(modeller.topology, **create_system_kwargs)
    except ValueError as exc:
        raise _explain_unparameterized(exc, ff, modeller.topology) from exc

    # ----- 5. Capture initial State -----
    # Use a no-op integrator just to obtain a Context for State serialization.
    integrator = omm["openmm"].VerletIntegrator(0.001 * unit.picoseconds)
    context = omm["openmm"].Context(system, integrator)
    context.setPositions(modeller.positions)
    context.setVelocitiesToTemperature(temperature_K * unit.kelvin)
    state = context.getState(
        getPositions=True, getVelocities=True, enforcePeriodicBox=True
    )

    # ----- 6. Serialize to disk -----
    solvated_pdb = out_dir / "solvated.pdb"
    topology_pdb = out_dir / "topology.pdb"  # alias for the analysis phase
    system_xml = out_dir / "system.xml"
    state_xml = out_dir / "state.xml"

    with solvated_pdb.open("w", encoding="utf-8") as fh:
        omm["PDBFile"].writeFile(modeller.topology, modeller.positions, fh, keepIds=True)
    # Symlink-style copy: keep both files (cheap, makes the analysis phase
    # contract simple — it always looks for topology.pdb).
    import shutil as _shutil
    _shutil.copy2(solvated_pdb, topology_pdb)

    with system_xml.open("w", encoding="utf-8") as fh:
        fh.write(omm["openmm"].XmlSerializer.serialize(system))
    with state_xml.open("w", encoding="utf-8") as fh:
        fh.write(omm["openmm"].XmlSerializer.serialize(state))

    logger.info("Wrote: %s, %s, %s, %s", solvated_pdb, topology_pdb, system_xml, state_xml)

    return {
        "solvated_pdb": solvated_pdb,
        "topology_pdb": topology_pdb,
        "system_xml": system_xml,
        "state_xml": state_xml,
        # How big the system ended up. A methods section has to state it, and
        # this was the only place that knew -- it was logged to the terminal
        # and then discarded, so the report had to call it unrecorded.
        "n_atoms_solvated": n_atoms_solvated,
        # And the box, for exactly the same reason. A methods section states
        # the periodic cell, and diagnosing a failed run means asking whether
        # the box was ever big enough for the cutoff -- a question that had to
        # be answered by reading CRYST1 out of solvated.pdb by hand, because
        # nothing recorded it. The perpendicular widths are what the minimum
        # image convention constrains, and for a dodecahedron they are shorter
        # than the edge lengths, so both are kept.
        "box": _box_record(modeller),
    }


def _box_record(modeller: Any) -> dict[str, Any] | None:
    """The periodic cell, in the terms the cutoff is actually judged against.

    ``box_vectors`` are what OpenMM holds. ``perpendicular_widths_nm`` are the
    diagonal components, which is the quantity the minimum image convention
    limits: a cutoff has to be at most half the smallest of them. For a
    rhombic dodecahedron the smallest is the edge divided by root two, so
    reading the edge alone overstates the room available by 40%.
    """
    try:
        vectors = modeller.topology.getPeriodicBoxVectors()
        if vectors is None:
            return None
        # OpenMM is present in any real setup, and asking it for nanometres is
        # the only way to be sure of the units. Where it is absent the vectors
        # are already plain numbers, and reading them is better than recording
        # nothing -- but the conversion is never skipped when it is available,
        # because a box recorded in the wrong units is worse than no box.
        try:
            from openmm import unit  # noqa: PLC0415 -- optional backend

            nanometre = unit.nanometer
        except ImportError:
            nanometre = None

        rows = [
            [float(component.value_in_unit(nanometre))
             if nanometre is not None else float(component)
             for component in vector]
            for vector in vectors
        ]
    except Exception:  # noqa: BLE001 -- a box that cannot be read is not a
        # reason to fail a setup that otherwise succeeded.
        return None

    widths = [rows[i][i] for i in range(3)]
    volume = widths[0] * widths[1] * widths[2]
    return {
        "vectors_nm": rows,
        "perpendicular_widths_nm": widths,
        "smallest_width_nm": min(widths),
        "volume_nm3": volume,
        "largest_usable_cutoff_nm": 0.5 * min(widths),
    }

def _normalize_ligands(
    ligand: str | Path | list[str | Path] | None,
) -> list[str | Path]:
    """Normalize the ligand argument to a list of paths (possibly empty)."""
    if ligand is None:
        return []
    if isinstance(ligand, (str, Path)):
        return [ligand]
    return list(ligand)



def _resolve_ligand_names(ligands, ligand_name) -> list[str]:
    """A residue name for every ligand, distinct from the others.

    One name may be given for one ligand, or a list matching them. Otherwise
    the file stem is used, which for chemistry retrieved from the structure is
    the component code and is exactly what the analyses expect to select on.
    """
    from pathlib import Path as _Path

    if isinstance(ligand_name, (list, tuple)):
        names = [str(n).strip().upper() for n in ligand_name]
        if len(names) != len(ligands):
            raise StudyError(
                f"{len(names)} ligand names given for {len(ligands)} ligands; "
                "give one name per ligand, or none to use the file names."
            , code="config.option.conflicting")
    elif len(ligands) == 1:
        names = [str(ligand_name or "LIG").strip().upper()]
    elif ligand_name:
        # One name cannot serve several ligands: they would be
        # indistinguishable in the topology and in every later selection.
        raise StudyError(
            f"a single ligand name ({ligand_name!r}) was given for "
            f"{len(ligands)} ligands. Give one name per ligand, or none to "
            "take them from the file names."
        , code="config.option.conflicting")
    else:
        # A single name across several ligands would make them
        # indistinguishable in the topology and in every later selection.
        names = [_Path(str(p)).stem.strip().upper()[:3] or "LIG" for p in ligands]

    # Distinct chemistries need distinct names; copies of one component do
    # not. Two 7VF molecules in a structure are both called 7VF, and an
    # analysis selecting `resname 7VF` wants both -- requiring uniqueness per
    # copy is what drove the generated names that lost the component code.
    # The file stem carries the component before its chain and number, so
    # copies of one component are recognisable as such.
    from pathlib import Path as _Path

    components: dict[str, set[str]] = {}
    for name, path in zip(names, ligands):
        component = _Path(str(path)).stem.split("_")[0].strip().upper()
        components.setdefault(name, set()).add(component)
    clashes = {name: sorted(found)
               for name, found in components.items() if len(found) > 1}
    if clashes:
        raise StudyError(
            f"different ligands were given the same residue name: {clashes}. "
            "Copies of one component may share a name -- they are told apart "
            "by chain and residue number -- but two different molecules "
            "cannot, in the topology or in any analysis that selects by "
            "residue."
        , code="config.option.conflicting")
    return names


def _resolve_ligand_charges(ligands, ligand_net_charge) -> list:
    """A net charge for every ligand; ``None`` means infer it from the file."""
    if isinstance(ligand_net_charge, (list, tuple)):
        charges = list(ligand_net_charge)
        if len(charges) != len(ligands):
            raise StudyError(
                f"{len(charges)} ligand charges given for {len(ligands)} "
                "ligands; give one per ligand, or none to infer them."
            , code="config.option.conflicting")
        return charges
    return [ligand_net_charge] * len(ligands)


def _is_monatomic_ion(atom) -> bool:
    """Whether an atom is a lone ion rather than part of a molecule.

    The residue name alone is not enough. ``CA`` names both the calcium ion and
    the alpha carbon of every amino acid, and ``CL``, ``NA`` and ``FE`` appear
    as atom names inside organic ligands. Requiring that the residue hold
    exactly one atom settles it: a calcium ion is a residue of one atom, an
    alpha carbon never is.
    """
    from fastmdxplora.setup.heterogens import ION_NAMES

    residue = atom.residue
    if residue.name.strip().upper() not in ION_NAMES:
        return False
    return sum(1 for _ in residue.atoms()) == 1


def _drop_ion_coordination_bonds(topology) -> list[str]:
    """Remove bonds that describe metal coordination as though it were covalent.

    A CONECT record between a metal and the residue holding it states a
    covalent bond. For a monatomic ion that is a misdescription, and the
    classifier in :mod:`fastmdxplora.setup.heterogens` already says so: the
    legacy PDB format writes coordination and covalency with the same record
    type, and only mmCIF separates them.

    The consequence is not cosmetic. OpenMM's ion templates declare no external
    bonds, so one such bond makes the ion unmatchable and ``createSystem``
    reports "No template found for residue" -- naming a residue whose atoms and
    bonds do in fact match, and whose real defect is an external bond the
    template does not allow. The force field could not honour the bond in any
    case: AMBER models these ions as 12-6 Lennard-Jones plus a charge, with no
    bonded term to apply, so coordination is already carried by electrostatics.

    Bonds between two ordinary residues are left alone, disulfides included. A
    genuine covalent adduct keeps its bond and reaches the classifier, which
    refuses it with an explanation rather than silently simulating a bond the
    force field cannot describe.
    """
    bonds = getattr(topology, "_bonds", None)
    if bonds is None:  # pragma: no cover - depends on OpenMM internals
        return []

    kept = []
    dropped: list[str] = []
    for bond in bonds:
        first, second = bond[0], bond[1]
        if _is_monatomic_ion(first) or _is_monatomic_ion(second):
            dropped.append(
                f"{first.residue.name}{first.residue.id}-"
                f"{second.residue.name}{second.residue.id}"
            )
        else:
            kept.append(bond)

    if dropped:
        topology._bonds = kept
        logger.info(
            "Ignoring %d connectivity record(s) that bond a monatomic ion to "
            "its surroundings (%s). The ion is kept and parameterized from the "
            "force field's non-bonded ion model, which is how metal "
            "coordination is represented.",
            len(dropped),
            ", ".join(sorted(set(dropped))),
        )
    return dropped


def _explain_unparameterized(exc: ValueError, ff, topology) -> Exception:
    """Turn OpenMM's template failure into a message that names the component.

    ``createSystem`` reports the residue by topology index -- a number that
    counts solvent, so it points at nothing a user can find in their input. The
    residue is resolved back to a name, chain and composition here.

    The check runs after ``createSystem`` rather than before it. A pre-flight
    ``getUnmatchedResidues`` looked like the tidier design, but it consults only
    the registered XML templates: the small-molecule template generators are
    invoked inside ``createSystem`` and nowhere else, so every OpenFF-handled
    ligand would be reported as unparameterizable. Letting the call fail and
    explaining the failure keeps anything the force field *can* parameterize
    working, which is the point of the automatic policy.
    """
    message = str(exc)
    if "No template found for residue" not in message:
        return exc

    unmatched = []
    try:
        unmatched = ff.getUnmatchedResidues(topology)
    except Exception:  # pragma: no cover - diagnostics must not mask the error
        pass

    # Solvent and ions added during solvation are not what the user supplied;
    # naming a water would send them looking in the wrong place.
    named = []
    for residue in unmatched:
        elements = sorted(
            {a.element.symbol for a in residue.atoms() if a.element is not None}
        )
        atom_count = len(list(residue.atoms()))
        named.append(
            f"{residue.name} (chain {residue.chain.id}, residue "
            f"{residue.id}, {atom_count} atom(s): {', '.join(elements)})"
        )

    if not named:
        return exc

    return ValueError(
        "The force field has no parameters for "
        + "; ".join(named)
        + ". A component reaches this point only when it was kept for "
        "simulation but no template matched it. Either supply parameters for "
        "it explicitly, exclude it from the system, or -- if it is the ligand "
        "you meant to simulate -- pass it through the small-molecule path with "
        "--setup-ligand so it is parameterized rather than assumed. "
        f"OpenMM reported: {message}"
    )


def _build_ligand_forcefield(force_field, small_molecule_ff, ligand_mols):
    """Build an OpenMM ForceField wired for one or more small-molecule ligands.

    Uses ``openmmforcefields``' ``SystemGenerator`` to combine the protein/
    water force field XMLs with the OpenFF small-molecule force field and the
    ligand molecule. Returns ``(forcefield, system_generator)`` — the
    ``forcefield`` is a standard OpenMM ``ForceField`` (with the ligand
    template generator registered) usable for ``addSolvent`` and
    ``createSystem``.
    """
    try:
        from openmmforcefields.generators import SystemGenerator
    except ImportError as exc:
        from fastmdxplora.setup.ligand import LigandError

        raise LigandError(
            "Ligand parameterization needs openmmforcefields, which is not "
            "installed. Install the ligand extra (pip install "
            "'fastmdxplora[ligand]') or via conda-forge "
            "(conda install -c conda-forge openmmforcefields)."
        , code="environment.backend.missing") from exc

    system_generator = SystemGenerator(
        forcefields=list(force_field),
        small_molecule_forcefield=small_molecule_ff,
        molecules=list(ligand_mols),
    )
    return system_generator.forcefield, system_generator


def _add_ligand_to_modeller(omm, modeller, ligand_mol, ligand_name="LIG") -> None:
    """Add an OpenFF ligand molecule into the Modeller topology + positions.

    The ligand residue is renamed to ``ligand_name`` so the written topology
    (topology.pdb) and the recorded manifest agree, and so downstream
    ``resname`` selections (e.g. ligand-aware analyses) can find it. OpenFF's
    ``to_openmm()`` otherwise names the residue ``UNK``.
    """
    from openff.units.openmm import to_openmm as _to_openmm

    off_topology = ligand_mol.to_topology()
    omm_topology = off_topology.to_openmm()
    # OpenFF names the ligand residue 'UNK'; set it to the configured name so
    # topology.pdb and setup_parameters.json are consistent and resname-based
    # selection works.
    for residue in omm_topology.residues():
        residue.name = ligand_name
    # Conformer positions -> OpenMM Quantity. OpenFF guarantees at least the
    # conformer loaded from the SDF/MOL2 file.
    positions = _to_openmm(ligand_mol.conformers[0])

    # Record the residue count before adding so we can re-assert the ligand
    # residue name on the MERGED topology — modeller.add() does not reliably
    # preserve the input topology's residue names across all OpenMM versions.
    n_residues_before = modeller.topology.getNumResidues()
    modeller.add(omm_topology, positions)
    for i, residue in enumerate(modeller.topology.residues()):
        if i >= n_residues_before:
            residue.name = ligand_name


#: Metals coordinate at 1.9-2.6 A, well inside any van der Waals threshold.
#: A coordinated ion is *supposed* to be that close, so measuring it against a
#: contact threshold asks the wrong question entirely.
_COORDINATING_ELEMENTS = frozenset({
    "Zn", "Mg", "Ca", "Fe", "Mn", "Cu", "Ni", "Co", "Cd", "Hg", "Na", "K",
    "Li", "Sr", "Ba",
})


def _check_ligand_clashes(
    modeller, n_protein_atoms: int, unit, *, threshold_nm: float, ligand_name: str,
) -> None:
    """Fail at setup if the ligand pose severely overlaps the protein.

    FastMDXplora simulates the protein-ligand complex as provided: the ligand
    coordinates in the SDF/MOL2 must already be a feasible bound pose (e.g.
    from a co-crystal structure or docking). If the supplied pose places
    ligand atoms on top of protein atoms, energy minimization cannot relieve
    the overlap and the simulation diverges to NaN several steps later. We
    detect that here and stop with an actionable message, rather than letting
    it surface as an opaque integration failure downstream.

    Parameters
    ----------
    n_protein_atoms : int
        Number of atoms in the topology before the ligand was added; atoms at
        index >= this are the ligand.
    threshold_nm : float
        Minimum allowed ligand-protein interatomic distance. Pairs closer than
        this count as a clash.
    """
    import math

    try:
        positions = modeller.positions
        n_total = modeller.topology.getNumAtoms()
        # Coordinates in nm as plain floats.
        coords = [
            (
                p.x if hasattr(p, "x") else p[0],
                p.y if hasattr(p, "y") else p[1],
                p.z if hasattr(p, "z") else p[2],
            )
            for p in positions.value_in_unit(unit.nanometer)
        ]
    except (AttributeError, TypeError):
        # Positions aren't real numeric quantities (e.g. mocked in tests) —
        # skip the geometric check.
        return

    # Hydrogens are excluded on both sides. Their positions are inferred
    # rather than observed: PDBFixer places the protein's, and the ligand's
    # come from its own geometry with no knowledge of the pocket. An inferred
    # hydrogen sitting close to something is not evidence of a bad pose, and
    # minimization moves it in the first few steps. A heavy-atom overlap is
    # the real thing this check exists to catch.
    try:
        elements = [
            (atom.element.symbol if atom.element is not None else "X")
            for atom in modeller.topology.atoms()
        ]
    except (AttributeError, TypeError):
        elements = []
    if len(elements) != len(coords):
        # Without reliable element information every atom is treated as heavy.
        # Excluding atoms we cannot identify would silently disable the check,
        # which is the opposite of what it is for.
        elements = ["X"] * len(coords)
    protein = [
        c for c, e in zip(coords[:n_protein_atoms], elements[:n_protein_atoms])
        if e != "H"
    ]
    ligand_pairs = [
        (c, e) for c, e in zip(coords[n_protein_atoms:n_total],
                               elements[n_protein_atoms:n_total])
        if e != "H"
    ]

    # A monatomic metal is here because it is coordinated by the protein, so
    # a short contact is the reason it was kept, not a fault.
    if ligand_pairs and all(e in _COORDINATING_ELEMENTS for _c, e in ligand_pairs):
        logger.info(
            "Clash check skipped for %s: a coordinated metal is expected to "
            "sit inside contact distance.", ligand_name,
        )
        return

    ligand = [c for c, _e in ligand_pairs]
    if not ligand or not protein:
        return

    thr_sq = threshold_nm * threshold_nm
    n_clashes = 0
    min_dist_sq = math.inf
    for lx, ly, lz in ligand:
        for px, py, pz in protein:
            d2 = (lx - px) ** 2 + (ly - py) ** 2 + (lz - pz) ** 2
            if d2 < min_dist_sq:
                min_dist_sq = d2
            if d2 < thr_sq:
                n_clashes += 1

    if n_clashes:
        min_dist_nm = math.sqrt(min_dist_sq)
        raise StudyError(
            f"Ligand {ligand_name!r} clashes with the protein: {n_clashes} "
            f"ligand-protein atom pair(s) are closer than "
            f"{threshold_nm:.2f} nm (closest {min_dist_nm:.3f} nm). "
            f"FastMDXplora simulates the complex as provided — the ligand "
            f"coordinates must be a feasible bound pose (from a co-crystal "
            f"structure or docking), not an arbitrary position. Provide a "
            f"properly placed ligand, or if the contact is acceptable, lower "
            f"`ligand_clash_threshold_nm` or set `check_ligand_clashes=False`. "
            f"(Hydrogens are excluded from this check, so these are "
            f"heavy-atom overlaps.)"
        , code="setup.ligand.clash")
    logger.info(
        "Ligand-protein clash check passed (closest contact %.3f nm).",
        math.sqrt(min_dist_sq) if min_dist_sq != math.inf else 0.0,
    )


def _resolve_constraints(omm: dict, constraints: str):
    """Map a string ``constraints`` argument to an OpenMM enum value."""
    if constraints is None or str(constraints).lower() == "none":
        return None
    mapping = {
        "hbonds": omm["HBonds"],
    }
    # Add more constraints if OpenMM exposes them in this install
    try:
        from openmm.app import AllBonds, HAngles

        mapping["allbonds"] = AllBonds
        mapping["hangles"] = HAngles
    except ImportError:
        pass

    key = str(constraints).lower()
    if key not in mapping:
        raise StudyError(
            f"Unknown constraints option {constraints!r}. Valid: "
            f"None, HBonds, AllBonds, HAngles."
        , code="config.option.not_permitted")
    return mapping[key]
