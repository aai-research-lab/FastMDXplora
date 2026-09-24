"""A ligand needs something to compute its charges, and setup says so first.

Every ligand parameterized with an OpenFF or GAFF small-molecule force field
takes AM1-BCC partial charges, and the OpenFF toolkit computes them by
calling AmberTools' `sqm` (or OpenEye, where it is licensed). conda-forge's
openff-toolkit stopped depending on AmberTools and FastMDXplora never
declared it, so a fresh 2.5.7 install solvated a protein-ligand system and
then failed with the toolkit's own words: "No registered toolkits can provide
the capability assign_partial_charges ... am1bcc".

AmberTools is declared wherever the stack is, `fastmdx info` reports whether
anything can compute the charges, and setup refuses before it repairs or
solvates anything when nothing can. None of this needs the toolkit to run:
it is stood in for at the one function that asks it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fastmdxplora.refusals import Kind, refusal_of
from fastmdxplora.setup import ligand as ligand_module
from fastmdxplora.setup import pdbfix as pdbfix_module
from fastmdxplora.setup import pipeline
from fastmdxplora.setup import prepare as prepare_module

ROOT = Path(__file__).resolve().parents[1]
INSTALL = "conda install -c conda-forge ambertools"

TRIPEPTIDE = """\
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O
ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C
ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N
ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C
ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C
ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O
ATOM     10  N   ALA A   3       6.171   3.845   0.000  1.00  0.00           N
ATOM     11  CA  ALA A   3       7.623   3.845   0.000  1.00  0.00           C
ATOM     12  C   ALA A   3       8.174   5.265   0.000  1.00  0.00           C
ATOM     13  O   ALA A   3       7.416   6.235   0.000  1.00  0.00           O
ATOM     14  CB  ALA A   3       8.153   3.072  -1.199  1.00  0.00           C
ATOM     15  OXT ALA A   3       9.400   5.400   0.000  1.00  0.00           O
END
"""


def _setup(tmp_path: Path, monkeypatch, *, provider, **options):
    """Run setup with the charge provider decided, recording what ran.

    PDBFixer and preparation are stood in for so that reaching either is
    visible: the refusal has to come before both. Returns the refusal, or
    None, and the two stand-ins.
    """
    structure = tmp_path / "complex.pdb"
    structure.write_text(TRIPEPTIDE, encoding="utf-8")
    if callable(provider):
        monkeypatch.setattr(ligand_module, "am1bcc_provider", provider)
    else:
        monkeypatch.setattr(ligand_module, "am1bcc_provider", lambda: provider)
    orchestrator = MagicMock()
    orchestrator.system = str(structure)
    orchestrator._presenter = None
    setup_dir = tmp_path / "setup"
    setup_dir.mkdir()
    with patch.object(pdbfix_module, "fix_pdb_with_pdbfixer") as repair, \
            patch.object(prepare_module, "prepare_system",
                         return_value={}) as solvate:
        try:
            pipeline.run(orchestrator=orchestrator, output_dir=setup_dir,
                         **options)
        except ValueError as refused:
            return refused, repair, solvate
    return None, repair, solvate


def _ligand_file(tmp_path: Path) -> str:
    """Never read: the refusal comes before the ligand is loaded."""
    path = tmp_path / "BEN.sdf"
    path.write_text("BEN\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n",
                    encoding="utf-8")
    return str(path)


class TestSetupRefusesBeforeItPreparesAnything:

    def test_a_supplied_ligand_with_nothing_to_charge_it(
            self, tmp_path, monkeypatch) -> None:
        refused, repair, solvate = _setup(
            tmp_path, monkeypatch, provider=None,
            ligand=_ligand_file(tmp_path), ligand_name="BEN")

        assert refused is not None, "setup went ahead with no charge provider"
        refusal = refusal_of(refused)
        assert refusal.code == "setup.environment.charges_unavailable"
        assert refusal.kind == Kind.ENVIRONMENTAL
        assert refusal.details["install_command"] == INSTALL
        assert INSTALL in str(refused)
        assert "AM1-BCC" in str(refused) and "AmberTools" in str(refused)
        assert not repair.called, "PDBFixer ran before the refusal"
        assert not solvate.called, "the system was solvated before the refusal"

    def test_a_ligand_found_in_the_structure_is_held_to_the_same(
            self, tmp_path, monkeypatch) -> None:
        """The default path: `heterogens: auto` finds the ligand itself."""
        found = _ligand_file(tmp_path)

        def discovered(params, *_args):
            params["ligand_name"] = "BEN"
            return [found]

        monkeypatch.setattr(pipeline, "_auto_ligands", discovered)
        refused, repair, solvate = _setup(tmp_path, monkeypatch, provider=None)

        assert refusal_of(refused).code == "setup.environment.charges_unavailable"
        assert not repair.called and not solvate.called

    def test_gaff_takes_the_same_charges(self, tmp_path, monkeypatch) -> None:
        refused, _, solvate = _setup(
            tmp_path, monkeypatch, provider=None,
            ligand=_ligand_file(tmp_path), ligand_forcefield="gaff-2.2.20")

        refusal = refusal_of(refused)
        assert refusal.code == "setup.environment.charges_unavailable"
        assert refusal.details["forcefield"] == "gaff-2.2.20"
        assert not solvate.called

    def test_with_a_provider_setup_goes_on(self, tmp_path, monkeypatch) -> None:
        refused, _, solvate = _setup(
            tmp_path, monkeypatch, provider="AmberTools",
            ligand=_ligand_file(tmp_path), ligand_name="BEN")

        assert refused is None
        assert solvate.called

    def test_a_study_with_no_ligand_is_not_asked(self, tmp_path, monkeypatch) -> None:
        """A protein alone needs no charges from AmberTools, so an install
        without it prepares one exactly as before."""
        asked = []

        def provider():
            asked.append(True)
            return None

        refused, repair, solvate = _setup(
            tmp_path, monkeypatch, provider=provider, heterogens="drop")

        assert refused is None
        assert repair.called and solvate.called
        assert not asked

    def test_where_the_toolkit_itself_is_absent_its_own_refusal_stands(
            self, tmp_path, monkeypatch) -> None:
        """Loading the ligand refuses for that, naming the toolkit; saying
        only that AmberTools is missing would send somebody to install one
        package of two."""
        def no_toolkit():
            raise ImportError("No module named 'openff'")

        refused, _, solvate = _setup(
            tmp_path, monkeypatch, provider=no_toolkit,
            ligand=_ligand_file(tmp_path), ligand_name="BEN")

        assert refused is None and solvate.called

    def test_the_toolkits_own_install_line_brings_ambertools_too(
            self, monkeypatch) -> None:
        """So that the one command it gives leaves nothing for the refusal
        above to find."""
        monkeypatch.setitem(sys.modules, "openff.toolkit", None)
        with pytest.raises(ligand_module.LigandError) as refused:
            ligand_module._import_openff()
        assert ("conda install -c conda-forge openff-toolkit "
                "openmmforcefields ambertools") in str(refused.value)


class TestWhoComputesTheCharges:
    """`am1bcc_provider` asks the toolkit exactly what it asks itself."""

    @staticmethod
    def _toolkit(monkeypatch, *, ambertools: bool, openeye: bool) -> None:
        toolkits = types.ModuleType("openff.toolkit.utils.toolkits")
        toolkits.AmberToolsToolkitWrapper = type(
            "AmberToolsToolkitWrapper", (),
            {"is_available": staticmethod(lambda: ambertools)})
        toolkits.OpenEyeToolkitWrapper = type(
            "OpenEyeToolkitWrapper", (),
            {"is_available": staticmethod(lambda: openeye)})
        for name in ("openff", "openff.toolkit", "openff.toolkit.utils"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        monkeypatch.setitem(sys.modules, toolkits.__name__, toolkits)

    def test_ambertools_first(self, monkeypatch) -> None:
        self._toolkit(monkeypatch, ambertools=True, openeye=True)
        assert ligand_module.am1bcc_provider() == "AmberTools"

    def test_openeye_where_it_is_the_one(self, monkeypatch) -> None:
        self._toolkit(monkeypatch, ambertools=False, openeye=True)
        assert ligand_module.am1bcc_provider() == "OpenEye"

    def test_nothing_where_there_is_neither(self, monkeypatch) -> None:
        """What a fresh conda-forge install had: the toolkit, and no
        AmberTools."""
        self._toolkit(monkeypatch, ambertools=False, openeye=False)
        assert ligand_module.am1bcc_provider() is None

    @pytest.mark.parametrize("name, takes", [
        ("openff-2.2.1", True),
        ("openff_unconstrained-2.2.1", True),
        ("smirnoff99Frosst-1.1.0", True),
        ("gaff-2.2.20", True),
        ("espaloma-0.3.2", False),
    ])
    def test_which_force_fields_take_am1bcc(self, name, takes) -> None:
        assert ligand_module.takes_am1bcc_charges(name) is takes


class TestInfoSaysWhetherALigandCanBeCharged:

    @staticmethod
    def _probed(am1bcc):
        from fastmdxplora.cli.main import _BACKENDS

        found = {name: ("installed", "")
                 for _group, rows in _BACKENDS for _display, name, _hint in rows}
        found["am1bcc"] = am1bcc
        return found

    def _info(self, monkeypatch, capsys, am1bcc, *, as_json: bool) -> str:
        # The module, not the `main` function the package re-exports.
        cli = importlib.import_module("fastmdxplora.cli.main")
        monkeypatch.setattr(cli, "_probe_backends",
                            lambda names: self._probed(am1bcc))
        assert cli._cmd_info(argparse.Namespace(json=as_json)) == 0
        return capsys.readouterr().out

    def test_the_text_names_the_row_and_the_install_line(
            self, monkeypatch, capsys) -> None:
        printed = self._info(monkeypatch, capsys, ("missing", ""), as_json=False)
        row = next(line for line in printed.splitlines()
                   if "AM1-BCC charges" in line)
        assert "missing" in row and INSTALL in row
        assert "ready (proteins only)" in printed

    def test_and_names_the_provider_when_there_is_one(
            self, monkeypatch, capsys) -> None:
        printed = self._info(monkeypatch, capsys, ("installed", "AmberTools"),
                             as_json=False)
        row = next(line for line in printed.splitlines()
                   if "AM1-BCC charges" in line)
        assert "installed" in row and "AmberTools" in row

    def test_the_json_carries_it_for_fastmdx_remote(
            self, monkeypatch, capsys) -> None:
        printed = self._info(monkeypatch, capsys, ("missing", ""), as_json=True)
        entry = json.loads(printed)["backends"]["am1bcc"]
        assert entry["name"] == "AM1-BCC charges"
        assert entry["state"] == "missing"
        assert entry["install"] == INSTALL
        assert entry["needed"] == "to prepare a ligand"

    @pytest.mark.parametrize("available, expected", [
        (True, ["installed", "AmberTools"]),
        (False, ["missing", ""]),
    ])
    def test_the_probe_asks_the_same_function_setup_does(
            self, tmp_path, monkeypatch, available, expected) -> None:
        """In the probe's own process, against a toolkit written to disk."""
        from fastmdxplora.cli.main import _probe_backends

        utils = tmp_path / "openff" / "toolkit" / "utils"
        utils.mkdir(parents=True)
        for package in (utils.parents[1], utils.parent, utils):
            (package / "__init__.py").write_text("", encoding="utf-8")
        (utils / "toolkits.py").write_text(
            "class AmberToolsToolkitWrapper:\n"
            f"    is_available = staticmethod(lambda: {available})\n"
            "class OpenEyeToolkitWrapper:\n"
            "    is_available = staticmethod(lambda: False)\n",
            encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
            [str(tmp_path), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))

        assert list(_probe_backends(("am1bcc",))["am1bcc"]) == expected


