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
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["BINARY_ATOM_FRAMES", "FRAMES_FILE", "FRAMES_TOPOLOGY", "PIECE_ATOM_FRAMES",
           "SUPERPOSED_ON", "as_xtc", "frames_for_binary", "frames_info", "frames_pieces",
           "pocket_backbone", "superposed_frames", "superposed_name"]

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
            interval = (_interval_ns(out, source) if source["kind"] == "trajectory"
                        and cached.get("source_kind") == "production-dcd" else None)
            if interval:
                # Read again where a record says it: frames written before
                # the times were read from the records carry the old ones,
                # and an analysis run since can say the interval.
                cached["frame_times_ns"] = _times(
                    list(cached.get("frame_indices") or []),
                    int(cached.get("n_frames_total") or 0), None, interval)
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
        kept = "not water"
        shown = whole.select(kept)
        if len(shown) == 0:
            kept = "all"
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
    # Which atoms of which topology the frames are, so an atom clicked in
    # them is named in that topology exactly (gui/selection.py).
    try:
        source_topology = str(topology_path.resolve().relative_to(simulation.parent.resolve()))
    except ValueError:
        source_topology = str(topology_path.resolve())
    # Each frame's box, in angstroms and degrees as the DCD holds it, for the
    # Viewer to show the box of the frame shown: an NPT run's changes.
    cells = ([[round(float(v), 4) for v in (*length, *angle)]
              for length, angle in zip(lengths, angles)] if boxed else None)
    return {"available": True, "reason": None, "n_atoms": int(len(shown)),
            "n_frames_total": int(total), "n_frames_browser": len(indices),
            "frame_indices": indices,
            "frame_times_ns": _times(indices, total, total_ns,
                                     _interval_ns(simulation.parent, source)),
            "made_whole": bool(boxed), "source_topology": source_topology, "shown": kept,
            "cells": cells}


def _from_history(source: dict[str, Any], simulation: Path, most_frames: int) -> dict[str, Any]:
    import mdtraj as md

    from fastmdxplora.gui.live_frames import cell_of, made_whole_frames

    records = source["records"]
    first = _atom_lines(_read(simulation / str(records[-1].get("path") or "")))
    chosen = [records[i] for i in _even(len(records), frames_for_binary(len(first), most_frames))]
    lines: list[str] | None = None
    opened = ""
    xyz, used, cells = [], [], []
    for record in chosen:
        text = _read(simulation / str(record.get("path") or ""))
        atoms = _atom_lines(text)
        if not atoms or (lines is not None and len(atoms) != len(lines)):
            continue
        if lines is None:
            lines, opened = atoms, text
        xyz.append([(float(a[30:38]), float(a[38:46]), float(a[46:54])) for a in atoms])
        cells.append(cell_of(text))
        used.append(record)
    if lines is None or len(used) < 2:
        return _unavailable("Fewer than two of the frames written could be read.")
    # Each snapshot wraps every molecule into the box on its own, so a bound
    # ligand was played a box length from its pocket; the frames are made
    # whole about the protein as a trajectory's are.
    coordinates = np.asarray(xyz, dtype=np.float32)
    try:
        whole = made_whole_frames(opened, coordinates, cells)
    except Exception:  # noqa: BLE001 - played as written rather than not at all
        whole = None
    if whole is not None:
        coordinates = whole
        lines = [f"{line.ljust(54)[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line.ljust(54)[54:]}".rstrip()
                 for line, (x, y, z) in zip(lines, whole[0])]
    _write_text(simulation / FRAMES_TOPOLOGY, "\n".join(lines) + "\nEND\n")
    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("UNK", chain)
    for _ in lines:
        topology.add_atom("X", md.element.carbon, residue)
    _write_dcd(md.Trajectory(coordinates / 10.0, topology), simulation / FRAMES_FILE)
    return {"available": True, "reason": None, "n_atoms": len(lines),
            "n_frames_total": len(records), "n_frames_browser": len(used),
            "frame_indices": [record.get("frame_index") for record in used],
            "frame_times_ns": [record.get("simulation_time_ns") for record in used],
            "made_whole": whole is not None}


