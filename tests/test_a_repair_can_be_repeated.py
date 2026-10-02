"""A repair that builds missing residues can be repeated from its seed.

PDBFixer minimises the atoms it builds, and where one is left within 0.13 nm
of another it runs short dynamics to separate them, from an integrator it
seeds only when asked. Setup never asked, so a structure with a gap could be
prepared two ways from one `setup.random_seed` (found 2026-10-02: a loop
built beside a ligand ended 0.7 to 2.7 Angstrom from it over six repairs).
The setup seed is now PDBFixer's.
"""

from __future__ import annotations

import pytest

from fastmdxplora.setup import pdbfix as pdbfix_module
from fastmdxplora.setup import pipeline
from tests.test_a_structure_is_repaired_once_around_its_ligands import (
    _deposition, _heavy, _loop,
)


def test_the_setup_seed_is_the_repair_s():
    params = {**pipeline.DEFAULTS, "_random_seed": 4242}
    assert pipeline._repair_arguments(params, "input.pdb")["seed"] == 4242


def test_one_seed_gives_one_structure(tmp_path, monkeypatch):
    pytest.importorskip("pdbfixer")
    from pdbfixer import PDBFixer

    monkeypatch.setattr(PDBFixer, "_downloadCCDDefinition", lambda self, name: None)
    restore = pipeline._one_cpu_thread()
    try:
        lines = [line for line in _deposition("1STP")
                 if not line.startswith(("HETATM", "CONECT", "END"))
                 and not (line.startswith("ATOM") and _loop(line))]
        gap = tmp_path / "gap.pdb"
        gap.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
        plain = tmp_path / "plain.pdb"
        pdbfix_module.fix_pdb_with_pdbfixer(str(gap), str(plain), ph=7.0, seed=1)
        where = _heavy(plain, _loop)
        # A ligand beside four of the atoms built there, so the next repairs
        # leave atoms too close and separate them with dynamics.
        site = [(x + 0.4, y, z) for x, y, z in where[len(where) // 2 - 2:len(where) // 2 + 2]]
        ligand = [f"HETATM{9001 + i:5d}  C{i + 1}  LIG B 901    {x:8.3f}{y:8.3f}{z:8.3f}"
                  "  1.00  0.00           C" for i, (x, y, z) in enumerate(site)]
        structure = tmp_path / "with_ligand.pdb"
        structure.write_text("\n".join(lines + ["TER"] + ligand) + "\nEND\n",
                             encoding="utf-8")
        built = []
        for attempt in range(2):
            out = tmp_path / f"prepared{attempt}.pdb"
            pdbfix_module.fix_pdb_with_pdbfixer(str(structure), str(out), ph=7.0,
                                                reinstated=("LIG",), seed=7)
            built.append(_heavy(out, _loop))
    finally:
        restore()
    assert built[0] == built[1]
