"""Open study documents keep their places without closing a simulation."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect


def _study(path, name):
    (path / "simulation").mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"system": name, "phases": [
        {"name": "simulation", "status": "completed"}]}))
    (path / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production", "system_id": name,
        "current_step": 10, "total_steps": 10}))
    return path


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    from fastmdxplora.gui.server import start_dashboard_session
    from fastmdxplora.gui.workspace import study_key

    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "config"))
    root = tmp_path / "workspace"
    studies = [_study(root / "first", "Alpha"), _study(root / "second", "Beta"),
               _study(root / "third", "Gamma")]
    session = start_dashboard_session(output=str(studies[0]), exploration_root=root,
                                      host="127.0.0.1", port=0)
    yield {"session": session, "studies": studies, "keys": [study_key(p) for p in studies], "root": root}
    session.stop()


def _open(browser, site, width=1440, theme="dark", context=None):
    context = context or browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(f"localStorage.setItem('fmx.theme', '{theme}');")
    page = context.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    page.goto(site["session"].url + "#overview", wait_until="domcontentloaded")
    page.locator('.study-tab-link[aria-current="page"]').wait_for()
    return page


def _link(page, key):
    return page.locator(f'.study-tab-link[data-study-key="{key}"]')


def _seen(page):
    return page.locator(".study-tab-link").evaluate_all("es => es.map(e => e.dataset.studyKey)")


def _active(page, key):
    # A switch loads the page again: on a busy machine that takes more than
    # the 5 s an expectation waits by default.
    expect(_link(page, key)).to_have_attribute("aria-current", "page", timeout=20000)
    page.wait_for_function("key => new URL(location.href).searchParams.get('study') === key", arg=key,
                           timeout=20000)


def _outside_open(page, site, index, route="overview"):
    page.evaluate("""async ({path, route}) => {
        const answer = await (await fetch('/api/explore/switch', {
            method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({folder: path})})).json();
        window.FastMDXDashboard.studyOpened(answer, route);
    }""", {"path": str(site["studies"][index]), "route": route})
    _active(page, site["keys"][index])


def test_tabs_restore_each_studys_last_page_and_survive_reload(browser, site):
    page = _open(browser, site)
    try:
        page.evaluate("window.FastMDXDashboard.navigate('analysis/rmsd')")
        page.wait_for_function("location.hash === '#analysis/rmsd'")
        _outside_open(page, site, 1)
        assert _seen(page) == site["keys"][:2]
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        expect(_link(page, site["keys"][0])).to_be_focused()
        assert page.url.endswith("#analysis/rmsd")
        page.reload(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        assert _seen(page) == site["keys"][:2]
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        assert page.url.endswith("#overview")
        assert page.errors == []
    finally:
        page.context.close()


def test_tab_name_updates_when_the_results_supply_the_study_name(browser, site):
    (site["studies"][0] / "simulation" / "live_status.json").unlink()
    context = browser.new_context()
    def results(route):
        payload = route.fetch().json()
        payload["system"] = {"system": "2XYZ"}
        route.fulfill(json=payload)
    context.route("**/api/results", results)
    page = _open(browser, site, context=context)
    try:
        expect(page.locator("#topbar-run-title")).to_have_text("2XYZ")
        expect(_link(page, site["keys"][0])).to_have_text("2XYZ")
        assert page.errors == []
    finally:
        page.context.close()


def test_a_switch_saves_the_page_before_its_navigation_frame_runs(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        page.evaluate("""key => {
            window.FastMDXDashboard.navigate('analysis/rmsd');
            document.querySelector('.study-tab-link[data-study-key="' + key + '"]').click();
        }""", site["keys"][0])
        _active(page, site["keys"][0])
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        assert page.url.endswith("#analysis/rmsd")
        assert page.errors == []
    finally:
        page.context.close()


def test_close_inactive_changes_no_study_and_close_active_selects_neighbour(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _outside_open(page, site, 2)
        requests = []
        page.on("request", lambda request: requests.append(request.url) if '/api/explore/switch' in request.url else None)
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][0]}"]').click()
        assert _seen(page) == site["keys"][1:]
        assert requests == []
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][2]}"]').click()
        _active(page, site["keys"][1])
        assert _seen(page) == [site["keys"][1]]
        assert all((p / "manifest.json").is_file() for p in site["studies"])
        assert page.errors == []
    finally:
        page.context.close()


def test_last_closed_tab_stays_closed_until_explicitly_opened_again(browser, site):
    page = _open(browser, site)
    try:
        page.locator(".study-tab-close").click()
        page.wait_for_function("location.hash === '#studies'")
        assert _seen(page) == []
        page.evaluate("""() => window.FastMDXDashboard.applyStatus({
            status: {system_id:'Alpha',status:'completed'}, study: new URL(location).searchParams.get('study')})""")
        assert _seen(page) == []
        page.reload(wait_until="domcontentloaded")
        page.locator("#study-tabs:not([hidden])").wait_for()
        assert _seen(page) == []
        _outside_open(page, site, 0)
        assert _seen(page) == [site["keys"][0]]
        assert page.errors == []
    finally:
        page.context.close()


def test_a_refused_switch_or_close_keeps_the_current_document(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        before = page.url
        page.route("**/api/explore/switch", lambda route: route.fulfill(
            status=403, content_type="application/json", body=json.dumps({"ok": False, "error": "Switch refused for this check."})))
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_have_text("Switch refused for this check.")
        assert page.url == before
        _active(page, site["keys"][1])
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][1]}"]').click()
        expect(page.locator("#study-tabs")).to_have_attribute("aria-busy", "false")
        assert _seen(page) == site["keys"][:2]
        assert page.url == before
        assert page.errors == []
    finally:
        page.context.close()


def test_pending_switch_starts_one_request_and_does_not_relabel_old_records(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        held = []
        page.route("**/api/explore/switch", lambda route: held.append(route))
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs")).to_have_attribute("aria-busy", "true")
        _link(page, site["keys"][1]).dispatch_event("click", {"button": 0})
        assert len(held) == 1
        _active(page, site["keys"][1])
        held[0].fulfill(status=500, content_type="application/json", body=json.dumps({"ok": False, "error": "Try later."}))
        expect(page.locator("#study-tabs-note")).to_have_text("Try later.")
        assert page.errors == []
    finally:
        page.context.close()


def test_browser_history_returns_to_the_study_and_its_page(browser, site):
    page = _open(browser, site)
    try:
        page.evaluate("window.FastMDXDashboard.navigate('analysis/rmsd')")
        _outside_open(page, site, 1, "files")
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.url.endswith("#analysis/rmsd")
        page.go_back()
        _active(page, site["keys"][1])
        assert page.url.endswith("#files")
        page.go_forward()
        _active(page, site["keys"][0])
        assert page.url.endswith("#analysis/rmsd")
        assert _seen(page) == site["keys"][:2]
        assert page.errors == []
    finally:
        page.context.close()


def test_untrusted_stored_entries_are_text_and_only_admitted_keys_are_requested(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    key = "fmx.studyTabs:" + str(site["root"])
    evil = '<img src=x onerror="window.badTab=true">'
    entries = {"tabs": [{"key": site["keys"][1], "title": evil, "page": "https://attacker.invalid/"},
                         {"key": site["keys"][1], "title": "duplicate", "page": "overview"},
                         {"key": "../../private", "title": "Invalid", "page": "overview"}], "closed": []}
    context.add_init_script("sessionStorage.setItem(" + json.dumps(key) + ", " + json.dumps(json.dumps(entries)) + ");")
    page = _open(browser, site, context=context)
    try:
        assert _seen(page) == [site["keys"][1], site["keys"][0]]
        assert _link(page, site["keys"][1]).text_content() == evil
        assert page.locator("#study-tabs img").count() == 0
        assert page.evaluate("!window.badTab")
        assert _link(page, site["keys"][1]).get_attribute("href").endswith("#overview")
        assert page.errors == []
    finally:
        page.context.close()


def test_an_unsent_agent_message_holds_the_switch_without_discarding_it(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        page.fill("#agent-request", "Keep this unsent question.")
        requests = []
        page.on("request", lambda request: requests.append(request.url) if '/api/explore/switch' in request.url else None)
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("unsent Agent message")
        assert requests == []
        assert page.input_value("#agent-request") == "Keep this unsent question."
        _active(page, site["keys"][1])
        page.fill("#agent-request", "")
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_an_unsent_attachment_holds_the_switch_until_removed(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        path = str(site["studies"][1] / "manifest.json")
        page.locator("#agent-attach-path").evaluate("""(input, path) => {
            input.value = path; input.dispatchEvent(new Event('change'));
        }""", path)
        page.locator("#agent-attachments .agent-chip").wait_for()
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("attachments")
        _active(page, site["keys"][1])
        expect(page.locator("#agent-attachments .agent-chip")).to_be_visible()
        page.get_by_role("button", name="Remove manifest.json", exact=True).click()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


@pytest.mark.parametrize("streaming", [True, False])
def test_an_agent_reply_holds_the_switch_until_it_finishes(browser, site, streaming):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        if not streaming:
            page.evaluate("window.ReadableStream = undefined")
        held = []
        page.route("**/api/agent/propose*", lambda route: held.append(route))
        page.fill("#agent-request", "Describe this study.")
        page.keyboard.press("Enter")
        page.locator("#agent-propose.is-writing").wait_for()
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("Agent reply")
        _active(page, site["keys"][1])
        assert len(held) == 1
        held[0].fulfill(json={"ok": False, "answer": "The reply has finished.", "cites": []})
        page.get_by_text("The reply has finished.", exact=True).wait_for()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_a_folder_without_study_records_never_becomes_a_study_tab(browser, site):
    from fastmdxplora.gui.server import start_dashboard_session

    empty = site["root"] / "empty"
    empty.mkdir()
    session = start_dashboard_session(output=str(empty), exploration_root=site["root"],
                                      host="127.0.0.1", port=0)
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(session.url + "#overview", wait_until="domcontentloaded")
        page.wait_for_function("""() => {
            const state = window.FastMDXDashboard?.state;
            return state?.studyIsOne === false && state.results?.output_dir;
        }""")
        expect(page.locator("#study-tabs")).to_be_visible()
        assert _seen(page) == []
        page.evaluate("window.FastMDXDashboard.navigate('files')")
        page.wait_for_function("location.hash === '#files'")
        # Navigation is announced on the next frame.
        page.evaluate("() => new Promise(requestAnimationFrame)")
        assert _seen(page) == []
        assert page.locator('.study-tab-link[aria-current="page"]').count() == 0
    finally:
        context.close()
        session.stop()


def test_an_attachment_still_loading_holds_the_switch(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        held = []
        page.route("**/api/agent/attachment", lambda route: held.append(route))
        with page.expect_request("**/api/agent/attachment"):
            page.locator("#agent-attach-path").evaluate("""input => {
                input.value = '/pending/notes.txt'; input.dispatchEvent(new Event('change'));
            }""")
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("attachment to finish loading")
        _active(page, site["keys"][1])
        held[0].fulfill(json={"ok": True, "path": "/pending/notes.txt", "name": "notes.txt",
                              "size": 4, "text": "kept"})
        expect(page.locator("#agent-attachments .agent-chip")).to_contain_text("notes.txt")
        assert page.errors == []
    finally:
        page.context.close()


def _builder(page, site):
    pdb = site["root"] / "peptide.pdb"
    pdb.write_text("ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\n"
                   "ATOM      2  CA  GLY A   1       1.450   0.000   0.000  1.00  0.00           C\n"
                   "ATOM      3  C   GLY A   1       2.900   0.000   0.000  1.00  0.00           C\nEND\n")
    page.evaluate("window.FastMDXDashboard.navigate('run')")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(pdb))
    page.locator('#run-settings [data-setting="ph"] input').fill("6.5")


def test_a_builder_launch_waiting_for_its_answer_holds_the_switch(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        held = []
        page.route("**/api/run", lambda route: held.append(route))
        with page.expect_request("**/api/run"):
            page.click("#run-start-button")
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("Config launch to finish")
        _active(page, site["keys"][1])
        held[0].fulfill(json={"ok": False, "error": "Launch refused for this check."})
        expect(page.locator("#run-note")).to_have_text("Launch refused for this check.")
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_switch_flushes_and_restores_the_workspace_builder_draft(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        page.evaluate("window.FastMDXDashboard.navigate('run')")
        expect(page.locator('#run-settings [data-setting="ph"] input')).to_have_value("6.5")
        assert page.errors == []
    finally:
        page.context.close()


def test_a_failed_draft_save_keeps_the_current_study_and_typed_value(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        page.evaluate("""() => {
            const original = Storage.prototype.setItem;
            Storage.prototype.setItem = function(k,v) {
                if (k.startsWith('fmx.builderDraft')) throw new DOMException('full','QuotaExceededError');
                return original.call(this,k,v);
            };
        }""")
        page.locator('#run-settings [data-setting="ph"] input').fill("6.6")
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("draft could not be saved")
        _active(page, site["keys"][1])
        assert page.input_value('#run-settings [data-setting="ph"] input') == "6.6"
        assert page.errors == []
    finally:
        page.context.close()


def _no_save_picker(page):
    # Saved as a download, which the test can read.
    page.context.add_init_script("delete window.showSaveFilePicker; window.showSaveFilePicker = undefined;")


def _no_draft_storage(page):
    page.evaluate("""() => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function(k,v) {
            if (k.startsWith('fmx.builderDraft')) throw new DOMException('full','QuotaExceededError');
            return original.call(this,k,v);
        };
    }""")


@pytest.mark.parametrize("release", ["download", "reset"])
def test_a_draft_this_browser_cannot_store_is_released_by_download_or_reset(browser, site, release):
    page = _open(browser, site)
    _no_save_picker(page)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        _no_draft_storage(page)
        _ph(page).fill("6.6")
        _link(page, site["keys"][0]).click()
        note = page.locator("#study-tabs-note")
        expect(note).to_have_text("The Config draft could not be saved in this browser. "
                                  "Download it (Download config) or Reset the form before switching studies.")
        expect(page.locator("#run-draft")).to_have_text("Not saved in this browser")
        _active(page, site["keys"][1])
        if release == "download":
            with page.expect_download() as got:
                page.click("#run-download")
            assert "6.6" in open(got.value.path()).read()
            expect(page.locator("#run-draft")).to_be_hidden()
        else:
            page.click("#run-reset")
            expect(_ph(page)).not_to_have_value("6.6")
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_a_newer_draft_hold_is_said_in_the_header_and_released_by_download(browser, site):
    page = _open(browser, site)
    _no_save_picker(page)
    other = page.context.new_page()
    other.set_default_timeout(20000)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        page.evaluate("document.activeElement.blur()")
        page.wait_for_function("() => Object.keys(localStorage).some(k => k.startsWith('fmx.builderDraft') && localStorage.getItem(k).includes('6.5'))")
        other.goto(site["session"].url + "#overview", wait_until="domcontentloaded")
        # Typed here, and before it is saved another window saves its own.
        _ph(page).fill("5.9")
        other.evaluate("""() => {
            const key = Object.keys(localStorage).find(k => k.startsWith('fmx.builderDraft'));
            localStorage.setItem(key, localStorage.getItem(key).replace('6.5', '8.1'));
        }""")
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("another window saved a newer Config draft")
        # The header says the same, not "Saved", whole and clear of Find a
        # setting, with the Agent beside at 1440.
        expect(page.locator("#run-draft")).to_have_text("Not saved: another window saved a newer draft")
        page.click("#agent-beside-open")
        page.wait_for_function("document.body.classList.contains('agent-beside') || !!document.querySelector('.agent-beside, [data-agent-beside]')")
        fit = page.evaluate("""() => {
            const said = document.getElementById('run-draft'), find = document.getElementById('run-find');
            const a = said.getBoundingClientRect(), b = find.getBoundingClientRect();
            return {cut: said.scrollWidth > said.clientWidth + 1,
                    overlaps: a.right > b.left + 1 && a.left < b.right && a.bottom > b.top && a.top < b.bottom};
        }""")
        assert fit == {"cut": False, "overlaps": False}, fit
        _active(page, site["keys"][1])
        with page.expect_download() as got:
            page.click("#run-download")
        assert "5.9" in open(got.value.path()).read()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        # The other window's newer draft is the one kept.
        assert "8.1" in _stored_draft(page)
        assert page.errors == []
    finally:
        page.context.close()


def test_a_tab_is_named_as_recent_names_it(browser, site):
    (site["studies"][1] / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "stage": "production", "system_id": "Alpha",
        "current_step": 10, "total_steps": 10}))
    (site["studies"][1] / "manifest.json").write_text(json.dumps({"system": "Alpha", "phases": [
        {"name": "simulation", "status": "completed"}]}))
    page = _open(browser, site)
    try:
        # Only one of the two studies of its system is open: named by its
        # folder too, as Recent names it.
        recent = page.locator(f'.sidebar-recent-item[data-path="{site["studies"][0]}"] .sidebar-recent-name')
        expect(recent).to_have_attribute("data-name", "ALPH \u00b7 first")
        expect(_link(page, site["keys"][0])).to_have_text("ALPH \u00b7 first")
        assert page.errors == []
    finally:
        page.context.close()


SCROLLED = """() => {
    const shown = s => [...document.querySelectorAll(s)].find(e => e.getClientRects().length);
    const box = e => e ? e.getBoundingClientRect() : null;
    const strip = box(document.getElementById('study-tabs'));
    const note = document.getElementById('study-tabs-note'), said = box(note);
    const header = box(shown('.page:not([hidden]) .page-header'));
    const steps = box(shown('.page:not([hidden]) .builder-steps'));
    const bar = box(document.querySelector('.sidebar'));
    const at = (r, dx) => document.elementFromPoint(r.left + dx, r.top + r.height / 2);
    return {
        scrolled: Math.max(document.querySelector('.main').scrollTop, document.scrollingElement.scrollTop, document.body.scrollTop),
        strip: [strip.top, strip.bottom], header: header && [header.top, header.bottom],
        steps: steps && [steps.top, steps.bottom], bar: [bar.top, bar.bottom, bar.width],
        noteSeen: [4, 40, said.width / 2].every(dx => note.contains(at(said, dx))),
        tabSeen: document.querySelector('.study-tab.is-current').contains(
            at(box(document.querySelector('.study-tab.is-current')), 12)),
    };
}"""


@pytest.mark.parametrize("width", [1440, 390])
def test_scrolled_down_the_strip_and_its_note_stay_in_view_above_the_page_bars(browser, site, width):
    page = _open(browser, site, width=width)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        _no_draft_storage(page)
        _ph(page).fill("6.6")
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("could not be saved")
        page.evaluate("document.activeElement.blur()")
        page.mouse.move(width // 2, 600)
        page.mouse.wheel(0, 1500)
        page.wait_for_function("Math.max(document.querySelector('.main').scrollTop, document.scrollingElement.scrollTop, document.body.scrollTop) > 400")
        # Measured once the strip's size is observed and the page drawn again.
        page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
        facts = page.evaluate(SCROLLED)
        assert facts["noteSeen"] and facts["tabSeen"], facts
        if facts["bar"][2] >= width - 1:
            # A phone: the strip stays under the bar across the top.
            assert abs(facts["strip"][0] - facts["bar"][1]) <= 1, facts
        else:
            # The page's header and the Builder's steps stay below the strip.
            assert facts["header"][0] >= facts["strip"][1] - 1, facts
            assert facts["steps"][0] >= facts["header"][1] - 1, facts
        assert page.errors == []
    finally:
        page.context.close()


def test_a_mouse_wheel_moves_a_long_strip(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    key = "fmx.studyTabs:" + str(site["root"])
    entries = {"tabs": [{"key": f"{n:016x}", "title": f"Study {n}", "page": "overview"} for n in range(10, 34)]
               + [{"key": site["keys"][0], "title": "Alpha", "page": "overview"}]}
    context.add_init_script("sessionStorage.setItem(" + json.dumps(key) + ", " + json.dumps(json.dumps(entries)) + ");")
    page = _open(browser, site, context=context)
    try:
        page.wait_for_function("document.getElementById('study-tabs').matches('.more-before')")
        before = page.evaluate("document.getElementById('study-tabs-list').scrollLeft")
        box = page.locator("#study-tabs-list").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, -400)
        page.wait_for_function("left => document.getElementById('study-tabs-list').scrollLeft < left - 100", arg=before)
        assert page.errors == []
    finally:
        page.context.close()


IN_VIEW = """() => {
    const list = document.getElementById('study-tabs-list');
    const current = list.querySelector('.study-tab.is-current').getBoundingClientRect();
    const room = list.getBoundingClientRect();
    return current.left >= room.left - 1 && current.right <= room.right + 1;
}"""


def test_a_tab_grown_before_the_open_one_keeps_it_in_view_unless_moved_away(browser, site):
    """A tab before the open one that grows (a name drawn once the page's
    font has come, a study's id read late) changes no size of the strip's
    own, so the open tab was pushed out of view past the strip's right edge.
    It is followed until the strip is moved away from it."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    key = "fmx.studyTabs:" + str(site["root"])
    entries = {"tabs": [{"key": f"{n:016x}", "title": f"Study {n}", "page": "overview"} for n in range(10, 34)]
               + [{"key": site["keys"][0], "title": "Alpha", "page": "overview"}]}
    context.add_init_script("sessionStorage.setItem(" + json.dumps(key) + ", " + json.dumps(json.dumps(entries)) + ");")
    page = _open(browser, site, context=context)
    frames = "new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    grow = "px => { document.querySelector('#study-tabs-list .study-tab').style.minWidth = px + 'px'; }"
    try:
        page.wait_for_function(IN_VIEW)
        page.evaluate(grow, 700)
        page.evaluate(frames)
        followed = page.evaluate(IN_VIEW)
        # Moved away by a wheel, the strip stays where it was put.
        box = page.locator("#study-tabs-list").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, -4000)
        page.wait_for_function("() => document.getElementById('study-tabs-list').scrollLeft < 50")
        page.evaluate(frames)
        left = page.evaluate("document.getElementById('study-tabs-list').scrollLeft")
        page.evaluate(grow, 900)
        page.evaluate(frames)
        stayed = page.evaluate("document.getElementById('study-tabs-list').scrollLeft")
        assert page.errors == []
    finally:
        page.context.close()
    assert followed
    assert abs(stayed - left) <= 1, (left, stayed)


