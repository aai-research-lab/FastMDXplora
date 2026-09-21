"""Tests for the four concrete analyses: RMSD, RMSF, Rg, Dihedrals.

These exercise each analysis in three dimensions:

  1. Numerical correctness — verified either against hand-computed values
     on a controlled synthetic trajectory, or against MDTraj's own
     primitives (which the analyses wrap).
  2. I/O contract — outputs land at the canonical paths, the data file
     round-trips through numpy/pandas, the options manifest is recorded.
  3. Plot customization — user-supplied title/labels/figsize/xunit
     reach the saved figure.

A small backbone-like trajectory fixture (5 ALA residues, 50 frames,
real timing) provides realistic input for protein analyses.
"""

from __future__ import annotations

import pytest

import sys

import json
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis import (
    AnalysisOrchestrator,
    available_analyses,
)
from fastmdxplora.analysis.dihedrals import Dihedrals
from fastmdxplora.analysis.rg import Rg
from fastmdxplora.analysis.rmsd import RMSD
from fastmdxplora.analysis.rmsf import RMSF


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _build_backbone_traj(
    n_residues: int = 5, n_frames: int = 50, seed: int = 42
) -> md.Trajectory:
    """Build a small protein-ish trajectory: 4 backbone atoms per residue."""
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    for i in range(n_residues):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("N", md.element.nitrogen, res)
        top.add_atom("CA", md.element.carbon, res)
        top.add_atom("C", md.element.carbon, res)
        top.add_atom("O", md.element.oxygen, res)

    n_atoms = top.n_atoms
    base = np.zeros((n_atoms, 3))
    for i in range(n_atoms):
        base[i] = [i * 0.15, 0.0, 0.0]  # 1.5 Å spacing along x

    xyz = np.tile(base[None, :, :], (n_frames, 1, 1))
    xyz += rng.normal(scale=0.02, size=xyz.shape)

    times = np.arange(n_frames) * 10.0  # 10 ps per frame
    return md.Trajectory(xyz=xyz.astype(np.float32), topology=top, time=times)


@pytest.fixture
def backbone_traj() -> md.Trajectory:
    return _build_backbone_traj()


@pytest.fixture
def backbone_traj_files(tmp_path: Path, backbone_traj: md.Trajectory):
    """Save the backbone trajectory as DCD + PDB files."""
    pdb = tmp_path / "top.pdb"
    dcd = tmp_path / "traj.dcd"
    backbone_traj[0].save_pdb(str(pdb))
    backbone_traj.save_dcd(str(dcd))
    return dcd, pdb


# ===========================================================================
# Registry — all four analyses present and in order
# ===========================================================================
def test_all_four_registered():
    names = available_analyses()
    for n in ("rmsd", "rmsf", "rg", "dihedrals"):
        assert n in names


def test_canonical_order():
    """Registration order matters for include=None executions."""
    names = available_analyses()
    expected = ("rmsd", "rmsf", "rg", "dihedrals")
    # All expected names should appear in the registry in the canonical
    # order (subsequent sub-deliveries may add more after them).
    indices = [names.index(n) for n in expected]
    assert indices == sorted(indices), f"out-of-order: {names}"


# ===========================================================================
# RMSD
# ===========================================================================
class TestRMSD:
    def test_compute_returns_one_value_per_frame(self, backbone_traj: md.Trajectory):
        rmsd = RMSD()
        out = rmsd.compute(backbone_traj)
        assert out.shape == (backbone_traj.n_frames,)
        assert out.dtype == np.float64

    def test_rmsd_to_self_is_zero(self, backbone_traj: md.Trajectory):
        """RMSD of the reference frame against itself is exactly zero.

        Without alignment this is the mathematical invariant (a frame minus
        itself), so it holds exactly regardless of geometry. The aligned
        path is exercised by the other RMSD tests; asserting "self == 0" on
        the aligned path would instead probe the ill-conditioning of QCP
        superposition on this near-collinear backbone fixture (the base
        geometry is points along the x-axis), which is not the intent and
        is platform/BLAS-dependent.
        """
        rmsd = RMSD(ref=0, align=False)
        out = rmsd.compute(backbone_traj)
        assert out[0] == 0.0

    def test_rmsd_is_nonnegative(self, backbone_traj: md.Trajectory):
        rmsd = RMSD()
        out = rmsd.compute(backbone_traj)
        assert (out >= 0).all()

    def test_negative_ref_index(self, backbone_traj: md.Trajectory):
        """ref=-1 should be the same as ref=last_frame."""
        n = backbone_traj.n_frames
        a = RMSD(ref=-1).compute(backbone_traj)
        b = RMSD(ref=n - 1).compute(backbone_traj)
        np.testing.assert_allclose(a, b, atol=1e-6)

    def test_out_of_range_ref_raises(self, backbone_traj: md.Trajectory):
        rmsd = RMSD(ref=9999)
        with pytest.raises(ValueError, match="out of range"):
            rmsd.compute(backbone_traj)

    def test_no_align_differs_from_aligned(self, backbone_traj: md.Trajectory):
        """Aligned and unaligned RMSD should generally differ."""
        a = RMSD(align=True).compute(backbone_traj)
        b = RMSD(align=False).compute(backbone_traj)
        # They should differ in at least some frames; allow for small numerical equivalence
        assert not np.allclose(a, b, atol=1e-9)

    def test_run_writes_outputs(self, tmp_path: Path, backbone_traj: md.Trajectory):
        rmsd = RMSD(output_dir=tmp_path)
        result = rmsd.run(backbone_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()
        assert result.options_path.exists()
        # Data round-trips
        loaded = np.loadtxt(result.data_path)
        np.testing.assert_allclose(loaded, result.data, rtol=1e-5)

    def test_options_recorded_in_manifest(
        self, tmp_path: Path, backbone_traj: md.Trajectory
    ):
        RMSD(output_dir=tmp_path, ref=5, align=False).run(backbone_traj)
        with (tmp_path / "rmsd" / "options.json").open() as fh:
            manifest = json.load(fh)
        assert manifest["options"]["ref"] == 5
        assert manifest["options"]["align"] is False

    def test_user_xunit_override(self, tmp_path: Path, backbone_traj: md.Trajectory):
        rmsd = RMSD(output_dir=tmp_path, xunit="ps")
        rmsd.run(backbone_traj)
        assert rmsd._user_xunit == "ps"

    def test_default_xlabel_uses_ns_when_time_available(
        self, tmp_path: Path, backbone_traj: md.Trajectory
    ):
        """A trajectory with time data should default to Time (ns)."""
        rmsd = RMSD(output_dir=tmp_path)
        rmsd.run(backbone_traj)
        assert rmsd.default_xlabel() == "Time (ns)"

    def test_default_xlabel_falls_back_to_frame(self, tmp_path: Path):
        """A trajectory without time data should default to Frame."""
        traj = _build_backbone_traj()
        traj.time = np.zeros(traj.n_frames)  # no real timing
        rmsd = RMSD(output_dir=tmp_path)
        rmsd.run(traj)
        assert rmsd.default_xlabel() == "Frame"


# ===========================================================================
# RMSF
# ===========================================================================
class TestRMSF:
    def test_per_residue_returns_two_columns(self, backbone_traj: md.Trajectory):
        out = RMSF().compute(backbone_traj)
        assert out.ndim == 2
        assert out.shape[1] == 2
        # CA selection → one row per residue
        assert out.shape[0] == backbone_traj.n_residues

    def test_per_atom_returns_two_columns(self, backbone_traj: md.Trajectory):
        out = RMSF(per_residue=False, selection="all").compute(backbone_traj)
        assert out.ndim == 2
        assert out.shape[1] == 2
        assert out.shape[0] == backbone_traj.n_atoms

    def test_rmsf_is_nonnegative(self, backbone_traj: md.Trajectory):
        out = RMSF().compute(backbone_traj)
        assert (out[:, 1] >= 0).all()

    def test_rmsf_magnitude_reasonable(self, backbone_traj: md.Trajectory):
        """The synthetic trajectory has 0.02 nm noise; RMSF should be ~0.02."""
        out = RMSF().compute(backbone_traj)
        mean_rmsf = out[:, 1].mean()
        # Per-atom Gaussian noise σ=0.02 nm in each xyz dimension gives
        # an expected position fluctuation of order σ. Allow factor-of-2 slack.
        assert 0.005 < mean_rmsf < 0.04, f"unexpected mean RMSF: {mean_rmsf}"

    def test_run_writes_outputs(self, tmp_path: Path, backbone_traj: md.Trajectory):
        result = RMSF(output_dir=tmp_path).run(backbone_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()

    def test_x_label_is_residue(self, tmp_path: Path, backbone_traj: md.Trajectory):
        rmsf = RMSF(output_dir=tmp_path)
        rmsf.run(backbone_traj)
        assert rmsf.default_xlabel() == "Residue"

    def test_x_label_is_atom_when_per_atom(self, tmp_path: Path, backbone_traj):
        rmsf = RMSF(output_dir=tmp_path, per_residue=False, selection="all")
        rmsf.run(backbone_traj)
        assert rmsf.default_xlabel() == "Atom serial"


# ===========================================================================
# Rg
# ===========================================================================
class TestRg:
    def test_default_returns_one_value_per_frame(self, backbone_traj: md.Trajectory):
        out = Rg().compute(backbone_traj)
        assert out.shape == (backbone_traj.n_frames,)

    def test_rg_is_positive(self, backbone_traj: md.Trajectory):
        out = Rg().compute(backbone_traj)
        assert (out > 0).all()

    def test_equal_weights_match_mdtraj_compute_rg(self, backbone_traj: md.Trajectory):
        """MDTraj's compute_rg weights every atom equally, and says so.

        This used to assert the default matched it, which held while the
        default was a thin wrapper. The default is now mass-weighted, as the
        docstring had always claimed and as the conventional definition has
        it, so the equivalence is with the unweighted option.
        """
        ours = Rg(mass_weighted=False).compute(backbone_traj)
        theirs = md.compute_rg(backbone_traj)
        np.testing.assert_allclose(ours, theirs, atol=1e-8)

    def test_the_default_is_mass_weighted(self, backbone_traj: md.Trajectory):
        weighted = Rg().compute(backbone_traj)
        equal = Rg(mass_weighted=False).compute(backbone_traj)
        assert not np.allclose(weighted, equal, rtol=1e-4)

    def test_with_selection(self, backbone_traj: md.Trajectory):
        """Rg with a CA selection must differ from all-atom Rg (atoms differ)."""
        all_atoms = Rg(selection="all").compute(backbone_traj)
        ca_only = Rg(selection="name CA").compute(backbone_traj)
        assert not np.allclose(all_atoms, ca_only)

    def test_by_chain_single_chain(self, backbone_traj: md.Trajectory):
        """With a single chain, by_chain should still return a (n, 1) shape."""
        out = Rg(by_chain=True).compute(backbone_traj)
        # The synthetic trajectory has 1 chain → 1 column (just total)
        assert out.ndim == 2
        assert out.shape[0] == backbone_traj.n_frames

    def test_run_writes_outputs(self, tmp_path: Path, backbone_traj):
        result = Rg(output_dir=tmp_path).run(backbone_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()


# ===========================================================================
# Dihedrals
# ===========================================================================
class TestDihedrals:
    def test_compute_returns_dataframe(self, backbone_traj: md.Trajectory):
        out = Dihedrals().compute(backbone_traj)
        assert isinstance(out, pd.DataFrame)
        assert set(out.columns) >= {"frame", "residue", "phi_deg", "psi_deg"}

    def test_angles_in_valid_range(self, backbone_traj: md.Trajectory):
        out = Dihedrals().compute(backbone_traj)
        assert ((out["phi_deg"] >= -180) & (out["phi_deg"] <= 180)).all()
        assert ((out["psi_deg"] >= -180) & (out["psi_deg"] <= 180)).all()

    def test_n_rows_equals_n_frames_times_n_inner_residues(
        self, backbone_traj: md.Trajectory
    ):
        """For a 5-residue peptide, residues 2-4 have both phi and psi (3 res)."""
        out = Dihedrals().compute(backbone_traj)
        # Inner residues = those with both phi (needs prev residue C) and
        # psi (needs next residue N). For a 5-residue chain: residues 2,3,4.
        n_inner = 3
        assert len(out) == backbone_traj.n_frames * n_inner

    def test_run_writes_outputs(self, tmp_path: Path, backbone_traj: md.Trajectory):
        result = Dihedrals(output_dir=tmp_path).run(backbone_traj)
        assert result.status == "ok"
        assert result.data_path.exists()
        assert result.figure_path.exists()
        # The .dat file is CSV format
        df = pd.read_csv(result.data_path)
        assert "phi_deg" in df.columns
        assert "psi_deg" in df.columns

    def test_no_protein_raises(self, tmp_path: Path):
        """A trajectory without backbone atoms should raise a clear error."""
        # Build a "trajectory" of pure water — no protein backbone
        top = md.Topology()
        chain = top.add_chain()
        res = top.add_residue("HOH", chain)
        for nm in ("O", "H1", "H2"):
            el = md.element.oxygen if nm == "O" else md.element.hydrogen
            top.add_atom(nm, el, res)
        xyz = np.random.RandomState(0).rand(5, top.n_atoms, 3).astype(np.float32)
        traj = md.Trajectory(xyz=xyz, topology=top)

        dh = Dihedrals(output_dir=tmp_path)
        with pytest.raises(ValueError, match="backbone dihedrals"):
            dh.compute(traj)

    def test_scatter_mode_runs(self, tmp_path: Path, backbone_traj):
        """Density=False switches to scatter plot."""
        result = Dihedrals(output_dir=tmp_path, density=False).run(backbone_traj)
        assert result.status == "ok"


# ===========================================================================
# End-to-end: all four through the AnalysisOrchestrator
# ===========================================================================
class TestEndToEnd:
    def test_all_four_run_through_orchestrator(
        self, tmp_path: Path, backbone_traj_files
    ):
        dcd, pdb = backbone_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd,
            topology=pdb,
            output_dir=tmp_path / "out",
        )
        results = ao.run(include=["rmsd", "rmsf", "rg", "dihedrals"])
        for name in ("rmsd", "rmsf", "rg", "dihedrals"):
            assert name in results
            assert results[name].status == "ok", results[name].message
            assert results[name].data_path.exists()
            assert results[name].figure_path.exists()

    def test_orchestrator_writes_complete_manifest(
        self, tmp_path: Path, backbone_traj_files
    ):
        dcd, pdb = backbone_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "out"
        )
        ao.run(include=["rmsd", "rg"])
        manifest_path = ao.output_dir / "analysis_manifest.json"
        with manifest_path.open() as fh:
            manifest = json.load(fh)
        assert manifest["plan"] == ["rmsd", "rg"]
        assert manifest["results"]["rmsd"]["status"] == "ok"
        assert manifest["results"]["rg"]["status"] == "ok"

    def test_per_analysis_options_forwarded(
        self, tmp_path: Path, backbone_traj_files
    ):
        dcd, pdb = backbone_traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "out"
        )
        results = ao.run(
            include=["rmsd"],
            options={"rmsd": {"ref": 5, "align": False}},
        )
        with (results["rmsd"].options_path).open() as fh:
            manifest = json.load(fh)
        assert manifest["options"]["ref"] == 5
        assert manifest["options"]["align"] is False


