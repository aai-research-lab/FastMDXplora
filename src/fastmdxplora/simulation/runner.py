"""OpenMM simulation runner.

Takes the serialized ``System`` + ``State`` produced by the setup phase
and runs a standard four-stage MD pipeline:

  1. Minimization  -- local energy minimizer to a force-tolerance
  2. NVT equilibration  -- fixed volume, Langevin thermostat
  3. NPT equilibration  -- Monte Carlo barostat added, box equilibrates
  4. Production  -- the trajectory frames the analysis phase consumes

The runner is intentionally separate from the orchestrator-facing
:mod:`fastmdxplora.simulation.pipeline` so it can be exercised
directly from Python for tests and ad-hoc scripts.

OpenMM is a conda-forge package and is imported lazily — without it the
runner raises a helpful ImportError on first use, but importing this
module does not.
"""

from __future__ import annotations

import time as _time

import csv
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.utils.logging import get_logger

logger = get_logger("simulation.runner")


# ---------------------------------------------------------------------------
# Defaults — production-cadence reporters and standard
# stage step counts.
# ---------------------------------------------------------------------------
DEFAULT_TIMESTEP_FS = 2.0
DEFAULT_TEMPERATURE_K = 300.0
DEFAULT_FRICTION_PER_PS = 1.0          # Langevin collision frequency
DEFAULT_PRESSURE_BAR = 1.0
DEFAULT_BAROSTAT_FREQUENCY = 25        # MC barostat step interval
DEFAULT_INTEGRATOR = "langevin_middle"
DEFAULT_INTEGRATOR_ERROR_TOLERANCE = 0.001  # for the variable-step integrators

# Atmospheres to bar (OpenMM's barostat takes bar). 1 atm = 1.01325 bar.
ATM_TO_BAR = 1.01325

# Integrators we can construct. langevin_middle is the modern default
# (better configurational sampling than the legacy LangevinIntegrator).
SUPPORTED_INTEGRATORS = (
    "langevin",
    "langevin_middle",
    "brownian",
    "verlet",
    "variable_langevin",
    "variable_verlet",
)

# Standard stage step counts for general-purpose MD
DEFAULT_NVT_STEPS = 250_000            # 500 ps @ 2 fs
DEFAULT_NPT_STEPS = 500_000            # 1 ns  @ 2 fs
DEFAULT_PRODUCTION_STEPS = 1_000_000   # 2 ns  @ 2 fs
DEFAULT_MINIMIZE_TOLERANCE_KJMOL_PER_NM = 10.0
DEFAULT_MINIMIZE_MAX_ITERATIONS = 0     # 0 == until convergence

# Reporter cadence
DEFAULT_TRAJECTORY_INTERVAL_STEPS = 1000
DEFAULT_STATE_INTERVAL_STEPS = 1000
DEFAULT_CHECKPOINT_INTERVAL_STEPS = 10_000


# ---------------------------------------------------------------------------
# Result struct
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimulationResult:
    """What the runner returns. All paths are absolute."""

    trajectory: Path
    topology: Path
    final_state: Path
    energy_csv: Path
    log_file: Path
    platform_used: str
    n_production_frames: int
    duration_ns_actual: float
    #: The pressure the constant-pressure stages actually ran at. Settable as
    #: bar or as atmospheres, and unset means one bar -- so the number the
    #: barostat used was known only inside the runner, and a methods section
    #: had to report the pressure of an NPT run as unrecorded.
    pressure_bar_used: float | None = None
    minimized_state: Path | None = None


# ---------------------------------------------------------------------------
# Lazy OpenMM import
# ---------------------------------------------------------------------------
def _import_openmm() -> dict:
    """Return a dict of the OpenMM symbols the runner needs.

    Raises a clean ImportError when OpenMM isn't installed. Bundling the
    imports into one function means the runner module itself imports
    cheaply, and tests can substitute the dict via monkeypatching.
    """
    try:
        import openmm
        from openmm import unit
        from openmm.app import (
            CheckpointReporter,
            DCDReporter,
            PDBFile,
            Simulation,
            StateDataReporter,
        )
    except ImportError as exc:
        raise ImportError(
            "Simulation phase requires OpenMM. Install via conda "
            "(recommended): conda install -c conda-forge openmm — or via "
            "pip with the optional [md] extras: "
            "pip install fastmdxplora[md]."
        ) from exc

    return {
        "openmm": openmm,
        "unit": unit,
        "CheckpointReporter": CheckpointReporter,
        "DCDReporter": DCDReporter,
        "PDBFile": PDBFile,
        "Simulation": Simulation,
        "StateDataReporter": StateDataReporter,
    }


# ---------------------------------------------------------------------------
# Platform selection
# ---------------------------------------------------------------------------
def _probe_platform(omm: Any, platform: Any, name: str,
                props: dict[str, str]) -> bool:
    """A registered platform may still be unusable. Probe it by actually
    computing a force; only meaningful for the GPU platforms.
    CPU/Reference are always usable.

    The probe used to build a Context around a System holding one
    particle and no forces. That loads almost no kernels, so a platform
    whose kernels will not compile passed the probe and failed on the
    real system a second later. Seen on a workstation whose driver
    rejected the installed build's PTX: `auto` chose CUDA, the probe
    said yes, and `Simulation(...)` raised
    `CUDA_ERROR_UNSUPPORTED_PTX_VERSION` -- with a working OpenCL
    platform sitting next in the candidate list, unused.

    `openmm.testInstallation` catches that case because it computes
    forces. So does this now: two particles and a NonbondedForce, which
    is the kernel every real system needs.
    """
    if name not in ("CUDA", "OpenCL", "HIP"):
        return True
    try:
        mm = omm["openmm"]
        unit = omm["unit"]
        sys_ = mm.System()
        sys_.addParticle(1.0)
        sys_.addParticle(1.0)
        force = mm.NonbondedForce()
        force.addParticle(0.0, 1.0, 0.0)
        force.addParticle(0.0, 1.0, 0.0)
        sys_.addForce(force)
        integ = mm.VerletIntegrator(0.001 * unit.picoseconds)
        ctx = mm.Context(sys_, integ, platform, props)
        ctx.setPositions([[0.0, 0.0, 0.0], [0.0, 0.0, 0.2]] * unit.nanometers)
        # The kernels compile here, not at Context construction.
        ctx.getState(getForces=True).getForces(asNumpy=True)
        del ctx, integ, sys_
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Platform %s is registered but not usable (%s); "
            "trying next candidate.", name, exc,
        )
        return False


def _say_what_is_available(omm: dict, wanted: "list[str]") -> None:
    """Name the platforms OpenMM found, and why a wanted one is missing.

    A platform that is not registered is skipped silently, and the silence is
    expensive. On a cluster with two GPUs, OpenMM offered `Reference` alone
    and reported no load failures -- because with `OPENMM_PLUGIN_DIR` unset it
    had not looked, so there was nothing to fail. A run would have finished,
    correct and about a hundred times slower, with nothing anywhere saying
    why.

    `getPluginLoadFailures` holds the answer whenever the directory is right
    and a library still would not load: on that machine, once the path was
    set, it said `libcuda.so.1: cannot open shared object file`, which is the
    whole diagnosis in one line.
    """
    mm = omm.get("openmm")
    if mm is None:
        return
    try:
        found = [mm.Platform.getPlatform(i).getName()
                 for i in range(mm.Platform.getNumPlatforms())]
    except Exception:  # noqa: BLE001 - a toolkit that will not enumerate
        return

    logger.info("OpenMM platforms available: %s", ", ".join(found) or "none")

    missing = [name for name in wanted if name not in found]
    if not missing:
        return

    try:
        failures = [str(f) for f in mm.Platform.getPluginLoadFailures()]
    except Exception:  # noqa: BLE001
        failures = []

    if failures:
        for failure in failures:
            logger.info("  a plugin did not load: %s", failure)
    elif "CPU" in missing:
        # Nothing failed and the CPU platform is absent, which means nothing
        # was attempted: the plugin directory is wrong or unset. Worth saying,
        # because the fallback that follows is the slow one.
        logger.info(
            "  no %s, and no plugin reported a failure, which means "
            "none was tried. OpenMM loads its platforms from a directory "
            "fixed at build time; where that path does not exist -- a "
            "relocated or shared installation -- set OPENMM_PLUGIN_DIR to "
            "the `lib/plugins` beside the OpenMM library.",
            ", ".join(missing))


def select_platform(
    omm: dict,
    requested: str = "auto",
    precision: str = "mixed",
    device_index: str | int | None = None,
) -> tuple[Any, dict[str, str], str]:
    """Pick the best available OpenMM Platform.

    Parameters
    ----------
    omm : dict
        Output of :func:`_import_openmm`.
    requested : str, default "auto"
        One of ``"auto"``, ``"CUDA"``, ``"OpenCL"``, ``"CPU"``, ``"HIP"``.
        ``"auto"`` tries CUDA → OpenCL → CPU and uses the first that loads.
    precision : str, default "mixed"
        Numerical precision for GPU platforms. ``"single"``, ``"mixed"``,
        or ``"double"``. Ignored for CPU.
    device_index : str | int | None
        GPU device index for multi-GPU machines (e.g. ``"0"`` or ``"0,1"``).
        Maps to ``CudaDeviceIndex`` / ``OpenCLDeviceIndex``. Ignored for CPU.

    Returns
    -------
    platform : openmm.Platform
    properties : dict[str, str]
        Per-platform properties to pass to ``Simulation(...)``.
    name : str
        The platform's name as a string for logging.
    """
    Platform = omm["openmm"].Platform

    auto = requested == "auto"
    if auto:
        candidates = ["CUDA", "OpenCL", "CPU"]
    else:
        candidates = [requested]

    def _make_props(name: str) -> dict[str, str]:
        props: dict[str, str] = {}
        if name in ("CUDA", "OpenCL", "HIP"):
            props["Precision"] = precision
            if device_index is not None:
                prop_key = {
                    "CUDA": "CudaDeviceIndex",
                    "OpenCL": "OpenCLDeviceIndex",
                    "HIP": "HipDeviceIndex",
                }[name]
                props[prop_key] = str(device_index)
        return props

    _say_what_is_available(omm, candidates)

    for name in candidates:
        try:
            platform = Platform.getPlatformByName(name)
        except Exception:  # noqa: BLE001 -- OpenMM raises different types
            continue
        props = _make_props(name)
        # For auto-selection, verify the platform actually works before
        # committing to it — a registered OpenCL/CUDA platform with no
        # usable device otherwise fails later at Context construction with
        # a confusing error. For an explicit request we honor it as-is so
        # the user sees the real error if their chosen platform is broken.
        if auto and not _probe_platform(omm, platform, name, props):
            continue
        # What was applied, not what was asked for. `Precision` is a property
        # of the GPU platforms; the CPU platform has only `Threads` and
        # `DeterministicForces`, so a run there is single precision whatever
        # was requested. Reporting the request made the banner say
        # "CPU (precision=mixed)", and the diagnosis printed after a
        # non-finite coordinate suggests `--simulate-precision double` --
        # which on CPU changes nothing at all, so somebody chasing an
        # unstable run would try it and learn nothing.
        applied = props.get("Precision")
        logger.info(
            "Selected OpenMM platform: %s (%s%s)",
            name,
            f"precision={applied}" if applied
            else f"{precision} precision requested; this platform has no such "
                 "setting",
            f", device={device_index}" if device_index is not None else "",
        )
        return platform, props, name

    raise RuntimeError(
        f"No usable OpenMM platform among {candidates}. "
        f"Install GPU drivers + a matching OpenMM build, or pass platform='CPU'."
    )


