"""Atomic live-coordinate snapshots and rolling browser playback history.

These files are a read-only dashboard side channel.  They never feed values
back into OpenMM and failures are swallowed so visualization cannot stop or
change a scientific simulation.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

LIVE_FRAME_FILE = "live_frame.pdb"
LIVE_FRAME_INDEX_FILE = "live_frame_index.json"
LIVE_FRAME_HISTORY_DIR = "live_frames"
LIVE_FRAME_HISTORY_FILE = "live_frame_history.json"
DEFAULT_MAX_HISTORY_FRAMES = 200

_WATER_RESNAMES = {"HOH", "WAT", "TIP", "TIP3", "TIP3P", "SOL", "H2O"}
_ION_RESNAMES = {
    "NA", "K", "CL", "BR", "I", "F", "MG", "CA", "ZN", "MN", "FE",
    "CU", "NI", "CO", "CD", "HG", "PB", "CS", "RB", "LI", "BA", "SR",
}


def dashboard_display_pdb(pdb_text: str) -> str:
    """Return a browser-friendly PDB with bulk solvent and ions removed.

    The simulation topology and DCD remain untouched.  Filtering only the
    dashboard copy keeps the viewer responsive while preserving protein and
    ligand coordinates.  Bonds are inferred by the viewer from the displayed
    atoms.
    """
    output: list[str] = []
    atom_written = False
    for line in str(pdb_text or "").splitlines():
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM"}:
            resname = line[17:20].strip().upper()
            if resname in _WATER_RESNAMES or resname in _ION_RESNAMES:
                continue
            output.append(line)
            atom_written = True
        elif record == "CRYST1":
            output.append(line)
        elif record in {"TER"} and atom_written:
            output.append(line)
    if atom_written:
        output.append("END")
    return "\n".join(output) + ("\n" if output else "")


def write_live_frame(
    output_dir: str | Path,
    *,
    pdb_text: str,
    frame_index: int | None = None,
    stage: str | None = None,
    simulation_time_ns: float | None = None,
    archive: bool = False,
    max_history_frames: int = DEFAULT_MAX_HISTORY_FRAMES,
) -> dict[str, Any]:
    """Atomically write the newest frame and optionally archive it.

    ``archive=True`` maintains a bounded rolling history used by the playback
    controls even while NVT/NPT/production are still running.
    """
    out = Path(output_dir)
    frame_path = out / LIVE_FRAME_FILE
    index_path = out / LIVE_FRAME_INDEX_FILE

    try:
        out.mkdir(parents=True, exist_ok=True)
        tmp = frame_path.with_suffix(".pdb.tmp")
        tmp.write_text(pdb_text, encoding="utf-8")
        os.replace(tmp, frame_path)
    except OSError as exc:
        return {"ok": False, "error": f"write-error: {exc}", "frame_index": frame_index}

    history_count = 0
    history_sequence = None
    if archive:
        history = _archive_frame(
            out,
            pdb_text=pdb_text,
            frame_index=frame_index,
            stage=stage,
            simulation_time_ns=simulation_time_ns,
            max_history_frames=max_history_frames,
        )
        history_count = int(history.get("count", 0) or 0)
        history_sequence = history.get("sequence")

    try:
        stat = frame_path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        mtime = time.time()
        size = len(pdb_text.encode("utf-8"))

    index_payload = {
        "live_frame_available": True,
        "live_frame_index": int(frame_index) if frame_index is not None else None,
        "live_frame_updated_at": _iso_now(time.time()),
        "live_frame_mtime": mtime,
        "live_frame_size": size,
        "simulation_stage": str(stage or "").lower() or None,
        "simulation_time_ns": float(simulation_time_ns) if simulation_time_ns is not None else None,
        "history_frame_count": history_count,
        "history_sequence": history_sequence,
    }
    try:
        _atomic_json(index_path, index_payload)
    except OSError:
        pass
    return {"ok": True, **index_payload}


def write_openmm_live_frame(
    output_dir: str | Path,
    *,
    pdbfile_writer: Any,
    topology: Any,
    positions: Any,
    frame_index: int | None = None,
    stage: str | None = None,
    simulation_time_ns: float | None = None,
    archive: bool = True,
    max_history_frames: int = DEFAULT_MAX_HISTORY_FRAMES,
) -> dict[str, Any]:
    """Write an OpenMM state as a solvent-stripped dashboard snapshot."""
    try:
        from io import StringIO

        buf = StringIO()
        try:
            pdbfile_writer(topology, positions, buf, keepIds=True)
        except TypeError:
            pdbfile_writer(topology, positions, buf)
        text = dashboard_display_pdb(buf.getvalue())
        if "ATOM" not in text and "HETATM" not in text:
            return {"ok": False, "error": "openmm-snapshot: no display atoms", "frame_index": frame_index}
    except Exception as exc:  # noqa: BLE001 - dashboard writes must never crash sim
        return {"ok": False, "error": f"openmm-snapshot: {exc}", "frame_index": frame_index}
    return write_live_frame(
        output_dir,
        pdb_text=text,
        frame_index=frame_index,
        stage=stage,
        simulation_time_ns=simulation_time_ns,
        archive=archive,
        max_history_frames=max_history_frames,
    )


def _archive_frame(
    output_dir: Path,
    *,
    pdb_text: str,
    frame_index: int | None,
    stage: str | None,
    simulation_time_ns: float | None,
    max_history_frames: int,
) -> dict[str, Any]:
    history_dir = output_dir / LIVE_FRAME_HISTORY_DIR
    history_path = output_dir / LIVE_FRAME_HISTORY_FILE
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        payload = read_live_frame_history(output_dir)
        records = payload.get("frames") if isinstance(payload, dict) else []
        records = list(records) if isinstance(records, list) else []
        last_sequence = max(
            (int(item.get("sequence", -1)) for item in records if isinstance(item, dict)),
            default=-1,
        )
        sequence = last_sequence + 1
        stage_token = re.sub(r"[^a-z0-9_-]+", "-", str(stage or "frame").lower()).strip("-") or "frame"
        step_token = int(frame_index) if frame_index is not None else sequence
        filename = f"frame_{sequence:06d}_{stage_token}_{step_token:012d}.pdb"
        destination = history_dir / filename
        tmp = destination.with_suffix(".pdb.tmp")
        tmp.write_text(pdb_text, encoding="utf-8")
        os.replace(tmp, destination)
        records.append({
            "sequence": sequence,
            "frame_index": int(frame_index) if frame_index is not None else None,
            "stage": str(stage or "").lower() or None,
            "simulation_time_ns": float(simulation_time_ns) if simulation_time_ns is not None else None,
            "path": f"{LIVE_FRAME_HISTORY_DIR}/{filename}",
            "updated_at": _iso_now(time.time()),
            "mtime_ns": destination.stat().st_mtime_ns,
        })
        cap = max(2, int(max_history_frames or DEFAULT_MAX_HISTORY_FRAMES))
        while len(records) > cap:
            removed = records.pop(0)
            if isinstance(removed, dict):
                old_path = output_dir / str(removed.get("path") or "")
                try:
                    old_path.unlink(missing_ok=True)
                except OSError:
                    pass
        manifest = {
            "version": 1,
            "count": len(records),
            "max_frames": cap,
            "frames": records,
            "updated_at": _iso_now(time.time()),
        }
        _atomic_json(history_path, manifest)
        return {"count": len(records), "sequence": sequence}
    except Exception:  # noqa: BLE001 - visualization history is best effort
        return {"count": 0, "sequence": None}


def read_live_frame_history(output_dir: str | Path) -> dict[str, Any]:
    path = Path(output_dir) / LIVE_FRAME_HISTORY_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"frames": [], "count": 0}
    except (OSError, json.JSONDecodeError):
        return {"frames": [], "count": 0}


def read_live_frame_index(output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    idx_path = out / LIVE_FRAME_INDEX_FILE
    if not idx_path.is_file():
        return {"live_frame_available": False}
    try:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"live_frame_available": False}


def live_frame_pdb_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / LIVE_FRAME_FILE


def live_frame_exists(output_dir: str | Path) -> bool:
    return live_frame_pdb_path(output_dir).is_file()


def live_frame_coordinates(output_dir: str | Path) -> dict[str, Any] | None:
    """The live frame's coordinates alone, as a one-frame DCD.

    A live frame was sent as its whole PDB, about 81 bytes an atom, and
    loaded as a new structure, which rebuilt every representation and took
    the measurements and picks with it. As a DCD it is 12 bytes an atom, and
    the viewer moves the atoms it already shows. Returned with the number of
    atoms, so a frame of other atoms is loaded whole instead, and the
    fingerprint the secondary structure is matched by: the first atom's
    coordinates as the PDB gives them (see ``gui/by_residue.py``). ``None``
    where there is no live frame or it holds no atoms.
    """
    import tempfile

    import numpy as np

    try:
        text = live_frame_pdb_path(output_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    atoms = [line for line in text.splitlines() if line[:6] in ("ATOM  ", "HETATM")]
    if not atoms:
        return None
    try:
        xyz = np.array([(float(a[30:38]), float(a[38:46]), float(a[46:54])) for a in atoms],
                       dtype=np.float32)
    except ValueError:
        return None
    import mdtraj as md

    topology = md.Topology()
    residue = topology.add_residue("UNK", topology.add_chain())
    for _ in atoms:
        topology.add_atom("X", md.element.carbon, residue)
    with tempfile.TemporaryDirectory(prefix="fastmdx-live-") as folder:
        path = Path(folder) / "live_frame.dcd"
        md.Trajectory(xyz[None] / 10.0, topology).save_dcd(str(path))
        data = path.read_bytes()
    return {"data": data, "atoms": len(atoms), "fingerprint": atoms[0][30:54]}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _iso_now(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
