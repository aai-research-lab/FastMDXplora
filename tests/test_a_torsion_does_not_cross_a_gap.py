"""A backbone torsion is not computed across a gap in the chain.

MDTraj joins consecutive residues of a chain by index and never asks
whether they are bonded, so the residue after a gap was given a phi and an
omega through atoms that are not joined: trypsin with residues 50 to 54
deleted read phi -61.8 and omega -110.4 degrees at residue 55, through a
C49-N55 distance of 1.68 nm.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.dihedrals import Dihedrals

DEPOSITED = Path(__file__).parent / "data" / "assemblies"


def _chain(n_residues: int = 8, *, bonds: bool = True, unbonded_before: int | None = None,
           n_frames: int = 4) -> md.Trajectory:
    """A backbone N, CA, C, O per residue on a zigzag with bond-length
    spacing, so every C(i-1)-N(i) is 0.145 nm apart. ``unbonded_before``
    leaves out the peptide bond into the residue at that index while
    keeping it close."""
    top = md.Topology()
    chain = top.add_chain()
    xyz = []
    previous_c = None
    step = 0
    for i in range(n_residues):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        atoms = []
        for name, element in (("N", md.element.nitrogen), ("CA", md.element.carbon),
                              ("C", md.element.carbon)):
            atoms.append(top.add_atom(name, element, res))
            xyz.append([0.125 * step, 0.07 * (step % 2), 0.03 * ((step // 2) % 2)])
            step += 1
        o = top.add_atom("O", md.element.oxygen, res)
        xyz.append([0.125 * (step - 1), -0.12, 0.0])
        n, ca, c = atoms
        if bonds:
            top.add_bond(n, ca)
            top.add_bond(ca, c)
            top.add_bond(c, o)
            if previous_c is not None and unbonded_before != i:
                top.add_bond(previous_c, n)
        previous_c = c
    rng = np.random.default_rng(7)
    frames = np.array(xyz)[None] + rng.normal(scale=0.004, size=(n_frames, len(xyz), 3))
    return md.Trajectory(frames.astype(np.float32), top)


def _without(traj: md.Trajectory, numbers: range) -> md.Trajectory:
    return traj.atom_slice([a.index for a in traj.topology.atoms
                            if a.residue.resSeq not in numbers])


def _by_hand(traj: md.Trajectory, number: int) -> dict[str, float]:
    """Phi, psi and omega of one residue from its named atoms, frame 0."""
    residues = {r.resSeq: r for r in traj.topology.residues}

    def atom(n: int, name: str) -> int:
        return next(a.index for a in residues[n].atoms if a.name == name)

    quartets = {
        "phi_deg": [atom(number - 1, "C"), atom(number, "N"), atom(number, "CA"), atom(number, "C")],
        "psi_deg": [atom(number, "N"), atom(number, "CA"), atom(number, "C"), atom(number + 1, "N")],
        "omega_deg": [atom(number - 1, "CA"), atom(number - 1, "C"), atom(number, "N"), atom(number, "CA")],
    }
    angles = np.rad2deg(md.compute_dihedrals(traj[0], np.array(list(quartets.values()))))[0]
    return dict(zip(quartets, angles))


class TestAGapIsNotATorsion:
    def test_the_residue_after_a_deleted_stretch_has_no_phi_or_omega(self) -> None:
        gapped = _without(_chain(), range(4, 6))
        analysis = Dihedrals()
        table = analysis.compute(gapped)
        assert 6 not in set(table["residue"]), "residue 6 follows the gap"
        assert 3 not in set(table["residue"]), "residue 3's psi would cross the gap"
        found = analysis.findings["chain_breaks"]
        assert found["quartets_dropped"] == {"phi": 1, "psi": 1, "omega": 1}
        assert found["residues_after_a_break"] == ["6"]

    def test_what_is_kept_matches_the_angles_computed_by_hand(self) -> None:
        gapped = _without(_chain(), range(4, 6))
        table = Dihedrals().compute(gapped)
        first = table[table["frame"] == 0].set_index("residue")
        for number in (2, 7):
            expected = _by_hand(gapped, number)
            for column, value in expected.items():
                assert first.loc[number, column] == pytest.approx(value, abs=1e-3)

    def test_an_unbonded_neighbour_is_a_break_even_when_close(self) -> None:
        traj = _chain(unbonded_before=4)      # the fifth residue, numbered 5
        table = Dihedrals().compute(traj)
        assert set(table["residue"]) == {2, 3, 6, 7}

    def test_without_bonds_the_distance_decides(self) -> None:
        gapped = _without(_chain(bonds=False), range(4, 6))
        assert gapped.topology.n_bonds == 0
        analysis = Dihedrals()
        table = analysis.compute(gapped)
        assert 6 not in set(table["residue"])
        # The continuous stretches keep every inner residue.
        assert set(table["residue"]) == {2, 7}

    def test_a_continuous_chain_loses_nothing(self) -> None:
        analysis = Dihedrals()
        table = analysis.compute(_chain())
        assert set(table["residue"]) == set(range(2, 8))
        assert analysis.findings["chain_breaks"]["quartets_dropped"] == {
            "phi": 0, "psi": 0, "omega": 0}
        assert "residues_after_a_break" not in analysis.findings["chain_breaks"]

    def test_trypsin_with_residues_50_to_54_deleted(self, tmp_path) -> None:
        """The deposited structure, the case the audit found."""
        source = tmp_path / "3PTB.pdb"
        source.write_bytes(gzip.decompress((DEPOSITED / "3PTB.pdb.gz").read_bytes()))
        trypsin = md.load(str(source))
        trypsin = trypsin.atom_slice(trypsin.topology.select("protein"))
        gapped = _without(trypsin, range(50, 55))
        c49 = next(a.index for a in gapped.topology.atoms
                   if a.residue.resSeq == 49 and a.name == "C")
        n55 = next(a.index for a in gapped.topology.atoms
                   if a.residue.resSeq == 55 and a.name == "N")
        assert md.compute_distances(gapped, [[c49, n55]])[0, 0] > 1.0
        table = Dihedrals().compute(gapped)
        assert 55 not in set(table["residue"])
        assert 49 not in set(table["residue"])
        assert 56 in set(table["residue"])
