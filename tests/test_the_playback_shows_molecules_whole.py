"""The trajectory is played with its molecules whole, as the analyses read it.

The playback was the trajectory as the engine wrote it, solvent stripped and
nothing else: a chain split across a face of the box was drawn in two
pieces, and a ligand stored in a periodic copy was drawn a box length from
its protein, jumping back between frames. The analyses make molecules whole
and put each beside the protein before they measure anything
(analysis/loading.py), and the frames the Viewer plays
(gui/trajectory_frames.py) are made the same way, so it shows what was
measured.
"""

from __future__ import annotations

import json
from pathlib import Path

import mdtraj as md
import numpy as np

from fastmdxplora.gui.trajectory_frames import frames_info

BOX = 5.0


def _wrapped_study(root: Path) -> Path:
    """Ten alanines and a four-atom ligand beside them in a 5 nm box; in
    the second frame the ligand is stored one box along x, and in the third
    the last five residues one box along y, as an engine that wraps by atom
    or by residue can write them."""
    (root / "simulation").mkdir(parents=True)
    topology = md.Topology()
    chain = topology.add_chain()
    xyz, previous = [], None
    for index in range(10):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        atoms = [topology.add_atom(name, element, residue) for name, element in (
            ("N", md.element.nitrogen), ("CA", md.element.carbon),
            ("C", md.element.carbon), ("O", md.element.oxygen))]
        for offset in range(4):
            xyz.append([1.0 + 0.36 * index + 0.09 * offset, 2.5, 2.5])
        for a, b in zip(atoms, atoms[1:]):
            topology.add_bond(a, b)
        if previous is not None:
            topology.add_bond(previous, atoms[0])
        previous = atoms[2]
    ligand = topology.add_residue("LIG", topology.add_chain(), resSeq=1)
    carbons = [topology.add_atom(f"C{n}", md.element.carbon, ligand) for n in range(4)]
    for a, b in zip(carbons, carbons[1:]):
        topology.add_bond(a, b)
    xyz += [[2.0 + 0.14 * n, 3.0, 2.5] for n in range(4)]
    frames = np.repeat(np.array(xyz, dtype=np.float32)[None], 3, axis=0)
    frames[1, 40:, 0] += BOX
    tail = [atom.index for atom in topology.atoms
            if atom.residue.chain.index == 0 and atom.residue.resSeq > 5]
    frames[2, tail, 1] -= BOX
    trajectory = md.Trajectory(frames, topology,
                               unitcell_lengths=np.full((3, 3), BOX),
                               unitcell_angles=np.full((3, 3), 90.0))
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8")
    return root


def test_each_frame_is_played_whole(tmp_path):
    root = _wrapped_study(tmp_path / "study")
    assert frames_info(root)["n_frames_browser"] == 3
    played = md.load_dcd(str(root / "simulation" / "frames.dcd"),
                         top=str(root / "simulation" / "frames_topology.pdb"))
    assert played.n_frames == 3
    protein = played.topology.select("protein")
    ligand = played.topology.select("resname LIG")
    for frame in range(3):
        xyz = played.xyz[frame]
        # Every bond of the chain is a bond's length, not a box's.
        bonds = [(a.index, b.index) for a, b in played.topology.bonds]
        longest = max(np.linalg.norm(xyz[a] - xyz[b]) for a, b in bonds)
        assert longest < 0.5, (frame, longest)
        # And the ligand is beside its protein.
        apart = np.linalg.norm(xyz[ligand].mean(axis=0) - xyz[protein].mean(axis=0))
        assert apart < 1.5, (frame, apart)
