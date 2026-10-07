"""Static HTML dashboard for FastMDXplora report outputs."""

from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import StudyError

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import FastMDXplora

logger = get_logger("gui.report_dashboard")

# Each analysis gets its own section. Grouping several analyses under
# invented headings ("Core Metrics", "Additional Analysis") made the
# placement look arbitrary: whether SASA got its own heading depended on how
# many figures it happened to produce. The sections are ordered, and the
# page's index grouped, by what each analysis studies: the run's ensemble
# first (whether the run held what it was asked to), then the protein's
# structure, its flexibility, its backbone, its contacts and solvent, the
# states it visited, a ligand, a bilayer and a free energy. This is the one
# list: every registered analysis is in it, and a test keeps it so. An
# analysis left out of it fell into one "Other" section with every other
# left out: twelve of thirty, end-to-end distance beside lipid order.
ANALYSIS_THEMES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("The run's ensemble", (
        ("thermodynamics", "Energy, temperature and density"),
    )),
    ("Structure and stability", (
        ("rmsd", "RMSD"),
        ("rg", "Radius of gyration"),
        ("end_to_end", "End-to-end distance"),
        ("moments_of_inertia", "Moments of inertia and shape"),
        ("qvalue", "Q-value"),
    )),
    ("Flexibility", (
        ("rmsf", "RMSF"),
        ("bfactor_comparison", "Comparison with B-factors"),
        ("order_parameters", "Backbone order parameters"),
    )),
    ("Secondary structure and backbone", (
        ("ss", "Secondary structure"),
        ("dihedrals", "Dihedrals"),
    )),
    ("Contacts and solvent", (
        ("hbonds", "Hydrogen bonds"),
        ("pair_distance", "Pair distance"),
        ("sasa", "Solvent accessible surface area"),
        ("rdf", "Radial distribution function"),
        ("coordination_number", "Coordination number"),
        ("water_sites", "Water sites"),
    )),
    ("Conformations", (
        ("cluster", "Clustering"),
        ("dimred", "Dimensionality reduction"),
    )),
    ("The ligand", (
        ("ligand_rmsd", "Ligand pose RMSD"),
        ("ligand_rmsf", "Ligand RMSF"),
        ("pl_contacts", "Protein-ligand contacts"),
        ("pl_hbonds", "Protein-ligand hydrogen bonds"),
        ("pl_interactions", "Protein-ligand interactions"),
    )),
    ("The bilayer", (
        ("area_per_lipid", "Area per lipid"),
        ("bilayer_thickness", "Bilayer thickness"),
        ("lipid_order", "Lipid chain order"),
    )),
    ("Free energy", (
        ("pmf", "Potential of mean force"),
        ("metad_surface", "Free-energy surface"),
        ("steered_work", "Steered work"),
    )),
)

#: Sections the report adds beside the analyses' own.
_REPORT_SECTIONS: tuple[str, ...] = ("Region highlights", "Apo/holo comparison", "Other")

SECTION_ORDER: tuple[str, ...] = tuple(
    title for _, members in ANALYSIS_THEMES for _, title in members
) + _REPORT_SECTIONS

SECTION_ANCHORS: dict[str, str] = {
    title: re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    for title in SECTION_ORDER
}

ANALYSIS_SECTION_BY_FOLDER: dict[str, str] = {
    folder: title for _, members in ANALYSIS_THEMES for folder, title in members
}
# Studies analysed before the contacts analysis was named pl_contacts.
ANALYSIS_SECTION_BY_FOLDER["contacts"] = "Protein-ligand contacts"
ANALYSIS_SECTION_BY_FOLDER["apo_holo"] = "Apo/holo comparison"

#: The theme each section belongs to, for the page's index.
SECTION_THEME: dict[str, str] = {
    title: theme for theme, members in ANALYSIS_THEMES for _, title in members
}
SECTION_THEME.update({title: "From the report" for title in _REPORT_SECTIONS[:2]})
SECTION_THEME["Other"] = "Other"


DASHBOARD_ASSET_TITLE_ALIASES: dict[str, tuple[str, ...]] = {
    "RMSD": ("RMSD",),
    "RMSF": ("RMSF",),
    "Radius of gyration": ("Radius of gyration", "Rg"),
    "Hydrogen bonds": ("Hydrogen bonds", "H-bonds"),
    "Total SASA": ("SASA", "Total SASA"),
    "PCA": ("PCA", "Dimensionality reduction PCA"),
    "MDS": ("MDS", "Dimensionality reduction MDS"),
    "t-SNE": ("t-SNE", "Dimensionality reduction t-SNE"),
    "KMeans trajectory scatter": ("KMeans trajectory scatter", "Cluster KMeans"),
    "KMeans population plot": ("KMeans population plot", "KMeans populations"),
    "Hierarchical trajectory scatter": ("Hierarchical trajectory scatter",),
    "Hierarchical population plot": ("Hierarchical population plot",),
    "Hierarchical dendrogram": ("Hierarchical dendrogram",),
    "DBSCAN trajectory scatter": ("DBSCAN trajectory scatter",),
    "DBSCAN population plot": ("DBSCAN population plot",),
    "Secondary structure": ("Secondary structure", "SS heatmap"),
    "Fraction of native contacts": ("Fraction of native contacts", "Q-value"),
    "Dihedrals": ("Dihedrals",),
}

# Title, data file, and how to summarise it. The report used to redraw
# each of these; now it only reads them for a caption.
DASHBOARD_SUMMARY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("RMSD", "analysis/rmsd/rmsd.dat", "line"),
    ("RMSF", "analysis/rmsf/rmsf.dat", "profile"),
    ("Radius of gyration", "analysis/rg/rg.dat", "line"),
    ("Hydrogen bonds", "analysis/hbonds/hbonds.dat", "line"),
    ("Total SASA", "analysis/sasa/sasa.dat", "line"),
    ("PCA", "analysis/dimred/dimred_pca.dat", "scatter"),
    ("MDS", "analysis/dimred/dimred_mds.dat", "scatter"),
    ("t-SNE", "analysis/dimred/dimred_tsne.dat", "scatter"),
    ("KMeans trajectory scatter", "analysis/cluster/cluster_kmeans.dat", "cluster"),
    ("KMeans population plot", "analysis/cluster/cluster_kmeans.dat", "cluster_counts"),
    ("Hierarchical trajectory scatter", "analysis/cluster/cluster_hierarchical.dat", "cluster"),
    ("Hierarchical population plot", "analysis/cluster/cluster_hierarchical.dat", "cluster_counts"),
    ("Hierarchical dendrogram", "analysis/cluster/hierarchical_linkage.npy", "dendrogram"),
    ("DBSCAN trajectory scatter", "analysis/cluster/cluster_dbscan.dat", "cluster"),
    ("DBSCAN population plot", "analysis/cluster/cluster_dbscan.dat", "cluster_counts"),
    ("Secondary structure", "analysis/ss/ss.dat", "ss"),
    ("Fraction of native contacts", "analysis/qvalue/qvalue.dat", "line"),
    ("Dihedrals", "analysis/dihedrals/dihedrals.dat", "dihedrals"),
)


@dataclass(frozen=True)
class DashboardCard:
    label: str
    value: str
    detail: str = ""
    kind: str = "neutral"


@dataclass(frozen=True)
class DashboardPanel:
    title: str
    source: str
    href: str
    original_source: str
    original_href: str
    mode: str
    summary: str = ""
    category: str = ""


@dataclass(frozen=True)
class DashboardLink:
    label: str
    href: str
    detail: str = ""


@dataclass(frozen=True)
class DashboardAsset:
    rel_path: str
    summary: str


@dataclass(frozen=True)
class DashboardSection:
    title: str
    anchor: str
    panels: list[DashboardPanel]
    theme: str = ""


@dataclass(frozen=True)
class PhaseRow:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class MetricRow:
    """One line of the Overview's table of what the analyses determined."""

    metric: str
    #: The mean to the place its error allows, with its unit and error.
    average: str
    #: Independent samples behind the mean.
    samples: str
    #: Determined, Not determined, or what kind of result it is.
    status: str
    #: Why a mean was not determined.
    why: str = ""


def _system_label(system: object) -> str:
    """The system's name for the header.

    The output folder and the `fastmdx gui --output ...` instruction below
    keep their full paths: this is a page opened on the machine that produced
    the run, and both need a path to be of any use. The header is the same
    field the report and the slides show, and it shows the same thing.
    """
    from fastmdxplora.report.context import _system_label as label

    return label(system)


