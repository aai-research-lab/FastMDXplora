"""How long a study will take, on this machine rather than on a typical one.

A caller deciding what to run needs a number before it commits the card,
and there is no honest way to supply one from the study alone. The same
config is an afternoon on one GPU and a fortnight on another, and the
difference is larger than any difference between two studies. So this
measures the machine once and estimates from that measurement.

What is measured is one number: seconds of wall clock per particle per
integration step. Molecular dynamics is dominated by the nonbonded
calculation, whose cost is close to linear in particle count for a
cutoff scheme with PME, so cost over a run is close to

    seconds ~ k x particles x steps

and ``k`` is a property of the hardware, the precision and the platform.
It is not a property of the protein, which is why one calibration serves
every study on a machine.

That relation is an approximation and this module says so rather than
implying more. It ignores the PME mesh term's mild superlinearity, any
constraint solver's iteration count, and the fixed per-step overhead that
dominates for very small systems. Against a measured run it is usually
good to a few tens of per cent, which is the accuracy a scheduling
decision needs and not the accuracy a paper would quote.

Three refusals rather than three guesses:

  - no calibration on this machine yet;
  - a calibration measured on different hardware, a different platform or
    a different precision than the study asks for;
  - a study whose particle count is not yet known, because the system has
    not been built.

Each is a case where a plausible number would be planned around and be
wrong, and a caller that has to ask again is better off than one that
schedules a fortnight as an afternoon.
"""

from __future__ import annotations

import json
import platform as _platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import StudyError
from fastmdxplora.user_dir import user_config_dir

__all__ = [
    "Calibration",
    "Estimate",
    "calibration_path",
    "calibrate",
    "measure_this_machine",
    "calibrate_from_runs",
    "costs_under",
    "Fit",
    "load_calibration",
    "estimate_seconds",
    "estimate_study",
    "describe_machine",
]


def describe_machine(platform_name: str = "", precision: str = "") -> dict[str, str]:
    """Enough about this machine to notice when it is a different one.

    Not a fingerprint and not trying to be. It records the things that
    change ``k`` by more than the model's own error: the compute platform,
    the precision, the processor and the operating system. A calibration
    carried to another machine on a shared filesystem is then detected
    rather than silently trusted.
    """
    return {
        "platform": platform_name or "unknown",
        "precision": precision or "unknown",
        "processor": _platform.processor() or _platform.machine(),
        "system": _platform.system(),
    }


@dataclass(frozen=True)
class Calibration:
    """One machine, measured.

    Attributes
    ----------
    seconds_per_particle_step
        The constant. Multiply by particles and by steps for a duration.
    particles, steps, seconds
        What was actually run to get it, kept so a reader can judge the
        measurement rather than take the constant on trust. A calibration
        from 500 steps on 3,000 particles deserves less confidence than
        one from 5,000 steps on 30,000, and only these say which it was.
    machine
        From :func:`describe_machine`.
    measured_at
        ISO timestamp. A calibration does not expire on a clock, but a
        very old one on a machine that has had a driver change is worth
        re-taking, and a reader cannot judge that without the date.
    """

    seconds_per_particle_step: float
    particles: int
    steps: int
    seconds: float
    machine: dict[str, str]
    measured_at: str

    def suits(self, machine: dict[str, str]) -> bool:
        """Whether this calibration describes the machine asked about.

        Compares only the fields that move ``k`` materially. A hostname
        change or a kernel upgrade does not invalidate a measurement; a
        move from CUDA to CPU, or from mixed to double precision, does.
        """
        for key in ("platform", "precision", "processor", "system"):
            here, there = self.machine.get(key), machine.get(key)
            if here and there and here != there:
                return False
        return True

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Estimate:
    """A duration, with what it rests on.

    ``seconds`` is never returned bare. The particle count and step count
    it was computed from travel with it, because an estimate that turns
    out wrong is almost always wrong about one of those rather than about
    the constant, and a caller cannot tell which without them.
    """

    seconds: float
    particles: int
    steps: int
    calibration: Calibration

    @property
    def hours(self) -> float:
        return self.seconds / 3600.0

    @property
    def days(self) -> float:
        return self.seconds / 86400.0

    def as_record(self) -> dict[str, Any]:
        return {
            "seconds": self.seconds,
            "hours": self.hours,
            "particles": self.particles,
            "steps": self.steps,
            "seconds_per_particle_step":
                self.calibration.seconds_per_particle_step,
            "measured_at": self.calibration.measured_at,
        }

    def __str__(self) -> str:
        if self.seconds < 90:
            rough = f"{self.seconds:.0f} seconds"
        elif self.hours < 1.5:
            rough = f"{self.seconds / 60:.0f} minutes"
        elif self.days < 1.5:
            rough = f"{self.hours:.1f} hours"
        else:
            rough = f"{self.days:.1f} days"
        return (f"about {rough} for {self.steps:,} steps on "
                f"{self.particles:,} particles")


