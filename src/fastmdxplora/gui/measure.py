"""A distance measured in the viewer, measured over every frame.

The viewer measures two atoms in the frame on screen: a distance as drawn.
Whether it held across the run is the question behind most such clicks, and
that is the `pair_distance` analysis over the study's trajectory, with the
minimum image applied. This writes the command that runs it, into a folder
of its own beside the study, from the two atoms' selections, each checked to
name exactly one atom in the topology the analyses read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["over_frames"]


def over_frames(root: Path | str, a: str, b: str) -> dict[str, Any]:
    """The command that measures the distance between two atoms at every
    frame, or why there is none."""
    from fastmdxplora.gui.figure_provenance import _said
    from fastmdxplora.gui.selection import topology_the_analyses_read

    base = Path(root)
    picked = [str(a or "").strip(), str(b or "").strip()]
    if not all(picked):
        return {"ok": False, "reason": "Two atoms are needed for a distance."}
    where = topology_the_analyses_read(base)
    if where is None:
        return {"ok": False, "reason": "No topology to check the atoms against."}
    try:
        import mdtraj as md

        topology = md.load_topology(str(where))
    except Exception as exc:  # noqa: BLE001 - said, not raised
        return {"ok": False, "reason": f"Could not read {where.name}: {exc}"}
    for selection in picked:
        try:
            count = len(topology.select(selection))
        except Exception:  # noqa: BLE001 - MDTraj's parser raises several types
            count = -1
        if count != 1:
            return {"ok": False,
                    "reason": f"{selection!r} does not name one atom in {where.name}, "
                              "the topology the analyses read."}
    if picked[0] == picked[1]:
        return {"ok": False, "reason": "The two atoms are the same atom."}

    analysis = _the_trajectory(base)
    if analysis is None:
        return {"ok": False, "reason": "This study has no trajectory to compute it over."}
    analysis["include"] = ["pair_distance"]
    analysis["options"] = {"pair_distance": {"selection_a": picked[0],
                                             "selection_b": picked[1],
                                             "measure": "closest"}}
    config = {"systems": [{"system": analysis["topology"]}],
              "include_phase": ["analysis"],
              "output": str(_a_folder_of_its_own(base)),
              "analysis": analysis}
    command, text = _said(config)
    return {"ok": True, "command": command, "config": text, "against": where.name,
            "output": config["output"]}


def _the_trajectory(base: Path) -> dict[str, Any] | None:
    """The trajectory and frames the study's analyses read, else the
    trajectory its simulation wrote with the topology beside it."""
    try:
        manifest = json.loads((base / "analysis" / "analysis_manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    if resolved.get("trajectory") and resolved.get("topology"):
        found = {"trajectory": resolved["trajectory"], "topology": resolved["topology"]}
        for key in ("stride", "first", "last"):
            if resolved.get(key) is not None:
                found[key] = resolved[key]
        return found
    trajectory = base / "simulation" / "production.dcd"
    topology = base / "simulation" / "trajectory_topology.pdb"
    if trajectory.is_file() and topology.is_file():
        return {"trajectory": str(trajectory), "topology": str(topology)}
    return None


def _a_folder_of_its_own(base: Path) -> Path:
    """Beside the study, never over it, nor over an earlier measurement."""
    stem = base.resolve().parent / f"{base.name}_distance"
    folder, n = stem, 2
    while folder.exists():
        folder = stem.parent / f"{stem.name}_{n}"
        n += 1
    return folder
