"""The Agent knows what the person is looking at, and what they name with @.

The page's hints went with every message, but only the Viewer had any, and
its frame went from every page. Now each page sends its own: its name, the
study open, the file open in the reader, the report's section being read;
the Viewer still its view. The look at what the page shows reads the facts
for them, a file inside the study and nowhere else (`read_study_file`).
What a message names with @ is resolved by the software to what it names
and the look that reads it, never pasted in, and a name that matches
nothing is said to match nothing.
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from fastmdxplora.agent.context import MOST_MENTIONS, mentions_in, named_in
from fastmdxplora.agent.tools import Toolbox, current_view_tool
from fastmdxplora.mcp.workspace import Workspace
from tests._agent_page import ready
from tests.test_the_workspace_says_its_studies import _study

STATIC = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui" / "static"


# -- what is named with @ -----------------------------------------------------------

def test_names_with_at_are_read_as_typed_and_an_address_is_not_one():
    said = ("What did @rmsd come to in @study, at @frame 120? Mail a@b.org. "
            "Look at @Trp6, @A:45 and @file analysis/rg/options.json. @rmsd again.")
    assert mentions_in(said) == ["rmsd", "study", "frame 120", "Trp6", "A:45",
                                 "file analysis/rg/options.json"]
    assert len(mentions_in(" ".join(f"@n{i}" for i in range(20)))) == MOST_MENTIONS
    assert mentions_in("no names here, or @ alone") == []


def test_each_name_is_resolved_to_where_its_record_is(tmp_path):
    study = _study(tmp_path / "ws" / "runs" / "ubq", means={"rmsd": (0.12, 0.01)})
    box = Toolbox(workspace=Workspace.at(tmp_path / "ws"))
    said = named_in("What did @rmsd come to? And @rg, @study, @frame 7, @Trp6, @docs, "
                    "@file analysis/rmsd/options.json?", box, study)
    lines = said.splitlines()
    assert lines[0] == "## Named with @ in this message"
    rmsd = next(line for line in lines if line.startswith("- @rmsd:"))
    assert "analysis/rmsd/options.json" in rmsd and "read_study" in rmsd
    # The record is named, its numbers are not.
    assert "0.12" not in said
    rg = next(line for line in lines if line.startswith("- @rg:"))
    assert "names nothing the software found" in rg and "analyses are: rmsd" in rg
    assert f"read_study (study: {study})" in said
    assert "frame 7 of the open study's trajectory" in said
    assert "`resname TRP and resSeq 6`" in said and "check_selection" in said
    assert "read_docs" in said
    assert ("read_study_file (study: " + str(study) + ", file: analysis/rmsd/options.json)"
            in said)


def test_a_study_is_found_by_its_folder_s_name_and_two_alike_are_asked_about(tmp_path):
    ws = tmp_path / "ws"
    one = _study(ws / "runs" / "trp_cage_310", system="1L2Y")
    box = Toolbox(workspace=Workspace.at(ws))
    said = named_in("What did @trp_cage_310 record?", box, None)
    assert f"read_study (study: {one})" in said
    _study(ws / "other" / "trp_cage_310", system="1L2Y")
    said = named_in("What did @trp_cage_310 record?", box, None)
    assert "more than one study" in said and "ask the person which" in said
    # With no study open, an analysis cannot be named.
    assert "No study is open" in named_in("@rmsd?", box, None)
    assert "no study is open" in named_in("@study?", box, None)


def test_the_message_carries_the_names_only_where_there_are_some(tmp_path):
    from fastmdxplora.agent.propose import propose_config

    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01)})
    prompts: list[str] = []

    def complete(prompt: str, **_: object) -> str:
        prompts.append(prompt)
        return "SAY: It is 0.12 nm."

    propose_config("What did @rmsd come to?", complete, study=study)
    assert "## Named with @ in this message" in prompts[-1]
    assert prompts[-1].index("## Named with @") < prompts[-1].index("## The study wanted")
    propose_config("What did it come to?", complete, study=study)
    assert "## Named with @" not in prompts[-1]
    # Without the context, nothing is resolved: the @ is as typed.
    propose_config("What did @rmsd come to?", complete, study=study, context=False)
    assert "## Named with @" not in prompts[-1] and "@rmsd" in prompts[-1]


def test_the_page_s_open_study_is_what_names_refer_to(monkeypatch, tmp_path):
    import fastmdxplora.agent as agent_module
    from fastmdxplora.gui.agent_panel import propose_endpoint
    from fastmdxplora.refusals import StudyError

    given = {}

    def propose(request, complete, **kw):
        given.update(kw)
        raise StudyError("stopped here")

    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: object())
    monkeypatch.setattr(agent_module, "propose_config", propose)
    propose_endpoint({"request": "what did @rmsd come to?"},
                     types.SimpleNamespace(exploration_root=str(tmp_path),
                                           active_root=str(tmp_path / "ubq")))
    assert given["study"] == str(tmp_path / "ubq")


# -- what the page shows -----------------------------------------------------------

def test_the_file_open_is_read_inside_the_study(tmp_path):
    study = _study(tmp_path / "ubq", means={"rg": (1.21, 0.02)})
    tool = current_view_tool({"page": "files", "study": str(study),
                              "file": "analysis/rg/options.json"}, active_root=str(study))
    box = Toolbox(extra=(tool,))
    look = box.use("current_view", {})
    assert look.ok
    assert "has analysis/rg/options.json open in the file reader" in look.said
    assert '"mean": 1.21' in look.said


def test_the_report_s_section_is_said_with_the_study_s_record(tmp_path):
    study = _study(tmp_path / "ubq", means={"rmsd": (0.12, 0.01)})
    tool = current_view_tool({"page": "report", "study": str(study), "section": "RMSD"},
                             active_root=str(study))
    said = Toolbox(extra=(tool,)).use("current_view", {}).said
    assert "reading the report at its section 'RMSD'" in said
    assert "Factual evidence from read_study" in said


@pytest.mark.parametrize("file, why", [
    ("/etc/passwd", "not a path inside the study"),
    ("../other/secret.txt", "not a path inside the study"),
    ("analysis/nothing.json", "holds no file"),
    ("", "Name the file"),
])
def test_a_file_outside_the_study_or_not_there_is_not_read(tmp_path, file, why):
    study = _study(tmp_path / "ubq")
    look = Toolbox(open_study=study).use("read_study_file", {"study": str(study), "file": file})
    assert not look.ok and why in look.said


def test_a_link_out_of_the_study_a_binary_and_a_huge_file_are_not_read(tmp_path, monkeypatch):
    import fastmdxplora.agent.tools as tools

    study = _study(tmp_path / "ubq")
    (tmp_path / "outside.txt").write_text("secret")
    os.symlink(tmp_path / "outside.txt", study / "leak.txt")
    box = Toolbox(open_study=study)
    look = box.use("read_study_file", {"study": str(study), "file": "leak.txt"})
    assert not look.ok and "leads out of the study" in look.said
    (study / "traj.dcd").write_bytes(b"CORD\0\0\0binary")
    look = box.use("read_study_file", {"study": str(study), "file": "traj.dcd"})
    assert not look.ok and "is not text" in look.said
    monkeypatch.setattr(tools, "MOST_FILE_BYTES", 10)
    (study / "big.log").write_text("x" * 50)
    look = box.use("read_study_file", {"study": str(study), "file": "big.log"})
    assert not look.ok and "too large" in look.said


def test_a_long_file_is_read_by_its_head_and_tail(tmp_path):
    study = _study(tmp_path / "ubq")
    (study / "run.log").write_text("".join(f"line {n}\n" for n in range(3000)))
    look = Toolbox(open_study=study).use("read_study_file",
                                         {"study": str(study), "file": "run.log"})
    assert look.ok and "line 0\n" in look.said and "line 2999" in look.said
    assert "(the middle was cut)" in look.said and len(look.said) <= 4000


def test_only_a_study_open_or_in_the_workspace_is_read_and_no_hidden_file(tmp_path):
    """A folder holding an `analysis` folder alone (a home folder) is not a
    study whose files are read; on the person's own computer the study is
    the one open or one in the workspace; a hidden file is never read."""
    home = tmp_path / "home"
    (home / "analysis").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    (home / "notes.txt").write_text("mine")
    look = Toolbox(open_study=home).use("read_study_file",
                                        {"study": str(home), "file": "notes.txt"})
    assert not look.ok and "holds no study's record" in look.said
    study = _study(tmp_path / "ws" / "ubq")
    (study / ".env").write_text("KEY=1")
    (study / "notes.txt").write_text("kept")
    look = Toolbox().use("read_study_file", {"study": str(study), "file": "notes.txt"})
    assert not look.ok and "neither the study open nor one in the workspace" in look.said
    inside = Toolbox(workspace=Workspace.at(tmp_path / "ws"))
    assert inside.use("read_study_file", {"study": str(study), "file": "notes.txt"}).ok
    os.symlink(".env", study / "envlink")
    for hidden in (".env", ".ssh/id_rsa", "analysis/.x", "envlink"):
        look = Toolbox(open_study=study).use("read_study_file",
                                             {"study": str(study), "file": hidden})
        assert not look.ok and "hidden" in look.said, hidden


def test_a_hosted_server_s_rule_holds_for_a_study_s_file(tmp_path):
    study = _study(tmp_path / "ubq")
    (study / "notes.txt").write_text("kept")
    refuse_notes = lambda p: None if str(p).endswith("notes.txt") else str(p)  # noqa: E731
    look = Toolbox(path_for=refuse_notes).use("read_study_file",
                                              {"study": str(study), "file": "notes.txt"})
    assert not look.ok and "outside the workspace" in look.said


def test_each_page_sends_its_own_hints_and_the_viewer_its_view():
    panel = (STATIC / "agent-panel.js").read_text(encoding="utf-8")
    assert "current_view: pageHints()," in panel
    # The Viewer's view only from the Viewer.
    assert 'if (page === "viewer" && viewer && viewer.currentViewHints)' in panel
    assert "window.FastMDXPageHints[page]" in panel
    assert "frame.previewing()" in panel
    frame = (STATIC / "frame.js").read_text(encoding="utf-8")
    assert "previewing: previewing" in frame
    # A file is of the study open, or of the study its text was read from,
    # as the server says it with the text.
    assert "var kept = previewed = { path: item.path, study: studyOpen() };" in frame
    assert "if (previewed !== kept) return;" in frame
    assert "if (data && data.ok && data.study) kept.study = data.study;" in frame
    report = (STATIC / "report-page.js").read_text(encoding="utf-8")
    assert "window.FastMDXPageHints.report = function ()" in report
    # The names with @ shown as chips while typed, by the server's pattern.
    assert "function renderMentions()" in panel
    assert "renderMentions(); });" in panel


def test_the_page_s_pattern_for_names_is_the_server_s():
    import re

    from fastmdxplora.agent import context

    panel = (STATIC / "agent-panel.js").read_text(encoding="utf-8")
    found = re.search(r"var MENTION = /\(\^\|\[\^\\w@\.\]\)@\((.+?)\)/g;", panel)
    assert found, "the page's pattern"
    server = context._MENTION.pattern
    assert server.endswith("@(" + found.group(1) + ")")



# -- in a browser -------------------------------------------------------------------

def test_names_chips_and_a_plan_s_ticks_in_a_browser(tmp_path, monkeypatch):
    """Driven: what is typed with @ shows as chips and is resolved for the AI
    model; the plan kept beside a reply shows as a checklist, goes back with
    the next message, and is shown again when the thread is opened again;
    the file opened in the reader is the one the page names."""
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("mdtraj")
    import tempfile

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent.turns import ToolCall, Turn, Usage
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _analysis, _manifest, _series
    from tests.test_the_agent_and_the_picker_are_driven_in_a_browser import _browser
    from tests.test_the_drawing_scripts_run_in_a_browser import FRAMES, _write_study

    root = _write_study(tmp_path / "study")
    _manifest(root, saving_interval_ps=2.0)
    _analysis(root, "rmsd", _series(FRAMES), {
        "mean": 0.112, "standard_error": 0.002, "effective_samples": 11.0, "discard": 5,
        "n_frames": FRAMES, "unit": "nm"})
    (root / "notes.txt").write_text("The ligand is benzamidine.\n", encoding="utf-8")
    plan = ToolCall("p1", "keep_plan", {"steps": [{"step": "Read the RMSD", "done": True},
                                                  {"step": "Write a longer study"}]})
    replies = [Turn("The RMSD settled at 0.112 nm.", (plan,), Usage(calls=1)),
               Turn("Next I would write the longer study.", (), Usage(calls=1))]
    sent: list[str] = []

    def complete(prompt: str) -> str:
        raise AssertionError("asked in text")

    def turn(system, messages, tools, **_streamed):
        sent.append(messages[-1]["text"])
        return replies.pop(0)

    complete.turn = turn
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser, page = _browser(pw)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            ready(page)
            page.fill("#agent-request", "Did @rmsd settle? Write to a@b.org about @study.")
            page.wait_for_selector("#agent-mentions:not([hidden]) .agent-chip")
            chips = page.locator("#agent-mentions .agent-chip").all_text_contents()
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-plan li")
            ticks = page.eval_on_selector_all(
                "#agent-thread .agent-plan input", "els => els.map(e => e.checked)")
            chips_after = page.is_hidden("#agent-mentions")
            ready(page)
            page.fill("#agent-request", "What next?")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-answer >> nth=1")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-thread .agent-plan li")
            restored = page.locator("#agent-thread .agent-plan li").all_text_contents()
            plan_head_text = page.locator("#agent-thread .agent-plan-head").first.text_content()
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.evaluate("window.FastMDXFrame.previewPath('notes.txt')")
            # The page's frames may not be painted headless: asked by time.
            page.wait_for_function("window.FastMDXFrame.previewing() === 'notes.txt'",
                                   polling=200)
            # Folded, or turned to the Log, the reader is not what is looked at.
            page.evaluate("window.FastMDXFrame.setCollapsed(true)")
            folded = page.evaluate("window.FastMDXFrame.previewing()")
            page.evaluate("window.FastMDXFrame.setCollapsed(false)")
            unfolded = page.evaluate("window.FastMDXFrame.previewing()")
            page.evaluate("window.FastMDXFrame.showTab('log')")
            log_shown = page.evaluate("window.FastMDXFrame.previewing()")
            errors = list(page.errors)
            browser.close()
    finally:
        session.server.shutdown()
    assert chips == ["@rmsd", "@study"]
    assert chips_after is True
    assert "## Named with @ in this message" in sent[0]
    assert "- @rmsd: the rmsd analysis of the study open" in sent[0]
    assert ticks == [True, False]
    assert "## Your plan so far" in sent[1] and "1. [x] Read the RMSD" in sent[1]
    assert [t.strip() for t in restored] == ["Read the RMSD", "Write a longer study"]
    assert plan_head_text == "Plan: 1 of 2 done"
    assert log_shown is None and folded is None and unfolded == "notes.txt"
    assert errors == []
