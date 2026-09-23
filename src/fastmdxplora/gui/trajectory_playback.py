"""Browser-safe trajectory playback for the live dashboard.

While a run is active, playback is compiled from the rolling solvent-stripped
PDB snapshots written by :mod:`fastmdxplora.gui.live_frames`.  After the run
completes, the scientific DCD is read with MDTraj and downsampled to a bounded,
solvent-stripped multi-model PDB.  Neither source is modified.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.gui.live_frames import read_live_frame_history

PLAYBACK_FILE = "playback.pdb"
PLAYBACK_INDEX_FILE = "playback_index.json"
_PLAYBACK_LOCKS: dict[str, threading.RLock] = {}
_PLAYBACK_LOCKS_GUARD = threading.Lock()

_HEADER_LIKE_RECORDS = frozenset({
    "CRYST1", "HEADER", "OBSLTE", "TITLE", "SPLT", "CAVEAT", "COMPND",
    "SOURCE", "KEYWDS", "EXPDTA", "NUMMDL", "MDLTYP", "AUTHOR", "REVDAT",
    "DBREF", "DBREF1", "DBREF2", "SEQADV", "SEQRES", "MODRES", "HET",
    "HETNAM", "HETSYN", "FORMUL",
})


def PlaybackUnavailable(reason: str) -> dict[str, Any]:
    return {
        "playback_available": False,
        "reason": reason,
        "source_kind": None,
        "n_frames_total": 0,
        "n_frames_browser": 0,
        "frame_indices": [],
        "frame_times_ns": [],
        "simulation_time_ns_first": None,
        "simulation_time_ns_last": None,
    }


def _import_mdtraj():
    try:
        import mdtraj as md
    except ImportError:
        return None
    return md


def playback_info(
    output_dir: str | Path,
    *,
    max_browser_frames: int = 200,
    simulation_time_ns_total: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return or build the best available browser playback companion."""
    out = Path(output_dir)
    lock = _playback_lock(out.resolve())
    with lock:
        return _playback_info_unlocked(
            out,
            max_browser_frames=max_browser_frames,
            simulation_time_ns_total=simulation_time_ns_total,
            force=force,
        )


