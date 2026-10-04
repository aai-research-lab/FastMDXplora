"""What holds the chains together, frame by frame, for the Viewer.

The interactions analysis records what holds a ligand; nothing recorded
what holds a protein's chains to one another: the hydrogen bonds and salt
bridges across the interface of a dimer, an antibody and its antigen, or
the strands of a fibril. This finds them in the frames the Viewer plays, by
the criteria the interactions analysis applies to a ligand
(``analysis/interactions.py``), so a contact is the same contact whichever
partner it holds:

- a hydrogen bond: donor and acceptor in different chains within 3.5 A,
  the donor-hydrogen-acceptor angle above 120 degrees (Baker and Hubbard
  1984; McDonald and Thornton 1994), with the hydrogens the force field
  placed;
- a salt bridge: the centres of a positive group (Arg, Lys, and a histidine
  setup protonated twice) and a negative one (Asp, Glu) in different chains
  within 4.5 A (ProLIF's threshold), measured to the group's centre since a
  carboxylate's charge is shared between its oxygens.

Each contact is given with the frames it was present in, as runs, and its
atoms by their place in the frames, which is the Viewer's numbering.
Written once beside the frames (``simulation/chain_contacts.json``), and
again when they are.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["CONTACTS_FILE", "MOST_CONTACTS", "chain_contacts"]

CONTACTS_FILE = "chain_contacts.json"
#: The most contacts sent, the most often present first.
MOST_CONTACTS = 40
HYDROGEN_BOND_NM = 0.35
HYDROGEN_BOND_DEG = 120.0
SALT_BRIDGE_NM = 0.45

_LOCK = threading.Lock()
_PAIRS_AT_ONCE = 5000


def _runs(present: np.ndarray) -> list[list[int]]:
    """The frames a contact was present in, as [first, last] runs."""
    runs: list[list[int]] = []
    start = None
    for index, here in enumerate(present.tolist()):
        if here and start is None:
            start = index
        elif not here and start is not None:
            runs.append([start, index - 1])
            start = None
    if start is not None:
        runs.append([start, len(present) - 1])
    return runs


def _residue(atom: Any) -> str:
    chain = getattr(atom.residue.chain, "chain_id", None) or str(atom.residue.chain.index)
    return f"{chain}:{atom.residue.name}{atom.residue.resSeq}"


def _hydrogen_bonds(frames: Any, protein: np.ndarray, chain_of: dict[int, int],
                    periodic: bool) -> tuple[list[dict[str, Any]], str | None]:
    import mdtraj as md

    from fastmdxplora.analysis.interactions import donors_and_acceptors
    from fastmdxplora.refusals import CodedError

    try:
        donors, acceptors = donors_and_acceptors(frames.topology, protein)
    except CodedError as exc:
        return [], str(exc)
    if not donors:
        return [], ("The frames have no hydrogens on their donors, so no hydrogen bond "
                    "can be found by its angle.")
    acceptors_of: dict[int, list[int]] = {}
    for acceptor in acceptors:
        acceptors_of.setdefault(chain_of[acceptor], []).append(acceptor)
    # Pairs near enough in any frame, chain by chain, before any angle is
    # measured: every donor against every acceptor would be millions. The
    # donors near another chain's acceptors in some frame, and those
    # acceptors, are found over all frames at once; their pairs are then
    # measured, and those ever within the cutoff kept.
    heavy_hydrogens: dict[int, list[int]] = {}
    for heavy, hydrogen in donors:
        heavy_hydrogens.setdefault(heavy, []).append(hydrogen)
    by_chain: dict[int, list[int]] = {}
    for heavy in heavy_hydrogens:
        by_chain.setdefault(chain_of[heavy], []).append(heavy)
    candidates: list[tuple[int, int, int]] = []
    for chain, heavies in by_chain.items():
        others = np.array([a for c, found in acceptors_of.items() if c != chain
                           for a in found], dtype=int)
        if not len(others):
            continue
        heavies_array = np.array(heavies, dtype=int)
        near = np.unique(np.concatenate([np.asarray(found, dtype=int) for found in
                                         md.compute_neighbors(frames, HYDROGEN_BOND_NM, others,
                                                              haystack_indices=heavies_array,
                                                              periodic=periodic)] or [[]]))
        close = np.unique(np.concatenate([np.asarray(found, dtype=int) for found in
                                          md.compute_neighbors(frames, HYDROGEN_BOND_NM,
                                                               heavies_array,
                                                               haystack_indices=others,
                                                               periodic=periodic)] or [[]]))
        if not len(near) or not len(close):
            continue
        pairs = np.array([(h, a) for h in near for a in close], dtype=int)
        # In blocks, so a wide interface over many frames stays in memory.
        ever = np.concatenate([
            (md.compute_distances(frames, pairs[start:start + _PAIRS_AT_ONCE],
                                  periodic=periodic) < HYDROGEN_BOND_NM).any(axis=0)
            for start in range(0, len(pairs), _PAIRS_AT_ONCE)])
        for heavy, acceptor in pairs[ever].tolist():
            for hydrogen in heavy_hydrogens[heavy]:
                candidates.append((heavy, hydrogen, acceptor))
    if not candidates:
        return [], None
    triples = np.array(sorted(candidates), dtype=int)
    separations = md.compute_distances(frames, triples[:, [0, 2]], periodic=periodic)
    openings = np.rad2deg(md.compute_angles(frames, triples, periodic=periodic))
    present = (separations < HYDROGEN_BOND_NM) & (openings > HYDROGEN_BOND_DEG)
    # One row per donor and acceptor: a bond made by either hydrogen of an
    # amine is the same bond.
    by_pair: dict[tuple[int, int], np.ndarray] = {}
    for column, (heavy, _hydrogen, acceptor) in enumerate(triples.tolist()):
        key = (heavy, acceptor)
        by_pair[key] = by_pair.get(key, np.zeros(frames.n_frames, bool)) | present[:, column]
    found = []
    topology = frames.topology
    for (heavy, acceptor), here in by_pair.items():
        if not here.any():
            continue
        donor_atom, acceptor_atom = topology.atom(heavy), topology.atom(acceptor)
        found.append({"kind": "hydrogen_bond", "said": "Hydrogen bond",
                      "residues": [_residue(donor_atom), _residue(acceptor_atom)],
                      "atoms_said": f"{donor_atom.name}-H···{acceptor_atom.name}",
                      "atoms": [heavy, acceptor], "present": here})
    return found, None


def _salt_bridges(frames: Any, protein: np.ndarray, chain_of: dict[int, int],
                  periodic: bool) -> list[dict[str, Any]]:
    from fastmdxplora.analysis.interactions import (
        _between,
        _group_centres,
        protein_charged_groups,
    )

    positive, negative = protein_charged_groups(frames.topology, protein)
    if not positive or not negative:
        return []
    centres_positive = _group_centres(frames, positive, periodic)
    centres_negative = _group_centres(frames, negative, periodic)
    separations = np.linalg.norm(_between(frames, positive, centres_positive, negative,
                                          centres_negative, periodic), axis=-1)
    topology = frames.topology
    found = []
    for i, plus in enumerate(positive):
        for j, minus in enumerate(negative):
            if chain_of[plus[0]] == chain_of[minus[0]]:
                continue
            here = separations[:, i, j] < SALT_BRIDGE_NM
            if not here.any():
                continue
            a, b = topology.atom(plus[0]), topology.atom(minus[0])
            found.append({"kind": "salt_bridge", "said": "Salt bridge",
                          "residues": [_residue(a), _residue(b)],
                          "atoms_said": f"{a.residue.name}+ {b.residue.name}−",
                          "atoms": [int(plus[0]), int(minus[0])], "present": here})
    return found


def _computed(simulation: Path) -> dict[str, Any]:
    import mdtraj as md

    from fastmdxplora.gui.trajectory_frames import FRAMES_FILE, FRAMES_TOPOLOGY
    from fastmdxplora.utils.native_output import suppress_native_output

    with suppress_native_output():
        frames = md.load_dcd(str(simulation / FRAMES_FILE),
                             top=str(simulation / FRAMES_TOPOLOGY))
    topology = frames.topology
    protein = topology.select("protein")
    chain_of = {int(atom.index): int(atom.residue.chain.index) for atom in topology.atoms}
    chains = sorted({chain_of[int(i)] for i in protein})
    if len(chains) < 2:
        return {"ok": False, "reason": "The protein has one chain, so nothing holds chains "
                                       "together."}
    periodic = frames.unitcell_vectors is not None
    bonds, why_not = _hydrogen_bonds(frames, protein, chain_of, periodic)
    contacts = bonds + _salt_bridges(frames, protein, chain_of, periodic)
    for contact in contacts:
        here = contact.pop("present")
        contact["occupancy"] = round(float(here.mean()), 4)
        contact["episodes"] = _runs(here)
    contacts.sort(key=lambda c: (-c["occupancy"], c["kind"], c["atoms"]))
    notes = [why_not] if why_not else []
    return {"ok": True, "n_frames": int(frames.n_frames), "chains": len(chains),
            "contacts": contacts, "notes": notes,
            "criteria": {"hydrogen_bond": "donor to acceptor within 3.5 Å, "
                                          "donor-hydrogen-acceptor angle above 120°",
                         "salt_bridge": "charged groups' centres within 4.5 Å"}}


def chain_contacts(root: str | Path, most: int = MOST_CONTACTS) -> dict[str, Any]:
    """The hydrogen bonds and salt bridges between the protein's chains in
    the frames played, the most often present first, or why there are none."""
    from fastmdxplora.gui.trajectory_frames import FRAMES_FILE, FRAMES_TOPOLOGY, frames_info

    simulation = Path(root) / "simulation"
    frames_file, topology_file = simulation / FRAMES_FILE, simulation / FRAMES_TOPOLOGY
    if not (frames_file.is_file() and topology_file.is_file()):
        # Written as the Viewer asks for them, which it may not have yet.
        frames_info(root)
    if not (frames_file.is_file() and topology_file.is_file()):
        return {"ok": False, "reason": "There are no frames to look between chains in yet."}
    target = simulation / CONTACTS_FILE
    with _LOCK:
        found = None
        if target.is_file() and target.stat().st_mtime_ns >= frames_file.stat().st_mtime_ns:
            try:
                found = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                found = None
        if not isinstance(found, dict):
            found = _computed(simulation)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_text(json.dumps(found), encoding="utf-8")
            temporary.replace(target)
    if not found.get("ok"):
        return found
    total = len(found["contacts"])
    return {**found, "contacts": found["contacts"][:max(0, int(most))], "total": total}