def calibration_path() -> Path:
    """Where the measurement lives.

    Under the user's config directory rather than beside the package, so
    that a machine keeps its calibration across upgrades and a shared
    installation does not hand one machine's measurement to another.
    """
    return user_config_dir() / "calibration.json"


def load_calibration(path: Path | None = None) -> Calibration | None:
    """The stored measurement, or ``None`` where there is not one."""
    target = path or calibration_path()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Calibration(**record)
    except TypeError:
        # Written by a version that shaped this differently. Treated as
        # absent rather than repaired: a calibration is cheap to retake
        # and guessing at a missing field would put an unmeasured number
        # into a planning decision.
        return None


def calibrate(
    *,
    particles: int,
    steps: int,
    seconds: float,
    platform_name: str = "",
    precision: str = "",
    path: Path | None = None,
    save: bool = True,
) -> Calibration:
    """Record a measured run as this machine's constant.

    Takes the measurement rather than making it, so that the caller
    decides what to run: the simulation phase already knows how to build
    and integrate a system, and duplicating that here would mean a second
    path through setup that could drift from the first.

    Parameters
    ----------
    particles, steps, seconds
        What ran, and how long it took. Wall clock, not CPU time.
    """
    if particles <= 0 or steps <= 0:
        raise StudyError(
            f"A calibration needs a real run behind it; got {particles} "
            f"particles and {steps} steps.",
            code="config.option.wrong_type",
            option="calibration", found_type="non-positive",
        )
    if seconds <= 0:
        raise StudyError(
            f"A calibration run took {seconds}s, which is not a duration a "
            "constant can be divided out of.",
            code="config.option.wrong_type",
            option="seconds", found_type="non-positive",
        )

    calibration = Calibration(
        seconds_per_particle_step=seconds / (particles * steps),
        particles=int(particles),
        steps=int(steps),
        seconds=float(seconds),
        machine=describe_machine(platform_name, precision),
        measured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    if save:
        target = path or calibration_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(calibration.as_record(), indent=2),
                          encoding="utf-8")
    return calibration


def estimate_seconds(
    *,
    particles: int,
    steps: int,
    platform_name: str = "",
    precision: str = "",
    calibration: Calibration | None = None,
    path: Path | None = None,
) -> Estimate:
    """How long this many steps on this many particles should take here.

    Raises
    ------
    StudyError
        With ``environment.calibration.absent`` where the machine has not
        been measured, and ``environment.calibration.stale`` where the
        stored measurement was taken on different hardware, a different
        platform or a different precision.

        Refusals rather than fall-backs on purpose. A default constant
        would be a number from somebody else's GPU, and the failure it
        produces is the quiet kind: a schedule built on it looks
        reasonable and is wrong by an order of magnitude.
    """
    if particles <= 0:
        raise StudyError(
            "The particle count is not known yet, so there is nothing to "
            "estimate from. Particle count is settled when the system is "
            "solvated, so estimate after setup rather than before it.",
            code="setup.structure.undetermined",
        )

    found = calibration or load_calibration(path)
    if found is None:
        raise StudyError(
            "This machine has not been measured, so there is no basis for "
            "an estimate. Run a short study and pass what it took to "
            "`calibrate()`; a few thousand steps is enough.",
            code="environment.calibration.absent",
        )

    wanted = describe_machine(platform_name, precision)
    if not found.suits(wanted):
        raise StudyError(
            "The stored measurement was taken on a different machine or "
            f"under different settings ({found.machine} against {wanted}), "
            "so it does not describe what this study would run on. "
            "Re-calibrate here.",
            code="environment.calibration.stale",
            measured_on=found.machine, asked_about=wanted,
        )

    return Estimate(
        seconds=found.seconds_per_particle_step * particles * steps,
        particles=int(particles),
        steps=int(steps),
        calibration=found,
    )


