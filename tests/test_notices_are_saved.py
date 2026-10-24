"""Notices saved, behind a bell in the status bar (IMDE 2.8).

A notice was a toast for three seconds and then gone; a run that ended
while the person was elsewhere said so only in the tab's title, and a
refusal only in the Log. Each is saved now on this computer, the last 200:
every toast as it is shown, whoever showed it; each run seen ending; each
refusal written while the page is open. The bell in the status bar says how
many are new; its list has All, Runs and Refusals and warnings, each
notice its time and its study, a run's end opening that study's Overview;
Clear the list asks nothing and offers Undo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

LOOK = """() => {
    const bell = document.getElementById('sb-notices');
    const box = document.getElementById('notices');
    const count = bell.querySelector('.sb-count');
    return {
        bell: !bell.hidden, count: count.hidden ? '' : count.textContent,
        label: bell.getAttribute('aria-label'),
        open: !!box && !box.hidden,
        rows: box ? [...box.querySelectorAll('.notice')].map((n) => [n.dataset.kind,
            n.querySelector('.notice-said').textContent,
            (n.querySelector('.notice-act') || {}).textContent || '']) : [],
        foot: ((box && box.querySelector('.notices-foot')) || {}).textContent || '',
        focus: document.activeElement.id || document.activeElement.className,
        saved: JSON.parse(localStorage.getItem('fmx.notices') || '[]').length,
    };
}"""


def test_toasts_runs_ended_and_refusals_are_saved_behind_the_bell(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_the_workspace_says_its_studies import _study

    study = _study(tmp_path / "chignolin")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#overview", wait_until="domcontentloaded")
            page.wait_for_selector("body.state-ready")
            page.wait_for_function("() => !!window.FastMDXNotices")
            none = page.evaluate(LOOK)
            # A toast, from whichever page shows it; the same one again at
            # once is one notice.
            page.evaluate("() => window.FastMDXDashboard.toast('Saved the view.', 'ok')")
            page.evaluate("() => window.FastMDXDashboard.toast('The study could not open.', 'warning')")
            page.evaluate("() => window.FastMDXDashboard.toast('The study could not open.', 'warning')")
            # A run seen ending (run-notice.js), and a refusal written since
            # the Log was first read (frame.js).
            page.evaluate("""(study) => window.dispatchEvent(new CustomEvent('fmx:run-ended', {
                detail: {name: 'chignolin seed 2', run: 'completed', stage: 'production done',
                         study: study}}))""", str(study))
            page.evaluate("""() => window.dispatchEvent(new CustomEvent('fmx:events', {
                detail: {events: [], refused: () => true}}))""")
            page.evaluate("""(at) => window.dispatchEvent(new CustomEvent('fmx:events', {
                detail: {events: [{timestamp: at, message: 'simulation.timestep_fs 5 is refused'}],
                         refused: () => true}}))""", later)
            page.wait_for_function("() => window.FastMDXNotices.all.length === 4")
            gathered = page.evaluate(LOOK)
            page.click("#sb-notices")
            opened = page.evaluate(LOOK)
            page.click('.notices-chip[data-show="runs"]')
            runs = page.evaluate(LOOK)
            page.click('.notices-chip[data-show="refused"]')
            refused = page.evaluate(LOOK)
            page.click('.notices-chip[data-show="all"]')
            # Saved: there after a reload, read.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => !!window.FastMDXNotices")
            reloaded = page.evaluate(LOOK)
            page.click("#sb-notices")
            page.click(".notices-clear")
            cleared = page.evaluate(LOOK)
            page.click(".notices-undo")
            undone = page.evaluate(LOOK)
            page.keyboard.press("Escape")
            closed = page.evaluate(LOOK)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    assert none["bell"] and none["count"] == "" and none["label"] == "Notices"
    assert gathered["count"] == "4" and gathered["label"] == "Notices, 4 new" and gathered["saved"] == 4
    assert opened["open"] and opened["count"] == ""
    assert opened["rows"] == [
        ["refused", "simulation.timestep_fs 5 is refused", ""],
        ["ended", "chignolin seed 2 completed: production done", "Open its Overview"],
        ["warning", "The study could not open.", ""],
        ["ok", "Saved the view.", ""],
    ]
    assert [r[0] for r in runs["rows"]] == ["ended"]
    assert [r[0] for r in refused["rows"]] == ["refused", "warning"]
    assert reloaded["saved"] == 4 and reloaded["count"] == ""
    assert cleared["rows"] == [] and cleared["foot"].startswith("Cleared 4.") and cleared["saved"] == 0
    assert len(undone["rows"]) == 4 and undone["saved"] == 4
    assert undone["foot"].startswith("Saved on this computer, the last 200")
    assert not closed["open"] and closed["focus"] == "sb-notices"


def _session(tmp_path, *, allow_control=True):
    """Two studies of one workspace, the first open; served on loopback as
    a page bound beyond it where ``allow_control`` is False."""
    import contextlib
    import threading
    from http.server import ThreadingHTTPServer

    from fastmdxplora.gui.exploration import DashboardRuntime
    from fastmdxplora.gui.server import make_handler
    from tests.test_the_workspace_says_its_studies import _study

    root = (tmp_path / "workspace").resolve()
    alpha, beta = _study(root / "alpha"), _study(root / "beta")

    @contextlib.contextmanager
    def serving():
        runtime = DashboardRuntime(workspace_root=root, exploration_root=root, active_root=alpha)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                    make_handler(alpha, runtime=runtime, allow_control=allow_control))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            httpd.server_close()
    return alpha, beta, serving()


def _status(study, said, step=5):
    """The run's live record, as a run writes it."""
    (study / "simulation").mkdir(exist_ok=True)
    (study / "simulation" / "live_status.json").write_text(json.dumps({
        "status": said, "stage": "production", "system_id": "TRIA", "current_step": step,
        "total_planned_steps": 10, "run_started_at": "2026-10-21T10:00:00+00:00",
        "last_update_timestamp": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")


RUNNING = "() => document.getElementById('status-run').getAttribute('data-run') === 'running'"
SAVED = "() => JSON.parse(localStorage.getItem('fmx.notices') || '[]')"


def _page(browser, url, where="#analysis", width=1440, context=None):
    page = (context or browser.new_context(viewport={"width": width, "height": 900})).new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_selector("body.state-ready")
    page.wait_for_function("() => !!window.FastMDXNotices")
    return page


def _run_ends(study, pages, said="completed"):
    """A run of ``study`` seen going in each page, then ended."""
    _status(study, "running")
    for page in pages:
        page.wait_for_function(RUNNING)
    _status(study, said, step=10 if said == "completed" else 5)
    for page in pages:
        page.wait_for_function(f"() => ({SAVED})().some((n) => n.act === 'overview')")


def test_a_run_s_end_opens_its_study_by_its_key(tmp_path):
    """A real run's end: saved with the server's key for its study and its
    folder's name; Open its Overview shows its own study's Overview, and
    another study's opened by its key as every switch is, held first."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.workspace import study_key

    alpha, beta, serving = _session(tmp_path)
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url)
        switches: list[str] = []
        page.on("request", lambda r: switches.append(r.post_data or "")
                if "/api/explore/switch" in r.url else None)
        _run_ends(alpha, [page])
        saved = page.evaluate(SAVED)
        page.click("#sb-notices")
        page.click(".notice-act")
        page.wait_for_function("() => location.hash.startsWith('#overview')")
        own = list(switches)
        # Another study open: the run's end opens alpha again, by its key.
        page.evaluate("(beta) => window.FastMDXPlace.open(beta, 'analysis')", str(beta))
        page.wait_for_function("(beta) => !!window.FastMDXDashboard && (window.FastMDXDashboard.state.appState || {}).active_run === beta",
                               arg=str(beta))
        page.wait_for_selector("body.state-ready")
        page.wait_for_function("() => !!window.FastMDXNotices")
        page.evaluate("""() => { window.FastMDXRun = window.FastMDXRun || {};
            window.FastMDXRun.beforeStudySwitch = () => 'The draft here is not saved.'; }""")
        before = len(switches)
        page.click("#sb-notices")
        page.click(".notice-act")
        page.wait_for_function("""() => (document.querySelector('#dashboard-toast .dashboard-toast-said')
            || {}).textContent === 'The draft here is not saved.'""")
        held = len(switches) - before
        page.evaluate("() => { window.FastMDXRun.beforeStudySwitch = () => ''; }")
        page.click("#sb-notices")
        page.click(".notice-act")
        page.wait_for_function("(alpha) => !!window.FastMDXDashboard && (window.FastMDXDashboard.state.appState || {}).active_run === alpha",
                               arg=str(alpha))
        page.wait_for_function("() => location.hash.startsWith('#overview')")
        errors = page.errors
        browser.close()
    assert errors == []
    ended = [n for n in saved if n["act"] == "overview"]
    assert len(ended) == 1 and ended[0]["kind"] == "ended"
    assert ended[0]["key"] == study_key(alpha) and ended[0]["name"] == "alpha"
    assert "folder" not in ended[0] and str(alpha) not in json.dumps(saved)
    assert own == [] and held == 0
    assert json.loads(switches[-1]) == {"study": study_key(alpha)}


def test_a_run_that_fails_with_no_record_is_saved_as_it_failed(tmp_path):
    """A run with no record of its own (a study of several runs, a process
    ended before it wrote one) ends as its process did: failed, under
    Refusals and warnings, as the browser's notice says it."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    process = {"state": "idle"}

    def app_state(route):
        answer = route.fetch().json()
        if process["state"] == "running":
            answer.update(process_running=True, status="running")
        elif process["state"] == "failed":
            answer.update(process_running=False, status="failed")
        route.fulfill(json=answer)

    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script("""window.__shown = [];
            window.Notification = function (title) { window.__shown.push(title); };
            window.Notification.permission = 'granted';""")
        context.route("**/api/app-state*", app_state)
        page = _page(browser, url, context=context)
        page.evaluate("() => { document.getElementById('setting-notify').checked = true; }")
        process["state"] = "running"
        page.wait_for_function(RUNNING)
        process["state"] = "failed"
        page.wait_for_function(f"() => ({SAVED})().some((n) => n.act === 'overview')")
        saved = [n for n in page.evaluate(SAVED) if n["act"] == "overview"]
        shown = page.evaluate("window.__shown")
        page.click("#sb-notices")
        page.click('.notices-chip[data-show="refused"]')
        refused = page.evaluate("[...document.querySelectorAll('.notice .notice-said')].map((n) => n.textContent)")
        browser.close()
    assert len(saved) == 1 and saved[0]["kind"] == "refused" and " failed" in saved[0]["said"]
    assert len(shown) == 1 and shown[0].endswith(" failed")
    assert saved[0]["said"] in refused


def test_a_notice_saves_only_what_it_said(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#overview")
        page.evaluate("() => window.FastMDXDashboard.toast('Saved the view.', 'ok')")
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [], refused: () => true}}))""")
        page.evaluate("""([at, where]) => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [{timestamp: at, message: 'analysis.rmsd.selection is refused: no atoms\\n'
                + 'Traceback (most recent call last):\\n  File "' + where + '/secret_notes.py", line 3'}],
                     refused: () => true}}))""", [later, str(alpha)])
        page.wait_for_function("() => window.FastMDXNotices.all.length === 2")
        saved = page.evaluate(SAVED)
        browser.close()
    said = {n["kind"]: n for n in saved}
    assert said["refused"]["said"] == "analysis.rmsd.selection is refused: no atoms"
    # No study's folder: a key only where it opens the study (a run's end).
    assert said["ok"]["key"] == "" and said["refused"]["key"] == ""
    assert "secret_notes" not in json.dumps(saved) and str(alpha) not in json.dumps(saved)


def test_a_toast_is_filed_under_the_study_of_its_page(tmp_path):
    """A notice on a page of no study (All studies, Compare) is about
    another study or none: never filed under the open one."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#overview")
        page.evaluate("() => window.FastMDXDashboard.toast('Saved the view.', 'ok')")
        page.evaluate("() => window.FastMDXDashboard.navigate('studies')")
        page.wait_for_function("() => document.documentElement.dataset.page === 'studies'")
        page.evaluate("() => window.FastMDXDashboard.toast('Saved the tags and note of beta.', 'ok')")
        page.wait_for_function("() => window.FastMDXNotices.all.length === 2")
        named = {n["said"]: n["name"] for n in page.evaluate(SAVED)}
        browser.close()
    assert named == {"Saved the view.": "alpha", "Saved the tags and note of beta.": ""}