#: What the frames can be superposed on, and how a pocket is chosen.
SUPERPOSED_ON = ("backbone", "pocket")
#: What they are fitted to: the first frame played, the structure the run
#: started from (as setup prepared it, the frames' own topology file), or
#: the structure the study was given (``setup/input.pdb``, a deposited
#: entry as it was downloaded).
SUPERPOSED_TO = ("first", "start", "deposited")
#: Windows the superposed frames can be smoothed over, in frames played: a
#: centred moving average of each atom's position, for watching a motion
#: through the jitter of thermal noise.
SMOOTHED_OVER = (1, 3, 5, 9, 15)
_LIGAND_NAME = re.compile(r"^[A-Za-z0-9]{1,4}$")
_BACKBONE = ("N", "CA", "C", "O")


def superposed_name(on: str, ligand: str | None, cutoff_angstrom: Any, to: str = "first",
                    smooth: Any = 1) -> tuple[str | None, float | None, str | None]:
    """The file the frames superposed so are written to, made from the
    request's words alone (never a path), with the cutoff read; or why
    there is none."""
    if on not in SUPERPOSED_ON:
        return None, None, f"Frames are superposed on {' or '.join(SUPERPOSED_ON)}."
    if to not in SUPERPOSED_TO:
        return None, None, ("Frames are fitted to the first frame, the starting structure "
                            "or the deposited structure.")
    window = _window(smooth)
    if window is None:
        return None, None, ("Frames are smoothed over "
                            + ", ".join(str(w) for w in SMOOTHED_OVER[1:]) + " frames, or not.")
    suffix = ("" if to == "first" else f"_to_{to}") + ("" if window == 1 else f"_smooth{window}")
    if on == "backbone":
        return f"frames_superposed_backbone{suffix}.dcd", None, None
    if not ligand or not _LIGAND_NAME.match(ligand):
        return None, None, "A pocket is the ligand's: no ligand was named."
    try:
        cutoff = float(cutoff_angstrom)
    except (TypeError, ValueError):
        cutoff = float("nan")
    if not 1.0 <= cutoff <= 20.0:
        return None, None, "The pocket's cutoff is 1 to 20 \u00c5."
    return f"frames_superposed_pocket_{ligand.upper()}_{cutoff:.2f}{suffix}.dcd", cutoff, None


def _window(smooth: Any) -> int | None:
    try:
        window = int(str(smooth).strip() or 1)
    except (TypeError, ValueError):
        return None
    return window if window in SMOOTHED_OVER else None


def smoothed(xyz: Any, window: int) -> Any:
    """Each frame's coordinates averaged with the ``window // 2`` frames on
    either side, over fewer at the ends where there are fewer: a centred
    moving average, which moves no feature in time."""
    if window <= 1 or len(xyz) < 2:
        return xyz
    half = window // 2
    running = np.concatenate([np.zeros((1,) + xyz.shape[1:], dtype=np.float64),
                              np.cumsum(xyz, axis=0, dtype=np.float64)])
    n = len(xyz)
    starts = np.clip(np.arange(n) - half, 0, n)
    ends = np.clip(np.arange(n) + half + 1, 0, n)
    counts = (ends - starts)[:, None, None]
    return ((running[ends] - running[starts]) / counts).astype(xyz.dtype)


def deposited_structure(output_dir: str | Path) -> Path | None:
    """The structure the study was given, as the B-factor comparison finds
    it: ``setup/input.pdb`` (a deposited entry as it was downloaded), else
    ``setup/structure.pdb``."""
    out = Path(output_dir)
    for candidate in (out / "setup" / "input.pdb", out / "setup" / "structure.pdb",
                      out / "input.pdb"):
        if candidate.is_file():
            return candidate
    return None


