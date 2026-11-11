"""Replica exchange: several copies of the system at different conditions,
swapping now and then, so the one at the conditions of interest crosses
barriers the others cross more easily.

Two kinds, chosen by ``method``:

**REST2** (``rest2``, the default; Wang, Friesner and Berne, J. Phys. Chem. B
115, 9431 (2011)). Every replica is at the run's temperature, and in replica
``m`` the solute's interactions are scaled by ``s_m = T / T_m``: within the
solute by ``s_m``, between the solute and the rest by ``sqrt(s_m)``, the rest
untouched. The solute is heated, as it were, to ``T_m`` while its water is
not. Since only the solute's energy decides an exchange, a few replicas
serve however much water there is, which is why it is the method for a
protein in explicit solvent. Here the scaling is done with the force field's
own terms: the solute's charges by ``sqrt(s)`` and its Lennard-Jones depths
by ``s`` (so pairs within it are scaled by ``s`` and pairs with the rest by
``sqrt(s)``, by the combining rule), its exceptions likewise, and its
torsions by ``s``, those that cross its edge by ``sqrt(s)``.

**Temperature** (``temperature``; Sugita and Okamoto, Chem. Phys. Lett. 314,
141 (1999)). Replica ``m`` is at ``T_m``, the whole system heated. In
explicit solvent the replicas needed grow with the square root of the
number of atoms, and a box of water needs dozens.

The ladder runs geometrically from the run's temperature to
``max_temperature_K``. Every ``exchange_interval_steps`` the replicas at
neighbouring conditions try to swap, by the Metropolis criterion; the
sampler is openmmtools' (Chodera and Shirts, J. Chem. Phys. 135, 194110
(2011)), which tries every pair, not only neighbours, as Gibbs sampling.

The frames written as the run's trajectory are those of whichever replica
was at the conditions of interest at each exchange, so the trajectory
describes the right ensemble and is **not one continuous path**: a frame's
successor may be another replica's. An analysis of motion over time
(diffusion, a Markov state model, autocorrelations) does not apply to it,
and the run's record says so.

What makes a run worth reading is that the replicas mix. Recorded beside it
(``replica_exchange.json``): the share of attempted swaps accepted between
each pair of neighbours (below 0.1 the ladder has a gap, and the replicas
above it do not reach the bottom), and how many times each replica went from
the bottom of the ladder to the top and back (a round trip; none means full ladder
traversal has not been demonstrated).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.refusals import StudyError


def _config_error(message: str, code: str) -> Exception:
    """A settings error, as the config loader raises them."""
    from fastmdxplora.config.loader import ConfigError

    return ConfigError(message, code=code)

logger = logging.getLogger(__name__)

METHODS = ("rest2", "temperature")
#: Neighbours accepting fewer swaps than this have a gap between them.
LOW_ACCEPTANCE = 0.1
KEYS = {"method", "replicas", "max_temperature_K", "solute", "exchange_interval_steps",
        "equilibration_iterations"}


@dataclass
class Plan:
    method: str
    replicas: int
    max_temperature_K: float
    solute: str
    exchange_interval_steps: int
    equilibration_iterations: int


def plan_from_block(block: dict[str, Any] | None) -> Plan:
    """The run's replica exchange as asked, its settings checked."""
    if block is not None and not isinstance(block, dict):
        raise _config_error("`replica_exchange` must be a block of settings.",
                            "config.option.wrong_type")
    block = dict(block or {})
    unknown = sorted(set(block) - KEYS)
    if unknown:
        raise _config_error(
            f"`replica_exchange` has no setting {', '.join(unknown)}; it takes "
            f"{', '.join(sorted(KEYS))}.", "config.option.unknown")
    method = str(block.get("method", "rest2")).lower()
    if method not in METHODS:
        raise _config_error(
            f"`replica_exchange.method` is rest2 or temperature; it was {method!r}.",
            "config.option.out_of_range")
    def integer(name: str, default: int) -> int:
        value = block.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _config_error(f"`replica_exchange.{name}` must be an integer.",
                                "config.option.wrong_type")
        return value

    replicas = integer("replicas", 8 if method == "rest2" else 16)
    if not 2 <= replicas <= 128:
        raise _config_error(
            f"`replica_exchange.replicas` is from 2 to 128; it was {replicas}.",
            "config.option.out_of_range")
    raw_top = block.get("max_temperature_K", 450.0 if method == "rest2" else 400.0)
    if isinstance(raw_top, bool) or not isinstance(raw_top, (int, float)):
        raise _config_error("`replica_exchange.max_temperature_K` must be a number.",
                            "config.option.wrong_type")
    top = float(raw_top)
    if not math.isfinite(top) or top <= 0:
        raise _config_error("`replica_exchange.max_temperature_K` must be finite and positive.",
                            "config.option.out_of_range")
    interval = integer("exchange_interval_steps", 500)
    if interval < 10:
        raise _config_error(
            f"`replica_exchange.exchange_interval_steps` is at least 10; it was "
            f"{interval}: swapping more often than the replicas decorrelate only "
            "costs time.", "config.option.out_of_range")
    equilibration = integer("equilibration_iterations", 50)
    if equilibration < 0:
        raise _config_error("`replica_exchange.equilibration_iterations` is at least 0.",
                         "config.option.out_of_range")
    solute = block.get("solute", "protein")
    if not isinstance(solute, str) or not solute.strip():
        raise _config_error("`replica_exchange.solute` must be a nonempty selection string.",
                            "config.option.wrong_type")
    return Plan(method=method, replicas=replicas, max_temperature_K=top,
                solute=solute,
                exchange_interval_steps=interval, equilibration_iterations=equilibration)