def build_dashboard(
    *,
    orchestrator: "FastMDXplora",
    output_dir: Path,
    title: str,
    include_bundle_link: bool = False,
    not_produced: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Write ``dashboard.html`` and return artifact paths relative to output_dir.

    ``not_produced`` is what the report phase could not make, with why, said
    on the Report page as the GUI says it.
    """
    project_root = orchestrator.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(project_root / "manifest.json")
    analysis_manifest = _load_json(project_root / "analysis" / "analysis_manifest.json")
    sim_manifest = _load_json(project_root / "simulation" / "simulation_parameters.json")
    # Imported here rather than at module scope: `report` imports this
    # module to build the dashboard, so a top-level import would be
    # circular.
    from fastmdxplora.report.context import load_phase_context

    phase_context = load_phase_context(project_root)
    generated_at = datetime.now(timezone.utc)

    cards = _summary_cards(
        project_root=project_root,
        manifest=manifest,
        analysis_manifest=analysis_manifest,
        sim_manifest=sim_manifest,
    )
    dashboard_assets = _dashboard_summaries(project_root)
    sections = _analysis_sections(project_root, output_dir, dashboard_assets)
    from fastmdxplora.gui.telemetry import read_status, run_stages

    # The live record too, as the GUI reads it: the manifest is written when
    # the run ends, after this page, so a first run's page had every phase
    # "Not run" beside cards saying which had finished.
    live_status = read_status(project_root) or {}
    phase_rows = _phase_rows(manifest, live_status)
    metrics = _metric_rows(project_root, analysis_manifest)
    status = _project_status(manifest)
    phase_notice = ""
    if phase_context.is_analysis_from_existing_trajectory:
        phase_notice = (
            "Analysis/report workflow from existing trajectory. Setup and "
            "simulation were not run in this workflow."
        )
    # The health card is about a simulation; with none in this workflow and
    # no live record, the GUI does not show it, and nor does this page.
    live_html = "" if phase_notice and not live_status else _render_static_live_panel(project_root)

    html = _render_dashboard(
        title=title,
        system=str(manifest.get("system") or getattr(orchestrator, "system", "")),
        status=status,
        generated_at=generated_at,
        phase_notice=phase_notice,
        cards=cards,
        sections=sections,
        phase_rows=phase_rows,
        metrics=metrics,
        output_folder=project_root.as_posix(),
        live_html=live_html,
        stages=_stage_steps(manifest, live_status, run_stages(project_root)),
        study_state=_study_state(manifest, live_status),
        platform=str(live_status.get("platform") or ""),
        report=_report_for_page(project_root, output_dir,
                                include_bundle_link=include_bundle_link,
                                not_produced=not_produced),
        methods=_methods_for_page(project_root),
        series=_series_for_page(project_root, sections),
        files_html=_files_page(project_root, output_dir,
                               include_bundle_link=include_bundle_link,
                               not_produced=not_produced),
    )

    dashboard_path = output_dir / "dashboard.html"
    dashboard_path.write_text(html, encoding="utf-8")
    logger.debug("dashboard: wrote %s", dashboard_path)
    return ["dashboard.html"]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _live_phase_progress(
    project_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    """What a run in progress has finished, read from its live status.

    A phase counts as finished when the stage the run is in comes after all
    of that phase's stages -- a run that is minimising has finished preparing,
    because it could not have started otherwise. Returns an empty status where
    there is no telemetry at all, which is a different claim from a run that
    has finished nothing.
    """
    from fastmdxplora.gui.telemetry import PHASE_STAGES, STAGE_ORDER, read_status

    status = read_status(project_root)
    if not status:
        return {}, []

    states = status.get("stage_states")
    if not isinstance(states, dict):
        states = {}
    settled = {"completed", "complete", "done", "ok"}
    current = str(status.get("stage") or "").strip().lower()
    index = STAGE_ORDER.index(current) if current in STAGE_ORDER else -1

    finished: list[str] = []
    for phase, stages in PHASE_STAGES.items():
        reached_past = index >= 0 and all(
            STAGE_ORDER.index(stage) < index for stage in stages
        )
        all_settled = bool(stages) and all(
            str(states.get(stage, "")).lower() in settled for stage in stages
        )
        if reached_past or all_settled:
            finished.append(phase)
    return status, finished


def _summary_cards(
    *,
    project_root: Path,
    manifest: dict[str, Any],
    analysis_manifest: dict[str, Any],
    sim_manifest: dict[str, Any],
) -> list[DashboardCard]:
    phases = [p for p in manifest.get("phases", []) if isinstance(p, dict)]
    completed = [
        str(p.get("name"))
        for p in phases
        if p.get("status") == "ok" and p.get("name")
    ]
    status = _project_status(manifest)
    if manifest:
        cards = [
            DashboardCard(
                "Project status",
                status.title(),
                "Recorded from manifest",
                "good" if status == "ok" else "warn",
            ),
            DashboardCard(
                "Phases completed",
                str(len(completed)),
                ", ".join(completed) if completed else "none recorded",
            ),
        ]
    else:
        # The manifest is written when the run ends, so during a run these
        # two cards used to read "Unknown / No run manifest found" and
        # "0 / not available" directly above a phases table correctly saying
        # Setup ok, Simulation running. Same page, same run, contradicting
        # itself -- because one of them was reading a file that does not hold
        # the answer yet and reporting the gap as the answer.
        live, live_completed = _live_phase_progress(project_root)
        cards = [
            DashboardCard(
                "Project status",
                str(live.get("status") or "starting").title() if live else "Not run",
                (
                    "In progress; the manifest is written when the run ends"
                    if live
                    else "Nothing has run in this folder"
                ),
                "warn" if not live or live.get("status") == "failed" else "good",
            ),
            DashboardCard(
                "Phases completed",
                str(len(live_completed)),
                ", ".join(live_completed)
                or ("none finished yet" if live else "nothing has run"),
            ),
        ]

    n_frames = analysis_manifest.get("n_frames")
    if n_frames is not None:
        cards.append(DashboardCard("Frames", _format_number(n_frames), "analysed"))

    # The system simulated, and what of it the trajectory kept. The card gave
    # the trajectory's count alone, "47" for a peptide simulated in 6,560
    # atoms of water, beside a report saying 6,560.
    n_atoms = analysis_manifest.get("n_atoms")
    setup = _load_json_file(project_root / "setup" / "setup_parameters.json")
    simulated = setup.get("n_atoms_solvated") if isinstance(setup, dict) else None
    if isinstance(simulated, int) and simulated > 0:
        kept = (f"simulated; {_format_number(n_atoms)} kept in the trajectory"
                if isinstance(n_atoms, int) and n_atoms != simulated else "simulated")
        cards.append(DashboardCard("Atom count", _format_number(simulated), kept))
    elif n_atoms is not None:
        cards.append(DashboardCard("Atom count", _format_number(n_atoms), "in the trajectory"))

    # Imported here rather than at module scope: `report` imports this
    # module to build the dashboard, so a top-level import would be
    # circular.
    from fastmdxplora.report.context import load_phase_context

    phase_context = load_phase_context(project_root)
    if phase_context.simulation_present:
        from fastmdxplora.gui.simulated_time import (
            equilibration_said,
            say_length,
            simulated_times,
        )

        sim_params = sim_manifest.get("parameters", {})
        # The production, which the analyses average, with its
        # equilibration said beside it (or the pieces it ran in): what the
        # run recorded it ran, else how far its live record says it got,
        # else what it was asked to run, said as planned.
        try:
            live = json.loads((project_root / "simulation" / "live_status.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            live = {}
        times = simulated_times(project_root, live if isinstance(live, dict) else {})
        before = equilibration_said(times)
        if times["production_ns"] is not None:
            cards.append(DashboardCard("Production", say_length(times["production_ns"]),
                                       before or "as the run recorded it"))
        else:
            planned = sim_params.get("duration_ns") if isinstance(sim_params, dict) else None
            if isinstance(planned, (int, float)) and not isinstance(planned, bool):
                cards.append(DashboardCard("Production", say_length(planned),
                                           "planned; the run recorded none"))
        temperature = _average_temperature(project_root / "simulation" / "energy.csv")
        if temperature is not None:
            cards.append(
                DashboardCard(
                    "Temperature",
                    f"{temperature:.1f} K",
                    "energy log average",
                )
            )

    wall_time = _wall_time(project_root, phases)
    if wall_time:
        cards.append(DashboardCard("Wall time", wall_time, "the phases' own times, added"))

    # No Output folder card. Every other card here is something measured
    # about the run -- frames, atoms, temperature, wall time -- and a
    # filesystem path is navigation, not a measurement. In the browser it sits
    # in the sidebar beside the run's name and platform, and on the Open
    # Output button; in a report sent to somebody else, an absolute path from
    # the machine that produced it was never useful.
    return cards


def _project_status(manifest: dict[str, Any]) -> str:
    phases = [p for p in manifest.get("phases", []) if isinstance(p, dict)]
    if not phases:
        return "unknown"
    if any(p.get("status") == "error" for p in phases):
        return "error"
    if all(p.get("status") in {"ok", "skipped"} for p in phases):
        return "ok"
    return "unknown"


def _phase_rows(
    manifest: dict[str, Any], live_status: dict[str, Any] | None = None
) -> list[PhaseRow]:
    """What each phase did, or is doing.

    The manifest is written when a run finishes, so during one it holds
    nothing and every phase read "Not run" -- on a page showing that same
    run's energy, its temperature and its speed in ns/day. "Not run" is a
    claim that a phase did not happen; while a run is going the truth is that
    it has not finished.

    ``live_status`` is what the run writes as it goes. Where it says a run is
    active, a phase with no record yet is pending rather than absent.
    """
    recorded = {
        str(p.get("name")): p
        for p in manifest.get("phases", [])
        if isinstance(p, dict) and p.get("name")
    }
    running = bool(live_status) and str(
        (live_status or {}).get("status") or "").lower() in {"running", "live"}

    order = ("setup", "simulation", "analysis", "report")
    # A run that is simulating has finished preparing, whatever the manifest
    # says: it could not have started otherwise. So the phases before the one
    # in progress are complete, not pending. The phase in progress is the
    # one its stage belongs to: this was always the simulation, so a run
    # writing its report read "Simulation: report", analysis not yet done.
    live_at = None
    if running:
        from fastmdxplora.gui.telemetry import PHASE_STAGES

        phase_of = {stage: phase for phase, stages in PHASE_STAGES.items() for stage in stages}
        stage_now = _normalise_stage((live_status or {}).get("stage"))
        live_at = order.index(phase_of.get(stage_now, "simulation"))

    rows: list[PhaseRow] = []
    for position, name in enumerate(order):
        phase = recorded.get(name)
        if phase is None:
            if live_at is None:
                rows.append(PhaseRow(name.title(), "not-run", "Not run"))
            elif position < live_at:
                rows.append(PhaseRow(
                    name.title(), "ok", "Complete; recorded when the run ends"))
            elif position == live_at:
                stage = str((live_status or {}).get("stage") or "").strip()
                rows.append(PhaseRow(
                    name.title(), "running", stage or "Running"))
            else:
                rows.append(PhaseRow(name.title(), "pending", "Not yet"))
            continue
        raw_status = str(phase.get("status") or "unknown")
        if raw_status == "ok":
            detail = "Completed"
        elif raw_status == "error":
            detail = "Failed"
        elif raw_status == "skipped":
            detail = "Skipped"
        else:
            detail = raw_status.title()
        rows.append(PhaseRow(name.title(), raw_status, detail))
    return rows


def _metric_rows(project_root: Path, analysis_manifest: dict[str, Any]) -> list[MetricRow]:
    """The study's main means, as the Analysis page's table gives them.

    Read from the record the Analysis page reads (`analysis_overview`): each
    mean after equilibration with its standard error, the independent
    samples behind it, and whether it was determined. The table computed its
    own mean and standard deviation over the frames, so the Overview said
    "RMSD 0.0988, std. dev. 0.0095" where the Analysis page said
    "0.0988 ± 0.0017 nm": a spread over the frames where the reader looks
    for the mean's error. RMSF is one value per residue, not a series over
    time, so it is said over its residues, with their range, and no error.
    """
    from fastmdxplora.gui.analysis_overview import _reweighted, overview_of
    from fastmdxplora.report.reweighted import load_reweighted

    specs: tuple[tuple[str, str, str], ...] = (
        ("RMSD", "rmsd", "nm"),
        ("RMSF", "rmsf", "nm"),
        ("Radius of gyration", "rg", "nm"),
        ("Hydrogen bonds", "hbonds", ""),
        ("SASA", "sasa", "nm²"),
    )
    overview = overview_of(project_root)
    by_name = {row["analysis"]: row for row in overview.get("rows", [])}
    biased = bool(overview.get("biased"))
    reweighted = load_reweighted(project_root)
    corrected = {item.get("analysis"): item
                 for item in ((reweighted or {}).get("quantities") or [])
                 if isinstance(item, dict)}
    rows: list[MetricRow] = []
    for label, name, unit in specs:
        if name == "rmsf":
            values = _numeric_series(project_root / "analysis" / "rmsf" / "rmsf.dat")
            # One value a residue, or one an atom where the analysis was
            # asked for atoms.
            try:
                options = json.loads((project_root / "analysis" / "rmsf" / "options.json")
                                     .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                options = {}
            asked = (options.get("options") if isinstance(options, dict) else None) or {}
            each = "residue" if not isinstance(asked, dict) or asked.get(
                "per_residue", True) else "atom"
            if values:
                rows.append(MetricRow(
                    metric=f"{label} (biased ensemble)" if biased else label,
                    average=(f"{_format_metric_value(_mean(values))} nm over "
                             f"{len(values):,} {each}s ({_format_metric_value(min(values))} "
                             f"to {_format_metric_value(max(values))})"),
                    samples="—",
                    status=f"per {each}",
                ))
            continue
        row = by_name.get(name)
        quantity = next((q for q in (row or {}).get("quantities", []) if q.get("key") == "mean"),
                        None)
        if quantity is None and name in corrected:
            # Reweighted, with no record of the analysis's own beside it.
            quantity = {"key": "mean", **_reweighted(name, corrected[name], None)}
        if quantity is None:
            # Analysed before a mean and its error were recorded: the mean of
            # every frame, said as such, and no error.
            values = _numeric_series(project_root / "analysis" / name / f"{name}.dat")
            if values:
                rows.append(MetricRow(
                    metric=f"{label} (biased ensemble)" if biased else label,
                    average=f"{_format_metric_value(_mean(values))}{f' {unit}' if unit else ''}"
                            " over all frames",
                    samples="—",
                    status="Not determined",
                    why=("Analysed before the mean's error was recorded; analyse the "
                         "study again for it."),
                ))
            continue
        suffix = (" (reweighted)" if quantity.get("reweighted")
                  else " (biased ensemble)" if biased else "")
        samples = quantity.get("samples")
        rows.append(MetricRow(
            metric=label + suffix,
            average=str(quantity.get("said") or "—"),
            samples="< 1" if samples == 0 else (f"{samples:,}" if isinstance(samples, int) else "—"),
            status="Determined" if quantity.get("determined") else "Not determined",
            why=str(quantity.get("why") or ""),
        ))

    if reweighted and reweighted.get("applies", True):
        effective = reweighted.get("effective_sample_size")
        try:
            frames = _format_metric_value(float(effective))
        except (TypeError, ValueError):
            frames = "—"
        rows.append(MetricRow(
            metric="Effective frames after reweighting",
            average=f"{frames} of {reweighted.get('n_frames')}",
            samples="—",
            status="",
        ))
    return rows


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _numeric_series(path: Path) -> list[float]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[list[float]] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        values = _numbers_in(text)
        if values:
            rows.append(values)
    if not rows:
        return []
    if all(len(row) == 1 for row in rows):
        return [row[0] for row in rows]
    return [row[-1] for row in rows if row]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def _format_metric_value(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:,.1f}"
    if abs(value) >= 10:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:,.3f}"
    return f"{value:,.4f}"


def _average_temperature(path: Path) -> float | None:
    if not path.is_file():
        return None
    values: list[float] = []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(_strip_comment_prefix(fh))
            for row in reader:
                raw = row.get("Temperature (K)")
                if raw in (None, "", "--"):
                    continue
                try:
                    values.append(float(raw))
                except ValueError:
                    continue
    except OSError:
        return None
    if not values:
        return None
    return sum(values) / len(values)


def _strip_comment_prefix(lines):
    for line in lines:
        yield line[1:] if line.startswith("#") else line


def _wall_time(project_root: Path, phases: list[dict[str, Any]]) -> str:
    from fastmdxplora.gui.simulated_time import phases_wall_seconds

    seconds = phases_wall_seconds(project_root, phases)
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _dashboard_summaries(project_root: Path) -> dict[str, DashboardAsset]:
    """Short captions for each analysis, keyed by figure title.

    Previously this rendered a second, restyled copy of every figure and
    returned the captions it computed along the way. The copies are no longer
    shown anywhere, so only the captions remain.
    """
    from fastmdxplora.report.reweighted import load_reweighted

    biased = load_reweighted(project_root)
    corrected = {item.get("analysis"): item
                 for item in ((biased or {}).get("quantities") or [])}
    summaries: dict[str, DashboardAsset] = {}
    for title, rel_data, kind in DASHBOARD_SUMMARY_SPECS:
        data_path = project_root / rel_data
        if not data_path.is_file():
            continue
        try:
            summary = (_biased_caption(data_path, corrected) if biased else None) \
                or (_what_the_analysis_found(data_path) if kind == "line" else None) \
                or _summarise_data_file(data_path, kind)
        except Exception as exc:  # noqa: BLE001 - a caption must never fail a report
            logger.debug("dashboard: no summary for %s: %s", title, exc)
            continue
        summaries[title] = DashboardAsset(rel_path=rel_data, summary=summary)
    return summaries



#: What each time series is measured in, for its caption.
#: As each analysis's own axis states it (`Analysis._recorded_unit`).
_UNITS = {"rmsd": "nm", "rmsf": "nm", "rg": "nm", "sasa": "nm\u00b2", "hbonds": "",
          "qvalue": "", "end_to_end": "nm", "pair_distance": "nm", "ligand_rmsd": "nm",
          "area_per_lipid": "nm\u00b2", "bilayer_thickness": "nm",
          "moments_of_inertia": "amu nm\u00b2", "coordination_number": ""}


def unit_of(name: str, found: dict[str, Any] | None = None) -> str:
    """What an analysis's mean is measured in: the unit it recorded beside
    the mean, or, for a study analysed before units were recorded, the one
    known for its name."""
    recorded = found.get("unit") if isinstance(found, dict) else None
    if isinstance(recorded, str):
        return recorded
    return _UNITS.get(name, "")


def _what_the_analysis_found(data_path: Path) -> str | None:
    """The mean the analysis determined, as its figure shows it.

    The caption was the mean of every row of the data file, equilibration
    included, beneath a figure giving the mean after equilibration with its
    error: an RMSD card read "avg 0.0157" under "mean after equilibration
    0.01297 \u00b1 0.0021 nm". The analysis records which frames it kept and
    what the mean is worth (`findings.mean` in its options.json); that is
    what is said here, and a series too short to measure says so.
    """
    try:
        record = json.loads((data_path.parent / "options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = (record.get("findings") or {}).get("mean") if isinstance(record, dict) else None
    if not isinstance(found, dict):
        return None
    mean = found.get("mean")
    unit = unit_of(data_path.parent.name, found)
    unit = f" {unit}" if unit else ""
    if not _finite(mean):
        return "no mean: too short to determine" if found.get("not_a_measurement") else None
    if found.get("not_a_measurement"):
        return f"mean {_format_metric_value(mean)}{unit}, too short to be determined"
    error = found.get("standard_error")
    text = (f"mean {_with_its_error(mean, error)}{unit}" if _finite(error) and error > 0
            else f"mean {_format_metric_value(mean)}{unit}")
    discard = found.get("discard")
    if isinstance(discard, int) and discard > 0:
        text += " after equilibration"
    samples = found.get("effective_samples")
    if _finite(samples):
        from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

        count = int(round(samples))
        text += f", {count} independent sample{'s' if count != 1 else ''}"
        if samples < MINIMUM_EFFECTIVE_SAMPLES:
            text += ", too few to determine the mean"
    return text


def _biased_caption(data_path: Path, corrected: dict[Any, Any]) -> str:
    """On a biased run the mean of a series is an average over the
    distribution the bias flattened. Where the analysis phase recovered the
    equilibrium value that is the one given; where it could not, no mean is."""
    name = data_path.parent.name
    item = corrected.get(name)
    unit = _UNITS.get(name, "")
    unit = f" {unit}" if unit else ""
    if item is not None and _finite(item.get("reweighted_mean")):
        return f"reweighted mean {_format_metric_value(item['reweighted_mean'])}{unit}"
    if name in _UNITS:
        return "biased ensemble: no unbiased mean"
    return ""


def _recorded_mean(project_root: Path, name: str) -> dict[str, Any] | None:
    """What an analysis recorded about its mean: the value after
    equilibration, or why the series cannot give one."""
    try:
        record = json.loads((project_root / "analysis" / name / "options.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = (record.get("findings") or {}).get("mean") if isinstance(record, dict) else None
    return found if isinstance(found, dict) else None


def _finite(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _with_its_error(mean: float, error: float) -> str:
    """A mean to the precision its error allows
    (:func:`fastmdxplora.statistics.with_its_error`)."""
    from fastmdxplora.statistics import with_its_error

    return with_its_error(mean, error)


def _summarise_data_file(data_path: Path, kind: str) -> str:
    """Describe a data file in one short phrase, without drawing anything.

    The panel captions ("avg 0.21 nm", "12 clusters") used to be produced as a
    side effect of rendering a second copy of each figure. Those copies are no
    longer displayed, so the caption is computed directly from the data.
    """
    if kind == "ss":
        _matrix, residues, frames = _secondary_structure_matrix(data_path)
        if not _matrix:
            raise StudyError("secondary structure data is empty", code="analysis.data.absent")
        return f"{len(frames)} frames, {len(residues)} residues"

    if kind == "dendrogram":
        import numpy as np

        linkage_matrix = np.load(data_path)
        if linkage_matrix.ndim != 2 or linkage_matrix.shape[1] != 4:
            raise StudyError("hierarchical linkage data must be an n x 4 matrix", code="analysis.data.absent")
        return f"{linkage_matrix.shape[0] + 1} frames"

    rows = _numeric_rows(data_path)
    if not rows:
        raise StudyError("numeric data is empty", code="analysis.data.absent")

    if kind == "scatter":
        if any(len(row) < 3 for row in rows):
            raise StudyError("projection data must include frame and two components", code="analysis.data.absent")
        return f"{len(rows)} frames"

    if kind in {"cluster", "cluster_counts"}:
        if any(len(row) < 2 for row in rows):
            raise StudyError("cluster data must include frame and cluster columns", code="analysis.data.absent")
        clusters = {int(row[1]) for row in rows}
        return f"{len(clusters)} clusters"

    if kind == "dihedrals":
        if any(len(row) < 4 for row in rows):
            raise StudyError("dihedral data must include frame, residue, phi, and psi", code="analysis.data.absent")
        return f"{len(rows)} angles"

    values = [row[0] for row in rows] if all(len(row) == 1 for row in rows) \
        else [row[-1] for row in rows]
    # Said for what it is: a plain mean, of every frame or every residue.
    # A time series whose analysis recorded its mean is captioned from that.
    over = "residues" if kind == "profile" else "all frames"
    unit = _UNITS.get(data_path.parent.name, "")
    return f"mean over {over} {_format_metric_value(_mean(values))}{' ' + unit if unit else ''}"









def _numeric_rows(path: Path) -> list[list[float]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[list[float]] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        values = _numbers_in(text)
        if values:
            rows.append(values)
    return rows


def _numbers_in(text: str) -> list[float]:
    """The numeric fields of one line, its text fields left out.

    A line holding any text was dropped whole, which kept a header out and
    also every row of a table that names a residue's chain -- so the RMSF of
    a structure with several chains read as no RMSF at all. A header has no
    numeric field, so it is still left out."""
    values: list[float] = []
    for part in text.replace(",", " ").split():
        try:
            values.append(float(part))
        except ValueError:
            continue
    return values


def _secondary_structure_matrix(path: Path) -> tuple[list[list[int]], list[str], list[str]]:
    code_map = {"C": 0, "H": 1, "E": 2, "B": 2, "G": 1, "I": 1, "T": 3, "S": 3}
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            residues = header[1:]
            matrix: list[list[int]] = []
            frames: list[str] = []
            for row in reader:
                if len(row) < 2:
                    continue
                frames.append(row[0])
                matrix.append([code_map.get(value, 0) for value in row[1:]])
    except (OSError, StopIteration):
        return [], [], []
    return matrix, residues, frames


def _analysis_sections(
    project_root: Path,
    output_dir: Path,
    dashboard_assets: dict[str, DashboardAsset],
) -> list[DashboardSection]:
    grouped: dict[str, list[DashboardPanel]] = {section: [] for section in SECTION_ORDER}
    seen_sources: set[str] = set()

    for source in _discover_analysis_images(project_root):
        rel = source.relative_to(project_root).as_posix()
        if rel in seen_sources:
            continue
        seen_sources.add(rel)
        folder = source.relative_to(project_root).parts[1]
        section = ANALYSIS_SECTION_BY_FOLDER.get(folder, "Other")
        title = _figure_title_from_path(source)
        # Show the figure the analysis wrote, in every surface. The report used
        # to display a restyled copy while the browser showed the original, so
        # the same panel looked different depending on where you opened it. The
        # analysis figures are also the publication-grade ones (6.5x4.2in,
        # 300 dpi, 9-11pt type), so they are the right choice for both.
        asset = _asset_for_title(title, dashboard_assets)
        display_rel = rel
        href = _href(source, output_dir)
        mode = "analysis figure"
        summary = asset.summary if asset is not None else ""
        grouped[section].append(
            DashboardPanel(
                title=title,
                source=display_rel,
                href=href,
                original_source=rel,
                original_href=_href(source, output_dir),
                mode=mode,
                summary=summary,
                category=section,
            )
        )

    for source in _discover_report_images(project_root):
        rel = source.relative_to(project_root).as_posix()
        if rel in seen_sources:
            continue
        seen_sources.add(rel)
        title = _figure_title_from_path(source)
        section = (
            "Region highlights"
            if "region" in source.stem.lower()
            else "Apo/holo comparison"
            if "apo" in source.stem.lower() or "holo" in source.stem.lower()
            else "Other"
        )
        grouped[section].append(
            DashboardPanel(
                title=title,
                source=rel,
                href=_href(source, output_dir),
                original_source=rel,
                original_href=_href(source, output_dir),
                mode="artifact fallback",
                summary="",
                category=section,
            )
        )

    grouped = _group_sparse_sections(grouped)
    sections: list[DashboardSection] = []
    for title in SECTION_ORDER:
        panels = sorted(grouped[title], key=lambda panel: _panel_sort_key(panel))
        if panels:
            sections.append(
                DashboardSection(
                    title=title,
                    anchor=SECTION_ANCHORS[title],
                    panels=panels,
                    theme=SECTION_THEME.get(title, "Other"),
                )
            )
    return sections


def _group_sparse_sections(
    grouped: dict[str, list[DashboardPanel]],
) -> dict[str, list[DashboardPanel]]:
    """Return the grouping unchanged.

    Sections used to be merged when they held three figures or fewer, which
    meant an analysis appeared under its own name or under a catch-all
    depending on how many plots it produced. Each analysis now keeps its own
    section regardless of size.
    """
    return {section: list(panels) for section, panels in grouped.items()}


def _discover_analysis_images(project_root: Path) -> list[Path]:
    analysis_dir = project_root / "analysis"
    if not analysis_dir.is_dir():
        return []
    suffixes = {".png", ".jpg", ".jpeg", ".svg"}
    found = [
        path
        for path in analysis_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes
    ]
    # Analyses write the same figure as both PNG and SVG. They carry the same
    # title, so listing both would show every figure twice; prefer the raster
    # for display and let the SVG be reached through the download links.
    raster = {".png", ".jpg", ".jpeg"}
    by_stem: dict[tuple[Path, str], Path] = {}
    for path in sorted(found):
        key = (path.parent, path.stem)
        current = by_stem.get(key)
        if current is None or (
            current.suffix.lower() not in raster and path.suffix.lower() in raster
        ):
            by_stem[key] = path
    return sorted(by_stem.values())


def _discover_report_images(project_root: Path) -> list[Path]:
    report_dir = project_root / "report"
    if not report_dir.is_dir():
        return []
    names = {
        "region_highlight_summary.png",
        "structure_region_highlights.png",
        "apo_holo_comparison.png",
    }
    return sorted(path for path in report_dir.iterdir() if path.name in names)


def _asset_for_title(
    title: str,
    dashboard_assets: dict[str, DashboardAsset],
) -> DashboardAsset | None:
    normalized = _normalize_label(title)
    for asset_title, aliases in DASHBOARD_ASSET_TITLE_ALIASES.items():
        if _normalize_label(asset_title) == normalized:
            return dashboard_assets.get(asset_title)
        if any(_normalize_label(alias) == normalized for alias in aliases):
            return dashboard_assets.get(asset_title)
    return None


def _figure_title_from_path(path: Path) -> str:
    stem = path.stem
    folder = path.parent.name
    special = {
        "rmsd": "RMSD",
        "rmsf": "RMSF",
        "rg": "Radius of gyration",
        "hbonds": "Hydrogen bonds",
        "ss": "Secondary structure",
        "sasa": "Total SASA",
        "sasa_heatmap": "Per-residue SASA heatmap",
        "sasa_by_residue": "Average per-residue SASA",
        "total_sasa": "Total SASA",
        "residue_sasa": "Per-residue SASA heatmap",
        "average_residue_sasa": "Average per-residue SASA",
        "dimred_pca": "PCA",
        "dimred_mds": "MDS",
        "dimred_tsne": "t-SNE",
        "cluster_kmeans": "KMeans trajectory scatter",
        "cluster_kmeans_counts": "KMeans population plot",
        "cluster_dbscan": "DBSCAN trajectory scatter",
        "cluster_dbscan_counts": "DBSCAN population plot",
        "cluster_hierarchical": "Hierarchical trajectory scatter",
        "cluster_hierarchical_counts": "Hierarchical population plot",
        "cluster_hierarchical_dendrogram": "Hierarchical dendrogram",
        "dbscan_pop": "DBSCAN population plot",
        "dbscan_traj_hist": "DBSCAN trajectory histogram",
        "dbscan_traj_scatter": "DBSCAN trajectory scatter",
        "dbscan_distance_matrix": "DBSCAN distance matrix",
        "kmeans_pop": "KMeans population plot",
        "kmeans_traj_hist": "KMeans trajectory histogram",
        "kmeans_traj_scatter": "KMeans trajectory scatter",
        "hierarchical_pop": "Hierarchical population plot",
        "hierarchical_traj_hist": "Hierarchical trajectory histogram",
        "hierarchical_traj_scatter": "Hierarchical trajectory scatter",
        "hierarchical_dendrogram": "Hierarchical dendrogram",
        "region_highlight_summary": "Region highlights",
        "structure_region_highlights": "Structure region highlights",
        "apo_holo_comparison": "Apo/holo comparison",
        "qvalue": "Fraction of native contacts",
        "dihedrals": "Dihedrals",
    }
    if stem in special:
        return special[stem]
    if folder == "dimred" and stem.startswith("dimred_"):
        return _friendly_name(stem.replace("dimred_", ""))
    if folder == "cluster":
        return _friendly_name(stem)
    return _friendly_name(stem)


def _friendly_name(value: str) -> str:
    words = value.replace("-", "_").split("_")
    replacements = {
        "rmsd": "RMSD",
        "rmsf": "RMSF",
        "rg": "Rg",
        "sasa": "SASA",
        "pca": "PCA",
        "mds": "MDS",
        "tsne": "t-SNE",
        "dbscan": "DBSCAN",
        "kmeans": "KMeans",
        "ss": "SS",
    }
    return " ".join(replacements.get(word.lower(), word.title()) for word in words)


def _panel_sort_key(panel: DashboardPanel) -> tuple[int, str]:
    source = panel.original_source
    priorities = (
        "rmsd.png",
        "rmsf.png",
        "rg.png",
        "hbonds.png",
        "sasa.png",
        "total_sasa.png",
        "sasa_heatmap.png",
        "residue_sasa.png",
        "sasa_by_residue.png",
        "average_residue_sasa.png",
        "ss.png",
        "dimred_pca.png",
        "dimred_mds.png",
        "dimred_tsne.png",
        "dbscan",
        "kmeans",
        "hierarchical",
    )
    for index, pattern in enumerate(priorities):
        if pattern in source:
            return index, source
    return len(priorities), source


def _normalize_label(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _artifact_links(
    project_root: Path,
    output_dir: Path,
    *,
    sections: list[DashboardSection],
    include_bundle_link: bool,
) -> list[DashboardLink]:
    candidates: list[tuple[str, str, str]] = [
        ("Dashboard HTML", "report/dashboard.html", "this file"),
        ("Markdown report", "report/report.md", "written report"),
        ("Slide deck", "report/slides.pptx", "presentation"),
        ("Project bundle", "report/project_bundle.zip", "shareable archive"),
        ("Run manifest", "manifest.json", "project provenance"),
        ("Analysis manifest", "analysis/analysis_manifest.json", "analysis provenance"),
        ("Analysis summary", "report/analysis_summary.png", "combined figure"),
        ("Analysis summary manifest", "report/analysis_summary_manifest.json", "figure provenance"),
        ("Region highlight manifest", "report/region_highlight_manifest.json", "region provenance"),
        ("Region highlight figure", "report/region_highlight_summary.png", "annotated RMSF"),
    ]
    for section in sections:
        for panel in section.panels:
            candidates.append((panel.title, panel.original_source, section.title))

    links: list[DashboardLink] = []
    seen: set[str] = set()
    for label, rel, detail in candidates:
        path = project_root / rel
        if rel in seen:
            continue
        future_current_run_artifact = rel == "report/dashboard.html" or (
            include_bundle_link and rel == "report/project_bundle.zip"
        )
        if not path.is_file() and not future_current_run_artifact:
            continue
        seen.add(rel)
        links.append(DashboardLink(label, _href(path, output_dir), detail))
    return links


def _href(path: Path, output_dir: Path) -> str:
    rel = os.path.relpath(path, output_dir).replace(os.sep, "/")
    return quote(rel, safe="/._-#")


def _format_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.3g}"






def analysis_sections_for(project_root: Path) -> list[DashboardSection]:
    """Categorised analysis panels for a completed or in-progress run.

    Uses the curated charts when the report phase has produced them and the
    per-analysis figures otherwise, so the browser shows the same grouping
    the generated report uses.
    """
    return _analysis_sections(
        project_root, project_root, _dashboard_summaries(project_root)
    )


def quick_actions_for(project_root: Path, sections: list[DashboardSection]) -> list[DashboardLink]:
    """The report's quick-action links, relative to the run directory."""
    links = _artifact_links(
        project_root, project_root, sections=sections, include_bundle_link=True
    )
    return _quick_action_links(links)


def _theme_tokens() -> str:
    """Return the shared design tokens for inlining into a static report.

    The GUI links ``gui/static/theme.css``; a generated report has to stand
    alone as a single file, so the same tokens are inlined here. Reading the
    file rather than duplicating it keeps the two surfaces from drifting
    apart.
    """
    theme = Path(__file__).resolve().parent / "static" / "theme.css"
    try:
        tokens = theme.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ":root { color-scheme: dark; }"
    return _FONT_FILE.sub(_font_inlined, tokens)


#: A font the stylesheet names by the GUI's address, which a page opened
#: from a file cannot reach.
_FONT_FILE = re.compile(r'url\("/static/fonts/([a-z0-9-]+\.woff2)(?:\?[^"]*)?"\)')


def _font_inlined(found: "re.Match[str]") -> str:
    """The font itself, so the page reads in the GUI's type away from it;
    the address left as it was if the file is missing, which the browser
    passes over to the next font of the stack."""
    import base64

    try:
        data = (Path(__file__).resolve().parent / "static" / "fonts" / found.group(1)).read_bytes()
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return found.group(0)
    return 'url("data:font/woff2;base64,' + base64.b64encode(data).decode("ascii") + '")'


def _lab_logo_uri() -> str:
    """The lab's logo the GUI shows as its avatar (``static/lab-logo.png``),
    inlined so the page stands alone; empty if the file is missing."""
    import base64

    from fastmdxplora.gui.server import LAB_LOGO

    try:
        data = (Path(__file__).resolve().parent / "static" / LAB_LOGO).read_bytes()
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _product_mark_uri() -> str:
    """FastMDXplora's mark as the tab's icon (``static/fastmdx-mark.svg``),
    inlined so the page stands alone; empty if the file is missing."""
    import base64

    from fastmdxplora.gui.server import PRODUCT_MARK

    try:
        data = (Path(__file__).resolve().parent / "static" / PRODUCT_MARK).read_bytes()
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")


def _files_script() -> str:
    """The GUI's Files page script (``static/files-page.js``): finding,
    filtering, sorting, folding and a file's menu, as there."""
    try:
        found = (Path(__file__).resolve().parent / "static" / "files-page.js").read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""
    return f"<script>{found}</script>\n"


def _tooltips_script() -> str:
    """The GUI's tooltips (``static/tooltips.js``), so a hint here is shown
    as it is there; nothing if the file is missing."""
    try:
        found = (Path(__file__).resolve().parent / "static" / "tooltips.js").read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""
    return f"<script>{found}</script>\n"


def _gui_stylesheet() -> str:
    """The GUI's own stylesheet, for inlining after the tokens.

    The page is laid out as the GUI is, in the GUI's classes, so it is
    styled by the GUI's own rules rather than by a second set written to
    look like them: the two looked different for as long as there were two.
    """
    sheet = Path(__file__).resolve().parent / "static" / "dashboard.css"
    try:
        return sheet.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""


DASH = "\u2014"

#: The GUI's Progress list, in its order and with its words.
STAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("setup", "Setup"),
    ("minimization", "Minimisation"),
    ("nvt", "NVT"),
    ("npt", "NPT"),
    ("production", "Production"),
    ("analysis", "Analysis"),
    ("report", "Report"),
)

