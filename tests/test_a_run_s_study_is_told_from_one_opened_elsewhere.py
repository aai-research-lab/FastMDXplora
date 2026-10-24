"""A run the Agent started opens its own study; any other study opened
meanwhile is said as opened elsewhere (CI-774, fourth round).

Found by reviews K5 and K6: the first change of study in the minute after a
launch was taken as the run's own, whichever study it was, and a refused
launch left that minute open; a page that never saw the run's study kept
the run's thread and wrote in it; and with two launches, the first run's
study judged emptied the second's.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

import pytest

from tests.test_a_conversation_follows_a_run_that_wrote_nothing import (  # noqa: F401
    _CONFIG,
    _a_study_opened_again_as_its_run_starts,
    _answer,
    _browsers_closed,
    _entries_everywhere,
    _gui,
    _launches,
    _page_on,
    _say,
)
from tests.test_open_studies_have_tabs import _builder, _open, browser, site  # noqa: F401

#: Each thing the page's notice says, in order, as it is shown.
_HEARD = """() => {
    window.__said = [];
    const note = () => {
        const toast = document.getElementById('dashboard-toast');
        const said = toast && toast.querySelector('.dashboard-toast-said');
        const text = toast && toast.classList.contains('show') && said ? said.textContent : '';
        if (text && window.__said[window.__said.length - 1] !== text) window.__said.push(text);
    };
    new MutationObserver(note).observe(document.body, {subtree: true, childList: true,
        characterData: true, attributes: true});
}"""

_RUN = "#agent-thread .agent-study:not([hidden]) [data-role=run]"


def _switch_elsewhere(session, folder):
    """Another window (or an AI app) opens ``folder``."""
    base = session.url.split("#")[0].rstrip("/")
    asked = urllib.request.Request(base + "/api/explore/switch", method="POST",
                                   data=json.dumps({"folder": str(folder)}).encode(),
                                   headers={"content-type": "application/json"})
    with urllib.request.urlopen(asked, timeout=30) as answer:
        assert json.loads(answer.read()).get("ok") is not False


def _study(folder):
    (folder / "simulation").mkdir(parents=True)
    (folder / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    return folder


def _shows(page, key, timeout=30000):
    page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                           arg=key, timeout=timeout)


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


def _elsewhere_said(page):
    return [s for s in page.evaluate("() => window.__said") if "opened elsewhere" in s]


def test_a_run_s_study_never_seen_is_said_and_not_taken_as_this_window_s(tmp_path, monkeypatch):
    """K6 M1: another window opens the study the run was started from again
    as the run starts. The page says where the run's thread is; the run's
    study, opened later, is said as opened elsewhere and takes no words
    typed here."""
    from fastmdxplora.gui.workspace import study_key

    more = "typed after the run's thread went"

    def then(page, session, workspace):
        here, run = workspace / "here", workspace / "run1"
        page.evaluate(_HEARD)
        page.fill("#agent-request", more)
        page.wait_for_timeout(500)
        _switch_elsewhere(session, run)
        _shows(page, study_key(run))
        page.wait_for_function("() => window.__said.some((s) => /opened elsewhere/.test(s))",
                               timeout=15000)
        page.wait_for_timeout(1000)
        return {"said": _elsewhere_said(page), "box": page.input_value("#agent-request"),
                "kept": _unsent(page), "here": study_key(here), "run": study_key(run)}

    shown, workspace, words = _a_study_opened_again_as_its_run_starts(tmp_path, monkeypatch,
                                                                       then=then)
    assert "The run's conversation is in the run's study" in shown["note"], shown["note"]
    after = shown["then"]
    assert len(after["said"]) == 1, after["said"]
    assert more not in after["box"]
    assert after["kept"].get(after["here"]) == more, after["kept"]
    assert more not in after["kept"].get(after["run"], ""), after["kept"]
    assert shown["errors"] == []


@pytest.mark.parametrize("how", ["seen launching", "refused", "own"])
def test_a_study_opened_elsewhere_after_a_launch_is_said(tmp_path, monkeypatch, how):
    """K6 L1 and K5 2: a study another window opens while a run starts, and
    seen before the run's, is said as opened elsewhere; so is one opened
    after a launch that was refused. The run's own study is not."""
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import study_key

    pytest.importorskip("playwright.sync_api")
    import tempfile

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    here = _study(workspace / "here")
    other = _study(workspace / "other")
    made = _launches(monkeypatch, workspace, taking=4.0)
    session = start_dashboard_session(output=str(here), host="127.0.0.1", port=0)
    held, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        if how == "refused":
            page.route("**/api/agent/run", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": False, "error": "Not started here."})))
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.evaluate(_HEARD)
        if how == "seen launching":
            # The page reads no status while the GUI is on the run's folder.
            page.route("**/api/status*", lambda route: held.append(route))
        page.click(_RUN)
        if how == "seen launching":
            time.sleep(1.0)
            _switch_elsewhere(session, other)
            page.unroute("**/api/status*")
            for route in list(held):
                route.continue_()
            _shows(page, study_key(other))
        while not answered:
            page.wait_for_timeout(100)
        if how == "refused":
            page.wait_for_timeout(500)
            _switch_elsewhere(session, other)
            _shows(page, study_key(other))
        if how == "own":
            _shows(page, study_key(workspace / "run1"))
            page.wait_for_timeout(3000)
        else:
            page.wait_for_function(
                "() => window.__said.some((s) => /opened elsewhere/.test(s))", timeout=15000)
            page.wait_for_timeout(1000)
        said = _elsewhere_said(page)
        title = page.text_content("#topbar-run-title")
    finally:
        close()
        session.server.shutdown()
    if how == "own":
        assert said == [] and made
    else:
        assert said == [f"{title} was opened elsewhere, in another window or by an AI app: "
                        "this window now shows it."], said
    assert errors == []


def test_words_follow_the_thread_to_the_second_of_two_runs(tmp_path, monkeypatch):
    """K5 1: run A answered and not yet seen, a message asking for run B is
    not sent (Send waits, saying so) until the page has read which study is
    open (it sees A) and the person sends it; B is started, and the words
    typed go with the thread to B, and are kept for B."""
    from fastmdxplora.gui.workspace import study_key

    workspace, made, session = _gui(tmp_path, monkeypatch, taking=0.5)
    words = "words for the thread"
    statuses, runs, later = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), dict(_CONFIG), dict(_CONFIG)])
    try:
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        page.wait_for_timeout(800)
        page.click(_RUN)
        run1, run2, run3 = (study_key(workspace / f"run{i}") for i in (1, 2, 3))
        _shows(page, run1)
        page.wait_for_selector("#agent-thread .agent-running")
        _when_known(page)
        _say(page, "again")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
        page.wait_for_timeout(800)
        # The page does not see run A (run2) yet.
        page.route("**/api/status*", lambda route: statuses.append(route))
        page.click(f"{_RUN}:visible >> nth=-1")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-running').length >= 2",
            timeout=30000)
        _say(page, "and again")
        page.wait_for_timeout(1500)
        # Waits: the thread went to run A, which the page has not seen.
        held = _note(page)
        cards = page.locator("#agent-thread .agent-study:not([hidden])").count()
        assert page.evaluate("() => window.FastMDXDashboard.state.studyShown") == run1
        page.unroute("**/api/status*")
        for route in list(statuses):
            route.continue_()
        _shows(page, run2)
        _send_when_offered(page)
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 3")
        page.wait_for_timeout(800)
        page.fill("#agent-request", words)
        page.wait_for_timeout(500)
        page.route("**/api/status*", lambda route: later.append(route))
        page.route("**/api/agent/run", lambda route: runs.append(route))
        page.click(f"{_RUN}:visible >> nth=-1")
        while not runs:
            page.wait_for_timeout(100)
        page.unroute("**/api/agent/run")
        for route in list(runs):
            route.continue_()
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-running').length >= 3",
            timeout=30000)
        page.wait_for_timeout(300)
        page.unroute("**/api/status*")
        for route in list(later):
            route.continue_()
        _shows(page, run3)
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert len(made) == 3
    assert cards == 2 and held == _SEND_WAITS, (cards, held)
    assert words in box and kept.get(run3) == words, (box, kept, note)
    assert errors == []


def _here_and_other(tmp_path, monkeypatch, taking=4.0):
    import tempfile

    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    here = _study(workspace / "here")
    other = _study(workspace / "other")
    made = _launches(monkeypatch, workspace, taking=taking)
    session = start_dashboard_session(output=str(here), host="127.0.0.1", port=0)
    return workspace, here, other, made, session


def _when_known(page):
    """Once the page has read which study is open after a launch: Send is
    offered then, not while the run starts (CI-774 round 3)."""
    page.wait_for_function("() => { const d = window.FastMDXDashboard;"
                           " return d.studyKnown() && d.viewKnown()"
                           " && !window.FastMDXAgentPanel.waits; }", timeout=30000)
    page.wait_for_timeout(300)


def _note(page):
    return page.evaluate("() => (document.getElementById('agent-unsent-note') || {}).textContent || ''")


