"""Starting structures for umbrella windows, taken from a steered pull.

A window is a restraint and a starting point, and only the restraint is
usually written down. Start every window from the same bound structure and
the ones near a barrier will not cross it: the restraint pulls one way, the
free energy pulls the other, and where both minima are reachable the window
settles in whichever one it began in. The histograms then sit somewhere
other than where the restraint says they are, and two neighbours that were
planned 0.07 nm apart end up 0.25 nm apart with nothing shared between them.
The recombination refuses, correctly, and a day of sampling says only that
the windows were badly started.

Steered MD is the standard answer and this module is the wiring. Pull once
along the same collective variable, take the frame whose measured value is
closest to each window's centre, and every window begins on the correct side
of whatever it would not have crossed. That is what steered MD is good for --
this package has said so in `simulation/steered.py` since before umbrella
sampling existed here -- and saying so in a docstring is not the same as
doing it for the user.

Two things this module refuses rather than guesses.

The collective variable is recomputed from the trajectory rather than read
from the pull's ``COLVAR``, because a COLVAR row and a trajectory frame are
written on different strides and aligning them by index is the kind of
off-by-one that produces a plausible seed at the wrong distance. Where a
COLVAR is present it is used as a check: the two series must agree, and a
disagreement means the selections here are not the selections PLUMED biased,
which is worth stopping for.

Molecules are made whole before any position is taken. Frames are stored
wrapped, and a ligand split across the periodic boundary is a set of
coordinates that looks fine and holds a bond several nanometres long. Every
seed's potential energy is measured against the prepared system's, and a
seed that is wildly worse is refused with its number rather than handed to a
simulation that would explode on the first step.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: How much worse than the prepared system a seed's potential energy may be
#: before it is refused, per atom. Generous: a frame pulled 1.6 nm through
#: water is genuinely strained relative to the bound state, and the check is
#: here to catch a split molecule or a mismatched topology -- failures that
#: are orders of magnitude, not tens of percent.
ENERGY_TOLERANCE_KJMOL_PER_ATOM = 5.0

#: How far a recomputed collective variable may sit from the pull's own
#: record before the two are called different quantities.
COLVAR_AGREEMENT_NM = 0.02


@dataclass(frozen=True)
class Seed:
    """One window's starting point, and where it came from."""

    index: int
    centre: float
    frame: int
    measured: float
    directory: str

    def as_record(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "centre": self.centre,
            "frame": self.frame,
            "measured": self.measured,
            "away_by": abs(self.measured - self.centre),
            "directory": self.directory,
        }


# ---------------------------------------------------------------------------
# Reading the pull
# ---------------------------------------------------------------------------
def read_colvar(path: Path | str) -> tuple[np.ndarray, np.ndarray] | None:
    """``(time, cv)`` from a PLUMED COLVAR, or ``None`` where there is none.

    Columns are found by name from the ``#! FIELDS`` header rather than by
    position, because a run that also biases something else writes more
    columns and the second one stops being ``cv``.
    """
    path = Path(path)
    if not path.is_file():
        return None

    fields: list[str] = []
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#!"):
            parts = line.split()
            if len(parts) > 2 and parts[1] == "FIELDS":
                fields = parts[2:]
            continue
        if not line.strip():
            continue
        try:
            rows.append([float(v) for v in line.split()])
        except ValueError:
            continue

    if not fields or not rows or "cv" not in fields or "time" not in fields:
        return None
    width = len(fields)
    kept = [r for r in rows if len(r) == width]
    if not kept:
        return None
    table = np.asarray(kept, dtype=float)
    return table[:, fields.index("time")], table[:, fields.index("cv")]


def measure_along(trajectory: Any, ligand_resname: str,
                  site_selection: str) -> np.ndarray:
    """The ligand-to-site centre distance in every frame, in nm.

    Mass-weighted on both sides, which is what PLUMED's ``COM`` is. An
    unweighted centroid differs by a few hundredths of a nanometre on a
    ligand with a heavy ring, which is a third of a window spacing.
    """
    topology = trajectory.topology
    ligand = topology.select(f"resname {ligand_resname}")
    site = topology.select(site_selection)
    if ligand.size == 0:
        raise ValueError(
            f"No atom matches `resname {ligand_resname}` in the pull's "
            "topology, so there is no ligand to measure from."
        )
    if site.size == 0:
        raise ValueError(
            f"No atom matches `{site_selection}` in the pull's topology, so "
            "there is no site to measure to."
        )

    masses = np.array([a.element.mass for a in topology.atoms], dtype=float)
    xyz = trajectory.xyz  # (frames, atoms, 3), nm

    def centre(selection: np.ndarray) -> np.ndarray:
        weight = masses[selection]
        return (xyz[:, selection, :] * weight[None, :, None]).sum(axis=1) \
            / weight.sum()

    return np.linalg.norm(centre(ligand) - centre(site), axis=1)