#: The pages, in the sidebar's order.
PAGES: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("analysis", "Analysis"),
    ("report", "Report"),
    ("files", "Files"),
)


@dataclass(frozen=True)
class StageStep:
    stage: str
    label: str
    state: str
    hidden: bool = False


def _normalise_stage(value: object) -> str:
    """A stage as the GUI names it (``normaliseStage`` in dashboard.js)."""
    stage = str(value or "").lower()
    for part, name in (("minim", "minimization"), ("nvt", "nvt"), ("npt", "npt"),
                       ("production", "production"), ("analysis", "analysis"),
                       ("report", "report")):
        if part in stage:
            return name
    if "setup" in stage or "loading" in stage:
        return "setup"
    return stage


def _phase_state(value: object) -> str:
    """A recorded state as the GUI paints it (``phaseVisualState``)."""
    status = str(value or "").lower()
    if status in {"ok", "complete", "completed", "success", "succeeded"}:
        return "completed"
    if status in {"error", "failed"}:
        return "failed"
    if status in {"skipped", "not run"}:
        return "skipped"
    if status in {"running", "active", "current"}:
        return "current"
    return "waiting"


def _stage_steps(
    manifest: dict[str, Any],
    live_status: dict[str, Any] | None,
    reachable: list[str] | None,
) -> list[StageStep]:
    """The sidebar's Progress list, by the rule the GUI paints it with
    (``renderStageTimeline`` in dashboard.js): the live record's state of
    each stage, the manifest's where the live record says nothing, and the
    stages before the one in progress done. Stages this run cannot reach are
    left out, as the GUI leaves them out."""
    phase_map = {
        str(p.get("name") or "").lower(): str(p.get("status") or "").lower()
        for p in manifest.get("phases", []) if isinstance(p, dict)
    }
    live = live_status or {}
    live_states = live.get("stage_states") if isinstance(live.get("stage_states"), dict) else {}
    order = [stage for stage, _ in STAGE_LABELS]
    current = _normalise_stage(live.get("stage"))
    current_index = order.index(current) if current in order else -1
    simulation_done = phase_map.get("simulation") in {"ok", "complete", "completed", "success"}
    steps: list[StageStep] = []
    for index, (stage, label) in enumerate(STAGE_LABELS):
        if reachable and stage not in reachable:
            steps.append(StageStep(stage, label, "waiting", hidden=True))
            continue
        state = _phase_state(live_states.get(stage))
        if state == "waiting":
            if stage == "setup":
                state = _phase_state(phase_map.get("setup"))
            if stage in {"minimization", "nvt", "npt", "production"} and simulation_done:
                state = "completed"
            if stage in {"analysis", "report"}:
                state = _phase_state(phase_map.get(stage))
        if current_index >= 0 and state == "waiting":
            if index < current_index:
                state = "completed"
            elif index == current_index:
                state = "current"
        if stage == current and _phase_state(live_states.get(stage)) == "current":
            state = "current"
        steps.append(StageStep(stage, label, state))
    return steps


