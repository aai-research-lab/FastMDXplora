"""Alchemical free energy: the free energy of switching a group's
interactions with everything around it off, one leg of a binding or a
solvation free energy.

A group (the ligand, unless ``group`` names another) has its charges turned
off first and then its Lennard-Jones interactions, through a schedule of
intermediate states (``lambda_electrostatics``, ``lambda_sterics``; 1 is the
group as it is, 0 the group decoupled), with a soft core so atoms can pass
through it as it fades (Beutler et al., Chem. Phys. Lett. 222, 529 (1994)).
The group's interactions within itself are kept: it is decoupled, not
annihilated, so what is measured is its interaction with its surroundings.
The states are sampled together by Hamiltonian replica exchange and the free
energy between them estimated by MBAR (Shirts and Chodera, J. Chem. Phys.
129, 124105 (2008)), through openmmtools.

``dG_decouple = G(decoupled) - G(coupled)`` is positive where the group is
held by its surroundings. A solvation free energy is minus the decoupling
from water; a binding free energy takes two legs, each a study of its own::

    dG_bind = dG_decouple(ligand in water) - dG_decouple(ligand in complex)
              - dG_restraint

where the complex leg holds the ligand in its site with a flat-bottomed
restraint (``restraint``: the ligand's centre within ``radius_nm`` of the
receptor atoms named, beyond which a harmonic wall), and ``dG_restraint`` is
the free energy of confining the decoupled ligand from the standard
concentration (1 M) to that volume, ``-kT ln(V / V0)``, computed here by
integrating the restraint's Boltzmann factor. It is recorded with the
complex leg; the solvent leg needs none.

What says the estimate can be believed is the **overlap** between
neighbouring states: below 0.03, MBAR's estimate between them rests on
almost no shared samples, and a state is missing from the schedule there.
The estimate's uncertainty is MBAR's, from the samples' statistical
inefficiency.

The trajectory written is of whichever replica held the fully coupled state
at each exchange: the ordinary ensemble, and not one continuous path.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.config.loader import ConfigError
from fastmdxplora.refusals import StudyError

KB = 0.0083144626  # kJ/mol/K
#: The volume of one molecule at 1 M, nm^3.
STANDARD_VOLUME_NM3 = 1.0 / (6.02214076e23 * 1e-24)
#: The least overlap between neighbouring states an estimate rests on.
LOW_OVERLAP = 0.03
KEYS = {"group", "lambda_electrostatics", "lambda_sterics", "exchange_interval_steps",
        "equilibration_iterations", "restraint"}
#: Charges off in five steps, then the Lennard-Jones in eleven, the soft
#: core taking the steps close to zero.
DEFAULT_ELECTROSTATICS = [1.0, 0.75, 0.5, 0.25, 0.0] + [0.0] * 11
DEFAULT_STERICS = [1.0] * 5 + [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.0]


@dataclass
class Plan:
    group: str
    electrostatics: list[float]
    sterics: list[float]
    exchange_interval_steps: int
    equilibration_iterations: int
    restraint: dict[str, Any] | None = field(default=None)


def plan_from_block(block: dict[str, Any] | None, ligand_resname: str | None = None) -> Plan:
    block = dict(block or {})
    unknown = sorted(set(block) - KEYS)
    if unknown:
        raise ConfigError(
            f"`alchemical` has no setting {', '.join(unknown)}; it takes "
            f"{', '.join(sorted(KEYS))}.", code="config.option.unknown")
    group = block.get("group") or (f"resname {ligand_resname}" if ligand_resname else "")
    electrostatics = [float(v) for v in block.get("lambda_electrostatics",
                                                   DEFAULT_ELECTROSTATICS)]
    sterics = [float(v) for v in block.get("lambda_sterics", DEFAULT_STERICS)]
    if len(electrostatics) != len(sterics) or len(sterics) < 2:
        raise ConfigError(
            "`alchemical.lambda_electrostatics` and `lambda_sterics` are one schedule: "
            f"as long as each other and two states at least; they have "
            f"{len(electrostatics)} and {len(sterics)}.", code="config.option.out_of_range")
    if any(not 0.0 <= v <= 1.0 for v in electrostatics + sterics):
        raise ConfigError("Every lambda is from 0 to 1.", code="config.option.out_of_range")
    if (electrostatics[0], sterics[0]) != (1.0, 1.0) or (electrostatics[-1], sterics[-1]) != (0.0, 0.0):
        raise ConfigError(
            "The schedule runs from the group as it is (both lambdas 1) to the group "
            "decoupled (both 0).", code="config.option.out_of_range")
    if any(e > 0 and s < 1 for e, s in zip(electrostatics, sterics)):
        raise ConfigError(
            "A state with charges on and Lennard-Jones partly off lets opposite charges "
            "meet with nothing to keep them apart: turn the charges off first.",
            code="config.option.conflicting")
    interval = int(block.get("exchange_interval_steps", 500))
    if interval < 10:
        raise ConfigError(f"`alchemical.exchange_interval_steps` is at least 10; it was "
                          f"{interval}.", code="config.option.out_of_range")
    restraint = block.get("restraint")
    if restraint is not None:
        if not isinstance(restraint, dict) or not restraint.get("receptor"):
            raise ConfigError(
                "`alchemical.restraint` is a block with `receptor` (the atoms whose "
                "centre holds the ligand), and `radius_nm` and `force_constant`.",
                code="config.option.missing_companion")
        restraint = {"receptor": str(restraint["receptor"]),
                     "radius_nm": float(restraint.get("radius_nm", 0.5)),
                     "force_constant": float(restraint.get("force_constant", 1000.0))}
    return Plan(group=str(group), electrostatics=electrostatics, sterics=sterics,
                exchange_interval_steps=interval,
                equilibration_iterations=int(block.get("equilibration_iterations", 50)),
                restraint=restraint)


def restraint_free_energy(radius_nm: float, force_constant: float, temperature_K: float) -> float:
    """``-kT ln(V / V0)``, kJ/mol: confining a free molecule's centre from
    the standard volume to a flat-bottomed well of ``radius_nm`` with a
    harmonic wall ``force_constant / 2 (r - radius)^2`` beyond it."""
    kt = KB * temperature_K
    beyond = np.linspace(0.0, radius_nm + 20 * math.sqrt(kt / force_constant), 20001)
    weight = np.where(beyond <= radius_nm, 1.0,
                      np.exp(-0.5 * force_constant * (beyond - radius_nm) ** 2 / kt))
    volume = float(np.trapezoid(4 * np.pi * beyond ** 2 * weight, beyond))
    return -kt * math.log(volume / STANDARD_VOLUME_NM3)


def binding_free_energy(complex_leg: dict[str, Any], solvent_leg: dict[str, Any]
                        ) -> dict[str, float]:
    """The binding free energy from two legs' records (``alchemical.json``):
    the solvent leg's decoupling less the complex leg's, less the complex
    leg's restraint correction, kJ/mol, with the uncertainties added in
    quadrature."""
    restraint = (complex_leg.get("restraint") or {}).get("dG_restraint_kj_per_mol")
    if restraint is None:
        raise StudyError(
            "The complex leg was run without a restraint, so the decoupled ligand was "
            "free to leave its site and the leg has no standard state: rerun it with "
            "`alchemical.restraint`.", code="simulation.method.inapplicable")
    value = (float(solvent_leg["dG_decouple_kj_per_mol"])
             - float(complex_leg["dG_decouple_kj_per_mol"]) - float(restraint))
    error = math.hypot(float(solvent_leg["uncertainty_kj_per_mol"]),
                       float(complex_leg["uncertainty_kj_per_mol"]))
    return {"dG_bind_kj_per_mol": value, "uncertainty_kj_per_mol": error,
            "dG_bind_kcal_per_mol": value / 4.184}


def neighbour_overlap(matrix: np.ndarray) -> list[float]:
    return [float(matrix[k, k + 1]) for k in range(len(matrix) - 1)]


def run_alchemical(*, system_xml: Path, state_xml: Path, topology_pdb: Path, output_dir: Path,
                   block: dict[str, Any], ligand_resname: str | None, temperature_K: float,
                   timestep_fs: float, friction_per_ps: float, production_steps: int,
                   pressure_bar: float | None, save_selection: str | None,
                   platform: str = "auto", random_seed: int | None = None,
                   on_progress: Any = None) -> dict[str, Any]:
    """Decouple the group through the schedule, estimate the free energy,
    and write the coupled state's frames as the run's trajectory."""
    try:
        import mdtraj as md
        import openmm
        from openmm import unit
        from openmmtools import alchemy, states
        from openmmtools.multistate import MultiStateSamplerAnalyzer
    except ImportError as exc:
        from fastmdxplora.dependencies import MissingBackendError, MissingDependency

        raise MissingBackendError([
            MissingDependency("openmmtools", "openmmtools", "openmmtools"),
            MissingDependency("pymbar", "pymbar", "pymbar")]) from exc

    from fastmdxplora.simulation.replica_exchange import copy_state, sample_states

    plan = plan_from_block(block, ligand_resname)
    if not plan.group:
        raise ConfigError(
            "The study names no ligand; name the group to decouple in `alchemical.group`.",
            code="config.option.missing_companion")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    system = openmm.XmlSerializer.deserialize(Path(system_xml).read_text(encoding="utf-8"))
    state = openmm.XmlSerializer.deserialize(Path(state_xml).read_text(encoding="utf-8"))
    topology = md.load_topology(str(topology_pdb))

    def select(said: str, role: str) -> list[int]:
        try:
            found = [int(i) for i in topology.select(said)]
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
            raise StudyError(f"The {role} {said!r} is not a selection MDTraj reads: {exc}",
                             code="simulation.cv.selection_empty") from exc
        if not found:
            raise StudyError(f"The {role} {said!r} matched no atoms.",
                             code="simulation.cv.selection_empty")
        return found

    group = select(plan.group, "group")
    restraint_energy = None
    if plan.restraint:
        receptor = select(plan.restraint["receptor"], "restraint's receptor")
        wall = openmm.CustomCentroidBondForce(
            2, "step(d - r0) * 0.5 * k_wall * (d - r0)^2; d = distance(g1, g2)")
        wall.addGlobalParameter("r0", plan.restraint["radius_nm"])
        wall.addGlobalParameter("k_wall", plan.restraint["force_constant"])
        wall.addGroup(receptor)
        wall.addGroup(group)
        wall.addBond([0, 1], [])
        wall.setUsesPeriodicBoundaryConditions(system.usesPeriodicBoundaryConditions())
        system.addForce(wall)
        restraint_energy = restraint_free_energy(plan.restraint["radius_nm"],
                                                 plan.restraint["force_constant"],
                                                 temperature_K)

    factory = alchemy.AbsoluteAlchemicalFactory(alchemical_pme_treatment="exact")
    region = alchemy.AlchemicalRegion(alchemical_atoms=group, annihilate_electrostatics=True,
                                      annihilate_sterics=False)
    alchemical_system = factory.create_alchemical_system(system, region)
    pressure = None if pressure_bar is None else pressure_bar * unit.bar
    if not any("Barostat" in type(f).__name__ for f in system.getForces()):
        pressure = None
    base = states.ThermodynamicState(alchemical_system, temperature_K * unit.kelvin, pressure)
    thermodynamic = []
    for electrostatic, steric in zip(plan.electrostatics, plan.sterics):
        lambdas = alchemy.AlchemicalState.from_system(alchemical_system)
        lambdas.lambda_electrostatics, lambdas.lambda_sterics = electrostatic, steric
        thermodynamic.append(states.CompoundThermodynamicState(copy_state(base), [lambdas]))

    if plan.exchange_interval_steps > production_steps:
        raise StudyError(
            f"The production run is {production_steps} steps and states are exchanged every "
            f"{plan.exchange_interval_steps}.", code="config.option.out_of_range")
    iterations = production_steps // plan.exchange_interval_steps
    sampled = sample_states(
        thermodynamic=thermodynamic, system=alchemical_system, state=state, topology=topology,
        output_dir=output_dir, storage_name="alchemical.nc", temperature_K=temperature_K,
        timestep_fs=timestep_fs, friction_per_ps=friction_per_ps,
        interval=plan.exchange_interval_steps, equilibration=plan.equilibration_iterations,
        iterations=iterations, save_selection=save_selection, platform=platform,
        random_seed=random_seed, on_progress=on_progress)
    reporter = sampled["reporter"]
    analyzer = MultiStateSamplerAnalyzer(reporter)
    free_energy, error = analyzer.get_free_energy()
    kt = KB * temperature_K
    decouple = float(free_energy[0, -1]) * kt
    uncertainty = float(error[0, -1]) * kt
    overlap = neighbour_overlap(np.asarray(analyzer.mbar.compute_overlap()["matrix"]))
    thin = [k for k, value in enumerate(overlap) if value < LOW_OVERLAP]
    record: dict[str, Any] = {
        "group": plan.group, "atoms": len(group),
        "states": [{"lambda_electrostatics": e, "lambda_sterics": s}
                   for e, s in zip(plan.electrostatics, plan.sterics)],
        "iterations": iterations, "exchange_interval_steps": plan.exchange_interval_steps,
        "dG_decouple_kj_per_mol": decouple, "uncertainty_kj_per_mol": uncertainty,
        "dG_decouple_kcal_per_mol": decouple / 4.184,
        "meaning": ("G(decoupled) - G(coupled): positive where the group is held by its "
                    "surroundings. Minus this is its solvation free energy in a box of "
                    "water; a binding free energy is the solvent leg's less the complex "
                    "leg's, less the restraint's"),
        "neighbour_overlap": overlap,
        "estimated_by": "MBAR over every state, through openmmtools",
        "trajectory": ("the frames of whichever replica held the fully coupled state: the "
                       "ordinary ensemble, and not one continuous path"),
    }
    if restraint_energy is not None:
        record["restraint"] = {**plan.restraint, "dG_restraint_kj_per_mol": restraint_energy,
                               "meaning": "-kT ln(V/V0): the free energy of confining the "
                                          "decoupled ligand from 1 M to the restraint's volume"}
    if thin:
        record["thin_overlap"] = (
            "Neighbouring states " + ", ".join(f"{k}-{k + 1}" for k in thin)
            + f" overlap by less than {LOW_OVERLAP}: the estimate between them rests on "
            "almost no shared samples. Add a state between them, or run longer.")
    (output_dir / "alchemical.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (output_dir / "alchemical.log").write_text(
        f"Alchemical decoupling of {plan.group} through {len(plan.sterics)} states, "
        f"{iterations} exchanges.\ndG_decouple = {decouple:.2f} +- {uncertainty:.2f} kJ/mol\n"
        f"Neighbour overlap: {[round(v, 3) for v in overlap]}\n"
        + (f"{record['thin_overlap']}\n" if thin else ""), encoding="utf-8")
    used = sampled["cache"].global_context_cache.platform
    record["platform"] = used.getName() if used is not None else "chosen by OpenMM"
    reporter.close()
    return {"trajectory": sampled["trajectory"],
            "topology": output_dir / "trajectory_topology.pdb", "frames": sampled["frames"],
            "record": record,
            "duration_ns": iterations * plan.exchange_interval_steps * timestep_fs / 1e6}
