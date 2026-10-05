"""What SASA writes per residue: one spread, every residue its own place,
and the surface it is computed against named.

The per-residue mean was written by two routes, and their spreads were two
statistics under one name: ``average_residue`` divided by n and the summary
beside a per-residue run by n - 1, so on five frames the second read
sqrt(5/4), 11.8 per cent, higher for every residue.
"""

from __future__ import annotations

import sys

import mdtraj as md
import numpy as np
import pandas as pd
import pytest


#: Areas as a working platform returns them for five residues, fixed so the
#: two aggregations are compared on one array and nothing else.
AREAS = np.array([
    [1.5136634, 0.9937443, 0.92079043, 1.0116162, 1.4542173],
    [1.6021402, 0.9488294, 0.94146925, 0.9430271, 1.4671384],
    [1.5551056, 0.9427111, 0.95254680, 0.9983771, 1.4339538],
    [1.5624902, 0.9891032, 0.97434320, 0.9191055, 1.4326298],
    [1.5212200, 0.9611100, 0.93021000, 0.9811900, 1.4481200],
], dtype=np.float32)


def _peptide(n_frames: int = 5) -> md.Trajectory:
    top = md.Topology()
    chain = top.add_chain()
    positions = []
    for index in range(5):
        residue = top.add_residue("ALA", chain, resSeq=index + 1)
        for name, element, place in (
                ("N", md.element.nitrogen, (0.000, 0.000, 0.000)),
                ("CA", md.element.carbon, (0.146, 0.000, 0.000)),
                ("C", md.element.carbon, (0.199, 0.140, 0.000)),
                ("O", md.element.oxygen, (0.322, 0.157, 0.000)),
                ("CB", md.element.carbon, (0.199, -0.076, -0.123))):
            top.add_atom(name, element, residue)
            positions.append(np.array(place) + [0.38 * index, 0.0, 0.0])
    rng = np.random.default_rng(3)
    xyz = np.array(positions)[None] + rng.normal(scale=0.008, size=(n_frames, top.n_atoms, 3))
    return md.Trajectory(xyz.astype(np.float32), top)


