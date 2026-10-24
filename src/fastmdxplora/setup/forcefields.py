"""Named force-field selector.

FastMDXplora lets users pick a force field by a short, documented **name**
(e.g. ``charmm36``, ``amber14``) rather than by listing raw OpenMM XML
filenames. Each name resolves to the underlying protein/water XML set, a
default water model, and whether the force field supports small-molecule
ligand parameterization.

This module is the single source of truth for that mapping. The setup phase
calls :func:`resolve_forcefield` to turn a name into a concrete
:class:`ForceFieldChoice`; everything else (the OpenMM ``ForceField`` build,
the manifest record, validation messages) is derived from it.

A raw-XML escape hatch remains available: a user who passes an explicit
``force_field`` list of XML filenames bypasses the named selector entirely
(see the setup pipeline). That is for power users who need a combination the
named registry does not (yet) cover.

The XML filenames below were verified against the force-field files bundled
with OpenMM (``openmm/app/data``). Adding a new named force field is a single
entry in :data:`_REGISTRY`.
"""

from __future__ import annotations

from dataclasses import dataclass
from fastmdxplora.refusals import StudyError


@dataclass(frozen=True)
class ForceFieldChoice:
    """A resolved force-field selection.

    Attributes
    ----------
    name : str
        The canonical name (lowercased), as recorded in the manifest.
    xmls : tuple[str, ...]
        OpenMM force-field XML files, passed positionally to
        ``openmm.app.ForceField(*xmls)``.
    water_model : str | None
        Default water model name for ``Modeller.addSolvent(model=...)``.
        ``None`` means "let Modeller infer it from the force field". It is
        the geometry the box is built from, which the force field's water
        file then parameterises, so it is not always the water's own name.
    water_name : str | None
        The water model as a methods section names it, where that differs
        from ``water_model``: TIP3P-FB is built from Modeller's ``tip3p``
        box and parameterised by ``tip3pfb.xml``.
    supports_ligand : bool
        Whether this force field can parameterize small-molecule ligands
        (via the OpenFF small-molecule generator). Used by the ligand
        feature to reject incoherent combinations with a clear error.
    small_molecule_forcefield : str | None
        Default small-molecule force field name handed to the OpenFF
        ``SystemGenerator`` (e.g. ``openff-2.2.1``) when ``supports_ligand``
        is true. ``None`` for protein-only force fields.
    description : str
        One-line human-readable summary for help text and templates.
    """

    name: str
    xmls: tuple[str, ...]
    water_model: str | None
    supports_ligand: bool
    small_molecule_forcefield: str | None
    description: str
    #: The Lennard-Jones cutoff this force field was developed with, in nm,
    #: and the distance at which to begin switching -- or ``None`` to truncate
    #: at the cutoff with no switch.
    #:
    #: Not a preference. A force field is fitted with a particular treatment
    #: of the truncation, and the effects of that truncation are compensated
    #: in the other parameters, so a cutoff scheme is part of the force field
    #: rather than a setting beside it. AMBER is developed with hard
    #: truncation near 0.8-1.0 nm and must not be run with a switch. CHARMM36
    #: is developed at 1.2 nm with switching from 1.0.
    nonbonded: tuple[float, float | None] = (1.0, None)
    #: The isotropic long-range dispersion correction the force field was
    #: fitted with, or ``None`` to leave it to the file (and OpenMM, which
    #: turns it on where the file says nothing). Part of the scheme for the
    #: same reason the cutoff is.
    dispersion_correction: bool | None = None
    #: See ``water_name`` above; ``None`` means it is ``water_model``.
    water_name: str | None = None
    #: The files this name resolved to in earlier releases. A study's record
    #: holds the name and its files together; one prepared then is carried
    #: on with the files it was prepared with, not the name's present ones.
    earlier_xmls: tuple[tuple[str, ...], ...] = ()


