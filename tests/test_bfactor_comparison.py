"""B-factors against simulated fluctuations, and what the number is not."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.bfactor_comparison import (
    B_TO_MSF,
    BFactorComparison,
    bfactors_from_pdb,
    has_crystallographic_bfactors,
)


def _pdb(tmp_path, bfactors, name="input.pdb", chain="A"):
    lines = []
    for i, b in enumerate(bfactors, start=1):
        x = i * 3.8
        lines.append(
            f"ATOM  {i:5d}  CA  ALA {chain}{i:4d}    "
            f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}{1.00:6.2f}{b:6.2f}           C"
        )
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


class TestReadingTheColumn:
    def test_it_reads_by_column_not_by_splitting(self, tmp_path):
        """The PDB is fixed-width, and its fields run together.

        A five-figure atom serial or a four-character residue name leaves
        no space between columns, so splitting on whitespace picks up
        whatever sits next to the B-factor rather than the B-factor.
        """
        path = tmp_path / "tight.pdb"
        # Occupancy and B run together, as they do in real files.
        path.write_text(
            "ATOM  99999  CA  ALA A   1     "
            "  1.000   2.000   3.000  1.0012.34           C\n",
            encoding="utf-8")
        assert bfactors_from_pdb(path) == {("A", 1, ""): 12.34}

    def test_alternate_locations_beyond_the_first_are_skipped(self, tmp_path):
        path = tmp_path / "alt.pdb"
        path.write_text(
            "ATOM      1  CA AALA A   1       "
            "1.000   2.000   3.000  0.50 10.00           C\n"
            "ATOM      2  CA BALA A   1       "
            "1.100   2.000   3.000  0.50 90.00           C\n",
            encoding="utf-8")
        assert bfactors_from_pdb(path) == {("A", 1, ""): 10.00}

    def test_a_file_of_zeros_does_not_count_as_having_them(self, tmp_path):
        """A minimised or generated structure writes zeros there.

        Correlating against a column of zeros is not a comparison, and
        letting it through would produce a number with no meaning rather
        than no number.
        """
        assert not has_crystallographic_bfactors(_pdb(tmp_path, [0.0] * 8))
        assert has_crystallographic_bfactors(
            _pdb(tmp_path, [12.0, 20.0, 35.0, 18.0, 22.0, 30.0],
                 name="real.pdb"))

    def test_a_handful_of_placeholders_is_not_enough(self, tmp_path):
        values = [0.0] * 9 + [15.0]
        assert not has_crystallographic_bfactors(
            _pdb(tmp_path, values, name="sparse.pdb"))


def _traj(amplitudes, n_frames=300, seed=0):
    """A chain of alpha carbons, each jittering by its own amount."""
    rng = np.random.RandomState(seed)
    top = md.Topology()
    chain = top.add_chain()
    base = []
    for i, _ in enumerate(amplitudes):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
        base.append([i * 0.38, 0.0, 0.0])
    xyz = np.tile(np.array(base)[None], (n_frames, 1, 1))
    for i, amp in enumerate(amplitudes):
        xyz[:, i, :] += rng.normal(scale=amp, size=(n_frames, 3))
    return md.Trajectory(xyz=xyz.astype(np.float32), topology=top)


class TestTheComparisonItself:
    def test_the_conversion_is_the_isotropic_one(self, tmp_path):
        """B = (8 pi^2 / 3) <u^2>, and nothing else.

        Checked by handing it a trajectory whose fluctuations are known and
        B-factors computed from those same fluctuations: the two columns
        must come out equal, which they only do if the constant and the
        Angstrom-to-nanometre conversion are both right.
        """
        amplitudes = np.array([0.02, 0.05, 0.10, 0.04, 0.08, 0.03])
        traj = _traj(amplitudes, n_frames=4000)

        # The simulated RMSF this will measure, converted the other way.
        # Measured after the same superposition the analysis performs:
        # aligning removes the rigid-body drift, so fluctuations taken from
        # the raw coordinates are a different quantity and comparing
        # against them would fail a correct implementation.
        alpha = traj.topology.select("name CA")
        xyz = traj.superpose(traj, frame=0, atom_indices=alpha).xyz
        rmsf_nm = np.sqrt(np.mean(
            np.sum((xyz - xyz.mean(axis=0)) ** 2, axis=2), axis=0))
        b = (rmsf_nm * 10.0) ** 2 / B_TO_MSF

        analysis = BFactorComparison(
            structure=str(_pdb(tmp_path, b)), align_selection="name CA")
        table = analysis.compute(traj)

        assert np.allclose(table[:, 1], table[:, 2], atol=2e-3)
        assert analysis.findings["bfactor_comparison"]["pearson_r"] > 0.99

    def test_residues_without_a_b_factor_are_left_out(self, tmp_path):
        amplitudes = np.array([0.02, 0.05, 0.10, 0.04, 0.08, 0.03])
        b = np.array([12.0, 0.0, 30.0, 18.0, 0.0, 22.0])
        analysis = BFactorComparison(structure=str(_pdb(tmp_path, b)))
        table = analysis.compute(_traj(amplitudes))

        assert table.shape[0] == 4
        assert analysis.findings["bfactor_comparison"][
            "residues_unmatched"] == 2

    def test_the_finding_says_what_the_number_is_not(self, tmp_path):
        """The caveat travels with the correlation, not in a footnote.

        B-factors carry static disorder and refinement choices, and the
        lattice damps loop motion, so they bound amplitudes from below.
        Agreement everywhere can mean a protein held too tightly.
        """
        amplitudes = np.array([0.02, 0.05, 0.10, 0.04, 0.08, 0.03])
        b = (amplitudes * 10.0) ** 2 / B_TO_MSF
        analysis = BFactorComparison(structure=str(_pdb(tmp_path, b)))
        analysis.compute(_traj(amplitudes))

        note = analysis.findings["bfactor_comparison"]["what_this_is_not"]
        assert "from below" in note
        assert "regression slope" in note
        assert "pearson_r" in analysis.findings["bfactor_comparison"]
        assert "slope" not in [
            k for k in analysis.findings["bfactor_comparison"]
            if k != "what_this_is_not"]

    def test_no_structure_is_a_refusal_that_says_what_to_give(self, tmp_path):
        analysis = BFactorComparison(structure=str(tmp_path / "absent.pdb"))
        analysis.output_dir = tmp_path
        with pytest.raises(FileNotFoundError, match="started from"):
            analysis.compute(_traj(np.array([0.02, 0.05, 0.10])))

    def test_residues_that_do_not_line_up_are_refused(self, tmp_path):
        """Renumbering during preparation is the usual cause.

        Silently comparing whatever happened to match would report a
        correlation over two residues as though it were over two hundred.
        """
        b = np.array([12.0, 30.0, 18.0, 22.0, 25.0, 19.0])
        path = _pdb(tmp_path, b, chain="A")
        # Same B-factors, but the trajectory numbers its residues 101+.
        traj = _traj(np.array([0.02, 0.05, 0.10, 0.04, 0.08, 0.03]))
        for i, residue in enumerate(traj.topology.residues):
            residue.resSeq = 101 + i

        analysis = BFactorComparison(structure=str(path))
        with pytest.raises(ValueError, match="renumbered or renamed"):
            analysis.compute(traj)


class TestTheOutputsAndTheDiscovery:
    """Short mechanical paths, which is why nothing exercised them."""

    def test_it_plots_both_profiles(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        amplitudes = np.array([0.02, 0.05, 0.10, 0.04, 0.08, 0.03])
        b = (amplitudes * 10.0) ** 2 / B_TO_MSF
        analysis = BFactorComparison(structure=str(_pdb(tmp_path, b)))
        result = analysis.compute(_traj(amplitudes))

        _fig, ax = plt.subplots()
        analysis.plot(result, ax)
        assert len(ax.lines) == 2, "simulated and implied, drawn together"
        assert ax.get_legend() is not None
        assert analysis.default_ylabel().startswith("RMSF")
        assert analysis.default_xlabel() == "Residue"
        plt.close("all")

    def test_it_discovers_the_structure_beside_a_run(self, tmp_path):
        """The path a cluster exercises and a laptop does not."""
        run = tmp_path / "run"
        (run / "setup").mkdir(parents=True)
        _pdb(run / "setup", [12.0, 20.0, 35.0, 18.0, 22.0, 30.0])
        analysis = BFactorComparison()
        analysis.output_dir = run / "analysis" / "bfactor_comparison"
        analysis.output_dir.mkdir(parents=True)
        assert analysis._state_path() if hasattr(
            analysis, "_state_path") else analysis._structure_path()

    def test_an_alignment_set_too_small_is_refused(self, tmp_path):
        b = np.array([12.0, 20.0, 35.0, 18.0, 22.0, 30.0])
        analysis = BFactorComparison(
            structure=str(_pdb(tmp_path, b)),
            align_selection="resid 0 and name CA")
        with pytest.raises(ValueError, match="at least three"):
            analysis.compute(_traj(np.array([0.02] * 6)))


class TestEachResidueIsItsOwn:
    """Deposited structures, read as they are deposited."""

    @staticmethod
    def _deposited(tmp_path, entry):
        import gzip
        from pathlib import Path

        path = tmp_path / f"{entry}.pdb"
        path.write_bytes(gzip.decompress(
            (Path(__file__).parent / "data" / "assemblies" / f"{entry}.pdb.gz").read_bytes()))
        return path

    @staticmethod
    def _wobble(traj, frames=6, scale=0.01):
        rng = np.random.default_rng(0)
        xyz = traj.xyz + rng.normal(0.0, scale, (frames,) + traj.xyz.shape[1:])
        return md.Trajectory(xyz.astype(np.float32), traj.topology)

    @staticmethod
    def _deposited_b(result):
        implied = result["implied_nm"] if hasattr(result, "columns") else result[:, 2]
        number = result["residue"] if hasattr(result, "columns") else result[:, 0]
        return np.asarray(number), (np.asarray(implied) * 10.0) ** 2 / B_TO_MSF

    def test_an_insertion_code_keeps_its_own_b_factor(self, tmp_path, monkeypatch):
        """3PTB: GLY 184A has B 9.49 and TYR 184 has 20.72. Keyed by number
        alone, both were compared with 20.72."""
        from fastmdxplora.analysis import residues

        monkeypatch.setattr(residues, "_INSERTION_CODES", {})
        source = self._deposited(tmp_path, "3PTB")
        structure = md.load(str(source))
        traj = self._wobble(structure.atom_slice(structure.topology.select("protein")))

        result = BFactorComparison(structure=str(source)).compute(traj)
        number, b = self._deposited_b(result)

        assert sorted(np.round(b[number == 184], 2)) == [9.49, 20.72]
        assert sorted(np.round(b[number == 221], 2)) == [11.46, 18.73]

    def test_and_where_the_trajectory_carries_the_codes(self, tmp_path, monkeypatch):
        from fastmdxplora.analysis import residues
        from fastmdxplora.analysis.loading import load_trajectory

        monkeypatch.setattr(residues, "_INSERTION_CODES", {})
        source = self._deposited(tmp_path, "3PTB")
        structure = load_trajectory(str(source))
        traj = self._wobble(structure.atom_slice(structure.topology.select("protein")))

        result = BFactorComparison(structure=str(source)).compute(traj)
        rows = {(int(n), c): b for n, c, b in zip(
            result["residue"], result["insertion"],
            (result["implied_nm"] * 10.0) ** 2 / B_TO_MSF)}

        assert round(rows[(184, "A")], 2) == 9.49
        assert round(rows[(184, "")], 2) == 20.72

    def test_a_chain_is_compared_with_its_own_b_factors(self, tmp_path):
        """1HHO's chain B alone: matched by order, VAL 1 was compared with
        chain A's 70.91 rather than its own 25.93."""
        source = self._deposited(tmp_path, "1HHO")
        structure = md.load(str(source))
        chain_b = structure.topology.select("protein and chainid 1")
        sub = structure.atom_slice(chain_b)
        if not getattr(sub.topology.chain(0), "chain_id", None):
            # MDTraj before 1.11 drops the ID when it slices; setup's files
            # carry it, so the trajectory is given it back as a load would.
            sub.topology.chain(0).chain_id = "B"
        traj = self._wobble(sub, scale=0.02)

        analysis = BFactorComparison(structure=str(source))
        result = analysis.compute(traj)
        _number, b = self._deposited_b(result)

        assert round(float(b[0]), 2) == 25.93
        assert "chains_matched_by_order" not in analysis.findings

    def test_without_chain_ids_order_is_used_and_said(self, tmp_path):
        source = self._deposited(tmp_path, "1HHO")
        structure = md.load(str(source))
        sub = structure.atom_slice(structure.topology.select("protein and chainid 0"))
        sub.topology.chain(0).chain_id = None
        analysis = BFactorComparison(structure=str(source))
        result = analysis.compute(self._wobble(sub, scale=0.02))
        _number, b = self._deposited_b(result)

        assert round(float(b[0]), 2) == 70.91  # chain A's VAL 1, first by order
        assert "by order" in analysis.findings["chains_matched_by_order"]


