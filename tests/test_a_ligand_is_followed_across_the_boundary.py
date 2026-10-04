"""Ligand pose RMSD reported the periodic box instead of the ligand.

`compute` took raw Cartesian displacement, and `superposed` discards the
unit cell, so nothing followed the ligand across a periodic face. Measured
on the 20 ns T4-lysozyme unbound control: 65 frame-to-frame jumps above
2 nm, the largest clustered at 6.58-6.80 nm between frames 10 ps apart, and
a maximum of 9.49 nm in a box smaller than that.

Two earlier attempts imaged the ligand per frame into the receptor's cell.
Both failed, and the failures are why this file is shaped as it is:

* The first rounded fractional coordinates. That is correct only in a
  near-orthogonal cell, and these runs use a rhombic dodecahedron, where it
  picks a longer image than the true minimum for about 30% of random pairs.
  It passed a *cubic* test fixture and moved the real control's maximum
  from 9.49 nm to 9.22 nm, which is no fix at all. Every fixture here is
  triclinic for that reason.

* The second delegated the minimum image to mdtraj, which handles triclinic
  correctly, and still made the jumps worse: 59 to 84. Per-frame imaging
  answers "where is it now" and cannot make a path continuous -- when the
  nearest image flips between frames, the path jumps though the ligand has
  not moved.

Unwrapping is the third approach: each frame takes the image nearest its
predecessor. Consecutive frames are a saving interval apart, so a step of
nearly a whole lattice vector is an image swap and nothing else.

Every test below compares against an independently constructed continuous
trajectory, so a fix that merely moves the numbers cannot pass.
"""
import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.ligand_rmsd import _followed_across_the_boundary


EDGE = 6.8
#: A rhombic dodecahedron, which is what `box_shape: dodecahedron` builds and
#: what every benchmark run used.
CELL = np.array([[EDGE, 0.0, 0.0],
                 [0.0, EDGE, 0.0],
                 [EDGE / 2, EDGE / 2, EDGE * np.sqrt(2) / 2]])


def _a_wandering_ligand(n_frames=400, step=0.08, seed=1, n_ligand=6):
    """A continuous walk, and the wrapped trajectory a DCD would hold.

    Returns the trajectory as stored, the true unwrapped ligand positions,
    and the two atom selections.
    """
    top = md.Topology()
    chain = top.add_chain()
    protein = top.add_residue("ALA", chain)
    for _ in range(20):
        top.add_atom("CA", md.element.carbon, protein)
    ligand = top.add_residue("BNZ", chain)
    for _ in range(n_ligand):
        top.add_atom("C", md.element.carbon, ligand)

    rng = np.random.default_rng(seed)
    receptor = np.tile(rng.random((20, 3)) + 1.5, (n_frames, 1, 1))
    ring = rng.random((n_ligand, 3)) * 0.15
    walk = np.cumsum(rng.normal(0, step, (n_frames, 1, 3)), axis=0)
    truth = ring[None] + receptor[0].mean(axis=0) + walk

    stored = np.concatenate([receptor, truth], axis=1)
    fractional = stored @ np.linalg.inv(CELL)
    wrapped = (fractional - np.floor(fractional)) @ CELL

    traj = md.Trajectory(wrapped.astype(np.float32), top)
    traj.unitcell_vectors = np.tile(
        CELL, (n_frames, 1, 1)).astype(np.float32)
    return traj, truth, top.select("resname BNZ"), top.select("name CA")


def _rmsd_from_start(xyz):
    displacement = xyz - xyz[0]
    return np.sqrt(np.mean(np.sum(displacement ** 2, axis=2), axis=1))


def test_it_recovers_the_continuous_path():
    """The test the first two attempts could not pass."""
    traj, truth, ligand, receptor = _a_wandering_ligand()
    followed = _followed_across_the_boundary(traj, ligand, receptor)
    assert np.abs(_rmsd_from_start(followed)
                  - _rmsd_from_start(truth)).max() < 1e-5