# ---------------------------------------------------------------------------
# Registry — the single source of truth for named force fields.
#
# XML filenames verified against OpenMM's bundled openmm/app/data. Each entry
# is independent; adding a force field is one line. The ligand-capable
# AMBER+OpenFF entry is added by the ligand feature, where the OpenFF
# small-molecule generator wiring lives.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, ForceFieldChoice] = {
    "charmm36": ForceFieldChoice(
        name="charmm36",
        xmls=("charmm36.xml", "charmm36/water.xml"),
        water_model=None,  # CHARMM36 water XML supplies the model
        supports_ligand=False,
        small_molecule_forcefield=None,
        description=("CHARMM36 (OpenMM's charmm36.xml, an older release without "
                     "the CHARMM36m protein parameters) with CHARMM-style water."),
        # CHARMM36 is developed at 1.2 nm, switched from 1.0 nm with CHARMM's
        # force-based switch. OpenMM's built-in switch acts on the potential,
        # so this is close to that protocol and not the same function. The
        # same holds for the long-range dispersion correction: the C36
        # lipids were fitted without one, `charmm36.xml` says so, and
        # `charmm36_2024.xml` says nothing, which OpenMM reads as on.
        nonbonded=(1.2, 1.0),
        dispersion_correction=False,
    ),
    "amber14": ForceFieldChoice(
        name="amber14",
        xmls=("amber14-all.xml", "amber14/tip3p.xml"),
        water_model="tip3p",
        supports_ligand=False,
        small_molecule_forcefield=None,
        description="AMBER14 (all biopolymers) with TIP3P water.",
    ),
    "amber-fb15": ForceFieldChoice(
        name="amber-fb15",
        # TIP3P-FB, the water AMBER-FB15 was developed with (Wang et al., J
        # Phys Chem B 121, 4023, 2017), rather than plain TIP3P: its charges
        # (O -0.848 against -0.834) and Lennard-Jones are refitted, and the
        # protein's were fitted beside them. Modeller has no `tip3pfb` box,
        # so the box is its 3-site `tip3p` one and `tip3pfb.xml` gives the
        # parameters, as `Modeller.addSolvent`'s docstring describes for
        # TIP3P-FB. Ions come from
        # `amberfb15.xml`, as they did.
        xmls=("amberfb15.xml", "tip3pfb.xml"),
        earlier_xmls=(("amberfb15.xml", "tip3p.xml"),),
        water_model="tip3p",
        water_name="TIP3P-FB",
        supports_ligand=False,
        small_molecule_forcefield=None,
        description="AMBER-FB15 protein force field with TIP3P-FB water, the "
                    "model it was developed with.",
    ),
    "amber-openff": ForceFieldChoice(
        name="amber-openff",
        # The full AMBER14 bundle rather than the protein file alone: it
        # carries DNA (OL15), RNA (OL3) and lipids, so a structure containing
        # nucleic acid can be prepared at all. With only protein.ff14SB.xml,
        # a DNA duplex failed with "no template found for residue DC", which
        # reads as a broken structure rather than a missing force field.
        #
        # It does not carry glycans, and an earlier version of this comment
        # said it did. Checked rather than assumed: the bundle loads 210
        # templates, among them ALA, DC and POPC, and none for NAG, BMA or
        # MAN. GLYCAM would not drop in either, because its residue names are
        # linkage-specific -- a 1->4 linked beta-GlcNAc is 4YB, not NAG -- so
        # every sugar would have to be renamed according to where it sits in
        # the tree, which means reading the tree first. That is the same
        # problem heme and NAD have: published parameters that do not apply
        # automatically because deposited names do not match the templates.
        xmls=("amber14-all.xml", "amber14/tip3p.xml"),
        water_model="tip3p",
        supports_ligand=True,
        small_molecule_forcefield="openff-2.2.1",
        description=(
            "AMBER14 biopolymers (ff14SB protein, OL15 DNA, OL3 RNA) + TIP3P "
            "water + OpenFF Sage 2.2.1 for small-molecule ligands."
        ),
    ),
}

#: What an unspecified force field means. ``auto`` picks a stack that
#: can parameterize a bound ligand, so the common case needs no flags.
DEFAULT_FORCEFIELD = "auto"


def available_forcefields() -> tuple[str, ...]:
    """Return the registered force-field names, sorted for stable display."""
    return tuple(sorted(_REGISTRY))


#: What ``auto`` resolves to. A single stack, whatever the structure holds:
#: choosing per-system would mean an apo run and its holo partner used
#: different protein force fields, and comparing them would be meaningless.
#: This one is ligand-capable, so a structure with a bound ligand needs no
#: special handling, and OpenFF was developed against exactly this protein
#: model and water.
AUTO_FORCEFIELD = "amber-openff"


