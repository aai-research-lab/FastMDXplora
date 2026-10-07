"""What each file in a study's folder is: its phase, its kind and its name.

One reading for every place that lists or packs a study's files: the GUI's
Files page, the standalone dashboard's, the project bundle and a data
deposit. Each read the folder its own way, so the bundle carried the 200
snapshots the live view keeps, and the Files page put what `--rerun` set
aside among the analyses that replaced it.

A path is read as the study writes it, relative to the study's folder.
A study of several runs keeps each in `runs/<id>/`, read as that run's.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

__all__ = ["FILTERS", "KINDS", "PHASES", "filter_of", "is_scratch", "kind_of", "label_of",
           "place"]

#: The phases in the order a study runs, then what is kept beside them.
PHASES: tuple[tuple[str, str], ...] = (
    ("setup", "Setup"),
    ("simulation", "Simulation"),
    ("analysis", "Analysis"),
    ("report", "Report"),
    ("saved", "Kept from the Viewer"),
    ("deposit", "Data deposits"),
    ("record", "Run record"),
    ("previous", "Set aside by --rerun"),
    ("scratch", "Viewer and live scratch"),
)

#: Top-level folders and what phase wrote them.
_PHASE_OF_FOLDER = {
    "setup": "setup", "shared_setup": "setup", "seeds": "setup",
    "simulation": "simulation", "joined": "simulation", "seed_pull": "simulation",
    "analysis": "analysis", "comparison": "analysis", "free_energy": "analysis",
    "pmf": "analysis",
    "report": "report",
    "scenes": "saved", "movies": "saved",
    "deposit": "deposit",
    "previous": "previous", "superseded": "previous",
}

#: Kept with the study by the Viewer, at its root: what a person made.
_SAVED_NAMES = frozenset({"viewer_views.json", "viewer_selections.json", "study_tags.json"})

#: What the Viewer and the live view write, and write again when asked:
#: the frames sent to the browser, the snapshots of a run as it goes, and
#: what the Viewer computed from the frames. Each is rebuilt from the
#: trajectory when it is next needed, and none is a result.
_SCRATCH_FOLDERS = frozenset({"live_frames", "frames_pieces"})
_SCRATCH_TOP = frozenset({"viewer_runs", "viewer_beside"})
_SCRATCH_IN_SIMULATION = frozenset({
    "live_frame_history.json", "chain_contacts.json", "contact_map.json",
    "backbone_angles.json", "motion_modes.npz",
})
_FRAMES = re.compile(r"^frames([._].*)?$")


def is_scratch(rel: str) -> bool:
    """Whether a file is the Viewer's or the live view's scratch."""
    parts = PurePosixPath(rel).parts
    if len(parts) >= 2 and parts[0] == "runs":
        parts = parts[2:]
    if not parts:
        return False
    if parts[0] in _SCRATCH_TOP:
        return True
    if any(part in _SCRATCH_FOLDERS for part in parts[:-1]):
        return True
    name = parts[-1]
    if len(parts) >= 2 and parts[-2] == "simulation":
        return name in _SCRATCH_IN_SIMULATION or bool(_FRAMES.match(name))
    return False


def place(rel: str) -> tuple[str, str, str]:
    """The phase a file belongs to, the run it is of ("" for the study's
    own) and its path within that run."""
    parts = PurePosixPath(rel).parts
    run = ""
    if len(parts) >= 3 and parts[0] == "runs":
        run, parts = parts[1], parts[2:]
    inner = "/".join(parts)
    if not parts:
        return "record", run, inner
    top = parts[0] if len(parts) > 1 else ""
    if top in ("previous", "superseded"):
        return "previous", run, inner
    if is_scratch(inner):
        return "scratch", run, inner
    if top.startswith("segment-") or inner == "stopping.json":
        return "simulation", run, inner
    if top in _PHASE_OF_FOLDER:
        return _PHASE_OF_FOLDER[top], run, inner
    if not top and parts[0] in _SAVED_NAMES:
        return "saved", run, inner
    return "record", run, inner


#: What a file holds, from its name.
KINDS: tuple[str, ...] = ("structure", "trajectory", "state", "data", "figure", "doc",
                          "slides", "page", "archive", "log", "config")

_KIND_BY_SUFFIX = {
    **dict.fromkeys((".pdb", ".cif", ".mmcif", ".gro", ".sdf", ".mol", ".mol2", ".pqr",
                     ".psf", ".prmtop", ".parm7", ".top", ".itp", ".mvsx"), "structure"),
    **dict.fromkeys((".dcd", ".xtc", ".trr", ".nc", ".ncdf", ".netcdf", ".h5", ".lh5"),
                    "trajectory"),
    **dict.fromkeys((".chk", ".rst", ".rst7", ".cpt"), "state"),
    **dict.fromkeys((".dat", ".csv", ".tsv", ".npy", ".npz", ".dx", ".xvg", ".colvar"),
                    "data"),
    **dict.fromkeys((".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm"),
                    "figure"),
    **dict.fromkeys((".md", ".markdown", ".pdf", ".txt", ".rst_txt", ".bib"), "doc"),
    ".pptx": "slides",
    **dict.fromkeys((".html", ".htm"), "page"),
    **dict.fromkeys((".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz"), "archive"),
    **dict.fromkeys((".log", ".out", ".err"), "log"),
    **dict.fromkeys((".json", ".yml", ".yaml", ".toml", ".xml", ".ini", ".cfg", ".sha256"),
                    "config"),
}


def kind_of(rel: str) -> str:
    """What a file holds: a structure, a trajectory, a state to continue
    from, data, a figure, a document, slides, a page, an archive, a log, or
    a configuration and record."""
    path = PurePosixPath(rel)
    name = path.name.lower()
    if path.suffix.lower() == ".xml" and name.startswith("state"):
        return "state"
    if name == "colvar" or name.startswith("colvar."):
        return "data"
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "config")


#: The filters the Files page offers, each over the kinds it shows.
FILTERS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("structure", "Structures", frozenset({"structure", "state"})),
    ("trajectory", "Trajectories", frozenset({"trajectory"})),
    ("data", "Data", frozenset({"data"})),
    ("figure", "Figures", frozenset({"figure"})),
    ("doc", "Documents", frozenset({"doc", "slides", "page", "archive"})),
    ("log", "Logs", frozenset({"log"})),
    ("config", "Configs and records", frozenset({"config"})),
)


def filter_of(kind: str) -> str:
    """The filter a kind of file is shown under."""
    for key, _, kinds in FILTERS:
        if kind in kinds:
            return key
    return "config"


#: What a file is, where its name does not say: by its path in its run.
_LABELS = {
    "setup/input.pdb": "Deposited structure, as given",
    "setup/prepared.pdb": "Prepared protein: repaired and protonated",
    "setup/retained.pdb": "Heterogens kept",
    "setup/solvated.pdb": "Solvated system",
    "setup/topology.pdb": "Topology of the solvated system",
    "setup/system.xml": "Force field and system, as OpenMM built it",
    "setup/state.xml": "Starting state: positions and velocities",
    "setup/setup_parameters.json": "Setup's settings and what it decided",
    "simulation/production.dcd": "Production trajectory",
    "simulation/trajectory_topology.pdb": "Topology of the trajectory: the atoms it saved",
    "simulation/topology.pdb": "Simulated system, solvated",
    "simulation/state_final.xml": "Final state: positions, velocities and box",
    "simulation/state_minimized.xml": "State after minimisation",
    "simulation/checkpoint.chk": "Checkpoint, for recovery by hand",
    "simulation/checkpoint.chk.json": "Checkpoint's record: the step and the platform",
    "simulation/checkpoint.chk.sha256": "Checkpoint's SHA-256",
    "simulation/energy.csv": "Energies of production, as OpenMM wrote them",
    "simulation/equilibration_energy.csv": "Energies of equilibration",
    "simulation/simulation.log": "Simulation log",
    "simulation/simulation_parameters.json": "Settings this simulation used",
    "simulation/cost.json": "How long the run took, and how fast it went",
    "simulation/live_status.json": "How the run stood when it last wrote",
    "simulation/live_metrics.csv": "Energies, temperature and speed as the run went",
    "simulation/live_events.log": "What the run reported as it went",
    "simulation/live_frame.pdb": "The last frame the live view showed",
    "simulation/live_frame_index.json": "Index of the live frame",
    "joined/production.dcd": "The whole trajectory, its segments joined",
    "joined/joined.json": "Which segments were joined, and how",
    "stopping.json": "Where the study stood against its stopping rule",
    "analysis/analysis_manifest.json": "What each analysis produced",
    "report/report.pdf": "Report (PDF)",
    "report/report.md": "Report (Markdown)",
    "report/slides.pptx": "Slide deck",
    "report/slides_outline.md": "Slide outline",
    "report/dashboard.html": "Standalone dashboard: this page as one file",
    "report/project_bundle.zip": "Everything, zipped",
    "report/not_produced.json": "What the report could not produce, and why",
    "report/analysis_summary.png": "Summary figure (PNG)",
    "report/analysis_summary.svg": "Summary figure (SVG)",
    "report/analysis_summary_manifest.json": "What the summary figure shows",
    "manifest.json": "What each phase produced",
    "resolved_config.yml": "The configuration it ran, every default filled in",
    "batch_manifest.json": "The runs of the study and their settings",
    "fastmdxplora.log": "The study's log",
    "exploration.log": "The study's log, as the GUI started it",
    "viewer_views.json": "Views saved in the Viewer",
    "viewer_selections.json": "Selections named in the Viewer",
    "study_tags.json": "The study's tags and note",
    "simulation/frames.dcd": "Frames sent to the Viewer",
    "simulation/frames_topology.pdb": "Topology of the frames sent to the Viewer",
    "simulation/frames_index.json": "Index of the frames sent to the Viewer",
    "simulation/live_frame_history.json": "Index of the live view's snapshots",
    "simulation/chain_contacts.json": "Contacts between chains, as the Viewer computed them",
    "simulation/contact_map.json": "Contact map, as the Viewer computed it",
    "simulation/backbone_angles.json": "Backbone angles, as the Viewer computed them",
    "simulation/motion_modes.npz": "Main motions, as the Viewer computed them",
}

_WHAT_BY_SUFFIX = {
    ".png": "figure (PNG)", ".svg": "figure (SVG)", ".dat": "data", ".csv": "data (CSV)",
    ".npy": "array", ".npz": "arrays", ".pdb": "structure", ".json": "record",
    ".dcd": "trajectory", ".xtc": "trajectory", ".mvsx": "scene", ".mp4": "movie",
    ".webm": "movie", ".dx": "map",
}


def label_of(inner: str) -> str:
    """What a file is, in words, from its path within its run."""
    named = _LABELS.get(inner)
    if named:
        return named
    path = PurePosixPath(inner)
    parts = path.parts
    if parts[:1] == ("previous",) and len(parts) > 1:
        return f"{label_of('/'.join(parts[1:]))}, set aside"
    if parts[:1] == ("superseded",) and len(parts) > 2:
        return f"{label_of('/'.join(parts[2:]))}, of a window run again"
    if parts[:1] == ("viewer_beside",):
        return f"Another study's frames fitted beside this one's for the Viewer: {path.name}"
    if parts[:1] == ("viewer_runs",):
        return (f"Frames of a run fitted for the Viewer: {path.name}" if path.suffix == ".dcd"
                else "Which runs the Viewer plays together")
    if "live_frames" in parts:
        return f"Live view snapshot: {path.name}"
    if "frames_pieces" in parts:
        return f"Frames sent to the Viewer, in pieces: {path.name}"
    if parts[:1] == ("scenes",):
        return f"Scene: {path.stem}"
    if parts[:1] == ("movies",):
        return f"Movie: {path.stem}"
    if path.name == "options.json" and len(parts) >= 2:
        return f"{path.parent.name}: options used"
    if path.name.startswith("frames_superposed_"):
        return f"Frames superposed for the Viewer: {path.name}"
    what = _WHAT_BY_SUFFIX.get(path.suffix.lower())
    if what:
        return f"{path.stem.replace('_', ' ')}: {what}"
    return path.name