# ---------------------------------------------------------------------------
# Integrator + barostat helpers
# ---------------------------------------------------------------------------
def _make_integrator(
    omm: dict,
    *,
    name: str,
    temperature_K: float,
    friction_per_ps: float,
    timestep_fs: float,
    error_tolerance: float,
    random_seed: int | None,
):
    """Construct an OpenMM integrator by name.

    Supported names (see :data:`SUPPORTED_INTEGRATORS`):

      - ``langevin_middle`` -- LangevinMiddleIntegrator (default; best
        configurational sampling, the modern recommendation)
      - ``langevin``        -- LangevinIntegrator (legacy)
      - ``brownian``        -- BrownianIntegrator (overdamped)
      - ``verlet``          -- VerletIntegrator (NVE; no thermostat)
      - ``variable_langevin`` -- VariableLangevinIntegrator (adaptive dt)
      - ``variable_verlet``   -- VariableVerletIntegrator (adaptive dt)

    The fixed-timestep integrators take ``timestep_fs``; the variable
    ones take ``error_tolerance`` instead. The thermostatted integrators
    take ``temperature_K`` and ``friction_per_ps``; Verlet variants don't
    (a thermostat must come from a separate force / barostat coupling).
    """
    unit = omm["unit"]
    openmm = omm["openmm"]
    key = str(name).lower()

    T = temperature_K * unit.kelvin
    gamma = friction_per_ps / unit.picoseconds
    dt = timestep_fs * unit.femtoseconds

    if key == "langevin_middle":
        integ = openmm.LangevinMiddleIntegrator(T, gamma, dt)
    elif key == "langevin":
        integ = openmm.LangevinIntegrator(T, gamma, dt)
    elif key == "brownian":
        integ = openmm.BrownianIntegrator(T, gamma, dt)
    elif key == "verlet":
        integ = openmm.VerletIntegrator(dt)
    elif key == "variable_langevin":
        integ = openmm.VariableLangevinIntegrator(T, gamma, float(error_tolerance))
    elif key == "variable_verlet":
        integ = openmm.VariableVerletIntegrator(float(error_tolerance))
    else:
        raise ValueError(
            f"Unknown integrator {name!r}. Supported: "
            f"{', '.join(SUPPORTED_INTEGRATORS)}."
        )

    if random_seed is not None and hasattr(integ, "setRandomNumberSeed"):
        integ.setRandomNumberSeed(int(random_seed))
    return integ


#: Residue names OpenMM gives the lipids it builds a bilayer from. A system
#: containing them is a membrane system and needs a barostat that knows it.
_LIPID_RESIDUES = frozenset({
    "POP", "POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC",
})


def is_membrane_system(topology: Any) -> bool:
    """Whether this system contains a lipid bilayer.

    Detected from the topology rather than taken as a setting, because the
    barostat has to be right whether or not anybody remembered to say so.
    A membrane run given an isotropic barostat completes and is wrong, which
    is not a failure mode worth leaving to somebody's memory.
    """
    return any(residue.name.upper() in _LIPID_RESIDUES
               for residue in topology.residues())


def _add_barostat(omm: dict, system: Any, *, temperature_K: float,
                  pressure_bar: float, frequency: int,
                  membrane: bool = False) -> int:
    """Add a Monte Carlo barostat. Returns the force index for later removal.

    A membrane gets a different one. An ordinary barostat scales x, y and z
    together, which squeezes a bilayer that should be free to change thickness
    independently of its area -- and area per lipid is the number membrane
    simulations are validated against, so getting it wrong invalidates the run
    without stopping it. ``MonteCarloMembraneBarostat`` couples x and y (the
    membrane plane) and lets z move separately.

    The surface tension is zero, which is the usual choice: a bilayer at
    equilibrium has none, and imposing one is a modelling decision that should
    be made deliberately rather than inherited from a default.
    """
    unit = omm["unit"]
    openmm = omm["openmm"]

    if membrane:
        barostat = openmm.MonteCarloMembraneBarostat(
            pressure_bar * unit.bar,
            0.0 * unit.bar * unit.nanometer,  # surface tension
            temperature_K * unit.kelvin,
            openmm.MonteCarloMembraneBarostat.XYIsotropic,
            openmm.MonteCarloMembraneBarostat.ZFree,
            int(frequency),
        )
        logger.info(
            "Membrane barostat: x and y coupled, z free, surface tension 0. "
            "An isotropic barostat would squeeze the bilayer and give the "
            "wrong area per lipid."
        )
    else:
        barostat = openmm.MonteCarloBarostat(
            pressure_bar * unit.bar,
            temperature_K * unit.kelvin,
            int(frequency),
        )
    return system.addForce(barostat)


def _remove_force(system: Any, force_index: int) -> None:
    """Remove a force previously added by index."""
    system.removeForce(force_index)


# ---------------------------------------------------------------------------
# Reporter attach/detach helpers
# ---------------------------------------------------------------------------
def _attach_state_reporter(
    omm: dict,
    simulation: Any,
    csv_path: Path,
    *,
    interval: int,
    total_steps: int,
) -> Any:
    """Attach a CSV StateDataReporter with the standard observables."""
    # OpenMM's StateDataReporter writes a one-line header automatically.
    # We open with newline="" so the line endings are consistent
    # cross-platform and so the CSV opens cleanly in Excel.
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    reporter = omm["StateDataReporter"](
        str(csv_path),
        interval,
        step=True,
        time=True,
        potentialEnergy=True,
        kineticEnergy=True,
        totalEnergy=True,
        temperature=True,
        volume=True,
        density=True,
        progress=True,
        remainingTime=True,
        speed=True,
        totalSteps=total_steps,
        separator=",",
    )
    simulation.reporters.append(reporter)
    return reporter


def _anchor_the_pull_where_it_starts(simulation: Any, topology: Any,
                                     reanchor: dict, script_path: "Path",
                                     topology_path: Any) -> None:
    """Rewrite the pull's script with the anchor equilibration produced.

    A moving restraint travels from one value to another, and the first is
    where the system is when the pull starts. That is not where the prepared
    structure sat: minimisation, NVT and NPT all run first and all move it.
    Anchoring at the earlier value leaves the restraint a little behind the
    system, pulling backwards for an instant before the anchor overtakes --
    small over a pull of a nanometre and a half, and avoidable, since by the
    time the force is attached the right positions exist.

    Failure here is not fatal. The script written from the starting structure
    is already a working pull, so a problem reading the current state costs
    the refinement and says so rather than costing the run.
    """
    from fastmdxplora.simulation.steered import (
        build_steered_script, plan_steered)

    try:
        import mdtraj as md
        from openmm import unit as openmm_unit

        state = simulation.context.getState(getPositions=True)
        positions = state.getPositions(asNumpy=True).value_in_unit(
            openmm_unit.nanometer)
        mdtop = (topology if isinstance(topology, md.Topology)
                 else md.Topology.from_openmm(topology))
        frame = md.Trajectory(positions[None, :, :], mdtop)
        vectors = state.getPeriodicBoxVectors(asNumpy=True).value_in_unit(
            openmm_unit.nanometer)
        frame.unitcell_vectors = vectors[None, :, :]

        plan = plan_steered(reanchor["spec"], mdtop,
                            temperature_K=reanchor["temperature_K"],
                            structure=frame)
        script_path.write_text(
            build_steered_script(
                plan, reference_pdb=str(topology_path)
                if plan.cv.collective_variable == "ligand_rmsd" else None,
                # Where PLUMED's counter actually is. Equilibration has
                # already run, and a restraint told to start at step zero
                # starts production part-way along its path.
                first_step=int(getattr(simulation, "currentStep", 0) or 0)),
            encoding="utf-8")
        logger.info(
            "Pull re-anchored at %.4f nm, measured in the equilibrated "
            "state rather than the structure the run started from.",
            plan.from_value)
    except Exception as exc:  # noqa: BLE001 -- a refinement, not the run
        logger.warning(
            "Could not re-measure the pull's anchor from the equilibrated "
            "state (%s). The script written from the starting structure "
            "stands, and its anchor is that structure's value.", exc)


