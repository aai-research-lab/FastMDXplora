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

#: How much of solute-plus-padding survives as a dodecahedron's smallest
#: width. Measured rather than derived: 1.2 nm of padding on a 0.9 nm solute
#: gives 1.70 nm across where the sum says 3.3, and the ratio held near a half
#: at every padding tried.
DODECAHEDRON_WIDTH_FRACTION = 0.52

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
                  _a_ligand_with_no_chemistry, _a_density_never_equilibrated):
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
    cutoff = settings.get("nonbonded_cutoff_nm", 1.0)
    extents = structure.get("extents_angstrom")
    if padding is None or not extents:
        return None

    # A dodecahedron's smallest width is far narrower than solute plus
    # padding suggests. Measured on a capped alanine: 1.2 nm of padding on a
    # 0.9 nm solute gives 3.3 nm by that reckoning and 1.70 nm in fact, and
    # 1.6 nm of padding was the first that cleared a 1.0 nm cutoff. The ratio
    # held near a half across every padding tried, so that is the factor
    # here -- an estimate, and the run computes the box exactly.
    longest_nm = max(float(x) for x in extents) / 10.0
    across = (longest_nm + 2.0 * float(padding)) * DODECAHEDRON_WIDTH_FRACTION
    if across >= 2.0 * float(cutoff):
        return None
    return Advisory(
        setting="solvent_padding_nm",
        summary=(
            f"{padding} nm of padding on a solute {longest_nm:.1f} nm across "
            f"leaves a box perhaps {across:.1f} nm at its narrowest."),
        detail=(
            f"A {cutoff} nm cutoff needs more than twice that in the box's "
            "smallest width, or a particle interacts with its own periodic "
            "image. The box a dodecahedron makes is narrower than its "
            "longest dimension, so this is closer than it looks."),
        remedy=(
            "Raise the padding, or lower the cutoff to something the force "
            "field still supports. The run will grow the box a little on its "
            "own and refuse where that is not enough."),
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