#: Defaults the simulation phase applies when a config leaves them out.
#: Read here so an estimate describes the run that would actually happen
#: rather than the subset of it the config happened to mention.
_DEFAULT_TIMESTEP_FS = 2.0
_DEFAULT_PRODUCTION_STEPS = 1_000_000
_DEFAULT_NVT_STEPS = 250_000
_DEFAULT_NPT_STEPS = 500_000


def total_steps(simulation: dict[str, Any] | None) -> int:
    """Every step a study would integrate, equilibration included.

    Counting production alone understates a short study badly: the
    default equilibration is 750,000 steps, which is most of the work in
    anything under a couple of nanoseconds.
    """
    block = simulation or {}
    timestep = float(block.get("timestep_fs") or _DEFAULT_TIMESTEP_FS)

    def steps_for(explicit: str, duration: str, fallback: int) -> int:
        if block.get(explicit) is not None:
            return int(block[explicit])
        if block.get(duration) is not None:
            return int(float(block[duration]) * 1e6 / timestep)
        return fallback

    return (
        steps_for("production_steps", "duration_ns", _DEFAULT_PRODUCTION_STEPS)
        + steps_for("nvt_steps", "nvt_duration_ns", _DEFAULT_NVT_STEPS)
        + steps_for("npt_steps", "npt_duration_ns", _DEFAULT_NPT_STEPS)
    )


def estimate_study(
    config: dict[str, Any],
    *,
    particles: int,
    platform_name: str = "",
    precision: str = "",
    calibration: Calibration | None = None,
    path: Path | None = None,
) -> Estimate:
    """An estimate for a whole config, reading its step counts from it.

    ``particles`` is passed rather than inferred because the config does
    not determine it: the count depends on the solvated box, which depends
    on the structure. A caller that has run setup knows it; one that has
    not should say so rather than have this invent a number.
    """
    return estimate_seconds(
        particles=particles,
        steps=total_steps(config.get("simulation")),
        platform_name=platform_name,
        precision=precision,
        calibration=calibration,
        path=path,
    )


