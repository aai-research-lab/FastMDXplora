"""A distance measured in the viewer, measured over every frame.

The viewer measures two atoms in the frame on screen: a distance as drawn.
Whether it held across the run is the question behind most such clicks, and
that is the `pair_distance` analysis over the study's trajectory, with the
minimum image applied. This writes the command that runs it, into a folder
of its own beside the study, from the two atoms' selections, each checked to
name exactly one atom in the topology the analyses read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmdxplora import record_access as _records

__all__ = ["over_frames"]


def over_frames(root: Path | str, a: str, b: str, *, relative: bool = False) -> dict[str, Any]:
    """The command that measures the distance between two atoms at every
    frame, or why there is none. Its files are the study's own, read as a
    chart's residues are (`series.topology_held`): in a read held to the
    study, one linked out of it, or named outside it, is refused and named.
    ``relative`` says them from the study's folder, where the command is
    then run (a page beyond this computer is told no path on it)."""
    from fastmdxplora.gui.figure_provenance import _said
    from fastmdxplora.gui.selection import analysed_structure

    base = Path(root)
    picked = [str(a or "").strip(), str(b or "").strip()]
    if not all(picked):
        return {"ok": False, "reason": "Two atoms are needed for a distance."}
    where = analysed_structure(base)
    if not isinstance(where, Path):
        return {"ok": False, "reason": where.replace("a selection", "the atoms")}
    try:
        from fastmdxplora.utils.native_output import topology_quietly

        topology = topology_quietly(where)
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

    analysis = _the_trajectory(base, where)
    if not isinstance(analysis, dict):
        return {"ok": False, "reason": analysis}
    output = _a_folder_of_its_own(base)
    if relative:
        # From the study's folder: no path on this computer is said.
        import os

        here = base.resolve()
        analysis["trajectory"] = os.path.relpath(analysis["trajectory"], here)
        analysis["topology"] = os.path.relpath(analysis["topology"], here)
        output = Path(os.path.relpath(output, here))
    analysis["include"] = ["pair_distance"]
    analysis["options"] = {"pair_distance": {"selection_a": picked[0],
                                             "selection_b": picked[1],
                                             "measure": "closest"}}
    config = {"systems": [{"system": analysis["topology"]}],
              "include_phase": ["analysis"],
              "output": str(output),
              "analysis": analysis}
    command, text = _said(config)
    said = {"ok": True, "command": command, "config": text, "against": where.name,
            "output": config["output"]}
    if relative:
        said["note"] = "Run it in the study's folder: its files are named from there."
    return said


def _the_trajectory(base: Path, topology: Path) -> dict[str, Any] | str:
    """The trajectory and frames the study's analyses read, with the
    structure they selected atoms in (``topology``), else the trajectory
    its simulation wrote; or why there is none. The study's own: one named
    outside it is its own file in that place (a study copied whole), as the
    trajectory a chart's frame opens is (`series.trajectory_of_this_study`),
    else not named."""
    from fastmdxplora.gui.series import _json, trajectory_of_this_study

    manifest = _json(base / "analysis" / "analysis_manifest.json")
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    if resolved.get("trajectory") and resolved.get("topology"):
        named = resolved["trajectory"]
        if not isinstance(named, str):
            return "The trajectory the analyses read is in several files; none is named here."
        trajectory = trajectory_of_this_study(base, manifest, Path(named))
        if trajectory is None:
            return ("The trajectory the analyses read is not one of this study's own records, "
                    "so it is not named here.")
        found = {"trajectory": str(trajectory), "topology": str(topology)}
        for key in ("stride", "first", "last"):
            if resolved.get(key) is not None:
                found[key] = resolved[key]
        return found
    trajectory = base / "simulation" / "production.dcd"
    saved = base / "simulation" / "trajectory_topology.pdb"
    if _records.is_file(trajectory) and _records.is_file(saved):
        return {"trajectory": str(trajectory), "topology": str(saved)}
    return "This study has no trajectory to compute it over."


def _a_folder_of_its_own(base: Path) -> Path:
    """Beside the study, never over it, nor over an earlier measurement."""
    stem = base.resolve().parent / f"{base.name}_distance"
    folder, n = stem, 2
    while folder.exists():
        folder = stem.parent / f"{stem.name}_{n}"
        n += 1
    return folder