def _study_state(manifest: dict[str, Any], live_status: dict[str, Any] | None) -> tuple[str, str]:
    """What the sidebar's dot and word say: the manifest's verdict, or,
    before it is written, the live record's."""
    recorded = _project_status(manifest)
    if manifest.get("phases"):
        if recorded == "ok":
            return "completed", "completed"
        if recorded == "error":
            return "failed", "error"
        return "recorded", "stale"
    status = str((live_status or {}).get("status") or "").lower()
    if status in {"failed", "error"}:
        return "failed", "error"
    if status in {"completed", "complete", "ok"}:
        return "completed", "completed"
    if status:
        return status, "waiting"
    return "not run", "stale"


def _files_page(
    project_root: Path,
    output_dir: Path,
    *,
    include_bundle_link: bool,
    not_produced: list[tuple[str, str]] | None = None,
) -> str:
    """The GUI's Files page, rendered as the GUI renders it
    (``files_page.render``) from the GUI's own listing
    (``server._artifact_records``), so the two pages offer the same files
    under the same names, laid out the same way; its links are made
    relative to this page, and what needs the server (a zip, a deposit,
    the side panel) is left out.

    This page and the bundle are written after the listing is read, so they
    are added by name. The scratch, what was set aside and deposits are not
    listed: this page travels in the bundle, which leaves them out.
    """
    from fastmdxplora.gui import server
    from fastmdxplora.gui.files_page import Links, files_model, render

    from fastmdxplora.study_files import place

    # Not what the bundle and a deposit leave out: in either, this page
    # goes with them, and their rows would be links to nothing.
    records = [dict(record) for record in server._artifact_records(project_root)
               if place(record["path"])[0] not in ("scratch", "previous", "deposit")]
    have = {record["path"] for record in records}
    later = [output_dir / "dashboard.html"]
    if include_bundle_link:
        later.append(output_dir / "project_bundle.zip")
    for path in later:
        try:
            rel = path.relative_to(project_root).as_posix()
        except ValueError:
            continue
        if rel in have:
            continue
        records.append({"path": rel, "size": None, "mtime": None})
    records.sort(key=lambda record: record["path"])
    for record in records:
        record["absolute_path"] = (project_root / record["path"]).as_posix()
    # What the report could not produce is said by the report phase as it
    # writes, not read from a record an earlier run may have left.
    model = files_model(project_root, records,
                        values=server._determined_values(project_root, records),
                        not_produced=[{"artifact": artifact, "reason": reason}
                                      for artifact, reason in not_produced or []])
    links = Links(standalone=True,
                  here=lambda rel: _href(project_root / rel, output_dir))
    return render(model, links, view="both")


