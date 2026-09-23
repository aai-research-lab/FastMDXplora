"""Static HTML dashboard for FastMDXplora report outputs."""

from __future__ import annotations

import csv
import json
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

# Each analysis gets its own section, in the order the analysis phase runs
# them. Grouping several analyses under invented headings ("Core Metrics",
# "Additional Analysis") made the placement look arbitrary: whether SASA got
# its own heading depended on how many figures it happened to produce.
SECTION_ORDER: tuple[str, ...] = (
    "RMSD",
    "RMSF",
    "Radius of Gyration",
    "Hydrogen Bonds",
    "Secondary Structure",
    "Solvent Accessible Surface Area",
    "Dihedrals",
    "Q-value",
    "Clustering",
    "Dimensionality Reduction",
    "Ligand Pose RMSD",
    "Ligand RMSF",
    "Protein-Ligand Contacts",
    "Protein-Ligand Hydrogen Bonds",
    "Region Highlights",
    "Apo/Holo Comparison",
    "Other",
)

SECTION_ANCHORS: dict[str, str] = {
    title: re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    for title in SECTION_ORDER
}

ANALYSIS_SECTION_BY_FOLDER: dict[str, str] = {
    "rmsd": "RMSD",
    "rmsf": "RMSF",
    "rg": "Radius of Gyration",
    "hbonds": "Hydrogen Bonds",
    "ss": "Secondary Structure",
    "sasa": "Solvent Accessible Surface Area",
    "dihedrals": "Dihedrals",
    "qvalue": "Q-value",
    "cluster": "Clustering",
    "dimred": "Dimensionality Reduction",
    "ligand_rmsd": "Ligand Pose RMSD",
    "ligand_rmsf": "Ligand RMSF",
    "contacts": "Protein-Ligand Contacts",
    "pl_hbonds": "Protein-Ligand Hydrogen Bonds",
    "apo_holo": "Apo/Holo Comparison",
}



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
    ("RMSF", "analysis/rmsf/rmsf.dat", "line"),
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


