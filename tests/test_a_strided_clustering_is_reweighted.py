"""A biased run's cluster populations are reweighted at any stride.

Clustering clusters at most 5,000 frames, every k-th past them, and the
reweighting read only labels of every frame: a biased run of more than
5,000 frames had no reweighted populations, and nothing said so. Each frame
clustered now carries its own weight; labels whose frames' weights cannot be
read are refused by name, never given a fraction unweighted. The Viewer's
states placed the labels of every 3rd frame on the first frames analysed.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pytest

import fastmdxplora.analysis  # noqa: F401 -- registers the analyses
from fastmdxplora.analysis import reweighted_averages as reweighting
from fastmdxplora.analysis.cluster import Cluster
from fastmdxplora.analysis.reweight import Weights, weighted_mean
from fastmdxplora.analysis.reweighted_averages import reweight_results, weights_for_run

sys.path.insert(0, str(Path(__file__).parent))
from test_a_clustering_fits_the_machine import _chain  # noqa: E402
from test_reweighted_averages import _Result, _write_run  # noqa: E402


def _biased(tmp_path: Path, n: int, **options):
    """A metadynamics run of ``n`` frames, clustered on coordinates with
    k-means (no matrix, so many frames are cheap), and its reweighting."""
    analysis = _write_run(tmp_path)
    times = np.linspace(1.0, 200.0, n)
    result = Cluster(output_dir=analysis, methods=["kmeans"], features="coordinates",
                     n_clusters=3, **options).run(_chain(n, seed=3))
    assert result.status == "ok", result.message
    record = reweight_results({"cluster": result}, {"cluster": Cluster}, n_frames=n,
                              frame_times_ps=times, output_dir=analysis)
    weights, _ = weights_for_run(analysis, times)
    return result, record, weights, analysis


def _expected(labels: np.ndarray, frames: np.ndarray, weights: Weights) -> dict[int, float]:
    """Each state's reweighted fraction, from the frames' own weights."""
    own = weights.values[frames]
    return {int(k): float(np.sum(own * (labels == k)) / np.sum(own))
            for k in np.unique(labels)}


@pytest.mark.parametrize("n, options, stride", [
    (6001, {}, 2),                                    # the default cap: 3,001 frames
    (900, {"stride": 3, "max_frames": None}, 3),      # a stride of its own
], ids=["default-cap", "explicit-stride"])
def test_each_frame_clustered_carries_its_own_weight(tmp_path, n, options, stride):
    result, record, weights, _ = _biased(tmp_path, n, **options)
    assert record is not None
    entries = [e for e in record["populations"] if e["method"] == "kmeans"]
    assert entries, record.get("populations_refused")
    entry = entries[0]
    labels = np.asarray(result.data["kmeans"])
    frames = np.arange(0, n, stride)
    assert labels.size == frames.size
    assert entry["stride"] == stride and entry["first_frame"] == 0
    assert entry["n_frames_labelled"] == frames.size
    expected = _expected(labels, frames, weights)
    for state in entry["states"]:
        assert state["reweighted_fraction"] == pytest.approx(expected[state["label"]],
                                                             rel=1e-9)
        assert state["raw_fraction"] == pytest.approx(float(np.mean(labels == state["label"])))
    # The weights do move the populations: a bias undone, not counts.
    assert any(abs(s["reweighted_fraction"] - s["raw_fraction"]) > 1e-3
               for s in entry["states"])
    assert f"every {'2nd' if stride == 2 else '3rd'} frame from frame 0" in entry["said"]
    assert record["populations_refused"] == []


def test_a_start_later_than_the_first_frame_is_weighted_from_it(tmp_path):
    analysis = _write_run(tmp_path)
    n = 400
    times = np.linspace(1.0, 200.0, n)
    traj = _chain(n, seed=3)
    traj.time = times
    result = Cluster(output_dir=analysis, methods=["kmeans"], features="coordinates",
                     n_clusters=3, start=0.1, stride=2).run(traj)
    assert result.status == "ok", result.message
    first = json.loads(Path(result.options_path).read_text())["findings"]["frames"][
        "first_frame"]
    assert first > 0
    record = reweight_results({"cluster": result}, {"cluster": Cluster}, n_frames=n,
                              frame_times_ps=times, output_dir=analysis)
    weights, _ = weights_for_run(analysis, times)
    entry = record["populations"][0]
    labels = np.asarray(result.data["kmeans"])
    expected = _expected(labels, np.arange(first, n, 2), weights)
    assert entry["first_frame"] == first
    for state in entry["states"]:
        assert state["reweighted_fraction"] == pytest.approx(expected[state["label"]])


def test_labels_whose_frames_cannot_be_read_are_refused_by_name(tmp_path):
    """No record of which frames, and fewer labels than frames: refused,
    not counted unweighted."""
    analysis = _write_run(tmp_path)
    times = np.linspace(1.0, 200.0, 60)
    labels = (np.sin(np.arange(30) / 4.0) > 0).astype(int)
    record = reweight_results({"cluster": _Result({"kmeans": labels})},
                              {"cluster": Cluster}, n_frames=60, frame_times_ps=times,
                              output_dir=analysis)
    assert record is not None
    assert record["populations"] == []
    refused = record["populations_refused"]
    assert len(refused) == 1 and refused[0]["analysis"] == "cluster"
    assert refused[0]["method"] == "kmeans"
    assert refused[0]["code"] == "analysis.data.absent"
    assert refused[0]["said"].startswith("cluster (kmeans) is not reweighted: 30 labels")

    from fastmdxplora.report.reweighted import reweighted_section

    said = reweighted_section(tmp_path, record)
    assert "cluster (kmeans) is not reweighted" in said
    assert "| State | Reweighted" not in said


def test_a_frame_with_no_weight_is_refused(tmp_path, monkeypatch):
    weights = Weights(np.array([1.0, np.nan, 1.0, 1.0]), 3.0, True)
    assert reweighting.weights_of_frames(weights, np.array([0, 2])) is not None
    assert reweighting.weights_of_frames(weights, np.array([0, 1])) is None
    assert reweighting.weights_of_frames(weights, np.array([0, 4])) is None

    analysis = _write_run(tmp_path)
    times = np.linspace(1.0, 200.0, 40)
    real = reweighting.weights_for_run

    def with_a_gap(directory, frame_times):
        found, provenance = real(directory, frame_times)
        values = found.values.copy()
        values[6] = np.nan
        return Weights(values, found.effective_sample_size, found.converged), provenance

    monkeypatch.setattr(reweighting, "weights_for_run", with_a_gap)
    labels = (np.arange(20) % 2).astype(int)

    result = _Result({"kmeans": labels})
    record_path = tmp_path / "options.json"
    record_path.write_text(json.dumps({"findings": {"frames": {
        "first_frame": 0, "stride": 2}}}))
    result.options_path = record_path
    record = reweight_results({"cluster": result}, {"cluster": Cluster}, n_frames=40,
                              frame_times_ps=times, output_dir=analysis)
    assert record["populations"] == []
    assert "has no weight that can be read" in record["populations_refused"][0]["said"]


def test_the_report_says_the_frames_weighted(tmp_path):
    _, record, _, _ = _biased(tmp_path, 6001)
    from fastmdxplora.report.reweighted import reweighted_section

    said = reweighted_section(tmp_path, record)
    assert "Weighted by each labelled frame's own weight: 3,001 of the 6,001 frames" in said


def test_weighted_mean_of_every_frame_is_unchanged(tmp_path):
    """One label per frame and no stride, as before."""
    analysis = _write_run(tmp_path)
    times = np.linspace(1.0, 200.0, 60)
    labels = (np.sin(times / 7.0) > 0).astype(int)
    record = reweight_results({"cluster": _Result({"kmeans": labels})}, {"cluster": Cluster},
                              n_frames=60, frame_times_ps=times, output_dir=analysis)
    weights, _ = weights_for_run(analysis, times)
    entry = record["populations"][0]
    assert "stride" not in entry and "said" not in entry
    for state in entry["states"]:
        assert state["reweighted_fraction"] == pytest.approx(
            weighted_mean((labels == state["label"]).astype(float), weights))


# --------------------------------------------------------------------------
# The Viewer's states, placed on the frames they label
# --------------------------------------------------------------------------
DATA = Path(__file__).parent / "data" / "assemblies"
FRAMES, SHIFT = 30, 0.4


@pytest.fixture(scope="module")
def strided_study(tmp_path_factory):
    """Trypsin with residues 16 to 65 moved 0.4 nm in the second half,
    clustered on every 3rd frame."""
    md = pytest.importorskip("mdtraj")
    from fastmdxplora.analysis import AnalysisOrchestrator
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path_factory.mktemp("states") / "study"
    (root / "simulation").mkdir(parents=True)
    raw = root / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    protein = whole.atom_slice(whole.topology.select("protein"))
    moving = protein.topology.select("resSeq 16 to 65")
    xyz = np.repeat(protein.xyz, FRAMES, axis=0)
    xyz[FRAMES // 2:, moving, 0] += SHIFT
    xyz += np.random.default_rng(9).normal(0, 0.01, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, protein.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    AnalysisOrchestrator(str(root / "simulation" / "production.dcd"),
                         str(root / "simulation" / "trajectory_topology.pdb"),
                         output_dir=str(root / "analysis")).run(
        include=["cluster"], options={"cluster": {"n_clusters": 2, "methods": ["kmeans"],
                                                  "stride": 3}})
    return root


def test_the_viewer_s_states_are_of_the_frames_labelled(strided_study):
    from fastmdxplora.gui.states import states_of

    said = states_of(strided_study)
    assert said["ok"], said
    # Labels of frames 0, 3, ..., 27: each frame played takes the state of
    # the labelled frame nearest it, so the first half's state holds frames
    # 0 to 13 and the second's 14 to 29.
    played = sorted(state["played"] for state in said["states"])
    assert played == [14, 16], said["states"]
    early, late = sorted(said["states"], key=lambda state: state["representative"])
    assert early["representative"] < FRAMES // 2 <= late["representative"]
    assert early["frames"] == late["frames"] == 5
    assert said["of_played"] == [early["state"]] * 14 + [late["state"]] * 16
    assert "among the 10 frames it clustered (every 3rd of the 30 analysed)" in said["said"]