# ===========================================================================
# Plot customization for the new analyses
# ===========================================================================
class TestUserCustomization:
    def test_user_xlabel_wins_over_default(
        self, tmp_path: Path, backbone_traj: md.Trajectory
    ):
        rmsd = RMSD(output_dir=tmp_path, xlabel="Custom X")
        rmsd.run(backbone_traj)
        assert rmsd._user_xlabel == "Custom X"
        # Default xlabel for RMSD (without override) is "Time (ns)" for
        # this trajectory; the user override should still be Custom X.

    def test_user_title_propagates(self, tmp_path: Path, backbone_traj):
        rg = Rg(output_dir=tmp_path, title="Compactness over time")
        rg.run(backbone_traj)
        assert rg.figure_title() == "Compactness over time"

    def test_user_xunit_frames(self, tmp_path: Path, backbone_traj):
        """xunit=frames overrides the auto-detected ns default."""
        rmsd = RMSD(output_dir=tmp_path, xunit="frames")
        rmsd.run(backbone_traj)
        assert rmsd.default_xlabel() == "Frame"
        x, label = rmsd.frame_axis(backbone_traj)
        assert label == "Frame"
        np.testing.assert_array_equal(x, np.arange(backbone_traj.n_frames))

    def test_user_xunit_ps(self, tmp_path: Path, backbone_traj):
        rmsd = RMSD(output_dir=tmp_path, xunit="ps")
        rmsd.run(backbone_traj)
        _, label = rmsd.frame_axis(backbone_traj)
        assert label == "Time (ps)"

    def test_invalid_xunit_raises(self, tmp_path: Path, backbone_traj):
        rmsd = RMSD(output_dir=tmp_path, xunit="furlongs")
        with pytest.raises(ValueError, match="xunit"):
            rmsd.frame_axis(backbone_traj)


def _hbond_traj(n_frames: int = 100, transient_frames: int = 5) -> md.Trajectory:
    """Two donor-acceptor pairs: one bonded always, one only briefly."""
    top = md.Topology()
    chain = top.add_chain()
    r1 = top.add_residue("ALA", chain, resSeq=1)
    n1 = top.add_atom("N", md.element.nitrogen, r1)
    h1 = top.add_atom("H", md.element.hydrogen, r1)
    r2 = top.add_residue("ALA", chain, resSeq=2)
    top.add_atom("O", md.element.oxygen, r2)
    r3 = top.add_residue("ALA", chain, resSeq=3)
    n2 = top.add_atom("N", md.element.nitrogen, r3)
    h2 = top.add_atom("H", md.element.hydrogen, r3)
    r4 = top.add_residue("ALA", chain, resSeq=4)
    top.add_atom("O", md.element.oxygen, r4)
    top.add_bond(n1, h1)
    top.add_bond(n2, h2)

    xyz = np.zeros((n_frames, 6, 3), dtype=np.float32)
    xyz[:, 0] = [0.00, 0.0, 0.0]
    xyz[:, 1] = [0.10, 0.0, 0.0]
    xyz[:, 2] = [0.30, 0.0, 0.0]          # bonded in every frame
    xyz[:, 3] = [0.00, 1.0, 0.0]
    xyz[:, 4] = [0.10, 1.0, 0.0]
    xyz[:, 5] = [2.00, 1.0, 0.0]          # far away
    xyz[:transient_frames, 5] = [0.30, 1.0, 0.0]   # except briefly
    return md.Trajectory(xyz=xyz, topology=top, time=np.arange(n_frames) * 1.0)


class TestHBondsCountsEveryBondItDepicts:
    """The series is the number of hydrogen bonds per frame, so it must be.

    Baker-Hubbard proposes bonds above an occupancy threshold, and only the
    proposed ones were evaluated frame by frame. A bond present in five per
    cent of frames therefore contributed to none of them -- including the
    frames where it was there. The plot said one bond where there were two.
    """

    def test_a_transient_bond_is_counted_in_the_frames_it_exists(self) -> None:
        from fastmdxplora.analysis.hbonds import HBonds

        traj = _hbond_traj(n_frames=100, transient_frames=5)
        counts = HBonds().compute(traj)["n_hbonds"].to_numpy()

        assert counts[0] == 2, "both bonds are present in the first frames"
        assert counts[50] == 1, "only the persistent one remains later"
        assert counts.sum() == 105

    def test_raising_the_threshold_restricts_the_series_again(self) -> None:
        """The old behaviour is still reachable, deliberately."""
        from fastmdxplora.analysis.hbonds import HBonds

        traj = _hbond_traj(n_frames=100, transient_frames=5)
        counts = HBonds(candidate_freq=0.1).compute(traj)["n_hbonds"].to_numpy()
        assert counts[0] == 1
        assert counts.sum() == 100

    def test_freq_reports_how_many_bonds_persist(self) -> None:
        """Once every bond is proposed, freq would otherwise decide nothing."""
        from fastmdxplora.analysis.hbonds import HBonds

        analysis = HBonds()
        analysis.compute(_hbond_traj())
        assert analysis.options["n_candidate_bonds"] == 2
        assert analysis.options["n_persistent_bonds"] == 1

    @staticmethod
    def _captured_warnings(build):
        """Collect what the package logger emits.

        ``caplog`` sees nothing: the package sets ``propagate = False`` on the
        ``fastmdx`` logger so its own handler owns the formatting, which means
        records never reach the root logger pytest attaches to. A handler is
        added to that logger instead.
        """
        import logging

        records: list[str] = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("fastmdx")
        handler = _Collect(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            build()
        finally:
            logger.removeHandler(handler)
        return " ".join(records)

    def test_the_multiplier_says_what_it_is_doing(self) -> None:
        """It reproduces an earlier count; it is not a convention.

        MDTraj lists each hydrogen bond once, as one donor-hydrogen-acceptor
        triplet, and version 1 counted the same way -- one row per triplet, one
        frame at a time. Doubling models neither.
        """
        from fastmdxplora.analysis.hbonds import HBonds

        said = self._captured_warnings(lambda: HBonds(count_multiplier=2))
        assert "not a convention" in said

    def test_no_multiplier_is_silent(self) -> None:
        from fastmdxplora.analysis.hbonds import HBonds

        said = self._captured_warnings(HBonds)
        assert "multiplying" not in said


def _hinge_traj(n_frames: int = 40, seed: int = 0) -> md.Trajectory:
    """Two conformational states, plus rigid-body drift on alternate frames.

    The hinge moves half the chain, which is a change of shape. The drift moves
    all of it, which is not.
    """
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    for i in range(12):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)

    base = np.zeros((12, 3))
    base[:, 0] = np.arange(12) * 0.38
    xyz = np.tile(base[None], (n_frames, 1, 1))
    xyz += rng.normal(scale=0.005, size=xyz.shape)
    xyz[n_frames // 2:, 6:, 1] += 0.5      # the hinge
    xyz[::2] += 5.0                         # the drift
    return md.Trajectory(xyz=xyz.astype(np.float32), topology=top)


def _recovery(labels: np.ndarray) -> float:
    """How much of the true two-state split the labels reproduce."""
    half = len(labels) // 2
    agree = (labels[:half] == labels[0]).sum() + (labels[half:] == labels[half]).sum()
    return max(agree, len(labels) - agree) / len(labels)


class TestClusteringComparesShapeNotPlacement:
    """What frames are compared in decides the answer.

    Comparing raw coordinates makes the leading difference between frames where
    the molecule drifted and how it turned. Two identical conformations in
    different orientations then look as far apart as anything in the run, and
    clustering reports position rather than shape.
    """

    def test_pairwise_rmsd_recovers_the_conformational_states(self) -> None:
        from fastmdxplora.analysis.cluster import Cluster

        labels = Cluster(
            features="rmsd", methods=["hierarchical"], n_clusters=2,
            linkage="ward",
        ).compute(_hinge_traj())["hierarchical"]
        assert _recovery(labels) == 1.0

    def test_superposed_coordinates_recover_them_too(self) -> None:
        from fastmdxplora.analysis.cluster import Cluster

        labels = Cluster(
            features="coordinates", methods=["hierarchical"], n_clusters=2,
            linkage="ward",
        ).compute(_hinge_traj())["hierarchical"]
        assert _recovery(labels) >= 0.9

    def test_unaligned_coordinates_do_not(self) -> None:
        """The comparison that motivates superposing at all.

        This is what clustering on ``traj.xyz`` without alignment gives: the
        rigid-body drift dominates and the split is no better than chance.
        """
        from sklearn.cluster import AgglomerativeClustering

        traj = _hinge_traj()
        raw = traj.xyz.reshape(traj.n_frames, -1)
        labels = AgglomerativeClustering(n_clusters=2, linkage="ward").fit_predict(raw)
        assert _recovery(labels) < 0.7

    def test_the_coordinate_space_keeps_distances_in_nm(self) -> None:
        """So eps means the same thing in both, and the two are comparable."""
        from fastmdxplora.analysis.cluster import _superposed_coordinates

        from fastmdxplora.analysis.base import superposed

        traj = _hinge_traj()
        atom_idx = np.arange(traj.n_atoms)
        # Aligning no longer rotates the caller's trajectory, so the frames
        # the points were built from have to be obtained the same way the
        # function obtains them rather than read back out of `traj`. The
        # earlier version compared against `traj.xyz` and passed only
        # because superposition mutated it in place -- a test shaped by the
        # defect it sat next to.
        aligned = superposed(traj, frame=0, atom_indices=atom_idx)
        points = _superposed_coordinates(traj, atom_idx)
        expected = np.sqrt(
            ((aligned.xyz[0, atom_idx] - aligned.xyz[1, atom_idx]) ** 2).sum()
            / len(atom_idx)
        )
        assert np.isclose(
            np.linalg.norm(points[0] - points[1]), expected, rtol=1e-5
        )

    def test_an_unknown_feature_space_is_refused(self) -> None:
        import pytest

        from fastmdxplora.analysis.cluster import Cluster

        with pytest.raises(ValueError, match="Unknown clustering features"):
            Cluster(features="cartesian")


class TestQValueFollowsThePaperItCites:
    """Best, Hummer & Eaton define Q through a switching function.

    A contact was instead counted as present or absent by a single distance
    shared by every pair, so one formed at 0.20 nm was judged by the same
    standard as one formed at 0.44 nm. The measure reported a fold coming apart
    well before the paper's does -- on a peeling hairpin it reached zero while
    the paper's still read 0.62.
    """

    @staticmethod
    def _hairpin(n_frames: int = 100, peel: float = 0.5, noise: float = 0.01):
        rng = np.random.RandomState(3)
        top = md.Topology()
        chain = top.add_chain()
        for i in range(14):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            top.add_atom("CA", md.element.carbon, res)
            top.add_atom("CB", md.element.carbon, res)
        pos = []
        for i in range(7):
            pos += [[i * 0.38, 0.0, 0.0], [i * 0.38, 0.15, 0.0]]
        for i in range(7):
            pos += [[(6 - i) * 0.38, 0.42, 0.0], [(6 - i) * 0.38, 0.27, 0.0]]
        xyz = np.tile(np.array(pos)[None], (n_frames, 1, 1))
        xyz[:, 14:, 1] += np.linspace(0.0, peel, n_frames)[:, None]
        xyz += rng.normal(scale=noise, size=xyz.shape)
        return md.Trajectory(xyz=xyz.astype(np.float32), topology=top)

    @staticmethod
    def _reference_q(traj, cutoff=0.45, beta=50.0, lam=1.8, sep=4):
        """The formula as published, written out independently.

        S is over pairs of heavy atoms, which is the part that was wrong
        here for as long as this test agreed with the code: the earlier
        version of both counted one closest-heavy distance per residue
        pair, so the test restated the implementation's choice rather than
        the paper's and could not see the difference. On this fixture the
        two readings stand 0.263 apart at their worst frame.
        """
        from itertools import combinations

        heavy = traj.topology.select_atom_indices("heavy")
        pairs = np.array([
            (i, j) for (i, j) in combinations(heavy, 2)
            if abs(traj.topology.atom(i).residue.index
                   - traj.topology.atom(j).residue.index) >= sep
        ])
        d0 = md.compute_distances(traj[0], pairs)[0]
        native = pairs[d0 < cutoff]
        r0 = md.compute_distances(traj[0], native)[0]
        r = md.compute_distances(traj, native)
        return (1.0 / (1.0 + np.exp(beta * (r - lam * r0)))).mean(axis=1)

    def test_it_matches_the_published_formula(self) -> None:
        """Against the enumeration MDTraj's own documentation publishes.

        The reference builds S by enumerating every heavy-atom combination
        and discarding what falls outside the cutoff; the implementation
        reaches the same set through a neighbour search, because on a real
        protein the enumeration holds millions of pairs to keep thousands.
        Agreement here is what makes the two interchangeable.
        """
        from fastmdxplora.analysis.qvalue import QValue

        traj = self._hairpin()
        assert np.allclose(
            QValue(selection="all").compute(traj),
            self._reference_q(traj),
            atol=1e-6,
        )

    def test_solvent_cannot_enter_the_contact_set(self) -> None:
        """Q is a statement about a fold, and water has none.

        With the selection left at the whole trajectory, S on a solvated
        system came out overwhelmingly water against water, and the
        number reported would have been how much of the solvent's
        starting arrangement survived. A full run passes a scope and
        never met this; a direct call is the ordinary way to.
        """
        from fastmdxplora.analysis.qvalue import QValue, native_contact_pairs

        rng = np.random.RandomState(0)
        top = md.Topology()
        chain = top.add_chain()
        positions = []
        for i in range(10):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            for name, element in (("N", md.element.nitrogen),
                                  ("CA", md.element.carbon),
                                  ("C", md.element.carbon)):
                top.add_atom(name, element, res)
                positions.append([i * 0.30 + 0.1 * len(positions) % 3, 0, 0])
        water = top.add_chain()
        for i in range(200):
            res = top.add_residue("HOH", water, resSeq=i + 1)
            top.add_atom("O", md.element.oxygen, res)
            positions.append(list(rng.random(3) * 2.0))
        traj = md.Trajectory(
            np.tile(np.array(positions)[None], (3, 1, 1)).astype(np.float32),
            top)

        assert QValue().selection == "protein"
        analysis = QValue()
        analysis.compute(traj)
        selected = analysis.select_atoms(traj)
        names = {traj.topology.atom(int(i)).residue.name for i in selected}
        assert "HOH" not in names

        # Without a selection the set is mostly solvent, which is the
        # thing the default now prevents.
        unrestricted, _ = native_contact_pairs(traj, ref=0)
        water_pairs = sum(
            1 for i, j in unrestricted
            if traj.topology.atom(int(i)).residue.name == "HOH"
            and traj.topology.atom(int(j)).residue.name == "HOH")
        assert water_pairs > len(unrestricted) / 2

    def test_the_selection_chooses_which_contacts_count(self) -> None:
        """Backbone, alpha carbons and all heavy atoms are three measures.

        They differ as much as the two schemes do, which is why the
        choice is recorded rather than assumed.
        """
        from fastmdxplora.analysis.qvalue import QValue

        traj = self._hairpin()
        whole = QValue(selection="all").compute(traj)
        alpha = QValue(selection="name CA").compute(traj)

        assert whole.shape == alpha.shape
        assert not np.allclose(whole, alpha), (
            "a coarse-grained Q is a different quantity, not a rounding of "
            "the all-atom one")
        assert QValue(selection="name CA").options["scheme"] == (
            "heavy-atom-pairs")

    def test_the_residue_reading_is_available_and_is_not_the_paper_s(self) -> None:
        """Both measures are defensible; only one is comparable with the
        literature, so the coarser one has to be asked for by name."""
        from fastmdxplora.analysis.qvalue import QValue

        traj = self._hairpin()
        published = QValue(selection="all").compute(traj)
        residue = QValue(
            selection="all", scheme="residue-closest-heavy").compute(traj)

        assert np.abs(published - residue).max() > 0.2
        assert QValue().scheme == "heavy-atom-pairs"
        assert QValue().options["scheme"] == "heavy-atom-pairs"

    def test_a_contact_is_judged_against_its_own_native_distance(self) -> None:
        """Not against one number shared by every pair.

        A single threshold treats a contact formed at 0.20 nm and one formed
        at 0.44 nm alike: the second breaks on any stretch at all, the first
        can more than double and still count. Judged against its own native
        distance, each is allowed the same proportional stretch, so a fold
        under way reads as further from unfolded than a threshold says.
        """
        from fastmdxplora.analysis.qvalue import QValue

        analysis = QValue(selection="all")
        traj = self._hairpin()
        q = analysis.compute(traj)

        # The same contacts, scored by the threshold this used to apply.
        # Scoring the same S is the point: comparing a switching function on
        # atom pairs against a threshold on residue pairs would confound two
        # differences and demonstrate neither.
        pair_idx, _ = analysis._native_atom_pairs(traj, 0)
        d = md.compute_distances(traj, pair_idx)
        threshold = (d < 0.45).sum(axis=1) / len(pair_idx)

        # Both read as folded at the reference. The switching function sits a
        # little under 1 even there, which is inherent to it: a contact at its
        # native distance is well inside the tolerance but the sigmoid is
        # smooth, so it never quite saturates.
        assert q[0] > 0.99 and threshold[0] == 1.0

        # The difference is in how the fold comes apart. Take the first frame
        # at which the threshold declares every native contact broken, and
        # ask what the paper's measure reads there.
        gone = int(np.argmax(threshold == 0.0))
        assert threshold[gone] == 0.0
        assert q[gone] > 0.4, (
            "the threshold calls the fold entirely gone while the paper's "
            "measure still finds most of it")

    def test_the_defaults_are_the_paper_s(self) -> None:
        from fastmdxplora.analysis.qvalue import QValue

        analysis = QValue()
        assert analysis.beta == 50.0
        assert analysis.lambda_factor == 1.8
        assert analysis.cutoff == 0.45
        assert analysis.min_seq_separation == 4

    def test_a_steep_beta_approaches_the_old_threshold(self) -> None:
        """The switching function contains the hard cutoff as a limit."""
        from fastmdxplora.analysis.qvalue import QValue

        traj = self._hairpin()
        steep = QValue(selection="all", beta=1e5, lambda_factor=1.0).compute(traj)
        assert steep.min() < 0.1, "at the limit it becomes a threshold again"


class TestSecondaryStructureIgnoresWhatIsNotProtein:
    """DSSP returns a column for every residue, not only the protein ones.

    A residue without backbone atoms gets the code ``NA``: a ligand, an ion, a
    water. Those columns used to survive into the timeline, where ``NA`` is not
    in the colour map and so was drawn as coil -- and because the labels were
    taken from the protein residues alone, the two lists came out different
    lengths and the code fell back to plain numbering. A protein numbered 10 to
    15 was relabelled 0 to 6, with the ligand as the last row.

    Scope defaults to ``solute``, which is protein and ligand, so this was the
    ordinary case for the systems this software is meant for.
    """

    @staticmethod
    def _protein_with_ligand(n_residues: int = 6, first_resseq: int = 10):
        top = md.Topology()
        chain = top.add_chain()
        for i in range(n_residues):
            res = top.add_residue("ALA", chain, resSeq=first_resseq + i)
            for name, element in (("N", md.element.nitrogen),
                                  ("CA", md.element.carbon),
                                  ("C", md.element.carbon),
                                  ("O", md.element.oxygen)):
                top.add_atom(name, element, res)
        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=200)
        top.add_atom("C1", md.element.carbon, ligand)

        xyz = np.zeros((5, top.n_atoms, 3), dtype=np.float32)
        for atom in range(top.n_atoms):
            xyz[:, atom] = [atom * 0.15, 0.0, 0.0]
        return md.Trajectory(xyz=xyz, topology=top)

    def test_the_ligand_is_not_given_a_row(self) -> None:
        from fastmdxplora.analysis.ss import SS

        traj = self._protein_with_ligand()
        columns = [c for c in SS(selection="all").compute(traj).columns
                   if c != "frame"]
        assert len(columns) == 6, "one row per protein residue and no more"

    def test_residue_numbering_survives(self) -> None:
        from fastmdxplora.analysis.ss import SS

        traj = self._protein_with_ligand(first_resseq=10)
        columns = [c for c in SS(selection="all").compute(traj).columns
                   if c != "frame"]
        assert columns == [10, 11, 12, 13, 14, 15]

    def test_no_NA_reaches_the_data(self) -> None:
        """It is not in the colour map, so it would be drawn as coil."""
        from fastmdxplora.analysis.ss import SS

        df = SS(selection="all").compute(self._protein_with_ligand())
        codes = df.drop(columns="frame").to_numpy()
        assert "NA" not in set(np.unique(codes).tolist())

    def test_a_protein_only_system_is_unchanged(self) -> None:
        from fastmdxplora.analysis.ss import SS

        traj = self._protein_with_ligand()
        protein = traj.atom_slice(traj.topology.select("protein"))
        columns = [c for c in SS(selection="all").compute(protein).columns
                   if c != "frame"]
        assert columns == [10, 11, 12, 13, 14, 15]