@pytest.mark.parametrize("step,frames", [(0.05, 400), (0.15, 800), (0.12, 1200)])
def test_it_holds_across_diffusion_rates(step, frames):
    traj, truth, ligand, receptor = _a_wandering_ligand(
        n_frames=frames, step=step, seed=step and int(step * 100))
    followed = _followed_across_the_boundary(traj, ligand, receptor)
    assert np.abs(_rmsd_from_start(followed)
                  - _rmsd_from_start(truth)).max() < 1e-5


def test_the_ligand_does_not_teleport():
    """A jump of a box repeat between adjacent frames is an image swap."""
    traj, _truth, ligand, receptor = _a_wandering_ligand()
    followed = _rmsd_from_start(_followed_across_the_boundary(
        traj, ligand, receptor))
    assert np.abs(np.diff(followed)).max() < 1.0


def test_the_stored_trajectory_really_does_teleport():
    """Without which the assertion above tests nothing."""
    traj, _truth, ligand, _receptor = _a_wandering_ligand()
    raw = _rmsd_from_start(
        np.asarray(traj.xyz[:, ligand, :], dtype=np.float64))
    assert np.abs(np.diff(raw)).max() > 1.0


def test_a_bound_ligand_is_left_where_it_is():
    """The common case must not move. A ligand that never crosses a face
    has one image, and following it is a no-op."""
    traj, truth, ligand, receptor = _a_wandering_ligand(
        n_frames=300, step=0.002, seed=3)
    followed = _rmsd_from_start(_followed_across_the_boundary(
        traj, ligand, receptor))
    assert followed.max() < 0.2
    assert np.abs(followed - _rmsd_from_start(truth)).max() < 1e-6


def test_a_trajectory_with_no_box_is_not_second_guessed():
    """Nothing to follow it across. The raw coordinates are all there is."""
    traj, _truth, ligand, receptor = _a_wandering_ligand(n_frames=50)
    traj.unitcell_vectors = None
    assert _followed_across_the_boundary(traj, ligand, receptor) is None


def test_rounding_fractional_coordinates_would_not_do():
    """The first attempt's method, shown wrong in the cell that matters.

    Kept as a test so the approach is not tried a fourth time.
    """
    rng = np.random.default_rng(0)
    a = rng.random((3000, 3)) @ CELL
    b = rng.random((3000, 3)) @ CELL

    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("X", chain)
    for _ in range(2):
        top.add_atom("C", md.element.carbon, residue)
    traj = md.Trajectory(
        np.stack([a, b], axis=1).astype(np.float32), top)
    traj.unitcell_vectors = np.tile(
        CELL, (3000, 1, 1)).astype(np.float32)

    true_minimum = md.compute_distances(traj, [[0, 1]], periodic=True)[:, 0]
    delta = b - a
    rounded = np.linalg.norm(
        delta - np.round(delta @ np.linalg.inv(CELL)) @ CELL, axis=1)

    assert (rounded > true_minimum + 1e-4).mean() > 0.1


def test_the_analysis_actually_uses_it():
    """The helper is proven above; this asserts `compute` calls it.

    Every test above imports `_followed_across_the_boundary` and exercises
    it directly, so all of them pass while the analysis ignores it.
    Confirmed by mutation: replacing the call site in `compute` with
    ``followed = None`` -- which routes straight back to the pre-fix
    branch -- left the whole suite green.

    That is the shape of defect this project has already paid for once. In
    2.5.4 a test compared its result against the caller's trajectory and
    passed only because superposition had mutated it. A helper that is
    proven but unused fails the same question: could this pass if the thing
    it tests were false?
    """
    from fastmdxplora.analysis.ligand_rmsd import LigandRMSD

    traj, truth, _ligand, _receptor = _a_wandering_ligand()

    naive = _rmsd_from_start(
        np.asarray(traj.xyz[:, traj.topology.select("resname BNZ"), :],
                   dtype=np.float64))
    assert np.abs(np.diff(naive)).max() > 1.0, (
        "the fixture must cross a face, or this asserts nothing")

    result = LigandRMSD(ligand_resname="BNZ",
                        align_selection="name CA").compute(traj)

    assert np.abs(np.diff(result)).max() < 1.0, (
        "an image swap reached the analysis output")
    assert np.abs(result - _rmsd_from_start(truth)).max() < 1e-3, (
        "compute() did not follow the ligand across the boundary")


