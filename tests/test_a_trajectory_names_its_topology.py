"""Each trajectory in the file list names the topology it is read with.

A run saves its trajectory without the water by default: trypsin's
`production.dcd` holds 3,257 atoms, and only `trajectory_topology.pdb`
describes them. The Files page offered `topology.pdb`, the solvated system
of 18,320 atoms, beside the trajectory, and listed the file that loads it
unlabelled in the folded run record.
"""

from __future__ import annotations

from pathlib import Path

from fastmdxplora.gui.server import _artifact_records


def _write(root: Path, *paths: str) -> None:
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")


def _by_path(root: Path) -> dict[str, dict[str, str]]:
    return {record["path"]: record for record in _artifact_records(root)}


def test_a_trajectory_without_its_water_is_read_with_the_atoms_it_saved(tmp_path):
    _write(tmp_path, "simulation/production.dcd", "simulation/trajectory_topology.pdb",
           "simulation/topology.pdb")
    records = _by_path(tmp_path)
    trajectory = records["simulation/production.dcd"]
    assert trajectory["opens_with"] == "simulation/trajectory_topology.pdb"
    assert trajectory["label"] == "Production trajectory, read with trajectory_topology.pdb"
    saved = records["simulation/trajectory_topology.pdb"]
    assert saved["group"] == "simulation"
    assert saved["label"] == "Topology of the trajectory: the atoms it saved"
    assert records["simulation/topology.pdb"]["label"] == (
        "Simulated system, solvated: not the trajectory's topology")


def test_a_trajectory_of_every_atom_is_read_with_the_system(tmp_path):
    _write(tmp_path, "simulation/production.dcd", "simulation/topology.pdb")
    records = _by_path(tmp_path)
    assert records["simulation/production.dcd"]["opens_with"] == "simulation/topology.pdb"
    assert records["simulation/topology.pdb"]["label"] == "Simulated system, solvated"


def test_each_run_and_the_joined_trajectory_name_their_own(tmp_path):
    _write(tmp_path, "runs/r1/simulation/production.dcd",
           "runs/r1/simulation/trajectory_topology.pdb",
           "joined/production.dcd", "simulation/trajectory_topology.pdb")
    records = _by_path(tmp_path)
    assert records["runs/r1/simulation/production.dcd"]["opens_with"] == (
        "runs/r1/simulation/trajectory_topology.pdb")
    assert records["joined/production.dcd"]["opens_with"] == (
        "simulation/trajectory_topology.pdb")
    assert records["joined/production.dcd"]["label"].startswith(
        "The whole trajectory, its segments joined")


def test_no_topology_no_pairing(tmp_path):
    _write(tmp_path, "simulation/production.dcd")
    assert "opens_with" not in _by_path(tmp_path)["simulation/production.dcd"]