def test_one_chain_with_insertion_codes_is_plotted(tmp_path):
    """Trypsin, one chain numbered with insertion codes (184 and 184A): its
    table has no chain column, and the figure grouped by one and raised, so
    a real 3PTB study recorded the analysis as an error."""
    import gzip
    from pathlib import Path

    import mdtraj as md
    import numpy as np

    from fastmdxplora.analysis.bfactor_comparison import BFactorComparison

    from fastmdxplora.analysis.loading import load_trajectory

    deposited = tmp_path / "input.pdb"
    text = gzip.decompress(
        (Path(__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz").read_bytes()).decode()
    deposited.write_text(text)
    # The protein's own lines, insertion codes and all, as a study's
    # trajectory topology keeps them; loaded as a study is.
    topology = tmp_path / "trajectory_topology.pdb"
    topology.write_text("".join(line + "\n" for line in text.splitlines()
                                if line.startswith("ATOM")) + "END\n")
    protein = md.load_pdb(str(topology))
    rng = np.random.default_rng(0)
    frames = protein.xyz + rng.normal(scale=0.02, size=(40,) + protein.xyz.shape[1:])
    md.Trajectory(frames.astype(np.float32), protein.topology).save_dcd(str(tmp_path / "p.dcd"))
    traj = load_trajectory(str(tmp_path / "p.dcd"), top=str(topology))
    result = BFactorComparison(structure=str(deposited), output_dir=str(tmp_path / "out")).run(traj)
    assert result.status == "ok", result.message
    assert (tmp_path / "out" / "bfactor_comparison" / "bfactor_comparison.png").is_file()
