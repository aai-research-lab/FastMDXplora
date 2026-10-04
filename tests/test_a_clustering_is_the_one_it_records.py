"""What the cluster analysis writes describes the clustering it did.

The seed k-means started from, the hierarchy behind a Ward clustering, and
which frames were clustered: each was either missing from the record or
described a different calculation from the one that labelled the frames.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np

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
