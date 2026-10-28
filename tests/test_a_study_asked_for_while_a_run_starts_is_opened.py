"""What is asked while a run the Agent started is being launched lands where
it was asked for (CI-774, second round).

Found by review K1: another study's conversation opened during a launch was
opened by the server at once, then the launch switched the GUI to the run's
folder, and the page opened whatever study the GUI had by then: the run's.
Found by review K2: words typed into the Agent while the run started were
split, their first part kept for the study left.
"""

from __future__ import annotations

import json

import pytest

from tests.test_a_conversation_follows_a_run_that_wrote_nothing import (  # noqa: F401
    _CONFIG,
    _browsers_closed,
    _gui,
    _page_on,
    _ready,
    _say,
    _slow_disk,
)

pytest.importorskip("playwright.sync_api")


def _other_study(workspace, name="other", cid="conv-20261008-000000-000000"):
    other = workspace / name
    store = other / "agent" / "conversations"
    store.mkdir(parents=True)
    (other / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    (store / f"{cid}.json").write_text(json.dumps(
        {"version": 3, "id": cid,
         "entries": [{"eid": "o", "role": "user", "text": f"about {name}"}]}), encoding="utf-8")
    return other


def test_another_study_opened_while_a_run_starts_is_the_one_opened(tmp_path, monkeypatch):
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=1.5)
    other = _other_study(workspace)
    asked = []
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        # These two only: the run's summary (`agent/run-summary`), asked as
        # it starts, is not the run.
        page.on("request", lambda r: asked.append(r.url.split("/api/")[-1].split("?")[0])
                if r.url.split("/api/")[-1].split("?")[0] in (
                    "agent/run", "agent/conversation/open") else None)
        _slow_disk(monkeypatch, 1.0)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-conversations")
        page.wait_for_selector("#agent-conv-list:not([hidden])")
        page.evaluate("(where) => { window.__opened = window.FastMDXAgentPanel.open("
                      "'conv-20261008-000000-000000', where, false); }", str(other))
        said = page.evaluate("() => (document.querySelector("
                             "'#agent-conv-list .agent-conv-held') || {}).textContent || ''")
        page.wait_for_timeout(9000)
        status = page.evaluate("async () => (await (await fetch('/api/status')).json()).study")
        shown = page.evaluate("() => window.FastMDXDashboard.state.studyShown")
        address = page.evaluate("() => new URL(location.href).searchParams.get('study')")
        close()
    finally:
        session.server.shutdown()
    assert made
    assert status == shown == address == study_key(other), (status, shown, address)
    # The whole open waited for the launch: asked of the server only then.
    assert asked[:2] == ["agent/run", "agent/conversation/open"], asked
    assert said == "Opens once the run has started."
    assert errors == []


def test_an_open_waiting_with_the_list_closed_is_said(tmp_path, monkeypatch):
    """Found by review K3 (third round): with the list of conversations
    closed, an open asked during a launch waited and said nothing, and a
    second open dropped the first with an error no one was shown."""
    from fastmdxplora.gui.workspace import study_key

    from tests.test_a_notice_waits_for_what_undo_says import _HEARD

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=4.0)
    other = _other_study(workspace)
    third = _other_study(workspace, "third", "conv-20261008-000000-000001")
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG)])
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_timeout(800)
        page.evaluate(_HEARD)
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        listed = page.is_visible("#agent-conv-list")
        page.evaluate("(where) => { window.__first = window.FastMDXAgentPanel.open("
                      "'conv-20261008-000000-000000', where, false); }", str(other))
        page.wait_for_timeout(300)
        page.evaluate("(where) => { window.FastMDXAgentPanel.open("
                      "'conv-20261008-000000-000001', where, false); }", str(third))
        first = page.evaluate("() => window.__first")
        said = page.evaluate("() => window.__said")
        # The study asked for last is opened (the page loads it).
        page.wait_for_function("(key) => window.FastMDXDashboard"
                               " && window.FastMDXDashboard.state.studyShown === key",
                               arg=study_key(third), timeout=30000)
        close()
    finally:
        session.server.shutdown()
    assert made and not listed
    assert first == {"ok": False, "error": "Another conversation was asked for."}
    assert said[:2] == [
        "The conversation opens once the run has started.",
        "The conversation asked for last opens once the run has started, not the one before."], said
    assert errors == []


def _unsent(page):
    return page.evaluate("""() => {
        const kept = {};
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (key.startsWith('fmx.agentUnsent:')) {
                kept[key.slice('fmx.agentUnsent:'.length)] = JSON.parse(sessionStorage.getItem(key)).words;
            }
        }
        return kept;
    }""")


#: When the words are typed after Run: all before the page sees the switch
#: to the run's study; half before it and half after; all after it.
MOMENTS = ("before the switch", "across the switch", "after it")


@pytest.mark.parametrize("moment", MOMENTS)
def test_words_typed_while_a_run_starts_go_with_it_whole(tmp_path, monkeypatch, moment):
    import threading

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=2.0)
    second_asked, release_second = threading.Event(), threading.Event()
    launch = DashboardRuntime.launch_from_config

    def held_launch(runtime, *args, **kwargs):
        # A loaded runner can take longer to type than the fake launch's
        # delay. Hold the switch until the intended words have been typed.
        if len(made) == 1:
            second_asked.set()
            assert release_second.wait(30), "The second launch was not released"
        return launch(runtime, *args, **kwargs)

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", held_launch)
    words = "is it going well so far"
    shown = "() => window.FastMDXDashboard.state.studyShown"
    try:
        page, errors, close = _page_on(session, [dict(_CONFIG), dict(_CONFIG)])
        # A first run, so the page is on a study the second one leaves.
        _say(page, "chignolin")
        page.wait_for_selector("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run]")
        page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                               arg=study_key(workspace / "run1"), timeout=30000)
        page.wait_for_selector("#agent-thread .agent-running")
        _ready(page)
        _say(page, "again")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
        page.wait_for_timeout(500)
        left, run = study_key(workspace / "run1"), study_key(workspace / "run2")
        switched = f"() => window.FastMDXDashboard.state.studyShown === '{run}'"
        page.click("#agent-thread .agent-study:not([hidden]) [data-role=run] >> nth=-1")
        assert second_asked.wait(30), "The second launch was not asked for"
        page.focus("#agent-request")
        seen = []
        if moment == "before the switch":
            page.keyboard.type(words)
            seen.append(page.evaluate(shown))
            release_second.set()
        elif moment == "across the switch":
            page.keyboard.type(words[:10])
            seen.append(page.evaluate(shown))
            release_second.set()
            page.wait_for_function(switched, timeout=30000, polling=10)
            page.keyboard.type(words[10:])
        else:
            release_second.set()
            page.wait_for_function(switched, timeout=30000, polling=10)
            page.keyboard.type(words)
        page.wait_for_function(switched, timeout=30000)
        page.wait_for_selector("#agent-thread .agent-running >> nth=1", timeout=30000)
        page.wait_for_timeout(1000)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        close()
    finally:
        release_second.set()
        session.server.shutdown()
    # Typed, all or the first part, while the page showed the study left.
    assert seen == ([] if moment == "after it" else [left]), seen
    assert box == words
    assert kept.get(run) == words, kept
    assert not kept.get(left), kept
    assert errors == []