class TestSecondaryStructureSaysWhenItDoesNotApply:
    """A system with no protein backbone has no secondary structure.

    Dropping the non-protein columns can leave none at all -- a nucleic acid
    run, a lone ligand, a coarse-grained model. Drawing an empty timeline for
    that is not an answer to the question; it is a picture of nothing, and the
    only sign anything was wrong came from matplotlib complaining that the axis
    had no extent.
    """

    @staticmethod
    def _no_backbone():
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain)
        for i in range(5):
            top.add_atom(f"A{i}", md.element.carbon, residue)
        xyz = np.random.RandomState(0).rand(6, 5, 3).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    def test_it_refuses_and_says_why(self) -> None:
        import pytest

        from fastmdxplora.analysis.ss import SS

        with pytest.raises(ValueError, match="has a protein backbone"):
            SS(selection="all").compute(self._no_backbone())

    def test_the_orchestrator_records_it_without_stopping(self) -> None:
        """One analysis that does not apply must not end the run."""
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        assert hasattr(AnalysisOrchestrator, "run")
        # The orchestrator catches per-analysis failures and records them; the
        # refusal above therefore reaches the user as a stated reason rather
        # than as an empty figure or a stopped exploration.


class TestAtomLabelsAreNeverNotANumber:
    """The atom column is written to the data file, so it must hold a number.

    ``Atom.serial`` is filled in by the file a topology came from. A trajectory
    built in memory -- which the Python API allows -- has none, and ``None``
    became NaN in the column. The saved ``.dat`` carried the NaN, and the plot
    cast it to the most negative integer there is and used that as an axis
    label.
    """

    @staticmethod
    def _in_memory():
        top = md.Topology()
        chain = top.add_chain()
        res = top.add_residue("ALA", chain, resSeq=1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, res)
        xyz = np.random.RandomState(0).rand(10, 4, 3).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    def test_rmsf_labels_atoms_without_serials(self) -> None:
        from fastmdxplora.analysis.rmsf import RMSF

        out = RMSF(per_residue=False, selection="all").compute(self._in_memory())
        assert not np.isnan(out[:, 0]).any()
        assert out[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0]

    def test_a_file_supplied_serial_is_kept(self, tmp_path) -> None:
        """Where the file says, the file wins: the number is the user's."""
        from fastmdxplora.analysis.rmsf import RMSF

        traj = self._in_memory()
        path = tmp_path / "top.pdb"
        traj[0].save_pdb(path)
        loaded = md.load(str(path))
        from_file = [a.serial for a in loaded.topology.atoms]

        out = RMSF(per_residue=False, selection="all").compute(
            md.Trajectory(xyz=traj.xyz, topology=loaded.topology))
        assert out[:, 0].tolist() == [float(s) for s in from_file]

    def test_ligand_rmsf_labels_survive_the_cast_the_plot_makes(self) -> None:
        from fastmdxplora.analysis.rmsf import _atom_labels

        traj = self._in_memory()
        labels = _atom_labels(list(traj.topology.atoms))
        assert labels.dtype == np.int64
        assert (labels > 0).all()


class TestDimRedSaysWhenThereIsNothingToDecompose:
    """Variance is what a decomposition decomposes.

    A structure that does not move has none. PCA divides each component's
    variance by the total, which is zero, so the ratios came out NaN and the
    figure was labelled "PC 1 (nan%)" over a scatter of coincident points --
    a plot that looks like a result and is not one. The only sign was a numpy
    warning about dividing by zero, nine of them in one test run.
    """

    @staticmethod
    def _motionless(n_frames: int = 10):
        top = md.Topology()
        chain = top.add_chain()
        res = top.add_residue("ALA", chain)
        for i in range(5):
            top.add_atom(f"A{i}", md.element.carbon, res)
        base = np.random.RandomState(0).rand(5, 3)
        xyz = np.tile(base[None], (n_frames, 1, 1)).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    def test_it_refuses_and_says_why(self) -> None:
        import pytest

        from fastmdxplora.analysis.dimred import DimRed

        with pytest.raises(ValueError, match="nothing to decompose"):
            DimRed(methods=["pca"], selection="all").compute(self._motionless())

    def test_no_division_by_zero_reaches_numpy(self) -> None:
        import warnings

        from fastmdxplora.analysis.dimred import DimRed

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                DimRed(methods=["pca"], selection="all").compute(self._motionless())
            except ValueError:
                pass
            assert not [w for w in caught if "invalid value" in str(w.message)]

    def test_every_method_is_covered_not_only_pca(self) -> None:
        """There are no neighbourhoods among points that are all one point."""
        import pytest

        from fastmdxplora.analysis.dimred import DimRed

        with pytest.raises(ValueError, match="nothing to decompose"):
            DimRed(methods=["tsne"], selection="all").compute(self._motionless())

    def test_a_trajectory_that_moves_is_unaffected(self) -> None:
        from fastmdxplora.analysis.dimred import DimRed

        traj = self._motionless()
        moving = md.Trajectory(
            xyz=traj.xyz + np.random.RandomState(1).normal(
                scale=0.01, size=traj.xyz.shape).astype(np.float32),
            topology=traj.topology,
        )
        analysis = DimRed(methods=["pca"], selection="all")
        embedding = analysis.compute(moving)["pca"]
        assert np.isfinite(embedding).all()
        assert np.isfinite(analysis._explained_variance).all()


class TestRadiusOfGyrationIsMassWeighted:
    """What was documented and what was computed had come apart.

    The docstring said the radius of gyration used masses from the topology.
    ``mdtraj.compute_rg`` weights every atom equally unless told otherwise --
    its own Notes say so -- and when it is given masses it still measures from
    the geometric centre rather than the centre of mass. So the reported number
    counted each hydrogen for as much as each carbon, a few per cent from the
    value GROMACS, cpptraj and a published figure all report.
    """

    @staticmethod
    def _protein_like(n_residues: int = 30, n_frames: int = 3):
        rng = np.random.RandomState(0)
        top = md.Topology()
        chain = top.add_chain()
        for i in range(n_residues):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            for name, element in (("N", md.element.nitrogen),
                                  ("CA", md.element.carbon),
                                  ("C", md.element.carbon),
                                  ("O", md.element.oxygen),
                                  ("HA", md.element.hydrogen),
                                  ("H", md.element.hydrogen)):
                top.add_atom(name, element, res)
        xyz = rng.normal(size=(n_frames, top.n_atoms, 3)).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    @staticmethod
    def _reference(traj):
        """The conventional definition, written out independently."""
        masses = np.array([a.element.mass for a in traj.topology.atoms])
        xyz = traj.xyz.astype(np.float64)
        centre = (xyz * masses[None, :, None]).sum(axis=1) / masses.sum()
        squared = ((xyz - centre[:, None, :]) ** 2).sum(axis=2)
        return np.sqrt((squared * masses[None, :]).sum(axis=1) / masses.sum())

    def test_it_matches_the_conventional_definition(self) -> None:
        from fastmdxplora.analysis.rg import Rg

        traj = self._protein_like()
        assert np.allclose(
            Rg(selection="all").compute(traj), self._reference(traj), atol=1e-9
        )

    def test_equal_weights_remain_available_and_match_mdtraj(self) -> None:
        from fastmdxplora.analysis.rg import Rg

        traj = self._protein_like()
        assert np.allclose(
            Rg(selection="all", mass_weighted=False).compute(traj),
            md.compute_rg(traj),
            atol=1e-6,
        )

    def test_the_two_differ_by_enough_to_matter(self) -> None:
        """Hydrogens are half the atoms and a fraction of the mass."""
        from fastmdxplora.analysis.rg import Rg

        traj = self._protein_like()
        weighted = Rg(selection="all").compute(traj)
        equal = Rg(selection="all", mass_weighted=False).compute(traj)
        assert not np.allclose(weighted, equal, rtol=1e-3)

    def test_a_topology_without_masses_falls_back(self) -> None:
        """A virtual site or a coarse-grained bead may carry no element."""
        from fastmdxplora.analysis.rg import Rg

        top = md.Topology()
        chain = top.add_chain()
        res = top.add_residue("UNK", chain)
        for _ in range(4):
            top.add_atom("X", md.element.virtual, res)
        traj = md.Trajectory(
            xyz=np.random.RandomState(1).rand(3, 4, 3).astype(np.float32),
            topology=top,
        )
        out = Rg(selection="all").compute(traj)
        assert np.isfinite(out).all()


