"""Each residue's backbone dihedrals over the frames played, as the Viewer's
Ramachandran plot shows them.

The `dihedrals` analysis writes the φ and ψ of the trajectory it analysed;
the Viewer plays its own frames, and a residue is followed there frame by
frame. Here φ and ψ are computed by MDTraj from the frames played as
written (a dihedral is the same in a fitted frame), each given to the
residue whose alpha carbon it turns about: φ (C of the residue before, N,
CA, C) and ψ (N, CA, C, N of the residue after), so a chain's first residue
has no φ and its last no ψ. They are sent in tenths of a degree, as 16-bit
integers in base 64, frame by frame, and written once beside the frames.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["ANGLES_FILE", "MISSING", "backbone_angles"]

ANGLES_FILE = "backbone_angles.json"
#: A dihedral a residue has none of (a chain's ends, a gap).
MISSING = -32768


def _packed(values: Any) -> str:
    tenths = np.where(np.isfinite(values), np.round(values * 10.0), MISSING)
    return base64.b64encode(np.ascontiguousarray(tenths, dtype="<i2").tobytes()).decode("ascii")


def _said(residues: int, frames: int) -> str:
    return f"φ and ψ of {residues:,} residues in each of the {frames:,} frames played, in degrees."


def backbone_angles(root: str | Path) -> dict[str, Any]:
    """φ and ψ of each protein residue in each frame played, in degrees;
    or why there are none."""
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import (FRAMES_FILE, FRAMES_INDEX, FRAMES_TOPOLOGY,
                                                    _atom_lines, _load_json, _read)
    from fastmdxplora.utils.native_output import suppress_native_output

    simulation = Path(root) / "simulation"
    index = _load_json(simulation / FRAMES_INDEX)
    if not index.get("available") or not (simulation / FRAMES_FILE).is_file():
        return {"ok": False, "reason": "There are no frames to follow a residue's angles in yet."}
    kept = _load_json(simulation / ANGLES_FILE)
    if kept.get("signature") == index.get("signature") and kept.get("ok"):
        # Said afresh: a file kept before said it in other words.
        return {**kept, "said": _said(len(kept.get("atoms") or []), int(kept.get("frames") or 0))}
    with suppress_native_output():
        frames = md.load_dcd(str(simulation / FRAMES_FILE),
                             top=str(simulation / FRAMES_TOPOLOGY))
    lines = _atom_lines(_read(simulation / FRAMES_TOPOLOGY))
    topology = frames.topology
    alphas = [atom.index for atom in topology.atoms
              if atom.name == "CA" and atom.residue.is_protein]
    if not alphas:
        return {"ok": False, "reason": "The frames have no protein residues."}
    place = {topology.atom(a).residue.index: k for k, a in enumerate(alphas)}
    phi = np.full((frames.n_frames, len(alphas)), np.nan)
    psi = np.full((frames.n_frames, len(alphas)), np.nan)
    phi_atoms, phi_values = md.compute_phi(frames)
    psi_atoms, psi_values = md.compute_psi(frames)
    for column, quartet in enumerate(phi_atoms):
        k = place.get(topology.atom(int(quartet[2])).residue.index)
        if k is not None:
            phi[:, k] = np.degrees(phi_values[:, column])
    for column, quartet in enumerate(psi_atoms):
        k = place.get(topology.atom(int(quartet[1])).residue.index)
        if k is not None:
            psi[:, k] = np.degrees(psi_values[:, column])
    residues = [[lines[a][21].strip() or None, int(lines[a][22:26]), lines[a][26].strip(),
                 lines[a][17:20].strip()] for a in alphas]
    said = {"ok": True, "signature": index.get("signature"), "frames": int(frames.n_frames),
            "residues": residues, "atoms": [int(a) for a in alphas],
            "phi": _packed(phi), "psi": _packed(psi),
            "said": _said(len(alphas), int(frames.n_frames))}
    temporary = simulation / f".{ANGLES_FILE}.tmp"
    temporary.write_text(json.dumps(said), encoding="utf-8")
    temporary.replace(simulation / ANGLES_FILE)
    return said