class TestOneSpreadUnderOneName:
    def test_both_routes_give_the_sample_standard_deviation(self, monkeypatch, tmp_path) -> None:
        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        monkeypatch.setattr(sasa_module.md, "shrake_rupley", lambda *a, **k: AREAS.copy())
        traj = _peptide()
        direct = SASA(mode="average_residue").compute(traj)
        per_residue = SASA(mode="residue", output_dir=tmp_path)
        per_residue.save_data(per_residue.compute(traj), per_residue.output_dir / "sasa.dat")
        summary = pd.read_csv(per_residue.output_dir / "sasa_average_per_residue.csv")

        expected = np.std(AREAS.astype(np.float64), axis=0, ddof=1)
        np.testing.assert_allclose(direct["std_sasa_nm2"].to_numpy(), expected, rtol=1e-12)
        np.testing.assert_allclose(summary["std_sasa_nm2"].to_numpy(), expected, rtol=1e-6)

    def test_one_frame_has_no_spread_rather_than_zero(self, monkeypatch) -> None:
        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        monkeypatch.setattr(sasa_module.md, "shrake_rupley", lambda *a, **k: AREAS[:1].copy())
        result = SASA(mode="average_residue").compute(_peptide(n_frames=1))
        assert result["std_sasa_nm2"].isna().all()

    @pytest.mark.xfail(sys.platform == "win32", strict=False,
                       reason="MDTraj's surface areas on Windows; see test_concrete_analyses.")
    def test_on_trypsin_the_two_files_agree(self, tmp_path, monkeypatch) -> None:
        """The deposited structure the audit found it on, five jittered frames,
        with 184A and 184 kept apart by their insertion codes."""
        import gzip
        from pathlib import Path

        from fastmdxplora.analysis import residues
        from fastmdxplora.analysis.sasa import SASA

        monkeypatch.setattr(residues, "_INSERTION_CODES", {})
        source = tmp_path / "3PTB.pdb"
        source.write_bytes(gzip.decompress(
            (Path(__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz").read_bytes()))
        residues.remember_insertion_codes(source)
        trypsin = md.load(str(source))
        trypsin = trypsin.atom_slice(trypsin.topology.select("protein"))[:1]
        rng = np.random.default_rng(0)
        traj = md.Trajectory(
            (trypsin.xyz + rng.normal(0, 0.01, (5,) + trypsin.xyz.shape[1:])).astype(np.float32),
            trypsin.topology)
        direct = SASA(mode="average_residue", n_sphere_points=240).compute(traj)
        per_residue = SASA(mode="residue", n_sphere_points=240, output_dir=tmp_path)
        per_residue.save_data(per_residue.compute(traj), per_residue.output_dir / "sasa.dat")
        summary = pd.read_csv(per_residue.output_dir / "sasa_average_per_residue.csv")
        assert len(summary) == len(direct) == 223
        both = direct.merge(summary.fillna({"insertion": ""}), on=["residue", "insertion"],
                            suffixes=("_direct", "_summary"))
        assert len(both) == 223
        np.testing.assert_allclose(both["std_sasa_nm2_summary"].to_numpy(),
                                   both["std_sasa_nm2_direct"].to_numpy(), rtol=1e-4)


def _with_an_insertion_code(monkeypatch) -> md.Trajectory:
    """Five residues numbered 183, 184A, 184, 185, 186, as trypsin numbers
    them: the code is known to the package by the first atom's serial, as
    the loader records it from the topology file."""
    from fastmdxplora.analysis import residues

    traj = _peptide()
    numbers = [(183, ""), (184, "A"), (184, ""), (185, ""), (186, "")]
    top = traj.topology.copy()
    codes = {}
    serial = 1
    for residue, (number, code) in zip(top.residues, numbers):
        residue.resSeq = number
        for atom in residue.atoms:
            atom.serial = serial
            serial += 1
        if code:
            codes[(next(iter(residue.atoms)).serial, residue.name, number)] = code
    monkeypatch.setattr(residues, "_INSERTION_CODES", codes)
    return md.Trajectory(traj.xyz, top)


class TestEveryResidueHasItsOwnBar:
    """Bars were placed at the residue number, so 184A and 184 of one chain
    stood at the same x and the taller hid the other."""

    def test_an_inserted_residue_is_a_bar_of_its_own(self, monkeypatch) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from fastmdxplora.analysis import sasa as sasa_module
        from fastmdxplora.analysis.sasa import SASA

        monkeypatch.setattr(sasa_module.md, "shrake_rupley", lambda *a, **k: AREAS.copy())
        traj = _with_an_insertion_code(monkeypatch)
        analysis = SASA(mode="average_residue")
        table = analysis.compute(traj)
        assert list(table["insertion"]) == ["", "A", "", "", ""]

        figure, ax = plt.subplots()
        try:
            analysis.plot(table, ax)
            bars = ax.patches
            centres = [bar.get_x() + bar.get_width() / 2 for bar in bars]
            heights = [bar.get_height() for bar in bars]
            labels = [tick.get_text() for tick in ax.get_xticklabels()]
        finally:
            plt.close(figure)
        assert len(set(centres)) == 5, "five residues, five places"
        np.testing.assert_allclose(heights, AREAS.astype(np.float64).mean(axis=0), rtol=1e-6)
        assert labels == ["183", "184A", "184", "185", "186"]


def _trypsin_with_benzamidine(tmp_path) -> md.Trajectory:
    import gzip
    from pathlib import Path

    source = tmp_path / "3PTB.pdb"
    source.write_bytes(gzip.decompress(
        (Path(__file__).parent / "data" / "assemblies" / "3PTB.pdb.gz").read_bytes()))
    complex_ = md.load(str(source))
    return complex_.atom_slice(complex_.topology.select("protein or resname BEN"))


@pytest.mark.xfail(sys.platform == "win32", strict=False,
                   reason="MDTraj's surface areas on Windows; see test_concrete_analyses.")
class TestTheSurfaceSaysWhetherTheLigandWasThere:
    """The default selection is the protein, so the surface reported beside
    a bound ligand is the protein's without it: in trypsin's S1 pocket
    SER190 read 0.110 nm2, against 0.001 nm2 with benzamidine in place."""

    def test_without_the_ligand_the_findings_say_so(self, tmp_path) -> None:
        from fastmdxplora.analysis.sasa import SASA

        traj = _trypsin_with_benzamidine(tmp_path)
        analysis = SASA(mode="average_residue", ligand_resname="BEN", n_sphere_points=240)
        table = analysis.compute(traj)
        assert "without the ligand BEN" in analysis.findings["ligand"]
        assert "with_ligand" in analysis.findings["ligand"]
        apo = md.shrake_rupley(traj.atom_slice(traj.topology.select("protein")),
                               mode="residue", n_sphere_points=240)[0]
        np.testing.assert_allclose(table["mean_sasa_nm2"].to_numpy(), apo, rtol=1e-6)

    def test_no_ligand_present_says_nothing(self, tmp_path) -> None:
        from fastmdxplora.analysis.sasa import SASA

        traj = _trypsin_with_benzamidine(tmp_path)
        protein = traj.atom_slice(traj.topology.select("protein"))
        analysis = SASA(mode="total", ligand_resname="BEN", n_sphere_points=240)
        analysis.compute(protein)
        assert "ligand" not in analysis.findings

    @pytest.mark.parametrize("mode", ["total", "residue", "average_residue"])
    def test_with_the_ligand_the_protein_atoms_of_the_complex_are_reported(
            self, tmp_path, mode) -> None:
        from fastmdxplora.analysis.sasa import SASA

        traj = _trypsin_with_benzamidine(tmp_path)
        analysis = SASA(mode=mode, ligand_resname="BEN", with_ligand=True,
                        n_sphere_points=240)
        table = analysis.compute(traj)
        assert "in the presence of the ligand BEN" in analysis.findings["ligand"]

        # Independently: every atom of the complex, then the protein's
        # atoms summed by residue.
        atoms = md.shrake_rupley(traj, mode="atom", n_sphere_points=240)[0]
        protein = traj.topology.select("protein")
        if mode == "total":
            assert table["sasa_nm2"].iloc[0] == pytest.approx(atoms[protein].sum(), rel=1e-5)
            return
        by_residue = np.zeros(traj.n_residues)
        np.add.at(by_residue, [traj.topology.atom(i).residue.index for i in protein],
                  atoms[protein])
        expected = by_residue[[r.index for r in traj.topology.residues if r.is_protein]]
        column = "sasa_nm2" if mode == "residue" else "mean_sasa_nm2"
        np.testing.assert_allclose(table[column].to_numpy(), expected, rtol=1e-5, atol=1e-7)
        serine = table.index[table["residue"] == 190][0]
        assert table[column].iloc[serine] < 0.01

    def test_with_the_ligand_needs_its_name(self) -> None:
        from fastmdxplora.analysis.sasa import SASA

        with pytest.raises(ValueError, match="needs `ligand_resname`"):
            SASA(with_ligand=True)

    def test_with_the_ligand_needs_it_present(self, tmp_path) -> None:
        from fastmdxplora.analysis.sasa import SASA

        traj = _trypsin_with_benzamidine(tmp_path)
        protein = traj.atom_slice(traj.topology.select("protein"))
        with pytest.raises(ValueError, match="no atoms of the ligand"):
            SASA(ligand_resname="BEN", with_ligand=True).compute(protein)