def resolve_forcefield(name: str | None) -> ForceFieldChoice:
    """Resolve a force-field name to a :class:`ForceFieldChoice`.

    Parameters
    ----------
    name : str | None
        A registered force-field name (case-insensitive). ``None`` selects
        :data:`DEFAULT_FORCEFIELD`.

    Returns
    -------
    ForceFieldChoice

    Raises
    ------
    ValueError
        If ``name`` is not a registered force field. The message lists the
        valid choices.
    """
    key = (name or DEFAULT_FORCEFIELD).strip().lower()
    if key == "auto":
        key = AUTO_FORCEFIELD
    choice = _REGISTRY.get(key)
    if choice is None:
        valid = ", ".join(available_forcefields())
        raise StudyError(
            f"Unknown force field {name!r}. Valid choices: {valid}. "
            f"(For an unlisted combination, pass an explicit `force_field` "
            f"list of OpenMM XML filenames instead.)"
        , code="setup.forcefield.unknown", given=name, permitted=sorted(_REGISTRY))
    return choice

#: The cutoff where neither the config nor the force field says one: an
#: explicit `force_field` XML list whose files name no one family.
FALLBACK_CUTOFF_NM = 1.0

#: The registered scheme an explicit list of files takes, by the family its
#: files belong to, read from each file's name and folder. Water and ion
#: files OpenMM keeps at the top of its data folder (`tip3p.xml`) belong to
#: no family and do not decide; a CHARMM file outside CHARMM36 (the Drude
#: `charmm_polar_*`) is a family with no scheme here.
_FAMILIES = (("amber", "amber14"), ("charmm36", "charmm36"))

#: The folders a force field's files sit in, in OpenMM and openmmforcefields.
#: A folder decides only if it is one of these and the name is relative and
#: two parts long, as those files are written.
_DATA_FOLDERS = {"amber14": "amber", "amber19": "amber", "amber": "amber",
                 "charmm36": "charmm36", "charmm36_2024": "charmm36",
                 "charmm": None}


def _family_of(files: list[str] | tuple[str, ...]) -> tuple[str, ForceFieldChoice] | None:
    """The one family an explicit list of force field files belongs to."""
    found: set[str] = set()
    for given in files:
        parts = str(given).replace("\\", "/").lower().split("/")
        family = next((label for label, _ in _FAMILIES
                       if parts[-1].startswith(label)), None)
        # A folder decides only in a relative two-part name, the way
        # OpenMM's and openmmforcefields' own files are written
        # (`amber14/tip3p.xml`); a person's `/home/u/amber/lig.xml` does not.
        if family is None and len(parts) == 2:
            family = _DATA_FOLDERS.get(parts[0])
        # Judged on the file's own name: openmmforcefields keeps CHARMM36's
        # water beside it in a folder called `charmm`.
        if family is None and parts[-1].startswith("charmm"):
            family = "other"
        if family is not None:
            found.add(family)
    if len(found) != 1 or "other" in found:
        return None
    family = found.pop()
    return family, _REGISTRY[dict(_FAMILIES)[family]]


def dispersion_correction_of(
        name: str | list[str] | tuple[str, ...] | None) -> tuple[bool | None, str | None]:
    """The dispersion correction a force field's scheme asks for where a run
    does not state one, and a sentence if it is decided here. ``None`` leaves
    it to the force field's file."""
    if isinstance(name, (list, tuple)):
        family = _family_of(name)
        choice = family[1] if family else None
    else:
        choice = _REGISTRY.get(str(name or "").lower())
    if choice is None or choice.dispersion_correction is None:
        return None, None
    named = ("CHARMM36, its lipids among it," if choice.name == "charmm36"
             else choice.name)
    return choice.dispersion_correction, (
        f"{named} is fitted "
        f"{'with' if choice.dispersion_correction else 'without'} the isotropic "
        f"long-range dispersion correction; with nothing stated, this run has it "
        f"{'on' if choice.dispersion_correction else 'off'}."),