def _backbone_keys(lines: list[str]) -> dict[tuple[str, str, str], int]:
    """Each backbone atom of the lines, by its chain, residue number with
    insertion code, and name, to its place among them: the first of
    alternate locations, and the first model."""
    keys: dict[tuple[str, str, str], int] = {}
    for place, line in enumerate(lines):
        name = line[12:16].strip()
        if name not in _BACKBONE or line[16:17] not in (" ", "A", ""):
            continue
        keys.setdefault((line[21:22], line[22:27].strip(), name), place)
    return keys


def _deposited_reference(output_dir: Path, topology_file: Path, atoms: Any
                         ) -> tuple[Any, Any, str] | tuple[None, None, str]:
    """The atoms of ``atoms`` the deposited structure has too, matched by
    chain, residue number and name, with its coordinates for them in nm; or
    why there are none."""
    deposited = deposited_structure(output_dir)
    if deposited is None:
        return None, None, "The study has no deposited structure (setup/input.pdb) to fit to."
    text = _read(deposited)
    first_model = text.split("\nENDMDL", 1)[0]
    theirs = [line for line in _atom_lines(first_model) if line.startswith("ATOM  ")]
    ours = _atom_lines(_read(topology_file))
    their_keys = _backbone_keys(theirs)
    our_keys = {place: key for key, place in _backbone_keys(ours).items()}
    matched = [(int(i), their_keys[our_keys[int(i)]]) for i in atoms
               if int(i) in our_keys and our_keys[int(i)] in their_keys]
    if len(matched) < 3:
        return None, None, ("The deposited structure's backbone could not be matched to the "
                            "frames' by chain, residue number and atom name.")
    try:
        xyz = np.array([[float(theirs[j][30:38]), float(theirs[j][38:46]),
                         float(theirs[j][46:54])] for _, j in matched]) / 10.0
    except ValueError:
        return None, None, "The deposited structure's coordinates could not be read."
    try:
        named = str(deposited.relative_to(output_dir))
    except ValueError:
        named = deposited.name
    said = f"the deposited structure ({named}, {len(matched):,} of {len(atoms):,} atoms matched)"
    return np.array([i for i, _ in matched], dtype=int), xyz, said


def pocket_backbone(frames: Any, ligand: str, cutoff_angstrom: float) -> tuple[Any, list[int]]:
    """The backbone atoms (N, CA, C, O) of each protein residue with a heavy
    atom within ``cutoff_angstrom`` of the ligand's heavy atoms in the first
    frame, and those residues; None for the atoms where the frames hold no
    such ligand."""
    import mdtraj as md

    topology = frames.topology
    heavy = topology.select(f"resname {ligand.upper()} and not element H")
    if len(heavy) == 0:
        return None, []
    protein = topology.select("protein and not element H")
    near = md.compute_neighbors(frames[0], float(cutoff_angstrom) / 10.0, heavy,
                                haystack_indices=protein)[0]
    residues = sorted({topology.atom(int(i)).residue.index for i in near})
    chosen = set(residues)
    backbone = topology.select("protein and backbone")
    atoms = np.array([i for i in backbone if topology.atom(int(i)).residue.index in chosen],
                     dtype=int)
    return atoms, residues


