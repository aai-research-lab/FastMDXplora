"""A water site is found where it is on the protein, however the protein turns.

`water_sites` clustered each water's position as the trajectory stored it.
A protein tumbles in its box: over a run it turns, and a water held in one
place on it traces an arc in the box. The arc was clustered as one wide
cluster and rejected as a surface, or split into sites at a fraction of
their occupancy. Each frame's positions are now put in the frame of the
site's atoms in the first frame, as an analysis fits before it compares, so
a site is one place on the protein.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_water_sites import _system

md = pytest.importorskip("mdtraj")


def _turning(n_frames=60, turn_degrees=180.0, seed=0):
    """The residue and one water held beside it, turned together about the
    residue's middle by ``turn_degrees`` over the run and carried across the
    box; the other waters bulk."""
    rng = np.random.RandomState(seed)
    top = _system()
    n_waters = (top.n_atoms - 4) // 3
    solute = np.array([[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]], dtype=float)
    held = np.array([0.15, 0.30, 0.0])
    middle = solute.mean(axis=0)
    xyz = np.zeros((n_frames, top.n_atoms, 3), dtype=np.float32)
    for frame in range(n_frames):
        angle = np.radians(turn_degrees) * frame / (n_frames - 1)
        turn = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0],
                         [0, 0, 1]])
        carried = np.array([3.0 + 0.01 * frame, 3.0, 3.0])
        xyz[frame, :4] = (solute - middle) @ turn.T + carried
        point = (held - middle + rng.normal(scale=0.02, size=3)) @ turn.T + carried
        for index in range(n_waters):
            base = 4 + index * 3
            spot = point if index == 0 else rng.uniform(0, 8, size=3)
            xyz[frame, base] = spot
            xyz[frame, base + 1] = spot + [0.01, 0, 0]
            xyz[frame, base + 2] = spot + [0, 0.01, 0]
    traj = md.Trajectory(xyz=xyz, topology=top)
    traj.unitcell_lengths = np.tile([8.0, 8.0, 8.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
    return traj


def _sites(traj, tmp_path, **kwargs):
    from fastmdxplora.analysis.water_sites import WaterSites

    analysis = WaterSites(output_dir=str(tmp_path), site_selection="resname ALA",
                          cutoff_nm=0.6, **kwargs)
    return analysis.compute(traj), analysis.findings


def test_a_water_held_on_a_turning_protein_is_one_site(tmp_path):
    found, findings = _sites(_turning(), tmp_path)
    assert len(found) == 1, findings
    row = found.iloc[0]
    assert row["occupancy"] == 1.0
    assert row["interpretation"] == "one molecule, bound"
    # Where it is on the residue as the residue stood in the first frame.
    solute = np.array([[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]])
    held = np.array([0.15, 0.30, 0.0]) - solute.mean(axis=0) + [3.0, 3.0, 3.0]
    assert np.linalg.norm(np.array([row["x"], row["y"], row["z"]]) - held) < 0.01
    assert findings["fitted_on"] == {"atoms": 4, "selection": "resname ALA",
                                     "to": "the first frame"}


def test_a_protein_that_does_not_turn_finds_the_same_site(tmp_path):
    still, _ = _sites(_turning(turn_degrees=0.0), tmp_path)
    turned, _ = _sites(_turning(turn_degrees=180.0), tmp_path)
    assert len(still) == len(turned) == 1
    for column in ("occupancy", "n_distinct_waters", "longest_stay_frames"):
        assert still.iloc[0][column] == turned.iloc[0][column]


def test_a_site_of_too_few_atoms_to_turn_is_only_moved(tmp_path):
    from fastmdxplora.analysis.water_sites import WaterSites

    analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA", cutoff_nm=0.6)
    analysis.compute(_turning(turn_degrees=0.0))
    assert analysis.findings["fitted_on"] == {
        "atoms": 1, "selection": "name CA",
        "to": "the first frame, moved only: a turn needs three atoms"}
