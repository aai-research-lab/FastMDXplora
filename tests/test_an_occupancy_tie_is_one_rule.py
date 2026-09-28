"""A tie in occupancy is decided by one rule, wherever it is found.

Two readings of one atom, or of one molecule, at occupancies too close to
prefer one are a question for the person, not a choice to make for them.
Three places read occupancy and did not agree:

- An ion at alternate locations inside one residue took the first where
  they tied, and the higher one however slight the lead, while the same tie
  between two residues stopped.
- The 0.10 tolerance was compared in floating point, so 0.55 against 0.45
  (a difference of 0.10000000000000003) resolved and 0.60 against 0.50
  (0.09999999999999998) was a tie, though both differ by 0.10.
"""

from __future__ import annotations

import pytest

from fastmdxplora.setup.heterogens import Action, AmbiguousStructureError, resolve
from tests.test_heterogens import _atom, _structure

HIS = [_atom("ATOM", 1, "NE2", " ", "HIS", "A", 1, 0, 0, 0, element="N")]


def _one_ion_at_two_locations(first: float, second: float):
    return HIS + [
        _atom("HETATM", 2, "ZN", "A", "ZN", "A", 101, 2.1, 0, 0, occupancy=first, element="ZN"),
        _atom("HETATM", 3, "ZN", "B", "ZN", "A", 101, 2.1, 0.4, 0, occupancy=second, element="ZN"),
    ]


def _two_ions_in_one_site(first: float, second: float):
    return HIS + [
        _atom("HETATM", 2, "ZN", " ", "ZN", "A", 101, 2.1, 0, 0, occupancy=first, element="ZN"),
        _atom("HETATM", 3, "ZN", " ", "ZN", "A", 102, 2.1, 0.4, 0, occupancy=second, element="ZN"),
    ]


def _ligand_in_two_conformations(first: float, second: float):
    return HIS + [
        _atom("HETATM", 10, "C1", "A", "BNZ", "A", 201, 9, 9, 9, occupancy=first),
        _atom("HETATM", 11, "C1", "B", "BNZ", "A", 201, 9, 9.5, 9, occupancy=second),
    ]


SHAPES = {"one ion, two locations": _one_ion_at_two_locations,
          "two ions, one site": _two_ions_in_one_site,
          "a ligand, two conformations": _ligand_in_two_conformations}


@pytest.mark.parametrize("shape", SHAPES)
def test_a_tie_stops_everywhere(shape) -> None:
    with pytest.raises(AmbiguousStructureError):
        resolve(_structure(SHAPES[shape](0.50, 0.50)))


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("occupancies", [(0.55, 0.45), (0.60, 0.50), (0.70, 0.30)])
def test_a_lead_of_the_tolerance_or_more_resolves_everywhere(shape, occupancies) -> None:
    decisions = resolve(_structure(SHAPES[shape](*occupancies)))
    assert all(d.action is not Action.STOP for d in decisions)


@pytest.mark.parametrize("shape", SHAPES)
def test_a_lead_under_it_is_still_a_tie(shape) -> None:
    with pytest.raises(AmbiguousStructureError):
        resolve(_structure(SHAPES[shape](0.54, 0.46)))


def test_the_ion_that_resolves_is_the_major_location() -> None:
    [zinc] = [d for d in resolve(_structure(_one_ion_at_two_locations(0.40, 0.60)))
              if d.resname == "ZN"]
    [atom] = zinc.instances[0].atoms
    assert atom.altloc == "B" and atom.occupancy == pytest.approx(0.60)