def superposed_frames(output_dir: str | Path, on: str, *, ligand: str | None = None,
                      cutoff_angstrom: float = 5.0, to: str = "first",
                      smooth: Any = 1) -> dict[str, Any]:
    """The frames the viewer plays, each turned and moved onto a reference.

    ``on`` is ``"backbone"`` (N, CA, C and O of the protein) or ``"pocket"``
    (the same atoms of each protein residue with a heavy atom within
    ``cutoff_angstrom`` of the ligand's heavy atoms in the first frame), by
    MDTraj's least-squares fit. ``to`` is the reference: the first frame,
    the structure the run started from, or the deposited structure, whose
    atoms are matched to the frames' by chain, residue number and name.
    ``smooth``, a window of frames from ``SMOOTHED_OVER``, averages each
    atom's fitted position over that many frames centred on each (the fit
    first: an average of a molecule turning is a molecule shrunk). Written
    once beside the frames, and again when they are. Returns
    ``{"ok": True, "file", "said", "atoms", "to", "smooth"}``, or why there
    is none.
    """
    import mdtraj as md

    out = Path(output_dir)
    simulation = out / "simulation"
    name, cutoff, reason = superposed_name(on, ligand, cutoff_angstrom, to, smooth)
    window = _window(smooth) or 1
    if name is None:
        return {"ok": False, "reason": reason}
    frames_file, topology_file = simulation / FRAMES_FILE, simulation / FRAMES_TOPOLOGY
    target = simulation / name
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(out.resolve()), threading.RLock())
    with lock:
        if not (frames_file.is_file() and topology_file.is_file()):
            return {"ok": False, "reason": "There are no frames to superpose yet."}
        from fastmdxplora.utils.native_output import suppress_native_output

        with suppress_native_output():
            frames = md.load_dcd(str(frames_file), top=str(topology_file))
        topology = frames.topology
        backbone = topology.select("protein and backbone")
        if on == "backbone":
            atoms = backbone
            said = f"the protein's backbone ({len(atoms):,} atoms)"
        else:
            atoms, residues = pocket_backbone(frames, ligand, cutoff)
            if atoms is None:
                return {"ok": False, "reason": f"There is no {ligand.upper()} in the frames."}
            said = (f"the backbone of the {len(residues)} residues within {cutoff:g} \u00c5 "
                    f"of {ligand.upper()} in the first frame ({len(atoms):,} atoms)")
        if len(atoms) < 3:
            return {"ok": False, "reason": (
                "The frames have no protein backbone to superpose on." if on == "backbone"
                else f"No protein residue is within {cutoff:g} \u00c5 of {ligand.upper()} in "
                     "the first frame.")}
        reference, reference_atoms = frames, atoms
        if to == "start":
            with suppress_native_output():
                reference = md.load_pdb(str(topology_file))
            said += ", fitted to the structure the run started from"
        elif to == "deposited":
            atoms, xyz, fitted = _deposited_reference(out, topology_file, atoms)
            if atoms is None:
                return {"ok": False, "reason": fitted}
            reference = md.Trajectory(xyz[None].astype(np.float32),
                                      frames.topology.subset(atoms))
            reference_atoms = np.arange(len(atoms))
            said += f", fitted to {fitted}"
        else:
            said += ", fitted to the first frame"
        if window > 1:
            said += f", smoothed over {window} frames"
        fresh = target.is_file() and target.stat().st_mtime_ns >= frames_file.stat().st_mtime_ns
        if not fresh:
            from fastmdxplora.analysis.base import superposed

            # Fitted as the analyses fit, by one function: each frame is
            # turned as well as moved, so its box is no longer its own, and
            # none is written.
            fitted = superposed(frames, atom_indices=atoms, reference=reference,
                                ref_atom_indices=reference_atoms)
            fitted.xyz = smoothed(fitted.xyz, window)
            _write_dcd(fitted, target)
    return {"ok": True, "file": name, "said": said, "atoms": int(len(atoms)), "to": to,
            "smooth": window}


#: The most atoms times frames in one piece of the frames sent as XTC:
#: about 24 MB as a DCD, about a third of that as XTC.
PIECE_ATOM_FRAMES = 2_000_000
PIECES = "frames_pieces"


