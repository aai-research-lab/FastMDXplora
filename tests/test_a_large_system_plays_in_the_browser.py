"""A large system is sent to the browser in fewer frames, not in more bytes.

Playback is a multi-model PDB the viewer parses whole, at about 81 bytes an
atom. Its frames were capped at 200 whatever the system's size, so a 50,000
atom membrane system, solvent stripped and lipids kept, was some 800 MB, more
than a browser tab holds. The frames are now bounded by atoms times frames
(`BROWSER_ATOM_FRAMES`), evenly spaced as before and never fewer than two.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.gui import trajectory_playback as playback

md = pytest.importorskip("mdtraj")


def _study(tmp_path, *, residues: int = 10, frames: int = 120):
    sim = tmp_path / "simulation"
    sim.mkdir(parents=True)
    topology = md.Topology()
    chain = topology.add_chain()
    for index in range(residues):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        for name, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                              ("C", md.element.carbon), ("O", md.element.oxygen)):
            topology.add_atom(name, element, residue)
    rng = np.random.RandomState(0)
    xyz = (np.linspace(0, 3.0, topology.n_atoms)[None, :, None]
           + rng.normal(0, 0.01, (frames, topology.n_atoms, 3))).astype(np.float32)
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(sim / "topology.pdb"))
    trajectory.save_dcd(str(sim / "production.dcd"))
    return tmp_path


def test_the_frames_follow_the_size():
    assert playback.frames_for(5_000, 200) == 200
    assert playback.frames_for(50_000, 200) == 20
    assert playback.frames_for(10_000_000, 200) == 2
    assert playback.frames_for(0, 200) == 200


def test_a_large_trajectory_is_sent_in_fewer_frames(tmp_path, monkeypatch):
    # Forty atoms stand for a large system against a budget of 800.
    monkeypatch.setattr(playback, "BROWSER_ATOM_FRAMES", 800)
    info = playback.playback_info(_study(tmp_path), max_browser_frames=200)
    assert info["playback_available"] and info["n_frames_total"] == 120
    assert info["n_frames_browser"] == 20
    text = (tmp_path / "simulation" / "playback.pdb").read_text()
    assert text.count("MODEL") == 20
    # Still spread over the whole run, first frame to last.
    assert info["frame_indices"][0] == 0 and info["frame_indices"][-1] == 119


def test_a_small_trajectory_is_sent_as_before(tmp_path):
    info = playback.playback_info(_study(tmp_path), max_browser_frames=50)
    assert info["n_frames_browser"] == 50


def test_live_snapshots_of_a_large_system_are_thinned_too(tmp_path, monkeypatch):
    from fastmdxplora.gui.live_frames import write_live_frame

    monkeypatch.setattr(playback, "BROWSER_ATOM_FRAMES", 30)
    sim = tmp_path / "simulation"
    atoms = "\n".join(
        f"ATOM  {i + 1:5d}  CA  ALA A{i + 1:4d}    {i:8.3f}   0.000   0.000  1.00  0.00           C"
        for i in range(10)) + "\nEND\n"
    for frame in range(12):
        write_live_frame(sim, pdb_text=atoms, frame_index=frame, stage="nvt",
                         simulation_time_ns=0.001 * frame, archive=True)
    info = playback.playback_info(tmp_path, max_browser_frames=20)
    assert info["source_kind"] == "live-history" and info["n_frames_browser"] == 3
