"""How a ligand's chemistry was known, tested route by route.

The module records not just the chemistry but the route that produced it --
supplied file, this run's setup, the dictionary, or perception from the
coordinates -- because a hydrogen bond resting on perceived bond orders is a
weaker claim than one resting on stated ones, and the report says which.

The routes are tried in a fixed order of confidence, each falls through to
the next, and the error at the end names everything that was tried. Each of
those behaviours is a promise the docstrings make; none had a test.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")
pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402

from fastmdxplora.analysis.ligand_chemistry import (  # noqa: E402
    SOURCES,
    ResolvedChemistry,
    resolve_ligand_chemistry,
)


def _write_sdf(path: Path, smiles: str = "c1ccccc1") -> int:
    """An SDF with coordinates, as setup would write. Returns its atom count."""
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()
    return mol.GetNumAtoms()


def _benzene_traj() -> tuple[md.Trajectory, list[int]]:
    """A benzene as a trajectory would carry it: names and positions only."""
    topology = md.Topology()
    chain = topology.add_chain()
    residue = topology.add_residue("BNZ", chain)
    ring = [(0.14 * np.cos(a), 0.14 * np.sin(a), 0.0)
            for a in np.linspace(0, 2 * np.pi, 6, endpoint=False)]
    hydrogens = [(0.25 * np.cos(a), 0.25 * np.sin(a), 0.0)
                 for a in np.linspace(0, 2 * np.pi, 6, endpoint=False)]
    for i in range(6):
        topology.add_atom(f"C{i+1}", md.element.carbon, residue)
    for i in range(6):
        topology.add_atom(f"H{i+1}", md.element.hydrogen, residue)
    xyz = np.array([ring + hydrogens], dtype=float)
    return md.Trajectory(xyz, topology), list(range(12))


class TestTheSuppliedFileWins:
    def test_a_stated_sdf_is_the_answer_and_says_so(self, tmp_path: Path) -> None:
        traj, indices = _benzene_traj()
        n = _write_sdf(tmp_path / "bnz.sdf")
        got = resolve_ligand_chemistry(
            traj, "BNZ", indices, supplied=tmp_path / "bnz.sdf",
            allow_fetch=False)
        assert got.source == "supplied"
        assert got.n_atoms == n
        assert not got.is_perceived
        assert got.charge_was_ambiguous is False

    def test_a_wrong_molecule_falls_through_rather_than_lying(
            self, tmp_path: Path) -> None:
        """An SDF whose atom count does not match the trajectory's residue is
        not this ligand, however plausible its name."""
        traj, indices = _benzene_traj()
        _write_sdf(tmp_path / "bnz.sdf", smiles="CCO")  # 9 atoms, not 12
        got = resolve_ligand_chemistry(
            traj, "BNZ", indices, supplied=tmp_path / "bnz.sdf",
            allow_fetch=False)
        assert got.source == "perceived"  # fell through to the last route


class TestTheRunsOwnSetup:
    def test_a_setup_written_sdf_is_found_by_glob(self, tmp_path: Path) -> None:
        traj, indices = _benzene_traj()
        _write_sdf(tmp_path / "setup" / "ligands" / "BNZ.sdf")
        got = resolve_ligand_chemistry(
            traj, "BNZ", indices, run_dir=tmp_path, allow_fetch=False)
        assert got.source == "run"
        assert "BNZ.sdf" in got.detail

    def test_the_supplied_file_outranks_the_run(self, tmp_path: Path) -> None:
        """Nothing should stop somebody stating the chemistry of their own
        ligand -- including a run that resolved it differently."""
        traj, indices = _benzene_traj()
        _write_sdf(tmp_path / "given.sdf")
        _write_sdf(tmp_path / "setup" / "ligands" / "BNZ.sdf")
        got = resolve_ligand_chemistry(
            traj, "BNZ", indices, supplied=tmp_path / "given.sdf",
            run_dir=tmp_path, allow_fetch=False)
        assert got.source == "supplied"


class TestPerception:
    def test_the_last_route_works_and_confesses(self) -> None:
        """Perceived chemistry is usable and marked as such: the record says
        the bond orders were inferred, which is the caveat every interaction
        computed from it inherits."""
        traj, indices = _benzene_traj()
        got = resolve_ligand_chemistry(traj, "BNZ", indices, allow_fetch=False)
        assert got.source == "perceived"
        assert got.is_perceived
        assert "inferred from the coordinates" in got.detail
        assert got.mol.GetNumAtoms() == 12

    def test_a_stated_charge_is_used_and_recorded(self) -> None:
        traj, indices = _benzene_traj()
        got = resolve_ligand_chemistry(
            traj, "BNZ", indices, allow_fetch=False, net_charge=0)
        assert "you stated" in got.detail
        assert got.charge_was_ambiguous is False


class TestTheRecordItself:
    def test_every_source_has_a_confidence(self) -> None:
        """The manifest prints a confidence per source; a source without one
        would crash the report at the end of a finished run."""
        mol = Chem.MolFromSmiles("C")
        for source in SOURCES:
            record = ResolvedChemistry(
                mol=mol, source=source, detail="", resname="LIG",
                n_atoms=1).as_record()
            assert record["confidence"]
            assert record["source"] == source

    def test_perceived_is_the_only_source_that_admits_it(self) -> None:
        mol = Chem.MolFromSmiles("C")
        flags = {source: ResolvedChemistry(
            mol=mol, source=source, detail="", resname="LIG",
            n_atoms=1).is_perceived for source in SOURCES}
        assert flags == {"supplied": False, "run": False, "perceived": True}