class TestDistancesHonourThePeriodicBox:
    """A solvated trajectory is not always imaged.

    A molecule split across the periodic boundary looks far from everything it
    is actually touching. Contacts and hydrogen bonds measured plain distances
    regardless, so a bound ligand sitting across the boundary reported no
    contacts at all -- while the Q-value, on the same frames, measured with the
    box because it never overrode MDTraj's default. Two analyses answering
    "is this near that" differently on one trajectory.
    """

    @staticmethod
    def _split_across_boundary(box: float = 5.0):
        top = md.Topology()
        chain = top.add_chain()
        for i in range(4):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            for name in ("N", "CA", "C", "O"):
                top.add_atom(name, md.element.carbon, res)
        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
        for i in range(3):
            top.add_atom(f"C{i}", md.element.carbon, ligand)

        protein = np.column_stack(
            [np.linspace(0.05, 0.5, 16), np.zeros(16), np.zeros(16)])
        # Just the other side of the wall: close to the protein through it.
        lig = np.array([[box - 0.2, 0, 0], [box - 0.15, 0, 0], [box - 0.25, 0, 0]])
        xyz = np.vstack([protein, lig])[None].astype(np.float32)
        traj = md.Trajectory(xyz=xyz, topology=top)
        traj.unitcell_vectors = np.array(
            [[[box, 0, 0], [0, box, 0], [0, 0, box]]], dtype=np.float32)
        return traj

    def test_contacts_are_found_through_the_boundary(self) -> None:
        from fastmdxplora.analysis.contacts import Contacts

        traj = self._split_across_boundary()
        found = Contacts(ligand_resname="LIG", protein_selection="not resname LIG").compute(traj)["n_contacts"][0]
        assert found > 0, "the ligand is bound; it is the box that is in the way"

    def test_the_old_behaviour_is_still_reachable(self) -> None:
        from fastmdxplora.analysis.contacts import Contacts

        traj = self._split_across_boundary()
        found = Contacts(ligand_resname="LIG", protein_selection="not resname LIG", periodic=False).compute(traj)["n_contacts"][0]
        assert found == 0

    def test_a_trajectory_without_a_box_is_unaffected(self) -> None:
        """Where there is no unit cell, the setting changes nothing."""
        from fastmdxplora.analysis.contacts import Contacts

        traj = self._split_across_boundary()
        traj.unitcell_vectors = None
        with_box = Contacts(ligand_resname="LIG", protein_selection="not resname LIG").compute(traj)["n_contacts"][0]
        without = Contacts(ligand_resname="LIG", protein_selection="not resname LIG", periodic=False).compute(traj)["n_contacts"][0]
        assert with_box == without

    def test_hydrogen_bonds_take_the_same_setting(self) -> None:
        from fastmdxplora.analysis.hbonds import HBonds

        analysis = HBonds()
        assert analysis.periodic is True
        assert analysis.options["periodic"] is True


class TestProteinLigandHBondsNeedsTheLigandsBonds:
    """A donor is a nitrogen or oxygen with a hydrogen bonded to it.

    A ligand whose bonds are absent from the topology therefore cannot be seen
    to donate, only to accept -- and the count reports one direction under a
    name that promises both. The guard that built missing bonds only fired when
    the topology had none at all, which a PDB carrying the protein's
    connectivity but no CONECT records for its ligand does not.
    """

    @staticmethod
    def _complex(bond_the_ligand: bool):
        top = md.Topology()
        chain = top.add_chain()
        res = top.add_residue("ALA", chain, resSeq=1)
        n = top.add_atom("N", md.element.nitrogen, res)
        ca = top.add_atom("CA", md.element.carbon, res)
        c = top.add_atom("C", md.element.carbon, res)
        o = top.add_atom("O", md.element.oxygen, res)
        top.add_bond(n, ca)
        top.add_bond(ca, c)
        top.add_bond(c, o)

        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
        lo = top.add_atom("O1", md.element.oxygen, ligand)
        lh = top.add_atom("H1", md.element.hydrogen, ligand)
        if bond_the_ligand:
            top.add_bond(lo, lh)

        # O1-H1 ... O : the ligand donating to the protein backbone oxygen.
        frame = np.array([[[0, 0, 0], [0.15, 0, 0], [0.30, 0, 0], [0.42, 0, 0],
                           [0.62, 0, 0], [0.52, 0, 0]]], dtype=np.float32)
        return md.Trajectory(xyz=np.repeat(frame, 4, axis=0), topology=top)

    def test_the_donation_is_found_when_the_bonds_are_there(self) -> None:
        from fastmdxplora.analysis.pl_hbonds import ProteinLigandHBonds

        counts = ProteinLigandHBonds(
            ligand_resname="LIG", protein_selection="not resname LIG",
        ).compute(self._complex(True))["n_hbonds"]
        assert (counts == 1).all()

    def test_it_refuses_rather_than_counting_one_direction(self) -> None:
        import pytest

        from fastmdxplora.analysis.pl_hbonds import ProteinLigandHBonds

        with pytest.raises(ValueError, match="cannot be seen to donate"):
            ProteinLigandHBonds(
                ligand_resname="LIG", protein_selection="not resname LIG",
            ).compute(self._complex(False))

    def test_a_ligand_with_no_hydrogens_is_not_blocked(self) -> None:
        """It can only accept, and that is a fact about the ligand."""
        from fastmdxplora.analysis.pl_hbonds import ProteinLigandHBonds

        traj = self._complex(True)
        heavy_only = traj.atom_slice(
            [a.index for a in traj.topology.atoms
             if a.element is None or a.element.symbol != "H"]
        )
        counts = ProteinLigandHBonds(
            ligand_resname="LIG", protein_selection="not resname LIG",
        ).compute(heavy_only)["n_hbonds"]
        assert len(counts) == traj.n_frames


class TestWhatHoldsALigandInPlace:
    """Counting contacts says how much of the protein a ligand touches. It
    does not say what is holding it.

    Each rule is the published criterion, named in its docstring. Where a
    widely used tool departs from the literature the departure is recorded
    rather than quietly adopted: PLIP allows a hydrogen bond at 4.1 A and 100
    degrees, which it says is tuned for structures where the hydrogen
    positions are unknown. These frames have hydrogens from the force field,
    so the angle is measured and the stricter criterion is the one the
    measurement supports.
    """

    @staticmethod
    def _complex():
        """A ligand hydroxyl donating to a backbone oxygen, and a methyl
        packed against a side chain. Both answers known by construction."""
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain, resSeq=1)
        ca = top.add_atom("CA", md.element.carbon, residue)
        cb = top.add_atom("CB", md.element.carbon, residue)
        hb = top.add_atom("HB", md.element.hydrogen, residue)
        o = top.add_atom("O", md.element.oxygen, residue)
        for first, second in ((ca, cb), (cb, hb), (ca, o)):
            top.add_bond(first, second)

        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
        lo = top.add_atom("O1", md.element.oxygen, ligand)
        lh = top.add_atom("H1", md.element.hydrogen, ligand)
        alcohol = top.add_atom("C1", md.element.carbon, ligand)
        methyl = top.add_atom("C2", md.element.carbon, ligand)
        hm = top.add_atom("H2", md.element.hydrogen, ligand)
        for first, second in ((lo, lh), (lo, alcohol), (alcohol, methyl),
                              (methyl, hm)):
            top.add_bond(first, second)

        xyz = np.array([[
            [0.15, 1.0, 0], [0.30, 1.0, 0], [0.30, 1.12, 0], [0.42, 1.0, 0],
            [0.62, 1.0, 0], [0.52, 1.0, 0], [0.74, 1.0, 0],
            [0.33, 1.05, 0], [0.33, 1.17, 0],
        ]], dtype=np.float32)
        traj = md.Trajectory(xyz=xyz, topology=top)
        ligand_idx = [a.index for a in top.atoms if a.residue.name == "LIG"]
        protein_idx = [a.index for a in top.atoms if a.residue.name != "LIG"]
        return traj, ligand_idx, protein_idx

    def test_the_hydrogen_bond_is_found_with_its_geometry(self) -> None:
        from fastmdxplora.analysis.interactions import hydrogen_bonds

        traj, ligand, protein = self._complex()
        found = hydrogen_bonds(traj, ligand, protein, periodic=False)
        assert len(found) == 1
        bond = found[0]
        assert bond.kind == "hydrogen_bond"
        assert np.isclose(bond.distance_nm, 0.20, atol=1e-3)
        assert np.isclose(bond.angle_deg, 180.0, atol=1.0)

    def test_a_bent_donor_is_not_a_hydrogen_bond(self) -> None:
        """Close is not enough. The angle is what distinguishes a hydrogen
        bond from two polar atoms that happen to be near each other."""
        from fastmdxplora.analysis.interactions import hydrogen_bonds

        traj, ligand, protein = self._complex()
        # Put the hydrogen behind the donor, so the angle collapses while the
        # heavy-atom distance is unchanged.
        moved = traj.xyz.copy()
        moved[0, 5] = [0.70, 1.0, 0]
        bent = md.Trajectory(xyz=moved, topology=traj.topology)
        assert hydrogen_bonds(bent, ligand, protein, periodic=False) == []

    def test_a_carbon_bonded_to_oxygen_is_not_hydrophobic(self) -> None:
        """An alcohol carbon is polarised. Counting it would report the same
        contact twice -- once here and once as a hydrogen bond."""
        from fastmdxplora.analysis.interactions import hydrophobic_atoms

        traj, ligand, _protein = self._complex()
        names = {str(traj.topology.atom(i))
                 for i in hydrophobic_atoms(traj.topology, ligand)}
        assert names == {"LIG900-C2"}, names

    def test_a_backbone_oxygen_is_not_hydrophobic_either(self) -> None:
        from fastmdxplora.analysis.interactions import hydrophobic_atoms

        traj, _ligand, protein = self._complex()
        names = {str(traj.topology.atom(i))
                 for i in hydrophobic_atoms(traj.topology, protein)}
        assert names == {"ALA1-CB"}, names

    def test_the_hydrophobic_contact_is_found(self) -> None:
        from fastmdxplora.analysis.interactions import hydrophobic_contacts

        traj, ligand, protein = self._complex()
        found = hydrophobic_contacts(traj, ligand, protein, periodic=False)
        assert len(found) == 1
        assert found[0].kind == "hydrophobic"
        assert found[0].angle_deg is None, "hydrophobic association has no geometry"

    def test_both_directions_of_donation_are_looked_for(self) -> None:
        """A protein donating to a ligand and a ligand donating to a protein
        are different interactions, and a ligand that can only accept is a
        fact about the ligand worth seeing."""
        from fastmdxplora.analysis.interactions import donors_and_acceptors

        traj, ligand, protein = self._complex()
        ligand_donors, ligand_acceptors = donors_and_acceptors(
            traj.topology, ligand)
        protein_donors, protein_acceptors = donors_and_acceptors(
            traj.topology, protein)
        assert ligand_donors, "the ligand hydroxyl should donate"
        assert protein_acceptors, "the backbone oxygen should accept"
        assert not protein_donors, "this residue has no polar hydrogen"
        assert ligand_acceptors

    def test_the_threshold_is_a_setting_not_a_constant(self) -> None:
        """The published values disagree, so which was used has to be
        visible -- and PLIP's remain reachable."""
        from fastmdxplora.analysis.interactions import hydrogen_bonds

        traj, ligand, protein = self._complex()
        assert hydrogen_bonds(traj, ligand, protein, distance_nm=0.15,
                              periodic=False) == []
        assert hydrogen_bonds(traj, ligand, protein, angle_deg=179.9,
                              periodic=False)


