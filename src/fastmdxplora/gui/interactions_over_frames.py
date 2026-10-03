"""What holds the ligand, frame by frame, for the Viewer.

The interactions analysis (``pl_interactions``) records how often each
contact between the ligand and the protein was present, and which atoms it
joins: a table read on the Analysis page, apart from the structure it
describes. It now also records the frames each contact was present in
(``pl_interactions_frames.json``, as runs of frames). This gives the Viewer
those runs on the trajectory's own clock, and the atoms of each contact
among the atoms the Viewer plays, so it can show the contacts of the frame
shown in the structure and when each formed and broke under the transport.

Only what the analysis determined is shown: the contacts are its typed,
published criteria, not distances the page works out. The atoms are named
by index in the topology the analysis read, mapped to the frames where the
frames were written from that same topology (``gui/trajectory_frames.py``
records which), each checked by its atom's and residue's names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["KINDS_SAID", "MOST_PAIRS", "interactions_over_frames"]

#: The most contacts sent, the most often present first.
MOST_PAIRS = 40

#: Each kind as a person names it.
KINDS_SAID = {
    "hydrogen_bond": "Hydrogen bond", "salt_bridge": "Salt bridge",
    "hydrophobic": "Hydrophobic", "pi_stacking": "π stacking",
    "pi_cation": "Cation-π", "halogen_bond": "Halogen bond",
    "metal_coordination": "Metal", "water_bridge": "Water bridge",
}


def interactions_over_frames(root: Path | str, most: int = MOST_PAIRS) -> dict[str, Any]:
    """The contacts the study's interactions analysis found, with the frames
    each was present in and its atoms among the frames played, or why there
    are none."""
    from fastmdxplora.gui.series import analysed_axis, of_the_played_trajectory

    base = Path(root)
    folder = base / "analysis" / "pl_interactions"
    try:
        record = json.loads((folder / "pl_interactions_frames.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict) or not isinstance(record.get("pairs"), list):
        if (folder / "pl_interactions.dat").is_file():
            return {"ok": False, "reason": (
                "This study's interactions were analysed before they were recorded frame "
                "by frame: run the pl_interactions analysis again to see them here.")}
        return {"ok": False, "reason": "No protein-ligand interactions were analysed."}
    n = int(record.get("n_frames") or 0)
    if n < 1:
        return {"ok": False, "reason": "The interactions analysis read no frames."}
    frames, x, x_label = analysed_axis(base, n)
    pairs = [p for p in record["pairs"] if isinstance(p, dict)]
    sent = pairs[:max(0, int(most))]
    shown = []
    for pair, atoms in zip(sent, _placed(base, sent)):
        kind = str(pair.get("kind") or "")
        names = f"{pair.get('protein_atom_name') or '?'}-{pair.get('ligand_atom_name') or '?'}"
        shown.append({
            "kind": kind, "said": KINDS_SAID.get(kind, kind),
            "residue": str(pair.get("residue") or "?"),
            "atoms_said": names,
            "occupancy": _finite(pair.get("occupancy")),
            "episodes": [[int(a), int(b)] for a, b in (pair.get("episodes") or [])
                         if 0 <= int(a) <= int(b) < n],
            "atoms": atoms,
        })
    return {"ok": True, "n_frames": n, "frames": frames, "x": x, "x_label": x_label,
            "linked": of_the_played_trajectory(base), "pairs": shown,
            "total_pairs": len(pairs)}


def _placed(base: Path, pairs: list[dict[str, Any]]) -> list[list[int] | None]:
    """Each contact's two atoms, protein first, among the atoms of the frames
    played; ``None`` for a contact whose atoms are not there, or not the ones
    the analysis named."""
    from fastmdxplora.gui.selection import topology_the_analyses_read

    nothing: list[list[int] | None] = [None] * len(pairs)
    where = topology_the_analyses_read(base)
    try:
        said = json.loads((base / "simulation" / "frames_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return nothing
    source, kept = said.get("source_topology"), said.get("shown")
    if where is None or not said.get("available") or not isinstance(source, str) \
            or kept not in ("not water", "all"):
        return nothing
    if Path(source if Path(source).is_absolute() else base / source).resolve() \
            != where.resolve():
        return nothing
    try:
        import mdtraj as md

        topology = md.load_topology(str(where))
    except Exception:  # noqa: BLE001 - the timeline is shown without the atoms
        return nothing
    shown = np.asarray(topology.select(kept))
    placed: list[list[int] | None] = []
    for pair in pairs:
        atoms = []
        for key in ("protein_atom", "ligand_atom"):
            try:
                index = int(pair.get(key))
            except (TypeError, ValueError):
                index = -1
            name = pair.get(f"{key}_name")
            position = int(np.searchsorted(shown, index)) if index >= 0 else len(shown)
            if (position < len(shown) and shown[position] == index
                    and topology.atom(index).name == name):
                atoms.append(position)
        placed.append(atoms if len(atoms) == 2 else None)
    return placed


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None
