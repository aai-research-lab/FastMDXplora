"""One confirmation, one empty, loading and error state, one list of
conversations (IMDE 2.10).

Stopping a run, stopping a job on another machine and clearing the scratch
each asked in a way of their own (a line, another line, a dialog); they ask
with one component now, beside what was pressed, Not now focused, Escape for
no. What can be undone (a selection forgotten) asks nothing and offers Undo.
A page with nothing to show, still reading or not able to read says it with
one block: what, why, and the one thing to do; the Files page said "Reading
the study's files..." for ever when the server did not answer. And the
sidebar's Conversations and Chats are one list, the study's first.
"""

from __future__ import annotations

import json

import pytest

from tests.test_the_workspace_says_its_studies import _study

ASK = """async () => {
    const host = document.createElement('div');
    host.id = 'an-ask';
    document.body.appendChild(host);
    const answer = window.FastMDXConfirm.ask(host, {title: 'Stop the run?',
        detail: 'It ends at its next frame.', yes: 'Yes, stop it', list: ['seed 1', 'seed 2']});
    const shown = {
        text: host.querySelector('.fmx-ask').innerText.replace(/\\s+/g, ' ').trim(),
        focus: document.activeElement.textContent,
        role: host.querySelector('.fmx-ask').getAttribute('role'),
        list: [...host.querySelectorAll('.fmx-ask-list li')].map((li) => li.textContent),
    };
    document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    shown.escaped = await answer;
    const again = window.FastMDXConfirm.ask(host, {title: 'Clear it?', yes: 'Clear',
        detail: Promise.resolve({detail: '3 files, 1.2 MB.', ready: true})});
    shown.waiting = host.querySelector('.fmx-ask-yes').disabled;
    await new Promise((r) => setTimeout(r, 50));
    shown.ready = !host.querySelector('.fmx-ask-yes').disabled;
    shown.read = host.querySelector('.fmx-ask-detail').textContent;
    host.querySelector('.fmx-ask-yes').click();
    shown.yes = await again;
    window.FastMDXConfirm.done(host);
    shown.gone = host.hidden && !host.childNodes.length;
    return shown;
}"""


def test_one_ask_one_state_and_one_list(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "alpha")
    _study(tmp_path / "beta")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    post = """async ([url, body]) => (await fetch(url, {method: 'POST',
        headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})).json()"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The Files page's read refused: said, with Try again.
            refused = {"now": True}

            def files(route):
                if refused["now"]:
                    route.fulfill(status=500, body="no")
                else:
                    route.continue_()
            page.route("**/api/files-page*", files)
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_selector('#files-page .fmx-state[data-kind="error"]')
            files_said = page.inner_text("#files-page .fmx-state")
            refused["now"] = False
            page.click("#files-page .fmx-state-act")
            page.wait_for_function("() => !document.querySelector('#files-page .fmx-state')")
            asked = page.evaluate(ASK)
            # Nothing to compare yet.
            page.goto(session.url + "#compare", wait_until="domcontentloaded")
            page.wait_for_selector('#compare-empty .fmx-state[data-kind="empty"]')
            compare = page.inner_text("#compare-empty")
            # All studies: none matching, then not listed with Try again.
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            page.fill("#studies-search", "nothing like it")
            none = page.inner_text("#studies-empty")
            page.fill("#studies-search", "")
            page.route("**/api/studies*", lambda route: route.fulfill(
                json={"ok": False, "reason": "Permission denied"}))
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector('#studies-empty .fmx-state[data-kind="error"]')
            listed = page.inner_text("#studies-empty")
            # Read again, it is listed.
            page.unroute("**/api/studies*")
            page.click("#studies-empty .fmx-state-act")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            # One list of conversations: the study's, then the chats.
            run = page.evaluate("() => window.FastMDXDashboard.state.appState.active_run")
            for where, text in ((None, "A chat about nothing open"), (run, "About this study")):
                begun = page.evaluate(post, ["/api/agent/conversation/new", {"study": where}])
                page.evaluate(post, ["/api/agent/conversation", {
                    "id": begun["id"], "study": where, "entries": [{"role": "user", "text": text}]}])
            page.evaluate("() => window.FastMDXChats.show()")
            page.wait_for_selector("#sidebar-chats-list .sidebar-conv")
            talks = page.evaluate("""() => ({
                head: document.querySelector('#sidebar-chats > summary').innerText.trim(),
                add: document.getElementById('sidebar-chat-new').getAttribute('aria-label'),
                rows: [...document.querySelectorAll('#sidebar-chats-list > *')]
                    .filter((n) => !n.classList.contains('sidebar-conv-new'))
                    .map((n) => n.classList.contains('sidebar-conv-part') ? '# ' + n.textContent
                        : n.dataset.group + ': ' + n.querySelector('.sidebar-conv-title').textContent),
                two: !!document.getElementById('sidebar-study-convs'),
            })""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert "The study's files could not be read" in files_said and "Try again" in files_said
    assert asked == {
        "text": "Stop the run? It ends at its next frame. seed 1 seed 2 Yes, stop it Not now",
        "focus": "Not now", "role": "group", "list": ["seed 1", "seed 2"], "escaped": False,
        "waiting": True, "ready": True, "read": "3 files, 1.2 MB.", "yes": True, "gone": True}
    assert compare.startswith("Nothing to compare yet")
    assert none.startswith("No study matches that")
    assert listed.startswith("The studies could not be listed") and "Permission denied" in listed
    assert talks == {
        "head": "Conversations", "add": "A new conversation about this study",
        "rows": ["# This study", "study: About this study",
                 "# Not about a study", "chats: A chat about nothing open"],
        "two": False}