def frames_pieces(output_dir: str | Path) -> dict[str, Any]:
    """The frames written as XTC in pieces, for the Viewer to play from the
    first while the rest arrive: each piece's address, first frame and
    count. XTC keeps a coordinate to a thousandth of a nanometre (0.01
    angstrom), about a third of a DCD's size; the DCD stays for whatever
    reads the frames here. Written once for the frames as they are."""
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    out = Path(output_dir)
    simulation = out / "simulation"
    index = _load_json(simulation / FRAMES_INDEX)
    if not index.get("available") or not (simulation / FRAMES_FILE).is_file():
        return {"ok": False, "reason": "There are no frames yet."}
    folder = simulation / PIECES
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(out.resolve()), threading.RLock())
    with lock:
        said = _load_json(folder / "index.json")
        if said.get("signature") == index.get("signature") and all(
                (folder / f"piece_{k}.xtc").is_file() for k in range(len(said.get("pieces", [])))):
            return said
        with suppress_native_output():
            frames = md.load_dcd(str(simulation / FRAMES_FILE),
                                 top=str(simulation / FRAMES_TOPOLOGY))
        size = max(1, min(frames.n_frames, PIECE_ATOM_FRAMES // max(1, frames.n_atoms)))
        folder.mkdir(exist_ok=True)
        pieces = []
        for k, start in enumerate(range(0, frames.n_frames, size)):
            piece = frames[start:start + size]
            temporary = _temporary(folder / f"piece_{k}.xtc")
            piece.save_xtc(str(temporary))
            temporary.replace(folder / f"piece_{k}.xtc")
            pieces.append({"start": start, "frames": int(piece.n_frames),
                           "url": f"/structure/frames-piece.xtc?k={k}"})
        said = {"ok": True, "signature": index.get("signature"), "pieces": pieces,
                "total": int(frames.n_frames), "atoms": int(frames.n_atoms),
                "bytes": sum((folder / f"piece_{k}.xtc").stat().st_size
                             for k in range(len(pieces))),
                "dcd_bytes": (simulation / FRAMES_FILE).stat().st_size}
        _write_json(folder / "index.json", said)
        return said


def as_xtc(dcd: Path) -> Path:
    """A DCD of the frames (the frames superposed) as XTC beside it, written
    once for it as it is."""
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    target = dcd.with_suffix(".xtc")
    if target.is_file() and target.stat().st_mtime_ns >= dcd.stat().st_mtime_ns:
        return target
    with suppress_native_output():
        frames = md.load_dcd(str(dcd), top=str(dcd.parent / FRAMES_TOPOLOGY))
    temporary = _temporary(target)
    frames.save_xtc(str(temporary))
    temporary.replace(target)
    return target


#: How many frames a movie may put in between two frames played.
BETWEEN = (1, 3, 7)


def frames_between(source: Path, span: Any, between: Any
                   ) -> tuple[Path | None, str | None]:
    """For a movie: the frames played ``from:to:every`` (``span``, either
    way), with ``between`` frames after each but the last, each atom moved
    in a straight line from its place in one frame played to its place in
    the next. Not simulated: a display between frames, which shortens a bond
    whose atoms swing far between them. Written beside ``source`` (the
    frames, or the frames superposed), replacing the last written for it."""
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    try:
        n = int(str(between))
        first, last, every = (int(part) for part in str(span).split(":"))
    except (TypeError, ValueError):
        return None, "Frames in between are asked for as span=from:to:every and between=1, 3 or 7."
    if n not in BETWEEN:
        return None, "A movie puts 1, 3 or 7 frames in between two frames played."
    if every < 1 or first < 0 or last < 0:
        return None, "A movie's frames are counted from 0, every 1 or more."
    target = source.with_name(f"{source.stem}_between{n}_{first}_{last}_{every}.dcd")
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(source.parent.parent.resolve()), threading.RLock())
    with lock:
        if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return target, None
        with suppress_native_output():
            frames = md.load_dcd(str(source), top=str(source.parent / FRAMES_TOPOLOGY))
        if max(first, last) >= frames.n_frames:
            return None, f"The frames played are 0 to {frames.n_frames - 1}."
        played = list(range(first, last + 1, every) if first <= last
                      else range(first, last - 1, -every))
        count = (len(played) - 1) * (n + 1) + 1
        if count * frames.n_atoms > BINARY_ATOM_FRAMES:
            return None, (f"{count:,} frames of {frames.n_atoms:,} atoms are too many to send; "
                          "choose fewer frames, or fewer in between.")
        xyz = frames.xyz[played].astype(np.float64)
        steps = np.arange(n + 1, dtype=np.float64) / (n + 1)
        moved = (xyz[:-1, None] + steps[None, :, None, None] * (xyz[1:] - xyz[:-1])[:, None])
        out = np.concatenate([moved.reshape(-1, *xyz.shape[1:]), xyz[-1:]]).astype(np.float32)
        lengths = angles = None
        if frames.unitcell_lengths is not None:
            given = frames.unitcell_lengths[played].astype(np.float64)
            lengths = np.concatenate([
                (given[:-1, None] + steps[None, :, None] * (given[1:] - given[:-1])[:, None])
                .reshape(-1, 3), given[-1:]])
            angles = np.repeat(frames.unitcell_angles[played], n + 1, axis=0)[:count]
        tweened = md.Trajectory(out, frames.topology, unitcell_lengths=lengths,
                                unitcell_angles=angles)
        for old in source.parent.glob(f"{source.stem}_between*"):
            if old.stem != target.stem:
                old.unlink(missing_ok=True)
        _write_dcd(tweened, target)
    return target, None


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


def _times(indices: list[int], total: int, total_ns: float | None,
           interval_ns: float | None = None) -> list[float | None]:
    """Each frame's time in the production, in ns.

    OpenMM's reporter writes frame k at (k + 1) saving intervals, which is
    the clock the analyses plot (`gui/series.analysed_axis`). The frames
    were spread evenly from 0 to the run's length, 0, 0.101, ... 10 ns for
    100 frames of 10 ns where they were 0.1, 0.2, ... 10, and a frame shown
    in the Viewer was a frame's spacing from the same frame on the Analysis
    page. A DCD written through MDTraj records no clock of its own, so the
    interval comes from the records (:func:`_interval_ns`); where none says
    it, the old spread stands.
    """
    if interval_ns:
        return [round((index + 1) * interval_ns, 12) for index in indices]
    if total_ns is None or total < 2:
        return [None] * len(indices)
    return [float(total_ns) * index / (total - 1) for index in indices]


def _interval_ns(out: Path, source: dict[str, Any]) -> float | None:
    """The time between the trajectory's frames, from the records: the
    interval the analyses read the played trajectory at, else, for a run's
    own production, its saving interval and timestep (the reading Derrick
    Kwan's branch, `fix/viewer-recorded-frame-times`, gives). ``None``
    where neither says it."""
    from fastmdxplora.gui.series import of_the_played_trajectory

    manifest = _load_json(out / "analysis" / "analysis_manifest.json")
    loaded = manifest.get("load_kwargs") if isinstance(manifest.get("load_kwargs"), dict) else {}
    interval_ps = loaded.get("saving_interval_ps")
    if (type(interval_ps) in (int, float) and np.isfinite(interval_ps) and interval_ps > 0
            and of_the_played_trajectory(out)):
        return float(interval_ps) / 1000.0
    trajectory = source.get("trajectory")
    if not isinstance(trajectory, Path) or trajectory != out / "simulation" / "production.dcd":
        return None
    record = _load_json(trajectory.parent / "simulation_parameters.json")
    resolved, parameters = record.get("resolved"), record.get("parameters")
    if record.get("continues") or not isinstance(resolved, dict) \
            or not isinstance(parameters, dict):
        return None
    steps = resolved.get("trajectory_interval_steps")
    timestep = parameters.get("timestep_fs")
    if (type(steps) is int and steps > 0 and type(timestep) in (int, float)
            and np.isfinite(timestep) and timestep > 0
            and parameters.get("integrator") in {"langevin", "langevin_middle", "verlet",
                                                 "brownian"}):
        return steps * float(timestep) / 1_000_000.0
    return None


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