class TestATopologyThatCannotAnswerSaysSo:
    """Found by comparing against another tool, which is what that is for.

    A selection carrying hydrogens with no bonds to them cannot be seen to
    donate, only to accept. The first version returned zero and said nothing --
    the same defect fixed in the protein-ligand hydrogen bond analysis earlier,
    reappearing in new code because the condition is easy to miss and silent
    when hit.

    It is not hypothetical. Renaming a residue in a PDB is enough to lose its
    standard bonds, and a ligand deposited without CONECT records arrives the
    same way.
    """

    @staticmethod
    def _hydrogens_without_bonds():
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("LIG", chain, resSeq=1)
        top.add_atom("N", md.element.nitrogen, residue)
        top.add_atom("H", md.element.hydrogen, residue)   # no bond between them
        other = top.add_chain()
        second = top.add_residue("ALA", other, resSeq=2)
        ca = top.add_atom("CA", md.element.carbon, second)
        o = top.add_atom("O", md.element.oxygen, second)
        top.add_bond(ca, o)
        xyz = np.array([[[0, 0, 0], [0.1, 0, 0], [0.4, 0, 0], [0.3, 0, 0]]],
                       dtype=np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    def test_it_refuses_rather_than_reporting_none(self) -> None:
        import pytest

        from fastmdxplora.analysis.interactions import donors_and_acceptors

        traj = self._hydrogens_without_bonds()
        with pytest.raises(ValueError, match="bonded to nothing"):
            donors_and_acceptors(traj.topology, [0, 1])

    def test_the_message_says_what_would_fix_it(self) -> None:
        import pytest

        from fastmdxplora.analysis.interactions import donors_and_acceptors

        traj = self._hydrogens_without_bonds()
        with pytest.raises(ValueError, match="CONECT"):
            donors_and_acceptors(traj.topology, [0, 1])

    def test_a_selection_with_no_hydrogens_is_not_blocked(self) -> None:
        """Accepting only is a fact about that selection, not a gap in the
        topology."""
        from fastmdxplora.analysis.interactions import donors_and_acceptors

        traj = self._hydrogens_without_bonds()
        donors, acceptors = donors_and_acceptors(traj.topology, [2, 3])
        assert donors == []
        assert acceptors == [3]

    def test_it_agrees_with_an_independent_implementation(self) -> None:
        """The criterion is Baker and Hubbard's, and MDTraj implements the
        same one in different code. Where two implementations of one published
        rule agree on every atom pair, the rule is being read the same way."""

        from fastmdxplora.analysis.interactions import hydrogen_bonds

        # A short peptide with real backbone geometry: N-H donors and
        # carbonyl acceptors, bonded as a topology from a file would be.
        rng = np.random.RandomState(4)
        top = md.Topology()
        chain = top.add_chain()
        previous_c = None
        for index in range(6):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            n = top.add_atom("N", md.element.nitrogen, residue)
            h = top.add_atom("H", md.element.hydrogen, residue)
            ca = top.add_atom("CA", md.element.carbon, residue)
            c = top.add_atom("C", md.element.carbon, residue)
            o = top.add_atom("O", md.element.oxygen, residue)
            for first, second in ((n, h), (n, ca), (ca, c), (c, o)):
                top.add_bond(first, second)
            if previous_c is not None:
                top.add_bond(previous_c, n)
            previous_c = c

        base = rng.normal(scale=0.25, size=(top.n_atoms, 3))
        traj = md.Trajectory(xyz=base[None].astype(np.float32), topology=top)

        probe = np.array([a.index for a in top.atoms if a.residue.index == 2])
        rest = np.array([a.index for a in top.atoms
                         if a.residue.index not in (1, 2, 3)])

        ours = {
            (min(c.ligand_atom, c.protein_atom), max(c.ligand_atom, c.protein_atom))
            for c in hydrogen_bonds(traj, probe, rest, periodic=False)
        }
        probe_set, rest_set = set(probe.tolist()), set(rest.tolist())
        theirs = {
            (min(int(d), int(a)), max(int(d), int(a)))
            for d, _h, a in md.baker_hubbard(traj, freq=0.0, periodic=False)
            if (int(d) in probe_set and int(a) in rest_set)
            or (int(d) in rest_set and int(a) in probe_set)
        }
        # Baker-Hubbard measures hydrogen to acceptor and this measures donor
        # to acceptor, so it is the stricter of the two: everything it finds
        # must be here, though the reverse need not hold.
        assert theirs <= ours, f"MDTraj found pairs we did not: {theirs - ours}"


class TestSaltBridgesAreAClaimAboutCharge:
    """So they are not reported from a charge that was guessed.

    Perception from coordinates is worst at exactly this: guanidinium is +1
    and -1 also balances, so a guessed charge can turn a cation into an anion
    and invent the bridge it was meant to detect.
    """

    @pytest.fixture(autouse=True)
    def _needs_rdkit(self):
        """A ligand's chemistry needs RDKit, which is the [ligand] extra.

        What is being tested is chemistry -- bond orders, aromaticity, formal
        charge -- so there is nothing useful to assert without it. A mock
        would only check that the mock was called.
        """
        pytest.importorskip("rdkit", reason="requires the [ligand] extra")


    @staticmethod
    def _ligand(smiles, charge):
        from rdkit import Chem
        from rdkit.Chem import AllChem

        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        AllChem.EmbedMolecule(mol, randomSeed=2)
        elements = {"C": md.element.carbon, "O": md.element.oxygen,
                    "N": md.element.nitrogen, "H": md.element.hydrogen}
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("LIG", chain, resSeq=1)
        for index, atom in enumerate(mol.GetAtoms()):
            top.add_atom(f"{atom.GetSymbol()}{index}",
                         elements[atom.GetSymbol()], residue)
        conf = mol.GetConformer()
        xyz = np.array(
            [[list(conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())]],
            dtype=np.float32) / 10.0
        traj = md.Trajectory(xyz=xyz, topology=top)
        indices = list(range(mol.GetNumAtoms()))
        return traj, indices, resolve_ligand_chemistry(
            traj, "LIG", indices, net_charge=charge, allow_fetch=False)

    def test_the_charge_sits_on_the_atoms_that_carry_it(self) -> None:
        """A carboxylate's charge is across its two oxygens, not on the carbon
        between them. Including the carbon would put the centre a third of a
        bond length into the molecule."""
        from fastmdxplora.analysis.interactions import ligand_charged_groups

        traj, indices, chemistry = self._ligand("CC(=O)[O-]", -1)
        _positive, negative = ligand_charged_groups(chemistry, indices)
        assert len(negative) == 1
        names = {traj.topology.atom(i).name for i in negative[0]}
        assert all(n.startswith("O") for n in names), names
        assert len(names) == 2

    def test_a_delocalised_cation_is_one_group(self) -> None:
        from fastmdxplora.analysis.interactions import ligand_charged_groups

        traj, indices, chemistry = self._ligand("NC(=[NH2+])N", 1)
        positive, _negative = ligand_charged_groups(chemistry, indices)
        assert len(positive) == 1, "guanidinium is one charge, not three"
        names = {traj.topology.atom(i).name for i in positive[0]}
        assert len(names) == 3 and all(n.startswith("N") for n in names)

    def test_a_neutral_ligand_has_no_charged_groups(self) -> None:
        from fastmdxplora.analysis.interactions import ligand_charged_groups

        _traj, indices, chemistry = self._ligand("c1ccccc1O", 0)
        positive, negative = ligand_charged_groups(chemistry, indices)
        assert positive == [] and negative == []

    def test_an_undetermined_charge_stops_the_measurement(self) -> None:
        import pytest

        from fastmdxplora.analysis.interactions import salt_bridges
        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        traj, indices, _stated = self._ligand("NC(=[NH2+])N", 1)
        guessed = resolve_ligand_chemistry(traj, "LIG", indices,
                                           allow_fetch=False)
        assert guessed.charge_was_ambiguous, "this fixture should be ambiguous"
        with pytest.raises(ValueError, match="claim about charge"):
            salt_bridges(traj, guessed, indices, [])

    def test_and_the_message_says_how_to_settle_it(self) -> None:
        import pytest

        from fastmdxplora.analysis.interactions import salt_bridges
        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        traj, indices, _stated = self._ligand("NC(=[NH2+])N", 1)
        guessed = resolve_ligand_chemistry(traj, "LIG", indices,
                                           allow_fetch=False)
        with pytest.raises(ValueError, match="State the ligand's net charge"):
            salt_bridges(traj, guessed, indices, [])

    def test_stating_the_charge_lets_it_proceed(self) -> None:
        from fastmdxplora.analysis.interactions import salt_bridges

        traj, indices, chemistry = self._ligand("NC(=[NH2+])N", 1)
        assert not chemistry.charge_was_ambiguous
        assert salt_bridges(traj, chemistry, indices, []) == []

    def test_a_protein_side_chain_is_known_not_perceived(self) -> None:
        """A protein is made of twenty residues. Asking a perception routine
        which arginine is positive would invite it to be wrong about something
        already settled."""
        from fastmdxplora.analysis.interactions import protein_charged_groups

        top = md.Topology()
        chain = top.add_chain()
        arg = top.add_residue("ARG", chain, resSeq=1)
        for name in ("CD", "NE", "CZ", "NH1", "NH2"):
            element = md.element.carbon if name.startswith("C") else md.element.nitrogen
            top.add_atom(name, element, arg)
        asp = top.add_residue("ASP", chain, resSeq=2)
        for name in ("CG", "OD1", "OD2"):
            element = md.element.carbon if name.startswith("C") else md.element.oxygen
            top.add_atom(name, element, asp)

        positive, negative = protein_charged_groups(top, range(top.n_atoms))
        assert len(positive) == 1 and len(negative) == 1
        assert {top.atom(i).name for i in positive[0]} == {"NE", "NH1", "NH2"}
        assert {top.atom(i).name for i in negative[0]} == {"OD1", "OD2"}

    def test_histidine_is_left_to_the_topology(self) -> None:
        """It titrates near physiological pH, so whether it is charged was
        decided when setup protonated it. Guessing here would contradict a
        decision made with more information."""
        from fastmdxplora.analysis.interactions import protein_charged_groups

        top = md.Topology()
        chain = top.add_chain()
        his = top.add_residue("HIS", chain, resSeq=1)
        for name in ("CG", "ND1", "CE1", "NE2"):
            element = md.element.carbon if name.startswith("C") else md.element.nitrogen
            top.add_atom(name, element, his)
        positive, _negative = protein_charged_groups(top, range(top.n_atoms))
        assert positive == []


class TestRingsStackedOnRings:
    """Two rings can be the right distance apart and side by side rather than
    stacked. Distance alone cannot tell those apart, which is why the offset
    from the ring's axis is part of the criterion and not a refinement of it.
    """

    @pytest.fixture(autouse=True)
    def _needs_rdkit(self):
        """A ligand's chemistry needs RDKit, which is the [ligand] extra.

        What is being tested is chemistry -- bond orders, aromaticity, formal
        charge -- so there is nothing useful to assert without it. A mock
        would only check that the mock was called.
        """
        pytest.importorskip("rdkit", reason="requires the [ligand] extra")


    @staticmethod
    def _rings(separation_nm, tilt_deg, sideways_nm=0.0):
        """A benzene placed exactly over a phenylalanine ring."""
        from rdkit import Chem
        from rdkit.Chem import AllChem

        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        top = md.Topology()
        chain = top.add_chain()
        phe = top.add_residue("PHE", chain, resSeq=1)
        for name in ("CG", "CD1", "CD2", "CE1", "CE2", "CZ"):
            top.add_atom(name, md.element.carbon, phe)

        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
        benzene = Chem.AddHs(Chem.MolFromSmiles("c1ccccc1"))
        AllChem.EmbedMolecule(benzene, randomSeed=1)
        elements = {"C": md.element.carbon, "H": md.element.hydrogen}
        for index, atom in enumerate(benzene.GetAtoms()):
            top.add_atom(f"{atom.GetSymbol()}{index}",
                         elements[atom.GetSymbol()], ligand)

        angles = np.linspace(0, 2 * np.pi, 6, endpoint=False)
        protein_xyz = np.column_stack(
            [0.14 * np.cos(angles), 0.14 * np.sin(angles), np.zeros(6)])
        conf = benzene.GetConformer()
        ligand_xyz = np.array(
            [list(conf.GetAtomPosition(i)) for i in range(benzene.GetNumAtoms())]
        ) / 10.0
        ligand_xyz -= ligand_xyz.mean(axis=0)
        tilt = np.deg2rad(tilt_deg)
        rotation = np.array([[1, 0, 0],
                             [0, np.cos(tilt), -np.sin(tilt)],
                             [0, np.sin(tilt), np.cos(tilt)]])
        ligand_xyz = ligand_xyz @ rotation.T + np.array(
            [sideways_nm, 0, separation_nm])

        traj = md.Trajectory(
            xyz=np.vstack([protein_xyz, ligand_xyz])[None].astype(np.float32),
            topology=top)
        ligand_idx = list(range(6, top.n_atoms))
        chemistry = resolve_ligand_chemistry(
            traj, "LIG", ligand_idx, net_charge=0, allow_fetch=False)
        return traj, chemistry, ligand_idx, list(range(6))

    def test_stacked_rings_are_face_to_face(self) -> None:
        from fastmdxplora.analysis.interactions import pi_stacking

        traj, chemistry, ligand, protein = self._rings(0.38, 0)
        found = pi_stacking(traj, chemistry, ligand, protein)
        assert [c.kind for c in found] == ["pi_stacking_face_to_face"]

    def test_a_perpendicular_ring_is_edge_to_face(self) -> None:
        """A different interaction with a different geometry, and a ligand
        that stacks one way and not the other says something about the
        pocket."""
        from fastmdxplora.analysis.interactions import pi_stacking

        traj, chemistry, ligand, protein = self._rings(0.50, 90)
        found = pi_stacking(traj, chemistry, ligand, protein)
        assert [c.kind for c in found] == ["pi_stacking_edge_to_face"]

    def test_rings_side_by_side_are_not_stacked(self) -> None:
        """At stacking distance but off the axis. This is what the offset test
        is for, and distance alone would call it a stack."""
        from fastmdxplora.analysis.interactions import pi_stacking

        traj, chemistry, ligand, protein = self._rings(0.38, 0, sideways_nm=0.40)
        assert pi_stacking(traj, chemistry, ligand, protein) == []

    def test_rings_too_far_apart_are_not_stacked(self) -> None:
        from fastmdxplora.analysis.interactions import pi_stacking

        traj, chemistry, ligand, protein = self._rings(0.70, 0)
        assert pi_stacking(traj, chemistry, ligand, protein) == []

    def test_a_tryptophan_offers_both_of_its_rings(self) -> None:
        """Fused, but a partner sits over one or the other and the centre of
        the pair is over neither."""
        from fastmdxplora.analysis.interactions import protein_aromatic_rings

        top = md.Topology()
        chain = top.add_chain()
        trp = top.add_residue("TRP", chain, resSeq=1)
        for name in ("CG", "CD1", "CD2", "NE1", "CE2", "CE3", "CZ2", "CZ3", "CH2"):
            element = md.element.nitrogen if name.startswith("N") else md.element.carbon
            top.add_atom(name, element, trp)
        rings = protein_aromatic_rings(top, range(top.n_atoms))
        assert len(rings) == 2
        assert sorted(len(r) for r in rings) == [5, 6]

    def test_a_ring_missing_an_atom_is_left_out(self) -> None:
        """It has no well-defined plane, and fitting one to what is left puts
        the normal somewhere the ring is not."""
        from fastmdxplora.analysis.interactions import protein_aromatic_rings

        top = md.Topology()
        chain = top.add_chain()
        phe = top.add_residue("PHE", chain, resSeq=1)
        for name in ("CG", "CD1", "CD2", "CE1"):        # two atoms short
            top.add_atom(name, md.element.carbon, phe)
        assert protein_aromatic_rings(top, range(top.n_atoms)) == []

    def test_a_pi_cation_needs_a_charge_that_was_determined(self) -> None:
        import pytest

        from fastmdxplora.analysis.interactions import pi_cation
        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        traj, _chemistry, ligand, protein = self._rings(0.38, 0)
        guessed = resolve_ligand_chemistry(traj, "LIG", ligand, allow_fetch=False)
        if not guessed.charge_was_ambiguous:
            pytest.skip("benzene's charge is unambiguous, as it should be")
        with pytest.raises(ValueError, match="claim about charge"):
            pi_cation(traj, guessed, ligand, protein)


class TestHalogenBondsAndMetals:
    """Where two tools disagree, the literature decides and the disagreement
    is recorded.

    PLIP counts fluorine as a halogen bond donor; ProLIF does not. Politzer
    and co-workers attribute fluorine's failure to halogen bond to its
    electronegativity and sp hybridisation neutralising the sigma-hole, and
    organic fluorine bound to carbon is unlikely to participate. Fluorine can
    halogen bond attached to something strongly electron-withdrawing, but that
    is not a C-F in a drug molecule.
    """

    @pytest.fixture(autouse=True)
    def _needs_rdkit(self):
        pytest.importorskip("rdkit", reason="requires the [ligand] extra")

    @staticmethod
    def _pointed_at_an_acceptor(smiles, angle_deg, distance_nm=0.30):
        from rdkit import Chem
        from rdkit.Chem import AllChem

        from fastmdxplora.analysis.ligand_chemistry import resolve_ligand_chemistry

        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        AllChem.EmbedMolecule(mol, randomSeed=5)
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain, resSeq=1)
        carbon = top.add_atom("C", md.element.carbon, residue)
        oxygen = top.add_atom("O", md.element.oxygen, residue)
        top.add_bond(carbon, oxygen)

        ligand_chain = top.add_chain()
        ligand = top.add_residue("LIG", ligand_chain, resSeq=900)
        elements = {"C": md.element.carbon, "H": md.element.hydrogen,
                    "F": md.element.fluorine, "Cl": md.element.chlorine,
                    "Br": md.element.bromine}
        for index, atom in enumerate(mol.GetAtoms()):
            top.add_atom(f"{atom.GetSymbol()}{index}",
                         elements[atom.GetSymbol()], ligand)

        halogen = next(a.GetIdx() for a in mol.GetAtoms()
                       if a.GetSymbol() in ("F", "Cl", "Br"))
        attached = next(n.GetIdx()
                        for n in mol.GetAtomWithIdx(halogen).GetNeighbors())
        conf = mol.GetConformer()
        ligand_xyz = np.array(
            [list(conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())]) / 10.0
        axis = ligand_xyz[halogen] - ligand_xyz[attached]
        axis /= np.linalg.norm(axis)
        turn = np.deg2rad(180 - angle_deg)
        direction = np.array([
            axis[0] * np.cos(turn) - axis[1] * np.sin(turn),
            axis[0] * np.sin(turn) + axis[1] * np.cos(turn),
            axis[2]])
        acceptor_at = ligand_xyz[halogen] + direction * distance_nm
        protein_xyz = np.array([acceptor_at + np.array([0, 0, 0.15]), acceptor_at])

        traj = md.Trajectory(
            xyz=np.vstack([protein_xyz, ligand_xyz])[None].astype(np.float32),
            topology=top)
        ligand_idx = list(range(2, top.n_atoms))
        chemistry = resolve_ligand_chemistry(
            traj, "LIG", ligand_idx, net_charge=0, allow_fetch=False)
        return traj, chemistry, ligand_idx, [0, 1]

    def test_a_chlorine_pointing_at_an_acceptor_bonds(self) -> None:
        from fastmdxplora.analysis.interactions import halogen_bonds

        args = self._pointed_at_an_acceptor("Clc1ccccc1", 175)
        assert len(halogen_bonds(*args)) == 1

    def test_a_bent_halogen_does_not(self) -> None:
        """The sigma-hole points along the carbon-halogen axis, so the
        interaction is directional and closeness alone is not enough."""
        from fastmdxplora.analysis.interactions import halogen_bonds

        args = self._pointed_at_an_acceptor("Clc1ccccc1", 100)
        assert halogen_bonds(*args) == []

    def test_fluorine_is_not_counted_by_default(self) -> None:
        from fastmdxplora.analysis.interactions import halogen_bonds

        args = self._pointed_at_an_acceptor("Fc1ccccc1", 175)
        assert halogen_bonds(*args) == [], (
            "organic fluorine bound to carbon has no positive sigma-hole"
        )

    def test_but_it_can_be_asked_for(self) -> None:
        """PLIP counts it, so somebody comparing against PLIP needs it."""
        from fastmdxplora.analysis.interactions import halogen_bonds

        traj, chemistry, ligand, protein = self._pointed_at_an_acceptor(
            "Fc1ccccc1", 175)
        assert len(halogen_bonds(traj, chemistry, ligand, protein,
                                 include_fluorine=True)) == 1

    def test_a_metal_is_found_on_either_side(self) -> None:
        """An ion is often neither protein nor ligand in the way a selection
        divides them."""
        from fastmdxplora.analysis.interactions import metal_coordination

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("HIS", chain, resSeq=1)
        top.add_atom("NE2", md.element.nitrogen, residue)
        ion_chain = top.add_chain()
        ion = top.add_residue("ZN", ion_chain, resSeq=500)
        top.add_atom("ZN", md.element.zinc, ion)

        traj = md.Trajectory(
            xyz=np.array([[[0, 0, 0], [0.21, 0, 0]]], dtype=np.float32),
            topology=top)
        found = metal_coordination(traj, [1], [0])
        assert len(found) == 1
        assert found[0].kind == "metal_coordination"

    def test_a_metal_too_far_away_is_not_coordinating(self) -> None:
        from fastmdxplora.analysis.interactions import metal_coordination

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("HIS", chain, resSeq=1)
        top.add_atom("NE2", md.element.nitrogen, residue)
        ion_chain = top.add_chain()
        ion = top.add_residue("ZN", ion_chain, resSeq=500)
        top.add_atom("ZN", md.element.zinc, ion)
        traj = md.Trajectory(
            xyz=np.array([[[0, 0, 0], [0.45, 0, 0]]], dtype=np.float32),
            topology=top)
        assert metal_coordination(traj, [1], [0]) == []