def test_not_now_and_escape_do_nothing_and_give_the_keyboard_back(tmp_path):
    """Asked from the keyboard, answered no by Escape or Not now: nothing is
    sent, the question goes, and the button pressed has the keyboard again."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_scratch_is_cleared_after_asking import _study as _with_scratch

    root = _with_scratch(tmp_path / "study")
    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    running = """() => window.dispatchEvent(new CustomEvent('dashboard:app-state',
        {detail: {process_running: true}}))"""
    after = """(button) => ({focus: document.activeElement === document.querySelector(button),
        asked: !!document.querySelector('.fmx-ask')})"""
    answers = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            sent: list[str] = []
            page.on("request", lambda r: sent.append(r.url + " " + (r.post_data or ""))
                    if r.method == "POST" and ("/stop" in r.url or "clear-scratch" in r.url) else None)
            page.goto(session.url + "#files", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            clear = ".files-usage [data-clear]"
            page.wait_for_selector(clear)
            for no in ("Escape", "Enter"):
                page.focus(clear)
                page.keyboard.press("Enter")
                page.wait_for_function("() => { const b = document.querySelector('.files-clear-go');"
                                       " return b && !b.disabled; }")
                if no == "Escape":
                    # The page read again while it is asked (the study's
                    # first state arriving, a file written): the question
                    # stays, and what changed is drawn once it is answered.
                    (root / "analysis").mkdir(exist_ok=True)
                    (root / "analysis" / "written_since.json").write_text("{}")
                    with page.expect_response(lambda r: "/api/files-page" in r.url):
                        page.evaluate("""() => window.dispatchEvent(new CustomEvent('dashboard:run-changed',
                            {detail: {previousRun: '', activeRun: 'x', first: true}}))""")
                    with page.expect_response(lambda r: "/api/files-page" in r.url):
                        page.evaluate("() => window.FastMDXFiles.refresh()")
                    kept = page.evaluate("() => !!document.querySelector('.files-clear-ask .files-clear-no')")
                page.keyboard.press(no)
                if no == "Escape":
                    page.wait_for_function("() => document.getElementById('files-page')"
                                           ".textContent.includes('written_since')")
                answers.append(page.evaluate(after, clear))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            for button, keys in (("#sb-stop", ["Escape"]), ("#sb-stop", ["Shift+Tab", "Escape"]),
                                 ("#sb-stop", ["Enter"])):
                page.evaluate(running)
                page.wait_for_selector(button, state="visible")
                page.focus(button)
                page.keyboard.press("Enter")
                page.wait_for_selector(".fmx-ask .stop-run-no")
                for key in keys:
                    page.keyboard.press(key)
                answers.append(page.evaluate(after, button))
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert kept
    assert answers == [{"focus": True, "asked": False}] * 5
    assert sent == [s for s in sent if '"dry":true' in s] and len(sent) == 2
    assert (root / "simulation" / "frames.dcd").exists()


def test_all_studies_says_it_reads_and_its_error_once(tmp_path):
    """All studies says it is reading while its list is first asked for,
    and an error once: the block says it, the subtitle where it looked."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "alpha")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            held = []
            page.route("**/api/studies*", lambda route: held.append(route))
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_function("() => window.FastMDXStudies && document.querySelector("
                                   "'#studies-empty .fmx-state[data-kind=\"loading\"]')")
            first = page.inner_text("#studies-empty")
            for route in held:
                route.fulfill(json={"ok": False, "reason": "No such folder: /gone"})
            page.unroute("**/api/studies*")
            page.wait_for_selector('#studies-empty .fmx-state[data-kind="error"]')
            listed = page.inner_text('section[data-page="studies"]')
            browser.close()
    finally:
        session.server.shutdown()
    assert first.startswith("Listing the studies")
    assert listed.count("could not be listed") == 1 and "No such folder: /gone" in listed


