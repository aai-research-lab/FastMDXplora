"""SASA gives each residue's surface relative to its maximum, and splits the
total into hydrophobic and polar surface.

An area in nm2 does not say whether a residue is buried: a tryptophan at
0.5 nm2 is about a sixth exposed and a glycine at 0.5 nm2 almost fully.
Relative SASA, against the theoretical maxima of Tien et al. 2013, is the
number a reader compares across residues. And the total alone hides
whether a change is in exposed hydrophobic or polar surface, which is what
folding and binding change.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import mdtraj as md
import numpy as np
import pandas as pd
import pytest

from fastmdxplora.analysis.sasa import (
    MAX_ASA_TIEN_2013_A2,
    SASA,
    max_asa_nm2,
    surface_classes,
)

pytestmark = pytest.mark.xfail(
    sys.platform == "win32", strict=False,
    reason="MDTraj's surface areas on Windows; see test_concrete_analyses.")

ASSEMBLIES = Path(__file__).parent / "data" / "assemblies"


@pytest.fixture(scope="module")
def trypsin_with_hydrogens(tmp_path_factory) -> md.Trajectory:
    """3PTB's protein with hydrogens added by PDBFixer, four jittered frames."""
    pdbfixer = pytest.importorskip("pdbfixer")
    from openmm.app import PDBFile

    folder = tmp_path_factory.mktemp("ptb")
    raw = folder / "3PTB.pdb"
    raw.write_bytes(gzip.decompress((ASSEMBLIES / "3PTB.pdb.gz").read_bytes()))
    fixer = pdbfixer.PDBFixer(filename=str(raw))
    fixer.removeHeterogens(False)
    fixer.findMissingResidues()
    fixer.missingResidues = {}
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(7.0)
    built = folder / "3PTB_h.pdb"
    with open(built, "w") as handle:
        PDBFile.writeFile(fixer.topology, fixer.positions, handle)
    structure = md.load(str(built))
    rng = np.random.default_rng(2)
    xyz = structure.xyz + rng.normal(0.0, 0.005, (4,) + structure.xyz.shape[1:])
    return md.Trajectory(xyz.astype(np.float32), structure.topology)


class TestTheMaximaAreThePublishedOnes:
    def test_the_table_is_tien_2013_theoretical(self) -> None:
        """Table 1 of Tien et al. 2013, "Theor." column, A^2."""
        published = {
            "ALA": 129, "ARG": 274, "ASN": 195, "ASP": 193, "CYS": 167,
            "GLN": 225, "GLU": 223, "GLY": 104, "HIS": 224, "ILE": 197,
            "LEU": 201, "LYS": 236, "MET": 224, "PHE": 240, "PRO": 159,
            "SER": 155, "THR": 172, "TRP": 285, "TYR": 263, "VAL": 174,
        }
        assert MAX_ASA_TIEN_2013_A2 == published

    def test_force_field_names_read_as_their_residue(self) -> None:
        assert max_asa_nm2("CYX") == max_asa_nm2("CYS") == pytest.approx(1.67)
        assert max_asa_nm2("HIE") == max_asa_nm2("HIS") == pytest.approx(2.24)
        assert np.isnan(max_asa_nm2("HOH"))