def _report_for_page(
    project_root: Path,
    output_dir: Path,
    *,
    include_bundle_link: bool,
    not_produced: list[tuple[str, str]] | None,
) -> dict[str, Any]:
    """The Report page's document and downloads, by the GUI's own
    ``report_payload``, its links made relative to this page.

    What the report phase could not produce is said only when the phase
    hands it over: ``not_produced.json`` is written after this page, and one
    left by an earlier run may no longer be true.
    """
    from fastmdxplora.gui.report_page import report_payload

    try:
        payload = report_payload(project_root)
    except Exception as exc:  # noqa: BLE001 - the page stands without its report
        logger.debug("dashboard: no report for the page: %s", exc)
        return {"ok": False}
    if not payload.get("ok") or "downloads" not in payload:
        return {"ok": False}
    downloads: dict[str, str] = {}
    for key, address in (payload.get("downloads") or {}).items():
        rel = str(address).split("?", 1)[0]
        if rel.startswith("/artifacts/"):
            downloads[key] = _href(project_root / rel[len("/artifacts/"):], output_dir)
    if include_bundle_link and "bundle" not in downloads:
        downloads["bundle"] = _href(output_dir / "project_bundle.zip", output_dir)
    return {
        "ok": True,
        "html": str(payload.get("html") or ""),
        "generated": str(payload.get("generated") or ""),
        "downloads": downloads,
        "not_produced": [(str(name), str(why)) for name, why in (not_produced or [])],
    }


def _methods_for_page(project_root: Path) -> tuple[str, str]:
    """The methods paragraphs as the Overview's Methods card gives them."""
    from fastmdxplora.gui.report_page import methods_payload

    try:
        said = methods_payload(project_root)
    except Exception as exc:  # noqa: BLE001 - the page stands without them
        logger.debug("dashboard: no methods for the page: %s", exc)
        return "", ""
    if not said.get("ok"):
        return "", ""
    return str(said.get("html") or ""), str(said.get("plain") or "")


#: An analysis's own figure, ``analysis/rmsd/rmsd.png``: the one the GUI
#: also plots from its numbers (``renderAnalysisSections``).
_OWN_FIGURE = re.compile(r"(?:^|/)analysis/([a-z][a-z0-9_]*)/\1\.png$")


