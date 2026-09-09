"""Analysis-phase entry point used by the project-level orchestrator.

The project-level :class:`fastmdxplora.FastMDXplora` orchestrator imports
``run`` from this module and calls it during the analysis phase. The
function is a thin adapter: it converts the project-level conventions
(orchestrator instance, phase output directory) into the analysis-level
orchestrator's API and returns the list of artifact paths.

Users who want the analysis layer directly should import
:class:`fastmdxplora.AnalysisOrchestrator` instead of using this function.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.analysis.orchestrator import (
    AnalysisOrchestrator,
    available_analyses,
)
from fastmdxplora.utils.logging import get_logger

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("analysis")


def __getattr__(name: str):
    """Lazy module-level attributes.

    ``AVAILABLE_ANALYSES`` is computed on demand so the registry is fully
    populated (analyses register themselves on import) by the time the
    attribute is read.
    """
    if name == "AVAILABLE_ANALYSES":
        return available_analyses()
    raise AttributeError(name)


def run(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    trajectory: str | None = None,
    topology: str | None = None,
    ligand_resname: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    options: dict[str, dict[str, Any]] | None = None,
    selection: str | None = None,
    select_atoms: str | None = None,
    scope: str = "solute",
    figure_colours: str | None = None,
    stride: int | None = None,
    first: int | None = None,
    last: int | None = None,
    **_extra: Any,
) -> list[str]:
    """Adapter from the project-level orchestrator to AnalysisOrchestrator.

    Resolves trajectory/topology paths (defaulting to the simulation phase
    outputs), instantiates an :class:`AnalysisOrchestrator`, runs the
    planned analyses, and returns the list of artifact paths relative to
    ``output_dir``.

    If the resolved trajectory does not exist on disk, the adapter writes
    a "deferred" manifest and returns gracefully. This keeps report-only
    and partial-pipeline workflows runnable while clearly recording the
    missing input.
    """
    import json

    # One word for a selection expression, whichever phase is asking. The
    # collective-variable blocks take `select_atoms`; an analysis taking a
    # different word for the same string would put the general name back
    # where it started, which is a thing to look up.
    if select_atoms is not None and selection is None:
        selection = select_atoms

    project_root = orchestrator.output_dir
    traj_path = Path(trajectory) if trajectory else project_root / "simulation" / "production.dcd"
    # The topology that matches the trajectory, where the two differ.
    # `save_selection` defaults to leaving the solvent out, so the file
    # beside the trajectory describes fewer atoms than the prepared system
    # does, and loading a subset against the full topology is not a
    # near-miss: it is an atom-count mismatch that stops the phase, or
    # worse a silent misalignment wherever the counts happen to agree.
    if topology:
        top_path = Path(topology)
    else:
        saved = project_root / "simulation" / "trajectory_topology.pdb"
        top_path = (saved if saved.is_file()
                    else project_root / "simulation" / "topology.pdb")

    presenter = getattr(orchestrator, "_presenter", None)

    # Graceful degradation when the simulation phase hasn't produced
    # a real trajectory yet.
    if not Path(traj_path).exists():
        deferred = {
            "phase": "analysis",
            "status": "deferred",
            "note": (
                "No trajectory found at the expected path; the simulation "
                "phase has not produced the trajectory needed for analysis. "
                "Run the simulation phase first, or pass explicit "
                "`analysis.trajectory` and `analysis.topology` paths."
            ),
            "expected_trajectory": str(traj_path),
            "expected_topology": str(top_path),
        }
        manifest_path = output_dir / "analysis_manifest.json"
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(deferred, fh, indent=2)
        logger.debug("analysis: trajectory not found, deferring (wrote %s)", manifest_path)
        if presenter:
            presenter.info("(no trajectory available — analysis deferred)")
        return ["analysis_manifest.json"]

    # Detect a ligand from the setup manifest so scope-based selections can
    # include it (solute = protein + ligand) and so ligand-specific analyses
    # know the residue name. Absent or unreadable manifest -> no ligand.
    resolved_ligand_resname = (
        ligand_resname or _detect_ligand_resname(project_root)
    )

    ao = AnalysisOrchestrator(
        trajectory=str(traj_path),
        topology=str(top_path),
        output_dir=output_dir,
        selection=selection,
        scope=scope,
        ligand_resname=resolved_ligand_resname,
        stride=stride,
        first=first,
        last=last,
        saving_interval_ps=_saving_interval_ps(project_root),
        figure_colours=figure_colours,
    )

    if presenter:
        presenter.info(
            f"Loading trajectory... {ao.traj.n_frames} frames, "
            f"{ao.traj.n_atoms} atoms, {ao.traj.n_residues} residues"
        )

    results = ao.run(include=include, exclude=exclude, options=options)

    # Per-analysis status rows, aligned to the longest analysis name.
    if presenter and results:
        name_width = max(len(n) for n in results)
        for name, r in results.items():
            # Elapsed time per analysis: best-effort from started/finished_at
            elapsed = 0.0
            if r.started_at and r.finished_at:
                from datetime import datetime as _dt

                try:
                    t0 = _dt.fromisoformat(r.started_at.replace("Z", "+00:00"))
                    t1 = _dt.fromisoformat(r.finished_at.replace("Z", "+00:00"))
                    elapsed = (t1 - t0).total_seconds()
                except ValueError:
                    elapsed = 0.0
            path = (
                r.output_dir.relative_to(orchestrator.output_dir).as_posix()
                if r.output_dir else f"analysis/{name}/"
            )
            presenter.analysis_table_row(
                name, r.status, path + "/", elapsed, name_width=name_width,
                reason=r.message if r.status != "ok" else None,
            )

    artifacts: list[str] = []
    for r in results.values():
        paths = r.artifacts or [
            p for p in (r.data_path, r.figure_path, r.options_path) if p is not None
        ]
        for p in paths:
            if p is not None:
                try:
                    artifacts.append(p.relative_to(output_dir).as_posix())
                except ValueError:
                    artifacts.append(str(p))
    artifacts.append("analysis_manifest.json")
    return artifacts


def _saving_interval_ps(project_root: Path) -> float | None:
    """Picoseconds between saved frames, from the run's own record.

    DCD does not carry this through MDTraj, so without it every time series
    is drawn against a frame index labelled "Time (ns)" -- see
    ``loading._with_a_real_clock``. The simulation phase records both halves
    of the answer already and neither had a reader.

    The reporter interval is preferred over the summary fields because it is
    what the reporter was actually configured with, and stays right when a
    run stops early: ``duration_ns_actual`` and ``n_production_frames`` are
    both counted from the steps production ran, so their ratio is also
    correct, but it is a reconstruction where the first is a setting.

    ``None`` where neither is recorded -- a foreign trajectory, or a run from
    before this was written -- which the loader turns into a frame axis
    rather than an invented one.
    """
    import json

    manifest = project_root / "simulation" / "simulation_parameters.json"
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    params = record.get("parameters") or {}
    steps = params.get("trajectory_interval_steps")
    timestep_fs = params.get("timestep_fs")
    try:
        if steps and timestep_fs:
            interval = float(steps) * float(timestep_fs) / 1000.0
            if interval > 0:
                return interval
    except (TypeError, ValueError):
        pass

    frames = record.get("n_production_frames")
    duration_ns = record.get("duration_ns_actual")
    try:
        if frames and duration_ns:
            interval = float(duration_ns) * 1000.0 / float(frames)
            if interval > 0:
                return interval
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _detect_ligand_resname(project_root: Path) -> str | None:
    """Read the ligand residue name from the setup manifest, if present.

    Returns the ligand name recorded under
    ``resolved_forcefield.ligand.name`` in ``setup/setup_parameters.json``,
    or ``None`` if there is no ligand or the manifest can't be read.
    """
    import json

    manifest = project_root / "setup" / "setup_parameters.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ligand = data.get("resolved_forcefield", {}).get("ligand")
    if not ligand:
        return None
    name = ligand.get("name")
    return str(name) if name else None
