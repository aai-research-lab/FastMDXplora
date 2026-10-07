"""The Files page: everything a study wrote, in the order it ran.

The page was five cards of every file the study holds, grouped by kind (a
figure here, its data there), two across, 9,700 pixels long for a study of
435 files, and the file that loads the trajectory was unlabelled in the
folded record. It is now:

- **What you came for**, first: the trajectory with the topology it is read
  with, the report, the bundle, the system as simulated, the final state
  and the configuration, each said in a line and downloaded in a click.
- **The phases in the order the study ran them**, one dense row a file:
  what it is in words, where it is, its size and when it was written. The
  analyses are a row each, under what they study, with the figure, the
  value the analysis determined, and a link to it on the Analysis page.
- **What is kept beside them**, folded: the run's records, what `--rerun`
  set aside, and the scratch: the Viewer's, written again from the
  trajectory when needed, and the live view's snapshots of the run as it
  went; on this computer it can be cleared.

It is rendered here, for the GUI (`GET /api/files-page`) and for the
standalone dashboard the report writes alike, so the two cannot drift;
`static/files-page.js` does what a person does with it: find, filter,
sort, fold, choose a run, and the actions on a file.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode

from fastmdxplora.study_files import FILTERS, PHASES, STORED, filter_of, kind_of, label_of, place

__all__ = ["FOLDED", "Links", "files_model", "human_size", "render", "render_folders"]

#: Sections shown folded until opened: kept, and not what most come for.
FOLDED = frozenset({"record", "previous", "scratch"})

#: What a folded section's heading says of it besides its size.
_PHASE_NOTE = {"previous": "one copy of each phase kept",
               "scratch": "the Viewer's, written again when needed, and the live view's snapshots"}

#: Analyses the study writes outside `analysis/`, by their folder.
_OUTSIDE_ANALYSIS = {"comparison": "The runs compared", "free_energy": "Free energy",
                     "pmf": "Potential of mean force"}


def human_size(size: object) -> str:
    """A size as the rest of the GUI says it."""
    try:
        value = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB"
    return f"{value / 1024 ** 3:.2f} GB"


def _when(mtime: object) -> tuple[str, str]:
    """When a file was written, as the page shows it before its script
    says it in the reader's own time, and as an ISO time."""
    try:
        seconds = float(mtime)  # type: ignore[arg-type]
        # Written after the listing was read (the standalone page, the
        # bundle): no time to say.
        if seconds <= 0:
            return "", ""
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "", ""
    return moment.strftime("%d %b %Y %H:%M UTC"), moment.isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        said = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

_ATOMS: dict[tuple[str, int, int], tuple[int, int]] = {}
_ATOMS_GUARD = threading.Lock()
_WATER = frozenset({"HOH", "WAT", "SOL", "TIP3", "TIP4", "TIP5", "T3P", "T4P", "SPC", "OPC",
                    "H2O", "TP3", "TP4"})


def atoms_in(path: Path) -> tuple[int, int]:
    """How many atoms a PDB holds, and how many of them are water's.
    Counted once a version of the file: a topology is read on every
    listing otherwise."""
    try:
        info = path.stat()
    except OSError:
        return 0, 0
    key = (str(path), info.st_mtime_ns, info.st_size)
    with _ATOMS_GUARD:
        if key in _ATOMS:
            return _ATOMS[key]
    atoms = water = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                if line.startswith((b"ATOM  ", b"HETATM")):
                    atoms += 1
                    if line[17:21].strip().decode("ascii", "replace").upper() in _WATER:
                        water += 1
                elif line.startswith(b"ENDMDL"):
                    break
    except OSError:
        return 0, 0
    with _ATOMS_GUARD:
        _ATOMS[key] = (atoms, water)
    return atoms, water


def _themes() -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Each analysis's title and theme, and the themes in order, as the
    Analysis page groups them."""
    from fastmdxplora.gui.report_dashboard import ANALYSIS_THEMES

    titles = {folder: (title, theme) for theme, members in ANALYSIS_THEMES
              for folder, title in members}
    titles.setdefault("contacts", ("Protein-ligand contacts", "The ligand"))
    for folder, title in _OUTSIDE_ANALYSIS.items():
        titles.setdefault(folder, (title, "Free energy" if folder != "comparison"
                                   else "The runs compared"))
    order = [theme for theme, _ in ANALYSIS_THEMES]
    order.append("The runs compared")
    return titles, order


def _order_of(inner: str) -> int:
    """Where a file comes in the study's order: the files a phase is known
    to write in the order it writes them, then the rest by name."""
    from fastmdxplora.study_files import _LABELS

    known = list(_LABELS)
    return known.index(inner) if inner in known else len(known)


def _manifest_figure(said: object, prefix: str, have: set[str]) -> str | None:
    """The figure an analysis's record names, as the listing says it."""
    text = str(said or "").replace("\\", "/")
    marker = "/analysis/"
    if marker not in f"/{text}":
        return None
    rel = prefix + "analysis/" + f"/{text}".split(marker, 1)[1]
    return rel if rel in have else None


