"""How much room a ligand's pocket has, frame by frame, as the Viewer shows
it.

The empty space of the pocket is counted on a grid, as POVME counts it
(Durrant et al., J. Chem. Theory Comput. 2014, 10, 5047): each frame played
is first fitted on the backbone of the pocket to the first frame, as the
Viewer superposes it, and the region looked in is fixed there: every point
of a grid half an angstrom apart within 4 Å of the ligand's heavy atoms in
the first frame. In each frame a point is empty where it is farther from
every protein atom than that atom's van der Waals radius (Bondi's), inside
the convex hull of the pocket residues' heavy atoms (POVME's exclusion of
the open solvent at the pocket's mouth), and joined, cube face to cube
face, to the points nearest the ligand in the first frame. The ligand, water
and ions are not counted as filling it: this is the room the protein leaves,
whatever is in it. The pocket's volume is the empty points times the volume
of a cube, and the empty points of the frame shown are rendered as a
surface.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["GRID_ANGSTROM", "REACH", "pocket_points", "pocket_volume"]

GRID_ANGSTROM = 0.5
#: How far from the ligand's heavy atoms in the first frame the pocket is
#: looked for, in angstroms.
REACH = 4.0
#: Points this near a ligand heavy atom in the first frame are where the
#: pocket is joined from.
SEED = 1.5
#: Bondi's van der Waals radii, in angstroms; another element takes 1.8.
RADII = {"H": 1.2, "C": 1.7, "N": 1.55, "O": 1.52, "S": 1.8, "P": 1.8, "F": 1.47,
         "Cl": 1.75, "Br": 1.85, "I": 1.98, "Se": 1.9}
_LOCK = threading.Lock()


class _Pocket:
    """The region a pocket is looked for in, fixed on the first frame."""

    def __init__(self, frames: Any, ligand: str, cutoff: float) -> None:
        from scipy.spatial import cKDTree

        from fastmdxplora.gui.trajectory_frames import pocket_backbone

        topology = frames.topology
        heavy = topology.select(f"resname {ligand} and not element H")
        first = frames.xyz[0, heavy] * 10.0
        low = first.min(axis=0) - REACH
        shape = np.ceil((first.max(axis=0) + REACH - low) / GRID_ANGSTROM).astype(int) + 1
        axes = [low[k] + GRID_ANGSTROM * np.arange(shape[k]) for k in range(3)]
        grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
        tree = cKDTree(first)
        near, _ = tree.query(grid, distance_upper_bound=REACH + 1e-6)
        self.inside = np.isfinite(near) & (near <= REACH)
        self.seed = np.isfinite(near) & (near <= SEED)
        self.grid, self.shape, self.origin = grid, tuple(int(n) for n in shape), low
        protein = topology.select("protein")
        elements = np.array([topology.atom(a).element.symbol if topology.atom(a).element
                             else "" for a in protein])
        self.groups = [(protein[elements == symbol], RADII.get(symbol, 1.8))
                       for symbol in sorted(set(elements.tolist()))]
        _, residues = pocket_backbone(frames, ligand, cutoff)
        hull = [atom.index for residue in residues for atom in topology.residue(residue).atoms
                if atom.element is not None and atom.element.symbol != "H"]
        self.hull = np.array(hull, dtype=int)
        self.cutoff = cutoff

    def empty(self, xyz: Any) -> Any:
        """The empty points of one frame's coordinates (nm), as a mask of
        the grid."""
        from scipy import ndimage
        from scipy.spatial import Delaunay, cKDTree

        points = self.grid[self.inside]
        free = np.ones(len(points), dtype=bool)
        for atoms, radius in self.groups:
            if not len(atoms):
                continue
            placed = xyz[atoms] * 10.0
            distance, _ = cKDTree(placed).query(points, distance_upper_bound=radius)
            free &= ~(distance < radius)
        if len(self.hull) >= 4:
            try:
                free &= Delaunay(xyz[self.hull] * 10.0).find_simplex(points) >= 0
            except Exception:  # noqa: BLE001 - a flat hull holds nothing
                free[:] = False
        mask = np.zeros(len(self.grid), dtype=bool)
        mask[np.where(self.inside)[0][free]] = True
        labels, _ = ndimage.label(mask.reshape(self.shape))
        joined = np.unique(labels.ravel()[self.seed & mask])
        joined = joined[joined > 0]
        return np.isin(labels.ravel(), joined) if len(joined) else np.zeros_like(mask)


def _pocket(frames: Any, ligand: str, cutoff: float) -> tuple[_Pocket | None, str | None]:
    """The pocket's region in these frames, or why there is none."""
    if len(frames.topology.select(f"resname {ligand} and not element H")) == 0:
        return None, f"There is no {ligand} in the frames."
    return _Pocket(frames, ligand, cutoff), None


