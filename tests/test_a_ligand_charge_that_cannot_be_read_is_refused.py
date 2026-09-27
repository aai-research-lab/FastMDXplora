"""A ligand whose formal charges cannot be summed to a whole number is refused.

The net charge was inferred from the file and carried as "unknown" when that
failed, and the check that stops a stated `ligand_net_charge` standing against
the file's own charges was skipped in exactly that case, so the stated number
went into the record unchecked. Formal charges in an SDF or MOL2 are whole
numbers: failing to sum them means the file was not read as the chemistry it
describes, and setup now says so.

OpenFF is not needed: the loader is given a stand-in for its Molecule class.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.setup import ligand as ligand_module


def _loader_returning(total_charge, monkeypatch):
    class Molecule:
        @staticmethod
        def from_file(path):
            return SimpleNamespace(total_charge=total_charge, name="")

    monkeypatch.setattr(ligand_module, "_import_openff", lambda: Molecule)


@pytest.fixture()
def sdf(tmp_path):
    path = tmp_path / "lig.sdf"
    path.write_text("stand-in\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("net_charge", [None, 1])
def test_a_charge_that_is_not_a_whole_number_is_refused(sdf, monkeypatch, net_charge) -> None:
    _loader_returning(SimpleNamespace(magnitude=0.4), monkeypatch)
    with pytest.raises(StudyError) as raised:
        ligand_module.load_ligand(sdf, name="BEN", net_charge=net_charge)
    found = refusal_of(raised.value)
    assert found.code == "setup.chemistry.charge_undetermined"
    assert "M  CHG" in found.message and "BEN" in found.message


def test_a_charge_that_cannot_be_read_at_all_is_refused(sdf, monkeypatch) -> None:
    class Unreadable:
        @property
        def magnitude(self):
            raise ValueError("no charge")

    _loader_returning(Unreadable(), monkeypatch)
    with pytest.raises(StudyError) as raised:
        ligand_module.load_ligand(sdf, name="BEN", net_charge=1)
    assert refusal_of(raised.value).code == "setup.chemistry.charge_undetermined"


def test_a_whole_number_is_taken_and_checked_as_before(sdf, monkeypatch) -> None:
    _loader_returning(SimpleNamespace(magnitude=1.0), monkeypatch)
    assert ligand_module.load_ligand(sdf, name="BEN", net_charge=1).name == "BEN"
    with pytest.raises(StudyError) as raised:
        ligand_module.load_ligand(sdf, name="BEN", net_charge=0)
    assert refusal_of(raised.value).code == "setup.chemistry.charge_contradicted"
