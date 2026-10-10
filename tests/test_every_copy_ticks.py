"""Every Copy ticks.

Some Copies said they had copied only in the notice, some swapped their
words for "Path copied", the Files page's only in the notice; and the Cite
dialog's and the Viewer's failed outright where the page has no clipboard
(a GUI reached over plain http from another machine). Each now goes through
one Copy (icons.js): copied however the browser allows, the button ticked
for a moment ("Copied") or crossed with the reason, then put back. A button
in words keeps them, the tick before them or where its icon is; a menu item
that closes its menu ticks the button that opened it. Open the folder, which
copies the path where it cannot open the folder, says it was copied only
where it was.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("mdtraj")

from tests.test_the_builder_page_in_a_browser import _peptide  # noqa: E402
from tests.test_the_drawing_scripts_run_in_a_browser import _write_study  # noqa: E402

FOLDER = "fastmdxplora_output_20261010_101500_copies_tick"
STATIC = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora" / "gui" / "static"

# The clipboard refused, as a browser refuses it; and none at all, as on a
# page that is not secure, with the hidden box's copy kept to be read.
REFUSED = """(() => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, get: () => ({
        writeText: () => Promise.reject(new DOMException('Refused', 'NotAllowedError'))})});
    document.execCommand = () => false; })()"""
NONE = """(() => {
    window.__boxed = [];
    Object.defineProperty(navigator, 'clipboard', {configurable: true, get: () => undefined});
    document.execCommand = function (what) {
        const box = document.activeElement;
        window.__boxed.push(what === 'copy' && box && box.tagName === 'TEXTAREA' ? box.value : null);
        return true; }; })()"""


def _shape(name):
    from fastmdxplora.gui.sidebar_icons import ICONS

    return re.search(r'd="([^"]+)"', ICONS[name]).group(1)


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session

    root = tmp_path_factory.mktemp("copies")
    study = _write_study(root / FOLDER)
    _peptide(root / "peptide.pdb")
    mp = pytest.MonkeyPatch()
    mp.setenv("FASTMDXPLORA_CACHE_DIR", str(root / "cache"))
    mp.setenv("FASTMDXPLORA_CONFIG_DIR", str(root / "config"))
    started = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    started.study = study
    started.peptide = root / "peptide.pdb"
    yield started
    started.server.shutdown()
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        yield launched
        launched.close()


def _open(browser, session, clipboard=None, where="#overview"):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    if clipboard:
        context.add_init_script(clipboard)
    else:
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=session.url)
    page = context.new_page()
    page.set_default_timeout(60000)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.goto(session.url + where, wait_until="domcontentloaded")
    page.wait_for_function("() => document.body.classList.contains('state-ready') && "
                           "!document.getElementById('study-folder').hidden && "
                           "document.getElementById('study-folder-name').textContent")
    return page


def _said(page, selector, said):
    """The button named as its copy went, and what it shows meanwhile."""
    page.wait_for_function("([s, said]) => { const b = document.querySelector(s); "
                           "return b && b.getAttribute('aria-label') === said; }", arg=[selector, said])
    return page.evaluate("""(s) => {
        const b = document.querySelector(s);
        const tick = b.querySelector(':scope > svg.copy-tick') || b.querySelector(':scope > svg');
        const path = tick && tick.querySelector('path');
        return {words: b.textContent.trim(), first: b.firstElementChild === tick,
                tick: !!b.querySelector(':scope > svg.copy-tick'),
                shape: path ? path.getAttribute('d') : '',
                next: tick && tick.nextElementSibling ? tick.nextElementSibling.getAttribute('class') : ''};
    }""", selector)


def _back(page, selector, name):
    page.wait_for_function("([s, name]) => { const b = document.querySelector(s); "
                           "return b.getAttribute('aria-label') === name && !b.querySelector('.copy-tick'); }",
                           arg=[selector, name], timeout=5000)


def _clipboard(page):
    return page.evaluate("() => navigator.clipboard.readText()")


def _menu_open_the_folder(page, session, opened):
    page.route("**/api/open-output", lambda route: route.fulfill(
        json={"path": str(session.study), "opened": opened}))
    page.click("#study-card")
    page.click("#open-output")


def test_the_folder_s_name_keeps_its_words_and_ticks(browser, session) -> None:
    page = _open(browser, session)
    name = page.text_content("#study-folder-name")
    page.click("#study-folder")
    shown = _said(page, "#study-folder", "Path copied")
    copied = _clipboard(page)
    # Fitted into what the tick leaves of the line, and again after.
    fits = page.evaluate("() => { const n = document.getElementById('study-folder-name'); "
                         "return n.scrollWidth <= n.clientWidth + 1; }")
    _back(page, "#study-folder", None)
    after = page.text_content("#study-folder-name")
    page.context.close()
    assert shown["tick"] and shown["first"] and shown["shape"] == _shape("check"), shown
    assert "…" in name and shown["words"] and "copied" not in shown["words"], shown
    assert fits and after == name
    assert copied.rstrip("/").endswith(FOLDER)
    assert page.errors == []


def test_open_the_folder_ticks_the_card_that_opened_its_menu(browser, session) -> None:
    page = _open(browser, session)
    _menu_open_the_folder(page, session, opened=True)
    shown = _said(page, "#study-card", "Path copied")
    hidden = page.evaluate("() => getComputedStyle(document.querySelector('#study-card .study-card-chevron')).display")
    copied = _clipboard(page)
    _back(page, "#study-card", None)
    shown_again = page.evaluate("() => getComputedStyle(document.querySelector('#study-card .study-card-chevron')).display")
    page.context.close()
    # Where its chevron is, the card's words left as they are.
    assert shown["tick"] and "study-card-chevron" in shown["next"], shown
    assert hidden == "none" and shown_again != "none"
    assert copied.rstrip("/").endswith(FOLDER)


def test_the_files_page_ticks_its_opener_and_its_code(browser, session) -> None:
    page = _open(browser, session)
    page.evaluate("() => window.FastMDXDashboard.navigate('files')")
    row = '.files-row[data-path="simulation/production.dcd"]'
    page.wait_for_selector(row)
    page.click(f"{row} [data-menu]")
    page.click(".files-menu [data-do=copy]")
    more = _said(page, f"{row} [data-menu]", "Path copied")
    path = _clipboard(page)
    page.click(".files-code")
    code = _said(page, ".files-code", "Copied")
    load = _clipboard(page)
    page.context.close()
    assert more["shape"] == _shape("check") and not more["words"], more
    assert path == str(session.study / "simulation" / "production.dcd")
    assert code["tick"] and code["first"] and code["words"] == load and load.startswith("md.load("), code


def test_cite_and_the_builder_s_command_tick(browser, session) -> None:
    page = _open(browser, session)
    page.evaluate("() => window.FastMDXDialog.open('cite-dialog')")
    page.click("[data-copy-from=cite-reference]")
    cite = _said(page, "[data-copy-from=cite-reference]", "Copied")
    reference = (_clipboard(page), page.text_content("#cite-reference").strip())
    page.evaluate("() => window.FastMDXDialog.close('cite-dialog')")
    page.evaluate("() => window.FastMDXDashboard.navigate('run')")
    page.click("#run-start [data-start='structure']")
    page.fill("#run-system", str(session.peptide))
    page.wait_for_selector("#run-copy-command:not([disabled])")
    page.click("#run-copy-command")
    command = _said(page, "#run-copy-command", "Command copied")
    copied = _clipboard(page)
    page.context.close()
    assert cite["shape"] == _shape("check") and reference[0] == reference[1] != ""
    assert command["shape"] == _shape("check") and copied.startswith("fastmdx")


def test_a_refused_copy_crosses_and_is_not_said_copied(browser, session) -> None:
    page = _open(browser, session, clipboard=REFUSED)
    page.click("#study-folder")
    folder = _said(page, "#study-folder", "Could not copy; the path is in the tooltip")
    _back(page, "#study-folder", None)
    _menu_open_the_folder(page, session, opened=False)
    card = _said(page, "#study-card", "Could not copy the path")
    page.wait_for_function("() => /Could not open the folder/.test("
                           "document.getElementById('dashboard-toast').textContent)")
    toast = page.text_content("#dashboard-toast")
    page.context.close()
    assert folder["tick"] and folder["shape"] == _shape("close"), folder
    assert card["shape"] == _shape("close"), card
    assert "copied" not in toast and str(session.study) in toast, toast
    assert page.errors == []


def test_without_a_clipboard_the_text_is_copied_still(browser, session) -> None:
    """A GUI reached over plain http from another machine has no clipboard;
    Cite said it could not copy, and Open the folder copied nothing."""
    page = _open(browser, session, clipboard=NONE)
    page.evaluate("() => window.FastMDXDialog.open('cite-dialog')")
    page.click("[data-copy-from=cite-reference]")
    cite = _said(page, "[data-copy-from=cite-reference]", "Copied")
    # The keyboard is back on the button, where the tick is.
    focused = page.evaluate("() => document.activeElement.getAttribute('data-copy-from')")
    reference = page.text_content("#cite-reference").strip()
    page.evaluate("() => window.FastMDXDialog.close('cite-dialog')")
    _menu_open_the_folder(page, session, opened=True)
    card = _said(page, "#study-card", "Path copied")
    boxed = page.evaluate("() => window.__boxed")
    page.context.close()
    assert cite["shape"] == _shape("check") and focused == "cite-reference"
    assert card["tick"]
    assert boxed[0] == reference and boxed[-1].rstrip("/").endswith(FOLDER), boxed


def test_no_copy_goes_round_the_one_copy() -> None:
    """A new Copy that writes the clipboard itself fails here, so none
    misses the tick. The Agent's own Copies are its session's to move; the
    Files page copies by itself only in the standalone dashboard, which
    carries it without icons.js."""
    writes = re.compile(r"clipboard\s*\??\.\s*writeText|execCommand\(\s*[\"']copy")
    found = {path.name for path in STATIC.glob("*.js") if writes.search(path.read_text(encoding="utf-8"))}
    assert found - {"agent-panel.js"} == {"icons.js", "files-page.js"}
    files = (STATIC / "files-page.js").read_text(encoding="utf-8")
    assert files.index("icons.copy(") < files.index("clipboard.writeText")