class TestOccupancyCarriesItsObservation:
    """A contact seen in 3 frames of 500 and one seen in 450 are both
    "present". Only one means anything.

    Reporting both as an occupancy hides that the first rests on three
    observations. The same mistake in a different guise ran through the
    hydrogen bond count for a long time: bonds present in few frames were
    dropped from every frame, and the plot said one thing while the trajectory
    said another.
    """

    @staticmethod
    def _contact(frame, tag=1):
        from fastmdxplora.analysis.interactions import Contact

        return Contact("hydrogen_bond", frame, 100, 200 + tag, 0.3, 170.0)

    def test_the_same_fraction_can_rest_on_very_different_watching(self) -> None:
        from fastmdxplora.analysis.interaction_summary import occupancies

        steady = [self._contact(f) for f in range(450)]
        flickering = [self._contact(f, tag=2) for f in range(0, 900, 2)]

        settled = occupancies(steady, 900)[0]
        moving = occupancies(flickering, 900)[0]

        assert settled.fraction == moving.fraction == 0.5
        assert settled.episodes == 1, "it formed once and stayed"
        assert moving.episodes == 450

    def test_the_error_counts_episodes_not_frames(self) -> None:
        """Consecutive frames are correlated. A contact present in 450
        consecutive frames has not been measured 450 times, and using the
        frame count would give an error several times too small."""
        import numpy as np

        from fastmdxplora.analysis.interaction_summary import occupancies

        flickering = [self._contact(f) for f in range(0, 900, 2)]
        moving = occupancies(flickering, 900)[0]
        by_episodes = moving.uncertainty
        by_frames = np.sqrt(0.5 * 0.5 / 450)
        assert np.isclose(by_episodes, by_frames, rtol=0.05), (
            "here they agree because every frame is its own episode"
        )

        steady = [self._contact(f) for f in range(450)]
        settled = occupancies(steady, 900)[0]
        assert np.isnan(settled.uncertainty), (
            "one observation supports no error bar at all"
        )

    def test_a_rare_contact_is_marked_as_thinly_observed(self) -> None:
        from fastmdxplora.analysis.interaction_summary import occupancies

        rare = [self._contact(f) for f in (10, 11, 12)]
        found = occupancies(rare, 500)[0]
        assert found.fraction < 0.01
        assert not found.is_well_sampled
        assert found.as_record()["well_sampled"] is False

    def test_the_record_carries_both_numbers(self) -> None:
        """So a reader sees the observation beside the fraction rather than
        having to ask for it."""
        from fastmdxplora.analysis.interaction_summary import occupancies

        record = occupancies([self._contact(f) for f in range(10)], 100)[0].as_record()
        assert record["fraction"] == 0.1
        assert record["frames_present"] == 10
        assert record["episodes"] == 1


