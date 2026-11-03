"""The states a study visited, and how two of them differ, as the Viewer shows them.

The `cluster` analysis labels each frame it analysed with a state, and plots
the labels over time and their populations; which structure a state is, and
how it differs from another, were left to be found frame by frame. Here each
state is given the frames played that it holds (an analysed frame's label
goes to the frame played nearest it in the trajectory), its share of the
frames analysed, and a representative: its medoid, the frame played with
the least summed RMSD to the state's other frames played, on the atoms the
analysis compared. Two states are compared by their representatives: the
second fitted onto the first on the alpha carbons, and each residue
coloured by how far its alpha carbon moved between them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["METHODS", "MOST_FOR_A_MEDOID", "state_difference", "states_of"]

#: The cluster analysis's methods, in the order one is chosen to show.
METHODS = ("kmeans", "hierarchical", "dbscan")
#: The most frames of a state its medoid is chosen among, evenly spaced.
MOST_FOR_A_MEDOID = 300


def _medoids(path: Path) -> dict[int, int]:
    """Each state's medoid as the cluster analysis recorded it: its frame
    among those the analysis was given (`medoid_frame` in
    `cluster_<method>_populations.csv`); none where not recorded."""
    import math

    import pandas as pd

    from fastmdxplora import record_access

    try:
        table = pd.read_csv(path) if record_access.can_read(path) and path.is_file() else None
    except (OSError, ValueError):
        return {}
    if table is None or not {"cluster", "medoid_frame"} <= set(table.columns):
        return {}
    out: dict[int, int] = {}
    for label, at in zip(table["cluster"], table["medoid_frame"]):
        try:
            label, at = int(label), float(at)
        except (TypeError, ValueError):
            continue
        if math.isfinite(at) and at >= 0 and at == int(at):
            out[label] = int(at)
    return out


def states_of(root: str | Path, method: str | None = None) -> dict[str, Any]:
    """Each state the cluster analysis found, with its frames played, its
    share and its representative; or why there are none."""
    from fastmdxplora.protein_names import ALPHA_CARBONS
    import mdtraj as md
    import pandas as pd

    from fastmdxplora.gui.series import analysed_axis, of_the_played_trajectory
    from fastmdxplora.gui.trajectory_frames import _even
    from fastmdxplora.utils.native_output import suppress_native_output

    out = Path(root)
    folder = out / "analysis" / "cluster"
    found = [name for name in METHODS if (folder / f"cluster_{name}.dat").is_file()]
    if not found:
        return {"ok": False, "reason": "The study has no states: the `cluster` analysis "
                                       "was not run."}
    chosen = method if method in found else found[0]
    if not of_the_played_trajectory(out):
        return {"ok": False, "reason": "The states were found in a trajectory other than the "
                                       "one played."}
    simulation = out / "simulation"
    index = _load_json(simulation / "frames_index.json")
    if not index.get("available") or not (simulation / "frames.dcd").is_file():
        return {"ok": False, "reason": "There are no frames to show the states in yet."}
    labels = pd.read_csv(folder / f"cluster_{chosen}.dat")["cluster"].to_numpy(dtype=int)
    analysed, _, _ = analysed_axis(out, len(labels))
    analysed = np.asarray(analysed)
    played = np.asarray([int(i) for i in index.get("frame_indices") or []])
    # Each frame played takes the label of the analysed frame nearest it.
    nearest = np.abs(played[:, None] - analysed[None, :]).argmin(axis=1)
    of_played = labels[nearest]
    record = _load_json(folder / "options.json")
    selection = str(record.get("selection") or ALPHA_CARBONS)
    with suppress_native_output():
        frames = md.load_dcd(str(simulation / "frames.dcd"),
                             top=str(simulation / "frames_topology.pdb"))
    atoms = frames.topology.select(selection)
    if len(atoms) < 3:
        atoms = frames.topology.select(ALPHA_CARBONS)
    times = index.get("frame_times_ns") or []
    recorded = _medoids(folder / f"cluster_{chosen}_populations.csv")
    states = []
    for label in sorted(set(labels.tolist()) - {-1}):
        members = np.where(of_played == label)[0]
        share = float((labels == label).mean())
        representative = None
        given = recorded.get(int(label))
        if len(members) and given is not None and given < len(analysed):
            # The medoid the analysis found among every frame it clustered
            # (`medoid_frame`, the frame given to it, placed in the
            # trajectory as each label is): the state's frame played nearest
            # it. Found again among the frames played, a near tie named
            # another frame than the analysis's own.
            representative = int(members[int(np.argmin(np.abs(played[members] - analysed[given])))])
        elif len(members):
            pool = members[_even(len(members), MOST_FOR_A_MEDOID)]
            pooled = frames[pool]
            summed = [float(md.rmsd(pooled, pooled, frame=k, atom_indices=atoms).sum())
                      for k in range(len(pool))]
            representative = int(pool[int(np.argmin(summed))])
        states.append({"state": int(label), "share": round(share, 4),
                       "frames": int((labels == label).sum()), "played": int(len(members)),
                       "representative": representative,
                       "time_ns": (times[representative] if representative is not None
                                   and representative < len(times) else None)})
    states.sort(key=lambda state: -state["share"])
    noise = int((labels == -1).sum())
    said = (f"The {len(states)} states the cluster analysis's {chosen} found among the "
            f"{len(labels):,} frames it analysed, each shown by its medoid: the frame played "
            f"nearest the one the analysis recorded, or where none is recorded, the frame "
            f"played with the least summed RMSD to the state's others, on `{selection}`.")
    if noise:
        said += f" {noise:,} frames belonged to no state."
    return {"ok": True, "method": chosen, "methods": found, "states": states,
            "unclustered": noise, "said": said,
            "of_played": [int(label) for label in of_played]}


def state_difference(root: str | Path, first: Any, second: Any,
                     frames_file: str | None = None) -> dict[str, Any]:
    """How the second representative differs from the first: the second
    fitted onto the first on the alpha carbons, as a PDB, its RMSD, and each
    residue's alpha carbon's displacement in angstroms, as the Viewer's
    results are given. ``frames_file`` names the frames as the Viewer shows
    them (superposed or not), so the second is placed on the first as shown."""
    from fastmdxplora.protein_names import ALPHA_CARBONS
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import FRAMES_FILE, FRAMES_TOPOLOGY, _atom_lines, _read
    from fastmdxplora.utils.native_output import suppress_native_output

    out = Path(root)
    simulation = out / "simulation"
    name = frames_file or FRAMES_FILE
    if "/" in name or "\\" in name or not name.endswith(".dcd") or not name.startswith("frames"):
        return {"ok": False, "reason": "The frames are named by the Viewer's own words."}
    path = simulation / name
    if not path.is_file():
        return {"ok": False, "reason": "There are no such frames yet."}
    try:
        a, b = int(first), int(second)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "Two frames are compared by their numbers."}
    with suppress_native_output():
        frames = md.load_dcd(str(path), top=str(simulation / FRAMES_TOPOLOGY))
    if not (0 <= a < frames.n_frames and 0 <= b < frames.n_frames):
        return {"ok": False, "reason": f"The frames played are 0 to {frames.n_frames - 1}."}
    alphas = frames.topology.select(ALPHA_CARBONS)
    if len(alphas) < 3:
        return {"ok": False, "reason": "A comparison needs three alpha carbons."}
    one, other = frames[a], frames[b]
    other = other.superpose(one, atom_indices=alphas)
    moved = np.linalg.norm(other.xyz[0, alphas] - one.xyz[0, alphas], axis=1) * 10.0
    rmsd = float(np.sqrt((moved ** 2).mean()))
    # Each residue named as the frames' own lines name it, as the Viewer
    # finds it: chain, number and insertion code.
    lines = _atom_lines(_read(simulation / FRAMES_TOPOLOGY))
    rows = [[lines[int(atom)][21].strip() or None, int(lines[int(atom)][22:26]),
             lines[int(atom)][26].strip(), round(float(value), 3)]
            for atom, value in zip(alphas, moved)]
    pdb = "".join(f"{line[:30]}{x * 10:8.3f}{y * 10:8.3f}{z * 10:8.3f}{line[54:].rstrip()}\n"
                  for line, (x, y, z) in zip(lines, other.xyz[0])) + "END\n"
    high = float(max(moved.max(), 0.5))
    return {"ok": True, "first": a, "second": b, "rmsd_angstrom": round(rmsd, 3), "pdb": pdb,
            "property": {"key": "state-difference", "label": "Moved between the states",
                         "unit": "Å", "low": 0.0, "high": round(high, 3), "absent": None,
                         "values": rows, "source": "the two states compared",
                         "about": (f"How far each residue's alpha carbon is between frame {b} "
                                   f"and frame {a}, fitted on the alpha carbons (RMSD "
                                   f"{rmsd:.2f} Å).")}}


def _load_json(path: Path) -> dict[str, Any]:
    """A record of the study, held to it where the read is: the frames'
    index linked out of it is what was said of the frames when written,
    while they stand (`trajectory_frames._load_json`)."""
    from fastmdxplora.gui.trajectory_frames import _load_json as held

    return held(path)
