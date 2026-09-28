"""A copy of an ion's name holding several atoms is not one ion.

A residue named ZN with two zinc atoms in it was kept, coordinated, as if it
were one ion. From a PDB identifier it was written into the structure as it
stood, where no force field template matches it ("has 1 Zn atom too many").
From a local file it was sent for chemistry, and refused with advice to
fetch `ZN_ideal.sdf` for a structure whose own residue was the problem.
Which ions its atoms are is not the structure's to say, so a copy that would
be kept is a question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.setup.heterogens import (
    Action,
    AmbiguousStructureError,
    decide,
    resolve,
)
from fastmdxplora.setup.pipeline import _auto_ligands, _ions_beside_a_supplied_ligand
from tests.test_heterogens import _atom, _structure

HIS = [
    _atom("ATOM", 1, "NE2", " ", "HIS", "A", 10, 10.0, 10, 10, element="N"),
    _atom("ATOM", 2, "NE2", " ", "HIS", "A", 11, 16.0, 10, 10, element="N"),
]


def _two_zincs_in_one_residue(x: float = 12.1) -> list[str]:
    return HIS + [
        _atom("HETATM", 3, "ZN1", " ", "ZN", "A", 401, x, 10, 10, element="ZN"),
        _atom("HETATM", 4, "ZN2", " ", "ZN", "A", 401, x + 2.0, 10, 10, element="ZN"),
    ]


class TestKeptItIsAQuestion:

    def test_it_is_refused_and_named(self) -> None:
        with pytest.raises(AmbiguousStructureError) as refused:
            resolve(_structure(_two_zincs_in_one_residue()))
        said = str(refused.value)
        assert "ZN A401 holds 2 atoms (ZN1, ZN2)" in said
        assert "an ion is one atom" in said
        assert refused.value.code == "setup.structure.undetermined"

    def test_from_a_local_file_it_is_not_sent_for_chemistry(self, tmp_path: Path) -> None:
        with pytest.raises(AmbiguousStructureError) as refused:
            _auto_ligands({"heterogens": "auto"},
                          _structure(_two_zincs_in_one_residue()), tmp_path, None)
        assert "PDB identifier" not in str(refused.value)
        assert "_ideal.sdf" not in str(refused.value)

    def test_from_an_entry_it_is_not_kept_as_it_stands(self, tmp_path: Path) -> None:
        params = {"heterogens": "auto"}
        with pytest.raises(AmbiguousStructureError):
            _auto_ligands(params, _structure(_two_zincs_in_one_residue()),
                          tmp_path, "1ABC")
        assert "_retained_pdb" not in params
        assert not (tmp_path / "retained.pdb").exists()

    def test_beside_a_supplied_ligand_it_is_refused_too(self, tmp_path: Path) -> None:
        with pytest.raises(AmbiguousStructureError, match="holds 2 atoms"):
            _ions_beside_a_supplied_ligand(
                {"heterogens": "auto"}, _structure(_two_zincs_in_one_residue()),
                tmp_path)


class TestOtherwiseAsBefore:

    def test_far_from_the_protein_it_is_discarded(self) -> None:
        """Nothing is kept, so there is nothing to ask."""
        [zinc] = [d for d in decide(_structure(_two_zincs_in_one_residue(x=40.0)))
                  if d.resname == "ZN"]
        assert zinc.action is Action.DISCARD

    def test_one_zinc_is_kept_as_an_ion(self, tmp_path: Path) -> None:
        lines = HIS + [_atom("HETATM", 3, "ZN", " ", "ZN", "A", 401, 12.1, 10, 10,
                             element="ZN")]
        params = {"heterogens": "auto"}
        assert _auto_ligands(params, _structure(lines), tmp_path, None) == []
        assert params["_retained_ions"] == ["ZN A401"]

    def test_two_zincs_in_two_residues_are_two_ions(self, tmp_path: Path) -> None:
        lines = HIS + [
            _atom("HETATM", 3, "ZN", " ", "ZN", "A", 401, 12.1, 10, 10, element="ZN"),
            _atom("HETATM", 4, "ZN", " ", "ZN", "A", 402, 14.1, 10, 10, element="ZN"),
        ]
        params = {"heterogens": "auto"}
        assert _auto_ligands(params, _structure(lines), tmp_path, None) == []
        assert params["_retained_ions"] == ["ZN A401", "ZN A402"]

    def test_one_zinc_at_two_locations_is_one_ion(self) -> None:
        lines = HIS + [
            _atom("HETATM", 3, "ZN", "A", "ZN", "A", 401, 12.1, 10, 10,
                  occupancy=0.7, element="ZN"),
            _atom("HETATM", 4, "ZN", "B", "ZN", "A", 401, 12.1, 10.4, 10,
                  occupancy=0.3, element="ZN"),
        ]
        [zinc] = [d for d in resolve(_structure(lines)) if d.resname == "ZN"]
        assert zinc.action is Action.SIMULATE and zinc.is_monatomic
