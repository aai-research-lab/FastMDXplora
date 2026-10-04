"""Insertion codes are read from whichever file gave the topology.

They were read only from an external ``.pdb`` topology. A PDB loaded as the
trajectory itself, or an mmCIF topology, lost them, and trypsin's GLY 184A
and TYR 184 came out as two residues numbered 184 in one chain.
"""

from __future__ import annotations

import collections
import gzip
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DEPOSITED = Path(__file__).parent / "data" / "assemblies"


@pytest.fixture(autouse=True)
def _no_codes_from_earlier_tests(monkeypatch):
    from fastmdxplora.analysis import residues

    monkeypatch.setattr(residues, "_INSERTION_CODES", {})


def _deposited(tmp_path, name):
    path = tmp_path / name.replace(".gz", "")
    path.write_bytes(gzip.decompress((DEPOSITED / name).read_bytes()))
    return path


def _rmsf_labels(traj):
    from fastmdxplora.analysis.rmsf import RMSF

    table = RMSF(equilibrated_from=0).compute(md.join([traj] * 3))
    assert hasattr(table, "columns"), "a structure with insertion codes is a table"
    return list(zip(table["residue"], table["insertion"]))


def test_a_pdb_loaded_as_the_trajectory_keeps_them(tmp_path):
    from fastmdxplora.analysis.loading import load_trajectory

    traj = load_trajectory(str(_deposited(tmp_path, "3PTB.pdb.gz")))
    labels = _rmsf_labels(traj)

    assert not [k for k, n in collections.Counter(labels).items() if n > 1]
    assert {(184, "A"), (184, ""), (188, "A"), (221, "A")} <= set(labels)


def test_an_mmcif_topology_keeps_them(tmp_path):
    pytest.importorskip("openmm")  # MDTraj reads mmCIF through OpenMM
    from fastmdxplora.analysis.loading import load_trajectory

    cif = _deposited(tmp_path, "3PTB.cif.gz")
    structure = md.load(str(cif))
    structure.save_dcd(str(tmp_path / "one.dcd"))
    traj = load_trajectory(str(tmp_path / "one.dcd"), top=str(cif))
    labels = _rmsf_labels(traj)

    assert not [k for k, n in collections.Counter(labels).items() if n > 1]
    assert {(184, "A"), (184, ""), (188, "A"), (221, "A")} <= set(labels)


def test_the_mmcif_reader_takes_the_columns_by_name(tmp_path):
    """Read as the format writes them, quoted values and all, first model
    only, and keyed as MDTraj names the atoms."""
    from fastmdxplora.analysis import residues

    path = tmp_path / "x.cif"
    path.write_text(
        "data_X\n#\nloop_\n"
        "_atom_site.group_PDB\n_atom_site.id\n_atom_site.label_atom_id\n"
        "_atom_site.label_comp_id\n_atom_site.pdbx_PDB_ins_code\n"
        "_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.auth_asym_id\n"
        "_atom_site.pdbx_PDB_model_num\n"
        "ATOM 1 \"O5'\" GLY A 184 GLY A 1\n"
        "ATOM 2 CA TYR ? 184 TYR A 1\n"
        "ATOM 3 CA GLY A 184 GLY A 2\n"
        "#\n", encoding="utf-8")

    assert residues.remember_insertion_codes(path) == 1
    assert residues._INSERTION_CODES == {(1, "GLY", 184): "A"}


def test_a_file_without_codes_records_none(tmp_path):
    from fastmdxplora.analysis import residues

    assert residues.remember_insertion_codes(_deposited(tmp_path, "1AKE.pdb.gz")) == 0
    assert residues._INSERTION_CODES == {}


def test_the_numbers_of_an_inserted_residue_stay_numbers(tmp_path):
    from fastmdxplora.analysis.loading import load_trajectory

    traj = load_trajectory(str(_deposited(tmp_path, "3PTB.pdb.gz")))
    assert all(isinstance(n, (int, np.integer)) for n, _ in _rmsf_labels(traj))
