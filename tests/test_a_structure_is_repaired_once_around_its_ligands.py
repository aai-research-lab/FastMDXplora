"""A structure is repaired once, with its ligands in place.

A titratable ligand's pKa was computed in a repair of the deposition with
every heterogen kept, and `prepared.pdb` came from a second repair with the
ligands stripped. Building missing residues is most of a repair's time: on
5WYZ (TLR8, 79 residues missing in five gaps) each repair took about 1,000 s
on the one CPU thread setup runs on, and the corpus stopped it at 1800 s
(network validation job, 2026-10-02). The second repair also built its loops
with the ligand's site empty. Now the structure is repaired once, as it will
be simulated, with the ligands in place while what is missing is built; the
pKa is computed in that repair and `prepared.pdb` is the same repair with the
ligands taken out.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.refusals import StudyError, refusal_of
from fastmdxplora.setup import ligand as ligand_module
from fastmdxplora.setup import pdbfix as pdbfix_module
from fastmdxplora.setup import pipeline
from fastmdxplora.setup import prepare as prepare_module
from tests.test_a_ligand_needs_a_charge_provider import TRIPEPTIDE

ASSEMBLIES = Path(__file__).resolve().parent / "data" / "assemblies"


def _deposition(name: str) -> list[str]:
    return gzip.decompress((ASSEMBLIES / f"{name}.pdb.gz").read_bytes()).decode().splitlines()


def _heavy(path: Path, keep) -> list[tuple[float, float, float]]:
    return [(float(line[30:38]), float(line[38:46]), float(line[46:54]))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(("ATOM", "HETATM")) and keep(line)
            and line[76:78].strip() != "H"]


def _loop(line: str) -> bool:
    return line[21] == "A" and 47 <= int(line[22:26]) <= 50


def test_the_loop_is_built_with_the_ligand_in_place(tmp_path, monkeypatch):
    """Streptavidin (1STP) with residues 47 to 50 taken out, and a ligand
    beside the gap. PDBFixer keeps the atoms it builds from the atoms already
    there; the ligand is now among them, as the protein is, and the pKa and
    `prepared.pdb` read the one repair."""
    pytest.importorskip("pdbfixer")
    import numpy as np
    import openmm as mm
    from pdbfixer import PDBFixer

    # The ligand's hydrogens are not this test's; nothing is fetched.
    monkeypatch.setattr(PDBFixer, "_downloadCCDDefinition", lambda self, name: None)
    lines = [line for line in _deposition("1STP")
             if not line.startswith(("HETATM", "CONECT", "END"))
             and not (line.startswith("ATOM") and _loop(line))]
    anchors = np.array([(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                        for line in lines if line.startswith("ATOM")
                        and line[12:16] == " CA " and int(line[22:26]) in (46, 51)])
    middle = anchors.mean(axis=0)
    site = [tuple(middle + offset) for offset in
            ((0, 0, 0), (1.5, 0, 0), (0, 1.5, 0), (0, 0, 1.5))]
    ligand = [f"HETATM{9001 + i:5d}  C{i + 1}  LIG B 901    {x:8.3f}{y:8.3f}{z:8.3f}"
              "  1.00  0.00           C" for i, (x, y, z) in enumerate(site)]
    structure = tmp_path / "with_ligand.pdb"
    structure.write_text("\n".join(lines + ["TER"] + ligand) + "\nEND\n",
                         encoding="utf-8")

    seen: list[np.ndarray] = []
    minimize = mm.LocalEnergyMinimizer.minimize

    def watched(context, *args, **kwargs):
        state = context.getState(getPositions=True)
        seen.append(state.getPositions(asNumpy=True).value_in_unit(mm.unit.angstrom))
        return minimize(context, *args, **kwargs)

    monkeypatch.setattr(mm.LocalEnergyMinimizer, "minimize", staticmethod(watched))
    prepared, complex_pdb = tmp_path / "prepared.pdb", tmp_path / "complex.pdb"
    pdbfix_module.fix_pdb_with_pdbfixer(str(structure), str(prepared), ph=7.0,
                                        reinstated=("LIG",), complex_pdb=str(complex_pdb))

    # The ligand's atoms are in the system the built atoms are minimised in.
    assert seen, "nothing was built"
    first = seen[0]
    assert all(np.linalg.norm(first - np.array(atom), axis=1).min() < 1e-3 for atom in site)
    built = _heavy(prepared, _loop)
    assert len(built) > 20, "the gap was not built"
    assert not any(line.startswith("HETATM") and line[17:20] == "LIG"
                   for line in prepared.read_text(encoding="utf-8").splitlines())
    # The pKa reads the same repair, with the ligand in it.
    held = complex_pdb.read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in held if line.startswith("HETATM") and line[17:20] == "LIG"
               and line[76:78].strip() != "H") == len(site)
    assert np.allclose(_heavy(complex_pdb, _loop), built, atol=1e-3)


def _run_setup(tmp_path, monkeypatch, repair):
    """Setup as a structure with two copies of a titratable ligand runs it:
    each copy's pKa asks for the repaired complex, then the structure is
    prepared. The finding of the ligands and the solvation are stood in for."""
    structure = tmp_path / "complex.pdb"
    structure.write_text(TRIPEPTIDE, encoding="utf-8")
    sdf = tmp_path / "LIG.sdf"
    sdf.write_text("LIG\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n",
                   encoding="utf-8")
    asked: list[Path] = []

    def found(params, input_pdb, setup_dir, entry_id):
        params["_reinstated_heterogens"] = ("LIG",)
        params["_explained_heterogens"] = ("LIG",)
        for _ in range(2):
            asked.append(Path(pipeline._repaired_complex(params, input_pdb, setup_dir)))
        params["ligand_name"] = "LIG"
        return [str(sdf)]

    monkeypatch.setattr(pipeline, "_auto_ligands", found)
    monkeypatch.setattr(ligand_module, "am1bcc_provider", lambda: "AmberTools")
    orchestrator = MagicMock()
    orchestrator.system = str(structure)
    orchestrator._presenter = None
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir()
    with patch.object(pdbfix_module, "fix_pdb_with_pdbfixer", side_effect=repair), \
            patch.object(prepare_module, "prepare_system", return_value={}):
        pipeline.run(orchestrator=orchestrator, output_dir=setup_dir)
    return setup_dir, asked


def test_setup_repairs_once_for_the_pka_and_the_structure(tmp_path, monkeypatch):
    calls: list[dict] = []

    def repair(input_pdb, output_pdb, *, complex_pdb=None, **arguments):
        calls.append({"output": output_pdb, "complex": complex_pdb, **arguments})
        Path(output_pdb).write_text(TRIPEPTIDE, encoding="utf-8")
        if complex_pdb:
            Path(complex_pdb).write_text(TRIPEPTIDE, encoding="utf-8")
        return []

    setup_dir, asked = _run_setup(tmp_path, monkeypatch, repair)
    assert len(calls) == 1
    assert calls[0]["output"] == str(setup_dir / "prepared.pdb")
    assert calls[0]["complex"] == str(setup_dir / "complex_for_pka.pdb")
    assert calls[0]["reinstated"] == ("LIG",)
    assert asked == [setup_dir / "complex_for_pka.pdb"] * 2


def test_a_repair_that_fails_leaves_the_structure_to_its_own(tmp_path, monkeypatch):
    calls: list[str | None] = []

    def repair(input_pdb, output_pdb, *, complex_pdb=None, **arguments):
        calls.append(complex_pdb)
        if complex_pdb:
            raise RuntimeError("the complex could not be repaired")
        Path(output_pdb).write_text(TRIPEPTIDE, encoding="utf-8")
        return []

    setup_dir, asked = _run_setup(tmp_path, monkeypatch, repair)
    # Tried once for both copies; the pKa reads the deposition, and the
    # structure is then repaired on its own.
    assert calls == [str(setup_dir / "complex_for_pka.pdb"), None]
    assert asked == [setup_dir / "input.pdb"] * 2
    assert (setup_dir / "prepared.pdb").is_file()


def test_a_refusal_in_the_repair_is_raised(tmp_path, monkeypatch):
    def repair(*_args, **_kwargs):
        raise StudyError("Mutation L2A names a residue that is not there.",
                         code="setup.structure.chain_unknown")

    with pytest.raises(StudyError) as refused:
        _run_setup(tmp_path, monkeypatch, repair)
    assert refusal_of(refused.value).code == "setup.structure.chain_unknown"


def test_the_ligands_are_known_before_the_repair(tmp_path, monkeypatch):
    """Trypsin (3PTB): benzamidine is reported as re-added, and the charges
    are checked, before the first pKa repairs the structure."""
    structure = tmp_path / "3PTB.pdb"
    structure.write_text("\n".join(_deposition("3PTB")) + "\n", encoding="utf-8")
    params = {**pipeline.DEFAULTS}
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir()

    def fetched(*_args, **_kwargs):
        raise AssertionError("chemistry was fetched before the charges were checked")

    from fastmdxplora.setup import ccd

    monkeypatch.setattr(ccd, "fetch_chemistry", fetched)
    monkeypatch.setattr(ligand_module, "am1bcc_provider", lambda: None)
    with pytest.raises(StudyError) as refused:
        pipeline._auto_ligands(params, structure, setup_dir, "3PTB")
    assert refusal_of(refused.value).code == "setup.environment.charges_unavailable"
    assert params["_reinstated_heterogens"] == ("BEN",)
    assert "CA" in params["_explained_heterogens"]
    # The ion is kept in place, and the ligand beside it until the repair
    # takes it out.
    retained = Path(params["_retained_pdb"]).read_text(encoding="utf-8")
    assert " BEN A   1" in retained and " CA  " in retained


def test_a_ligand_written_in_its_chain_does_not_make_a_terminus_a_loop():
    from types import SimpleNamespace

    from fastmdxplora.setup.pdbfix import _drop_terminal_extensions

    residues = [SimpleNamespace(name="ALA")] * 100 + [SimpleNamespace(name="BEN")]
    chain = SimpleNamespace(residues=lambda: iter(residues))
    fixer = SimpleNamespace(topology=SimpleNamespace(chains=lambda: iter([chain])),
                            missingResidues={(0, 40): ["GLY"], (0, 100): ["LEU"] * 9})
    _drop_terminal_extensions(fixer, ignoring=frozenset({"BEN"}))
    assert fixer.missingResidues == {(0, 40): ["GLY"]}


def test_a_complex_without_its_hydrogens_still_serves(tmp_path, monkeypatch, caplog):
    pytest.importorskip("pdbfixer")
    from pdbfixer import PDBFixer

    structure = tmp_path / "peptide.pdb"
    structure.write_text(TRIPEPTIDE, encoding="utf-8")
    fixer = PDBFixer(filename=str(structure))

    def refused(self, *args, **kwargs):
        raise ValueError("no hydrogens for this residue")

    monkeypatch.setattr(PDBFixer, "addMissingHydrogens", refused)
    target = tmp_path / "complex.pdb"
    with caplog.at_level("WARNING"):
        pdbfix_module._write_complex(fixer, target, 7.4)
    assert "reads the repaired heavy atoms" in caplog.text
    assert sum(1 for line in target.read_text(encoding="utf-8").splitlines()
               if line.startswith("ATOM")) == 15
