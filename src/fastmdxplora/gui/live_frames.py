"""Atomic live-coordinate snapshots and rolling browser playback history.

These files are a read-only dashboard side channel.  They never feed values
back into OpenMM and failures are swallowed so visualization cannot stop or
change a scientific simulation.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

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
    cell: Any = None,
) -> dict[str, Any]:
    """Atomically write the newest frame and optionally archive it.

    ``cell`` is the box the frame was wrapped in (a, b, c in angstroms and
    the three angles), recorded so a reader knows its CRYST1 is that box.

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
        "cell": [round(float(value), 4) for value in cell] if cell is not None else None,
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
    box_vectors: Any = None,
) -> dict[str, Any]:
    """Write an OpenMM state as a solvent-stripped dashboard snapshot.

    ``box_vectors`` (3 x 3, nanometres) is the state's box, written as the
    snapshot's CRYST1 in place of the topology's.
    """
    cell = None
    try:
        from io import StringIO

        buf = StringIO()
        try:
            pdbfile_writer(topology, positions, buf, keepIds=True)
        except TypeError:
            pdbfile_writer(topology, positions, buf)
        text = dashboard_display_pdb(buf.getvalue())
        if box_vectors is not None:
            cell = cell_of_vectors(np.asarray(box_vectors, dtype=float) * 10.0)
            text = with_cell(text, cell)
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
        cell=cell,
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


#: The live frames last made whole, by file, time written and size: a
#: running study's frame is read by the Viewer, its coordinates and its
#: secondary structure, each of which would otherwise image it again.
_WHOLE: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_WHOLE_GUARD = threading.Lock()
_WHOLE_KEPT = 8


def live_frame_text(output_dir: str | Path) -> str | None:
    """The live frame as every reader is sent it: made whole across the
    periodic boundary and centred on the protein (:func:`made_whole_pdb`).
    ``None`` where there is none.

    A snapshot written before its box was recorded carries the box the run
    started with, and a barostat has changed it since, so its molecules
    were wrapped by lattice vectors other than the ones it names: imaged in
    that box, the ligand came out a few angstroms inside the protein. Such
    a frame is imaged in the box of the trajectory's last frame, the moment
    a finished run's last snapshot is of.
    """
    out = Path(output_dir)
    path = live_frame_pdb_path(out)
    recorded = read_live_frame_index(out).get("cell")
    trajectory = out / "production.dcd"
    try:
        stat = path.stat()
        key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
        if not recorded and trajectory.is_file():
            moved = trajectory.stat()
            key += (int(moved.st_mtime_ns), int(moved.st_size))
        with _WHOLE_GUARD:
            if key in _WHOLE:
                _WHOLE.move_to_end(key)
                return _WHOLE[key]
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cell = None if recorded else _last_cell(trajectory)
    whole = made_whole_pdb(with_cell(text, cell) if cell is not None else text)
    with _WHOLE_GUARD:
        _WHOLE[key] = whole
        while len(_WHOLE) > _WHOLE_KEPT:
            _WHOLE.popitem(last=False)
    return whole


def made_whole_pdb(pdb_text: str) -> str:
    """A snapshot's PDB with its molecules made whole and a ligand in the
    copy of the box nearest the protein, as the analyses image the
    trajectory (``analysis/loading._made_whole``).

    A snapshot is written with each molecule wrapped into the box on its
    own, so a ligand bound in the pocket of a protein near a box face was
    shown a box length away from it, in the copy across that face, and a
    protein of two chains came apart. Only the coordinates change: every
    line keeps its names, numbers and order. A snapshot with no box, or one
    that cannot be read, is returned as it was.
    """
    lines = str(pdb_text or "").splitlines()
    atoms = [i for i, line in enumerate(lines) if line[:6] in ("ATOM  ", "HETATM")]
    cell = cell_of(pdb_text)
    if not atoms or cell is None:
        return pdb_text
    try:
        xyz = np.array([[(float(lines[i][30:38]), float(lines[i][38:46]),
                          float(lines[i][46:54])) for i in atoms]], dtype=np.float32)
        whole = made_whole_frames(pdb_text, xyz, [cell])
    except Exception:  # noqa: BLE001 - shown as written rather than not at all
        return pdb_text
    if whole is None:
        return pdb_text
    for i, (x, y, z) in zip(atoms, whole[0]):
        line = lines[i].ljust(54)
        lines[i] = f"{line[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}".rstrip()
    return "\n".join(lines) + ("\n" if str(pdb_text).endswith("\n") else "")


