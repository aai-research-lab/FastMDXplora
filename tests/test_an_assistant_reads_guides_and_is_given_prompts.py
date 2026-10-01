"""What an assistant can read from `fastmdx mcp`, and what the person can ask it.

Two guides (how to work with studies here, and the config language as the
Agent is shown it) and each study's record are resources; four prompts start
a piece of work the way FastMDXplora does it, a study's record going with
the prompt that is about one.
"""

from __future__ import annotations

import pytest

from fastmdxplora.gui.workspace import RESOLVED_AT
from fastmdxplora.mcp import App, Workspace
from tests._mcp_wire import Wire
from tests.test_an_assistant_reads_and_checks_studies import _study


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    root = tmp_path / "work"
    _study(root / "project" / "ubq 10ns", duration=10, means={"rmsd": (0.1234, 0.0056)},
           started="2026-09-02T10:00:00+00:00")
    return root


@pytest.fixture
def wire(workspace):
    wire = Wire(App(Workspace.at(workspace)).server())
    yield wire
    wire.close()


class TestResources:
    def test_the_guides_and_each_study_are_listed(self, wire):
        result = wire.request("resources/list")["result"]
        assert (result["cacheScope"], result["ttlMs"]) == ("private", 5000)
        uris = [r["uri"] for r in result["resources"]]
        assert uris == ["fastmdxplora://guide/working-with-studies",
                        "fastmdxplora://guide/config-language",
                        "fastmdxplora://study/project/ubq%2010ns"]
        study = result["resources"][2]
        assert study["name"] == "project/ubq 10ns" and study["title"] == "1UBQ: project/ubq 10ns"
        templates = wire.request("resources/templates/list")["result"]
        assert templates["resourceTemplates"][0]["uriTemplate"] == \
            "fastmdxplora://study/{+path}"

    def test_a_guide_is_read_and_keeps_for_an_hour(self, wire):
        result = wire.request("resources/read", {
            "uri": "fastmdxplora://guide/working-with-studies"})["result"]
        assert (result["cacheScope"], result["ttlMs"]) == ("public", 3_600_000)
        text = result["contents"][0]["text"]
        assert text.startswith("# Working with FastMDXplora studies")
        assert RESOLVED_AT == 2.0 and "exceeds\n  twice its combined standard error" in text
        language = wire.request("resources/read", {
            "uri": "fastmdxplora://guide/config-language"})["result"]["contents"][0]["text"]
        assert "duration_ns" in language and "resume_from" in language

    def test_a_study_is_read_as_its_record(self, wire):
        result = wire.request("resources/read", {
            "uri": "fastmdxplora://study/project/ubq%2010ns"})["result"]
        assert result["cacheScope"] == "private"
        text = result["contents"][0]["text"]
        assert text.startswith("The study at project/ubq 10ns\nstatus: completed")
        assert "rmsd: mean 0.1234 ± 0.0056 nm" in text

    @pytest.mark.parametrize("uri", ["fastmdxplora://study/project",
                                     "fastmdxplora://study/../../etc",
                                     "fastmdxplora://guide/nothing", "file:///etc/passwd"])
    def test_anything_else_is_not_found_with_its_uri(self, wire, uri):
        error = wire.request("resources/read", {"uri": uri})["error"]
        assert error == {"code": -32602, "message": "Resource not found", "data": {"uri": uri}}

    def test_a_legacy_client_reads_without_the_modern_fields(self, wire):
        wire.initialize("2025-06-18")
        result = wire.request("resources/read", {
            "uri": "fastmdxplora://guide/working-with-studies"}, modern=False)["result"]
        assert set(result) == {"contents"}


class TestPrompts:
    def test_the_prompts_are_listed_with_their_arguments(self, wire):
        prompts = wire.request("prompts/list")["result"]["prompts"]
        assert [p["name"] for p in prompts] == ["design_a_study", "explain_a_study",
                                                "why_did_it_stop", "continue_a_study"]
        assert prompts[0]["arguments"] == [
            {"name": "goal", "description": "What the study is for, in your words.",
             "required": True},
            {"name": "structure", "description": "A PDB identifier or a structure file, "
                                                 "if you have one.", "required": False}]

    def test_designing_puts_the_agent_first_and_nothing_runs_unasked(self, wire):
        result = wire.request("prompts/get", {"name": "design_a_study", "arguments": {
            "goal": "Does the loop open at 310 K?", "structure": "1UBQ"}})["result"]
        text = result["messages"][0]["content"]["text"]
        assert text.startswith("I want a molecular dynamics study with FastMDXplora. Does "
                               "the loop open at 310 K?\nThe structure: 1UBQ.\n\nUse ask_agent")
        assert "do not start anything until I say so" in text
        bare = wire.request("prompts/get", {"name": "design_a_study", "arguments": {
            "goal": "Fold chignolin."}})["result"]["messages"][0]["content"]["text"]
        assert "The structure" not in bare

    def test_a_prompt_about_a_study_carries_its_record(self, wire):
        result = wire.request("prompts/get", {"name": "explain_a_study",
                                              "arguments": {"study": "project/ubq 10ns"}})["result"]
        assert len(result["messages"]) == 2
        resource = result["messages"][1]["content"]["resource"]
        assert resource["uri"] == "fastmdxplora://study/project/ubq%2010ns"
        assert "rmsd: mean 0.1234" in resource["text"]

    def test_a_missing_argument_an_unknown_prompt_or_study_is_refused(self, wire):
        missing = wire.request("prompts/get", {"name": "continue_a_study",
                                               "arguments": {"study": "project/ubq 10ns"}})
        assert missing["error"] == {"code": -32602, "message": "continue_a_study needs `more`."}
        assert wire.request("prompts/get", {"name": "x"})["error"]["code"] == -32602
        nowhere = wire.request("prompts/get", {"name": "why_did_it_stop",
                                               "arguments": {"study": "../.."}})
        assert nowhere["error"]["message"] == "../.. is not a study in the workspace."
