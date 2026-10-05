"""Preferences and the citation open as dialogs, as the Agent's settings do.

Asked for (10-05): "the settings need modernizing ... Citation, Display
preference should not be pages but popups like Agent settings". Found
then: the Display preferences page kept nothing (a reload lost every one),
six of its settings changed nothing (ligand representation, depth fog,
compact mode, reduced motion, advanced metrics, the scientific notation
threshold), two of its three blacks were one colour, and its polling
interval only set the poll the page falls back to. Decided by the user:
the six removed, the study's display name and ligand residue moved to the
study card's menu; the polling interval removed (left to this session).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_the_workspace_says_its_studies import _study

#: Settings that changed nothing, and the poll's interval.
GONE = ("setting-ligand-rep", "setting-fog", "setting-compact", "setting-reduced-motion",
        "setting-advanced-metrics", "setting-scinote", "setting-refresh-seconds",
        "setting-background")


def test_the_settings_that_changed_nothing_are_gone():
    from fastmdxplora.gui.server import _load_template

    page = _load_template()
    for gone in GONE:
        assert gone not in page, gone
    assert 'data-page="settings"' not in page and 'data-page="cite"' not in page
    # Each preference the Preferences dialog keeps is a field of it.
    dialog = page[page.index('id="prefs-dialog"'):page.index('id="cite-dialog"')]
    for kept in ("setting-protein-rep", "setting-ground", "setting-show-water",
                 "setting-show-ions", "setting-spin", "setting-preserve-camera",
                 "setting-pocket-cutoff", "setting-time-format", "setting-chart-history"):
        assert f'id="{kept}"' in dialog, kept
    # The study's own words are in its card's menu.
    menu = page[page.index('id="study-menu"'):page.index('id="study-elsewhere"')]
    assert 'data-study-field="name"' in menu and 'data-study-field="ligand"' in menu


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


@pytest.fixture
def session(tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    study = _study(tmp_path / "ubiquitin", system="1UBQ")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield started, study
    started.server.shutdown()


def _open(browser, url, where="#overview"):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard && window.FastMDXDialog")
    page.wait_for_selector("body:not(.state-loading)")
    return page


def _focused(page) -> str:
    return page.evaluate("() => document.activeElement.id || document.activeElement.className")


def test_the_menu_opens_each_dialog_over_the_page(browser, session):
    started, _ = session
    page = _open(browser, started.url, "#analysis")
    seen = {}
    for dialog in ("prefs-dialog", "cite-dialog"):
        page.click("#settings-open")
        page.click(f'#settings-popup [data-dialog-open="{dialog}"]')
        page.wait_for_selector(f"#{dialog}:not([hidden])")
        inside = page.evaluate(f"() => document.getElementById('{dialog}')"
                               ".contains(document.activeElement)")
        # Tab and Shift+Tab stay inside it.
        for key in ("Tab", "Shift+Tab", "Shift+Tab", "Tab", "Tab", "Tab"):
            page.keyboard.press(key)
            inside = inside and page.evaluate(
                f"() => document.getElementById('{dialog}').contains(document.activeElement)")
        page.keyboard.press("Escape")
        seen[dialog] = (inside, page.is_hidden(f"#{dialog}"), _focused(page),
                        page.is_hidden("#settings-popup"),
                        page.evaluate("() => document.documentElement.dataset.page"))
    # A click on the dimmed page around it closes it too.
    page.evaluate("() => window.FastMDXDialog.open('cite-dialog')")
    page.mouse.click(30, 450)
    outside = page.is_hidden("#cite-dialog")
    errors = page.errors
    page.context.close()
    assert errors == []
    for dialog, (inside, closed, focus, popup_closed, shown) in seen.items():
        assert inside and closed and popup_closed, dialog
        assert focus == "settings-open", (dialog, focus)
        assert shown == "analysis", "the page under it stays"
    assert outside


def test_an_old_link_opens_the_dialog(browser, session):
    started, _ = session
    for where, dialog in (("#cite", "cite-dialog"), ("#settings", "prefs-dialog")):
        page = _open(browser, started.url, where)
        page.wait_for_selector(f"#{dialog}:not([hidden])")
        shown = page.evaluate("() => [document.documentElement.dataset.page, location.hash]")
        page.context.close()
        assert shown == ["overview", "#overview"], where


def test_the_preferences_are_kept_and_put_back(browser, session):
    started, _ = session
    page = _open(browser, started.url, "#viewer")
    page.evaluate("() => window.FastMDXDialog.open('prefs-dialog')")
    page.select_option("#setting-time-format", "12h")
    page.select_option("#setting-ground", "white")
    page.check("#setting-show-water")
    page.fill("#setting-pocket-cutoff", "7.5")
    page.dispatch_event("#setting-pocket-cutoff", "change")
    page.keyboard.press("Escape")
    told = page.evaluate("() => window.FastMDXDashboard.settings()")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard")
    page.wait_for_selector("body:not(.state-loading)")
    after = page.evaluate("""() => [document.getElementById('setting-time-format').value,
        document.getElementById('setting-ground').value,
        document.getElementById('setting-show-water').checked,
        document.getElementById('setting-pocket-cutoff').value,
        document.querySelector('[data-action="background"]').getAttribute('aria-pressed')]""")
    # Restore defaults puts the release's back and keeps nothing.
    page.evaluate("() => window.FastMDXDialog.open('prefs-dialog')")
    page.click("#prefs-restore")
    page.wait_for_timeout(100)
    restored = page.evaluate("""() => [document.getElementById('setting-time-format').value,
        document.getElementById('setting-ground').value,
        document.getElementById('setting-show-water').checked,
        localStorage.getItem('fmx.preferences')]""")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert set(told) == {"ligand", "pocketCutoff", "chartHistory", "proteinRepresentation",
                         "ground", "showWater", "showIons", "spin", "preserveCamera"}
    assert told["ground"] == "white" and told["showWater"] and told["pocketCutoff"] == 7.5
    # The Viewer's ground button says white as it opens.
    assert after == ["12h", "white", True, "7.5", "true"]
    assert restored == ["24h", "dark", False, None]


def test_the_study_s_own_words_are_kept_by_its_folder(browser, session):
    started, study = session
    page = _open(browser, started.url)
    page.wait_for_function("() => window.FastMDXDashboard.state.outputDir")
    page.wait_for_function("() => document.getElementById('topbar-run-title').textContent"
                           " !== 'No active study'")
    before = page.text_content("#topbar-run-title")
    page.click("#study-card")
    page.click('[data-study-field="name"]')
    page.fill("#study-field-input", "Ubiquitin, wild type")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.getElementById('topbar-run-title').textContent"
                           " === 'Ubiquitin, wild type'")
    page.click("#study-card")
    page.click('[data-study-field="ligand"]')
    page.fill("#study-field-input", "two words")
    page.click("#study-field-save")
    refused = (page.text_content("#study-field-refused"), page.is_visible("#study-field-dialog"))
    page.fill("#study-field-input", "ben")
    page.click("#study-field-save")
    kept = page.evaluate("() => Object.keys(localStorage).filter((k) => k.startsWith('fmx.study:'))"
                         ".map((k) => [k, JSON.parse(localStorage.getItem(k))])")
    # Another study's words are not this one's.
    page.evaluate("() => { localStorage.setItem('fmx.study:/elsewhere', JSON.stringify("
                  "{name: 'Not this one'})); }")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => document.getElementById('topbar-run-title').textContent"
                           " === 'Ubiquitin, wild type'")
    ligand = page.evaluate("() => window.FastMDXDashboard.settings().ligand")
    # Use the default: the system's name again, and nothing kept.
    page.click("#study-card")
    page.click('[data-study-field="name"]')
    page.click("#study-field-clear")
    page.wait_for_function("(name) => document.getElementById('topbar-run-title').textContent"
                           " === name", arg=before)
    left = page.evaluate(f"() => localStorage.getItem('fmx.study:{study}')")
    errors = page.errors
    page.context.close()
    assert errors == []
    assert before != "Ubiquitin, wild type"
    assert refused == ("A residue name is 1 to 5 letters or digits.", True)
    assert kept == [[f"fmx.study:{study}", {"name": "Ubiquitin, wild type", "ligand": "BEN"}]]
    assert ligand == "BEN"
    assert json.loads(left) == {"ligand": "BEN"}


def test_the_citation_is_copied_in_one_click(browser, session):
    started, _ = session
    page = _open(browser, started.url)
    page.evaluate("() => window.FastMDXDialog.open('cite-dialog')")
    copied = {}
    for source in ("cite-reference", "cite-bibtex"):
        page.click(f'[data-copy-from="{source}"]')
        page.wait_for_timeout(200)
        copied[source] = (page.evaluate("() => navigator.clipboard.readText()"),
                          page.text_content(f"#{source}").strip())
    page.context.close()
    for source, (clipboard, shown) in copied.items():
        assert clipboard == shown and shown, source


def test_the_standalone_dashboard_has_the_citation_s_dialog(tmp_path, browser):
    from fastmdxplora.gui.report_dashboard import build_dashboard

    (tmp_path / "manifest.json").write_text(json.dumps({"system": "1L2Y", "phases": []}),
                                            encoding="utf-8")
    build_dashboard(orchestrator=SimpleNamespace(output_dir=tmp_path, system="1L2Y"),
                    output_dir=tmp_path / "report", title="A study")
    html_file = tmp_path / "report" / "dashboard.html"
    html = html_file.read_text(encoding="utf-8")
    assert 'data-page="cite"' not in html and 'id="cite-dialog"' in html
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(html_file.as_uri())
    page.click("#settings-open")
    page.click('[data-dialog-open="cite-dialog"]')
    shown = page.is_visible("#cite-dialog")
    page.keyboard.press("Escape")
    closed = (page.is_hidden("#cite-dialog"), _focused(page))
    page.goto(html_file.as_uri() + "#cite")
    by_link = page.is_visible("#cite-dialog")
    page.close()
    assert errors == []
    assert shown and closed == (True, "settings-open") and by_link