def resolve_save_selection(topology: Any, selection: str | None
                           ) -> "tuple[list[int] | None, str]":
    """Which atoms go into the trajectory, and a sentence about it.

    ``None`` means every atom, which is what OpenMM's own reporter writes
    and what this did before there was a choice.

    Water dominates a solvated system by a factor of ten or more, so a
    trajectory that leaves it out is a tenth the size. What it can no
    longer answer is any question about the water itself, which is why the
    saved topology is written beside it and the analyses that need solvent
    say so rather than reporting an empty result.
    """
    if selection is None or str(selection).strip().lower() in ("all", "*"):
        return None, "every atom"

    import mdtraj as md

    try:
        mdtop = (topology if isinstance(topology, md.Topology)
                 else md.Topology.from_openmm(topology))
    except (TypeError, ValueError, AttributeError, ImportError):
        # A topology this cannot read is not a reason to fail the run: the
        # trajectory is still writable, it just holds everything. Said out
        # loud, because a study that asked for the solvent to be left out
        # and got it anyway should hear why rather than find out from the
        # file size.
        #
        # ImportError belongs here and was missing. MDTraj raises it, not a
        # TypeError, when `openmm.app` is absent -- and this function runs
        # on every simulation path, so the omission failed eight tests on
        # the one CI leg without the `[md]` extra, none of which were about
        # saving a subset.
        return None, "every atom (the topology could not be read to select)"

    try:
        kept = [int(i) for i in mdtop.select(str(selection))]
    except (ValueError, SyntaxError) as exc:
        # A selection that will not parse is a mistake in the study file,
        # not a fact about the system, and it is fixable in one edit. That
        # is worth stopping for, where a valid selection matching nothing
        # is not.
        raise ValueError(
            f"`save_selection` {selection!r} is not a selection this can "
            f"read: {exc}. It is written in MDTraj's language -- `not "
            "water`, `protein`, `all` -- and a run will not start on one "
            "that cannot be parsed, because every frame would be affected."
        ) from exc
    if not kept:
        # Everything, rather than nothing or a refusal. A box of pure water
        # is a legitimate study -- it is how a water model's density is
        # checked -- and `not water` selects nothing in one, so a default
        # that refused would refuse the very study it cannot help with. A
        # mistyped selection lands here too, which is why the sentence
        # returned names it.
        return None, (
            f"every atom ({selection!r} matched none, so nothing was left "
            "out)")
    if len(kept) == mdtop.n_atoms:
        # Named, rather than reported as though `all` had been asked for.
        # A selection can parse, be wrong, and still match: `not resname
        # WAT` on a system whose water is HOH keeps every atom, and a
        # study that meant to leave the solvent out would otherwise get a
        # full-size trajectory and a line saying "every atom" with nothing
        # to suggest the selection had done nothing.
        return None, f"every atom ({selection!r} excluded none of them)"
    return kept, (
        f"{len(kept)} of {mdtop.n_atoms} atoms ({selection})")


def _attach_dcd_reporter(
    omm: dict, simulation: Any, dcd_path: Path, *, interval: int,
    atom_subset: "list[int] | None" = None,
) -> Any:
    dcd_path.parent.mkdir(parents=True, exist_ok=True)
    if atom_subset is None:
        reporter = omm["DCDReporter"](str(dcd_path), interval)
    else:
        # MDTraj's reporter, because OpenMM's writes every atom and has no
        # way to be told otherwise. The topology that matches this subset
        # is written beside it: a trajectory of 2,600 atoms read against a
        # topology of 30,750 does not fail, it misaligns, and every
        # measurement afterwards is of the wrong atoms.
        from mdtraj.reporters import DCDReporter as _SubsetDCDReporter

        reporter = _SubsetDCDReporter(
            str(dcd_path), interval, atomSubset=atom_subset)
    simulation.reporters.append(reporter)
    return reporter


def write_trajectory_topology(
    topology: Any, positions: Any, path: Path,
    atom_subset: "list[int] | None",
) -> Path | None:
    """The topology that matches what the trajectory actually holds.

    Written only when a subset was saved, because otherwise the setup
    phase's own topology already describes it and a second copy is a
    second thing to keep in step.
    """
    if atom_subset is None:
        return None

    import mdtraj as md

    mdtop = (topology if isinstance(topology, md.Topology)
             else md.Topology.from_openmm(topology))
    import numpy as _np

    coordinates = _np.asarray(
        positions.value_in_unit(__import__("openmm").unit.nanometer)
        if hasattr(positions, "value_in_unit") else positions, dtype=float)
    frame = md.Trajectory(coordinates[None, ...], mdtop)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.atom_slice(atom_subset).save_pdb(str(path))
    return path


def _attach_checkpoint_reporter(
    omm: dict, simulation: Any, chk_path: Path, *, interval: int
) -> Any:
    """Attach a binary CheckpointReporter for crash recovery / restart.

    OpenMM writes a portable ``.chk`` file every ``interval`` steps.

    There is no ``--resume`` in this software, and the checkpoint alone is not
    enough to add one safely. The positions and velocities come back; the
    biasing state does not.

    PLUMED does not re-read ``HILLS`` unless its script says ``RESTART``, and
    no script here does. A metadynamics run resumed without it would begin
    again from zero bias with the system sitting in a well it had already
    filled, and produce a free energy surface that is wrong without saying
    so. A steered pull is worse: the moving restraint is placed by absolute
    step number, so resuming mid-pull puts the anchor somewhere the protein
    is not.

    Resuming is straightforward only for unbiased runs and for umbrella
    windows, whose restraint does not depend on time. Doing it properly means
    emitting ``RESTART`` where it applies, refusing where it does not, and
    recording in the manifest that a run resumed and from where -- because a
    trajectory assembled from two pieces is not the same object as one that
    ran through, and the analyses read that provenance.

    Skipped cleanly when ``interval <= 0``.
    """
    if interval <= 0:
        return None
    chk_path.parent.mkdir(parents=True, exist_ok=True)
    reporter = omm["CheckpointReporter"](str(chk_path), interval)
    simulation.reporters.append(reporter)
    return reporter


def _detach_all_reporters(simulation: Any) -> None:
    # Closing the trajectory reporter is what flushes the DCD header.
    for r in simulation.reporters:
        close = getattr(r, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    simulation.reporters.clear()


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------
def _run_minimize(
    omm: dict,
    simulation: Any,
    *,
    tolerance_kjmol_per_nm: float,
    max_iterations: int,
    on_progress: Callable[[str], None] | None = None,
    on_explain: Callable[[str | None], None] | None = None,
) -> None:
    unit = omm["unit"]
    if on_progress:
        on_progress("Minimizing energy...")
        if on_explain:
            on_explain("minimize")
    simulation.minimizeEnergy(
        tolerance=tolerance_kjmol_per_nm * unit.kilojoules_per_mole / unit.nanometer,
        maxIterations=int(max_iterations),
    )


def _flatten_numbers(value: Any):
    """Yield numeric leaves from nested OpenMM/numpy/list containers."""
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (str, bytes)):
        return
    try:
        iterator = iter(value)
    except TypeError:
        return
    for item in iterator:
        yield from _flatten_numbers(item)


def _value_in_unit(quantity: Any, unit_value: Any) -> Any:
    if hasattr(quantity, "value_in_unit"):
        return quantity.value_in_unit(unit_value)
    return quantity


def _validation_error(stage: str, detail: str, *, topology: Any = None,
                      positions: Any = None, platform: str | None = None
                      ) -> RuntimeError:
    """Say what failed, reading the state where one is available.

    The remedies this used to list -- lower the timestep, lower the
    temperature, raise the friction -- are things that sometimes help, offered
    without knowing which applies. Sometimes none of them do, because the
    problem is a ligand whose parameters are wrong, and no timestep is small
    enough for that.
    """
    if topology is not None and positions is not None:
        try:
            from fastmdxplora.simulation.diagnose import diagnose_failure

            # OpenMM's own words are kept beside the diagnosis rather than
            # replaced by it. They say what the integrator noticed, which is
            # searchable and links to its FAQ; the diagnosis says which atoms
            # it happened to. Neither substitutes for the other.
            return RuntimeError(
                f"{diagnose_failure(topology, positions, stage=stage, platform=platform).as_text()}"
                f"\n\nOpenMM reported: {detail}.")
        except Exception:  # noqa: BLE001 - a diagnosis that fails is not the
            # failure worth reporting; fall through to the general message.
            pass

    return RuntimeError(
        f"Invalid simulation state after {stage}: {detail}. "
        "Try safer settings: lower --simulate-timestep-fs, lower "
        "--simulate-temperature-K, increase --simulate-friction-per-ps, use "
        "--simulate-precision double, or disable NPT for the first smoke test "
        "with --simulate-npt-steps 0."
    )


def _validate_state_finite(omm: dict, simulation: Any, *, stage: str) -> None:
    """Validate finite positions and potential energy at a stage boundary."""
    unit = omm["unit"]
    # Asked of the context rather than threaded down, because the context is
    # the thing that knows, and the advice that follows depends on it: on
    # CPU there is no `Precision` property to change.
    try:
        running_on = simulation.context.getPlatform().getName()
    except Exception:  # noqa: BLE001 - a platform that will not say its name
        running_on = None
    try:
        state = simulation.context.getState(
            getPositions=True,
            getEnergy=True,
            enforcePeriodicBox=True,
        )
    except TypeError:
        try:
            state = simulation.context.getState()
        except Exception as exc:  # noqa: BLE001
            raise _validation_error(stage, f"could not evaluate state ({exc})") from exc
    except Exception as exc:  # noqa: BLE001
        raise _validation_error(stage, f"could not evaluate state ({exc})") from exc

    try:
        positions = state.getPositions(asNumpy=True)
    except TypeError:
        positions = state.getPositions()
    except AttributeError as exc:
        raise _validation_error(stage, "positions unavailable") from exc

    try:
        position_values = _value_in_unit(positions, unit.nanometer)
    except Exception as exc:  # noqa: BLE001
        raise _validation_error(stage, f"could not read positions ({exc})") from exc
    position_numbers = list(_flatten_numbers(position_values))
    if not position_numbers:
        raise _validation_error(stage, "no positions found")
    if not all(math.isfinite(x) for x in position_numbers):
        # The state is the evidence: which atoms went non-finite, and what
        # they belong to, distinguishes a wrong ligand parameter from a
        # packing problem from an integration failure. The remedies differ.
        raise _validation_error(
            stage, "positions contain NaN or Inf",
            topology=simulation.topology, positions=positions,
            platform=running_on)

    try:
        energy = state.getPotentialEnergy()
    except AttributeError as exc:
        raise _validation_error(stage, "potential energy unavailable") from exc
    try:
        energy_value = _value_in_unit(energy, unit.kilojoules_per_mole)
    except Exception as exc:  # noqa: BLE001
        raise _validation_error(stage, f"could not read potential energy ({exc})") from exc
    energy_numbers = list(_flatten_numbers(energy_value))
    if not energy_numbers:
        raise _validation_error(stage, "potential energy missing")
    if not all(math.isfinite(x) for x in energy_numbers):
        raise _validation_error(
            stage, "potential energy is NaN or Inf",
            topology=simulation.topology, positions=positions,
            platform=running_on)


