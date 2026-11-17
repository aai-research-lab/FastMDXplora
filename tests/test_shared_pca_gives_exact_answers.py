"""Ensembles generated from known Gaussians along known axes, projected onto the
reference's PCA basis: the variances, shares and shifts come out as generated."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.dimred import DimRed

N_ATOMS = 8


def _topology() -> md.Topology:
    topology = md.Topology()
    chain = topology.add_chain("A")
    for i in range(N_ATOMS):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=i + 1))
    return topology


def _shape_and_modes(seed: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """A base shape and three orthonormal motions of it that neither move
    nor turn it as a whole, so superposing on the base leaves them be."""
    rng = np.random.default_rng(seed)
    base = rng.normal(scale=0.6, size=(N_ATOMS, 3))
    base -= base.mean(axis=0)
    rigid = [np.tile(np.eye(3)[k], N_ATOMS) for k in range(3)]
    rigid += [np.cross(np.eye(3)[k], base).ravel() for k in range(3)]
    found = []
    for vector in [*rigid, *rng.normal(size=(3, 3 * N_ATOMS))]:
        for other in found:
            vector = vector - (vector @ other) * other
        found.append(vector / np.linalg.norm(vector))
    return base, np.asarray(found[6:])


def _frames(base, modes, amplitudes) -> np.ndarray:
    return (base.ravel() + amplitudes @ modes).reshape(-1, N_ATOMS, 3)


def _save(folder, name, xyz):
    trajectory = md.Trajectory(xyz.astype(np.float32), _topology())
    pdb, dcd = folder / f"{name}.pdb", folder / f"{name}.dcd"
    trajectory[0].save_pdb(str(pdb))
    trajectory.save_dcd(str(dcd))
    return {"label": name, "trajectory": str(dcd), "topology": str(pdb)}


@pytest.fixture
def generated(tmp_path):
    base, (u1, u2, u3) = _shape_and_modes()
    rng = np.random.default_rng(11)
    n = 1500
    ref_amp = np.column_stack([rng.normal(0, 0.04, n), rng.normal(0, 0.02, n), np.zeros(n)])
    ref_amp[0] = 0  # its first frame, every frame's superposition target, is the base
    shifted = np.column_stack([rng.normal(0.1, 0.04, n), rng.normal(0, 0.02, n), np.zeros(n)])
    off_axis = np.column_stack([np.zeros(n), np.zeros(n), rng.normal(0, 0.04, n)])
    modes = np.stack([u1, u2, u3])
    reference = md.Trajectory(_frames(base, modes, ref_amp).astype(np.float32), _topology())
    targets = [_save(tmp_path, "shifted", _frames(base, modes, shifted)),
               _save(tmp_path, "off_axis", _frames(base, modes, off_axis))]
    analysis = DimRed(methods=["pca"], n_components=2, selection="all", landscape_bins=20,
                      comparison={"reference": {"label": "reference"}, "targets": targets})
    result = analysis.compute(reference)
    return analysis, result, (ref_amp, shifted, off_axis), (u1, u2)


def test_the_basis_is_the_reference_axes_and_its_variance(generated):
    analysis, result, (ref_amp, _, _), (u1, u2) = generated
    components = result["components"]
    assert abs(components[0] @ u1) == pytest.approx(1, abs=1e-3)
    assert abs(components[1] @ u2) == pytest.approx(1, abs=1e-3)
    generated_variance = np.var(ref_amp[:, :2], axis=0, ddof=1)
    record = analysis.findings["comparison"]
    assert record["variance_nm2"] == pytest.approx(generated_variance, rel=2e-3)
    # All of the reference's motion is on two axes: the two hold all of it.
    assert record["variance_explained"] == pytest.approx(
        generated_variance / generated_variance.sum(), abs=1e-3)
    assert record["basis"]["fitted_on"] == "reference"
    assert np.allclose(result["projections"][0].mean(axis=0), 0, atol=1e-6)


def test_a_shift_along_pc1_is_found_on_the_reference_mean(generated):
    _, result, (ref_amp, shifted, _), (u1, _) = generated
    sign = np.sign(result["components"][0] @ u1)
    found = result["projections"][1][:, 0].mean() - result["projections"][0][:, 0].mean()
    assert sign * found == pytest.approx(shifted[:, 0].mean() - ref_amp[:, 0].mean(), abs=1e-3)


def test_motion_off_the_reference_axes_is_said_to_be_off_them(generated):
    analysis, _, (_, shifted, off_axis), _ = generated
    record = analysis.findings["comparison"]
    share = record["share_of_own_variance_on_pcs"]
    assert share[0] == pytest.approx(1, abs=1e-3)
    assert share[1] == pytest.approx(1, abs=1e-3)
    assert share[2] == pytest.approx(0, abs=1e-3)
    # What the off-axis target does lies in what PCA's two axes leave.
    assert record["target_residual_mean_squared_nm2"][1] == pytest.approx(
        np.mean(off_axis[:, 2] ** 2) / (3 * N_ATOMS), rel=2e-2)
    assert record["variance_on_pcs_nm2"][1] == pytest.approx(
        np.var(shifted[:, :2], axis=0, ddof=1), rel=2e-3)


def test_each_landscape_is_minus_ln_p_in_kt_with_empty_bins_blank(generated):
    analysis, result, _, _ = generated
    record = analysis.findings["comparison"]
    assert record["landscape_unit"] == "kT"
    for points, grid, occupied in zip(result["projections"], result["landscapes"],
                                      record["occupied_bins"]):
        counts, _, _ = np.histogram2d(points[:, 0], points[:, 1],
                                      bins=(result["edges_x"], result["edges_y"]))
        assert np.array_equal(grid["counts"], counts)
        assert grid["counts"].sum() == len(points)
        assert occupied == int((counts > 0).sum())
        visited = counts > 0
        assert np.isnan(grid["free_energy"][~visited]).all()
        # Bins of one area: a difference of -ln P is the log of the counts' ratio.
        top = counts.max()
        expected = np.log(top / counts[visited])
        assert np.allclose(grid["free_energy"][visited], expected, atol=1e-12)
