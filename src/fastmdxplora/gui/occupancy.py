"""Where the ligand and the water go over the frames played, and the water
sites the study found, placed on the structure the Viewer shows.

A trajectory says where a ligand sat and where water collected only frame
by frame; the Viewer showed one frame at a time. This gives each, over the
frames played, as an occupancy map, VMD's VolMap in kind: space cut into
cubes half an angstrom wide, and for each cube the fraction of frames in
which an atom's centre is within its radius of the cube's centre (a heavy
atom of the ligand, 1.5 angstroms; a water's oxygen, 1.4). Each frame is
first fitted on the backbone of the ligand's pocket to the first frame
played, as the Viewer superposes it, so the map is in the first frame's
place and a contour reads "present here in 30% of frames". Bulk water,
at 0.0334 molecules a cubic angstrom, would read about 0.38: a water map is
read against that. Each map is written beside the frames as OpenDX, which
Mol* reads.

The sites `water_sites` found are given in the frame of the site's atoms in
the first frame it analysed; they are placed by fitting those atoms there
onto the same atoms in the first frame played.
"""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["BULK_WATER", "GRID_ANGSTROM", "MOST_WATER_FRAMES", "occupancy", "occupancy_file",
           "water_sites_placed"]

#: The width of a cube of the map, in angstroms.
GRID_ANGSTROM = 0.5
#: How near an atom's centre a cube's centre must be for the atom to occupy
#: it, in angstroms: about a carbon's radius, and an oxygen's.
RADIUS = {"ligand": 1.5, "water": 1.4}
#: Water molecules a cubic angstrom in bulk at room temperature.
BULK_WATER = 0.0334
#: The most frames a water map reads: each holds the whole box.
MOST_WATER_FRAMES = 500
#: How far around the ligand a water map reaches, in angstroms.
WATER_REACH = 6.0
_LIGAND = re.compile(r"^[A-Za-z0-9]{1,4}$")
_LOCK = threading.Lock()


def _bulk_reads() -> float:
    """What bulk water reads in a water map: the chance an oxygen's centre
    is within the radius of a point."""
    volume = 4.0 / 3.0 * math.pi * RADIUS["water"] ** 3
    return 1.0 - math.exp(-BULK_WATER * volume)


def occupancy_file(of: str, ligand: str, cutoff_angstrom: Any) -> tuple[str | None, str | None]:
    """The file a map is written to, from the request's words alone (never a
    path), or why there is none."""
    if of not in RADIUS:
        return None, "A map is of the ligand or of the water."
    if not ligand or not _LIGAND.match(ligand):
        return None, "A map is about a ligand: no ligand was named."
    try:
        cutoff = float(cutoff_angstrom)
    except (TypeError, ValueError):
        cutoff = float("nan")
    if not 1.0 <= cutoff <= 20.0:
        return None, "The pocket's cutoff is 1 to 20 Å."
    return f"occupancy_{of}_{ligand.upper()}_{cutoff:.2f}.dx", None


def occupancy(root: str | Path, of: str, *, ligand: str | None,
              cutoff_angstrom: Any = 5.0) -> dict[str, Any]:
    """The map of the ligand or the water over the frames played, written
    beside them if it is not already for these frames, with what it is; or
    why there is none."""
    out = Path(root)
    simulation = out / "simulation"
    name, reason = occupancy_file(of, ligand or "", cutoff_angstrom)
    if name is None:
        return {"ok": False, "reason": reason}
    cutoff = float(cutoff_angstrom)
    index = _load_json(simulation / "frames_index.json")
    if not index.get("available") or not (simulation / "frames.dcd").is_file():
        return {"ok": False, "reason": "There are no frames to map yet."}
    signature = f"{index.get('signature')}|{GRID_ANGSTROM}|{RADIUS[of]}|{MOST_WATER_FRAMES}"
    record = simulation / (name[:-3] + ".json")
    with _LOCK:
        said = _load_json(record)
        if said.get("signature") == signature and (simulation / name).is_file():
            return said
        try:
            said = (_ligand_map if of == "ligand" else _water_map)(
                out, index, ligand.upper(), cutoff)
        except Exception as exc:  # noqa: BLE001 - said, not raised
            return {"ok": False, "reason": f"The map could not be made: {exc}"}
        if not said.get("ok"):
            return said
        grid = said.pop("grid")
        _write_dx(simulation / name, grid["counts"] / said["frames"], grid["origin"])
        said.update(signature=signature, file=name, of=of, ligand=ligand.upper(),
                    cutoff=cutoff, grid_angstrom=GRID_ANGSTROM, radius=RADIUS[of])
        _write_json(record, said)
        return said


