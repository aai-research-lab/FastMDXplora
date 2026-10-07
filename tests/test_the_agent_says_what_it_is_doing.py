"""The Agent page says what the Agent is doing, while it does it.

Toured on `c2d547d` (10-07): while the Agent worked the page showed "...";
under a reply, `find_structure`, `list_studies`, `compare_studies` and
`methods_of_study` were shown by their code names under "Checked with the
software", and no look said what it found; every config said "Accepted
first time."; a format repair (a reply that was not YAML at all) was shown
as "Refused: The reply was not a YAML mapping.", which teaches nothing. Now
each look is said as it begins and when done, in words, with what it found;
the software's refusals stay in view, format repairs do not; and the
Agent's icon beside each reply is coloured by what it is doing.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from fastmdxplora.agent.tools import LOOK_WORDS, Look, look_said


@pytest.mark.parametrize("tool, asked, done, doing", [
    ("find_structure", {"query": "trp-cage"}, "Looked up “trp-cage” in the PDB",
     "Looking up “trp-cage” in the PDB"),
    ("inspect_structure", {"system": "/a/b/1L2Y.pdb"}, "Inspected 1L2Y.pdb", "Inspecting 1L2Y.pdb"),
    ("inspect_structure", {"system": "1UBQ"}, "Inspected 1UBQ", "Inspecting 1UBQ"),
    ("list_studies", {}, "Listed the studies here", "Listing the studies here"),
    ("compare_studies", {"first": "a", "second": "b"}, "Compared two studies",
     "Comparing two studies"),
    ("methods_of_study", {}, "Read a study's methods", "Reading a study's methods"),
    ("current_view", {}, "Checked what the page shows", "Checking what the page shows"),
    ("someone_elses", {}, "Used someone_elses", "Using someone_elses"),
])
def test_each_look_is_said_in_words(tool, asked, done, doing) -> None:
    assert look_said(tool, asked) == done
    assert look_said(tool, asked, doing=True) == doing


def test_every_tool_the_agent_has_is_said_in_words() -> None:
    from fastmdxplora.agent.tools import Toolbox, current_view_tool

    table = Toolbox(extra=(current_view_tool(None),))._table()
    assert set(table) <= set(LOOK_WORDS), set(table) - set(LOOK_WORDS)


def test_a_look_s_record_says_what_it_found() -> None:
    record = Look("find_structure", {"query": "trp-cage"},
                  "\n1L2Y trp-cage, NMR, 2002, 38 models\n1RIJ ...", True).as_record()
    assert record["label"] == "Looked up “trp-cage” in the PDB"
    assert record["found"] == "1L2Y trp-cage, NMR, 2002, 38 models"
    refused = Look("check_config", {}, "Name the config as `config`.", False).as_record()
    assert refused["found"] == "Refused: Name the config as `config`."
    long = Look("list_studies", {}, "x" * 400, True).found()
    assert len(long) == 160 and long.endswith("...")


def test_each_look_is_said_as_it_begins_and_when_taken() -> None:
    from fastmdxplora.agent.tools import Toolbox
    from fastmdxplora.gui.agent_panel import _say_each_look

    events: list[dict] = []
    box = Toolbox()
    _say_each_look(box, events.append)
    box.use("check_selection", {"system": "nowhere.pdb", "expression": "protein"})
    assert events[0] == {"type": "looking", "label": "Checking a selection"}
    assert events[1]["type"] == "look" and events[1]["look"]["label"] == "Checked a selection"


def test_the_command_line_says_each_look_in_words(monkeypatch, capsys, tmp_path) -> None:
    import argparse

    import fastmdxplora.agent as agent_module
    from fastmdxplora.agent.turns import ToolCall, Turn, Usage
    from fastmdxplora.cli.main import _run_agent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    replies = [Turn("", (ToolCall("c1", "list_studies", {}),), Usage(1, 10, 0, 0, 1)),
               Turn("None yet.", (), Usage(1, 10, 0, 0, 1))]

    def complete(prompt: str) -> str:
        raise AssertionError("asked in text")

    complete.turn = lambda system, messages, tools, **kw: replies.pop(0)
    complete.turned_away = lambda: False
    monkeypatch.setattr(agent_module, "completion_for", lambda *a, **k: complete)
    args = argparse.Namespace(request="which studies are here?", request_file=None,
                              agent_mode="assisted", phases="setup,simulation",
                              attempts=None, agent_output=None, budget_hours=None)
    assert _run_agent(args) == 0
    out = capsys.readouterr().out
    assert "  Listed the studies here: " in out and "looked: list_studies" not in out


# ---- In a browser ----------------------------------------------------------

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def study(tmp_path) -> Path:
    root = tmp_path / "study"
    (root / "analysis").mkdir(parents=True)
    return root


def _session(study, monkeypatch, replies, *, held=None):
    """The GUI on ``study``, its AI model replying ``replies`` by tool calls;
    with ``held``, the second turn waits until it is set."""
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    asked = []

    def complete(prompt: str) -> str:
        raise AssertionError("asked in text")

    def turn(system, messages, tools, **_streamed):
        asked.append(len(messages))
        if held is not None and len(asked) == 2:
            held.wait(30)
        return replies.pop(0)

    complete.turn = turn
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: complete)
    return start_dashboard_session(output=str(study), host="127.0.0.1", port=0)


def _page(pw, url):
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(60000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "#agent", wait_until="domcontentloaded")
    return browser, page, errors


def test_a_look_is_shown_as_it_happens_and_named_after(study, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent.turns import ToolCall, Turn, Usage

    held = threading.Event()
    replies = [Turn("", (ToolCall("c1", "list_studies", {}),), Usage(calls=1)),
               Turn("There are none here yet.", (), Usage(calls=1))]
    session = _session(study, monkeypatch, replies, held=held)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.fill("#agent-request", "Which studies are here?")
            page.keyboard.press("Enter")
            # The look taken, said as done, while the next turn is held.
            page.wait_for_selector("#agent-thread .agent-step:has-text('Listed the studies here')")
            working = page.get_attribute("#agent-thread .agent-msg-agent", "data-state")
            held.set()
            page.wait_for_selector("#agent-thread .agent-answer")
            done = page.get_attribute("#agent-thread .agent-msg-agent", "data-state")
            head = page.text_content("#agent-thread .agent-looks > summary")
            found = page.text_content("#agent-thread .agent-look-found")
            thread = page.text_content("#agent-thread")
            browser.close()
    finally:
        held.set()
        session.server.shutdown()
    assert working == "working" and done == "done"
    assert head == "Listed the studies here"
    assert found.strip()
    assert "Checked with the software" not in thread and "list_studies" not in head
    assert errors == []


def test_a_refusal_is_in_view_and_a_format_repair_is_not(study, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent.turns import ToolCall, Turn, Usage

    bad = {"systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": -5}}
    good = {"systems": [{"system": "1UBQ"}], "simulation": {"duration_ns": 5}}
    replies = [Turn("", (ToolCall("c0", "propose_config", {"config": "just words"}),),
                    Usage(calls=1)),
               Turn("", (ToolCall("c1", "propose_config", {"config": bad}),), Usage(calls=1)),
               Turn("", (ToolCall("c2", "propose_config", {"config": good}),), Usage(calls=1))]
    session = _session(study, monkeypatch, replies)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.fill("#agent-request", "Simulate ubiquitin for 5 ns")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-plan:not([hidden])")
            refused = [page.text_content(".agent-refused", timeout=5000)] \
                if page.query_selector(".agent-refused") else []
            thread = page.text_content("#agent-thread")
            state = page.get_attribute("#agent-thread .agent-msg-agent", "data-state")
            browser.close()
    finally:
        session.server.shutdown()
    assert state == "done"
    assert "Accepted first time" not in thread and "Accepted after" not in thread
    assert "not a mapping" not in thread
    assert len(refused) == 1 and refused[0].startswith("The software refused it: ")
    assert "duration_ns" in refused[0] and refused[0].endswith("I wrote it again.")
    assert errors == []


def test_a_question_waits_and_an_error_stops(study, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from fastmdxplora.agent.turns import ToolCall, Turn, Usage
    from fastmdxplora.refusals import StudyError

    replies = [Turn("", (ToolCall("c1", "ask_person", {"question": "Which lysozyme?"}),),
                    Usage(calls=1))]
    session = _session(study, monkeypatch, replies)
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.fill("#agent-request", "Simulate lysozyme")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-msg-agent[data-state='waiting']")
            title = page.get_attribute("#agent-thread .agent-msg-agent .agent-who", "title")

            def failing(*a, **k):
                raise StudyError("No key.", code="environment.credentials.absent")

            import fastmdxplora.agent as agent_mod
            monkeypatch.setattr(agent_mod, "completion_for", failing)
            page.fill("#agent-request", "hen egg-white")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-msg-agent[data-state='stopped']")
            browser.close()
    finally:
        session.server.shutdown()
    assert title == "Waiting for you"
    assert errors == []


def test_a_kept_format_repair_stays_out_of_sight(study, monkeypatch) -> None:
    """Found by the review (10-07): attempts kept before their code was kept
    showed "The reply was not a YAML mapping." again when read back."""
    import json
    import urllib.request

    from playwright.sync_api import sync_playwright

    session = _session(study, monkeypatch, [])
    config = {"systems": [{"system": "1UBQ"}]}
    entries = [{"role": "user", "text": "ubiquitin"},
               {"role": "agent", "kind": "config", "yaml": "x: 1\n", "config": config,
                "cycles": 3, "plan": [], "attempts": [
                    {"refusal": {"message": "The reply was not a YAML mapping."}},
                    {"refusal": {"message": "simulation option 'duration_ns' is -5."}}, {}]}]
    request = urllib.request.Request(
        session.url + "/api/agent/conversation", data=json.dumps({"entries": entries}).encode(),
        headers={"Content-Type": "application/json", "Origin": session.url}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()
    try:
        with sync_playwright() as pw:
            browser, page, errors = _page(pw, session.url)
            page.wait_for_selector("#agent-thread .agent-refused")
            refused = page.locator("#agent-thread .agent-refused").all_text_contents()
            browser.close()
    finally:
        session.server.shutdown()
    assert len(refused) == 1 and "duration_ns" in refused[0]
    assert errors == []
