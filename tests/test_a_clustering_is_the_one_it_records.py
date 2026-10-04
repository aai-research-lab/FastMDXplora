"""What the cluster analysis writes describes the clustering it did.

The seed k-means started from, the hierarchy behind a Ward clustering, and
which frames were clustered: each was either missing from the record or
described a different calculation from the one that labelled the frames.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
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