def _a_rigid_complex_tumbling(cell, n_frames=40, reach=4.4):
    """A receptor and ligand that never move relative to each other, turning
    4 degrees a frame in ``cell``, stored as written. The receptor is
    elongated, its first alpha carbon ``reach`` nm from the ligand, so the
    first atom of the alignment is more than half the box from it."""
    top = md.Topology()
    chain = top.add_chain()
    protein = top.add_residue("ALA", chain)
    for _ in range(12):
        top.add_atom("CA", md.element.carbon, protein)
    ligand = top.add_residue("LIG", chain)
    for k in range(6):
        top.add_atom(f"C{k}", md.element.carbon, ligand)
    rng = np.random.default_rng(0)
    alphas = np.column_stack([np.linspace(0, reach, 12),
                              rng.normal(0, 0.3, 12), rng.normal(0, 0.3, 12)])
    body = np.vstack([alphas, [reach, 0.5, 0] + rng.normal(0, 0.12, (6, 3))])
    body -= body.mean(axis=0)
    xyz = []
    for frame in range(n_frames):
        angle = np.deg2rad(4 * frame)
        turn = np.array([[np.cos(angle), -np.sin(angle), 0],
                         [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        xyz.append(body @ turn.T + 3.4)
    traj = md.Trajectory(np.array(xyz, dtype=np.float32), top)
    traj.unitcell_vectors = np.tile(cell, (n_frames, 1, 1)).astype(np.float32)
    return traj


@pytest.mark.parametrize("cell", [np.eye(3) * EDGE, CELL], ids=["cube", "dodecahedron"])
def test_a_rigid_complex_far_from_its_first_alpha_carbon_has_a_still_ligand(cell):
    """The first frame's minimum image is right only under half the box.

    Anchored on the first alignment atom, 4.4 nm from the ligand in a
    6.8 nm box, the first frame took the wrong copy and this read 13.3 nm.
    """
    from fastmdxplora.analysis.ligand_rmsd import LigandRMSD

    traj = _a_rigid_complex_tumbling(cell)
    result = LigandRMSD(ligand_resname="LIG", align_selection="name CA").compute(traj)

    assert result.max() < 1e-3


def test_a_bound_ligand_on_a_face_has_its_own_fluctuation():
    """The ligand RMSF reads the same ligand the RMSD does.

    A bound ligand atom on the +x face of a 5 nm box, stored on the far side
    in some frames as an engine writes it. Fitted coordinates alone carry
    the stored copy, and this read 1.80 nm against a true 0.035 nm.
    """
    from fastmdxplora.analysis.ligand_rmsf import LigandRMSF

    box = 5.0
    rng = np.random.default_rng(0)
    top = md.Topology()
    chain = top.add_chain()
    protein = top.add_residue("ALA", chain)
    for _ in range(12):
        top.add_atom("CA", md.element.carbon, protein)
    top.add_atom("C1", md.element.carbon, top.add_residue("LIG", chain))
    alphas = rng.normal(0, 0.4, (12, 3)) + [4.6, 2.5, 2.5]
    truth = np.array([4.98, 2.5, 2.5]) + rng.normal(0, 0.02, (200, 3))
    stored = truth.copy()
    stored[:, 0] %= box
    assert (stored[:, 0] < 1.0).sum() > 10, "the fixture must cross the face"
    xyz = np.concatenate([np.tile(alphas, (200, 1, 1)), stored[:, None, :]], axis=1)
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_vectors = np.tile(np.eye(3) * box, (200, 1, 1)).astype(np.float32)

    result = LigandRMSF(ligand_resname="LIG", align_selection="name CA").compute(traj)

    true_rmsf = np.sqrt(((truth - truth.mean(axis=0)) ** 2).sum(axis=1).mean())
    assert result[0, 1] == pytest.approx(true_rmsf, abs=1e-4)
