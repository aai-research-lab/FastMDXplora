"""Every page of a study says where it is: `study › run › Page`.

The place leads each page's head, its heading the last part: the study, the
run of a study of several (a menu of its other runs and the study as a
whole), then the page. A page of no study is its name alone, and each
page's name is the sidebar's word for it. The sidebar's list of runs and
its "Run of" card said the same in another place, the names cut there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_a_study_of_runs_is_shown_as_one import _a_sweep_study  # noqa: E402
from tests.test_the_workspace_says_its_studies import _study  # noqa: E402

DONE = {"stage": "completed", "current_step": 500, "total_planned_steps": 500}
STUDY_PAGES = {"overview": "Overview", "viewer": "Viewer", "analysis": "Analysis",
               "report": "Report", "files": "Files"}
OTHER_PAGES = {"studies": "All studies", "run": "New study", "agent": "Agent"}

#: The place on the page shown: its parts, and the heading after them.
PLACE = """() => {
    const head = document.querySelector('.page:not([hidden]) .page-header');
    const nav = head.querySelector('.place');
    return {
        parts: nav && !nav.hidden ? [...nav.querySelectorAll('.place-crumb')].map(
            (c) => c.textContent.trim()) : [],
        heading: head.querySelector('.page-title').textContent.trim(),
        oneLine: Math.abs(head.querySelector('.page-title').getBoundingClientRect().top
            - (nav && !nav.hidden ? nav.getBoundingClientRect().top : head.querySelector(
                '.page-title').getBoundingClientRect().top)) < 8,
    };
}"""


def _serve(root: Path, look, *, width: int = 1440):
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(root), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(60000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            said = look(page, session.url)
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    return said


@pytest.fixture(scope="module")
def sweep(tmp_path_factory) -> Path:
    root = _a_sweep_study(tmp_path_factory.mktemp("place"))
    for folder in sorted((root / "runs").iterdir()):
        (folder / "simulation" / "live_status.json").write_text(json.dumps(DONE), encoding="utf-8")
    return root


def _open(page, url: str, where: str) -> None:
    page.goto(url + "#" + where, wait_until="domcontentloaded")
    page.wait_for_function("() => window.FastMDXPlace && window.FastMDXDashboard")
    page.wait_for_function("""() => {
        const state = window.FastMDXDashboard.state;
        return state.appState?.active_run
            && state.results?.output_dir === state.appState.active_run;
    }""")


def test_each_page_of_a_study_of_runs_says_the_study_the_runs_and_the_page(sweep):
    def look(page, url):
        said = {}
        for name in list(STUDY_PAGES) + list(OTHER_PAGES):
            _open(page, url, name)
            said[name] = page.evaluate(PLACE)
        said["sidebar"] = page.evaluate(
            "() => [!!document.getElementById('study-runs'), !!document.getElementById('study-run-of')]")
        return said

    said = _serve(sweep, look)
    for name, heading in STUDY_PAGES.items():
        assert said[name] == {"parts": ["sweep", "All runs"], "heading": heading, "oneLine": True}, name
    for name, heading in OTHER_PAGES.items():
        assert said[name]["parts"] == [] and said[name]["heading"] == heading, name
    assert said["sidebar"] == [False, False]


def test_the_run_switcher_opens_another_run_on_the_same_page(sweep):
    def look(page, url):
        _open(page, url, "analysis")
        page.click(".page[data-page=analysis] .place-run")
        page.wait_for_selector("#place-menu")
        menu = page.eval_on_selector_all(
            "#place-menu .place-menu-item",
            "items => items.map((i) => [i.querySelector('.place-menu-label').textContent,"
            " i.querySelector('.place-menu-said').textContent, i.getAttribute('aria-checked'),"
            " (i.querySelector('.status-dot') || {className: ''}).className])")
        focused = page.evaluate("() => document.activeElement.textContent")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => (document.querySelector('.page[data-page=analysis] .place-run') || {}).textContent"
            " && document.querySelector('.page[data-page=analysis] .place-run').textContent"
            ".includes('310')")
        page.wait_for_timeout(500)
        after = page.evaluate(PLACE)
        where = page.evaluate("() => document.documentElement.dataset.page")
        # The study's part leads back to the study as a whole, on its Overview.
        page.click(".page[data-page=analysis] .place-study")
        page.wait_for_function(
            "() => (document.querySelector('.page[data-page=overview] .place-run') || {}).textContent"
            " === 'All runs'")
        back = page.evaluate("() => document.documentElement.dataset.page")
        return {"menu": menu, "focused": focused, "after": after, "where": where, "back": back}

    said = _serve(sweep, look)
    assert [row[:3] for row in said["menu"]] == [
        ["All runs", "2 of 2 completed", "true"],
        ["temperature 300 K", "completed", "false"],
        ["temperature 310 K", "completed", "false"]]
    assert all("status-dot-completed" in row[3] for row in said["menu"][1:])
    assert "All runs" in said["focused"]
    assert said["where"] == "analysis"
    assert said["after"]["parts"] == ["sweep", "temperature 310 K"]
    assert said["after"]["heading"] == "Analysis"
    assert said["back"] == "overview"


def test_a_study_of_one_run_says_the_study_and_the_page(tmp_path):
    study = _study(tmp_path / "ubiquitin", means={"rmsd": (0.1, 0.01)})

    def look(page, url):
        _open(page, url, "report")
        return page.evaluate(PLACE)

    said = _serve(study, look)
    assert said["heading"] == "Report"
    assert len(said["parts"]) == 1 and said["parts"][0]
