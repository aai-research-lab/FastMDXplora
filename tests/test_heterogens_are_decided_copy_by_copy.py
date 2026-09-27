"""A heterogen is decided by what its own records say about it.

Three decisions read the structure too coarsely:

- A sugar was a glycan if any sugar of its name was LINKed to an asparagine,
  and an inner sugar if the structure was glycosylated anywhere. A free NAG in
  an active site beside a glycosylated asparagine was discarded with the
  glycan, and a lactose bonded only to itself was discarded as one. The glycan
  is now followed from the protein, sugar by sugar, through the LINK records.
- An ion LINKed to anything was "coordinated by the protein", so a magnesium
  30 A from the protein and LINKed only to a water was kept.
- Every model of an NMR entry was read, so a zinc counted once per model and
  was not recognised as one ion.
"""

from __future__ import annotations

import pytest

from fastmdxplora.setup.heterogens import Action, AmbiguousStructureError, resolve
from tests.test_heterogens import PROTEIN, _atom, _structure

ASN = [_atom("ATOM", 4, "ND2", " ", "ASN", "A", 2, 3.0, 0, 0, element="N")]


def _decisions(lines):
    return {d.resname: d for d in resolve(_structure(lines))}


class TestSugars:

    def test_a_free_sugar_beside_a_glycan_is_still_a_question(self) -> None:
        lines = PROTEIN + ASN + [
            "LINK         ND2 ASN A   2                 C1  NAG A 400",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4.4, 0, 0),
            _atom("HETATM", 310, "C1", " ", "NAG", "A", 500, 20, 20, 20),
        ]
        with pytest.raises(AmbiguousStructureError) as refused:
            resolve(_structure(lines))
        said = str(refused.value)
        assert "NAG A400 is part of a glycan on ASN A2" in said
        assert "NAG A500 is bonded to no glycosylated residue" in said

    def test_a_disaccharide_bonded_only_to_itself_is_free(self) -> None:
        """Lactose in a structure whose asparagine carries a glycan."""
        lines = PROTEIN + ASN + [
            "LINK         ND2 ASN A   2                 C1  NAG A 400",
            "LINK         O4  GLC A 600                 C1  GAL A 601",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4.4, 0, 0),
            _atom("HETATM", 320, "C1", " ", "GLC", "A", 600, 20, 20, 20),
            _atom("HETATM", 321, "C1", " ", "GAL", "A", 601, 21.4, 20, 20),
        ]
        with pytest.raises(AmbiguousStructureError, match="free sugar"):
            resolve(_structure(lines))
        # The glycan itself is still settled.
        lines = [line for line in lines if "GLC" not in line and "GAL" not in line]
        assert _decisions(lines)["NAG"].action is Action.DISCARD

    def test_a_glycan_is_followed_to_its_last_sugar(self) -> None:
        lines = PROTEIN + ASN + [
            "LINK         ND2 ASN A   2                 C1  NAG A 400",
            "LINK         O4  NAG A 400                 C1  NAG A 401",
            "LINK         O4  NAG A 401                 C1  BMA A 402",
            "LINK         O3  BMA A 402                 C1  MAN A 403",
            _atom("HETATM", 300, "C1", " ", "NAG", "A", 400, 4.4, 0, 0),
            _atom("HETATM", 301, "C1", " ", "NAG", "A", 401, 5.8, 0, 0),
            _atom("HETATM", 302, "C1", " ", "BMA", "A", 402, 7.2, 0, 0),
            _atom("HETATM", 303, "C1", " ", "MAN", "A", 403, 8.6, 0, 0),
        ]
        decisions = _decisions(lines)
        for name in ("NAG", "BMA", "MAN"):
            assert decisions[name].action is Action.DISCARD, name
            assert "ASN A2" in decisions[name].reason


class TestIons:

    def test_an_ion_linked_only_to_a_water_is_not_held_by_the_protein(self) -> None:
        lines = PROTEIN + [
            "LINK        MG    MG A 700                 O   HOH A 701",
            _atom("HETATM", 400, "MG", " ", "MG", "A", 700, 30, 30, 30, element="MG"),
            _atom("HETATM", 401, "O", " ", "HOH", "A", 701, 32.1, 30, 30, element="O"),
        ]
        assert _decisions(lines)["MG"].action is Action.DISCARD

    def test_an_ion_linked_to_a_ligand_is_kept_and_says_so(self) -> None:
        lines = PROTEIN + [
            "LINK        O2G  GNP A 200                MG    MG A 201",
            _atom("HETATM", 400, "MG", " ", "MG", "A", 201, 30, 30, 30, element="MG"),
            _atom("HETATM", 410, "O2G", " ", "GNP", "A", 200, 32.0, 30, 30, element="O"),
        ]
        decision = _decisions(lines)["MG"]
        assert decision.action is Action.SIMULATE
        assert "LINK record to GNP" in decision.reason
        assert "by the protein" not in decision.reason

    def test_an_ion_linked_to_the_protein_is_named_as_such(self) -> None:
        lines = [
            _atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N"),
            "LINK         NE2 HIS A   1                ZN    ZN A 112",
            _atom("HETATM", 2, "ZN", " ", "ZN", "A", 112, 2.1, 0, 0, element="ZN"),
        ]
        reason = _decisions(lines)["ZN"].reason
        assert "coordinated by the protein" in reason and "HIS" in reason


def test_only_the_first_model_is_read() -> None:
    """Three models of a zinc finger: one zinc, kept as one ion."""
    lines = []
    for model in (1, 2, 3):
        lines += [f"MODEL     {model:>4}",
                  _atom("ATOM", 1, "SG", " ", "CYS", "A", 1, 0, 0, 0, element="S"),
                  _atom("HETATM", 2, "ZN", " ", "ZN", "A", 101, 2.3, 0, 0, element="ZN"),
                  "ENDMDL"]
    decision = _decisions(lines)["ZN"]
    assert decision.action is Action.SIMULATE
    assert [len(h.atoms) for h in decision.instances] == [1]