def ladder(low: float, high: float, count: int) -> list[float]:
    """Temperatures from ``low`` to ``high``, geometrically spaced."""
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low < high):
        raise StudyError(
            f"`replica_exchange.max_temperature_K` ({high:g} K) has to be above the "
            f"run's temperature ({low:g} K).", code="config.option.out_of_range")
    return [low * (high / low) ** (k / (count - 1)) for k in range(count)]


# ---------------------------------------------------------------------------
# REST2's scaling, with the force field's own terms

#: The global parameters a REST2 replica sets: the solute's charges are
#: multiplied by 1 + rest_q = sqrt(s), its Lennard-Jones depths and its
#: exceptions within it by 1 + rest_eps = s, its torsions by rest_torsion = s
#: and those crossing its edge by rest_edge = sqrt(s).
REST_PARAMETERS = {"rest_q": 0.0, "rest_eps": 0.0, "rest_torsion": 1.0, "rest_edge": 1.0}


def rest_values(scale: float) -> dict[str, float]:
    root = math.sqrt(scale)
    return {"rest_q": root - 1.0, "rest_eps": scale - 1.0,
            "rest_torsion": scale, "rest_edge": root}


def with_rest2(system: Any, solute: set[int]) -> Any:
    """A copy of ``system`` whose solute's interactions are scaled by REST2's
    global parameters (at their unscaled values to begin with)."""
    import copy

    import openmm

    system = copy.deepcopy(system)
    nonbonded = [f for f in system.getForces() if isinstance(f, openmm.NonbondedForce)]
    if len(nonbonded) != 1:
        raise StudyError(
            "REST2 scales the solute's terms in the system's one NonbondedForce, and "
            f"this system has {len(nonbonded)}.", code="simulation.method.inapplicable")
    if any(isinstance(f, openmm.CustomNonbondedForce) for f in system.getForces()):
        raise StudyError(
            "The force field puts part of its nonbonded terms in a CustomNonbondedForce "
            "(a CHARMM NBFIX, for one), which REST2 here does not scale. Use "
            "`method: temperature`.", code="simulation.method.inapplicable")
    supported = (openmm.NonbondedForce, openmm.HarmonicBondForce,
                 openmm.HarmonicAngleForce, openmm.PeriodicTorsionForce,
                 openmm.CMMotionRemover, openmm.MonteCarloBarostat)
    unsupported = [type(f).__name__ for f in system.getForces()
                   if not isinstance(f, supported)]
    if unsupported:
        raise StudyError(
            f"REST2 does not scale these force terms: {', '.join(unsupported)}. "
            "Use `method: temperature`.", code="simulation.method.inapplicable")
    force = nonbonded[0]
    if force.getNumParticleParameterOffsets() or force.getNumExceptionParameterOffsets():
        raise StudyError("REST2 cannot compose with existing nonbonded parameter offsets.",
                         code="simulation.method.inapplicable")
    force.addGlobalParameter("rest_q", 0.0)
    force.addGlobalParameter("rest_eps", 0.0)
    for index in solute:
        charge, _, epsilon = force.getParticleParameters(index)
        if charge._value:
            force.addParticleParameterOffset("rest_q", index, charge, 0.0, 0.0)
        if epsilon._value:
            force.addParticleParameterOffset("rest_eps", index, 0.0, 0.0, epsilon)
    for k in range(force.getNumExceptions()):
        i, j, product, _, epsilon = force.getExceptionParameters(k)
        inside = (i in solute) + (j in solute)
        if inside and (product._value or epsilon._value):
            force.addExceptionParameterOffset("rest_eps" if inside == 2 else "rest_q", k,
                                              product, 0.0, epsilon)
    torsions = [f for f in system.getForces() if isinstance(f, openmm.PeriodicTorsionForce)]
    scaled = {}
    for name in ("rest_torsion", "rest_edge"):
        custom = openmm.CustomTorsionForce(f"{name}*k*(1+cos(n*theta-theta0))")
        custom.addGlobalParameter(name, 1.0)
        for parameter in ("n", "theta0", "k"):
            custom.addPerTorsionParameter(parameter)
        scaled[name] = custom
    for torsion in torsions:
        for k in range(torsion.getNumTorsions()):
            a, b, c, d, periodicity, phase, constant = torsion.getTorsionParameters(k)
            inside = sum(atom in solute for atom in (a, b, c, d))
            if not inside or not constant._value:
                continue
            name = "rest_torsion" if inside == 4 else "rest_edge"
            if torsion.usesPeriodicBoundaryConditions():
                scaled[name].setUsesPeriodicBoundaryConditions(True)
            scaled[name].addTorsion(a, b, c, d, [periodicity, phase, constant])
            torsion.setTorsionParameters(k, a, b, c, d, periodicity, phase, 0.0)
    # Both are added even where one holds no torsion, so every replica sets
    # the same parameters whatever the solute.
    for custom in scaled.values():
        system.addForce(custom)
    return system


