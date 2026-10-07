"""A study's files as a data repository takes them, each with its SHA-256.

A deposit for Zenodo, a journal's data repository or a collaborator is one
zip: the files ticked, by what they are (the inputs, the configuration, the
trajectory with the topology it is read with, the final state, the
analyses' numbers, the figures, the report, the records), a README written
from the study's records, and `SHA256SUMS`, which a reader checks every
file against with `sha256sum -c SHA256SUMS`. Never in it: the Viewer's
scratch and the live view's snapshots, what `--rerun` set aside, the
project bundle (a copy of the rest) and an earlier deposit. Left out unless
asked for: the checkpoint, binary and read only by the OpenMM build and
platform that wrote it, and a joined study's segments, which its joined
trajectory is made of. The figures are of one kind, SVG or PNG, each kept
in the other where it has no twin of the kind chosen, and the report keeps
the pictures it shows.

It is written into the study's `deposit/` folder, under a name of its own
and renamed into place, so a reader never sees half of one; one at a time,
and not while the study runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = ["FIGURES", "SETS", "deposit_plan", "readme", "set_of", "sha256_of", "write_deposit"]

_SUMS: dict[tuple[str, int, int], str] = {}
_SUMS_GUARD = threading.Lock()


def sha256_of(path: Path | str) -> str:
    """A file's SHA-256, read a megabyte at a time, once a version of it."""
    path = Path(path)
    info = path.stat()
    key = (str(path.resolve()), info.st_mtime_ns, info.st_size)
    with _SUMS_GUARD:
        if key in _SUMS:
            return _SUMS[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    said = digest.hexdigest()
    with _SUMS_GUARD:
        _SUMS[key] = said
    return said


#: What can be put in a deposit, in the order the README lists it: each
#: set's key, title, what it holds, and whether it is ticked to begin with.
SETS: tuple[tuple[str, str, str, bool], ...] = (
    ("inputs", "Inputs", "the deposited structure, the prepared and solvated system, the force "
     "field as OpenMM built it, the starting state", True),
    ("config", "The configuration", "every setting it ran with, defaults filled in", True),
    ("trajectory", "The trajectory", "with the topology it is read with", True),
    ("segments", "The segments", "each piece the joined trajectory is made of, as it was "
     "run", False),
    ("state", "The final state", "positions, velocities and box at the last step, and after "
     "minimisation", True),
    ("analysis", "Analysis data", "every analysis's numbers and the options it ran with", True),
    ("figures", "Figures", "the analyses' and the report's", True),
    ("report", "The report", "as written: Markdown, PDF where made, slides, the standalone page",
     True),
    ("records", "The records", "manifests, logs, energies and each phase's settings", True),
    ("checkpoint", "Checkpoint", "binary, read only by the OpenMM build and platform that "
     "wrote it", False),
    ("saved", "Kept from the Viewer", "scenes, movies, views and selections", False),
)

#: The figures a deposit carries, one kind of each: a vector figure scales
#: for a journal, a picture opens anywhere. Both doubled every figure.
FIGURES = {"svg": frozenset({".svg"}), "png": frozenset({".png", ".jpg", ".jpeg"})}
_TWINNED = frozenset({".svg", ".png", ".jpg", ".jpeg"})

_NEVER = frozenset({"scratch", "previous", "deposit"})


def set_of(entry: dict[str, Any], trajectory_parts: dict[str, str]) -> str | None:
    """The set a file of the study goes in, or None for what never goes:
    the scratch, what was set aside, the bundle and earlier deposits.
    ``trajectory_parts`` are the trajectories and their topologies, each
    with its set (:func:`_trajectory_parts`)."""
    phase = entry["phase"]
    path = PurePosixPath(entry["path"])
    if phase in _NEVER or path.name == "project_bundle.zip":
        return None
    if entry["path"] in trajectory_parts:
        return trajectory_parts[entry["path"]]
    kind = entry["kind"]
    name = path.name
    if phase == "saved":
        return "saved"
    if name.startswith("checkpoint.chk"):
        return "checkpoint"
    if kind == "figure":
        return "figures"
    if name == "resolved_config.yml":
        return "config"
    if phase == "setup":
        return "records" if path.suffix == ".json" else "inputs"
    if phase == "simulation":
        if kind == "state":
            return "state"
        if name == "topology.pdb":
            return "inputs"
        return "records"
    if phase == "analysis":
        return "records" if name == "analysis_manifest.json" else "analysis"
    if phase == "report":
        return "report"
    return "records"


def _entries(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The study's files as the Files page places them."""
    from fastmdxplora.study_files import kind_of, label_of, place

    out = []
    for record in records:
        phase, run, inner = place(str(record["path"]))
        try:
            size = int(record.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append({"path": str(record["path"]), "phase": phase, "run": run,
                    "kind": kind_of(str(record["path"])), "label": label_of(inner),
                    "size": size, "opens_with": record.get("opens_with") or ""})
    return out


def _trajectory_parts(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Each trajectory and the topology it is read with, and its set: a
    joined study's joined trajectory is "trajectory" and the segments it is
    made of are "segments", so it is not deposited twice over."""
    have = {entry["path"] for entry in entries}
    parts: dict[str, str] = {}
    for entry in entries:
        if entry["kind"] != "trajectory" or entry["phase"] != "simulation":
            continue
        prefix = f"runs/{entry['run']}/" if entry["run"] else ""
        joined = f"{prefix}joined/joined.json" in have and f"{prefix}joined/production.dcd" in have
        key = ("trajectory" if not joined or entry["path"] == f"{prefix}joined/production.dcd"
               else "segments")
        for path in (entry["path"], entry["opens_with"]):
            if path and parts.get(path) != "trajectory":
                parts[path] = key
    return parts


def _embedded(root: Path, entries: list[dict[str, Any]]) -> set[str]:
    """The pictures each report shows, as paths of the study: kept with the
    report, whichever kind of figure is chosen, so it is not deposited
    with its pictures missing."""
    import re

    have = {entry["path"] for entry in entries}
    shown: set[str] = set()
    for entry in entries:
        if entry["phase"] != "report" or PurePosixPath(entry["path"]).suffix != ".md":
            continue
        try:
            text = (Path(root) / entry["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        folder = PurePosixPath(entry["path"]).parent
        for link in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text) + re.findall(
                r"<img[^>]*\ssrc=[\"']([^\"']+)", text):
            if "://" in link or link.startswith(("/", "#", "data:")):
                continue
            parts: list[str] = []
            for part in (folder / link.split("#")[0].split("?")[0]).parts:
                if part == "..":
                    if parts:
                        parts.pop()
                elif part != ".":
                    parts.append(part)
            rel = "/".join(parts)
            if rel in have:
                shown.add(rel)
    return shown


def chosen(entries: list[dict[str, Any]], sets: set[str], figures: str,
           root: Path | None = None) -> list[dict[str, Any]]:
    """The files of the sets ticked, the figures of the kind asked for; a
    figure of the other kind where it has no twin of the kind chosen (a
    rendered structure has no SVG); and with the report, the pictures it
    shows, read from ``root``."""
    keep = FIGURES.get(figures, FIGURES["svg"])
    parts = _trajectory_parts(entries)
    stems = {}
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if path.suffix.lower() in keep:
            stems[path.with_suffix("").as_posix()] = True
    shown = _embedded(root, entries) if root is not None and "report" in sets else set()
    out = []
    for entry in entries:
        key = set_of(entry, parts)
        if key is None:
            continue
        if entry["path"] in shown:
            out.append(entry)
            continue
        if key not in sets:
            continue
        path = PurePosixPath(entry["path"])
        suffix = path.suffix.lower()
        if key == "figures" and suffix in _TWINNED and suffix not in keep \
                and path.with_suffix("").as_posix() in stems:
            continue
        out.append(entry)
    return out


def deposit_name(root: Path, when: datetime | None = None) -> str:
    """`<system>_deposit_<date>`: the study's system as four capitals where
    its record names one, else the study's folder."""
    from fastmdxplora.system_id import system_id

    manifest = _load(Path(root) / "manifest.json")
    system = system_id(manifest.get("system")) if isinstance(manifest, dict) else ""
    base = system or Path(root).name
    base = "".join(c if c.isalnum() or c in "-_" else "_" for c in base) or "study"
    return f"{base}_deposit_{(when or datetime.now(timezone.utc)).strftime('%Y-%m-%d')}"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def readme(root: Path, files: list[dict[str, Any]], *, name: str,
           when: datetime | None = None) -> str:
    """The deposit's README, written from the study's records: what was
    simulated and how, with what software, how to read the trajectory,
    every file and what it is, how to check them, and how to cite."""
    import fastmdxplora

    root = Path(root)
    manifest = _load(root / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    when = when or datetime.now(timezone.utc)
    lines = [f"# {name.replace('_', ' ')}", ""]
    system = manifest.get("system")
    lines.append(f"A molecular dynamics study{f' of {system}' if system else ''}, as FastMDXplora "
                 f"wrote it in the folder `{root.name}`. This deposit was written from the study's "
                 f"records on {when.strftime('%d %B %Y')}.")
    lines.append("")
    methods = _methods(root)
    if methods:
        lines += ["## What was done", "", methods.strip(), ""]
    software = []
    version = manifest.get("version")
    if version:
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        commit = f", commit {source.get('commit')}" if source.get("commit") else ""
        software.append(f"FastMDXplora {version}{commit}")
    environment = manifest.get("environment") if isinstance(manifest.get("environment"), dict) else {}
    for package, said in (("openmm", "OpenMM"), ("mdtraj", "MDTraj"), ("pdbfixer", "PDBFixer"),
                          ("numpy", "NumPy"), ("python", "Python")):
        if environment.get(package):
            software.append(f"{said} {environment[package]}")
    if software:
        lines += ["## Software", "", ", ".join(software) + ".", ""]
    pairs = [(f["path"], f["opens_with"]) for f in files
             if f["kind"] == "trajectory" and f["opens_with"]
             and any(g["path"] == f["opens_with"] for g in files)]
    if pairs:
        lines += ["## Reading the trajectory", "",
                  "Each trajectory holds the atoms its run saved, and is read with the topology "
                  "beside it, with MDTraj for example:", "", "```python", "import mdtraj as md", ""]
        lines += [f'trajectory = md.load("{trajectory}", top="{topology}")'
                  for trajectory, topology in pairs]
        lines += ["```", ""]
    lines += ["## Files", "", "| File | Size | What it is |", "|---|---|---|"]
    for entry in files:
        label = str(entry["label"]).replace("|", "\\|")
        lines.append(f"| `{entry['path']}` | {_human(entry['size'])} | {label} |")
    lines += ["", f"{len(files)} files, {_human(sum(f['size'] for f in files))}. Left out: the "
              "Viewer's scratch and the live view's snapshots, anything set aside when a phase "
              "was run again, and the project bundle, a copy of the rest.", ""]
    lines += ["## Checking the files", "",
              "`SHA256SUMS` gives every file's SHA-256. In this folder:", "", "```",
              "sha256sum -c SHA256SUMS          # Linux",
              "shasum -a 256 -c SHA256SUMS      # macOS", "```", ""]
    citation = manifest.get("citation") or getattr(fastmdxplora, "__citation__", "")
    doi = manifest.get("doi") or getattr(fastmdxplora, "__doi__", "")
    if citation:
        lines += ["## Citing", "", "The software that ran the study:", "", str(citation)
                  + (f" https://doi.org/{doi}" if doi and doi not in str(citation) else ""), ""]
    return "\n".join(lines)


def _methods(root: Path) -> str:
    """The study's methods as its report gives them, or "" where they
    cannot be written (a study of several runs gives each run's)."""
    if (root / "batch_manifest.json").is_file():
        return ("A study of several runs: each run's methods are in its report, "
                "`runs/<run>/report/report.md`, and the runs and their settings in "
                "`batch_manifest.json`.")
    try:
        from fastmdxplora.report.document import methods_prose

        return methods_prose(root) or ""
    except Exception:  # noqa: BLE001 - a README without its methods is still a README
        return ""


def deposit_plan(root: Path, records: list[dict[str, Any]], *, figures: str = "svg",
                 sets: list[str] | None = None) -> dict[str, Any]:
    """What each set would carry, in files and bytes, the README the sets
    ticked would give (``sets``, or those ticked to begin with), and the
    deposit's name."""
    entries = _entries(root, records)
    parts = _trajectory_parts(entries)
    sets_said = []
    for key, title, what, on in SETS:
        files = chosen(entries, {key}, figures)
        sets_said.append({"key": key, "title": title, "what": what, "on": on and bool(files),
                     "files": len(files), "bytes": sum(f["size"] for f in files)})
    left = [e for e in entries if set_of(e, parts) is None]
    name = deposit_name(root)
    if sets is None:
        ticked = {said["key"] for said in sets_said if said["on"]}
    else:
        ticked = set(sets)
    files = chosen(entries, ticked, figures, root)
    return {"ok": True, "name": name, "where": f"deposit/{name}.zip", "sets": sets_said,
            "chosen": {"files": len(files), "bytes": sum(f["size"] for f in files)},
            "figures": figures if figures in FIGURES else "svg",
            "left_out": {"files": len(left), "bytes": sum(e["size"] for e in left)},
            "readme": readme(root, files, name=name)}


#: One deposit written at a time: two at once (two tabs) chose one name.
_WRITING = threading.Lock()


def _still_running(root: Path) -> str:
    """The run of the study still going, said as it is named, or ""."""
    from fastmdxplora.gui.telemetry import status_as_it_stands

    folders = [("The study", root)]
    runs = root / "runs"
    if runs.is_dir():
        folders += [(f"Run {child.name}", child) for child in sorted(runs.iterdir()) if child.is_dir()]
    folders += [(f"Segment {child.name}", child) for child in sorted(root.glob("segment-*"))
                if child.is_dir()]
    for said, folder in folders:
        status = status_as_it_stands(folder)
        if str(status.get("status") or "").lower() in ("running", "starting", "paused"):
            return said
    return ""


def write_deposit(root: Path, records: list[dict[str, Any]], *, sets: list[str],
                  figures: str = "svg", progress: Callable[[str], Any] | None = None
                  ) -> dict[str, Any]:
    """Write the deposit of the sets named into the study's `deposit/`
    folder; what was written, or why nothing was."""
    import zipfile

    root = Path(root).resolve()
    known = {key for key, *_ in SETS}
    asked = {key for key in sets if key in known}
    if not asked:
        return {"ok": False, "error": "Nothing was ticked to deposit."}
    going = _still_running(root)
    if going:
        return {"ok": False, "error": f"{going} is still running: a deposit of it now would "
                "carry a trajectory and analyses still being written."}
    entries = _entries(root, records)
    files = chosen(entries, asked, figures, root)
    if not files:
        return {"ok": False, "error": "The sets ticked hold no files."}
    if not _WRITING.acquire(blocking=False):
        return {"ok": False, "error": "A deposit is being written already; it says when it is done."}
    import tempfile

    temporary: Path | None = None
    try:
        name = deposit_name(root)
        folder = root / "deposit"
        folder.mkdir(exist_ok=True)
        target = folder / f"{name}.zip"
        number = 2
        while target.exists():
            target = folder / f"{name}-{number}.zip"
            number += 1
        handle, named = tempfile.mkstemp(dir=folder, prefix=f".{target.stem}.", suffix=".tmp")
        os.close(handle)
        temporary = Path(named)
        text = readme(root, files, name=target.stem)
        sums = []
        from fastmdxplora.study_files import STORED

        with zipfile.ZipFile(temporary, mode="w", allowZip64=True) as archive:
            for entry in files:
                source = root / entry["path"]
                if progress is not None:
                    progress(entry["path"])
                info = zipfile.ZipInfo.from_file(source, arcname=f"{target.stem}/{entry['path']}")
                info.compress_type = (zipfile.ZIP_STORED if source.suffix.lower() in STORED
                                      else zipfile.ZIP_DEFLATED)
                digest = hashlib.sha256()
                with source.open("rb") as read, archive.open(info, "w", force_zip64=True) as sink:
                    for piece in iter(lambda: read.read(1 << 20), b""):
                        digest.update(piece)
                        sink.write(piece)
                sums.append(f"{digest.hexdigest()}  {entry['path']}")
            data = text.encode("utf-8")
            sums.insert(0, f"{hashlib.sha256(data).hexdigest()}  README.md")
            archive.writestr(f"{target.stem}/README.md", data, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr(f"{target.stem}/SHA256SUMS", "\n".join(sums) + "\n",
                             compress_type=zipfile.ZIP_DEFLATED)
        temporary.replace(target)
        temporary = None
    except OSError as exc:
        return {"ok": False, "error": f"The deposit could not be written: {exc}"}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _WRITING.release()
    rel = target.relative_to(root).as_posix()
    return {"ok": True, "path": rel, "files": len(files),
            "bytes": target.stat().st_size, "sha256": sha256_of(target)}