READ_THE_LOG = """() => fetch('/api/app-state').then((r) => r.json()).then((d) =>
    window.dispatchEvent(new CustomEvent('dashboard:app-state', {detail: d})))"""


def test_two_windows_save_one_notice_once(tmp_path):
    """A refusal the Log reads and a run's end, seen by two windows of the
    GUI, are saved once."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        first, second = _page(browser, url, context=context), _page(browser, url, context=context)
        _status(alpha, "running")
        for page in (first, second):
            page.wait_for_function(RUNNING)
        events = alpha / "simulation" / "live_events.log"
        events.write_text("", encoding="utf-8")
        for page in (first, second):
            # Each window has read the open study's Log once, as it does with
            # each app state: what is written after is new.
            with page.expect_response(lambda r: "/api/events" in r.url):
                page.evaluate(READ_THE_LOG)
        at = datetime.now(timezone.utc).isoformat()
        events.write_text(f"{at}\terror\tsetup.forcefield.unknown is refused\n"
                          f"{at}\terror\tFastMDXplora exploration failed\n", encoding="utf-8")
        for page in (first, second):
            # Each window reads the Log again, as it does with each app state.
            with page.expect_response(lambda r: "/api/events" in r.url):
                page.evaluate(READ_THE_LOG)
            page.wait_for_timeout(300)
        _run_ends(alpha, [], said="completed")
        for page in (first, second):
            page.wait_for_function(f"() => ({SAVED})().some((n) => n.act === 'overview')")
        first.wait_for_timeout(3000)
        saved = first.evaluate(SAVED)
        browser.close()
    kinds = sorted(n["kind"] for n in saved)
    assert kinds == ["ended", "refused", "refused"], saved


def test_keys_pressed_in_the_list_stay_in_it(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    chosen = ("() => [...document.querySelectorAll('#studies-table tbody input[type=checkbox]')]"
              ".filter((b) => b.checked).length")
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#studies")
        page.evaluate(f"() => window.FastMDXStudies.load({str(alpha.parent)!r})")
        page.wait_for_selector("#studies-table tbody tr.studies-row")
        for box in page.query_selector_all("#studies-table tbody tr.studies-row input[type='checkbox']")[:2]:
            box.check()
        page.evaluate("() => window.FastMDXDashboard.toast('A notice.', 'ok')")
        page.click("#sb-notices")
        for key in ("c", "j", "k", "t", "x"):
            page.keyboard.press(key)
        typed = {"hash": page.evaluate("location.hash"), "chosen": page.evaluate(chosen),
                 "focus": page.evaluate("document.getElementById('notices').contains(document.activeElement)")}
        page.keyboard.press("Escape")
        closed = {"open": page.evaluate("!document.getElementById('notices').hidden"),
                  "chosen": page.evaluate(chosen), "focus": page.evaluate("document.activeElement.id")}
        browser.close()
    assert typed == {"hash": "#studies", "chosen": 2, "focus": True}
    assert closed == {"open": False, "chosen": 2, "focus": "sb-notices"}


def test_undo_after_clear_keeps_the_newest_first_and_the_count_right(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    count = "() => { const c = document.querySelector('#sb-notices .sb-count'); return c.hidden ? '' : c.textContent; }"
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#overview")
        page.evaluate("() => window.FastMDXDashboard.toast('Older one.', 'ok')")
        page.wait_for_timeout(50)
        page.evaluate("() => window.FastMDXDashboard.toast('Older two.', 'ok')")
        page.click("#sb-notices")
        page.click(".notices-clear")
        page.wait_for_timeout(50)
        page.evaluate("() => window.FastMDXDashboard.toast('Newest, after Clear.', 'warning')")
        page.click(".notices-undo")
        order = [n["said"] for n in page.evaluate("window.FastMDXNotices.all")]
        page.keyboard.press("Escape")
        page.click("#sb-notices")
        page.keyboard.press("Escape")
        seen = page.evaluate(count)
        browser.close()
    assert order == ["Newest, after Clear.", "Older two.", "Older one."]
    assert seen == ""


def test_a_page_bound_beyond_this_computer_offers_no_switch_or_stop(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.workspace import study_key

    alpha, beta, serving = _session(tmp_path, allow_control=False)
    other = [{"at": 1, "kind": "ended", "said": "beta completed.", "name": "beta", "act": "overview",
              "key": study_key(beta)}]
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        # Another study's run's end, saved by another window.
        context.add_init_script(f"localStorage.setItem('fmx.notices', {json.dumps(json.dumps(other))});")
        page = _page(browser, url, "#overview", context=context)
        # Its own study's run, going, then ended.
        _status(alpha, "running")
        page.wait_for_function(RUNNING)
        stops = page.evaluate("[...document.querySelectorAll('[data-stop-run]')].filter((b) => !b.hidden).length")
        running = page.evaluate("window.FastMDXStop.running()")
        _status(alpha, "completed", step=10)
        page.wait_for_function(f"() => ({SAVED})().length === 2")
        page.click("#sb-notices")
        acts = page.evaluate("""[...document.querySelectorAll('.notice')].map((n) =>
            (n.querySelector('.notice-act') || {}).textContent || '')""")
        browser.close()
    # Its own study's Overview is a page of this one; another study is a
    # switch, a POST refused here.
    assert acts == ["Open its Overview", ""]
    assert stops == 0 and running is False


def test_no_notice_is_saved_with_a_path_in_it(tmp_path):
    """A toast that names a path (the study menu's Open the folder, a
    server's refusal, a Log line, from the root, the home, a drive or a
    share) is saved without it; a list saved before is saved again
    without."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    folders = iter([{"ok": True, "opened": True, "path": "/Users/kunle aina/Studies/alpha"},
                    {"ok": True, "opened": False, "path": "C:\\Users\\Kunle Aina\\Studies\\alpha"}])
    said = [
        "Permission denied: '/home/kunle/ws/locked'",
        "[Errno 2] No such file or directory: 'C:\\Users\\Kunle\\x.yml'",
        "The folder ~/studies/alpha is not a study.",
        "Saved to /Users/kunle/ws/alpha/view.json.",
        "No such folder: \\\\lab-nas\\share\\kunle\\ws",
        "Could not read file:///Users/kunle/x.pdb",
        "TRIA ran at 120 ns/day and/or 1/2 kJ/mol, see https://example.org/a/b.",
    ]
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        # A list saved before a path was left out.
        context.add_init_script("""if (!sessionStorage.getItem('seeded')) {
            sessionStorage.setItem('seeded', '1');
            localStorage.setItem('fmx.notices', JSON.stringify([{at: Date.now() - 60000, kind: 'ok',
                said: 'Opened output folder: /Users/kunle/Studies/old', name: 'old', act: ''},
                {at: Date.now() - 50000, kind: 'refused', said: 'Could not read /Users/kunle/a.pdb',
                 name: 'old', act: '', id: 'event::2026-10-21T10:00:00Z:Could not read /Users/kunle/a.pdb'}]));
        }""")
        context.route("**/api/open-output*", lambda route: route.fulfill(json=next(folders)))
        page = _page(browser, url, "#overview", context=context)
        before = page.evaluate(SAVED)
        # Open the folder, opened; then not opened, its path not copied.
        page.evaluate("() => { window.FastMDXIcons.copy = () => Promise.resolve(false);"
                      " window.FastMDXIcons.copyText = () => Promise.resolve(false); }")
        for _ in range(2):
            page.click("#study-card")
            page.click("#open-output")
            page.wait_for_function("""() => /folder/.test((document.querySelector(
                '#dashboard-toast.show .dashboard-toast-said') || {}).textContent || '')""")
            page.evaluate("() => document.getElementById('dashboard-toast').classList.remove('show')")
        for words in said:
            page.evaluate("(w) => window.FastMDXDashboard.toast(w, 'warning')", words)
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [], refused: () => true}}))""")
        page.evaluate("""(at) => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [{timestamp: at, message: 'Could not read /Users/kunle/ws/alpha/in.pdb: no atoms'}],
                     refused: () => true}}))""", later)
        page.wait_for_function(f"() => window.FastMDXNotices.all.length === {len(said) + 5}")
        saved = page.evaluate(SAVED)
        errors = page.errors
        browser.close()
    assert errors == []
    assert [n["said"] for n in before] == ["Could not read", "Opened output folder"]
    assert [n["said"] for n in reversed(saved)] == [
        "Opened output folder",
        "Could not read",
        "Opened output folder",
        "Could not open the folder automatically.",
        "Permission denied",
        "[Errno 2] No such file or directory",
        "The folder is not a study.",
        "Saved.",
        "No such folder",
        "Could not read",
        "TRIA ran at 120 ns/day and/or 1/2 kJ/mol, see https://example.org/a/b.",
        "Could not read: no atoms",
    ]
    text = json.dumps(saved)
    for private in ("/Users", "/home", "C:", "~/", "lab-nas", "file:", "kunle", "Kunle", str(alpha)):
        assert private not in text, private


SWITCHED = """() => JSON.parse(localStorage.getItem('fmx.notices') || '[]')
    .filter((n) => / was opened elsewhere, /.test(n.said)).length"""


def test_a_study_opened_elsewhere_is_saved_once_however_late_a_window_hears(tmp_path):
    """Two windows hear of a study opened elsewhere, one 3 s late (as a
    window in the background does): saved once. Each switch is saved, the
    same two studies again included, and in one window after one of its
    own."""
    pytest.importorskip("playwright.sync_api")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.workspace import study_key

    alpha, beta, serving = _session(tmp_path)

    def switch(url, folder):
        asked = urllib.request.Request(url + "/api/explore/switch", method="POST",
                                       data=json.dumps({"folder": str(folder)}).encode(),
                                       headers={"content-type": "application/json"})
        assert json.loads(urllib.request.urlopen(asked, timeout=60).read())["ok"]

    def heard(page, folder, n):
        page.wait_for_function("""([key, n]) => window.FastMDXDashboard.state.studyShown === key
            && window.__heard === n && / was opened elsewhere, /.test((document.querySelector(
                '#dashboard-toast.show .dashboard-toast-said') || {}).textContent || '')""",
                               arg=[study_key(folder), n])

    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script("""window.__heard = 0;
            window.addEventListener('dashboard:study-changed-elsewhere', () => { window.__heard += 1; });""")
        first = _page(browser, url, "#overview", context=context)
        late = context.new_page()
        late.set_default_timeout(60000)
        late.add_init_script("""(() => { const fetched = window.fetch;
            window.fetch = (what, ...rest) => /\\/api\\/(status|app-state)/.test(String(what))
                ? new Promise((go) => setTimeout(go, 3000)).then(() => fetched(what, ...rest))
                : fetched(what, ...rest); })();""")
        late.goto(url + "#overview", wait_until="domcontentloaded")
        late.wait_for_selector("body.state-ready")
        late.wait_for_function("() => !!window.FastMDXNotices")
        counts = []
        for n, folder in enumerate((beta, alpha, beta), start=1):
            switch(url, folder)
            heard(first, folder, n)
            heard(late, folder, n)
            late.wait_for_timeout(300)
            counts.append(first.evaluate(SWITCHED))
        late.close()
        # One window: the study opened from it, then elsewhere again.
        first.evaluate("(alpha) => window.FastMDXPlace.open(alpha, 'overview')", str(alpha))
        first.wait_for_function("(key) => !!window.FastMDXDashboard && window.FastMDXDashboard.state.studyShown === key",
                                arg=study_key(alpha))
        first.wait_for_function("() => !!window.FastMDXNotices")
        first.wait_for_timeout(500)
        switch(url, beta)
        heard(first, beta, 1)
        counts.append(first.evaluate(SWITCHED))
        errors = first.errors
        browser.close()
    assert errors == []
    assert counts == [1, 2, 3, 4]


def test_tab_stays_in_the_open_list_until_escape_or_a_choice(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    inside = "() => !document.getElementById('notices').hidden && document.getElementById('notices').contains(document.activeElement)"
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#overview")
        page.evaluate("() => window.FastMDXDashboard.toast('A notice.', 'ok')")
        page.click("#sb-notices")
        stops = page.evaluate("document.querySelectorAll('#notices button:not([disabled])').length")
        went = []
        for _ in range(stops + 2):
            page.keyboard.press("Tab")
            went.append(page.evaluate(inside))
        for _ in range(stops + 2):
            page.keyboard.press("Shift+Tab")
            went.append(page.evaluate(inside))
        # A choice: All, Runs, Refusals and warnings keep it open, with the keyboard.
        page.keyboard.press("Enter")
        chose = page.evaluate(inside)
        page.keyboard.press("Escape")
        closed = page.evaluate("() => [document.getElementById('notices').hidden, document.activeElement.id]")
        browser.close()
    assert stops >= 4 and all(went), went
    assert chose and closed == [True, "sb-notices"]


def test_a_long_line_s_path_is_left_out_before_it_is_cut(tmp_path):
    """A Log line of 300 characters whose quoted path crosses the 200th, a
    toast whose quote is never closed, and a list saved before with such a
    line cut and its id spaced as the Log wrote it: no path is saved."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    where = ("/Users/kunle/Research/FastMDXplora studies/lysozyme T4 mutants/L99A and L99A-M102Q/"
             "simulation/production/checkpoints/segments/of the second campaign/"
             "replicas of the third week/production_segment_0003.chk")
    line = f"FileNotFoundError: [Errno 2] No such file or directory: '{where}'"
    assert len(line) >= 250 and line.index("/Users") < 200
    cut = line[:199] + "\u2026"
    seeded = json.dumps([{"at": 0, "kind": "refused", "said": cut, "name": "old", "act": "",
                          "id": "event::2026-10-21T10:00:00Z:" + cut.replace("[Errno 2] ", "[Errno 2]\t")}])
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(f"""if (!sessionStorage.getItem('seeded')) {{
            sessionStorage.setItem('seeded', '1');
            localStorage.setItem('fmx.notices', JSON.stringify({seeded}.map((n) => ({{...n, at: Date.now() - 60000}}))));
        }}""")
        page = _page(browser, url, "#overview", context=context)
        before = page.evaluate(SAVED)
        page.evaluate("() => window.FastMDXDashboard.toast('Could not open \"/Users/kunle/x.pdb', 'warning')")
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [], refused: () => true}}))""")
        page.evaluate("""([at, line]) => window.dispatchEvent(new CustomEvent('fmx:events', {
            detail: {events: [{timestamp: at, message: line + '\\nTraceback (most recent call last):'}],
                     refused: () => true}}))""", [later, line])
        page.wait_for_function("() => window.FastMDXNotices.all.length === 3")
        saved = page.evaluate(SAVED)
        errors = page.errors
        browser.close()
    assert errors == []
    assert [n["said"] for n in before] == ["FileNotFoundError: [Errno 2] No such file or directory"]
    assert [n["said"] for n in reversed(saved)] == [
        "FileNotFoundError: [Errno 2] No such file or directory",
        "Could not open",
        "FileNotFoundError: [Errno 2] No such file or directory",
    ]
    text = json.dumps(saved)
    for private in ("/Users", "kunle", "lysozyme", "production_segment", "chk"):
        assert private not in text, private


def test_a_window_that_hears_two_switches_as_one_saves_no_second_notice(tmp_path):
    """Two studies opened elsewhere a second apart: a window reading the
    server 3 s late hears one switch, from the first study to the last,
    and saves nothing the other window saved; each study opened is saved
    once."""
    pytest.importorskip("playwright.sync_api")
    import urllib.request

    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.workspace import study_key
    from tests.test_the_workspace_says_its_studies import _study

    alpha, beta, serving = _session(tmp_path)
    gamma = _study(alpha.parent / "gamma")

    def switch(url, folder):
        asked = urllib.request.Request(url + "/api/explore/switch", method="POST",
                                       data=json.dumps({"folder": str(folder)}).encode(),
                                       headers={"content-type": "application/json"})
        assert json.loads(urllib.request.urlopen(asked, timeout=60).read())["ok"]

    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script("""window.__heard = [];
            window.addEventListener('dashboard:study-changed-elsewhere',
                (e) => { window.__heard.push(e.detail.from + '>' + e.detail.to); });""")
        first = _page(browser, url, "#overview", context=context)
        late = context.new_page()
        late.set_default_timeout(60000)
        late.errors = []
        late.on("pageerror", lambda error: late.errors.append(str(error)))
        late.add_init_script("""(() => { const fetched = window.fetch;
            window.fetch = (what, ...rest) => /\\/api\\/(status|app-state)/.test(String(what))
                ? new Promise((go) => setTimeout(go, 3000)).then(() => fetched(what, ...rest))
                : fetched(what, ...rest); })();""")
        late.goto(url + "#overview", wait_until="domcontentloaded")
        late.wait_for_selector("body.state-ready")
        late.wait_for_function("() => !!window.FastMDXNotices")
        keys = [study_key(alpha), study_key(beta), study_key(gamma)]
        switch(url, beta)
        first.wait_for_function("(key) => window.FastMDXDashboard.state.studyShown === key", arg=keys[1])
        switch(url, gamma)
        shown = """(key) => window.FastMDXDashboard.state.studyShown === key && / was opened elsewhere, /.test(
            (document.querySelector('#dashboard-toast.show .dashboard-toast-said') || {}).textContent || '')"""
        first.wait_for_function(shown, arg=keys[2])
        late.wait_for_function(shown, arg=keys[2])
        late.wait_for_timeout(500)
        heard = {"first": first.evaluate("window.__heard"), "late": late.evaluate("window.__heard")}
        saved = [n["id"] for n in first.evaluate(SAVED) if " was opened elsewhere, " in n["said"]]
        errors = first.errors + late.errors
        browser.close()
    assert errors == []
    a, b, c = keys
    assert heard == {"first": [f"{a}>{b}", f"{b}>{c}"], "late": [f"{a}>{c}"]}
    assert sorted(saved) == sorted([f"switch:{a}>{b}", f"switch:{b}>{c}"])


PATHS_AND_WORDS = [
    ("The folder /Users/Kunle Aina could not be read", "The folder could not be read"),
    ("Could not open C:\\Users\\Jane Doe", "Could not open"),
    ("The path is read from where fastmdx was run (/home/kunle/Jane Doe), not from the config.",
     "The path is read from where fastmdx was run, not from the config."),
    ("Opened //server/share/kunle", "Opened"),
    ("Saved to smb://server/share/kunle.", "Saved."),
    ("Copied to kunle@lab.edu:/home/kunle/ws", "Copied"),
    ("Copy failed on lab:/home/kunle/ws", "Copy failed on lab"),
    ("Confirm /static/molstar/molstar.js is being served", "Confirm /static/molstar/molstar.js is being served"),
    ("Remote: /scratch/kunle/run1 is full", "Remote: is full"),
    ("Could not read: /Users/kunle/a.pdb is not a PDB", "Could not read: is not a PDB"),
    ("Could not open the folder automatically. Its path: /Users/kunle/ws. Copy it.",
     "Could not open the folder automatically. Copy it."),
    ("Files ['/Users/kunle/x.pdb', '/Users/kunle/y.pdb'] are missing", "Files are missing"),
    ("Setup failed (see /Users/kunle/log.txt)", "Setup failed"),
    ("Run `fastmdx resume /Users/kunle/ws/run` to carry it on.", "Run `fastmdx resume` to carry it on."),
    ("[Errno 13] Permission denied: '/Users/kunle/Bob's study/run'", "[Errno 13] Permission denied"),
    ("Written to /Users/aaina/Google Drive.", "Written."),
    ("Inputs [/Users/kunle/x.pdb] are missing", "Inputs are missing"),
    # Not paths: kept as said.
    ("Speed 12.3 ns/day, 1/2 of it done, and/or more", "Speed 12.3 ns/day, 1/2 of it done, and/or more"),
    ("See https://example.org/a/b and http://127.0.0.1:8000/api/status",
     "See https://example.org/a/b and http://127.0.0.1:8000/api/status"),
]


def test_a_path_is_left_out_whole_and_the_words_about_it_stay(tmp_path):
    """A path with a space in its last part, on a share or another machine,
    is left out whole as far as the words tell; what is said of it stays,
    and the page's own addresses are not paths."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    # A refusal saved before, its words with spaces collapsed and its id
    # with the Log line as it was.
    said = "The folder /Users/Kunle Aina could not be read"
    seeded = json.dumps([{"at": 0, "kind": "refused", "said": said, "name": "old", "act": "",
                          "id": "event::2026-10-21T10:00:00Z:" + said.replace("Kunle ", "Kunle\t")}])
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(f"""if (!sessionStorage.getItem('seeded')) {{
            sessionStorage.setItem('seeded', '1');
            localStorage.setItem('fmx.notices', JSON.stringify({seeded}.map((n) => ({{...n, at: Date.now() - 60000}}))));
        }}""")
        page = _page(browser, url, "#overview", context=context)
        before = page.evaluate(SAVED)
        for words, _ in PATHS_AND_WORDS:
            page.evaluate("(w) => window.FastMDXDashboard.toast(w, 'warning')", words)
        page.wait_for_function(f"() => window.FastMDXNotices.all.length === {len(PATHS_AND_WORDS) + 1}")
        saved = [n["said"] for n in reversed(page.evaluate(SAVED))][1:]
        errors = page.errors
        browser.close()
    assert errors == []
    assert [(n["said"], n["id"]) for n in before] == [
        ("The folder could not be read", "event::2026-10-21T10:00:00Z:The folder could not be read")]
    assert saved == [said for _, said in PATHS_AND_WORDS]