def _rest_state_class():
    from openmmtools.states import GlobalParameterState

    class RESTState(GlobalParameterState):
        """The REST2 scaling of one replica, as openmmtools' states take it."""
        rest_q = GlobalParameterState.GlobalParameter("rest_q", standard_value=0.0)
        rest_eps = GlobalParameterState.GlobalParameter("rest_eps", standard_value=0.0)
        rest_torsion = GlobalParameterState.GlobalParameter("rest_torsion", standard_value=1.0)
        rest_edge = GlobalParameterState.GlobalParameter("rest_edge", standard_value=1.0)

    return RESTState


# ---------------------------------------------------------------------------
# Reading how the replicas mixed

def round_trips(states: np.ndarray) -> list[int]:
    """For each replica (columns of ``states``, iterations by replicas, the
    condition each was at), how many times it went from the bottom of the
    ladder to the top and back to the bottom."""
    top = int(states.max()) if states.size else 0
    found = []
    for path in states.T:
        trips, heading_up = 0, None
        for state in path:
            if state == 0:
                if heading_up is False:
                    trips += 1
                heading_up = True
            elif state == top and heading_up:
                heading_up = False
        found.append(trips)
    return found


def neighbour_acceptance(accepted: np.ndarray, proposed: np.ndarray) -> list[float | None]:
    """The share of attempted swaps accepted between each pair of
    neighbouring conditions, summed over the run."""
    found = []
    for k in range(len(accepted) - 1):
        tried = proposed[k, k + 1] + proposed[k + 1, k]
        took = accepted[k, k + 1] + accepted[k + 1, k]
        found.append(float(took / tried) if tried else None)
    return found


def mixing_record(plan: Plan, conditions: list[dict[str, float]], states: np.ndarray,
                  accepted: np.ndarray, proposed: np.ndarray) -> dict[str, Any]:
    acceptance = neighbour_acceptance(accepted, proposed)
    trips = round_trips(states)
    gaps = [k for k, value in enumerate(acceptance) if value is not None
            and value < LOW_ACCEPTANCE]
    record: dict[str, Any] = {
        "method": plan.method, "replicas": plan.replicas, "conditions": conditions,
        "iterations": int(len(states)),
        "exchange_interval_steps": plan.exchange_interval_steps,
        "neighbour_acceptance": acceptance,
        "round_trips": trips, "round_trips_total": int(sum(trips)),
        "trajectory": ("the frames of whichever replica was at the conditions of "
                       "interest at each exchange: the right ensemble, and not one "
                       "continuous path"),
    }
    if gaps:
        record["gaps"] = (
            "Neighbours " + ", ".join(f"{k}-{k + 1}" for k in gaps)
            + f" accepted fewer than {LOW_ACCEPTANCE:.0%} of swaps: the ladder has a gap "
            "there, and replicas above it rarely reach the conditions of interest. "
            "More replicas, or a lower top, close it.")
    if not sum(trips):
        record["no_round_trip"] = (
            "No replica went from the bottom of the ladder to the top and back: the "
            "this run has not demonstrated traversal of the entire ladder. "
            "A longer run or a ladder with more acceptance may be needed.")
    return record