def _fitted_frames(out: Path, ligand: str, cutoff: float) -> tuple[Any, Any, str]:
    """The frames played, fitted on the backbone of the ligand's pocket to
    the first frame, the pocket's atoms and what they were fitted on; or
    None, None and why there are none."""
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import (FRAMES_TOPOLOGY, pocket_backbone,
                                                    superposed_frames)
    from fastmdxplora.utils.native_output import suppress_native_output

    fitted = superposed_frames(out, "pocket", ligand=ligand, cutoff_angstrom=cutoff)
    if not fitted.get("ok"):
        return None, None, str(fitted.get("reason"))
    simulation = out / "simulation"
    with suppress_native_output():
        frames = md.load_dcd(str(simulation / fitted["file"]),
                             top=str(simulation / FRAMES_TOPOLOGY))
    atoms, _ = pocket_backbone(frames, ligand, cutoff)
    return frames, atoms, fitted["said"]


def _ligand_map(out: Path, index: dict[str, Any], ligand: str, cutoff: float) -> dict[str, Any]:
    frames, _, fitted = _fitted_frames(out, ligand, cutoff)
    if frames is None:
        return {"ok": False, "reason": f"The map could not be made: {fitted}"}
    heavy = frames.topology.select(f"resname {ligand} and not element H")
    if len(heavy) == 0:
        return {"ok": False, "reason": f"There is no {ligand} in the frames."}
    points = [frames.xyz[k, heavy] * 10.0 for k in range(frames.n_frames)]
    grid = _counted(points, RADIUS["ligand"], margin=RADIUS["ligand"] + 1.0)
    return {"ok": True, "grid": grid, "frames": int(frames.n_frames), "atoms": int(len(heavy)),
            "peak": float(grid["counts"].max() / frames.n_frames),
            "said": (f"Where {ligand}'s {len(heavy)} heavy atoms were over the "
                     f"{frames.n_frames:,} frames played, each fitted on {fitted}: the "
                     f"fraction of frames a heavy atom's centre is within "
                     f"{RADIUS['ligand']:g} Å of each point.")}


def _water_map(out: Path, index: dict[str, Any], ligand: str, cutoff: float) -> dict[str, Any]:
    import mdtraj as md

    from fastmdxplora.analysis.water_sites import WATER_RESIDUES
    from fastmdxplora.gui.trajectory_frames import _even, _source
    from fastmdxplora.utils.native_output import suppress_native_output

    source = _source(out)
    if source is None or source["kind"] != "trajectory":
        return {"ok": False, "reason": "A water map is read from the run's trajectory, "
                                       "which it has not written yet."}
    frames, pocket, fitted = _fitted_frames(out, ligand, cutoff)
    if frames is None:
        return {"ok": False, "reason": f"The map could not be made: {fitted}"}
    heavy = frames.topology.select(f"resname {ligand} and not element H")
    with suppress_native_output():
        whole = md.load_topology(str(source["topology"]))
    waters = np.array([atom.index for atom in whole.atoms
                       if atom.residue.name.upper() in WATER_RESIDUES
                       and atom.element is not None and atom.element.symbol == "O"], dtype=int)
    if not len(waters):
        return {"ok": False, "reason": (
            "The trajectory holds no water: `simulation.save_selection` defaults to "
            "`not water`. Set it to `all` for a study whose subject is the water.")}
    kept = whole.select("not water")
    if len(kept) == 0 or len(kept) != frames.n_atoms:
        return {"ok": False, "reason": "The frames played are not the trajectory's atoms "
                                       "without its water, so its water cannot be placed."}
    # The pocket atom each water is placed against, in the source's numbering.
    anchor = int(pocket[0])
    anchor_source = int(kept[anchor])
    played = [int(i) for i in index.get("frame_indices") or []]
    chosen = _even(len(played), MOST_WATER_FRAMES)
    reference = frames.xyz[0, pocket].astype(float)
    centre = frames.xyz[0, heavy].mean(axis=0) * 10.0
    # Each water's nearest copy to a pocket atom, set beside that atom as the
    # frame played has it, then fitted as that frame was.
    read = np.sort(np.append(waters, anchor_source))
    at = int(np.searchsorted(read, anchor_source))
    others = np.array([i for i in range(len(read)) if i != at], dtype=int)
    pairs = np.array([[at, i] for i in others], dtype=int)
    subset = whole.subset(read)
    points = []
    with suppress_native_output():
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            for k in chosen:
                handle.seek(played[k])
                xyz, lengths, angles = handle.read(1, atom_indices=read)
                boxed = lengths is not None and bool(np.all(np.asarray(lengths) > 0))
                one = md.Trajectory(xyz / 10.0, subset,
                                    unitcell_lengths=lengths / 10.0 if boxed else None,
                                    unitcell_angles=angles if boxed else None)
                offsets = md.compute_displacements(one, pairs, periodic=boxed)[0]
                shown = _frame_xyz(out, k)
                into = _fit(shown[pocket].astype(float), reference)
                near = into(shown[anchor] + offsets) * 10.0
                keep = np.linalg.norm(near - centre, axis=1) <= WATER_REACH + 4.0
                points.append(near[keep])
    reach = np.abs(frames.xyz[0, heavy] * 10.0 - centre).max() + WATER_REACH
    grid = _counted(points, RADIUS["water"], margin=0.0,
                    bounds=(centre - reach, centre + reach))
    bulk = _bulk_reads()
    return {"ok": True, "grid": grid, "frames": len(chosen), "atoms": int(len(waters)),
            "peak": float(grid["counts"].max() / len(chosen)), "bulk": round(bulk, 3),
            "said": (f"Where water's oxygens were within {WATER_REACH:g} Å of {ligand} "
                     f"over {len(chosen):,} of the frames played, each fitted on {fitted}: the "
                     f"fraction of frames an oxygen's centre is within {RADIUS['water']:g} "
                     f"Å of each point. Bulk water would read about {bulk:.2f}.")}


