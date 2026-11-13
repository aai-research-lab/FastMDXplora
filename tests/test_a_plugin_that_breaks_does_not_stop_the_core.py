"""A plug-in that breaks does not stop the core.

An installed package adds to FastMDXplora through its entry points
(``fastmdxplora.plugins``). One of them failing to import, or offering
something that is not what the group takes, is left out and named with why;
everything else, the core's own analyses among them, is there as before.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types

import pytest

from tests._plugins import Offered, plugged  # noqa: F401

pytest.importorskip("mdtraj")

from fastmdxplora import plugins  # noqa: E402
from fastmdxplora.analysis import orchestrator  # noqa: E402
from fastmdxplora.analysis.base import Analysis  # noqa: E402


class Good(Analysis):
    name = "good_one"


class TestAPlugInThatBreaks:
    def test_one_that_fails_to_import_is_left_out_and_named(self, plugged, caplog):  # noqa: F811
        plugged({plugins.ANALYSES: [
            Offered("broken_one", ImportError("No module named 'gone'"),
                    package="lab-extras", value="lab_extras.broken:Broken"),
            Offered("good_one", Good, package="lab-extras"),
        ]})
        with caplog.at_level(logging.WARNING, logger="fastmdxplora"):
            loaded = plugins.load(plugins.ANALYSES)
        assert list(loaded) == ["good_one"]
        (failure,) = plugins.failures()
        assert (failure.name, failure.package) == ("broken_one", "lab-extras")
        assert "No module named 'gone'" in failure.reason
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "lab-extras" in said and "lab_extras.broken:Broken" in said
        assert "No module named 'gone'" in said

    def test_the_core_s_analyses_register_beside_it(self, plugged):  # noqa: F811
        before = orchestrator.available_analyses()
        plugged({plugins.ANALYSES: [
            Offered("broken_one", RuntimeError("its own fault")),
            Offered("good_one", Good),
        ]})
        orchestrator.register_plugged_in()
        after = orchestrator.available_analyses()
        assert [name for name in after if name != "good_one"] == list(before)
        assert orchestrator.get_analysis_class("good_one") is Good
        assert "broken_one" not in after

    def test_what_is_not_an_analysis_is_left_out(self, plugged):  # noqa: F811
        plugged({plugins.ANALYSES: [Offered("not_one", object())]})
        orchestrator.register_plugged_in()
        assert "not_one" not in orchestrator.available_analyses()
        (failure,) = plugins.failures()
        assert "not a subclass" in failure.reason

    def test_a_name_the_core_has_is_the_core_s(self, plugged):  # noqa: F811
        rmsd = orchestrator.get_analysis_class("rmsd")
        plugged({plugins.ANALYSES: [Offered("rmsd", Good, package="lab-extras")]})
        orchestrator.register_plugged_in()
        assert orchestrator.get_analysis_class("rmsd") is rmsd
        (failure,) = plugins.failures()
        assert failure.package == "lab-extras" and "already registered" in failure.reason

    def test_two_packages_offering_one_name(self, plugged):  # noqa: F811
        class Other(Analysis):
            name = "good_one"

        plugged({plugins.ANALYSES: [Offered("good_one", Other, package="b-extras"),
                                    Offered("good_one", Good, package="a-extras")]})
        assert plugins.load(plugins.ANALYSES) == {"good_one": Good}
        (failure,) = plugins.failures()
        assert failure.package == "b-extras" and "a-extras already offers" in failure.reason

    def test_an_install_whose_entry_points_cannot_be_read(self, monkeypatch, caplog):
        def unreadable(group):
            raise OSError("metadata unreadable")

        monkeypatch.setattr(plugins, "_entry_points", unreadable)
        plugins.forget()
        try:
            with caplog.at_level(logging.WARNING, logger="fastmdxplora"):
                assert plugins.load(plugins.ANALYSES) == {}
                assert plugins.offered(plugins.ANALYSES) == {}
            assert "metadata unreadable" in caplog.text
        finally:
            monkeypatch.undo()
            plugins.forget()

    def test_a_study_asking_for_what_broke_is_told_to_reinstall(self, plugged):  # noqa: F811
        plugged({plugins.ANALYSES: [
            Offered("lipid_order", ImportError("cannot import name 'x'"),
                    package="fastmdxplora-membrane")]}, without=("lipid_order",))
        orchestrator.register_plugged_in()
        with pytest.raises(KeyError) as refused:
            orchestrator.get_analysis_class("lipid_order")
        assert refused.value.code == "environment.plugin.broken"
        details = refused.value.refusal.details
        assert details["package"] == "fastmdxplora-membrane"
        assert details["install_command"] == (
            "pip install --force-reinstall fastmdxplora-membrane")
        assert "cannot import name 'x'" in refused.value.refusal.message


class TestAPlugInImportedBeforeTheCore:
    """A script that imports a plug-in's module first: the module imports the
    core's analyses, which read the entry points while that module is still
    half made."""

    def test_its_analysis_waits_and_is_not_a_failure(self, plugged, monkeypatch):  # noqa: F811
        half_made = types.ModuleType("lab_extras.good")
        half_made.__spec__ = types.SimpleNamespace(_initializing=True)
        monkeypatch.setitem(sys.modules, "lab_extras.good", half_made)
        plugged({plugins.ANALYSES: [
            Offered("good_one", Good, package="lab-extras", value="lab_extras.good:Good")]})
        orchestrator.register_plugged_in()
        assert plugins.failures() == ()
        assert plugins.waiting(plugins.ANALYSES)
        assert "good_one" not in orchestrator._REGISTRY

        half_made.__spec__._initializing = False
        assert "good_one" in orchestrator.available_analyses()
        assert not plugins.waiting(plugins.ANALYSES)
        assert plugins.failures() == ()

    @pytest.mark.parametrize("first", ["fastmdxplora_membrane.lipid_order",
                                       "fastmdxplora.analysis.lipid_order",
                                       "fastmdxplora_membrane"])
    def test_the_membrane_package_first_in_a_fresh_interpreter(self, first):
        pytest.importorskip("fastmdxplora_membrane")
        script = (
            f"import {first}\n"
            "from fastmdxplora import plugins\n"
            "from fastmdxplora.analysis.orchestrator import _REGISTRY, ANALYSIS_ORDER\n"
            "assert plugins.failures() == (), plugins.failures()\n"
            "assert tuple(_REGISTRY) == ANALYSIS_ORDER, tuple(_REGISTRY)\n")
        ran = subprocess.run([sys.executable, "-c", script], capture_output=True,
                             text=True, timeout=120)
        assert ran.returncode == 0, ran.stderr