def test_a_chat_of_no_study_is_begun_with_a_study_open(tmp_path):
    """The head's + begins a conversation about the open study; a chat of
    no study is still begun from the sidebar while one is open."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "alpha")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_function("() => !!window.FastMDXDashboard.state.appState.active_run")
            page.evaluate("() => window.FastMDXChats.show()")
            page.wait_for_selector("#sidebar-chats-list .sidebar-conv-chat", state="visible")
            with page.expect_request(lambda r: r.url.endswith("/api/agent/conversation/new")) as asked:
                page.click("#sidebar-chats-list .sidebar-conv-chat")
            begun = json.loads(asked.value.post_data or "{}")
            browser.close()
    finally:
        session.server.shutdown()
    assert begun["study"] is None


def test_one_frame_for_a_question_a_whole_count_and_a_notice_beside_the_list(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.files_page import render

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "alpha")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1250, "height": 900})
            page.set_default_timeout(60000)
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_function("() => !!window.FastMDXNotices && !!window.FastMDXStop")
            page.evaluate("() => window.dispatchEvent(new CustomEvent('dashboard:app-state',"
                          " {detail: {process_running: true}}))")
            page.click("#sb-stop")
            frame = page.evaluate("""() => { const s = getComputedStyle(document.querySelector('#sb-stop-ask .fmx-ask'));
                return [s.borderTopWidth, s.paddingTop]; }""")
            page.keyboard.press("Escape")
            # The bell's count whole, however narrow the bar.
            page.set_viewport_size({"width": 900, "height": 900})
            for n in range(12):
                page.evaluate(f"() => window.FastMDXDashboard.toast('Notice {n}.', 'ok')")
            page.wait_for_function("() => document.querySelector('#sb-notices .sb-count').textContent === '12'")
            whole = page.evaluate("""() => { const c = document.querySelector('#sb-notices .sb-count');
                return c.scrollWidth <= c.clientWidth && getComputedStyle(c).textOverflow !== 'ellipsis'; }""")
            page.click("#sb-notices")
            page.evaluate("() => window.FastMDXDashboard.toast('Shown while the list is open.', 'ok')")
            page.wait_for_function("() => document.getElementById('dashboard-toast').classList.contains('show')")
            page.wait_for_timeout(300)
            apart = page.evaluate("""() => { const a = document.getElementById('dashboard-toast').getBoundingClientRect();
                const b = document.getElementById('notices').getBoundingClientRect();
                return a.right <= b.left || a.bottom <= b.top; }""")
            browser.close()
    finally:
        session.server.shutdown()
    assert frame == ["0px", "0px"]
    assert whole and apart
    empty = render({"files": []}, None)
    assert 'class="fmx-state" data-kind="empty"' in empty and "files-empty" not in empty


def test_try_again_reads_the_folder_asked_and_no_answer_is_said(tmp_path):
    """Try again on All studies reads the folder that could not be listed,
    not the last one listed, and the head names it whole on hover; the
    Problems tab says the server did not answer, as All studies does."""
    pytest.importorskip("playwright.sync_api")
    from urllib.parse import parse_qs, urlparse

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "alpha")
    locked = tmp_path / "locked"
    locked.mkdir()
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    asked: list[str] = []

    def studies(route):
        where = (parse_qs(urlparse(route.request.url).query).get("path") or [""])[0]
        asked.append(where)
        if where == str(locked):
            route.fulfill(json={"ok": False, "reason": "Permission denied"})
        else:
            route.continue_()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script("localStorage.setItem('fmx.panelCollapsed', '0');")
            page = context.new_page()
            page.set_default_timeout(60000)
            page.route("**/api/studies*", studies)
            page.goto(session.url + "#studies", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.evaluate(f"() => window.FastMDXStudies.load({str(tmp_path)!r})")
            page.wait_for_selector("#studies-table tbody tr.studies-row")
            page.evaluate(f"() => window.FastMDXStudies.load({str(locked)!r})")
            page.wait_for_selector('#studies-empty .fmx-state[data-kind="error"]')
            # The folder looked in, whole on hover where the head cuts it.
            looked = page.evaluate("() => { const head = document.getElementById('studies-where');"
                                   " return [head.textContent, head.title]; }")
            before = len(asked)
            with page.expect_response(lambda r: "/api/studies" in r.url):
                page.click("#studies-empty .fmx-state-act")
            again = asked[before:]
            # The Problems tab, its read not answered.
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            if page.locator("#side-expand").is_visible():
                page.locator("#side-expand").click()
            page.locator("#problems-tab").click()
            page.wait_for_function("() => !document.getElementById('problems-refresh').disabled"
                                   " && !document.querySelector('#problems-body .fmx-state[data-kind=\"loading\"]')")
            page.route("**/api/problems?*", lambda route: route.abort())
            page.click("#problems-refresh")
            page.wait_for_selector("#problems-body .fmx-state[data-kind='error']")
            unanswered = page.inner_text("#problems-body .fmx-state")
            page.unroute("**/api/problems?*")
            page.click("#problems-body .fmx-state-act")
            page.wait_for_function("() => !document.querySelector('#problems-body .fmx-state[data-kind=\"error\"]')")
            browser.close()
    finally:
        session.server.shutdown()
    assert again == [str(locked)]
    assert looked == [f"Looked in {locked}.", f"Looked in {locked}."]
    assert unanswered.startswith("The problems could not be read")
    assert "The server did not answer." in unanswered
