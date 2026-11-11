"""A machine-learned potential for part of the system.

The atoms named (the ligand, unless ``atoms`` names others) have their
energy within themselves taken from a neural network trained on quantum
chemistry, in place of the force field's bonds, angles, torsions and
nonbonded terms between them; their interactions with everything else stay
the force field's. This is openmm-ml's mixed system (Eastman et al., J.
Phys. Chem. B 128, 109 (2024)), built once the force field has made the
whole system, so the run itself needs nothing else.

The default model is ANI-2x (Devereux et al., J. Chem. Theory Comput. 16,
4192 (2020)), which knows H, C, N, O, F, S and Cl and neutral molecules
only, and which is refused for anything else rather than asked to
extrapolate. Other models openmm-ml knows (``mace-off23-small``,
``aimnet2``, ...) are taken by name where their own packages are installed.

What it costs: the network is evaluated every step, on the GPU where there
is one, and a run is slower than with the force field alone, roughly in
proportion to the atoms given to it, which are limited to 300.

Why: a ligand's torsions and its internal strain are where a general force
field such as GAFF or OpenFF is least reliable, and a model trained on
quantum chemistry is closer to it there.
"""

from __future__ import annotations

from typing import Any

from fastmdxplora.refusals import StudyError

#: The elements each model was trained on, where it is a short list.
ELEMENTS = {"ani2x": {"H", "C", "N", "O", "F", "S", "Cl"},
            "ani1ccx": {"H", "C", "N", "O"}}
#: Models that describe neutral molecules only.
NEUTRAL_ONLY = {"ani2x", "ani1ccx"}
MOST_ATOMS = 300
KEYS = {"model", "atoms"}


def check_block(block: Any) -> dict[str, str]:
    """The block's settings, checked before anything is built."""
    from fastmdxplora.config.loader import ConfigError

    if not isinstance(block, dict):
        raise ConfigError("`setup.ml_potential` is a block (`model`, `atoms`), not a "
                          "single value.", code="config.option.wrong_type")
    unknown = sorted(set(block) - KEYS)
    if unknown:
        raise ConfigError(f"`setup.ml_potential` has no setting {', '.join(unknown)}; it "
                          f"takes {', '.join(sorted(KEYS))}.", code="config.option.unknown")
    return {"model": str(block.get("model") or "ani2x"), "atoms": str(block.get("atoms") or "")}


def with_ml_potential(system: Any, topology: Any, block: dict[str, Any],
                      ligand_names: list[str] | None = None) -> tuple[Any, dict[str, Any]]:
    """``system`` with the named atoms' energy within themselves from the
    model, and a record of what was done."""
    import mdtraj as md
    import openmm

    settings = check_block(block)
    model = settings["model"]
    said = settings["atoms"] or (" or ".join(f"resname {n}" for n in (ligand_names or []))
                                 if ligand_names else "")
    if not said:
        raise StudyError(
            "The study has no ligand, so `setup.ml_potential.atoms` has to name the atoms "
            "the model describes.", code="simulation.method.inapplicable")
    md_topology = md.Topology.from_openmm(topology)
    try:
        atoms = [int(i) for i in md_topology.select(said)]
    except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
        raise StudyError(f"`setup.ml_potential.atoms` {said!r} is not a selection MDTraj "
                         f"reads: {exc}", code="simulation.cv.selection_empty") from exc
    if not atoms:
        raise StudyError(f"`setup.ml_potential.atoms` {said!r} matched no atoms.",
                         code="simulation.cv.selection_empty")
    if len(atoms) > MOST_ATOMS:
        raise StudyError(
            f"{len(atoms)} atoms given to the model; {MOST_ATOMS} is the most, as it is "
            "evaluated every step. Give it the ligand, or the part of the protein that "
            "matters, not the protein.", code="simulation.method.inapplicable")
    elements = {md_topology.atom(i).element.symbol for i in atoms
                if md_topology.atom(i).element is not None}
    known = ELEMENTS.get(model)
    if known is not None and not elements <= known:
        raise StudyError(
            f"{model} was trained on {', '.join(sorted(known))}, and the atoms given hold "
            f"{', '.join(sorted(elements - known))}. Choose another model, or leave those "
            "atoms to the force field.", code="simulation.method.inapplicable")
    if model in NEUTRAL_ONLY:
        nonbonded = next(f for f in system.getForces() if isinstance(f, openmm.NonbondedForce))
        charge = sum(nonbonded.getParticleParameters(i)[0]._value for i in atoms)
        if abs(charge) > 0.01:
            raise StudyError(
                f"The atoms given carry a net charge of {charge:+.2f} e, and {model} "
                "describes neutral molecules only. Choose a model that takes a charge.",
                code="simulation.method.inapplicable")
    try:
        from openmmml import MLPotential
    except ImportError as exc:
        from fastmdxplora.dependencies import MissingBackendError, MissingDependency

        raise MissingBackendError([MissingDependency("openmm-ml", "openmmml", "openmm-ml")]) from exc
    try:
        potential = MLPotential(model)
        mixed = potential.createMixedSystem(topology, system, atoms)
    except (ImportError, ModuleNotFoundError) as exc:
        from fastmdxplora.dependencies import MissingBackendError, MissingDependency

        package = {"ani2x": "torchani", "ani1ccx": "torchani"}.get(model, model)
        raise MissingBackendError([MissingDependency(model, package, package)]) from exc
    except ValueError as exc:
        raise StudyError(f"openmm-ml does not know the model {model!r}: {exc}",
                         code="config.option.out_of_range") from exc
    record = {"model": model, "atoms": said, "count": len(atoms),
              "elements": sorted(elements),
              "replaced": "the atoms' energy within themselves; their interactions with "
                          "everything else stay the force field's",
              "by": "openmm-ml's mixed system"}
    return mixed, record