def _series_for_page(project_root: Path, sections: list[DashboardSection]) -> dict[str, Any]:
    """Each plotted series as the GUI's ``/api/series`` gives it, for the
    GUI's own chart script to plot on this page as it does there."""
    from fastmdxplora.gui.series import series_payload

    found: dict[str, Any] = {}
    for section in sections:
        for panel in section.panels:
            own = _OWN_FIGURE.search(panel.original_source)
            if not own or own.group(1) in found:
                continue
            try:
                payload = series_payload(project_root, own.group(1))
                # Checked here: a value JSON cannot hold leaves the figure.
                json.dumps(payload, allow_nan=False)
            except Exception as exc:  # noqa: BLE001 - the figure stands without its chart
                logger.debug("dashboard: no series for %s: %s", own.group(1), exc)
                continue
            if payload.get("ok"):
                found[own.group(1)] = payload
    return found


def _render_dashboard(
    *,
    title: str,
    system: str,
    status: str,
    generated_at: datetime,
    phase_notice: str,
    cards: list[DashboardCard],
    sections: list[DashboardSection],
    phase_rows: list[PhaseRow],
    metrics: list[MetricRow],
    output_folder: str,
    live_html: str,
    stages: list[StageStep] | None = None,
    study_state: tuple[str, str] | None = None,
    platform: str = "",
    report: dict[str, Any] | None = None,
    methods: tuple[str, str] = ("", ""),
    files_html: str = "",
    series: dict[str, Any] | None = None,
) -> str:
    """The page, laid out as the GUI is: its sidebar, and its Overview,
    Analysis, Report and Files pages, with the Cite page its settings menu
    opens. What the GUI does live (the Viewer, the Agent, the Config builder,
    live charts) needs the server, and stays in ``fastmdx gui``."""
    from fastmdxplora import (
        __bibtex__,
        __citation__,
        __copyright__,
        __doi__,
        __expansion__,
        __version__,
    )

    generated = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    word, dot = study_state or ((status, "completed") if status == "ok" else (status, "stale"))
    stage_steps = stages if stages is not None else _stage_steps({}, None, None)
    system_label = _system_label(system) if system else "study"

    pages = "\n".join((
        _render_overview(
            title=title, generated=generated, phase_notice=phase_notice, cards=cards,
            phase_rows=phase_rows, metrics=metrics, live_html=live_html, methods=methods,
            has_report=bool(report and report.get("ok"))),
        _render_analysis_page(sections, series or {}),
        _render_report_page(report or {"ok": False}),
        _render_files_page(files_html),
    ))
    mark = _product_mark_uri()
    icon = f'<link rel="icon" type="image/svg+xml" href="{mark}">\n' if mark else ""
    sidebar = _render_sidebar(
        title=title, system_label=system_label, word=word, dot=dot, platform=platform,
        output_folder=output_folder, stages=stage_steps, cards=cards,
        generated=generated, generated_epoch=f"{generated_at.timestamp():.0f}",
        expansion=__expansion__, logo=_lab_logo_uri())
    settings = (_render_settings(__version__) + "\n"
                + _render_cite_dialog(__citation__, __doi__, __version__, __bibtex__,
                                      __copyright__, __expansion__))

    return "".join((
        "<!doctype html>\n<html lang=\"en\" data-page=\"overview\">\n<head>\n",
        "<meta charset=\"utf-8\">\n",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n",
        "<meta name=\"color-scheme\" content=\"dark light\">\n",
        f"<title>{escape(title)} - FastMDXplora</title>\n", icon,
        f"<script>{_THEME_FIRST_JS}</script>\n",
        "<style>\n", _theme_tokens(), "\n", _gui_stylesheet(), "\n",
        _STATIC_ONLY_CSS, "\n</style>\n</head>\n",
        "<body class=\"panel-collapsed static-dashboard\">\n",
        settings, "\n",
        "<div class=\"app-shell\">\n",
        "<aside class=\"sidebar\" aria-label=\"Dashboard navigation\">\n", sidebar,
        "\n</aside>\n",
        "<div class=\"col-handle\" aria-hidden=\"true\"></div>\n",
        "<div class=\"main\">\n<main class=\"page-shell\" role=\"main\">\n", pages,
        "\n</main>\n</div>\n</div>\n",
        _render_series_scripts(series or {}),
        f"<script>{_PAGE_JS}</script>\n{_files_script()}{_tooltips_script()}</body>\n</html>\n",
    ))


def _render_sidebar(
    *,
    title: str,
    system_label: str,
    word: str,
    dot: str,
    platform: str,
    output_folder: str,
    stages: list[StageStep],
    cards: list[DashboardCard],
    generated: str,
    generated_epoch: str,
    expansion: str,
    logo: str = "",
) -> str:
    """The GUI's sidebar, as a page with no server can have it: the study as
    its card (no studies to switch to), its pages, where a run stood when it
    stopped short, and what the page was written from."""
    from fastmdxplora.gui.sidebar_icons import icon

    by_label = {card.label: card for card in cards}
    facts = [("Written", f'<span data-when="{escape(generated_epoch)}">{escape(generated)}</span>')]
    for label in ("Production", "Wall time"):
        card = by_label.get(label)
        if card is not None:
            facts.append((label, escape(card.value)))
    current = ' aria-current="page"'
    nav = "\n".join(
        f'<a href="#{key}" class="nav-link{" active" if key == "overview" else ""}" '
        f'data-view-link="{key}"{current if key == "overview" else ""}>'
        f'{icon(key)}<span>{label}</span></a>'
        for key, label in PAGES)
    steps = "\n".join(
        f'<li class="stage-step" data-stage="{escape(step.stage)}" '
        f'data-state="{escape(step.state)}"{" hidden" if step.hidden else ""}>'
        f'<span class="stage-marker"></span><span class="stage-label">{escape(step.label)}</span></li>'
        for step in stages)
    mark = f'<img class="sidebar-account-logo" src="{logo}" alt="">' if logo else ""
    metrics = "\n".join(
        f'<span class="metric-label">{escape(label)}</span>'
        f'<span class="metric-value mono">{value}</span>' for label, value in facts)
    # The progress card as the GUI shows it: for a run that was going on,
    # stopped short or failed when the page was written; none once it
    # finished.
    state = str(word or "").lower()
    run = ("failed" if state in {"failed", "error"} else
           "stopped" if state == "stopped" else
           "running" if state in {"running", "starting", "paused"} else "")
    shown = [step for step in stages if not step.hidden]
    at = next((k for k, step in enumerate(shown) if step.state in {"current", "failed"}), -1)
    stage = shown[at].label if at >= 0 else ""
    heading = (f"{stage} failed" if run == "failed" and stage else
               f"{stage} stopped" if run == "stopped" and stage else stage or "Stopped")
    count = f"Stage {at + 1} of {len(shown)}" if at >= 0 else ""
    said = {"completed": "Completed", "failed": "Failed", "recorded": "Recorded",
            "not run": "Not run"}.get(state, state[:1].upper() + state[1:])
    platform_line = (f'<span class="status-divider" aria-hidden="true">&middot;</span>'
                     f'<span class="status-platform" title="Platform">{escape(platform)}</span>'
                     if platform else "")
    return f"""<div class="sidebar-brand">
  <div class="brand-text">
    <div class="brand-product" title="{escape(expansion)}">FastMDXplora</div>
  </div>
</div>
<div class="sidebar-study">
  <div class="sidebar-study-card" title="{escape(system_label)}">
    <span class="study-card-body">
      <span class="study-kicker">Study</span>
      <span class="study-name" title="{escape(title)}">{escape(title)}</span>
      <span class="study-status" role="status">
        <span class="status-dot status-dot-{escape(dot)}"></span>
        <span class="status-text">{escape(said)}</span>{platform_line}
        <span class="status-divider" aria-hidden="true">&middot;</span>
        <span title="Written by the report phase; it does not update">Snapshot</span>
      </span>
    </span>
  </div>
</div>
<nav class="sidebar-nav" role="navigation" aria-label="Dashboard sections">
  <div class="nav-heading">This study</div>
  {nav}
</nav>
<div class="sidebar-snapshot">
  <div class="sidebar-metrics">
  {metrics}
  </div>
  <button class="ghost-btn" type="button" data-copy-text="{escape(output_folder)}" title="Copy the output folder's path">Copy the folder's path</button>
</div>
<div class="sidebar-progress" data-run="{run}">
  <div class="progress-head"><span class="progress-stage">{escape(heading)}</span></div>
  <ol class="sidebar-stages" aria-label="Stages">
  {steps}
  </ol>
  <div class="sidebar-metrics"><span class="metric-label">{escape(count)}</span></div>
</div>
<div class="sidebar-foot">
  <button type="button" class="sidebar-account" id="settings-open" aria-haspopup="dialog" aria-expanded="false" title="Settings">
    <span class="sidebar-account-avatar" aria-hidden="true">{mark}</span>
    <span class="sidebar-account-text"><span class="sidebar-account-name">FastMDXplora</span></span>
    {icon("gear", "sidebar-account-gear")}
  </button>
</div>"""


def _render_settings(version: str) -> str:
    """The settings menu at the foot of the sidebar, with what a page with
    no server can offer: the scheme, the citation and the links."""
    return f"""<div class="settings-popup" id="settings-popup" hidden role="dialog" aria-label="Settings">
  <div class="settings-section">Appearance</div>
  <div class="settings-row">
    <span>Theme</span>
    <div class="seg" role="group" aria-label="Theme">
      <button type="button" class="seg-btn active" data-theme="system" aria-pressed="true" title="As the computer is set, light or dark">System</button>
      <button type="button" class="seg-btn" data-theme="light" aria-pressed="false">Light</button>
      <button type="button" class="seg-btn" data-theme="dark" aria-pressed="false">Dark</button>
    </div>
  </div>
  <div class="settings-divider"></div>
  <button type="button" class="settings-item" data-dialog-open="cite-dialog" aria-haspopup="dialog">Cite FastMDXplora&hellip; <span class="mono settings-hint">{escape(version)}</span></button>
  <a href="https://fastmdxplora.readthedocs.io/en/latest/gui.html" class="settings-item" target="_blank" rel="noopener">Documentation <span class="mono settings-hint">&#8599;</span></a>
  <a href="https://github.com/aai-research-lab/FastMDXplora" class="settings-item" target="_blank" rel="noopener">GitHub <span class="mono settings-hint">&#8599;</span></a>
</div>"""


def _page_header(title: str, subtitle: str, actions: str = "") -> str:
    return (
        '<div class="page-header"><div>'
        f'<h1 class="page-title">{escape(title)}</h1>'
        f'<div class="page-subtitle">{escape(subtitle)}</div>'
        f'</div><div class="page-header-actions">{actions}</div></div>'
    )