@dataclass(frozen=True)
class PhaseRow:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class MetricRow:
    metric: str
    average: str
    stddev: str
    unit: str


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
) -> list[str]:
    """Write ``dashboard.html`` and return artifact paths relative to output_dir."""
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
    links = _artifact_links(
        project_root,
        output_dir,
        sections=sections,
        include_bundle_link=include_bundle_link,
    )
    phase_rows = _phase_rows(manifest)
    metrics = _metric_rows(project_root, analysis_manifest)
    status = _project_status(manifest)
    live_html = _render_static_live_panel(project_root)
    phase_notice = ""
    if phase_context.is_analysis_from_existing_trajectory:
        phase_notice = (
            "Analysis/report workflow from existing trajectory. Setup and "
            "simulation were not run in this workflow."
        )

    html = _render_dashboard(
        title=title,
        system=str(manifest.get("system") or getattr(orchestrator, "system", "")),
        status=status,
        generated_at=generated_at,
        phase_notice=phase_notice,
        cards=cards,
        sections=sections,
        links=links,
        phase_rows=phase_rows,
        metrics=metrics,
        output_folder=project_root.as_posix(),
        live_html=live_html,
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
        cards.append(DashboardCard("Frames", _format_number(n_frames), "analysis metadata"))

    n_atoms = analysis_manifest.get("n_atoms")
    if n_atoms is not None:
        cards.append(DashboardCard("Atom count", _format_number(n_atoms), "topology metadata"))

    # Imported here rather than at module scope: `report` imports this
    # module to build the dashboard, so a top-level import would be
    # circular.
    from fastmdxplora.report.context import load_phase_context

    phase_context = load_phase_context(project_root)
    if phase_context.simulation_present:
        sim_params = sim_manifest.get("parameters", {})
        if isinstance(sim_params, dict):
            duration = sim_params.get("duration_ns")
            if duration is not None:
                cards.append(
                    DashboardCard(
                        "Simulation time",
                        f"{_format_number(duration)} ns",
                        "simulation parameters",
                    )
                )
        temperature = _average_temperature(project_root / "simulation" / "energy.csv")
        if temperature is not None:
            cards.append(
                DashboardCard(
                    "Temperature",
                    f"{temperature:.1f} K",
                    "energy log average",
                )
            )

    wall_time = _wall_time(phases)
    if wall_time:
        cards.append(DashboardCard("Wall time", wall_time, "recorded phase timestamps"))

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
    # in progress are complete, not pending.
    live_at = order.index("simulation") if running else None

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
    specs: tuple[tuple[str, str, str, str], ...] = (
        ("RMSD", "analysis/rmsd/rmsd.dat", "nm", "rmsd"),
        ("RMSF", "analysis/rmsf/rmsf.dat", "nm", "rmsf"),
        ("Radius of gyration", "analysis/rg/rg.dat", "nm", "rg"),
        ("H-bonds", "analysis/hbonds/hbonds.dat", "count", "hbonds"),
        ("SASA", "analysis/sasa/sasa.dat", "nm^2", "sasa"),
    )

    # On a biased run the mean of a .dat file is an average over a
    # distribution the bias flattened, and this table is the first thing a
    # reader looks at. Where the analysis phase recovered the equilibrium
    # value, that is the one shown, and where it could not the row says so
    # rather than presenting a biased average as a measurement.
    from fastmdxplora.report.reweighted import load_reweighted

    reweighted = load_reweighted(project_root)
    corrected = {
        item.get("analysis"): item
        for item in ((reweighted or {}).get("quantities") or [])
    }

    def formatted(value: Any) -> str:
        """A dash where there is no number, rather than a crash or a 'nan'.

        The weighted spread is nan when the weights carry one effective
        frame, which is a real outcome of a badly converged run and not an
        error to raise in the middle of building a page.
        """
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        if number != number:
            return "—"
        return _format_metric_value(number)

    rows: list[MetricRow] = []
    for label, rel, unit, name in specs:
        values = _numeric_series(project_root / rel)
        if not values:
            continue
        item = corrected.get(name)
        if item is not None:
            rows.append(
                MetricRow(
                    metric=f"{label} (reweighted)",
                    average=formatted(item.get("reweighted_mean")),
                    stddev=formatted(item.get("reweighted_std")),
                    unit=unit,
                )
            )
            continue
        rows.append(
            MetricRow(
                metric=f"{label} (biased ensemble)" if reweighted else label,
                average=_format_metric_value(_mean(values)),
                stddev=_format_metric_value(_stddev(values)),
                unit=unit,
            )
        )

    if reweighted and reweighted.get("applies", True):
        rows.append(
            MetricRow(
                metric="Effective frames after reweighting",
                average=formatted(reweighted.get("effective_sample_size")),
                stddev="—",
                unit=f"of {reweighted.get('n_frames')}",
            )
        )

    # A count is one number, not a sample: there is no standard deviation to
    # report, which is a different thing from one that could not be obtained. The
    # column said "not available", which reads as a measurement that failed.
    n_frames = analysis_manifest.get("n_frames")
    if n_frames is not None:
        rows.append(MetricRow("Frame count", _format_number(n_frames), "—", "frames"))
    n_atoms = analysis_manifest.get("n_atoms")
    if n_atoms is not None:
        rows.append(MetricRow("Atom count", _format_number(n_atoms), "—", "atoms"))
    return rows


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


def _wall_time(phases: list[dict[str, Any]]) -> str:
    starts: list[datetime] = []
    finishes: list[datetime] = []
    for phase in phases:
        start = _parse_datetime(phase.get("started_at"))
        finish = _parse_datetime(phase.get("finished_at"))
        if start:
            starts.append(start)
        if finish:
            finishes.append(finish)
    if not starts or not finishes:
        return ""
    seconds = max(0, int((max(finishes) - min(starts)).total_seconds()))
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
    summaries: dict[str, DashboardAsset] = {}
    for title, rel_data, kind in DASHBOARD_SUMMARY_SPECS:
        data_path = project_root / rel_data
        if not data_path.is_file():
            continue
        try:
            summary = _summarise_data_file(data_path, kind)
        except Exception as exc:  # noqa: BLE001 - a caption must never fail a report
            logger.debug("dashboard: no summary for %s: %s", title, exc)
            continue
        summaries[title] = DashboardAsset(rel_path=rel_data, summary=summary)
    return summaries



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
    return f"avg {_format_metric_value(_mean(values))}"









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
            "Region Highlights"
            if "region" in source.stem.lower()
            else "Apo/Holo Comparison"
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
        "apo_holo_comparison": "Apo/Holo comparison",
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
        return theme.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - only if the installed package is incomplete
        return ":root { color-scheme: dark; }"


def _render_dashboard(
    *,
    title: str,
    system: str,
    status: str,
    generated_at: datetime,
    phase_notice: str,
    cards: list[DashboardCard],
    sections: list[DashboardSection],
    links: list[DashboardLink],
    phase_rows: list[PhaseRow],
    metrics: list[MetricRow],
    output_folder: str,
    live_html: str,
) -> str:
    theme_tokens = _theme_tokens()
    # The report and the slides both print this and the dashboard did not,
    # although it is the file somebody opens first. Taken from the same
    # constant, so the three cannot drift apart.
    from fastmdxplora import __citation__, __doi__

    citation_html = (
        f"{escape(__citation__)} "
        f'<a href="https://doi.org/{escape(__doi__)}">'
        f"doi:{escape(__doi__)}</a>"
    )
    nav_html = _render_sidebar(sections, links)
    card_html = "\n".join(_render_card(card) for card in cards)
    sections_html = "\n".join(_render_section(section) for section in sections)
    if not sections_html:
        sections_html = (
            '<section class="empty-state panel-block">'
            "<h2>Analysis panels</h2>"
            "<p>No analysis figures are available in this run output.</p>"
            "</section>"
        )
    link_html = _render_output_list(links)
    quick_html = "\n".join(_render_quick_action(link) for link in _quick_action_links(links))
    phase_html = "\n".join(_render_phase_row(row) for row in phase_rows)
    metrics_html = "\n".join(_render_metric_row(row) for row in metrics)
    if not metrics_html:
        # What is absent, not the fact of absence. This table is filled from
        # the analysis outputs, so an empty one means the analysis phase has
        # not produced them yet -- which is worth saying.
        metrics_html = (
            '<tr><td colspan="4" class="table-empty">'
            "No analysis outputs to summarise yet."
            "</td></tr>"
        )
    notice_html = (
        f'<div class="notice"><strong>Existing trajectory analysis</strong>'
        f"{escape(phase_notice)}</div>" if phase_notice else ""
    )
    generated = generated_at.strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - FastMDXplora Dashboard</title>
  <style>
    {theme_tokens}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(57, 183, 201, 0.12), transparent 34rem),
        linear-gradient(180deg, var(--background-primary) 0%, var(--background-secondary) 100%);
      color: var(--text-primary);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    a {{ color: inherit; }}
    .layout {{
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
      border-right: 1px solid var(--border-primary);
      background: linear-gradient(180deg, rgba(7, 19, 33, 0.98), rgba(5, 13, 23, 0.98));
      padding: 22px 16px;
    }}
    .logo {{
      display: grid;
      grid-template-columns: 42px 1fr;
      gap: 12px;
      align-items: center;
      padding-bottom: 22px;
      border-bottom: 1px solid var(--border-primary);
      margin-bottom: 20px;
    }}
    .mark {{
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border-radius: 12px;
      border: 1px solid rgba(77, 157, 247, 0.5);
      background: rgba(77, 157, 247, 0.12);
      color: var(--accent-cyan);
      font-weight: 800;
    }}
    .logo-title {{
      font-size: 1.08rem;
      font-weight: 800;
      line-height: 1.1;
    }}
    .logo-subtitle {{
      color: var(--text-muted);
      font-size: 0.72rem;
      margin-top: 3px;
    }}
    .nav-section {{ margin: 18px 0; }}
    .nav-heading {{
      color: var(--text-muted);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      margin: 0 0 8px;
    }}
    .nav-link {{
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 34px;
      padding: 7px 10px;
      border-radius: 8px;
      color: var(--text-secondary);
      text-decoration: none;
      font-size: 0.9rem;
    }}
    .nav-link.active {{
      background: linear-gradient(90deg, rgba(77, 157, 247, 0.32), rgba(57, 183, 201, 0.14));
      color: white;
    }}
    .nav-link:hover, .output-link:hover, .action-link:hover {{
      border-color: rgba(77, 157, 247, 0.6);
      background-color: rgba(77, 157, 247, 0.08);
    }}
    .nav-icon {{
      width: 1.35em;
      text-align: center;
      color: var(--accent-cyan);
    }}
    .shell {{
      width: min(1480px, calc(100% - 36px));
      margin: 0 auto;
      padding: 18px 0 40px;
    }}
    header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 20px;
      align-items: end;
      padding: 20px 0 24px;
      border-bottom: 1px solid var(--border-primary);
    }}
    .breadcrumb {{
      color: var(--text-muted);
      font-size: 0.86rem;
      margin-top: 5px;
    }}
    .brand {{
      color: var(--accent-cyan);
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 6px 0;
      font-size: clamp(1.8rem, 3vw, 3.2rem);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .subtle {{ color: var(--text-muted); }}
    .status {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border-primary);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-muted);
      white-space: nowrap;
    }}
    .dot {{
      width: 9px;
      height: 9px;
      border-radius: 99px;
      background: var(--accent-orange);
    }}
    .dot.ok {{ background: var(--accent-green); }}
    .dot.error {{ background: var(--accent-red); }}
    .dot.unknown {{ background: var(--accent-orange); }}
    .notice {{
      margin: 20px 0 0;
      padding: 14px 16px;
      border: 1px solid rgba(57, 183, 201, 0.38);
      border-radius: 8px;
      background: rgba(57, 183, 201, 0.09);
      color: var(--accent-cyan);
    }}
    .notice strong {{
      display: block;
      color: white;
      margin-bottom: 3px;
    }}
    .live-panel {{
      margin: 20px 0 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(220px, 0.36fr);
      gap: 14px;
      align-items: stretch;
    }}
    .live-panel .panel-block {{
      margin: 0;
    }}
    .live-status-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .live-mini-card {{
      border: 1px solid rgba(148, 163, 184, 0.16);
      border-radius: 8px;
      background: var(--background-elevated);
      padding: 10px;
    }}
    .live-mini-card span {{
      display: block;
      color: var(--text-muted);
      font-size: 0.75rem;
      margin-bottom: 4px;
    }}
    .live-mini-card strong {{
      overflow-wrap: anywhere;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin: 22px 0;
    }}
    .card, .plot-card, .output-link, .empty-state, .panel-block, .action-link {{
      border: 1px solid var(--border-primary);
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(19, 35, 56, 0.92), rgba(16, 27, 41, 0.96));
      box-shadow: var(--shadow-card);
    }}
    .card {{
      min-height: 124px;
      padding: 18px;
    }}
    .card .label {{
      color: var(--text-muted);
      font-size: 0.88rem;
      margin-bottom: 10px;
    }}
    .card .value {{
      font-size: 1.55rem;
      font-weight: 760;
      overflow-wrap: anywhere;
    }}
    .card.good .value {{ color: var(--accent-green); }}
    .card.warn .value {{ color: var(--accent-orange); }}
    .card .detail {{
      color: var(--text-muted);
      font-size: 0.86rem;
      margin-top: 8px;
      overflow-wrap: anywhere;
    }}
    .status-card {{
      border-color: rgba(57, 183, 201, 0.35);
      background: linear-gradient(180deg, rgba(57, 183, 201, 0.11), rgba(16, 27, 41, 0.96));
    }}
    .section-heading {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
      margin: 26px 0 12px;
    }}
    h2 {{
      margin: 0;
      font-size: 1.08rem;
      letter-spacing: 0;
    }}
    .plot-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      grid-auto-rows: 8px;
      grid-auto-flow: dense;
      column-gap: 18px;
      row-gap: 8px;
      align-items: stretch;
    }}
    .plot-card {{
      overflow: hidden;
      min-width: 0;
      min-height: 0;
      padding: 14px;
      display: flex;
      flex-direction: column;
      position: relative;
      grid-column: span var(--col-span, 1);
      grid-row: span var(--row-span, 20);
    }}
    .plot-card.card-sm {{ --col-span: 1; --row-span: 16; }}
    .plot-card.card-md {{ --col-span: 1; --row-span: 20; }}
    .plot-card.card-lg {{ --col-span: 2; --row-span: 38; }}
    .plot-card.card-wide {{ --col-span: 2; --row-span: 24; }}
    .plot-header {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
      margin-bottom: 12px;
    }}
    .plot-header h3 {{
      margin: 0;
      font-size: 1rem;
    }}
    .plot-title-group {{
      min-width: 0;
    }}
    .size-controls {{
      display: inline-flex;
      gap: 4px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .size-button, .reset-layout {{
      border: 1px solid rgba(148, 163, 184, 0.28);
      background: rgba(15, 26, 42, 0.88);
      color: var(--text-muted);
      border-radius: 6px;
      padding: 3px 6px;
      font: inherit;
      font-size: 0.68rem;
      line-height: 1.2;
      cursor: pointer;
    }}
    .size-button:hover, .size-button.active, .reset-layout:hover {{
      color: white;
      border-color: rgba(57, 183, 201, 0.72);
      background: rgba(57, 183, 201, 0.18);
    }}
    .plot-frame {{
      flex: 1 1 auto;
      min-height: 180px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      background: var(--background-elevated);
      border-radius: 12px;
      padding: 8px;
      border: 1px solid rgba(148, 163, 184, 0.18);
    }}
    .plot-card.fallback .plot-frame {{
      background: var(--background-elevated);
      padding: 12px;
    }}
    .plot-frame a {{
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      text-decoration: none;
      min-width: 0;
    }}
    .plot-frame img {{
      display: block;
      max-width: 100%;
      max-height: 100%;
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: 6px;
      background: var(--background-soft);
    }}
    .plot-card.fallback .plot-frame img {{
      background: white;
    }}
    .resize-handle {{
      position: absolute;
      right: 8px;
      bottom: 8px;
      width: 16px;
      height: 16px;
      cursor: nwse-resize;
      opacity: 0.75;
      touch-action: none;
    }}
    .resize-handle::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(135deg, transparent 0 45%, rgba(148, 163, 184, 0.9) 46% 52%, transparent 53%),
        linear-gradient(135deg, transparent 0 65%, rgba(148, 163, 184, 0.7) 66% 72%, transparent 73%);
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      padding: 3px 7px;
      border-radius: 999px;
      border: 1px solid rgba(57, 183, 201, 0.32);
      color: var(--accent-cyan);
      background: rgba(57, 183, 201, 0.10);
      font-size: 0.72rem;
      white-space: nowrap;
    }}
    .summary-value {{
      color: var(--text-primary);
      font-size: 0.84rem;
      margin-top: 9px;
    }}
    .source {{
      padding: 10px 2px 0;
      color: var(--text-muted);
      font-size: 0.82rem;
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.35;
    }}
    .plot-meta {{
      overflow-wrap: anywhere;
      font-size: 0.82rem;
    }}
    .category-label {{
      color: var(--text-muted);
      font-size: 0.78rem;
      margin-top: 8px;
    }}
    .lower-grid {{
      display: grid;
      grid-template-columns: minmax(280px, 0.95fr) minmax(320px, 1.15fr)
        minmax(280px, 0.95fr) minmax(250px, 0.8fr);
      gap: 14px;
      align-items: start;
      margin-top: 26px;
    }}
    .panel-block {{
      padding: 16px;
    }}
    .panel-block h2 {{
      margin-bottom: 14px;
    }}
    .phase-row {{
      display: grid;
      grid-template-columns: 22px 1fr auto;
      gap: 9px;
      align-items: center;
      padding: 9px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.07);
    }}
    .phase-row:last-child {{ border-bottom: 0; }}
    .phase-dot {{
      display: grid;
      place-items: center;
      width: 22px;
      height: 18px;
      border-radius: 99px;
      background: var(--border-strong);
      color: white;
      font-size: 0.58rem;
      font-weight: 800;
    }}
    .phase-row.ok .phase-dot {{ background: var(--accent-green); }}
    .phase-row.error .phase-dot {{ background: var(--accent-red); }}
    .phase-row.skipped .phase-dot {{ background: var(--accent-orange); }}
    .phase-row.not-run .phase-dot {{ background: var(--text-muted); }}
    .phase-row.pending .phase-dot {{ background: var(--text-muted); opacity: .5; }}
    .phase-row.running .phase-dot {{ background: var(--accent-cyan);
                                     animation: phase-pulse 1.6s ease-in-out infinite; }}
    @keyframes phase-pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .35; }} }}
    .phase-detail {{
      color: var(--text-muted);
      font-size: 0.84rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    th, td {{
      padding: 9px 8px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--text-muted);
      font-weight: 700;
    }}
    td.num {{ text-align: right; }}
    .table-empty {{
      color: var(--text-muted);
      text-align: center;
    }}
    .outputs {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }}
    .output-link {{
      display: block;
      padding: 9px 10px;
      text-decoration: none;
      box-shadow: none;
    }}
    .output-link strong {{
      display: block;
      margin-bottom: 3px;
    }}
    .output-link span {{
      display: block;
      color: var(--text-muted);
      font-size: 0.78rem;
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.35;
    }}
    .outputs-extra {{
      margin-top: 10px;
    }}
    .outputs-extra summary {{
      cursor: pointer;
      color: var(--accent-cyan);
      font-size: 0.84rem;
      margin-bottom: 10px;
    }}
    .actions {{
      display: grid;
      gap: 10px;
    }}
    .action-link {{
      display: block;
      padding: 12px 13px;
      text-decoration: none;
      box-shadow: none;
    }}
    .action-title {{
      display: block;
      font-weight: 700;
    }}
    .action-subtitle {{
      display: block;
      margin-top: 4px;
      color: var(--text-muted);
      font-size: 0.9rem;
    }}
    .artifact-path {{
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.35;
    }}
    .folder-note {{
      margin-top: 12px;
      color: var(--text-muted);
      font-size: 0.82rem;
      overflow-wrap: anywhere;
    }}
    .empty-state {{
      padding: 20px;
      color: var(--text-muted);
    }}
    footer {{
      margin-top: 30px;
      color: var(--text-muted);
      font-size: 0.82rem;
    }}
    @media (max-width: 720px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{
        position: relative;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--border-primary);
      }}
      .shell {{ width: min(100% - 20px, 1480px); padding-top: 14px; }}
      header {{ grid-template-columns: 1fr; align-items: start; }}
      .plot-grid {{ grid-template-columns: 1fr; }}
      .plot-card,
      .plot-card.card-lg,
      .plot-card.card-wide {{ --col-span: 1 !important; }}
      .lower-grid {{ grid-template-columns: 1fr; }}
      .live-panel {{ grid-template-columns: 1fr; }}
    }}
    @media (min-width: 721px) and (max-width: 1320px) {{
      .lower-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      {nav_html}
    </aside>
    <main class="shell">
      <header id="dashboard">
        <div>
          <div class="brand">FastMDXplora Results</div>
          <h1>Dashboard</h1>
          <div class="subtle">{escape(title)}</div>
          <div class="breadcrumb">Project / Results / Dashboard</div>
          <div class="subtle">System: {escape(_system_label(system) if system else "—")}</div>
        </div>
        <div class="status">
          <span class="dot {escape(status)}"></span>{escape(status.title())} -
          Last generated {escape(generated)}
          <button class="reset-layout" type="button" data-reset-layout>Reset layout</button>
        </div>
      </header>
      {notice_html}
      {live_html}
      <section class="cards" aria-label="Summary metrics">
        {card_html}
      </section>
      {sections_html}
      <section class="lower-grid">
        <div class="panel-block" id="run-status">
          <h2>Run Progress</h2>
          {phase_html}
        </div>
        <div class="panel-block" id="top-metrics">
          <h2>Top Metrics</h2>
          <table>
            <thead>
              <tr><th>Metric</th><th>Average</th><th>Std. Dev.</th><th>Unit</th></tr>
            </thead>
            <tbody>{metrics_html}</tbody>
          </table>
        </div>
        <div class="panel-block" id="recent-outputs">
          <h2>Recent Outputs</h2>
          {link_html}
        </div>
        <div class="panel-block" id="quick-actions">
          <h2>Quick Actions</h2>
          <div class="actions">
            {quick_html}
          </div>
          <div class="folder-note">
            Output folder: {escape(output_folder)}.
            Some browsers block direct local folder opening from static pages.
          </div>
        </div>
      </section>
      <footer>
        Generated by FastMDXplora from recorded run artifacts.
        Missing metrics are omitted.
        <br><br>
        If this software contributed to your work, please cite:
        <br>{citation_html}
      </footer>
    </main>
  </div>
  <script>
    (() => {{
      const key = "fastmdx-dashboard-card-layout:" + window.location.pathname;
      const presets = {{
        sm: {{ cols: 1, rows: 16 }},
        md: {{ cols: 1, rows: 20 }},
        lg: {{ cols: 2, rows: 38 }},
        wide: {{ cols: 2, rows: 24 }},
      }};
      let saved = {{}};
      try {{ saved = JSON.parse(localStorage.getItem(key) || "{{}}"); }}
      catch (_) {{ saved = {{}}; }}
      const cards = Array.from(document.querySelectorAll(".plot-card[data-card-key]"));
      const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
      const gridMetrics = (grid) => {{
        const style = getComputedStyle(grid);
        const columns = style.gridTemplateColumns.split(" ").filter(Boolean);
        const firstColumn = parseFloat(columns[0]) || 280;
        return {{
          columns: Math.max(1, columns.length),
          columnWidth: firstColumn,
          columnGap: parseFloat(style.columnGap) || 18,
          rowHeight: parseFloat(style.gridAutoRows) || 8,
          rowGap: parseFloat(style.rowGap) || 8,
        }};
      }};
      const applySpan = (card, cols, rows, persist = true) => {{
        const grid = card.closest(".plot-grid");
        const metrics = grid ? gridMetrics(grid) : {{ columns: 1 }};
        cols = clamp(Math.round(cols || 1), 1, metrics.columns || 1);
        rows = clamp(Math.round(rows || 20), 14, 64);
        card.style.setProperty("--col-span", cols);
        card.style.setProperty("--row-span", rows);
        for (const name of Object.keys(presets)) card.classList.remove("card-" + name);
        for (const button of card.querySelectorAll("[data-card-size]")) {{
          const preset = presets[button.dataset.cardSize];
          button.classList.toggle(
            "active",
            Boolean(preset && preset.cols === cols && preset.rows === rows)
          );
        }}
        if (persist) {{
          saved[card.dataset.cardKey] = {{ cols, rows }};
          try {{ localStorage.setItem(key, JSON.stringify(saved)); }}
          catch (_) {{}}
        }}
      }};
      const applySize = (card, size, persist = true) => {{
        const preset = presets[size] || presets.md;
        applySpan(card, preset.cols, preset.rows, persist);
      }};
      for (const card of cards) {{
        const restored = saved[card.dataset.cardKey];
        if (restored && Number.isFinite(restored.cols) && Number.isFinite(restored.rows)) {{
          applySpan(card, restored.cols, restored.rows, false);
        }} else {{
          applySize(card, "md", false);
        }}
        for (const button of card.querySelectorAll("[data-card-size]")) {{
          button.addEventListener("click", () => applySize(card, button.dataset.cardSize));
        }}
        const handle = card.querySelector(".resize-handle");
        if (handle) {{
          handle.addEventListener("pointerdown", (event) => {{
            event.preventDefault();
            handle.setPointerCapture(event.pointerId);
            const grid = card.closest(".plot-grid");
            const metrics = gridMetrics(grid);
            const start = {{
              x: event.clientX,
              y: event.clientY,
              cols: parseInt(getComputedStyle(card).getPropertyValue("--col-span"), 10) || 1,
              rows: parseInt(getComputedStyle(card).getPropertyValue("--row-span"), 10) || 20,
            }};
            const move = (moveEvent) => {{
              const colUnit = metrics.columnWidth + metrics.columnGap;
              const rowUnit = metrics.rowHeight + metrics.rowGap;
              const cols = start.cols + Math.round((moveEvent.clientX - start.x) / colUnit);
              const rows = start.rows + Math.round((moveEvent.clientY - start.y) / rowUnit);
              applySpan(card, cols, rows, false);
            }};
            const done = () => {{
              handle.removeEventListener("pointermove", move);
              handle.removeEventListener("pointerup", done);
              handle.removeEventListener("pointercancel", done);
              const cols = parseInt(getComputedStyle(card).getPropertyValue("--col-span"), 10) || 1;
              const rows = parseInt(getComputedStyle(card).getPropertyValue("--row-span"), 10) || 20;
              applySpan(card, cols, rows, true);
            }};
            handle.addEventListener("pointermove", move);
            handle.addEventListener("pointerup", done);
            handle.addEventListener("pointercancel", done);
          }});
        }}
      }}
      const reset = document.querySelector("[data-reset-layout]");
      if (reset) {{
        reset.addEventListener("click", () => {{
          saved = {{}};
          try {{ localStorage.removeItem(key); }} catch (_) {{}}
          for (const card of cards) applySize(card, "md", false);
        }});
      }}
    }})();
  </script>
</body>
</html>
"""


def _render_card(card: DashboardCard) -> str:
    detail = f'<div class="detail">{escape(card.detail)}</div>' if card.detail else ""
    classes = f"card {card.kind}"
    if card.label == "Project status":
        classes += " status-card"
    return (
        f'<article class="{escape(classes)}">'
        f'<div class="label">{escape(card.label)}</div>'
        f'<div class="value">{escape(card.value)}</div>'
        f"{detail}"
        "</article>"
    )


def _render_section(section: DashboardSection) -> str:
    panels = "\n".join(_render_panel(panel) for panel in section.panels)
    count = f"{len(section.panels)} artifact" + ("" if len(section.panels) == 1 else "s")
    classes = f"analysis-section section-{section.anchor}"
    return (
        f'<section class="{escape(classes)}" id="{escape(section.anchor)}">'
        '<div class="section-heading">'
        f"<h2>{escape(section.title)}</h2>"
        f'<span class="subtle">{escape(count)}</span>'
        "</div>"
        '<div class="plot-grid">'
        f"{panels}"
        "</div>"
        "</section>"
    )


def _render_panel(panel: DashboardPanel) -> str:
    # Every panel now shows the figure the analysis wrote, so there is no
    # "fallback" case left to style differently.
    card_class = "plot-card card-md"
    summary = (
        f'<div class="summary-value">{escape(panel.summary)}</div>'
        if panel.summary else ""
    )
    category = (
        f'<div class="category-label">{escape(panel.category)}</div>'
        if panel.category else ""
    )
    original = ""
    if panel.original_source != panel.source:
        original = (
            f' - original: <a href="{escape(panel.original_href)}">'
            f"{escape(panel.original_source)}</a>"
        )
    card_key = _anchor(panel.original_source)
    return (
        f'<article class="{escape(card_class)}" id="{escape(_anchor(panel.title))}" '
        f'data-card-key="{escape(card_key)}">'
        '<div class="plot-header">'
        '<div class="plot-title-group">'
        f"<h3>{escape(panel.title)}</h3>"
        f'<span class="tag">{escape(panel.category or panel.mode)}</span>'
        "</div>"
        '<div class="size-controls" aria-label="Card size">'
        '<button type="button" class="size-button" data-card-size="sm">S</button>'
        '<button type="button" class="size-button active" data-card-size="md">M</button>'
        '<button type="button" class="size-button" data-card-size="lg">L</button>'
        '<button type="button" class="size-button" data-card-size="wide">Wide</button>'
        "</div>"
        "</div>"
        '<div class="plot-frame">'
        f'<a href="{escape(panel.href)}">'
        f'<img src="{escape(panel.href)}" alt="{escape(panel.title)} plot">'
        "</a>"
        "</div>"
        f"{summary}"
        f"{category}"
        f'<div class="source plot-meta artifact-path">{escape(panel.source)}{original}</div>'
        '<div class="resize-handle" title="Drag to resize"></div>'
        "</article>"
    )


def _render_link(link: DashboardLink) -> str:
    detail = escape(link.detail) if link.detail else escape(link.href)
    return (
        f'<a class="output-link" href="{escape(link.href)}">'
        f'<strong class="output-title">{escape(link.label)}</strong>'
        f'<span class="output-subtitle artifact-path">{detail}</span>'
        "</a>"
    )


def _render_output_list(links: list[DashboardLink]) -> str:
    visible = links[:10]
    hidden = links[10:]
    visible_html = "\n".join(_render_link(link) for link in visible)
    html = f'<div class="outputs output-list">{visible_html}</div>'
    if hidden:
        hidden_html = "\n".join(_render_link(link) for link in hidden)
        html += (
            '<details class="outputs-extra">'
            f"<summary>Show all outputs ({len(hidden)} more)</summary>"
            f'<div class="outputs output-list">{hidden_html}</div>'
            "</details>"
        )
    return html


def _render_static_live_panel(project_root: Path) -> str:
    from fastmdxplora.gui.telemetry import analyze_health, read_metrics, read_status

    serve_command = f"fastmdx gui --output {project_root.as_posix()}"
    escaped_command = escape(serve_command)
    status = read_status(project_root)
    metrics = read_metrics(project_root)
    health = analyze_health(status, metrics)
    if not status:
        # Naming the real cause. This used to advise starting the dashboard
        # during a run, which is what somebody looking at this page has just
        # done: telemetry is written only when the run asks for it, and it is
        # off by default, so the advice sent them to repeat what had already
        # failed.
        body = (
            "This run did not record live telemetry, so there is nothing for "
            "this page to read. It is written only when a run asks for it, "
            "and that is off by default: set <code>live_telemetry: true</code> "
            "under <code>simulation</code> in the config, or pass "
            "<code>--live-telemetry</code>, and this page fills as the run "
            "goes."
        )
        # The prose above has already said why there is nothing here; a dash
        # says the same without three more verdicts. This page describes a
        # finished run, so an unreported stage is not "starting" the way it is
        # on the live page -- it is simply not recorded.
        stage = "—"
        updated = "—"
        platform = "—"
    else:
        body = escape(str(health.get("explanation") or "—"))
        stage = escape(str(status.get("stage") or "—"))
        updated = escape(str(status.get("last_update_timestamp") or "—"))
        platform = escape(str(status.get("platform") or "—"))
    health_state = escape(str(health.get("state") or "unknown"))
    health_message = escape(str(health.get("message") or "Live telemetry is not available."))
    return (
        '<section class="live-panel" id="live-simulation">'
        '<div class="panel-block">'
        "<h2>Live Simulation</h2>"
        f"<p>{body}</p>"
        '<div class="live-status-grid">'
        f'<div class="live-mini-card"><span>Status</span><strong>{health_state}</strong></div>'
        f'<div class="live-mini-card"><span>Stage</span><strong>{stage}</strong></div>'
        f'<div class="live-mini-card"><span>Platform</span><strong>{platform}</strong></div>'
        f'<div class="live-mini-card"><span>Last update</span><strong>{updated}</strong></div>'
        "</div>"
        "</div>"
        '<div class="panel-block">'
        "<h2>Monitoring</h2>"
        f"<p>{health_message}</p>"
        '<p class="subtle">For real-time charts and events, run '
        f"<code>{escaped_command}</code>.</p>"
        "</div>"
        "</section>"
    )


def _render_sidebar(sections: list[DashboardSection], links: list[DashboardLink]) -> str:
    analysis_links = {section.title: f"#{section.anchor}" for section in sections}
    report_links = {link.label: link.href for link in links}
    parts = [
        '<div class="logo">',
        '<div class="mark">FX</div>',
        '<div><div class="logo-title">FastMDXplora</div>',
        '<div class="logo-subtitle">Explore. Analyze. Visualize. Share.</div></div>',
        '</div>',
        '<nav aria-label="Dashboard navigation">',
        '<div class="nav-section"><p class="nav-heading">Overview</p>',
        _nav_link("#dashboard", "Dashboard", "active", "D"),
        _nav_link("#live-simulation", "Live Simulation", "", "L"),
        _nav_link("#top-metrics", "System Info", "", "I"),
        _nav_link("#run-status", "Run Status", "", "S"),
        '</div>',
        '<div class="nav-section"><p class="nav-heading">Analysis</p>',
    ]
    for label in SECTION_ORDER:
        href = analysis_links.get(label)
        if href:
            parts.append(_nav_link(href, label, "", "-"))
    parts.extend(
        [
            '</div>',
            '<div class="nav-section"><p class="nav-heading">Reports</p>',
        ]
    )
    for label in ("Markdown report", "Slide deck", "Dashboard HTML", "Project bundle"):
        href = report_links.get(label)
        if href:
            display = {
                "Markdown report": "Markdown Report",
                "Slide deck": "Slides PPTX",
                "Dashboard HTML": "Dashboard HTML",
                "Project bundle": "Bundle",
            }[label]
            parts.append(_nav_link(href, display, "", "R"))
    parts.extend(["</div>", "</nav>"])
    return "\n".join(parts)


def _nav_link(href: str, label: str, classes: str, icon: str) -> str:
    class_attr = f"nav-link {classes}".strip()
    return (
        f'<a class="{escape(class_attr)}" href="{escape(href)}">'
        f'<span class="nav-icon">{escape(icon)}</span>{escape(label)}</a>'
    )


def _render_phase_row(row: PhaseRow) -> str:
    symbol = {
        "ok": "OK",
        "error": "!",
        "skipped": "-",
        "not-run": "-",
    }.get(row.status, "?")
    return (
        f'<div class="phase-row {escape(row.status)}">'
        f'<span class="phase-dot">{escape(symbol)}</span>'
        f"<span>{escape(row.name)}</span>"
        f'<span class="phase-detail">{escape(row.detail)}</span>'
        "</div>"
    )


def _render_metric_row(row: MetricRow) -> str:
    return (
        "<tr>"
        f"<td>{escape(row.metric)}</td>"
        f'<td class="num">{escape(row.average)}</td>'
        f'<td class="num">{escape(row.stddev)}</td>'
        f"<td>{escape(row.unit)}</td>"
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


def _render_quick_action(link: DashboardLink) -> str:
    return (
        f'<a class="action-link" href="{escape(link.href)}">'
        f'<span class="action-title">{escape(link.label)}</span>'
        f'<span class="action-subtitle">{escape(link.detail)}</span>'
        "</a>"
    )


def _anchor(value: str) -> str:
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    anchor = "".join(chars).strip("-")
    return anchor or "section"
