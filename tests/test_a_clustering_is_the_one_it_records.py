"""What the cluster analysis writes describes the clustering it did.

The seed k-means started from, the hierarchy behind a Ward clustering, and
which frames were clustered: each was either missing from the record or
described a different calculation from the one that labelled the frames.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.analysis.cluster import Cluster


def _alpha_carbons(xyz: np.ndarray, time_ps: np.ndarray | None = None) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(xyz.shape[1]):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain))
    return md.Trajectory(xyz.astype(np.float32), top, time=time_ps)


def _drifting(n_frames: int = 120) -> md.Trajectory:
    """Continuous drift with no clean states, where linkages disagree."""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.5, (10, 3))
    return _alpha_carbons(np.array([
        base + 0.02 * f * np.sin(np.arange(30).reshape(10, 3) + f / 7)
        + rng.normal(0, 0.05, (10, 3)) for f in range(n_frames)]))


def test_the_seed_and_the_starts_are_recorded(tmp_path):
    rng = np.random.default_rng(0)
    traj = _alpha_carbons(rng.normal(0, 0.5, (20, 8, 3)))

    Cluster(methods=["kmeans"], n_clusters=3, random_state=7, n_init=3,
            output_dir=tmp_path).run(traj)

    options = json.loads((tmp_path / "cluster" / "options.json").read_text())["options"]
    assert options["random_state"] == 7
    assert options["n_init"] == 3


@pytest.mark.parametrize("features", ["rmsd", "coordinates"])
def test_the_saved_ward_hierarchy_is_the_one_that_labelled_the_frames(tmp_path, features):
    """It was average linkage on the distances: cut at five clusters it
    agreed with the labels to an adjusted Rand index of 0.64."""
    from scipy.cluster.hierarchy import fcluster
    from sklearn.metrics import adjusted_rand_score

    analysis = Cluster(methods=["hierarchical"], n_clusters=5, linkage="ward",
                       features=features, output_dir=tmp_path)
    result = analysis.run(_drifting())
    assert result.status == "ok", result.message

    saved = np.load(tmp_path / "cluster" / "hierarchical_linkage.npy")
    cut = fcluster(saved, 5, "maxclust")

    assert adjusted_rand_score(result.data["hierarchical"], cut) == pytest.approx(1.0)
    recorded = json.loads((tmp_path / "cluster" / "options.json").read_text())["findings"]
    assert "ward_points" in recorded["hierarchical"]


def _three_states(n_frames: int = 90) -> md.Trajectory:
    """Three shapes of ten alpha carbons, visited in turn, each frame turned
    and moved at random, 10 ps apart."""
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(0)
    shapes = [rng.normal(0, 0.5, (10, 3)) for _ in range(3)]
    xyz = []
    for f in range(n_frames):
        shape = shapes[(f // 10) % 3] + rng.normal(0, 0.03, (10, 3))
        xyz.append(shape @ Rotation.random(random_state=f).as_matrix().T + rng.normal(0, 3, 3))
    return _alpha_carbons(np.array(xyz), time_ps=10.0 * (np.arange(n_frames) + 1))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("states")
    traj = _three_states()
    analysis = Cluster(methods=["kmeans", "dbscan"], n_clusters=3, eps=0.15,
                       min_samples=3, output_dir=out)
    result = analysis.run(traj)
    assert result.status == "ok", result.message
    return traj, analysis, result, out / "cluster"


class TestWhatTheClusteringLeavesBehind:

    def test_the_rmsd_matrix_is_written_with_its_times(self, run):
        traj, analysis, result, folder = run
        saved = np.load(folder / "cluster_rmsd_matrix.npz")

        assert saved["rmsd_nm"].shape == (traj.n_frames, traj.n_frames)
        assert np.allclose(saved["rmsd_nm"], analysis._distances, atol=1e-6)
        assert np.allclose(saved["time_ns"], traj.time / 1000.0)
        assert np.array_equal(saved["frames"], np.arange(traj.n_frames))
        assert (folder / "cluster_rmsd_matrix.png").is_file()
        assert folder / "cluster_rmsd_matrix.npz" in result.artifacts

    def test_the_heatmap_is_time_against_time_in_nm(self, run):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from fastmdxplora.analysis.cluster import _plot_distance_matrix

        traj, analysis, _result, _folder = run
        fig, ax = plt.subplots()
        _plot_distance_matrix(ax, analysis._distances, np.arange(traj.n_frames),
                              traj.time / 1000.0)
        labels = (ax.get_xlabel(), ax.get_ylabel(), fig.axes[-1].get_ylabel())
        plt.close(fig)
        assert labels == ("Time (ns)", "Time (ns)", "RMSD (nm)")

    def test_each_cluster_has_its_share_and_its_medoid(self, run):
        traj, analysis, result, folder = run
        labels = result.data["kmeans"]
        table = pd.read_csv(folder / "cluster_kmeans_populations.csv")

        assert table["fraction"].sum() == pytest.approx(1.0)
        assert table["frames"].sum() == traj.n_frames
        distances = analysis._distances
        for row in table.itertuples():
            members = np.nonzero(labels == row.cluster)[0]
            summed = distances[np.ix_(members, members)].sum(axis=1)
            assert row.medoid_frame == members[np.argmin(summed)]
            assert row.medoid_time_ns == pytest.approx(traj.time[row.medoid_frame] / 1000.0)
            structure = md.load(str(folder / f"cluster_kmeans_medoid_{row.cluster}.pdb"))
            assert structure.n_atoms == traj.n_atoms
            assert md.rmsd(structure, traj, frame=row.medoid_frame)[0] < 1e-3

    def test_noise_is_in_no_cluster(self, run):
        _traj, _analysis, result, folder = run
        labels = result.data["dbscan"]
        table = pd.read_csv(folder / "cluster_dbscan_populations.csv")

        assert -1 not in set(table["cluster"])
        assert table["frames"].sum() == int((labels != -1).sum())