def nonbonded_scheme(
    name: str | list[str] | tuple[str, ...] | None,
    *,
    cutoff_nm: float | None,
    use_switching_function: bool | None,
    switch_distance_nm: float | None,
) -> tuple[float, bool, float | None, str | None]:
    """The cutoff, the switch, and a sentence if either was decided here.

    A force field is fitted with a particular treatment of the truncation and
    the rest of its parameters compensate for it, so the scheme belongs to the
    force field rather than beside it. AMBER is developed with hard truncation
    and should not be switched at all; CHARMM36 is developed at 1.2 nm with
    switching from 1.0, and OpenMM's built-in switch acts on the potential
    rather than the force as CHARMM's does -- close to that protocol, and not
    the same function.

    A stated setting always wins; ``None`` means not stated, and only then
    does the force field decide. The schema used to hand over 1.0 nm and a
    switch whether or not anybody wrote them, so a stated 1.0 nm could not
    be told from silence and became CHARMM36's 1.2, and a stated switch was
    replaced by the force field's either way, each with only a log line.

    ``name`` may be an explicit list of files. Those were switched at nine
    tenths of a 1 nm cutoff whatever they were, so ``amber14-all.xml`` listed
    by hand ran AMBER switched, which named ``amber14`` is not, and CHARMM36
    files ran at 1 nm rather than 1.2. A list whose files belong to one
    family takes that family's scheme; any other takes OpenMM's own default,
    a 1 nm cutoff and no switch, and says that nothing chose it.
    """
    label = None
    if isinstance(name, (list, tuple)):
        family = _family_of(name)
        if family is None:
            cutoff = float(cutoff_nm) if cutoff_nm is not None else FALLBACK_CUTOFF_NM
            # A switch distance named without the flag asks for a switch.
            switching = (switch_distance_nm is not None
                         if use_switching_function is None
                         else bool(use_switching_function))
            decided = []
            if cutoff_nm is None:
                decided.append(f"a {cutoff:g} nm cutoff")
            if use_switching_function is None and switch_distance_nm is None:
                decided.append("no switching function")
            said = None
            if decided:
                said = ("The force field files given do not belong to one "
                        "family this package knows the cutoff scheme of, so "
                        f"this run uses OpenMM's own default, {' and '.join(decided)}. "
                        "State `nonbonded_cutoff_nm` and `use_switching_function` "
                        "for the scheme the force field was developed with.")
            return cutoff, switching, switch_distance_nm, said
        family_name, choice = family
        label = (f"The {'AMBER' if family_name == 'amber' else 'CHARMM36'} files "
                 f"given ({', '.join(str(f) for f in name)}) are")
    else:
        choice = _REGISTRY.get(str(name or "").lower())
    if choice is None:
        # A name with no entry: nothing to consult, so the fallbacks.
        cutoff = float(cutoff_nm) if cutoff_nm is not None else FALLBACK_CUTOFF_NM
        switching = (switch_distance_nm is not None if use_switching_function is None
                     else bool(use_switching_function))
        return cutoff, switching, switch_distance_nm, None

    wanted_cutoff, wanted_switch = choice.nonbonded
    cutoff = float(cutoff_nm) if cutoff_nm is not None else wanted_cutoff
    if use_switching_function is not None:
        switching = bool(use_switching_function)
    else:
        # A switch distance named without the flag asks for a switch.
        switching = switch_distance_nm is not None or wanted_switch is not None
    distance = switch_distance_nm
    if switching and distance is None and wanted_switch is not None \
            and wanted_switch < cutoff:
        distance = wanted_switch

    decided = []
    if cutoff_nm is None:
        decided.append(f"a {cutoff:g} nm cutoff")
    if use_switching_function is None and switch_distance_nm is None:
        decided.append(
            f"switching from {distance:g} nm" if switching and distance
            else f"switching from {0.9 * cutoff:g} nm, nine tenths of the cutoff"
            if switching else "no switching function")
    if not decided:
        return cutoff, switching, distance, None
    how = (f"developed at {wanted_cutoff:g} nm with switching from "
           f"{wanted_switch:g} nm" if wanted_switch is not None
           else f"developed with hard truncation at {wanted_cutoff:g} nm and no "
                "switching function")
    said = (f"{label or choice.name + ' is'} {how}; with nothing stated, this "
            f"run uses {' and '.join(decided)}.")
    if wanted_switch is not None and switching:
        said += (" OpenMM's switch is potential-based, where CHARMM's is "
                 "force-based, so this is close to the CHARMM protocol and "
                 "not the same function.")
    elif wanted_switch is None and not switching:
        said += (" A switch here would move the run away from the "
                 "parameterisation rather than towards it.")
    return cutoff, switching, distance, said
