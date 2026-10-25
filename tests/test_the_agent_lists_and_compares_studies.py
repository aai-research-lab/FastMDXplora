"""The Agent lists and compares studies as an AI app does, in one set of words.

An AI app (`fastmdx mcp`) could list the studies in a workspace and compare
two of them; the Agent, asked "which of my runs was longer?" or "how do
these two differ?", could only read one study at a time and compare means
itself, which is the one thing it is told never to do. Now both list and
compare through :mod:`fastmdxplora.workspace_studies`, so the answers are
the same, a study linked out of the workspace is left out of both, and a
difference is called resolved by the software, not by an AI model.
"""

from __future__ import annotations

import argparse
import types

import pytest

from fastmdxplora.agent.tools import Toolbox
from fastmdxplora.mcp.workspace import Workspace
from tests._mcp_wire import Wire, text_of
from tests.test_an_ai_app_reads_and_checks_studies import _structure, _study


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    root.mkdir()
    _structure(root / "ghg.pdb")
    (root / "ghg.yml").write_text("systems:\n  - system: ghg.pdb\n", encoding="utf-8")
    _study(root / "ubq_10ns", duration=10, means={"rmsd": (0.1234, 0.0056)},
           started="2026-09-02T10:00:00+00:00")
    _study(root / "ubq_20ns", duration=20, means={"rmsd": (0.1500, 0.0040)},
           started="2026-09-03T10:00:00+00:00")
    return root


def _mcp(root, tool, **arguments) -> str:
    from fastmdxplora.mcp.app import App

    wire = Wire(App(Workspace.at(root)).server())
    try:
        return text_of(wire.request("tools/call", {"name": tool, "arguments": arguments}))
    finally:
        wire.close()


def _box(root) -> Toolbox:
    workspace = Workspace.at(root)
    return Toolbox(path_for=workspace.path_for, workspace=workspace)


def test_the_agent_lists_the_studies_as_an_ai_app_does(workspace):
    look = _box(workspace).use("list_studies", {})
    assert look.ok
    assert look.said == _mcp(workspace, "list_studies")
    assert look.said.splitlines()[1].startswith("- ubq_20ns: 1UBQ, ")
    assert "    RMSD: 0.1500 ± 0.0040 nm" in look.said.splitlines()


def test_the_agent_compares_two_studies_as_an_ai_app_does(workspace):
    look = _box(workspace).use("compare_studies", {"first": "ubq_10ns", "second": "ubq_20ns"})
    assert look.ok
    assert look.said == _mcp(workspace, "compare_studies", first="ubq_10ns", second="ubq_20ns")
    assert "  simulation.duration_ns: 10 -> 20" in look.said
    assert "+0.0266 ± 0.0069 nm, resolved" in look.said


def test_a_tag_keeps_the_studies_tagged_so(workspace):
    from fastmdxplora.study_tags import add_tags

    add_tags(workspace / "ubq_10ns", ["baseline"])
    said = _box(workspace).use("list_studies", {"tag": "baseline"}).said
    assert said.startswith("1 study in ") and "tagged 'baseline'" in said
    assert "ubq_20ns" not in said


def test_a_study_linked_out_of_the_workspace_is_not_read(workspace):
    secret = workspace.parent / "secret.txt"
    secret.write_text("API_KEY=not-for-the-model")
    study = workspace / "ubq_10ns"
    (study / "resolved_config.yml").unlink()
    (study / "resolved_config.yml").symlink_to(secret)
    box = _box(workspace)
    for tool, asked in (("read_study", {"study": "ubq_10ns"}),
                        ("compare_studies", {"first": "ubq_10ns", "second": "ubq_20ns"})):
        look = box.use(tool, asked)
        assert not look.ok
        assert "links out of the workspace" in look.said
        assert "not-for-the-model" not in look.said
    assert "ubq_10ns" not in box.use("list_studies", {}).said


def test_with_no_folder_of_studies_the_list_says_so():
    look = Toolbox().use("list_studies", {})
    assert not look.ok and "no folder of studies here to list" in look.said


def test_a_comparison_names_each_study_or_is_refused(workspace):
    look = _box(workspace).use("compare_studies", {"first": "ubq_10ns"})
    assert not look.ok and "Name the study as `second`" in look.said


def test_the_home_folder_and_the_top_are_not_folders_of_studies(tmp_path, monkeypatch):
    from pathlib import Path

    from fastmdxplora.workspace_studies import folder_of_studies

    monkeypatch.setenv("HOME", str(tmp_path))
    assert folder_of_studies(tmp_path) is None
    assert folder_of_studies(Path(tmp_path.anchor)) is None
    assert folder_of_studies(None) is None
    (tmp_path / "studies").mkdir()
    assert folder_of_studies(tmp_path / "studies").root == (tmp_path / "studies").resolve()


def test_the_command_line_agent_lists_the_folder_it_is_run_in(workspace, monkeypatch, capsys):
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage

    monkeypatch.chdir(workspace)
    replies = iter([Turn("", (ToolCall("c1", "list_studies", {}),), Usage(1, 10, 0, 0, 5)),
                    Turn("There are two.", (), Usage(1, 10, 0, 0, 5))])

    def complete(prompt):  # pragma: no cover - the tool path is taken
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: next(replies)
    import fastmdxplora.agent as agent_module

    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    from fastmdxplora.cli.main import _run_agent

    _run_agent(argparse.Namespace(request="which studies are there?", request_file=None,
                                  agent_mode="assisted", phases="setup,simulation",
                                  attempts=None, agent_output=None, budget_hours=None))
    out = capsys.readouterr().out
    assert "2 studies in " in out


def test_the_gui_agent_lists_where_new_studies_go(workspace, monkeypatch):
    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from fastmdxplora.refusals import StudyError

    given = {}

    def propose(request, complete, **kw):
        given["tools"] = kw["tools"]
        raise StudyError("stopped here")

    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: object())
    monkeypatch.setattr(agent_module, "propose_config", propose)
    runtime = types.SimpleNamespace(exploration_root=str(workspace))
    propose_endpoint({"request": "which studies are there?"}, runtime)
    assert given["tools"].workspace.root == workspace.resolve()
    assert given["tools"].use("list_studies", {}).said.startswith("2 studies in ")


def test_a_study_listed_is_read_by_the_name_listed_wherever_it_was_started(
        workspace, tmp_path, monkeypatch):
    # The GUI, not hosted, has no rule for paths: a name list_studies gave,
    # relative to the folder of studies, was read from where it was started.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    box = Toolbox(workspace=Workspace.at(workspace))
    assert "- ubq_10ns: " in box.use("list_studies", {}).said
    assert box.use("read_study", {"study": "ubq_10ns"}).ok
    assert box.use("compare_studies", {"first": "ubq_10ns", "second": "ubq_20ns"}).ok


def test_a_study_whose_tags_link_out_is_not_listed(workspace):
    secret = workspace.parent / "secret.json"
    secret.write_text('{"tags": ["x"], "note": "SECRET read from outside"}')
    (workspace / "ubq_10ns" / "study_tags.json").symlink_to(secret)
    for said in (_box(workspace).use("list_studies", {}).said, _mcp(workspace, "list_studies")):
        assert "SECRET" not in said and "ubq_10ns" not in said
        assert "ubq_20ns" in said