def _warn_density_was_never_equilibrated(
        simulation: Any, system: Any, *, temperature_K: float) -> None:
    """Say what density a run without a barostat is actually simulating at.

    The number is already measured every reporting interval and written to
    `energy.csv`, where nobody looks until something has gone wrong. Said once
    at the point the choice takes effect, it is a fact about the run rather
    than a forensic exercise.
    """
    try:
        # OpenMM is present in any real run. Where it is not, the quantities
        # still carry `value_in_unit` and the arithmetic is the same, so the
        # import is not allowed to decide whether the number gets reported.
        try:
            from openmm import unit  # noqa: PLC0415 -- optional backend

            volume_unit, mass_unit = unit.nanometer ** 3, unit.dalton
        except ImportError:
            volume_unit = mass_unit = None

        state = simulation.context.getState()
        # Coerced here so that anything which is not a number -- an unusual
        # state, a stub -- takes the same quiet path as a state that could
        # not be read at all, rather than raising further down.
        volume = float(
            state.getPeriodicBoxVolume().value_in_unit(volume_unit))
        mass = float(sum(
            system.getParticleMass(i).value_in_unit(mass_unit)
            for i in range(system.getNumParticles())))
    except Exception:  # noqa: BLE001 -- a missing number is not a reason to
        # stop a run that is otherwise fine.
        return

    if volume <= 0:
        return
    # 1 Da / nm^3 = 1.66054 g/mL.
    density = mass / volume * 1.66054e-3
    logger.warning(
        "No NPT stage, so the density is whatever solvation produced and "
        "stays there: %.3f g/mL. Liquid water is near 1.0, and a box below "
        "that has voids in it. Add `npt_steps` to let a barostat correct it, "
        "or accept that the run is not at the density of water.",
        density)


def _state_for_diagnosis(simulation: Any) -> tuple[Any, Any]:
    """The positions a failed step left behind, where they can be read.

    OpenMM detects a non-finite coordinate during integration and throws, so
    the stage-boundary check that would have read the state never runs -- and
    that is the common way a simulation dies, not the rare one. The context
    survives the exception and still holds the positions that went wrong,
    which are the whole evidence: which atoms, and what they belong to,
    separates a bad ligand parameter from a packing problem from an
    integration failure, and the remedies differ.
    """
    try:
        state = simulation.context.getState(getPositions=True)
        try:
            positions = state.getPositions(asNumpy=True)
        except TypeError:
            positions = state.getPositions()
        return simulation.topology, positions
    except Exception:  # noqa: BLE001 - a state that cannot be read is not
        # the failure worth reporting; the caller falls back to the general
        # message rather than losing the original error.
        return None, None


#: The explanation that belongs to each stage, keyed by the label the stage
#: announces itself under. A mapping rather than an argument threaded through
#: both stage runners, because the two of them announce in one place each and
#: the label is already there.
_STAGE_EXPLANATIONS: dict[str, str] = {
    "NVT equilibration": "nvt",
    "NPT equilibration": "npt",
    "Production": "production",
}


def _explanation_for(label: str) -> str | None:
    """Which explanation a stage's label calls for, if any."""
    for prefix, key in _STAGE_EXPLANATIONS.items():
        if label.startswith(prefix):
            return key
    return None


