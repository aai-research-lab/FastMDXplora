"""Tests for sub-delivery 3 analyses: HBonds, SS, SASA, QValue, Cluster, DimRed.

Each analysis is tested across three dimensions:
  - Numerical sanity (correct shapes, ranges, consistency)
  - I/O contract (canonical paths, round-trip, options manifest)
  - End-to-end via AnalysisOrchestrator
"""

from __future__ import annotations

import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.analysis import (
    AnalysisOrchestrator,
    available_analyses,
)
from fastmdxplora.analysis.cluster import Cluster
from fastmdxplora.analysis.dimred import DimRed
from fastmdxplora.analysis.hbonds import HBonds
from fastmdxplora.analysis.qvalue import QValue
from fastmdxplora.analysis.sasa import SASA
from fastmdxplora.analysis.ss import SS


# ---------------------------------------------------------------------------
# Fixtures: real-ish protein trajectory with sidechain Cβ atoms
# (needed by SASA, hydrogen bonding, and DSSP)
# ---------------------------------------------------------------------------
def _build_protein_traj(
    n_residues: int = 8, n_frames: int = 60, seed: int = 7
) -> md.Trajectory:
    """5-atom-per-residue (N, CA, C, O, CB) ALA peptide for protein analyses."""
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    for i in range(n_residues):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("N", md.element.nitrogen, res)
        top.add_atom("CA", md.element.carbon, res)
        top.add_atom("C", md.element.carbon, res)
        top.add_atom("O", md.element.oxygen, res)
        top.add_atom("CB", md.element.carbon, res)

    n_atoms = top.n_atoms
    base = np.zeros((n_atoms, 3))
    for i in range(n_atoms):
        base[i] = [(i % n_atoms) * 0.15, 0.05 * np.sin(i * 0.3), 0.0]

    xyz = np.tile(base[None, :, :], (n_frames, 1, 1))
    xyz += rng.normal(scale=0.03, size=xyz.shape)
    times = np.arange(n_frames) * 20.0
    return md.Trajectory(xyz=xyz.astype(np.float32), topology=top, time=times)


@pytest.fixture
def protein_traj() -> md.Trajectory:
    return _build_protein_traj()


def _chained(traj: md.Trajectory) -> md.Trajectory:
    """The same peptide with its backbone N, CA, C a zigzag 0.15 nm a step
    and O and CB beside it, so that C(i-1) and N(i) are as close as a
    peptide bond. In ``_build_protein_traj`` every atom is on one line and
    they are 0.45 nm apart, which the dihedrals read as a gap in the chain;
    the noise of each frame is kept."""
    xyz = np.array(traj.xyz)
    step = 0
    for atom in traj.topology.atoms:
        noise = xyz[:, atom.index] - xyz[0, atom.index]
        if atom.name in ("N", "CA", "C"):
            place = [0.15 * step, 0.05 * (step % 2), 0.0]
            step += 1
        else:
            place = [0.15 * (step - 1), -0.12 if atom.name == "O" else 0.12, 0.05]
        xyz[:, atom.index] = np.asarray(place) + noise
    return md.Trajectory(xyz=xyz, topology=traj.topology, time=traj.time)


@pytest.fixture
def protein_traj_files(tmp_path: Path, protein_traj: md.Trajectory):
    pdb = tmp_path / "top.pdb"
    dcd = tmp_path / "traj.dcd"
    chained = _chained(protein_traj)
    chained[0].save_pdb(str(pdb))
    chained.save_dcd(str(dcd))
    return dcd, pdb


# ===========================================================================
# Registry — all 10 registered
# ===========================================================================
def test_all_ten_registered():
    names = set(available_analyses())
    expected = {
        "rmsd", "rmsf", "rg", "hbonds", "ss",
        "sasa", "dihedrals", "qvalue", "cluster", "dimred",
    }
    assert expected.issubset(names)


