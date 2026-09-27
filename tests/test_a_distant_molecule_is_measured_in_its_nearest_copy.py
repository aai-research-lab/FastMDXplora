"""A molecule away from the protein is measured in its copy nearest the protein.

Loading made molecules whole with every solute molecule as an anchor, and
MDTraj keeps an anchor in whichever periodic copy it was stored in. A ligand
that had left the pocket was then measured in the copy the engine wrote, up
to a box length away, so a radius of gyration of protein and ligand read too
large, and a ligand RMSD jumped. Seen on a real run: the T4 unbound control,
150 of 2,000 frames, Rg off by up to 0.028 nm. The protein and nucleic chains
now anchor, and every other solute molecule is moved to the copy whose centre
is nearest theirs, searched exactly, which rounding fractional coordinates is
not in the dodecahedron and octahedron that setup builds by default.

The answer each frame is checked against is a brute-force search over every
lattice vector within three cells, independent of the code under test.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis.loading import _made_whole, load_trajectory  # noqa: E402

EDGE_NM = 6.4


def _box(shape: str) -> np.ndarray:
    """Rows are box vectors, in the reduced form OpenMM writes."""
    d, s2, s6 = EDGE_NM, np.sqrt(2.0), np.sqrt(6.0)
    return {
        "dodecahedron": np.array([[d, 0, 0], [0, d, 0], [d / 2, d / 2, d * s2 / 2]]),
        "octahedron": np.array([[d, 0, 0], [d / 3, 2 * s2 * d / 3, 0],
                                [-d / 3, s2 * d / 3, s6 * d / 3]]),
        "cube": np.eye(3) * d,
    }[shape]


def _topology(*, residues: int = 40, ligand_atoms: int = 6, ions: int = 2):
    top = md.Topology()
    chain = top.add_chain()
    previous = None
    for number in range(residues):
        atom = top.add_atom("CA", md.element.carbon,
                            top.add_residue("ALA", chain, resSeq=number + 1))
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom
    ligand = top.add_residue("LIG", top.add_chain())
    previous = None
    for number in range(ligand_atoms):
        atom = top.add_atom(f"C{number}", md.element.carbon, ligand)
        if previous is not None:
            top.add_bond(previous, atom)
        previous = atom
    for _ in range(ions):
        top.add_atom("NA", md.element.sodium, top.add_residue("NA", top.add_chain()))
    return top


def _nearest(offset: np.ndarray, box: np.ndarray) -> np.ndarray:
    candidates = [offset + np.array(k) @ box
                  for k in itertools.product(range(-3, 4), repeat=3)]
    return min(candidates, key=np.linalg.norm)


def _stored(shape: str, frames: int, *, radius=(0.5, 4.0), seed: int = 7):
    """A compact protein at the box centre, a ligand and two ions at random
    directions and distances from it, each stored in an arbitrary copy."""
    rng = np.random.default_rng(seed)
    box = _box(shape)
    top = _topology()
    centre = box.sum(axis=0) / 2
    protein = np.cumsum(rng.normal(0, 0.12, (40, 3)), axis=0)
    protein -= protein.mean(axis=0)
    ligand = np.array([[0.14 * j, 0.0, 0.0] for j in range(6)])
    ligand -= ligand.mean(axis=0)
    xyz = np.zeros((frames, top.n_atoms, 3))
    for frame in range(frames):
        xyz[frame, :40] = protein + centre
        for start, shape_of in ((40, ligand), (46, np.zeros((1, 3))), (47, np.zeros((1, 3)))):
            direction = rng.normal(size=3)
            direction /= np.linalg.norm(direction)
            placed = shape_of + centre + direction * rng.uniform(*radius)
            xyz[frame, start:start + len(shape_of)] = placed + rng.integers(-1, 2, 3) @ box
    trajectory = md.Trajectory(xyz.astype(np.float32), top)
    trajectory.unitcell_vectors = np.repeat(box[None], frames, axis=0).astype(np.float32)
    return trajectory, box


def _wrong_copies(trajectory, box, atoms) -> int:
    wrong = 0
    for frame in trajectory.xyz.astype(np.float64):
        offset = frame[atoms].mean(axis=0) - frame[:40].mean(axis=0)
        if np.linalg.norm(offset - _nearest(offset, box)) > 1e-4:
            wrong += 1
    return wrong


@pytest.mark.parametrize("shape", ["dodecahedron", "octahedron", "cube"])
class TestEveryFrame:

    def test_the_ligand_is_in_its_nearest_copy(self, shape) -> None:
        trajectory, box = _stored(shape, 300)
        assert _wrong_copies(_made_whole(trajectory), box, slice(40, 46)) == 0

    def test_so_are_the_ions(self, shape) -> None:
        trajectory, box = _stored(shape, 300)
        imaged = _made_whole(trajectory)
        assert _wrong_copies(imaged, box, [46]) == 0
        assert _wrong_copies(imaged, box, [47]) == 0

    def test_the_ligand_is_moved_whole(self, shape) -> None:
        imaged = _made_whole(_stored(shape, 50)[0])
        bonds = np.linalg.norm(np.diff(imaged.xyz[:, 40:46], axis=1), axis=2)
        assert np.allclose(bonds, 0.14, atol=1e-4)


def test_the_stored_copy_was_the_wrong_one_often_enough_to_matter() -> None:
    # The fixture has to exercise the defect: without the move, a good share
    # of frames sits in another copy.
    trajectory, box = _stored("dodecahedron", 300)
    assert _wrong_copies(trajectory, box, slice(40, 46)) > 50


def test_a_bound_ligand_is_left_where_it_is() -> None:
    trajectory, _ = _stored("dodecahedron", 100, radius=(0.3, 0.8))
    # Stored in the protein's own copy, as a bound ligand is.
    box = _box("dodecahedron")
    for frame in trajectory.xyz:
        offset = frame[40:46].mean(axis=0) - frame[:40].mean(axis=0)
        frame[40:46] += (_nearest(offset, box) - offset).astype(np.float32)
    # Relative to the protein: imaging may move the complex as a whole.
    before = trajectory.xyz[:, 40:46] - trajectory.xyz[:, :40].mean(axis=1, keepdims=True)
    imaged = _made_whole(trajectory)
    after = imaged.xyz[:, 40:46] - imaged.xyz[:, :40].mean(axis=1, keepdims=True)
    assert np.abs(after - before).max() < 1e-5


def test_the_radius_of_gyration_is_that_of_the_nearest_copy() -> None:
    trajectory, box = _stored("dodecahedron", 200)
    imaged = _made_whole(trajectory)
    complex_atoms = list(range(46))
    measured = md.compute_rg(imaged.atom_slice(complex_atoms))
    expected = []
    for frame in trajectory.xyz.astype(np.float64):
        offset = frame[40:46].mean(axis=0) - frame[:40].mean(axis=0)
        placed = frame[40:46] + (_nearest(offset, box) - offset)
        points = np.vstack([frame[:40], placed])
        expected.append(np.sqrt(((points - points.mean(axis=0)) ** 2).sum(axis=1).mean()))
    assert np.allclose(measured, expected, atol=1e-4)


def test_without_a_macromolecule_every_molecule_still_anchors() -> None:
    """A ligand alone in a box has nothing to be nearest to: imaged as before."""
    top = md.Topology()
    residue = top.add_residue("LIG", top.add_chain())
    atoms = [top.add_atom(f"C{i}", md.element.carbon, residue) for i in range(4)]
    for first, second in zip(atoms, atoms[1:]):
        top.add_bond(first, second)
    xyz = np.array([[[4.85, 2.5, 2.5], [4.95, 2.5, 2.5],
                     [0.05, 2.5, 2.5], [0.15, 2.5, 2.5]]], dtype=np.float32)
    trajectory = md.Trajectory(xyz, top)
    trajectory.unitcell_vectors = np.array([np.eye(3) * 5.0], dtype=np.float32)
    expected = trajectory.image_molecules(
        inplace=False, anchor_molecules=trajectory.topology.find_molecules())
    assert np.array_equal(_made_whole(trajectory).xyz, expected.xyz)


def test_it_reaches_the_loader(tmp_path) -> None:
    trajectory, box = _stored("dodecahedron", 40)
    trajectory[0].save_pdb(str(tmp_path / "topology.pdb"))
    trajectory.save_dcd(str(tmp_path / "production.dcd"))
    loaded = load_trajectory(tmp_path / "production.dcd", top=tmp_path / "topology.pdb")
    assert _wrong_copies(loaded, box, slice(40, 46)) == 0
