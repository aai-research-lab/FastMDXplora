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
the bottom of the ladder to the top and back (a round trip; none, and the
enhanced sampling did not happen).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.config.loader import ConfigError
from fastmdxplora.refusals import StudyError

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
    block = dict(block or {})
    unknown = sorted(set(block) - KEYS)
    if unknown:
        raise ConfigError(
            f"`replica_exchange` has no setting {', '.join(unknown)}; it takes "
            f"{', '.join(sorted(KEYS))}.", code="config.option.unknown")
    method = str(block.get("method", "rest2")).lower()
    if method not in METHODS:
        raise ConfigError(
            f"`replica_exchange.method` is rest2 or temperature; it was {method!r}.",
            code="config.option.out_of_range")
    replicas = int(block.get("replicas", 8 if method == "rest2" else 16))
    if not 2 <= replicas <= 128:
        raise ConfigError(
            f"`replica_exchange.replicas` is from 2 to 128; it was {replicas}.",
            code="config.option.out_of_range")
    top = float(block.get("max_temperature_K", 450.0 if method == "rest2" else 400.0))
    interval = int(block.get("exchange_interval_steps", 500))
    if interval < 10:
        raise ConfigError(
            f"`replica_exchange.exchange_interval_steps` is at least 10; it was "
            f"{interval}: swapping more often than the replicas decorrelate only "
            "costs time.", code="config.option.out_of_range")
    equilibration = int(block.get("equilibration_iterations", 50))
    if equilibration < 0:
        raise ConfigError("`replica_exchange.equilibration_iterations` is at least 0.",
                          code="config.option.out_of_range")
    return Plan(method=method, replicas=replicas, max_temperature_K=top,
                solute=str(block.get("solute") or "protein"),
                exchange_interval_steps=interval, equilibration_iterations=equilibration)


def ladder(low: float, high: float, count: int) -> list[float]:
    """Temperatures from ``low`` to ``high``, geometrically spaced."""
    if high <= low:
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
    force = nonbonded[0]
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
            "replicas did not mix, and the run sampled no better than one copy would. "
            "A longer run, or a ladder with more acceptance, is needed.")
    return record


# ---------------------------------------------------------------------------
# Running

