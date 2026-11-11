"""The dynamic cross-correlation map, held against motions whose
correlations are known by construction."""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

FRAMES = 400


def _chain(count: int, chains: int = 1):
    topology = md.Topology()
    for _ in range(chains):
        chain = topology.add_chain()
        for k in range(count // chains):
            residue = topology.add_residue("ALA", chain, resSeq=k + 1)
            topology.add_atom("CA", md.element.carbon, residue)
    return topology


def _two_domains(*, frames: int = FRAMES, noise: float = 0.005, seed: int = 2,
                 once: bool = False):
    """Forty alpha carbons in two blocks of twenty that open and close along
    x, each block moving as one, with a little noise on every atom."""
    rng = np.random.default_rng(seed)
    start = np.zeros((40, 3))
    start[:, 0] = np.repeat([-1.0, 1.0], 20)
    start[:, 1] = np.tile(np.arange(20) * 0.15, 2)
    start[:, 2] = rng.normal(0, 0.2, 40)
    opening = (np.linspace(-0.2, 0.2, frames) if once
               else 0.2 * np.sin(np.arange(frames) / 9.0))
    xyz = np.repeat(start[None], frames, axis=0)
    xyz[:, :20, 0] -= opening[:, None]
    xyz[:, 20:, 0] += opening[:, None]
    xyz += rng.normal(0, noise, xyz.shape)
    return md.Trajectory(xyz.astype(np.float32), _chain(40))


def _run(trajectory, tmp_path, **options):
    from fastmdxplora.analysis.cross_correlation import CrossCorrelation

    analysis = CrossCorrelation(output_dir=tmp_path, **options)
    return analysis, analysis.run(trajectory)


def test_atoms_moving_alike_opposite_and_apart_are_one_minus_one_and_zero():
    from fastmdxplora.analysis.cross_correlation import cross_correlation

    rng = np.random.default_rng(0)
    moved = rng.normal(size=(5000, 1, 3))
    displaced = np.concatenate([
        moved, 2.5 * moved, -moved, rng.normal(size=(5000, 1, 3)),
        np.zeros((5000, 1, 3))], axis=1)
    displaced -= displaced.mean(axis=0)
    found = cross_correlation(displaced)
    assert np.allclose(found, found.T) and np.allclose(np.diag(found), 1.0)
    assert found[0, 1] == pytest.approx(1.0) and found[0, 2] == pytest.approx(-1.0)
    assert abs(found[0, 3]) < 0.05
    # An atom that never moves is correlated with nothing.
    assert np.all(found[4, :4] == 0.0)


def test_motion_in_concert_at_right_angles_reads_as_none():
    """What the measure is blind to, as its findings say."""
    from fastmdxplora.analysis.cross_correlation import cross_correlation

    swing = np.random.default_rng(1).normal(size=5000)
    displaced = np.zeros((5000, 2, 3))
    displaced[:, 0, 0], displaced[:, 1, 1] = swing, swing
    assert cross_correlation(displaced)[0, 1] == 0.0


def test_two_domains_on_a_hinge_are_two_blocks(tmp_path):
    analysis, result = _run(_two_domains(), tmp_path)
    assert result.status == "ok", result.message
    with np.load(tmp_path / "cross_correlation" / "cross_correlation.npz") as held:
        matrix = held["correlation"]
        assert list(held["residue"][:2]) == ["ALA1", "ALA2"]
    assert matrix.shape == (40, 40)
    assert matrix[:20, :20].mean() > 0.9 and matrix[20:, 20:].mean() > 0.9
    assert matrix[:20, 20:].mean() < -0.9
    found = analysis.findings
    assert found["between_the_halves"]["agreement"] > 0.95
    assert "not_sampled" not in found
    assert found["strongest"]["opposed"][0]["correlation"] < -0.9
    pairs = result.data
    assert list(pairs.columns) == ["residue_i", "chain_i", "residue_j", "chain_j",
                                   "correlation"]
    assert pairs["correlation"].max() > 0.9 and pairs["correlation"].min() < -0.9
    assert result.figure_path.is_file()


def test_noise_alone_is_said_not_to_be_sampled(tmp_path):
    """Atoms jiggling each on its own: the halves' maps share nothing."""
    rng = np.random.default_rng(6)
    start = rng.uniform(0, 3, (40, 3))
    xyz = start[None] + rng.normal(0, 0.02, (FRAMES, 40, 3))
    analysis, result = _run(md.Trajectory(xyz.astype(np.float32), _chain(40)), tmp_path)
    assert result.status == "ok", result.message
    assert analysis.findings["between_the_halves"]["agreement"] < 0.5
    assert "were not" in analysis.findings["not_sampled"]


def test_neighbours_along_a_chain_are_left_out_of_the_pairs(tmp_path):
    analysis, result = _run(_two_domains(), tmp_path, min_separation=10)
    assert result.status == "ok", result.message
    pairs = result.data
    numbers = pairs[["residue_i", "residue_j"]].apply(
        lambda column: column.str[3:].astype(int))
    assert (numbers["residue_j"] - numbers["residue_i"]).abs().min() >= 10


def test_fitted_on_one_domain_the_other_moves_against_it(tmp_path):
    analysis, result = _run(_two_domains(), tmp_path, fit_selection="resid 0 to 19")
    assert result.status == "ok", result.message
    assert "20 of the selection's atoms" in analysis.findings["map"]["fitted_on"]
    with np.load(tmp_path / "cross_correlation" / "cross_correlation.npz") as held:
        matrix = held["correlation"]
    # The second block moves as one against the first, held still.
    assert matrix[20:, 20:].mean() > 0.95


def test_a_turned_and_carried_protein_gives_the_same_map(tmp_path):
    trajectory = _two_domains()
    moved = trajectory.xyz.copy()
    for frame in range(FRAMES):
        angle = 0.02 * frame
        turn = np.array([[np.cos(angle), -np.sin(angle), 0],
                         [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        moved[frame] = trajectory.xyz[frame] @ turn.T + [0.01 * frame, 0, 0]
    _, still = _run(trajectory, tmp_path / "still")
    _, carried = _run(md.Trajectory(moved, trajectory.topology), tmp_path / "carried")
    with np.load(tmp_path / "still" / "cross_correlation" / "cross_correlation.npz") as a, \
            np.load(tmp_path / "carried" / "cross_correlation" / "cross_correlation.npz") as b:
        assert np.abs(a["correlation"] - b["correlation"]).max() < 1e-3


@pytest.mark.parametrize("trajectory, options, said", [
    (lambda: _two_domains(frames=10), {}, "the fewest it is taken over"),
    (lambda: _two_domains(), {"selection": "resid 0 to 1"}, "needs at least three"),
    (lambda: _two_domains(), {"fit_selection": "resid 0"}, "a fit needs at least three"),
    (lambda: _two_domains(), {"fit_selection": "resid ("}, "is not a selection"),
])
def test_what_cannot_be_mapped_is_refused(trajectory, options, said, tmp_path):
    _, result = _run(trajectory(), tmp_path, **options)
    assert result.status == "error" and said in result.message


def test_too_many_atoms_are_refused(tmp_path, monkeypatch):
    import fastmdxplora.analysis.cross_correlation as module

    monkeypatch.setattr(module, "MOST_ATOMS", 30)
    _, result = _run(_two_domains(), tmp_path)
    assert result.status == "error" and "their number squared" in result.message


def test_it_runs_only_where_a_study_names_it():
    from fastmdxplora.analysis.cross_correlation import CrossCorrelation

    assert CrossCorrelation.requires_naming is True
    with pytest.raises(Exception, match="at least 1"):
        CrossCorrelation(min_separation=0)