class TestARemoteMachineIsHeldToIt:

    def test_a_machine_that_cannot_charge_a_ligand_is_not_ready(self) -> None:
        from fastmdxplora.remote.identity import CodeIdentity
        from fastmdxplora.remote.machines import Machine, readiness
        from fastmdxplora.remote.plan import backends_plan
        from fastmdxplora.remote.probe import Environment, Inspection

        names = ("openmm", "pdbfixer", "openff.toolkit", "openmmforcefields",
                 "rdkit", "propka")
        backends = {n: {"name": n, "state": "installed"} for n in names}
        backends["am1bcc"] = {"name": "AM1-BCC charges", "state": "missing",
                              "install": INSTALL}
        env = Environment("/e/fastmdx-9.9.9", "9.9.9")
        inspection = Inspection(os="Linux", arch="x86_64", internet="yes",
                                conda={"conda": "/c/bin/conda"},
                                environments=[env])
        machine = Machine("box", "2026-09-24T00:00:00Z", inspection,
                          {env.path: {"backends": backends}})

        verdict = readiness(machine, CodeIdentity("9.9.9"))
        assert not verdict.ready and "AM1-BCC charges" in verdict.summary
        plan = backends_plan(inspection, env.path, ["am1bcc"], "box")
        assert '"ambertools"' in plan.steps[0].command


