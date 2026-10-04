"""The runs of a study, played together in one Viewer.

A study of several runs opened to an empty Viewer: each run's frames were
inside the run, and the root has no trajectory of its own. Replicas of one
system are best watched together, so the Viewer plays the first run with a
trajectory as it plays any study (its clicks, measurements, pocket and
colours by result are that run's) and renders every other run of the same
atoms beside it, each in a colour of its own.

Each other run is read at the source frames the first run's frames were
taken from, so frame k of every run is the same step of its simulation; a
run with fewer frames ends sooner. Each is made whole as the first run's
frames are, and fitted on the protein's backbone to the first run's first
frame, the frame the first run's own frames are fitted to when superposed
on the backbone: the runs are then superposed on one shared structure. A run
whose atoms are not the first run's (another system, or another setup) is
left out, and why is said.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["RUN_COLOURS", "TOGETHER", "run_shown", "runs_together"]

#: Okabe and Ito's palette without its black and yellow, in the order the
#: Analysis page colours the runs (runs-compared.js), so a run is one colour
#: on both pages.
RUN_COLOURS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00")

#: Where the other runs' fitted frames are written, under the study's root.
TOGETHER = "viewer_runs"
_INDEX = "index.json"

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _colour(index: int) -> str:
    return RUN_COLOURS[index % len(RUN_COLOURS)]


def run_shown(root: Path | str) -> Path | None:
    """The run the Viewer plays for a study of several runs: the first, in
    the order the study planned them, with a trajectory to read. None for a
    study of one run, or one whose runs have no trajectory yet."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.trajectory_frames import _source

    for run in runs_of_a_study(Path(root)) or []:
        folder = Path(run["path"])
        source = _source(folder) if folder.is_dir() else None
        if source is not None and source["kind"] == "trajectory":
            return folder
    return None