# ---------------------------------------------------------------------------
# Running

def run_replica_exchange(*, system_xml: Path, state_xml: Path, topology_pdb: Path,
                         output_dir: Path, block: dict[str, Any], temperature_K: float,
                         timestep_fs: float, friction_per_ps: float, production_steps: int,
                         pressure_bar: float | None, save_selection: str | None,
                         platform: str = "auto", random_seed: int | None = None,
                         precision: str = "mixed", device_index: str | int | None = None,
                         barostat_frequency: int = 25, minimize: bool = True,
                         on_progress: Any = None) -> dict[str, Any]:
    """Run copies from an equilibrated state; save the target-state ensemble.

    Each frame is sampled after one exchange interval. Storage contains all
    replicas and their state assignments; the DCD contains state zero only.
    """
    try:
        import mdtraj as md
        import openmm
        from openmm import unit
        from openmmtools import cache, mcmc, states
        from openmmtools.multistate import MultiStateReporter, ReplicaExchangeSampler
    except ImportError as exc:
        from fastmdxplora.dependencies import MissingBackendError, MissingDependency

        raise MissingBackendError([
            MissingDependency("openmmtools", "openmmtools", "openmmtools"),
            MissingDependency("pymbar", "pymbar", "pymbar")]) from exc

    from fastmdxplora.simulation.runner import (
        _import_openmm, is_membrane_system, resolve_save_selection, select_platform,
    )

    plan = plan_from_block(block)
    temperatures = ladder(temperature_K, plan.max_temperature_K, plan.replicas)
    if (not isinstance(production_steps, int) or isinstance(production_steps, bool)
            or production_steps < plan.exchange_interval_steps
            or production_steps % plan.exchange_interval_steps):
        raise StudyError(
            "Replica exchange needs a positive production length divisible by "
            f"exchange_interval_steps ({plan.exchange_interval_steps}); it was "
            f"{production_steps} steps.", code="config.option.out_of_range")
    iterations = production_steps // plan.exchange_interval_steps
    for name, value in (("timestep_fs", timestep_fs), ("friction_per_ps", friction_per_ps)):
        if not math.isfinite(value) or value <= 0:
            raise StudyError(f"{name} must be finite and positive for replica exchange.",
                             code="config.option.out_of_range")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    storage = output_dir / "replica_exchange.nc"
    if storage.exists():
        raise StudyError(
            f"Replica-exchange storage already exists at {storage}. Use a new output folder.",
            code="config.option.conflicting")
    system = openmm.XmlSerializer.deserialize(Path(system_xml).read_text(encoding="utf-8"))
    state = openmm.XmlSerializer.deserialize(Path(state_xml).read_text(encoding="utf-8"))
    topology = md.load_topology(str(topology_pdb))
    if topology.n_atoms != system.getNumParticles():
        raise StudyError("Replica-exchange topology and system have different atom counts.",
                         code="simulation.method.inapplicable")
    omm_topology = topology.to_openmm()
    pressure = None if pressure_bar is None else pressure_bar * unit.bar
    if pressure_bar is not None and (not math.isfinite(pressure_bar) or pressure_bar <= 0):
        raise StudyError("Replica-exchange pressure must be finite and positive.",
                         code="config.option.out_of_range")
    if pressure is not None and (not system.usesPeriodicBoundaryConditions()
                                 or is_membrane_system(omm_topology)):
        raise StudyError(
            "NPT replica exchange currently supports periodic, non-membrane systems. "
            "Membrane pressure coupling has not been implemented for this sampler.",
            code="simulation.method.inapplicable")
    # The ensemble is requested explicitly, rather than inferred from whether
    # the setup XML happens to have a barostat (it normally does not).
    for k in reversed(range(system.getNumForces())):
        if "Barostat" in type(system.getForce(k)).__name__:
            system.removeForce(k)
    if pressure is not None:
        barostat = openmm.MonteCarloBarostat(pressure, temperature_K * unit.kelvin,
                                            barostat_frequency)
        if random_seed is not None:
            barostat.setRandomNumberSeed(int(random_seed) % (2 ** 31))
        system.addForce(barostat)
    system.setDefaultPeriodicBoxVectors(*state.getPeriodicBoxVectors())

    if plan.method == "rest2":
        try:
            solute = {int(i) for i in topology.select(plan.solute)}
        except Exception as exc:
            raise StudyError(f"`replica_exchange.solute` {plan.solute!r} is not a "
                             f"selection MDTraj reads: {exc}",
                             code="simulation.cv.selection_empty") from exc
        if not solute:
            raise StudyError(f"`replica_exchange.solute` {plan.solute!r} matched no atoms.",
                             code="simulation.cv.selection_empty")
        if any((a.index in solute) != (b.index in solute) for a, b in topology.bonds):
            raise StudyError(
                "REST2's solute must contain whole molecules: its selection cuts a bond.",
                code="simulation.method.inapplicable")
        scaled = with_rest2(system, solute)
        RESTState = _rest_state_class()
        base = states.ThermodynamicState(scaled, temperature_K * unit.kelvin, pressure)
        thermodynamic, conditions = [], []
        for hot in temperatures:
            rest = RESTState.from_system(scaled)
            for name, value in rest_values(temperature_K / hot).items():
                setattr(rest, name, value)
            thermodynamic.append(states.CompoundThermodynamicState(
                base, composable_states=[rest]))
            conditions.append({"effective_temperature_K": hot, "scale": temperature_K / hot})
    else:
        thermodynamic = [states.ThermodynamicState(system, t * unit.kelvin, pressure)
                         for t in temperatures]
        conditions = [{"temperature_K": t} for t in temperatures]

    selected_platform, properties, platform_name = select_platform(
        _import_openmm(), requested=platform, precision=precision, device_index=device_index)
    contexts = cache.ContextCache(platform=selected_platform, platform_properties=properties)
    kept, described = resolve_save_selection(omm_topology, save_selection)
    atoms = np.arange(topology.n_atoms) if kept is None else np.asarray(kept)
    # Give every replica finite, constrained velocities at its own temperature.
    # An absent velocity array can leave this backend's initial step undefined.
    drawn = openmm.Context(system, openmm.VerletIntegrator(0.001), selected_platform, properties)
    sampler_states = []
    try:
        drawn.setPeriodicBoxVectors(*state.getPeriodicBoxVectors())
        drawn.setPositions(state.getPositions())
        drawn.applyConstraints(1e-8)
        for i, temp in enumerate(temperatures):
            kinetic_temperature = temperature_K if plan.method == "rest2" else temp
            seed = 0 if random_seed is None else (int(random_seed) + i) % (2 ** 31)
            drawn.setVelocitiesToTemperature(kinetic_temperature * unit.kelvin, seed)
            drawn.applyVelocityConstraints(1e-8)
            initialized = drawn.getState(getPositions=True, getVelocities=True)
            sampler_states.append(states.SamplerState(
                positions=initialized.getPositions(asNumpy=True),
                velocities=initialized.getVelocities(asNumpy=True),
                box_vectors=initialized.getPeriodicBoxVectors(asNumpy=True)))
    finally:
        del drawn
    move = mcmc.LangevinDynamicsMove(
        timestep=timestep_fs * unit.femtoseconds,
        collision_rate=friction_per_ps / unit.picoseconds,
        n_steps=plan.exchange_interval_steps, reassign_velocities=True)
    reporter = MultiStateReporter(str(storage), checkpoint_interval=max(1, iterations // 20),
                                  analysis_particle_indices=[int(a) for a in atoms])
    rng_state = np.random.get_state()
    try:
        if random_seed is not None:
            np.random.seed(int(random_seed) % (2 ** 32))
        sampler = ReplicaExchangeSampler(mcmc_moves=move, number_of_iterations=iterations,
                                         replica_mixing_scheme="swap-all",
                                         online_analysis_interval=None)
        sampler.energy_context_cache = contexts
        sampler.sampler_context_cache = contexts
        sampler.create(thermodynamic_states=thermodynamic, sampler_states=sampler_states,
                       storage=reporter)
        if minimize:
            sampler.minimize(tolerance=10.0 * unit.kilojoule_per_mole / unit.nanometer,
                             max_iterations=1000)
        if plan.equilibration_iterations:
            sampler.equilibrate(plan.equilibration_iterations)
        done = 0
        while done < iterations:
            step = min(max(1, iterations // 50), iterations - done)
            sampler.run(step)
            done += step
            if on_progress is not None:
                on_progress(done, iterations)

        held = np.asarray(reporter.read_replica_thermodynamic_states())
        xyz, boxes = [], []
        first_iteration = held.shape[0] - iterations
        for iteration in range(first_iteration, held.shape[0]):
            replica = int(np.flatnonzero(held[iteration] == 0)[0])
            saved_states = reporter.read_sampler_states(iteration=iteration,
                                                        analysis_particles_only=True)
            xyz.append(np.asarray(saved_states[replica].positions.value_in_unit(unit.nanometer)))
            boxes.append(np.asarray(saved_states[replica].box_vectors.value_in_unit(unit.nanometer)))
        replica = int(np.flatnonzero(held[-1] == 0)[0])
        final_context = openmm.Context(system, openmm.VerletIntegrator(0.001),
                                       selected_platform, properties)
        try:
            sampler.sampler_states[replica].apply_to_context(final_context)
            final_context.setTime(state.getTime() + (production_steps
                + plan.equilibration_iterations * plan.exchange_interval_steps)
                * timestep_fs * unit.femtoseconds)
            final_state = output_dir / "state_final.xml"
            final_state.write_text(openmm.XmlSerializer.serialize(final_context.getState(
                getPositions=True, getVelocities=True, getParameters=True, getEnergy=True)),
                encoding="utf-8")
        finally:
            del final_context
        times = np.arange(1, len(xyz) + 1) * plan.exchange_interval_steps * timestep_fs / 1000.0
        frames = md.Trajectory(np.array(xyz, dtype=np.float32), topology.subset(atoms), time=times)
        if system.usesPeriodicBoundaryConditions():
            frames.unitcell_vectors = np.array(boxes, dtype=np.float32)
        trajectory = output_dir / "production.dcd"
        frames.save_dcd(str(trajectory))
        frames[0].save_pdb(str(output_dir / "trajectory_topology.pdb"))

        # Reduced potentials contain pV in NPT. Subtract it before labelling
        # the column potential energy. 1 bar nm^3 = 0.0602214076 kJ/mol.
        energies = np.asarray(reporter.read_energies()[0])
        kt = (unit.MOLAR_GAS_CONSTANT_R * temperature_K * unit.kelvin).value_in_unit(
            unit.kilojoule_per_mole)
        with open(output_dir / "energy.csv", "w", encoding="utf-8") as handle:
            handle.write("Time (ps),Potential Energy (kJ/mol),Box Volume (nm^3)\n")
            for k, iteration in enumerate(range(first_iteration, held.shape[0])):
                replica = int(np.flatnonzero(held[iteration] == 0)[0])
                pv = 0.0 if pressure_bar is None else pressure_bar * np.linalg.det(boxes[k]) * 0.0602214076
                handle.write(f"{times[k]:.6f},{energies[iteration, replica, 0] * kt - pv:.8f},"
                             f"{np.linalg.det(boxes[k]):.8f}\n")
        accepted, proposed = reporter.read_mixing_statistics()
        record = mixing_record(plan, conditions, held[first_iteration:],
                               np.asarray(accepted)[first_iteration:].sum(axis=0),
                               np.asarray(proposed)[first_iteration:].sum(axis=0))
        record.update(saved=described, frames=len(xyz), platform=platform_name,
                      platform_properties=properties, production_steps=production_steps,
                      temperature_K=temperature_K, pressure_bar=pressure_bar,
                      ensemble="nvt" if pressure_bar is None else "npt",
                      timestep_fs=timestep_fs, integrator="langevin_middle",
                      random_seed=random_seed, equilibration_iterations=plan.equilibration_iterations,
                      minimization=({"tolerance_kjmol_per_nm": 10.0, "max_iterations": 1000}
                                    if minimize else None),
                      solute=plan.solute if plan.method == "rest2" else None, storage=storage.name)
        (output_dir / "replica_exchange.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        (output_dir / "replica_exchange.log").write_text(
            f"Replica exchange ({plan.method}), {plan.replicas} replicas, {iterations} "
            f"exchanges every {plan.exchange_interval_steps} steps.\n"
            f"Neighbour acceptance: {record['neighbour_acceptance']}\n"
            f"Round trips: {record['round_trips']}\n"
            + "".join(f"{record[k]}\n" for k in ("gaps", "no_round_trip") if k in record),
            encoding="utf-8")
        return {"trajectory": trajectory, "topology": output_dir / "trajectory_topology.pdb",
                "final_state": final_state,
                "frames": len(xyz), "record": record,
                "duration_ns": production_steps * timestep_fs / 1e6}
    finally:
        reporter.close()
        contexts.empty()
        np.random.set_state(rng_state)
