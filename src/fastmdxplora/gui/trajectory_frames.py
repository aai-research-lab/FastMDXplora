"""The trajectory for the browser as binary frames: a topology and a DCD.

The viewer was sent a multi-model PDB, about 81 bytes an atom a frame,
parsed whole as text, and capped at a million atoms times frames, which
gave a 116,000-atom system eight frames. As binary a frame is 12 bytes an
atom (three 32-bit coordinates), so a topology is sent once and the frames
as a DCD, bounded by :data:`BINARY_ATOM_FRAMES` at about 120 MB: a
5,000-atom protein gets 2,000 frames and a 116,000-atom system 86.

What is sent, in order of preference: a study's joined trajectory, else its
production trajectory, else, while a run is going, the snapshots it has
written; solvent stripped; evenly spaced frames; and made whole and centred
on the protein as the analyses read the trajectory. The topology is the
source's own lines for the atoms kept, so insertion codes and a ligand's
bonds survive, which MDTraj's PDB writer would drop.

Each file is written under a name of its own and renamed into place, so a
reader never sees half of one, and two servers writing the same study at
once (two dashboards on one folder) each finish, the last one's copy kept.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["BINARY_ATOM_FRAMES", "FRAMES_FILE", "FRAMES_TOPOLOGY", "frames_info",
           "frames_for_binary"]

FRAMES_FILE = "frames.dcd"
FRAMES_TOPOLOGY = "frames_topology.pdb"
FRAMES_INDEX = "frames_index.json"

#: The most atoms times frames sent as binary: 12 bytes each, about 120 MB.
BINARY_ATOM_FRAMES = 10_000_000

#: The most frames sent, whatever the system's size.
MOST_FRAMES = 2000

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def frames_for_binary(n_atoms: int, most: int = MOST_FRAMES) -> int:
    """How many frames of a system this size are sent: as many as fit in
    :data:`BINARY_ATOM_FRAMES`, at most ``most``, never fewer than two."""
    most = max(2, min(MOST_FRAMES, int(most)))
    if n_atoms <= 0:
        return most
    return max(2, min(most, BINARY_ATOM_FRAMES // n_atoms))


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "playback_available": False, "reason": reason, "n_atoms": 0,
            "n_frames_total": 0, "n_frames_browser": 0, "frame_indices": [],
            "frame_times_ns": []}


def frames_info(output_dir: str | Path, *, most_frames: int = MOST_FRAMES,
                simulation_time_ns_total: float | None = None,
                force: bool = False) -> dict[str, Any]:
    """The binary frames for the study at ``output_dir``, written beside its
    simulation if they are not already there for this source, or why there
    are none."""
    out = Path(output_dir)
    key = str(out.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        return _frames_info(out, most_frames=most_frames,
                            simulation_time_ns_total=simulation_time_ns_total, force=force)


def _source(out: Path) -> dict[str, Any] | None:
    """Where the frames come from: the joined trajectory, then production,
    then the snapshots a run still going has written. A study that saved a
    subset of its atoms is read with the topology of what was saved."""
    from fastmdxplora.gui.live_frames import read_live_frame_history

    simulation = out / "simulation"
    saved = simulation / "trajectory_topology.pdb"
    topology = saved if saved.is_file() else simulation / "topology.pdb"
    trajectory = simulation / "production.dcd"
    status = _load_json(simulation / "live_status.json")
    finished = str(status.get("status") or "").lower() in {
        "completed", "complete", "ok", "success", "succeeded"}
    joined = _load_json(out / "joined" / "joined.json")
    joined_dcd = out / "joined" / "production.dcd"
    if joined and joined_dcd.is_file() and joined_dcd.stat().st_size > 0:
        trajectory = joined_dcd
        named = Path(str(joined.get("topology") or ""))
        if named.is_file():
            topology = named
        finished = True
    history = [record for record in read_live_frame_history(simulation).get("frames", [])
               if isinstance(record, dict)]
    if len(history) >= 2 and not finished:
        return {"kind": "live-history", "records": history, "simulation": simulation,
                "signature": f"history:{len(history)}:{history[-1].get('sequence')}:"
                             f"{history[-1].get('mtime_ns')}"}
    if topology.is_file() and trajectory.is_file() and trajectory.stat().st_size > 0:
        return {"kind": "trajectory", "trajectory": trajectory, "topology": topology,
                "records": history, "simulation": simulation,
                "signature": f"{trajectory.parent.name}/{trajectory.name}:"
                             f"{_stamp(trajectory)}:{topology.name}:{_stamp(topology)}"}
    if len(history) >= 2:
        return {"kind": "live-history", "records": history, "simulation": simulation,
                "signature": f"history:{len(history)}:{history[-1].get('sequence')}:"
                             f"{history[-1].get('mtime_ns')}"}
    return None


def _frames_info(out: Path, *, most_frames: int, simulation_time_ns_total: float | None,
                 force: bool) -> dict[str, Any]:
    simulation = out / "simulation"
    source = _source(out)
    if source is None:
        return _unavailable("There is no trajectory, and fewer than two frames written.")
    signature = f"{source['signature']}:most={int(most_frames)}"
    index = simulation / FRAMES_INDEX
    if not force:
        cached = _load_json(index)
        if (cached.get("available") and cached.get("signature") == signature
                and (simulation / FRAMES_FILE).is_file()
                and (simulation / FRAMES_TOPOLOGY).is_file()):
            return cached
    try:
        if source["kind"] == "trajectory":
            try:
                said = _from_trajectory(source, simulation, most_frames,
                                        simulation_time_ns_total)
            except Exception:  # noqa: BLE001 - the snapshots stand in, or it is said
                # A trajectory that cannot be read now (being written, or
                # cut short) is played from the snapshots the run wrote.
                if len(source["records"]) < 2:
                    raise
                source = dict(source, kind="live-history",
                              signature=f"history:{len(source['records'])}:"
                                        f"{source['records'][-1].get('sequence')}:"
                                        f"{source['records'][-1].get('mtime_ns')}")
                signature = f"{source['signature']}:most={int(most_frames)}"
                said = _from_history(source, simulation, most_frames)
        else:
            said = _from_history(source, simulation, most_frames)
    except Exception as exc:  # noqa: BLE001 - the viewer is told, not broken
        return _unavailable(f"The frames could not be written: {exc}")
    if not said.get("available"):
        return said
    # The names the page reads: the dashboard's transport and a chart's
    # point opening its frame (`source_kind` "production-dcd" is a
    # trajectory's frames).
    said.update(signature=signature, source_signature=signature, playback_available=True,
                source_kind="production-dcd" if source["kind"] == "trajectory"
                else source["kind"],
                compiled_at=time.time(), topology=FRAMES_TOPOLOGY, coordinates=FRAMES_FILE)
    _write_json(index, said)
    return said


def _from_trajectory(source: dict[str, Any], simulation: Path, most_frames: int,
                     total_ns: float | None) -> dict[str, Any]:
    import mdtraj as md

    from fastmdxplora.analysis.loading import _made_whole
    from fastmdxplora.utils.native_output import suppress_native_output

    topology_path: Path = source["topology"]
    with suppress_native_output():
        whole = md.load_topology(str(topology_path))
        shown = whole.select("not water")
        if len(shown) == 0:
            shown = np.arange(whole.n_atoms)
        topology = whole.subset(shown)
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            total = len(handle)
        if total < 1:
            return _unavailable("The trajectory has no frames.")
        indices = _even(total, frames_for_binary(len(shown), most_frames))
        xyz, lengths, angles = [], [], []
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            for frame in indices:
                handle.seek(frame)
                coordinates, length, angle = handle.read(1, atom_indices=shown)
                xyz.append(coordinates[0])
                lengths.append(None if length is None else length[0])
                angles.append(None if angle is None else angle[0])
    boxed = all(length is not None and np.all(np.asarray(length) > 0) for length in lengths)
    frames = md.Trajectory(
        np.asarray(xyz, dtype=np.float32) / 10.0, topology,
        unitcell_lengths=np.asarray(lengths, dtype=np.float32) / 10.0 if boxed else None,
        unitcell_angles=np.asarray(angles, dtype=np.float32) if boxed else None)
    if boxed:
        frames = _made_whole(frames)
    _write_topology(topology_path, shown, simulation / FRAMES_TOPOLOGY)
    _write_dcd(frames, simulation / FRAMES_FILE)
    return {"available": True, "reason": None, "n_atoms": int(len(shown)),
            "n_frames_total": int(total), "n_frames_browser": len(indices),
            "frame_indices": indices, "frame_times_ns": _times(indices, total, total_ns),
            "made_whole": bool(boxed)}


def _from_history(source: dict[str, Any], simulation: Path, most_frames: int) -> dict[str, Any]:
    import mdtraj as md

    records = source["records"]
    first = _atom_lines(_read(simulation / str(records[-1].get("path") or "")))
    chosen = [records[i] for i in _even(len(records), frames_for_binary(len(first), most_frames))]
    lines: list[str] | None = None
    xyz, used = [], []
    for record in chosen:
        atoms = _atom_lines(_read(simulation / str(record.get("path") or "")))
        if not atoms or (lines is not None and len(atoms) != len(lines)):
            continue
        if lines is None:
            lines = atoms
        xyz.append([(float(a[30:38]), float(a[38:46]), float(a[46:54])) for a in atoms])
        used.append(record)
    if lines is None or len(used) < 2:
        return _unavailable("Fewer than two of the frames written could be read.")
    _write_text(simulation / FRAMES_TOPOLOGY, "\n".join(lines) + "\nEND\n")
    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("UNK", chain)
    for _ in lines:
        topology.add_atom("X", md.element.carbon, residue)
    _write_dcd(md.Trajectory(np.asarray(xyz, dtype=np.float32) / 10.0, topology),
               simulation / FRAMES_FILE)
    return {"available": True, "reason": None, "n_atoms": len(lines),
            "n_frames_total": len(records), "n_frames_browser": len(used),
            "frame_indices": [record.get("frame_index") for record in used],
            "frame_times_ns": [record.get("simulation_time_ns") for record in used],
            "made_whole": False}


def _write_topology(source: Path, kept: Any, target: Path) -> None:
    """The source's own lines for the atoms kept, in order, with the box
    and the bonds among them."""
    keep = {int(index) for index in kept}
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    written: list[str] = []
    serials: set[int] = set()
    position = -1
    since_ter = False
    for line in lines:
        record = line[:6]
        if record.startswith("ENDMDL"):
            break
        if record.startswith("CRYST1"):
            written.append(line)
        elif record in ("ATOM  ", "HETATM"):
            position += 1
            if position in keep:
                written.append(line)
                since_ter = True
                try:
                    serials.add(int(line[6:11]))
                except ValueError:
                    pass
        elif record.startswith("TER") and since_ter:
            written.append("TER")
            since_ter = False
    for line in lines:
        if line.startswith("CONECT"):
            fields = [line[i:i + 5].strip() for i in range(6, len(line), 5)]
            numbers = [int(field) for field in fields if field.isdigit()]
            if numbers and all(number in serials for number in numbers):
                written.append(line)
    _write_text(target, "\n".join(written) + "\nEND\n")


def _temporary(target: Path) -> Path:
    """A name beside ``target`` no other writer is using."""
    return target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def _write_text(target: Path, text: str) -> None:
    temporary = _temporary(target)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def _write_dcd(frames: Any, target: Path) -> None:
    temporary = _temporary(target)
    frames.save_dcd(str(temporary))
    temporary.replace(target)


def _times(indices: list[int], total: int, total_ns: float | None) -> list[float | None]:
    if total_ns is None or total < 2:
        return [None] * len(indices)
    return [float(total_ns) * index / (total - 1) for index in indices]


def _even(count: int, cap: int) -> list[int]:
    if count <= cap:
        return list(range(count))
    if cap <= 2:
        return [0, count - 1]
    return sorted({round(index * (count - 1) / (cap - 1)) for index in range(cap)})


def _atom_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line[:6] in ("ATOM  ", "HETATM")]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _stamp(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload))