def _render_overview(
    *,
    title: str,
    generated: str,
    phase_notice: str,
    cards: list[DashboardCard],
    phase_rows: list[PhaseRow],
    metrics: list[MetricRow],
    live_html: str,
    methods: tuple[str, str],
    has_report: bool,
) -> str:
    from fastmdxplora.gui.sidebar_icons import icon

    actions = ('<a href="#analysis" class="ghost-btn" data-view-link="analysis">Analysis</a>'
               + ('<a href="#report" class="ghost-btn" data-view-link="report">Report</a>'
                  if has_report else ""))
    parts = [
        '<section class="page" data-page="overview" id="dashboard">',
        _page_header("Study Overview",
                     f"{title}, as recorded when this page was written ({generated}).", actions),
    ]
    if phase_notice:
        parts.append(
            '<div class="card"><div class="card-header">'
            '<h2 class="card-title">Existing trajectory analysis</h2></div>'
            f'<div class="card-body">{escape(phase_notice)}</div></div>')
    parts.append('<div id="live-panels">')
    if live_html:
        parts.append(live_html)
    if cards:
        parts.append('<div class="grid overview-summary metric-cards" id="overview-summary-cards">'
                     + "".join(_render_card(card) for card in cards) + "</div>")
    if phase_rows:
        parts.append(
            '<div class="card" id="overview-phase-card"><div class="card-header">'
            '<h2 class="card-title">Phases</h2></div><table class="phase-table">'
            "<thead><tr><th>Phase</th><th>Status</th><th>Detail</th></tr></thead>"
            '<tbody id="overview-phase-rows">'
            + "".join(_render_phase_row(row) for row in phase_rows) + "</tbody></table></div>")
    parts.append(
        '<div class="card" id="overview-stats-card"><div class="card-header">'
        '<h2 class="card-title">What the analyses determined</h2></div><table class="phase-table">'
        "<thead><tr><th>Analysis</th><th>Mean ± standard error</th>"
        "<th>Independent samples</th><th>Status</th></tr></thead>"
        '<tbody id="overview-stat-rows">'
        + ("".join(_render_metric_row(row) for row in metrics) or
           # What is absent, not the fact of absence: the table is filled
           # from the analysis outputs, so empty means none yet.
           '<tr><td colspan="4" class="muted">No analysis outputs to summarise yet.</td></tr>')
        + "</tbody></table></div>")
    methods_html, methods_plain = methods
    if methods_html:
        parts.append(
            '<div class="card" id="overview-methods-card"><div class="card-header">'
            '<h2 class="card-title">Methods</h2>'
            '<button class="line-btn" type="button" data-copy-from="overview-methods-plain" '
            f'aria-label="Copy the methods" title="Copy the methods, as plain text">{icon("copy", "line-icon")}'
            "</button></div>"
            f'<div class="card-body methods-text" id="overview-methods-text">{methods_html}</div>'
            f'<pre id="overview-methods-plain" hidden>{escape(methods_plain)}</pre></div>')
    parts.append("</div></section>")
    return "\n".join(parts)


