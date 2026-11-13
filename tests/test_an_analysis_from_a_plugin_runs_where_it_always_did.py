"""An analysis from a plug-in runs where it always did.

The bilayer analyses moved to a package of their own. With it installed a
study is as it was: the analyses have the names and the place in the order
they had, their modules import from the paths they had, and the core finds
them by entry point without importing the package itself.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from tests._plugins import Offered, plugged  # noqa: F401

pytest.importorskip("mdtraj")

from fastmdxplora import plugins  # noqa: E402
from fastmdxplora.analysis import orchestrator  # noqa: E402
from fastmdxplora.analysis.base import Analysis  # noqa: E402

SRC = Path(orchestrator.__file__).resolve().parents[1]

#: The order every analysis registered in while all of them were modules of
#: `fastmdxplora.analysis`, as `available_analyses()` gave it then.
AS_IT_WAS = (
    "image_check", "pmf", "metad_surface", "steered_work", "rmsd", "rmsf",
    "rg", "hbonds", "ss", "sasa", "dihedrals", "qvalue", "order_parameters",
    "bfactor_comparison", "thermodynamics", "rdf", "coordination_number",
    "end_to_end", "moments_of_inertia", "pair_distance", "area_per_lipid",
    "bilayer_thickness", "lipid_order", "cluster", "dimred", "water_sites",
    "ligand_rmsd", "ligand_rmsf", "pl_contacts", "pl_hbonds",
    "pl_interactions",
)


class TestTheOrderTheAnalysesRunIn:
    def test_it_is_the_order_they_always_ran_in(self):
        pytest.importorskip("fastmdxplora_membrane")
        assert orchestrator.available_analyses() == AS_IT_WAS
        assert orchestrator.ANALYSIS_ORDER == AS_IT_WAS

    def test_without_the_package_the_rest_keep_their_order(self, plugged):  # noqa: F811
        gone = ("area_per_lipid", "bilayer_thickness", "lipid_order")
        plugged({}, without=gone)
        orchestrator.register_plugged_in()
        assert orchestrator.available_analyses() == tuple(
            name for name in AS_IT_WAS if name not in gone)

    def test_an_analysis_from_elsewhere_runs_after_them_by_name(self, plugged):  # noqa: F811
        class Second(Analysis):
            name = "zz_second"

        class First(Analysis):
            name = "aa_first"

        plugged({plugins.ANALYSES: [Offered("zz_second", Second), Offered("aa_first", First)]})
        orchestrator.register_plugged_in()
        names = orchestrator.available_analyses()
        assert names[-2:] == ("aa_first", "zz_second")
        assert tuple(n for n in names if n in AS_IT_WAS) == tuple(
            n for n in AS_IT_WAS if n in names)

    def test_the_package_s_classes_are_the_ones_registered(self):
        membrane = pytest.importorskip("fastmdxplora_membrane.lipid_order")
        assert orchestrator.get_analysis_class("lipid_order") is membrane.LipidOrder
        assert membrane.LipidOrder.requires_bilayer


class TestTheModulesAtTheirOldPaths:
    @pytest.mark.parametrize("module", ["bilayer", "area_per_lipid",
                                        "bilayer_thickness", "lipid_order"])
    def test_an_old_path_is_the_module_that_moved(self, module):
        new = pytest.importorskip(f"fastmdxplora_membrane.{module}")
        assert importlib.import_module(f"fastmdxplora.analysis.{module}") is new

    def test_a_name_is_imported_from_an_old_path(self):
        pytest.importorskip("fastmdxplora_membrane")
        from fastmdxplora.analysis.lipid_order import LipidOrder
        from fastmdxplora_membrane import lipid_order

        from fastmdxplora.analysis import bilayer

        assert LipidOrder is lipid_order.LipidOrder
        assert bilayer.__name__ == "fastmdxplora_membrane.bilayer"

    def test_without_the_package_an_old_path_names_it(self, monkeypatch):
        import sys

        monkeypatch.setattr(plugins, "_entry_points", lambda group: [])
        monkeypatch.setattr(plugins, "_moved_modules", None)
        for name in [n for n in sys.modules if n.startswith("fastmdxplora.analysis.lipid_order")]:
            monkeypatch.delitem(sys.modules, name)
        with pytest.raises(ModuleNotFoundError) as refused:
            importlib.import_module("fastmdxplora.analysis.lipid_order")
        assert "fastmdxplora-membrane" in str(refused.value)
        assert "pip install fastmdxplora-membrane" in str(refused.value)

    def test_a_path_that_never_was_is_not_found_as_before(self):
        with pytest.raises(ModuleNotFoundError) as refused:
            importlib.import_module("fastmdxplora.analysis.no_such_analysis")
        assert "fastmdxplora-membrane" not in str(refused.value)


class TestTheCoreNeverImportsAPlugIn:
    def test_no_module_of_the_core_imports_the_membrane_package(self):
        importing = re.compile(r"^\s*(from|import)\s+fastmdxplora_membrane\b", re.M)
        found = [str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
                 if importing.search(path.read_text(encoding="utf-8"))]
        assert found == []

    def test_the_package_s_modules_register_nothing_themselves(self):
        package = pytest.importorskip("fastmdxplora_membrane")
        for path in Path(package.__file__).parent.glob("*.py"):
            assert "register_analysis" not in path.read_text(encoding="utf-8"), path.name


class TestACheckoutWhoseOwnPackageWasNeverInstalled:
    """A clone installed before the analyses moved has the core and not the
    package they moved to. It reads the package from `packages/`."""

    PYPROJECT = (
        '[project]\nname = "lab-extras"\ndynamic = ["version"]\n\n'
        '[project.entry-points."fastmdxplora.analyses"]\n'
        'lab_one = "lab_extras.one:LabOne"\n\n'
        '[project.entry-points."fastmdxplora.moved_modules"]\n'
        '"fastmdxplora.analysis.lab_one" = "lab_extras.one"\n\n'
        '[tool.setuptools_scm]\nroot = "../.."\n')

    @pytest.fixture
    def checkout(self, tmp_path, monkeypatch, plugged):  # noqa: F811
        import sys

        source = tmp_path / "lab-extras" / "src" / "lab_extras"
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "one.py").write_text(
            "from fastmdxplora.analysis.base import Analysis\n\n\n"
            "class LabOne(Analysis):\n    name = 'lab_one'\n", encoding="utf-8")
        (tmp_path / "lab-extras" / "pyproject.toml").write_text(
            self.PYPROJECT, encoding="utf-8")
        plugged({})
        monkeypatch.undo()
        monkeypatch.setattr(plugins, "_checkout_packages", lambda: tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        plugins.forget()
        yield tmp_path
        for name in [n for n in sys.modules if n.startswith("lab_extras")]:
            del sys.modules[name]
        sys.modules.pop("fastmdxplora.analysis.lab_one", None)
        plugins._read_from_the_checkout.pop("lab-extras", None)

    def test_its_analysis_is_registered_from_where_it_sits(self, checkout):
        orchestrator.register_plugged_in()
        cls = orchestrator.get_analysis_class("lab_one")
        assert cls.__module__ == "lab_extras.one"
        assert plugins.failures() == ()
        assert plugins.offered(plugins.ANALYSES)["lab_one"] == "lab-extras"

    def test_its_module_is_found_at_its_old_path(self, checkout):
        old = importlib.import_module("fastmdxplora.analysis.lab_one")
        assert old.__name__ == "lab_extras.one"

    def test_info_says_it_is_read_from_the_checkout(self, checkout):
        plugins.load(plugins.ANALYSES)
        entry = plugins.report()["lab-extras"]
        assert entry["state"] == "checkout"
        assert entry["install"] == f"pip install -e {checkout / 'lab-extras'}"

    def test_an_installed_copy_reads_no_checkout(self, monkeypatch):
        monkeypatch.setattr(plugins, "_checkout_packages", lambda: None)
        assert plugins._from_the_checkout(plugins.ANALYSES) == []

    def test_installed_the_installed_one_is_used(self):
        pytest.importorskip("fastmdxplora_membrane")
        from importlib.metadata import PackageNotFoundError, distribution

        try:
            distribution("fastmdxplora-membrane")
        except PackageNotFoundError:
            pytest.skip("fastmdxplora-membrane is read from the checkout here")
        assert plugins._from_the_checkout(plugins.ANALYSES) == []

    def test_what_is_read_is_what_the_package_declares(self):
        tomllib = pytest.importorskip("tomllib")
        packages = plugins._checkout_packages()
        if packages is None:
            pytest.skip("not run from a source checkout")
        for pyproject in sorted(packages.glob("*/pyproject.toml")):
            text = pyproject.read_text(encoding="utf-8")
            project = tomllib.loads(text)["project"]
            for group in plugins.GROUPS:
                assert plugins._declared(text, group) == (
                    project["name"], project.get("entry-points", {}).get(group, {}))
