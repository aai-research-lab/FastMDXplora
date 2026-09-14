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
        ``None`` means "let Modeller infer it from the force field".
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
        description="CHARMM36 protein force field with CHARMM-style water.",
        # Lee et al., CHARMM-GUI Input Generator, J Chem Theory Comput 2016:
        # OpenMM offers only the potential-based switch, not CHARMM's
        # force-based one, and this is the protocol they tested against
        # CHARMM's own results for each program.
        nonbonded=(1.2, 1.0),
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
        xmls=("amberfb15.xml", "tip3p.xml"),
        water_model="tip3p",
        supports_ligand=False,
        small_molecule_forcefield=None,
        description="AMBER-FB15 protein force field with TIP3P water.",
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

#: What the schema hands over when nobody chose. A value equal to this is
#: taken as "not chosen" so the force field's own scheme can apply; a value
#: different from it was typed by somebody and is left alone.
SCHEMA_CUTOFF_DEFAULT_NM = 1.0


def nonbonded_scheme(
    name: str,
    *,
    cutoff_nm: float | None,
    use_switching_function: bool,
    switch_distance_nm: float | None,
) -> tuple[float, bool, float | None, str | None]:
    """The cutoff, the switch, and a sentence if either was decided here.

    A force field is fitted with a particular treatment of the truncation and
    the rest of its parameters compensate for it, so the scheme belongs to the
    force field rather than beside it. AMBER is developed with hard truncation
    and should not be switched at all; CHARMM36 is developed at 1.2 nm with
    switching from 1.0, and OpenMM offers the potential-based switch rather
    than CHARMM's force-based one -- close enough to be the protocol
    CHARMM-GUI prescribes for OpenMM, and not the same function.

    An explicit setting always wins. What this decides is what happens when
    nobody chose, which until now was one cutoff and one switch for every
    force field: the wrong distance for CHARMM36 and a switch AMBER should
    never have had.
    """
    choice = _REGISTRY.get(str(name).lower())
    if choice is None:
        return (cutoff_nm or SCHEMA_CUTOFF_DEFAULT_NM,
                use_switching_function, switch_distance_nm, None)

    wanted_cutoff, wanted_switch = choice.nonbonded
    chosen = (cutoff_nm is not None
              and abs(float(cutoff_nm) - SCHEMA_CUTOFF_DEFAULT_NM) > 1e-9)

    cutoff = float(cutoff_nm) if chosen else wanted_cutoff
    if switch_distance_nm is not None:
        # Somebody named the switch; the cutoff scheme is theirs to set.
        return cutoff, use_switching_function, switch_distance_nm, None

    switching = wanted_switch is not None
    said = None
    if not chosen:
        if switching:
            said = (
                f"{choice.name} is developed at {wanted_cutoff:g} nm with "
                f"switching from {wanted_switch:g} nm, so that is what this "
                "run uses. OpenMM offers the potential-based switch rather "
                "than CHARMM's force-based one; it is the protocol CHARMM-GUI "
                "prescribes for OpenMM, and it is not the same function.")
        else:
            said = (
                f"{choice.name} is developed with hard truncation at "
                f"{wanted_cutoff:g} nm and no switching function, so none is "
                "applied. A switch here would move the run away from the "
                "parameterisation rather than towards it.")
    return cutoff, switching, wanted_switch, said