def frames_for_centres(measured: np.ndarray,
                       centres: list[float]) -> list[tuple[int, float]]:
    """The frame closest to each centre, as ``(frame, measured)``.

    Frames may repeat where a pull moved slowly through a region and two
    centres are nearer to each other than to any other frame; that is a
    statement about the pull's resolution and is reported by the caller
    rather than silently fixed by shifting a window.
    """
    return [(int(np.argmin(np.abs(measured - c))), float(
        measured[int(np.argmin(np.abs(measured - c)))])) for c in centres]


# ---------------------------------------------------------------------------
# Writing the seeds
# ---------------------------------------------------------------------------
def _prepared_files(prepared: Path) -> tuple[Path, Path]:
    system = prepared / "system.xml"
    topology = prepared / "topology.pdb"
    for path in (system, topology):
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} is not there, so the seeds cannot be built from the "
                "same system the windows will simulate. Seeds written "
                "against a different preparation place the waters "
                "differently and will not load."
            )
    return system, topology


def write_seeds(prepared: Path | str,
                trajectory: Any,
                chosen: list[tuple[int, float]],
                centres: list[float],
                destination: Path | str,
                *,
                temperature_K: float = 300.0,
                random_seed: int = 0) -> list[Seed]:
    """One prepared directory per window, each holding that window's start.

    The system and topology are copied unchanged from the pull's own
    preparation, so every window simulates the identical system and differs
    only in where it starts and what holds it. Velocities are drawn fresh
    from the Maxwell-Boltzmann distribution rather than carried from the
    pull: the pull's velocities are the velocities of a system being
    dragged, and starting equilibrium sampling from them biases the first
    picoseconds in the direction of the pull.
    """
    from openmm import (  # noqa: PLC0415  -- optional at import time
        Context, LangevinMiddleIntegrator, Platform, XmlSerializer, unit)

    prepared = Path(prepared)
    destination = Path(destination)
    system_xml, topology_pdb = _prepared_files(prepared)

    system = XmlSerializer.deserialize(
        system_xml.read_text(encoding="utf-8"))
    if system.getNumParticles() != trajectory.n_atoms:
        raise ValueError(
            f"The prepared system has {system.getNumParticles()} particles "
            f"and the pull's trajectory has {trajectory.n_atoms}. These are "
            "not the same system, and a seed written from one into the other "
            "would be nonsense."
        )

    # One context for every window. Building a Reference context over tens of
    # thousands of particles takes seconds, and thirty of them takes minutes
    # for no reason: positions are all that changes.
    integrator = LangevinMiddleIntegrator(
        temperature_K * unit.kelvin, 1.0 / unit.picosecond,
        0.002 * unit.picoseconds)
    context = Context(system, integrator,
                      Platform.getPlatformByName("Reference"))

    reference = _potential_of_prepared(context, prepared, XmlSerializer, unit)

    whole = trajectory.image_molecules(inplace=False)

    seeds: list[Seed] = []
    for index, ((frame, measured), centre) in enumerate(zip(chosen, centres)):
        out = destination / f"window-{index:02d}"
        out.mkdir(parents=True, exist_ok=True)

        context.setPositions(whole.xyz[frame] * unit.nanometer)
        if whole.unitcell_vectors is not None:
            context.setPeriodicBoxVectors(
                *(whole.unitcell_vectors[frame] * unit.nanometer))
        context.setVelocitiesToTemperature(
            temperature_K * unit.kelvin, random_seed + index)

        state = context.getState(getPositions=True, getVelocities=True,
                                 getEnergy=True, enforcePeriodicBox=False)
        potential = state.getPotentialEnergy().value_in_unit(
            unit.kilojoule_per_mole)
        _refuse_an_impossible_seed(potential, reference,
                                   system.getNumParticles(), index, measured)

        (out / "state.xml").write_text(XmlSerializer.serialize(state),
                                       encoding="utf-8")
        shutil.copy2(system_xml, out / "system.xml")
        shutil.copy2(topology_pdb, out / "topology.pdb")

        # Asserted rather than assumed. A directory that is missing one of
        # the three loads as "no prepared system here" and the window
        # silently prepares its own, which is the failure this whole module
        # exists to avoid.
        for name in ("system.xml", "state.xml", "topology.pdb"):
            if not (out / name).is_file():
                raise RuntimeError(
                    f"{out / name} was not written, so window {index} has no "
                    "starting system.")

        seeds.append(Seed(index=index, centre=float(centre), frame=int(frame),
                          measured=float(measured), directory=str(out)))

    return seeds


def _potential_of_prepared(context, prepared, XmlSerializer, unit
                           ) -> float | None:
    """The prepared system's own potential energy, for scale.

    Returns ``None`` where the prepared state cannot be read: the seeds are
    still written, and the check that needed a reference is skipped rather
    than replaced with a guess about what a reasonable energy would be.
    """
    state_xml = prepared / "state.xml"
    if not state_xml.is_file():
        return None
    try:
        state = XmlSerializer.deserialize(
            state_xml.read_text(encoding="utf-8"))
        context.setState(state)
        return context.getState(getEnergy=True).getPotentialEnergy(
        ).value_in_unit(unit.kilojoule_per_mole)
    except Exception as exc:  # pragma: no cover -- diagnostic only
        logger.debug("No reference energy from %s: %s", state_xml, exc)
        return None