def run_replica_exchange(*, system_xml: Path, state_xml: Path, topology_pdb: Path,
                         output_dir: Path, block: dict[str, Any], temperature_K: float,
                         timestep_fs: float, friction_per_ps: float, production_steps: int,
                         pressure_bar: float | None, save_selection: str | None,
                         platform: str = "auto", random_seed: int | None = None,
                         on_progress: Any = None) -> dict[str, Any]:
    """Run replica exchange from a prepared system, and write the frames at
    the conditions of interest as the run's trajectory.

    Returns the paths written and the mixing record.
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

    from fastmdxplora.simulation.runner import resolve_save_selection

    plan = plan_from_block(block)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    system = openmm.XmlSerializer.deserialize(Path(system_xml).read_text(encoding="utf-8"))
    state = openmm.XmlSerializer.deserialize(Path(state_xml).read_text(encoding="utf-8"))
    topology = md.load_topology(str(topology_pdb))
    if plan.exchange_interval_steps > production_steps:
        raise StudyError(
            f"The production run is {production_steps} steps and swaps are tried every "
            f"{plan.exchange_interval_steps}: no swap would be tried.",
            code="config.option.out_of_range")
    iterations = production_steps // plan.exchange_interval_steps

    temperatures = ladder(temperature_K, plan.max_temperature_K, plan.replicas)
    pressure = None if pressure_bar is None else pressure_bar * unit.bar
    has_barostat = any("Barostat" in type(f).__name__ for f in system.getForces())
    if not has_barostat:
        pressure = None
    if plan.method == "rest2":
        try:
            solute = {int(i) for i in topology.select(plan.solute)}
        except Exception as exc:  # noqa: BLE001 - MDTraj's parser raises several
            raise StudyError(f"`replica_exchange.solute` {plan.solute!r} is not a "
                             f"selection MDTraj reads: {exc}",
                             code="simulation.cv.selection_empty") from exc
        if not solute:
            raise StudyError(f"`replica_exchange.solute` {plan.solute!r} matched no atoms.",
                             code="simulation.cv.selection_empty")
        scaled = with_rest2(system, solute)
        RESTState = _rest_state_class()
        base = states.ThermodynamicState(scaled, temperature_K * unit.kelvin, pressure)
        thermodynamic = []
        conditions = []
        for hot in temperatures:
            rest = RESTState.from_system(scaled)
            for name, value in rest_values(temperature_K / hot).items():
                setattr(rest, name, value)
            thermodynamic.append(states.CompoundThermodynamicState(
                copy_state(base), composable_states=[rest]))
            conditions.append({"effective_temperature_K": hot, "scale": temperature_K / hot})
    else:
        thermodynamic = [states.ThermodynamicState(system, t * unit.kelvin, pressure)
                         for t in temperatures]
        conditions = [{"temperature_K": t} for t in temperatures]

    # The contexts of an earlier run in this process are let go first:
    # openmmtools' cache will not change platform while it holds any.
    cache.global_context_cache.empty()
    if platform and platform != "auto":
        cache.global_context_cache.platform = openmm.Platform.getPlatformByName(platform)
    # Velocities drawn at the run's temperature, the constraints applied:
    # given none, openmmtools leaves them undefined and the first step
    # of every replica is NaN.
    drawn = openmm.Context(system, openmm.VerletIntegrator(0.001),
                           openmm.Platform.getPlatformByName("Reference"))
    drawn.setPeriodicBoxVectors(*state.getPeriodicBoxVectors())
    drawn.setPositions(state.getPositions())
    drawn.setVelocitiesToTemperature(temperature_K * unit.kelvin,
                                     int(random_seed or 0) % (2 ** 31))
    drawn.applyVelocityConstraints(1e-8)
    sampler_state = states.SamplerState(
        positions=state.getPositions(asNumpy=True),
        velocities=drawn.getState(getVelocities=True).getVelocities(asNumpy=True),
        box_vectors=state.getPeriodicBoxVectors(asNumpy=True))
    del drawn
    move = mcmc.LangevinDynamicsMove(
        timestep=timestep_fs * unit.femtoseconds,
        collision_rate=friction_per_ps / unit.picoseconds,
        n_steps=plan.exchange_interval_steps, reassign_velocities=False)
    storage = output_dir / "replica_exchange.nc"
    if storage.exists():
        storage.unlink()
    # The atoms the study saves are stored every iteration; the whole system
    # only at checkpoints.
    kept, described = resolve_save_selection(md.Topology.to_openmm(topology), save_selection)
    atoms = np.arange(topology.n_atoms) if kept is None else np.asarray(kept)
    reporter = MultiStateReporter(str(storage), checkpoint_interval=max(1, iterations // 20),
                                  analysis_particle_indices=[int(a) for a in atoms])
    sampler = ReplicaExchangeSampler(mcmc_moves=move, number_of_iterations=iterations,
                                     replica_mixing_scheme="swap-all")
    if random_seed is not None:
        np.random.seed(int(random_seed) % (2 ** 32))
    sampler.create(thermodynamic_states=thermodynamic, sampler_states=sampler_state,
                   storage=reporter)
    sampler.minimize()
    if plan.equilibration_iterations:
        sampler.equilibrate(plan.equilibration_iterations)
    done = 0
    while done < iterations:
        step = min(max(1, iterations // 50), iterations - done)
        sampler.extend(step) if done else sampler.run(step)
        done += step
        if on_progress is not None:
            on_progress(done, iterations)

    # The frames at the conditions of interest, written as the trajectory.
    held = np.asarray(reporter.read_replica_thermodynamic_states())
    xyz, boxes = [], []
    first_iteration = held.shape[0] - iterations
    for iteration in range(first_iteration, held.shape[0]):
        replica = int(np.flatnonzero(held[iteration] == 0)[0])
        sampler_states = reporter.read_sampler_states(iteration=iteration,
                                                      analysis_particles_only=True)
        positions = sampler_states[replica].positions.value_in_unit(unit.nanometer)
        xyz.append(np.asarray(positions))
        vectors = sampler_states[replica].box_vectors
        boxes.append(np.asarray(vectors.value_in_unit(unit.nanometer)))
    saved = topology.subset(atoms)
    frames = md.Trajectory(np.array(xyz, dtype=np.float32), saved,
                           time=np.arange(len(xyz)) * plan.exchange_interval_steps
                           * timestep_fs / 1000.0)
    frames.unitcell_vectors = np.array(boxes, dtype=np.float32)
    trajectory = output_dir / "production.dcd"
    frames.save_dcd(str(trajectory))
    frames[0].save_pdb(str(output_dir / "trajectory_topology.pdb"))

    # The potential energy of the replica at the conditions of interest, at
    # each exchange, as the run's energy record.
    energies = np.asarray(reporter.read_energies()[0])
    kt = (0.0083144626 * temperature_K)
    with open(output_dir / "energies.csv", "w", encoding="utf-8") as handle:
        handle.write("time_ps,potential_kj_per_mol\n")
        for k, iteration in enumerate(range(first_iteration, held.shape[0])):
            replica = int(np.flatnonzero(held[iteration] == 0)[0])
            handle.write(f"{k * plan.exchange_interval_steps * timestep_fs / 1000.0:.3f},"
                         f"{energies[iteration, replica, 0] * kt:.4f}\n")
    accepted, proposed = reporter.read_mixing_statistics()
    record = mixing_record(plan, conditions, held[first_iteration:],
                           np.asarray(accepted).sum(axis=0) if np.ndim(accepted) == 3
                           else np.asarray(accepted),
                           np.asarray(proposed).sum(axis=0) if np.ndim(proposed) == 3
                           else np.asarray(proposed))
    record.update(saved=described, frames=len(xyz),
                  solute=plan.solute if plan.method == "rest2" else None,
                  storage=storage.name)
    (output_dir / "replica_exchange.json").write_text(json.dumps(record, indent=2),
                                                     encoding="utf-8")
    used = cache.global_context_cache.platform
    record["platform"] = used.getName() if used is not None else "chosen by OpenMM"
    (output_dir / "replica_exchange.log").write_text(
        f"Replica exchange ({plan.method}), {plan.replicas} replicas, {iterations} "
        f"exchanges every {plan.exchange_interval_steps} steps.\n"
        f"Neighbour acceptance: {record['neighbour_acceptance']}\n"
        f"Round trips: {record['round_trips']}\n"
        + "".join(f"{record[k]}\n" for k in ("gaps", "no_round_trip") if k in record),
        encoding="utf-8")
    reporter.close()
    return {"trajectory": trajectory, "topology": output_dir / "trajectory_topology.pdb",
            "frames": len(xyz), "record": record,
            "duration_ns": iterations * plan.exchange_interval_steps * timestep_fs / 1e6}


def copy_state(state: Any) -> Any:
    import copy

    return copy.deepcopy(state)
