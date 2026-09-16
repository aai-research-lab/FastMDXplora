"""What is in this directory, and what can be done with it.

Someone with a trajectory already -- from GROMACS, from AMBER, from their own
OpenMM script, from a run this software did last week -- should be able to
point the browser at the folder and be offered the analyses, rather than being
asked to reproduce the simulation first. That is most of the field: requiring a
run through this software before the browser is any use excludes everyone who
already has the data.

Nothing here reads a trajectory. Recognising a file by its extension costs
nothing and cannot fail; opening a multi-gigabyte DCD to see whether it is one
would stall the page. The frame count and the atom count come later, when an
analysis is actually asked for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["inspect_directory", "TRAJECTORY_SUFFIXES", "TOPOLOGY_SUFFIXES"]


#: What MDTraj will load as a trajectory. Ordered by how likely a given run is
#: to have produced it, so the first match is usually the right guess.
TRAJECTORY_SUFFIXES = (
    ".dcd", ".xtc", ".trr", ".nc", ".netcdf", ".h5", ".binpos", ".lammpstrj",
    ".mdcrd", ".crd", ".arc", ".tng",
)

#: What can supply a topology. A PDB is both, which is why it appears twice --
#: a folder holding only a PDB has a topology, and possibly a trajectory too.
TOPOLOGY_SUFFIXES = (".pdb", ".prmtop", ".parm7", ".psf", ".gro", ".top", ".cif")

#: A file this software wrote, which says the folder is a previous run.
#:
#: Every name here is checked against what actually writes it, because two
#: of them were not: `status.json` and `setup_manifest.json` were renamed to
#: `live_status.json` and `setup_parameters.json` and this list stayed
#: behind. Half of "a file this software wrote" was a file it never wrote,
#: and nothing failed -- the two live names matched first, so a folder was
#: still recognised and the dead pair simply never fired.
#:
#: `live_status.json` sits in the simulation directory rather than the run
#: root, so it marks a folder someone opened one level in. That is worth
#: recognising: a person browsing to the trajectory is in that directory,
#: not its parent.
_RUN_MARKERS = (
    "resolved_config.yml", "manifest.json",
    "live_status.json", "setup_parameters.json",
)

#: Sizes are shown so a person can tell the production run from the
#: equilibration without opening either.
_MAX_ENTRIES = 200


def _describe(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "name": path.name,
        "path": str(path),
        "size_bytes": size,
        "size_mb": round(size / 1e6, 2),
    }


def inspect_directory(directory: str | Path) -> dict[str, Any]:
    """List what a folder holds, and what that makes possible.

    Returns the trajectories and topologies found, whether the folder looks
    like a previous run of this software, and a suggested pairing -- the
    largest trajectory with a topology beside it, which is nearly always the
    production run and the structure it came from.
    """
    root = Path(directory).expanduser()
    if not root.exists():
        return {"ok": False, "error": f"No such directory: {root}"}
    if not root.is_dir():
        return {"ok": False, "error": f"Not a directory: {root}"}

    trajectories: list[dict[str, Any]] = []
    topologies: list[dict[str, Any]] = []
    markers: list[str] = []

    try:
        entries = sorted(root.rglob("*"))
    except OSError as exc:
        return {"ok": False, "error": f"Could not read {root}: {exc}"}

    for path in entries[: _MAX_ENTRIES * 10]:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in TRAJECTORY_SUFFIXES:
            trajectories.append(_describe(path))
        if suffix in TOPOLOGY_SUFFIXES:
            topologies.append(_describe(path))
        if path.name in _RUN_MARKERS:
            markers.append(path.name)

    # The production run is the long one. Sorting by size finds it without
    # opening anything, and without assuming a naming convention that only
    # this software follows.
    trajectories.sort(key=lambda e: e["size_bytes"], reverse=True)

    suggestion: dict[str, Any] | None = None
    if trajectories and topologies:
        chosen = trajectories[0]
        beside = [t for t in topologies
                  if Path(t["path"]).parent == Path(chosen["path"]).parent]
        suggestion = {
            "trajectory": chosen["path"],
            "topology": (beside or topologies)[0]["path"],
        }
    elif topologies and not trajectories:
        # A lone PDB is a structure to set up and simulate, not one to analyse.
        suggestion = {"trajectory": None, "topology": topologies[0]["path"]}

    return {
        "ok": True,
        "directory": str(root),
        "trajectories": trajectories[:_MAX_ENTRIES],
        "topologies": topologies[:_MAX_ENTRIES],
        "is_previous_run": bool(markers),
        "run_markers": sorted(set(markers)),
        "suggestion": suggestion,
        "can_analyse": bool(trajectories and topologies),
        "can_set_up": bool(topologies),
    }