class TestEveryDeclarationOfTheStackCarriesAmberTools:
    """Four places say what a full install is, and one of them lacking it is
    the failure this file is about."""

    def test_the_recipe_runs_with_it(self) -> None:
        recipe = yaml.safe_load(
            (ROOT / "recipes" / "fastmdxplora" / "recipe.yaml")
            .read_text(encoding="utf-8"))
        run = [str(entry).split()[0] for entry in recipe["requirements"]["run"]]
        assert "ambertools" in run
        scripts = " ".join(str(line) for test in recipe["tests"]
                           for line in test.get("script", []))
        assert "AmberToolsToolkitWrapper" in scripts, (
            "nothing in the recipe's tests would notice AmberTools missing: "
            "the toolkit imports without it")

    def test_the_environment_file_installs_it(self) -> None:
        loaded = yaml.safe_load((ROOT / "environment.yml").read_text(
            encoding="utf-8"))
        assert "ambertools" in [str(entry).split(">")[0].split("=")[0]
                                for entry in loaded["dependencies"]]

    def test_the_image_installs_it_and_charges_a_molecule(self) -> None:
        definition = (ROOT / "container" / "fastmdx.def").read_text(
            encoding="utf-8")
        install = definition[definition.index("micromamba install"):]
        assert "ambertools" in install[:install.index("micromamba clean")]
        assert '"am1bcc", toolkit_registry=AmberToolsToolkitWrapper()' in definition

    def test_the_remote_plan_knows_where_it_comes_from(self) -> None:
        from fastmdxplora.remote.machines import REQUIRED_BACKENDS
        from fastmdxplora.remote.plan import CONDA_PACKAGE

        assert "am1bcc" in REQUIRED_BACKENDS
        assert CONDA_PACKAGE["am1bcc"] == "ambertools"
