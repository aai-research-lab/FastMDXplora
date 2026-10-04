"""Which residues of the protein touch which over the frames played, as the
Viewer's contact map shows them.

Two residues are in contact in a frame when any heavy atom of one is within
4.5 Å of any heavy atom of the other, MDTraj's ``closest-heavy`` contact at
its 0.45 nm cutoff; residues fewer than three apart in one chain are left
out, as MDTraj leaves them out, since they touch by being bonded. The pairs
of each frame are found with a k-d tree, every frame played at once read in
pieces, and each pair is given the share of frames it was in contact in.
The frames are made whole and centred on the protein, so no contact is
looked for across the periodic box.

Two states the cluster analysis found are compared by the share of each
state's frames played a pair was in contact in, the second's less the
first's; and one pair is followed frame by frame, its closest heavy atoms
and how far apart they are.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["CONTACT_ANGSTROM", "MAP_FILE", "NEAREST_APART", "contact_map", "contact_pair"]

CONTACT_ANGSTROM = 4.5
#: Residues of one chain fewer than this many apart are not counted.
NEAREST_APART = 3
MAP_FILE = "contact_map.json"
_PIECE = 100


def _frames_and_residues(root: Path) -> tuple[Any, Any, list[list[Any]], str | None]:
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import (FRAMES_FILE, FRAMES_INDEX, FRAMES_TOPOLOGY,
                                                    _atom_lines, _load_json, _read)
    from fastmdxplora.utils.native_output import suppress_native_output

    simulation = root / "simulation"
    index = _load_json(simulation / FRAMES_INDEX)
    if not index.get("available") or not (simulation / FRAMES_FILE).is_file():
        return None, index, [], "There are no frames to find contacts in yet."
    with suppress_native_output():
        frames = md.load_dcd(str(simulation / FRAMES_FILE),
                             top=str(simulation / FRAMES_TOPOLOGY))
    lines = _atom_lines(_read(simulation / FRAMES_TOPOLOGY))
    residues = [r for r in frames.topology.residues if r.is_protein]
    if len(residues) < NEAREST_APART + 1:
        return None, index, [], "The frames have too few protein residues for a contact map."
    named = []
    for residue in residues:
        first = next(iter(residue.atoms)).index
        line = lines[first] if first < len(lines) else ""
        named.append([line[21].strip() or None, int(line[22:26]) if line else residue.resSeq,
                      line[26].strip() if line else "", residue.name])
    return frames, index, named, None


def _heavy(frames: Any) -> tuple[Any, Any, Any, Any]:
    """The protein's heavy atoms, each with its residue's place in the list,
    and each residue's chain and place in it."""
    residues = [r for r in frames.topology.residues if r.is_protein]
    place = {r.index: k for k, r in enumerate(residues)}
    atoms = [a.index for r in residues for a in r.atoms
             if a.element is not None and a.element.symbol != "H"]
    residue_of = np.array([place[frames.topology.atom(a).residue.index] for a in atoms])
    chain_of = np.array([r.chain.index for r in residues])
    in_chain = np.zeros(len(residues), dtype=int)
    for chain in set(chain_of.tolist()):
        members = np.where(chain_of == chain)[0]
        in_chain[members] = np.arange(len(members))
    return np.array(atoms), residue_of, chain_of, in_chain


def _counts(frames: Any, which: Any) -> tuple[Any, Any]:
    """Each pair of residues in contact in any of the frames ``which``, as
    keys ``i * n + j`` (``i < j``), and in how many."""
    from scipy.spatial import cKDTree

    atoms, residue_of, chain_of, in_chain = _heavy(frames)
    n = len(chain_of)
    keys = np.zeros(0, dtype=np.int64)
    counts = np.zeros(0, dtype=np.int64)
    which = np.asarray(which, dtype=int)
    for start in range(0, len(which), _PIECE):
        found = []
        for frame in which[start:start + _PIECE]:
            xyz = frames.xyz[frame, atoms] * 10.0
            pairs = cKDTree(xyz).query_pairs(CONTACT_ANGSTROM, output_type="ndarray")
            if not len(pairs):
                continue
            a, b = residue_of[pairs[:, 0]], residue_of[pairs[:, 1]]
            i, j = np.minimum(a, b), np.maximum(a, b)
            kept = (i != j) & ((chain_of[i] != chain_of[j])
                               | (np.abs(in_chain[i] - in_chain[j]) >= NEAREST_APART))
            found.append(np.unique(i[kept].astype(np.int64) * n + j[kept]))
        if not found:
            continue
        merged = np.concatenate([keys, *found])
        weights = np.concatenate([counts, np.ones(sum(len(f) for f in found), np.int64)])
        keys, inverse = np.unique(merged, return_inverse=True)
        counts = np.bincount(inverse, weights=weights).astype(np.int64)
    return keys, counts