def _render_series_scripts(series: dict[str, Any]) -> str:
    """The series, and the GUI's chart script reading them as it reads
    ``/api/series``: its requests for a series answered from the page, so the
    script is the GUI's own, unchanged."""
    if not series:
        return ""
    script = Path(__file__).resolve().parent / "static" / "series-chart.js"
    try:
        chart = script.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ""
    data = json.dumps(series, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    return (f'<script type="application/json" id="fmx-series">{data}</script>\n'
            f"<script>{_SERIES_FROM_THE_PAGE_JS}</script>\n<script>{chart}</script>\n")


def _render_analysis_page(sections: list[DashboardSection], series: dict[str, Any]) -> str:
    count = sum(len(section.panels) for section in sections)
    meta = (f'<div class="muted small mono" id="analysis-meta">'
            f'{count} figure{"" if count == 1 else "s"}</div>' if count
            else '<div class="muted small mono" id="analysis-meta">no analyses yet</div>')
    charted: set[str] = set()
    body = "\n".join(_render_section(section, charted, series) for section in sections) or (
        '<div class="empty-state" id="analysis-empty">'
        '<div class="empty-title">No analysis figures in this study</div>'
        '<div class="empty-detail muted">The analysis phase writes them; '
        "this page was written without any.</div></div>")
    return ('<section class="page" data-page="analysis" hidden>'
            + _page_header("Analysis", "Trajectory analyses and protein-ligand interactions", meta)
            + f'<div id="analysis-sections">{body}</div></section>')


_DOWNLOAD_LABELS: tuple[tuple[str, str], ...] = (
    ("pdf", "PDF"), ("slides", "Slides"), ("bundle", "Bundle"),
    ("markdown", "Markdown"), ("summary", "Summary figure"),
)


def _render_report_page(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return ('<section class="page" data-page="report" hidden>'
                + _page_header("Report", "The study, written up.")
                + '<div class="empty-state" id="report-empty">'
                "<p>No report yet. The report phase writes one at the end of a run.</p>"
                "</div></section>")
    downloads = report.get("downloads") or {}
    actions = "".join(
        f'<a class="{"primary-btn" if key == "pdf" else "ghost-btn"}" '
        f'href="{escape(downloads[key])}" download>{escape(label)}</a>'
        for key, label in _DOWNLOAD_LABELS if downloads.get(key))
    notices = "".join(
        '<div class="report-notice"><span class="report-notice-kind mono">'
        f"not produced · {escape(name)}</span>{escape(why)}</div>"
        for name, why in report.get("not_produced") or [])
    subtitle = (f"Generated {report['generated']}" if report.get("generated")
                else "The study, written up.")
    return ('<section class="page" data-page="report" hidden>'
            + _page_header("Report", subtitle, actions)
            + (f'<div class="report-notices" id="report-notices">{notices}</div>' if notices else "")
            + f'<article class="card report-document" id="report-document">{report.get("html", "")}'
            "</article></section>")


def _render_files_page(files_html: str) -> str:
    body = files_html or '<div class="files-empty">This study has not written any files yet.</div>'
    return ('<section class="page" data-page="files" hidden>'
            + _page_header("Files", "Everything the study wrote, in the order it ran")
            + f'<div id="files-page" class="files-page">{body}</div></section>')


def _render_cite_dialog(citation: str, doi: str, version: str, bibtex: str,
                        copyright_: str, expansion: str) -> str:
    """The citation as the GUI gives it: a dialog over the page, the
    reference and its BibTeX each copied in one click. The report, the
    slides and the GUI all say it, from the same constants."""
    from fastmdxplora.gui.sidebar_icons import icon

    return f"""<div id="cite-dialog" class="agent-dialog" hidden>
  <div class="agent-dialog-panel dialog-narrow" role="dialog" aria-modal="true" aria-labelledby="cite-title">
    <div class="agent-dialog-head">
      <div>
        <div class="builder-label" id="cite-title">Cite FastMDXplora</div>
        <div class="builder-card-note">If this software contributed to your work, please cite it.</div>
      </div>
      <button type="button" class="line-btn dialog-x" data-dialog-close aria-label="Close" title="Close">{icon("close", "line-icon")}</button>
    </div>
    <div class="agent-dialog-body cite-body">
      <p class="cite-name"><strong>FastMDXplora</strong>: {escape(expansion)}</p>
      <div class="cite-block">
        <p id="cite-reference">{escape(citation)}</p>
        <button type="button" class="line-btn" data-copy-from="cite-reference" aria-label="Copy the reference" title="Copy the reference">{icon("copy", "line-icon")}</button>
      </div>
      <p class="subtle">DOI: <a href="https://doi.org/{escape(doi)}" target="_blank" rel="noopener">{escape(doi)}</a> &nbsp;&middot;&nbsp; version <span id="cite-version">{escape(version)}</span></p>
      <div class="cite-bibtex-head">
        <span class="builder-label">BibTeX</span>
        <button type="button" class="line-btn" data-copy-from="cite-bibtex" aria-label="Copy the BibTeX" title="Copy the BibTeX">{icon("copy", "line-icon")}</button>
      </div>
      <pre class="mono" id="cite-bibtex">{escape(bibtex)}</pre>
      <p class="subtle">&copy; Copyright {escape(copyright_)}.</p>
    </div>
  </div>
</div>"""


def _render_card(card: DashboardCard) -> str:
    value = card.value or DASH
    # Paths in monospace at a smaller size, as the GUI's Overview sets them.
    kind = "path" if ("/" in value or "\\" in value) else "text"
    return (
        '<div class="metric-card">'
        f'<div class="metric-card-label">{escape(card.label)}</div>'
        f'<div class="metric-card-value mono" data-kind="{kind}" title="{escape(value)}">'
        f"{escape(value)}</div>"
        f'<div class="metric-card-unit" title="{escape(card.detail)}">{escape(card.detail)}</div>'
        "</div>"
    )


def _render_section(
    section: DashboardSection,
    charted: set[str] | None = None,
    series: dict[str, Any] | None = None,
) -> str:
    charted = set() if charted is None else charted
    named: set[str] = set()
    cards = []
    for panel in section.panels:
        folder = re.search(r"(?:^|/)analysis/([a-z][a-z0-9_]*)/", panel.original_source)
        name = folder.group(1) if folder and folder.group(1) not in named else ""
        if name:
            named.add(name)
        # An analysis's own figure is also plotted from its numbers, once.
        own = _OWN_FIGURE.search(panel.original_source)
        plotted = own.group(1) if own and own.group(1) not in charted else ""
        if plotted and plotted in (series or {}):
            charted.add(plotted)
        else:
            plotted = ""
        cards.append(_render_panel(panel, section.title, name, plotted))
    count = len(section.panels)
    return (
        f'<section class="analysis-section" id="{escape(section.anchor)}">'
        '<div class="analysis-section-heading">'
        f'<h2 class="analysis-section-title">{escape(section.title)}</h2>'
        f'<span class="analysis-section-count">{count} figure{"" if count == 1 else "s"}</span>'
        f'</div><div class="analysis-grid">{"".join(cards)}</div></section>'
    )


def _render_panel(panel: DashboardPanel, section_title: str = "", analysis: str = "",
                  plotted: str = "") -> str:
    figure = panel.original_href or panel.href
    named = f' data-analysis="{escape(analysis)}"' if analysis else ""
    series = f' data-series="{escape(plotted)}"' if plotted else ""
    toggle = ('<a class="file-action" href="#" data-series-toggle hidden>Show the figure</a>'
              if plotted else "")
    return (
        f'<article class="analysis-card" data-state="complete" id="{escape(_anchor(panel.title))}"'
        f"{named}>"
        '<div class="ac-header">'
        f'<div class="ac-title">{escape(panel.title)}</div>'
        f'<div class="ac-status">{escape(section_title or panel.category)}</div></div>'
        f'<div class="ac-frame"{series}><img src="{escape(figure)}" alt="{escape(panel.title)}" '
        'loading="lazy"></div>'
        f'<div class="ac-body">{escape(panel.summary)}</div>'
        f'<div class="ac-footer"><a class="file-action" href="{escape(figure)}" target="_blank" '
        f'rel="noopener">Open full size</a>{toggle}</div>'
        "</article>"
    )


def _render_static_live_panel(project_root: Path) -> str:
    """The Overview's health card and strip, from the live record the run
    left, worded as the GUI words them (``renderHealth``)."""
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, read_status

    serve_command = f"fastmdx gui --output {project_root.as_posix()}"
    status = read_status(project_root)
    metrics = read_metrics(project_root)
    health = analyze_health(status, metrics)
    state = str(health.get("state") or "unknown").lower()
    headline = str(health.get("headline") or health.get("message") or state.title())
    said = ([health.get("message"), health.get("explanation")] if health.get("headline")
            else [health.get("explanation")])
    # The explanation names commands in backticks; set them as code.
    explanation = re.sub(r"`([^`]+)`", r"<code>\1</code>",
                         escape(" ".join(str(part) for part in said if part)))
    items = health.get("items") if isinstance(health.get("items"), list) else []
    listed = "".join(
        f'<li data-state="{escape(str(item.get("severity") or "ok"))}">'
        f'<strong>{escape(str(item.get("title") or item.get("severity") or "info"))}</strong>'
        f'<span class="muted small"> {DASH} {escape(str(item.get("detail") or ""))}</span></li>'
        for item in items[:4] if isinstance(item, dict))
    # This page describes the run as it was written, so a stage never
    # reported is not "starting" as on the live page: it is not recorded.
    facts = (
        ("Status", str(status.get("status") or DASH) if status else DASH),
        ("Stage", str(status.get("stage") or DASH) if status else DASH),
        ("Platform", str(status.get("platform") or DASH) if status else DASH),
        ("Last update", str(status.get("last_update_timestamp") or DASH) if status else DASH),
    )
    def said(label: str, value: str) -> str:
        # A time is put in the reader's own zone, as the GUI's strip does.
        moment = _parse_datetime(value) if label == "Last update" else None
        when = f' data-when="{moment.timestamp():.0f}"' if moment else ""
        return f'<div><dt>{escape(label)}</dt><dd class="mono"{when}>{escape(value)}</dd></div>'

    strip = "".join(said(label, value) for label, value in facts)
    return (
        '<div class="overview-facts card" id="live-simulation">'
        f'<div class="hero-card" id="hero-health" data-state="{escape(state)}">'
        '<div class="hero-card-top"><div><div class="hero-label">Simulation health</div>'
        f'<div class="hero-status" id="health-headline">{escape(headline)}</div></div>'
        f'<span class="stage-pill" id="health-pill" data-state="{escape(state)}">{escape(state)}</span>'
        f'</div><p class="muted" id="health-explanation">{explanation or DASH}</p>'
        + (f'<ul class="health-list" role="list">{listed}</ul>' if listed else "")
        + f'</div><dl class="overview-strip">{strip}</dl>'
        '<p class="muted small">For live charts and the structure as it is written, open the '
        f"study in the GUI: <code>{escape(serve_command)}</code></p></div>"
    )


def _render_phase_row(row: PhaseRow) -> str:
    return (
        "<tr>"
        f"<td>{escape(row.name)}</td>"
        f'<td><span class="stage-pill">{escape(row.status)}</span></td>'
        f'<td class="muted">{escape(row.detail)}</td>'
        "</tr>"
    )


def _render_metric_row(row: MetricRow) -> str:
    why = f' title="{escape(row.why)}"' if row.why else ""
    return (
        "<tr>"
        f"<td>{escape(row.metric)}</td>"
        f'<td class="mono">{escape(row.average)}</td>'
        f'<td class="mono">{escape(row.samples)}</td>'
        f'<td class="muted"{why}>{escape(row.status)}</td>'
        "</tr>"
    )


def _quick_action_links(links: list[DashboardLink]) -> list[DashboardLink]:
    by_label = {link.label: link for link in links}
    actions: list[DashboardLink] = []
    for label, display in (
        ("Markdown report", "Open Markdown Report"),
        ("Slide deck", "Open Slides"),
        ("Project bundle", "Open Bundle"),
        ("Analysis manifest", "Open Analysis Manifest"),
    ):
        link = by_label.get(label)
        if link:
            actions.append(DashboardLink(display, link.href, link.detail))
    return actions


def _anchor(value: str) -> str:
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    anchor = "".join(chars).strip("-")
    return anchor or "section"


#: What only this page needs beside the GUI's rules: its pages are shown by
#: the address rather than by the GUI's router, and its column has no side
#: panel to share the width with.
_STATIC_ONLY_CSS = """
.static-dashboard .col-handle { cursor: default; }
.static-dashboard .col-handle::after { display: none; }
.static-dashboard .methods-text p:first-child { margin-top: 0; }
.static-dashboard .report-document img { max-width: 100%; height: auto; }
"""

#: The scheme before the first paint, as the GUI chooses it (frame.js): the
#: one chosen, kept under the GUI's own key, or the system's light or dark.
_THEME_FIRST_JS = """
(function () {
  var kept = null, former = {graphite: "dark", ink: "dark", paper: "light"};
  try { kept = localStorage.getItem("fmx.theme"); } catch (e) {}
  kept = former[kept] || kept;
  if (kept !== "light" && kept !== "dark") {
    try { kept = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
    catch (e) { kept = "dark"; }
  }
  document.documentElement.dataset.theme = kept;
})();
"""

#: The GUI's chart script asks ``/api/series`` for a series; on this page
#: the answer is the one the page carries. Every other request goes on.
_SERIES_FROM_THE_PAGE_JS = """
(function () {
  "use strict";
  var held = {};
  try { held = JSON.parse(document.getElementById("fmx-series").textContent) || {}; } catch (e) {}
  var ask = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (address, options) {
    var found = /^\\/api\\/series\\?analysis=([^&]+)$/.exec(String(address));
    if (found) {
      var said = held[decodeURIComponent(found[1])] || { ok: false };
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve(said); } });
    }
    return ask ? ask(address, options) : Promise.reject(new Error("no fetch"));
  };
})();
"""

_PAGE_JS = """
(function () {
  "use strict";
  var PAGES = ["overview", "analysis", "report", "files"];
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* As the GUI's (frame.js): System, Light or Dark, System following the
   * computer as it changes; a former scheme's name read as its scheme. */
  var FORMER = { graphite: "dark", ink: "dark", paper: "light" };
  function chosenScheme() {
    var kept = null;
    try { kept = localStorage.getItem("fmx.theme"); } catch (e) {}
    kept = FORMER[kept] || kept;
    return kept === "light" || kept === "dark" ? kept : "system";
  }
  function systemScheme() {
    try { return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
    catch (e) { return "dark"; }
  }
  function applyTheme(choice, chosen) {
    choice = FORMER[choice] || choice;
    if (choice !== "light" && choice !== "dark") choice = "system";
    var name = choice === "system" ? systemScheme() : choice;
    document.documentElement.dataset.theme = name;
    document.body.dataset.theme = name;
    $$(".seg-btn[data-theme]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.theme === choice);
      b.setAttribute("aria-pressed", String(b.dataset.theme === choice));
    });
    if (chosen) { try { localStorage.setItem("fmx.theme", choice); } catch (e) {} }
    document.dispatchEvent(new CustomEvent("fmx:theme", { detail: name }));
  }
  applyTheme(chosenScheme(), false);
  try {
    var query = matchMedia("(prefers-color-scheme: light)");
    var follow = function () { if (chosenScheme() === "system") applyTheme("system", false); };
    if (query.addEventListener) query.addEventListener("change", follow);
  } catch (e) { /* no media queries */ }
  $$(".seg-btn[data-theme]").forEach(function (b) {
    b.addEventListener("click", function () { applyTheme(b.dataset.theme, true); });
  });

  function show(name, target) {
    if (PAGES.indexOf(name) < 0) name = "overview";
    document.documentElement.setAttribute("data-page", name);
    $$("section.page").forEach(function (page) { page.hidden = page.getAttribute("data-page") !== name; });
    $$(".sidebar-nav .nav-link").forEach(function (link) {
      var on = link.getAttribute("data-view-link") === name;
      link.classList.toggle("active", on);
      if (on) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    if (name === "analysis" && window.FastMDXSeries) {
      window.FastMDXSeries.hydrate(document.getElementById("analysis-sections"));
    }
    if (target) target.scrollIntoView(); else window.scrollTo(0, 0);
  }
  /* Dialogs over the page, as in the GUI: Escape, Close or a click on the
   * dimmed page closes one, and focus goes back to what opened it. */
  var opener = null;
  function openDialog(id, from) {
    var dialog = document.getElementById(id);
    if (!dialog) return;
    opener = from || document.activeElement;
    dialog.hidden = false;
    var first = dialog.querySelector("button, [href]");
    if (first) first.focus();
  }
  function closeDialogs() {
    var open = $$(".agent-dialog").filter(function (d) { return !d.hidden; });
    open.forEach(function (d) { d.hidden = true; });
    if (open.length && opener && opener.offsetParent !== null) opener.focus();
    return open.length > 0;
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (target.classList.contains("agent-dialog") || target.closest("[data-dialog-close]")) {
      closeDialogs();
      return;
    }
    var trigger = target.closest("[data-dialog-open]");
    if (trigger) {
      var gear = document.getElementById("settings-open");
      if (popup && popup.contains(trigger)) closeSettings();
      openDialog(trigger.getAttribute("data-dialog-open"), popup && popup.contains(trigger) ? gear : trigger);
    }
  }, true);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && closeDialogs()) event.stopImmediatePropagation();
  }, true);

  function route() {
    var id = "";
    try { id = decodeURIComponent((location.hash || "").slice(1)); } catch (e) {}
    // A link to the citation, which was a page, opens its dialog.
    if (id === "cite") { show("overview"); openDialog("cite-dialog"); return; }
    if (!id || PAGES.indexOf(id) >= 0) { show(id || "overview"); return; }
    var target = document.getElementById(id);
    var page = target && target.closest("section.page");
    show(page ? page.getAttribute("data-page") : "overview", target);
  }
  window.addEventListener("hashchange", route);
  route();

  var popup = document.getElementById("settings-popup");
  var opener = document.getElementById("settings-open");
  function closeSettings() {
    if (!popup || popup.hidden) return;
    popup.hidden = true;
    if (opener) opener.setAttribute("aria-expanded", "false");
  }
  if (popup && opener) {
    opener.addEventListener("click", function (event) {
      event.stopPropagation();
      popup.hidden = !popup.hidden;
      opener.setAttribute("aria-expanded", String(!popup.hidden));
    });
    document.addEventListener("click", function (event) {
      if (!popup.contains(event.target)) closeSettings();
    });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeSettings(); });
    $$("a", popup).forEach(function (a) { a.addEventListener("click", closeSettings); });
  }

  $$("[data-when]").forEach(function (el) {
    var seconds = parseFloat(el.getAttribute("data-when"));
    if (isFinite(seconds)) el.textContent = new Date(seconds * 1000).toLocaleString();
  });

  function toast(message, kind) {
    var el = document.getElementById("dashboard-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "dashboard-toast";
      el.className = "dashboard-toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.setAttribute("data-kind", kind || "ok");
    el.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { el.classList.remove("show"); }, 3500);
  }
  function copy(text, said) {
    var done = function () { toast(said); };
    var failed = function () { toast("Select the text to copy it.", "warning"); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, failed);
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(area);
    if (ok) done(); else failed();
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy-text], [data-copy-from]");
    if (!button) return;
    if (button.hasAttribute("data-copy-text")) {
      copy(button.getAttribute("data-copy-text") || "", "Output folder path copied.");
    } else {
      var source = document.getElementById(button.getAttribute("data-copy-from"));
      copy(source ? source.textContent : "", "Copied.");
    }
  });
})();
"""
