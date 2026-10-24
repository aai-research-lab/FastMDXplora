"""A notice that a study was opened elsewhere, waiting on an Undo, is said
after what the Undo says, and only the latest such notice is said (CI-774,
second round: reviews K1 and K2).

The tags' Undo says its result when the server answers: the notice shown at
the press was replaced a moment later and lost. Notices queued one after the
other were said late, the older ones naming a study no longer shown.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from tests.test_the_workspace_says_its_studies import _study

pytest.importorskip("playwright.sync_api")

from fastmdxplora.gui.workspace import study_key  # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    workspace = tmp_path_factory.mktemp("notices")
    first = _study(workspace / "wild_type", started="2026-09-02T10:00:00+00:00")
    _study(workspace / "mutant", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    _study(workspace / "hairpin", system="1UAO", started="2026-09-04T10:00:00+00:00")
    started = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    started.workspace, started.first = workspace, first
    yield started
    started.server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _elsewhere(session, folder):
    """Another window opens a study."""
    base = session.url.rstrip("/")
    request = urllib.request.Request(base + "/api/explore/switch", data=json.dumps(
        {"folder": str(folder)}).encode(),
        headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as answer:
        assert json.loads(answer.read())["ok"]


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


def _tags_kept(page, session):
    # The studies as cards: All studies opens on its list.
    page.add_init_script("try { localStorage.setItem('fmx.studiesView', 'cards'); } catch (e) {}")
    page.goto(session.url + "#studies", wait_until="domcontentloaded")
    page.wait_for_selector("body.state-ready")
    page.evaluate(f"() => window.FastMDXStudies.load({str(session.workspace)!r})")
    card = '.study-card[data-path$="wild_type"]'
    page.wait_for_selector(card)
    page.click(f"{card} .study-tag-edit")
    page.fill(f"{card} .study-tag-input", "slip")
    page.click(f"{card} .study-tag-editor button:has-text('Save')")
    page.wait_for_selector("#dashboard-toast.show:has-text('Saved the tags') .dashboard-toast-action")
    page.evaluate(_HEARD)
    # On the Undo, so it is still offered however long the page takes to see
    # the other window's study.
    page.focus("#dashboard-toast .dashboard-toast-action")


def _seen(page, folder):
    page.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key",
                           arg=study_key(folder), timeout=30000)


def test_the_notice_is_said_after_what_the_undo_of_tags_says(browser, session) -> None:
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        _tags_kept(page, session)
        _elsewhere(session, session.workspace / "mutant")
        _seen(page, session.workspace / "mutant")
        page.click("#dashboard-toast .dashboard-toast-action")
        page.wait_for_function("() => window.__said.some((s) => /opened elsewhere/.test(s))",
                               timeout=15000)
        page.wait_for_timeout(1000)
        said = page.evaluate("() => window.__said")
        shown = page.text_content("#dashboard-toast.show .dashboard-toast-said")
    finally:
        _elsewhere(session, session.first)
        page.context.close()
    assert [s for s in said if "back as they were" in s or "opened elsewhere" in s] == [
        "The tags and note are back as they were.", said[-1]], said
    assert "opened elsewhere" in said[-1] and shown == said[-1]
    assert errors == []


def test_only_the_latest_study_opened_elsewhere_is_said(browser, session) -> None:
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        _tags_kept(page, session)
        for folder in ("mutant", "hairpin"):
            _elsewhere(session, session.workspace / folder)
            _seen(page, session.workspace / folder)
        page.click("#dashboard-toast .dashboard-toast-action")
        page.wait_for_function("() => window.__said.some((s) => /opened elsewhere/.test(s))",
                               timeout=15000)
        # Long enough for a second notice to follow the first.
        page.wait_for_timeout(5000)
        said = page.evaluate("() => window.__said")
        title = page.text_content("#topbar-run-title")
    finally:
        _elsewhere(session, session.first)
        page.context.close()
    elsewhere = [s for s in said if "opened elsewhere" in s]
    assert elsewhere == [f"{title} was opened elsewhere, in another window or by an AI app: "
                         "this window now shows it."], said
    assert errors == []


def test_a_study_opened_elsewhere_is_said_by_its_own_name(browser, session) -> None:
    """Said as the page saw the switch, the notice named the study read
    before it (its name comes with the study's results, read after)."""
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        _seen(page, session.first)
        page.evaluate(_HEARD)
        _elsewhere(session, session.workspace / "hairpin")
        page.wait_for_function("() => window.__said.some((s) => /opened elsewhere/.test(s))",
                               timeout=15000)
        said = page.evaluate("() => window.__said")
        title = page.text_content("#topbar-run-title")
    finally:
        _elsewhere(session, session.first)
        page.context.close()
    assert [s for s in said if "opened elsewhere" in s] == [
        f"{title} was opened elsewhere, in another window or by an AI app: this window now shows it."]
    assert title != "wild_type"
    assert errors == []


#: As _HEARD, with when each was shown (ms).
_TIMED = """() => {
    window.__heard = [];
    const note = () => {
        const toast = document.getElementById('dashboard-toast');
        const said = toast && toast.querySelector('.dashboard-toast-said');
        const text = toast && toast.classList.contains('show') && said ? said.textContent : '';
        const last = window.__heard[window.__heard.length - 1];
        if (text && (!last || last.text !== text)) window.__heard.push({text, at: performance.now()});
    };
    new MutationObserver(note).observe(document.body, {subtree: true, childList: true,
        characterData: true, attributes: true});
}"""


def test_a_notice_waits_for_a_toast_still_within_its_time(browser, session) -> None:
    """Found by review K4 (third round): the Undo's refusal, a toast with
    no action, was replaced by the notice of a study opened elsewhere at
    once, and was up for about 2 s of its 3.5."""
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    refused = "The view was not put back: another study is open now."
    held = []
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        _seen(page, session.first)
        page.evaluate(_TIMED)
        # The page's next read of the study open is held, so it sees the
        # other window's study just after the refusal is shown.
        page.route("**/api/status*", lambda route: held.append(route))
        _elsewhere(session, session.workspace / "mutant")
        while not held:
            page.wait_for_timeout(100)
        page.evaluate("(text) => window.FastMDXDashboard.toast(text, 'warning')", refused)
        for route in list(held):
            route.continue_()
        page.unroute("**/api/status*")
        page.wait_for_function("() => window.__heard.some((s) => /opened elsewhere/.test(s.text))",
                               timeout=15000)
        heard = page.evaluate("() => window.__heard")
    finally:
        _elsewhere(session, session.first)
        page.context.close()
    assert [h["text"] for h in heard][0] == refused, heard
    assert "opened elsewhere" in heard[1]["text"], heard
    # The refusal had its whole time (3.5 s) before the notice.
    assert heard[1]["at"] - heard[0]["at"] >= 3400, heard
    assert errors == []