_FRAMES_READ: dict[str, Any] = {}


def _frame_xyz(out: Path, k: int) -> Any:
    """Frame ``k`` of the frames played, as written (made whole), in nm."""
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    path = out / "simulation" / "frames.dcd"
    key = f"{path}:{path.stat().st_mtime_ns}"
    if _FRAMES_READ.get("key") != key:
        with suppress_native_output():
            with md.formats.DCDTrajectoryFile(str(path)) as handle:
                xyz, _, _ = handle.read()
        _FRAMES_READ.clear()
        _FRAMES_READ.update(key=key, xyz=xyz / 10.0)
    return _FRAMES_READ["xyz"][k]


def _fit(points: Any, reference: Any) -> Any:
    """The least-squares move and turn of ``points`` onto ``reference``, as
    a function of positions."""
    here, there = points.mean(axis=0), reference.mean(axis=0)
    left, _, right = np.linalg.svd((points - here).T @ (reference - there))
    sign = np.sign(np.linalg.det(left @ right)) or 1.0
    rotation = (left @ np.diag([1.0, 1.0, sign]) @ right).T
    return lambda positions: (np.asarray(positions, dtype=float) - here) @ rotation.T + there


def _counted(points: list[Any], radius: float, *, margin: float,
             bounds: tuple[Any, Any] | None = None) -> dict[str, Any]:
    """For each cube of a grid about the points, how many of the frames
    have a point within ``radius`` of its centre."""
    every = np.concatenate([p for p in points if len(p)] or [np.zeros((1, 3))])
    low, high = (every.min(axis=0) - margin, every.max(axis=0) + margin) if bounds is None \
        else bounds
    low = np.floor(np.asarray(low) / GRID_ANGSTROM) * GRID_ANGSTROM
    shape = np.maximum(1, np.ceil((np.asarray(high) - low) / GRID_ANGSTROM).astype(int) + 1)
    reach = int(math.ceil(radius / GRID_ANGSTROM))
    steps = np.arange(-reach, reach + 1)
    offsets = np.array(np.meshgrid(steps, steps, steps, indexing="ij")).reshape(3, -1).T
    counts = np.zeros(int(np.prod(shape)), dtype=np.int32)
    for frame in points:
        if not len(frame):
            continue
        nearest = np.rint((frame - low) / GRID_ANGSTROM).astype(int)
        cells = (nearest[:, None, :] + offsets[None, :, :]).reshape(-1, 3)
        centres = low + cells * GRID_ANGSTROM
        owners = np.repeat(frame, len(offsets), axis=0)
        inside = (np.linalg.norm(centres - owners, axis=1) <= radius) \
            & np.all(cells >= 0, axis=1) & np.all(cells < shape, axis=1)
        flat = np.ravel_multi_index(cells[inside].T, shape)
        counts[np.unique(flat)] += 1
    return {"counts": counts.reshape(tuple(shape)), "origin": low}


