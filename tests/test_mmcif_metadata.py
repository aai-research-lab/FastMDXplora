"""Structure summaries read mmCIF atom_site columns, not PDB character offsets."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from fastmdxplora.advisories import advise
from fastmdxplora.structure_info import count_structure, ligand_atom_counts

# The mmCIF parser uses OpenMM's PdbxReader, which is only installed by the
# optional [md] extra. Keep the PDB-only cases below runnable in base installs.
_requires_openmm = pytest.mark.skipif(
    importlib.util.find_spec("openmm") is None,
    reason="mmCIF parsing requires OpenMM's PdbxReader",
)

_COLUMNS = (
    "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
    "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
    "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
    "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
    "pdbx_PDB_model_num",
)


def _atom(group: str, element: str, atom: str, residue: str, chain: str,
          sequence: str, model: str = "1") -> tuple[str, ...]:
    return (group, "1", element, atom, ".", residue, chain, "1", sequence,
            "?", "0.0", "0.0", "0.0", "1.0", "0.0", sequence,
            residue, chain, model)


def _write_cif(path: Path, atoms: list[tuple[str, ...]]) -> Path:
    path.write_text(
        "data_test\nloop_\n"
        + "".join(f"_atom_site.{column}\n" for column in _COLUMNS)
        + "".join(" ".join(atom) + "\n" for atom in atoms)
        + "#\n",
        encoding="utf-8",
    )
    return path


def _write_pdb(path: Path, atoms: list[tuple[str, ...]], *, models: bool = False) -> Path:
    lines = []
    model_ids = sorted({int(atom[-1]) for atom in atoms})
    for model_id in model_ids:
        if models:
            lines.append(f"MODEL     {model_id:4d}")
        for serial, atom in enumerate((row for row in atoms if int(row[-1]) == model_id), 1):
            group, _id, element, name, _alt, residue, chain, _entity, _label_seq = atom[:9]
            _ins, x, y, z, _occupancy, _bfactor, sequence = atom[9:16]
            record = "ATOM  " if group == "ATOM" else "HETATM"
            lines.append(
                f"{record}{serial:5d} {name:4s} {residue:>3s} {chain:1s}{int(sequence):4d}    "
                f"{float(x):8.3f}{float(y):8.3f}{float(z):8.3f}  1.00  0.00          {element:>2s}"
            )
        if models:
            lines.append("ENDMDL")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _counters(info: dict) -> dict:
    keys = ("models", "atoms", "protein_atoms", "hetatm_atoms", "water_atoms",
            "ion_atoms", "chains", "all_chains", "n_chains", "protein_residues",
            "water_residues", "ions", "ion_resnames", "ligand_residues_total",
            "ligand_resnames", "ligand_instances")
    return {key: info[key] for key in keys}


@_requires_openmm
def test_protein_only_mmcif_does_not_turn_atom_names_into_ligands_or_metals(tmp_path) -> None:
    atoms = [
        _atom("ATOM", "C", "CA", "ALA", "A", "1"),
        _atom("ATOM", "C", "CD", "ALA", "A", "1"),
        _atom("ATOM", "H", "HG", "ALA", "A", "1"),
        _atom("ATOM", "N", "N", "ALA", "A", "1"),
        _atom("ATOM", "O", "O", "ALA", "A", "1"),
        _atom("ATOM", "S", "SD", "ALA", "A", "1"),
    ]
    cif = _write_cif(tmp_path / "protein.cif", atoms)
    pdb = _write_pdb(tmp_path / "protein.pdb", atoms)

    info = count_structure(cif)
    assert info["valid"] is True
    assert _counters(info) == _counters(count_structure(pdb))
    assert info["protein_atoms"] == 6
    assert info["ligand_resnames"] == []
    assert info["ion_resnames"] == []
    assert ligand_atom_counts(cif) == ligand_atom_counts(pdb) == {}
    assert not {item.setting for item in advise(info) if item.setting in {"ligand", "forcefield"}}


@_requires_openmm
def test_genuine_ligand_ion_and_water_match_pdb_counters(tmp_path) -> None:
    atoms = [
        _atom("ATOM", "C", "CA", "ALA", "A", "1"),
        _atom("HETATM", "C", "C1", "BNZ", "A", "101"),
        _atom("HETATM", "Zn", "ZN", "ZN", "A", "201"),
        _atom("HETATM", "O", "O", "HOH", "W", "301"),
    ]
    cif = _write_cif(tmp_path / "mixture.cif", atoms)
    pdb = _write_pdb(tmp_path / "mixture.pdb", atoms)

    info = count_structure(cif)
    assert info["valid"] is True
    assert _counters(info) == _counters(count_structure(pdb))
    assert info["ligand_resnames"] == ["BNZ"]
    assert info["ion_resnames"] == ["ZN"]
    assert info["water_atoms"] == 1
    assert ligand_atom_counts(cif) == {"BNZ": 1}
    settings = {item.setting for item in advise(info)}
    assert {"ligand", "forcefield"} <= settings


@_requires_openmm
def test_mmcif_model_count_covers_all_models_but_metadata_uses_the_first(tmp_path) -> None:
    atoms = [
        _atom("ATOM", "C", "CA", "ALA", "A", "1", "1"),
        _atom("HETATM", "C", "C1", "BNZ", "A", "101", "1"),
        _atom("HETATM", "C", "C1", "LIG", "A", "102", "2"),
    ]
    cif = _write_cif(tmp_path / "ensemble.cif", atoms)
    pdb = _write_pdb(tmp_path / "ensemble.pdb", atoms, models=True)

    info = count_structure(cif)
    assert info["models"] == 2
    assert info["atoms"] == 2
    assert info["ligand_resnames"] == ["BNZ"]
    assert ligand_atom_counts(cif) == {"BNZ": 1}
    assert _counters(info) == _counters(count_structure(pdb))


@_requires_openmm
def test_mmcif_metadata_keeps_long_chain_and_residue_ids(tmp_path) -> None:
    cif = _write_cif(
        tmp_path / "long-ids.cif",
        [_atom("ATOM", "C", "CA", "ALA", "protein-chain-101", "10001"),
         _atom("HETATM", "C", "C1", "LONG_LIGAND", "ligand-chain-102", "20001")],
    )

    info = count_structure(cif)
    assert info["valid"] is True
    assert info["all_chains"] == ["ligand-chain-102", "protein-chain-101"]
    assert info["ligand_resnames"] == ["LONG_LIGAND"]
    assert ligand_atom_counts(cif) == {"LONG_LIGAND": 1}


@_requires_openmm
def test_invalid_mmcif_metadata_is_explicit(tmp_path) -> None:
    invalid = tmp_path / "invalid.cif"
    invalid.write_text("data_invalid\n_entry.id 1\n", encoding="utf-8")
    got = count_structure(invalid)
    assert got["valid"] is False
    assert got["reason"].startswith("parse-error:")
    assert ligand_atom_counts(invalid) == {}

    empty = _write_cif(tmp_path / "empty.cif", [])
    got = count_structure(empty)
    assert got["valid"] is False
    assert got["reason"].startswith("parse-error:")


def test_unavailable_mmcif_metadata_is_explicit(tmp_path, monkeypatch) -> None:
    from fastmdxplora.setup import mmcif

    cif = _write_cif(
        tmp_path / "missing-dependency.cif",
        [_atom("ATOM", "C", "CA", "ALA", "A", "1")],
    )

    def unavailable(_path):
        raise ModuleNotFoundError("openmm is unavailable")

    monkeypatch.setattr(mmcif, "_read_first_block", unavailable)
    got = count_structure(cif)
    assert got["valid"] is False
    assert got["reason"].startswith("dependency-unavailable:")
    assert ligand_atom_counts(cif) == {}


def test_mmcif_size_limit_is_reported_without_parsing(tmp_path) -> None:
    cif = _write_cif(tmp_path / "bounded.cif", [_atom("ATOM", "C", "CA", "ALA", "A", "1")])
    got = count_structure(cif, max_bytes=1)
    assert got["valid"] is False
    assert got["reason"] == "too-large"
    assert got["max_inline_bytes"] == 1


@pytest.mark.parametrize("changes", [
    dict.fromkeys(set(_COLUMNS) - {"label_comp_id"}),
    dict.fromkeys(set(_COLUMNS) - {"id"}),
    {"auth_asym_id": None, "label_asym_id": None},
    {"auth_comp_id": "?", "label_comp_id": "."},
    {"auth_seq_id": None, "label_seq_id": None},
    {"group_PDB": None},
    {"group_PDB": "?"},
    {"Cartn_x": None},
    {"Cartn_y": "?"},
    {"Cartn_z": "invalid"},
    {"Cartn_x": "nan"},
    {"Cartn_y": "inf"},
])
@_requires_openmm
def test_incomplete_atom_site_is_unavailable(tmp_path, changes) -> None:
    fields = dict(zip(_COLUMNS, _atom("HETATM", "C", "C1", "BNZ", "A", "101")))
    fields.update(changes)
    fields = {name: value for name, value in fields.items() if value is not None}
    path = tmp_path / "incomplete.cif"
    path.write_text("data_bad\nloop_\n"
                    + "".join(f"_atom_site.{name}\n" for name in fields)
                    + " ".join(fields.values()) + "\n#\n", encoding="utf-8")

    info = count_structure(path)
    assert info["valid"] is False
    assert info["reason"].startswith("parse-error:")
    assert ligand_atom_counts(path) == {}
    assert advise(info) == []


@pytest.mark.parametrize("source", ["auth", "label"])
@_requires_openmm
def test_mmcif_identity_fallbacks_need_no_unconsumed_fields(tmp_path, source) -> None:
    fields = dict(zip(_COLUMNS, _atom("HETATM", "C", "C1", "BNZ", "A", "101")))
    keep = {f"{source}_{field}" for field in ("asym_id", "comp_id", "seq_id")}
    keep.update(("group_PDB", "Cartn_x", "Cartn_y", "Cartn_z"))
    fields = {name: value for name, value in fields.items() if name in keep}
    path = tmp_path / "fallback.cif"
    path.write_text("data_fallback\nloop_\n"
                    + "".join(f"_atom_site.{name}\n" for name in fields)
                    + " ".join(fields.values()) + "\n#\n", encoding="utf-8")

    info = count_structure(path)
    assert info["valid"] is True
    assert info["all_chains"] == ["A"]
    assert info["ligand_instances"][0]["resi"] == "101"
    assert info["centroid_angstrom"] == [0.0, 0.0, 0.0]
    assert ligand_atom_counts(path) == {"BNZ": 1}


@_requires_openmm
def test_interleaved_first_model_ligand_count(tmp_path) -> None:
    from fastmdxplora.setup.mmcif import to_pdb_lines

    path = _write_cif(tmp_path / "interleaved.cif", [
        _atom("HETATM", "C", "C1", "BNZ", "A", "101", "1"),
        _atom("HETATM", "C", "C1", "BNZ", "A", "101", "2"),
        _atom("HETATM", "C", "C2", "BNZ", "A", "101", "1"),
    ])
    info = count_structure(path)
    assert info["models"] == 2
    assert info["atoms"] == len([line for line in to_pdb_lines(path)
                                 if line.startswith(("ATOM", "HETATM"))]) == 2
    assert ligand_atom_counts(path) == {"BNZ": 2}


@pytest.mark.parametrize("prefix,suffix,models,atoms", [
    ("", "", 1, 2),
    ("MODEL        1\n", "ENDMDL\nMODEL        2\n{body}ENDMDL\n", 2, 2),
    ("MODEL        1\n", "ENDMDL\n{body}", 1, 2),
    ("MODEL        1\n", "ENDMDL\nMODEL        2\nENDMDL\n", 2, 2),
    ("MODEL        1\nENDMDL\nMODEL        2\n", "ENDMDL\n", 2, 0),
    ("MODEL        1\nMODEL        1\n", "ENDMDL\n", 2, 2),
])
def test_pdb_endmdl_boundary_matches_main(tmp_path, prefix, suffix, models, atoms) -> None:
    rows = [_atom("ATOM", "C", "CA", "ALA", "A", "1"),
            _atom("HETATM", "C", "C1", "BNZ", "A", "101")]
    reference = _write_pdb(tmp_path / "reference.pdb", rows if atoms else [])
    body = _write_pdb(tmp_path / "body.pdb", rows).read_text().removesuffix("END\n")
    path = tmp_path / "boundary.pdb"
    path.write_text(prefix + body + suffix.format(body=body) + "END\n", encoding="utf-8")
    expected = count_structure(reference)
    expected.update(path=path.resolve().as_posix(), size=path.stat().st_size, models=models)

    assert count_structure(path) == expected
    assert count_structure(path)["atoms"] == atoms
    assert ligand_atom_counts(path) == ({"BNZ": 1} if atoms else {})