def test_a_study_seen_after_the_answer_leaves_no_run_s_study_this_window_s(tmp_path, monkeypatch):
    """L1, order A: another window opens `other` during the launch and the
    page sees it only after the answer; another window then opens the run's
    study. It is said as opened elsewhere, and the words typed for `other`
    stay kept for `other`."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    run = workspace / "run1"
    held, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.evaluate("""() => { window.__else = [];
            window.addEventListener('dashboard:study-changed-elsewhere',
                                    (e) => window.__else.push(e.detail.to)); }""")
        page.route("**/api/status*", lambda route: held.append(route))
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type("typed during launch")
        time.sleep(1.0)
        _switch_elsewhere(session, other)
        while not answered:
            page.wait_for_timeout(100)
        page.wait_for_timeout(300)
        page.unroute("**/api/status*")
        for route in list(held):
            route.continue_()
        _shows(page, study_key(other))
        page.wait_for_timeout(2000)
        page.fill("#agent-request", "typed for other")
        page.wait_for_timeout(600)
        _switch_elsewhere(session, run)
        _shows(page, study_key(run))
        page.wait_for_function("() => window.__else.length >= 2", timeout=15000)
        page.wait_for_timeout(1000)
        said = page.evaluate("() => window.__else")
        box = page.input_value("#agent-request")
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    assert made
    assert said == [study_key(other), study_key(run)], said
    assert "typed for other" not in box
    assert kept.get(study_key(other)) == "typed for other", kept
    assert "typed for other" not in kept.get(study_key(run), ""), kept
    assert errors == []


def test_the_first_of_two_runs_never_seen_is_said_when_opened_elsewhere(tmp_path, monkeypatch):
    """L1, order B: the Agent's run A (run1) answered and not yet seen, the
    Config Builder's run B (run2) started; the page sees only B. Another
    window then opens A: said as opened elsewhere, and the words typed in
    B are kept for B, not taken to A."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.5)
    statuses, answered, built = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        page.on("response", lambda r: built.append(r) if r.url.endswith("/api/run") else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.evaluate("""() => { window.__else = [];
            window.addEventListener('dashboard:study-changed-elsewhere',
                                    (e) => window.__else.push(e.detail.to)); }""")
        # The page does not see run A until B has started.
        page.route("**/api/status*", lambda route: statuses.append(route))
        page.click(_RUN)
        while not answered:
            page.wait_for_timeout(100)
        _builder(page, {"root": workspace})
        page.click("#run-start-button")
        while not built:
            page.wait_for_timeout(100)
        page.unroute("**/api/status*")
        for route in list(statuses):
            route.continue_()
        run_a, run_b = workspace / "run1", workspace / "run2"
        _shows(page, study_key(run_b))
        page.wait_for_timeout(1500)
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.fill("#agent-request", "typed in B")
        page.wait_for_timeout(600)
        _switch_elsewhere(session, run_a)
        _shows(page, study_key(run_a))
        page.wait_for_function("() => window.__else.length >= 1", timeout=15000)
        page.wait_for_timeout(1000)
        said = page.evaluate("() => window.__else")
        box = page.input_value("#agent-request")
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    assert len(made) == 2
    assert said == [study_key(run_a)], said
    assert kept.get(study_key(run_b)) == "typed in B", kept
    assert "typed in B" not in box
    assert errors == []


@pytest.mark.parametrize("sent", ["during the launch", 0.3, 1.5])
def test_a_message_sent_as_the_run_starts_is_answered_in_the_study_shown(tmp_path, monkeypatch,
                                                                          sent):
    """L1 M2 and L2 M1: another window opens the start study again as the
    run starts, and the message is sent while the run starts or just after
    it answers, before the page's next status. Send waits, saying so; once
    the page knows, the person sends it, and it is answered with the study
    shown open and kept in that study's thread, never in the run's."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "words typed as it starts"
    held, answered, asked = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Said here.")])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        page.on("request", lambda r: asked.append(json.loads(r.post_data or "{}"))
                if "/api/agent/propose" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.route("**/api/status*", lambda route: held.append(route))
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type(words)
        time.sleep(1.0)
        _switch_elsewhere(session, here)
        page.unroute("**/api/status*")
        released = list(held)
        for route in released:
            route.continue_()
        if sent == "during the launch":
            page.wait_for_timeout(1200)
            assert not answered
        else:
            while not answered:
                page.wait_for_timeout(20)
            # No status read comes before the send.
            page.route("**/api/status*", lambda route: held.append(route))
            page.wait_for_timeout(int(sent * 1000))
        page.focus("#agent-request")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        waited = (_note(page) if page.input_value("#agent-request") else "sent at once") \
            if sent == "during the launch" else ""
        if sent != "during the launch":
            page.wait_for_timeout(1000)
            page.unroute("**/api/status*")
            for route in held[len(released):]:
                route.continue_()
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Said here.')", timeout=30000)
        page.wait_for_timeout(1500)
        thread = page.inner_text("#agent-thread")
    finally:
        close()
        session.server.shutdown()
    kept = _entries_everywhere(workspace)
    in_here = [e.get("text") for path, entries in kept.items()
               if "here/agent" in path for e in entries]
    in_run = [e.get("text") for path, entries in kept.items()
              if "run1/agent" in path for e in entries]
    runs = [path.rsplit("/", 1)[1][:-5] for path in kept if "run1/agent" in path]
    assert made and runs
    assert words not in in_run and "Said here." not in in_run, kept
    assert words in in_here and "Said here." in in_here, kept
    assert "Said here." in thread and "chignolin" not in thread
    # While the launch is unanswered Send waits; after it, the message is
    # sent at once or when the person sends it again, into the study
    # shown's own thread only (asserted below).
    assert sent != "during the launch" or waited == _SEND_WAITS, waited
    assert [a.get("conversation") for a in asked if a.get("request") == words
            and a.get("conversation") in runs] == []
    assert errors == []


def test_words_typed_for_the_run_s_thread_follow_it_past_another_study(tmp_path, monkeypatch):
    """L2 L1: words typed after Run into the thread the run took with it,
    another study seen first: they are kept for the run's study, where the
    thread went, not for the study the run was started from."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "words for the run's thread"
    held, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.route("**/api/status*", lambda route: held.append(route))
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type(words)
        time.sleep(1.0)
        _switch_elsewhere(session, other)
        page.unroute("**/api/status*")
        for route in list(held):
            route.continue_()
        _shows(page, study_key(other))
        while not answered:
            page.wait_for_timeout(100)
        page.wait_for_timeout(2000)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    run = study_key(workspace / "run1")
    assert made
    assert kept.get(run) == words, kept
    assert words not in kept.get(study_key(here), ""), kept
    assert words not in box
    assert "saved for it, not put here" in note, note
    assert errors == []


#: Every request of the dashboard's poll: held together, the page reads
#: no part of a poll without the others (CI-774 review L3).
_POLL = re.compile(r"/api/(app-state|status|metrics|events|results|structure-info|frames-info)(\?|$)")


def _hold(page, held):
    page.route(_POLL, lambda route: held.append(route))


def _release(page, held):
    page.unroute(_POLL)
    for route in list(held):
        route.continue_()
    del held[:]


def _texts(workspace, part):
    kept = _entries_everywhere(workspace)
    return [e.get("text") for path, entries in kept.items() if part in path for e in entries]


def _asking(page, asked):
    page.on("request", lambda r: asked.append(json.loads(r.post_data or "{}"))
            if "/api/agent/propose" in r.url else None)


def _events(page):
    page.evaluate("""() => { window.__else = [];
        window.addEventListener('dashboard:study-changed-elsewhere',
                                (e) => window.__else.push(e.detail.to)); }""")


#: Said beside the box while Send waits (CI-774 round 3: a message held to
#: be sent later by itself was lost, sent twice or sent elsewhere).
_SEND_WAITS = "Send waits until the run has started and this page shows which study is open."

_OFFERED = "() => document.getElementById('agent-propose').getAttribute('aria-disabled') === 'false'"


def _send_offered(page):
    return page.evaluate(_OFFERED)


def _send_when_offered(page, timeout=30000):
    """Once the page knows where a message would be answered, Send is
    offered again, and the person presses Enter on what is in the box (if
    the Enter before sent it, nothing is left to send)."""
    page.wait_for_function("() => (" + _OFFERED + ")()"
                           " || !document.getElementById('agent-request').value.trim()",
                           timeout=timeout)
    if page.input_value("#agent-request").strip():
        page.focus("#agent-request")
        page.keyboard.press("Enter")


def _requests(asked):
    return [a.get("request") for a in asked]


def _typed_and_entered_during_a_launch(page, here, words):
    """"chignolin" answered with a config; Run pressed; `words` typed and
    Enter pressed while the launch goes on: Send waits, and says so."""
    from fastmdxplora.gui.workspace import study_key

    _say(page, "chignolin")
    page.wait_for_selector(_RUN)
    _shows(page, study_key(here))
    page.wait_for_timeout(800)
    page.click(_RUN)
    page.focus("#agent-request")
    page.keyboard.type(words)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    return {"note": _note(page), "offered": _send_offered(page),
            "box": page.input_value("#agent-request")}


def test_a_run_s_study_passed_while_launching_is_not_this_window_s(tmp_path, monkeypatch):
    """L3 2, order A: the GUI goes to run1, then another window opens
    `other`, seen while the launch is unanswered. After the answer the page
    reads `other` again; the person types for it; another window opens run1.
    It is said as opened elsewhere, and the words stay kept for `other`."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=4.0)
    run = workspace / "run1"
    held, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _events(page)
        _hold(page, held)
        page.click(_RUN)
        time.sleep(1.0)
        _switch_elsewhere(session, other)
        _release(page, held)
        _shows(page, study_key(other))
        assert not answered
        while not answered:
            page.wait_for_timeout(100)
        page.wait_for_timeout(4000)
        page.fill("#agent-request", "typed for other")
        page.wait_for_timeout(600)
        _switch_elsewhere(session, run)
        _shows(page, study_key(run))
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
        box = page.input_value("#agent-request")
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    assert made
    assert said == [study_key(other), study_key(run)], said
    assert "typed for other" not in box
    assert kept.get(study_key(other)) == "typed for other", kept
    assert errors == []


def test_an_agent_launch_answered_after_a_builder_launch_leaves_no_run_ahead(tmp_path,
                                                                           monkeypatch):
    """L3 2, order B: the Agent's Run (GUI to run1, answered after 4 s),
    then the Config Builder's Start (GUI to run2, answered at once). The
    page sees only run2. Another window then opens run1: said as opened
    elsewhere."""
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.0)
    takes = [4.0, 0.3]

    def launch(self, state, *, dashboard_url=None, config=None):
        folder = workspace / f"run{len(made) + 1}"
        folder.mkdir()
        (folder / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        made.append(str(folder))
        self.active_root = folder
        time.sleep(takes[len(made) - 1])
        return {"ok": True, "output": str(folder)}

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", launch)
    held, answered, built = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        page.on("response", lambda r: built.append(r) if r.url.endswith("/api/run") else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _events(page)
        _hold(page, held)
        page.click(_RUN)
        while not made:
            time.sleep(0.05)
        _builder(page, {"root": workspace})
        page.click("#run-start-button")
        while not (built and answered):
            page.wait_for_timeout(100)
        _release(page, held)
        run1, run2 = workspace / "run1", workspace / "run2"
        _shows(page, study_key(run2))
        page.wait_for_timeout(2000)
        _switch_elsewhere(session, run1)
        _shows(page, study_key(run1))
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
    finally:
        close()
        session.server.shutdown()
    assert len(made) == 2
    assert said == [study_key(run1)], said
    assert errors == []


def test_a_study_opened_and_gone_past_as_the_run_starts_is_said(tmp_path, monkeypatch):
    """L3 5: Run pressed; before the run's switch, another window opens
    `other`; the page sees `other`, then the run's study, both while the
    launch is unanswered. `other`'s opening is said; the run's is not."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=6.0)
    runs, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _events(page)
        page.evaluate(_HEARD)
        page.route("**/api/agent/run", lambda route: runs.append(route))
        page.click(_RUN)
        while not runs:
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(500)
        runs[0].continue_()
        page.unroute("**/api/agent/run")
        _shows(page, study_key(workspace / "run1"))
        assert not answered
        while not answered:
            page.wait_for_timeout(100)
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
        heard = page.evaluate("() => window.__said")
    finally:
        close()
        session.server.shutdown()
    assert made
    assert said == [study_key(other)], said
    assert [h for h in heard if "was opened elsewhere as the run started" in h], heard
    assert errors == []


def test_words_kept_for_a_study_are_put_back_when_it_is_opened_again(tmp_path, monkeypatch):
    """L5 1 and L6 H1, with no launch: words typed on `here`, another window
    opens `other` (the words kept for `here`); words typed on `other`,
    another window opens `here` again. `here`'s words are back in its box,
    and `other`'s are kept for it: they were erased as the box emptied. The
    two are of one system, and the note names the study by its folder
    alone (L6 L1, L7 6, L16)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    for study in (here, other):
        (study / "manifest.json").write_text(json.dumps({"system": "tri-ala.pdb"}),
                                             encoding="utf-8")
    page, errors, close = _page_on(session, [])
    try:
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.fill("#agent-request", "for here")
        page.wait_for_timeout(500)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(1000)
        note = _note(page)
        page.fill("#agent-request", "for other")
        page.wait_for_timeout(500)
        _switch_elsewhere(session, here)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    assert box == "for here", (box, kept)
    assert kept.get(study_key(other)) == "for other", kept
    # Named by its folder alone, never by its title as the switch is seen,
    # which may be its system's before its run's name is read (L16).
    assert "The unsent message typed for here is saved for it" in note, note
    assert "tri-ala" not in note.lower(), note
    assert errors == []


def test_the_read_asked_at_a_launch_s_answer_counts_as_after_it(tmp_path, monkeypatch):
    """L5 3 and L6 M4: the status read asked as a launch answers is asked in
    the same millisecond; counted in polls, it is one asked after the
    answer, and the study is known once it is applied."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    page, errors, close = _page_on(session, [])
    try:
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        known = []
        for _ in range(3):
            page.evaluate("""() => { const d = window.FastMDXDashboard;
                d.studyChangeAsked(); d.studyChangeAnswered(""); }""")
            page.wait_for_timeout(1500)
            known.append(page.evaluate("() => window.FastMDXDashboard.studyKnown()"))
    finally:
        close()
        session.server.shutdown()
    assert known == [True, True, True], known
    assert errors == []


def _stale_status(page, stale, later=None):
    """The next status read is answered as the server reads it now, and
    handed to the page only when the test says (`stale[0][0].fulfill`);
    with `later`, the reads after it are held there too."""
    def hold(route):
        if stale and later is not None:
            later.append(route)
        elif stale:
            route.continue_()
        else:
            stale.append((route, route.fetch()))

    page.route("**/api/status*", hold)
    page.evaluate("() => document.getElementById('refresh-now').click()")
    while not stale:
        page.wait_for_timeout(50)


def test_a_read_of_another_study_asked_before_the_answer_leaves_the_run_this_window_s(
        tmp_path, monkeypatch):
    """L6 M3: Run pressed; another window opens `other` before the run's
    switch; the page's read of `other`, asked then, is applied after the
    answer. `other` is said as opened elsewhere; the run's own study, seen
    next, is not."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.0)
    runs, stale, answered, later = [], [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _events(page)
        page.route("**/api/agent/run", lambda route: runs.append(route))
        page.click(_RUN)
        while not runs:
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        _stale_status(page, stale, later)
        runs[0].continue_()
        page.unroute("**/api/agent/run")
        while not answered:
            page.wait_for_timeout(50)
        page.wait_for_timeout(200)
        # The read of `other`, asked before the answer, is applied first;
        # the reads asked at the answer after it.
        stale[0][0].fulfill(response=stale[0][1])
        page.wait_for_timeout(500)
        page.unroute("**/api/status*")
        for route in later:
            route.continue_()
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
    finally:
        close()
        session.server.shutdown()
    assert made
    assert said == [study_key(other)], said
    assert errors == []


def test_an_unreadable_answer_takes_the_run_s_study_seen_last_as_its_own(tmp_path, monkeypatch):
    """L5 4: Run pressed; another window opens `other`, which the page sees;
    the run starts (the GUI goes to run1, which the page sees) and its answer
    cannot be read. `other` is said as opened elsewhere; run1 is not."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.0)
    runs = []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _events(page)
        page.route("**/api/agent/run", lambda route: runs.append(route))
        page.click(_RUN)
        while not runs:
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        runs[0].fetch()
        _shows(page, study_key(workspace / "run1"))
        runs[0].fulfill(status=502, content_type="text/html", body="<h1>Bad gateway</h1>")
        page.wait_for_selector("text=I could not read the software's answer", timeout=30000)
        page.wait_for_timeout(2000)
        said = page.evaluate("() => window.__else")
    finally:
        close()
        session.server.shutdown()
    assert made
    assert said == [study_key(other)], said
    assert errors == []


def test_enter_during_a_launch_sends_nothing_until_the_person_sends_again(tmp_path,
                                                                           monkeypatch):
    """L10 F1 and round 3: Enter during the launch sends nothing; it says
    why beside the box, and the words stay there. They go with the thread
    to the run's study; once the page shows it, Send is offered again, and
    nothing is sent until the person sends it, into the run's thread with
    the run's view. A message held to be sent later by itself was lost,
    sent twice, or sent into another thread or study."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    run = workspace / "run1"
    words = "typed as it starts"
    asked, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        _asking(page, asked)
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        during = _typed_and_entered_during_a_launch(page, here, words)
        while not answered:
            page.wait_for_timeout(50)
        _shows(page, study_key(run))
        page.wait_for_function(_OFFERED, timeout=15000)
        page.wait_for_timeout(3000)
        before, note = _requests(asked), _note(page)
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert during == {"note": _SEND_WAITS, "offered": False, "box": words}, during
    assert before == ["chignolin"] and note == "", (before, note)
    assert _requests(asked) == ["chignolin", words]
    assert (asked[1].get("current_view") or {}).get("study", "").endswith("run1"), asked[1]
    assert words in _texts(workspace, "run1/agent")
    assert errors == []


def test_a_study_seen_first_takes_nothing_sent_and_blocks_nothing(tmp_path, monkeypatch):
    """L3 1, L4 H1, L5 2 and L6 M2: Enter during the launch; the launch
    answers before the page reads a status, and another window opens
    `other`, which the page sees first. Nothing is sent, into the run's
    thread or `other`'s; the words are kept for the run's study, where the
    thread they were typed in went. On `other`, a question is sent at once."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    words = "typed as it starts"
    held, answered, asked = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("About other.")])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _asking(page, asked)
        _hold(page, held)
        during = _typed_and_entered_during_a_launch(page, here, words)
        while not answered:
            page.wait_for_timeout(50)
        page.wait_for_timeout(400)
        _switch_elsewhere(session, other)
        _release(page, held)
        _shows(page, study_key(other))
        page.wait_for_timeout(3000)
        box, kept, before = page.input_value("#agent-request"), _unsent(page), _requests(asked)
        page.fill("#agent-request", "a question about other")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('About other.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert during["note"] == _SEND_WAITS, during
    assert before == ["chignolin"], before
    assert words not in box and kept.get(study_key(workspace / "run1")) == words, (box, kept)
    assert _requests(asked) == ["chignolin", "a question about other"]
    assert (asked[1].get("current_view") or {}).get("study", "").endswith("other")
    assert words not in _texts(workspace, "run1/agent") + _texts(workspace, "other/agent")
    assert errors == []


def test_words_that_follow_the_thread_are_sent_only_by_the_person(tmp_path, monkeypatch):
    """L4 M1, L3 6 and L5 5, in L4's order: Enter during the launch; another
    window opens `other` before the run's switch and the page sees it; the
    run starts, and the page sees its study. The words went with the thread
    there; Send is offered soon after, and they are sent when the person
    sends them, with the run's view."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.3)
    run = workspace / "run1"
    words = "words for the run's thread"
    runs, asked = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        _asking(page, asked)
        page.route("**/api/agent/run", lambda route: runs.append(route))
        during = _typed_and_entered_during_a_launch(page, here, words)
        while not runs:
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(500)
        runs[0].continue_()
        page.unroute("**/api/agent/run")
        _shows(page, study_key(run))
        page.wait_for_function(_OFFERED, timeout=6000)
        page.wait_for_timeout(2000)
        before, box = _requests(asked), page.input_value("#agent-request")
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=15000)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert during["note"] == _SEND_WAITS, during
    assert before == ["chignolin"] and box == words, (before, box)
    assert (asked[-1].get("current_view") or {}).get("study", "").endswith("run1")
    assert words in _texts(workspace, "run1/agent") and note == "", note
    assert errors == []


def test_an_unreadable_answer_sends_nothing_by_itself(tmp_path, monkeypatch):
    """L3 3: Enter during the launch; the server starts the run (the GUI
    goes to run1, the thread with it) but its answer cannot be read.
    Nothing is sent by itself; once the page has read which study is open,
    the person sends it, with that study open."""
    import urllib.request

    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=1.0)
    words = "typed as it starts"
    held, asked = [], []
    base = session.url.split("#")[0].rstrip("/")

    def gui_study():
        with urllib.request.urlopen(base + "/api/status", timeout=10) as answer:
            return json.loads(answer.read()).get("study")

    def lost(route):
        route.fetch()
        route.fulfill(status=502, content_type="text/html", body="<h1>Bad gateway</h1>")

    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        page.on("request", lambda r: asked.append((json.loads(r.post_data or "{}"), gui_study()))
                if "/api/agent/propose" in r.url else None)
        page.route("**/api/agent/run", lost)
        _hold(page, held)
        during = _typed_and_entered_during_a_launch(page, here, words)
        page.wait_for_selector("text=I could not read the software's answer", timeout=30000)
        page.wait_for_timeout(1500)
        _release(page, held)
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_function(_OFFERED, timeout=15000)
        page.wait_for_timeout(2000)
        before = [a.get("request") for a, _ in asked]
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=30000)
    finally:
        close()
        session.server.shutdown()
    assert during["note"] == _SEND_WAITS, during
    assert before == ["chignolin"], before
    sent = [(a, gui) for a, gui in asked if a.get("request") == words]
    assert len(sent) == 1, asked
    view = (sent[0][0].get("current_view") or {}).get("study") or ""
    assert study_key(view) == sent[0][1] == study_key(workspace / "run1"), (view, sent[0][1])
    assert words in _texts(workspace, "run1/agent")
    assert errors == []


def test_a_stale_read_of_the_start_study_leaves_the_words_with_the_run(tmp_path, monkeypatch):
    """L6 M1: Enter during the launch; its answer cannot be read; a status
    read of the start study, asked before the run's switch, is applied
    after the answer. The words still go with the thread to the run's
    study, and are sent there when the person sends them."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=1.0)
    words = "typed as it starts"
    stale, asked = [], []

    def lost(route):
        route.fetch()
        route.fulfill(status=502, content_type="text/html", body="<h1>Bad gateway</h1>")

    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        _asking(page, asked)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.route("**/api/agent/run", lost)
        _stale_status(page, stale)
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type(words)
        page.keyboard.press("Enter")
        page.wait_for_selector("text=I could not read the software's answer", timeout=30000)
        stale[0][0].fulfill(response=stale[0][1])
        _shows(page, study_key(workspace / "run1"))
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=30000)
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    sent = [a for a in asked if a.get("request") == words]
    assert len(sent) == 1 and (sent[0].get("current_view") or {}).get("study", "").endswith("run1")
    assert words in _texts(workspace, "run1/agent")
    assert words not in json.dumps(kept), kept
    assert errors == []