def _write_dx(path: Path, values: Any, origin: Any) -> None:
    """A map as OpenDX: x slowest, z fastest, in angstroms."""
    from fastmdxplora.gui.trajectory_frames import _write_text

    nx, ny, nz = values.shape
    flat = values.ravel()
    lines = [f"object 1 class gridpositions counts {nx} {ny} {nz}",
             f"origin {origin[0]:.4f} {origin[1]:.4f} {origin[2]:.4f}",
             f"delta {GRID_ANGSTROM:.4f} 0 0", f"delta 0 {GRID_ANGSTROM:.4f} 0",
             f"delta 0 0 {GRID_ANGSTROM:.4f}",
             f"object 2 class gridconnections counts {nx} {ny} {nz}",
             f"object 3 class array type double rank 0 items {flat.size} data follows"]
    for start in range(0, flat.size, 3):
        lines.append(" ".join(f"{value:.4g}" for value in flat[start:start + 3]))
    lines += ['attribute "dep" string "positions"', 'object "occupancy" class field',
              'component "positions" value 1', 'component "connections" value 2',
              'component "data" value 3', ""]
    _write_text(path, "\n".join(lines))


def water_sites_placed(root: str | Path) -> dict[str, Any]:
    """The sites `water_sites` found, placed on the first frame played, with
    what each is; or why there are none."""
    import mdtraj as md
    import pandas as pd

    from fastmdxplora.analysis.loading import _made_whole
    from fastmdxplora.gui.series import analysed_axis, of_the_played_trajectory
    from fastmdxplora.gui.trajectory_frames import _source
    from fastmdxplora.utils.native_output import suppress_native_output

    out = Path(root)
    folder = out / "analysis" / "water_sites"
    record = _load_json(folder / "options.json")
    table = folder / "water_sites.dat"
    if not record or not table.is_file():
        return {"ok": False, "reason": "The study has no water sites: the `water_sites` "
                                       "analysis was not run."}
    findings = record.get("findings") or {}
    sites = pd.read_csv(table)
    if not len(sites):
        return {"ok": True, "sites": [], "said": str(findings.get("not_found") or
                                                     "The analysis found no site.")}
    if not isinstance(findings.get("fitted_on"), dict):
        return {"ok": False, "reason": (
            "These sites were found before each frame was put in the site's own frame, "
            "so they are not places on the protein: analyse the study again.")}
    if not of_the_played_trajectory(out):
        return {"ok": False, "reason": "The water sites were found in a trajectory other "
                                       "than the one played."}
    index = _load_json(out / "simulation" / "frames_index.json")
    source = _source(out)
    if not index.get("available") or source is None or source["kind"] != "trajectory":
        return {"ok": False, "reason": "There are no frames to place the sites on yet."}
    with suppress_native_output():
        whole = md.load_topology(str(source["topology"]))
    site = whole.select(str((findings.get("site") or {}).get("selection") or "protein"))
    kept = whole.select("not water")
    within = np.searchsorted(kept, site)
    if not len(site) or np.any(within >= len(kept)) or np.any(kept[within] != site):
        return {"ok": False, "reason": "The site's atoms are not among the atoms played."}
    first = analysed_axis(out, 1)[0][0]
    with suppress_native_output():
        with md.formats.DCDTrajectoryFile(str(source["trajectory"])) as handle:
            handle.seek(first)
            xyz, lengths, angles = handle.read(1, atom_indices=kept)
    analysed = md.Trajectory(xyz / 10.0, whole.subset(kept),
                             unitcell_lengths=None if lengths is None else lengths / 10.0,
                             unitcell_angles=angles)
    if lengths is not None:
        analysed = _made_whole(analysed)
    played = _frame_xyz(out, 0)[within].astype(float)
    there = analysed.xyz[0, within].astype(float)
    turned = len(site) >= 3
    into = _fit(there, played) if turned else (lambda p: np.asarray(p) - there.mean(axis=0)
                                                + played.mean(axis=0))
    centres = into(sites[["x", "y", "z"]].to_numpy(dtype=float)) * 10.0
    placed = []
    for (_, row), centre in zip(sites.iterrows(), centres):
        meaning = str(row["interpretation"])
        placed.append({"site": int(row["site"]), "x": round(float(centre[0]), 3),
                       "y": round(float(centre[1]), 3), "z": round(float(centre[2]), 3),
                       "occupancy": float(row["occupancy"]),
                       "waters": int(row["n_distinct_waters"]),
                       "bound": "molecule" in meaning, "said": meaning})
    return {"ok": True, "sites": placed,
            "said": (f"{len(placed)} site{'s' if len(placed) != 1 else ''} the water_sites "
                     f"analysis found near {findings['fitted_on'].get('selection')}, placed by "
                     f"fitting its {len(site):,} atoms in the first frame analysed onto the "
                     "first frame played.")}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    from fastmdxplora.gui.trajectory_frames import _write_text

    _write_text(path, json.dumps(payload))