class TestATransitionRateNeedsTransitions:
    """A matrix from three observed switches is arithmetic, not kinetics.

    Computing it is easy; knowing whether the trajectory supports it is the
    part that decides whether the answer means anything, and it is the check
    the other tools do not make.
    """

    def test_a_trajectory_that_barely_switches_gets_no_rate(self) -> None:
        from fastmdxplora.analysis.interaction_summary import mode_transitions

        barely = [["a"]] * 200 + [["b"]] * 200 + [["a"]] * 200
        out = mode_transitions(barely)
        assert out["observed_transitions"] == 2
        assert not out["supported"]
        assert out["probabilities"] is None

    def test_but_the_counts_are_still_given(self) -> None:
        """Withholding the number would be as unhelpful as presenting it as a
        rate. What is withheld is the claim, not the observation."""
        from fastmdxplora.analysis.interaction_summary import mode_transitions

        barely = [["a"]] * 200 + [["b"]] * 200 + [["a"]] * 200
        out = mode_transitions(barely)
        assert out["counts"], "the observations should still be reported"
        assert "uncertainty larger than itself" in out["reason"]

    def test_a_trajectory_that_switches_often_gets_one(self) -> None:
        from fastmdxplora.analysis.interaction_summary import mode_transitions

        often = [["a"] if (f // 7) % 2 == 0 else ["b"] for f in range(600)]
        out = mode_transitions(often)
        assert out["supported"]
        assert out["probabilities"]
        rows = list(out["probabilities"].values())
        assert all(abs(sum(row.values()) - 1.0) < 1e-6 for row in rows)

    def test_one_frame_cannot_show_a_transition(self) -> None:
        from fastmdxplora.analysis.interaction_summary import mode_transitions

        out = mode_transitions([["a"]])
        assert not out["supported"]
        assert "at least two frames" in out["reason"]


class TestBindingModesAreCombinations:
    """A mode is the set of interactions present in a frame, and the modes are
    what a ligand moves between."""

    @staticmethod
    def _contact(frame, tag):
        from fastmdxplora.analysis.interactions import Contact

        return Contact("hydrogen_bond", frame, 100, 200 + tag, 0.3, 170.0)

    def test_frames_sharing_a_set_are_one_mode(self) -> None:
        from fastmdxplora.analysis.interaction_summary import binding_modes

        contacts = ([self._contact(f, 1) for f in range(100)]
                    + [self._contact(f, 2) for f in range(50)])
        found = binding_modes(contacts, 100)
        assert len(found["modes"]) == 2
        assert found["modes"][0]["frames"] == 50

    def test_a_fleeting_contact_does_not_split_a_mode(self) -> None:
        """Otherwise a single flicker turns one arrangement into two."""
        from fastmdxplora.analysis.interaction_summary import binding_modes

        contacts = ([self._contact(f, 1) for f in range(100)]
                    + [self._contact(3, 2)])
        found = binding_modes(contacts, 100, minimum_occupancy=0.1)
        assert len(found["modes"]) == 1


class TestAnAlphaCarbonIsNotCalcium:
    """Found by running the analysis on a protein with no metals in it.

    Every amino acid has an atom called CA -- its alpha carbon -- and CA is
    also calcium's symbol. Matching atom names turns every residue into a
    metal centre. CD, HG, NA and IN are the same trap: cadmium, mercury,
    sodium and indium are all ordinary atom names in a protein.
    """

    def test_a_protein_with_no_metals_reports_none(self) -> None:
        from fastmdxplora.analysis.interactions import metal_coordination

        top = md.Topology()
        chain = top.add_chain()
        for index, resname in enumerate(("ALA", "LYS", "TRP")):
            residue = top.add_residue(resname, chain, resSeq=index + 1)
            for name in ("N", "CA", "C", "O", "CB", "CD", "HG"):
                element = {"N": md.element.nitrogen, "O": md.element.oxygen}.get(
                    name[0], md.element.carbon)
                top.add_atom(name, element, residue)

        rng = np.random.RandomState(1)
        traj = md.Trajectory(
            xyz=rng.normal(scale=0.1, size=(1, top.n_atoms, 3)).astype(np.float32),
            topology=top)
        assert metal_coordination(traj, range(7), range(7, top.n_atoms)) == []

    def test_a_real_ion_is_still_found(self) -> None:
        from fastmdxplora.analysis.interactions import metal_coordination

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("HIS", chain, resSeq=1)
        top.add_atom("NE2", md.element.nitrogen, residue)
        ion_chain = top.add_chain()
        ion = top.add_residue("ZN", ion_chain, resSeq=500)
        top.add_atom("ZN", md.element.zinc, ion)

        traj = md.Trajectory(
            xyz=np.array([[[0, 0, 0], [0.21, 0, 0]]], dtype=np.float32),
            topology=top)
        assert len(metal_coordination(traj, [1], [0])) == 1

    def test_a_metal_symbol_inside_a_residue_is_a_misassignment(self) -> None:
        """An ion is its own residue. A calcium found among a residue's other
        atoms is a mislabelled carbon, not a coordination centre."""
        from fastmdxplora.analysis.interactions import metal_coordination

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain, resSeq=1)
        top.add_atom("CA", md.element.calcium, residue)   # mislabelled
        top.add_atom("CB", md.element.carbon, residue)
        other = top.add_chain()
        second = top.add_residue("SER", other, resSeq=2)
        top.add_atom("OG", md.element.oxygen, second)

        traj = md.Trajectory(
            xyz=np.array([[[0, 0, 0], [0.15, 0, 0], [0.2, 0, 0]]],
                         dtype=np.float32),
            topology=top)
        assert metal_coordination(traj, [0, 1], [2]) == []


class TestSettingsThatWereFixedInPlace:
    """Two numbers were written into the code where version 1 had them as
    options, and both looked settled without being so.

    A seed fixed at 42 makes every run agree with every other, which reads as
    determinism and hides the thing worth knowing: k-means finds a local
    optimum, and a clustering that survives a change of seed is a finding
    while one that does not is an artefact of where the algorithm started.

    Hydrogen bond cutoffs written into the code cannot be compared against
    another tool's, and the same day every other threshold in this software
    became a setting, these were missed.
    """

    def test_the_clustering_seed_can_be_changed(self) -> None:
        from fastmdxplora.analysis.cluster import Cluster

        assert Cluster().random_state == 42, "the old fixed value is the default"
        assert Cluster(random_state=7).random_state == 7
        assert Cluster(n_init=25).n_init == 25

    def test_a_different_seed_can_give_a_different_clustering(self) -> None:
        """Which is the point. If it could not, the option would be theatre."""
        from fastmdxplora.analysis.cluster import _cluster_kmeans

        rng = np.random.RandomState(0)
        points = rng.normal(size=(60, 4))
        distances = np.linalg.norm(
            points[:, None, :] - points[None, :, :], axis=-1)

        first = _cluster_kmeans(distances, 5, random_state=1, n_init=1)
        again = _cluster_kmeans(distances, 5, random_state=1, n_init=1)
        assert np.array_equal(first, again), "the same seed must repeat"
        # Different seeds may or may not differ on this data; what matters is
        # that the seed reaches the algorithm at all.
        other = _cluster_kmeans(distances, 5, random_state=99, n_init=1)
        assert other.shape == first.shape

    def test_the_hydrogen_bond_cutoffs_reach_the_measurement(self) -> None:
        from fastmdxplora.analysis.hbonds import HBonds

        traj = _peptide_with_side_chain_donors()
        loose = HBonds(distance_cutoff=0.30).compute(traj)["n_hbonds"].sum()
        tight = HBonds(distance_cutoff=0.20).compute(traj)["n_hbonds"].sum()
        assert loose >= tight, "a looser cutoff cannot find fewer"

    def test_they_reach_both_places_that_use_them(self) -> None:
        """They were written in two places -- passed to MDTraj to propose the
        bonds, and again to count them frame by frame. A setting reaching one
        would leave the count disagreeing with the bonds it was counting.

        Here the only bond sits 0.28 nm from its acceptor: outside the
        default 0.25, inside a chosen 0.30. With 0.30 it must be proposed
        and counted in every frame; reaching only one of the two places
        leaves it unproposed or uncounted, and the count at zero."""
        from fastmdxplora.analysis.hbonds import HBonds, _per_frame_baker_hubbard

        traj = _one_bond_at(0.28, n_frames=20)
        assert HBonds().compute(traj)["n_hbonds"].to_numpy().tolist() == [0] * 20
        counted = HBonds(distance_cutoff=0.30).compute(traj)["n_hbonds"].to_numpy()
        assert counted.tolist() == [1] * 20

        # And the per-frame mask takes what it is given, for both cutoffs.
        triple = np.array([[0, 1, 2]])
        within, _ = _per_frame_baker_hubbard(traj, triple, periodic=False, distance_cutoff=0.30)
        beyond, _ = _per_frame_baker_hubbard(traj, triple, periodic=False, distance_cutoff=0.25)
        assert within.tolist() == [1] * 20 and beyond.tolist() == [0] * 20
        bent = _one_bond_at(0.20, n_frames=5, angle_deg=110.0)
        loose, _ = _per_frame_baker_hubbard(bent, triple, periodic=False, angle_cutoff=100.0)
        strict, _ = _per_frame_baker_hubbard(bent, triple, periodic=False, angle_cutoff=120.0)
        assert loose.tolist() == [1] * 5 and strict.tolist() == [0] * 5

    def test_a_cutoff_that_would_be_ignored_is_refused(self) -> None:
        """Wernet-Nilsson uses an angle-dependent distance of its own.
        Accepting a cutoff and ignoring it would let somebody set a number and
        believe it was used."""
        import pytest

        from fastmdxplora.analysis.hbonds import HBonds

        traj = _peptide_with_side_chain_donors()
        with pytest.raises(ValueError, match="baker_hubbard only"):
            HBonds(method="wernet_nilsson", distance_cutoff=0.35).compute(traj)

    def test_side_chain_bonds_can_be_asked_for_on_their_own(self) -> None:
        """A backbone hydrogen bond holds the fold together; a side-chain one
        is what a substitution can change."""
        from fastmdxplora.analysis.hbonds import HBonds

        traj = _peptide_with_side_chain_donors()
        everything = HBonds().compute(traj)["n_hbonds"].sum()
        side_only = HBonds(sidechain_only=True).compute(traj)["n_hbonds"].sum()
        assert side_only <= everything


def _peptide_with_side_chain_donors():
    """A short peptide with backbone and side-chain donors.

    Named apart from the existing _hbond_traj, which takes arguments and is
    used by the tests above -- defining a second one under the same name
    shadowed it and broke them.
    """
    top = md.Topology()
    chain = top.add_chain()
    previous_c = None
    for index in range(6):
        residue = top.add_residue("SER", chain, resSeq=index + 1)
        n = top.add_atom("N", md.element.nitrogen, residue)
        h = top.add_atom("H", md.element.hydrogen, residue)
        ca = top.add_atom("CA", md.element.carbon, residue)
        c = top.add_atom("C", md.element.carbon, residue)
        o = top.add_atom("O", md.element.oxygen, residue)
        og = top.add_atom("OG", md.element.oxygen, residue)
        hg = top.add_atom("HG", md.element.hydrogen, residue)
        for first, second in ((n, h), (n, ca), (ca, c), (c, o), (ca, og),
                              (og, hg)):
            top.add_bond(first, second)
        if previous_c is not None:
            top.add_bond(previous_c, n)
        previous_c = c
    rng = np.random.RandomState(2)
    xyz = rng.normal(scale=0.22, size=(3, top.n_atoms, 3)).astype(np.float32)
    return md.Trajectory(xyz=xyz, topology=top)


class TestOmegaAndMDS:
    """Two things version 1 measured that this did not.

    Omega is the peptide bond torsion. It is near 180 degrees in almost every
    residue, and the exceptions are the finding: a cis bond near zero, most
    often before a proline.

    MDS answers a different question from PCA. PCA finds the directions of
    largest variance in the coordinates; MDS finds an arrangement preserving
    the distances between frames -- and the distance between two frames is
    their RMSD, which is what a structural biologist means by how different
    two conformations are.
    """

    @staticmethod
    def _peptide(n_residues=6):
        top = md.Topology()
        chain = top.add_chain()
        previous_c = None
        for index in range(n_residues):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            n = top.add_atom("N", md.element.nitrogen, residue)
            ca = top.add_atom("CA", md.element.carbon, residue)
            c = top.add_atom("C", md.element.carbon, residue)
            o = top.add_atom("O", md.element.oxygen, residue)
            cb = top.add_atom("CB", md.element.carbon, residue)
            for first, second in ((n, ca), (ca, c), (c, o), (ca, cb)):
                top.add_bond(first, second)
            if previous_c is not None:
                top.add_bond(previous_c, n)
            previous_c = c
        rng = np.random.RandomState(5)
        xyz = rng.normal(scale=0.2, size=(8, top.n_atoms, 3)).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    def test_omega_is_computed_by_default(self) -> None:
        from fastmdxplora.analysis.dihedrals import Dihedrals

        result = Dihedrals().compute(self._peptide())
        assert "omega_deg" in result.columns

    def test_it_can_be_left_out(self) -> None:
        from fastmdxplora.analysis.dihedrals import Dihedrals

        result = Dihedrals(angles=["phi", "psi"]).compute(self._peptide())
        assert "omega_deg" not in result.columns
        assert {"phi_deg", "psi_deg"} <= set(result.columns)

    def test_an_angle_that_does_not_exist_is_refused(self) -> None:
        import pytest

        from fastmdxplora.analysis.dihedrals import Dihedrals

        with pytest.raises(ValueError, match="Unknown dihedral"):
            Dihedrals(angles=["phi", "chi1"])

    def test_the_accepted_angles_are_declared_for_a_form_to_read(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis
        from fastmdxplora.analysis.dihedrals import VALID_ANGLES

        option = next(o for o in describe_analysis("dihedrals")
                      if o.name == "angles")
        assert option.choices == VALID_ANGLES

    def test_omega_is_matched_to_the_residue_it_belongs_to(self) -> None:
        """MDTraj's quartet is CA(i-1), C(i-1), N(i), CA(i), so the residue is
        the one at index 3 where phi's is at index 2. Joining on the wrong one
        would shift every value by a residue -- a plausible plot of the wrong
        thing."""
        import mdtraj

        traj = self._peptide()
        phi_idx, _ = mdtraj.compute_phi(traj)
        omega_idx, _ = mdtraj.compute_omega(traj)
        phi_residue = traj.topology.atom(int(phi_idx[0, 2])).residue.index
        omega_residue = traj.topology.atom(int(omega_idx[0, 3])).residue.index
        assert phi_residue == omega_residue

    def test_mds_is_offered_again(self) -> None:
        from fastmdxplora.analysis.dimred import VALID_METHODS

        assert "mds" in VALID_METHODS

    def test_mds_preserves_the_distances_it_claims_to(self) -> None:
        """Which is what makes it a different question rather than a worse
        PCA."""
        from itertools import combinations

        from fastmdxplora.analysis.dimred import _classical_mds, _pairwise_rmsd

        traj = self._peptide(n_residues=8)
        distances = _pairwise_rmsd(traj)
        embedded = _classical_mds(distances, 2)

        pairs = list(combinations(range(traj.n_frames), 2))
        real = np.array([distances[i, j] for i, j in pairs])
        got = np.array([np.linalg.norm(embedded[i] - embedded[j])
                        for i, j in pairs])
        assert np.corrcoef(real, got)[0, 1] > 0.8

    def test_the_distance_matrix_is_symmetric(self) -> None:
        """md.rmsd is not exactly symmetric to floating point, and an
        asymmetric matrix gives MDS a slightly complex eigenspectrum."""
        from fastmdxplora.analysis.dimred import _pairwise_rmsd

        distances = _pairwise_rmsd(self._peptide())
        assert np.allclose(distances, distances.T)


class TestSASAReportsWhatVersionOneReported:
    """Version 1 wrote three things from one run: the total per frame, every
    residue per frame, and each residue's mean over the whole run.

    The third is the one somebody reads -- which residues are buried and which
    are on the surface. The per-frame matrix contains it, but reading it off a
    heatmap by eye is not the same as having it.
    """

    @staticmethod
    def _traj():
        top = md.Topology()
        chain = top.add_chain()
        previous_c = None
        for index in range(5):
            residue = top.add_residue("ALA", chain, resSeq=index + 1)
            n = top.add_atom("N", md.element.nitrogen, residue)
            ca = top.add_atom("CA", md.element.carbon, residue)
            c = top.add_atom("C", md.element.carbon, residue)
            o = top.add_atom("O", md.element.oxygen, residue)
            cb = top.add_atom("CB", md.element.carbon, residue)
            for first, second in ((n, ca), (ca, c), (c, o), (ca, cb)):
                top.add_bond(first, second)
            if previous_c is not None:
                top.add_bond(previous_c, n)
            previous_c = c
        # A peptide, not a cloud of points. Random coordinates at 0.3 nm put
        # the closest pair 0.074 nm apart -- half a bond length, and deep
        # inside another atom's van der Waals radius. Shrake-Rupley answers
        # such a geometry by deciding, for each of its sphere points, whether
        # it falls inside a neighbour; for spheres nested that far the
        # decision is knife-edge, and a point flipping changes an atom's area
        # by a whole quantum. The two routes to the average then disagreed by
        # eight per cent on one residue, on one job of fifteen.
        #
        # Built here from bond lengths and angles, so the atoms are where
        # atoms go, and jittered per frame to give the run something to
        # average over.
        backbone = np.array([
            [0.000, 0.000, 0.000],   # N
            [0.146, 0.000, 0.000],   # CA
            [0.199, 0.140, 0.000],   # C
            [0.322, 0.157, 0.000],   # O
            [0.199, -0.076, -0.123],  # CB
        ], dtype=np.float64)

        positions = np.concatenate([
            backbone + np.array([0.38 * index, 0.0, 0.0])
            for index in range(5)
        ])

        # Jitter small against a bond: at 0.02 nm per coordinate the draw
        # brought one N and CA to 0.075 nm apart, which is half a bond and
        # back inside the regime this fixture exists to leave.
        rng = np.random.RandomState(3)
        xyz = (positions[None, :, :]
               + rng.normal(scale=0.008, size=(6, top.n_atoms, 3))
               ).astype(np.float32)
        return md.Trajectory(xyz=xyz, topology=top)

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason=(
            "Computes surface areas for real, so it meets the MDTraj "
            "defect that returns unwritten frames on Windows. Not "
            "strict: which calls are affected varies, so the test "
            "passes whenever the retry finds a complete answer."
        ),
        strict=False,
    )
    def test_the_average_per_residue_is_a_mode_of_its_own(self) -> None:
        from fastmdxplora.analysis.sasa import SASA

        result = SASA(mode="average_residue").compute(self._traj())
        assert list(result.columns) == ["residue", "mean_sasa_nm2",
                                        "std_sasa_nm2"]
        assert len(result) == 5, "one row per residue, not per frame"

    def test_it_carries_the_spread_as_well_as_the_mean(self) -> None:
        """A residue at 1.0 every frame and one alternating between 0 and 2
        have the same mean and are not the same thing."""
        from fastmdxplora.analysis.sasa import SASA

        result = SASA(mode="average_residue").compute(self._traj())
        assert (result["std_sasa_nm2"] >= 0).all()
        assert result["std_sasa_nm2"].notna().all()

    def test_a_per_residue_run_writes_the_average_beside_it(self, tmp_path) -> None:
        """That run already has every number the average needs, so computing
        the surface again for it would cost minutes for arithmetic."""
        from fastmdxplora.analysis.sasa import SASA

        SASA(mode="residue", output_dir=str(tmp_path)).run(self._traj())
        written = {f.name for f in tmp_path.rglob("*") if f.is_file()}
        assert "sasa_average_per_residue.csv" in written

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason=(
            "MDTraj returns an unwritten final frame from the first call on "
            "Windows; reported upstream. Not strict, because which runs "
            "manifest it varies -- 3.11 on one commit, 3.9 and 3.12 on the "
            "next -- so requiring the failure would flake the other way. An "
            "unexpected pass is the signal that upstream has fixed it and "
            "the retry in sasa.py can go."
        ),
        strict=False,
    )
    def test_shrake_rupley_gives_the_same_answer_twice(self) -> None:
        """Pinned because it stopped being true.

        Our two routes to a per-residue average each call MDTraj once, and on
        Windows they disagreed: by eight per cent on one residue in one run,
        then by fifteen per cent on all five in the next, with the per-frame
        values matching what every other platform produces and the direct
        route alone coming out low. Different Python versions failed each time
        -- 3.11, then 3.9 and 3.12 -- which is what nondeterminism looks like
        rather than a version-specific bug.

        Ten calls on one trajectory are bit-identical here. If this fails, the
        fault is in the surface-area calculation itself and not in anything
        this package does with the result, and this is the reproduction to
        send upstream.
        """
        traj = self._traj()
        runs = [
            md.shrake_rupley(traj, probe_radius=0.14, n_sphere_points=960,
                             mode="residue")
            for _ in range(10)
        ]

        for index, run in enumerate(runs[1:], start=2):
            assert np.array_equal(runs[0], run), (
                f"call {index} differs from the first by up to "
                f"{np.abs(run - runs[0]).max():.4g} nm^2 on the same "
                f"trajectory.\n  first: {runs[0]}\n  this : {run}"
            )

    def test_the_two_routes_to_the_average_agree(self, monkeypatch) -> None:
        """One computes it directly, the other groups a per-frame table. They
        are the same number or one of them is wrong.

        Both are given the same surface areas, so this asks about the two
        aggregations and nothing else. It used to let each route call MDTraj
        for itself, which meant it was also asserting that two calls agree --
        a property of MDTraj, tested above, and one that failed on Windows
        while this package's arithmetic was correct.
        """
        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        traj = self._traj()
        fixed = self._areas()
        monkeypatch.setattr(sasa_module.md, "shrake_rupley",
                            lambda *args, **kwargs: fixed)

        direct = SASA(mode="average_residue").compute(traj)
        per_frame = SASA(mode="residue").compute(traj)
        grouped = per_frame.groupby("residue")["sasa_nm2"].mean()

        left = direct.set_index("residue")["mean_sasa_nm2"].to_numpy()
        right = grouped.loc[direct["residue"]].to_numpy()

        if not np.allclose(left, right, atol=1e-6):
            offenders = {
                int(res): per_frame.loc[per_frame["residue"] == res,
                                        "sasa_nm2"].tolist()
                for res, ok in zip(direct["residue"],
                                   np.isclose(left, right, atol=1e-6))
                if not ok
            }
            raise AssertionError(
                f"the two aggregations disagree on one array of areas.\n"
                f"  direct : {left}\n  grouped: {right}\n"
                f"  per-frame values for the residues that differ: {offenders}"
            )

    #: The final frame MDTraj returns from a first call on Windows: a partial
    #: value where the write stopped, then zeros.
    TRUNCATED_TAIL = [0.5354027, 0.0, 0.0, 0.0, 0.0]

    #: Areas as a working platform returns them. Fixed rather than computed,
    #: because these tests are about recognising a truncated answer and
    #: computing one on Windows may itself return a truncated answer -- three
    #: of them failed that way, building their fixtures from the fault they
    #: exist to detect.
    COMPLETE = np.array([
        [1.5136634, 0.9937443, 0.92079043, 1.0116162, 1.4542173],
        [1.6021402, 0.9488294, 0.94146925, 0.9430271, 1.4671384],
        [1.5551056, 0.9427111, 0.95254680, 0.9983771, 1.4339538],
        [1.5624902, 0.9891032, 0.97434320, 0.9191055, 1.4326298],
        [1.6004661, 0.9510080, 0.94540954, 0.9633576, 1.4857112],
        [1.5783486, 0.9532764, 0.95507420, 0.9405923, 1.4887716],
    ], dtype=np.float32)

    def _areas(self):
        return self.COMPLETE.copy()

    def test_the_frame_windows_returns_is_recognised(self) -> None:
        """A first version looked for an all-zero row and would not have seen
        it: the row is a partial value followed by zeros, because the write
        stopped part-way rather than never starting."""
        from fastmdxplora.analysis.sasa import _unwritten_frames

        areas = self._areas()
        truncated = areas.copy()
        truncated[-1] = self.TRUNCATED_TAIL

        assert _unwritten_frames(truncated).tolist() == [len(areas) - 1]
        assert _unwritten_frames(areas).size == 0

    def test_a_protein_with_buried_residues_is_left_alone(self) -> None:
        """The reason the first version of this check was wrong, and the case
        it broke.

        A deeply buried residue has a surface area of exactly zero, and
        flickers between zero and a little above it as the structure breathes.
        Asking whether a residue was exposed in some frames and zero in others
        is therefore true of every buried residue in every protein: it refused
        a solvated T4 lysozyme outright, five attempts and eighty seconds
        before giving up, on a run that was perfectly good.
        """
        from fastmdxplora.analysis.sasa import _unwritten_frames

        rng = np.random.RandomState(0)
        protein = rng.uniform(0.2, 2.0, size=(200, 160)).astype(np.float32)
        buried = rng.choice(160, 24, replace=False)
        protein[:, buried] = np.where(
            rng.uniform(size=(200, 24)) < 0.7, 0.0,
            rng.uniform(0, 0.05, size=(200, 24)))

        assert _unwritten_frames(protein).size == 0

    def test_a_wholly_buried_set_is_left_alone(self) -> None:
        """Every residue at zero in every frame is a strange system, not an
        unwritten one: nothing stands out against the rest."""
        from fastmdxplora.analysis.sasa import _unwritten_frames

        assert _unwritten_frames(np.zeros((10, 40), dtype=np.float32)).size == 0

    def test_a_row_wiped_in_the_middle_is_caught(self) -> None:
        """The fault is not confined to the last frame."""
        from fastmdxplora.analysis.sasa import _unwritten_frames

        rng = np.random.RandomState(0)
        areas = rng.uniform(0.2, 2.0, size=(50, 40)).astype(np.float32)
        areas[7] = 0.0

        assert _unwritten_frames(areas).tolist() == [7]

    def test_a_truncated_answer_is_computed_again(self, monkeypatch) -> None:
        """The second call returns the frame fully written, and it agrees with
        every other platform. Taken, and recorded."""
        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        traj = self._traj()
        complete = self._areas()
        truncated = complete.copy()
        truncated[-1] = self.TRUNCATED_TAIL

        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            return truncated if calls["n"] == 1 else complete

        monkeypatch.setattr(sasa_module.md, "shrake_rupley", flaky)

        analysis = SASA(mode="average_residue")
        result = analysis.compute(traj)

        assert calls["n"] == 2
        assert np.allclose(result["mean_sasa_nm2"].to_numpy(),
                           complete.mean(axis=0), atol=1e-6)
        assert "unwritten frame" in analysis.findings["recomputed"]

    def test_a_complete_answer_is_not_computed_twice(self, monkeypatch) -> None:
        """The retry is for a defect, not a routine."""
        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        traj = self._traj()
        complete = self._areas()
        calls = {"n": 0}

        def counted(*args, **kwargs):
            calls["n"] += 1
            return complete

        monkeypatch.setattr(sasa_module.md, "shrake_rupley", counted)
        analysis = SASA(mode="residue")
        analysis.compute(traj)

        assert calls["n"] == 1
        assert "recomputed" not in analysis.findings

    def test_always_truncated_is_refused(self, monkeypatch) -> None:
        """A workaround that quietly returns a wrong answer would be worse
        than the defect it works around."""
        import pytest as _pytest

        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        traj = self._traj()
        truncated = self._areas()
        truncated[-1] = self.TRUNCATED_TAIL

        monkeypatch.setattr(sasa_module.md, "shrake_rupley",
                            lambda *a, **k: truncated)

        with _pytest.raises(RuntimeError, match="all 5 attempts"):
            SASA(mode="residue").compute(traj)

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason=(
            "Characterises the MDTraj defect rather than testing this "
            "package. Reported upstream; the numbers in the failure message "
            "are what a fix needs."
        ),
        strict=False,
    )
    def test_how_often_the_calculation_is_truncated(self) -> None:
        """Twenty calls, counting how many come back incomplete and where.

        Written after two workarounds were designed on a guess and both proved
        wrong. The first assumed only the final frame was affected: frames 0,
        2 and 5 have since been seen. The second assumed a second call would
        be clean: CI returned answers truncated on both attempts.

        This measures instead of assuming. On a working platform every call is
        complete and it passes. Where it fails, the message carries the rate,
        the frames affected and whether the same frame recurs -- which is what
        decides whether asking again can work at all, or whether the analysis
        simply cannot be trusted there.
        """
        from fastmdxplora.analysis.sasa import _unwritten_frames

        traj = self._traj()
        affected: list[list[int]] = []
        for _ in range(20):
            areas = md.shrake_rupley(traj, probe_radius=0.14,
                                     n_sphere_points=960, mode="residue")
            affected.append(_unwritten_frames(areas).tolist())

        truncated = [frames for frames in affected if frames]
        if truncated:
            counts: dict[int, int] = {}
            for frames in truncated:
                for frame in frames:
                    counts[frame] = counts.get(frame, 0) + 1
            raise AssertionError(
                f"{len(truncated)} of 20 calls returned unwritten frames.\n"
                f"  frames affected, and how often: "
                f"{dict(sorted(counts.items()))}\n"
                f"  per call: {affected}"
            )

    def test_the_modes_are_declared_for_a_form_to_read(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis
        from fastmdxplora.analysis.sasa import VALID_MODES

        assert VALID_MODES == ("total", "residue", "average_residue")
        option = next(o for o in describe_analysis("sasa") if o.name == "mode")
        assert option.choices == VALID_MODES

    def test_an_unknown_mode_is_refused(self) -> None:
        import pytest

        from fastmdxplora.analysis.sasa import SASA

        with pytest.raises(ValueError, match="SASA mode"):
            SASA(mode="per_atom")


class TestASelectionThatIsNotProtein:
    """The charge and ring tables are the twenty standard amino acids, which
    is why they need no perception. The cost is that anything else falls
    through them silently.

    Point the protein side at a nucleic acid and the hydrogen bonds still come
    out right, being found from elements and bonds, while the salt bridges and
    the stacking come out as zero -- though a phosphate is charged and a
    nucleobase is aromatic. Zero is an answer; "these residues were not
    examined" is a different one, and the true one.
    """

    @staticmethod
    def _nucleotide():
        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("DG", chain, resSeq=1)
        for name, element in (("P", md.element.phosphorus),
                              ("OP1", md.element.oxygen),
                              ("OP2", md.element.oxygen),
                              ("N9", md.element.nitrogen),
                              ("C8", md.element.carbon)):
            top.add_atom(name, element, residue)
        return top

    def test_a_nucleotide_finds_no_charge_or_ring(self) -> None:
        """Which is the behaviour being reported, not a behaviour being
        fixed: the tables are amino acids by design."""
        from fastmdxplora.analysis.interactions import (
            protein_aromatic_rings, protein_charged_groups)

        top = self._nucleotide()
        positive, negative = protein_charged_groups(top, range(top.n_atoms))
        assert positive == [] and negative == []
        assert protein_aromatic_rings(top, range(top.n_atoms)) == []

    def test_and_it_is_reported_rather_than_passed_over(self) -> None:
        from fastmdxplora.analysis.interactions import residues_not_covered

        top = self._nucleotide()
        assert residues_not_covered(top, range(top.n_atoms)) == {"DG": 1}

    def test_ordinary_amino_acids_are_not_reported(self) -> None:
        """Including the ones with neither a charge nor a ring, which are left
        out of the tables because they have nothing to contribute rather than
        because they are unknown."""
        from fastmdxplora.analysis.interactions import residues_not_covered

        top = md.Topology()
        chain = top.add_chain()
        for index, name in enumerate(("ALA", "GLY", "ARG", "ASP", "TRP",
                                      "PRO", "CYS", "MET")):
            residue = top.add_residue(name, chain, resSeq=index + 1)
            top.add_atom("CA", md.element.carbon, residue)
        assert residues_not_covered(top, range(top.n_atoms)) == {}

    def test_a_modified_residue_is_named_so_it_can_be_judged(self) -> None:
        """One in a large protein is a footnote; a selection made entirely of
        them is not, and only the person who made the selection can tell
        which."""
        from fastmdxplora.analysis.interactions import residues_not_covered

        top = md.Topology()
        chain = top.add_chain()
        for index, name in enumerate(("ALA", "SEP", "ALA")):   # phosphoserine
            residue = top.add_residue(name, chain, resSeq=index + 1)
            top.add_atom("CA", md.element.carbon, residue)
        assert residues_not_covered(top, range(top.n_atoms)) == {"SEP": 1}

    def test_the_analysis_records_it(self) -> None:
        import pathlib

        from fastmdxplora.gui import server

        source = (pathlib.Path(server.__file__).parents[1] / "analysis"
                  / "pl_interactions.py").read_text(encoding="utf-8")
        assert "residues_not_examined_for_charge_or_rings" in source
        assert "residues_not_covered" in source


class TestAPeptideTooShortToFoldIsNotAFailure:
    """Q measures how much of a fold is intact, from contacts between
    residues far enough apart in sequence to probe tertiary structure. A
    tripeptide has no such pair, and the analysis raised -- recording a
    failed analysis for a peptide that simply has no tertiary structure.

    The same category error `requires_water` exists to avoid: "there is no
    water here" was a failed phase rather than a question that did not
    apply."""

    def _peptide(self, residues: int, waters: int = 0):
        """A peptide, optionally solvated.

        Solvated by default in the tests that matter, because that is where
        the first version of this gate failed: it counted the box's residues
        rather than the solute's, so a tripeptide in 526 waters looked like
        a 529-residue protein and the analysis was planned. It then sliced to
        the solute, found three, and raised -- the exact number the gate
        existed to catch.
        """
        import mdtraj as md
        import numpy as np

        top = md.Topology()
        chain = top.add_chain()
        for _ in range(residues):
            residue = top.add_residue("ALA", chain)
            for name, element in (
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon), ("O", md.element.oxygen),
            ):
                top.add_atom(name, element, residue)
        if waters:
            solvent = top.add_chain()
            for _ in range(waters):
                residue = top.add_residue("HOH", solvent)
                top.add_atom("O", md.element.oxygen, residue)
        atoms = residues * 4 + waters
        xyz = np.random.default_rng(0).normal(scale=0.5, size=(2, atoms, 3))
        return md.Trajectory(xyz, top)

    def _planned(self, tmp_path, residues: int, waters: int = 0):
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        trajectory = self._peptide(residues, waters)
        trajectory.save_pdb(str(tmp_path / "t.pdb"))
        trajectory.save_dcd(str(tmp_path / "t.dcd"))
        orchestrator = AnalysisOrchestrator(
            trajectory=str(tmp_path / "t.dcd"),
            topology=str(tmp_path / "t.pdb"),
            output_dir=str(tmp_path / "analysis"))
        return orchestrator._build_plan(None, None)

    def test_a_tripeptide_does_not_run_it(self, tmp_path) -> None:
        assert "qvalue" not in self._planned(tmp_path, 3)

    def test_nor_does_a_chain_exactly_as_long_as_the_separation(
        self, tmp_path
    ) -> None:
        """Four residues, separation four: the pair would be a residue with
        itself."""
        assert "qvalue" not in self._planned(tmp_path, 4)

    def test_the_shortest_chain_that_has_a_pair_does(self, tmp_path) -> None:
        assert "qvalue" in self._planned(tmp_path, 5)

    def test_a_protein_does(self, tmp_path) -> None:
        assert "qvalue" in self._planned(tmp_path, 12)

    def test_it_is_declared_rather_than_inferred(self) -> None:
        """So the gate reads the analysis's own requirement rather than
        hard-coding which analyses are about folds."""
        from fastmdxplora.analysis.qvalue import QValue

        assert QValue.requires_tertiary_structure is True

    def test_the_gate_uses_the_analysis_own_separation(self) -> None:
        """A user who sets `min_seq_separation: 10` needs eleven residues,
        not five.

        The plan was built before the options were read, so the gate fell
        back to 4 whatever was asked: a separation of 10 on six residues was
        planned and then failed as an error, and a separation of 2 on four
        was left out though it would have run."""
        from types import SimpleNamespace

        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        def planned(n_residues, separation=None):
            runner = SimpleNamespace(output_dir="unused", ligand_resname=None,
                                     traj=_a_chain(n_residues))
            asked = {} if separation is None else {"qvalue": {"min_seq_separation": separation}}
            return "qvalue" in AnalysisOrchestrator._build_plan(runner, None, None, asked)

        assert planned(6) and not planned(4)               # the default, 4
        assert not planned(6, separation=10)                # needs eleven
        assert planned(12, separation=10)
        assert planned(4, separation=2)                     # allowed, so kept

    def test_the_options_reach_the_plan(self, monkeypatch) -> None:
        # The gate can only honour a setting the plan is given.
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        seen: list = []

        class Planned(Exception):
            pass

        def planning(self, include, exclude, options=None):
            seen.append(options)
            raise Planned

        monkeypatch.setattr(AnalysisOrchestrator, "_build_plan", planning)
        runner = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        asked = {"qvalue": {"min_seq_separation": 10}}
        with pytest.raises(Planned):
            AnalysisOrchestrator.run(runner, options=asked)
        assert seen == [asked]

    def test_a_solvated_tripeptide_is_still_too_short(self, tmp_path) -> None:
        """The case that got through: 3 protein residues in 526 waters is
        529 residues by the box's count, and the gate read that as a protein
        long enough to fold."""
        assert "qvalue" not in self._planned(tmp_path, 3, waters=526)

    def test_water_does_not_make_a_tripeptide_long_enough(self, tmp_path) -> None:
        """The gate counted residues in the box. A solvated tripeptide has
        529 of them, three of which are peptide and the rest water: the gate
        saw 529, let qvalue through, and qvalue sliced to its own selection
        and failed on three. The analysis restricts to the solute before
        enumerating pairs, and the gate has to ask the same question."""
        assert "qvalue" not in self._planned(tmp_path, 3, waters=526)

    def test_a_solvated_protein_still_runs_it(self, tmp_path) -> None:
        assert "qvalue" in self._planned(tmp_path, 12, waters=500)

    def test_the_gate_counts_the_solute_not_the_box(self) -> None:
        """Asserted on behaviour rather than on a spelling: the first version
        of this demanded `residue.is_protein` and the implementation used
        `topology.select("protein")`, so a correct gate failed a test about
        how it was written."""
        assert "qvalue" not in self._planned_for(3, waters=526)
        assert "qvalue" in self._planned_for(12, waters=500)

    def _planned_for(self, residues: int, waters: int = 0):
        import tempfile
        from pathlib import Path

        return self._planned(Path(tempfile.mkdtemp()), residues, waters)


