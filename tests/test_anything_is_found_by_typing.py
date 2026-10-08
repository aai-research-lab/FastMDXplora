"""Anything the GUI can go to or do is found by typing (Cmd+K, Ctrl+K).

A page, a study under Recent, an analysis, a tool of the Viewer, the
scheme, Preferences, Cite and the Viewer's keys, each a line found by the
words typed, chosen with the arrows and Enter.
"""

from __future__ import annotations

import pytest

from tests.test_the_drawing_scripts_run_in_a_browser import _open, _write_study


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure
    from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest

    study = _write_study(tmp_path_factory.mktemp("palette") / "study")
    _manifest(study, {"rmsd": {"status": "ok"}})
    _analysis(study, "rmsd", 0.30 + 0.002 * _ar1(500, 0.5, 1))
    _figure(study / "analysis" / "rmsd" / "rmsd.png")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    yield session
    session.server.shutdown()


@pytest.fixture
def page(dashboard):
    pytest.importorskip("playwright.sync_api")
    for opened in _open(dashboard, "#overview"):
        opened.set_default_timeout(60000)
        opened.wait_for_function("() => window.FastMDXPalette && window.FastMDXDashboard")
        yield opened


def _found(page) -> list[str]:
    return page.eval_on_selector_all("#palette-list .palette-item", "(all) => all.map((i) => i.textContent)")


def test_a_page_is_gone_to_by_its_name(page) -> None:
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette:not([hidden])")
    assert page.evaluate("() => document.activeElement.id") == "palette-input"
    page.keyboard.type("files")
    assert _found(page)[0] == "Files"
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.documentElement.dataset.page === 'files'")
    assert page.locator("#palette").is_hidden()
    assert not page.errors


def test_the_arrows_choose(page) -> None:
    page.keyboard.press("Control+k")
    page.keyboard.type("light")
    found = _found(page)
    assert found[0] == "Light"
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")


def test_an_analysis_and_a_viewer_tool_are_there(page) -> None:
    page.wait_for_selector('.analysis-card[data-analysis="rmsd"]', state="attached")
    page.keyboard.press("Control+k")
    page.keyboard.type("rmsd")
    assert any("RMSD" in line for line in _found(page))
    page.fill("#palette-input", "saved views")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.documentElement.dataset.page === 'viewer'")
    page.wait_for_function("() => window.FastMDXViewerRail.chosen === 'side-saved'")


def test_nothing_found_says_so_and_escape_closes(page) -> None:
    page.keyboard.press("Control+k")
    page.keyboard.type("zzzzqqq")
    assert page.text_content("#palette-list") == "Nothing by that name."
    page.keyboard.press("Escape")
    page.wait_for_selector("#palette", state="hidden")
    # Again closes it, as the Agent's key does its drawer.
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette:not([hidden])")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette", state="hidden")


def test_each_analysis_once_by_its_heading(tmp_path) -> None:
    """The clustering was listed by its first figure's title, "KMeans
    trajectory scatter", and again for that figure's own card."""
    pytest.importorskip("playwright.sync_api")
    from fastmdxplora.gui.server import start_dashboard_session
    from tests.test_an_analysis_is_drawn_from_its_numbers import _figure
    from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest

    study = _write_study(tmp_path / "study")
    _manifest(study, {"rmsd": {"status": "ok"}, "cluster": {"status": "ok"}})
    _analysis(study, "rmsd", 0.30 + 0.002 * _ar1(500, 0.5, 1))
    _figure(study / "analysis" / "rmsd" / "rmsd.png")
    (study / "analysis" / "cluster").mkdir(exist_ok=True)
    for name in ("kmeans_trajectory_scatter", "kmeans_population"):
        _figure(study / "analysis" / "cluster" / f"{name}.png")
    session = start_dashboard_session(output=str(study), host="127.0.0.1", port=0)
    try:
        for opened in _open(session, "#analysis"):
            opened.set_default_timeout(60000)
            opened.wait_for_selector('.analysis-card[data-analysis="cluster"]', state="attached")
            opened.keyboard.press("Control+k")
            opened.wait_for_selector("#palette:not([hidden])")
            listed = opened.evaluate("""() => {
              const out = []; let group = '';
              for (const li of document.querySelectorAll('#palette-list li')) {
                if (li.classList.contains('palette-group')) group = li.textContent;
                else if (group === 'Analysis') out.push(li.textContent);
              }
              return out; }""")
            assert not opened.errors
    finally:
        session.server.shutdown()
    assert "Clustering" in listed and "RMSD" in listed
    assert not [label for label in listed if label.lower().startswith("kmeans")], listed
    assert len(listed) == len({label.lower() for label in listed}), listed
