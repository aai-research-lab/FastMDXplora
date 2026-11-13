"""An analysis whose package is absent names the package.

The bilayer analyses come with ``fastmdxplora-membrane``. A study that asks
for one where that package is not installed is refused, wherever it asks,
with the package and the command that installs it
(``environment.plugin.missing``): it is not a misspelling, and the nearest
name is not what was meant.
"""

from __future__ import annotations

import logging

import pytest

from tests._plugins import plugged  # noqa: F401

md = pytest.importorskip("mdtraj")

from fastmdxplora import plugins  # noqa: E402
from fastmdxplora.analysis import orchestrator  # noqa: E402
from fastmdxplora.config.loader import ConfigError, validate_config  # noqa: E402
from fastmdxplora.refusals import StudyError, code_for  # noqa: E402

BILAYER = ("area_per_lipid", "bilayer_thickness", "lipid_order")
STUDY = {"systems": [{"system": "1UBQ"}]}


@pytest.fixture
def absent(plugged):  # noqa: F811
    plugged({}, without=BILAYER)


def _refused_for_the_package(refusal, name: str) -> None:
    assert refusal.code == "environment.plugin.missing"
    assert refusal.details["package"] == "fastmdxplora-membrane"
    assert refusal.details["feature"] == name
    assert refusal.details["install_command"] == "pip install fastmdxplora-membrane"
    assert "pip install fastmdxplora-membrane" in refusal.message
    assert "Did you mean" not in refusal.message


class TestAnAbsentPackageIsNamed:
    def test_in_a_config_s_list_of_analyses(self, absent):
        with pytest.raises(ConfigError) as refused:
            validate_config({**STUDY, "analysis": {"include": ["rmsd", "lipid_order"]}})
        _refused_for_the_package(refused.value.refusal, "lipid_order")
        assert refused.value.refusal.details["option"] == "analysis.include"

    def test_excluding_it_asks_for_what_is_so(self, absent):
        validate_config({**STUDY, "analysis": {"exclude": ["rmsd", "lipid_order"]}})
        planner = orchestrator.AnalysisOrchestrator.__new__(orchestrator.AnalysisOrchestrator)
        planner.ligand_resname = None
        planner.output_dir = "."
        planned = planner._build_plan(None, ["rmsd", "lipid_order"])
        assert "rmsd" not in planned and "lipid_order" not in planned and "rg" in planned

    def test_in_a_config_s_options(self, absent):
        with pytest.raises(ConfigError) as refused:
            validate_config({**STUDY, "analysis": {
                "options": {"area_per_lipid": {"stride": 2}}}})
        _refused_for_the_package(refused.value.refusal, "area_per_lipid")

    def test_by_the_registry(self, absent):
        with pytest.raises(KeyError) as refused:
            orchestrator.get_analysis_class("bilayer_thickness")
        _refused_for_the_package(refused.value.refusal, "bilayer_thickness")

    def test_by_the_plan(self, absent):
        planner = orchestrator.AnalysisOrchestrator.__new__(orchestrator.AnalysisOrchestrator)
        planner.ligand_resname = None
        planner.output_dir = "."
        with pytest.raises(StudyError) as refused:
            planner._build_plan(["lipid_order"], None)
        _refused_for_the_package(refused.value.refusal, "lipid_order")

    def test_a_name_no_package_has_is_still_a_misspelling(self, absent):
        with pytest.raises(ConfigError) as refused:
            validate_config({**STUDY, "analysis": {"include": ["lipid_ordr"]}})
        assert refused.value.code == "analysis.unknown"

    def test_the_codes_say_an_install_is_the_remedy(self):
        for code in ("environment.plugin.missing", "environment.plugin.broken"):
            assert code_for(code).kind == "environmental"
            assert code_for(code).disclosure == "action"
            assert not code_for(code).retryable

    def test_installed_it_is_not_refused(self):
        pytest.importorskip("fastmdxplora_membrane")
        assert orchestrator.not_installed("lipid_order") is None
        validate_config({**STUDY, "analysis": {"include": list(BILAYER)}})

    def test_every_name_it_brings_is_one_the_schema_offers(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        named = {name for (group, name) in plugins.FIRST_PARTY if group == plugins.ANALYSES}
        assert named == set(BILAYER) <= set(ANALYSIS_NAMES)
        assert named <= set(orchestrator.ANALYSIS_ORDER)


class TestABilayerWithoutItsAnalyses:
    def _bilayer(self):
        import numpy as np

        top = md.Topology()
        chain = top.add_chain()
        for _ in range(40):
            residue = top.add_residue("POPC", chain)
            top.add_atom("P", md.element.phosphorus, residue)
        traj = md.Trajectory(np.zeros((1, 40, 3), dtype="float32"), top)
        traj.unitcell_lengths = np.full((1, 3), 5.0, dtype="float32")
        traj.unitcell_angles = np.full((1, 3), 90.0, dtype="float32")
        return traj

    def test_a_default_plan_says_what_it_goes_without(self, absent, caplog):
        with caplog.at_level(logging.WARNING, logger="fastmdxplora"):
            orchestrator._say_what_a_bilayer_goes_without(self._bilayer())
        assert "area_per_lipid, bilayer_thickness, lipid_order are not among" in caplog.text
        assert "pip install fastmdxplora-membrane" in caplog.text

    def test_nothing_is_said_where_they_are_installed(self, caplog):
        pytest.importorskip("fastmdxplora_membrane")
        with caplog.at_level(logging.WARNING, logger="fastmdxplora"):
            orchestrator._say_what_a_bilayer_goes_without(self._bilayer())
        assert caplog.text == ""
