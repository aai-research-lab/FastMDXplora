"""An AI app runs a study's analysis or report again, once the person agrees.

`fastmdx mcp` started a study only into a folder never used, so an AI app
asked to add an analysis to a finished study could not. `run_phases_again`
runs the analysis, the report or both again in the study's folder by the
phase command with `--rerun`, under the workspace's start rule, asking the
person where the AI app can; a read-only server does not offer it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed, _made  # noqa: E402

from fastmdxplora.mcp.app import App  # noqa: E402
from fastmdxplora.mcp.tools import Context, ToolError, _run_phases_again  # noqa: E402
from fastmdxplora.mcp.workspace import Workspace  # noqa: E402


@pytest.fixture()
def workspace(tmp_path):
    _analysed(tmp_path / "space" / "study")
    return Workspace.at(tmp_path / "space")


def test_it_is_offered_only_where_runs_are(workspace):
    assert "run_phases_again" in [tool.name for tool in App(workspace).tools]
    assert "run_phases_again" not in [tool.name for tool in App(workspace, runs=False).tools]
    from fastmdxplora.mcp.app import _instructions

    assert "run_phases_again" in _instructions(workspace, True)
    assert "run_phases_again" not in _instructions(workspace, False)


def test_an_analysis_is_added_and_said_done(workspace):
    said = _run_phases_again(Context(workspace), {"study": "study", "phases": ["analysis"],
                                                  "analyses": ["rmsd", "rg"]})
    root = workspace.root / "study"
    assert said.startswith("Done on study: analysis and report run again.")
    assert "study/previous" in said
    assert _made(root) == ["rmsd", "rg"] and _made(root / "previous") == ["rmsd"]


def test_what_cannot_be_run_again_is_refused_as_the_software_says(workspace):
    with pytest.raises(ToolError) as caught:
        _run_phases_again(Context(workspace), {"study": "study", "phases": ["simulation"]})
    assert caught.value.code == "config.option.not_permitted"
    assert "setup_from" in str(caught.value)
    with pytest.raises(ToolError) as caught:
        _run_phases_again(Context(workspace), {"study": "study", "phases": ["analysis"],
                                               "analyses": ["rmsdd"]})
    assert caught.value.code == "analysis.unknown"
    with pytest.raises(ToolError, match="outside the workspace"):
        _run_phases_again(Context(workspace), {"study": "/elsewhere", "phases": ["report"]})


def test_the_person_is_asked_and_a_no_runs_nothing(workspace):
    from fastmdxplora.mcp import tools

    asked: list[str] = []

    class Call:
        cancelled = False

        def confirm(self, key, message, schema, *, bound_to):
            asked.append(message)
            return {"action": "decline"}

    context = Context(workspace, call=Call())
    before = (workspace.root / "study" / "analysis" / "analysis_manifest.json").read_bytes()

    said = tools._run_phases_again(context, {"study": "study", "phases": ["analysis"],
                                             "analyses": ["rg"]})

    assert said == "Not run: the person did not go ahead."
    assert asked and asked[0].startswith("Run analysis and report again on study?")
    assert "kept in previous/" in asked[0]
    after = (workspace.root / "study" / "analysis" / "analysis_manifest.json").read_bytes()
    assert after == before and not (workspace.root / "study" / "previous").exists()


class _Process:
    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


def _started(code, log=""):
    def run_again(self, phases, analyses=None, **_):
        self.process = _Process(code)
        (self.active_root / "exploration.log").write_text(log, encoding="utf-8")
        return {"ok": True, "pid": 4242}
    return run_again


def test_a_long_one_is_left_running_and_a_failed_one_said(workspace, monkeypatch):
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.mcp import tools

    monkeypatch.setattr(tools, "RECORDED_WITHIN_S", 0.3)
    monkeypatch.setattr(DashboardRuntime, "run_again", _started(None))
    going = _run_phases_again(Context(workspace), {"study": "study", "phases": ["report"]})
    assert going.startswith("Started on study (process 4242):")
    assert "closing the AI app does not stop it" in going

    monkeypatch.setattr(DashboardRuntime, "run_again", _started(1, "loading\nit broke here"))
    with pytest.raises(ToolError, match="it broke here"):
        _run_phases_again(Context(workspace), {"study": "study", "phases": ["report"]})


def test_a_cancelled_call_runs_nothing(workspace, monkeypatch):
    from fastmdxplora.gui.exploration import DashboardRuntime

    class Call:
        cancelled = True

        def confirm(self, *args, **kwargs):
            return None

    monkeypatch.setattr(DashboardRuntime, "run_again",
                        lambda *a, **k: pytest.fail("started after a cancel"))
    said = _run_phases_again(Context(workspace, call=Call()),
                             {"study": "study", "phases": ["report"]})
    assert said == "Not run: the call was cancelled."