UNDO_TOAST = """(said) => window.FastMDXDashboard.toast(said, 'ok',
    {label: 'Undo', run: () => { window.undone = (window.undone || 0) + 1; }})"""
ON = "() => document.activeElement.textContent"


def test_a_notice_s_undo_beside_the_open_list_is_reached_by_tab(tmp_path):
    """With the list open, Tab goes round its buttons and the Undo of a
    notice shown beside it; Undo done gives the keyboard back to the list,
    a notice saved meanwhile leaves it where it was, and Escape on the Undo
    closes the list."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    alpha, beta, serving = _session(tmp_path)
    with serving as url, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = _page(browser, url, "#overview")
        page.evaluate(UNDO_TOAST, "Saved the tags and note of alpha.")
        page.click("#sb-notices")
        went = []
        for key in ["Tab"] * 6 + ["Shift+Tab"] * 2:
            page.keyboard.press(key)
            went.append(page.evaluate(ON))
        page.keyboard.press("Enter")
        undone = page.evaluate("() => [window.undone, document.getElementById('notices').hidden]")
        after_undo = page.evaluate(ON)
        # Another notice with Undo while the keyboard is in the list.
        page.keyboard.press("Tab")
        page.evaluate(UNDO_TOAST, "Saved the tags and note of beta.")
        page.wait_for_function("() => window.FastMDXNotices.all.length === 2")
        kept = page.evaluate(ON)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Shift+Tab")
        on_undo = page.evaluate(ON)
        page.keyboard.press("Escape")
        closed = page.evaluate("() => [document.getElementById('notices').hidden, document.activeElement.id]")
        errors = page.errors
        browser.close()
    assert errors == []
    assert went == ["Runs", "Refusals and warnings", "Clear the list", "Undo", "All", "Runs", "All", "Undo"]
    assert undone == [1, False] and after_undo == "All"
    assert kept == "Runs"
    assert on_undo == "Undo"
    assert closed == [True, "sb-notices"]
