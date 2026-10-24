"""A fix the software runs is run from the GUI, once the person has said so.

1131 made a stopped study say what would fix it, the command and its price.
The command still had to be typed. A fix that is this software's own
command (`fastmdx resume`, windows run again with `--rerun-window`) now runs
from the Overview's card or by telling the Agent, and only then: the person
sees the command and the price and confirms. The arguments are the ones the
remedy built from the study's record, never text from a request, and a fix
waiting on a choice only the person can make, a setting to change or an
install command is said and never run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.fixes_view import fixes_payload
from tests.test_every_refusal_says_its_fix_and_price import (
    PROTONATION, STOPPED, _config, _manifest, _speed, _stopped_at)


@pytest.fixture
def stopped(tmp_path) -> Path:
    root = tmp_path / "study"
    _config(root)
    _speed(root)
    _stopped_at(root, 100_000)
    _manifest(root, "simulation", STOPPED)
    return root


@pytest.fixture
def undecided(tmp_path) -> Path:
    root = tmp_path / "study"
    _config(root, duration_ns=10.0)
    _manifest(root, "setup", PROTONATION)
    return root


def _runtime(root: Path) -> DashboardRuntime:
    runtime = DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent)
    runtime.active_root = root
    return runtime


class TestWhatThePageIsGiven:
    def test_a_resume_can_be_run_from_here(self, stopped):
        page = fixes_payload(stopped)
        (fix,) = page["fixes"]
        assert page["ok"] and fix["runnable"] and fix["index"] == 0
        assert fix["argv"] == ["resume", str(stopped.resolve())]
        assert fix["price_said"].startswith("300 ps of production")

    def test_a_choice_only_the_person_can_make_is_not(self, undecided):
        (fix,) = fixes_payload(undecided)["fixes"]
        assert fix["decision"] and not fix["runnable"] and fix["argv"] == []

    def test_a_run_of_a_campaign_is_fixed_with_its_campaign(self, tmp_path):
        from fastmdxplora.gui.fixes_view import study_of

        root = tmp_path / "campaign"
        (root / "runs" / "seed1").mkdir(parents=True)
        (root / "batch_manifest.json").write_text("{}", encoding="utf-8")
        assert study_of(root / "runs" / "seed1") == root
        assert study_of(tmp_path / "alone") == tmp_path / "alone"
        assert fixes_payload(None) == {"ok": False, "reason": "No study is open."}


class TestRunningIt:
    def test_the_remedys_own_arguments_are_run_in_the_study(self, stopped):
        runtime = _runtime(stopped)
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            answer = runtime.run_a_fix(0)
        assert answer["ok"] and answer["fix"]["code"] == "simulation.run.stopped"
        command, where, _ = spawn.call_args.args
        assert command == [sys.executable, "-m", "fastmdxplora", "resume",
                           str(stopped.resolve())]
        assert where == stopped

    def test_what_is_not_the_softwares_to_run_is_refused(self, undecided):
        runtime = _runtime(undecided)
        with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            answer = runtime.run_a_fix(0)
        assert not answer["ok"] and answer["error"].startswith("This fix is not a command")
        spawn.assert_not_called()

    def test_nothing_to_fix_and_nonsense_are_refused(self, stopped, tmp_path):
        runtime = _runtime(stopped)
        with mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert not runtime.run_a_fix(3)["ok"]
            assert runtime.run_a_fix("x")["error"] == "Say which fix to run."
            finished = tmp_path / "finished"
            finished.mkdir()
            assert not _runtime(finished).run_a_fix(0)["ok"]
        spawn.assert_not_called()

    def test_not_while_something_runs(self, stopped):
        runtime = _runtime(stopped)
        runtime.process = mock.Mock(poll=mock.Mock(return_value=None))
        with mock.patch.object(DashboardRuntime, "_refresh_process"), \
                mock.patch.object(DashboardRuntime, "_spawn") as spawn:
            assert runtime.run_a_fix(0)["error"] == "A FastMDXplora workflow is already running."
        spawn.assert_not_called()


def test_the_routes_answer_and_run_it(stopped) -> None:
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(stopped), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        page = json.loads(urllib.request.urlopen(base + "/api/fixes", timeout=30).read())
        with mock.patch.object(DashboardRuntime, "_spawn",
                               return_value={"launched": True}) as spawn:
            request = urllib.request.Request(
                base + "/api/fix", data=json.dumps({"index": 0}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": base})
            ran = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        session.server.shutdown()
    assert page["ok"] and page["fixes"][0]["runnable"]
    assert ran["ok"] and spawn.call_args.args[0][-2:] == ["resume", str(stopped.resolve())]


class TestTheAgent:
    def test_it_is_told_which_fix_it_would_run(self, stopped):
        from fastmdxplora.gui.agent_panel import _remedies_summary

        said = _remedies_summary(stopped)
        assert "[runs here] the study's simulation:" in said
        assert "`DO: run the fix` runs" in said

    def test_the_action_carries_the_fix_and_always_asks(self, stopped, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui.agent_panel import propose_endpoint

        monkeypatch.setattr(agent_mod, "completion_for",
                            lambda *a, **k: (lambda prompt: "DO: run the fix"))
        answer = propose_endpoint({"request": "resume it"}, _runtime(stopped))
        assert answer["action"] == "run the fix" and answer["confirm"] is True
        assert answer["fix"]["argv"] == ["resume", str(stopped.resolve())]

    def test_with_nothing_to_run_it_says_so(self, undecided, monkeypatch):
        import fastmdxplora.agent as agent_mod
        from fastmdxplora.gui.agent_panel import propose_endpoint

        monkeypatch.setattr(agent_mod, "completion_for",
                            lambda *a, **k: (lambda prompt: "DO: run the fix"))
        answer = propose_endpoint({"request": "do that"}, _runtime(undecided))
        assert answer["action"] == "run the fix" and answer["fix"] is None


def test_the_overview_card_runs_it_after_asking(stopped) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(stopped), host="127.0.0.1", port=0)
    ran: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def answer(route):
                ran.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(json={"ok": True})
            page.route("**/api/fix", answer)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("#fixes-card:not([hidden]) .fix")
            said = page.text_content("#fixes-card .fix")
            page.click("#fixes-card .fix-run")
            asked = page.text_content("#fixes-card .fix-said")
            assert ran == []            # nothing yet: asked first
            page.click("#fixes-card .fix-confirm")
            page.wait_for_selector("#fixes-card .fix-said:has-text('Started')")
            browser.close()
    finally:
        session.server.shutdown()
    assert "The run was asked to stop" in said and "fastmdx resume" in said
    assert asked.startswith("Run the command above now? It costs 300 ps of production")
    assert said.startswith("The study's simulation: ")
    assert ran == [{"index": 0}]
    assert errors == []


def _drawn_again(page) -> None:
    """The card drawn again as a study change draws it: the fixes asked for
    again and drawn afresh."""
    page.evaluate("document.querySelectorAll('#fixes-card .fix')"
                  ".forEach(f => f.dataset.old = '1')")
    page.evaluate("window.dispatchEvent(new CustomEvent('dashboard:run-changed'))")
    page.wait_for_selector("#fixes-card .fix:not([data-old])", state="attached")


def _has_keyboard(page) -> str:
    """The control with the keyboard, with the index of its fix."""
    return page.evaluate(
        "(() => { const a = document.activeElement;"
        " const item = a.closest('.fix');"
        " const at = [...document.querySelectorAll('#fixes-card .fix')].indexOf(item);"
        " return at + ' ' + a.className; })()")


def _opened(stopped, also=None):
    """The Overview of the stopped study in a browser, its fixes as the
    server gives them (and `also`, a second runnable fix, when given)."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(stopped), host="127.0.0.1", port=0)
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_default_timeout(60000)
    page.ran, page.errors = [], []
    page.on("pageerror", lambda error: page.errors.append(str(error)))

    def run(route):
        page.ran.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(json={"ok": True})

    def close():
        browser.close()
        pw.stop()
        session.server.shutdown()
    try:
        page.route("**/api/fix", run)
        _routed(page, also)
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_selector("#fixes-card:not([hidden]) .fix .fix-run")
    except BaseException:
        close()
        raise
    return page, close


