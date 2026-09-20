"""The docs about the Agent say what the software does.

Twice the docs said the opposite of what was true: "it does not hold a
conversation" for weeks after it did, "no file access" after the + was
built. The docs-honesty tests held two counts and would not have noticed
either. These hold the claims that can go stale, each against the code
that makes it true or false.
"""

from __future__ import annotations

import inspect
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
AGENT = (DOCS / "agent.md").read_text(encoding="utf-8")
GUI = (DOCS / "gui.md").read_text(encoding="utf-8")


class TestClaimsThatWentStaleBefore:

    def test_it_holds_a_conversation_and_the_docs_say_so(self):
        from fastmdxplora.agent.propose import prompt_for

        assert "## The conversation so far" in prompt_for("x", history=[{"role": "user", "text": "y"}])
        assert "does not hold a conversation" not in AGENT
        assert "Conversations belong to studies" in AGENT

    def test_files_can_be_attached_and_the_docs_say_so(self):
        from fastmdxplora.gui.agent_panel import read_attachment

        assert callable(read_attachment)
        assert "A file you attached" in AGENT
        assert "does not have access to files" not in AGENT.lower()

    def test_it_acts_and_the_docs_list_the_actions(self):
        from fastmdxplora.agent.propose import ACTIONS

        for action in ACTIONS:
            assert action in AGENT, f"the docs do not list the action {action!r}"

    def test_it_continues_a_study_and_the_docs_say_how(self):
        from fastmdxplora.simulation.resume import continuation_of

        assert callable(continuation_of)
        assert "### Continuing a study that stopped" in AGENT
        assert "never writes `resume_from` by hand" in AGENT


class TestTheGuiDocsMatchTheFrame:

    def test_the_tabs_are_named_as_the_page_names_them(self):
        import fastmdxplora.gui as gui

        page = (Path(gui.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
        for tab in ("Agent", "Config", "Overview", "Viewer", "Analysis", "Report", "Files"):
            assert f"<span>{tab}</span>" in page, tab
            assert f"| **{tab}** |" in GUI, f"gui.md has no row for the {tab} tab"
        assert "| **Builder** |" not in GUI
        assert "| **New Exploration** |" not in GUI

    def test_the_study_block_and_load_available_study(self):
        import fastmdxplora.gui as gui

        page = (Path(gui.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
        assert ">Load available study<" in page
        assert "Load available study" in GUI
        assert "id=\"study-elsewhere\"" in page
        assert "RUNNING" in GUI

    def test_the_folder_naming_rule_is_the_documented_one(self):
        from fastmdxplora.naming import default_output_name

        name = default_output_name("1UAO")
        assert name.startswith("fastmdxplora_1UAO_study_")
        assert "fastmdxplora_<system>_study_<UTC timestamp>" in GUI

    def test_a_run_outlives_the_server_and_the_docs_say_so(self):
        from fastmdxplora.gui import exploration

        assert "start_new_session=True" in inspect.getsource(exploration.DashboardRuntime._spawn)
        assert "The run outlives the server" in GUI
        assert hasattr(exploration.DashboardRuntime, "_adopt_if_running")
        assert "adopts" in GUI and "Stop reaches it" in GUI