def test_a_strip_moved_by_the_wheel_stays_put_through_a_redraw_before_it_scrolls(browser, site):
    """The strip followed the open tab until the scroll event a frame after
    the wheel, so a redraw in between (a study's id read, a switch begun
    elsewhere) scrolled the open tab back into view."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    key = "fmx.studyTabs:" + str(site["root"])
    entries = {"tabs": [{"key": f"{n:016x}", "title": f"Study {n}", "page": "overview"} for n in range(10, 34)]
               + [{"key": site["keys"][0], "title": "Alpha", "page": "overview"}]}
    context.add_init_script("sessionStorage.setItem(" + json.dumps(key) + ", " + json.dumps(json.dumps(entries)) + ");")
    page = _open(browser, site, context=context)
    frames = "new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    try:
        page.wait_for_function(IN_VIEW)
        page.evaluate(frames)
        moved = page.evaluate("""() => {
            const list = document.getElementById('study-tabs-list');
            const before = list.scrollLeft;
            list.dispatchEvent(new WheelEvent('wheel', {deltaY: -4000, bubbles: true, cancelable: true}));
            const wheeled = list.scrollLeft;
            // Drawn again in the same task: a switch begun and ended elsewhere.
            const dashboard = window.FastMDXDashboard;
            const held = dashboard.beginStudySwitch();
            if (!held) dashboard.endStudySwitch();
            return {before, wheeled, held};
        }""")
        page.evaluate(frames)
        left = page.evaluate("document.getElementById('study-tabs-list').scrollLeft")
        assert page.errors == []
    finally:
        page.context.close()
    assert moved["held"] == ""
    assert moved["before"] > 50 and moved["wheeled"] < 50, moved
    assert left < 50, (moved, left)


LAYOUT = """() => {
    const box = el => el && el.getClientRects().length ? el.getBoundingClientRect() : null;
    const visible = el => el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const host = document.getElementById('study-tabs'), list = document.getElementById('study-tabs-list');
    const strip = box(host), room = box(list), current = box(list.querySelector('.study-tab.is-current'));
    const open = box(document.getElementById('study-tabs-open'));
    const panel = [...document.querySelectorAll('.side-panel, .agent-drawer')].filter(visible).map(box)
        .filter(r => r.width && r.left > innerWidth / 2);
    const title = box(document.querySelector('.page.active .page-title, .page[data-page="overview"] .page-title'));
    return {
        wider: document.documentElement.scrollWidth - innerWidth,
        strip: [strip.left, strip.right, strip.top, strip.bottom],
        currentInView: Boolean(current) && current.left >= room.left - 1 && current.right <= room.right + 1,
        openAfterList: room.right <= open.left + 1 && open.right <= strip.right + 1,
        scrolls: list.scrollWidth > list.clientWidth,
        moreShown: host.classList.contains('more-before') || host.classList.contains('more-after'),
        panelLeft: panel.length ? Math.min(...panel.map(r => r.left)) : innerWidth,
        titleTop: title ? title.top : null,
        stops: [...host.querySelectorAll('a, button')].filter(e => e.tabIndex >= 0).map(e => e.className),
    };
}"""


@pytest.mark.parametrize("width,beside", [(1440, False), (1250, True), (900, False), (390, False)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_many_tabs_fit_the_page_and_work_by_keyboard(browser, site, width, beside, theme):
    context = browser.new_context(viewport={"width": width, "height": 900})
    key = "fmx.studyTabs:" + str(site["root"])
    entries = {"tabs": [{"key": site["keys"][1], "title": "Beta" * 60, "page": "overview"}]
               + [{"key": f"{n:016x}", "title": f"Study {n}", "page": "overview"} for n in range(10, 28)]
               + [{"key": site["keys"][0], "title": "Alpha", "page": "overview"}]}
    context.add_init_script("sessionStorage.setItem(" + json.dumps(key) + ", " + json.dumps(json.dumps(entries)) + ");")
    page = _open(browser, site, width=width, theme=theme, context=context)
    try:
        if beside:
            page.click("#agent-beside-open")
            page.locator("#agent-drawer").wait_for()
        page.wait_for_function("document.getElementById('study-tabs').matches('.more-before, .more-after')")
        # The strip narrows as the Agent opens beside it; its observer puts
        # the open tab back in view on the next frame. Asserted below.
        try:
            page.wait_for_function(IN_VIEW, timeout=10000)
        except Exception:
            pass
        facts = page.evaluate(LAYOUT)
        # The open study is in view at the end of a long strip; a fade says
        # more tabs lie past the edge; nothing runs past the page or under
        # the side panel or the Agent, and the page's title starts below.
        assert facts["wider"] <= 0, facts
        assert facts["strip"][0] >= 0 and facts["strip"][1] <= min(width, facts["panelLeft"]) + 1, facts
        assert facts["currentInView"] and facts["scrolls"] and facts["moreShown"], facts
        assert facts["openAfterList"], facts
        assert facts["titleTop"] is None or facts["titleTop"] >= facts["strip"][3] - 1, facts
        # One keyboard stop for the strip's tabs, then its button.
        assert facts["stops"] == ["study-tab-link", "line-btn study-tabs-open"], facts
        expect(_link(page, site["keys"][0])).to_have_attribute("tabindex", "0")
        _link(page, site["keys"][0]).focus()
        page.keyboard.press("Home")
        expect(_link(page, site["keys"][1])).to_be_focused()
        page.keyboard.press("End")
        expect(_link(page, site["keys"][0])).to_be_focused()
        page.keyboard.press("Home")
        # The current tab and the focused one look different.
        looks = page.evaluate("""keys => keys.map(k => {
            const item = document.querySelector('.study-tab-link[data-study-key="' + k + '"]').parentElement;
            const css = getComputedStyle(item);
            return css.backgroundColor + css.boxShadow;
        })""", [site["keys"][0], site["keys"][1]])
        assert looks[0] != looks[1]
        page.keyboard.press("Delete")
        assert site["keys"][1] not in _seen(page)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.errors == []
    finally:
        page.context.close()


def test_a_delayed_success_does_not_show_new_records_under_the_old_tab(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        held = []
        def switch(route):
            response = route.fetch()
            held.append((route, response))
        page.route("**/api/explore/switch", switch)
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs")).to_have_attribute("aria-busy", "true")
        # The strip is busy before the switch is asked: the Builder's draft
        # is saved first, so the request comes after a reply of its own.
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert held
        assert page.evaluate("getComputedStyle(document.querySelector('.page-shell')).visibility") == "hidden"
        assert page.evaluate("window.FastMDXDashboard.state.studyShown") == site["keys"][1]
        # The actual backend has changed while the HTTP reply is held.
        server_key = page.evaluate("async () => (await (await fetch('/api/status')).json()).study")
        assert server_key == site["keys"][0]
        _active(page, site["keys"][1])
        held[0][0].fulfill(response=held[0][1])
        _active(page, site["keys"][0])
        expect(page.locator(".page-shell")).to_have_css("visibility", "visible")
        assert page.errors == []
    finally:
        page.context.close()


def _agent_versions(page):
    import yaml

    from fastmdxplora.gui.plan import changes_between

    base = {"systems": [{"system": "1UBQ"}], "include_phase": ["simulation"],
            "simulation": {"temperature_K": 300}}
    after = {**base, "simulation": {"temperature_K": 330}}
    entries = [{"role": "agent", "kind": "config", "config": c,
                "yaml": yaml.safe_dump(c), "plan": [], "attempts": [], "cycles": 1,
                "version": i + 1} for i, c in enumerate([base, after])]
    entries[1]["changes"] = changes_between(base, after)
    page.evaluate("""async entries => {
        await fetch('/api/agent/conversation', {method:'POST',
            headers:{'content-type':'application/json'}, body:JSON.stringify({entries})});
    }""", entries)
    page.reload(wait_until="domcontentloaded")
    return page.locator('.agent-study:not(.is-replaced):not([hidden])')


def test_a_rejected_change_not_applied_holds_the_tab_switch(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        current = _agent_versions(page)
        row = current.locator('[data-review-setting="simulation.temperature_K"]')
        row.locator('[data-choice="reject"]').click()
        requests = []
        page.on("request", lambda request: requests.append(request.url) if '/api/explore/switch' in request.url else None)
        _link(page, site["keys"][0]).click()
        # The reason names the review's own buttons, here and beside it.
        said = "Use reviewed version, or Use this version to keep version 2 as proposed, before switching studies."
        expect(page.locator("#study-tabs-note")).to_have_text(said)
        expect(current.locator(".agent-review-note")).to_contain_text("before switching studies")
        assert requests == []
        _active(page, site["keys"][1])
        assert row.locator('[data-choice="reject"]').get_attribute("aria-pressed") == "true"
        current.get_by_role("button", name="Use this version", exact=True).click()
        page.locator('.agent-study:not(.is-replaced):not([hidden]) [data-role="version"]').filter(has_text="3").wait_for()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_an_agent_launch_waiting_for_its_answer_holds_the_switch(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        current = _agent_versions(page)
        held = []
        page.route("**/api/agent/run", lambda route: held.append(route))
        with page.expect_request("**/api/agent/run"):
            current.locator('[data-role="run"]').click()
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("study launch to finish")
        _active(page, site["keys"][1])
        held[0].fulfill(json={"ok": False, "error": "Launch refused for this check."})
        page.get_by_text("Launch refused for this check.", exact=True).first.wait_for()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def _switches(page):
    asked = []
    page.on("request", lambda request: asked.append(request.url)
            if request.method == "POST" and "/api/explore/switch" in request.url else None)
    return asked


def test_a_gone_folder_of_another_tab_is_said_on_it_and_asked_again_when_chosen(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        before = page.url
        away = site["root"].parent / "away"
        shutil.move(str(site["studies"][0]), str(away))
        _link(page, site["keys"][0]).click()
        note = page.locator("#study-tabs-note")
        # The note names the study whose folder is gone, not the open one.
        expect(note).to_have_text("The folder of ALPH is no longer there.")
        # No path of the server's is said.
        assert str(site["root"]) not in note.text_content()
        expect(_link(page, site["keys"][0])).to_contain_text("folder gone")
        assert page.url == before
        _active(page, site["keys"][1])
        # Its tooltip says so once the pointer has left it, as an open gone
        # tab's does.
        page.mouse.move(700, 600)
        expect(_link(page, site["keys"][0])).to_have_attribute(
            "title", "ALPH\nThis study's folder is no longer there.")
        # Said on its tab after a reload too; choosing it again asks once.
        page.reload(wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        expect(_link(page, site["keys"][0])).to_contain_text("folder gone")
        asked = _switches(page)
        _link(page, site["keys"][0]).click()
        expect(note).to_have_text("The folder of ALPH is no longer there.")
        assert len(asked) == 1 and page.url == before
        # Its folder back: chosen, it opens.
        shutil.move(str(away), str(site["studies"][0]))
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        expect(_link(page, site["keys"][0])).not_to_contain_text("folder gone")
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][1]}"]').click()
        assert _seen(page) == [site["keys"][0]]
        assert page.errors == []
    finally:
        page.context.close()


def test_closing_the_open_tab_goes_past_a_neighbour_whose_folder_has_gone(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _outside_open(page, site, 2)
        # Gone unnoticed: its tab is not marked yet.
        shutil.rmtree(site["studies"][1])
        expect(_link(page, site["keys"][1])).not_to_contain_text("folder gone")
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][2]}"]').click()
        # The next neighbour opens; the gone one is marked on its tab.
        _active(page, site["keys"][0])
        assert _seen(page) == site["keys"][:2]
        expect(_link(page, site["keys"][1])).to_contain_text("folder gone")
        # Its neighbours gone, one known and one not: All studies, and the
        # note names the study found gone, not the one closed.
        _outside_open(page, site, 2)
        shutil.rmtree(site["studies"][0])
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][2]}"]').click()
        page.wait_for_function("location.hash === '#studies'")
        expect(page.locator("#study-tabs-note")).to_have_text("The folder of ALPH is no longer there.")
        assert _seen(page) == site["keys"][:2]
        expect(_link(page, site["keys"][0])).to_contain_text("folder gone")
        assert page.errors == []
    finally:
        page.context.close()


def test_the_open_studys_gone_folder_is_said_and_not_shown_as_an_empty_study(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        shutil.rmtree(site["studies"][1])
        gone = page.locator("#study-gone")
        expect(gone).to_be_visible(timeout=20000)
        expect(gone).to_contain_text("This study's folder is no longer there")
        expect(page.locator(".page-shell")).to_be_hidden()
        expect(_link(page, site["keys"][1])).to_contain_text("folder gone")
        expect(_link(page, site["keys"][1])).to_contain_text(re.compile("beta", re.I))
        # The address still names it, after a reload as well.
        page.reload(wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        expect(gone).to_be_visible()
        expect(page.locator(".page-shell")).to_be_hidden()
        assert str(site["root"]) not in page.locator("#study-tabs").inner_text()
        # All studies is still a page of its own; Close opens the neighbour.
        page.evaluate("window.FastMDXDashboard.navigate('studies')")
        expect(gone).to_be_hidden()
        page.evaluate("window.FastMDXDashboard.navigate('overview')")
        page.locator("#study-gone-close").click()
        _active(page, site["keys"][0])
        expect(gone).to_be_hidden()
        assert _seen(page) == [site["keys"][0]]
        assert page.errors == []
    finally:
        page.context.close()


def test_closing_the_tab_of_a_running_study_leaves_its_run_going(browser, site):
    first = site["studies"][0]
    (first / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "running", "stage": "production", "system_id": "Alpha",
        "current_step": 10, "total_steps": 5000}))
    runtime = site["session"].runtime
    runtime.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                       start_new_session=True)
    runtime.running_root = first
    page = _open(browser, site)
    try:
        posted = []
        page.on("request", lambda request: posted.append(request.url) if request.method == "POST" else None)
        _outside_open(page, site, 1)
        _outside_open(page, site, 0)
        posted.clear()
        # The open one, a running study: its neighbour opens; the run goes on.
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][0]}"]').click()
        _active(page, site["keys"][1])
        # Only the switch to the neighbour: nothing stops or changes the run.
        assert [u for u in posted if "/api/explore/switch" in u] == posted[:1], posted
        assert not any("stop" in url or "/api/run" in url for url in posted), posted
        assert runtime.process.poll() is None
        state = page.evaluate("async () => (await (await fetch('/api/app-state')).json())")
        assert state["running_elsewhere"] == str(first.resolve())
        # Not the open one: nothing is asked of the server at all.
        _outside_open(page, site, 0)
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        posted.clear()
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][0]}"]').click()
        assert _seen(page) == [site["keys"][1]]
        assert not any("switch" in url or "stop" in url for url in posted), posted
        assert runtime.process.poll() is None
        assert json.loads((first / "simulation" / "live_status.json").read_text())["status"] == "running"
        assert page.errors == []
    finally:
        page.context.close()
        runtime.process.kill()
        runtime.process.wait()


def _typed(page, site):
    _outside_open(page, site, 1, "agent")
    page.fill("#agent-request", "Unsent words.")
    return _switches(page)


def _a_conversation_of_the_first_study(page):
    page.evaluate("""async () => {
        await fetch('/api/agent/conversation', {method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({entries: [{role: 'user', text: 'Asked in the first study.'},
                                            {role: 'agent', kind: 'answer', text: 'Answered there.'}]})});
    }""")


@pytest.mark.parametrize("way", ["recent", "all studies", "load study", "agent conversation",
                                 "quick open", "place"])
def test_every_way_to_another_study_is_held_by_unsent_agent_words(browser, site, way):
    page = _open(browser, site)
    try:
        if way == "agent conversation":
            _a_conversation_of_the_first_study(page)
        asked = _typed(page, site)
        posted = []
        page.on("request", lambda request: posted.append(request.url)
                if request.method == "POST" and "/conversation/open" in request.url else None)
        before = page.url
        if way == "recent":
            page.locator('.sidebar-recent-item:not([aria-current])').first.click()
        elif way == "all studies":
            page.evaluate("window.FastMDXDashboard.navigate('studies')")
            page.locator('.page[data-page="studies"] .study-open').first.click()
            before = page.url
        elif way == "load study":
            page.locator("#load-study-path").evaluate("""(input, path) => {
                input.value = path; input.dispatchEvent(new Event('change'));
            }""", str(site["studies"][0]))
        elif way == "quick open":
            # A study of the workspace, found by typing (palette.js).
            page.evaluate("window.FastMDXPalette.open()")
            page.fill("#palette-input", "first")
            page.locator("#palette-list .palette-item", has_text="first").first.click()
        elif way == "place":
            # The way the place's run menu, the status bar's run elsewhere
            # and the Overview's runs open another study (place.js).
            page.evaluate("path => window.FastMDXPlace.open(path)", str(site["studies"][0]))
        else:
            # Another study's conversation, chosen in the Agent's list.
            page.click("#agent-conversations")
            row = page.locator('.agent-conv-row .title[title*="opens this study"]').first
            row.evaluate("button => { const fold = button.closest('details'); if (fold) fold.open = true; }")
            row.click()
            expect(page.locator(".agent-conv-held")).to_contain_text("unsent Agent message")
        expect(page.locator("#study-tabs-note")).to_contain_text("unsent Agent message")
        assert asked == [] and posted == []
        assert page.input_value("#agent-request") == "Unsent words."
        _active(page, site["keys"][1])
        assert page.url == before
        assert page.errors == []
    finally:
        page.context.close()


def test_back_then_return_by_the_strip_keeps_unsent_agent_words_and_attachments(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        path = str(site["studies"][1] / "manifest.json")
        page.locator("#agent-attach-path").evaluate("""(input, path) => {
            input.value = path; input.dispatchEvent(new Event('change'));
        }""", path)
        page.locator("#agent-attachments .agent-chip").wait_for()
        # Back is not held: the words are kept for the study they were typed in.
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        assert page.input_value("#agent-request") == ""
        # Returned to by its tab, not the browser's page cache.
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        expect(page.locator("#agent-request")).to_have_value("Unsent words.")
        expect(page.locator("#agent-attachments .agent-chip")).to_contain_text("manifest.json")
        # Cleared, nothing is kept for it.
        page.fill("#agent-request", "")
        page.get_by_role("button", name="Remove manifest.json", exact=True).click()
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.wait_for_function("window.FastMDXDashboard.state.addressKnown")
        assert page.input_value("#agent-request") == ""
        assert page.locator("#agent-attachments .agent-chip").count() == 0
        assert page.errors == []
    finally:
        page.context.close()


def test_a_back_within_the_page_goes_and_keeps_unsent_agent_words(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        # A step of this page naming another study, gone back to.
        page.evaluate("""key => {
            const here = location.href, there = new URL(here);
            there.searchParams.set('study', key);
            history.pushState(null, '', there.href);
            history.pushState(null, '', here);
            history.back();
        }""", site["keys"][0])
        _active(page, site["keys"][0])
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        expect(page.locator("#agent-request")).to_have_value("Unsent words.")
        assert page.errors == []
    finally:
        page.context.close()


def test_a_held_back_keeps_the_step_it_was_asked_from(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1, "agent")
        current = _agent_versions(page)
        current.locator('[data-review-setting="simulation.temperature_K"] [data-choice="reject"]').click()
        asked = _switches(page)
        page.evaluate("""key => {
            const here = location.href, there = new URL(here);
            there.searchParams.set('study', key);
            history.pushState(null, '', there.href);
            history.pushState(null, '', here);
            history.back();
        }""", site["keys"][0])
        expect(page.locator("#study-tabs-note")).to_contain_text("Use reviewed version")
        assert asked == []
        _active(page, site["keys"][1])
        # Done with the review, Back reaches the step that was asked.
        current.get_by_role("button", name="Use this version", exact=True).click()
        page.locator('.agent-study:not(.is-replaced):not([hidden]) [data-role="version"]').filter(has_text="3").wait_for()
        page.go_back()
        _active(page, site["keys"][0])
        assert page.errors == []
    finally:
        page.context.close()


def test_a_switch_asked_another_way_marks_the_strip_busy(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        held = []
        page.route("**/api/explore/switch", lambda route: held.append(route))
        page.locator('.sidebar-recent-item:not([aria-current])').first.click()
        expect(page.locator("#study-tabs")).to_have_attribute("aria-busy", "true")
        _link(page, site["keys"][0]).dispatch_event("click", {"button": 0})
        # Nothing is to happen: a short wait, then no second request.
        page.wait_for_timeout(300)
        assert len(held) == 1
        held[0].fulfill(status=500, content_type="application/json",
                        body=json.dumps({"ok": False, "error": "Try later."}))
        expect(page.locator("#study-tabs")).to_have_attribute("aria-busy", "false")
        _active(page, site["keys"][1])
        assert page.errors == []
    finally:
        page.context.close()


def _ph(page):
    return page.locator('#run-settings [data-setting="ph"] input')


def _stored_draft(page):
    return page.evaluate("""() => Object.keys(localStorage).filter(k => k.startsWith('fmx.builderDraft'))
        .map(k => localStorage.getItem(k)).join('')""")


def test_a_newer_draft_from_another_window_is_kept_and_does_not_hold_the_switch(browser, site):
    page = _open(browser, site)
    other = page.context.new_page()
    other.set_default_timeout(20000)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        # Typed and left: nothing in this window is being typed now.
        page.evaluate("document.activeElement.blur()")
        page.wait_for_function("() => Object.keys(localStorage).some(k => k.startsWith('fmx.builderDraft') && localStorage.getItem(k).includes('6.5'))")
        other.goto(site["session"].url + "#run", wait_until="domcontentloaded")
        expect(_ph(other)).to_have_value("6.5")
        _ph(other).fill("7.2")
        other.keyboard.press("Tab")
        other.wait_for_function("() => Object.keys(localStorage).some(k => k.startsWith('fmx.builderDraft') && localStorage.getItem(k).includes('7.2'))")
        # This window changed nothing since: it takes the newer draft and
        # switches, and the newer draft is the one kept.
        expect(_ph(page)).to_have_value("7.2")
        _link(page, site["keys"][0]).click()
        _active(page, site["keys"][0])
        assert "7.2" in _stored_draft(page)
        # Changes here not saved, a newer draft from the other window: held,
        # and said as that, not as a draft that could not be saved.
        page.evaluate("window.FastMDXDashboard.navigate('run')")
        expect(_ph(page)).to_have_value("7.2")
        _ph(page).fill("6.1")
        other.evaluate("""() => {
            const key = Object.keys(localStorage).find(k => k.startsWith('fmx.builderDraft'));
            localStorage.setItem(key, localStorage.getItem(key).replace('7.2', '7.4'));
        }""")
        _link(page, site["keys"][1]).click()
        note = page.locator("#study-tabs-note")
        expect(note).to_contain_text("another window saved a newer Config draft")
        assert "could not be saved" not in note.text_content()
        _active(page, site["keys"][0])
        assert page.input_value('#run-settings [data-setting="ph"] input') == "6.1"
        assert "7.4" in _stored_draft(page)
        assert page.errors == []
    finally:
        page.context.close()


def test_without_session_storage_the_strip_says_open_studies_are_not_kept(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("""Object.defineProperty(window, 'sessionStorage', {
        get() { throw new DOMException('denied', 'SecurityError'); }});""")
    page = _open(browser, site, context=context)
    try:
        expect(page.locator("#study-tabs-unkept")).to_contain_text("Open studies are not saved in this window")
        assert page.errors == []
    finally:
        page.context.close()


def test_tabs_of_one_system_are_named_by_their_folders_too(browser, site):
    for study in site["studies"][1:]:
        (study / "simulation" / "live_status.json").write_text(json.dumps({
            "status": "completed", "stage": "production", "system_id": "Alpha",
            "current_step": 10, "total_steps": 10}))
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _outside_open(page, site, 2)
        expect(_link(page, site["keys"][0])).to_have_text("ALPH \u00b7 first")
        expect(_link(page, site["keys"][1])).to_have_text("ALPH \u00b7 second")
        expect(_link(page, site["keys"][2])).to_have_text("ALPH \u00b7 third")
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][1]}"]').click()
        page.locator(f'.study-tab-close[data-study-key="{site["keys"][0]}"]').click()
        # Alone with its system, its system names it.
        expect(_link(page, site["keys"][2])).to_have_text("ALPH")
        assert page.errors == []
    finally:
        page.context.close()


def test_closing_the_last_tab_leaves_no_closed_study_named(browser, site):
    page = _open(browser, site)
    try:
        page.locator(".study-tab-close").click()
        page.wait_for_function("location.hash === '#studies'")
        assert "study=" not in page.url
        # Nothing is to happen: a short wait, then the address still names none.
        page.wait_for_timeout(500)
        page.evaluate("() => window.FastMDXDashboard.applyStatus({status: {system_id: 'Alpha', status: 'completed'}, study: window.FastMDXDashboard.state.studyShown})")
        assert "study=" not in page.url
        page.reload(wait_until="domcontentloaded")
        page.locator("#study-tabs:not([hidden])").wait_for()
        page.wait_for_function("window.FastMDXDashboard.state.addressKnown")
        assert "study=" not in page.url and _seen(page) == []
        assert page.errors == []
    finally:
        page.context.close()


def test_a_hold_note_goes_once_another_page_is_shown(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        _link(page, site["keys"][0]).click()
        note = page.locator("#study-tabs-note")
        expect(note).to_contain_text("unsent Agent message")
        page.evaluate("window.FastMDXDashboard.navigate('overview')")
        expect(note).to_be_hidden()
        assert page.errors == []
    finally:
        page.context.close()


def _kept_words(page):
    return page.evaluate("""() => Object.keys(sessionStorage).filter(k => k.startsWith('fmx.agentUnsent:'))
        .map(k => [k.slice(16), JSON.parse(sessionStorage.getItem(k))])""")


def _attach(page, path):
    page.locator("#agent-attach-path").evaluate("""(input, path) => {
        input.value = path; input.dispatchEvent(new Event('change'));
    }""", str(path))
    page.locator("#agent-attachments .agent-chip").filter(has_text=path.name).wait_for()


def test_back_to_another_study_shows_and_writes_its_own_conversation(browser, site):
    page = _open(browser, site)
    try:
        # The second study has a conversation with a change to review.
        _outside_open(page, site, 1, "agent")
        _agent_versions(page).first.wait_for()
        # Back to the first, a load of its own: its Agent is its own.
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        page.wait_for_function("window.FastMDXAgentPanel && !document.body.classList.contains('study-switching')")
        page.wait_for_function("""() => fetch('/api/agent/conversation').then(r => r.json())
            .then(d => d.study_key === new URL(location.href).searchParams.get('study'))""")
        expect(page.locator(".agent-study")).to_have_count(0)
        assert page.evaluate("window.FastMDXAgentPanel.current.study") in (None, str(site["studies"][0]))
        # The attach picker opens in this study's folder.
        page.click("#agent-attach")
        expect(page.locator("#fastmdx-picker-path")).to_have_text(str(site["studies"][0].resolve()))
        page.click("#fastmdx-picker-cancel")
        # The other study is not held by a review that is not on this page.
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        assert page.errors == []
    finally:
        page.context.close()


def test_a_page_for_another_study_is_given_and_writes_no_conversation(browser, site):
    page = _open(browser, site)
    try:
        _a_conversation_of_the_first_study(page)
        written = {p: p.read_text() for p in site["studies"][0].rglob("*.json")}
        # Another window opens the second study; this page is still for the first.
        page.evaluate("""async path => {
            await fetch('/api/explore/switch', {method: 'POST', headers: {'content-type': 'application/json'},
                body: JSON.stringify({folder: path})});
        }""", str(site["studies"][1]))
        answer = page.evaluate("""async ({key, other}) => {
            const read = await (await fetch('/api/agent/conversation?study=' + key)).json();
            const write = await fetch('/api/agent/conversation', {method: 'POST',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({entries: [{role: 'user', text: 'From a stale page.'}],
                                      seen: [], key: 'k1', page_study: key})});
            const named = await fetch('/api/agent/conversation', {method: 'POST',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({entries: [{role: 'user', text: 'Into another study.'}], seen: [],
                                      id: 'conv-x', study: other, page_study: key})});
            return {read, write: [write.status, await write.json()], named: named.status};
        }""", {"key": site["keys"][0], "other": str(site["studies"][1])})
        assert answer["read"]["ok"] is False and answer["read"]["entries"] == []
        assert answer["read"]["study_changed"] is True
        assert answer["write"][0] == 409 and answer["write"][1]["study_changed"] is True
        assert answer["named"] == 409
        assert not any("From a stale page." in p.read_text() or "Into another study." in p.read_text()
                       for p in site["root"].rglob("*.json"))
        assert {p: p.read_text() for p in site["studies"][0].rglob("*.json")} == written
    finally:
        page.context.close()


def test_back_and_forward_keep_an_attachment_with_the_unsent_words(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        _attach(page, site["studies"][1] / "manifest.json")
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        page.go_forward(wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        expect(page.locator("#agent-request")).to_have_value("Unsent words.")
        expect(page.locator("#agent-attachments .agent-chip")).to_contain_text("manifest.json")
        kept = dict(_kept_words(page))[site["keys"][1]]
        assert [f["name"] for f in kept["files"]] == ["manifest.json"]
        # Only what finds the file again is kept, not its text.
        assert set(kept["files"][0]) <= {"path", "name", "size", "sha256", "truncated"}
        page.reload(wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        expect(page.locator("#agent-attachments .agent-chip")).to_contain_text("manifest.json")
        assert page.errors == []
    finally:
        page.context.close()


def test_an_attachment_that_cannot_be_read_again_is_said_by_name(browser, site):
    page = _open(browser, site)
    try:
        extra = site["studies"][1] / "notes.txt"
        extra.write_text("A note to attach.\n")
        _typed(page, site)
        _attach(page, extra)
        _link(page, site["keys"][0]).click()
        expect(page.locator("#study-tabs-note")).to_contain_text("unsent Agent message")
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        extra.unlink()
        _link(page, site["keys"][1]).click()
        _active(page, site["keys"][1])
        page.evaluate("window.FastMDXDashboard.navigate('agent')")
        expect(page.locator("#agent-request")).to_have_value("Unsent words.")
        expect(page.locator("#agent-unsent-note")).to_contain_text("Could not attach again: notes.txt")
        expect(page.locator("#agent-attachments .agent-chip")).to_have_count(0)
        assert page.errors == []
    finally:
        page.context.close()


def _no_unsent_storage(context):
    context.add_init_script("""(() => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (k, v) {
            if (String(k).startsWith('fmx.agentUnsent:')) throw new DOMException('full', 'QuotaExceededError');
            return original.call(this, k, v);
        };
    })();""")


def test_with_storage_full_the_unsent_message_is_said_not_kept_and_leaving_is_asked(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    _no_unsent_storage(context)
    page = _open(browser, site, context=context)
    try:
        _typed(page, site)
        _attach(page, site["studies"][1] / "manifest.json")
        expect(page.locator("#agent-unsent-note")).to_contain_text(
            "could not keep this message and its attachments")
        _asked_before_leaving(page, "history.back()")
        assert page.input_value("#agent-request") == "Unsent words."
        _active(page, site["keys"][1])
        _asked_before_leaving(page, "location.reload()")
        assert page.input_value("#agent-request") == "Unsent words."
        assert page.errors == []
    finally:
        context.close()


def _asked_before_leaving(page, how):
    # The browser asks before the page goes; told to stay, it stays.
    with page.expect_event("dialog") as asked:
        page.evaluate(f"setTimeout(() => {how}, 0)")
    assert asked.value.type == "beforeunload"
    asked.value.dismiss()


def test_an_unsaved_config_draft_this_browser_cannot_keep_asks_before_back(browser, site):
    page = _open(browser, site)
    try:
        _outside_open(page, site, 1)
        _builder(page, site)
        _no_draft_storage(page)
        _ph(page).fill("6.6")
        page.evaluate("document.activeElement.blur()")
        # An address typed naming the other study, and a reload.
        _asked_before_leaving(page, "location.assign('/?study=" + site["keys"][0] + "#overview')")
        _active(page, site["keys"][1])
        _asked_before_leaving(page, "location.reload()")
        expect(_ph(page)).to_have_value("6.6")
        assert page.errors == []
    finally:
        page.context.close()


def test_a_window_whose_study_changed_under_it_says_so_and_keeps_its_words_for_their_study(browser, site):
    first = _open(browser, site)
    second = _open(browser, site)
    try:
        _outside_open(second, site, 1, "agent")
        second.fill("#agent-request", "A question about the second study.")
        # The first window opens the third study; the server watches one.
        _outside_open(first, site, 2)
        second.wait_for_function("key => window.FastMDXDashboard.state.studyShown === key", arg=site["keys"][2])
        expect(second.locator("#dashboard-toast")).to_contain_text("was opened elsewhere")
        expect(second.locator("#agent-request")).to_have_value("")
        expect(second.locator("#agent-unsent-note")).to_contain_text("is saved for it")
        assert dict(_kept_words(second))[site["keys"][1]]["words"] == "A question about the second study."
        assert site["keys"][2] not in dict(_kept_words(second))
        # Back in the second study, the words are there again.
        _link(second, site["keys"][1]).click()
        _active(second, site["keys"][1])
        second.evaluate("window.FastMDXDashboard.navigate('agent')")
        expect(second.locator("#agent-request")).to_have_value("A question about the second study.")
        assert first.errors == [] and second.errors == []
    finally:
        first.context.close()
        second.context.close()


def test_words_typed_before_the_study_is_known_go_after_the_kept_words(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    key = site["keys"][0]
    context.add_init_script("sessionStorage.setItem('fmx.agentUnsent:" + key + "', "
                            + json.dumps(json.dumps({"words": "Kept for it.", "files": []})) + ");")
    held, freed = [], []
    context.route("**/api/status", lambda route: route.continue_() if freed else held.append(route))
    page = context.new_page()
    page.set_default_timeout(20000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    try:
        page.goto(site["session"].url + "#agent", wait_until="domcontentloaded")
        page.wait_for_function("window.FastMDXAgent")
        page.fill("#agent-request", "Typed early.")
        assert held and page.evaluate("!window.FastMDXDashboard.state.studyShown")
        freed.append(True)
        for route in held:
            route.continue_()
        expect(page.locator("#agent-request")).to_have_value("Kept for it.\n\nTyped early.")
        assert dict(_kept_words(page))[key]["words"] == "Kept for it.\n\nTyped early."
        assert page.errors == []
    finally:
        context.close()


def test_open_it_on_an_address_from_another_site_goes_through_the_hold(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        page.goto(site["session"].url + "?study=" + site["keys"][0] + "#agent",
                  referer="https://elsewhere.example/", wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        page.fill("#agent-request", "Unsent words.")
        asked = _switches(page)
        page.get_by_role("button", name="Open it").click()
        expect(page.locator("#study-tabs-note")).to_contain_text("unsent Agent message")
        assert asked == []
        _active(page, site["keys"][1])
        assert page.errors == []
    finally:
        page.context.close()


def test_kept_words_are_forgotten_when_their_tab_is_closed(browser, site):
    page = _open(browser, site)
    try:
        _typed(page, site)
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][0])
        assert site["keys"][1] in dict(_kept_words(page))
        _link(page, site["keys"][1]).locator("xpath=..").locator(".study-tab-close").click()
        page.wait_for_function("key => !sessionStorage.getItem('fmx.agentUnsent:' + key)", arg=site["keys"][1])
        assert page.errors == []
    finally:
        page.context.close()


def test_opening_another_studys_conversation_is_a_step_of_its_own(browser, site):
    page = _open(browser, site)
    try:
        _a_conversation_of_the_first_study(page)
        _outside_open(page, site, 1, "agent")
        steps = page.evaluate("history.length")
        page.click("#agent-conversations")
        row = page.locator('.agent-conv-row .title[title*="opens this study"]').first
        row.evaluate("button => { const fold = button.closest('details'); if (fold) fold.open = true; }")
        row.click()
        _active(page, site["keys"][0])
        assert page.url.endswith("#agent")
        assert page.evaluate("history.length") == steps + 1
        page.go_back(wait_until="domcontentloaded")
        _active(page, site["keys"][1])
        assert page.url.endswith("#agent")
        assert page.errors == []
    finally:
        page.context.close()
