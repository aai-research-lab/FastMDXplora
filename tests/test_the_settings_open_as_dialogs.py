"""The citation opens as a dialog, as the Agent's settings do; Preferences,
a dialog until IMDE 2.9, are rows of the Settings page.

Asked for (10-05): "the settings need modernizing ... Citation, Display
preference should not be pages but popups like Agent settings". Found
then: the Display preferences page kept nothing (a reload lost every one),
six of its settings changed nothing (ligand representation, depth fog,
compact mode, reduced motion, advanced metrics, the scientific notation
threshold), two of its three blacks were one colour, and its polling
interval only set the poll the page falls back to. Decided by the user:
the six removed, the study's display name and ligand residue moved to the
study card's menu; the polling interval removed (left to this session).
The Settings page (IMDE 2.9, user 10-21) put every setting on one page
again, Preferences' rows among them, each applied as it is changed and
saved: `test_one_settings_page.py`.
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
    assert 'data-page="cite"' not in page and 'id="prefs-dialog"' not in page
    # Each preference saved in this browser is a field of the Settings page.
    start = page.index('data-page="settings"')
    dialog = page[start:page.index("</section>", start)]
    for kept in ("setting-protein-rep", "setting-ground", "setting-show-water",
                 "setting-show-ions", "setting-spin", "setting-preserve-camera",
                 "setting-pocket-cutoff", "setting-time-format", "setting-chart-history"):
        assert f'id="{kept}"' in dialog, kept
    # The study's own words are in its card's menu.
    menu = page[page.index('id="study-menu"'):page.index('class="sidebar-nav sidebar-pages"')]
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
    for dialog in ("cite-dialog",):
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
    page = _open(browser, started.url, "#cite")
    page.wait_for_selector("#cite-dialog:not([hidden])")
    shown = page.evaluate("() => [document.documentElement.dataset.page, location.hash]")
    page.context.close()
    assert shown == ["overview", "#overview"]
    # Preferences' old addresses are the Settings page.
    for where in ("#settings", "#preferences"):
        page = _open(browser, started.url, where)
        page.wait_for_selector('.page[data-page="settings"]:not([hidden])')
        shown = page.evaluate("() => [document.documentElement.dataset.page, location.hash]")
        page.context.close()
        assert shown == ["settings", "#settings"], where


def test_the_preferences_are_saved_and_put_back(browser, session):
    started, _ = session
    page = _open(browser, started.url, "#settings")
    page.select_option("#setting-time-format", "12h")
    page.select_option("#setting-ground", "white")
    page.check("#setting-show-water")
    page.fill("#setting-pocket-cutoff", "7.5")
    page.dispatch_event("#setting-pocket-cutoff", "change")
    told = page.evaluate("() => window.FastMDXDashboard.settings()")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXDashboard")
    page.wait_for_selector("body:not(.state-loading)")
    after = page.evaluate("""() => [document.getElementById('setting-time-format').value,
        document.getElementById('setting-ground').value,
        document.getElementById('setting-show-water').checked,
        document.getElementById('setting-pocket-cutoff').value,
        document.getElementById('viewer-ground').value]""")
    # Restore defaults puts the release's back and saves nothing.
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
    # The Viewer's ground says white as it opens.
    assert after == ["12h", "white", True, "7.5", "white"]
    # The release's ground is the scheme's own.
    assert restored == ["24h", "scheme", False, None]


def test_the_study_s_own_words_are_kept_by_its_folder(browser, session):
    started, study = session
    page = _open(browser, started.url)
    page.wait_for_function("() => window.FastMDXDashboard.state.outputDir")
    page.wait_for_function("() => document.getElementById('topbar-run-title').textContent"
                           " !== 'No active study'")
    before = page.text_content("#topbar-run-title")
    page.click("#study-card")
    page.click('[data-study-field="name"]')
    # The system's ID, four letters or digits at most.
    page.fill("#study-field-input", "Ubiquitin, wild type")
    page.keyboard.press("Enter")
    too_long = page.text_content("#study-field-refused")
    page.fill("#study-field-input", "ubwt")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.getElementById('topbar-run-title').textContent"
                           " === 'UBWT'")
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
                           " === 'UBWT'")
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
    assert before != "UBWT" and too_long == "An ID is 1 to 4 letters or digits."
    assert refused == ("A residue name is 1 to 5 letters or digits.", True)
    assert kept == [[f"fmx.study:{study}", {"name": "UBWT", "ligand": "BEN"}]]
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


def test_the_menu_says_the_agent_s_provider_and_mode_and_its_model_only_in_its_settings(
        tmp_path, browser):
    """The AI model is said only in the Agent's own settings (user, 10-18):
    the gear's menu says whose AI the Agent asks and its mode, never the
    model, and its link opens the settings where the model is."""
    from fastmdxplora.agent.models import ModelChoice, save_choice
    from fastmdxplora.gui.server import start_dashboard_session

    save_choice(ModelChoice(provider="openai", model="gpt-test-model"))
    study = _study(tmp_path / "ubiquitin", system="1UBQ")
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        page = _open(browser, started.url)
        page.click("#settings-open")
        page.wait_for_function(
            "() => document.getElementById('settings-engine').textContent !== 'none'")
        # Nowhere on the page but in the Agent's settings: its words, its
        # tooltips and its labels, shown or not, on the Agent's page too.
        page.goto(started.url + "#agent", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.getElementById('settings-engine').textContent !== 'none'")
        elsewhere = page.evaluate("""() => {
            const settings = document.getElementById('agent-settings');
            const all = [...document.body.querySelectorAll('*')]
                .filter((e) => !settings.contains(e) && e.tagName !== 'SCRIPT');
            return all.map((e) => [...e.childNodes].filter((n) => n.nodeType === 3)
                    .map((n) => n.textContent).join(' ') + ' ' + (e.title || '') + ' '
                    + (e.getAttribute('aria-label') || '') + ' ' + (e.value || '')).join(' ');
        }""")
        page.click("#settings-open")
        menu = page.eval_on_selector("#settings-popup", """p => p.innerText + ' '
            + [...p.querySelectorAll('[title],[aria-label]')]
                .map((e) => (e.title || '') + ' ' + (e.getAttribute('aria-label') || '')).join(' ')""")
        engine = page.text_content("#settings-engine").strip()
        mode = page.text_content("#settings-mode").strip()
        page.click("#settings-agent-link")
        page.wait_for_selector("#agent-settings:not([hidden])")
        page.wait_for_function(
            "() => document.getElementById('agent-model-current').textContent.trim()")
        settings = page.text_content("#agent-model-current").strip()
        errors = page.errors
        page.context.close()
    finally:
        started.server.shutdown()
    assert errors == []
    assert engine == "OpenAI"
    assert mode == "Seeker: built-in, assisted"
    assert "gpt-test-model" not in menu
    assert "gpt-test-model" not in elsewhere
    assert settings == "gpt-test-model"


def test_the_docs_say_the_menu_names_the_provider_and_mode_not_the_model():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "docs" / "gui.md").read_text(encoding="utf-8")
    said = text[text.index("**The settings**"):text.index("**Settings** is one page")]
    assert "the Agent's AI model and its mode" not in said
    assert "whose AI the Agent asks (its provider) and its mode" in said
    assert "where alone its AI model is said" in said
