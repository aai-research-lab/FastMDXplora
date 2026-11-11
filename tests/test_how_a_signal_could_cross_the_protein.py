"""The residue interaction network, held against networks whose shape and
couplings are known by construction."""

from __future__ import annotations

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

FRAMES = 200


def _residues(places: np.ndarray, motion: np.ndarray, *, seed: int = 0, noise=0.01):
    """One alpha carbon a residue at ``places`` (residues, 3), each frame
    moved by ``motion`` (frames, residues, 3) plus noise; each residue its
    own chain, so none is a neighbour along a chain."""
    topology = md.Topology()
    for k in range(len(places)):
        chain = topology.add_chain()
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=k + 1))
    rng = np.random.default_rng(seed)
    xyz = places[None] + motion + rng.normal(0, noise, (len(motion),) + places.shape)
    return md.Trajectory(xyz.astype(np.float32), topology)


def _with_a_still_frame(places: np.ndarray, motion: np.ndarray):
    """``places`` and ``motion`` with twenty still residues far off, out of
    contact, so that fitting each frame to the mean leaves the motion given."""
    still = np.stack([np.full(20, 5.0), np.arange(20) * 0.6, np.zeros(20)], axis=1)
    return (np.concatenate([places, still]),
            np.concatenate([motion, np.zeros((len(motion), 20, 3))], axis=1))


def _run(trajectory, tmp_path, **options):
    from fastmdxplora.analysis.residue_network import ResidueNetwork

    analysis = ResidueNetwork(output_dir=tmp_path, **options)
    return analysis, analysis.run(trajectory)


def test_betweenness_on_a_line_is_highest_in_the_middle():
    from fastmdxplora.analysis.residue_network import betweenness

    length = np.zeros((5, 5))
    for k in range(4):
        length[k, k + 1] = length[k + 1, k] = 1.0
    through, _ = betweenness(length)
    # Of the ten pairs, the middle node lies between four (0-3, 0-4, 1-3, 1-4).
    assert through.tolist() == pytest.approx([0.0, 0.3, 0.4, 0.3, 0.0])


def test_the_residue_joining_two_clusters_is_the_bottleneck(tmp_path):
    """Two tight clusters of four, joined only through one residue."""
    places = np.array([[0, 0, 0], [0.4, 0, 0], [0, 0.4, 0], [0.4, 0.4, 0],
                       [0.75, 0.2, 0],
                       [1.1, 0, 0], [1.5, 0, 0], [1.1, 0.4, 0], [1.5, 0.4, 0]], dtype=float)
    analysis, result = _run(_residues(places, np.zeros((FRAMES, 9, 3))), tmp_path)
    assert result.status == "ok", result.message
    table = result.data
    assert table.loc[table["betweenness"].idxmax(), "residue"] == "ALA5"
    assert analysis.findings["bottlenecks"][0]["residue"] == "ALA5"
    # Every one of the sixteen pairs across passes through it, of 36.
    assert analysis.findings["bottlenecks"][0]["betweenness"] == pytest.approx(16 / 36, abs=1e-4)
    assert analysis.findings["network"]["parts"] == 1


def test_the_path_takes_the_branch_that_moves_together(tmp_path):
    """A ring of six: from residue 1 to 4 one way round through 2 and 3,
    which move with them, the other way through 5 and 6, which move on
    their own. The path takes the first way."""
    angle = np.radians(np.arange(6) * 60.0)
    places = np.stack([0.38 * np.cos(angle), 0.38 * np.sin(angle), np.zeros(6)], axis=1)
    rng = np.random.default_rng(3)
    shared = 0.03 * rng.normal(size=(FRAMES, 1, 3))
    motion = np.concatenate([np.repeat(shared, 4, axis=1),
                             0.03 * rng.normal(size=(FRAMES, 2, 3))], axis=1)
    analysis, result = _run(_residues(*_with_a_still_frame(places, motion)), tmp_path,
                            contact_cutoff_nm=0.45, source="resid 0", sink="resid 3")
    assert result.status == "ok", result.message
    path = analysis.findings["path"]
    assert path["found"] is True
    assert [r.split()[0] for r in path["residues"]] == ["ALA1", "ALA2", "ALA3", "ALA4"]


def test_sites_in_parts_with_no_join_have_no_path(tmp_path):
    places = np.array([[0, 0, 0], [0.4, 0, 0], [0.8, 0, 0],
                       [3.0, 0, 0], [3.4, 0, 0], [3.8, 0, 0]], dtype=float)
    motion = np.zeros((FRAMES, 6, 3))
    analysis, result = _run(_residues(places, motion), tmp_path,
                            source="resid 0", sink="resid 5")
    assert result.status == "ok", result.message
    assert analysis.findings["network"]["parts"] == 2
    assert analysis.findings["path"]["found"] is False
    assert "no join" in analysis.findings["apart"]


def test_neighbours_along_a_chain_are_not_joined(tmp_path):
    topology = md.Topology()
    chain = topology.add_chain()
    for k in range(6):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=k + 1))
    places = np.stack([np.arange(6) * 0.38, np.zeros(6), np.zeros(6)], axis=1)
    rng = np.random.default_rng(1)
    xyz = places[None] + rng.normal(0, 0.01, (FRAMES, 6, 3))
    analysis, result = _run(md.Trajectory(xyz.astype(np.float32), topology), tmp_path)
    assert result.status == "ok", result.message
    # Only i, i+1 pairs are within 0.45 nm, and they are left out.
    assert analysis.findings["network"]["joins"] == 0
    assert (result.data["joins"] == 0).all()


@pytest.mark.parametrize("options, said", [
    ({"contact_cutoff_nm": 2.0}, "from 0.2 to 1.0 nm"),
    ({"contact_share": 0.0}, "above 0 and at most 1"),
    ({"source": "resid 0"}, "name both, or neither"),
])
def test_options_that_mean_nothing_are_refused(options, said, tmp_path):
    from fastmdxplora.analysis.residue_network import ResidueNetwork
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match=said):
        ResidueNetwork(output_dir=tmp_path, **options)


def test_a_site_outside_the_network_is_refused(tmp_path):
    places = np.stack([np.arange(4) * 0.4, np.zeros(4), np.zeros(4)], axis=1)
    _, result = _run(_residues(places, np.zeros((FRAMES, 4, 3))), tmp_path,
                     source="resid 0", sink="resid 40")
    assert result.status == "error" and "holds no residue of the network" in result.message


def test_it_runs_only_where_a_study_names_it():
    from fastmdxplora.analysis.residue_network import ResidueNetwork

    assert ResidueNetwork.requires_naming is True