def _routed(page, also) -> None:
    if also:
        def fixes(route):
            given = route.fetch().json()
            given["fixes"].append(dict(given["fixes"][0], index=1, **also))
            route.fulfill(json=given)
        page.route("**/api/fixes", fixes)


def test_a_question_asked_stays_when_the_card_is_drawn_again(stopped) -> None:
    """The card is drawn again whenever the study changes, the first time a
    moment after the page loads. A Run pressed in that moment had its
    question drawn away, and Yes with it (CI on a5bcec3a: 60 s waiting for
    Yes). The keyboard stays where it was: on Not now it is not moved to
    Yes, where Enter would start what the person was declining."""
    pytest.importorskip("playwright.sync_api")
    page, close = _opened(stopped)
    try:
        page.click("#fixes-card .fix-run")
        _drawn_again(page)
        asked = page.query_selector("#fixes-card .fix-said")
        asked = asked.text_content() if asked else ""
        on_yes = _has_keyboard(page)
        page.focus("#fixes-card .fix-cancel")
        _drawn_again(page)
        on_no = _has_keyboard(page)
        # Not now is the person's answer: Run is offered, and kept so.
        page.click("#fixes-card .fix-cancel")
        after_no = _has_keyboard(page)
        _drawn_again(page)
        offered = _has_keyboard(page)
        page.click("#fixes-card .fix-run")
        page.click("#fixes-card .fix-confirm")
        page.wait_for_selector("#fixes-card .fix-said:has-text('Started')")
        # Started is what it says, not Run it again.
        _drawn_again(page)
        started = page.text_content("#fixes-card .fix-actions")
        run_again = page.query_selector("#fixes-card .fix-run")
        ran, errors = page.ran, page.errors
    finally:
        close()
    assert asked.startswith("Run the command above now?")
    assert on_yes == "0 primary-btn fix-confirm"
    assert on_no == "0 ghost-btn fix-cancel"
    assert after_no == offered == "0 primary-btn fix-run"
    assert started.startswith("Started.") and run_again is None
    assert ran == [{"index": 0}]
    assert errors == []


