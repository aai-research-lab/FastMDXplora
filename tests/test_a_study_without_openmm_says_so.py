"""A study run without OpenMM says what is missing, and nothing else.

Without the chemistry stack, `explore` fetched the structure, said "setup
complete" over a folder with no prepared system, then told the person to
"run setup first" before naming the package that was missing. Its advice
for analysis-only work, `--include analyze report`, is refused by the plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastmdxplora.dependencies import (
    CHEMISTRY_DEPENDENCIES,
    dependency_error_message,
)
from fastmdxplora.orchestrator import PHASES, PhaseResult, _how_it_ended
from fastmdxplora.simulation import pipeline as _pipeline


class TestTheMessage:

    def test_one_package_is_named_as_one(self) -> None:
        said = dependency_error_message(list(CHEMISTRY_DEPENDENCIES)[:1])
        assert "needs OpenMM, but it is not installed" in said
        assert "Install it in that same environment" in said

    def test_two_are_named_as_two(self) -> None:
        said = dependency_error_message(list(CHEMISTRY_DEPENDENCIES))
        assert "needs OpenMM and PDBFixer, but they are not installed" in said

    def test_the_advice_names_phases_the_plan_accepts(self) -> None:
        said = dependency_error_message(list(CHEMISTRY_DEPENDENCIES))
        advised = said.split("`--include-phase ")[1].split("`")[0].split()
        assert advised and set(advised) <= set(PHASES)


def _setup(artifacts, *, status="ok", taken_from="") -> PhaseResult:
    return PhaseResult(name="setup", status=status, output_dir=Path("setup"),
                       started_at="", finished_at="", message="why",
                       artifacts=list(artifacts), taken_from=taken_from)


class TestHowSetupIsShown:

    def test_no_system_prepared_is_a_warning(self) -> None:
        shown = _how_it_ended(_setup(["input.pdb", "setup_parameters.json"]))
        assert shown["status"] == "warning"
        assert "without preparing a system" in shown["message"]

    def test_a_prepared_system_is_complete(self) -> None:
        assert _how_it_ended(_setup(["system.xml", "state.xml"])) == {"status": "ok"}

    def test_a_system_taken_from_elsewhere_is_not_questioned(self) -> None:
        shown = _how_it_ended(_setup([], status="skipped", taken_from="earlier/setup"))
        assert shown == {"status": "skipped", "message": "setup skipped: why"}

    def test_other_phases_are_shown_as_recorded(self) -> None:
        result = PhaseResult(name="analysis", status="ok", output_dir=Path("a"),
                             started_at="", finished_at="")
        assert _how_it_ended(result) == {"status": "ok"}


def test_the_simulation_names_the_package_not_the_setup(tmp_path) -> None:
    orchestrator = MagicMock()
    orchestrator.output_dir = tmp_path
    presenter = MagicMock()
    orchestrator._presenter = presenter
    out = tmp_path / "simulation"
    out.mkdir()
    missing = list(CHEMISTRY_DEPENDENCIES)

    with patch.object(_pipeline, "missing_dependencies", return_value=missing):
        with pytest.raises(RuntimeError, match="needs OpenMM and PDBFixer"):
            _pipeline.run(orchestrator=orchestrator, output_dir=out)

    notes = json.loads((out / "simulation_parameters.json").read_text(encoding="utf-8"))["notes"]
    assert any("needs OpenMM and PDBFixer" in note for note in notes)
    assert not any("Run the setup phase first" in note for note in notes)
    said = " ".join(str(call) for call in presenter.step.call_args_list)
    assert "run setup first" not in said