def runs_together(root: Path | str, *, most_frames: int, force: bool = False
                  ) -> dict[str, Any] | None:
    """The first run's frames, as the Viewer plays a study's, with every
    other run of the same atoms fitted beside them under ``runs``; None
    where ``root`` is not a study of several runs."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    base = Path(root)
    runs = runs_of_a_study(base)
    if runs is None:
        return None
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(base.resolve()), threading.RLock())
    with lock:
        return _together(base, runs, most_frames=most_frames, force=force)


def _together(base: Path, runs: list[dict[str, Any]], *, most_frames: int,
              force: bool) -> dict[str, Any]:
    from fastmdxplora.gui.runs_compared import _axes_that_differ, _label
    from fastmdxplora.gui.trajectory_frames import _source, _unavailable, frames_info

    main = run_shown(base)
    if main is None:
        said = _unavailable("No run of this study has a trajectory yet.")
        said["runs_together"] = {"runs": [], "excluded": []}
        return said
    axes = _axes_that_differ(runs)
    labels = {run["run_id"]: _label(run, axes) for run in runs}
    order = {run["run_id"]: i for i, run in enumerate(runs)}
    main_id = main.name
    parameters = _load_json(main / "simulation" / "simulation_parameters.json")
    shown = frames_info(main, most_frames=most_frames,
                        simulation_time_ns_total=parameters.get("duration_ns_actual"),
                        force=force)
    if not shown.get("available"):
        shown = dict(shown)
        shown["runs_together"] = {"runs": [], "excluded": []}
        return shown
    sources = {}
    for run in runs:
        folder = Path(run["path"])
        if run["run_id"] != main_id and folder.is_dir():
            sources[run["run_id"]] = _source(folder)
    # A run still writing its frames is not played, so its new frames are
    # not a reason to fit the others again.
    signature = json.dumps([shown.get("signature"),
                            sorted((key, (value or {}).get("signature")
                                    if (value or {}).get("kind") == "trajectory"
                                    else (value or {}).get("kind"))
                                   for key, value in sources.items())])
    out = base / TOGETHER
    cached = _load_json(out / _INDEX)
    if (not force and cached.get("signature") == signature
            and all((out / entry["file"]).is_file() for entry in cached.get("written", []))):
        together = cached["together"]
    else:
        together = _written(base, main, shown, runs, sources, labels, order)
        together_index = {"signature": signature, "together": together,
                          "written": [{"file": f"run_{entry['index']}.dcd"}
                                      for entry in together["runs"] if not entry.get("main")]}
        out.mkdir(exist_ok=True)
        from fastmdxplora.gui.trajectory_frames import _write_text

        _write_text(out / _INDEX, json.dumps(together_index))
    said = dict(shown)
    # The page reloads the frames when this changes: a run finishing, or one
    # written again, is a new set of frames to play together.
    said["source_signature"] = f"{shown.get('source_signature')}|{signature}"
    said["runs_together"] = together
    return said


def _written(base: Path, main: Path, shown: dict[str, Any], runs: list[dict[str, Any]],
             sources: dict[str, Any], labels: dict[str, str], order: dict[str, int]
             ) -> dict[str, Any]:
    """Each other run's frames fitted to the first run's first frame and
    written beside the study, with what was left out and why."""
    import mdtraj as md

    from fastmdxplora.analysis.base import superposed
    from fastmdxplora.gui.trajectory_frames import FRAMES_FILE, FRAMES_TOPOLOGY, _atom_lines, _read
    from fastmdxplora.utils.native_output import suppress_native_output

    simulation = main / "simulation"
    with suppress_native_output():
        first = md.load_dcd(str(simulation / FRAMES_FILE), top=str(simulation / FRAMES_TOPOLOGY),
                            frame=0)
    ours = [_named(line) for line in _atom_lines(_read(simulation / FRAMES_TOPOLOGY))]
    backbone = first.topology.select("protein and backbone")
    indices = [int(i) for i in shown.get("frame_indices") or []]
    main_id = main.name
    entries = [{"run_id": main_id, "label": labels.get(main_id, main_id),
                "colour": _colour(order[main_id]), "frames": len(indices), "main": True,
                "index": order[main_id]}]
    excluded = []
    for run in runs:
        run_id = run["run_id"]
        if run_id == main_id:
            continue
        label = labels.get(run_id, run_id)
        source = sources.get(run_id)
        if source is None or source["kind"] != "trajectory":
            excluded.append({"run_id": run_id, "label": label, "reason": (
                "It is still running: it is shown once it has finished." if source is not None
                else "It has no trajectory yet.")})
            continue
        try:
            frames, why = _frames_of(source, ours, indices)
        except Exception as exc:  # noqa: BLE001 - the run is left out, and why is said
            frames, why = None, f"Its trajectory could not be read: {exc}"
        if frames is None:
            excluded.append({"run_id": run_id, "label": label, "reason": why})
            continue
        if len(backbone) >= 3:
            frames = superposed(frames, atom_indices=backbone, reference=first,
                                ref_atom_indices=backbone)
        target = base / TOGETHER / f"run_{order[run_id]}.dcd"
        target.parent.mkdir(exist_ok=True)
        from fastmdxplora.gui.trajectory_frames import _write_dcd

        _write_dcd(frames, target)
        entries.append({"run_id": run_id, "label": label, "colour": _colour(order[run_id]),
                        "frames": int(frames.n_frames), "main": False, "index": order[run_id]})
    fitted = (f"on the protein's backbone ({len(backbone):,} atoms) to the first frame of "
              f"{labels.get(main_id, main_id)}" if len(backbone) >= 3 else None)
    return {"runs": entries, "excluded": excluded, "fitted": fitted,
            "first": labels.get(main_id, main_id)}


def _named(line: str) -> str:
    """What an atom is, by its PDB line: name, residue, chain and number."""
    return line[12:27]


def _frames_of(source: dict[str, Any], ours: list[str], indices: list[int]
               ) -> tuple[Any, str | None]:
    """A run's frames at the first run's source frames, made whole, with
    the first run's atoms; or why there are none."""
    import mdtraj as md

    from fastmdxplora.analysis.loading import _made_whole
    from fastmdxplora.gui.trajectory_frames import _atom_lines, _read
    from fastmdxplora.utils.native_output import suppress_native_output

    topology_path: Path = source["topology"]
    with suppress_native_output():
        whole = md.load_topology(str(topology_path))
    kept = whole.select("not water")
    if len(kept) == 0:
        kept = np.arange(whole.n_atoms)
    lines = _atom_lines(_read(topology_path).split("\nENDMDL", 1)[0])
    if len(lines) != whole.n_atoms or len(kept) != len(ours):
        return None, "Its atoms are not those of the run shown."
    if [_named(lines[int(i)]) for i in kept] != ours:
        return None, "Its atoms are not those of the run shown."
    with suppress_native_output():
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            total = len(handle)
        wanted = [frame for frame in indices if frame < total]
        if not wanted:
            return None, "Its trajectory has no frames yet."
        xyz, lengths, angles = [], [], []
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            for frame in wanted:
                handle.seek(frame)
                coordinates, length, angle = handle.read(1, atom_indices=kept)
                xyz.append(coordinates[0])
                lengths.append(None if length is None else length[0])
                angles.append(None if angle is None else angle[0])
    boxed = all(length is not None and np.all(np.asarray(length) > 0) for length in lengths)
    frames = md.Trajectory(
        np.asarray(xyz, dtype=np.float32) / 10.0, whole.subset(kept),
        unitcell_lengths=np.asarray(lengths, dtype=np.float32) / 10.0 if boxed else None,
        unitcell_angles=np.asarray(angles, dtype=np.float32) if boxed else None)
    if boxed:
        frames = _made_whole(frames)
    return frames, None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