def files_model(root: Path, records: list[dict[str, Any]], *,
                values: dict[str, str] | None = None,
                not_produced: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the page shows, from the file listing (`server._artifact_records`)
    and the study's own records: every file placed, the analyses gathered,
    the key files found, the runs of a study of several."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    root = Path(root)
    runs = [{"id": run["run_id"],
             "label": ", ".join(f"{k} {v}" for k, v in (run.get("values") or {}).items())
             or str(run.get("system") or run["run_id"])}
            for run in (runs_of_a_study(root) or [])]
    run_order = {run["id"]: index for index, run in enumerate(runs)}
    have = {str(record["path"]) for record in records}
    paired: set[str] = set()
    not_theirs: set[str] = set()
    for record in records:
        if record.get("opens_with"):
            paired.update({record["path"], record["opens_with"]})
            whole = PurePosixPath(record["opens_with"]).with_name("topology.pdb").as_posix()
            if record["opens_with"].endswith("/trajectory_topology.pdb") and whole in have:
                not_theirs.add(whole)

    files: list[dict[str, Any]] = []
    for record in records:
        rel = str(record["path"])
        phase, run, inner = place(rel)
        kind = kind_of(rel)
        try:
            size = int(record.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            mtime = float(record.get("mtime") or 0)
        except (TypeError, ValueError):
            mtime = 0.0
        note = ""
        if record.get("opens_with"):
            note = f"read with {PurePosixPath(record['opens_with']).name}"
        elif rel in not_theirs:
            note = "not the trajectory's"
        analysis = ""
        parts = PurePosixPath(inner).parts
        if phase == "analysis":
            if parts[:1] == ("analysis",) and len(parts) >= 3:
                analysis = parts[1]
            elif parts[:1] and parts[0] in _OUTSIDE_ANALYSIS and len(parts) >= 2:
                analysis = parts[0]
        entry = {
            "path": rel, "name": PurePosixPath(rel).name, "label": label_of(inner),
            "phase": phase, "run": run, "kind": kind, "filter": filter_of(kind),
            "size": size, "mtime": mtime, "note": note, "pair": rel in paired,
            "analysis": analysis, "opens_with": record.get("opens_with") or "",
            "order": (run_order.get(run, -1), _order_of(inner), inner),
            "href": record.get("href_here") or record.get("href") or "",
            "download_href": record.get("download_href") or "",
        }
        if record.get("absolute_path"):
            entry["absolute_path"] = record["absolute_path"]
        files.append(entry)
    files.sort(key=lambda f: f["order"])
    for index, entry in enumerate(files):
        entry["order"] = index

    titles, theme_order = _themes()
    analyses: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in files:
        if not entry["analysis"]:
            continue
        key = (entry["run"], entry["analysis"])
        if key not in analyses:
            title, theme = titles.get(entry["analysis"], (entry["analysis"].replace("_", " "),
                                                          "Other"))
            analyses[key] = {"run": entry["run"], "folder": entry["analysis"], "title": title,
                             "theme": theme, "files": [], "size": 0, "mtime": 0.0,
                             "status": "", "message": "", "value": "", "figure": None,
                             "figures": 0}
        group = analyses[key]
        group["files"].append(entry["path"])
        group["size"] += entry["size"]
        group["mtime"] = max(group["mtime"], entry["mtime"])
        if entry["path"].endswith(".png"):
            group["figures"] += 1
    manifests: dict[str, dict[str, Any]] = {}
    for (run, folder), group in analyses.items():
        prefix = f"runs/{run}/" if run else ""
        if run not in manifests:
            manifests[run] = (_load_json(root / prefix / "analysis" / "analysis_manifest.json")
                              .get("results") or {})
        result = manifests[run].get(folder) if isinstance(manifests[run], dict) else None
        if isinstance(result, dict):
            group["status"] = str(result.get("status") or "")
            if group["status"] not in ("ok", ""):
                group["message"] = str(result.get("message") or "")
            group["figure"] = _manifest_figure(result.get("figure_path"), prefix, have)
        if group["figure"] is None:
            pngs = [p for p in group["files"] if p.endswith(".png")]
            named = [p for p in pngs if PurePosixPath(p).stem == folder]
            group["figure"] = (named or pngs or [None])[0]
        if not run and values and values.get(folder):
            group["value"] = values[folder]
    ordered = sorted(analyses.values(), key=lambda a: (
        run_order.get(a["run"], -1),
        theme_order.index(a["theme"]) if a["theme"] in theme_order else len(theme_order),
        list(titles).index(a["folder"]) if a["folder"] in titles else len(titles),
        a["folder"]))

    counts = {key: 0 for key, _, _ in FILTERS}
    for entry in files:
        counts[entry["filter"]] = counts.get(entry["filter"], 0) + 1
    return {"files": files, "analyses": ordered, "runs": runs,
            "keys": _key_files(root, files, runs, not_produced), "counts": counts,
            "total": sum(entry["size"] for entry in files)}


def _key_files(root: Path, files: list[dict[str, Any]], runs: list[dict[str, str]],
               not_produced: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The files most come for, for the study's own folder and each run.
    ``not_produced`` is what the report could not write, where the caller
    knows it better than the record on the disk (the report phase, which
    writes that record after this page)."""
    by_path = {entry["path"]: entry for entry in files}
    scopes = [""] + [run["id"] for run in runs]
    tiles: list[dict[str, Any]] = []
    for run in scopes:
        prefix = f"runs/{run}/" if run else ""
        tiles.extend(_tiles_of(root, prefix, run, by_path, None if run else not_produced))
    return tiles


def _tiles_of(root: Path, prefix: str, run: str, by_path: dict[str, dict[str, Any]],
              not_produced: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    tiles: list[dict[str, Any]] = []

    def there(rel: str) -> dict[str, Any] | None:
        return by_path.get(prefix + rel)

    trajectory = (there("joined/production.dcd") if there("joined/joined.json") else None) \
        or there("simulation/production.dcd")
    record = _load_json(root / prefix / "simulation" / "simulation_parameters.json")
    if trajectory is not None:
        opens_with = by_path.get(trajectory["opens_with"]) if trajectory["opens_with"] else None
        said = []
        frames = record.get("n_production_frames")
        span = record.get("duration_ns_actual")
        if trajectory["path"].startswith(prefix + "joined/"):
            joined = _load_json(root / prefix / "joined" / "joined.json")
            frames = joined.get("n_frames") or joined.get("frames") or None
            span = joined.get("duration_ns") or joined.get("ns") or None
        if isinstance(frames, (int, float)) and frames > 0:
            said.append(f"{int(frames):,} frames")
            if isinstance(span, (int, float)) and span > 0:
                said[-1] += f" over {_number(span)} ns"
                if frames > 1:
                    said.append(f"one every {_number(span * 1000 / frames)} ps")
        if opens_with is not None:
            atoms, water = atoms_in(root / opens_with["path"])
            if atoms:
                said.append(f"{atoms:,} atoms" + ("" if water else ", no water"))
        paths = [trajectory["path"]] + ([opens_with["path"]] if opens_with else [])
        load = ""
        if opens_with is not None:
            load = (f'md.load("{trajectory["path"][len(prefix):]}", '
                    f'top="{opens_with["path"][len(prefix):]}")')
        tiles.append({"key": "trajectory", "run": run, "icon": "trajectory", "wide": True,
                      "title": ("The whole trajectory, with its topology"
                                if "joined/" in trajectory["path"]
                                else "Production trajectory, with its topology"),
                      "what": " · ".join(said), "paths": paths, "load": load,
                      "name": "trajectory"})
    report = there("report/report.pdf") or there("report/report.md")
    if report is not None:
        what = "Written"
        if report["path"].endswith(".md") and there("report/report.pdf") is None:
            said = (not_produced if not_produced is not None
                    else _load_json_list(root / prefix / "report" / "not_produced.json"))
            for item in said:
                if str(item.get("artifact")) == "report.pdf":
                    what += " · no PDF: " + str(item.get("reason") or "").split(". ")[0].rstrip(".")
                    break
        tiles.append({"key": "report", "run": run, "icon": "doc", "title": "Report",
                      "what": what, "when": report["mtime"], "paths": [report["path"]],
                      "read": True})
    bundle = there("report/project_bundle.zip")
    if bundle is not None:
        tiles.append({"key": "bundle", "run": run, "icon": "archive", "title": "Everything, zipped",
                      "what": "The study as the report packed it, without the Viewer's scratch",
                      "paths": [bundle["path"]]})
    solvated = there("setup/solvated.pdb")
    system = there("setup/system.xml")
    if solvated is not None:
        atoms, _ = atoms_in(root / solvated["path"])
        what = f"{atoms:,} atoms, solvated" if atoms else "Solvated"
        if system is not None:
            what += ", with the force field as OpenMM built it"
        tiles.append({"key": "system", "run": run, "icon": "structure",
                      "title": "The system as simulated", "what": what,
                      "paths": [solvated["path"]] + ([system["path"]] if system else []),
                      "name": "system"})
    final = there("simulation/state_final.xml")
    if final is not None:
        tiles.append({"key": "state", "run": run, "icon": "state", "title": "Final state",
                      "what": "Positions, velocities and box at the last step, to continue from",
                      "paths": [final["path"]]})
    config = there("resolved_config.yml")
    if config is not None:
        tiles.append({"key": "config", "run": run, "icon": "config",
                      "title": "The configuration it ran",
                      "what": "Every setting, defaults filled in, to run it again",
                      "paths": [config["path"]], "read": True})
    return tiles


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        said = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in said if isinstance(item, dict)] if isinstance(said, list) else []


def _number(value: float) -> str:
    """A time as written: 0.1, 2.5, 100, not 0.1000 or 1e2."""
    text = f"{value:.4g}"
    return text if "e" not in text else f"{value:g}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_ICON = {
    "structure": '<path d="M12 3l7.5 4.3v9.4L12 21l-7.5-4.3V7.3z"/>'
                 '<path d="M12 12l7.5-4.7M12 12v9M12 12L4.5 7.3"/>',
    "trajectory": '<rect x="3.5" y="5" width="17" height="14" rx="2"/>'
                  '<path d="M7.5 5v14M16.5 5v14M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"/>',
    "state": '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "data": '<rect x="4" y="4.5" width="16" height="15" rx="2"/>'
            '<path d="M4 9.5h16M4 14.5h16M10 4.5v15"/>',
    "figure": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>'
              '<path d="M6.5 16l4-5 3 3.5 2-2.5 2.5 4"/>',
    "log": '<path d="M6 4.5h9l3 3v12H6z"/><path d="M9 11h6M9 14h6M9 17h4"/>',
    "config": '<path d="M5 7h14M5 12h14M5 17h14"/><circle cx="9" cy="7" r="1.8"/>'
              '<circle cx="15" cy="12" r="1.8"/><circle cx="8" cy="17" r="1.8"/>',
    "archive": '<rect x="3.5" y="4.5" width="17" height="5" rx="1.2"/>'
               '<path d="M5 9.5v10h14v-10M10 13h4"/>',
    "doc": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/>',
    "slides": '<rect x="3.5" y="4.5" width="17" height="11" rx="1.5"/><path d="M12 15.5v4M8.5 19.5h7"/>',
    "page": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 8.5h17M6 6.5h.01"/>',
    "download": '<path d="M12 4.5v11M7.5 11l4.5 4.5 4.5-4.5M5 19.5h14"/>',
    "eye": '<path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
           '<circle cx="12" cy="12" r="2.8"/>',
    "more": '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/>'
            '<circle cx="18" cy="12" r="1.3"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
    "folder": '<path d="M3.5 6.5h6l2 2h9v10h-17z"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="1.5"/><path d="M15.5 8.5v-3h-11v11h4"/>',
    "chev": '<path d="M9 6l6 6-6 6"/>',
    "box": '<path d="M12 3.5l8 4v9l-8 4-8-4v-9z"/><path d="M4 7.5l8 4 8-4M12 11.5v9"/>',
    "list": '<path d="M8 6.5h12M8 12h12M8 17.5h12M4 6.5h.01M4 12h.01M4 17.5h.01"/>',
}


def icon(name: str, cls: str = "files-ic") -> str:
    """An icon, taken from the page's one set (:func:`icons`): 435 rows of
    four icons each, written out, were most of a megabyte."""
    name = name if name in _ICON else "doc"
    return f'<svg class="{cls}" aria-hidden="true"><use href="#files-i-{name}"/></svg>'


def icons() -> str:
    """The icons the page draws from, once."""
    return ('<svg class="files-icons" aria-hidden="true" focusable="false" width="0" height="0">'
            + "".join(f'<symbol id="files-i-{name}" viewBox="0 0 24 24">{paths}</symbol>'
                      for name, paths in _ICON.items())
            + "</svg>")


#: What can be read in the side panel, and what the browser shows itself.
PREVIEWED = frozenset({"yml", "yaml", "json", "log", "md", "markdown", "txt", "csv", "tsv", "dat",
                       "pdb", "cif", "py", "toml", "ini", "cfg", "xml", "sdf", "mol2", "sha256",
                       "png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "html"})
OPENED = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "html"})


@dataclass
class Links:
    """How the page reaches a file: through the GUI's server, or beside the
    standalone dashboard, where there is nothing to zip or preview with."""

    standalone: bool = False
    #: Where the page may act on the study: zip, deposit, clear, reveal.
    can: dict[str, bool] = field(default_factory=dict)
    #: The word the person's file manager is known by, for "Show in …".
    reveal_word: str = "folder"
    #: The standalone dashboard's links, by a file's path in the study.
    here: Callable[[str], str] | None = None

    def href(self, entry: dict[str, Any]) -> str:
        if self.here is not None:
            return self.here(entry["path"])
        return entry.get("href") or f"/artifacts/{quote(entry['path'])}"

    def download(self, entry: dict[str, Any]) -> str:
        if self.here is not None:
            return self.here(entry["path"])
        href = entry.get("download_href")
        if href:
            return href
        base = self.href(entry)
        return f"{base}{'&' if '?' in base else '?'}download=1"

    def zipped(self, paths: list[str], name: str) -> str:
        """A zip of several files, or "" where nothing can make one."""
        if self.standalone or not paths or not self.can.get("zip"):
            return ""
        return "/api/files/zip?" + urlencode([("name", name)] + [("path", p) for p in paths])


def _attr(value: object) -> str:
    return escape(str(value), quote=True)


def _row(entry: dict[str, Any], links: Links) -> str:
    """A file: its kind, what it is, where, its size, when, and what can be
    done with it."""
    suffix = entry["name"].rsplit(".", 1)[-1].lower() if "." in entry["name"] else ""
    when, iso = _when(entry["mtime"])
    note = (f'<span class="files-note">{escape(entry["note"])}</span>' if entry["note"] else "")
    acts = []
    if not links.standalone and suffix in PREVIEWED:
        acts.append(f'<button type="button" class="files-act" data-preview title="Read it in the '
                    f'side panel" aria-label="Read {_attr(entry["name"])} in the side panel">'
                    f'{icon("eye")}</button>')
    elif links.standalone and suffix in OPENED:
        acts.append(f'<a class="files-act" href="{_attr(links.href(entry))}" target="_blank" '
                    f'rel="noopener" title="Open it" aria-label="Open {_attr(entry["name"])}">'
                    f'{icon("eye")}</a>')
    acts.append(f'<a class="files-act" href="{_attr(links.download(entry))}" download '
                f'title="Download" aria-label="Download {_attr(entry["name"])}">'
                f'{icon("download")}</a>')
    acts.append(f'<button type="button" class="files-act" data-menu aria-haspopup="menu" '
                f'title="More" aria-label="More for {_attr(entry["name"])}">{icon("more")}</button>')
    where = (f' data-where="{_attr(entry["absolute_path"])}"'
             if entry.get("absolute_path") else "")
    return (
        f'<div class="files-row{" files-pair" if entry["pair"] else ""}" '
        f'data-path="{_attr(entry["path"])}"{where} '
        f'data-href="{_attr(links.href(entry))}" data-filter="{entry["filter"]}" '
        f'data-run="{_attr(entry["run"])}" data-size="{entry["size"]}" '
        f'data-mtime="{entry["mtime"]:.0f}" data-order="{entry["order"]}">'
        f'<span class="files-kind" title="{_attr(entry["kind"])}">{icon(entry["kind"])}</span>'
        f'<div class="files-what"><div class="files-name">{escape(entry["label"])}{note}</div>'
        f'<div class="files-path">{escape(entry["path"])}</div></div>'
        f'<span class="files-size">{escape(human_size(entry["size"]))}</span>'
        + (f'<time class="files-when" datetime="{_attr(iso)}" data-when="{entry["mtime"]:.0f}">'
           f'{escape(when)}</time>' if iso else '<span class="files-when"></span>')
        + f'<div class="files-acts">{"".join(acts)}</div></div>')


def _chip_order(path: str) -> tuple[int, str]:
    """The files an analysis is read by first: its numbers, its vector
    figure, its picture, then its options."""
    suffix = PurePosixPath(path).suffix.lower()
    rank = {".dat": 0, ".csv": 0, ".npz": 1, ".npy": 1, ".pdb": 1, ".svg": 2, ".png": 3}
    return rank.get(suffix, 4), path


def _analysis_row(group: dict[str, Any], entries: list[dict[str, Any]], links: Links,
                  by_path: dict[str, dict[str, Any]], runs: dict[str, str]) -> str:
    figure = by_path.get(group["figure"]) if group["figure"] else None
    thumb = (f'<img class="files-thumb" src="{_attr(links.href(figure))}" alt="" loading="lazy" '
             f'decoding="async">' if figure else f'<span class="files-thumb files-no-thumb">'
             f'{icon("data")}</span>')
    more = (f'<span class="files-thumb-more">+{group["figures"] - 1}</span>'
            if group["figures"] > 1 else "")
    value = (f'<span class="files-val">{escape(group["value"])}</span>' if group["value"] else "")
    state = ""
    if group["status"] and group["status"] != "ok":
        state = (f'<span class="files-state" data-state="{_attr(group["status"])}" '
                 f'title="{_attr(group["message"])}">{escape(group["status"])}</span>')
    run = (f'<span class="files-run">{escape(runs.get(group["run"], group["run"]))}</span>'
           if group["run"] else "")
    names = [by_path[p]["name"] for p in sorted(group["files"], key=_chip_order)
             if p in by_path]
    shown = names[:3]
    chips = "".join(f'<span class="files-chip">{escape(n)}</span>' for n in shown)
    if len(names) > len(shown):
        chips += f'<span class="files-chip">+ {len(names) - len(shown)} more</span>'
    rows = "".join(_row(entry, links) for entry in entries)
    key = f'{group["run"]}/{group["folder"]}'
    ident = "files-a-" + "".join(c if c.isalnum() else "-" for c in key)
    zipped = links.zipped(group["files"], f"{group['folder']}{'-' + group['run'] if group['run'] else ''}")
    acts = []
    if not links.standalone and not group["run"]:
        acts.append(f'<button type="button" class="files-btn" data-open-analysis='
                    f'"{_attr(group["folder"])}">Open<span class="files-wide"> in Analysis'
                    f'</span></button>')
    if zipped:
        acts.append(f'<a class="files-act" href="{_attr(zipped)}" download title="Download '
                    f'this analysis\'s files as a zip" aria-label="Download {_attr(group["title"])}'
                    f' as a zip">{icon("download")}</a>')
    search = f'{group["title"]} {group["folder"]} {group["theme"]}'.lower()
    return (
        f'<div class="files-arow" data-key="{_attr(key)}" data-run="{_attr(group["run"])}" '
        f'data-name="{_attr(group["title"].lower())}" data-size="{group["size"]}" '
        f'data-mtime="{group["mtime"]:.0f}" data-order="{min((by_path[p]["order"] for p in group["files"] if p in by_path), default=0)}" '
        f'data-search="{_attr(search)}">'
        f'<div class="files-arow-head">'
        f'<button type="button" class="files-expand" aria-expanded="false" '
        f'aria-controls="{ident}" title="Its files">{icon("chev", "files-chev")}'
        f'<span class="files-sr">{escape(group["title"])}: its files</span></button>'
        f'<span class="files-thumbs">{thumb}{more}</span>'
        f'<div class="files-what"><div class="files-name">{escape(group["title"])}{run}{value}'
        f'{state}</div><div class="files-chips">{chips}</div></div>'
        f'<span class="files-size">{len(names)} file{"s" if len(names) != 1 else ""}'
        f'<br>{escape(human_size(group["size"]))}</span>'
        f'<div class="files-acts">{"".join(acts)}</div></div>'
        f'<div class="files-rows files-arow-files" id="{ident}" hidden>{rows}</div></div>')


def _tile(tile: dict[str, Any], by_path: dict[str, dict[str, Any]], links: Links,
          runs: dict[str, str]) -> str:
    entries = [by_path[p] for p in tile["paths"] if p in by_path]
    if not entries:
        return ""
    meta = " + ".join(f'<span class="files-mono">{escape(e["path"])}</span>' for e in entries)
    meta += f" · {escape(human_size(sum(e['size'] for e in entries)))}"
    buttons = []
    if tile.get("read") and not links.standalone:
        if tile["key"] == "report":
            buttons.append('<a class="ghost-btn" href="#report" data-view-link="report">Read</a>')
        else:
            buttons.append(f'<button type="button" class="ghost-btn" data-preview-path='
                           f'"{_attr(entries[0]["path"])}">Read</button>')
    if len(entries) > 1:
        zipped = links.zipped([e["path"] for e in entries], tile.get("name") or tile["key"])
        if zipped:
            buttons.append(f'<a class="ghost-btn" href="{_attr(zipped)}" download>'
                           f'{icon("download")} Download {"the pair" if tile["key"] == "trajectory" else "both"}</a>')
        else:
            buttons.extend(f'<a class="ghost-btn" href="{_attr(links.download(e))}" download>'
                           f'{icon("download")} {escape(e["name"])}</a>' for e in entries)
    else:
        buttons.append(f'<a class="ghost-btn" href="{_attr(links.download(entries[0]))}" download>'
                       f'{icon("download")} Download</a>')
    if tile.get("load"):
        buttons.append(f'<button type="button" class="ghost-btn files-code" data-copy='
                       f'"{_attr(tile["load"])}" title="Copy, to read it with MDTraj from the '
                       f'study\'s folder">{escape(tile["load"])}</button>')
    run = (f' <span class="files-run">{escape(runs.get(tile["run"], tile["run"]))}</span>'
           if tile["run"] else "")
    return (
        f'<div class="files-tile{" files-tile-wide" if tile.get("wide") else ""}" '
        f'data-run="{_attr(tile["run"])}" data-key="{_attr(tile["key"])}">'
        f'<div class="files-tile-head">{icon(tile["icon"], "files-tile-ic")}<div>'
        f'<div class="files-tile-title">{escape(tile["title"])}{run}</div>'
        f'<div class="files-tile-what">{_said_with_when(tile)}</div></div></div>'
        f'<div class="files-tile-meta">{meta}</div>'
        f'<div class="files-tile-acts">{"".join(buttons)}</div></div>')


def _said_with_when(tile: dict[str, Any]) -> str:
    """A tile's line, "Written" followed by when, in the reader's time once
    the page's script has run."""
    what = escape(tile["what"])
    if "when" not in tile:
        return what
    when, iso = _when(tile["when"])
    stamp = (f'<time datetime="{_attr(iso)}" data-when="{float(tile["when"]):.0f}">'
             f'{escape(when)}</time>')
    head, _, rest = what.partition(" · ")
    return f"{head} {stamp}" + (f" · {rest}" if rest else "")


def _section(key: str, title: str, meta: str, body: str, extra: str = "") -> str:
    ident = f"files-sec-{key}"
    folded = key in FOLDED
    return (
        f'<section class="files-section" data-phase="{key}">'
        f'<div class="files-sec-head"><button type="button" class="files-fold" '
        f'aria-expanded="{"false" if folded else "true"}" aria-controls="{ident}">'
        f'{icon("chev", "files-chev")}<h2 class="files-sec-title">{escape(title)}</h2></button>'
        f'<span class="files-sec-meta">{meta}</span>{extra}</div>'
        f'<div class="files-rows" id="{ident}"{" hidden" if folded else ""}>{body}</div>'
        f'</section>')


def _meta(entries: list[dict[str, Any]], note: str = "") -> str:
    count = len(entries)
    said = f'{count} file{"s" if count != 1 else ""} · {human_size(sum(e["size"] for e in entries))}'
    return escape(said + (f" · {note}" if note else ""))


def _toolbar(model: dict[str, Any], links: Links) -> str:
    chips = [f'<button type="button" class="files-filter" data-filter="" aria-pressed="true">'
             f'All <b>{len(model["files"])}</b></button>']
    for key, title, _ in FILTERS:
        count = model["counts"].get(key, 0)
        if count:
            chips.append(f'<button type="button" class="files-filter" data-filter="{key}" '
                         f'aria-pressed="false">{escape(title)} <b>{count}</b></button>')
    runs = ""
    if model["runs"]:
        options = "".join(f'<option value="{_attr(run["id"])}">{escape(run["label"])}</option>'
                          for run in model["runs"])
        runs = (f'<label class="files-pick">Run <select data-run-pick>'
                f'<option value="*">Every run</option>{options}</select></label>')
    deposit = ""
    if links.can.get("deposit"):
        deposit = (f'<button type="button" class="primary-btn files-deposit-open" '
                   f'data-deposit>{icon("box")} Prepare a data deposit</button>')
    return (
        '<div class="files-toolbar">'
        f'<label class="files-search">{icon("search")}<span class="files-sr">Find a file</span>'
        '<input type="search" placeholder="Find a file" data-find autocomplete="off" '
        'spellcheck="false"></label>'
        f'{runs}'
        '<label class="files-pick files-sort">Sort <select data-sort>'
        '<option value="order">As the study ran</option><option value="name">By name</option>'
        '<option value="size">Largest first</option><option value="mtime">Newest first'
        '</option></select></label>'
        f'<button type="button" class="ghost-btn files-view" data-view aria-pressed="false">'
        f'{icon("folder")} Folder view</button>{deposit}</div>'
        f'<div class="files-filters" role="toolbar" aria-label="Show only">{"".join(chips)}</div>')


#: The phases the bar of space shows, each in its own colour.
_USAGE = (("setup", "Setup"), ("simulation", "Simulation"), ("analysis", "Analysis"),
          ("report", "Report"), ("other", "Records and kept"), ("previous", "Set aside"),
          ("scratch", "Scratch"))


def _usage(model: dict[str, Any], links: Links) -> str:
    total = model["total"]
    by: dict[str, int] = {}
    for entry in model["files"]:
        key = entry["phase"] if entry["phase"] in dict(_USAGE) else "other"
        by[key] = by.get(key, 0) + entry["size"]
    scratch = by.get("scratch", 0)
    clear = ""
    if links.can.get("clear") and scratch:
        clear = (f'<button type="button" class="ghost-btn files-clear" data-clear>'
                 f'Clear scratch… frees about {escape(human_size(scratch))}</button>')
    bar = "".join(f'<span class="files-b-{key}" style="flex:{by[key]}" data-usage="{key}"></span>'
                  for key, _ in _USAGE if by.get(key))
    legend = "".join(f'<span data-usage-key="{key}"><i class="files-b-{key}"></i>{escape(title)} '
                     f'<b>{escape(human_size(by[key]))}</b></span>'
                     for key, title in _USAGE if by.get(key))
    note = (f'{escape(human_size(scratch))} of it is scratch the Viewer and the live view write '
            'again when needed' if scratch else "")
    described = ", ".join(f"{title} {human_size(by[key])}" for key, title in _USAGE if by.get(key))
    return (
        '<div class="files-usage">'
        f'<div class="files-usage-head"><span class="files-usage-title" data-usage-total>'
        f'{escape(human_size(total))} in this study</span>'
        f'<span class="files-usage-note">{note}</span>{clear}</div>'
        f'<div class="files-bar" role="img" aria-label="{_attr(described)}">{bar}</div>'
        f'<div class="files-legend">{legend}</div></div>')


def render(model: dict[str, Any], links: Links, *, view: str = "phases") -> str:
    """The page: what you came for, then the study by its phases, or by its
    folders (``view="folders"``), or both, one hidden (``"both"``, for the
    standalone dashboard, which has no server to ask for the other)."""
    if not model["files"]:
        return ('<div class="files-empty">This study has not written any files yet.</div>')
    by_path = {entry["path"]: entry for entry in model["files"]}
    runs = {run["id"]: run["label"] for run in model["runs"]}
    tiles = "".join(_tile(tile, by_path, links, runs) for tile in model["keys"])
    parts = [icons(), _toolbar(model, links), _usage(model, links)]
    if tiles:
        parts.append(f'<div class="files-keys"><h2 class="files-keys-title">What you came for</h2>'
                     f'<div class="files-tiles">{tiles}</div></div>')
    parts.append('<div class="files-none" hidden>No file matches.</div>')
    if view in ("phases", "both"):
        parts.append(f'<div class="files-body" data-body="phases">'
                     f'{_by_phase(model, links, by_path, runs)}</div>')
    if view in ("folders", "both"):
        parts.append(f'<div class="files-body" data-body="folders"'
                     f'{" hidden" if view == "both" else ""}>{render_folders(model, links)}</div>')
    return "".join(parts)


def _by_phase(model: dict[str, Any], links: Links, by_path: dict[str, dict[str, Any]],
              runs: dict[str, str]) -> str:
    parts: list[str] = []
    grouped = {entry["path"] for group in model["analyses"] for entry in
               [by_path[p] for p in group["files"] if p in by_path]}
    for key, title in PHASES:
        entries = [e for e in model["files"] if e["phase"] == key]
        if not entries:
            continue
        extra = ""
        if key == "analysis":
            body = []
            theme = None
            for group in model["analyses"]:
                if group["theme"] != theme:
                    theme = group["theme"]
                    body.append(f'<div class="files-theme">{escape(theme)}</div>')
                body.append(_analysis_row(group, [by_path[p] for p in group["files"]
                                                  if p in by_path], links, by_path, runs))
            loose = [e for e in entries if e["path"] not in grouped]
            if loose:
                body.append('<div class="files-theme">The analyses\' record</div>')
                body.extend(_row(e, links) for e in loose)
            if not links.standalone and any(e["name"].endswith(".svg") for e in entries):
                extra = (f'<a class="ghost-btn files-sec-btn" href="/analysis-figures-svg.zip" '
                         f'download>{icon("download")} Every figure as SVG</a>')
            meta = escape(f'{len(model["analyses"])} analyses · ') + _meta(entries)
            parts.append(_section(key, title, meta, "".join(body), extra))
            continue
        if key == "scratch" and links.can.get("clear"):
            extra = '<button type="button" class="ghost-btn files-sec-btn" data-clear>Clear…</button>'
        body = (_in_folders(entries, links, key) if key in ("previous", "scratch")
                else "".join(_row(e, links) for e in entries))
        parts.append(_section(key, title, _meta(entries, _PHASE_NOTE.get(key, "")), body, extra))
    return "".join(parts)


#: A folder of this many files or more is one row, opened to list them.
_GATHERED = 8

_FOLDER_TITLES = {"live_frames": "Live view snapshots",
                  "frames_pieces": "Frames sent to the Viewer, in pieces",
                  "viewer_runs": "Runs fitted for the Viewer",
                  "viewer_beside": "Another study's frames fitted for the Viewer"}


def _in_folders(entries: list[dict[str, Any]], links: Links, phase: str) -> str:
    """What is kept aside, a folder of many files a row: the live view's
    200 snapshots were 200 rows, and what `--rerun` set aside is a phase
    each."""
    folders: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if phase == "previous":
            parts = path.parts
            head = 3 if parts[0] == "runs" else 1
            folder = "/".join(parts[:head + 1]) if len(parts) > head + 1 else str(path.parent)
        else:
            folder = str(path.parent)
        folders.setdefault(folder, []).append(entry)
    rows = []
    for folder, inside in folders.items():
        if phase == "scratch" and len(inside) < _GATHERED:
            rows.extend(_row(e, links) for e in inside)
            continue
        name = PurePosixPath(folder).name
        if phase == "previous":
            title = f"{name.capitalize()}, as it was before it was run again"
            if name == "superseded" or folder.startswith("superseded"):
                title = f"{name}, a window run again"
        else:
            title = _FOLDER_TITLES.get(name, f"{name}/")
        rows.append(_group_row(f"dir:{folder}", title, f"{folder}/", inside, links))
    return "".join(rows)


def _group_row(key: str, title: str, where: str, entries: list[dict[str, Any]],
               links: Links) -> str:
    """Files gathered as one row: a folder of them, opened to list them."""
    ident = "files-g-" + "".join(c if c.isalnum() else "-" for c in key)
    size = sum(e["size"] for e in entries)
    zipped = links.zipped([e["path"] for e in entries], PurePosixPath(where).name or "files")
    acts = (f'<a class="files-act" href="{_attr(zipped)}" download title="Download them as a zip" '
            f'aria-label="Download {_attr(title)} as a zip">{icon("download")}</a>' if zipped else "")
    runs = {e["run"] for e in entries}
    run = next(iter(runs)) if len(runs) == 1 else ""
    return (
        f'<div class="files-arow files-group" data-key="{_attr(key)}" data-run="{_attr(run)}" '
        f'data-size="{size}" data-mtime="{max((e["mtime"] for e in entries), default=0):.0f}" '
        f'data-order="{min((e["order"] for e in entries), default=0)}" '
        f'data-search="{_attr((title + " " + where).lower())}">'
        f'<div class="files-arow-head">'
        f'<button type="button" class="files-expand" aria-expanded="false" aria-controls="{ident}" '
        f'title="Its files">{icon("chev", "files-chev")}<span class="files-sr">{escape(title)}: '
        f'its files</span></button>'
        f'<span class="files-thumbs files-no-thumb">{icon("folder")}</span>'
        f'<div class="files-what"><div class="files-name">{escape(title)}</div>'
        f'<div class="files-path">{escape(where)}</div></div>'
        f'<span class="files-size">{len(entries)} file{"s" if len(entries) != 1 else ""}<br>'
        f'{escape(human_size(size))}</span><div class="files-acts">{acts}</div></div>'
        f'<div class="files-rows files-arow-files" id="{ident}" hidden>'
        f'{"".join(_row(e, links) for e in entries)}</div></div>')


def render_folders(model: dict[str, Any], links: Links) -> str:
    """The page as the study's folders, each a fold of its files."""
    folders: dict[str, list[dict[str, Any]]] = {".": []}
    for entry in sorted(model["files"], key=lambda e: e["path"]):
        folders.setdefault(str(PurePosixPath(entry["path"]).parent), []).append(entry)
    parts = []
    for folder, entries in folders.items():
        if not entries:
            continue
        key = "root" if folder == "." else folder
        ident = "files-dir-" + "".join(c if c.isalnum() else "-" for c in key)
        folded = all(e["phase"] in FOLDED for e in entries)
        name = "The study's folder" if folder == "." else f"{folder}/"
        parts.append(
            f'<section class="files-section files-dir" data-phase="dir">'
            f'<div class="files-sec-head"><button type="button" class="files-fold" '
            f'aria-expanded="{"false" if folded else "true"}" aria-controls="{ident}">'
            f'{icon("chev", "files-chev")}<h2 class="files-sec-title files-mono">{escape(name)}</h2>'
            f'</button><span class="files-sec-meta">{_meta(entries)}</span></div>'
            f'<div class="files-rows" id="{ident}"{" hidden" if folded else ""}>'
            f'{"".join(_row(e, links) for e in entries)}</div></section>')
    return "".join(parts)


def reveal_word() -> str:
    """What the person's file manager is called where they are."""
    import sys

    if sys.platform == "darwin":
        return "Finder"
    if os.name == "nt":
        return "Explorer"
    return "folder"


def zip_entries(out: Any, entries: list[tuple[Path, str]], *,
                extra: list[tuple[str, bytes]] | None = None) -> None:
    """Write a zip of ``entries`` (each a file and its name in the zip) to
    ``out`` as it is read, a megabyte at a time, so a stream that cannot
    seek is written to and no copy is made; then ``extra``, each a name and
    its contents."""
    import shutil
    import zipfile

    with zipfile.ZipFile(out, mode="w", allowZip64=True) as archive:
        for source, name in entries:
            stored = source.suffix.lower() in STORED
            info = zipfile.ZipInfo.from_file(source, arcname=name)
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            with source.open("rb") as handle, archive.open(info, "w", force_zip64=True) as sink:
                shutil.copyfileobj(handle, sink, 1 << 20)
        for name, data in extra or []:
            archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


# ---------------------------------------------------------------------------
# Clearing the scratch
# ---------------------------------------------------------------------------

#: What a run's record says while it goes on, and once it has finished.
_GOING = frozenset({"running", "starting", "paused"})
_DONE = frozenset({"completed", "complete", "ok", "success", "succeeded"})


def _snapshot(inner: str) -> bool:
    """Whether a scratch file is one of the live view's snapshots, written
    by the run as it went and by nothing after: the rest of the scratch is
    the Viewer's, written again from the trajectory when next needed."""
    parts = PurePosixPath(inner).parts
    return "live_frames" in parts[:-1] or parts[-1] == "live_frame_history.json"


def _run_folder(inner: str) -> str:
    """The folder of the run a scratch file is of: the one holding the
    `simulation/` it is in (the study's, a run's, a segment's), or "" for
    the Viewer's fits at the study's root."""
    parts = PurePosixPath(inner).parts
    if "simulation" not in parts[:-1]:
        return ""
    return "/".join(parts[:parts.index("simulation")])


def _why_kept(folder: Path) -> str:
    """Why a run's snapshots are kept, or "" where they can go: a run still
    going writes and reads them; a run with no trajectory has only them to
    play; and a run that failed or ended without saying so may have a
    trajectory cut short, which the Viewer plays the snapshots instead of."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    status = status_as_it_stands(folder)
    said = str(status.get("status") or "").lower()
    if said in _GOING:
        return "it is still running"
    has_trajectory = False
    for trajectory in (folder / "joined" / "production.dcd", folder / "simulation" / "production.dcd"):
        try:
            if trajectory.stat().st_size > 0:
                has_trajectory = True
                break
        except OSError:
            continue
    if not has_trajectory:
        return "it has no trajectory, and the snapshots are all the Viewer has to play"
    if status and said not in _DONE:
        return (f"the run {'was ' + said if said else 'did not say it finished'}, and its "
                "trajectory may be cut short: the snapshots may be all the Viewer can play")
    return ""


def clear_scratch(root: Path, records: list[dict[str, Any]], *, running: bool,
                  dry: bool = False) -> dict[str, Any]:
    """Remove the scratch (`study_files.is_scratch`), or with ``dry`` say
    what would go: the Viewer's, which it writes again from the trajectory
    when next needed, and the live view's snapshots of a run that finished
    with its trajectory, which nothing writes again. Never while the study
    runs, and each run's snapshots judged by that run's own record (a
    study's, a run's of several, a segment's). A link is removed as a link:
    resolved, the file it pointed to went, a trajectory among them."""
    if running:
        return {"ok": False, "error": "The study is running; its scratch is in use.",
                "files": 0, "bytes": 0, "snapshots": 0, "kept": []}
    root = Path(root).resolve()
    going: list[Path] = []
    kept: dict[str, str] = {}
    judged: dict[str, str] = {}
    size = snapshots = 0
    for record in records:
        rel = str(record["path"])
        phase, _, _ = place(rel)
        if phase != "scratch":
            continue
        if _snapshot(rel):
            folder = _run_folder(rel)
            if folder not in judged:
                judged[folder] = _why_kept(root / folder if folder else root)
            if judged[folder]:
                kept[folder] = judged[folder]
                continue
        target = root / rel
        try:
            # Its folder in the study, not where a link leads.
            target.parent.resolve().relative_to(root)
            info = target.lstat()
        except (OSError, ValueError):
            continue
        going.append(target)
        size += info.st_size
        snapshots += _snapshot(rel)
    said = {"ok": True, "files": len(going), "bytes": size, "snapshots": snapshots,
            "kept": [{"run": folder, "why": why} for folder, why in kept.items()]}
    if dry:
        return said
    removed = freed = 0
    for target in going:
        try:
            freed += target.lstat().st_size
            target.unlink()
            removed += 1
        except OSError:
            continue
    # The folders scratch alone filled, now empty.
    for folder in sorted({t.parent for t in going}, key=lambda p: len(p.parts), reverse=True):
        while folder != root and folder.name in ("live_frames", "frames_pieces", "viewer_runs",
                                                 "viewer_beside"):
            try:
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent
    said.update(files=removed, bytes=freed)
    return said