def _pairs(keys: Any, values: Any, n: int, places: int = 4) -> dict[str, list[Any]]:
    return {"i": (keys // n).astype(int).tolist(), "j": (keys % n).astype(int).tolist(),
            "v": [round(float(v), places) for v in values]}


def contact_map(root: str | Path, first: Any = None, second: Any = None,
                method: str | None = None) -> dict[str, Any]:
    """Each pair of residues in contact in any frame played, with the share
    of frames it was in contact in; or, with two states the cluster
    analysis found (``first``, ``second``), the share in the second's frames
    less the share in the first's. Or why there is none."""
    out = Path(root)
    compare = first is not None or second is not None
    if not compare:
        from fastmdxplora.gui.trajectory_frames import _load_json

        kept = _load_json(out / "simulation" / MAP_FILE)
        index = _load_json(out / "simulation" / "frames_index.json")
        if kept.get("ok") and kept.get("signature") == index.get("signature"):
            return kept
    frames, index, residues, reason = _frames_and_residues(out)
    if reason:
        return {"ok": False, "reason": reason}
    n = len(residues)
    common = {"ok": True, "residues": residues, "frames": int(frames.n_frames),
              "cutoff_angstrom": CONTACT_ANGSTROM, "signature": index.get("signature")}
    if compare:
        from fastmdxplora.gui.states import states_of

        states = states_of(out, method)
        if not states.get("ok"):
            return {"ok": False, "reason": states.get("reason")}
        try:
            a, b = int(first), int(second)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "Two states are compared by their numbers."}
        labels = np.asarray(states["of_played"])
        if len(labels) != frames.n_frames:
            return {"ok": False, "reason": "The states were found for other frames than these."}
        shares = {}
        for state in (a, b):
            which = np.where(labels == state)[0]
            if not len(which):
                return {"ok": False, "reason": f"No frame played is in state {state}."}
            keys, counts = _counts(frames, which)
            shares[state] = (dict(zip(keys.tolist(), (counts / len(which)).tolist())),
                             len(which))
        union = np.array(sorted(set(shares[a][0]) | set(shares[b][0])), dtype=np.int64)
        difference = [shares[b][0].get(k, 0.0) - shares[a][0].get(k, 0.0) for k in union.tolist()]
        return {**common, "compared": [a, b], "method": states["method"],
                "frames_of": [shares[a][1], shares[b][1]],
                "pairs": _pairs(union, difference, n),
                "said": (f"The share of frames each pair of residues was in contact in, in "
                         f"state {b} ({shares[b][1]:,} frames played) less in state {a} "
                         f"({shares[a][1]:,}), as the cluster analysis's {states['method']} "
                         f"found them: red where state {b} holds the pair more, blue where "
                         "less.")}
    keys, counts = _counts(frames, np.arange(frames.n_frames))
    said = {**common, "pairs": _pairs(keys, counts / frames.n_frames, n),
            "said": (f"Pairs of the {n:,} protein residues in contact (heavy atoms within "
                     f"{CONTACT_ANGSTROM:g} Å, residues of one chain fewer than "
                     f"{NEAREST_APART} apart left out) in the {frames.n_frames:,} frames "
                     "played, each with the share of frames it was in contact in.")}
    target = out / "simulation" / MAP_FILE
    temporary = target.with_name(f".{MAP_FILE}.tmp")
    temporary.write_text(json.dumps(said), encoding="utf-8")
    temporary.replace(target)
    return said


def contact_pair(root: str | Path, first: Any, second: Any) -> dict[str, Any]:
    """Two residues (by their places in the contact map's list) frame by
    frame: their closest heavy atoms, as atoms of the frames played, and how
    far apart they are in Å."""
    import mdtraj as md

    frames, _, residues, reason = _frames_and_residues(Path(root))
    if reason:
        return {"ok": False, "reason": reason}
    try:
        a, b = int(first), int(second)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "Two residues are named by their places in the map."}
    if not (0 <= a < len(residues) and 0 <= b < len(residues)) or a == b:
        return {"ok": False, "reason": f"Two residues of the map, 0 to {len(residues) - 1}."}
    protein = [r for r in frames.topology.residues if r.is_protein]
    heavy = [[atom.index for atom in protein[k].atoms
              if atom.element is not None and atom.element.symbol != "H"] for k in (a, b)]
    pairs = np.array([(x, y) for x in heavy[0] for y in heavy[1]])
    distances = md.compute_distances(frames, pairs, periodic=False) * 10.0
    closest = distances.argmin(axis=1)
    apart = distances[np.arange(frames.n_frames), closest]
    return {"ok": True, "a": residues[a], "b": residues[b],
            "atoms": pairs[closest].astype(int).tolist(),
            "angstrom": [round(float(d), 2) for d in apart],
            "share": round(float((apart <= CONTACT_ANGSTROM).mean()), 4),
            "cutoff_angstrom": CONTACT_ANGSTROM}