def _refuse_an_impossible_seed(potential: float, reference: float | None,
                               particles: int, index: int,
                               measured: float) -> None:
    if reference is None or not np.isfinite(potential):
        if not np.isfinite(potential):
            raise ValueError(
                f"Window {index}'s seed has a potential energy of "
                f"{potential}, which is what a frame with a molecule split "
                "across the periodic boundary gives. The frame was imaged "
                "before it was used, so this is more likely a topology that "
                "does not match the system."
            )
        return
    excess = (potential - reference) / max(particles, 1)
    if excess > ENERGY_TOLERANCE_KJMOL_PER_ATOM:
        raise ValueError(
            f"Window {index}, seeded at {measured:.3f} nm, has a potential "
            f"energy {excess:.1f} kJ/mol per atom above the prepared system "
            f"({potential:.3g} against {reference:.3g}). A pulled frame is "
            "strained, but not by this much: something is wrong with the "
            "frame or with the topology it was read against, and a window "
            "started here would fail on its first step."
        )


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------
def seed_windows(pull_directory: Path | str,
                 prepared: Path | str,
                 centres: list[float],
                 destination: Path | str,
                 *,
                 ligand_resname: str,
                 site_selection: str,
                 temperature_K: float = 300.0,
                 random_seed: int = 0) -> list[Seed]:
    """Build one starting system per window from a finished steered pull.

    `pull_directory` is a run's output: the trajectory and, where PLUMED
    wrote one, the COLVAR that checks the measurement.
    """
    import mdtraj as md  # noqa: PLC0415 -- heavy, and only needed here

    pull = Path(pull_directory)
    trajectory_file, topology_file = _pull_files(pull)
    trajectory = md.load(str(trajectory_file), top=str(topology_file))
    if trajectory.n_frames < 2:
        raise ValueError(
            f"The pull at {pull} has {trajectory.n_frames} frame(s). Seeds "
            "are frames along a pull, so there is nothing to take."
        )

    measured = measure_along(trajectory, ligand_resname, site_selection)
    _check_against_colvar(pull, measured)

    chosen = frames_for_centres(measured, centres)
    _report(chosen, centres, measured)
    return write_seeds(prepared, trajectory, chosen, centres, destination,
                       temperature_K=temperature_K, random_seed=random_seed)


def _pull_files(pull: Path) -> tuple[Path, Path]:
    """The pull's trajectory and the topology that reads it."""
    simulation = pull / "simulation"
    root = simulation if simulation.is_dir() else pull
    trajectories = sorted(
        [p for p in root.glob("*.dcd")] + [p for p in root.glob("*.xtc")],
        key=lambda p: p.stat().st_size, reverse=True)
    if not trajectories:
        raise FileNotFoundError(
            f"No trajectory under {root}. A pull that wrote no frames cannot "
            "seed anything -- check that the run finished.")
    for name in ("trajectory_topology.pdb", "topology.pdb"):
        candidate = root / name
        if candidate.is_file():
            return trajectories[0], candidate
    raise FileNotFoundError(
        f"No topology beside {trajectories[0]}, so its frames cannot be "
        "read.")


def _check_against_colvar(pull: Path, measured: np.ndarray) -> None:
    """Compare the recomputed variable with the one PLUMED biased.

    Not an alignment -- the two are written on different strides. Both cover
    the same pull, so their ranges must match; where they do not, the
    selections here are measuring something PLUMED was not.
    """
    simulation = pull / "simulation"
    root = simulation if simulation.is_dir() else pull
    record = read_colvar(root / "COLVAR")
    if record is None:
        logger.info("No COLVAR beside the pull; the collective variable was "
                    "recomputed and not cross-checked.")
        return
    _, cv = record
    for name, ours, theirs in (("start", measured[0], cv[0]),
                               ("end", measured[-1], cv[-1])):
        if abs(float(ours) - float(theirs)) > COLVAR_AGREEMENT_NM:
            raise ValueError(
                f"At the {name} of the pull this module measures "
                f"{float(ours):.3f} nm and PLUMED recorded "
                f"{float(theirs):.3f} nm. Those are different quantities, so "
                "the `ligand_resname` and `site_selection` used to seed are "
                "not the ones that were biased, and the seeds would sit at "
                "distances nobody asked for."
            )


def _report(chosen: list[tuple[int, float]], centres: list[float],
            measured: np.ndarray) -> None:
    """Say how well the pull covered the windows, before anything is run."""
    away = [abs(m - c) for (_, m), c in zip(chosen, centres)]
    worst = int(np.argmax(away))
    frames = {f for f, _ in chosen}
    print(f"Seeds:          {len(centres)} windows from "
          f"{measured.size} frames spanning "
          f"{measured.min():.3f}-{measured.max():.3f} nm; "
          f"worst placement {away[worst]:.4f} nm at window {worst}")
    if len(frames) != len(chosen):
        print(f"                {len(chosen) - len(frames)} window(s) share a "
              "frame with a neighbour -- the pull moved faster than the "
              "windows are spaced there.")