def test_a_chat_of_no_study_waits_and_is_filed_as_one(tmp_path, monkeypatch):
    """L3 4 and L9 2: after a launch answers, before the page has read which
    study is open, the person starts a chat of no study and presses Enter:
    nothing is sent, as for any message then. Sent by the person once Send
    is offered, it is the chat's, never filed under a study."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    words = "a general question"
    held, answered, asked = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("General answer.")])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _asking(page, asked)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        _hold(page, held)
        page.click(_RUN)
        time.sleep(1.0)
        _switch_elsewhere(session, here)
        while not answered:
            page.wait_for_timeout(50)
        page.wait_for_timeout(300)
        page.evaluate("window.FastMDXAgentPanel.fresh(null)")
        page.wait_for_timeout(800)
        page.fill("#agent-request", words)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        note, before = _note(page), _requests(asked)
        _release(page, held)
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('General answer.')",
                               timeout=15000)
        page.wait_for_timeout(1000)
    finally:
        close()
        session.server.shutdown()
    assert note == _SEND_WAITS and before == ["chignolin"], (note, before)
    assert _requests(asked) == ["chignolin", words]
    assert words not in _texts(workspace, "here/agent") + _texts(workspace, "run1/agent")
    assert words in _texts(workspace, "")
    assert errors == []


def test_a_message_waiting_is_never_sent_as_the_page_goes(tmp_path, monkeypatch):
    """L4 H2 and L8 H1: Enter during the launch, then the page goes (its
    pagehide; the page is kept to watch what follows). Nothing is sent as
    it goes, and the words are kept as any unsent words."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=2.0)
    words = "typed, then the page went"
    asked, answered = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _asking(page, asked)
        _typed_and_entered_during_a_launch(page, here, words)
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}))")
        while not answered:
            page.wait_for_timeout(50)
        _shows(page, study_key(workspace / "run1"))
        page.evaluate("() => window.FastMDXDashboard.readStatusNow()")
        page.wait_for_timeout(3000)
        kept = _unsent(page)
    finally:
        close()
        session.server.shutdown()
    assert made
    assert _requests(asked) == ["chignolin"], asked
    assert words in json.dumps(kept), kept
    assert errors == []


