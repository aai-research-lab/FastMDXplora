"""Holding parts of a system still while the rest equilibrates.

A structure that has just been minimised is not at equilibrium. Heating it
lets the solvent find its arrangement, and it also lets the solute move --
side chains relax into the vacuum the crystal packing left, a ligand drifts
out of the pose that was measured, a membrane thins around a protein that has
not yet found its depth. The conventional remedy is to hold the solute in
place while the solvent equilibrates around it, and then let go in stages.

Without that, a run reaches production having already lost the arrangement it
started from, and the trajectory answers a question about a structure nobody
determined. This software had no restraints at all until now: it went from
minimisation to unrestrained dynamics in one step.

Four kinds are implemented, which is what the protocols in common use ask for:

- **position** -- hold atoms near where they are, the workhorse of
  equilibration
- **distance** -- hold two atoms, or two groups, at a separation
- **angle** -- hold three atoms at an angle
- **torsion** -- hold four atoms at a dihedral

Each is a spring rather than a wall, and every kind is written in one form,
E = k x^2 with no half (the form AMBER and CHARMM use): x is the departure
from the target, in nm or in radians, and the curvature is 2k whatever the
kind. The torsion is 2k(1 - cos x), the same near its target and continuous
across pi. GROMACS and PLUMED write 0.5 k x^2, so their k is twice this one
for the same spring; this package's umbrella and steered windows follow
PLUMED. A restraint given no target is held where it is in the structure
setup prepared (the reference a position restraint holds to, from before
minimisation, which runs with the restraints on), for every kind. That matters for what a
restrained run means. A constrained atom cannot move; a restrained one can,
and the restraint says how much it cost. Reporting a restrained trajectory as
though it were free is a claim the simulation does not support, so the
restraints in force at each stage are recorded with the results.

**Restraints do not survive into production.** A biased production run
measures the bias. They are released before it, and a run that keeps them
must say so.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any
from fastmdxplora.refusals import StudyError

__all__ = [
    "Restraint",
    "PositionRestraint",
    "DistanceRestraint",
    "AngleRestraint",
    "TorsionRestraint",
    "ReleaseSchedule",
    "build_restraint_forces",
    "parse_restraints",
    "resolve_restraints",
    "DEFAULT_POSITION_FORCE",
    "ENERGY_FORM",
]


#: A force constant in kJ/mol/nm², in the form E = k d², that holds heavy
#: atoms firmly without distorting them: each coordinate fluctuates by
#: sqrt(kT / 2k), 0.035 nm at 300 K. It is the first step of the default
#: release ladder, and GROMACS's usual 1000 in its 0.5 k d² form is 500 here.
DEFAULT_POSITION_FORCE = 1000.0

#: The energy every kind is written in, said with the record so a reader
#: comparing against another program can convert.
ENERGY_FORM = "k*x^2 (no half); torsion 2k*(1 - cos x)"

#: A selection that lists atoms by index, the one form whose order can be
#: read: an angle's apex and a dihedral's atoms are taken as written there.
_INDEX_LIST = re.compile(r"^\s*index\s+(\d+(?:\s+\d+)*)\s*$")


@dataclass(frozen=True)
class Restraint:
    """What is held, and how firmly.

    ``force_constant`` is in kJ/mol/nm² for position and distance restraints
    and kJ/mol/rad² for angle and torsion. The units differ because the
    coordinate does: a spring on a length and a spring on an angle are not
    measured in the same thing, and quietly using one number for both is how
    an angle restraint ends up a thousand times too weak.
    """

    selection: str
    force_constant: float = DEFAULT_POSITION_FORCE
    #: Where to hold it, in nm or radians. ``None`` means "where it is now":
    #: the value in the reference positions, filled in by
    #: :func:`resolve_restraints`.
    target: float | None = None
    #: Whether ``force_constant`` was written. One that was not (the short
    #: form, or a position block without one) takes the release ladder's
    #: first step, so a ladder written in kJ/mol/nm² keeps its meaning.
    stated: bool = True
    #: Set by each subclass. Declared last so a subclass overriding it does
    #: not leave a defaulted field ahead of a required one.
    kind: str = "position"

    @property
    def units(self) -> str:
        return ("kJ/mol/nm^2" if self.kind in ("position", "distance")
                else "kJ/mol/rad^2")

    def as_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selection": self.selection,
            "force_constant": self.force_constant,
            "units": self.units,
            "target": self.target,
            "energy": ENERGY_FORM,
        }


@dataclass(frozen=True)
class PositionRestraint(Restraint):
    kind: str = "position"


@dataclass(frozen=True)
class DistanceRestraint(Restraint):
    kind: str = "distance"


@dataclass(frozen=True)
class AngleRestraint(Restraint):
    kind: str = "angle"


@dataclass(frozen=True)
class TorsionRestraint(Restraint):
    kind: str = "torsion"


@dataclass(frozen=True)
class ReleaseSchedule:
    """How a restraint weakens as equilibration proceeds.

    Letting go all at once undoes the point of restraining: the solute is
    released into a solvent arrangement that formed around a structure held
    rigid, and the sudden freedom shows up as a jump in energy and a lurch in
    the structure. Standard protocols step the force down instead -- 1000,
    500, 100, 0 in kJ/mol/nm² is the shape of it -- so each stage starts from
    something the previous one prepared.

    The steps are spread across the equilibration stages. The last is applied
    to the end of NPT, and production always runs at zero unless somebody
    asks otherwise and is told what that means.

    The steps are in kJ/mol/nm² and are what a restraint with no force
    constant of its own runs at. One with its own constant is stepped down in
    the same proportions, ``step / steps[0]`` of it, so a torsion asked for at
    50 kJ/mol/rad² is not run at 1000 of a length's units.
    """

    steps: tuple[float, ...] = (1000.0, 500.0, 100.0, 0.0)

    def force_at(self, fraction: float) -> float:
        """The force constant at a point through equilibration.

        ``fraction`` runs from 0 at the start to 1 at the end.
        """
        if not self.steps:
            return 0.0
        if fraction >= 1.0:
            return float(self.steps[-1])
        index = min(int(fraction * len(self.steps)), len(self.steps) - 1)
        return float(self.steps[index])

    def fraction_at(self, fraction: float) -> float:
        """The share of each restraint's own force constant applied at a
        point through equilibration: the step there over the first step."""
        if not self.steps or float(self.steps[0]) <= 0.0:
            return 0.0
        return self.force_at(fraction) / float(self.steps[0])

    def check(self) -> None:
        """Refuse a ladder that cannot be read as one."""
        if not self.steps:
            raise StudyError(
                "`restraint_release` is empty: a ladder needs at least the "
                "strength the restraints start at.",
                code="config.option.out_of_range", option="restraint_release",
                context="simulation")
        if any(float(step) < 0.0 for step in self.steps):
            raise StudyError(
                f"`restraint_release` {list(self.steps)} has a negative step; "
                "a negative force constant pushes atoms away from where they "
                "are held.",
                code="config.option.out_of_range", option="restraint_release",
                context="simulation")
        if float(self.steps[0]) <= 0.0:
            raise StudyError(
                f"`restraint_release` {list(self.steps)} starts at zero, so "
                "nothing is ever held. Its first step is the strength the "
                "restraints start at.",
                code="config.option.out_of_range", option="restraint_release",
                context="simulation")
        if any(float(step) > float(self.steps[0]) for step in self.steps):
            raise StudyError(
                f"`restraint_release` {list(self.steps)} rises above its first "
                "step. A restraint with its own force constant is stepped in "
                "the ladder's proportions, so it would be held more firmly than "
                "it was asked to be; the first step is the strongest.",
                code="config.option.out_of_range", option="restraint_release",
                context="simulation")

    def as_record(self) -> dict[str, Any]:
        return {"steps_kjmol_per_nm2": list(self.steps)}


def _check_target(kind: str, target: float) -> None:
    """Refuse a target outside what the coordinate can be: an angle given in
    degrees (109.5) was held at 109.5 rad and pushed to pi."""
    bounds = {"distance": (0.0, math.inf, "nm, more than zero"),
              "angle": (0.0, math.pi, "radians, from 0 to pi"),
              "torsion": (-math.pi, math.pi, "radians, from -pi to pi")}
    if kind not in bounds:
        return
    low, high, said = bounds[kind]
    outside = (target <= low) if kind == "distance" else not (low <= target <= high)
    if outside or not math.isfinite(target):
        raise StudyError(
            f"{'An' if kind == 'angle' else 'A'} {kind} restraint's target of "
            f"{target:g} is not {'an' if kind == 'angle' else 'a'} {kind}: it is "
            f"given in {said}" + (" (degrees are not read)" if kind != "distance"
                                  else "") + ".",
            code="config.option.out_of_range", option="restrain.target",
            context="simulation", given=target,
            minimum=low, maximum=None if math.isinf(high) else high)


#: What a block of the long form may say.
_BLOCK_KEYS = {"kind", "selection", "force_constant", "target"}


def parse_restraints(spec: Any) -> list[Restraint]:
    """Read restraints from a config block.

    Accepts the short form -- a selection string, meaning position restraints
    on it at the default force -- and the long form, a list of blocks. The
    short form is what an equilibration usually wants and should not require
    a paragraph to say.
    """
    if spec is None or spec == "":
        return []

    if isinstance(spec, str):
        return [PositionRestraint(selection=spec, stated=False)]

    if isinstance(spec, dict):
        spec = [spec]

    found: list[Restraint] = []
    classes = {
        "position": PositionRestraint,
        "distance": DistanceRestraint,
        "angle": AngleRestraint,
        "torsion": TorsionRestraint,
    }
    for number, entry in enumerate(spec, start=1):
        if isinstance(entry, str):
            found.append(PositionRestraint(selection=entry, stated=False))
            continue
        if not isinstance(entry, dict):
            raise StudyError(
                f"Restraint {number} is {entry!r}: each is a selection, or a "
                "block with `kind`, `selection`, `force_constant` and `target`.",
                code="config.option.wrong_type", option="restrain",
                context="simulation", expected_type="string or mapping",
                found_type=type(entry).__name__)
        unknown = sorted(set(entry) - _BLOCK_KEYS)
        if unknown:
            # Ignored, `taget: 0.35` held the restraint where it started.
            import difflib

            near = difflib.get_close_matches(unknown[0], sorted(_BLOCK_KEYS), n=1)
            raise StudyError(
                f"Restraint {number} has {', '.join(repr(k) for k in unknown)}, "
                f"which a restraint does not take"
                + (f" (did you mean {near[0]!r}?)" if near else "")
                + f". It takes {', '.join(sorted(_BLOCK_KEYS))}.",
                code="config.option.unknown", option=unknown[0],
                context="simulation.restrain", permitted=sorted(_BLOCK_KEYS),
                suggestion=near[0] if near else None)
        kind = str(entry.get("kind", "position")).lower()
        if kind not in classes:
            raise StudyError(
                f"Unknown restraint kind {kind!r}. "
                f"Valid: {', '.join(sorted(classes))}."
            , code="simulation.cv.unknown")
        selection = entry.get("selection")
        if not selection:
            raise StudyError(
                f"A {kind} restraint needs a `selection` saying what it holds."
            , code="simulation.cv.missing_companion")
        default_force = (DEFAULT_POSITION_FORCE if kind == "position"
                         else None)
        force = entry.get("force_constant", default_force)
        if force is None:
            raise StudyError(
                f"A {kind} restraint needs a `force_constant`. There is no "
                "conventional default for it the way there is for position "
                "restraints, and guessing one would be inventing the strength "
                "of a bias."
            , code="simulation.bias.parameter_missing")
        if not math.isfinite(float(force)) or float(force) < 0.0:
            raise StudyError(
                f"Restraint {number}'s force constant {force!r} is not a spring: "
                + ("a negative one pushes the atoms away from where they are held."
                   if math.isfinite(float(force)) else "it is not a finite number."),
                code="config.option.out_of_range", option="restrain.force_constant",
                context="simulation", given=force, minimum=0.0)
        target = entry.get("target")
        if target is not None and kind == "position":
            raise StudyError(
                f"Restraint {number} is a position restraint with a `target`: a "
                "position restraint holds each atom where it starts, and has no "
                "single value to hold it at.",
                code="config.option.inapplicable", option="restrain.target",
                context="simulation")
        if target is not None:
            _check_target(kind, float(target))
        found.append(classes[kind](
            selection=str(selection),
            force_constant=float(force),
            target=(None if entry.get("target") is None
                    else float(entry["target"])),
            stated=entry.get("force_constant") is not None,
        ))
    return found


def _in_nm(positions: Any) -> Any:
    """Positions as a plain array in nm, from a Quantity or an array."""
    import numpy as np

    try:
        from openmm import unit

        return np.asarray(positions.value_in_unit(unit.nanometer), dtype=float)
    except (AttributeError, ImportError):
        return np.asarray(positions, dtype=float)


def _atoms_of(mdtop: Any, restraint: Restraint) -> list[int]:
    """The atoms a restraint holds, in the order that defines it.

    A selection is a set and comes back in index order, which is harmless for
    a distance and for an angle or dihedral read the other way round (both
    are unchanged by reversal), but moves an angle's apex or reorders a
    dihedral otherwise: "index 8 6 4 14" came back as 4, 6, 8, 14. An index
    list is the one form that states an order, so its order is kept.
    """
    try:
        atoms = [int(a) for a in mdtop.select(restraint.selection)]
    except Exception as exc:  # noqa: BLE001 - a bad selection is the user's
        raise StudyError(
            f"The restraint selection {restraint.selection!r} could not be "
            f"read: {exc}"
        , code="simulation.cv.selection_empty") from exc
    if len(atoms) == 0:
        raise StudyError(
            f"The restraint selection {restraint.selection!r} matched no "
            "atoms. A restraint on nothing holds nothing, and a run that "
            "silently applied it would look restrained and not be."
        , code="simulation.cv.selection_empty")
    written = _INDEX_LIST.match(restraint.selection)
    if written:
        listed = [int(a) for a in written.group(1).split()]
        if len(set(listed)) == len(listed) and sorted(listed) == sorted(atoms):
            return listed
    return atoms


_ARITY = {"distance": (2, "two"), "angle": (3, "three"), "torsion": (4, "four")}


def _nearest_image(vector: Any, box: Any) -> Any:
    """A separation as OpenMM measures it under periodic boundaries: the
    nearest image in a reduced triclinic box, c then b then a."""
    if box is None:
        return vector
    a, b, c = box
    vector = vector - c * round(float(vector[2] / c[2]))
    vector = vector - b * round(float(vector[1] / b[1]))
    return vector - a * round(float(vector[0] / a[0]))


def _value_now(kind: str, xyz: Any, box: Any = None) -> float:
    """The distance (nm), angle or dihedral (rad) of these atoms, measured as
    OpenMM measures it: the angle at the middle atom, the dihedral by the
    IUPAC convention in (-pi, pi], each bond vector the nearest image when
    the box is given."""
    import numpy as np

    def bond(i: int, j: int) -> Any:
        return _nearest_image(xyz[j] - xyz[i], box)

    if kind == "distance":
        return float(np.linalg.norm(bond(0, 1)))
    if kind == "angle":
        a, b = bond(1, 0), bond(1, 2)
        cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        return float(math.acos(max(-1.0, min(1.0, cosine))))
    b1, b2, b3 = bond(0, 1), bond(1, 2), bond(2, 3)
    n1, n2 = np.cross(b1, b2), np.cross(b2, b3)
    y = float(np.linalg.norm(b2) * np.dot(b1, n2))
    return float(math.atan2(y, float(np.dot(n1, n2))))


def resolve_restraints(
    topology: Any,
    positions: Any,
    restraints: list[Restraint],
    *,
    box: Any = None,
) -> list[tuple[Restraint, list[int]]]:
    """Each restraint with its atoms in order and its target filled in.

    A distance, angle or torsion given no target was held at zero: two atoms
    pulled onto each other, an angle folded shut, a dihedral forced cis,
    while ``target: None`` was documented as "where it is now". It is now
    that: the value in ``positions``, which the runner takes from the
    structure setup prepared, as it does for a position restraint's
    reference. ``box`` (three box vectors, nm) makes every separation the
    nearest image, as the forces measure it in a periodic system: setup
    writes each molecule wrapped into the box, so a ligand and the residue it
    sits against can lie a box apart in the coordinates.
    """
    import mdtraj as md

    mdtop = md.Topology.from_openmm(topology)
    xyz = None
    resolved: list[tuple[Restraint, list[int]]] = []
    for restraint in restraints:
        atoms = _atoms_of(mdtop, restraint)
        if restraint.kind in _ARITY:
            wanted, word = _ARITY[restraint.kind]
            if len(atoms) != wanted:
                article = "An" if restraint.kind == "angle" else "A"
                raise StudyError(
                    f"{article} {restraint.kind} restraint holds {word} atoms; "
                    f"{restraint.selection!r} matched {len(atoms)}."
                , code="simulation.restraint.selection_arity")
            if restraint.target is None:
                if xyz is None:
                    xyz = _in_nm(positions)
                restraint = replace(restraint, target=_value_now(
                    restraint.kind, xyz[atoms], _box_in_nm(box)))
        resolved.append((restraint, atoms))
    return resolved


def _box_in_nm(box: Any) -> Any:
    if box is None:
        return None
    return _in_nm(box)


def build_restraint_forces(
    omm: Any,
    topology: Any,
    positions: Any,
    restraints: list[Restraint],
    *,
    box: Any = None,
) -> list[tuple[Any, str]]:
    """:func:`build_resolved_restraint_forces` without the resolutions."""
    return [(force, parameter) for force, parameter, _restraint, _atoms in
            build_resolved_restraint_forces(omm, topology, positions,
                                            restraints, box=box)]


def build_resolved_restraint_forces(
    omm: Any,
    topology: Any,
    positions: Any,
    restraints: list[Restraint],
    *,
    box: Any = None,
) -> list[tuple[Any, str, Restraint, list[int]]]:
    """Turn restraints into OpenMM forces, paired with the parameter that
    scales each one.

    The parameter is what makes staged release possible: a force added to a
    System cannot be removed once the context exists, but a global parameter
    can be set to zero, which is the same thing to every atom in the system
    and much cheaper than rebuilding. It holds the restraint's force
    constant at full strength and is set by the runner as the ladder steps.

    With ``box`` (a periodic system's box vectors) the distance, angle and
    torsion forces measure the nearest image, as a position restraint's
    `periodicdistance` does: without it a ligand wrapped a box away from
    the residue it is restrained to was pulled through the box.
    """
    forces: list[tuple[Any, str, Restraint, list[int]]] = []
    periodic = box is not None

    for index, (restraint, atoms) in enumerate(
            resolve_restraints(topology, positions, restraints, box=box)):
        parameter = f"restraint_k_{index}"

        if restraint.kind == "position":
            # periodicdistance, so an atom that wraps across the boundary is
            # not yanked the width of the box back to its reference point.
            force = omm.CustomExternalForce(
                f"{parameter}*periodicdistance(x, y, z, x0, y0, z0)^2")
            force.addGlobalParameter(parameter, restraint.force_constant)
            for name in ("x0", "y0", "z0"):
                force.addPerParticleParameter(name)
            for atom in atoms:
                reference = positions[int(atom)]
                force.addParticle(int(atom), [reference[0], reference[1],
                                              reference[2]])
        elif restraint.kind == "distance":
            force = omm.CustomBondForce(f"{parameter}*(r - r0)^2")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerBondParameter("r0")
            force.addBond(atoms[0], atoms[1], [restraint.target])
            if periodic:
                force.setUsesPeriodicBoundaryConditions(True)
        elif restraint.kind == "angle":
            force = omm.CustomAngleForce(f"{parameter}*(theta - theta0)^2")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerAngleParameter("theta0")
            force.addAngle(*atoms, [restraint.target])
            if periodic:
                force.setUsesPeriodicBoundaryConditions(True)
        else:  # torsion
            # Written through cos so the penalty is continuous across the
            # wrap at pi: a plain (theta - theta0)^2 jumps by 4*pi^2 there and
            # kicks the atoms apart. 2k so its curvature at the target is the
            # 2k of k*x^2, as for every other kind; k*(1 - cos) was half as
            # stiff as an angle restraint given the same number.
            force = omm.CustomTorsionForce(
                f"2*{parameter}*(1 - cos(theta - theta0))")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerTorsionParameter("theta0")
            force.addTorsion(*atoms, [restraint.target])
            if periodic:
                force.setUsesPeriodicBoundaryConditions(True)

        forces.append((force, parameter, restraint, atoms))
    return forces