def test_each_question_is_kept_with_its_own_fix(stopped) -> None:
    """Two fixes, both asked: drawn again, both questions stay, and the
    keyboard stays on the first fix's Yes, not the second's."""
    pytest.importorskip("playwright.sync_api")
    page, close = _opened(stopped, also={"where": "the other run"})
    try:
        # Found again at each step: the page draws the card again itself
        # once, a moment after it loads.
        fix = page.locator("#fixes-card .fix")
        fix.nth(0).locator(".fix-run").click()
        fix.nth(1).locator(".fix-run").click()
        fix.nth(0).locator(".fix-confirm").focus()
        _drawn_again(page)
        questions = len(page.query_selector_all("#fixes-card .fix-confirm"))
        on = _has_keyboard(page)
        page.keyboard.press("Enter")
        page.wait_for_selector("#fixes-card .fix-said:has-text('Started')")
        ran, errors = page.ran, page.errors
    finally:
        close()
    assert questions == 2
    assert on == "0 primary-btn fix-confirm"
    assert ran == [{"index": 0}]
    assert errors == []


def test_the_agent_asks_then_runs_it(stopped, monkeypatch) -> None:
    pytest.importorskip("playwright.sync_api")
    import tempfile

    from playwright.sync_api import sync_playwright

    import fastmdxplora.agent as agent_mod
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    monkeypatch.setattr(agent_mod, "completion_for",
                        lambda *a, **k: (lambda prompt: "DO: run the fix"))
    session = start_dashboard_session(output=str(stopped), host="127.0.0.1", port=0)
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
            page.route("**/api/fix", answer)
            page.goto(session.url + "#agent", wait_until="domcontentloaded")
            # Enter sends nothing until the page has read its study and thread
            # (CI-774 L12): pressed sooner, the words stay in the box, unsent.
            page.wait_for_function(
                "() => window.FastMDXAgentPanel && !window.FastMDXAgentPanel.waits", timeout=30000)
            page.fill("#agent-request", "resume it")
            page.keyboard.press("Enter")
            page.wait_for_selector("#agent-thread .agent-confirm")
            asked = page.locator("#agent-thread .agent-confirm-q").last.text_content()
            price = page.locator("#agent-thread .agent-confirm-facts").last.text_content()
            assert ran == []
            page.fill("#agent-request", "yes")
            page.keyboard.press("Enter")
            # Started, and the Overview opened to follow it.
            page.wait_for_selector("#agent-thread .agent-attempt:has-text('Started')",
                                   state="attached")
            page.wait_for_selector('.page[data-page="overview"]:not([hidden])')
            browser.close()
    finally:
        session.server.shutdown()
    assert asked == f"Run fastmdx resume {stopped.resolve()}?"
    assert price == ("It costs 300 ps of production; at this study's own speed on CUDA, "
                     "about 15 min.")
    assert ran == [{"index": 0}]
    assert errors == []