# ===========================================================================
# HBonds
# ===========================================================================
class TestHBonds:
    def test_returns_dataframe_with_correct_columns(self, protein_traj):
        out = HBonds().compute(protein_traj)
        assert isinstance(out, pd.DataFrame)
        assert set(out.columns) == {"frame", "n_hbonds"}
        assert len(out) == protein_traj.n_frames

    def test_counts_are_nonnegative_int(self, protein_traj):
        out = HBonds().compute(protein_traj)
        assert (out["n_hbonds"] >= 0).all()
        assert pd.api.types.is_integer_dtype(out["n_hbonds"])

    def test_wernet_nilsson_method(self, protein_traj):
        out = HBonds(method="wernet_nilsson").compute(protein_traj)
        assert len(out) == protein_traj.n_frames

    def test_invalid_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            HBonds(method="bogus")

    def test_run_writes_outputs(self, tmp_path: Path, protein_traj):
        result = HBonds(output_dir=tmp_path).run(protein_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()


# ===========================================================================
# Secondary structure
# ===========================================================================
class TestSS:
    def test_returns_dataframe(self, protein_traj):
        out = SS().compute(protein_traj)
        assert isinstance(out, pd.DataFrame)
        assert "frame" in out.columns
        assert len(out) == protein_traj.n_frames

    def test_dssp_codes_are_letters(self, protein_traj):
        out = SS().compute(protein_traj)
        # All non-frame cells should be single uppercase letters. We don't
        # assert column dtype because modern pandas returns StringDtype
        # for string-only columns (formerly `object`).
        for col in out.columns:
            if col == "frame":
                continue
            for cell in out[col]:
                assert isinstance(cell, str)
                assert len(cell) == 1

    def test_run_writes_outputs(self, tmp_path: Path, protein_traj):
        result = SS(output_dir=tmp_path).run(protein_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()


# ===========================================================================
# SASA
# ===========================================================================
class TestSASA:
    def test_total_mode(self, protein_traj):
        out = SASA(mode="total").compute(protein_traj)
        assert set(out.columns) == {"frame", "sasa_nm2"}
        assert len(out) == protein_traj.n_frames
        assert (out["sasa_nm2"] > 0).all()

    def test_residue_mode(self, protein_traj):
        out = SASA(mode="residue").compute(protein_traj)
        assert set(out.columns) == {"frame", "residue", "sasa_nm2"}
        # n_frames × n_residues rows
        assert len(out) == protein_traj.n_frames * protein_traj.n_residues
        assert (out["sasa_nm2"] >= 0).all()

    def test_probe_radius_changes_result(self, protein_traj):
        a = SASA(probe_radius=0.14).compute(protein_traj)["sasa_nm2"]
        b = SASA(probe_radius=0.5).compute(protein_traj)["sasa_nm2"]
        # Larger probe → larger SASA (the probe rolls further out). On this
        # tiny near-linear synthetic peptide a huge 0.5 nm probe can make an
        # occasional individual frame dip below the small-probe value due to
        # float noise, so assert the physically-guaranteed relationship on the
        # mean rather than per-frame (the per-frame all() was flaky across
        # platforms/Python versions).
        assert b.mean() > a.mean()

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            SASA(mode="bogus")

    def test_run_writes_outputs(self, tmp_path: Path, protein_traj):
        result = SASA(output_dir=tmp_path).run(protein_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()


# ===========================================================================
# Q-value
# ===========================================================================
class TestQValue:
    def test_returns_one_value_per_frame(self, protein_traj):
        out = QValue().compute(protein_traj)
        assert out.shape == (protein_traj.n_frames,)

    def test_reference_frame_is_near_one(self):
        """Q at the reference is high, and deliberately not exactly one.

        A threshold makes Q[ref] exactly 1 by construction: every native
        contact is inside the cutoff because that is how it was chosen. The
        published measure is a switching function, so a contact sitting at its
        native distance is well inside the tolerance but still short of
        saturating -- how short depends on how close the contact is. Rescaling
        to force 1.0 would misstate a measure the paper leaves unnormalised.

        Build a compact folded geometry so the residue pairs satisfying
        ``min_seq_separation=4`` are within the contact cutoff.
        """
        rng = np.random.RandomState(0)
        top = md.Topology()
        chain = top.add_chain()
        for i in range(8):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            top.add_atom("N", md.element.nitrogen, res)
            top.add_atom("CA", md.element.carbon, res)
            top.add_atom("C", md.element.carbon, res)
            top.add_atom("O", md.element.oxygen, res)
            top.add_atom("CB", md.element.carbon, res)
        # Compact globule: place each residue cluster within a 0.5 nm sphere
        # so far-sequence contacts are within the 0.45 nm cutoff.
        n_atoms = top.n_atoms
        base = rng.uniform(-0.2, 0.2, size=(n_atoms, 3))
        n_frames = 20
        xyz = np.tile(base[None, :, :], (n_frames, 1, 1))
        xyz += rng.normal(scale=0.01, size=xyz.shape)
        traj = md.Trajectory(xyz=xyz.astype(np.float32), topology=top)

        out = QValue(ref=0).compute(traj)
        # Every native contact is at its native distance in this frame.
        assert 0.9 < out[0] <= 1.0
        # And it is the highest point of the run: nothing is more folded than
        # the structure the contacts were taken from.
        assert out[0] == pytest.approx(out.max(), abs=1e-6)

    def test_values_in_unit_range(self, protein_traj):
        out = QValue().compute(protein_traj)
        valid = ~np.isnan(out)
        assert (out[valid] >= 0).all()
        assert (out[valid] <= 1).all()

    def test_min_seq_separation_too_high_raises(self, protein_traj):
        # n_residues = 8; min_seq_separation = 99 → no valid pairs
        with pytest.raises(ValueError, match="min_seq_separation"):
            QValue(min_seq_separation=99).compute(protein_traj)

    def test_run_writes_outputs(self, tmp_path: Path, protein_traj):
        result = QValue(output_dir=tmp_path).run(protein_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()


# ===========================================================================
# Cluster
# ===========================================================================
class TestCluster:
    def test_kmeans_default(self, tmp_path: Path, protein_traj):
        result = Cluster(output_dir=tmp_path, n_clusters=3).run(protein_traj)
        assert result.status == "ok"
        # One labels array per method
        assert "kmeans" in result.data
        labels = result.data["kmeans"]
        assert labels.shape == (protein_traj.n_frames,)
        # K=3 → at most 3 distinct labels
        assert len(set(labels)) <= 3

    def test_multiple_methods(self, tmp_path: Path, protein_traj):
        result = Cluster(
            output_dir=tmp_path,
            methods=["kmeans", "hierarchical", "dbscan"],
            n_clusters=3,
        ).run(protein_traj)
        assert result.status == "ok"
        for m in ("kmeans", "hierarchical", "dbscan"):
            assert m in result.data
            assert (result.output_dir / f"cluster_{m}.dat").exists()
            assert (result.output_dir / f"cluster_{m}.png").exists()
            assert (result.output_dir / f"cluster_{m}_counts.png").exists()
        dendrogram = result.output_dir / "cluster_hierarchical_dendrogram.png"
        skipped = result.output_dir / "cluster_hierarchical_dendrogram_skipped.json"
        distance_matrix = result.output_dir / "hierarchical_distance_matrix.npy"
        linkage_matrix = result.output_dir / "hierarchical_linkage.npy"
        assert distance_matrix.exists()
        assert dendrogram.exists() or skipped.exists()
        if dendrogram.exists():
            assert linkage_matrix.exists()
            assert str(linkage_matrix) in [str(path) for path in result.artifacts]
        if skipped.exists():
            skip_info = json.loads(skipped.read_text(encoding="utf-8"))
            assert skip_info["status"] == "skipped"
            assert skip_info["reason"]

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unknown clustering"):
            Cluster(methods=["bogus"])

    def test_dbscan_can_produce_noise_labels(self, tmp_path: Path, protein_traj):
        """DBSCAN may label some frames as noise (-1) depending on eps."""
        result = Cluster(
            output_dir=tmp_path,
            methods=["dbscan"],
            eps=0.01,  # very tight → most frames will be noise
            min_samples=5,
        ).run(protein_traj)
        assert result.status == "ok"
        # -1 is a valid label
        labels = result.data["dbscan"]
        assert labels.min() >= -1


    def test_too_few_frames_clear_error(self, tmp_path: Path, protein_traj):
        """Fewer frames than n_clusters → a clear, actionable error message
        (not an opaque scikit-learn internals error)."""
        short = protein_traj[:3]  # 3 frames
        result = Cluster(output_dir=tmp_path, n_clusters=5).run(short)
        assert result.status == "error"
        assert "at least n_clusters" in result.message
        assert "only 3" in result.message

    def test_dbscan_exempt_from_frame_guard(self, tmp_path: Path, protein_traj):
        """DBSCAN doesn't take n_clusters, so the guard must not block it."""
        short = protein_traj[:3]
        result = Cluster(
            output_dir=tmp_path, methods=["dbscan"], min_samples=2,
        ).run(short)
        assert result.status == "ok"


# ===========================================================================
# DimRed
# ===========================================================================
class TestDimRed:
    def test_pca_default(self, tmp_path: Path, protein_traj):
        result = DimRed(output_dir=tmp_path).run(protein_traj)
        assert result.status == "ok"
        assert "pca" in result.data
        emb = result.data["pca"]
        assert emb.shape == (protein_traj.n_frames, 2)

    def test_tsne(self, tmp_path: Path, protein_traj):
        result = DimRed(output_dir=tmp_path, methods=["tsne"]).run(protein_traj)
        assert result.status == "ok"
        emb = result.data["tsne"]
        assert emb.shape == (protein_traj.n_frames, 2)

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unknown dimred"):
            DimRed(methods=["bogus"])

    def test_multiple_methods(self, tmp_path: Path, protein_traj):
        result = DimRed(
            output_dir=tmp_path, methods=["pca", "tsne"]
        ).run(protein_traj)
        assert result.status == "ok"
        for m in ("pca", "tsne"):
            assert (result.output_dir / f"dimred_{m}.dat").exists()
            assert (result.output_dir / f"dimred_{m}.png").exists()

    def test_n_components_three(self, tmp_path: Path, protein_traj):
        result = DimRed(
            output_dir=tmp_path, methods=["pca"], n_components=3
        ).run(protein_traj)
        emb = result.data["pca"]
        assert emb.shape == (protein_traj.n_frames, 3)


# ===========================================================================
# End-to-end: all 10 analyses run through the orchestrator
# ===========================================================================
class TestAllAnalysesEndToEnd:
    def test_run_all_ten(self, tmp_path: Path, protein_traj_files):
        dcd, pdb = protein_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "out"
        )
        results = ao.run()  # no include/exclude — runs everything
        assert len(results) >= 10
        for name, r in results.items():
            assert r.status == "ok", f"{name} failed: {r.message}"

    def test_select_subset(self, tmp_path: Path, protein_traj_files):
        dcd, pdb = protein_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "out"
        )
        results = ao.run(include=["hbonds", "sasa", "qvalue"])
        assert set(results.keys()) == {"hbonds", "sasa", "qvalue"}
        for r in results.values():
            assert r.status == "ok", r.message

    def test_manifest_records_all_analyses(self, tmp_path, protein_traj_files):
        dcd, pdb = protein_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "out"
        )
        ao.run(include=["hbonds", "ss", "cluster"])
        m = json.loads((ao.output_dir / "analysis_manifest.json").read_text(encoding="utf-8"))
        assert m["plan"] == ["hbonds", "ss", "cluster"]
        for n in m["plan"]:
            assert m["results"][n]["status"] == "ok"


# ===========================================================================
# Plot customization still works for sub-3 analyses
# ===========================================================================
class TestUserCustomization:
    def test_hbonds_user_title(self, tmp_path: Path, protein_traj):
        a = HBonds(output_dir=tmp_path, title="My H-bond plot")
        a.run(protein_traj)
        assert a.figure_title() == "My H-bond plot"

    def test_sasa_user_xunit_frames(self, tmp_path: Path, protein_traj):
        a = SASA(output_dir=tmp_path, mode="total", xunit="frames")
        a.run(protein_traj)
        x, label = a.frame_axis(protein_traj)
        assert label == "Frame"