def measure_this_machine(
    *,
    particles: int = 3000,
    steps: int = 2000,
    platform_name: str = "",
    precision: str = "mixed",
    path: Path | None = None,
    save: bool = True,
) -> Calibration:
    """Run a small system here and record what it cost.

    :func:`calibrate` takes a measurement; this makes one. The difference
    matters because a caller who has to produce a timed run before they
    can get an estimate will not bother, and then every estimate refuses
    and the refusal looks like the software being difficult rather than
    honest.

    What runs is argon in a periodic box with a cutoff and no water: the
    cheapest thing that exercises the nonbonded calculation the cost model
    is built on. It is not a protein, and it does not need to be -- the
    constant being measured is seconds per particle per step on this
    hardware, and the nonbonded kernel is what sets it.

    Two thousand steps on three thousand particles is a few seconds on
    anything. Large enough that the fixed per-step overhead is not most of
    it, small enough that nobody is discouraged from running it.

    Raises
    ------
    BackendUnavailable
        Where OpenMM is not installed. Nothing here can measure a machine
        without it, and a fabricated constant would be worse than none.
    """
    import time as _time

    try:
        import openmm as mm
        import openmm.app as app
        from openmm import unit
    except ImportError as exc:
        from fastmdxplora.refusals import BackendUnavailable

        raise BackendUnavailable(
            "Measuring this machine needs OpenMM, and it is not installed. "
            "Without it there is no way to time a run here, and a constant "
            "taken from anywhere else would describe another machine.",
            code="environment.backend.missing", packages=["openmm"],
        ) from exc

    system = mm.System()
    spacing = 0.45
    side = int(round(particles ** (1 / 3))) + 1
    length = spacing * side
    system.setDefaultPeriodicBoxVectors(
        mm.Vec3(length, 0, 0) * unit.nanometer,
        mm.Vec3(0, length, 0) * unit.nanometer,
        mm.Vec3(0, 0, length) * unit.nanometer)
    topology = app.Topology()
    topology.setUnitCellDimensions(
        mm.Vec3(length, length, length) * unit.nanometer)
    chain = topology.addChain()
    residue = topology.addResidue("AR", chain)
    nonbonded = mm.NonbondedForce()
    nonbonded.setNonbondedMethod(mm.NonbondedForce.CutoffPeriodic)
    nonbonded.setCutoffDistance(0.9 * unit.nanometer)
    argon = app.Element.getBySymbol("Ar")
    positions = []
    for index in range(particles):
        system.addParticle(39.948 * unit.amu)
        topology.addAtom(f"AR{index}", argon, residue)
        nonbonded.addParticle(0.0, 0.34 * unit.nanometer,
                              0.996 * unit.kilojoule_per_mole)
        positions.append(mm.Vec3(spacing * (index % side),
                                 spacing * ((index // side) % side),
                                 spacing * (index // side ** 2)))
    system.addForce(nonbonded)

    integrator = mm.LangevinMiddleIntegrator(
        300 * unit.kelvin, 1 / unit.picosecond, 2 * unit.femtosecond)
    if platform_name:
        platform = mm.Platform.getPlatformByName(platform_name)
        properties = ({"Precision": precision}
                      if platform_name in ("CUDA", "OpenCL", "HIP") else None)
        simulation = app.Simulation(topology, system, integrator, platform,
                                    properties)
        measured_on = platform_name
    else:
        simulation = app.Simulation(topology, system, integrator)
        measured_on = simulation.context.getPlatform().getName()

    simulation.context.setPositions(positions * unit.nanometer)
    simulation.minimizeEnergy(maxIterations=50)
    simulation.context.setVelocitiesToTemperature(300 * unit.kelvin, 1)

    # Warm up before timing. The first steps pay for kernel compilation and
    # buffer allocation, and charging them to the constant would overstate
    # every estimate afterwards -- on a GPU by a great deal.
    simulation.step(max(100, steps // 10))
    simulation.context.getState(getEnergy=True)

    started = _time.perf_counter()
    simulation.step(steps)
    simulation.context.getState(getEnergy=True)  # make the queue drain
    elapsed = _time.perf_counter() - started

    return calibrate(particles=particles, steps=steps, seconds=elapsed,
                     platform_name=measured_on, precision=precision,
                     path=path, save=save)


@dataclass(frozen=True)
class Fit:
    """A constant fitted from real runs, and how well it fits.

    ``measure_this_machine`` gives one point from argon. A machine that has
    run real studies knows more about itself than argon does: different
    sizes, different force fields, PME rather than a plain cutoff. Fitting
    across them is strictly better information, and it also does something
    a single point cannot -- it says whether the model holds here at all.

    ``spread`` is the ratio of the largest per-run constant to the
    smallest. One would mean every run agreed exactly. The cost model
    assumes seconds go as particles times steps, and if that assumption
    fails on some hardware -- a PME mesh term dominating differently, a GPU
    whose occupancy changes sharply with system size -- the constants will
    not agree and the spread is how that shows up.
    """

    seconds_per_particle_step: float
    runs: int
    spread: float
    #: The per-run constants, so a reader can see the distribution rather
    #: than a summary of it. Two clusters and one outlier are different
    #: stories and a spread of 4 tells all three the same way.
    constants: tuple[float, ...]
    machine: dict[str, str]

    @property
    def trustworthy(self) -> bool:
        return self.spread <= SPREAD_REFUSE_ABOVE


#: How far the per-run constants may disagree before a fit is refused.
#: Three is generous -- the model is only claimed to a few tens of per cent
#: -- and it is generous on purpose, because refusing a usable fit sends
#: somebody back to argon, which is worse information.
SPREAD_REFUSE_ABOVE = 3.0


def costs_under(root: Path | str) -> list[dict[str, Any]]:
    """Every `cost.json` a run left under here.

    Walks rather than taking a list, because a campaign leaves them one per
    segment per study and asking a caller to enumerate those is asking them
    not to bother.
    """
    found: list[dict[str, Any]] = []
    for record in sorted(Path(root).rglob("cost.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if all(data.get(key) for key in ("particles", "steps", "seconds")):
            found.append(data)
    return found


def calibrate_from_runs(
    root: Path | str,
    *,
    platform_name: str = "",
    precision: str = "",
    path: Path | None = None,
    save: bool = True,
) -> Fit:
    """Fit this machine's constant from studies it has actually run.

    Better than argon in the ways that matter: real system sizes, real
    force fields, PME rather than a plain cutoff, and the same integrator
    settings the next study will use.

    Runs from other platforms or precisions are excluded rather than
    averaged in. A mixed-precision GPU run and a double-precision CPU run
    have genuinely different constants, and a mean of the two describes
    neither.

    Raises
    ------
    StudyError
        With ``environment.calibration.inconsistent`` where the per-run
        constants disagree by more than :data:`SPREAD_REFUSE_ABOVE`. That
        is not a noisy measurement, it is the linear model failing on this
        hardware, and averaging through it would produce a confident
        constant for a relationship that does not hold. The refusal names
        the spread so a reader can decide whether to fit over a narrower
        range of system sizes instead.
    """
    wanted = describe_machine(platform_name, precision)
    records = [
        r for r in costs_under(root)
        if (not platform_name or r.get("platform") == platform_name)
        and (not precision or r.get("precision") == precision)
    ]
    if not records:
        raise StudyError(
            f"No completed runs under {root} to fit from. A run writes "
            "`cost.json` beside its output when it finishes; a machine "
            "that has not run anything here has to start from "
            "`measure_this_machine()`.",
            code="environment.calibration.absent",
        )

    constants = tuple(
        float(r["seconds"]) / (float(r["particles"]) * float(r["steps"]))
        for r in records)
    low, high = min(constants), max(constants)
    spread = (high / low) if low > 0 else float("inf")

    if spread > SPREAD_REFUSE_ABOVE:
        raise StudyError(
            f"The {len(records)} runs under {root} disagree about what a "
            f"particle-step costs by a factor of {spread:.1f}. The cost "
            "model assumes seconds go as particles times steps, and a "
            "disagreement this wide says that does not hold here rather "
            "than that the measurements were noisy. Averaging through it "
            "would give a confident constant for a relationship that is "
            "not there. Fit over a narrower range of system sizes, or use "
            "`measure_this_machine()` and treat its estimates as rough.",
            code="environment.calibration.inconsistent",
            spread=spread, runs=len(records),
        )

    # The median rather than the mean. One run that swapped, or shared the
    # card with something else, is slow by an arbitrary amount and pulls a
    # mean with it; nothing makes a run anomalously fast, so the
    # distribution is one-sided and the median is the honest centre.
    ordered = sorted(constants)
    middle = len(ordered) // 2
    constant = (ordered[middle] if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2)

    total_particles = sum(int(r["particles"]) for r in records)
    total_steps = sum(int(r["steps"]) for r in records)
    total_seconds = sum(float(r["seconds"]) for r in records)
    fitted = Calibration(
        seconds_per_particle_step=constant,
        particles=total_particles // len(records),
        steps=total_steps // len(records),
        seconds=total_seconds,
        machine=wanted,
        measured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    if save:
        target = path or calibration_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(fitted.as_record(), indent=2),
                          encoding="utf-8")
    return Fit(seconds_per_particle_step=constant, runs=len(records),
               spread=spread, constants=constants, machine=wanted)
