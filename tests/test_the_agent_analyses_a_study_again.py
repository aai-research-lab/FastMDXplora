"""The Agent analyses the study open again, or writes its report again, when told.

Told "add SASA to this study" or "write its report again", the Agent could
answer only in words. It now replies `DO: analyze again rmsd sasa` or
`DO: write the report again`, read from that one line by a strict pattern;
the analyses named are checked against the software's own, the person is
asked what will run and what is kept aside, and on a yes the phase command
runs with `--rerun` on the study. A name the software does not have is said, and nothing
runs.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed  # noqa: E402

from fastmdxplora.agent.propose import _again_in, propose_config  # noqa: E402
from fastmdxplora.gui.exploration import DashboardRuntime  # noqa: E402


@pytest.mark.parametrize("line,read", [
    ("DO: analyze again", ("analyze again", {"analyses": None})),
    ("DO: analyse again", ("analyze again", {"analyses": None})),
    ("DO: Analyze again rmsd, rg and sasa.", ("analyze again", {"analyses": ["rmsd", "rg", "sasa"]})),
    ("DO: analyse it again with hbonds", ("analyze again", {"analyses": ["hbonds"]})),
    ("DO: write the report again", ("write the report again", {})),
    ("DO: write its report again.", ("write the report again", {})),
])
def test_what_is_read(line, read):
    assert _again_in(line) == read


@pytest.mark.parametrize("line", [
    "DO: analyze again; rm -rf ~",
    "DO: analyze again rmsd\nand then stop",
    "analyse again rmsd",
    "DO: simulate again",
    "DO: analyze again rmsd-rg",
])
def test_nothing_else_is(line):
    assert _again_in(line) is None


def test_the_loop_returns_it_as_an_action():
    proposal = propose_config("add sasa", lambda prompt: "DO: analyze again rmsd sasa")
    assert proposal.action == "analyze again"
    assert proposal.arguments == {"analyses": ["rmsd", "sasa"]}


def test_the_agent_is_told_how_and_what_is_not_run_in_place():
    seen: list[str] = []
    propose_config("add sasa", lambda prompt: seen.append(prompt) or "DO: analyze again")
    said = " ".join(seen[0].split())
    assert "`DO: analyze again rmsd rg sasa hbonds`" in said
    assert "`DO: write the report again`" in said
    assert "Setup and simulation are not run again on a study that has them" in said


def _runtime(root: Path) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


def test_the_agent_is_answered_with_what_would_run(tmp_path, monkeypatch):
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.agent_panel import propose_endpoint

    root = _analysed(tmp_path / "study")
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "DO: analyze again rmsd rg"))
    answer = propose_endpoint({"request": "add rg"}, _runtime(root))

    assert answer["action"] == "analyze again" and answer["confirm"] is True
    fix = answer["fix"]
    assert fix["route"] == "/api/again"
    assert fix["request"] == {"phases": ["analysis", "report"], "analyses": ["rmsd", "rg"]}
    assert fix["fix"] == ("Analyze this study again with rmsd, rg, writing its report again "
                          "too, keeping what they replace in previous/")


def test_a_name_it_does_not_have_is_said(tmp_path, monkeypatch):
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.agent_panel import propose_endpoint

    root = _analysed(tmp_path / "study")
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "DO: analyze again rmsdd"))
    answer = propose_endpoint({"request": "add it"}, _runtime(root))

    assert answer["fix"] is None and "did you mean 'rmsd'" in answer["refused"]


def test_an_ai_app_is_told_what_does_it(tmp_path, monkeypatch):
    from tests.test_an_ai_app_asks_the_agent import Model, _wire, ask

    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    space = tmp_path / "work"
    space.mkdir()
    wire = _wire(space, Model("DO: analyze again rmsd rg", "DO: write the report again"))
    added = ask(wire, request="add rg")["content"][0]["text"]
    written = ask(wire, request="write its report again")["content"][0]["text"]
    wire.close()
    assert added.startswith("The Agent read this as an instruction: analyze again.\n"
                            "run_phases_again runs it on the study, once the person has "
                            "agreed, with the analyses rmsd, rg.")
    assert written.startswith("The Agent read this as an instruction: write the report "
                              "again.\nrun_phases_again runs it on the study, once the "
                              "person has agreed.")


def test_the_agent_asks_then_writes_the_report_again(tmp_path, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    root = _analysed(tmp_path / "study")
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(agent_mod, "completion_for", lambda *a, **k: (
        lambda prompt: "DO: write the report again"))
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    ran: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def answer(route):
                ran.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(json={"ok": True})
            page.route("**/api/again", answer)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "write its report again")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Say yes')")
            asked = page.locator("#agent-thread .agent-attempt").last.text_content()
            assert ran == []
            page.fill("#agent-request", "yes")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Started')",
                                   state="attached")
            started = page.locator("#agent-thread .agent-attempt").last.text_content()
            browser.close()
    finally:
        session.server.shutdown()
    assert asked == ("Write the report again from this study's records, keeping the report "
                     "there now in previous/? Say yes.")
    assert ran == [{"phases": ["report"], "analyses": None}]
    assert f"fastmdx report --output {root.resolve()} --rerun" in started
    assert errors == []