def test_reloading_during_a_launch_asks_nothing_and_sends_nothing(tmp_path, monkeypatch):
    """L7 2, L7 4, L8 H1, L10 F1 and L12 F9: Enter during the launch, then a
    reload. The words are kept in this tab as any unsent words, so the
    browser does not ask; nothing is ever sent by itself after the reload,
    and the page that comes back says where the words are."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    words = "typed, then reloaded"
    asked, dialogs = [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Answered.")])
    try:
        _asking(page, asked)
        page.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
        _typed_and_entered_during_a_launch(page, here, words)
        page.reload(wait_until="domcontentloaded")
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_timeout(5000)
        kept = _unsent(page)
        box = page.input_value("#agent-request")
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert dialogs == [], dialogs
    assert _requests(asked) == ["chignolin"], asked
    assert words in json.dumps(kept) or words in box, (kept, box)
    # L12 F9: the page that comes back says where they are.
    assert words in box or "as a run started is saved for it" in note, (box, note)
    assert errors == []


#: The run's request held, and failed as the reload cuts it, between the
#: panel's first pagehide listener and its later ones, as Chromium sometimes
#: fails it (CI-774 reviews L17 E, L18 2).
_RUN_FAILS_AMID_PAGEHIDE = (
    "(() => { const real = window.fetch; let fail = null;"
    " window.fetch = function (url, opts) {"
    "   if (/[/]api[/]agent[/]run$/.test(String(url).split('?')[0])) {"
    "     real.call(this, url, opts); return new Promise((ok, no) => { fail = no; }); }"
    "   return real.apply(this, arguments); };"
    " const add = window.addEventListener;"
    " window.addEventListener = function (type, fn, opts) {"
    "   const done = add.call(this, type, fn, opts);"
    "   if (type === 'pagehide' && /leaving = true/.test(String(fn))) {"
    "     add.call(window, 'pagehide', () => { if (fail) fail(new TypeError('Failed to fetch')); }); }"
    "   return done; }; })();")


def test_a_launch_failed_amid_the_reload_still_says_where_the_words_are(tmp_path, monkeypatch):
    """L17 E and L18 2: Enter during a launch, then a reload whose cut
    request failed between the page's own pagehide listeners. The failure
    counted the launch answered first, so the words kept were not marked as
    kept as a run started, and the page that came back said nothing of
    them. The going takes the launch as it was when it began."""
    from fastmdxplora.gui.workspace import study_key
    from tests.test_a_conversation_follows_a_run_that_wrote_nothing import _ready

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    words = "typed, then reloaded"
    asked = []
    page, errors, close = _page_with(session, [dict(_CONFIG), _answer("Answered.")],
                                     _RUN_FAILS_AMID_PAGEHIDE)
    try:
        _asking(page, asked)
        page.goto(session.url + "#agent", wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        _ready(page)
        _typed_and_entered_during_a_launch(page, here, words)
        page.reload(wait_until="domcontentloaded")
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_function("(w) => document.getElementById('agent-request').value.includes(w)"
                               " || ((document.getElementById('agent-unsent-note') || {}).textContent"
                               " || '').includes('as a run started is saved for it')",
                               arg=words, timeout=15000)
        kept, box, note = _unsent(page), page.input_value("#agent-request"), _note(page)
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["chignolin"], asked
    assert kept.get(study_key(here)) == words, kept
    assert words not in box and "as a run started is saved for it" in note, (box, note)
    assert errors == []


def test_enter_after_a_reload_during_a_launch_sends_nothing_by_itself(tmp_path, monkeypatch):
    """L12 F1: Run (the run's switch comes a few seconds in, as a real
    run's does), words typed, Enter (Send waits), the page reloaded. On the
    page that came back Send was offered, and Enter queued the message until
    the thread was read: when the launch answered it went by itself, with
    the start study's view, was refused and lost. Now Enter there sends
    nothing, says why, and nothing goes by itself after. The run's switch
    waits for that Enter (CI-774 reviews L13, L14: under load the page read
    its thread first, and the Enter went, rightly, into the run's)."""
    import threading
    import time as clock

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    run = workspace / "run1"
    entered = threading.Event()

    def launch(self, state, *, dashboard_url=None, config=None):
        entered.wait(60)
        clock.sleep(0.5)
        run.mkdir()
        (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        made.append(str(run))
        self.active_root = run
        clock.sleep(0.5)
        return {"ok": True, "output": str(run)}

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", launch)
    words = "typed, reloaded, entered"
    asked = []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        _asking(page, asked)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        _shows(page, study_key(here))
        page.wait_for_timeout(800)
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type(words)
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(500)
        if not page.input_value("#agent-request").strip():
            page.fill("#agent-request", words)
        page.focus("#agent-request")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        note = _note(page)
        entered.set()
        _shows(page, study_key(run))
        page.wait_for_timeout(5000)
        kept, box = _unsent(page), page.input_value("#agent-request")
    finally:
        entered.set()
        close()
        session.server.shutdown()
    assert made
    assert note in (_SEND_READS, _SEND_WAITS), note
    assert _requests(asked) == ["chignolin"], asked
    # Nowhere on disk; kept unsent, in this tab.
    assert _where(workspace, words) == []
    assert words in json.dumps(kept) or words in box, (kept, box)
    assert errors == []


def test_the_page_s_own_switches_go_on_with_words_kept(tmp_path, monkeypatch):
    """L9 1: words kept for the run's study (Enter during the launch,
    another window opening `other` first). The page opens `here`, then the
    run's study, by their tabs: no "Leave site?", each switch made; the
    words are in the run's box, and nothing is sent."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    run = workspace / "run1"
    words = "typed as it starts"
    held, answered, asked, dialogs = [], [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG)])
    try:
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        _asking(page, asked)
        _hold(page, held)
        _typed_and_entered_during_a_launch(page, here, words)
        while not answered:
            page.wait_for_timeout(50)
        page.wait_for_timeout(400)
        _switch_elsewhere(session, other)
        _release(page, held)
        _shows(page, study_key(other))
        page.wait_for_timeout(2000)
        for study in (here, run):
            page.evaluate("(key) => { window.FastMDXDashboard.openStudyTab(key, 'agent'); }",
                          study_key(study))
            page.wait_for_timeout(1500)
            page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDashboard.state")
            _shows(page, study_key(study))
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(1500)
        box = page.input_value("#agent-request")
    finally:
        close()
        session.server.shutdown()
    assert dialogs == [], dialogs
    assert box == words, box
    assert _requests(asked) == ["chignolin"], asked
    assert errors == []


def test_a_retry_during_a_launch_sends_nothing(tmp_path, monkeypatch):
    """L9 3 and L10 F4: "hello" answered; Run pressed; Retry on "hello"
    while the launch goes on: nothing is sent, now or later by itself, and
    Send waits, saying so."""
    from tests.test_a_conversation_follows_a_run_that_wrote_nothing import _RETRY
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    asked, answered = [], []
    page, errors, close = _page_on(session, [_answer("Hello."), dict(_CONFIG), _answer("Again.")])
    try:
        _asking(page, asked)
        page.on("response", lambda r: answered.append(r) if "/api/agent/run" in r.url else None)
        _shows(page, study_key(here))
        _say(page, "hello")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Hello.')")
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        page.wait_for_timeout(800)
        page.click(_RUN)
        hello = page.locator("#agent-thread .agent-msg-user").first
        hello.hover()
        hello.locator(_RETRY).first.click(force=True)
        page.wait_for_timeout(300)
        note = _note(page)
        while not answered:
            page.wait_for_timeout(50)
        _shows(page, study_key(workspace / "run1"))
        page.wait_for_timeout(4000)
    finally:
        close()
        session.server.shutdown()
    assert note == _SEND_WAITS, note
    assert _requests(asked) == ["hello", "chignolin"], asked
    assert errors == []


def test_a_config_builder_launch_makes_send_wait_too(tmp_path, monkeypatch):
    """L10 F3: while a launch of the Config Builder is unanswered (asked
    and answered as the Builder asks them), Enter sends nothing and says
    so; once it answers, Send is offered, and the message goes into the
    study shown's thread."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "asked as the Builder starts"
    asked = []
    page, errors, close = _page_on(session, [_answer("Answered.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        page.evaluate("() => window.FastMDXDashboard.studyChangeAsked()")
        page.fill("#agent-request", words)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        note, offered, before = _note(page), _send_offered(page), _requests(asked)
        page.evaluate("() => window.FastMDXDashboard.studyChangeAnswered('')")
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert (note, offered, before) == (_SEND_WAITS, False, []), (note, offered, before)
    assert _requests(asked) == [words]
    assert (asked[0].get("current_view") or {}).get("study", "").endswith("here")
    assert words in _texts(workspace, "here/agent")
    assert errors == []


def test_a_send_goes_with_the_view_read_after_its_study_s_status(tmp_path, monkeypatch):
    """L8 M2: one poll reads the app state with `here` open, another window
    opens `other`, and the poll's status is read after it: the page shows
    `other` with `here` as its view, and the notice named `here`. Enter
    sends once the view is read again after the status, with `other` as
    its view, and the notice names `other`."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "about the study shown"
    asked, apps, later, statuses = [], [], [], []

    def app(route):
        if apps:
            later.append(route)
        else:
            apps.append((route, route.fetch()))

    def status(route):
        if statuses:
            route.continue_()
        else:
            statuses.append(route)

    page, errors, close = _page_on(session, [_answer("Answered.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        page.evaluate(_HEARD)
        page.route("**/api/app-state*", app)
        page.route("**/api/status*", status)
        page.evaluate("() => document.getElementById('refresh-now').click()")
        while not (apps and statuses):
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        apps[0][0].fulfill(response=apps[0][1])
        statuses[0].continue_()
        page.unroute("**/api/status*")
        _shows(page, study_key(other))
        page.wait_for_timeout(500)
        page.fill("#agent-request", words)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        note, before = _note(page), _requests(asked)
        page.unroute("**/api/app-state*")
        for route in later:
            route.continue_()
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Answered.')", timeout=15000)
        page.wait_for_function("() => window.__said.some((s) => /opened elsewhere/.test(s))",
                               timeout=15000)
        said = [s for s in page.evaluate("() => window.__said") if "opened elsewhere" in s]
    finally:
        close()
        session.server.shutdown()
    assert before == [], (note, before)
    assert len(asked) == 1, asked
    assert (asked[0].get("current_view") or {}).get("study", "").endswith("other"), asked
    assert said and said[-1].startswith("other was opened elsewhere"), said
    assert errors == []


def test_send_and_the_notice_do_not_wait_for_the_study_s_results(tmp_path, monkeypatch):
    """L10 F6: another window opens `other`, whose results take seconds to
    read. Once the page shows `other`, a message sent there goes as soon as
    the view is read after its status, and the notice is said: the study's
    results are not waited for again (they were read only for its name)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    pending, asked = [], []
    page, errors, close = _page_on(session, [_answer("Answered.")])
    try:
        page.on("request", lambda r: asked.append(time.time())
                if "/api/agent/propose" in r.url else None)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        page.evaluate(_HEARD)
        page.route("**/api/results*", lambda route: pending.append((time.time(), route)))

        def pump(until, most=40.0):
            end = time.time() + most
            while time.time() < end:
                now = time.time()
                for when, route in list(pending):
                    if now - when > 5.0:
                        pending.remove((when, route))
                        route.continue_()
                if until():
                    return True
                page.wait_for_timeout(50)
            return False

        _switch_elsewhere(session, other)
        assert pump(lambda: page.evaluate("() => window.FastMDXDashboard.state.studyShown")
                    == study_key(other))
        shown = time.time()
        page.fill("#agent-request", "about other")
        assert pump(lambda: _send_offered(page), 20.0)
        page.keyboard.press("Enter")
        assert pump(lambda: bool(asked), 20.0)
        assert pump(lambda: page.evaluate(
            "() => window.__said.some((s) => /opened elsewhere/.test(s))"), 20.0)
        said = time.time()
        page.unroute("**/api/results*")
        for _, route in pending:
            route.continue_()
    finally:
        close()
        session.server.shutdown()
    assert asked[0] - shown < 3.0 and said - shown < 3.0, (asked[0] - shown, said - shown)
    assert errors == []


def _where(workspace, text):
    """The conversations on disk that hold `text`, by their path."""
    return sorted(str(f.relative_to(workspace)) for f in workspace.rglob("conv-*.json")
                  if text in f.read_text(encoding="utf-8"))


#: Holds the page's reads of a study's conversation while `__holdConv`.
_HOLD_CONV = ("(() => { const real = window.fetch; window.__holdConv = false;"
              " window.fetch = function (url, opts) {"
              "   const p = real.call(this, url, opts);"
              "   const u = String(url);"
              "   if (window.__holdConv && /[/]api[/]agent[/]conversation([?]|$)/.test(u)"
              "       && (!opts || !opts.method || opts.method === 'GET')) {"
              "     window.__held = (window.__held || 0) + 1;"
              "     return p.then((r) => new Promise((ok) => { const t = setInterval(() => {"
              "       if (!window.__holdConv) { clearInterval(t); ok(r); } }, 100); })); }"
              "   return p; }; })();")

#: Holds the page's status reads until `__release`.
_HOLD_STATUS = ("(() => { const real = window.fetch; window.fetch = function (url, opts) {"
                "   const p = real.call(this, url, opts);"
                "   if (String(url).indexOf('/api/status') >= 0 && !window.__release) {"
                "     return p.then((r) => new Promise((ok) => { const t = setInterval(() => {"
                "       if (window.__release) { clearInterval(t); ok(r); } }, 100); })); }"
                "   return p; }; })();")


def _page_with(session, replies, script=None):
    """A page on the session's Agent, `script` run before its own."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    chromium = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
    page = chromium.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(30000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/agent/propose*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(replies.pop(0))))
    if script:
        page.add_init_script(script)

    def close():
        chromium.close()
        pw.stop()
    return page, errors, close


_SEND_READS = "Send waits until this page has read the study open and its conversation."
_VIEW_READS = "Sent once this page has read the study's view, if nothing changes meanwhile."


def test_after_a_run_left_no_study_open_a_question_is_sent(tmp_path, monkeypatch):
    """L11 1: a run that failed at once leaves the GUI with no study open
    (`data_stale`, no `active_root`). The person asks why: Send waited for
    the study the page had shown, for ever, and the page read its status
    without pause meanwhile."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked, reads = [], []
    page, errors, close = _page_on(session, [_answer("Because.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        session.runtime.data_stale = True
        session.runtime.active_root = None
        page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                               timeout=30000)
        page.wait_for_timeout(2000)
        page.on("request", lambda r: reads.append(r.url) if "/api/status" in r.url else None)
        page.wait_for_timeout(5000)
        page.fill("#agent-request", "why did it fail?")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Because.')", timeout=15000)
        page.wait_for_timeout(2000)
        note, box = _note(page), page.input_value("#agent-request")
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["why did it fail?"], asked
    assert len(reads) < 10, len(reads)
    # Kept in a chat of no study, as the GUI has none open; not refused.
    assert box == "" and "Not saved" not in note, (box, note)
    where = _where(workspace, "why did it fail?")
    assert where and not any(w.startswith("here/") for w in where), where
    assert errors == []


@pytest.mark.parametrize("elsewhere", [False, True], ids=["here", "another study opened"])
def test_enter_before_the_page_has_read_its_study_sends_nothing(tmp_path, monkeypatch,
                                                                 elsewhere):
    """L11 3 and L12 F1: the page's first status not yet read. Enter was
    sent with no view of a study (or queued until the thread was read, and
    sent by itself), and with another study opened meanwhile it was kept in
    no conversation. It sends nothing then, and says why. Once Send is
    offered it goes into the thread of the page's study, with its view;
    with another study opened meanwhile, the words are kept for the page's
    own, as any words typed for a study the GUI left."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    replies, asked = [_answer("First.")], []
    page, errors, close = _page_with(session, replies, _HOLD_STATUS)
    try:
        _asking(page, asked)
        page.goto(session.url + "#agent", wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_timeout(1500)
        if elsewhere:
            _switch_elsewhere(session, other)
            page.wait_for_timeout(300)
        page.fill("#agent-request", "first question")
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)
        before, note = _requests(asked), _note(page)
        page.evaluate("() => { window.__release = true; }")
        _shows(page, study_key(other if elsewhere else here))
        _when_known(page)
        page.wait_for_timeout(1000)
        kept = _unsent(page)
        if not elsewhere:
            _send_when_offered(page)
            page.wait_for_selector("#agent-thread .agent-answer:has-text('First.')", timeout=15000)
        page.wait_for_timeout(1500)
    finally:
        close()
        session.server.shutdown()
    assert before == [] and note == _SEND_READS, (before, note)
    if elsewhere:
        assert asked == [] and kept.get(study_key(here)) == "first question", (asked, kept)
        assert _where(workspace, "first question") == []
    else:
        assert _requests(asked) == ["first question"]
        assert (asked[0].get("current_view") or {}).get("study", "").endswith("here"), asked
        where = _where(workspace, "first question")
        assert where and all(w.startswith("here/") for w in where), where
    assert errors == []


@pytest.mark.parametrize("then", ["typed", "new", "nothing"])
def test_enter_with_only_the_view_left_says_what_it_does(tmp_path, monkeypatch, then):
    """L11 4 and L12 F5: only the view is left to read after another window
    opened a study. Enter said nothing while it was read, and, with the
    words or the thread changed meanwhile, sent nothing and said nothing.
    It says so at once, and after."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("About other.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        page.wait_for_timeout(1500)
        page.evaluate("""() => { const real = window.fetch; let seq = 0, metricsAt = -1;
            window.__holdView = true;
            window.fetch = function (url, opts) {
                const u = String(url), n = ++seq, self = this;
                if (u.indexOf('/api/metrics') >= 0) metricsAt = n;
                if (u.indexOf('/api/app-state') < 0) return real.call(self, url, opts);
                return new Promise((ok) => setTimeout(ok, 0)).then(() => {
                    if (metricsAt > n || !window.__holdView) return real.call(self, url, opts);
                    return new Promise((ok) => { const t = setInterval(() => {
                        if (!window.__holdView) { clearInterval(t); ok(real.call(self, url, opts)); }
                    }, 50); });
                });
            }; }""")
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_timeout(1000)
        page.fill("#agent-request", "view only")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        during = _note(page)
        if then == "typed":
            page.type("#agent-request", " and more")
        elif then == "new":
            page.evaluate("() => window.FastMDXAgentPanel.fresh(null)")
        if then != "nothing":
            page.wait_for_timeout(2500)
        page.evaluate("() => { window.__holdView = false; }")
        page.wait_for_timeout(3000)
        after = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert during == _VIEW_READS, during
    if then == "nothing":
        assert _requests(asked) == ["view only"], asked
        assert (asked[0].get("current_view") or {}).get("study", "").endswith("other")
    else:
        assert asked == [] and after.startswith("Not sent:"), (asked, after)
    assert errors == []


def test_enter_while_the_shown_study_s_thread_is_read_sends_nothing(tmp_path, monkeypatch):
    """L11 3 and L12 F3: another window opens `other`; the page shows it
    and reads its view, while `other`'s thread is still being read. Enter
    was asked with `other`'s view and filed in `here`'s thread. It sends
    nothing until the thread is read, then goes into `other`'s."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    replies, asked = [_answer("Here."), _answer("Other.")], []
    page, errors, close = _page_with(session, replies, _HOLD_CONV)
    try:
        _asking(page, asked)
        page.goto(session.url + "#agent", wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        _shows(page, study_key(here))
        _when_known(page)
        page.fill("#agent-request", "first in here")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Here.')", timeout=15000)
        page.wait_for_timeout(1000)
        page.evaluate("() => { window.__holdConv = true; }")
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXDashboard.viewKnown() && (window.__held || 0) > 0",
                               timeout=20000)
        page.fill("#agent-request", "asked about other")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        before, note = _requests(asked), _note(page)
        page.evaluate("() => { window.__holdConv = false; }")
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Other.')", timeout=15000)
        page.wait_for_timeout(1000)
    finally:
        close()
        session.server.shutdown()
    assert before == ["first in here"] and note == _SEND_READS, (before, note)
    assert (asked[1].get("current_view") or {}).get("study", "").endswith("other")
    where = _where(workspace, "asked about other")
    assert where and all(w.startswith("other/") for w in where), where
    assert errors == []


def test_enter_after_a_launch_while_the_run_s_thread_is_read_sends_nothing(tmp_path,
                                                                            monkeypatch):
    """L11 3: Run, New while it starts; the launch answers and the page
    shows run1, its view read, its thread still being read. Enter was asked
    with run1's view and lost when the thread read landed. It sends nothing
    until the thread is read, then goes into run1's."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=3.0)
    replies, asked = [dict(_CONFIG), _answer("After.")], []
    page, errors, close = _page_with(session, replies, _HOLD_CONV)
    try:
        _asking(page, asked)
        page.goto(session.url + "#agent", wait_until="domcontentloaded")
        page.wait_for_selector("#agent-request", state="visible")
        _shows(page, study_key(here))
        _when_known(page)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        page.click(_RUN)
        page.wait_for_timeout(500)
        page.click("#agent-new")
        page.wait_for_timeout(300)
        page.evaluate("() => { window.__holdConv = true; }")
        run1 = workspace / "run1"
        page.wait_for_function("(k) => window.FastMDXDashboard.state.studyShown === k"
                               " && window.FastMDXDashboard.viewKnown() && (window.__held || 0) > 0",
                               arg=study_key(run1), timeout=30000)
        page.fill("#agent-request", "after the run")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        before, note = _requests(asked), _note(page)
        page.evaluate("() => { window.__holdConv = false; }")
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('After.')", timeout=15000)
        page.wait_for_timeout(1500)
    finally:
        close()
        session.server.shutdown()
    assert before == ["chignolin"] and note in (_SEND_READS, _SEND_WAITS), (before, note)
    where = _where(workspace, "after the run")
    assert where and all(w.startswith("run1/") for w in where), where
    assert errors == []


@pytest.mark.parametrize("way", ["retry", "edit", "choice"])
def test_a_button_while_send_waits_leaves_the_words_and_the_thread(tmp_path, monkeypatch, way):
    """L10 F4, L11 2 and L12 F2: words typed and Entered while a launch goes
    on (Send waits); then Retry, an Edit, or a choice pressed. Each wrote
    over the words kept in the box, cut the thread, spent the choice, and
    sent nothing. Now each says Send waits and changes nothing; once the
    launch has answered, the choice sends its own words, the person's words
    still in the box."""
    from tests.test_a_conversation_follows_a_run_that_wrote_nothing import _RETRY
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    question = {"ok": False, "question": "Which lysozyme?", "asked": "Which lysozyme?",
                "choices": ["2VB1 hen egg-white", "1LYZ hen egg-white"], "attempts": []}
    asked = []
    page, errors, close = _page_on(session, [question, _answer("Chosen.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        _say(page, "lysozyme please")
        page.wait_for_selector("#agent-thread .agent-choice")
        page.wait_for_timeout(500)
        # A launch going on (the Config Builder's, as it asks it).
        page.evaluate("() => window.FastMDXDashboard.studyChangeAsked()")
        page.fill("#agent-request", "my own words")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        first = page.locator("#agent-thread .agent-msg-user").first
        if way == "retry":
            first.hover()
            first.locator(_RETRY).first.click(force=True)
        elif way == "edit":
            first.hover()
            first.locator("button[aria-label='Edit'], button[title^='Edit this message']") \
                .first.click(force=True)
            page.keyboard.type(" edited")
            page.keyboard.press("Enter")
        else:
            page.locator("#agent-thread .agent-choice").first.click()
        page.wait_for_timeout(500)
        mid = {"box": page.input_value("#agent-request"), "note": _note(page),
               "users": page.locator("#agent-thread .agent-msg-user").count(),
               "first": first.inner_text(),
               "choices": page.locator("#agent-thread .agent-choice:not([disabled])").count()}
        kept = _unsent(page)
        before = _requests(asked)
        page.evaluate("() => window.FastMDXDashboard.studyChangeAnswered('')")
        _when_known(page)
        page.locator("#agent-thread .agent-choice").first.click()
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Chosen.')", timeout=15000)
        box = page.input_value("#agent-request")
    finally:
        close()
        session.server.shutdown()
    assert mid["box"] == "my own words" and mid["note"] == _SEND_WAITS, mid
    # An Edit refused keeps its words in the bubble, still being edited.
    assert mid["users"] == 1 and ("edited" in mid["first"]) == (way == "edit"), mid
    assert mid["choices"] == 2, mid
    assert kept.get(study_key(here)) == "my own words", kept
    assert before == ["lysozyme please"], before
    assert _requests(asked) == ["lysozyme please", "2VB1"], asked
    assert box == "my own words"
    assert errors == []


def test_a_builder_run_s_study_has_its_own_thread_and_the_start_study_its_words(tmp_path,
                                                                                monkeypatch):
    """L10 F3, L11 5 and L12 F4: the Config Builder's Start; its run's folder
    is no study for a while (as a real run's is), and the Agent's thread was
    then a chat of no study, where a message on the run's study went; the
    start study's words were offered for sending there. Send waits until
    the run's folder is a study; the start study's words go back to it, and
    a message on the run's study goes into its own thread."""
    import threading
    import time as clock

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key
    from tests.test_open_studies_have_tabs import _builder

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    run = workspace / "run1"

    processes = []
    # Once the test has read what Send says while the run starts: a fixed
    # time was spent under load before it read it (L19 L6).
    read = threading.Event()

    def written():
        read.wait(120)
        (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        (run / "setup").mkdir()

    def launch(self, state, *, dashboard_url=None, config=None):
        run.mkdir()
        self.active_root = run
        # As a real run: a process runs it, and its folder is a study once
        # it has written its first record.
        processes.append(_going(session, run))
        clock.sleep(1.0)
        threading.Thread(target=written, daemon=True).start()
        return {"ok": True, "output": str(run)}

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", launch)
    asked, built = [], []
    page, errors, close = _page_on(session, [_answer("In here."), _answer("On run1.")])
    try:
        _asking(page, asked)
        page.on("response", lambda r: built.append(r) if r.url.endswith("/api/run") else None)
        _shows(page, study_key(here))
        _when_known(page)
        _say(page, "q in here")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('In here.')", timeout=15000)
        _builder(page, {"root": workspace})
        page.click("#run-start-button")
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.fill("#agent-request", "the start study's words")
        page.keyboard.press("Enter")
        while not built:
            page.wait_for_timeout(100)
        _shows(page, study_key(run))
        page.wait_for_timeout(1000)
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.focus("#agent-request")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        waiting = {"note": _note(page), "asked": _requests(asked)}
        read.set()
        page.wait_for_function("() => window.FastMDXDashboard.state.studyIsOne"
                               " && window.FastMDXAgentPanel.current.study", timeout=30000)
        _when_known(page)
        page.wait_for_timeout(1500)
        moved = {"box": page.input_value("#agent-request"), "kept": _unsent(page)}
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.fill("#agent-request", "asked on run1")
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('On run1.')", timeout=15000)
        page.wait_for_timeout(1000)
        current = page.evaluate("() => window.FastMDXAgentPanel.current")
    finally:
        read.set()
        close()
        session.server.shutdown()
        for process in processes:
            process.kill()
    assert waiting == {"note": _SEND_WAITS, "asked": ["q in here"]}, waiting
    assert moved["box"] == "" and moved["kept"].get(study_key(here)) == "the start study's words", moved
    assert _requests(asked) == ["q in here", "asked on run1"], asked
    where = _where(workspace, "asked on run1")
    assert where and all(w.startswith("run1/") for w in where), where
    assert str(current.get("study") or "").endswith("run1"), current
    assert errors == []


def test_a_message_refused_as_the_gui_moved_is_kept_unsent_for_its_study(tmp_path,
                                                                          monkeypatch):
    """L12 F6: a message sent as another window (or another tab's run)
    opened another study: the view check refuses it, and its save is
    refused as written for the study left; the page then put the other
    study's thread up, and the message was in no thread and not kept. It is
    kept as unsent words for the study it was written in, and said."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)

        def moved_then_refused(route):
            _switch_elsewhere(session, other)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"ok": False, "error": "The current view changed. Select it again before asking "
                 "the Agent.", "code": "agent.view.changed"}))

        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", moved_then_refused)
        page.fill("#agent-request", "sent as the GUI moved")
        page.keyboard.press("Enter")
        _shows(page, study_key(other))
        page.wait_for_timeout(4000)
        kept = _unsent(page)
        box = page.input_value("#agent-request")
        on_disk = _where(workspace, "sent as the GUI moved")
    finally:
        close()
        session.server.shutdown()
    assert on_disk or "sent as the GUI moved" in kept.get(study_key(here), "") \
        or "sent as the GUI moved" in box, (on_disk, kept, box)
    assert errors == []


def _pump(page, pending, until, most=40.0, after=6.0):
    """Waits for `until`, answering each held read once it has waited
    `after` seconds (a study whose results take long to read)."""
    end = time.time() + most
    while time.time() < end:
        now = time.time()
        for when, route in list(pending):
            if now - when > after:
                pending.remove((when, route))
                route.continue_()
        if until():
            return True
        page.wait_for_timeout(50)
    return False


def test_send_is_offered_soon_after_a_launch_whatever_the_results_take(tmp_path, monkeypatch):
    """L12 F7: Send waited for a whole poll after the launch answered, the
    study's results with it (seconds on a study of runs). It waits for the
    app state and the status alone."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=1.0)
    pending, asked, answered = [], [], []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("After.")])
    try:
        _asking(page, asked)
        page.on("response", lambda r: answered.append(time.time())
                if "/api/agent/run" in r.url else None)
        _shows(page, study_key(here))
        _when_known(page)
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        page.wait_for_timeout(800)
        page.route("**/api/results*", lambda route: pending.append((time.time(), route)))
        page.click(_RUN)
        page.focus("#agent-request")
        page.keyboard.type("typed as it starts")
        page.keyboard.press("Enter")
        assert _pump(page, pending, lambda: bool(answered))
        assert _pump(page, pending, lambda: _send_offered(page)
                     and page.evaluate("() => window.FastMDXDashboard.state.studyShown")
                     == study_key(workspace / "run1"), 20.0)
        offered = time.time()
        _send_when_offered(page)
        assert _pump(page, pending, lambda: len(asked) == 2, 20.0)
        page.unroute("**/api/results*")
        for _, route in pending:
            route.continue_()
    finally:
        close()
        session.server.shutdown()
    assert offered - answered[0] < 4.0, offered - answered[0]
    assert (asked[1].get("current_view") or {}).get("study", "").endswith("run1"), asked
    assert errors == []


# ---- CI-774 round 5: one rule for a study; buttons refused before acting --


def test_a_folder_the_gui_opens_as_a_study_is_one_the_agent_writes_for(tmp_path) -> None:
    """L13 2 and L14 F1, F2: a run the Config Builder started has its config
    written (exploration.yml) and a log, nothing else yet. The GUI opened it
    as a study; the Agent counted it as none, so its messages went into a
    chat of no study, or Send waited for good where the run ended there."""
    from fastmdxplora.gui.agent_panel import _is_study, merge_conversation, read_conversation
    from fastmdxplora.gui.browse import is_study
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    run = workspace / "run1"
    run.mkdir(parents=True)
    (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
    (run / "exploration.log").write_text("", encoding="utf-8")
    begun = workspace / "begun"
    begun.mkdir()
    (begun / "exploration.log").write_text("", encoding="utf-8")
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    runtime.active_root = run
    assert is_study(run) and _is_study(run)
    assert not is_study(begun) and not _is_study(begun)
    read = read_conversation(runtime)
    assert read["ok"] and read["study"] == str(run), read
    saved = merge_conversation(runtime, [{"eid": "e1", "role": "user", "text": "why?"}], [],
                               None, key="k1")
    assert saved["ok"] and saved["study"] == str(run), saved
    assert list((run / "agent" / "conversations").glob("conv-*.json"))


def _builder_run(monkeypatch, run, *, written=True):
    """The Config Builder's Start, without a process: `run` gets its config
    (or, `written` False, nothing, as "Run as it is" leaves it) and the log,
    and the GUI opens it."""
    from fastmdxplora.gui.exploration import DashboardRuntime

    def launch(self, state, *, dashboard_url=None, config=None):
        run.mkdir()
        if written:
            (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        (run / "exploration.log").write_text("", encoding="utf-8")
        self.active_root = run
        return {"ok": True, "output": str(run)}

    monkeypatch.setattr(DashboardRuntime, "launch_from_config", launch)


def _going(session, run):
    """A process runs `run`, as the GUI's own run of it."""
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    session.runtime.process = process
    session.runtime.running_root = run
    return process


def _start_from_the_builder(page, workspace, run):
    from fastmdxplora.gui.workspace import study_key

    built = []
    page.on("response", lambda r: built.append(r) if r.url.endswith("/api/run") else None)
    _builder(page, {"root": workspace})
    page.click("#run-start-button")
    while not built:
        page.wait_for_timeout(100)
    _shows(page, study_key(run))
    page.evaluate("window.FastMDXDashboard.navigate('agent')")


@pytest.mark.parametrize("then", ["launching window", "reloaded"])
def test_a_builder_run_that_ended_at_once_leaves_send_working(tmp_path, monkeypatch, then):
    """L13 2 and L14 F1: a Config Builder run stopped (or dead) in its first
    seconds leaves its folder with its config and log. Send waited on it for
    good, saying a run was starting; after a reload the message went into a
    chat of no study. It goes into the run's study's own thread."""
    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    run = workspace / "run1"
    _builder_run(monkeypatch, run)
    asked = []
    page, errors, close = _page_on(session, [_answer("It stopped.")])
    try:
        _asking(page, asked)
        _start_from_the_builder(page, workspace, run)
        if then == "reloaded":
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#agent-request", state="visible")
        page.fill("#agent-request", "why did it fail?")
        page.wait_for_function("() => !window.FastMDXAgentPanel.waits", timeout=15000)
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('It stopped.')", timeout=15000)
        page.wait_for_timeout(1500)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["why did it fail?"], asked
    assert note != _SEND_WAITS, note
    where = _where(workspace, "why did it fail?")
    assert where and all(w.startswith("run1/") for w in where), where
    assert errors == []


@pytest.mark.parametrize("written", ["config", "nothing yet"])
def test_another_window_follows_a_builder_run_onto_its_own_thread(tmp_path, monkeypatch, written):
    """L14 F2: another window starts a Config Builder run; this one follows
    the GUI to it, and filed every message in the newest chat of no study,
    one about something else, for good. With its config written the run's
    folder is a study with its own thread at once; with nothing yet, Send
    waits while its run goes, and the folder's own thread is read once it
    is a study."""
    from fastmdxplora.gui.agent_panel import _write_one
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    chats = workspace / ".fastmdxplora_agent_conversations"
    chats.mkdir()
    _write_one(chats, "conv-20260101-000000-000001",
               [{"eid": "old", "role": "user", "text": "an old chat"}])
    (chats / "current").write_text("conv-20260101-000000-000001", encoding="utf-8")
    run = workspace / "run1"
    asked, process = [], None
    page, errors, close = _page_on(session, [_answer("On run1.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        # The other window's launch.
        run.mkdir()
        (run / "exploration.log").write_text("", encoding="utf-8")
        if written == "config":
            (run / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        else:
            process = _going(session, run)
        session.runtime.active_root = run
        _shows(page, study_key(run))
        if written == "nothing yet":
            page.wait_for_function("() => window.FastMDXAgentPanel.waits === 'run'", timeout=15000)
            _say(page, "typed as it starts")
            page.wait_for_timeout(1000)
            waited = {"note": _note(page), "asked": _requests(asked)}
            (run / "resolved_config.yml").write_text("systems: []\n", encoding="utf-8")
            assert waited == {"note": _SEND_WAITS, "asked": []}, waited
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study"
                               " && !window.FastMDXAgentPanel.waits", timeout=20000)
        page.wait_for_timeout(500)
        _say(page, "asked on run1")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('On run1.')", timeout=15000)
        page.wait_for_timeout(1500)
        old = page.locator("#agent-thread .agent-msg-user", has_text="an old chat").count()
    finally:
        close()
        session.server.shutdown()
        if process is not None:
            process.kill()
    assert _requests(asked) == ["asked on run1"], asked
    where = _where(workspace, "asked on run1")
    assert where and all(w.startswith("run1/") for w in where), where
    assert old == 0
    assert errors == []


@pytest.mark.parametrize("written", ["config", "nothing yet"])
def test_new_on_a_builder_run_s_folder_gives_that_study_s_chat(tmp_path, monkeypatch, written):
    """L13 6: New pressed on a Config Builder run's folder before the Agent
    counted it a study made a chat of no study, where every message after
    went, the folder a study or not. New there gives the run's study's chat
    (or, with nothing written yet, Send waits while the run goes and the
    study's own thread is read once it is one)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    run = workspace / "run1"
    _builder_run(monkeypatch, run, written=(written == "config"))
    asked, process = [], None
    page, errors, close = _page_on(session, [_answer("New on run1.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        _start_from_the_builder(page, workspace, run)
        if written == "nothing yet":
            process = _going(session, run)
        page.wait_for_timeout(1000)
        page.click("#agent-new")
        page.wait_for_timeout(1000)
        if written == "nothing yet":
            page.wait_for_function("() => window.FastMDXAgentPanel.waits === 'run'", timeout=15000)
            (run / "resolved_config.yml").write_text("systems: []\n", encoding="utf-8")
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study"
                               " && !window.FastMDXAgentPanel.waits", timeout=20000)
        page.wait_for_timeout(500)
        _say(page, "asked in a new chat")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('New on run1.')", timeout=15000)
        page.wait_for_timeout(1500)
    finally:
        close()
        session.server.shutdown()
        if process is not None:
            process.kill()
    assert _requests(asked) == ["asked in a new chat"], asked
    where = _where(workspace, "asked in a new chat")
    assert where and all(w.startswith("run1/") for w in where), where
    assert errors == []


def _seed_a_review(session):
    """A thread: a study, a change to it (version 2, its review offered), a
    question, and a question back with candidates."""
    from copy import deepcopy

    from fastmdxplora.gui.plan import changes_between
    from tests.test_config_change_review_in_a_browser import BASE, _entry, _request

    before, after = deepcopy(BASE), deepcopy(BASE)
    after["simulation"].update(temperature_K=330, duration_ns=10)
    second = _entry(after, 2)
    second["changes"] = changes_between(before, after)
    _request(session, "/api/agent/conversation", {"entries": [
        {"role": "user", "text": "A study of ubiquitin."}, _entry(before, 1),
        {"role": "user", "text": "Change the conditions."}, second,
        {"role": "user", "text": "How long will it take?"},
        {"role": "agent", "kind": "question", "text": "Which lysozyme?", "asked": "Which lysozyme?",
         "choices": ["2VB1 hen egg-white", "1LYZ hen egg-white"]}]})


@pytest.mark.parametrize("way", ["retry", "edit", "choice"])
def test_a_button_while_a_review_holds_sending_cuts_nothing(tmp_path, monkeypatch, way):
    """L13 1 and L14 F4: a change rejected and not applied holds the next
    message. Retry or Edit cut the question and its answer, and a choice was
    spent, before the hold refused to send: the words were nowhere. Now the
    hold is asked first, said, and nothing is cut or spent."""
    import tempfile

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace), host="127.0.0.1", port=0)
    _seed_a_review(session)
    live = "#agent-thread .agent-study:not(.is-replaced):not([hidden])"
    asked = []
    page, errors, close = _page_on(session, [])
    try:
        _asking(page, asked)
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"]').wait_for()
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"] '
                     '[data-choice="reject"]').click()
        page.wait_for_timeout(500)
        msg = page.locator("#agent-thread .agent-msg-user").filter(has_text="How long will it take?")
        if way == "retry":
            msg.hover()
            msg.get_by_role("button", name="Retry").click()
        elif way == "edit":
            msg.hover()
            msg.get_by_role("button", name="Edit").click()
            page.keyboard.type(" Roughly?")
            page.keyboard.press("Enter")
        else:
            page.locator("#agent-thread .agent-choice").first.click()
        page.wait_for_timeout(1500)
        out = {"users": page.locator("#agent-thread .agent-msg-user").all_text_contents(),
               "choices": page.locator("#agent-thread .agent-choice:not([disabled])").count(),
               "note": _note(page),
               "editing": msg.locator(".editing").count() if way == "edit" else 0}
        saved = _entries_everywhere(tmp_path)
    finally:
        close()
        session.server.shutdown()
    assert asked == [], asked
    users = out["users"]
    assert len(users) == 3 and any("How long will it take?" in u for u in users), out
    assert out["choices"] == 2, out
    assert out["note"].startswith("Not sent: choose in the review above first"), out
    if way == "edit":
        assert "Roughly?" in users[2] and out["editing"] == 1, out
    texts = [e.get("text") for entries in saved.values() for e in entries]
    assert "How long will it take?" in texts and "Which lysozyme?" in texts, texts
    assert errors == []


def test_edit_while_send_waits_keeps_the_edited_words(tmp_path, monkeypatch):
    """L13 8: an Edit sent while Send waits put the bubble back to the old
    words, the edit lost. Nothing is cut and the edited words stay, still
    being edited, through a click elsewhere while the person waits (L16: the
    click put the old words back); Enter on them once Send is offered sends
    them."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("First."), _answer("Edited.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        _say(page, "the first words")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('First.')", timeout=15000)
        page.evaluate("() => window.FastMDXDashboard.studyChangeAsked()")
        msg = page.locator("#agent-thread .agent-msg-user").first
        msg.hover()
        msg.get_by_role("button", name="Edit").click()
        page.keyboard.type(" and more")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.click("#agent-request")
        page.wait_for_timeout(300)
        held = {"text": msg.inner_text(), "editing": msg.locator(".editing").count(),
                "note": _note(page), "asked": _requests(asked)}
        page.evaluate("() => window.FastMDXDashboard.studyChangeAnswered('')")
        page.wait_for_function("() => !window.FastMDXAgentPanel.waits", timeout=20000)
        msg.locator(".editing").press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Edited.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert "the first words and more" in held["text"] and held["editing"] == 1, held
    assert held["note"] == _SEND_WAITS and held["asked"] == ["the first words"], held
    assert _requests(asked) == ["the first words", "the first words and more"], asked
    assert errors == []


def test_a_starter_over_words_in_autonomous_mode_says_why(tmp_path, monkeypatch):
    """L13 9 and L14 F5: in autonomous mode a suggestion pressed with words
    in the box did nothing, and said nothing. Said; and no more once the
    words are sent (L16: the note stayed beside an empty box)."""
    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    page, errors, close = _page_on(session, [_answer("Sent.")])
    try:
        _when_known(page)
        page.evaluate("""() => { const m = document.getElementById('agent-mode');
            m.value = 'autonomous'; m.dispatchEvent(new Event('change')); }""")
        page.wait_for_function("() => !document.getElementById('agent-start').hidden")
        page.fill("#agent-request", "my words")
        page.locator(".agent-starter:visible").first.click()
        page.wait_for_timeout(300)
        box, note = page.input_value("#agent-request"), _note(page)
        page.focus("#agent-request")
        page.keyboard.press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Sent.')", timeout=15000)
        after = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert box == "my words"
    assert note.startswith("The box holds words of yours"), note
    assert after == "", after
    assert errors == []


@pytest.mark.parametrize("seen", ["after the answer", "before the answer"])
def test_a_message_answered_as_another_window_opens_a_study_is_kept_in_its_own(tmp_path,
                                                                                monkeypatch, seen):
    """L14 F3, L13 3 and L13 4: the first message in a study, with a file;
    its answer comes as another window opens another study. Its save went
    to the study open and was refused: the words were put back as unsent
    (in the box, the message also left in the thread, so it was asked and
    kept twice; or kept without its file), the answer lost. The thread is
    saved in the study it was written in, answer and file with it; with the
    other study seen before the answer came (the answer then stopped, as
    for any thread left), it is the thread that study shows again."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    (workspace / "notes.txt").write_text("n\n", encoding="utf-8")
    asked, proposals, saves, polls = [], [], [], []
    page, errors, close = _page_on(session, [])
    try:
        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", lambda route: (
            asked.append(json.loads(route.request.post_data or "{}").get("request")),
            proposals.append(route)))
        page.on("response", lambda r: saves.append(r.status)
                if r.url.endswith("/api/agent/conversation") and r.request.method == "POST" else None)
        _shows(page, study_key(here))
        _when_known(page)
        page.evaluate("""(p) => { const i = document.getElementById('agent-attach-path');
            i.value = p; i.dispatchEvent(new Event('change')); }""", str(workspace / "notes.txt"))
        page.wait_for_selector("#agent-attachments :text('notes.txt')")
        page.fill("#agent-request", "with a file")
        page.keyboard.press("Enter")
        while not proposals:
            page.wait_for_timeout(50)
        if seen == "after the answer":
            _hold(page, polls)
        _switch_elsewhere(session, other)
        if seen == "before the answer":
            _shows(page, study_key(other))
            page.wait_for_timeout(1500)
        proposals[0].fulfill(status=200, content_type="application/json",
                             body=json.dumps(_answer("W.")))
        if seen == "after the answer":
            page.wait_for_selector("#agent-thread .agent-answer:has-text('W.')", timeout=15000)
        while not saves:
            page.wait_for_timeout(50)
        page.wait_for_timeout(1000)
        _release(page, polls)
        _shows(page, study_key(other))
        page.wait_for_timeout(1500)
        on_other = {"box": page.input_value("#agent-request"), "kept": _unsent(page),
                    "note": _note(page)}
        _switch_elsewhere(session, here)
        _shows(page, study_key(here))
        page.wait_for_timeout(2500)
        back = {"box": page.input_value("#agent-request"),
                "users": page.locator("#agent-thread .agent-msg-user").all_text_contents()}
    finally:
        close()
        session.server.shutdown()
    assert asked == ["with a file"], asked
    assert 409 not in saves, saves
    kept = {path: entries for path, entries in _entries_everywhere(workspace).items()
            if any(e.get("text") == "with a file" for e in entries)}
    assert len(kept) == 1 and "/here/" in next(iter(kept)), kept
    entries = next(iter(kept.values()))
    said = [e for e in entries if e.get("text") == "with a file"]
    assert len(said) == 1 and [a["name"] for a in said[0].get("attachments", [])] == ["notes.txt"]
    if seen == "after the answer":
        assert any(e.get("text") == "W." for e in entries), entries
    assert on_other["box"] == "" and not on_other["kept"] and "Not saved" not in on_other["note"]
    assert back["box"] == "" and sum("with a file" in u for u in back["users"]) == 1, back
    assert errors == []


def test_two_enters_with_only_the_view_left_send_once_and_say_nothing_false(tmp_path,
                                                                           monkeypatch):
    """L13 5: only the view left to read; Enter pressed twice. The first sent
    the message once the view was read, and the second then said "Not sent
    ... Send it again"."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("Once.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        page.evaluate("""() => { const real = window.fetch; let seq = 0, metricsAt = -1;
            window.__holdView = true;
            window.fetch = function (url, opts) {
                const u = String(url), n = ++seq, self = this;
                if (u.indexOf('/api/metrics') >= 0) metricsAt = n;
                if (u.indexOf('/api/app-state') < 0) return real.call(self, url, opts);
                return new Promise((ok) => setTimeout(ok, 0)).then(() => {
                    if (metricsAt > n || !window.__holdView) return real.call(self, url, opts);
                    return new Promise((ok) => { const t = setInterval(() => {
                        if (!window.__holdView) { clearInterval(t); ok(real.call(self, url, opts)); }
                    }, 50); });
                });
            }; }""")
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.waits === 'view'", timeout=10000)
        page.fill("#agent-request", "asked on other")
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.evaluate("() => { window.__holdView = false; }")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Once.')", timeout=15000)
        page.wait_for_timeout(2500)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["asked on other"], asked
    assert not note.startswith("Not sent"), note
    assert errors == []


def test_enter_after_a_conversation_read_refused_as_the_gui_moved_waits(tmp_path, monkeypatch):
    """L13 7: the page's read of its study's conversation refused, as the
    GUI had opened another study meanwhile (`study_changed`). Send was
    offered all the same, and Enter went with the view of the study left.
    It waits until the study open and its thread are read."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked, polls = [], []
    page, errors, close = _page_on(session, [_answer("On other.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        _hold(page, polls)
        _switch_elsewhere(session, other)
        refused = []
        page.on("response", lambda r: refused.append(r.json().get("study_changed"))
                if "/api/agent/conversation?" in r.url else None)
        # The page reads its study's conversation again (as at a run's change).
        page.evaluate("() => window.dispatchEvent(new CustomEvent('dashboard:run-changed',"
                      " {detail: {activeRun: '/elsewhere'}}))")
        while not refused:
            page.wait_for_timeout(50)
        page.wait_for_timeout(500)
        _say(page, "asked on other")
        page.wait_for_timeout(1000)
        held = {"asked": _requests(asked), "note": _note(page),
                "waits": page.evaluate("() => window.FastMDXAgentPanel.waits")}
        _release(page, polls)
        _shows(page, study_key(other))
        _when_known(page)
        # The words typed on `here` are kept for it; asked again on `other`.
        _say(page, "asked on other")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('On other.')", timeout=15000)
        page.wait_for_timeout(1500)
    finally:
        close()
        session.server.shutdown()
    assert refused[0] is True
    assert held == {"asked": [], "note": _SEND_READS, "waits": "read"}, held
    assert _requests(asked) == ["asked on other"], asked
    where = _where(workspace, "asked on other")
    assert where and all(w.startswith("other/") for w in where), where
    assert errors == []


# ---- CI-774 round 6: no study is an answer; a chat of no study follows ----


#: Holds the reloaded page's read of its study's conversation while a flag
#: is set in this window's session storage.
_HOLD_RELOADED_READ = (
    "(() => { const real = window.fetch;"
    " window.__holdRead = !!sessionStorage.getItem('__holdRead');"
    " window.fetch = function (url, opts) {"
    "   const u = String(url);"
    "   if (window.__holdRead && /[/]api[/]agent[/]conversation[?]study=/.test(u)"
    "       && (!opts || !opts.method || opts.method === 'GET')) {"
    "     window.__heldRead = u;"
    "     return new Promise((ok) => { const t = setInterval(() => {"
    "       if (!window.__holdRead) { clearInterval(t); ok(real.call(window, url, opts)); } }, 50); }); }"
    "   return real.call(this, url, opts); }; })();")


def _begun(workspace):
    """A folder a run began in and left before it was a study: its log only."""
    begun = workspace / "begun"
    begun.mkdir()
    (begun / "exploration.log").write_text("starting\n", encoding="utf-8")
    return begun


@pytest.mark.parametrize("left", ["no study", "a folder not a study"])
def test_a_reload_with_no_study_open_leaves_send_working(tmp_path, monkeypatch, left):
    """L15 1: the GUI left its study for none (a run that failed at once) or
    for a folder that is not a study, and the page was reloaded: its read of
    the study left was refused, and Send waited for good, as no study open
    was never taken as where the GUI is. The question goes into the
    workspace's chats."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("After a reload.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        if left == "no study":
            session.runtime.data_stale = True
            session.runtime.active_root = None
            page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                                   timeout=30000)
            page.reload(wait_until="domcontentloaded")
        else:
            page.add_init_script(_HOLD_RELOADED_READ)
            page.evaluate("() => sessionStorage.setItem('__holdRead', '1')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => window.__heldRead", timeout=30000)
            session.runtime.active_root = _begun(workspace)
            _shows(page, study_key(workspace / "begun"))
            page.evaluate("() => { sessionStorage.removeItem('__holdRead'); window.__holdRead = false; }")
        page.wait_for_selector("#agent-request", state="visible")
        page.wait_for_function("() => window.FastMDXDashboard.studyKnown()"
                               " && !window.FastMDXAgentPanel.waits", timeout=20000)
        _say(page, "and after a reload?")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('After a reload.')", timeout=15000)
        page.wait_for_timeout(1500)
        note = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["and after a reload?"], asked
    assert "Not saved" not in note, note
    where = _where(workspace, "and after a reload?")
    assert where and all(w.startswith(".fastmdxplora_agent_conversations/") for w in where), where
    assert errors == []


def test_a_question_asked_with_no_study_shown_is_refused_once_one_is_open(tmp_path, monkeypatch):
    """L18 1: with no study open, Enter pressed as another window opened a
    study went to the Agent with that study open, as the page's view named
    no study, and was filed in the workspace's chats. A view naming no study
    is refused while the GUI has one open, as another study's is."""
    import fastmdxplora.agent as agent
    from fastmdxplora.gui.agent_panel import _active_view_error, propose_endpoint
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    other = _study(workspace / "other")
    begun = _begun(workspace)
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    called = []
    monkeypatch.setattr(agent, "completion_for", lambda **_: called.append(True))
    none = {"page": "agent", "study": ""}
    runtime.active_root = other
    answer = propose_endpoint({"request": "Why with none?", "current_view": none}, runtime)
    assert not answer["ok"] and answer["code"] == "agent.view.changed", answer
    assert called == []
    # Where the GUI has no study open, or a folder that is not one, it is not.
    for open_now in (None, begun):
        runtime.active_root = open_now
        assert _active_view_error(none, runtime, None) is None, open_now
    runtime.active_root, runtime.data_stale = other, True
    assert _active_view_error(none, runtime, None) is None
    # A view that says nothing of a study is judged as before.
    runtime.data_stale = False
    assert _active_view_error({"page": "agent"}, runtime, None) is None


def test_the_page_names_no_study_open_in_its_view(tmp_path, monkeypatch):
    """L18 1: the view sent with a message named no study when none was
    open, so the server could not tell it from a view that says nothing of
    one. It names no study open, on every page and in the Viewer's view."""
    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    session.runtime.data_stale = True
    session.runtime.active_root = None
    asked = []
    page, errors, close = _page_on(session, [_answer("With none.")])
    try:
        _asking(page, asked)
        page.wait_for_function("() => window.FastMDXDashboard.studyKnown()"
                               " && !window.FastMDXAgentPanel.waits", timeout=30000)
        _say(page, "asked with no study")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('With none.')", timeout=15000)
        viewer = page.evaluate("() => window.FastMDXMoleculeViewer"
                               " && window.FastMDXMoleculeViewer.currentViewHints()")
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["asked with no study"], asked
    assert asked[0].get("current_view", {}).get("study") == "", asked[0].get("current_view")
    assert viewer and viewer.get("study") == "", viewer
    assert errors == []


@pytest.mark.parametrize("left", ["no study", "a folder not a study"])
def test_words_typed_for_a_study_are_kept_for_it_as_the_gui_leaves_it_for_none(
        tmp_path, monkeypatch, left):
    """L18 3: words typed on `here`, then the GUI goes to no study (a run
    that failed at once) or to a folder that is not a study. The words
    stayed in the box, unsaid, and went into the workspace's chats; for
    another study they are kept for theirs and said. So they are here, and
    are in the box again when `here` is open."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "typed on here"
    page, errors, close = _page_on(session, [])
    try:
        _shows(page, study_key(here))
        _when_known(page)
        page.focus("#agent-request")
        page.keyboard.type(words)
        if left == "no study":
            session.runtime.data_stale = True
            session.runtime.active_root = None
            page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                                   timeout=30000)
        else:
            session.runtime.active_root = _begun(workspace)
            page.wait_for_function("() => window.FastMDXDashboard.state.studyIsOne === false",
                                   timeout=30000)
        page.wait_for_function("() => !document.getElementById('agent-request').value", timeout=15000)
        out = {"note": _note(page), "kept": _unsent(page)}
        session.runtime.data_stale = False
        _switch_elsewhere(session, here)
        page.wait_for_function("(w) => document.getElementById('agent-request').value === w",
                               arg=words, timeout=20000)
        out["back"] = _note(page)
    finally:
        close()
        session.server.shutdown()
    assert out["kept"].get(study_key(here)) == words, out
    assert "The unsent message typed for here" in out["note"] and "saved for it" in out["note"], out
    assert "typed for here" not in out["back"], out
    assert errors == []


def test_words_typed_as_a_run_starts_stay_in_the_box_when_it_ends_at_once(tmp_path, monkeypatch):
    """L18's "dies at once": words typed and Enter pressed as a run this page
    started starts; the run's study shown, then the run ends at once and no
    study is open. The words are the person's about that run: they stay in
    the box, not kept for a study that will not open, and go into the
    workspace's chats when sent."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=2.0)
    words = "why did it fail?"
    asked = []
    page, errors, close = _page_on(session, [dict(_CONFIG), _answer("Because.")])
    try:
        _asking(page, asked)
        _typed_and_entered_during_a_launch(page, here, words)
        _shows(page, study_key(workspace / "run1"))
        _when_known(page)
        session.runtime.data_stale = True
        session.runtime.active_root = None
        page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                               timeout=30000)
        page.wait_for_timeout(2000)
        box, note = page.input_value("#agent-request"), _note(page)
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Because.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert box == words and "saved for it" not in note, (box, note)
    assert _requests(asked) == ["chignolin", words], asked
    where = _where(workspace, words)
    assert where and all(w.startswith(".fastmdxplora_agent_conversations/") for w in where), where
    assert errors == []


@pytest.mark.parametrize("left", ["no study", "a folder not a study"])
def test_a_study_opened_after_no_study_shows_its_own_thread(tmp_path, monkeypatch, left):
    """L15 2: no study open (or a folder that is not one), a question asked
    there; another window opens `other`. The chat of no study stayed on
    screen, and the message written on `other` went into it with `other`'s
    view (or, for the folder, was refused on save and kept for a folder the
    GUI does not open). `other`'s own thread is read in its place."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    if left == "no study":
        session.runtime.data_stale = True
        session.runtime.active_root = None
    else:
        session.runtime.active_root = _begun(workspace)
    asked, saves = [], []
    page, errors, close = _page_on(session, [_answer("With none."), _answer("On other.")])
    page.on("response", lambda r: saves.append(r.status)
            if r.url.endswith("/api/agent/conversation") and r.request.method == "POST" else None)
    try:
        _asking(page, asked)
        page.wait_for_function("() => window.FastMDXDashboard.studyKnown()"
                               " && !window.FastMDXAgentPanel.waits", timeout=30000)
        _say(page, "asked with no study")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('With none.')", timeout=15000)
        page.wait_for_timeout(1000)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study"
                               " && !window.FastMDXAgentPanel.waits", timeout=20000)
        _say(page, "asked on other")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('On other.')", timeout=15000)
        page.wait_for_timeout(1500)
        out = {"note": _note(page), "kept": _unsent(page),
               "users": page.locator("#agent-thread .agent-msg-user").all_text_contents()}
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["asked with no study", "asked on other"], asked
    assert 409 not in saves, saves
    assert out["users"] == ["asked on other"] and not out["kept"], out
    assert "Not saved" not in out["note"], out
    none = _where(workspace, "asked with no study")
    assert none and all(w.startswith(".fastmdxplora_agent_conversations/") for w in none), none
    where = _where(workspace, "asked on other")
    assert where and all(w.startswith("other/") for w in where), where
    assert errors == []


def test_an_edit_refused_by_a_review_is_kept_through_the_choice(tmp_path, monkeypatch):
    """L15 3: an Edit refused because a review holds sending; then the choice
    the note asks for. Its click blurred the bubble, which put the old words
    back: the edit was lost, and the note stayed. The edit stays, the note
    goes once the review is chosen, and Enter in the bubble sends it."""
    import tempfile

    from fastmdxplora.gui.server import start_dashboard_session

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace), host="127.0.0.1", port=0)
    _seed_a_review(session)
    live = "#agent-thread .agent-study:not(.is-replaced):not([hidden])"
    asked = []
    page, errors, close = _page_on(session, [_answer("Roughly a day.")])
    try:
        _asking(page, asked)
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"]').wait_for()
        page.wait_for_function("() => !window.FastMDXAgentPanel.waits", timeout=20000)
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"] '
                     '[data-choice="reject"]').click()
        page.wait_for_timeout(500)
        msg = page.locator("#agent-thread .agent-msg-user").filter(has_text="How long will it take?")
        msg.hover()
        msg.get_by_role("button", name="Edit").click()
        page.keyboard.type(" Roughly?")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        refused = _note(page)
        page.locator(f"{live} button", has_text="Use reviewed version").first.click()
        page.wait_for_selector("#agent-thread .agent-msg-user:has-text('Reviewed version 2')",
                               timeout=15000)
        page.wait_for_timeout(500)
        chosen = {"bubble": msg.inner_text(), "editing": msg.locator(".editing").count(),
                  "note": _note(page), "asked": _requests(asked)}
        msg.locator(".editing").press("Enter")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Roughly a day.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert refused.startswith("Not sent: choose in the review above first"), refused
    assert "How long will it take? Roughly?" in chosen["bubble"] and chosen["editing"] == 1, chosen
    assert chosen["note"] == "" and chosen["asked"] == [], chosen
    assert _requests(asked) == ["How long will it take? Roughly?"], asked
    assert errors == []


@pytest.mark.parametrize("way", ["no study, one opened", "a study, another opened", "a reload"])
def test_an_edit_refused_by_a_review_is_kept_as_its_thread_is_replaced(tmp_path, monkeypatch, way):
    """L17 B and L18 4: an Edit refused because a review holds sending, then
    the thread replaced: another window opens a study, or the page is
    reloaded. The edited words were nowhere, and the note still asked for a
    choice in a review no longer on screen. They are saved for the study
    they were written for (with no study, put in the box), and said; the
    note asking for the review's choice goes with the review."""
    import tempfile

    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import study_key

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", tempfile.mkdtemp())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    here = _study(workspace / "here")
    other = _study(workspace / "other")
    start = workspace if way.startswith("no study") else here
    session = start_dashboard_session(output=str(start), host="127.0.0.1", port=0)
    _seed_a_review(session)
    live = "#agent-thread .agent-study:not(.is-replaced):not([hidden])"
    edited = "How long will it take? Roughly?"
    page, errors, close = _page_on(session, [])
    try:
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"]').wait_for()
        page.wait_for_function("() => !window.FastMDXAgentPanel.waits", timeout=20000)
        page.locator(f'{live} [data-review-setting="simulation.temperature_K"] '
                     '[data-choice="reject"]').click()
        page.wait_for_timeout(500)
        msg = page.locator("#agent-thread .agent-msg-user").filter(has_text="How long will it take?")
        msg.hover()
        msg.get_by_role("button", name="Edit").click()
        page.keyboard.type(" Roughly?")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        refused = _note(page)
        if way == "a reload":
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("(w) => document.getElementById('agent-request').value.includes(w)",
                                   arg=edited, timeout=20000)
        else:
            _switch_elsewhere(session, other)
            _shows(page, study_key(other))
            page.wait_for_function("() => window.FastMDXAgentPanel.current.study", timeout=20000)
        page.wait_for_timeout(1000)
        out = {"note": _note(page), "box": page.input_value("#agent-request"), "kept": _unsent(page)}
        if way == "a study, another opened":
            _switch_elsewhere(session, here)
            page.wait_for_function("(w) => document.getElementById('agent-request').value.includes(w)",
                                   arg=edited, timeout=20000)
            out["back"] = page.input_value("#agent-request")
    finally:
        close()
        session.server.shutdown()
    assert refused.startswith("Not sent: choose in the review above first"), refused
    assert "choose in the review" not in out["note"], out
    if way == "no study, one opened":
        assert out["box"] == edited and "The edit not sent is in the box" in out["note"], out
    elif way == "a study, another opened":
        assert out["box"] == "" and out["kept"].get(study_key(here)) == edited, out
        assert "The edit not sent is saved for here" in out["note"], out
        assert out["back"] == edited, out
    else:
        # Said why it is there (L20: nothing was).
        assert out["box"] == edited, out
        assert out["note"] == ("The edit not sent as the page was left is in the box, "
                               "to send from there."), out
    assert errors == []


@pytest.mark.parametrize("way", ["edit", "retry"])
def test_edit_and_retry_send_the_message_s_file_with_it(tmp_path, monkeypatch, way):
    """L16: Edit made the line naming the files editable and sent it as
    words, the file dropped; Retry sent no file either. The words alone are
    edited, and the file is read again and sent with them."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    (workspace / "notes.txt").write_text("n\n", encoding="utf-8")
    asked = []
    page, errors, close = _page_on(session, [_answer("First."), _answer("Again.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        page.evaluate("""(p) => { const i = document.getElementById('agent-attach-path');
            i.value = p; i.dispatchEvent(new Event('change')); }""", str(workspace / "notes.txt"))
        page.wait_for_selector("#agent-attachments :text('notes.txt')")
        _say(page, "with a file")
        page.wait_for_selector("#agent-thread .agent-answer:has-text('First.')", timeout=15000)
        msg = page.locator("#agent-thread .agent-msg-user").first
        msg.hover()
        if way == "edit":
            msg.get_by_role("button", name="Edit").click()
            editing = page.locator("#agent-thread .editing").inner_text()
            page.keyboard.type(" edited")
            page.keyboard.press("Enter")
        else:
            editing = ""
            msg.get_by_role("button", name="Retry").click()
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Again.')", timeout=15000)
        page.wait_for_timeout(1500)
        users = page.locator("#agent-thread .agent-msg-user").all_text_contents()
    finally:
        close()
        session.server.shutdown()
    words = "with a file edited" if way == "edit" else "with a file"
    assert _requests(asked) == ["with a file", words], asked
    assert [a["name"] for a in asked[1].get("attachments", [])] == ["notes.txt"], asked[1]
    assert "notes.txt" not in editing, editing
    assert len(users) == 1 and users[0].startswith(words) and "notes.txt" in users[0], users
    said = [e for entries in _entries_everywhere(workspace).values() for e in entries
            if e.get("role") == "user"]
    assert [e["text"] for e in said] == [words], said
    assert [a["name"] for a in said[0].get("attachments", [])] == ["notes.txt"], said
    assert errors == []


#: Holds the page's reads of an attachment while `__holdAttachment`.
_HOLD_ATTACHMENT = ("() => { const real = window.fetch; window.__holdAttachment = true;"
                    " window.fetch = function (url, opts) {"
                    "   if (window.__holdAttachment && String(url).indexOf('/api/agent/attachment') >= 0) {"
                    "     window.__heldAttachment = true;"
                    "     return new Promise((ok) => { const t = setInterval(() => {"
                    "       if (!window.__holdAttachment) { clearInterval(t); ok(real.call(window, url, opts)); }"
                    "     }, 50); }); }"
                    "   return real.call(this, url, opts); }; }")


def _a_message_with_a_file(page, session, workspace, here):
    """"with a file" sent with notes.txt, and answered "First."."""
    from fastmdxplora.gui.workspace import study_key

    (workspace / "notes.txt").write_text("n\n", encoding="utf-8")
    _shows(page, study_key(here))
    _when_known(page)
    page.evaluate("""(p) => { const i = document.getElementById('agent-attach-path');
        i.value = p; i.dispatchEvent(new Event('change')); }""", str(workspace / "notes.txt"))
    page.wait_for_selector("#agent-attachments :text('notes.txt')")
    _say(page, "with a file")
    page.wait_for_selector("#agent-thread .agent-answer:has-text('First.')", timeout=15000)
    return page.locator("#agent-thread .agent-msg-user").first


def test_an_edit_refused_as_its_file_is_read_again_is_kept(tmp_path, monkeypatch):
    """L18 4: Edit of a message with a file, Enter, and another window opens
    a study while the file is read again: "Not sent: another conversation is
    shown now.", and the edited words were nowhere. They are saved for the
    study they were written for, and said; with the message's file (L20: the
    words came back without it, and went without it)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("First."), _answer("Edited.")])
    try:
        _asking(page, asked)
        msg = _a_message_with_a_file(page, session, workspace, here)
        page.evaluate(_HOLD_ATTACHMENT)
        msg.hover()
        msg.get_by_role("button", name="Edit").click()
        page.keyboard.type(" edited")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__heldAttachment", timeout=10000)
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study", timeout=20000)
        page.wait_for_timeout(1000)
        page.evaluate("() => { window.__holdAttachment = false; }")
        page.wait_for_function("() => /The edit not sent/.test((document.getElementById("
                               "'agent-unsent-note') || {}).textContent || '')", timeout=15000)
        out = {"note": _note(page), "box": page.input_value("#agent-request"), "kept": _unsent(page)}
        _switch_elsewhere(session, here)
        _shows(page, study_key(here))
        page.wait_for_function("(w) => document.getElementById('agent-request').value === w",
                               arg="with a file edited", timeout=20000)
        page.wait_for_timeout(1000)
        out["chips"] = page.locator("#agent-attachments").inner_text()
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Edited.')", timeout=15000)
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["with a file", "with a file edited"], asked
    assert out["box"] == "" and out["kept"].get(study_key(here)) == "with a file edited", out
    assert out["note"].startswith("Not sent: another conversation is shown now."), out
    assert "The edit not sent is saved for here" in out["note"], out
    assert "notes.txt" in out["chips"], out
    assert [a["name"] for a in asked[1].get("attachments", [])] == ["notes.txt"], asked[1]
    assert errors == []


@pytest.mark.parametrize("way", ["edit, the file changed", "retry, the file gone"])
def test_edit_and_retry_with_a_file_gone_or_changed_send_nothing(tmp_path, monkeypatch, way):
    """L17 C: Edit and Retry send the message's files again; one gone or
    changed since is named and nothing is sent or cut, and an edit stays
    in its bubble, Escape putting the old words and the file line back."""
    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("First.")])
    try:
        _asking(page, asked)
        msg = _a_message_with_a_file(page, session, workspace, here)
        if way.endswith("changed"):
            (workspace / "notes.txt").write_text("changed\n", encoding="utf-8")
        else:
            (workspace / "notes.txt").unlink()
        msg.hover()
        if way.startswith("edit"):
            msg.get_by_role("button", name="Edit").click()
            page.keyboard.type(" edited")
            page.keyboard.press("Enter")
        else:
            msg.get_by_role("button", name="Retry").click()
        page.wait_for_function("() => /gone or changed since/.test((document.getElementById("
                               "'agent-unsent-note') || {}).textContent || '')", timeout=15000)
        page.wait_for_timeout(500)
        out = {"note": _note(page), "bubble": msg.inner_text(),
               "editing": msg.locator(".editing").count(),
               "answers": page.locator("#agent-thread .agent-answer").count()}
        if way.startswith("edit"):
            page.click("#agent-request")
            page.wait_for_timeout(300)
            out["clicked"] = msg.locator(".editing").count()
            msg.locator(".editing").press("Escape")
            page.wait_for_timeout(300)
            out["escaped"] = msg.inner_text()
    finally:
        close()
        session.server.shutdown()
    assert _requests(asked) == ["with a file"], asked
    assert "notes.txt is gone or changed since" in out["note"], out
    assert out["answers"] == 1, out
    if way.startswith("edit"):
        assert "with a file edited" in out["bubble"] and out["editing"] == 1, out
        assert out["clicked"] == 1, out
        assert "edited" not in out["escaped"] and "notes.txt" in out["escaped"], out
    else:
        assert out["editing"] == 0 and "notes.txt" in out["bubble"], out
    assert errors == []


def test_enter_just_after_a_switch_put_words_back_sends_nothing(tmp_path, monkeypatch):
    """L16: words left unsent on `here`; another window opens `other`, where
    the person types; another window opens `here` again, and the person
    presses Enter for what they typed. The words put back for `here` went,
    not those typed. Said, and the next Enter sends them; the note then says
    no more that they were not sent."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    asked = []
    page, errors, close = _page_on(session, [_answer("Old.")])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        page.fill("#agent-request", "old words")
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => !document.getElementById('agent-request').value", timeout=15000)
        page.focus("#agent-request")
        page.keyboard.type("new words")
        _switch_elsewhere(session, here)
        page.wait_for_function("() => document.getElementById('agent-request').value === 'old words'",
                               timeout=15000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        first = {"asked": _requests(asked), "note": _note(page), "box": page.input_value("#agent-request")}
        _send_when_offered(page)
        page.wait_for_selector("#agent-thread .agent-answer:has-text('Old.')", timeout=15000)
        page.wait_for_timeout(500)
        kept, after = _unsent(page), _note(page)
    finally:
        close()
        session.server.shutdown()
    assert first["asked"] == [] and first["box"] == "old words", first
    assert "Press Enter again to send them" in first["note"], first
    assert _requests(asked) == ["old words"], asked
    assert kept.get(study_key(other)) == "new words", kept
    # L17 A, L18: once they are sent, the note no longer says they were not;
    # what it says of the words saved for `other` stays.
    assert "Not sent" not in after and "Press Enter again" not in after, after
    assert "typed for other is saved for it" in after, after
    assert errors == []


def test_a_config_builder_launch_takes_only_its_run_s_study_as_its_own(browser, site,  # noqa: F811
                                                                         monkeypatch):
    """L1 4 and L2 M2: the Config Builder's Start pressed, another window
    opens a study while the launch waits, and this page sees it; then the
    run starts and this page sees its study. The other window's study is
    said as opened elsewhere, the run's own is not."""
    from fastmdxplora.gui.workspace import study_key

    made = _launches(monkeypatch, site["root"])
    page = _open(browser, site)
    try:
        page.evaluate("""() => { window.__else = [];
            window.addEventListener('dashboard:study-changed-elsewhere',
                                    (e) => window.__else.push(e.detail.to)); }""")
        _builder(page, site)
        page.wait_for_timeout(800)
        held = []
        page.route("**/api/run", lambda route: held.append(route))
        with page.expect_request("**/api/run"):
            page.click("#run-start-button")
        _switch_elsewhere(site["session"], site["studies"][1])
        _shows(page, site["keys"][1])
        page.wait_for_timeout(500)
        held[0].continue_()
        page.unroute("**/api/run")
        _shows(page, study_key(site["root"] / "run1"))
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
        errors = list(page.errors)
    finally:
        page.context.close()
    assert made
    assert said == [site["keys"][1]], said
    assert errors == []


def test_run_as_it_is_takes_its_run_s_study_as_its_own(browser, site, monkeypatch):  # noqa: F811
    """L4 M2: the Config Builder's "Run on this machine as it is" opens its
    run's study as this window's own change: not said as opened elsewhere."""
    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.workspace import study_key

    root = site["root"]
    config = root / "as_it_is.yml"
    config.write_text("systems:\n- system: 1UAO\n", encoding="utf-8")
    made = []

    def launch(self, config_path, output=None, dashboard_url=None):
        folder = root / "run1"
        folder.mkdir()
        (folder / "exploration.yml").write_text("systems: []\n", encoding="utf-8")
        made.append(str(folder))
        self.active_root = folder
        return {"ok": True, "output": str(folder), "config_path": str(config_path)}

    monkeypatch.setattr(DashboardRuntime, "launch_existing_config", launch)
    page = _open(browser, site)
    try:
        page.route("**/api/check-config", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "phases": ["simulation"], "systems": 1,
                             "settings_named": 1})))
        _events(page)
        page.evaluate("window.FastMDXDashboard.navigate('run')")
        page.evaluate("() => window.FastMDXRun.setStart('config')")
        page.fill("#run-config-path", str(config))
        page.click("#run-check-config")
        page.wait_for_function("() => !document.getElementById('run-as-is').disabled")
        page.click("#run-as-is")
        _shows(page, study_key(root / "run1"))
        page.wait_for_timeout(3000)
        said = page.evaluate("() => window.__else")
        errors = list(page.errors)
    finally:
        page.context.close()
    assert made
    assert said == [], said
    assert errors == []


def test_an_older_poll_answered_late_is_not_applied_over_a_newer_one(browser, site):  # noqa: F811
    """L4 L2: a poll's status read before another window opened a study,
    answered after a later poll that read that study: not applied, so the
    page neither goes back nor says the study it showed opened elsewhere."""
    page = _open(browser, site)
    late = []
    try:
        page.wait_for_timeout(1000)
        _events(page)

        def slow(route):
            if late:
                route.continue_()
            else:
                late.append((route, route.fetch()))

        page.route("**/api/status*", slow)
        page.evaluate("() => document.getElementById('refresh-now').click()")
        while not late:
            page.wait_for_timeout(50)
        _switch_elsewhere(site["session"], site["studies"][1])
        page.evaluate("() => document.getElementById('refresh-now').click()")
        _shows(page, site["keys"][1])
        route, answer = late[0]
        route.fulfill(response=answer)
        page.wait_for_timeout(2000)
        shown = page.evaluate("() => window.FastMDXDashboard.state.studyShown")
        said = page.evaluate("() => window.__else")
        errors = list(page.errors)
    finally:
        page.context.close()
    assert shown == site["keys"][1]
    assert said == [site["keys"][1]], said
    assert errors == []


def test_run_as_it_is_with_an_unreadable_answer_is_offered_again(browser, site):  # noqa: F811
    """L6 L2: "Run on this machine as it is" whose answer cannot be read:
    said as Start says it, its button offered again, no page error."""
    root = site["root"]
    config = root / "as_it_is.yml"
    config.write_text("systems:\n- system: 1UAO\n", encoding="utf-8")
    page = _open(browser, site)
    try:
        page.route("**/api/check-config", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "phases": ["simulation"], "systems": 1,
                             "settings_named": 1})))
        page.route("**/api/run-config", lambda route: route.fulfill(
            status=502, content_type="text/html", body="<h1>Bad gateway</h1>"))
        page.evaluate("window.FastMDXDashboard.navigate('run')")
        page.evaluate("() => window.FastMDXRun.setStart('config')")
        page.fill("#run-config-path", str(config))
        page.click("#run-check-config")
        page.wait_for_function("() => !document.getElementById('run-as-is').disabled")
        page.click("#run-as-is")
        page.wait_for_function(
            "() => document.getElementById('run-note').textContent === 'Could not reach the server.'")
        page.wait_for_function("() => !document.getElementById('run-as-is').disabled")
        page.wait_for_timeout(500)
        errors = list(page.errors)
    finally:
        page.context.close()
    assert errors == []


@pytest.mark.parametrize("start", ["no study", "a study"])
def test_a_message_refused_as_a_study_opens_is_put_back_or_saved(tmp_path, monkeypatch, start):
    """L19 M1 (L18's orders): Enter pressed as another window opens a study,
    from no study or from another study; the Agent refuses it, and it was
    left in its thread with the refusal, the thread then out of view: the
    words were gone, unsaid. They are in the box (no study) or saved for
    the study they were written in (said), and sent from there once."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "pressed as other opened"
    asked, held, polls = [], [], []

    def propose(route):
        request = json.loads(route.request.post_data or "{}").get("request")
        asked.append(request)
        if request == words and not held:
            held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(_answer("A: " + request)))

    page, errors, close = _page_on(session, [])
    try:
        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", propose)
        if start == "no study":
            session.runtime.data_stale = True
            session.runtime.active_root = None
            page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                                   timeout=30000)
            page.wait_for_function("() => window.FastMDXDashboard.studyKnown()"
                                   " && !window.FastMDXAgentPanel.waits", timeout=30000)
            _say(page, "with none first")
            page.wait_for_selector("#agent-thread .agent-answer:has-text('A: with none first')",
                                   timeout=15000)
            page.wait_for_timeout(500)
        else:
            _shows(page, study_key(here))
            _when_known(page)
        _hold(page, polls)
        _say(page, words)
        while not held:
            page.wait_for_timeout(50)
        session.runtime.data_stale = False
        _switch_elsewhere(session, other)
        held[0].continue_()
        page.wait_for_function("() => /Not sent, as the study open changed|Select it again/"
                               ".test(document.getElementById('agent-thread').textContent + ' '"
                               " + ((document.getElementById('agent-unsent-note') || {}).textContent || ''))",
                               timeout=15000)
        page.wait_for_timeout(300)
        refused = {"note": _note(page), "box": page.input_value("#agent-request")}
        assert refused["box"] == words, refused
        _release(page, polls)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study", timeout=20000)
        page.wait_for_timeout(1500)
        on_other = {"note": _note(page), "box": page.input_value("#agent-request"),
                    "kept": _unsent(page),
                    "users": page.locator("#agent-thread .agent-msg-user").all_text_contents()}
        if start == "a study":
            _switch_elsewhere(session, here)
            _shows(page, study_key(here))
            page.wait_for_function("(w) => document.getElementById('agent-request').value === w",
                                   arg=words, timeout=20000)
        _send_when_offered(page)
        page.wait_for_selector(f"#agent-thread .agent-answer:has-text('A: {words}')", timeout=15000)
        page.wait_for_timeout(1500)
    finally:
        close()
        session.server.shutdown()
    assert refused["note"].startswith("Not sent, as the study open changed as it was sent"), refused
    assert on_other["users"] == [], on_other
    if start == "no study":
        assert on_other["box"] == words and "back in the box" in on_other["note"], on_other
        place = "other/"
    else:
        assert on_other["box"] == "" and on_other["kept"].get(study_key(here)) == words, on_other
        assert "The unsent message typed for here is saved for it" in on_other["note"], on_other
        place = "here/"
    first = ["with none first"] if start == "no study" else []
    assert asked == [*first, words, words], asked
    where = _where(workspace, words)
    assert len(where) == 1 and where[0].startswith(place), where
    said = [e for e in _entries_everywhere(workspace)[str(workspace / where[0])] if e.get("text") == words]
    assert len(said) == 1, said
    assert errors == []


def _from_no_study(page, session):
    """The GUI shows no study, and "with none first" is answered there."""
    session.runtime.data_stale = True
    session.runtime.active_root = None
    page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                           timeout=30000)
    page.wait_for_function("() => window.FastMDXDashboard.studyKnown()"
                           " && !window.FastMDXAgentPanel.waits", timeout=30000)
    _say(page, "with none first")
    page.wait_for_selector("#agent-thread .agent-answer:has-text('A: with none first')",
                           timeout=15000)
    page.wait_for_timeout(500)


def _attach_notes(page, workspace):
    page.evaluate("""(p) => { const i = document.getElementById('agent-attach-path');
        i.value = p; i.dispatchEvent(new Event('change')); }""", str(workspace / "notes.txt"))
    page.wait_for_selector("#agent-attachments :text('notes.txt')")


@pytest.mark.parametrize("what", ["a message", "an edit", "a message of no study"])
def test_a_refused_message_s_file_read_as_the_switch_is_seen_comes_back(tmp_path, monkeypatch,
                                                                         what):
    """L21 M1, L22 M1: a message (or an Edit) with a file, refused as
    another window opens a study, the refusal coming before the page sees
    the switch: put back in the box and its file read again. Seen while the
    file was read, the switch saved the words without it, and the file read
    after was dropped, unsaid. It comes back as a chip with the words, on
    the study they were written in, and is sent with them."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    (workspace / "notes.txt").write_text("n\n", encoding="utf-8")
    words = "with a file edited" if what == "an edit" else "pressed with a file"
    asked, held, polls, reads, holding = [], [], [], [], []

    def propose(route):
        body = json.loads(route.request.post_data or "{}")
        asked.append(body)
        if body.get("request") == words and not held:
            # Its file read again from here on is held.
            holding.append(True)
            held.append(route)
        else:
            request = body.get("request")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                _answer("First." if request == "with a file" else "A: " + request)))

    def attachment(route):
        if holding:
            reads.append(route)
        else:
            route.continue_()

    page, errors, close = _page_on(session, [])
    try:
        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", propose)
        page.route("**/api/agent/attachment*", attachment)
        if what == "an edit":
            msg = _a_message_with_a_file(page, session, workspace, here)
            msg.hover()
            msg.get_by_role("button", name="Edit").click()
            page.keyboard.type(" edited")
        else:
            if what == "a message of no study":
                _from_no_study(page, session)
            else:
                _shows(page, study_key(here))
                _when_known(page)
            _attach_notes(page, workspace)
            page.fill("#agent-request", words)
        _hold(page, polls)
        page.keyboard.press("Enter")
        while not held:
            page.wait_for_timeout(50)
        session.runtime.data_stale = False
        _switch_elsewhere(session, other)
        held[0].continue_()
        page.wait_for_function("() => /Not sent, as the study open changed/.test((document"
                               ".getElementById('agent-unsent-note') || {}).textContent || '')",
                               timeout=15000)
        while not reads:
            page.wait_for_timeout(50)
        refused = {"box": page.input_value("#agent-request"), "chips": page.locator(
            "#agent-attachments").inner_text()}
        _release(page, polls)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study", timeout=20000)
        page.wait_for_timeout(1500)
        del holding[:]
        for route in list(reads):
            route.continue_()
        page.wait_for_timeout(1500)
        on_other = {"note": _note(page), "box": page.input_value("#agent-request"),
                    "kept": _unsent(page), "chips": page.locator("#agent-attachments").inner_text()}
        if what != "a message of no study":
            _switch_elsewhere(session, here)
            _shows(page, study_key(here))
            page.wait_for_function("(w) => document.getElementById('agent-request').value === w",
                                   arg=words, timeout=20000)
        page.wait_for_selector("#agent-attachments :text('notes.txt')", timeout=15000)
        _send_when_offered(page)
        page.wait_for_selector(f"#agent-thread .agent-answer:has-text('A: {words}')", timeout=15000)
        page.wait_for_timeout(1000)
    finally:
        close()
        session.server.shutdown()
    assert refused["box"] == words and "notes.txt" not in refused["chips"], refused
    if what == "a message of no study":
        assert on_other["box"] == words and "notes.txt" in on_other["chips"], on_other
        assert "back in the box" in on_other["note"], on_other
    else:
        assert on_other["box"] == "" and on_other["kept"].get(study_key(here)) == words, on_other
        assert "The unsent message typed for here is saved for it" in on_other["note"], on_other
    first = {"an edit": ["with a file"], "a message of no study": ["with none first"]}.get(what, [])
    assert _requests(asked) == [*first, words, words], _requests(asked)
    assert [a["name"] for a in asked[-1].get("attachments", [])] == ["notes.txt"], asked[-1]
    assert errors == []


@pytest.mark.parametrize("start", ["a study", "no study"])
def test_a_message_refused_as_a_study_opens_names_the_study(tmp_path, monkeypatch, start):
    """L21 L2, L22 L4: the notes of a message refused as another window
    opened a study named no study, where every other note names it by its
    folder. Saved for the study it was written in (the page has seen the
    switch as the refusal comes), the note names that study; from no study,
    it names the study opened."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "pressed as other opened"
    asked, held, polls, threads, holding = [], [], [], [], []

    def propose(route):
        request = json.loads(route.request.post_data or "{}").get("request")
        asked.append(request)
        if request == words and not held:
            held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(_answer("A: " + request)))

    def conversation(route):
        if holding:
            threads.append(route)
        else:
            route.continue_()

    page, errors, close = _page_on(session, [])
    try:
        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", propose)
        page.route("**/api/agent/conversation*", conversation)
        if start == "no study":
            _from_no_study(page, session)
            _hold(page, polls)
        else:
            _shows(page, study_key(here))
            _when_known(page)
        _say(page, words)
        while not held:
            page.wait_for_timeout(50)
        session.runtime.data_stale = False
        if start == "a study":
            # Seen before the refusal comes, the other study's thread read
            # held meanwhile: the message is saved for `here`.
            holding.append(True)
            _switch_elsewhere(session, other)
            _shows(page, study_key(other))
            while not threads:
                page.wait_for_timeout(50)
            held[0].continue_()
        else:
            _switch_elsewhere(session, other)
            held[0].continue_()
        page.wait_for_function("() => /^Not sent, as /.test((document.getElementById("
                               "'agent-unsent-note') || {}).textContent || '')", timeout=15000)
        page.wait_for_timeout(300)
        refused = _note(page)
        del holding[:]
        for route in list(threads):
            route.continue_()
        _release(page, polls)
        _shows(page, study_key(other))
        page.wait_for_function("() => window.FastMDXAgentPanel.current.study", timeout=20000)
        page.wait_for_timeout(1500)
        on_other = {"note": _note(page), "box": page.input_value("#agent-request"),
                    "kept": _unsent(page)}
    finally:
        close()
        session.server.shutdown()
    if start == "a study":
        assert refused == ("Not sent, as the study open changed as it was sent: the message is "
                           "saved for here, where it was written: it is in the box again when "
                           "here is open."), refused
        assert on_other["box"] == "" and on_other["kept"].get(study_key(here)) == words, on_other
        assert on_other["note"] == refused, on_other
    else:
        assert refused.startswith("Not sent, as the study open changed as it was sent"), refused
        assert on_other["box"] == words, on_other
        assert on_other["note"] == ("Not sent, as other was opened as it was sent: "
                                    "the message is back in the box."), on_other
    assert errors == []


def test_the_welcome_is_shown_from_its_top_once_a_refused_message_is_taken_out(tmp_path,
                                                                               monkeypatch):
    """L22 L5: at 1440, a message refused as another window opened a study
    was taken out of the thread, and what can be asked was shown again
    scrolled where the thread was, its heading under the Agent bar."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "pressed as other opened"
    held, polls = [], []

    def propose(route):
        if not held:
            held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(_answer("A")))

    page, errors, close = _page_on(session, [])
    try:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.unroute("**/api/agent/propose*")
        page.route("**/api/agent/propose*", propose)
        _shows(page, study_key(here))
        _when_known(page)
        _hold(page, polls)
        _say(page, words)
        while not held:
            page.wait_for_timeout(50)
        _switch_elsewhere(session, other)
        held[0].continue_()
        page.wait_for_function("() => /Not sent, as the study open changed/.test((document"
                               ".getElementById('agent-unsent-note') || {}).textContent || '')",
                               timeout=15000)
        page.wait_for_timeout(1000)
        shown = page.evaluate("""() => {
            const main = document.querySelector('.main');
            const head = document.querySelector('section[data-page=agent] .page-header');
            const title = document.querySelector('#agent-start .agent-start-title');
            return {top: main.scrollTop, over: main.scrollHeight > main.clientHeight,
                    start: !document.getElementById('agent-start').hidden,
                    title: title.getBoundingClientRect().top,
                    head: head.getBoundingClientRect().bottom};
        }""")
        _release(page, polls)
    finally:
        close()
        session.server.shutdown()
    assert shown["start"] and shown["over"], shown
    assert shown["top"] == 0 and shown["title"] >= shown["head"], shown
    assert errors == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_at_390_a_note_of_two_lines_hides_the_page_beneath(tmp_path, monkeypatch, scheme):
    """L20: at 390, on a study's Agent page with its starters, a note of two
    lines or more showed the page scrolled beneath it through its first
    line. The note reads the same with what is beneath it hidden (a phone
    of 390 by 664, the starters running on under the box)."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    page, errors, close = _page_on(session, [])
    try:
        page.set_viewport_size({"width": 390, "height": 664})
        page.evaluate("(s) => { document.documentElement.dataset.theme = s;"
                      " document.body.dataset.theme = s; }", scheme)
        _shows(page, study_key(here))
        _when_known(page)
        page.focus("#agent-request")
        page.keyboard.type("typed on here")
        _switch_elsewhere(session, other)
        _shows(page, study_key(other))
        page.wait_for_function("() => /saved for it/.test((document.getElementById("
                               "'agent-unsent-note') || {}).textContent || '')", timeout=20000)
        page.wait_for_timeout(1000)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        note = page.locator("#agent-unsent-note")
        box = note.bounding_box()
        lines = page.evaluate("() => { const n = document.getElementById('agent-unsent-note');"
                              " return Math.round(n.getBoundingClientRect().height"
                              " / parseFloat(getComputedStyle(n).lineHeight)); }")
        beneath = page.evaluate("""() => { const r = document.getElementById('agent-unsent-note')
            .getBoundingClientRect();
            return document.elementsFromPoint(r.left + 40, r.top + 4)
                .some((e) => e.matches('.agent-starter')); }""")
        dark = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        clip = {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}
        shown = page.screenshot(clip=clip, animations="disabled")
        page.evaluate("""() => { const c = document.querySelector('.agent-composer');
            for (const n of c.parentElement.children) if (n !== c) n.style.visibility = 'hidden'; }""")
        page.wait_for_timeout(300)
        alone = page.screenshot(clip=clip, animations="disabled")
    finally:
        close()
        session.server.shutdown()
    assert lines >= 2 and beneath, (lines, beneath)
    assert (dark == "rgb(0, 0, 0)") == (scheme == "dark"), dark
    assert shown == alone
    assert errors == []


def test_a_folder_holding_only_a_moved_conversation_is_no_study_open(tmp_path):
    """L19 L1, L20: a folder the GUI shows as no study that holds only a
    conversation moved there (a run stopped in its first second) counted as
    a study open, so every message sent there, naming no study, was refused."""
    from fastmdxplora.gui.agent_panel import _active_view_error
    from fastmdxplora.gui.exploration import DashboardRuntime

    workspace = tmp_path / "workspace"
    run = workspace / "run1"
    (run / "agent" / "conversations").mkdir(parents=True)
    (run / "agent" / "conversations" / "conv-1.json").write_text('{"entries": []}', encoding="utf-8")
    runtime = DashboardRuntime(workspace_root=workspace, exploration_root=workspace)
    runtime.active_root = run
    assert runtime.snapshot().get("active_run") is None
    assert _active_view_error({"page": "agent", "study": ""}, runtime, None) is None


def test_a_reload_during_a_builder_launch_says_where_the_words_are(tmp_path, monkeypatch):
    """L19 L2: a Config Builder launch unanswered, words typed and Enter
    (refused, Send waits); the GUI goes to the run's study (here `other`)
    and the page is reloaded. The words were saved for the study they were
    typed in, unsaid: only the panel's own launches were counted."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch)
    words = "typed as the Builder starts"
    asked = []
    page, errors, close = _page_on(session, [])
    try:
        _asking(page, asked)
        _shows(page, study_key(here))
        _when_known(page)
        page.evaluate("() => window.FastMDXDashboard.studyChangeAsked()")
        page.focus("#agent-request")
        page.keyboard.type(words)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        refused = _note(page)
        _switch_elsewhere(session, other)
        page.reload(wait_until="domcontentloaded")
        _shows(page, study_key(other))
        page.wait_for_function("() => /as a run started is saved for it/.test((document"
                               ".getElementById('agent-unsent-note') || {}).textContent || '')",
                               timeout=15000)
        kept, box, note = _unsent(page), page.input_value("#agent-request"), _note(page)
    finally:
        close()
        session.server.shutdown()
    assert refused == _SEND_WAITS, refused
    assert _requests(asked) == [], asked
    assert kept.get(study_key(here)) == words and box == "", (kept, box)
    assert note.startswith("The unsent message typed for here as a run started is saved for it"), note
    assert errors == []


def test_words_for_an_earlier_run_s_study_are_saved_as_the_gui_leaves_it(tmp_path, monkeypatch):
    """L19 L4: words typed for a run this page started stay in the box when
    it ends at once, for a while after its launch answered; that while was
    one for every run, the latest launch's. Words typed on run1's study,
    opened again after a later launch (run2) and past run1's own while, then
    the GUI going to no study: they stayed in the box. They are saved for
    run1 and said."""
    from fastmdxplora.gui.workspace import study_key

    workspace, here, other, made, session = _here_and_other(tmp_path, monkeypatch, taking=0.5)
    words = "about run1"
    run1, run2 = study_key(workspace / "run1"), study_key(workspace / "run2")
    page, errors, close = _page_on(session, [dict(_CONFIG), dict(_CONFIG)])
    try:
        _shows(page, study_key(here))
        _when_known(page)
        # The page's clock, put on past run1's while once run1 has answered.
        page.evaluate("() => { const real = Date.now; window.__ahead = 0;"
                      " Date.now = () => real() + window.__ahead; }")
        _say(page, "chignolin")
        page.wait_for_selector(_RUN)
        page.wait_for_timeout(800)
        page.click(_RUN)
        _shows(page, run1)
        _when_known(page)
        page.evaluate("() => { window.__ahead = 31000; }")
        _say(page, "again")
        page.wait_for_function(
            "() => document.querySelectorAll('#agent-thread .agent-study:not([hidden])').length === 2")
        page.wait_for_timeout(800)
        page.click(f"{_RUN}:visible >> nth=-1")
        _shows(page, run2)
        _when_known(page)
        _switch_elsewhere(session, workspace / "run1")
        _shows(page, run1)
        _when_known(page)
        page.focus("#agent-request")
        page.keyboard.type(words)
        session.runtime.data_stale = True
        session.runtime.active_root = None
        page.wait_for_function("() => !(window.FastMDXDashboard.state.appState || {}).active_run",
                               timeout=30000)
        page.wait_for_timeout(2000)
        out = {"box": page.input_value("#agent-request"), "kept": _unsent(page), "note": _note(page)}
    finally:
        close()
        session.server.shutdown()
    assert len(made) == 2, made
    assert out["box"] == "" and out["kept"].get(run1) == words, out
    assert "The unsent message typed for run1 is saved for it" in out["note"], out
    assert errors == []
