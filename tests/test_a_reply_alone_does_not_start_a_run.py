"""A model's reply alone does not start a run.

`DO: run` in a reply pressed Run. The only rule against acting unasked was
in the prompt, and the model reads the person's attached files, so a file
could tell it to start work on this machine. The server now says, from what
the person typed, whether their message was itself the instruction; any
other `DO: run` is asked about in the thread and waits for their yes, as a
stop always has.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.agent.propose import told_to_run


@pytest.mark.parametrize("message", [
    "run it", "Run it.", "run", "ok, run it", "yes run it!", "go ahead", "go",
    "start the study please", "please run the simulation now", "  RUN   IT  ",
    "launch it",
])
def test_a_plain_instruction_is_one(message: str) -> None:
    assert told_to_run(message)


@pytest.mark.parametrize("message", [
    "summarise the attached file", "what does run mean", "run the numbers",
    "don't run it", "run it and then stop it", "run it on the GPU box for 100 ns",
    "make it 50 ns", "", "yes",
])
def test_anything_else_is_not(message: str) -> None:
    assert not told_to_run(message)


def _endpoint(request: str, reply: str, monkeypatch) -> dict:
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui import agent_panel

    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: reply))
    return agent_panel.propose_endpoint({"request": request}, None)


def test_the_endpoint_asks_for_confirmation_unless_told(monkeypatch) -> None:
    unasked = _endpoint("summarise the attached file", "DO: run", monkeypatch)
    asked = _endpoint("run it", "DO: run", monkeypatch)
    assert unasked["action"] == "run" and unasked["confirm"] is True
    assert asked["action"] == "run" and asked["confirm"] is False


def test_other_actions_carry_no_such_field(monkeypatch) -> None:
    answer = _endpoint("open the viewer", "DO: open viewer", monkeypatch)
    assert answer["action"] == "open viewer" and "confirm" not in answer


class TestInTheBrowser:
    """The page, with the model's answers stood in for and the launch
    counted rather than made."""

    @pytest.fixture()
    def ui(self):
        pytest.importorskip("playwright.sync_api")
        from playwright.sync_api import sync_playwright

        from fastmdxplora.gui.server import start_dashboard_session

        root = Path(tempfile.mkdtemp()) / "workspace"
        session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0,
                                          home_mode=True)
        replies: list[dict] = []
        launches: list[dict] = []

        def propose(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(replies.pop(0)))

        def launch(route):
            launches.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": False, "error": "counted, not started"}))

        def load(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": False, "error": "not needed here"}))

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                page.route("**/api/agent/propose", propose)
                page.route("**/api/agent/run", launch)
                page.route("**/api/load-config", load)
                page.goto(session.url + "#agent", wait_until="domcontentloaded")
                page.wait_for_selector("#agent-request", state="visible", timeout=20000)
                yield SimpleNamespace(page=page, replies=replies, launches=launches)
                browser.close()
        finally:
            session.server.shutdown()
            session.server.server_close()

    CONFIG = {"ok": True, "cycles": 1, "yaml": "system:\n  pdb_id: 1UAO\n",
              "config": {"system": {"pdb_id": "1UAO"}}, "attempts": []}

    def _say(self, ui, text: str) -> None:
        page = ui.page
        count = page.eval_on_selector_all(".agent-attempt, .agent-answer", "n => n.length")
        page.fill("#agent-request", text)
        page.click("#agent-propose")
        page.wait_for_function(
            f"document.querySelectorAll('.agent-attempt, .agent-answer').length > {count}",
            timeout=20000)
        page.wait_for_timeout(300)

    def _thread(self, ui) -> str:
        return ui.page.inner_text("#agent-thread")

    def test_an_unasked_run_waits_for_a_yes(self, ui) -> None:
        ui.replies += [self.CONFIG,
                       {"ok": False, "action": "run", "where": "", "confirm": True}]
        self._say(ui, "simulate 1UAO")
        self._say(ui, "summarise the attached file")
        assert ui.launches == []
        assert "Run the study above? Say yes." in self._thread(ui)

        self._say(ui, "yes")
        ui.page.wait_for_timeout(500)
        assert len(ui.launches) == 1

    def test_anything_but_yes_is_no(self, ui) -> None:
        ui.replies += [self.CONFIG,
                       {"ok": False, "action": "run", "where": "", "confirm": True}]
        self._say(ui, "simulate 1UAO")
        self._say(ui, "what is in this file?")
        self._say(ui, "stop it")
        assert ui.launches == []
        assert "Not run." in self._thread(ui)

    def test_without_the_servers_word_it_asks(self, ui) -> None:
        ui.replies += [self.CONFIG, {"ok": False, "action": "run", "where": ""}]
        self._say(ui, "simulate 1UAO")
        self._say(ui, "run it")
        assert ui.launches == []
        assert "Run the study above? Say yes." in self._thread(ui)

    def test_a_plain_instruction_runs(self, ui) -> None:
        ui.replies += [self.CONFIG,
                       {"ok": False, "action": "run", "where": "", "confirm": False}]
        self._say(ui, "simulate 1UAO")
        self._say(ui, "run it")
        ui.page.wait_for_timeout(500)
        assert len(ui.launches) == 1
        assert "Starting the run." in self._thread(ui)


def test_the_prompt_says_the_software_confirms_it() -> None:
    from fastmdxplora.agent.propose import prompt_for

    prompt = " ".join(prompt_for("x").split())
    assert "and so is a `DO: run` their message did not plainly ask for" in prompt