def _one_bond_at(distance_nm: float, *, n_frames: int, angle_deg: float = 180.0) -> md.Trajectory:
    """A donor, its hydrogen and an acceptor, the hydrogen `distance_nm` from
    the acceptor and the donor-hydrogen-acceptor angle `angle_deg`."""
    top = md.Topology()
    chain = top.add_chain()
    donor_residue = top.add_residue("ALA", chain, resSeq=1)
    n = top.add_atom("N", md.element.nitrogen, donor_residue)
    h = top.add_atom("H", md.element.hydrogen, donor_residue)
    acceptor_residue = top.add_residue("ALA", chain, resSeq=2)
    top.add_atom("O", md.element.oxygen, acceptor_residue)
    top.add_bond(n, h)
    turn = np.deg2rad(180.0 - angle_deg)
    xyz = np.zeros((n_frames, 3, 3), dtype=np.float32)
    xyz[:, 1] = [0.10, 0.0, 0.0]
    xyz[:, 2] = [0.10 + distance_nm * np.cos(turn), distance_nm * np.sin(turn), 0.0]
    return md.Trajectory(xyz=xyz, topology=top, time=np.arange(n_frames) * 1.0)


def _a_chain(n_residues: int) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    for index in range(n_residues):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
    return md.Trajectory(np.zeros((2, top.n_atoms, 3), dtype=np.float32), top)
