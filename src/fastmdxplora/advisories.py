"""What is worth knowing before a run starts, rather than after.

A run says a great deal about what it is doing: which heterogens it discarded,
that a force field wants hard truncation, that a metal in a site will not be
held there by charge alone. All of it is true and all of it arrives once the
run has begun -- by which point the person who would have changed something
has stopped watching, and on a cluster has gone home.

Most of it is decidable earlier. A structure and a set of settings are enough
to say that this protein has a zinc in a site and these parameters will not
hold it, or that this cutoff wants a bigger box than this padding will make.
Said while somebody is still choosing, the same sentence changes what they do
instead of explaining what already happened.

This is deliberately not a validator. A validator answers "will this run", and
these all describe things that run perfectly well and produce a result worth
doubting. Nothing here refuses anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Metals that sit in a protein site rather than in the solvent. The same list
#: the setup phase warns on, imported rather than repeated: two lists of what
#: counts as a structural metal would drift, and the drift would show as a
#: warning at config time that the run does not repeat, or the reverse.
from fastmdxplora.setup.prepare import STRUCTURAL_METALS

#: Force fields developed with hard truncation, where a switching function
#: moves a run away from the parameterisation rather than towards it.
_NO_SWITCH = ("amber",)


@dataclass(frozen=True)
class Advisory:
    """Something worth knowing, and what to do about it.

    ``setting`` names the field this is about, so an interface can show it
    beside the control rather than in a list somewhere else -- which is the
    whole point of saying it early.
    """

    setting: str
    summary: str
    detail: str
    remedy: str

    def as_text(self) -> str:
        return f"{self.summary} {self.detail} {self.remedy}"


def advise(structure: dict[str, Any] | None,
           settings: dict[str, Any] | None = None) -> list[Advisory]:
    """Everything worth saying about this structure and these settings.

    ``structure`` is the report from :func:`gui.structure_info.count_structure`
    or anything shaped like it; ``settings`` is a config's ``setup`` and
    ``simulation`` blocks flattened together. Both may be absent, and what can
    be said falls back to what is known.
    """
    structure = structure or {}
    settings = settings or {}
    found: list[Advisory] = []

    for check in (_a_metal_in_a_site, _a_box_too_small_for_the_cutoff,
                  _a_switch_the_force_field_does_not_want,
                  _a_ligand_with_no_chemistry, _a_density_never_equilibrated,
                  _a_bilayer_near_or_below_its_transition,
                  _a_bilayer_barely_equilibrated):
        said = check(structure, settings)
        if said is not None:
            found.append(said)
    return found


def _a_metal_in_a_site(structure: dict[str, Any],
                       settings: dict[str, Any]) -> Advisory | None:
    """A zinc held by histidines will leave, and nothing else will say so."""
    names = {str(n).upper() for n in structure.get("ion_resnames", [])}
    if not names:
        # `count_structure` reports a count without names on some paths, and
        # a count alone cannot tell a structural metal from added salt.
        return None
    structural = sorted(names & STRUCTURAL_METALS)
    if not structural:
        return None
    return Advisory(
        setting="forcefield",
        summary=f"This structure holds {', '.join(structural)}.",
        detail=(
            "A force field holds a metal in place with charge and "
            "Lennard-Jones terms alone. That is enough for a carboxylate "
            "cage and often not for one held by histidines: it can drift out "
            "of its site over a run while the fold stays intact and the RMSD "
            "stays flat, so nothing else reports it."),
        remedy=(
            "If the site is what the study is about, restrain the metal to "
            "its ligands or use a bonded or dummy-atom model, and check the "
            "coordination at the end against the structure it started from."),
    )


def _a_box_too_small_for_the_cutoff(structure: dict[str, Any],
                                    settings: dict[str, Any]) -> Advisory | None:
    """A cutoff longer than half the box means a particle sees its own image.

    Said here in nanometres of padding, because that is the setting somebody
    is looking at. The run computes the box exactly and grows it where it can;
    this is the arithmetic that decides whether it will have to.
    """
    padding = settings.get("solvent_padding_nm")
    cutoff = settings.get("nonbonded_cutoff_nm")
    if cutoff is None:
        # Not stated: the force field's own, as setup will use it.
        from fastmdxplora.setup.forcefields import FALLBACK_CUTOFF_NM, _REGISTRY

        choice = _REGISTRY.get(str(settings.get("forcefield") or "auto").lower())
        if choice is None and str(settings.get("forcefield") or "auto").lower() == "auto":
            from fastmdxplora.setup.forcefields import AUTO_FORCEFIELD

            choice = _REGISTRY.get(AUTO_FORCEFIELD)
        cutoff = choice.nonbonded[0] if choice is not None else FALLBACK_CUTOFF_NM
    extents = structure.get("extents_angstrom")
    if padding is None or not extents or settings.get("membrane"):
        # A bilayer's box is rectangular and made of whole patches about
        # 6 nm across, so the dodecahedron's arithmetic does not apply.
        return None

    # The box as OpenMM sizes it: the solute's bounding sphere plus the
    # padding, or twice the padding for a solute smaller than that, times the
    # shape's narrowest width per unit size. A capped alanine, 0.9 nm across
    # at 1.2 nm of padding, is a dodecahedron 1.70 nm at its narrowest, as
    # measured. The longest extent stands in for the sphere, which is at
    # least that wide, so this can only be kind to the box; the run computes
    # it exactly.
    from fastmdxplora.setup.prepare import (
        NARROWEST_WIDTH_PER_SIZE,
        NPT_CONTRACTION_MARGIN,
        padding_that_reaches,
        sized_by_the_padding,
    )

    shape = str(settings.get("box_shape") or "dodecahedron").lower()
    factor = NARROWEST_WIDTH_PER_SIZE.get(shape, 1.0)
    longest_nm = max(float(x) for x in extents) / 10.0
    padding = float(padding)
    across = factor * max(longest_nm + padding, 2.0 * padding)
    wanted = 2.0 * float(cutoff) * NPT_CONTRACTION_MARGIN
    if across >= wanted:
        return None
    grown = padding_that_reaches(smallest_nm=across, padding_nm=padding,
                                 nonbonded_cutoff_nm=float(cutoff), box_shape=shape)
    grows = (sized_by_the_padding(grown_nm=grown, nonbonded_cutoff_nm=float(cutoff),
                                  box_shape=shape)
             or grown - padding <= 0.5)
    return Advisory(
        setting="solvent_padding_nm",
        summary=(
            f"{padding:g} nm of padding on a solute {longest_nm:.1f} nm across "
            f"makes a {shape} about {across:.2f} nm at its narrowest."),
        detail=(
            f"A {cutoff:g} nm cutoff needs the box's narrowest width to be more "
            "than twice that, with room for the barostat to shrink it, or a "
            "particle interacts with its own periodic image. Padding is the "
            "least distance between the solute and that image, not the "
            "distance to the box's wall."),
        remedy=(
            f"Setup will grow the padding to about {grown:.2f} nm by itself, "
            "and say so; set that here to have the config say what runs."
            if grows else
            f"Setup will refuse this: raise the padding to about {grown:.2f} nm, "
            "use a cube, or lower the cutoff to something the force field "
            "still supports."),
    )


def _a_switch_the_force_field_does_not_want(
        structure: dict[str, Any], settings: dict[str, Any]) -> Advisory | None:
    """AMBER is fitted with hard truncation; switching it is not a refinement."""
    forcefield = str(settings.get("forcefield") or "").lower()
    switching = settings.get("use_switching_function")
    if switching is not True or not forcefield:
        return None
    if not any(forcefield.startswith(name) for name in _NO_SWITCH):
        return None
    return Advisory(
        setting="use_switching_function",
        summary=f"{forcefield} is developed with hard truncation.",
        detail=(
            "Its Lennard-Jones parameters were fitted against a cutoff with "
            "no switching function, and the effects of that truncation are "
            "compensated in the rest of them."),
        remedy=(
            "Leave the switch off for this force field. Switching is what "
            "CHARMM wants, at 1.2 nm from 1.0, and it is not a general "
            "improvement."),
    )


def _a_ligand_with_no_chemistry(structure: dict[str, Any],
                                settings: dict[str, Any]) -> Advisory | None:
    """A local structure carries no bond orders, and a ligand needs them."""
    ligands = structure.get("ligand_resnames") or []
    if not ligands or settings.get("ligand"):
        return None
    path = str(structure.get("path") or "")
    if not path or len(path.strip()) == 4:
        # Given by identifier, so the chemistry can be looked up.
        return None
    return Advisory(
        setting="ligand",
        summary=f"{', '.join(ligands)} looks like a ligand and has no chemistry.",
        detail=(
            "A PDB carries coordinates and element names. Bond orders, "
            "formal charges and aromaticity -- which a force field needs -- "
            "are not in it, and the entry that would settle them is "
            "reachable only for a structure given by identifier."),
        remedy=(
            "Supply the chemistry as an SDF or MOL2. Its coordinates do not "
            "matter: the ligand is placed where this structure has it."),
    )


def _a_density_never_equilibrated(structure: dict[str, Any],
                                  settings: dict[str, Any]) -> Advisory | None:
    """Without a barostat the box keeps whatever density solvation made.

    Asks the ensemble as well as the stage length. They were the same
    question while `npt_steps > 0` decided both, and separating them left
    this one behind: a resumed segment with `ensemble: npt` and no
    equilibration gets its barostat, and this still advised that the box
    was stuck at whatever solvation produced.

    Found by a rehearsal on ubiquitin, after the same mistake had already
    been fixed in the runner -- this advisory fires before the simulation
    phase starts, from a different module, so fixing one did not fix the
    other. Two places asking the same outdated question.
    """
    from fastmdxplora.simulation.ensembles import resolve_ensemble

    npt = settings.get("npt_steps")
    if npt is None or int(npt) > 0:
        return None
    if resolve_ensemble(settings) == "npt":
        # A barostat acts during production, so the density is equilibrated
        # even though no stage is labelled NPT.
        return None
    return Advisory(
        setting="npt_steps",
        summary="With no NPT stage the box keeps the density solvation gave it.",
        detail=(
            "Solvation leaves a gap around the solute, and in a small box "
            "that gap is a large share of the volume: a solute at 1.0 to 1.2 "
            "nm of padding packs near 0.90 g/mL against water's 1.0. Only a "
            "barostat closes it."),
        remedy=(
            "Give npt_steps a value unless the run is deliberately at fixed "
            "volume. The run reports the density it ended up at either way."),
    )


#: How close above a lipid's main transition a bilayer is still worth a word:
#: fluid, but where the transition a force field reproduces only roughly is a
#: few kelvin away.
NEAR_TRANSITION_K = 6.0


def _a_bilayer_near_or_below_its_transition(
        structure: dict[str, Any], settings: dict[str, Any]) -> Advisory | None:
    """A bilayer below its main transition belongs in the gel phase.

    Below the lipid's main transition the equilibrium is the gel, which a
    simulation reaches, if at all, over far longer than a run, and which
    force fields reproduce less well than the fluid. The area per lipid and
    thickness then describe neither phase. DPPC at the default 300 K is the
    case (41 C transition).
    """
    lipid = str(settings.get("membrane") or "").upper()
    if not lipid:
        return None
    from fastmdxplora.setup.membrane import MAIN_TRANSITION_K

    transition = MAIN_TRANSITION_K.get(lipid)
    if transition is None:
        return None
    temperature = float(settings.get("temperature_K") or 300.0)
    if temperature >= transition + NEAR_TRANSITION_K:
        return None
    celsius = transition - 273.15
    if temperature < transition:
        return Advisory(
            setting="temperature_K",
            summary=(f"{lipid} is below its main transition at {temperature:g} K "
                     f"({transition:g} K, {celsius:.0f} °C)."),
            detail=(
                "Its equilibrium there is the gel phase, which a simulation "
                "reaches, if at all, over far longer than a run, and which "
                "force fields reproduce less well than the fluid: the area "
                "per lipid and thickness describe neither phase."),
            remedy=(
                f"Simulate above {transition + NEAR_TRANSITION_K:g} K, or choose "
                "a lipid that is fluid at this temperature (POPC or DOPC)."),
        )
    return Advisory(
        setting="temperature_K",
        summary=(f"{lipid} at {temperature:g} K is {temperature - transition:.0f} K "
                 f"above its main transition ({transition:g} K)."),
        detail=(
            "The bilayer is fluid, but near a transition a force field "
            "reproduces only roughly, where its area and thickness change "
            "steeply with temperature."),
        remedy=(
            f"Simulate at {transition + NEAR_TRANSITION_K:g} K or above for a "
            "bilayer clear of it, or keep this temperature deliberately."),
    )


#: NPT equilibration under which a bilayer's area has not settled: its area
#: per lipid relaxes over nanoseconds, where water's density takes
#: picoseconds.
BILAYER_NPT_NS = 1.0


def _a_bilayer_barely_equilibrated(
        structure: dict[str, Any], settings: dict[str, Any]) -> Advisory | None:
    """A bilayer's area relaxes over nanoseconds at constant pressure."""
    if not settings.get("membrane"):
        return None
    timestep = float(settings.get("timestep_fs") or 2.0)
    if settings.get("npt_steps") is not None:
        npt_ns = int(settings["npt_steps"]) * timestep / 1e6
    elif settings.get("npt_duration_ns") is not None:
        npt_ns = float(settings["npt_duration_ns"])
    else:
        npt_ns = 1.0  # the default NPT stage
    if npt_ns >= BILAYER_NPT_NS:
        return None
    return Advisory(
        setting="npt_duration_ns",
        summary=f"{npt_ns:g} ns of NPT equilibration is short for a bilayer.",
        detail=(
            "A bilayer's area per lipid relaxes over nanoseconds at constant "
            "pressure, where water's density takes picoseconds, so production "
            "will begin while the membrane is still settling."),
        remedy=(
            "Give NPT equilibration a few nanoseconds (npt_duration_ns), or "
            "read the area per lipid the membrane analysis reports and "
            "discard the stretch before it levels off."),
    )
