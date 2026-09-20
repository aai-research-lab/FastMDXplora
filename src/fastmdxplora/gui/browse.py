"""Walking the filesystem from the page, because typing a path is a poor ask.

A browser cannot show its own file dialog for a *folder* the server will read:
the dialog it offers uploads files to a page, and this page needs a path the
process can open. So the walking is done here, one directory at a time, and the
page draws it.

Only directories are listed, plus a count of what is inside worth analysing, so
somebody scanning a list of run folders can see which ones hold a trajectory
without opening each. That count is by extension: nothing is read.

This walks the machine the dashboard runs on. The dashboard binds to loopback
by default and disables its controls when it does not, which is the same
boundary that decides whether a run may be started -- listing a directory is no
more revealing than the run that would follow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmdxplora.gui.directory_inspect import (
    TOPOLOGY_SUFFIXES,
    TRAJECTORY_SUFFIXES,
)

__all__ = ["browse", "KINDS"]

#: What a field is asking for. A picker that lists every file in a directory
#: makes somebody read past forty of them to find the one trajectory; a picker
#: that lists none makes them type the path anyway.
KINDS: dict[str, tuple[str, ...]] = {
    "trajectory": TRAJECTORY_SUFFIXES,
    "structure": TOPOLOGY_SUFFIXES,
    "config": (".yml", ".yaml"),
    # PLUMED input is conventionally named plumed.dat; .plumed is what this
    # package itself writes next to a run (umbrella.plumed and its kin), so
    # a script produced by one study can be picked up by the next. A file
    # named anything else can still be typed into the box.
    "plumed": (".dat", ".plumed"),
}

#: Enough to scan by eye. A folder with more subdirectories than this is
#: somewhere to type a path, not somewhere to click through.
_MAX_ENTRIES = 400


#: How far below a listed folder to look for data. Counting only what sits
#: directly inside marks almost nothing: a run keeps its trajectory in
#: `simulation/`, an installed package keeps its data in `<name>/data/`, and
#: the folder somebody is looking at is usually the one above those. Three
#: levels reaches both without turning a click into a walk of the disk.
_LOOK_DEPTH = 3

#: Enough to answer "is there data in here". Past a handful the exact number
#: stops mattering and the cost of finding it does not.
_ENOUGH = 25


def _interesting(directory: Path, depth: int = _LOOK_DEPTH) -> dict[str, Any]:
    """How many trajectories and structures are in here or just below.

    Bounded in both directions -- a few levels down, and stopping once there
    is clearly something -- because this runs once per row of a listing.
    """
    trajectories = structures = 0
    truncated = False

    def walk(here: Path, remaining: int) -> None:
        nonlocal trajectories, structures, truncated
        if remaining < 0 or truncated:
            return
        try:
            entries = list(here.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            if trajectories + structures >= _ENOUGH:
                truncated = True
                return
            if entry.is_dir():
                if not entry.name.startswith("."):
                    walk(entry, remaining - 1)
                continue
            suffix = entry.suffix.lower()
            if suffix in TRAJECTORY_SUFFIXES:
                trajectories += 1
            elif suffix in TOPOLOGY_SUFFIXES:
                structures += 1

    try:
        walk(directory, depth)
    except (OSError, PermissionError):
        return {"trajectories": 0, "structures": 0, "readable": False,
                "truncated": False}
    out: dict[str, Any] = {
        "trajectories": trajectories,
        "structures": structures,
        "readable": True,
        "truncated": truncated,
        # A FastMDXplora study: the folder a run wrote. The markers are
        # what every run leaves at its root, whatever phases it ran.
        "study": _is_study(directory),
    }
    if out["study"]:
        cont = continuation_of(directory)
        if cont:
            # Shown as a continuation of its parent, by the parent's name.
            out["continues"] = Path(str(cont["study"])).name
            out["from_step"] = cont.get("from_step")
    return out


def continuation_of(directory: Path) -> dict[str, Any] | None:
    """What a study continues, from its simulation manifest, or None."""
    import json

    manifest = directory / "simulation" / "simulation_parameters.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cont = data.get("continues") if isinstance(data, dict) else None
    return cont if isinstance(cont, dict) and cont.get("study") else None


def _is_study(directory: Path) -> bool:
    try:
        return any((directory / m).exists()
                   for m in ("manifest.json", "resolved_config.yml", "exploration.yml"))
    except OSError:
        return False


def browse(
    path: str | Path | None = None, kind: str | None = None
) -> dict[str, Any]:
    """What is inside ``path``: the folders always, and the files worth naming.

    ``kind`` says what is being looked for -- a trajectory, a structure, a
    config -- and only those files are listed. Everything else in a working
    directory is noise to somebody trying to find one file.

    With no path, starts at the home directory, which is where somebody's data
    usually is and never somewhere they cannot read.
    """
    root = Path(path).expanduser() if path else Path.home()

    try:
        root = root.resolve()
    except OSError as exc:
        return {"ok": False, "error": f"Cannot resolve {root}: {exc}"}

    if not root.exists():
        return {"ok": False, "error": f"No such folder: {root}"}
    if not root.is_dir():
        # A file was given -- the folder holding it is what was meant.
        root = root.parent

    # A kind narrows the files to one sort, which is what the builder
    # wants. No kind used to mean no files at all -- the picker was built
    # to find a trajectory or a structure and nothing else -- so a person
    # opening it from the Agent to attach a log or a manifest saw folders
    # and no way into them. No kind lists every file the Agent can read.
    if kind:
        wanted = KINDS.get(kind, ())
    else:
        from fastmdxplora.gui.agent_panel import ATTACHABLE_SUFFIXES

        wanted = ATTACHABLE_SUFFIXES

    entries: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if len(entries) < _MAX_ENTRIES:
                    entries.append({"name": entry.name, "path": str(entry),
                                    **_interesting(entry)})
                continue
            if not wanted or entry.suffix.lower() not in wanted:
                continue
            if len(files) >= _MAX_ENTRIES:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            files.append({
                "name": entry.name,
                "path": str(entry),
                "size_bytes": size,
                "size_mb": round(size / 1e6, 2),
            })
    except PermissionError:
        return {"ok": False, "error": f"Not allowed to read {root}"}
    except OSError as exc:
        return {"ok": False, "error": f"Cannot read {root}: {exc}"}

    parent = root.parent
    return {
        "ok": True,
        "path": str(root),
        "parent": str(parent) if parent != root else None,
        "home": str(Path.home()),
        "entries": entries,
        "files": files,
        "kind": kind,
        "truncated": len(entries) >= _MAX_ENTRIES,
        # What this folder itself holds, so somebody can stop as soon as the
        # listing says there is something here.
        **_interesting(root),
    }
