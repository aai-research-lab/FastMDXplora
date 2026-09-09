"""Simulation pipeline: minimize, equilibrate, run production MD.

This module's public surface is :func:`run`, called by the FastMDXplora
orchestrator. Starting in v0.2.0 it performs real MD via OpenMM:

  1. Locate ``system.xml``, ``state.xml``, ``topology.pdb`` produced by
     the setup phase. (If they're missing, skip with a clear note.)
  2. Delegate to :func:`fastmdxplora.simulation.runner.run_simulation`
     for the actual minimize → NVT → NPT → production stages.
  3. Write ``simulation_parameters.json`` recording the full execution
     plan and what was produced.

Defaults
--------
Stage step counts and timestep follow standard
``build_auto_config`` so users moving between the two tools see the same
behavior:

  - Minimize: until convergence (10 kJ/mol/nm tolerance)
  - NVT: 250,000 steps (500 ps at 2 fs)
  - NPT: 500,000 steps (1 ns at 2 fs)
  - Production: 1,000,000 steps (2 ns at 2 fs)
  - Timestep: 2 fs; Temperature: 300 K; HBonds constraints (set in setup)

Pass ``duration_ns=`` to set the production length (standard MD
convention — "I ran a 10 ns simulation" means 10 ns of production).
Equilibration is independent: it uses fixed standard defaults
(500 ps NVT + 1 ns NPT) regardless of production length, because
reaching a stable ensemble takes the same wall-time whether the
production is 10 ns or 1000 ns.

To customize equilibration, pass ``nvt_duration_ns=`` /
``npt_duration_ns=`` (or the lower-level ``nvt_steps=`` /
``npt_steps=`` for exact control).

Graceful degradation
--------------------
When OpenMM isn't installed (the ``[setup]`` extras), or when the setup
phase didn't produce a ``system.xml`` (it was scaffolded only), the
simulation phase writes a manifest noting what was missing and returns
cleanly without raising. This keeps the project-level pipeline runnable
end-to-end for users who only want analysis or report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.dependencies import MissingBackendError, missing_dependencies
from fastmdxplora.config.schema import SIMULATION
from fastmdxplora.utils.logging import get_logger

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("simulation")


# Default parameters — see runner.py for the precise step counts.
#: What each option starts as, read from the schema that declares it.
#: This was a second copy of those values, and the two drifted: the pH
#: default was 7.4 in the schema and 7.0 here. There is one declaration
#: now, and a phase reads it rather than restating it.
DEFAULTS: dict[str, Any] = SIMULATION.defaults()

def _resolve_params(options: dict[str, Any]) -> dict[str, Any]:
    """What the run will use: the declared defaults, then what was asked for.

    There was a `gentle` preset here that dropped the temperature to 100 K
    along with shortening the run. That is not a smoke test, it is different
    physics -- water is ice at 100 K, and a run that survives there says
    nothing about whether the system is stable at 300 K, which is the question
    a smoke test is asked. Shortening a run at the conditions it will actually
    use is both simpler and more informative, and is what the documentation
    teaches.
    """
    return {**DEFAULTS, **options}


def _setup_outputs_present(setup_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    """Return paths to setup outputs that exist on disk, else (None, ...)."""
    system_xml = setup_dir / "system.xml"
    state_xml = setup_dir / "state.xml"
    topology = setup_dir / "topology.pdb"
    if not (system_xml.exists() and state_xml.exists() and topology.exists()):
        return None, None, None
    return system_xml, state_xml, topology


def _a_prepared_system_sits_in(directory: Path) -> bool:
    """Whether this directory holds a system a run could start from.

    All three files or none: a directory with two of them is a setup that
    did not finish, and starting from it fails later and less clearly than
    saying so here.
    """
    return all((directory / name).is_file()
               for name in ("system.xml", "state.xml", "topology.pdb"))


def _write_steered_work(output_dir: Path, params: dict, presenter: Any) -> str | None:
    """Summarise the work done by a pull, beside the run.

    Umbrella sampling goes from a config block to a curve or a refusal, and
    metadynamics to a surface or a refusal. A steered run ended with PLUMED's
    COLVAR on disk and nothing reading it -- and the work is the one number
    this method produces, the number its own documentation says is reported
    "so the claim can be judged". Left in a column of a PLUMED file, it is
    not reported; it is available.

    The work is not a free energy and this says so, because the distinction
    is the whole point: a fast pull does work against the solvent and the
    strain of the molecule as well as against the interactions of interest,
    and none of that dissipated work cancels. The rate is recorded beside the
    number so the two can be read together.
    """
    import json

    import numpy as np

    colvar = output_dir / "COLVAR"
    if not colvar.is_file():
        logger.info(
            "No work recorded for the pull: PLUMED wrote no COLVAR in %s. "
            "The pull may have been configured without a PRINT line.",
            output_dir)
        return None

    try:
        columns = np.loadtxt(colvar, comments="#")
    except (OSError, ValueError) as exc:
        logger.info("No work recorded for the pull: %s could not be read (%s).",
                    colvar.name, exc)
        return None
    # A pull short enough to write one row gives a 1-D array, which is a short
    # pull rather than an absent one.
    columns = np.atleast_2d(columns)
    if columns.shape[1] < 3 or not len(columns):
        logger.info(
            "No work recorded for the pull: %s holds %d column(s), and the "
            "work is the third. PLUMED was asked to print %s.",
            colvar.name, columns.shape[1] if columns.ndim == 2 else 0,
            "cv,pull.work,pull.bias")
        return None

    # PRINT ARG=cv,pull.work,pull.bias, so time, cv, work, bias.
    coordinate = columns[:, 1]
    work = columns[:, 2]

    spec = params.get("steered") or {}
    timestep_fs = float(params.get("timestep_fs") or 2.0)
    rate = None
    try:
        from fastmdxplora.simulation.steered import plan_steered

        topology = output_dir / "topology.pdb"
        if topology.is_file():
            import mdtraj as md

            plan = plan_steered(dict(spec), md.load(str(topology)).topology)
            rate = plan.rate_per_ns(timestep_fs)
    except Exception:  # noqa: BLE001 - the rate is a courtesy, not the result
        rate = None

    record = {
        "work_kjmol": float(work[-1]),
        "work_is_not_a_free_energy": (
            "The work done by a pull depends on how fast the anchor moves. A "
            "fast pull does work against the solvent and against the strain "
            "of the molecule as well as against the interactions being "
            "measured, and that dissipated work does not cancel -- so a "
            "single pull overestimates a barrier. Jarzynski's equality "
            "recovers a free energy from many pulls, and its average is "
            "dominated by rare low-work trajectories, so it needs many more "
            "than feels reasonable. One pull gives a pathway."
        ),
        "pull_rate_per_ns": rate,
        "from": float(coordinate[0]),
        "to": float(coordinate[-1]),
        "requested_to": spec.get("to"),
        "samples": int(len(work)),
        "trajectory": {
            "coordinate": [float(x) for x in coordinate],
            "work_kjmol": [float(x) for x in work],
        },
    }
    written = output_dir / "steered_work.json"
    written.write_text(json.dumps(record, indent=2), encoding="utf-8")

    if presenter:
        rate_text = (f" at {rate:.3g} per ns" if rate else "")
        presenter.step(
            f"Pulled to {coordinate[-1]:.3g}{rate_text}: "
            f"{float(work[-1]):.1f} kJ/mol of work, which is a pathway rather "
            "than a free energy"
        )
    return "steered_work.json"


def _write_metadynamics_surface(
    output_dir: Path, presenter: Any, temperature_K: float = 300.0
) -> str | None:
    """Build the free energy surface beside the run, or record why not.

    Either way a file is written. A refusal that leaves no trace is a run that
    looks as though the question was never asked, and the evidence behind it
    is what lets a borderline run be judged rather than only rejected.
    """
    import json

    import numpy as np

    from fastmdxplora.simulation.metad_surface import (
        compute_surface, compute_surface_2d, periodic_dimensions, read_hills)

    hills = output_dir / "HILLS"
    if not hills.is_file():
        return None

    try:
        n_dims = read_hills(hills).n_dims
    except ValueError:
        n_dims = 1

    colvar = output_dir / "COLVAR"
    sampled = None
    if colvar.is_file():
        rows = [
            line.split()
            for line in colvar.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        # One column per biased variable, in the order PRINT wrote them.
        wide = [row[1:1 + n_dims] for row in rows if len(row) > n_dims]
        if wide:
            block = np.array(wide, dtype=float)
            sampled = block[:, 0] if n_dims == 1 else block

    try:
        # A torsion is a circle, and the Gaussians have to be summed the
        # short way round. Read from the resolved script rather than passed
        # in, because that is PLUMED's own statement of what the variable is
        # and it sits beside the hills it produced. Without this the barrier
        # came out at 60.7 kJ/mol on a run whose periodic value was 30.
        script = output_dir / "plumed.dat"
        per_dim = periodic_dimensions(script, n_dims)
        if n_dims == 1:
            outcome = compute_surface(hills, sampled, periodic=per_dim[0])
        else:
            # Per variable, from its own definition line. A run biasing a
            # torsion against a distance is periodic in one and not the
            # other, and a single flag for both wraps the distance around
            # a circle it does not live on.
            names = tuple(f"cv{i + 1}" for i in range(n_dims))
            # The run's temperature, not the default. `marginal_profile`
            # integrates a dimension out through exp(-F/kT), so at 350 K a
            # 300 K constant misstated a basin free-energy difference by
            # 0.81 kJ/mol on the reproduced case -- and the error is
            # entropic, which is exactly the part the marginal exists to
            # capture. The one-dimensional path had always passed it.
            outcome = compute_surface_2d(
                hills, sampled, periodic=per_dim, names=names,
                temperature_K=float(temperature_K))
    except ValueError as exc:
        outcome = {"surface": None, "grid": None, "refused": str(exc)}

    record = {
        "refused": outcome.get("refused"),
        # Whether the surface beside this is a finished answer or a snapshot
        # of a landscape still filling. Both are worth having; only one is
        # worth quoting.
        "provisional": bool(outcome.get("provisional")),
        "evidence": outcome.get("evidence"),
        "dimensions": n_dims,
        "grid": (None if outcome.get("grid") is None
                 else [float(x) for x in outcome["grid"]]),
        # Two axes and a grid of values, where a one-variable run writes one
        # axis and a list. Named separately so a reader of the file does not
        # have to infer the shape from the length.
        "axes": outcome.get("axes"),
        "free_energy_kjmol": (
            None if outcome.get("surface") is None
            else ([float(x) for x in outcome["surface"]] if n_dims == 1
                  else [[float(x) for x in row] for row in outcome["surface"]])
        ),
    }
    written = output_dir / "metadynamics_surface.json"
    written.write_text(json.dumps(record, indent=2), encoding="utf-8")

    if presenter:
        if record["refused"]:
            # Whole, not clipped at 160 characters. This is the one message
            # whose only job is to explain why there is no surface, and the
            # cut fell mid-sentence -- "...rather than having flattened it --
            # the surface" -- losing the clause that says the output is still
            # a usable snapshot of the filling. A refusal that runs long is
            # a refusal with something to say.
            presenter.step("No free energy surface: "
                           + record["refused"].split(":", 1)[-1].strip())
        else:
            barrier = outcome["evidence"]["barrier_kjmol"]
            presenter.step(
                f"Free energy surface: barrier {barrier:.1f} kJ/mol",
                explain="metadynamics")
    return "metadynamics_surface.json"


def _where_the_system_was_prepared(
    orchestrator: "FastMDXplora", prepared_from: Any
) -> tuple[Path, bool]:
    """The directory to read the prepared system from, and whether it was named.

    Ordinarily a run prepares its own system into ``setup/`` beside its
    results. ``prepared_from`` points somewhere else instead: a run that
    already prepared this molecule, so a set of runs can share one prepared
    system rather than each solvating separately.

    Sharing matters beyond the minutes saved. Solvation places water by a
    procedure that does not give the same answer twice, so preparing the same
    molecule n times gives n systems with different atom counts. Where the
    runs are one measurement -- umbrella windows recombined into a single
    free energy -- that difference is noise in the result rather than physics.
    """
    if not prepared_from:
        return orchestrator.output_dir / "setup", False

    named = Path(str(prepared_from)).expanduser()
    if not named.is_absolute():
        # Relative to where the run was started, which is where somebody
        # typing the path can see it.
        named = Path.cwd() / named

    # A study is what a person has and remembers: `runs/reference`. Where
    # the prepared system sits inside it is this package's own layout, and
    # it is not one thing -- a single run keeps it in `setup/`, a set of
    # umbrella windows keeps one in `shared_setup/setup/` for all of them.
    # Requiring the exact interior path meant knowing which shape the
    # earlier study had, and the error for getting it wrong told the user
    # to go and look. Looking is something this can do.
    for candidate in (named,
                      named / "shared_setup" / "setup",
                      named / "setup"):
        if _a_prepared_system_sits_in(candidate):
            return candidate, True

    # Nothing found: hand back what was named, so the error names the path
    # the user wrote rather than one of the places it was looked for.
    return named, True


def run(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    **options: Any,
) -> list[str]:
    """Run the simulation phase.

    Parameters
    ----------
    orchestrator : FastMDXplora
    output_dir : pathlib.Path
        Destination for simulation artifacts.
    **options
        Overrides of the module-level :data:`DEFAULTS`.

    Returns
    -------
    list of str
        Paths (relative to ``output_dir``) of artifacts produced.
    """
    params: dict[str, Any] = _resolve_params(options)
    presenter = getattr(orchestrator, "_presenter", None)
    artifacts: list[str] = []
    notes: list[str] = []

    # ---- Locate setup outputs ------------------------------------------
    # `setup_from` is the name; `prepared_from` is what it used to be
    # called and still answers to. "Preparation" is what a person does to a
    # structure before any of this runs; `setup` is the phase that wrote
    # the directory being named.
    # Named back to the user under the spelling they used. A message about
    # `setup_from` sends somebody searching a config that says
    # `prepared_from`, and the two are the same key.
    named_by = "setup_from" if params.get("setup_from") else "prepared_from"
    setup_dir, prepared_elsewhere = _where_the_system_was_prepared(
        orchestrator,
        params.get("setup_from") or params.get("prepared_from"),
    )
    system_xml, state_xml, topology = _setup_outputs_present(setup_dir)
    if system_xml is None and prepared_elsewhere:
        # Named explicitly, so this is a wrong path rather than a phase that
        # has not run yet, and saying "run setup first" would send somebody
        # to fix the wrong thing.
        raise RuntimeError(
            f"{named_by} points at {setup_dir}, and neither it, nor "
            f"{setup_dir / 'setup'}, nor "
            f"{setup_dir / 'shared_setup' / 'setup'} holds a prepared "
            "system (system.xml, state.xml and topology.pdb). Name a study "
            "that finished its setup phase, or the setup directory itself."
        )
    if system_xml is None:
        notes.append(
            f"Setup outputs not found in {setup_dir} (system.xml / state.xml / "
            f"topology.pdb). Run the setup phase first, or skip simulation."
        )
        if presenter:
            presenter.step(
                "No setup outputs found — run setup first to produce "
                "system.xml/state.xml/topology.pdb",
                status="warning",
            )
        _write_manifest(output_dir, params, artifacts, notes, platform_used=None)
        artifacts.append("simulation_parameters.json")
        missing = missing_dependencies()
        if missing:
            raise MissingBackendError(missing)
        raise RuntimeError(
            f"Simulation cannot start because setup outputs are missing in {setup_dir}. "
            "Run the setup phase first, or choose an analysis-only workflow."
        )

    # ---- Run the simulation --------------------------------------------
    try:
        from fastmdxplora.simulation.runner import run_simulation

        if presenter:
            duration_label = (
                f"{params['duration_ns']} ns"
                if params["duration_ns"] is not None
                else "default stages"
            )
            presenter.step(
                f"Starting MD ({duration_label}, platform={params['platform']})"
            )

        def _progress(msg: str) -> None:
            if presenter:
                presenter.info(msg)

        def _explain(key: str | None) -> None:
            """Print the reason a stage exists, beside the stage.

            `explain.py` says a pipeline that does all this silently
            "teaches nothing". That held for setup, which explains its
            protonation and its solvation, and not here -- the entries for
            minimisation, NVT, NPT and production existed and were never
            reached, because the runner announces through a callback that
            carried a message and nothing else.
            """
            if presenter and key:
                presenter.explanation(key)

        def _bar(label, done, total, rate, seconds_left) -> None:
            """Drive the terminal's progress bar during a stage.

            Molecular dynamics is the part that takes the time, and it
            announced how many steps it was about to take and then said
            nothing until the stage ended.
            """
            if presenter:
                presenter.progress(label, done, total,
                                   rate_ns_per_day=rate,
                                   seconds_left=seconds_left)

        result = run_simulation(
            system_xml=system_xml,
            state_xml=state_xml,
            topology_pdb=topology,
            output_dir=output_dir,
            minimize=bool(params["minimize"]),
            minimize_tolerance_kjmol_per_nm=float(
                params["minimize_tolerance_kjmol_per_nm"]
            ),
            minimize_max_iterations=int(params["minimize_max_iterations"]),
            nvt_steps=params["nvt_steps"],
            npt_steps=params["npt_steps"],
            production_steps=params["production_steps"],
            duration_ns=params["duration_ns"],
            nvt_duration_ns=params["nvt_duration_ns"],
            npt_duration_ns=params["npt_duration_ns"],
            integrator=str(params["integrator"]),
            integrator_error_tolerance=float(params["integrator_error_tolerance"]),
            timestep_fs=float(params["timestep_fs"]),
            temperature_K=float(params["temperature_K"]),
            friction_per_ps=float(params["friction_per_ps"]),
            pressure_bar=params["pressure_bar"],
            pressure_atm=params["pressure_atm"],
            barostat_frequency=int(params["barostat_frequency"]),
            random_seed=params["random_seed"],
            platform=str(params["platform"]),
            precision=str(params["precision"]),
            device_index=params["device_index"],
            trajectory_interval_steps=params["trajectory_interval_steps"],
            save_selection=params.get("save_selection", "not water"),
            state_interval_steps=int(params["state_interval_steps"]),
            checkpoint_interval_steps=int(params["checkpoint_interval_steps"]),
            live_telemetry=bool(params["live_telemetry"]),
            telemetry_interval=int(params["telemetry_interval"]),
            on_progress=_progress,
            on_explain=_explain,
            on_step_progress=_bar,
            plumed=params.get("plumed"),
            metadynamics=params.get("metadynamics"),
            steered=params.get("steered"),
            umbrella=params.get("umbrella"),
            restrain=params.get("restrain"),
            restraint_release=params.get("restraint_release"),
            restrain_production=bool(params.get("restrain_production", False)),
        )

        # Record artifacts relative to output_dir
        for path in (
            result.trajectory,
            result.topology,
            result.final_state,
            result.energy_csv,
            result.log_file,
        ):
            try:
                artifacts.append(path.relative_to(output_dir).as_posix())
            except ValueError:
                artifacts.append(str(path))
        if result.minimized_state is not None:
            try:
                artifacts.append(result.minimized_state.relative_to(output_dir).as_posix())
            except ValueError:
                artifacts.append(str(result.minimized_state))

        if presenter:
            presenter.step(
                f"Production complete: {result.n_production_frames:,} frames, "
                f"{result.duration_ns_actual:.3f} ns on {result.platform_used}"
            )

        # A metadynamics run used to end here, with PLUMED's HILLS and COLVAR
        # on disk and nothing reading them: the run produced files rather than
        # a result, and whoever wanted a surface summed the hills themselves
        # and got one with nothing attached. Umbrella sampling went from a
        # config block to a curve or a refusal; this went to a pair of files.
        if params.get("metadynamics"):
            written = _write_metadynamics_surface(
                output_dir, presenter,
                temperature_K=float(params["temperature_K"]))
            if written is not None:
                artifacts.append(written)

        # And a steered run, for the same reason: it ended with COLVAR on
        # disk and nothing reading it, so the work -- the one number this
        # method produces -- was available rather than reported.
        if params.get("steered"):
            written = _write_steered_work(output_dir, params, presenter)
            if written is not None:
                artifacts.append(written)
        elif params.get("plumed"):
            # A steered block reaches the runner as a PLUMED script, and if
            # anything downstream reads the script rather than the block the
            # record is never written -- the pull happens and nothing
            # summarises it. Said rather than passed over.
            logger.debug(
                "No steered block in the resolved parameters, so no work "
                "record was written. Keys present: %s",
                sorted(k for k in params if params.get(k) is not None)[:40])

        _write_manifest(
            output_dir, params, artifacts, notes,
            platform_used=result.platform_used,
            pressure_bar_used=result.pressure_bar_used,
            n_frames=result.n_production_frames,
            duration_ns_actual=result.duration_ns_actual,
        )
    except ImportError as exc:
        notes.append(f"OpenMM unavailable: {exc}")
        if presenter:
            presenter.step(
                "OpenMM not installed — simulation skipped. "
                "Install via: conda install -c conda-forge openmm",
                status="warning",
            )
        _write_manifest(output_dir, params, artifacts, notes, platform_used=None)
        raise MissingBackendError(missing_dependencies()) from exc
    except Exception as exc:  # noqa: BLE001 -- runtime errors from OpenMM
        # Real runtime failure (numerical instability, bad topology, etc.).
        # Record it in the manifest so the project-level manifest still
        # picks up an actionable trace.
        notes.append(f"Simulation failed: {type(exc).__name__}: {exc}")
        if presenter:
            presenter.step(f"Simulation error: {exc}", status="error")
        _write_manifest(output_dir, params, artifacts, notes, platform_used=None)
        # Re-raise so the orchestrator marks the phase as errored.
        raise

    artifacts.append("simulation_parameters.json")

    if presenter:
        presenter.step("Wrote simulation_parameters.json")

    logger.debug("simulation: wrote %d artifact(s) to %s", len(artifacts), output_dir)
    return artifacts


def _write_manifest(
    output_dir: Path,
    params: dict[str, Any],
    artifacts: list[str],
    notes: list[str],
    *,
    platform_used: str | None,
    n_frames: int | None = None,
    duration_ns_actual: float | None = None,
    pressure_bar_used: float | None = None,
) -> None:
    """Write ``simulation_parameters.json`` with full provenance."""
    canonical = {
        "trajectory": "production.dcd",
        "topology": "topology.pdb",
        "state": "state_final.xml",
        "minimized_state": "state_minimized.xml",
        "energy_log": "energy.csv",
        "stdout_log": "simulation.log",
        "checkpoint": "checkpoint.chk",
    }
    manifest = {
        "phase": "simulation",
        "parameters": params,
        "platform_used": platform_used,
        # What the barostat ran at, rather than what was asked for: pressure
        # can be given in bar or atmospheres and unset means one bar, so the
        # number used was known only inside the runner.
        "pressure_bar_used": pressure_bar_used,
        "n_production_frames": n_frames,
        "duration_ns_actual": duration_ns_actual,
        "artifacts_planned": canonical,
        "artifacts_written": list(artifacts),
        "notes": notes,
    }
    with (output_dir / "simulation_parameters.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
