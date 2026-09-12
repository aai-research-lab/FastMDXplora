"""Holding parts of a system still while the rest settles.

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

Each is a harmonic penalty: the force grows with the square of the departure,
so a restraint is a spring rather than a wall. That matters for what a
restrained run means. A constrained atom cannot move; a restrained one can,
and the restraint says how much it cost. Reporting a restrained trajectory as
though it were free is a claim the simulation does not support, so the
restraints in force at each stage are recorded with the results.

**Restraints do not survive into production.** A biased production run
measures the bias. They are released before it, and a run that keeps them
must say so.
"""

from __future__ import annotations

from dataclasses import dataclass
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
]


#: A force constant in kJ/mol/nm² that holds heavy atoms firmly without
#: distorting them. The value in common use across AMBER, GROMACS and CHARMM
#: protocols, quoted for position restraints on protein heavy atoms.
DEFAULT_POSITION_FORCE = 1000.0


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
    #: Where to hold it. ``None`` means "where it is now", which is what a
    #: position restraint almost always wants.
    target: float | None = None
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

    def as_record(self) -> dict[str, Any]:
        return {"steps_kjmol_per_nm2": list(self.steps)}


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
        return [PositionRestraint(selection=spec)]

    if isinstance(spec, dict):
        spec = [spec]

    found: list[Restraint] = []
    classes = {
        "position": PositionRestraint,
        "distance": DistanceRestraint,
        "angle": AngleRestraint,
        "torsion": TorsionRestraint,
    }
    for entry in spec:
        if isinstance(entry, str):
            found.append(PositionRestraint(selection=entry))
            continue
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
        found.append(classes[kind](
            selection=str(selection),
            force_constant=float(force),
            target=(None if entry.get("target") is None
                    else float(entry["target"])),
        ))
    return found


def build_restraint_forces(
    omm: Any,
    topology: Any,
    positions: Any,
    restraints: list[Restraint],
) -> list[tuple[Any, str]]:
    """Turn restraints into OpenMM forces, paired with the parameter that
    scales each one.

    The parameter is what makes staged release possible: a force added to a
    System cannot be removed once the context exists, but a global parameter
    can be set to zero, which is the same thing to every atom in the system
    and much cheaper than rebuilding.
    """
    import mdtraj as md

    forces: list[tuple[Any, str]] = []
    mdtop = md.Topology.from_openmm(topology)

    for index, restraint in enumerate(restraints):
        try:
            atoms = mdtop.select(restraint.selection)
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
            if len(atoms) != 2:
                raise StudyError(
                    f"A distance restraint holds two atoms; "
                    f"{restraint.selection!r} matched {len(atoms)}."
                , code="simulation.restraint.selection_arity")
            force = omm.CustomBondForce(f"{parameter}*(r - r0)^2")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerBondParameter("r0")
            force.addBond(int(atoms[0]), int(atoms[1]),
                          [restraint.target if restraint.target is not None
                           else 0.0])
        elif restraint.kind == "angle":
            if len(atoms) != 3:
                raise StudyError(
                    f"An angle restraint holds three atoms; "
                    f"{restraint.selection!r} matched {len(atoms)}."
                , code="simulation.restraint.selection_arity")
            force = omm.CustomAngleForce(f"{parameter}*(theta - theta0)^2")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerAngleParameter("theta0")
            force.addAngle(*(int(a) for a in atoms),
                           [restraint.target or 0.0])
        else:  # torsion
            if len(atoms) != 4:
                raise StudyError(
                    f"A torsion restraint holds four atoms; "
                    f"{restraint.selection!r} matched {len(atoms)}."
                , code="simulation.restraint.selection_arity")
            # Written through cos so the penalty is continuous across the
            # wrap at pi: a plain (theta - theta0)^2 jumps by 4*pi^2 there and
            # kicks the atoms apart.
            force = omm.CustomTorsionForce(
                f"{parameter}*(1 - cos(theta - theta0))")
            force.addGlobalParameter(parameter, restraint.force_constant)
            force.addPerTorsionParameter("theta0")
            force.addTorsion(*(int(a) for a in atoms),
                             [restraint.target or 0.0])

        forces.append((force, parameter))
    return forces