class TestRelativeSurface:
    @pytest.mark.parametrize("mode, filename", [
        ("average_residue", "sasa.dat"),
        ("residue", "sasa_average_per_residue.csv"),
    ])
    def test_each_residue_over_its_maximum(self, tmp_path, trypsin_with_hydrogens,
                                           mode, filename) -> None:
        traj = trypsin_with_hydrogens
        analysis = SASA(mode=mode, n_sphere_points=240, output_dir=tmp_path)
        assert analysis.run(traj).status == "ok"
        written = pd.read_csv(tmp_path / "sasa" / filename)

        # Independently: MDTraj's per-residue areas, averaged, over the
        # published maximum looked up by residue name.
        areas = md.shrake_rupley(traj, mode="residue", n_sphere_points=240).mean(axis=0)
        names = [r.name for r in traj.topology.residues]
        expected = areas / np.array([MAX_ASA_TIEN_2013_A2[n] / 100.0 for n in names])
        if mode == "residue":
            # The summary is grouped, so in deposited order only where the
            # numbering is increasing: compare by number.
            order = np.argsort([r.resSeq for r in traj.topology.residues], kind="stable")
            expected = expected[order]
        np.testing.assert_allclose(written["mean_relative_sasa"], expected, rtol=1e-5)
        assert 0.0 <= written["mean_relative_sasa"].min()
        assert written["mean_relative_sasa"].max() < 1.0
        assert "Tien et al. 2013" in analysis.findings["relative_sasa"]

    def test_a_residue_that_is_not_an_amino_acid_has_none(self, tmp_path) -> None:
        top = md.Topology()
        chain = top.add_chain()
        for name in ("ALA", "LIG"):
            residue = top.add_residue(name, chain)
            top.add_atom("C1", md.element.carbon, residue)
        xyz = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]] * 3, dtype=np.float32)
        analysis = SASA(mode="average_residue", selection="all", output_dir=tmp_path)
        assert analysis.run(md.Trajectory(xyz, top)).status == "ok"
        written = pd.read_csv(tmp_path / "sasa" / "sasa.dat")
        assert not np.isnan(written["mean_relative_sasa"][0])
        assert np.isnan(written["mean_relative_sasa"][1])

    def test_another_probe_is_said_not_to_match(self) -> None:
        traj = md.Trajectory(np.zeros((2, 1, 3), dtype=np.float32), _one_alanine())
        analysis = SASA(mode="average_residue", selection="all", probe_radius=0.2)
        analysis.compute(traj)
        assert "not on the footing" in analysis.findings["relative_sasa"]


def _one_alanine() -> md.Topology:
    top = md.Topology()
    residue = top.add_residue("ALA", top.add_chain())
    top.add_atom("CA", md.element.carbon, residue)
    return top


class TestHydrophobicAndPolarSurface:
    def test_the_split_is_the_atoms_areas_by_element(self, tmp_path,
                                                     trypsin_with_hydrogens) -> None:
        traj = trypsin_with_hydrogens
        analysis = SASA(mode="total", n_sphere_points=240, output_dir=tmp_path)
        assert analysis.run(traj).status == "ok"
        split = pd.read_csv(tmp_path / "sasa" / "sasa_polar_split.csv")
        total = pd.read_csv(tmp_path / "sasa" / "sasa.dat")

        # Independently: each atom's area, a hydrogen given to the heavy atom
        # it is bonded to, then summed by that atom's element.
        atoms = md.shrake_rupley(traj, mode="atom", n_sphere_points=240)
        element = np.array([a.element.symbol for a in traj.topology.atoms], dtype=object)
        for a, b in traj.topology.bonds:
            if a.element.symbol == "H":
                element[a.index] = b.element.symbol
            elif b.element.symbol == "H":
                element[b.index] = a.element.symbol
        hydrophobic = atoms[:, np.isin(element, ["C", "S"])].sum(axis=1)
        polar = atoms[:, np.isin(element, ["N", "O"])].sum(axis=1)

        np.testing.assert_allclose(split["hydrophobic_sasa_nm2"], hydrophobic, rtol=1e-5)
        np.testing.assert_allclose(split["polar_sasa_nm2"], polar, rtol=1e-5)
        np.testing.assert_allclose(split["other_sasa_nm2"], 0.0, atol=1e-9)
        np.testing.assert_allclose(
            split[["hydrophobic_sasa_nm2", "polar_sasa_nm2", "other_sasa_nm2"]].sum(axis=1),
            total["sasa_nm2"], rtol=1e-5)
        for name in ("hydrophobic_sasa", "polar_sasa"):
            assert analysis.findings[name]["unit"] == "nm²"
            assert analysis.findings[name]["n_frames"] == traj.n_frames
        assert "carbon and sulfur" in analysis.findings["surface_classes"]

    def test_hydrogens_follow_their_heavy_atom_and_the_rest_are_other(self) -> None:
        top = md.Topology()
        residue = top.add_residue("MOL", top.add_chain())
        c = top.add_atom("C1", md.element.carbon, residue)
        o = top.add_atom("O1", md.element.oxygen, residue)
        hc = top.add_atom("H1", md.element.hydrogen, residue)
        ho = top.add_atom("H2", md.element.hydrogen, residue)
        top.add_atom("SE", md.element.selenium, residue)
        top.add_atom("H3", md.element.hydrogen, residue)  # no bond recorded
        top.add_bond(c, hc)
        top.add_bond(ho, o)
        top.add_bond(c, o)
        assert surface_classes(top).tolist() == [0, 1, 0, 1, 2, 2]