def made_whole_frames(pdb_text: str, xyz: Any, cells: list[Any]) -> Any:
    """Frames of the atoms of ``pdb_text``, in angstroms (frames, atoms, 3),
    made whole and placed about the protein in each frame's box (``cells``:
    a, b, c in angstroms and the three angles), as a new array; ``None``
    where the atoms are not the ones the PDB names or a frame has no box."""
    import mdtraj as md

    from fastmdxplora.analysis.loading import _made_whole

    xyz = np.asarray(xyz, dtype=np.float32)
    if not cells or len(cells) != len(xyz) or any(cell is None for cell in cells):
        return None
    topology = _bonded_topology(pdb_text)
    if topology is None or topology.n_atoms != xyz.shape[1]:
        return None
    boxes = np.asarray(cells, dtype=np.float32)
    frames = md.Trajectory(xyz / 10.0, topology, unitcell_lengths=boxes[:, :3] / 10.0,
                           unitcell_angles=boxes[:, 3:6])
    return np.asarray(_made_whole(frames).xyz, dtype=np.float32) * 10.0


def _bonded_topology(pdb_text: str) -> Any:
    """The topology of a snapshot's atoms, with a molecule's atoms bonded.

    A snapshot carries no CONECT records, so a ligand's atoms read as so
    many molecules of one atom, each placed by itself. The atoms of a
    residue with no bonds are joined in the order written; that a molecule
    is one is all the imaging needs of its bonds, and a ligand is written
    whole, so each atom is reached from the one before it.
    """
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    with tempfile.TemporaryDirectory(prefix="fastmdx-live-") as folder:
        path = Path(folder) / "frame.pdb"
        path.write_text(pdb_text, encoding="utf-8")
        with suppress_native_output():
            topology = md.load_topology(str(path))
    bonded = {atom.index for bond in topology.bonds for atom in bond}
    for residue in topology.residues:
        members = list(residue.atoms)
        if len(members) > 1 and not any(atom.index in bonded for atom in members):
            for first, second in zip(members, members[1:]):
                topology.add_bond(first, second)
    return topology


def _last_cell(trajectory: Path) -> tuple[float, ...] | None:
    """The box of a DCD's last frame, or ``None`` where it has none or
    cannot be read (a run still writing it)."""
    if not trajectory.is_file():
        return None
    try:
        import mdtraj as md

        from fastmdxplora.utils.native_output import suppress_native_output

        with suppress_native_output(), md.formats.DCDTrajectoryFile(str(trajectory)) as handle:
            total = len(handle)
            if total < 1:
                return None
            handle.seek(total - 1)
            _, lengths, angles = handle.read(1)
    except Exception:  # noqa: BLE001 - imaged in the box it names instead
        return None
    if lengths is None or angles is None:
        return None
    cell = tuple(float(v) for v in (*lengths[0], *angles[0]))
    return cell if min(cell[:3]) > 5.0 and min(cell[3:]) > 0 else None


def cell_of_vectors(vectors: Any) -> tuple[float, ...]:
    """Box vectors as rows, in angstroms, as a, b, c and the three angles."""
    a, b, c = (np.asarray(row, dtype=float) for row in vectors)
    lengths = [float(np.linalg.norm(v)) for v in (a, b, c)]

    def angle(u, v):
        return float(np.degrees(np.arccos(np.clip(
            np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v)), -1.0, 1.0))))

    return (*lengths, angle(b, c), angle(a, c), angle(a, b))


def with_cell(pdb_text: str, cell: Any) -> str:
    """``pdb_text`` with its CRYST1 record saying ``cell``, added first
    where it had none."""
    a, b, c, alpha, beta, gamma = (float(v) for v in cell)
    record = f"CRYST1{a:9.3f}{b:9.3f}{c:9.3f}{alpha:7.2f}{beta:7.2f}{gamma:7.2f} P 1           1"
    lines = str(pdb_text or "").splitlines()
    for i, line in enumerate(lines):
        if line[:6] == "CRYST1":
            lines[i] = record
            break
    else:
        lines.insert(0, record)
    return "\n".join(lines) + ("\n" if str(pdb_text).endswith("\n") or not lines else "")


def cell_of(pdb_text: str) -> tuple[float, ...] | None:
    """A PDB's box from its CRYST1 record, or ``None`` where it has none
    (an implicit-solvent run) or a placeholder one."""
    for line in str(pdb_text or "").splitlines():
        if line[:6] == "CRYST1":
            try:
                cell = tuple(float(line[start:end]) for start, end in
                             ((6, 15), (15, 24), (24, 33), (33, 40), (40, 47), (47, 54)))
            except ValueError:
                return None
            return cell if min(cell[:3]) > 5.0 and min(cell[3:]) > 0 else None
    return None


def live_frame_coordinates(output_dir: str | Path) -> dict[str, Any] | None:
    """The live frame's coordinates alone, as a one-frame DCD.

    A live frame was sent as its whole PDB, about 81 bytes an atom, and
    loaded as a new structure, which rebuilt every representation and took
    the measurements and picks with it. As a DCD it is 12 bytes an atom, and
    the viewer moves the atoms it already shows. Returned with the number of
    atoms, so a frame of other atoms is loaded whole instead, and the
    fingerprint the secondary structure is matched by: the first atom's
    coordinates as the PDB is sent (see ``gui/by_residue.py``), made whole
    as every reader of it is (:func:`live_frame_text`). ``None``
    where there is no live frame or it holds no atoms.
    """
    text = live_frame_text(output_dir)
    if text is None:
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
