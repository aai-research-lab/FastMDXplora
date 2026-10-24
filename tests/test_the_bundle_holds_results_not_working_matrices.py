"""The project bundle holds results and records, and says what working
matrices it left in the study's folder.

A chignolin study's bundle was 7.3 GB: it zipped the clustering's whole
frame-by-frame matrix twice over beside 98 MB of results.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from fastmdxplora.report import bundle as bundling
from fastmdxplora.report.bundle import LEFT_OUT_LIST, build_bundle, left_out_of_bundle


def _study(root: Path) -> Path:
    cluster = root / "analysis" / "cluster"
    cluster.mkdir(parents=True)
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.dat").write_text("0.1\n0.2\n")
    (root / "report").mkdir()
    np.save(cluster / "cluster_rmsd_matrix_full.npy", np.zeros((40, 40), dtype=np.float32))
    np.save(cluster / "hierarchical_distance_matrix.npy", np.zeros((40, 40)))
    # An older study's map, the whole matrix in it, past the bound below.
    rng = np.random.default_rng(0)
    np.savez(cluster / "cluster_rmsd_matrix.npz", rmsd_nm=rng.random((60, 60)))
    (cluster / "cluster_kmeans.dat").write_text("frame,cluster\n0,0\n")
    (root / "simulation").mkdir()
    (root / "simulation" / "production.dcd").write_bytes(b"\0" * 5000)
    return root


def test_a_large_intermediate_is_left_out_and_said_inside(tmp_path, monkeypatch):
    monkeypatch.setattr(bundling, "MOST_MATRIX_BYTES", 20_000)
    root = _study(tmp_path / "study")
    build_bundle(orchestrator=SimpleNamespace(output_dir=root), output_dir=root / "report")

    with zipfile.ZipFile(root / "report" / "project_bundle.zip") as archive:
        names = set(archive.namelist())
        listed = archive.read(LEFT_OUT_LIST).decode()
    left = {"analysis/cluster/cluster_rmsd_matrix_full.npy",
            "analysis/cluster/hierarchical_distance_matrix.npy",
            "analysis/cluster/cluster_rmsd_matrix.npz"}
    assert not left & names
    # The results and records, and the trajectory as before, go in.
    assert {"analysis/rmsd/rmsd.dat", "analysis/cluster/cluster_kmeans.dat",
            "simulation/production.dcd"} <= names
    for rel in left:
        assert rel in listed
    assert "study's folder" in listed
    assert "past the bundle's bound of 20 KB" in listed


def test_a_small_map_goes_in(tmp_path):
    root = _study(tmp_path / "study")
    left = {rel for rel, _, _ in left_out_of_bundle(root)}
    assert "analysis/cluster/cluster_rmsd_matrix.npz" not in left
    assert "analysis/cluster/cluster_rmsd_matrix_full.npy" in left


def test_the_report_says_the_same_list(tmp_path, monkeypatch):
    from fastmdxplora.report.document import _bundle_section

    monkeypatch.setattr(bundling, "MOST_MATRIX_BYTES", 20_000)
    root = _study(tmp_path / "study")
    said = _bundle_section(root)
    assert "project_bundle.zip" in said and LEFT_OUT_LIST in said
    for rel, _, _ in left_out_of_bundle(root):
        assert f"`{rel}`" in said
    assert "3 working files" in said


def test_a_bundle_with_nothing_left_out_says_nothing(tmp_path):
    from fastmdxplora.report.document import _bundle_section

    root = tmp_path / "study"
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.dat").write_text("0.1\n")
    (root / "report").mkdir()
    build_bundle(orchestrator=SimpleNamespace(output_dir=root), output_dir=root / "report")
    with zipfile.ZipFile(root / "report" / "project_bundle.zip") as archive:
        assert LEFT_OUT_LIST not in archive.namelist()
    assert _bundle_section(root) == ""