def _ligand_and_cutoff(ligand: Any, cutoff_angstrom: Any) -> tuple[str | None, float, str | None]:
    import re

    if not ligand or not re.match(r"^[A-Za-z0-9]{1,4}$", str(ligand)):
        return None, 0.0, "A pocket is the ligand's: no ligand was named."
    try:
        cutoff = float(cutoff_angstrom)
    except (TypeError, ValueError):
        cutoff = float("nan")
    if not 1.0 <= cutoff <= 20.0:
        return None, 0.0, "The pocket's cutoff is 1 to 20 Å."
    return str(ligand).upper(), cutoff, None


def _frames(out: Path, ligand: str, cutoff: float) -> tuple[Any, str]:
    from fastmdxplora.gui.occupancy import _fitted_frames

    frames, _, fitted = _fitted_frames(out, ligand, cutoff)
    return frames, fitted


def pocket_volume(root: str | Path, ligand: Any, cutoff_angstrom: Any = 5.0) -> dict[str, Any]:
    """The pocket's volume in each frame played, in cubic angstroms, with
    its mean and spread over the frames; written once beside the frames.
    Or why there is none."""
    from fastmdxplora.gui.trajectory_frames import _load_json

    out = Path(root)
    simulation = out / "simulation"
    name, cutoff, reason = _ligand_and_cutoff(ligand, cutoff_angstrom)
    if name is None:
        return {"ok": False, "reason": reason}
    index = _load_json(simulation / "frames_index.json")
    if not index.get("available") or not (simulation / "frames.dcd").is_file():
        return {"ok": False, "reason": "There are no frames to find the pocket in yet."}
    record = simulation / f"pocket_volume_{name}_{cutoff:.2f}.json"
    signature = f"{index.get('signature')}|{GRID_ANGSTROM}|{REACH}|{SEED}"
    with _LOCK:
        kept = _load_json(record)
        if kept.get("signature") == signature:
            return kept
        frames, fitted = _frames(out, name, cutoff)
        if frames is None:
            return {"ok": False, "reason": f"The pocket could not be found: {fitted}"}
        pocket, why = _pocket(frames, name, cutoff)
        if pocket is None:
            return {"ok": False, "reason": why}
        cube = GRID_ANGSTROM ** 3
        volumes = [round(float(pocket.empty(frames.xyz[k]).sum() * cube), 2)
                   for k in range(frames.n_frames)]
        values = np.array(volumes)
        said = {"ok": True, "signature": signature, "ligand": name, "cutoff": cutoff,
                "volumes": volumes, "frames": int(frames.n_frames),
                "times": index.get("frame_times_ns") or [],
                "mean": round(float(values.mean()), 1), "sd": round(float(values.std()), 1),
                "most": round(float(values.max()), 1), "least": round(float(values.min()), 1),
                "region": round(float(pocket.inside.sum() * cube), 1),
                "said": (f"The room the protein leaves in {name}'s pocket, POVME's way: grid "
                         f"points {GRID_ANGSTROM:g} Å apart within {REACH:g} Å of {name}'s heavy "
                         f"atoms in the first frame (a region of "
                         f"{pocket.inside.sum() * cube:,.0f} Å³), each frame fitted on "
                         f"{fitted}; a point is empty beyond every protein atom's van der Waals "
                         "radius, inside the hull of the pocket's residues and joined to the "
                         f"ligand's place. Over the {frames.n_frames:,} frames played: mean "
                         f"{values.mean():,.0f} Å³, standard deviation over the frames "
                         f"{values.std():,.0f} Å³ (not the error of the mean: the frames are "
                         "correlated).")}
        temporary = record.with_name(f".{record.name}.tmp")
        temporary.write_text(json.dumps(said), encoding="utf-8")
        temporary.replace(record)
        return said


def pocket_points(root: str | Path, ligand: Any, cutoff_angstrom: Any, frame: Any
                  ) -> tuple[str | None, str | None]:
    """The empty points of the pocket in one frame played, as OpenDX (1 at
    an empty point, 0 elsewhere) placed as the frames are fitted; or why
    there are none."""
    from fastmdxplora.gui.occupancy import _dx_text

    out = Path(root)
    name, cutoff, reason = _ligand_and_cutoff(ligand, cutoff_angstrom)
    if name is None:
        return None, reason
    frames, fitted = _frames(out, name, cutoff)
    if frames is None:
        return None, f"The pocket could not be found: {fitted}"
    try:
        k = int(frame)
    except (TypeError, ValueError):
        return None, "A frame is named by its number."
    if not 0 <= k < frames.n_frames:
        return None, f"The frames played are 0 to {frames.n_frames - 1}."
    pocket, why = _pocket(frames, name, cutoff)
    if pocket is None:
        return None, why
    values = pocket.empty(frames.xyz[k]).astype(float).reshape(pocket.shape)
    return _dx_text(values, pocket.origin, GRID_ANGSTROM), None