def _playback_info_unlocked(
    output_dir: Path,
    *,
    max_browser_frames: int = 200,
    simulation_time_ns_total: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build playback while the caller holds the per-output lock."""
    out = output_dir
    sim_dir = out / "simulation"
    companion_pdb = sim_dir / PLAYBACK_FILE
    companion_idx = sim_dir / PLAYBACK_INDEX_FILE
    cap = min(2000, max(2, int(max_browser_frames or 200)))

    history = read_live_frame_history(sim_dir)
    history_frames = [item for item in history.get("frames", []) if isinstance(item, dict)]
    status = _load_json(sim_dir / "live_status.json")
    workflow_completed = str(status.get("status") or "").lower() in {
        "completed", "complete", "ok", "success", "succeeded"
    }

    # As in the analysis phase: the trajectory may hold a subset of the
    # system, and the topology beside it is the one that describes what was
    # actually written. Playing a solute-only trajectory against the full
    # topology does not render the solvent; it fails to load at all.
    _saved_topology = sim_dir / "trajectory_topology.pdb"
    topology_path = (_saved_topology if _saved_topology.is_file()
                     else sim_dir / "topology.pdb")
    dcd_path = sim_dir / "production.dcd"

    # A study that has been extended or resumed plays its joined trajectory,
    # against the topology its record names. The first segment's own file
    # was played instead, so an extended study showed only its first part,
    # and a killed one, whose status still says running, showed the live
    # snapshots. A joined trajectory is written only from finished segments.
    joined_record = _load_json(out / "joined" / "joined.json")
    joined_dcd = out / "joined" / "production.dcd"
    if joined_record and joined_dcd.is_file() and joined_dcd.stat().st_size > 0:
        dcd_path = joined_dcd
        named = Path(str(joined_record.get("topology") or ""))
        if named.is_file():
            topology_path = named
        workflow_completed = True

    # During a live run use lightweight snapshots.  This allows play/pause and
    # scrubbing during minimization/NVT/NPT before a production DCD even exists.
    if len(history_frames) >= 2 and not workflow_completed:
        signature = f"{_history_signature(history_frames)}:cap={cap}"
        cached = _cached_playback(companion_pdb, companion_idx, "live-history", signature, force)
        if cached is not None:
            return cached
        try:
            return _generate_from_history(
                sim_dir=sim_dir,
                records=history_frames,
                companion_pdb=companion_pdb,
                companion_idx=companion_idx,
                max_browser_frames=cap,
                source_signature=signature,
            )
        except Exception as exc:  # noqa: BLE001 - dashboard is best effort
            cached = _load_index(companion_idx)
            if cached.get("playback_available"):
                return cached
            return PlaybackUnavailable(f"history-generate-error: {exc}")

    # Completed runs prefer the scientific DCD.  The browser copy is atom-
    # sliced and downsampled; the original DCD remains unchanged.
    if topology_path.is_file() and dcd_path.is_file() and dcd_path.stat().st_size > 0:
        # The topology is part of what was played: the same trajectory read
        # against a different one is a different playback, and the copy made
        # from the first was served after the second replaced it.
        signature = (f"{dcd_path.parent.name}/{dcd_path.name}:{_file_signature(dcd_path)}:"
                     f"{topology_path.name}:{_file_signature(topology_path)}:cap={cap}")
        cached = _cached_playback(companion_pdb, companion_idx, "production-dcd", signature, force)
        if cached is not None:
            return cached
        md = _import_mdtraj()
        if md is not None:
            try:
                return _generate_from_dcd(
                    md=md,
                    topology_path=topology_path,
                    dcd_path=dcd_path,
                    companion_pdb=companion_pdb,
                    companion_idx=companion_idx,
                    max_browser_frames=cap,
                    simulation_time_ns_total=simulation_time_ns_total,
                    source_signature=signature,
                )
            except Exception:
                # A DCD can be temporarily unreadable while OpenMM is writing
                # its final frame. Fall through to the already-safe history.
                pass

    if len(history_frames) >= 2:
        signature = f"{_history_signature(history_frames)}:cap={cap}"
        cached = _cached_playback(companion_pdb, companion_idx, "live-history", signature, force)
        if cached is not None:
            return cached
        try:
            return _generate_from_history(
                sim_dir=sim_dir,
                records=history_frames,
                companion_pdb=companion_pdb,
                companion_idx=companion_idx,
                max_browser_frames=cap,
                source_signature=signature,
            )
        except Exception as exc:  # noqa: BLE001
            return PlaybackUnavailable(f"history-generate-error: {exc}")

    if dcd_path.is_file() and _import_mdtraj() is None:
        return PlaybackUnavailable("mdtraj-not-installed")
    return PlaybackUnavailable("not-enough-frames")


def _generate_from_history(
    *,
    sim_dir: Path,
    records: list[dict[str, Any]],
    companion_pdb: Path,
    companion_idx: Path,
    max_browser_frames: int,
    source_signature: str,
) -> dict[str, Any]:
    selected = _even_sample(records, max_browser_frames)
    blocks: list[str] = []
    used: list[dict[str, Any]] = []
    for record in selected:
        path = sim_dir / str(record.get("path") or "")
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        atoms = _pdb_atom_lines(text)
        if not atoms:
            continue
        model_number = len(used) + 1
        blocks.append(f"MODEL     {model_number:>4d}")
        blocks.extend(atoms)
        blocks.append("ENDMDL")
        used.append(record)
    if len(used) < 2:
        return PlaybackUnavailable("not-enough-readable-history-frames")
    blocks.append("END")
    _atomic_text(companion_pdb, "\n".join(blocks) + "\n")

    times = [record.get("simulation_time_ns") for record in used]
    indices = [record.get("frame_index") for record in used]
    payload = _playback_payload(
        source_kind="live-history",
        source_signature=source_signature,
        n_total=len(records),
        n_browser=len(used),
        frame_indices=indices,
        frame_times_ns=times,
    )
    _atomic_json(companion_idx, payload)
    return payload


def _generate_from_dcd(
    *,
    md: Any,
    topology_path: Path,
    dcd_path: Path,
    companion_pdb: Path,
    companion_idx: Path,
    max_browser_frames: int,
    simulation_time_ns_total: float | None,
    source_signature: str,
) -> dict[str, Any]:
    # MDTraj reads DCDs through VMD's plugin, which announces every file it
    # opens on the C-level file descriptor -- "dcdplugin) detected standard
    # 32-bit DCD file" -- where Python's logging cannot reach it. Two lines
    # per read, into the terminal of a server that reads on demand.
    from fastmdxplora.utils.native_output import suppress_native_output

    with suppress_native_output():
        topology_traj = md.load_pdb(str(topology_path))
        try:
            display_atoms = topology_traj.topology.select("not water")
        except Exception:
            display_atoms = None
        if display_atoms is not None and len(display_atoms) == 0:
            display_atoms = None

        # Stream the DCD in bounded chunks.  Loading the complete production
        # trajectory before downsampling can exhaust memory on realistic runs.
        display_topology = topology_traj.topology
        if display_atoms is not None and hasattr(topology_traj, "atom_slice"):
            display_topology = topology_traj.atom_slice(display_atoms).topology
        selected_traj = None
        selected_indices: list[int] = []
        total_seen = 0
        if not hasattr(md, "iterload"):
            # Keep lightweight test doubles and older MDTraj-compatible adapters
            # working; real MDTraj uses the bounded streaming path below.
            trajectory = md.load_dcd(
                str(dcd_path),
                top=topology_traj.topology,
                atom_indices=display_atoms,
            )
            total_seen = int(trajectory.n_frames)
            selected_indices = _even_indices(total_seen, max_browser_frames)
            selected_traj = trajectory[selected_indices]
        else:
            for chunk in md.iterload(
                str(dcd_path),
                top=topology_traj.topology,
                atom_indices=display_atoms,
                chunk=1000,
            ):
                chunk_count = int(chunk.n_frames)
                if chunk_count == 0:
                    continue
                chunk_indices = list(range(total_seen, total_seen + chunk_count))
                if selected_traj is None:
                    combined_xyz = chunk.xyz
                    combined_time = chunk.time
                    combined_indices = chunk_indices
                else:
                    combined_xyz = np.concatenate((selected_traj.xyz, chunk.xyz), axis=0)
                    combined_time = np.concatenate((selected_traj.time, chunk.time), axis=0)
                    combined_indices = selected_indices + chunk_indices
                keep = _even_indices(len(combined_indices), max_browser_frames)
                selected_indices = [combined_indices[index] for index in keep]
                selected_traj = md.Trajectory(
                    combined_xyz[keep],
                    display_topology,
                    time=combined_time[keep],
                )
                total_seen += chunk_count

    if selected_traj is None:
        return PlaybackUnavailable("not-enough-trajectory-frames")
    n_total = total_seen
    if n_total < 2:
        return PlaybackUnavailable("not-enough-trajectory-frames")
    frame_indices = selected_indices
    browser_traj = selected_traj
    companion_pdb.parent.mkdir(parents=True, exist_ok=True)
    tmp = companion_pdb.with_suffix(".pdb.tmp")
    browser_traj.save_pdb(str(tmp))
    _replace_or_yield(tmp, companion_pdb)

    times: list[float | None]
    try:
        raw_time = [float(value) / 1000.0 for value in browser_traj.time]
        if len(raw_time) > 1 and max(raw_time) > min(raw_time):
            times = raw_time
        else:
            raise ValueError
    except Exception:
        if simulation_time_ns_total is not None and n_total > 1:
            times = [
                float(simulation_time_ns_total) * (index / (n_total - 1))
                for index in frame_indices
            ]
        else:
            times = [None] * len(frame_indices)

    payload = _playback_payload(
        source_kind="production-dcd",
        source_signature=source_signature,
        n_total=n_total,
        n_browser=len(frame_indices),
        frame_indices=frame_indices,
        frame_times_ns=times,
    )
    _atomic_json(companion_idx, payload)
    return payload


def _playback_payload(
    *,
    source_kind: str,
    source_signature: str,
    n_total: int,
    n_browser: int,
    frame_indices: list[Any],
    frame_times_ns: list[Any],
) -> dict[str, Any]:
    clean_times = [float(value) if value not in (None, "") else None for value in frame_times_ns]
    return {
        "playback_available": True,
        "reason": None,
        "source_kind": source_kind,
        "source_signature": source_signature,
        "n_frames_total": int(n_total),
        "n_frames_browser": int(n_browser),
        "frame_indices": [int(value) if value not in (None, "") else None for value in frame_indices],
        "frame_times_ns": clean_times,
        "simulation_time_ns_first": clean_times[0] if clean_times else None,
        "simulation_time_ns_last": clean_times[-1] if clean_times else None,
        "companion_pdb": PLAYBACK_FILE,
        "compiled_at": time.time(),
    }


def _cached_playback(
    pdb_path: Path,
    index_path: Path,
    source_kind: str,
    source_signature: str,
    force: bool,
) -> dict[str, Any] | None:
    if force or not pdb_path.is_file() or not index_path.is_file():
        return None
    payload = _load_index(index_path)
    if (
        payload.get("playback_available")
        and payload.get("source_kind") == source_kind
        and payload.get("source_signature") == source_signature
    ):
        return payload
    return None


def _even_indices(count: int, cap: int) -> list[int]:
    if count <= cap:
        return list(range(count))
    if cap <= 2:
        return [0, count - 1]
    return sorted({round(index * (count - 1) / (cap - 1)) for index in range(cap)})


def _even_sample(records: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    return [records[index] for index in _even_indices(len(records), cap)]


def _pdb_atom_lines(text: str) -> list[str]:
    output: list[str] = []
    for line in text.splitlines():
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM", "TER"}:
            output.append(line)
    return output


def _history_signature(records: list[dict[str, Any]]) -> str:
    last = records[-1] if records else {}
    return f"{len(records)}:{last.get('sequence')}:{last.get('mtime_ns')}"


def _file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_index(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    return payload if payload else PlaybackUnavailable("invalid-companion")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace_or_yield(tmp, path)


def _replace_or_yield(tmp: Path, path: Path) -> None:
    """Rename into place, unless somebody else already has.

    The dashboard polls, and two requests arriving together both find no
    playback yet and both build it. Each writes a `.tmp` at the same name;
    the first renames it into place, and the second's rename fails with
    ENOENT because its `.tmp` is the one that was just moved. The final
    file exists and is right. Seen in the browser as "dashboard route
    failed: No such file or directory: playback.pdb.tmp" -- an error for
    a race whose outcome was correct.

    So: if the rename fails and the destination is there, the work was
    done by the other request and this one has nothing to add. If the
    destination is not there, something is genuinely wrong, and that is
    the error worth raising.
    """
    try:
        os.replace(tmp, path)
    except FileNotFoundError:
        if not path.is_file():
            raise


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, indent=2))


def _playback_lock(output_dir: Path) -> threading.RLock:
    key = str(output_dir)
    with _PLAYBACK_LOCKS_GUARD:
        lock = _PLAYBACK_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PLAYBACK_LOCKS[key] = lock
        return lock


def neighborhood_residues(
    *,
    topology_path: Path,
    ligand_resname: str,
    cutoff_angstrom: float = 5.0,
    coords_path: Path | None = None,
) -> list[tuple[str, int]]:
    """Return residues with any atom within ``cutoff_angstrom`` of ligand."""
    if not topology_path.is_file():
        return []
    coords_source = coords_path if coords_path and coords_path.is_file() else topology_path
    ligand_coords = _ligand_coord_list(coords_source, ligand_resname)
    if not ligand_coords:
        return []
    cutoff_sq = cutoff_angstrom * cutoff_angstrom
    residues: set[tuple[str, int]] = set()
    try:
        with coords_source.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not (line.startswith("ATOM") or line.startswith("HETATM")):
                    continue
                resname = line[17:20].strip().upper()
                if resname == ligand_resname.upper():
                    continue
                chain_id = line[21:22].strip() or "A"
                try:
                    res_seq = int(line[22:26])
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                except (ValueError, IndexError):
                    continue
                if _any_atom_within_cutoff(x, y, z, ligand_coords, cutoff_sq):
                    residues.add((chain_id, res_seq))
    except OSError:
        return []
    return sorted(residues)


def _ligand_coord_list(path: Path, ligand_resname: str) -> list[tuple[float, float, float]]:
    target = ligand_resname.upper()
    coords: list[tuple[float, float, float]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not (line.startswith("ATOM") or line.startswith("HETATM")):
                    continue
                if line[17:20].strip().upper() != target:
                    continue
                try:
                    coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                except (ValueError, IndexError):
                    continue
    except OSError:
        return []
    return coords


def _any_atom_within_cutoff(
    x: float,
    y: float,
    z: float,
    ligand_coords: list[tuple[float, float, float]],
    cutoff_sq: float,
) -> bool:
    return any(
        (x - lx) ** 2 + (y - ly) ** 2 + (z - lz) ** 2 <= cutoff_sq
        for lx, ly, lz in ligand_coords
    )
