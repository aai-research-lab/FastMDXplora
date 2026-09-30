"""A run the Agent's Run here is refused says what would fix it.

The builder's refusals carried their fix since 1133; the same config
refused through the Agent's Run here came back as the refusal alone, and an
autonomous config that carried its own `budget_hours` was refused for the
panel's field being empty. Now every coded refusal of Run here comes with
its remedy, a missing backend with its install command, and where the fix
is a setting of the study the Agent is offered it: the refusal goes into
the thread as the person's next message, and the Agent rewrites its config.
A budget, an install or a choice only the person can make is said and not
handed on.
"""

from __future__ import annotations

import json
import tempfile
from unittest import mock

import pytest

from fastmdxplora.gui.agent_panel import run_endpoint
from fastmdxplora.gui.exploration import DashboardRuntime

A_STUDY = {"systems": [{"id": "a", "system": "1UBQ"}]}


def _runtime(tmp_path) -> DashboardRuntime:
    return DashboardRuntime(workspace_root=tmp_path, exploration_root=tmp_path)


class TestWhatTheServerSays:
    def test_a_budget_refusal_names_the_setting(self, tmp_path):
        answer = run_endpoint({"config": {**A_STUDY, "agent": "autonomous"}}, _runtime(tmp_path))
        assert answer["code"] == "environment.budget.absent"
        assert answer["remedy"]["settings"] == ["budget_hours"]
        assert answer["remedy"]["argv"] == []

    def test_a_budget_the_config_carries_is_the_budget(self, tmp_path):
        runtime = _runtime(tmp_path)
        with mock.patch.object(DashboardRuntime, "launch_from_config",
                               return_value={"ok": True}) as launch:
            answer = run_endpoint({"config": {**A_STUDY, "agent": "autonomous",
                                              "budget_hours": 2}}, runtime)
        assert answer == {"ok": True}
        assert launch.call_args.kwargs["config"]["budget_hours"] == 2.0

    def test_the_panels_field_comes_first(self, tmp_path):
        with mock.patch.object(DashboardRuntime, "launch_from_config",
                               return_value={"ok": True}) as launch:
            run_endpoint({"config": {**A_STUDY, "agent": "autonomous", "budget_hours": 2},
                          "budget_hours": "5"}, _runtime(tmp_path))
        assert launch.call_args.kwargs["config"]["budget_hours"] == 5.0

    def test_a_refused_setting_comes_with_its_fix(self, tmp_path):
        answer = run_endpoint({"config": {**A_STUDY, "setup": {"ph": 25}}}, _runtime(tmp_path))
        assert not answer["ok"] and answer["refusal"]["code"] == "config.option.out_of_range"
        assert answer["remedy"]["settings"] == ["setup.ph"]
        assert not (tmp_path / "exploration.yml").exists()

    def test_a_refusal_raised_comes_with_its_fix(self, tmp_path):
        from fastmdxplora.refusals import StudyError

        with mock.patch.object(DashboardRuntime, "launch_from_config",
                               side_effect=StudyError("no", code="setup.forcefield.unknown")):
            answer = run_endpoint({"config": A_STUDY}, _runtime(tmp_path))
        assert answer["code"] == "setup.forcefield.unknown" and answer["error"] == "no"
        assert answer["remedy"]["settings"] == ["setup.forcefield"]

    def test_a_missing_backend_comes_with_its_install_command(self, tmp_path):
        from fastmdxplora import dependencies

        missing = [dependencies.MissingDependency("OpenMM", "openmm", "openmm")]
        with mock.patch.object(dependencies, "missing_dependencies", return_value=missing), \
                mock.patch("fastmdxplora.gui.exploration.exploration_environment_error",
                           return_value="This workflow needs OpenMM."):
            answer = run_endpoint({"config": {**A_STUDY, "output": "here"}}, _runtime(tmp_path))
        assert answer["code"] == "environment.backend.missing"
        assert answer["remedy"]["command"] == "conda install -c conda-forge openmm"
        # Said, never run for the person.
        assert answer["remedy"]["argv"] == []

    def test_a_refusal_without_a_code_is_said_as_it_is(self):
        from fastmdxplora.gui.agent_panel import _with_its_fix

        assert _with_its_fix({"ok": False, "error": "busy"}) == {"ok": False, "error": "busy"}


def test_the_agent_is_offered_the_fix(tmp_path, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    written = "systems:\n  - {id: a, system: 1UBQ}\nsetup:\n  ph: 7.0\n"
    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: written))
    refusal = run_endpoint({"config": {**A_STUDY, "setup": {"ph": 25}}}, _runtime(tmp_path))
    budget = run_endpoint({"config": {**A_STUDY, "agent": "autonomous"}}, _runtime(tmp_path))
    session = start_dashboard_session(output=str(tmp_path), host="127.0.0.1", port=0)
    asked: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            answers = [refusal, budget]
            page.route("**/api/agent/run", lambda route: route.fulfill(json=answers.pop(0)))
            page.on("request", lambda r: asked.append(json.loads(r.post_data or "{}"))
                    if r.url.endswith(("/api/agent/propose", "/api/agent/propose-stream")) else None)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            page.fill("#agent-request", "ubiquitin")
            page.keyboard.press("Enter")
            run = "#agent-thread .agent-msg-agent [data-role=run]"
            page.wait_for_selector(f"{run}:visible")
            page.locator(run).last.click()
            fix = page.locator("#agent-thread [data-role=fix]").last
            fix.locator(".agent-fix-ask").wait_for()
            said = fix.text_content()
            code = fix.locator(".fix-fix code").text_content()
            fix.locator(".agent-fix-ask").click()
            page.wait_for_function("() => document.querySelectorAll("
                                   "'#agent-thread .agent-msg-agent [data-role=run]').length > 1"
                                   " && [...document.querySelectorAll("
                                   "'#agent-thread .agent-msg-agent [data-role=run]')]"
                                   ".pop().offsetParent !== null")
            # The second config's Run here is refused for its budget: said,
            # and not handed to the model.
            page.locator(run).last.click()
            second = page.locator("#agent-thread [data-role=fix]").last
            page.wait_for_function("el => !el.hidden", arg=second.element_handle())
            budget_said = second.text_content()
            has_ask = second.locator(".agent-fix-ask").count()
            browser.close()
    finally:
        session.server.shutdown()
    assert said.startswith("Fix: Change setup.ph.") and code == "setup.ph"
    assert len(asked) == 2
    assert asked[1]["request"].startswith("Running it was refused: ")
    assert "What would fix it: Change `setup.ph`." in asked[1]["request"]
    assert "ph: 7.0" in asked[1]["current_config"]
    assert "budget_hours" in budget_said and has_ask == 0
    assert errors == []
