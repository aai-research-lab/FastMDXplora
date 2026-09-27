"""Molecules are made whole on the older MDTraj that Python 3.10 installs.

MDTraj 1.10.3 raises NotImplementedError from ``Residue.is_nucleic`` rather
than answering. Choosing the macromolecules to anchor on asked it, the error
was caught as an imaging failure, and every trajectory loaded on Python 3.10
was analysed unimaged: split molecules stayed split. Choosing now goes
through the same tolerant test the residue naming uses, and a failure to
place the other molecules in their nearest copy no longer undoes making them
whole.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.analysis import loading  # noqa: E402
from tests.test_a_distant_molecule_is_measured_in_its_nearest_copy import (  # noqa: E402
    _stored,
    _wrong_copies,
)


@pytest.fixture
def old_mdtraj(monkeypatch):
    def unanswered(self):
        raise NotImplementedError

    monkeypatch.setattr(md.core.topology.Residue, "is_nucleic", property(unanswered))


def _split_ligand():
    top = md.Topology()
    residue = top.add_residue("LIG", top.add_chain())
    atoms = [top.add_atom(f"C{i}", md.element.carbon, residue) for i in range(4)]
    for first, second in zip(atoms, atoms[1:]):
        top.add_bond(first, second)
    xyz = np.array([[[4.85, 2.5, 2.5], [4.95, 2.5, 2.5],
                     [0.05, 2.5, 2.5], [0.15, 2.5, 2.5]]], dtype=np.float32)
    trajectory = md.Trajectory(xyz, top)
    trajectory.unitcell_vectors = np.array([np.eye(3) * 5.0], dtype=np.float32)
    return trajectory


def _span(trajectory) -> float:
    return float(np.ptp(trajectory.xyz[0, :, 0]))


def test_the_fixture_is_the_old_behaviour(old_mdtraj) -> None:
    residue = _split_ligand().topology.residue(0)
    with pytest.raises(NotImplementedError):
        bool(residue.is_nucleic)


def test_a_split_molecule_is_made_whole(old_mdtraj) -> None:
    assert _span(loading._made_whole(_split_ligand())) == pytest.approx(0.3, abs=1e-4)


@pytest.mark.parametrize("shape", ["dodecahedron", "cube"])
def test_the_ligand_is_in_its_nearest_copy(old_mdtraj, shape) -> None:
    trajectory, box = _stored(shape, 100)
    assert _wrong_copies(loading._made_whole(trajectory), box, slice(40, 46)) == 0


def test_a_failed_placement_leaves_the_molecules_whole(monkeypatch, caplog) -> None:
    def fails(*_):
        raise ValueError("no lattice")

    monkeypatch.setattr(loading, "_nearest_copies", fails)
    trajectory, _ = _stored("cube", 20)
    split = trajectory.xyz[:, 40:46].copy()
    split[:, 3:] += np.float32(6.4)
    trajectory.xyz[:, 40:46] = split
    with caplog.at_level(logging.WARNING):
        imaged = loading._made_whole(trajectory)
    bonds = np.linalg.norm(np.diff(imaged.xyz[:, 40:46], axis=1), axis=2)
    assert np.allclose(bonds, 0.14, atol=1e-4)
    assert "no lattice" in caplog.text
    assert "nearest the macromolecule" in caplog.text
