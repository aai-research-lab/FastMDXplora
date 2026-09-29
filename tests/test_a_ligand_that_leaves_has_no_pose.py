"""A ligand that leaves its site has no pose, and its RMSD has no mean.

Followed across the periodic boundary, as it has to be, a departed ligand's
RMSD from where it started is a path through solvent that grows without
bound. The distance to the site it started in is bounded by the box, so that
is measured, the departure is said, and no mean RMSD is given.
"""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis.ligand_rmsd import (  # noqa: E402
    AWAY_NM,
    LigandRMSD,
    distance_to_the_site,
)

BOX = 5.0


def _trajectory(path: np.ndarray) -> "md.Trajectory":
    """A protein of eight alanines, and a ligand whose centre follows ``path``
    (nm, per frame) from beside the protein's surface, in a 5 nm cubic box
    and stored wrapped, as a run stores it."""
    topology = md.Topology()
    chain = topology.add_chain()
    protein = []
    for index in range(8):
        residue = topology.add_residue("ALA", chain, resSeq=index + 1)
        for offset, (name, element) in enumerate((
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon), ("O", md.element.oxygen))):
            topology.add_atom(name, element, residue)
            protein.append([1.0 + 0.38 * index, 2.0 + 0.1 * offset, 2.0])
    ligand = topology.add_residue("LIG", topology.add_chain())
    shape = np.array([[0.0, 0.0, 0.0], [0.14, 0.0, 0.0], [0.28, 0.0, 0.0]])
    for n in range(3):
        topology.add_atom(f"C{n}", md.element.carbon, ligand)
    frames = []
    for centre in path:
        atoms = np.vstack((protein, shape + centre))
        frames.append(atoms % BOX)
    n = len(frames)
    return md.Trajectory(np.array(frames), topology, time=np.arange(n) * 10.0,
                         unitcell_lengths=np.full((n, 3), BOX),
                         unitcell_angles=np.full((n, 3), 90.0))


#: Beside the protein: its closest atom 0.4 nm from the site's.
START = np.array([2.0, 2.7, 2.0])


def _run(tmp_path, path) -> LigandRMSD:
    analysis = LigandRMSD(ligand_resname="LIG", output_dir=tmp_path)
    result = analysis.run(_trajectory(path))
    assert result.status == "ok", result.message
    return analysis


def test_a_ligand_that_stays_has_a_pose_and_a_mean(tmp_path) -> None:
    rng = np.random.default_rng(0)
    path = START + rng.normal(0.0, 0.02, (60, 3))
    analysis = _run(tmp_path, path)
    assert analysis.findings["site"]["frames_away"] == 0
    assert "mean" in analysis.findings["mean"]
    assert "not_a_measurement" not in analysis.findings["mean"]


def test_a_ligand_that_leaves_is_said_to_and_given_no_mean(tmp_path) -> None:
    # Twenty frames at the site, then straight out through the box and
    # beyond: 6 nm in forty frames, further than the box is wide.
    stay = np.repeat(START[None], 20, axis=0)
    leave = START + np.linspace(0.0, 6.0, 41)[1:, None] * np.array([0.0, 1.0, 0.0])
    analysis = _run(tmp_path, np.vstack((stay, leave)))
    site = analysis.findings["site"]
    assert 20 <= site["first_frame_away"] <= 22
    # Stated on the run's clock as well as by frame.
    assert site["first_away_at"].endswith(")")
    assert "not_a_measurement" in analysis.findings["mean"]
    assert "left the site it started in" in analysis.findings["mean"]["not_a_measurement"]
    # The RMSD keeps growing past the box; the distance to the site does not.
    rmsd = np.loadtxt(tmp_path / "ligand_rmsd" / "ligand_rmsd.dat")
    distance = np.loadtxt(tmp_path / "ligand_rmsd" / "ligand_site_distance.dat")
    assert rmsd[-1] == pytest.approx(6.0, abs=0.01)
    assert distance.max() <= BOX * np.sqrt(3) / 2
    assert distance[:20].max() < AWAY_NM < distance[-1]


def test_a_ligand_with_no_site_is_not_judged(tmp_path) -> None:
    far = np.repeat((START + np.array([0.0, 2.0, 0.0]))[None], 10, axis=0)
    analysis = _run(tmp_path, far)
    assert "no site" in analysis.findings["site"]["not_measured"]
    assert not (tmp_path / "ligand_rmsd" / "ligand_site_distance.dat").exists()


def test_the_distance_is_by_minimum_image(tmp_path) -> None:
    """Across the face of the box the ligand is still beside the site."""
    traj = _trajectory(np.array([START, START + [0.0, 0.0, BOX]]))
    ligand = traj.topology.select("resname LIG")
    distance, site = distance_to_the_site(traj, ligand, 0)
    assert distance[1] == pytest.approx(distance[0], abs=1e-4)
    assert site.size > 0