def _run_md_stage(
    simulation: Any,
    *,
    n_steps: int,
    label: str,
    on_progress: Callable[[str], None] | None = None,
    on_explain: Callable[[str | None], None] | None = None,
    on_step_progress: Callable[..., None] | None = None,
    timestep_fs: float | None = None,
) -> None:
    """Run ``n_steps`` of MD. Skips cleanly if ``n_steps <= 0``."""
    if n_steps <= 0:
        return
    if on_progress:
        on_progress(f"{label}: {n_steps:,} steps")
    if on_explain:
        on_explain(_explanation_for(label))

    # Stepped in chunks so the run can say how far through it is. A single
    # blocking call to step() printed the count and then nothing until the
    # stage finished, which for production is half an hour of a terminal that
    # looks exactly like a hung one. The chunk is a fiftieth of the stage,
    # bounded so that a short stage still reports and a long one does not pay
    # for the interruption: OpenMM's overhead per call is small beside the
    # steps in it, and at a fiftieth it is under a percent.
    total = int(n_steps)
    chunk = min(max(total // 50, 1), 5000)
    done = 0
    started = _time.monotonic()
    while done < total:
        this = min(chunk, total - done)
        try:
            simulation.step(this)
        except Exception as exc:  # noqa: BLE001
            failed_topology, failed_positions = _state_for_diagnosis(simulation)
            raise _validation_error(
                label, f"OpenMM integration failed ({exc})",
                topology=failed_topology, positions=failed_positions) from exc
        done += this
        if on_step_progress is not None:
            elapsed = _time.monotonic() - started
            rate = None
            left = None
            if elapsed > 0:
                steps_per_second = done / elapsed
                if timestep_fs and steps_per_second > 0:
                    ns_per_day = (steps_per_second * timestep_fs * 1e-6
                                  * 86_400)
                    rate = ns_per_day
                    left = (total - done) / steps_per_second
            on_step_progress(label, done, total, rate, left)


def _run_md_stage_with_live_metrics(
    omm: dict,
    simulation: Any,
    telemetry: Any,
    *,
    n_steps: int,
    label: str,
    on_progress: Callable[[str], None] | None,
    on_explain: Callable[[str | None], None] | None = None,
    current_step: int,
    total_steps: int,
    timestep_fs: float,
    telemetry_interval: int,
    trajectory_interval_steps: int | None = None,
    on_step_progress: Callable[..., None] | None = None,
    on_fraction: Callable[[float], None] | None = None,
) -> int:
    """Run an MD stage in chunks so live telemetry can sample real state.

    ``trajectory_interval_steps`` is passed only where a trajectory is being
    written, so the frame count reports the frames on disk. Left unset during
    equilibration, where a count of zero would be a claim rather than a gap.
    """
    if n_steps <= 0:
        return current_step
    if on_progress:
        on_progress(f"{label}: {n_steps:,} steps")
    if on_explain:
        on_explain(_explanation_for(label))
    remaining = int(n_steps)
    interval = max(1, int(telemetry_interval or DEFAULT_STATE_INTERVAL_STEPS))
    started = _time.monotonic()
    while remaining > 0:
        chunk = min(interval, remaining)
        try:
            simulation.step(chunk)
        except Exception as exc:  # noqa: BLE001
            failed_topology, failed_positions = _state_for_diagnosis(simulation)
            raise _validation_error(
                label, f"OpenMM integration failed ({exc})",
                topology=failed_topology, positions=failed_positions) from exc
        current_step += chunk
        remaining -= chunk
        # Frames written so far, from steps actually run. The count used to
        # arrive once, when production ended, so a page watching a run showed
        # a dash where the frame count goes for the whole of it.
        frames_written = (
            (int(n_steps) - remaining) // trajectory_interval_steps
            if trajectory_interval_steps
            else None
        )
        _append_live_metric(
            omm,
            simulation,
            telemetry,
            stage=label,
            step=current_step,
            total_steps=total_steps,
            timestep_fs=timestep_fs,
            frame_count=frames_written,
        )
        # The bar, which this stage could not drive because it had no
        # parameter for it. `_run_md_stage` took `on_step_progress` and this
        # one did not, and this one is the branch taken whenever live
        # telemetry is on -- which is the default. So production, the stage
        # that takes the hours, was the one stage with no progress shown:
        # "Production: 1,000,000 steps" and then fifty minutes of silence.
        # Anything that changes through a stage changes here: the restraint
        # ladder steps as equilibration runs rather than at its boundaries.
        if on_fraction is not None:
            on_fraction((int(n_steps) - remaining) / max(1, int(n_steps)))
        if on_step_progress is not None:
            done = int(n_steps) - remaining
            elapsed = _time.monotonic() - started
            rate = None
            left = None
            if elapsed > 0 and done > 0:
                per_step = elapsed / done
                left = per_step * remaining
                rate = (done * timestep_fs * 1e-6) / (elapsed / 86400.0)
            on_step_progress(label, done, int(n_steps), rate, left)
    return current_step


# ---------------------------------------------------------------------------
# Stage planning
# ---------------------------------------------------------------------------
def plan_stages(
    *,
    duration_ns: float | None,
    timestep_fs: float,
    nvt_steps: int | None,
    npt_steps: int | None,
    production_steps: int | None,
    nvt_duration_ns: float | None = None,
    npt_duration_ns: float | None = None,
) -> dict[str, int]:
    """Resolve per-stage step counts from a user's duration spec.

    Equilibration and production are independent. ``duration_ns`` sets
    *production* time only — standard MD convention, what people mean
    when they say "I ran a 10 ns simulation." Equilibration uses the
    the standard default lengths (500 ps NVT + 1 ns NPT) regardless
    of production length, because reaching a stable ensemble takes the
    same wall-time whether the production run is 10 ns or 1000 ns.

    Three ways to override the defaults:

      - ``nvt_steps`` / ``npt_steps`` / ``production_steps``: explicit
        step counts.
      - ``nvt_duration_ns`` / ``npt_duration_ns``: time-flavored
        equivalents for the equilibration stages.
      - ``duration_ns``: production time.

    Step-count overrides win over duration-ns overrides if both are
    supplied (lower-level wins; explicit beats inferred).
    """
    if production_steps is not None:
        auto_prod = int(production_steps)
    elif duration_ns is not None and duration_ns > 0:
        steps_per_ns = int(round(1_000_000.0 / float(timestep_fs)))
        auto_prod = int(round(duration_ns * steps_per_ns))
    else:
        auto_prod = DEFAULT_PRODUCTION_STEPS

    # NVT: fixed default, optionally overridden by ns-flavored kwarg
    if nvt_steps is not None:
        auto_nvt = int(nvt_steps)
    elif nvt_duration_ns is not None and nvt_duration_ns > 0:
        steps_per_ns = int(round(1_000_000.0 / float(timestep_fs)))
        auto_nvt = int(round(nvt_duration_ns * steps_per_ns))
    else:
        auto_nvt = DEFAULT_NVT_STEPS

    # NPT: same pattern
    if npt_steps is not None:
        auto_npt = int(npt_steps)
    elif npt_duration_ns is not None and npt_duration_ns > 0:
        steps_per_ns = int(round(1_000_000.0 / float(timestep_fs)))
        auto_npt = int(round(npt_duration_ns * steps_per_ns))
    else:
        auto_npt = DEFAULT_NPT_STEPS

    return {
        "nvt_steps": auto_nvt,
        "npt_steps": auto_npt,
        "production_steps": auto_prod,
    }


def trajectory_interval_for(
    production_steps: int,
    target_frames: int = 2000,
    min_interval: int = 100,
) -> int:
    """Compute a sensible DCD reporter interval.

    Aims for ~``target_frames`` frames in the production run, with a
    floor at ``min_interval`` to avoid absurd write rates on short runs.
    """
    if production_steps <= 0:
        return DEFAULT_TRAJECTORY_INTERVAL_STEPS
    interval = max(production_steps // target_frames, min_interval)
    return int(interval)


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------
def run_simulation(
    *,
    system_xml: str | Path,
    state_xml: str | Path,
    topology_pdb: str | Path,
    output_dir: str | Path,
    save_selection: str | None = "not water",
    # Stage controls
    minimize: bool = True,
    minimize_tolerance_kjmol_per_nm: float = DEFAULT_MINIMIZE_TOLERANCE_KJMOL_PER_NM,
    minimize_max_iterations: int = DEFAULT_MINIMIZE_MAX_ITERATIONS,
    nvt_steps: int | None = None,
    npt_steps: int | None = None,
    production_steps: int | None = None,
    duration_ns: float | None = None,
    nvt_duration_ns: float | None = None,
    npt_duration_ns: float | None = None,
    # Integrator
    integrator: str = DEFAULT_INTEGRATOR,
    integrator_error_tolerance: float = DEFAULT_INTEGRATOR_ERROR_TOLERANCE,
    timestep_fs: float = DEFAULT_TIMESTEP_FS,
    temperature_K: float = DEFAULT_TEMPERATURE_K,
    friction_per_ps: float = DEFAULT_FRICTION_PER_PS,
    pressure_bar: float | None = None,
    pressure_atm: float | None = None,
    barostat_frequency: int = DEFAULT_BAROSTAT_FREQUENCY,
    random_seed: int | None = None,
    # Hardware
    platform: str = "auto",
    precision: str = "mixed",
    device_index: str | int | None = None,
    # Reporters
    trajectory_interval_steps: int | None = None,
    state_interval_steps: int = DEFAULT_STATE_INTERVAL_STEPS,
    checkpoint_interval_steps: int = DEFAULT_CHECKPOINT_INTERVAL_STEPS,
    live_telemetry: bool = False,
    telemetry_interval: int = DEFAULT_STATE_INTERVAL_STEPS,
    # Hooks
    on_progress: Callable[[str], None] | None = None,
    on_explain: Callable[[str | None], None] | None = None,
    on_step_progress: Callable[..., None] | None = None,
    # Enhanced sampling
    plumed: dict[str, Any] | None = None,
    restrain: Any = None,
    restraint_release: Any = None,
    restrain_production: bool = False,
    metadynamics: dict[str, Any] | None = None,
    steered: dict[str, Any] | None = None,
    umbrella: dict[str, Any] | None = None,
) -> SimulationResult:
    """Run minimize → NVT → NPT → production and return paths to outputs.

    This is the function the orchestrator-facing
    :mod:`fastmdxplora.simulation.pipeline` calls. It can also be used
    directly from Python:

    >>> from fastmdxplora.simulation.runner import run_simulation
    >>> result = run_simulation(                          # doctest: +SKIP
    ...     system_xml="setup/system.xml",
    ...     state_xml="setup/state.xml",
    ...     topology_pdb="setup/topology.pdb",
    ...     output_dir="simulation/",
    ...     duration_ns=10.0,
    ... )
    """
    omm = _import_openmm()
    unit = omm["unit"]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Resolve pressure (OpenMM's barostat is in bar) ----------------
    # Accept either pressure_bar or pressure_atm; atm is the unit lab
    # scientists think in (and what AMBER uses), bar is
    # OpenMM-native. If both are given, bar wins (it's the native unit);
    # if neither, default to 1 bar.
    if pressure_bar is not None:
        resolved_pressure_bar = float(pressure_bar)
    elif pressure_atm is not None:
        resolved_pressure_bar = float(pressure_atm) * ATM_TO_BAR
    else:
        resolved_pressure_bar = DEFAULT_PRESSURE_BAR

    # ---- Resolve stage step counts -------------------------------------
    plan = plan_stages(
        duration_ns=duration_ns,
        timestep_fs=timestep_fs,
        nvt_steps=nvt_steps,
        npt_steps=npt_steps,
        production_steps=production_steps,
        nvt_duration_ns=nvt_duration_ns,
        npt_duration_ns=npt_duration_ns,
    )
    if trajectory_interval_steps is None:
        trajectory_interval_steps = trajectory_interval_for(plan["production_steps"])
    total_planned_steps = plan["nvt_steps"] + plan["npt_steps"] + plan["production_steps"]

    # ---- Deserialize System + State + Topology -------------------------
    if on_progress:
        on_progress("Loading System, State, and topology")

    system_xml_path = Path(system_xml)
    state_xml_path = Path(state_xml)
    topology_path = Path(topology_pdb)

    for label, path in [("system_xml", system_xml_path), ("state_xml", state_xml_path),
                        ("topology_pdb", topology_path)]:
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")

    with system_xml_path.open(encoding="utf-8") as fh:
        system = omm["openmm"].XmlSerializer.deserialize(fh.read())
    with state_xml_path.open(encoding="utf-8") as fh:
        state = omm["openmm"].XmlSerializer.deserialize(fh.read())

    pdb = omm["PDBFile"](str(topology_path))
    topology = pdb.topology

    # ---- Restraints -----------------------------------------------------
    # Added before the context exists, because a force cannot join a System
    # afterwards. Their strength is a global parameter, so the release
    # schedule below can weaken them without rebuilding anything.
    #
    # The reference positions come from the state the setup phase wrote --
    # the minimised structure -- rather than from wherever the run has got
    # to, so "hold it where it is" means where it started rather than where
    # it drifted.
    from fastmdxplora.simulation.restraints import (
        ReleaseSchedule,
        build_restraint_forces,
        parse_restraints,
    )

    wanted_restraints = parse_restraints(restrain)
    restraint_parameters: list[str] = []
    if wanted_restraints:
        reference = state.getPositions(asNumpy=True)
        for force, parameter in build_restraint_forces(
                omm["openmm"], topology, reference, wanted_restraints):
            system.addForce(force)
            restraint_parameters.append(parameter)
        logger.info(
            "Restraints: %s",
            "; ".join(f"{r.kind} on {r.selection} at {r.force_constant:g} "
                      f"{r.units}" for r in wanted_restraints),
        )

    release = ReleaseSchedule(
        steps=tuple(float(x) for x in restraint_release)
        if restraint_release else ReleaseSchedule().steps
    )

    def _hold_at(fraction: float) -> float:
        """Set every restraint to the strength this point in equilibration
        calls for, and report it."""
        if not restraint_parameters:
            return 0.0
        strength = release.force_at(fraction)
        for parameter in restraint_parameters:
            simulation.context.setParameter(parameter, strength)
        return strength

    if umbrella:
        # One window. The set of them is expanded above, so what arrives here
        # is a coordinate and the position this run holds it at.
        from fastmdxplora.simulation.metadynamics import cv_lines, plan_from_config
        from fastmdxplora.simulation.umbrella import Window

        centre = umbrella.get("centre")
        if centre is None:
            raise ValueError(
                "An umbrella run needs a `centre`: the value of the "
                "collective variable this window holds. A block describing a "
                "whole set of windows is expanded into runs before it reaches "
                "here."
            )
        force = umbrella.get("force_constant")
        if force is None:
            raise ValueError("An umbrella window needs a `force_constant`.")

        spec = {k: v for k, v in umbrella.items()
                if k not in ("centre", "force_constant", "n_windows",
                             "centres", "from", "to")}
        spec.setdefault("sigma", 0.05)
        spec.setdefault("unbounded", True)
        # The ligand's residue name, which a ligand variable needs and the
        # metadynamics path already passes. Without it, ligand_distance in an
        # umbrella window refused for want of something the run knew.
        cv_plan = plan_from_config(
            spec, topology, temperature_K=temperature_K,
            ligand_resname=umbrella.get("ligand_resname"))

        window = Window(index=int(umbrella.get("index", 0)),
                        centre=float(centre), force_constant=float(force))
        script_path = Path(output_dir) / "umbrella.plumed"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            window.plumed_lines(cv_lines(cv_plan, str(topology_path))),
            encoding="utf-8")
        logger.info(
            "Umbrella window %d prepared: production holds %s at %g with "
            "k=%g. The restraint applies to production only.",
            window.index, cv_plan.collective_variable, window.centre,
            window.force_constant)
        plumed = {"enabled": True, "script": str(script_path)}

    if len([x for x in (steered, metadynamics, umbrella) if x]) > 1:
        raise ValueError(
            "A run can be steered, biased with metadynamics, or held in an "
            "umbrella window -- not more than one. They are different ways "
            "of moving the same coordinate and their forces would add."
        )

    if steered and metadynamics:
        raise ValueError(
            "A run can be steered or biased with metadynamics, not both: "
            "they are two ways of moving the same coordinate and their "
            "forces would add. Pick one."
        )

    if steered:
        from fastmdxplora.simulation.steered import (
            build_steered_script,
            plan_steered,
        )

        # The structure is handed over so `from` can default to where the
        # system already is. Without it the user has to measure the
        # variable by hand and type it back in -- a number this already
        # holds.
        #
        # Measured twice where it was not given. Here, from the structure
        # the run starts out with, so the script on disk is complete and a
        # reader can see what it will do; and again at production, from the
        # state equilibration actually produced, which is where the pull
        # begins and therefore where its anchor belongs. The two differ by
        # a few hundredths of a nanometre -- 0.287 against 0.334 in the
        # study this was written for -- which is small and is not nothing,
        # since an anchor behind the system pulls backwards on the first
        # step.
        measure_the_anchor_again = steered.get("from") is None
        steered_plan = plan_steered(
            steered, topology, temperature_K=temperature_K,
            structure=str(topology_path) if topology_path else None)
        script = build_steered_script(
            steered_plan, reference_pdb=str(topology_path)
            if steered_plan.cv.collective_variable == "ligand_rmsd" else None)
        script_path = Path(output_dir) / "steered.plumed"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        if measure_the_anchor_again:
            # Read again at production, from the state equilibration made.
            # This belongs to the pull and lived, for one release, in the
            # umbrella branch above -- where the name it tests is never
            # assigned, so every window raised UnboundLocalError on its
            # first second. Three `plumed = {...}` lines in one function and
            # the edit landed on the wrong one.
            reanchor_after_equilibration = {"spec": dict(steered),
                                            "temperature_K": temperature_K}
        else:
            reanchor_after_equilibration = None

        rate = steered_plan.rate_per_ns(timestep_fs)
        logger.info(
            "Steered pull prepared: production steers %s to %g over %s "
            "steps%s. This gives a pathway and the work done along it, not a "
            "free energy.",
            steered_plan.cv.collective_variable, steered_plan.to_value,
            f"{steered_plan.steps:,}",
            f" ({rate:.3g} per ns)" if rate is not None else "",
        )
        plumed = {"enabled": True, "script": str(script_path)}
        if reanchor_after_equilibration:
            plumed["reanchor"] = reanchor_after_equilibration

    if metadynamics:
        # A named collective variable becomes PLUMED input, which the existing
        # integration then runs. Written to the output directory rather than
        # kept in memory: it decides what the run measures, and somebody
        # checking a result should be able to read it.
        from fastmdxplora.simulation.metadynamics import (
            build_plumed_script,
            build_plumed_script_pair,
            plan_from_config,
            plan_pair_from_config,
        )

        # Variables that are defined against a reference structure. Passing
        # the reference only for ligand_rmsd left `q` raising "no reference
        # structure was given" the first time anyone ran it from a config,
        # because the set of native contacts is fixed by that structure the
        # same way a reference RMSD is.
        needs_reference = {"ligand_rmsd", "q"}

        # `bias_plan`, not `plan`: `plan` is the stage plan -- a dict of step
        # counts that the rest of this function subscripts. Rebinding it to a
        # MetadynamicsPlan made the next `plan["nvt_steps"]` raise
        # "'MetadynamicsPlan' object is not subscriptable", so metadynamics
        # failed on the first line of use. Thirty-eight tests cover the module
        # and twenty-three the surface; none of them runs the runner, which is
        # where the two names met.
        if metadynamics.get("variables"):
            bias_plan = plan_pair_from_config(
                metadynamics, topology, temperature_K=temperature_K)
            biased = bias_plan.collective_variables
            reference = (str(topology_path)
                         if needs_reference & set(biased) else None)
            script = build_plumed_script_pair(
                bias_plan, reference_pdb=reference)
            described = " and ".join(biased)
            bias_factor = bias_plan.first.bias_factor
        else:
            bias_plan = plan_from_config(
                metadynamics, topology, temperature_K=temperature_K)
            reference = (str(topology_path)
                         if bias_plan.collective_variable in needs_reference
                         else None)
            script = build_plumed_script(bias_plan, reference_pdb=reference)
            described = bias_plan.collective_variable
            bias_factor = bias_plan.bias_factor

        script_path = Path(output_dir) / "metadynamics.plumed"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        logger.info(
            "Metadynamics prepared on %s. %s The bias is applied to "
            "production only; minimisation and equilibration run unbiased.",
            described,
            "Well-tempered." if bias_factor > 1 else
            "Not well-tempered: the bias will not settle and the surface "
            "will not converge.",
        )
        plumed = {"enabled": True, "script": str(script_path)}

    # PLUMED biasing (if enabled) is added just before the production stage,
    # not here — equilibration runs unbiased, matching standard enhanced-
    # sampling protocol. See Stage 4 below.

    # ---- Platform ------------------------------------------------------
    platform_obj, platform_props, platform_name = select_platform(
        omm, requested=platform, precision=precision, device_index=device_index
    )

    # ---- Integrator + Simulation ---------------------------------------
    integrator = _make_integrator(
        omm,
        name=integrator,
        temperature_K=temperature_K,
        friction_per_ps=friction_per_ps,
        timestep_fs=timestep_fs,
        error_tolerance=integrator_error_tolerance,
        random_seed=random_seed,
    )
    simulation = omm["Simulation"](
        topology, system, integrator, platform_obj, platform_props
    )
    simulation.context.setState(state)
    _validate_state_finite(omm, simulation, stage="loading state.xml")

    # Output paths
    traj_path = output_dir / "production.dcd"
    minimized_state_path = output_dir / "state_minimized.xml"
    final_state_path = output_dir / "state_final.xml"
    energy_csv = output_dir / "energy.csv"
    log_path = output_dir / "simulation.log"
    # Copy topology so the analysis phase finds it at the expected path.
    topo_out = output_dir / "topology.pdb"
    if not topo_out.exists() or topo_out.resolve() != topology_path.resolve():
        shutil.copy2(topology_path, topo_out)

    # Open the log file once and route the per-stage progress notes
    # through it in addition to whatever on_progress does.
    log_fh = log_path.open("w", encoding="utf-8")
    telemetry = None
    if live_telemetry:
        from fastmdxplora.gui.telemetry import TelemetryWriter

        planned_frames = (
            plan["production_steps"] // trajectory_interval_steps
            if trajectory_interval_steps and plan["production_steps"] > 0 else 0
        )
        telemetry = TelemetryWriter(
            output_dir,
            enabled=True,
            total_steps=total_planned_steps,
            planned_frames=planned_frames,
            timestep_fs=timestep_fs,
            platform=platform_name,
            # `Precision` is a property of the GPU platforms only, so whether
            # the requested value is in force depends on what was selected.
            # The page needs both to say which happened.
            precision=precision,
            precision_applied=bool(platform_props.get("Precision")),
            target_temperature_K=temperature_K,
        )
        telemetry.event("Live simulation telemetry started")
        telemetry.write_status(
            stage="loading",
            status="running",
            current_step=0,
            total_planned_steps=total_planned_steps,
            current_checkpoint_path=(output_dir / "checkpoint.chk").as_posix(),
        )

    def _bar(label: str, done: int, total: int, rate, left) -> None:
        """Show how far through a stage the run is, without logging it.

        Deliberately not written to the run log: a bar is for somebody
        watching, and a file full of the same line at one per cent intervals
        is noise where the log's job is to record what happened.
        """
        if on_step_progress is not None:
            on_step_progress(label, done, total, rate, left)

    def _log_step(msg: str) -> None:
        log_fh.write(msg + "\n")
        log_fh.flush()
        if telemetry is not None:
            telemetry.event(msg)
        if on_progress:
            on_progress(msg)

    try:
        # ---- Stage 1: Minimize ----------------------------------------
        if minimize:
            if telemetry is not None:
                telemetry.mark_stage("minimization", "current", status="running", current_step=0)
                telemetry.event("Minimization started")
            _run_minimize(
                omm,
                simulation,
                tolerance_kjmol_per_nm=minimize_tolerance_kjmol_per_nm,
                max_iterations=minimize_max_iterations,
                on_progress=_log_step,
                on_explain=on_explain,
            )
            _validate_state_finite(omm, simulation, stage="minimization")
            if random_seed is None:
                simulation.context.setVelocitiesToTemperature(
                    temperature_K * unit.kelvin
                )
            else:
                simulation.context.setVelocitiesToTemperature(
                    temperature_K * unit.kelvin,
                    int(random_seed),
                )
            _log_step(f"Reset velocities to {temperature_K:.1f} K after minimization")
            minimized_state = simulation.context.getState(
                getPositions=True,
                getVelocities=True,
                enforcePeriodicBox=True,
            )
            with minimized_state_path.open("w", encoding="utf-8") as fh:
                fh.write(omm["openmm"].XmlSerializer.serialize(minimized_state))
            if telemetry is not None:
                _append_live_metric(
                    omm,
                    simulation,
                    telemetry,
                    stage="minimization",
                    step=0,
                    total_steps=total_planned_steps,
                    timestep_fs=timestep_fs,
                )
                telemetry.mark_stage("minimization", "completed", status="running", current_step=0)
                telemetry.event("Minimization completed")
        elif telemetry is not None:
            telemetry.mark_stage("minimization", "skipped", status="running", current_step=0)
            telemetry.event("Minimization skipped")

        # ---- Stage 2: NVT equilibration -------------------------------
        # Use the integrator's existing thermostat (Langevin). No barostat.
        # Restraints start at full strength: this is the stage they exist for,
        # where the solvent is finding its arrangement and the solute should
        # not be moving while it does.
        _hold_at(0.0)

        # The ladder was sampled at two points -- once before NVT and once
        # before NPT -- so a four-rung ladder reached 1000 and 100 and never
        # 500 or 0. With `npt_steps: 0` the second sat inside a branch that
        # did not run, so the restraint held at full strength for the whole
        # of equilibration and dropped to zero at production: the release all
        # at once that the ladder exists to prevent, from a setting the user
        # had written out in four steps.
        #
        # Equilibration is one span whether or not a barostat runs in part of
        # it, so the fraction is measured across both stages together.
        _equilibration_steps = plan["nvt_steps"] + plan["npt_steps"]

        def _ladder_over(stage_steps: int, already_done: int):
            """Step the restraints as a stage runs, or nothing to do."""
            if not restraint_parameters or _equilibration_steps <= 0:
                return None
            last_reported: list[float] = []

            def _hook(fraction_of_stage: float) -> None:
                through = (already_done + fraction_of_stage * stage_steps) / (
                    _equilibration_steps)
                strength = _hold_at(min(1.0, through))
                if not last_reported or last_reported[-1] != strength:
                    last_reported.append(strength)
                    logger.info(
                        "Restraints now %g kJ/mol/nm^2 (%.0f%% through "
                        "equilibration).", strength, 100.0 * through)

            return _hook

        _attach_state_reporter(
            omm, simulation, energy_csv,
            interval=state_interval_steps,
            total_steps=plan["nvt_steps"] + plan["npt_steps"] + plan["production_steps"],
        )
        current_step = 0
        if telemetry is not None:
            if plan["nvt_steps"] > 0:
                telemetry.mark_stage("nvt", "current", status="running", current_step=current_step)
                telemetry.event("NVT started")
            else:
                telemetry.mark_stage("nvt", "skipped", status="running", current_step=current_step)
                telemetry.event("NVT skipped (0 steps)")
        if telemetry is not None:
            current_step = _run_md_stage_with_live_metrics(
                omm,
                simulation,
                telemetry,
                n_steps=plan["nvt_steps"],
                label="NVT equilibration",
                on_progress=_log_step,
                on_explain=on_explain,
                current_step=current_step,
                total_steps=total_planned_steps,
                timestep_fs=timestep_fs,
                telemetry_interval=telemetry_interval,
                on_step_progress=_bar,
                on_fraction=_ladder_over(plan["nvt_steps"], 0),
            )
        else:
            _run_md_stage(
                simulation,
                n_steps=plan["nvt_steps"],
                label="NVT equilibration",
                on_progress=_log_step,
                on_explain=on_explain,
                on_step_progress=_bar,
                timestep_fs=timestep_fs,
            )
            current_step += plan["nvt_steps"]
        _validate_state_finite(omm, simulation, stage="NVT equilibration")
        if telemetry is not None:
            _append_live_metric(
                omm,
                simulation,
                telemetry,
                stage="NVT",
                step=current_step,
                total_steps=total_planned_steps,
                timestep_fs=timestep_fs,
            )
            if plan["nvt_steps"] > 0:
                telemetry.mark_stage("nvt", "completed", status="running", current_step=current_step)
                telemetry.event("NVT completed")

        # No boundary sample here any more: the ladder steps continuously
        # across equilibration, so setting it to the halfway value at the
        # start of NPT would jump it backwards or forwards depending on how
        # the two stages are divided.

        # ---- Stage 3: NPT equilibration -------------------------------
        # Add the barostat and reinitialize the context so the system picks up
        # the new force. Production then continues in NPT.
        # Phrased against the same comparison the branch below uses,
        # so a plan that answers one answers both.
        if not plan["npt_steps"] > 0:
            # Solvation packs a box that is not at the density of water. A
            # real run measured 0.92 g/mL and held it there for every step,
            # because the barostat is the only thing that fixes it: the same
            # system under NPT contracted by 11% by volume to reach 1.03.
            # An under-dense box has voids, water accelerates into them, and
            # that is a plausible route to the integration failures seen on
            # exactly these runs. Reported rather than corrected, because
            # skipping NPT is a legitimate choice and how far off the density
            # is depends on the system -- but it should not be silent.
            _warn_density_was_never_equilibrated(
                simulation, system, temperature_K=temperature_K)
            # The measured number, then why it is a number worth reading.
            if on_explain:
                on_explain("ensemble_choice")

        if plan["npt_steps"] > 0:
            _add_barostat(
                omm, system,
                temperature_K=temperature_K,
                pressure_bar=resolved_pressure_bar,
            membrane=is_membrane_system(topology),
                frequency=barostat_frequency,
            )
            simulation.context.reinitialize(preserveState=True)
            if telemetry is not None:
                telemetry.mark_stage("npt", "current", status="running", current_step=current_step)
                telemetry.event("NPT started")
            if telemetry is not None:
                current_step = _run_md_stage_with_live_metrics(
                    omm,
                    simulation,
                    telemetry,
                    n_steps=plan["npt_steps"],
                    label="NPT equilibration",
                    on_progress=_log_step,
                on_explain=on_explain,
                    current_step=current_step,
                    total_steps=total_planned_steps,
                    timestep_fs=timestep_fs,
                    telemetry_interval=telemetry_interval,
                    on_step_progress=_bar,
                    on_fraction=_ladder_over(
                        plan["npt_steps"], plan["nvt_steps"]),
                )
            else:
                _run_md_stage(
                    simulation,
                    n_steps=plan["npt_steps"],
                    label="NPT equilibration",
                    on_progress=_log_step,
                on_explain=on_explain,
                    on_step_progress=_bar,
                    timestep_fs=timestep_fs,
                )
                current_step += plan["npt_steps"]
            _validate_state_finite(omm, simulation, stage="NPT equilibration")
            if telemetry is not None:
                _append_live_metric(
                    omm,
                    simulation,
                    telemetry,
                    stage="NPT",
                    step=current_step,
                    total_steps=total_planned_steps,
                    timestep_fs=timestep_fs,
                )
                telemetry.mark_stage("npt", "completed", status="running", current_step=current_step)
                telemetry.event("NPT completed")
        elif telemetry is not None:
            telemetry.mark_stage("npt", "skipped", status="running", current_step=current_step)
            telemetry.event("NPT skipped (0 steps)")

        # Restraints come off before production, because a biased production
        # run measures the bias. Keeping them is possible and has to be asked
        # for by name, and the manifest records that the trajectory was
        # restrained -- a reader comparing it against a free one otherwise has
        # no way to know.
        if restraint_parameters:
            if restrain_production:
                held = _hold_at(0.0)
                logger.warning(
                    "Restraints are still applied during production at %g. "
                    "The trajectory is biased and measures of flexibility "
                    "computed from it -- RMSF, clustering, dimensionality "
                    "reduction -- describe the restraint as much as the "
                    "system.", held,
                )
            else:
                _hold_at(1.0)
                logger.info("Restraints released for production.")

        # ---- Stage 4: Production --------------------------------------
        # Production runs in NPT (the standard default ensemble).
        #
        # Enhanced sampling: add the PLUMED biasing force now (not during
        # equilibration), then reinitialize the context so it takes effect —
        # the standard protocol equilibrates unbiased and biases production.
        if plumed:
            from fastmdxplora.simulation.plumed import add_plumed_force
            from fastmdxplora.utils.native_output import suppress_native_output

            # The anchor, from the state equilibration produced rather than
            # the one it started from. The force is attached here, so the
            # script is still ours to rewrite, and this is the first moment
            # the positions the pull will actually begin at exist.
            reanchor = plumed.pop("reanchor", None)
            if reanchor:
                _anchor_the_pull_where_it_starts(
                    simulation, topology, reanchor,
                    Path(plumed["script"]), topology_path)

            # PLUMED prints its whole setup at the moment the context takes
            # the force: which atoms the variable is built from, the hill
            # width, the pace, the bias factor, the temperature it inferred.
            # Forty lines, written from C++ straight to the file descriptor,
            # arriving in the middle of a progress bar. It is provenance and
            # worth keeping, so it goes to a file beside the run rather than
            # to the terminal or to nowhere.
            plumed_log = Path(output_dir) / "plumed.log"
            with suppress_native_output(into=plumed_log):
                plumed_force = add_plumed_force(
                    omm, system, plumed, Path(output_dir))
                if plumed_force is not None:
                    simulation.context.reinitialize(preserveState=True)
            if plumed_force is not None:
                logger.info(
                    "PLUMED enabled: biasing force added; resolved script "
                    "-> %s", (Path(output_dir) / "plumed.dat").as_posix())
            if plumed_log.is_file():
                logger.info("PLUMED's own setup log -> %s",
                            plumed_log.as_posix())
        saved_atoms, saved_description = resolve_save_selection(
            topology, save_selection)
        _attach_dcd_reporter(
            omm, simulation, traj_path,
            interval=trajectory_interval_steps, atom_subset=saved_atoms,
        )
        saved_topology = write_trajectory_topology(
            topology,
            simulation.context.getState(getPositions=True).getPositions(),
            output_dir / "trajectory_topology.pdb", saved_atoms,
        )
        logger.info(
            "Saving %s to the trajectory.%s",
            saved_description,
            "" if saved_atoms is None else
            f" The topology that matches it is {saved_topology.name}; the "
            "full system is still in setup/. Analyses of the solvent "
            "itself need `save_selection: all`.",
        )
        # Where production begins, so what it produced can be measured from
        # steps actually run rather than from the steps that were planned.
        production_start_step = current_step
        # Checkpoint reporter for crash recovery / restart.
        _attach_checkpoint_reporter(
            omm, simulation, output_dir / "checkpoint.chk",
            interval=checkpoint_interval_steps,
        )
        if telemetry is not None:
            if plan["production_steps"] > 0:
                telemetry.mark_stage("production", "current", status="running", current_step=current_step)
                telemetry.event("Production started")
            else:
                telemetry.mark_stage("production", "skipped", status="running", current_step=current_step)
                telemetry.event("Production skipped (0 steps)")
        if telemetry is not None:
            current_step = _run_md_stage_with_live_metrics(
                omm,
                simulation,
                telemetry,
                n_steps=plan["production_steps"],
                label="Production",
                on_progress=_log_step,
                on_explain=on_explain,
                current_step=current_step,
                total_steps=total_planned_steps,
                timestep_fs=timestep_fs,
                telemetry_interval=telemetry_interval,
                trajectory_interval_steps=trajectory_interval_steps,
                on_step_progress=_bar,
            )
        else:
            _run_md_stage(
                simulation,
                n_steps=plan["production_steps"],
                label="Production",
                on_progress=_log_step,
                on_explain=on_explain,
                on_step_progress=_bar,
                timestep_fs=timestep_fs,
            )
            current_step += plan["production_steps"]

        # ---- Finalize -------------------------------------------------
        # Capture the final State for restarts.
        final_state = simulation.context.getState(
            getPositions=True,
            getVelocities=True,
            enforcePeriodicBox=True,
        )
        with final_state_path.open("w", encoding="utf-8") as fh:
            fh.write(omm["openmm"].XmlSerializer.serialize(final_state))
        if telemetry is not None:
            n_frames = _frames_written(
                current_step - production_start_step, trajectory_interval_steps
            )
            _append_live_metric(
                omm,
                simulation,
                telemetry,
                stage="production",
                step=current_step,
                total_steps=total_planned_steps,
                timestep_fs=timestep_fs,
                frame_count=n_frames,
            )
            if plan["production_steps"] > 0:
                telemetry.mark_stage(
                    "production",
                    "completed",
                    status="running",
                    current_step=current_step,
                    current_frame_count=n_frames,
                    simulation_time_completed_ns=(
                        float(current_step) * float(timestep_fs) / 1_000_000.0
                    ),
                )
                telemetry.event("Production completed")
            telemetry.write_status(
                status="running",
                current_step=current_step,
                current_frame_count=n_frames,
                simulation_time_completed_ns=(
                    float(current_step) * float(timestep_fs) / 1_000_000.0
                ),
            )

    except Exception as exc:
        if telemetry is not None:
            telemetry.event(f"error: {type(exc).__name__}: {exc}", level="error")
            try:
                from fastmdxplora.gui.telemetry import read_status

                current = str(read_status(output_dir.parent).get("stage") or "production").lower()
                if current in {"minimization", "nvt", "npt", "production"}:
                    telemetry.mark_stage(
                        current,
                        "failed",
                        status="failed",
                        latest_error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    telemetry.write_status(
                        status="failed", latest_error=f"{type(exc).__name__}: {exc}"
                    )
            except Exception:
                telemetry.write_status(status="failed", latest_error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # Detached here rather than at the end of the `try`, where it was.
        # Any exception between attaching a reporter and that last statement
        # -- a NaN state from _validate_state_finite, an OpenMM integration
        # error, a PLUMED failure -- skipped it entirely and the `except`
        # re-raised without cleanup. mdtraj's DCD writer flushes per write so
        # no data was lost, but `_run_sequential` calls `_execute_run` in the
        # parent process rather than through a pool, so a long sweep leaked
        # three descriptors per failed run in one long-lived process. The
        # suite already needs an ulimit of 65536 to finish.
        _detach_all_reporters(simulation)
        log_fh.close()

    # Counted from the steps production actually ran, not from the steps it
    # was planned to run. Reading the DCD header would mean a hard dependency
    # on mdtraj; step / interval is exact for a fixed reporter interval, and
    # unlike the plan it is still right when a run stops early -- which is
    # when a field labelled "actual" matters most.
    produced_steps = current_step - production_start_step
    n_frames = _frames_written(produced_steps, trajectory_interval_steps)
    duration_ns_actual = produced_steps * timestep_fs / 1_000_000.0

    return SimulationResult(
        trajectory=traj_path,
        topology=topo_out,
        final_state=final_state_path,
        energy_csv=energy_csv,
        log_file=log_path,
        platform_used=platform_name,
        pressure_bar_used=resolved_pressure_bar,
        n_production_frames=int(n_frames),
        duration_ns_actual=float(duration_ns_actual),
        minimized_state=minimized_state_path if minimize else None,
    )


# ---------------------------------------------------------------------------
# Convenience reader -- a tiny CSV reader for the energy log (no pandas)
# ---------------------------------------------------------------------------
def read_energy_csv(path: str | Path) -> list[dict[str, str]]:
    """Read an energy.csv file and return a list of dict rows."""
    p = Path(path)
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def _frames_written(steps: int, interval: int | None) -> int:
    """Frames a reporter on ``interval`` has written over ``steps`` steps."""
    if not interval or steps <= 0:
        return 0
    return int(steps) // int(interval)


def _append_live_metric(
    omm: dict,
    simulation: Any,
    telemetry: Any,
    *,
    stage: str,
    step: int,
    total_steps: int,
    timestep_fs: float,
    frame_count: int | None = None,
) -> None:
    """Append one real state sample to live telemetry if OpenMM can provide it."""
    unit = omm["unit"]
    try:
        state = simulation.context.getState(getEnergy=True, enforcePeriodicBox=True)
    except Exception as exc:  # noqa: BLE001
        telemetry.event(f"warning: could not sample live metrics ({exc})", level="warning")
        return
    potential = _quantity_to_float(state.getPotentialEnergy(), unit.kilojoules_per_mole)
    kinetic = _quantity_to_float(state.getKineticEnergy(), unit.kilojoules_per_mole)
    total = None
    if potential is not None and kinetic is not None:
        total = potential + kinetic
    sim_time_ns = float(step) * float(timestep_fs) / 1_000_000.0
    progress = (float(step) / float(total_steps) * 100.0) if total_steps else None
    temperature = _temperature_from_kinetic_energy(simulation, kinetic)
    volume_nm3 = _periodic_volume_nm3(state, unit)
    density_g_ml = _system_density_g_ml(simulation, unit, volume_nm3)
    speed_ns_day = _telemetry_speed_ns_day(telemetry, sim_time_ns)
    telemetry.append_metric(
        stage=stage,
        step=step,
        simulation_time_ns=sim_time_ns,
        potential_energy=potential,
        kinetic_energy=kinetic,
        total_energy=total,
        temperature=temperature,
        volume=volume_nm3,
        density=density_g_ml,
        speed=speed_ns_day,
        current_frame_count=frame_count,
        progress_percent=progress,
    )
    stage_text = str(stage).lower()
    if "minim" in stage_text:
        stage_key = "minimization"
    elif "nvt" in stage_text:
        stage_key = "nvt"
    elif "npt" in stage_text:
        stage_key = "npt"
    elif "production" in stage_text:
        stage_key = "production"
    else:
        stage_key = stage_text
    telemetry.mark_stage(
        stage_key,
        "current",
        status="running",
        current_step=step,
        current_frame_count=frame_count,
        simulation_time_completed_ns=sim_time_ns,
    )
    _maybe_write_live_frame(
        omm,
        simulation,
        telemetry,
        step,
        stage=stage_key,
        simulation_time_ns=sim_time_ns,
    )


def _temperature_from_kinetic_energy(
    simulation: Any,
    kinetic_kj_per_mol: float | None,
) -> float | None:
    """Estimate instantaneous temperature using OpenMM's standard DOF rule."""

    if kinetic_kj_per_mol is None:
        return None
    try:
        system = simulation.system
        dof = 3 * int(system.getNumParticles()) - int(system.getNumConstraints())
        for index in range(int(system.getNumForces())):
            force = system.getForce(index)
            if force.__class__.__name__ == "CMMotionRemover":
                dof -= 3
                break
        if dof <= 0:
            return None
        # R in kJ mol^-1 K^-1.  OpenMM's StateDataReporter uses the same
        # equipartition relation for its Temperature column.
        gas_constant = 0.00831446261815324
        return (2.0 * float(kinetic_kj_per_mol)) / (float(dof) * gas_constant)
    except Exception:  # noqa: BLE001 - telemetry is best-effort
        return None


def _periodic_volume_nm3(state: Any, unit: Any) -> float | None:
    try:
        volume = state.getPeriodicBoxVolume()
    except Exception:  # noqa: BLE001
        return None
    try:
        return _quantity_to_float(volume, unit.nanometer**3)
    except Exception:  # noqa: BLE001
        return None


def _system_density_g_ml(
    simulation: Any,
    unit: Any,
    volume_nm3: float | None,
) -> float | None:
    if volume_nm3 is None or volume_nm3 <= 0:
        return None
    try:
        system = simulation.system
        mass_dalton = 0.0
        for index in range(int(system.getNumParticles())):
            mass_dalton += float(
                _value_in_unit(system.getParticleMass(index), unit.dalton)
            )
        # 1 Da / nm^3 = 1.66053906660e-3 g/mL.
        return mass_dalton * 1.66053906660e-3 / float(volume_nm3)
    except Exception:  # noqa: BLE001
        return None


def _telemetry_speed_ns_day(telemetry: Any, sim_time_ns: float) -> float | None:
    try:
        elapsed = (
            datetime.now(timezone.utc) - telemetry.start_time
        ).total_seconds()
    except Exception:  # noqa: BLE001
        return None
    if elapsed <= 0:
        return None
    return float(sim_time_ns) * 86400.0 / float(elapsed)


def _maybe_write_live_frame(
    omm: dict,
    simulation: Any,
    telemetry: Any,
    step: int,
    *,
    stage: str,
    simulation_time_ns: float,
) -> None:
    """Snapshot the current positions to ``simulation/live_frame.pdb``.

    Best-effort. Called from the telemetry loop so the dashboard's
    molecular viewer can refresh without disturbing the simulation.
    All exceptions are swallowed: live frames are a UI affordance and
    must never terminate the run.
    """
    try:
        from fastmdxplora.gui.live_frames import write_openmm_live_frame
        positions_state = simulation.context.getState(
            getPositions=True, enforcePeriodicBox=True
        )
        write_openmm_live_frame(
            telemetry.root,
            pdbfile_writer=omm["PDBFile"].writeFile,
            topology=simulation.topology,
            positions=positions_state.getPositions(),
            frame_index=step,
            stage=stage,
            simulation_time_ns=simulation_time_ns,
            archive=True,
        )
    except Exception as exc:  # noqa: BLE001 - dashboard failures must not crash sim
        logger.debug("live frame snapshot skipped: %s", exc)


def _quantity_to_float(quantity: Any, unit_value: Any) -> float | None:
    try:
        value = _value_in_unit(quantity, unit_value)
    except Exception:  # noqa: BLE001
        return None
    numbers = list(_flatten_numbers(value))
    return numbers[0] if numbers else None
