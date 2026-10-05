"""Clustering and the projections say which frames they read.

Both read every frame by default, the relaxation from the starting structure
included, and said nothing about it. That stays the default, so results do
not change; the record now says how many frames were read and whether the
equilibration the RMSD detects is among them, and ``start`` begins later.
"""

from __future__ import annotations

import json

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.analysis.cluster import Cluster
from fastmdxplora.analysis.dimred import DimRed
from fastmdxplora.refusals import StudyError


def _relaxing_then_sampling(n_relax: int = 60, n_after: int = 240) -> md.Trajectory:
    """Ten alpha carbons that relax steadily away from their start for
    ``n_relax`` frames, then fluctuate about where they arrived. 10 ps a
    frame, so the relaxation ends at 0.6 ns."""
    rng = np.random.default_rng(4)
    start = rng.normal(0, 0.5, (10, 3))
    direction = rng.normal(0, 1, (10, 3))
    direction /= np.linalg.norm(direction)
    frames = []
    for f in range(n_relax + n_after):
        progress = min(f, n_relax) / n_relax
        frames.append(start + 1.2 * progress * direction + rng.normal(0, 0.02, (10, 3)))
    top = md.Topology()
    chain = top.add_chain()
    for _ in range(10):
        top.add_atom("CA", md.element.carbon, top.add_residue("ALA", chain))
    n = len(frames)
    return md.Trajectory(np.array(frames, dtype=np.float32), top,
                         time=10.0 * (np.arange(n) + 1))


@pytest.fixture(scope="module")
def traj() -> md.Trajectory:
    return _relaxing_then_sampling()


def test_the_record_says_every_frame_and_the_equilibration_were_clustered(tmp_path, traj):
    analysis = Cluster(methods=["kmeans"], n_clusters=3, output_dir=tmp_path)
    result = analysis.run(traj)
    assert result.status == "ok", result.message

    record = json.loads((tmp_path / "cluster" / "options.json").read_text())
    frames = record["findings"]["frames"]
    assert frames["n_frames_analysed"] == traj.n_frames
    assert frames["first_frame"] == 0
    assert 30 <= frames["equilibration_frames_by_rmsd"] <= 120
    assert frames["equilibration_included"] is True
    assert "included" in frames["said"]
    assert "start" not in record["options"]          # the default is as it was


def test_the_projection_says_so_too(tmp_path, traj):
    analysis = DimRed(methods=["pca"], output_dir=tmp_path)
    assert analysis.run(traj).status == "ok"

    assert analysis.findings["frames"]["n_frames_analysed"] == traj.n_frames
    assert analysis.findings["frames"]["equilibration_included"] is True


def test_start_at_the_equilibration_leaves_it_out(tmp_path, traj):
    analysis = Cluster(methods=["kmeans"], n_clusters=3, start="equilibrated",
                       output_dir=tmp_path)
    result = analysis.run(traj)
    assert result.status == "ok", result.message

    frames = analysis.findings["frames"]
    first = frames["first_frame"]
    assert first == frames["equilibration_frames_by_rmsd"] > 0
    assert frames["equilibration_included"] is False
    assert len(result.data["kmeans"]) == traj.n_frames - first
    table = pd.read_csv(tmp_path / "cluster" / "cluster_kmeans.dat")
    assert table["frame"].iloc[0] == first           # frames of the trajectory given
    assert analysis.options["start"] == "equilibrated"


def test_start_at_a_time_begins_at_the_first_frame_from_it(tmp_path, traj):
    analysis = DimRed(methods=["pca"], start=1.0, output_dir=tmp_path)
    result = analysis.run(traj)
    assert result.status == "ok", result.message

    assert analysis.findings["frames"]["first_frame"] == 99     # 1000 ps is frame 99
    assert len(result.data["pca"]) == traj.n_frames - 99
    table = pd.read_csv(tmp_path / "dimred" / "dimred_pca.dat")
    assert table["frame"].iloc[0] == 99


def test_a_time_needs_a_clock(tmp_path, traj):
    """The loader fills the time with NaN where the run did not say."""
    clockless = md.Trajectory(traj.xyz, traj.topology,
                              time=np.full(traj.n_frames, np.nan))
    with pytest.raises(StudyError) as raised:
        Cluster(methods=["kmeans"], n_clusters=3, start=1.0,
                output_dir=tmp_path).compute(clockless)
    assert raised.value.code == "analysis.data.absent"
    # The default, the first frame, is found without one.
    labels = Cluster(methods=["kmeans"], n_clusters=3, start=0,
                     output_dir=tmp_path).compute(clockless)["kmeans"]
    assert len(labels) == traj.n_frames


@pytest.mark.parametrize("given,code", [
    ("halfway", "analysis.option.not_permitted"),
    (-1.0, "analysis.option.out_of_range"),
    (True, "analysis.option.wrong_type"),
])
def test_a_start_that_is_not_one_is_refused(given, code):
    with pytest.raises(StudyError) as raised:
        DimRed(start=given)
    assert raised.value.code == code
