"""A ligand in a structure given by PDB identifier needs no file.

For a structure named by identifier, `heterogens: auto` fetches each
ligand's chemistry from the entry, and discards crystallisation additives
and ions from the liquor by itself. Asked for benzene in T4 lysozyme L99A
(181L), the Agent's look said "BNZ, HED looks like a ligand and has no
chemistry": the advisory checked for an identifier in the path it was
given, and every caller gave it the file the identifier had been fetched
into. The Agent asked for a benzene file it did not need, and whether to
leave out an additive setup leaves out by itself. The builder said the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.advisories import advise

#: Three residues of protein, benzene, an additive and a chloride.
LINES = [
    "ATOM      1  N   ALA A   1      10.000  10.000  10.000  1.00  0.00           N",
    "ATOM      2  CA  ALA A   1      11.450  10.000  10.000  1.00  0.00           C",
    "ATOM      3  C   ALA A   1      12.000  11.420  10.000  1.00  0.00           C",
    "ATOM      4  O   ALA A   1      11.300  12.400  10.000  1.00  0.00           O",
    "ATOM      5  CB  ALA A   1      11.950   9.200  11.200  1.00  0.00           C",
    "ATOM      6  N   ALA A   2      13.320  11.520  10.000  1.00  0.00           N",
    "ATOM      7  CA  ALA A   2      13.950  12.830  10.000  1.00  0.00           C",
    "ATOM      8  C   ALA A   2      15.470  12.700  10.000  1.00  0.00           C",
    "ATOM      9  O   ALA A   2      16.050  11.620  10.000  1.00  0.00           O",
    "ATOM     10  CB  ALA A   2      13.450  13.630  11.200  1.00  0.00           C",
    "ATOM     11  N   ALA A   3      16.100  13.860  10.000  1.00  0.00           N",
    "ATOM     12  CA  ALA A   3      17.550  13.950  10.000  1.00  0.00           C",
    "ATOM     13  C   ALA A   3      18.100  15.370  10.000  1.00  0.00           C",
    "ATOM     14  O   ALA A   3      17.400  16.350  10.000  1.00  0.00           O",
    "ATOM     15  CB  ALA A   3      18.050  13.150  11.200  1.00  0.00           C",
    "ATOM     16  OXT ALA A   3      19.300  15.500  10.000  1.00  0.00           O",
] + [
    f"HETATM{17 + k:5d}  C{k + 1}  BNZ A 200    {x:8.3f}{y:8.3f}{14.000:8.3f}  1.00  0.00           C"
    for k, (x, y) in enumerate([(14.0, 8.0), (15.39, 8.0), (16.09, 9.2),
                                (15.39, 10.4), (14.0, 10.4), (13.3, 9.2)])
] + [
    f"HETATM{23 + k:5d}  {name:<3} HED A 300    {x:8.3f}{30.000:8.3f}{30.000:8.3f}  1.00  0.00           {element}"
    for k, (name, x, element) in enumerate([("C1", 30.0, "C"), ("O1", 31.4, "O"), ("C2", 32.8, "C"),
                                            ("S3", 34.6, "S"), ("S4", 36.6, "S"), ("C5", 38.4, "C"),
                                            ("C6", 39.8, "C"), ("O6", 41.2, "O")])
] + [
    "HETATM   31 CL    CL A 400      40.000  40.000  40.000  1.00  0.00          CL",
]


@pytest.fixture
def entry(tmp_path, monkeypatch) -> Path:
    """181L, as though fetched: the file an identifier is fetched into."""
    from fastmdxplora.gui import preview

    fetched = tmp_path / "cache" / "181L.pdb"
    fetched.parent.mkdir()
    fetched.write_text("\n".join(LINES) + "\nEND\n", encoding="utf-8")
    monkeypatch.setattr(preview, "_fetched", lambda pdb_id: fetched)
    monkeypatch.chdir(tmp_path)
    return fetched


class TestTheAdvice:
    STRUCTURE = {"ligand_resnames": ["BNZ", "HED"], "path": "/cache/181L.pdb"}

    def _said(self, structure):
        return [a.summary for a in advise(structure, {}) if a.setting == "ligand"]

    def test_a_file_is_told_it_needs_the_chemistry(self):
        assert self._said(self.STRUCTURE) == ["BNZ, HED look like ligands and have no chemistry."]
        assert self._said({**self.STRUCTURE, "ligand_resnames": ["BNZ"]}) == [
            "BNZ looks like a ligand and has no chemistry."]

    def test_an_entry_or_a_prepared_system_is_not(self):
        assert self._said({**self.STRUCTURE, "entry": "181L"}) == []
        assert self._said({**self.STRUCTURE, "prepared": True}) == []


def test_the_builder_does_not_ask_for_a_file(entry) -> None:
    from fastmdxplora.gui.preview import _as_given, given_by_identifier
    from fastmdxplora.structure_info import count_structure

    assert given_by_identifier("181l") == "181L" and given_by_identifier("x.pdb") is None
    counted = _as_given(count_structure(entry), "181L")
    assert counted["entry"] == "181L"
    assert [a for a in advise(counted, {}) if a.setting == "ligand"] == []


def test_the_agent_is_told_what_setup_does_with_each(entry) -> None:
    from fastmdxplora.agent.tools import Toolbox, _inspect_structure

    said = _inspect_structure(Toolbox(), {"system": "181L"})
    heterogens = next(line for line in said.splitlines() if line.startswith("what setup does"))
    assert "simulate BNZ" in heterogens
    assert "setup fetches its chemistry from the 181L entry, so no ligand file is needed" in heterogens
    assert "discard HED: crystallization additive" in heterogens
    assert "discard CL" in heterogens
    assert "has no chemistry" not in said and "have no chemistry" not in said

    local = _inspect_structure(Toolbox(), {"system": str(entry)})
    assert "a file has no entry to fetch its chemistry from" in local
    assert "have no chemistry" in local


def test_the_settings_say_where_the_chemistry_comes_from() -> None:
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    fields = {f.name: f for f in PHASE_SCHEMAS["setup"].fields}
    assert "fetches each ligand's chemistry from the entry" in fields["heterogens"].help
    assert "for one given by PDB identifier, setup fetches" in fields["ligand"].help
