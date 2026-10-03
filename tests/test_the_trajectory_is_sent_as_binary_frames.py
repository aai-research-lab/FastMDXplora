"""The trajectory is sent to the browser as a topology and binary frames.

The playback is a multi-model PDB, about 81 bytes an atom a frame, capped at
a million atoms times frames: a 116,000-atom system was sent eight frames.
As binary, 12 bytes an atom, under ten million atoms times frames, it is
sent 85, and a 5,000-atom protein 2,000 (gui/trajectory_frames.py). The
frames are the playback's, chosen the same way and made whole the same
way, and the topology is the source's own lines, so insertion codes and a
ligand's bonds are kept.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.gui.by_residue import secondary_structure
from fastmdxplora.gui.trajectory_frames import (BINARY_ATOM_FRAMES, frames_for_binary,
                                                frames_info)
from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study
from tests.test_the_playback_shows_molecules_whole import _wrapped_study


def test_how_many_frames_a_system_is_sent():
    assert frames_for_binary(5_000) == 2000
    assert frames_for_binary(116_601) == BINARY_ATOM_FRAMES // 116_601 == 85
    assert frames_for_binary(10_000_000) == 2
    assert frames_for_binary(0) == 2000
    assert frames_for_binary(100, most=50) == 50


@pytest.fixture(scope="module")
def helical(tmp_path_factory) -> Path:
    return _helical_study(tmp_path_factory.mktemp("frames") / "study")


def test_the_frames_are_the_trajectory_s(helical):
    said = frames_info(helical)
    assert said["available"] and said["source_kind"] == "production-dcd"
    assert said["playback_available"] and said["source_signature"] == said["signature"]
    assert (said["n_atoms"], said["n_frames_total"], said["n_frames_browser"]) == (2192, 6, 6)
    sent = md.load_dcd(str(helical / "simulation" / "frames.dcd"),
                       top=str(helical / "simulation" / "frames_topology.pdb"))
    source = md.load_dcd(str(helical / "simulation" / "production.dcd"),
                         top=str(helical / "simulation" / "trajectory_topology.pdb"))
    assert np.array_equal(sent.xyz, source.xyz)
    # And the cartoon rendered on them is what the analysis plotted.
    table = pd.read_csv(helical / "analysis" / "ss" / "ss.dat")
    plotted = ["".join(table.iloc[row, 1:]) for row in range(len(table))]
    assert secondary_structure(helical, "frames")["frames"] == plotted
    assert secondary_structure(helical, "frames")["signature"] == said["signature"]


def test_they_are_written_once_for_a_trajectory(helical):
    first = frames_info(helical)
    assert frames_info(helical)["compiled_at"] == first["compiled_at"]
    dcd = helical / "simulation" / "production.dcd"
    dcd.write_bytes(dcd.read_bytes())
    assert frames_info(helical)["compiled_at"] != first["compiled_at"]


def test_the_frames_are_made_whole(tmp_path):
    root = _wrapped_study(tmp_path / "study")
    said = frames_info(root)
    assert said["made_whole"] is True
    sent = md.load_dcd(str(root / "simulation" / "frames.dcd"),
                       top=str(root / "simulation" / "frames_topology.pdb"))
    protein = sent.topology.select("protein")
    ligand = sent.topology.select("resname LIG")
    for frame in range(sent.n_frames):
        apart = np.linalg.norm(sent.xyz[frame, ligand].mean(axis=0)
                               - sent.xyz[frame, protein].mean(axis=0))
        assert apart < 1.5, (frame, apart)


def test_a_long_trajectory_is_thinned_evenly(tmp_path):
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    topology = md.Topology()
    residue = topology.add_residue("ALA", topology.add_chain())
    for name in ("N", "CA", "C"):
        topology.add_atom(name, md.element.carbon, residue)
    xyz = np.zeros((2500, 3, 3), dtype=np.float32)
    xyz[:, :, 0] = np.arange(2500, dtype=np.float32)[:, None] * 1e-3
    xyz[:, 1, 1] = 0.15
    xyz[:, 2, 2] = 0.15
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    said = frames_info(root, most_frames=100, simulation_time_ns_total=5.0)
    assert said["n_frames_total"] == 2500 and said["n_frames_browser"] == 100
    assert said["frame_indices"][0] == 0 and said["frame_indices"][-1] == 2499
    assert said["frame_times_ns"][-1] == pytest.approx(5.0)
    sent = md.load_dcd(str(root / "simulation" / "frames.dcd"),
                       top=str(root / "simulation" / "frames_topology.pdb"))
    assert np.allclose(sent.xyz[:, 0, 0], np.array(said["frame_indices"]) * 1e-3, atol=1e-6)


def test_the_topology_keeps_what_the_source_said(tmp_path):
    """Insertion codes, the box, and the bonds among the atoms kept; the
    water and its bonds left out."""
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)

    def atom(serial, name, resname, chain, number, code, x, record="ATOM  "):
        return (f"{record}{serial:5d} {name:<4} {resname:<3} {chain}{number:4d}{code}   "
                f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00          {name[0]:>2}")

    lines = ["CRYST1   50.000   50.000   50.000  90.00  90.00  90.00 P 1           1"]
    serial = 1
    for place, (number, code) in enumerate(((184, " "), (184, "A"), (185, " "))):
        for offset, name in enumerate(("N", "CA", "C", "O")):
            lines.append(atom(serial, name, "GLY", "A", number, code, 3.8 * place + offset))
            serial += 1
    lines.append("TER")
    lines += [atom(13, "C1", "LIG", "B", 1, " ", 20.0, "HETATM"),
              atom(14, "C2", "LIG", "B", 1, " ", 21.5, "HETATM"),
              atom(15, "O", "HOH", "C", 1, " ", 30.0, "HETATM"),
              atom(16, "H1", "HOH", "C", 1, " ", 30.9, "HETATM"),
              "CONECT   13   14", "CONECT   15   16", "END"]
    (root / "simulation" / "trajectory_topology.pdb").write_text("\n".join(lines) + "\n",
                                                                 encoding="utf-8")
    loaded = md.load_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    md.Trajectory(np.repeat(loaded.xyz, 3, axis=0), loaded.topology,
                  unitcell_lengths=np.full((3, 3), 5.0),
                  unitcell_angles=np.full((3, 3), 90.0)).save_dcd(
        str(root / "simulation" / "production.dcd"))
    said = frames_info(root)
    assert said["n_atoms"] == 14
    written = (root / "simulation" / "frames_topology.pdb").read_text(encoding="utf-8")
    assert written.startswith("CRYST1")
    assert [line[17:27] for line in written.splitlines() if line.startswith("ATOM")][4] == (
        "GLY A 184A")
    assert "HOH" not in written
    assert "CONECT   13   14" in written and "CONECT   15   16" not in written


def test_frames_written_while_a_run_goes(tmp_path):
    from fastmdxplora.gui.live_frames import write_live_frame

    root = tmp_path / "study"
    simulation = root / "simulation"
    for step in range(4):
        pdb = "".join(
            f"ATOM  {n + 1:5d}  CA  ALA A{n + 1:4d}    {1.0 * n + 0.1 * step:8.3f}"
            f"{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C\n" for n in range(5))
        write_live_frame(simulation, pdb_text=pdb + "END\n", frame_index=step * 100,
                         stage="production", simulation_time_ns=0.01 * step, archive=True)
    (simulation / "live_status.json").write_text(json.dumps({"status": "running"}),
                                                 encoding="utf-8")
    said = frames_info(root)
    assert said["source_kind"] == "live-history"
    assert said["n_frames_browser"] == 4 and said["frame_indices"] == [0, 100, 200, 300]
    sent = md.load_dcd(str(simulation / "frames.dcd"), top=str(simulation / "frames_topology.pdb"))
    assert np.allclose(sent.xyz[:, 0, 0], [0.0, 0.01, 0.02, 0.03], atol=1e-6)


def test_why_there_are_none(tmp_path):
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    said = frames_info(root)
    assert said["available"] is False and "no trajectory" in said["reason"]


def test_the_server_sends_them(helical):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(helical), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        info = json.loads(urllib.request.urlopen(base + "/api/frames-info").read())
        with urllib.request.urlopen(base + "/structure/frames.dcd") as response:
            dcd = response.read()
            kind = response.headers["Content-Type"]
        topology = urllib.request.urlopen(base + "/structure/frames-topology.pdb").read()
        said = json.loads(urllib.request.urlopen(
            base + "/api/secondary-structure?of=frames").read())
    finally:
        session.server.shutdown()
    assert info["available"] and kind == "application/octet-stream"
    assert dcd == (helical / "simulation" / "frames.dcd").read_bytes()
    assert topology.startswith(b"ATOM") or topology.startswith(b"CRYST1")
    assert said["available"] and said["n_frames"] == info["n_frames_browser"]


def test_a_finished_run_with_only_its_snapshots_plays_them(tmp_path):
    """A run that finished without a trajectory (it saved none) plays the
    frames it wrote as it went."""
    from fastmdxplora.gui.live_frames import write_live_frame

    simulation = tmp_path / "study" / "simulation"
    for step in range(3):
        pdb = "".join(f"ATOM  {n + 1:5d}  CA  ALA A{n + 1:4d}    {1.0 * n + 0.1 * step:8.3f}"
                      f"{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C\n" for n in range(4))
        write_live_frame(simulation, pdb_text=pdb + "END\n", frame_index=step,
                         stage="production", simulation_time_ns=0.01 * step, archive=True)
    (simulation / "live_status.json").write_text(json.dumps({"status": "completed"}),
                                                 encoding="utf-8")
    said = frames_info(tmp_path / "study")
    assert said["source_kind"] == "live-history" and said["n_frames_browser"] == 3


def test_the_topology_is_the_first_model_and_keeps_odd_serials(tmp_path):
    """A topology of several models gives the first; a serial past 99,999,
    written as OpenMM does, is no number and is not a bond's."""
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    atoms = [f"ATOM  {serial:>5} {name:<4} GLY A   1    {x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00"
             f"           {name[0]}" for serial, name, x in (
                 ("1", "N", 0.0), ("2", "CA", 1.46), ("a0000", "C", 2.0))]
    text = "\n".join(["MODEL        1", *atoms, "ENDMDL", "MODEL        2", *atoms, "ENDMDL",
                      "END"]) + "\n"
    (root / "simulation" / "trajectory_topology.pdb").write_text(text, encoding="utf-8")
    loaded = md.load_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    md.Trajectory(np.repeat(loaded.xyz[:1], 2, axis=0), loaded.topology).save_dcd(
        str(root / "simulation" / "production.dcd"))
    assert frames_info(root)["n_atoms"] == 3
    written = (root / "simulation" / "frames_topology.pdb").read_text(encoding="utf-8")
    assert sum(line.startswith("ATOM") for line in written.splitlines()) == 3


def test_dssp_of_frames_that_cannot_be_read_is_said(helical, tmp_path):
    """Their topology without their coordinates, or without their index,
    gives DSSP of the topology alone and no signature to match."""
    import shutil

    from fastmdxplora.gui import by_residue

    frames_info(helical)
    root = tmp_path / "copy"
    (root / "simulation").mkdir(parents=True)
    shutil.copy(helical / "simulation" / "frames_topology.pdb", root / "simulation")
    (root / "simulation" / "frames.dcd").write_bytes(b"not frames")
    by_residue._CACHE.clear()
    said = secondary_structure(root, "frames")
    assert said["available"] and said["n_frames"] == 1 and said["signature"] is None
