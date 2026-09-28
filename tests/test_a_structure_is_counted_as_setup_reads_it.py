"""The structure summary counts the first model, as setup reads it.

Every model of a multi-model file was counted, so a 20-model NMR entry was
summarised in the GUI as twenty times its atoms and its ligand twenty times
over, while setup simulates the first model.
"""

from __future__ import annotations

from pathlib import Path

from fastmdxplora.structure_info import count_structure, ligand_atom_counts

MODEL = (
    "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
    "HETATM    3  C1  BNZ A 101       5.000   0.000   0.000  1.00  0.00           C\n"
)


def _models(tmp_path: Path, n: int) -> Path:
    path = tmp_path / f"models-{n}.pdb"
    if n == 1:
        path.write_text(MODEL + "END\n", encoding="utf-8")
    else:
        path.write_text("".join(f"MODEL     {i:>4}\n{MODEL}ENDMDL\n"
                                for i in range(1, n + 1)) + "END\n", encoding="utf-8")
    return path


def test_twenty_models_count_as_one(tmp_path: Path) -> None:
    one = count_structure(_models(tmp_path, 1))
    twenty = count_structure(_models(tmp_path, 20))
    assert twenty["atoms"] == one["atoms"] == 3
    assert twenty["protein_residues"] == one["protein_residues"] == 1
    assert (one["models"], twenty["models"]) == (1, 20)


def test_the_ligand_is_counted_once(tmp_path: Path) -> None:
    assert ligand_atom_counts(_models(tmp_path, 20)) == {"BNZ": 1}
