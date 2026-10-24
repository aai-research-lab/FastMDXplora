"""A write to a study's views, scenes or selections names the study of the
page it came from, and the server refuses it when another study is open
(CI-774, second round: review K2).

Found by review K2: a view's Undo pressed before the window had seen another
window open another study put the view back into that other study:
`/api/views` wrote into whichever study was open, told nothing of which the
page was for.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests import viewer_hooks as hooks
from tests.test_a_notice_waits_for_what_undo_says import _HEARD
from tests.test_the_workspace_says_its_studies import _study

pytest.importorskip("playwright.sync_api")

from fastmdxplora.gui.saved_views import views_of  # noqa: E402
from fastmdxplora.gui.workspace import study_key  # noqa: E402

CAMERA = {"position": [1.0, 2.0, 30.0], "target": [1.0, 2.0, 3.0], "up": [0.0, 1.0, 0.0],
          "radius": 12.5, "fov": 0.785, "mode": "perspective"}


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    workspace = tmp_path_factory.mktemp("page-study")
    first = _study(workspace / "wild_type", started="2026-09-02T10:00:00+00:00")
    _study(workspace / "mutant", system="1UBQ", started="2026-09-03T10:00:00+00:00")
    started = start_dashboard_session(output=str(first), host="127.0.0.1", port=0)
    started.workspace, started.first = workspace, first
    yield started
    started.server.shutdown()


def _post(session, route, body):
    base = session.url.rstrip("/")
    request = urllib.request.Request(base + route, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Origin": base},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read())


def test_a_write_for_another_study_is_refused(session) -> None:
    mutant = session.workspace / "mutant"
    other = study_key(mutant)
    view = {"camera": CAMERA, "frame": 1}
    asked = {
        "/api/views": {"action": "save", "name": "elsewhere", "view": view},
        "/api/scenes": {"name": "elsewhere", "view": view},
        "/api/viewer-selections": {"action": "save", "name": "elsewhere",
                                   "selection": {"expression": "protein"}},
    }
    answers = {route: _post(session, route, {**body, "page_study": other})
               for route, body in asked.items()}
    for route, (status, said) in answers.items():
        assert status == 409, (route, said)
        assert said["ok"] is False and said["study_changed"] is True
        assert said["study_key"] == study_key(session.first)
        assert "another study" in said["reason"]
    # Nothing was written in the study open.
    assert views_of(session.first)["views"] == []
    assert not (session.first / "scenes").exists()
    # The page's own study, or none named (an AI app), is written as before.
    assert _post(session, "/api/views", {**asked["/api/views"], "page_study":
                                         study_key(session.first)})[1]["ok"]
    assert _post(session, "/api/views", {"action": "delete", "name": "elsewhere"})[1]["ok"]


def test_undo_before_the_window_sees_another_study_is_refused(session) -> None:
    from playwright.sync_api import sync_playwright

    mutant = session.workspace / "mutant"
    assert _post(session, "/api/views", {"action": "save", "name": "two",
                                         "view": {"camera": CAMERA, "frame": 1}})[1]["ok"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
            other_window = browser.new_context(viewport={"width": 1000, "height": 800}).new_page()
            page.set_default_timeout(60000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            page.wait_for_selector("#viewer-views option[value='two']", state="attached")
            hooks.tool(page, "side-saved")
            page.select_option("#viewer-views", "two")
            page.click("#viewer-view-forget")
            page.wait_for_selector("#dashboard-toast.show .dashboard-toast-action")
            page.focus("#dashboard-toast .dashboard-toast-action")
            page.evaluate(_HEARD)
            other_window.goto(session.url + "#overview", wait_until="domcontentloaded")
            # This window has not yet seen the other's switch: its reads of
            # the study open go unanswered meanwhile (a window took 2.9 to
            # 5 s to see one, review K2).
            page.route("**/api/status*", lambda route: route.abort())
            other_window.evaluate("""(folder) => fetch('/api/explore/switch', {method: 'POST',
                headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})})""",
                                  str(mutant))
            # Undo pressed before this window has seen the other's switch.
            shown = page.evaluate("""() => {
                const shown = window.FastMDXDashboard.state.studyShown;
                document.querySelector('#dashboard-toast .dashboard-toast-action').click();
                return shown;
            }""")
            page.unroute("**/api/status*")
            page.wait_for_timeout(2500)
            said = page.evaluate("() => window.__said")
        finally:
            browser.close()
            _post(session, "/api/explore/switch", {"folder": str(session.first)})
    assert shown == study_key(session.first)
    assert views_of(mutant)["views"] == [], "put back into the other study"
    assert views_of(session.first)["views"] == []
    assert "The view was not put back: another study is open now." in said, said
    assert errors == []


def test_a_selection_changed_before_the_window_sees_another_study_is_refused(session) -> None:
    """Found by review K3 (third round): the page's selection writes named
    no study, so a window that had not yet seen another one open a study
    wrote its selection into that study."""
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.viewer_selections import save_selection, selections_of

    mutant = session.workspace / "mutant"
    assert save_selection(session.first, "kept", {"kind": "expression", "expression": "protein"})["ok"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
            page.set_default_timeout(60000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            hooks.tool(page, "side-selections")
            page.wait_for_selector('.sel-item[data-name="kept"]')
            # Another study opened (as by another window), and the change
            # made at once, before this window can have seen the switch.
            with page.expect_response(lambda r: r.url.endswith("/api/viewer-selections")
                                      and r.request.method == "POST"):
                shown = page.evaluate("""async (folder) => {
                    const shown = window.FastMDXDashboard.state.studyShown;
                    await fetch('/api/explore/switch', {method: 'POST',
                        headers: {'content-type': 'application/json'}, body: JSON.stringify({folder})});
                    document.querySelector('.sel-item[data-name="kept"] .sel-eye').click();
                    return shown;
                }""", str(mutant))
            page.wait_for_timeout(300)
            said = page.text_content("#sel-said")
        finally:
            browser.close()
            _post(session, "/api/explore/switch", {"folder": str(session.first)})
    assert shown == study_key(session.first)
    assert selections_of(mutant)["selections"] == [], "written into the other study"
    assert said == "The change was not saved: another study is open now.", said
    assert errors == []
